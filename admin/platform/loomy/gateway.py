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

import asyncio
import json
import logging
import os
import time

from starlette.responses import JSONResponse, StreamingResponse

from admin.platform import shared
from admin.platform import protocol_bridge as bridge
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

#: 单次请求最多换几个号。
#:
#: ## 为什么从 3 提到 8（2026-11-10）
#:
#: 用户反馈「当前请求的额度已用尽，网关没在尝试使用其它账号的吗」。
#: 实测当时池子里有 **33 个可选号**（余额 > 0、不在冷却），但请求仍然报
#: 额度用尽 —— 因为循环上限是 3：只要前 3 个被选中的号都回了 402，
#: 就直接 `raise last_error` 把错误甩给客户端。
#:
#: 而 402 有两种，混在一起时前 3 个很容易全中：
#:   * **永久余额为 0** —— 这号确实没钱；
#:   * **当日额度用尽** —— Loomy 有 `dailyQuota`，当天的额度花完了但
#:     `currentBalance` 还是正的。这类号**换一个就能成**，不该让客户端看到错误。
#:
#: 提到 8 之后，撞上几个当日额度用尽的号也能继续往后试。
#: 上限仍有：每次尝试都要占用一个号的租约，无上限会让一个坏请求打遍整池。
MAX_ATTEMPTS = int(os.getenv("LOOMY_MAX_ATTEMPTS", "8"))


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


#: 上游（OpenAI 兼容 /chat/completions）接受的角色。
#: 2026-11-05 修正：官方 3065 客户端（agentApi）打同一上游端点的 498 条
#: 真实请求体里，`tool` 消息（175 条）与 assistant `tool_calls`（122 条）
#: **原样透传，490/490 全部 200** —— 之前「上游只认三种角色」的判断是错的，
#: 把 tool 转成 user 文本反而导致 call 工具调用不生效。
_UPSTREAM_ROLES = frozenset({"system", "user", "assistant", "tool"})


def normalize_messages(raw) -> list[dict]:
    """把 messages 归一成上游能接受的样子。

    ## 为什么必须做

    `developer` 是 OpenAI 较新的角色，DSH 这类 harness 在**开推理时**
    会用它承载系统提示（还把整段 system prompt 塞进去），上游不认，
    需要归一成 `system`。

    ## 归一规则（2026-11-05 起改为**结构化透传**）

    * `developer` -> `system`（它本来就是系统提示的语义）
    * `tool` -> **原样保留**（含 `tool_call_id` / `name`），上游实测接受；
      老式 `function` 角色没有 call id 结构，降级为 user 文本
    * assistant 的 `tool_calls` -> **原样保留**（不再压成 JSON 文本块）。
      之前转文本的后果：模型把上一轮调用读成「一段描述」，
      参数经常缺引号/少括号，客户端解析失败直接中断
    * 未知角色 -> 丢弃
    * `system` **全部前置**（中途出现的 system 会失去指令权重）
    * content 数组仍取文本块（上游文本协议）；assistant 工具调用消息的
      `content: null` 保留 —— 那是标准 OpenAI 形状
    """
    if not isinstance(raw, list):
        return []
    systems: list[dict] = []
    others: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role == "developer":
            role = "system"
        elif role == "function":
            # 老式 function 角色语义等同工具结果。PR #18 实测上游接受
            # role=tool，所以归一到 tool 并保留调用标识，不再降级成 user 文本
            # （降级会让模型把结果当成又一条用户消息，重复发起同一调用）。
            role = "tool"
        if role not in _UPSTREAM_ROLES:
            continue
        content = item.get("content")
        if isinstance(content, list):
            # 兼容 parts 数组：只取文本块
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text") or ""))
                elif isinstance(block, str):
                    parts.append(block)
            content = "".join(parts)
        # content=None 只允许标准 OpenAI 形状
        # `{"role":"assistant","content":null,"tool_calls":[...]}`；
        # 其余空消息直接丢，避免上游把空输入当非法请求。
        _has_calls = isinstance(item.get("tool_calls"), list)
        if content is None and not (role == "assistant" and _has_calls):
            continue
        message = {"role": role, "content": content}
        if role == "tool":
            # 工具结果**原样透传**（含调用标识）：上游实测接受 role=tool。
            # 丢掉 tool_call_id 的话模型不知道结果对应哪次调用，会重复调用。
            _tcid = item.get("tool_call_id")
            if _tcid:
                message["tool_call_id"] = str(_tcid)
            elif item.get("name"):
                # function 角色没有 tool_call_id，用 name 兜住配对凭据
                message["tool_call_id"] = str(item["name"])
            if item.get("name"):
                message["name"] = str(item["name"])
        if role == "assistant" and isinstance(item.get("tool_calls"), list):
            # assistant 的工具调用**原样保留**（不再压成 JSON 文本块）：
            # 官方 3065 客户端就是结构化透传，实测上游全部 200。转文本会让
            # 模型把调用读成「一段描述」，参数经常缺引号/少括号。
            # 唯一例外：协议要求 arguments 是字符串；客户端传对象时序列化回去。
            calls = []
            for call in item["tool_calls"]:
                if not isinstance(call, dict):
                    continue
                c = dict(call)
                fn = c.get("function")
                if isinstance(fn, dict) and not isinstance(fn.get("arguments"), str):
                    fn = dict(fn)
                    fn["arguments"] = json.dumps(fn.get("arguments"), ensure_ascii=False)
                    c["function"] = fn
                calls.append(c)
            message["tool_calls"] = calls
        if role == "system":
            systems.append(message)
        else:
            others.append(message)
    return systems + others


