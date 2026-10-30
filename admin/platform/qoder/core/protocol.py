"""协议转换：OpenAI / Anthropic / Responses ↔ Qoder 上游信封。

## 上游要什么

Qoder 不收裸的 `{model, messages}`，而是一个带**会话、记录、业务上下文**的
固定信封（见 `build_upstream_body`）。所以三个下游协议都要先归一到
「消息序列 + 参数」，再套信封。

## 三个必须处理的差异（都是上游的硬约束）

1. **system 必须放进 messages 里** —— 上游顶层 `system` 字段无效
2. **`model_config` 必须剥掉 `thinking_config`** —— 原样回传会覆盖
   `parameters.enable_thinking`，导致「关闭思考」失效
3. **响应多一层信封** —— `{statusCodeValue, body}`，`body` 是 JSON 字符串，
   要拆开才能拿到内层 OpenAI chunk

## 响应里 content 与 reasoning_content 是分开的

上游在 `delta.reasoning_content` 里给思考过程，在 `delta.content` 里给正文。
下游要按协议决定：
  * OpenAI：思考可以放进 `reasoning_content`（部分客户端认）
  * Anthropic：思考要转成 `thinking_delta`
  * Responses：思考转 `response.reasoning_summary_text.delta`
"""
from __future__ import annotations

import json
import time
import uuid

#: 上游 `chat_task`
CHAT_TASK = "common"
#: 上游 `session_type`
SESSION_TYPE = "agent"
#: 上游 `agent_id`
AGENT_ID = "agent"
#: 输出上限（上游对这个值有上限，超了会被截断）
MAX_OUTPUT_TOKENS = 32768

#: 透传给上游、但**必须剥掉**的模型目录字段
#: （带 `thinking_config` 会覆盖 `parameters.enable_thinking`）
_STRIP_FROM_MODEL_CONFIG = frozenset({"thinking_config"})


def slim_model_config(model: dict) -> dict:
    """模型目录条目 → 上游 `model_config`（剥掉会覆盖参数的字段）。"""
    keep = ("key", "display_name", "format", "source", "is_reasoning",
            "is_vl", "max_input_tokens", "price_factor")
    return {k: model[k] for k in keep if k in model
            and k not in _STRIP_FROM_MODEL_CONFIG}


