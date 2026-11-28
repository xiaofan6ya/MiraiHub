"""Qoder 对话转发：三个下游协议 → 上游信封 → 下游帧。

## 协议对照

| 下游 | 入参 | 出帧 |
|---|---|---|
| `/v1/chat/completions` | `{model, messages, stream, ...}` | OpenAI chunk |
| `/v1/messages` | Anthropic `{model, messages, system, max_tokens}` | Anthropic event |
| `/v1/responses` | `{model, input}` | Responses event |

## 上游是 OpenAI 兼容的（`format: openai`）

模型目录里 10 个 chat 模型的 `format` 全是 `openai`，
所以 OpenAI 那条路可以近乎直通，Anthropic / Responses 需要事件名映射。

## 两个必须在流里处理的点

1. **业务错误在信封里**（`statusCodeValue`），HTTP 永远 200 ——
   不检查就会把「额度耗尽」渲染成「空回复」
2. **`content` 与 `reasoning_content` 分开** ——
   思考内容要按下游协议决定放哪，不能直接拼进正文
"""
from __future__ import annotations

import json
import logging
import time
import uuid

from admin.platform.qoder.core import client, endpoints, protocol
from admin.platform.qoder.services import accounts as qoder_accounts

logger = logging.getLogger("admin.platform.qoder.chat")

#: 上游帧里表示「思考中」的字段
REASONING_KEY = "reasoning_content"


def pick_model(catalog: dict, model_name: str, scene: str = "chat") -> dict | None:
    """按名字找模型目录条目。

    匹配顺序：`key` 精确 → `display_name` 精确 → `key`/名字不区分大小写。
    **不接受模糊前缀匹配** —— 静默降级到别的模型比报错更糟。
    """
    items = catalog.get(scene) or []
    if not model_name:
        # 没指定就取默认（`is_default`），再退第一个
        for item in items:
            if isinstance(item, dict) and item.get("is_default"):
                return item
        return items[0] if items and isinstance(items[0], dict) else None

    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("key") or "") == model_name:
            return item
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("display_name") or "") == model_name:
            return item
    lowered = model_name.lower()
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("key") or "").lower() == lowered:
            return item
        if str(item.get("display_name") or "").lower() == lowered:
            return item
    return None


def model_list(catalog: dict, scene: str = "chat") -> list[dict]:
    """目录 → OpenAI `/v1/models` 形状。"""
    out = []
    for item in catalog.get(scene) or []:
        if not isinstance(item, dict):
            continue
        if item.get("enable") is False:
            continue
        out.append({
            "id": str(item.get("key") or ""),
            "object": "model",
            "created": 0,
            "owned_by": "qoder",
            "name": str(item.get("display_name") or item.get("key") or ""),
            "x_qoder": {
                "display_name": item.get("display_name"),
                "format": item.get("format"),
                "is_reasoning": bool(item.get("is_reasoning")),
                "is_vl": bool(item.get("is_vl")),
                "max_input_tokens": item.get("max_input_tokens"),
                "price_factor": item.get("price_factor"),
                "is_free": bool(item.get("is_free")),
                # 能力字段：`describe_model` 靠这些拼描述
                "context_config": item.get("context_config") or {},
                "is_default": bool(item.get("is_default")),
                "is_sensitive": bool(item.get("is_sensitive")),
            },
        })
    return out


def _thinking_choice(body: dict) -> bool | None:
    """从下游请求里读「要不要思考」。

    两种写法都认：
      * OpenAI 风格 `reasoning_effort`（"none"/"minimal" = 关）
      * Anthropic 风格 `thinking: {type: "enabled"|"disabled"}`
    """
    thinking = body.get("thinking")
    if isinstance(thinking, dict):
        kind = str(thinking.get("type") or "").lower()
        if kind == "enabled":
            return True
        if kind == "disabled":
            return False
    effort = str(body.get("reasoning_effort") or "").lower()
    if effort:
        return effort not in ("none", "minimal", "off")
    return None


