"""Qoder Gateway 契约实现：账号池 11 项 + 任务 + 探测 + 概览卡片。"""
from __future__ import annotations

import json
import logging

from admin.platform.gateway import AccountOps, ClientProbe, Gateway
from admin.platform.qoder.core import client, endpoints
from admin.platform.qoder.services import accounts as qoder_accounts
from admin.platform.qoder.services import chat as qoder_chat
from admin.platform.qoder.services import checkin as qoder_checkin
from admin.platform.qoder.services import scan as qoder_scan

logger = logging.getLogger("admin.platform.qoder.platform_gateway")


def _apply_usage(account, usage: dict) -> None:
    """把上游额度写回统一列（与 `sync_quota` 同口径）。

    Qoder 有两个桶：`userQuota`（套餐）+ `addOnQuota`（资源包/签到）。
    `balance_remain/total` 存**两个桶之和**（用户关心「总共还能用多少」），
    同时把明细留在 `credits_snapshot` 里供界面区分。
    """
    from datetime import datetime

    user_quota = usage.get("userQuota") or {}
    addon_quota = usage.get("addOnQuota") or {}
    remain = float(user_quota.get("remaining") or 0) + float(
        addon_quota.get("remaining") or 0)
    total = float(user_quota.get("total") or 0) + float(
        addon_quota.get("total") or 0)
    account.balance_remain = int(round(remain))
    account.balance_total = int(round(total))
    account.credits_synced_at = datetime.utcnow()
    account.last_sync_at = datetime.utcnow()
    account.credits_snapshot = json.dumps({
        "realm": getattr(account, "domain", "") or "cn",
        "plan": usage.get("userType") or "",
        "unit": "credits",
        "user_quota": user_quota,
        "addon_quota": addon_quota,
        "total_usage_percentage": usage.get("totalUsagePercentage"),
        "is_quota_exceeded": usage.get("isQuotaExceeded"),
        "expires_at": usage.get("expiresAt"),
    }, ensure_ascii=False)[:20000]


