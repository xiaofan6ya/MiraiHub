"""MiniMax Code 管理契约实现（Gateway / AccountOps / ClientProbe）。

冷路径：账号池 11 项操作、定时任务（签到 / 续签保活 / 额度刷新）、
客户端探测、概览卡片。转发热路径在 `gateway.py`。
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from admin.models import Account
from admin.platform.minimax.core import client, paths
from admin.platform.minimax.core.endpoints import (
    LLM_MESSAGES_PATH, PUBLIC, agent_base)
from admin.platform.minimax.services import accounts as minimax_accounts

logger = logging.getLogger("admin.platform.minimax.platform_gateway")


def _run_checkin(db: Session, schedule=None) -> dict:
    """每日签到（全部 active 账号；已签过的上游返回 claim_result 幂等）。"""
    from admin.db import SessionLocal
    from admin.platform.shared import run_account_batch

    rows = minimax_accounts.accounts_for(db)
    ids = [int(row.id or 0) for row in rows if row.status == "active"]
    if not ids:
        return {"ok": True, "checked": 0, "claimed": 0,
                "summary": "没有 active 的 MiniMax 账号"}

    summary = {"claimed": 0, "already": 0, "failed": 0}

    def worker(session: Session, account) -> dict:
        data = minimax_accounts.ensure_token(session, account)
        token = minimax_accounts.token_of(data)
        realm = minimax_accounts.realm_of(account)
        status = client.signin_status(token, realm,
                                      account_id=int(account.id or 0))
        days = status.get("days") if isinstance(status.get("days"), list) else []
        today = next((d for d in days
                      if isinstance(d, dict) and d.get("is_today")), None)
        if today is None:
            summary["already"] += 1
            return {"ok": True, "skipped": "今日不在签到周期"}
        if int(today.get("status") or 0) != 3:
            # 3=今日可签；4/其它=已签或不可签
            summary["already"] += 1
            return {"ok": True, "skipped": "今日已签或不可签"}
        result = client.signin_claim(token, realm,
                                     account_id=int(account.id or 0))
        points = result.get("points")
        summary["claimed"] += 1
        logger.info("MiniMax 账号 %s 签到成功 +%s 积分", account.id, points)
        return {"ok": True, "points": points}

    run_account_batch(ids, worker, label="minimax-checkin")
    return {"ok": summary["failed"] == 0, "checked": len(ids),
            "claimed": summary["claimed"], "already": summary["already"],
            "summary": "签到 %d · 已签/跳过 %d" % (summary["claimed"],
                                                  summary["already"])}


def _run_keepalive(db: Session, schedule=None) -> dict:
    """续签保活：access 票临期（< 2 小时）的账号逐个 refresh_token 换新。

    ## 窗口为什么是 2 小时而不是「7 天」

    MiniMax 的 access 票只有 ~11 小时寿命，refresh 是**轮换票**（旧票
    连同 refresh 一起立即吊销）。按 7 天窗口 = 每次 keepalive 都续签 =
    每 6 小时无谓轮换一次，还会与官方客户端互相踢掉对方的票（客户端
    持有的 refresh 失效就掉登录）。收窄到 2 小时：空闲期提前换好新票、
    避免在线请求撞上续签延迟，又不制造多余轮换。续签后新票会**回写
    官方客户端 auth.json**（paths.write_back_credential）。
    """
    import time as _time

    from admin.platform.shared import run_account_batch

    rows = minimax_accounts.accounts_for(db)
    horizon = _time.time() * 1000 + 2 * 3600 * 1000
    ids = []
    for row in rows:
        if row.status != "active":
            continue
        data = minimax_accounts.credentials_of(row)
        expires = int(data.get("expires_at_ms") or 0)
        if not expires or expires < horizon:
            ids.append(int(row.id or 0))
    if not ids:
        return {"ok": True, "checked": len(rows), "renewed": 0,
                "summary": "没有临期凭证（<2 小时）"}

    renewed = {"n": 0}

    def worker(session: Session, account) -> dict:
        data = minimax_accounts.credentials_of(account)
        refresh = minimax_accounts.refresh_token_of(data)
        if not refresh:
            return {"ok": False, "error": "缺 refresh_token"}
        grant = client.refresh_credentials(
            refresh, minimax_accounts.realm_of(account))
        data = dict(data)
        data["access_token"] = grant["access_token"]
        if grant.get("refresh_token"):
            data["refresh_token"] = grant["refresh_token"]
        data["expires_at_ms"] = grant.get("expires_at_ms") or 0
        account.auth_json = json_dumps(data)
        account.last_sync_at = utcnow()
        session.commit()
        try:
            paths.write_back_credential(
                minimax_accounts.realm_of(account),
                access_token=grant["access_token"],
                refresh_token=data["refresh_token"],
                expires_at_ms=int(data["expires_at_ms"] or 0))
        except Exception:  # noqa: BLE001 - 回写失败不影响续签
            pass
        renewed["n"] += 1
        return {"ok": True}

    run_account_batch(ids, worker, label="minimax-keepalive")
    return {"ok": True, "checked": len(rows), "renewed": renewed["n"],
            "summary": "续签 %d 个账号（已回写客户端）" % renewed["n"]}


def _run_refresh_quota(db: Session, schedule=None) -> dict:
    from admin.platform.shared import run_account_batch

    rows = minimax_accounts.accounts_for(db)
    ids = [int(row.id or 0) for row in rows if row.status == "active"]
    if not ids:
        return {"ok": True, "refreshed": 0, "summary": "没有 active 账号"}

    def worker(session: Session, account) -> dict:
        result = minimax_accounts.sync_quota(session, account)
        return {"ok": bool(result.get("ok")),
                "remain": result.get("remain")}

    run_account_batch(ids, worker, label="minimax-refresh-quota")
    return {"ok": True, "refreshed": len(ids),
            "summary": "刷新 %d 个账号的积分" % len(ids)}


def json_dumps(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)


def utcnow():
    from datetime import datetime
    return datetime.utcnow()


class MinimaxAccountOps:
    """账号池 11 项操作（Gateway 契约 §AccountOps）。"""

    def list(self, db: Session) -> list[dict]:
        return [minimax_accounts.public_view(row)
                for row in minimax_accounts.accounts_for(db)]

    def scan(self) -> dict:
        return {"platform": PUBLIC, "items": paths.discover(),
                "auth_home": paths.auth_home()}

    def import_local(self, db: Session) -> dict:
        return minimax_accounts.import_local(db)

    def import_record(self, db: Session, record: dict):
        return minimax_accounts.import_record(db, record)

    def refresh(self, db: Session, account_id: int, *, force: bool = False) -> dict:
        account = db.query(Account).filter(
            Account.id == int(account_id),
            Account.platform == PUBLIC).first()
        if account is None:
            return {"ok": False, "error": "账号不存在"}
        result = minimax_accounts.sync_quota(db, account)
        view = minimax_accounts.public_view(account)
        return {"ok": bool(result.get("ok")), "account": view,
                "remain": result.get("remain")}

    def keepalive(self, db: Session) -> dict:
        return _run_keepalive(db)

    def checkin(self, db: Session) -> dict:
        return _run_checkin(db)

    def credits(self, db: Session) -> dict:
        return minimax_accounts.summarize(db)

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
        """分段健康检查：凭证 → 用户信息 → 对话。"""
        account = db.query(Account).filter(
            Account.id == int(account_id),
            Account.platform == PUBLIC).first()
        if account is None:
            return {"ok": False, "error": "账号不存在"}
        steps: dict = {}
        try:
            data = minimax_accounts.ensure_token(db, account)
            token = minimax_accounts.token_of(data)
            steps["token"] = {"ok": bool(token)}
            realm = minimax_accounts.realm_of(account)
            profile = client.fetch_profile(token, realm,
                                           account_id=int(account.id or 0))
            steps["profile"] = {"ok": bool(profile.get("user_id")),
                                "name": profile.get("user_name") or ""}
            result = client.request_json(
                "POST", agent_base(realm) + LLM_MESSAGES_PATH,
                token=token, realm=realm,
                json_body={"model": "MiniMax-M2.7", "max_tokens": 8,
                           "messages": [{"role": "user", "content": "ping"}]},
                account_id=int(account.id or 0))
            steps["chat"] = {"ok": isinstance(result, dict)
                             and result.get("type") == "message"}
            return {"ok": all(s.get("ok") for s in steps.values()),
                    "steps": steps}
        except client.UpstreamError as exc:
            steps["error"] = {"ok": False, "kind": exc.kind,
                              "message": exc.message}
            return {"ok": False, "steps": steps}

    def inject(self, db: Session, account_id: int) -> dict:
        return {"supported": False,
                "message": "MiniMax 客户端凭证由官方 OAuth 文件管理，注入未支持"}

    def export(self, db: Session, ids=None) -> dict:
        rows = minimax_accounts.accounts_for(db)
        if ids:
            want = {int(i) for i in ids}
            rows = [row for row in rows if int(row.id or 0) in want]
        items = []
        for row in rows:
            data = minimax_accounts.credentials_of(row)
            items.append({
                "platform": PUBLIC,
                "uid": row.uid,
                "name": row.name,
                "realm": minimax_accounts.realm_of(row),
                "credential": data,
            })
        return {"format": "minimax-backup", "version": 1, "items": items}


class MinimaxClientProbe:
    """客户端探测：本机凭证目录只读。"""

    def dirs(self) -> list[str]:
        return [entry["path"] for entry in paths.credential_files()]

    def probe(self) -> dict:
        files = paths.credential_files()
        return {"platform": PUBLIC,
                "auth_home": paths.auth_home(),
                "files": len(files),
                "accounts": len(paths.discover())}

    def schema(self) -> list[dict]:
        return []

    def read(self, key: str):
        if key == "auth_home":
            return paths.auth_home()
        return None

    def write(self, key: str, value) -> dict:
        return {"supported": False,
                "message": "MiniMax 客户端参数为只读探测"}


class MinimaxGateway:
    """Gateway 管理契约实现（转发方法委托 gateway.py）。"""

    platform_id = PUBLIC

    def __init__(self, realm: str = ""):
        self.realm = realm

    # ---- 转发（热路径委托） ----
    def chat_completions(self, request, body):
        from admin.platform.minimax import gateway
        return gateway._forward_openai_via_anthropic(
            gateway.bridge.openai_request_to_anthropic(body), request)

    def responses(self, request, body):
        from admin.platform.minimax import gateway
        return gateway.responses(request)

    def messages(self, request, body):
        from admin.platform.minimax import gateway
        return gateway._forward_anthropic(body, request)

    def count_tokens(self, request, body):
        from admin.platform.minimax import gateway
        return gateway.count_tokens(request)

    def list_models(self, request):
        from admin.platform.minimax import gateway
        return gateway.list_models(request)

    def balance(self, request):
        from admin.platform.minimax import gateway
        return gateway.balance(request)

    # ---- 管理契约 ----
    def account_ops(self) -> MinimaxAccountOps:
        return MinimaxAccountOps()

    def task_runners(self) -> dict:
        return {"minimax_checkin": _run_checkin,
                "minimax_keepalive": _run_keepalive,
                "minimax_refresh_quota": _run_refresh_quota}

    def client_probe(self) -> MinimaxClientProbe:
        return MinimaxClientProbe()

    def overview_cards(self, db: Session) -> list[dict]:
        summary = minimax_accounts.summarize(db)
        return [
            {"key": "accounts", "label": "账号总数",
             "value": summary["accounts"],
             "sub": "启用 %d" % summary["active"],
             "icon": "fa-users", "tone": ""},
            {"key": "credits", "label": "可用积分",
             "value": "{:,.2f}".format(summary["remain_credits"]),
             "sub": "累计 %s" % "{:,.2f}".format(summary["total_credits"]),
             "icon": "fa-wallet", "tone": "brand"},
        ]


gateway = MinimaxGateway("")
