"""Loomy Gateway 契约实现。

## 三个必须照做的点

1. `platform_id` **类属性**必须声明（Protocol 的一部分，
   缺了 `isinstance` 判 False 且错误信息不提示）
2. 方法签名与 Protocol 完全一致（不能有默认值差异）
3. `install()` 幂等

## Loomy 的特殊性

* **没有额度接口**：计费在每轮 `usage.points_consumed` → 计量制
* **没有 refresh**：session 失效只能重新登录
* **没有签到**：`checkin` 明确返回 `supported: False`
* 凭证是明文 JSON → `import_local` 能真正工作（用户什么都不用做）
"""
from __future__ import annotations

import json
import logging

from admin.platform.gateway import AccountOps, ClientProbe, Gateway
from admin.platform.loomy.core import client, endpoints
from admin.platform.loomy.services import accounts as loomy_accounts
from admin.platform.loomy.services import scan as loomy_scan

logger = logging.getLogger("admin.platform.loomy.platform_gateway")


class LoomyAccountOps:
    """账号池 11 项操作。"""

    def __init__(self, realm: str = ""):
        self.realm = realm

    def list(self, db):
        """账号列表。

        **会同步 DB 列**：admin 的 `/api/accounts` 读的是列，
        不写回会导致前端账号池显示旧数据。
        """
        views = []
        for account in loomy_accounts.accounts_for(db, self.realm):
            quota = None
            if not loomy_accounts.quota_is_fresh(account):
                # 用 sync_quota 而不是 fetch_quota ——
                # 前者**同时**拉积分余额与模型数（fetch_quota 只探模型，
                # 用它会导致余额永远显示 0）
                result = loomy_accounts.sync_quota(db, account)
                if result.get("ok"):
                    quota = result.get("quota")
            else:
                try:
                    snap = json.loads(account.credits_snapshot or "{}")
                except ValueError:
                    snap = {}
                quota = snap if isinstance(snap, dict) else None
            views.append(loomy_accounts.public_view(account, quota=quota))
        db.commit()
        return {"items": views, "summary": loomy_accounts.summarize(views)}

    def scan(self):
        return loomy_scan.discover()

    def import_local(self, db):
        """本机导入 —— **Loomy 的主路径**（明文 JSON，用户零操作）。"""
        return loomy_accounts.import_from_local(db)

    def import_record(self, db, record):
        return loomy_accounts.import_record(db, record)

    def refresh(self, db, account_id, *, force=False):
        from admin.models import Account

        account = db.query(Account).filter(
            Account.id == int(account_id), Account.platform == "loomy").first()
        if account is None:
            raise ValueError("账号不存在")
        return loomy_accounts.sync_quota(db, account)

    def keepalive(self, db):
        """保活：探活 + 同步余额。

        Loomy 的 session **不会续期**（没有 refresh 接口），
        所以「保活」在这里的实际含义是：

          1. 每轮探一次 `/points/balance`，确认 session 还能用
          2. 顺带把余额写回 DB（保活任务跑完，前端数字就是新的）
          3. session 失效就把账号标 disabled，让选号跳过它

        做不到续期就**不假装能续期** —— 失效会在返回里明确列出，
        用户需要去客户端重新登录。
        """
        ok, failed, refreshed = 0, [], 0
        for account in loomy_accounts.accounts_for(db, self.realm):
            realm = loomy_accounts.realm_of(account)
            try:
                data = loomy_accounts.ensure_token(db, account)
                token = loomy_accounts.token_of(data)
                points = client.fetch_points_balance(token, realm, account.id)
                loomy_accounts.apply_quota(account, points)
                db.commit()
                refreshed += 1
                ok += 1
            except client.UpstreamError as exc:
                if exc.kind == "auth":
                    # session 真的失效了 —— 标记后前端会显示「已禁用」
                    account.status = "disabled"
                    try:
                        db.commit()
                    except Exception:  # noqa: BLE001
                        db.rollback()
                failed.append({"account_id": account.id, "account": account.name,
                               "error": exc.message[:120]})
        return {"ok": not failed, "probed": ok, "refreshed": refreshed,
                "failed": failed,
                "note": "Loomy 无续期机制，session 失效需在客户端重新登录"}

    def checkin(self, db):
        """每日签到：领取当日赠送额度（5000 积分）。

        实测 `POST /points/first-login` 幂等 —— 已领过时返回
        `alreadyProcessed: true`，不是错误。
        """
        from admin.platform.loomy.services import checkin as checkin_service

        result = checkin_service.run_daily_checkin(db, realm=self.realm or "cn")
        return {"supported": True, **result}

    def credits(self, db):
        rows = loomy_accounts.accounts_for(db, self.realm)
        items = []
        for account in rows:
            try:
                snap = json.loads(account.credits_snapshot or "{}")
            except ValueError:
                snap = {}
            items.append({
                "id": account.id,
                "name": account.name,
                "available": int(getattr(account, "balance_remain", 0) or 0),
                "balance": int((snap or {}).get("balance") or 0),
                "daily_balance": int((snap or {}).get("daily_balance") or 0),
                "models": int((snap or {}).get("models") or 0),
                "unit": "points",
            })
        total = sum(i["available"] for i in items)
        return {"items": items, "unit": "points",
                "summary": {"available": total,
                            "accounts": len(items)},
                "note": "availableBalance = 基础余额 + 当日额度"}

    def usage(self, db, **filters):
        """用量：上游无明细接口，给本网关账本。"""
        days = int(filters.get("days") or 30)
        return {"realm": self.realm, "days": days,
                "source": "local_only",
                "local": _local_ledger(db, self.realm, days),
                "note": "上游不提供用量明细；以下为本网关记录（含 points_consumed 累计）"}

    def health(self, db, account_id):
        from admin.models import Account

        account = db.query(Account).filter(
            Account.id == int(account_id), Account.platform == "loomy").first()
        if account is None:
            raise ValueError("账号不存在")
        stages = {}
        data = loomy_accounts.credentials_of(account)
        token = loomy_accounts.token_of(data)
        stages["session"] = {"ok": bool(token), "detail": "len=%d" % len(token)}
        try:
            models = client.fetch_models(token, loomy_accounts.realm_of(account),
                                         account.id)
            stages["models"] = {"ok": bool(models), "detail": "%d 个模型" % len(models)}
        except client.UpstreamError as exc:
            stages["models"] = {"ok": False, "detail": exc.message[:100]}
        return {"account_id": account.id, "account": account.name,
                "ok": all(s["ok"] for s in stages.values()), "stages": stages}

    def inject(self, db, account_id):
        return {"supported": False,
                "summary": "Loomy 的 session 由客户端自管，本网关不注入"}

    def export(self, db, ids=None):
        rows = loomy_accounts.accounts_for(db, self.realm)
        if ids:
            wanted = {int(i) for i in ids}
            rows = [r for r in rows if r.id in wanted]
        out = []
        for account in rows:
            data = loomy_accounts.credentials_of(account)
            out.append({
                "id": account.id, "name": account.name, "uid": account.uid,
                "realm": loomy_accounts.realm_of(account),
                "token": loomy_accounts.token_of(data),
                "phone": str(data.get("phone") or ""),
            })
        return {"format": "backup", "platform": "loomy", "accounts": out}


