"""腾讯 Marvis 的 `/v1/*` 转发实现。

## 上游就是 OpenAI 协议

Marvis 的模型网关 `POST /v1/chat/completions` 本身就是 OpenAI 兼容的（SSE 流式、
`finish_reason`、`tool_calls`、`reasoning_content` 都在原位），所以这里的活主要是：

  1. **模型 id 归一** —— 用户传的是显示名（`deepseek-v4-1-flash-261017`），
     上游只认槽位名（`main-auto`）。直接透传真实模型名会 400。
  2. **鉴权头** —— 不是 Bearer token，而是 `Ual-Access-*` 那一组（见 core/endpoints）。
  3. **流式原样转发** —— 不在这里读干流。
  4. **记账** —— 落 usage_logs，控制台的用量与积分统计靠它。

## 为什么流式要用线程池读

`core/client.py` 走的是 `urllib`（同步）。在 async handler 里直接 `stream.read()`
会把整个事件循环按秒级卡住 —— 一个慢速长回答就能让全网关停摆。这里用
`run_in_executor` 把每块读放到工作线程，事件循环照常服务其它请求。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse

from admin.platform import shared
from admin.platform.marvis.core import client, endpoints
from admin.platform.marvis.services import accounts as marvis_accounts

logger = logging.getLogger("admin.platform.marvis.gateway")

PUBLIC = endpoints.PLATFORM
STORAGE = marvis_accounts.PLATFORM
MAX_ATTEMPTS = 3

PATHS = frozenset({
    "/v1/chat/completions",
    "/v1/responses",
    "/v1/messages",
    "/v1/messages/count_tokens",
    "/v1/models",
    "/v1/balance",
})


class _BadRequest(Exception):
    """本地就能判定的错请求（模型不可用 / 空 messages）。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _realm(request) -> str:
    """Marvis 只有国内区。保留这个函数是为了与其它平台同形。"""
    return "cn"


def _client_ip(request) -> str:
    try:
        forwarded = request.headers.get("x-forwarded-for") or ""
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else ""
    except Exception:  # noqa: BLE001
        return ""


def _fail(exc: client.UpstreamError) -> JSONResponse:
    status = exc.status or 502
    if status < 400 or status > 599:
        status = 502
    return JSONResponse(
        {"error": {"type": exc.kind, "code": exc.code or "upstream_error",
                   "message": exc.message}},
        status_code=status)


async def _body_of(request: Request) -> dict:
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def _validate_model(body: dict) -> str:
    """归一模型 id，并在本地拦掉已知持续失败的模型。

    ⚠ 不可用模型返回 **404**（模型级错误），不是 401。
    401 会被上层当成「账号登录失效」去 failover 到每一个账号，
    一个坏模型就能把整池好号全标成失效。
    """
    raw = str(body.get("model") or endpoints.DEFAULT_MODEL)
    slot = endpoints.canonical_model_id(raw)
    if endpoints.is_unavailable_model(slot):
        raise _BadRequest("model_not_found",
                          "模型 %s 在 Marvis 上游不可用（已确认持续失败）" % raw)
    if slot not in endpoints.DISPLAY_BY_SLOT:
        raise _BadRequest("model_not_found",
                          "未知模型 %s；可用：%s" % (
                              raw, "、".join(endpoints.DISPLAY_BY_SLOT)))
    if not (body.get("messages") or body.get("input")):
        raise _BadRequest("invalid_request", "请求缺少 messages")
    return slot


def _bill(key, account, model, usage, *, request, started=None, error="",
          stats: dict | None = None):
    """落一条 usage_logs。**绝不抛**（记账失败不能影响转发）。"""
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
                use_case="marvis-chat", seq=1,
                ttfb_ms=ttfb, latency_ms=latency,
                reasoning_effort=shared.reasoning_effort_of_request(request))
    except Exception:  # noqa: BLE001
        logger.exception("Marvis 记账失败")


