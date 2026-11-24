"""使用记录统计：平台全部 Key 的请求数 / Token / 积分 / 耗时聚合，供前端图表使用。

数据源是 usage_logs（由代理网关逐请求写入）。全部聚合在 SQL 侧完成，
不把明细拉到 Python 里再算，避免日志量大时接口变慢。

统计口径：
- 只统计成功请求（error_kind 为空或 'success'）的 token / 积分，
  失败请求若也计进去会虚高消费；
- 请求数则统计全部调用（含失败），用于反映真实负载。
"""
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import and_, func, case, or_
from sqlalchemy.orm import Session

from admin.db import get_db
from admin.models import Account, ApiKey, UsageLog
from admin.platform import scope as platform_scope
from admin.security import require_admin

logger = logging.getLogger("admin.stats")

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

    2026-11-07 加共享缓存（10s）：这条要全量扫账号表 + 冷却表 + 两个
    当日/24h 聚合，此前完全没有缓存，模型池面板每开一次就是一整套。
    """
    def _compute():
        return _model_pool_stats_agg(db, model)

    return _shared_cached(_shared_cache_key("model_pool", (model,)), _compute, ttl=10.0)


def _model_pool_stats_agg(db: Session, model: str):
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
                  and not POOL.inflight.full(POOL.key_for(a), POOL.limit_for(a)))
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


def _strip_realm_suffix(label: str) -> str:
    """`Trae 国内版` -> `Trae`，`Qoder 中国版` -> `Qoder`（落库名标签用）。"""
    base = str(label or "")
    for suffix in (" 国内版", " 国际版", " 中国版", "国内版", "国际版", "中国版"):
        if base.endswith(suffix):
            return base[: -len(suffix)].strip()
    return base


def platform_labels(db: Session) -> dict[str, str]:
    """落库平台名 / 对外 id -> 展示名。

    `usage_logs.platform` 与汇总表存的是**落库名**（`trae` / `qoder`），一个落库名
    可能对应两个区（`trae-cn` + `trae-intl`）。原实现用 `setdefault(storage, label)`
    让 `trae` 取到第一个登记的「Trae 国内版」—— 国际版的流量于是全部显示成
    国内版（platform_scoping_review P1-6 / N4）。

    现在：
      * 对外 id（`trae-cn` / `qoder-intl`）-> 各自的标签；
      * 多区共用的落库名 -> 「产品名（两区合计）」，**不冒充任何一个区**；
      * 单区平台落库名 = 对外 id，标签就是平台名。
    `qoder` 既是落库名又是中国区 id：日志里的 `qoder` 行是两区合计，按落库名语义标注。
    """
    from admin.platform import registry

    labels: dict[str, str] = {}
    by_storage: dict[str, list] = {}
    for spec in registry.all_platforms(enabled_only=False):
        label = getattr(spec, "label", "") or spec.id
        labels.setdefault(str(spec.id).lower(), label)
        by_storage.setdefault(str(spec.storage() or "").lower(), []).append(spec)
    for storage, specs in by_storage.items():
        if not storage:
            continue
        if len(specs) > 1:
            base = _strip_realm_suffix(getattr(specs[0], "label", "") or storage)
            labels[storage] = "%s（两区合计）" % (base or storage)
        else:
            labels.setdefault(storage, getattr(specs[0], "label", "") or storage)
    labels.setdefault("", "未知平台")
    return labels


def _platform_filter(filters: list, db: Session, platform: str, realm: str) -> None:
    """按平台收窄 where（就地追加）。

    不带区时两条来源**取并集**：
      · `usage_logs.platform` —— 写入时就带着，能覆盖「没有账号」的失败请求
        （准入拒绝 / 无号 503 / 超时）。只按 account_id 筛会把这类行全部漏掉，
        用户在日志里看不到 503 就是这么来的；
      · `account_id in (该平台账号)` —— 兼容历史行（platform 列为空）。

    **带区时（`realm=intl`）不能再并 `platform='trae'`**：落库名是两区合计，
    并进来区筛选就失效了（P1-6 / N4）。短期口径：只认
    「该区的账号」+「platform 列恰好写着该区对外 id 的行」。区信息只在账号上，
    `account_id=0` 的失败行在区筛选下看不到 —— 中期要给 usage_logs 加 realm 列。
    """
    from sqlalchemy import or_

    from admin.platform import registry

    # 允许直接传对外 id（`trae-intl`）：先归一成 (落库名, 区)。
    parsed_platform, parsed_realm = platform_scope.parse(platform)
    if parsed_platform:
        platform = parsed_platform
        realm = realm or parsed_realm
    realm = (realm or "").strip().lower()
    specs = [spec for spec in registry.all_platforms(enabled_only=False)
             if str(spec.storage() or "").lower() == platform]
    scope_ids = platform_scope.account_ids(db, platform, realm)
    if realm:
        values = {str(spec.id).lower() for spec in specs
                  if (spec.realm or "") == realm and str(spec.id).lower() != platform}
    else:
        values = {platform} | {str(spec.id).lower() for spec in specs}
    clauses = []
    if values:
        clauses.append(UsageLog.platform.in_(sorted(values)))
    if scope_ids:
        clauses.append(UsageLog.account_id.in_(scope_ids))
    if not clauses:
        # 指定了平台 / 区却一个来源都没有：必须是空结果，不能退化成「不过滤」。
        filters.append(UsageLog.id < 0)
        return
    filters.append(or_(*clauses))


def _floor_time(value, step: int):
    """把时间戳**向下**取整到 `step` 秒（缓存键稳定用；保留 tzinfo）。

    前端每次传的 `start` 都精确到秒（甚至毫秒），原样进缓存键就永远不命中、
    单飞也合并不了（R1 6.2 / P8）。向下取整只会让窗口最多早 `step` 秒，
    统计口径可以接受。
    """
    if value is None or not isinstance(value, datetime) or step <= 1:
        return value
    epoch = value.replace(tzinfo=None)
    seconds = epoch.hour * 3600 + epoch.minute * 60 + epoch.second
    floored = seconds - seconds % step
    return value.replace(hour=floored // 3600, minute=(floored % 3600) // 60,
                         second=floored % 60, microsecond=0)


def _ceil_time(value, step: int):
    """向上取整（结束时间用，避免把最后不足 `step` 秒的数据截掉）。"""
    if value is None or not isinstance(value, datetime) or step <= 1:
        return value
    floored = _floor_time(value, step)
    return floored if floored == value else floored + timedelta(seconds=step)


def _model_display_map(db: Session) -> dict[str, str]:
    """`model_id -> 展示名`（含跨平台别名归并）。

    为什么统计也要映射：Qoder 的用法日志里模型列存的是内部 id
    （`dfmodel` / `kmodel`），图表上显示出来用户根本不知道是哪个模型。
    这里借用聚合用的归一逻辑，把 `dfmodel` 显示成 `DeepSeek-V4.1-Flash`。

    ## 槽位名别名（2026-11-17）

    还要把**上游槽位名**（Marvis 的 `main-auto`）也登记进来 —— 历史
    `usage_logs` 行存的是槽位名，而 `model_configs` 里只有可读名
    （`deepseek-v4.1-flash`）。不登记的话统计表 / 下拉筛选里就是
    `main-auto` 一行、`deepseek-v4.1-flash` 另一行，同一模型被拆成两条。

    写入侧已改为落可读名（`marvis/gateway.py::_billing_model`），这里只是
    让历史行也能归到同一组，**不需要批量改库**。
    """
    from admin.model_alias import canonical_key, display_name, model_display
    from admin.models import ModelConfig
    from admin.platform.models_router import MODEL_NAME_MAP

    out: dict[str, str] = {}
    try:
        rows = (db.query(ModelConfig.model_id, ModelConfig.upstream_metadata,
                         ModelConfig.platform)
                .filter(ModelConfig.enabled == 1).all())
    except Exception:  # noqa: BLE001 - 映射失败就显示原始 id
        return out
    for model_id, metadata, platform in rows:
        model_id = str(model_id or "")
        if not model_id:
            continue
        display = model_display(metadata)
        key = canonical_key(model_id, display)
        name = display_name(key, display or model_id)
        out.setdefault(model_id, name)
        # 反向登记：本平台的**上游槽位名**也指向同一个展示名。
        # `MODEL_NAME_MAP[platform]` 是「槽位 → 可读名」，反查即得。
        table = MODEL_NAME_MAP.get(str(platform or "").lower()) or {}
        for slot, readable in table.items():
            if readable and readable == model_id:
                out.setdefault(slot, name)
    return out


#: 使用记录聚合的短缓存：一次请求要跑 8 个分组聚合（概览/模型/端点/Key/
#: 账号/平台/结果/趋势），日志一大就是几十万行的扫描。控制台切换筛选、
#: 图表重绘、多标签页同时开着时，同一份统计会被反复算 —— 短缓存能把这类
#: "重复劳动"直接消掉，而观测数据的短暂滞后没有实际影响。
#: 2026-11-07 起走 admin/stats_cache.py：L1 内存 + L2 Redis + 单飞
#: （此前是裸 dict：并发同条件会各自打一遍库，也没有容量上限）。
from admin.stats_cache import cached as _shared_cached  # noqa: E402
from admin.stats_cache import cache_key as _shared_cache_key  # noqa: E402

_STATS_TTL = 8.0


def _stats_ttl_for_days(days) -> float:
    """按窗口给 TTL：与 logs.py 同策略 —— 窗口越大、数据变化越慢，留得越久。"""
    try:
        d = int(days or 0)
    except (TypeError, ValueError):
        return _STATS_TTL
    if d <= 1:
        return _STATS_TTL
    if d <= 7:
        return 30.0
    return 60.0


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
    # 缓存键量化：start/end 取整到分钟（P8）。
    start = _floor_time(start, 60)
    end = _ceil_time(end, 60)
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

    # ── 短缓存：同一组筛选条件在 TTL 内只算一次（含单飞合并并发） ─────────
    force = False
    try:
        force = (request.query_params.get("refresh") or "") == "1"
    except Exception:  # noqa: BLE001
        force = False
    cache_key = _shared_cache_key("usage", (
        days, granularity, key_id, model, account_id, use_case,
        start.isoformat() if start else "", end.isoformat() if end else "",
        platform, realm))
    if not force:
        hit = _stats_cache_get(cache_key)
        if hit is not None:
            return hit
    ttl = _stats_ttl_for_days(days)

    def _compute():
        if platform:
            _platform_filter(filters, db, platform, realm)
        return _usage_aggregates(db, filters, days=days, granularity=granularity,
                                 key_id=key_id, model=model, account_id=account_id,
                                 use_case=use_case, start=start, end=end,
                                 platform=platform, realm=realm)

    if force:
        # refresh=1：跳过读缓存直接算，但结果照常写入（后续请求受益）。
        result = _compute()
        _stats_cache_put(cache_key, result, ttl)
        return result
    return _shared_cached(cache_key, _compute, ttl)


def _stats_cache_get(key):
    from admin.stats_cache import cache_get as _cg
    hit = _cg(key)
    # L2（Redis）反序列化出来的是纯 JSON 结构；调用方只读，这里浅拷贝顶层
    # 与旧实现（_freeze）行为一致，避免缓存值被调用方增删键污染。
    if isinstance(hit, dict):
        return dict(hit)
    if isinstance(hit, list):
        return list(hit)
    return hit


def _stats_cache_put(key, value, ttl=None):
    from admin.stats_cache import cache_put as _cp
    _cp(key, value, _STATS_TTL if ttl is None else ttl)


def _usage_aggregates(db, filters, *, days, granularity, key_id, model,
                      account_id, use_case, start, end, platform, realm):
    """一次请求的全部聚合（概览/模型/平台/端点/Key/账号/结果/趋势）。

    只做纯 SQL 工作，被 `/usage` 的共享缓存（L1+L2+单飞）包裹 ——
    缓存命中时这个函数根本不会执行。
    """
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
    # 只取三列：整行会把 `auth_json` 这类大字段一起读出来（几百个账号就是几 MB）。
    key_names = {kid: name for kid, name in db.query(ApiKey.id, ApiKey.name).all()}
    account_names = {aid: name or uid or f"账号 #{aid}"
                     for aid, name, uid in db.query(Account.id, Account.name, Account.uid).all()}
    # ── 账号分布：SQL 侧只取 Top 8，其余合并成「其他」────────────────────
    # 池子上千时把几百行明细全量吐给前端，环形图会挤成一坨（图例每行 2.8%
    # 的占比毫无信息量），payload 也跟着膨胀。这里在 SQL 里 LIMIT 取大头，
    # 剩余账号一次 SUM 滚总，前端只画「Top 8 + 其他 N 个账号」。
    _BY_ACCOUNT_TOP = 8
    by_account = (_ok_filter(_f(db.query(
        UsageLog.account_id, func.count(UsageLog.id),
        func.coalesce(func.sum(UsageLog.total_tokens), 0),
        func.coalesce(func.sum(UsageLog.credits), 0.0))))
        .group_by(UsageLog.account_id)
        .order_by(func.count(UsageLog.id).desc())
        .limit(_BY_ACCOUNT_TOP).all())
    by_account_other_row = None
    if len(by_account) >= _BY_ACCOUNT_TOP:
        top_ids = [row[0] for row in by_account if row[0] is not None]
        null_in_top = any(row[0] is None for row in by_account)
        # NULL（未分配账号）不在 Top 里时要并进「其他」；IN 会把 NULL 行
        # 过滤掉，所以必须显式补 is_(None) 分支。
        others_cond = (~UsageLog.account_id.in_(top_ids) if null_in_top
                       else or_(UsageLog.account_id.is_(None),
                                ~UsageLog.account_id.in_(top_ids)))
        others_row = (_ok_filter(_f(db.query(
            func.count(UsageLog.id),
            func.coalesce(func.sum(UsageLog.total_tokens), 0),
            func.coalesce(func.sum(UsageLog.credits), 0.0),
            func.count(func.distinct(UsageLog.account_id)))))
            .filter(others_cond).one())
        if others_row and others_row[0]:
            by_account_other_row = others_row
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
        "by_account": (
            [{**item, "account_id": row[0]} for row, item in zip(
                by_account, _dist(by_account, lambda v: account_names.get(v) or
                                 (f"已删除账号 #{v}" if v else "未分配账号")))]
            + ([{
                "label": f"其他 {int(by_account_other_row[3] or 0)} 个账号",
                "requests": int(by_account_other_row[0] or 0),
                "tokens": int(by_account_other_row[1] or 0),
                "credits": round(float(by_account_other_row[2] or 0), 4),
                "account_id": None,
                "others": True,
            }] if by_account_other_row is not None else [])
        ),
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

    2026-11-07 加共享缓存（30s）：这条要跑 3 个 group_by + 全量 Account 表，
    却在使用记录面板每次切换筛选时都重打一遍；选项晚 30 秒无感知。
    """
    # 下拉选项晚几分钟无感知：start/end 量化到 10 分钟，缓存 120 秒（P8）。
    start = _floor_time(start, 600)
    end = _ceil_time(end, 600)

    def _compute():
        return _usage_filter_options_agg(db, days, start, end)

    return _shared_cached(
        _shared_cache_key("usage_options", (days, start.isoformat() if start else "",
                                            end.isoformat() if end else "")),
        _compute, ttl=120.0)


