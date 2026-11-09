"""腾讯 Marvis Gateway 契约实现。

## 三个必须照做的点（漏了就过不了 verify_platform.py）

1. `platform_id` **类属性**必须声明 —— 它是 Protocol 的一部分，
   缺了 `isinstance(gw, Gateway)` 会判 False，而错误信息不提示缺属性
2. 方法签名**与 Protocol 完全一致**（不能有默认值差异）
3. `install()` 幂等（重复调用要安静返回）
"""
from __future__ import annotations

import json
import logging

from admin.platform.gateway import AccountOps, ClientProbe, Gateway
from admin.platform.marvis.core import client
from admin.platform.marvis.services import accounts as marvis_accounts
from admin.platform.marvis.services import scan as marvis_scan

logger = logging.getLogger("admin.platform.marvis.platform_gateway")


class MarvisAccountOps:
    """账号池 11 项操作。"""

    def __init__(self, realm: str = ""):
        self.realm = realm

    def list(self, db):
        """账号列表。

        ## 铁律：列表**不许同步打上游**

        额度是**展示数据**。原模板是在这里对每个快照过期的号串行拉一次
        上游，50 个号实测把接口拖到 **2.8 秒**（几千个号就是几十秒），
        用户只会说「这个页面好卡」。

        正确做法：只读 `credits_snapshot` 立刻返回，过期号交给
        `shared.kick_background_refresh()` 在后台刷新
        （单飞 + 限频 10s + 一次 8 个 + 绝不冒泡）。

        写回 DB 列**仍然要发生**（admin 的通用 `/api/accounts` 只读列，
        不调本方法），只是发生在后台线程里，不占请求时间。
        """
        views = []
        stale = []
        for account in marvis_accounts.accounts_for(db, self.realm):
            if not marvis_accounts.quota_is_fresh(account):
                stale.append(int(account.id or 0))
            views.append(marvis_accounts.public_view(
                account, quota=marvis_accounts.snapshot_of(account)))
        if stale:
            from admin.platform import shared

            shared.kick_background_refresh(
                "marvis", stale,
                lambda session, account: marvis_accounts.sync_quota(session, account),
                max_items=8, min_gap=10.0)
        return {"items": views, "summary": marvis_accounts.summarize(views)}

    def scan(self):
        """扫描本机（**不含 token**）。"""
        return marvis_scan.discover()

    def import_local(self, db):
        """本机导入。

        读不出 token 时必须**显式返回 ok=False + reason**，
        不能假装导入成功 —— 那会让用户以为号已加进来。
        """
        found = marvis_scan.discover()
        return {"ok": False, "imported": 0,
                "scanned": len(found.get("items") or []),
                "reason": "no_plain_credential",
                "hint": found.get("hint") or ""}

    def import_record(self, db, record):
        return marvis_accounts.import_record(db, record)

    def refresh(self, db, account_id, *, force=False):
        from admin.models import Account

        account = db.query(Account).filter(
            Account.id == int(account_id), Account.platform == "marvis").first()
        if account is None:
            raise ValueError("账号不存在")
        return marvis_accounts.sync_quota(db, account)

    def keepalive(self, db):
        ok, failed = 0, []
        for account in marvis_accounts.accounts_for(db, self.realm):
            try:
                marvis_accounts.ensure_token(db, account)
                ok += 1
            except client.UpstreamError as exc:
                failed.append({"account_id": account.id, "error": exc.message[:120]})
        return {"ok": not failed, "refreshed": ok, "failed": failed}

    def checkin(self, db):
        """签到。**不支持就明确返回 supported=False。**"""
        return {"supported": False,
                "summary": "腾讯 Marvis 暂无签到接口"}

    def credits(self, db):
        data = self.list(db)
        return {"items": data["items"], "summary": data["summary"]}

    def usage(self, db, **filters):
        """用量。

        上游没有明细接口时，用本网关账本兜底（永远可查）。
        """
        days = int(filters.get("days") or 30)
        return {"realm": self.realm, "days": days,
                "source": "local_only",
                "local": _local_ledger(db, self.realm, days),
                "note": "上游未提供用量明细，以下为本网关记录"}

    def health(self, db, account_id):
        from admin.models import Account

        account = db.query(Account).filter(
            Account.id == int(account_id), Account.platform == "marvis").first()
        if account is None:
            raise ValueError("账号不存在")
        stages = {}
        data = marvis_accounts.credentials_of(account)
        token = marvis_accounts.token_of(data)
        stages["token"] = {"ok": bool(token), "detail": "len=%d" % len(token)}
        try:
            marvis_accounts.ensure_token(db, account)
            stages["refresh"] = {"ok": True, "detail": "ok"}
        except client.UpstreamError as exc:
            stages["refresh"] = {"ok": False, "detail": exc.message[:100]}
        return {"account_id": account.id, "account": account.name,
                "ok": all(s["ok"] for s in stages.values()), "stages": stages}

    def inject(self, db, account_id):
        return {"supported": False, "summary": "腾讯 Marvis 不做客户端注入"}

    def export(self, db, ids=None):
        rows = marvis_accounts.accounts_for(db, self.realm)
        if ids:
            wanted = {int(i) for i in ids}
            rows = [r for r in rows if r.id in wanted]
        return {"format": "backup", "platform": "marvis",
                "accounts": [{
                    "id": a.id, "name": a.name, "uid": a.uid,
                    "realm": marvis_accounts.realm_of(a),
                    "token": marvis_accounts.token_of(marvis_accounts.credentials_of(a)),
                } for a in rows]}