def _effort_of(body: dict) -> str:
    """下游档位 -> Qoder 上游认的三档（low/medium/high）。

    Qoder 的 `parameters.reasoning_effort` 只有三档，原来只透传 low/medium/high，
    于是客户端发 `xhigh` / `max` / `minimal` 会被**静默丢弃** ——
    表现是「档位设了也没变化」，而用量日志里又记着 xhigh，看起来像上游不认。
    这里做等价映射：更高档收敛到 high，minimal 收敛到 low。日志仍记**原始值**
    （`shared.reasoning_effort_of` 读的是下游 body），所以不会失真。
    """
    effort = str(body.get("reasoning_effort") or "").lower()
    effort = {"minimal": "low", "xhigh": "high", "max": "high",
              "ultra": "high"}.get(effort, effort)
    if effort in ("low", "medium", "high"):
        return effort
    thinking = body.get("thinking")
    if isinstance(thinking, dict) and thinking.get("budget_tokens"):
        try:
            budget = int(thinking["budget_tokens"])
        except (TypeError, ValueError):
            return ""
        if budget <= 2048:
            return "low"
        if budget <= 8192:
            return "medium"
        return "high"
    return ""


def stable_session_seed(account, body: dict) -> str:
    """上游 `session_id` 的种子：按「账号 + 对话」确定性派生。

    原先直接拿 `body["user"] / body["session_id"]`，而绝大多数客户端两个都
    不传 → `protocol.build_upstream_body` 走 `uuid4()` 分支，**每请求新建一个
    上游会话**：上游的上下文缓存永远命中不了，一个号在几分钟里就"新建"了
    几十个会话（风控视角的脚本特征）。

    现在：显式会话标识（`session_id` / `conversation_id` / `user`）仍被尊重，
    但会和账号绑在一起哈希；没有标识时用首条 user 文本派生，
    连消息都没有才退化成「每账号一个固定会话」。**任何一层都不是随机值。**
    """
    merged = body
    if isinstance(body, dict):
        explicit = (body.get("session_id") or body.get("conversation_id")
                    or body.get("user"))
        if isinstance(explicit, str) and explicit.strip():
            merged = dict(body)
            merged.setdefault("conversation_id", explicit.strip())
    try:
        from admin import pool

        return pool.stable_session_id(
            "qoder", int(getattr(account, "id", 0) or 0), merged, size=24)
    except Exception:  # noqa: BLE001 - 派生失败退回「不指定」，不能挡住调用
        logger.debug("qoder session seed 派生失败", exc_info=True)
        return ""