def _usage_filter_options_agg(db, days, start, end):
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
        {"id": kid, "name": name}
        for kid, name in db.query(ApiKey.id, ApiKey.name).order_by(ApiKey.id.asc()).all()
    ]
    # 只读三列（整行带 auth_json，194 个号就是几 MB 的无用 IO）
    account_names = {aid: name or uid or f"账号 #{aid}"
                     for aid, name, uid in db.query(Account.id, Account.name, Account.uid).all()}
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


# ============================================================================
# 数据看板（对齐 new-api「模型调用分析」一屏的口径）
# ============================================================================
#
# 为什么单开一个接口而不是往 /api/stats/usage 里塞：
#
#   · `/usage` 是「使用记录」页的口径 —— 8 个分组聚合、按积分/请求数画环图，
#     payload 已经不小；看板要的是**另几件事**：RPM/TPM、吞吐（t/s）、
#     每个模型的成功率、按模型堆叠的时间序列、调用次数排行。
#   · 两页的刷新节奏也不同：看板默认「今日 + 按小时」，切 tab 就重算；
#     使用记录页常挂着 7 天/30 天窗口。
#   合成一个接口的结果是两边都变慢、缓存键互相打架。
#
# 口径与 new-api 的对应关系（见 docs/STATS-MODULES.md）：
#
#   总数 / 总积分 / 总 Token / 缓存读取 / 平均 RPM / 平均 TPM
#       -> summary.requests / credits / total_tokens / cached_tokens / rpm / tpm
#   性能健康（成功率 / 平均延迟 / 吞吐）
#       -> summary.success_rate / avg_latency_ms / throughput_tps
#   消耗分布（按模型堆叠的柱/面积图）
#       -> distribution.series（top N 模型 + 其他）
#   调用趋势 / 调用次数分布 / 调用次数排行
#       -> trend.series / trend.total / ranking
#
# 与 /usage 一致的两条铁律：
#   1. 成功口径 = error_kind in ('', 'success')；Token/积分只算成功，
#      请求数算全部（含失败），否则失败率会被人为抹平。
#   2. 一次请求内所有聚合共用同一份 where，卡片与图表不会互相打架。

