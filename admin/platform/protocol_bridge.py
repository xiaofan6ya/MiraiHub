"""平台协议桥：三协议（OpenAI / Anthropic / Responses）工具调用的统一转换层。

## 为什么必须有这个模块

loomy / raccoon / qoder 各自拷贝了一份「Anthropic ↔ OpenAI」与
「Responses ↔ OpenAI」转换器，且**各自漏的部位还不一样**：

  * loomy      `/v1/messages`：请求方向丢 `tools`/`tool_use`/`tool_result`；
               流式方向把 `delta.tool_calls` 静默丢弃、stop_reason 恒
               `end_turn` —— 客户端（Claude Code 系）在执行工具的那一轮
               收到「助手结束回合但没有调用」，表现就是**任务跑到工具
               那一步突然终止**；
  * raccoon    请求方向与非流式响应是完整的，但流式中继同样丢工具、
               丢思考块、吞错误帧；
  * qoder      请求方向直接把 Anthropic body 原样塞给上游（上游是
               OpenAI 形态，工具定义与 tool_result 全部无效）；
               流式只翻 text/thinking。

统一收敛到这里，修一处、四家生效。

## 内容

    请求方向
        anthropic_to_openai(body)        Anthropic 请求 → OpenAI 请求
        responses_to_openai(body)        Responses 请求 → OpenAI 请求

    非流式响应
        openai_to_anthropic(result, model)
        openai_to_responses(result, model)

    流式中继（inner 必须是产 OpenAI chat.completion.chunk SSE 的
    StreamingResponse —— loomy/raccoon/qoder 的 `_forward_stream` 都满足）
        anthropic_stream_relay(inner, model, prefix=...)
        responses_stream_relay(inner, model, prefix=...)

    心跳（「上游慢/排队时整条流静默」的可见性修复）
        with_pings(source, interval=, ping=)   同步流空闲心跳（源侧注入）
        CHAT_PING / RESPONSES_PING / ANTHROPIC_PING

## 心跳为什么在「源」注入而不是在中继定时器里

流式读取的阻塞点在内层同步生成器（socket read）。若在中继用
`asyncio.wait_for(__anext__(), timeout)` 实现定时 ping，超时会**取消**
`__anext__()`，而 anyio 的线程池任务无法真正中断 —— 下一次 `__anext__`
又会再起一个线程去 `next()` 同一个生成器，两个线程并发消费同一条流，
行会被撕碎。`with_pings` 用「读线程 + 队列」把阻塞搬进独立线程，
主消费线程只做 `queue.get(timeout)`，超时 yield 一帧 ping ——
单消费者、零竞态，同步生成器原样可用。

## 工具调用的关键协议约束（都是实测踩过的）

  * OpenAI：`finish_reason="tool_calls"` 必须存在，`arguments` 是
    多帧拼接的字符串；`index` 标识并行调用槽位。
  * Anthropic：工具调用是 `tool_use` **内容块**（content_block_start →
    input_json_delta → content_block_stop），stop_reason 必须是
    `"tool_use"`；tool_result 是 **user 消息里的块**，转到 OpenAI 必须
    拆成独立的 `role="tool"` 消息，否则上游看不到结果、会重复调用。
  * Responses：工具调用是 `function_call` **输出项**（output_item.added
    → function_call_arguments.delta → output_item.done），输入侧的
    `function_call_output` 要转成 `role="tool"` 消息。

## 与平台特有归一的关系

桥产出的是**协议级正确**的 OpenAI 请求（role="tool"、tool_calls 完整）。
各平台自己的怪癖归一（比如 loomy 上游只认 system/user/assistant 三角色）
在平台网关里继续做 —— 两层职责分离，桥不感知平台。
"""
from __future__ import annotations

import json
import queue
import threading
import time
import uuid

from starlette.responses import StreamingResponse

# ---------------------------------------------------------------------------
# 心跳
# ---------------------------------------------------------------------------
#: OpenAI chat / Responses 的 SSE 注释帧（所有标准 SSE 解析器都忽略）
CHAT_PING = b": ping\n\n"
RESPONSES_PING = b": ping\n\n"
#: Anthropic 的 ping 事件（官方事件，SDK 会触发 message 上的 ping 回调）
_ANTHROPIC_PING_PAYLOAD = json.dumps({"type": "ping"}, separators=(",", ":"))
ANTHROPIC_PING = ("event: ping\ndata: %s\n\n" % _ANTHROPIC_PING_PAYLOAD).encode()

#: 空闲多久发一帧心跳。上游排队/限流/慢思考时，客户端至少每 8 秒
#: 看到一次「链路活着」，不再表现为「卡死」。
PING_INTERVAL_S = 8.0


def with_pings(source, *, interval: float = PING_INTERVAL_S,
               ping: bytes = CHAT_PING):
    """给**同步**迭代器加空闲心跳（源侧注入）。

    读源搬进独立线程，主线程 `queue.get(timeout)`：超过 `interval`
    没有新条目就 yield 一帧 `ping`。异常与结束都会正确传播；
    单消费者，不存在并发 `next()` 撕裂流的问题。
    """
    box: "queue.SimpleQueue" = queue.SimpleQueue()
    sentinel = object()

    def _worker():
        try:
            for item in source:
                box.put(item)
        except BaseException as exc:  # noqa: BLE001 - 异常也要交给消费线程
            box.put(exc)
        finally:
            box.put(sentinel)

    threading.Thread(target=_worker, daemon=True,
                     name="sse-ping-bridge").start()

    while True:
        try:
            item = box.get(timeout=max(0.05, float(interval)))
        except queue.Empty:
            yield ping
            continue
        if item is sentinel:
            return
        if isinstance(item, BaseException):
            raise item
        yield item


