"""Calendar cycles use Beijing time; database timestamps remain naive UTC."""
import calendar
import json
import re
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field, validator


class ScheduleTiming(BaseModel):
    mode: Literal["preset", "interval", "daily", "weekly", "monthly"] = "preset"
    time: str = "09:00"
    weekdays: list[int] = Field(default_factory=lambda: [0])
    monthday: int = Field(default=1, ge=1, le=31)

    # Monday=0, matching datetime.weekday().
    @validator("time")
    def valid_time(cls, value):
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("执行时间格式必须为 HH:MM")
        return value

    # Use the validator API shared by desktop Pydantic 2 and Android Pydantic 1.
    @validator("weekdays")
    def valid_weekdays(cls, value):
        if not 1 <= len(value) <= 7 or any(day < 0 or day > 6 for day in value):
            raise ValueError("星期必须在周一至周日之间")
        return sorted(set(value))


def timing_of(schedule) -> ScheduleTiming:
    raw = getattr(schedule, "timing_config", "") or ""
    return ScheduleTiming(**json.loads(raw)) if raw else ScheduleTiming()


def _daily_jitter_minutes(schedule) -> float:
    """每日/每周/每月任务的**分钟级**抖动幅度（± 分钟）。

    ## 为什么必须抖（2026-11-06 用户明确要求）

    > 执行任务、每日签到时间随机不再固定时分，分可以变化而不是固定整数区间的。

    原实现每天都是 `09:00:00` 整跑一次。上游看到的是一台准点机器：
    每天同一分钟、同一个秒、同一批账号一起签到。真人不会这样 ——
    真人今天 8:47、明天 9:23、后天忘了。

    幅度按周期取：日任务 ±25 分钟，周任务 ±90 分钟，月任务 ±180 分钟。
    用环境变量 `ADMIN_SCHEDULE_JITTER_MINUTES` 可以整体放大/缩小或关掉（0）。
    """
    import os

    override = os.getenv("ADMIN_SCHEDULE_JITTER_MINUTES", "").strip()
    if override:
        try:
            return max(0.0, float(override))
        except ValueError:
            pass
    from admin.schedule_timing import timing_of  # 自身模块，保持局部可读

    mode = timing_of(schedule).mode
    if mode == "weekly":
        return 90.0
    if mode == "monthly":
        return 180.0
    return 25.0


def _apply_jitter(target: datetime, minutes: float) -> datetime:
    """给目标时刻加一个**对称**的随机偏移（分钟级，秒也随机）。"""
    if minutes <= 0:
        return target
    import random

    offset = random.uniform(-minutes, minutes)
    return target + timedelta(minutes=offset,
                              seconds=random.uniform(-59, 59))


def next_run_at(schedule, now: datetime) -> datetime:
    timing = timing_of(schedule)
    if timing.mode == "preset" and schedule.task == "keepalive_tokens":
        from admin.scheduler import _next_keepalive_at

        return _next_keepalive_at(now)
    if timing.mode in ("preset", "interval"):
        minutes = schedule.interval_minutes or 60
        # 区间任务也抖：1440 分钟（日）抖 ±3%（约 ±43 分钟），1 小时抖 ±2 分钟。
        # 下界 60 秒、上界 45 分钟 —— 抖太狠会让「每小时」变成「每两小时」。
        spread = max(1.0, min(45.0, minutes * 0.03))
        return _apply_jitter(now + timedelta(minutes=minutes), spread)
    local = now + timedelta(hours=8)
    hour, minute = map(int, timing.time.split(":"))
    jitter = _daily_jitter_minutes(schedule)
    if timing.mode == "monthly":
        year, month = local.year, local.month
        for _ in range(3):
            day = min(timing.monthday, calendar.monthrange(year, month)[1])
            target = datetime(year, month, day, hour, minute)
            if target > local:
                shifted = _apply_jitter(target, jitter)
                return (shifted if shifted > local else target) - timedelta(hours=8)
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    for days in range(9):
        target = (local + timedelta(days=days)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target > local and (timing.mode == "daily" or target.weekday() in timing.weekdays):
            shifted = _apply_jitter(target, jitter)
            # 关键：抖动不能把时刻拉到「已过去」，否则任务会被判定为当场到期、
            # 立刻执行 —— 抖了等于没抖。拉回去就退回未抖动的目标。
            return (shifted if shifted > local else target) - timedelta(hours=8)
    raise ValueError("无法计算下次执行时间")


def cycle_label(schedule) -> str:
    timing = timing_of(schedule)
    if timing.mode == "preset" and schedule.task == "keepalive_tokens":
        from admin.config import settings
        hours = sorted(set(h for h in settings.KEEPALIVE_HOURS if 0 <= h <= 23)) or [22]
        return "每天 " + "、".join(f"{h:02d}:00" for h in hours) + "（系统时区）"
    if timing.mode in ("preset", "interval"):
        minutes = schedule.interval_minutes or 60
        for size, unit in ((10080, "周"), (1440, "天"), (60, "小时")):
            if minutes % size == 0:
                return f"每 {minutes // size} {unit}"
        return f"每 {minutes} 分钟"
    if timing.mode == "daily":
        return f"每天 {timing.time}"
    if timing.mode == "weekly":
        days = "、".join("周" + "一二三四五六日"[day] for day in sorted(set(timing.weekdays)))
        return f"每{days} {timing.time}"
    return f"每月 {timing.monthday} 日 {timing.time}"


DEFAULT_NAMES = {
    "refresh_balances": ("整点刷新平台总积分", "刷新平台总积分"),
    "refresh_credits": ("每 2 小时更新积分到期快照", "更新积分到期快照"),
    "sync_models": ("每日同步模型列表", "同步模型列表"),
    "daily_checkin": ("每日签到领取积分", "签到领取积分"),
    "refresh_growth_tasks": ("每日更新成长任务列表", "更新成长任务列表"),
    "run_growth_tasks": ("每日自动做成长任务", "自动做成长任务"),
    "run_growth_plan": ("WorkBuddy 成长计划巡检", "WorkBuddy 成长计划巡检"),
    "keepalive_tokens": ("每日 token 保活刷新", "token 保活刷新"),
}


def display_name(schedule) -> str:
    original, base = DEFAULT_NAMES.get(schedule.task, ("", schedule.task))
    if not schedule.name or schedule.name in (original, base, "token 保活（按整点）"):
        return f"{cycle_label(schedule)} · {base}"
    return schedule.name