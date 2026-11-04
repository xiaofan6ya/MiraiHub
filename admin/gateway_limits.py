"""Per-key sliding window and renewable Redis concurrency leases."""
from collections import deque
import asyncio
import anyio
import json
import logging
import math
import random
import uuid
import threading
import time

from admin.config import settings
from admin.security import GatewayRejected, check_key_current

_lock = threading.Lock()
_windows = {}
_active = {}
_waiters = {}
_total_waiters = 0
_signals = {}
#: 进程内租约登记表：token -> (Admission, 过期时刻 monotonic)。
#:
#: 为什么要有它：`_active[key]` 只是「当前有几路在途」的计数，**谁占着**
#: 没有登记。于是只要有一条收尾路径没调到 `release()`（任务被取消、
#: 生成器没关、中间件抛错），这个名额就永远回不来 —— 表现为「并发被
#: 打满、排队不回收」，只能重启进程。有了登记表就能按时间兜底回收。
_leases: dict[str, tuple] = {}
_lease_sweep_at = 0.0
#: 租约最长存活：网关请求超时 + 120 秒冗余。超过它一定不是「还在正常跑」。
_LEASE_GRACE_S = 120.0
_redis = None
_redis_initialized = False
_redis_lock = threading.Lock()
_logger = logging.getLogger(__name__)
_ACQUIRE = """
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2])/1000000
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now-60)
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now)
if tonumber(ARGV[1]) > 0 and redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[1]) then
 local first = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
 return {math.max(1, math.ceil(60-now+tonumber(first[2]))), 1, redis.call('ZCARD', KEYS[1])}
end
if tonumber(ARGV[2]) > 0 and redis.call('ZCARD', KEYS[2]) >= tonumber(ARGV[2]) then
 return {1, 2, redis.call('ZCARD', KEYS[2])}
end
if tonumber(ARGV[1]) > 0 then
 redis.call('ZADD', KEYS[1], now, ARGV[3]); redis.call('EXPIRE', KEYS[1], 61)
end
redis.call('ZADD', KEYS[2], now+120, ARGV[3]); redis.call('EXPIRE', KEYS[2], 121)
return {0, 0, 0}
"""
_RENEW = """
if not redis.call('ZSCORE', KEYS[1], ARGV[1]) then return 0 end
local t = redis.call('TIME')
redis.call('ZADD', KEYS[1], tonumber(t[1])+tonumber(t[2])/1000000+120, ARGV[1])
redis.call('EXPIRE', KEYS[1], 121)
return 1
"""


def _redis_client():
    global _redis, _redis_initialized
    with _redis_lock:
        if not _redis_initialized:
            backend = settings.GATEWAY_LIMIT_BACKEND
            if backend not in ('auto', 'redis', 'memory'):
                raise ValueError('invalid gateway limit backend')
            if backend != 'memory':
                try:
                    import redis
                    client = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=1, socket_timeout=1)
                    client.ping()
                    _redis = client
                except Exception:
                    if backend == 'redis':
                        raise
                    _logger.warning('Gateway admission falling back to process-local memory')
            _redis_initialized = True
        return _redis


def _keys(key):
    return (f'wb:gateway:{{{key}}}:rate', f'wb:gateway:{{{key}}}:active')


class Admission:
    def __init__(self, key, retry_after=0):
        self.key = key
        self.retry_after = retry_after
        self.released = False
        self.client = None
        self.token = uuid.uuid4().hex
        #: 拿到名额的时刻（monotonic）。回收线程据此判断「占了多久没还」。
        self.started = time.monotonic()


def lease_ttl_seconds() -> float:
    """一个名额最多允许占用多久（秒）。"""
    try:
        base = float(getattr(settings, "GATEWAY_REQUEST_TIMEOUT", 300) or 300)
    except (TypeError, ValueError):
        base = 300.0
    return max(60.0, base + _LEASE_GRACE_S)


def _lease_of(admission) -> float:
    """登记一个进程内租约，返回它的过期时刻。"""
    deadline = time.monotonic() + lease_ttl_seconds()
    with _lock:
        _leases[admission.token] = (admission, deadline)
    return deadline


