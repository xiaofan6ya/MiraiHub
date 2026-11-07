"""Freebuff 协议层：目录合并、usage 归一、SSE 合成、三协议翻译。

## 这里唯一「非标准」的地方

上游是 OpenAI 兼容端点，帧就是标准 OpenAI SSE（`delta.content`），所以
**能直接透传**。但实测目录里 280 个模型中只有 `deepseek-v4.1-flash` 的
`capabilities.streaming` 为 true，其余 279 个为 false。对不支持流式的模型
如果硬发 `stream:true`，上游会报错或退化成一次性返回。

所以 `gateway.py` 的策略是：
  * 模型支持流式 → 逐帧透传；
  * 模型不支持流式 → 上游用非流式拿完整答复，再用 `synthesize_sse()`
    在本地按 48 字节切片合成标准 OpenAI SSE 帧。

这样客户端拿到的永远是合法 SSE，不必关心上游这个模型能不能流。
"""
from __future__ import annotations

import json
import time

from admin.platform.freebuff.core import endpoints


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def normalize_usage(usage: dict | None) -> dict:
    """OpenAI usage 归一：只保证三个关键字段存在且 total 合理。"""
    if not isinstance(usage, dict):
        return {}
    prompt = _int(usage.get("prompt_tokens"))
    completion = _int(usage.get("completion_tokens"))
    total = _int(usage.get("total_tokens"))
    if not total:
        total = prompt + completion
    out = {"prompt_tokens": prompt, "completion_tokens": completion,
           "total_tokens": total}
    for key in ("prompt_tokens_details", "completion_tokens_details"):
        value = usage.get(key)
        if isinstance(value, dict) and value:
            out[key] = value
    for key in ("cached_tokens", "reasoning_tokens"):
        if _int(usage.get(key)):
            out[key] = _int(usage[key])
    return out


# ---------------------------------------------------------------------------
# 目录合并
# ---------------------------------------------------------------------------
def merge_catalog(rows: list | None) -> list[dict]:
    """上游目录（只有 id + capabilities）合并内置元数据。

    上游给的信息很少（没有名字/上下文长度），所以能对上的就用内置表里的，
    对不上的按插件同一套正则推断能力，上下文用 131072/16384 兜底。
    """
    out: list[dict] = []
    if not isinstance(rows, list) or not rows:
        return endpoints.default_catalog()
    for row in rows:
        if not isinstance(row, dict):
            continue
        model_id = str(row.get("id") or "").strip()
        if not model_id:
            continue
        caps = row.get("capabilities") if isinstance(row.get("capabilities"),
                                                     dict) else {}
        known = endpoints.known_model(model_id)
        inferred = endpoints.infer_capabilities(model_id)
        meta = known or {
            "id": model_id,
            "name": row.get("name") or model_id,
            "context": row.get("context_length") or 131072,
            "output": row.get("max_tokens") or 16384,
            "reasoning": inferred["reasoning"],
            "vision": inferred["vision"],
        }
        out.append({
            "id": model_id,
            "name": str(row.get("name") or meta.get("name") or model_id),
            "description": "",
            "context_window": _int(row.get("context_length")
                                   or meta.get("context")) or 131072,
            "max_tokens": _int(row.get("max_tokens")
                               or meta.get("output")) or 16384,
            "reasoning": bool(caps.get("reasoning", meta.get("reasoning"))),
            "vision": bool(caps.get("images", meta.get("vision"))),
            "toolcall": bool(caps.get("toolCalls", True)),
            "streaming": bool(caps.get("streaming")),
            "chat": bool(caps.get("chat", True)),
            "default": model_id == endpoints.DEFAULT_MODEL,
        })
    return out or endpoints.default_catalog()


def catalog_row(catalog: list[dict], model: str) -> dict | None:
    target = str(model or "")
    for row in catalog or []:
        if row.get("id") == target:
            return row
    return None


