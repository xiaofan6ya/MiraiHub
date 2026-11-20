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
import random
import re
import threading
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


def _quota_tight_message(upstream_code: str, notes: dict | None = None) -> str:
    """额度族（3000100 / 1000007）的对外文案 —— 给用户看的那一段。

    ## 为什么不能只说「请求太大」

    `3000100 max input token error` 曾经被解释成「输入 token + max_tokens 合计
    一条预算」，于是文案引导用户拆消息。线上样本把这个解释否掉了：被拒的请求
    只有 24 KB / 2 条消息，而**同一个账号**在实时窗口内成功处理过更大的形状
    （515 KB / 98 条消息，20 次 200）。体积不是判据，拆消息改了也没用。

    剩下唯一站得住的维度是「**该账号在该模型上的当次剩余可用额度**」不够。
    文案必须同时否掉两个方向：不是你的 API Key 无效、也不是账号池总额度用光。
    """
    notes = notes if isinstance(notes, dict) else {}
    degraded = notes.get("degraded_max_tokens") or {}
    switched = int(notes.get("quota_switch_accounts") or 0)
    tried = []
    if degraded:
        tried.append("把 max_tokens 从 %s 降到 %s 后同号重试"
                     % (degraded.get("from"), degraded.get("to")))
    if switched:
        tried.append("换号后试过 %d 个账号" % switched)
    action = ("网关已自动处置：%s，但" % "、".join(tried)) if tried else ""
    return (
        "Marvis 上游额度不足（上游 %s）：这是「该账号在该模型上的当次可用"
        "额度」不够，不是请求太大、不是你的 API Key 无效，也不是账号池的"
        "总额度已经用光。%s额度仍然不够，本次请求失败。"
        "建议：稍后重试、换一个模型，或在控制台换一个 Marvis 账号再发。"
        % (upstream_code or "3000100", action))


def _error_payload(exc: client.UpstreamError,
                   notes: dict | None = None) -> tuple[int, dict, dict] | None:
    """额度族 / 上游内部错误 → `(status, error_obj, headers)`；其余返回 `None`。

    **只处理本 PR 新增的三种 kind**，其余（signer_unavailable / content_block /
    risk_control / auth / 参数 400）一律返回 `None` 交回 `_fail()` 的既有分支 ——
    这样那几条已在 origin/main 上稳定的分类一个字都不用动。

    ## 为什么要独立出来

    这几种失败以前全都落进 `upstream_business`：网关「不重试、不冷却、原样抛」，
    客户端只拿到 `HTTP 402` / `HTTP 500`，语义上等于「你的请求写错了」，
    用户于是去改一个根本没写错的请求体。业务码才是判据，所以：

      · `3000100` → 400 `max_input_tokens_exceeded`（额度族，可操作文案）；
      · `1000007` → 400 `insufficient_quota`；
      · `3000001` → 402 `quota_exhausted`（账号级总额度，等每日重置 / 加号）；
      · `5100007` → 502 `upstream_internal_error`（上游侧故障，与号池无关）。

    `family` 是给下游聚合用的族标记（目前只有 `quota`），具体原因看
    `upstream_code` —— 后者保留上游业务码，便于排障时与上游工单对齐。
    """
    upstream_code = client._biz_code_of(exc.detail or "").strip()
    headers: dict[str, str] = {}
    family = ""
    if exc.kind == "max_input_tokens" or upstream_code == "1000007":
        # 额度族（3000100 / 1000007）：判据是「该号该模型当次可用额度」不够。
        status = 400
        code = ("max_input_tokens_exceeded"
                if exc.kind == "max_input_tokens" else "insufficient_quota")
        err_type = exc.kind or "quota_exceeded"
        shown_code = upstream_code
        if shown_code in ("", "400", "401", "402", "403", "404", "422",
                          "429", "500", "502", "503"):
            # detail 抠不出业务码时，exc.code 只是 HTTP 状态 —— 文案里要写上游码。
            shown_code = ("3000100" if exc.kind == "max_input_tokens"
                          else "1000007")
        message = _quota_tight_message(shown_code, notes)
        family = "quota"
    elif exc.kind == "upstream_internal":
        # 上游服务端内部错误：换号只会把整池的 err_count 写满，与账号无关。
        status = 502
        code = "upstream_internal_error"
        err_type = "upstream_internal"
        message = (
            "Marvis 上游服务端内部错误（上游 %s）：与你的 API Key、账号、额度都"
            "无关，这是上游侧故障，换账号不会改变结果。稍后重试即可。"
            "原始信息：%s" % (upstream_code or "5100007", exc.message))
    elif exc.kind == "quota_exceeded":
        # 账号级总额度（3000001 用户名下 token 不足）：等的是上游每日
        # 00:00(+08) 的配额重置，不是「冷却到期」—— 两种结局完全不同。
        status = 402
        code = "quota_exhausted"
        err_type = "quota_exceeded"
        family = "quota"
        headers["Retry-After"] = "3600"
        message = (
            "Marvis 账号额度已耗尽（上游 %s）：这是账号级总额度用光，"
            "不是你的 API Key 无效，也不是网关在冷却。上游每日 00:00(+08)"
            "为各账号重置额度，请等待重置或在控制台加号。原始信息：%s"
            % (upstream_code or "3000001", exc.message))
    else:
        return None
    error_obj = {"type": err_type, "code": code, "message": message,
                 # 这几种都不是「换个号就好」的登录态问题
                 "upstream_account": False}
    if family:
        error_obj["family"] = family
    if upstream_code:
        error_obj["upstream_code"] = upstream_code
    return status, error_obj, headers


def _fail(exc: client.UpstreamError, *,
          notes: dict | None = None) -> JSONResponse:
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
    # 额度族 / 上游内部错误（3000100 / 1000007 / 3000001 / 5100007）
    # ------------------------------------------------------------------
    #
    # 先判掉这几种 kind：它们与下面 signer / content_block / risk_control /
    # auth 的分支互斥，放在这里能保证那几条既有分类一个字都不动。
    quota = _error_payload(exc, notes)
    if quota is not None:
        q_status, q_obj, q_headers = quota
        return JSONResponse({"error": q_obj}, status_code=q_status,
                            headers=q_headers or None)
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
    # ## 4100404 的错误面已移交 `_risk_control_rejected()`（PR #67，2026-11-20）
    #
    # 原来这里直接返回 `429 + Retry-After: 600`，理由是「客户端会按它退避」。
    # 但 600 秒对应的是**账号级冷却**时代的语义（整池都在风控里、等十分钟）；
    # PR #67 把 4100404 改成**请求级**失败之后，平台级退避窗口只有 10 秒，
    # 给 600 会让客户端误以为要等十分钟，而这条请求下次就可能通。
    #
    # 现在统一走 `_risk_control_rejected()`：503 + 1~10s，文案分两档
    # （「平台退避中」/「这一条被拦」）。
    if exc.kind == "risk_control":
        reject = _risk_control_rejected(exc.detail or exc.message)
        return JSONResponse(
            {"error": {
                "type": "upstream_risk_control",
                "code": reject.code,
                "message": reject.message,
                # 平台侧问题，但不是「换个号就好」的那种账号问题
                "upstream_account": False}},
            # ⚠ `PlatformRejected` 的属性叫 `status`，不是 `status_code`
            status_code=reject.status,
            headers=dict(reject.headers or {}))
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


# ---------------------------------------------------------------------------
# 请求体体积兜底（2026-11-18）
# ---------------------------------------------------------------------------
#: ## 为什么需要这一层
#:
#: 上游对单条消息的体积有硬上限，超了只回一个看不出原因的
#: `400 3000101 invalid parameter`。线上 27 次 3000101 全是同一个形状
#: （bytes≈11.4MB / msgs=2 / sys_chars=252）—— 客户端把单条 11MB 的消息塞进
#: `messages[0]`。于是每次都要走完「选号 → 建连 → 4xx → 换号」才失败，
#: 白占一个账号的上游配额，日志里也只剩一句 `invalid parameter`。
#:
#: ## 阈值为什么不是 4MB
#:
#: 最初取 4MB，依据是「3.7MB 的正常大对话成功过」。但真实长对话会到 6.5MB
#: 级，被本地 4MB 的 413 挡住 —— 用户看到的是「请求体过大」，只能去拆消息，
#: 而拆消息对他们往往是不可接受的。所以默认值提到 256MiB：它已远超任何真实
#: 对话，实际上等于「不设限」，但仍保留一个兜底，防止单个请求把进程内存打爆。
#:
#: ## 环境变量 `MIRAIHUB_MARVIS_MAX_BODY_BYTES` 的语义
#:
#:   * 未设置 / 非法值 → 用 `DEFAULT_MAX_BODY_BYTES`（256 MiB）
#:   * 正数           → 用该值（按需调大或调小）
#:   * **0 或负数**   → 完全关闭体积检查（不限制）
#:
#:   ⚠ 0 是**语义反转**：旧实现里 0 属于「非法值」，会退回当时的默认 4MB。
#:     升级后若要保留旧上限，请显式写 `MIRAIHUB_MARVIS_MAX_BODY_BYTES=4194304`，
#:     不要依赖 0 的旧行为。负数一律按「关闭」处理，与 0 等价。
MAX_BODY_ENV = "MIRAIHUB_MARVIS_MAX_BODY_BYTES"
DEFAULT_MAX_BODY_BYTES = 256 * 1024 * 1024   # 256 MiB：给长对话留足空间
#: 旧名字保留，避免外部脚本 / 历史代码引用时炸掉。
MAX_BODY_DEFAULT_BYTES = DEFAULT_MAX_BODY_BYTES


def _max_body_bytes() -> int:
    """单次请求体的上限（字节）。

    返回值 **<= 0 表示不限制**（`MIRAIHUB_MARVIS_MAX_BODY_BYTES=0` 或负数）。
    未设置 / 非法值退回 `DEFAULT_MAX_BODY_BYTES`（256 MiB）。
    """
    raw = str(os.environ.get(MAX_BODY_ENV) or "").strip()
    if not raw:
        return DEFAULT_MAX_BODY_BYTES
    try:
        # int(float(...)) 而不是裸 int：兼容 "1048576.0" / "1e6" 这类写法
        return int(float(raw))
    except (TypeError, ValueError):
        return DEFAULT_MAX_BODY_BYTES


