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
    """够 dispatch 用的 PlatformSpec 替身。"""

    enabled = True
    realm = ""

    def __init__(self, pid):
        self.id = pid
        self.storage_platform = pid

    def storage(self):
        return self.id


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
            mock.patch.object(dispatch, "_candidates_for_model",
                              lambda db, model, key: [("fake-b", "", model)]),
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