def prepare(db, account, body: dict, *, scene: str = "chat",
            catalog: dict | None = None) -> dict:
    """准备一次对话：拿 token、找模型、组信封。

    2026-11-07 起双协议并存，**主走 model server 新链路**（plan["mode"]
    ="model_server"）：标准 OpenAI 形态 + Bearer 作业令牌，不依赖账号的
    agent flow 配置（旧 agent_chat 对未初始化账号一律 400 agent_router，
    实测连官方客户端能聊的号也被拒）。旧信封组包保留（plan["upstream"]），
    便于回退与对照。
    """
    data = qoder_accounts.ensure_token(db, account)
    token = qoder_accounts.token_of(data)
    realm = qoder_accounts.realm_of(account)
    uid = str(data.get("uid") or "")
    machine_id = qoder_accounts.machine_id_of(data)

    if catalog is None:
        catalog = client.fetch_model_catalog(
            token, realm, uid=uid, name=str(data.get("name") or ""),
            email=str(data.get("email") or ""), machine_id=machine_id)

    model = pick_model(catalog, str(body.get("model") or ""), scene)
    if model is None:
        raise client.UpstreamError(
            "模型不存在：%s" % body.get("model"), status=404, kind="model_unknown",
            detail=json.dumps(model_list(catalog, scene), ensure_ascii=False)[:400])

    messages = protocol.normalize_messages(body.get("messages") or [])
    if not messages:
        raise client.UpstreamError("messages 不能为空", status=400, kind="bad_request")

    # 上游 `session_id` 是**会话**标识：必须按「账号 + 对话」稳定，否则每请求
    # 新建一个会话 → 上游上下文缓存命中不了、一个号短时间内冒出大量会话。
    # 见 pool.stable_session_id 的推导口径。
    session_seed = stable_session_seed(account, body)
    upstream = protocol.build_upstream_body(
        model=model, messages=messages, tools=body.get("tools"),
        max_tokens=body.get("max_tokens") or body.get("max_completion_tokens"),
        thinking=_thinking_choice(body),
        reasoning_effort=_effort_of(body),
        session_seed=session_seed,
    )
    plan = {"token": token, "realm": realm, "uid": uid, "machine_id": machine_id,
            # 哨兵归因用：哪条账号出了错
            "account_id": int(getattr(account, "id", 0) or 0),
            "name": str(data.get("name") or ""), "email": str(data.get("email") or ""),
            "model": model, "upstream": upstream,
            "model_name": str(body.get("model") or model.get("key") or "")}

    # ---- model server 新链路组包（OpenAI 直通 + metadata）----
    # messages 用规整后的形态（content 统一字符串、system 合并）——
    # 实测新端点对这套形态完全接受，且与旧协议共用同一份规整逻辑，
    # 两条链路的入参口径保持一致。
    upstream_openai = _build_model_server_body(
        model=model, messages=messages, body=body,
        session_seed=session_seed,
        account_id=plan["account_id"])
    plan["upstream_openai"] = upstream_openai
    plan["mode"] = "model_server"
    return plan


#: model server 的 metadata.business 块。官方客户端（qoder-agent-sdk
#: worker 运行时）的常量：product/type/scene 是固定值，缺了这块上游
#: 会把请求判成 Unsupported model / All backends failed。
_BUSINESS_PRODUCT = "qoder_work"
_BUSINESS_TYPE = "agent"
_BUSINESS_STAGE = "agent"


def _build_model_server_body(*, model: dict, messages: list[dict],
                             body: dict, session_seed: str,
                             account_id: int) -> dict:
    """组 model server 请求体（OpenAI 直通 + metadata）。

    参数透传白名单与官方 worker 的 `hcl` 一致（top_p/top_k/…/enable_thinking/
    reasoning_budget_tokens/preserve_thinking）；`reasoning_effort` 单独顶层。
    """
    session_id = ("sess-" + session_seed[:32]) if session_seed else (
        "sess-" + uuid.uuid4().hex[:16])
    request_id = str(uuid.uuid4())
    out: dict = {
        "model": model.get("key") or "",
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
        "metadata": {
            "context": {
                "request_id": request_id,
                "request_set_id": str(uuid.uuid4()),
                "session_id": session_id,
                "source_session_id": "",
                "task_id": "common",
                "client_type": endpoints.MODEL_CLIENT_TYPE,
            },
            "business": {
                "product": _BUSINESS_PRODUCT,
                "type": _BUSINESS_TYPE,
                "version": "0.3.0",
                "id": str(uuid.uuid4()),
                "name": "gateway",
                "begin_at": int(time.time() * 1000),
                "stage": _BUSINESS_STAGE,
            },
        },
    }
    max_tokens = body.get("max_tokens") or body.get("max_completion_tokens")
    if max_tokens:
        out["max_tokens"] = max_tokens
    if body.get("temperature") is not None:
        out["temperature"] = body["temperature"]
    if body.get("stop"):
        out["stop"] = body["stop"]
    tools = body.get("tools")
    if isinstance(tools, list) and tools:
        out["tools"] = tools
    effort = _effort_of(body)
    if effort:
        out["reasoning_effort"] = effort
    thinking = _thinking_choice(body)
    if thinking is False or thinking == "disable":
        out["enable_thinking"] = False
    for key in ("top_p", "top_k", "presence_penalty", "frequency_penalty",
                "parallel_tool_calls", "response_format", "tool_choice",
                "seed", "user"):
        if body.get(key) is not None:
            out[key] = body[key]
    return out