def _thinking_requested(body: dict) -> str:
    """下游是否**明确要**思考；要的话返回档位（空串表示没要）。

    以前这里是无条件 `enable_thinking=False` —— 后果是用户把
    `reasoning_effort` 设成 high/xhigh/max 也照样被网关按死，面板上看
    「档位没生效」，其实是网关把请求改写成了「不要思考」。

    规则和 `shared.reasoning_effort_of` 对齐：`off/none/minimal` 视为不要，
    其余档位（含 Anthropic 的 `thinking.budget_tokens`）视为要。
    """
    from admin.platform.shared import reasoning_effort_of

    effort = reasoning_effort_of(body)
    if not effort:
        return ""
    if effort in ("off", "none", "minimal"):
        return ""
    return effort


def _apply_no_think(body: dict) -> dict:
    """按下游意图决定「要不要思考」，并给够输出预算。

    默认（下游没说要思考）关掉推理链 —— 上游 reasoning 模型会**无视
    max_tokens 跑满思考链**，导致 `finish_reason: length` 且 `content: ""`，
    看起来像「空回复」，其实是正文还没开始就被截断。

    但**下游明确要了档位**（`reasoning_effort` = low/medium/high/xhigh/max）
    时必须尊重：打开 `enable_thinking` 并把档位原样带给上游。否则这个参数
    在 Loomy 上就是个摆设 —— 客户端以为调高了推理强度，实际被网关抹平。
    """
    out = dict(body)
    requested = _thinking_requested(body)
    params = dict(out.get("chat_template_kwargs") or {})
    if requested:
        params["enable_thinking"] = True
        out["enable_thinking"] = True
        # 上游认的是 OpenAI 的 `reasoning_effort`；档位名原样带过去，
        # 不支持的档位由上游决定「忽略」还是「报错」，网关不擅自降级。
        if requested.isdigit() or ":" in requested:
            # `budget:8192` / `adaptive` 这类不是上游枚举值：转成等价档位
            try:
                budget = int(requested.split(":", 1)[1])
            except (IndexError, ValueError):
                budget = 0
            if budget:
                out["reasoning_effort"] = ("low" if budget <= 2048 else
                                           "medium" if budget <= 8192 else "high")
        else:
            out["reasoning_effort"] = requested
    else:
        params.setdefault("enable_thinking", False)
        out.setdefault("enable_thinking", False)
    out["chat_template_kwargs"] = params

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
    # 所以只在**确实要关思考**时才拼；用户明确要思考时拼上去等于往 prompt
    # 里塞垃圾，既污染输入，也白白拉低缓存命中。
    if not requested:
        messages = normalize_messages(out.get("messages") or [])
        # ⚠️ 归一后为空时**不要回写**。
        # normalize_messages 会丢弃无法识别的角色；全被丢光还回写，
        # 就是把空数组发给上游 -> `400 messages 不能为空`（PR #18 定位的
        # 第二个来源）。保留原始 messages，让上游看到真实输入、报错更准确。
        if messages:
            for index in range(len(messages) - 1, -1, -1):
                item = messages[index]
                if not isinstance(item, dict) or item.get("role") != "user":
                    continue
                content = item.get("content")
                # 只给真正的用户文本加前缀：空串跳过，避免生成
                # "/no_think " 这种只有前缀没有内容的消息。
                if (isinstance(content, str) and content.strip()
                        and not content.lstrip().startswith("/no_think")):
                    messages[index] = {**item, "content": "/no_think " + content}
                break
            out["messages"] = messages
    return out