def supports_streaming(catalog: list[dict], model: str) -> bool:
    """该模型在目录里是否声明支持流式。

    目录里查不到时按「支持」处理（交给上游判定），因为未知模型不会命中
    大多数调用；已知模型按真实标志走。
    """
    row = catalog_row(catalog, model)
    if row is None:
        return True
    if not row.get("chat", True):
        return False
    return bool(row.get("streaming"))


# ---------------------------------------------------------------------------
# 非流式结果 → OpenAI SSE 帧
# ---------------------------------------------------------------------------
def synthesize_sse(result: dict, model: str):
    """把一个 `chat.completion` 拆成标准 OpenAI SSE chunk 序列。"""
    created = _int(result.get("created")) or int(time.time())
    chunk_id = str(result.get("id") or ("chatcmpl-freebuff-%d" % created))
    choices = result.get("choices") or []
    first = choices[0] if choices else {}
    message = first.get("message") if isinstance(first, dict) else {}
    message = message if isinstance(message, dict) else {}
    finish = str(first.get("finish_reason") or "stop") if isinstance(first, dict) \
        else "stop"

    def chunk(delta: dict, finish_reason=None, usage=None) -> bytes:
        payload = {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model or result.get("model") or "",
            "choices": [{"index": 0, "delta": delta,
                         "finish_reason": finish_reason}],
        }
        if usage:
            payload["choices"] = []
            payload["usage"] = usage
        return ("data: %s\n\n" % json.dumps(payload, ensure_ascii=False)).encode()

    yield chunk({"role": "assistant", "content": ""})
    reasoning = message.get("reasoning_content")
    if isinstance(reasoning, str) and reasoning:
        for offset in range(0, len(reasoning), endpoints.SSE_CHUNK):
            yield chunk({"reasoning_content":
                         reasoning[offset:offset + endpoints.SSE_CHUNK]})
    text = message.get("content")
    if isinstance(text, str) and text:
        for offset in range(0, len(text), endpoints.SSE_CHUNK):
            yield chunk({"content": text[offset:offset + endpoints.SSE_CHUNK]})
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list) and tool_calls:
        delta_calls = []
        for index, item in enumerate(tool_calls):
            if not isinstance(item, dict):
                continue
            entry = {"index": index, "type": "function",
                     "function": {"name": (item.get("function") or {}).get("name")
                                  or "", "arguments": ""}}
            if item.get("id"):
                entry["id"] = item["id"]
            delta_calls.append(entry)
        yield chunk({"tool_calls": delta_calls})
        # arguments 逐片下发，保持与真流式一致的形态
        for index, item in enumerate(tool_calls):
            if not isinstance(item, dict):
                continue
            arguments = (item.get("function") or {}).get("arguments") or ""
            for offset in range(0, len(arguments), endpoints.SSE_CHUNK):
                yield chunk({"tool_calls": [{
                    "index": index,
                    "function": {"arguments":
                                 arguments[offset:offset + endpoints.SSE_CHUNK]}}]})
    yield chunk({}, finish or "stop")
    usage = result.get("usage")
    if isinstance(usage, dict) and usage:
        yield chunk({}, None, normalize_usage(usage))
    yield b"data: [DONE]\n\n"


# ---------------------------------------------------------------------------
# Anthropic / Responses 翻译
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 工具闭环：Anthropic / Responses ↔ OpenAI
#
# 上游是 OpenAI 兼容端点，工具调用是原生的 `tools` / `tool_calls`。所以
# Agent 侧只要把另外两种协议的工具语义翻译成 OpenAI 的，工具就能闭环：
#
#   Anthropic  tools[{name, description, input_schema}]   → OpenAI tools
#              assistant content[{type:tool_use}]         → message.tool_calls
#              user content[{type:tool_result}]           → role="tool" 消息
#              stop_reason="tool_use"                     ← finish_reason="tool_calls"
#
#   Responses  tools[{type:function,name,parameters}]     → OpenAI tools
#              input[{type:function_call,...}]             → assistant tool_calls
#              input[{type:function_call_output,...}]      → role="tool" 消息
#              output[{type:function_call,...}]            ← message.tool_calls
#
# 三条硬约束（都来自实测）：
#   1. `parameters` 必须压平 —— `anyOf` / `$ref` / 数组型 `type` 会让上游整轮失败；
#   2. `arguments` 是**字符串**，Anthropic 侧要 `json.loads` 成 `input`；
#   3. tool_calls 与 tool 结果必须成对，孤儿 `tool` 消息会被上游拒。
# JSON Schema 压平（上游转换器只吃朴素子集）
# ---------------------------------------------------------------------------
_MAX_DEPTH = 24