class QoderAccountOps:
    """账号池 11 项操作。"""

    def __init__(self, realm: str = ""):
        self.realm = realm

    # ---- 列表 ----
    def list(self, db):
        """账号列表（**会同步 DB 余额**）。

        ## 为什么要写回 DB

        admin 的通用 `/api/accounts` 读 `Account.balance_remain` 这一列，
        不调本方法。如果只在这里实时拉、不写回，前端「账号池」表格会一直
        显示旧余额 —— 表现就是「消耗了积分，余额没变」。

        ## 为什么带 TTL

        列表是高频读（每次切平台、每次刷新页面），每次全量拉上游会打爆它。
        `QUOTA_TTL`（60 秒）内直接用缓存，超出才拉。
        """
        usage_map = {}
        rows = qoder_accounts.accounts_for(db, self.realm)
        refreshed = False
        for account in rows:
            if qoder_accounts.quota_is_fresh(account):
                # 新鲜期内：用 DB 快照，但也要返回给视图用
                try:
                    snap = json.loads(account.credits_snapshot or "{}")
                except ValueError:
                    snap = {}
                usage_map[account.id] = {
                    "userQuota": snap.get("user_quota") or {},
                    "addOnQuota": snap.get("addon_quota") or {},
                    "userType": snap.get("plan") or "",
                } if isinstance(snap, dict) else {}
                continue
            try:
                data = qoder_accounts.credentials_of(account)
                usage = client.fetch_usage(qoder_accounts.token_of(data),
                                           qoder_accounts.realm_of(account))
                usage_map[account.id] = usage
                # 写回 DB 列：让 /api/accounts 与概览卡片也看到新值
                _apply_usage(account, usage)
                refreshed = True
            except Exception:  # noqa: BLE001 - 单账号失败不影响列表
                usage_map[account.id] = {}
        if refreshed:
            db.commit()

        views = [qoder_accounts.public_view(a, usage=usage_map.get(a.id))
                 for a in rows]
        return {"items": views, "summary": qoder_accounts.summarize(views)}

    # ---- 扫描本机 ----
    def scan(self):
        return qoder_scan.discover()

    # ---- 导入 ----
    def import_local(self, db):
        """本机导入。

        Qoder 的凭证加密在原生层（`auth.v1.dat`），**无法直接读出 token** ——
        所以这里明确返回「需要 PAT 或设备授权」，而不是假装导入成功。
        """
        found = qoder_scan.discover()
        return {
            "ok": False,
            "imported": 0,
            "scanned": len(found.get("items") or []),
            "data_dirs": found.get("data_dirs") or [],
            "hint": found.get("hint") or "",
            "reason": "credential_encrypted",
        }

    def import_record(self, db, record):
        if isinstance(record, dict) and record.get("pat"):
            return qoder_accounts.import_pat(
                db, str(record["pat"]), str(record.get("realm") or self.realm or "cn"))
        payload = record if isinstance(record, dict) else {}
        if not payload.get("realm"):
            payload["realm"] = self.realm or "cn"
        return qoder_accounts.import_record(db, payload)

    # ---- 刷新 ----
    def refresh(self, db, account_id, *, force=False):
        from admin.models import Account

        account = db.query(Account).filter(
            Account.id == int(account_id), Account.platform == "qoder").first()
        if account is None:
            raise ValueError("账号不存在")
        result = qoder_accounts.sync_quota(db, account)
        if not result.get("ok"):
            return {"ok": False, "error": result.get("error")}
        return {"ok": True, "balance_remain": result["remain"],
                "balance_total": result["total"],
                "user_quota": result.get("user_quota"),
                "addon_quota": result.get("addon_quota"),
                "plan": result.get("plan")}

    # ---- 保活 ----
    def keepalive(self, db):
        ok = 0
        failed = []
        for account in qoder_accounts.accounts_for(db, self.realm):
            try:
                qoder_accounts.ensure_token(db, account)
                ok += 1
            except client.UpstreamError as exc:
                failed.append({"account_id": account.id, "account": account.name,
                               "error": exc.message[:160]})
        return {"ok": not failed, "refreshed": ok, "failed": failed}

    # ---- 签到 ----
    def checkin(self, db):
        return qoder_checkin.run_daily_checkin(db, None, self.realm)

    # ---- 额度 ----
    def credits(self, db):
        rows = qoder_accounts.accounts_for(db, self.realm)
        items = []
        for account in rows:
            try:
                data = qoder_accounts.credentials_of(account)
                usage = client.fetch_usage(qoder_accounts.token_of(data),
                                           qoder_accounts.realm_of(account))
            except Exception:  # noqa: BLE001
                usage = {}
            user_quota = usage.get("userQuota") or {}
            addon_quota = usage.get("addOnQuota") or {}
            items.append({
                "id": account.id, "name": account.name,
                "plan": usage.get("userType") or "",
                "user_remain": user_quota.get("remaining"),
                "user_total": user_quota.get("total"),
                "addon_remain": addon_quota.get("remaining"),
                "addon_total": addon_quota.get("total"),
                "expires_at": usage.get("expiresAt"),
                "exceeded": bool(usage.get("isQuotaExceeded")),
            })
        return {"items": items, "unit": "credits",
                "summary": qoder_accounts.summarize(
                    [{"status": "active" if i["user_remain"] else "disabled",
                      "balance_remain": float(i["user_remain"] or 0)
                      + float(i["addon_remain"] or 0),
                      "balance_total": float(i["user_total"] or 0)
                      + float(i["addon_total"] or 0)}
                     for i in items])}

    # ---- 用量 ----
    def usage(self, db, **filters):
        """用量。

        Qoder 上游**没有**按会话的用量明细接口（拆包确认：`/algo` 族只有
        model/list 与 chat SSE）。所以这里给两个来源：

          * `quota`   上游额度桶（套餐 + 资源包）
          * `local`   本网关账本（按模型聚合的 token）
        """
        days = int(filters.get("days") or 30)
        items = []
        for account in qoder_accounts.accounts_for(db, self.realm):
            try:
                data = qoder_accounts.credentials_of(account)
                usage = client.fetch_usage(qoder_accounts.token_of(data),
                                           qoder_accounts.realm_of(account))
            except Exception:  # noqa: BLE001
                usage = {}
            items.append({"account_id": account.id, "account": account.name,
                          "plan": usage.get("userType"),
                          "user_quota": usage.get("userQuota"),
                          "addon_quota": usage.get("addOnQuota"),
                          "expires_at": usage.get("expiresAt")})

        local = _local_ledger(db, self.realm, days)
        return {"realm": self.realm, "days": days,
                "source": "upstream_quota", "total": len(items),
                "quota": items, "local": local,
                "note": "Qoder 上游不提供按会话的用量明细；"
                        "上面是额度桶，下面是本网关账本。"}

    # ---- 健康检查 ----
    def health(self, db, account_id):
        from admin.models import Account

        account = db.query(Account).filter(
            Account.id == int(account_id), Account.platform == "qoder").first()
        if account is None:
            raise ValueError("账号不存在")
        stages = {}
        realm = qoder_accounts.realm_of(account)
        data = qoder_accounts.credentials_of(account)
        token = qoder_accounts.token_of(data)
        stages["token"] = {"ok": bool(token),
                           "detail": "%s…（%s）" % (token[:6], len(token)) if token else "缺失"}
        try:
            profile = client.fetch_profile(token, realm)
            stages["profile"] = {"ok": True,
                                 "detail": str(profile.get("name") or "")}
        except client.UpstreamError as exc:
            stages["profile"] = {"ok": False, "detail": exc.message[:100]}
        try:
            catalog = client.fetch_model_catalog(
                token, realm, uid=str(data.get("uid") or ""),
                name=str(data.get("name") or ""),
                email=str(data.get("email") or ""),
                machine_id=qoder_accounts.machine_id_of(data))
            count = len(catalog.get("chat") or [])
            stages["models"] = {"ok": count > 0, "detail": "%d 个模型" % count}
        except client.UpstreamError as exc:
            stages["models"] = {"ok": False, "detail": exc.message[:100]}
        try:
            usage = client.fetch_usage(token, realm)
            quota = usage.get("userQuota") or {}
            stages["quota"] = {"ok": True,
                               "detail": "剩余 %s / %s" % (
                                   quota.get("remaining"), quota.get("total"))}
        except client.UpstreamError as exc:
            stages["quota"] = {"ok": False, "detail": exc.message[:100]}
        return {"account_id": account.id, "account": account.name,
                "ok": all(s.get("ok") for s in stages.values()),
                "stages": stages}

    # ---- 注入客户端 ----
    def inject(self, db, account_id):
        """Qoder 没有「注入客户端」概念（凭证加密在原生层）。

        显式返回不支持 —— 缺方法无法和「忘了实现」区分开。
        """
        return {"supported": False,
                "summary": "Qoder 的凭证由客户端自管（auth.v1.dat 加密），"
                           "本网关不做注入。"}

    # ---- 导出 ----
    def export(self, db, ids=None):
        rows = qoder_accounts.accounts_for(db, self.realm)
        if ids:
            wanted = {int(i) for i in ids}
            rows = [r for r in rows if r.id in wanted]
        accounts = []
        for account in rows:
            data = qoder_accounts.credentials_of(account)
            accounts.append({
                "id": account.id, "name": account.name, "uid": account.uid,
                "realm": qoder_accounts.realm_of(account),
                # 凭证明文导出：仅在显式要求备份时给出，且带明确标记
                "token": qoder_accounts.token_of(data),
                "refresh_token": str(data.get("refresh_token") or ""),
                "expires_at": int(data.get("expires_at") or 0),
                "machine_id": str(data.get("machine_id") or ""),
            })
        return {"format": "backup", "platform": "qoder",
                "exported_at": __import__("time").time(),
                "accounts": accounts}


