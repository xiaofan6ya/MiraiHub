# -*- coding: utf-8 -*-
"""protocol_bridge 流式中继测试：模拟内层 OpenAI chunk SSE，断言三种协议的
工具调用 / 思考块 / 心跳 / 错误事件完整到达客户端。"""
import asyncio
import json
import os
import sys
import pathlib
import time
import types
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import importlib.util

_spec = importlib.util.spec_from_file_location(
    "protocol_bridge", str(pathlib.Path(__file__).resolve().parent.parent
                           / "admin" / "platform" / "protocol_bridge.py"))
bridge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bridge)

from admin.platform.marvis import gateway


def frames(*payloads):
    out = []
    for p in payloads:
        if p == ": ping":
            out.append(b": ping\n\n")
        elif p == "[DONE]":
            out.append(b"data: [DONE]\n\n")
        else:
            out.append(("data: %s\n\n" % json.dumps(p, ensure_ascii=False)).encode())
    return out


class FakeInner:
    """模拟内层 StreamingResponse：只提供 body_iterator。"""

    def __init__(self, chunks):
        self.body_iterator = self._gen(chunks)

    async def _gen(self, chunks):
        for c in chunks:
            yield c


def collect(relay_response):
    async def _collect():
        out = []
        async for piece in relay_response.body_iterator:
            if isinstance(piece, bytes):
                out.append(piece.decode())
            else:
                out.append(str(piece))
        return out
    return asyncio.run(_collect())


def parse_events(lines):
    """把 SSE 文本拆成 (type, payload)。

    内层帧可能一段含多行（`event: X\\ndata: Y\\n\\n`），必须按行拆，
    不能假设一段 = 一行。
    """
    events = []
    for piece in lines:
        for line in piece.split("\n"):
            line = line.strip()
            if not line:
                continue
            if line.startswith(":"):
                events.append(("comment", line))
                continue
            if line.startswith("data:"):
                payload = line[5:].strip()
                try:
                    data = json.loads(payload)
                except ValueError:
                    continue
                events.append((data.get("type") or "?", data))
    return events