def _type_of(value) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "string"


def _branch_score(node: dict) -> tuple[int, int]:
    """`anyOf` / `oneOf` 选支：object > array > 标量，同级取字段多的。"""
    kind = str(node.get("type") or "")
    rank = {"object": 3, "array": 2}.get(kind, 1)
    return (rank, len(node))


def sanitize_schema(schema, defs: dict | None = None, seen=None, depth: int = 0):
    """把任意 JSON Schema 压成上游能吃的朴素形状。

    上游的 schema 转换器只认最朴素的子集，出现 `anyOf` / `$ref` 就回
    `AIError`（对外文案「AI服务异常」），`type` 用数组则 400 `InvalidArgument`。
    """
    if depth > _MAX_DEPTH:
        return {"type": "string"}
    if not isinstance(schema, dict):
        return {"type": _type_of(schema)}
    defs = defs if isinstance(defs, dict) else {}
    seen = set() if seen is None else seen

    node = dict(schema)

    # $defs / definitions 只作为展开素材，不往上传
    local_defs = node.pop("$defs", None)
    if isinstance(local_defs, dict):
        defs = {**defs, **local_defs}
    extra_defs = node.pop("definitions", None)
    if isinstance(extra_defs, dict):
        defs = {**defs, **extra_defs}

    node.pop("not", None)

    # $ref：仅展开本地指针 `#/...`
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        if ref in seen:
            return {"type": "string"}
        # `#/$defs/X` 与 `#/definitions/X` 都落到同一张 defs 表上：
        # defs 已经被 pop 出来了，指针头两段要跳过，否则永远查不到。
        path = [token.replace("~1", "/").replace("~0", "~")
                for token in ref[2:].split("/")]
        if path and path[0] in ("$defs", "definitions"):
            path = path[1:]
        target = defs
        for token in path:
            if isinstance(target, dict) and token in target:
                target = target[token]
            else:
                target = None
                break
        if isinstance(target, dict):
            merged = dict(target)
            merged.update({k: v for k, v in node.items() if k != "$ref"})
            return sanitize_schema(merged, defs, seen | {ref}, depth + 1)
        node.pop("$ref", None)
    else:
        node.pop("$ref", None)

    # const → 单元素 enum
    if "const" in node:
        node.pop("const")
        node.setdefault("enum", [schema["const"]])

    # allOf → 合并
    all_of = node.pop("allOf", None)
    if isinstance(all_of, list):
        merged: dict = {}
        for item in all_of:
            piece = sanitize_schema(item, defs, seen, depth + 1)
            if not isinstance(piece, dict):
                continue
            if isinstance(piece.get("properties"), dict):
                merged.setdefault("properties", {}).update(piece["properties"])
            if isinstance(piece.get("required"), list):
                have = merged.setdefault("required", [])
                for name in piece["required"]:
                    if name not in have:
                        have.append(name)
            for key, value in piece.items():
                if key not in ("properties", "required", "type"):
                    merged.setdefault(key, value)
            if piece.get("type") == "object":
                merged["type"] = "object"
        for key, value in node.items():
            if key not in merged:
                merged[key] = value
        node = merged

    # anyOf / oneOf → 保留信息量最大的一支
    for union in ("anyOf", "oneOf"):
        branches = node.pop(union, None)
        if not isinstance(branches, list) or not branches:
            continue
        usable = []
        for item in branches:
            piece = sanitize_schema(item, defs, seen, depth + 1)
            if not isinstance(piece, dict):
                continue
            if str(piece.get("type") or "") == "null":
                continue
            usable.append(piece)
        if not usable:
            node.setdefault("type", "string")
            continue
        best = max(usable, key=_branch_score)
        node = {**best, **{k: v for k, v in node.items() if k not in best}}

    # type 是数组 → 第一个非 null
    raw_type = node.get("type")
    if isinstance(raw_type, list):
        picked = next((str(t) for t in raw_type if str(t) != "null"), "")
        if picked:
            node["type"] = picked
        else:
            node.pop("type", None)
    elif isinstance(raw_type, str) and raw_type == "null":
        node.pop("type", None)

    # 递归子结构
    if isinstance(node.get("properties"), dict):
        node["properties"] = {
            key: sanitize_schema(value, defs, seen, depth + 1)
            for key, value in node["properties"].items()
        }
    if isinstance(node.get("items"), (dict, list)):
        items = node["items"]
        node["items"] = ([sanitize_schema(item, defs, seen, depth + 1)
                          for item in items] if isinstance(items, list)
                         else sanitize_schema(items, defs, seen, depth + 1))
    if isinstance(node.get("additionalProperties"), dict):
        node["additionalProperties"] = sanitize_schema(
            node["additionalProperties"], defs, seen, depth + 1)

    # 每个节点必须有 type
    if not str(node.get("type") or "").strip():
        if isinstance(node.get("properties"), dict) or "required" in node \
                or "additionalProperties" in node:
            node["type"] = "object"
        elif "items" in node:
            node["type"] = "array"
        elif isinstance(node.get("enum"), list) and node["enum"]:
            node["type"] = _type_of(node["enum"][0])
        else:
            node["type"] = "string"
    return node