def _plan_iterator(plan: dict):
    """按 plan["mode"] 选上游迭代器（两条链路产出同形态的 chunk dict）。

    `plan["abort"]`（可选，`http_keepalive.AbortHandle`）：网关在客户端断开时用它
    打断阻塞读（r3 第 2 项）；没有时行为与原来一致。

    ## 端点被撤时的自动回退（2026-11-26）

    model server 的 HTTP 端点 `/model/v1/chat/completions` 在上游已经撤掉：
    连不带凭证的请求都回**空 404**，所以那不是「模型名写错」，而是**路由没了**。
    以前这条 404 被记成 `model_unknown`，用户看到「模型不存在」，账号还被
    记一次失败 —— 明明是网关侧要换链路。

    现在：第一次拿到 `kind="endpoint_gone"` 就把这条链路**进程内标记为不可用**
    （`_MODEL_SERVER_STATE`），本次请求立刻改走旧 COSY 链路
    （`/algo/.../agent_chat_generation`，实测 200 正常出流），
    之后所有请求直接走旧链路，不再每次白撞一次 404。
    标记有 TTL：上游哪天把端点放回来，到点会自动再用新链路试。

    ## 主机不可达也算「这条链路没了」（2026-11-28，issue #126）

    原来只回退 `endpoint_gone`（空 404）。但主机本身消失（DNS 解析失败 /
    连接被拒 / 无路由）同样是**链路级**事实，跟请求内容、账号状态无关 ——
    这类错误原来被原样抛给用户，国际版账号因此 100% 报「对话请求网络失败」，
    连一次旧链路都没试过。

    只放宽到「不可达」这几类，**不含 timeout**：超时可能请求已经到过上游并
    记账，重发会二次计费。
    """
    mode = plan.get("mode")
    if mode == "model_server" and _model_server_usable():
        try:
            yield from client.iter_model_chat_stream(
                plan["token"], plan["realm"], body=plan["upstream_openai"],
                account_id=plan.get("account_id", 0), abort=plan.get("abort"))
            return
        except client.UpstreamError as exc:
            if not _is_link_gone(exc):
                raise
            _mark_model_server_down(exc.message)
            logger.warning("Qoder model server 不可用，自动回退旧链路：%s",
                           exc.message)
    yield from client.iter_chat_stream(
        plan["token"], plan["realm"], uid=plan["uid"],
        body=plan["upstream"], name=plan["name"], email=plan["email"],
        machine_id=plan["machine_id"], account_id=plan.get("account_id", 0),
        abort=plan.get("abort"))


#: 「主机不可达」的特征片段（小写比较）。只认**没到过上游**的失败形态：
#: DNS 解析失败、连接被拒、网络不可达、没有路由。
#: **不要**把 timeout / reset 放进来 —— 那两类的请求可能已经到过上游并记账，
#: 重发等于二次计费。
_UNREACHABLE = (
    "getaddrinfo", "11001", "name or service not known",
    "temporary failure in name resolution", "nodename nor servname",
    # macOS / musl 上常见的另一种写法（PR #130 补的）
    "name resolution",
    "connection refused", "10061", "network is unreachable", "no route to host",
)


def _is_link_gone(exc) -> bool:
    """这条上游链路是不是**整体没了**（可以安全回退到旧链路）。

    `endpoint_gone`：端点被撤（空 404），上游自己表示「没有这条路由」。
    `network` 且特征命中 `_UNREACHABLE`：主机不可达（例如国际版
    `api2-v2.qoder.sh` 已 NXDOMAIN），请求没到过上游，可以改走旧链路。
    """
    if getattr(exc, "kind", "") == "endpoint_gone":
        return True
    if getattr(exc, "kind", "") != "network":
        return False
    detail = str(getattr(exc, "detail", "") or "").lower()
    return any(marker in detail for marker in _UNREACHABLE)


