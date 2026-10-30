"""Loomy /v1/* 转发实现（供 dispatch.py 调用）。

## 三个协议都要实现

    /v1/chat/completions   OpenAI
    /v1/messages           Anthropic
    /v1/responses          Responses
    /v1/models
    /v1/balance

## 鉴权 / 选号 / 记账（本次修复）

改造前这里同样没有 Key 校验、没有账号绑定、不写用量日志、不占租约。
现在统一走 `admin.platform.shared`：

    authenticate()      鉴权 + 配额 + 哨兵暂停
    select_account()    平台 + 区域 + Key 归属 + 冷却 + 在途
    record_usage()      写带 platform 的 usage_logs

## 流式

Loomy 上游只有非流式接口。`stream: true` 时把完整结果**合成为 SSE 帧**
返回 —— 客户端按 SSE 解析不会因为收到一个 JSON 就崩掉。
"""
from __future__ import annotations

import json
import logging
import time

from starlette.responses import JSONResponse, StreamingResponse

from admin.platform import shared
from admin.platform.loomy.core import client
from admin.platform.loomy.services import accounts as loomy_accounts

logger = logging.getLogger("admin.platform.loomy.gateway")

#: 拦截路径（dispatch 据此判断要不要转交）
PATHS = (
    "/v1/chat/completions",
    "/v1/messages",
    "/v1/messages/count_tokens",
    "/v1/responses",
    "/v1/models",
    "/v1/balance",
)

