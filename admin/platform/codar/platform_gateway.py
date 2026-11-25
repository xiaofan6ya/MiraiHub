"""科搭 Codar 管理契约实现（Gateway / AccountOps / ClientProbe）。

冷路径都在这里：账号池 12 项操作、三个定时任务（签到 / 点数刷新 / 令牌
续期）、客户端探测、概览卡片、凭证有效期。转发热路径在 `gateway.py`。
"""
from __future__ import annotations

import logging
import time

from sqlalchemy.orm import Session

from admin.models import Account
from admin.platform.codar.core import client, endpoints, paths
from admin.platform.codar.services import accounts as codar_accounts
from admin.platform.codar.services import checkin as codar_checkin

logger = logging.getLogger("admin.platform.codar.platform_gateway")

#: 令牌续期的提前量：剩余不足 2 天就换新（令牌寿命约 7 天）
KEEPALIVE_HORIZON_S = 2 * 24 * 3600


# ---------------------------------------------------------------------------
# 定时任务
# ---------------------------------------------------------------------------
def _run_checkin(db: Session, schedule=None) -> dict:
    """每日签到（滚动 24 小时；上游幂等，已领过不会再领一次）。"""
    return codar_checkin._run_checkin(db, schedule)


def _run_refresh_quota(db: Session, schedule=None) -> dict:
    """刷新点数（两个池子一起读）。"""
    from admin.platform.shared import run_account_batch

    rows = codar_accounts.accounts_for(db)
    ids = [int(row.id or 0) for row in rows if row.status == "active"]
    if not ids:
        return {"ok": True, "refreshed": 0, "summary": "没有启用中的科搭账号"}

    def worker(session: Session, account) -> dict:
        try:
            result = codar_accounts.sync_balances(session, account)
            return {"ok": True, "account_id": account.id,
                    "remain": result.get("remain")}
        except client.UpstreamError as exc:
            return {"ok": False, "account_id": account.id,
                    "error": exc.message}

    results = run_account_batch(ids, worker, label="codar-refresh-quota")
    failed = sum(1 for item in results if not item.get("ok"))
    return {"ok": failed == 0, "refreshed": len(ids) - failed,
            "failed": failed,
            "summary": "刷新 %d 个账号的点数%s"
                       % (len(ids) - failed,
                          " · 失败 %d" % failed if failed else "")}


def _run_keepalive(db: Session, schedule=None) -> dict:
    """令牌续期：剩余不足 2 天的号提前换新。

    科搭的令牌是 JWT（约 7 天），续期用的是同一枚令牌（`/v1/token/refresh`）。
    提前换的价值是「不要让在线请求撞上刚好过期」—— 过期后的续期请求一律
    401，那时候只能让用户打开客户端重新登录。
    """
    from admin.platform.shared import run_account_batch

    rows = codar_accounts.accounts_for(db)
    horizon = time.time() + KEEPALIVE_HORIZON_S
    ids = []
    for row in rows:
        if row.status != "active":
            continue
        expires = codar_accounts.expires_of(row)
        if not expires or expires <= horizon:
            ids.append(int(row.id or 0))
    if not ids:
        return {"ok": True, "checked": len(rows), "renewed": 0,
                "summary": "没有临期令牌（>2 天）"}

    renewed = {"n": 0}

    def worker(session: Session, account) -> dict:
        before = codar_accounts.expires_of(account)
        try:
            data = codar_accounts.refresh_and_store(session, account)
        except client.UpstreamError as exc:
            return {"ok": False, "account_id": account.id,
                    "error": exc.message}
        after = int(data.get("expires_at") or 0)
        if after > before:
            renewed["n"] += 1
        return {"ok": True, "account_id": account.id}

    results = run_account_batch(ids, worker, label="codar-keepalive")
    failed = sum(1 for item in results if not item.get("ok"))
    return {"ok": failed == 0, "checked": len(rows), "renewed": renewed["n"],
            "failed": failed,
            "summary": "续期 %d 个令牌%s"
                       % (renewed["n"],
                          " · 失败 %d" % failed if failed else "")}


