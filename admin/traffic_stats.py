"""平台实时流量统计（Redis 每日计数 + 最近请求环形缓冲）。

## 为什么不能靠 `usage_logs` 做实时面板

用量日志是**账本**：只记录真正打到上游的调用，准入拒绝、无号 503、
超时、客户端断开这些都没有账本行（也不该有 —— 它们不扣积分）。
但「实时流量」这个面板要看的就是这些结果，否则用户只看到
「日志里一堆超时 / 503 却没记录」（本次反馈的原话）。

## 为什么放 Redis、按天分键

  * 热路径每次 `/v1` 请求都要写一次，写库会与业务写入抢连接；
  * 数据只关心「今天 / 最近一小时」，天然可过期；
  * 按天分键 `wb:traffic:YYYYMMDD:<platform>`，TTL 3 天，
    不需要清理任务，也不会把内存/磁盘撑爆；
  * Redis 不可用时降级到进程内存（重启即丢，但不影响转发）。

## 记录的是「请求结果」而不是「上游调用」

字段：`requests / ok / error / timeout / last_at / last_status`。
`ok` 只看 HTTP < 400 且没有上游错误；超时单独计，方便一眼看出
「平台是在排队还是在报错」。
"""
from __future__ import annotations

import json
import logging
import threading
import time

logger = logging.getLogger("admin.traffic")

PREFIX = "wb:traffic:"
RECENT_KEY = PREFIX + "recent"
#: 进行中的请求（hash: rid -> item）。**与 RECENT_KEY 分开存**是必须的：
#: 有序集合没法按成员内容精确删除，放进去就会出现「请求结束了、
#: 面板还显示进行中」的永久残留。这里用 hash，结束时 hdel 即清。
RUNNING_KEY = PREFIX + "running"
#: 每日计数保留 3 天（跨天排障够用，也不会无限增长）
DAY_TTL = 3 * 24 * 3600
#: 最近请求明细保留 1 小时
RECENT_TTL = 3600
#: 明细条数上限
RECENT_MAX = 300

_lock = threading.Lock()
_redis = None
_redis_ready = False
_memory_days: dict[str, dict[str, dict]] = {}
_memory_recent: list[tuple[float, dict]] = []
#: 内存兜底模式下的「进行中」请求（rid -> item）
_memory_running: dict[str, dict] = {}


def _client():
    global _redis, _redis_ready
    with _lock:
        if _redis_ready:
            return _redis
        _redis_ready = True
        try:
            import redis

            from admin.config import settings

            _redis = redis.Redis.from_url(
                settings.REDIS_URL, decode_responses=True,
                socket_connect_timeout=1.0, socket_timeout=1.0, protocol=2)
            _redis.ping()
            logger.info("实时流量统计已接入 Redis")
        except TypeError:
            try:
                import redis

                from admin.config import settings

                _redis = redis.Redis.from_url(
                    settings.REDIS_URL, decode_responses=True,
                    socket_connect_timeout=1.0, socket_timeout=1.0)
                _redis.ping()
            except Exception as exc:  # noqa: BLE001
                _redis = None
                logger.warning("实时流量统计降级为内存（Redis 不可用：%s）", exc)
        except Exception as exc:  # noqa: BLE001
            _redis = None
            logger.warning("实时流量统计降级为内存（Redis 不可用：%s）", exc)
        return _redis


def _day() -> str:
    return time.strftime("%Y%m%d")


def _normalize(platform: str) -> str:
    """平台名统一成**对外平台 id**（带区后缀的那种）。

    这里**故意不合并到 storage**：同一产品的国内版与国际版是两个账号池、
    两份额度，合并成一行会把「国际版只有 1 个请求、国内版 47 个」混掉，
    平台健康条也就看不出是哪一区在失败。

    只做两件事：大小写归一；以及把**没有区信息的裸落库名**归到该产品
    唯一的对外 id（历史数据里曾经这么写过，否则会多出一行裸名）。
    """
    name = str(platform or "unknown").strip().lower() or "unknown"
    try:
        from admin.platform import registry

        # 裸落库名（没有区后缀）且该 storage 只有一个对外 id 时直接用它，
        # 这样不会与带区后缀的 id 并存成两行。
        spec = registry.get(name)
        if spec is None:
        # 裸落库名：registry 里只有区级 id，归到该产品的**默认区**，
        # 否则会和区级 id 一起在面板上出现多行同名平台。
            siblings = [sp for sp in registry.all_platforms()
                        if sp.storage() == name]
            if siblings:
                cn = next((sp for sp in siblings if sp.realm == "cn"), siblings[0])
                return cn.id
        elif spec.id == name and spec.realm:
            return spec.id
    except Exception:  # noqa: BLE001 - 反查失败按原值
        pass
    return name


