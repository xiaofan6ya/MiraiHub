"""腾讯 Marvis 的 `/v1/*` 转发实现。

## 上游就是 OpenAI 协议

Marvis 的模型网关 `POST /v1/chat/completions` 本身就是 OpenAI 兼容的（SSE 流式、
`finish_reason`、`tool_calls`、`reasoning_content` 都在原位），所以这里的活主要是：

  1. **模型 id 归一** —— 用户传的是显示名（`deepseek-v4-1-flash-261017`），
     上游只认槽位名（`main-auto`）。直接透传真实模型名会 400。
  2. **鉴权头** —— 不是 Bearer token，而是 `Ual-Access-*` 那一组（见 core/endpoints）。
  3. **流式原样转发** —— 不在这里读干流。
  4. **记账** —— 落 usage_logs，控制台的用量与积分统计靠它。

## 为什么每一步都要 `to_thread`（2026-11-16 全站无响应事故）

`core/client.py` 走的是 `urllib`（**同步**）。`admin.server` 是单进程单 worker 的
uvicorn，所以任何同步上游调用只要留在事件循环线程上，就会把**全网关**钉住 ——
控制台、账号导入、健康检查、其它平台的请求一起排队。

2026-11-16 的现场（`admin.loop_watch` 抓的栈）有两条，都属于这一类：

    http/client.py getresponse → ssl.read              （13.5 秒）
    urllib/request.py https_open → ssl._load_windows_store_certs （13.3 秒）

第一条是 `_attempt()` 里的 `open_chat_stream` / `chat_completions` 直接跑在事件
循环上；第二条是裸 `urlopen` 每条新连接都重扫 Windows 证书库（已由
`admin.upstream_http.install_ssl_defaults()` 统一成共享上下文）。

现在：鉴权 + 选号 + 建连（`_acquire`）、记账（`_bill`）全部丢进工作线程，
事件循环只负责转发字节。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time

from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse

from admin.platform import shared
from admin.platform.marvis.core import client, endpoints
from admin.platform.marvis.services import accounts as marvis_accounts

logger = logging.getLogger("admin.platform.marvis.gateway")

PUBLIC = endpoints.PLATFORM
STORAGE = marvis_accounts.PLATFORM
MAX_ATTEMPTS = 3

PATHS = frozenset({
    "/v1/chat/completions",
    "/v1/responses",
    "/v1/messages",
    "/v1/messages/count_tokens",
    "/v1/models",
    "/v1/balance",
})


class _BadRequest(Exception):
    """本地就能判定的错请求（模型不可用 / 空 messages）。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _realm(request) -> str:
    """Marvis 只有国内区。保留这个函数是为了与其它平台同形。"""
    return "cn"


def _client_ip(request) -> str:
    try:
        forwarded = request.headers.get("x-forwarded-for") or ""
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else ""
    except Exception:  # noqa: BLE001
        return ""


def _fail(exc: client.UpstreamError) -> JSONResponse:
    """把**上游账号侧**的失败翻译成客户端能看懂的错误。

    ## 为什么不能把 401 原样透出去（2026-11-16 用户实测踩坑）

    上游说 401 = 「**这个平台账号**的登录态没了」，需要重新扫码。
    但 `401` 在 HTTP 语义里是「调用方没通过身份验证」，而所有 OpenAI 兼容
    客户端（Cherry Studio / LobeChat / 桌面端…）看到 401 都会渲染成
    **「API 密钥无效」** —— 于是用户去翻自己的 Key，而 Key 根本没问题，
    真正要做的是回控制台重扫 Marvis 账号。

    更糟的是「首次录入账号后的第一次调用」正好撞在这个窗口里：授权码换出
    的票要等一次续签/钱包同步才算完全生效，第一次调用偶发 401，用户就被
    指向了完全错误的方向。

    ## 映射规则

      · 账号登录态问题（`auth` / `credential_expired`）→ **503** + 明确文案，
        503 在客户端里是「服务暂时不可用」，不会被当成 Key 错误；
      · 其余保持 `exc.status`（402/429/5xx 本来就是上游语义，客户端认）。

    真正的 Key 问题在 `shared.authenticate()` 里就 401 掉了（`invalid_api_key`
    / `missing_api_key`），不会走到这里 —— 所以这里把 401 换掉不会掩盖真问题。
    """
    status = exc.status or 502
    if status < 400 or status > 599:
        status = 502
    code = exc.code or "upstream_error"
    message = exc.message
    # ------------------------------------------------------------------
    # 签名器不可用（fail-closed）—— **请求根本没发出去**
    # ------------------------------------------------------------------
    #
    # 这一类必须排在最前面：它不是上游给的结论，是网关自己拒的。
    # 用 503 而不是 4xx/5xx 的原因：这是「本机环境没配好」，不是客户端的错，
    # 客户端会按 Retry-After 退避重试，也不会误渲染成「API 密钥无效」。
    #
    # `upstream_account: False` 很关键 —— 上层任何 failover / 号池调度逻辑
    # 看到这个字段就不该再换号（换号也签不出来）。
    if exc.kind == "signer_unavailable":
        detail = (exc.detail or "").strip()
        return JSONResponse(
            {"error": {
                "type": "signature_unavailable",
                "code": "signer_unavailable",
                "message": (
                    "Marvis 客户端指纹签名器不可用，网关已按 fail-closed 拒绝本次"
                    "请求（请求一个字节都没发给上游）。这与你的 API Key、"
                    "Marvis 账号状态都无关，换号 / 重扫码 / 换 Key 都不会好。"
                    "处理方式：① Windows 上确认 beacon_wrapper.dll（含依赖）在位、"
                    "能加载，控制台 marvis 状态里 uskey.mode 应为 sdk；"
                    "② Linux 上起 wine 签名服务并配置 MARVIS_USKEY_SERVICE；"
                    "③ 临时确需放行（会撞 4100404 并污染账号池）可设 "
                    "MARVIS_USKEY_FAIL_CLOSED=0，仅限排查用。"
                    + ("诊断：%s" % detail[:300] if detail else "")),
                "upstream_account": False}},
            status_code=503,
            headers={"Retry-After": "30"})
    # ------------------------------------------------------------------
    # 内容风控（4100406）单独处理 —— 这是**请求内容**的问题
    # ------------------------------------------------------------------
    #
    # 为什么必须换掉状态码（2026-11-17 用户实测）：
    #
    #   上游 403 传到 Claude Code / Codex 会被渲染成 **「API 密钥无效」**，
    #   于是用户去翻 Key、去重扫码、去换号 —— 三个方向全是错的：
    #   Key 是好的、号是好的、登录态也是好的，是**提示词首行命中黑名单**。
    #
    #   400 + `content_filter` 是 OpenAI 语义里「请求内容不被接受」的标准表达，
    #   客户端会原样显示 message，不会误导。也不给 `upstream_account`
    #   （它不是账号问题，别让上层去 failover 换号）。
    if exc.kind == "content_block":
        detail = (exc.detail or "").strip()
        return JSONResponse(
            {"error": {
                "type": "content_filter",
                "code": "content_blocked",
                "message": (
                    "上游内容风控拦截：系统提示词首行命中腾讯黑名单"
                    "（与你的 API Key、Marvis 账号都无关，换号也没用）。"
                    "网关已尝试把首行 `You are` 改写成 `You're` 仍被拦，"
                    "说明命中的是别的形态，请把原始提示词首行发维护者。"
                    + ("上游原文：%s" % detail[:300] if detail else "")),
                "upstream_account": False}},
            status_code=400)
    # ------------------------------------------------------------------
    # 设备 / IP 风控（4100404）—— 与内容风控**完全不同**的一类
    # ------------------------------------------------------------------
    #
    # ## 为什么必须单独一个分支（2026-11-17 用户实测）
    #
    # 它以前跟内容风控共用一条路径，后果是把「平台侧封禁」说成了「提示词问题」，
    # 用户按前者的提示去改提示词，怎么改都没用 —— 因为**连最简请求都过不了**：
    #
    #     {"model":"main-auto","messages":[{"role":"user","content":"hi"}]}
    #     -> 403 {"code":4100404,"msg":"客户端通用错误:请求被风控拦截"}
    #
    # 四个号全部如此，而**同一份凭证的钱包接口 `/v3/marvis_get_wallet` 回 200**
    # （余额正常）—— 说明凭证没过期，是模型网关这一侧的身份判定被拒。
    #
    # ## 2026-11-18 更正：根因不是 IP，是两个空头
    #
    # 旧结论写的是「出口 IP / 设备画像被风控，只能换 IP 或等」——**那是错的**。
    # 当时全部实验都用随机 uskey，几个变量搅在一起，分不出是谁在起作用。
    #
    # 三组单变量对照（同一账号、同一凭证、同一出口）：
    #
    #     scene=preload_summary + 空 conv/rid + 真签 uskey -> 403 4100404
    #     scene=agent_chat      + 空 conv/rid + 真签 uskey -> 403 4100404
    #     scene=agent_chat      + 有 conv/rid + 真签 uskey -> 200 ✅
    #
    # 另一组（证 uskey 也必须真签）：
    #
    #     真签 uskey -> 无 403
    #     随机 uskey -> 403 4100404
    #
    # ⇒ 真正被查的是三样：**uskey 必须是 Beacon SDK 真签**、
    #    **`Marvis-Conversation-ID` 非空**、**`Marvis-Response-ID` 非空**。
    #    IP 从头到尾没参与 —— 换出口只是碰巧碰到别的变量变了，属于误判。
    #
    # 现在这三样都在 `endpoints.llm_headers()` 里兜住了（缺省自动生成 id、
    # uskey 走 `uskey_signer`），所以这条分支正常不该再命中。
    # 真命中时的文案仍然保留「别改提示词、别重扫码、别换 Key」——
    # 那三条确实没用，只是再加一条：先确认签名器和 id 有没有被绕过。
    if exc.kind == "risk_control":
        detail = (exc.detail or "").strip()
        return JSONResponse(
            {"error": {
                "type": "upstream_risk_control",
                "code": "risk_control",
                "message": (
                    "腾讯 Marvis 回 4100404「请求被风控拦截」。"
                    "这不是提示词内容的问题（改提示词没用），也不是账号登录态问题"
                    "（凭证钱包接口仍然正常）。上游查的是客户端身份三件套："
                    "uskey 是否为 Beacon SDK 真签、Marvis-Conversation-ID 是否非空、"
                    "Marvis-Response-ID 是否非空。"
                    "处理方式：① 确认网关发的是真签 uskey（本机会加载 "
                    "beacon_wrapper.dll，缺失时会导致随机填充降级）"
                    "② 换号 / 重扫码 / 换 Key 都不起作用，不要在这些方向浪费时间"
                    "③ 确实三项都正常仍被拦，再考虑换出口 IP。"
                    + ("上游原文：%s" % detail[:200] if detail else "")),
                # 平台侧问题，但不是「换个号就好」的那种账号问题
                "upstream_account": False}},
            # 429 语义最贴切：客户端会按 Retry-After 退避，而不是当成 Key 错误
            status_code=429,
            headers={"Retry-After": "600"})
    if status == 401 or exc.kind == "auth" or code == "credential_expired":
        status = 503
        if "重新扫码" not in message and "授权登录" not in message:
            message = ("Marvis 平台账号的登录态已失效（与你的 API Key 无关）："
                       "请在控制台「授权登录」重新扫码。原始信息：%s" % message)
        else:
            message = ("%s —— 这是 Marvis 平台账号的问题，你的 API Key 是有效的。"
                       % message)
    elif status == 400:
        # 参数类 400 必须带出**上游原文**。以前只有一句「模型网关失败（HTTP 400）」，
        # 客户端 / 控制台 / 日志三处都看不出是哪个字段踩的 —— 2026-11-17 排查
        # `response_format: json_object` 时正是卡在这一句上，只能逐个字段手试。
        # 上游现在回的是 `{"code":3000101,"msg":"invalid parameter"}`，原样带上。
        upstream = (exc.detail or "").strip()
        # 上下文超限：重试都失败说明这次真的装不下（路由到哪个后端都小）。
        # 这时**不能**说「模型网关失败」—— 用户会以为服务坏了，一直重试。
        # 要说清楚是「这轮对话太长」，并给出唯一有效的动作。
        if _is_context_overflow(exc):
            message = ("这轮对话的上下文太长，Marvis 上游装不下（与你的 API Key、"
                       "账号额度都无关）。已自动重发 %d 次仍未通过，说明不是"
                       "临时路由问题。请新建一个对话继续，或让客户端清理历史后重试。"
                       % _MAX_CTX_RETRIES)
            if upstream:
                message = "%s；上游原文：%s" % (message, upstream[:200])
            return JSONResponse(
                {"error": {"type": "context_too_long",
                           "code": "context_length_exceeded",
                           "message": message,
                           "upstream_account": False}},
                status_code=400)
        if upstream:
            message = "%s；上游原文：%s" % (message, upstream[:300])
        # 参数错误不是账号问题，别让客户端以为是号池挂了
    return JSONResponse(
        {"error": {"type": exc.kind, "code": code, "message": message,
                   # 给客户端一个可读的提示位：明确「不是 Key 的问题」
                   "upstream_account": status == 503}},
        status_code=status)


