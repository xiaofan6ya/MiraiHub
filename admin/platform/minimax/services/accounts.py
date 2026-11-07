"""MiniMax Code 账号池：扫描 / 导入 / 额度同步 / 凭证续期。

## 余额口径

`credit/details` 的 `remaining_amount` 求和 = 可用积分（签到积分 30 天有效，
仅 MiniMax Code 可用；M Plan 是订阅制不落这个接口）。统一列写法与其它
平台一致：`balance_remain = 可用积分`，`balance_total = 累计发放`，
`credits_snapshot` 记明细 JSON（到期时间 / 各包余量），到期快照列同步填。
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime

from sqlalchemy.orm import Session

from admin.models import Account
from admin.platform.minimax.core import client, endpoints, paths
from admin.platform.minimax.core.endpoints import normalize_region
from admin.security import clean_display_name

logger = logging.getLogger("admin.platform.minimax.services.accounts")

PLATFORM = endpoints.PLATFORM


def credentials_of(account) -> dict:
    try:
        data = json.loads(account.auth_json or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def token_of(data: dict) -> str:
    return str(data.get("access_token") or data.get("token") or "")


def refresh_token_of(data: dict) -> str:
    return str(data.get("refresh_token") or data.get("refreshToken") or "")


def realm_of(account) -> str:
    data = credentials_of(account)
    explicit = str(data.get("realm") or "").strip().lower()
    if explicit in ("cn", "en"):
        return explicit
    return normalize_region(account.domain)


def public_view(account: Account) -> dict:
    data = credentials_of(account)
    realm = realm_of(account)
    expires = int(data.get("expires_at_ms") or 0)
    return {
        "id": account.id,
        "name": account.name,
        "uid": account.uid,
        "realm": realm,
        "status": account.status,
        "balance_remain": account.balance_remain,
        "balance_total": account.balance_total,
        "credits_snapshot": account.credits_snapshot,
        "credits_expiring": account.credits_expiring,
        "expires_at_ms": expires,
        "expires_in_days": round((expires / 1000 - time.time()) / 86400, 1)
        if expires else None,
        "has_refresh": bool(refresh_token_of(data)),
        "last_sync_at": account.balance_synced_at.isoformat()
        if account.balance_synced_at else None,
        "created_at": account.created_at.isoformat()
        if account.created_at else None,
        "last_err": account.last_err_msg or "",
    }


def accounts_for(db: Session, realm: str = "") -> list[Account]:
    query = db.query(Account).filter(Account.platform == PLATFORM)
    if realm:
        query = query.filter(Account.domain == normalize_region(realm))
    return query.order_by(Account.id.asc()).all()


def _upsert(db: Session, payload: dict) -> tuple[Account, bool]:
    """按 (platform, uid) 幂等写入。同 uid 只更新凭证。

    ## uid 必须是官方 user_id（同一账号只许一行）

    MiniMax 的账号有两个标识：auth.json record key 的 account_key
    （opaque 串，随客户端安装而异）与 **profile 的数字 user_id（权威）**。
    两个来源（本机扫描 / 设备授权）若各用各的标识，同一个真实账号就会
    落成两行 —— 一行活票一行死票。规则：
      * 扫描路径在入库前先 fetch_profile 把 uid 解析成 user_id；
      * 匹配顺序：uid → token → account_key 血缘（认领旧行并把 uid
        改写成 user_id，完成迁移）；
      * 命中后若池内还有同 uid 的**其它行**（历史双命名空间残留），
        一并合并删除 —— 同一真实账号只保留一行。
    """
    uid = str(payload.get("uid") or "").split("\0", 1)[-1].strip()
    payload["uid"] = uid
    account_key = str(payload.get("account_key") or "").strip()
    realm = normalize_region(payload.get("realm"))
    existing = None
    if uid:
        existing = (db.query(Account)
                    .filter(Account.platform == PLATFORM,
                            Account.uid == uid).first())
    if existing is None:
        tok = str(payload.get("token") or "")
        for row in accounts_for(db, realm):
            row_uid = str(row.uid or "").split("\0", 1)[-1].strip()
            if tok and row_uid and token_of(credentials_of(row)) == tok:
                existing = row
                break
    if existing is None and account_key:
        # 血缘认领：旧行可能记着 account_key（老版本扫描入库），uid 还是
        # opaque 串 —— 认领它并把 uid 迁移成 user_id
        for row in accounts_for(db, realm):
            if credentials_of(row).get("account_key") == account_key:
                existing = row
                break
        if existing is None:
            for row in accounts_for(db, realm):
                row_uid = str(row.uid or "").split("\0", 1)[-1].strip()
                if account_key and row_uid == account_key:
                    existing = row
                    break
    created = existing is None
    if existing is None:
        existing = Account(platform=PLATFORM, uid=uid, status="active",
                           domain=realm)
        db.add(existing)
    elif uid and existing.uid != uid:
        existing.uid = uid  # 血缘/令牌认领的旧行：uid 迁移到权威 user_id
    # 合并残留：同 uid 的其它行（历史双命名空间造成的重复）删掉，
    # 它们的凭证要么已死、要么与本行相同，保留 uid 权威行
    if uid:
        duplicates = (db.query(Account)
                      .filter(Account.platform == PLATFORM,
                              Account.uid == uid,
                              Account.id != existing.id).all())
        for dup in duplicates:
            db.delete(dup)
    existing.name = clean_display_name(
        str(payload.get("name") or existing.name or uid or "MiniMax Code"))
    existing.domain = realm
    existing.auth_json = json.dumps(payload, ensure_ascii=False)
    if created:
        existing.balance_total = 0
        existing.balance_remain = 0
    existing.status = "active"
    existing.last_sync_at = datetime.utcnow()
    db.commit()
    db.refresh(existing)
    return existing, created


def record_from_token(record: dict) -> dict:
    """外部凭证 → 标准入库 payload（token 必填）。"""
    token = str(record.get("token") or record.get("access_token") or "")
    if not token:
        raise ValueError("凭证缺少 token")
    return {
        "realm": normalize_region(record.get("realm") or record.get("domain")),
        "token": token,
        "refresh_token": str(record.get("refresh_token")
                             or record.get("refreshToken") or ""),
        "expires_at_ms": int(record.get("expires_at_ms")
                             or record.get("expiresAtMs") or 0),
        "uid": str(record.get("uid") or ""),
        "name": str(record.get("name") or ""),
        "source": str(record.get("source") or "import"),
    }


def import_record(db: Session, record: dict) -> tuple[Account, bool]:
    """导入一条凭证（幂等）。入库后自动补刷积分由 shared.schedule_import_refresh 完成。"""
    payload = record_from_token(record)
    account, created = _upsert(db, payload)
    # 入库后自动补刷积分（后台线程）：与 WorkBuddy/Trae 导入行为对齐
    try:
        from admin.platform import shared as _shared
        _shared.schedule_import_refresh(PLATFORM, [account.id])
    except Exception:  # noqa: BLE001 - 补刷排队失败不影响导入结果
        pass
    return account, created


def _resolve_uid_via_profile(token: str, realm: str,
                             fallback: str) -> tuple[str, str, str]:
    """用 token 拉一次 profile，把 uid 统一到**官方数字 user_id**。

    返回 (uid, name, account_id)；profile 拉不到时回落 (fallback, "", "")。
    本机扫描与设备授权两条入库路径共用它 —— 双命名空间（account_key vs
    user_id）会让同一真实账号落成两行，一行活票一行死票。
    """
    try:
        profile = client.fetch_profile(token, realm)
        uid = str(profile.get("user_id") or "")
        name = str(profile.get("user_name") or "")
        if uid:
            return uid, name, str(profile.get("user_id") or "")
    except Exception:  # noqa: BLE001 - 死票/离线时仍允许入库（回落标识）
        pass
    return fallback, "", ""


def import_local(db: Session, realm: str = "",
                 uids: list[str] | None = None) -> dict:
    """扫描本机 ~/.minimax 凭证并入库；可按区域 / uid 收窄。

    `uids` 来自通用扫描弹窗的勾选框（`uid_full`）——为空时导入全部
    （与其它平台「不勾就全导」语义一致）。返回带 `imported` 键，
    与通用导入确认弹窗的判定对齐。
    """
    files = paths.credential_files()
    if realm:
        want = normalize_region(realm)
        files = [f for f in files if f["realm"] == want]
    if not files:
        return {"imported": 0, "added": 0, "updated": 0, "scanned": 0,
                "reason": "未在本机找到 MiniMax Code 登录态（%s）"
                          % paths.auth_home()}
    added = updated = 0
    scanned = 0
    errors: list[str] = []
    for entry in files:
        for record in paths.read_records(entry["path"]):
            scanned += 1
            if uids:
                scan_uid = str(record.get("uid") or "")
                if scan_uid not in uids:
                    continue
            try:
                # uid 统一到官方 user_id（profile 一次往返，失败回落 account_key）
                uid, profile_name, _ = _resolve_uid_via_profile(
                    record["token"], record["realm"],
                    str(record.get("uid") or ""))
                record["uid"] = uid
                if profile_name:
                    record["name"] = profile_name
                _account, created = _upsert(db, record)
                added += 1 if created else 0
                updated += 0 if created else 1
                try:
                    from admin.platform import shared as _shared
                    _shared.schedule_import_refresh(PLATFORM, [_account.id])
                except Exception:  # noqa: BLE001
                    pass
            except Exception as exc:  # noqa: BLE001 - 单条失败不影响其余
                errors.append("跳过一条：%s" % str(exc)[:120])
    imported = added + updated
    result = {"imported": imported, "added": added, "updated": updated,
              "scanned": scanned}
    if errors:
        result["errors"] = errors
    if not imported and not errors:
        result["reason"] = ("勾选的凭证没有匹配到本机登录态"
                            if uids else "本机登录态里没有可用凭证")
    return result


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
        except Exception as exc:  # noqa: BLE001
            failed += 1
            errors.append("跳过一条：%s" % str(exc)[:120])
    return {"ok": failed == 0, "added": added, "updated": updated,
            "failed": failed, "total": len(items), "errors": errors[:20]}


# ---------------------------------------------------------------------------
# 凭证续期与额度
# ---------------------------------------------------------------------------
def ensure_token(db: Session, account: Account) -> dict:
    """拿可用 token：过期（提前 60s）且带 refresh_token 时先续签落库。

    续签成功后把新票**回写官方客户端 auth.json**（轮换票互踢的根治，
    见 paths.write_back_credential）。
    """
    data = credentials_of(account)
    expires = int(data.get("expires_at_ms") or 0)
    fresh_ok = expires and expires > time.time() * 1000 + 60_000
    if token_of(data) and fresh_ok:
        return data
    refresh = refresh_token_of(data)
    if not refresh:
        if token_of(data):
            return data
        raise client.UpstreamError(
            "凭证缺 access_token 与 refresh_token（请重新扫码授权或扫描本机）",
            kind="auth")
    grant = client.refresh_credentials(refresh, realm_of(account))
    data = dict(data)
    data["access_token"] = grant["access_token"]
    if grant.get("refresh_token"):
        data["refresh_token"] = grant["refresh_token"]
    data["expires_at_ms"] = grant.get("expires_at_ms") or 0
    account.auth_json = json.dumps(data, ensure_ascii=False)
    account.last_sync_at = datetime.utcnow()
    db.commit()
    try:
        paths.write_back_credential(
            realm_of(account),
            access_token=grant["access_token"],
            refresh_token=data["refresh_token"],
            expires_at_ms=int(data["expires_at_ms"] or 0))
    except Exception:  # noqa: BLE001 - 回写失败不影响续签本身
        pass
    logger.info("MiniMax 账号 %s 凭证已续期（已回写客户端 auth.json）", account.id)
    return data


def sync_quota(db: Session, account: Account) -> dict:
    """刷积分明细 → 统一列 + credits_snapshot + 到期快照。"""
    data = ensure_token(db, account)
    credit = client.fetch_credit_details(token_of(data), realm_of(account),
                                         account_id=int(account.id or 0))
    # 空结构不能当 0 清零（skill 铁律 5）：fetch_credit_details 已抛错/带明细
    account.balance_remain = int(round(credit["remain"]))
    account.balance_total = int(round(credit["total"]))
    account.last_sync_at = datetime.utcnow()
    account.balance_synced_at = datetime.utcnow()
    account.credits_snapshot = json.dumps({
        "unit": "credits",
        "remain": credit["remain"],
        "total": credit["total"],
        "expire_at_ms": credit["expire_at_ms"],
        "details": credit["details"][:20],
    }, ensure_ascii=False)
    if credit["expire_at_ms"]:
        account.credits_soonest_expire_at = datetime.utcfromtimestamp(
            credit["expire_at_ms"] / 1000)
    db.commit()
    return {"ok": True, "remain": credit["remain"], "total": credit["total"],
            "expire_at_ms": credit["expire_at_ms"]}


def summarize(db: Session, realm: str = "") -> dict:
    rows = accounts_for(db, realm)
    active = [row for row in rows if row.status == "active"]
    return {
        "platform": PLATFORM,
        "accounts": len(rows),
        "active": len(active),
        "remain_credits": round(sum(row.balance_remain or 0 for row in active), 2),
        "total_credits": round(sum(row.balance_total or 0 for row in active), 2),
    }
