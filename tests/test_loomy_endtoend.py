# -*- coding: utf-8 -*-
"""Loomy 网关端到端验证（mock 上游）。

用 ADMIN_LOOMY_BASE_URL 把上游指到本地 mock HTTP 服务，真实走
gateway.messages / gateway.responses 的完整链路（鉴权 mock 掉），
验证：
  1. /v1/messages 流式：tools 定义到达上游、tool_use 块 +
     input_json_delta + stop_reason=tool_use 完整到达客户端；
  2. tool_result（is_error）作为 role=tool 消息到达上游；
  3. 归因头（ChatId/MsgId/TurnId/traceparent）到达上游；
  4. /v1/responses：function_call 输入项转成 role=tool，输出项含
     function_call + response.completed。
"""
import asyncio
import json
import os
import sys
import threading
import pathlib
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

CAPTURED = {}


class MockLoomy(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 静音
        pass

    def _sse(self, chunks):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for c in chunks:
            if c == "[DONE]":
                self.wfile.write(b"data: [DONE]\n\n")
            else:
                self.wfile.write(("data: %s\n\n" % json.dumps(c)).encode())
            self.wfile.flush()

    def do_GET(self):
        if self.path.startswith("/models"):
            body = json.dumps({"object": "list", "data": [
                {"id": "loomy-x", "displayName": "Loomy X", "reasoning_efforts": ["low"]}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/points/balance"):
            body = json.dumps({"code": "000000", "data": {
                "balance": 100, "dailyBalance": 200,
                "availableBalance": 300}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        CAPTURED["path"] = self.path
        CAPTURED["headers"] = {k.lower(): v for k, v in self.headers.items()}
        try:
            CAPTURED["body"] = json.loads(raw)
        except ValueError:
            CAPTURED["body"] = None
        if not self.path.startswith("/chat/completions"):
            self.send_response(404); self.end_headers(); return
        body = CAPTURED["body"] or {}
        if not body.get("stream"):
            # 带 tools 的非流式请求回一个工具调用（模拟上游真实行为），
            # 否则 responses 的 function_call 输出项无从验证
            if body.get("tools"):
                message = {"role": "assistant", "content": None,
                           "tool_calls": [{"id": "c9", "type": "function",
                                           "function": {"name": "read",
                                                        "arguments": "{\"path\": \"/x\"}"}}]}
                finish = "tool_calls"
            else:
                message = {"role": "assistant", "content": "好的"}
                finish = "stop"
            result = {"id": "cmpl-1", "object": "chat.completion",
                      "model": body.get("model"),
                      "choices": [{"index": 0, "message": message,
                                   "finish_reason": finish}],
                      "usage": {"prompt_tokens": 10, "completion_tokens": 2,
                                "total_tokens": 12}}
            payload = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self._sse([
            {"choices": [{"delta": {"reasoning_content": "think"}}]},
            {"choices": [{"delta": {"content": "我来读文件"}}]},
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "c9", "type": "function",
                 "function": {"name": "read", "arguments": "{\"path"}}]}}]},
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": "\":\"/x\"}"}}]}}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
            {"usage": {"prompt_tokens": 50, "completion_tokens": 9,
                       "total_tokens": 59}, "choices": []},
            "[DONE]",
        ])


def start_mock():
    server = ThreadingHTTPServer(("127.0.0.1", 0), MockLoomy)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def make_request(body: bytes):
    from starlette.requests import Request

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {"type": "http", "method": "POST", "path": "/v1/messages",
             "headers": [(b"content-type", b"application/json"),
                         (b"authorization", b"Bearer sk-test")],
             "query_string": b"", "server": ("127.0.0.1", 80)}
    return Request(scope, receive)