# ---------------------------------------------------------------------------
def openai_tools_sanitized(tools) -> list[dict]:
    """客户端 tools → 上游能吃的 OpenAI 形状（schema 压平）。"""
    out: list[dict] = []
    if not isinstance(tools, list):
        return out
    for item in tools:
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "function") != "function":
            continue
        function = item.get("function")
        source = function if isinstance(function, dict) else item
        name = source.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        entry: dict = {"type": "function",
                       "function": {"name": name.strip()}}
        if isinstance(source.get("description"), str):
            entry["function"]["description"] = source["description"]
        params = source.get("parameters")
        entry["function"]["parameters"] = sanitize_schema(
            params if isinstance(params, dict) else {"type": "object",
                                                     "properties": {}})
        if isinstance(source.get("strict"), bool):
            entry["function"]["strict"] = source["strict"]
        out.append(entry)
    return out


def anthropic_tools_to_openai(tools) -> list[dict]:
    """Anthropic `tools[{name, description, input_schema}]` → OpenAI tools。"""
    if not isinstance(tools, list):
        return []
    converted = []
    for item in tools:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        converted.append({
            "type": "function",
            "function": {
                "name": name.strip(),
                "description": str(item.get("description") or ""),
                "parameters": sanitize_schema(
                    item.get("input_schema")
                    if isinstance(item.get("input_schema"), dict)
                    else {"type": "object", "properties": {}}),
            },
        })
    return converted


def anthropic_tool_choice_to_openai(choice):
    """`auto` / `any` / `{type:tool,name}` / `{type:none}` → OpenAI tool_choice。"""
    if choice is None:
        return None
    if isinstance(choice, str):
        if choice == "any":
            return "required"
        if choice == "none":
            return "none"
        return "auto"
    if isinstance(choice, dict):
        kind = str(choice.get("type") or "")
        if kind == "tool":
            name = choice.get("name")
            if isinstance(name, str) and name.strip():
                return {"type": "function", "function": {"name": name.strip()}}
        if kind == "none":
            return "none"
        if kind == "any":
            return "required"
    return "auto"


def _text_of_blocks(blocks) -> str:
    if isinstance(blocks, str):
        return blocks
    if not isinstance(blocks, list):
        return ""
    parts = []
    for block in blocks:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") in (None, "text"):
            parts.append(str(block.get("text") or ""))
    return "".join(parts)


