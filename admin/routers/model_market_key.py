"""**给 API Key 用的模型广场**：`GET /v1/model-market`。

## 为什么单独做一条

控制台的模型广场（`/api/stats/model-market`）是**平台公共视角**：它按归一后的
模型给出全平台的用量、报价与可用账号数。但客户端 / 第三方拿着一个 Key 来问
「我现在能用什么模型、每个还剩多少」，公共视角有三个错：

  * **越权**：会列出这个 Key 用不了的模型（分组外、平台外、绑定账号没有权益的），
    客户端照着点，一发就 403；
  * **口径不对**：`requests/credits` 是全平台历史，不是**这个 Key** 的消耗；
  * **没有「我还剩多少」**：Key 的额度上限、已用、剩余都不在公共视角里。

所以这里按 Key 收窄：先用 `_key_allowed_models()` 求出它**真正能用**的模型集合
（分组 ∩ 平台 ∩ 绑定账号权益 ∩ 启用），再逐模型附上该 Key 的窗口内用量与
Key 级额度余额。

## 字段

尽量与公共广场同名（`requests` / `success_rate` / `credits` / `tps` /
`available` …），这样客户端不必学两套；额外补 Key 视角的
`key_used`、`key_remaining`、`status`、`health`。

## 认证

与 `/v1/*` 一致：`Authorization: Bearer <key>` 或 `X-API-Key: <key>`。
**故意改用 GET 且不改用 admin 鉴权** —— 它是客户端自助查询，不是管理端点。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Header
from fastapi.responses import JSONResponse
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from admin.account_binding import scope_accounts
from admin.db import get_db
from admin.model_alias import canonical_key, display_name
from admin.models import Account, AccountModelCooldown, ApiKey, ModelConfig, UsageLog
from admin.security import get_key_row

logger = logging.getLogger("admin.routers.model_market_key")

router = APIRouter(tags=["model-market"])

#: 窗口上限：广场默认近 24 小时，最多一周（与公共广场一致）
DEFAULT_HOURS = 24
MAX_HOURS = 168


def _read_key(x_api_key: str | None, authorization: str | None) -> str:
    if x_api_key:
        return x_api_key.strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


def _auth(db: Session, api_key: str) -> tuple[ApiKey | None, JSONResponse | None]:
    """与 `/v1/balance` 同口径的鉴权（含「已停用」的明确文案）。"""
    if not api_key:
        return None, JSONResponse(status_code=401, content={
            "error": {"message": "缺少 API Key", "type": "auth_error",
                      "code": "missing_api_key"}})
    key = get_key_row(db, api_key)
    if key is None:
        return None, JSONResponse(status_code=401, content={
            "error": {"message": "无效 API Key", "type": "auth_error",
                      "code": "invalid_api_key"}})
    if str(getattr(key, "status", "active") or "active") != "active":
        return None, JSONResponse(status_code=403, content={
            "error": {"message": "API Key 已停用", "type": "key_disabled",
                      "code": "key_disabled"}})
    return key, None


def _allowed_models(db: Session, key: ApiKey) -> set[str] | None:
    """该 Key 能用的模型集合；`None` = 不限制（全部启用模型）。

    复用代理热路径的 `_key_allowed_models`（分组 ∩ 绑定账号权益 ∩ 启用），
    这样「广场里能点」与「真正能调」永远是同一套判据 —— 两处各算一遍必然漂移。
    绑定账号那一步要读一次上游模型权益，失败时降级成「只按分组收窄」，
    不能因为上游抖一下就把广场变成空列表。
    """
    from admin.routers.proxy import _key_allowed_models

    try:
        return _key_allowed_models(db, key)
    except Exception as exc:  # noqa: BLE001 - 上游不可达时降级
        logger.warning("读取 Key 模型权益失败，降级为分组口径：%s", exc)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        from admin.routers.proxy import _key_group_models

        try:
            return _key_group_models(db, key)
        except Exception:  # noqa: BLE001
            return None


def _key_platform(key: ApiKey) -> str:
    """Key 绑定的平台（空 = 不限平台）。"""
    return str(getattr(key, "platform", "") or "").strip().lower()


def _readable(storage: str, model_id: str) -> str:
    """模型的可读名（Marvis 这类「槽位名」平台要换成真实模型名）。"""
    try:
        from admin.platform import models_router

        return models_router.readable_model_id(storage, model_id) or model_id
    except Exception:  # noqa: BLE001 - 映射失败就原样
        return model_id


def _key_usage(db: Session, key_id: int, start: datetime,
               end: datetime) -> dict[str, dict]:
    """该 Key 在窗口内按**模型**的用量（走 `ix_usage_logs_key_created` 索引）。

    汇总表（`usage_rollups`）没有 Key 维度，所以这里直接读原表 ——
    窗口最长一周、且只取这个 Key 的行，代价可控。
    """
    rows = (db.query(
        UsageLog.model.label("model"),
        func.count(UsageLog.id).label("requests"),
        func.coalesce(func.sum(UsageLog.prompt_tokens), 0).label("prompt_tokens"),
        func.coalesce(func.sum(UsageLog.completion_tokens), 0).label("completion_tokens"),
        func.coalesce(func.sum(UsageLog.total_tokens), 0).label("tokens"),
        func.coalesce(func.sum(UsageLog.cached_tokens), 0).label("cached_tokens"),
        func.coalesce(func.sum(UsageLog.credits), 0).label("credits"),
        func.coalesce(func.sum(UsageLog.latency_ms), 0).label("latency_sum"),
        func.coalesce(func.sum(case((UsageLog.latency_ms.isnot(None), 1), else_=0)),
                      0).label("latency_count"),
        func.coalesce(func.sum(UsageLog.ttfb_ms), 0).label("ttfb_sum"),
        func.coalesce(func.sum(case((UsageLog.ttfb_ms.isnot(None), 1), else_=0)),
                      0).label("ttfb_count"),
        # 成功 = 没有错误分类。与公共广场的 success_rate 同口径。
        func.coalesce(func.sum(case((or_(UsageLog.error_kind.is_(None),
                                         UsageLog.error_kind == ""), 1),
                                   else_=0)), 0).label("successes"),
    ).filter(UsageLog.api_key_id == int(key_id),
             UsageLog.created_at >= start,
             UsageLog.created_at <= end)
     .group_by(UsageLog.model).all())

    out: dict[str, dict] = {}
    for row in rows:
        out[str(row.model or "")] = {
            "requests": int(row.requests or 0),
            "successes": int(row.successes or 0),
            "prompt_tokens": int(row.prompt_tokens or 0),
            "completion_tokens": int(row.completion_tokens or 0),
            "tokens": int(row.tokens or 0),
            "cached_tokens": int(row.cached_tokens or 0),
            "credits": float(row.credits or 0.0),
            "latency_sum": int(row.latency_sum or 0),
            "latency_count": int(row.latency_count or 0),
            "ttfb_sum": int(row.ttfb_sum or 0),
            "ttfb_count": int(row.ttfb_count or 0),
        }
    return out


def _capacity(db: Session, key: ApiKey) -> dict[str, tuple[int, int]]:
    """按**落库平台**统计该 Key 可见的「活跃账号 / 模型级冷却账号」。

    账号维度必须按 Key 收窄（`scope_accounts`）—— 否则 Key 只绑了 1 个号，
    广场却显示「可用账号 40」，用户点了必然排不上。
    """
    now = datetime.utcnow()
    query = scope_accounts(db.query(Account.platform, func.count(Account.id)),
                           getattr(key, "account_id", None), key.id, key=key)
    active: dict[str, int] = {}
    for platform, count in (query
                            .filter(Account.status == "active",
                                    or_(Account.cool_until.is_(None),
                                        Account.cool_until <= now))
                            .group_by(Account.platform).all()):
        active[str(platform or "").lower()] = int(count or 0)

    cooled: dict[str, int] = {}
    for model, count in (db.query(AccountModelCooldown.model,
                                 func.count(AccountModelCooldown.id))
                         .filter(AccountModelCooldown.until > now)
                         .group_by(AccountModelCooldown.model).all()):
        cooled[str(model or "")] = int(count or 0)
    return {"active": active, "cooled": cooled}


def build_market(db: Session, key: ApiKey, *, hours: int = DEFAULT_HOURS) -> dict:
    """给某个 Key 算模型广场（纯函数式，便于测试与复用）。"""
    hours = max(1, min(int(hours or DEFAULT_HOURS), MAX_HOURS))
    end = datetime.utcnow()
    start = end - timedelta(hours=hours)

    allowed = _allowed_models(db, key)
    platform_scope = _key_platform(key)

    configs = [c for c in db.query(ModelConfig).filter(ModelConfig.enabled == 1).all()
               if not platform_scope or str(c.platform or "").lower() == platform_scope]

    # 归一：同一个模型在多个平台是同一个模型，键用归一 key
    groups: dict[str, dict] = {}
    for config in configs:
        storage = str(config.platform or "").lower()
        mid = str(config.model_id or "")
        readable = _readable(storage, mid)
        # 白名单判定要同时认「配置里的 model_id」与「对外可读名」——
        # 客户端（和分组配置）两种写法都可能出现。
        if allowed is not None and mid not in allowed and readable not in allowed:
            continue
        ck = canonical_key(readable or mid, "") or readable or mid
        entry = groups.setdefault(ck, {
            "key": ck,
            "name": display_name(ck, readable or mid),
            "models": set(),
            "readables": set(),
            "platforms": set(),
            "pricing": [],
        })
        entry["models"].add(mid)
        entry["readables"].add(readable)
        entry["platforms"].add(storage)
        # `credit_multiplier` 负数 = 「该平台没配这个模型的计价」（Trae 的
        # `-1` 就是这种情况），既不是免费也不是某个倍率。当成「未知」输出，
        # 否则客户端会显示成 `×-1.00`。
        raw_multiplier = config.credit_multiplier
        known = raw_multiplier is not None and float(raw_multiplier) >= 0
        entry["pricing"].append({
            "platform": storage,
            "platform_label": _platform_label(storage),
            "model_id": mid,
            "call_id": readable,
            "multiplier": float(raw_multiplier) if known else None,
            "free": known and float(raw_multiplier) == 0,
        })

    usage = _key_usage(db, key.id, start, end)
    caps = _capacity(db, key)
    quota = _key_quota(key)

    items = []
    for ck, entry in groups.items():
        # 这个模型组在该 Key 窗口内的用量（同一模型多平台时按 model_id 累加）
        agg = {"requests": 0, "successes": 0, "prompt_tokens": 0,
               "completion_tokens": 0, "tokens": 0, "cached_tokens": 0,
               "credits": 0.0, "latency_sum": 0, "latency_count": 0,
               "ttfb_sum": 0, "ttfb_count": 0}
        for mid in entry["models"]:
            stats = usage.get(mid)
            if not stats:
                continue
            for field in agg:
                agg[field] += stats[field]

        platforms = sorted(entry["platforms"])
        capacity = sum(caps["active"].get(p, 0) for p in platforms)
        blocked = sum(caps["cooled"].get(m, 0) for m in entry["models"])
        usable = max(0, capacity - blocked)
        req = int(agg["requests"])
        ok = int(agg["successes"])
        success_rate = round(ok / req, 4) if req else None

        items.append({
            "key": ck,
            "name": entry["name"],
            # **给客户端直接用的调用名**（与 /v1/models 的 id 对齐）
            "id": sorted(entry["readables"])[0] if entry["readables"] else ck,
            "models": sorted(entry["models"]),
            "call_ids": sorted(entry["readables"]),
            "platforms": platforms,
            "platform_labels": [_platform_label(p) for p in platforms],
            "pricing": entry["pricing"],
            "free": bool(entry["pricing"]) and all(p["free"] for p in entry["pricing"]),
            "min_multiplier": min((p["multiplier"] for p in entry["pricing"]
                                   if p["multiplier"] is not None), default=None),
            "priced": any(p["multiplier"] is not None for p in entry["pricing"]),
            # ---- 用量（**这个 Key** 的，不是全平台）----
            "requests": req,
            "success_rate": success_rate,
            "tokens": int(agg["tokens"]),
            "prompt_tokens": int(agg["prompt_tokens"]),
            "completion_tokens": int(agg["completion_tokens"]),
            "cached_tokens": int(agg["cached_tokens"]),
            "credits": round(float(agg["credits"]), 4),
            "avg_latency_ms": (int(round(agg["latency_sum"] / agg["latency_count"]))
                               if agg["latency_count"] else 0),
            "avg_ttfb_ms": (int(round(agg["ttfb_sum"] / agg["ttfb_count"]))
                            if agg["ttfb_count"] else 0),
            # ---- 容量 / 健康 ----
            "active_accounts": capacity,
            "exhausted_accounts": blocked,
            "available_accounts": usable,
            "available": usable > 0,
            "status": ("available" if usable > 0
                       else ("exhausted" if capacity > 0 else "unavailable")),
            "health": _health_label(success_rate, req, usable),
            # ---- 这个 Key 还剩多少 ----
            "key_used_credits": round(float(agg["credits"]), 4),
            "key_quota_remaining": quota["remaining"],
        })

    items.sort(key=lambda item: (-item["requests"], item["name"]))
    return {
        "object": "model_market",
        "scope": "api_key",
        "window_hours": hours,
        "start": start.isoformat() + "Z",
        "end": end.isoformat() + "Z",
        "key": {
            "id": key.id,
            "name": key.name or "",
            "platform": platform_scope,
            "group_id": int(getattr(key, "group_id", 0) or 0),
            "restricted": allowed is not None,
            "quota": quota,
        },
        "items": items,
        "total": len(items),
        "available_models": sum(1 for item in items if item["available"]),
    }


def _key_quota(key: ApiKey) -> dict:
    unlimited = bool(key.unlimited)
    limit = None if unlimited else float(key.credit_limit or 0)
    used = round(float(key.credit_used or 0), 4)
    remaining = None if unlimited else round(max(0.0, limit - used), 4)
    return {"unlimited": unlimited, "limit": limit,
            "used": used, "remaining": remaining}


def _platform_label(storage: str) -> str:
    try:
        from admin.platform import registry

        for spec in registry.all_platforms(enabled_only=False):
            if str(spec.storage()).lower() == str(storage).lower():
                return spec.label or spec.id
    except Exception:  # noqa: BLE001
        pass
    return storage or "未知平台"


def _health_label(success_rate: float | None, requests: int, usable: int) -> str:
    """三档健康度（给客户端做颜色用；数字口径见 `success_rate`）。

    * `no_data`：窗口内没有调用 —— 不代表坏，也不代表好；
    * `ok`：成功率 ≥ 95% 且至少 5 次调用（样本太少不下结论）；
    * `degraded`：成功率 ≥ 70%；
    * `down`：低于 70%，或一个可用账号都没有。
    """
    if usable <= 0:
        return "down"
    if not requests:
        return "no_data"
    rate = success_rate or 0.0
    if requests >= 5 and rate >= 0.95:
        return "ok"
    if rate >= 0.70:
        return "degraded"
    return "down"


@router.get("/v1/model-market")
@router.get("/v1/models/market")
def model_market_for_key(
    hours: int = DEFAULT_HOURS,
    db: Session = Depends(get_db),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None),
):
    """**这个 Key 能用**的模型广场：用量、报价、容量、健康、剩余额度。

    与 `/api/stats/model-market`（管理员公共视角）的区别见模块文档。
    客户端只需要把 Key 带上，拿到的就是「我能用什么、用掉多少、还剩多少」。
    """
    key, error = _auth(db, _read_key(x_api_key, authorization))
    if error is not None:
        return error
    try:
        return build_market(db, key, hours=hours)
    except Exception as exc:  # noqa: BLE001 - 明细计算失败不把接口打成 500
        logger.exception("Key 模型广场计算失败 key=%s", getattr(key, "id", "?"))
        return JSONResponse(status_code=500, content={
            "error": {"message": "模型广场暂时不可用：%s" % str(exc)[:160],
                      "type": "server_error", "code": "market_unavailable"}})
