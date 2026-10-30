"""进程内存观测：RSS 基线 + 各内存缓存的实际占用。

为什么需要
----------
1.16.0 之后有好几个进程内缓存（请求载荷、日志统计、模型繁忙、IP 活动、
成长任务…）。它们各自有上限，但**没有一个地方能看到当前实际占了多少**：
长跑之后 RSS 涨了，是缓存堆积、连接池没归还，还是 Python 分配器碎片，
以前只能靠猜。

这里只做**观测**，不改变任何缓存的行为：
* `rss_bytes` 走 psutil（若装了）→ Windows GlobalMemoryStatusEx 兜底 →
  `/proc/self/status` 兜底，三级降级，任何平台都不会因为缺依赖报错；
* `caches` 逐个调用缓存模块自己的 `status()`；某个模块重构掉了就跳过，
  不让观测接口本身成为故障点。

缓存注册表故意保持"笨"：手写模块名 + 函数名，没有自动发现、没有反射。
自动发现看着聪明，但会把「这个模块已经不存在了」变成线上 500。
"""
from __future__ import annotations

import os
import sys

#: (模块路径, status 函数名, 展示名)。函数不存在 = 跳过。
_CACHE_SOURCES = (
    ("admin.request_payload", "status", "request_payload"),
    ("admin.routers.logs", "stats_cache_status", "logs.stats"),
    ("admin.routers.growth", "acct_tasks_cache_status", "growth.account_tasks"),
    ("admin.routers.ip_management", "activity_cache_status", "ip_management.activity"),
    ("admin.model_busy", "status", "model_busy"),
    ("admin.model_catalog", "status", "model_catalog"),
)


def _rss_bytes() -> tuple[int | None, str]:
    """当前进程常驻内存。返回 (字节数, 取值来源)。"""
    try:
        import psutil  # 可选依赖
        return int(psutil.Process().memory_info().rss), "psutil"
    except Exception:
        pass
    if sys.platform == "win32":
        try:
            import ctypes

            class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                            ("PeakWorkingSetSize", ctypes.c_size_t),
                            ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t),
                            ("PeakPagefileUsage", ctypes.c_size_t)]

            counters = _PROCESS_MEMORY_COUNTERS()
            counters.cb = ctypes.sizeof(counters)
            # GetCurrentProcess() 返回伪句柄 -1，不需要 CloseHandle
            if ctypes.windll.psapi.GetProcessMemoryInfo(
                    ctypes.windll.kernel32.GetCurrentProcess(),
                    ctypes.byref(counters), counters.cb):
                return int(counters.WorkingSetSize), "win32.WorkingSetSize"
        except Exception:
            pass
    try:
        with open("/proc/self/status", encoding="ascii", errors="ignore") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024, "proc.VmRSS"
    except OSError:
        pass
    return None, "unavailable"


def _gc_counts() -> dict:
    import gc
    counts = gc.get_count()
    return {"gen0": counts[0], "gen1": counts[1], "gen2": counts[2],
            "tracked_objects": len(gc.get_objects())}


def caches() -> list[dict]:
    """逐个缓存取 status()；缺失或抛异常的模块记为 skipped，绝不冒泡。"""
    import importlib

    out: list[dict] = []
    for module_name, func_name, label in _CACHE_SOURCES:
        try:
            module = importlib.import_module(module_name)
            getter = getattr(module, func_name, None)
            if getter is None:
                out.append({"name": label, "skipped": "no_status_function"})
                continue
            value = getter()
            out.append(value if isinstance(value, dict) else {"name": label, "value": value})
        except Exception as exc:  # 观测接口不能因为某个模块坏掉而整体失败
            out.append({"name": label, "error": f"{type(exc).__name__}: {str(exc)[:120]}"})
    return out


def snapshot() -> dict:
    import time

    rss, source = _rss_bytes()
    return {
        "rss_bytes": rss,
        "rss_mb": round(rss / 1024 / 1024, 1) if rss else None,
        "rss_source": source,
        "pid": os.getpid(),
        "observed_at": time.time(),
        "gc": _gc_counts(),
        "caches": caches(),
    }