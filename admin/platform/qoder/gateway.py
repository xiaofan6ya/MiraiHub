"""Qoder `/v1/*` 转发实现（供 `dispatch.py` 调用）。

## 协议

上游是 OpenAI 兼容（模型目录里 10 个 chat 模型的 `format` 全是 `openai`），
所以 `chat_completions` 近乎直通；`messages`（Anthropic）与 `responses`
需要事件名映射 —— 都已在本文件实现。

## 鉴权 / 选号 / 记账（本次修复）

改造前这里**没有 Key 校验、没有账号绑定、不写用量日志、不占在途租约**：
任何人拿任意 Key（甚至不带 Key）都能打 Qoder，用量日志里一条都看不到，
并发时所有请求只砸第一个账号。现在统一走 `admin.platform.shared`：

    authenticate()      鉴权 + 配额 + 哨兵暂停
    select_account()    平台 + 区域 + Key 归属 + 冷却 + 在途
    record_usage()      写带 platform 的 usage_logs
    note_failure()      账号状态回写 + 哨兵

## 区域怎么定

`dispatch` 把请求路由到本模块时，区域来自 `PlatformSpec.realm`
（`qoder` → cn，`qoder-intl` → intl）。本模块从 `request.state` 再确认一次，
防止跨区串号（国际版 token 打到国内网关会 403 Login expired）。
"""
from __future__ import annotations

import json
import logging
import time

from starlette.responses import JSONResponse, StreamingResponse

from admin.platform import shared
from admin.platform.qoder.core import client
from admin.platform.qoder.services import accounts as qoder_accounts
from admin.platform.qoder.services import chat as qoder_chat

logger = logging.getLogger("admin.platform.qoder.gateway")

#: 拦截的路径（`dispatch` 据此判断要不要转交）
PATHS = (
    "/v1/chat/completions",
    "/v1/messages",
    "/v1/messages/count_tokens",
    "/v1/responses",
    "/v1/models",
    "/v1/balance",
)

#: 对外平台 id（落库值见 `qoder_accounts.PLATFORM`）
PUBLIC = {"cn": "qoder", "intl": "qoder-intl"}

#: 单次请求最多换几个号
MAX_ATTEMPTS = 3

#: 模型目录缓存（上游目录接口 0.4~30s 不等，每次请求都拉会拖慢首字）
_CATALOG_TTL = 180
_catalog_cache: dict[tuple[str, int], tuple[float, dict]] = {}


def _realm(request) -> str:
    storage, realm, _spec = shared.locate(request, "qoder")
    return realm or "cn"


def _public(request) -> str:
    storage, realm, spec = shared.locate(request, "qoder")
    return shared.public_platform_of(request, PUBLIC.get(realm or "cn", "qoder"))


def _auth(db, request):
    return shared.authenticate(db, request, platform=_public(request))


def _json_body(body: dict, status: int = 400, kind: str = "bad_request") -> tuple:
    return body, status, kind


def _catalog_for(db, account, data: dict, realm: str):
    """取模型目录：优先缓存，失败回落数据库。

    上游目录接口偶发几十秒不返回；没有缓存时每个请求都要等它，
    表现就是「Qoder 有时候要等很久 / 超时」。这里缓存 3 分钟，
    并在失败时用 `model_configs` 兜底（至少客户端能选到模型）。
    """
    cache_key = (realm, int(getattr(account, "id", 0) or 0))
    cached = _catalog_cache.get(cache_key)
    now = time.time()
    if cached and now - cached[0] < _CATALOG_TTL:
        return cached[1]
    try:
        catalog = client.fetch_model_catalog(
            qoder_accounts.token_of(data), realm,
            uid=str(data.get("uid") or ""),
            name=str(data.get("name") or ""),
            email=str(data.get("email") or ""),
            machine_id=qoder_accounts.machine_id_of(data))
        _catalog_cache[cache_key] = (now, catalog)
        if len(_catalog_cache) > 32:
            oldest = min(_catalog_cache, key=lambda k: _catalog_cache[k][0])
            _catalog_cache.pop(oldest, None)
        return catalog
    except Exception as exc:  # noqa: BLE001
        logger.info("Qoder 模型目录拉取失败 realm=%s：%s", realm, exc)
        if cached:
            return cached[1]
        return {"chat": _db_catalog(db), "_fallback": True}


