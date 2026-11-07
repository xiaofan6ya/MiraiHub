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
        # 控制台按这个字段拼管理接口前缀（/api/<platform>/accounts/{id}/refresh），
        # 缺了它会退化成 WorkBuddy 的 /api/accounts，点「刷新余额」必然报凭证结构不符。
        "platform": PLATFORM,
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


def import_batch(db: Session, text: str, *, on_progress=None,
                 probe: bool = True) -> dict:
    """批量导入：控制台「导入账号」粘贴框 / 备份文件走的入口。

    ## 为什么必须接受「裸 Cookie 行」

    灵犀的凭证形态就是一串 `wps_sid=…; uid=…`，用户从 DevTools 复制出来
    根本不会是 JSON。所以这里按「逐条猜」的顺序解析：

      1. 整段是 JSON 数组 → 每条一个记录；
      2. 整段是 JSON 对象 → 可能是 `{"accounts":[...]}` 包装，也可能是单条；
      3. 逐行 JSON（JSONL，导出文件的形态）；
      4. 逐行**裸 Cookie**（含 `wps_sid=` 的行）—— 粘贴最常见的形式。

    上游探测失败**不阻断落库**（`import_record` 自己会带原因），
    这样「Cookie 可能过期但先存着」的用法也能成立。
    """
    import json as _json

    records: list = []
    body = (text or "").strip()
    if not body:
        return {"ok": False, "added": 0, "updated": 0, "failed": 0, "total": 0,
                "errors": ["没有内容"]}

    parsed = None
    try:
        parsed = _json.loads(body)
    except Exception:  # noqa: BLE001 - 不是整段 JSON，走逐行解析
        parsed = None

    if isinstance(parsed, list):
        records = [item for item in parsed if isinstance(item, (dict, str))]
    elif isinstance(parsed, dict):
        inner = parsed.get("accounts") or parsed.get("items")
        if isinstance(inner, list):
            records = [item for item in inner if isinstance(item, (dict, str))]
        else:
            records = [parsed]
    else:
        for line in body.splitlines():
            line = line.strip().rstrip(",")
            if not line or line in ("[", "]"):
                continue
            try:
                item = _json.loads(line)
            except Exception:  # noqa: BLE001 - 就当裸 Cookie 行
                if "wps_sid=" in line or line.count("=") >= 1:
                    records.append(line)
                continue
            if isinstance(item, (dict, str)):
                records.append(item)

    total = len(records)
    added = updated = failed = 0
    errors: list[str] = []
    for index, record in enumerate(records, 1):
        try:
            # 先判断这条是新增还是更新（import_record 按 uid/account_key 幂等）
            existed = False
            try:
                existing = _existing_of(db, record)
                existed = existing is not None
            except Exception:  # noqa: BLE001
                existed = False
            result = import_record(db, record, probe=probe)
            if result.get("ok"):
                if existed:
                    updated += 1
                else:
                    added += 1
            else:
                # 落库了但探活失败：算「导入成功、探活失败」，如实记一条
                if existed:
                    updated += 1
                else:
                    added += 1
                error = (result.get("error") or {}).get("message") \
                    if isinstance(result.get("error"), dict) else result.get("error")
                if error and len(errors) < 200:
                    errors.append("%s：%s" % (result.get("uid") or "?", error))
        except Exception as exc:  # noqa: BLE001 - 单条失败不影响整批
            failed += 1
            if len(errors) < 200:
                errors.append(str(exc)[:160])
        if callable(on_progress):
            try:
                on_progress(index, total,
                            "已导入 %d/%d（新增 %d，更新 %d，失败 %d）"
                            % (index, total, added, updated, failed))
            except Exception:  # noqa: BLE001
                pass

    return {"ok": failed == 0, "added": added, "updated": updated,
            "failed": failed, "total": total, "errors": errors}


