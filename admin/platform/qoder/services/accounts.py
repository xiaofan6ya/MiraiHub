"""Qoder 凭证与账号池。

## 凭证形态

存进 `Account.auth_json`：

    {
      "realm": "cn",
      "token": "jt-...",              # 作业令牌（Bearer / COSY 都用它）
      "refresh_token": "jrt-...",
      "expires_at": 1794...,
      "uid": "01a18...",
      "name": "lifan2004",
      "email": "",
      "machine_id": "<uuid>",
      "pat": "<可选，PAT 换来的账号才有>"
    }

## 三种令牌的区别（不能混用）

| 前缀 | 来源 | 续期端点 |
|---|---|---|
| `jt-` | PAT 交换 | `/api/v1/jobToken/refresh` |
| `jrt-` | 上面那个的 refresh | — |
| `dt-` | 设备流（浏览器） | `/api/v1/deviceToken/refresh` |
| `drt-` | 上面那个的 refresh | — |

把 `drt-` 交给 jobToken/refresh、或把 `jrt-` 交给 deviceToken/refresh，
上游都按「令牌无效」拒绝 —— 两个端点**不可互换**。
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from admin.models import Account
from admin.platform.qoder.core import client, endpoints

logger = logging.getLogger("admin.platform.qoder.accounts")

PLATFORM = endpoints.PLATFORM


# ---------------------------------------------------------------------------
# 凭证读写
# ---------------------------------------------------------------------------
def credentials_of(account: Account) -> dict:
    try:
        data = json.loads(account.auth_json or "{}")
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def token_of(data: dict) -> str:
    return str(data.get("token") or "")


def realm_of(account: Account) -> str:
    return str(getattr(account, "domain", "") or "cn")


def machine_id_of(data: dict) -> str:
    """机器标识（COSY 头要带）。

    上游用它判断「同一账号不同设备」。没有就生成一个并**写回**，
    否则每次签名都换机器 id，容易触发风控。
    """
    mid = str(data.get("machine_id") or "")
    if mid:
        return mid
    return str(uuid.uuid4())


def ensure_token(db: Session, account: Account) -> dict:
    """保证 token 可用：过期就刷新，刷不动抛 `UpstreamError(kind="auth")`。"""
    data = credentials_of(account)
    token = token_of(data)
    expires = int(data.get("expires_at") or 0)
    now = int(time.time())
    # 提前 5 分钟刷新，避免「发出去的瞬间过期」
    if token and expires and expires - now > 300:
        return data
    if token and not expires:
        return data

    refresh = str(data.get("refresh_token") or "")
    if not refresh:
        raise client.UpstreamError("凭证缺少 refresh_token，需要重新登录",
                                   status=401, kind="auth")
    realm = realm_of(account)
    try:
        if refresh.startswith("jrt-"):
            fresh = client.refresh_job_token(refresh, realm)
        elif refresh.startswith("drt-"):
            fresh = client.refresh_device_token(refresh, realm)
        else:
            raise client.UpstreamError("无法识别的 refresh_token 前缀",
                                       status=401, kind="auth")
    except client.UpstreamError as exc:
        raise client.UpstreamError("令牌续期失败：%s" % exc.message,
                                   status=exc.status or 401, kind="auth",
                                   detail=exc.detail) from exc

    data["token"] = str(fresh.get("token") or "")
    data["refresh_token"] = str(fresh.get("refresh_token") or refresh)
    exp = fresh.get("expires_at")
    if isinstance(exp, str):
        data["expires_at"] = _iso_to_epoch(exp)
    elif isinstance(exp, (int, float)):
        data["expires_at"] = int(exp)
    account.auth_json = json.dumps(data, ensure_ascii=False)
    account.last_sync_at = datetime.utcnow()
    db.commit()
    logger.info("Qoder 账号 %s 令牌已续期", account.id)
    return data


def _iso_to_epoch(value: str) -> int:
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except (ValueError, AttributeError):
        return int(time.time()) + 3600


# ---------------------------------------------------------------------------
# 导入
# ---------------------------------------------------------------------------
def import_pat(db: Session, pat: str, realm: str = "cn",
               name: str = "") -> dict:
    """用 PAT 建账号（**验证链路用的最短路径**，不是主推方式）。"""
    pat = (pat or "").strip()
    if not pat.startswith("pt-"):
        raise ValueError("PAT 应以 pt- 开头")
    data = client.exchange_pat(pat, realm)
    token = str(data.get("token") or "")
    refresh = str(data.get("refresh_token") or "")
    if not token:
        raise ValueError("PAT 交换未返回 token")

    profile = client.fetch_profile(token, realm)
    uid = str(profile.get("id") or "")
    machine_id = str(uuid.uuid4())

    payload = {
        "realm": realm,
        "token": token,
        "refresh_token": refresh,
        "expires_at": _iso_to_epoch(str(data.get("expires_at") or "")),
        "uid": uid,
        "name": name or str(profile.get("name") or profile.get("username") or ""),
        "email": str(profile.get("email") or ""),
        "machine_id": machine_id,
        "pat": pat,
        "source": "pat",
    }
    account = _upsert(db, payload)
    return {"ok": True, "account_id": account.id,
            "uid": uid, "name": payload["name"], "realm": realm}


def import_record(db: Session, record: dict) -> dict:
    """导入一条已有凭证（备份 / 手工粘贴）。"""
    if not isinstance(record, dict):
        raise ValueError("凭证必须是对象")
    token = str(record.get("token") or record.get("access_token") or "")
    if not token:
        raise ValueError("凭证缺少 token")
    realm = str(record.get("realm") or record.get("mode") or "cn")
    if realm not in endpoints.REGIONS:
        realm = "cn"
    payload = {
        "realm": realm,
        "token": token,
        "refresh_token": str(record.get("refresh_token") or ""),
        "expires_at": int(record.get("expires_at") or 0),
        "uid": str(record.get("uid") or record.get("user_id") or ""),
        "name": str(record.get("name") or ""),
        "email": str(record.get("email") or ""),
        "machine_id": str(record.get("machine_id") or uuid.uuid4()),
        "source": str(record.get("source") or "import"),
    }
    # ⚠️ 总是拉一次 profile —— 不只是为了补 uid。
    #
    # 设备授权返回的凭证里有 uid 但**没有 name**，只在「uid 为空」时
    # 才查 profile 会让昵称永远缺失，最终显示成默认的
    # `qoder-cn-main` 而不是真实昵称（lifan2004）。实测发现。
    #
    # 已经有 token 了，这次请求成本很低；失败才降级。
    try:
        profile = client.fetch_profile(token, realm)
    except client.UpstreamError as exc:
        if not payload["uid"]:
            raise ValueError("无法校验凭证：%s" % exc.message) from exc
        profile = {}
    if profile:
        payload["uid"] = payload["uid"] or str(profile.get("id") or "")
        payload["name"] = (payload["name"]
                           or str(profile.get("name") or "")
                           or str(profile.get("username") or ""))
        payload["email"] = payload["email"] or str(profile.get("email") or "")
    if not payload["expires_at"]:
        payload["expires_at"] = int(time.time()) + 3600

    account = _upsert(db, payload)
    return {"ok": True, "account_id": account.id, "uid": payload["uid"],
            "name": payload["name"], "realm": realm}


def _upsert(db: Session, payload: dict) -> Account:
    """按 (platform, uid) 幂等写入。"""
    uid = payload["uid"]
    existing = None
    if uid:
        existing = (db.query(Account)
                    .filter(Account.platform == PLATFORM,
                            Account.uid == uid).first())
    if existing is None:
        existing = Account(platform=PLATFORM, name=payload["name"] or uid or "Qoder",
                           uid=uid or "", status="active")
        db.add(existing)
    existing.name = payload["name"] or existing.name or uid or "Qoder"
    existing.uid = uid or existing.uid
    existing.domain = payload["realm"]
    existing.auth_json = json.dumps(payload, ensure_ascii=False)
    existing.status = "active"
    existing.last_sync_at = datetime.utcnow()
    db.commit()
    db.refresh(existing)
    return existing


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------
def accounts_for(db: Session, realm: str = "") -> list[Account]:
    query = db.query(Account).filter(Account.platform == PLATFORM)
    if realm:
        query = query.filter(Account.domain == realm)
    return query.order_by(Account.id.asc()).all()


def select(db: Session, realm: str = "", mark_picked: bool = False) -> Account | None:
    """选一个可用账号（跳过冷却/禁用）。"""
    now = datetime.utcnow()
    for account in accounts_for(db, realm):
        if (getattr(account, "status", "") or "") != "active":
            continue
        cool_until = getattr(account, "cool_until", None)
        if cool_until and cool_until > now:
            continue
        if mark_picked:
            account.last_used_at = now
            db.commit()
        return account
    return None


def public_view(account: Account, *, usage: dict | None = None) -> dict:
    """给前端的账号视图（**不含 token**）。"""
    data = credentials_of(account)
    quota = (usage or {})
    user_quota = quota.get("userQuota") or {}
    addon_quota = quota.get("addOnQuota") or {}
    return {
        "id": account.id,
        "name": account.name,
        "uid": account.uid,
        "realm": realm_of(account),
        "status": getattr(account, "status", "") or "",
        "plan": str(quota.get("userType") or ""),
        "balance_remain": float(user_quota.get("remaining") or 0),
        "balance_total": float(user_quota.get("total") or 0),
        "addon_remain": float(addon_quota.get("remaining") or 0),
        "addon_total": float(addon_quota.get("total") or 0),
        "balance_unit": "credits",
        "expires_at": account.credits_expire_at.isoformat()
        if getattr(account, "credits_expire_at", None) else None,
        "token_type": token_of(data)[:3],
        "has_refresh": bool(data.get("refresh_token")),
        "machine_id": str(data.get("machine_id") or "")[:8],
        "last_sync_at": account.last_sync_at.isoformat()
        if account.last_sync_at else None,
        #: 额度是否来自实时拉取（false = 读的 DB 快照，可能落后）
        "quota_live": bool(usage),
    }


def summarize(rows: list[dict]) -> dict:
    total = len(rows)
    active = sum(1 for r in rows if r.get("status") == "active")
    return {
        "total": total,
        "active": active,
        "disabled": total - active,
        "cooling": 0,
        "balance_remain": sum(float(r.get("balance_remain") or 0) for r in rows),
        "balance_total": sum(float(r.get("balance_total") or 0) for r in rows),
        "addon_remain": sum(float(r.get("addon_remain") or 0) for r in rows),
        "addon_total": sum(float(r.get("addon_total") or 0) for r in rows),
    }


# ---------------------------------------------------------------------------
# 刷新
# ---------------------------------------------------------------------------
#: 额度快照的「新鲜」窗口（秒）。
#:
#: 为什么需要它：账号池列表是高频读（每次切平台、每次刷新页面），
#: 但上游额度接口有速率限制。每次读都拉会打爆上游；
#: 完全靠 DB 缓存又会显示陈旧余额（用户消耗 1 积分后仍显示旧值）。
#: 60 秒是「用户感知得到变化」与「不打爆上游」的折中。
QUOTA_TTL = 60


def quota_is_fresh(account: Account) -> bool:
    """额度快照是否还在新鲜期内。"""
    synced = getattr(account, "credits_synced_at", None)
    if synced is None:
        return False
    return (datetime.utcnow() - synced).total_seconds() < QUOTA_TTL


def sync_quota(db: Session, account: Account) -> dict:
    """拉额度并写回统一列。

    Qoder 有**两个额度桶**：
      userQuota   套餐额度
      addOnQuota  资源包 / 签到发的
    两边都算进 `balance_remain/total`（用户关心的是「总共还能用多少」），
    同时把 addOn 单独留着供界面区分。
    """
    data = ensure_token(db, account)
    realm = realm_of(account)
    token = token_of(data)
    try:
        usage = client.fetch_usage(token, realm)
    except client.UpstreamError as exc:
        return {"ok": False, "error": exc.to_dict()}

    user_quota = usage.get("userQuota") or {}
    addon_quota = usage.get("addOnQuota") or {}
    remain = float(user_quota.get("remaining") or 0) + float(
        addon_quota.get("remaining") or 0)
    total = float(user_quota.get("total") or 0) + float(
        addon_quota.get("total") or 0)

    account.balance_remain = int(round(remain))
    account.balance_total = int(round(total))
    account.last_sync_at = datetime.utcnow()
    account.credits_synced_at = datetime.utcnow()
    expires = usage.get("expiresAt")
    if isinstance(expires, (int, float)) and expires > 0:
        # ⚠️ 上游的 `expiresAt` 单位**不统一**：Qoder 国内版给毫秒
        # （1795825334000），国际版给秒（1795825334）。
        # 统一按「大于 1e11 就当毫秒」判定，否则 `utcfromtimestamp(秒/1000)`
        # 会落到 1970 年附近甚至溢出 —— Windows 上直接抛
        # `OSError: [Errno 22] Invalid argument`，整个账号刷新失败
        # （2026-10-31 实测：qoder-intl 的号全刷不出来）。
        secs = float(expires) / 1000.0 if float(expires) > 1e11 else float(expires)
        try:
            account.credits_expire_at = datetime.utcfromtimestamp(secs)
        except (OSError, OverflowError, ValueError):
            account.credits_expire_at = None
    account.credits_snapshot = json.dumps({
        "realm": realm,
        "plan": usage.get("userType") or "",
        "unit": "credits",
        "user_quota": user_quota,
        "addon_quota": addon_quota,
        "total_usage_percentage": usage.get("totalUsagePercentage"),
        "is_quota_exceeded": usage.get("isQuotaExceeded"),
        "expires_at": expires,
    }, ensure_ascii=False)[:20000]
    db.commit()
    return {"ok": True, "remain": remain, "total": total,
            "user_quota": user_quota, "addon_quota": addon_quota,
            "plan": usage.get("userType") or ""}