def _db_catalog(db) -> list[dict]:
    """数据库兜底目录（形状按 qoder_chat.pick_model 的期望）。"""
    rows = shared.catalog_from_db(db, qoder_accounts.PLATFORM)
    return [{
        "key": row["id"], "display_name": row["name"], "format": "openai",
        "enable": True, "is_reasoning": False, "is_vl": False,
    } for row in rows]


def _no_account_response(realm: str) -> JSONResponse:
    return JSONResponse(
        {"error": {"type": "no_account", "code": "no_available_account",
                   "message": "Qoder %s 没有可用账号"
                              % ("国际版" if realm == "intl" else "中国版")}},
        status_code=503)


def _fail(exc: client.UpstreamError) -> JSONResponse:
    status = exc.status or 502
    if status < 400 or status > 599:
        status = 502
    return JSONResponse(
        {"error": {"type": exc.kind, "code": exc.code or "upstream_error",
                   "message": exc.message, "detail": exc.detail[:400]}},
        status_code=status)


async def _body_of(request) -> dict:
    """读 JSON 请求体。

    `GET /v1/models` **没有 body**：直接 `request.json()` 会等一个永远不会
    到来的 body（实测卡 60s）。所以无 body 的方法直接返回空字典。
    """
    if request.scope.get("method", "GET").upper() in ("GET", "HEAD", "OPTIONS"):
        return {}
    body = request.scope.get("_wb_body")
    if isinstance(body, bytes) and body:
        try:
            data = json.loads(body)
            return data if isinstance(data, dict) else {}
        except (ValueError, UnicodeDecodeError):
            return {}
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001 - 空 body / 非 JSON 都当空字典
        return {}
    return data if isinstance(data, dict) else {}


def _client_ip(request) -> str:
    try:
        from admin.client_ip import request_ip

        return request_ip(request)
    except Exception:  # noqa: BLE001
        return ""


def _cool_seconds(kind: str) -> int:
    return {"auth": 600, "quota_exceeded": 1800,
            "signature_invalid": 300, "upstream_business": 120}.get(kind, 0)


def _attempt(db, request, key, body: dict, *, realm: str, model_hint: str = ""):
    """选号 + 租约 + 目录 + 组包。返回 `(plan, account)`。"""
    exclude: set[int] = set()
    realm = realm or "cn"
    last_error = None
    for _ in range(MAX_ATTEMPTS):
        account = shared.select_account(
            db, storage=qoder_accounts.PLATFORM, realm=realm,
            key=key, model=model_hint, exclude_ids=exclude)
        if account is None:
            break
        if not shared.lease(account):
            exclude.add(account.id)
            continue
        try:
            data = qoder_accounts.ensure_token(db, account)
            catalog = _catalog_for(db, account, data, realm)
            plan = qoder_chat.prepare(db, account, body, catalog=catalog)
            return plan, account
        except client.UpstreamError as exc:
            shared.release(account)
            shared.note_failure(
                db, account, kind=exc.kind, detail=exc.detail or exc.message,
                status=exc.status, model=model_hint, public_platform=_public(request),
                cool_seconds=_cool_seconds(exc.kind))
            last_error = exc
            exclude.add(account.id)
            if not exc.retryable():
                break
        except Exception as exc:  # noqa: BLE001
            shared.release(account)
            logger.exception("Qoder 准备请求失败 acc=%s", account.id)
            last_error = client.UpstreamError(str(exc), kind="network")
            exclude.add(account.id)
            continue
    if last_error is not None:
        raise last_error
    raise shared.PlatformRejected(
        503, "Qoder 没有可用账号（请先在控制台导入并确认状态为启用）",
        kind="server_error", code="no_available_account",
        headers={"Retry-After": "3"})


