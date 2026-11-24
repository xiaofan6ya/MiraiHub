"""进程级共享的上游 HTTP 资源（perf 线：第 5 批 P1 / P3 / P4）。

这里只放**和平台无关**的四件事，平台代码按需调用：

1. `client_kwargs(name, timeout=...)`：httpx 系平台（小浣熊 / MiniMax / WPS 灵犀 /
   AStudio / 科搭 / KroWork）共享连接池 + 共享 TLS 上下文。原来每个请求都
   `httpx.Client(...)`：不复用连接（每次多一轮 TCP+TLS，悉尼到各上游 150–600 ms），
   而且不传 `verify=`，每个 Client 都重新加载 certifi（Linux 约 6 ms，
   Windows 实测约 1 s，会重扫系统证书库）。
2. `cap_timeout(timeout)`：建连超时封顶（默认 10 s），读 / 写 / 取连接超时保持原值。
3. `run_blocking(fn, ...)`：阻塞的上游调用离开事件循环，走专用线程池；
   等待方被取消时把线程里已经拿到的资源（账号租约、上游连接）交给 `on_orphan` 收尾。
4. `async_stream(source, ping=...)`：把同步生成器桥成**异步**生成器。
   Starlette 对同步迭代器用 `iterate_in_threadpool`，整条流的每次 `next()` 都占一个
   anyio 线程令牌（默认只有 40 个），并发 40 条非 Marvis 流时所有同步接口排队；
   而且客户端断开后生产线程会把上游读完才退出。这里改成「独立线程生产 +
   `asyncio.Queue` 消费」：不占令牌；消费端结束（正常 / 断连 / 取消）时置停止位，
   生产线程在下一个片段到达时停止读取并 `close()` 源生成器，平台生成器的
   `finally`（关上游响应、释放账号、记账）随即执行。

## 共享连接池为什么不会串号

只共享**传输层**（连接池 + TLS 上下文）。`httpx.Client` 仍然每个请求新建：cookie、
默认请求头、跟随重定向这些「每账号 / 每请求」的状态照旧隔离，A 账号响应里的
Set-Cookie 不会被 B 账号的请求带上。

## 为什么用 `mounts={"all://": ...}` 而不是 `transport=`

httpx 在传了 `transport=` 时**不再读环境代理**（HTTPS_PROXY、Windows 系统代理）——
设备实测：默认 Client 有 2 条代理 mount，传 transport 后 0 条。上一轮的实现因此会让
依赖环境代理出网的部署直连失败。`mounts` 的优先级低于环境代理的 `https://` 模式，
所以：没有环境代理时请求走共享池；有环境代理时仍按原样走代理（每请求的代理传输层，
只是 TLS 上下文不再重建）；`NO_PROXY` 命中的主机走 Client 自己的默认传输层。
代理语义与改动前逐条一致。

## 开关（全部 `os.getenv`，关掉即回到改动前的行为）

| env | 默认 | 含义 |
|---|---|---|
| `ADMIN_UPSTREAM_SHARED_CLIENT` | 1 | 0 = httpx 平台每请求新建传输层（旧行为） |
| `ADMIN_UPSTREAM_SHARED_EXCLUDE` | 空 | 逗号分隔的平台名，这些平台不共享（风控上不想多号共连接时用） |
| `ADMIN_UPSTREAM_POOL_KEEPALIVE` | 16 | 每个（平台, 出口）保留的空闲连接数 |
| `ADMIN_UPSTREAM_KEEPALIVE_EXPIRY` | 30 | 空闲连接保留秒数（低于常见对端 60 s 空闲关闭） |
| `ADMIN_UPSTREAM_POOL_MAX` | 0 | 每个（平台, 出口）同时在用的连接上限；0 = 不限，与旧行为一致，不引入新的排队点 |
| `ADMIN_UPSTREAM_SHARED_MAX` | 32 | 共享传输层条目上限；满了以后新键退回每请求新建（不淘汰在用的池） |
| `ADMIN_UPSTREAM_CONNECT_TIMEOUT` | 10 | 建连超时上限（秒）；0 = 不改调用方的值。urllib 侧（`upstream_http`）共用同一个值 |
| `ADMIN_UPSTREAM_THREADS` | 128 | `run_blocking` 专用线程池大小；0 = 用事件循环默认执行器（`ADMIN_BLOCKING_THREADS`） |
| `ADMIN_STREAM_OFFLOAD` | 1 | 0 = 同步流仍交给 Starlette 线程池 / `with_pings`（占 anyio 令牌，旧行为） |
"""
from __future__ import annotations

