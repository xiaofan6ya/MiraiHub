"""AStudio 管理契约实现（Gateway / AccountOps / ClientProbe）。

冷路径：账号池 11 项操作、定时任务（每日签到 / 额度刷新）、
客户端探测、概览卡片。转发热路径在 `gateway.py`。
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from admin.models import Account
from admin.platform.astudio.core import client, paths
from admin.platform.astudio.core.endpoints import PUBLIC
from admin.platform.astudio.services import accounts as astudio_accounts
from admin.platform.astudio.services import checkin as astudio_checkin

logger = logging.getLogger("admin.platform.astudio.platform_gateway")


def _run_checkin(db: Session, schedule=None) -> dict:
    """每日签到（全部 active 账号；判定逻辑见 services/checkin.py）。"""
    return astudio_checkin.run_checkin(db)


def _run_refresh_quota(db: Session, schedule=None) -> dict:
    from admin.platform.shared import run_account_batch

    rows = astudio_accounts.accounts_for(db)
    ids = [int(row.id or 0) for row in rows if row.status == "active"]
    if not ids:
        return {"ok": True, "refreshed": 0, "summary": "没有 active 账号"}

    def worker(session: Session, account) -> dict:
        result = astudio_accounts.sync_quota(session, account)
        return {"ok": bool(result.get("ok")),
                "remain": result.get("remain"),
                "error": result.get("error")}

    results = run_account_batch(ids, worker, label="astudio-refresh-quota")
    ok = sum(1 for r in results if r.get("ok"))
    return {"ok": ok == len(ids), "refreshed": len(ids),
            "summary": "刷新 %d 个账号的积分（%d 个无网页会话不可读）"
                       % (len(ids), len(ids) - ok)}


class AStudioAccountOps:
    """账号池 11 项操作（Gateway 契约 §AccountOps）。"""

    def list(self, db: Session) -> list[dict]:
        return [astudio_accounts.public_view(row)
                for row in astudio_accounts.accounts_for(db)]

    def scan(self) -> dict:
        return {"platform": PUBLIC, "items": paths.discover(),
                "data_root": paths.FALLBACK_DATA_ROOT}

    def import_local(self, db: Session) -> dict:
        return astudio_accounts.import_local(db)

    def import_record(self, db: Session, record: dict):
        return astudio_accounts.import_record(db, record)

    def refresh(self, db: Session, account_id: int, *, force: bool = False) -> dict:
        account = db.query(Account).filter(
            Account.id == int(account_id),
            Account.platform == PUBLIC).first()
        if account is None:
            return {"ok": False, "error": "账号不存在"}
        # 扫描来源的账号先重读本机登录态（客户端重登会换发新票），
        # 再刷额度。import 来源没有本机文件可读，直接刷。
        try:
            astudio_accounts.refresh_from_disk(db, account)
            result = astudio_accounts.sync_quota(db, account)
        except client.UpstreamError as exc:
            return {"ok": False, "error": exc.message, "kind": exc.kind}
        view = astudio_accounts.public_view(account)
        return {"ok": bool(result.get("ok")), "account": view,
                "remain": result.get("remain"),
                "total": result.get("total"),
                "note": result.get("error") or ""}

    def keepalive(self, db: Session) -> dict:
        # 模型凭据是 3 年 JWT，没有续签端点；网页会话过期只影响积分/签到，
        # 聊天照常。保活 = 重读本机登录态 + 复检，不做网络续期。
        rows = astudio_accounts.accounts_for(db)
        renewed = 0
        for row in rows:
            if row.status != "active":
                continue
            if astudio_accounts.refresh_from_disk(db, row):
                renewed += 1
        return {"ok": True, "checked": len(rows), "renewed": renewed,
                "summary": "重读本机登录态 %d 个账号%s" % (
                    len(rows),
                    "（更新 %d）" % renewed if renewed else "（无变化）")}

    def checkin(self, db: Session) -> dict:
        return _run_checkin(db)

    def credits(self, db: Session) -> dict:
        return astudio_accounts.summarize(db)

    def usage(self, db: Session, **filters) -> dict:
        from admin.models import UsageLog

        query = (db.query(UsageLog)
                 .filter(UsageLog.platform == PUBLIC))
        rows = query.order_by(UsageLog.id.desc()).limit(50).all()
        return {"platform": PUBLIC, "recent": [
            {"id": row.id, "model": row.model, "credits": row.credits,
             "created_at": row.created_at.isoformat() if row.created_at else None}
            for row in rows]}

    def health(self, db: Session, account_id: int) -> dict:
        """分段健康检查：模型凭据 → 网页会话 → 对话（限免模型）。"""
        account = db.query(Account).filter(
            Account.id == int(account_id),
            Account.platform == PUBLIC).first()
        if account is None:
            return {"ok": False, "error": "账号不存在"}
        steps: dict = {}
        try:
            data = astudio_accounts.credentials_of(account)
            token = astudio_accounts.token_of(data)
            steps["token"] = {"ok": bool(token)}
            if not token:
                return {"ok": False, "steps": steps,
                        "error": "账号凭证缺少模型凭据"}
            session = astudio_accounts.session_of(data)
            if session is not None:
                try:
                    balance = client.fetch_balance(
                        session, account_id=int(account.id or 0))
                    steps["account"] = {"ok": True,
                                        "balance": balance.get("totalBalance")}
                except client.UpstreamError as exc:
                    steps["account"] = {"ok": False, "kind": exc.kind,
                                        "message": exc.message}
            # 限免模型 spark-x2.6-4b：不发真请求的验收等于没验收
            result = client.request_responses(
                token,
                {"model": "spark-x2.6-4b", "max_output_tokens": 8,
                 "input": [{"role": "user",
                            "content": [{"type": "input_text", "text": "ping"}]}]},
                account_id=int(account.id or 0))
            steps["chat"] = {"ok": isinstance(result, dict)
                             and result.get("object") == "response"}
            return {"ok": all(s.get("ok") for s in steps.values()),
                    "steps": steps}
        except client.UpstreamError as exc:
            steps["error"] = {"ok": False, "kind": exc.kind,
                              "message": exc.message}
            return {"ok": False, "steps": steps}

    def inject(self, db: Session, account_id: int) -> dict:
        return {"supported": False,
                "message": "AStudio 客户端凭证由桌面端自行管理，注入未支持"}

    def export(self, db: Session, ids=None) -> dict:
        rows = astudio_accounts.accounts_for(db)
        if ids:
            want = {int(i) for i in ids}
            rows = [row for row in rows if int(row.id or 0) in want]
        items = []
        for row in rows:
            data = astudio_accounts.credentials_of(row)
            items.append({
                "platform": PUBLIC,
                "uid": astudio_accounts.account_uid(row),
                "name": row.name,
                "credential": data,
            })
        return {"format": "astudio-backup", "version": 1, "items": items}


class AStudioClientProbe:
    """客户端探测：本机登录态目录只读。"""

    def dirs(self) -> list[str]:
        return paths.session_files() + paths.config_files()

    def probe(self) -> dict:
        return {"platform": PUBLIC,
                "data_root": paths.FALLBACK_DATA_ROOT,
                "roots": paths.data_roots(),
                "session_files": len(paths.session_files()),
                "accounts": len(paths.discover())}

    def schema(self) -> list[dict]:
        return []

    def read(self, key: str):
        if key == "data_root":
            return paths.FALLBACK_DATA_ROOT
        return None

    def write(self, key: str, value) -> dict:
        return {"supported": False,
                "message": "AStudio 客户端参数为只读探测"}


class AStudioGateway:
    """Gateway 管理契约实现（转发方法委托 gateway.py）。"""

    platform_id = PUBLIC

    def __init__(self, realm: str = ""):
        self.realm = realm

    # ---- 转发（热路径委托） ----
    def chat_completions(self, request, body):
        from admin.platform.astudio import gateway
        return gateway.chat_completions(request)

    def responses(self, request, body):
        from admin.platform.astudio import gateway
        return gateway.responses(request)

    def messages(self, request, body):
        from admin.platform.astudio import gateway
        return gateway.messages(request)

    def count_tokens(self, request, body):
        from admin.platform.astudio import gateway
        return gateway.count_tokens(request)

    def list_models(self, request):
        from admin.platform.astudio import gateway
        return gateway.list_models(request)

    def balance(self, request):
        from admin.platform.astudio import gateway
        return gateway.balance(request)

    # ---- 管理契约 ----
    def account_ops(self) -> AStudioAccountOps:
        return AStudioAccountOps()

    def task_runners(self) -> dict:
        return {"astudio_checkin": _run_checkin,
                "astudio_refresh_quota": _run_refresh_quota}

    def client_probe(self) -> AStudioClientProbe:
        return AStudioClientProbe()

    def overview_cards(self, db: Session) -> list[dict]:
        summary = astudio_accounts.summarize(db)
        return [
            {"key": "accounts", "label": "账号总数",
             "value": summary["accounts"],
             "sub": "启用 %d" % summary["active"],
             "icon": "fa-users", "tone": ""},
            {"key": "credits", "label": "可用积分",
             "value": "{:,.0f}".format(summary["remain_credits"]),
             "sub": "双池合计 %s" % "{:,.0f}".format(summary["total_credits"]),
             "icon": "fa-wallet", "tone": "brand"},
        ]


gateway = AStudioGateway("")
