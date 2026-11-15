"""小浣熊转发层：/v1/chat/completions、/v1/responses、/v1/messages、/v1/models、/v1/balance。

## 与小浣熊上游的关系

上游是**标准 OpenAI 形态**（`{llmBase}/chat/completions`，Bearer JWT），
所以 chat 这条链路基本是透传 —— 不需要像 Loomy 那样做协议重塑。

三处必须自己处理：

1. **信封**：上游业务错误可能是 HTTP 200 + `{"code": 401xx}`，
   `client.unwrap()` 已把它转成 `UpstreamError`。
2. **流式**：上游 `stream:true` 直接吐 `text/event-stream`，透传即可；
   但要自己拼收尾（usage 帧 + `[DONE]`）并记账。
3. **角色归一**：只保留 system/user/assistant（理由见 `_UPSTREAM_ROLES`）。

## 为什么没有「图像模型过滤」

`/model_catalog` 里 `categories[].type` 目前只有 `chat` 一类；
`visible=false` 的模型也会出现在目录里（那是上游的隐藏模型）。
`list_models` 里把 `visible=false` 的过滤掉，避免客户端选到不给调的型号。
"""
from __future__ import annotations

import json
import logging
import time

from starlette.background import BackgroundTask
from starlette.responses import JSONResponse, StreamingResponse

from admin.platform import shared
from admin.platform import protocol_bridge as bridge
from admin.platform.raccoon.core import client
from admin.platform.raccoon.services import accounts as raccoon_accounts

logger = logging.getLogger("admin.platform.raccoon.gateway")

#: 拦截路径（dispatch 据此判断要不要转交）
PATHS = (
    "/v1/chat/completions",
    "/v1/messages",
    "/v1/messages/count_tokens",
    "/v1/responses",
    "/v1/models",
    "/v1/balance",
)

PUBLIC = "raccoon"
MAX_ATTEMPTS = 3


def _cool_seconds(exc) -> int:
    """按错误类别给冷却秒数（对齐 `qoder/gateway.py` 的 per-kind 约定）。

    * `rate`：优先用上游 `retry after`，夹逼到
      `[client._RATE_COOL_MIN_SECONDS, client._RATE_COOL_MAX_SECONDS]`；
      解析不到回落 600（上游大多只给文案、不给秒数）。
    * `auth` / `quota`：600（与改动前一致）。
    * 其余：0 —— 交给换号重试，不冷却。
    """
    if exc.kind == "rate":
        seconds = client.retry_after_seconds(exc.detail or exc.message)
        if seconds is None:
            return 600
        return max(client._RATE_COOL_MIN_SECONDS,
                   min(client._RATE_COOL_MAX_SECONDS, seconds))
    if exc.kind in ("auth", "quota"):
        return 600
    return 0


