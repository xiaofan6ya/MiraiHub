"""AStudio 账号池：扫描 / 导入 / 额度同步 / 本机重读。

## 凭证结构

    {"key": "<modelBearerToken>", "session": {"sso_session_id":…,
     "account_id":…, "token":…}, "expires_at": 0, "source": "scan|import",
     "uid":…, "name":…}

`key` 是模型凭据（JWT，约 3 年）；`session` 是网页会话 Cookie 材料
（积分 / 签到用）。只有 `key` 没有 `session` 的账号聊天照常、积分不可见。

## 余额口径

`points/balance` 给两个池子：通用积分（`totalBalance`，所有非星火模型
扣它）与星火积分（`sparkTotalBalance`，只圈 spark- 模型）。统一列：
`balance_remain = 通用积分`（选号路由用的是它），`balance_total =
通用 + 星火`（当前两个池子的总额），`credits_expiring = 活动余额`
（`activityBalance`，有到期时间 `activityNextExpireTime`），
`credits_snapshot` 记明细 JSON。
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime

from sqlalchemy.orm import Session

from admin.models import Account
from admin.platform.astudio.core import client, endpoints, paths
from admin.security import clean_display_name

logger = logging.getLogger("admin.platform.astudio.services.accounts")

PLATFORM = endpoints.PLATFORM


def credentials_of(account) -> dict:
    try:
        data = json.loads(account.auth_json or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def token_of(data: dict) -> str:
    return str(data.get("key") or data.get("modelBearerToken") or "")


def session_of(data: dict) -> dict | None:
    session = data.get("session")
    if not isinstance(session, dict):
        return None
    if not str(session.get("account_id") or "").strip():
        return None
    return session


def uid_of(data: dict) -> str:
    return str(data.get("uid") or "").strip()


def account_uid(account: Account) -> str:
    data = credentials_of(account)
    uid = uid_of(data)
    if uid:
        return uid
    return str(account.uid or "")


def public_view(account: Account) -> dict:
    data = credentials_of(account)
    expires = int(data.get("expires_at") or 0)
    return {
        "id": account.id,
        # 前端按账号归属平台选接口（刷新/删除/禁用），少了这个字段就会
        # 一律退回 WorkBuddy 的 `/api/accounts/*`，报「账号凭证缺少 auth」。
        "platform": PLATFORM,
        "name": account.name,
        "uid": account.uid,
        "realm": "cn",
        "status": account.status,
        "balance_remain": account.balance_remain,
        "balance_total": account.balance_total,
        "credits_snapshot": account.credits_snapshot,
        "credits_expiring": account.credits_expiring,
        "expires_at_ms": expires,
        "expires_in_days": round((expires / 1000 - time.time()) / 86400, 1)
        if expires else None,
        "has_session": bool(session_of(data)),
        "source": str(data.get("source") or ""),
        "last_sync_at": account.balance_synced_at.isoformat()
        if account.balance_synced_at else None,
        "created_at": account.created_at.isoformat()
        if account.created_at else None,
        "last_err": account.last_err_msg or "",
    }


def accounts_for(db: Session, realm: str = "") -> list[Account]:
    query = db.query(Account).filter(Account.platform == PLATFORM)
    return query.order_by(Account.id.asc()).all()


def _payload_from_record(record: dict) -> dict:
    """外部凭证 → 标准入库 payload（key 必填）。

    可接受的键（兼容三种来源的形状）：
      * key / modelBearerToken —— 模型凭据；
      * session_token / sessionToken / token —— 网页会话 token；
      * account_id / accountId —— 网页会话 account_id；
      * sso_session_id / ssoSessionId —— 网页会话补充材料；
      * uid / name / expires_at / source。
    """
    key = str(record.get("key") or record.get("modelBearerToken") or "").strip()
    web_token = str(record.get("session_token")
                    or record.get("sessionToken")
                    or record.get("token") or "").strip()
    account_id = str(record.get("account_id")
                     or record.get("accountId") or "").strip()
    if not key:
        # 只给了一个 token 字段时按「粘贴模型凭据」处理（老登录态里
        # modelBearerToken 缺失时桌面端也是拿 token 当模型凭据）；
        # token + accountId 齐了却没有 key 才是真缺 —— 那套是网页会话材料。
        if web_token and not account_id:
            key = web_token
            web_token = ""
        else:
            raise ValueError("凭证缺少模型凭据（key / modelBearerToken）")
    session = None
    if web_token and account_id:
        session = {"token": web_token, "account_id": account_id}
        sso = str(record.get("sso_session_id")
                  or record.get("ssoSessionId") or "").strip()
        if sso:
            session["sso_session_id"] = sso
    expires = record.get("expires_at")
    if expires is None:
        expires = record.get("expiresAt")
    try:
        expires_at = int(expires or 0)
    except (TypeError, ValueError):
        expires_at = 0
    return {
        "key": key,
        "session": session,
        "expires_at": expires_at,
        "source": str(record.get("source") or "import"),
        "uid": str(record.get("uid") or "").strip(),
        "name": str(record.get("name") or "").strip(),
    }


def _fallback_uid(key: str) -> str:
    import hashlib
    return "ast-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


def _upsert(db: Session, payload: dict) -> tuple[Account, bool]:
    """按 (platform, uid) 幂等写入。同 uid 只更新凭证。"""
    uid = str(payload.get("uid") or "").strip()
    key = str(payload.get("key") or "")
    existing = None
    if uid:
        existing = (db.query(Account)
                    .filter(Account.platform == PLATFORM,
                            Account.uid == uid).first())
    if existing is None and key:
        # 没有 uid 的旧行（历史导入）按 token 血缘认领
        for row in accounts_for(db):
            if token_of(credentials_of(row)) == key:
                existing = row
                break
    created = existing is None
    if existing is None:
        if not uid:
            uid = _fallback_uid(key)
        existing = Account(platform=PLATFORM, uid=uid, status="active",
                           domain="cn")
        db.add(existing)
    elif uid and existing.uid != uid:
        existing.uid = uid
    existing.name = clean_display_name(
        str(payload.get("name") or existing.name or uid or "AStudio"))
    existing.domain = "cn"
    existing.auth_json = json.dumps(payload, ensure_ascii=False)
    if created:
        existing.balance_total = 0
        existing.balance_remain = 0
    existing.status = "active"
    existing.last_sync_at = datetime.utcnow()
    db.commit()
    db.refresh(existing)
    return existing, created


def import_record(db: Session, record: dict) -> tuple[Account, bool]:
    """导入一条凭证（幂等）。入库后自动补刷积分由 shared.schedule_import_refresh 完成。"""
    payload = _payload_from_record(record)
    account, created = _upsert(db, payload)
    try:
        from admin.platform import shared as _shared
        _shared.schedule_import_refresh(PLATFORM, [account.id])
    except Exception:  # noqa: BLE001 - 补刷排队失败不影响导入结果
        pass
    return account, created


def import_batch(db: Session, text: str) -> dict:
    """批量导入（JSONL / JSON 数组 / 单对象）。"""
    text = (text or "").strip()
    if not text:
        return {"ok": False, "added": 0, "updated": 0, "failed": 0,
                "total": 0, "errors": ["没有提供任何凭证"]}
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = None
    items: list = []
    if isinstance(parsed, dict):
        items = [parsed]
    elif isinstance(parsed, list):
        items = [row for row in parsed if isinstance(row, dict)]
    else:
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict):
                items.append(obj)
    if not items:
        return {"ok": False, "added": 0, "updated": 0, "failed": 0,
                "total": 0,
                "errors": ["没有解析出任何账号记录（应为 JSONL 或 JSON 数组）"]}
    added = updated = failed = 0
    errors: list[str] = []
    for item in items:
        try:
            _account, created = import_record(db, item)
            added += 1 if created else 0
            updated += 0 if created else 1
        except Exception as exc:  # noqa: BLE001 - 单条失败不影响其余
            failed += 1
            errors.append("跳过一条：%s" % str(exc)[:120])
    return {"ok": failed == 0, "added": added, "updated": updated,
            "failed": failed, "total": len(items), "errors": errors[:20]}


def import_local(db: Session, uids: list[str] | None = None) -> dict:
    """扫描本机 AStudio 登录态并入库；可按 uid 收窄。

    `uids` 来自通用扫描弹窗的勾选框（`uid_full`）——为空时导入全部
    （与其它平台「不勾就全导」语义一致）。被封禁的登录态跳过。
    """
    items = paths.discover()
    if uids:
        items = [it for it in items if it.get("uid_full") in uids]
    added = updated = scanned = 0
    errors: list[str] = []
    for item in items:
        if item.get("banned"):
            continue
        scanned += 1
        path = item.get("path") or ""
        if path.endswith("config.toml"):
            record = paths.read_config_token(path)
        else:
            record = paths.read_session(path)
        if record is None or not record.get("key"):
            errors.append("跳过一条：%s" % (item.get("problem") or "无可用凭据"))
            continue
        try:
            _account, created = _upsert(db, record)
            added += 1 if created else 0
            updated += 0 if created else 1
            try:
                from admin.platform import shared as _shared
                _shared.schedule_import_refresh(PLATFORM, [_account.id])
            except Exception:  # noqa: BLE001
                pass
        except Exception as exc:  # noqa: BLE001
            errors.append("跳过一条：%s" % str(exc)[:120])
    result = {"imported": added + updated, "added": added, "updated": updated,
              "scanned": scanned}
    if errors:
        result["errors"] = errors
    if not result["imported"] and not errors:
        result["reason"] = ("勾选的凭证没有匹配到本机登录态"
                            if uids else "本机登录态里没有可用凭证")
    return result


def refresh_from_disk(db: Session, account: Account) -> bool:
    """扫描来源的账号：重新读本机登录态，捡回客户端重登后的新凭据。

    模型凭据是 3 年 JWT 没有续签端点，但用户在客户端重新登录会换发
    新凭据、旧 token 仍可用 —— 不重读就会一直拿旧票。读到就更新落库，
    读不到保留现有凭证（不把健康号改成死号）。
    """
    data = credentials_of(account)
    if str(data.get("source") or "") != "local-scan":
        return False
    items = paths.discover()
    key = token_of(data)
    for item in items:
        path = item.get("path") or ""
        if path.endswith("config.toml"):
            record = paths.read_config_token(path)
        else:
            record = paths.read_session(path)
        if record is None or not record.get("key"):
            continue
        if key and record["key"] != key:
            continue
        fresh = dict(data)
        fresh.update({k: record[k] for k in
                      ("key", "session", "uid", "name", "expires_at")
                      if k in record})
        fresh["source"] = "local-scan"
        account.auth_json = json.dumps(fresh, ensure_ascii=False)
        db.commit()
        return True
    return False


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def sync_quota(db: Session, account: Account) -> dict:
    """刷积分余额 → 统一列 + credits_snapshot。

    只有模型凭据（无网页会话）的账号无法读积分：返回 ok=False 与原因，
    不把余额清零（空结构绝不能当 0 落库）。
    """
    data = credentials_of(account)
    token = token_of(data)
    if not token:
        return {"ok": False, "remain": None, "total": None,
                "error": "凭证缺少模型凭据"}
    session = session_of(data)
    if session is None:
        account.last_sync_at = datetime.utcnow()
        account.balance_synced_at = datetime.utcnow()
        account.credits_snapshot = json.dumps({
            "unit": "credits",
            "note": "只有模型凭据（无网页会话），积分余额不可用",
        }, ensure_ascii=False)
        db.commit()
        return {"ok": False, "remain": None, "total": None,
                "error": "该账号只有模型凭据（无网页会话），积分余额不可用"}
    account_id = int(account.id or 0)
    balance = client.fetch_balance(session, account_id=account_id)
    membership = None
    try:
        membership = client.fetch_membership(session, account_id=account_id)
    except client.UpstreamError as exc:
        if exc.kind == "auth":
            raise
        # 会员档读失败不塌方：余额照常展示
    general = _int(balance.get("totalBalance"))
    spark = _int(balance.get("sparkTotalBalance"))
    activity = _int(balance.get("activityBalance"))
    account.balance_remain = general
    account.balance_total = general + spark
    account.credits_expiring = activity
    account.last_sync_at = datetime.utcnow()
    account.balance_synced_at = datetime.utcnow()
    account.credits_snapshot = json.dumps({
        "unit": "credits",
        "general": general,
        "spark": spark,
        "activity": activity,
        "activity_next_expire": str(balance.get("activityNextExpireTime") or ""),
        "plan": str((membership or {}).get("planName") or ""),
        "member_until": str((membership or {}).get("endTime") or ""),
    }, ensure_ascii=False)
    db.commit()
    return {"ok": True, "remain": general, "total": general + spark,
            "plan": str((membership or {}).get("planName") or "")}


def summarize(db: Session) -> dict:
    rows = accounts_for(db)
    active = [row for row in rows if row.status == "active"]
    return {
        "platform": PLATFORM,
        "accounts": len(rows),
        "active": len(active),
        "remain_credits": round(sum(row.balance_remain or 0 for row in active), 2),
        "total_credits": round(sum(row.balance_total or 0 for row in active), 2),
    }
