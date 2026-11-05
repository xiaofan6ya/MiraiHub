"""事件循环卡死探针。

## 为什么需要它

2026-11-05 线上「导入账号直接卡死」的真实原因不是导入逻辑，而是
**某个 Loomy 上游请求在事件循环线程里同步阻塞**：`admin.server` 是
单进程单 worker，事件循环一被钉住，控制台、账号导入、同步接口、健康
检查**全部**无响应，从外面看就是「服务死了」。

当时的现场只能靠临时装 `py-spy` 才抓到栈（`_request` → `urlopen` →
`ssl.read`），既慢又依赖网络。事后重启，现场就永久丢了。

这个探针用一条**独立线程**盯着事件循环的心跳：主线程一旦超过阈值没有
心跳，就把它的调用栈直接写进日志 —— 等价于内置了一个轻量 py-spy。
它只读 `sys._current_frames()`，不改任何业务状态，开销是每 0.5 秒
一次时间戳更新。
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
import traceback

logger = logging.getLogger("admin.loop_watch")

#: 心跳周期（秒）。必须远小于阈值，否则正常抖动会被误判。
BEAT = 0.5
#: 连续多久没有心跳就判定为「卡死」（秒）。
THRESHOLD = 3.0
#: 同一次阻塞最多打几份栈（避免长时间卡死把日志刷爆）。
MAX_DUMPS = 3
#: 两次栈之间的最小间隔（秒）。
DUMP_GAP = 10.0
#: 启动宽限期（秒）：进程刚起来时的初始化本身就慢，别把它报成故障。
WARMUP = 20.0

_STATE = {
    "beat": time.monotonic(),
    "loop_tid": 0,
    "blocked_since": 0.0,
    "dumps": 0,
    "last_dump": 0.0,
    "started": False,
    "armed_at": 0.0,
    "task": None,
}


def _enabled() -> bool:
    return (os.getenv("ADMIN_LOOP_WATCH", "1") or "").strip().lower() not in (
        "0", "false", "off", "no")


def _dump_stack() -> str:
    """取事件循环线程当前的 Python 栈（纯读操作）。

    只取「最内层」会全是 sqlalchemy / ssl 的库帧，看不出是谁调用的；
    只取「最外层」又看不到卡在哪一句。两头各取几帧最有用。
    """
    frame = sys._current_frames().get(_STATE["loop_tid"])
    if frame is None:
        return "<取不到事件循环线程的栈>"
    frames = traceback.format_stack(frame)
    if len(frames) <= 12:
        return "".join(frames).rstrip()
    return ("".join(frames[:6])
            + "  ...（中间省略 %d 帧）...\n" % (len(frames) - 12)
            + "".join(frames[-6:])).rstrip()


def _watch() -> None:
    while True:
        time.sleep(BEAT)
        try:
            now = time.monotonic()
            lag = now - _STATE["beat"]
            if lag < THRESHOLD:
                if _STATE["blocked_since"]:
                    held = now - _STATE["blocked_since"]
                    logger.warning(
                        "事件循环已恢复：本次阻塞约 %.1f 秒（期间所有请求都会"
                        "排队/超时）", held)
                    _STATE["blocked_since"] = 0.0
                    _STATE["dumps"] = 0
                continue
            if not _STATE["blocked_since"]:
                _STATE["blocked_since"] = _STATE["beat"]
            if now - _STATE["armed_at"] < WARMUP:
                # 启动期（建表 / 载入目录 / 装配号池）本来就慢，不算故障。
                # 同时把心跳基准推到现在，避免宽限期结束后用一段陈旧的
                # `beat` 算出「假阻塞」。
                _STATE["beat"] = now
                _STATE["blocked_since"] = 0.0
                continue
            if (_STATE["dumps"] < MAX_DUMPS
                    and now - _STATE["last_dump"] >= DUMP_GAP):
                _STATE["dumps"] += 1
                _STATE["last_dump"] = now
                logger.error(
                    "事件循环卡死 %.1f 秒！主线程调用栈（这就是全站无响应的"
                    "原因，请把这段栈一并反馈）：\n%s",
                    lag, _dump_stack())
        except Exception:  # noqa: BLE001 - 探针自己绝不能挂
            logger.exception("事件循环探针异常（不影响业务）")


async def _beat_loop() -> None:
    while True:
        _STATE["beat"] = time.monotonic()
        await asyncio.sleep(BEAT)


def start(app=None) -> bool:
    """在事件循环里调用一次；返回是否真的启动。"""
    if _STATE["started"]:
        return False
    if not _enabled():
        logger.info("事件循环探针已按 ADMIN_LOOP_WATCH 关闭")
        return False
    _STATE["loop_tid"] = threading.get_ident()
    _STATE["beat"] = time.monotonic()
    _STATE["armed_at"] = time.monotonic()
    _STATE["blocked_since"] = 0.0
    _STATE["dumps"] = 0
    _STATE["last_dump"] = 0.0
    try:
        _STATE["task"] = asyncio.get_running_loop().create_task(_beat_loop())
    except RuntimeError:  # 不在事件循环里（例如被脚本直接调用）
        return False
    threading.Thread(target=_watch, name="wb-loop-watch", daemon=True).start()
    _STATE["started"] = True
    logger.info("事件循环探针已启动：超过 %.0f 秒无心跳即打印主线程调用栈",
                THRESHOLD)
    return True


async def stop() -> None:
    task = _STATE.get("task")
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    _STATE["task"] = None
    _STATE["started"] = False