def _elapsed_ms(started) -> int:
    """从 perf_counter 起点算到现在的毫秒数。"""
    import time as _t

    try:
        return int((_t.perf_counter() - started) * 1000)
    except Exception:  # noqa: BLE001
        return 0


def _bill(key, account, model, usage, *, request, ttfb_ms=0, latency_ms=0,
          started=None, seq=1, error=""):
    """独立会话记账，绝不影响已经返回的响应。"""
    try:
        from admin.db import SessionLocal

        with SessionLocal() as db:
            # latency 必须单独传：之前 `latency_ms=ttfb_ms` 把总耗时写成了
            # 首包时间，于是面板上「P50 总耗时 == TTFT」，TPS 永远算不出来
            # （生成耗时 = latency - ttfb = 0）。没给 latency 时退回 ttfb，
            # 至少不要是 0。
            shared.record_usage(
                db,
                key_id=int(getattr(key, "id", 0) or 0),
                account_id=int(getattr(account, "id", 0) or 0),
                model=model, usage=usage, public_platform=_public(request),
                storage=qoder_accounts.PLATFORM, error_kind=error,
                client_ip=_client_ip(request),
                use_case="qoder-" + (request.url.path.rsplit("/", 1)[-1] or "chat"),
                seq=seq, ttfb_ms=ttfb_ms,
                latency_ms=latency_ms or _elapsed_ms(started) or ttfb_ms)
    except Exception:  # noqa: BLE001
        logger.exception("Qoder 记账失败")


# ---------------------------------------------------------------------------
# /v1/models
# ---------------------------------------------------------------------------
async def list_models(request) -> JSONResponse:
    from admin.db import SessionLocal

    with SessionLocal() as db:
        _auth(db, request)
        realm = _realm(request)
        scene = "chat"
        account = None
        try:
            account = shared.select_account(
                db, storage=qoder_accounts.PLATFORM, realm=realm,
                key=shared.key_row(db, request))
        except Exception:  # noqa: BLE001
            account = None
        if account is None:
            # 没有号也返回目录：客户端至少能选模型，调用时再报「无可用账号」。
            entries = [{
                "id": row["id"], "object": "model", "created": 0,
                "owned_by": "qoder", "name": row["name"],
            } for row in _db_catalog(db)]
            return JSONResponse({"object": "list", "data": entries,
                                 "note": "号池没有可用 Qoder 账号，仅返回配置目录"})
        try:
            data = qoder_accounts.ensure_token(db, account)
            catalog = _catalog_for(db, account, data, realm)
        except client.UpstreamError as exc:
            return _fail(exc)

    return JSONResponse({"object": "list",
                         "data": qoder_chat.model_list(catalog, scene)})


# ---------------------------------------------------------------------------
# /v1/balance
# ---------------------------------------------------------------------------
async def balance(request):
    from admin.db import SessionLocal

    with SessionLocal() as db:
        key = _auth(db, request)
        realm = _realm(request)
        account = shared.select_account(
            db, storage=qoder_accounts.PLATFORM, realm=realm,
            key=shared.key_row(db, request))
        if account is None:
            return _no_account_response(realm)
        try:
            data = qoder_accounts.ensure_token(db, account)
            usage = client.fetch_usage(qoder_accounts.token_of(data), realm)
        except client.UpstreamError as exc:
            return _fail(exc)

    user_quota = usage.get("userQuota") or {}
    addon_quota = usage.get("addOnQuota") or {}
    return JSONResponse({
        "platform": "qoder",
        "realm": realm,
        "account": account.name,
        "plan": usage.get("userType"),
        "unit": "credits",
        "user_quota": user_quota,
        "addon_quota": addon_quota,
        "total": float(user_quota.get("total") or 0) + float(addon_quota.get("total") or 0),
        "remaining": float(user_quota.get("remaining") or 0) + float(
            addon_quota.get("remaining") or 0),
        "expires_at": usage.get("expiresAt"),
        "key_credit_used": float(getattr(key, "credit_used", 0) or 0),
    })


