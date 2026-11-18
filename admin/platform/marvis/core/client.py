"""腾讯 Marvis HTTP 客户端。

## 四件事必须做对

1. 错误分类（`kind` 决定上层是冷却、禁用还是换号）
2. 网络异常上报哨兵（连续失败 = 平台不可用）
3. 不要静默吞异常（否则「查不到」和「没有」分不清）
4. **上游会话 id 必须稳定派生**：用 `session_id_for()`（内部走
   `pool.stable_session_id`），从下游真实会话标识按「账号 + 对话」算出，
   同一账号同一对话恒定。**绝不许 `uuid4()`** —— 每请求换 id 会让上游
   前缀缓存永远打不中，还会让一个号几分钟内冒出几十个会话（风控特征）。
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
import uuid

from . import endpoints

logger = logging.getLogger("admin.platform.marvis.client")
DEFAULT_TIMEOUT = 30.0

#: 进程级共享 opener 的惰性缓存（见 `_opener()`）。
_OPENER = None
#: 不跟随重定向的 opener 缓存（换登录态那次请求专用，见 `_no_redirect_opener()`）。
_NO_REDIRECT_OPENER = None

SENTINEL_KINDS = frozenset({"auth", "quota_exceeded", "network",
                             "signature_invalid", "upstream_business"})


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """不跟随 3xx —— 换登录态要看 302 的 `Set-Cookie`，跟过去等于把凭证扔掉。"""

    def redirect_request(self, *_args, **_kwargs):  # noqa: D401
        return None


def _opener():
    """进程级共享的 urllib opener（**别用裸 `urllib.request.urlopen`**）。

    ## 为什么（2026-11-16 全站无响应事故）

    `urlopen()` 用的是默认 opener，`HTTPSConnection(context=None)` 会为
    **每一条新连接**调 `ssl._create_default_https_context()` → 在 Windows 上
    重扫系统证书库（`ssl._load_windows_store_certs`）。实测单次 3~13 秒，
    而且每次连接都重来一遍 —— 期间单 worker 的事件循环完全停摆。

    `admin.upstream_http.urllib_opener()` 把共享 SSL 上下文（系统库 + certifi）
    建一次复用，并默认不走系统代理（加速器塞进注册表的代理会让 TLS 校验失败，
    见 issue #5）。与 Loomy 等平台同一条链路，不再各写各的。
    """
    global _OPENER
    if _OPENER is None:
        from admin.upstream_http import urllib_opener

        _OPENER = urllib_opener()
    return _OPENER


def _no_redirect_opener():
    """同 `_opener()`，但不跟随重定向（`exchange_code` 专用）。

    ## 为什么必须用「替换」而不是「追加」

    `urllib.request.build_opener()` 会自动装上默认的 `HTTPRedirectHandler`。
    之后再 `opener.add_handler(_NoRedirect())` 只是往链尾**追加**，
    默认处理器仍在前头、先命中，302 照样被跟随 —— 表现就是请求被带到
    落地页「登录成功」HTML，`Set-Cookie` 里的 token 丢掉，报
    「登录接口没有下发 accesstoken」（2026-11-16 真机逐层排查确认）。

    正确做法是把自定义 handler 作为 `build_opener()` 的**位置参数**传进去，
    urllib 才会按「子类替换父类」把默认的顶替掉。

    `admin.upstream_http.build_opener` 因此加了 `*extra_handlers` 形参；
    这里做一次兼容判断：老版本没这个形参时，退回直接用标准库构造
    （TLS 上下文仍从 `ssl_context()` 取，与其它请求一致）。
    """
    global _NO_REDIRECT_OPENER
    if _NO_REDIRECT_OPENER is None:
        import inspect

        from admin import upstream_http

        fn = upstream_http.build_opener
        try:
            accepts = "extra_handlers" in inspect.signature(fn).parameters
        except (TypeError, ValueError):
            accepts = False
        if accepts:
            _NO_REDIRECT_OPENER = fn(None, _NoRedirect())
        else:
            # 兜底：标准库构造，把 _NoRedirect 作为位置参数传入（关键所在）
            _NO_REDIRECT_OPENER = urllib.request.build_opener(
                urllib.request.HTTPSHandler(
                    context=upstream_http.ssl_context()),
                _NoRedirect())
    return _NO_REDIRECT_OPENER



class UpstreamError(Exception):
    def __init__(self, message: str, *, status: int = 0, code: str = "",
                 kind: str = "http_error", detail: str = ""):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.kind = kind
        self.detail = detail

    def retryable(self) -> bool:
        return self.status in (408, 429) or self.status >= 500

    def to_dict(self) -> dict:
        return {"message": self.message, "status": self.status,
                "code": self.code, "kind": self.kind, "detail": self.detail[:300]}


def note_sentinel(realm: str, account_id: int, kind: str, *,
                  code: str = "", status: int = 0, detail: str = "") -> None:
    """上报风控哨兵。**绝不抛异常**（哨兵坏了不能阻断转发）。"""
    if kind not in SENTINEL_KINDS:
        return
    try:
        from admin import sentinel

        sentinel.note_failure("marvis", int(account_id or 0), kind,
                              code=str(code or ""), status=int(status or 0),
                              detail=str(detail or "")[:200])
    except Exception:  # noqa: BLE001
        logger.debug("哨兵上报失败 kind=%s", kind, exc_info=True)


def session_id_for(account_id: int, body: dict, *, prefix: str = "",
                   size: int = 16) -> str:
    """**稳定派生**的上游会话 id —— 不要在这里用 `uuid4()`。

    规则见 `admin/pool.py::stable_session_id`：`sha256(平台 | 账号 | 会话键)`。
    会话键优先取客户端自己的会话标识，没有就取首条 user 文本的哈希 ——
    两种都是**确定性**的，所以同一段对话永远得到同一个 id。

    为什么必须这样：
      * 上游前缀缓存按会话算，id 每换一次就是一次全新对话（首字慢、费用高、
        面板命中率永远是 0）；
      * 一个号短时间内"新建"大量会话，是风控最敏感的形状。

    注意：`MsgId` / `TurnId` / `requestId` 是**另一回事**，它们必须每次唯一
    （上游拿它们去重，复用会导致少记账）。
    """
    try:
        from admin import pool

        return pool.stable_session_id("marvis", int(account_id or 0), body,
                                      prefix=prefix, size=size)
    except Exception:  # noqa: BLE001 - 派生失败退回空串，不能挡住调用
        logger.debug("session id 派生失败", exc_info=True)
        return ""


def conversation_id_for(account_id: int, body: dict) -> str:
    """`Marvis-Conversation-ID` 的取值：**稳定派生**，形状仍按 UUID。

    派生规则见 `session_id_for()`（`sha256(平台 | 账号 | 会话键)`），这里只
    多做一件事：把 32 位十六进制重新排成 `8-4-4-4-12` 的 UUID 形状。

    为什么还要管形状 —— 上游看到的 `Marvis-Conversation-ID` 一直是
    `str(uuid4())`（36 字符带连字符）。改成裸 hex 虽然同样是「不随机」，
    但等于换了一种从未在真机上验证过的 id 形状，没必要为一个纯外观问题
    去赌风控。形状保持不变、取值变成确定性，风险最小。

    ## 为什么必须改（对齐 INTEGRATION-CHECKLIST §八 / FIXED-BUGS 五·补47）

    改造前这条链路是：

        `open_chat_stream()` → `llm_headers(acct, model=...)`
            → `uskey_identity()` → `uskey_cache.get_or_create()`
                → `conversation_id or str(uuid4())`   ← 每请求一个新会话

    后果是双份的，而且都看不见：

      · 上游前缀缓存按会话算 —— 每条请求都是「全新对话」，系统提示、工具
        定义、历史消息全部重算，首字更慢、费用更高，控制台命中率永远 0；
      · 一个号在几分钟里「新建」几十个会话，是上游风控里最典型的脚本形状。

    `response_id` **不在这里派生**：它是每次上游调用的去重键，必须每次唯一
    （见 `INTEGRATION-CHECKLIST` 那张表 —— 会话级稳定、请求级唯一，别搞反）。
    """
    raw = session_id_for(account_id, body, size=32)
    if len(raw) < 32:
        return ""          # 派生失败 → 交给 `uskey_cache` 的老路径兜底
    return "%s-%s-%s-%s-%s" % (raw[0:8], raw[8:12], raw[12:16],
                               raw[16:20], raw[20:32])


def _request(url: str, *, method: str = "GET", body: dict | None = None,
             raw_body: str | None = None,
             headers: dict | None = None, timeout: float = DEFAULT_TIMEOUT,
             account_id: int = 0, realm: str = "") -> dict:
    """发一个 JSON 请求。

    `raw_body` 与 `body` 二选一 —— 需要签名的接口（`/v3/*`）必须用 `raw_body`：
    签名对象是**实际发出的那串字节**，先序列化再签名和先签名再序列化，
    只要有一个空格或键序不同，上游就判签名错误。
    """
    if raw_body is not None:
        data = raw_body.encode("utf-8")
    else:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=data, method=method.upper())
    for key, value in (headers or {}).items():
        req.add_header(str(key), str(value))
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with _opener().open(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", "replace")
            return json.loads(text) if text.strip() else {}
    except urllib.error.HTTPError as exc:
        payload = ""
        try:
            payload = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            pass
        kind = ("auth" if exc.code in (401, 403)
                else "rate_limit" if exc.code == 429
                else "upstream_error" if exc.code >= 500 else "http_error")
        note_sentinel(realm, account_id, kind, code=str(exc.code),
                      status=exc.code, detail=payload)
        raise UpstreamError("%s失败（HTTP %d）" % (method, exc.code),
                            status=exc.code, code=str(exc.code), kind=kind,
                            detail=payload) from exc
    except Exception as exc:  # noqa: BLE001
        note_sentinel(realm, account_id, "network", detail=str(exc)[:200])
        raise UpstreamError("网络失败", status=0, kind="network",
                            detail=str(exc)[:200]) from exc


# ---------------------------------------------------------------------------
# 具体接口（Marvis 上游）
# ---------------------------------------------------------------------------
#: 模型网关的 SSE 读超时：一次长回答可能要几分钟，不能按普通接口的 30s 算。
STREAM_TIMEOUT = 300.0


def _acct(credential: dict) -> dict:
    """把落库的凭证规整成 endpoints 期望的形状（snake_case）。"""
    data = credential or {}
    return {
        "openid": str(data.get("openid") or data.get("openId") or ""),
        "access_token": str(data.get("access_token") or data.get("accessToken") or ""),
        "guid": str(data.get("guid") or ""),
        "login_type": str(data.get("login_type") or data.get("loginType") or "1"),
        "client_version": str(data.get("client_version") or ""),
    }


def fetch_wallet(credential: dict, realm: str = "cn", *, account_id: int = 0) -> dict:
    """令牌钱包 —— 卡片上的额度来源。

    上游 `POST /v3/marvis_get_wallet`，返回 `{data: {total_tokens, used_tokens,
    frozen_tokens, avail_tokens, total_cost, status}}`。

    ⚠ 签名对象是**请求体原文**（这里是 `{}`），不是 URL。
    """
    acct = _acct(credential)
    body_text = "{}"
    data = _request(
        endpoints.wallet_url(realm),
        method="POST",
        raw_body=body_text,
        headers=endpoints.business_headers(acct, query=body_text, method="POST"),
        realm=realm, account_id=account_id)
    inner = data.get("data") if isinstance(data, dict) else None
    if not isinstance(inner, dict):
        # ⚠ **`data: null` 是登录态失效的信号，不是「业务失败」**
        # （2026-11-15 真机实测）：access_token 过期后，钱包接口仍然回
        # `{"code": 0, "msg": "success", "data": null}` —— HTTP 200、业务码 0，
        # 唯一异常是 data 为空。同一份凭证打模型网关会明确回
        # `4100403 登录态过期/未登录`，两条路径指同一件事。
        #
        # 以前这里按 `upstream_business` 归类，后果有二：
        #   · 账号不会被标成「凭证失效」，控制台只显示「钱包接口没有返回 data
        #     字段」——用户完全不知道要去重新扫码；
        #   · 网关到最后仍然报「没有可用账号，请先在控制台导入凭证」，
        #     而账号明明是 active、凭证也在，只是过期了。
        # 现在归成 `auth`：上层据此冷却 + 控制台显示「登录态已过期」。
        raise UpstreamError(
            "Marvis 登录态已过期（钱包接口无数据），请在控制台重新扫码登录",
            status=401, kind="auth",
            detail=json.dumps(data, ensure_ascii=False)[:200])
    return inner


def open_chat_stream(credential: dict, realm: str, body: dict, *,
                     account_id: int = 0):
    """打开模型网关的 SSE 流，**原样**返回 `HTTPResponse` 交给上层转发。

    ⚠ 不要在这里读流：读干了上层就没东西可转发（插件侧踩过的同一个坑）。
    上层负责 finally 调 `close_stream()`。
    """
    acct = _acct(credential)
    url = endpoints.chat_url(realm)
    data = json.dumps(body, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    # ⚠ 必须把 body 里的 model 带进签名头。
    #
    # `llm_headers()` 会把 model 写进 uskey 的 bus_info，服务端用 nonce 复核
    # 这个指纹。以前这里调成 `llm_headers(acct)`，model 取默认空串，而 body
    # 里发的是真实模型名 —— 两边对不上，上游直接判非法客户端回 4100404。
    #
    # 现场表现很像「出口被风控」：凭证钱包接口正常、换号无效、换 Key 无效，
    # 因为被拦的根本不是账号，是这条请求的身份自证。
    chat_model = str(body.get("model") or "")
    # 会话级 id：同账号 + 同对话恒定（`conversation_id_for`）；请求级 id：
    # 每次上游调用唯一（去重键，复用会少记账）。
    #
    # 两个值必须**一起**交给 `llm_headers()`：它内部只调一次 `uskey_identity()`
    # 并签出 uskey，让 bus_info 与这两个请求头取自同一份身份 —— 缺一个就会
    # 退回随机生成，uskey 与头对不上直接 403 4100404（见 2026-11-18 事故）。
    conversation_id = conversation_id_for(account_id, body)
    response_id = uuid.uuid4().hex
    try:
        headers = endpoints.llm_headers(acct, model=chat_model,
                                        conversation_id=conversation_id,
                                        response_id=response_id)
    except endpoints.SignerUnavailable as exc:
        # fail-closed：签名器不可用时**一个字节都不往上游发**。
        #
        # 这里必须转成 `UpstreamError` 而不是让异常裸奔：裸奔会被下面那个
        # 兜底 except 归成 `network`，上层于是「换号重试」—— 换号也签不出来，
        # 白白把号池里的号一个个 note_failure。归成 `signer_unavailable`
        # 之后 `_attempt()` 会直接 break、不记账号失败（见 gateway）。
        raise UpstreamError(
            "Marvis 客户端指纹签名不可用（beacon 未就绪），已按 fail-closed "
            "拒绝本次请求；这与 API Key、账号状态都无关",
            status=503, code="signer_unavailable", kind="signer_unavailable",
            detail=(exc.detail or str(exc))[:300]) from exc
    for key, value in headers.items():
        req.add_header(str(key), str(value))

    # 排障开关：设 MARVIS_DEBUG_HEADERS=1 时把这次请求的关键字段打出来。
    #
    # 为什么需要它：同一账号、同一 uskey、同一分钟，
    # 网关 200 而外部脚本 403 —— 只有比对「网关究竟发了什么」才能定因。
    # 只打 uskey 前 16 字符和长度，不打完整 token。
    if (os.getenv("MARVIS_DEBUG_HEADERS") or "").strip() in ("1", "true", "yes"):
        ext = headers.get("Ual-Access-MarvisExt") or ""
        try:
            parsed = json.loads(ext)
        except Exception:  # noqa: BLE001
            parsed = {}
        logger.info(
            "marvis 请求 | model=%r scene=%r agent=%r conv=%r rid=%r "
            "uskey=%s(%d) nonce=%s clientVer=%s ua=%r",
            chat_model,
            headers.get("Marvis-Scene"),
            headers.get("Marvis-AgentTag"),
            headers.get("Marvis-Conversation-ID"),
            headers.get("Marvis-Response-ID"),
            str(parsed.get("uskey", ""))[:16],
            len(str(parsed.get("uskey", ""))),
            str(parsed.get("nonce", ""))[:8],
            parsed.get("clientVersion"),
            headers.get("User-Agent"),
        )
    try:
        return _opener().open(req, timeout=STREAM_TIMEOUT)
    except urllib.error.HTTPError as exc:
        payload = ""
        try:
            payload = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            pass
        kind = _classify(exc.code, payload)
        note_sentinel(realm, account_id, kind, code=str(exc.code),
                      status=exc.code, detail=payload)
        raise UpstreamError("模型网关失败（HTTP %d）" % exc.code,
                            status=exc.code, code=str(exc.code), kind=kind,
                            detail=payload) from exc
    except Exception as exc:  # noqa: BLE001
        note_sentinel(realm, account_id, "network", detail=str(exc)[:200])
        raise UpstreamError("模型网关网络失败", status=0, kind="network",
                            detail=str(exc)[:200]) from exc


def close_stream(resp) -> None:
    """关掉 SSE 连接。**绝不抛**（已经在 finally 里）。"""
    try:
        if resp is not None:
            resp.close()
    except Exception:  # noqa: BLE001
        pass


def _classify(status: int, text: str) -> str:
    """把上游错误归成一类 —— 上层据此决定冷却 / 禁用 / 换号。

    ## 三个 403 必须分开（2026-11-17 实测）

        {"code":4100404,...}  设备 / IP 被风控  → risk_control
        {"code":4100406,"msg":"非法请求"}  提示词内容命中黑名单 → content_block
        其余 403              签名或参数问题   → signature_invalid

    `4100406` 以前掉进 `signature_invalid`，后果是**换号语义完全错**：
    上层对非 risk/auth 的 403 会继续换号（`_attempt` 里不 break），
    于是同一条被拦的提示词把三个好号依次撞一遍、每个号 `note_failure`
    记一次失败 —— 用户看到的是「号全坏了」，而号一个都没坏，
    真正要改的是这次请求的提示词。

    内容风控是**请求侧**的问题：换号、冷却、等保活全都没用，只能改内容。
    """
    blob = (text or "").lower()
    # 内容风控：`4100406 非法请求`。必须排在 `risk`/`风控` 判断之前 ——
    # 上游文案里也可能出现「风控」字样，先判具体业务码才不会被抢走。
    if "4100406" in blob or "非法请求" in blob:
        return "content_block"
    if "4100404" in blob or "risk" in blob or "风控" in blob:
        return "risk_control"
    if status == 401:
        return "auth"
    if status == 403:
        # 403 但没有风控码 —— 签名或参数问题
        return "signature_invalid"
    if status == 429:
        return "rate_limit"
    if status >= 500:
        return "upstream_error"
    return "http_error"


def chat_completions(credential: dict, realm: str, body: dict, *,
                     account_id: int = 0) -> dict:
    """非流式对话（上层通常走流式；这里给健康检查与单发用）。"""
    stream = open_chat_stream(credential, realm, body, account_id=account_id)
    try:
        text = stream.read().decode("utf-8", "replace")
        return json.loads(text) if text.strip() else {}
    finally:
        close_stream(stream)


def list_models(_credential: dict | None = None, realm: str = "") -> list[dict]:
    """模型目录。

    Marvis 的模型网关**没有** `/v1/models`（实测 404/无响应），目录是客户端
    内置的槽位表 —— 所以这里返回静态目录，不猜上游。
    """
    out = []
    for item in endpoints.models_catalog(realm):
        out.append({
            "id": item["name"],
            "object": "model",
            "created": 0,
            "owned_by": "marvis",
            "name": item["name"],
            "api_id": item["api_id"],
            "context": item["context"],
        })
    return out


#: 续签失败时「登录态真的死了」的业务码。
#:
#: · `-107 RC_NO_AUTH_ERR` —— 凭证无效；
#: · `-101` —— 未授权（同类）；
#: · `-114` —— 上游把 OAuth 的报错原样回给你，实测形状是
#:   `code:-114, msg:callback({"error":100036,"error_description":"invalid refresh token"})`。
#:   参数形状对、只是这张 refresh_token 已经不能用了（被客户端轮换掉、或过期）。
#:
#: 除了这几个，其余（`-109` 参数、`-7` 签名、`-5` 缺头）都是**本端**的问题 ——
#: 把它们也当成 auth 失败会把好账号标成「需要重新登录」，比不续签更糟。
FATAL_REFRESH_CODES = frozenset({-107, -101, -114})

#: `msg` 里出现这些片段同样判死（上游有时把 OAuth 原文塞进 msg 而不改 code）。
FATAL_REFRESH_HINTS = ("invalid refresh token", "refresh token expired",
                       "refresh_token invalid")


def _expires_in_seconds(info: dict) -> int:
    """取续签下发的有效期秒数 —— **数字与数字字符串都收**。

    上游给的是字符串（微信实测 `"7200"`），只认 number 会让 `expires_at`
    恒为 0，下一次就又要走「按推断 TTL 算」那条腿，面板上的到期时间永远是猜的。
    """
    for key in ("expires_in", "expiresIn", "access_token_expire_time"):
        raw = info.get(key)
        if raw in (None, ""):
            continue
        try:
            seconds = int(float(raw))
        except (TypeError, ValueError):
            continue
        if seconds > 1e9:      # 已经是绝对时间戳，交给 expires_at 那条腿
            return 0
        if seconds > 0:
            return seconds
    return 0


def refresh_credentials(refresh_token: str = "", realm: str = "cn", *,
                        account_id: int = 0,
                        credential: dict | None = None) -> dict:
    """续签登录态 —— `POST /marvis_client_login/marvis_refresh_token`。

    ## 请求形状（这一层嵌套就是全部关键，见 `endpoints.refresh_body`）

        {"userInfo":{"openId":"…","refreshToken":"…","accessToken":"…",
                     "loginType":"Marvis"}}

    签名口径与 `/v3/*` 相同（签名对象是 **body 原文**），`Ual-Access-Guid` 必带。
    上游读的是 `body.userInfo.{openId,refreshToken,accessToken}`：少一层嵌套、
    字段写成 `openid`、或把 `loginType` 填成登录方式码（"1"/"2"），一律回
    `-109 RefreshToken missing required param (openid, guid, token)` —— 所以
    从错误文案反推参数位置永远推不出来（issue #41 的穷举都卡在这一步）。

    ## 响应语义

        {"code":0,"msg":"RC_SUCCESS",
         "user_info":{"access_token":"…","refresh_token":"…","expires_in":"7200"}}

      · `access_token` **不变** —— 续签只重置有效期，不要指望换出新票面；
      · `refresh_token` 可能回**空串**（微信实测如此）：空串 = 这次没给新值，
        必须回落旧值，否则下一次续签直接没票可用；
      · `expires_in` 是**字符串**，见 `_expires_in_seconds()`；
      · `-107 / -101` 才是登录态真死，`-109` 一类是本端问题（`FATAL_REFRESH_CODES`）。
    """
    data = dict(credential or {})
    if refresh_token and not data.get("refresh_token"):
        data["refresh_token"] = refresh_token

    openid = str(data.get("openid") or data.get("openId") or "").strip()
    guid = str(data.get("guid") or "").strip()
    token = str(data.get("access_token") or data.get("accessToken") or "").strip()
    refresh = str(data.get("refresh_token") or data.get("refreshToken") or "").strip()
    if not (openid and token and refresh):
        raise UpstreamError(
            "续签需要 openid + access_token + refresh_token，缺一换不出来："
            "请在控制台「授权登录」重新扫码（QQ / 微信）",
            status=401, code="credential_incomplete", kind="auth")

    acct = {"openid": openid, "access_token": token, "refresh_token": refresh,
            "guid": guid,
            "login_type": str(data.get("login_type") or data.get("loginType") or "1")}
    # 签名对象是**实际发出的那串字节**：先序列化，再拿同一串去做签名头。
    body = endpoints.refresh_body(acct)
    headers = endpoints.business_headers(acct, query=body, method="POST")
    # business_headers 已含该头；显式再写一遍是为了让「guid 到底带没带」这件事
    # 在代码里看得见（issue #41 曾把它当成 -109 的根因，值相同不影响签名）。
    headers["Ual-Access-Guid"] = guid

    payload = _request(endpoints.refresh_url(realm), method="POST",
                       raw_body=body, headers=headers, realm=realm,
                       account_id=account_id)

    code = None
    inner = None
    if isinstance(payload, dict):
        code = payload.get("code")
        inner = payload.get("user_info") or payload.get("userInfo")

    if code in (0, "0") and isinstance(inner, dict):
        new_access = str(inner.get("access_token") or inner.get("accesstoken")
                         or inner.get("accessToken") or token)
        # 空串回落旧值 —— 「这次没给」不等于「清空」
        new_refresh = str(inner.get("refresh_token") or inner.get("refreshtoken")
                          or inner.get("refreshToken") or "") or refresh
        seconds = _expires_in_seconds(inner)
        return {
            "access_token": new_access,
            "refresh_token": new_refresh,
            "expires_in": seconds,
            "expires_at": int(time.time()) + seconds if seconds else 0,
            "login_type": acct["login_type"],
            "openid": str(inner.get("openid") or inner.get("openId") or openid),
            "nickname": str(inner.get("nick_name") or inner.get("nickname") or ""),
            "avatar": str(inner.get("head_img_url") or ""),
            "raw": payload,
        }

    msg = str((payload or {}).get("msg") or "") if isinstance(payload, dict) else ""
    blob = msg.lower()
    fatal = (code in FATAL_REFRESH_CODES
             or any(hint in blob for hint in FATAL_REFRESH_HINTS))
    raise UpstreamError(
        "Marvis 续签被上游拒绝（code=%s %s）%s"
        % (code, msg,
           "：这张 refresh_token 已经不能用了，请重新扫码登录（QQ / 微信）"
           if fatal else "——这是本端参数/签名问题，账号本身没坏"),
        status=401, code=str(code or ""),
        kind=("auth" if fatal else "refresh_rejected"),
        detail=json.dumps(payload, ensure_ascii=False)[:300])


def exchange_code(code: str, login_type: str, realm: str = "cn", *,
                  state: str = "", guid: str = "") -> dict:
    """用授权码换登录态。

    ⚠ 三个必须做对的细节（插件侧实测）：
      · `query` 里**只能有** `login_type` / `code` / `state`，多一个参数就 -109；
      · 成功形态是 **302 + Set-Cookie**，跟随重定向等于把登录态扔掉；
      · 请求必须带 `Ual-Access-Guid`，不带一律 -109。
    """
    if not code:
        raise UpstreamError("没有授权码", kind="auth")
    if not guid:
        raise UpstreamError("缺少设备 guid（不带它上游一律 -109）", kind="auth")

    from urllib.parse import urlencode

    params = {"login_type": login_type, "code": code}
    if state:
        params["state"] = str(state)
    url = endpoints.oauth_url(realm) + "?" + urlencode(params)

    req = urllib.request.Request(url, method="GET")
    req.add_header("Accept",
                   "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8")
    req.add_header("User-Agent", endpoints.BROWSER_UA)
    req.add_header("Ual-Access-Guid", str(guid))
    try:
        resp = _no_redirect_opener().open(req, timeout=DEFAULT_TIMEOUT)
    except urllib.error.HTTPError as exc:
        # 302 在 urllib 里会以 HTTPError 抛出 —— 这里正是要它的 Set-Cookie
        resp = exc
    except Exception as exc:  # noqa: BLE001
        raise UpstreamError("换取登录态失败", kind="network",
                            detail=str(exc)[:200]) from exc

    cookies = _cookies_of(resp)
    body_text = ""
    # **302 也要尽量读一遍 body**（2026-11-16）。
    #
    # 原来写成 `if status != 302: read()`，理由是「302 没有 body」。但上游
    # 有两种失败形态会夹在 302 里一起回来（带 JSON 错误码），不读就只剩
    # 一个空 info → biz_code=0 → 直接掉到「没有下发 accesstoken」这个
    # 什么也没说清楚的兜底分支。
    #
    # 读不到/不是 JSON 无害（下面 `json.loads` 失败就退化成空 info），
    # 所以这里不设条件、统一尝试。
    try:
        body_text = resp.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - 302 的 body 可能不可读
        body_text = ""

    info = {}
    if body_text.strip():
        try:
            payload = json.loads(body_text)
        except Exception:  # noqa: BLE001
            payload = {}
        info = payload.get("user_info") or payload.get("data") or payload or {}

    if not isinstance(info, dict):
        info = {}

    # ---- 上游错误码 → 人话（2026-11-15 真机逐条对照出来的）----
    #
    #   -109 RC_PARAMS_INVALID : **缺 `Ual-Access-Guid` 请求头**（或缺
    #       login_type / code）。浏览器直接打开 `marvis_oauth?code=…` 时
    #       就是这个 —— 浏览器没有 guid。**这一步不会消耗 code**（参数校验在
    #       兑换之前），所以「浏览器显示 -109」是正常噪音，不影响我们兑换。
    #   -107 RC_NO_AUTH_ERR   : 参数齐全但 code 无效 —— 已过期或已被用过。
    #       QQ/微信的 code 只有几分钟寿命，必须马上提交。
    #
    # 这两条以前都被压成一句「没有下发 accesstoken」，用户完全不知道该干嘛。
    biz_code = 0
    if isinstance(info, dict) and info.get("code") is not None:
        try:
            biz_code = int(info.get("code"))
        except (TypeError, ValueError):
            biz_code = 0
    if biz_code == -109:
        raise UpstreamError(
            "上游判参数无效（-109）：这是浏览器直接打开回调页时的正常提示，"
            "不影响提交；请确认你贴的是完整回调地址（含 code=…），并在授权后 1 分钟内提交。",
            status=400, code="-109", kind="auth",
            detail=body_text[:200])
    if biz_code == -107:
        raise UpstreamError(
            "授权码已过期或已被使用（-107）：QQ / 微信的 code 是一次性的、"
            "只有几分钟有效期。常见原因是**这次授权其实已经成功过**，或者"
            "你把同一条链接提交/打开过两次。请重新扫码，拿到回调后在 1 分钟内"
            "提交一次；如果账号列表里已经多了这个号，说明上一次已经入库了。",
            status=400, code="-107", kind="auth",
            detail=body_text[:200])
    if biz_code not in (0, 200):
        raise UpstreamError(
            "上游返回错误（%s）：%s" % (biz_code,
                                   str(info.get("msg") or info.get("message") or "")[:120]),
            status=400, code=str(biz_code), kind="auth", detail=body_text[:200])

    # ⚠ 字段名是**小写无下划线**：上游实测返回
    #   `{"openid": "...", "accesstoken": "mv_...", "refreshtoken": "...",
    #     "expires_in": "5184000", "appid": "1903794758"}`
    #   只认 `access_token` 会把一次成功的登录判成失败（凭证其实已经发下来了）。
    access = str(info.get("accesstoken") or info.get("access_token")
                 or info.get("accessToken") or cookies.get("accesstoken")
                 or cookies.get("access_token") or "")
    openid = str(info.get("openid") or info.get("open_id")
                 or info.get("openId") or cookies.get("openid") or "")
    refresh = str(info.get("refreshtoken") or info.get("refresh_token")
                  or info.get("refreshToken") or cookies.get("refreshtoken")
                  or cookies.get("refresh_token") or "")
    if not access or not openid:
        # 到这一步说明：既没有业务错误码，也没拿到 token。最可能是上游换了
        # 响应形状，或者 302 的 Set-Cookie 没被读到。把「看到了什么」原样
        # 带出来，否则用户只看到一句「没有下发」，排查无从下手。
        seen = (body_text or "").strip()[:160] or "（空响应体）"
        raise UpstreamError(
            "登录接口没有下发 accesstoken / openid。"
            "这通常是授权码已被使用过（一次性的），或上游响应形状变了。"
            "请重新扫码再试一次；仍然不行请把下面这段反馈：%s" % seen,
            kind="auth",
            detail=(body_text or str(cookies))[:200])

    # `expires_in` 是**相对秒数**（QQ 给 5184000 = 60 天）。直接当绝对时间戳用
    # 会得到一个 1970 年的过期时间，下次选号立刻判死。
    expires_at = 0
    raw_expires = info.get("expires_in") or info.get("expiresIn")
    try:
        if raw_expires is not None:
            seconds = float(raw_expires)
            # 大于 1e9 的视为已经是绝对时间戳（秒），否则按相对秒数换算
            expires_at = int(seconds) if seconds > 1e9 else int(time.time() + seconds)
    except (TypeError, ValueError):
        expires_at = 0
    if not expires_at:
        raw_at = info.get("expires_at") or info.get("expiresAt")
        try:
            expires_at = int(float(raw_at)) if raw_at else 0
        except (TypeError, ValueError):
            expires_at = 0

    return {
        "openid": openid,
        "access_token": access,
        "refresh_token": refresh,
        "nickname": str(info.get("nick_name") or info.get("nickname") or ""),
        "avatar": str(info.get("head_img_url") or ""),
        "login_type": endpoints.LOGIN_TYPE.get(
            str(info.get("login_type") or "").upper(), login_type),
        "guid": str(guid),
        "expires_at": expires_at,
        "appid": str(info.get("appid") or ""),
    }


def _cookies_of(resp) -> dict:
    """从响应头里抠出所有 Set-Cookie 的 k=v（不分域，Marvis 只在一个 host 上）。"""
    out: dict[str, str] = {}
    try:
        headers = getattr(resp, "headers", None)
        if headers is None:
            return out
        raw = headers.get_all("Set-Cookie") if hasattr(headers, "get_all") else None
        items = raw or ([headers.get("Set-Cookie")] if headers.get("Set-Cookie") else [])
    except Exception:  # noqa: BLE001
        return out
    for item in items:
        for piece in str(item).split(","):
            head = piece.split(";")[0].strip()
            if "=" not in head:
                continue
            key, _, value = head.partition("=")
            key = key.strip()
            if key:
                out[key] = value.strip()
    return out