class _BadRequest(Exception):
    """请求体本身有问题（客户端错误），应由调用方转成 400。

    单独一个类型是为了让它**穿过** `except Exception` 的资源清理分支
    之后不被当成 5xx —— 同时又不绕过清理（直接 return 会泄漏 Session）。
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

#: 上游只认这三种角色。`developer` 是 OpenAI 新角色，原样透传会被上游
#: 静默丢弃 —— 表现为「回复里没有系统提示」而不是报错，极难排查。
_UPSTREAM_ROLES = frozenset({"system", "user", "assistant"})


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
    """把上游错误转成响应。

    **上游原文只进日志，不进响应**：`message` 是给调用方看的短句，
    `detail`（客户端里已脱敏）落日志。否则上游若回显 Authorization，
    token 会随响应扩散到每一个调用方。
    """
    try:
        logger.warning("小浣熊上游失败 kind=%s status=%s code=%s msg=%s detail=%s",
                       getattr(exc, "kind", ""), getattr(exc, "status", ""),
                       getattr(exc, "code", ""),
                       str(getattr(exc, "message", ""))[:200],
                       str(getattr(exc, "detail", ""))[:600])
    except Exception:  # noqa: BLE001
        pass
    status = exc.status or 502
    if status < 400 or status > 599:
        status = 502
    return JSONResponse(
        {"error": {"type": exc.kind, "code": exc.code or "upstream_error",
                   "message": exc.message}},
        status_code=status)


async def _body_of(request) -> dict:
    """读 JSON body；GET/HEAD 无 body，直接返回空（读了会卡住）。"""
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


def normalize_messages(raw) -> list[dict]:
    """把 messages 归一成上游能接受的样子。

    ## 为什么 `tool` 不能改成 `user` 还留着 `tool_call_id`

    上游只认 `system` / `user` / `assistant` 三种角色。把 `tool` 直接
    改名为 `user` 却**保留 `tool_call_id`**，产出的是

        {"role": "user", "tool_call_id": "call_1", "content": "..."}

    这不符合 OpenAI 协议（`tool_call_id` 只允许出现在 `role=tool` 里），
    严格校验的上游直接 400。而 agent 多轮任务的主路径**必然**走这条路。

    正确做法（与 `loomy/gateway.py:normalize_messages` 一致）：
    把工具结果包成带调用标识的文本，再以 `user` 身份发出 ——
    模型能看出「这是哪次调用的结果」，也会照此决定下一步，
    这比丢给模型一段孤立文本可靠得多（否则它会重复调同一个工具）。

    其余规则：
      * `developer` → `system`；未知角色丢弃
      * `system` **全部前置**（中途出现的 system 会失去指令权重）
      * assistant 的 `tool_calls` 转成结构化 JSON 代码块，
        而不是删掉 —— 删了模型就不知道上一轮调过什么
    """
    if not isinstance(raw, list):
        return []
    systems: list[dict] = []
    others: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        tool_hint = ""
        if role == "developer":
            role = "system"
        elif role in ("tool", "function"):
            role = "user"
            tcid = str(item.get("tool_call_id") or item.get("name") or "").strip()
            raw_c = item.get("content")
            text = raw_c if isinstance(raw_c, str) else ""
            if not text and isinstance(raw_c, list):
                text = "".join(str(b.get("text") or "") for b in raw_c
                               if isinstance(b, dict))
            tool_hint = ("[tool result%s]\n%s"
                         % (" for " + tcid if tcid else "", text))
        if role not in _UPSTREAM_ROLES:
            continue

        content = tool_hint or item.get("content")
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") in ("text", "input_text", "output_text"):
                        parts.append(str(block.get("text") or ""))
                    elif block.get("type") == "image_url":
                        # 小浣熊目录只有 chat 模型，图片块丢弃而不是冒 400
                        logger.debug("小浣熊：丢弃 image_url 块")
                elif isinstance(block, str):
                    parts.append(block)
            content = "".join(parts)

        # ⚠️ `content is None` 不能直接丢：标准 OpenAI 的 assistant 工具调用
        # 就是 `{"role":"assistant","content":null,"tool_calls":[...]}`。
        # 这里必须先看有没有 tool_calls，否则整条消息被丢弃 —— 模型
        # 下一轮看不到自己调过什么，会重复调同一个工具。
        if content is None:
            if role != "assistant" or not isinstance(item.get("tool_calls"), list):
                continue

        if role == "assistant" and isinstance(item.get("tool_calls"), list):
            calls = []
            for call in item["tool_calls"]:
                if not isinstance(call, dict):
                    continue
                fn = call.get("function") if isinstance(call.get("function"), dict) else {}
                args = fn.get("arguments")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:  # noqa: BLE001 - 保留原串，别丢信息
                        pass
                calls.append({"name": fn.get("name") or "unknown",
                              "arguments": args if args is not None else {}})
            if calls:
                prev = content if isinstance(content, str) else ""
                block = json.dumps(calls, ensure_ascii=False)
                content = ("%s\n\n[上一轮你调用过的工具]\n```json\n%s\n```\n"
                           "这些调用已经执行完毕，结果在下面以 [tool result] 开头给出。"
                           % (prev, block)).strip()

        if isinstance(content, str) and not content.strip():
            continue
        message = {"role": role, "content": content}
        # `tool_call_id` / `name` 只在原始角色仍是 tool 时才合法 —— 上面已经
        # 把内容并进文本，这里一律不再透传，避免产出非法消息。
        if role == "system":
            systems.append(message)
        else:
            others.append(message)
    return systems + others


def prepare_body(body: dict) -> dict:
    """请求体预处理：角色归一 + 补齐参数。

    ## 为什么要塞 `stream_options.include_usage`

    OpenAI 协议的流式响应**默认不返回 usage**（末帧只有 finish_reason）。
    不显式要求的话，`holder["usage"]` 永远是空的 —— 结果是
    **所有流式请求都记 0 token / 0 积分**，而非流式是正常的。
    计费面板因此系统性偏向流式流量，且看不出是漏记。

    上游若不认这个字段，多传一个未知参数通常无害（多数实现会忽略）。
    """
    out = dict(body)
    out["messages"] = normalize_messages(body.get("messages"))
    if not out["messages"]:
        # ⚠️ 不要静默塞一句 "hi"。
        #
        # 触发这里的两类输入都**不该被悄悄改写**：
        #   1. 只带图片的用户消息 —— 小浣熊目录只有 chat 模型，
        #      图片块在 normalize 时被丢弃，于是整条消息变空。
        #      换成 "hi" 会得到一个「凭空回答 hi」的诡异响应。
        #   2. 客户端传了非法 messages —— 明确报错比伪造请求有用。
        #
        # 抛 `ValueError`，由 `_forward` 转成 400 给调用方。
        raise ValueError("messages 归一后为空：请检查是否只发送了不受支持的"
                         "消息类型（本平台目录只有 chat 模型，图片块会被丢弃）")
    if out.get("stream"):
        options = out.get("stream_options")
        options = dict(options) if isinstance(options, dict) else {}
        options.setdefault("include_usage", True)
        out["stream_options"] = options
    return out


# ---------------------------------------------------------------------------
# 选号与调用
# ---------------------------------------------------------------------------
def _attempt(db, request, key, body: dict, realm: str):
    """选号 + 租约 + 调上游（失败换号重试）。

    ## 重试策略

    * **不重试**（直接跳出）的情况：客户端参数错（400/422）、
      模型不存在。这类错误与账号无关，换号也一样失败 ——
      继续换号只会把号池里健康的号逐个排掉。
    * **换号重试**：auth（这个号废了）、quota（这个号没额度了）、
      rate（换个号更快）、上游 5xx。

    每次循环都会 `exclude.add`，所以最多 MAX_ATTEMPTS 轮就一定能退出，
    不存在死循环。
    """
    model = str(body.get("model") or "")
    exclude: set[int] = set()
    last_error: client.UpstreamError | None = None
    for _ in range(MAX_ATTEMPTS):
        account = shared.select_account(
            db, storage=raccoon_accounts.PLATFORM, realm=realm, key=key,
            model=model, exclude_ids=exclude)
        if account is None:
            break
        if not shared.lease(account):
            exclude.add(account.id)
            continue
        started = time.perf_counter()
        try:
            data = raccoon_accounts.ensure_token(db, account)
            result = client.request_json(
                "POST",
                raccoon_accounts.endpoints.llm_base_of(realm) + "/chat/completions",
                token=raccoon_accounts.token_of(data), realm=realm,
                json_body=body, account_id=int(account.id or 0))
            return result, account, started
        except client.UpstreamError as exc:
            shared.release(account)
            shared.note_failure(
                db, account, kind=exc.kind, detail=exc.detail or exc.message,
                status=exc.status, model=model, public_platform=PUBLIC,
                cool_seconds=_cool_seconds(exc))
            last_error = exc
            exclude.add(account.id)
            # 客户端参数错（400/422）与账号无关：换号必然同样失败，
            # 立即停止，避免把健康号逐个排掉。
            #
            # ⚠️ 但 auth/quota/rate 必须继续换号 —— 上游可能用 HTTP 400
            # 承载业务码 401xx（`_classify(40101, 400)` = auth）。
            # 无条件 break 会让「这个号废了」变成「整次请求失败」，
            # 与上面 docstring 写的「auth → 换号重试」直接矛盾。
            if exc.status in (400, 422) and exc.kind not in ("auth", "quota", "rate"):
                break
        except Exception as exc:  # noqa: BLE001
            shared.release(account)
            # 同流式路径：`logger.exception` 的 traceback 会带出
            # SQLAlchemy 绑定参数里的明文凭证，这里只记类型名。
            logger.warning("小浣熊调用失败 acc=%s: %s",
                           account.id, type(exc).__name__)
            last_error = client.UpstreamError("%s 调用异常（%s）"
                                              % (PUBLIC, type(exc).__name__),
                                              kind="upstream")
            exclude.add(account.id)
    if last_error is not None:
        raise last_error
    raise shared.PlatformRejected(
        503, "小浣熊没有可用账号（请先在控制台导入凭证并确认状态为启用）",
        kind="server_error", code="no_available_account",
        headers={"Retry-After": "3"})


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
                usage=usage, public_platform=PUBLIC,
                storage=raccoon_accounts.PLATFORM,
                error_kind=error, client_ip=_client_ip(request),
                use_case="raccoon-chat", seq=1,
                ttfb_ms=ttfb, latency_ms=latency,
                reasoning_effort=shared.reasoning_effort_of_request(request))
    except Exception:  # noqa: BLE001
        logger.exception("小浣熊记账失败")


async def _forward(body: dict, request) -> JSONResponse:
    """鉴权 -> 选号 -> 上游 -> 记账。"""
    from admin.db import SessionLocal

    realm = _realm(request)
    db = SessionLocal()
    try:
        key = shared.authenticate(db, request, platform=PUBLIC)
        try:
            body = prepare_body(body)
        except ValueError as exc:
            # 请求体归一后为空（如只发了图片块）—— 明确的客户端错误，
            # 不是服务端故障，也绝不能伪造一句 "hi" 发上去。
            #
            # 这里可以直接 return：本函数有 `finally: db.close()`。
            # （流式那条路径没有同样的 finally，所以它用 _BadRequest
            #  穿过资源清理分支 —— 两处写法不同是有原因的。）
            return JSONResponse(
                {"error": {"type": "invalid_request_error",
                           "code": "empty_messages", "message": str(exc)}},
                status_code=400)
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
        # `result` 可能是 `None`（上游 200 但 data 为空）。直接 JSONResponse(None)
        # 会给客户端一个字面量 `null`，/v1/chat/completions 的调用方解析
        # `choices[0]` 时崩掉。这里转成明确的错误。
        if not isinstance(result, dict):
            _bill(key, account, str(body.get("model") or ""), {},
                  request=request, started=started, error="empty_upstream")
            return JSONResponse(
                {"error": {"type": "upstream_error", "code": "empty_response",
                           "message": "小浣熊上游返回空响应"}},
                status_code=502)
        _bill(key, account, str(body.get("model") or ""), result,
              request=request, started=started)
        return JSONResponse(result)
    finally:
        db.close()


async def chat_completions(request):
    """对话入口。"""
    body = await _body_of(request)
    if not body.get("stream"):
        return await _forward(body, request)
    return await _forward_stream(body, request)


async def _forward_stream(body: dict, request):
    """流式：选号 -> 上游 SSE -> 逐帧透传 -> 收尾记账。

    小浣熊的流是标准 OpenAI chunk，**不用重塑**，只需：
      * 边转发边抓 `usage`（有的模型只在末帧给）
      * 转发结束后补 `[DONE]`（上游有时不给）
    """
    from admin.db import SessionLocal

    # **强制要 usage 尾帧**：客户端没带 `stream_options.include_usage` 时
    # 上游不发 usage —— 记账与客户端「消耗积分」全是 0。
    if body.get("stream") and not isinstance(
            (body.get("stream_options") or {}).get("include_usage"), bool):
        body = {**body, "stream_options": {"include_usage": True}}

    realm = _realm(request)
    db = SessionLocal()
    account = None
    upstream = None
    leased = False
    try:
        # ⚠️ `authenticate` 会抛 `PlatformRejected`（缺 Key / Key 无效）。
        # 这是**匿名请求也能打到**的路径，必须保证 db 被关掉：
        # 连接池上限 20+40，泄漏 60 次后全站 DB 操作都会超时。
        key = shared.authenticate(db, request, platform=PUBLIC)
        try:
            body = prepare_body(body)
        except ValueError as exc:
            # 请求体归一后为空（如只发了图片块）—— 明确的客户端错误。
            #
            # ⚠️ 这里**不能直接 return**：`return` 会跳过下面的
            # `except Exception`（那里才关 db），造成 Session 泄漏 ——
            # 匿名请求调用 60 次就能耗尽连接池、全站 DB 超时。
            # 用局部异常触发统一清理，再在 except 里转成 400。
            raise _BadRequest("empty_messages", str(exc)) from exc
        body["stream"] = True
        model = str(body.get("model") or "")
        stats: dict = {}
        exclude: set[int] = set()
        last_error = None
        started = time.perf_counter()

        for _ in range(MAX_ATTEMPTS):
            account = shared.select_account(
                db, storage=raccoon_accounts.PLATFORM, realm=realm, key=key,
                model=model, exclude_ids=exclude)
            if account is None:
                break
            if not shared.lease(account):
                exclude.add(account.id)
                continue
            leased = True
            try:
                data = raccoon_accounts.ensure_token(db, account)
                upstream = client.open_stream(
                    raccoon_accounts.endpoints.llm_base_of(realm) + "/chat/completions",
                    token=raccoon_accounts.token_of(data), realm=realm,
                    json_body=body, account_id=int(account.id or 0), stats=stats)
                break
            except client.UpstreamError as exc:
                shared.release(account)
                leased = False
                shared.note_failure(
                    db, account, kind=exc.kind, detail=exc.detail or exc.message,
                    status=exc.status, model=model, public_platform=PUBLIC,
                    cool_seconds=_cool_seconds(exc))
                last_error = exc
                exclude.add(account.id)
            except Exception as exc:  # noqa: BLE001
                shared.release(account)
                leased = False
                # ⚠️ 不用 `logger.exception`：它会打出完整 traceback，
                # 而 SQLAlchemy 的异常 repr 带 `[parameters: (... 'eyJ...')]`，
                # 日志里就是一份明文凭证。只记类型名。
                logger.warning("小浣熊流式调用失败 acc=%s: %s",
                               account.id, type(exc).__name__)
                # 同理：`str(exc)` 会把绑定参数带进 message，
                # 而 message 会直接回给 /v1 调用方。
                last_error = client.UpstreamError(
                    "小浣熊调用异常（%s）" % type(exc).__name__, kind="upstream")
                exclude.add(account.id)
    except _BadRequest as exc:
        # 客户端错误：释放已持有资源（此处通常没有），关 db，回 400。
        if leased and account is not None:
            try:
                shared.release(account)
            except Exception:  # noqa: BLE001
                pass
        db.close()
        return JSONResponse(
            {"error": {"type": "invalid_request_error",
                       "code": exc.code, "message": exc.message}},
            status_code=400)
    except Exception:
        # 鉴权失败 / 选号中断：释放一切已持有的资源再抛出
        if leased and account is not None:
            try:
                shared.release(account)
            except Exception:  # noqa: BLE001
                pass
        if upstream is not None:
            try:
                upstream.close()
            except Exception:  # noqa: BLE001
                pass
        db.close()
        raise

    if upstream is None:
        db.close()
        if last_error is not None:
            return _fail(last_error)
        raise shared.PlatformRejected(
            503, "小浣熊没有可用账号（请先在控制台导入凭证并确认状态为启用）",
            kind="server_error", code="no_available_account",
            headers={"Retry-After": "3"})

    holder: dict = {}
    cleaned = {"done": False}

    def cleanup():
        """幂等清理：连接 / 租约 / Session / 记账。

        ## 为什么不能只靠生成器的 finally

        uvicorn 的 ASGI `spec_version` 是 2.3：客户端断连时会**取消 task**，
        同步生成器只是被丢弃，`finally` 要等 GC 才跑 —— 而 gc 时机不确定。
        实测：不显式 gc 时内层 `finally` 根本不执行。

        泄漏的后果不是「连接多占一会」，而是：
          * `shared.release()` 没跑 → 在途租约不减 →
            `MAX_IN_FLIGHT`（默认 3）耗尽后，**健康的号永久退出号池**；
          * httpx 连接与 DB Session 一起挂着。
        租约泄漏满 3 次，号池就 503，且**必须重启进程**才恢复。

        所以清理做成**幂等函数**，由 `starlette` 的
        `BackgroundTask`（响应结束/断开时一定会跑）兜底调用，
        生成器 finally 只作为快路径。
        """
        if cleaned["done"]:
            return
        cleaned["done"] = True
        try:
            if upstream is not None:
                upstream.close()
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
        try:
            _bill(key, account, model, {"usage": holder.get("usage") or {}},
                  request=request, started=started, stats=stats)
        except Exception:  # noqa: BLE001
            logger.exception("小浣熊流式记账失败")
        try:
            db.close()
        except Exception:  # noqa: BLE001
            pass

    def generator():
        try:
            yield from _relay_stream(upstream, holder)
        except client.UpstreamError as exc:
            yield ("data: %s\n\n" % json.dumps(
                {"error": {"message": exc.message, "type": exc.kind}},
                ensure_ascii=False)).encode()
        except Exception as exc:  # noqa: BLE001
            # ⚠️ 这里必须兜住**所有**异常，不能只 catch UpstreamError。
            # 上游中途断流抛的是 `httpx.RemoteProtocolError` /
            # `httpx.ReadTimeout`，它不在 UpstreamError 的继承链上 ——
            # 旧写法会让异常逃出生成器，客户端收到的是
            # 「无 [DONE]、无错误帧」的裸截断，表现成「回复说到一半没了」，
            # 且网关日志里只有一条 traceback，看不出是流断的。
            # 补一个合法的错误帧 + [DONE]，客户端才能正常收尾。
            logger.warning("小浣熊流式转发中断：%s", type(exc).__name__)
            try:
                yield ("data: %s\n\n" % json.dumps(
                    {"error": {"message": "上游连接中断（%s）"
                                          % type(exc).__name__,
                               "type": "upstream_error"}},
                    ensure_ascii=False)).encode()
            except Exception:  # noqa: BLE001 - 客户端可能已断开
                pass
        finally:
            # 快路径：正常结束时立即清理
            cleanup()

    return StreamingResponse(
        # **空闲心跳**：上游排队/限流/慢思考时整条流可能几十秒无字节，
        # 每 8 秒一帧**空增量 data 帧**（真实事件，会被解析并重置客户端
        # idle 计时器；旧注释帧已被证明无效，可用
        # MIRAIHUB_KEEPALIVE_DATA_FRAME=0 回退）。
        bridge.with_pings(generator(), ping=bridge.CHAT_PING),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        # 兜底：客户端断开 / task 被取消时，Starlette 仍会跑 background
        background=BackgroundTask(cleanup))


def _relay_stream(upstream, holder: dict):
    """逐事件转发上游 SSE，抓 usage，必要时补 `[DONE]`。

    ## 必须同时兼容两种上游形态

    SSE 规范里一个事件是「多行 `data:` 直到空行」。但真实上游分两类：

      A. **标准形态**：每个事件以空行结尾，长 body 会拆成多行 `data:`。
      B. **精简形态**：一条 `data:` 就是一个完整 JSON，**不发空行**。

    只按 A 处理会把 B 的连续几个 chunk 拼成一条非法 payload
    （`{"a":1}{"b":2}`），`json.loads` 失败 → 整段流内容丢失；
    只按 B 处理会把 A 的多行 body 拆成多个坏帧。

    判据：读到一条 `data:` 且**累积内容本身就是一个完整 JSON**，
    说明它自成事件（形态 B），立即提交；否则继续累积等空行（形态 A）。
    这个探测只在「还没有后续行」时做猜测 —— 一旦该事件后面确实跟了
    空行，两种判据结论一致，不会歧义。
    """
    saw_done = False
    data_lines: list[str] = []
    head_lines: list[str] = []

    def emit(payload: str, head: str):
        """产出单帧。返回 True 表示是 [DONE]。"""
        if payload.strip() == "[DONE]":
            holder.setdefault("usage", holder.get("usage") or {})
            return "done"
        try:
            chunk = json.loads(payload)
        except ValueError:
            # 不是 JSON：原样透传，别吞。
            # 注意 `id:` / `event:` / `retry:` 这些 head 行在 JSON 分支也要带上，
            # 否则 `Last-Event-ID` 续传会失效（之前只在非 JSON 分支拼 head）。
            body = "".join("data: %s\n" % x for x in payload.split("\n"))
            return head + body + "\n"
        # ⚠️ 上游偶尔发 `data: "pong"` / `data: [1,2,3]` 这类非对象帧
        # （心跳、厂商扩展）。直接 `.get()` 会 AttributeError，
        # 而它不在 `except UpstreamError` 覆盖范围内 —— 整个流被截断。
        if isinstance(chunk, dict):
            usage = chunk.get("usage")
            if isinstance(usage, dict) and usage:
                holder["usage"] = usage
        return head + "data: %s\n\n" % json.dumps(chunk, ensure_ascii=False)

    def commit():
        if not data_lines:
            return None
        return emit("\n".join(data_lines), "".join(head_lines))

    for raw in upstream.iter_lines():
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        line = line.rstrip("\r\n")

        # 空行 = 事件边界（形态 A）
        if not line:
            out = commit()
            data_lines, head_lines = [], []
            if out is None:
                continue
            if out == "done":
                saw_done = True
                yield b"data: [DONE]\n\n"
                continue
            yield out.encode()
            continue

        if line.startswith("data:"):
            piece = line[5:].lstrip()
            data_lines.append(piece)
            # 形态 B 探测：单行即完整 **对象** 时立刻提交。
            #
            # ⚠️ 判据必须是「以 `{` 开头且解析成 dict」，不能是「能 json.loads」。
            # 实测漏洞：形态 A 的多行事件写成
            #     data: 123
            #     data: 456
            #     data: 789
            # （想表达一个数组）时，`123` 本身是合法 JSON → 被当成形态 B
            # 提前提交，整个事件被拆成 3 帧。
            # 真实上游 chunk 都是闭合对象，加 `{` 前缀判断即可排除标量/数组；
            # 再要求结果是 dict，避免 `[1,2,3]` 这种扩展帧被误判。
            if len(data_lines) == 1 and piece.startswith("{"):
                try:
                    if not isinstance(json.loads(piece), dict):
                        raise ValueError("not a dict")
                except ValueError:
                    pass
                else:
                    out = commit()
                    data_lines, head_lines = [], []
                    if out == "done":
                        saw_done = True
                        yield b"data: [DONE]\n\n"
                    elif out is not None:
                        yield out.encode()
        elif line.startswith(":"):
            # 注释/心跳：直接透传，不参与事件聚合
            yield (line + "\n").encode()
        else:
            head_lines.append(line + "\n")

    # 上游没给结尾空行时，把最后一个事件补出去
    out = commit()
    if out is not None:
        if out == "done":
            saw_done = True
            yield b"data: [DONE]\n\n"
        else:
            yield out.encode()

    if not saw_done:
        yield b"data: [DONE]\n\n"


async def messages(request):
    """Anthropic 协议（**转换整段收敛到 protocol_bridge**）。

    请求方向 / 非流式响应方向原本就是完整的；本次把**流式方向**换成
    `bridge.anthropic_stream_relay` —— 旧中继把 `delta.tool_calls`
    静默丢掉、stop_reason 恒 `end_turn`、思考链混进正文、上游错误帧
    被吞。桥里全部修正：tool_use 块 + input_json_delta + thinking 块
    + 空闲心跳 + error 事件。
    """
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
    return bridge.anthropic_stream_relay(inner, model, prefix="raccoon")


async def count_tokens(request):
    """本地估算（上游没有这个接口）。"""
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
    """OpenAI Responses 协议（**转换整段收敛到 protocol_bridge**）。

    旧实现不发 function_call 输出项、流式没有
    `response.function_call_arguments.delta`、`length` 截断被报成
    `completed`。桥里全部修正。
    """
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
    return bridge.responses_stream_relay(inner, model, prefix="raccoon")


async def list_models(request):
    """/v1/models —— 返回**全部可调**模型；无号时回落数据库目录。

    ## 为什么不过滤 `visible`

    实测（2026-11-03）`raccoon-090dd3` 等 3 个模型的 `visible` 是 `false`，
    但它们**能正常对话**（`/v1/chat/completions` 返回 200 + 真实回复）。
    `visible` 是客户端 UI 的「要不要放进下拉框」，不是「能不能调」。
    按它过滤会把主力模型藏起来 —— 第一版就是这么错的，`/v1/models`
    返回空列表，而对话却是通的。

    所以：全部返回，用 `visible` 标注可见性由客户端自己决定。
    """
    from admin.db import SessionLocal

    with SessionLocal() as db:
        shared.authenticate(db, request, platform=PUBLIC)
        realm = _realm(request)
        account = shared.select_account(
            db, storage=raccoon_accounts.PLATFORM, realm=realm,
            key=shared.key_row(db, request))
        if account is None:
            return JSONResponse({
                "object": "list",
                "data": shared.catalog_from_db(db, raccoon_accounts.PLATFORM),
                "note": "号池没有可用小浣熊账号，仅返回配置目录"})
        try:
            data = raccoon_accounts.ensure_token(db, account)
            models = client.fetch_models(raccoon_accounts.token_of(data), realm,
                                         account_id=int(account.id or 0))
        except client.UpstreamError as exc:
            return _fail(exc)
    payload = [{"id": m["name"], "object": "model",
                "owned_by": "raccoon",
                # `name` 是**官方展示名**：模型 id 是小浣熊的内部代号
                # （`raccoon-090dd3` / `sn-glm-5-3`），只有展示名能和别的平台
                # 聚到一起（`DeepSeek-V4.1-Flash-1017` ↔ `DeepSeek-V4.1-Flash`）。
                # 同步时它还会落进 `note`，模型配置页终于能看懂这行是什么。
                "name": m.get("display") or m["name"],
                "display_name": m.get("display") or m["name"],
                # ⚠️ 必须带上描述：模型名是代号（`raccoon-090dd3` /
                # `sn-glm-5-3`），没有描述用户在模型配置里根本看不出
                # 它是什么、该不该开。上游 `/model_catalog` 的
                # `display_description` 正是那条文案，漏传就等于丢掉了
                # 唯一能解释模型用途的信息。
                "description": m.get("description") or "",
                # 计费倍率：`billing_effective_multiplier` 是**实扣**倍率
                # （限免 / 折扣之后），`billing_multiplier` 是标价。
                # 两者都透出，模型配置页才能显示「×0.25」而不是「无」，
                # 也才能解释「标价 0.5 但当前限免」。
                "billing_multiplier": m.get("billing_multiplier"),
                "billing_effective_multiplier": m.get("billing_effective_multiplier"),
                "billing_category": m.get("billing_category") or "",
                "billing_status": m.get("billing_status") or "",
                "billing_status_note": m.get("billing_status_note") or "",
                # 能力元数据（上游 tags / 上下文），同步后进 upstream_metadata，
                # 模型配置页的标签与 tooltip 都用得上。
                "tags": m.get("tags") or [],
                "maxInputTokens": m.get("context_window"),
                "maxOutputTokens": m.get("max_tokens"),
                "visible": bool(m.get("visible", True))}
               for m in models]
    return JSONResponse({"object": "list", "data": payload})


async def balance(request):
    """/v1/balance —— 用第一个可用账号探一次积分。"""
    from admin.db import SessionLocal

    with SessionLocal() as db:
        shared.authenticate(db, request, platform=PUBLIC)
        realm = _realm(request)
        account = shared.select_account(
            db, storage=raccoon_accounts.PLATFORM, realm=realm,
            key=shared.key_row(db, request))
        if account is None:
            return JSONResponse({"platform": PUBLIC, "realm": realm,
                                 "reachable": False, "models": 0,
                                 "note": "号池没有可用账号"}, status_code=503)
        try:
            data = raccoon_accounts.ensure_token(db, account)
            points = client.fetch_points(raccoon_accounts.token_of(data), realm,
                                         account_id=int(account.id or 0))
            models = client.fetch_models(raccoon_accounts.token_of(data), realm,
                                         account_id=int(account.id or 0))
        except client.UpstreamError as exc:
            return _fail(exc)
    return JSONResponse({"platform": PUBLIC, "realm": realm, "reachable": True,
                         "available": points.get("available"),
                         "daily": points.get("daily_balance"),
                         "models": len(models), "unit": "points"})


#: 请求体预处理后的路径表
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
            {"error": {"message": "小浣熊不支持 %s" % path,
                       "type": "invalid_request_error"}},
            status_code=404)
    try:
        return await handler(request)
    except shared.PlatformRejected as exc:
        if path in ("/v1/messages", "/v1/messages/count_tokens"):
            return exc.anthropic()
        return exc.openai()
