# -*- coding: utf-8 -*-
"""protocol_bridge 流式中继测试：模拟内层 OpenAI chunk SSE，断言三种协议的
工具调用 / 思考块 / 心跳 / 错误事件完整到达客户端。"""
import asyncio
import json
import sys
import pathlib
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import importlib.util

_spec = importlib.util.spec_from_file_location(
    "protocol_bridge", str(pathlib.Path(__file__).resolve().parent.parent
                           / "admin" / "platform" / "protocol_bridge.py"))
bridge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bridge)


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
