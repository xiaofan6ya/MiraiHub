"""小浣熊管理契约：账号池 11 项操作 + 客户端探测 + 概览卡片。

admin 只认 `AccountOps` / `ClientProbe` 这两个 Protocol，不认识「小浣熊」。
所以本文件把平台知识全部封装起来，加/删平台时 admin 一行不用改。

## 几个刻意的取舍

1. **scan 返回 `supported: False`**
   小浣熊客户端把登录态存在 Electron LevelDB 里（非明文），且本机常常
   根本没装。`scan()` 必须能安全地直接回给前端，所以这里明确说「不支持
   本机扫描」，而不是返回空列表让用户以为「客户端没登录」。

2. **checkin 返回 `supported: False`**
   小浣熊没有签到接口。契约里写得很清楚：不要伪造成功。

3. **inject 返回 `supported: False`**
   往客户端写回凭证需要改写 LevelDB，风险高且没必要 —— 用户要的是
   网关能调，不是让客户端显示已登录。

4. **usage 只有本地账本**
   上游不提供按 key 的调用明细，伪造一份会误导。
"""
from __future__ import annotations

import json
import logging

from admin.platform.gateway import AccountOps, ClientProbe
from admin.platform.raccoon.core import client, endpoints
from admin.platform.raccoon.services import accounts as raccoon_accounts

logger = logging.getLogger("admin.platform.raccoon.platform_gateway")
PLATFORM = endpoints.PLATFORM


def _get_account(db, account_id):
    from admin.models import Account

    account = db.query(Account).filter(
        Account.id == int(account_id), Account.platform == PLATFORM).first()
    if account is None:
        raise ValueError("账号不存在")
    return account