def _prepare_body(body: dict, slot: str) -> dict:
    """把下游请求整理成上游能接受的样子。

    · `model` 换成槽位名；
    · 流式时补 `stream_options.include_usage` —— 少了它上游不返回 usage 帧，
      这张卡片和用量统计就永远是 0。
    """
    out = dict(body)
    out["model"] = slot
    if out.get("stream"):
        opts = dict(out.get("stream_options") or {})
        opts["include_usage"] = True
        out["stream_options"] = opts
    return out


def _attempt(db, request, key, body: dict, realm: str, *, stream: bool):
    """选号 + 租约 + 调上游。换号语义与其它平台一致。"""
    model = str(body.get("model") or "")
    exclude: set[int] = set()
    last_error: client.UpstreamError | None = None
    for _ in range(MAX_ATTEMPTS):
        # `min_balance=0` = 只要余额 > 0 的号。
        #
        # Marvis 是令牌钱包制：额度用完的号凭证**是好的**，但每次调用都会被上游
        # 打回。不在这里挡掉的话，只能靠「撞一次 → 冷却 → 换号」，而冷却到期后
        # 它又排回队首（这些号从没成功过，`last_used_at` 恒为 NULL，选号按它升序）
        # —— 用户感受到的就是「一直失败，用不了」。NULL 余额（从没同步过）仍放行，
        # 否则新导入的号会被全部误杀。
        account = shared.select_account(
            db, storage=STORAGE, realm=realm, key=key,
            model=model, exclude_ids=exclude, min_balance=0)
        if account is None:
            break
        if not shared.lease(account):
            exclude.add(account.id)
            continue
        started = time.perf_counter()
        try:
            data = marvis_accounts.ensure_token(db, account)
            if stream:
                resp = client.open_chat_stream(
                    data, realm, body, account_id=int(account.id or 0))
                return resp, account, started
            result = client.chat_completions(
                data, realm, body, account_id=int(account.id or 0))
            shared.note_success(account)
            shared.release(account)
            return result, account, started
        except client.UpstreamError as exc:
            shared.release(account)
            shared.note_failure(
                db, account, kind=exc.kind, detail=exc.detail or exc.message,
                status=exc.status, model=model, public_platform=PUBLIC,
                cool_seconds=(600 if exc.kind in ("auth", "risk_control") else 0))
            last_error = exc
            exclude.add(account.id)
            # 参数类错误换号也没用，直接停
            if exc.status in (400, 422) and exc.kind not in (
                    "auth", "rate_limit", "risk_control"):
                break
        except Exception as exc:  # noqa: BLE001
            shared.release(account)
            logger.warning("Marvis 调用失败 acc=%s: %s",
                           account.id, type(exc).__name__)
            last_error = client.UpstreamError(
                "Marvis 调用异常（%s）" % type(exc).__name__, kind="upstream")
            exclude.add(account.id)
    if last_error is not None:
        raise last_error
    raise shared.PlatformRejected(
        503, "Marvis 没有可用账号（请先在控制台导入凭证并确认状态为启用）",
        kind="server_error", code="no_available_account",
        headers={"Retry-After": "3"})


async def _iter_upstream(resp):
    """把同步 `HTTPResponse` 的 read 挪到工作线程，避免卡住事件循环。"""
    loop = asyncio.get_running_loop()
    while True:
        chunk = await loop.run_in_executor(None, resp.read, 8192)
        if not chunk:
            break
        yield chunk


def _usage_of(result: dict) -> dict:
    usage = result.get("usage") if isinstance(result, dict) else None
    return usage if isinstance(usage, dict) else {}


