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
import threading
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

    ## 两个数据源，优先用真的那个

    · `expires_at`  —— 续签接口下发的绝对到期时间（**真值**，微信实测 7200s）；
    · `issued_at + TTL` —— 老凭证没有 `expires_at` 时的推断值（见 `TOKEN_TTL_BY_LOGIN`）。

    以前只有推断那条腿，所以「续签成功」也推进不了这个数字 —— 面板会一直
    停在登录时的 2 小时档上，用户看到的是「已过期却还能用」。有真值就必须优先。

    `state`：
      * `unknown`  —— 两个数据源都没有（不吓用户）；
      * `fresh`    —— 还早；
      * `soon`     —— 剩余不足 `TOKEN_WARN_SECONDS`，该准备重登了；
      * `expired`  —— 已过期，大概率上游已判失效。
    """
    ttl = token_ttl_seconds(data)
    expires = expires_at_of(data)
    if expires:
        remaining = expires - int(time.time())
        age = None
        if remaining <= 0:
            state = "expired"
        elif remaining <= TOKEN_WARN_SECONDS:
            state = "soon"
        else:
            state = "fresh"
        return {"state": state, "age_seconds": age,
                "remaining_seconds": max(0, remaining),
                "expires_at": expires, "source": "upstream",
                "ttl_seconds": ttl, "login_type": login_type_of(data),
                "refreshable": _refreshable_of(data),
                "needs_relogin": bool((data or {}).get("refresh_dead"))}

    age = token_age_seconds(data)
    if age is None:
        return {"state": "unknown", "age_seconds": None,
                "remaining_seconds": None, "expires_at": 0, "source": "unknown",
                "ttl_seconds": ttl, "login_type": login_type_of(data),
                "refreshable": _refreshable_of(data),
                "needs_relogin": bool((data or {}).get("refresh_dead"))}
    remaining = ttl - age
    if remaining <= 0:
        state = "expired"
    elif remaining <= TOKEN_WARN_SECONDS:
        state = "soon"
    else:
        state = "fresh"
    return {"state": state, "age_seconds": age,
            "remaining_seconds": max(0, remaining),
            "expires_at": 0, "source": "inferred",
            "ttl_seconds": ttl, "login_type": login_type_of(data),
            "refreshable": _refreshable_of(data),
                "needs_relogin": bool((data or {}).get("refresh_dead"))}


# ---------------------------------------------------------------------------
# 续签（2026-11-16 打通：`POST /marvis_client_login/marvis_refresh_token`）
# ---------------------------------------------------------------------------
#: 提前多久开始续签（秒）。取 15 分钟：比微信档的 2 小时短得多，
#: 既不会每次转发都去打一次续签接口，又能在真过期前把票换好。
REFRESH_AHEAD_SECONDS = 15 * 60

#: 续签失败后的退避（秒）。失败一次就每个请求再试一遍，等于拿号去撞上游；
#: 10 分钟内只认上一次的结果，到期再试。
REFRESH_FAIL_BACKOFF = 10 * 60

#: 同账号续签串行锁（进程内）。
#:
#: 为什么必须串行：`refresh_token` 是**一次性轮换**票 —— 同号并发两次续签，
#: 后拿到的那份会把先拿到的打掉，两边都失效。锁按 `account.id` 分，
#: 不同账号之间不互相排队。
_REFRESH_LOCKS: dict[int, threading.Lock] = {}
_REFRESH_LOCKS_GUARD = threading.Lock()


def _refresh_lock(account_id: int) -> threading.Lock:
    with _REFRESH_LOCKS_GUARD:
        lock = _REFRESH_LOCKS.get(int(account_id))
        if lock is None:
            lock = threading.Lock()
            _REFRESH_LOCKS[int(account_id)] = lock
        return lock


def refresh_token_of(data: dict) -> str:
    return str((data or {}).get("refresh_token")
               or (data or {}).get("refreshToken") or "")


def _refreshable_of(data: dict) -> bool:
    """这份凭证还能不能靠续签续命（有票，且没被判死）。"""
    return bool(refresh_token_of(data)) and not (data or {}).get("refresh_dead")


def expires_at_of(data: dict) -> int:
    """凭证的绝对到期时间（秒）。毫秒值自动降级成秒 —— 上游两种都给过。"""
    raw = (data or {}).get("expires_at") or (data or {}).get("expiresAt")
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        return 0
    if value > 10 ** 12:          # 毫秒
        value //= 1000
    return max(0, value)


def needs_refresh(data: dict, *, now: int | None = None) -> bool:
    """这份凭证该不该续签。

    没有 `refresh_token` 的一律 False —— 那种号只能重新扫码，白打接口没用。
    上次续签被判死（`refresh_dead`）的也不再试，直到重新导入凭证。
    """
    if not refresh_token_of(data):
        return False
    if (data or {}).get("refresh_dead"):
        return False
    ts = int(time.time()) if now is None else int(now)
    expires = expires_at_of(data)
    if expires:
        return ts >= expires - REFRESH_AHEAD_SECONDS
    age = token_age_seconds(data)
    if age is None:
        # 不知道什么时候签的（老凭证没记 issued_at）：先续一次，续完就有 expires_at。
        return True
    return age >= token_ttl_seconds(data) - REFRESH_AHEAD_SECONDS


def _refresh_backoff_active(data: dict) -> bool:
    try:
        failed_at = int((data or {}).get("refresh_failed_at") or 0)
    except (TypeError, ValueError):
        return False
    return failed_at > 0 and (int(time.time()) - failed_at) < REFRESH_FAIL_BACKOFF


def refresh_account(db: Session, account: Account) -> dict:
    """用 `refresh_token` 换一次新有效期，并写回 `auth_json`。

    返回 `{"ok", "reason", "fatal", "error", "expires_at"}`，**不抛**：
    转发链路不能因为续签失败就断掉 —— 旧票可能还能用，真死了让上游 401 说话。

    `fatal=True` 表示上游明确说凭证无效（`-107 / -101`）；其余失败（`-109`
    参数、`-7` 签名）是本端问题，**不把账号标死**。
    """
    account_id = int(account.id or 0)
    with _refresh_lock(account_id):
        try:
            db.refresh(account)     # 锁内二次读：并发里前一个可能刚续过
        except Exception:           # noqa: BLE001 - 非 session 对象 / 已过期
            pass
        data = credentials_of(account)
        try:
            grant = client.refresh_credentials(
                "", realm_of(account), account_id=account_id, credential=data)
        except client.UpstreamError as exc:
            data["refresh_failed_at"] = int(time.time())
            data["refresh_err"] = exc.message[:200]
            if exc.kind == "auth":
                # 上游明确说这张 refresh_token 不能用了 —— 别再每轮打一次接口，
                # 标死等重新扫码（导入新凭证时 `_upsert` 会整份覆盖，标记自然清）。
                data["refresh_dead"] = 1
            account.auth_json = json.dumps(data, ensure_ascii=False)
            account.last_err_at = datetime.utcnow()
            account.last_err_msg = exc.message[:240]
            db.commit()
            logger.info("Marvis 续签失败 acc=%s kind=%s：%s",
                        account_id, exc.kind, exc.message[:160])
            return {"ok": False, "reason": exc.message[:200],
                    "fatal": exc.kind == "auth", "error": exc, "expires_at": 0}
        except Exception as exc:    # noqa: BLE001 - 续签不能把转发打挂
            data["refresh_failed_at"] = int(time.time())
            data["refresh_err"] = str(exc)[:200]
            account.auth_json = json.dumps(data, ensure_ascii=False)
            db.commit()
            logger.warning("Marvis 续签异常 acc=%s", account_id, exc_info=True)
            return {"ok": False, "reason": str(exc)[:200], "fatal": False,
                    "error": exc, "expires_at": 0}

        fresh = dict(data)
        fresh["access_token"] = grant["access_token"]
        if grant.get("refresh_token"):
            # 空串不覆盖 —— 空串是「这次没给新值」
            fresh["refresh_token"] = grant["refresh_token"]
        if grant.get("expires_at"):
            fresh["expires_at"] = int(grant["expires_at"])
        fresh["issued_at"] = int(time.time())
        if grant.get("nickname"):
            fresh["nickname"] = grant["nickname"]
        if grant.get("avatar"):
            fresh["avatar"] = grant["avatar"]
        fresh.pop("refresh_failed_at", None)
        fresh.pop("refresh_err", None)
        fresh.pop("refresh_dead", None)
        account.auth_json = json.dumps(fresh, ensure_ascii=False)
        account.last_err_at = None
        account.last_err_msg = ""
        db.commit()
        logger.info("Marvis 续签成功 acc=%s expires_in=%ss",
                    account_id, grant.get("expires_in") or 0)
        return {"ok": True, "reason": "", "fatal": False, "error": None,
                "expires_at": int(fresh.get("expires_at") or 0),
                "expires_in": int(grant.get("expires_in") or 0)}


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
    # **重新授权 = 这是一个全新可用的号**（2026-11-16 用户实测踩坑）。
    #
    # 只清 `cool_until` 是不够的：额度类失败会把号写进 `degrade_until`
    # （默认 30 分钟）、风控类会写 `breaker_until`、被判死会打 `refresh_dead`。
    # 这三样只要还挂着，选号照样跳过它 —— 用户重新扫码成功后仍然 503
    # 「账号都在冷却或排队中」，而他明明刚授权完。
    #
    # 语义上这些都是「针对**旧凭证**的判断」，新凭证到手就该全部作废。
    account.degrade_until = None
    account.breaker_until = None
    account.err_count = 0
    for flag in ("refresh_dead", "dead", "session_dead"):
        if hasattr(account, flag):
            setattr(account, flag, 0)
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


def ensure_token(db: Session, account: Account, *, force: bool = False) -> dict:
    """取一份可用凭证 —— **临期会先续签**（`refresh_token` 换新有效期）。

    ## 为什么在这里续而不是等 401

    微信登录的 access_token 实测只活 **2 小时 10 分**（QQ 是 30 天量级）。
    不续签的话，扫码授权完过两小时，转发链路就必然撞一次
    `4100403 登录态过期` —— 用户看到的是「刚登的号又掉了」。
    续签窗口见 `REFRESH_AHEAD_SECONDS`（15 分钟），失败退避见
    `REFRESH_FAIL_BACKOFF`。

    ## 失败语义

    · `force=False`（转发链路 / 定时保活）：续签失败只记日志和 `last_err_msg`，
      **继续用旧票** —— 旧票可能还能用，真死了让上游 401 说话；
    · `force=True`（用户点「刷新」）：失败就把上游原文抛上去，让用户看见。

    `fatal=True` 的失败（`-107 / -101`，凭证无效）说明只能重新扫码，
    调用方可据此提示重登。
    """
    data = credentials_of(account)
    if not openid_of(data) or not token_of(data):
        raise client.UpstreamError(
            "账号凭证不完整（缺 openid / access_token），请重新扫码登录",
            status=401, kind="auth")
    if not guid_of(data):
        # 历史数据可能没 guid：补一个并写回，否则这份凭证永远换不出登录态。
        data["guid"] = str(uuid.uuid4())
        account.auth_json = json.dumps(data, ensure_ascii=False)
        db.commit()

    if force or needs_refresh(data):
        if force or not _refresh_backoff_active(data):
            result = refresh_account(db, account)
            if result.get("ok"):
                data = credentials_of(account)
            elif force:
                raise result["error"]
            else:
                logger.info("Marvis 续签未成功 acc=%s，本次沿用旧票：%s",
                            account.id, result.get("reason"))

    hint = token_expiry_hint(data)
    if hint["state"] == "expired":
        # 推断值可能偏保守（尤其 QQ 档），只提示不拦 —— 真让上游判死才有说服力。
        logger.info("Marvis 凭证已判过期（source=%s）acc=%s，本次仍尝试调用",
                    hint.get("source"), account.id)
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