_DASHBOARD_MAX_HOUR_BUCKETS = 24 * 7


def _as_utc_naive(value):
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value and value.tzinfo else value


def _bucket_expr(db: Session, granularity: str):
    if db.get_bind().dialect.name == "sqlite":
        fmt = "%Y-%m-%d %H:00" if granularity == "hour" else "%Y-%m-%d"
        return func.strftime(fmt, UsageLog.created_at)
    fmt = "%Y-%m-%d %H:00" if granularity == "hour" else "%Y-%m-%d"
    return func.date_format(UsageLog.created_at, fmt)


def _bucket_keys(start: datetime, end: datetime, granularity: str):
    """把时间窗铺成连续桶：没有请求的那一小时也要留在横轴上。

    不补空桶的图会「把 3 天的空白压成一条线」，看起来像服务一直很忙。
    """
    step = timedelta(hours=1) if granularity == "hour" else timedelta(days=1)
    cursor = start.replace(minute=0, second=0, microsecond=0) if granularity == "hour" \
        else start.replace(hour=0, minute=0, second=0, microsecond=0)
    keys = []
    while cursor <= end and len(keys) <= 4000:
        keys.append(cursor.strftime("%Y-%m-%d %H:00" if granularity == "hour" else "%Y-%m-%d"))
        cursor += step
    return keys


