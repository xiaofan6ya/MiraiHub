"""WPS 灵犀 /v1/* 转发（供 dispatch.py 调用）。

## 三条对外协议

    /v1/chat/completions   OpenAI      —— 上游是自定义 SSE 帧，本地逐帧翻译
    /v1/messages           Anthropic   —— 本地把 messages 翻成 OpenAI 再打
    /v1/responses          Responses   —— 本地翻译
    /v1/models /v1/balance

## 与 Loomy / 小浣熊适配的区别

1. **上游不是 OpenAI 帧**：正文在 `choices[].text`（不是 `delta.content`），
   收尾靠 `finish_reason` 非空（上游不发 `[DONE]`，本地补）。
2. **一个 run 覆盖多次调用**：建会话 + 领 run 的结果按「账号 × 模型」缓存
   1 小时，命中就只打第 ③ 步，少扣一次智点。
3. **审核拦截要单独成一类**：`AuditFailed` 是上游内容审核，换号没用，
   直接 400 报给客户端，不重试、不重建会话。

## 记账

统一走 `admin.platform.shared`：`authenticate` / `select_account` /
`lease` / `record_usage`，与其它平台同一套账本。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from starlette.responses import JSONResponse, StreamingResponse

from admin.platform import shared
from admin.platform.wps_lingxi.core import client, endpoints, protocol
from admin.platform.wps_lingxi.services import accounts as lingxi_accounts

logger = logging.getLogger("admin.platform.wps_lingxi.gateway")

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

#: 换号重试上限。灵犀单号可能因为审核 / 智点用尽失效，多试几个号。
MAX_ATTEMPTS = 4

#: 各类失败的冷却秒数。`quota` 要长冷却（智点耗尽不会自己回血）。
_COOL = {"auth": 900, "rate": 300, "quota": 3600, "network": 20,
         "audit": 0, "fatal": 0, "upstream": 30}

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
    return realm or "cn"


def _public(request) -> str:
    return shared.public_platform_of(request, PUBLIC)


def _client_ip(request) -> str:
    try:
        from admin.client_ip import request_ip

        return request_ip(request)
    except Exception:  # noqa: BLE001
        return ""


def _fail(exc: client.UpstreamError) -> JSONResponse:
    try:
        logger.warning("灵犀上游失败 kind=%s status=%s msg=%s detail=%s",
                       getattr(exc, "kind", ""), getattr(exc, "status", ""),
                       str(getattr(exc, "message", ""))[:200],
                       str(getattr(exc, "detail", ""))[:400])
    except Exception:  # noqa: BLE001
        pass
    status = exc.status or 502
    if exc.kind == "audit":
        status = 400
    elif status < 400 or status > 599:
        status = 502
    return JSONResponse(
        {"error": {"type": exc.kind, "code": exc.code or "upstream_error",
                   "message": exc.message}},
        status_code=status)


def _has_content(result: dict) -> bool:
    """答复是否真有内容（空答复要换号重试）。

    `tool_calls` / `reasoning_content` / `finish_reason` 三者任一非空都算
    正常 —— 推理模型把预算烧在思维链上时正文就是空的。
    """
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
                use_case="wps-lingxi-chat", seq=1,
                ttfb_ms=ttfb, latency_ms=latency)
    except Exception:  # noqa: BLE001
        logger.exception("灵犀记账失败")


# ---------------------------------------------------------------- 非流式
def _attempt(db, request, key, body: dict, realm: str):
    """选号 + 租约 + 调上游（换号重试）。"""
    model = str(body.get("model") or "")
    upstream_model = protocol.normalize_model(model)
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
        cookie = lingxi_accounts.cookie_of(account)
        started = time.perf_counter()
        try:
            if not cookie:
                raise client.UpstreamError(
                    "账号缺少 Cookie，请重新导入", status=401, kind="auth")
            payload, dropped, elided = protocol.prepare_agent_body(
                {**body, "model": upstream_model})
            if dropped or elided:
                logger.info("灵犀提示词裁剪 acc=%s 省略 %d 条、截断 %d 条",
                            account.id, dropped, elided)
            retried = False
            while True:
                run = client.ensure_run(cookie, upstream_model,
                                        messages=body.get("messages"))
                try:
                    result = client.collect(cookie, run, payload,
                                            model=upstream_model)
                    break
                except client.UpstreamError as exc:
                    # 会话失效 → 重建一次；审核拦截绝不重建
                    if retried or exc.kind == "audit" \
                            or not client.looks_stale(exc.message):
                        raise
                    retried = True
                    client.forget_run(cookie, upstream_model)
            result["model"] = model or result.get("model")
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
            shared.note_failure(
                db, account, kind=exc.kind, detail=exc.detail or exc.message,
                status=exc.status, model=model, public_platform=PUBLIC,
                cool_seconds=_COOL.get(exc.kind, 0))
            last_error = exc
            exclude.add(account.id)
            # 审核 / 请求本身有问题：换号也没用
            if exc.kind in ("audit", "fatal"):
                break
        except Exception as exc:  # noqa: BLE001
            shared.release(account)
            logger.exception("灵犀调用失败 acc=%s", account.id)
            last_error = client.UpstreamError(str(exc)[:300], kind="network")
            exclude.add(account.id)
    if last_error is not None:
        raise last_error
    raise shared.PlatformRejected(
        503, "没有可用的 WPS 灵犀账号（请先在控制台导入并确认状态为启用）",
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


# ---------------------------------------------------------------- 流式
def _open_stream_sync(cookie, run, body, model, account_id, started, stats):
    """在工作线程里建连并读第一帧。

    两点都是必需的：
    1. httpx 同步流在事件循环里直接建连会和 uvicorn 争 SSL，实测稳定复现
       `UNEXPECTED_EOF_WHILE_READING`，所以丢进线程池；
    2. 先读一帧才能把上游 4xx/5xx 翻成正常 HTTP 状态码（否则客户端只看到
       「200 + 空流」），顺便量到真实 ttfb。
    """
    gen = client.chat_stream(cookie, run, body, model=model, prime=True)
    first = next(gen, None)
    stats["ttfb_ms"] = int((time.perf_counter() - started) * 1000)
    return gen, first


async def _forward_stream(body: dict, request):
    from admin.db import SessionLocal

    from starlette.concurrency import run_in_threadpool

    realm = _realm(request)
    db = SessionLocal()
    key = shared.authenticate(db, request, platform=_public(request))
    model = str(body.get("model") or "")
    upstream_model = protocol.normalize_model(model)
    stats: dict = {}
    translate = protocol.FrameTranslator(model or upstream_model)
    exclude: set[int] = set()
    account = None
    gen = None
    first = None
    last_error = None
    started = time.perf_counter()

    try:
        payload, dropped, elided = protocol.prepare_agent_body(
            {**body, "model": upstream_model})
        if dropped or elided:
            logger.info("灵犀提示词裁剪（流式）省略 %d 条、截断 %d 条",
                        dropped, elided)
    except Exception as exc:  # noqa: BLE001
        db.close()
        return _fail(client.UpstreamError(str(exc)[:200], kind="fatal"))

    for _ in range(MAX_ATTEMPTS):
        account = shared.select_account(
            db, storage=STORAGE, realm=realm, key=key, model=model,
            exclude_ids=exclude)
        if account is None:
            break
        if not shared.lease(account):
            exclude.add(account.id)
            continue
        cookie = lingxi_accounts.cookie_of(account)
        try:
            if not cookie:
                raise client.UpstreamError("账号缺少 Cookie，请重新导入",
                                           status=401, kind="auth")
            retried = False
            while True:
                run = client.ensure_run(cookie, upstream_model,
                                        messages=body.get("messages"))
                try:
                    gen, first = await run_in_threadpool(
                        _open_stream_sync, cookie, run, payload,
                        upstream_model, int(account.id or 0), started, stats)
                    break
                except client.UpstreamError as exc:
                    if retried or exc.kind == "audit" \
                            or not client.looks_stale(exc.message):
                        raise
                    retried = True
                    client.forget_run(cookie, upstream_model)
            break
        except client.UpstreamError as exc:
            shared.release(account)
            shared.note_failure(
                db, account, kind=exc.kind, detail=exc.detail or exc.message,
                status=exc.status, model=model, public_platform=PUBLIC,
                cool_seconds=_COOL.get(exc.kind, 0))
            last_error = exc
            exclude.add(account.id)
            if exc.kind in ("audit", "fatal"):
                break
        except Exception as exc:  # noqa: BLE001
            shared.release(account)
            logger.exception("灵犀流式调用失败 acc=%s", account.id)
            last_error = client.UpstreamError(str(exc)[:300], kind="network")
            exclude.add(account.id)

    if gen is None:
        db.close()
        if last_error is not None:
            return _fail(last_error)
        raise shared.PlatformRejected(
            503, "没有可用的 WPS 灵犀账号（请先在控制台导入并确认状态为启用）",
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
                    frame, pending = pending, None
                else:
                    nxt = await loop.run_in_executor(
                        None, lambda: next(gen, _SENTINEL))
                    if nxt is _SENTINEL:
                        break
                    frame = nxt
                if frame is None:
                    continue
                for chunk in translate.feed(frame):
                    yield chunk
            # 上游没有 [DONE]：本地补 finish + usage + [DONE]
            for chunk in translate.tail():
                yield chunk
            if translate.usage:
                holder["usage"] = translate.usage
        except client.UpstreamError as exc:
            payload = {"error": {"message": exc.message, "type": "upstream_error",
                                 "code": exc.code or "upstream_error"}}
            yield ("data: %s\n\n" % json.dumps(payload, ensure_ascii=False)).encode()
        except Exception as exc:  # noqa: BLE001
            logger.exception("灵犀流式转发中断")
            yield ("data: %s\n\n" % json.dumps(
                {"error": {"message": str(exc)[:200], "type": "stream_error"}},
                ensure_ascii=False)).encode()
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
            _bill(key, account, model,
                  {"usage": holder.get("usage") or translate.usage or {}},
                  request=request, started=started, stats=stats)
            db.close()

    return StreamingResponse(
        agen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def chat_completions(request):
    body = await _body_of(request)
    if not body.get("stream"):
        return await _forward(body, request)
    return await _forward_stream(body, request)


# ---------------------------------------------------------------- Anthropic
def _sse(event: str, payload: dict) -> bytes:
    """Anthropic SSE 帧：**必须同时有 `event:` 与 `data:` 两行**。

    Anthropic 官方流式协议是

        event: content_block_delta
        data: {"type":"content_block_delta", ...}

    客户端按 **事件名** 分派处理器。只发 `data:` 行时，客户端拿到的事件名
    是 SSE 规范默认值 `message`，`content_block_delta` / `message_stop`
    一个都匹配不上 —— 表现是「HTTP 200、有字节流、但消息零内容块」。
    （这正是 PR #20 报告的问题；本平台一直没修。）
    """
    return ("event: %s\ndata: %s\n\n"
            % (event, json.dumps(payload, ensure_ascii=False))).encode()


def _anthropic_frames(result: dict, model: str):
    """OpenAI 聚合结果 → Anthropic 流式帧（含 tool_use 与缓存 usage）。

    与官方协议的对应关系：

        message_start        usage: input_tokens + cache_read_input_tokens
        content_block_start  (text / tool_use 各一个块)
        content_block_delta  text_delta / input_json_delta
        content_block_stop
        message_delta        usage: output_tokens（+ 缓存字段）
        message_stop
    """
    payload = protocol.openai_to_anthropic(result, model)
    usage = result.get("usage") or {}
    anthropic_usage = protocol._anthropic_usage(usage)  # noqa: SLF001 - 同包协作
    input_usage = dict(anthropic_usage)
    input_usage["output_tokens"] = 0

    yield _sse("message_start", {
        "type": "message_start",
        "message": {"id": payload["id"], "type": "message",
                    "role": "assistant", "model": payload["model"],
                    "content": [], "usage": input_usage},
    })

    index = 0
    for block in payload.get("content") or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            text = str(block.get("text") or "")
            yield _sse("content_block_start", {
                "type": "content_block_start", "index": index,
                "content_block": {"type": "text", "text": ""}})
            for offset in range(0, len(text), 48):
                yield _sse("content_block_delta", {
                    "type": "content_block_delta", "index": index,
                    "delta": {"type": "text_delta",
                              "text": text[offset:offset + 48]}})
            yield _sse("content_block_stop",
                       {"type": "content_block_stop", "index": index})
            index += 1
        elif block.get("type") == "tool_use":
            # 工具调用块：**不能静默丢弃** —— 丢了客户端就以为模型没调工具，
            # 任务直接卡死（旧实现只取 content[0] 的 text）。
            yield _sse("content_block_start", {
                "type": "content_block_start", "index": index,
                "content_block": {"type": "tool_use", "id": block.get("id") or "",
                                  "name": block.get("name") or "", "input": {}}})
            args = json.dumps(block.get("input") or {}, ensure_ascii=False)
            yield _sse("content_block_delta", {
                "type": "content_block_delta", "index": index,
                "delta": {"type": "input_json_delta", "partial_json": args}})
            yield _sse("content_block_stop",
                       {"type": "content_block_stop", "index": index})
            index += 1

    yield _sse("message_delta", {
        "type": "message_delta",
        "delta": {"stop_reason": payload["stop_reason"]},
        "usage": anthropic_usage,
    })
    yield _sse("message_stop", {"type": "message_stop"})


async def messages(request):
    body = await _body_of(request)
    model = str(body.get("model") or "")
    payload = protocol.anthropic_to_openai(body)
    payload["model"] = protocol.normalize_model(model)

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
    model = str(body.get("model") or "")
    payload = protocol.responses_request_to_openai(body)
    payload["model"] = protocol.normalize_model(model)

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
                    for offset in range(0, len(text), 48):
                        yield ("data: %s\n\n" % json.dumps(
                            {"type": "response.output_text.delta",
                             "item_id": item["id"], "output_index": index,
                             "content_index": cindex,
                             "delta": text[offset:offset + 48]},
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
    """/v1/models：优先拉上游 plans，失败回落「配置目录」或内置快照。"""
    from admin.db import SessionLocal

    db = SessionLocal()
    try:
        shared.authenticate(db, request, platform=PUBLIC)
        realm = _realm(request)
        account = shared.select_account(
            db, storage=STORAGE, realm=realm,
            key=shared.key_row(db, request))
        fallback = shared.catalog_from_db(db, STORAGE)
        models: list[dict] = []
        note = ""
        if account is not None:
            cookie = lingxi_accounts.cookie_of(account)
            if cookie:
                try:
                    models = client.catalog_of(
                        client.fetch_plans(cookie, lingxi_accounts.base_of(account)))
                except client.UpstreamError as exc:
                    note = "上游目录拉取失败：%s" % exc.message
        else:
            note = "号池没有可用的灵犀账号"
        if not models:
            models = endpoints.default_catalog() if not fallback else []
        out = [{
            "id": item["id"],
            "object": "model",
            "created": 0,
            "owned_by": PUBLIC,
            "name": item.get("name") or item["id"],
            "description": item.get("description") or "",
            "context_window": item.get("context_window"),
            "x_lingxi": {"multiplier": item.get("multiplier"),
                         "reasoning": item.get("reasoning"),
                         "vision": item.get("vision"),
                         "default": item.get("default")},
        } for item in (models or fallback)]
        payload = {"object": "list", "data": out}
        if note:
            payload["note"] = note
        return JSONResponse(payload)
    finally:
        db.close()


async def balance(request):
    from admin.db import SessionLocal

    realm = _realm(request)
    db = SessionLocal()
    try:
        shared.authenticate(db, request, platform=PUBLIC)
        account = shared.select_account(
            db, storage=STORAGE, realm=realm,
            key=shared.key_row(db, request))
        if account is None:
            return JSONResponse({"platform": PUBLIC, "realm": realm,
                                 "error": "no_account"}, status_code=200)
        try:
            result = lingxi_accounts.sync_quota(db, account)
        except client.UpstreamError as exc:
            return JSONResponse({"platform": PUBLIC, "realm": realm,
                                 "error": exc.message}, status_code=200)
        quota = result.get("quota") or (result.get("error") or {})
        return JSONResponse({"platform": PUBLIC, "realm": realm, **quota})
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