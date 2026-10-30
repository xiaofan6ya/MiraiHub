"""Trae `/v1/*` 分发入口。

多端点协议转换是这次接入**必须复刻**的能力，也是 trae 目录存在的理由：

    /v1/chat/completions        OpenAI Chat Completions（流式 / 非流式）
    /v1/responses               OpenAI Responses（Codex / Claude Code）
    /v1/messages                Anthropic Messages（Claude Code / SDK）
    /v1/messages/count_tokens   Anthropic token 计数
    /v1/models                  模型清单
    /v1/balance                 额度

上游只有一种协议（`llm_raw_chat` 的 SSE 信封流），四套客户端协议在
`core/protocol.py` 里双向转换，因此任意端点之间不需要两两实现。

**复用系统能力的部分**（不重复实现）：
    Key 校验与配额   admin.security.get_key_row / check_quota
    号池与选号       admin.pool + services/pool.select
    在途租约/粘性    admin.pool.POOL
    用量与积分       usage_logs + api_keys.credit_used
    请求指标         admin.request_metrics

**首片段交接**：`_run()` 会消费渲染器的第一个片段，用来判断「这次尝试到底
成不成功」——上游的鉴权/风控/额度错误都是在**流内部**以 `event: error` 出现
的，不先读一帧就无法区分「成功」与「HTTP 200 + 业务错误」。因此响应对象
留在 holder 里，交给 `_tail()` 继续读完，而不是重新发一次请求。
"""
from __future__ import annotations

import json
import logging
import threading
import time

from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from admin.client_ip import request_ip
from admin.db import SessionLocal
from admin.platform import registry
from admin.platform.registry import get as get_platform
from admin.pool import POOL
from admin.security import check_quota, get_key_row
from admin.platform.trae.core import catalog, client, protocol
from admin.platform.trae.services import pool as trae_pool

logger = logging.getLogger("admin.platform.trae.gateway")

#: 落库平台（两个区共用）
PLATFORM = "trae"
#: 对外平台 id（国内版 / 国际版各自算一个平台）
CN_ID = "trae-cn"
INTL_ID = "trae-intl"
#: 存储层的区域键
REALM_KEYS = ("cn", "intl")

#: 本网关负责的路径。
PATHS = frozenset({
    "/v1/chat/completions", "/v1/responses", "/v1/messages",
    "/v1/messages/count_tokens", "/v1/models", "/v1/balance",
})

#: 端点 -> usage_logs.use_case
USE_CASE = {
    "/v1/chat/completions": "trae-chat",
    "/v1/responses": "trae-responses",
    "/v1/messages": "trae-messages",
    "/v1/models": "trae-models",
    "/v1/balance": "trae-balance",
}

#: 单请求最多换几个号。Trae 的失败判定很快（不像 WorkBuddy 要等静默超时），
#: 因此允许比全局更多次尝试；仍有上限，避免把一次请求放大成风暴。
MAX_ATTEMPTS = 4


class GatewayError(Exception):
    """本层的拒绝（鉴权 / 参数 / 无可用账号），统一转成对应协议的错误体。"""

    def __init__(self, status: int, message: str, *, kind="invalid_request_error",
                 code: str = "", headers: dict | None = None):
        self.status = status
        self.message = message
        self.kind = kind
        self.code = code
        self.headers = headers or {}
        super().__init__(message)

    def openai(self) -> JSONResponse:
        body = {"error": {"message": self.message, "type": self.kind}}
        if self.code:
            body["error"]["code"] = self.code
        return JSONResponse(status_code=self.status, content=body,
                            headers=self.headers)

    def anthropic(self) -> JSONResponse:
        return JSONResponse(
            status_code=self.status,
            content={"type": "error",
                     "error": {"type": self.kind, "message": self.message}},
            headers=self.headers)


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------
def resolve_target(request: Request, payload: dict | None, key) -> tuple[str, str]:
    """解析 `(realm, model)`。

    区域优先级：中间件解析结果（`X-Platform: trae-cn`）> 显式
    `X-Realm` / `?realm=` > 模型名自带区域 > 目录归属 > Key 绑定 > 默认区。

    注意现在**国内版与国际版是两个平台**（`trae-cn` / `trae-intl`），
    中间件已经按平台把 `realm` 定下来了，这里通常只是把它取出来用。
    """
    spec = get_platform(PLATFORM)
    model = ""
    if isinstance(payload, dict):
        model = str(payload.get("model") or "")
    if not model:
        model = str(request.query_params.get("model") or "")

    realm = ""
    # 最优先：中间件按平台选择器解析出的区域（`trae-cn` -> cn）。
    state_realm = getattr(getattr(request, "state", None), "realm", "") or ""
    if state_realm and state_realm in REALM_KEYS:
        realm = state_realm
    if not realm:
        for candidate in (request.headers.get("x-realm"),
                          request.query_params.get("realm")):
            if candidate:
                normalized = registry.normalize_realm(spec, candidate) if spec else ""
                if normalized:
                    realm = normalized
                    break

    # 模型名可以写成 `trae:cn/deepseek-V3`（老写法）或 `trae-cn/deepseek-V3`
    if "/" in model:
        head, _, tail = model.partition("/")
        head = head.strip().lower()
        if head in (PLATFORM, CN_ID, INTL_ID) or ":" in head:
            prefix_platform, _, prefix_realm = head.partition(":")
            if prefix_platform == PLATFORM and prefix_realm and not realm:
                realm = registry.normalize_realm(spec, prefix_realm) if spec else ""
            elif prefix_platform == CN_ID:
                realm = realm or "cn"
            elif prefix_platform == INTL_ID:
                realm = realm or "intl"
            if realm:
                model = tail.strip()

    if not realm:
        realm = catalog.realm_for(model)
    if not realm and key is not None:
        bound = str(getattr(key, "platform", "") or "")
        parsed_platform, parsed_realm = registry.resolve(bound) if bound else ("", "")
        if parsed_realm:
            realm = parsed_realm
    if not realm:
        # 两个平台都固定了区域，取当前请求实际来自哪个平台
        active = getattr(getattr(request, "state", None), "platform", "") or ""
        active_spec = get_platform(active) if active else None
        if active_spec and active_spec.realm:
            realm = active_spec.realm
    if not realm:
        realm = "cn"
    return realm, model