def _attempt(db, request, key, body: dict, realm: str):
    """选号 + 租约 + 调上游（换号重试）。

    **必须在工作线程里执行**（见 `_forward`）：本函数从头到尾是同步的
    —— SQLAlchemy 同步会话 + `client.chat_completions` 里的 `urlopen`。
    直接跑在事件循环线程上时，一次上游卡顿会把整个单进程服务冻住
    （2026-11-05 线上「导入账号卡死」就是这么来的：`DEFAULT_TIMEOUT`
    是 600 秒，期间所有请求、控制台、同步接口全部无响应）。
    """
    model = str(body.get("model") or "")
    exclude: set[int] = set()
    last_error: client.UpstreamError | None = None
    for _ in range(MAX_ATTEMPTS):
        account = shared.select_account(
            db, storage=loomy_accounts.PLATFORM, realm=realm, key=key,
            model=model, exclude_ids=exclude,
            # 余额为 0 的号调用必被上游 402 打回：直接不进候选，
            # 而不是先撞一次再冷却（见 shared.select_account 的说明）。
            min_balance=0)
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
            # **contextvar 只在设置它的线程上下文里可见**。这段跑在工作线程，
            # 事件循环那边的 `_bill` 读不到，必须在这里取出来随结果带回去。
            stats = {
                "ttfb_ms": int(client.LAST_TTFB_MS.get() or 0),
                "total_ms": int(client.LAST_TOTAL_MS.get() or 0),
            }
            return result, account, started, stats
        except client.UpstreamError as exc:
            shared.release(account)
            # ---- 402 先复核，再决定要不要把这个号判死（2026-11-10）----
            #
            # 上游的 402 有两种含义：
            #   1. **永久余额为 0** —— 该号真的没钱了，判死是对的；
            #   2. **当日额度用尽** —— `dailyQuota` 花完而 `currentBalance`
            #      仍是正的。这类号只是今天不能用了，把 `balance_remain`
            #      写成 0 会**永久**丢掉它（要等下次同步才恢复），而且会
            #      让「可选号」数量虚低，最终表现就是「明明有几十个号，
            #      却一直报额度用尽」。
            #
            # 这里用一次便宜的积分接口复核：上游说还有余额 → 只做短暂冷却
            # （当日额度类），不判死；上游也说 0 → 按原来的判死处理。
            zero_balance = exc.kind == "quota_exceeded"
            cool_seconds = 600 if exc.kind in ("auth", "quota_exceeded") else 0
            if exc.kind == "quota_exceeded":
                try:
                    probe = loomy_accounts.sync_quota(db, account, with_models=False)
                    quota = (probe or {}).get("quota") or {}
                    available = int(quota.get("available") or 0)
                    if available > 0:
                        # 上游确认还有余额：这是「当日额度/瞬时限额」，不是没钱
                        zero_balance = False
                        cool_seconds = 300
                        logger.info(
                            "Loomy 402 但余额仍为 %s，判为当日/瞬时限额，短暂冷却 300s（acc=%s）",
                            available, account.id)
                except Exception:  # noqa: BLE001 - 复核失败就按原逻辑保守处理
                    logger.debug("Loomy 402 余额复核失败 acc=%s", account.id,
                                 exc_info=True)
            shared.note_failure(
                db, account, kind=exc.kind, detail=exc.detail or exc.message,
                status=exc.status, model=model, public_platform=PUBLIC,
                cool_seconds=cool_seconds,
                zero_balance=zero_balance)
            last_error = exc
            # **额度耗尽 / 登录失效必须换号，不能把客户端晾在错误上**：
            # 这两类错都是「号坏了」而不是「请求坏了」，换下一个号就能成。
            # 它们已在上面的 note_failure 里冷却（额度号余额归零），
            # 不会死循环 —— 真没号可用时下一轮 select_account 返回 None，
            # 走 _no_account_error 的分场景文案。
            if not exc.retryable() and exc.kind not in ("auth", "quota_exceeded"):
                break
            # **400「请重试」类错误不要立刻换号**：DashScope 的瞬时 400 与
            # 账号无关，换号也一样失败，反而把唯一可用账号排除掉。
            # 先短暂退避、同账号再试一次（上游通常几百毫秒就恢复）。
            if exc.status == 400 and exc.kind != "quota_exceeded":
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
        # 试满一轮还是失败：把「试了几个号」说清楚。
        # 用户看到「额度已用尽」时最想知道的就是「到底试过几个，还有没有号」。
        tried = len(exclude)
        if tried >= 2:
            raise shared.PlatformRejected(
                502 if last_error.kind not in ("quota_exceeded",) else 402,
                "Loomy 已连续尝试 %d 个账号都失败（最后一个原因：%s）。"
                "已自动跳过这些号，其它账号会在 5 分钟内回到候选。"
                % (tried, last_error.message[:80]),
                kind=last_error.kind, code="attempts_exhausted",
                headers={"Retry-After": "5"})
        raise last_error
    raise _no_account_error(db, realm)