#: 只记「真的会产生对话流量」的路径。
#:
#: 哨兵页看的是「谁在烧额度 / 谁在报错」，`/v1/models`、`/v1/balance`
#: 这类目录查询既不计费也不代表模型健康度，记进去纯属噪音 ——
#: 最近请求列表会被它们刷屏。
TRACKED_PATHS = frozenset({
    "/v1/chat/completions",
    "/v1/responses",
    "/v1/messages",
    "/v1/messages/count_tokens",
})

#: 明确**不记**的路径（即使将来新增了别的 TRACKED，也优先排除这些）
SKIP_PATHS = frozenset({"/v1/models", "/v1/balance", "/v1/models/"})


def start(platform: str, *, model: str = "", path: str = "",
          at: float | None = None) -> str:
    """**请求开始**时调一次，立刻在「最近请求」里出现一行 running。

    为什么需要它：原来只有 `record()`（请求结束）才写，一个跑 30 秒的任务
    前 28 秒面板上什么都看不到。这里在开始时先落一条 `outcome=running` 的占位，
    结束时由 `record()` 用同一个 `rid` 覆盖掉。

    返回 `rid`（请求标识），调用方把它传给 `record(..., rid=rid)`。
    绝不抛异常：统计坏了不能影响转发。
    """
    import uuid as _uuid

    rid = _uuid.uuid4().hex[:16]
    try:
        platform = _normalize(platform)
        now = at if at is not None else time.time()
        item = {
            "ts": round(now, 3),
            "platform": platform,
            "outcome": "running",
            "status": 0,
            "elapsed_ms": 0,
            "model": str(model or "")[:120],
            "path": str(path or "")[:80],
            "error": "",
            "rid": rid,
        }
        client = _client()
        if client is not None:
            try:
                pipe = client.pipeline()
                # ⚠️ **绝不能写进 RECENT_KEY**。
                #
                # RECENT_KEY 是有序集合（最近请求列表），成员是 JSON 串，
                # 只能按 score 或 rank 删，**没法按 rid 精确删除**。
                # 之前把 running 也 zadd 进去，`finish_running` 只 hdel 了
                # 哈希表 —— 于是 zset 里那条 running 永久残留，
                # 面板上「进行中」永远不消失（实测堆了 60 条）。
                #
                # 现在 running 只存独立哈希，`snapshot()` 读列表时临时合并，
                # 结束直接 hdel 就干净了。
                pipe.hset(RUNNING_KEY, rid, json.dumps(item, ensure_ascii=False))
                pipe.expire(RUNNING_KEY, RECENT_TTL)
                pipe.execute()
                return rid
            except Exception:  # noqa: BLE001
                logger.debug("running 写 Redis 失败", exc_info=True)
        with _lock:
            _memory_running[rid] = item
    except Exception:  # noqa: BLE001
        logger.debug("traffic start 失败", exc_info=True)
    return rid


def finish_running(rid: str) -> None:
    """请求结束时清掉 running 占位（`record()` 会写入正式记录）。"""
    if not rid:
        return
    try:
        client = _client()
        if client is not None:
            client.hdel(RUNNING_KEY, rid)
        else:
            with _lock:
                _memory_running.pop(rid, None)
    except Exception:  # noqa: BLE001
        pass


