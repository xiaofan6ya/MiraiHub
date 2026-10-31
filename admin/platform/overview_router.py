"""总览聚合接口：`/api/overview`。

## 为什么单独一个接口

首页要把「积分 / 用量 / 额度 / 缓存命中 / 账号监测 / 风控告警」一次画出来。
如果前端分别去拉 `/api/accounts` + `/api/stats/usage` + `/api/credits/expiry`
+ `/api/sentinel/status` + `/api/models/configs`，就是 5 次串行请求、
5 份重复的全表扫描，而且**口径还可能不一致**（各自算各自的时间窗）。

因此这里在服务端一次性算完，返回一个扁平结构。所有聚合都在 SQL 侧完成，
不把明细拉进 Python。

## 口径约定（和既有面板保持一致，不另立一套）

* 「今日」= 本地时区当天 0 点起（与 `usage_logs.created_at` 的 UTC 存储换算）；
* 积分/额度只统计 `status='active'` 的账号（与 `/api/accounts` 一致）；
* 用量只统计成功请求的 token（`error_kind` 为空或 success），
  请求次数则统计全部（含失败）—— 与 `/api/stats/usage` 同口径；
* 缓存命中率的分母只算「有缓存统计之后」的输入 token，否则历史数据会
  永久拉低命中率（upstream 早期不回传 cached_tokens）。
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import and_, case, func
from sqlalchemy.orm import Session

from admin.db import get_db
from admin.models import Account, ApiKey, ModelConfig, UsageLog
from admin.platform import registry
from admin.platform import scope as platform_scope
from admin.security import require_admin

logger = logging.getLogger("admin.overview")

router = APIRouter(prefix="/api/overview", tags=["overview"])

#: 视为「成功」的 error_kind（与 routers/stats.py 保持一致）
_OK_KINDS = ("", "success")

#: 本地时区偏移（小时）。usage_logs.created_at 存 UTC。
_LOCAL_OFFSET = 8


def _day_start(days_back: int = 0) -> datetime:
    """本地时区「今天 0 点」对应的 UTC 时间（naive，与库里的值可直接比较）。"""
    now = datetime.now(timezone.utc) + timedelta(hours=_LOCAL_OFFSET)
    local_midnight = datetime(now.year, now.month, now.day) - timedelta(days=days_back)
    return local_midnight - timedelta(hours=_LOCAL_OFFSET)


def _window(db: Session, since: datetime, scope_ids=None) -> dict:
    """一段时间的用量聚合（一次 SQL 出全部数字）。"""
    ok = func.coalesce(UsageLog.error_kind, "").in_(_OK_KINDS)
    query = db.query(
        func.count(UsageLog.id),
        func.coalesce(func.sum(case((ok, 1), else_=0)), 0),
        func.coalesce(func.sum(case((ok, UsageLog.prompt_tokens), else_=0)), 0),
        func.coalesce(func.sum(case((ok, UsageLog.completion_tokens), else_=0)), 0),
        func.coalesce(func.sum(case((ok, UsageLog.total_tokens), else_=0)), 0),
        func.coalesce(func.sum(case((ok, UsageLog.cached_tokens), else_=0)), 0),
        func.coalesce(func.sum(case((ok, UsageLog.credits), else_=0)), 0),
    ).filter(UsageLog.created_at >= since)
    if scope_ids is not None:
        if not scope_ids:
            return {"requests": 0, "ok_requests": 0, "prompt_tokens": 0,
                    "completion_tokens": 0, "total_tokens": 0,
                    "cached_tokens": 0, "credits": 0.0}
        query = query.filter(UsageLog.account_id.in_(scope_ids))
    row = query.one()
    return {
        "requests": int(row[0] or 0),
        "ok_requests": int(row[1] or 0),
        "prompt_tokens": int(row[2] or 0),
        "completion_tokens": int(row[3] or 0),
        "total_tokens": int(row[4] or 0),
        "cached_tokens": int(row[5] or 0),
        "credits": float(row[6] or 0),
    }


def _cache_hit(db: Session, since: datetime, scope_ids=None) -> dict:
    """缓存命中率。

    分母只算**有缓存统计之后**的输入 token：早期上游不回传 cached_tokens，
    那些行会把命中率永久拉低（实测能差出几十个百分点）。
    因此单独取最早一条 `cached_tokens > 0` 的时间作为统计起点。
    """
    first_cached = _first_cached_at(db)
    start = since
    note = ""
    if first_cached and first_cached > since:
        start = first_cached
        note = "自 %s 起（此前的上游响应不含缓存字段）" % first_cached.strftime("%Y-%m-%d")
    row = db.query(
        func.coalesce(func.sum(UsageLog.prompt_tokens), 0),
        func.coalesce(func.sum(UsageLog.cached_tokens), 0),
        func.count(UsageLog.id),
    ).filter(UsageLog.created_at >= start,
             func.coalesce(UsageLog.error_kind, "").in_(_OK_KINDS))
    if scope_ids is not None:
        if not scope_ids:
            return {"cached_tokens": 0, "prompt_tokens": 0, "rate": None,
                    "note": "当前平台没有流量"}
        row = row.filter(UsageLog.account_id.in_(scope_ids))
    prompt, cached, samples = row.one()
    prompt = int(prompt or 0)
    cached = int(cached or 0)
    return {
        "cached_tokens": cached,
        "prompt_tokens": prompt,
        # 分母是输入 token；没有样本时给 None 而不是 0（0% 会被读成「测过且为零」）
        "rate": (cached / prompt) if prompt else None,
        "samples": int(samples or 0),
        "note": note,
    }


#: 「最早一条带缓存命中的记录」的记忆值。
#:
#: 它是**历史常量**（一旦出现就不会再往前），但原实现每算一次命中率就
#: `SELECT MIN(created_at) WHERE cached_tokens > 0` 扫一遍 42 万行
#: ——线上实测 104ms，而且一屏概览要算两三个窗口，白烧 300ms。
_FIRST_CACHED = {"at": 0.0, "value": None}
_FIRST_CACHED_TTL = 600.0
_FIRST_CACHED_LOCK = threading.Lock()


def _first_cached_at(db: Session):
    """带缓存命中的最早记录时间（进程内记忆 10 分钟）。"""
    now = time.time()
    with _FIRST_CACHED_LOCK:
        cached = _FIRST_CACHED
        if cached["at"] and now - cached["at"] < _FIRST_CACHED_TTL:
            return cached["value"]
    try:
        value = db.query(func.min(UsageLog.created_at)).filter(
            UsageLog.cached_tokens > 0).scalar()
    except Exception:  # noqa: BLE001 - 取不到就退回「不裁剪窗口」
        return None
    with _FIRST_CACHED_LOCK:
        _FIRST_CACHED["at"] = now
        _FIRST_CACHED["value"] = value
    return value


#: 「本月累计」的进程内缓存（60 秒）。
#:
#: 它只是 `average_credits`（户均消耗）的来源，变化极慢，却是 7 天/30 天
#: 口径里最贵的一次扫描（线上 29 天 = 371ms）。按 60 秒缓存后，
#: 同一个平台下的连续请求不再重复付这笔钱。
_MONTH_CACHE: dict[str, tuple[float, dict]] = {}
_MONTH_TTL = 60.0
_MONTH_LOCK = threading.Lock()


def _cached_month(db: Session, month_start: datetime, scope_ids=None,
                  platform: str = "", realm: str = "") -> dict:
    """本月累计（带 60 秒缓存）。算失败不影响主流程，退回直算。"""
    key = "%s|%s|%s" % (month_start.isoformat(), platform or "", realm or "")
    now = time.time()
    with _MONTH_LOCK:
        hit = _MONTH_CACHE.get(key)
        if hit and hit[0] > now:
            return hit[1]
    try:
        value = _window(db, month_start, scope_ids)
    except Exception:  # noqa: BLE001
        raise
    with _MONTH_LOCK:
        for stale in [k for k, (exp, _v) in _MONTH_CACHE.items() if exp < now]:
            _MONTH_CACHE.pop(stale, None)
        _MONTH_CACHE[key] = (now + _MONTH_TTL, value)
    return value


def _account_monitor(db: Session, scope_ids=None, platform: str = "") -> dict:
    """账号监测：按状态分桶，给出「谁正处于什么阶段」。

    分桶顺序即用户关心的优先级：异常 → 冷却 → 额度耗尽 → 活跃。
    一次查询拿到全部账号的这些字段，不做 N+1。

    **与风控哨兵联动**：`colling` 的账号会带上哨兵在最近窗口里记录到的
    业务码（11140 / 403 …）和信号次数 —— 用户要的就是「这个号为什么在冷、
    上游到底回了什么」。哨兵是 Redis 里的短命数据，读不到就降级为空，
    绝不让监测面板因此打不开。
    """
    now = datetime.utcnow()
    query = db.query(Account.id, Account.name, Account.platform, Account.domain,
                     Account.status, Account.cool_until, Account.cool_kind,
                     Account.breaker_until, Account.balance_remain,
                     Account.last_err_msg, Account.last_err_at)
    if scope_ids is not None and not scope_ids:
        return {"active": 0, "cooling": 0, "breaker": 0, "disabled": 0,
                "no_balance": 0, "total": 0, "items": []}
    if scope_ids:
        query = query.filter(Account.id.in_(scope_ids))
    rows = query.all()

    # 哨兵信号（按账号聚合）。只覆盖有信号的号，通常是个位数量。
    signal_map: dict[int, dict] = {}
    try:
        from admin import sentinel
        for item in sentinel.by_account(platform) or []:
            signal_map[int(item.get("account_id") or 0)] = item
    except Exception:  # noqa: BLE001 - 哨兵不可用不影响监测
        signal_map = {}

    buckets = {"active": 0, "cooling": 0, "breaker": 0, "disabled": 0, "no_balance": 0}
    items = []
    for (acc_id, name, platform, domain, status, cool_until, cool_kind,
         breaker_until, balance, last_err, last_err_at) in rows:
        if status != "active":
            state, label = "disabled", "已禁用"
        elif breaker_until and breaker_until > now:
            state, label = "breaker", "熔断中"
        elif cool_until and cool_until > now:
            state, label = "cooling", cool_kind or "冷却中"
        elif (balance or 0) <= 0:
            state, label = "no_balance", "额度耗尽"
        else:
            state, label = "active", "活跃"
        buckets[state] = buckets.get(state, 0) + 1
        signal = signal_map.get(int(acc_id or 0)) or {}
        items.append({
            "id": acc_id,
            "name": name or "",
            "platform": platform or registry.DEFAULT_PLATFORM,
            "domain": domain or "",
            "state": state,
            "label": label,
            "balance": int(balance or 0),
            "until": (breaker_until or cool_until).isoformat() + "Z"
            if ((breaker_until and breaker_until > now)
                or (cool_until and cool_until > now)) and (breaker_until or cool_until) else None,
            # 距离自动恢复还有多少秒（前端直接显示「xx 分钟后自动重试」）
            "cool_remaining": (
                int(((breaker_until or cool_until) - now).total_seconds())
                if ((breaker_until and breaker_until > now)
                    or (cool_until and cool_until > now)) else 0),
            "last_error": (last_err or "")[:120],
            "last_error_at": last_err_at.isoformat() + "Z" if last_err_at else None,
            # 哨兵记录到的真实业务码与次数（有就是有，没有就是空）
            "signal_code": signal.get("code") or "",
            "signal_count": int(signal.get("count") or 0),
            "signal_label": signal.get("label") or "",
            "signal_last_at": signal.get("last_ts") or None,
        })
    # 异常优先展示：出问题的号要排在最前面
    order = {"breaker": 0, "disabled": 1, "cooling": 2, "no_balance": 3, "active": 4}
    items.sort(key=lambda item: (order.get(item["state"], 9), item["id"]))
    buckets["total"] = len(rows)
    buckets["items"] = items[:60]
    return buckets


#: 概览结果缓存（key -> (过期时间, 结果)）。
#:
#: ## 为什么要它
#:
#: 控制台会同时打 overview + platforms + sentinel，切页、点刷新、
#: 多开标签页都会重复请求同一个 overview。这些聚合本身不便宜
#: （线上 42 万行时单次约 1.4s），重复算纯属浪费。
#:
#: TTL 给 **3 秒**：短到用户点「刷新」时视觉上仍是新的
#: （概览本来就是「趋势看板」，不是逐笔账），
#: 又长到足以吃掉同一页面里连续几次请求。
#:
#: 只缓存**成功**结果；算失败不写缓存，避免把一次抖动钉死 3 秒。
_OVERVIEW_CACHE: dict[str, tuple[float, dict]] = {}
_OVERVIEW_TTL = 3.0
_OVERVIEW_CACHE_LOCK = threading.Lock()


def _ttl_for(days: int) -> float:
    """按窗口给 TTL：窗口越大、数据变化越慢，缓存留得越久。

    今日数据每秒都在变，3 秒已经够吃掉「切页/刷新/多标签」的重复请求；
    7 天 / 30 天是趋势口径，20~45 秒内的重复请求完全没必要重算
    （那是 20 万行的全量聚合）。前端有骨架屏，用户看到的是「秒开」。
    """
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 1
    if days <= 1:
        return 3.0
    if days <= 7:
        return 20.0
    return 45.0


def _cache_key(days: int, platform: str, realm: str) -> str:
    return "%d|%s|%s" % (int(days), platform or "", realm or "")


def _cache_get(key: str) -> dict | None:
    now = time.time()
    with _OVERVIEW_CACHE_LOCK:
        hit = _OVERVIEW_CACHE.get(key)
        if hit is None:
            return None
        expires, payload = hit
        if expires < now:
            _OVERVIEW_CACHE.pop(key, None)
            return None
        return payload


def _cache_put(key: str, payload: dict, ttl: float | None = None) -> None:
    now = time.time()
    expires = now + (_OVERVIEW_TTL if ttl is None else ttl)
    with _OVERVIEW_CACHE_LOCK:
        # 顺手清理过期项，避免长期运行堆积（key 数量本来就很小）
        for stale in [k for k, (exp, _) in _OVERVIEW_CACHE.items() if exp < now]:
            _OVERVIEW_CACHE.pop(stale, None)
        _OVERVIEW_CACHE[key] = (expires, payload)


@router.get("")
def overview(days: int = Query(default=1, ge=1, le=30),
             request: Request = None,
             _: bool = Depends(require_admin),
             db: Session = Depends(get_db)):
    """首页总览：一次返回所有需要的聚合。

    平台作用域：控制台带 `X-Platform` 时只统计该平台（与其它面板一致）；
    不带则统计全部（老脚本 / curl 行为不变）。

    ## 为什么原来要等 10 多秒

    这个接口串行跑了 **5 个全表/大范围聚合**（今日、窗口、30 天、
    缓存命中率、账号监测），每个都要扫一遍 `usage_logs`。
    数据量一上来（几万行）总耗时就是 5×单次，前端只能干等 ——
    用户看到的是「概览一片空白，等十几秒才出来」。现在这 5 段并发跑。

    另外默认统计范围改成**今日**（前端 `_ovDays = 1`）：
    30 天窗口要扫 22 万行，而用户打开概览多数只想看「今天怎么样」。

    现在加了一层**短 TTL 进程内缓存**：控制台会同时请求 overview +
    platforms + sentinel，切页/刷新/多标签页会重复打同一个接口，
    缓存能把这部分直接吃掉；真正的首屏仍然只算一次。
    """
    platform, realm = platform_scope.from_request(request)

    # 命中缓存直接返回 —— 3 秒内的重复请求不打数据库
    _ck = _cache_key(days, platform, realm)
    _hit = _cache_get(_ck)
    if _hit is not None:
        return _hit

    scope_ids = platform_scope.account_ids(db, platform, realm)

    today_start = _day_start(0)
    month_start = _day_start(29)
    window_start = _day_start(days - 1)

    # ---- 额度（只算 active）----
    acc_query = db.query(
        func.count(Account.id),
        func.coalesce(func.sum(Account.balance_remain), 0),
        func.coalesce(func.sum(Account.balance_total), 0),
    ).filter(Account.status == "active")
    acc_query = platform_scope.filter_accounts(acc_query, db, platform, realm)
    total_accounts, balance_remain, balance_total = acc_query.one()

    # 已禁用账号那部分余额：透明展示，但不计入「可用」
    disabled_query = db.query(func.coalesce(func.sum(Account.balance_remain), 0)).filter(
        Account.status != "active")
    disabled_query = platform_scope.filter_accounts(disabled_query, db, platform, realm)
    disabled_balance = int(disabled_query.scalar() or 0)

    # ---- 三个时间窗口 + 缓存命中率 + 账号监测：**串行执行** ----
    #
    # ## 为什么不用线程池并发（曾经用过，实测更慢，已回退）
    #
    # 本地 SQLite 上并发看起来能省 13%；但**线上是 MySQL + 有限连接池**，
    # 并发立刻暴露两个问题：
    #
    #   1. 每个线程各开一个 Session，等于同一时刻占掉 5 条连接。
    #      线上还有网关请求在跑，池子被占满后**所有查询一起排队**，
    #      实测「7 天」从 1.4s 恶化到 13.8s（慢了 10 倍）。
    #   2. MySQL 是按连接串行处理同一张表的扫描的，五个并发 scan
    #      并不会真的并行，只是把排队从「进程内」挪到了「数据库端」。
    #
    # 关键数据：线上单条 `COUNT(*) WHERE created_at >= ?`
    #   今日 17ms / 7 天 45ms / 30 天 107ms。
    #   串行跑完这 5 段 ≈ 230ms —— 完全够快，根本不需要并发。
    #
    # 结论：**这里的瓶颈从来不是「没并行」，而是「扫了太多行」**。
    # 真正的解法是「别重复扫」+ 结果短缓存，实测下来三招最有效：
    #
    #   1. **30 天口径下 `window` 与 `month` 是同一个区间**（都是 _day_start(29)），
    #      原来算了整整两遍 —— 线上实测白烧 371ms；
    #   2. 「最早一条带缓存的记录」是历史常量，不再每次扫 42 万行（省 ~100ms/次）；
    #   3. 本月累计（amount_credits 的来源）单独按 60s 缓存 —— 它变化极慢，
    #      却占着 7 天/30 天口径里最大的一笔扫描（371ms）。
    #
    # 反面教材（实测过、不要回去）：把三个窗口用 CASE 合成一次扫描，
    # MySQL 上是 **1335ms**，比分开查的 494ms 还慢一倍 —— 因为小窗口本来
    # 只扫几千行，合成后每行都要过 18 个聚合表达式。
    today = _window(db, today_start, scope_ids)
    if window_start == today_start:
        window = dict(today)
    else:
        window = _window(db, window_start, scope_ids)
    if window_start == month_start:
        # 30 天口径：两个窗口完全重合，绝不能算两遍
        month = dict(window)
    else:
        month = _cached_month(db, month_start, scope_ids,
                              platform=platform, realm=realm)
    cache_hit = _cache_hit(db, window_start, scope_ids)
    monitor = _account_monitor(db, scope_ids, platform)
    # ---- 模型 / Key 计数 ----
    model_query = db.query(func.count(ModelConfig.id)).filter(ModelConfig.enabled == 1)
    if platform:
        model_query = model_query.filter(
            ModelConfig.platform == (registry.get(platform).storage()
                                     if registry.get(platform) else platform))
    enabled_models = int(model_query.scalar() or 0)

    total_keys = int(db.query(func.count(ApiKey.id)).filter(
        ApiKey.status == "active").scalar() or 0)

    payload = {
        "platform": platform,
        "realm": realm,
        "platform_label": platform_scope.label(platform, realm),
        "accounts": {
            "total": int(total_accounts or 0),
            "balance_remain": int(balance_remain or 0),
            "balance_total": int(balance_total or 0),
            "disabled_balance": disabled_balance,
            # 已用比例：进度条用；总额为 0 时给 None（避免除零与「0%」误导）
            "used_ratio": (1 - (int(balance_remain or 0) / int(balance_total))
                           if int(balance_total or 0) > 0 else None),
        },
        "models": enabled_models,
        "keys": total_keys,
        "today": today,
        "window": dict(window, days=days),
        "month": month,
        # 户均：本月消耗 / active 账号数（无账号时为 None）
        "average_credits": (month["credits"] / int(total_accounts)
                            if int(total_accounts or 0) > 0 else None),
        "cache": cache_hit,
        "monitor": monitor,
        "now": datetime.utcnow().isoformat() + "Z",
    }
    _cache_put(_ck, payload, _ttl_for(days))
    return payload