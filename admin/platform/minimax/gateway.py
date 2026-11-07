"""MiniMax Code `/v1/*` 转发热路径。

## 上游协议特殊性

MiniMax 是本网关**第一个 Anthropic 协议上游**（其余平台全是 OpenAI
chat.completions）。所以三条入口协议的处理是：

    /v1/messages          → 请求/响应**原样透传**（协议同形，只换主机与凭证）
    /v1/chat/completions  → bridge.openai_request_to_anthropic 转请求，
                            anthropic_message_to_openai / anthropic_upstream_stream_relay 转响应
    /v1/responses         → responses_to_openai → openai_request_to_anthropic，
                            响应经 anthropic_message_to_openai 后再 openai_to_responses

模型清单是**静态目录**（官方客户端 models.generated.js 的受管四模型）——
受管 token plan 没有模型列表接口（`GET /llm/v1/models` 实测 503
direct_route_not_configured），目录随官方客户端版本维护。
"""
from __future__ import annotations

import json
import logging
import time

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from admin.platform import protocol_bridge as bridge
from admin.platform import shared
from admin.platform.minimax.core import client
from admin.platform.minimax.core.endpoints import (
    LLM_MESSAGES_PATH, PUBLIC, agent_base)
from admin.platform.minimax.services import accounts as minimax_accounts

logger = logging.getLogger("admin.platform.minimax.gateway")

STORAGE = minimax_accounts.PLATFORM
MAX_ATTEMPTS = 3

PATHS = frozenset({
    "/v1/chat/completions",
    "/v1/responses",
    "/v1/messages",
    "/v1/messages/count_tokens",
    "/v1/models",
    "/v1/balance",
})

#: 受管模型目录（官方客户端 models.generated.js + config.yaml 实测可调）
MODEL_CATALOG = [
    {"id": "MiniMax-M2.7", "name": "MiniMax-M2.7",
     "description": "MiniMax M2.7 编码模型（思考型，文本输入）",
     "contextWindow": 204800, "maxTokens": 131072, "input": ["text"],
     "reasoning": True},
    {"id": "MiniMax-M2.7-highspeed", "name": "MiniMax-M2.7-highspeed",
     "description": "MiniMax M2.7 高速版（思考型，低延迟）",
     "contextWindow": 204800, "maxTokens": 131072, "input": ["text"],
     "reasoning": True},
    {"id": "MiniMax-M3", "name": "MiniMax-M3",
     "description": "MiniMax M3（思考型，文本+图像输入，512K 上下文）",
     "contextWindow": 512000, "maxTokens": 128000,
     "input": ["text", "image"], "reasoning": True},
    {"id": "MiniMax-M3.1-Flash", "name": "MiniMax-M3.1-Flash",
     "description": "MiniMax M3.1 Flash（官方客户端默认模型）",
     "contextWindow": 512000, "maxTokens": 128000,
     "input": ["text", "image"], "reasoning": True},
]
MODEL_IDS = {m["id"] for m in MODEL_CATALOG}


