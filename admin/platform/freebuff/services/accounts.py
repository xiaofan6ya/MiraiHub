"""Freebuff 凭证与账号池。

## 凭证形态

只有一种：**Developer API key**（`fb_...`）。落库归一：

```json
{"token":"fb_...","label":"personal","key_tail":"a1b2","verified_at":0,
 "verify_state":"unverified"}
```

`Account.uid` 用 `sha256(key)[:8]`（同 key 幂等，不同 key 不覆盖）；
`Account.name` 用 label（用户给的「哪把 key」），没给就 `freebuff-<尾 4 位>`。

## 为什么「探活」不能真的验证 key

实测上游只有两个端点：

    GET  /models              200，**不需要鉴权**
    POST /chat/completions    需要 key，401 invalid_api_key

`/usage`、`/credits`、`/me`、`/balance`、`/health` 全是 404。所以：

* 拉目录成功**不能**证明 key 有效（无鉴权也能拉）；
* 唯一能判定 key 的方式是发一次真实对话 —— 那要花钱，不能当探活。

于是这里的探活做三件事，并**如实**把状态写回：
  1. key 形状检查（UUID = 会话 token，本地直接拒，不发上游）；
  2. 目录可达性（网络/上游是否活着，模型数）；
  3. `verify_state` 保持 `unverified`，直到真实对话成功/401 —— 那由
     `gateway.py` 在真实请求里回写（成功 → `ok`，401 → `rejected`）。
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from admin.models import Account
from admin.platform.freebuff.core import client, endpoints, protocol

logger = logging.getLogger("admin.platform.freebuff.accounts")
PLATFORM = endpoints.PLATFORM
QUOTA_TTL = 60

#: 连续多少次真实对话 401 才判定账号失效（避免抖动误杀）。
PROBE_DISABLE_AFTER = 2


def credentials_of(account: Account) -> dict:
    try:
        data = json.loads(account.auth_json or "{}")
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def key_of(account: Account) -> str:
    data = credentials_of(account)
    for name in ("token", "key", "api_key", "apiKey"):
        value = data.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def label_of(account: Account) -> str:
    return str(credentials_of(account).get("label") or "")


def key_tail(key: str) -> str:
    return str(key or "")[-4:]


def accounts_for(db: Session, realm: str = "") -> list[Account]:
    query = db.query(Account).filter(Account.platform == PLATFORM)
    if realm:
        query = query.filter(Account.domain == realm)
    return query.order_by(Account.id.asc()).all()


def quota_is_fresh(account: Account) -> bool:
    synced = getattr(account, "credits_synced_at", None)
    if synced is None:
        return False
    return (datetime.utcnow() - synced).total_seconds() < QUOTA_TTL


def apply_probe(account: Account, *, state: str, detail: str = "",
                models: int = 0) -> None:
    """把探活结论写回 DB（`verify_state` 在 `auth_json` 里）。"""
    data = credentials_of(account)
    data["verify_state"] = state
    data["verified_at"] = int(datetime.utcnow().timestamp())
    data["models"] = int(models or 0)
    account.auth_json = json.dumps(data, ensure_ascii=False)[:20000]
    account.credits_snapshot = json.dumps({
        "unit": "credits",
        "quota_kind": "unknown",
        "verify_state": state,
        "models": int(models or 0),
        "detail": detail[:200],
    }, ensure_ascii=False)[:20000]
    account.credits_synced_at = datetime.utcnow()
    account.last_sync_at = datetime.utcnow()
    if detail:
        account.last_err_msg = detail[:250]


def probe(db: Session, account: Account) -> dict:
    """探活：形状检查 + 目录可达。**不花额度、不假装验证通过**。"""
    key = key_of(account)
    if not key:
        return {"ok": False, "error": {"kind": "auth", "status": 401,
                                       "message": "账号缺少 API key，请重新导入"}}
    if endpoints.is_session_token(key):
        apply_probe(account, state="session_token",
                    detail="这是 Freebuff 会话 token，不是 Developer API key")
        account.status = "disabled"
        db.commit()
        return {"ok": False, "error": {
            "kind": "auth", "status": 401,
            "message": endpoints.SESSION_TOKEN_MESSAGE}}
    try:
        rows = client.fetch_catalog(force=True)
    except client.UpstreamError as exc:
        apply_probe(account, state="catalog_error", detail=exc.message)
        try:
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
        return {"ok": False, "error": exc.to_dict()}
    models = len(rows)
    apply_probe(account, state="unverified",
                detail="目录可达；key 有效性需首次真实对话确认", models=models)
    if int(getattr(account, "consecutive_fails", 0) or 0):
        account.consecutive_fails = 0
    if (account.status or "") == "disabled":
        account.status = "active"
    try:
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
    return {"ok": True, "models": models, "verify_state": "unverified",
            "note": "上游没有校验 key 的轻量端点（/models 免鉴权），"
                    "key 是否有效只能在真实对话时确认"}


def mark_key_ok(db: Session, account: Account) -> None:
    """真实对话成功后回写（由 gateway 调用）。"""
    data = credentials_of(account)
    if data.get("verify_state") == "ok":
        return
    data["verify_state"] = "ok"
    data["verified_at"] = int(datetime.utcnow().timestamp())
    account.auth_json = json.dumps(data, ensure_ascii=False)[:20000]
    account.consecutive_fails = 0
    try:
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()


def mark_key_rejected(db: Session, account: Account, detail: str = "") -> None:
    """真实对话 401 后回写（由 gateway 调用）。"""
    data = credentials_of(account)
    data["verify_state"] = "rejected"
    data["verified_at"] = int(datetime.utcnow().timestamp())
    account.auth_json = json.dumps(data, ensure_ascii=False)[:20000]
    fails = int(getattr(account, "consecutive_fails", 0) or 0) + 1
    account.consecutive_fails = fails
    account.err_count = int(getattr(account, "err_count", 0) or 0) + 1
    account.last_err_msg = (detail or "API key 被上游拒绝")[:250]
    account.last_err_at = datetime.utcnow()
    if fails >= PROBE_DISABLE_AFTER:
        account.status = "disabled"
        logger.warning("Freebuff 账号 %s 连续 %d 次 401，已禁用",
                       account.id, fails)
    try:
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()


def health(db: Session, account: Account) -> dict:
    """分段健康检查：凭证形状 / 目录 / 对话（只能靠真实请求）。"""
    key = key_of(account)
    stages: dict = {}
    if not key:
        return {"ok": False, "stages": {"key": {"ok": False,
                                               "detail": "没有 API key"}}}
    if endpoints.is_session_token(key):
        return {"ok": False, "stages": {"key": {
            "ok": False,
            "detail": "这是会话 token（UUID），不是 Developer API key；"
                      "请到 https://freebuff.com/account/api 创建"}}}
    stages["key"] = {"ok": True, "detail": "…%s（形状合法）" % key_tail(key)}
    try:
        rows = client.fetch_catalog(force=True)
        stages["catalog"] = {"ok": True, "detail": "目录 %d 个模型" % len(rows)}
    except client.UpstreamError as exc:
        stages["catalog"] = {"ok": False, "detail": exc.message}
    state = str(credentials_of(account).get("verify_state") or "unverified")
    stages["chat"] = {
        "ok": state == "ok",
        "detail": ("最近一次真实对话成功" if state == "ok"
                   else "被上游拒绝（401）" if state == "rejected"
                   else "未验证：上游没有校验 key 的端点，只能靠真实对话确认"),
    }
    return {"ok": all(item.get("ok") for key_name, item in stages.items()
                      if key_name != "chat"), "stages": stages}


def public_view(account: Account) -> dict:
    data = credentials_of(account)
    return {
        "id": account.id,
        "name": account.name,
        "uid": account.uid,
        "realm": str(getattr(account, "domain", "") or ""),
        "status": getattr(account, "status", "") or "",
        "label": str(data.get("label") or ""),
        "key_tail": str(data.get("key_tail") or ""),
        "verify_state": str(data.get("verify_state") or "unverified"),
        "models": int(data.get("models") or 0),
        "quota_kind": "unknown",
        "balance_remain": int(getattr(account, "balance_remain", 0) or 0),
        "last_sync_at": account.last_sync_at.isoformat()
        if account.last_sync_at else None,
        "last_err_msg": (getattr(account, "last_err_msg", "") or "")[:200],
    }


def summarize(rows: list[dict]) -> dict:
    total = len(rows)
    active = sum(1 for row in rows if row.get("status") == "active")
    return {
        "total": total,
        "active": active,
        "disabled": total - active,
        "verified": sum(1 for row in rows if row.get("verify_state") == "ok"),
        "rejected": sum(1 for row in rows
                        if row.get("verify_state") == "rejected"),
        "quota_kind": "unknown",
    }


def import_record(db: Session, record, *, probe_upstream: bool = True) -> dict:
    """导入一条凭证。

    `record` 兼容：
      * `{"key": "fb_..."}` / `{"api_key": "fb_..."}` / `{"token": "..."}`
      * 裸字符串（key，或 `Bearer fb_...`）
      * `{"credential": {...}}`（批量导入时常见）

    会话 token（UUID）**本地拒绝**：不落库，直接把可操作提示返回。
    """
    raw = record
    label = ""
    if isinstance(record, dict):
        raw = (record.get("credential") or record.get("key")
               or record.get("api_key") or record.get("apiKey")
               or record.get("token") or "")
        label = str(record.get("label") or record.get("email") or "").strip()
    if not isinstance(raw, str):
        raise ValueError("凭证里没有 API key")
    key = endpoints.normalize_key(raw)
    if not key:
        raise ValueError("凭证里没有 API key（需要 fb_ 开头的 Developer API key）")
    if endpoints.is_session_token(key):
        raise ValueError(endpoints.SESSION_TOKEN_MESSAGE)

    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]
    existing = db.query(Account).filter(Account.platform == PLATFORM,
                                        Account.uid == digest).first()
    if existing is None:
        existing = Account(platform=PLATFORM, uid=digest,
                           name=label or ("freebuff-%s" % key_tail(key)),
                           status="active")
        db.add(existing)
    existing.name = label or existing.name or ("freebuff-%s" % key_tail(key))
    existing.domain = "cn"
    existing.auth_json = json.dumps({
        "token": key,
        "label": label,
        "key_tail": key_tail(key),
        "verify_state": "unverified",
        "verified_at": 0,
    }, ensure_ascii=False)[:20000]
    existing.status = "active"
    existing.err_count = 0
    existing.consecutive_fails = 0
    existing.last_sync_at = datetime.utcnow()
    db.commit()
    db.refresh(existing)

    result = {"ok": True, "account_id": existing.id, "uid": digest,
              "key_tail": key_tail(key)}
    if probe_upstream:
        result["probe"] = probe(db, existing)
    return result


def export_records(db: Session, realm: str = "", *, reveal: bool = False) -> dict:
    """导出：默认脱敏（只给尾 4 位），`reveal=True` 给完整 key。"""
    out = []
    for account in accounts_for(db, realm):
        item = {
            "provider": PLATFORM,
            "uid": account.uid,
            "name": account.name,
            "label": label_of(account),
            "key_tail": key_tail(key_of(account)),
            "status": getattr(account, "status", "") or "",
            "verify_state": str(credentials_of(account).get("verify_state")
                                or "unverified"),
        }
        if reveal:
            item["key"] = key_of(account)
        out.append(item)
    return {"format": "miraihub-account", "platform": PLATFORM,
            "revealed": bool(reveal), "accounts": out}


def keepalive(db: Session, realm: str = "") -> dict:
    ok = 0
    failed: list[dict] = []
    for account in accounts_for(db, realm):
        try:
            result = probe(db, account)
            if result.get("ok"):
                ok += 1
            else:
                failed.append({"id": account.id,
                               "error": (result.get("error") or {}).get("message")})
        except Exception as exc:  # noqa: BLE001
            failed.append({"id": account.id, "error": str(exc)[:200]})
    return {"ok": True, "probed": ok, "failed": failed,
            "note": "探活只验证目录可达；key 有效性由真实对话回写"}


def catalog_rows() -> tuple[list[dict], str]:
    """拿目录（上游优先，失败回落内置）。返回 `(rows, note)`。"""
    try:
        rows = client.fetch_catalog()
    except client.UpstreamError as exc:
        logger.debug("Freebuff 目录拉取失败：%s", exc.message)
        return endpoints.default_catalog(), "上游目录拉取失败：%s" % exc.message
    return protocol.merge_catalog(rows), ""


def usage(db: Session, realm: str = "", days: int = 30) -> dict:
    from sqlalchemy import func

    from admin.models import UsageLog

    since = datetime.utcnow() - timedelta(days=int(days or 30))
    query = db.query(
        UsageLog.model,
        func.count(UsageLog.id).label("requests"),
        func.sum(UsageLog.total_tokens).label("total"),
    ).filter(UsageLog.platform == PLATFORM, UsageLog.created_at >= since)
    if realm:
        ids = [row[0] for row in db.query(Account.id).filter(
            Account.platform == PLATFORM,
            Account.domain == realm).all()]
        if not ids:
            return {"days": days, "items": [], "totals": {}}
        query = query.filter(UsageLog.account_id.in_(ids))
    items = [{"model": row.model or "-",
              "requests": int(row.requests or 0),
              "total_tokens": int(row.total or 0)}
             for row in query.group_by(UsageLog.model).all()]
    items.sort(key=lambda item: item["total_tokens"], reverse=True)
    return {"days": days, "items": items,
            "totals": {"requests": sum(item["requests"] for item in items),
                       "total_tokens": sum(item["total_tokens"]
                                           for item in items)}}