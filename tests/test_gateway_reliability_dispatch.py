# PERSISTENT REGRESSION TESTS — keep this file in the repo.
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""网关可靠性回归：dispatch 重试语义 + 请求体预读契约（零网络、零真实上游）。

A. 平台内部异常（raise）说明这次请求可能已有副作用，候选循环不再换平台重放；
   平台明确返回 404/502/503 时保留原重试契约。

B. 预读上限**边读边判**：越线立刻停读并 413（dispatch 与 marvis 的
   _reject_oversized 都是）；已声明 Content-Length 且超上限时保持「只看头、
   不预读」的旧语义。

C. 预读**失败**不转发：断开 400 client_disconnected，其它读失败 400
   body_read_failed —— 半截 body 绝不能当完整请求交给下游。

D. 预读成功（含空 body）必须回放；_replay_receive 回放完必须委托 tail，
   真实断连才能透传给 StreamingResponse。

运行：
  python -m pytest tests/test_gateway_reliability_dispatch.py -q
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

_TMP_DB = Path(tempfile.gettempdir()) / ("dispatch_reliability_%d.db" % os.getpid())
os.environ.setdefault("ADMIN_DATABASE_URL", "sqlite:///" + str(_TMP_DB))

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(ROOT))

from starlette.requests import Request  # noqa: E402
from starlette.responses import JSONResponse, StreamingResponse  # noqa: E402

from admin.platform import dispatch, registry  # noqa: E402
from admin.platform.marvis import gateway as marvis_gateway  # noqa: E402


def tearDownModule():  # 清掉本轮临时 sqlite
    # Windows 上 sqlite 文件被引擎持有时删不掉：先释放连接池
    with contextlib.suppress(Exception):
        from admin import db as _admin_db

        engine = getattr(_admin_db, "engine", None)
        if engine is not None:
            engine.dispose()
    with contextlib.suppress(OSError):
        _TMP_DB.unlink()


# ---------------------------------------------------------------------------
# 公共替身
# ---------------------------------------------------------------------------
class _FakeSpec:
    """够 dispatch / registry 用的 PlatformSpec 替身。"""

    enabled = True
    realm = ""
    realms = ()

    def __init__(self, pid, realm="", storage=None):
        self.id = pid
        self.realm = realm
        # 一区一 spec 的平台（trae-cn / trae-intl）共享同一个 storage
        self.storage_platform = storage or pid

    def storage(self):
        return self.storage_platform

    def default_realm(self):
        return self.realm


class _Ctx:
    """dispatch._db() 的替身：只提供上下文管理，不碰真实库。"""

    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False


def _key():
    return SimpleNamespace(id=1, name="test-key", label="", platform="",
                           aggregate=True, group_id=0, account_id=None,
                           account_ids="")


def _scope(path="/v1/chat/completions", method="POST", headers=None):
    return {
        "type": "http", "http_version": "1.1", "method": method,
        "path": path, "raw_path": path.encode(), "query_string": b"",
        "scheme": "http", "server": ("testserver", 80),
        "client": ("127.0.0.1", 12345),
        "headers": list(headers or []),
    }


def _receive_from(chunks):
    """合成 ASGI receive：按块返回 http.request，最后一次 more_body=False。

    返回 (receive, calls)：calls 记录 receive 被调用的次数，用来证明
    「超限之后没有再继续把 body 读完」。
    """
    calls = []
    state = {"i": 0}

    async def receive():
        calls.append(1)
        i = state["i"]
        state["i"] = i + 1
        if i >= len(chunks):
            await asyncio.sleep(3600)          # 与真实行为一致：没有更多数据
            return {"type": "http.disconnect"}
        more = i < len(chunks) - 1
        return {"type": "http.request", "body": chunks[i], "more_body": more}

    return receive, calls


def _collect():
    messages = []

    async def send(message):
        messages.append(message)

    return messages, send


def _status(messages):
    for message in messages:
        if message.get("type") == "http.response.start":
            return int(message["status"])
    return None


def _payload(messages):
    raw = b"".join(m.get("body", b"") for m in messages
                   if m.get("type") == "http.response.body")
    try:
        return json.loads(raw)
    except ValueError:
        return {}


def _noop_app():
    async def app(scope, receive, send):
        response = JSONResponse({"app": True})
        await response(scope, receive, send)
    return app


@contextlib.contextmanager
def _fake_traffic_modules():
    traffic = SimpleNamespace(annotate=lambda *a, **k: None,
                              mark_stage=lambda *a, **k: None,
                              start=lambda *a, **k: "rid-test")
    metrics = SimpleNamespace(current=SimpleNamespace(get=lambda: None))
    with mock.patch.dict(sys.modules, {"admin.traffic_stats": traffic,
                                       "admin.request_metrics": metrics}):
        yield


# ---------------------------------------------------------------------------
# A. dispatch 异常重试
# ---------------------------------------------------------------------------
class DispatchExceptionRetryTests(unittest.TestCase):
    """平台内部异常后，候选循环必须立刻返回，不再换平台重放。"""

    def _run(self, first, second):
        calls = []
        modules = {
            "fake-a": SimpleNamespace(dispatch=first(calls)),
            "fake-b": SimpleNamespace(dispatch=second(calls)),
        }
        body = json.dumps({"model": "reliability-model"}).encode()
        headers = [(b"content-type", b"application/json"),
                   (b"content-length", str(len(body)).encode())]
        scope = _scope(headers=headers)
        receive, _ = _receive_from([body])
        messages, send = _collect()

        patches = [
            mock.patch.object(dispatch, "resolve", lambda request: ("fake-a", "")),
            mock.patch.object(dispatch, "_key_of", lambda request: _key()),
            mock.patch.object(dispatch, "_disabled_platforms", lambda: set()),
            mock.patch.object(dispatch, "_db", lambda: _Ctx()),
            mock.patch.object(dispatch, "_spec_with_gateway",
                              lambda pid, realm: _FakeSpec(pid)),
            mock.patch.object(dispatch, "_module_of",
                              lambda spec: modules.get(getattr(spec, "id", ""))),
            mock.patch.object(dispatch, "_model_id_for",
                              lambda db, storage, model: model),
            # 候选只有一个入口：_candidates_cached() 内部走 _candidates_detail()。
            # 这里替换粒度保持「本请求的候选集合」，重试语义的断言不变。
            mock.patch.object(dispatch, "_candidates_detail",
                              lambda db, model, key: ([("fake-b", "", model)], [])),
            mock.patch.object(dispatch, "_reroute_target", lambda *a, **k: None),
        ]
        with contextlib.ExitStack() as stack:
            for patch in patches:
                patch.start()
                stack.callback(patch.stop)
            with _fake_traffic_modules():
                middleware = dispatch.PlatformDispatchMiddleware(_noop_app())
                asyncio.run(middleware(scope, receive, send))
        return calls, messages

    def test_internal_exception_does_not_try_next_candidate(self):
        side_effects = []

        def first(calls):
            async def _dispatch(request, path):
                calls.append("fake-a")
                # 模拟「已经做了一半的副作用」（例如已向上游发起请求）
                side_effects.append("upstream-touched")
                raise RuntimeError("platform exploded")
            return _dispatch

        def second(calls):
            async def _dispatch(request, path):
                calls.append("fake-b")
                return JSONResponse({"ok": True})
            return _dispatch

        calls, messages = self._run(first, second)

        self.assertEqual(calls, ["fake-a"], "第一个候选抛异常后不许再调用第二个候选")
        self.assertEqual(len(side_effects), 1)
        self.assertEqual(_status(messages), 502)
        self.assertEqual(_payload(messages)["error"]["code"], "platform_error")

    def test_explicit_503_still_retries_next_candidate(self):
        """平台**明确**返回 503 时保留原有重试契约。"""

        def first(calls):
            async def _dispatch(request, path):
                calls.append("fake-a")
                return JSONResponse({"error": {"code": "platform_unavailable"}},
                                    status_code=503)
            return _dispatch

        def second(calls):
            async def _dispatch(request, path):
                calls.append("fake-b")
                return JSONResponse({"ok": True})
            return _dispatch

        calls, messages = self._run(first, second)

        self.assertEqual(calls, ["fake-a", "fake-b"])
        self.assertEqual(_status(messages), 200)