async def _body_of(request: Request) -> dict:
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def _validate_model(body: dict) -> str:
    """归一模型 id，并在本地拦掉已知持续失败的模型。

    ⚠ 不可用模型返回 **404**（模型级错误），不是 401。
    401 会被上层当成「账号登录失效」去 failover 到每一个账号，
    一个坏模型就能把整池好号全标成失效。
    """
    raw = str(body.get("model") or endpoints.DEFAULT_MODEL)
    slot = endpoints.canonical_model_id(raw)
    if endpoints.is_unavailable_model(slot):
        raise _BadRequest("model_not_found",
                          "模型 %s 在 Marvis 上游不可用（已确认持续失败）" % raw)
    if slot not in endpoints.DISPLAY_BY_SLOT:
        raise _BadRequest("model_not_found",
                          "未知模型 %s；可用：%s" % (
                              raw, "、".join(endpoints.DISPLAY_BY_SLOT)))
    if not (body.get("messages") or body.get("input")):
        raise _BadRequest("invalid_request", "请求缺少 messages")
    return slot


def _bill(key, account, model, usage, *, request, started=None, error="",
          stats: dict | None = None):
    """落一条 usage_logs。**绝不抛**（记账失败不能影响转发）。

    ⚠ `model` 必须是**可读名**（`deepseek-v4.1-flash`），不是上游槽位名
    （`main-auto`）—— 见 `_billing_model` 的说明。这一列是控制台
    「使用记录 / 用量统计 / 模型筛选」的唯一数据源，写成槽位名会让
    `model_configs` 的表全部对不上，前端只能原样显示 `main-auto`。
    """
    try:
        from admin.db import SessionLocal

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
                model=model,
                usage=usage if isinstance(usage, dict) else {},
                public_platform=PUBLIC,
                storage=STORAGE,
                error_kind=error, client_ip=_client_ip(request),
                use_case="marvis-chat", seq=1,
                ttfb_ms=ttfb, latency_ms=latency,
                reasoning_effort=shared.reasoning_effort_of_request(request))
    except Exception:  # noqa: BLE001
        logger.exception("Marvis 记账失败")


def _billing_model(slot: str) -> str:
    """记账用的模型名：上游槽位 → **对外可读名**。

    ## 为什么这一列不能存槽位名（2026-11-17 用户实测反馈）

    控制台的「使用记录 / 用量统计 / 模型下拉筛选」都是拿 `usage_logs.model`
    去 `model_configs.model_id` 里查展示名的。而 Marvis 侧：

        model_configs 登记的 model_id   → deepseek-v4.1-flash
        usage_logs      记的 model      → main-auto（上游槽位）

    两列永远对不上，前端查不到展示名就退回原样输出，于是整张表显示
    `main-auto`、下面还挂一行「思考：默认 / 未记录」—— 用户看到的既不是
    自己请求的 `deepseek-v4.1-flash-marvis`，也认不出这到底是什么模型。

    ## 为什么不改 model_configs 去存槽位名

    反过来更糟：`model_configs` 是模型目录的**对外身份**，槽位名是上游实现
    细节。改成 `main-auto` 会让 `/v1/models` 重新暴露槽位名、dispatch 的
    跨平台归一也要跟着改（`readable_model_id` 存在的全部理由就是这件事）。

    ## 所以：落库时就写可读名

    `endpoints.readable_model_id()` 查不到时原样返回，对没有槽位映射的平台
    是恒等变换，不会误伤。

    不写平台后缀（`-marvis`）：`usage_logs.platform` 已经单列了平台，
    再拼进 model 会让同一模型在统计里裂成两条，也会让筛选下拉出现
    `deepseek-v4.1-flash` 与 `deepseek-v4.1-flash-marvis` 两个选项。
    """
    try:
        readable = endpoints.readable_model_id(slot)
    except Exception:  # noqa: BLE001 - 映射失败就记原始值，不能挡住记账
        return str(slot or "")
    return str(readable or slot or "")


async def _bill_async(*args, **kwargs) -> None:
    """在工作线程里记账（`_bill` 是同步 DB 写，不能占事件循环）。

    用 `shield` 包住：客户端中途关掉页面时，流式生成器的 `finally` 会立刻收到
    `CancelledError`；不 shield 的话记账直接被取消 —— 用户已经消耗的额度就
    查不到了。shield 之后内部任务照旧跑完，只是本协程不再等它。
    """
    try:
        await asyncio.shield(asyncio.to_thread(_bill, *args, **kwargs))
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - 记账失败绝不影响响应
        logger.exception("Marvis 记账线程异常")


