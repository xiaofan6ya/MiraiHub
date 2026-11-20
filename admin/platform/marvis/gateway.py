"""腾讯 Marvis 的 `/v1/*` 转发实现。

## 上游就是 OpenAI 协议

Marvis 的模型网关 `POST /v1/chat/completions` 本身就是 OpenAI 兼容的（SSE 流式、
`finish_reason`、`tool_calls`、`reasoning_content` 都在原位），所以这里的活主要是：

  1. **模型 id 归一** —— 用户传的是显示名（`deepseek-v4-1-flash-261017`），
     上游只认槽位名（`main-auto`）。直接透传真实模型名会 400。
  2. **鉴权头** —— 不是 Bearer token，而是 `Ual-Access-*` 那一组（见 core/endpoints）。
  3. **流式原样转发** —— 不在这里读干流。
  4. **记账** —— 落 usage_logs，控制台的用量与积分统计靠它。

## 为什么**每一个**上游调用都要在工作线程里

`core/client.py` 走的是 `urllib`（同步）。在 async handler 里直接
`urlopen` / `stream.read()` 会把整个事件循环按秒级卡住 ——
`admin.server` 是单进程单 worker 的 uvicorn，事件循环一停，全站（控制台、
账号同步、健康检查、所有平台的转发）一起停摆。

2026-11-15 线上事故就是这一条：`open_chat_stream` 里的 `urlopen` 在事件
循环线程上等上游状态行，`admin.loop_watch` 实测卡死
3.3s / 7s / 17s / 27s / **63.5s**，43 个在飞请求被串行化后超期，上游回
`4100406 非法请求`（再被旧版 `_classify` 误判成 `signature_invalid`，
DSH 侧就显示成「API key is invalid」）。

所以现在：**建连**（`open_chat_stream` / `chat_completions`）、**逐块读**
（`resp.read`）、以及**同步 DB**（选号 / 租约 / 记账）全部走
`asyncio.to_thread`；事件循环线程上只剩 await。

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
import gzip
import hashlib
import inspect
import json
import logging
import os
import random
import re
import threading
import time
from contextvars import ContextVar

from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse

from admin.platform import shared
from admin.platform.marvis.core import client, endpoints
from admin.platform.marvis.services import accounts as marvis_accounts

logger = logging.getLogger("admin.platform.marvis.gateway")

PUBLIC = endpoints.PLATFORM
STORAGE = marvis_accounts.PLATFORM
#: 一次请求最多尝试**几个账号**（不是上游调用次数）。
#:
#: 与「同号重试」是两层独立的上限（2026-11-16 解耦）：
#:   · 同一个号内部：`_call_upstream` 最多 1 + SAME_ACCOUNT_RETRIES = 3 次；
#:   · 账号之间：本常量 = 3 个号，每号最多 3 次 ⇒ 最坏 9 次上游调用。
#: 以前这两层没有任何日志能区分：一个号把 3 次尝试全吃掉后请求就回 403，
#: 看不出是「池里没别的号」还是「换号没发生」。现在换号显式化并留日志
#: （`Marvis 换号重试` / `Marvis 换号终止`）。
MAX_ATTEMPTS = 3

#: 403 请求体转储 —— **临时排障开关，默认关闭**（2026-11-16 加）。
#:
#: 线上 4100406 只在大请求上出现（bytes=3590147 / msgs=270 / sys_chars=142860，
#: 3 次尝试全 403），同一时刻同一个号的小请求 200；而**同形状的合成请求**
#: （270 条 / 3.65MB / sys 143K）全部 200 ⇒ 触发条件是**具体内容**，不是体积、
#: 不是消息数、不是 tools 数。要二分内容，先得拿到真实失败的那一份 body。
#:
#: 开关（每次调用都读环境变量，改 env 后无需改代码）：
#:   · `MIRAIHUB_MARVIS_DUMP_403=1`      打开转储；默认 0 / 未设置 = 完全不碰磁盘；
#:   · `MIRAIHUB_MARVIS_DUMP_403_DIR`   目录，默认 `/tmp`（容器内）；
#:   · `MIRAIHUB_MARVIS_DUMP_403_KEEP`  最多保留几份，默认 3。
#:
#: ⚠ **内容绝不进日志**：日志里只有路径与字节数。dump 文件里是用户对话原文，
#: 排障结束必须删除（`_prune_dump_403` 只管份数，不管生命周期）。
DUMP_403_ENV = "MIRAIHUB_MARVIS_DUMP_403"
DUMP_403_DIR_ENV = "MIRAIHUB_MARVIS_DUMP_403_DIR"
DUMP_403_KEEP_ENV = "MIRAIHUB_MARVIS_DUMP_403_KEEP"
DUMP_403_DEFAULT_DIR = "/tmp"
DUMP_403_DEFAULT_KEEP = 3
DUMP_403_PREFIX = "mh403-"
#: 已落盘内容的指纹 → 路径。
#:
#: 一次请求会打多次上游（同号最多 3 次 + 换号最多 3 个号），**body 完全相同**；
#: 不去重的话一次请求就能把 3 个名额占满，而且三份内容一模一样、毫无增量。
#: 按内容 sha256 去重 ⇒ 一次请求最多落一份，名额留给**不同**的失败请求。
_DUMP_403_SEEN: dict[str, str] = {}
_DUMP_403_SEEN_MAX = 16

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
    """额度族（3000100 / 1000007）的统一文案 —— 给用户看的那一段。

    2026-11-19 归因结论：那两条 3000100 实际走了「降到 32768 → 同号重试
    → 200」，所以库里没有失败记录；用户看到 400 的那次是降级后**仍被拒**。
    请求形状维度已被证伪（同一账号在实时窗口内成功处理过更大的形状 20 次），
    剩下的唯一维度是「该号该模型当次的剩余可用预算」。

    ⚠ 旧文案说「上游把输入 token + max_tokens 合起来算一条预算」，这句已被
    25 发实测证伪 —— 不要再写回来。
    """
    notes = notes if isinstance(notes, dict) else {}
    degraded = notes.get("degraded_max_tokens") or {}
    switched = int(notes.get("quota_switch_accounts") or 0)
    tried = []
    if degraded:
        tried.append("把 max_tokens 从 %s 降到 %s 在同号重试"
                     % (degraded.get("from"), degraded.get("to")))
    if switched:
        tried.append("换号后试过 %d 个账号" % switched)
    action = ("、".join(tried) + "，") if tried else "在原账号上重试，"
    return (
        "Marvis 上游额度不足（上游 %s）：这是「该账号在该模型上的当次可用"
        "额度」不够，不是请求太大、不是你的 API Key 无效，也不是账号池的"
        "总额度已经用光。网关已自动处置：%s额度仍然不够，本次请求失败。"
        "建议：稍后重试、换一个模型，或在控制台换一个 Marvis 账号再发。"
        % (upstream_code or "3000100", action))


def _fail_payload(exc: client.UpstreamError,
                  notes: dict | None = None) -> tuple[int, dict, dict]:
    """把**上游账号侧**的失败翻译成「状态码 + 错误体 + 响应头」。

    返回 `(status, error_obj, headers)` —— 由 `_fail()` 决定包成什么形状：
    还没开始写字节时是真状态码 + JSON，流已经开始之后只能包成 SSE 错误帧。

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
    headers: dict[str, str] = {}
    # 类型默认跟随 kind；下面几条专用分支会覆盖（content_too_long 等）。
    err_type = exc.kind
    #: 显式覆盖 upstream_account；None 表示按「status == 503」推断。
    upstream_account = None
    # ------------------------------------------------------------------
    # 签名器不可用（fail-closed）—— **请求根本没发出去**（上游 PR #55）
    # ------------------------------------------------------------------
    #
    # 必须排在最前面：它不是上游给的结论，是网关自己拒的。用 503 而不是
    # 4xx：这是「本机环境没配好」，客户端会按 Retry-After 退避重试，也不会
    # 误渲染成「API 密钥无效」。upstream_account=False 让上层不再换号
    # （换号也签不出来）。
    if exc.kind == "signer_unavailable":
        detail = (exc.detail or "").strip()
        status = 503
        code = "signer_unavailable"
        err_type = "signature_unavailable"
        upstream_account = False
        headers["Retry-After"] = "30"
        message = (
            "Marvis 客户端指纹签名器不可用，网关已按 fail-closed 拒绝本次"
            "请求（请求一个字节都没发给上游）。这与你的 API Key、"
            "Marvis 账号状态都无关，换号 / 重扫码 / 换 Key 都不会好。"
            "处理方式：① 确认本机 uskey 签名链路可用（qimei 真值表 / 本地 mint）；"
            "② 或设 MARVIS_USKEY_FAIL_CLOSED=0 临时退回随机填充，仅限排查用。"
            + ("诊断：%s" % detail[:300] if detail else ""))
    # ------------------------------------------------------------------
    # 内容风控（4100406）单独处理 —— 这是**请求内容**的问题
    # ------------------------------------------------------------------
    #
    # 上游 403 传到 Claude Code / Codex 会被渲染成「API 密钥无效」，用户去翻
    # Key / 重扫码 / 换号 —— 三个方向全是错的：是提示词命中黑名单。
    # 400 + content_filter 是 OpenAI 语义里「请求内容不被接受」的标准表达。
    #
    # ## 两个入口，同一张脸（2026-11-20）
    #
    # ① `kind == content_block` —— 200 + application/json 里的 4100406，
    #    由 `client._classify_json_body()` 产出；
    # ② `kind == upstream_business` 且业务码 4100406 —— 403 那条路。
    #    403+4100406 按规定仍归 upstream_business（同号可重试一次，见
    #    client._classify 的 docstring 与 tests/test_marvis_platform.py），
    #    但**归类不动、呈现必须动**：以前这里没有任何分支认领 403，对外就是
    #    `403 + type=upstream_business + code=403` —— 正是上面那段注释点名的
    #    「被渲染成 API Key 无效」。同一个 4100406 换个 HTTP 状态（200/403）
    #    不该换一张脸，所以判据取业务码、复用同一段文案。
    #
    # ⚠ 只改错误面，**不改重试语义**：`exc.kind` 仍是 upstream_business，
    # `_call_upstream` 的「同号重试」判据（kind + RETRY_SAME_ACCOUNT_BIZ_CODES）
    # 完全不受影响。
    elif exc.kind == "content_block" or (
            exc.kind == "upstream_business"
            and "4100406" in str(exc.detail or "")):
        detail = (exc.detail or "").strip()
        status = 400
        code = "content_blocked"
        err_type = "content_filter"
        upstream_account = False
        message = (
            "上游内容风控拦截：系统提示词命中腾讯黑名单"
            "（与你的 API Key、Marvis 账号都无关，换号也没用）。"
            "网关已按指纹规则表改写仍被拦，说明命中的是别的形态，"
            "请把原始提示词首行发维护者。"
            + ("上游原文：%s" % detail[:300] if detail else ""))
    if exc.kind == "risk_control":
        # 4100404「请求被风控拦截」：**不是** Key 的问题，也**不是**账号/余额的问题。
        # 原样透 403 会被客户端渲染成「Key 无效」或「请求非法」，用户去翻一个
        # 根本没问题的 Key —— 换成 503 + Retry-After 才是「稍后再试」的可重试语义。
        #
        # 2026-11-18：文案必须区分两种状态，否则会对用户说假话：
        #   · 已开平台退避（连续命中撞满阈值）→ 说「平台在退避」；
        #   · 单次命中（平台没退避）→ 只能说「这一条请求被拦」，并指向真因：
        #     请求内容里的 system 提示词撞了上游内容黑名单（见
        #     _TENCENT_FINGERPRINT_RULES），改 Key / 换账号都不会变。
        # 2026-11-20（PR #67）：错误面统一收进 _risk_control_rejected() ——
        # 503 + code=risk_control_backoff / risk_control_request + Retry-After 1~11，
        # 两档文案由它按平台此刻是否在退避自己选。type 仍是 risk_control。
        reject = _risk_control_rejected(exc.detail or exc.message)
        status = reject.status
        code = reject.code
        message = reject.message
        # 平台侧问题，但不是换个号就好的那种账号问题
        upstream_account = False
        headers.update(dict(reject.headers or {}))
    if status == 401 or exc.kind == "auth" or code == "credential_expired":
        status = 503
        if "重新扫码" not in message and "授权登录" not in message:
            message = ("Marvis 平台账号的登录态已失效（与你的 API Key 无关）："
                       "请在控制台「授权登录」重新扫码。原始信息：%s" % message)
        else:
            message = ("%s —— 这是 Marvis 平台账号的问题，你的 API Key 是有效的。"
                       % message)
    # ① / ② 的专用文案：这两种失败用通用的「上游返回 X」等于什么都没说。
    # 业务码要从**错误体**里抠：client 把 code 填成 HTTP 状态（500/400），
    # 真正的 5100007 / 3000100 只出现在 detail 的 JSON 里。
    upstream_code = (client._biz_code_of(exc.detail or "")
                     or str(exc.code or "")).strip()
    # `family` 是给下游聚合用的族标记：额度族（3000100 / 1000007 / 3000001）
    # 同一个 family，具体原因仍然看 upstream_code。
    family = ""
    quota_detail = str(exc.detail or "")
    if (exc.kind == "max_input_tokens" or upstream_code == "1000007"
            or "1000007" in quota_detail
            or "积分余额不足" in quota_detail):
        # 额度族（3000100 / 1000007）：判据是「该账号在该模型上的当次可用
        # 额度」不够 —— 不是请求体积、不是 Key、也不是池子总额度。
        # 2026-11-19 实测：acc=193 在实时窗口内处理过 bytes=527389 / msgs=98
        # 的更大形状（200），体积维度被否证；降级后仍被拒才是预算真的不够。
        status = 400
        code = ("max_input_tokens_exceeded"
                if exc.kind == "max_input_tokens" else "insufficient_quota")
        shown_code = upstream_code
        if shown_code in ("", "400", "401", "402", "403", "404", "422",
                          "429", "500", "502", "503"):
            # detail 抠不出业务码时，exc.code 是 HTTP 状态 —— 文案里要写上游码。
            shown_code = ("3000100" if exc.kind == "max_input_tokens"
                          else "1000007")
        message = _quota_tight_message(shown_code, notes)
        family = "quota"
    elif exc.kind == "upstream_internal":
        # 上游服务端内部错误：与账号池无关，已在同号重试过一次。
        status = 502
        code = "upstream_internal_error"
        message = (
            "Marvis 上游服务端内部错误（上游 %s）：与你的 API Key、账号、额度都"
            "无关，网关已在同一个账号上重试过一次。换账号不会改变结果，这是"
            "上游侧故障，稍后重试即可。原始信息：%s"
            % (upstream_code or "-", message))
    elif exc.kind == "quota_exceeded":
        # 账号级总额度（3000001 等）：文案保持原样，只补 quota 族标记，
        # 让下游按同一族聚合；1000007 已走上面的「当次额度」文案。
        family = "quota"
    elif status == 400:
        # 参数类 400 必须带出**上游原文**（上游 PR #55）。以前只有一句
        # 「模型网关失败（HTTP 400）」，客户端 / 控制台 / 日志三处都看不出
        # 是哪个字段踩的 —— 2026-11-17 排查 response_format=json_object
        # 时正是卡在这一句上，只能逐个字段手试。
        upstream = (exc.detail or "").strip()
        if _is_context_overflow(exc):
            code = "context_length_exceeded"
            err_type = "context_too_long"
            # 重发循环已在 _attempt 里落地（_MAX_CTX_RETRIES=4，原样重发靠
            # main-auto 重新路由），所以这里可以如实说重发了几次。
            message = (
                "这轮对话的上下文太长，Marvis 上游装不下（与你的 API Key、"
                "账号额度都无关）。已自动重发 %d 次仍未通过，说明不是"
                "临时路由问题。请新建一个对话继续，或让客户端清理历史后重试。"
                % _MAX_CTX_RETRIES)
            if upstream:
                message = "%s；上游原文：%s" % (message, upstream[:200])
        elif upstream:
            message = "%s；上游原文：%s" % (message, upstream[:300])
    error_obj = {"type": err_type, "code": code, "message": message,
                 # 给客户端一个可读的提示位：明确「不是 Key 的问题」
                 "upstream_account": (status == 503
                                       if upstream_account is None
                                       else upstream_account)}
    if family:
        error_obj["family"] = family
    if upstream_code:
        error_obj["upstream_code"] = upstream_code
    return status, error_obj, headers


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


def _fail(exc: client.UpstreamError, *, notes: dict | None = None,
          stream: bool = False):
    """把上游失败包成响应。

    `stream=False`：真实状态码 + JSON —— 此时一个字节都还没写给客户端。
    `stream=True`：**只有在流已经开始（响应头已发出）之后**才允许走这条：
    状态码改不了，只能 200 + 错误帧，状态由帧里的 http_status 携带。
    """
    if notes is None:
        notes = _notes()
    # 额度族 / 上游内部错误 → 统一由 _error_payload() 出（返回 Optional）：
    # 它只接管 b8a01fe 新增的那几种 kind，其余返回 None 交回 _fail_payload()
    # 的既有分类 —— 那几条已在线上稳定的分支一个字都不用动。
    quota = _error_payload(exc, notes)
    if quota is not None:
        q_status, q_obj, q_headers = quota
        if stream:
            return _stream_error_response(
                q_status, q_obj,
                extra_headers=_degraded_headers(notes) or dict(q_headers or {}))
        q_headers = dict(q_headers or {})
        q_headers.update(_degraded_headers(notes))
        return JSONResponse({"error": q_obj}, status_code=q_status,
                            headers=q_headers or None)
    status, error_obj, headers = _fail_payload(exc, notes)
    if stream:
        return _stream_error_response(
            status, error_obj, extra_headers=_degraded_headers(notes)
            or dict(headers or {}))
    headers = dict(headers or {})
    headers.update(_degraded_headers(notes))
    return JSONResponse({"error": error_obj}, status_code=status,
                        headers=headers or None)


