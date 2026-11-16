"""腾讯 Marvis 账号池：凭证落库 / 导入 / 额度同步。

## 凭证形态

一份 Marvis 登录态就是这几个字段（magpie 插件落盘的也是同一份）：

    {
      "openid":       "…",          # 身份，必需
      "access_token": "…",          # 必需
      "refresh_token": "…",         # 可选
      "guid":         "…",          # 设备标识，必需 —— 缺了上游一律 -109
      "login_type":   "1" | "2",    # 1=QQ，2=微信
      "nickname":     "…",          # 卡片上显示的名字
      "avatar":       "…"           # 可选
    }

`uid` 用 `openid`（唯一且稳定）；`accountId`（展示名）用昵称，取不到就退手机号
尾号以外的兜底 —— 不拿内部 ID 冒充名字。

## `guid` 为什么是必需项

上游每个请求都要 `Ual-Access-Guid`，缺了直接 -109 参数无效。它是**设备**标识，
不是账号标识：同一个号在不同设备上 guid 不同也能用，但每个号必须有一个。
导入时没带 guid 的凭证会**补一个**并写回库 —— 否则这份凭证永远换不出登录态。
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from admin.models import Account
from admin.platform.marvis.core import client, endpoints

logger = logging.getLogger("admin.platform.marvis.accounts")

PLATFORM = "marvis"

#: 额度快照的有效期（秒）。超过这个时间的号，列表页只返回旧快照，
#: 由后台线程去刷 —— 列表接口**绝不同步打上游**（50 个号能把接口拖到秒级）。
QUOTA_TTL = 300


def quota_is_fresh(account: Account) -> bool:
    """额度快照是否还在有效期内。"""
    synced = getattr(account, "credits_synced_at", None)
    if synced is None:
        return False
    return (datetime.utcnow() - synced).total_seconds() < QUOTA_TTL


def snapshot_of(account: Account) -> dict:
    """上一次的额度快照（列表接口只读它，不打上游）。"""
    try:
        snap = json.loads(getattr(account, "credits_snapshot", "") or "{}")
    except (ValueError, TypeError):
        snap = {}
    return snap if isinstance(snap, dict) else {}

#: 钱包没读到时，卡片显示「剩余额度未知」，路由不计入 —— 所以余额字段允许为空。
#: 一旦有读数，`balance_remain/total` 都写真实值。


def credentials_of(account: Account) -> dict:
    """从账号行里取出凭证字典。坏 JSON 当空 dict（不让一行坏数据打挂整池）。"""
    try:
        data = json.loads(account.auth_json or "{}")
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def token_of(data: dict) -> str:
    return str(data.get("access_token") or data.get("accessToken") or "")


def openid_of(data: dict) -> str:
    return str(data.get("openid") or data.get("openId") or "")


def guid_of(data: dict) -> str:
    return str(data.get("guid") or "")


def realm_of(account: Account) -> str:
    return (account.domain or "cn").strip() or "cn"


#: 凭证有效期（秒）——**实测推断值，不是上游下发的**。按登录方式分档。
#:
#: 为什么是推断而不是读字段：QQ / 微信登录回调**不下发 `expires_in`**，
#: `expires_at` 恒为 0。看不到真实值，只能按实测收敛。
#:
#: ## 两个档位来源不同，别混为一谈
#:
#: · `WX` 2 小时：**我自己真机测的**。2026-11-15，13:34 授权 → 15:44 上游回
#:   `4100403 登录态过期/未登录`，存活 2 小时 10 分；按 4 小时假设会晚 1 小时
#:   50 分，不成立。取 2 小时并留 10 分钟预警余量。
#: · `QC` 30 天：**依据 issue #41 报告人的实测**（QQ 授权 18:48 签发，4 小时
#:   29 分后调用仍 200），我没条件自己复现 QQ 那条链路。这个值只是
#:   「不会早于 30 天失效」的保守估计 —— 微信和 QQ 互联是两套授权体系，
#:   把微信的 2 小时套到 QQ 上会让 QQ 号每 2 小时就被标注「该重登了」，
#:   那是**误报**，比不提示更烦人。
#:
#: 若将来实测到更准的值，只改这张表即可（`token_ttl_seconds()` 是唯一读取点）。
TOKEN_TTL_SECONDS = 2 * 3600          # 兼容旧引用：默认（微信）档
TOKEN_TTL_BY_LOGIN = {
    "WX": 2 * 3600,                   # 微信：实测 2 小时 10 分失效
    "WXAPP": 2 * 3600,
    "QC": 30 * 24 * 3600,             # QQ 互联：据报 4.5 小时后仍有效，按 30 天保守估
}
#: 提前多久开始提示「该重登了」。
TOKEN_WARN_SECONDS = 10 * 60


def login_type_of(data: dict) -> str:
    """凭证里的登录方式（`WX` / `QC`）。历史数据可能是 `1`/`2`。"""
    raw = str((data or {}).get("login_type") or "").strip().upper()
    if raw in ("1", "QC", "QQ"):
        return "QC"
    if raw in ("2", "WX", "WXAPP"):
        return "WX"
    return raw or "WX"


def token_ttl_seconds(data: dict) -> int:
    """按登录方式取 TTL（唯一读取点，改档位只改表）。"""
    return TOKEN_TTL_BY_LOGIN.get(login_type_of(data), TOKEN_TTL_SECONDS)


def token_age_seconds(data: dict) -> int | None:
    """这份凭证签发到现在多少秒（拿不到 `issued_at` 返回 None）。"""
    raw = data.get("issued_at")
    try:
        issued = int(raw)
    except (TypeError, ValueError):
        return None
    if issued <= 0:
        return None
    return max(0, int(time.time()) - issued)


def token_expiry_hint(data: dict) -> dict:
    """凭证时效提示（给控制台账号卡与错误信息共用）。

    `state`：
      * `unknown`  —— 老凭证没有 `issued_at`（无法判断，不吓用户）；
      * `fresh`    —— 还早；
      * `soon`     —— 剩余不足 `TOKEN_WARN_SECONDS`，该准备重登了；
      * `expired`  —— 已超过推断 TTL，大概率上游已判失效。
    """
    ttl = token_ttl_seconds(data)
    age = token_age_seconds(data)
    if age is None:
        return {"state": "unknown", "age_seconds": None,
                "remaining_seconds": None, "ttl_seconds": ttl,
                "login_type": login_type_of(data)}
    remaining = ttl - age
    if remaining <= 0:
        state = "expired"
    elif remaining <= TOKEN_WARN_SECONDS:
        state = "soon"
    else:
        state = "fresh"
    return {"state": state, "age_seconds": age,
            "remaining_seconds": max(0, remaining),
            "ttl_seconds": ttl, "login_type": login_type_of(data)}


def public_view(account: Account, *, quota: dict | None = None,
                include_credential: bool = False) -> dict:
    """给控制台的账号视图。**默认不含 token**。

    `quota` 是调用方传入的额度快照（列表接口读缓存列，不在这里打上游）。
    """
    data = credentials_of(account)
    view = {
        "id": account.id,
        "platform": PLATFORM,
        "name": account.name or "",
        "uid": account.uid or "",
        "realm": realm_of(account),
        "status": account.status,
        "openid": openid_of(data),
        "login_type": str(data.get("login_type") or "1"),
        "has_guid": bool(guid_of(data)),
        "nickname": str(data.get("nickname") or ""),
        "avatar": str(data.get("avatar") or ""),
        "balance_total": account.balance_total,
        "balance_remain": account.balance_remain,
        "quota": quota if isinstance(quota, dict) else snapshot_of(account),
        "quota_fresh": quota_is_fresh(account),
        "last_sync_at": account.last_sync_at.isoformat()
        if getattr(account, "last_sync_at", None) else None,
        "last_err_msg": account.last_err_msg or "",
        # 凭证时效：QQ/微信 token 实测只活约 2 小时（上游不下发 expires_in），
        # 控制台据此提前亮「该重新授权了」，不必等一次 401 才发现。
        "token_expiry": token_expiry_hint(data),
    }
    if include_credential:
        view["credential"] = data
    return view


def accounts_for(db: Session, realm: str = "") -> list[Account]:
    query = db.query(Account).filter(Account.platform == PLATFORM)
    if realm:
        query = query.filter(Account.domain == realm)
    return query.order_by(Account.id.asc()).all()


def select(db: Session, realm: str = "", *, exclude_ids: set[int] | None = None):
    """挑一个可用账号：优先「有余额且最近没出错」的。

    排序口径（与网关选号一致的地方尽量对齐）：
      · `status='active'` 且不在冷却中；
      · 有余额读数的排在没有读数的前面（没读数 = 不知道还能不能用）；
      · `last_used_at` 早的先上，避免一个号被打爆。
    """
    now = datetime.utcnow()
    query = db.query(Account).filter(Account.platform == PLATFORM,
                                     Account.status == "active")
    if realm:
        query = query.filter(Account.domain == realm)
    rows = query.all()
    candidates = []
    for account in rows:
        if exclude_ids and account.id in exclude_ids:
            continue
        until = getattr(account, "cool_until", None)
        if until and until > now:
            continue
        has_balance = account.balance_remain is not None
        candidates.append((0 if has_balance else 1,
                           account.last_used_at or datetime.min,
                           account.id, account))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    return candidates[0][3]


def _upsert(db: Session, payload: dict) -> tuple[Account, bool]:
    """按 openid 幂等写入。返回 `(行, 是否新建)`。"""
    data = dict(payload)
    openid = openid_of(data)
    if not openid:
        raise ValueError("凭证缺少 openid")
    if not token_of(data):
        raise ValueError("凭证缺少 access_token")
    # 缺 guid 的凭证永远换不出登录态 —— 补一个并写回。
    if not guid_of(data):
        data["guid"] = str(uuid.uuid4())
    data.setdefault("login_type", "1")
    # **记下这份凭证是什么时候签发的**（2026-11-15）。
    #
    # 为什么必须记：QQ / 微信登录回调**不下发 `expires_in`**，所以
    # `expires_at` 一直是 0，系统对「这票还能活多久」一无所知 —— 只能等上游
    # 回 401 才发现过期。实测（第一份凭证 13:34 授权、15:44 失效）存活约
    # **2 小时**，比常见的 24 小时短一个数量级，所以「授权完能用、过一阵
    # 突然退登」是可以预判的。有了 `issued_at` 才能提前提示用户重登。
    data.setdefault("issued_at", int(time.time()))

    realm = str(data.get("realm") or "cn")
    uid = openid
    name = str(data.get("nickname") or "").strip() or ("Marvis " + openid[-6:])

    account = (db.query(Account)
               .filter(Account.platform == PLATFORM, Account.uid == uid)
               .first())
    created = account is None
    if created:
        account = Account(platform=PLATFORM, uid=uid, name=name,
                          domain=realm, status="active")
        db.add(account)
    account.name = name
    account.domain = realm
    account.auth_json = json.dumps(data, ensure_ascii=False)
    account.status = "active"
    account.session_dead_fails = 0
    account.consecutive_fails = 0
    account.cool_until = None
    account.cool_kind = ""
    account.last_err_msg = ""
    db.commit()
    db.refresh(account)
    return account, created


def record_from_payload(raw) -> dict:
    """把用户粘进来的东西归一成凭证 dict。

    接受：整段 JSON 对象 / `{"credential": {...}}` 包装 / magpie 落盘的同形态对象。
    """
    data = raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            raise ValueError("凭证为空")
        try:
            data = json.loads(text)
        except Exception as exc:  # noqa: BLE001
            raise ValueError("不是合法 JSON：%s" % exc) from exc
    if not isinstance(data, dict):
        raise ValueError("凭证必须是 JSON 对象")
    inner = data.get("credential") or data.get("auth") or data.get("metadata")
    if isinstance(inner, dict):
        merged = {**inner, **{k: v for k, v in data.items() if k in
                             ("openid", "access_token", "openId", "accessToken",
                              "refresh_token", "guid", "login_type", "nickname")}}
        data = merged
    return data


def import_record(db: Session, record: dict) -> tuple[Account, bool]:
    return _upsert(db, record_from_payload(record))


def import_batch(db: Session, text: str) -> dict:
    """批量导入：JSON 数组 / JSONL / 单个对象。

    与其它平台一致返回 `added/updated/failed/total/errors`，
    控制台据此显示「新增几个 / 更新几个 / 失败几条」。
    """
    raw = (text or "").strip().lstrip("\ufeff")
    if not raw:
        return {"ok": False, "added": 0, "updated": 0, "failed": 0, "total": 0,
                "errors": ["没有内容"]}

    records: list = []
    parsed = None
    try:
        parsed = json.loads(raw)
    except Exception:  # noqa: BLE001
        parsed = None
    if isinstance(parsed, list):
        records = parsed
    elif isinstance(parsed, dict):
        inner = parsed.get("accounts") or parsed.get("items")
        records = inner if isinstance(inner, list) else [parsed]
    else:
        for line in raw.splitlines():
            line = line.strip().rstrip(",")
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except Exception:  # noqa: BLE001
                continue

    added = updated = failed = 0
    errors: list[str] = []
    for index, record in enumerate(records, 1):
        try:
            _account, created = import_record(db, record)
            if created:
                added += 1
            else:
                updated += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            if len(errors) < 50:
                errors.append("第 %d 条：%s" % (index, str(exc)[:120]))

    return {"ok": not errors, "added": added, "updated": updated,
            "failed": failed, "total": len(records), "errors": errors}


def sync_quota(db: Session, account: Account) -> dict:
    """拉一次令牌钱包，写回余额字段。

    余额口径：`balance_remain = avail_tokens`（可用），`balance_total = total_tokens`。
    两个都写成**计费 token**，与上游语义一致。
    """
    data = credentials_of(account)
    realm = realm_of(account)
    try:
        wallet = client.fetch_wallet(data, realm, account_id=account.id)
    except client.UpstreamError as exc:
        account.last_err_at = datetime.utcnow()
        account.last_err_msg = exc.message[:240]
        account.last_upstream_error = json.dumps(exc.to_dict(), ensure_ascii=False)
        db.commit()
        raise

    total = _as_int(wallet.get("total_tokens"))
    used = _as_int(wallet.get("used_tokens"))
    avail = _as_int(wallet.get("avail_tokens"))
    if avail is None and total is not None and used is not None:
        avail = max(0, total - used)
    account.balance_total = total
    account.balance_remain = avail
    account.last_sync_at = datetime.utcnow()
    # 额度快照 + 采集时间：列表接口只读这两列（`quota_is_fresh` / `snapshot_of`），
    # 不打上游。缺了它们，列表页要么永远「额度未知」，要么每次刷新都同步打一遍。
    account.credits_snapshot = json.dumps({
        "total": total, "used": used, "avail": avail,
        "frozen": _as_int(wallet.get("frozen_tokens")),
        "unit": "tokens",
    }, ensure_ascii=False)
    account.credits_synced_at = datetime.utcnow()
    account.last_err_msg = ""
    account.last_err_at = None
    account.last_upstream_error = ""
    db.commit()
    return {"total": total, "used": used, "avail": avail,
            "frozen": _as_int(wallet.get("frozen_tokens")),
            "raw": wallet}


def _as_int(value):
    try:
        if value is None:
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def ensure_token(db: Session, account: Account) -> dict:
    """取一份可用凭证。

    Marvis 没有独立的 refresh 接口（续签要整份凭证），所以这里只做**完整性**
    校验：缺 openid / access_token / guid 的直接判失效，让上层换号或重新扫码，
    而不是发一个注定 -109 的请求出去。
    """
    data = credentials_of(account)
    if not openid_of(data) or not token_of(data):
        raise client.UpstreamError(
            "账号凭证不完整（缺 openid / access_token），请重新扫码登录",
            status=401, kind="auth")
    # 凭证按推断 TTL 已过期：**提前**给出可操作的提示，而不是发一个注定 401
    # 的请求再报「登录态过期」。仍然放行（只是提示）—— 推断值可能偏保守，
    # 真让上游判死才有说服力；这里只把话说清楚。
    hint = token_expiry_hint(data)
    if hint["state"] == "expired":
        logger.info("Marvis 凭证已超推断 TTL（%ss）acc=%s，本次仍尝试调用",
                    hint["age_seconds"], account.id)
    if not guid_of(data):
        # 历史数据可能没 guid：补一个并写回，否则这份凭证永远换不出登录态。
        data["guid"] = str(uuid.uuid4())
        account.auth_json = json.dumps(data, ensure_ascii=False)
        db.commit()
    return data


def summarize(views) -> dict:
    """汇总一组账号视图（`public_view` 的返回值列表）。

    接受的是**视图列表**而不是 db —— 因为 `MarvisAccountOps.list()` 已经
    把行读出来了，再传 db 进去就是二次查询。
    """
    rows = [v for v in (views or []) if isinstance(v, dict)]
    total = len(rows)
    active = sum(1 for v in rows if v.get("status") == "active")
    return {
        "accounts": total,
        "active": active,
        "disabled": total - active,
        "balance_total": sum(int(v.get("balance_total") or 0) for v in rows),
        "balance_remain": sum(int(v.get("balance_remain") or 0) for v in rows),
        "updated_at": time.time(),
    }


def summarize_db(db: Session, realm: str = "") -> dict:
    """按库直接汇总（路由层用；不依赖已构造好的视图）。"""
    return summarize([public_view(a) for a in accounts_for(db, realm)])