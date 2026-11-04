"""CodeArts /v1/* 转发实现（供 dispatch.py 调用）。

## 与其它平台最大的不同：上游本身就是 OpenAI 协议

`POST /api/v2/chat/completions` 的请求体与响应体与 OpenAI 一致
（连 `usage.completion_tokens_details.reasoning_tokens` 都有），
`stream: true` 是真 SSE。所以这里**不做字段翻译**：

    OpenAI  ──原样──▶  CodeArts
    Anthropic / Responses ──protocol_bridge 转一次──▶ CodeArts

真正的工作量在别处：
  · 每个请求要现算 AK/SK 签名（`core/signer`）；
  · 临时凭证 6 小时过期，要能自动续（`core/session.refresh_session`）；
  · 选号要跳过 0 积分的号，否则必然 402。

## 并发：上游限的是「会话」不是「在途请求」

CodeArts 上游对**每个账号**限制「同时存活的会话数 = 3」，而会话只在**流式**
请求时建立、且在请求结束后仍滞留约 50 秒（实测，见
`core/session_window.py` 的实测记录）。这与网关通用的在途闸门
（`admin.pool.InFlight`，数的是在途请求）**口径不同**：

    网关认为「这个号空了」（在途 0），上游认为「这个号还有 3 个会话」

所以撞上限不是偶发，而是必然。处理方式：

  * **发起上游请求前**先在 `core.session_window` 占位，窗口满就换号；
  * 只有一个号时**等待**窗口腾出空位，而不是打上游一个注定失败的 400；
  * 上游 4xx 拒绝时**回滚**占位（那种情况会话并未建立）。

## 鉴权 / 选号 / 记账

统一走 `admin.platform.shared`（与其它平台同一套）：
`authenticate()` 校验 Key+配额，`select_account()` 过滤健康/归属/冷却/在途，
`record_usage()` 写带 platform 的账本。**不要自己写一份**，
否则「用量日志里看不到这个平台」「并发不限号」这类问题会原样复现。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from starlette.responses import JSONResponse, StreamingResponse

from admin.platform import protocol_bridge as bridge
from admin.platform import shared
from admin.platform.codearts.core import client, session_window
from admin.platform.codearts.services import accounts as ca

logger = logging.getLogger("admin.platform.codearts.gateway")

PATHS = (
    "/v1/chat/completions",
    "/v1/messages",
    "/v1/messages/count_tokens",
    "/v1/responses",
    "/v1/models",
    "/v1/balance",
)

PUBLIC = ca.PLATFORM
MAX_ATTEMPTS = 3


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


def _public(request) -> str:
    return shared.public_platform_of(request, PUBLIC)


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
    """把上游错误翻成 OpenAI 风格。

    日志里必须留上游原文：华为云的错误体（`APIG.0301 ... signature fail`）
    是排障的唯一线索，只回一句「请求失败」等于什么也没说。
    """
    try:
        logger.warning("CodeArts 上游失败 kind=%s status=%s msg=%s detail=%s",
                       exc.kind, exc.status, str(exc.message)[:200],
                       str(exc.detail)[:600])
    except Exception:  # noqa: BLE001
        pass
    status = exc.status or 502
    if status < 400 or status > 599:
        status = 502
    return JSONResponse(
        {"error": {"type": exc.kind, "code": exc.code or "upstream_error",
                   "message": exc.message}},
        status_code=status)


# ---------------------------------------------------------------------------
# 选号 + 调用
# ---------------------------------------------------------------------------
def _attempt(db, request, key, body: dict, realm: str):
    model = str(body.get("model") or "")
    exclude: set[int] = set()
    last_error: client.UpstreamError | None = None
    for _ in range(MAX_ATTEMPTS):
        account = shared.select_account(
            db, storage=ca.PLATFORM, realm=realm, key=key, model=model,
            exclude_ids=exclude,
            # 0 积分的号调用必被上游打回，别浪费一次尝试
            min_balance=0)
        if account is None:
            break
        if not shared.lease(account):
            exclude.add(account.id)
            continue
        started = time.perf_counter()
        try:
            credential = ca.ensure_credential(db, account)
            result = client.chat_completions(credential, body,
                                             account_id=int(account.id or 0))
            return result, account, started
        except client.UpstreamError as exc:
            shared.release(account)
            shared.note_failure(
                db, account, kind=exc.kind, detail=exc.detail or exc.message,
                status=exc.status, model=model, public_platform=PUBLIC,
                cool_seconds=(1800 if exc.kind in ("auth", "quota_exceeded",
                                                   "signature_invalid") else 0))
            last_error = exc
            if not exc.retryable():
                break
            exclude.add(account.id)
        except Exception as exc:  # noqa: BLE001
            shared.release(account)
            logger.exception("CodeArts 调用失败 acc=%s", account.id)
            last_error = client.UpstreamError(str(exc), kind="network")
            exclude.add(account.id)
        continue
    if last_error is not None:
        raise last_error
    raise shared.PlatformRejected(
        503, "CodeArts 没有可用账号（请先在控制台导入并确认状态为启用）",
        kind="server_error", code="no_available_account",
        headers={"Retry-After": "3"})


def _bill(key, account, model: str, usage: dict | None, *, request,
          started=None, error: str = "", stats: dict | None = None,
          body: dict | None = None) -> None:
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
        with SessionLocal() as db:
            shared.record_usage(
                db, key_id=int(getattr(key, "id", 0) or 0),
                account_id=int(getattr(account, "id", 0) or 0),
                model=model, usage=usage or {},
                public_platform=PUBLIC, storage=ca.PLATFORM,
                error_kind=error, client_ip=_client_ip(request),
                use_case="codearts-chat", seq=1,
                ttfb_ms=ttfb or None, latency_ms=latency,
                # 思考档位：不传的话用量日志里永远显示「默认 / 未记录」，
                # 用户会以为网关把 reasoning_effort 吞了（实际是没记）
                reasoning_effort=shared.reasoning_effort_of(body or {}))
    except Exception:  # noqa: BLE001
        logger.exception("CodeArts 记账失败")


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
        _bill(key, account, str(body.get("model") or ""),
              (result or {}).get("usage"), request=request, started=started,
              body=body)
        return JSONResponse(result)
    finally:
        db.close()


async def chat_completions(request):
    body = await _body_of(request)
    if not body.get("stream"):
        return await _forward(body, request)
    return await _forward_stream(body, request)


async def reserve_slot(uid: str, deadline: float):
    """在会话窗口里占一个名额；满了就在 `deadline` 之前排队等。

    返回 `(slot, waited, timed_out)`：

    * `slot` —— 占到了返回 token，超时返回 None；
    * `waited` —— 有没有真的等过（用来区分"号不可用"和"号忙"）；
    * `timed_out` —— 等到了截止时刻仍然没有空位。

    等待必须**不消耗换号次数**：只有一个号时，换号解决不了任何问题，
    而 5 路并发里第 4/5 个请求的等待时间会超过 MAX_ATTEMPTS 的量级
    （实测第 3/4 路等满 56s 后被判成"没有可用账号"，是错的）。
    """
    waited = False
    while True:
        slot = session_window.WINDOW.try_acquire(uid)
        if slot is not None:
            return slot, waited, False
        wait = session_window.WINDOW.wait_time(uid)
        remaining = deadline - time.perf_counter()
        # `wait_time == 0` 仍可能拿不到名额（别的线程刚好抢走）：
        # 让出 50ms 再试，别当场判"忙"。
        wait = wait if wait > 0 else 0.05
        if wait > remaining:
            return None, True, True
        waited = True
        await asyncio.sleep(min(wait, remaining) + 0.05)


async def _forward_stream(body: dict, request):
    """真流式：上游 SSE 逐帧转发。

    与非流式路径的关键差异：**流式请求会在上游建立会话**，而会话按账号
    计数、请求结束后仍滞留约 50 秒（实测，见 `core/session_window.py`）。
    所以这里必须先在会话窗口里占位，满了就换号/等待。
    """
    from admin.db import SessionLocal

    # 强制要 usage 尾帧：不带 `include_usage` 时上游不发 usage，
    # 账本就只能记 0 token（「调用有记录但没消耗」的典型成因）。
    options = body.get("stream_options")
    if not isinstance(options, dict) or not options.get("include_usage"):
        body = {**body, "stream_options": {"include_usage": True}}

    realm = _realm(request)
    db = SessionLocal()
    key = shared.authenticate(db, request, platform=_public(request))
    model = str(body.get("model") or "")
    stats: dict = {}
    exclude: set[int] = set()
    account = None
    chunks = None
    slot = None
    last_error: client.UpstreamError | None = None
    started = time.perf_counter()
    #: 「等容量」的截止时刻。排队（无论是在途租约满还是会话窗口满）
    #: **不消耗** MAX_ATTEMPTS —— 换号次数是给「上游真的失败」用的。
    #: 旧实现把两者混在一个 `for attempt in range(3)` 里，5 路并发时
    #: 第 3/4 路会立刻被判定成「没有可用账号」（实测两次都这样）。
    wait_deadline = started + session_window.DEFAULT_WAIT_TIMEOUT
    waited = False
    saw_account = False

    attempts = 0
    while attempts < MAX_ATTEMPTS:
        if time.perf_counter() >= wait_deadline:
            break
        account = shared.select_account(
            db, storage=ca.PLATFORM, realm=realm, key=key, model=model,
            exclude_ids=exclude, min_balance=0,
            # 在途满 ≠ 没有号：返回最闲的那个，让下面的 lease 失败分支排队。
            # 否则单账号池并发时这里直接返回 None，被报成「没有可用账号」。
            ignore_capacity=True)
        if account is None:
            break
        saw_account = True
        if not shared.lease(account):
            # 在途租约满（单号并发上限）：**不是**「这个号不可用」，
            # 别把它加进 exclude —— 加进去就再也不会被选中了。
            waited = True
            await asyncio.sleep(0.2)
            continue

        # 会话窗口占位：上游按账号限「同时存活的会话数」，这与在途闸门
        # 不是一回事 —— 在途数在这里可能是 0，但上游那边仍有会话没释放。
        uid = account.uid or ""
        slot, waited_window, _timed_out = await reserve_slot(uid, wait_deadline)
        waited = waited or waited_window
        if slot is None:
            # 等到超时仍是满的：换谁都是同一个窗口，如实报「忙」。
            # 必须把在途租约还回去，否则这个号会被自己的租约永久占住。
            shared.release(account)
            break
        attempts += 1
        try:
            credential = ca.ensure_credential(db, account)
            chunks = client.chat_stream(credential, body,
                                        account_id=int(account.id or 0),
                                        stats=stats)
            break
        except client.UpstreamError as exc:
            shared.release(account)
            # 上游拒绝了请求 → 会话**没有**建立 → 把名额还回去。
            session_window.WINDOW.rollback(uid, slot)
            slot = None
            shared.note_failure(
                db, account, kind=exc.kind, detail=exc.detail or exc.message,
                status=exc.status, model=model, public_platform=PUBLIC,
                cool_seconds=(1800 if exc.kind in ("auth", "quota_exceeded",
                                                   "signature_invalid") else 0))
            last_error = exc
            exclude.add(account.id)
            if not exc.retryable():
                break
        except Exception as exc:  # noqa: BLE001
            shared.release(account)
            session_window.WINDOW.rollback(uid, slot)
            slot = None
            logger.exception("CodeArts 流式调用失败 acc=%s", account.id)
            last_error = client.UpstreamError(str(exc), kind="network")
            exclude.add(account.id)

    if chunks is None:
        db.close()
        if last_error is not None:
            return _fail(last_error)
        if saw_account and waited:
            # 报「忙」而不是「没有可用账号」：号是好的，只是上游按账号
            # 限「3 个会话 / 约 55 秒」（或在途租约满），此刻排不上。
            # 给 Retry-After 让客户端待会儿再来，而不是让人去后台查一个
            # 其实存在、状态也正常的号。
            raise shared.PlatformRejected(
                429, "CodeArts 上游会话窗口已满（每账号 3 个 / 约 55 秒），"
                     "请稍后重试", kind="rate_limit_error",
                code="session_window_full", headers={"Retry-After": "30"})
        raise shared.PlatformRejected(
            503, "CodeArts 没有可用账号（请先在控制台导入并确认状态为启用）",
            kind="server_error", code="no_available_account",
            headers={"Retry-After": "3"})

    holder: dict = {}

    def generator():
        try:
            yield from _sse_from_chunks(chunks, model, holder)
        except client.UpstreamError as exc:
            holder["error"] = exc.kind or "upstream_error"
            yield ("data: %s\n\n" % json.dumps(
                {"error": {"message": exc.message, "type": exc.kind,
                           "code": exc.code}},
                ensure_ascii=False)).encode("utf-8")
            # 必须补终止帧：只发一个 error 帧就断流，严格客户端会报
            # 「Stream ended without finish_reason」并按退避反复重试。
            yield _terminal_frame(model)
        finally:
            # 会话占位从**流结束**开始计时（上游按空闲算滞留）：
            # 一次跑了 5 秒的流，占位总时长是 5 + 55，而不是 55。
            # 不戳这一下，闸门会比上游早放行 ~「流的时长」，实测导致
            # 放行后仍被 400 TM.00001041 打回。
            try:
                session_window.WINDOW.touch(account.uid or "", slot)
            except Exception:  # noqa: BLE001 - 观测性操作，绝不影响收尾
                logger.debug("CodeArts 会话窗口 touch 失败", exc_info=True)
            try:
                shared.release(account)
                # 流内错误不是「调用成功」：别把坏响应记成绿勾，也别重置
                # 账号的连续失败计数（但也不惩罚账号，错在请求体/上游）。
                if not holder.get("error"):
                    shared.note_success(account)
                db.commit()
            except Exception:  # noqa: BLE001
                try:
                    db.rollback()
                except Exception:  # noqa: BLE001
                    pass
            _bill(key, account, model, holder.get("usage"), request=request,
                  started=started, stats=stats, body=body,
                  error=str(holder.get("error") or ""))
            db.close()

    # 上游慢思考时整条流可能几十秒无字节；8 秒一帧注释让「链路活着」可见
    safe = bridge.with_pings(generator(), ping=bridge.CHAT_PING)
    return StreamingResponse(
        safe, media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _terminal_frame(model: str) -> bytes:
    """流内错误时补齐 OpenAI 终止序列（finish_reason + [DONE]）。

    上游把错误塞在 200 的 SSE 里时，`_sse_from_chunks` 会因异常提前退出，
    末尾的 `[DONE]` 根本发不出去；客户端把「无 finish_reason 的结束」判成
    失败并按退避反复重试。这里显式收尾，让它正常结束并看到 error 帧内容。
    """
    chunk = {
        "id": "chatcmpl-codearts-error",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model or "",
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    return ("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n"
            "data: [DONE]\n\n").encode("utf-8")


def _sse_from_chunks(chunks, model: str, holder: dict):
    """上游帧 -> 下发帧。

    上游已经是 OpenAI chunk，这里只做三件事：
      1. 补 `model`（有些帧不带，客户端拿不到会显示空模型）；
      2. 把末帧 `usage` 收进 `holder`（记账用）；
      3. 保证结束时一定发 `[DONE]`（漏了客户端会一直等）。
    """
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        usage = chunk.get("usage")
        if isinstance(usage, dict) and usage:
            holder["usage"] = usage
        if not chunk.get("model") and model:
            chunk["model"] = model
        yield ("data: %s\n\n" % json.dumps(chunk, ensure_ascii=False)).encode("utf-8")
    yield b"data: [DONE]\n\n"


# ---------------------------------------------------------------------------
# 其它协议
# ---------------------------------------------------------------------------
async def messages(request):
    """Anthropic 协议：工具调用经协议桥全量映射。"""
    body = await _body_of(request)
    normalized = bridge.anthropic_to_openai(body)
    model = str(body.get("model") or "")
    if not body.get("stream"):
        normalized["stream"] = False
        response = await _forward(normalized, request)
        if getattr(response, "status_code", 200) >= 400:
            return response
        try:
            payload = json.loads(response.body)
        except Exception:  # noqa: BLE001
            return response
        return JSONResponse(bridge.openai_to_anthropic(payload, model))
    inner = await _forward_stream(normalized, request)
    if getattr(inner, "status_code", 200) >= 400:
        return inner
    return bridge.anthropic_stream_relay(inner, model, prefix="codearts")


async def count_tokens(request):
    """本地估算（上游没有 count_tokens 接口）。"""
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


async def responses(request):
    """OpenAI Responses 协议：工具调用经协议桥全量映射。"""
    body = await _body_of(request)
    normalized = bridge.responses_to_openai(body)
    model = str(body.get("model") or "")
    if not body.get("stream"):
        normalized["stream"] = False
        response = await _forward(normalized, request)
        if getattr(response, "status_code", 200) >= 400:
            return response
        try:
            payload = json.loads(response.body)
        except Exception:  # noqa: BLE001
            return response
        return JSONResponse(bridge.openai_to_responses(payload, model))
    inner = await _forward_stream(normalized, request)
    if getattr(inner, "status_code", 200) >= 400:
        return inner
    return bridge.responses_stream_relay(inner, model, prefix="codearts")


# ---------------------------------------------------------------------------
# 模型 / 余额
# ---------------------------------------------------------------------------
async def list_models(request):
    """/v1/models —— 只返回能调的模型；无号时回落数据库目录。"""
    from admin.db import SessionLocal

    with SessionLocal() as db:
        shared.authenticate(db, request, platform=PUBLIC)
        realm = _realm(request)
        account = shared.select_account(
            db, storage=ca.PLATFORM, realm=realm, key=shared.key_row(db, request))
        if account is None:
            return JSONResponse({
                "object": "list",
                "data": shared.catalog_from_db(db, ca.PLATFORM),
                "note": "号池没有可用 CodeArts 账号，仅返回配置目录"})
        try:
            credential = ca.ensure_credential(db, account)
            models = client.fetch_models(credential,
                                         account_id=int(account.id or 0))
        except client.UpstreamError as exc:
            return _fail(exc)
    return JSONResponse({"object": "list", "data": models})


async def balance(request):
    """/v1/balance —— 真实积分余额（含分包与到期日）。"""
    from admin.db import SessionLocal

    with SessionLocal() as db:
        shared.authenticate(db, request, platform=PUBLIC)
        realm = _realm(request)
        account = shared.select_account(
            db, storage=ca.PLATFORM, realm=realm, key=shared.key_row(db, request))
        if account is None:
            return JSONResponse({"platform": PUBLIC, "realm": realm,
                                 "reachable": False, "balance": 0,
                                 "note": "号池没有可用账号"}, status_code=503)
        try:
            credential = ca.ensure_credential(db, account)
            quota = client.fetch_quota(credential,
                                       account_id=int(account.id or 0))
        except client.UpstreamError as exc:
            return _fail(exc)
    return JSONResponse({
        "platform": PUBLIC, "realm": realm, "reachable": True,
        "balance": quota.get("remain") or 0,
        "available": quota.get("remain") or 0,
        "total": quota.get("total") or 0,
        "used": quota.get("used") or 0,
        "expiring": quota.get("expiring") or 0,
        "unit": "credits",
        "package": quota.get("package_name") or "",
        "period": {"start": quota.get("start_date") or "",
                   "end": quota.get("end_date") or ""},
    })


_HANDLERS = {
    "/v1/chat/completions": chat_completions,
    "/v1/messages": messages,
    "/v1/messages/count_tokens": count_tokens,
    "/v1/responses": responses,
    "/v1/models": list_models,
    "/v1/balance": balance,
}


async def dispatch(request, path: str):
    """`PlatformDispatchMiddleware` 的唯一入口（**必须 async**）。"""
    handler = _HANDLERS.get(path)
    if handler is None:
        return JSONResponse(
            {"error": {"message": "CodeArts 不支持 %s" % path,
                       "type": "invalid_request_error"}},
            status_code=404)
    try:
        return await handler(request)
    except shared.PlatformRejected as exc:
        if path in ("/v1/messages", "/v1/messages/count_tokens"):
            return exc.anthropic()
        return exc.openai()
