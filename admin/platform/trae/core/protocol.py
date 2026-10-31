"""对话协议转换：Trae 信封流 <-> OpenAI Chat / OpenAI Responses / Anthropic Messages。

这是从 trae2api-hub 里**唯一必须保留**的能力：上游只有一种协议
（`llm_raw_chat` 的 SSE 信封流），而客户端有四种说话方式。本模块把上游流
归一成一条内部增量流，再按目标协议渲染 —— 任何协议之间都不需要两两实现。

    iter_deltas()             Trae SSE -> 内部增量（唯一解析上游的地方）
    stream_openai()           增量 -> chat.completion.chunk SSE
    aggregate_openai()        增量 -> 非流式 chat.completion
    responses_to_chat()       Responses 请求 -> Chat 请求
    stream_responses()        增量 -> Responses SSE 事件
    chat_to_responses()       非流式 chat.completion -> Responses 对象
    messages_to_chat()        Anthropic 请求 -> Chat 请求
    stream_anthropic()        增量 -> Anthropic SSE 事件
    chat_to_messages()        非流式 chat.completion -> Anthropic 消息

工具调用：上游 raw chat 没有结构化工具协议，模型把调用写成
`<|FunctionCallBegin|>[{"name":...,"parameters":{...}}]<|FunctionCallEnd|>`。
这里跨 chunk 缓冲解析成真正的 tool_calls；解析失败就原样留在正文里，
一个字都不吞（fail-open）。
"""
from __future__ import annotations

import json
import time
import uuid

from admin.platform.trae.core import client

TOOL_BEGIN = "<|FunctionCallBegin|>"
TOOL_END = "<|FunctionCallEnd|>"
TOOL_BUFFER_LIMIT = 256 * 1024

#: 上游 token_usage -> OpenAI usage（保留原生字段供记账用）。
def usage_to_openai(raw) -> dict:
    u = raw if isinstance(raw, dict) else {}

    def _i(value):
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    prompt = _i(u.get("prompt_tokens"))
    completion = _i(u.get("completion_tokens"))
    total = _i(u.get("total_tokens")) or (prompt + completion)
    cache_read = _i(u.get("cache_read_input_tokens"))
    reasoning = _i(u.get("reasoning_tokens"))
    out = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "prompt_tokens_details": {"cached_tokens": cache_read},
        "completion_tokens_details": {"reasoning_tokens": reasoning},
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": _i(u.get("cache_creation_input_tokens")),
    }
    for key, value in u.items():
        out.setdefault(key, value)
    return out