class _BadRequest(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _realm(request) -> str:
    return getattr(request.state, "realm", "") or "cn"


def _client_ip(request) -> str:
    try:
        return request.client.host if request.client else ""
    except Exception:  # noqa: BLE001
        return ""


def _fail(exc: client.UpstreamError) -> JSONResponse:
    """UpstreamError → 协议错误响应（Anthropic 路径给 anthropic 形态）。"""
    status = exc.status if exc.status >= 400 else 502
    if status < 400:
        status = 502
    openai_body = {"error": {"message": exc.message, "type": exc.kind,
                             "code": exc.code}}
    anthropic_body = {"type": "error",
                      "error": {"type": exc.kind, "message": exc.message}}
    return JSONResponse(
        anthropic_body if getattr(exc, "anthropic_style", False) else openai_body,
        status_code=status)


def anthropic_fail(exc: client.UpstreamError) -> JSONResponse:
    status = exc.status if exc.status >= 400 else 502
    return JSONResponse({"type": "error",
                         "error": {"type": exc.kind, "message": exc.message}},
                        status_code=status)


async def _body_of(request: Request) -> dict:
    raw = await request.body()
    if not raw:
        return {}
    try:
        body = json.loads(raw)
    except ValueError:
        raise _BadRequest("invalid_json", "请求体不是合法 JSON")
    return body if isinstance(body, dict) else {}


def _validate_model(body: dict) -> None:
    model = str(body.get("model") or "")
    if not model:
        raise _BadRequest("missing_model", "缺少 model 字段")
    if model not in MODEL_IDS and not model.startswith("minimax-"):
        # 平台后缀已由 dispatch 中间件剥掉；到这里还不认识就是真不认识
        raise _BadRequest("model_not_found",
                          "模型 %s 不在 MiniMax 受管目录（可用：%s）"
                          % (model, ", ".join(sorted(MODEL_IDS))))


# ---------------------------------------------------------------------------
# 选号与调用
# ---------------------------------------------------------------------------
def _attempt(db, request, key, body: dict, realm: str):
    """选号 + 租约 + 调上游（非流式）。换号重试语义与 raccoon 相同。"""
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
            data = minimax_accounts.ensure_token(db, account)
            result = client.request_json(
                "POST", agent_base(realm) + LLM_MESSAGES_PATH,
                token=minimax_accounts.token_of(data), realm=realm,
                json_body=body, account_id=int(account.id or 0))
            return result, account, started
        except client.UpstreamError as exc:
            shared.release(account)
            shared.note_failure(
                db, account, kind=exc.kind, detail=exc.detail or exc.message,
                status=exc.status, model=model, public_platform=PUBLIC,
                cool_seconds=(600 if exc.kind in ("auth", "quota") else 0))
            last_error = exc
            exclude.add(account.id)
            if exc.status in (400, 422) and exc.kind not in ("auth", "quota", "rate"):
                break
        except Exception as exc:  # noqa: BLE001
            shared.release(account)
            logger.warning("MiniMax 调用失败 acc=%s: %s",
                           account.id, type(exc).__name__)
            last_error = client.UpstreamError(
                "MiniMax 调用异常（%s）" % type(exc).__name__, kind="upstream")
            exclude.add(account.id)
    if last_error is not None:
        raise last_error
    raise shared.PlatformRejected(
        503, "MiniMax 没有可用账号（请先在控制台扫描/导入凭证并确认状态为启用）",
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
                use_case="minimax-chat", seq=1,
                ttfb_ms=ttfb, latency_ms=latency,
                reasoning_effort=shared.reasoning_effort_of_request(request))
    except Exception:  # noqa: BLE001
        logger.exception("MiniMax 记账失败")


async def _forward_anthropic(body: dict, request) -> JSONResponse:
    """非流式：Anthropic 请求直通上游，返回 Anthropic message。"""
    from admin.db import SessionLocal

    realm = _realm(request)
    db = SessionLocal()
    try:
        key = shared.authenticate(db, request, platform=PUBLIC)
        try:
            result, account, started = _attempt(db, request, key, body, realm)
        except shared.PlatformRejected:
            raise
        except client.UpstreamError as exc:
            return anthropic_fail(exc)
        shared.release(account)
        shared.note_success(account)
        try:
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
        if not isinstance(result, dict):
            _bill(key, account, str(body.get("model") or ""), {},
                  request=request, started=started, error="empty_upstream")
            return JSONResponse(
                {"type": "error",
                 "error": {"type": "upstream_error",
                           "message": "MiniMax 上游返回空响应"}},
                status_code=502)
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
        _bill(key, account, str(body.get("model") or ""), usage,
              request=request, started=started)
        return JSONResponse(result)
    finally:
        db.close()


def _anthropic_usage_of_message(result: dict) -> dict:
    """Anthropic usage → OpenAI 口径（记账统一用 OpenAI 字段）。"""
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    cached = int(usage.get("cache_read_input_tokens") or 0)
    return {"prompt_tokens": int(usage.get("input_tokens") or 0) + cached,
            "completion_tokens": int(usage.get("output_tokens") or 0)}


async def _forward_openai_via_anthropic(body: dict, request):
    """非流式：OpenAI 请求 → Anthropic 上游 → OpenAI 响应。"""
    anthropic_body = bridge.openai_request_to_anthropic(body)
    response = await _forward_anthropic(anthropic_body, request)
    if getattr(response, "status_code", 200) >= 400:
        # Anthropic 错误体 → OpenAI 错误体
        try:
            err = json.loads(response.body)
            message = str((err.get("error") or {}).get("message") or "上游错误")
        except Exception:  # noqa: BLE001
            message = "上游错误"
        return JSONResponse({"error": {"message": message,
                                       "type": "upstream_error"}},
                            status_code=response.status_code)
    try:
        message = json.loads(response.body)
    except Exception:  # noqa: BLE001
        return JSONResponse({"error": {"message": "上游返回空响应",
                                       "type": "upstream_error"}},
                            status_code=502)
    model = str(body.get("model") or "")
    return JSONResponse(bridge.anthropic_message_to_openai(message, model))


async def _forward_stream(body: dict, request, *, anthropic_native: bool):
    """流式：选号 → 上游 SSE → 中继 → 收尾记账。

    `anthropic_native=True`：入口是 /v1/messages，上游事件流**原样透传**
    （只抓 usage 记账）；否则翻译成 OpenAI chunk 流。
    """
    from admin.db import SessionLocal

    realm = _realm(request)
    db = SessionLocal()
    account = None
    upstream = None
    leased = False
    key = None
    model = str(body.get("model") or "")
    started = time.perf_counter()
    stats: dict = {}
    exclude: set[int] = set()
    last_error = None
    try:
        key = shared.authenticate(db, request, platform=PUBLIC)
        body = {**body, "stream": True}
        for _ in range(MAX_ATTEMPTS):
            account = shared.select_account(
                db, storage=STORAGE, realm=realm, key=key,
                model=model, exclude_ids=exclude)
            if account is None:
                break
            if not shared.lease(account):
                exclude.add(account.id)
                continue
            leased = True
            try:
                data = minimax_accounts.ensure_token(db, account)
                upstream = client.open_stream(
                    agent_base(realm) + LLM_MESSAGES_PATH,
                    token=minimax_accounts.token_of(data), realm=realm,
                    json_body=body, account_id=int(account.id or 0),
                    stats=stats)
                break
            except client.UpstreamError as exc:
                shared.release(account)
                leased = False
                shared.note_failure(
                    db, account, kind=exc.kind, detail=exc.detail or exc.message,
                    status=exc.status, model=model, public_platform=PUBLIC,
                    cool_seconds=(600 if exc.kind in ("auth", "quota") else 0))
                last_error = exc
                exclude.add(account.id)
            except Exception as exc:  # noqa: BLE001
                shared.release(account)
                leased = False
                logger.warning("MiniMax 流式调用失败 acc=%s: %s",
                               account.id, type(exc).__name__)
                last_error = client.UpstreamError(
                    "MiniMax 调用异常（%s）" % type(exc).__name__, kind="upstream")
                exclude.add(account.id)
    except shared.PlatformRejected as exc:
        if leased and account is not None:
            try:
                shared.release(account)
            except Exception:  # noqa: BLE001
                pass
        db.close()
        return exc.openai() if not anthropic_native else exc.anthropic()
    except Exception:
        if leased and account is not None:
            try:
                shared.release(account)
            except Exception:  # noqa: BLE001
                pass
        db.close()
        raise

    if upstream is None:
        db.close()
        if last_error is not None:
            return anthropic_fail(last_error) if anthropic_native else _fail(last_error)
        raise shared.PlatformRejected(
            503, "MiniMax 没有可用账号（请先在控制台扫描/导入凭证）",
            kind="server_error", code="no_available_account",
            headers={"Retry-After": "3"})

    holder: dict = {}
    cleaned = {"done": False}

    def cleanup():
        if cleaned["done"]:
            return
        cleaned["done"] = True
        client.close_stream(upstream)
        try:
            shared.release(account)
            shared.note_success(account)
            db.commit()
        except Exception:  # noqa: BLE001
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
        try:
            _bill(key, account, model, holder.get("usage") or {},
                  request=request, started=started, stats=stats)
        except Exception:  # noqa: BLE001
            logger.exception("MiniMax 流式记账失败")
        try:
            db.close()
        except Exception:  # noqa: BLE001
            pass

    def generator():
        if anthropic_native:
            # 原样透传 Anthropic 事件流；顺手抓 usage（message_start /
            # message_delta 的 usage 字段）
            try:
                for raw in upstream.iter_lines():
                    line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
                    stripped = line.strip()
                    if stripped.startswith("data:"):
                        payload = stripped[5:].strip()
                        if payload and payload != "[DONE]":
                            try:
                                event = json.loads(payload)
                            except ValueError:
                                event = None
                            if isinstance(event, dict):
                                usage = event.get("usage")
                                if isinstance(usage, dict) and usage:
                                    merged = dict(holder.get("usage") or {})
                                    merged.update(usage)
                                    holder["usage"] = merged
                    yield (line + "\n").encode()
                yield b"\n"
            except Exception as exc:  # noqa: BLE001
                logger.warning("MiniMax 流式转发中断：%s", type(exc).__name__)
                try:
                    yield ("event: error\ndata: %s\n\n" % json.dumps(
                        {"type": "error",
                         "error": {"type": "upstream_error",
                                   "message": "上游连接中断（%s）" % type(exc).__name__}},
                        ensure_ascii=False)).encode()
                except Exception:  # noqa: BLE001
                    pass
            finally:
                cleanup()
            return  # native 分支到此为止（下方 return 不会执行）

    # 非 native（OpenAI / Responses）入口：`anthropic_upstream_stream_relay`
    # 是 async 中继（消费 inner 的 async body_iterator），不能包进同步
    # generator —— 在进入 generator 之前直接返回它的 StreamingResponse，
    # cleanup 挂在 background（断连兜底与 native 分支同款）。
    if not anthropic_native:
        async def _agen():
            for line in _passthrough_lines(upstream):
                yield line + b"\n"

        class _Inner:
            body_iterator = _agen()

        resp = bridge.anthropic_upstream_stream_relay(
            _Inner(), model, prefix="minimax")
        resp.background = BackgroundTask(cleanup)
        return resp

    def _native_only():
        yield from generator()

    return StreamingResponse(
        bridge.with_pings(_native_only(), ping=bridge.CHAT_PING),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        background=BackgroundTask(cleanup))


def _passthrough_lines(upstream):
    """把上游字节流拆成单行 bytes（含事件行），供翻译中继消费。"""
    for raw in upstream.iter_lines():
        yield raw if isinstance(raw, bytes) else str(raw).encode()


# ---------------------------------------------------------------------------
# 入口端点
# ---------------------------------------------------------------------------
async def chat_completions(request: Request):
    body = await _body_of(request)
    try:
        normalized = bridge.openai_request_to_anthropic(body)
    except ValueError as exc:
        return JSONResponse(
            {"error": {"message": str(exc), "type": "invalid_request_error",
                       "code": "empty_messages"}},
            status_code=400)
    if body.get("stream"):
        return await _forward_stream(normalized, request, anthropic_native=False)
    return await _forward_openai_via_anthropic(normalized, request)


async def messages(request: Request):
    body = await _body_of(request)
    _validate_model(body)
    if body.get("stream"):
        return await _forward_stream(body, request, anthropic_native=True)
    return await _forward_anthropic(body, request)


async def responses(request: Request):
    body = await _body_of(request)
    normalized = bridge.responses_to_openai(body)
    model = str(body.get("model") or "")
    if not body.get("stream"):
        normalized["stream"] = False
        anthropic_body = bridge.openai_request_to_anthropic(normalized)
        response = await _forward_anthropic(anthropic_body, request)
        if getattr(response, "status_code", 200) >= 400:
            return JSONResponse({"error": {"message": "上游错误",
                                           "type": "upstream_error"}},
                                status_code=response.status_code)
        try:
            message = json.loads(response.body)
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": {"message": "上游返回空响应",
                                           "type": "upstream_error"}},
                                status_code=502)
        openai_result = bridge.anthropic_message_to_openai(message, model)
        return JSONResponse(bridge.openai_to_responses(openai_result, model))
    inner_response = await _forward_stream(normalized, request,
                                           anthropic_native=False)
    if getattr(inner_response, "status_code", 200) >= 400:
        return inner_response
    return bridge.responses_stream_relay(inner_response, model, prefix="minimax")


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