class LoomyClientProbe:
    """本机探测。可改项：无（客户端配置由它自己管）。"""

    def __init__(self, realm: str = ""):
        self.realm = realm

    def dirs(self) -> list[str]:
        return list(loomy_scan.discover().get("data_dirs") or [])

    def probe(self) -> dict:
        result = loomy_scan.discover()
        # 补 app_path / app_version / ua（路径表在本平台 core/paths.py，
        # 不放总网关 —— 加平台不用改共享文件）
        from admin.platform.probe import enrich
        from admin.platform.loomy.core import paths as _paths

        return enrich(result,
                      exe_candidates=_paths.APP_EXE_CANDIDATES,
                      data_dirs=_paths.DATA_DIR_CANDIDATES,
                      product=_paths.UA_PRODUCT)


    def schema(self) -> list[dict]:
        return []

    def read(self, key: str):
        return None

    def write(self, key: str, value) -> dict:
        return {"supported": False, "summary": "Loomy 客户端参数只读"}


class LoomyGateway:
    """网关主体。"""

    #: ★ Protocol 要求 —— 缺了 isinstance 判 False
    platform_id = "loomy"

    def __init__(self, realm: str = ""):
        self.realm = realm or "cn"

    # ---- 转发（实现都在 gateway.py）----
    def chat_completions(self, request, body):
        from admin.platform.loomy import gateway as impl

        return impl.chat_completions(request)

    def responses(self, request, body):
        from admin.platform.loomy import gateway as impl

        return impl.responses(request)

    def messages(self, request, body):
        from admin.platform.loomy import gateway as impl

        return impl.messages(request)

    def count_tokens(self, request, body):
        from admin.platform.loomy import gateway as impl

        return impl.count_tokens(request)

    def list_models(self, request):
        from admin.platform.loomy import gateway as impl

        return impl.list_models(request)

    def balance(self, request):
        from admin.platform.loomy import gateway as impl

        return impl.balance(request)

    # ---- 契约 ----
    def account_ops(self) -> AccountOps:
        return LoomyAccountOps(self.realm)

    def task_runners(self) -> dict:
        """本平台的定时任务。

        `loomy_checkin` —— 每日签到，领取当日赠送额度（5000 积分）。

        之前这里返回 `{}`（当时误判「Loomy 没有签到接口」）。
        实测 `POST /points/first-login` 就是签到入口，且**幂等** ——
        当天第一次调触发发放，之后返回 `alreadyProcessed: true`。
        """
        from admin.platform.loomy import _task_runners as declared

        runners = dict(declared())
        # 保活：Loomy 没有 refresh 接口，但 ensure_token 会在本地判断
        # 过期并复用刚签发的 session；定时跑一次能把死号尽早标出来。
        runners["loomy_keepalive"] = self.account_ops().keepalive
        return runners

    def client_probe(self) -> ClientProbe:
        return LoomyClientProbe(self.realm)

    def overview_cards(self, db) -> list[dict]:
        rows = loomy_accounts.accounts_for(db, self.realm)
        total = len(rows)
        active = sum(1 for r in rows
                     if (getattr(r, "status", "") or "") == "active")
        available = sum(int(getattr(r, "balance_remain", 0) or 0) for r in rows)
        daily = sum(int(getattr(r, "credits_expiring_soon", 0) or 0) for r in rows)
        models = 0
        for account in rows:
            try:
                snap = json.loads(account.credits_snapshot or "{}")
            except ValueError:
                snap = {}
            models = max(models, int((snap or {}).get("models") or 0))

        return [
            {"key": "accounts", "label": "账号总数", "value": total,
             "sub": "启用 %d" % active, "icon": "fa-users", "tone": ""},
            {"key": "credits", "label": "可用积分",
             "value": "{:,}".format(available),
             "sub": "其中当日额度 %s" % "{:,}".format(daily),
             "icon": "fa-wallet", "tone": "brand"},
            {"key": "models", "label": "可用模型", "value": models,
             "sub": "来自上游 /models（已过滤图像模型）",
             "icon": "fa-cube", "tone": ""},
            {"key": "mode", "label": "接入方式", "value": "会话鉴权",
             "sub": "双 token 头 + traceparent", "icon": "fa-key", "tone": ""},
        ]