def reclaim_expired(now: float | None = None) -> int:
    """回收「超期还没归还」的名额，返回回收条数。

    ## 这不是「猜」——阈值有硬依据

    网关在 `GATEWAY_REQUEST_TIMEOUT`（默认 300s）时会 `cancel()` 掉整条
    请求任务，正常路径上 `finally` 一定跑过 `release()`。所以一个名额
    被占用超过 `timeout + 120s`，只可能是收尾路径丢了（取消竞态、
    生成器没关、中间件异常），不可能是「请求还在正常跑」。

    以前没有任何兜底：丢一次就永久少一路并发，丢满 `MAX_CONCURRENT` 次
    整个网关就再也接不进新请求 —— 只能重启。用户看到的正是这个。
    """
    now = now if now is not None else time.monotonic()
    with _lock:
        expired = [(adm, dl) for adm, dl in _leases.values() if dl <= now]
    released = 0
    for admission, deadline in expired:
        try:
            _logger.warning(
                "回收超期未归还的并发名额 key=%s token=%s 占用 %.1fs（阈值 %.0fs）",
                admission.key, admission.token[:8], now - (deadline - lease_ttl_seconds()),
                lease_ttl_seconds())
            release(admission)
            released += 1
        except Exception:  # noqa: BLE001
            _logger.debug("回收名额失败", exc_info=True)
    return released


def _maybe_sweep(now: float) -> None:
    """限频清扫：最多 5 秒一次，避免每次准入都遍历登记表。"""
    global _lease_sweep_at
    with _lock:
        if now - _lease_sweep_at < 5.0:
            return
        _lease_sweep_at = now
    reclaim_expired(now)


def pressure() -> dict:
    """当前的并发压力快照（排障用）。"""
    now = time.monotonic()
    with _lock:
        active = dict(_active)
        leases = [{"key": adm.key, "held_s": round(now - adm.started, 1),
                   "ttl_s": round(dl - now, 1)}
                  for adm, dl in _leases.values()]
        waiters = dict(_waiters)
        total_waiters = _total_waiters
    rate, concurrent = _limits()
    return {
        "global_limit": concurrent,
        "rate_limit_per_minute": rate,
        "active_by_key": active,
        "active_total": sum(active.values()),
        "held_leases": sorted(leases, key=lambda x: -x["held_s"])[:20],
        "waiters_by_key": waiters,
        "waiters_total": total_waiters,
        "lease_ttl_s": lease_ttl_seconds(),
        "storage": "redis" if _redis is not None else "memory",
    }


class Denied(tuple):
    """Keep the (None, retry_after) interface while retaining the actual bound."""
    def __new__(cls, retry_after, reason, limit, current):
        result = super().__new__(cls, (None, retry_after))
        result.reason = reason
        result.limit = limit
        result.current = current
        return result


def _limits():
    return max(0, int(getattr(settings, "GATEWAY_RATE_PER_MINUTE", 0))), max(0, int(getattr(settings, "GATEWAY_MAX_CONCURRENT", 0)))


def allow(key, rate=None, concurrent=None):
    """申请准入。concurrent 支持 per-key 覆盖：None=全局默认，0=不限，N=上限 N。"""
    g_rate, g_conc = _limits()
    if rate is None:
        rate = g_rate
    if concurrent is None:
        concurrent = g_conc
    if not key or (not rate and not concurrent):
        return Admission(None)
    client = _redis_client()
    if client is not None:
        lease = Admission(key)
        lease.client = client
        wait, reason, count = map(int, client.eval(_ACQUIRE, 2, *_keys(key), rate, concurrent, lease.token))
        if wait:
            return Denied(wait, 'requests_per_minute' if reason == 1 else 'concurrent_requests',
                          rate if reason == 1 else concurrent, count)
        return lease
    now = time.monotonic()
    # 顺手回收超期名额：丢一个名额 = 永久少一路并发，必须兜底。
    _maybe_sweep(now)
    with _lock:
        for stale in list(_windows):
            values = _windows[stale]
            while values and now - values[0] >= 60:
                values.popleft()
            if not values and not _active.get(stale):
                _windows.pop(stale)
        q = _windows.setdefault(key, deque())
        if rate:
            while q and now - q[0] >= 60:
                q.popleft()
            if len(q) >= rate:
                return Denied(max(1, math.ceil(60 - (now - q[0]))), 'requests_per_minute', rate, len(q))
            q.append(now)
        current = _active.get(key, 0)
        if concurrent and current >= concurrent:
            if rate and q and q[-1] == now:
                q.pop()
            return Denied(1, 'concurrent_requests', concurrent, current)
        _active[key] = current + 1
    lease = Admission(key)
    _lease_of(lease)
    return lease