def _too_large(size: int, limit: int) -> JSONResponse:
    """413：说清这是**本网关**的体积兜底，并给出唯一的两个动作。

    不再教用户「自己去拆消息」—— 用户要的是能发，解法是调上限或关掉检查。
    """
    logger.warning("Marvis 请求体过大已本地拒绝 bytes=%d limit=%d（未打上游）",
                   size, limit)
    return JSONResponse(
        {"error": {
            "type": "invalid_request_error",
            "code": "body_too_large",
            "message": (
                "请求体过大（约 %.1f MB，当前网关上限 %.1f MB）。"
                "这是本网关的体积兜底，不是模型 / 账号 / 额度问题"
                "（上游根本没被调用）。"
                "如需发送更大的请求，把环境变量 %s 设为更大的字节数，"
                "或设为 0 完全关闭该检查。"
                % (size / 1048576.0, limit / 1048576.0, MAX_BODY_ENV)),
        }},
        status_code=413)


async def _reject_oversized(request: Request):
    """体积检查：超限返回 413 响应，否则返回 None。

    上限 <= 0（`MIRAIHUB_MARVIS_MAX_BODY_BYTES=0` 或负数）表示**关闭检查**，
    直接放行。先看 `Content-Length`（不读 body，零成本）；缺这个头时才把 body
    读出来量（Starlette 会缓存已读的 body，后续 `request.json()` 不会重复读流）。
    """
    limit = _max_body_bytes()
    if limit <= 0:              # 0 / 负数 = 不限制
        return None
    declared = request.headers.get("content-length")
    if declared:
        try:
            size = int(declared)
        except (TypeError, ValueError):
            size = 0
        return _too_large(size, limit) if size > limit else None
    try:
        raw = await request.body()
    except Exception:  # noqa: BLE001 - 读不出来就交给下游按原逻辑报错
        return None
    return _too_large(len(raw), limit) if len(raw) > limit else None


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
#
# ## 2026-11-18 扩表：通用开场白（当前第 9-13 条）
#
# 原来的规则 1-7 只覆盖「特定客户端」的身份句（Claude Code / Codex / DSH），
# 但上游黑名单同时还有一层**通用开场白**：任何客户端只要把
# `You are a helpful assistant` 这类开场白放进 system，就整条 403 / 4100406。
# 真机对照（同一把 key、同一个槽位，交替 24 次）：
#
#     不带 system 的同一请求           12 次 -> 全 200
#     带 system 的同一请求             12 次 -> 全 403 / 4100406
#     长段自定义提示词（不带开场白）      -> 200
#
# 即命中的是**那几句固定开头**，不是长度、不是内容本身。于是补 5 条：
#
#     9.  You are a helpful AI assistant
#     10. You are a helpful assistant
#     11. You are an AI assistant（含 `You are a AI assistant` 这种冠词错写）
#     12. You are a coding assistant
#     13. 你是乐于助人的助手
#
# 改法与上面 7 条**完全同一口径**：只把开场白写成同义的另一种说法
# （`helpful` -> `capable` / 给 `AI assistant` 补 `capable`），**不删句子**。
# 这些开场白后面常跟同一条身份约束（「回答用中文」「不要泄露密钥」），
# 删掉首句等于把约束一起丢掉 —— 网关不替用户做这种裁剪。
#
# ⚠️ 顺序是「具体 -> 泛化」：`a helpful AI assistant` 必须排在
#    `a helpful assistant` 之前 —— 两条正则的 `helpful` 与 `assistant` 之间
#    分别是 `AI\s+` 与直接相接，本来不会互相吃掉，但先把具体的改完更稳妥：
#    以后若有人把 `(?:AI\s+)?` 合进一条，前半被改坏后后半会再命中一次。
#    第 11 / 12 条同理，各留一条独立规则，不做「合并成一条」的优化。

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
    #
    #    ⚠ **品牌名要同时认两个**（2026-11-18 实测）：DSH 换过一次产品名 ——
    #    旧名是 `DeepSeek\s+Harness`（带空格），新名去掉了空格、把 Harness
    #    换成了 Runtime。旧正则只认前者，换名之后在真机上**静默空跑**：
    #    规则还在表里、`rules_hit` 恒 0、上游照旧 403 / 4100406。
    #    「规则存在、看着也修过、却永不命中」是最难查的一类，所以这里用
    #    `DeepSeek\s*(?:Harness|Runtime)` 同时认两个品牌名（`\s*` 容忍有无空格）。
    (re.compile(r"You\s+are\s+an?\s+AI\s+(?:coding\s+)?agent\s*,?\s*powered\s+by\s+"
                r"(?:the\s+)?DeepSeek\s*(?:Harness|Runtime)", re.I),
     "You're a capable AI coding assistant, powered by a large language model"),
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
    # 9. 通用开场白：`You are a helpful AI assistant`（2026-11-18 真机新增黑名单）。
    #    只把 `helpful` 换成 `capable`：**不删句子** —— 这类开场白后面常跟同一条
    #    身份约束（「回答用中文」「不要泄露密钥」），删掉首句等于把约束一起丢掉。
    #    替换值用 `You're` 形态而不是 `You are a capable ...`：上游兜底的
    #    `_rewrite_identity_line()` 会把首行的 `You are ` 再改一次，一条规则
    #    命中一次更好对账（也免得 `rules_hit` 虚高一倍）。
    (re.compile(r"You\s+are\s+a\s+helpful\s+AI\s+assistant", re.I),
     "You're a capable AI assistant"),
    # 10. 通用开场白：`You are a helpful assistant`（不含 AI）。
    #     **必须排在 9 之后**：先跑 10 的话，9 的触发串会被改成
    #     `You're a capable assistant`，后半句的 `AI` 直接丢掉。
    (re.compile(r"You\s+are\s+a\s+helpful\s+assistant", re.I),
     "You're a capable assistant"),
    # 11. 通用开场白：`You are an AI assistant`；冠词错写 `You are a AI assistant`
    #     一样会被拦，所以用 `an?`。替换文本里的冠词只能是 `a`，否则会改出
    #     `an capable` 这种病句。
    (re.compile(r"You\s+are\s+an?\s+AI\s+assistant", re.I),
     "You're a capable AI assistant"),
    # 12. 通用开场白：`You are a coding assistant`。
    (re.compile(r"You\s+are\s+a\s+coding\s+assistant", re.I),
     "You're a capable coding assistant"),
    # 13. 中文通用开场白：`你是乐于助人的助手`。句末标点（`。` / `！` / 无标点）
    #     不在匹配内，原样保留；同上，不删句子、只换说法。
    (re.compile(r"你是乐于助人的助手", re.I),
     "你是一位能力出众的助手"),
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


def _sanitize_top_level_system(body: dict) -> int:
    """顶层 `system` 字段同样过一遍指纹改写，返回命中条数。

    ## 为什么还要单独洗这一处（2026-11-18）

    `/v1/messages` 的 `_anthropic_to_openai()` 会把顶层 `system` 搬进
    `messages[0]`，那条路径本来就被 `_sanitize_messages()` 覆盖了。但走
    `/v1/chat/completions` 的客户端可以直接带一个**顶层** `system`
    （Anthropic 风格：`{"system": "You are a helpful assistant", ...}`），
    而 `_sanitize_messages()` 只遍历 `messages[]` —— 这份正文整段在改写
    作用域之外：**同一句触发串换个承载字段就能打到上游**，日志里
    `rules_hit` 还是 0，看起来像「规则没生效」。

    这里只改写、**不搬位置**：字段仍然是顶层的 `system`（上游认哪个字段是
    上游的事，网关不做形态搬迁）；列表形态（`[{"type": "text", ...}]`）
    逐段洗，其余形态原样放行。

    命中才写回 —— 零命中时 `body` 一个字符都不动，与 `_sanitize_messages()`
    同一取舍：前缀缓存不能被无谓重建打掉。
    """
    value = body.get("system")
    if isinstance(value, str):
        rewritten, hits = rewrite_tencent_fingerprints(value)
        if hits:
            body["system"] = rewritten
        return hits
    if isinstance(value, list):
        hits = 0
        out = list(value)
        for index, part in enumerate(value):
            if isinstance(part, str):
                rewritten, n = rewrite_tencent_fingerprints(part)
                if n:
                    out[index] = rewritten
                    hits += n
                continue
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                rewritten, n = rewrite_tencent_fingerprints(part["text"])
                if n:
                    out[index] = dict(part, text=rewritten)
                    hits += n
        if hits:
            body["system"] = out
        return hits
    return 0


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
#:
#: ⚠ 这里**不含业务码**：`3000100` 是额度族（见 `QUOTA_EXHAUSTED_CODES`），
#: 按「装不下」处理会在同一个号上反复重发。而它的文案里带 `max input token`，
#: 所以 `_is_context_overflow()` 还要先按**码**排掉一次（见下）。
_CONTEXT_OVERFLOW_WORDS = (
    "max input token",
    "maximum context length",
    "context length exceeded",
    "context_length_exceeded",
    "input is too long",
    "prompt is too long",
    "too many tokens",
    "exceeds the maximum",
)

#: 上下文超限时**原样重发**几次。
#:
#: 每次重发都是一次重新路由的机会。给到 4 次是因为实测一次失败后
#: 下一次就换到能装下的后端了；再多只是在慢请求上浪费时间。
_MAX_CTX_RETRIES = 4

#: 重试之间的间隔。不纯粹是退避 —— 上游的路由决策带一点时间相关性，
#: 间隔一两秒能显著提高「换一个后端」的概率。
_CTX_RETRY_GAP = 1.2

