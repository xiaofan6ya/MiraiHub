"""统计结果共享缓存：L1 进程内 LRU + L2 Redis + 单飞（single-flight）。

## 为什么收敛成一个模块

此前每个统计接口各养一份缓存：logs.py 的 OrderedDict+单飞、stats.py 的裸
dict（无单飞、无上限）、overview_router.py 的 dict+锁（无单飞）。行为不一致：
并发同 key 会打成倍 SQL，键多时无限膨胀。统一到这里：

* **L1** —— 进程内 LRU（默认 256 条），命中 ~1µs；
* **L2** —— Redis JSON（TTL 上限 120s）。进程重启后的第一波请求不再全量
  打库；将来多进程/多实例部署时天然共享一份统计结果；
* **单飞** —— 同 key 并发重算时后来者等 leader 结果。2 核机器上并发重算
  同一条聚合会把单次 200ms 拖到 1s+（logs.py 里实测过的教训）；
* **尽力而为**：Redis 不可用自动退化为纯内存，任何缓存故障都不影响请求；
* 值必须能过 `json.dumps`（`default=str` 兜底），不序列化的对象只进 L1。

观测入口：`status()` 汇总进 `/api/stats/memory`（process_memory 也在读）。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import OrderedDict

logger = logging.getLogger("admin.stats_cache")

#: L1 容量：统计缓存键空间很小（窗口 × 筛选组合），256 绰绰有余
_L1_MAX = 256
#: L1 默认 TTL（调用方几乎都显式传）
_DEFAULT_TTL = 5.0
#: L2（Redis）TTL 上限：观测数据宁可短，防「重启后读到 2 分钟前的旧数」
_REDIS_TTL_CAP = 120.0
#: 单飞等待上限（leader 的 SQL 本身受请求超时约束，这是兜底）
_INFLIGHT_WAIT = 30.0

_l1: "OrderedDict[tuple, tuple[float, object]]" = OrderedDict()
_l1_lock = threading.Lock()
_inflight: dict[tuple, threading.Event] = {}
_inflight_lock = threading.Lock()

_redis = None
_redis_ready = False
_redis_lock = threading.Lock()


def _rclient():
    """Redis 客户端（懒加载 + 一次性探测，失败永久降级为 None）。"""
    global _redis, _redis_ready
    with _redis_lock:
        if _redis_ready:
            return _redis
        _redis_ready = True
        try:
            import redis

            from admin.config import settings

            # protocol=2 与 traffic_stats 同款：老 Redis（<6.0）不认识 RESP3
            # 的 HELLO 握手，显式钉在 RESP2 才能真正连上；太老的 redis-py
            # 不认这个参数会抛 TypeError，去掉再试。
            try:
                client = redis.Redis.from_url(
                    settings.REDIS_URL, decode_responses=True,
                    socket_connect_timeout=1.0, socket_timeout=1.0, protocol=2)
                client.ping()
            except TypeError:
                client = redis.Redis.from_url(
                    settings.REDIS_URL, decode_responses=True,
                    socket_connect_timeout=1.0, socket_timeout=1.0)
                client.ping()
            _redis = client
            logger.info("统计缓存已接入 Redis（L2）")
        except Exception as exc:  # noqa: BLE001 - 无 Redis 是常态而非异常
            _redis = None
            logger.info("统计缓存 L2 降级为纯内存（Redis 不可用：%s）", exc)
        return _redis


def cache_key(kind, values) -> tuple:
    """(kind, values) -> 可哈希键。values 必须是可 JSON 序列化的元组/列表。"""
    return kind, json.dumps(values, sort_keys=True, default=str, separators=(",", ":"))


def _l1_get(key):
    now = time.monotonic()
    with _l1_lock:
        entry = _l1.get(key)
        if entry is None:
            return None
        expires, value = entry
        if expires <= now:
            _l1.pop(key, None)
            return None
        _l1.move_to_end(key)
        return value


def _l1_put(key, value, ttl):
    with _l1_lock:
        _l1[key] = (time.monotonic() + ttl, value)
        _l1.move_to_end(key)
        while len(_l1) > _L1_MAX:
            _l1.popitem(last=False)


def _redis_get(key):
    client = _rclient()
    if client is None:
        return None
    try:
        raw = client.get("wb:stats:" + key[0] + ":" + key[1])
        return json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001 - 缓存永远不能打断业务
        return None


def _redis_put(key, value, ttl):
    client = _rclient()
    if client is None:
        return
    try:
        payload = json.dumps(value, ensure_ascii=False, default=str)
        client.setex("wb:stats:" + key[0] + ":" + key[1],
                     int(min(max(ttl, 10.0), _REDIS_TTL_CAP)), payload)
    except Exception:  # noqa: BLE001
        pass


def cache_get(key):
    """L1 -> L2 查找；L2 命中回填 L1。未命中返回 None。"""
    hit = _l1_get(key)
    if hit is not None:
        return hit
    hit = _redis_get(key)
    if hit is not None:
        _l1_put(key, hit, _DEFAULT_TTL)
    return hit


def cache_put(key, value, ttl=None):
    """写入两层。序列化失败的值只进 L1（Redis 层静默跳过）。"""
    ttl = _DEFAULT_TTL if ttl is None else float(ttl)
    _l1_put(key, value, ttl)
    _redis_put(key, value, ttl)


def cached(key, compute, ttl=None):
    """单飞 + 两级缓存地跑一次聚合。

    ``compute`` 在调用方会话上执行纯 SQL 工作；leader 失败时后来者
    自行重算（等待超时或拿到空结果都会落到这条路径）。
    """
    hit = _l1_get(key)
    if hit is not None:
        return hit
    with _inflight_lock:
        pending = _inflight.get(key)
        leader = pending is None
        if leader:
            pending = _inflight[key] = threading.Event()
    if not leader:
        pending.wait(timeout=_INFLIGHT_WAIT)
        hit = _l1_get(key)
        if hit is not None:
            return hit
        # leader 失败/超时：自己算，不再排队
    try:
        value = compute()
    finally:
        if leader:
            with _inflight_lock:
                _inflight.pop(key, None)
            pending.set()
    cache_put(key, value, ttl)
    return value


def stats_cache_status() -> dict:
    """缓存观测（/api/stats/memory 与 process_memory 汇总用）。"""
    client = _rclient()
    with _l1_lock:
        entries = len(_l1)
    return {"name": "stats.shared", "entries": entries, "max_entries": _L1_MAX,
            "layers": 2 if client is not None else 1,
            "inflight": len(_inflight)}