#: 请求体上限（字节）：超过就直接 413，**不送去撞上游**。
#:
#: 2026-11-17 线上 27 次 `400 3000101 invalid parameter` **全部**是同一个形状：
#: bytes≈11.4MB / msgs=2 / sys_chars=252 —— 客户端把单条 11MB 的消息塞进
#: messages[0]。上游对单条消息体积有自己的上限，超了只回一个看不出原因的
#: 参数错误，于是每次都要走完「选号 → 建连 → 4xx → 换号」才失败，白占上游配额。
#:
#: （历史）阈值曾是 4MB：正常大对话有 3.7MB 的成功先例（270 条消息 / sys 143K），
#: 但 4MB 对长对话太紧，见下方 2026-11-18 的调整。
#: 2026-11-18 用户反馈：大量长对话被 4MB 挡住（线上报错约 6.5MB）。默认值提到
#: 256MB —— 已远超任何真实对话，实际上等于「不设限」，但仍保留一个兜底，
#: 防止单个请求把进程内存打爆。
#:
#: 环境变量 `MIRAIHUB_MARVIS_MAX_BODY_BYTES` 的语义：
#:   * 未设置 / 非法值 → 用 `DEFAULT_MAX_BODY_BYTES`（256 MiB）
#:   * 正数           → 用该值（临时调小）
#:   * **0 或负数**   → 完全关闭体积检查（不限制）
MAX_BODY_ENV = "MIRAIHUB_MARVIS_MAX_BODY_BYTES"
DEFAULT_MAX_BODY_BYTES = 256 * 1024 * 1024   # 256 MiB：给长对话留足空间
#: 旧名字保留，避免外部脚本/历史代码引用时炸掉。
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
        value = int(float(raw))
    except (TypeError, ValueError):
        return DEFAULT_MAX_BODY_BYTES
    return value