def _local_ledger(db, realm: str, days: int) -> dict:
    """本网关账本（直接查 usage_logs）。"""
    from datetime import datetime, timedelta

    from sqlalchemy import func

    from admin.models import Account, UsageLog

    since = datetime.utcnow() - timedelta(days=max(1, int(days)))
    query = db.query(
        UsageLog.model.label("model"),
        func.count(UsageLog.id).label("requests"),
        func.coalesce(func.sum(UsageLog.total_tokens), 0).label("total"),
    ).filter(UsageLog.platform == "loomy", UsageLog.created_at >= since)
    if realm:
        ids = [row[0] for row in db.query(Account.id).filter(
            Account.platform == "loomy", Account.domain == realm).all()]
        if not ids:
            return {"days": days, "items": [], "totals": {}}
        query = query.filter(UsageLog.account_id.in_(ids))
    items = [{"model": r.model or "-", "requests": int(r.requests or 0),
              "total_tokens": int(r.total or 0)}
             for r in query.group_by(UsageLog.model).all()]
    items.sort(key=lambda i: i["total_tokens"], reverse=True)
    return {"days": days, "items": items,
            "totals": {"requests": sum(i["requests"] for i in items),
                       "total_tokens": sum(i["total_tokens"] for i in items)}}


#: 无固定区域的实例
gateway = LoomyGateway("")