# ---------------------------------------------------------------------------
# 消息归一
# ---------------------------------------------------------------------------
def content_to_text(content) -> str:
    """把各种形态的 content 拍平成纯文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text") or ""))
                elif block.get("type") == "image_url":
                    # 上游的图片走 `image_urls` 字段，不进文本
                    continue
                elif "text" in block:
                    parts.append(str(block["text"]))
        return "".join(parts)
    if content is None:
        return ""
    return str(content)


def normalize_messages(messages: list[dict]) -> list[dict]:
    """把下游 messages 规整成上游能吃的形状。

    处理：
      * 丢弃 role 为空的消息（上游会 400）
      * system / developer 消息**合并到最前面**（上游不收顶层 system）
      * content 统一成字符串
    """
    system_parts: list[str] = []
    out: list[dict] = []

    for message in messages or []:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").strip()
        if not role:
            continue
        text = content_to_text(message.get("content"))
        if role in ("system", "developer"):
            if text:
                system_parts.append(text)
            continue
        if role not in ("user", "assistant", "tool"):
            continue
        item = {"role": role, "content": text}
        # tool 消息要带 tool_call_id，否则上游不知道这是哪个调用的结果
        if role == "tool" and message.get("tool_call_id"):
            item["tool_call_id"] = message["tool_call_id"]
        if message.get("tool_calls"):
            item["tool_calls"] = message["tool_calls"]
        # 连续同角色合并（上游对交替有要求）
        if out and out[-1]["role"] == role and role in ("user", "assistant"):
            out[-1]["content"] = (out[-1]["content"] + "\n\n" + text).strip()
            if message.get("tool_calls"):
                out[-1].setdefault("tool_calls", []).extend(message["tool_calls"])
            continue
        out.append(item)

    if system_parts:
        preamble = "\n\n".join(system_parts)
        if out and out[0]["role"] == "user":
            out[0]["content"] = "%s\n\n%s" % (preamble, out[0]["content"])
        else:
            out.insert(0, {"role": "user", "content": preamble})
    return out


def last_user_text(messages: list[dict]) -> str:
    """最后一条 user 文本（上游 `chat_context.text` 用它）。"""
    for message in reversed(messages or []):
        if message.get("role") == "user":
            return content_to_text(message.get("content"))
    return ""


# ---------------------------------------------------------------------------
# 请求体构造
# ---------------------------------------------------------------------------
def build_upstream_body(*, model: dict, messages: list[dict],
                        tools: list | None = None,
                        max_tokens: int | None = None,
                        thinking: bool | None = None,
                        reasoning_effort: str = "",
                        session_seed: str = "") -> dict:
    """组装上游信封。

    `session_seed` 用于「同一段对话复用同一 session」：下游给了 `user` 或
    `session_id` 时用它派生，否则每次新建。
    """
    limit = MAX_OUTPUT_TOKENS
    if max_tokens and int(max_tokens) > 0:
        limit = min(int(max_tokens), MAX_OUTPUT_TOKENS)

    record_id = str(uuid.uuid4())
    session_id = ("sess-" + session_seed[:32]) if session_seed else (
        "sess-" + uuid.uuid4().hex[:16])

    parameters: dict = {"max_tokens": limit}
    if thinking is True:
        parameters["enable_thinking"] = True
        if reasoning_effort:
            parameters["reasoning_effort"] = reasoning_effort
    elif thinking is False:
        parameters["enable_thinking"] = False

    prompt_text = last_user_text(messages)
    model_key = str(model.get("key") or "")

    return {
        "request_id": str(uuid.uuid4()),
        "request_set_id": record_id,
        "chat_record_id": record_id,
        "session_id": session_id,
        "stream": True,
        "chat_task": CHAT_TASK,
        "is_reply": True,
        "is_retry": False,
        "source": 1,
        "version": "3",
        "session_type": SESSION_TYPE,
        "agent_id": AGENT_ID,
        "task_id": "common",
        "code_language": "",
        "chat_prompt": "",
        "image_urls": None,
        "aliyun_user_type": "",
        "system": "",
        "messages": messages,
        "tools": tools or [],
        "parameters": parameters,
        "chat_context": {
            "chatPrompt": "",
            "imageUrls": None,
            "extra": {
                "context": [],
                "modelConfig": {"key": model_key,
                                "is_reasoning": bool(model.get("is_reasoning"))},
                "originalContent": prompt_text,
            },
            "features": [],
            "text": prompt_text,
        },
        "model_config": slim_model_config(model),
    }


# ---------------------------------------------------------------------------
# 下游响应帧
# ---------------------------------------------------------------------------
def openai_chunk(*, chunk_id: str, model_name: str, created: int | None = None,
                 content: str = "", reasoning: str = "",
                 finish_reason: str | None = None,
                 role: str | None = None,
                 tool_calls: list | None = None,
                 tool_name: str = "", tool_args: str = "") -> dict:
    """OpenAI `chat.completion.chunk` 帧。

    `tool_calls` **必须能转发**：harness 类客户端靠它决定「下一步调哪个工具」，
    丢了就只剩一个 `finish_reason=tool_calls` 的空收尾 ——
    客户端看到「该调工具了」却拿不到调用内容，只能直接停下
    。
    """
    delta: dict = {}
    if role:
        delta["role"] = role
    if content:
        delta["content"] = content
    if reasoning:
        # 部分客户端认这个字段；不认也只是忽略，不会报错
        delta["reasoning_content"] = reasoning
    if tool_calls:
        delta["tool_calls"] = tool_calls
    elif tool_name or tool_args:
        # 非增量形态：上游给的是完整一条，这里包成 tool_calls[0]
        delta["tool_calls"] = [{
            "index": 0, "type": "function",
            "function": {"name": tool_name, "arguments": tool_args},
        }]
    return {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": created or int(time.time()),
        "model": model_name,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }


def openai_usage_frame(*, chunk_id: str, model_name: str, usage: dict,
                       created: int | None = None,
                       credit: float | None = None) -> dict:
    """带 usage 的收尾帧。

    `credit` / `credits` / `cost` 三个同义键**都要给**：客户端底部显示
    「消耗 N 积分」读的是它们（不同客户端读不同键）。不给的话
    Qoder / Trae 的对话永远显示不出消耗。
    """
    out = {
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
    }
    if credit is not None:
        out["credit"] = credit
        out["credits"] = credit
        out["cost"] = credit
    return {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": created or int(time.time()),
        "model": model_name,
        "choices": [{"index": 0, "delta": {}, "finish_reason": None}],
        "usage": out,
    }


def sse(payload) -> bytes:
    """SSE 帧编码。`payload` 为 dict 时自动 JSON 序列化。"""
    text = payload if isinstance(payload, str) else json.dumps(
        payload, ensure_ascii=False)
    return ("data: %s\n\n" % text).encode()


def sse_done() -> bytes:
    return b"data: [DONE]\n\n"