# ---------------------------------------------------------------------------
# 账号池
# ---------------------------------------------------------------------------
class CodarAccountOps:
    """账号池 11 项操作（`admin.platform.gateway.AccountOps`）。"""

    def list(self, db: Session) -> list[dict]:
        return [codar_accounts.public_view(row)
                for row in codar_accounts.accounts_for(db)]

    def scan(self) -> dict:
        items = paths.discover()
        return {"platform": endpoints.PUBLIC, "items": items,
                "auth_home": paths.config_home(),
                "config": paths.config_path()}

    def import_local(self, db: Session) -> dict:
        return codar_accounts.import_local(db)

    def import_record(self, db: Session, record: dict):
        return codar_accounts.import_record(db, record)

    def refresh(self, db: Session, account_id: int, *, force: bool = False) -> dict:
        account = _account(db, account_id)
        if account is None:
            return {"ok": False, "error": "账号不存在"}
        try:
            result = codar_accounts.sync_balances(db, account)
        except client.UpstreamError as exc:
            return {"ok": False, "error": exc.message, "kind": exc.kind}
        return {"ok": True, "account": codar_accounts.public_view(account),
                "remain": result.get("remain"), "total": result.get("total"),
                "general": result.get("general"), "agent": result.get("agent")}

    def keepalive(self, db: Session) -> dict:
        return _run_keepalive(db)

    def checkin(self, db: Session) -> dict:
        return _run_checkin(db)

    def credits(self, db: Session) -> dict:
        return codar_accounts.summarize(db)

    def usage(self, db: Session, **filters) -> dict:
        from admin.models import UsageLog

        limit = int(filters.get("limit") or 50)
        rows = (db.query(UsageLog).filter(UsageLog.platform == endpoints.PUBLIC)
                .order_by(UsageLog.id.desc()).limit(max(1, min(limit, 200))).all())
        return {"platform": endpoints.PUBLIC, "recent": [
            {"id": row.id, "model": row.model, "credits": row.credits,
             "prompt_tokens": row.prompt_tokens,
             "completion_tokens": row.completion_tokens,
             "created_at": row.created_at.isoformat() if row.created_at else None}
            for row in rows]}

    def health(self, db: Session, account_id: int) -> dict:
        """分段检查：令牌 → 账号名 → 点数 → 对话。

        对话那一步故意用**最便宜的档**（1 token 的回复上限），健康检查不该
        替用户花掉一次完整请求的额度。
        """
        account = _account(db, account_id)
        if account is None:
            return {"ok": False, "error": "账号不存在"}
        steps: dict = {}
        token = ""
        try:
            token = codar_accounts.token_for_call(db, account)
            steps["token"] = {"ok": bool(token),
                              "expires_at": codar_accounts.expires_of(account)}
        except client.UpstreamError as exc:
            steps["token"] = {"ok": False, "error": exc.message,
                              "kind": exc.kind}
        name = codar_accounts.username_of_account(account)
        steps["account"] = {"ok": bool(name), "username": name}
        if token and name:
            try:
                points = codar_accounts.read_points(
                    token, name, account_id=int(account.id or 0))
                steps["points"] = {"ok": True, "general": points.get("general"),
                                   "agent": points.get("agent")}
            except client.UpstreamError as exc:
                steps["points"] = {"ok": False, "error": exc.message,
                                   "kind": exc.kind}
            model = _probe_model(db)
            if model:
                protocol = client.protocol_of(model)
                try:
                    # 两套协议的探活体形状一样（都只要 model + messages），
                    # 差别全在鉴权头，由 client 按 protocol 选
                    payload = {"model": model, "max_tokens": 8,
                               "messages": [{"role": "user",
                                             "content": "ping"}]}
                    result = client.request_json(
                        "POST", endpoints.gateway_url(
                            endpoints.MESSAGES_PATH if protocol == "anthropic"
                            else endpoints.CHAT_PATH),
                        token=token, protocol=protocol, json_body=payload,
                        account_id=int(account.id or 0), context="探测",
                        timeout=client.SHORT_TIMEOUT)
                    steps["chat"] = {"ok": isinstance(result, dict),
                                     "model": model, "protocol": protocol}
                except client.UpstreamError as exc:
                    steps["chat"] = {"ok": False, "error": exc.message,
                                     "kind": exc.kind, "model": model,
                                     "protocol": protocol}
            else:
                steps["chat"] = {"ok": False, "error": "账号池没有可用模型"}
        return {"ok": all(step.get("ok") for step in steps.values()),
                "steps": steps}

    def inject(self, db: Session, account_id: int) -> dict:
        return {"supported": False,
                "message": "科搭的登录态由客户端自己维护，本网关只读不写回"}

    def export(self, db: Session, ids=None) -> dict:
        rows = codar_accounts.accounts_for(db)
        if ids:
            want = {int(i) for i in ids}
            rows = [row for row in rows if int(row.id or 0) in want]
        items = []
        for row in rows:
            data = codar_accounts.credentials_of(row)
            items.append({
                "platform": endpoints.PUBLIC,
                "uid": row.uid,
                "name": row.name,
                "realm": "cn",
                "credential": {
                    "token": data.get("token"),
                    "username": data.get("username"),
                    "expires_at": data.get("expires_at"),
                    "source": data.get("source"),
                },
            })
        return {"format": "codar-backup", "version": 1, "items": items}


def _account(db: Session, account_id: int):
    return (db.query(Account)
            .filter(Account.id == int(account_id),
                    Account.platform == endpoints.PLATFORM).first())


