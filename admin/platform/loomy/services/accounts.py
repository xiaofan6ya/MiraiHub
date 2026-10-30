"""Loomy 凭证与账号池。

## 凭证形态

`<configRoot>/userData/auth-session.json`（明文）：

```json
{"session": "...", "userid": "...", "phone": "...", "updatedAt": 1793...}
```

存进 `Account.auth_json` 后归一成：

```json
{"realm": "cn", "token": "<session>", "uid": "<userid>",
 "phone": "...", "expires_at": 0}
```

`expires_at = 0` 表示「上游不告诉我们有效期」—— Loomy 确实没有。

## 额度怎么算

Loomy **没有额度接口**，计费信息在每轮对话的 `usage.points_consumed`。
所以：
  * `balance_total` 存**累计消耗**（本网关记账）
  * 没有「剩余额度」概念 —— 不要伪造一个数字

`public_view` 里明确标注 `quota_kind = "metered"`，
前端据此显示「已用 N points」而不是「剩余 N」。
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime

from sqlalchemy.orm import Session

from admin.models import Account
from admin.platform.loomy.core import client, endpoints

logger = logging.getLogger("admin.platform.loomy.accounts")
PLATFORM = endpoints.PLATFORM
QUOTA_TTL = 60


def credentials_of(account: Account) -> dict:
    try:
        data = json.loads(account.auth_json or "{}")
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def token_of(data: dict) -> str:
    return str(data.get("token") or data.get("session") or "")


def realm_of(account: Account) -> str:
    return str(getattr(account, "domain", "") or "cn")


def quota_is_fresh(account: Account) -> bool:
    synced = getattr(account, "credits_synced_at", None)
    if synced is None:
        return False
    return (datetime.utcnow() - synced).total_seconds() < QUOTA_TTL


def ensure_token(db: Session, account: Account) -> dict:
    """Loomy 没有续期机制 —— token 只能用现有的。

    这里只做「有没有」检查，不做「是否过期」猜测：
    过期与否由上游返回的 `100002` 决定，那时 raise 让上层提示重新登录。
    """
    data = credentials_of(account)
    if not token_of(data):
        raise client.UpstreamError(
            "账号缺少 session，请在客户端登录后重新扫描",
            status=401, kind="auth")
    return data


def accounts_for(db: Session, realm: str = "") -> list[Account]:
    query = db.query(Account).filter(Account.platform == PLATFORM)
    if realm:
        query = query.filter(Account.domain == realm)
    return query.order_by(Account.id.asc()).all()


def select(db: Session, realm: str = "", mark_picked: bool = False) -> Account | None:
    now = datetime.utcnow()
    for account in accounts_for(db, realm):
        if (getattr(account, "status", "") or "") != "active":
            continue
        cool = getattr(account, "cool_until", None)
        if cool and cool > now:
            continue
        if mark_picked:
            account.last_used_at = now
            db.commit()
        return account
    return None


def apply_quota(account: Account, quota: dict) -> None:
    """写回统一列。

    ## 额度口径（实测 2026-10-28）

        GET {base}/points/balance
        → {"balance": 5500, "dailyBalance": 4960, "availableBalance": 10460}

    `availableBalance` 才是「可用余额」（基础 + 当日额度）。
    第一版误以为 Loomy 没有额度接口，做了「计量制」——
    现在换成正常口径：`balance_remain = availableBalance`。

    `dailyBalance` 每日重置，所以它天然是「会过期的额度」，
    这里映射到 `credits_expiring_soon` 让「积分到期」面板也能看到。
    """
    available = int(quota.get("available") or 0)
    daily = int(quota.get("daily_balance") or 0)
    account.balance_remain = available
    account.balance_total = available
    account.credits_expiring_soon = daily
    account.credits_synced_at = datetime.utcnow()
    account.last_sync_at = datetime.utcnow()
    account.credits_snapshot = json.dumps({
        "unit": "points",
        "quota_kind": "balance",
        "balance": int(quota.get("balance") or 0),
        "daily_balance": daily,
        "available": available,
        "models": int(quota.get("models") or 0),
    }, ensure_ascii=False)[:20000]


def sync_quota(db: Session, account: Account) -> dict:
    """同步 = 探一次 /models（确认凭证还能用）。"""
    data = ensure_token(db, account)
    realm = realm_of(account)
    token = token_of(data)
    quota: dict = {}
    try:
        points = client.fetch_points_balance(token, realm, account.id)
        quota.update(points)
    except client.UpstreamError as exc:
        if exc.kind == "auth":
            account.status = "disabled"
            db.commit()
        return {"ok": False, "error": exc.to_dict()}
    try:
        models = client.fetch_models(token, realm, account.id)
        quota["models"] = len(models)
    except client.UpstreamError:
        quota.setdefault("models", 0)
    apply_quota(account, quota)
    db.commit()
    return {"ok": True, "quota": quota}


def public_view(account: Account, *, quota: dict | None = None) -> dict:
    """给前端的视图（**不含 token**）。"""
    data = credentials_of(account)
    return {
        "id": account.id,
        "name": account.name,
        "uid": account.uid,
        "realm": realm_of(account),
        "status": getattr(account, "status", "") or "",
        "balance_remain": int(getattr(account, "balance_remain", 0) or 0),
        "balance_total": int(getattr(account, "balance_total", 0) or 0),
        "daily_balance": int(getattr(account, "credits_expiring_soon", 0) or 0),
        "quota_kind": "balance",
        "quota_live": bool(quota),
        "models": int((quota or {}).get("models") or 0),
        "phone": str(data.get("phone") or ""),
        "has_refresh": False,
        "last_sync_at": account.last_sync_at.isoformat()
        if account.last_sync_at else None,
    }


def summarize(rows: list[dict]) -> dict:
    total = len(rows)
    active = sum(1 for r in rows if r.get("status") == "active")
    return {
        "total": total,
        "active": active,
        "disabled": total - active,
        "balance_remain": 0,
        "balance_total": sum(int(r.get("balance_total") or 0) for r in rows),
        "quota_kind": "metered",
    }


def import_from_local(db: Session) -> dict:
    """从本机 auth-session.json 导入（**用户什么都不用做**）。

    这是 Loomy 的主路径：凭证是明文 JSON，直接读。
    """
    from admin.platform.loomy.services import scan

    creds = scan.read_credentials()
    if not creds:
        return {"ok": False, "imported": 0,
                "reason": "no_plain_credential",
                "hint": "预期位置：%s" % scan.cred_path()}
    record = {
        "realm": "cn",
        "token": str(creds.get("session") or ""),
        "uid": str(creds.get("userid") or ""),
        "phone": str(creds.get("phone") or ""),
        "name": "Loomy %s" % (str(creds.get("phone") or "")[-4:] or "account"),
        "expires_at": 0,
    }
    result = import_record(db, record)
    return {"ok": True, "imported": 1, **result}


def import_record(db: Session, record: dict) -> dict:
    """导入一条凭证。按 uid 幂等。"""
    if not isinstance(record, dict):
        raise ValueError("凭证必须是对象")
    token = token_of(record)
    if not token:
        raise ValueError("凭证缺少 session/token")
    realm = str(record.get("realm") or "cn")
    uid = str(record.get("uid") or record.get("userid") or "")

    existing = None
    if uid:
        existing = db.query(Account).filter(Account.platform == PLATFORM,
                                            Account.uid == uid).first()
    if existing is None:
        existing = Account(platform=PLATFORM,
                           name=str(record.get("name") or uid or "Loomy"),
                           uid=uid, status="active")
        db.add(existing)

    existing.name = str(record.get("name") or existing.name or uid or "Loomy")
    existing.uid = uid or existing.uid
    existing.domain = realm
    existing.auth_json = json.dumps({
        "realm": realm,
        "token": token,
        "uid": uid,
        "phone": str(record.get("phone") or ""),
        "expires_at": int(record.get("expires_at") or 0),
    }, ensure_ascii=False)
    existing.status = "active"
    existing.last_sync_at = datetime.utcnow()
    db.commit()
    db.refresh(existing)

    # 立刻探一次，确认凭证可用
    probe = sync_quota(db, existing)
    return {"ok": bool(probe.get("ok")) or True,
            "account_id": existing.id, "uid": uid,
            "probe": probe}