def anthropic_messages_to_openai(messages) -> list[dict]:
    """Anthropic messages → OpenAI messages，**含工具闭环**。

    * `assistant` 的 `tool_use` 块 → `message.tool_calls`；
    * `user` 的 `tool_result` 块 → 独立的 `role="tool"` 消息（带 `tool_call_id`）；
    * 同一 message 里既有文本又有工具结果时，先出 tool 消息、再出文本消息。
    """
    out: list[dict] = []
    for item in (messages or []):
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "user")
        content = item.get("content")

        if isinstance(content, str):
            if content.strip():
                out.append({"role": role, "content": content})
            continue
        if not isinstance(content, list):
            continue

        texts: list[str] = []
        tool_calls: list[dict] = []
        tool_results: list[dict] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            kind = str(block.get("type") or "")
            if kind == "text":
                texts.append(str(block.get("text") or ""))
            elif kind == "tool_use":
                arguments = block.get("input")
                if not isinstance(arguments, str):
                    try:
                        arguments = json.dumps(arguments, ensure_ascii=False)
                    except (TypeError, ValueError):
                        arguments = "{}"
                tool_calls.append({
                    "id": str(block.get("id") or ""),
                    "type": "function",
                    "function": {"name": str(block.get("name") or ""),
                                 "arguments": arguments},
                })
            elif kind == "tool_result":
                tool_results.append({
                    "tool_call_id": str(block.get("tool_use_id") or ""),
                    "content": _text_of_blocks(block.get("content")),
                })
            elif kind in ("image", "document"):
                # 图片/文档上游（OpenAI 兼容）不是所有模型都支持；
                # 这里用占位文本保留语义，避免整条消息被丢掉。
                texts.append("[%s omitted]" % kind)

        if role == "assistant":
            message: dict = {"role": "assistant",
                             "content": "".join(texts) if texts else ""}
            if tool_calls:
                message["tool_calls"] = tool_calls
            if message["content"] or tool_calls:
                out.append(message)
            continue

        # user：tool_result 必须先出，且每条一个 tool 消息
        for result in tool_results:
            out.append({"role": "tool",
                        "tool_call_id": result["tool_call_id"],
                        "content": result["content"]})
        if "".join(texts).strip():
            out.append({"role": "user", "content": "".join(texts)})
    return out


def anthropic_to_openai(body: dict) -> dict:
    """Anthropic `/v1/messages` → OpenAI `chat/completions`（含 tools）。"""
    out: dict = {"model": body.get("model") or ""}
    messages: list[dict] = []

    system = body.get("system")
    if isinstance(system, str) and system.strip():
        messages.append({"role": "system", "content": system})
    elif isinstance(system, list):
        text = _text_of_blocks(system)
        if text.strip():
            messages.append({"role": "system", "content": text})

    messages.extend(anthropic_messages_to_openai(body.get("messages")))
    out["messages"] = messages

    tools = anthropic_tools_to_openai(body.get("tools"))
    if tools:
        out["tools"] = tools
        choice = anthropic_tool_choice_to_openai(body.get("tool_choice"))
        if choice is not None:
            out["tool_choice"] = choice
        # Anthropic 的并行工具调用开关与 OpenAI 无关，但上游支持并行，
        # 保持默认即可。
    for src, dst in (("max_tokens", "max_tokens"),
                     ("temperature", "temperature"),
                     ("top_p", "top_p"),
                     ("stop_sequences", "stop")):
        if body.get(src) is not None:
            out[dst] = body[src]
    return out


def _cached_of(usage: dict) -> int:
    cached = _int(usage.get("cached_tokens"))
    if not cached:
        details = usage.get("prompt_tokens_details")
        if isinstance(details, dict):
            cached = _int(details.get("cached_tokens"))
    return cached


def _reasoning_of(usage: dict) -> int:
    reasoning = _int(usage.get("reasoning_tokens"))
    if not reasoning:
        details = usage.get("completion_tokens_details")
        if isinstance(details, dict):
            reasoning = _int(details.get("reasoning_tokens"))
    return reasoning


