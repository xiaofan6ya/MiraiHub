"""WPS 灵犀凭证与账号池。

## 凭证形态

上游只认 `wps_sid`（可选 `uid` / `copilot_server_branch`），**没有刷新接口**
—— 过期只能重新粘 Cookie。落库归一：

```json
{"realm":"cn","cookie":"wps_sid=…; uid=…","sid":"<尾 4 位之外不单独存>",
 "uid":"…","account_id":"wps-<uid 或 sid 后 8 位>","name":"昵称",
 "base":"https://lingxi.wps.cn","probed_at":<秒>}
```

> `auth_json` 里存**整段 Cookie**，因为三步通路每次都要原样带；
> 对外视图（`public_view`）只给 sid 尾 4 位，绝不下发 Cookie。

## 额度

`GET /api/public/v1/credits/balance` → `total_balance`（读不到回落 `balance`）。
`bonus_summary` 算已用比例；`bonus_credits[]` 里 15 天内到期的批次
映射到 `credits_expiring_soon`（"临时积分"）。

## 探活

鉴权失败**不立刻禁用**：先累计 `consecutive_fails`，达到
`PROBE_DISABLE_AFTER` 才置 `disabled`。灵犀没有 refresh，偶发 401
（风控抖动）很常见，单次就禁用会把好号误杀。
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from admin.models import Account
from admin.platform.wps_lingxi.core import client, endpoints, protocol

logger = logging.getLogger("admin.platform.wps_lingxi.accounts")
PLATFORM = endpoints.PLATFORM
QUOTA_TTL = 60

#: 连续鉴权失败多少次才判定账号真死。
PROBE_DISABLE_AFTER = 3


def credentials_of(account: Account) -> dict:
    try:
        data = json.loads(account.auth_json or "{}")
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def cookie_of(account: Account) -> str:
    data = credentials_of(account)
    cookie = str(data.get("cookie") or "").strip()
    if cookie:
        return cookie
    sid = str(data.get("sid") or "").strip()
    if sid:
        uid = str(data.get("uid") or "").strip()
        return "wps_sid=%s%s" % (sid, ("; uid=%s" % uid) if uid else "")
    return ""


def base_of(account: Account) -> str:
    data = credentials_of(account)
    return str(data.get("base") or endpoints.base_of()).rstrip("/")


def realm_of(account: Account) -> str:
    return str(getattr(account, "domain", "") or "cn")


def quota_is_fresh(account: Account) -> bool:
    synced = getattr(account, "credits_synced_at", None)
    if synced is None:
        return False
    return (datetime.utcnow() - synced).total_seconds() < QUOTA_TTL


def accounts_for(db: Session, realm: str = "") -> list[Account]:
    query = db.query(Account).filter(Account.platform == PLATFORM)
    if realm:
        query = query.filter(Account.domain == realm)
    return query.order_by(Account.id.asc()).all()


def select(db: Session, realm: str = "", mark_picked: bool = False):
    now = datetime.utcnow()
    for account in accounts_for(db, realm):
        if (getattr(account, "status", "") or "") != "active":
            continue
        cool = getattr(account, "cool_until", None)
        if cool and cool > now:
            continue
        breaker = getattr(account, "breaker_until", None)
        if breaker and breaker > now:
            continue
        if mark_picked:
            account.last_picked_at = now
            db.commit()
        return account
    return None


def apply_quota(account: Account, quota: dict) -> None:
    total = int(quota.get("total") or 0)
    account.balance_remain = total
    account.balance_total = int(quota.get("bonus_total") or 0) or total
    account.credits_expiring_soon = int(quota.get("expiring_balance") or 0)
    account.credits_synced_at = datetime.utcnow()
    account.last_sync_at = datetime.utcnow()
    account.credits_snapshot = json.dumps({
        "unit": "credits",
        "quota_kind": "balance",
        "total": total,
        "bonus_total": int(quota.get("bonus_total") or 0),
        "bonus_used": quota.get("bonus_used"),
        "used_pct": int(quota.get("used_pct") or 0),
        "expiring_balance": int(quota.get("expiring_balance") or 0),
        "expiring_value": int(quota.get("expiring_value") or 0),
        "expiring_earliest": str(quota.get("expiring_earliest") or ""),
        "batches": int(quota.get("batches") or 0),
    }, ensure_ascii=False)[:20000]


def _count_failure(db: Session, account: Account, exc: client.UpstreamError) -> None:
    fails = int(getattr(account, "consecutive_fails", 0) or 0) + 1
    account.consecutive_fails = fails
    account.err_count = int(getattr(account, "err_count", 0) or 0) + 1
    account.last_err_msg = (exc.message or "")[:250]
    account.last_err_at = datetime.utcnow()
    if exc.kind == "auth" and fails >= PROBE_DISABLE_AFTER:
        account.status = "disabled"
        logger.warning("灵犀账号 %s 连续 %d 次鉴权失败，已禁用",
                       account.id, fails)
    try:
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()


def sync_quota(db: Session, account: Account) -> dict:
    """探活 + 同步余额。返回 `{ok, quota|error}`。"""
    cookie = cookie_of(account)
    if not cookie:
        return {"ok": False, "error": {"kind": "auth", "status": 401,
                                       "message": "账号缺少 Cookie，请重新导入"}}
    try:
        quota = client.fetch_balance(cookie, base_of(account))
    except client.UpstreamError as exc:
        _count_failure(db, account, exc)
        return {"ok": False, "error": exc.to_dict()}
    if int(getattr(account, "consecutive_fails", 0) or 0):
        account.consecutive_fails = 0
    if (account.status or "") == "disabled":
        account.status = "active"
    apply_quota(account, quota)
    try:
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
    return {"ok": True, "quota": quota}


def health(db: Session, account: Account) -> dict:
    """分段健康检查：凭证 / 资料 / 额度 / 目录 / 对话可用性。"""
    cookie = cookie_of(account)
    stages: dict = {}
    if not cookie:
        return {"ok": False, "stages": {"cookie": {"ok": False,
                                                   "detail": "没有 Cookie"}}}
    stages["cookie"] = {"ok": True,
                        "detail": "wps_sid 尾号 %s" % cookie[-4:]}
    try:
        profile = client.fetch_profile(cookie)
        stages["profile"] = {"ok": True,
                             "detail": "%s（%s）" % (profile.get("name") or "-",
                                                     profile.get("base"))}
    except client.UpstreamError as exc:
        stages["profile"] = {"ok": False, "detail": exc.message}
        return {"ok": False, "stages": stages}
    try:
        quota = client.fetch_balance(cookie, base_of(account))
        stages["quota"] = {"ok": True,
                           "detail": "剩余 %s 智点%s" % (
                               quota["total"],
                               ("，%s 天内到期 %s" % (
                                   os.getenv("ADMIN_WPS_LINGXI_TEMP_DAYS") or 15,
                                   quota["expiring_balance"]))
                               if quota.get("expiring_balance") else "")}
    except client.UpstreamError as exc:
        stages["quota"] = {"ok": False, "detail": exc.message}
    models = client.fetch_plans(cookie, base_of(account))
    stages["models"] = {"ok": bool(models),
                        "detail": "目录 %d 个模型" % len(models)
                        if models else "目录不可达（回落内置快照）"}
    stages["chat"] = {"ok": True, "detail": "需要真实请求才能判定"}
    return {"ok": all(item.get("ok") for key, item in stages.items()
                      if key != "chat"), "stages": stages}


def public_view(account: Account) -> dict:
    """给前端的视图（**不含 Cookie**）。"""
    data = credentials_of(account)
    try:
        snap = json.loads(account.credits_snapshot or "{}")
    except ValueError:
        snap = {}
    snap = snap if isinstance(snap, dict) else {}
    cookie = cookie_of(account)
    return {
        "id": account.id,
        "name": account.name,
        "uid": account.uid,
        "realm": realm_of(account),
        "status": getattr(account, "status", "") or "",
        "balance_remain": int(getattr(account, "balance_remain", 0) or 0),
        "balance_total": int(getattr(account, "balance_total", 0) or 0),
        "expiring": int(getattr(account, "credits_expiring_soon", 0) or 0),
        "expiring_earliest": str(snap.get("expiring_earliest") or ""),
        "used_pct": int(snap.get("used_pct") or 0),
        "quota_kind": "balance",
        "quota_live": bool(snap),
        "account_id": str(data.get("account_id") or ""),
        "sid_tail": cookie[-4:] if cookie else "",
        "base": base_of(account),
        "has_cookie": bool(cookie),
        "last_sync_at": account.last_sync_at.isoformat()
        if account.last_sync_at else None,
        "credits_synced_at": account.credits_synced_at.isoformat()
        if getattr(account, "credits_synced_at", None) else None,
        "last_err_msg": (getattr(account, "last_err_msg", "") or "")[:200],
    }


def summarize(rows: list[dict]) -> dict:
    total = len(rows)
    active = sum(1 for row in rows if row.get("status") == "active")
    return {
        "total": total,
        "active": active,
        "disabled": total - active,
        "balance_remain": sum(int(row.get("balance_remain") or 0) for row in rows),
        "balance_total": sum(int(row.get("balance_total") or 0) for row in rows),
        "expiring": sum(int(row.get("expiring") or 0) for row in rows),
        "quota_kind": "balance",
    }


# ---------------------------------------------------------------------------
# 导入 / 导出
# ---------------------------------------------------------------------------
def import_record(db: Session, record, *, probe: bool = True) -> dict:
    """导入一条凭证。

    `record` 兼容：
      * `{"cookie": "wps_sid=…; uid=…"}`
      * `{"wps_sid": "…", "uid": "…"}`
      * 裸字符串（整段 Cookie 或裸 sid）

    `probe=False` 时**不打上游**（批量导入几十个号时先落库，再统一探活）。
    """
    raw = record
    name = ""
    if isinstance(record, dict):
        raw = (record.get("cookie") or record.get("credential")
               or record.get("wps_sid") or record.get("sid") or "")
        name = str(record.get("name") or "")
        if not raw and isinstance(record.get("raw"), str):
            raw = record["raw"]
        if not raw:
            # 整个 dict 可能是 {"wps_sid": "...", "uid": "..."} 的形状
            parts = []
            for key in ("wps_sid", "uid", "copilot_server_branch"):
                value = record.get(key)
                if isinstance(value, str) and value.strip():
                    parts.append("%s=%s" % (key, value.strip()))
            raw = "; ".join(parts)
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("凭证里没有 wps_sid（需要 cookie 或裸 sid）")

    parsed = protocol.parse_cookie(raw)
    uid = parsed["uid"]
    account_key = parsed["account_id"]

    existing = None
    if uid:
        existing = db.query(Account).filter(Account.platform == PLATFORM,
                                            Account.uid == uid).first()
    if existing is None:
        existing = db.query(Account).filter(
            Account.platform == PLATFORM, Account.name == account_key).first()
    if existing is None:
        existing = Account(platform=PLATFORM, uid=uid, name=account_key,
                           status="active")
        db.add(existing)

    base = endpoints.base_of()
    profile_name = ""
    if probe:
        try:
            profile = client.fetch_profile(parsed["cookie"])
            base = profile.get("base") or base
            profile_name = profile.get("name") or ""
            uid = uid or profile.get("uid") or ""
        except client.UpstreamError as exc:
            # 探活失败仍落库（可能只是网络抖动），但把原因带回去
            existing.auth_json = json.dumps({
                "realm": "cn", "cookie": parsed["cookie"], "uid": uid,
                "account_id": account_key, "base": base,
                "probed_at": 0,
            }, ensure_ascii=False)
            existing.status = "active"
            db.commit()
            return {"ok": False, "account_id": existing.id, "uid": uid,
                    "error": exc.to_dict(),
                    "hint": "Cookie 可能无效或已过期；已按原样落库，可稍后重新探活"}

    existing.name = name or profile_name or account_key
    existing.uid = uid or existing.uid
    existing.domain = "cn"
    existing.auth_json = json.dumps({
        "realm": "cn",
        "cookie": parsed["cookie"],
        "uid": uid,
        "account_id": account_key,
        "base": base,
        "probed_at": int(datetime.utcnow().timestamp()),
    }, ensure_ascii=False)[:20000]
    existing.status = "active"
    existing.err_count = 0
    existing.consecutive_fails = 0
    existing.last_sync_at = datetime.utcnow()
    db.commit()
    db.refresh(existing)

    probe_result = sync_quota(db, existing) if probe else {"ok": None,
                                                          "skipped": True}
    return {"ok": True, "account_id": existing.id, "uid": uid,
            "account_id_label": account_key, "probe": probe_result}


def export_records(db: Session, realm: str = "", *, reveal: bool = False) -> dict:
    """导出账号。

    `reveal=False`（默认）**脱敏**：只给 sid 尾 4 位，用于核对；
    `reveal=True` 给完整 Cookie，用于迁移/恢复（调用方须是管理员）。
    """
    out = []
    for account in accounts_for(db, realm):
        data = credentials_of(account)
        cookie = cookie_of(account)
        item = {
            "provider": PLATFORM,
            "uid": account.uid,
            "name": account.name,
            "account_id": str(data.get("account_id") or ""),
            "base": base_of(account),
            "status": getattr(account, "status", "") or "",
            "balance_remain": int(getattr(account, "balance_remain", 0) or 0),
            "sid_tail": cookie[-4:] if cookie else "",
        }
        if reveal:
            item["cookie"] = cookie
        out.append(item)
    return {"format": "miraihub-account", "platform": PLATFORM,
            "revealed": bool(reveal), "accounts": out}


# ---------------------------------------------------------------------------
# 本机客户端登录态（Windows / macOS 的 WPS 灵犀客户端）
# ---------------------------------------------------------------------------
def client_dirs() -> list[str]:
    """灵犀客户端的数据目录候选（存在的才返回）。"""
    names = ("WPS 灵犀", "Lingxi", "lingxi", "lingxi-desktop")
    roots: list[str] = []
    explicit = (os.getenv("ADMIN_WPS_LINGXI_HOME") or "").strip()
    if explicit:
        roots.append(explicit)
    if sys.platform == "win32":
        appdata = os.getenv("APPDATA") or os.path.expanduser(
            "~/AppData/Roaming")
        roots.extend(os.path.join(appdata, name) for name in names)
    elif sys.platform == "darwin":
        support = os.path.expanduser("~/Library/Application Support")
        roots.extend(os.path.join(support, name) for name in names)
    else:
        config = os.path.expanduser("~/.config")
        roots.extend(os.path.join(config, name) for name in names)
    out: list[str] = []
    for root in roots:
        if root and os.path.isdir(root) and root not in out:
            out.append(root)
    return out


def cookie_dbs() -> list[str]:
    """Cookie 库候选：`Network/Cookies`、`Cookies`、`Partitions/<n>/…`。"""
    found: list[str] = []
    for root in client_dirs():
        candidates = [os.path.join(root, "Network", "Cookies"),
                      os.path.join(root, "Cookies")]
        partitions = os.path.join(root, "Partitions")
        if os.path.isdir(partitions):
            try:
                for name in sorted(os.listdir(partitions)):
                    sub = os.path.join(partitions, name)
                    if not os.path.isdir(sub):
                        continue
                    candidates.append(os.path.join(sub, "Network", "Cookies"))
                    candidates.append(os.path.join(sub, "Cookies"))
            except OSError:
                pass
        for path in candidates:
            if os.path.isfile(path) and path not in found:
                found.append(path)
    return found


def _dpapi_unprotect(blob: bytes) -> bytes:
    """DPAPI（CurrentUser）解密。仅 Windows 可用。"""
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_byte))]

    def _to_blob(data: bytes):
        buffer = ctypes.create_string_buffer(data, len(data))
        value = DATA_BLOB(len(data), ctypes.cast(
            buffer, ctypes.POINTER(ctypes.c_byte)))
        return value, buffer

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    blob_in, keep_in = _to_blob(blob)
    blob_out = DATA_BLOB()
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0,
        ctypes.byref(blob_out))
    if not ok:
        raise OSError("CryptUnprotectData 失败（错误码 %s）"
                      % kernel32.GetLastError())
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _chromium_key(root: str) -> bytes:
    """从 `Local State` 拿 Chromium 的 AES-256 key（DPAPI 包装）。"""
    state_path = os.path.join(root, "Local State")
    if not os.path.isfile(state_path):
        raise OSError("没有找到 Local State")
    with open(state_path, "r", encoding="utf-8") as handle:
        state = json.load(handle)
    encrypted = ((state.get("os_crypt") or {}).get("encrypted_key") or "")
    if not encrypted:
        raise OSError("Local State 里没有 os_crypt.encrypted_key")
    import base64

    raw = base64.b64decode(encrypted)
    if raw[:5] != b"DPAPI":
        raise OSError("Local State 里的 key 不是 DPAPI 包装格式")
    key = _dpapi_unprotect(raw[5:])
    if len(key) != 32:
        raise OSError("解出的 AES key 长度不是 32 字节")
    return key


def _decrypt_cookie(blob: bytes, key: bytes, host: str) -> str:
    """解 Chromium v10/v11 Cookie（AES-256-GCM + 32 字节 host 哈希前缀）。"""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if blob[:3] not in (b"v10", b"v11"):
        raise ValueError("不支持的 Cookie 加密版本")
    nonce = blob[3:15]
    payload = blob[15:]
    if len(payload) <= 16:
        raise ValueError("Cookie 密文太短")
    plain = AESGCM(key).decrypt(nonce, payload[:-16], None)
    # Chromium 会把自己的 host 哈希放在明文前 32 字节
    if len(plain) > 32:
        prefix = plain[:32]
        if any(byte < 0x09 or 0x0D < byte < 0x20 or byte > 0x7E
               for byte in prefix):
            return plain[32:].decode("utf-8", "replace")
    return plain.decode("utf-8", "replace")


def _read_cookies(path: str) -> dict:
    """从一个 Cookie 库里挖出 `wps_sid` / `uid`。"""
    host = os.path.dirname(os.path.dirname(path))
    key = b""
    try:
        key = _chromium_key(host)
    except Exception as exc:  # noqa: BLE001
        logger.debug("灵犀客户端 Cookie 密钥不可用：%s", exc)
    values: dict[str, str] = {}
    connection = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"),
                                 uri=True)
    try:
        rows = connection.execute(
            "select host_key, name, value, encrypted_value from cookies "
            "where name in ('wps_sid','uid')").fetchall()
    finally:
        connection.close()
    for host_key, name, value, encrypted in rows:
        text = (value or "").strip()
        if not text and encrypted:
            try:
                text = _decrypt_cookie(bytes(encrypted), key, host_key or "")
            except Exception:  # noqa: BLE001
                continue
        text = (text or "").strip()
        if not text or not all(0x21 <= ord(ch) <= 0x7E for ch in text):
            continue
        if name == "wps_sid":
            # 同名 cookie 多个 host 时优先 wps.cn
            if "wps_sid" in values and not str(host_key or "").endswith("wps.cn"):
                continue
            values["wps_sid"] = text
            values["_host"] = str(host_key or "")
        elif name == "uid" and "uid" not in values:
            values["uid"] = text
    return values


def scan_local() -> dict:
    """扫描本机灵犀客户端登录态（**不返回 Cookie 明文**）。"""
    items: list[dict] = []
    errors: list[str] = []
    dbs = cookie_dbs()
    if not dbs:
        dirs = client_dirs()
        hint = ("没有找到 WPS 灵犀客户端数据目录"
                if not dirs else
                "找到客户端目录但没有 Cookies 库（%s），先在灵犀客户端登录一次"
                % "、".join(dirs))
        return {"items": [], "errors": [], "dbs": 0, "hint": hint}
    for path in dbs:
        try:
            values = _read_cookies(path)
        except Exception as exc:  # noqa: BLE001
            errors.append("%s: %s" % (path, str(exc)[:160]))
            continue
        sid = values.get("wps_sid") or ""
        if len(sid) < 16:
            continue
        items.append({
            "path": path,
            "host": values.get("_host") or "",
            "uid": values.get("uid") or "",
            "sid_tail": sid[-4:],
            "has_sid": True,
            "usable": True,
        })
    return {"items": items, "errors": errors, "dbs": len(dbs)}


def import_local(db: Session, *, probe: bool = True) -> dict:
    """把扫描到的本机登录态导入账号池。"""
    if not cookie_dbs():
        return {"ok": False, "imported": 0,
                "reason": "没有找到 WPS 灵犀客户端 Cookie 库",
                "hint": "先在灵犀客户端登录，或改用「粘贴 Cookie」导入"}
    imported = 0
    failed: list[str] = []
    for item in scan_local().get("items") or []:
        try:
            values = _read_cookies(item["path"])
        except Exception as exc:  # noqa: BLE001
            failed.append("%s: %s" % (item["path"], str(exc)[:120]))
            continue
        sid = values.get("wps_sid") or ""
        if not sid:
            continue
        raw = "wps_sid=%s" % sid
        if values.get("uid"):
            raw += "; uid=%s" % values["uid"]
        try:
            import_record(db, raw, probe=probe)
            imported += 1
        except Exception as exc:  # noqa: BLE001
            failed.append(str(exc)[:160])
    return {"ok": True, "imported": imported, "failed": failed}


def keepalive(db: Session, realm: str = "") -> dict:
    ok = 0
    failed: list[dict] = []
    for account in accounts_for(db, realm):
        try:
            result = sync_quota(db, account)
            if result.get("ok"):
                ok += 1
            else:
                failed.append({"id": account.id, "uid": account.uid,
                               "error": (result.get("error") or {}).get("message")})
        except Exception as exc:  # noqa: BLE001
            failed.append({"id": account.id, "error": str(exc)[:200]})
    return {"ok": True, "refreshed": ok, "failed": failed}