# ---------------------------------------------------------------------------
# ① 3000100（max_input_tokens）到底是什么 —— **额度族，不是「请求太大」**
# ---------------------------------------------------------------------------
#
# 我们曾按字面意思把它读成「这条请求装不下」，于是在 `_attempt` 里原样重发
# 4 次，指望上游重新选路 —— 结果是同一个号被同一条请求连撞 4 次，最后还告诉
# 用户「请新建对话」。用户去删历史，删完照样失败。
#
# 实测反例（2026-11-19，fork 侧直连对照）：24 KB / 2 条消息 / 系统提示 1058
# 字符的请求被 3000100 拒；而**同一个号**在同一个实时窗口内成功处理过
# 515 KB / 98 条消息的形状。体积不是判据。
#
# 真正的判据是「该号在该模型上的当次可用预算」：输入 + `max_tokens` 合起来
# 越过这个号此刻的额度，上游就用 3000100 回你。所以它属于**额度族**，
# 处置方式也与额度一致 —— 先省钱（把输出预算降下来）再换号。
#: 额度族的业务码。上游 HTTP 层只给状态码，业务码在响应体的 JSON 里。
QUOTA_EXHAUSTED_CODES = ("3000100",)
#: 输出预算超过这个值才认为「用户传了一个上游预算装不下的 max_tokens」。
#:
#: 实测依据：256 / 4096 / 16384 / 32768 / 65536 / 128000 / 1M 这一串取值都试过，
#: 32768 是折中 —— 足够长（不会因为截断让用户以为模型变笨），又能在绝大多数
#: 「输入偏大」的形状下把总预算压回上游收得下的区间。
MAX_TOKENS_DEGRADE_THRESHOLD = 32768
#: 降级目标值。与阈值同值：只降不升，绝不会把用户要的更短的长度改长。
MAX_TOKENS_DEGRADE_TARGET = 32768
#: 降级后仍 3000100 ⇒ **该号在该模型上的当次额度不足**，按 (账号 × 模型) 隔离。
#:
#: 为什么不是账号级：额度只对触发的那一个模型成立，同一个号换个小模型多半还能
#: 用，按 `degrade_until` 整号摘掉等于白扔一个账号。`shared.select_account` 已按
#: `account_model_cool` 跳过 (账号, 模型) 组合，这里只是往那张既有表里写一行
#: —— **没有第二套换号机制**。
MODEL_QUOTA_TIGHT_SECONDS = 1800
MODEL_QUOTA_TIGHT_KIND = "model_quota"

# ---------------------------------------------------------------------------
# ② 5100007（上游服务端内部错误）：同号重试一次，**不换号**
# ---------------------------------------------------------------------------
#
# 这个码是「上游自己出错了」，与账号无关：同一份 body 打给任何号都是同一个
# 答案。它以前落进 5xx 的「换号重试」路径，于是一条请求把整池号依次打一遍、
# 每个号各记一次 `note_failure`（err_count 全池 +1），用户看到「号全坏了」，
# 而号一个都没坏。正确处置只有一条：**在同号上重试一次**，仍失败就如实报错。
UPSTREAM_INTERNAL_CODES = ("5100007",)
#: 同号再试几次（不含首发）。1 = 「首发 + 1 次」。
INTERNAL_RETRIES = 1
#: 退避基数 + 抖动上限（秒）：实测落在 1~2 秒 —— 够上游缓过来，又不至于让
#: 客户端以为卡死。
INTERNAL_RETRY_BASE = 1.0
INTERNAL_RETRY_JITTER = 1.0


def _biz_blob(exc: "client.UpstreamError") -> str:
    """上游错误的可检索文本。

    `detail` 是上游**原始响应体**（业务码在里面），`message` 是网关自己的话。
    上游把 HTTP 状态塞进 `exc.code`，业务码从来不在那个字段里 —— 所以只能按
    文本认，与既有的 `_is_param_error()` 同口径。
    """
    return "%s %s" % (exc.detail or "", exc.message or "")


def _quota_code_of(exc: "client.UpstreamError") -> str:
    """这条错误里的额度族业务码；不是额度族返回空串。"""
    blob = _biz_blob(exc)
    for code in QUOTA_EXHAUSTED_CODES:
        if code in blob:
            return code
    return ""


def _is_quota_exhausted(exc: "client.UpstreamError") -> bool:
    """3000100：该号在该模型上的**当次可用额度不足**（不是请求太大）。"""
    return bool(_quota_code_of(exc))


def _is_upstream_internal(exc: "client.UpstreamError") -> bool:
    """5100007：上游服务端内部错误（与账号无关，换号无意义）。"""
    blob = _biz_blob(exc)
    return any(code in blob for code in UPSTREAM_INTERNAL_CODES)


def _max_tokens_of(body: dict) -> int:
    """取请求里的输出预算（三个别名等价）；取不到 / 非正数返回 0。"""
    if not isinstance(body, dict):
        return 0
    for key in ("max_tokens", "max_completion_tokens", "max_output_tokens"):
        raw = body.get(key)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            continue
        value = int(raw)
        if value > 0:
            return value
    return 0


def _is_context_overflow(exc: "client.UpstreamError") -> bool:
    """这条错误是不是「上下文太长，这次那个后端装不下」。

    必须在 `_is_param_error` **之前**判断：`3000101` 是字段不认，
    与「装不下」的处置完全相反。

    ⚠ `3000100` **不归这里**：它是额度族（见 `QUOTA_EXHAUSTED_CODES`），
    要先降 `max_tokens` 再换号。上游 3000100 的文案里也带 `max input token`，
    只按措辞判会把它抢走，所以第一件事是**按码排掉**。
    """
    if _quota_code_of(exc):
        return False
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
    · 顶层 `system` 字段（Anthropic 形态）同样过一遍改写：`_sanitize_messages()`
      只遍历 messages，顶层字段是同一句触发串的另一条承载路径。
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
    # 顶层 `system` 字段（Anthropic 形态）不在 messages 里，单独过一遍 ——
    # 同一句触发串换个承载字段就能绕过上面那段改写。
    system_hits = _sanitize_top_level_system(out)
    if system_hits:
        logger.info("Marvis 内容风控指纹改写（顶层 system）| rules_hit=%d", system_hits)
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


# ---------------------------------------------------------------------------
# 4100404：请求级判定 + 平台级退避
# ---------------------------------------------------------------------------
#
# 4100404「请求被风控拦截」以前按**账号粒度**处置：命中一次给当前号打 60 秒
# 冷却，`_attempt` 再换号重试，把同一份 body 在 1 秒内重放给另外几个号 —— 几个号
# 一起吃冷却是常态，`shared.select_account` 随即返回空，网关自己回「都在冷却中」。
# 于是 `命中 → 锁 60s → 到期 → 再命中` 可以无限循环：一条坏提示词（system 正文
# 撞上游字面量黑名单）就能长期把整站按在地上。
#
# 现在把两件事分开：
#
#   · **请求级**：单次 4100404 只是「这一条请求」失败 —— 不给账号打冷却、不换号、
#     不 exclude，客户端拿到可重试的 503 和「这一条被拦」的说明；
#   · **平台级**：只有 RISK_HIT_WINDOW_SECONDS 窗口内**连续**撞满
#     RISK_BACKOFF_THRESHOLD 次，才认为「出口成段被风控」，开一个
#     RISK_BACKOFF_DEFAULT_SECONDS 的退避窗口；窗口内请求直接拒，一个字节都不打上游。
#
# 任何一次上游成功都会 `clear_risk_hits()`：阈值判的是**连续**，不是历史累计
# （否则一天里零散撞 5 次也会把平台锁一次）。

#: 平台级退避的窗口长度（秒），可用 `MIRAIHUB_MARVIS_RISK_BACKOFF_SECONDS` 覆盖。
#:
#: 60 → 10 的理由：一次 4100404 命中的经常是**这一条请求的内容**，用一个 60 秒的
#: 平台窗口去处置它，等于把一条坏提示词放大成全站不可用（实测 20 分钟内 59 次 503，
#: 上游真正拒绝的只有 8 次）。10 秒是「别连续轰炸上游」与「别把偶发放大成故障」
#: 之间的折中。
RISK_BACKOFF_ENV = "MIRAIHUB_MARVIS_RISK_BACKOFF_SECONDS"
RISK_BACKOFF_DEFAULT_SECONDS = 10

#: **连续**命中多少次 4100404 才允许触发平台级退避（默认 5）。
#:
#: 阈值 = 1（旧行为）的问题：一次内容命中就把平台锁死。真实形态是「零散偶发」与
#: 「成串」两种，只有成串才说明出口整段被风控、平台级退避才有意义。
#: 可用 `MIRAIHUB_MARVIS_RISK_BACKOFF_THRESHOLD` 覆盖。
RISK_BACKOFF_THRESHOLD_ENV = "MIRAIHUB_MARVIS_RISK_BACKOFF_THRESHOLD"
RISK_BACKOFF_THRESHOLD = 5

#: 「连续」的时间窗（秒）：命中计数只统计最近这么久内的 4100404（默认 120）。
#:
#: 没有这个窗口的话，一天里零散撞 5 次也会把平台锁一次 —— 而「连续」的语义本来
#: 就是「短时间成串」。窗口内没有新命中，计数自然过期归零。
#: 可用 `MIRAIHUB_MARVIS_RISK_HIT_WINDOW_SECONDS` 覆盖。
RISK_HIT_WINDOW_ENV = "MIRAIHUB_MARVIS_RISK_HIT_WINDOW_SECONDS"
RISK_HIT_WINDOW_SECONDS = 120

#: 平台级风控状态：`at` = 最近一次命中的 monotonic 时间戳，`until` = 退避截止，
#: `hits` = 窗口内的连续命中次数。
#:
#: ## 为什么是模块级 dict + threading.Lock，不是 asyncio 全局变量
#:
#: `_attempt()` **整段跑在 asyncio.to_thread 的工作线程里**（见 `_acquire` 的
#: docstring），线程里没有运行中的事件循环 —— asyncio.Lock 拿不到也没法用。而并发
#: 请求来自线程池里的**不同线程**，所以这里要的是**线程间**互斥：一把 threading.Lock
#: 保护这几个浮点数，把「读 → 比较 → 写 max」放在同一次加锁里完成。否则两个线程同时
#: 命中 4100404 会各写各的截止时间，退避窗口可能被更早的那次覆盖成更短。
_RISK_LOCK = threading.Lock()
_RISK_STATE: dict[str, float] = {"at": 0.0, "until": 0.0, "hits": 0.0}