#: model server 端点的可用性标记（进程内，跨账号共享 —— 端点没了是平台级事实）。
_MODEL_SERVER_STATE: dict = {"down_at": 0.0, "reason": ""}

#: 端点被撤后多久再试一次（秒）。太长会让上游恢复后还用旧链路，
#: 太短会回到「每次请求白撞一次 404」。
MODEL_SERVER_RETRY_S = 600.0


def _model_server_usable() -> bool:
    """model server 当前能不能用（被标记下线且未到期 → 不用）。"""
    down_at = float(_MODEL_SERVER_STATE.get("down_at") or 0.0)
    if not down_at:
        return True
    if time.time() - down_at >= MODEL_SERVER_RETRY_S:
        # 到期：清掉标记，用一次新链路探活；失败会重新标记
        _MODEL_SERVER_STATE.update({"down_at": 0.0, "reason": ""})
        return True
    return False


def _mark_model_server_down(reason: str) -> None:
    """把 model server 标记为不可用（下次到期前所有请求直接走旧链路）。"""
    if not _MODEL_SERVER_STATE.get("down_at"):
        logger.warning("Qoder model server 标记下线（%s），%d 秒后再试",
                       reason, int(MODEL_SERVER_RETRY_S))
    _MODEL_SERVER_STATE.update({"down_at": time.time(),
                                "reason": str(reason or "")[:200]})


def model_server_state() -> dict:
    """当前 model server 链路状态（诊断用：`/api/qoder/...` 会透出）。"""
    down_at = float(_MODEL_SERVER_STATE.get("down_at") or 0.0)
    return {
        "usable": _model_server_usable() if down_at else True,
        "down_at": down_at or None,
        "reason": _MODEL_SERVER_STATE.get("reason") or "",
        "retry_after_s": max(0, int(MODEL_SERVER_RETRY_S - (time.time() - down_at))
                             ) if down_at else 0,
    }