import asyncio
import contextvars
import functools
import logging
import os
import queue
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx

_logger = logging.getLogger(__name__)

_FALSE = ("0", "false", "no", "off")


def _env_on(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() not in _FALSE


def _env_int(name: str, default: int, low: int, high: int) -> int:
    raw = (os.getenv(name, "") or "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = default
    return max(low, min(high, value))


def _env_float(name: str, default: float, low: float, high: float) -> float:
    raw = (os.getenv(name, "") or "").strip()
    try:
        value = float(raw) if raw else default
    except ValueError:
        value = default
    return max(low, min(high, value))


_STATS_LOCK = threading.Lock()
_STATS = {
    "shared_clients": 0,      # 用上共享池的 Client 次数
    "fallback_disabled": 0,   # 开关关闭 / 平台被排除
    "fallback_full": 0,       # 共享条目到上限，退回每请求新建
    "orphans": 0,             # 等待方被取消、由 on_orphan 收尾的阻塞调用
    "orphan_errors": 0,
    "streams_started": 0,
    "streams_active": 0,
    "streams_aborted": 0,     # 消费端先走、生产线程提前停读上游
    "streams_reaped": 0,      # 调用方忘了 close、由 GC 兜底回收的上游响应
}


def _bump(key: str, delta: int = 1) -> None:
    with _STATS_LOCK:
        _STATS[key] = _STATS.get(key, 0) + delta


# ---------------------------------------------------------------------------
# 建连超时
# ---------------------------------------------------------------------------
def connect_timeout() -> float:
    """上游建连（TCP + CONNECT + TLS）超时上限，秒；0 表示不单独限制。"""
    raw = (os.getenv("ADMIN_UPSTREAM_CONNECT_TIMEOUT", "") or "").strip()
    try:
        value = float(raw) if raw else 10.0
    except ValueError:
        value = 10.0
    return max(0.0, value)


def cap_timeout(timeout):
    """把 httpx 超时里的「建连」一项压到 `connect_timeout()` 以内。

    读 / 写 / 取连接超时一律保持调用方原值（长生成要等 600 s 照旧）。
    数字超时 `t` 变成 `Timeout(t, connect=min(t, 上限))`；`None`（不限）、
    元组等其他形态原样返回，不猜调用方意图。
    """
    limit = connect_timeout()
    if not limit or timeout is None:
        return timeout
    if isinstance(timeout, httpx.Timeout):
        connect = timeout.connect
        if connect is not None and connect <= limit:
            return timeout
        return httpx.Timeout(connect=limit, read=timeout.read,
                             write=timeout.write, pool=timeout.pool)
    if isinstance(timeout, (int, float)) and not isinstance(timeout, bool):
        value = float(timeout)
        if value <= limit:
            return timeout
        return httpx.Timeout(value, connect=limit)
    return timeout


# ---------------------------------------------------------------------------
# 共享 TLS 上下文 + 共享 httpx 传输层
# ---------------------------------------------------------------------------
_FALLBACK_CTX = None
_FALLBACK_CTX_LOCK = threading.Lock()


def ssl_context():
    """与 `upstream_http.ssl_context()` 同一个对象（系统证书库 + certifi，进程内建一次）。"""
    try:
        from admin.upstream_http import ssl_context as _shared_ctx

        return _shared_ctx()
    except Exception:  # noqa: BLE001 - 独立脚本里没有 admin 包：退回 certifi
        global _FALLBACK_CTX
        with _FALLBACK_CTX_LOCK:
            if _FALLBACK_CTX is None:
                import ssl

                import certifi

                _FALLBACK_CTX = ssl.create_default_context(cafile=certifi.where())
            return _FALLBACK_CTX


#: GC 兜底回收队列。`SimpleQueue.put` 是可重入安全的（官方文档明确可在 `__del__`
#: 里用）；真正的 close 在独立线程里做 —— 在 `__del__` 里直接 close 可能撞上
#: 同一线程正持有的连接池锁（GC 可以在任意字节码处触发）而死锁。
_REAP: "queue.SimpleQueue" = queue.SimpleQueue()
_REAPER_LOCK = threading.Lock()
_REAPER_STARTED = False


def _reaper() -> None:
    while True:
        stream = _REAP.get()
        try:
            stream.close()
        except Exception:  # noqa: BLE001
            pass


def _ensure_reaper() -> None:
    global _REAPER_STARTED
    if _REAPER_STARTED:
        return
    with _REAPER_LOCK:
        if not _REAPER_STARTED:
            threading.Thread(target=_reaper, daemon=True, name="wb-http-reaper").start()
            _REAPER_STARTED = True


class _GuardedStream(httpx.SyncByteStream):
    """响应体外壳：调用方忘了 close 时，对象被回收时把连接还给池（或关掉）。

    每请求新建 Client 的旧写法里，漏关的响应会随 Client 一起被 GC 回收；共享池
    强引用着连接，漏关一次就**永久**占住一条 socket。这层把旧写法的兜底补回来。
    """

    def __init__(self, stream):
        self._stream = stream
        self._closed = False

    def __iter__(self):
        for chunk in self._stream:
            yield chunk

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stream.close()

    def __del__(self):
        if not self._closed:
            self._closed = True
            try:
                _REAP.put(self._stream)
                _STATS["streams_reaped"] = _STATS.get("streams_reaped", 0) + 1
            except Exception:  # noqa: BLE001 - 解释器退出阶段
                pass


class _NoCloseTransport(httpx.BaseTransport):
    """包一层：按请求关闭的 `Client.close()` 不能把进程共享的连接池关掉。

    httpx 的 `Client.close()` 会关闭 `transport` 和**所有 mounts**，所以共享池
    必须包在一个 `close()` 为空的壳里。响应体再套一层 `_GuardedStream`（漏关兜底）。
    """

    def __init__(self, inner: httpx.BaseTransport):
        self._inner = inner

    def handle_request(self, request):
        response = self._inner.handle_request(request)
        _ensure_reaper()
        return httpx.Response(status_code=response.status_code,
                              headers=response.headers,
                              stream=_GuardedStream(response.stream),
                              extensions=response.extensions)

    def close(self):
        return None


_SHARED: dict = {}
_SHARED_LOCK = threading.Lock()


def _norm(name) -> str:
    return str(name or "default").strip().lower() or "default"


def shared_enabled(name) -> bool:
    """该平台是否使用共享连接池（总开关 + 排除名单）。"""
    if not _env_on("ADMIN_UPSTREAM_SHARED_CLIENT", True):
        return False
    raw = os.getenv("ADMIN_UPSTREAM_SHARED_EXCLUDE", "") or ""
    excluded = {part.strip().lower() for part in raw.split(",") if part.strip()}
    return _norm(name) not in excluded


def _limits() -> httpx.Limits:
    keepalive = _env_int("ADMIN_UPSTREAM_POOL_KEEPALIVE", 16, 0, 256)
    expiry = _env_float("ADMIN_UPSTREAM_KEEPALIVE_EXPIRY", 30.0, 1.0, 600.0)
    cap = _env_int("ADMIN_UPSTREAM_POOL_MAX", 0, 0, 4096)
    return httpx.Limits(max_connections=cap or None,
                        max_keepalive_connections=keepalive,
                        keepalive_expiry=expiry)


def shared_transport(name, *, proxy: str | None = None):
    """按（平台, 显式出口）取共享传输层；条目到上限时返回 None（调用方退回旧行为）。"""
    key = (_norm(name), str(proxy or "").strip())
    with _SHARED_LOCK:
        inner = _SHARED.get(key)
    if inner is None:
        ctx = ssl_context()          # 可能较慢（首次建上下文），不在锁里做
        kwargs = {"verify": ctx, "limits": _limits()}
        if key[1]:
            kwargs["proxy"] = key[1]
        with _SHARED_LOCK:
            inner = _SHARED.get(key)
            if inner is None:
                if len(_SHARED) >= _env_int("ADMIN_UPSTREAM_SHARED_MAX", 32, 1, 1024):
                    full = True
                else:
                    full = False
                    inner = httpx.HTTPTransport(**kwargs)
                    _SHARED[key] = inner
            else:
                full = False
        if full:
            _bump("fallback_full")
            return None
    return _NoCloseTransport(inner)


def client_kwargs(name, *, timeout=None, proxy: str | None = None) -> dict:
    """`httpx.Client(**client_kwargs("raccoon", timeout=TIMEOUT), follow_redirects=...)`。

    返回的字典里：
      * `timeout`：建连封顶后的超时（调用方给了才有）；
      * `verify` + `mounts`：共享 TLS 上下文和共享连接池（开关打开时才有）；
      * `trust_env=False`：只在显式传了 `proxy` 时出现（显式出口优先于环境代理）。

    开关关闭 / 平台被排除 / 条目满时只返回 `timeout`，Client 的其余行为与改动前一致。
    """
    out: dict = {}
    if timeout is not None:
        out["timeout"] = cap_timeout(timeout)
    if not shared_enabled(name):
        _bump("fallback_disabled")
        return out
    transport = shared_transport(name, proxy=proxy)
    if transport is None:
        return out
    out["verify"] = ssl_context()
    out["mounts"] = {"all://": transport}
    if proxy:
        out["trust_env"] = False
    _bump("shared_clients")
    return out


def reset_shared() -> None:
    """关闭并丢弃全部共享传输层（测试 / 排障 / 进程退出用）。"""
    with _SHARED_LOCK:
        items = list(_SHARED.values())
        _SHARED.clear()
    for inner in items:
        try:
            inner.close()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# 阻塞调用离开事件循环（P1）
# ---------------------------------------------------------------------------
_EXECUTOR: ThreadPoolExecutor | None = None
_EXECUTOR_LOCK = threading.Lock()


def _executor():
    """上游阻塞调用专用线程池（与 DB 小查询用的默认执行器隔离）。

    Trae / KroWork 的 `_run` 要一直等到上游首帧，非流式更要等整段生成 —— 放进默认
    执行器（`ADMIN_BLOCKING_THREADS`，默认 64）会把鉴权、记账这些毫秒级的 to_thread
    挤到后面。0 = 不单独建池，用事件循环默认执行器。
    """
    global _EXECUTOR
    workers = _env_int("ADMIN_UPSTREAM_THREADS", 128, 0, 1024)
    if workers <= 0:
        return None
    with _EXECUTOR_LOCK:
        if _EXECUTOR is None:
            _EXECUTOR = ThreadPoolExecutor(max_workers=max(8, workers),
                                           thread_name_prefix="wb-upstream")
        return _EXECUTOR


def _run_orphan(on_orphan, result) -> None:
    try:
        on_orphan(result)
    except Exception:  # noqa: BLE001 - 收尾失败只记日志
        _bump("orphan_errors")
        _logger.exception("上游调用的等待方已取消，收尾失败")


def _orphan_done(on_orphan, future) -> None:
    if future.cancelled():
        return
    if future.exception() is not None:   # 取走异常，避免 "exception was never retrieved"
        return
    _bump("orphans")
    threading.Thread(target=_run_orphan, args=(on_orphan, future.result()),
                     daemon=True, name="wb-orphan-cleanup").start()


async def run_blocking(fn, /, *args, on_orphan=None, **kwargs):
    """在线程里执行阻塞调用（带 contextvars），返回其结果。

    与 `asyncio.to_thread` 的区别：

    * 走专用线程池（见 `_executor`）；
    * **取消安全**：同步版本不会在中途被取消，放进线程后就会（中间件断连检测、
      超时、进程排空）。线程里的调用照样会跑完并拿到账号租约 / 打开上游连接，
      而等待方已经走了 —— 资源就泄漏了。传 `on_orphan(result)` 时，这种情况下
      结果会交给它（在独立线程里）去释放；调用本身抛异常时不调用。
    """
    loop = asyncio.get_running_loop()
    ctx = contextvars.copy_context()
    call = functools.partial(ctx.run, fn, *args, **kwargs)
    future = loop.run_in_executor(_executor(), call)
    try:
        return await asyncio.shield(future)
    except asyncio.CancelledError:
        if on_orphan is not None:
            future.add_done_callback(functools.partial(_orphan_done, on_orphan))
        else:
            future.add_done_callback(_swallow)
        raise


def _swallow(future) -> None:
    if not future.cancelled():
        future.exception()


# ---------------------------------------------------------------------------
# 同步流 → 异步流（P4：流式不再占 anyio 令牌；断连时停止读上游）
# ---------------------------------------------------------------------------
_DONE = object()


def stream_offload_enabled() -> bool:
    return _env_on("ADMIN_STREAM_OFFLOAD", True)


def _close_quietly(obj) -> None:
    close = getattr(obj, "close", None)
    if close is None:
        return
    try:
        close()
    except Exception:  # noqa: BLE001
        pass


#: 公开别名：给 `run_blocking(..., on_orphan=http_shared.close_quietly)` 用。
close_quietly = _close_quietly


def async_stream(source, *, ping: bytes | None = None, interval: float | None = None):
    """把同步迭代器交给 `StreamingResponse` 前先过这里。

    * `ping` 不为空：空闲超过 `interval`（默认 `protocol_bridge.PING_INTERVAL_S`）
      没有新片段就补一帧心跳，语义与 `protocol_bridge.with_pings` 相同；
    * 开关 `ADMIN_STREAM_OFFLOAD=0`：原样返回（有 ping 时返回 `with_pings(...)`），
      即改动前的写法。
    """
    if not stream_offload_enabled():
        if ping is None:
            return source
        from admin.platform import protocol_bridge as bridge

        return bridge.with_pings(source, interval=interval or bridge.PING_INTERVAL_S,
                                 ping=ping)
    return _bridge(source, ping, interval)


def aiter_sync(source):
    """**一定**返回异步迭代器，给只接受 async `body_iterator` 的协议中继用。

    科搭 / MiniMax 的翻译中继原来写成 `async def _agen(): for line in 同步迭代器: yield`，
    同步读上游就发生在事件循环上，整条流期间全站冻结。开关打开时走异步桥；
    `ADMIN_STREAM_OFFLOAD=0` 时退回原写法（行为与改动前一致）。
    """
    if stream_offload_enabled():
        return _bridge(source, None, None)
    return _sync_as_async(source)


async def _sync_as_async(source):
    for item in source:
        yield item


def _default_interval() -> float:
    try:
        from admin.platform.protocol_bridge import PING_INTERVAL_S

        return float(PING_INTERVAL_S)
    except Exception:  # noqa: BLE001
        return 8.0


async def _bridge(source, ping, interval):
    if ping is not None and not interval:
        interval = _default_interval()
    loop = asyncio.get_running_loop()
    box: asyncio.Queue = asyncio.Queue()
    stop = threading.Event()

    def put(item) -> bool:
        try:
            loop.call_soon_threadsafe(box.put_nowait, item)
            return True
        except RuntimeError:         # 事件循环已关闭（进程退出）
            stop.set()
            return False

    def produce():
        iterator = None
        try:
            iterator = iter(source)
            for item in iterator:
                if stop.is_set() or not put(item):
                    _bump("streams_aborted")
                    break            # 消费端已经走了：不再读上游
        except BaseException as exc:  # noqa: BLE001 - 异常交给消费端抛出
            if not stop.is_set():
                put(exc)
        finally:
            # 提前停止时 close() 让平台生成器的 finally 立即执行（关上游、放账号、记账）；
            # 正常结束时 close() 是空操作。
            if iterator is not None and iterator is not source:
                _close_quietly(iterator)
            _close_quietly(source)
            put(_DONE)

    thread = threading.Thread(target=contextvars.copy_context().run, args=(produce,),
                              daemon=True, name="sse-async-bridge")
    _bump("streams_started")
    _bump("streams_active")
    try:
        thread.start()
        while True:
            if ping is None:
                item = await box.get()
            else:
                try:
                    item = await asyncio.wait_for(box.get(),
                                                  timeout=max(0.05, float(interval)))
                except asyncio.TimeoutError:
                    yield ping
                    continue
            if item is _DONE:
                return
            if isinstance(item, BaseException):
                raise item
            yield item
    finally:
        stop.set()
        _bump("streams_active", -1)


def stats() -> dict:
    """观测用快照（接到 `/api/stats/memory` 需要协调者在 server/router 里加一行）。"""
    with _STATS_LOCK:
        out = dict(_STATS)
    with _SHARED_LOCK:
        out["shared_transports"] = sorted("%s%s" % (k[0], "@proxy" if k[1] else "")
                                          for k in _SHARED)
    out["upstream_threads"] = (_EXECUTOR._max_workers if _EXECUTOR is not None  # noqa: SLF001
                               else _env_int("ADMIN_UPSTREAM_THREADS", 128, 0, 1024))
    out["connect_timeout"] = connect_timeout()
    out["shared_enabled"] = _env_on("ADMIN_UPSTREAM_SHARED_CLIENT", True)
    out["stream_offload"] = stream_offload_enabled()
    return out
