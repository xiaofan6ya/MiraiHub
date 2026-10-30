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
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import case, func
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
    first_cached = db.query(func.min(UsageLog.created_at)).filter(
        UsageLog.cached_tokens > 0).scalar()
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


@router.get("")
def overview(days: int = Query(default=1, ge=1, le=30),
             request: Request = None,
             _: bool = Depends(require_admin),
             db: Session = Depends(get_db)):
    """首页总览：一次返回所有需要的聚合。

    平台作用域：控制台带 `X-Platform` 时只统计该平台（与其它面板一致）；
    不带则统计全部（老脚本 / curl 行为不变）。
    """
    platform, realm = platform_scope.from_request(request)
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

    today = _window(db, today_start, scope_ids)
    window = _window(db, window_start, scope_ids)

    # 本月消耗：30 天窗口的积分
    month = _window(db, month_start, scope_ids)

    # ---- 模型 / Key 计数 ----
    model_query = db.query(func.count(ModelConfig.id)).filter(ModelConfig.enabled == 1)
    if platform:
        model_query = model_query.filter(
            ModelConfig.platform == (registry.get(platform).storage()
                                     if registry.get(platform) else platform))
    enabled_models = int(model_query.scalar() or 0)

    total_keys = int(db.query(func.count(ApiKey.id)).filter(
        ApiKey.status == "active").scalar() or 0)

    return {
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
        "cache": _cache_hit(db, window_start, scope_ids),
        "monitor": _account_monitor(db, scope_ids, platform),
        "now": datetime.utcnow().isoformat() + "Z",
    }