# ---------------------------------------------------------------------------
# 工具标记解析
# ---------------------------------------------------------------------------
class _ToolMarkerBuffer:
    """跨 chunk 缓冲 `<|FunctionCallBegin|>…<|FunctionCallEnd|>` 并转成 tool_calls。"""

    def __init__(self, allowed: set[str] | None = None):
        self.allowed = allowed
        self._buf = ""
        self.emitted = False
        self._index = 0

    def feed(self, text: str):
        """喂入正文，产出 `("text", s)` 或 `("call", {name, arguments, index})`。"""
        if not text:
            return
        self._buf += text
        while self._buf:
            start = self._buf.find(TOOL_BEGIN)
            if start < 0:
                # 没有起始标记：只把「可能是标记前缀」的尾巴留住
                hold = self._hold_tail(self._buf)
                out, self._buf = self._buf[:len(self._buf) - hold], self._buf[len(self._buf) - hold:]
                if out:
                    yield ("text", out)
                return
            if start > 0:
                head, self._buf = self._buf[:start], self._buf[start:]
                if head:
                    yield ("text", head)
                continue
            end = self._buf.find(TOOL_END)
            if end < 0:
                if len(self._buf) > TOOL_BUFFER_LIMIT:
                    # 明显不是标记：整段当正文吐出，避免无限缓冲
                    out, self._buf = self._buf, ""
                    if out:
                        yield ("text", out)
                return
            body = self._buf[len(TOOL_BEGIN):end]
            self._buf = self._buf[end + len(TOOL_END):]
            calls = self._parse(body)
            if not calls:
                # 解析失败：原样保留，不吞内容
                yield ("text", TOOL_BEGIN + body + TOOL_END)
                continue
            for call in calls:
                self.emitted = True
                yield ("call", call)

    def flush(self):
        if self._buf:
            out, self._buf = self._buf, ""
            if out:
                yield ("text", out)

    @staticmethod
    def _hold_tail(buf: str) -> int:
        """返回需要留待下一 chunk 校验的尾长（标记前缀可能被截断）。"""
        limit = min(len(buf), len(TOOL_BEGIN) - 1)
        for size in range(limit, 0, -1):
            if TOOL_BEGIN.startswith(buf[-size:]):
                return size
        return 0

    def _parse(self, body: str):
        body = body.strip()
        try:
            parsed = json.loads(body)
        except ValueError:
            return []
        if isinstance(parsed, dict):
            parsed = [parsed]
        if not isinstance(parsed, list):
            return []
        out = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("function") or "").strip()
            if not name:
                continue
            if self.allowed and name not in self.allowed:
                continue
            args = item.get("parameters")
            if args is None:
                args = item.get("arguments", {})
            if not isinstance(args, str):
                args = json.dumps(args, ensure_ascii=False)
            out.append({"index": self._index, "name": name, "arguments": args})
            self._index += 1
        return out


# ---------------------------------------------------------------------------
# 上游信封流 -> 内部增量
# ---------------------------------------------------------------------------
def iter_deltas(resp, holder: dict | None = None, allowed_names=None):
    """把 Trae 的 SSE 信封流归一成内部增量。

    产出 `(kind, payload)`：
        ("role", "")                  会话开始
        ("text", "...")               正文增量
        ("reasoning", "...")          思考增量
        ("usage", {...})              OpenAI 形态 usage
        ("quota", {...})              上游额度快照（notify_usage）
        ("finish", "stop"|"tool_calls")
    上游 `event: error` 直接抛 `UpstreamError`（调用方决定换号还是回错）。
    """
    holder = holder if isinstance(holder, dict) else {}
    stream = _ToolMarkerBuffer(set(allowed_names) if allowed_names else None)
    meta = {"id": "chatcmpl-trae-" + uuid.uuid4().hex[:24], "model": ""}
    holder.setdefault("meta", meta)
    started = False
    finished = False

    for event, data in client.iter_events(resp, idle_timeout=holder.get("idle_timeout", 180)):
        obj = data if isinstance(data, dict) else {}
        if event == "metadata":
            session_id = str(obj.get("session_id") or "")
            if session_id:
                meta["id"] = "chatcmpl-" + session_id.replace("-", "")[:24]
            model_name = str(obj.get("model") or "")
            if model_name:
                meta["model"] = model_name
            if not started:
                started = True
                yield ("role", "")
            continue
        if event in ("output", "message"):
            if not started:
                started = True
                yield ("role", "")
            reasoning = obj.get("reasoning_content")
            if isinstance(reasoning, str) and reasoning:
                yield ("reasoning", reasoning)
            native = obj.get("tool_calls")
            if isinstance(native, list):
                for index, call in enumerate(native):
                    if not isinstance(call, dict):
                        continue
                    fn = call.get("function") if isinstance(call.get("function"), dict) else call
                    name = str(fn.get("name") or "").strip()
                    if not name:
                        continue
                    args = fn.get("arguments")
                    if not isinstance(args, str):
                        args = json.dumps(args or {}, ensure_ascii=False)
                    stream.emitted = True
                    yield ("call", {"index": index, "name": name, "arguments": args})
            text = obj.get("response")
            if isinstance(text, str) and text:
                for kind, payload in stream.feed(text):
                    yield (kind, payload)
            continue
        if event == "notify_usage":
            holder["quota"] = obj
            yield ("quota", obj)
            continue
        if event in ("token_usage", "usage"):
            usage = usage_to_openai(obj)
            holder["usage"] = usage
            yield ("usage", usage)
            continue
        if event == "done":
            if not started:
                started = True
                yield ("role", "")
            for kind, payload in stream.flush():
                yield (kind, payload)
            reason = str(obj.get("finish_reason") or "stop") or "stop"
            if stream.emitted:
                reason = "tool_calls"
            finished = True
            yield ("finish", reason)
            return
        if event == "error":
            raise client.error_from_code(obj.get("code"), obj.get("message"),
                                         obj.get("error"))
        # keepalive / phase / 未知事件：忽略，不制造空帧

    # 流提前结束（缺 done 帧）：flush 并给出终态，客户端不会挂死
    if not started:
        yield ("role", "")
    for kind, payload in stream.flush():
        yield (kind, payload)
    if not finished:
        yield ("finish", "tool_calls" if stream.emitted else "stop")


