"""Qoder 对话转发：三个下游协议 → 上游信封 → 下游帧。

## 协议对照

| 下游 | 入参 | 出帧 |
|---|---|---|
| `/v1/chat/completions` | `{model, messages, stream, ...}` | OpenAI chunk |
| `/v1/messages` | Anthropic `{model, messages, system, max_tokens}` | Anthropic event |
| `/v1/responses` | `{model, input}` | Responses event |

## 上游是 OpenAI 兼容的（`format: openai`）

模型目录里 10 个 chat 模型的 `format` 全是 `openai`，
所以 OpenAI 那条路可以近乎直通，Anthropic / Responses 需要事件名映射。

## 两个必须在流里处理的点

1. **业务错误在信封里**（`statusCodeValue`），HTTP 永远 200 ——
   不检查就会把「额度耗尽」渲染成「空回复」
2. **`content` 与 `reasoning_content` 分开** ——
   思考内容要按下游协议决定放哪，不能直接拼进正文
"""
from __future__ import annotations

import json
import logging
import time
import uuid

from admin.platform.qoder.core import client, protocol
from admin.platform.qoder.services import accounts as qoder_accounts

logger = logging.getLogger("admin.platform.qoder.chat")

#: 上游帧里表示「思考中」的字段
REASONING_KEY = "reasoning_content"


def pick_model(catalog: dict, model_name: str, scene: str = "chat") -> dict | None:
    """按名字找模型目录条目。

    匹配顺序：`key` 精确 → `display_name` 精确 → `key`/名字不区分大小写。
    **不接受模糊前缀匹配** —— 静默降级到别的模型比报错更糟。
    """
    items = catalog.get(scene) or []
    if not model_name:
        # 没指定就取默认（`is_default`），再退第一个
        for item in items:
            if isinstance(item, dict) and item.get("is_default"):
                return item
        return items[0] if items and isinstance(items[0], dict) else None

    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("key") or "") == model_name:
            return item
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("display_name") or "") == model_name:
            return item
    lowered = model_name.lower()
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("key") or "").lower() == lowered:
            return item
        if str(item.get("display_name") or "").lower() == lowered:
            return item
    return None


def model_list(catalog: dict, scene: str = "chat") -> list[dict]:
    """目录 → OpenAI `/v1/models` 形状。"""
    out = []
    for item in catalog.get(scene) or []:
        if not isinstance(item, dict):
            continue
        if item.get("enable") is False:
            continue
        out.append({
            "id": str(item.get("key") or ""),
            "object": "model",
            "created": 0,
            "owned_by": "qoder",
            "name": str(item.get("display_name") or item.get("key") or ""),
            "x_qoder": {
                "display_name": item.get("display_name"),
                "format": item.get("format"),
                "is_reasoning": bool(item.get("is_reasoning")),
                "is_vl": bool(item.get("is_vl")),
                "max_input_tokens": item.get("max_input_tokens"),
                "price_factor": item.get("price_factor"),
                "is_free": bool(item.get("is_free")),
                # 能力字段：`describe_model` 靠这些拼描述
                "context_config": item.get("context_config") or {},
                "is_default": bool(item.get("is_default")),
                "is_sensitive": bool(item.get("is_sensitive")),
            },
        })
    return out


def _thinking_choice(body: dict) -> bool | None:
    """从下游请求里读「要不要思考」。

    两种写法都认：
      * OpenAI 风格 `reasoning_effort`（"none"/"minimal" = 关）
      * Anthropic 风格 `thinking: {type: "enabled"|"disabled"}`
    """
    thinking = body.get("thinking")
    if isinstance(thinking, dict):
        kind = str(thinking.get("type") or "").lower()
        if kind == "enabled":
            return True
        if kind == "disabled":
            return False
    effort = str(body.get("reasoning_effort") or "").lower()
    if effort:
        return effort not in ("none", "minimal", "off")
    return None


def _effort_of(body: dict) -> str:
    effort = str(body.get("reasoning_effort") or "").lower()
    if effort in ("low", "medium", "high"):
        return effort
    thinking = body.get("thinking")
    if isinstance(thinking, dict) and thinking.get("budget_tokens"):
        try:
            budget = int(thinking["budget_tokens"])
        except (TypeError, ValueError):
            return ""
        if budget <= 2048:
            return "low"
        if budget <= 8192:
            return "medium"
        return "high"
    return ""


def prepare(db, account, body: dict, *, scene: str = "chat",
            catalog: dict | None = None) -> dict:
    """准备一次对话：拿 token、找模型、组信封。"""
    data = qoder_accounts.ensure_token(db, account)
    token = qoder_accounts.token_of(data)
    realm = qoder_accounts.realm_of(account)
    uid = str(data.get("uid") or "")
    machine_id = qoder_accounts.machine_id_of(data)

    if catalog is None:
        catalog = client.fetch_model_catalog(
            token, realm, uid=uid, name=str(data.get("name") or ""),
            email=str(data.get("email") or ""), machine_id=machine_id)

    model = pick_model(catalog, str(body.get("model") or ""), scene)
    if model is None:
        raise client.UpstreamError(
            "模型不存在：%s" % body.get("model"), status=404, kind="model_unknown",
            detail=json.dumps(model_list(catalog, scene), ensure_ascii=False)[:400])

    messages = protocol.normalize_messages(body.get("messages") or [])
    if not messages:
        raise client.UpstreamError("messages 不能为空", status=400, kind="bad_request")

    upstream = protocol.build_upstream_body(
        model=model, messages=messages, tools=body.get("tools"),
        max_tokens=body.get("max_tokens") or body.get("max_completion_tokens"),
        thinking=_thinking_choice(body),
        reasoning_effort=_effort_of(body),
        session_seed=str(body.get("user") or body.get("session_id") or ""),
    )
    return {"token": token, "realm": realm, "uid": uid, "machine_id": machine_id,
            # 哨兵归因用：哪条账号出了错
            "account_id": int(getattr(account, "id", 0) or 0),
            "name": str(data.get("name") or ""), "email": str(data.get("email") or ""),
            "model": model, "upstream": upstream,
            "model_name": str(body.get("model") or model.get("key") or "")}