PUBLIC = "loomy"
MAX_ATTEMPTS = 3


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
    # **把上游原始错误完整记下来**：Loomy 的 400 只回「请求失败（HTTP 400）」，
    # 看不出到底哪里不合法。detail 里是上游原文，必须落日志才排得动。
    try:
        logger.warning("Loomy 上游失败 kind=%s status=%s msg=%s detail=%s",
                       getattr(exc, "kind", ""), getattr(exc, "status", ""),
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


#: 上游只认这三个角色。别的角色进来后**不能原样透传**。
_UPSTREAM_ROLES = frozenset({"system", "user", "assistant"})


def normalize_messages(raw) -> list[dict]:
    """把 messages 归一成上游能接受的样子。

    ## 为什么必须做

    上游（Loomy）只认 `system` / `user` / `assistant` 三种角色。
    `developer` 是 OpenAI 较新的角色，DSH 这类 harness 在**开推理时**
    会用它承载系统提示（还把整段 system prompt 塞进去）。

    原样透传的后果**不是报错，而是流式永久卡死**：
    上游静默丢弃这条消息、仍然回 200、但从不发 `finish_reason`，
    客户端就一直等。

    ## 归一规则

    * `developer` -> `system`（它本来就是系统提示的语义）
    * `tool` / `function` -> `user`（老式函数结果，上游无对应角色）
    * 未知角色 -> 丢弃
    * `system` **全部前置**（中途出现的 system 会失去指令权重）
    """
    if not isinstance(raw, list):
        return []
    systems: list[dict] = []
    others: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        # 工具结果要**带上调用标识**再转 user：
        # 只写 content 的话模型看到的是一段孤立文本，不知道它对应哪次调用，
        # 于是会重复调同一个工具（或干脆不调）。这里包一层可读标记。
        tool_hint = ""
        if role == "developer":
            role = "system"
        elif role in ("tool", "function"):
            role = "user"
            _tcid = str(item.get("tool_call_id") or item.get("name") or "").strip()
            _raw_c = item.get("content")
            _txt = _raw_c if isinstance(_raw_c, str) else ""
            if not _txt and isinstance(_raw_c, list):
                _txt = "".join(str(b.get("text") or "") for b in _raw_c
                               if isinstance(b, dict))
            tool_hint = ("[tool result%s]\n%s"
                         % (" for " + _tcid if _tcid else "", _txt))
        if role not in _UPSTREAM_ROLES:
            continue
        content = tool_hint or item.get("content")
        if isinstance(content, list):
            # 兼容 parts 数组：只取文本块
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text") or ""))
                elif isinstance(block, str):
                    parts.append(block)
            content = "".join(parts)
        if content is None:
            continue
        # assistant 发起的工具调用：上游不认 `tool_calls` 字段，
        # 转成可读文本，让模型在下一轮知道自己刚才调了什么。
        if role == "assistant" and isinstance(item.get("tool_calls"), list):
            _calls = []
            for _c in item["tool_calls"]:
                if not isinstance(_c, dict):
                    continue
                _fn = _c.get("function") if isinstance(_c.get("function"), dict) else {}
                _calls.append("%s(%s)" % (_fn.get("name") or "?",
                                          _fn.get("arguments") or ""))
            if _calls:
                _prev = content if isinstance(content, str) else ""
                content = ("%s\n[tool calls] %s" % (_prev, "; ".join(_calls))).strip()
        if role == "system":
            systems.append({"role": "system", "content": content})
        else:
            others.append({"role": role, "content": content})
    return systems + others


def _apply_no_think(body: dict) -> dict:
    """关掉推理链的三层保险（对非 reasoning 模型是 noop）。

    上游 reasoning 模型会**无视 max_tokens 跑满思考链**，
    导致 `finish_reason: length` 且 `content: ""` —— 看起来像「空回复」，
    其实是正文还没开始就被截断了。
    """
    out = dict(body)
    params = dict(out.get("chat_template_kwargs") or {})
    params.setdefault("enable_thinking", False)
    out["chat_template_kwargs"] = params
    out.setdefault("enable_thinking", False)

    # **`max_tokens` 必须放大**。
    #
    # Loomy 的 DeepSeek-V4.1-Flash 是**只能推理**的模型：
    # 实测 `enable_thinking:false`、`chat_template_kwargs`、`/no_think`
    # 三种关思考手段**全部无效**，它一定先跑完思考链才出正文。
    #
    # 后果：客户端给 `max_tokens: 32` 时，32 个 token 全被 reasoning 吃掉，
    # `finish_reason: length` 且 `content: ""` —— 看起来像「模型不回复」。
    # 实测 max_tokens=200 就正常出正文了。
    #
    # 所以给一个**思考预算下限**：客户端要得少也补到 512，
    # 但不超过 8192（避免要长文时被额外收费）。
    # **`max_tokens` 必须按输入长度动态放大**。
    #
    # Loomy 的 DeepSeek-V4.1 只能推理，先跑完思考链才出正文。实测：
    #   短提示 + max_tokens=100  -> 正文 13 字（512 下限兜住了）
    #   长提示 + max_tokens=512  -> finish_reason=length，**正文 0 字**
    #     （512 token 全被思考吃掉，usage.reasoning_tokens=512）
    #
    # 所以下限要跟输入规模挂钩：输入越长、思考越久。这里按「输入字符数的
    # 2 倍 + 512」估算思考预算，并给足正文空间。
    try:
        want = int(out.get("max_tokens") or 0)
    except (TypeError, ValueError):
        want = 0
    # 输入规模（字符数，中文按 1 算，够用）
    _chars = 0
    for _m in out.get("messages") or []:
        _c = _m.get("content")
        if isinstance(_c, str):
            _chars += len(_c)
        elif isinstance(_c, list):
            for _b in _c:
                if isinstance(_b, dict):
                    _chars += len(str(_b.get("text") or ""))
    # 思考预算：**Loomy 的思考链会吃掉几千到上万 token**。
    #
    # 实测同一段提示词：
    #   max_tokens=4000   -> finish=length，正文 0 字（全被思考吃光）
    #   max_tokens=8000   -> finish=length，正文 6402 字（还是被截）
    #   max_tokens=16000  -> finish=stop，  正文 9069 字  ✅
    #
    # 所以下限必须够大：**给 16384**（多数上游的单次输出上限），
    # 不能只给 4096 —— 那等于把慢思考模型永久截断，表现是「回复到一半停住」
    # 或「正文 0 字」，用户很难判断是模型问题还是网关问题。
    # 客户端显式要得更多时，放宽到 32768。
    _floor = 16384 + min(8192, _chars * 2)
    if want < _floor:
        out["max_tokens"] = _floor
    elif want > 32768:
        out["max_tokens"] = 32768

    # `/no_think` 前缀对 Loomy **无效**（实测它会把这三个字当正文念出来），
    # 所以这里不再拼接，避免污染用户输入。
    messages = normalize_messages(out.get("messages") or [])
    for index in range(len(messages) - 1, -1, -1):
        item = messages[index]
        if not isinstance(item, dict) or item.get("role") != "user":
            continue
        content = item.get("content")
        if isinstance(content, str) and not content.lstrip().startswith("/no_think"):
            messages[index] = {**item, "content": "/no_think " + content}
        break
    out["messages"] = messages
    return out


def _attempt(db, request, key, body: dict, realm: str):
    """选号 + 租约 + 调上游（换号重试）。"""
    model = str(body.get("model") or "")
    exclude: set[int] = set()
    last_error: client.UpstreamError | None = None
    for _ in range(MAX_ATTEMPTS):
        account = shared.select_account(
            db, storage=loomy_accounts.PLATFORM, realm=realm, key=key,
            model=model, exclude_ids=exclude)
        if account is None:
            break
        if not shared.lease(account):
            exclude.add(account.id)
            continue
        started = time.perf_counter()
        try:
            data = loomy_accounts.ensure_token(db, account)
            result = client.chat_completions(
                loomy_accounts.token_of(data), realm, _apply_no_think(body),
                account_id=int(account.id or 0))
            return result, account, started
        except client.UpstreamError as exc:
            shared.release(account)
            shared.note_failure(
                db, account, kind=exc.kind, detail=exc.detail or exc.message,
                status=exc.status, model=model, public_platform=PUBLIC,
                cool_seconds=(600 if exc.kind in ("auth", "quota_exceeded") else 0))
            last_error = exc
            if not exc.retryable():
                break
            # **400「请重试」类错误不要立刻换号**：DashScope 的瞬时 400 与
            # 账号无关，换号也一样失败，反而把唯一可用账号排除掉。
            # 先短暂退避、同账号再试一次（上游通常几百毫秒就恢复）。
            if exc.status == 400:
                time.sleep(0.8)
                continue
            exclude.add(account.id)
        except Exception as exc:  # noqa: BLE001
            shared.release(account)
            logger.exception("Loomy 调用失败 acc=%s", account.id)
            last_error = client.UpstreamError(str(exc), kind="network")
            exclude.add(account.id)
            continue
    if last_error is not None:
        raise last_error
    raise shared.PlatformRejected(
        503, "Loomy 没有可用账号（请先在控制台导入并确认状态为启用）",
        kind="server_error", code="no_available_account",
        headers={"Retry-After": "3"})


def _bill(key, account, model, result, *, request, started=None, error="",
          stats: dict | None = None):
    try:
        from admin.db import SessionLocal

        usage = (result or {}).get("usage") or {}
        elapsed = int((time.perf_counter() - started) * 1000) if started else None
        # **分段耗时**：`client._request` 在 `urlopen` 返回处记了首字节时间，
        # 那里才是真正「开始有响应」的时刻。之前把 ttfb 和 latency 都写成
        # 总耗时，`latency - ttfb = 0`，TPS 只能退回整段计算 —— 于是
        # 真实 214 tok/s 被算成 44 tok/s（差一个量级）。
        # **优先用调用方显式传入的 stats**：`_bill` 在流结束后的另一个
        # 协程里跑，contextvar 在这里读不到生成器里设的值（这正是之前
        # `ttfb == latency`、TPS 永远算不出来的原因）。
        ttfb = total = 0
        if isinstance(stats, dict):
            ttfb = int(stats.get("ttfb_ms") or 0)
            total = int(stats.get("total_ms") or 0)
        if not ttfb and not total:
            try:
                ttfb = int(client.LAST_TTFB_MS.get() or 0)
                total = int(client.LAST_TOTAL_MS.get() or 0)
            except Exception:  # noqa: BLE001
                ttfb = total = 0
        if not (0 < ttfb <= (total or elapsed or 0)):
            ttfb = 0
        latency = max(total, elapsed or 0) or None
        # 改成**真流式**后，`ttfb`（首帧到达）与 `latency`（最后一帧）
        # 天然有区分度，`latency - ttfb` 就是真实的纯解码耗时，
        # TPS 可以直接算。不再需要「只记 ttfb」那种回避做法。
        if not ttfb:
            ttfb = latency
        with SessionLocal() as db:
            shared.record_usage(
                db,
                key_id=int(getattr(key, "id", 0) or 0),
                account_id=int(getattr(account, "id", 0) or 0),
                model=model or str((result or {}).get("model") or ""),
                usage=usage, public_platform=PUBLIC,
                storage=loomy_accounts.PLATFORM,
                error_kind=error, client_ip=_client_ip(request),
                use_case="loomy-chat", seq=1,
                ttfb_ms=ttfb, latency_ms=latency)
    except Exception:  # noqa: BLE001
        logger.exception("Loomy 记账失败")


async def _forward(body: dict, request, *, usage=None) -> JSONResponse:
    """鉴权 -> 选号 -> 上游 -> 记账。"""
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


def _public(request) -> str:
    return shared.public_platform_of(request, PUBLIC)


async def chat_completions(request):
    """对话入口：**流式请求走真流式转发**。

    ## 历史坑（已修）

    这里原来是「伪流式」：先发非流式请求拿到**完整结果**，再切块合成 SSE。
    注释写的理由是「上游只有非流式」，但那是错的 —— 实测上游 `stream: true`
    返回 `text/event-stream`，首帧 1.6 秒就到。

    伪流式的代价：
      · 客户端先干等几十秒（等于把 TTFT 变成整个生成时间），
        再一瞬间收完整篇 —— 完全不像是流式，长任务还会撞网关总时限；
      · `ttfb` 与 `latency` 天然相等，TPS 算不出来（只能记「—」）；
      · 观感是「卡住了」，实际是上游早就开始吐字了。

    现在：`client.chat_stream` 逐帧读上游、`_sse_from_chunks` 逐帧转协议、
    `StreamingResponse` 逐帧下发 —— 与直连上游的时序基本一致。
    """
    body = await _body_of(request)
    if not body.get("stream"):
        return await _forward(body, request)
    return await _forward_stream(body, request)


async def _forward_stream(body: dict, request):
    """真流式：选号 -> 上游 SSE -> 逐帧转发 -> 收尾记账。"""
    from admin.db import SessionLocal

    realm = _realm(request)
    db = SessionLocal()
    key = shared.authenticate(db, request, platform=_public(request))
    model = str(body.get("model") or "")
    # 分段耗时容器：**必须在调用 client.chat_stream 之前定义**。
    # 它由上游流式读取器填 ttfb/total，再交给 _bill 记账。
    stats: dict = {}
    exclude: set[int] = set()
    account = None
    chunks = None
    last_error = None
    started = time.perf_counter()
    for _ in range(MAX_ATTEMPTS):
        account = shared.select_account(
            db, storage=loomy_accounts.PLATFORM, realm=realm, key=key,
            model=model, exclude_ids=exclude)
        if account is None:
            break
        if not shared.lease(account):
            exclude.add(account.id)
            continue
        try:
            data = loomy_accounts.ensure_token(db, account)
            chunks = client.chat_stream(
                loomy_accounts.token_of(data), realm, _apply_no_think(body),
                account_id=int(account.id or 0), stats=stats)
            break
        except client.UpstreamError as exc:
            shared.release(account)
            shared.note_failure(
                db, account, kind=exc.kind, detail=exc.detail or exc.message,
                status=exc.status, model=model, public_platform=PUBLIC,
                cool_seconds=(600 if exc.kind in ("auth", "quota_exceeded") else 0))
            last_error = exc
            exclude.add(account.id)
            if not exc.retryable():
                break
        except Exception as exc:  # noqa: BLE001
            shared.release(account)
            logger.exception("Loomy 流式调用失败 acc=%s", account.id)
            last_error = client.UpstreamError(str(exc), kind="network")
            exclude.add(account.id)

    if chunks is None:
        db.close()
        if last_error is not None:
            return _fail(last_error)
        raise shared.PlatformRejected(
            503, "Loomy 没有可用账号（请先在控制台导入并确认状态为启用）",
            kind="server_error", code="no_available_account",
            headers={"Retry-After": "3"})

    holder: dict = {}

    def generator():
        try:
            yield from _sse_from_chunks(chunks, model, holder)
        except client.UpstreamError as exc:
            yield ("data: %s\n\n" % json.dumps(
                {"error": {"message": exc.message, "type": exc.kind}},
                ensure_ascii=False)).encode()
        finally:
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
        generator(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _sse_from_chunks(chunks, model: str, holder: dict):
    """把上游 chunk 逐帧转成 OpenAI SSE 并产出**字节**。

    `holder['usage']` 收集最后一帧的 usage（含积分），供收尾记账与
    「客户端显示消耗积分」共用。
    """
    chunk_id = "chatcmpl-loomy"
    created = int(time.time())
    saw_reasoning = False

    def frame(delta: dict, finish=None, usage: dict | None = None) -> bytes:
        payload = {
            "id": chunk_id, "object": "chat.completion.chunk",
            "created": created, "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        if usage is not None:
            payload["usage"] = usage
            payload["choices"] = []
        return ("data: %s\n\n" % json.dumps(payload, ensure_ascii=False)).encode()

    usage_acc: dict = {}
    for chunk in chunks:
        if isinstance(chunk.get("usage"), dict) and chunk["usage"]:
            usage_acc.update(chunk["usage"])
        for choice in (chunk.get("choices") or []):
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta") or {}
            out: dict = {}
            if delta.get("reasoning_content"):
                out["reasoning_content"] = delta["reasoning_content"]
                saw_reasoning = True
            if delta.get("content"):
                out["content"] = delta["content"]
            if delta.get("tool_calls"):
                out["tool_calls"] = delta["tool_calls"]
            if out:
                yield frame(out)
            if choice.get("finish_reason"):
                yield frame({}, str(choice["finish_reason"]))

    # 收尾：带上整合好的 usage + 积分（客户端底部显示消耗要用）
    final_usage = _usage_with_credit(usage_acc, model)
    holder["usage"] = final_usage
    yield frame({}, None, usage=final_usage)
    yield b"data: [DONE]\n\n"


def _usage_with_credit(usage: dict, model: str) -> dict:
    """usage 里补齐 `credit` / `credits` / `cost` 三个同义键。"""
    out = {
        "prompt_tokens": int((usage or {}).get("prompt_tokens") or 0),
        "completion_tokens": int((usage or {}).get("completion_tokens") or 0),
        "total_tokens": int((usage or {}).get("total_tokens") or 0),
    }
    credit = 0.0
    try:
        raw = (usage or {}).get("credit")
        credit = float(raw) if isinstance(raw, (int, float)) else 0.0
    except (TypeError, ValueError):
        credit = 0.0
    if credit <= 0:
        try:
            from admin.db import SessionLocal as _SL
            from admin.platform.shared import credits_for as _cf

            toks = out["total_tokens"] or out["completion_tokens"]
            with _SL() as _db:
                credit = _cf(_db, loomy_accounts.PLATFORM, model, toks)
        except Exception:  # noqa: BLE001
            credit = 0.0
    if credit:
        out.update({"credit": credit, "credits": credit, "cost": credit})
    return out


def _sse_from_chat(result: dict):
    chunk_id = str(result.get("id") or "chatcmpl-loomy")
    model = str(result.get("model") or "")
    created = int(result.get("created") or time.time())
    choice = (result.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    text = str(message.get("content") or "")
    reasoning = str(message.get("reasoning_content") or "")

    # 终点帧要带 usage：客户端底部显示「消耗 N 积分」读的就是它，
    # 不带的话 Loomy 的对话永远显示不出消耗。
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    credit = 0.0
    try:
        credit = float(usage.get("credit") or usage.get("credits") or 0)
    except (TypeError, ValueError):
        credit = 0.0
    if credit <= 0:
        # 上游没算积分时按倍率补一个（`_bill` 里已算过同样的值）
        try:
            from admin.db import SessionLocal
            from admin.platform.shared import credits_for

            toks = (usage.get("total_tokens")
                    or usage.get("completion_tokens") or 0)
            with SessionLocal() as _db:
                credit = credits_for(_db, loomy_accounts.PLATFORM, model, toks)
        except Exception:  # noqa: BLE001
            credit = 0.0

    def frame(delta: dict, finish=None, with_usage: bool = False):
        payload = {
            "id": chunk_id, "object": "chat.completion.chunk",
            "created": created, "model": model,
            "choices": [{"index": 0, "delta": delta,
                         "finish_reason": finish}]}
        if with_usage:
            payload["usage"] = {
                "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                "completion_tokens": int(usage.get("completion_tokens") or 0),
                "total_tokens": int(usage.get("total_tokens") or 0),
                "credit": credit, "credits": credit, "cost": credit,
            }
        return ("data: %s\n\n" % json.dumps(payload, ensure_ascii=False)).encode()

    yield frame({"role": "assistant"})
    if reasoning:
        yield frame({"reasoning_content": reasoning})
    # **tool_calls 必须转发**：harness 客户端靠它决定下一步做什么。
    # 原来完全不处理，客户端只收到 `finish_reason=tool_calls`，
    # 于是「思考完就结束、任务没开始跑」。
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list) and tool_calls:
        yield frame({"tool_calls": tool_calls})
    if text:
        yield frame({"content": text})
    yield frame({}, str(choice.get("finish_reason") or "stop"))
    # **始终补一个 usage 终点帧**（带积分），客户端据此显示「消耗 N 积分」。
    # 原来只在 `result["usage"]` 非空时发，且原样透传 —— Loomy 的 usage 里
    # 没有 credit，客户端就永远显示不出消耗。
    yield frame({}, None, with_usage=True)
    yield b"data: [DONE]\n\n"


def _anthropic_to_openai(body: dict) -> dict:
    """Anthropic 请求体 → OpenAI 请求体。"""
    messages = []
    system = body.get("system")
    if isinstance(system, str) and system.strip():
        messages.append({"role": "system", "content": system})
    elif isinstance(system, list):
        text = "".join(str(b.get("text") or "") for b in system
                       if isinstance(b, dict))
        if text.strip():
            messages.append({"role": "system", "content": text})

    for item in body.get("messages") or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        content = item.get("content")
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text") or ""))
                elif isinstance(block, str):
                    parts.append(block)
            content = "".join(parts)
        # 角色归一统一在 `normalize_messages`（`_apply_no_think` 里调用），
        # 这里只把 Anthropic 的结构拆成 OpenAI 的样子。
        if role and content is not None:
            messages.append({"role": role, "content": content})

    out = {"model": body.get("model"),
           "messages": messages,
           "max_tokens": body.get("max_tokens") or 4096}
    for key in ("temperature", "top_p", "stream"):
        if key in body:
            out[key] = body[key]
    return out


def _openai_to_anthropic(result: dict, model: str) -> dict:
    """OpenAI 响应 → Anthropic message 结构。"""
    choice = (result.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    text = str(message.get("content") or "")
    usage = result.get("usage") or {}
    stop = {"length": "max_tokens", "tool_calls": "tool_use"}.get(
        str(choice.get("finish_reason") or ""), "end_turn")
    return {
        "id": "msg_" + str(result.get("id") or "")[:24],
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": text}] if text else [],
        "stop_reason": stop,
        "usage": {
            "input_tokens": int(usage.get("prompt_tokens") or 0),
            "output_tokens": int(usage.get("completion_tokens") or 0),
        },
    }


async def messages(request):
    """Anthropic 协议。**流式请求真流式转发**，非流式才聚合。"""
    body = await _body_of(request)
    normalized = _anthropic_to_openai(body)
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
        return JSONResponse(_openai_to_anthropic(payload, model))
    # 流式：上游逐帧 -> 转 Anthropic 事件 -> 立即下发
    return await _forward_stream_anthropic(normalized, request, model)


async def _forward_stream_anthropic(body: dict, request, model: str):
    """把 Loomy 的上游流翻成 Anthropic `content_block_delta` 事件流。"""
    inner = await _forward_stream(body, request)
    if getattr(inner, "status_code", 200) >= 400:
        return inner

    async def relay():
        msg_id = "msg_loomy_" + str(int(time.time() * 1000))
        started = False
        async for frame in inner.body_iterator:
            text = frame.decode("utf-8", "replace") if isinstance(frame, bytes) else str(frame)
            for line in text.split("\n"):
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload in ("", "[DONE]"):
                    continue
                try:
                    chunk = json.loads(payload)
                except ValueError:
                    continue
                choice = (chunk.get("choices") or [{}])[0]
                delta = choice.get("delta") or {}
                piece = delta.get("content") or delta.get("reasoning_content") or ""
                if not piece:
                    if choice.get("finish_reason") and started:
                        yield ("data: %s\n\n" % json.dumps(
                            {"type": "content_block_stop", "index": 0},
                            ensure_ascii=False)).encode()
                        yield ("data: %s\n\n" % json.dumps(
                            {"type": "message_delta",
                             "delta": {"stop_reason": "end_turn"},
                             "usage": {"output_tokens": 0}},
                            ensure_ascii=False)).encode()
                        yield ("data: %s\n\n" % json.dumps(
                            {"type": "message_stop"},
                            ensure_ascii=False)).encode()
                    continue
                if not started:
                    started = True
                    yield ("data: %s\n\n" % json.dumps(
                        {"type": "message_start",
                         "message": {"id": msg_id, "type": "message",
                                     "role": "assistant", "model": model,
                                     "content": [], "stop_reason": None,
                                     "usage": {"input_tokens": 0,
                                               "output_tokens": 0}}},
                        ensure_ascii=False)).encode()
                    yield ("data: %s\n\n" % json.dumps(
                        {"type": "content_block_start", "index": 0,
                         "content_block": {"type": "text", "text": ""}},
                        ensure_ascii=False)).encode()
                yield ("data: %s\n\n" % json.dumps(
                    {"type": "content_block_delta", "index": 0,
                     "delta": {"type": "text_delta", "text": piece}},
                    ensure_ascii=False)).encode()

    return StreamingResponse(
        relay(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


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


def _openai_to_responses(result: dict, model: str) -> dict:
    """OpenAI chat.completion → Responses 结构。"""
    choice = (result.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    text = str(message.get("content") or "")
    usage = result.get("usage") or {}
    output = []
    if text:
        output.append({
            "type": "message",
            "id": "msg_" + str(result.get("id") or "")[:20],
            "role": "assistant",
            "content": [{"type": "output_text", "text": text}],
        })
    reasoning = str(message.get("reasoning_content") or "")
    return {
        "id": "resp_" + str(result.get("id") or "")[:20],
        "object": "response",
        "created_at": int(result.get("created") or 0),
        "model": model or result.get("model") or "",
        "status": "completed" if choice.get("finish_reason") == "stop" else "incomplete",
        "output": output,
        "output_text": text,
        "reasoning_summary": reasoning[:2000] if reasoning else "",
        "usage": {
            "input_tokens": int(usage.get("prompt_tokens") or 0),
            "output_tokens": int(usage.get("completion_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
        },
    }


async def responses(request):
    """OpenAI Responses 协议。**流式请求真流式转发**。"""
    body = await _body_of(request)
    normalized = dict(body)
    raw_input = body.get("input")
    if isinstance(raw_input, str):
        normalized["messages"] = [{"role": "user", "content": raw_input}]
    elif isinstance(raw_input, list):
        normalized["messages"] = raw_input
    normalized.setdefault("max_tokens", body.get("max_output_tokens") or 4096)
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
        return JSONResponse(_openai_to_responses(payload, model))
    return await _forward_stream_responses(normalized, request, model)


async def _forward_stream_responses(body: dict, request, model: str):
    """把上游流翻成 Responses 事件流（`response.output_text.delta`）。"""
    inner = await _forward_stream(body, request)
    if getattr(inner, "status_code", 200) >= 400:
        return inner

    async def relay():
        resp_id = "resp_loomy_" + str(int(time.time() * 1000))
        started = False
        async for frame in inner.body_iterator:
            text = frame.decode("utf-8", "replace") if isinstance(frame, bytes) else str(frame)
            for line in text.split("\n"):
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload in ("", "[DONE]"):
                    continue
                try:
                    chunk = json.loads(payload)
                except ValueError:
                    continue
                choice = (chunk.get("choices") or [{}])[0]
                delta = choice.get("delta") or {}
                piece = delta.get("content") or delta.get("reasoning_content") or ""
                if not piece:
                    if choice.get("finish_reason") and started:
                        yield ("data: %s\n\n" % json.dumps(
                            {"type": "response.completed",
                             "response": {"id": resp_id, "object": "response",
                                          "status": "completed", "model": model}},
                            ensure_ascii=False)).encode()
                    continue
                if not started:
                    started = True
                    yield ("data: %s\n\n" % json.dumps(
                        {"type": "response.created",
                         "response": {"id": resp_id, "object": "response",
                                      "status": "in_progress", "model": model,
                                      "output": []}},
                        ensure_ascii=False)).encode()
                yield ("data: %s\n\n" % json.dumps(
                    {"type": "response.output_text.delta", "item_id": resp_id,
                     "output_index": 0, "content_index": 0, "delta": piece},
                    ensure_ascii=False)).encode()

    return StreamingResponse(
        relay(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def list_models(request):
    """/v1/models —— 只返回能调的模型；无号时回落数据库目录。"""
    from admin.db import SessionLocal

    with SessionLocal() as db:
        shared.authenticate(db, request, platform=PUBLIC)
        realm = _realm(request)
        account = shared.select_account(
            db, storage=loomy_accounts.PLATFORM, realm=realm,
            key=shared.key_row(db, request))
        if account is None:
            return JSONResponse({
                "object": "list", "data": shared.catalog_from_db(
                    db, loomy_accounts.PLATFORM),
                "note": "号池没有可用 Loomy 账号，仅返回配置目录"})
        try:
            data = loomy_accounts.ensure_token(db, account)
            models = client.fetch_models(loomy_accounts.token_of(data), realm,
                                         account_id=int(account.id or 0))
        except client.UpstreamError as exc:
            return _fail(exc)
    return JSONResponse({"object": "list", "data": models})


async def balance(request):
    from admin.db import SessionLocal

    with SessionLocal() as db:
        shared.authenticate(db, request, platform=PUBLIC)
        realm = _realm(request)
        account = shared.select_account(
            db, storage=loomy_accounts.PLATFORM, realm=realm,
            key=shared.key_row(db, request))
        if account is None:
            return JSONResponse({"platform": PUBLIC, "realm": realm,
                                 "reachable": False, "models": 0,
                                 "note": "号池没有可用账号"}, status_code=503)
        try:
            data = loomy_accounts.ensure_token(db, account)
            quota = client.fetch_quota(loomy_accounts.token_of(data), realm,
                                       account_id=int(account.id or 0))
        except client.UpstreamError as exc:
            return _fail(exc)
    return JSONResponse({"platform": PUBLIC, "realm": realm, **quota})


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
            {"error": {"message": "Loomy 不支持 %s" % path,
                        "type": "invalid_request_error"}},
            status_code=404)
    try:
        return await handler(request)
    except shared.PlatformRejected as exc:
        if path in ("/v1/messages", "/v1/messages/count_tokens"):
            return exc.anthropic()
        return exc.openai()