# ---------------------------------------------------------------------------
# 腾讯内容风控指纹改写（修 403 / 4100406）
# ---------------------------------------------------------------------------
#
# ## 现象
#
# 上游 `yybadaccess.3g.qq.com` 对 `messages[]` 里的 **system 正文**做**字面量**
# 黑名单匹配（与 buddy / workbuddy 同一套内容风控）。命中即整条请求被拒：
#
#     HTTP 403 {"code": 4100406, "msg": "非法请求"}
#
# 业务码不进响应体，上层框架常把它渲染成「鉴权失败 / API key invalid」之类的
# 无关错误 —— 客户端既看不出是内容被拦，也看不出拦的是哪一句。
#
# ## 为什么中间网关必须自己做一遍
#
# 客户端插件（dsh-providers-hub 的 `tencent-fingerprint.js`）已有同一张表，但
# 它按 **provider 白名单**放行：`TENCENT_PROVIDERS = ['buddy', 'workbuddy',
# 'marvis']`。客户端直连 marvis 时 `provider='marvis'`，改写生效；一旦中间隔了
# 任何网关，`provider` 就变成网关自己的 id，改写短路、原文原样透传 → 403。
# ⇒ 这类改写必须在网关侧再做一遍，本表即为此存在。
#
# ## 改写边界（有意不扩大）
#
#   · **只改 system**；user / assistant / tool 一律不碰 —— 改用户内容或会话
#     历史会污染对话语义，也违背「网关不篡改用户输入」。
#   · 只换写法、不删句子：替换值与客户端插件逐字一致，语义等义。
#   · **不命中的文本逐字符不变**（先 `search` 预检，命中才 `sub`）：上游按前缀
#     缓存计费，无谓的字符串重建会让缓存失效、白吃一次全量重算。
#   · `MIRAIHUB_MARVIS_SANITIZE_TENCENT=0` 可整体关闭，恢复原样透传。
#
# ## 表的来源与匹配语义
#
# 7 条规则逐条移植自客户端插件的 `CONTENT_FINGERPRINTS`：`find` 是**字面量**
# （`re.escape` + `re.IGNORECASE`，上游匹配大小写不敏感）；第 4 条用插件登记的
# `findRegex`（容忍空白归一与可选词漂移）。
#
# ⚠️ 各条的**锚点语义不同**，不要统一加 `^`：
#
#   1. ZCode 分支名常量 —— 位置无关
#   2. Claude Code 身份句 —— 行首锚定（前接任意字符即放行）
#   3. Claude Code 反馈句 —— 位置无关
#   4. DSH 身份首句 —— 行首锚定
#   5. DSH Web 面开头两句 —— 位置无关
#   6. DSH `@` 引用说明句 —— 位置无关
#   7. SDK 计费头字样 —— 位置无关
#
# 本实现**不**给任何一条加行首锚定：改写范围宁可略宽 —— 漏改一处就是整轮
# 403，多改一处只是语义等价的措辞变化（与客户端插件同一取舍）。
#
# ## 2026-11-17 实测修正：上面这套「逐句替换」不成立
#
# 逐句对上游做 A/B（四个号、`yybadaccess.3g.qq.com`，每条只改系统提示词）：
#
#     规则2 原文  "You are Claude Code, Anthropic's official CLI for Claude."   -> 403
#     规则2 替换  "You are Claude Code, Anthropic's CLI for Claude"             -> 403
#     规则4 原文  "You are an AI coding agent, powered by the DeepSeek Harness" -> 200
#     规则4 替换  "You are an AI coding assistant, powered by a large language
#                  model"                                                      -> 403
#
# 两个结论，都与直觉相反：
#
#   1. **替换值本身也被拦** —— 逐句换措辞只是换一个同样命中的句子，等于白改；
#      规则 2 从上线起就没生效过。
#   2. **规则 4 会主动制造 403** —— 它的原文本来是放行的，改写后反而被拦。
#      这条规则每命中一次，就是把一条本来正常的请求改成失败。
#
# 命中形态实测（同一批账号）：
#
#     "You are Claude Code."                403     "# You are Claude Code."      200
#     "  You are Claude Code."（前导空格）   403     "- You are Claude Code."      200
#     "\nYou are Claude Code."（前导换行）   403     "Preamble.\nYou are ..."      200
#     "you are claude code."（全小写）       403     "You are not Claude Code."    200
#     "You are Codex."                      403     "You're Claude Code."         200
#     "You are a Claude agent"              403     user 角色带同一句             200
#
# ⇒ 上游匹配的是**第一条非空行**（前导空白会跳过）、**大小写不敏感**、
#   形态是 `You are <产品名>`；加一个 `#` / `-` 前缀能绕过，但那是把正文改成
#   标题，不可取。`You are` → `You're` 是唯一既等价又通过的改法。
#
# 所以现在分两层：
#   · 上面 7 条**只保留确实需要的**，替换值一律改成 `You're` 形态（不再自造新句）；
#   · 新增 `_rewrite_identity_line()` 作为**兜底**：首行只要是 `You are ` 就改写。
#     它覆盖 Claude Code、Codex 以及以后新增的任何客户端身份句 —— 不再需要
#     每次客户端升级就来补一条规则。

#: 改写开关（默认开）：设为 0 / false / off / no 时**完全跳过**改写，请求原样
#: 发上游。这是「替用户改写内容」的行为，必须能一键退回原样透传。
SANITIZE_TENCENT_ENV = "MIRAIHUB_MARVIS_SANITIZE_TENCENT"
_SANITIZE_OFF_VALUES = frozenset({"0", "false", "off", "no"})

#: 参与改写内容风控的角色。`developer` 是 OpenAI 新规范里的 system 等价物，
#: Codex 的身份句就放在 developer 段 —— 漏掉它等于 Codex 全量 403。
_SANITIZE_ROLES = frozenset({"system", "developer"})


def _tencent_sanitize_enabled() -> bool:
    """开关当前状态。

    每次调用现读环境变量而不是模块级求值：开关是排障用的，改 `.env` 重启即
    生效；也便于测试逐例切换两种状态而不必重载模块。
    """
    raw = (os.getenv(SANITIZE_TENCENT_ENV) or "").strip().lower()
    return raw not in _SANITIZE_OFF_VALUES


_TENCENT_FINGERPRINT_RULES = (
    # 1. ZCode 内建常量（每个 git 仓库会话都会被注入）。位置无关。
    (re.compile(re.escape("Main branch (you will usually use this for PRs)"), re.I),
     "Main branch (PRs usually go here)"),
    # 2. Claude Code 身份句。**行首锚定**；刻意不带尾句点，一条覆盖两种常量形态。
    #
    #    ⚠ 替换值必须是 `You're` 形态：以前写成 "You are Claude Code,
    #    Anthropic's CLI for Claude" —— 仍然 `You are` 开头，实测依旧 403，
    #    等于这条规则从上线起就是空转（2026-11-17 实测）。
    (re.compile(re.escape("You are Claude Code, Anthropic's official CLI for Claude"), re.I),
     "You're Claude Code, Anthropic's CLI for Claude"),
    # 3. Claude Code 反馈句（每轮必带的固定段落）。位置无关。
    #    find 必须写整句（含 URL）：上游认逐字符，砍掉半句等于静默空跑。
    (re.compile(re.escape("To give feedback, users should report the issue at "
                          "https://github.com/anthropics/claude-code/issues"), re.I),
     "To give feedback, users should report the issue at the project issue tracker"),
    # 4. DSH 身份首句（**行首锚定**）。
    #
    #    ⚠ **原文实测是放行的**（`You are an AI coding agent, powered by the
    #    DeepSeek Harness` -> 200），而旧替换值
    #    `You are an AI coding assistant, powered by a large language model`
    #    **反而是 403** —— 也就是说这条规则以前每命中一次，就在把一条正常
    #    请求改成失败（2026-11-17 实测）。保留规则但换成 `You're` 形态，
    #    从此它只是同义改写、不再是陷阱。
    (re.compile(r"You\s+are\s+an?\s+AI\s+(?:coding\s+)?agent\s*,?\s*powered\s+by\s+"
                r"(?:the\s+)?DeepSeek\s+Harness", re.I),
     "You're an AI coding agent, powered by the DeepSeek Harness"),
    # 5. DSH Web 运行面的开头两句。位置无关。
    #    触发串**跨句边界**（前半句、后半句各自都放行），find 必须是前两句的
    #    并集；砍半 = 静默空跑。改写只去掉品牌串，其余逐字不动。
    #
    #    替换值同样用 `You're` 开头：这条改写完若仍以 `You are` 起句，兜底的
    #    首行改写会再动它一次（两次改写本身无害，但「一条规则一次改写」
    #    更好对账，也让前缀缓存的计算可预测）。
    (re.compile(re.escape(
        'You are interacting with the user through the DeepSeek Harness Web GUI '
        'whose URL the runtime context names. When the user refers to "this page", '
        '"this GUI", or "this app" without naming another target, they mean this GUI.'),
        re.I),
     "You're interacting with the user through the harness Web GUI "
     'whose URL the runtime context names. When the user refers to "this page", '
     '"this GUI", or "this app" without naming another target, they mean this GUI.'),
    # 6. DSH 的 `@` 文件引用说明句。位置无关，同样跨句边界。
    #    只调整语序；后半段（目录 list / 用 read / 不得声称已读过 / 引号语法）
    #    是防模型编造文件内容的核心约束，一个字都不许删。
    (re.compile(re.escape(
        "Tokens prefixed with @ are paths the user explicitly referenced. "
        "Relative paths resolve from the workspace root"), re.I),
     "Tokens prefixed with @ are paths the user explicitly referenced. "
     "Paths resolve relative to the workspace root"),
    # 7. 客户端 SDK 的计费头字样。位置无关；连裸词也会被拦。
    (re.compile(re.escape("x-anthropic-billing-header"), re.I),
     "x-client-billing-header"),
)


