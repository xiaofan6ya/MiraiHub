"""CodeArts Gateway 契约实现（账号池 / 任务 / 探测 / 概览卡片）。

## 这个平台的特殊性

* **凭证是 AK/SK + STS**，不是 Bearer；所有请求都要现算签名（`core/signer`）。
* **可以真正续期**：`refresh_token` + DPoP 私钥 + PKCE verifier 都在登录态里，
  所以「保活」不是空话 —— 它能把 6 小时的临时凭证一直续下去。
* **额度是积分包**（体验包 / 基础包 / 赠送包 / 按需），有明确的起止日期，
  所以概览卡片能把「其中多少会先过期」讲清楚。
* 没有签到（`checkin` 明确返回 `supported: False`，不伪造成功）。
"""
from __future__ import annotations

import json
import logging

from admin.platform.codearts.core import client
from admin.platform.codearts.services import accounts as ca
from admin.platform.codearts.services import scan as ca_scan
from admin.platform.gateway import AccountOps, ClientProbe, Gateway

logger = logging.getLogger("admin.platform.codearts.platform_gateway")

#: 一次后台刷新同步几个号（见 shared.kick_background_refresh）
QUOTA_SYNC_PER_LIST = 8
#: 后台刷新的最小间隔（秒）：翻页很频繁，不能每页都排一批上游请求
QUOTA_REFRESH_MIN_GAP = 10.0


def _kick_quota_refresh(account_ids: list[int]) -> bool:
    from admin.platform import shared

    return shared.kick_background_refresh(
        ca.PLATFORM, account_ids,
        lambda session, account: ca.sync_quota(session, account, with_models=False),
        max_items=QUOTA_SYNC_PER_LIST, min_gap=QUOTA_REFRESH_MIN_GAP)


def _snapshot_of(account) -> dict:
    return ca.snapshot_of(account)