# ---------------------------------------------------------------------------
# B1. dispatch._read_body 增量上限
# ---------------------------------------------------------------------------
class DispatchReadBodyLimitTests(unittest.TestCase):
    def test_under_limit_reads_everything(self):
        chunks = [b"aaaa", b"bbbb", b"cccc"]
        receive, calls = _receive_from(chunks)
        body = asyncio.run(dispatch._read_body(receive, 1024))
        self.assertEqual(body, b"".join(chunks))
        self.assertEqual(len(calls), 3)

    def test_over_limit_stops_reading_immediately(self):
        chunks = [b"x" * 10 for _ in range(5)]
        receive, calls = _receive_from(chunks)
        with self.assertRaises(dispatch.BodyTooLarge) as ctx:
            asyncio.run(dispatch._read_body(receive, 15))
        # 2 块 = 20 字节 > 15：第二块之后就必须停，不能把 5 块读完
        self.assertEqual(len(calls), 2, "超限后仍在继续读 body")
        self.assertGreater(ctx.exception.total, 15)

    def test_middleware_returns_413_for_oversized_chunked_body(self):
        chunks = [b"x" * 20 for _ in range(4)]      # 80 字节，无 Content-Length
        scope = _scope(headers=[(b"content-type", b"application/json")])
        receive, calls = _receive_from(chunks)
        messages, send = _collect()
        with mock.patch.object(dispatch, "_BODY_HARD_CAP", 32):
            middleware = dispatch.PlatformDispatchMiddleware(_noop_app())
            asyncio.run(middleware(scope, receive, send))
        self.assertEqual(_status(messages), 413)
        self.assertEqual(_payload(messages)["error"]["code"], "body_too_large")
        self.assertLess(len(calls), len(chunks), "超限后仍把 body 读完了")

    def test_declared_content_length_path_unchanged(self):
        """声明长度超上限：沿用旧语义（不预读、不 413，交给下游）。"""
        scope = _scope(headers=[(b"content-type", b"application/json"),
                                (b"content-length", b"999999")])
        with mock.patch.object(dispatch, "_BODY_HARD_CAP", 32):
            self.assertFalse(dispatch._should_peek(Request(scope)))

        # 声明长度在范围内：照旧预读
        ok_scope = _scope(headers=[(b"content-type", b"application/json"),
                                   (b"content-length", b"10")])
        self.assertTrue(dispatch._should_peek(Request(ok_scope)))

        # 未知长度（chunked）：照旧预读
        chunked = _scope(headers=[(b"content-type", b"application/json")])
        self.assertTrue(dispatch._should_peek(Request(chunked)))


# ---------------------------------------------------------------------------
# B2. marvis 网关 _reject_oversized 增量上限
# ---------------------------------------------------------------------------
MIB = 1024 * 1024


@contextlib.contextmanager
def _limit(value):
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop(marvis_gateway.MAX_BODY_ENV, None)
        os.environ[marvis_gateway.MAX_BODY_ENV] = str(value)
        yield


def _marvis_request(chunks, *, declared="auto"):
    """合成 ASGI 请求；declared=auto 时按总字节数写 Content-Length。"""
    headers = [(b"content-type", b"application/json")]
    if declared == "auto":
        declared = sum(len(c) for c in chunks)
    if declared is not None:
        headers.append((b"content-length", str(declared).encode()))
    scope = _scope(headers=headers)
    receive, calls = _receive_from(list(chunks))
    return Request(scope, receive=receive), calls


class MarvisIncrementalBodyLimitTests(unittest.TestCase):
    def test_unknown_length_under_limit_is_cached_for_downstream(self):
        payload = b'{"model": "main-auto", "messages": [{"role": "user"}]}'
        chunks = [payload[:10], payload[10:30], payload[30:]]
        request, calls = _marvis_request(chunks, declared=None)
        with _limit(MIB):
            self.assertIsNone(asyncio.run(
                marvis_gateway._reject_oversized(request)))
        self.assertEqual(len(calls), len(chunks))
        # 回写缓存：下游 _body_of / json() 必须还能读到完整 body
        self.assertEqual(asyncio.run(request.body()), payload)
        self.assertEqual(asyncio.run(marvis_gateway._body_of(request))["model"],
                         "main-auto")

    def test_unknown_length_over_limit_stops_reading(self):
        chunks = [b"x" * 300 for _ in range(5)]       # 1500 字节
        request, calls = _marvis_request(chunks, declared=None)
        with _limit(1024):
            response = asyncio.run(marvis_gateway._reject_oversized(request))
        self.assertEqual(response.status_code, 413)
        self.assertEqual(json.loads(response.body)["error"]["code"],
                         "body_too_large")
        self.assertLess(len(calls), len(chunks), "超限后仍把 body 读完了")

    def test_declared_length_never_reads_body(self):
        request, calls = _marvis_request([b"{}"], declared=2 * MIB)
        with _limit(1024):
            response = asyncio.run(marvis_gateway._reject_oversized(request))
        self.assertEqual(response.status_code, 413)
        self.assertEqual(calls, [], "声明长度路径不该读 body")

    def test_zero_disables_without_reading(self):
        request, calls = _marvis_request([b"{}"], declared=None)
        with _limit(0):
            self.assertIsNone(asyncio.run(
                marvis_gateway._reject_oversized(request)))
        self.assertEqual(calls, [], "关闭检查时不该读 body")