# ---------------------------------------------------------------------------
# OpenAI Chat Completions
# ---------------------------------------------------------------------------
def _chunk(meta: dict, created: int, delta: dict | None,
           finish_reason=None, usage=None) -> bytes:
    """合成一帧 `chat.completion.chunk`（沿用上游的 id / model）。"""
    payload = {
        "id": meta["id"],
        "object": "chat.completion.chunk",
        "created": created,
        "model": meta.get("model") or "",
        "choices": [],
    }
    if delta is not None:
        payload["choices"] = [{"index": 0, "delta": delta,
                               "finish_reason": finish_reason}]
    if usage:
        payload["usage"] = usage
    return ("data: " + json.dumps(payload, ensure_ascii=False) + "\n\n").encode("utf-8")


def stream_openai(resp, holder=None, allowed_names=None):
    """Trae SSE -> `chat.completion.chunk` 的 SSE 字节流。"""
    holder = holder if isinstance(holder, dict) else {}
    created = int(time.time())
    meta = holder.setdefault("meta", {"id": "chatcmpl-trae-" + uuid.uuid4().hex[:24],
                                      "model": holder.get("requested_model") or ""})
    for kind, payload in iter_deltas(resp, holder, allowed_names):
        if kind == "role":
            yield _chunk(meta, created, {"role": "assistant", "content": ""})
        elif kind == "text":
            yield _chunk(meta, created, {"content": payload})
        elif kind == "reasoning":
            yield _chunk(meta, created, {"reasoning_content": payload})
        elif kind == "call":
            yield _chunk(meta, created, {"tool_calls": [{
                "index": payload["index"], "id": "call_%d" % payload["index"],
                "type": "function",
                "function": {"name": payload["name"], "arguments": payload["arguments"]},
            }]})
        elif kind == "usage":
            yield _chunk(meta, created, None, usage=payload)
        elif kind == "finish":
            yield _chunk(meta, created, {}, finish_reason=payload)
    yield b"data: [DONE]\n\n"


def aggregate_openai(resp, holder=None, allowed_names=None) -> dict:
    """Trae SSE -> 非流式 `chat.completion` 对象。"""
    holder = holder if isinstance(holder, dict) else {}
    text, reasoning, finish = [], [], "stop"
    calls: dict[int, dict] = {}
    created = int(time.time())
    for kind, payload in iter_deltas(resp, holder, allowed_names):
        if kind == "text":
            text.append(payload)
        elif kind == "reasoning":
            reasoning.append(payload)
        elif kind == "call":
            calls.setdefault(payload["index"], {
                "id": "call_%d" % payload["index"], "type": "function",
                "function": {"name": payload["name"], "arguments": payload["arguments"]},
            })
        elif kind == "finish":
            finish = payload
    meta = holder.get("meta") or {}
    message = {"role": "assistant", "content": "".join(text) or None}
    if reasoning:
        message["reasoning_content"] = "".join(reasoning)
    if calls:
        message["tool_calls"] = [calls[k] for k in sorted(calls)]
        message["content"] = "".join(text) or None
        finish = "tool_calls"
    return {
        "id": meta.get("id") or ("chatcmpl-trae-" + uuid.uuid4().hex[:24]),
        "object": "chat.completion",
        "created": created,
        "model": meta.get("model") or holder.get("requested_model") or "",
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": holder.get("usage") or {},
    }