def openai_to_anthropic(result: dict, model: str) -> dict:
    """OpenAI `chat.completion` → Anthropic `message`（含 tool_use 块）。"""
    choices = result.get("choices") or []
    first = choices[0] if choices else {}
    first = first if isinstance(first, dict) else {}
    message = first.get("message") if isinstance(first.get("message"), dict) else {}
    usage = result.get("usage") or {}

    blocks: list[dict] = []
    text = message.get("content")
    if isinstance(text, str) and text:
        blocks.append({"type": "text", "text": text})
    for call in (message.get("tool_calls") or []):
        if not isinstance(call, dict):
            continue
        function = call.get("function") if isinstance(call.get("function"),
                                                     dict) else {}
        raw_arguments = function.get("arguments")
        try:
            parsed = json.loads(raw_arguments) if isinstance(raw_arguments, str) \
                and raw_arguments.strip() else {}
        except Exception:  # noqa: BLE001 - 上游偶尔给非 JSON，原样塞进去
            parsed = {"_raw": raw_arguments}
        blocks.append({
            "type": "tool_use",
            "id": str(call.get("id") or ("toolu_%d" % len(blocks))),
            "name": str(function.get("name") or ""),
            "input": parsed if isinstance(parsed, dict) else {"value": parsed},
        })
    if not blocks:
        blocks.append({"type": "text", "text": ""})

    finish = str(first.get("finish_reason") or "")
    stop_reason = {"tool_calls": "tool_use",
                   "length": "max_tokens",
                   "stop": "end_turn"}.get(finish, finish or "end_turn")

    anthropic_usage = {
        "input_tokens": _int(usage.get("prompt_tokens")),
        "output_tokens": _int(usage.get("completion_tokens")),
    }
    cached = _cached_of(usage)
    if cached:
        anthropic_usage["cache_read_input_tokens"] = cached
    reasoning = _reasoning_of(usage)
    if reasoning:
        anthropic_usage["reasoning_tokens"] = reasoning

    return {
        "id": result.get("id") or ("msg_%d" % int(time.time())),
        "type": "message",
        "role": "assistant",
        "model": model or result.get("model") or "",
        "content": blocks,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": anthropic_usage,
    }


def responses_tools_to_openai(tools) -> list[dict]:
    """Responses `tools`（顶层平铺的 function）→ OpenAI tools。"""
    if not isinstance(tools, list):
        return []
    converted = []
    for item in tools:
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "function") not in ("function", ""):
            continue  # web_search / file_search 之类上游没有，如实丢弃
        name = item.get("name") or (item.get("function") or {}).get("name") \
            if isinstance(item.get("function"), dict) else item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        schema = item.get("parameters")
        if schema is None and isinstance(item.get("function"), dict):
            schema = item["function"].get("parameters")
        converted.append({
            "type": "function",
            "function": {
                "name": name.strip(),
                "description": str(item.get("description") or ""),
                "parameters": sanitize_schema(
                    schema if isinstance(schema, dict)
                    else {"type": "object", "properties": {}}),
            },
        })
    return converted


def responses_input_to_openai(raw) -> list[dict]:
    """Responses `input` → OpenAI messages（含 function_call 历史）。"""
    messages: list[dict] = []
    if isinstance(raw, str):
        return [{"role": "user", "content": raw}]
    if not isinstance(raw, list):
        return [{"role": "user", "content": ""}]
    for item in raw:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "")
        if kind == "function_call":
            arguments = item.get("arguments")
            if not isinstance(arguments, str):
                try:
                    arguments = json.dumps(arguments or {}, ensure_ascii=False)
                except (TypeError, ValueError):
                    arguments = "{}"
            messages.append({
                "role": "assistant", "content": "",
                "tool_calls": [{
                    "id": str(item.get("call_id") or item.get("id") or ""),
                    "type": "function",
                    "function": {"name": str(item.get("name") or ""),
                                 "arguments": arguments},
                }],
            })
            continue
        if kind == "function_call_output":
            messages.append({
                "role": "tool",
                "tool_call_id": str(item.get("call_id") or ""),
                "content": _text_of_blocks(item.get("output")),
            })
            continue
        role = str(item.get("role") or "user")
        content = item.get("content")
        if isinstance(content, list):
            content = _text_of_blocks(content)
        if content:
            messages.append({"role": role, "content": content})
    if not messages:
        messages.append({"role": "user", "content": ""})
    return messages