def _existing_of(db: Session, record):
    """这条记录是不是已经在库里（用来区分新增 / 更新）。"""
    raw = record
    if isinstance(record, dict):
        raw = (record.get("cookie") or record.get("credential")
               or record.get("wps_sid") or record.get("sid") or record.get("raw") or "")
    if not isinstance(raw, str) or not raw.strip():
        return None
    parsed = protocol.parse_cookie(raw)
    uid = parsed["uid"]
    if uid:
        row = db.query(Account).filter(Account.platform == PLATFORM,
                                       Account.uid == uid).first()
        if row is not None:
            return row
    key = parsed["account_id"]
    if key:
        return db.query(Account).filter(Account.platform == PLATFORM,
                                        Account.name == key).first()
    return None


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


def _chromium_profile_dbs(root: str) -> list[str]:
    """取一个 Chromium 系浏览器用户目录下的所有 Cookies 库。

    Chrome / Edge / 360 的结构一致：`<root>/<Default|Profile N>/Network/Cookies`。
    老版本在 `<profile>/Cookies`。两个都找。
    """
    out: list[str] = []
    if not os.path.isdir(root):
        return out
    try:
        profiles = sorted(os.listdir(root))
    except OSError:
        return out
    for name in profiles:
        sub = os.path.join(root, name)
        if not os.path.isdir(sub):
            continue
        for rel in (("Network", "Cookies"), ("Cookies",)):
            path = os.path.join(sub, *rel)
            if os.path.isfile(path) and path not in out:
                out.append(path)
    return out


def browser_cookie_dbs() -> list[tuple[str, str]]:
    """**浏览器**里的 wps/kdocs 登录态 → `[(浏览器名, Cookies 路径)]`。

    这是「授权登录」最省事的一条路：客户端里没有登录态，但用户浏览器里
    往往已经登着网页版。登录一次网页版，点「扫描本机」就能直接抓 `wps_sid`，
    不用去 DevTools 手抄 Cookie。

    只读**本机当前用户**的目录（DPAPI 只能解当前用户加密的 blob）。
    """
    local = os.getenv("LOCALAPPDATA") or ""
    roaming = os.getenv("APPDATA") or ""
    candidates: list[tuple[str, str]] = [
        ("Chrome", os.path.join(local, "Google", "Chrome", "User Data")),
        ("Edge", os.path.join(local, "Microsoft", "Edge", "User Data")),
        ("360极速", os.path.join(local, "360Chrome", "Chrome", "User Data")),
        ("360安全", os.path.join(roaming, "360se6", "User Data")),
        ("Brave", os.path.join(local, "BraveSoftware", "Brave-Browser", "User Data")),
        ("QQ浏览器", os.path.join(local, "Tencent", "QQBrowser", "User Data")),
        ("Vivaldi", os.path.join(local, "Vivaldi", "User Data")),
        ("Chromium", os.path.join(local, "Chromium", "User Data")),
    ]
    out: list[tuple[str, str]] = []
    for label, root in candidates:
        for path in _chromium_profile_dbs(root):
            out.append((label, path))
    return out


def cookie_dbs() -> list[str]:
    """Cookie 库候选：客户端本体 + 浏览器里已登录的网页版。"""
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
    # 浏览器里已登录的网页版同样可读（Chrome/Edge/360 等 Chromium 系）
    for _label, path in browser_cookie_dbs():
        if path not in found:
            found.append(path)
    return found


def _source_of(path: str) -> str:
    """这条 Cookies 库来自哪里（给扫描结果做来源标注）。"""
    lowered = path.replace("\\", "/").lower()
    if "/wps 灵犀/" in lowered or "/lingxi/" in lowered:
        return "灵犀客户端"
    marks = (("360chrome", "360极速"), ("360se6", "360安全"),
             ("qqbrowser", "QQ浏览器"), ("chrome", "Chrome"),
             ("edge", "Edge"), ("brave", "Brave"), ("vivaldi", "Vivaldi"),
             ("chromium", "Chromium"))
    for key, label in marks:
        if key in lowered:
            return label
    return "本机"