# ---------------------------------------------------------------------------
# OpenAI Responses
# ---------------------------------------------------------------------------
def _flatten_content(content) -> tuple[str, list[dict]]:
    """Responses 的 content（字符串或 parts 列表）-> (纯文本, 图片 part)。"""
    if content is None:
        return "", []
    if isinstance(content, str):
        return content, []
    if isinstance(content, dict):
        content = [content]
    texts, images = [], []
    for part in content if isinstance(content, (list, tuple)) else []:
        if isinstance(part, str):
            texts.append(part)
            continue
        if not isinstance(part, dict):
            continue
        kind = str(part.get("type") or "")
        if kind in ("input_text", "output_text", "text", "summary_text"):
            texts.append(str(part.get("text") or ""))
        elif kind == "input_image":
            url = part.get("image_url")
            if isinstance(url, dict):
                url = url.get("url")
            if url:
                images.append({"type": "image_url",
                               "image_url": {"url": str(url)}})
        elif kind == "refusal":
            texts.append(str(part.get("refusal") or ""))
    return "".join(texts), images


def responses_to_chat(payload: dict) -> dict:
    """OpenAI Responses 请求 -> Chat Completions 请求。

    Codex / Claude Code 走这条路。关键映射：
      · `input`（字符串或多轮 item 列表）-> `messages`
      · `instructions` -> system
      · `max_output_tokens` -> `max_tokens`
      · `reasoning.effort` -> `reasoning_effort`
    """
    payload = payload if isinstance(payload, dict) else {}
    messages: list[dict] = []
    instructions = payload.get("instructions")
    if isinstance(instructions, str) and instructions.strip():
        messages.append({"role": "system", "content": instructions})

    raw_input = payload.get("input")
    if isinstance(raw_input, str):
        messages.append({"role": "user", "content": raw_input})
    elif isinstance(raw_input, list):
        for item in raw_input:
            if isinstance(item, str):
                messages.append({"role": "user", "content": item})
                continue
            if not isinstance(item, dict):
                continue
            kind = str(item.get("type") or "")
            if kind in ("function_call", "function_call_output"):
                # 工具历史在 Chat 侧没有等价结构，转成可读文本，避免丢上下文。
                name = str(item.get("name") or "tool")
                body = item.get("arguments") or item.get("output") or ""
                if not isinstance(body, str):
                    body = json.dumps(body, ensure_ascii=False)
                role = "assistant" if kind == "function_call" else "user"
                messages.append({"role": role,
                                 "content": "[%s] %s" % (name, body)})
                continue
            role = str(item.get("role") or "user").strip().lower()
            if role == "developer":
                role = "system"
            if role not in ("system", "user", "assistant", "tool"):
                role = "user"
            text, images = _flatten_content(item.get("content"))
            if role == "tool":
                # Responses 的 tool 结果 -> assistant 可读文本
                messages.append({"role": "user", "content": text or ""})
                continue
            if images and role == "user":
                parts = [{"type": "text", "text": text}] if text else []
                messages.append({"role": role, "content": parts + images})
            else:
                messages.append({"role": role, "content": text})

    if not messages:
        messages.append({"role": "user", "content": ""})

    chat = {"model": payload.get("model") or "", "messages": messages}
    if payload.get("stream"):
        chat["stream"] = True
    max_out = payload.get("max_output_tokens")
    if max_out:
        chat["max_tokens"] = max_out
    reasoning = payload.get("reasoning")
    if isinstance(reasoning, dict) and reasoning.get("effort"):
        chat["reasoning_effort"] = reasoning["effort"]
    for key in ("temperature", "top_p"):
        if payload.get(key) is not None:
            chat[key] = payload[key]

    tools = []
    for tool in payload.get("tools") or ():
        if not isinstance(tool, dict) or tool.get("type") != "function":
            continue
        fn = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        name = str(fn.get("name") or "").strip()
        if not name:
            continue
        tools.append({"type": "function", "function": {
            "name": name,
            "description": str(fn.get("description") or ""),
            "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
        }})
    if tools:
        chat["tools"] = tools
        choice = payload.get("tool_choice")
        if choice in ("auto", "required", "none"):
            chat["tool_choice"] = choice
    return chat