async def chat_completions(request: Request):
    """OpenAI 兼容入口 —— 本平台的唯一主路径。"""
    body = await _body_of(request)
    realm = _realm(request)
    try:
        slot = _validate_model(body)
    except _BadRequest as exc:
        return JSONResponse({"error": {"type": exc.code, "message": exc.message,
                                       "code": exc.code}},
                            status_code=404 if exc.code == "model_not_found" else 400)

    upstream_body = _prepare_body(body, slot)
    wants_stream = bool(body.get("stream"))

    from admin.db import SessionLocal

    db = SessionLocal()
    try:
        key = shared.authenticate(db, request, platform=PUBLIC)
        resp, account, started = _attempt(db, request, key, upstream_body, realm,
                                          stream=wants_stream)
    except shared.PlatformRejected as exc:
        db.close()
        # `openai()` 本身返回的就是 **JSONResponse**，不要再包一层 ——
        # 包了会 TypeError: Object of type JSONResponse is not JSON serializable。
        return exc.openai()
    except client.UpstreamError as exc:
        db.close()
        return _fail(exc)
    except Exception:
        db.close()
        raise
    db.close()

    if not wants_stream:
        try:
            usage = _usage_of(resp)
            _bill(key, account, slot, usage, request=request, started=started)
            return JSONResponse(resp)
        except Exception:  # noqa: BLE001
            raise

    async def generator():
        stats = {"ttfb_ms": 0, "total_ms": 0}
        usage: dict = {}
        first_at = None
        began = time.perf_counter()
        buffer = b""
        try:
            async for chunk in _iter_upstream(resp):
                if first_at is None:
                    first_at = time.perf_counter()
                    stats["ttfb_ms"] = int((first_at - started) * 1000)
                yield chunk
                # 顺手把最后一个 usage 帧记下来 —— 不需要改动转发的字节
                buffer += chunk
                if b'"usage"' in buffer:
                    for line in buffer.split(b"\n"):
                        line = line.strip()
                        if not line.startswith(b"data:"):
                            continue
                        payload = line[5:].strip()
                        if payload in (b"", b"[DONE]"):
                            continue
                        try:
                            obj = json.loads(payload)
                        except Exception:  # noqa: BLE001
                            continue
                        if isinstance(obj, dict) and isinstance(obj.get("usage"), dict):
                            usage = obj["usage"]
                    # 只保留尾部，避免 buffer 无限增长
                    buffer = buffer[-8192:]
        finally:
            stats["total_ms"] = int((time.perf_counter() - began) * 1000)
            client.close_stream(resp)
            # 流正常读完才算这个号「用成功过」—— 失败的情况下 note_success
            # 会把刚才 note_failure 记的失败计数清零，等于把坏号洗白。
            shared.note_success(account)
            shared.release(account)
            _bill(key, account, slot, usage, request=request, started=started,
                  stats=stats)

    return StreamingResponse(generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


async def messages(request: Request):
    """Anthropic 入口 → 转成 OpenAI 请求再复用主路径。

    Marvis 上游是 OpenAI 协议，所以这里做的是协议翻译，不是透传。
    """
    body = await _body_of(request)
    normalized = _anthropic_to_openai(body)
    return await _run_normalized(request, normalized)


async def responses(request: Request):
    """Responses 入口 → 折叠成 chat/completions 请求。"""
    body = await _body_of(request)
    normalized = _responses_to_openai(body)
    return await _run_normalized(request, normalized)


def _anthropic_to_openai(body: dict) -> dict:
    messages: list[dict] = []
    system = body.get("system")
    if isinstance(system, str) and system.strip():
        messages.append({"role": "system", "content": system})
    elif isinstance(system, list):
        text = "".join(str(part.get("text") or "") for part in system
                       if isinstance(part, dict))
        if text.strip():
            messages.append({"role": "system", "content": text})

    for item in (body.get("messages") or []):
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "user")
        content = item.get("content")
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text") or ""))
            content = "".join(parts)
        messages.append({"role": role, "content": content or ""})

    out = {
        "model": body.get("model") or endpoints.DEFAULT_MODEL,
        "messages": messages,
        "stream": bool(body.get("stream")),
    }
    if body.get("max_tokens"):
        out["max_tokens"] = body["max_tokens"]
    if body.get("temperature") is not None:
        out["temperature"] = body["temperature"]
    if isinstance(body.get("tools"), list):
        tools = []
        for tool in body["tools"]:
            if not isinstance(tool, dict):
                continue
            name = tool.get("name")
            if not name:
                continue
            tools.append({"type": "function",
                          "function": {"name": name,
                                       "description": tool.get("description") or "",
                                       "parameters": tool.get("input_schema") or {}}})
        if tools:
            out["tools"] = tools
    return out