#: 首行身份句：`<缩进>You are<空白>...`，大小写不敏感。
#:
#: 只锚 `^`＋缩进，**不锚整句** —— 上游拦的是「`You are` + 产品名」这个宽形态，
#: 不是某一句原文。锚整句的后果就是客户端一改措辞（Claude Code 从
#: `Anthropic's official CLI for Claude` 改成 `Claude Agent SDK`）就失效。
_IDENTITY_LINE = re.compile(r"^([ \t]*)you([ \t]+)are([ \t]+)", re.I)


def _youre(match: re.Match) -> str:
    """把命中的 `You are ` 换成 `You're `，保留缩进、空白与大小写形态。

    大小写要**跟着原样走**：全大写句子里混一个 `You're` 会让模型读到风格
    突变的提示词；`YOU ARE` 要变 `YOU'RE`，`you are` 要变 `you're`。
    """
    lead, gap, tail = match.group(1), match.group(2), match.group(3)
    word = match.group(0)[len(lead):len(lead) + 3]      # 原句里的 "You"
    if word.isupper():
        replacement = "YOU'RE"
    elif word[:1].isupper():
        replacement = "You're"
    else:
        replacement = "you're"
    return lead + replacement + tail


def _rewrite_identity_line(text: str) -> tuple[str, int]:
    """改写**第一条非空行**开头的 `You are `，返回 `(文本, 是否命中)`。

    ## 为什么只改第一行

    上游只检查第一条非空行（实测见上方规则表的对照数据）：
    `Preamble.\\nYou are Claude Code.` 是 200，而同样的句子放在第一行就是 403。
    改第一行就够，改更多行只会无谓地动到用户的提示词正文。

    ## 为什么前导空行要跳过

    `"\\nYou are Claude Code."` 实测仍是 403 —— 上游会跳过前导空白再匹配。
    只认 `text.startswith("You are")` 会漏掉这种形态。
    """
    if not text:
        return text, 0
    lines = text.split("\n")
    for index, line in enumerate(lines):
        if not line.strip():
            continue                     # 前导空行：上游也会跳过，继续往下找
        if _IDENTITY_LINE.match(line) is None:
            return text, 0               # 首条非空行不是身份句 → 整段不动
        lines[index] = _IDENTITY_LINE.sub(_youre, line, count=1)
        return "\n".join(lines), 1
    return text, 0


def rewrite_tencent_fingerprints(text: str) -> tuple[str, int]:
    """按上表做窄改写，返回 `(改写后的文本, 命中的规则条数)`。

    **零重建**：开关关闭、空文本、非字符串、无命中时原样返回**入参对象** ——
    上游按前缀缓存计费，无谓的字符串重建会让缓存失效、白吃一次全量重算。
    """
    if not isinstance(text, str) or not text:
        return text, 0
    if not _tencent_sanitize_enabled():
        return text, 0
    out = text
    hits = 0
    for pattern, replacement in _TENCENT_FINGERPRINT_RULES:
        if pattern.search(out) is None:      # 预检：不命中连 sub 都不进
            continue
        out = pattern.sub(replacement, out)
        hits += 1
    # 兜底：首行身份句。放在具体规则**之后** —— 上面某些规则也会重写首行
    # （规则 4 会把 `You are an AI ...` 换成 `You're ...`），先跑兜底的话
    # 就看不到它们的结果了。
    out, identity_hits = _rewrite_identity_line(out)
    return out, hits + identity_hits


