"""人味最小集：把「固定间隔」和「整点批量」换成有分布的随机值。

## 为什么要单独一个模块

风控看的从来不是「你有没有请求」，而是**请求的时间分布形状**：

    · 每隔整 1.00 秒一次、连续 100 次  → 机器
    · 每次间隔 0.6~2.3 秒、偶尔停 40 秒 → 人

我们历史上大量地方写的是 `time.sleep(0.5)`、`time.sleep(settings.GAP)`、
「每天 08:00 整跑一次」—— 这些单独看都很无辜，合起来就是一张
「机器在批量刷号」的画像。

所以这里只做一件事：**给所有「等待」和「启动时刻」一个可解释的分布**。
不是伪造不存在的请求头，也不是假装成别的客户端 —— 只是不要在时间上
排得那么整齐。

## 三个入口

    human_gap(base)       账号/任务之间的间隔（±45%，对数正态更自然）
    human_start_delay(n)  批量任务启动前的随机准备时间（0~n 秒）
    human_schedule_drift(seconds)  定时任务触发时刻的漂移（±seconds）

## 一条硬约束

**不要在循环里用同一个随机种子**。每次调用都重新取随机数，
否则 100 个号的间隔会完全一样。
"""
from __future__ import annotations

import os
import random
import time

#: 间隔抖动幅度（相对比例）。0.45 意味着 `base=2s` 会落在 1.1~2.9 秒。
DEFAULT_SPREAD = 0.45

#: 全局开关。压测/复现问题时可以关掉，让行为完全确定。
ENV_DISABLE = "ADMIN_HUMAN_JITTER"
DISABLED = os.getenv(ENV_DISABLE, "1") == "0"


def enabled() -> bool:
    global DISABLED
    DISABLED = os.getenv(ENV_DISABLE, "1") == "0"
    return not DISABLED


def human_gap(base: float, *, spread: float = DEFAULT_SPREAD,
              floor: float = 0.0, ceil: float | None = None) -> float:
    """把固定间隔 `base` 变成一个**看起来像人**的等待时长。

    ## 为什么用对数正态

    真人的操作间隔不是均匀分布 —— 大量集中在「正常速度」，偶尔冒出一个
    很长的停顿（起身、切窗口、想事情）。均匀分布（`base ± 45%`）在
    直方图上是个方盒子，一样很假。

    所以这里用 `lognormvariate`：中位数锚在 `base`，长尾自然拖出偶发的大间隔。

    `spread` 越大越松。`0.45` 大约对应「多数落在 base 的 0.65~1.6 倍」。
    """
    if not enabled():
        return max(floor, base)
    value = base
    if base > 0:
        # 对数正态的 sigma：spread 0.45 -> 约 0.42，实测手感最接近人工操作
        sigma = max(0.01, spread * 0.95)
        value = random.lognormvariate(0.0, sigma) * base
        # 再叠一层偶发长停顿：约 8% 的概率多停 1.5~3 倍
        if random.random() < 0.08:
            value *= random.uniform(1.5, 3.0)
        # 下界：再快也不该快过 base 的 35%（"急手"也是有下限的，
        # 无下限会跑出 0.1 秒这种人类连点都做不到的间隔）
        floor = max(floor, base * 0.35)
    value = max(floor, value)
    if ceil is not None:
        value = min(ceil, value)
    return value


def human_sleep(base: float, *, spread: float = DEFAULT_SPREAD,
                floor: float = 0.0, ceil: float | None = None) -> float:
    """`human_gap` + 真的睡下去，返回实际睡了多久（便于日志）。"""
    seconds = human_gap(base, spread=spread, floor=floor, ceil=ceil)
    if seconds > 0:
        time.sleep(seconds)
    return seconds


def human_start_delay(span: float = 8.0) -> float:
    """批量任务开始前的随机「准备时间」（0~span 秒）。

    为什么需要：定时任务如果都在同一个 cron 分钟一起启动，上游会看到
    「同一秒内 5 个任务同时开始」—— 这是人绝对不会做的事。

    返回实际延迟秒数（**不睡**，由调用方决定睡不睡）。
    """
    if not enabled() or span <= 0:
        return 0.0
    # 偏向短延迟，偶尔等久一点：指数分布截断到 span
    return min(span, random.expovariate(2.0 / span))


def human_schedule_drift(seconds: float = 240.0) -> float:
    """定时任务的触发时刻漂移：返回 ±seconds 内的随机秒数（可正可负）。

    用来把「每天 08:00:00 整」变成「每天 07:57~08:03 之间某刻」。
    """
    if not enabled() or seconds <= 0:
        return 0.0
    return random.uniform(-seconds, seconds)


def human_probability(p: float) -> bool:
    """以概率 `p` 返回 True（用来让「可选动作」不是每次都做）。

    例：签到完成后 30% 概率再看一眼积分明细 —— 真实用户不会每次只看一个页面。
    """
    if not enabled():
        return True
    return random.random() < p