class QoderClientProbe:
    """本机探测（Qoder 的可改项很少，如实返回）。

    `.models/<uid>/catalog-v7` 是 QMC 容器（base64 + 自定义编码），
    不作为可改项暴露 —— 改坏了客户端会不认。
    """

    def __init__(self, realm: str = ""):
        self.realm = realm

    def dirs(self) -> list[str]:
        found = qoder_scan.discover()
        return list(found.get("data_dirs") or []) + list(found.get("cli_dirs") or [])

    def probe(self) -> dict:
        found = qoder_scan.discover()
        result = {
            "platform": "qoder",
            "data_dirs": found.get("data_dirs") or [],
            "cli_dirs": found.get("cli_dirs") or [],
            "live_user_data_dir": found.get("live_user_data_dir"),
            "machine_id": found.get("machine_id"),
            "app_status": found.get("app_status") or {},
            "accounts_found": len(found.get("items") or []),
            "catalogs": found.get("catalogs") or [],
        }
        # 补 app_path / app_version / ua（路径表在本平台 core/paths.py，
        # 不放总网关 —— 加平台不用改共享文件）
        from admin.platform.probe import enrich
        from admin.platform.qoder.core import paths as _paths

        return enrich(result,
                      exe_candidates=_paths.APP_EXE_CANDIDATES,
                      data_dirs=_paths.DATA_DIR_CANDIDATES,
                      product=_paths.UA_PRODUCT)

    def schema(self) -> list[dict]:
        # 只暴露真正可改、且改了不影响登录的项
        return [{
            "key": "machine_id",
            "label": "机器标识",
            "type": "string",
            "description": "COSY 签名用的机器 id。改动会让上游认为换了设备。",
        }]

    def read(self, key: str):
        if key == "machine_id":
            return qoder_scan.machine_id_from_cli()
        return None

    def write(self, key: str, value) -> dict:
        return {"supported": False,
                "summary": "Qoder 的客户端参数由客户端自管，本网关只读。"}