def _no_account_error(db, realm: str):
    """「没有可用账号」要分清三种完全不同的原因。

    原来一律回 `503 请先导入并确认状态为启用` —— 号明明有 100 个、只是全都
    额度用尽时，这句话会把人往「去检查账号状态」的死路上带。

    ## 三种原因（2026-11-09 补第三种）

    1. **确实都没额度**：`balance_remain <= 0` 且上游从没发过配额；
    2. **都在冷却 / 在途**：号是好的，此刻排不上；
    3. **余额被误清零**：`balance_remain <= 0` 但 `balance_total > 0` ——
       这类号**其实有额度**，是被历史上的一次 402 写死的。
       必须先尝试自愈重同步，再把结果如实说出来，不能笼统回「都没额度了」。
    """
    try:
        from admin.models import Account

        query = db.query(Account.id, Account.balance_remain,
                         Account.balance_total).filter(
            Account.platform == loomy_accounts.PLATFORM,
            Account.status == "active")
        if realm:
            query = query.filter(Account.domain == realm)
        rows = query.all()
    except Exception:  # noqa: BLE001 - 统计不出来就退回通用文案
        rows = []
    total = len(rows)
    empty = [rid for rid, balance, _total
             in ((r[0], r[1], r[2]) for r in rows) if int(balance or 0) <= 0]
    stale = [rid for rid, balance, granted
             in ((r[0], r[1], r[2]) for r in rows)
             if int(balance or 0) <= 0 and int(granted or 0) > 0]

    # 有「被误清零」的号就先修一批，修好了立刻让调用方重试，
    # 而不是把错误话术甩给用户。
    if stale:
        try:
            healed = loomy_accounts.heal_stale_zero(db, realm)
        except Exception:  # noqa: BLE001 - 自愈失败不能盖住原始错误
            healed = {"healed": 0}
            logger.debug("Loomy 余额自愈失败", exc_info=True)
        if int(healed.get("healed") or 0) > 0:
            raise shared.PlatformRejected(
                503,
                "Loomy 号池刚恢复 %d 个「余额被误判为 0」的账号，请立即重试。"
                % int(healed.get("healed") or 0),
                kind="server_error", code="balances_healed",
                headers={"Retry-After": "1"})

    if total and len(empty) >= total:
        # 全是 0：再区分「上游确实没给配额」与「我们没同步过」。
        if stale:
            return shared.PlatformRejected(
                402,
                "Loomy 有 %d 个账号余额显示为 0，其中 %d 个上游其实发过配额，"
                "正在重新同步余额（约 1 分钟内生效）。若仍失败，"
                "请在「账号池」里对这些号点一次「同步余额」。"
                % (total, len(stale)),
                kind="quota_exceeded", code="all_quota_exceeded_stale")
        return shared.PlatformRejected(
            402, "所有 Loomy 账号的积分都已用尽。等下一次余额同步或每日签到"
                 "刷新到额度后会自动恢复，也可以现在去「账号池」给某个号充值/签到。",
            kind="quota_exceeded", code="all_quota_exceeded")
    if total:
        # 有余额但全在冷却 / 熔断 / 在途占满：这不是"没有号"，是"此刻都不可用"。
        return shared.PlatformRejected(
            503, "Loomy 账号都在冷却或用满并发，请稍后重试（%d 个号可用但此刻排不上）"
                 % total,
            kind="server_error", code="all_cooling",
            headers={"Retry-After": "10"})
    return shared.PlatformRejected(
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
                ttfb_ms=ttfb, latency_ms=latency,
                reasoning_effort=shared.reasoning_effort_of_request(request))
    except Exception:  # noqa: BLE001
        logger.exception("Loomy 记账失败")


def _settle(db, account) -> None:
    """释放租约 + 记成功 + 提交（同步 DB，跟着工作线程一起跑）。"""
    shared.release(account)
    shared.note_success(account)
    try:
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()