def record(platform: str, *, outcome: str, status: int = 0,
           elapsed_ms: float = 0, model: str = "", path: str = "",
           error: str = "", at: float | None = None,
           rid: str = "") -> None:
    """记一次请求结果。**绝不抛异常**：统计坏了不能影响转发。

    `path` 不是对话端点时直接跳过 —— 口径见 `TRACKED_PATHS`。
    """
    try:
        _path = str(path or "").strip()
        if _path:
            if _path in SKIP_PATHS:
                return
            if _path not in TRACKED_PATHS:
                return
        platform = _normalize(platform)
        now = at if at is not None else time.time()
        status = int(status or 0)
        outcome = str(outcome or "")
        timed_out = outcome == "timeout" or status == 504
        failed = (not timed_out) and (outcome not in ("completed", "ok")
                                      or status >= 400)
        # `error` 传的是 error_kind，成功请求会写 "success"/"ok"；
        # 它只用于「为什么失败」的展示，不该把成功记录染成失败色。
        kind = str(error or "").strip().lower()
        if kind in ("success", "ok", "none", "0"):
            kind = ""
        item = {
            "ts": round(now, 3),
            "platform": platform,
            "outcome": outcome or "completed",
            "status": status,
            "elapsed_ms": round(float(elapsed_ms or 0), 1),
            "model": str(model or "")[:120],
            "path": str(path or "")[:80],
            "error": kind[:64],
        }
        if rid:
            item["rid"] = rid
        finish_running(rid)
        day = _day()
        client = _client()
        if client is not None:
            try:
                key = "%s%s:%s" % (PREFIX, day, platform)
                pipe = client.pipeline()
                pipe.hincrby(key, "requests", 1)
                pipe.hincrby(key, "ok", 0 if (failed or timed_out) else 1)
                pipe.hincrby(key, "error", 1 if failed else 0)
                pipe.hincrby(key, "timeout", 1 if timed_out else 0)
                pipe.hset(key, mapping={"last_at": now, "last_status": status})
                pipe.expire(key, DAY_TTL)
                pipe.zadd(RECENT_KEY, {json.dumps(item, ensure_ascii=False): now})
                pipe.zremrangebyrank(RECENT_KEY, 0, -(RECENT_MAX + 1))
                pipe.expire(RECENT_KEY, RECENT_TTL)
                pipe.execute()
                return
            except Exception:  # noqa: BLE001 - 降级内存
                logger.debug("流量统计写 Redis 失败，改写内存", exc_info=True)
        with _lock:
            bucket = _memory_days.setdefault(day, {}).setdefault(platform, {
                "requests": 0, "ok": 0, "error": 0, "timeout": 0,
                "last_at": 0, "last_status": 0})
            bucket["requests"] += 1
            if failed:
                bucket["error"] += 1
            elif timed_out:
                bucket["timeout"] += 1
            else:
                bucket["ok"] += 1
            bucket["last_at"] = now
            bucket["last_status"] = status
            _memory_recent.append((now, item))
            cutoff = now - RECENT_TTL
            kept = [row for row in _memory_recent if row[0] >= cutoff][-RECENT_MAX:]
            _memory_recent[:] = kept
            # 只保留最近 3 天，防止长期运行累积
            for stale in list(_memory_days):
                if stale < time.strftime("%Y%m%d", time.localtime(now - DAY_TTL)):
                    _memory_days.pop(stale, None)
    except Exception:  # noqa: BLE001
        logger.debug("流量统计写入失败", exc_info=True)


def _platform_ids() -> list[tuple[str, str]]:
    """`[(public_id, label)]`，按登记顺序。"""
    try:
        from admin.platform import registry
        from admin.platform.loader import load_platforms

        load_platforms()
        return [(spec.id, spec.label) for spec in registry.all_platforms()]
    except Exception:  # noqa: BLE001 - 注册表坏了也要能出图，用默认平台兜底
        from admin.platform import registry as _registry

        spec = _registry.get(_registry.DEFAULT_PLATFORM)
        return [(spec.id if spec else _registry.DEFAULT_PLATFORM,
                 spec.label if spec else _registry.DEFAULT_PLATFORM)]


