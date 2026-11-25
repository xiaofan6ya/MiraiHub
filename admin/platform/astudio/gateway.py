"""AStudio `/v1/*` 转发热路径。

## 上游是 OpenAI Responses 协议

模型网关（`/model-gateway/v1/responses`）是标准 Responses 形态（桌面端
内核 wire_api 同款）。三条对外入口的处理：

    /v1/responses          → 请求/响应**原样透传**（协议同形，只换主机与凭证）
    /v1/chat/completions   → 请求转 Responses（messages → input 项、
                             tools → Responses 工具、max_tokens →
                             max_output_tokens），响应转回 chat.completion
    /v1/messages           → anthropic_to_openai（协议桥）→ 同上转
                             Responses；响应经 responses→chat 后再
                             openai_to_anthropic（协议桥）

协议桥只覆盖「Anthropic / Responses ↔ OpenAI chat」两个方向，**没有
「OpenAI chat → Responses 请求」与「Responses SSE → chat chunks」**——
这是 Responses 上游独有的两个方向，落在本模块（`_chat_to_responses` /
`_chat_stream_generator`），工具调用映射与 Anthropic 侧转换全部走桥，
不重复实现。

流式：/v1/responses 透传上游事件（含 event: 行，加 RESPONSES_PING 保活）；
chat/messages 入口先把 Responses 事件翻成 OpenAI chunk，再交给
协议桥的 responses_stream_relay / anthropic_stream_relay 转最终协议。
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import uuid
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.responses import StreamingResponse

from admin import http_shared
from admin.platform import protocol_bridge as bridge
from admin.platform import shared
from admin.platform.astudio.core import client, endpoints
from admin.platform.astudio.core.endpoints import MODEL_SLUGS, PUBLIC
from admin.platform.astudio.services import accounts as astudio_accounts

logger = logging.getLogger("admin.platform.astudio.gateway")

STORAGE = astudio_accounts.PLATFORM
MAX_ATTEMPTS = 3

PATHS = frozenset({
    "/v1/chat/completions",
    "/v1/responses",
    "/v1/messages",
    "/v1/messages/count_tokens",
    "/v1/models",
    "/v1/balance",
})

#: 模型目录按账号的 TTL 缓存（目录读取不计费，但没必要每次 /v1/models
#: 都打一次上游；60s 起，参考桌面端目录的更新节奏）。
CATALOG_TTL_S = 60.0
_CATALOG_CACHE: dict[int, dict] = {}
_CATALOG_LOCK = threading.Lock()


class _BadRequest(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _realm(request) -> str:
    _storage, realm, _spec = shared.locate(request, PUBLIC)
    return realm or "cn"


def _client_ip(request) -> str:
    try:
        from admin.client_ip import request_ip
        return request_ip(request)
    except Exception:  # noqa: BLE001
        return ""


def _fail(exc: client.UpstreamError) -> JSONResponse:
    status = exc.status if 400 <= exc.status <= 599 else 502
    return JSONResponse(
        {"error": {"message": exc.message, "type": exc.kind,
                   "code": exc.code}},
        status_code=status)


def anthropic_fail(exc: client.UpstreamError) -> JSONResponse:
    status = exc.status if 400 <= exc.status <= 599 else 502
    return JSONResponse({"type": "error",
                         "error": {"type": exc.kind, "message": exc.message}},
                        status_code=status)


async def _body_of(request: Request) -> dict:
    if request.scope.get("method", "GET").upper() in ("GET", "HEAD", "OPTIONS"):
        return {}
    cached = request.scope.get("_wb_body")
    if isinstance(cached, (bytes, bytearray)) and cached:
        try:
            data = json.loads(cached)
            return data if isinstance(data, dict) else {}
        except (ValueError, UnicodeDecodeError):
            return {}
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def _validate_model(body: dict) -> None:
    model = str(body.get("model") or "")
    if not model:
        raise _BadRequest("missing_model", "缺少 model 字段")
    if model not in MODEL_SLUGS:
        # 平台后缀已由 dispatch 中间件剥掉；到这里还不认识就是真不认识
        raise _BadRequest(
            "model_not_found",
            "模型 %s 不在 AStudio 可用目录（可用：%s）"
            % (model, ", ".join(sorted(MODEL_SLUGS))))


# ---------------------------------------------------------------------------
# OpenAI chat ↔ Responses 请求转换（Responses 上游独有的两个方向）
# ---------------------------------------------------------------------------
def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
        return "".join(parts)
    return str(content or "")


def _content_to_input_parts(content):
    """chat content（str / parts）→ Responses input 内容（str 或块列表）。"""
    if isinstance(content, str):
        return content if content else None
    if not isinstance(content, list):
        text = str(content or "")
        return text if text else None
    parts: list[dict] = []
    for block in content:
        if isinstance(block, str):
            if block:
                parts.append({"type": "input_text", "text": block})
            continue
        if not isinstance(block, dict):
            continue
        btype = str(block.get("type") or "text")
        if btype in ("text", "input_text"):
            text = str(block.get("text") or "")
            if text:
                parts.append({"type": "input_text", "text": text})
        elif btype == "image_url":
            url = str((block.get("image_url") or {}).get("url") or "")
            if url:
                parts.append({"type": "input_image", "image_url": url})
    return parts if parts else None


def _chat_to_responses(body: dict) -> dict:
    """OpenAI chat 请求体 → Responses 请求体。

    ## 关键映射（与协议桥的 `responses_to_openai` 严格互逆）

      * system / developer 消息 → 顶层 `instructions`（多条合并）；
      * role=tool 消息 → `function_call_output` 输入项（call_id 配对）；
      * assistant 的 tool_calls → 先 message 文本项、再逐个
        `function_call` 项（与 Responses 输出形状互逆）；
      * `tools[].function` → Responses 扁平工具定义；
      * `max_tokens` → `max_output_tokens`；`reasoning_effort` →
        `reasoning: {effort}`；`response_format.type` → `text.format`。
    """
    out = {"model": str(body.get("model") or "")}
    instructions: list[str] = []
    items: list[dict] = []
    for msg in body.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "").lower()
        content = msg.get("content")
        if role in ("system", "developer"):
            text = _content_text(content)
            if text:
                instructions.append(text)
        elif role == "tool":
            items.append({
                "type": "function_call_output",
                "call_id": str(msg.get("tool_call_id") or ""),
                "output": _content_text(content),
            })
        elif role == "assistant":
            calls = msg.get("tool_calls")
            if isinstance(calls, list) and calls:
                text = _content_text(content)
                if text:
                    items.append({"type": "message", "role": "assistant",
                                  "content": [{"type": "output_text",
                                               "text": text}]})
                for call in calls:
                    if not isinstance(call, dict):
                        continue
                    fn = call.get("function") \
                        if isinstance(call.get("function"), dict) else {}
                    items.append({
                        "type": "function_call",
                        "call_id": str(call.get("id") or ""),
                        "name": str(fn.get("name") or "unknown"),
                        "arguments": str(fn.get("arguments") or "{}"),
                    })
            else:
                parts = _content_to_input_parts(content)
                if parts is not None:
                    items.append({"type": "message", "role": "assistant",
                                  "content": parts})
        else:
            parts = _content_to_input_parts(content)
            if parts is not None:
                items.append({"type": "message", "role": role or "user",
                              "content": parts})
    if instructions:
        out["instructions"] = "\n".join(instructions)
    if items:
        out["input"] = items
    tools = []
    for tool in body.get("tools") or []:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function") if isinstance(tool.get("function"), dict) else None
        if fn is None:
            continue
        converted = {"type": "function", "name": str(fn.get("name") or "")}
        if isinstance(fn.get("parameters"), dict):
            converted["parameters"] = fn["parameters"]
        if isinstance(fn.get("description"), str) and fn["description"].strip():
            converted["description"] = fn["description"]
        if "strict" in fn:
            converted["strict"] = bool(fn["strict"])
        tools.append(converted)
    if tools:
        out["tools"] = tools
    choice = body.get("tool_choice")
    if isinstance(choice, dict) and choice.get("type") == "function":
        name = str((choice.get("function") or {}).get("name") or "")
        if name:
            out["tool_choice"] = {"type": "function", "name": name}
    elif choice in ("auto", "none", "required"):
        out["tool_choice"] = choice
    if body.get("max_tokens") is not None:
        out["max_output_tokens"] = int(body["max_tokens"])
    if body.get("reasoning_effort"):
        out["reasoning"] = {"effort": str(body["reasoning_effort"])}
    for key in ("temperature", "top_p", "stream"):
        if key in body:
            out[key] = body[key]
    rf = body.get("response_format")
    if isinstance(rf, dict) and rf.get("type"):
        out["text"] = {"format": {"type": str(rf["type"])}}
    if not out.get("input") and not out.get("instructions"):
        raise _BadRequest("empty_messages",
                          "messages 归一后为空，上游无法受理")
    return out


def _usage_to_chat(usage) -> dict:
    """Responses usage → OpenAI chat usage（缓存/推理明细透传）。"""
    usage = usage if isinstance(usage, dict) else {}
    prompt = int(usage.get("input_tokens") or 0)
    completion = int(usage.get("output_tokens") or 0)
    cached = int((usage.get("input_tokens_details") or {})
                 .get("cached_tokens") or 0)
    reasoning_tokens = int((usage.get("output_tokens_details") or {})
                           .get("reasoning_tokens") or 0)
    out = {"prompt_tokens": prompt, "completion_tokens": completion,
           "total_tokens": int(usage.get("total_tokens") or (prompt + completion))}
    if cached:
        out["prompt_tokens_details"] = {"cached_tokens": cached}
    if reasoning_tokens:
        out["completion_tokens_details"] = {"reasoning_tokens": reasoning_tokens}
    return out


def _responses_to_chat(result: dict, model: str) -> dict:
    """Responses 对象 → OpenAI chat.completion（非流式）。

    `output` 输出项：message → 正文、reasoning → 思考链、function_call →
    tool_calls；`status=failed` 时抛 UpstreamError（上游明确报错不能
    伪装成一次成功的完成）。
    """
    result = result if isinstance(result, dict) else {}
    status = str(result.get("status") or "completed")
    if status == "failed":
        err = result.get("error")
        message = (str(err.get("message") or "上游返回失败状态")
                   if isinstance(err, dict) else "上游返回失败状态")
        raise client.UpstreamError("AStudio 上游返回失败：%s" % message,
                                   status=502, kind="upstream")
    output = result.get("output")
    items = output if isinstance(output, list) else []
    texts: list[str] = []
    reasoning: list[str] = []
    calls: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "")
        if kind == "message":
            for part in item.get("content") or []:
                if isinstance(part, dict) and part.get("type") in (
                        "output_text", "input_text", "text"):
                    texts.append(str(part.get("text") or ""))
        elif kind == "reasoning":
            for part in item.get("summary") or []:
                if isinstance(part, dict) and part.get("type") == "summary_text":
                    reasoning.append(str(part.get("text") or ""))
        elif kind == "function_call":
            calls.append({
                "id": str(item.get("call_id") or item.get("id") or ""),
                "type": "function",
                "function": {"name": str(item.get("name") or "unknown"),
                             "arguments": str(item.get("arguments") or "{}")},
            })
    message = {"role": "assistant", "content": "".join(texts) or None}
    if reasoning:
        message["reasoning_content"] = "".join(reasoning)
    if calls:
        message["tool_calls"] = calls
    # Responses 的 completed 不区分「说完」与「要调工具」：输出里有
    # function_call 项就得标 tool_calls，否则下游（chat 客户端 / 桥的
    # anthropic 方向）会把工具调用当普通结尾（stop_reason=end_turn）。
    if status == "incomplete":
        finish = "length"
    elif calls:
        finish = "tool_calls"
    else:
        finish = "stop"
    return {
        "id": "chatcmpl-" + str(result.get("id") or uuid.uuid4().hex[:24]),
        "object": "chat.completion",
        "created": int(result.get("created_at") or time.time()),
        "model": model or str(result.get("model") or ""),
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": _usage_to_chat(result.get("usage")),
    }


# ---------------------------------------------------------------------------
# 流式：Responses 事件 → OpenAI chunks（chat/messages 入口用）
# ---------------------------------------------------------------------------
def _iter_responses_events(upstream):
    """上游 SSE 字节流 → (event_name, data) 序列。

    Responses 流的标准形态是 `event:` 行 + `data:` 行；有些网关只发
    `data:` 行（事件名在 payload 的 type 里），两种都认。
    """
    event_name = ""
    for raw in upstream.iter_lines():
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        stripped = line.strip()
        if not stripped:
            event_name = ""
            continue
        if stripped.startswith("event:"):
            event_name = stripped[6:].strip()
            continue
        if stripped.startswith(":"):
            continue
        if not stripped.startswith("data:"):
            continue
        payload = stripped[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            data = json.loads(payload)
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        yield str(data.get("type") or event_name or ""), data
        event_name = ""


def _chat_stream_generator(upstream, model: str, holder: dict):
    """Responses 事件流 → OpenAI chunk SSE（逐事件翻译，结束时收尾）。

    文本 / 思考 / 工具参数分别对应 output_text / reasoning_summary /
    function_call_arguments 三类增量事件；`response.completed`（或
    incomplete / failed）触发终止帧 + usage + [DONE]。上游没发终止事件
    就断流的，补一帧正常终止，严格客户端不会挂死。
    """
    msg_id = "chatcmpl-astudio-" + uuid.uuid4().hex[:16]
    created = int(time.time())

    def frame(delta, finish=None, usage=None) -> bytes:
        payload = {"id": msg_id, "object": "chat.completion.chunk",
                   "created": created, "model": model,
                   "choices": [{"index": 0, "delta": delta,
                                "finish_reason": finish}]}
        if usage is not None:
            payload["usage"] = usage
        return ("data: %s\n\n" % json.dumps(payload,
                                            ensure_ascii=False)).encode()

    def error_frame(message: str) -> bytes:
        return ("data: %s\n\n" % json.dumps(
            {"error": {"message": message, "type": "upstream_error"}},
            ensure_ascii=False)).encode()

    def terminal(error: str = "") -> list:
        out = []
        if error:
            holder["error"] = error
            out.append(error_frame("上游连接中断，请重试"))
        out.append(frame({}, finish="stop"))
        out.append(b"data: [DONE]\n\n")
        return out

    yield frame({"role": "assistant", "content": ""})
    slots: dict[int, dict] = {}
    for etype, data in _iter_responses_events(upstream):
        if etype == "response.output_text.delta":
            piece = str(data.get("delta") or "")
            if piece:
                yield frame({"content": piece})
        elif etype == "response.reasoning_summary_text.delta":
            piece = str(data.get("delta") or "")
            if piece:
                yield frame({"reasoning_content": piece})
        elif etype == "response.output_item.added":
            item = data.get("item") if isinstance(data.get("item"), dict) else {}
            if str(item.get("type") or "") != "function_call":
                continue
            idx = int(data.get("output_index") or 0)
            slot = slots.setdefault(idx, {"id": "", "name": ""})
            call_id = str(item.get("call_id") or item.get("id") or "")
            name = str(item.get("name") or "")
            if call_id:
                slot["id"] = call_id
            if name:
                slot["name"] = name
            if call_id and name:
                yield frame({"tool_calls": [{
                    "index": idx, "id": call_id, "type": "function",
                    "function": {"name": name, "arguments": ""}}]})
        elif etype == "response.function_call_arguments.delta":
            idx = int(data.get("output_index") or 0)
            slot = slots.setdefault(idx, {"id": "", "name": ""})
            piece = str(data.get("delta") or "")
            if not piece:
                continue
            call = {"index": idx, "type": "function",
                    "function": {"arguments": piece}}
            if slot["id"]:
                call["id"] = slot["id"]
            yield frame({"tool_calls": [call]})
        elif etype in ("response.completed", "response.incomplete"):
            resp = data.get("response") \
                if isinstance(data.get("response"), dict) else {}
            usage = _usage_to_chat(resp.get("usage"))
            if usage:
                holder["usage"] = usage
            finish = "length" if etype == "response.incomplete" else "stop"
            yield frame({}, finish=finish, usage=usage or None)
            yield b"data: [DONE]\n\n"
            return
        elif etype == "response.failed":
            err = data.get("error") if isinstance(data.get("error"), dict) else {}
            holder["error"] = "upstream"
            yield error_frame(str(err.get("message") or "上游返回失败"))
            yield frame({}, finish="stop")
            yield b"data: [DONE]\n\n"
            return
        elif etype == "error" or data.get("error"):
            err = data.get("error") if isinstance(data.get("error"), dict) else {}
            holder["error"] = "upstream"
            yield error_frame(str(err.get("message") or "上游流错误"))
            yield frame({}, finish="stop")
            yield b"data: [DONE]\n\n"
            return
    # 上游没发 completed 就断流：补正常终止帧（holder 记 network 避免记成功）
    holder["error"] = holder.get("error") or "stream_interrupted"
    for piece in terminal():
        yield piece


def _responses_passthrough(upstream):
    """/v1/responses 入口：上游事件流原样透传（只重编码换行）。"""
    for raw in upstream.iter_lines():
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        yield (line + "\n").encode()
    yield b"\n"


# ---------------------------------------------------------------------------
# 选号与调用
# ---------------------------------------------------------------------------
def _attempt(db, request, key, body: dict, realm: str):
    """选号 + 租约 + 调上游（非流式）。换号重试语义与其它平台相同。"""
    model = str(body.get("model") or "")
    exclude: set[int] = set()
    last_error: client.UpstreamError | None = None
    for _ in range(MAX_ATTEMPTS):
        account = shared.select_account(
            db, storage=STORAGE, realm=realm, key=key,
            model=model, exclude_ids=exclude)
        if account is None:
            break
        if not shared.lease(account):
            exclude.add(account.id)
            continue
        started = time.perf_counter()
        try:
            token = astudio_accounts.token_of(
                astudio_accounts.credentials_of(account))
            result = client.request_responses(
                token, body, account_id=int(account.id or 0))
            return result, account, started
        except client.UpstreamError as exc:
            shared.release(account)
            shared.note_failure(
                db, account, kind=exc.kind, detail=exc.detail or exc.message,
                status=exc.status, model=model, public_platform=PUBLIC,
                code=exc.code,
                cool_seconds=(600 if exc.kind in ("auth", "quota") else 0))
            last_error = exc
            exclude.add(account.id)
            if exc.status in (400, 422) and exc.kind not in ("auth", "quota", "rate"):
                break
        except Exception as exc:  # noqa: BLE001
            shared.release(account)
            logger.warning("AStudio 调用失败 acc=%s: %s",
                           account.id, type(exc).__name__)
            last_error = client.UpstreamError(
                "AStudio 调用异常（%s）" % type(exc).__name__, kind="upstream")
            exclude.add(account.id)
    if last_error is not None:
        raise last_error
    raise shared.PlatformRejected(
        503, "AStudio 没有可用账号（请先在控制台扫描/导入凭证并确认状态为启用）",
        kind="server_error", code="no_available_account",
        headers={"Retry-After": "3"})


def _bill(key, account, model, usage, *, request, started=None, error="",
          stats: dict | None = None):
    try:
        from admin.db import SessionLocal

        elapsed = int((time.perf_counter() - started) * 1000) if started else None
        ttfb = total = 0
        if isinstance(stats, dict):
            ttfb = int(stats.get("ttfb_ms") or 0)
            total = int(stats.get("total_ms") or 0)
        if not (0 < ttfb <= (total or elapsed or 0)):
            ttfb = 0
        latency = max(total, elapsed or 0) or None
        if not ttfb:
            ttfb = latency
        with SessionLocal() as db:
            shared.record_usage(
                db,
                key_id=int(getattr(key, "id", 0) or 0),
                account_id=int(getattr(account, "id", 0) or 0),
                model=model,
                usage=usage if isinstance(usage, dict) else {},
                public_platform=PUBLIC,
                storage=STORAGE,
                error_kind=error, client_ip=_client_ip(request),
                use_case="astudio-chat", seq=1,
                ttfb_ms=ttfb, latency_ms=latency,
                reasoning_effort=shared.reasoning_effort_of_request(request))
    except Exception:  # noqa: BLE001
        logger.exception("AStudio 记账失败")


async def _forward(body: dict, request) -> tuple[dict, Any, float, Any]:
    """非流式：选号 + 调用 + 收尾，返回 (result, account, started, key)。

    错误统一抛 client.UpstreamError / shared.PlatformRejected，由入口
    转对应协议的错误体。
    """
    from admin.db import SessionLocal

    realm = _realm(request)
    db = SessionLocal()
    try:
        key = shared.authenticate(db, request, platform=PUBLIC)
        # 选号 + 建连 + 读完整响应全程同步：留在事件循环上就是
        # 「一次上游卡顿，全站停摆」，必须进工作线程。
        result, account, started = await asyncio.to_thread(
            _attempt, db, request, key, body, realm)
        shared.release(account)
        shared.note_success(account)
        try:
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
        return result, account, started, key
    finally:
        db.close()


async def _forward_stream(body: dict, request, *, chat_relay: bool,
                          model: str):
    """流式：选号 → 上游 SSE → 翻译中继 → 收尾记账。

    `chat_relay=True`：把 Responses 事件翻成 OpenAI chunk 流
    （chat/messages 入口）；否则原样透传（/v1/responses 入口）。
    """
    from admin.db import SessionLocal

    realm = _realm(request)
    db = SessionLocal()
    key = shared.authenticate(db, request, platform=PUBLIC)
    started = time.perf_counter()
    stats: dict = {}
    exclude: set[int] = set()
    account = None
    upstream = None
    last_error: client.UpstreamError | None = None
    for _ in range(MAX_ATTEMPTS):
        account = await asyncio.to_thread(shared.select_account,
            db, storage=STORAGE, realm=realm, key=key,
            model=model, exclude_ids=exclude)
        if account is None:
            break
        if not shared.lease(account):
            exclude.add(account.id)
            continue
        try:
            token = astudio_accounts.token_of(
                astudio_accounts.credentials_of(account))
            upstream = await http_shared.run_blocking(client.open_responses_stream,
                token, body, account_id=int(account.id or 0), stats=stats,
                           on_orphan=http_shared.close_quietly)
            break
        except client.UpstreamError as exc:
            shared.release(account)
            await asyncio.to_thread(shared.note_failure,
                db, account, kind=exc.kind, detail=exc.detail or exc.message,
                status=exc.status, model=model, public_platform=PUBLIC,
                code=exc.code,
                cool_seconds=(600 if exc.kind in ("auth", "quota") else 0))
            last_error = exc
            exclude.add(account.id)
            if exc.status in (400, 422) and exc.kind not in ("auth", "quota", "rate"):
                break
        except Exception as exc:  # noqa: BLE001
            shared.release(account)
            logger.warning("AStudio 流式调用失败 acc=%s: %s",
                           account.id, type(exc).__name__)
            last_error = client.UpstreamError(
                "AStudio 调用异常（%s）" % type(exc).__name__, kind="upstream")
            exclude.add(account.id)

    if upstream is None:
        db.close()
        if last_error is not None:
            return _fail(last_error)
        raise shared.PlatformRejected(
            503, "AStudio 没有可用账号（请先在控制台扫描/导入凭证并确认状态为启用）",
            kind="server_error", code="no_available_account",
            headers={"Retry-After": "3"})

    holder: dict = {}
    finish_lock = threading.Lock()
    finished: list = []

    def _finish():
        # 第 4 轮 R4-BE-05：原 finally 抽出来，生成器 finally 与「内层未启动」钩子共用，
        # 加锁只执行一次。
        with finish_lock:
            if finished:
                return
            finished.append(1)
        client.close_stream(upstream)
        try:
            shared.release(account)
            # 流内错误不是「调用成功」：别把坏响应记成绿勾
            if not holder.get("error"):
                shared.note_success(account)
            db.commit()
        except Exception:  # noqa: BLE001
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
        try:
            _bill(key, account, model, holder.get("usage") or {},
                  request=request, started=started, stats=stats,
                  error=str(holder.get("error") or ""))
        finally:
            db.close()

    def _release_unstarted():
        holder.setdefault("error", "client_disconnected")
        _finish()

    def generator():
        try:
            if chat_relay:
                yield from _chat_stream_generator(upstream, model, holder)
            else:
                yield from _responses_passthrough(upstream)
        except client.UpstreamError as exc:
            holder["error"] = exc.kind or "upstream_error"
            if chat_relay:
                yield ("data: %s\n\n" % json.dumps(
                    {"error": {"message": exc.message, "type": exc.kind}},
                    ensure_ascii=False)).encode()
                yield ("data: %s\n\n" % json.dumps(
                    {"id": "chatcmpl-astudio-error",
                     "object": "chat.completion.chunk",
                     "created": int(time.time()), "model": model,
                     "choices": [{"index": 0, "delta": {},
                                  "finish_reason": "stop"}]},
                    ensure_ascii=False)).encode()
                yield b"data: [DONE]\n\n"
            else:
                # 透传入口的中断：补 response.failed，客户端能区分
                # 「上游报错」与「流悄悄断了」
                yield ("event: response.failed\ndata: %s\n\n" % json.dumps(
                    {"type": "response.failed",
                     "response": {"id": "resp-astudio-error",
                                  "object": "response",
                                  "created_at": int(time.time()),
                                  "status": "failed", "output": [],
                                  "error": {"message": exc.message}}},
                    ensure_ascii=False)).encode()
        except Exception as exc:  # noqa: BLE001 - 断流 / socket 超时 / 解码失败
            holder["error"] = "network"
            logger.warning("AStudio 流中断 acc=%s: %s",
                           getattr(account, "id", ""), str(exc)[:200])
        finally:
            _finish()

    ping = bridge.CHAT_PING if chat_relay else bridge.RESPONSES_PING
    # P4：异步桥（不占 anyio 令牌；断连后停读上游并 close 生成器）
    safe = http_shared.async_stream(generator(), ping=ping)
    # /v1/messages 会把它交给 anthropic_stream_relay：中继一帧没读时 generator
    # 不会启动，钩子在守护线程里收尾（释放账号 + 记 client_disconnected）。
    # 第 5 轮（A6）：chat / responses 直连返回时，响应头发送失败同样一帧不读 ——
    # `guarded_stream_response` 在自身 __call__ 的 finally 里也调这个钩子。
    return bridge.guarded_stream_response(
        safe, _release_unstarted, name="astudio-unstarted",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------------------
# 入口端点
# ---------------------------------------------------------------------------
async def chat_completions(request: Request):
    body = await _body_of(request)
    model = str(body.get("model") or "")
    _validate_model(body)
    try:
        responses_body = _chat_to_responses(body)
    except _BadRequest as exc:
        return JSONResponse(
            {"error": {"message": exc.message, "type": "invalid_request_error",
                       "code": exc.code}},
            status_code=400)
    if body.get("stream"):
        return await _forward_stream(responses_body, request,
                                     chat_relay=True, model=model)
    result, account, started, key = await _forward(responses_body, request)
    try:
        chat = _responses_to_chat(result, model)
    except client.UpstreamError as exc:
        return _fail(exc)
    await asyncio.to_thread(_bill, key, account, model, chat.get("usage") or {},
          request=request, started=started)
    return JSONResponse(chat)


async def responses(request: Request):
    body = await _body_of(request)
    model = str(body.get("model") or "")
    _validate_model(body)
    if body.get("stream"):
        return await _forward_stream(body, request, chat_relay=False,
                                     model=model)
    result, account, started, key = await _forward(body, request)
    await asyncio.to_thread(_bill, key, account, model, _usage_to_chat(result.get("usage")),
          request=request, started=started)
    return JSONResponse(result)


async def messages(request: Request):
    body = await _body_of(request)
    model = str(body.get("model") or "")
    _validate_model(body)
    try:
        chat_body = bridge.anthropic_to_openai(body)
        responses_body = _chat_to_responses(chat_body)
    except (ValueError, _BadRequest) as exc:
        message = str(getattr(exc, "message", exc) or exc)
        return JSONResponse(
            {"type": "error",
             "error": {"type": "invalid_request_error", "message": message}},
            status_code=400)
    if body.get("stream"):
        inner = await _forward_stream(responses_body, request,
                                      chat_relay=True, model=model)
        if getattr(inner, "status_code", 200) >= 400:
            return inner
        return bridge.anthropic_stream_relay(inner, model, prefix="astudio")
    result, account, started, key = await _forward(responses_body, request)
    try:
        chat = _responses_to_chat(result, model)
        anthropic = bridge.openai_to_anthropic(chat, model)
    except client.UpstreamError as exc:
        return anthropic_fail(exc)
    await asyncio.to_thread(_bill, key, account, model, chat.get("usage") or {},
          request=request, started=started)
    return JSONResponse(anthropic)


async def count_tokens(request: Request):
    """本地估算（上游无此接口）。"""
    body = await _body_of(request)
    parts = []
    if isinstance(body.get("system"), str):
        parts.append(body["system"])
    for message in body.get("messages") or []:
        if isinstance(message, dict):
            content = message.get("content")
            parts.append(content if isinstance(content, str) else str(content or ""))
    text = "\n".join(parts)
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return JSONResponse({"input_tokens": cjk + max(1, (len(text) - cjk) // 4)})


# ---------------------------------------------------------------------------
# 模型目录
# ---------------------------------------------------------------------------
def _catalog_row(entry: dict) -> dict:
    slug = str(entry.get("slug") or "")
    name = str(entry.get("display_name") or slug)
    try:
        context = int(entry.get("context_window") or 0)
    except (TypeError, ValueError):
        context = 0
    try:
        output = int(entry.get("max_output_tokens") or 32768)
    except (TypeError, ValueError):
        output = 32768
    modalities = entry.get("input_modalities")
    inputs = ["text"]
    if isinstance(modalities, list) and "image" in modalities:
        inputs.append("image")
    efforts = entry.get("supported_reasoning_levels")
    reasoning = False
    if isinstance(efforts, list):
        reasoning = any(
            isinstance(level, dict) and str(level.get("effort") or "") != "none"
            for level in efforts)
    badge = entry.get("badge")
    free = bool(isinstance(badge, dict)
                and (badge.get("text") or {}).get("en") == "Free")
    return {
        "id": slug, "object": "model", "owned_by": PUBLIC,
        "name": name, "display_name": name,
        "description": str(entry.get("description") or ""),
        "contextWindow": context, "maxInputTokens": context,
        "maxOutputTokens": output, "max_tokens": output,
        "input": inputs, "reasoning": reasoning, "free": free,
        "visible": True,
    }


def _static_catalog_rows() -> list:
    return [_catalog_row(dict(m, supported_reasoning_levels=[
        {"effort": "high"}], badge={"text": {"en": "Free" if m.get("free")
                                             else ""}}))
            for m in endpoints.BOOTSTRAP_MODELS]


def _catalog_cached(account_id: int, token: str) -> list:
    """按账号拉实时目录（TTL 缓存）。读失败回落静态启动表。"""
    now = time.monotonic()
    with _CATALOG_LOCK:
        cached = _CATALOG_CACHE.get(account_id)
        if cached is not None and now - cached["at"] < CATALOG_TTL_S:
            return cached["rows"]
    try:
        entries = client.fetch_catalog(token, account_id=account_id)
    except client.UpstreamError:
        return _static_catalog_rows()
    catalog_slugs = {str(m.get("slug") or "")
                     for m in entries if isinstance(m, dict)}
    rows = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("visibility") == "hidden" \
                or entry.get("supported_in_api") is False:
            continue
        rows.append(_catalog_row(entry))
    if not rows:
        # 过滤后为空不塌方：回落静态表
        rows = _static_catalog_rows()
    else:
        # 补位：目录里没出现过这个名字才补（出现即以上游为准，
        # 包括被 visibility / supported_in_api 过滤掉的「下架」表态）
        for extra in endpoints.EXTRA_CATALOG:
            if extra["slug"] not in catalog_slugs:
                rows.append(_catalog_row(extra))
    with _CATALOG_LOCK:
        _CATALOG_CACHE[account_id] = {"at": time.monotonic(), "rows": rows}
    return rows


async def list_models(request: Request):
    from admin.db import SessionLocal

    with SessionLocal() as db:
        shared.authenticate(db, request, platform=PUBLIC)
        realm = _realm(request)
        account = await asyncio.to_thread(shared.select_account,
            db, storage=STORAGE, realm=realm,
            key=shared.key_row(db, request))
        if account is None:
            return JSONResponse({
                "object": "list",
                "data": _static_catalog_rows(),
                "note": "号池没有可用 AStudio 账号，仅返回内置目录"})
        token = astudio_accounts.token_of(
            astudio_accounts.credentials_of(account))
        rows = _catalog_cached(int(account.id or 0), token)
    return JSONResponse({"object": "list", "data": rows})


async def balance(request: Request):
    from admin.db import SessionLocal

    with SessionLocal() as db:
        shared.authenticate(db, request, platform=PUBLIC)
        realm = _realm(request)
        account = await asyncio.to_thread(shared.select_account,
            db, storage=STORAGE, realm=realm,
            key=shared.key_row(db, request))
        if account is None:
            return JSONResponse({"platform": PUBLIC, "realm": realm,
                                 "reachable": False, "models": len(MODEL_SLUGS),
                                 "note": "号池没有可用 AStudio 账号"},
                                status_code=503)
        try:
            quota = await asyncio.to_thread(astudio_accounts.sync_quota, db, account)
        except client.UpstreamError as exc:
            return _fail(exc)
        if not quota.get("ok"):
            return JSONResponse({"platform": PUBLIC, "realm": realm,
                                 "reachable": False, "models": len(MODEL_SLUGS),
                                 "note": quota.get("error") or "余额不可用"},
                                status_code=200)
    return JSONResponse({"platform": PUBLIC, "realm": realm, "reachable": True,
                         "available": quota.get("remain") or 0,
                         "total": quota.get("total") or 0,
                         "models": len(MODEL_SLUGS),
                         "unit": "credits",
                         "plan": quota.get("plan") or ""})


_HANDLERS = {
    "/v1/chat/completions": chat_completions,
    "/v1/messages": messages,
    "/v1/messages/count_tokens": count_tokens,
    "/v1/responses": responses,
    "/v1/models": list_models,
    "/v1/balance": balance,
}


async def dispatch(request: Request, path: str):
    """`PlatformDispatchMiddleware` 的唯一入口（**必须 async**）。"""
    handler = _HANDLERS.get(path)
    if handler is None:
        return JSONResponse(
            {"error": {"message": "AStudio 不支持 %s" % path,
                       "type": "invalid_request_error"}},
            status_code=404)
    try:
        return await handler(request)
    except shared.PlatformRejected as exc:
        if path in ("/v1/messages", "/v1/messages/count_tokens"):
            return exc.anthropic()
        return exc.openai()
    except _BadRequest as exc:
        if path.startswith("/v1/messages"):
            return JSONResponse(
                {"type": "error",
                 "error": {"type": "invalid_request_error", "message": exc.message}},
                status_code=400)
        return JSONResponse(
            {"error": {"message": exc.message, "type": "invalid_request_error",
                       "code": exc.code}},
            status_code=400)