def _auth(db: Session, request: Request):
    api_key = request.headers.get("x-api-key")
    if not api_key:
        authorization = request.headers.get("authorization") or ""
        if authorization.startswith("Bearer "):
            api_key = authorization[7:].strip()
    if not api_key:
        raise GatewayError(401, "缺少 API Key", kind="auth_error")
    key = get_key_row(db, api_key)
    if key is None:
        raise GatewayError(401, "无效 API Key", kind="auth_error")
    try:
        check_quota(key)
    except Exception as exc:  # HTTPException(402/403)
        detail = getattr(exc, "detail", {}) or {}
        error = detail.get("error") if isinstance(detail, dict) else None
        raise GatewayError(getattr(exc, "status_code", 403),
                           (error or {}).get("message") or "Key 不可用",
                           code=(error or {}).get("code", "")) from None

    # 风控哨兵熔断：平台被暂停时**直接拒绝，不打上游、不消耗额度**。
    # 这是「及时止损」的落点 —— 上游连坐封号时，继续送请求只会扩大损失。
    try:
        from admin.platform import sentinel_router
        active = getattr(getattr(request, "state", None), "platform", "") or ""
        hold = sentinel_router.paused(active or PLATFORM)
        if hold:
            raise GatewayError(
                503, "该平台已被风控哨兵暂停：%s" % (hold.get("reason") or "疑似连坐封控"),
                kind="server_error", code="upstream_paused",
                headers={"Retry-After": "60",
                         "X-Sentinel-Paused": "1"})
    except GatewayError:
        raise
    except Exception:  # noqa: BLE001 - 哨兵故障不能阻断转发
        pass
    return key


def _client_ip(request: Request) -> str:
    try:
        return request_ip(request)
    except Exception:  # noqa: BLE001
        return ""


#: SOLO 通道**首包**最长等待（秒）。上游正常 1~3 秒就有事件，
#: 超过这个时间基本是排队或卡死，早点换号比干等划算。
_SOLO_FIRST_BYTE_S = 45.0
#: SOLO 通道**两次实质输出之间**的最大间隔（秒）。
#: 上游会零散发 timing/metadata 心跳，socket 读超时抓不到它，
#: 只能靠这个看门狗判「卡住」。
_SOLO_CONTENT_GAP_S = 60.0


def _timeout() -> float:
    from admin.config import settings
    try:
        return float(getattr(settings, "STREAM_IDLE_TIMEOUT", 180) or 180)
    except Exception:  # noqa: BLE001
        return 180.0


def _tool_names(tools) -> set[str] | None:
    names = set()
    for tool in tools or ():
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        name = str(fn.get("name") or "").strip()
        if name:
            names.add(name)
    return names or None


def _tool_system_prompt(tools) -> str:
    """把工具清单渲染成提示词，让上游按约定格式吐 tool_calls。

    Trae raw chat 没有结构化工具协议，只能用文本约定；`core/protocol` 负责
    把约定格式解析回真正的 tool_calls。
    """
    specs = []
    for tool in tools or ():
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        name = str(fn.get("name") or "").strip()
        if not name:
            continue
        specs.append({"name": name,
                      "description": str(fn.get("description") or ""),
                      "parameters": fn.get("parameters")
                      or {"type": "object", "properties": {}}})
    if not specs:
        return ""
    return (
        "你可以调用工具。需要调用工具时，**只**输出下面这一种格式，"
        "不要输出任何解释、前后缀或 markdown 代码块：\n"
        "%s[{\"name\":\"工具名\",\"parameters\":{...}}]%s\n"
        "可以一次输出多个调用（JSON 数组）。不需要调用工具时正常回答。\n"
        "可用工具（JSON Schema）：\n%s"
    ) % (protocol.TOOL_BEGIN, protocol.TOOL_END,
         json.dumps(specs, ensure_ascii=False))