# ---------------------------------------------------------------------------
# Anthropic 请求 → OpenAI 请求
# ---------------------------------------------------------------------------
def _tool_result_text(content) -> str:
    """Anthropic tool_result 的 content → 纯文本。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text") or ""))
                elif block.get("type") == "image":
                    parts.append("[图片结果，已省略]")
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return str(content)


def anthropic_tools_to_openai(raw) -> list[dict]:
    """Anthropic `tools` → OpenAI `tools`。

    Anthropic 是扁平定义（name/input_schema），OpenAI 嵌在 `function`
    里且键名是 `parameters`。漏了这步的后果不是报错，而是**工具彻底
    发不出去**：上游永远不回 tool_calls，任务直接卡死。
    """
    out: list[dict] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        schema = item.get("input_schema")
        if not isinstance(schema, dict):
            schema = {"type": "object", "properties": {}}
        fn: dict = {"name": name, "parameters": schema}
        desc = item.get("description")
        if isinstance(desc, str) and desc.strip():
            fn["description"] = desc
        out.append({"type": "function", "function": fn})
    return out


def anthropic_tool_choice_to_openai(raw):
    """Anthropic `tool_choice` → OpenAI `tool_choice`。"""
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("type") or "").strip().lower()
    if kind == "auto":
        return "auto"
    if kind == "any":
        return "required"
    if kind == "none":
        return "none"
    if kind == "tool" and raw.get("name"):
        return {"type": "function", "function": {"name": raw["name"]}}
    return None


def _anthropic_image_url(source: dict) -> str | None:
    """Anthropic image source → OpenAI image_url.url。"""
    if not isinstance(source, dict):
        return None
    if source.get("type") == "url" and source.get("url"):
        return str(source["url"])
    if source.get("type") == "base64" and source.get("data"):
        media = str(source.get("media_type") or "image/png")
        return "data:%s;base64,%s" % (media, source["data"])
    return None


def anthropic_to_openai(body: dict) -> dict:
    """Anthropic 请求体 → OpenAI 请求体（工具双向全量映射）。

    ## 为什么 tool_result 必须拆成独立 role=tool 消息

    Anthropic 把工具结果作为 **user 消息里的 tool_result 块**回传；
    OpenAI 语义里它是一条独立的 `role="tool"` 消息。只把文本抽出来拼回
    user 消息的话，上游看到的是「一段孤立的粘贴文本」，
    不知道它对应哪次调用 → 重复调用同一个工具，或干脆把参数写成散文。
    """
    messages: list[dict] = []
    system = body.get("system")
    if isinstance(system, str) and system.strip():
        messages.append({"role": "system", "content": system})
    elif isinstance(system, list):
        text = "".join(str(b.get("text") or "") for b in system
                       if isinstance(b, dict) and b.get("type") == "text")
        if text.strip():
            messages.append({"role": "system", "content": text})

    for item in body.get("messages") or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        content = item.get("content")

        if isinstance(content, list):
            texts: list[str] = []
            images: list[dict] = []
            tool_uses: list[dict] = []
            tool_results: list[dict] = []
            for block in content:
                if isinstance(block, str):
                    texts.append(block)
                    continue
                if not isinstance(block, dict):
                    continue
                btype = str(block.get("type") or "")
                if btype == "text":
                    texts.append(str(block.get("text") or ""))
                elif btype == "image":
                    url = _anthropic_image_url(block.get("source"))
                    if url:
                        images.append({"type": "image_url",
                                       "image_url": {"url": url}})
                elif btype == "tool_use":
                    # assistant 发起的工具调用 → OpenAI tool_calls
                    args = block.get("input")
                    tool_uses.append({
                        "id": str(block.get("id") or ("call_%d" % len(tool_uses))),
                        "type": "function",
                        "function": {
                            "name": str(block.get("name") or "unknown"),
                            "arguments": json.dumps(
                                args if isinstance(args, dict) else {},
                                ensure_ascii=False),
                        },
                    })
                elif btype == "tool_result":
                    raw = block.get("content")
                    txt = _tool_result_text(raw)
                    if block.get("is_error"):
                        txt = ("[工具执行出错]\n" + txt).strip()
                    tool_results.append({
                        "role": "tool",
                        "tool_call_id": str(block.get("tool_use_id") or ""),
                        "content": txt,
                    })
                # thinking / redacted_thinking / server_tool_use 等块：
                # 上游不可复用，丢弃（下一轮历史里保留会破坏上游配对）。
            text = "".join(texts)
            if tool_results and role == "user":
                # 工具结果先走；该消息里同时带的正文作为一条 user 消息
                if text.strip() or images:
                    user_msg: dict = {"role": "user", "content": text or None}
                    if images:
                        if not isinstance(user_msg["content"], list):
                            user_msg["content"] = (
                                [{"type": "text", "text": text}]
                                if text else [])
                        user_msg["content"].extend(images)
                    messages.append(user_msg)
                messages.extend(tool_results)
                continue
            if tool_uses:
                msg: dict = {"role": "assistant" if role != "user" else "assistant"}
                msg["content"] = text if text.strip() else None
                msg["tool_calls"] = tool_uses
                messages.append(msg)
                continue
            if text.strip() or images:
                if images:
                    parts = ([{"type": "text", "text": text}] if text else [])
                    parts.extend(images)
                    messages.append({"role": role or "user", "content": parts})
                else:
                    messages.append({"role": role or "user", "content": text})
            continue

        if role and content is not None:
            messages.append({"role": role, "content": content})

    out = {"model": body.get("model"), "messages": messages,
           "max_tokens": body.get("max_tokens") or 4096}
    for key in ("temperature", "top_p", "stream", "metadata"):
        if key in body:
            out[key] = body[key]
    if "stop_sequences" in body and body.get("stop_sequences"):
        out["stop"] = body["stop_sequences"]

    tools = anthropic_tools_to_openai(body.get("tools"))
    if tools:
        out["tools"] = tools
    choice = anthropic_tool_choice_to_openai(body.get("tool_choice"))
    if choice is not None:
        out["tool_choice"] = choice
    return out


# ---------------------------------------------------------------------------
# Responses 请求 → OpenAI 请求
# ---------------------------------------------------------------------------
def _responses_tool_to_openai(item) -> dict | None:
    """Responses 工具定义 → OpenAI 工具定义。非 function 类型丢弃。"""
    if not isinstance(item, dict):
        return None
    if str(item.get("type") or "function") != "function":
        # web_search / computer_use / custom 等上游 chat 协议无法承载
        return None
    name = str(item.get("name") or "").strip()
    if not name:
        return None
    fn: dict = {"name": name}
    if isinstance(item.get("parameters"), dict):
        fn["parameters"] = item["parameters"]
    if isinstance(item.get("description"), str) and item["description"].strip():
        fn["description"] = item["description"]
    if "strict" in item:
        fn["strict"] = bool(item["strict"])
    return {"type": "function", "function": fn}


def responses_input_to_messages(items) -> list[dict]:
    """Responses `input`（列表形态）→ OpenAI messages。

    关键是两种工具项：

        {"type":"function_call", "call_id", "name", "arguments"}
            → assistant 消息的 tool_calls
        {"type":"function_call_output", "call_id", "output"}
            → role="tool" 消息

    漏了前者，模型看不见自己上一轮调了什么；漏了后者，模型看不见
    工具结果 —— 两种都会让 Codex 系客户端的工具循环中断。
    """
    messages: list[dict] = []
    if isinstance(items, str):
        return [{"role": "user", "content": items}]
    if not isinstance(items, list):
        return []
    for item in items:
        if isinstance(item, str):
            messages.append({"role": "user", "content": item})
            continue
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "message")
        if kind == "message":
            role = str(item.get("role") or "user").lower()
            content = item.get("content")
            if isinstance(content, str):
                messages.append({"role": role, "content": content})
                continue
            texts: list[str] = []
            images: list[dict] = []
            for block in content or []:
                if isinstance(block, str):
                    texts.append(block)
                    continue
                if not isinstance(block, dict):
                    continue
                btype = str(block.get("type") or "")
                if btype in ("input_text", "output_text", "text"):
                    texts.append(str(block.get("text") or ""))
                elif btype in ("input_image", "image_url"):
                    url = block.get("image_url") or block.get("url")
                    if isinstance(url, str) and url:
                        images.append({"type": "image_url",
                                       "image_url": {"url": url}})
            if images:
                parts = ([{"type": "text", "text": "".join(texts)}]
                         if texts else [])
                parts.extend(images)
                messages.append({"role": role, "content": parts})
            elif texts:
                messages.append({"role": role, "content": "".join(texts)})
            continue
        if kind == "function_call":
            messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": str(item.get("call_id") or item.get("id")
                               or ("call_%d" % len(messages))),
                    "type": "function",
                    "function": {
                        "name": str(item.get("name") or "unknown"),
                        "arguments": str(item.get("arguments") or "{}"),
                    },
                }],
            })
            continue
        if kind == "function_call_output":
            out = item.get("output")
            if isinstance(out, dict):
                out = out.get("output") or json.dumps(out, ensure_ascii=False)
            if isinstance(out, list):
                out = "".join(
                    str(b.get("text") or "") if isinstance(b, dict) else str(b)
                    for b in out)
            messages.append({
                "role": "tool",
                "tool_call_id": str(item.get("call_id") or ""),
                "content": "" if out is None else str(out),
            })
            continue
        # reasoning / local_shell_call / web_search_call / item_reference 等：
        # 上游 chat 协议不可承载，丢弃。
    return messages


def responses_to_openai(body: dict) -> dict:
    """Responses 请求体 → OpenAI 请求体。

    ## `instructions` 必须显式转成 system 消息

    它是 Responses 协议里的 system prompt。白名单式拷贝会把它**静默
    丢掉**：所有带 system prompt 的调用退化成裸用户消息，
    角色与格式约束全部失效，而且不报错 —— 这类比 400 更难发现。
    """
    _CHAT_KEYS = ("model", "temperature", "top_p", "stream", "stop",
                  "presence_penalty", "frequency_penalty", "seed",
                  "response_format", "user",
                  "n", "logprobs", "top_logprobs", "logit_bias",
                  "parallel_tool_calls", "service_tier",
                  "stream_options", "reasoning_effort")
    normalized = {k: body[k] for k in _CHAT_KEYS if k in body}

    reasoning = body.get("reasoning")
    if isinstance(reasoning, dict) and reasoning.get("effort"):
        normalized.setdefault("reasoning_effort", reasoning["effort"])
    text_cfg = body.get("text")
    if isinstance(text_cfg, dict) and isinstance(text_cfg.get("format"), dict):
        normalized.setdefault("response_format", text_cfg["format"])

    messages: list[dict] = []
    instructions = body.get("instructions")
    if isinstance(instructions, str) and instructions.strip():
        messages.append({"role": "system", "content": instructions})
    messages.extend(responses_input_to_messages(body.get("input")))
    normalized["messages"] = messages

    tools = []
    for item in body.get("tools") or []:
        converted = _responses_tool_to_openai(item)
        if converted is not None:
            tools.append(converted)
    if tools:
        normalized["tools"] = tools

    choice = body.get("tool_choice")
    if isinstance(choice, dict) and choice.get("type") == "function":
        name = str(choice.get("name") or "")
        if name:
            normalized["tool_choice"] = {
                "type": "function", "function": {"name": name}}
    elif isinstance(choice, str) and choice in ("auto", "none", "required"):
        normalized["tool_choice"] = choice

    normalized.setdefault("max_tokens", body.get("max_output_tokens") or 4096)
    return normalized


# ---------------------------------------------------------------------------
# usage（缓存字段保留）
# ---------------------------------------------------------------------------
def anthropic_usage_of(usage) -> dict:
    """OpenAI usage → Anthropic usage（缓存命中拆成单列字段）。

    ## 为什么不能硬编码 `cache_read_input_tokens = 0`

    Anthropic 协议把缓存命中的输入 token 单列在
    `cache_read_input_tokens`。硬编码 0 有两个后果：客户端命中率恒为 0；
    输入量被放大（OpenAI 的 `prompt_tokens` **含**缓存命中部分，
    Anthropic 的 `input_tokens` 不含）。
    """
    try:
        from upstream_compat import anthropic_usage_from

        out = anthropic_usage_from(usage)
        if out is not None:
            return out
    except Exception:  # noqa: BLE001 - 兼容层缺失时退回手工实现
        pass
    u = usage if isinstance(usage, dict) else {}
    prompt = int(u.get("prompt_tokens") or 0)
    details = u.get("prompt_tokens_details")
    cached = int(
        u.get("cache_read_input_tokens")
        or u.get("cached_tokens")
        or (details.get("cached_tokens") if isinstance(details, dict) else 0)
        or 0)
    fresh = max(prompt - cached, 0) if cached else prompt
    return {"input_tokens": fresh,
            "output_tokens": int(u.get("completion_tokens") or 0),
            "cache_read_input_tokens": cached,
            "cache_creation_input_tokens": 0}


# ---------------------------------------------------------------------------
# OpenAI 响应 → Anthropic（非流式）
# ---------------------------------------------------------------------------
def _anthropic_tool_blocks(message: dict) -> list[dict]:
    """OpenAI `tool_calls` → Anthropic `tool_use` 内容块。"""
    blocks: list[dict] = []
    calls = message.get("tool_calls")
    if not isinstance(calls, list):
        return blocks
    for call in calls:
        if not isinstance(call, dict):
            continue
        fn = call.get("function") if isinstance(call.get("function"), dict) else {}
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args) if args.strip() else {}
            except ValueError:
                args = {"_raw": args}
        blocks.append({
            "type": "tool_use",
            "id": str(call.get("id") or ("call_%d" % len(blocks))),
            "name": str(fn.get("name") or "unknown"),
            "input": args if isinstance(args, dict) else {},
        })
    return blocks


def openai_to_anthropic(result: dict, model: str) -> dict:
    """OpenAI chat.completion → Anthropic message（含工具与思考块）。"""
    result = result if isinstance(result, dict) else {}
    choices = result.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices else {}
    choice = choice if isinstance(choice, dict) else {}
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    text = str(message.get("content") or "")
    reasoning = str(message.get("reasoning_content") or "")
    usage = result.get("usage") or {}
    tool_blocks = _anthropic_tool_blocks(message)
    stop = {"length": "max_tokens", "tool_calls": "tool_use"}.get(
        str(choice.get("finish_reason") or ""), "end_turn")
    # 没有 tool_use 块时不能声称要调工具 —— 客户端会一直等
    if stop == "tool_use" and not tool_blocks:
        stop = "end_turn"
    content: list[dict] = []
    if reasoning:
        content.append({"type": "thinking", "thinking": reasoning})
    if text:
        content.append({"type": "text", "text": text})
    content.extend(tool_blocks)
    return {
        "id": "msg_" + str(result.get("id") or uuid.uuid4().hex[:20]),
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop,
        "usage": anthropic_usage_of(usage),
    }


# ---------------------------------------------------------------------------
# OpenAI 响应 → Responses（非流式）
# ---------------------------------------------------------------------------
def _responses_status_of(finish: str) -> tuple[str, dict | None]:
    """finish_reason → (status, incomplete_details)。"""
    if finish == "length":
        return "incomplete", {"reason": "max_output_tokens"}
    return "completed", None


def _responses_usage(usage) -> dict:
    """OpenAI usage → Responses usage（缓存/推理明细透传）。

    Codex 客户端显示「cached input tokens」读的是
    `usage.input_tokens_details.cached_tokens`。只搬三个基础字段的话，
    缓存命中信息在 Responses 协议上就丢了 —— 客户端永远显示 0。
    """
    u = usage if isinstance(usage, dict) else {}
    out = {"input_tokens": int(u.get("prompt_tokens") or 0),
           "output_tokens": int(u.get("completion_tokens") or 0),
           "total_tokens": int(u.get("total_tokens") or 0)}
    details = u.get("prompt_tokens_details")
    if isinstance(details, dict) and details.get("cached_tokens"):
        out["input_tokens_details"] = {
            "cached_tokens": int(details["cached_tokens"])}
    completion = u.get("completion_tokens_details")
    if isinstance(completion, dict) and completion.get("reasoning_tokens"):
        out["output_tokens_details"] = {
            "reasoning_tokens": int(completion["reasoning_tokens"])}
    return out


def openai_to_responses(result: dict, model: str) -> dict:
    """OpenAI chat.completion → Responses 对象（含 function_call 输出项）。

    只翻 text 不翻 tool_calls 的话，Codex 系客户端看到的是「模型说完了」
    而不是「模型要调工具」—— 工具循环直接断掉。
    """
    result = result if isinstance(result, dict) else {}
    choices = result.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices else {}
    choice = choice if isinstance(choice, dict) else {}
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    text = str(message.get("content") or "")
    reasoning = str(message.get("reasoning_content") or "")
    usage = result.get("usage") or {}
    created = int(result.get("created") or time.time())
    rid = "resp_" + str(result.get("id") or uuid.uuid4().hex[:20])

    output: list[dict] = []
    if reasoning:
        output.append({
            "type": "reasoning", "id": "rs_" + uuid.uuid4().hex[:20],
            "summary": [{"type": "summary_text",
                         "text": reasoning[:2000]}],
        })
    if text:
        output.append({
            "type": "message", "id": "msg_" + uuid.uuid4().hex[:20],
            "role": "assistant",
            "content": [{"type": "output_text", "text": text}],
        })
    calls = message.get("tool_calls")
    if isinstance(calls, list):
        for call in calls:
            if not isinstance(call, dict):
                continue
            fn = call.get("function") if isinstance(call.get("function"), dict) else {}
            output.append({
                "type": "function_call",
                "id": "fc_" + uuid.uuid4().hex[:20],
                "call_id": str(call.get("id") or ("call_%d" % len(output))),
                "name": str(fn.get("name") or "unknown"),
                "arguments": str(fn.get("arguments") or "{}"),
            })
    status, incomplete = _responses_status_of(str(choice.get("finish_reason") or "stop"))
    response: dict = {
        "id": rid, "object": "response", "created_at": created,
        "model": model or str(result.get("model") or ""),
        "status": status,
        "output": output,
        "output_text": text,
        "usage": _responses_usage(usage),
    }
    if incomplete:
        response["incomplete_details"] = incomplete
    return response


# ---------------------------------------------------------------------------
# SSE 行解析（内层 StreamingResponse → 行）
# ---------------------------------------------------------------------------
async def _iter_sse_lines(inner) -> "object":
    """把内层字节流拆成单行（bytes）。

    内层生成器每次 yield 一个完整帧（`data: ...\n\n`），但心跳/代理
    都可能拆散边界，所以必须按字节缓冲再切行，不能假设对齐。
    """
    buf = b""
    async for piece in inner.body_iterator:
        if piece is None:
            continue
        if isinstance(piece, str):
            piece = piece.encode()
        buf += bytes(piece)
        while True:
            nl = buf.find(b"\n")
            if nl < 0:
                break
            line = buf[:nl].rstrip(b"\r")
            buf = buf[nl + 1:]
            if line:
                yield line
    if buf.strip():
        yield buf


# ---------------------------------------------------------------------------
# Anthropic 流式中继
# ---------------------------------------------------------------------------
def anthropic_stream_relay(inner, model: str, *, prefix: str = "gw") -> StreamingResponse:
    """OpenAI chunk SSE → Anthropic 事件流（工具/思考/心跳/错误全量）。

    ## 协议要点

    * `message_start` **先发**：客户端立刻知道请求被受理；
      上游排队/思考期间只看到 ping，不会再「整条流静默」。
    * 工具调用：OpenAI 的 `delta.tool_calls[]` 按 `index` 分槽缓冲，
      `id`+`name` 齐了才开 `tool_use` 块（名字没到就开块，客户端会拿到
      一个无名调用），参数按增量发 `input_json_delta`。
    * 思考链（`reasoning_content`）单独成 `thinking` 块，不混进正文 ——
      混进去的话 Claude Code 会把思考内容当回复渲染出来。
    * 内层的 `{"error": ...}` 帧不再吞掉：转成 Anthropic `error` 事件。
      原来上游 429/限流时客户端只看到流悄悄结束，毫无提示。
    * 只要开过流，收尾三件套（content_block_stop / message_delta /
      message_stop）**一定补齐** —— 上游不发 finish_reason 也不能让
      客户端挂死。
    """

    async def relay():
        msg_id = "msg_%s_%s" % (prefix, uuid.uuid4().hex[:20])
        started = False
        next_block = 0
        open_block = -1
        open_kind = ""
        tools: dict[int, dict] = {}
        finish = ""
        usage_seen: dict = {}
        error: dict | None = None

        def sse(data: dict) -> bytes:
            return ("data: %s\n\n" % json.dumps(data, ensure_ascii=False)).encode()

        def _close_open() -> list[bytes]:
            nonlocal open_block, open_kind
            if open_block < 0:
                return []
            out = [sse({"type": "content_block_stop", "index": open_block})]
            open_block = -1
            open_kind = ""
            return out

        def _open(kind: str, block: dict) -> list[bytes]:
            nonlocal open_block, open_kind, next_block
            out = _close_open()
            open_block = next_block
            open_kind = kind
            next_block += 1
            out.append(sse({"type": "content_block_start",
                            "index": open_block, "content_block": block}))
            return out

        yield sse({
            "type": "message_start",
            "message": {"id": msg_id, "type": "message", "role": "assistant",
                        "model": model, "content": [], "stop_reason": None,
                        "usage": {"input_tokens": 0, "output_tokens": 0}},
        })
        started = True

        async for line in _iter_sse_lines(inner):
            text = line.decode("utf-8", "replace").strip()
            if not text:
                continue
            if text.startswith(":"):
                # 源侧心跳注释原样透传（SSE 规范：客户端忽略注释行）
                yield text.encode() + b"\n\n"
                continue
            if not text.startswith("data:"):
                continue
            payload = text[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                chunk = json.loads(payload)
            except ValueError:
                continue
            if not isinstance(chunk, dict):
                continue
            err = chunk.get("error")
            if err:
                error = err if isinstance(err, dict) else {"message": str(err)}
                break
            usage = chunk.get("usage")
            if isinstance(usage, dict) and usage:
                usage_seen.update(usage)
            choices = chunk.get("choices")
            choice = choices[0] if isinstance(choices, list) and choices else {}
            choice = choice if isinstance(choice, dict) else {}
            delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
            if choice.get("finish_reason"):
                finish = str(choice["finish_reason"])

            if delta.get("reasoning_content"):
                if open_kind != "thinking":
                    for piece in _open("thinking",
                                       {"type": "thinking", "thinking": ""}):
                        yield piece
                yield sse({"type": "content_block_delta", "index": open_block,
                           "delta": {"type": "thinking_delta",
                                     "thinking": str(delta["reasoning_content"])}})
            if delta.get("content"):
                if open_kind != "text":
                    for piece in _open("text", {"type": "text", "text": ""}):
                        yield piece
                yield sse({"type": "content_block_delta", "index": open_block,
                           "delta": {"type": "text_delta",
                                     "text": str(delta["content"])}})
            for tc in delta.get("tool_calls") or []:
                if not isinstance(tc, dict):
                    continue
                idx = tc.get("index")
                if not isinstance(idx, int):
                    idx = 0
                slot = tools.setdefault(
                    idx, {"id": "", "name": "", "buf": "",
                          "block": -1, "flushed": 0})
                if tc.get("id"):
                    slot["id"] = str(tc["id"])
                fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                if fn.get("name"):
                    slot["name"] += str(fn["name"])
                piece_args = str(fn.get("arguments") or "")
                if piece_args:
                    slot["buf"] += piece_args
                if slot["block"] < 0:
                    # id+name 都齐了才开块；没齐就继续缓冲
                    if slot["id"] and slot["name"]:
                        for piece in _close_open():
                            yield piece
                        slot["block"] = next_block
                        next_block += 1
                        yield sse({"type": "content_block_start",
                                   "index": slot["block"],
                                   "content_block": {"type": "tool_use",
                                                     "id": slot["id"],
                                                     "name": slot["name"],
                                                     "input": {}}})
                        if slot["buf"]:
                            yield sse({"type": "content_block_delta",
                                       "index": slot["block"],
                                       "delta": {"type": "input_json_delta",
                                                 "partial_json": slot["buf"]}})
                            slot["flushed"] = len(slot["buf"])
                elif len(slot["buf"]) > slot["flushed"]:
                    piece = slot["buf"][slot["flushed"]:]
                    slot["flushed"] = len(slot["buf"])
                    yield sse({"type": "content_block_delta",
                               "index": slot["block"],
                               "delta": {"type": "input_json_delta",
                                         "partial_json": piece}})

        # 流结束：冲刷还没开块的调用（只有参数没等到名字的极端情况）
        for idx in sorted(tools):
            slot = tools[idx]
            if slot["block"] < 0:
                for piece in _close_open():
                    yield piece
                slot["block"] = next_block
                next_block += 1
                yield sse({"type": "content_block_start",
                           "index": slot["block"],
                           "content_block": {
                               "type": "tool_use",
                               "id": slot["id"] or ("call_%d_%s" % (idx, uuid.uuid4().hex[:8])),
                               "name": slot["name"] or "unknown",
                               "input": {}}})
                if slot["buf"]:
                    yield sse({"type": "content_block_delta",
                               "index": slot["block"],
                               "delta": {"type": "input_json_delta",
                                         "partial_json": slot["buf"]}})
            yield sse({"type": "content_block_stop", "index": slot["block"]})
        for piece in _close_open():
            yield piece

        had_tools = any(slot["block"] >= 0 for slot in tools.values())
        if had_tools:
            stop_reason = "tool_use"
        else:
            stop_reason = {"length": "max_tokens", "tool_calls": "tool_use"}.get(
                finish or "stop", "end_turn")
        yield sse({"type": "message_delta",
                   "delta": {"stop_reason": stop_reason},
                   "usage": anthropic_usage_of(usage_seen)})
        if error is not None:
            # 上游错误不再静默：显式 error 事件 + 正常收尾，
            # 客户端能看到「为什么断了」而不是「悄悄断了」。
            yield ("event: error\ndata: %s\n\n" % json.dumps(
                {"type": "error", "error": error}, ensure_ascii=False)).encode()
        yield sse({"type": "message_stop"})

    return StreamingResponse(
        relay(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------------------
# Responses 流式中继
# ---------------------------------------------------------------------------
def responses_stream_relay(inner, model: str, *, prefix: str = "gw") -> StreamingResponse:
    """OpenAI chunk SSE → Responses 事件流（function_call 全量支持）。

    输出项顺序与官方一致：reasoning → message(text) → function_call。
    `finish_reason="length"` 发 `response.incomplete`（带
    incomplete_details），错误发 `response.failed` —— Codex 才能区分
    「模型想继续但被截断」与「上游报错」，而不是显示成正常完成。
    """

    async def relay():
        resp_id = "resp_%s_%s" % (prefix, uuid.uuid4().hex[:20])
        created_at = int(time.time())
        finish = ""
        usage_seen: dict = {}
        error: dict | None = None

        text_state = {"added": False, "item_id": "", "output_index": 0,
                      "buf": ""}
        reasoning_state = {"added": False, "item_id": "", "buf": ""}
        tools: dict[int, dict] = {}
        output: list[dict] = []
        next_output_index = 0

        def event(name: str, data: dict) -> bytes:
            return ("event: %s\ndata: %s\n\n"
                    % (name, json.dumps(data, ensure_ascii=False))).encode()

        def _close_text() -> list[bytes]:
            if not text_state["added"]:
                return []
            text_state["added"] = False
            item_id = text_state["item_id"]
            idx = text_state["output_index"]
            final_text = text_state["buf"]
            text_state["buf"] = ""
            output.append({"type": "message", "id": item_id,
                           "status": "completed", "role": "assistant",
                           "content": [{"type": "output_text",
                                        "annotations": [],
                                        "text": final_text}]})
            return [
                event("response.content_part.done", {
                    "item_id": item_id, "output_index": idx,
                    "content_index": 0,
                    "part": {"type": "output_text", "annotations": [],
                             "text": final_text}}),
                event("response.output_item.done", {
                    "output_index": idx,
                    "item": {"type": "message", "id": item_id,
                             "status": "completed", "role": "assistant",
                             "content": [{"type": "output_text",
                                          "annotations": [],
                                          "text": final_text}]}}),
            ]

        def _close_reasoning() -> list[bytes]:
            if not reasoning_state["added"]:
                return []
            reasoning_state["added"] = False
            item_id = reasoning_state["item_id"]
            final_buf = reasoning_state["buf"]
            reasoning_state["buf"] = ""
            output.append({"type": "reasoning", "id": item_id,
                           "summary": [{"type": "summary_text",
                                        "text": final_buf}]})
            return [
                event("response.output_item.done", {
                    "output_index": reasoning_state.get("output_index", 0),
                    "item": {"type": "reasoning", "id": item_id,
                             "summary": [{"type": "summary_text",
                                          "text": final_buf}]}}),
            ]

        yield event("response.created", {
            "type": "response.created",
            "response": {"id": resp_id, "object": "response",
                         "created_at": created_at, "status": "in_progress",
                         "model": model, "output": []}})

        async for line in _iter_sse_lines(inner):
            text = line.decode("utf-8", "replace").strip()
            if not text:
                continue
            if text.startswith(":"):
                yield text.encode() + b"\n\n"
                continue
            if not text.startswith("data:"):
                continue
            payload = text[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                chunk = json.loads(payload)
            except ValueError:
                continue
            if not isinstance(chunk, dict):
                continue
            err = chunk.get("error")
            if err:
                error = err if isinstance(err, dict) else {"message": str(err)}
                break
            usage = chunk.get("usage")
            if isinstance(usage, dict) and usage:
                usage_seen.update(usage)
            choices = chunk.get("choices")
            choice = choices[0] if isinstance(choices, list) and choices else {}
            choice = choice if isinstance(choice, dict) else {}
            delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
            if choice.get("finish_reason"):
                finish = str(choice["finish_reason"])

            if delta.get("reasoning_content"):
                if not reasoning_state["added"]:
                    for piece in _close_text():
                        yield piece
                    reasoning_state["item_id"] = "rs_" + uuid.uuid4().hex[:20]
                    reasoning_state["output_index"] = next_output_index
                    next_output_index += 1
                    reasoning_state["added"] = True
                    yield event("response.output_item.added", {
                        "type": "response.output_item.added",
                        "output_index": reasoning_state["output_index"],
                        "item": {"type": "reasoning",
                                 "id": reasoning_state["item_id"],
                                 "summary": []}})
                    yield event("response.reasoning_summary_part.added", {
                        "type": "response.reasoning_summary_part.added",
                        "item_id": reasoning_state["item_id"],
                        "output_index": reasoning_state["output_index"],
                        "summary_index": 0,
                        "part": {"type": "summary_text", "text": ""}})
                piece = str(delta["reasoning_content"])
                reasoning_state["buf"] += piece
                yield event("response.reasoning_summary_text.delta", {
                    "type": "response.reasoning_summary_text.delta",
                    "item_id": reasoning_state["item_id"],
                    "output_index": reasoning_state["output_index"],
                    "summary_index": 0, "delta": piece})
            if delta.get("content"):
                if not text_state["added"]:
                    for piece in _close_reasoning():
                        yield piece
                    text_state["item_id"] = "msg_" + uuid.uuid4().hex[:20]
                    text_state["output_index"] = next_output_index
                    next_output_index += 1
                    text_state["added"] = True
                    text_state["buf"] = ""
                    yield event("response.output_item.added", {
                        "type": "response.output_item.added",
                        "output_index": text_state["output_index"],
                        "item": {"type": "message",
                                 "id": text_state["item_id"],
                                 "status": "in_progress", "role": "assistant",
                                 "content": []}})
                    yield event("response.content_part.added", {
                        "type": "response.content_part.added",
                        "item_id": text_state["item_id"],
                        "output_index": text_state["output_index"],
                        "content_index": 0,
                        "part": {"type": "output_text", "annotations": [],
                                 "text": ""}})
                piece = str(delta["content"])
                text_state["buf"] += piece
                yield event("response.output_text.delta", {
                    "type": "response.output_text.delta",
                    "item_id": text_state["item_id"],
                    "output_index": text_state["output_index"],
                    "content_index": 0, "delta": piece})
            for tc in delta.get("tool_calls") or []:
                if not isinstance(tc, dict):
                    continue
                idx = tc.get("index")
                if not isinstance(idx, int):
                    idx = 0
                slot = tools.setdefault(
                    idx, {"call_id": "", "name": "", "buf": "",
                          "item_id": "", "added": False,
                          "output_index": 0, "flushed": 0})
                if tc.get("id"):
                    slot["call_id"] = str(tc["id"])
                fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                if fn.get("name"):
                    slot["name"] += str(fn["name"])
                piece_args = str(fn.get("arguments") or "")
                if piece_args:
                    slot["buf"] += piece_args
                if not slot["added"]:
                    if slot["call_id"] and slot["name"]:
                        for piece in _close_text():
                            yield piece
                        for piece in _close_reasoning():
                            yield piece
                        slot["item_id"] = "fc_" + uuid.uuid4().hex[:20]
                        slot["output_index"] = next_output_index
                        next_output_index += 1
                        slot["added"] = True
                        yield event("response.output_item.added", {
                            "output_index": slot["output_index"],
                            "item": {"type": "function_call",
                                     "id": slot["item_id"],
                                     "call_id": slot["call_id"],
                                     "name": slot["name"],
                                     "arguments": "",
                                     "status": "in_progress"}})
                        if slot["buf"]:
                            yield event("response.function_call_arguments.delta", {
                                "type": "response.function_call_arguments.delta",
                                "item_id": slot["item_id"],
                                "output_index": slot["output_index"],
                                "delta": slot["buf"]})
                            slot["flushed"] = len(slot["buf"])
                elif len(slot["buf"]) > slot["flushed"]:
                    piece_args = slot["buf"][slot["flushed"]:]
                    slot["flushed"] = len(slot["buf"])
                    yield event("response.function_call_arguments.delta", {
                        "type": "response.function_call_arguments.delta",
                        "item_id": slot["item_id"],
                        "output_index": slot["output_index"],
                        "delta": piece_args})

        # 冲刷没等到名字的工具块
        for idx in sorted(tools):
            slot = tools[idx]
            if not slot["added"]:
                for piece in _close_text():
                    yield piece
                for piece in _close_reasoning():
                    yield piece
                slot["item_id"] = "fc_" + uuid.uuid4().hex[:20]
                slot["output_index"] = next_output_index
                next_output_index += 1
                slot["added"] = True
                yield event("response.output_item.added", {
                    "output_index": slot["output_index"],
                    "item": {"type": "function_call", "id": slot["item_id"],
                             "call_id": slot["call_id"]
                             or ("call_%d_%s" % (idx, uuid.uuid4().hex[:8])),
                             "name": slot["name"] or "unknown",
                             "arguments": "", "status": "in_progress"}})
            if slot["buf"]:
                yield event("response.function_call_arguments.delta", {
                    "item_id": slot["item_id"],
                    "output_index": slot["output_index"],
                    "delta": slot["buf"]})
            yield event("response.output_item.done", {
                "type": "response.output_item.done",
                "output_index": slot["output_index"],
                "item": {"type": "function_call", "id": slot["item_id"],
                         "call_id": slot["call_id"]
                         or ("call_%d_%s" % (idx, uuid.uuid4().hex[:8])),
                         "name": slot["name"] or "unknown",
                         "arguments": slot["buf"],
                         "status": "completed"}})
            output.append({
                "type": "function_call", "id": slot["item_id"],
                "call_id": slot["call_id"]
                or ("call_%d_%s" % (idx, uuid.uuid4().hex[:8])),
                "name": slot["name"] or "unknown",
                "arguments": slot["buf"]})
        for piece in _close_text():
            yield piece
        for piece in _close_reasoning():
            yield piece

        usage_out = _responses_usage(usage_seen)
        base = {"id": resp_id, "object": "response", "created_at": created_at,
                "model": model, "output": output, "usage": usage_out}
        if error is not None:
            base["status"] = "failed"
            base["error"] = error
            yield event("response.failed",
                        {"type": "response.failed", "response": base})
            return
        status, incomplete = _responses_status_of(finish or "stop")
        base["status"] = status
        if incomplete:
            base["incomplete_details"] = incomplete
            yield event("response.incomplete",
                        {"type": "response.incomplete", "response": base})
        else:
            yield event("response.completed",
                        {"type": "response.completed", "response": base})

    return StreamingResponse(
        relay(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