async def _forward(body: dict, request, *, usage=None) -> JSONResponse:
    """鉴权 -> 选号 -> 上游 -> 记账。

    ## 为什么每一步都要 `to_thread`

    `admin.server` 是**单进程单 worker** 的 uvicorn。`shared.authenticate`
    与 `_attempt` 都是纯同步实现（SQLAlchemy 同步会话 + `urlopen`），
    直接 `await` 之外的地方调用就等于把事件循环钉死：一次上游卡顿会让
    控制台、导入账号、同步接口全部失去响应，且 `DEFAULT_TIMEOUT=600`
    意味着最坏要等 10 分钟。2026-11-05 线上事故即此。

    放进工作线程后，阻塞只占一个线程，事件循环照常服务其它请求。
    """
    from admin.db import SessionLocal

    realm = _realm(request)
    db = SessionLocal()
    try:
        key = await asyncio.to_thread(
            shared.authenticate, db, request, platform=_public(request))
        try:
            result, account, started, stats = await asyncio.to_thread(
                _attempt, db, request, key, body, realm)
        except shared.PlatformRejected:
            raise
        except client.UpstreamError as exc:
            return _fail(exc)
        await asyncio.to_thread(_settle, db, account)
        await asyncio.to_thread(
            _bill, key, account, str(body.get("model") or ""), result,
            request=request, started=started, stats=stats)
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


def _open_stream(db, request, key, body: dict, realm: str, stats: dict,
                 model: str):
    """选号 + 租约 + 上游建连（**同步阻塞，必须跑在工作线程里**）。

    `client.chat_stream` 里那句 `opener.open(...)` 会一直阻塞到**响应头**
    到达才返回，因此这一步和 `_attempt` 一样不能待在事件循环上。

    返回 `(chunks, account, last_error, started)`：`chunks` 为 None 表示
    没拿到可用账号，调用方据此返回上游错误或 503。
    """
    exclude: set[int] = set()
    account = None
    chunks = None
    last_error = None
    started = time.perf_counter()
    for _ in range(MAX_ATTEMPTS):
        account = shared.select_account(
            db, storage=loomy_accounts.PLATFORM, realm=realm, key=key,
            model=model, exclude_ids=exclude,
            # 同上：零余额号不进候选，避免每次先撞一次 402 再换号。
            min_balance=0)
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
            # 与 `_attempt` 同口径：402 先复核余额，别把「当日额度用尽」判死。
            zero_balance = exc.kind == "quota_exceeded"
            cool_seconds = 600 if exc.kind in ("auth", "quota_exceeded") else 0
            if exc.kind == "quota_exceeded":
                try:
                    probe = loomy_accounts.sync_quota(db, account, with_models=False)
                    available = int(((probe or {}).get("quota") or {}).get("available") or 0)
                    if available > 0:
                        zero_balance = False
                        cool_seconds = 300
                        logger.info(
                            "Loomy 流式 402 但余额仍为 %s，短暂冷却 300s（acc=%s）",
                            available, account.id)
                except Exception:  # noqa: BLE001
                    logger.debug("流式 402 余额复核失败 acc=%s", account.id,
                                 exc_info=True)
            shared.note_failure(
                db, account, kind=exc.kind, detail=exc.detail or exc.message,
                status=exc.status, model=model, public_platform=PUBLIC,
                cool_seconds=cool_seconds,
                zero_balance=zero_balance)
            last_error = exc
            exclude.add(account.id)
            # 额度耗尽 / 登录失效：号坏了不是请求坏了，换下一个号
            # （与非流式 `_attempt` 同口径，见那里的注释）。
            if (not exc.retryable()
                    and exc.kind not in ("auth", "quota_exceeded")):
                break
        except Exception as exc:  # noqa: BLE001
            shared.release(account)
            logger.exception("Loomy 流式调用失败 acc=%s", account.id)
            last_error = client.UpstreamError(str(exc), kind="network")
            exclude.add(account.id)
    return chunks, account, last_error, started