def chat_to_responses(chat: dict, model: str, request_id: str = "") -> dict:
    """非流式 chat.completion -> Responses 对象。"""
    chat = chat if isinstance(chat, dict) else {}
    choice = (chat.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    output = []
    text = message.get("content")
    if isinstance(text, str) and text:
        output.append({
            "type": "message", "id": "msg_" + uuid.uuid4().hex[:24],
            "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": text, "annotations": []}],
        })
    for index, call in enumerate(message.get("tool_calls") or ()):
        fn = call.get("function") if isinstance(call.get("function"), dict) else {}
        output.append({
            "type": "function_call",
            "id": "fc_" + uuid.uuid4().hex[:24],
            "call_id": call.get("id") or ("call_%d" % index),
            "name": str(fn.get("name") or ""),
            "arguments": str(fn.get("arguments") or "{}"),
            "status": "completed",
        })
    usage = chat.get("usage") or {}
    return {
        "id": request_id or ("resp_" + uuid.uuid4().hex[:24]),
        "object": "response",
        "created_at": int(chat.get("created") or time.time()),
        "status": "completed",
        "model": model or chat.get("model") or "",
        "output": output,
        "usage": {
            "input_tokens": int(usage.get("prompt_tokens") or 0),
            "output_tokens": int(usage.get("completion_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
        },
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
    }


def stream_responses(resp, model: str, holder=None, allowed_names=None,
                     response_id: str = ""):
    """Trae SSE -> Responses API 事件流。"""
    holder = holder if isinstance(holder, dict) else {}
    rid = response_id or ("resp_" + uuid.uuid4().hex[:24])
    created = int(time.time())
    seq = 0
    started = False
    item_id = "msg_" + uuid.uuid4().hex[:24]

    def event(name: str, payload: dict) -> bytes:
        nonlocal seq
        seq += 1
        payload = {"type": name, "sequence_number": seq, **payload}
        return ("event: %s\ndata: %s\n\n" % (name, json.dumps(payload, ensure_ascii=False))
                ).encode("utf-8")

    yield event("response.created", {"response": {
        "id": rid, "object": "response", "created_at": created, "status": "in_progress",
        "model": model, "output": []}})
    for kind, payload in iter_deltas(resp, holder, allowed_names):
        if kind == "role":
            if started:
                continue
            started = True
            yield event("response.output_item.added", {
                "output_index": 0,
                "item": {"type": "message", "id": item_id, "role": "assistant",
                         "status": "in_progress", "content": []}})
            yield event("response.content_part.added", {
                "item_id": item_id, "output_index": 0, "content_index": 0,
                "part": {"type": "output_text", "text": "", "annotations": []}})
        elif kind == "text":
            yield event("response.output_text.delta", {
                "item_id": item_id, "output_index": 0, "content_index": 0,
                "delta": payload})
        elif kind == "reasoning":
            yield event("response.reasoning_summary_text.delta", {
                "item_id": item_id, "output_index": 0, "summary_index": 0,
                "delta": payload})
        elif kind == "call":
            call_id = "call_%s" % uuid.uuid4().hex[:16]
            yield event("response.output_item.added", {
                "output_index": payload["index"] + 1,
                "item": {"type": "function_call", "id": "fc_" + uuid.uuid4().hex[:24],
                         "call_id": call_id, "name": payload["name"],
                         "arguments": "", "status": "in_progress"}})
            yield event("response.function_call_arguments.delta", {
                "item_id": call_id, "output_index": payload["index"] + 1,
                "delta": payload["arguments"]})
        elif kind == "usage":
            pass  # 终态统一带 usage，避免中途重复
    usage = holder.get("usage") or {}
    yield event("response.completed", {"response": {
        "id": rid, "object": "response", "created_at": created, "status": "completed",
        "model": model, "output": [],
        "usage": {
            "input_tokens": int(usage.get("prompt_tokens") or 0),
            "output_tokens": int(usage.get("completion_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
        }}})
    yield b"data: [DONE]\n\n"


# ---------------------------------------------------------------------------
# Anthropic Messages
# ---------------------------------------------------------------------------
def messages_to_chat(payload: dict) -> dict:
    """Anthropic Messages 请求 -> Chat Completions 请求。"""
    payload = payload if isinstance(payload, dict) else {}
    messages: list[dict] = []

    system = payload.get("system")
    if system:
        text, _ = _flatten_content(system)
        if text.strip():
            messages.append({"role": "system", "content": text})

    for item in payload.get("messages") or ():
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "user").strip().lower()
        if role not in ("user", "assistant"):
            role = "user"
        content = item.get("content")
        texts, images = [], []
        if isinstance(content, str):
            texts.append(content)
        else:
            for part in content if isinstance(content, (list, tuple)) else []:
                if isinstance(part, str):
                    texts.append(part)
                    continue
                if not isinstance(part, dict):
                    continue
                kind = str(part.get("type") or "")
                if kind == "text":
                    texts.append(str(part.get("text") or ""))
                elif kind == "image":
                    source = part.get("source") or {}
                    if isinstance(source, dict) and source.get("type") == "base64":
                        url = "data:%s;base64,%s" % (
                            source.get("media_type") or "image/png",
                            source.get("data") or "")
                        images.append({"type": "image_url", "image_url": {"url": url}})
                elif kind == "tool_use":
                    texts.append("[tool_call %s] %s" % (
                        part.get("name") or "",
                        json.dumps(part.get("input") or {}, ensure_ascii=False)))
                elif kind == "tool_result":
                    inner, _ = _flatten_content(part.get("content"))
                    texts.append(str(inner or "")[:4000])
        text = "".join(texts)
        if images and role == "user":
            parts = [{"type": "text", "text": text}] if text else []
            messages.append({"role": role, "content": parts + images})
        else:
            messages.append({"role": role, "content": text})

    if not messages:
        messages.append({"role": "user", "content": ""})

    chat = {"model": payload.get("model") or "", "messages": messages}
    if payload.get("stream"):
        chat["stream"] = True
    if payload.get("max_tokens"):
        chat["max_tokens"] = payload["max_tokens"]
    for key in ("temperature", "top_p"):
        if payload.get(key) is not None:
            chat[key] = payload[key]
    if payload.get("stop_sequences"):
        chat["stop"] = payload["stop_sequences"]

    tools = []
    for tool in payload.get("tools") or ():
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name") or "").strip()
        if not name:
            continue
        tools.append({"type": "function", "function": {
            "name": name,
            "description": str(tool.get("description") or ""),
            "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
        }})
    if tools:
        chat["tools"] = tools
    return chat


_STOP_REASON = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "tool_use": "tool_use",
    "content_filter": "end_turn",
}


def chat_to_messages(chat: dict, model: str = "") -> dict:
    """非流式 chat.completion -> Anthropic message。"""
    chat = chat if isinstance(chat, dict) else {}
    choice = (chat.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    blocks = []
    text = message.get("content")
    if isinstance(text, str) and text:
        blocks.append({"type": "text", "text": text})
    for call in message.get("tool_calls") or ():
        fn = call.get("function") if isinstance(call.get("function"), dict) else {}
        try:
            parsed = json.loads(fn.get("arguments") or "{}")
        except ValueError:
            parsed = {}
        blocks.append({
            "type": "tool_use",
            "id": call.get("id") or ("toolu_" + uuid.uuid4().hex[:20]),
            "name": str(fn.get("name") or ""),
            "input": parsed if isinstance(parsed, dict) else {},
        })
    if not blocks:
        blocks.append({"type": "text", "text": ""})
    usage = chat.get("usage") or {}
    return {
        "id": "msg_" + uuid.uuid4().hex[:24],
        "type": "message",
        "role": "assistant",
        "model": model or chat.get("model") or "",
        "content": blocks,
        "stop_reason": _STOP_REASON.get(choice.get("finish_reason") or "stop", "end_turn"),
        "stop_sequence": None,
        "usage": {
            "input_tokens": int(usage.get("prompt_tokens") or 0),
            "output_tokens": int(usage.get("completion_tokens") or 0),
        },
    }


def stream_anthropic(resp, model: str = "", holder=None, allowed_names=None):
    """Trae SSE -> Anthropic Messages 事件流。"""
    holder = holder if isinstance(holder, dict) else {}
    msg_id = "msg_" + uuid.uuid4().hex[:24]
    seq = 0
    block_index = 0
    text_open = False

    def event(name: str, payload: dict) -> bytes:
        nonlocal seq
        seq += 1
        payload = {"type": name, **payload}
        return ("event: %s\ndata: %s\n\n" % (name, json.dumps(payload, ensure_ascii=False))
                ).encode("utf-8")

    yield event("message_start", {"message": {
        "id": msg_id, "type": "message", "role": "assistant", "model": model,
        "content": [], "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": 0, "output_tokens": 0}}})
    for kind, payload in iter_deltas(resp, holder, allowed_names):
        if kind == "role":
            if not text_open:
                text_open = True
                yield event("content_block_start", {
                    "index": block_index,
                    "content_block": {"type": "text", "text": ""}})
        elif kind == "text":
            if not text_open:
                text_open = True
                yield event("content_block_start", {
                    "index": block_index,
                    "content_block": {"type": "text", "text": ""}})
            yield event("content_block_delta", {
                "index": block_index,
                "delta": {"type": "text_delta", "text": payload}})
        elif kind == "reasoning":
            if not text_open:
                text_open = True
                yield event("content_block_start", {
                    "index": block_index,
                    "content_block": {"type": "text", "text": ""}})
            yield event("content_block_delta", {
                "index": block_index,
                "delta": {"type": "thinking_delta", "thinking": payload}})
        elif kind == "call":
            if text_open:
                yield event("content_block_stop", {"index": block_index})
                block_index += 1
                text_open = False
            yield event("content_block_start", {
                "index": block_index,
                "content_block": {"type": "tool_use",
                                  "id": "toolu_" + uuid.uuid4().hex[:20],
                                  "name": payload["name"], "input": {}}})
            yield event("content_block_delta", {
                "index": block_index,
                "delta": {"type": "input_json_delta",
                          "partial_json": payload["arguments"]}})
            yield event("content_block_stop", {"index": block_index})
            block_index += 1
    if text_open:
        yield event("content_block_stop", {"index": block_index})
    usage = holder.get("usage") or {}
    stop_reason = "tool_use" if block_index > 1 else "end_turn"
    # Anthropic usage 必须单列缓存命中，否则客户端命中率恒为 0。
    _a_u = None
    try:
        from upstream_compat import anthropic_usage_from

        _a_u = anthropic_usage_from(usage)
    except Exception:  # noqa: BLE001
        _a_u = None
    if _a_u is None:
        _a_u = {"input_tokens": int(usage.get("prompt_tokens") or 0),
                "output_tokens": int(usage.get("completion_tokens") or 0),
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0}
    yield event("message_delta", {
        "delta": {"stop_reason": stop_reason, "stop_sequence": None},
        "usage": _a_u})
    yield event("message_stop", {})


def anthropic_error(message: str, kind: str = "api_error") -> dict:
    return {"type": "error", "error": {"type": kind, "message": message}}