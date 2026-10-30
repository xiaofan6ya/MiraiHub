"""Trae 账号服务：扫描本机、导入、刷新、额度快照。

**复用系统能力，而不是另起一套**：本服务只负责「Trae 的凭证长什么样、
从哪里读」，落库仍然写系统统一的 `accounts` 表（`platform='trae'`），
额度仍然写统一的 `balance_total/balance_remain` 与 `credits_*` 列 ——
这样「积分」「用量」「日志」「Key 配额」四个模块无需任何改动就直接生效。

本机扫描路径（与官方客户端一致）：

    %APPDATA%/Trae/User/globalStorage/storage.json      国际版
    %APPDATA%/Trae CN/User/globalStorage/storage.json   国内版
    ~/.trae/trae-jwt-token                              国际版临时 token 兜底
    ~/.trae-cn/trae-jwt-token                           国内版临时 token 兜底

storage.json 里的 `iCubeAuthInfo://icube.cloudide` 是加密的，用 `core/icube`
解开；`iCubeAuthInfo://icube-dc:<holder>` 是该账号登录设备的证明私钥，
解出 holderId 后写进账号的 `device_id`——国际版缺了它会被上游 4015。
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime

from sqlalchemy.orm import Session

from admin.models import Account
from admin.platform.trae.core import client, icube

logger = logging.getLogger("admin.platform.trae.accounts")

PLATFORM = "trae"

#: 桌面端 storage.json 的候选目录：(路径, realm)。
def storage_paths() -> list[dict]:
    entries = []
    extra = os.getenv("TRAE_DESKTOP_DIRS") or ""
    roots: list[tuple[str, str]] = []
    for part in extra.split(os.pathsep):
        part = part.strip()
        if part:
            roots.append((part, ""))
    if not roots:
        appdata = (os.environ.get("APPDATA")
                   or os.path.join(os.path.expanduser("~"), "AppData", "Roaming"))
        roots = [(os.path.join(appdata, "Trae"), "intl"),
                 (os.path.join(appdata, "Trae CN"), "cn")]
    for root, hint in roots:
        storage = os.path.join(root, "User", "globalStorage", "storage.json")
        if not os.path.isfile(storage):
            continue
        realm = hint
        if not realm:
            low = root.lower()
            realm = "cn" if ("trae cn" in low or low.endswith("trae-cn")) else "intl"
        entries.append({"path": storage, "realm": realm})
    return entries


#: 临时 token（只有 IDE 数据面可用，7 天）
def temp_token_paths() -> list[dict]:
    home = os.path.expanduser("~")
    out = []
    for directory, realm in ((".trae-cn", "cn"), (".trae", "intl")):
        path = os.path.join(home, directory, "trae-jwt-token")
        if os.path.isfile(path):
            out.append({"path": path, "realm": realm})
    return out


def _read_json(path: str) -> dict:
    with open(path, encoding="utf-8-sig") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}


def _realm_from_body(body: dict, hint: str) -> str:
    region = body.get("userRegion") if isinstance(body.get("userRegion"), dict) else {}
    code = str(region.get("region") or region.get("_aiRegion") or "").upper()
    if code == "CN":
        return "cn"
    if code:
        return "intl"
    host = str(body.get("host") or "")
    if "trae.com.cn" in host or "mchost.guru" in host:
        return "cn"
    inner = body.get("account") if isinstance(body.get("account"), dict) else {}
    if str(inner.get("storeCountryCode") or "").lower() == "cn":
        return "cn"
    return hint or "intl"


def _mask(text: str, keep: int = 4) -> str:
    raw = str(text or "")
    if len(raw) <= keep * 2:
        return "*" * len(raw)
    return "%s***%s" % (raw[:keep], raw[-keep:])


def discover(*, include_temp: bool = True) -> dict:
    """扫描本机 Trae 登录态，返回**不含 token** 的清单（供控制台展示）。

    一次返回两个区的结果：`items` 里每条带 realm，前端按平台切换条过滤。
    """
    items: list[dict] = []
    errors: list[dict] = []
    seen: set[tuple] = set()
    for entry in storage_paths():
        try:
            storage = _read_json(entry["path"])
        except Exception as exc:  # noqa: BLE001 - 单个文件坏不影响另一个区
            errors.append({"path": entry["path"], "error": str(exc)})
            continue
        for key, value in storage.items():
            if not isinstance(key, str) or not key.startswith("iCubeAuthInfo://"):
                continue
            if not key.endswith("icube.cloudide"):
                continue
            try:
                body = value if isinstance(value, dict) else icube.decrypt_blob(value)
            except Exception as exc:  # noqa: BLE001
                errors.append({"path": entry["path"], "key": key, "error": str(exc)})
                continue
            if not isinstance(body, dict) or not client.token_of(body):
                continue
            realm = _realm_from_body(body, entry["realm"])
            uid = client.uid_of(body)
            dedupe = (realm, uid or client.token_of(body)[-8:])
            if dedupe in seen:
                continue
            seen.add(dedupe)
            inner = body.get("account") if isinstance(body.get("account"), dict) else {}
            device = _device_index().get((realm, entry["path"])) or {}
            items.append({
                "realm": realm,
                "uid": uid,
                "name": str(inner.get("username") or inner.get("email") or uid or "-"),
                "email": _mask(inner.get("email") or "", 2),
                "region": str((body.get("userRegion") or {}).get("region") or ""),
                "host": str(body.get("host") or ""),
                "expires_at": client.expiry_of(body),
                "has_refresh_token": bool(body.get("refreshToken") or body.get("refresh_token")),
                "device_id": device.get("holderId") or "",
                "source": entry["path"],
                "kind": "desktop",
            })
    if include_temp:
        for entry in temp_token_paths():
            existing = {item["realm"] for item in items}
            if entry["realm"] in existing:
                continue
            try:
                with open(entry["path"], encoding="utf-8") as handle:
                    text = handle.read().strip()
            except OSError as exc:
                errors.append({"path": entry["path"], "error": str(exc)})
                continue
            token = text
            if text.startswith("{"):
                try:
                    parsed = json.loads(text)
                except ValueError:
                    parsed = {}
                token = str(parsed.get("token") or parsed.get("accessToken") or "")
            if not token:
                continue
            items.append({
                "realm": entry["realm"], "uid": "", "name": "临时 token（仅对话）",
                "email": "", "region": "", "host": "", "expires_at": 0,
                "has_refresh_token": False, "device_id": "",
                "source": entry["path"], "kind": "temp-token",
            })
    items.sort(key=lambda item: (0 if item["realm"] == "cn" else 1, item["name"]))
    return {"dirs": [e["path"] for e in storage_paths()], "items": items, "errors": errors}


def _device_index() -> dict:
    """(realm, source) -> icube-dc 设备条目（holderId）。

    发现页只需要展示「有没有可信设备号」，因此这里只暴露 holderId，
    私钥留在 `_device_credentials()` 里，不进任何对外结构。
    """
    index: dict[tuple, dict] = {}
    for device in _device_credentials():
        index.setdefault((device["realm"], device["source"]),
                         {"holderId": device["holderId"]})
    return index


def _device_credentials() -> list[dict]:
    """解析 storage.json 里的 icube-dc 设备证明（国际版刷新 token 需要）。"""
    devices = []
    seen = set()
    for entry in storage_paths():
        try:
            storage = _read_json(entry["path"])
        except Exception:
            continue
        for key, value in storage.items():
            if not isinstance(key, str) or not key.startswith("iCubeAuthInfo://icube-dc"):
                continue
            holder = key.split(":", 2)[-1]
            try:
                body = value if isinstance(value, dict) else icube.decrypt_blob(value)
            except Exception:
                continue
            pem = body.get("privateKeyPEM") if isinstance(body, dict) else None
            if not pem:
                continue
            if (entry["realm"], holder) in seen:
                continue
            seen.add((entry["realm"], holder))
            devices.append({"realm": entry["realm"], "holderId": holder,
                            "privateKeyPEM": pem, "source": entry["path"]})
    return devices


def collect_accounts() -> list[dict]:
    """返回可导入的**完整**账号数据（含 token，仅服务端使用）。"""
    out: list[dict] = []
    seen: set[tuple] = set()
    now = datetime.utcnow().isoformat() + "Z"
    devices = _device_credentials()
    for entry in storage_paths():
        try:
            storage = _read_json(entry["path"])
        except Exception:
            continue
        for key, value in storage.items():
            if not isinstance(key, str) or not key.startswith("iCubeAuthInfo://"):
                continue
            if not key.endswith("icube.cloudide"):
                continue
            try:
                body = value if isinstance(value, dict) else icube.decrypt_blob(value)
            except Exception:
                continue
            if not isinstance(body, dict) or not client.token_of(body):
                continue
            realm = _realm_from_body(body, entry["realm"])
            uid = client.uid_of(body)
            dedupe = (realm, uid or client.token_of(body)[-8:])
            if dedupe in seen:
                continue
            seen.add(dedupe)
            record = dict(body)
            record["realm"] = realm
            record["uid"] = uid
            record["source"] = entry["path"]
            record["imported_at"] = now
            # 设备证明：把与账号同源的 holderId 记进 device_id，国际版缺它会 4015。
            device = next((d for d in devices
                           if d["realm"] == realm and d["source"] == entry["path"]), None)
            if device:
                record["device_id"] = device["holderId"]
                record["device_private_key"] = device["privateKeyPEM"]
            out.append(record)
    for entry in temp_token_paths():
        if any(rec["realm"] == entry["realm"] for rec in out):
            continue
        try:
            with open(entry["path"], encoding="utf-8") as handle:
                text = handle.read().strip()
        except OSError:
            continue
        token = text
        if text.startswith("{"):
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = {}
            token = str(parsed.get("token") or parsed.get("accessToken") or "")
        if not token:
            continue
        out.append({"token": token, "realm": entry["realm"], "uid": "",
                    "host": client.REALMS[entry["realm"]]["account"],
                    "source": entry["path"], "temp_token": True,
                    "imported_at": now})
    out.sort(key=lambda rec: (0 if rec["realm"] == "cn" else 1, str(rec.get("uid") or "")))
    return out


# ---------------------------------------------------------------------------
# 落库
# ---------------------------------------------------------------------------
def _display_name(record: dict) -> str:
    inner = record.get("account") if isinstance(record.get("account"), dict) else {}
    for key in ("username", "email"):
        value = str(inner.get(key) or "").strip()
        if value:
            return value
    uid = client.uid_of(record)
    if uid:
        return "Trae %s" % uid[-6:]
    return "Trae %s" % client.normalize_realm(record.get("realm")).upper()


def upsert(db: Session, record: dict) -> tuple[Account, bool]:
    """按 (platform, realm, uid) 幂等写入号池。返回 (账号, 是否新建)。

    已存在时只更新凭证（token 会轮换），不动运营字段（状态/优先级/绑定），
    否则每次「导入本机」都会把人工调过的配置清掉。
    """
    realm = client.normalize_realm(record.get("realm"))
    uid = client.uid_of(record)
    payload = dict(record)
    payload["realm"] = realm
    payload["uid"] = uid
    # 设备私钥只用于本地签名，落到 auth_json 里供刷新时使用；
    # 控制台只展示 holderId（见 public_view）。
    query = db.query(Account).filter(Account.platform == PLATFORM)
    if uid:
        account = query.filter(Account.uid == uid).first()
        if account is None:
            # 老数据没记 platform 时按 workbuddy 存着，这里不做跨平台认领：
            # 同一 uid 在两个平台是两套凭证，混用会污染号池。
            account = query.filter(Account.name == _display_name(payload)).first()
    else:
        account = None
    created = False
    if account is None:
        account = Account(platform=PLATFORM, status="active")
        account.balance_total = 0
        account.balance_remain = 0
        created = True
    account.platform = PLATFORM
    account.name = _display_name(payload)
    account.uid = uid
    account.domain = realm
    account.auth_json = json.dumps(payload, ensure_ascii=False)
    if created:
        db.add(account)
    db.commit()
    db.refresh(account)
    return account, created


def import_local(db: Session) -> dict:
    """扫描本机并导入号池；返回 added / updated / errors。"""
    records = collect_accounts()
    if not records:
        return {"added": 0, "updated": 0, "errors": [
            {"error": "未在本机找到 Trae 登录态（%s）"
                      % (", ".join(e["path"] for e in storage_paths()) or "无候选目录")}]}
    added = updated = 0
    errors: list[dict] = []
    for record in records:
        try:
            _account, created = upsert(db, record)
            added += 1 if created else 0
            updated += 0 if created else 1
        except Exception as exc:  # noqa: BLE001 - 单个号失败不影响其余
            errors.append({"uid": str(record.get("uid") or ""), "error": str(exc)})
    return {"added": added, "updated": updated, "errors": errors}


def import_record(db: Session, record: dict) -> tuple[Account, bool]:
    """导入一条外部凭证（手工粘贴 refresh token / 备份文件）。"""
    if not client.token_of(record) and not (record.get("refresh_token")
                                            or record.get("refreshToken")):
        raise ValueError("凭证缺少 token 或 refresh_token")
    record.setdefault("realm", client.realm_of(record))
    return upsert(db, record)


# ---------------------------------------------------------------------------
# 刷新 / 额度
# ---------------------------------------------------------------------------
def refresh(db: Session, account: Account, *, force: bool = False) -> dict:
    """刷新账号凭证并把新 token 原子落库。

    上游每次刷新都会轮换 refresh token，旧的立即失效 —— 因此必须落库，
    否则下次刷新直接失败（这是「刷新一次就废号」的典型成因）。
    """
    data = json.loads(account.auth_json or "{}")
    if not isinstance(data, dict):
        data = {}
    if client.is_expired(data) or force or not client.token_of(data):
        try:
            fresh = client.refresh_account(data)
        except client.UpstreamError as exc:
            account.last_err_msg = str(exc.message)[:255]
            account.last_err_at = datetime.utcnow()
            db.commit()
            raise
        data.update(fresh)
        data["realm"] = client.normalize_realm(data.get("realm"))
        account.auth_json = json.dumps(data, ensure_ascii=False)
        db.commit()
    quota = sync_quota(db, account)
    return {"refreshed": True, "quota": quota}


def sync_quota(db: Session, account: Account) -> dict:
    """拉取权益/额度并写回统一额度列（积分模块直接复用）。

    Trae 没有 WorkBuddy 的积分包结构，因此把「剩余 / 总量」映射到
    `balance_remain / balance_total`；`credits_*` 到期口径保持为 0
    （Trae 额度按月重置，没有逐包到期日），避免污染「积分到期」面板。
    """
    data = json.loads(account.auth_json or "{}")
    realm = client.normalize_realm(data.get("realm"))
    out = {"realm": realm, "total": None, "remain": None, "plan": "", "errors": []}
    try:
        ent = client.fetch_entitlement(data, realm)
    except client.UpstreamError as exc:
        out["errors"].append(exc.to_dict())
        return out
    out["errors"] = ent.get("errors") or []
    usage = ent.get("ent_usage") or {}
    if isinstance(usage, dict):
        detail = quota_detail(usage)
        basis = detail.get("basis") or []
        # 额度单位（决定前端怎么显示，别把美元当积分）：
        #   usd     -> 国际版付费/免费额度，有 $ 金额（basic_usage_limit）
        #   percent -> 国际版只有请求次数限额，没有金额
        #   credit  -> 国内版积分 + 倍率
        if "usd" in basis:
            unit = "usd"
        elif basis:
            unit = "percent"
        elif usage.get("is_dollar_usage_billing"):
            unit = "percent"
        else:
            unit = "credit"
        out.update({"total": detail["total"], "remain": detail["remain"],
                    "used": detail["used"], "limits": detail["limits"],
                    "basis": basis,
                    "plan": _plan_label(usage),
                    "unit": unit,
                    # 美元口径的原始金额（人类可读），前端直接用
                    "usd_total": detail.get("usd_total"),
                    "usd_remain": detail.get("usd_remain"),
                    "usd_used": detail.get("usd_used")})
    if out["remain"] is not None:
        account.balance_total = int(out["total"] if out["total"] is not None
                                    else out["remain"])
        account.balance_remain = int(out["remain"])
        account.last_sync_at = datetime.utcnow()
        account.credits_synced_at = datetime.utcnow()
        account.credits_snapshot = json.dumps(
            {"realm": realm, "plan": out["plan"], "total": out["total"],
             "remain": out["remain"], "used": out["used"], "limits": out["limits"],
             "unit": out.get("unit", "credit"),
             # 美元口径的原始金额：`total/remain` 存的是美分（整数，便于
             # 排序与比较），前端要显示 "$0.65 / $1.00" 时用这三个字段。
             "usd_total": out.get("usd_total"),
             "usd_remain": out.get("usd_remain"),
             "usd_used": out.get("usd_used"),
             "raw": _trim(usage)},
            ensure_ascii=False)[:20000]
        db.commit()
    return out


def _trim(payload, depth: int = 0):
    """额度原始响应裁剪，避免 credits_snapshot 被超大 JSON 撑爆。"""
    if depth > 3:
        return "..."
    if isinstance(payload, dict):
        return {k: _trim(v, depth + 1) for k, v in list(payload.items())[:30]}
    if isinstance(payload, (list, tuple)):
        return [_trim(v, depth + 1) for v in list(payload)[:10]]
    if isinstance(payload, (str, int, float, bool)) or payload is None:
        return payload
    return str(payload)[:200]


_QUOTA_REMAIN_KEYS = ("remain_credit", "remainCredit", "remaining_credit",
                      "left_credit", "remain", "credit_remain", "available_credit",
                      "surplus_credit")
_QUOTA_TOTAL_KEYS = ("total_amount", "total_credit", "totalCredit", "total",
                     "credit_total", "limit_credit", "monthly_credit",
                     "credits_limit")
_QUOTA_USED_KEYS = ("consumed_amount", "used_credit", "usedCredit", "consume",
                    "consumed", "usage_amount")


def _walk_dicts(node, depth: int = 0, out: list | None = None) -> list[dict]:
    """收集一棵 JSON 里所有 dict（限深），供「字段可能在哪一层都认」用。"""
    out = out if out is not None else []
    if not isinstance(node, dict) or depth > 4:
        return out
    out.append(node)
    for value in node.values():
        if isinstance(value, dict):
            _walk_dicts(value, depth + 1, out)
        elif isinstance(value, list):
            for item in value[:20]:
                if isinstance(item, dict):
                    _walk_dicts(item, depth + 1, out)
    return out


def quota_detail(payload: dict) -> dict:
    """把权益响应翻成统一额度结构。

    两个区结构不同，实测（2026-10-27，真实账号）：

      国内版  ent_usage.usage_summary = {
                  "consumed_amount": 9.22,      # 已用
                  "consumption_ratio": 0.0154,
                  "total_amount": 600,          # 总量
              }

      国际版  ent_usage 没有 usage_summary，免费计划是**按请求次数**限额：
              user_entitlement_pack_list[].quota = {
                  "premium_model_fast_request_limit": 10,
                  "premium_model_slow_request_limit": 50,
                  "advanced_model_request_limit": 1000, "credits_limit": 0, ...
              }

    因此「积分」口径只在国内版有意义；国际版把这些限额原样带出去展示，
    不硬凑成一个数字 —— 编造额度会直接导致选号与用量报表说谎。

    解析不做固定路径：上游随时会加一层包装，写死路径等于下次改版就归零。
    """
    nodes = _walk_dicts(payload) if isinstance(payload, dict) else []

    def _num(keys) -> float | None:
        for source in nodes:
            for key in keys:
                value = source.get(key)
                if isinstance(value, bool):
                    continue
                if isinstance(value, (int, float)):
                    return float(value)
                if isinstance(value, str):
                    try:
                        return float(value.strip())
                    except ValueError:
                        continue
        return None

    total = _num(_QUOTA_TOTAL_KEYS)
    used = _num(_QUOTA_USED_KEYS)
    remain = _num(_QUOTA_REMAIN_KEYS)

    # 「总量 - 已用」是唯一可靠的算法；两个字段同时存在时优先它，
    # 避免 `credits_amount` 这类语义随区域漂移的字段把剩余算反。
    if total is not None and used is not None:
        remain = max(0.0, total - used)
    elif remain is None and total is not None and used is None:
        remain = total

    limits = {}
    for key in ("premium_model_fast_request_limit", "premium_model_slow_request_limit",
                "advanced_model_request_limit", "auto_completion_limit",
                "solo_agent_parallel_limit"):
        value = None
        for source in nodes:
            if key in source and isinstance(source[key], (int, float)):
                value = source[key]
                break
        if value is not None:
            limits[key] = value

    # `total_amount=0` 且没有已用量的情况，语义是「这个区没有积分口径」
    # （国际版免费计划按请求次数限额），不能报成「剩余 0 分」。
    if total == 0 and used is None:
        total = used = remain = None

    # 国际版：basic_usage_amount 是「基础额度已用量」，额度上限在
    # basic_usage_limit(=1) —— 单位是「份」不是 token，因此只在没有积分口径
    # 时把它当成 0~100% 的额度水位，供 `/v1/balance` 与选号排序参考。
    credits_only = set()
    basic_used = None
    basic_limit = None
    for source in nodes:
        if "basic_usage_amount" in source:
            value = source.get("basic_usage_amount")
            if isinstance(value, (int, float)):
                basic_used = float(value)
        if "basic_usage_limit" in source:
            value = source.get("basic_usage_limit")
            if isinstance(value, (int, float)):
                basic_limit = float(value)
    # 国际版的美元额度。
    #
    # ⚠️ **两个字段单位不同**（实测 2026-10-28，真实账号）：
    #     usage.basic_usage_amount = 0.00066   -> 这是 milli 美元 = $0.66
    #     quota.basic_usage_limit  = 1         -> 这是美元      = $1.00
    #   网页端显示 "$0.65 / $1.00"，若把 amount 当美元算就是 $0.00066，
    #   与网页差 1000 倍 —— 界面上永远显示「已用 $0.00」。
    #   因此 amount 必须 ×1000 换算成美元。
    USD_MILLI = 1000.0
    usd_total = None
    usd_remain = None
    usd_used = None
    if total is None and basic_limit is not None and basic_limit > 0:
        limit_usd = float(basic_limit)                 # 已是美元
        used_usd = float(basic_used or 0.0) * USD_MILLI  # milli -> 美元
        used_usd = min(used_usd, limit_usd)             # 防上游给超
        if limit_usd > 0:
            credits_only.add("usd")
            usd_total = round(limit_usd, 6)
            usd_used = round(used_usd, 6)
            usd_remain = round(max(0.0, limit_usd - used_usd), 6)

    if usd_total is not None:
        # 美元口径：total/remain 用**美分整数**（便于排序与比较），
        # 原始金额另存 usd_total / usd_remain / usd_used 供界面显示。
        total = int(round(usd_total * 100))
        remain = int(round((usd_remain or 0) * 100))
    elif total is None and basic_limit is not None:
        credits_only.add("basic")
        pct = 0.0
        if basic_limit > 0 and basic_used is not None:
            pct = max(0.0, min(1.0, basic_used / basic_limit))
        # 用 0~100 表示剩余百分比：既保留「有额度水位可排序」的能力，
        # 又不会假装这是 token/积分数量。
        total = 100
        remain = int(round(100 * (1.0 - pct)))

    return {
        "total": int(round(total)) if total is not None else None,
        "remain": int(round(remain)) if remain is not None else None,
        "used": used,
        "limits": limits,
        #: 空 = 积分口径；`usd` = 美元额度；`basic` = 请求次数额度
        "basis": sorted(credits_only),
        #: 美元口径的原始值（人类可读，前端直接显示 "$0.66 / $1.00"）
        "usd_total": usd_total,
        "usd_remain": usd_remain,
        "usd_used": usd_used,
    }


def _extract_quota(payload: dict) -> tuple[int | None, int | None]:
    """兼容旧调用方：返回 (剩余, 总量)。"""
    detail = quota_detail(payload)
    return detail["remain"], detail["total"]


def _plan_label(payload: dict) -> str:
    for source in (payload, payload.get("data") if isinstance(payload.get("data"), dict) else {}):
        if not isinstance(source, dict):
            continue
        for key in ("plan", "plan_name", "planName", "product", "sku", "level"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:60]
    return ""


def expiry_epoch(account: Account) -> float:
    try:
        data = json.loads(account.auth_json or "{}")
    except ValueError:
        return 0.0
    return client.expiry_of(data) if isinstance(data, dict) else 0.0


def public_view(account: Account) -> dict:
    """控制台展示用：绝不含 token / 私钥。"""
    try:
        data = json.loads(account.auth_json or "{}")
    except ValueError:
        data = {}
    data = data if isinstance(data, dict) else {}
    inner = data.get("account") if isinstance(data.get("account"), dict) else {}
    return {
        "realm": client.normalize_realm(data.get("realm")),
        "uid": account.uid or "",
        "email": _mask(inner.get("email") or "", 2),
        "username": str(inner.get("username") or ""),
        "region": str((data.get("user_region")
                       or data.get("userRegion") or {}).get("region")
                      or (data.get("user_region") or {}).get("_aiRegion") or ""),
        "host": str(data.get("host") or ""),
        "device_id": str(data.get("device_id") or ""),
        "has_refresh_token": bool(data.get("refresh_token") or data.get("refreshToken")),
        "expires_at": int(client.expiry_of(data) or 0),
        "source": str(data.get("source") or ""),
        "imported_at": str(data.get("imported_at") or ""),
    }


def appdata_hint() -> str:
    return os.pathsep.join(e["path"] for e in storage_paths()) or "(未找到 Trae 桌面端)"