# ---------------------------------------------------------------------------
# 流式：OpenAI
# ---------------------------------------------------------------------------
def stream_openai(plan: dict, *, want_usage: bool = False,
                  holder: dict | None = None):
    """产出 OpenAI SSE 字节帧。最后一个 chunk 带 `usage`（若下游要）。

    `holder` 由网关传入：流结束时把累计 `usage` 写回去，供记账使用 ——
    生成器本身的返回值拿不到，而用量只能从最后一帧读。
    """
    holder = holder if isinstance(holder, dict) else {}
    chunk_id = "chatcmpl-" + uuid.uuid4().hex[:24]
    created = int(time.time())
    model_name = plan["model_name"]
    usage: dict = {}
    emitted_role = False
    # **真实分段耗时**：首字 / 末字的墙钟时刻，流结束时写回 holder。
    # 以前这里只记 usage，网关只能拿「请求开始→收尾」当 TTFT，
    # 于是 `latency == ttfb`、TPS 永远算不出来（面板上 Qoder 一片“—”）。
    started_at = time.perf_counter()
    first_at = last_at = 0.0

    for inner in _plan_iterator(plan):
        choices = inner.get("choices") or []
        if choices:
            choice = choices[0] or {}
            delta = choice.get("delta") or {}
            finish = choice.get("finish_reason")
            content = delta.get("content") or ""
            reasoning = delta.get(REASONING_KEY) or ""
            # **tool_calls 必须透传**：harness 客户端靠它继续执行任务。
            # 原来只取 content / reasoning，tool_calls 被丢弃，于是客户端
            # 只收到一个 `finish_reason=tool_calls` 的空收尾，
            # 表现为「思考完就结束，任务没开始跑」。
            tool_calls = delta.get("tool_calls")
            if not emitted_role and (content or reasoning or tool_calls):
                yield protocol.sse(protocol.openai_chunk(
                    chunk_id=chunk_id, model_name=model_name, created=created,
                    role="assistant"))
                emitted_role = True
            if tool_calls:
                yield protocol.sse(protocol.openai_chunk(
                    chunk_id=chunk_id, model_name=model_name, created=created,
                    tool_calls=tool_calls))
            if content or reasoning:
                now = time.perf_counter()
                if not first_at:
                    first_at = now
                last_at = now
                yield protocol.sse(protocol.openai_chunk(
                    chunk_id=chunk_id, model_name=model_name, created=created,
                    content=content, reasoning=reasoning))
            if finish:
                yield protocol.sse(protocol.openai_chunk(
                    chunk_id=chunk_id, model_name=model_name, created=created,
                    finish_reason=str(finish)))
        frame_usage = inner.get("usage")
        if isinstance(frame_usage, dict) and frame_usage:
            usage = frame_usage

    # 估算积分：上游 usage 里没有 credit 时按倍率补，
    # 客户端底部「消耗 N 积分」读的就是这个字段。
    credit = 0.0
    try:
        from admin.db import SessionLocal
        from admin.platform.shared import credits_for

        _raw = usage.get("credit") if isinstance(usage, dict) else None
        credit = float(_raw) if isinstance(_raw, (int, float)) else 0.0
        if credit <= 0 and isinstance(usage, dict):
            _toks = (usage.get("total_tokens")
                     or usage.get("completion_tokens") or 0)
            storage = str(plan.get("storage") or plan.get("platform") or "qoder")
            with SessionLocal() as _db:
                credit = credits_for(_db, storage, model_name, _toks)
    except Exception:  # noqa: BLE001
        credit = 0.0
    if want_usage and usage:
        yield protocol.sse(protocol.openai_usage_frame(
            chunk_id=chunk_id, model_name=model_name, usage=usage,
            created=created, credit=credit))
    holder["usage"] = usage
    if first_at:
        holder["ttfb_ms"] = int((first_at - started_at) * 1000)
    if last_at:
        # 纯解码段终点（不含收尾帧 / usage 帧的网络时间）
        holder["total_ms"] = int((last_at - started_at) * 1000)
    yield protocol.sse_done()


def collect_openai(plan: dict) -> dict:
    """非流式：把流收成一个完整 `chat.completion`。"""
    chunk_id = "chatcmpl-" + uuid.uuid4().hex[:24]
    created = int(time.time())
    model_name = plan["model_name"]
    parts: list[str] = []
    reasoning_parts: list[str] = []
    finish = "stop"
    usage: dict = {}

    for inner in _plan_iterator(plan):
        choices = inner.get("choices") or []
        if choices:
            choice = choices[0] or {}
            delta = choice.get("delta") or {}
            if delta.get("content"):
                parts.append(str(delta["content"]))
            if delta.get(REASONING_KEY):
                reasoning_parts.append(str(delta[REASONING_KEY]))
            if choice.get("finish_reason"):
                finish = str(choice["finish_reason"])
        frame_usage = inner.get("usage")
        if isinstance(frame_usage, dict) and frame_usage:
            usage = frame_usage

    message: dict = {"role": "assistant", "content": "".join(parts)}
    if reasoning_parts:
        message["reasoning_content"] = "".join(reasoning_parts)
    out = {
        "id": chunk_id,
        "object": "chat.completion",
        "created": created,
        "model": model_name,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
    }
    if usage:
        out["usage"] = {
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
        }
        # 上游给的积分是权威计费值，记账层优先用它。
        if isinstance(usage.get("credits"), (int, float)):
            out["usage"]["credits"] = float(usage["credits"])
    out["x_qoder_credits"] = usage.get("credits")
    return out