TOOL_CHUNKS = frames(
    {"choices": [{"delta": {"reasoning_content": "thinking..."}}]},
    {"choices": [{"delta": {"content": "我来读文件"}}]},
    {"choices": [{"delta": {"tool_calls": [
        {"index": 0, "id": "c1", "type": "function",
         "function": {"name": "read", "arguments": "{\"pa"}}]}}]},
    {"choices": [{"delta": {"tool_calls": [
        {"index": 0, "function": {"arguments": "th\": \"/a\"}"}}]}}]},
    {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
    {"usage": {"prompt_tokens": 100, "completion_tokens": 10,
               "total_tokens": 110,
               "prompt_tokens_details": {"cached_tokens": 40}},
     "choices": []},
    "[DONE]",
)


def test_anthropic_relay_tools():
    inner = FakeInner(TOOL_CHUNKS)
    resp = bridge.anthropic_stream_relay(inner, "loomy-x", prefix="t")
    events = parse_events(collect(resp))
    types = [t for t, _ in events]
    assert types[0] == "message_start", types[:3]
    assert "content_block_start" in types
    # 思考块
    thinking_start = [d for t, d in events
                      if t == "content_block_start"
                      and d["content_block"]["type"] == "thinking"]
    assert thinking_start, "缺 thinking 块"
    assert any(d["delta"].get("thinking") == "thinking..."
               for t, d in events if t == "content_block_delta")
    # 正文块
    text_start = [d for t, d in events
                  if t == "content_block_start"
                  and d["content_block"]["type"] == "text"]
    assert text_start
    # 工具块：id+name+完整参数
    tool_start = [d for t, d in events
                  if t == "content_block_start"
                  and d["content_block"]["type"] == "tool_use"]
    assert tool_start and tool_start[0]["content_block"]["id"] == "c1"
    assert tool_start[0]["content_block"]["name"] == "read"
    args = "".join(d["delta"]["partial_json"] for t, d in events
                   if t == "content_block_delta"
                   and d["delta"].get("type") == "input_json_delta")
    assert json.loads(args) == {"path": "/a"}, args
    # 收尾：stop_reason=tool_use + 缓存字段保留
    delta = [d for t, d in events if t == "message_delta"][0]
    assert delta["delta"]["stop_reason"] == "tool_use"
    assert delta["usage"]["input_tokens"] == 60
    assert delta["usage"]["cache_read_input_tokens"] == 40
    assert types[-1] == "message_stop"
    # 每个开过的块都有 stop
    stops = [d["index"] for t, d in events if t == "content_block_stop"]
    starts = [d["index"] for t, d in events if t == "content_block_start"]
    assert sorted(stops) == sorted(starts), (starts, stops)


def test_responses_relay_tools():
    inner = FakeInner(TOOL_CHUNKS)
    resp = bridge.responses_stream_relay(inner, "loomy-x", prefix="t")
    events = parse_events(collect(resp))
    types = [t for t, _ in events]
    assert types[0] == "response.created"
    args = "".join(d["delta"] for t, d in events
                   if t == "response.function_call_arguments.delta")
    assert json.loads(args) == {"path": "/a"}, args
    fc_done = [d for t, d in events
               if t == "response.output_item.done"
               and d["item"]["type"] == "function_call"]
    assert fc_done and fc_done[0]["item"]["call_id"] == "c1"
    assert fc_done[0]["item"]["arguments"].startswith('{"path"')
    completed = [d for t, d in events if t == "response.completed"][0]
    assert completed["response"]["status"] == "completed"
    assert completed["response"]["usage"]["input_tokens"] == 100
    # output 数组含 function_call 项
    fc_items = [o for o in completed["response"]["output"]
                if o["type"] == "function_call"]
    assert fc_items, "completed.output 缺 function_call"


def test_anthropic_relay_error_event():
    chunks = frames(
        {"choices": [{"delta": {"content": "hi"}}]},
        {"error": {"message": "rate limited", "type": "rate"}},
        "[DONE]",
    )
    resp = bridge.anthropic_stream_relay(FakeInner(chunks), "m", prefix="t")
    text = "".join(collect(resp))
    assert "event: error" in text, "上游错误必须显式发 error 事件"
    assert "rate limited" in text
    assert text.rstrip().endswith('"type": "message_stop"}') or \
        '"message_stop"' in text, "错误后仍要补 message_stop"


def test_responses_relay_incomplete():
    chunks = frames(
        {"choices": [{"delta": {"content": "partial"}}]},
        {"choices": [{"delta": {}, "finish_reason": "length"}]},
        "[DONE]",
    )
    resp = bridge.responses_stream_relay(FakeInner(chunks), "m", prefix="t")
    events = parse_events(collect(resp))
    assert any(t == "response.incomplete" for t, _ in events)
    inc = [d for t, d in events if t == "response.incomplete"][0]
    assert inc["response"]["incomplete_details"]["reason"] == "max_output_tokens"
    assert not any(t == "response.completed" for t, _ in events)


def test_ping_passthrough():
    chunks = frames(": ping", {"choices": [{"delta": {"content": "hi"}}]}, "[DONE]")
    resp = bridge.anthropic_stream_relay(FakeInner(chunks), "m", prefix="t")
    text = "".join(collect(resp))
    assert ": ping" in text, "心跳注释必须透传"


def test_with_pings_keeps_order_and_inserts():
    def slow_source():
        yield "a"
        time.sleep(0.35)
        yield "b"

    got = list(bridge.with_pings(slow_source(), interval=0.1, ping=b"PING"))
    # a ... PING(至少一帧) ... b，顺序不乱
    assert got[0] == "a" and got[-1] == "b"
    assert b"PING" in got[1:-1], got


class _GatewayRequest:
    headers = {"content-length": "100"}
    client = types.SimpleNamespace(host="127.0.0.1")
    scope = {}

    async def json(self):
        return {"model": "deepseek-v4.1-flash", "stream": True,
                "messages": [{"role": "user", "content": "hi"}]}


class _GatewayUpstream:
    def __init__(self):
        self.chunks = [b'data: {"error":{"code":"E_INBAND",'
                       b'"message":"upstream boom"}}\n\n']
        self.closed = False

    def read1(self, _size=-1):
        return self.chunks.pop(0) if self.chunks else b""

    def close(self):
        self.closed = True


class _CloseTrackingIterator:
    def __init__(self, iterator):
        self.iterator = iterator
        self.close_count = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.iterator.__anext__()

    async def aclose(self):
        self.close_count += 1
        await self.iterator.aclose()


def test_marvis_relays_close_default_keepalive_and_bill_inband_failure():
    """Regression: both relays close default keepalive and bill in-band errors."""
    calls = {"success": [], "release": [], "bill": []}
    current = {"upstream": None}
    account = types.SimpleNamespace(id=7, uid="u7")

    def acquire(_request, _body, _realm, *, stream):
        assert stream
        return types.SimpleNamespace(id=3), current["upstream"], account, time.perf_counter()

    async def bill(*_args, **kwargs):
        calls["bill"].append(kwargs)

    async def exercise(relay_factory):
        for values in calls.values():
            values.clear()
        current["upstream"] = _GatewayUpstream()
        response = await gateway.chat_completions(_GatewayRequest())
        tracked = _CloseTrackingIterator(response.body_iterator)
        relayed = relay_factory(types.SimpleNamespace(body_iterator=tracked))
        output = [piece async for piece in relayed.body_iterator]
        assert calls["release"] == [account]
        assert calls["success"] == []
        assert calls["bill"][-1]["error"]
        assert tracked.close_count == 1
        assert current["upstream"].closed
        return b"".join(output).decode()

    async def run():
        return [
            await exercise(lambda inner: bridge.anthropic_stream_relay(inner, "m")),
            await exercise(lambda inner: bridge.responses_stream_relay(inner, "m")),
        ]

    with mock.patch.dict(os.environ, {gateway._KEEPALIVE_ENV: "1"}), \
            mock.patch.object(gateway, "_validate_model", return_value="main-auto"), \
            mock.patch.object(gateway, "_prepare_body", side_effect=lambda body, _slot: dict(body)), \
            mock.patch.object(gateway, "_acquire", side_effect=acquire), \
            mock.patch.object(gateway, "_bill_async", new=bill), \
            mock.patch.object(gateway.shared, "note_success",
                              side_effect=lambda value: calls["success"].append(value)), \
            mock.patch.object(gateway.shared, "release",
                              side_effect=lambda value: calls["release"].append(value)), \
            mock.patch.object(gateway.client, "note_sentinel"):
        outputs = asyncio.run(run())

    assert "event: error" in outputs[0] and "upstream boom" in outputs[0]
    assert "event: response.failed" in outputs[1] and "upstream boom" in outputs[1]


def test_protocol_relays_close_inner_iterator_on_client_disconnect():
    async def exercise():
        cases = [
            (bridge.anthropic_stream_relay, b"event: content_block_delta"),
            (bridge.responses_stream_relay, b"event: response.output_text.delta"),
        ]
        closed = []
        for relay_factory, content_event in cases:
            inner = FakeInner([
                b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'])
            tracked = _CloseTrackingIterator(inner.body_iterator)
            inner.body_iterator = tracked
            response = relay_factory(inner, "m")
            iterator = response.body_iterator
            async for piece in iterator:
                if content_event in piece:
                    break
            await iterator.aclose()
            closed.append(tracked.close_count)
        assert closed == [1, 1], closed

    asyncio.run(exercise())


def test_protocol_relays_close_marvis_stream_on_asgi_disconnect():
    calls = {"success": [], "release": [], "bill": []}
    current = {"upstream": None}
    account = types.SimpleNamespace(id=7, uid="u7")

    def acquire(_request, _body, _realm, *, stream):
        assert stream
        return types.SimpleNamespace(id=3), current["upstream"], account, time.perf_counter()

    async def bill(*_args, **kwargs):
        calls["bill"].append(kwargs)

    async def exercise(relay_factory, content_event, terminal_event):
        for values in calls.values():
            values.clear()
        current["upstream"] = _GatewayUpstream()
        current["upstream"].chunks = [
            b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n']
        response = await gateway.chat_completions(_GatewayRequest())
        tracked = _CloseTrackingIterator(response.body_iterator)
        response.body_iterator = tracked
        relayed = relay_factory(types.SimpleNamespace(body_iterator=tracked), "m")
        messages = []
        content_sent = asyncio.Event()
        receive_calls = 0
        disconnected = False

        async def receive():
            nonlocal receive_calls, disconnected
            receive_calls += 1
            if receive_calls == 1:
                return {"type": "http.request", "body": b"", "more_body": False}
            await content_sent.wait()
            disconnected = True
            return {"type": "http.disconnect"}

        async def send(message):
            messages.append(message)
            if (message["type"] == "http.response.body"
                    and content_event in message.get("body", b"")):
                content_sent.set()
                await asyncio.sleep(0)

        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1", "method": "GET", "scheme": "http",
            "path": "/", "raw_path": b"/", "query_string": b"", "headers": [],
            "client": ("127.0.0.1", 1), "server": ("localhost", 80),
        }
        await relayed(scope, receive, send)

        assert any(message["type"] == "http.response.start" for message in messages)
        assert content_sent.is_set(), "disconnect must follow a normal relay content event"
        assert disconnected, "ASGI receive must report client disconnect"
        assert not any(terminal_event in message.get("body", b"")
                       for message in messages), messages
        assert tracked.close_count == 1
        assert current["upstream"].closed
        assert calls["success"] == [account]
        assert calls["release"] == [account]
        assert len(calls["bill"]) == 1

    async def run():
        await exercise(bridge.anthropic_stream_relay,
                       b"event: content_block_delta", b"event: message_stop")
        await exercise(bridge.responses_stream_relay,
                       b"event: response.output_text.delta",
                       b"event: response.completed")

    with mock.patch.dict(os.environ, {gateway._KEEPALIVE_ENV: "1"}), \
            mock.patch.object(gateway, "_validate_model", return_value="main-auto"), \
            mock.patch.object(gateway, "_prepare_body",
                              side_effect=lambda body, _slot: dict(body)), \
            mock.patch.object(gateway, "_acquire", side_effect=acquire), \
            mock.patch.object(gateway, "_bill_async", new=bill), \
            mock.patch.object(gateway.shared, "note_success",
                              side_effect=lambda value: calls["success"].append(value)), \
            mock.patch.object(gateway.shared, "release",
                              side_effect=lambda value: calls["release"].append(value)), \
            mock.patch.object(gateway.client, "note_sentinel"):
        asyncio.run(run())


def test_protocol_relays_close_inner_iterator_on_normal_eof_and_error():
    cases = [
        (frames(
            {"choices": [{"delta": {"content": "done"}, "finish_reason": "stop"}]},
            "[DONE]",
        ), None),
        (frames({"error": {"message": "relay failed", "type": "test"}},
                "[DONE]"), b"relay failed"),
    ]
    async def exercise():
        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1", "method": "GET", "scheme": "http",
            "path": "/", "raw_path": b"/", "query_string": b"", "headers": [],
            "client": ("127.0.0.1", 1), "server": ("localhost", 80),
        }

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(_message):
            messages.append(_message)

        for relay_factory, error_event in (
                (bridge.anthropic_stream_relay, b"event: error"),
                (bridge.responses_stream_relay, b"event: response.failed")):
            for chunks, error_text in cases:
                inner = FakeInner(chunks)
                tracked = _CloseTrackingIterator(inner.body_iterator)
                inner.body_iterator = tracked
                messages = []
                await relay_factory(inner, "m")(scope, receive, send)
                assert tracked.close_count == 1
                if error_text:
                    body = b"".join(message.get("body", b"") for message in messages)
                    assert error_event in body
                    assert error_text in body

    asyncio.run(exercise())


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("PASS %s" % fn.__name__)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            import traceback
            traceback.print_exc()
            print("FAIL %s: %s" % (fn.__name__, exc))
    print("== %d/%d passed ==" % (len(fns) - failed, len(fns)))
    sys.exit(1 if failed else 0)