# ---------------------------------------------------------------------------
# E. 空 body 回放 + declared/chunked 上限契约
# ---------------------------------------------------------------------------
class EmptyBodyReplayAndCapContractTests(unittest.TestCase):
    """预读成功（**包括 b""**）就必须回放；declared 超 cap 与 chunked 超 cap 契约不同。"""

    def _run(self, scope, receive, fake_app):
        messages, send = _collect()
        patches = [
            mock.patch.object(dispatch, "resolve",
                              lambda request: (registry.DEFAULT_PLATFORM, "")),
            mock.patch.object(dispatch, "_key_of", lambda request: _key()),
            mock.patch.object(dispatch, "_disabled_platforms", lambda: set()),
            mock.patch.object(dispatch, "_db", lambda: _Ctx()),
            mock.patch.object(dispatch, "_spec_with_gateway",
                              lambda pid, realm: None),
            mock.patch.object(dispatch, "_module_of", lambda spec: None),
        ]
        with contextlib.ExitStack() as stack:
            for patch in patches:
                patch.start()
                stack.callback(patch.stop)
            with _fake_traffic_modules():
                middleware = dispatch.PlatformDispatchMiddleware(fake_app)
                started = time.perf_counter()
                asyncio.run(middleware(scope, receive, send))
                elapsed = time.perf_counter() - started
        return messages, elapsed

    def test_empty_body_without_content_length_is_replayed(self):
        """无 Content-Length 的空 POST：预读了就必须把空 http.request 还给下游。"""
        state = {"used": False}

        async def receive():
            if not state["used"]:
                state["used"] = True
                return {"type": "http.request", "body": b"", "more_body": False}
            # 真实 ASGI 语义：body 已被消费后不会再给 http.request
            await asyncio.sleep(3600)
            return {"type": "http.disconnect"}

        seen = {}

        async def fake_app(scope, receive, send):
            seen["called"] = True
            seen["wb_body"] = scope.get("_wb_body", "MISSING")
            try:
                seen["message"] = await asyncio.wait_for(receive(), 0.5)
            except asyncio.TimeoutError:
                seen["message"] = "TIMEOUT"
            await JSONResponse({"app": True})(scope, receive, send)

        scope = _scope(headers=[(b"content-type", b"application/json")])
        messages, elapsed = self._run(scope, receive, fake_app)

        self.assertTrue(seen.get("called"), "请求没有落到下游 app")
        self.assertEqual(seen.get("message"),
                         {"type": "http.request", "body": b"", "more_body": False},
                         "空 body 没有被回放：下游等不到 http.request")
        self.assertEqual(seen.get("wb_body"), b"")
        self.assertEqual(_status(messages), 200)
        self.assertLess(elapsed, 1.0)

    def test_declared_over_cap_is_forwarded_without_pre_read(self):
        """声明长度超 cap：不预读、不 413，交给下游自己的体积检查。"""
        calls = []

        async def receive():
            calls.append(1)
            await asyncio.sleep(3600)
            return {"type": "http.disconnect"}

        seen = {}

        async def fake_app(scope, receive, send):
            seen["called"] = True
            await JSONResponse({"app": True})(scope, receive, send)

        scope = _scope(headers=[(b"content-type", b"application/json"),
                                (b"content-length", b"999999")])
        with mock.patch.object(dispatch, "_BODY_HARD_CAP", 32):
            messages, elapsed = self._run(scope, receive, fake_app)

        self.assertTrue(seen.get("called"), "declared 超 cap 的请求应交给下游")
        self.assertEqual(_status(messages), 200, "declared 超 cap 不应被 dispatch 413")
        self.assertEqual(calls, [], "declared 超 cap 时不该预读 body")
        self.assertLess(elapsed, 1.0)

# ---------------------------------------------------------------------------
# D. _replay_receive 必须把真实断连透传给下游
# ---------------------------------------------------------------------------
class ReplayReceiveDisconnectTests(unittest.TestCase):
    """回放 body 之后必须委托 tail：真实断连要被 listen_for_disconnect 看到。

    旧实现回放完 body 就 asyncio.sleep(3600) 再返回 http.disconnect，
    真实客户端的断开永远传不到 StreamingResponse 的 listen_for_disconnect：
    生成器会一路跑到自然结束，上游连接 / 在途租约 / 断开统计全被拖住。
    """

    def _run_stream(self, tail):
        events = []
        sent = []
        closed = asyncio.Event()

        async def gen():
            try:
                yield b"one"
                events.append("after-first")
                await asyncio.sleep(0.2)      # 真实断连应在这段等待里把生成器取消
                events.append("after-sleep")
                yield b"two"
            finally:
                events.append("gen-closed")
                closed.set()

        async def send(message):
            body = message.get("body")
            if message.get("type") == "http.response.start":
                events.append("send start")
            elif body == b"one":
                events.append("send one")
            elif body == b"two":
                events.append("send two")
            elif body == b"":
                events.append("send empty")
            sent.append(message)

        receive = dispatch._replay_receive(b"payload-body", tail)

        async def scenario():
            first = await receive()
            started = time.perf_counter()
            # 断连必须让整条响应在 1 秒内收尾（旧代码会挂 3600 秒）
            await asyncio.wait_for(StreamingResponse(gen())(_scope(), receive, send),
                                   timeout=1.0)
            return first, time.perf_counter() - started

        first, elapsed = asyncio.run(scenario())
        return first, events, sent, closed, elapsed

    def _slow_tail(self, calls, delay=0.05):
        async def tail():
            calls.append(1)
            await asyncio.sleep(delay)
            return {"type": "http.disconnect"}
        return tail

    def test_tail_disconnect_cancels_generator(self):
        calls = []
        first, events, sent, closed, elapsed = self._run_stream(
            self._slow_tail(calls))

        # 第一次调用仍然回放**完整 body**
        self.assertEqual(first, {"type": "http.request", "body": b"payload-body",
                                "more_body": False})
        # tail 被真正调用（旧实现从不调用）
        self.assertTrue(calls, "回放 body 后没有委托 tail，真实断连被吞掉")
        # 断连后生成器被取消 / 关闭，第二块没有被发出去
        self.assertIn("gen-closed", events, events)
        self.assertTrue(closed.is_set())
        self.assertNotIn("after-sleep", events, events)
        self.assertNotIn("send two", events, events)
        self.assertEqual([m.get("body") for m in sent if m.get("body") == b"two"],
                         [])
        # 不能在旧实现的 3600 秒等待里挂住
        self.assertLess(elapsed, 1.0)

    def test_no_tail_keeps_fallback_wait(self):
        """tail=None 时保留兜底：第二次调用不能立即返回（否则下游忙循环）。"""
        receive = dispatch._replay_receive(b"body-only", None)

        async def scenario():
            first = await receive()
            pending = asyncio.ensure_future(receive())
            await asyncio.sleep(0.02)
            done = pending.done()
            pending.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await pending
            return first, done

        first, done = asyncio.run(scenario())
        self.assertEqual(first["body"], b"body-only")
        self.assertFalse(done, "tail=None 时第二次调用立即返回，会让 listen_for_disconnect 忙循环")


# ---------------------------------------------------------------------------
# C. 预读失败（断开 / 读错误）不转发、不回放
# ---------------------------------------------------------------------------
class BodyReadFailureTests(unittest.TestCase):
    """半截 body 既不能当成 EOF，也不能交给下游。"""

    def _run(self, scope, receive, fake_app):
        messages, send = _collect()
        patches = [
            mock.patch.object(dispatch, "resolve",
                              lambda request: (registry.DEFAULT_PLATFORM, "")),
            mock.patch.object(dispatch, "_key_of", lambda request: _key()),
            mock.patch.object(dispatch, "_disabled_platforms", lambda: set()),
            mock.patch.object(dispatch, "_db", lambda: _Ctx()),
            mock.patch.object(dispatch, "_spec_with_gateway",
                              lambda pid, realm: None),
            mock.patch.object(dispatch, "_module_of", lambda spec: None),
        ]
        with contextlib.ExitStack() as stack:
            for patch in patches:
                patch.start()
                stack.callback(patch.stop)
            with _fake_traffic_modules():
                middleware = dispatch.PlatformDispatchMiddleware(fake_app)
                asyncio.run(middleware(scope, receive, send))
        return messages

    def test_read_body_raises_on_pre_read_disconnect(self):
        async def receive():
            return {"type": "http.disconnect"}

        with self.assertRaises(dispatch.ClientDisconnected):
            asyncio.run(dispatch._read_body(receive, 1024))

    def test_disconnect_after_valid_json_prefix_is_not_forwarded(self):
        # more_body=True 说明客户端还没发完：这段「合法 JSON」只是前缀。
        part = b'{"model": "main-auto"}'
        state = {"i": 0}

        async def receive():
            if state["i"] == 0:
                state["i"] = 1
                return {"type": "http.request", "body": part, "more_body": True}
            return {"type": "http.disconnect"}

        seen = {}

        async def fake_app(scope, receive, send):
            seen["called"] = True
            await JSONResponse({"app": True})(scope, receive, send)

        scope = _scope(headers=[(b"content-type", b"application/json")])
        messages = self._run(scope, receive, fake_app)

        self.assertFalse(seen.get("called"), "预读断开后仍把请求交给了下游")
        self.assertNotIn("_wb_body", scope, "断开时不该回放 body")
        self.assertEqual(_status(messages), 400)
        self.assertEqual(_payload(messages)["error"]["code"], "client_disconnected")

    def test_read_error_returns_400_without_forwarding(self):
        async def receive():
            raise RuntimeError("socket reset while reading body")

        seen = {}

        async def fake_app(scope, receive, send):
            seen["called"] = True
            await JSONResponse({"app": True})(scope, receive, send)

        scope = _scope(headers=[(b"content-type", b"application/json")])
        messages = self._run(scope, receive, fake_app)

        self.assertFalse(seen.get("called"), "读失败后仍把半截请求交给了下游")
        self.assertNotIn("_wb_body", scope)
        self.assertEqual(_status(messages), 400)
        self.assertEqual(_payload(messages)["error"]["code"], "body_read_failed")