# ---------------------------------------------------------------------------
# 上游调用（含换号重试）
# ---------------------------------------------------------------------------
def _run(db: Session, *, realm: str, model: str, body: dict, key,
         render, sticky_key: str, use_case: str):
    """选号 -> 请求 -> 渲染首个片段，失败按 Trae 错误码换号重试。

    `render(resp, holder)` 必须是**生成器函数**：返回一个迭代器，首个片段
    用来判定成功与否。响应对象与渲染器都留在 holder 里，成功时交给调用方
    继续消费（`_tail`），失败时由这里关闭。

    返回 `(account, holder, first_chunk, attempted, ttfb_ms)`。
    """
    exclude: set[int] = set()
    last_error: client.UpstreamError | None = None
    attempted = 0
    started = time.perf_counter()

    for _ in range(MAX_ATTEMPTS):
        account = trae_pool.select(
            db, realm=realm, model=model, exclude_ids=exclude, sticky_key=sticky_key,
            account_id=getattr(key, "account_id", None),
            key_id=getattr(key, "id", None), key=key)
        if account is None:
            break
        if not POOL.acquire_account(account):
            exclude.add(account.id)
            continue
        attempted += 1
        try:
            creds = trae_pool.credentials(db, account)
            resp = client.open_chat(creds, realm, body, timeout=_timeout())
        except client.UpstreamError as exc:
            POOL.release(account.uid or "")
            outcome = trae_pool.note_failure(db, account, exc, model=model)
            last_error = exc
            exclude.add(account.id)
            logger.info("trae 换号 acc=%s kind=%s detail=%s", account.id, exc.kind,
                        exc.detail[:120])
            if not outcome.get("retry", True):
                break
            continue
        except Exception as exc:  # noqa: BLE001 - 传输层意外
            POOL.release(account.uid or "")
            last_error = client.UpstreamError("network", str(exc), None, kind="network")
            exclude.add(account.id)
            continue

        holder = {"requested_model": model, "idle_timeout": _timeout(),
                  "account_id": account.id, "uid": account.uid, "realm": realm,
                  "credentials": creds, "_resp": resp}
        iterator = render(resp, holder)
        try:
            first = next(iterator, None)
        except client.UpstreamError as exc:
            POOL.release(account.uid or "")
            _close(resp)
            outcome = trae_pool.note_failure(db, account, exc, model=model)
            last_error = exc
            exclude.add(account.id)
            if not outcome.get("retry", True):
                break
            continue
        except Exception as exc:  # noqa: BLE001
            POOL.release(account.uid or "")
            _close(resp)
            logger.exception("trae 渲染异常 acc=%s: %s", account.id, exc)
            last_error = client.UpstreamError("render", str(exc), None, kind="network")
            exclude.add(account.id)
            continue

        # 首个片段已产出：本次尝试成功，后续不再换号（否则客户端会看到重复正文）。
        trae_pool.note_success(db, account)
        holder["_iterator"] = iterator
        ttfb_ms = int((time.perf_counter() - started) * 1000)
        return account, holder, first, attempted, ttfb_ms

    message = (last_error.message if last_error
               else "号池里没有可用的 Trae 账号（请先在控制台导入并确认状态为启用）")
    status = (last_error.status if last_error and last_error.status else 503)
    raise GatewayError(status if status >= 400 else 502, message,
                       kind="upstream_error" if last_error else "server_error",
                       code=str(last_error.code) if last_error else "no_account",
                       headers={"Retry-After": "3"} if status >= 500 else {})


def _close(resp) -> None:
    try:
        resp.close()
    except Exception:  # noqa: BLE001
        pass


def _tail(holder, first, *, stream: bool):
    """把「首片段 + 剩余片段」接起来，结束时释放租约并记账。"""
    try:
        if first is not None:
            yield first
        for chunk in holder.get("_iterator") or ():
            if chunk is not None:
                yield chunk
    finally:
        _close(holder.get("_resp"))
        holder["_iterator"] = None