def _read_cookies_safe(path: str) -> dict:
    """读 Cookies 库；被浏览器占用时先复制到临时文件再读。

    Chrome/Edge 运行时对 `Cookies` 持独占写锁，直接 open 会
    `database is locked`。复制一份（含 -wal/-shm）是标准做法。
    """
    import shutil
    import tempfile

    try:
        return _read_cookies(path)
    except Exception as exc:  # noqa: BLE001 - 锁或权限问题，走复制
        logger.debug("灵犀直读 Cookies 失败（%s），改为复制后读取", exc)
    tmp_dir = tempfile.mkdtemp(prefix="lingxi-cookies-")
    target = os.path.join(tmp_dir, "Cookies")
    try:
        shutil.copyfile(path, target)
        for suffix in ("-wal", "-shm"):
            src = path + suffix
            if os.path.isfile(src):
                shutil.copyfile(src, target + suffix)
        return _read_cookies(target)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


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
    """扫描本机灵犀客户端登录态（**不返回 Cookie 明文**）。

    返回字段按控制台的通用扫描渲染对齐（`showScanResult`）：
    顶层 `can_import`，每项 `uid` / `name` / `has_plain_credential` / `path` /
    `realm`。少了 `can_import` 就不会出现「导入这些凭证」按钮，
    少了 `has_plain_credential` 行首就没有勾选框 —— 表现都是「啥也不能干」。
    """
    items: list[dict] = []
    errors: list[str] = []
    dbs = cookie_dbs()
    if not dbs:
        dirs = client_dirs()
        hint = ("没有找到 WPS 灵犀客户端数据目录"
                if not dirs else
                "找到客户端目录但没有 Cookies 库（%s），先在灵犀客户端登录一次"
                % "、".join(dirs))
        browsers = [label for label, _path in browser_cookie_dbs()]
        hint += ("；也可以直接在 Chrome / Edge 打开 https://lingxi.wps.cn 登录网页版，"
                 "再点一次「扫描本机」——会自动读取浏览器里的 wps_sid，不用手抄 Cookie"
                 if browsers else
                 "；也可以粘贴 Cookie 导入")
        return {"items": [], "errors": [], "dbs": 0, "can_import": False,
                "hint": hint}
    for path in dbs:
        try:
            values = _read_cookies_safe(path)
        except Exception as exc:  # noqa: BLE001
            errors.append("%s: %s" % (path, str(exc)[:160]))
            continue
        sid = values.get("wps_sid") or ""
        if len(sid) < 16:
            continue
        uid = values.get("uid") or ""
        items.append({
            "path": path,
            "host": values.get("_host") or "",
            "uid": uid,
            "name": ("wps-" + uid) if uid else "灵犀客户端登录态",
            "realm": "cn",
            "sid_tail": sid[-4:],
            "has_sid": True,
            # 控制台的勾选框与「导入这些凭证」按钮都看这两个字段
            "has_plain_credential": True,
            "can_import": True,
            "usable": True,
            "note": "来自%s 的 Cookies" % _source_of(path),
            "source": _source_of(path),
        })
    if not items:
        # 找到了 Cookies 库但没有 wps_sid：说明浏览器/客户端还没登录过网页版。
        # 这条提示很关键 —— 否则用户只看到「0 条」，以为扫描坏了。
        return {"items": [], "errors": errors, "dbs": len(dbs),
                "can_import": False,
                "hint": "找到 %d 个 Cookies 库，但里面没有 wps_sid："
                        "先在 Chrome / Edge 打开 https://lingxi.wps.cn 登录一次，"
                        "或登录灵犀客户端，再点「扫描本机」" % len(dbs)}
    return {"items": items, "errors": errors, "dbs": len(dbs),
            "can_import": bool(items)}


def import_local(db: Session, *, probe: bool = True,
                 uids: list[str] | None = None) -> dict:
    """把扫描到的本机登录态导入账号池（可按 uids 只导勾选的）。"""
    if not cookie_dbs():
        return {"ok": False, "imported": 0,
                "reason": "没有找到 WPS 灵犀客户端 Cookie 库",
                "hint": "先在灵犀客户端登录，或改用「粘贴 Cookie」导入"}
    wanted = {str(item).strip() for item in (uids or []) if str(item).strip()}
    imported = 0
    failed: list[str] = []
    scanned = scan_local().get("items") or []
    if wanted:
        scanned = [item for item in scanned
                   if str(item.get("uid") or "") in wanted]
        if not scanned:
            return {"ok": False, "imported": 0,
                    "reason": "本机凭证不在勾选范围内，未导入"}
    for item in scanned:
        try:
            values = _read_cookies_safe(item["path"])
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