class CodeArtsAccountOps:
    """账号池 11 项操作。"""

    def __init__(self, realm: str = ""):
        self.realm = realm

    # ---- 列表 ----
    def list(self, db, *, page: int | None = None, page_size: int = 50,
             search: str = "", status: str = "", sort: str = "id_desc",
             min_credits=None, max_credits=None):
        """账号列表。

        与 Loomy 同一套性能约定：**只读 DB 快照，绝不在请求线程里打上游**。
        过期的额度快照交给后台线程刷（单飞 + 限频 + 限量）。
        """
        from sqlalchemy import func as _func

        from admin.models import Account
        from admin.platform.shared import (account_order, apply_account_filters,
                                           summarize_accounts_sql)

        query = db.query(Account).filter(Account.platform == ca.PLATFORM)
        if self.realm:
            query = query.filter(Account.domain == self.realm)
        query = apply_account_filters(
            query, Account, search=search, status=status,
            min_credits=min_credits, max_credits=max_credits)
        query = account_order(query, Account, sort)

        if page is None:
            views = self._views_of(query.all())
            return {"items": views, "summary": ca.summarize(views)}

        total = int(query.with_entities(_func.count(Account.id)).scalar() or 0)
        rows = (query.offset((max(1, page) - 1) * page_size)
                .limit(page_size).all())
        views = self._views_of(rows)

        agg = db.query(Account).filter(Account.platform == ca.PLATFORM)
        if self.realm:
            agg = agg.filter(Account.domain == self.realm)
        agg = apply_account_filters(
            agg, Account, search=search, status="",
            min_credits=min_credits, max_credits=max_credits)
        return {"items": views, "summary": summarize_accounts_sql(agg, Account),
                "paginated": True, "total": total,
                "page": max(1, page), "page_size": page_size}

    def _views_of(self, accounts) -> list[dict]:
        views: list[dict] = []
        stale: list[int] = []
        for account in accounts:
            if not ca.quota_is_fresh(account):
                stale.append(int(account.id or 0))
            views.append(ca.public_view(account, quota=None))
        try:
            _kick_quota_refresh(stale)
        except Exception:  # noqa: BLE001 - 后台调度失败不影响列表
            logger.debug("后台额度刷新调度失败", exc_info=True)
        return views

    # ---- 扫描 / 导入 ----
    def scan(self):
        return ca_scan.discover()

    def import_local(self, db, uids: list[str] | None = None):
        """本机导入。uids 目前只有一个会话，传了也校验一下（避免导错）。"""
        result = ca.import_local(db)
        if uids and result.get("account"):
            if str(result["account"].get("uid") or "") not in {str(u) for u in uids}:
                return {"ok": False, "imported": 0,
                        "error": "本机登录态与所选账号不一致，请重新扫描"}
        return result

    def import_record(self, db, record):
        return ca.import_record(db, record)

    def import_batch(self, db, text: str, *, probe: bool = True, on_progress=None):
        return ca.import_batch(db, text, probe=probe, on_progress=on_progress)

    # ---- 刷新 / 保活 ----
    def refresh(self, db, account_id, *, force: bool = False):
        from admin.models import Account

        account = db.query(Account).filter(
            Account.id == int(account_id), Account.platform == ca.PLATFORM).first()
        if account is None:
            raise ValueError("账号不存在")
        if force:
            # 强制刷新 = 先续期（如果到期）再拉额度
            ca.ensure_credential(db, account, force=False)
        result = ca.sync_quota(db, account, with_models=True)
        if not result.get("ok"):
            error = result.get("error") or {}
            raise ValueError(error.get("message") or "刷新失败")
        return result

    def keepalive(self, db):
        """保活：续期 + 刷新额度。

        CodeArts 的临时 AK/SK 只有 6 小时，但**有 refresh_token 就能一直续**，
        所以这里能做到真正的「不掉线」；续期失败的号会被标 disabled 并列出来。
        """
        ok = refreshed = 0
        failed: list[dict] = []
        for account in ca.accounts_for(db, self.realm):
            try:
                ca.ensure_credential(db, account)
                result = ca.sync_quota(db, account, with_models=False)
                if result.get("ok"):
                    ok += 1
                    refreshed += 1
                else:
                    error = result.get("error") or {}
                    failed.append({"account_id": account.id, "account": account.name,
                                   "error": str(error.get("message") or "")[:120]})
            except client.UpstreamError as exc:
                failed.append({"account_id": account.id, "account": account.name,
                               "error": exc.message[:120]})
            except Exception as exc:  # noqa: BLE001 - 单号失败不影响整批
                failed.append({"account_id": account.id, "account": account.name,
                               "error": str(exc)[:120]})
        return {"ok": not failed, "probed": ok, "refreshed": refreshed,
                "failed": failed,
                "note": "CodeArts 支持 refresh_token 续期；续期失败的号需重新登录客户端"}

    def checkin(self, db):
        return {"supported": False,
                "summary": "CodeArts 没有签到接口；额度来自积分包（按购买/赠送）"}

    # ---- 额度 / 用量 ----
    def credits(self, db):
        items = []
        for account in ca.accounts_for(db, self.realm):
            snapshot = ca.snapshot_of(account)
            items.append({
                "id": account.id,
                "name": account.name,
                "available": int(account.balance_remain or 0),
                "total": int(account.balance_total or 0),
                "expiring": int(account.credits_expiring_soon or 0),
                "basic_remain": int(snapshot.get("basic_remain") or 0),
                "bonus_remain": int(snapshot.get("bonus_remain") or 0),
                "package_name": snapshot.get("package_name") or "",
                "end_date": snapshot.get("end_date") or "",
                "unit": "credits",
            })
        return {"items": items, "unit": "credits",
                "summary": {"available": sum(i["available"] for i in items),
                            "accounts": len(items)},
                "note": "可用积分 = usageTotalPackageCredit.package_credit_remain"}

    def usage(self, db, **filters):
        days = int(filters.get("days") or 30)
        return {"realm": self.realm, "days": days, "source": "local_only",
                "local": _local_ledger(db, self.realm, days),
                "note": "上游按次计费、不提供明细接口；以下为本网关账本"}

    def health(self, db, account_id):
        from admin.models import Account

        account = db.query(Account).filter(
            Account.id == int(account_id), Account.platform == ca.PLATFORM).first()
        if account is None:
            raise ValueError("账号不存在")
        stages: dict[str, dict] = {}
        data = ca.credentials_of(account)
        stages["credential"] = {
            "ok": bool(data.get("access_key") and data.get("secret_key")),
            "detail": "AK=%s… 到期 %s" % (
                str(data.get("access_key") or "")[:6],
                _expire_text(ca.expires_at_ms(data))),
        }
        try:
            identity = client.fetch_identity(data, account_id=int(account.id or 0))
            stages["identity"] = {"ok": bool(identity),
                                  "detail": "签名通过（caller-identity）"}
        except client.UpstreamError as exc:
            stages["identity"] = {"ok": False, "detail": exc.message[:100]}
        try:
            models = client.fetch_models(data, account_id=int(account.id or 0))
            stages["models"] = {"ok": bool(models),
                                "detail": "%d 个模型" % len(models)}
        except client.UpstreamError as exc:
            stages["models"] = {"ok": False, "detail": exc.message[:100]}
        try:
            quota = client.fetch_quota(data, account_id=int(account.id or 0))
            stages["quota"] = {"ok": True,
                               "detail": "剩余 %s 积分" % quota.get("remain")}
        except client.UpstreamError as exc:
            stages["quota"] = {"ok": False, "detail": exc.message[:100]}
        return {"account_id": account.id, "account": account.name,
                "ok": all(stage["ok"] for stage in stages.values()),
                "stages": stages}

    def inject(self, db, account_id):
        return {"supported": False,
                "summary": "CodeArts 登录态由客户端自管，本网关不回写（避免破坏客户端）"}

    def export(self, db, ids=None):
        return ca.export_accounts(db, ids)