# ---------------------------------------------------------------------------
# D. Marvis 预读失败必须就地 400（不重读已消费的 receive、不进业务逻辑）
# ---------------------------------------------------------------------------
class MarvisPreReadFailureTests(unittest.TestCase):
    """无 Content-Length 时体积检查边读边判；读到一半出错/断开就地收尾。

    旧实现把预读异常吞掉返回 None，入口再 `_body_of()` 读一次已被消费的
    receive，最终把「读不到 body」伪装成 400 invalid_request「缺少 messages」。
    """

    def _request(self, chunks, *, disconnect=False, error=None):
        calls = []
        state = {"i": 0}

        async def receive():
            calls.append(1)
            index = state["i"]
            state["i"] = index + 1
            if index < len(chunks):
                # 一律 more_body=True：body 没发完，下一次读才会出错/断开。
                return {"type": "http.request", "body": chunks[index],
                        "more_body": True}
            if disconnect:
                return {"type": "http.disconnect"}
            raise error or OSError("connection reset while reading body")

        scope = _scope(headers=[(b"content-type", b"application/json")])   # 无 Content-Length
        return Request(scope, receive=receive), calls

    def _run(self, request):
        with mock.patch.dict(os.environ,
                             {marvis_gateway.MAX_BODY_ENV: str(MIB)}):
            return asyncio.run(marvis_gateway.chat_completions(request))

    def _assert_body_failure(self, response, code):
        self.assertEqual(response.status_code, 400)
        error = json.loads(response.body)["error"]
        self.assertEqual(error["code"], code, error)
        self.assertEqual(error["type"], "invalid_request_error", error)

    def test_partial_body_then_oserror_is_body_read_failed(self):
        request, calls = self._request([b'{"model": "main-auto", "mess'])
        with mock.patch.object(marvis_gateway, "_body_of") as body_of:
            response = self._run(request)
        self._assert_body_failure(response, "body_read_failed")
        self.assertEqual(len(calls), 2, "出错后不该再读一次已消费的 receive")
        body_of.assert_not_called()

    def test_partial_body_then_disconnect_is_client_disconnected(self):
        request, calls = self._request([b'{"model": "main-auto", "mess'],
                                       disconnect=True)
        with mock.patch.object(marvis_gateway, "_body_of") as body_of:
            response = self._run(request)
        self._assert_body_failure(response, "client_disconnected")
        self.assertEqual(len(calls), 2, "断开后不该再读一次已消费的 receive")
        body_of.assert_not_called()

    def test_cancellation_still_propagates(self):
        async def receive():
            raise asyncio.CancelledError()

        scope = _scope(headers=[(b"content-type", b"application/json")])
        with mock.patch.dict(os.environ,
                             {marvis_gateway.MAX_BODY_ENV: str(MIB)}):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(marvis_gateway._reject_oversized(
                    Request(scope, receive=receive)))


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# E. §五 首选平台 / 候选 / 重试沿用同一套限制（计划任务 3）
#
# 两个假平台 alpha / beta 都声明同一个模型（库内写法不同：demo-model /
# DEMO-MODEL），账号与目录都种在临时 SQLite 里，平台侧由 _PlatformRecorder
# 记录「到底收到了什么」。策略固定成「首个候选」，这样调用顺序可断言；
# 策略本身的排序由 gateway_router 的用例覆盖。
# ---------------------------------------------------------------------------
_ALPHA = "alpha"
_BETA = "beta"
_MODEL = "demo-model"           # alpha 的库内写法（也是客户端拿到的公开名）
_BETA_SPELLING = "DEMO-MODEL"   # 同一个模型在 beta 的写法：大小写不同
_ALT_MODEL = "other-model"      # 只在 alpha 登记（分组负例用）
_BETA_ONLY = "beta-only-model"  # 只在 beta 登记（绑定 alpha 的 Key 必须改道）

KEY_AGG = "sk-routing-aggregate-0001"
KEY_BOUND = "sk-routing-bound-alpha-0001"
KEY_GROUP = "sk-routing-group-alpha-0001"
KEY_NOAGG = "sk-routing-noaggregate-0001"
KEY_GROUP_BOUND = "sk-routing-group-bound-0001"

_MODELS = (
    dict(platform=_ALPHA, model_id=_MODEL, enabled=1, credit_multiplier=1.0,
         note="Demo Model", upstream_metadata=""),
    dict(platform=_ALPHA, model_id=_ALT_MODEL, enabled=1, credit_multiplier=1.0,
         note="", upstream_metadata=""),
    dict(platform=_BETA, model_id=_BETA_SPELLING, enabled=1,
         credit_multiplier=2.0, note="", upstream_metadata=""),
    dict(platform=_BETA, model_id=_BETA_ONLY, enabled=1,
         credit_multiplier=2.0, note="", upstream_metadata=""),
)

_SCHEMA = {"done": False}


def _ensure_schema() -> None:
    if _SCHEMA["done"]:
        return
    from admin import db as admin_db

    admin_db.Base.metadata.create_all(bind=admin_db.engine)
    _SCHEMA["done"] = True


def _reset_caches() -> None:
    from admin.platform import gateway_router, model_suffix, models_router

    models_router.invalidate()
    models_router._native_cache.update({"at": 0.0, "ids": {}})
    dispatch.invalidate_model_cache()
    dispatch.invalidate_platform_cache()
    dispatch._health_cache.clear()
    gateway_router.invalidate()
    model_suffix.invalidate()
    model_suffix._cache.pop("storage", None)


def _reset_world() -> None:
    # 清表 + 清缓存：本轮只允许看到本轮种的行（缓存都带 TTL）。
    from sqlalchemy import text

    from admin import db as admin_db

    _ensure_schema()
    with admin_db.engine.begin() as conn:
        for table in ("api_keys", "accounts", "model_configs", "usage_logs",
                      "account_model_cool", "system_settings",
                      "model_group_items", "model_groups"):
            conn.execute(text('DELETE FROM "%s"' % table))
    _reset_caches()