async def _forward_stream(body: dict, request):
    """真流式：选号 -> 上游 SSE -> 逐帧转发 -> 收尾记账。"""
    from admin.db import SessionLocal

    # **强制要 usage 尾帧**：客户端没带 `stream_options.include_usage` 时
    # 上游不发 usage —— `_bill` 记到 0 token，客户端的「消耗积分」
    # 也永远是空。这是「积分不显示」的直接根因之一。
    if body.get("stream") and not isinstance(
            (body.get("stream_options") or {}).get("include_usage"), bool):
        body = {**body, "stream_options": {"include_usage": True}}

    realm = _realm(request)
    db = SessionLocal()
    key = await asyncio.to_thread(
        shared.authenticate, db, request, platform=_public(request))
    model = str(body.get("model") or "")
    # 分段耗时容器：**必须在调用 client.chat_stream 之前定义**。
    # 它由上游流式读取器填 ttfb/total，再交给 _bill 记账。
    stats: dict = {}
    chunks, account, last_error, started = await asyncio.to_thread(
        _open_stream, db, request, key, body, realm, stats, model)

    if chunks is None:
        # **先算提示文案，再关会话** —— `_no_account_error` 要查库。
        # 顺序写反会在关闭后的 Session 上查询，异常被上层吞成
        # 一个没有信息的 500。
        exhausted = _no_account_error(db, realm) if last_error is None else None
        db.close()
        if last_error is not None:
            return _fail(last_error)
        raise exhausted

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

    # **空闲心跳**：上游排队/限流/慢思考时整条流可能几十秒无字节，
    # 客户端只能干等。每 8 秒发一帧 `: ping` 注释（SSE 规范，
    # 所有标准解析器忽略），把「链路活着」变成可见事实。
    safe_gen = bridge.with_pings(generator(), ping=bridge.CHAT_PING)

    return StreamingResponse(
        safe_gen, media_type="text/event-stream",
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
    # **工具调用的协议完整性**（客户端跑任务时中断就出在这里）：
    #   1. `tool_calls[].index` 必须存在且连续 —— 上游偶尔省略 index，
    #      客户端按 index 拼 arguments 会错位，拼出来的 JSON 是坏的；
    #   2. `finish_reason` 必须与**最后一个 tool_calls 分片同一帧**发出。
    #      原来先发一个空 delta 帧、再单独发 finish_reason，严格解析的客户端
    #      （DSH / Codex / VS Code 系）会认为工具调用不完整 → 直接中断任务；
    #   3. `arguments` 必须是能 json.loads 的完整串。上游截断时给半截
    #      `{"path": "/tm`，客户端一解析就抛错 —— 这里补成 `{}` 并记日志，
    #      让流程能继续（宁可少一个参数，也不要整个任务崩掉）。
    #
    # 合并所有 tool_calls 分片，等流结束再一次性发出：工具调用本来就不是
    # 增量消费的，客户端要的是「完整调用 + finish_reason」，不是逐字流。
    pending_calls: dict[int, dict] = {}
    saw_tool_call = False
    finish_out = None

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
            for tc in (delta.get("tool_calls") or []):
                if not isinstance(tc, dict):
                    continue
                saw_tool_call = True
                idx = tc.get("index")
                if not isinstance(idx, int):
                    idx = len(pending_calls)
                slot = pending_calls.setdefault(
                    idx, {"id": None, "name": None, "arguments": ""})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                if fn.get("name"):
                    slot["name"] = (slot["name"] or "") + str(fn["name"])
                if fn.get("arguments"):
                    slot["arguments"] += str(fn["arguments"])
            if choice.get("finish_reason"):
                finish_out = str(choice["finish_reason"])
            # 有正文/思考就逐帧发；工具调用留到最后合并发（见上）
            if out:
                yield frame(out)

    if saw_tool_call:
        # index 归一成 0..n-1，保证连续（客户端按它顺序建数组）
        merged = []
        for new_idx, idx in enumerate(sorted(pending_calls)):
            slot = pending_calls[idx]
            args = slot["arguments"] or "{}"
            try:
                json.loads(args)
            except Exception:  # noqa: BLE001 - 上游截断/拼错，兜成空对象
                import logging
                logging.getLogger("loomy").warning(
                    "tool_call arguments 不是合法 JSON，已兜底: %r", args[:200])
                args = "{}"
            merged.append({
                "index": new_idx,
                "id": slot["id"] or ("call_%d_%s" % (new_idx, int(time.time() * 1000))),
                "type": "function",
                "function": {"name": slot["name"] or "unknown", "arguments": args},
            })
        # **finish_reason 与 tool_calls 同一帧**（关键修复）
        yield frame({"tool_calls": merged}, "tool_calls")
        finish_out = "tool_calls"
    elif finish_out:
        yield frame({}, finish_out)

    # 收尾：带上整合好的 usage + 积分（客户端底部显示消耗要用）
    final_usage = _usage_with_credit(usage_acc, model)
    holder["usage"] = final_usage
    yield frame({}, None, usage=final_usage)
    yield b"data: [DONE]\n\n"


def _usage_with_credit(usage: dict, model: str) -> dict:
    """usage 里补齐 `credit` / `credits` / `cost` 三个同义键。

    ⚠️ **必须以原始 usage 为底**，不能只挑 prompt/completion/total 重造：
    那样 `cached_tokens` / `prompt_tokens_details` / `cache_read_input_tokens`
    会被整段丢掉，客户端与面板的「缓存命中」恒为 0（这就是 Loomy 命中率为 0
    的真正原因，与有没有账号无关）。
    """
    src = usage if isinstance(usage, dict) else {}
    out = dict(src)                      # 原样保留所有字段（含缓存）
    out["prompt_tokens"] = int(src.get("prompt_tokens") or 0)
    out["completion_tokens"] = int(src.get("completion_tokens") or 0)
    out["total_tokens"] = int(src.get("total_tokens") or 0)
    # 缓存命中补齐成各家客户端认的写法（幂等；源里没有就一个都不加）
    try:
        from upstream_compat import normalize_usage_cache

        normalize_usage_cache(out)
    except Exception:  # noqa: BLE001 - 补齐失败不影响主流程
        pass
    credit = 0.0
    try:
        # 优先用**上游真实计费**：Loomy 的 usage 里带 `points_consumed`
        # （官方客户端就从 `usage.points_consumed` 读每轮消耗）。
        # 之前只认 `credit` 键 —— 上游明明报了真实消耗，我们却忽略它，
        # 转头按倍率估算，客户端显示的「消耗积分」和流水全对不上。
        for key in ("credit", "credits", "cost", "points_consumed", "points"):
            raw = (usage or {}).get(key)
            if isinstance(raw, (int, float)) and float(raw) > 0:
                credit = float(raw)
                break
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
            # 同样保留上游原始字段（缓存命中在这条路径上也不能丢）
            _u = dict(usage) if isinstance(usage, dict) else {}
            _u["prompt_tokens"] = int(_u.get("prompt_tokens") or 0)
            _u["completion_tokens"] = int(_u.get("completion_tokens") or 0)
            _u["total_tokens"] = int(_u.get("total_tokens") or 0)
            _u["credit"] = _u["credits"] = _u["cost"] = credit
            try:
                from upstream_compat import normalize_usage_cache

                normalize_usage_cache(_u)
            except Exception:  # noqa: BLE001
                pass
            payload["usage"] = _u
        return ("data: %s\n\n" % json.dumps(payload, ensure_ascii=False)).encode()

    yield frame({"role": "assistant"})
    if reasoning:
        yield frame({"reasoning_content": reasoning})

    # **tool_calls 必须转发且协议完整**：harness 客户端靠它决定下一步做什么。
    #
    # 这条是 Loomy 的**真实路径**（上游只有非流式，网关自己合成 SSE），
    # 所以这里的坑最要命。原来有三个问题：
    #   1. `tool_calls` 与 `finish_reason` **分成两帧**发 —— 严格客户端
    #      （DSH / Codex / VS Code 系）要求两者同帧，否则判定调用不完整，
    #      直接中断任务（这正是「跑任务经常报错中断」的主因）；
    #   2. 原样透传上游的 `tool_calls`，**补 `index`** 缺失时客户端按 index
    #      拼参数会错位，拼出来的 JSON 是坏的；
    #   3. 不校验 `arguments` 是否合法 JSON —— 上游给半截串就直接抛错。
    #
    # 现在：归一 index、校验参数（坏则兜成 {}）、与 finish_reason 同帧。
    raw_calls = message.get("tool_calls")
    merged_calls = []
    if isinstance(raw_calls, list):
        for i, tc in enumerate(raw_calls):
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
            args = fn.get("arguments")
            if isinstance(args, dict):
                args = json.dumps(args, ensure_ascii=False)
            if not isinstance(args, str) or not args.strip():
                args = "{}"
            try:
                json.loads(args)
            except Exception:  # noqa: BLE001 - 上游截断/拼错
                import logging
                logging.getLogger("loomy").warning(
                    "upstream tool_call arguments 非法，已兜底: %r", args[:200])
                args = "{}"
            merged_calls.append({
                "index": i,
                "id": tc.get("id") or ("call_%d_%s" % (i, int(time.time() * 1000))),
                "type": "function",
                "function": {"name": (fn.get("name") or "unknown"), "arguments": args},
            })

    if text:
        yield frame({"content": text})
    if merged_calls:
        # **finish_reason 与 tool_calls 同一帧**
        yield frame({"tool_calls": merged_calls}, "tool_calls")
    else:
        yield frame({}, str(choice.get("finish_reason") or "stop"))
    # **始终补一个 usage 终点帧**（带积分），客户端据此显示「消耗 N 积分」。
    # 原来只在 `result["usage"]` 非空时发，且原样透传 —— Loomy 的 usage 里
    # 没有 credit，客户端就永远显示不出消耗。
    yield frame({}, None, with_usage=True)
    yield b"data: [DONE]\n\n"


async def messages(request):
    """Anthropic 协议。**工具调用经协议桥全量映射**（修复执行工具中断）。

    ## 历史坑（本次修复）

    旧实现在请求方向把 `tools` 定义、assistant 的 `tool_use` 块、user 的
    `tool_result` 块**全部丢弃**（只抽 text），在流式方向把
    `delta.tool_calls` 静默丢掉、stop_reason 恒 `end_turn`。结果就是
    「任务跑到执行工具的那一轮，客户端收到『助手结束回合』但没有调用，
    突然终止」。现在整条链路交给 `protocol_bridge`：

    * 请求：`anthropic_to_openai`（tools/tool_use/tool_result 全量映射）
    * 非流式：`openai_to_anthropic`（tool_use 块 + thinking 块 + 缓存 usage）
    * 流式：`anthropic_stream_relay`（tool_use 块 + input_json_delta +
      thinking 块 + 空闲心跳 + 上游错误显式 error 事件）
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
    # 流式：内层 OpenAI chunk 流 → 协议桥转 Anthropic 事件流
    inner = await _forward_stream(normalized, request)
    if getattr(inner, "status_code", 200) >= 400:
        return inner
    return bridge.anthropic_stream_relay(inner, model, prefix="loomy")


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
    """OpenAI Responses 协议。**工具调用经协议桥全量映射**。

    旧实现只认 `input` 的字符串形态，`function_call` /
    `function_call_output` 输入项不认（Codex 的工具循环第二轮就断），
    输出方向不发 function_call 项、流式没有
    `response.function_call_arguments.delta`。整条链路现在交给
    `protocol_bridge.responses_to_openai` / `openai_to_responses` /
    `responses_stream_relay`。
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
    return bridge.responses_stream_relay(inner, model, prefix="loomy")


def _models_payload(db, request, realm: str) -> dict:
    """`/v1/models` 的同步实现：选号 -> 取 token -> 拉模型目录。

    同步网络调用，必须由调用方放进工作线程（理由同 `_attempt`）。
    """
    account = shared.select_account(
        db, storage=loomy_accounts.PLATFORM, realm=realm,
        key=shared.key_row(db, request))
    if account is None:
        return {
            "object": "list", "data": shared.catalog_from_db(
                db, loomy_accounts.PLATFORM),
            "note": "号池没有可用 Loomy 账号，仅返回配置目录"}
    data = loomy_accounts.ensure_token(db, account)
    models = client.fetch_models(loomy_accounts.token_of(data), realm,
                                 account_id=int(account.id or 0))
    return {"object": "list", "data": models}


async def list_models(request):
    """/v1/models —— 只返回能调的模型；无号时回落数据库目录。"""
    from admin.db import SessionLocal

    with SessionLocal() as db:
        realm = _realm(request)
        await asyncio.to_thread(
            shared.authenticate, db, request, platform=PUBLIC)
        try:
            payload = await asyncio.to_thread(
                _models_payload, db, request, realm)
        except client.UpstreamError as exc:
            return _fail(exc)
    return JSONResponse(payload)


def _balance_payload(db, request, realm: str) -> dict:
    """`/v1/balance` 的同步实现（同步网络，交给工作线程跑）。"""
    account = shared.select_account(
        db, storage=loomy_accounts.PLATFORM, realm=realm,
        key=shared.key_row(db, request))
    if account is None:
        return {"error": "no_available_account"}
    data = loomy_accounts.ensure_token(db, account)
    points = client.fetch_points_balance(
        loomy_accounts.token_of(data), realm,
        account_id=int(account.id or 0))
    return {
        "platform": PUBLIC, "realm": realm, "reachable": True,
        "balance": points.get("available") or 0,
        "available": points.get("available") or 0,
        "base_balance": points.get("balance") or 0,
        "daily_balance": points.get("daily_balance") or 0,
        "unit": "points",
        "raw": points.get("raw") or {},
    }


async def balance(request):
    """`/v1/balance` —— **真实积分余额**。

    ## 历史坑（本次修复）

    之前调的是 `fetch_quota`（拿 models 列表当探针），`/v1/balance`
    永远只回「reachable + 模型数」，一分钱余额都看不到 —— 客户端
    面板的 Loomy 积分显示是空的。Loomy 有真实的积分接口
    （`GET /points/balance`，`availableBalance = 基础 + 当日`），
    现在直接返回它。
    """
    from admin.db import SessionLocal

    with SessionLocal() as db:
        realm = _realm(request)
        await asyncio.to_thread(
            shared.authenticate, db, request, platform=PUBLIC)
        try:
            payload = await asyncio.to_thread(
                _balance_payload, db, request, realm)
        except client.UpstreamError as exc:
            return _fail(exc)
    if payload.get("error") == "no_available_account":
        return JSONResponse({"platform": PUBLIC, "realm": realm,
                             "reachable": False, "balance": 0,
                             "note": "号池没有可用账号"}, status_code=503)
    return JSONResponse(payload)


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