def responses_request_to_openai(body: dict) -> dict:
    """Responses `/v1/responses` → OpenAI `chat/completions`（含 tools）。"""
    model = str(body.get("model") or "")
    out = {"model": model,
           "messages": responses_input_to_openai(body.get("input"))}
    tools = responses_tools_to_openai(body.get("tools"))
    if tools:
        out["tools"] = tools
        choice = body.get("tool_choice")
        if isinstance(choice, str) and choice in ("auto", "required", "none"):
            out["tool_choice"] = choice
        elif isinstance(choice, dict) and choice.get("name"):
            out["tool_choice"] = {"type": "function",
                                  "function": {"name": str(choice["name"])}}
    if body.get("max_output_tokens") is not None:
        out["max_tokens"] = body["max_output_tokens"]
    elif body.get("max_tokens") is not None:
        out["max_tokens"] = body["max_tokens"]
    if body.get("temperature") is not None:
        out["temperature"] = body["temperature"]
    if body.get("instructions") and isinstance(body["instructions"], str):
        out["messages"].insert(0, {"role": "system",
                                   "content": body["instructions"]})
    return out


def openai_to_responses(result: dict, model: str) -> dict:
    """OpenAI `chat.completion` → Responses `response`（含 function_call 输出项）。"""
    choices = result.get("choices") or []
    first = choices[0] if choices else {}
    first = first if isinstance(first, dict) else {}
    message = first.get("message") if isinstance(first.get("message"), dict) else {}
    usage = result.get("usage") or {}
    rid = "resp_" + str(result.get("id") or int(time.time()))

    output: list[dict] = []
    text = message.get("content")
    if isinstance(text, str) and text:
        output.append({
            "type": "message", "id": "msg_" + rid[-12:], "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": text,
                         "annotations": []}],
        })
    for call in (message.get("tool_calls") or []):
        if not isinstance(call, dict):
            continue
        function = call.get("function") if isinstance(call.get("function"),
                                                     dict) else {}
        arguments = function.get("arguments")
        if not isinstance(arguments, str):
            try:
                arguments = json.dumps(arguments or {}, ensure_ascii=False)
            except (TypeError, ValueError):
                arguments = "{}"
        output.append({
            "type": "function_call",
            "id": "fc_" + str(call.get("id") or len(output)),
            "call_id": str(call.get("id") or ""),
            "name": str(function.get("name") or ""),
            "arguments": arguments,
            "status": "completed",
        })
    if not output:
        output.append({
            "type": "message", "id": "msg_" + rid[-12:], "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "", "annotations": []}],
        })

    responses_usage = {
        "input_tokens": _int(usage.get("prompt_tokens")),
        "output_tokens": _int(usage.get("completion_tokens")),
        # 上游偶尔只给 prompt/completion，total 缺了就自算（别让客户端看到 0）
        "total_tokens": _int(usage.get("total_tokens")) or (
            _int(usage.get("prompt_tokens")) + _int(usage.get("completion_tokens"))),
    }
    cached = _cached_of(usage)
    if cached:
        responses_usage["input_tokens_details"] = {"cached_tokens": cached}
    reasoning = _reasoning_of(usage)
    if reasoning:
        responses_usage["output_tokens_details"] = {
            "reasoning_tokens": reasoning}

    finish = str(first.get("finish_reason") or "")
    status = "incomplete" if finish in ("length", "content_filter") else "completed"
    return {
        "id": rid,
        "object": "response",
        "created_at": result.get("created") or int(time.time()),
        "status": status,
        "model": model or result.get("model") or "",
        "output": output,
        "usage": responses_usage,
    }


def estimate_tokens(text: str) -> int:
    return max(1, int(len(text or "") / 2.2))


def sse_text(line: str) -> str:
    """从一行 SSE 里取 `data:` 后的内容（兼容 `data:` 后无空格）。"""
    text = str(line or "").strip()
    if text.startswith("data:"):
        return text[5:].strip()
    return text