def main():
    server = start_mock()
    port = server.server_address[1]
    os.environ["ADMIN_LOOMY_BASE_URL"] = "http://127.0.0.1:%d" % port

    from admin.platform.loomy import gateway
    from admin.platform import shared

    class FakeKey:
        id = 1
        name = "test"

    class FakeAccount:
        id = 7

    shared.authenticate = lambda db, request, platform="": FakeKey()
    shared.locate = lambda request, public: (public, "cn", None)
    shared.select_account = lambda *a, **k: FakeAccount()
    shared.lease = lambda account: True
    shared.release = lambda account: None
    shared.note_success = lambda account: None
    shared.note_failure = lambda *a, **k: None
    shared.key_row = lambda db, request: FakeKey()
    gateway.loomy_accounts.ensure_token = lambda db, account: {"token": "sess"}
    gateway.loomy_accounts.token_of = lambda data: data.get("token") or ""
    # 记账落库 mock 掉（不碰真实 DB）
    gateway._bill = lambda *a, **k: None

    # ---- 1) /v1/messages 流式：工具调用必须完整 ----
    anthropic_body = {
        "model": "loomy-x", "max_tokens": 100, "stream": True,
        "system": "be brief",
        "tools": [{"name": "read", "description": "read",
                   "input_schema": {"type": "object",
                                    "properties": {"path": {"type": "string"}}}}],
        "messages": [
            {"role": "user", "content": "读一下 /x"},
            {"role": "assistant", "content": [
                {"type": "text", "text": "ok"},
                {"type": "tool_use", "id": "t1", "name": "read",
                 "input": {"path": "/x"}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1",
                 "content": "file content", "is_error": True}]},
        ],
    }
    req = make_request(json.dumps(anthropic_body).encode())
    resp = asyncio.run(gateway.messages(req))
    assert resp.status_code == 200, resp.status_code

    async def drain(response):
        out = []
        async for piece in response.body_iterator:
            out.append(piece.decode() if isinstance(piece, bytes) else str(piece))
        return "".join(out)

    text = asyncio.run(drain(resp))
    events = []
    for piece in text.split("\n"):
        piece = piece.strip()
        if piece.startswith("data:"):
            try:
                events.append(json.loads(piece[5:].strip()))
            except ValueError:
                pass

    types = [e.get("type") for e in events if isinstance(e, dict)]
    assert types and types[0] == "message_start", types[:5]
    tool_starts = [e for e in events if e.get("type") == "content_block_start"
                   and e.get("content_block", {}).get("type") == "tool_use"]
    assert tool_starts, "缺 tool_use 块（工具中断 bug 未修复）"
    assert tool_starts[0]["content_block"]["id"] == "c9"
    args = "".join(e["delta"]["partial_json"] for e in events
                   if e.get("type") == "content_block_delta"
                   and e["delta"].get("type") == "input_json_delta")
    assert json.loads(args) == {"path": "/x"}, args
    msg_delta = [e for e in events if e.get("type") == "message_delta"][0]
    assert msg_delta["delta"]["stop_reason"] == "tool_use", msg_delta
    assert types[-1] == "message_stop", types[-3:]

    # ---- 2) 上游侧：tools / 工具历史 / 归因头到没到 ----
    # 2026-11-05 起归一层改为**结构化透传**（与官方 3065 客户端同款行为，
    # 实测上游对 role=tool / assistant.tool_calls 全部 200）：
    # 不再把工具历史压成 "[tool result ...]" / "[上一轮你调用过的工具]" 文本。
    up_body = CAPTURED["body"]
    if os.getenv("E2E_DEBUG"):
        for i, msg in enumerate(up_body["messages"]):
            print("UP[%d] %s: %s" % (i, msg["role"],
                                     (msg.get("content") or "")[:260]))
    assert up_body["tools"], "tools 定义没到上游"
    assert up_body["tools"][0]["function"]["name"] == "read"
    tool_msgs = [m for m in up_body["messages"] if m.get("role") == "tool"]
    assert tool_msgs, "tool 结果消息被丢弃（应结构化透传）"
    assert tool_msgs[0].get("tool_call_id") == "t1", tool_msgs[0]
    assert "file content" in str(tool_msgs[0].get("content")), "工具结果内容丢失"
    assert "[工具执行出错]" in str(tool_msgs[0].get("content")), "is_error 语义丢失"
    asst = [m for m in up_body["messages"]
            if m.get("role") == "assistant"
            and isinstance(m.get("tool_calls"), list)]
    assert asst, "assistant.tool_calls 没有结构化透传"
    fn = asst[-1]["tool_calls"][0].get("function") or {}
    assert fn.get("name") == "read", fn
    assert "path" in str(fn.get("arguments")), fn
    up_headers = CAPTURED["headers"]
    for h in ("authorization", "token", "traceparent", "chatid", "msgid",
              "turnid", "x-loomy-request-purpose"):
        assert h in up_headers, "缺归因/鉴权头 %s：%s" % (h, sorted(up_headers))

    # ---- 3) /v1/responses：function_call 输入项 + 输出项 ----
    CAPTURED.clear()
    responses_body = {
        "model": "loomy-x", "stream": False, "instructions": "sys",
        "input": [
            {"type": "message", "role": "user",
             "content": [{"type": "input_text", "text": "go"}]},
            {"type": "function_call", "call_id": "call_1", "name": "read",
             "arguments": "{}"},
            {"type": "function_call_output", "call_id": "call_1",
             "output": "data"},
        ],
        "tools": [{"type": "function", "name": "read",
                   "parameters": {"type": "object"}}],
        "max_output_tokens": 64,
    }
    req = make_request(json.dumps(responses_body).encode())
    resp = asyncio.run(gateway.responses(req))
    assert resp.status_code == 200
    out = json.loads(resp.body)
    assert out["status"] == "completed", out.get("status")
    fcs = [o for o in out["output"] if o["type"] == "function_call"]
    assert fcs and fcs[0]["name"] == "read", out["output"]
    up_body = CAPTURED["body"]
    tool_msgs2 = [m for m in up_body["messages"] if m.get("role") == "tool"]
    assert tool_msgs2, "responses 输入的 function_call_output 结果没到上游"
    assert any(m.get("tool_call_id") == "call_1" for m in tool_msgs2), tool_msgs2
    assert any("data" in str(m.get("content")) for m in tool_msgs2), tool_msgs2
    assert up_body["messages"][0]["role"] == "system", "instructions 没转 system"
    # loomy 的思考预算下限会放大 max_tokens（≥ 客户端要求值）
    assert up_body["max_tokens"] >= 64
    assert up_body["tools"][0]["function"]["name"] == "read"

    # ---- 4) balance：真积分 ----
    req = make_request(b"")
    req.scope["method"] = "GET"
    resp = asyncio.run(gateway.balance(req))
    bal = json.loads(resp.body)
    assert bal["available"] == 300 and bal["unit"] == "points", bal

    server.shutdown()
    print("E2E-ALL-OK  (messages 工具流 / 归因头 / responses 工具项 / balance 真积分)")


if __name__ == "__main__":
    main()