# ---------------------------------------------------------------------------
# /v1/chat/completions
# ---------------------------------------------------------------------------
async def chat_completions(request):
    from admin.db import SessionLocal

    body = await _body_of(request)
    realm = _realm(request)
    stream = bool(body.get("stream"))
    # **默认就要 usage**：不取 usage 的话 `_bill` 拿不到 token 数，
    # 用量日志和积分统计都会是空的。
    # 客户端没显式关掉就一律要 —— 多一个尾部帧，不改变正文流。
    _so = body.get("stream_options") if isinstance(body.get("stream_options"), dict) else {}
    want_usage = _so.get("include_usage", True) is not False

    db = SessionLocal()
    try:
        key = _auth(db, request)
        try:
            plan, account = _attempt(db, request, key, body, realm=realm,
                                     model_hint=str(body.get("model") or ""))
        except shared.PlatformRejected:
            raise
        except client.UpstreamError as exc:
            return _fail(exc)

        if not stream:
            started = time.perf_counter()
            try:
                result = qoder_chat.collect_openai(plan)
            except client.UpstreamError as exc:
                shared.release(account)
                shared.note_failure(
                    db, account, kind=exc.kind, detail=exc.detail or exc.message,
                    status=exc.status, model=plan.get("model_name", ""),
                    public_platform=_public(request),
                    cool_seconds=_cool_seconds(exc.kind))
                _bill(key, account, plan.get("model_name", ""), {},
                      request=request, error=exc.kind)
                return _fail(exc)
            shared.release(account)
            shared.note_success(account)
            try:
                db.commit()
            except Exception:  # noqa: BLE001
                db.rollback()
            _bill(key, account, result.get("model") or plan.get("model_name", ""),
                  result.get("usage") or {}, request=request,
                  ttfb_ms=int((time.perf_counter() - started) * 1000),
                  started=started)
            return JSONResponse(result)

        # 流式分支也要计时起点：`generator()` 的 finally 里 `_bill` 要用。
        # 之前只在 `if not stream` 分支定义，流式请求走到闭包里就抛
        # `NameError: free variable 'started' referenced before assignment`
        # （客户端看到 400「请求失败」，日志里连请求都没记上）。
        started = time.perf_counter()
        usage_holder: dict = {}

        def generator():
            try:
                yield from qoder_chat.stream_openai(plan, want_usage=want_usage,
                                                    holder=usage_holder)
            except client.UpstreamError as exc:
                yield _sse_error(exc)
            finally:
                shared.release(account)
                _bill(key, account, plan.get("model_name", ""),
                      usage_holder.get("usage") or {}, request=request,
                      started=started)

        return StreamingResponse(
            generator(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    finally:
        db.close()


def _sse_error(exc: client.UpstreamError) -> bytes:
    return ("data: %s\n\n" % json.dumps(
        {"error": {"message": exc.message, "type": exc.kind,
                   "code": exc.code}}, ensure_ascii=False)).encode()


# ---------------------------------------------------------------------------
# /v1/messages（Anthropic）
# ---------------------------------------------------------------------------
def _anthropic_event(name: str, data: dict) -> bytes:
    return ("event: %s\ndata: %s\n\n"
            % (name, json.dumps(data, ensure_ascii=False))).encode()


def _stop_reason_of(finish: str | None) -> str:
    return {"length": "max_tokens", "tool_calls": "tool_use",
            "stop": "end_turn"}.get(str(finish or ""), "end_turn")


def _anthropic_usage_of(usage: dict) -> dict:
    """OpenAI usage → Anthropic usage（含缓存读取/写入两个单列字段）。

    少了 `cache_read_input_tokens`，Claude Code 这类客户端的「缓存命中」
    就恒显示 0，而网关侧账目是对的 —— 必须整份透出。
    """
    try:
        from upstream_compat import anthropic_usage_from

        out = anthropic_usage_from(usage)
        if out is not None:
            return out
    except Exception:  # noqa: BLE001
        pass
    return {"input_tokens": int((usage or {}).get("prompt_tokens") or 0),
            "output_tokens": int((usage or {}).get("completion_tokens") or 0),
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0}


async def messages(request):
    # 计时起点：`_bill` 依赖它算总耗时，缺了会抛
    # `name 'started' is not defined` → 客户端看到 502。
    started = time.perf_counter()
    from admin.db import SessionLocal

    body = await _body_of(request)
    realm = _realm(request)
    normalized = dict(body)
    system = body.get("system")
    if system:
        prefix = [{"role": "system", "content": system}]
        normalized["messages"] = prefix + list(body.get("messages") or [])

    db = SessionLocal()
    try:
        key = _auth(db, request)
        try:
            plan, account = _attempt(db, request, key, normalized, realm=realm,
                                     model_hint=str(body.get("model") or ""))
        except shared.PlatformRejected:
            raise
        except client.UpstreamError as exc:
            return _fail(exc)

        if not body.get("stream"):
            try:
                result = qoder_chat.collect_openai(plan)
            except client.UpstreamError as exc:
                shared.release(account)
                shared.note_failure(
                    db, account, kind=exc.kind, detail=exc.detail or exc.message,
                    status=exc.status, model=plan.get("model_name", ""),
                    public_platform=_public(request),
                    cool_seconds=_cool_seconds(exc.kind))
                return _fail(exc)
            shared.release(account)
            shared.note_success(account)
            try:
                db.commit()
            except Exception:  # noqa: BLE001
                db.rollback()
            _bill(key, account, result.get("model") or plan.get("model_name", ""),
                  result.get("usage") or {}, request=request, started=started)
            text = result["choices"][0]["message"].get("content") or ""
            return JSONResponse({
                "id": "msg_" + result["id"].split("-", 1)[-1],
                "type": "message",
                "role": "assistant",
                "model": result["model"],
                "content": [{"type": "text", "text": text}],
                "stop_reason": _stop_reason_of(
                    result["choices"][0].get("finish_reason")),
                "usage": {
                    "input_tokens": (result.get("usage") or {}).get("prompt_tokens", 0),
                    "output_tokens": (result.get("usage") or {}).get("completion_tokens", 0),
                },
            })

        usage_holder: dict = {}

        def generator():
            try:
                yield from _stream_anthropic(plan, usage_holder)
            finally:
                shared.release(account)
                _bill(key, account, plan.get("model_name", ""),
                      usage_holder.get("usage") or {}, request=request,
                      started=started)

        return StreamingResponse(
            generator(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    finally:
        db.close()


def _stream_anthropic(plan: dict, holder: dict | None = None):
    import uuid

    holder = holder if isinstance(holder, dict) else {}
    msg_id = "msg_" + uuid.uuid4().hex[:24]
    model_name = plan["model_name"]
    yield _anthropic_event("message_start", {
        "type": "message_start",
        "message": {"id": msg_id, "type": "message", "role": "assistant",
                    "model": model_name,
                    "content": [], "stop_reason": None,
                    "usage": {"input_tokens": 0, "output_tokens": 0}},
    })
    yield _anthropic_event("content_block_start", {
        "type": "content_block_start", "index": 0,
        "content_block": {"type": "text", "text": ""}})

    usage: dict = {}
    try:
        for inner in client.iter_chat_stream(
                plan["token"], plan["realm"], uid=plan["uid"],
                body=plan["upstream"], name=plan["name"],
                email=plan["email"], machine_id=plan["machine_id"],
                account_id=plan.get("account_id", 0)):
            choices = inner.get("choices") or []
            if choices:
                delta = (choices[0] or {}).get("delta") or {}
                if delta.get("content"):
                    yield _anthropic_event("content_block_delta", {
                        "type": "content_block_delta", "index": 0,
                        "delta": {"type": "text_delta",
                                  "text": str(delta["content"])}})
                if delta.get("reasoning_content"):
                    yield _anthropic_event("content_block_delta", {
                        "type": "content_block_delta", "index": 0,
                        "delta": {"type": "thinking_delta",
                                  "thinking": str(delta["reasoning_content"])}})
                finish = (choices[0] or {}).get("finish_reason")
                if finish:
                    yield _anthropic_event("content_block_stop", {
                        "type": "content_block_stop", "index": 0})
                    yield _anthropic_event("message_delta", {
                        "type": "message_delta",
                        "delta": {"stop_reason": _stop_reason_of(finish)},
                        "usage": _anthropic_usage_of(usage)})
            frame_usage = inner.get("usage")
            if isinstance(frame_usage, dict) and frame_usage:
                usage = frame_usage
    except client.UpstreamError as exc:
        yield _anthropic_event("error", {
            "type": "error",
            "error": {"type": exc.kind, "message": exc.message}})
        return
    finally:
        holder["usage"] = usage
    yield _anthropic_event("message_stop", {"type": "message_stop"})


# ---------------------------------------------------------------------------
# /v1/responses
# ---------------------------------------------------------------------------
async def responses(request):
    # 同上
    started = time.perf_counter()
    body = await _body_of(request)
    realm = _realm(request)
    normalized = dict(body)
    raw_input = body.get("input")
    if isinstance(raw_input, str):
        normalized["messages"] = [{"role": "user", "content": raw_input}]
    elif isinstance(raw_input, list):
        normalized["messages"] = raw_input
    normalized.setdefault("max_tokens", body.get("max_output_tokens"))

    from admin.db import SessionLocal

    db = SessionLocal()
    try:
        key = _auth(db, request)
        try:
            plan, account = _attempt(db, request, key, normalized, realm=realm,
                                     model_hint=str(body.get("model") or ""))
        except shared.PlatformRejected:
            raise
        except client.UpstreamError as exc:
            return _fail(exc)

        if not body.get("stream"):
            try:
                result = qoder_chat.collect_openai(plan)
            except client.UpstreamError as exc:
                shared.release(account)
                shared.note_failure(
                    db, account, kind=exc.kind, detail=exc.detail or exc.message,
                    status=exc.status, model=plan.get("model_name", ""),
                    public_platform=_public(request),
                    cool_seconds=_cool_seconds(exc.kind))
                return _fail(exc)
            shared.release(account)
            shared.note_success(account)
            try:
                db.commit()
            except Exception:  # noqa: BLE001
                db.rollback()
            _bill(key, account, result.get("model") or plan.get("model_name", ""),
                  result.get("usage") or {}, request=request, started=started)
            text = result["choices"][0]["message"].get("content") or ""
            return JSONResponse({
                "id": "resp_" + result["id"].split("-", 1)[-1],
                "object": "response",
                "model": result["model"],
                "output": [{"type": "message", "role": "assistant",
                            "content": [{"type": "output_text", "text": text}]}],
                "usage": result.get("usage"),
            })

        usage_holder: dict = {}

        def generator():
            try:
                yield from _stream_responses(plan, usage_holder)
            finally:
                shared.release(account)
                _bill(key, account, plan.get("model_name", ""),
                      usage_holder.get("usage") or {}, request=request,
                      started=started)

        return StreamingResponse(
            generator(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    finally:
        db.close()


def _stream_responses(plan: dict, holder: dict | None = None):
    import uuid

    holder = holder if isinstance(holder, dict) else {}
    resp_id = "resp_" + uuid.uuid4().hex[:24]
    model_name = plan["model_name"]

    def event(name: str, data: dict) -> bytes:
        return ("event: %s\ndata: %s\n\n"
                % (name, json.dumps(data, ensure_ascii=False))).encode()

    yield event("response.created", {
        "type": "response.created",
        "response": {"id": resp_id, "object": "response", "model": model_name,
                     "status": "in_progress", "output": []}})

    usage: dict = {}
    finish = "stop"
    try:
        for inner in client.iter_chat_stream(
                plan["token"], plan["realm"], uid=plan["uid"],
                body=plan["upstream"], name=plan["name"],
                email=plan["email"], machine_id=plan["machine_id"],
                account_id=plan.get("account_id", 0)):
            choices = inner.get("choices") or []
            if choices:
                choice = choices[0] or {}
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    yield event("response.output_text.delta", {
                        "type": "response.output_text.delta",
                        "item_id": resp_id, "output_index": 0,
                        "content_index": 0, "delta": str(delta["content"])})
                if delta.get("reasoning_content"):
                    yield event("response.reasoning_summary_text.delta", {
                        "type": "response.reasoning_summary_text.delta",
                        "item_id": resp_id, "output_index": 0,
                        "summary_index": 0,
                        "delta": str(delta["reasoning_content"])})
                if choice.get("finish_reason"):
                    finish = str(choice["finish_reason"])
            frame_usage = inner.get("usage")
            if isinstance(frame_usage, dict) and frame_usage:
                usage = frame_usage
    except client.UpstreamError as exc:
        yield event("response.failed", {
            "type": "response.failed",
            "response": {"id": resp_id, "status": "failed",
                         "error": {"code": exc.kind, "message": exc.message}}})
        return
    finally:
        holder["usage"] = usage

    yield event("response.completed", {
        "type": "response.completed",
        "response": {"id": resp_id, "object": "response", "model": model_name,
                     "status": "completed",
                     "incomplete_details": None if finish == "stop" else {"reason": finish},
                     "usage": {
                         "input_tokens": int(usage.get("prompt_tokens") or 0),
                         "output_tokens": int(usage.get("completion_tokens") or 0),
                         "total_tokens": int(usage.get("total_tokens") or 0)}}})


async def count_tokens(request):
    """`/v1/messages/count_tokens` —— 本地估算（上游没有这个接口）。"""
    body = await _body_of(request)
    text_parts: list[str] = []
    system = body.get("system")
    if isinstance(system, str):
        text_parts.append(system)
    for message in body.get("messages") or []:
        if isinstance(message, dict):
            content = message.get("content")
            text_parts.append(str(content or "")
                              if not isinstance(content, list)
                              else " ".join(str(b.get("text") or "")
                                            for b in content
                                            if isinstance(b, dict)))
    text = "\n".join(text_parts)
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = max(0, len(text) - cjk)
    return JSONResponse({"input_tokens": cjk + max(1, other // 4)})


# ---------------------------------------------------------------------------
# dispatch 协议：单一入口
# ---------------------------------------------------------------------------
async def dispatch(request, path: str):
    """`PlatformDispatchMiddleware` 的唯一入口（**必须 async**）。"""
    handler = _HANDLERS.get(path)
    if handler is None:
        return JSONResponse({"error": {"message": "Qoder 不支持的路径：%s" % path,
                                       "type": "invalid_request_error"}},
                            status_code=404)
    try:
        return await handler(request)
    except shared.PlatformRejected as exc:
        if path in ("/v1/messages", "/v1/messages/count_tokens"):
            return exc.anthropic()
        return exc.openai()


_HANDLERS = {
    "/v1/chat/completions": chat_completions,
    "/v1/messages": messages,
    "/v1/messages/count_tokens": count_tokens,
    "/v1/responses": responses,
    "/v1/models": list_models,
    "/v1/balance": balance,
}