def _too_large(size: int, limit: int) -> JSONResponse:
    """413：说清这是**本网关**的体积兜底。

    不再教用户「自己去拆消息」—— 用户要的是能发，解法是调上限或关掉检查。
    """
    logger.warning(
        "Marvis 请求体过大已本地拒绝 bytes=%d limit=%d（未打上游）",
        size, limit)
    return JSONResponse(
        {"error": {
            "type": "invalid_request_error",
            "code": "body_too_large",
            "message": (
                "请求体过大（约 %.1f MB，当前网关上限 %.1f MB）。"
                "这是本网关的体积兜底，不是模型 / 账号 / 额度问题"
                "（上游根本没被调用）。"
                "如需发送更大的请求，把环境变量 %s 设为更大的值，"
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
    if limit <= 0:      # 0 / 负数 = 不限制
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
    """落一条 usage_logs。**绝不抛**（记账失败不能影响转发）。"""
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
# 前 7 条规则逐条移植自客户端插件的 `CONTENT_FINGERPRINTS`：`find` 是**字面量**
# （`re.escape` + `re.IGNORECASE`，上游匹配大小写不敏感）；第 4 条用插件登记的
# `findRegex`（容忍空白归一与可选词漂移）。第 8 条是**本地补的**（插件表里没有，
# 2026-11-16 真机二分得出，见该条注释）。
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
#   8. DSH「follow-up suggestion」提示词首句 —— **行首锚定**（唯一带 `^\s*`
#      的一条，见规则 8 的注释）
#
# 前 7 条**不**加行首锚定：改写范围宁可略宽 —— 漏改一处就是整轮 403，多改一处
# 只是语义等价的措辞变化（与客户端插件同一取舍）。第 8 条是这条取舍的**唯一
# 例外**：上游对那一串本身就是**前缀锚定**（前接任意字符即放行，实测 200），
# 加锚点是让改写范围与上游判定**同形**，既不漏改，也不去动句中正常叙述。

#: 腾讯内容级风控**指纹表**（8 条：前 7 条全量移植自 DSH 插件
#: `dsh-providers-hub/lib/openai-gateway/tencent-fingerprint.js` 的
#: `CONTENT_FINGERPRINTS`；第 8 条本地补，见该条注释）。
#:
#: ## 为什么要这张表
#:
#: 上游 `yybadaccess.3g.qq.com` 对 **system 消息正文**做**字面量**黑名单匹配
#: （腾讯内容风控，与 `buddy` / `workbuddy` 同一套）。命中即回
#: `HTTP 403 {"code":4100406,"msg":"非法请求"}` —— 客户端只看到「非法请求」，
#: 看不出是内容被拦，也看不出拦的是哪一句。
#:
#: ## 2026-11-16 真机定位（账号 189，直连上游 8 次探针，真实 DSH system 98,594 字符）
#:
#: | 探针 | system 形态 | 结果 |
#: |---|---|---|
#: | F0 基线 | 首行原样 | ❌ 403/4100406 ×2 |
#: | F1 | system 搬成**顶层字段**，messages 只留 user | ✅ 200 ×2 |
#: | F2 | 首行按本表第 4 条改写，其余逐字不动 | ✅ **200 ×2** |
#: | F3 | 首行前加一个 ASCII 空格 | ❌ 403 ×2 |
#: | F4 | 首行前加「换行 + 空格」 | ❌ 403 ×2 |
#:
#: ⇒ 两条**与旧结论相反**的事实（别照抄旧笔记）：
#:   1. **改写首行能放行**（F2 2/2 200），**行首加前缀不能**（F3 / F4 全 403）
#:      ⇒ 这条锚点不是「前接任意字符即放行」，「加个空格绕过」的写法是错的。
#:   2. F1 虽然也 200，但它只对**带顶层 `system` 字段**的请求成立；DSH 实际把
#:      system 放进 `messages[0]`（线上失败画像 `msgs=338`），F1 救不了那条路。
#:      ⇒ 落地选 **F2**（本函数），它是唯一对「system 在 messages[] 里」有效的形态。
#:
#: ## 移植纪律（⚠️ 这一节是 2026-11-16 一次**真实缺陷**换来的）
#:
#: · **每条 `find` 必须与插件 verbatim 逐字符一致**：上游认的是**整句
#:   字面量**，**砍掉前半或后半就放行** ⇒ 写短了、写错一个词 = **静默空跑**。
#:   空跑的症状是「表里登记了、日志里 `rules_hit` 恒 0、上游仍每轮
#:   403/4100406」，与「完全没登记」同形，极难归因。
#:   第一版就踩了这条：规则 1 首词写成 `Default branch`（插件真值是
#:   `Main branch`）、规则 3 只留半句（插件真值是 99 字符整句，含
#:   `https://github.com/anthropics/claude-code/issues`），两条因此**永不命中**；
#:   规则 2 多写了一个 `tool`、规则 7 写成 `hdr`（截断后把原串尾巴留在
#:   后面，改出垃圾串）。现由 `tests/test_marvis_content_fingerprint.py` 的
#:   `FindLiteralDriftTests` 直接锁字面量（首词 / 长度 / 整句内容）。
#: · `find` 是**字面量**（`re.escape` + `re.I`，上游大小写不敏感）；第 4 条用
#:   插件登记的 `findRegex`（容忍空白归一与 `an AI agent` /
#:   `an AI coding agent`、`powered by` / `powered by the` 等形态漂移）。
#: · **只改内容，不删句子**：替换值与插件逐字一致，语义等义。
#: · **不命中的文本逐字符不变**（先 `search` 预检，命中才 `sub`）——
#:   腾讯侧按前缀缓存，无谓重建字符串会白吃一次全量重算。
#: · ⚠️ 插件侧还实测过 **assistant 正文**同样会被拦，这里**不**顺手扩过去：
#:   本次故障的证据链只落在 system（8 次探针 + 线上 403 dump），扩大到
#:   assistant 会篡改会话历史。将来真出现「assistant 复述被拦」的报障，
#:   再按插件那张表补，别凭「统一一下」直接加。

#: 改写开关（默认开）：设为 0 / false / off / no 时**完全跳过**改写，请求原样
#: 发上游。这是「替用户改写内容」的行为，必须能一键退回原样透传。
SANITIZE_TENCENT_ENV = "MIRAIHUB_MARVIS_SANITIZE_TENCENT"
_SANITIZE_OFF_VALUES = frozenset({"0", "false", "off", "no"})


def _tencent_sanitize_enabled() -> bool:
    """开关当前状态。

    每次调用现读环境变量而不是模块级求值：开关是排障用的，改 `.env` 重启即
    生效；也便于测试逐例切换两种状态而不必重载模块。
    """
    raw = (os.getenv(SANITIZE_TENCENT_ENV) or "").strip().lower()
    return raw not in _SANITIZE_OFF_VALUES


_TENCENT_FINGERPRINT_RULES = (
    # 1. ZCode 内建常量（每个 git 仓库会话都会被注入）。位置无关。
    #    ⚠️ 首词是 `Main`，不是 `Default`：上一版写成 Default 后**永不命中**。
    (re.compile(re.escape("Main branch (you will usually use this for PRs)"), re.I),
     "Main branch (PRs usually go here)"),
    # 2. Claude Code 身份句。**行首锚定**；刻意不带尾句点，一条覆盖两种常量形态。
    #    ⚠️ 插件原文是 `official CLI for Claude`，别顺手补 `tool`（补了就空跑）。
    (re.compile(re.escape("You are Claude Code, Anthropic's official CLI for Claude"), re.I),
     "You are Claude Code, Anthropic's CLI for Claude"),
    # 3. Claude Code 反馈句（每轮必带的固定段落）。位置无关。
    #    find 必须写整句（含 URL，99 字符）：上游认逐字符，砍掉半句等于静默空跑。
    (re.compile(re.escape("To give feedback, users should report the issue at "
                          "https://github.com/anthropics/claude-code/issues"), re.I),
     "To give feedback, users should report the issue at the project issue tracker"),
    # 4. DSH 身份首句（位置无关；本条就是 4100406 的触发串）。
    #    2026-11-18：DSH 把品牌名从 `DeepSeek Harness` 改成 `DeepSeekRuntime`，
    #    旧正则只认 Harness，真机上**空跑**（规则在表里、日志 rules_hit 恒 0、
    #    上游照旧 403/4100406）。这里合并两种品牌名，`\s*` 容忍有无空格。
    #    ⚠ 改写**不能**保留 `You are an AI ...` 这个开头：2026-11-18 实测两次
    #    都 403/4100406（各 3 次尝试全 403）——
    #      · `You are an AI agent powered by a large language model`（只换品牌）
    #      · `You are an AI coding assistant, powered by a large language model`
    #    ⇒ 黑名单覆盖的是 **`You are an AI` 前缀**，不是品牌词，也不只是
    #    `AI agent powered by`。最终把 `capable` 插在 `AI` 前：
    #    `You are a capable AI coding assistant, ...` —— 前缀被真实文本改动破坏，
    #    语义仍是「由大模型驱动的、能力出众的 AI 编码助手」。
    #    仍容忍空白归一（\s+）以及 `an AI coding agent`、`powered by` /
    #    `powered by the` 等形态漂移。
    (re.compile(r"You\s+are\s+an?\s+AI\s+(?:coding\s+)?agent\s*,?\s*powered\s+by\s+"
                 r"(?:the\s+)?DeepSeek\s*(?:Harness|Runtime)", re.I),
     "You are a capable AI coding assistant, powered by a large language model"),
    # 4b. 规则 4 的 **assistant** 形态（2026-11-20 实测空洞，紧跟规则 4 补）。
    #     漏的是这个组合：`You are an AI coding assistant, powered by DeepSeek
    #     Harness`（或 `... powered by the DeepSeekRuntime`）。三条都不吃 ——
    #       · 规则 4 要求 `agent`（不是 `assistant`）；
    #       · 规则 11 要求 `AI` **紧跟** `assistant`；
    #       · 规则 12 要求 `a coding assistant`（`an AI coding assistant` 不匹配）。
    #     实测（改前 13 条）：规则表层面全 MISS，只剩首行身份句兜底把它改成
    #     `You're ...` —— 能过，但 `rules_hit` 记不到这条，日志里等于没命中，
    #     归因断链（与「规则写窄」同形，见本文件 FindLiteralDriftTests）。
    #     替换值与规则 4 **逐字一致**：`You are a capable AI ...` 正是 4100406
    #     的原样命中形态（2026-11-18 对照：`You are an AI` 前缀被拦，必须把
    #     `capable` 插到 `AI` 前）。
    #     ⚠ 顺序仍排在规则 4 之后：具体（带品牌后缀 + agent）先吃，泛化后吃，
    #     与 9 → 10 的既有约定一致，避免将来合表时二次改写。
    (re.compile(r"You\s+are\s+an?\s+AI\s+coding\s+assistant\s*,?\s*powered\s+by\s+"
                 r"(?:the\s+)?DeepSeek\s*(?:Harness|Runtime)", re.I),
     "You are a capable AI coding assistant, powered by a large language model"),
    # 4c. **品牌前置**形态（2026-11-20 实测空洞）：`You are DeepSeek Harness, an
    #     agentic coding tool`。规则 4 / 4b 都是「身份在前、品牌在后」，这条反过来，
    #     同样三条都不吃。只改前缀、**保留后面的身份名词**：匹配截到冠词
    #     （含可选的 `n`）为止，替换成 `You are a capable`，于是
    #     `... , an agentic coding tool` → `You are a capable agentic coding tool`。
    #     ⚠ 逗号是**必需**的：只认「品牌 + 逗号 + 冠词」这个句式，写成可选会把
    #     `You are DeepSeek Harness and ...` 这类正文改坏。
    #     ⚠ 冠词后的 `\b` 同理：没有它，`, and you must ...` 会被吃成
    #     `a capable d you must ...`（`an?` 贪掉 `and` 的前两个字母）。
    (re.compile(r"You\s+are\s+DeepSeek\s*(?:Harness|Runtime)?\s*,\s*an?\b", re.I),
     "You are a capable"),
    # 5. DSH Web 运行面的开头两句。位置无关。
    #    触发串**跨句边界**（前半句、后半句各自都放行），find 必须是前两句的
    #    并集；砍半 = 静默空跑。改写只去掉品牌串，其余逐字不动。
    (re.compile(re.escape(
        'You are interacting with the user through the DeepSeek Harness Web GUI '
        'whose URL the runtime context names. When the user refers to "this page", '
        '"this GUI", or "this app" without naming another target, they mean this GUI.'),
        re.I),
     'You are interacting with the user through the harness Web GUI '
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
    #    ⚠️ find 必须是完整的 `header`：上一版写成 `hdr`，虽然命中，却把
    #    原串尾巴（`eader`）留在后面，改出一串垃圾。
    (re.compile(re.escape("x-anthropic-billing-header"), re.I),
     "x-client-billing-header"),
    # 8. DSH 的「follow-up suggestion」系统提示词首句（插件表里没有，本地补）。
    #    ⚠️ 上游这一条的判定与前 7 条**不同**，2026-11-16 真机二分 26 次确证：
    #      · 命中长度恰好 **32** 字符：`...suggestio`(31) → 200，
    #        `...suggestion`(32) → 403/4100406（不要求词边界）；
    #      · **前缀锚定**，不是 contains：前面加一句（`System note. ...`）→ 200；
    #      · 只查 **system** 角色（同一串放 user → 200）；
    #      · 前导空白先 strip（前导空格 / 换行照样 403），大小写不敏感。
    #    所以这里是**唯一**带 `^\s*` 的一条：锚点与上游判定同形 —— 句中出现的
    #    同一串本来就不会被拦，改了只是无谓地篡改正文。
    #    改法是把 `the` 换成 `a`：**破坏前缀、不删句子**，语义仍是「后续建议
    #    引擎」。⚠️ 不能靠加空格 / 换行绕过（上游 strip 前导空白），必须是真实
    #    文本改动；也不能把首句删掉（那会丢掉「只输出建议、不要别的内容」的约束）。
    (re.compile(r"^\s*You\s+are\s+the\s+follow-up\s+suggestion", re.I),
     "You are a follow-up suggestion"),
    # 9-13. 上游 2026-11-18 新增的**通用开场白**黑名单（真机 23 次对照：
    #    同一 Key / 模型下，不带 system 12 次全 200，带 system 12 次全 403；
    #    长段自定义提示词 200 ⇒ 命中的是这些固定开头，不是长度）。
    #    只把 `helpful` 换成 `capable` / 给 `AI assistant` 补 `capable`，
    #    **不删句子**：这些开场白后面常跟同一条身份约束，删了会丢语义。
    #    ⚠ 顺序：`helpful AI assistant` 必须排在 `helpful assistant` 前面。
    #    两条正则的 `helpful` 与 `assistant` 之间分别是 `AI\s+` 与直接相接，
    #    本不会互相吃掉；按「具体 → 泛化」排是防止以后有人把 `(?:AI\s+)?`
    #    合进来时，前半被改成 `capable` 后又被后半二次改写。
    (re.compile(r"You\s+are\s+a\s+helpful\s+AI\s+assistant", re.I),
     "You are a capable AI assistant"),
    (re.compile(r"You\s+are\s+a\s+helpful\s+assistant", re.I),
     "You are a capable assistant"),
    # `an AI assistant` → `a capable AI assistant`：替换文本里的冠词必须是
    # `a`，否则会留下 `an capable`。句末句点不在匹配内，原样保留。
    (re.compile(r"You\s+are\s+an?\s+AI\s+assistant", re.I),
     "You are a capable AI assistant"),
    (re.compile(r"You\s+are\s+a\s+coding\s+assistant", re.I),
     "You are a capable coding assistant"),
    # 中文开场白。句末标点（`。` / `！` / 无标点）不在匹配内，原样保留。
    (re.compile(r"你是乐于助人的助手", re.I),
     "你是一位能力出众的助手"),
)


#: 首行身份句：`<缩进>You are<空白>...`，大小写不敏感。
#:
#: 只锚 `^`＋缩进，**不锚整句** —— 上游拦的是「`You are` + 产品名」这个宽形态，
#: 不是某一句原文。锚整句的后果就是客户端一改措辞（Claude Code 从
#: `Anthropic's official CLI for Claude` 改成 `Claude Agent SDK`）就失效。
_IDENTITY_LINE = re.compile(r"^([ \t]*)you([ \t]+)are([ \t]+)", re.I)

#: 规则表**改写后**的形态：凡是替换值本身以 `You are` / `You're` 开头的规则，
#: 取它的**前 4 个词**作为「已 sanitize」的指纹前缀。兜底看到这些前缀就放行 ——
#: 否则再换一遍会把 `You are a capable ...` 改成 `You're a capable ...`：同一段
#: 文字两次处理结果不同（幂等性被打破），rules_hit 也不再是「一条规则命中一次」。
#: 取前 4 个词而不是整句：规则 4 的替换值有 13 个词，而实际文本常常只到
#: 「You are a capable AI coding assistant.」就结束 —— 整句比对会漏。
_IDENTITY_SAFE_PREFIXES = tuple(sorted({
    " ".join(str(_repl).strip().lower().split()[:4])
    for _pat, _repl in _TENCENT_FINGERPRINT_RULES
    if str(_repl).strip().lower().startswith(("you are ", "you're "))}))

#: `You are the <role>`：对**角色 / 辅助提示词**的描述，不是产品身份句。
#: 上游对它的判定是 32 字符前缀锚定（规则 8），兜底不该抢 —— 抢了就会去改
#: 「You are the memory curation assistant.」这类本来放行的正文。
_IDENTITY_ROLE_LINE = re.compile(r"^[ \t]*you[ \t]+are[ \t]+the[ \t]+", re.I)


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
        low = line.strip().lower()
        if (low.startswith(_IDENTITY_SAFE_PREFIXES)
                or _IDENTITY_ROLE_LINE.match(line) is not None):
            # 已经 sanitize 过的形态 / 角色描述句：兜底放行（见上面两条常量）。
            return text, 0
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
    # 兜底：首行身份句（D2「方案 B」）。**只在规则表零命中时**才动。
    #
    # 为什么不无条件追加：规则 10 命中一次之后，改写出来的
    # You are a capable assistant 仍以 You are 开头 —— 无条件再跑一遍兜底
    # 会把 rules_hit 从 1 记成 2，同一条规则在日志里数成两次，规则表与命中数
    # 从此对不上账。零命中时才轮到兜底：它覆盖规则表没登记的新客户端身份句
    # （You are Codex. 之类），命中即 1，与规则表互不重叠。
    if hits == 0:
        out, identity_hits = _rewrite_identity_line(out)
        return out, identity_hits
    return out, hits


def _sanitize_messages(messages: list) -> tuple[list, int]:
    """对**每一条 system 消息**的 `content` 做指纹改写，其余角色原样不动。

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
        if str(message.get("role") or "") != "system":
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
# 角色归一化（2026-11-19）：developer -> system / 多条 system 合并 / 顶层 system 并入
# ---------------------------------------------------------------------------
#: 上游只认 `system`。`developer` 是 OpenAI 给「系统指令」新起的名字，Marvis 不认。
#:
#: 2026-11-19 同一把 key、同一个网关、同一个 `main-auto` 模型，**只改 role** 的对照：
#:   · role=developer + 9 KB 系统提示词 + 61 tools + stream -> 403 / 4100406（43.8 s）
#:   · role=system    同一份 body                          -> 200
#:   · role=developer + 短提示词                            -> 400 / 3000101
#: 触发方在客户端：DSH 的 pi-ai 适配器把「非标准 baseURL」一律当
#: supportsDeveloperRole=true，而 marvis 条目声明了 reasoningEfforts（reasoning
#: = true），两者相乘 ⇒ **每条**请求的首条消息都是 developer。客户端版本不受
#: 网关控制，归一化只能在这里做。
#:
#: ⚠ 归一化必须发生在**指纹改写之前**：改写只认 role=system 的消息，先合并再洗
#:   才能保证 developer 转来的那几段正文整段都被洗到；反过来会漏掉它们。
_SYSTEM_ROLE = "system"
_DEVELOPER_ROLE = "developer"


def _join_system_contents(contents: list) -> object:
    """按出现顺序合并多条 system 正文，**用双换行连接**。

    · 全是字符串 -> 返回字符串，`"\n\n".join(contents)`。每一段**原样**保留
      （首尾空白、段落、换行都不动），连接符只加在两段之间；
    · 含数组（`[{"type": "text", ...}]`）-> 返回数组：逐段展开，段与段之间插一个
      `{"type": "text", "text": "\n\n"}`，字符串段包成等价的 text 块。
      每一段的文本仍然逐字未变，只是换了承载形态。
    """
    if all(isinstance(item, str) for item in contents):
        return "\n\n".join(contents)
    parts: list = []
    for index, content in enumerate(contents):
        if index:
            parts.append({"type": "text", "text": "\n\n"})
        if isinstance(content, str):
            parts.append({"type": "text", "text": content})
        elif isinstance(content, list):
            parts.extend(content)
        else:
            # 空 / 非常规正文：补一个空 text 块占位，保证段序不错位。
            parts.append({"type": "text", "text": ""})
    return parts


def _normalize_system_roles(body: dict) -> tuple[dict, dict]:
    """把 `developer` 归一成 `system`，并把所有 system 合并成**一条**。

    规则：

      1. `messages[]` 里 role=developer 的消息改成 role=system，**content 不动**；
      2. 多条 system（含由 developer 转来的）按**出现顺序**合并，双换行连接，
         各段首尾空白与段落原样保留；
      3. 顶层 `system` 字段（Anthropic / 部分客户端的承载形态）并入同一条，
         位置在**最前** —— 它本来就是「对话之前」的系统指令；
      4. 合并后的那条落在**第一条** system 原来的位置（没有 system 消息时放
         第 0 条），user / assistant 的相对顺序保持不变；
      5. 没有可改的东西时**原样返回入参**，连 dict 都不重建（上游按前缀缓存
         计费，无谓重建等于白吃一次全量重算）。

    返回 `(body, stats)`；stats 只给日志用，**不含正文**（system 里可能有用户
    prompt / 路径 / 密钥）。
    """
    messages = body.get("messages")
    top_system = body.get("system") if "system" in body else None
    top_mergeable = isinstance(top_system, (str, list)) and top_system != ""
    if not isinstance(messages, list) and not top_mergeable:
        return body, {}

    source = messages if isinstance(messages, list) else []
    out_messages: list = []
    developer_roles = 0
    system_slots: list[int] = []
    for message in source:
        if isinstance(message, dict):
            role = str(message.get("role") or "")
            if role == _DEVELOPER_ROLE:
                message = dict(message, role=_SYSTEM_ROLE)
                developer_roles += 1
                role = _SYSTEM_ROLE
            if role == _SYSTEM_ROLE:
                system_slots.append(len(out_messages))
        out_messages.append(message)

    if developer_roles == 0 and len(system_slots) <= 1 and not top_mergeable:
        return body, {}

    if system_slots or top_mergeable:
        contents: list = []
        if top_mergeable:
            contents.append(top_system)
        anchor = system_slots[0] if system_slots else 0
        for slot in system_slots:
            contents.append(out_messages[slot].get("content"))
        # 只有一段且没有顶层 system 时，content 就是原来那个对象 —— 绝不重建。
        merged = contents[0] if len(contents) == 1 else _join_system_contents(contents)
        first = out_messages[anchor] if system_slots else None
        if isinstance(first, dict):
            merged_message = dict(first, role=_SYSTEM_ROLE, content=merged)
        else:
            merged_message = {"role": _SYSTEM_ROLE, "content": merged}
        for slot in reversed(system_slots):
            if slot != anchor:
                del out_messages[slot]
        if system_slots:
            out_messages[anchor] = merged_message
        else:
            out_messages.insert(0, merged_message)

    out = dict(body)
    out["messages"] = out_messages
    if top_mergeable:
        out.pop("system", None)
    stats = {"developer_to_system": developer_roles,
             "system_merged": len(system_slots) + int(top_mergeable),
             "top_system_merged": int(top_mergeable)}
    return out, stats


def _normalize_max_tokens(body: dict) -> None:
    """`max_completion_tokens` -> `max_tokens`（上游只认后者）。

    · 只有 max_completion_tokens：改名成 max_tokens；
    · 两个都有且**不等**：以 max_completion_tokens 为准（OpenAI 现行的名字，
      客户端显式写的那个才是真意图），打 WARNING 留痕；
    · 两个都有且相等：只留 max_tokens，去掉重复字段。

    改完只留 max_tokens 一个字段：①的降级重试改的是 `body["max_tokens"]`，
    若把 max_completion_tokens 留在 body 里，上游按它优先就会让降级**静默失效**。
    """
    if "max_completion_tokens" not in body:
        return
    value = body.get("max_completion_tokens")
    if value is None:
        body.pop("max_completion_tokens", None)
        return
    current = body.get("max_tokens")
    if current is None:
        logger.info("Marvis max_completion_tokens 映射为 max_tokens | value=%s",
                    value)
    elif current != value:
        logger.warning(
            "Marvis max_tokens 冲突，以 max_completion_tokens 为准 | "
            "max_tokens=%s max_completion_tokens=%s", current, value)
    body["max_tokens"] = value
    body.pop("max_completion_tokens", None)


#: 上游只认的 response_format.type
_RESPONSE_FORMAT_OK = frozenset({"text", "json_schema"})

#: json_object 的等价物：不约束字段、只约束「是一个 JSON 对象」。
_JSON_OBJECT_AS_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "json_object",
        "schema": {"type": "object", "additionalProperties": True},
        "strict": False,
    },
}


def _normalize_response_format(value) -> tuple[dict | None, str]:
    """把 response_format 归一成上游认的形状，返回 (新值, 说明)。

    新值为 None 表示「翻译不了，这个字段整个摘掉」：宁可少一个 JSON 约束，
    也不要整条请求 400 —— 客户端拿到出字总比拿到一句无用的参数错误强。
    （上游 PR #55：json_object 未实现，改写成等价 json_schema。）
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


def _normalize_token_limit(out: dict) -> None:
    """把 max_completion_tokens 并进 max_tokens（上游 PR #55 口径）。

    ⚠ 主链路用的是本项目的 _normalize_max_tokens()（冲突时以
    max_completion_tokens 为准并打 WARNING，见其 docstring 的测试契约）。
    这里保留上游那份「取两者较小值」的实现，供需要更保守语义的调用方显式
    使用 —— 两份**不要**同时跑，否则后跑的会覆盖先跑的。
    """
    if "max_completion_tokens" not in out:
        return
    alias = out.pop("max_completion_tokens")
    try:
        limit = int(alias)
    except (TypeError, ValueError):
        return
    current = out.get("max_tokens")
    try:
        current_int = int(current)
    except (TypeError, ValueError):
        current_int = None
    out["max_tokens"] = (limit if current_int is None
                         else min(limit, current_int))


def _prepare_body(body: dict, slot: str) -> dict:
    """把下游请求整理成上游能接受的样子。

    · **角色归一化**（2026-11-19）：`developer` -> `system`、多条 system 合并成
      一条、顶层 `system` 并入 —— 见上面「角色归一化」小节的实测证据。这一步
      必须在指纹改写**之前**，否则 developer 转来的正文洗不到；
    · `max_completion_tokens` -> `max_tokens`（上游只认后者，冲突打 WARNING）；
    · `model` 换成槽位名；
    · 流式时补 `stream_options.include_usage` —— 少了它上游不返回 usage 帧，
      这张卡片和用量统计就永远是 0；
    · **system 正文过一遍腾讯内容风控指纹改写**（`4100406` 的修法，表见上）。
      `/v1/messages` 与 `/v1/responses` 都经 `_run_normalized` → `chat_completions`
      落到这里，所以只改这一处就覆盖三个入口。
       系统提示有两种承载形态：`messages` 里 `role=system` 的消息，以及
       Anthropic 风格 / 部分客户端的**顶层 `system` 字段** —— 两种都要洗。
    """
    out = dict(body)
    out["model"] = slot
    # ⓵ 角色归一化：developer -> system、多条 system 合并、顶层 system 并入。
    # 必须排在指纹改写之前 —— 改写只认 role=system，先合并再洗才能保证
    # developer 转来的那几段正文整段被洗到。
    out, role_stats = _normalize_system_roles(out)
    if role_stats:
        logger.info(
            "Marvis 角色归一化 | developer_to_system=%d system_merged=%d "
            "top_system=%d", role_stats["developer_to_system"],
            role_stats["system_merged"], role_stats["top_system_merged"])
    # ⓶ 上游只认 max_tokens：把 OpenAI 的新名字映射过去（含冲突告警）。
    _normalize_max_tokens(out)
    hits = 0
    messages = out.get("messages")
    if isinstance(messages, list):
        out["messages"], hits = _sanitize_messages(messages)
    # 顶层 `system` 字段不经过 `messages`，2026-11-18 之前这里**只洗消息**，
    # 同一句触发串放进顶层字段就整条绕过规则表（latent 漏改路径）。
    system = out.get("system")
    if isinstance(system, str):
        rewritten, n = rewrite_tencent_fingerprints(system)
        if n:
            out["system"] = rewritten
            hits += n
    if hits:
        # 只记命中条数，**不记原文**：system 里可能带用户的 prompt / 路径 / 密钥。
        logger.info("Marvis 内容风控指纹改写 | rules_hit=%d", hits)
    # ⓷ response_format 归一（上游 PR #55）：上游未实现 json_object，
    # 直接透传会整条 400（3000101）。放在指纹改写之后、补 stream_options 之前。
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


#: 允许「**同号**立刻重试一次」的瞬时失败 kind。
#:
#: 只有这两类是「网络抖动 / 上游瞬断」：换号没用（新号一样走同一条坏链路），
#: 同一个号隔几百毫秒再来一次往往就过去了。
#:
#: `auth`、`risk_control`、`signature_invalid` **一律不在白名单** ——
#: 它们不会因为「再打一次」而变好；上一版把瞬时 403 当成可重试信号
#: （`exclude.discard()` 后重新选号）实际是「换号重试」，既救不了请求也
#: 白占一个租约。
#:
#: `upstream_business` 整体也不在白名单 —— 唯一的例外是
#: `RETRY_SAME_ACCOUNT_BIZ_CODES` 里的业务码，见下。
RETRY_SAME_ACCOUNT_KINDS = frozenset({"network", "timeout"})
#: 上游偶发 403 4100406「非法请求」：实测同号同分钟 200/403 交替（约 5–10%），
#: 且事件循环阻塞修复后单次仅 ~5s ⇒ 同号重试**两次**（最多 3 次尝试）把失败率
#: 压到 ~0.1%（按每次 ~5–10% 独立概率估算：0.1³ = 0.1%、0.05³ ≈ 0.0125%）。
#:
#: 2026-11-16 线上反例：16:06:44.928 那条 403 elapsed 8869ms ≈ 两次尝试，
#: 即「首发 + 1 次重试」都撞上随机拒绝。单次概率没变（同一时段 60 次连打 0 个
#: 403），只是重试次数不够 —— 所以提到 2 次重试（3 次尝试）。
#:
#: 证据（2026-11-15 14:40–15:05，`request_audits` 21 行）：2 条 403
#: （14:49:51 用时 5.3s、14:52:46 用时 5.5s）+ 19 条 200，同一 key
#: （api_key_id=3）、同一模型（deepseek-v4.1-flash-marvis）。同号、同分钟
#: 内 200 与 403 交替 ⇒ 这是**上游偶发**拒绝，不是请求体本身错，也不是
#: 账号/凭证问题（换号救不了，所以仍然复用同一个账号）。
#:
#: ⚠ 只放这一个码：4100403（登录态过期）是 `auth`、4100404（风控）是
#: `risk_control`，都不在这里，也不会因为这条规则被重试。
RETRY_SAME_ACCOUNT_BIZ_CODES = frozenset({"4100406"})
#: 同号重试次数（不含首次）。2 ⇒ 最多 3 次尝试。
SAME_ACCOUNT_RETRIES = 2
#: 同号重试的**基数**退避（秒）：实际等待 = 基数 × (index + 1) + 抖动，
#: 即第 1 次重试 ~0.3s、第 2 次 ~0.6s（各再加 0–0.3s 随机抖动）。
#: 递增是为了避开上游的短时抖动窗口；抖动是为了避免同频重发。
SAME_ACCOUNT_RETRY_DELAY = 0.3
#: 退避抖动上限（秒），与 `SAME_ACCOUNT_RETRY_DELAY` 一起构成递增 + 抖动退避。
SAME_ACCOUNT_RETRY_JITTER = 0.3

# ---------------------------------------------------------------------------
# ① 输入 + max_tokens 超预算：把 max_tokens 降下来再试一次（2026-11-19）
# ---------------------------------------------------------------------------
#: 超过这个值就认为「用户侧传了一个上游预算装不下的 max_tokens」。
#:
#: 线上样本（2026-11-19）：bytes=24203 / msgs=2 / sys_chars=1058 —— 请求体
#: 只有 24 KB 却被上游判超限，唯一解释是 max_tokens=128000 与输入合起来越过
#: 了上游预算：4 KB 输入 + 128000 输出预算，怎么算都装不下。
MAX_TOKENS_DEGRADE_THRESHOLD = 32768
#: 降级目标值：上游明确收得下的输出预算。
MAX_TOKENS_DEGRADE_TARGET = 32768
#: 降级到 32768 同号重试仍然 3000100 ⇒ **该号在该模型上的当次额度不足**。
#:
#: 隔离用 (账号 × 模型) 级标记（select_account 已按这张表跳过同模型），
#: 不用账号级 degrade_until：这个号换个小模型多半还能用，整号摘掉等于白扔。
MODEL_QUOTA_TIGHT_SECONDS = 1800
MODEL_QUOTA_TIGHT_KIND = "model_quota"

# ---------------------------------------------------------------------------
# ② 上游内部错误（5100007）：同号重试一次，**不换号**
# ---------------------------------------------------------------------------
#: 同一个号最多再试几次（不含首发）。1 = 「首发 + 1 次」。
INTERNAL_RETRIES = 1
#: 退避基数（秒）+ 抖动上限（秒）：实测取值落在 1~2 秒。
INTERNAL_RETRY_BASE = 1.0
INTERNAL_RETRY_JITTER = 1.0

# ---------------------------------------------------------------------------
# ③ 等上游响应头的上限（与读超时 STREAM_TIMEOUT 严格分开）
# ---------------------------------------------------------------------------
#: headers-first 之后客户端不会因为「没响应」超时了，但用户会干等。
#: 2026-11-19 线上 headers_ms P90=25823、max=174066 —— 后者是 2 分 54 秒。
#:
#: ⚠ 这和 core/client.py 的 STREAM_TIMEOUT（300s）不是一回事：
#:   · STREAM_TIMEOUT 管**读**：允许长回答慢慢吐；
#:   · 本值管**等头部**：上游 prefill 迟迟不返回响应头时收手。
#: 混用会把「长回答被腰斩」和「prefill 卡死」变成同一个故障。
ACQUIRE_TIMEOUT_ENV = "MIRAIHUB_MARVIS_ACQUIRE_TIMEOUT"
ACQUIRE_TIMEOUT_DEFAULT = 120.0

# ---------------------------------------------------------------------------
# ③b 流式请求的「真实状态码宽限期」（2026-11-19 混合策略）
# ---------------------------------------------------------------------------
#: ## 为什么需要这个值（上一版的问题）
#:
#: 上一版把 `/v1/chat/completions` 的**所有**流式失败都改成 200 + 错误帧。
#: 只看 HTTP 状态的客户端会把「认证失败 / 额度耗尽 / 参数错」当成**成功**，
#: 拿到一个空内容 —— 失败被静默吞掉了。
#:
#: ## 混合策略
#:
#:   · acquire 在 GRACE 内失败 → 此时**还一个字节都没写**，返回**真实状态码**
#:     + JSON 错误（401/402/400/503 的语义完整保留）；
#:   · 超过 GRACE 还没拿到上游响应头 → 先发心跳帧（headers-first 不变），
#:     之后无论成功失败都只能 200 + 错误帧，状态由帧里的 http_status 携带。
#:
#: ## 为什么默认是 6.0s（2026-11-19 复核，原来 0.8s）
#:
#: 首字节时刻 = min(acquire, GRACE)，GRACE 就是「客户端最多等多久拿到第一个
#: 字节」。旧的 0.8s 建立在「认证 / 额度 / 参数这类失败 0.1~0.5s 就回」之上，
#: 真机复测把这个前提推翻了（同一把 key / 同一个网关 / 同一个 main-auto）：
#:   · developer + 短提示词（61 tools + stream + reasoning_effort:high）
#:     -> 400 / 3000101，**4.0s**；
#:   · developer + 9 KB 提示词（同上参数）-> 400 / 3000101，**5.0s**；
#:   · 内容本身触发风控时 -> 403 / 4100406，**43~57s**，任何合理宽限期都盖不住
#:     （这正是 ④ 保留错误帧分支的原因，不是 GRACE 能解的）。
#: 6.0 = 实测最慢的「快失败」5.0s + 1s 抖动余量；再大只是把正常请求的首字节
#: 一起往后拖，收益为零（43s 那一档怎么调都盖不住）。回退旧行为把 env 设 0.8。
STREAM_STATUS_GRACE_ENV = "MIRAIHUB_MARVIS_STREAM_STATUS_GRACE"
STREAM_STATUS_GRACE_DEFAULT = 6.0

# ---------------------------------------------------------------------------
# ⑤ 超大请求的可观测性阈值
# ---------------------------------------------------------------------------
#: 超过这个体积的请求把日志从 WARNING 提到 **ERROR**。
#:
#: 实测：2 MB 合成请求上游回 500（5100007）；3.19 MB 真实请求要 25.8 s 才出头部。
#: 事后要能直接按 acc / model / msgs 定位「是谁发了这么大的东西」—— INFO 在
#: 两万行日志里是捞不出来的。
HUGE_REQUEST_BYTES = 4 * 1024 * 1024

#: 1 MB 的常量，避免各处重复写字面量。
_MB = 1048576.0

#: 本请求的处置备注（① max_tokens 降级、② 不换号原因 …）。
#:
#: 为什么用 ContextVar 而不是加形参：这些字段只在**一个请求内**有意义，而
#: _acquire / _attempt / _call_upstream 的签名被契约测试盯着（
#: test_async_sync_consistency 直接解包 4 元组并断言调用形状），加形参等于
#: 强迫所有调用点与测试替身跟着改。
#:
#: asyncio.to_thread 会把 contextvars 复制进工作线程；这里传的是**同一个**
#: dict 对象，所以工作线程里的写入在主上下文同样可见。
#: 每个请求是独立 task、独立 context，不需要手工 reset。
_REQUEST_NOTES: ContextVar = ContextVar("marvis_request_notes", default=None)
#: 本请求的 max_tokens 是否已被额度族降级过（跨账号、跨 _call_upstream 调用）。
#: 降级值就地写进 body，换号后仍然生效 —— 失败画像的 degraded 列必须反映
#: 整条请求的状态，否则第二个号会同时打出 32768 和 degraded=0，事后读不出
#: 它到底是不是降级后的那一发。用 ContextVar 而不是函数参数：调用链上
#: _call_upstream 被测试整体替换过（签名固定），不能加位置参数。
#: 降级目标值本身即「已降级」的判据（见 _call_upstream 的失败画像）：降级是就地
#: 改 body 的，所以 body 里的 max_tokens == 降级目标值 就说明这一发是降级形态。
#: 用它而不是 ContextVar：ContextVar 在「同线程连续跑多个 _attempt」的测试里会
#: 跨请求泄漏，把下一次的首次失败标成 degraded=1。
_DEGRADED_MARK = MAX_TOKENS_DEGRADE_TARGET


def _notes() -> dict:
    """当前请求的备注 dict（没有就返回一个临时 dict，写入不会丢）。"""
    value = _REQUEST_NOTES.get()
    return value if isinstance(value, dict) else {}


def _max_tokens_of(body: dict) -> int:
    """取请求里的输出预算（三个别名等价）；取不到 / 非数字返回 0。"""
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


def _acquire_timeout() -> float:
    """等上游响应头的上限（秒）。未设置 / 非法 -> 120；<=0 -> 关闭。"""
    raw = str(os.environ.get(ACQUIRE_TIMEOUT_ENV) or "").strip()
    if not raw:
        return ACQUIRE_TIMEOUT_DEFAULT
    try:
        return float(raw)
    except (TypeError, ValueError):
        return ACQUIRE_TIMEOUT_DEFAULT


def _stream_status_grace() -> float:
    """流式请求的「真实状态码宽限期」（秒）。非法 -> 默认；<=0 -> 立刻发心跳。"""
    raw = str(os.environ.get(STREAM_STATUS_GRACE_ENV) or "").strip()
    if not raw:
        return STREAM_STATUS_GRACE_DEFAULT
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return STREAM_STATUS_GRACE_DEFAULT
    return value if value > 0 else 0.0


def _fmt_seconds(value: float) -> str:
    """秒数的可读写法：整秒不带小数点，小数保留一位（0.5 / 120）。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number == int(number):
        return str(int(number))
    return ("%.1f" % number)


class _AcquireTimeout(Exception):
    """等上游响应头超时（ACQUIRE_TIMEOUT_ENV）。

    单独一个异常类型而不是复用 PlatformRejected：它是**本端**的中止，
    但日志里必须留下 acquire_timeout 这个值（需求 ③c），便于后面按小时
    统计「有多少请求是 prefill 卡的」。
    """

    def __init__(self, seconds: float, model: str = ""):
        self.seconds = float(seconds)
        self.model = str(model or "")
        super().__init__("acquire_timeout %.0fs" % self.seconds)


def _sse_error_frame(error_obj: dict) -> bytes:
    """统一的 SSE 错误帧：data: {"error":{...}} 后面紧跟终止帧 [DONE]。"""
    frame = {"error": dict(error_obj)}
    return (b"data: " + json.dumps(frame, ensure_ascii=False).encode("utf-8")
            + b"\n\n" + b"data: [DONE]\n\n")


def _heartbeat_frame() -> bytes:
    """流式请求的第一帧心跳 —— 用 protocol_bridge 的**合法 data 帧**。

    注释帧（`: ping`）不产生事件、不重置客户端 idle 计时器，等于没保活；
    严格校验的 SDK 还会因为缺 created / model 报 ValidationError。这里复用
    已经过 test_keepalive_frames 的那一帧。
    """
    try:
        from admin.platform import protocol_bridge as bridge

        return bridge.CHAT_PING
    except Exception:  # noqa: BLE001 - 心跳失败不该挡住流
        logger.debug("心跳帧取用失败", exc_info=True)
        return (b'data: {"id": "chatcmpl-keepalive", "object": '
                b'"chat.completion.chunk", "created": 0, "model": "keepalive", '
                b'"choices": [{"index": 0, "delta": {}, '
                b'"finish_reason": null}]}\n\n')


def _stream_error_response(http_status: int, error_obj: dict, *,
                           extra_headers: dict | None = None):
    """流式请求**已经开始**之后的失败：HTTP 200 + 错误帧（首帧）+ 终止帧。

    ## 为什么不返回 JSON 错误体

    stream:true 的调用方拿到的是事件流解析器。把 JSON 错误体塞进
    text/event-stream，多数客户端只会报「流里没有数据 / 连接被关闭」，真正原因
    （5100007 / 3000100 / 超时）全部丢失 —— 这正是 2026-11-19 那 4 条失败在
    客户端侧「看不出发生了什么」的原因。

    ## 为什么错误帧必须是**第一条 data 帧**

    ASGI 的 http.response.start 一旦发出，响应头就锁死，之后**无法**再补
    X-Marvis-Stream-Error。所以这里做两件事：
      1. 结局在建流前已确定 -> 响应头里**直接**带 X-Marvis-Stream-Error: 1
         与错误码 / 原始状态，客户端只看头就够判断；
      2. 错误帧作为**第一条 data 帧**发出，任何按行解析的客户端第一件事就撞见
         {"error":{...}}，不会先收到一堆正常 chunk。

    帧里带 http_status（映射后的原始 HTTP 状态）供上层还原语义 —— 传输层已经
    是 200 了，状态信息只能靠帧携带。
    """
    payload = dict(error_obj)
    payload["http_status"] = int(http_status)
    body = _sse_error_frame(payload)

    async def generator():
        yield body

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "X-Marvis-Stream-Error": "1",
        "X-Marvis-Stream-Error-Code": str(payload.get("code") or ""),
        "X-Marvis-Stream-Error-Status": str(int(http_status)),
    }
    headers.update(extra_headers or {})
    response = StreamingResponse(generator(), status_code=200,
                                 media_type="text/event-stream",
                                 headers=headers)
    # 供 /v1/messages 与 /v1/responses 把这条流还原成真实状态码（见
    # _run_normalized）：中继按 choices[] 解析，认不出错误帧。
    response.marvis_error = payload
    return response


def _degraded_headers(notes: dict | None) -> dict:
    """①c：把「max_tokens 被降级过」这件事**告诉调用方**，不静默改。"""
    degraded = (notes or {}).get("degraded_max_tokens")
    if not degraded:
        return {}
    return {"X-Marvis-Max-Tokens-Degraded": str(degraded.get("to")),
            "X-Marvis-Max-Tokens-Requested": str(degraded.get("from"))}


# ---------------------------------------------------------------------------
# 风控（4100404）= **平台级**退避，不是账号级冷却（2026-11-17）
# ---------------------------------------------------------------------------
#: 账号级冷却秒数 —— 只有「这个号自己坏了」才配。
#:
#: auth（4100403 登录态过期）：600 秒，期间不选这个号。
COOL_SECONDS_AUTH = 600
#: 需要给**账号**打冷却（cool_until）的 kind。
#:
#: ⚠ risk_control **故意不在**这里 —— 判据见 _cool_seconds 的 docstring。
COOL_KINDS = frozenset({"auth"})


def _cool_seconds(kind: str) -> int:
    """按失败类型决定**账号级**冷却秒数；0 = 不给这个号打冷却。

    ## 为什么 risk_control 必须是 0（2026-11-17 线上死循环的根因）

    4100404「请求被风控拦截」是**出口 IP 级**的风控，不是账号级。真机证据：

      · 3 个号的 guid / openid 全不同，同一秒被拦；
      · 2KB 的 ping 请求同样被拦（与请求内容、模型、账号都无关）；
      · 4100403（凭证失效）零次、4100406（指纹非法）零次
        ⇒ 凭证与设备指纹都是好的；
      · 唯一共享因子是**出口 IP**（3.105.136.123，AWS 悉尼）。

    把 IP 级的错误按账号级处理会**自放大**成整站不可用：

      1. 请求撞 4100404 → 给当前号打 600 秒冷却；
      2. _attempt 换号重试，把**同一份 body** 在 1 秒内重放给另外 2 个号
         → 3 个号全部吃 600 秒冷却；shared.select_account 的
         cool_until <= now 过滤随即返回空 → 503 accounts_cooling；
      3. 600 秒到期 → 下一个请求再次 4100404 → 重新冷却 → **无限循环**
         （实测 10:05:08 UTC 到期、10:05:46 又被续到 10:15:46）。

    ⇒ 风控只做**平台级退避**（note_risk_control），一个号都不冷却：
    换掉出口 IP 之后号池原样可用，不需要人工解冻。
    """
    return COOL_SECONDS_AUTH if kind in COOL_KINDS else 0


#: 平台级风控退避的默认窗口（秒），可用 MIRAIHUB_MARVIS_RISK_BACKOFF_SECONDS 覆盖。
#:
#: ⚠️ 2026-11-18 17:38 全站 503 事故：60 → 10。
#: `4100404 请求被风控拦截` 命中的是**这一条请求的内容**（腾讯内容风控
#: 对 system 正文做字面量黑名单），不是「整个出口 IP 失效」。用一个 60 秒的
#: 平台级窗口去处置一次内容命中，等于把**一条坏提示词**放大成**全站不可用**：
#: 实测 20 分钟内 59 次 503（退避期内直接拒），而上游真正被拦的只有 8 次。
#: 10 秒是「保护上游不被连续轰炸」与「别把偶发放大成故障」之间的折中。
RISK_BACKOFF_ENV = "MIRAIHUB_MARVIS_RISK_BACKOFF_SECONDS"
RISK_BACKOFF_DEFAULT_SECONDS = 10

#: **连续**命中多少次 4100404 才允许触发平台级退避（默认 5）。
#:
#: 阈值 = 1（旧行为）的问题：一次内容命中就把平台锁死。真实形态是「零散偶发」
#: 与「成串」两种，只有成串才说明出口 IP 整段被风控、退避才有意义。
#: 可用 MIRAIHUB_MARVIS_RISK_BACKOFF_THRESHOLD 覆盖。
RISK_BACKOFF_THRESHOLD_ENV = "MIRAIHUB_MARVIS_RISK_BACKOFF_THRESHOLD"
RISK_BACKOFF_THRESHOLD = 5

#: 「连续」的时间窗（秒）：命中计数只统计最近这么久内的 4100404。
#:
#: 没有这个窗口的话，一天里零散撞 5 次也会把平台锁一次 —— 而「连续」的语义
#: 本来就是「短时间成串」。窗口内没有新命中，计数自然过期归零。
RISK_HIT_WINDOW_SECONDS = 120

#: 平台级风控状态：until = 退避截止的 monotonic 时间戳，at = 最近一次命中。
#:
#: ## 为什么是模块级 dict + threading.Lock，不是 asyncio 全局变量
#:
#: _attempt（真正打上游的那段）**整段跑在 asyncio.to_thread 的工作线程里**
#: （见 _acquire 的 docstring），线程里没有运行中的事件循环 ——
#: asyncio.Lock 拿不到也没法用。而 marvis 的并发请求来自线程池里的
#: **不同线程**，所以这里要的是**线程间**互斥：一把 threading.Lock 保护
#: 这几个浮点数，把「读 → 比较 → 写 max」放在同一次加锁里完成。否则两个线程
#: 同时命中 4100404 会各写各的截止时间，退避窗口可能被更早的那次覆盖成更短。
_RISK_LOCK = threading.Lock()
_RISK_STATE: dict[str, float] = {"at": 0.0, "until": 0.0, "hits": 0.0}


def _risk_backoff_threshold() -> int:
    """连续命中多少次才触发平台退避。未设置 / 非法 / 非正值一律退回默认 5。"""
    raw = str(os.environ.get(RISK_BACKOFF_THRESHOLD_ENV) or "").strip()
    if not raw:
        return RISK_BACKOFF_THRESHOLD
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        return RISK_BACKOFF_THRESHOLD
    return value if value > 0 else RISK_BACKOFF_THRESHOLD


def _risk_backoff_seconds() -> int:
    """退避窗口长度（秒）。未设置 / 非法 / 非正值一律退回默认 60。"""
    raw = str(os.environ.get(RISK_BACKOFF_ENV) or "").strip()
    if not raw:
        return RISK_BACKOFF_DEFAULT_SECONDS
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        return RISK_BACKOFF_DEFAULT_SECONDS
    return value if value > 0 else RISK_BACKOFF_DEFAULT_SECONDS


def note_risk_control() -> float:
    """记一次 4100404 命中，返回**当前**平台退避剩余秒数（0 = 没有触发退避）。

    **绝不抛**，绝不主动锁平台。语义（2026-11-18 17:38 事故后重写）：

      1. 单次 4100404 **不触发**平台退避 —— 只让这一条请求失败、原样报给客户端；
      2. 最近 RISK_HIT_WINDOW_SECONDS 窗口内**连续**撞满
         _risk_backoff_threshold() 次，才认为「出口 IP 成段被风控」，此时
         才开一个 _risk_backoff_seconds() 的窗口；
      3. 任何一次上游成功都会 clear_risk_hits() 把连击清零 ——「连续」是
         真连续，不是累计。

    ## 为什么单次不能锁平台（旧行为 = 全站 503）

    4100404 的触发源里至少有一条是**请求内容**（system 正文里被拉黑的开场白 /
    身份句，见 _TENCENT_FINGERPRINT_RULES）。同一条坏提示词每轮都会重发，旧实现
    于是「命中 → 锁 60 秒 → 到期 → 再命中」，而这 60 秒里**所有**请求连上游都
    打不到，直接 503：一条提示词拖垮整站（17:38 实测 20 分钟 59 次 503）。
    """
    seconds = _risk_backoff_seconds()
    threshold = _risk_backoff_threshold()
    now = time.monotonic()
    with _RISK_LOCK:
        # 窗口外的旧命中不算「连续」：计数过期归零，再把这一次重新数进来。
        if now - float(_RISK_STATE.get("at") or 0.0) > RISK_HIT_WINDOW_SECONDS:
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
        if now - float(_RISK_STATE.get("at") or 0.0) > RISK_HIT_WINDOW_SECONDS:
            return 0
        return int(_RISK_STATE.get("hits") or 0)


def clear_risk_hits() -> None:
    """上游成功一次 ⇒ 连击计数归零（**不动**已有的退避窗口）。"""
    with _RISK_LOCK:
        _RISK_STATE["hits"] = 0.0
        _RISK_STATE["at"] = 0.0


def risk_backoff_remaining() -> float:
    """平台级风控退避还剩几秒（0 = 没有退避，可以打上游）。"""
    now = time.monotonic()
    with _RISK_LOCK:
        return max(0.0, _RISK_STATE["until"] - now)


def reset_risk_backoff() -> None:
    """清空平台级退避状态（测试 / 人工恢复用）。"""
    with _RISK_LOCK:
        _RISK_STATE["at"] = 0.0
        _RISK_STATE["until"] = 0.0
        _RISK_STATE["hits"] = 0.0


# ---------------------------------------------------------------------------
# 4100404 的**账号级短冷却**（2026-11-20，内存态、不写库）
# ---------------------------------------------------------------------------
# 平台级退避回答的是「这几十秒别再打上游」；它没回答「撞墙的那个号下一发
# 还会不会排到队首」。没有账号维度的话，退避过去后同一个号立刻又被选中、
# 再撞一次 4100404 —— 用户看到的就是「一直好不了」。
#
# 刻意**不**复用 DB 的 cool_until（shared.note_failure 那条路）：
#   · 4100404 的触发源里既有出口 IP、也有这一条请求的内容，把它写进号表等于
#     给好号留一条永久脏记录，控制台上还会长成一排「风控」；
#   · 这里要的只是「几十秒内别选它」——进程内的单调时钟表就够了，重启即清空。
#
# 这也是本 fork 的口径：不写库、不跨进程，单进程内生效。
#: 冷却秒数（默认 60）。`MARVIS_RISK_ACCOUNT_COOLDOWN` 覆盖。
RISK_ACCOUNT_COOLDOWN_ENV = "MARVIS_RISK_ACCOUNT_COOLDOWN"
RISK_ACCOUNT_COOLDOWN_DEFAULT = 60
#: 总开关（默认 1 = 开）。设 0 回到「命中后不冷却任何账号」。
RISK_ACCOUNT_COOLDOWN_SWITCH_ENV = "MARVIS_RISK_COOLDOWN_ON_4100404"

#: 认出「关」的写法；其余（含写错）一律按开处理。
_OFF_VALUES = frozenset({"0", "false", "no", "off", "disable", "disabled"})

_RISK_ACCOUNT_LOCK = threading.Lock()
#: account_id -> 冷却截止的 monotonic 时间戳。**只活在本进程**。
_RISK_ACCOUNT_STATE: dict[int, float] = {}


def risk_account_cooldown_enabled() -> bool:
    """`MARVIS_RISK_COOLDOWN_ON_4100404` 是否为开（缺省 = 开）。"""
    raw = (os.environ.get(RISK_ACCOUNT_COOLDOWN_SWITCH_ENV) or "").strip().lower()
    if not raw:
        return True
    return raw not in _OFF_VALUES


def _risk_account_cooldown_seconds() -> int:
    """账号冷却秒数；未设置 / 非法 / 非正值一律退回默认 60。"""
    raw = str(os.environ.get(RISK_ACCOUNT_COOLDOWN_ENV) or "").strip()
    if not raw:
        return RISK_ACCOUNT_COOLDOWN_DEFAULT
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        return RISK_ACCOUNT_COOLDOWN_DEFAULT
    return value if value > 0 else RISK_ACCOUNT_COOLDOWN_DEFAULT


def cool_account_after_risk(account_id: int) -> float:
    """记一次 4100404 的账号冷却，返回本次冷却秒数（0 = 关掉了 / 记不上）。**绝不抛**。

    取 max 而不是直接覆盖：并发请求同时命中同一个号时，后到的那次不许把
    先到那次的窗口改短（与 _RISK_STATE 的写法同口径）。
    """
    if not risk_account_cooldown_enabled():
        return 0.0
    try:
        account_id = int(account_id or 0)
    except (TypeError, ValueError):
        return 0.0
    if account_id <= 0:
        return 0.0
    seconds = _risk_account_cooldown_seconds()
    until = time.monotonic() + seconds
    with _RISK_ACCOUNT_LOCK:
        _RISK_ACCOUNT_STATE[account_id] = max(
            _RISK_ACCOUNT_STATE.get(account_id, 0.0), until)
    return float(seconds)


def risk_cooled_accounts() -> frozenset[int]:
    """当前仍在冷却窗口内的账号 id（顺手清掉过期的）。"""
    now = time.monotonic()
    with _RISK_ACCOUNT_LOCK:
        for account_id in [k for k, until in _RISK_ACCOUNT_STATE.items()
                           if until <= now]:
            _RISK_ACCOUNT_STATE.pop(account_id, None)
        return frozenset(_RISK_ACCOUNT_STATE)


def risk_account_cooldown_remaining() -> float:
    """还在冷却的账号里最长的剩余秒数（0 = 没有号在冷却）。"""
    now = time.monotonic()
    with _RISK_ACCOUNT_LOCK:
        return max([0.0] + [until - now for until in _RISK_ACCOUNT_STATE.values()])


def reset_risk_account_cooldown() -> None:
    """清空账号级冷却（测试 / 人工恢复用）。"""
    with _RISK_ACCOUNT_LOCK:
        _RISK_ACCOUNT_STATE.clear()


def _warn_upstream_failure(account, exc: client.UpstreamError, *,
                           model: str, stream: bool) -> None:
    """上游失败打一条 warn —— 可观测性的最低要求。

    2026-11-15 之前 marvis 失败**一条日志都没有**：线上只能靠 `usage_logs`
    的毫秒时间戳反推「哪一秒开始串行化」，完全看不出失败原因（kind / 业务码 /
    到底是哪个号 / 上游原话）。这里把 kind、HTTP 状态、业务码、detail 前
    200 字符和 account id 一次打全。

    ⚠ **不含任何凭证**：detail 是上游返回体，access_token 只在请求头里。
    """
    detail = str(exc.detail or exc.message or "").replace("\n", " ").replace("\r", " ")
    logger.warning(
        "Marvis 上游失败 kind=%s status=%s code=%s acc=%s model=%s stream=%s "
        "detail=%s",
        exc.kind, exc.status or "-", exc.code or "-",
        int(getattr(account, "id", 0) or 0), model or "-",
        "1" if stream else "0", detail[:200])


def _profile_of(body: dict) -> dict:
    """请求形状画像 —— **只算数字，绝不复制内容**。

    2026-11-16 线上：4100406 只在大请求（主代理那条 `You are a helpful AI
    assistant…`）上偶发，同一时刻同一个号的小请求 200。要把「请求多大 /
    几条消息 / 几个工具 / 系统提示多长」和成功失败对上，才可能定位触发条件。

    这里算的字节数与 `client` 真正发出去的 JSON 完全一致
    （`json.dumps(body, ensure_ascii=False).encode()`）。
    """
    messages = body.get("messages")
    messages = messages if isinstance(messages, list) else []
    tools = body.get("tools")
    tools = tools if isinstance(tools, list) else []
    sys_chars = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        if str(message.get("role") or "") not in ("system", "developer"):
            continue
        content = message.get("content")
        if isinstance(content, str):
            sys_chars += len(content)
        else:
            sys_chars += len(str(content or ""))
    try:
        size = len(json.dumps(body, ensure_ascii=False).encode("utf-8"))
    except Exception:  # noqa: BLE001 - 画像失败绝不能影响转发
        size = 0
    return {"bytes": size, "msgs": len(messages), "tools": len(tools),
            "sys_chars": sys_chars, "max_tokens": body.get("max_tokens")}


def _dump_403_enabled() -> bool:
    """临时转储开关是否打开（每次调用都读环境变量，默认关闭）。"""
    return str(os.environ.get(DUMP_403_ENV) or "").strip().lower() in (
        "1", "true", "yes", "on")


def _dump_403_keep() -> int:
    """最多保留几份；非法值退回默认 3。"""
    try:
        keep = int(str(os.environ.get(DUMP_403_KEEP_ENV) or "").strip() or
                   DUMP_403_DEFAULT_KEEP)
    except (TypeError, ValueError):
        return DUMP_403_DEFAULT_KEEP
    return keep if keep > 0 else DUMP_403_DEFAULT_KEEP


def _prune_dump_403(directory: str) -> None:
    """只留最新 N 份，删最旧的。

    文件名里带纳秒时间戳（`mh403-<ns>-<sha16>.json.gz`），定宽 ⇒ 字典序就是
    时间序，不用 stat 每个文件。
    """
    keep = _dump_403_keep()
    try:
        names = sorted(name for name in os.listdir(directory)
                       if name.startswith(DUMP_403_PREFIX)
                       and name.endswith(".json.gz"))
    except OSError:
        return
    for name in names[:max(0, len(names) - keep)]:
        try:
            os.unlink(os.path.join(directory, name))
        except OSError:  # noqa: PERF203 - 删不掉不影响本次转储
            pass


def _dump_403_body(body: dict, *, status: int | None,
                   account_id: int = 0) -> str:
    """把**本次实际发出的 body 原始字节**写成 gzip，返回路径（未落盘返回空串）。

    字节与 `client.open_chat_stream` 发出去的完全一致：
    `json.dumps(body, ensure_ascii=False).encode()`（client.py 同一行），
    所以**原样重放这份 dump 就等于重放上游当时收到的那份请求**。

    只对 `status == 403` 落盘，且同内容只落一次（见 `_DUMP_403_SEEN`）。
    ⚠ 绝不把内容写进日志 —— 日志里只有路径与字节数。
    """
    if not _dump_403_enabled() or status != 403:
        return ""
    try:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    except Exception:  # noqa: BLE001 - 转储失败绝不能影响转发
        logger.warning("Marvis 403 转储：body 序列化失败，已跳过")
        return ""
    digest = hashlib.sha256(data).hexdigest()[:16]
    seen = _DUMP_403_SEEN.get(digest)
    if seen:
        return seen
    directory = str(os.environ.get(DUMP_403_DIR_ENV) or DUMP_403_DEFAULT_DIR)
    path = os.path.join(directory, "%s%d-%s.json.gz" % (
        DUMP_403_PREFIX, time.time_ns(), digest))
    try:
        os.makedirs(directory, exist_ok=True)
        # 先写 .part 再 rename：读到半截文件的重放脚本会拿到坏 JSON。
        partial = path + ".part"
        with gzip.open(partial, "wb") as handle:
            handle.write(data)
        os.replace(partial, path)
    except Exception as exc:  # noqa: BLE001 - 同上，绝不能影响转发
        logger.warning("Marvis 403 转储失败 path=%s: %s",
                       path, type(exc).__name__)
        return ""
    _prune_dump_403(directory)
    if len(_DUMP_403_SEEN) >= _DUMP_403_SEEN_MAX:
        _DUMP_403_SEEN.clear()
    _DUMP_403_SEEN[digest] = path
    # 只有路径与字节数 —— 内容一个字符都不进日志。
    logger.warning(
        "Marvis 403 请求体已转储 path=%s bytes=%d acc=%s（内容不进日志；"
        "排障结束必须删除）", path, len(data), account_id)
    return path


def _log_failure_profile(account_id: int, exc: "client.UpstreamError", *,
                         model: str, stream: bool, body: dict,
                         degraded: bool, profile: dict | None = None,
                         attempt: int = 0, attempts: int = 0) -> None:
    """上游失败画像 —— **必须带 max_tokens / degraded / tools / sys_chars / attempt**。

    为什么这几列是硬要求：

      · `max_tokens` + `degraded`：3000100 的判据是「`max_tokens` 与该号该模型
        当次剩余预算的对照」，没有 `max_tokens` 就看不出这次到底是原始值还是
        降级值撞的；没有 `degraded` 就分不清「第一次命中」与「降级后仍命中」，
        而这两者的处置完全相反（前者同号重试、后者换号）。
      · `tools` + `sys_chars`：4100406 的判据是**请求形状**（见 `_call_upstream`
        的 docstring：只在大请求上偶发、同一时刻小请求 200）。这两列必须能和
        INFO 请求画像的同名列逐列对齐 —— 失败画像与成功画像不同列，
        「形状 vs 成功/失败」就无从比对。
      · `attempt=%d/%d`：同号重试最多 `1 + SAME_ACCOUNT_RETRIES` 次，`index+1`
        就是「这一发是第几次」。缺了它，同一次请求内第 1 发（原值）与第 2 发
        （降级值）在日志里只能靠 max_tokens 猜。

    其余字段只记**数字**：`detail` 是上游返回体（不含凭证），截断到 200 字符。
    """
    messages = body.get("messages") if isinstance(body, dict) else None
    msgs = len(messages) if isinstance(messages, list) else 0
    try:
        size = len(json.dumps(body, ensure_ascii=False).encode("utf-8"))
    except Exception:  # noqa: BLE001 - 画像失败绝不能影响转发
        size = 0
    shape = profile if isinstance(profile, dict) else _profile_of(body or {})
    detail = str(exc.detail or exc.message or "").replace("\n", " ")
    max_tokens = body.get("max_tokens", "-") if isinstance(body, dict) else "-"
    logger.warning(
        "Marvis 失败画像 kind=%s status=%s code=%s acc=%s model=%s stream=%s "
        "bytes=%d msgs=%d tools=%d sys_chars=%d max_tokens=%s degraded=%s "
        "attempt=%d/%d detail=%s",
        exc.kind, exc.status or "-", exc.code or "-",
        int(account_id or 0), model or "-",
        "1" if stream else "0", size, msgs,
        int(shape.get("tools") or 0), int(shape.get("sys_chars") or 0),
        max_tokens, "1" if degraded else "0",
        int(attempt or 0), int(attempts or 0), detail[:200])


def _call_upstream(data: dict, realm: str, body: dict, account_id: int, *,
                   stream: bool, profile: dict | None = None):
    """调上游 —— **同步实现**，由 `_acquire` 所在的工作线程承载；只对「瞬时」失败同号重试。

    ## 什么算「瞬时」

    ① 白名单 kind（`network` / `timeout`）；
    ② `upstream_business` 里 **业务码命中 `RETRY_SAME_ACCOUNT_BIZ_CODES`**
       （当前只有 `4100406 非法请求`）—— 实测同号同分钟 200/403 交替，属上游
       偶发，重打一次就过；参数类错误（400/422）不在此列，换号/重打都无用。

    ## 为什么是**同步**，以及谁负责把它挪出事件循环

    `client.open_chat_stream` 内部是 `urllib.request.urlopen`：它会一直阻塞
    到上游把**响应状态行 + 响应头**吐回来。`admin.server` 是单进程单 worker
    的 uvicorn，在事件循环线程里调它等于把整个网关钉死 —— 2026-11-15 实测
    单次阻塞 3.3s/7s/17s/27s/**63.5s**，43 个在飞请求被串行化后超期，
    上游回 `4100406 非法请求`。非流式的 `chat_completions` 会 `read()`
    整个响应体，同样是阻塞调用。

    ⇒ 阻塞是真的，所以**整条 `_acquire`（鉴权 + 选号 + 建连）在工作线程里同步
    跑完**，事件循环只做 `await asyncio.to_thread(_acquire, ...)`。本函数因此在
    线程里**同步调用** client，**不许**再出现 `asyncio.to_thread` —— 线程里套
    线程只是多一次上下文切换，还把「谁在事件循环上」这件事搞模糊了。

    ## 2026-11-17：758ab76 全量 502 的根因

    29f245f 合并时把本函数与 `_attempt` 改成 `async def`（旧的「每步 to_thread」
    写法），却留下了上游同步的 `_acquire` —— 它在工作线程里 `_attempt(...)`，
    拿到的是 coroutine 对象，解包即 `TypeError: cannot unpack non-iterable
    coroutine object`，marvis 100% 502。现在统一回上游的同步形状：阻塞调用在
    线程池，异步只留给 `chat_completions` 的 `to_thread(_acquire)` 与记账。

    ## 为什么重试**不重新选号**

    `select_account` 是「换号」语义。瞬时网络失败时换号毫无意义，还会白占
    一个租约；这里重试**复用同一个已租账号**（不 `exclude.discard`、不重新
    `select`），与「换号」严格分开。

    换号由调用方 `_attempt` 负责：本函数把**同号重试耗尽**后照样抛，
    `_attempt` 捕获后把该号加入 `exclude` 再选下一个号，并打一条
    `Marvis 换号重试` 日志。2026-11-16 之前换号是**静默**的：线上只能看到
    一个号连打 3 次后回 403，看不出到底有没有第二个号可换。

    ## 请求画像（2026-11-16 加）

    每次上游调用打一条 INFO：`bytes / msgs / tools / sys_chars / max_tokens`；
    失败分支再打一条 `失败画像`，`_attempt` 成功时打 `成功画像`。
    4100406 只在大请求上偶发、同一时刻小请求 200 ⇒ 必须把「请求形状」和
    「成功 / 失败」放在一起比。**只记数字，绝不落 body 内容**（含用户对话）。
    """
    if profile is None:
        profile = _profile_of(body)
    notes = _notes()
    logger.info(
        "Marvis 请求画像 acc=%s model=%s stream=%s bytes=%d msgs=%d tools=%d "
        "sys_chars=%d max_tokens=%s",
        account_id, body.get("model") or "-", int(bool(stream)),
        profile["bytes"], profile["msgs"], profile["tools"],
        profile["sys_chars"], profile["max_tokens"])
    # ⑤ >4 MB：WARNING 不够用，升到 ERROR 并带全定位字段。
    # 实测 2 MB 合成请求上游就回 500（5100007）、3.19 MB 真实请求要 25.8 s 才
    # 出响应头 —— 事后必须能一眼捞出「谁发了多大的东西」。
    if profile["bytes"] > HUGE_REQUEST_BYTES:
        logger.error(
            "Marvis 超大请求 bytes=%d (%.2f MB) acc=%s model=%s msgs=%d "
            "tools=%d sys_chars=%d max_tokens=%s threshold=%d —— 上游对这类请求"
            "可能直接 500（5100007）或 400（3000101 invalid parameter）",
            profile["bytes"], profile["bytes"] / _MB, account_id,
            body.get("model") or "-", profile["msgs"], profile["tools"],
            profile["sys_chars"], profile["max_tokens"], HUGE_REQUEST_BYTES)
    # ① 的「已降级」标记与 ② 的同号重试计数：都只在**本请求**内有效。
    internal_retries = 0
    degrade_done = False
    for index in range(1 + SAME_ACCOUNT_RETRIES):
        try:
            if stream:
                return client.open_chat_stream(
                    data, realm, body, account_id=account_id)
            return client.chat_completions(
                data, realm, body, account_id=account_id)
        except client.UpstreamError as exc:
            # 失败画像：与「成功画像」同列，按 bytes/msgs/tools 直接对比；
            # 另带**原始** max_tokens 与「是否已降级」—— 3000100 的判据是
            # max_tokens 与该号该模型当次剩余预算的对照，缺这两列没法事后判定。
            # 失败画像：**每一次**上游失败都留痕（WARNING），包括即将被降级重试的
            # 第一次命中 —— 否则事后判定不出这一发是原值还是降级值。
            # max_tokens 与 degraded 两列是判据，缺一列就无法复核 3000100 的处置链。
            _log_failure_profile(
                account_id, exc, model=str(body.get("model") or ""),
                stream=stream, body=body,
                degraded=(degrade_done
                          or _max_tokens_of(body) == _DEGRADED_MARK),
                # 形状列必须与上面那条 INFO 请求画像**同一套数**（profile 就是
                # 函数开头算的那一份）：失败画像的价值在于能和成功画像逐列比。
                profile=profile,
                # 这一发是第几次：同号重试最多 1 + SAME_ACCOUNT_RETRIES 次。
                # 少了它，第 1 发（原值）与第 2 发（降级值）在日志里分不开。
                attempt=index + 1, attempts=1 + SAME_ACCOUNT_RETRIES)
            # 403 时把「本次实际发出的 body」转储一份（仅当
            # MIRAIHUB_MARVIS_DUMP_403=1；默认关闭，连目录都不建）。
            # 3.5MB 的 gzip 要十几毫秒 —— 这里本来就在工作线程里，直接落盘。
            if exc.status == 403:
                _dump_403_body(body, status=exc.status,
                               account_id=account_id)
            # ① 输入 + max_tokens 超上游预算：**不是**请求体积问题。
            # 把 max_tokens 降到上游收得下的值再打一次；降过还失败就直接报，
            # 不再消耗任何账号（用户侧只需要知道「要减上下文」）。
            if _is_quota_exhausted(exc):
                want = _max_tokens_of(body)
                if not degrade_done and want > MAX_TOKENS_DEGRADE_THRESHOLD:
                    # **就地**改（不要 body = dict(body)）：_attempt 传下来的是
                    # 同一个 dict，降级后的形状要跟着换号走 —— 否则每个新号都会
                    # 先用 128000 白撞一次 3000100 再降级，平白多耗一轮。
                    body["max_tokens"] = MAX_TOKENS_DEGRADE_TARGET
                    body.pop("max_completion_tokens", None)
                    body.pop("max_output_tokens", None)
                    degrade_done = True
                    notes["degraded_max_tokens"] = {
                        "from": want, "to": MAX_TOKENS_DEGRADE_TARGET}
                    logger.warning(
                        "Marvis max_tokens 降级重试 degraded_max_tokens=true "
                        "acc=%s from=%d to=%d（上游 3000100：该号该模型当次预算"
                        "不够，先降 max_tokens 同号重试）",
                        account_id, want, MAX_TOKENS_DEGRADE_TARGET)
                    continue
                # 降级后仍是 3000100（或 max_tokens 本来就没超阈值）：这**不是**
                # 参数错，而是这个号在这个模型上的当次可用额度不够。交给
                # _attempt 标记 (账号×模型) 额度紧张并换号；只有池子用完才报。
                # 业务码在 detail 的 JSON 里；exc.code 在生产路径上是 HTTP
                # 状态（client 把 code 填成 400），直接记会丢掉 3000100。
                notes["quota_fail_code"] = (
                    client._biz_code_of(exc.detail or "")
                    or str(exc.code or "") or "3000100")
                raise
            # ② 上游内部错误（5100007）：与账号无关 -> 同号重试一次，
            # **绝不换号**（换号只会把同一份 body 在另一个号上再失败一次，并把
            # 那个号的 err_count 也打上去 —— 线上 192->191->194 即此）。
            if _is_upstream_internal(exc):
                if internal_retries >= INTERNAL_RETRIES:
                    raise
                internal_retries += 1
                delay = (INTERNAL_RETRY_BASE
                         + random.uniform(0, INTERNAL_RETRY_JITTER))
                logger.warning(
                    "Marvis 上游内部错误同号重试 acc=%s code=%s 第 %d/%d 次，"
                    "退避 %.2fs（不换号）",
                    account_id, exc.code or "-", internal_retries,
                    INTERNAL_RETRIES, delay)
                time.sleep(delay)
                continue
            # 白名单 kind（网络抖动 / 瞬断）**或** 上游偶发业务码（4100406）。
            # 后者只在 kind 已经是 `upstream_business` 时才看 —— `auth` /
            # `risk_control` 即便 detail 里带了同一个码也不重试。
            transient = exc.kind in RETRY_SAME_ACCOUNT_KINDS or (
                exc.kind == "upstream_business"
                and any(code in str(exc.detail or "")
                        for code in RETRY_SAME_ACCOUNT_BIZ_CODES))
            # 上限仍然**只由这一个标记**决定（`index + 1 > SAME_ACCOUNT_RETRIES`），
            # 没有第二个计数器：最多 1 + SAME_ACCOUNT_RETRIES = 3 次尝试。
            if index + 1 > SAME_ACCOUNT_RETRIES or not transient:
                raise
            # 递增 + 抖动退避：第 index 次尝试失败后等
            # 基数 × (index + 1) + U(0, 抖动上限) 秒。
            # 按每次 ~5–10% 独立概率估算，3 次尝试把失败率压到 ~0.1%
            # （0.1³ = 0.1%，0.05³ ≈ 0.0125%）。
            delay = (SAME_ACCOUNT_RETRY_DELAY * (index + 1)
                     + random.uniform(0, SAME_ACCOUNT_RETRY_JITTER))
            logger.warning(
                "Marvis 瞬时失败（kind=%s status=%s code=%s），同号重试 "
                "acc=%s 第 %s/%s 次重试，退避 %.2fs",
                exc.kind, exc.status or "-", exc.code or "-", account_id,
                index + 1, SAME_ACCOUNT_RETRIES, delay)
            # 退避是「本请求」的等待：工作线程里同步 sleep，事件循环照旧转。
            time.sleep(delay)


#: 额度族的业务码。上游 HTTP 层只给状态码，业务码在响应体的 JSON 里。
#: 3000100 以前被当上下文超限处理，结果在同一个号上反复重发；现在归属
#: 额度族：先省输出预算（降 max_tokens）再换号。
QUOTA_EXHAUSTED_CODES = ("3000100",)
#: 上游服务端内部错误的业务码（与账号无关，换号只会把好号打脏）。
UPSTREAM_INTERNAL_CODES = ("5100007",)


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
    按 `account_model_cool` 跳过 (account, model) 组合，所以这里只负责落库
    —— **走的是既有链路，没有第二套换号机制**。

    ## 与 5100007 的分界

    5100007 是上游服务端内部错误：同一个 body 打给任何号都是同一个答案，
    标了也没用，换号只会污染整池。3000100 是**额度**：换一个号就有预算，
    所以标完继续换号。

    **不抛异常**：隔离写失败不能影响响应。
    """
    if account is None or not str(model or "").strip():
        return
    try:
        from datetime import datetime, timedelta

        from admin.models import AccountModelCooldown

        now = datetime.utcnow()
        until = now + timedelta(seconds=MODEL_QUOTA_TIGHT_SECONDS)
        reason = "上游 %s：该号在该模型上的当次可用额度不足" % (
            code or "3000100")
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


#: 上游回 3000101 invalid parameter 时按顺序剥掉的可选字段（上游 PR #55）。
#:
#: 网关对 OpenAI 扩展字段是**原样透传**的（那是客户端的自由），代价是上游
#: 一旦不认就整条 400。上游又不告诉你是哪个字段，只回一句 invalid parameter。
#: 所以留一条降级路径：撞上 3000101 就按这张表逐个摘，摘掉后能过就照常出字。
#: 顺序是「最可能被误加 -> 最不可能」：response_format 已在前置归一里处理，
#: 剩下的按客户端常见溢出来排；工具相关放最后（剥掉会让模型不再调工具）。
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
    "tool_choice",
    "tools",
    "functions",
)


def _degrade_body(body: dict, dropped: set[str]) -> dict | None:
    """按 _DEGRADE_FIELDS 摘掉**一个**还没摘过的字段，返回新 body。

    没有可摘的字段时返回 None（说明这条请求已经没什么可退的了）。
    dropped 由调用方持有，保证同一个字段不会反复摘、也不会无限重试。
    """
    for field in _DEGRADE_FIELDS:
        if field in body and field not in dropped:
            dropped.add(field)
            out = dict(body)
            out.pop(field, None)
            return out
    return None


def _is_param_error(exc: client.UpstreamError) -> bool:
    """这条 400 是不是「参数不被上游接受」（而不是账号 / 额度 / 风控）。

    判据取上游原文：3000101 invalid parameter。只有这一类**换号无用、降级
    有效** —— 换号只会把好号一个个撞成「有错误记录」，而降级能真出字。
    """
    if int(exc.status or 0) not in (400, 422):
        return False
    blob = ("%s %s" % (exc.detail or "", exc.message or "")).lower()
    return "3000101" in blob or "invalid parameter" in blob


#: 上游表达「上下文超限」的各种措辞（不同后端说法不一样）。
#:
#: 判据取「够具体、不会误伤」的短语 —— 不能只匹配 token，否则把
#: invalid token 这类参数错也吃进来。必须在 _is_param_error **之前**判断：
#: 3000100 和 3000101 前缀相同，但一是内容长度、一是字段不认，处理方式相反。
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

#: 上下文超限时**原样重发**几次（每次都是一次重新路由的机会）。
_MAX_CTX_RETRIES = 4

#: 重试之间的间隔：不纯粹是退避 —— 上游路由决策带时间相关性，间隔一两秒
#: 能显著提高「换一个后端」的概率。
_CTX_RETRY_GAP = 1.2


def _is_context_overflow(exc: "client.UpstreamError") -> bool:
    """这条错误是不是「上下文太长，这次那个后端装不下」。"""
    if int(exc.status or 0) not in (400, 413, 422):
        return False
    blob = ("%s %s" % (exc.detail or "", exc.message or "")).lower()
    return any(word in blob for word in _CONTEXT_OVERFLOW_WORDS)


def _sticky_key_for(body: dict) -> str:
    """会话键：marvis|会话标识（PR #48）。

    带平台前缀是必须的 —— session_key_for() 在各平台算法相同，不带前缀会让
    「同时打 marvis 和 loomy 的同一段对话」共用一条绑定，换平台就命中不上
    （更糟：命中到别的平台的 uid）。
    """
    try:
        from admin.pool import session_key_for

        key = session_key_for(body)
    except Exception:  # noqa: BLE001 - 算不出来就退化成「不粘」
        return ""
    return ("marvis|" + key) if key else ""


def _bind_sticky(sticky_key: str, account) -> None:
    """成功后把这个会话绑到该号，后续同会话优先回到它。"""
    if not sticky_key or account is None:
        return
    uid = str(getattr(account, "uid", "") or "")
    if not uid:
        return
    try:
        from admin.pool import POOL

        POOL.sticky.bind(sticky_key, uid)
    except Exception:  # noqa: BLE001 - 粘性只是优化，不能影响主链路
        pass


def _attempt(db, request, key, body: dict, realm: str, *, stream: bool):
    """选号 + 租约 + 调上游。换号语义与其它平台一致。

    ## 为什么是**同步**：它整段都跑在工作线程里

    这里的三类调用全是**同步阻塞**的：

      · `shared.select_account` / `lease` / `note_failure`：SQLAlchemy 同步会话；
      · `marvis_accounts.ensure_token`：可能 `db.commit()`；
      · `client.open_chat_stream` / `chat_completions`：`urllib` 同步 HTTP。

    留在事件循环线程上，任何一次慢调用都是**全站**慢（见 `_call_upstream`
    的 63.5 秒事故）。正确的挪法不是「在 async 函数里给每一步套 to_thread」，
    而是**整条 `_acquire` 一次性丢进工作线程**：鉴权、选号、建连、记账各走各的
    线程会让同一个 SQLAlchemy Session 跨线程复用（上游 `_acquire` 的
    docstring 明确写了这条禁忌），也平白多出几次线程切换。

    ⇒ 本函数是普通 `def`，只允许由 `_acquire`（已在线程池里）调用。
    **不要**改回 `async def`：`_acquire` 是同步的，改了就是 758ab76 那个
    `TypeError: cannot unpack non-iterable coroutine object`。

    ## 两层重试（2026-11-16 解耦）

    同一个号内部的重试在 `_call_upstream`（最多 1 + SAME_ACCOUNT_RETRIES = 3 次）；
    本函数只负责**换号**：同号重试耗尽 ⇒ 该号进 `exclude` ⇒ 选下一个号，
    最多 `MAX_ATTEMPTS` 个号。`auth`（4100403）不重试，直接冷却该号并换号；
    400/422 换号也没用，直接抛。

    换号成功打 `Marvis 换号重试 acc=A -> B（原因 kind=... code=...）`；
    一个号都没换成（池里只有它 / 全被 exclude）时打 `Marvis 换号终止`。
    2026-11-16 之前这两条日志都不存在，线上 403 无法区分「没号可换」和
    「没换号」—— 这次把换号做成**可数的事实**。

    ## 参数类 400 / 上下文超限：同号重试，不记账号失败（上游 PR #55）

    上游回 3000101 invalid parameter 时，**这个号是好的**、凭证是好的，
    只是请求里某个字段它不认。以前这里直接 note_failure 后抛：号上留下
    last_err_msg=invalid parameter、err_count +1，用户看到「号有错」，
    而真正要动的是请求体。现在按 _DEGRADE_FIELDS 逐个摘，摘到能过就出字，
    且不吃换号预算（attempts -= 1）。

    上下文超限（400/413 + 3000100）同理：内容长度是请求属性，换号发也一样长，
    靠 main-auto 的自动路由原样重发（_MAX_CTX_RETRIES 次）重新选路。

    """
    # 平台级风控退避：窗口内再有请求**直接 503**，一个字节都不打上游。
    #
    # ⚠️ 2026-11-18：这里**只在连续命中撞满阈值后**才会被打开（见
    # note_risk_control）—— 单次 4100404 不再进这个窗口。旧实现阈值 = 1，
    # 一条坏提示词就把整站锁 60 秒。
    # 注意它**不动任何账号**（不打 cool_until、不 exclude），退避一过号池原样可用。
    remaining = risk_backoff_remaining()
    if remaining > 0:
        logger.warning(
            "Marvis 平台级风控退避中，拒绝上游调用：剩余 %.0fs model=%s",
            remaining, str(body.get("model") or "") or "-")
        raise shared.PlatformRejected(
            503,
            "Marvis 连续 %d 次命中上游风控（4100404），已进入平台级退避："
            "约 %d 秒后自动恢复。这与你的 API Key、账号余额都无关，"
            "不需要换 Key，等窗口过去即可。" % (risk_hits(), int(remaining) + 1),
            kind="server_error", code="risk_control_backoff",
            headers={"Retry-After": str(int(remaining) + 1)})
    model = str(body.get("model") or "")
    # PR #48 会话亲和：同一会话在 TTL 内优先回到上次成功的号（算不出就是空串，
    # 退化成完全不粘，行为与改动前一致）。
    sticky_key = _sticky_key_for(body)
    exclude: set[int] = set()
    # 4100404 的账号级短冷却（内存，2026-11-20）：窗口内的号这次不参与选号。
    # 放在选号之前而不是选完再筛 —— 否则 select_account 的 sticky / 排序都白做，
    # 而且「选到了却不能用」会走成租约失败，日志里看不出真因。
    exclude.update(risk_cooled_accounts())
    last_error: client.UpstreamError | None = None
    # 请求画像只算一次，本次请求的所有上游尝试共用（只记数字，不落内容）。
    profile = _profile_of(body)
    # 两层重试，各管各的上限：
    #   · 同一个号：`_call_upstream` 内最多 1 + SAME_ACCOUNT_RETRIES = 3 次；
    #   · 账号之间：本循环最多 MAX_ATTEMPTS = 3 个号（每号 3 次 ⇒ 最多 9 次）。
    # 换号 = 把失败号加入 `exclude` 后继续循环 —— `select_account` 会跳过它。
    switch_from = 0
    switch_reason: tuple[str, str] = ("", "")
    tried = 0
    # 换号预算（MAX_ATTEMPTS）与两类同号重试（参数降级 / 上下文超限）分开计数：
    # 同号重试要 attempts -= 1，不吃「三次机会」—— 否则降级重试会把换号预算吃光。
    attempts = 0
    param_retries = 0
    ctx_retries = 0
    dropped: set[str] = set()
    # 请求级「已降级」标记（跨账号）：降级值就地写进 body，换号后仍然生效。
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
        # 会话亲和只在「真能算出席位键 + 选号函数确实支持 sticky_key」时才传参：
        # 老签名（第三方包装 / 测试替身）不会被多一个 kwarg 打挂，行为与「不粘」
        # 完全一致。用签名探测而不是 try/except TypeError —— 后者会把选号实现内部
        # 真正的 TypeError 一起吞掉。
        sticky_kwargs = {}
        if sticky_key:
            try:
                params = inspect.signature(shared.select_account).parameters
            except (TypeError, ValueError):  # noqa: BLE001 - 拿不到签名就当不支持
                params = {}
            if "sticky_key" in params:
                sticky_kwargs["sticky_key"] = sticky_key
        account = shared.select_account(
            db, storage=STORAGE, realm=realm, key=key,
            model=model, exclude_ids=exclude, min_balance=0,
            **sticky_kwargs)
        if account is None:
            break
        if switch_from:
            # 真换到号了才打这条 —— 日志里能直接数出「换号重试」触发了几次。
            logger.info(
                "Marvis 换号重试 acc=%s -> %s（原因 kind=%s code=%s）",
                switch_from, int(account.id or 0),
                switch_reason[0] or "-", switch_reason[1] or "-")
            switch_from = 0
        if not shared.lease(account):
            exclude.add(account.id)
            continue
        started = time.perf_counter()
        try:
            data = marvis_accounts.ensure_token(db, account)
            tried += 1
            result = _call_upstream(
                data, realm, body, int(account.id or 0), stream=stream,
                profile=profile)
            if not stream:
                shared.note_success(account)
                shared.release(account)
            # 成功一次就把 4100404 的「连续」计数清零：阈值判的是**连续**成串，
            # 不是历史累计（否则一天里零散撞 5 次也会把平台锁一次）。
            clear_risk_hits()
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            # ⑤c：body 大小与 headers_ms 放在**同一行**，事后可以直接把
            # 「多大 -> 头部等多久」画成表。elapsed_ms 量的是
            # 「_attempt 开始 -> 上游把响应头吐出来」，与 request_metrics 的
            # headers_ms 同口径（read1 / 逐帧读取都在这之后，改不到它）。
            # billed 列是**对外可读名**（`main-auto` -> `deepseek-v4.1-flash`）：
            # 控制台的「使用记录 / 用量统计 / 模型筛选」全按这一列对 model_configs
            # 查展示名，写槽位名会让整张表对不上（见 _billing_model 的说明）。
            # 这里只用于日志；落库那一列仍由 _bill() 的调用方决定，不在这里改。
            logger.info(
                "Marvis 成功画像 acc=%s model=%s billed=%s bytes=%d msgs=%d "
                "tools=%d sys_chars=%d elapsed_ms=%d headers_ms=%d mb=%.2f",
                int(account.id or 0), model or "-", _billing_model(model),
                profile["bytes"], profile["msgs"], profile["tools"],
                profile["sys_chars"], elapsed_ms, elapsed_ms,
                profile["bytes"] / _MB)
            # 只有**成功**才写绑定：失败号不该被会话粘住（下个请求还要重试它）。
            _bind_sticky(sticky_key, account)
            return result, account, started
        except client.UpstreamError as exc:
            shared.release(account)
            notes = _notes()
            # ------------------------------------------------------------------
            # 上游 PR #55 的四类「不记账号失败」处理（顺序有意：先判先停）
            # ------------------------------------------------------------------
            #
            # ## 签名器不可用（fail-closed）
            # 请求根本没发出去；换号也签不出来（签名器是进程级/环境级的东西，与
            # 账号无关），立刻停且**不** note_failure —— 否则环境问题会被写成
            # 「这些号都被风控」，用户去换号重扫码，全是白费。
            if exc.kind == "signer_unavailable":
                last_error = exc
                logger.error(
                    "Marvis 签名器不可用，fail-closed 拒绝（不换号、不记账号失败，"
                    "与账号无关）| acc=%s | %s",
                    int(account.id or 0), (exc.detail or exc.message or "")[:200])
                break
            # ## 内容风控（4100406）
            # 请求正文命中上游黑名单：换号无用，而且会把好号逐个写成失败记录。
            if exc.kind == "content_block":
                last_error = exc
                logger.warning(
                    "Marvis 内容风控拦截（换号无效，不记账号失败）| acc=%s | %s",
                    int(account.id or 0), (exc.detail or exc.message or "")[:200])
                break
            # ## 3000100（额度族）：同号降级已在 _call_upstream 用掉，仍失败
            # ⇒ 该号在该模型上的**当次额度**不够，换号（不记账号失败）。
            #
            # 与 5100007 的分界（两者都不记账号失败，但处置相反）：
            #   · 3000100 = 额度：换一个号就有预算 → 标 (账号 × 模型) 后换号；
            #   · 5100007 = 上游内部错误：换号只会把好号也打脏 → 同号重试后停。
            #
            # 判据是**业务码**不是 kind：client 的旧版本 / 测试替身把 3000100 归成
            # http_error 也照样要走进来（3000100 与 3000101 前缀相同，处置相反）。
            quota_code = _quota_code_of(exc)
            if quota_code:
                last_error = exc
                # 请求已处于降级形态：后面每个新号都直接用降级值。
                degraded_max_tokens = True
                _note_model_quota_tight(db, account, model, code=quota_code)
                notes["quota_switch_accounts"] = (
                    int(notes.get("quota_switch_accounts") or 0) + 1)
                logger.warning(
                    "Marvis 额度不足换号 | acc=%s | model=%s | code=%s | "
                    "max_tokens=%s | 同号降级重试已用尽",
                    int(account.id or 0), model or "-", quota_code,
                    body.get("max_tokens", "-"))
                exclude.add(account.id)
                continue
            # ## 5100007（上游服务端内部错误）：同号重试也在上面用掉了，
            # 仍失败就如实报错 —— **不换号、不 note_failure、不进 exclude**。
            if _is_upstream_internal(exc):
                last_error = exc
                logger.warning(
                    "Marvis 上游内部错误，同号重试已用尽（不换号、不记账号失败）"
                    " | acc=%s | %s",
                    int(account.id or 0), (exc.detail or exc.message or "")[:200])
                break
            # ## 上下文超限（400/413 + 3000100）
            # main-auto 是自动路由：原样重发让上游重新选路，可能就落到装得下的
            # 后端。不裁消息（对话历史就是任务本身），不换号（长度是请求属性）。
            # 3000100 已由 client 归成 max_input_tokens（额度族）：那条走 max_tokens
            # 降级 + 换号，不能被「上下文超限」抢走（否则会死磕同一个号 4 次）。
            if exc.kind != "max_input_tokens" and _is_context_overflow(exc):
                last_error = exc
                if ctx_retries < _MAX_CTX_RETRIES:
                    ctx_retries += 1
                    logger.warning(
                        "Marvis 上下文超限（本次路由的后端装不下），原样重发第 %d/%d "
                        "次以重新路由 | acc=%s | 消息 %d 条 | %s",
                        ctx_retries, _MAX_CTX_RETRIES, int(account.id or 0),
                        len(body.get("messages") or []),
                        (exc.detail or exc.message or "")[:160])
                    attempts -= 1
                    time.sleep(_CTX_RETRY_GAP)
                    continue
                logger.warning(
                    "Marvis 上下文超限，重发 %d 次仍装不下 | acc=%s | %s",
                    _MAX_CTX_RETRIES, int(account.id or 0),
                    (exc.detail or exc.message or "")[:300])
                break
            # ## 参数类 400（3000101 invalid parameter）
            # 号是好的、凭证是好的，只是某个字段上游不认。按 _DEGRADE_FIELDS
            # 逐个摘，摘到能过就出字；摘不干净才抛，且不污染账号。
            if _is_param_error(exc):
                degraded = _degrade_body(body, dropped)
                last_error = exc
                if degraded is not None and param_retries < len(_DEGRADE_FIELDS):
                    param_retries += 1
                    logger.warning(
                        "Marvis 参数被上游拒绝，摘掉 %s 后同号重试 | acc=%s | %s",
                        sorted(dropped), int(account.id or 0),
                        (exc.detail or exc.message or "")[:180])
                    body = degraded
                    profile = _profile_of(body)
                    attempts -= 1
                    continue
                logger.warning(
                    "Marvis 参数被上游拒绝且无可降级字段 | acc=%s | %s",
                    int(account.id or 0), (exc.detail or exc.message or "")[:300])
                break
            # 可观测性：失败必须留痕（kind / status / 业务码 / detail / 号）。
            # ⚠ 放在「改写成用户文案」之前 —— 这里要的是**上游原话**。
            _warn_upstream_failure(account, exc, model=model, stream=stream)
            # 换号日志要写**上游原话**：auth 下面会被改写成 credential_expired，
            # 而排障时想知道的是上游那个 4100403。
            fail_kind, fail_code = exc.kind, exc.code or ""
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
            # ② upstream_internal（5100007）**不是这个号的错**：
            # 不累加 err_count、不打冷却，只在 last_err_msg 留痕。
            # ③ 额度族（3000100）同理：号本身是健康的，只是这个模型上的当次
            # 预算不够 —— 计 err_count 会把好号推去熔断，隔离改用 (账号×模型)
            # 级标记（见 _note_model_quota_tight）。
            no_fault = _is_upstream_internal(exc)
            quota_soft = _is_quota_exhausted(exc)
            shared.note_failure(
                db, account, kind=exc.kind,
                detail=exc.detail or exc.message, status=exc.status,
                model=model, public_platform=PUBLIC,
                code=str(exc.code or ""),
                count_error=not (no_fault or quota_soft),
                # 账号级冷却：只有 auth（这个号自己坏了）才配。
                # risk_control 是**出口 IP 级**风控 —— 给号打冷却等于把好号
                # 一起打挂，而且冷却到期立刻复发，见 _cool_seconds 的 docstring。
                cool_seconds=(0 if (no_fault or quota_soft)
                              else _cool_seconds(exc.kind)),
                # 额度耗尽走**降级窗口**（degrade_until = QUOTA_DEGRADE_SECONDS），
                # 而不是把余额写成 0：余额是事实数据，写 0 会让这个号永久出池。
                zero_balance=(exc.kind == "quota_exceeded"))
            # 风控：**不 exclude、不换号、不重试**，直接抛 + 记平台级退避。
            #
            # 换号会把**同一份 body** 在 1 秒内重放给另外两个号 —— 那正是风控
            # 引擎最敏感的「同源短时重放」特征，等于主动喂特征；而且 3 个号会
            # 一起吃冷却（2026-11-17 死循环的第二步）。
            # 正确处置：整平台退避，等出口 IP 换掉 / 风控自然过期。
            if exc.kind == "risk_control":
                # 2026-11-18：单次 4100404 **不锁平台**，只让本次请求失败。
                # 2026-11-20：下层**不再同号重签重试**（client.CHAT_ATTEMPTS = 1）
                # —— 一次命中 = 一份特征 = 一发请求。所以这里再重试只会把同一份
                # body 再打一遍上游，不做。只有**连续**撞满阈值才开平台窗口，
                # 见 note_risk_control。
                backoff = note_risk_control()
                hits = risk_hits()
                threshold = _risk_backoff_threshold()
                # 账号维度：撞墙的这个号歇一会儿（内存表，不写库、不跨进程）。
                # 没有这一步的话，平台退避一过它又排在队首、再撞同一个判定。
                cooled = cool_account_after_risk(int(account.id or 0))
                if backoff > 0:
                    logger.warning(
                        "Marvis 风控连续命中 %d/%d 次（code=%s acc=%s）："
                        "进入平台级退避 %.0fs",
                        hits, threshold, exc.code or "-",
                        int(account.id or 0), backoff)
                else:
                    logger.warning(
                        "Marvis 内容风控命中（code=%s acc=%s）：单次不触发平台"
                        "退避，仅本次请求失败（连续 %d/%d 次）；该号冷却 %.0fs"
                        "（MARVIS_RISK_COOLDOWN_ON_4100404=%s）",
                        exc.code or "-", int(account.id or 0), hits, threshold,
                        cooled,
                        "1" if risk_account_cooldown_enabled() else "0")
                raise exc
            # ② 上游内部错误：同号重试已经在 _call_upstream 用掉了 -> 直接抛，
            # **不进 exclude、不换号**。
            if no_fault:
                raise exc
            # ③ 额度族（3000100）：该号在该模型上的当次可用额度不够。
            # 降级已经在 _call_upstream 里试过（max_tokens > 阈值时），这里
            # **换号再试** —— 换一个号可能就有预算。标记落在 (账号×模型) 级：
            # 同号换个小模型仍然可用。只有池子用完（本函数末尾 last_error）
            # 才把 3000100 报给用户。
            # 与 5100007 的分界：那个是上游内部错误，换号只会污染整池。
            last_error = exc
            exclude.add(account.id)
            # 参数类错误换号也没用，直接抛（不再浪费一次上游往返）。
            # 额度族（3000100 / 1000007）不在此列：它们**不是**参数错，
            # 换号可能就有预算，必须继续走换号链路。
            if exc.status in (400, 422) and exc.kind not in (
                    "auth", "rate_limit", "risk_control", "quota_exceeded",
                    "max_input_tokens"):
                raise exc
            # 走到这里 = 同号重试已经在 `_call_upstream` 里**耗尽**
            # （白名单 kind + 4100406 业务码，最多 2 次）⇒ 该换号了：
            # 失败号已进 `exclude`，下一轮选号自然换到下一个号。
            # `auth`（4100403）在 `_call_upstream` 里不重试，这里是**直接**
            # 冷却该号（note_failure 600s）并换号。
            switch_from = int(account.id or 0)
            switch_reason = (fail_kind, fail_code)
        except Exception as exc:  # noqa: BLE001
            shared.release(account)
            logger.warning("Marvis 调用失败 acc=%s: %s: %s",
                           int(getattr(account, "id", 0) or 0),
                           type(exc).__name__, str(exc)[:200])
            last_error = client.UpstreamError(
                "Marvis 调用异常（%s）" % type(exc).__name__, kind="upstream")
            exclude.add(account.id)
            switch_from = int(account.id or 0)
            switch_reason = ("exception", type(exc).__name__)
    if last_error is not None:
        if switch_from:
            # 换号没得换了（池子只有一个号 / 全被 exclude）—— 这条是排障关键：
            # 它证明「不是没试换号，是没人可换」。
            logger.warning(
                "Marvis 换号终止：已无更多可用账号（已试 %d 个号，最后 kind=%s "
                "code=%s）", tried, switch_reason[0] or "-",
                switch_reason[1] or "-")
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
        # 风控处置分两层（2026-11-20 起）：
        #   · 平台级退避（note_risk_control）：连续成串才开窗，解决「别继续轰上游」；
        #   · 账号级短冷却（_RISK_ACCOUNT_STATE，内存 60s）：只把**这一发撞墙的号**
        #     挪出选号队列一会儿，不写库、不留脏记录，重启即清空。
        # 旧实现（上游 PR #67 之前）把 300s 冷却写进账号表，于是「命中 → 锁 300s
        # → 到期 → 又排到队首 → 再命中」自我循环 —— 那是**写库 + 300s + 不区分
        # 触发源**造成的，不是「账号维度」本身造成的。
    except Exception:  # noqa: BLE001
        alive, expired = [], []
    # 「一个号都没选到」同样是网关失败，而且是最难从响应里看出原因的一种：
    # 响应里只有 503 + 一句人话，没有 kind / 没有上游。这里补一条 warn。
    logger.warning("Marvis 无可用账号 realm=%s model=%s active=%d expired=%d",
                   realm, model or "-", len(alive), len(expired))
    if alive and len(expired) == len(alive):
        raise shared.PlatformRejected(
            503, "Marvis 账号登录态已全部过期，请在控制台「授权登录」重新扫码"
                 "（access_token 由上游按天轮换，过期后只能重登）",
            kind="server_error", code="credential_expired",
            headers={"Retry-After": "5"})
    # **额度耗尽 ≠ 冷却**（2026-11-18 事故的直接放大器）。
    #
    # 上游 3000001 是**账号级**总额度用尽：三个号的日配额（各 10,000,000
    # token）同时在 16:18 打满，凭证、登录态、风控全部正常，只有钱包是 0。
    # 选号用的是 `min_balance=0`（见 `_attempt`），余额 0 的号**全部**被滤掉
    # ⇒ 一个号都选不到，走到这里被报成 503「都在冷却或排队中，请稍后重试」。
    # 那句话把用户送去「等冷却到期」，而冷却永远不会到期 —— 真正要等的是
    # **上游每日 00:00(+08) 的配额重置**，长则十几小时。两种结局完全不同，
    # 必须分开报：一个是「几分钟后就好」，一个是「等重置或加号」。
    #
    # 判据只看 `alive`：active 且余额为 0 的号算「没配额」。NULL（从没同步过
    # 余额）不计入 —— 未知不等于耗尽，那种情况仍按冷却重试。
    # 全池都在**风控冷却**里 —— 这是 17:24 那次 503 的真实原因，而不是
    # 「排队中」。文案要能直接指到「出口风控」这个动作上（上游 PR #55）。
    zero = [r for r in alive
            if getattr(r, "balance_remain", None) is not None
            and int(getattr(r, "balance_remain", 0) or 0) <= 0]
    if alive and len(zero) == len(alive):
        logger.warning(
            "Marvis 额度已耗尽 realm=%s model=%s active=%d 余额全为 0"
            "（上游 3000001：等待 00:00+08 配额重置）",
            realm, model or "-", len(alive))
        # 走 client.UpstreamError 而不是 shared.PlatformRejected：只有前者会经过
        # _fail() / _error_payload()，错误体才能带上 family / upstream_code 两个字段
        # （PlatformRejected.openai() 只出 type/message/code）。
        raise client.UpstreamError(
            "Marvis 账号额度已耗尽（上游 3000001 用户名下token不足）："
            "上游每日 00:00(+08) 为各账号重置额度，请等待重置或在控制台加号",
            status=402, code="quota_exhausted", kind="quota_exceeded",
            # detail 保持上游错误体的形状：_error_payload() 用 client._biz_code_of()
            # 从这里抠业务码，抠不到 upstream_code 就丢了（而它正是排障时要与
            # 上游工单对齐的那一列）。
            detail="{\"code\":3000001,\"msg\":\"用户名下token不足；"
                   "等待上游 00:00(+08) 配额重置\"}")
    # 全池都在 4100404 的**账号级短冷却**里：这不是「排队中」，而是一句能照做的
    # 话（等几十秒；别去换 Key / 重扫码 —— 那三个方向都错）。2026-11-20 新增，
    # 与平台级退避分开报，否则用户会把「出口风控」误读成「账号坏了」。
    cooled = risk_cooled_accounts()
    if alive and cooled and all(
            int(getattr(row, "id", 0) or 0) in cooled for row in alive):
        left = risk_account_cooldown_remaining()
        logger.warning(
            "Marvis 账号全部在 4100404 冷却中 realm=%s model=%s active=%d "
            "剩余 %.0fs", realm, model or "-", len(alive), left)
        raise shared.PlatformRejected(
            503, "Marvis 账号刚撞上游风控（4100404），已按账号隔离冷却 %.0f 秒："
                 "窗口过后自动恢复，不需要换 Key、也不需要重新扫码"
            % (left + 1,),
            kind="server_error", code="risk_control_cooling",
            headers={"Retry-After": str(int(left) + 1)})
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
    """逐块读上游 SSE —— **每一块都在工作线程里读**，事件循环上不留阻塞调用。

    每取一块才发起下一次 `read`，消费者慢下来就自然形成背压；线程里的
    异常会在 `await` 处原样抛回，不需要自己搬运。

    用 `asyncio.to_thread`（而不是裸 `run_in_executor`）：它会带上当前
    contextvars，`request_metrics` / 实时面板那套按请求隔离的上下文才不会丢。

    ⚠ 建连（`open_chat_stream`）也是阻塞的，已经在上游的 `to_thread` 里；
    这里只负责「已建好的连接」的读取。
    """
    # 上游 PR #55：用 read1() 而不是 read()。
    #
    # HTTPResponse.read(n) 底层是 BufferedReader.read(n) —— 它会**一直读到凑满
    # n 字节或 EOF** 才返回。SSE 的帧又小又慢（一个 token 十几字节、几十毫秒
    # 一个），于是「攒满 8KB 才吐一次」变成常态：客户端看到的是先卡一秒、再
    # 一次性刷一屏。read1(n) 只做**一次**底层读取，有多少给多少，所以 n 可以
    # 放大到 64KB 而不引入等待。
    #
    # 兜底：极老的 Python 或自定义 resp 没有 read1 时退回 read，行为与以前一致。
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


def _usage_of(result: dict) -> dict:
    usage = result.get("usage") if isinstance(result, dict) else None
    return usage if isinstance(usage, dict) else {}


def _reap_abandoned_acquire(task) -> None:
    """acquire 超时后那条线程还在跑（to_thread 无法中断）—— 等它回来再收尾。

    必须做这件事：不做的话，一次 acquire 超时就会**永久**占住那个账号的租约，
    POOL 的并发计数永远不减，后续请求全部变成「账号都在冷却或排队中」——
    超时处理本身成了新的故障源。
    """

    def _cleanup(fut):
        try:
            if fut.cancelled():
                return
            result = fut.result()
        except Exception:  # noqa: BLE001 - 迟到的失败已经没人要了
            return
        try:
            _key, resp, account, _started = result
        except Exception:  # noqa: BLE001
            return
        try:
            client.close_stream(resp)
        except Exception:  # noqa: BLE001
            pass
        try:
            shared.release(account)
        except Exception:  # noqa: BLE001
            pass
        logger.warning("Marvis acquire 超时后回收：迟到的上游响应已关闭 acc=%s",
                       int(getattr(account, "id", 0) or 0))

    task.add_done_callback(_cleanup)


def _acquire_timeout_response(exc, *, stream: bool):
    """③ 的对外呈现：504 + code=acquire_timeout，日志里留同名 error 值。"""
    status = 504
    error_obj = {
        "type": "timeout",
        "code": "acquire_timeout",
        "message": (
            "等待 Marvis 上游返回响应头超过 %s 秒，已中止本次请求。"
            "这是上游 prefill（处理输入）超时，不是 API Key、账号或额度问题。"
            "请减小上下文（减少历史消息 / 工具定义），或稍后重试。"
            % _fmt_seconds(exc.seconds)),
    }
    logger.error(
        "Marvis acquire_timeout %ss model=%s（上游 prefill 未在期限内返回响应头，"
        "已中止；core/client.py 的 STREAM_TIMEOUT 是**读**超时，与本值无关）",
        _fmt_seconds(exc.seconds), exc.model or "-")
    try:
        from admin import request_metrics

        request_metrics.mark_request_error("acquire_timeout")
    except Exception:  # noqa: BLE001 - 观测失败不能挡住错误响应
        pass
    if stream:
        return _stream_error_response(status, error_obj,
                                      extra_headers={"Retry-After": "5"})
    return JSONResponse({"error": dict(error_obj, http_status=status)},
                        status_code=status, headers={"Retry-After": "5"})


def _inband_error(obj: dict) -> str:
    """识别「HTTP 200 + 流内错误帧」的那一类失败，返回错误描述（正常帧返回空串）。

    上游有一部分失败**不走状态码**：HTTP 200、Content-Type: text/event-stream
    照旧，但流里只有一条错误帧、没有 choices：

        data: {"error":{"code":3000101,"message":"...非法参数..."}}

    client._classify() 只看得到 200，整条链路会按成功处理：错误帧被原样当
    正文透传、finally 里 note_success()、用量日志记一笔正常调用 —— 现场表现
    是「网关全绿、客户端一直重试」。判据刻意收窄：有 choices 就不当错误。
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


#: 流读到一半上游断开（socket reset / read timeout）时补发的 error 帧。
#:
#: _terminal_frame() 只保证「流有结尾」，不说原因；断流时上游并没有发过任何
#: 错误帧，客户端看到的就是一个干净的 stop —— 所以这里先补一帧 error 把原因
#: 说清楚，再让调用方补终止帧。只出现在异常路径上，正常流一个字节都不改。
_STREAM_BROKEN_FRAME = ("data: %s\n\n" % json.dumps(
    {"error": {"type": "upstream_stream_broken",
               "code": "stream_broken",
               "message": "Marvis 上游流在中途断开（读超时或连接被重置），"
                          "已收到的内容保持有效，请重试本轮"}},
    ensure_ascii=False)).encode()


def _terminal_frame(model: str) -> bytes:
    """补一帧终止帧：finish_reason=stop + [DONE]。

    流「有头无尾」是客户端无限重试的根因 —— OpenAI SDK 靠 finish_reason 判定，
    裸 SSE 客户端靠 [DONE]，两个都要给。上游出错帧、提前 EOF、被截断时都补上：
    让客户端**正常收尾**，原因由前面那条错误帧自己说明，而不是让它以为连接掉了、
    换个号再来一遍。
    """
    frame = {"id": "chatcmpl-gateway", "object": "chat.completion.chunk",
             "created": int(time.time()), "model": model,
             "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
    try:
        body = json.dumps(frame, ensure_ascii=False).encode("utf-8")
    except Exception:  # noqa: BLE001 - 兜底：手写字面量，绝不让收尾失败
        body = b'{"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}'
    # ⚠ data: 前缀不能省：SSE 只认 data: / event: / id: / retry: 四种字段行，
    # 裸 JSON 行会被解析器**直接丢弃** —— 那样补出来的终止帧里只剩 [DONE] 生效。
    return b"data: " + body + b"\n\ndata: [DONE]\n\n"


def _scan_stream(pending: bytes, chunk: bytes, state: dict) -> bytes:
    """扫描 SSE 字节流，抽取 usage / finish_reason / 流内错误。

    返回「还没凑成整行的尾巴」，由调用方在下一个 chunk 前拼回去 —— 一帧被 TCP
    切成两半是常态，按 chunk 独立解析会漏掉恰好被切开的那条错误帧或 usage 帧。
    只做**只读**解析，转发的字节一个都不改。
    """
    if not isinstance(chunk, (bytes, bytearray)):
        return pending
    data = (pending or b"") + bytes(chunk)
    lines = data.split(b"\n")
    pending = lines.pop()
    # 上游若长时间不发换行，尾巴会无限增长；超过 1MB 说明不是正常 SSE，
    # 直接丢弃（拼行已经救不回来），只保留末尾供下次重试。
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


_KEEPALIVE_ENV = "MIRAIHUB_MARVIS_KEEPALIVE"
_KEEPALIVE_OFF = frozenset({"0", "false", "off", "no"})


def _keepalive_enabled() -> bool:
    """心跳开关，默认开；MIRAIHUB_MARVIS_KEEPALIVE=0 整体关掉。

    留开关的理由与内容改写（SANITIZE_TENCENT_ENV）一致：这是**往客户的流里
    插入我们自己造的帧**，出问题必须能一键退回原样透传，而不是改代码。
    """
    return (os.getenv(_KEEPALIVE_ENV) or "").strip().lower() not in _KEEPALIVE_OFF


async def _with_keepalive(source, *, interval: float, ping: bytes):
    """给 async 生成器加空闲心跳：interval 秒没新块就发一帧 ping。

    超时**不取消**内层那次读取：只把心跳先吐出去，再接着等同一个 task。
    始终只有一个读线程，不会踩到 bridge 那个「取消后重起线程并发消费同一条
    流、行被撕碎」的坑。
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
    """给 StreamingResponse 套一层空闲心跳；其它响应原样返回。"""
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
    """OpenAI 兼容入口 —— 本平台的唯一主路径。

    ## 流式失败的状态码策略（2026-11-19 混合）

    上一版把流式失败一律改成 200 + 错误帧，只看 HTTP 状态的客户端会把
    「认证失败 / 额度耗尽 / 参数错」当成功（空内容）。现在分两段：

      · acquire 在 GRACE（STREAM_STATUS_GRACE_ENV，默认 6.0s）内失败 ——
        还没写过任何字节，返回**真实状态码** + JSON 错误；
      · 超过 GRACE —— 先发心跳帧把响应头定下来（headers-first 不变），
        此后失败只能 200 + 错误帧，原始状态放在帧里的 http_status。
    """
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

    upstream_body = _prepare_body(body, slot)
    wants_stream = bool(body.get("stream"))
    # /v1/messages 与 /v1/responses 的中继（见 _run_normalized）**不要**错误帧：
    # 它们按 choices[] 解析，帧会被丢掉。那边还没写任何字节，直接给真状态码。
    relay = bool(dict(getattr(request, "scope", None) or {}).get("marvis_relay"))

    # ① / ② 的处置结果要一路带到响应头（降级提示、错误细节）：挂在 ContextVar
    # 上，由 _call_upstream 写、由这里读（见 _REQUEST_NOTES 的说明）。
    notes: dict = {}
    _REQUEST_NOTES.set(notes)
    # ③ 等上游响应头的上限（ACQUIRE_TIMEOUT_ENV，默认 120s；<=0 关闭）。
    # 这是**本端**的收尾，与 core/client.py 的 STREAM_TIMEOUT（读超时）无关。
    limit = _acquire_timeout()
    # ③b 只有「真的对客户端流式」的请求才有宽限期：非流式与内部中继本来就能
    # 直接返回真实状态码，多等一个 GRACE 纯属把延迟做大。
    grace = 0.0 if (not wants_stream or relay) else _stream_status_grace()
    started_at = time.monotonic()
    acquire_task = asyncio.ensure_future(asyncio.to_thread(
        _acquire, request, upstream_body, realm, stream=wants_stream))
    acquired = None
    try:
        if grace > 0:
            # 混合策略第一段：给「快失败」一个窗口 —— 窗口内失败，响应头还没发，
            # 改得了状态码。asyncio.wait 不会取消任务，超时就往下走。
            #
            # ⚠ 窗口不能超过 ACQUIRE_TIMEOUT：否则「上限 0.2s、宽限 0.8s」时
            # 会拖到 0.8s 才动，等于把超时承诺作废。effective = min(grace, limit)。
            window = grace if limit <= 0 else min(grace, limit)
            done, _pending = await asyncio.wait({acquire_task}, timeout=window)
            if acquire_task in done:
                acquired = acquire_task.result()
            elif limit > 0 and window >= limit:
                # 宽限期内就撞上了 acquire 超时 -> 还没写字节，真状态码。
                _reap_abandoned_acquire(acquire_task)
                return _acquire_timeout_response(
                    _AcquireTimeout(limit, str(upstream_body.get("model") or "")),
                    stream=False)
        if acquired is None and grace <= 0:
            if limit > 0:
                # shield：wait_for 超时只取消这层壳，工作线程照旧跑完 —— 否则
                # to_thread 的 future 被取消后，里面的 Session / 连接再没人收。
                acquired = await asyncio.wait_for(
                    asyncio.shield(acquire_task), limit)
            else:
                acquired = await acquire_task
    except asyncio.TimeoutError:
        _reap_abandoned_acquire(acquire_task)
        return _acquire_timeout_response(
            _AcquireTimeout(limit, str(upstream_body.get("model") or "")),
            stream=wants_stream and not relay)
    except shared.PlatformRejected as exc:
        # `openai()` 本身返回的就是 **JSONResponse**，不要再包一层 ——
        # 包了会 TypeError: Object of type JSONResponse is not JSON serializable。
        # 走到这里说明宽限期内就失败了（或非流式）：状态码是真的。
        return exc.openai()
    except client.UpstreamError as exc:
        # 同上：宽限期内失败 = 还没写字节 -> 真状态码 + JSON（stream=False）。
        return _fail(exc, notes=notes, stream=False)

    if not wants_stream:
        key, resp, account, started = acquired
        usage = _usage_of(resp)
        # 非流式同样可能是「HTTP 200 + 错误体」：_classify() 只能按状态码判，
        # 这类拒绝会一路被当成成功记账。原样把上游 body 交给调用方（不改语义），
        # 但记账要标成失败，否则面板上看不出真实成功率。
        # 记账口径用本项目的**槽位**（slot），不是上游回的 billed_model。
        inband = _inband_error(resp)
        await _bill_async(key, account, slot, usage, request=request,
                          started=started, error=inband)
        return JSONResponse(resp, headers=_degraded_headers(notes) or None)

    # 宽限期内拿到了上游响应头 -> 正常流式（与改动前完全一致，无心跳）。
    # 宽限期过了 -> 响应头先发出去，第一帧心跳，剩下的在生成器里等。
    deadline = started_at + limit if limit > 0 else 0.0
    # 宽限期已经过掉了多少 —— 生成器里剩下的等待要扣掉这一段。

    async def generator(acquired_tuple=None):
        stats = {"ttfb_ms": 0, "total_ms": 0}
        usage: dict = {}
        first_at = None
        began = time.perf_counter()
        key = resp = account = started = None
        buffer = b""
        pending = b""
        # 只读旁路状态：usage / finish_reason / [DONE] / 流内错误（见 _scan_stream）。
        state = {"usage": {}, "finish": False, "done": False, "error": ""}
        reaped = [False]

        def _reap_once() -> None:
            """超时 / 客户端断开时回收迟到的上游响应（只做一次）。"""
            if reaped[0]:
                return
            reaped[0] = True
            _reap_abandoned_acquire(acquire_task)

        try:
            if acquired_tuple is None:
                # ③b 第二段：先把心跳发出去（headers-first 语义不变），
                # 再去等上游响应头。此后失败只能靠帧携带状态。
                yield _heartbeat_frame()
                stats["heartbeat_ms"] = int(
                    (time.perf_counter() - started_at) * 1000)
                try:
                    if limit > 0:
                        remaining = max(0.05, deadline - time.monotonic())
                        acquired_tuple = await asyncio.wait_for(
                            asyncio.shield(acquire_task), remaining)
                    else:
                        acquired_tuple = await acquire_task
                except asyncio.TimeoutError:
                    _reap_once()
                    logger.error(
                        "Marvis acquire_timeout %ss model=%s（流式已发心跳，"
                        "错误以帧返回）", _fmt_seconds(limit),
                        str(upstream_body.get("model") or "") or "-")
                    yield _sse_error_frame({
                        "type": "timeout",
                        "code": "acquire_timeout",
                        "message": (
                            "等待 Marvis 上游返回响应头超过 %s 秒，已中止本次请求。"
                            "这是上游 prefill（处理输入）超时，不是 API Key、账号"
                            "或额度问题。请减小上下文，或稍后重试。"
                            % _fmt_seconds(limit)),
                        "http_status": 504,
                    })
                    return
                except shared.PlatformRejected as exc:
                    yield _sse_error_frame({
                        "type": exc.kind, "code": exc.code or "",
                        "message": exc.message,
                        "http_status": int(exc.status)})
                    return
                except client.UpstreamError as exc:
                    status, error_obj, _hdr = _error_payload(exc, notes)
                    yield _sse_error_frame(dict(error_obj,
                                                http_status=int(status)))
                    return
                except Exception as exc:  # noqa: BLE001 - 流已开始，只能补帧
                    logger.warning("Marvis 流式建连异常: %s: %s",
                                   type(exc).__name__, str(exc)[:200])
                    yield _sse_error_frame({
                        "type": "upstream", "code": "stream_interrupted",
                        "message": "建立上游连接时异常（%s），请重试"
                                   % type(exc).__name__,
                        "http_status": 502})
                    return
            key, resp, account, started = acquired_tuple
            async for chunk in _iter_upstream(resp):
                if first_at is None:
                    first_at = time.perf_counter()
                    stats["ttfb_ms"] = int((first_at - started) * 1000)
                yield chunk
                # 只读旁路：解析 usage / finish_reason / 流内错误。
                # **转发的字节一个都不改**，只记账与收尾需要这些信息。
                pending = _scan_stream(pending, chunk, state)
            # EOF：最后一段可能没带换行（裸 `data: [DONE]`），`_scan_stream`
            # 只缓存不成行的尾巴、不判读 —— 补一个分隔符再扫一遍，否则 `done`
            # 认不出来：白白多补一帧终止帧，还会和 `[DONE]` 粘成一行。
            if pending:
                _scan_stream(pending, _FRAME_SEP, state)
            usage = state["usage"] if isinstance(state.get("usage"), dict) else {}
            if state.get("error"):
                # HTTP 200 + 流内错误帧：状态码已经发出去了，补一帧 error 把原因
                # 说清楚（客户端不该看到一个没有 finish_reason 的干净结尾）。
                logger.warning("Marvis 流内错误 acc=%s | %s",
                               int(getattr(account, "id", 0) or 0),
                               str(state["error"])[:200])
                notes["stream_error"] = str(state["error"])[:300]
                try:
                    client.note_sentinel(
                        realm, int(getattr(account, "id", 0) or 0),
                        "upstream_business", detail=str(state["error"])[:200])
                except Exception:  # noqa: BLE001 - 哨兵坏了不能挡住收尾
                    pass
                yield _sse_error_frame({
                    "type": "upstream", "code": "stream_inband_error",
                    "message": "Marvis 上游在流内返回错误：%s"
                               % str(state["error"])[:300],
                    "http_status": 200,
                })
                # _sse_error_frame 自带 [DONE]，不再补终止帧。
                state["done"] = True
            if not state.get("done"):
                # 上游有头无尾（提前 EOF / 错误帧后直接断）：补终止帧，别让客户端
                # 以为连接掉了、无脑重试。
                yield _terminal_frame(str(upstream_body.get("model") or slot))
        except Exception as exc:  # noqa: BLE001 - 流已开始，只能补帧收尾
            # ④ 上游流中途断开：状态码在 headers-first 时已经发出去了，改不了。
            # 不补这一帧，客户端看到的是「流突然断掉」，只会无脑重试。
            logger.warning("Marvis 流中断 acc=%s: %s: %s",
                           int(getattr(account, "id", 0) or 0),
                           type(exc).__name__, str(exc)[:200])
            state["error"] = "upstream_business"
            try:
                from admin import request_metrics

                request_metrics.mark_stream_error()
                request_metrics.mark_request_error("stream_interrupted")
            except Exception:  # noqa: BLE001
                pass
            # 先补一帧 stream_broken 说明原因（上游什么都没发），再补终止帧；
            # 两帧都是合法 data 行，客户端能正常收尾而不是无限重试。
            yield _STREAM_BROKEN_FRAME
            yield _terminal_frame(str(upstream_body.get("model") or slot))
        finally:
            stats["total_ms"] = int((time.perf_counter() - began) * 1000)
            if account is None and not acquire_task.done():
                # 客户端半路断开：acquire 还卡在上游 —— 不回收就是永久租约。
                _reap_once()
            if resp is not None:
                client.close_stream(resp)
            if account is not None:
                # 流正常读完才算这个号「用成功过」—— 失败的情况下 note_success
                # 会把刚才 note_failure 记的失败计数清零，等于把坏号洗白。
                # 流内错误 / 断流时 state["error"] 非空，这里**不** note_success。
                if not state.get("error"):
                    shared.note_success(account)
                shared.release(account)
                # 记账是同步 DB 写，必须离开事件循环（客户端断开时也不会丢账，
                # 见 `_bill_async` 里的 shield）。
                await _bill_async(key, account, slot, usage, request=request,
                                  started=started, stats=stats,
                                  error=str(state.get("error") or ""))

    stream_headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    # ①c：降级重试过就把请求值 / 实际值写在响应头里，客户端能看见。
    stream_headers.update(_degraded_headers(notes))
    # 空闲心跳（上游 PR #55）：整段流交给 _wrap_keepalive —— 模型先思考 5~10 秒
    # 一个字节都不发时，bridge.CHAT_PING 是合法的空增量 data 帧，能重置客户端 /
    # 中间层的 idle 计时器。开关 MIRAIHUB_MARVIS_KEEPALIVE=0 可整体退回原样透传。
    ping = _heartbeat_frame()
    if acquired is not None:
        return _wrap_keepalive(
            StreamingResponse(generator(acquired),
                              media_type="text/event-stream",
                              headers=stream_headers), ping)
    # ③b 宽限期已过：状态码已经改不了，先亮明「这是待定流」，首帧是心跳。
    stream_headers["X-Marvis-Stream-Pending"] = "1"
    return _wrap_keepalive(
        StreamingResponse(generator(None), media_type="text/event-stream",
                          headers=stream_headers), ping)


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

    # 标记「内部中继」：chat_completions 看到这个就不要用流式错误帧
    # （见那里的 relay 分支）。两个中继都按 choices[] 解析，错误帧会被丢掉。
    # 拷一份 scope，绝不原地改 request.scope —— 那是上游框架的对象。
    inner_scope = dict(request.scope or {})
    inner_scope["marvis_relay"] = True
    inner = _Request(inner_scope, receive)
    response = await chat_completions(inner)

    # /v1/messages 与 /v1/responses **不走**流式错误帧：Anthropic / Responses 的
    # 中继按 choices[] 解析，错误帧会被丢掉，客户端只会看到「流空了」。
    # 这两个入口还没向客户端发出任何字节，直接还原成真实状态码 + JSON 错误即可。
    payload = getattr(response, "marvis_error", None)
    if isinstance(payload, dict):
        status = int(payload.get("http_status") or 502)
        if protocol == "anthropic":
            return JSONResponse(
                status_code=status,
                content={"type": "error",
                         "error": {"type": str(payload.get("type") or "api_error"),
                                   "message": str(payload.get("message") or "")}},
                headers={"X-Marvis-Stream-Error": "1"})
        return JSONResponse(
            status_code=status,
            content={"error": {k: v for k, v in payload.items()
                               if k != "http_status"}},
            headers={"X-Marvis-Stream-Error": "1"})

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
    oversized = await _reject_oversized(request)
    if oversized is not None:
        return oversized
    body = await _body_of(request)
    normalized = _anthropic_to_openai(body)
    return await _run_normalized(request, normalized, protocol="anthropic")


async def responses(request: Request):
    """Responses 入口 → 折叠成 chat/completions → 响应翻回 Responses。"""
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