def _notify_release(key):
    with _lock:
        signals = list(_signals.get(key, ()))
    for loop, event in signals:
        try:
            loop.call_soon_threadsafe(event.set)
        except RuntimeError:
            pass


def release(admission):
    if not admission or not admission.key:
        return
    if admission.client is not None:
        admission.client.zrem(_keys(admission.key)[1], admission.token)
        _notify_release(admission.key)
        return
    with _lock:
        if admission.released:
            return
        admission.released = True
        _leases.pop(admission.token, None)
        current = _active.get(admission.key, 0)
        if current <= 1:
            _active.pop(admission.key, None)
        else:
            _active[admission.key] = current - 1
        if admission.key in _windows and not _windows[admission.key]:
            _windows.pop(admission.key, None)
    _notify_release(admission.key)


def snapshot():
    with _lock:
        return {"active": dict(_active), "window_sizes": {k: len(v) for k, v in _windows.items()},
                "scope": "process_local"}


def _valid_key_id(token):
    from admin.db import SessionLocal
    from admin.security import get_key_row
    with SessionLocal() as db:
        row = get_key_row(db, token)
        return str(row.id) if row and row.status == "active" else None


def _key_admission_params(token):
    """解析 token → (key_id, rate, concurrent)。

    concurrent 语义：
      None = 跟随全局 ADMIN_GATEWAY_MAX_CONCURRENT
      0    = 该 key 不限并发
      N    = 最多 N 路在途
    """
    rate, g_conc = _limits()
    if not token:
        return None, rate, g_conc
    from admin.db import SessionLocal
    from admin.security import get_key_row
    with SessionLocal() as db:
        row = get_key_row(db, token)
        if not row or row.status != "active":
            return None, rate, g_conc
        mc = getattr(row, "max_concurrent", None)
        rpm = getattr(row, "rate_per_minute", None)
        if rpm is not None:
            rate = max(0, int(rpm))
        if mc is None:
            conc = g_conc
        else:
            conc = max(0, int(mc))
        return str(row.id), rate, conc