def _responses_to_openai(body: dict) -> dict:
    raw_input = body.get("input")
    messages: list[dict] = []
    instructions = body.get("instructions")
    if isinstance(instructions, str) and instructions.strip():
        messages.append({"role": "system", "content": instructions})
    if isinstance(raw_input, str):
        messages.append({"role": "user", "content": raw_input})
    elif isinstance(raw_input, list):
        for item in raw_input:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "user")
            content = item.get("content")
            if isinstance(content, list):
                content = "".join(str(part.get("text") or "") for part in content
                                  if isinstance(part, dict))
            messages.append({"role": role, "content": content or ""})
    out = {"model": body.get("model") or endpoints.DEFAULT_MODEL,
           "messages": messages,
           "stream": bool(body.get("stream"))}
    if body.get("max_output_tokens"):
        out["max_tokens"] = body["max_output_tokens"]
    return out


async def _run_normalized(request: Request, normalized: dict):
    """把已翻译好的 OpenAI 请求走一遍主路径（复用选号/记账/流式）。"""
    from starlette.requests import Request as _Request

    payload = json.dumps(normalized, ensure_ascii=False).encode()

    async def receive():
        return {"type": "http.request", "body": payload, "more_body": False}

    inner = _Request(request.scope, receive)
    return await chat_completions(inner)


async def count_tokens(request: Request):
    """本地估算。口径：CJK 1 token/字，其它 4 字符 1 token（保守近似）。"""
    body = await _body_of(request)
    parts: list[str] = []
    if isinstance(body.get("system"), str):
        parts.append(body["system"])
    for message in (body.get("messages") or []):
        if isinstance(message, dict):
            content = message.get("content")
            parts.append(content if isinstance(content, str) else str(content or ""))
    text = "\n".join(parts)
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return JSONResponse({"input_tokens": cjk + max(1, (len(text) - cjk) // 4)})


async def list_models(_request: Request):
    """模型目录。

    Marvis 的模型网关**没有** `/v1/models`（实测无此端点），目录是客户端内置的
    槽位表，所以这里返回静态目录 —— 不猜上游。

    `id` 给**槽位名**（上游接受的 id），真实模型名放 `name`。
    """
    data = [{"id": item["api_id"], "object": "model", "created": 0,
             "owned_by": PUBLIC, "name": item["name"],
             "api_id": item["api_id"], "context": item["context"]}
            for item in endpoints.models_catalog()]
    return JSONResponse({"object": "list", "data": data})


async def balance(request: Request):
    """余额快照 —— 汇总各账号的令牌钱包。"""
    from admin.db import SessionLocal

    realm = _realm(request)
    with SessionLocal() as db:
        summary = marvis_accounts.summarize(db, realm)
    return JSONResponse({"object": "balance", "platform": PUBLIC,
                         "realm": realm,
                         "total_tokens": summary["balance_total"],
                         "remain_tokens": summary["balance_remain"],
                         "accounts": summary["accounts"],
                         "active": summary["active"]})


async def dispatch(request: Request, path: str):
    """按路径分发（供 `PlatformSpec.gateway` 的通用入口调用）。"""
    if path in ("/v1/chat/completions", "/chat/completions"):
        return await chat_completions(request)
    if path in ("/v1/messages", "/messages"):
        return await messages(request)
    if path in ("/v1/responses", "/responses"):
        return await responses(request)
    if path in ("/v1/messages/count_tokens", "/messages/count_tokens"):
        return await count_tokens(request)
    if path in ("/v1/models", "/models"):
        return await list_models(request)
    if path in ("/v1/balance", "/balance"):
        return await balance(request)
    return JSONResponse({"error": {"message": "Marvis 不支持的路径：%s" % path,
                                   "type": "not_found"}}, status_code=404)