class CodeArtsClientProbe:
    """本机探测：只读，不提供可改项（客户端配置由它自己管）。"""

    def __init__(self, realm: str = ""):
        self.realm = realm

    def dirs(self) -> list[str]:
        return ca_scan.installed_dirs()

    def probe(self) -> dict:
        from admin.platform import probe as probe_mod
        from admin.platform.codearts.core import paths as ca_paths

        result = ca_scan.discover()
        return probe_mod.enrich(result,
                                exe_candidates=ca_paths.app_exe_candidates(),
                                data_dirs=ca_paths.data_dir_candidates(),
                                product=ca_paths.UA_PRODUCT)

    def schema(self) -> list[dict]:
        return []

    def read(self, key: str):
        return None

    def write(self, key: str, value) -> dict:
        return {"supported": False, "summary": "CodeArts 客户端参数只读"}


class CodeArtsGateway:
    """网关主体。"""

    #: ★ Protocol 要求
    platform_id = "codearts"

    def __init__(self, realm: str = ""):
        self.realm = realm or "cn"

    # ---- 转发（实现在 gateway.py）----
    def chat_completions(self, request, body):
        from admin.platform.codearts import gateway as impl

        return impl.chat_completions(request)

    def responses(self, request, body):
        from admin.platform.codearts import gateway as impl

        return impl.responses(request)

    def messages(self, request, body):
        from admin.platform.codearts import gateway as impl

        return impl.messages(request)

    def count_tokens(self, request, body):
        from admin.platform.codearts import gateway as impl

        return impl.count_tokens(request)

    def list_models(self, request):
        from admin.platform.codearts import gateway as impl

        return impl.list_models(request)

    def balance(self, request):
        from admin.platform.codearts import gateway as impl

        return impl.balance(request)

    # ---- 契约 ----
    def account_ops(self) -> AccountOps:
        return CodeArtsAccountOps(self.realm)

    # ---- 凭证有效期 ----
    def credential_expiry(self, auth_json) -> dict:
        """CodeArts 的 STS 临时 AK/SK 到期 + 能否续。

        它的 AK/SK 只有 **6 小时**，但有 `refresh_token` + DPoP 私钥 + PKCE
        verifier，就能一直续 —— 所以「6 小时」不是死线，续期窗口才是。
        """
        from admin.platform import shared
        from admin.platform.codearts.services import accounts as ca_accounts

        data = shared.parse_auth_json(auth_json)
        if not data:
            return shared.credential_window()
        expires = shared.seconds_from(
            data.get("expires_at") or data.get("expiresAt")
            or ca_accounts.expires_at_ms(data) / 1000.0)
        has_refresh = bool(data.get("refresh_token") or data.get("refreshToken"))
        refresh_expires = shared.seconds_from(
            data.get("refresh_expires_at") or data.get("refreshExpiresAt"))
        return shared.credential_window(
            expires_at=expires,
            # 没给续期截止时间时**不要**拿 AK/SK 的 6 小时当续期窗口 ——
            # 那会让所有 CodeArts 号都显示「即将过期」，纯粹是误报。
            refresh_expires_at=refresh_expires or None,
            has_refresh=has_refresh)

    def renewal_guard_days(self) -> int:
        return 7

    def task_runners(self) -> dict:
        from admin.platform.codearts import _task_runners as declared

        runners = dict(declared())
        runners["codearts_keepalive"] = self.account_ops().keepalive
        return runners

    def client_probe(self) -> ClientProbe:
        return CodeArtsClientProbe(self.realm)

    def overview_cards(self, db) -> list[dict]:
        rows = ca.accounts_for(db, self.realm)
        total = len(rows)
        active = sum(1 for row in rows if (row.status or "") == "active")
        remain = sum(int(row.balance_remain or 0) for row in rows)
        full = sum(int(row.balance_total or 0) for row in rows)
        expiring = sum(int(row.credits_expiring_soon or 0) for row in rows)
        return [
            {"key": "accounts", "label": "账号总数", "value": total,
             "sub": "启用 %d · 已禁用 %d" % (active, total - active),
             "icon": "fa-users", "tone": ""},
            {"key": "credits", "label": "可用积分",
             "value": "{:,}".format(remain),
             "sub": "总额度 {:,} · 其中 {:,} 即将过期".format(full, expiring),
             "icon": "fa-wallet", "tone": "brand"},
            {"key": "mode", "label": "接入方式", "value": "AK/SK 签名",
             "sub": "华为云 APIG · STS 临时凭证自动续期",
             "icon": "fa-key", "tone": ""},
            {"key": "protocol", "label": "协议", "value": "OpenAI 兼容",
             "sub": "chat/completions 真流式 · 支持 tools",
             "icon": "fa-code", "tone": ""},
        ]


def _expire_text(expires_ms: int) -> str:
    if not expires_ms:
        return "未知"
    from datetime import datetime

    try:
        return datetime.fromtimestamp(expires_ms / 1000).strftime("%m-%d %H:%M")
    except (ValueError, OSError):
        return "未知"


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
    ).filter(UsageLog.platform == ca.PLATFORM, UsageLog.created_at >= since)
    if realm:
        ids = [row[0] for row in db.query(Account.id).filter(
            Account.platform == ca.PLATFORM, Account.domain == realm).all()]
        if not ids:
            return {"days": days, "items": [], "totals": {}}
        query = query.filter(UsageLog.account_id.in_(ids))
    items = [{"model": row.model or "-", "requests": int(row.requests or 0),
              "total_tokens": int(row.total or 0)}
             for row in query.group_by(UsageLog.model).all()]
    items.sort(key=lambda item: item["total_tokens"], reverse=True)
    return {"days": days, "items": items,
            "totals": {"requests": sum(item["requests"] for item in items),
                       "total_tokens": sum(item["total_tokens"] for item in items)}}


#: 无固定区域的实例（供 registry 直接取）
gateway = CodeArtsGateway("")