async def _finish_thread(fn, *args):
    task = asyncio.create_task(asyncio.to_thread(fn, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        with anyio.CancelScope(shield=True):
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
        raise


class GatewayAdmissionMiddleware:
    def __init__(self, app, wait_timeout=None, max_waiters_per_key=8, max_waiters=128,
                 request_timeout=None, dynamic_settings=False):
        self.app = app
        self._wait_timeout_dynamic = bool(dynamic_settings)
        self._wait_timeout_default = max(0, float(wait_timeout if wait_timeout is not None else 0))
        self.max_waiters_per_key = max_waiters_per_key
        self.max_waiters = max_waiters
        self._request_timeout_dynamic = bool(dynamic_settings)
        self._request_timeout_default = max(0, float(request_timeout if request_timeout is not None else 160))

    # 等待/总时限实时读 settings（后台「运行配置」可动态改，无需重启）；
    # 读不到或非法时回落注册时的默认值。
    @property
    def wait_timeout(self) -> float:
        if not self._wait_timeout_dynamic:
            return self._wait_timeout_default
        try:
            return max(0.0, float(getattr(settings, "GATEWAY_WAIT_TIMEOUT",
                                          self._wait_timeout_default)))
        except (TypeError, ValueError):
            return self._wait_timeout_default

    @property
    def request_timeout(self) -> float:
        if not self._request_timeout_dynamic:
            return self._request_timeout_default
        try:
            return max(0.0, float(getattr(settings, "GATEWAY_REQUEST_TIMEOUT",
                                          self._request_timeout_default)))
        except (TypeError, ValueError):
            return self._request_timeout_default

    async def _acquire_once(self, key, rate, concurrent):
        pending = asyncio.create_task(asyncio.to_thread(allow, key, rate, concurrent))
        try:
            return await asyncio.shield(pending)
        except asyncio.CancelledError:
            with anyio.CancelScope(shield=True):
                while not pending.done():
                    try:
                        await asyncio.shield(pending)
                    except asyncio.CancelledError:
                        continue
                result = pending.result()
                if not isinstance(result, tuple):
                    await _finish_thread(release, result)
            raise

    async def _admit(self, key, rate, concurrent, start_monitor=None):
        global _total_waiters
        from admin import request_metrics
        result = await self._acquire_once(key, rate, concurrent)
        if (not isinstance(result, tuple) or not self.wait_timeout
                or getattr(result, 'reason', '') == 'requests_per_minute'):
            return result
        with _lock:
            if (_waiters.get(key, 0) >= self.max_waiters_per_key or
                    _total_waiters >= self.max_waiters):
                return result
            _waiters[key] = _waiters.get(key, 0) + 1
            _total_waiters += 1
        started = time.monotonic()
        deadline = started + self.wait_timeout
        signal = (asyncio.get_running_loop(), asyncio.Event())
        with _lock:
            _signals.setdefault(key, []).append(signal)
        try:
            if start_monitor is not None:
                start_monitor()
            while isinstance(result, tuple):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return result
                reason = getattr(result, 'reason', 'concurrent_requests')
                if reason == 'requests_per_minute':
                    return result
                request_metrics.set_phase('gateway_wait_' + reason)
                delay = result[1] if reason == 'requests_per_minute' else random.uniform(.2, .4)
                wake = time.monotonic() + min(remaining, delay)
                while True:
                    try:
                        await asyncio.wait_for(signal[1].wait(), min(.5, max(0, wake - time.monotonic())))
                    except asyncio.TimeoutError:
                        pass
                    notified = signal[1].is_set()
                    signal[1].clear()
                    row = await _finish_thread(check_key_current, key)
                    if row is not None:
                        global_rate, global_concurrent = _limits()
                        next_rate = row.rate_per_minute if row.rate_per_minute is not None else global_rate
                        next_concurrent = row.max_concurrent if row.max_concurrent is not None else global_concurrent
                        if (rate, concurrent) != (next_rate, next_concurrent):
                            rate, concurrent = max(0, next_rate), max(0, next_concurrent)
                            break
                    if notified or time.monotonic() >= wake:
                        break
                result = await self._acquire_once(key, rate, concurrent)
            request_metrics.set_phase('gateway_admission')
            return result
        finally:
            request_metrics.add_stage('gateway_queue_wait', (time.monotonic() - started) * 1000)
            with _lock:
                signals = _signals.get(key, [])
                if signal in signals:
                    signals.remove(signal)
                if not signals:
                    _signals.pop(key, None)
                _total_waiters = max(0, _total_waiters - 1)
                count = _waiters.get(key, 1) - 1
                if count:
                    _waiters[key] = count
                else:
                    _waiters.pop(key, None)

    async def __call__(self, scope, receive, send):
        if (scope.get('type') != 'http' or not scope.get('path', '').startswith('/v1/')
                or scope.get('method', 'GET').upper() != 'POST' or not self.request_timeout):
            return await self._dispatch(scope, receive, send)

        state = {'timed_out': False, 'started': False, 'complete': False, 'sse': False}

        async def tracked_send(message):
            if state['timed_out']:
                return
            is_final_body = (message['type'] == 'http.response.body'
                             and not message.get('more_body', False))
            if message['type'] == 'http.response.start':
                state['started'] = True
                state['sse'] = any(name.lower() == b'content-type' and
                                   b'text/event-stream' in value.lower()
                                   for name, value in message.get('headers', []))
            await send(message)
            if is_final_body:
                state['complete'] = True

        request = asyncio.create_task(self._dispatch(scope, receive, tracked_send))
        try:
            done, _ = await asyncio.wait({request}, timeout=self.request_timeout)
        except asyncio.CancelledError:
            request.cancel()
            await asyncio.wait({request}, timeout=1.0)
            if request.done() and not request.cancelled():
                try:
                    request.exception()
                except BaseException:
                    pass
            raise
        if request in done:
            return request.result()

        state['timed_out'] = True
        from admin import request_metrics
        request_metrics.mark_request_error('request_timeout')
        request_metrics.mark_gateway_result(504)
        record = request_metrics.current.get()
        if record is not None:
            record['phase'] = 'request_timeout'
            record['outcome'] = 'timeout'
            if state['sse'] and not state['complete']:
                record['stream_error'] = True
        request.cancel()
        done, _ = await asyncio.wait({request}, timeout=1.0)
        if request in done:
            if not request.cancelled():
                try:
                    request.exception()
                except BaseException:
                    pass
        else:
            request.add_done_callback(self._consume_task)

        if state['complete']:
            return
        try:
            if not state['started']:
                from starlette.responses import JSONResponse
                seconds = f'{self.request_timeout:g}'
                response = JSONResponse(
                    {'error': {'message': f'Request exceeded the {seconds}-second gateway limit',
                               'type': 'timeout', 'code': 'request_timeout'}},
                    status_code=504, headers={'Retry-After': '1'})
                await response(scope, receive, send)
            elif state['sse']:
                body = ('event: error\ndata: ' + json.dumps(
                    {'error': {'message': f'Request exceeded the {self.request_timeout:g}-second gateway limit',
                               'type': 'timeout', 'code': 'request_timeout'}}) + '\n\n').encode()
                await send({'type': 'http.response.body', 'body': body, 'more_body': False})
            else:
                await send({'type': 'http.response.body', 'body': b'', 'more_body': False})
        except (asyncio.CancelledError, OSError):
            pass

    @staticmethod
    def _consume_task(task):
        if not task.cancelled():
            try:
                task.exception()
            except BaseException:
                pass

    async def _dispatch(self, scope, receive, send):
        from starlette.responses import JSONResponse
        # Model listing / health probes do not start inference and must not
        # consume the same quota or occupy a long-running generation slot.
        if (scope['type'] != 'http' or not scope.get('path', '').startswith('/v1/')
                or scope.get('method', 'POST').upper() in ('GET', 'HEAD', 'OPTIONS')):
            return await self.app(scope, receive, send)
        headers = dict(scope.get('headers', []))
        token = headers.get(b'x-api-key', b'').decode('latin1')
        if not token:
            auth = headers.get(b'authorization', b'').decode('latin1')
            token = auth[7:].strip() if auth.startswith('Bearer ') else ''
        # Only authenticated IDs enter the bounded state, never raw client tokens.
        # per-key 并发：解析后的 concurrent 可能与全局不同，须一并传给 allow。
        key, rate, concurrent = await _finish_thread(_key_admission_params, token) if token \
            else (None, *_limits())
        from admin import request_metrics
        record = request_metrics.current.get()
        if key is not None:
            try:
                scope['_gateway_key_id'] = int(key)
            except (TypeError, ValueError):
                pass
            identities = scope.get('_client_identities', [])
            if identities:
                from admin.client_identity import remember_request
                from admin.client_ip import request_ip
                from starlette.requests import Request
                await _finish_thread(remember_request, identities, request_ip(Request(scope)), int(key))
        if record is not None and key is not None:
            try:
                record['key_id'] = int(key)
            except (TypeError, ValueError):
                pass
        if key is None or not (rate or concurrent):
            return await self.app(scope, receive, send)
        # One reader owns the ASGI input while waiting. A bounded queue keeps
        # uploads streaming and replays every frame unchanged to the endpoint.
        # Start it only after admission actually queues, so unlimited requests
        # incur neither a queue nor an input-buffering task.
        frames = asyncio.Queue(maxsize=16)
        disconnected = asyncio.Event()
        reader = None
        admission_task = None
        async def pump():
            while True:
                message = await receive()
                if message['type'] == 'http.disconnect':
                    disconnected.set()
                    if admission_task is not None and not admission_task.done():
                        admission_task.cancel()
                    await frames.put(message)
                    return
                await frames.put(message)
        def start_monitor():
            nonlocal reader
            reader = asyncio.create_task(pump())
        async def replay():
            if disconnected.is_set() and frames.empty():
                return {'type': 'http.disconnect'}
            return await frames.get()
        async def stop_monitor():
            if reader is not None:
                reader.cancel()
                try:
                    await reader
                except asyncio.CancelledError:
                    pass
        try:
            admission_task = asyncio.create_task(self._admit(key, rate, concurrent, start_monitor))
            result = await admission_task
        except asyncio.CancelledError:
            await stop_monitor()
            if disconnected.is_set():
                request_metrics.mark_request_error('client_disconnected')
                return
            raise
        except GatewayRejected as exc:
            await stop_monitor()
            request_metrics.mark_gateway_error(exc.status_code, exc.detail)
            return await JSONResponse(exc.payload(), status_code=exc.status_code)(scope, receive, send)
        except Exception:
            await stop_monitor()
            return await JSONResponse({'error': {'message': '流量控制服务暂不可用', 'type': 'gateway_unavailable'}},
                                      status_code=503, headers={'Retry-After': '5'})(scope, receive, send)
        if isinstance(result, tuple):
            await stop_monitor()
            _, retry = result
            reason = getattr(result, 'reason', 'unknown')
            limit = getattr(result, 'limit', None)
            count = getattr(result, 'current', None)
            if reason == 'concurrent_requests':
                message = f'API Key 并发已达上限（当前 {count} / 上限 {limit}），请等待在途请求结束后重试'
            elif reason == 'requests_per_minute':
                message = f'API Key 每分钟请求数已达上限（{limit} 次/分钟），请在 {retry} 秒后重试；提高并发上限不会解除此限制'
            else:
                message = 'API Key 请求过于频繁，请稍后重试'
            request_metrics.mark_request_error(reason)
            return await JSONResponse({'error': {'message': message, 'type': 'rate_limit_exceeded',
                                                'code': reason, 'limit': limit, 'current': count,
                                                'retry_after': retry}},
                                      status_code=429, headers={'Retry-After': str(retry)})(scope, receive, send)
        owner = asyncio.current_task()
        async def renew():
            while True:
                await asyncio.sleep(30)
                try:
                    if await _finish_thread(result.client.eval, _RENEW, 1, _keys(result.key)[1], result.token):
                        continue
                except Exception:
                    pass
                owner.cancel()
                return
        heartbeat = asyncio.create_task(renew()) if result.client is not None else None
        try:
            await self.app(scope, replay if reader is not None else receive, send)
        finally:
            # ---- 名额必须归还，任何异常都不能拦住 ----
            #
            # 原来的写法是：
            #     await stop_monitor()      # ← 这一句在被取消时会抛 CancelledError
            #     await _finish_thread(release, result)   # ← 于是永远执行不到
            # 而 `except Exception` 又捕不到 CancelledError（它是 BaseException）。
            # 结果：请求一旦在客户端断开/网关超时的时候被取消，这个并发名额
            # 就再也回不来 —— 丢满上限后整个网关排队不动，只能重启。
            #
            # 现在拆成三步，且每一步都不允许影响下一步：
            #   1) 停监听（失败忽略）
            #   2) 心跳（失败忽略）
            #   3) 归还名额：内存路径**同步**调用（不会被取消打断），
            #      Redis 路径用 shield 包住
            try:
                await stop_monitor()
            except BaseException:  # noqa: BLE001 - 取消/异常都不影响归还
                pass
            if heartbeat:
                heartbeat.cancel()
                try:
                    await heartbeat
                except BaseException:  # noqa: BLE001
                    pass
            try:
                if result.client is None:
                    release(result)
                else:
                    await asyncio.shield(_finish_thread(release, result))
            except BaseException:  # noqa: BLE001
                _logger.warning('Gateway lease release failed; Redis lease will expire')