def _flatten_content(content) -> str | None:
    """把 OpenAI 多段 content 数组压成纯文本。压不出文本时返回 `None`。

    ## 为什么必须压（2026-11-18 实测）

        上游接受：content 是字符串
        上游接受：role=system + content 多段数组（text 段）
        上游拒绝：role=developer + content 多段数组  -> 400 3000101
        上游拒绝：content 里带 image_url              -> 400 3000101

    也就是说「数组」本身不一定非法，但一旦和 `developer` 或图片段组合就整条
    400。上游不告诉你是哪个字段，只回 `invalid parameter`，所以这里直接把
    三种形态统一成最保守的「纯字符串」——Marvis 是纯文本模型，本来也吃不下
    图片，`image_url` 段保留只会让整条请求失败。
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    for seg in content:
        if not isinstance(seg, dict):
            if isinstance(seg, str):
                parts.append(seg)
            continue
        kind = str(seg.get("type") or "")
        if kind in ("text", "input_text") or not kind:
            text = seg.get("text")
            if isinstance(text, str):
                parts.append(text)
            continue
        # image_url / input_image / audio / file —— 上游不支持，丢弃
    return "\n".join(parts) if parts else ""


def _normalize_roles(messages: list) -> tuple[list, bool]:
    """把 `developer` 归一成 `system`，并压平多段 content。

    ## `developer` 为什么必须改（2026-11-18 实测）

        role=developer + content 字符串 -> 200
        role=developer + content 数组   -> 400 3000101
        role=system    + content 数组   -> 200

    `developer` 在 OpenAI 新规范里就是 `system`，但上游只对 `system` 放过
    数组形态；发 `developer` 等于走进一条它没覆盖的分支。DSH / Codex 这类
    客户端第一条就是 `developer`，且正文常常是多段数组 —— 于是它们的请求
    100% 400，而手工 curl 一条 `role=user` 却是 200，非常容易被误判成
    「网关不稳定」。

    归一成 `system` 后语义不变，两个分支都能过。
    """
    changed = False
    out: list = []
    for message in messages:
        if not isinstance(message, dict):
            out.append(message)
            continue
        role = str(message.get("role") or "")
        content = message.get("content")

        new_content = content
        if isinstance(content, list):
            flat = _flatten_content(content)
            if flat is not None and flat != content:
                new_content = flat

        if role == "developer" or new_content is not content:
            out.append(dict(message,
                            role=("system" if role == "developer" else role),
                            content=new_content))
            changed = True
        else:
            out.append(message)
    return (out if changed else messages), changed


def _sanitize_messages(messages: list) -> tuple[list, int]:
    """对**每一条 system / developer 消息**做指纹改写，其余角色原样不动。

    ## 为什么 `developer` 也必须改（2026-11-17 实测）

    OpenAI 新规范里 `developer` 就是 `system`，Codex 的身份句正是放在
    `input[0]`（role=`developer`）里。以前这里只认 `role == "system"`，
    于是 Codex 的请求**完全没被改写**，直连上游验证：

        system    + "You are a coding agent running in the Codex CLI ..." -> 403
        developer + 同一句                                                -> 403

    两个角色上游都会查，所以两个都必须在改写范围内。

    为什么不是只改 `messages[0]`：非首位 system 同样会被上游拦，只改第一条
    等于留一条随时会踩的路径。

    命中时**新建 dict**，不就地改调用方的 body（同一份 body 后面还要取会话
    粘性键、记账）；零命中时连列表都不重建。
    """
    out = list(messages)
    hits = 0
    changed = False
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            continue
        if str(message.get("role") or "") not in _SANITIZE_ROLES:
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        rewritten, n = rewrite_tencent_fingerprints(content)
        if n == 0:
            continue
        out[index] = dict(message, content=rewritten)
        hits += n
        changed = True
    return (out if changed else messages), hits


# ---------------------------------------------------------------------------
# `response_format` 归一（修 400 / 3000101）
# ---------------------------------------------------------------------------
#
# ## 现象
#
# 客户端带 `response_format: {"type": "json_object"}` 时，请求被上游判成
# **非法参数**，整条 400：
#
#     HTTP 400 {"code":3000101,"msg":"invalid parameter"}
#
# 这个 400 到客户端只剩一句「模型网关失败（HTTP 400）」，看不出是哪个字段
# 踩的 —— 2026-11-17 排查时正是卡在这里：账号 active、凭证刚续签、钱包接口
# 正常、换号也没用，因为问题根本不在账号侧。
#
# ## 实测边界（2026-11-17，四个号逐个直连 `yybadaccess.3g.qq.com` 复核）
#
#     {"type": "text"}                                  -> 200
#     {"type": "json_schema", "json_schema": {...}}     -> 200
#     {"type": "json_object"}                           -> 400 3000101
#
# 也就是说上游只实现了 `text` 与 `json_schema` 两种，`json_object` 这个
# OpenAI 经典模式**没实现**，且不认就直接报参数非法。
#
# ## 等价表达
#
# `json_object` 的语义是「这次回答必须是合法 JSON 对象」，不约束字段。
# 用 `additionalProperties: true` 的宽松 object schema 表达完全等价 ——
# 只约束形状、不约束键，模型照样必须吐 JSON：
#
#     改写后实测输出：{"name": "Elena Marchetti", "age": 34}

#: 上游真正实现了的 `response_format.type`。
_RESPONSE_FORMAT_OK = frozenset({"text", "json_schema"})

#: `json_object` 的等价物：不约束字段、只约束「是一个 JSON 对象」。
_JSON_OBJECT_AS_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "json_object",
        "schema": {"type": "object", "additionalProperties": True},
        "strict": False,
    },
}


def _normalize_response_format(value) -> tuple[dict | None, str]:
    """把 `response_format` 归一成上游认的形状，返回 `(新值, 说明)`。

    新值为 `None` 表示「翻译不了，这个字段整个摘掉」：宁可少一个 JSON 约束，
    也不要整条请求 400 —— 客户端拿到出字总比拿到一句无用的参数错误强。
    """
    if not isinstance(value, dict):
        return None, "不是 dict，已摘除"
    kind = str(value.get("type") or "").strip().lower()
    if kind in _RESPONSE_FORMAT_OK:
        return value, ""
    if kind == "json_object":
        return dict(_JSON_OBJECT_AS_SCHEMA), (
            "json_object → json_schema（上游未实现 json_object）")
    return None, "未知 type=%r，已摘除" % kind


#: 上游回 `3000101 invalid parameter` 时按顺序剥掉的可选字段。
#:
#: 网关对 OpenAI 扩展字段是**原样透传**的（那是客户端的自由），代价是上游
#: 一旦不认就整条 400。上游又不告诉你是哪个字段，只回一句 `invalid parameter`。
#: 所以留一条降级路径：撞上 3000101 就按这张表逐个摘，摘掉后能过就照常出字。
#: 顺序是「最可能被误加 → 最不可能」：`response_format` 已在前置归一里处理，
#: 剩下的按客户端常见溢出来排。
_DEGRADE_FIELDS = (
    "response_format",
    "max_completion_tokens",
    "parallel_tool_calls",
    "reasoning_effort",
    "stream_options",
    "metadata",
    "service_tier",
    "store",
    "logprobs",
    "top_logprobs",
    "logit_bias",
    "n",
    "modalities",
    "seed",
    "user",
    "top_k",
    "repetition_penalty",
    "prompt_cache_key",
    "session_id",
    # 工具相关放最后：剥掉它们会让模型不再调工具，能出字但功能降级，
    # 所以宁可先试完前面那些纯装饰字段。
    "tool_choice",
    "tools",
    "functions",
)


def _degrade_body(body: dict, dropped: set[str]) -> dict | None:
    """按 `_DEGRADE_FIELDS` 摘掉**一个**还没摘过的字段，返回新 body。

    没有可摘的字段时返回 `None`（说明这条请求已经没什么可退的了）。
    `dropped` 由调用方持有，保证同一个字段不会反复摘、也不会无限重试。
    """
    for field in _DEGRADE_FIELDS:
        if field in body and field not in dropped:
            dropped.add(field)
            out = dict(body)
            out.pop(field, None)
            return out
    return None


# ======================================================================
# 上下文超限重试（400 / 413 + 3000100）
# ======================================================================
#
# ## 为什么是「原样重试」而不是「裁剪上下文」
#
# 我第一版写的是「按比例删掉最老的消息」，这是**错的**：
#
#     用户的对话历史就是任务本身。删一半 = 模型看不到前面的约束、
#     工具结果、已确认的决策 —— 它不会「少看几轮」，它会**把任务做错**。
#     一个悄悄做错的任务，比一条明确的报错糟糕得多。
#
# 正确做法是重试，理由是 `main-auto` 这个槽位是**自动路由**：
#
#     同一个模型名，上游每次派到哪个后端是它自己决定的。实测同一段
#     40 万字符的历史，一次落到 `deepseek-v4-1-flash-261017`（装不下），
#     另一次落到 `default`（装得下）。
#
# 所以超限不等于「内容真的太大」，也可能「这次运气不好路由到小窗口」。
# **重发一次，让上游重新选路**，能过就过；过不了再如实报错。
#
# ## 为什么不换号
#
# 上下文长度是请求本身的属性，换号发也一样长。换号只会把好号
# 一个个写成失败记录。所以不 note_failure、不进 exclude。

#: 上游表达「上下文超限」的各种措辞。
#:
#: 不同后端说法不一样，换个路由后端就换一套措辞。全部按小写子串匹配，
#: 判据取「够具体、不会误伤」的短语 —— 不能只匹配 `token`，否则把
#: `invalid token` 这类参数错也吃进来。
_CONTEXT_OVERFLOW_WORDS = (
    "max input token",
    "maximum context length",
    "context length exceeded",
    "context_length_exceeded",
    "input is too long",
    "prompt is too long",
    "too many tokens",
    "exceeds the maximum",
    "3000100",
)

#: 上下文超限时**原样重发**几次。
#:
#: 每次重发都是一次重新路由的机会。给到 4 次是因为实测一次失败后
#: 下一次就换到能装下的后端了；再多只是在慢请求上浪费时间。
_MAX_CTX_RETRIES = 4

#: 重试之间的间隔。不纯粹是退避 —— 上游的路由决策带一点时间相关性，
#: 间隔一两秒能显著提高「换一个后端」的概率。
_CTX_RETRY_GAP = 1.2


def _is_context_overflow(exc: "client.UpstreamError") -> bool:
    """这条错误是不是「上下文太长，这次那个后端装不下」。

    必须在 `_is_param_error` **之前**判断：`3000100` 和 `3000101` 前缀相同，
    但一是内容长度、一是字段不认，处理方式完全相反。
    """
    if int(exc.status or 0) not in (400, 413, 422):
        return False
    blob = ("%s %s" % (exc.detail or "", exc.message or "")).lower()
    return any(word in blob for word in _CONTEXT_OVERFLOW_WORDS)




def _normalize_token_limit(out: dict) -> None:
    """把 `max_completion_tokens` 并进 `max_tokens`（上游只认后者）。

    ## 为什么不能像别的可选字段那样直接摘

    `max_completion_tokens` 是 `max_tokens` 的 **OpenAI 新别名**，语义完全
    相同 —— 不是「多余的装饰」，摘掉就等于**放开输出长度**：模型会一路写到
    上游自己的上限，慢、贵，而且客户端拿到的长度和它要求的对不上。

    ## 实测（2026-11-17，上游 `yybadaccess.3g.qq.com`）

        {"max_completion_tokens": 16}                    -> 400 3000101
        {"max_tokens": 16}                               -> 200

    两个都带时上游照样回 400（它只认 `max_tokens`，多一个不认识的键就不给过），
    所以这里**合并后只留 `max_tokens`**，以更保守的那个值为准：两个都给了就取
    小的 —— 宁可比客户端要的短，也不要比它要的长（长度超了客户端会截断，
    短了只是少写几个字，不会破坏协议）。
    """
    if "max_completion_tokens" not in out:
        return
    alias = out.pop("max_completion_tokens")
    try:
        limit = int(alias)
    except (TypeError, ValueError):
        return
    if limit <= 0:
        return
    current = out.get("max_tokens")
    try:
        current = int(current)
    except (TypeError, ValueError):
        current = 0
    out["max_tokens"] = min(current, limit) if current > 0 else limit


def _prepare_body(body: dict, slot: str) -> dict:
    """把下游请求整理成上游能接受的样子。

    · `model` 换成槽位名；
    · `response_format` 归一（`json_object` → 等价 `json_schema`，见上）；
    · `max_completion_tokens` 并进 `max_tokens`（上游只认后者，见
      `_normalize_token_limit`）；
    · 流式时补 `stream_options.include_usage` —— 少了它上游不返回 usage 帧，
      这张卡片和用量统计就永远是 0；
    · **system 正文过一遍腾讯内容风控指纹改写**（`4100406` 的修法，表见上）。
      `/v1/messages` 与 `/v1/responses` 都经 `_run_normalized` → `chat_completions`
      落到这里，所以只改这一处就覆盖三个入口。
    """
    out = dict(body)
    out["model"] = slot
    _normalize_token_limit(out)
    messages = out.get("messages")
    if isinstance(messages, list):
        # 先做角色 / content 形态归一，再做内容风控改写。
        #
        # 顺序不能反：`developer` + 数组 content 会被上游判 400，而改写函数
        # 只处理字符串 content —— 先归一才能保证该改的都改到。
        messages, roles_changed = _normalize_roles(messages)
        if roles_changed:
            logger.debug("Marvis 消息归一 | developer->system / content 压平")
        out["messages"], hits = _sanitize_messages(messages)
        if hits:
            # 只记命中条数，**不记原文**：system 里可能带用户的 prompt / 路径 / 密钥。
            logger.info("Marvis 内容风控指纹改写 | rules_hit=%d", hits)
    if "response_format" in out:
        shape, note = _normalize_response_format(out["response_format"])
        if note:
            logger.info("Marvis response_format 归一 | %s", note)
        if shape is None:
            out.pop("response_format", None)
        else:
            out["response_format"] = shape
    if out.get("stream"):
        opts = dict(out.get("stream_options") or {})
        opts["include_usage"] = True
        out["stream_options"] = opts
    return out


def _is_param_error(exc: client.UpstreamError) -> bool:
    """这条 400 是不是「参数不被上游接受」（而不是账号 / 额度 / 风控）。

    判据取上游原文：`3000101 invalid parameter`。只有这一类**换号无用、降级
    有效** —— 换号只会把好号一个个撞成「有错误记录」，而降级能真出字。
    """
    if int(exc.status or 0) not in (400, 422):
        return False
    blob = ("%s %s" % (exc.detail or "", exc.message or "")).lower()
    return "3000101" in blob or "invalid parameter" in blob


def _attempt(db, request, key, body: dict, realm: str, *, stream: bool):
    """选号 + 租约 + 调上游。换号语义与其它平台一致。

    ## 参数类 400 走降级，不记账号失败

    上游回 `3000101 invalid parameter` 时，**这个号是好的**、凭证是好的，
    只是请求里某个字段它不认。以前这里直接 `note_failure` + `break`：
    号上留下 `last_err_msg = invalid parameter`、`err_count` +1，用户看到
    「号有错」，而真正要动的是请求体。现在：

      1. 先按 `_DEGRADE_FIELDS` 摘一个字段，**同号重试**；
      2. 摘到能过就出字（并把摘掉的字段记进日志）；
      3. 实在摘不干净，才按原样抛给上层，且**不污染账号**。
    """
    model = str(body.get("model") or "")
    exclude: set[int] = set()
    last_error: client.UpstreamError | None = None
    dropped: set[str] = set()
    # `MAX_ATTEMPTS` 数的是「换了几个号」；参数降级是**同一个号上的同一条
    # 请求**，单独记数，否则「三次机会」会被降级重试吃光。
    attempts = 0
    param_retries = 0
    # 上下文超限的重试计数。和参数降级分开，因为失败原因正交：
    # 一条请求既可能字段不被认（3000101），也可能这次路由到的后端装不下（3000100）。
    ctx_retries = 0
    while attempts < MAX_ATTEMPTS:
        attempts += 1
        # `min_balance=0` = 只要余额 > 0 的号。
        #
        # Marvis 是令牌钱包制：额度用完的号凭证**是好的**，但每次调用都会被上游
        # 打回。不在这里挡掉的话，只能靠「撞一次 → 冷却 → 换号」，而冷却到期后
        # 它又排回队首（这些号从没成功过，`last_used_at` 恒为 NULL，选号按它升序）
        # —— 用户感受到的就是「一直失败，用不了」。NULL 余额（从没同步过）仍放行，
        # 否则新导入的号会被全部误杀。
        account = shared.select_account(
            db, storage=STORAGE, realm=realm, key=key,
            model=model, exclude_ids=exclude, min_balance=0)
        if account is None:
            break
        if not shared.lease(account):
            exclude.add(account.id)
            continue
        started = time.perf_counter()
        try:
            data = marvis_accounts.ensure_token(db, account)
            if stream:
                resp = client.open_chat_stream(
                    data, realm, body, account_id=int(account.id or 0))
                return resp, account, started
            result = client.chat_completions(
                data, realm, body, account_id=int(account.id or 0))
            shared.note_success(account)
            shared.release(account)
            return result, account, started
        except client.UpstreamError as exc:
            shared.release(account)
            # 签名器不可用（fail-closed）：**请求根本没发出去**。
            #
            # 换号必然也签不出来（签名器是进程级/环境级的东西，与账号无关），
            # 所以这里必须立刻停、且**不** note_failure —— 否则环境问题会被
            # 写成「这三个号都被风控」，用户去换号重扫码，全是白费。
            if exc.kind == "signer_unavailable":
                last_error = exc
                logger.error(
                    "Marvis 签名器不可用，fail-closed 拒绝（不换号、不记账号失败，"
                    "与账号无关）| acc=%s | %s",
                    account.id, (exc.detail or exc.message or "")[:200])
                break
            # 内容风控（4100406）：**请求正文**命中上游黑名单。
            # 换号毫无用处 —— 同一个号、别的号都拦这条提示词；而且换号会把
            # 好号一个个写成「有错误记录」，用户会以为号坏了。
            # 所以：不 note_failure、不进 exclude、立刻停。
            if exc.kind == "content_block":
                last_error = exc
                logger.warning(
                    "Marvis 内容风控拦截（换号无效，不记账号失败）| acc=%s | %s",
                    account.id, (exc.detail or exc.message or "")[:200])
                break
            # 上下文超限（400/413 + 3000100）：**原样重发**，靠重新路由过关。
            #
            # 为什么不裁消息：对话历史就是任务本身。删掉一半，模型不会
            # 「少看几轮」，它会看不到前面的约束和已确认的决策，**把任务做错**。
            # 一个悄悄做错的任务，比一条明确的报错糟糕得多。
            #
            # 为什么原样重发有可能过：`main-auto` 是自动路由槽位，同一个模型名
            # 每次派到哪个后端由上游决定。实测同一段 40 万字符的历史，
            # 一次落到装不下的后端（3000100），下一次落到装得下的后端（200）。
            #
            # 为什么不换号：内容长度是请求本身的属性，换号发也一样长，
            # 只会把好号挨个写成失败记录。所以不 note_failure、不进 exclude。
            if _is_context_overflow(exc):
                if ctx_retries < _MAX_CTX_RETRIES:
                    ctx_retries += 1
                    logger.warning(
                        "Marvis 上下文超限（本次路由的后端装不下），"
                        "原样重发第 %d/%d 次以重新路由 | acc=%s | 消息 %d 条 | %s",
                        ctx_retries, _MAX_CTX_RETRIES, account.id,
                        len(body.get("messages") or []),
                        (exc.detail or exc.message or "")[:160])
                    last_error = exc
                    # 同号重试：不换号、不记失败、不动 body
                    attempts -= 1
                    time.sleep(_CTX_RETRY_GAP)
                    continue
                last_error = exc
                logger.warning(
                    "Marvis 上下文超限，重发 %d 次仍装不下 | acc=%s | 消息 %d 条 | %s",
                    _MAX_CTX_RETRIES, account.id,
                    len(body.get("messages") or []),
                    (exc.detail or exc.message or "")[:300])
                break
            # 参数类 400：换号没用、也不该记在号上。先降级重试。
            #
            # 上限单独计数（`param_retries`），不吃 `MAX_ATTEMPTS` 的换号预算；
            # 且每次只摘一个字段，摘到能过就出字 —— 见 `_DEGRADE_FIELDS`。
            if _is_param_error(exc):
                degraded = _degrade_body(body, dropped)
                if degraded is not None and param_retries < len(_DEGRADE_FIELDS):
                    param_retries += 1
                    logger.warning(
                        "Marvis 参数被上游拒绝，摘掉 %s 后重试 | acc=%s | %s",
                        sorted(dropped), account.id,
                        (exc.detail or exc.message or "")[:180])
                    body = degraded
                    last_error = exc
                    # 同号重试：不进 exclude、不 note_failure
                    attempts -= 1
                    continue
                last_error = exc
                logger.warning(
                    "Marvis 参数被上游拒绝且无可降级字段 | acc=%s | %s",
                    account.id, (exc.detail or exc.message or "")[:300])
                break
            # 设备 / IP 风控（4100404）：**立刻停，不换号**。
            #
            # 以前这里会继续换号（`risk_control` 不在 break 的名单里），后果是
            # 一条请求把池子里所有号依次撞一遍、每个号吃 600 秒冷却 ——
            # 4 个号撞满就是 10 分钟**全池冷却**，接下来哪怕风控已经解除，
            # 网关自己也回 `503 accounts_cooling` 把请求挡在门外
            # （2026-11-17 用户实测：09:23 撞完，09:24 起全 503）。
            #
            # 风控是**出口侧**的判定，所有号共用同一个出口，换号 100% 无效。
            # 所以：只记当前号一次失败、立刻停，把真实原因交给 `_fail` 说清楚。
            # 冷却也给短一些（60 秒）—— 它的作用只是让后续请求别继续砸墙，
            # 不需要 600 秒那么久，因为 `_fail` 已经把"别重试"告诉了客户端。
            if exc.kind == "risk_control":
                shared.note_failure(
                    db, account, kind=exc.kind, detail=exc.detail or exc.message,
                    status=exc.status, model=model, public_platform=PUBLIC,
                    cool_seconds=60)
                last_error = exc
                logger.warning(
                    "Marvis 出口被风控（4100404，换号无效，立刻停止）| acc=%s | %s",
                    account.id, (exc.detail or exc.message or "")[:200])
                break
            # 401 是「登录态过期」而不是「配置错了」—— 文案要能直接指导操作。
            # Marvis 的 token 实测只活约 2 小时（见 `TOKEN_TTL_SECONDS`），
            # 说「请检查凭证」只会让用户去翻配置，而答案是重新扫码。
            detail = exc.detail or exc.message
            if exc.kind == "auth":
                hint = marvis_accounts.token_expiry_hint(
                    marvis_accounts.credentials_of(account))
                age = hint.get("age_seconds")
                extra = ("（该凭证已签发 %d 分钟，超过约 2 小时的有效期）"
                         % (age // 60) if age is not None else "")
                exc = client.UpstreamError(
                    "Marvis 登录态已过期%s：请在控制台「授权登录」重新扫码（QQ / 微信）"
                    % extra, status=401, code="credential_expired", kind="auth",
                    detail=detail)
            shared.note_failure(
                db, account, kind=exc.kind, detail=exc.detail or exc.message,
                status=exc.status, model=model, public_platform=PUBLIC,
                cool_seconds=(600 if exc.kind in ("auth", "risk_control") else 0))
            last_error = exc
            exclude.add(account.id)
            # 参数类错误换号也没用，直接停
            if exc.status in (400, 422) and exc.kind not in (
                    "auth", "rate_limit", "risk_control"):
                break
        except Exception as exc:  # noqa: BLE001
            shared.release(account)
            logger.warning("Marvis 调用失败 acc=%s: %s",
                           account.id, type(exc).__name__)
            last_error = client.UpstreamError(
                "Marvis 调用异常（%s）" % type(exc).__name__, kind="upstream")
            exclude.add(account.id)
    if last_error is not None:
        raise last_error
    # 走到这里说明**一个号都没选到**（选号返回 None 或全被租约挤掉）。
    # 「导入凭证」这句话只在池子真的空的时候成立；有 active 号却选不到，
    # 原因只有两种，得分开讲清楚（2026-11-15 用户实测被这句话误导：账号明明
    # 是 active、凭证也在，只是 access_token 过了约 2 小时有效期）。
    try:
        rows = marvis_accounts.accounts_for(db, realm)
        alive = [r for r in rows if (getattr(r, "status", "") or "") == "active"]
        expired = [r for r in alive
                   if "过期" in str(getattr(r, "last_err_msg", "") or "")]
        # 冷却中的号里，有多少是被**风控**挂起来的（`cool_kind`）。
        #
        # 为什么必须区分：这两种"冷却"对用户的含义完全相反 ——
        #   · 普通冷却：忙 / 刚失败，等几十秒就好，`Retry-After: 3` 是对的；
        #   · 风控冷却：出口被平台拦了，等 3 秒没用，重试只会延长封禁。
        # 以前统一回「都在冷却或排队中，请稍后重试」，用户就一直重试，
        # 一边重试一边看它继续失败（2026-11-17 用户实测踩到）。
        risk_cooling = [r for r in alive
                        if str(getattr(r, "cool_kind", "") or "") == "risk_control"]
        risk_ids = [r.id for r in risk_cooling]
    except Exception:  # noqa: BLE001
        alive, expired, risk_cooling, risk_ids = [], [], [], []
    if alive and len(expired) == len(alive):
        raise shared.PlatformRejected(
            503, "Marvis 账号登录态已全部过期，请在控制台「授权登录」重新扫码"
                 "（access_token 由上游按天轮换，过期后只能重登）",
            kind="server_error", code="credential_expired",
            headers={"Retry-After": "5"})
    if risk_cooling and len(risk_cooling) == len(alive):
        # 全池都在风控冷却里 —— 这是 17:24 那次 503 的真实原因，
        # 而不是「排队中」。文案要能直接指到「出口风控」这个动作上。
        raise shared.PlatformRejected(
            429,
            "腾讯 Marvis 对当前出口做了风控拦截（4100404），全部 %d 个账号都在冷却中"
            "（账号 id：%s）。这不是账号或 API Key 的问题：凭证钱包接口正常，"
            "换号 / 重扫码 / 换 Key 都无效。请停止重试（重试会延长风控窗口），"
            "等风控过去或换出口 IP 后再试。"
            % (len(risk_cooling), "、".join(str(i) for i in risk_ids[:8])),
            kind="upstream_risk_control", code="risk_control_cooling",
            headers={"Retry-After": "600"})
    if alive:
        raise shared.PlatformRejected(
            503, "Marvis 账号都在冷却或排队中，请稍后重试",
            kind="server_error", code="accounts_cooling",
            headers={"Retry-After": "3"})
    raise shared.PlatformRejected(
        503, "Marvis 还没有账号，请在控制台「授权登录」扫码（QQ 或微信）",
        kind="server_error", code="no_available_account",
        headers={"Retry-After": "3"})


def _acquire(request, body: dict, realm: str, *, stream: bool):
    """鉴权 + 选号 + 打开上游，**全程同步、必须在工作线程里跑**。

    ## 为什么整段都在线程里

    这里三件事都是**阻塞**的：`shared.authenticate`（SQLAlchemy 同步会话 +
    查 Key/配额）、`_attempt`（选号 + 租约 + `urlopen`），而 `urlopen` 会一直
    等到上游把**响应头**吐出来（流式也是：`STREAM_TIMEOUT=300s`）。
    单 worker 的 uvicorn 上，这段留在事件循环里就是「一次上游卡顿，全站停摆」
    —— 2026-11-16 事故即此。

    会话在**本线程内**新建、`finally` 里关闭：SQLAlchemy 的 Session 不跨线程
    复用，交给上层就一定会踩到「连接已被归还但对象还在用」的坑。

    返回 `(key, resp, account, started)`；异常（`PlatformRejected` /
    `UpstreamError`）原样抛出，由调用方转成对应协议的响应。
    """
    from admin.db import SessionLocal

    db = SessionLocal()
    try:
        key = shared.authenticate(db, request, platform=PUBLIC)
        resp, account, started = _attempt(db, request, key, body, realm,
                                          stream=stream)
        return key, resp, account, started
    finally:
        db.close()


async def _iter_upstream(resp):
    """把同步 `HTTPResponse` 的 read 挪到工作线程，避免卡住事件循环。

    用 `asyncio.to_thread`（而不是裸 `run_in_executor`）：它会带上当前
    contextvars，`request_metrics` / 实时面板那套按请求隔离的上下文才不会丢。
    """
    while True:
        chunk = await asyncio.to_thread(resp.read, 8192)
        if not chunk:
            break
        yield chunk


def _usage_of(result: dict) -> dict:
    usage = result.get("usage") if isinstance(result, dict) else None
    return usage if isinstance(usage, dict) else {}


async def chat_completions(request: Request):
    """OpenAI 兼容入口 —— 本平台的唯一主路径。"""
    body = await _body_of(request)
    realm = _realm(request)
    try:
        slot = _validate_model(body)
    except _BadRequest as exc:
        return JSONResponse({"error": {"type": exc.code, "message": exc.message,
                                       "code": exc.code}},
                            status_code=404 if exc.code == "model_not_found" else 400)

    upstream_body = _prepare_body(body, slot)
    # 记账用可读名（`deepseek-v4.1-flash`），**不是**槽位名（`main-auto`）。
    # 详见 `_billing_model`：这一列是控制台所有模型维度的唯一数据源。
    billed_model = _billing_model(slot)
    wants_stream = bool(body.get("stream"))

    try:
        key, resp, account, started = await asyncio.to_thread(
            _acquire, request, upstream_body, realm, stream=wants_stream)
    except shared.PlatformRejected as exc:
        # `openai()` 本身返回的就是 **JSONResponse**，不要再包一层 ——
        # 包了会 TypeError: Object of type JSONResponse is not JSON serializable。
        return exc.openai()
    except client.UpstreamError as exc:
        return _fail(exc)

    if not wants_stream:
        usage = _usage_of(resp)
        await _bill_async(key, account, billed_model, usage, request=request,
                          started=started)
        return JSONResponse(resp)

    async def generator():
        stats = {"ttfb_ms": 0, "total_ms": 0}
        usage: dict = {}
        first_at = None
        began = time.perf_counter()
        buffer = b""
        try:
            async for chunk in _iter_upstream(resp):
                if first_at is None:
                    first_at = time.perf_counter()
                    stats["ttfb_ms"] = int((first_at - started) * 1000)
                yield chunk
                # 顺手把最后一个 usage 帧记下来 —— 不需要改动转发的字节
                buffer += chunk
                if b'"usage"' in buffer:
                    for line in buffer.split(b"\n"):
                        line = line.strip()
                        if not line.startswith(b"data:"):
                            continue
                        payload = line[5:].strip()
                        if payload in (b"", b"[DONE]"):
                            continue
                        try:
                            obj = json.loads(payload)
                        except Exception:  # noqa: BLE001
                            continue
                        if isinstance(obj, dict) and isinstance(obj.get("usage"), dict):
                            usage = obj["usage"]
                    # 只保留尾部，避免 buffer 无限增长
                    buffer = buffer[-8192:]
        finally:
            stats["total_ms"] = int((time.perf_counter() - began) * 1000)
            client.close_stream(resp)
            # 流正常读完才算这个号「用成功过」—— 失败的情况下 note_success
            # 会把刚才 note_failure 记的失败计数清零，等于把坏号洗白。
            shared.note_success(account)
            shared.release(account)
            # 记账是同步 DB 写，必须离开事件循环（客户端断开时也不会丢账，
            # 见 `_bill_async` 里的 shield）。
            await _bill_async(key, account, billed_model, usage, request=request,
                              started=started, stats=stats)

    return StreamingResponse(generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


def _anthropic_to_openai(body: dict) -> dict:
    messages: list[dict] = []
    system = body.get("system")
    if isinstance(system, str) and system.strip():
        messages.append({"role": "system", "content": system})
    elif isinstance(system, list):
        text = "".join(str(part.get("text") or "") for part in system
                       if isinstance(part, dict))
        if text.strip():
            messages.append({"role": "system", "content": text})

    for item in (body.get("messages") or []):
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "user")
        content = item.get("content")
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text") or ""))
            content = "".join(parts)
        messages.append({"role": role, "content": content or ""})

    out = {
        "model": body.get("model") or endpoints.DEFAULT_MODEL,
        "messages": messages,
        "stream": bool(body.get("stream")),
    }
    if body.get("max_tokens"):
        out["max_tokens"] = body["max_tokens"]
    if body.get("temperature") is not None:
        out["temperature"] = body["temperature"]
    if isinstance(body.get("tools"), list):
        tools = []
        for tool in body["tools"]:
            if not isinstance(tool, dict):
                continue
            name = tool.get("name")
            if not name:
                continue
            tools.append({"type": "function",
                          "function": {"name": name,
                                       "description": tool.get("description") or "",
                                       "parameters": tool.get("input_schema") or {}}})
        if tools:
            out["tools"] = tools
    return out


def _responses_to_openai(body: dict) -> dict:
    """Responses 请求 → OpenAI chat 请求（**复用 protocol_bridge**）。

    2026-11-15 修：这里原来是自己手写的一个精简版，只搬 messages /
    max_output_tokens，**把 `tools` 整个丢了** —— 于是通过 `/v1/responses`
    发工具定义时，上游根本收不到，模型只能回「我没有 get_time 工具」
    （真机实测 function_call=0，而同一个请求走 `/v1/messages` 正常返回
    tool_use）。`protocol_bridge.responses_to_openai` 是各平台共用的成熟实现
    （工具、tool_choice、function_call 回传都处理过），直接用它。
    """
    from admin.platform import protocol_bridge as bridge

    out = dict(bridge.responses_to_openai(body) or {})
    # 模型 id 必须是槽位名（上游只认槽位），bridge 会原样保留上游名。
    out["model"] = endpoints.DEFAULT_MODEL
    if not out.get("messages"):
        # bridge 对纯字符串 input 已经处理；这里兜底空消息体
        out["messages"] = [{"role": "user",
                            "content": str(body.get("input") or "")}]
    return out


class _Inner:
    """给 bridge 中继用的内层：`body_iterator` 产出 OpenAI SSE 字节。"""

    def __init__(self, response):
        self.body_iterator = response.body_iterator


async def _run_normalized(request: Request, normalized: dict, *, protocol: str):
    """把已翻译好的 OpenAI 请求走一遍主路径，**再把响应翻回请求方协议**。

    ## 2026-11-15 修：这里原来少了「翻回去」这一步

    改造前是 `return await chat_completions(inner)` —— 直接把 OpenAI 的
    JSON / SSE 原样返回。Anthropic 客户端读 `content[]`、Responses 客户端读
    `output[]`，拿到的却是 `choices[]`，于是**流式零事件、非流式空文本**
    （真机实测：`/v1/messages` 与 `/v1/responses` 全空，只有
    `/v1/chat/completions` 正常）。协议翻译只做了一半。

    现在：上游返回后按 `protocol` 走 `protocol_bridge` 对应的中继/整形，
    与豆包、Loomy 等平台同一条路径。
    """
    from starlette.requests import Request as _Request

    from admin.platform import protocol_bridge as bridge

    payload = json.dumps(normalized, ensure_ascii=False).encode()

    async def receive():
        return {"type": "http.request", "body": payload, "more_body": False}

    inner = _Request(request.scope, receive)
    response = await chat_completions(inner)

    if protocol == "anthropic":
        model = str(normalized.get("model") or "")
        if getattr(response, "status_code", 200) >= 400:
            return response                     # 错误原样返回（已是 JSON）
        if isinstance(response, StreamingResponse):
            return bridge.anthropic_stream_relay(_Inner(response), model,
                                                 prefix="marvis")
        # 非流式：JSONResponse.body 是已序列化的 bytes
        result = json.loads(response.body.decode("utf-8"))
        return JSONResponse(bridge.openai_to_anthropic(result, model))

    if protocol == "responses":
        model = str(normalized.get("model") or "")
        if getattr(response, "status_code", 200) >= 400:
            return response
        if isinstance(response, StreamingResponse):
            return bridge.responses_stream_relay(_Inner(response), model,
                                                 prefix="marvis")
        result = json.loads(response.body.decode("utf-8"))
        return JSONResponse(bridge.openai_to_responses(result, model))

    return response


async def messages(request: Request):
    """Anthropic 入口 → 转成 OpenAI 请求跑主路径 → 响应翻回 Anthropic。"""
    body = await _body_of(request)
    normalized = _anthropic_to_openai(body)
    return await _run_normalized(request, normalized, protocol="anthropic")


async def responses(request: Request):
    """Responses 入口 → 折叠成 chat/completions → 响应翻回 Responses。"""
    body = await _body_of(request)
    normalized = _responses_to_openai(body)
    return await _run_normalized(request, normalized, protocol="responses")


async def count_tokens(request: Request):
    """本地估算。口径：CJK 1 token/字，其它 4 字符 1 token（保守近似）。

    ⚠ 这里原来是一段**接在 `responses()` 的 `return` 之后**的死代码：
    函数永远走不到，`dispatch()` 调 `count_tokens(request)` 时直接
    `NameError` → 500。测试从不打这个路径，所以一直没人发现。
    """
    body = await _body_of(request)
    parts: list[str] = []
    if isinstance(body.get("system"), str):
        parts.append(body["system"])
    for message in (body.get("messages") or []):
        if isinstance(message, dict):
            content = message.get("content")
            parts.append(content if isinstance(content, str) else str(content or ""))
    text = "\n".join(parts)
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return JSONResponse({"input_tokens": cjk + max(1, (len(text) - cjk) // 4)})


async def list_models(_request: Request):
    """模型目录。

    Marvis 的模型网关**没有** `/v1/models`（实测无此端点），目录是客户端内置的
    槽位表，所以这里返回静态目录 —— 不猜上游。

    `id` 给**可读模型名**（`deepseek-v4.1-flash`）而不是槽位名
    （`main-auto`）—— 后者对外无法理解（用户实测反馈「怎么模型能叫这个
    名称呢」）。槽位名留在 `api_id` 里，调用时由 `canonical_model_id()`
    换回去，客户端不需要知道它是槽位。
    """
    data = [{"id": item["id"], "object": "model", "created": 0,
             "owned_by": PUBLIC, "name": item["name"],
             "api_id": item["api_id"], "context": item["context"],
             "display_name": item["name"]}
            for item in endpoints.models_catalog()]
    return JSONResponse({"object": "list", "data": data})


async def balance(request: Request):
    """余额快照 —— 汇总各账号的令牌钱包。"""
    from admin.db import SessionLocal

    realm = _realm(request)
    with SessionLocal() as db:
        summary = marvis_accounts.summarize_db(db, realm)
    return JSONResponse({"object": "balance", "platform": PUBLIC,
                         "realm": realm,
                         "total_tokens": summary["balance_total"],
                         "remain_tokens": summary["balance_remain"],
                         "accounts": summary["accounts"],
                         "active": summary["active"]})


async def dispatch(request: Request, path: str):
    """按路径分发（供 `PlatformSpec.gateway` 的通用入口调用）。"""
    if path in ("/v1/chat/completions", "/chat/completions"):
        return await chat_completions(request)
    if path in ("/v1/messages", "/messages"):
        return await messages(request)
    if path in ("/v1/responses", "/responses"):
        return await responses(request)
    if path in ("/v1/messages/count_tokens", "/messages/count_tokens"):
        return await count_tokens(request)
    if path in ("/v1/models", "/models"):
        return await list_models(request)
    if path in ("/v1/balance", "/balance"):
        return await balance(request)
    return JSONResponse({"error": {"message": "Marvis 不支持的路径：%s" % path,
                                   "type": "not_found"}}, status_code=404)