class RaccoonAccountOps:
    """账号池的 11 项操作。"""

    def __init__(self, realm: str = ""):
        self.realm = realm

    # -- 读 ---------------------------------------------------------------
    def list(self, db):
        """账号列表。

        **会同步 DB 列**：admin 的 `/api/accounts` 读的是表列，不写回就会
        显示旧余额。只在缓存过期时才打上游，避免一刷页面就把所有号探一遍。
        """
        views = []
        for account in raccoon_accounts.accounts_for(db, self.realm):
            quota = None
            if not raccoon_accounts.quota_is_fresh(account):
                # 被动刷新：**不允许**因一次鉴权失败就把账号永久禁用。
                # 列表页会对每个缓存过期的号调这里，一次网络抖动或上游
                # 临时 403 不该把整页账号标死。禁用交给主动的
                # refresh / keepalive 任务（那里 mark_disabled 默认 True）。
                result = raccoon_accounts.sync_quota(db, account,
                                                     mark_disabled=False)
                if result.get("ok"):
                    quota = result.get("quota")
                # partial：凭证有效但积分接口不可读 —— 沿用上次快照，
                # 不因为「读不到余额」就把账号从列表里判定为不可用。
            else:
                try:
                    snap = json.loads(account.credits_snapshot or "{}")
                except ValueError:
                    snap = {}
                quota = snap if isinstance(snap, dict) else None
            views.append(raccoon_accounts.public_view(account, quota=quota))
        db.commit()
        return {"items": views, "summary": raccoon_accounts.summarize(views)}

    def scan(self):
        """本机扫描：不支持。

        返回结构保持与支持扫描的平台一致（`items` 为空 + `supported`），
        这样前端「扫描本机」按钮点下去能得到一句人话解释，而不是空列表。
        """
        return {
            "supported": False,
            "items": [],
            "data_dirs": [],
            "reason": "electron_leveldb",
            "summary": "小浣熊登录态存在 Electron LevelDB 中，不是明文文件，无法直接扫描",
            "hint": "请在 agent2api 中登录后导出账号 JSON，再用「导入」批量添加；"
                    "或直接粘贴 JWT。",
        }

    def import_local(self, db):
        """本机导入：与 scan 同理，不支持。"""
        return {
            "supported": False,
            "imported": 0,
            "reason": "electron_leveldb",
            "hint": "请使用「导入凭证」粘贴 agent2api 导出的 JSON 或裸 JWT。",
        }

    def import_record(self, db, record):
        """导入单条。返回 `(账号, 是否新建)`（契约要求）。"""
        account, created = raccoon_accounts.import_record(db, record)
        raccoon_accounts.sync_quota(db, account)
        db.refresh(account)
        return account, created

    # -- 写 ---------------------------------------------------------------
    def refresh(self, db, account_id, *, force: bool = False):
        account = _get_account(db, account_id)
        return raccoon_accounts.sync_quota(db, account)

    def keepalive(self, db):
        """续期临期 JWT + 探活。

        小浣熊**有** refresh 接口，但实测 refreshToken == accessToken，
        所以这是「同票换新票」：能延 exp，救不回被吊销的会话。
        做不到的事在返回里写清楚，不假装。
        """
        renewed = 0
        alive = 0
        partial = 0
        failed = []
        for account in raccoon_accounts.accounts_for(db, self.realm):
            if (getattr(account, "status", "") or "") != "active":
                continue
            try:
                before = raccoon_accounts.credentials_of(account).get("token")
                raccoon_accounts.ensure_token(db, account)
                after = raccoon_accounts.credentials_of(account).get("token")
                if before != after:
                    renewed += 1
                result = raccoon_accounts.sync_quota(db, account)
                if result.get("ok"):
                    alive += 1
                elif result.get("partial"):
                    # 凭证有效、只是积分接口读不到（当前上游对本批账号
                    # 统一 403）。这**不是**账号失效，绝不能报成 failed ——
                    # 否则保活会「上线即全红」，运维以为号池挂了。
                    partial += 1
                    alive += 1
                else:
                    failed.append({"account_id": account.id, "account": account.name,
                                   "error": (result.get("error") or {}).get("message", "")[:120]})
            except client.UpstreamError as exc:
                failed.append({"account_id": account.id, "account": account.name,
                               "error": exc.message[:120]})
        return {
            "ok": not failed,
            "renewed": renewed,
            "probed": alive,
            "partial": partial,
            "failed": failed,
            "note": "小浣熊 refreshToken 与 accessToken 同值，续期只能推迟 exp；"
                    "被吊销的会话需回官方客户端重新登录。"
                    "partial 表示凭证有效但积分接口不可读（上游现状），不计入失败",
        }

    def checkin(self, db):
        """没有签到接口 —— 明确说不支持。"""
        return {
            "supported": False,
            "summary": "小浣熊没有签到接口；额度按积分计量，每日额度由客户端打开时发放",
        }

    # -- 统计 -------------------------------------------------------------
    def credits(self, db):
        rows = raccoon_accounts.accounts_for(db, self.realm)
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
                "balance": int((snap or {}).get("available") or 0),
                "daily_balance": int((snap or {}).get("daily_balance") or 0),
                "monthly_balance": int((snap or {}).get("monthly_balance") or 0),
                "reward": int((snap or {}).get("reward") or 0),
                "models": int((snap or {}).get("models") or 0),
                "unit": "points",
            })
        total = sum(i["available"] for i in items)
        daily = sum(i["daily_balance"] for i in items)
        return {
            "items": items,
            "unit": "points",
            "summary": {"available": total, "daily": daily, "accounts": len(items)},
            "note": "available = available_points；daily 每日重置，属会过期额度",
        }

    def usage(self, db, **filters):
        days = int(filters.get("days") or 30)
        return {
            "realm": self.realm,
            "days": days,
            "source": "local_only",
            "local": _local_ledger(db, self.realm, days),
            "note": "小浣熊上游不提供按 key 的调用明细；以下为本网关账本",
        }

    # -- 诊断 -------------------------------------------------------------
    def health(self, db, account_id):
        """连通性检查：分段返回，让前端能指出「哪一段坏了」。"""
        account = _get_account(db, account_id)
        stages = {}
        data = raccoon_accounts.credentials_of(account)
        token = raccoon_accounts.token_of(data)
        realm = raccoon_accounts.realm_of(account)

        stages["token"] = {"ok": bool(token), "detail": "len=%d" % len(token)}
        claims = raccoon_accounts.jwt_claims(token)
        stages["jwt"] = {
            "ok": claims.get("iss") == "xiaohuanxiong.com/iam",
            "detail": "iss=%s sub=%s" % (claims.get("iss") or "-",
                                         (claims.get("sub") or "-")[:12]),
        }
        try:
            models = client.fetch_models(token, realm, account.id)
            stages["models"] = {"ok": bool(models), "detail": "%d 个模型" % len(models)}
        except client.UpstreamError as exc:
            stages["models"] = {"ok": False, "detail": exc.message[:100]}
        try:
            points = client.fetch_points(token, realm, account.id)
            stages["points"] = {
                "ok": points.get("available", 0) > 0,
                "detail": "可用 %s points（当日 %s）" % (
                    points.get("available"), points.get("daily_balance")),
            }
        except client.UpstreamError as exc:
            stages["points"] = {"ok": False, "detail": exc.message[:100]}
        return {
            "account_id": account.id,
            "account": account.name,
            "ok": all(s.get("ok") for s in stages.values()),
            "stages": stages,
        }

    def inject(self, db, account_id):
        return {"supported": False,
                "summary": "写入客户端需要改 Electron LevelDB，风险高且无必要；"
                           "网关能调即可"}

    def export(self, db, ids=None):
        rows = raccoon_accounts.export_records(db, ids)
        return {"format": "agent2api-account", "platform": PLATFORM,
                "accounts": rows}