@router.get("/dashboard")
def dashboard_stats(
    days: int = Query(default=1, ge=1, le=90),
    granularity: str = "hour",
    platform: str = "",
    model: str = "",
    top_models: int = Query(default=6, ge=1, le=12),
    window: str = Query(default="rolling"),
    _: bool = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """数据看板：一屏拿齐 KPI / 性能健康 / 消耗分布 / 调用分析。

    `window=day` 时按北京时间自然日取窗（与概览同口径）；默认仍是滚动窗口。
    返回体带 `scope: "gateway"`：这是**全网关**口径（不读 X-Platform），
    前端据此在卡片上标注「全网关」。耗时字段单位见 `units`（全部毫秒）。

    `days` 是**滚动窗口**（近 N 个 24 小时），与 `/usage` 的 `days` 一致。
    小时粒度只在 7 天内有效：再长就自动降级成按天 —— 30 天 × 24 桶 = 720 根
    柱子，前端画出来全是细线，没有信息量，还白白多扫一遍。
    """
    granularity = "day" if str(granularity).lower() == "day" else "hour"
    if granularity == "hour" and days > 7:
        granularity = "day"
    platform = (platform or "").strip().lower()
    if platform in ("all", "*"):
        platform = ""
    # 对外 id（`trae-intl`）先归一成 (落库名, 区)：汇总表存的是落库名，
    # 原来直接拿 `trae-intl` 去比，汇总段查不到、原表段又能查到，两段对不上。
    realm = ""
    if platform:
        parsed, parsed_realm = platform_scope.parse(platform)
        if parsed:
            platform, realm = parsed, parsed_realm
            # 单区平台的 realm 没有区分意义，不要因此退化到原表
            if realm and not _storage_has_realms(platform):
                realm = ""
    model = (model or "").strip()
    window = "day" if str(window or "").strip().lower() in ("day", "today", "natural") else "rolling"

    key = _shared_cache_key("dashboard", (days, granularity, platform, realm, model,
                                          top_models, window))
    ttl = _stats_ttl_for_days(days)
    return _shared_cached(
        key,
        lambda: _dashboard_agg(db, days, granularity, platform, model, top_models,
                               realm=realm, window=window),
        ttl,
    )


def _storage_has_realms(storage: str) -> bool:
    """这个落库平台是否有多个区（trae / qoder）。"""
    from admin.platform import registry

    count = sum(1 for spec in registry.all_platforms(enabled_only=False)
                if str(spec.storage() or "").lower() == storage)
    return count > 1


#: 本地时区偏移（与概览 `_day_start` 同口径，统一由 `rollup.local_offset()` 提供，
#: 环境变量 ADMIN_STATS_TZ_OFFSET_HOURS，默认 +8）。usage_logs.created_at 存 UTC。
#: 保留这个名字只为兼容外部引用；内部一律调用 `rollup.local_day_start()`。
_CN_OFFSET = timedelta(hours=8)


def _dashboard_range(days: int, window: str = "rolling") -> tuple[datetime, datetime]:
    """看板时间窗（UTC naive）。

    * `rolling`（默认，老行为）：近 N×24 小时；
    * `day`：北京时间自然日，从 N-1 天前的 00:00 起到现在 —— 与概览
      `/api/overview` 的「今日 / 近 N 天」同一口径（N5：概览一屏三种时间窗）。
    """
    from admin import rollup as rollup_mod

    end = datetime.utcnow()
    if window == "day":
        return rollup_mod.local_day_start(max(1, days) - 1, now=end), end
    return end - timedelta(days=max(1, days)), end


@router.get("/model-market")
def model_market(hours: int = Query(default=24, ge=1, le=168),
                 _: bool = Depends(require_admin),
                 db: Session = Depends(get_db)):
    """模型广场：**每个模型一张卡**要的全部数字（参考 new-api 的模型广场）。

    卡片上要的是「这东西现在能不能用、多少钱、快不快」。数据来源三处：

      · 近 N 小时用量 —— 小时汇总表（`usage_rollups`），只读几行；
      · 定价 —— `model_configs.credit_multiplier`（0 = 免费）；
      · 可用性 —— 活跃账号数 − 该模型当前处于冷却的账号数。

    返回按**归一后的模型**聚合（同一个模型在多平台是同一个模型，价格与可用性
    分平台列在 `platforms` 里），前端据此画卡片/列表。
    """
    from admin import rollup as rollup_mod
    from admin.model_alias import canonical_key, display_name

    hours = max(1, min(int(hours or 24), 168))
    end = datetime.utcnow()
    start = end - timedelta(hours=hours)

    def _compute():
        labels = platform_labels(db)
        model_labels = _model_display_map(db)
        try:
            rollup_mod.ensure_coverage(db, budget=0.4, blocking=False)
            covered = rollup_mod.coverage(db)
            rows = rollup_mod.window_rows(db, start, end, covered)
            source = "rollup"
        except Exception as exc:  # noqa: BLE001 - 汇总层故障时退回原表
            logger.warning("模型广场读汇总表失败，降级原表：%s", exc)
            covered = (None, None)
            rows = rollup_mod.window_rows(db, start, end, covered)
            source = "raw"

        # 定价（同模型可能多个平台不同价，全部列出来）
        #
        # 2026-11-24 N3（ui_review_round3 A1/A2、platform_scoping P1-7）：
        #   * 一条 model_configs 按 `_entry_targets` 展开成 `(对外 id, 区)`，Trae 国内 /
        #     国际不再都叫 `trae`；
        #   * 按 `(对外 id, 区, 归一模型)` **去重**：同一模型 system/user 两行、
        #     Marvis 可读名与槽位名两行时，只留一条（优先 level=system）；
        #   * 倍率 < 0 是「该平台没有倍率概念、按上游用量计量」的约定，不是负价：
        #     单条给 `metered=true`、`price_label="按量"`，`min_multiplier` 只在
        #     非负倍率里取（全是按量时为 None），前端不会再显示 `×-1.00`。
        from admin.models import ModelConfig
        from admin.platform.models_router import _entry_targets

        config_rows = (db.query(ModelConfig.platform, ModelConfig.model_id,
                                ModelConfig.credit_multiplier, ModelConfig.level)
                       .filter(ModelConfig.enabled == 1).all())
        intl_twins = set()
        for plat, mid, _mult, _level in config_rows:
            text_id = str(mid or "")
            if text_id.lower().endswith(":intl"):
                intl_twins.add((str(plat or "").lower(), text_id[:-5].lower()))
        pricing: dict[str, list] = {}
        chosen: dict[tuple, dict] = {}
        for plat, mid, mult, level in config_rows:
            storage = (plat or "").lower()
            mid = mid or ""
            key = canonical_key(mid, model_labels.get(mid) or "")
            base = mid[:-5] if mid.lower().endswith(":intl") else mid
            try:
                targets = _entry_targets({
                    "platform": storage, "model_id": mid,
                    "_has_intl_twin": (storage, base.lower()) in intl_twins})
            except Exception:  # noqa: BLE001 - 展开失败按落库名单条处理
                targets = [(storage, "")]
            value = float(mult or 0)
            for pid, realm in targets or [(storage, "")]:
                pid = str(pid or storage).lower()
                entry = {
                    "platform": storage,
                    "platform_id": pid,
                    "realm": realm or "",
                    "platform_label": labels.get(pid) or labels.get(storage)
                                      or (storage or "未知平台"),
                    "model_id": mid,
                    "multiplier": value if value >= 0 else None,
                    "metered": value < 0,
                    "free": value == 0,
                    "price_label": ("按量" if value < 0 else
                                    "免费" if value == 0 else "×%g" % value),
                    "level": level or "",
                }
                dedupe = (pid, realm or "", key)
                previous = chosen.get(dedupe)
                if previous is not None:
                    # system 行优先；同级保留先到的那条
                    if previous.get("level") == "system" or entry["level"] != "system":
                        continue
                    pricing[key].remove(previous)
                chosen[dedupe] = entry
                pricing.setdefault(key, []).append(entry)

        # 可用性：活跃账号 − 冷却中的账号（模型级冷却）
        from admin.models import Account, AccountModelCooldown

        active: dict[str, int] = {}
        now = datetime.utcnow()
        for platform, count in db.query(Account.platform, func.count(Account.id)).filter(
                Account.status == "active",
                or_(Account.cool_until.is_(None), Account.cool_until <= now)).group_by(
                Account.platform).all():
            active[(platform or "").lower()] = int(count or 0)
        # 冷却按 (平台, 模型) 计：同名模型在不同平台互不扣减（P1-7 ④）。
        exhausted: dict[tuple[str, str], int] = {}
        try:
            cooled_rows = (db.query(Account.platform, AccountModelCooldown.model,
                                    func.count(AccountModelCooldown.id))
                           .join(Account, Account.id == AccountModelCooldown.account_id)
                           .filter(AccountModelCooldown.until > now)
                           .group_by(Account.platform, AccountModelCooldown.model).all())
        except Exception:  # noqa: BLE001 - 关联失败退回按模型名统计
            cooled_rows = [("*", model_name, count) for model_name, count in db.query(
                AccountModelCooldown.model, func.count(AccountModelCooldown.id)).filter(
                AccountModelCooldown.until > now).group_by(AccountModelCooldown.model).all()]
        for plat, model_name, count in cooled_rows:
            exhausted[((plat or "").lower(), model_name or "")] = int(count or 0)

        def _blocked(platforms, models) -> int:
            total = 0
            for (plat, model_name), count in exhausted.items():
                if model_name in models and (plat == "*" or plat in platforms):
                    total += count
            return total

        items: dict[str, dict] = {}
        for (plat, mid), values in rows.items():
            key = canonical_key(mid, model_labels.get(mid) or "")
            entry = items.setdefault(key, {
                "key": key,
                "name": display_name(key, model_labels.get(mid) or mid or "未知模型"),
                "requests": 0, "successes": 0, "tokens": 0, "credits": 0.0,
                "latency_sum": 0, "latency_count": 0,
                "ttfb_sum": 0, "ttfb_count": 0, "completion_tokens": 0,
                "tps_sum": 0.0, "tps_count": 0,
                "platforms": set(), "models": set(), "traffic_platforms": set(),
            })
            for name in ("requests", "successes", "tokens", "credits",
                         "latency_sum", "latency_count", "ttfb_sum",
                         "ttfb_count", "completion_tokens", "tps_sum", "tps_count"):
                entry[name] += values.get("total_tokens" if name == "tokens" else name) or 0
            entry["platforms"].add(plat)
            entry["traffic_platforms"].add(plat)
            entry["models"].add(mid)

        # 已配置但窗口内没有流量的模型也要出现在广场里（显示「0 次调用」），
        # 否则用户会以为「这个模型没被收录」。
        for key, prices in pricing.items():
            if key in items:
                continue
            first = prices[0]["model_id"]
            items[key] = {
                "key": key,
                "name": display_name(key, model_labels.get(first) or first or "未知模型"),
                "requests": 0, "successes": 0, "tokens": 0, "credits": 0.0,
                "latency_sum": 0, "latency_count": 0,
                "ttfb_sum": 0, "ttfb_count": 0, "completion_tokens": 0,
                "tps_sum": 0.0, "tps_count": 0,
                "platforms": set(), "models": set(), "traffic_platforms": set(),
            }

        out = []
        for key, entry in items.items():
            req = int(entry["requests"])
            ok = int(entry["successes"])
            prices = pricing.get(key, [])
            # **平台 = 流量平台 ∪ 定价平台**（N3 / A1）：原来有流量的模型只列
            # 「近 N 小时跑过的平台」，71 个模型里 25 个因此只显示 1 个平台。
            platforms = sorted(set(entry["platforms"])
                               | {item["platform"] for item in prices})
            models = set(entry["models"]) | {item["model_id"] for item in prices}
            platform_ids = sorted({item["platform_id"] for item in prices}
                                  | {plat for plat in entry["traffic_platforms"]
                                     if plat and not any(item["platform"] == plat
                                                         for item in prices)})
            capacity = sum(active.get(plat, 0) for plat in platforms) if platforms else 0
            blocked = _blocked(set(platforms), models)
            priced = [item["multiplier"] for item in prices
                      if item["multiplier"] is not None]
            metered = bool(prices) and not priced
            out.append({
                "key": key,
                "name": entry["name"],
                "platforms": platforms,
                "platform_labels": [labels.get(plat) or plat or "未知平台" for plat in platforms],
                "platform_ids": platform_ids,
                "traffic_platforms": sorted(entry["traffic_platforms"]),
                "models": sorted(models),
                "requests": req,
                "success_rate": round(ok / req, 4) if req else None,
                "tokens": int(entry["tokens"]),
                "credits": round(float(entry["credits"]), 4),
                "avg_latency_ms": (int(round(entry["latency_sum"] / entry["latency_count"]))
                                   if entry["latency_count"] else 0),
                "avg_ttfb_ms": (int(round(entry["ttfb_sum"] / entry["ttfb_count"]))
                                if entry["ttfb_count"] else 0),
                "tps": (round(entry["tps_sum"] / entry["tps_count"], 2)
                        if entry["tps_count"] else 0.0),
                "pricing": prices,
                "free": bool(priced) and all(value == 0 for value in priced)
                        and len(priced) == len(prices),
                # 只在非负倍率里取最小值；全部「按量」时为 None（前端渲染「按量」）
                "min_multiplier": min(priced) if priced else None,
                "metered": metered,
                "price_label": ("按量" if metered else
                                "免费" if priced and max(priced) == 0 else
                                ("×%g" % min(priced)) if priced else "—"),
                "active_accounts": capacity,
                "exhausted_accounts": blocked,
                "available": bool(prices) and (capacity - blocked > 0),
            })

        out.sort(key=lambda item: (-item["requests"], item["name"]))
        return {
            "window_hours": hours,
            "start": start.isoformat() + "Z",
            "end": end.isoformat() + "Z",
            "source": source,
            "items": out,
            "total": len(out),
            "available_models": sum(1 for item in out if item["available"]),
            # 耗时字段单位：*_ms 全部是毫秒，tps 是 tokens/秒（N1 文档化）
            "units": {"avg_latency_ms": "ms", "avg_ttfb_ms": "ms", "tps": "tokens/s"},
            "scope": "gateway",
        }

    key = _shared_cache_key("model_market", (hours,))
    return _shared_cached(key, _compute, ttl=30.0)


def _dashboard_agg(db: Session, days: int, granularity: str, platform: str,
                   model: str, top_models: int, *, realm: str = "",
                   window: str = "rolling") -> dict:
    result = _dashboard_agg_inner(db, days, granularity, platform, model, top_models,
                                  realm=realm, window=window)
    if isinstance(result, dict):
        result.setdefault("window", {})["kind"] = window
        result.setdefault("filters", {})["realm"] = realm
        # 口径标注（N5）：看板永远是全网关数据，只有显式 ?platform= 才收窄。
        result["scope"] = "gateway"
        result["platform_scoped"] = False
        # 单位文档化：所有 *_ms 字段都是毫秒（前端 fmtDuration 直接按 ms 渲染）。
        result["units"] = {"avg_latency_ms": "ms", "avg_ttfb_ms": "ms",
                           "tps": "tokens/s", "throughput_tps": "tokens/s"}
    return result


def _dashboard_agg_inner(db: Session, days: int, granularity: str, platform: str,
                         model: str, top_models: int, *, realm: str = "",
                         window: str = "rolling") -> dict:
    """数据看板聚合入口：优先读小时级汇总表，异常时降级为原表扫描。

    为什么要有这条降级路径：汇总表是「加速层」，它的写入/回填任何一环出问题，
    都不能让看板变成白的。原表那条路慢，但永远是对的。
    """
    # 归一化放在这里而不是只放在路由上：测试/内部调用也走同一条口径，
    # 免得出现「接口按天、直调按小时」两套完全不同的桶数。
    if str(granularity).lower() != "day" and (days or 0) > 7:
        granularity = "day"
    elif str(granularity).lower() == "day":
        granularity = "day"
    else:
        granularity = "hour"

    from admin import rollup as rollup_mod

    try:
        # 同步补一小段缺口（最多 0.4 秒）：让首次请求就能用上汇总表。
        # 更长的历史由 server 启动时的后台线程补齐，绝不阻塞请求。
        if realm:
            # 汇总表没有区维度：带区筛选只能走原表（按该区账号过滤）。
            return _dashboard_from_raw(db, days, granularity, platform, model, top_models,
                                       realm=realm, window=window)
        rollup_mod.ensure_coverage(db, budget=0.4, blocking=False)
        return _dashboard_from_rollup(db, days, granularity, platform, model, top_models,
                                      window=window)
    except Exception as exc:  # noqa: BLE001 - 汇总层永远不能把看板带崩
        logger.warning("汇总表读取失败，降级为原表聚合：%s", exc)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return _dashboard_from_raw(db, days, granularity, platform, model, top_models,
                                   realm=realm, window=window)


def _dashboard_from_rollup(db: Session, days: int, granularity: str, platform: str,
                           model: str, top_models: int, *, window: str = "rolling") -> dict:
    """汇总表版本：只读 `小时数 × 模型数` 行，30 天窗口也是毫秒级。"""
    from admin import rollup as rollup_mod

    labels = platform_labels(db)
    model_labels = _model_display_map(db)
    start, end = _dashboard_range(days, window)
    minutes = max(1.0, (end - start).total_seconds() / 60.0)

    def _label_platform(value: str) -> str:
        return labels.get((value or "").lower()) or (value or "未知平台")

    def _label_model(value: str) -> str:
        return model_labels.get(value or "") or (value or "未知模型")

    fields = rollup_mod._SUM_FIELDS
    covered = rollup_mod.coverage(db)
    # 两段都让 SQL 先做聚合：汇总表原始行是「小时 × 平台 × 模型」，
    # 30 天窗口两万多行，全拉到 Python 里反而更慢（2c2g 上实测翻倍）。
    rows = rollup_mod.window_rows(db, start, end, covered,
                                  platform=platform, model=model)

    total = {name: 0 for name in fields}
    by_model: dict[str, dict] = {}
    by_platform: dict[str, dict] = {}
    for (plat, mid), values in rows.items():
        for name in fields:
            total[name] += values.get(name) or 0
        target = by_model.setdefault(mid, {
            "values": {name: 0 for name in fields}, "platforms": set()})
        for name in fields:
            target["values"][name] += values.get(name) or 0
        target["platforms"].add(plat)
        slot = by_platform.setdefault(plat, {name: 0 for name in fields})
        for name in fields:
            slot[name] += values.get(name) or 0

    def _avg(total_sum, count):
        return int(round(total_sum / count)) if count else 0

    def _tps(values):
        """平均生成速度：逐请求 t/s 的算术平均（与日志页同一口径）。

        旧实现用 `Σcompletion / Σlatency`，被少数排队/重试十几分钟的请求拖垮
        （实测卡片 42 t/s、日志每行 200+）。没有有效样本时退回区间总吞吐。
        """
        count = values.get("tps_count") or 0
        if count:
            return round(float(values.get("tps_sum") or 0) / count, 2)
        latency = values.get("latency_sum") or 0
        if not latency:
            return 0.0
        return round(float(values.get("completion_tokens") or 0) * 1000.0 / latency, 2)

    requests = int(total["requests"])
    summary = {
        "requests": requests,
        "successes": int(total["successes"]),
        "failures": max(0, requests - int(total["successes"])),
        "success_rate": round(total["successes"] / requests, 4) if requests else None,
        "credits": round(float(total["credits"]), 4),
        "total_tokens": int(total["total_tokens"]),
        "prompt_tokens": int(total["prompt_tokens"]),
        "completion_tokens": int(total["completion_tokens"]),
        "cached_tokens": int(total["cached_tokens"]),
        "cache_hit_rate": (round(total["cached_tokens"] / total["prompt_tokens"], 4)
                           if total["prompt_tokens"] else None),
        "rpm": round(requests / minutes, 2),
        "tpm": round(float(total["total_tokens"]) / minutes, 2),
        "avg_latency_ms": _avg(total["latency_sum"], total["latency_count"]),
        "avg_ttfb_ms": _avg(total["ttfb_sum"], total["ttfb_count"]),
        "throughput_tps": _tps(total),
        "window_minutes": int(minutes),
    }

    models = []
    for mid, entry in by_model.items():
        values = entry["values"]
        req = int(values["requests"])
        ok = int(values["successes"])
        models.append({
            "model": mid or "",
            "label": _label_model(mid),
            "platform": (sorted(entry["platforms"])[0] if len(entry["platforms"]) == 1 else ""),
            "platforms": sorted(entry["platforms"]),
            "requests": req,
            "successes": ok,
            "failures": max(0, req - ok),
            "success_rate": round(ok / req, 4) if req else None,
            "tokens": int(values["total_tokens"]),
            "credits": round(float(values["credits"]), 4),
            "avg_latency_ms": _avg(values["latency_sum"], values["latency_count"]),
            "avg_ttfb_ms": _avg(values["ttfb_sum"], values["ttfb_count"]),
            "tps": _tps(values),
        })
    models.sort(key=lambda item: item["requests"], reverse=True)

    platforms = []
    for value, values in by_platform.items():
        req = int(values["requests"])
        ok = int(values["successes"])
        platforms.append({
            "value": value,
            "label": _label_platform(value),
            "requests": req,
            "success_rate": round(ok / req, 4) if req else None,
            "tokens": int(values["total_tokens"]),
            "credits": round(float(values["credits"]), 4),
        })
    platforms.sort(key=lambda item: item["requests"], reverse=True)

    # ── 时间序列（按模型堆叠，Top N + 其他）──────────────────────────────
    buckets = _bucket_keys(start, end, granularity)
    index = {key: pos for pos, key in enumerate(buckets)}
    per_model: dict[str, dict] = {}
    total_requests = [0] * len(buckets)
    for row in rollup_mod.window_series(db, start, end, granularity, covered,
                                        platform=platform, model=model):
        pos = index.get(row["bucket"])
        if pos is None:
            continue
        mid = row["model"] or ""
        entry = per_model.setdefault(mid, {
            "name": _label_model(mid),
            "requests": [0] * len(buckets),
            "credits": [0.0] * len(buckets),
            "tokens": [0] * len(buckets),
        })
        entry["requests"][pos] += int(row["requests"] or 0)
        entry["credits"][pos] = round(entry["credits"][pos] + float(row["credits"] or 0), 4)
        entry["tokens"][pos] += int(row["total_tokens"] or 0)
        total_requests[pos] += int(row["requests"] or 0)

    ordered = sorted(per_model.values(),
                     key=lambda item: sum(item["credits"]) or sum(item["tokens"]),
                     reverse=True)
    head, tail = ordered[:top_models], ordered[top_models:]
    distribution_series = []
    for item in head:
        metric = "credits" if sum(item["credits"]) else "tokens"
        distribution_series.append({
            "name": item["name"],
            "metric": metric,
            "values": [round(v, 4) for v in item["credits"]] if metric == "credits"
                      else item["tokens"],
        })
    if tail:
        use_credits = any(sum(item["credits"]) for item in tail)
        merged = [0.0] * len(buckets)
        for item in tail:
            source = item["credits"] if (use_credits and sum(item["credits"])) else item["tokens"]
            merged = [round(a + b, 4) for a, b in zip(merged, source)]
        distribution_series.append({
            "name": "其他 %d 个模型" % len(tail),
            "metric": "credits" if use_credits else "tokens",
            "values": merged,
        })

    ranking = []
    for item in sorted(models, key=lambda row: row["requests"], reverse=True):
        ranking.append({**item,
                        "share": round(item["requests"] / requests, 4) if requests else 0.0})

    return {
        "window": {
            "days": days,
            "granularity": granularity,
            "start": start.isoformat() + "Z",
            "end": end.isoformat() + "Z",
            "minutes": int(minutes),
        },
        "filters": {"platform": platform, "model": model},
        "platform_label": _label_platform(platform) if platform else "全部平台",
        "summary": summary,
        "models": models,
        "platforms": platforms,
        "distribution": {"buckets": buckets, "series": distribution_series},
        "trend": {
            "buckets": buckets,
            "total_requests": total_requests,
            "series": [{"name": item["name"], "requests": item["requests"],
                        "tokens": item["tokens"], "credits": item["credits"]}
                       for item in head],
        },
        "ranking": ranking,
        "errors": _error_breakdown(db, start, end, platform=platform, model=model),
        "source": "rollup",
    }


def _error_breakdown(db: Session, start: datetime, end: datetime, **filters) -> list[dict]:
    """失败分类。汇总表只存成功量，失败原因必须回原表取。

    失败行远少于总行数，而且 `(error_kind, created_at)` 有索引，代价可以接受。
    """
    ok = UsageLog.error_kind.in_(_OK_KINDS)
    query = db.query(UsageLog.error_kind, func.count(UsageLog.id)).filter(
        UsageLog.created_at >= start, UsageLog.created_at <= end,
        or_(UsageLog.error_kind.is_(None), ~UsageLog.error_kind.in_(_OK_KINDS)))
    platform = (filters.get("platform") or "").strip().lower()
    if platform:
        query = query.filter(func.lower(UsageLog.platform) == platform)
    model = (filters.get("model") or "").strip()
    if model:
        query = query.filter(UsageLog.model == model)
    rows = query.group_by(UsageLog.error_kind).order_by(
        func.count(UsageLog.id).desc()).limit(8).all()
    return [{"kind": kind or "未分类", "count": int(count or 0)}
            for kind, count in rows if count]


def _dashboard_from_raw(db: Session, days: int, granularity: str, platform: str,
                        model: str, top_models: int, *, realm: str = "",
                        window: str = "rolling") -> dict:
    labels = platform_labels(db)
    model_labels = _model_display_map(db)

    start, end = _dashboard_range(days, window)
    minutes = max(1.0, (end - start).total_seconds() / 60.0)

    def _label_platform(value: str) -> str:
        return labels.get((value or "").lower()) or (value or "未知平台")

    def _label_model(value: str) -> str:
        return model_labels.get(value or "") or (value or "未知模型")

    filters = [UsageLog.created_at >= start, UsageLog.created_at <= end]
    if platform:
        _platform_filter(filters, db, platform, realm)
    if model:
        filters.append(UsageLog.model == model)

    ok = UsageLog.error_kind.in_(_OK_KINDS)
    # 首字只认流式样本（N2，与汇总表同一条件，见 rollup.ttfb_valid_clause）
    from admin import rollup as _rollup_mod
    ttfb_ok = _rollup_mod.ttfb_valid_clause()

    # ── 概览（KPI 行 + 性能健康）─────────────────────────────────────────
    total_req = db.query(func.count(UsageLog.id)).filter(*filters).scalar() or 0
    (ok_req, tok, ptok, ctok, cached, credits,
     avg_latency, avg_ttfb, sum_latency) = (
        db.query(
            func.count(UsageLog.id),
            func.coalesce(func.sum(UsageLog.total_tokens), 0),
            func.coalesce(func.sum(UsageLog.prompt_tokens), 0),
            func.coalesce(func.sum(UsageLog.completion_tokens), 0),
            func.coalesce(func.sum(UsageLog.cached_tokens), 0),
            func.coalesce(func.sum(UsageLog.credits), 0.0),
            func.avg(UsageLog.latency_ms),
            func.avg(case((ttfb_ok, UsageLog.ttfb_ms), else_=None)),
            func.coalesce(func.sum(UsageLog.latency_ms), 0),
        ).filter(*filters, ok).one()
    )
    ok_req = int(ok_req or 0)
    total_req = int(total_req or 0)
    # 吞吐：生成阶段的 Token/秒，分母用 Σlatency 而不是「请求数 × 平均延迟」——
    # 后者会被一两条超长请求平滑掉。
    throughput = (float(ctok or 0) * 1000.0 / float(sum_latency)) if sum_latency else 0.0
    summary = {
        "requests": total_req,
        "successes": ok_req,
        "failures": max(0, total_req - ok_req),
        "success_rate": round(ok_req / total_req, 4) if total_req else None,
        "credits": round(float(credits or 0), 4),
        "total_tokens": int(tok or 0),
        "prompt_tokens": int(ptok or 0),
        "completion_tokens": int(ctok or 0),
        "cached_tokens": int(cached or 0),
        "cache_hit_rate": (round(float(cached or 0) / float(ptok), 4)
                           if ptok else None),
        "rpm": round(total_req / minutes, 2),
        "tpm": round(float(tok or 0) / minutes, 2),
        "avg_latency_ms": int(avg_latency) if avg_latency else 0,
        "avg_ttfb_ms": int(avg_ttfb) if avg_ttfb else 0,
        "throughput_tps": round(throughput, 2),
        "window_minutes": int(minutes),
    }

    # ── 按模型 / 平台（成功率必须含失败，所以用 case 汇总而不是 _ok_filter）──
    model_rows = (
        db.query(
            UsageLog.model,
            func.count(UsageLog.id),
            func.sum(case((ok, 1), else_=0)),
            func.sum(case((ok, UsageLog.total_tokens), else_=0)),
            func.sum(case((ok, UsageLog.credits), else_=0.0)),
            func.avg(case((ok, UsageLog.latency_ms), else_=None)),
            func.avg(case((and_(ok, ttfb_ok), UsageLog.ttfb_ms), else_=None)),
            func.sum(case((ok, UsageLog.completion_tokens), else_=0)),
            func.sum(case((ok, UsageLog.latency_ms), else_=0)),
        )
        .filter(*filters)
        .group_by(UsageLog.model)
        .order_by(func.count(UsageLog.id).desc())
        .all()
    )

    models = []
    for (mid, req, okn, tokens, cred, lat, ttfb, ctoks, latsum) in model_rows:
        req = int(req or 0)
        okn = int(okn or 0)
        models.append({
            "model": mid or "",
            "label": _label_model(mid),
            "requests": req,
            "successes": okn,
            "failures": max(0, req - okn),
            "success_rate": round(okn / req, 4) if req else None,
            "tokens": int(tokens or 0),
            "credits": round(float(cred or 0), 4),
            "avg_latency_ms": int(lat) if lat else 0,
            "avg_ttfb_ms": int(ttfb) if ttfb else 0,
            "tps": round(float(ctoks or 0) * 1000.0 / float(latsum), 2) if latsum else 0.0,
        })

    platform_rows = (
        db.query(
            UsageLog.platform,
            func.count(UsageLog.id),
            func.sum(case((ok, 1), else_=0)),
            func.sum(case((ok, UsageLog.total_tokens), else_=0)),
            func.sum(case((ok, UsageLog.credits), else_=0.0)),
        )
        .filter(*filters)
        .group_by(UsageLog.platform)
        .order_by(func.count(UsageLog.id).desc())
        .all()
    )
    platforms = []
    for (value, req, okn, tokens, cred) in platform_rows:
        req = int(req or 0)
        okn = int(okn or 0)
        platforms.append({
            "value": (value or "").lower(),
            "label": _label_platform(value),
            "requests": req,
            "success_rate": round(okn / req, 4) if req else None,
            "tokens": int(tokens or 0),
            "credits": round(float(cred or 0), 4),
        })

    # ── 时间序列（按模型堆叠）────────────────────────────────────────────
    bucket = _bucket_expr(db, granularity)
    series_rows = (
        db.query(
            bucket.label("b"),
            UsageLog.model,
            func.sum(case((ok, 1), else_=0)),
            func.sum(case((ok, UsageLog.credits), else_=0.0)),
            func.sum(case((ok, UsageLog.total_tokens), else_=0)),
        )
        .filter(*filters)
        .group_by("b", UsageLog.model)
        .order_by("b")
        .all()
    )

    buckets = _bucket_keys(start, end, granularity)
    index = {key: pos for pos, key in enumerate(buckets)}
    per_model: dict[str, dict] = {}
    total_requests = [0] * len(buckets)
    for (b, mid, req, cred, tokens) in series_rows:
        pos = index.get(b)
        if pos is None:
            continue
        mid = mid or ""
        entry = per_model.get(mid)
        if entry is None:
            entry = per_model[mid] = {
                "name": _label_model(mid),
                "requests": [0] * len(buckets),
                "credits": [0.0] * len(buckets),
                "tokens": [0] * len(buckets),
            }
        entry["requests"][pos] += int(req or 0)
        entry["credits"][pos] = round(entry["credits"][pos] + float(cred or 0), 4)
        entry["tokens"][pos] += int(tokens or 0)
        total_requests[pos] += int(req or 0)

    # 堆叠图只画前 N 个模型，其余滚成「其他 N 个模型」——
    # 20 个模型的图例在 1080p 上会占掉半屏，且每根柱子被切成细丝。
    ordered = sorted(per_model.values(),
                     key=lambda item: sum(item["credits"]) or sum(item["tokens"]),
                     reverse=True)
    head, tail = ordered[:top_models], ordered[top_models:]
    distribution_series = [
        {"name": item["name"], "values": item["credits"] or item["tokens"],
         "metric": "credits" if sum(item["credits"]) else "tokens"}
        for item in head
    ]
    if tail:
        merged = [0.0] * len(buckets)
        for item in tail:
            values = item["credits"] if sum(item["credits"]) else item["tokens"]
            merged = [round(a + b, 4) for a, b in zip(merged, values)]
        distribution_series.append({"name": f"其他 {len(tail)} 个模型", "values": merged,
                                    "metric": "credits" if any(merged) else "tokens"})

    trend_series = [{"name": item["name"], "requests": item["requests"],
                     "tokens": item["tokens"], "credits": item["credits"]}
                    for item in head]

    # ── 调用次数排行 ────────────────────────────────────────────────────
    ranking = sorted(models, key=lambda item: item["requests"], reverse=True)
    for item in ranking:
        item["share"] = round(item["requests"] / total_req, 4) if total_req else 0.0

    # ── 失败分类（风控/上游的入口）───────────────────────────────────────
    error_rows = (
        db.query(UsageLog.error_kind, func.count(UsageLog.id))
        .filter(*filters,
                or_(UsageLog.error_kind.is_(None), ~UsageLog.error_kind.in_(_OK_KINDS)))
        .group_by(UsageLog.error_kind)
        .order_by(func.count(UsageLog.id).desc())
        .limit(8)
        .all()
    )
    errors = [{"kind": kind or "未分类", "count": int(count or 0)}
              for kind, count in error_rows if count]

    return {
        "window": {
            "days": days,
            "granularity": granularity,
            "start": start.isoformat() + "Z",
            "end": end.isoformat() + "Z",
            "minutes": int(minutes),
        },
        "filters": {"platform": platform, "model": model},
        "platform_label": _label_platform(platform) if platform else "全部平台",
        "summary": summary,
        "models": models,
        "platforms": platforms,
        "distribution": {
            "buckets": buckets,
            "series": distribution_series,
        },
        "trend": {
            "buckets": buckets,
            "total_requests": total_requests,
            "series": trend_series,
        },
        "ranking": ranking,
        "errors": errors,
    }


@router.get("/rollup/status")
def rollup_status(_: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """汇总表水位与一次性重建标记（排障用，只读）。"""
    from admin import rollup as rollup_mod
    from admin.models import SystemSetting

    covered_from, covered_to = rollup_mod.coverage(db)
    marker = db.get(SystemSetting, rollup_mod.REBUILD_MARKER)
    return {"covered_from": covered_from.isoformat() + "Z" if covered_from else None,
            "covered_to": covered_to.isoformat() + "Z" if covered_to else None,
            "rebuild_marker": rollup_mod.REBUILD_MARKER,
            "rebuilt_at": (marker.value if marker is not None else None)}


@router.get("/db-health")
def db_health(_: bool = Depends(require_admin)):
    """数据库运行状况（只读，P7）：方言、连接池、SQLite 参数、`database is locked` 次数。

    「锁升级失败被吞、这次用量就丢了」原来没有任何可观测入口；现在引擎层
    `handle_error` 会计数并打 WARNING，这里能直接看到最近一次的时间和语句类型。
    """
    from admin import db as db_mod

    stats = getattr(db_mod, "lock_error_stats", None)
    return stats() if callable(stats) else {"available": False}


@router.get("/retention")
def retention_preview(_: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """数据保留策略预览（只读）：每张表按当前配置「将删除多少行」。

    开关 `ADMIN_RETENTION_ENABLED=1` 打开后，后台每天最多执行一轮，
    见 admin/retention.py。
    """
    from admin import retention

    return retention.preview(db)


@router.post("/rollup/rebuild")
def rollup_rebuild(_: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """手动重建小时汇总表（幂等）。

    只清「原表还在」的那段汇总、把水位退回；真正的重算由后台回填线程完成
    （每轮 30 秒预算，5 分钟一轮），期间看板自动降级走原表，数字仍然正确。
    重建后统计缓存会自然过期（TTL ≤ 60 秒）。
    """
    from admin import rollup as rollup_mod

    result = rollup_mod.rebuild(db, reason="管理员手动触发")
    try:
        # 先补最近 2 秒能补的尾部，让看板尽快用回汇总表
        rollup_mod.ensure_coverage(db, budget=2.0)
    except Exception as exc:  # noqa: BLE001 - 补不完交给后台线程
        logger.info("重建后的即时回填未完成（交给后台）：%s", exc)
    return {"ok": True, **result}


@router.get("/memory")
def memory_stats(_: bool = Depends(require_admin)):
    """进程内存现状：RSS + 各内存缓存的实际条目数 / 上限。

    只读观测，不清理任何东西 —— 想对比「基线 → 压测 → 回落」时，
    直接隔一段时间各取一次快照即可。缓存条目数贴到上限就说明该调大容量
    或缩短 TTL，而不是继续往上堆。
    """
    from admin import process_memory
    snap = process_memory.snapshot()
    # 上游连接池观测（perf / gateway 线新增）：读失败不影响内存快照本身。
    pools: dict = {}
    try:
        from admin import http_shared
        pools["shared"] = http_shared.stats()
    except Exception as exc:  # noqa: BLE001
        pools["shared"] = {"error": str(exc)[:120]}
    try:
        from admin.platform.marvis.core import http_pool as _marvis_pool
        if hasattr(_marvis_pool, "stats"):
            pools["marvis"] = _marvis_pool.stats()
    except Exception as exc:  # noqa: BLE001
        pools["marvis"] = {"error": str(exc)[:120]}
    # 第 4 轮：urllib 长连接池（Trae / CodeArts / Qoder，按平台一项；没用过的平台不出现）。
    try:
        from admin import http_keepalive
        pools["urllib_keepalive"] = http_keepalive.stats()
    except Exception as exc:  # noqa: BLE001
        pools["urllib_keepalive"] = {"error": str(exc)[:120]}
    if isinstance(snap, dict):
        snap = {**snap, "upstream_pools": pools}
    return snap
