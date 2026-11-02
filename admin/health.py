"""Gateway observations, independent of official daily quota accounting."""
from collections import Counter
from datetime import datetime, timedelta
import threading
import time
import math

from sqlalchemy import case, func

from admin.config import settings
from admin.models import Account, AccountModelCooldown, ModelConfig, UsageLog
from admin.pool import POOL
from admin.account_binding import scope_accounts


def risk_health(db, now):
    """Passive symptoms in gateway traffic, not an upstream risk score."""
    from admin import maintenance
    since = now - timedelta(minutes=15)
    kind = case((UsageLog.error_kind == "", "success"), else_=UsageLog.error_kind)
    rows = db.query(kind, func.count(UsageLog.id),
                    func.count(func.distinct(UsageLog.account_id))).filter(
        UsageLog.created_at >= since).group_by(kind).all()
    reasons = {kind or "unknown": {"requests": count, "accounts": accounts}
               for kind, count, accounts in rows}
    return {"window_minutes": 15, "request_count": sum(r[1] for r in rows),
            "observations": reasons,
            "waf_active": POOL.waf.active(),
            "waf_remaining_seconds": POOL.waf.remaining(),
            "maintenance": maintenance.snapshot(), "source": "gateway_only"}


def _percentiles(values):
    values = sorted(v for v in values if v is not None and v >= 0)
    return {f"p{p}": values[max(0, math.ceil(len(values) * p / 100) - 1)] if values else None
            for p in (50, 95)}


#: model_health 结果的进程内缓存。
#:
#: 这个接口对**每个模型**各跑一条 `ORDER BY created_at DESC LIMIT 2000`
#: （65 个模型 = 65 条查询，线上 218ms），而它回答的是「最近 24 小时的
#: 健康状况」—— 30 秒内不可能变到需要重新逐模型扫 2000 行。
#: 缓存后模型页刷新从 200ms+ 降到 ~1ms，同时省掉 64 条本可以避免的查询。
_MODEL_HEALTH_CACHE = {"at": 0.0, "hours": None, "value": None}
_MODEL_HEALTH_TTL = 30.0
_MODEL_HEALTH_LOCK = threading.Lock()


def model_health(db, hours=24):
    """最近 N 小时的模型健康（带 30 秒进程内缓存，见上方说明）。"""
    now_ts = time.time()
    with _MODEL_HEALTH_LOCK:
        if (_MODEL_HEALTH_CACHE["value"] is not None
                and _MODEL_HEALTH_CACHE["hours"] == hours
                and now_ts - _MODEL_HEALTH_CACHE["at"] < _MODEL_HEALTH_TTL):
            return _MODEL_HEALTH_CACHE["value"]
    value = _model_health_uncached(db, hours)
    with _MODEL_HEALTH_LOCK:
        _MODEL_HEALTH_CACHE.update({"at": now_ts, "hours": hours, "value": value})
    return value