def _seed_world(*, group_models=None, extra_rows=(), extra_accounts=(),
                group_bound_platform="") -> None:
    from admin import db as admin_db
    from admin.models import (Account, ApiKey, ModelConfig, ModelGroup,
                              ModelGroupItem)
    from admin.security import hash_key

    with admin_db.SessionLocal() as db:
        for row in _MODELS:
            db.add(ModelConfig(**row))
        for row in extra_rows:
            db.add(ModelConfig(**row))
        db.add_all([
            Account(platform=_ALPHA, name="alpha-1", uid="alpha-1",
                    auth_json="{}", status="active", domain="",
                    balance_total=100, balance_remain=100),
            Account(platform=_BETA, name="beta-1", uid="beta-1",
                    auth_json="{}", status="active", domain="",
                    balance_total=100, balance_remain=100),
        ])
        for kwargs in extra_accounts:
            db.add(Account(**kwargs))
        group_id = 0
        if group_models is not None:
            group = ModelGroup(name="routing-group")
            db.add(group)
            db.flush()
            group_id = int(group.id)
            for item in group_models:
                db.add(ModelGroupItem(group_id=group_id, model_id=item))
        for name, secret in (("agg", KEY_AGG), ("bound", KEY_BOUND),
                             ("group", KEY_GROUP), ("noagg", KEY_NOAGG),
                             ("groupbound", KEY_GROUP_BOUND)):
            platform = ""
            if name == "bound":
                platform = _ALPHA
            elif name == "groupbound":
                platform = group_bound_platform
            db.add(ApiKey(name="routing-" + name, key_hash=hash_key(secret),
                          key_prefix="sk-routing", unlimited=1, status="active",
                          aggregate=(name != "noagg"),
                          platform=platform,
                          group_id=(group_id if name in ("group", "groupbound")
                                    else 0)))
        db.commit()


class _PlatformRecorder:
    # 本地假平台网关：记录每次调用的 model 与正文，回固定状态码。

    def __init__(self, platform_id: str, status: int = 200):
        self.platform_id = platform_id
        self.status = status
        self.calls: list[dict] = []

    async def dispatch(self, request, path):
        raw = await request.body()
        try:
            payload = json.loads(raw) if raw else {}
        except (ValueError, UnicodeDecodeError):
            payload = {}
        self.calls.append({"platform": self.platform_id, "path": path,
                           "model": str(payload.get("model") or ""),
                           "body": payload})
        if self.status == 200:
            return JSONResponse(
                {"object": "chat.completion", "platform": self.platform_id,
                 "model": payload.get("model")})
        return JSONResponse(
            {"error": {"message": "%s upstream failed" % self.platform_id,
                       "code": "upstream_unavailable"}},
            status_code=self.status)

    @property
    def models(self) -> list[str]:
        return [call["model"] for call in self.calls]


@contextlib.contextmanager
def _fake_platforms(gateways, specs=None):
    # 把假平台装进 registry（只在本次测试内），其余访问器走真实实现。
    # gateway 传 None 表示「这个平台没有 adapter」——用来验证缺 adapter 的
    # 候选不会占掉重试名额。
    spec_map = {_ALPHA: _FakeSpec(_ALPHA), _BETA: _FakeSpec(_BETA)}
    for pid, kwargs in (specs or {}).items():
        spec_map[pid] = _FakeSpec(pid, **kwargs)
    real = {"get": registry.get, "ids": registry.ids,
            "all": registry.all_platforms, "load": registry.load_gateway}

    def _get(pid):
        key = str(pid or "").strip().lower()
        return spec_map.get(key) or real["get"](pid)

    def _ids(*, enabled_only: bool = True):
        out = list(real["ids"](enabled_only=enabled_only))
        for pid in spec_map:
            if pid not in out:
                out.append(pid)
        return out

    def _all(*args, **kwargs):
        out = list(real["all"](*args, **kwargs))
        seen = {str(getattr(spec, "id", "") or "") for spec in out}
        for pid, spec in spec_map.items():
            if pid not in seen:
                out.append(spec)
        return out

    def _load(spec):
        pid = str(getattr(spec, "id", "") or "").strip().lower()
        if pid in gateways:
            gateway = gateways[pid]
            if gateway is None:
                return None
            return SimpleNamespace(dispatch=gateway.dispatch)
        return real["load"](spec)

    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(registry, "get", _get))
        stack.enter_context(mock.patch.object(registry, "ids", _ids))
        stack.enter_context(mock.patch.object(registry, "all_platforms", _all))
        stack.enter_context(mock.patch.object(registry, "load_gateway", _load))
        yield


def _counting_app(calls):
    async def app(scope, receive, send):
        calls.append("main")
        await JSONResponse({"app": True})(scope, receive, send)

    return app


def _routing_request(model: str, key: str, *, extra_headers=(),
                     query: bytes = b""):
    body = json.dumps({"model": model,
                       "messages": [{"role": "user", "content": "ping"}],
                       "stream": False}).encode()
    headers = [(b"content-type", b"application/json"),
               (b"content-length", str(len(body)).encode()),
               (b"authorization", ("Bearer " + key).encode())]
    headers += [(str(name).lower().encode(), str(value).encode())
                for name, value in extra_headers]
    scope = _scope(headers=headers)
    scope["query_string"] = query
    return scope, body


def _run_routing(scope, body: bytes, gateways, *, fix_pick: bool = True,
                 specs=None):
    # 跑一次真实中间件；返回 (messages, main_app_calls)。
    # fix_pick=True 把平台策略固定成「首个候选」：本文件的断言是调用序列，
    # 不能让 random/round_robin 决定谁先被调用（策略排序另有用例覆盖）。
    main_calls: list[str] = []
    messages, send = _collect()
    middleware = dispatch.PlatformDispatchMiddleware(_counting_app(main_calls))
    receive, _calls = _receive_from([body])
    stack = contextlib.ExitStack()
    with stack:
        if fix_pick:
            stack.enter_context(mock.patch.object(
                dispatch, "_strategy_pick_within",
                lambda ids: ((list(ids)[0], "") if list(ids) else None)))
        stack.enter_context(_fake_platforms(gateways, specs=specs))
        stack.enter_context(_fake_traffic_modules())
        asyncio.run(middleware(scope, receive, send))
    return messages, main_calls


def _header_of(messages, name: str) -> str:
    for message in messages:
        if message.get("type") == "http.response.start":
            for key, value in message.get("headers") or []:
                if key.decode("latin-1").lower() == name:
                    return value.decode("latin-1")
    return ""