def snapshot(window: int = 3600) -> dict:
    """今日各平台计数 + 最近 `window` 秒的请求明细。"""
    day = _day()
    since = time.time() - max(60, int(window))
    platforms = []
    totals = {"requests": 0, "ok": 0, "error": 0, "timeout": 0}
    client = _client()
    rows: list[dict] = []
    recent: list[dict] = []

    if client is not None:
        try:
            for platform_id, label in _platform_ids():
                data = client.hgetall("%s%s:%s" % (PREFIX, day, platform_id))
                rows.append(_row(platform_id, label, data))
            # 未登记过的平台（历史 / 手写 platform）也展示，避免数据凭空消失
            known = {row["platform"] for row in rows}
            for key in client.scan_iter(match=PREFIX + day + ":*", count=100):
                platform_id = key.rsplit(":", 1)[-1]
                if platform_id in known:
                    continue
                rows.append(_row(platform_id, platform_id, client.hgetall(key)))
            # redis-py 的 withscores 返回 `(member, score)`；别把 score 当 JSON。
            for raw, _score in client.zrangebyscore(
                    RECENT_KEY, since, "+inf", withscores=True, start=0, num=RECENT_MAX):
                try:
                    recent.append(json.loads(raw))
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            logger.debug("流量统计读 Redis 失败，回退内存", exc_info=True)
            rows, recent = [], []

    if not rows and not recent:
        with _lock:
            for platform_id, label in _platform_ids():
                rows.append(_row(platform_id, label,
                                 _memory_days.get(day, {}).get(platform_id, {})))
            recent = [item for ts, item in _memory_recent if ts >= since]

    # 合并归一后相同的平台（历史数据里裸名与区级 id 是两条 Redis key），
    # 否则面板会出现三行同名平台的 0%/100% 混合，看着像统计错了。
    merged: dict[str, dict] = {}
    for row in rows:
        key = _normalize(row.get("platform"))
        current = merged.get(key)
        if current is None:
            row = dict(row)
            row["platform"] = key
            merged[key] = row
            continue
        for field in ("requests", "ok", "error", "timeout"):
            current[field] = int(current.get(field) or 0) + int(row.get(field) or 0)
        if (row.get("last_at") or 0) > (current.get("last_at") or 0):
            current["last_at"] = row["last_at"]
            current["last_status"] = row.get("last_status")
    rows = list(merged.values())

    for row in rows:
        for field in totals:
            totals[field] += int(row.get(field) or 0)
    # **把「进行中」的请求合并进来**（它们只存在 RUNNING_KEY 哈希里，
    # 不占 RECENT_KEY）。放最前面，因为面板按时间倒序读，进行中的最新。
    running: list[dict] = []
    if client is not None:
        try:
            for raw in client.hvals(RUNNING_KEY):
                try:
                    running.append(json.loads(raw))
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            logger.debug("读 running 失败", exc_info=True)
    else:
        with _lock:
            running = list(_memory_running.values())

    seen = {str(item.get("rid") or "") for item in recent if item.get("rid")}
    running = [r for r in running if str(r.get("rid") or "") not in seen]
    running.sort(key=lambda item: item.get("ts") or 0, reverse=True)
    # 进行中的永远排在正式记录之前
    merged_recent = running + sorted(
        recent, key=lambda item: item.get("ts") or 0, reverse=True)
    return {
        "day": day,
        "window_seconds": max(60, int(window)),
        "platforms": rows,
        "totals": totals,
        "recent": merged_recent[:120],
        "running": len(running),
        "storage": "redis" if client is not None else "memory",
        "now": time.time(),
    }


def _row(platform: str, label: str, data: dict) -> dict:
    def value(name):
        try:
            return int(float(data.get(name) or 0))
        except (TypeError, ValueError):
            return 0

    last_at = data.get("last_at") or 0
    try:
        last_at = float(last_at)
    except (TypeError, ValueError):
        last_at = 0
    return {
        "platform": platform,
        "label": label,
        "requests": value("requests"),
        "ok": value("ok"),
        "error": value("error"),
        "timeout": value("timeout"),
        "last_at": last_at,
        "last_status": value("last_status"),
    }


def reset() -> None:
    """清空统计（排障 / 测试用）。"""
    global _redis, _redis_ready
    client = _client()
    if client is not None:
        try:
            keys = list(client.scan_iter(match=PREFIX + "*", count=200))
            if keys:
                client.delete(*keys)
        except Exception:  # noqa: BLE001
            logger.debug("清空流量统计失败", exc_info=True)
    with _lock:
        _memory_days.clear()
        _memory_recent.clear()
        _memory_running.clear()
        _redis = None
        _redis_ready = False


def status() -> dict:
    return {"name": "traffic", "storage": "redis" if _client() is not None else "memory",
            "day": _day(), "recent": len(_memory_recent)}
