"""KroWork 账号池：凭证的入库、刷新、导出、统计。

## 凭证结构（`accounts.auth_json`）

    {
      "id_token":      "<Keycloak id_token，30 分钟寿命>",
      "refresh_token": "<Keycloak refresh_token，客户端关掉后靠它续命>",
      "region":        "cn" | "global",
      "sub":           "<jwt.sub>",
      "email":         "<preferred_username>",
      "device_id":     "<64 hex>",
      "expires_at":    1795072902,
      "source":        "sidecar" | "import" | "file" | "env"
    }

## 为什么 uid 用 `sub`

Keycloak 的 `sub` 是**稳定主键**（同一账号跨设备一致）；`email` /
`preferred_username` 会变，用它做幂等键会在改名后插出重复账号。

## 闸门与铁律（与 skill 一致）

* 凭证写库前先证明它能用（拉一次 `/config/model-map`）；
* 任何返回给前端的数据都不含 token 原文；
* 「不支持」用 `{"supported": False}` 表达，不伪造成功。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from sqlalchemy.orm import Session

from admin.models import Account
from admin.platform.krowork.core import client, endpoints, token as token_mod

logger = logging.getLogger("admin.platform.krowork.accounts")

PLATFORM = "krowork"
STORAGE = "krowork"


# ---------------------------------------------------------------------------
# 读写 auth_json
# ---------------------------------------------------------------------------
def credentials_of(account: Account | None) -> dict:
    if account is None:
        return {}
    try:
        data = json.loads(account.auth_json or "{}")
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def token_of(data: dict | None) -> str:
    data = data or {}
    return str(data.get("id_token") or data.get("access_token") or "")


def refresh_token_of(data: dict | None) -> str:
    return str((data or {}).get("refresh_token") or "")


def region_of(account: Account | None) -> str:
    data = credentials_of(account)
    return str(data.get("region") or "cn")


def public_view(account: Account) -> dict:
    """给控制台的账号行（**不含 token**）。"""
    data = credentials_of(account)
    exp = int(data.get("expires_at") or 0)
    return {
        "id": account.id,
        "name": account.name or "",
        "uid": (account.uid or "")[:8] + "…" if account.uid else "",
        "uid_full": account.uid or "",
        "email": data.get("email") or "",
        "region": region_of(account),
        "status": account.status,
        "platform": account.platform,
        "has_token": bool(token_of(data)),
        "has_refresh": bool(refresh_token_of(data)),
        "expires_at": exp,
        "expires_in": max(0, exp - int(time.time())) if exp else 0,
        "expired": bool(exp and exp <= time.time()),
        "source": data.get("source") or "",
        "balance_total": account.balance_total or 0,
        "balance_remain": account.balance_remain or 0,
        "last_used_at": account.last_used_at.isoformat()
        if getattr(account, "last_used_at", None) else "",
    }


# ---------------------------------------------------------------------------
# 入库
# ---------------------------------------------------------------------------
def _identity(payload: dict) -> tuple[str, str]:
    """从 id_token 里取 `(sub, email)`；token 缺失时回落到 payload 自带字段。"""
    tok = token_of(payload)
    claim = token_mod.jwt_payload(tok) if tok else {}
    sub = str(payload.get("sub") or claim.get("sub") or "")
    email = str(payload.get("email") or claim.get("preferred_username")
                or claim.get("email") or "")
    return sub, email


def _upsert(db: Session, payload: dict) -> tuple[Account, bool]:
    sub, email = _identity(payload)
    if not sub:
        raise ValueError("凭证里没有 sub（id_token 无效或未登录）")
    row = (db.query(Account)
             .filter(Account.platform == STORAGE, Account.uid == sub)
             .first())
    created = row is None
    if row is None:
        row = Account(platform=STORAGE, uid=sub, name=email or sub[:8],
                      auth_json="{}", status="active")
        db.add(row)

    old = credentials_of(row)
    merged = dict(old)
    merged.update({k: v for k, v in payload.items() if v not in (None, "")})
    merged["sub"] = sub
    merged["email"] = email or merged.get("email") or ""
    tok = token_of(merged)
    if tok:
        merged["expires_at"] = int(token_mod.token_exp(tok) or merged.get("expires_at") or 0)
        merged["device_id"] = client.device_id()
    merged.setdefault("region", "cn")
    row.auth_json = json.dumps(merged, ensure_ascii=False)
    row.name = row.name or merged.get("email") or sub[:8]
    db.flush()
    return row, created


def import_record(db: Session, record: dict) -> tuple[Account, bool]:
    """导入单条（接受 request 里常见的几种外层包装）。"""
    payload = record
    for key in ("credential", "credentials", "account", "data"):
        inner = payload.get(key) if isinstance(payload, dict) else None
        if isinstance(inner, dict):
            payload = inner
            break
    if not isinstance(payload, dict) or not (token_of(payload) or refresh_token_of(payload)):
        raise ValueError("导入内容里既没有 id_token 也没有 refresh_token")
    account, created = _upsert(db, dict(payload))
    db.commit()
    return account, created


def import_local(db: Session, *, probe: bool = True) -> dict:
    """把本机 KroWork 的凭证拉进号池。

    来源与 `core.token` 同一条链（env → 文件 → sidecar）。
    `probe=True` 时先打一次模型表，**证明能用才入库** ——
    这条规矩是为了避免「坏票覆盖好票导致整池突然全挂」。
    """
    snap = token_mod.current(force=True)
    if not snap.ok:
        return {"imported": 0, "items": [],
                "error": snap.detail or "没读到本机凭证（KroWork 未登录或没在运行）"}
    if probe:
        try:
            with __import__("httpx").Client(timeout=client.TIMEOUT) as http:
                resp = http.get(endpoints.host_of("cn") + endpoints.PATH_MODEL_MAP,
                                headers=client.headers(snap.id_token))
            if resp.status_code >= 400:
                return {"imported": 0, "items": [],
                        "error": "凭证不可用（上游 %s）：%s"
                                 % (resp.status_code, resp.text[:160])}
        except Exception as exc:  # noqa: BLE001
            return {"imported": 0, "items": [], "error": "验证失败：%s" % exc}
    account, created = _upsert(db, {
        "id_token": snap.id_token,
        "refresh_token": snap.refresh_token,
        "expires_at": int(snap.expires_at or 0),
        "source": snap.source,
        "region": "cn",
    })
    db.commit()
    return {"imported": 1, "created": created,
            "items": [public_view(account)], "source": snap.source}


def scan() -> dict:
    """扫描本机登录态，返回**不含 token** 的摘要。"""
    snap = token_mod.current(force=True)
    cfg = {}
    try:
        path = token_mod.data_dir() / "config.json"
        if path.is_file():
            cfg = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        cfg = {}
    item = {
        "name": "本机 KroWork",
        "region": str(cfg.get("region") or "cn"),
        "projects_dir": str(cfg.get("projectsDir") or ""),
        "data_dir": str(token_mod.data_dir()),
        "has_token": snap.ok,
        "has_refresh": bool(snap.refresh_token),
        "source": snap.source,
        "expires_at": int(snap.expires_at or 0),
        "detail": snap.detail,
        "uid_full": token_mod.token_sub(snap.id_token)[:8] + "…" if snap.ok else "",
    }
    return {"items": [item] if snap.ok else [], "errors": [] if snap.ok else [snap.detail],
            "probe": item}


# ---------------------------------------------------------------------------
# 令牌维护
# ---------------------------------------------------------------------------
def ensure_token(db: Session | None, account: Account, *, force: bool = False) -> str:
    """确保账号有可用 id_token（过期就用 refresh_token 换）。返回 token。

    ## 续期失败时的记忆（2026-11-20 加）

    会话型 refresh_token 换票必然失败（见 `token.refresh_with`）。如果每次
    `/v1` 请求都去试一遍，就是**每个请求打一次 Keycloak**，把登录接口打出
    限流。所以失败后写一个 `refresh_blocked_until` 时间戳，冷却期内直接
    用旧票（让上游回 401，语义更清楚）并给出「重新授权」的提示。
    """
    data = credentials_of(account)
    tok = token_of(data)
    exp = int(data.get("expires_at") or 0)
    alive = bool(tok) and (exp == 0 or exp - token_mod.REFRESH_MARGIN > time.time())
    if alive and not force:
        return tok

    # 续期冷却：失败过一次就 5 分钟内别再试（别把 Keycloak 打限流）
    blocked_until = float(data.get("refresh_blocked_until") or 0)
    if blocked_until > time.time() and tok and not force:
        return tok

    refreshed = token_mod.refresh_with(refresh_token_of(data), region_of(account))
    if refreshed.ok:
        payload = {"id_token": refreshed.id_token,
                   "expires_at": int(refreshed.expires_at or 0),
                   "source": "refresh"}
        if refreshed.refresh_token:
            # ⚠️ 必须写回：Keycloak 会**轮换** refresh_token，旧的那枚立刻作废
            payload["refresh_token"] = refreshed.refresh_token
        data.pop("refresh_blocked_until", None)
        data.pop("refresh_blocked_reason", None)
        data.update(payload)
        account.auth_json = json.dumps(data, ensure_ascii=False)
        if db is not None:
            db.commit()
        return refreshed.id_token

    # 记下失败原因 + 冷却，避免每请求重试
    data["refresh_blocked_until"] = time.time() + 300
    data["refresh_blocked_reason"] = str(refreshed.detail)[:300]
    account.auth_json = json.dumps(data, ensure_ascii=False)
    if db is not None:
        try:
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()

    # 客户端在跑的时候，sidecar 通常比 refresh 更新鲜
    snap = token_mod.current(force=True)
    if snap.ok:
        data.update({"id_token": snap.id_token,
                     "expires_at": int(snap.expires_at or 0),
                     "source": snap.source})
        account.auth_json = json.dumps(data, ensure_ascii=False)
        if db is not None:
            db.commit()
        return snap.id_token

    if tok:
        return tok  # 交给上游判 401（比在这里抛更利于排障）
    raise ValueError("账号没有可用凭证：%s"
                     % (refreshed.detail or "请在控制台重新走一次「授权登录」"))


def verify_token(tok: str, region: str = "cn") -> dict:
    """打一次模型表，确认 token 真的能用。"""
    try:
        import httpx

        with httpx.Client(timeout=client.TIMEOUT) as http:
            resp = http.get(endpoints.host_of(region) + endpoints.PATH_MODEL_MAP,
                            headers=client.headers(tok))
        if resp.status_code >= 400:
            return {"ok": False, "status": resp.status_code, "detail": resp.text[:200]}
        body = resp.json() if resp.content else {}
        return {"ok": True, "status": resp.status_code,
                "models": len((body or {}).get("models") or {}),
                "version": (body or {}).get("version")}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": "%s: %s" % (type(exc).__name__, exc)}


def credit_packages(items, now_ms: int | None = None) -> list[dict]:
    """把 `/krow/commerce/credit/balance` 的 `items` 转成**已归一化的积分包**。

    ## 为什么要这一步（2026-11-20）

    控制台账号池那一列「到期」读的是 `credits_snapshot`（逐包明细）。
    KroWork 的积分本来就是**按包**发的（免费试用 / 每日签到 / 增值包），
    上游每条 `items[]` 正好是一个包：

        {"planId":1, "creditType":"免费试用", "credits":2953,
         "originCredits":3000, "expireTime":"2026-12-19 14:47:18"}

    不写这个快照，界面就一直是「未采集 · 点重新采集」，
    而实际上游明明有到期日可以显示。

    ## 为什么直接产出**归一化后**的结构

    `credits.normalize_packages()` 认的是 WorkBuddy 的字段名
    （`CycleCapacitySize` / `CycleCapacityRemain` …）。KroWork 给的是
    `credits` / `originCredits` / `expireTime`，喂给 `normalize_packages`
    会得到一堆 0（实测：`package_count:2` 但 `total_capacity:0`）。

    所以这里一步到位产出 `summarize_account` 直接吃的形状：
    `total` / `remaining` / `used` / `expire_at`(毫秒) / `expired` /
    `expiring_soon` / `days_left`。跳过 normalize 那一层，不做字段名翻译游戏。
    """
    import datetime as _dt

    now = now_ms if now_ms is not None else int(_dt.datetime.now().timestamp() * 1000)
    soon_window = 7 * 86400 * 1000
    out: list[dict] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        try:
            remaining = float(item.get("credits") or 0)
        except (TypeError, ValueError):
            remaining = 0.0
        try:
            total = float(item.get("origin") or item.get("originCredits") or remaining)
        except (TypeError, ValueError):
            total = remaining
        expire_ms = None
        raw_expire = item.get("expire_time") or item.get("expireTime") or ""
        if raw_expire:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
                try:
                    dt = _dt.datetime.strptime(str(raw_expire)[:19], fmt)
                    # 上游给的是**东八区本地时间**，转 UTC 毫秒
                    expire_ms = int((dt - _dt.timedelta(hours=8))
                                    .replace(tzinfo=_dt.timezone.utc).timestamp() * 1000)
                    break
                except ValueError:
                    continue
        expired = expire_ms is not None and expire_ms <= now
        expiring_soon = (expire_ms is not None and expire_ms > now
                         and expire_ms - now <= soon_window)
        days_left = None
        if expire_ms is not None:
            days_left = max(0.0, (expire_ms - now) / 86400000.0)
        label = str(item.get("type") or item.get("creditType") or "积分包")
        out.append({
            "package_code": "krow-%s" % (item.get("plan_id") or item.get("planId") or ""),
            "package_name": label,
            "display_name": label,
            "total": round(max(0.0, total), 4),
            "remaining": round(max(0.0, remaining), 4),
            "used": round(max(0.0, total - remaining), 4),
            "status": 0 if not expired else 3,
            "expire_at": expire_ms,
            "expired": expired,
            "expiring_soon": expiring_soon,
            "days_left": round(days_left, 2) if days_left is not None else None,
        })
    return out


# ---------------------------------------------------------------------------
# 统计
# ---------------------------------------------------------------------------
def accounts_for(db: Session, region: str = "") -> list[Account]:
    query = db.query(Account).filter(Account.platform == STORAGE)
    rows = query.order_by(Account.id.asc()).all()
    if not region:
        return rows
    return [row for row in rows if region_of(row) == region]


def summarize(db: Session, region: str = "") -> dict:
    rows = accounts_for(db, region)
    active = [r for r in rows if (r.status or "active") == "active"]
    live = 0
    now = time.time()
    for row in active:
        exp = int(credentials_of(row).get("expires_at") or 0)
        if not exp or exp > now:
            live += 1
    return {
        "total": len(rows),
        "active": len(active),
        "live": live,
        "expired": len(active) - live,
        "disabled": len(rows) - len(active),
        "platform": PLATFORM,
    }


def usage_stats(db: Session, *, days: int = 30, limit: int = 50) -> dict:
    """本地账本口径的用量（上游没有用量接口）。"""
    from sqlalchemy import func

    from admin.models import UsageLog  # noqa: WPS433 - 延迟导入避免循环

    since = None
    if days > 0:
        import datetime as _dt

        since = _dt.datetime.utcnow() - _dt.timedelta(days=days)
    query = db.query(UsageLog).filter(UsageLog.platform == STORAGE)
    if since is not None:
        query = query.filter(UsageLog.created_at >= since)
    total = query.count()
    agg = (db.query(func.sum(UsageLog.total_tokens or 0))
             .filter(UsageLog.platform == STORAGE))
    if since is not None:
        agg = agg.filter(UsageLog.created_at >= since)
    tokens = int((agg.scalar() or 0))
    rows = query.order_by(UsageLog.id.desc()).limit(limit).all()
    return {
        "platform": PLATFORM,
        "days": days,
        "total": total,
        "total_tokens": tokens,
        "source": "local-ledger",
        "items": [{
            "id": r.id,
            "model": r.model,
            "tokens": int(r.total_tokens or 0),
            "credits": float(r.credits or 0),
            "error": r.error_kind or "",
            "at": r.created_at.isoformat() if r.created_at else "",
        } for r in rows],
    }
