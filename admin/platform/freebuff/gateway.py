"""Freebuff /v1/* 转发（供 dispatch.py 调用）。

## 为什么这里有一份「本地合成 SSE」

上游是标准 OpenAI 兼容端点，帧可以直接透传。但实测 `GET /models` 里
280 个模型中只有 `deepseek-v4.1-flash` 的 `capabilities.streaming` 为 true，
其余 279 个是 false。对这些模型硬发 `stream:true`，上游会报错或退化成
一次性返回。

所以：

    stream=true + 模型支持流式  → 逐帧透传上游 SSE
    stream=true + 模型不支持    → 上游走非流式拿完整答复，
                                  本地用 protocol.synthesize_sse 合成标准帧
    stream=false               → 普通 JSON

客户端永远看到合法 SSE，不必关心上游这个模型能不能流。

## 记账

统一走 `admin.platform.shared`（authenticate / select_account / lease /
record_usage），与其它平台同一套账本。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from starlette.responses import JSONResponse, StreamingResponse

from admin.platform import shared
from admin.platform.freebuff.core import client, endpoints, protocol
from admin.platform.freebuff.services import accounts as fb_accounts

logger = logging.getLogger("admin.platform.freebuff.gateway")

PATHS = (
    "/v1/chat/completions",
    "/v1/messages",
    "/v1/messages/count_tokens",
    "/v1/responses",
    "/v1/models",
    "/v1/balance",
)

PUBLIC = endpoints.PUBLIC
STORAGE = endpoints.PLATFORM

#: 换号重试上限（key 被撤销时换下一个号）。
MAX_ATTEMPTS = 4

#: 各失败类型的冷却秒数。
_COOL = {"auth": 1800, "rate": 300, "quota": 1800, "network": 20,
         "upstream": 30}

_SENTINEL = object()


async def _body_of(request) -> dict:
    if request.scope.get("method", "GET").upper() in ("GET", "HEAD", "OPTIONS"):
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


def _realm(request) -> str:
    _storage, realm, _spec = shared.locate(request, PUBLIC)
    return realm or ""


def _public(request) -> str:
    return shared.public_platform_of(request, PUBLIC)


def _client_ip(request) -> str:
    try:
        from admin.client_ip import request_ip

        return request_ip(request)
    except Exception:  # noqa: BLE001
        return ""


def _catalog() -> list[dict]:
    rows, _note = fb_accounts.catalog_rows()
    return rows


def _fail(exc: client.UpstreamError) -> JSONResponse:
    try:
        logger.warning("Freebuff 上游失败 kind=%s status=%s msg=%s",
                       getattr(exc, "kind", ""), getattr(exc, "status", ""),
                       str(getattr(exc, "message", ""))[:200])
    except Exception:  # noqa: BLE001
        pass
    status = exc.status or 502
    if status < 400 or status > 599:
        status = 502
    return JSONResponse(
        {"error": {"type": exc.kind, "code": exc.code or "upstream_error",
                   "message": exc.message}},
        status_code=status)


def _has_content(result: dict) -> bool:
    if not isinstance(result, dict):
        return False
    choices = result.get("choices") or []
    if not choices:
        return False
    choice = choices[0] or {}
    message = choice.get("message") or {}
    if (message.get("content") or "").strip():
        return True
    if message.get("tool_calls"):
        return True
    if (message.get("reasoning_content") or "").strip():
        return True
    return (choice.get("finish_reason") or "") in (
        "stop", "length", "tool_calls", "content_filter")


def _bill(key, account, model, result, *, request, started=None, error="",
          stats: dict | None = None):
    try:
        from admin.db import SessionLocal

        usage = (result or {}).get("usage") or {}
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
                model=model or str((result or {}).get("model") or ""),
                usage=usage, public_platform=PUBLIC, storage=STORAGE,
                error_kind=error, client_ip=_client_ip(request),
                use_case="freebuff-chat", seq=1,
                ttfb_ms=ttfb, latency_ms=latency)
    except Exception:  # noqa: BLE001
        logger.exception("Freebuff 记账失败")


# ---------------------------------------------------------------- 选号
def _requested_model(request) -> str:
    """客户端**真正请求**的模型名。

    dispatch 中间件会做「slug 还原 / 别名还原」：把 `/v1/models` 给客户端的
    规范名换回它以为的上游 id。但那些映射表是按 workbuddy / qoder 的上游
    写法建的 —— `deepseek-v4-pro` 会被换成 `dmodel`，而 Freebuff 的上游 id
    本来就是 `deepseek-v4-pro`。照单全收会把请求发成不存在的模型
    （上游 400/404，看起来像"模型不可用"）。

    所以这里读中间件存下的原名，并用本平台的目录校验一遍。
    """
    try:
        return str(request.scope.get("_wb_requested_model") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _resolve_model(body: dict, request) -> str:
    model = str(body.get("model") or "")
    requested = _requested_model(request)
    if not requested or requested == model:
        return model
    try:
        ids = {row.get("id") for row in _catalog()}
    except Exception:  # noqa: BLE001 - 目录拿不到就按中间件给的走
        ids = set()
    return requested if requested in ids else model


def _prepared(body: dict) -> dict:
    """请求体整形：工具 schema 压平，否则上游整轮失败。

    上游是 OpenAI 兼容端点，但它的 schema 转换器只吃朴素 JSON Schema ——
    `anyOf` / `$ref` / 数组型 `type` 会让它回「AI服务异常」这类无信息错误。
    客户端（尤其是 Anthropic / Responses 协议进来的）经常带这类 schema，
    所以统一在这里压平一次，两条转发路径共用。
    """
    payload = dict(body)
    tools = protocol.openai_tools_sanitized(body.get("tools"))
    if tools:
        payload["tools"] = tools
        if body.get("tool_choice") is not None:
            payload["tool_choice"] = body["tool_choice"]
    else:
        payload.pop("tools", None)
        payload.pop("tool_choice", None)
    return payload


def _prepare_key(account) -> str:
    key = fb_accounts.key_of(account)
    if not key:
        raise client.UpstreamError("账号缺少 API key，请重新导入",
                                   status=401, kind="auth")
    if endpoints.is_session_token(key):
        raise client.UpstreamError(endpoints.SESSION_TOKEN_MESSAGE,
                                   status=401, kind="auth",
                                   code="session_token")
    return key


def _attempt(db, request, key, body: dict, realm: str):
    model = _resolve_model(body, request)
    body = _prepared(dict(body, model=model))
    exclude: set[int] = set()
    last_error: client.UpstreamError | None = None

    for _ in range(MAX_ATTEMPTS):
        account = shared.select_account(
            db, storage=STORAGE, realm=realm, key=key, model=model,
            exclude_ids=exclude)
        if account is None:
            break
        if not shared.lease(account):
            exclude.add(account.id)
            continue
        started = time.perf_counter()
        try:
            api_key = _prepare_key(account)
            result = client.chat_completions(api_key, dict(body))
            result["model"] = model or result.get("model")
            fb_accounts.mark_key_ok(db, account)
            if not _has_content(result):
                shared.release(account)
                shared.note_failure(
                    db, account, kind="empty",
                    detail="上游返回 200 但内容为空", status=200,
                    model=model, public_platform=PUBLIC, cool_seconds=60)
                last_error = client.UpstreamError("上游返回空内容", status=502,
                                                  kind="empty")
                exclude.add(account.id)
                continue
            return result, account, started
        except client.UpstreamError as exc:
            shared.release(account)
            if exc.kind == "auth":
                fb_accounts.mark_key_rejected(db, account, exc.message)
            shared.note_failure(
                db, account, kind=exc.kind, detail=exc.detail or exc.message,
                status=exc.status, model=model, public_platform=PUBLIC,
                cool_seconds=_COOL.get(exc.kind, 0))
            last_error = exc
            exclude.add(account.id)
            # 请求本身有问题 / 免费额度风控：换号没用
            if exc.kind in ("fatal", "update_required"):
                break
        except Exception as exc:  # noqa: BLE001
            shared.release(account)
            logger.exception("Freebuff 调用失败 acc=%s", account.id)
            last_error = client.UpstreamError(str(exc)[:300], kind="network")
            exclude.add(account.id)
    if last_error is not None:
        raise last_error
    raise shared.PlatformRejected(
        503, "没有可用的 Freebuff 账号（请先在控制台导入 Developer API key）",
        kind="server_error", code="no_available_account",
        headers={"Retry-After": "3"})


async def _forward(body: dict, request) -> JSONResponse:
    from admin.db import SessionLocal

    realm = _realm(request)
    db = SessionLocal()
    try:
        key = shared.authenticate(db, request, platform=_public(request))
        try:
            result, account, started = _attempt(db, request, key, body, realm)
        except shared.PlatformRejected:
            raise
        except client.UpstreamError as exc:
            return _fail(exc)
        shared.release(account)
        shared.note_success(account)
        try:
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
        _bill(key, account, str(body.get("model") or ""), result,
              request=request, started=started)
        return JSONResponse(result)
    finally:
        db.close()


# ---------------------------------------------------------------- 流式：透传
def _open_stream_sync(api_key, body, model, account_id, started, stats):
    gen = client.chat_stream(api_key, body, prime=True)
    first = next(gen, None)
    stats["ttfb_ms"] = int((time.perf_counter() - started) * 1000)
    return gen, first


def _rewrite(raw: bytes, obj, want: str) -> bytes:
    """把帧里的 model 改回客户端请求名（客户端按 model 分流）。"""
    if not want or not isinstance(obj, dict):
        return raw
    if isinstance(obj.get("model"), str) and obj["model"] != want:
        obj["model"] = want
        return ("data: %s\n\n" % json.dumps(obj, ensure_ascii=False)).encode("utf-8")
    return raw


async def _forward_stream(body: dict, request):
    from admin.db import SessionLocal

    from starlette.concurrency import run_in_threadpool

    realm = _realm(request)
    db = SessionLocal()
    key = shared.authenticate(db, request, platform=_public(request))
    model = _resolve_model(body, request)
    body = _prepared(dict(body, model=model))
    stats: dict = {}
    exclude: set[int] = set()
    account = None
    gen = None
    first = None
    last_error = None
    started = time.perf_counter()

    for _ in range(MAX_ATTEMPTS):
        account = shared.select_account(
            db, storage=STORAGE, realm=realm, key=key, model=model,
            exclude_ids=exclude)
        if account is None:
            break
        if not shared.lease(account):
            exclude.add(account.id)
            continue
        try:
            api_key = _prepare_key(account)
            gen, first = await run_in_threadpool(
                _open_stream_sync, api_key, dict(body), model,
                int(account.id or 0), started, stats)
            fb_accounts.mark_key_ok(db, account)
            break
        except client.UpstreamError as exc:
            shared.release(account)
            if exc.kind == "auth":
                fb_accounts.mark_key_rejected(db, account, exc.message)
            shared.note_failure(
                db, account, kind=exc.kind, detail=exc.detail or exc.message,
                status=exc.status, model=model, public_platform=PUBLIC,
                cool_seconds=_COOL.get(exc.kind, 0))
            last_error = exc
            exclude.add(account.id)
            if exc.kind in ("fatal", "update_required"):
                break
        except Exception as exc:  # noqa: BLE001
            shared.release(account)
            logger.exception("Freebuff 流式调用失败 acc=%s", account.id)
            last_error = client.UpstreamError(str(exc)[:300], kind="network")
            exclude.add(account.id)

    if gen is None:
        db.close()
        if last_error is not None:
            return _fail(last_error)
        raise shared.PlatformRejected(
            503, "没有可用的 Freebuff 账号（请先在控制台导入 Developer API key）",
            kind="server_error", code="no_available_account",
            headers={"Retry-After": "3"})

    loop = asyncio.get_event_loop()
    holder: dict = {}

    async def agen():
        nonlocal first
        try:
            pending = first
            first = None
            while True:
                if pending is not None:
                    piece, pending = pending, None
                else:
                    nxt = await loop.run_in_executor(
                        None, lambda: next(gen, _SENTINEL))
                    if nxt is _SENTINEL:
                        break
                    piece = nxt
                if piece is None:
                    continue
                raw, obj = piece
                if isinstance(obj, dict):
                    usage = obj.get("usage")
                    if isinstance(usage, dict) and usage:
                        holder["usage"] = protocol.normalize_usage(usage)
                yield _rewrite(raw, obj, model)
        except client.UpstreamError as exc:
            yield ("data: %s\n\n" % json.dumps(
                {"error": {"message": exc.message, "type": exc.kind}},
                ensure_ascii=False)).encode("utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Freebuff 流式转发中断")
            yield ("data: %s\n\n" % json.dumps(
                {"error": {"message": str(exc)[:200], "type": "stream_error"}},
                ensure_ascii=False)).encode("utf-8")
        finally:
            stats.setdefault("total_ms",
                             int((time.perf_counter() - started) * 1000))
            try:
                gen.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                shared.release(account)
                shared.note_success(account)
                db.commit()
            except Exception:  # noqa: BLE001
                try:
                    db.rollback()
                except Exception:  # noqa: BLE001
                    pass
            _bill(key, account, model, {"usage": holder.get("usage") or {}},
                  request=request, started=started, stats=stats)
            db.close()

    return StreamingResponse(
        agen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- 流式：本地合成
async def _forward_synthesized(body: dict, request):
    """模型不支持流式：上游走非流式，本地合成标准 SSE 帧。"""
    resolved = _resolve_model(body, request)
    result_response = await _forward(dict(body, stream=False, model=resolved),
                                     request)
    if getattr(result_response, "status_code", 200) >= 400:
        return result_response
    try:
        result = json.loads(bytes(result_response.body).decode("utf-8"))
    except Exception:  # noqa: BLE001
        return result_response
    model = resolved

    def gen():
        yield from protocol.synthesize_sse(result, model)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no",
                                      "X-Freebuff-Stream": "synthesized"})


async def chat_completions(request):
    body = await _body_of(request)
    if not body.get("stream"):
        return await _forward(body, request)
    model = _resolve_model(body, request)
    if protocol.supports_streaming(_catalog(), model):
        return await _forward_stream(body, request)
    logger.info("Freebuff 模型 %s 上游不支持流式，本地合成 SSE 帧", model)
    return await _forward_synthesized(body, request)


# ---------------------------------------------------------------- Anthropic
def _anthropic_frames(result: dict, model: str):
    payload = protocol.openai_to_anthropic(result, model)
    text = payload["content"][0]["text"] if payload["content"] else ""
    usage = result.get("usage") or {}
    yield ("data: %s\n\n" % json.dumps({
        "type": "message_start",
        "message": {"id": payload["id"], "type": "message",
                    "role": "assistant", "model": payload["model"],
                    "content": [], "usage": {
                        "input_tokens": int(usage.get("prompt_tokens") or 0),
                        "output_tokens": 0}}}, ensure_ascii=False)).encode()
    yield ("data: %s\n\n" % json.dumps({
        "type": "content_block_start", "index": 0,
        "content_block": {"type": "text", "text": ""}},
        ensure_ascii=False)).encode()
    for offset in range(0, len(text), endpoints.SSE_CHUNK):
        yield ("data: %s\n\n" % json.dumps({
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta",
                      "text": text[offset:offset + endpoints.SSE_CHUNK]}},
            ensure_ascii=False)).encode()
    yield ("data: %s\n\n" % json.dumps({
        "type": "content_block_stop", "index": 0},
        ensure_ascii=False)).encode()
    yield ("data: %s\n\n" % json.dumps({
        "type": "message_delta",
        "delta": {"stop_reason": payload["stop_reason"]},
        "usage": {"output_tokens": int(usage.get("completion_tokens") or 0)}},
        ensure_ascii=False)).encode()
    yield ("data: %s\n\n" % json.dumps({"type": "message_stop"},
                                       ensure_ascii=False)).encode()


async def messages(request):
    body = await _body_of(request)
    model = _resolve_model(body, request)
    payload = protocol.anthropic_to_openai(body)
    payload["model"] = model

    from admin.db import SessionLocal

    realm = _realm(request)
    db = SessionLocal()
    try:
        key = shared.authenticate(db, request, platform=_public(request))
        try:
            result, account, started = _attempt(db, request, key, payload, realm)
        except shared.PlatformRejected:
            raise
        except client.UpstreamError as exc:
            return _fail(exc)
        shared.release(account)
        shared.note_success(account)
        try:
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
        result["model"] = model or result.get("model")
        _bill(key, account, model, result, request=request, started=started)

        if not body.get("stream"):
            return JSONResponse(protocol.openai_to_anthropic(result, model))

        def gen():
            yield from _anthropic_frames(result, model)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})
    finally:
        db.close()


async def count_tokens(request):
    body = await _body_of(request)
    text = ""
    system = body.get("system")
    if isinstance(system, str):
        text += system
    for item in (body.get("messages") or []):
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if isinstance(content, str):
            text += content
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    text += str(block.get("text") or "")
    return JSONResponse({"input_tokens": protocol.estimate_tokens(text)})


# ---------------------------------------------------------------- Responses
async def responses(request):
    body = await _body_of(request)
    model = _resolve_model(body, request)
    payload = protocol.responses_request_to_openai(body)
    payload["model"] = model

    from admin.db import SessionLocal

    realm = _realm(request)
    db = SessionLocal()
    try:
        key = shared.authenticate(db, request, platform=_public(request))
        try:
            result, account, started = _attempt(db, request, key, payload, realm)
        except shared.PlatformRejected:
            raise
        except client.UpstreamError as exc:
            return _fail(exc)
        shared.release(account)
        shared.note_success(account)
        try:
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
        _bill(key, account, model, result, request=request, started=started)

        final = protocol.openai_to_responses(result, model)
        if not body.get("stream"):
            return JSONResponse(final)

        def gen():
            yield ("data: %s\n\n" % json.dumps(
                {"type": "response.created",
                 "response": dict(final, status="in_progress", output=[])},
                ensure_ascii=False)).encode()
            for index, item in enumerate(final["output"]):
                yield ("data: %s\n\n" % json.dumps(
                    {"type": "response.output_item.added", "output_index": index,
                     "item": dict(item, status="in_progress", content=[])},
                    ensure_ascii=False)).encode()
                for cindex, block in enumerate(item.get("content") or []):
                    text = str(block.get("text") or "")
                    yield ("data: %s\n\n" % json.dumps(
                        {"type": "response.content_part.added",
                         "item_id": item["id"], "output_index": index,
                         "content_index": cindex,
                         "part": {"type": "output_text", "text": "",
                                  "annotations": []}},
                        ensure_ascii=False)).encode()
                    for offset in range(0, len(text), endpoints.SSE_CHUNK):
                        yield ("data: %s\n\n" % json.dumps(
                            {"type": "response.output_text.delta",
                             "item_id": item["id"], "output_index": index,
                             "content_index": cindex,
                             "delta": text[offset:offset + endpoints.SSE_CHUNK]},
                            ensure_ascii=False)).encode()
                    yield ("data: %s\n\n" % json.dumps(
                        {"type": "response.output_text.done",
                         "item_id": item["id"], "output_index": index,
                         "content_index": cindex, "text": text},
                        ensure_ascii=False)).encode()
                yield ("data: %s\n\n" % json.dumps(
                    {"type": "response.output_item.done",
                     "output_index": index, "item": item},
                    ensure_ascii=False)).encode()
            yield ("data: %s\n\n" % json.dumps(
                {"type": "response.completed", "response": final},
                ensure_ascii=False)).encode()

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})
    finally:
        db.close()


# ---------------------------------------------------------------- models / balance
async def list_models(request):
    """/v1/models：上游目录（280 个，只有 id + capabilities）合并内置元数据。"""
    from admin.db import SessionLocal

    db = SessionLocal()
    try:
        shared.authenticate(db, request, platform=PUBLIC)
        rows, note = fb_accounts.catalog_rows()
        out = [{
            "id": item["id"],
            "object": "model",
            "created": 0,
            "owned_by": PUBLIC,
            "name": item.get("name") or item["id"],
            "context_window": item.get("context_window"),
            "x_freebuff": {
                "reasoning": item.get("reasoning"),
                "vision": item.get("vision"),
                "toolcall": item.get("toolcall"),
                "streaming": item.get("streaming"),
                "default": item.get("default"),
            },
        } for item in rows]
        payload = {"object": "list", "data": out}
        if note:
            payload["note"] = note
        return JSONResponse(payload)
    finally:
        db.close()


async def balance(request):
    """上游没有余额接口：只报本地账本与 key 校验状态，不编数字。"""
    from admin.db import SessionLocal

    realm = _realm(request)
    db = SessionLocal()
    try:
        shared.authenticate(db, request, platform=PUBLIC)
        rows = fb_accounts.accounts_for(db, realm)
        verified = sum(1 for account in rows
                       if fb_accounts.credentials_of(account).get("verify_state")
                       == "ok")
        return JSONResponse({
            "platform": PUBLIC,
            "realm": realm,
            "accounts": len(rows),
            "verified": verified,
            "note": "Freebuff 上游只有 /models 与 /chat/completions，没有余额接口；"
                    "额度请在 freebuff.com 控制台查看，本网关只记本地用量",
        })
    finally:
        db.close()


# ---------------------------------------------------------------- dispatch
async def dispatch(request, path: str):
    try:
        if path == "/v1/chat/completions":
            return await chat_completions(request)
        if path == "/v1/messages":
            return await messages(request)
        if path == "/v1/messages/count_tokens":
            return await count_tokens(request)
        if path == "/v1/responses":
            return await responses(request)
        if path == "/v1/models":
            return await list_models(request)
        if path == "/v1/balance":
            return await balance(request)
    except shared.PlatformRejected as exc:
        return JSONResponse({"error": {"message": exc.message,
                                       "type": exc.kind}},
                            status_code=exc.status)
    return JSONResponse({"error": {"message": "not found"}}, status_code=404)