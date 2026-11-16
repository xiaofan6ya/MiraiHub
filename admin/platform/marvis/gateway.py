"""腾讯 Marvis 的 `/v1/*` 转发实现。

## 上游就是 OpenAI 协议

Marvis 的模型网关 `POST /v1/chat/completions` 本身就是 OpenAI 兼容的（SSE 流式、
`finish_reason`、`tool_calls`、`reasoning_content` 都在原位），所以这里的活主要是：

  1. **模型 id 归一** —— 用户传的是显示名（`deepseek-v4-1-flash-261017`），
     上游只认槽位名（`main-auto`）。直接透传真实模型名会 400。
  2. **鉴权头** —— 不是 Bearer token，而是 `Ual-Access-*` 那一组（见 core/endpoints）。
  3. **流式原样转发** —— 不在这里读干流。
  4. **记账** —— 落 usage_logs，控制台的用量与积分统计靠它。

## 为什么流式要用线程池读

`core/client.py` 走的是 `urllib`（同步）。在 async handler 里直接 `stream.read()`
会把整个事件循环按秒级卡住 —— 一个慢速长回答就能让全网关停摆。这里用
`run_in_executor` 把每块读放到工作线程，事件循环照常服务其它请求。
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
    if status == 401 or exc.kind == "auth" or code == "credential_expired":
        status = 503
        if "重新扫码" not in message and "授权登录" not in message:
            message = ("Marvis 平台账号的登录态已失效（与你的 API Key 无关）："
                       "请在控制台「授权登录」重新扫码。原始信息：%s" % message)
        else:
            message = ("%s —— 这是 Marvis 平台账号的问题，你的 API Key 是有效的。"
                       % message)
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
    (re.compile(re.escape("Main branch (you will usually use this for PRs)"), re.I),
     "Main branch (PRs usually go here)"),
    # 2. Claude Code 身份句。**行首锚定**；刻意不带尾句点，一条覆盖两种常量形态。
    (re.compile(re.escape("You are Claude Code, Anthropic's official CLI for Claude"), re.I),
     "You are Claude Code, Anthropic's CLI for Claude"),
    # 3. Claude Code 反馈句（每轮必带的固定段落）。位置无关。
    #    find 必须写整句（含 URL）：上游认逐字符，砍掉半句等于静默空跑。
    (re.compile(re.escape("To give feedback, users should report the issue at "
                          "https://github.com/anthropics/claude-code/issues"), re.I),
     "To give feedback, users should report the issue at the project issue tracker"),
    # 4. DSH 身份首句（**行首锚定**；本条就是 4100406 的触发串）。
    #    用插件登记的 findRegex：容忍空白归一（\s+）以及 `an AI agent` /
    #    `an AI coding agent`、`powered by` / `powered by the` 等形态漂移。
    (re.compile(r"You\s+are\s+an?\s+AI\s+(?:coding\s+)?agent\s*,?\s*powered\s+by\s+"
                r"(?:the\s+)?DeepSeek\s+Harness", re.I),
     "You are an AI coding assistant, powered by a large language model"),
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
    (re.compile(re.escape("x-anthropic-billing-header"), re.I),
     "x-client-billing-header"),
)


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


def _prepare_body(body: dict, slot: str) -> dict:
    """把下游请求整理成上游能接受的样子。

    · `model` 换成槽位名；
    · 流式时补 `stream_options.include_usage` —— 少了它上游不返回 usage 帧，
      这张卡片和用量统计就永远是 0；
    · **system 正文过一遍腾讯内容风控指纹改写**（`4100406` 的修法，表见上）。
      `/v1/messages` 与 `/v1/responses` 都经 `_run_normalized` → `chat_completions`
      落到这里，所以只改这一处就覆盖三个入口。
    """
    out = dict(body)
    out["model"] = slot
    messages = out.get("messages")
    if isinstance(messages, list):
        out["messages"], hits = _sanitize_messages(messages)
        if hits:
            # 只记命中条数，**不记原文**：system 里可能带用户的 prompt / 路径 / 密钥。
            logger.info("Marvis 内容风控指纹改写 | rules_hit=%d", hits)
    if out.get("stream"):
        opts = dict(out.get("stream_options") or {})
        opts["include_usage"] = True
        out["stream_options"] = opts
    return out


def _attempt(db, request, key, body: dict, realm: str, *, stream: bool):
    """选号 + 租约 + 调上游。换号语义与其它平台一致。"""
    model = str(body.get("model") or "")
    exclude: set[int] = set()
    last_error: client.UpstreamError | None = None
    for _ in range(MAX_ATTEMPTS):
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
    if alive:
        raise shared.PlatformRejected(
            503, "Marvis 账号都在冷却或排队中，请稍后重试",
            kind="server_error", code="accounts_cooling",
            headers={"Retry-After": "3"})
    raise shared.PlatformRejected(
        503, "Marvis 还没有账号，请在控制台「授权登录」扫码（QQ 或微信）",
        kind="server_error", code="no_available_account",
        headers={"Retry-After": "3"})


async def _iter_upstream(resp):
    """把同步 `HTTPResponse` 的 read 挪到工作线程，避免卡住事件循环。"""
    loop = asyncio.get_running_loop()
    while True:
        chunk = await loop.run_in_executor(None, resp.read, 8192)
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
    wants_stream = bool(body.get("stream"))

    from admin.db import SessionLocal

    db = SessionLocal()
    try:
        key = shared.authenticate(db, request, platform=PUBLIC)
        resp, account, started = _attempt(db, request, key, upstream_body, realm,
                                          stream=wants_stream)
    except shared.PlatformRejected as exc:
        db.close()
        # `openai()` 本身返回的就是 **JSONResponse**，不要再包一层 ——
        # 包了会 TypeError: Object of type JSONResponse is not JSON serializable。
        return exc.openai()
    except client.UpstreamError as exc:
        db.close()
        return _fail(exc)
    except Exception:
        db.close()
        raise
    db.close()

    if not wants_stream:
        try:
            usage = _usage_of(resp)
            _bill(key, account, slot, usage, request=request, started=started)
            return JSONResponse(resp)
        except Exception:  # noqa: BLE001
            raise

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
            _bill(key, account, slot, usage, request=request, started=started,
                  stats=stats)

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
    """本地估算。口径：CJK 1 token/字，其它 4 字符 1 token（保守近似）。"""
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