# ---------------------------------------------------------------------------
# 流式：OpenAI
# ---------------------------------------------------------------------------
def stream_openai(plan: dict, *, want_usage: bool = False,
                  holder: dict | None = None):
    """产出 OpenAI SSE 字节帧。最后一个 chunk 带 `usage`（若下游要）。

    `holder` 由网关传入：流结束时把累计 `usage` 写回去，供记账使用 ——
    生成器本身的返回值拿不到，而用量只能从最后一帧读。
    """
    holder = holder if isinstance(holder, dict) else {}
    chunk_id = "chatcmpl-" + uuid.uuid4().hex[:24]
    created = int(time.time())
    model_name = plan["model_name"]
    usage: dict = {}
    emitted_role = False

    for inner in client.iter_chat_stream(
            plan["token"], plan["realm"], uid=plan["uid"],
            body=plan["upstream"], name=plan["name"], email=plan["email"],
            machine_id=plan["machine_id"], account_id=plan.get("account_id", 0)):
        choices = inner.get("choices") or []
        if choices:
            choice = choices[0] or {}
            delta = choice.get("delta") or {}
            finish = choice.get("finish_reason")
            content = delta.get("content") or ""
            reasoning = delta.get(REASONING_KEY) or ""
            # **tool_calls 必须透传**：harness 客户端靠它继续执行任务。
            # 原来只取 content / reasoning，tool_calls 被丢弃，于是客户端
            # 只收到一个 `finish_reason=tool_calls` 的空收尾，
            # 表现为「思考完就结束，任务没开始跑」。
            tool_calls = delta.get("tool_calls")
            if not emitted_role and (content or reasoning or tool_calls):
                yield protocol.sse(protocol.openai_chunk(
                    chunk_id=chunk_id, model_name=model_name, created=created,
                    role="assistant"))
                emitted_role = True
            if tool_calls:
                yield protocol.sse(protocol.openai_chunk(
                    chunk_id=chunk_id, model_name=model_name, created=created,
                    tool_calls=tool_calls))
            if content or reasoning:
                yield protocol.sse(protocol.openai_chunk(
                    chunk_id=chunk_id, model_name=model_name, created=created,
                    content=content, reasoning=reasoning))
            if finish:
                yield protocol.sse(protocol.openai_chunk(
                    chunk_id=chunk_id, model_name=model_name, created=created,
                    finish_reason=str(finish)))
        frame_usage = inner.get("usage")
        if isinstance(frame_usage, dict) and frame_usage:
            usage = frame_usage

    # 估算积分：上游 usage 里没有 credit 时按倍率补，
    # 客户端底部「消耗 N 积分」读的就是这个字段。
    credit = 0.0
    try:
        from admin.db import SessionLocal
        from admin.platform.shared import credits_for

        _raw = usage.get("credit") if isinstance(usage, dict) else None
        credit = float(_raw) if isinstance(_raw, (int, float)) else 0.0
        if credit <= 0 and isinstance(usage, dict):
            _toks = (usage.get("total_tokens")
                     or usage.get("completion_tokens") or 0)
            storage = str(plan.get("storage") or plan.get("platform") or "qoder")
            with SessionLocal() as _db:
                credit = credits_for(_db, storage, model_name, _toks)
    except Exception:  # noqa: BLE001
        credit = 0.0
    if want_usage and usage:
        yield protocol.sse(protocol.openai_usage_frame(
            chunk_id=chunk_id, model_name=model_name, usage=usage,
            created=created, credit=credit))
    holder["usage"] = usage
    yield protocol.sse_done()


def collect_openai(plan: dict) -> dict:
    """非流式：把流收成一个完整 `chat.completion`。"""
    chunk_id = "chatcmpl-" + uuid.uuid4().hex[:24]
    created = int(time.time())
    model_name = plan["model_name"]
    parts: list[str] = []
    reasoning_parts: list[str] = []
    finish = "stop"
    usage: dict = {}

    for inner in client.iter_chat_stream(
            plan["token"], plan["realm"], uid=plan["uid"],
            body=plan["upstream"], name=plan["name"], email=plan["email"],
            machine_id=plan["machine_id"], account_id=plan.get("account_id", 0)):
        choices = inner.get("choices") or []
        if choices:
            choice = choices[0] or {}
            delta = choice.get("delta") or {}
            if delta.get("content"):
                parts.append(str(delta["content"]))
            if delta.get(REASONING_KEY):
                reasoning_parts.append(str(delta[REASONING_KEY]))
            if choice.get("finish_reason"):
                finish = str(choice["finish_reason"])
        frame_usage = inner.get("usage")
        if isinstance(frame_usage, dict) and frame_usage:
            usage = frame_usage

    message: dict = {"role": "assistant", "content": "".join(parts)}
    if reasoning_parts:
        message["reasoning_content"] = "".join(reasoning_parts)
    out = {
        "id": chunk_id,
        "object": "chat.completion",
        "created": created,
        "model": model_name,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
    }
    if usage:
        out["usage"] = {
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
        }
        # 上游给的积分是权威计费值，记账层优先用它。
        if isinstance(usage.get("credits"), (int, float)):
            out["usage"]["credits"] = float(usage["credits"])
    out["x_qoder_credits"] = usage.get("credits")
    return out
