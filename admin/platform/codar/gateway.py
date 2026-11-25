"""科搭 Codar `/v1/*` 转发热路径。

## 这个平台为什么不能「一条线到底」

科搭的上游**同时存在两套对话协议**：

    大多数模型   OpenAI      POST /v1/chat/completions   Bearer
    Claude 系    Anthropic   POST /v1/messages           X-Api-Key + Anthropic-Version

所以每个请求先做一次「选路」，然后按 入口协议 × 上游协议 的组合处理：

| 入口 | 上游 | 请求 | 响应 |
|---|---|---|---|
| chat/completions | chat | 原样 | 原样透传（含 SSE） |
| chat/completions | anthropic | bridge 转一次 | 非流式 `anthropic_message_to_openai`；流式 `anthropic_upstream_stream_relay` |
| messages | anthropic | 原样 | 原样透传（含 SSE） |
| messages | chat | `bridge.anthropic_to_openai` | 非流式 `openai_to_anthropic`；流式 `anthropic_stream_relay` |
| responses | 任一 | `responses_to_openai` 再按上游转 | 先落到 OpenAI 口径，再 `openai_to_responses` / `responses_stream_relay` |

**转换只做一次**：`openai_request_to_anthropic` 不是幂等的（Anthropic 形态的
`tools[]` 没有 `function` 键，转两遍会把整段工具定义丢掉），所以每个入口
只在自己的最外层转一次。

## 并发

每账号 3 个在途请求（上游实测，第 4 个回 429 `concurrent_limit_exceeded`）。
闸门在 `core/client.py`：拿不到名额就**排队**，排队超时才回 429。
流式请求的名额一直占到流结束（清理函数里释放）。

## 错误语义

`account_restricted` / `client_not_supported` 是**账号级**拒绝，重登无效，
这里把号标成 disabled 并留下具体原因；`401` 先续期一次，续期也失败才判
登录失效；`429` 原样透传 `Retry-After`。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime

from fastapi import Request
from starlette.background import BackgroundTask
from starlette.responses import JSONResponse, StreamingResponse

from admin.platform import protocol_bridge as bridge
from admin.platform import shared
from admin.platform.codar.core import client, endpoints
from admin.platform.codar.services import accounts as codar_accounts

logger = logging.getLogger("admin.platform.codar.gateway")

STORAGE = codar_accounts.PLATFORM
PUBLIC = endpoints.PUBLIC
MAX_ATTEMPTS = 3

PATHS = frozenset({
    "/v1/chat/completions",
    "/v1/responses",
    "/v1/messages",
    "/v1/messages/count_tokens",
    "/v1/models",
    "/v1/balance",
})


# ---------------------------------------------------------------------------
# 通用小件
# ---------------------------------------------------------------------------
async def _body_of(request) -> dict:
    if request.scope.get("method", "POST").upper() in ("GET", "HEAD", "OPTIONS"):
        return {}
    cached = request.scope.get("_wb_body")
    if isinstance(cached, bytes) and cached:
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


def _public(request) -> str:
    return shared.public_platform_of(request, PUBLIC)


def _client_ip(request) -> str:
    try:
        return request.client.host if request.client else ""
    except Exception:  # noqa: BLE001
        return ""


def _fail(exc: client.UpstreamError, *, anthropic: bool = False) -> JSONResponse:
    """上游错误 → 协议错误体（Anthropic 入口回 Anthropic 形态）。"""
    status = exc.status if 400 <= exc.status <= 599 else 502
    headers = {}
    if exc.retry_after:
        headers["Retry-After"] = str(int(exc.retry_after))
    body = exc.anthropic_error() if anthropic else exc.openai_error()
    return JSONResponse(body, status_code=status, headers=headers or None)


def _protocol_of(model: str) -> str:
    """按目录选路；目录还没拉过就按模型名兜底。"""
    return codar_accounts.protocol_for(None, model)


def _no_account() -> shared.PlatformRejected:
    return shared.PlatformRejected(
        503, "科搭没有可用账号（请先在控制台「扫描本机」或导入令牌）",
        kind="server_error", code="no_available_account",
        headers={"Retry-After": "3"})


def _note_failure(db, account, exc: client.UpstreamError, model: str) -> None:
    """把失败写回账号：区分「账号级拒绝」「登录失效」「只是忙」。

    * `account_restricted` / `client_gated`：厂商对**这个账号**的限制，
      重新登录无效 —— 标 disabled 并留下原因，免得它每半小时被重试一次；
    * `auth`：令牌死了，冷却 600 秒等人工换令牌；
    * `concurrent` / `rate`：账号没问题，不记在它头上；
    * `request` / `model`：请求本身的问题，换号也一样错。
    """
    if exc.kind in ("account_restricted", "client_gated"):
        try:
            account.status = "disabled"
            account.last_err_at = datetime.utcnow()
            account.last_err_msg = exc.message[:255]
            db.commit()
        except Exception:  # noqa: BLE001 - 状态写回失败不影响响应
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
        shared.note_failure(db, account, kind=exc.kind,
                            detail=exc.detail or exc.message, status=exc.status,
                            model=model, public_platform=PUBLIC, code=exc.code)
        return
    if exc.kind in ("concurrent", "rate"):
        return
    if exc.kind in ("request", "model"):
        return
    shared.note_failure(
        db, account, kind=exc.kind, detail=exc.detail or exc.message,
        status=exc.status, model=model, public_platform=PUBLIC, code=exc.code,
        cool_seconds=(600 if exc.kind == "auth" else 0))


def _pick(db, key, model: str, exclude: set[int]):
    """选号：`ignore_capacity=True`，因为「忙」要排队而不是「没有号」。"""
    return shared.select_account(db, storage=STORAGE, realm="cn", key=key,
                                 model=model, exclude_ids=exclude,
                                 ignore_capacity=True)


def _bill(key, account, model: str, usage, *, request, started=None,
          error: str = "", stats: dict | None = None, body: dict | None = None):
    try:
        from admin.db import SessionLocal

        elapsed = int((time.perf_counter() - started) * 1000) if started else None
        ttfb = int((stats or {}).get("ttfb_ms") or 0)
        total = int((stats or {}).get("total_ms") or 0)
        latency = total or elapsed
        if latency and not ttfb:
            ttfb = latency
        if ttfb and latency and ttfb > latency:
            ttfb = latency
        with SessionLocal() as session:
            shared.record_usage(
                session, key_id=int(getattr(key, "id", 0) or 0),
                account_id=int(getattr(account, "id", 0) or 0),
                model=model, usage=_openai_usage(usage or {}),
                public_platform=PUBLIC, storage=STORAGE,
                error_kind=error, client_ip=_client_ip(request),
                use_case="codar-chat", seq=1,
                ttfb_ms=ttfb or None, latency_ms=latency,
                reasoning_effort=shared.reasoning_effort_of(body or {}))
    except Exception:  # noqa: BLE001
        logger.exception("科搭记账失败")


def _openai_usage(usage: dict) -> dict:
    """两种协议的 usage 统一成 OpenAI 口径（记账只认一套字段）。"""
    if not isinstance(usage, dict):
        return {}
    if "prompt_tokens" in usage or "completion_tokens" in usage:
        return usage
    if "input_tokens" in usage or "output_tokens" in usage:
        cached = int(usage.get("cache_read_input_tokens") or 0)
        return {"prompt_tokens": int(usage.get("input_tokens") or 0) + cached,
                "completion_tokens": int(usage.get("output_tokens") or 0),
                "cached_tokens": cached}
    return usage


def _usage_in(event) -> dict:
    """事件帧里的 usage（两套协议都认）。

    OpenAI 的 chunk 把 usage 放顶层；Anthropic 的 `message_start` 放在
    `message.usage` 里，`message_delta` 又放回顶层 —— 只看顶层会漏掉
    输入 token，账本上就是「只有输出没有输入」。
    """
    if not isinstance(event, dict):
        return {}
    usage = event.get("usage")
    if isinstance(usage, dict) and usage:
        return usage
    inner = event.get("message")
    if isinstance(inner, dict):
        usage = inner.get("usage")
        if isinstance(usage, dict) and usage:
            return usage
    return {}


# ---------------------------------------------------------------------------
# 上游调用
# ---------------------------------------------------------------------------
def _url_of(protocol: str) -> str:
    return endpoints.gateway_url(
        endpoints.MESSAGES_PATH if protocol == "anthropic" else endpoints.CHAT_PATH)


def _post(token: str, body: dict, protocol: str, account_id=None):
    return client.request_json(
        "POST", _url_of(protocol), token=token, protocol=protocol,
        json_body=body, account_id=account_id, context="对话",
        timeout=client.TIMEOUT)


def _call_with_reauth(db, account, body: dict, protocol: str):
    """单账号非流式调用；401 时续期一次再问（只重试一次）。"""
    account_id = int(getattr(account, "id", 0) or 0)
    token = codar_accounts.token_for_call(db, account)
    try:
        return _post(token, body, protocol, account_id)
    except client.UpstreamError as exc:
        if exc.kind != "auth":
            raise
        fresh = codar_accounts.token_for_call(db, account, stale_token=token)
        return _post(fresh, body, protocol, account_id)


def _open_with_reauth(db, account, body: dict, protocol: str,
                      stats: dict | None = None):
    """单账号开流；401 时续期一次再开。"""
    account_id = int(getattr(account, "id", 0) or 0)
    token = codar_accounts.token_for_call(db, account)

    def open_with(value: str):
        return client.open_stream(_url_of(protocol), token=value,
                                  protocol=protocol, json_body=body,
                                  account_id=account_id, stats=stats)

    try:
        return open_with(token)
    except client.UpstreamError as exc:
        if exc.kind != "auth":
            raise
        fresh = codar_accounts.token_for_call(db, account, stale_token=token)
        return open_with(fresh)


def _attempt_json(db, request, key, body: dict, protocol: str):
    """选号 + 排队 + 调用（非流式）。返回 (结果, 账号, 开始时间)。"""
    model = str(body.get("model") or "")
    exclude: set[int] = set()
    last_error: client.UpstreamError | None = None
    for _ in range(MAX_ATTEMPTS):
        account = _pick(db, key, model, exclude)
        if account is None:
            break
        uid = str(account.uid or "")
        if not client.acquire_slot(uid):
            # 排队也排不到：号是好的，只是此刻忙。换号解决不了（同一个上限）
            raise client.UpstreamError(
                "科搭每账号仅允许 %d 个在途请求，当前已排满，请稍后重试"
                % endpoints.max_concurrency(),
                status=429, kind="concurrent",
                code="concurrent_limit_exceeded", retry_after=5)
        started = time.perf_counter()
        try:
            result = _call_with_reauth(db, account, body, protocol)
            return result, account, started
        except client.UpstreamError as exc:
            _note_failure(db, account, exc, model)
            last_error = exc
            exclude.add(account.id)
            if not exc.retryable:
                break
        except Exception as exc:  # noqa: BLE001
            logger.exception("科搭调用异常 acc=%s", account.id)
            last_error = client.UpstreamError(str(exc), kind="network")
            exclude.add(account.id)
        finally:
            client.release_slot(uid)
    if last_error is not None:
        raise last_error
    raise _no_account()


# ---------------------------------------------------------------------------
# 非流式入口
# ---------------------------------------------------------------------------
async def _forward_json(body: dict, request, *, upstream: str):
    """非流式转发：返回 (上游原始结果, 记账用的 OpenAI 口径 usage)。"""
    from admin.db import SessionLocal

    db = SessionLocal()
    try:
        key = shared.authenticate(db, request, platform=_public(request))
        result, account, started = await asyncio.to_thread(
            _attempt_json, db, request, key, body, upstream)
        shared.note_success(account)
        try:
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
        usage = result.get("usage") if isinstance(result, dict) else {}
        _bill(key, account, str(body.get("model") or ""), usage,
              request=request, started=started, body=body)
        return result
    finally:
        db.close()


async def _json_chat(body: dict, request):
    result = await _forward_json(body, request, upstream="chat")
    if not isinstance(result, dict):
        return JSONResponse({"error": {"message": "上游返回空响应",
                                       "type": "upstream_error"}},
                            status_code=502)
    return JSONResponse(result)


async def _json_openai_entry_via_anthropic(body: dict, request):
    """OpenAI 入口 + Anthropic 上游：请求转一次，响应翻回来。"""
    try:
        anthropic_body = bridge.openai_request_to_anthropic(body)
    except ValueError as exc:
        return JSONResponse(
            {"error": {"message": str(exc), "type": "invalid_request_error",
                       "code": "empty_messages"}}, status_code=400)
    result = await _forward_json(anthropic_body, request, upstream="anthropic")
    if not isinstance(result, dict):
        return JSONResponse({"error": {"message": "上游返回空响应",
                                       "type": "upstream_error"}},
                            status_code=502)
    model = str(body.get("model") or "")
    if isinstance(result.get("error"), dict):
        return _fail(client.UpstreamError(
            str(result["error"].get("message") or "上游错误"),
            status=502, kind="upstream"), anthropic=True)
    return JSONResponse(bridge.anthropic_message_to_openai(result, model))


async def _json_anthropic_entry_via_chat(body: dict, request):
    """Anthropic 入口 + OpenAI 上游：请求经桥转一次，响应翻回来。"""
    normalized = bridge.anthropic_to_openai(body)
    model = str(body.get("model") or "")
    result = await _forward_json(normalized, request, upstream="chat")
    if not isinstance(result, dict):
        return JSONResponse({"type": "error",
                             "error": {"type": "upstream_error",
                                       "message": "上游返回空响应"}},
                            status_code=502)
    return JSONResponse(bridge.openai_to_anthropic(result, model))


async def _json_responses(body: dict, request, *, upstream: str):
    """`/v1/responses` 非流式：先落到 OpenAI 口径，再包回 Responses。"""
    normalized = bridge.responses_to_openai(body)
    model = str(body.get("model") or "")
    if upstream == "anthropic":
        try:
            anthropic_body = bridge.openai_request_to_anthropic(normalized)
        except ValueError as exc:
            return JSONResponse(
                {"error": {"message": str(exc), "type": "invalid_request_error",
                           "code": "empty_messages"}}, status_code=400)
        result = await _forward_json(anthropic_body, request, upstream="anthropic")
        if not isinstance(result, dict):
            return JSONResponse({"error": {"message": "上游返回空响应",
                                           "type": "upstream_error"}},
                                status_code=502)
        openai_result = bridge.anthropic_message_to_openai(result, model)
    else:
        openai_result = await _forward_json(normalized, request, upstream="chat")
        if not isinstance(openai_result, dict):
            return JSONResponse({"error": {"message": "上游返回空响应",
                                           "type": "upstream_error"}},
                                status_code=502)
    return JSONResponse(bridge.openai_to_responses(openai_result, model))


# ---------------------------------------------------------------------------
# 流式入口
# ---------------------------------------------------------------------------
def _stream_body(body: dict, protocol: str) -> tuple[dict, bool]:
    """流式请求体：OpenAI 上游补 `include_usage`（否则记账只有 0 token）。

    只**补**这一个键，调用方自己的 stream_options 原样保留。
    """
    if protocol != "chat":
        return body, False
    options = body.get("stream_options")
    if isinstance(options, dict) and options.get("include_usage"):
        return body, False
    merged = dict(options) if isinstance(options, dict) else {}
    merged["include_usage"] = True
    return {**body, "stream_options": merged}, True


def _open_stream_attempt(db, request, key, body: dict, protocol: str, stats):
    """选号 + 排队 + 开流。名额**不在这里释放**（流结束才释放）。

    返回 (上游响应, 账号, uid, 开始时间)。
    """
    model = str(body.get("model") or "")
    exclude: set[int] = set()
    last_error: client.UpstreamError | None = None
    for _ in range(MAX_ATTEMPTS):
        account = _pick(db, key, model, exclude)
        if account is None:
            break
        uid = str(account.uid or "")
        if not client.acquire_slot(uid):
            raise client.UpstreamError(
                "科搭每账号仅允许 %d 个在途请求，当前已排满，请稍后重试"
                % endpoints.max_concurrency(),
                status=429, kind="concurrent",
                code="concurrent_limit_exceeded", retry_after=5)
        started = time.perf_counter()
        try:
            return (_open_with_reauth(db, account, body, protocol, stats),
                    account, uid, started)
        except client.UpstreamError as exc:
            client.release_slot(uid)
            _note_failure(db, account, exc, model)
            last_error = exc
            exclude.add(account.id)
            if not exc.retryable:
                break
        except Exception as exc:  # noqa: BLE001
            client.release_slot(uid)
            logger.exception("科搭开流异常 acc=%s", account.id)
            last_error = client.UpstreamError(str(exc), kind="network")
            exclude.add(account.id)
    if last_error is not None:
        raise last_error
    raise _no_account()


async def _stream(request, body: dict, *, upstream: str, entry: str):
    """流式：选号 → 上游 SSE → 按 入口/上游 组合中继 → 收尾记账。"""
    from admin.db import SessionLocal

    db = SessionLocal()
    key = None
    account = None
    uid = ""
    resp = None
    stats: dict = {}
    model = str(body.get("model") or "")
    started = time.perf_counter()
    holder: dict = {}
    cleaned = {"done": False}
    injected = False
    try:
        key = shared.authenticate(db, request, platform=_public(request))
        wire_body, injected = _stream_body({**body, "stream": True}, upstream)
        try:
            # 建连是同步的（选号 + 排队 + TLS），必须离开事件循环
            resp, account, uid, started = await asyncio.to_thread(
                _open_stream_attempt, db, request, key, wire_body, upstream,
                stats)
        except client.UpstreamError as exc:
            if injected and exc.kind == "request":
                # 上游不认 stream_options：去掉补的键再来一次
                resp, account, uid, started = await asyncio.to_thread(
                    _open_stream_attempt, db, request, key,
                    {**body, "stream": True}, upstream, stats)
            else:
                db.close()
                return _fail(exc, anthropic=(entry == "anthropic"))
    except shared.PlatformRejected:
        db.close()
        raise
    except Exception:
        db.close()
        raise

    def cleanup():
        if cleaned["done"]:
            return
        cleaned["done"] = True
        if resp is not None:
            client.close_stream(resp)
        if uid:
            client.release_slot(uid)
        try:
            shared.note_success(account)
            db.commit()
        except Exception:  # noqa: BLE001
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
        stats["total_ms"] = int((time.perf_counter() - started) * 1000)
        _bill(key, account, model, holder.get("usage") or {}, request=request,
              started=started, stats=stats, body=body,
              error=str(holder.get("error") or ""))
        try:
            db.close()
        except Exception:  # noqa: BLE001
            pass

    # ---- 原样透传（入口协议 == 上游协议） ----
    if (upstream == "chat" and entry == "openai") or \
            (upstream == "anthropic" and entry == "anthropic"):
        anthropic_style = upstream == "anthropic"

        def generator():
            done = False
            try:
                for line in client.sse_lines(resp):
                    text = line.decode("utf-8", "replace") if isinstance(line, bytes) else str(line)
                    stripped = text.strip()
                    if stripped.startswith("data:"):
                        payload = stripped[5:].strip()
                        if payload == "[DONE]":
                            done = True
                        elif payload:
                            try:
                                event = json.loads(payload)
                            except ValueError:
                                event = None
                            if isinstance(event, dict):
                                usage = _usage_in(event)
                                if usage:
                                    merged = dict(holder.get("usage") or {})
                                    merged.update(usage)
                                    holder["usage"] = merged
                    yield (text + "\n").encode("utf-8")
                yield b"\n"
                if not anthropic_style and not done:
                    # 上游没发终止帧：补一个，否则客户端一直等
                    yield b"data: [DONE]\n\n"
            except Exception as exc:  # noqa: BLE001 - 断流 / 解码失败
                holder["error"] = "network"
                logger.warning("科搭流中断 acc=%s: %s",
                               getattr(account, "id", ""), str(exc)[:200])
            finally:
                cleanup()

        ping = bridge.ANTHROPIC_PING if anthropic_style else bridge.CHAT_PING
        return StreamingResponse(
            bridge.with_pings(generator(), ping=ping),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ---- 需要翻译的组合：先把上游字节流包成 inner，再交给桥 ----
    def _lines():
        # 桥会消费掉整段事件流，usage 不会回传 —— 所以在这里顺手记一份：
        # 两套协议的 usage 都在 `data:` 帧里（OpenAI 的 chunk.usage /
        # Anthropic 的 message_delta.usage），不记的话账本就是 0 token。
        for line in client.sse_lines(resp):
            text = (line.decode("utf-8", "replace") if isinstance(line, bytes)
                    else str(line))
            stripped = text.strip()
            if stripped.startswith("data:"):
                payload = stripped[5:].strip()
                if payload and payload != "[DONE]":
                    try:
                        event = json.loads(payload)
                    except ValueError:
                        event = None
                    usage = _usage_in(event) if isinstance(event, dict) else None
                    if usage:
                        merged = dict(holder.get("usage") or {})
                        merged.update(usage)
                        holder["usage"] = merged
            yield (line if isinstance(line, bytes) else str(line).encode("utf-8"))

    async def _agen():
        for line in _lines():
            yield line + b"\n"

    class _Inner:
        body_iterator = _agen()

    if upstream == "anthropic" and entry == "openai":
        return _with_cleanup(
            bridge.anthropic_upstream_stream_relay(_Inner(), model,
                                                   prefix="codar"), cleanup)

    if upstream == "chat" and entry == "anthropic":
        return _with_cleanup(
            bridge.anthropic_stream_relay(_Inner(), model, prefix="codar"),
            cleanup)

    if entry == "responses":
        if upstream == "anthropic":
            inner_relay = bridge.anthropic_upstream_stream_relay(
                _Inner(), model, prefix="codar")

            class _Wrapped:
                body_iterator = inner_relay.body_iterator

            relay = bridge.responses_stream_relay(_Wrapped(), model,
                                                  prefix="codar")
        else:
            relay = bridge.responses_stream_relay(_Inner(), model,
                                                  prefix="codar")
        return _with_cleanup(relay, cleanup)

    cleanup()
    return JSONResponse({"error": {"message": "不支持的协议组合",
                                   "type": "invalid_request_error"}},
                        status_code=500)


def _with_cleanup(relay, cleanup):
    """给桥的中继套上「流一结束就收尾」。

    桥返回的是它自己的 `StreamingResponse`，清理只能挂在 `background` 上 ——
    而 background 由 ASGI 层在响应发完之后跑。客户端断连、或被别的中间件
    提前收走响应时，`background` 不一定会跑到，上游连接与在途名额就漏了。
    这里再包一层生成器：无论正常结束还是提前关闭，`finally` 都收尾一次
    （cleanup 自己是幂等的），`background` 保留作兜底。
    """
    inner = relay.body_iterator

    async def _gen():
        try:
            async for piece in inner:
                yield piece
        finally:
            cleanup()

    relay.body_iterator = _gen()
    relay.background = BackgroundTask(cleanup)
    return relay


# ---------------------------------------------------------------------------
# 入口端点
# ---------------------------------------------------------------------------
async def chat_completions(request: Request):
    body = await _body_of(request)
    model = str(body.get("model") or "")
    if not model:
        return JSONResponse({"error": {"message": "缺少 model 字段",
                                       "type": "invalid_request_error"}},
                            status_code=400)
    protocol = _protocol_of(model)
    if protocol == "anthropic":
        if body.get("stream"):
            try:
                normalized = bridge.openai_request_to_anthropic(body)
            except ValueError as exc:
                return JSONResponse(
                    {"error": {"message": str(exc),
                               "type": "invalid_request_error",
                               "code": "empty_messages"}}, status_code=400)
            return await _stream(request, normalized, upstream="anthropic",
                                 entry="openai")
        return await _json_openai_entry_via_anthropic(body, request)
    if body.get("stream"):
        return await _stream(request, body, upstream="chat", entry="openai")
    return await _json_chat(body, request)


async def messages(request: Request):
    body = await _body_of(request)
    model = str(body.get("model") or "")
    if not model:
        return JSONResponse({"type": "error",
                             "error": {"type": "invalid_request_error",
                                       "message": "缺少 model 字段"}},
                            status_code=400)
    protocol = _protocol_of(model)
    if protocol == "anthropic":
        if body.get("stream"):
            return await _stream(request, body, upstream="anthropic",
                                 entry="anthropic")
        return await _json_anthropic_native(body, request)
    if body.get("stream"):
        # 协议转换是**普通函数**，别写成 `await bridge.anthropic_to_openai(...)`：
        # 那会把 dict 当协程去 await，运行期直接 TypeError。先转好再进流式。
        converted = bridge.anthropic_to_openai(body)
        return await _stream(request, converted, upstream="chat",
                             entry="anthropic")
    return await _json_anthropic_entry_via_chat(body, request)


async def _json_anthropic_native(body: dict, request):
    result = await _forward_json(body, request, upstream="anthropic")
    if not isinstance(result, dict):
        return JSONResponse({"type": "error",
                             "error": {"type": "upstream_error",
                                       "message": "上游返回空响应"}},
                            status_code=502)
    if isinstance(result.get("error"), dict):
        return _fail(client.UpstreamError(
            str(result["error"].get("message") or "上游错误"),
            status=502, kind="upstream"), anthropic=True)
    if not result.get("type"):
        result = {**result, "type": "message"}
    return JSONResponse(result)


async def responses(request: Request):
    body = await _body_of(request)
    model = str(body.get("model") or "")
    if not model:
        return JSONResponse({"error": {"message": "缺少 model 字段",
                                       "type": "invalid_request_error"}},
                            status_code=400)
    protocol = _protocol_of(model)
    if body.get("stream"):
        normalized = bridge.responses_to_openai(body)
        return await _stream(request, normalized, upstream=protocol,
                             entry="responses")
    return await _json_responses(body, request, upstream=protocol)


async def count_tokens(request: Request):
    """本地估算（上游没有 count_tokens 接口）。

    口径：CJK 一字一 token，其余按 4 字符一 token —— 与同类网关一致，
    用于客户端预估上下文占用，不用于计费。
    """
    body = await _body_of(request)
    parts: list[str] = []
    system = body.get("system")
    if isinstance(system, str):
        parts.append(system)
    elif isinstance(system, list):
        for block in system:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
    for message in body.get("messages") or []:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    parts.append(block["text"])
    text = "\n".join(parts)
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return JSONResponse({"input_tokens": cjk + max(1, (len(text) - cjk) // 4)})


async def list_models(request: Request):
    """模型目录：只列本网关真能发的模型（chat / anthropic 两套线）。"""
    from admin.db import SessionLocal

    with SessionLocal() as db:
        key = shared.authenticate(db, request, platform=_public(request))
        info = codar_accounts.catalog(db, key=key)
    payload = []
    for row in info["rows"]:
        protocol = row.get("protocol") or endpoints.PROTOCOL_CHAT
        if protocol not in (endpoints.PROTOCOL_CHAT, endpoints.PROTOCOL_ANTHROPIC):
            continue
        payload.append({
            "id": row.get("id"),
            "object": "model",
            "owned_by": "codar",
            "name": row.get("name") or row.get("id"),
            "display_name": row.get("name") or row.get("id"),
            "contextWindow": int(row.get("context") or 0),
            "maxInputTokens": int(row.get("context") or 0),
            "maxOutputTokens": int(row.get("max_output") or 0),
            "max_tokens": int(row.get("max_output") or 0),
            "input": row.get("input") or ["text"],
            "output": row.get("output") or ["text"],
            "reasoning": bool(row.get("reasoning")),
            # 厂商自己的倍率（rate_badge），只用于展示
            "rate": row.get("rate") or 0,
            "protocol": protocol,
            "visible": True,
        })
    result = {"object": "list", "data": payload}
    if info.get("fallback"):
        # 上游目录这次没拿到：明说这是兜底表，别让客户端当成权威
        result["fallback"] = True
        result["note"] = info.get("error") or "上游目录不可达，已使用内置目录"
    return JSONResponse(result)


async def balance(request: Request):
    """额度：主读数 + 副读数（一条失败不影响另一条出数）。"""
    from admin.db import SessionLocal

    with SessionLocal() as db:
        key = shared.authenticate(db, request, platform=_public(request))
        account = shared.select_account(db, storage=STORAGE, realm="cn",
                                        key=key, ignore_capacity=True)
        if account is None:
            return JSONResponse({"platform": PUBLIC, "reachable": False,
                                 "note": "号池没有启用中的科搭账号"},
                                status_code=503)
        try:
            token = codar_accounts.token_for_call(db, account)
            username = codar_accounts.username_of_account(account)
            points = codar_accounts.read_points(
                token, username, account_id=int(account.id or 0))
        except client.UpstreamError as exc:
            return _fail(exc)
    general = points.get("general")
    agent = points.get("agent")
    return JSONResponse({
        "platform": PUBLIC,
        "reachable": True,
        "available": general,
        "agent": agent,
        "total": (general or 0) + (agent or 0),
        "unit": "points",
        "quota_kind": "points",
        "models": len(client.cached_catalog()["rows"]),
        "notes": points.get("errors") or [],
    })


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
            {"error": {"message": "科搭不支持 %s" % path,
                       "type": "invalid_request_error"}}, status_code=404)
    anthropic_entry = path.startswith("/v1/messages")
    try:
        return await handler(request)
    except shared.PlatformRejected as exc:
        return exc.anthropic() if anthropic_entry else exc.openai()
    except client.UpstreamError as exc:
        return _fail(exc, anthropic=anthropic_entry)