def _model_health_uncached(db, hours=24):
    from admin.routers.proxy import _looks_login_dead

    now = datetime.utcnow()
    since = now - timedelta(hours=hours)
    accounts = scope_accounts(db.query(Account)).all()
    configs = {c.model_id: c for c in db.query(ModelConfig).filter(
        ModelConfig.level == "system", ModelConfig.enabled == 1).all()}
    limits = db.query(AccountModelCooldown).filter(AccountModelCooldown.until > now).all()
    grouped = db.query(UsageLog.model, UsageLog.error_kind, func.count(UsageLog.id)).filter(
        UsageLog.created_at >= since).group_by(UsageLog.model, UsageLog.error_kind).all()
    counts = {}
    for model, kind, count in grouped:
        if not model:
            continue
        counts.setdefault(model, Counter())["success" if kind == "" else kind or "unknown"] += count
    result = []
    waf = POOL.waf.remaining()
    healthy = [a for a in accounts if a.status == "active" and not _looks_login_dead(a, now)
               and all(getattr(a, field) is None or getattr(a, field) <= now
                       for field in ("cool_until", "breaker_until", "degrade_until"))]
    for model in sorted(set(configs) | set(counts) | {r.model for r in limits}):
        config = configs.get(model)
        free = config is not None and config.credit_multiplier == 0
        cooling = [r for r in limits if r.model == model]
        blocked = {r.account_id for r in cooling}
        eligible = [a for a in healthy if a.id not in blocked and (free or (a.balance_remain or 0) > 0)]
        available = [a for a in eligible if not POOL.inflight.full(a.uid or "", settings.MAX_IN_FLIGHT)]
        distribution = counts.get(model, Counter())
        total = sum(distribution.values())
        ok = distribution.get("success", 0) + distribution.get("", 0)
        # Percentiles describe the latest bounded sample; counts use the whole window.
        samples = db.query(UsageLog.ttfb_ms, UsageLog.latency_ms, UsageLog.total_tokens,
                           UsageLog.completion_tokens).filter(
            UsageLog.model == model, UsageLog.created_at >= since,
            UsageLog.error_kind.in_(("", "success"))).order_by(
            UsageLog.created_at.desc(), UsageLog.id.desc()).limit(2000).all()
        # 吞吐 TPS：**completion_tokens / 纯解码耗时**，与逐条日志、DSH 同口径。
        #
        # 这里原来写的是「总 Token / 端到端耗时」，两个都错，而且错得离谱：
        #   · 分子用 total_tokens（含 prompt）—— 命中缓存的 1 万 prompt 被当成
        #     「生成量」，直接把 TPS 放大一个数量级（面板 300~500 vs 真实 100~200）；
        #   · 分母用整段 latency —— 首包等待（常常 10~20s）被算成解码时间。
        # 现在改成与 `bench_router` / 前端耗时列完全相同的口径，并且剔除
        # 「整包突发」样本：解码段 < 300ms 或折算超过 300 tok/s 的，说明上游
        # 一次性把整段吐出来了，那个窗口量不出真实出字速度，宁可不计入。
        tps_values = []
        for _ttfb, _latency, _total, _completion in samples:
            if not _completion or not _latency:
                continue
            _dec = int(_latency) - int(_ttfb or 0)
            if _dec < 300:
                continue
            _v = float(_completion) * 1000.0 / _dec
            if 0 < _v <= 300:
                tps_values.append(round(_v, 1))
        tps_stats = _percentiles(tps_values)
        if tps_values:
            tps_stats["mean"] = round(sum(tps_values) / len(tps_values), 1)
            tps_stats["samples"] = len(tps_values)
        else:
            tps_stats["mean"] = None
            tps_stats["samples"] = 0
        result.append({"model": model, "enabled": config is not None, "free": free,
                       "eligible_accounts": len(eligible),
                       "available_accounts": len(available) if not waf and config else 0,
                       "limited_accounts": len(blocked),
                       "earliest_model_recovery": min((r.until for r in cooling), default=None),
                       "limit_reasons": dict(Counter(r.kind for r in cooling)),
                       "requests": total, "successes": ok, "failures": total - ok,
                       "success_rate": round(ok / total, 4) if total else None,
                       "failure_rate": round((total - ok) / total, 4) if total else None,
                       "failure_reasons": {k: v for k, v in distribution.items() if k not in ("", "success")},
                       "ttfb_ms": _percentiles([r[0] for r in samples]),
                       "latency_ms": _percentiles([r[1] for r in samples]),
                       "tps": tps_stats,
                       "latency_sample_count": len(samples)})
    for item in result:
        reset = item["earliest_model_recovery"]
        item["earliest_model_recovery"] = reset.isoformat() + "Z" if reset else None
    return {"items": result, "usage_scope": "gateway_only", "range_hours": hours,
            "risk": risk_health(db, now),
            "observed_at": now.isoformat() + "Z", "waf_remaining_seconds": waf,
            "latency_sample_limit": 2000, "daily_quota_remaining": None,
            "runtime_scope": "process_local"}