def _probe_model(db: Session) -> str:
    """探活用哪个模型：目录里第一个 chat 模型（探测不该挑贵的）。"""
    info = codar_accounts.catalog(db)
    for row in info.get("rows") or []:
        if row.get("protocol") == endpoints.PROTOCOL_CHAT:
            return str(row.get("id") or "")
    rows = info.get("rows") or []
    return str(rows[0].get("id") or "") if rows else ""


# ---------------------------------------------------------------------------
# 客户端探测
# ---------------------------------------------------------------------------
class CodarClientProbe:
    """客户端探测：配置文件只读（令牌字段不对外展示）。"""

    def dirs(self) -> list[str]:
        home = paths.config_home()
        import os

        return [home] if os.path.isdir(home) else []

    def probe(self) -> dict:
        path = paths.config_path()
        import os

        found = paths.app_token(path)
        return {"platform": endpoints.PUBLIC,
                "config": path,
                "exists": os.path.isfile(path),
                "has_token": bool(found.get("token")),
                "username": found.get("username") or "",
                "expires_at": int(found.get("expires_at") or 0),
                "error": found.get("error") or ""}

    def schema(self) -> list[dict]:
        # 客户端配置是客户端自己的：本网关不提供写入项
        return []

    def read(self, key: str):
        if key in ("config", "config_path"):
            return paths.config_path()
        if key in ("home", "config_home"):
            return paths.config_home()
        return None

    def write(self, key: str, value) -> dict:
        return {"supported": False,
                "message": "科搭客户端配置为只读探测（写入会影响客户端登录态）"}


# ---------------------------------------------------------------------------
# Gateway
# ---------------------------------------------------------------------------
class CodarGateway:
    """`admin.platform.gateway.Gateway` 实现（转发委托 `gateway.py`）。"""

    platform_id = endpoints.PUBLIC

    def __init__(self, realm: str = ""):
        self.realm = realm or "cn"

    # ---- 转发（热路径委托） ----
    def chat_completions(self, request, body):
        from admin.platform.codar import gateway as hot

        return hot.chat_completions(request)

    def responses(self, request, body):
        from admin.platform.codar import gateway as hot

        return hot.responses(request)

    def messages(self, request, body):
        from admin.platform.codar import gateway as hot

        return hot.messages(request)

    def count_tokens(self, request, body):
        from admin.platform.codar import gateway as hot

        return hot.count_tokens(request)

    # ---- 目录 ----
    def list_models(self, request):
        from admin.platform.codar import gateway as hot

        return hot.list_models(request)

    def balance(self, request):
        from admin.platform.codar import gateway as hot

        return hot.balance(request)

    # ---- 管理契约 ----
    def account_ops(self) -> CodarAccountOps:
        return CodarAccountOps()

    def task_runners(self) -> dict:
        return {"codar_checkin": _run_checkin,
                "codar_refresh_quota": _run_refresh_quota,
                "codar_keepalive": _run_keepalive}

    def client_probe(self) -> CodarClientProbe:
        return CodarClientProbe()

    # ---- 凭证有效期 ----
    def credential_expiry(self, auth_json) -> dict:
        """令牌到期时间（epoch 秒）。续期窗口与令牌同寿命：过期前都能换。"""
        from admin.platform import shared

        data = shared.parse_auth_json(auth_json)
        token = str((data or {}).get("token") or "")
        expires = shared.seconds_from(
            (data or {}).get("expires_at")) or float(paths.expires_at(token) or 0)
        renewable = bool(expires)
        return shared.credential_window(
            expires_at=expires or None,
            # 没有独立的 refresh token：续期靠同一枚令牌，所以续期窗口
            # 就是这枚令牌自己的寿命
            refresh_expires_at=expires or None,
            has_refresh=renewable)

    def renewal_guard_days(self) -> int:
        return 2

    def overview_cards(self, db: Session) -> list[dict]:
        summary = codar_accounts.summarize(db)
        catalog = client.cached_catalog()
        cards = [
            {"key": "accounts", "label": "账号总数",
             "value": summary["accounts"],
             "sub": "启用 %d" % summary["active"],
             "icon": "fa-users", "tone": ""},
            {"key": "points", "label": "可用点数",
             "value": "{:,.0f}".format(summary["remain_points"]),
             "sub": "agent %s" % "{:,.0f}".format(summary["agent_points"]),
             "icon": "fa-wallet", "tone": "brand"},
            {"key": "models", "label": "可用模型",
             "value": len(catalog["rows"]),
             "sub": "内置目录" if catalog["fallback"] else "上游目录",
             "icon": "fa-cubes",
             "tone": "warn" if catalog["fallback"] else ""},
        ]
        return cards


gateway = CodarGateway("")
