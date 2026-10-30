"""使用记录统计：平台全部 Key 的请求数 / Token / 积分 / 耗时聚合，供前端图表使用。

数据源是 usage_logs（由代理网关逐请求写入）。全部聚合在 SQL 侧完成，
不把明细拉到 Python 里再算，避免日志量大时接口变慢。

统计口径：
- 只统计成功请求（error_kind 为空或 'success'）的 token / 积分，
  失败请求若也计进去会虚高消费；
- 请求数则统计全部调用（含失败），用于反映真实负载。
"""
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, case
from sqlalchemy.orm import Session

from admin.db import get_db
from admin.models import Account, ApiKey, UsageLog
from admin.platform import scope as platform_scope
from admin.security import require_admin

router = APIRouter(prefix="/api/stats", tags=["stats"])

#: 视为「成功」的 error_kind 取值（空串 = 历史数据未分类，同样按成功计）
_OK_KINDS = ("", "success")


@router.get("/model-health")
def model_health_stats(hours: int = Query(default=24, ge=1, le=168),
                       _: bool = Depends(require_admin), db: Session = Depends(get_db)):
    from admin.health import model_health
    return model_health(db, hours)


@router.get("/model-pool")
def model_pool_stats(model: str = Query(default=""),
                     _: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """账号池 × 模型 的当日使用面：哪些号能用、用了多少、日额度是否耗尽。

    口径与估算
    ----------
    * 「今天」按**中国时区自然日**（展示口径，与使用记录一致）。
    * 今日已用 = 当日成功请求（error_kind 空/success）的 total_tokens 之和，
      按账号 × 模型聚合。
    * **上游 6004/6008 的「日额度」是滚动 24 小时窗口**（2026-10-24 实测：
      9 个打满账号在打满时刻回看 24h 的用量聚在 199.3M~202.1M），
      恢复时间 = 最早越界用量滑出窗口的时刻 —— 不是自然日重置。
    * 日额度耗尽 = AccountModelCooldown 里该模型 kind=model_daily 且仍在
      生效的账号（6004/6008 的确定性负缓存，带上游恢复时间）。
    * 单账号日额度估算 = 「已经打满的账号」在**打满时刻回看 24h** 的用量
      平均值（与上游同口径）；样本不足 1 个时返回 null（前端显示「未知」），
      不瞎猜。按自然日取样会严重低估（窗口错位）。
    * 剩余额度估算 = Σ max(0, 单账号日额度 - 近 24h 已用)，只算当前可用的号。
      这是**本地推断值**，不是上游承诺；真实额度以上游为准。
    """
    from admin.models import AccountModelCooldown, ModelConfig
    from admin.model_limits import next_cn_midnight
    from admin.routers.proxy import _looks_login_dead
    from admin.pool import POOL
    from admin.config import settings

    now = datetime.utcnow()
    today_start = next_cn_midnight(now) - timedelta(days=1)

    enabled = [m.model_id for m in db.query(ModelConfig).filter(
        ModelConfig.level == "system", ModelConfig.enabled == 1).order_by(ModelConfig.model_id).all()]
    usage_models = [m for (m,) in db.query(UsageLog.model).filter(
        UsageLog.created_at >= today_start, UsageLog.model != "").distinct().all()]
    known = sorted(set(enabled) | set(usage_models))
    if not model:
        # 不带 model：返回模型清单 + 各模型当日概要（供选择器与卡片）。
        counts = dict(db.query(UsageLog.model, func.count(UsageLog.id)).filter(
            UsageLog.created_at >= today_start, UsageLog.error_kind.in_(_OK_KINDS),
            UsageLog.model != "").group_by(UsageLog.model).all())
        tokens = dict(db.query(UsageLog.model, func.coalesce(func.sum(UsageLog.total_tokens), 0)).filter(
            UsageLog.created_at >= today_start, UsageLog.error_kind.in_(_OK_KINDS),
            UsageLog.model != "").group_by(UsageLog.model).all())
        cooled = db.query(AccountModelCooldown).filter(
            AccountModelCooldown.until > now, AccountModelCooldown.kind == "model_daily").all()
        exhausted = {}
        for row in cooled:
            exhausted[row.model] = exhausted.get(row.model, 0) + 1
        return {"models": [{"model": m, "enabled": m in enabled,
                            "requests_today": int(counts.get(m, 0)),
                            "tokens_today": int(tokens.get(m, 0)),
                            "exhausted_accounts": exhausted.get(m, 0)} for m in known],
                "today_start": today_start.isoformat() + "Z"}

    accounts = db.query(Account).order_by(Account.id.asc()).all()
    cooldowns = {row.account_id: row for row in db.query(AccountModelCooldown).filter(
        AccountModelCooldown.model == model, AccountModelCooldown.until > now).all()}
    usage = {int(acc_id): (int(tokens or 0), int(count or 0))
             for acc_id, tokens, count in db.query(UsageLog.account_id,
                                                   func.coalesce(func.sum(UsageLog.total_tokens), 0),
                                                   func.count(UsageLog.id)).filter(
        UsageLog.model == model, UsageLog.created_at >= today_start,
        UsageLog.error_kind.in_(_OK_KINDS)).group_by(UsageLog.account_id).all()}

    # 对照口径：滚动 24 小时（「使用记录」里「近 1 天」的算法）。
    # 自然日 vs 滚动窗的差值会让两个面板看起来「对不上」，把同模型的
    # 滚动窗数字一并返回，前端并排展示，口径差异即可自解释。
    # 上游 6004 的「日额度」同样是滚动 24h 窗口（2026-10-24 实测：9 个打满
    # 账号在打满时刻回看 24h 的用量聚在 199.3M~202.1M），额度估算与剩余
    # 额度必须用同一口径，按自然日取样会严重低估。
    roll_start = now - timedelta(days=1)
    usage_24h = {int(acc_id): (int(tokens or 0), int(count or 0))
                 for acc_id, tokens, count in db.query(UsageLog.account_id,
                                                       func.coalesce(func.sum(UsageLog.total_tokens), 0),
                                                       func.count(UsageLog.id)).filter(
        UsageLog.model == model, UsageLog.created_at >= roll_start,
        UsageLog.error_kind.in_(_OK_KINDS)).group_by(UsageLog.account_id).all()}
    roll_tokens = sum(t for t, _ in usage_24h.values())
    roll_reqs = sum(c for _, c in usage_24h.values())

    rows = []
    exhausted_used = []
    config = db.query(ModelConfig).filter(
        ModelConfig.model_id == model, ModelConfig.level == "system").first()
    free = config is not None and config.credit_multiplier == 0
    for a in accounts:
        row = cooldowns.get(a.id)
        daily = row is not None and row.kind == "model_daily"
        used_today, requests_today = usage.get(a.id, (0, 0))
        used_today, requests_today = int(used_today or 0), int(requests_today or 0)
        used_24h, requests_24h = usage_24h.get(a.id, (0, 0))
        used_24h, requests_24h = int(used_24h or 0), int(requests_24h or 0)
        cooling = any(getattr(a, f) is not None and getattr(a, f) > now
                      for f in ("cool_until", "breaker_until", "degrade_until"))
        # 该模型任何生效中的冷却（日额度/限流/排队/不可用）都意味着
        # 「此账号此刻不能服务此模型」—— 不能只有 model_daily 才排除，
        # 否则限流中的号还计进「可用账号」，与其他面板对不上。
        usable = (a.status == "active" and not cooling and row is None
                  and not _looks_login_dead(a, now)
                  and (free or (a.balance_remain or 0) > 0)
                  and not POOL.inflight.full(a.uid or "", settings.MAX_IN_FLIGHT))
        entry = {"id": a.id, "name": a.name or a.uid or f"#{a.id}",
                 "uid": (a.uid or "")[:12], "status": a.status,
                 "usable": usable, "cooling": cooling,
                 "daily_exhausted": daily, "daily_reset_at": None,
                 "limit_kind": row.kind if row is not None and not daily else "",
                 "used_today_tokens": used_today, "requests_today": requests_today,
                 "used_24h_tokens": used_24h, "requests_24h": requests_24h,
                 "balance_remain": a.balance_remain or 0,
                 "last_used_at": a.last_used_at.isoformat() if a.last_used_at else None}
        if daily:
            entry["daily_reset_at"] = row.until.isoformat() + "Z"
            # 额度样本：打满时刻回看 24h 的成功用量（与上游 6004 同口径）。
            # updated_at 之后账号已被冷却挡住，几乎没有新增成功量；
            # 取 min(now, updated_at) 截断，避免误吞解除冷却后的新一轮用量。
            w_start = row.until - timedelta(hours=24)
            w_end = min(now, row.updated_at or now)
            sample = db.query(func.coalesce(func.sum(UsageLog.total_tokens), 0)).filter(
                UsageLog.account_id == a.id, UsageLog.model == model,
                UsageLog.created_at >= w_start, UsageLog.created_at <= w_end,
                UsageLog.error_kind.in_(_OK_KINDS)).scalar() or 0
            exhausted_used.append(int(sample))
        rows.append(entry)

    quota = None
    if exhausted_used:
        # 打满即额度：耗尽账号在滚动 24h 窗口内的用量就是其额度的近似样本。
        quota = int(round(sum(exhausted_used) / len(exhausted_used)))
    for entry in rows:
        entry["est_remaining"] = None if quota is None else max(
            0, quota - entry["used_24h_tokens"]) if entry["usable"] else 0
    usable_rows = [e for e in rows if e["usable"]]
    summary = {
        "model": model, "enabled": config is not None, "free": free,
        "today_start": today_start.isoformat() + "Z",
        "total_accounts": len(rows), "usable_accounts": len(usable_rows),
        "exhausted_accounts": len(exhausted_used),
        "model_limited_accounts": sum(1 for e in rows if e["limit_kind"]),
        "cooling_accounts": sum(1 for e in rows if e["cooling"] and not e["daily_exhausted"]),
        "inactive_accounts": sum(1 for e in rows if e["status"] != "active"),
        "requests_today": sum(e["requests_today"] for e in rows),
        "tokens_today": sum(e["used_today_tokens"] for e in rows),
        "today_elapsed_hours": round((now - today_start).total_seconds() / 3600, 1),
        "rolling_24h": {"tokens": int(roll_tokens), "requests": int(roll_reqs)},
        "est_per_account_quota": quota, "quota_samples": len(exhausted_used),
        "est_total_remaining": (sum(e["est_remaining"] for e in usable_rows)
                                if quota is not None else None),
        "est_pool_capacity": quota * len(usable_rows) if quota is not None else None,
        "earliest_reset": min((e["daily_reset_at"] for e in rows if e["daily_reset_at"]),
                              default=None),
    }
    return {"summary": summary, "accounts": rows,
            "note": "额度为上游滚动 24 小时窗口口径（6004 实测：打满时刻回看 24h 的用量即单账号额度，"
                    "恢复时间 = 最早越界用量滑出窗口的时刻）；剩余额度 = 估算额度 − 近 24h 已用。"
                    "「今日已用」= 北京时间 00:00 起的自然日，与「使用记录→今日（自然日）」同口径。"
                    "额度与剩余为按已打满账号样本的本地推断值，非上游承诺"}


def _window(days: int) -> datetime:
    """返回统计窗口起点（UTC，与 usage_logs.created_at 存的口径一致）。"""
    return datetime.utcnow() - timedelta(days=max(1, days))


def _time_filters(days: int, start: Optional[datetime], end: Optional[datetime]):
    """Explicit bounds override the rolling window, using the logs' UTC clock."""
    def utc(value):
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value and value.tzinfo else value
    start, end = utc(start), utc(end)
    if start and end and start > end:
        raise HTTPException(400, "开始日期不能晚于结束日期")
    filters = []
    if start is not None:
        filters.append(UsageLog.created_at >= start)
    elif end is None:
        filters.append(UsageLog.created_at >= _window(days))
    if end is not None:
        filters.append(UsageLog.created_at <= end)
    return filters


def _ok_filter(q):
    return q.filter(UsageLog.error_kind.in_(_OK_KINDS))


def platform_labels(db: Session) -> dict[str, str]:
    """落库平台名 -> 展示名（含国内/国际两个区的区分）。

    `usage_logs.platform` 存的是**落库名**（registry 里的 storage 平台），
    而界面要的是产品名 + 区域（例如「某产品 国内版 / 国际版」）。
    这里一次性把 registry 里的 id 与 storage_platform 都映射到 label，
    两个区共用一个 storage 时用「id 优先」的写法兜底。
    """
    from admin.platform import registry

    labels: dict[str, str] = {}
    for spec in registry.all_platforms(enabled_only=False):
        label = getattr(spec, "label", "") or spec.id
        realms = getattr(spec, "realms", None) or []
        storage = spec.storage()
        if realms:
            for realm in realms:
                key = "%s-%s" % (storage, realm.key)
                labels.setdefault(key, "%s %s" % (label, realm.label))
            # 没有区信息的老行只显示产品名，不冒充某个区
            labels.setdefault(storage, label)
        else:
            labels.setdefault(storage, label)
            labels.setdefault(spec.id, label)
    labels.setdefault("", "未知平台")
    return labels


def _platform_filter(filters: list, db: Session, platform: str, realm: str) -> None:
    """按平台收窄 where（就地追加）。

    两条来源必须**取并集**：
      · `usage_logs.platform` —— 写入时就带着，能覆盖「没有账号」的失败请求
        （准入拒绝 / 无号 503 / 超时）。只按 account_id 筛会把这类行全部漏掉，
        用户在日志里看不到 503 就是这么来的；
      · `account_id in (该平台账号)` —— 兼容历史行（platform 列为空）。
    """
    from sqlalchemy import or_

    from admin.platform import registry

    values = {platform}
    for spec in registry.all_platforms(enabled_only=False):
        if spec.storage() == platform:
            values.add(spec.id)
    scope_ids = platform_scope.account_ids(db, platform, realm)
    clauses = [UsageLog.platform.in_(sorted(values))]
    if scope_ids:
        clauses.append(UsageLog.account_id.in_(scope_ids))
    filters.append(or_(*clauses))


def _model_display_map(db: Session) -> dict[str, str]:
    """`model_id -> 展示名`（含跨平台别名归并）。

    为什么统计也要映射：Qoder 的用法日志里模型列存的是内部 id
    （`dfmodel` / `kmodel`），图表上显示出来用户根本不知道是哪个模型。
    这里借用聚合用的归一逻辑，把 `dfmodel` 显示成 `DeepSeek-V4.1-Flash`。
    """
    from admin.model_alias import canonical_key, display_name, model_display
    from admin.models import ModelConfig

    out: dict[str, str] = {}
    try:
        rows = (db.query(ModelConfig.model_id, ModelConfig.upstream_metadata)
                .filter(ModelConfig.enabled == 1).all())
    except Exception:  # noqa: BLE001 - 映射失败就显示原始 id
        return out
    for model_id, metadata in rows:
        model_id = str(model_id or "")
        if not model_id or model_id in out:
            continue
        display = model_display(metadata)
        key = canonical_key(model_id, display)
        out[model_id] = display_name(key, display or model_id)
    return out


#: 使用记录聚合的短缓存：一次请求要跑 8 个分组聚合（概览/模型/端点/Key/
#: 账号/平台/结果/趋势），日志一大就是几十万行的扫描。控制台切换筛选、
#: 图表重绘、多标签页同时开着时，同一份统计会被反复算 —— 6 秒的缓存
#: 能把这类"重复劳动"直接消掉，而观测数据的 6 秒滞后没有实际影响。
_STATS_TTL = 6.0
_stats_cache: dict[tuple, tuple[float, dict]] = {}


@router.get("/usage")
def usage_stats(
    days: int = 1,
    granularity: str = "hour",  # day | hour
    key_id: Optional[int] = None,
    model: Optional[str] = None,
    account_id: Optional[int] = None,
    use_case: Optional[str] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    request: Request = None,
    _: bool = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """使用记录总览：概览卡片 + 模型分布 + 分组分布 + 端点分布 + 趋势。

    默认 **近 1 天 / 按小时**：这是「今天用得怎么样」最常见的诉求，
    默认 14 天会把当天的问题稀释在两周的曲线里，看不出异常。

    支持按 `key_id` / `model` / `account_id` / `use_case` 筛选：
    筛选后**所有**图与卡片都跟着变（同一份 where 条件），
    这样「某个 Key 今天花了多少」是直接可读的，不用自己心算做减法。

    **默认全网关共享**：不带 `?platform=` 时统计**所有平台**的流量。

    这与「账号模块跟随当前平台」是刻意分开的两套口径：

        账号池 / 积分到期 / 积分流水 / 成长计划  -> 跟随当前平台（管的是那家的号）
        使用记录 / 用量日志 / 风控哨兵           -> 全网关共享（看的是整体流量）

    之前这里读 `X-Platform` 头，控制台每个请求都带 —— 于是切平台后
    统计只剩那一家，「全网关」的观察意义就没了。现在只认显式的 `?platform=` 查询参数。
    """
    # ── 统一筛选条件 ────────────────────────────────────────────────────
    # 只构造一次，保证概览/分布/趋势三个口径完全一致 ——
    # 分开写很容易出现「卡片数字和图表对不上」的经典问题。
    filters = _time_filters(days, start, end)
    if key_id is not None:
        filters.append(UsageLog.api_key_id == key_id)
    if model:
        filters.append(UsageLog.model == model)
    if account_id is not None:
        filters.append(UsageLog.account_id == account_id)
    if use_case:
        filters.append(UsageLog.use_case == use_case)

    # 只认**显式**的 ?platform= （用户主动筛选某个平台时才用）。
    # 不看 X-Platform 头 —— 那是控制台的全局上下文，不该切碎观测数据。
    platform = ""
    realm = ""
    try:
        platform = (request.query_params.get("platform") or "").strip().lower()
        realm = (request.query_params.get("realm") or "").strip().lower()
        if platform in ("all", "*"):
            platform = ""
    except Exception:  # noqa: BLE001 - 测试桩可能没有 query_params
        platform = ""

    # ── 短缓存：同一组筛选条件 6 秒内只算一次 ────────────────────────────
    force = False
    try:
        force = (request.query_params.get("refresh") or "") == "1"
    except Exception:  # noqa: BLE001
        force = False
    cache_key = (days, granularity, key_id, model, account_id, use_case,
                 start.isoformat() if start else "", end.isoformat() if end else "",
                 platform, realm)
    now_ts = time.time()
    if not force:
        cached = _stats_cache.get(cache_key)
        if cached and now_ts - cached[0] < _STATS_TTL:
            return cached[1]

    if platform:
        _platform_filter(filters, db, platform, realm)

    labels = platform_labels(db)
    model_labels = _model_display_map(db)

    def _f(q):
        return q.filter(*filters)

    # ── 概览 ────────────────────────────────────────────────────────────
    ov = _ok_filter(
        _f(db.query(
            func.count(UsageLog.id),
            func.coalesce(func.sum(UsageLog.credits), 0.0),
            func.coalesce(func.sum(UsageLog.prompt_tokens), 0),
            func.coalesce(func.sum(UsageLog.completion_tokens), 0),
            func.coalesce(func.sum(UsageLog.total_tokens), 0),
            func.coalesce(func.sum(UsageLog.cached_tokens), 0),
            func.avg(UsageLog.latency_ms),
        ))
    ).one()

    total_req, credits, ptok, ctok, ttok, cached, avg_latency = ov
    # 总请求数按全部调用计（含失败），与图表口径区分
    all_req = _f(db.query(func.count(UsageLog.id))).scalar() or 0
    ok_req = int(total_req or 0)
    fail_req = max(0, int(all_req or 0) - ok_req)

    # ── 模型分布（按 模型 × 平台 分组）────────────────────────────────────
    # 为什么要带上平台：同一个模型名在各平台是**不同权益**（同名模型在不同
    # 平台是两笔账，倍率也不同）。只按 model 分组会把它们合成一行，
    # 看不出「这个模型到底在哪个平台烧的积分」。
    # 前端在「已选平台」时把平台列隐藏，所以多出来的维度不会变噪音。
    by_model = (
        _ok_filter(
            _f(db.query(
                UsageLog.model,
                UsageLog.platform,
                func.count(UsageLog.id),
                func.coalesce(func.sum(UsageLog.total_tokens), 0),
                func.coalesce(func.sum(UsageLog.credits), 0.0),
            ))
        )
        .group_by(UsageLog.model, UsageLog.platform)
        .order_by(func.coalesce(func.sum(UsageLog.credits), 0.0).desc())
        .all()
    )

    # ── 平台分布（每个平台这一窗口的请求 / Token / 积分）─────────────────
    by_platform_rows = (
        _ok_filter(
            _f(db.query(
                UsageLog.platform,
                func.count(UsageLog.id),
                func.coalesce(func.sum(UsageLog.total_tokens), 0),
                func.coalesce(func.sum(UsageLog.credits), 0.0),
            ))
        )
        .group_by(UsageLog.platform)
        .order_by(func.coalesce(func.sum(UsageLog.credits), 0.0).desc())
        .all()
    )

    # ── 端点分布 ────────────────────────────────────────────────────────
    # use_case 由网关写入（如 responses / chat / messages），作为"端点"维度
    by_endpoint = (
        _ok_filter(
            _f(db.query(
                UsageLog.use_case,
                func.count(UsageLog.id),
                func.coalesce(func.sum(UsageLog.total_tokens), 0),
                func.coalesce(func.sum(UsageLog.credits), 0.0),
            ))
        )
        .group_by(UsageLog.use_case)
        .order_by(func.coalesce(func.sum(UsageLog.credits), 0.0).desc())
        .all()
    )

    # ── 按 Key 分组分布（对应参考图的"分组使用分布"）────────────────────
    by_key = (
        _ok_filter(
            _f(db.query(
                UsageLog.api_key_id,
                func.count(UsageLog.id),
                func.coalesce(func.sum(UsageLog.total_tokens), 0),
                func.coalesce(func.sum(UsageLog.credits), 0.0),
            ))
        )
        .group_by(UsageLog.api_key_id)
        .order_by(func.coalesce(func.sum(UsageLog.credits), 0.0).desc())
        .all()
    )
    key_names = {k.id: k.name for k in db.query(ApiKey).all()}
    account_names = {a.id: a.name or a.uid or f"账号 #{a.id}"
                     for a in db.query(Account).all()}
    by_account = (_ok_filter(_f(db.query(
        UsageLog.account_id, func.count(UsageLog.id),
        func.coalesce(func.sum(UsageLog.total_tokens), 0),
        func.coalesce(func.sum(UsageLog.credits), 0.0))))
        .group_by(UsageLog.account_id)
        .order_by(func.count(UsageLog.id).desc()).all())
    by_result = (_f(db.query(UsageLog.error_kind, func.count(UsageLog.id)))
                 .group_by(UsageLog.error_kind).order_by(func.count(UsageLog.id).desc()).all())
    account_models = []
    if account_id is not None:
        from admin.routers.accounts import _active_model_limits
        limits = _active_model_limits(db, datetime.utcnow()).get(account_id, [])
        limit_map = {entry['model']: entry for entry in limits}
        ok = UsageLog.error_kind.in_(_OK_KINDS)
        rows = (_f(db.query(UsageLog.model, func.count(UsageLog.id),
                           func.sum(case((ok, 1), else_=0)),
                           func.sum(case((ok, UsageLog.total_tokens), else_=0)),
                           func.sum(case((ok, UsageLog.credits), else_=0))))
                .group_by(UsageLog.model).order_by(func.count(UsageLog.id).desc()).all())
        for mid, count, successes, tokens, used in rows:
            account_models.append({'model': mid, 'requests': count,
                                   'successes': int(successes or 0),
                                   'failures': count - int(successes or 0),
                                   'tokens': int(tokens or 0), 'credits': round(float(used or 0), 4),
                                   'limit': limit_map.pop(mid, None)})
        # Keep a current limit visible even if this date window has no calls.
        for mid, entry in limit_map.items():
            if not model or model == mid:
                account_models.append({'model': mid, 'requests': 0, 'successes': 0,
                                       'failures': 0, 'tokens': 0, 'credits': 0, 'limit': entry})

    # ── 趋势（按天/按小时分桶）──────────────────────────────────────────
    if db.get_bind().dialect.name == "sqlite":
        bucket = func.strftime("%Y-%m-%d %H:00" if granularity == "hour" else "%Y-%m-%d",
                               UsageLog.created_at)
    elif granularity == "hour":
        bucket = func.date_format(UsageLog.created_at, "%Y-%m-%d %H:00")
    else:
        bucket = func.date_format(UsageLog.created_at, "%Y-%m-%d")
    trend_rows = (
        _ok_filter(
            _f(db.query(
                bucket.label("b"),
                func.coalesce(func.sum(UsageLog.prompt_tokens), 0),
                func.coalesce(func.sum(UsageLog.completion_tokens), 0),
                func.coalesce(func.sum(UsageLog.cached_tokens), 0),
                func.coalesce(func.sum(UsageLog.credits), 0.0),
                func.count(UsageLog.id),
            ))
        )
        .group_by("b")
        .order_by("b")
        .all()
    )

    def _dist(rows, label_fn):
        return [
            {
                "label": label_fn(r[0]),
                "requests": int(r[1] or 0),
                "tokens": int(r[2] or 0),
                "credits": round(float(r[3] or 0), 4),
            }
            for r in rows
        ]

    def _platform_label(value: str) -> str:
        return labels.get((value or "").lower()) or (value or "未知平台")

    result = {
        "range_days": None if start or end else days,
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "granularity": granularity,
        "filters": {
            "key_id": key_id,
            "model": model,
            "account_id": account_id,
            "use_case": use_case,
            "platform": platform,
        },
        "platform": platform,
        "platform_label": _platform_label(platform) if platform else "全部平台",
        "platforms": [
            {
                "value": (r[0] or "").lower(),
                "label": _platform_label(r[0]),
                "requests": int(r[1] or 0),
                "tokens": int(r[2] or 0),
                "credits": round(float(r[3] or 0), 4),
            }
            for r in by_platform_rows
        ],
        "overview": {
            "requests": int(all_req or 0),
            "ok_requests": ok_req,
            "failed_requests": fail_req,
            "credits": round(float(credits or 0), 4),
            "prompt_tokens": int(ptok or 0),
            "completion_tokens": int(ctok or 0),
            "total_tokens": int(ttok or 0),
            "cached_tokens": int(cached or 0),
            "avg_latency_ms": int(avg_latency) if avg_latency else 0,
        },
        "by_model": [
            {
                # 展示名优先（dfmodel -> DeepSeek-V4.1-Flash）；
                # 原始 id 仍留在 `model` 字段，供筛选与排障使用。
                "label": model_labels.get(r[0] or "") or (r[0] or "未知"),
                "model": r[0] or "",
                "platform": (r[1] or "").lower(),
                "platform_label": _platform_label(r[1]),
                "requests": int(r[2] or 0),
                "tokens": int(r[3] or 0),
                "credits": round(float(r[4] or 0), 4),
            }
            for r in by_model
        ],
        "by_endpoint": _dist(by_endpoint, lambda v: v or "chat"),
        "by_key": _dist(by_key, lambda v: key_names.get(v) or f"#{v}"),
        "by_account": [{**item, "account_id": row[0]} for row, item in zip(
            by_account, _dist(by_account, lambda v: account_names.get(v) or
                             (f"已删除账号 #{v}" if v else "未分配账号")))],
        "by_result": [{"label": "成功" if kind in _OK_KINDS else kind or "未分类",
                       "requests": int(count)} for kind, count in by_result],
        "account_models": account_models,
        "usage_scope": "gateway_only",
        "trend": [
            {
                "bucket": r[0],
                "prompt_tokens": int(r[1] or 0),
                "completion_tokens": int(r[2] or 0),
                "cached_tokens": int(r[3] or 0),
                "credits": round(float(r[4] or 0), 4),
                "requests": int(r[5] or 0),
            }
            for r in trend_rows
        ],
    }
    if len(_stats_cache) > 64:
        _stats_cache.clear()
    _stats_cache[cache_key] = (now_ts, result)
    return result


@router.get("/usage/options")
def usage_filter_options(
    days: int = 30,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    _: bool = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """筛选下拉的可选项：实际用过的 Key / 模型 / 端点。

    只返回**窗口内真实出现过**的值，避免下拉里塞一堆从没用过的模型，
    让人以为「有数据」却筛出空结果。
    """
    filters = _time_filters(days, start, end)

    model_labels = _model_display_map(db)
    models = [
        {
            # value = 落库的原始 id（筛选要用它）；label = 展示名（给用户看）
            "value": r[0],
            "label": model_labels.get(r[0]) or r[0],
            "requests": int(r[1] or 0),
        }
        for r in
        db.query(UsageLog.model, func.count(UsageLog.id))
        .filter(*filters, UsageLog.model.isnot(None))
        .group_by(UsageLog.model)
        .order_by(func.count(UsageLog.id).desc())
        .all() if r[0]
    ]
    cases = [
        r[0] for r in
        db.query(UsageLog.use_case)
        .filter(*filters, UsageLog.use_case.isnot(None))
        .group_by(UsageLog.use_case)
        .order_by(func.count(UsageLog.id).desc())
        .all() if r[0]
    ]
    keys = [
        {"id": k.id, "name": k.name}
        for k in db.query(ApiKey).order_by(ApiKey.id.asc()).all()
    ]
    account_names = {a.id: a.name or a.uid or f"账号 #{a.id}" for a in db.query(Account).all()}
    accounts = [{"id": aid, "name": account_names.get(aid) or f"已删除账号 #{aid}"}
                for (aid,) in db.query(UsageLog.account_id).filter(*filters, UsageLog.account_id.isnot(None))
                .group_by(UsageLog.account_id).order_by(func.count(UsageLog.id).desc()).all()]
    seen = {a['id'] for a in accounts}
    accounts.extend({'id': aid, 'name': name} for aid, name in account_names.items() if aid not in seen)
    # 平台下拉：只给窗口内**真的出现过**的平台（含空串 = 历史行没记录平台），
    # 这样筛选下拉不会塞一堆「选了必然 0 条」的项。
    labels = platform_labels(db)
    platform_rows = (
        db.query(UsageLog.platform, func.count(UsageLog.id))
        .filter(*filters)
        .group_by(UsageLog.platform)
        .order_by(func.count(UsageLog.id).desc())
        .all()
    )
    platforms = [
        {
            "value": (value or "").lower(),
            "label": labels.get((value or "").lower()) or (value or "历史（未记录平台）"),
            "requests": int(count or 0),
        }
        for value, count in platform_rows
    ]
    return {"models": models, "use_cases": cases, "keys": keys,
            "accounts": accounts, "platforms": platforms}


@router.get("/memory")
def memory_stats(_: bool = Depends(require_admin)):
    """进程内存现状：RSS + 各内存缓存的实际条目数 / 上限。

    只读观测，不清理任何东西 —— 想对比「基线 → 压测 → 回落」时，
    直接隔一段时间各取一次快照即可。缓存条目数贴到上限就说明该调大容量
    或缩短 TTL，而不是继续往上堆。
    """
    from admin import process_memory
    return process_memory.snapshot()