class QoderGateway:
    """网关主体。"""

    #: 协议要求（`Gateway.platform_id`）。
    #: 缺了它 `isinstance(gw, Gateway)` 会判 False，而错误信息只写
    #: 「不满足协议」，看不出是缺一个属性 —— 这是最容易漏的一条。
    platform_id = "qoder"

    def __init__(self, realm: str = ""):
        self.realm = realm or "cn"

    # ---- 转发 ----
    def chat_completions(self, request, body):
        from admin.platform.qoder import gateway as qoder_gateway

        return qoder_gateway.chat_completions(request)

    def responses(self, request, body):
        from admin.platform.qoder import gateway as qoder_gateway

        return qoder_gateway.responses(request)

    def messages(self, request, body):
        from admin.platform.qoder import gateway as qoder_gateway

        return qoder_gateway.messages(request)

    def count_tokens(self, request, body):
        from admin.platform.qoder import gateway as qoder_gateway

        return qoder_gateway.count_tokens(request)

    # ---- 目录 ----
    def list_models(self, request):
        from admin.platform.qoder import gateway as qoder_gateway

        return qoder_gateway.list_models(request)

    def balance(self, request):
        from admin.platform.qoder import gateway as qoder_gateway

        return qoder_gateway.balance(request)

    # ---- 契约 ----
    def account_ops(self) -> AccountOps:
        return QoderAccountOps(self.realm)

    def task_runners(self) -> dict[str, object]:
        # 签到 + 保活：Qoder 的 token 有有效期，定时续期能避免
        # 「账号状态正常但上游 401」这种看着像前端 bug 的情况。
        return {
            "qoder_checkin": qoder_checkin.run_daily_checkin,
            "qoder_keepalive": self.account_ops().keepalive,
        }

    def client_probe(self) -> ClientProbe:
        return QoderClientProbe(self.realm)

    # ---- 概览卡片 ----
    def overview_cards(self, db) -> list[dict]:
        rows = qoder_accounts.accounts_for(db, self.realm)
        total = len(rows)
        active = sum(1 for r in rows if (getattr(r, "status", "") or "") == "active")

        user_remain = 0.0
        user_total = 0.0
        addon_remain = 0.0
        plan = ""
        for account in rows:
            try:
                snap = json.loads(account.credits_snapshot or "{}")
            except ValueError:
                snap = {}
            if not isinstance(snap, dict):
                snap = {}
            uq = snap.get("user_quota") or {}
            aq = snap.get("addon_quota") or {}
            user_remain += float(uq.get("remaining") or 0)
            user_total += float(uq.get("total") or 0)
            addon_remain += float(aq.get("remaining") or 0)
            plan = plan or str(snap.get("plan") or "")

        def _num(value: float) -> str:
            return "{:,.0f}".format(value) if value >= 100 else "{:,.1f}".format(value)

        cards = [
            {"key": "accounts", "label": "账号总数", "value": total,
             "sub": "启用 %d" % active, "icon": "fa-users", "tone": ""},
            {"key": "credits", "label": "套餐额度",
             "value": "%s / %s" % (_num(user_remain), _num(user_total)),
             "sub": plan or "未同步",
             "icon": "fa-wallet", "tone": "brand"},
            {"key": "addon", "label": "资源包（含签到）",
             "value": _num(addon_remain),
             "sub": "签到积分落在这里",
             "icon": "fa-gift", "tone": "warn" if addon_remain <= 0 else "brand"},
            {"key": "mode", "label": "接入方式", "value": "COSY 签名",
             "sub": "模型格式 OpenAI，三协议互转",
             "icon": "fa-network-wired", "tone": ""},
        ]
        return cards