async def list_models(request: Request):
    from admin.db import SessionLocal

    with SessionLocal() as db:
        shared.authenticate(db, request, platform=PUBLIC)
    payload = [{
        "id": m["id"], "object": "model", "owned_by": "minimax",
        "name": m["name"], "display_name": m["name"],
        "description": m["description"],
        "contextWindow": m["contextWindow"],
        "maxInputTokens": m["contextWindow"],
        "maxOutputTokens": m["maxTokens"],
        "max_tokens": m["maxTokens"],
        "input": m["input"], "reasoning": m["reasoning"],
        "visible": True,
    } for m in MODEL_CATALOG]
    return JSONResponse({"object": "list", "data": payload})


async def balance(request: Request):
    from admin.db import SessionLocal

    with SessionLocal() as db:
        shared.authenticate(db, request, platform=PUBLIC)
        realm = _realm(request)
        account = shared.select_account(
            db, storage=STORAGE, realm=realm,
            key=shared.key_row(db, request))
        if account is None:
            return JSONResponse({"platform": PUBLIC, "realm": realm,
                                 "reachable": False, "models": len(MODEL_CATALOG),
                                 "note": "号池没有可用 MiniMax 账号"},
                                status_code=503)
        try:
            data = minimax_accounts.ensure_token(db, account)
            credit = client.fetch_credit_details(
                minimax_accounts.token_of(data), realm,
                account_id=int(account.id or 0))
        except client.UpstreamError as exc:
            return _fail(exc)
    return JSONResponse({"platform": PUBLIC, "realm": realm, "reachable": True,
                         "available": credit["remain"],
                         "total": credit["total"], "models": len(MODEL_CATALOG),
                         "unit": "credits"})


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
            {"error": {"message": "MiniMax 不支持 %s" % path,
                       "type": "invalid_request_error"}},
            status_code=404)
    try:
        return await handler(request)
    except shared.PlatformRejected as exc:
        if path in ("/v1/messages", "/v1/messages/count_tokens"):
            return exc.anthropic()
        return exc.openai()
    except _BadRequest as exc:
        return JSONResponse(
            {"error": {"message": exc.message, "type": "invalid_request_error",
                       "code": exc.code}},
            status_code=400)