def _release(holder) -> None:
    try:
        POOL.release(holder.get("uid") or "")
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------
async def _solo_chat(request, db, key, payload, realm, model, messages,
                      wants_stream, allowed, target="chat"):
    """SOLO / Agent 通道（`/api/agent/v3/llm_utils_chat`）。

    只有这条通道能调 `deepseek-v4.1-flash` 这类 Agent 模型 —— v1 的
    `llm_raw_chat` 对它们一律 4023。上游 SSE 的事件格式也不同：
    `event:output` + `data:{"response":..., "reasoning_content":...}`，
    这里翻译成 OpenAI 的 `choices[].delta`。

    `target` 指定**对外协议**：
      · `chat`      OpenAI `chat.completion.chunk`
      · `responses` OpenAI Responses 事件（`response.output_text.delta`）
      · `messages`  Anthropic（`content_block_delta` / `message_start`）

    不做这层转换的话，`/v1/messages` 会吐 OpenAI 帧，Anthropic 客户端
    解析不了（实测拿到 200 但正文是空的）。
    """
    import json as _json

    from admin.platform.trae.core import solo as trae_solo
    from fastapi.responses import JSONResponse, StreamingResponse

    account = trae_pool.select(db, realm=realm, key_id=getattr(key, "id", None),
                               account_id=getattr(key, "account_id", None))
    if account is None:
        raise GatewayError(503, "号池里没有可用的 Trae 账号（请先导入并确认状态为启用）",
                           kind="server_error", code="no_account")
    creds = trae_pool.credentials(db, account)
    started = time.perf_counter()

    msg_id = "msg_solo_" + str(int(time.time() * 1000))
    anthropic_started = {"done": False}

    def render_event(event) -> str:
        """把 SOLO 事件翻成**目标协议**的 SSE 帧。

        三种出口格式差异很大，不能只发 OpenAI 帧：
          · chat      -> `choices[].delta`
          · responses -> `response.output_text.delta`
          · messages  -> `content_block_delta`（Anthropic）
        """
        reasoning = str(event.get("reasoning_content") or "")
        text = str(event.get("response") or "")
        # **SOLO 的 tool_calls 是 `function_call` 子对象**，与 OpenAI 的
        # `function` 不同；而且参数是分片增量（`arguments` 多帧拼接）。
        # 不转换的话客户端拿到的是未知形状，只能忽略 → 任务不执行。
        raw_tc = event.get("tool_calls")
        if isinstance(raw_tc, list) and raw_tc:
            converted = []
            for item in raw_tc:
                if not isinstance(item, dict):
                    continue
                fc = item.get("function_call")
                if not isinstance(fc, dict):
                    fc = item.get("function") if isinstance(item.get("function"), dict) else {}
                piece = {
                    "index": int(item.get("index") or 0),
                    "type": "function",
                    "function": {
                        "name": str(fc.get("name") or ""),
                        "arguments": str(fc.get("arguments") or ""),
                    },
                }
                if item.get("id"):
                    piece["id"] = str(item["id"])
                converted.append(piece)
            if converted:
                # 走 chat 协议时直接塞进 delta；其它协议下面各自处理
                if target == "chat":
                    chunk = {"id": "chatcmpl-solo",
                             "object": "chat.completion.chunk",
                             "created": int(time.time()), "model": model,
                             "choices": [{"index": 0,
                                          "delta": {"tool_calls": converted},
                                          "finish_reason": None}]}
                    return "data: %s\n\n" % _json.dumps(chunk, ensure_ascii=False)
                # anthropic / responses 协议：把工具调用拼成 JSON 文本块发下去，
                # 由客户端自己解析（这两条协议的 tool_use 形状与 OpenAI 差异太大）
                text = _json.dumps({"tool_calls": converted}, ensure_ascii=False)

        if not reasoning and not text:
            return ""

        # ---- Anthropic `messages` ----
        if target == "messages":
            frames = []
            if not anthropic_started["done"]:
                anthropic_started["done"] = True
                frames.append({
                    "type": "message_start",
                    "message": {"id": msg_id, "type": "message",
                                "role": "assistant", "model": model,
                                "content": [], "stop_reason": None,
                                "usage": {"input_tokens": 0, "output_tokens": 0}},
                })
                frames.append({"type": "content_block_start", "index": 0,
                               "content_block": {"type": "text", "text": ""}})
            piece = text or reasoning
            frames.append({"type": "content_block_delta", "index": 0,
                           "delta": {"type": "text_delta", "text": piece}})
            return "".join("data: %s\n\n" % _json.dumps(f, ensure_ascii=False)
                           for f in frames)

        # ---- OpenAI `responses` ----
        if target == "responses":
            chunks = []
            if not anthropic_started["done"]:
                anthropic_started["done"] = True
                chunks.append({"type": "response.created",
                               "response": {"id": msg_id, "object": "response",
                                            "status": "in_progress",
                                            "model": model, "output": []}})
            piece = text or reasoning
            chunks.append({"type": "response.output_text.delta",
                           "item_id": msg_id, "output_index": 0,
                           "content_index": 0, "delta": piece})
            return "".join("data: %s\n\n" % _json.dumps(c, ensure_ascii=False)
                           for c in chunks)

        # ---- OpenAI `chat`（原生）----
        delta = {}
        if reasoning:
            delta["reasoning_content"] = reasoning
        if text:
            delta["content"] = text
        chunk = {"id": "chatcmpl-solo", "object": "chat.completion.chunk",
                 "created": int(time.time()), "model": model,
                 "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
        return "data: %s\n\n" % _json.dumps(chunk, ensure_ascii=False)

    try:
        resp = trae_solo.open_stream(creds, model, messages,
                                     max_tokens=payload.get("max_tokens") or 0,
                                     tools=payload.get("tools") or None,
                                     timeout=_timeout())
    except Exception as exc:  # noqa: BLE001
        trae_pool.note_failure(db, account, exc, model=model)
        raise GatewayError(502, "SOLO 通道调用失败：%s" % str(exc)[:200],
                           kind="upstream_error") from exc

    if wants_stream:
        def generator():
            finish = "stop"
            saw_tool = False
            usage: dict = {}
            # 首包时间：**第一个上游事件到达**。
            first_event_at = None
            # **首个正文/工具**到达时刻 —— 这才是 TTFT 的正确定义。
            #
            # SOLO 会先吐 `reasoning_content`（思考链），正文要晚得多。
            # 实测同一个请求：首个思考事件 1.3 秒，首个正文 **22 秒**。
            # 用首个事件当 TTFT，等于说「1.3 秒就出字了」，跟客户端看到的
            # 完全不一致，而且解码段被算成 22 秒，TPS 只有 73（真实约 1000）。
            first_content_at = None
            last_content_at = None
            # ---- 看门狗：**首包之后长时间没有实质输出就判卡死** ----
            #
            # 实测有这种病态请求：`ttfb=2473ms`（首包正常），
            # 但之后 **296 秒一个正文 token 都没出**，一直挂到 300 秒总时限
            # 才断（`out=0`）。上游会零星发 timing/metadata 事件，
            # 所以 socket 读超时永远不会触发 —— 必须自己看门狗。
            #
            # 判据是「实质输出」：正文 / 思考链 / 工具调用都算有进展，
            # 只有这些都没有才认为卡住。
            watchdog: list = [None]

            def _arm_watchdog(seconds: float):
                """重置看门狗：`seconds` 内没有实质输出就关掉上游连接。"""
                if watchdog[0] is not None:
                    try:
                        watchdog[0].cancel()
                    except Exception:  # noqa: BLE001
                        pass
                timer = threading.Timer(seconds, _kill_stalled)
                timer.daemon = True
                watchdog[0] = timer
                timer.start()

            def _kill_stalled():
                try:
                    resp.close()
                except Exception:  # noqa: BLE001
                    pass

            def _disarm_watchdog():
                if watchdog[0] is not None:
                    try:
                        watchdog[0].cancel()
                    except Exception:  # noqa: BLE001
                        pass
                    watchdog[0] = None

            try:
                # 首包最多等 FIRST_BYTE_S 秒；拿到首包后按 CONTENT_GAP_S 计。
                _arm_watchdog(_SOLO_FIRST_BYTE_S)
                for event in trae_solo.iter_events(resp):
                    if first_event_at is None:
                        first_event_at = time.perf_counter()
                    code = event.get("code")
                    # 同上：code 可能是 dict，不能用 `in`
                    if code is not None and code is not False and not (
                            isinstance(code, (int, float)) and code == 0) and not (
                            isinstance(code, str) and not code.strip()) and not (
                            isinstance(code, (dict, list)) and not code):
                        yield ("data: %s\n\n" % _json.dumps(
                            {"error": {"message": event.get("message") or "upstream error",
                                       "type": "upstream_error"}},
                            ensure_ascii=False))
                        break
                    if event.get("finish_reason"):
                        finish = str(event["finish_reason"])
                    # 收到工具调用就把收尾语义改成 `tool_calls`：
                    # 客户端据此知道「该执行工具了」，而不是「说完了」。
                    if isinstance(event.get("tool_calls"), list) and event["tool_calls"]:
                        saw_tool = True
                    if "prompt_tokens" in event:
                        usage = {"prompt_tokens": int(event.get("prompt_tokens") or 0),
                                 "completion_tokens": int(event.get("completion_tokens") or 0),
                                 "total_tokens": int(event.get("total_tokens") or 0)}
                    # 有实质输出就重置看门狗（正文/思考/工具都算进展）
                    if (event.get("response") or event.get("reasoning_content")
                            or event.get("tool_calls")):
                        _arm_watchdog(_SOLO_CONTENT_GAP_S)
                    # TTFT 只认**正文/工具**；思考链不算「开始出字」
                    if event.get("response") or event.get("tool_calls"):
                        _now_c = time.perf_counter()
                        if first_content_at is None:
                            first_content_at = _now_c
                        last_content_at = _now_c
                    frame = render_event(event)
                    if frame:
                        yield frame
                if saw_tool and finish in ("", "stop"):
                    finish = "tool_calls"
                # 估算积分：客户端底部「消耗 N 积分」读 credit/credits/cost。
                # 上游 SOLO 事件里没有积分类字段，按倍率补一个。
                credit = 0.0
                try:
                    from admin.db import SessionLocal
                    from admin.platform.shared import credits_for

                    _toks = int((usage or {}).get("total_tokens")
                                or (usage or {}).get("completion_tokens") or 0)
                    if _toks:
                        with SessionLocal() as _db:
                            credit = credits_for(_db, "trae", model, _toks)
                except Exception:  # noqa: BLE001
                    credit = 0.0
                if target == "messages":
                    yield "data: %s\n\n" % _json.dumps(
                        {"type": "content_block_stop", "index": 0},
                        ensure_ascii=False)
                    yield "data: %s\n\n" % _json.dumps(
                        {"type": "message_delta",
                         "delta": {"stop_reason": finish or "end_turn"},
                         "usage": {"input_tokens":
                                   int((usage or {}).get("prompt_tokens") or 0),
                                   "output_tokens":
                                   int((usage or {}).get("completion_tokens") or 0),
                                   **({"credit": credit, "credits": credit,
                                       "cost": credit} if credit else {})}},
                        ensure_ascii=False)
                    yield "data: %s\n\n" % _json.dumps(
                        {"type": "message_stop"}, ensure_ascii=False)
                elif target == "responses":
                    yield "data: %s\n\n" % _json.dumps(
                        {"type": "response.completed",
                         "response": {"id": msg_id, "object": "response",
                                      "status": "completed", "model": model,
                                      "usage": {**(usage or {}),
                                                **({"credit": credit,
                                                    "credits": credit,
                                                    "cost": credit}
                                                   if credit else {})}}},
                        ensure_ascii=False)
                else:
                    tail = {"id": "chatcmpl-solo",
                            "object": "chat.completion.chunk",
                            "created": int(time.time()), "model": model,
                            "choices": [{"index": 0, "delta": {},
                                         "finish_reason": finish}]}
                    _u = dict(usage or {})
                    if credit:
                        _u.update({"credit": credit, "credits": credit,
                                   "cost": credit})
                    if _u:
                        tail["usage"] = _u
                    yield "data: %s\n\n" % _json.dumps(tail, ensure_ascii=False)
                    yield "data: [DONE]\n\n"
            finally:
                _disarm_watchdog()
                try:
                    resp.close()
                except Exception:  # noqa: BLE001
                    pass
                # TTFT 优先用首个正文；整段没有任何正文时才退回首个事件
                _ttft_at = first_content_at or first_event_at
                _last_at = last_content_at or _ttft_at
                _bill_solo(db, key, account, model, usage, started,
                           request, error="",
                           ttfb_ms=(int((_ttft_at - started) * 1000)
                                    if _ttft_at else None),
                           end_ms=(int((_last_at - started) * 1000)
                                   if _last_at else None))

        return StreamingResponse(
            generator(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    result = trae_solo.collect(resp)
    try:
        resp.close()
    except Exception:  # noqa: BLE001
        pass
    content = result.get("content") or ""
    if not content and not result.get("reasoning"):
        raise GatewayError(502, "SOLO 返回空内容", kind="upstream_error")
    _bill_solo(db, key, account, model, result.get("usage") or {}, started,
               request, error="")
    trae_pool.note_success(db, account)
    finish = result.get("finish_reason") or "stop"
    usage = result.get("usage") or {}

    # ---- Anthropic `messages` 非流式 ----
    if target == "messages":
        return JSONResponse({
            "id": msg_id, "type": "message", "role": "assistant",
            "model": model,
            "content": [{"type": "text", "text": content}],
            "stop_reason": ("end_turn" if finish == "stop" else finish),
            "usage": {
                "input_tokens": int(usage.get("prompt_tokens") or 0),
                "output_tokens": int(usage.get("completion_tokens") or 0),
            },
        })

    # ---- OpenAI `responses` 非流式 ----
    if target == "responses":
        return JSONResponse({
            "id": msg_id, "object": "response", "status": "completed",
            "model": model,
            "output": [{"type": "message", "role": "assistant",
                        "content": [{"type": "output_text", "text": content}]}],
            "usage": {
                "input_tokens": int(usage.get("prompt_tokens") or 0),
                "output_tokens": int(usage.get("completion_tokens") or 0),
            },
        })

    # ---- OpenAI `chat` 非流式 ----
    message = {"role": "assistant", "content": content}
    if result.get("reasoning"):
        message["reasoning_content"] = result["reasoning"]
    return JSONResponse({
        "id": "chatcmpl-solo", "object": "chat.completion",
        "created": int(time.time()), "model": model,
        "choices": [{"index": 0, "message": message,
                     "finish_reason": finish}],
        "usage": usage,
    })


def _bill_solo(db, key, account, model, usage, started, request, error="",
               ttfb_ms=None, end_ms=None):
    """记账（SOLO 通道）。

    `ttfb_ms` 是**首个正文**到达时间，`end_ms` 是**最后一个正文**时间。
    两者相减才是纯解码耗时。

    为什么必须分开：SOLO 会先吐很长的思考链，再用几百毫秒把正文一次性
    爆发出来。实测同一个请求 —— 首包 1.3 秒（那是思考）、首个正文 22 秒、
    全部输出在最后 1.5 秒内完成。若 `latency` 取「读完整个响应」，
    解码段被算成 22 秒，TPS 只有 73（真实约 1000，差一个量级）。
    """
    try:
        from admin.platform import shared

        elapsed = int((time.perf_counter() - started) * 1000)
        ttfb = int(ttfb_ms) if ttfb_ms else elapsed
        # **latency 取最后一个正文的时刻**，而不是「读完响应」——
        # 后者含有收尾帧、usage 帧与闲聊事件的传输时间。
        latency = int(end_ms) if end_ms else elapsed
        if ttfb > latency:
            ttfb, latency = latency, ttfb
        shared.record_usage(
            db,
            key_id=int(getattr(key, "id", 0) or 0),
            account_id=int(getattr(account, "id", 0) or 0),
            model=model, usage=usage,
            public_platform=shared.public_platform_of(request, "trae"),
            storage="trae", error_kind=error,
            client_ip=_client_ip(request),
            use_case="trae-solo-chat",
            ttfb_ms=ttfb, latency_ms=latency)
    except Exception:  # noqa: BLE001
        logger.exception("SOLO 记账失败")


async def chat_completions(request: Request) -> StreamingResponse | JSONResponse:
    started = time.perf_counter()   # _bill 用它算总耗时
    db = SessionLocal()
    holder_box: dict = {}
    try:
        key = _auth(db, request)
        payload = await _json_body(request)
        realm, model = resolve_target(request, payload, key)
        messages = payload.get("messages")
        if not messages:
            raise GatewayError(400, "messages 不能为空")

        tools = payload.get("tools") or []
        allowed = _tool_names(tools)
        wants_stream = bool(payload.get("stream"))

        # SOLO / Agent 类模型（deepseek-v4.1-flash、glm-5.3-flash、kimi-k3 …）
        # 必须走 /api/agent/v3/llm_utils_chat，走 llm_raw_chat 会被上游
        # 回 4023「模型不存在」—— 那个错误码是端点级的，不是模型级的。
        from admin.platform.trae.core import solo as trae_solo

        if trae_solo.is_solo_model(model):
            return await _solo_chat(request, db, key, payload, realm, model,
                                    messages, wants_stream, allowed,
                                    target="chat")

        body = client.build_chat_body(
            catalog.upstream_name(model, realm), messages,
            temperature=payload.get("temperature"),
            top_p=payload.get("top_p"),
            max_tokens=payload.get("max_tokens") or payload.get("max_completion_tokens"),
            stop=payload.get("stop"),
            presence_penalty=payload.get("presence_penalty"),
            frequency_penalty=payload.get("frequency_penalty"),
        )
        prompt = _tool_system_prompt(tools)
        if prompt:
            body["messages"].insert(0, {"role": "system",
                                        "content": [{"type": "text", "text": prompt}]})
        sticky = trae_pool.session_key(payload)
        ip = _client_ip(request)

        if not wants_stream:
            def render(resp, hold):
                yield protocol.aggregate_openai(resp, hold, allowed)

            account, holder, result, attempted, ttfb = _run(
                db, realm=realm, model=model, body=body, key=key,
                render=render, sticky_key=sticky, use_case=USE_CASE["/v1/chat/completions"])
            _release(holder)
            if isinstance(result, dict):
                # 上游会在流内回真名（例如客户端写 `DeepSeek-V3`、上游认
                # `deepseek-V3`）。展示成真名更利于排障，也便于客户端复制。
                result["model"] = (holder.get("meta") or {}).get("model") or model
            _bill(key, account, model, holder, ip, ttfb, attempted,
"/v1/chat/completions", started=started)
            return JSONResponse(result or {})

        def render_stream(resp, hold):
            yield from protocol.stream_openai(resp, hold, allowed)

        account, holder, first, attempted, ttfb = _run(
            db, realm=realm, model=model, body=body, key=key,
            render=render_stream, sticky_key=sticky,
            use_case=USE_CASE["/v1/chat/completions"])
        holder_box["h"] = holder

        def generator():
            try:
                yield from _tail(holder, first, stream=True)
            finally:
                _release(holder)
                _bill(key, account, model, holder, ip, ttfb, attempted,
"/v1/chat/completions", started=started)

        return StreamingResponse(generator(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})
    finally:
        db.close()


async def responses(request: Request) -> StreamingResponse | JSONResponse:
    started = time.perf_counter()   # _bill 用它算总耗时
    db = SessionLocal()
    try:
        key = _auth(db, request)
        payload = await _json_body(request)
        chat = protocol.responses_to_chat(payload)
        realm, model = resolve_target(request, chat, key)
        chat["model"] = model
        messages = chat.get("messages")
        if not messages:
            raise GatewayError(400, "input 不能为空")

        tools = chat.get("tools") or []
        allowed = _tool_names(tools)
        wants_stream = bool(payload.get("stream"))

        # SOLO / Agent 类模型必须走 `/api/agent/v3/llm_utils_chat`。
        # `responses` 之前漏了这段分发，于是 deepseek-v4.1-flash 还是
        # 打老端点、被上游回 4023「模型不存在」，再被记成 model_block
        # 负缓存 6 小时 → 后续所有请求都 503「没有可用账号」。
        from admin.platform.trae.core import solo as trae_solo

        if trae_solo.is_solo_model(model):
            return await _solo_chat(request, db, key, payload, realm, model,
                                    messages, wants_stream, allowed,
                                    target="responses")

        body = client.build_chat_body(
            catalog.upstream_name(model, realm), messages,
            temperature=chat.get("temperature"), top_p=chat.get("top_p"),
            max_tokens=chat.get("max_tokens"), stop=chat.get("stop"),
        )
        prompt = _tool_system_prompt(tools)
        if prompt:
            body["messages"].insert(0, {"role": "system",
                                        "content": [{"type": "text", "text": prompt}]})
        sticky = trae_pool.session_key(chat)
        ip = _client_ip(request)

        if not wants_stream:
            def render(resp, hold):
                yield protocol.aggregate_openai(resp, hold, allowed)

            account, holder, result, attempted, ttfb = _run(
                db, realm=realm, model=model, body=body, key=key,
                render=render, sticky_key=sticky, use_case=USE_CASE["/v1/responses"])
            _release(holder)
            _bill(key, account, model, holder, ip, ttfb, attempted,
"/v1/responses", started=started)
            return JSONResponse(protocol.chat_to_responses(result or {}, model))

        def render_stream(resp, hold):
            yield from protocol.stream_responses(resp, model, hold, allowed)

        account, holder, first, attempted, ttfb = _run(
            db, realm=realm, model=model, body=body, key=key,
            render=render_stream, sticky_key=sticky, use_case=USE_CASE["/v1/responses"])

        def generator():
            try:
                yield from _tail(holder, first, stream=True)
            finally:
                _release(holder)
                _bill(key, account, model, holder, ip, ttfb, attempted,
"/v1/responses", started=started)

        return StreamingResponse(generator(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})
    finally:
        db.close()


async def messages(request: Request) -> StreamingResponse | JSONResponse:
    started = time.perf_counter()   # _bill 用它算总耗时
    db = SessionLocal()
    try:
        key = _auth(db, request)
        payload = await _json_body(request)
        chat = protocol.messages_to_chat(payload)
        realm, model = resolve_target(request, chat, key)
        chat["model"] = model
        messages_list = chat.get("messages")
        if not messages_list:
            raise GatewayError(400, "messages 不能为空")

        tools = chat.get("tools") or []
        allowed = _tool_names(tools)
        wants_stream = bool(payload.get("stream"))

        # 同 `responses`：SOLO 类模型必须走 Agent 端点，
        # 否则老端点回 4023「模型不存在」并污染 model_block 负缓存。
        from admin.platform.trae.core import solo as trae_solo

        if trae_solo.is_solo_model(model):
            return await _solo_chat(request, db, key, payload, realm, model,
                                    messages_list, wants_stream, allowed,
                                    target="messages")

        body = client.build_chat_body(
            catalog.upstream_name(model, realm), messages_list,
            temperature=chat.get("temperature"), top_p=chat.get("top_p"),
            max_tokens=chat.get("max_tokens"), stop=chat.get("stop"),
        )
        prompt = _tool_system_prompt(tools)
        if prompt:
            body["messages"].insert(0, {"role": "system",
                                        "content": [{"type": "text", "text": prompt}]})
        sticky = trae_pool.session_key(chat)
        ip = _client_ip(request)

        if not wants_stream:
            def render(resp, hold):
                yield protocol.aggregate_openai(resp, hold, allowed)

            account, holder, result, attempted, ttfb = _run(
                db, realm=realm, model=model, body=body, key=key,
                render=render, sticky_key=sticky, use_case=USE_CASE["/v1/messages"])
            _release(holder)
            _bill(key, account, model, holder, ip, ttfb, attempted,
"/v1/messages", started=started)
            return JSONResponse(protocol.chat_to_messages(result or {}, model))

        def render_stream(resp, hold):
            yield from protocol.stream_anthropic(resp, model, hold, allowed)

        account, holder, first, attempted, ttfb = _run(
            db, realm=realm, model=model, body=body, key=key,
            render=render_stream, sticky_key=sticky, use_case=USE_CASE["/v1/messages"])

        def generator():
            try:
                yield from _tail(holder, first, stream=True)
            finally:
                _release(holder)
                _bill(key, account, model, holder, ip, ttfb, attempted,
"/v1/messages", started=started)

        return StreamingResponse(generator(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})
    finally:
        db.close()


async def count_tokens(request: Request) -> JSONResponse:
    """Anthropic `/v1/messages/count_tokens`。

    上游没有对应接口，按字符数估算（中文按 1 token / 字，其余按 4 字符 / token）。
    宁可估高一点：客户端用它做预算判断，低估更容易炸上下文。
    """
    db = SessionLocal()
    try:
        _auth(db, request)
        payload = await _json_body(request)
        text_parts: list[str] = []
        system = payload.get("system")
        for item in _as_list(system):
            text_parts.append(str(item.get("text") if isinstance(item, dict) else item))
        for item in _as_list(payload.get("messages")):
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if isinstance(content, str):
                text_parts.append(content)
            for part in _as_list(content):
                if isinstance(part, dict) and part.get("type") == "text":
                    text_parts.append(str(part.get("text") or ""))
        return JSONResponse({"input_tokens": _estimate_tokens("".join(text_parts))})
    finally:
        db.close()


def _as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _estimate_tokens(text: str) -> int:
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    rest = len(text) - cjk
    return max(1, cjk + (rest // 4) + 1)


async def list_models(request: Request) -> JSONResponse:
    """`/v1/models`：目录 + 上游真实清单合并去重。

    上游 `model_list` 只有当前账号可用的少数几个模型，而快照里有更多；
    只回上游会让大量模型「消失」，只回快照会让客户端点到调不通的模型。
    因此：**上游清单优先排前**，快照补在后面并标 `available=false`。
    """
    db = SessionLocal()
    try:
        key = _auth(db, request)
        realm, _model = resolve_target(request, None, key)
        live_names: set[str] = set()
        note = ""
        try:
            account = trae_pool.select(db, realm=realm, key_id=getattr(key, "id", None),
                                       account_id=getattr(key, "account_id", None),
                                       key=key, mark_picked=False)
            if account is None:
                note = "号池里没有可用的 Trae 账号，仅返回目录"
            else:
                creds = trae_pool.credentials(db, account)
                rows = client.fetch_model_list(creds, realm)
                catalog.note_live_rows(realm, rows)
                live_names = {str(r.get("name") or "") for r in rows}
                _release({"uid": account.uid})
        except client.UpstreamError as exc:
            note = "上游模型清单获取失败（%s），仅返回目录" % exc.message
        except Exception as exc:  # noqa: BLE001
            note = "上游模型清单获取失败：%s" % exc

        entries = []
        for item in catalog.realm_models(realm):
            upstream = str(item.get("upstream") or item.get("key") or "")
            # 同一模型可能在两个区都存在，这里必须按**本次请求的区**上报，
            # 否则国际版的清单里会混进国内版条目（客户端按 realm 过滤会失效）。
            if item.get("realm") and item.get("realm") != realm:
                continue
            # **不返回会员专属模型**：本系统的号几乎全是 free，列出来只会
            # 让客户端选到调不通的模型、白拿一轮 4xx。
            # `free` 来自客户端 identity_list（见 desktop_catalog）；
            # `None` 表示无法识别，保守保留而不是藏起来。
            if item.get("free") is False:
                continue
            entries.append({
                "id": upstream,
                "object": "model",
                "created": 0,
                "owned_by": "trae",
                "name": item.get("display_name") or upstream,
                "display_name": item.get("display_name") or upstream,
                "realm": item.get("realm") or realm,
                "context_window": item.get("context_window") or 0,
                "max_tokens": item.get("max_tokens") or 0,
                "model_type": item.get("model_type") or "chat_model",
                "available": (upstream in live_names) if live_names else True,
            })
        payload = {"object": "list", "data": entries, "realm": realm}
        if note:
            payload["note"] = note
        return JSONResponse(payload)
    finally:
        db.close()


async def balance(request: Request) -> JSONResponse:
    db = SessionLocal()
    try:
        key = _auth(db, request)
        realm, _ = resolve_target(request, None, key)
        rows = trae_pool.accounts_for(db, realm)
        total = sum(int(a.balance_total or 0) for a in rows if a.status == "active")
        remain = sum(int(a.balance_remain or 0) for a in rows if a.status == "active")
        return JSONResponse({
            "object": "balance",
            "platform": PLATFORM,
            "realm": realm,
            "accounts": len(rows),
            "active": sum(1 for a in rows if a.status == "active"),
            "total_credits": total,
            "remain_credits": remain,
            "key_credit_used": float(getattr(key, "credit_used", 0) or 0),
            "key_credit_limit": (None if getattr(key, "unlimited", 0)
                                 else float(getattr(key, "credit_limit", 0) or 0)),
        })
    finally:
        db.close()


async def _json_body(request: Request) -> dict:
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        raise GatewayError(400, "bad json") from None
    if not isinstance(payload, dict):
        raise GatewayError(400, "请求体必须是 JSON 对象")
    return payload


def _bill(key, account, model, holder, ip, ttfb_ms, attempted, path,
          started=None) -> None:
    """记一条用量（独立 session，绝不影响已返回的响应）。

    `started` 是请求开始时的 `time.perf_counter()`。用它算**总耗时**，
    和 `ttfb_ms` 分开记 —— 两者相等的话 `latency - ttfb = 0`，
    TPS 只能退回整段计算，真实 200+ tok/s 会被算成 40 多。
    """
    try:
        usage = holder.get("usage") or {}
        # 总耗时：流式消费完再记账，此刻的时间才是真正的完成时间
        total_ms = (int((time.perf_counter() - started) * 1000)
                    if started else None)
        ttfb = int(ttfb_ms or 0)
        # 数据自洽性：总耗时不能小于首包时间
        if total_ms is not None and total_ms < ttfb:
            total_ms = ttfb
        with SessionLocal() as session:
            trae_pool.record_usage(
                session,
                key_id=getattr(key, "id", 0) or 0,
                account_id=getattr(account, "id", 0) or 0,
                model=model or str((holder.get("meta") or {}).get("model") or ""),
                usage=usage,
                client_ip=ip,
                use_case=USE_CASE.get(path, ""),
                seq=attempted,
                ttfb_ms=ttfb or total_ms,
                latency_ms=total_ms or ttfb,
                realm=str(holder.get("realm") or ""),
            )
    except Exception:  # noqa: BLE001
        logger.exception("trae 记账失败 path=%s", path)


# ---------------------------------------------------------------------------
# 分发入口（由 admin.routers.proxy 调用）
# ---------------------------------------------------------------------------
HANDLERS = {
    "/v1/chat/completions": chat_completions,
    "/v1/responses": responses,
    "/v1/messages": messages,
    "/v1/messages/count_tokens": count_tokens,
    "/v1/models": list_models,
    "/v1/balance": balance,
}


async def dispatch(request: Request, path: str):
    """按路径分发；未知路径返回 404。"""
    handler = HANDLERS.get(path)
    if handler is None:
        return JSONResponse(status_code=404,
                            content={"error": {"message": "Not Found",
                                               "type": "invalid_request_error"}})
    try:
        return await handler(request)
    except GatewayError as exc:
        if path in ("/v1/messages", "/v1/messages/count_tokens"):
            return exc.anthropic()
        return exc.openai()