def _local_ledger(db, realm: str, days: int) -> dict:
    """本网关账本（直接查 usage_logs，不依赖路由函数签名）。"""
    from datetime import datetime, timedelta

    from sqlalchemy import func

    from admin.models import Account, UsageLog

    since = datetime.utcnow() - timedelta(days=max(1, int(days)))
    query = db.query(
        UsageLog.model.label("model"),
        func.count(UsageLog.id).label("requests"),
        func.coalesce(func.sum(UsageLog.prompt_tokens), 0).label("prompt"),
        func.coalesce(func.sum(UsageLog.completion_tokens), 0).label("completion"),
        func.coalesce(func.sum(UsageLog.total_tokens), 0).label("total"),
    ).filter(UsageLog.platform == "qoder", UsageLog.created_at >= since)

    if realm:
        ids = [row[0] for row in db.query(Account.id).filter(
            Account.platform == "qoder", Account.domain == realm).all()]
        if not ids:
            return {"days": days, "realm": realm, "items": [],
                    "totals": {"requests": 0, "total_tokens": 0}}
        query = query.filter(UsageLog.account_id.in_(ids))

    items = [{"model": r.model or "—", "requests": int(r.requests or 0),
              "prompt_tokens": int(r.prompt or 0),
              "completion_tokens": int(r.completion or 0),
              "total_tokens": int(r.total or 0)}
             for r in query.group_by(UsageLog.model).all()]
    items.sort(key=lambda i: i["total_tokens"], reverse=True)
    return {"days": days, "realm": realm, "items": items,
            "totals": {"requests": sum(i["requests"] for i in items),
                       "total_tokens": sum(i["total_tokens"] for i in items)}}


#: 无固定区域的实例（供 dispatch 按请求头判断）
gateway = QoderGateway("")