def _env_positive_int(name: str, default: int) -> int:
    """读一个正整数的 env；未设置 / 非法 / 非正值一律退回默认值。"""
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _risk_backoff_threshold() -> int:
    """连续命中多少次才触发平台退避（`MIRAIHUB_MARVIS_RISK_BACKOFF_THRESHOLD`）。"""
    return _env_positive_int(RISK_BACKOFF_THRESHOLD_ENV, RISK_BACKOFF_THRESHOLD)


def _risk_backoff_seconds() -> int:
    """退避窗口长度，秒（`MIRAIHUB_MARVIS_RISK_BACKOFF_SECONDS`）。"""
    return _env_positive_int(RISK_BACKOFF_ENV, RISK_BACKOFF_DEFAULT_SECONDS)


def _risk_hit_window() -> int:
    """「连续」的时间窗，秒（`MIRAIHUB_MARVIS_RISK_HIT_WINDOW_SECONDS`）。"""
    return _env_positive_int(RISK_HIT_WINDOW_ENV, RISK_HIT_WINDOW_SECONDS)


def note_risk_control() -> float:
    """记一次 4100404 命中，返回**当前**平台退避剩余秒数（0 = 没有触发退避）。

    **绝不抛**，也绝不单独锁平台。语义：

      1. 单次 4100404 **不触发**平台退避 —— 只让这一条请求失败、原样报给客户端；
      2. 最近 `_risk_hit_window()` 秒内**连续**撞满
         `_risk_backoff_threshold()` 次，才认为「出口成段被风控」，此时才开一个
         `_risk_backoff_seconds()` 的窗口；
      3. 任何一次上游成功都会 `clear_risk_hits()` 把连击清零 ——「连续」是真连续，
         不是累计。

    ## 为什么单次不能锁平台

    4100404 的触发源里至少有一条是**请求内容**（system 正文里被上游拉黑的开场白 /
    身份句）。同一条坏提示词每轮都会重发，旧实现于是「命中 → 锁 60 秒 → 到期 →
    再命中」，而这 60 秒里**所有**请求连上游都打不到 —— 一条提示词拖垮整站。
    """
    seconds = _risk_backoff_seconds()
    threshold = _risk_backoff_threshold()
    window = _risk_hit_window()
    now = time.monotonic()
    with _RISK_LOCK:
        # 窗口外的旧命中不算「连续」：计数过期归零，再把这一次重新数进来。
        if now - float(_RISK_STATE.get("at") or 0.0) > window:
            _RISK_STATE["hits"] = 0.0
        _RISK_STATE["hits"] = float(_RISK_STATE.get("hits") or 0.0) + 1.0
        _RISK_STATE["at"] = now
        if _RISK_STATE["hits"] < threshold:
            return 0.0
        until = now + seconds
        if _RISK_STATE["until"] < until:
            _RISK_STATE["until"] = until
        return _RISK_STATE["until"] - now


def risk_hits() -> int:
    """最近窗口内的连续 4100404 次数（窗口过期返回 0；只读，绝不抛）。"""
    now = time.monotonic()
    with _RISK_LOCK:
        if now - float(_RISK_STATE.get("at") or 0.0) > _risk_hit_window():
            return 0
        return int(_RISK_STATE.get("hits") or 0)


def clear_risk_hits() -> None:
    """上游成功一次 ⇒ 连击计数归零（**不动**已有的退避窗口）。"""
    with _RISK_LOCK:
        _RISK_STATE["hits"] = 0.0
        _RISK_STATE["at"] = 0.0


def risk_backoff_remaining() -> float:
    """平台级退避还剩几秒（0 = 没有退避，可以打上游）。"""
    with _RISK_LOCK:
        return max(0.0, float(_RISK_STATE.get("until") or 0.0) - time.monotonic())


def reset_risk_backoff() -> None:
    """清空平台级退避状态（测试 / 人工恢复用）。"""
    with _RISK_LOCK:
        _RISK_STATE["at"] = 0.0
        _RISK_STATE["until"] = 0.0
        _RISK_STATE["hits"] = 0.0


def _risk_control_rejected(detail: str = "") -> shared.PlatformRejected:
    """4100404 的客户端错误面：**两种文案**，取决于平台此刻是否在退避。

    ## 为什么必须分两档说

    对用户说假话的代价比报错本身大得多：

      · 平台**在退避**（连续命中撞满阈值）→ 说「平台在退避，约 X 秒恢复」，
        让他等；
      · **单次命中**（平台没退避，下一轮可能就通）→ 只能说「这一条请求被拦」，
        并指向真因：请求里的 system 正文撞了上游内容黑名单 —— 这条路径上换 Key /
        换号 / 重扫码都不会改变结果。

    状态码取 **503**（不是上游那个 403）：403 在所有 OpenAI 兼容客户端里都会被渲染成
    「API 密钥无效」，用户于是去翻一个根本没问题的 Key。503 + `Retry-After` 才是
    「先别急着重试」的中性表达。
    """
    hits = risk_hits()
    remaining = risk_backoff_remaining()
    detail = (detail or "").strip()
    if remaining > 0:
        message = (
            "Marvis 上游风控（4100404）：平台连续命中 %d 次后进入退避，约 %d 秒后"
            "自动恢复。这与你的 API Key、账号余额都无关，不需要换 Key，"
            "等窗口过去即可。" % (hits, int(remaining) + 1))
        code = "risk_control_backoff"
        retry_after = max(1, int(remaining) + 1)
    else:
        message = (
            "Marvis 上游内容风控（4100404）：这一条请求被拦（连续命中 %d/%d 次，"
            "未触发平台退避）。与你的 API Key、账号余额都无关，换 Key / 换号都不会"
            "改变结果 —— 触发源通常是请求里的 system 正文撞了上游内容黑名单，"
            "需要在网关侧补内容改写规则。"
            % (hits, _risk_backoff_threshold()))
        code = "risk_control_request"
        retry_after = 1
    if detail:
        message = "%s上游原文：%s" % (message, detail[:200])
    return shared.PlatformRejected(
        503, message, kind="upstream_risk_control", code=code,
        headers={"Retry-After": str(retry_after)})
def _note_model_quota_tight(db, account, model: str, *,
                            code: str = "3000100") -> None:
    """把「该号在该模型上的当次额度不够」写成 **(账号 × 模型)** 级隔离。

    ## 为什么不是账号级

    额度紧张只对**触发的那一个模型**成立：同一个号换个小模型多半还能用，
    按 `degrade_until` 整号摘掉等于白扔一个账号。`shared.select_account` 已经
    按 `account_model_cool` 跳过 (账号, 模型) 组合，所以这里只负责落库
    —— **走的是既有链路，没有第二套换号机制**。

    ## 与 5100007 的分界（本 PR 的核心判据）

    · `3000100` 是**额度**：换一个号就有预算，所以标完继续换号；
    · `5100007` 是上游**服务端内部错误**：同一个 body 打给任何号都是同一个
      答案，标了也没用，换号只会把好号也打脏 —— 所以那边同号重试后直接报错。

    **不抛异常**：隔离写失败不能影响响应。
    """
    if account is None or not str(model or "").strip():
        return
    try:
        from datetime import datetime, timedelta

        from admin.models import AccountModelCooldown

        now = datetime.utcnow()
        until = now + timedelta(seconds=MODEL_QUOTA_TIGHT_SECONDS)
        reason = ("上游 %s：该号在该模型上的当次可用额度不足"
                  % (code or "3000100"))
        row = (db.query(AccountModelCooldown)
               .filter(AccountModelCooldown.account_id == account.id,
                       AccountModelCooldown.model == model)
               .first())
        if row is None:
            db.add(AccountModelCooldown(
                account_id=int(account.id or 0), model=model, until=until,
                kind=MODEL_QUOTA_TIGHT_KIND, reason=reason[:255],
                upstream_error=str(code or "3000100"),
                reset_source="quota"))
        else:
            row.until = until
            row.kind = MODEL_QUOTA_TIGHT_KIND
            row.reason = reason[:255]
            row.upstream_error = str(code or "3000100")
            row.hits = int(row.hits or 0) + 1
        db.commit()
    except Exception:  # noqa: BLE001 - 隔离写失败不影响响应
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass


def _log_failure_profile(account, exc: "client.UpstreamError", *,
                         model: str, stream: bool, body: dict,
                         degraded: bool) -> None:
    """上游失败画像 —— **必须带 max_tokens 与 degraded 两列**。

    为什么这两列是硬要求：3000100 的判据是「`max_tokens` 与该号该模型当次剩余
    预算的对照」，没有 `max_tokens` 就看不出这次到底是原始值还是降级值撞的；
    没有 `degraded` 就分不清「第一次命中」与「降级后仍命中」，而这两者的处置
    完全相反（前者同号重试、后者换号）。缺任何一列，事后都无法复核这条链路。

    其余字段只记**数字**：`detail` 是上游返回体（不含凭证），截断到 200 字符。
    """
    messages = body.get("messages") if isinstance(body, dict) else None
    msgs = len(messages) if isinstance(messages, list) else 0
    try:
        size = len(json.dumps(body, ensure_ascii=False).encode("utf-8"))
    except Exception:  # noqa: BLE001 - 画像失败绝不能影响转发
        size = 0
    detail = str(exc.detail or exc.message or "").replace("\n", " ")
    max_tokens = body.get("max_tokens", "-") if isinstance(body, dict) else "-"
    logger.warning(
        "Marvis 失败画像 kind=%s status=%s code=%s acc=%s model=%s stream=%s "
        "bytes=%d msgs=%d max_tokens=%s degraded=%s detail=%s",
        exc.kind, exc.status or "-", exc.code or "-",
        int(getattr(account, "id", 0) or 0), model or "-",
        "1" if stream else "0", size, msgs, max_tokens,
        "1" if degraded else "0", detail[:200])


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

    ## 3000100 与 5100007：同号重试一次，但失败后的处置**相反**

    两者的共同点是「**不是这个号的错**」：都不该 `note_failure`（err_count /
    冷却会把好号一路推向熔断），也都不该吃 `MAX_ATTEMPTS` 的换号名额。
    但之后必须分开走：

      · `3000100`（额度族）：第一次命中就**就地**把 `max_tokens` 降到
        `MAX_TOKENS_DEGRADE_TARGET` 再同号试一次；降过还失败 ⇒ 这个号在这个
        模型上的当次额度不够，标 (账号 × 模型) 后**换号** —— 换一个号就有预算。
      · `5100007`（上游服务端内部错误）：同号重试一次，仍失败**直接报错**。
        换号只会把同一份 body 打进另一个好号，给它也记一次失败。

    降级是**就地改同一个 dict**：值必须跟着换号走，否则每个新号都要先用原值
    白撞一次 3000100 再降级，白耗一轮上游往返。
    """
    model = str(body.get("model") or "")
    # 平台级风控退避：只有**连续**撞满阈值才可能走到这里（见 note_risk_control），
    # 窗口内一个字节都不打上游。它不动任何账号（不打 cool_until、不 exclude），
    # 退避一过号池原样可用。
    remaining = risk_backoff_remaining()
    if remaining > 0:
        logger.warning(
            "Marvis 平台级风控退避中，拒绝上游调用：剩余 %.0fs model=%s",
            remaining, model or "-")
        raise _risk_control_rejected()
    exclude: set[int] = set()
    last_error: client.UpstreamError | None = None
    dropped: set[str] = set()
    # `MAX_ATTEMPTS` 数的是「换了几个号」；参数降级是**同一个号上的同一条
    # 请求**，单独记数，否则「三次机会」会被降级重试吃光。
    attempts = 0
    param_retries = 0
    # 上下文超限的重试计数。和参数降级分开，因为失败原因正交：
    # 一条请求既可能字段不被认（3000101），也可能这次路由到的后端装不下
    # （措辞类超限，见 `_CONTEXT_OVERFLOW_WORDS`；3000100 是额度族，不在此列）。
    ctx_retries = 0
    # 「这条请求的 max_tokens 是否已经被降级」—— **请求级**，不是账号级：
    # 降级值是就地写进 body 的，换号后仍然生效，所以日志里的 degraded 必须
    # 反映整条请求的状态，否则第二个号会同时打出「max_tokens=32768」和
    # 「degraded=0」，事后读不出它到底是不是降级后的那一发。
    degraded_max_tokens = False
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
        # 同号重试的计数是**每个号各自一份**：不吃 `MAX_ATTEMPTS` 的换号名额，
        # 也不重新选号（租约继续持有，见下面两处 `continue`）。
        internal_retries = 0
        try:
            data = marvis_accounts.ensure_token(db, account)
            # 同号重试循环：只有「降级」与「上游内部错误」这两种**与账号无关**的
            # 失败会 `continue`；其余一律 `raise` 给下面那个 except 统一处置
            # （换号 / 停 / 冷却）。
            #
            # ⚠ 这个 `while True` 必须在 try 内部、且**包住所有上游调用**。
            #   之前与 PR #67 合并时出过事故：风控分支的 `return` 被插到循环
            #   前面，`while` 整个变成死代码 —— 表现是「降级重试从不发生」，
            #   而且流式路径直接越过循环返回，非流式却在循环外返回。
            while True:
                try:
                    if stream:
                        resp = client.open_chat_stream(
                            data, realm, body, account_id=int(account.id or 0))
                        clear_risk_hits()
                        return resp, account, started
                    result = client.chat_completions(
                        data, realm, body, account_id=int(account.id or 0))
                    shared.note_success(account)
                    # 成功一次就把 4100404 的「连续」计数清零：阈值判的是**连续**
                    # 成串，不是历史累计（否则一天里零散撞 5 次也会锁一次平台）。
                    clear_risk_hits()
                    shared.release(account)
                    return result, account, started
                except client.UpstreamError as exc:
                    # 失败画像：**每一次**上游失败都留痕，包括即将被降级重试的
                    # 第一次命中 —— 否则事后无法判定「这一发是原值还是降级值」。
                    # 画像自带 max_tokens 与 degraded 两列（判据缺一不可）。
                    _log_failure_profile(account, exc, model=model,
                                         stream=stream, body=body,
                                         degraded=degraded_max_tokens)
                    # ① 3000100（额度族）：把输出预算降下来，**同号**再试一次。
                    #
                    # 就地改 `body`（不是 `body = dict(body)`）是硬要求：这个
                    # dict 是整条请求共用的，降级后的值必须跟着换号走 —— 否则每个
                    # 新号都会先拿原值白撞一次 3000100 再降级，白耗一轮上游往返。
                    #
                    # 两种情况才降：
                    #   · want == 0：请求根本没带输出预算，上游会按它自己的默认值
                    #     算总预算 —— 这时显式给 32768 是**收紧**，不是放宽；
                    #   · want > 32768：显式要得太长，压回 32768。
                    # 0 < want <= 32768 时不动（降了等于把用户要的更短的长度改长），
                    # 直接交给外层换号。
                    if not degraded_max_tokens and _is_quota_exhausted(exc):
                        want = _max_tokens_of(body)
                        if want == 0 or want > MAX_TOKENS_DEGRADE_THRESHOLD:
                            body["max_tokens"] = MAX_TOKENS_DEGRADE_TARGET
                            body.pop("max_completion_tokens", None)
                            body.pop("max_output_tokens", None)
                            degraded_max_tokens = True
                            logger.warning(
                                "Marvis max_tokens 降级同号重试 degraded=1 "
                                "acc=%s from=%s to=%d（3000100 是额度族：这个号"
                                "在这个模型上的当次预算不够，先省输出预算）",
                                int(account.id or 0), want or "-",
                                MAX_TOKENS_DEGRADE_TARGET)
                            continue
                    # ② 5100007（上游服务端内部错误）：同号重试一次。
                    #
                    # **绝不换号**：同一份 body 打给任何号都是同一个答案，换号只会
                    # 把好号也记一次失败。仍失败就交给外层如实报错。
                    if (internal_retries < INTERNAL_RETRIES
                            and _is_upstream_internal(exc)):
                        internal_retries += 1
                        delay = (INTERNAL_RETRY_BASE
                                 + random.uniform(0, INTERNAL_RETRY_JITTER))
                        logger.warning(
                            "Marvis 上游内部错误同号重试 acc=%s code=5100007 "
                            "第 %d/%d 次，退避 %.2fs（不换号）",
                            int(account.id or 0), internal_retries,
                            INTERNAL_RETRIES, delay)
                        time.sleep(delay)
                        continue
                    raise
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
            # ① 3000100（额度族）：同号降级已经在上面用掉了，仍失败 ⇒
            # **该号在该模型上的当次额度不够**，换号重试。
            #
            # 与 5100007 的分界（两者都不记账号失败，但处置相反）：
            #   · 3000100 = 额度：换一个号就有预算 → 标 (账号 × 模型) 后换号；
            #   · 5100007 = 上游内部错误：换号只会把好号也打脏 → 同号重试后停。
            quota_code = _quota_code_of(exc)
            if quota_code:
                _note_model_quota_tight(db, account, model, code=quota_code)
                last_error = exc
                logger.warning(
                    "Marvis 额度不足换号 | acc=%s | model=%s | code=%s | "
                    "max_tokens=%s | 同号降级重试已用尽",
                    account.id, model or "-", quota_code,
                    body.get("max_tokens", "-"))
                exclude.add(account.id)
                # 不吃 note_failure：号本身是健康的，只是「这个模型 + 当次」额度
                # 不够 —— 计 err_count 会把它一路推向熔断。
                continue
            # ② 5100007（上游服务端内部错误）：同号重试也在上面用掉了，
            # 仍失败就如实报错 —— **不换号、不 note_failure、不进 exclude**。
            if _is_upstream_internal(exc):
                last_error = exc
                logger.warning(
                    "Marvis 上游内部错误，同号重试已用尽（不换号、不记账号失败）"
                    " | acc=%s | %s",
                    account.id, (exc.detail or exc.message or "")[:200])
                break
            # 上下文超限（400/413 + 措辞类信号）：**原样重发**，靠重新路由过关。
            #
            # ⚠ 3000100 **不在这里**：它是额度族，处置是「降 max_tokens → 换号」，
            # 见上面那个分支；`_is_context_overflow()` 也会先按码排掉它。
            #
            # 为什么不裁消息：对话历史就是任务本身。删掉一半，模型不会
            # 「少看几轮」，它会看不到前面的约束和已确认的决策，**把任务做错**。
            # 一个悄悄做错的任务，比一条明确的报错糟糕得多。
            #
            # 为什么原样重发有可能过：`main-auto` 是自动路由槽位，同一个模型名
            # 每次派到哪个后端由上游决定。实测同一段 40 万字符的历史，
            # 一次落到装不下的后端（措辞类超限），下一次落到装得下的后端（200）。
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
            # 设备 / 内容风控（4100404）：**请求级失败，不冷却账号、不换号**。
            #
            # 以前这里给当前号打 60 秒冷却（更早是 600 秒）并换号重试：同一份 body
            # 在 1 秒内被重放给另外几个号 —— 那正是风控引擎最敏感的「同源短时重放」
            # 特征，几个号一起吃冷却，选号随即返回空，网关自己把请求挡在门外
            # （2026-11-17 用户实测：09:23 撞完，09:24 起全 503）。
            #
            # 换号 100% 无效：风控判的是这条请求 / 这个出口，不是账号。
            # 所以这里只记一次审计（`cool_seconds=0`，不打冷却），然后：
            #   · 未撞满阈值 → 本次请求按「这一条被拦」报给客户端（503）；
            #   · 撞满阈值   → 开平台级退避窗口，之后的请求在 `_attempt` 入口就拒。
            if exc.kind == "risk_control":
                # 记一次**审计**（`cool_seconds=0`，不打冷却）。
                #
                # ⚠ 这里容易误读成「又给账号记账了」：`cool_seconds=0` 只表示
                #   「不加冷却窗口」，note_failure 本身仍会留痕（`last_err_msg`
                #   / 哨兵事件），排查时要能看出「这个号撞过风控」。
                #   PR #67 原文就是这么写的，test_marvis_risk_backoff 也钉了
                #   这条（`note_calls[0]["cool_seconds"] == 0`）。
                shared.note_failure(
                    db, account, kind=exc.kind, detail=exc.detail or exc.message,
                    status=exc.status, model=model, public_platform=PUBLIC,
                    cool_seconds=0)
                backoff = note_risk_control()
                hits = risk_hits()
                if backoff > 0:
                    logger.warning(
                        "Marvis 风控连续命中 %d/%d 次（acc=%s）：进入平台级退避 %.0fs",
                        hits, _risk_backoff_threshold(), account.id, backoff)
                else:
                    logger.warning(
                        "Marvis 风控命中（acc=%s）：单次不触发平台退避，仅本次请求"
                        "失败（连续 %d/%d 次）",
                        account.id, hits, _risk_backoff_threshold())
                raise _risk_control_rejected(exc.detail or exc.message)
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
    except Exception:  # noqa: BLE001
        alive, expired = [], []
    if alive and len(expired) == len(alive):
        raise shared.PlatformRejected(
            503, "Marvis 账号登录态已全部过期，请在控制台「授权登录」重新扫码"
                 "（access_token 由上游按天轮换，过期后只能重登）",
            kind="server_error", code="credential_expired",
            headers={"Retry-After": "5"})
    # ## 风控冷却段已删除（PR #67）

    # 原来这里有一条「全池都在风控冷却 → 429 + Retry-After: 600」。
    # 删掉的理由见 PR #67：
    #
    #   4100404 判的是「这条请求有没有越过上游内容黑名单」「这个出口是不是
    #   成串被拦」，不是账号好坏。给命中账号打冷却会把好号坏号一起停掉，
    #   而换号 100% 无效（同一份 body 换个号重放还是被拦）。
    #
    #   更糟的是它会自我循环：命中 → 锁 60s → 到期 → 再命中，实测 20 分钟
    #   内 59 次 503，而上游真正拒绝的只有 8 次；退避期内所有请求连上游都
    #   打不到，客户端只看到「都在冷却中」。
    #
    # 现在由**平台级退避**接管（`_RISK_BACKOFF`）：单次命中不算账号失败、
    # 不写账号状态；只有窗口内连续撞满阈值才进入退避，退避期过了号池原样可用。

    # **额度耗尽 ≠ 冷却**（2026-11-18 事故的直接放大器）。
    #
    # 上游 3000001 是**账号级**总额度用尽：几个号的日额度可以同时打满，
    # 凭证、登录态、风控全部正常，只有钱包是 0。选号用的是 `min_balance=0`
    # （余额 0 的号全部被滤掉）⇒ 一个号都选不到，走到这里被报成
    # 503「都在冷却或排队中，请稍后重试」。
    #
    # 那句话把用户送去「等冷却到期」，而冷却永远不会到期 —— 真正要等的是
    # **上游每日 00:00(+08) 的配额重置**（长则十几小时），或者加号。
    # 两种结局完全不同，必须分开报：一个是「几分钟后就好」，一个是「等重置」。
    #
    # 判据只看 `alive`：active 且余额 <= 0 的号算「没配额」；NULL（从没同步过
    # 余额）不计入 —— 未知不等于耗尽，那种情况仍按冷却重试。
    zero = [r for r in alive
            if getattr(r, "balance_remain", None) is not None
            and int(getattr(r, "balance_remain", 0) or 0) <= 0]
    if alive and len(zero) == len(alive):
        logger.warning(
            "Marvis 额度已耗尽 realm=%s model=%s active=%d 余额全为 0"
            "（上游 3000001：等待 00:00+08 配额重置）",
            realm, model or "-", len(alive))
        # 走 `client.UpstreamError` 而不是 `shared.PlatformRejected`：只有前者
        # 会经过 `_fail()` / `_error_payload()`，错误体才能带上 family /
        # upstream_code 两个字段（PlatformRejected.openai() 只出 type/message/code）。
        raise client.UpstreamError(
            "Marvis 账号额度已耗尽（上游 3000001 用户名下token不足）："
            "上游每日 00:00(+08) 为各账号重置额度，请等待重置或在控制台加号",
            status=402, code="quota_exhausted", kind="quota_exceeded",
            # detail 保持上游错误体的形状：`_error_payload()` 用
            # `client._biz_code_of()` 从这里抠业务码，抠不到 `upstream_code`
            # 字段就丢了（而它正是排障时要与上游工单对齐的那一列）。
            detail='{"code":3000001,"msg":"用户名下token不足；'
                   '等待上游 00:00(+08) 配额重置"}')
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


#: 一次从上游读多少字节。
#:
#: 配 `read1()` 用，**不是**配 `read()` —— 见 `_iter_upstream()`。
_STREAM_CHUNK = 65536

#: SSE 帧分隔符：一个空行。一帧由若干字段行 + 一个空行收尾。
_FRAME_SEP = b"\n\n"

#: 攒到这么多字节还没见到空行就原样放行。
#:
#: 防守用：上游如果根本不是 SSE（或迟迟不发空行），缓冲会一直长，「按帧
#: 对齐」就变成「攒到 EOF 才吐」—— 那比撕裂更糟。宁可失去对齐，也不能
#: 把流卡死。
_FRAME_MAX = 262144

#: 一条 SSE 字段行的前缀（data / event / id / retry / 注释）。
_SSE_FIELD_PREFIXES = (b"data:", b"event:", b"id:", b"retry:", b":")


def _flush_tail(tail: bytes) -> bytes:
    """给 EOF 前没等到空行的**半个帧**补上分隔符；非 SSE 尾巴原样返回。

    ## 为什么必须补

    上游最后一段常常是裸的 `data: [DONE]` —— **连一个换行都没有**，这正是
    「`data: [DONE]` 与终止帧粘连」的精确触发条件（有换行、但没有空行时
    **不**触发：`_scan_stream` 按换行切行，那一行已经能判读）。不补的话
    紧随其后的帧（心跳 / `_terminal_frame`）会直接粘在这一行后面：

        data: [DONE]data: {"id":"chatcmpl-gateway", ...}

    一行两帧，客户端按行 `json.loads` 必炸。

    ## 为什么带守卫

    这条路径同时承担**原样透传**（上游 200 + JSON 错误体、非 SSE 的响应
    对象）：尾巴不是 SSE 字段行时一个字节都不能改，否则「转发不改字节」
    这条承诺就破了。
    """
    if not tail:
        return tail
    if not any(line.startswith(_SSE_FIELD_PREFIXES)
               for line in tail.split(b"\n")):
        return tail
    return tail + b"\n" if tail.endswith(b"\n") else tail + _FRAME_SEP


def _open_frame(chunk: bytes) -> bool:
    """块是不是停在**半个帧**里（还没等到帧分隔符）。

    判据宁窄勿宽：去掉前导缩进后，以 SSE 字段行（data / event / id / retry /
    注释）或 JSON 片段（{ / [）起头，才算半帧。非 SSE 的普通字节（测试桩、
    纯文本透传）一律返回 False —— 心跳照发，行为与加守卫之前完全一致。
    """
    head = chunk.lstrip()
    return head.startswith(_SSE_FIELD_PREFIXES) or head[:1] in (b"{", b"[")


async def _iter_upstream(resp):
    """把同步 `HTTPResponse` 的 read 挪到工作线程，避免卡住事件循环。

    用 `asyncio.to_thread`（而不是裸 `run_in_executor`）：它会带上当前
    contextvars，`request_metrics` / 实时面板那套按请求隔离的上下文才不会丢。

    ## 为什么是 `read1()` 而不是 `read()`（首字延迟）

    `HTTPResponse.read(n)` 底层是 `BufferedReader.read(n)` —— 它会**一直读到凑满
    n 字节或 EOF** 才返回。SSE 的帧又小又慢（一个 token 十几字节、几十毫秒一个），
    于是「攒满 8KB 才吐一次」变成常态：客户端看到的是**先卡一秒、再一次性刷一屏**，
    而不是逐字出。流式体验被这一行吃掉了大半，且越慢的模型越明显。

    `HTTPResponse.read1(n)` 只做**一次**底层读取，有多少给多少（chunked /
    `Content-Length` 的解码它都自己处理，语义安全），所以：

      · 上游吐一帧 → 这里立刻拿到并转发（真正的逐帧流式）；
      · `n` 可以放大到 64KB 而**不会**引入等待（读不到就返回，不会等满），
        顺带把 `to_thread` 的线程切换次数从「每 8KB 一次」降到「有多少读多少」。

    兜底：极老的 Python 或自定义 `resp` 没有 `read1` 时退回 `read`，行为与以前一致。

    ## 为什么还要按帧对齐（2026-11-19 修）

    `read1()` 的块边界与 SSE 帧边界**无关**：一帧可能被切成两块，两块也可能
    凑成一帧。下游 `_with_keepalive()` 在空闲 `PING_INTERVAL_S`（8s）后插一帧
    心跳，它只看「有没有新块」、不看「块停在哪」—— 于是心跳会落在
    `data: {"content":"hel` 与 `f"}}]}` 之间，客户端按行 `json.loads` 必炸。

    **对齐放在这一层**：出口只有完整帧（以空行收尾），下游无论怎么插心跳都
    只能落在帧边界上。同一处顺带把 EOF 前的半帧补上分隔符，收尾帧也就不会
    和裸的 `data: [DONE]` 粘成一行。
    """
    read = getattr(resp, "read1", None) or resp.read
    buf = b""
    while True:
        chunk = await asyncio.to_thread(read, _STREAM_CHUNK)
        if not chunk:
            break
        buf += chunk
        # 只把**已完整**的帧交出去（见 docstring 末段）：半帧留在 buf 里，
        # 等下一块拼齐。read1() 的块边界与 SSE 帧边界无关，直接透传就会
        # 让下游的空闲心跳插在半帧中间。
        frames = buf.split(_FRAME_SEP)
        buf = frames.pop()
        for frame in frames:
            yield frame + _FRAME_SEP
        if len(buf) > _FRAME_MAX:
            # 上游不是 SSE（或迟迟不发空行）：宁可失去对齐，也不能卡死
            yield buf
            buf = b""
    # EOF：尾巴是半帧就补齐分隔符。不补的话紧随其后的帧会粘在这一行后面
    # —— 客户端看到 `data: [DONE]data: {...}`，一行两帧。
    if buf:
        yield _flush_tail(buf)


def _inband_error(obj: dict) -> str:
    """识别「HTTP 200 + 流内错误帧」的那一类失败，返回错误描述（正常帧返回空串）。

    ## 为什么需要（对齐 FIXED-BUGS 五·补51 / REQUEST-BODY-CONTRACT §5）

    上游有一部分失败**不走状态码**：HTTP 200、`Content-Type: text/event-stream`
    照旧，但流里只有一条错误帧、没有 `choices`：

        data: {"error":{"code":3000101,"message":"...非法参数..."}}

    `client._classify()` 只看得到 200，所以整条链路按成功处理：错误帧被原样
    当正文透传给客户端、`finally` 里 `note_success()`、用量日志记一笔正常
    调用。现场表现是「网关全绿、客户端一直重试」—— 因为客户端拿到的是一个
    没有任何 `finish_reason` 的流，SDK 会判定流被截断并按退避重试。

    判据刻意收得很窄：**有 `choices` 就不当错误**（正常帧里也可能带 `error`
    字段的网关扩展），只有「没 `choices` + 有错误键」才算。
    """
    if not isinstance(obj, dict):
        return ""
    if obj.get("choices"):
        return ""
    err = obj.get("error")
    if isinstance(err, dict):
        msg = err.get("message") or err.get("msg") or err.get("detail") or ""
        code = err.get("code") or err.get("error_code") or ""
        return ("%s%s" % ("%s " % code if code else "", msg)).strip() or "stream error"
    if isinstance(err, str) and err.strip():
        return err.strip()
    if obj.get("error_code") or obj.get("error_msg"):
        return ("%s %s" % (obj.get("error_code") or "",
                           obj.get("error_msg") or "")).strip()
    return ""


def _terminal_frame(model: str) -> bytes:
    """补一帧终止帧：`finish_reason=stop` + `[DONE]`。

    流「有头无尾」是客户端无限重试的根因 —— OpenAI SDK 靠 `finish_reason`
    判定，裸 SSE 客户端靠 `[DONE]`，两个都要给。上游出错帧、提前 EOF、
    被截断时都补上：让客户端**正常收尾**，原因由前面那条错误帧自己说明，
    而不是让它以为连接掉了、换个号再来一遍。
    """
    frame = {"id": "chatcmpl-gateway", "object": "chat.completion.chunk",
             "created": int(time.time()), "model": model,
             "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
    try:
        body = json.dumps(frame, ensure_ascii=False).encode("utf-8")
    except Exception:  # noqa: BLE001 - 兜底：手写字面量，绝不让收尾失败
        body = b'{"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}'
    # ⚠ `data: ` 前缀不能省：SSE 只认 `data:` / `event:` / `id:` / `retry:` 四种
    # 字段行，裸 JSON 行会被解析器**直接丢弃** —— 那样补出来的终止帧里只剩
    # `[DONE]` 生效，`finish_reason` 那一帧等于没发（客户端仍然判定流被截断）。
    return b"data: " + body + b"\n\ndata: [DONE]\n\n"


def _scan_stream(pending: bytes, chunk: bytes, state: dict) -> bytes:
    """扫描 SSE 字节流，抽取 `usage` / `finish_reason` / 流内错误。

    返回「还没凑成整行的尾巴」，由调用方在下一个 chunk 前拼回去 —— 一帧
    被 TCP 切成两半是常态，按 chunk 独立解析会漏掉恰好被切开的那条错误帧
    或 usage 帧。只做**只读**解析，转发的字节一个都不改。
    """
    if not isinstance(chunk, (bytes, bytearray)):
        return pending
    data = (pending or b"") + bytes(chunk)
    lines = data.split(b"\n")
    pending = lines.pop()
    # 上游若长时间不发换行，尾巴会无限增长；超过 1MB 说明不是正常 SSE，
    # 直接丢弃（解析已经不可能靠拼行救回），只保留末尾供下次重试。
    if len(pending) > 1_048_576:
        pending = pending[-65536:]
    for line in lines:
        line = line.strip()
        if not line.startswith(b"data:"):
            continue
        payload = line[5:].strip()
        if not payload:
            continue
        if payload == b"[DONE]":
            state["done"] = True
            continue
        try:
            obj = json.loads(payload)
        except Exception:  # noqa: BLE001 - 非 JSON 帧不属于我们的观测范围
            continue
        if not isinstance(obj, dict):
            continue
        if isinstance(obj.get("usage"), dict):
            state["usage"] = obj["usage"]
        choices = obj.get("choices")
        if isinstance(choices, list):
            for item in choices:
                if isinstance(item, dict) and item.get("finish_reason"):
                    state["finish"] = True
        elif not state.get("error"):
            state["error"] = _inband_error(obj)
    return pending


def _usage_of(result: dict) -> dict:
    usage = result.get("usage") if isinstance(result, dict) else None
    return usage if isinstance(usage, dict) else {}


#: 流读到一半上游断开（socket reset / read timeout）时补发的 error 帧。
#:
#: `_terminal_frame()` 只保证「流有结尾」，不说原因；断流时上游并没有发过任何
#: 错误帧，客户端看到的就是一个干净的 stop —— 所以这里先补一帧 error 把原因
#: 说清楚，再让调用方补终止帧。只出现在异常路径上，正常流一个字节都不改。
_STREAM_BROKEN_FRAME = ("data: %s\n\n" % json.dumps(
    {"error": {"type": "upstream_stream_broken",
               "code": "stream_broken",
               "message": "Marvis 上游流在中途断开（读超时或连接被重置），"
                          "已收到的内容保持有效，请重试本轮"}},
    ensure_ascii=False)).encode()


# ---------------------------------------------------------------------------
# 空闲心跳（对齐 loomy / codearts / qoder / raccoon / trae / minimax 的做法）
# ---------------------------------------------------------------------------
#
# ## 为什么 marvis 原来没有、现在必须有
#
# 其余六个平台在把流交给 `StreamingResponse` 之前都套了一层
# `bridge.with_pings(...)`，只有 marvis 是**原始字节直通**。直通本身没错
# （上游就是 OpenAI 协议，改字节反而有风险），但少了一层「我来保证链路活着」：
#
#   · DeepSeek-V4.1 这类模型**先流式思考 5~10 秒**才吐正文，这段时间上游
#     一个字节都不发（loomy 的实测记录：ttfb 9.33s / latency 9.49s）；
#   · 客户端（Cherry Studio / LobeChat / Claude Code / DSH）按 idle 超时判
#     「流死了」并掐断，中间层（CF / nginx / 反代）也会按读超时断链。
#
# 结果就是「明明在思考，界面上卡住不动，然后连接断了」。
#
# `bridge.CHAT_PING` 是**合法的空增量 data 帧**，不是 SSE 注释帧 ——
# 注释帧不产生事件、不重置客户端 idle 计时器，等于没发（bridge 顶部有实测）。
#
# ## 为什么不用现成的 `bridge.with_pings`
#
# 那是**同步**迭代器版本（读线程 + queue），而 marvis 的 generator 是 async
# 的（读已经在 `asyncio.to_thread` 里）。这里用 `asyncio.wait` 而**不是**
# `asyncio.wait_for`：超时不取消内层 `__anext__()` 的 task，只是先吐一帧心跳
# 再接着等同一个 task —— 始终只有一个读线程，不会踩到 bridge 顶部注释里那个
# 「取消后重起线程并发消费同一条流、行被撕碎」的坑。
#
# ## 为什么不给 `/v1/responses` 加
#
# Responses 流的事件带 `sequence_number`，客户端按它排序去重。
# `bridge.RESPONSES_PING` 目前的 `sequence_number` 固定为 0（bridge 自己标注
# 「占位形状、没有调用方」），插进流中间可能与真实事件序号冲突 —— 宁可不发，
# 也不能让 Codex 这类严格校验的客户端报序号错。

_KEEPALIVE_ENV = "MIRAIHUB_MARVIS_KEEPALIVE"
_KEEPALIVE_OFF = frozenset({"0", "false", "off", "no"})


def _keepalive_enabled() -> bool:
    """心跳开关，默认开；`MIRAIHUB_MARVIS_KEEPALIVE=0` 整体关掉。

    留开关的理由与内容改写（`SANITIZE_TENCENT_ENV`）一致：这是**往客户的流里
    插入我们自己造的帧**，出问题必须能一键退回原样透传，而不是改代码。
    """
    return (os.getenv(_KEEPALIVE_ENV) or "").strip().lower() not in _KEEPALIVE_OFF


async def _with_keepalive(source, *, interval: float, ping: bytes):
    """给 async 生成器加空闲心跳：`interval` 秒没新块就发一帧 `ping`。

    超时**不取消**内层那次读取（见上），只把心跳先吐出去。

    ## 帧边界守卫（第二道防线）

    `_iter_upstream()` 已经保证「出口只有完整帧」，所以正常路径上这个守卫
    不会触发。它防的是**将来**：谁再往这条链上接一个按字节切块的源，心跳就
    会插进半帧中间，客户端按行 `json.loads` 必炸。

    判据刻意保守，两条都满足才判「半帧」：块不以空行收尾，且**要么**已经
    见过空行（确认这是分帧的 SSE），**要么**块本身是 SSE 字段行 / JSON 片段
    的起头（`_open_frame`）。纯文本块（测试桩、非 SSE 透传）照常发心跳 ——
    行为与加守卫之前完全一致，不猜。
    """
    iterator = source.__aiter__()
    pending: "asyncio.Future | None" = None
    framed = False   # 见过空行 → 确认这是按帧分隔的 SSE
    tail = b""       # 上一块的末尾，用来判断它停在不在帧边界上
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(iterator.__anext__())
            done, _ = await asyncio.wait({pending}, timeout=interval)
            if not done:
                if not tail.endswith(_FRAME_SEP) and (framed or _open_frame(tail)):
                    # 停在半帧中间：这一轮不插心跳，接着等同一块
                    continue
                yield ping
                continue
            try:
                chunk = pending.result()
            except StopAsyncIteration:
                return
            finally:
                pending = None
            if chunk:
                framed = framed or _FRAME_SEP in chunk
                tail = chunk
            yield chunk
    finally:
        # 客户端断开 / 上层取消：把还挂着的读任务收掉，别让它悬着
        if pending is not None and not pending.done():
            pending.cancel()
            try:
                await pending
            except BaseException:  # noqa: BLE001 - 取消与收尾异常都不外抛
                pass


def _wrap_keepalive(response, ping: bytes):
    """给 `StreamingResponse` 套一层空闲心跳；其它响应原样返回。"""
    if not _keepalive_enabled() or not isinstance(response, StreamingResponse):
        return response
    body = getattr(response, "body_iterator", None)
    if body is None:
        return response
    from admin.platform import protocol_bridge as bridge

    return StreamingResponse(
        _with_keepalive(body, interval=bridge.PING_INTERVAL_S, ping=ping),
        status_code=response.status_code,
        headers=dict(response.headers))


async def chat_completions(request: Request):
    """OpenAI 兼容入口 —— 本平台的唯一主路径。"""
    # 体积兜底放在**最前面**：早于任何上游动作，超限就地 413，不打上游、不占账号。
    oversized = await _reject_oversized(request)
    if oversized is not None:
        return oversized
    body = await _body_of(request)
    realm = _realm(request)
    try:
        slot = _validate_model(body)
    except _BadRequest as exc:
        return JSONResponse({"error": {"type": exc.code, "message": exc.message,
                                       "code": exc.code}},
                            status_code=404 if exc.code == "model_not_found" else 400)

    # `_prepare_body` 是**纯 CPU**：7 条内容风控正则 + 首行身份改写 + 角色归一。
    # 实测 DSH 这种 60KB 级 system prompt 要 **2.1ms**，200KB 的历史要 **7.0ms**，
    # 而它原来直接跑在事件循环线程上、在首字节之前 —— 单 worker 下并发几十路
    # 就是几百毫秒的串行排队（与 2026-11-16 那类事故同一形状，只是不联网）。
    # 一次 `to_thread` 往返约 50μs，换掉这段完全划算。
    upstream_body = await asyncio.to_thread(_prepare_body, body, slot)
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
        # 非流式同样可能是「HTTP 200 + 错误体」：`_classify()` 只能按状态码判，
        # 这类拒绝会一路被当成成功记账。原样把上游 body 交给调用方（不改语义），
        # 但记账要标成失败，否则面板上看不出真实成功率。
        nonstream_error = _inband_error(resp if isinstance(resp, dict) else {})
        if nonstream_error:
            client.note_sentinel(realm, int(getattr(account, "id", 0) or 0),
                                 "upstream_business", code="body_error",
                                 status=200, detail=nonstream_error)
            logger.warning("Marvis 非流式错误体（HTTP 200）| acc=%s | %s",
                           account.id, nonstream_error[:200])
        await _bill_async(key, account, billed_model, usage, request=request,
                          started=started,
                          error=("upstream_business" if nonstream_error else ""))
        return JSONResponse(resp)

    async def generator():
        stats = {"ttfb_ms": 0, "total_ms": 0}
        # `state` 由 `_scan_stream` 增量填充：usage / finish / done / error。
        state: dict = {}
        first_at = None
        began = time.perf_counter()
        pending = b""
        try:
            async for chunk in _iter_upstream(resp):
                if first_at is None:
                    first_at = time.perf_counter()
                    # 亚毫秒首块取整会得到 0，而 0 在 `_bill` 里表示「没测到」，
                    # 会被兜底成整条流的 latency —— 等于把 4ms 的 TTFB 记成
                    # 800ms。下限钉在 1。
                    stats["ttfb_ms"] = max(1, int((first_at - started) * 1000))
                yield chunk
                # 只读旁路：解析 usage / finish_reason / 流内错误。
                # **转发的字节一个都不改**，只记账与收尾需要这些信息。
                pending = _scan_stream(pending, chunk, state)
            # EOF：最后一段可能没带换行（裸 `data: [DONE]`），`_scan_stream`
            # 只缓存不成行的尾巴、不判读 —— 补一个分隔符再扫一遍，否则 `done`
            # 认不出来：白白多补一帧终止帧，还会和 `[DONE]` 粘成一行。
            if pending:
                _scan_stream(pending, _FRAME_SEP, state)
            # 上游既没给 `finish_reason` 也没给 `[DONE]`（错误帧 / 提前 EOF /
            # 被截断）→ 补终止帧。不补的话客户端判定流被截断，按退避无限重试，
            # 而网关这边一条日志都没有（FIXED-BUGS 五·补51）。
            if not (state.get("finish") or state.get("done")):
                logger.warning(
                    "Marvis 流缺终止帧，已补发 | acc=%s | %s",
                    account.id,
                    "流内错误" if state.get("error") else "提前 EOF")
                yield _terminal_frame(billed_model)
        except Exception as exc:  # noqa: BLE001
            # 上游读超时 / 连接被重置。`_iter_upstream` 抛的是 urllib / socket 的
            # 原始异常（`open_chat_stream()` 早就返回了 resp，之后的读不再包装）。
            # 不接住的话这条协程带异常结束：客户端只看到连接断，而 `finally` 里
            # `state` 没有 error → `note_success()` 会把这次失败记成成功。
            # `CancelledError` 是 BaseException，客户端主动断开不会被这里吞掉。
            previous = str(state.get("error") or "")
            state["error"] = ("%s（读中断：%s）" % (previous, type(exc).__name__)
                              if previous else
                              "%s: %s" % (type(exc).__name__, str(exc)[:160]))
            logger.warning("Marvis 上游流中断 | acc=%s | %s: %s",
                           account.id, type(exc).__name__, str(exc)[:160])
            yield _STREAM_BROKEN_FRAME
            # 再补终止帧：客户端不会因为「没有 finish_reason」而按退避重试
            yield _terminal_frame(billed_model)
        finally:
            stats["total_ms"] = int((time.perf_counter() - began) * 1000)
            client.close_stream(resp)
            usage = state.get("usage") if isinstance(state.get("usage"), dict) else {}
            error = str(state.get("error") or "")
            if error:
                # 流内错误 = 上游明确拒绝。三件事分开做：
                #   · 不 `note_success()` —— 否则把刚被拒的号记成「成功过」，
                #     失败计数清零，坏号永远洗不白也筛不掉；
                #   · 也不 `note_failure()` —— 这一层分不清「号坏了」还是
                #     「这个请求参数它不认」，后者换号 100% 无效，误记会把
                #     一池好号挨个打上失败记录；
                #   · 上哨兵 + 记账 error_kind —— 让「网关全绿、客户端重试」
                #     这种静默故障在控制台可见，而不是等用户来报。
                client.note_sentinel(realm, int(getattr(account, "id", 0) or 0),
                                     "upstream_business", code="stream_error",
                                     status=200, detail=error)
                logger.warning("Marvis 流内错误（HTTP 200，已补终止帧）| acc=%s | %s",
                               account.id, error[:200])
            else:
                shared.note_success(account)
            shared.release(account)
            # 记账是同步 DB 写，必须离开事件循环（客户端断开时也不会丢账，
            # 见 `_bill_async` 里的 shield）。
            await _bill_async(key, account, billed_model, usage, request=request,
                              started=started, stats=stats,
                              error=("upstream_business" if error else ""))

    # 空闲心跳（见 `_with_keepalive`）：上游慢思考时几十秒无字节，
    # 没有这一层客户端会按 idle 超时把流掐断。
    from admin.platform import protocol_bridge as bridge

    return _wrap_keepalive(
        StreamingResponse(generator(), media_type="text/event-stream",
                          headers={"Cache-Control": "no-cache",
                                   "X-Accel-Buffering": "no"}),
        bridge.CHAT_PING)


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
            # 心跳要在**中继输出这一侧**再加一层：内层注入的 chat 形态空增量
            # 帧会被中继吞掉（它没有 content / tool_calls，中继不产出事件），
            # 客户端依然一字节都看不到。这里用 Anthropic 官方 `ping` 事件 ——
            # SDK 会触发 message 上的 ping 回调，语义正确。
            return _wrap_keepalive(
                bridge.anthropic_stream_relay(_Inner(response), model,
                                              prefix="marvis"),
                bridge.ANTHROPIC_PING)
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
    # 体积兜底放在**最前面**：早于任何上游动作，超限就地 413，不打上游、不占账号。
    oversized = await _reject_oversized(request)
    if oversized is not None:
        return oversized
    body = await _body_of(request)
    normalized = _anthropic_to_openai(body)
    return await _run_normalized(request, normalized, protocol="anthropic")


async def responses(request: Request):
    """Responses 入口 → 折叠成 chat/completions → 响应翻回 Responses。"""
    # 体积兜底放在**最前面**：早于任何上游动作，超限就地 413，不打上游、不占账号。
    oversized = await _reject_oversized(request)
    if oversized is not None:
        return oversized
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