class PlatformScopedRoutingTests(unittest.TestCase):
    # 计划 §五 表格逐行：请求入口 -> 首次选择 -> 允许的后续尝试。

    def _pair(self, alpha_status=200, beta_status=200, *, beta_adapter=True):
        alpha = _PlatformRecorder(_ALPHA, status=alpha_status)
        beta = _PlatformRecorder(_BETA, status=beta_status)
        gateways = {_ALPHA: alpha, _BETA: beta if beta_adapter else None}
        return alpha, beta, gateways

    def test_model_suffix_pins_platform_and_keeps_its_503(self):
        _reset_world()
        _seed_world()
        alpha, beta, gateways = self._pair(alpha_status=503)
        scope, body = _routing_request(_MODEL + "-" + _ALPHA, KEY_AGG)
        messages, main_calls = _run_routing(scope, body, gateways)

        self.assertEqual([c["platform"] for c in alpha.calls], [_ALPHA])
        self.assertEqual(beta.calls, [], "仅后缀固定 alpha 时不许改道到 beta")
        self.assertEqual(main_calls, [], "带平台后缀的请求不该回主应用")
        self.assertEqual(_status(messages), 503)
        self.assertEqual(alpha.models, [_MODEL], "上游只认裸名")

    def test_platform_header_pins_platform_and_keeps_its_503(self):
        _reset_world()
        _seed_world()
        alpha, beta, gateways = self._pair(alpha_status=503)
        scope, body = _routing_request(
            _MODEL, KEY_AGG, extra_headers=[("x-platform", _ALPHA)])
        messages, main_calls = _run_routing(scope, body, gateways)

        self.assertEqual(alpha.models, [_MODEL])
        self.assertEqual(beta.calls, [], "X-Platform 固定后不许跨平台重试")
        self.assertEqual(main_calls, [])
        self.assertEqual(_status(messages), 503)

    def test_query_platform_pins_platform_and_keeps_its_503(self):
        _reset_world()
        _seed_world()
        alpha, beta, gateways = self._pair(alpha_status=503)
        scope, body = _routing_request(
            _MODEL, KEY_AGG, query=b"platform=alpha&realm=")
        messages, main_calls = _run_routing(scope, body, gateways)

        self.assertEqual(alpha.models, [_MODEL])
        self.assertEqual(beta.calls, [])
        self.assertEqual(main_calls, [])
        self.assertEqual(_status(messages), 503)

    def test_bound_key_pins_platform_and_keeps_its_503(self):
        _reset_world()
        _seed_world()
        alpha, beta, gateways = self._pair(alpha_status=503)
        scope, body = _routing_request(_MODEL, KEY_BOUND)
        messages, main_calls = _run_routing(scope, body, gateways)

        self.assertEqual(alpha.models, [_MODEL])
        self.assertEqual(beta.calls, [], "绑定 alpha 的 Key 不许用 beta 的额度")
        self.assertEqual(main_calls, [])
        self.assertEqual(_status(messages), 503)

    def test_bare_aggregate_fails_over_with_per_target_spelling(self):
        _reset_world()
        _seed_world()
        alpha, beta, gateways = self._pair(alpha_status=503, beta_status=200)
        scope, body = _routing_request(_MODEL, KEY_AGG)
        messages, main_calls = _run_routing(scope, body, gateways)

        self.assertEqual(alpha.models, [_MODEL], "alpha 收到自己的写法")
        self.assertEqual(beta.models, [_BETA_SPELLING], "beta 收到它自己的写法")
        self.assertEqual([c["platform"] for c in alpha.calls], [_ALPHA])
        self.assertEqual([c["platform"] for c in beta.calls], [_BETA])
        self.assertEqual(_status(messages), 200)
        self.assertEqual(_header_of(messages, "x-gateway-platform"), _BETA,
                         "终态落点必须是真正给出 200 的平台")
        self.assertEqual(main_calls, [])

    def test_bare_without_aggregate_does_not_fail_over(self):
        _reset_world()
        _seed_world()
        alpha, beta, gateways = self._pair(alpha_status=503)
        scope, body = _routing_request(_MODEL, KEY_NOAGG)
        messages, main_calls = _run_routing(scope, body, gateways)

        self.assertEqual(alpha.models, [_MODEL])
        self.assertEqual(beta.calls, [], "Key 关了聚合就不许跨平台重试")
        self.assertEqual(main_calls, [])
        self.assertEqual(_status(messages), 503)

    def test_disabled_platform_is_never_used_for_retry(self):
        _reset_world()
        _seed_world()
        alpha, beta, gateways = self._pair(alpha_status=503)
        scope, body = _routing_request(_MODEL, KEY_AGG)
        with mock.patch.object(dispatch, "_disabled_platforms",
                               lambda: {_BETA}):
            messages, main_calls = _run_routing(scope, body, gateways)

        self.assertEqual(alpha.models, [_MODEL])
        self.assertEqual(beta.calls, [], "被禁用的平台不许被排序加回来")
        self.assertEqual(_status(messages), 503)
        self.assertEqual(main_calls, [])

    def test_suffix_wins_over_conflicting_header(self):
        _reset_world()
        _seed_world()
        alpha, beta, gateways = self._pair(alpha_status=200)
        scope, body = _routing_request(
            _MODEL + "-" + _ALPHA, KEY_AGG,
            extra_headers=[("x-platform", _BETA)])
        messages, main_calls = _run_routing(scope, body, gateways)

        self.assertEqual(alpha.models, [_MODEL])
        self.assertEqual(beta.calls, [], "后缀与头冲突时以后缀为准")
        self.assertEqual(_status(messages), 200)
        self.assertEqual(_header_of(messages, "x-gateway-platform"), _ALPHA)
        self.assertEqual(main_calls, [])

    def test_group_scope_routes_to_group_platform_without_main_app(self):
        _reset_world()
        _seed_world(group_models=[_MODEL])
        alpha, beta, gateways = self._pair()
        scope, body = _routing_request(_MODEL, KEY_GROUP)
        messages, main_calls = _run_routing(scope, body, gateways)

        self.assertEqual(alpha.models, [_MODEL],
                         "分组只含 alpha 的行时，上游必须收到组内原生 ID")
        self.assertEqual(beta.calls, [], "组外平台不许承接")
        self.assertEqual(main_calls, [], "主应用不该被调用（否则会把组内 ID 发错平台）")
        self.assertEqual(_status(messages), 200)
        self.assertEqual(_header_of(messages, "x-gateway-platform"), _ALPHA)

    def test_group_scope_rejects_out_of_group_model(self):
        _reset_world()
        _seed_world(group_models=[_MODEL])
        alpha, beta, gateways = self._pair()
        scope, body = _routing_request(_ALT_MODEL, KEY_GROUP)
        messages, main_calls = _run_routing(scope, body, gateways)

        self.assertEqual(_status(messages), 403)
        self.assertEqual(_payload(messages)["error"]["code"],
                         "model_not_in_group")
        self.assertEqual(alpha.calls, [], "组外模型不许打任何平台")
        self.assertEqual(beta.calls, [])
        self.assertEqual(main_calls, [], "拒绝必须发生在 dispatch 选路阶段")

    def test_candidate_without_adapter_does_not_consume_retry_slot(self):
        _reset_world()
        _seed_world()
        alpha, beta, gateways = self._pair(alpha_status=503, beta_adapter=False)
        scope, body = _routing_request(_MODEL, KEY_AGG)
        messages, main_calls = _run_routing(scope, body, gateways)

        self.assertEqual(alpha.models, [_MODEL])
        self.assertEqual(beta.calls, [], "没有 adapter 的平台调不动")
        self.assertEqual(_status(messages), 503)
        self.assertEqual(main_calls, [])

    def test_reroute_does_not_override_the_already_selected_platform(self):
        # 计划 §五 第 71 行：改道接收「当前已选目标」，不再按「想要的平台 =
        # 默认平台」重算 —— 否则已经按策略选中 beta 的请求会被改道回 alpha。
        _reset_world()
        _seed_world()
        alpha, beta, gateways = self._pair()
        scope, body = _routing_request(_MODEL, KEY_AGG)

        with mock.patch.object(dispatch, "_strategy_pick_within",
                               lambda ids: ((_BETA, "") if _BETA in list(ids)
                                            else None)):
            messages, main_calls = _run_routing(scope, body, gateways,
                                                fix_pick=False)

        self.assertEqual(alpha.calls, [],
                         "选路已经选中 beta，改道不许把它换成 alpha")
        self.assertEqual(beta.models, [_BETA_SPELLING])
        self.assertEqual(_status(messages), 200)
        self.assertEqual(_header_of(messages, "x-gateway-platform"), _BETA)
        self.assertEqual(main_calls, [])

    def test_bound_key_reroutes_when_model_is_not_on_bound_platform(self):
        # 绑定平台的 Key 在「绑定目标确实做不了这个模型」时仍可改道；
        # 改道后账号范围 / 分组 / 配额照旧（_ReroutedKey 只脱 platform）。
        _reset_world()
        _seed_world()
        alpha, beta, gateways = self._pair()
        scope, body = _routing_request(_BETA_ONLY, KEY_BOUND)
        messages, main_calls = _run_routing(scope, body, gateways)

        self.assertEqual(alpha.calls, [], "绑定平台做不了这个模型")
        self.assertEqual(beta.models, [_BETA_ONLY])
        self.assertEqual(_status(messages), 200)
        self.assertEqual(_header_of(messages, "x-gateway-platform"), _BETA)
        self.assertEqual(main_calls, [])

    def test_candidates_and_ranking_are_computed_once_per_request(self):
        # 计划 §五 第 75 行：选择 / 改道 / 重试共用同一份候选与排序决定。
        _reset_world()
        _seed_world()
        alpha, beta, gateways = self._pair(alpha_status=503, beta_status=200)
        scope, body = _routing_request(_MODEL, KEY_AGG)

        candidate_calls: list[str] = []
        real_detail = dispatch._candidates_detail

        def _counting_detail(db, model, key):
            candidate_calls.append(model)
            return real_detail(db, model, key)

        pick_calls: list[tuple] = []

        def _counting_pick(ids):
            ids = list(ids)
            pick_calls.append(tuple(ids))
            return (ids[0], "") if ids else None

        with mock.patch.object(dispatch, "_candidates_detail",
                               _counting_detail), \
                mock.patch.object(dispatch, "_strategy_pick_within",
                                  _counting_pick):
            messages, _main = _run_routing(scope, body, gateways, fix_pick=False)

        self.assertEqual(_status(messages), 200)
        self.assertEqual(len(candidate_calls), 1,
                         "一次请求只该算一次候选：%r" % (candidate_calls,))
        self.assertEqual(len(pick_calls), 1,
                         "一次请求只该排序一次：%r" % (pick_calls,))
        self.assertEqual([c["platform"] for c in alpha.calls], [_ALPHA])
        self.assertEqual([c["platform"] for c in beta.calls], [_BETA])

    def test_attempt_log_carries_platform_realm_model_and_attempt(self):
        import logging

        _reset_world()
        _seed_world()
        _alpha, _beta, gateways = self._pair(alpha_status=503, beta_status=200)
        scope, body = _routing_request(_MODEL, KEY_AGG)

        records: list[str] = []

        class _Handler(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        target = logging.getLogger("admin.platform.dispatch")
        handler = _Handler(level=logging.INFO)
        old_level = target.level
        target.addHandler(handler)
        target.setLevel(logging.INFO)
        try:
            messages, _main = _run_routing(scope, body, gateways)
        finally:
            target.removeHandler(handler)
            target.setLevel(old_level)

        self.assertEqual(_status(messages), 200)
        attempts = [line for line in records if "dispatch 尝试 |" in line]
        self.assertEqual(len(attempts), 2, attempts)
        self.assertIn("attempt=1/2", attempts[0])
        self.assertIn("public_platform='alpha'", attempts[0])
        self.assertIn("model='demo-model'", attempts[0])
        self.assertIn("realm=''", attempts[0])
        self.assertIn("requested='demo-model'", attempts[0])
        self.assertIn("attempt=2/2", attempts[1])
        self.assertIn("public_platform='beta'", attempts[1])
        self.assertIn("model='DEMO-MODEL'", attempts[1])
        self.assertFalse([line for line in records if "ping" in line])


# ---------------------------------------------------------------------------
# F. 分组授权的边界（t17）：别收得过紧 + 交集语义 + 403/404 可区分 + :intl 字面量
# ---------------------------------------------------------------------------
_WB = registry.DEFAULT_PLATFORM
#: 只在 realm 平台用：一个 storage 两个区（trae 形态）
_TRAE_ROWS = (
    dict(platform="trae", model_id=_MODEL, enabled=1, credit_multiplier=1.0,
         note="", upstream_metadata=""),
    dict(platform="trae", model_id=_MODEL + ":intl", enabled=1,
         credit_multiplier=1.0, note="", upstream_metadata=""),
)
_TRAE_SPECS = {
    "trae-cn": {"realm": "cn", "storage": "trae"},
    "trae-intl": {"realm": "intl", "storage": "trae"},
}


def _default_platform_row(model_id=_MODEL):
    return dict(platform=_WB, model_id=model_id, enabled=1,
                credit_multiplier=1.0, note="", upstream_metadata="")


class GroupAuthorizationBoundaryTests(unittest.TestCase):
    # 1) 分组里**合法包含**默认平台行时，组内请求不得被误判成越权。

    def test_group_including_default_platform_member_is_not_over_rejected(self):
        _reset_world()
        _seed_world(group_models=[_MODEL],
                    extra_rows=[_default_platform_row()])
        alpha = _PlatformRecorder(_ALPHA)
        beta = _PlatformRecorder(_BETA)
        scope, body = _routing_request(_MODEL, KEY_GROUP)
        messages, main_calls = _run_routing(scope, body,
                                            {_ALPHA: alpha, _BETA: beta})

        self.assertEqual(_status(messages), 200, _payload(messages))
        # 组内成员：alpha 与默认平台都合法，落到哪一个都算通过；beta 不在组里
        self.assertEqual(beta.calls, [])
        landed = ([call["platform"] for call in alpha.calls]
                  + ([ _WB ] if main_calls else []))
        self.assertEqual(len(landed), 1, landed)
        self.assertIn(landed[0], (_ALPHA, _WB))

    def test_group_including_default_platform_accepts_workbuddy_suffix(self):
        _reset_world()
        _seed_world(group_models=[_MODEL],
                    extra_rows=[_default_platform_row()])
        alpha = _PlatformRecorder(_ALPHA)
        scope, body = _routing_request(_MODEL + "-" + _WB, KEY_GROUP)
        messages, main_calls = _run_routing(scope, body, {_ALPHA: alpha})

        self.assertEqual(_status(messages), 200, _payload(messages))
        self.assertEqual(main_calls, ["main"], "组内成员后缀必须能落到主应用")
        self.assertEqual(alpha.calls, [], "已固定到默认平台，不该再调别人")
        self.assertEqual(_header_of(messages, "x-gateway-platform"), _WB)

    def test_group_without_default_platform_rejects_workbuddy_suffix(self):
        _reset_world()
        _seed_world(group_models=[_MODEL])   # 组里只有 alpha 的行
        alpha = _PlatformRecorder(_ALPHA)
        scope, body = _routing_request(_MODEL + "-" + _WB, KEY_GROUP)
        messages, main_calls = _run_routing(scope, body, {_ALPHA: alpha})

        self.assertEqual(_status(messages), 403, _payload(messages))
        self.assertEqual(_payload(messages)["error"]["code"],
                         "model_not_in_group")
        self.assertEqual(main_calls, [], "组外后缀不许转交主应用")
        self.assertEqual(alpha.calls, [])

    # 2) group_id 与 api_keys.platform 同时设置：只落在「分组 ∩ 绑定平台」。

    def test_group_and_bound_platform_intersect_only(self):
        # 2a) 绑定 beta，但组里只有 alpha 的行 -> 403（绑定不得绕过分组）
        _reset_world()
        _seed_world(group_models=[_MODEL], group_bound_platform=_BETA)
        alpha = _PlatformRecorder(_ALPHA)
        beta = _PlatformRecorder(_BETA)
        scope, body = _routing_request(_MODEL, KEY_GROUP_BOUND)
        messages, main_calls = _run_routing(scope, body,
                                            {_ALPHA: alpha, _BETA: beta})
        self.assertEqual(_status(messages), 403, _payload(messages))
        self.assertEqual(_payload(messages)["error"]["code"],
                         "model_not_in_group")
        self.assertEqual(alpha.calls, [], "组内但非绑定平台不许承载")
        self.assertEqual(beta.calls, [], "绑定平台不在组内不许承载")
        self.assertEqual(main_calls, [])

        # 2b) 组里登记的正是绑定平台的行 -> 200，落点就是绑定平台
        _reset_world()
        _seed_world(group_models=[_BETA_SPELLING], group_bound_platform=_BETA)
        alpha = _PlatformRecorder(_ALPHA)
        beta = _PlatformRecorder(_BETA)
        scope, body = _routing_request(_MODEL, KEY_GROUP_BOUND)
        messages, main_calls = _run_routing(scope, body,
                                            {_ALPHA: alpha, _BETA: beta})
        self.assertEqual(_status(messages), 200, _payload(messages))
        self.assertEqual(beta.models, [_BETA_SPELLING])
        self.assertEqual(alpha.calls, [])
        self.assertEqual(main_calls, [])

    # 3) 「组外」与「目录查不到」必须能区分。

    def test_group_rejection_and_missing_directory_are_distinguishable(self):
        # 3a) 组 Key + 模型存在（alpha 有行）+ 固定到组外平台 -> 403 授权错误
        _reset_world()
        _seed_world(group_models=[_MODEL])
        alpha = _PlatformRecorder(_ALPHA)
        scope, body = _routing_request(_MODEL + "-" + _WB, KEY_GROUP)
        group_messages, _main = _run_routing(scope, body, {_ALPHA: alpha})
        group_pair = (_status(group_messages), _code_of(group_messages))

        # 3b) 同类请求（固定到目录里没有这个模型的平台）但**无分组** -> 404。
        # 用非默认平台 alpha 作为固定目标，这样才会走到 _model_for_platform 的
        # 「模型不属于该平台」判定；默认平台本来就走主应用自己的白名单。
        _reset_world()
        _seed_world()
        alpha = _PlatformRecorder(_ALPHA)
        scope, body = _routing_request(_BETA_ONLY + "-" + _ALPHA, KEY_AGG)
        plain_messages, _main = _run_routing(scope, body, {_ALPHA: alpha})
        plain_pair = (_status(plain_messages), _code_of(plain_messages))

        # 3c) 组 Key + 模型谁都没登记 -> 404 model_not_found（不是越权）
        _reset_world()
        _seed_world(group_models=[_MODEL])
        alpha = _PlatformRecorder(_ALPHA)
        scope, body = _routing_request(_GHOST_MODEL, KEY_GROUP)
        ghost_messages, _main = _run_routing(scope, body, {_ALPHA: alpha})
        ghost_pair = (_status(ghost_messages), _code_of(ghost_messages))

        self.assertEqual(group_pair, (403, "model_not_in_group"))
        self.assertEqual(plain_pair, (404, "model_not_on_platform"), plain_pair)
        self.assertIn(plain_pair[1], ("model_not_on_platform",
                                      "model_not_found"), plain_pair)
        self.assertEqual(ghost_pair, (404, "model_not_found"))
        self.assertEqual(len({group_pair, plain_pair, ghost_pair}), 3,
                         (group_pair, plain_pair, ghost_pair))

    # 4) 不带后缀的 :intl 字面量：落点 / 上游串 / 分组判定都用该区的目录身份。

    def test_intl_literal_without_suffix_uses_that_realms_identity(self):
        _reset_world()
        _seed_world(group_models=[_MODEL + ":intl"], extra_rows=_TRAE_ROWS)
        cn = _PlatformRecorder("trae-cn")
        intl = _PlatformRecorder("trae-intl")
        scope, body = _routing_request(_MODEL + ":intl", KEY_GROUP)
        messages, main_calls = _run_routing(
            scope, body, {"trae-cn": cn, "trae-intl": intl},
            specs=_TRAE_SPECS)

        self.assertEqual(_status(messages), 200, _payload(messages))
        self.assertEqual(intl.models, [_MODEL],
                         "上游串必须去掉区域标记")
        self.assertEqual(cn.calls, [], "组里只登记国际区，国内区不许承接")
        self.assertEqual(_header_of(messages, "x-gateway-platform"), "trae-intl")
        self.assertEqual(main_calls, [])

    def test_group_cross_realm_twin_is_rejected(self):
        # 组里只有国内行：国际区后缀必须 403；国内后缀必须 200（两个方向）
        _reset_world()
        _seed_world(group_models=[_MODEL], extra_rows=_TRAE_ROWS)
        cn = _PlatformRecorder("trae-cn")
        intl = _PlatformRecorder("trae-intl")
        scope, body = _routing_request(_MODEL + "-trae-intl", KEY_GROUP)
        messages, _main = _run_routing(
            scope, body, {"trae-cn": cn, "trae-intl": intl},
            specs=_TRAE_SPECS)
        self.assertEqual(_status(messages), 403, _payload(messages))
        self.assertEqual(_payload(messages)["error"]["code"],
                         "model_not_in_group")
        self.assertEqual(intl.calls, [])
        self.assertEqual(cn.calls, [])

        _reset_world()
        _seed_world(group_models=[_MODEL], extra_rows=_TRAE_ROWS)
        cn = _PlatformRecorder("trae-cn")
        intl = _PlatformRecorder("trae-intl")
        scope, body = _routing_request(_MODEL + "-trae-cn", KEY_GROUP)
        messages, _main = _run_routing(
            scope, body, {"trae-cn": cn, "trae-intl": intl},
            specs=_TRAE_SPECS)
        self.assertEqual(_status(messages), 200, _payload(messages))
        self.assertEqual(cn.models, [_MODEL])
        self.assertEqual(intl.calls, [])

    def test_group_membership_is_resolved_once_per_request(self):
        # 硬约束：同一请求最多查一次组内集合；无分组时一次都不查。
        import admin.routers.groups as groups_module

        calls: list[int] = []
        real = groups_module.effective_group_models

        def _counting(db, gid):
            calls.append(int(gid))
            return real(db, gid)

        _reset_world()
        _seed_world(group_models=[_MODEL])
        alpha = _PlatformRecorder(_ALPHA)
        beta = _PlatformRecorder(_BETA)
        scope, body = _routing_request(_MODEL, KEY_GROUP)
        with mock.patch.object(groups_module, "effective_group_models",
                               _counting):
            messages, _main = _run_routing(scope, body,
                                           {_ALPHA: alpha, _BETA: beta})
        self.assertEqual(_status(messages), 200, _payload(messages))
        self.assertEqual(len(calls), 1, "组内集合只该查一次：%r" % (calls,))

        # 无分组 Key：一次都不查（行为一个字节不变）
        _reset_world()
        _seed_world()
        calls.clear()
        alpha = _PlatformRecorder(_ALPHA)
        beta = _PlatformRecorder(_BETA)
        scope, body = _routing_request(_MODEL, KEY_AGG)
        with mock.patch.object(groups_module, "effective_group_models",
                               _counting):
            messages, _main = _run_routing(scope, body,
                                           {_ALPHA: alpha, _BETA: beta})
        self.assertEqual(_status(messages), 200, _payload(messages))
        self.assertEqual(calls, [], "无分组不该有任何组查询：%r" % (calls,))

    def test_group_case_variant_of_same_row_is_allowed(self):
        # 组/目录登记 DeepSeek-V4.1-Flash，客户端发小写：同一目录身份 -> 放行，
        # 且上游收到**库内原拼写**。
        _reset_world()
        _seed_world(group_models=["DeepSeek-V4.1-Flash"],
                    extra_rows=[_default_platform_row("DeepSeek-V4.1-Flash")])
        alpha = _PlatformRecorder(_ALPHA)
        scope, body = _routing_request("deepseek-v4.1-flash-" + _WB, KEY_GROUP)
        messages, main_calls = _run_routing(scope, body, {_ALPHA: alpha})

        self.assertEqual(_status(messages), 200, _payload(messages))
        self.assertEqual(main_calls, ["main"])
        self.assertEqual(alpha.calls, [])


def _code_of(messages) -> str:
    return str((_payload(messages).get("error") or {}).get("code") or "")


_GHOST_MODEL = "ghost-model"