class MarvisClientProbe:
    """本机探测。可改项很少时如实返回空列表。"""

    def __init__(self, realm: str = ""):
        self.realm = realm

    def dirs(self) -> list[str]:
        return list(marvis_scan.discover().get("data_dirs") or [])

    def probe(self) -> dict:
        from admin.platform.probe import enrich
        from admin.platform.marvis.core import paths as _paths
        return enrich(marvis_scan.discover(),
                      exe_candidates=_paths.app_exe_candidates(),
                      data_dirs=_paths.data_dir_candidates(),
                      product=_paths.UA_PRODUCT)

    def schema(self) -> list[dict]:
        return []

    def read(self, key: str):
        return None

    def write(self, key: str, value) -> dict:
        return {"supported": False, "summary": "腾讯 Marvis 客户端参数只读"}


class MarvisGateway:
    """网关主体。"""

    #: ★ Protocol 要求 —— 缺了 isinstance 判 False
    platform_id = "marvis"

    def __init__(self, realm: str = ""):
        self.realm = realm or "cn"

    def chat_completions(self, request, body):
        from admin.platform.marvis import gateway as impl

        return impl.chat_completions(request)

    def responses(self, request, body):
        from admin.platform.marvis import gateway as impl

        return impl.responses(request)

    def messages(self, request, body):
        from admin.platform.marvis import gateway as impl

        return impl.messages(request)

    def count_tokens(self, request, body):
        from admin.platform.marvis import gateway as impl

        return impl.count_tokens(request)

    def list_models(self, request):
        from admin.platform.marvis import gateway as impl

        return impl.list_models(request)

    def balance(self, request):
        from admin.platform.marvis import gateway as impl

        return impl.balance(request)

    def account_ops(self) -> AccountOps:
        return MarvisAccountOps(self.realm)

    def task_runners(self) -> dict:
        """本平台的定时任务：额度同步。

        Marvis 没有签到 / 保活的概念 —— 额度是**令牌钱包**，每天按自然日重置，
        不需要「领」。唯一值得定时做的是把钱包读回来，否则卡片上的数字会一直
        停在最后一次手工刷新时的值。
        """
        return {"marvis_refresh_quota": _run_refresh_quota}

    def client_probe(self) -> ClientProbe:
        return MarvisClientProbe(self.realm)

    def overview_cards(self, db) -> list[dict]:
        rows = marvis_accounts.accounts_for(db, self.realm)
        total = len(rows)
        active = sum(1 for r in rows
                     if (getattr(r, "status", "") or "") == "active")
        remain = sum(int(getattr(r, "balance_remain", 0) or 0) for r in rows)
        limit = sum(int(getattr(r, "balance_total", 0) or 0) for r in rows)
        return [
            {"key": "accounts", "label": "账号总数", "value": total,
             "sub": "启用 %d" % active, "icon": "fa-users", "tone": ""},
            {"key": "credits", "label": "可用额度", "value": remain,
             "sub": "总额度 %d" % limit, "icon": "fa-wallet", "tone": "brand"},
            {"key": "mode", "label": "接入方式", "value": "反向代理",
             "sub": "腾讯 Marvis", "icon": "fa-network-wired", "tone": ""},
        ]


def _local_ledger(db, realm: str, days: int) -> dict:
    """本网关账本（直接查 usage_logs，不依赖路由函数签名）。"""
    from datetime import datetime, timedelta

    from sqlalchemy import func

    from admin.models import Account, UsageLog

    since = datetime.utcnow() - timedelta(days=max(1, int(days)))
    query = db.query(
        UsageLog.model.label("model"),
        func.count(UsageLog.id).label("requests"),
        func.coalesce(func.sum(UsageLog.total_tokens), 0).label("total"),
    ).filter(UsageLog.platform == "marvis", UsageLog.created_at >= since)
    if realm:
        ids = [row[0] for row in db.query(Account.id).filter(
            Account.platform == "marvis", Account.domain == realm).all()]
        if not ids:
            return {"days": days, "items": [], "totals": {}}
        query = query.filter(UsageLog.account_id.in_(ids))
    items = [{"model": r.model or "-", "requests": int(r.requests or 0),
              "total_tokens": int(r.total or 0)}
             for r in query.group_by(UsageLog.model).all()]
    return {"days": days, "items": items,
            "totals": {"requests": sum(i["requests"] for i in items),
                       "total_tokens": sum(i["total_tokens"] for i in items)}}


def _run_refresh_quota(db, schedule=None) -> dict:
    """定时任务：把所有启用账号的令牌钱包读一遍。

    与「刷新余额」按钮同一个函数（`services.accounts.sync_quota`），
    区别只是这里并发跑全池、逐条容错。

    为什么值得定时跑：钱包额度按自然日重置，不读回来卡片就一直停在旧数字，
    智能路由也会按过期余额排序 —— 用户看到的是「这个号还剩很多」但其实早已重置。
    """
    from admin.platform import shared

    rows = [a for a in marvis_accounts.accounts_for(db, "")
            if (getattr(a, "status", "") or "") == "active"]
    if not rows:
        return {"accounts": 0, "ok": 0, "failed": 0, "note": "没有启用账号"}

    results: list[dict] = []

    def worker(session, account) -> dict:
        try:
            wallet = marvis_accounts.sync_quota(session, account)
            return {"id": account.id, "ok": True,
                    "avail": wallet.get("avail"),
                    "total": wallet.get("total")}
        except Exception as exc:  # noqa: BLE001 - 单号失败不影响别人
            return {"id": account.id, "ok": False, "error": str(exc)[:120]}

    for item in shared.run_account_batch(
            [a.id for a in rows], worker, label="marvis-refresh-quota"):
        if item:
            results.append(item)

    ok = sum(1 for r in results if r.get("ok"))
    return {"accounts": len(rows), "ok": ok, "failed": len(results) - ok,
            "items": results[:200]}


#: 无固定区域的实例
gateway = MarvisGateway("")
