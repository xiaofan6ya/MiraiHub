"""KroWork `/v1/*` 转发（供 `dispatch.py` 调用）。

## 三端协议各自怎么走

| 入口 | 目标模型 provider | 上游端点 | 是否转换 |
|---|---|---|---|
| `/v1/chat/completions` | openai-chat / dashscope | `{proxy_path}/chat/completions` | **不转**（原生 OpenAI） |
| `/v1/chat/completions` | anthropic | `/anthropic/v1/messages` | 请求→Anthropic、响应→OpenAI |
| `/v1/messages` | anthropic | `/anthropic/v1/messages` | **不转**（原生 Anthropic） |
| `/v1/messages` | 其它 | `{proxy_path}/chat/completions` | 双向转换 |
| `/v1/responses` | openai | `/openai/v1/responses` | **不转**（原生 Responses） |
| `/v1/responses` | 其它 | `{proxy_path}/chat/completions` | 双向转换 |

**优先走原生端点**：能省的转换一律不转 —— 转换层每多一次，思考块 /
工具参数增量 / 缓存字段就多一个静默丢失的机会。

## 三个必须处理的细节

1. **`x-model` 头与 `body.model` 必须是 backend id**（`ds4-pro`）。
   用户给 slot（`krow-model-1`）时先经模型表还原。
2. **流式逐帧 flush**：`StreamingResponse` 里不许攒帧，否则客户端
   「卡 30 秒后一次全出来」。
3. **usage 从终止帧取**：上游把 usage 放在最后一帧，边读边记最后一份非空。
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time

from starlette.responses import JSONResponse, StreamingResponse

from admin import http_shared
from admin.platform import protocol_bridge as bridge
from admin.platform import shared
from admin.platform.krowork.core import client, endpoints
from admin.platform.krowork.core import token as token_mod
from admin.platform.krowork.services import accounts as ka

logger = logging.getLogger("admin.platform.krowork.gateway")

PATHS = (
    "/v1/chat/completions",
    "/v1/messages",
    "/v1/messages/count_tokens",
    "/v1/responses",
    "/v1/models",
    "/v1/balance",
)

PUBLIC = ka.PLATFORM
STORAGE = ka.STORAGE
MAX_ATTEMPTS = 2

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
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
    """上游错误 → 客户端能看懂的错误。

    401 不能原样透出去：OpenAI 系客户端会把 401 一律渲染成「API Key 无效」，
    而实际是 KroWork 客户端登录态过期 —— 用户会去翻自己的 Key，方向全错。
    """
    logger.warning("KroWork 上游失败 kind=%s status=%s msg=%s",
                   getattr(exc, "kind", ""), getattr(exc, "status", ""),
                   str(getattr(exc, "message", ""))[:200])
    if exc.kind == "auth":
        return JSONResponse(
            {"error": {
                "type": "upstream_account",
                "code": "krowork_token_invalid",
                "message": ("KroWork 登录态已失效（与你的 API Key 无关）："
                            "请打开 KroWork 客户端保持登录，或在控制台"
                            "「KroWork → 账号池 → 从本机导入」重新取票。"
                            "上游原文：%s" % str(exc.message or "")[:200]),
                "upstream_account": True}},
            status_code=503)
    if exc.kind == "permission":
        return JSONResponse(
            {"error": {"type": "upstream_account",
                       "code": "krowork_permission_denied",
                       "message": "KroWork 这个账号没有该模型的权限（上游 403）：%s"
                                  % str(exc.message or "")[:200],
                       "upstream_account": True}},
            status_code=503)
    if exc.kind == "no_account":
        return JSONResponse(
            {"error": {"type": "server_error", "code": "no_account",
                       "message": exc.message}}, status_code=503)
    status = exc.status or 502
    if status < 400 or status > 599:
        status = 502
    return JSONResponse(
        {"error": {"type": exc.kind or "upstream_error",
                   "code": exc.code or "upstream_error",
                   "message": exc.message}},
        status_code=status)


class _Bill:
    """流式记账器：流走完（或断开）时写一条用量。"""

    def __init__(self, key, account_id: int, model: str, request, use_case: str):
        self.key = key
        self.account_id = account_id
        self.model = model
        self.request = request
        self.use_case = use_case
        self.started = time.perf_counter()
        #: 首个上游字节到达时刻（流式首字）。原来 ttfb 直接记成总耗时（N2 同类问题）。
        self.first_at: float | None = None
        self.usage: dict = {}
        self.done = False
        # 第 5 轮：读线程 finally 与「未启动」兜底线程可能并发 record()，判重加锁
        self._lock = threading.Lock()

    def mark_first(self) -> None:
        if self.first_at is None:
            self.first_at = time.perf_counter()

    def record(self, usage: dict | None = None) -> None:
        with self._lock:
            if self.done:
                return
            self.done = True
        usage = usage or self.usage or {}
        try:
            from admin.db import SessionLocal

            elapsed = int((time.perf_counter() - self.started) * 1000)
            ttfb = (max(1, int((self.first_at - self.started) * 1000))
                    if self.first_at is not None else None)
            with SessionLocal() as db:
                shared.record_usage(
                    db,
                    key_id=int(getattr(self.key, "id", 0) or 0),
                    account_id=int(self.account_id or 0),
                    model=self.model,
                    usage=usage if isinstance(usage, dict) else {},
                    public_platform=PUBLIC, storage=STORAGE,
                    client_ip=_client_ip(self.request),
                    use_case=self.use_case, seq=1,
                    ttfb_ms=ttfb, latency_ms=elapsed)
        except Exception:  # noqa: BLE001
            logger.exception("KroWork 记账失败")


def _bill_once(key, account, model, usage, *, request, started, use_case):
    try:
        from admin.db import SessionLocal

        elapsed = int((time.perf_counter() - started) * 1000) if started else None
        with SessionLocal() as db:
            shared.record_usage(
                db,
                key_id=int(getattr(key, "id", 0) or 0),
                account_id=int(getattr(account, "id", 0) or 0),
                model=model or "",
                usage=usage if isinstance(usage, dict) else {},
                public_platform=PUBLIC, storage=STORAGE,
                client_ip=_client_ip(request),
                use_case=use_case, seq=1,
                # 非流式没有「首字」：存 NULL（N2），不拿总耗时冒充
                ttfb_ms=None, latency_ms=elapsed)
    except Exception:  # noqa: BLE001
        logger.exception("KroWork 记账失败")


# ---------------------------------------------------------------------------
# 选号 + 取票
# ---------------------------------------------------------------------------
def _candidate(db, request, key, model: str, exclude: set[int], depth: int = 0):
    """返回 `(account, token)`；账号池为空时回落到进程级 token。"""
    if depth > 4:
        raise client.UpstreamError("没有可用账号（连续租约失败）",
                                   status=503, kind="no_account")
    account = shared.select_account(db, storage=STORAGE, realm=_realm(request),
                                    key=key, model=model, exclude_ids=exclude)
    if account is None:
        snap = token_mod.current()
        if snap.ok:
            return None, snap.id_token
        raise client.UpstreamError(
            "号池里没有可用的 KroWork 账号，本机也读不到登录态：%s"
            % (snap.detail or "KroWork 未登录"), status=503, kind="no_account")
    if not shared.lease(account):
        exclude.add(int(account.id))
        return _candidate(db, request, key, model, exclude, depth + 1)
    try:
        tok = ka.ensure_token(db, account)
    except Exception as exc:  # noqa: BLE001
        shared.release(account)
        exclude.add(int(account.id))
        logger.warning("KroWork 账号 %s 取票失败：%s", account.id, exc)
        return _candidate(db, request, key, model, exclude, depth + 1)
    return account, tok


def _prepare(body: dict, ref: client.ModelRef, *, protocol: str) -> dict:
    """把客户端 body 改写成上游要的形状。

    * `model` 一律改成 backend id；
    * OpenAI 系把 `max_tokens` 迁到 `max_completion_tokens`
      （部分 provider 只认后者）；Anthropic 保留 `max_tokens`（必填）；
    * 按模型 `max_output_tokens` 封顶；
    * 带上模型级 `extra_body`（thinking 开关等）。
    """
    out = dict(body)
    out["model"] = ref.id
    if protocol == "anthropic":
        if not out.get("max_tokens"):
            out["max_tokens"] = min(ref.max_output_tokens or 4096, 8192)
    else:
        if "max_tokens" in out and "max_completion_tokens" not in out:
            out["max_completion_tokens"] = out.pop("max_tokens")
        if ref.max_output_tokens:
            cap = int(ref.max_output_tokens)
            for key in ("max_completion_tokens", "max_output_tokens"):
                try:
                    if int(out.get(key) or 0) > cap:
                        out[key] = cap
                except (TypeError, ValueError):
                    out[key] = cap
    for key, value in (ref.extra_body or {}).items():
        if key == "max_tokens" and protocol != "anthropic":
            continue
        out.setdefault(key, value)
    return out


def _usage_of(payload) -> dict:
    if not isinstance(payload, dict):
        return {}
    usage = payload.get("usage")
    return usage if isinstance(usage, dict) else {}


# ---------------------------------------------------------------------------
# 非流式
# ---------------------------------------------------------------------------
def _call_once(db, request, key, body: dict, ref: client.ModelRef, protocol: str,
               exclude: set[int]):
    account, tok = _candidate(db, request, key, str(body.get("model") or ""), exclude)
    payload = _prepare(body, ref, protocol=protocol)
    started = time.perf_counter()
    try:
        result = client.call_json(ref.path, payload, ref=ref,
                                  realm=_realm(request), attempt_token=tok)
    except client.UpstreamError as exc:
        if account is not None:
            shared.release(account)
            if exc.kind in ("auth", "server", "rate_limit"):
                shared.note_failure(db, account, kind=exc.kind,
                                    detail=str(exc.message)[:200],
                                    status=exc.status, model=ref.id,
                                    public_platform=PUBLIC)
                db.commit()
                exclude.add(int(account.id))
        raise
    if account is not None:
        shared.note_success(account)
        shared.release(account)
        try:
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
    _bill_once(key, account, ref.id, _usage_of(result), request=request,
               started=started, use_case="krowork-%s" % protocol)
    return result


def _call(db, request, key, body: dict, ref: client.ModelRef, protocol: str) -> dict:
    exclude: set[int] = set()
    last: client.UpstreamError | None = None
    for _ in range(MAX_ATTEMPTS):
        try:
            return _call_once(db, request, key, body, ref, protocol, exclude)
        except client.UpstreamError as exc:
            last = exc
            if exc.kind in ("no_account", "permission", "quota"):
                break
    raise last or client.UpstreamError("上游不可用", status=502, kind="server")


# ---------------------------------------------------------------------------
# 流式中继
# ---------------------------------------------------------------------------
def _close_and_bill(resp, http_client, bill: "_Bill | None") -> None:
    """未启动兜底：关上游响应 / httpx client、记一条账（幂等：bill.record 判重）。"""
    try:
        resp.close()
    except Exception:  # noqa: BLE001
        pass
    try:
        http_client.close()
    except Exception:  # noqa: BLE001
        pass
    if bill is not None:
        bill.record()


def _chat_relay(resp, http_client, ref: client.ModelRef, bill: _Bill,
                model: str) -> StreamingResponse:
    """上游 OpenAI SSE → 客户端（透传帧，补 [DONE]，顺带抓 usage）。"""

    def gen():
        try:
            for raw in client.iter_sse(resp):
                bill.mark_first()
                line = raw.strip()
                if not line:
                    yield b"\n"
                    continue
                if line.startswith(b"data:"):
                    chunk = line[5:].strip()
                    if chunk == b"[DONE]":
                        continue
                    try:
                        obj = json.loads(chunk)
                    except ValueError:
                        yield raw
                        continue
                    usage = _usage_of(obj)
                    if usage:
                        bill.usage = usage
                    if model and not obj.get("model"):
                        obj["model"] = model
                    yield ("data: %s\n\n" % json.dumps(obj, ensure_ascii=False)).encode()
                    continue
                yield raw
            yield b"data: [DONE]\n\n"
        finally:
            try:
                resp.close()
            finally:
                try:
                    http_client.close()
                finally:
                    bill.record()

    # P4：异步桥 —— 整条流不再占 anyio 线程令牌；客户端断开后停止读上游并关闭
    # 第 5 轮（A6）：直连返回，响应头发送失败时 gen() 从未启动 → 自身钩子兜底收尾。
    return bridge.guarded_stream_response(
        http_shared.async_stream(gen()), lambda: _close_and_bill(resp, http_client, bill),
        name="krowork-unstarted", headers=_SSE_HEADERS)


def _native_relay(resp, http_client, bill: _Bill) -> StreamingResponse:
    """上游已是目标协议（Anthropic / Responses）：原样透传，只抓 usage。"""

    def gen():
        try:
            for raw in client.iter_sse(resp):
                bill.mark_first()
                line = raw.strip()
                if line.startswith(b"data:"):
                    try:
                        obj = json.loads(line[5:].strip())
                    except ValueError:
                        obj = None
                    if isinstance(obj, dict):
                        usage = _usage_of(obj)
                        if usage:
                            bill.usage = usage
                        if obj.get("type") == "message_delta":
                            usage = _usage_of(obj.get("delta"))
                            if usage:
                                bill.usage = usage
                yield raw
        finally:
            try:
                resp.close()
            finally:
                try:
                    http_client.close()
                finally:
                    bill.record()

    # P4：异步桥 —— 整条流不再占 anyio 线程令牌；客户端断开后停止读上游并关闭
    # 第 5 轮（A6）：同上，直连流的未启动兜底。
    return bridge.guarded_stream_response(
        http_shared.async_stream(gen()), lambda: _close_and_bill(resp, http_client, bill),
        name="krowork-unstarted", headers=_SSE_HEADERS)


def _bridge_inner(resp, http_client, bill: "_Bill | None" = None):
    """给 protocol_bridge 的内层流（它读 `.body_iterator`）。

    2026-11-24：原来这条路径（协议转换的流式请求）**从不记账** —— `_Bill` 建了却
    没人调 `record()`。现在边转发边抓 usage（OpenAI `usage` / Anthropic
    `message_delta.usage`），流结束时记一条。
    """

    def gen():
        try:
            for raw in client.iter_sse(resp):
                if bill is not None:
                    bill.mark_first()
                    line = raw.strip()
                    if line.startswith(b"data:"):
                        try:
                            obj = json.loads(line[5:].strip())
                        except ValueError:
                            obj = None
                        if isinstance(obj, dict):
                            usage = _usage_of(obj) or (
                                _usage_of(obj.get("delta"))
                                if obj.get("type") == "message_delta" else {})
                            if not usage and isinstance(obj.get("message"), dict):
                                usage = _usage_of(obj["message"])
                            if usage:
                                bill.usage = {**(bill.usage or {}), **usage}
                yield raw
        finally:
            try:
                resp.close()
            finally:
                try:
                    http_client.close()
                finally:
                    if bill is not None:
                        bill.record()

    def _release_unstarted():
        # 第 4 轮 R4-BE-05：中继一帧没读内层时 gen() 从未启动，上面的 finally 不执行，
        # 上游连接 / httpx client 要等 GC、这次请求不记账。
        try:
            resp.close()
        finally:
            try:
                http_client.close()
            finally:
                if bill is not None:
                    bill.record()

    return bridge.guard_unstarted_detached(
        StreamingResponse(http_shared.async_stream(gen()), media_type="text/event-stream"),
        _release_unstarted, "krowork-unstarted")


def _open_tracked(opened: list, *args, **kwargs):
    """`client.open_stream` + 记下打开的连接，供等待方被取消时 `_close_opened` 收尾。"""
    http_client, resp = client.open_stream(*args, **kwargs)
    opened.append((resp, http_client))
    return http_client, resp


def _close_opened(opened: list) -> None:
    """`_work` 跑完时请求已被取消（P1 放进线程后才会出现）：关掉它打开的上游流。"""
    for resp, http_client in opened:
        for obj in (resp, http_client):
            try:
                obj.close()
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def _unknown(model: str) -> JSONResponse:
    return JSONResponse({"error": {"message": "未知模型 %s" % model,
                                   "type": "invalid_request_error",
                                   "code": "model_not_found"}}, status_code=404)


def _restore_model(result, model: str):
    if isinstance(result, dict) and model:
        out = dict(result)
        out["model"] = model
        return out
    return result


async def chat_completions(request):
    body = await _body_of(request)
    model = str(body.get("model") or "")
    # 模型表缓存过期时 resolve 会同步拉上游（建连 15s / 读 600s），不能在事件循环上做
    ref = await asyncio.to_thread(client.resolve, model)
    if ref is None:
        return _unknown(model)
    if ref.is_responses:
        return JSONResponse(
            {"error": {"message": (
                "模型 %s 只提供 Responses 协议（provider=%s）：请把 base_url "
                "指向 /v1 并使用 /v1/responses，或换一个 chat 模型。"
                % (ref.id, ref.provider_type)),
                "type": "invalid_request_error", "code": "wrong_endpoint"}},
            status_code=400)

    from admin.db import SessionLocal

    # P1：鉴权 / 选号 / 取票 / 建连 / 非流式整段生成全是同步阻塞（同步 httpx），
    # 原来直接跑在事件循环上，一个 KroWork 请求就让全站冻结。整段放进线程，
    # 线程里只返回「响应构造器」，响应对象在事件循环上创建。
    opened: list = []

    def _work():
        with SessionLocal() as db:
            key = shared.authenticate(db, request, platform=PUBLIC)
            if ref.is_anthropic:
                converted = bridge.openai_request_to_anthropic(
                    _prepare(body, ref, protocol="anthropic"))
                if body.get("stream"):
                    account, tok = _candidate(db, request, key, model, set())
                    bill = _Bill(key, getattr(account, "id", 0), ref.id, request,
                                 "krowork-chat-stream")
                    if account is not None:
                        shared.release(account)
                    http_client, resp = _open_tracked(opened, 
                        ref.path, converted, ref=ref, realm=_realm(request),
                        attempt_token=tok)
                    inner = _bridge_inner(resp, http_client, bill)
                    return lambda: bridge.anthropic_stream_relay(inner, model, prefix="krowork")
                result = _call(db, request, key, converted, ref, "anthropic")
                return lambda: JSONResponse(bridge.anthropic_message_to_openai(result, model))

            if body.get("stream"):
                account, tok = _candidate(db, request, key, model, set())
                bill = _Bill(key, getattr(account, "id", 0), ref.id, request,
                             "krowork-chat-stream")
                if account is not None:
                    shared.release(account)
                payload = _prepare(body, ref, protocol="chat")
                http_client, resp = _open_tracked(opened, 
                    ref.path, payload, ref=ref, realm=_realm(request), attempt_token=tok)
                return lambda: _chat_relay(resp, http_client, ref, bill, model)

            result = _call(db, request, key, body, ref, "chat")
            return lambda: JSONResponse(_restore_model(result, model))

    build = await http_shared.run_blocking(
        _work, on_orphan=lambda _build: _close_opened(opened))
    return build()


async def messages(request):
    body = await _body_of(request)
    model = str(body.get("model") or "")
    # 模型表缓存过期时 resolve 会同步拉上游（建连 15s / 读 600s），不能在事件循环上做
    ref = await asyncio.to_thread(client.resolve, model)
    if ref is None:
        return _unknown(model)

    from admin.db import SessionLocal

    # P1：鉴权 / 选号 / 取票 / 建连 / 非流式整段生成全是同步阻塞（同步 httpx），
    # 原来直接跑在事件循环上，一个 KroWork 请求就让全站冻结。整段放进线程，
    # 线程里只返回「响应构造器」，响应对象在事件循环上创建。
    opened: list = []

    def _work():
        with SessionLocal() as db:
            key = shared.authenticate(db, request, platform=PUBLIC)
            if ref.is_anthropic:
                native = _prepare(body, ref, protocol="anthropic")
                if body.get("stream"):
                    account, tok = _candidate(db, request, key, model, set())
                    bill = _Bill(key, getattr(account, "id", 0), ref.id, request,
                                 "krowork-messages-stream")
                    if account is not None:
                        shared.release(account)
                    http_client, resp = _open_tracked(opened, 
                        ref.path, native, ref=ref, realm=_realm(request),
                        attempt_token=tok)
                    return lambda: _native_relay(resp, http_client, bill)
                result = _call(db, request, key, native, ref, "anthropic")
                return lambda: JSONResponse(_restore_model(result, model))

            normalized = bridge.anthropic_to_openai(body)
            if body.get("stream"):
                account, tok = _candidate(db, request, key, model, set())
                bill = _Bill(key, getattr(account, "id", 0), ref.id, request,
                             "krowork-messages-stream")
                if account is not None:
                    shared.release(account)
                payload = _prepare(normalized, ref, protocol="chat")
                http_client, resp = _open_tracked(opened, 
                    ref.path, payload, ref=ref, realm=_realm(request), attempt_token=tok)
                inner = _bridge_inner(resp, http_client, bill)
                return lambda: bridge.anthropic_stream_relay(inner, model, prefix="krowork")

            result = _call(db, request, key, normalized, ref, "anthropic")
            return lambda: JSONResponse(bridge.openai_to_anthropic(result, model))

    build = await http_shared.run_blocking(
        _work, on_orphan=lambda _build: _close_opened(opened))
    return build()


async def count_tokens(request):
    """本地估算（上游没有 count_tokens 端点，实测 `/anthropic/v1/messages/count_tokens` 404）。"""
    body = await _body_of(request)
    parts: list[str] = []
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
    body = await _body_of(request)
    model = str(body.get("model") or "")
    # 模型表缓存过期时 resolve 会同步拉上游（建连 15s / 读 600s），不能在事件循环上做
    ref = await asyncio.to_thread(client.resolve, model)
    if ref is None:
        return _unknown(model)

    from admin.db import SessionLocal

    # P1：鉴权 / 选号 / 取票 / 建连 / 非流式整段生成全是同步阻塞（同步 httpx），
    # 原来直接跑在事件循环上，一个 KroWork 请求就让全站冻结。整段放进线程，
    # 线程里只返回「响应构造器」，响应对象在事件循环上创建。
    opened: list = []

    def _work():
        with SessionLocal() as db:
            key = shared.authenticate(db, request, platform=PUBLIC)
            if ref.is_responses:
                native = _prepare(body, ref, protocol="responses")
                if body.get("stream"):
                    account, tok = _candidate(db, request, key, model, set())
                    bill = _Bill(key, getattr(account, "id", 0), ref.id, request,
                                 "krowork-responses-stream")
                    if account is not None:
                        shared.release(account)
                    http_client, resp = _open_tracked(opened, 
                        ref.path, native, ref=ref, realm=_realm(request),
                        attempt_token=tok)
                    return lambda: _native_relay(resp, http_client, bill)
                result = _call(db, request, key, native, ref, "responses")
                return lambda: JSONResponse(_restore_model(result, model))

            normalized = bridge.responses_to_openai(body)
            if body.get("stream"):
                account, tok = _candidate(db, request, key, model, set())
                bill = _Bill(key, getattr(account, "id", 0), ref.id, request,
                             "krowork-responses-stream")
                if account is not None:
                    shared.release(account)
                payload = _prepare(normalized, ref, protocol="chat")
                http_client, resp = _open_tracked(opened, 
                    ref.path, payload, ref=ref, realm=_realm(request), attempt_token=tok)
                inner = _bridge_inner(resp, http_client, bill)
                return lambda: bridge.responses_stream_relay(inner, model, prefix="krowork")

            result = _call(db, request, key, normalized, ref, "chat")
            return lambda: JSONResponse(bridge.openai_to_responses(result, model))

    build = await http_shared.run_blocking(
        _work, on_orphan=lambda _build: _close_opened(opened))
    return build()


# ---------------------------------------------------------------------------
# 目录 / 余额
# ---------------------------------------------------------------------------
async def list_models(request):
    """只返回**当前凭证真能调**的模型（槽位名优先）。

    ⚠️ 凭证要从**号池**取（授权登录入库的号不在 sidecar 里）。
    号池有票 → 顺手 refresh 一次模型表；号池无票 → 用落盘缓存，绝不返回空表。
    """
    from admin.db import SessionLocal

    def _work():
        tok = ""
        with SessionLocal() as db:
            shared.authenticate(db, request, platform=PUBLIC)
            realm = _realm(request)
            rows = ka.accounts_for(db, realm) or ka.accounts_for(db)
            for row in rows:
                try:
                    tok = ka.ensure_token(db, row)
                    break
                except Exception:  # noqa: BLE001
                    continue
            if not tok:
                tok = token_mod.current().id_token
            model_map = client.fetch_model_map()
            data = []
            for ref in model_map.catalog():
                item = ref.public()
                item["platform"] = PUBLIC
                data.append(item)
            if not data:
                data = shared.catalog_from_db(db, STORAGE)
        return tok, model_map, data

    # P1：取票（可能续签）+ 拉模型表都是同步网络调用，放进线程
    tok, model_map, data = await asyncio.to_thread(_work)
    return JSONResponse({"object": "list", "data": data,
                         "region": model_map.region,
                         "model_map_version": model_map.version,
                         "has_token": bool(tok),
                         "note": "" if tok else "号池没有可用凭证，目录来自落盘缓存"})


async def balance(request):
    """/v1/balance —— 上游积分（`/krow/commerce/credit/balance`，只读 GET）+ 本地账本。

    ⚠️ 这里**必须从号池取票**（`ka.ensure_token`），不能只看进程级 token ——
    授权登录入库的号不经过 sidecar，用 `token_mod.current()` 会拿空票。
    """
    from admin.db import SessionLocal

    def _work():
        tok = ""
        with SessionLocal() as db:
            shared.authenticate(db, request, platform=PUBLIC)
            realm = _realm(request)
            summary = ka.summarize(db, realm)
            stats = ka.usage_stats(db, days=30, limit=0)
            rows = ka.accounts_for(db, realm) or ka.accounts_for(db)
            for row in rows:
                try:
                    tok = ka.ensure_token(db, row)
                    break
                except Exception:  # noqa: BLE001 - 这个号取不到票就试下一个
                    continue
        if not tok:
            tok = token_mod.current().id_token
        credits = client.fetch_credits(realm, tok=tok, force=True)
        return tok, summary, stats, credits

    # P1：DB 汇总 + 取票 + 查上游积分都是同步调用，放进线程
    tok, summary, stats, credits = await asyncio.to_thread(_work)
    remain = float(credits.get("remain_credits") or 0)
    total = float(credits.get("total_credits") or 0)
    items = credits.get("items") or []
    return JSONResponse({
        "platform": PUBLIC,
        "reachable": bool(tok),
        "balance": remain,
        "available": remain,
        "total": total or remain,
        "supported": bool(credits.get("ok")),
        "note": ("积分来源 %s" % credits.get("path")) if credits.get("ok")
                else ("读不到上游积分接口：%s（用量以本地账本为准）"
                      % (credits.get("detail") or "")[:160]),
        "credits": items,
        "plan": credits.get("plan") or {},
        "accounts": summary,
        "usage": {"total": stats.get("total", 0),
                  "total_tokens": stats.get("total_tokens", 0)},
    })


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
        return JSONResponse({"error": {"message": exc.message, "type": exc.kind}},
                            status_code=getattr(exc, "status", 400))
    except client.UpstreamError as exc:
        return _fail(exc)
    return JSONResponse({"error": {"message": "not found"}}, status_code=404)