class RaccoonClientProbe:
    """客户端探测。

    小浣熊客户端可选安装，且本项目不依赖它（凭证来自导入）。
    所以这里只回答「装没装、在哪」，可改项为空 —— 我们没有它的配置契约。
    """

    def __init__(self, realm: str = ""):
        self.realm = realm

    def dirs(self) -> list[str]:
        import os

        candidates = [
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\raccoon"),
            os.path.expandvars(r"%LOCALAPPDATA%\raccoon"),
            os.path.expandvars(r"%LOCALAPPDATA%\xiaohuanxiong"),
            os.path.expandvars(r"%APPDATA%\raccoon"),
            os.path.expandvars(r"%APPDATA%\xiaohuanxiong"),
        ]
        return [p for p in candidates if p and os.path.isdir(p)]

    def probe(self) -> dict:
        import os

        from admin.platform.probe import enrich
        from admin.platform.raccoon.core import paths as _paths

        result = {
            "platform": PLATFORM,
            "supported": False,
            "data_dirs": self.dirs(),
            "installed": bool(self.dirs()),
            "note": "凭证不依赖本机客户端；此处仅报告是否安装",
        }
        return enrich(result,
                      exe_candidates=_paths.APP_EXE_CANDIDATES,
                      data_dirs=_paths.DATA_DIR_CANDIDATES,
                      product=_paths.UA_PRODUCT)

    def schema(self) -> list[dict]:
        return []

    def read(self, key: str):
        return None

    def write(self, key: str, value) -> dict:
        return {"supported": False, "summary": "小浣熊客户端参数只读"}


class RaccoonGateway:
    """网关主体。"""

    #: ★ Protocol 要求 —— 缺了 isinstance 判 False
    platform_id = PLATFORM

    def __init__(self, realm: str = ""):
        self.realm = realm or "cn"

    # ---- 转发（实现都在 gateway.py）----
    def chat_completions(self, request, body):
        from admin.platform.raccoon import gateway as impl

        return impl.chat_completions(request)

    def responses(self, request, body):
        from admin.platform.raccoon import gateway as impl

        return impl.responses(request)

    def messages(self, request, body):
        from admin.platform.raccoon import gateway as impl

        return impl.messages(request)

    def count_tokens(self, request, body):
        from admin.platform.raccoon import gateway as impl

        return impl.count_tokens(request)

    def list_models(self, request):
        from admin.platform.raccoon import gateway as impl

        return impl.list_models(request)

    def balance(self, request):
        from admin.platform.raccoon import gateway as impl

        return impl.balance(request)

    # ---- 契约 ----
    def account_ops(self) -> AccountOps:
        return RaccoonAccountOps(self.realm)

    def task_runners(self) -> dict:
        from admin.platform.raccoon import _task_runners as declared
        from admin.platform.raccoon.services import tasks

        runners = dict(declared())
        runners["raccoon_expiry_sweep"] = tasks.run_expiry_sweep
        return runners

    def client_probe(self) -> ClientProbe:
        return RaccoonClientProbe(self.realm)

    def overview_cards(self, db) -> list[dict]:
        rows = raccoon_accounts.accounts_for(db, self.realm)
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
             "sub": "来自上游 /model_catalog", "icon": "fa-cube", "tone": ""},
            {"key": "mode", "label": "接入方式", "value": "Bearer JWT",
             "sub": "凭证导入（无本机扫描）", "icon": "fa-key", "tone": ""},
        ]


def _local_ledger(db, realm: str, days: int) -> dict:
    """本网关账本（查 usage_logs）。"""
    from datetime import datetime, timedelta

    from sqlalchemy import func

    from admin.models import Account, UsageLog

    since = datetime.utcnow() - timedelta(days=max(1, int(days)))
    query = db.query(
        UsageLog.model.label("model"),
        func.count(UsageLog.id).label("requests"),
        func.coalesce(func.sum(UsageLog.total_tokens), 0).label("total"),
    ).filter(UsageLog.platform == PLATFORM, UsageLog.created_at >= since)
    if realm:
        ids = [row[0] for row in db.query(Account.id).filter(
            Account.platform == PLATFORM, Account.domain == realm).all()]
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
gateway = RaccoonGateway("")
