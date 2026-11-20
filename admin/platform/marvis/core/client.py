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

import contextlib
import contextvars
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request

from . import endpoints

logger = logging.getLogger("admin.platform.marvis.client")
DEFAULT_TIMEOUT = 30.0

#: 历史遗留：opener 现在由 `admin.upstream_http.platform_opener()` 按平台缓存
#: （见 `_opener()`），这个全局不再使用；保留是为了不破坏外部对它的引用。
_OPENER = None
#: 不跟随重定向的 opener 缓存（换登录态那次请求专用，见 `_no_redirect_opener()`）。
_NO_REDIRECT_OPENER = None

#: `note_sentinel()` 的 kind 白名单 —— 不在里面的失败**一个字节都不上报**。
#:
#: 2026-11-20 补齐三种「线上看得见才行」的失败（哨兵侧补可观测性，比恢复
#: 账号级 note_failure 轻：这三种要么根本不该写 err_count，要么已经当场 break）：
#:
#:   · `content_block` —— 200 + JSON 的 4100406（真内容拦截）。它是**唯一**
#:     不写账号 err_count 的失败（`_attempt` 里直接 break），不在白名单里
#:     就等于「拦了但线上无声」。
#:   · `upstream_internal`（5100007）—— 上游服务端故障，同样不写 err_count，
#:     以前只剩一条 WARNING 画像。
#:   · `max_input_tokens`（3000100）—— 额度族，隔离落在 (账号×模型) 级，
#:     账号侧不留痕。
#:
#: 三者的 severity 都不在 `sentinel.KIND_SEVERITY` 里 ⇒ 落 INFO
#: （`severity or SEVERITY_INFO`），**不参与熔断**（熔断只认 RISK）。
SENTINEL_KINDS = frozenset({"auth", "quota_exceeded", "network",
                             "signature_invalid", "upstream_business",
                             "content_block", "upstream_internal",
                             "max_input_tokens"})


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """不跟随 3xx —— 换登录态要看 302 的 `Set-Cookie`，跟过去等于把凭证扔掉。"""

    def redirect_request(self, *_args, **_kwargs):  # noqa: D401
        return None


# --- 按账号固定出口（egress） ---
#: 当前请求绑定的出口代理（contextvar）。空串 = 不指定出口，走平台级 opener
#: （直连，或 `MIRAIHUB_<PLATFORM>_PROXY` 配的出口）。
_EGRESS_VAR = contextvars.ContextVar("marvis_egress_proxy", default="")
#: 按代理 URL 缓存 opener：每次重建都要重建 TLS 上下文（见 `_opener()`）。
_EGRESS_OPENERS: dict = {}
_EGRESS_LOCK = threading.Lock()


def _egress_proxy(account_id: int) -> str:
    """当前账号的出口代理；池没配 / 模块缺失返回 ""（= 不指定出口）。"""
    try:
        from . import egress

        return egress.pick(account_id)
    except Exception as exc:  # noqa: BLE001
        # 出口池是可选加速项：它坏了不能连带把对话打死。
        logger.warning("出口池不可用（回退平台出口）：%s", exc)
        return ""


def _opener_for(proxy: str):
    """按代理 URL 取 opener；空串时与改动前行为一致（平台级 opener）。"""
    if not proxy:
        from admin.upstream_http import platform_opener

        return platform_opener(endpoints.PLATFORM)
    with _EGRESS_LOCK:
        opener = _EGRESS_OPENERS.get(proxy)
        if opener is None:
            from admin.upstream_http import build_opener

            opener = build_opener(None, proxy=proxy)
            _EGRESS_OPENERS[proxy] = opener
        return opener


@contextlib.contextmanager
def _egress_bound(proxy: str):
    """在 with 块内把 `_opener()` 绑到指定出口（空串 = 平台级 opener）。"""
    token = _EGRESS_VAR.set(str(proxy or ""))
    try:
        yield
    finally:
        _EGRESS_VAR.reset(token)


def reset_egress_openers() -> None:
    """丢掉按出口的 opener 缓存（测试用；轮换出口后想立刻重建也用它）。"""
    with _EGRESS_LOCK:
        _EGRESS_OPENERS.clear()


def _opener():
    """进程级共享的 urllib opener（**别用裸 `urllib.request.urlopen`**）。

    ## 为什么（2026-11-16 全站无响应事故）

    `urlopen()` 用的是默认 opener，`HTTPSConnection(context=None)` 会为
    **每一条新连接**调 `ssl._create_default_https_context()` → 在 Windows 上
    重扫系统证书库（`ssl._load_windows_store_certs`）。实测单次 3~13 秒，
    而且每次连接都重来一遍 —— 期间单 worker 的事件循环完全停摆。

    `admin.upstream_http.platform_opener()` 把共享 SSL 上下文（系统库 + certifi）
    建一次复用，并默认不走系统代理（加速器塞进注册表的代理会让 TLS 校验失败，
    见 issue #5）。与 Loomy 等平台同一条链路，不再各写各的。

    ## 2026-11-18：出口可按账号固定

    有出口池时（`core/egress.py`），本次请求会绑一个出口代理，这里返回
    **按代理 URL 缓存**的 opener；没绑（默认）时返回平台级 opener，与改动前
    完全一致。绑定用 contextvar，所以 SSE 流跨线程流转也不会串号。
    """
    return _opener_for(_EGRESS_VAR.get())


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
    from admin import upstream_http

    # 走**按平台**的 opener：代理与 TLS 信任库和普通请求完全一致；
    # `_NoRedirect` 作为**位置参数**传下去才会顶替默认的 HTTPRedirectHandler。
    opener_fn = getattr(upstream_http, "platform_opener", None)
    if opener_fn is not None:
        return opener_fn(endpoints.PLATFORM, _NoRedirect())

    # 兜底：老版 upstream_http（没有 platform_opener）时的降级路径。
    global _NO_REDIRECT_OPENER
    if _NO_REDIRECT_OPENER is None:
        import inspect

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


#: 风控码：`4100404 请求被风控拦截`。
#:
#: 2026-11-17 四发实测：手工拼装的 uskey（`magic + F(bus_info) + 随机尾`）
#: 100% 撞这个码；只有走完整三层流水线产出的 uskey（见 `uskey_local.py`）
#: 才 200。这说明 `uskey` 的尾块服务端会校验，不是装饰。
#:
#: 即便是好 uskey，**同一组 (cid/rid/nonce)** 也不该复用；但**重签重发同样
#: 有害**（2026-11-20）：撞 4100404 之后再打一发，等于 1 秒内把同一份 body 的
#: 第二份特征喂给风控引擎，而那一发必然还撞同一个判定 —— 18:19 的现场就是这样
#: 把 4100404 推成 4100403（登录态被打死，不可逆）的。现在一发即止（见
#: `_open_chat_stream_direct` 里 4100404 分支的注释）。
RISK_CONTROL_CODE = "4100404"

#: 模型网关每次调用最多试几次：**1 = 只发首发，不重试**（2026-11-20 起）。
#:
#: 这个常量只约束 4100404 那条路径：`_open_chat_stream_direct` 里只有它曾经
#: `continue` 重发，其它错误（network / timeout / 4100406 / 3000101 / 4100403）
#: 本来就是直接抛。所以降到 1 **不改变**任何其它错误码的重试次数 —— 同号重试
#: 的 2 次在 gateway `_call_upstream` 里，判据是
#: `RETRY_SAME_ACCOUNT_KINDS` / `RETRY_SAME_ACCOUNT_BIZ_CODES` 白名单，与这里无关。
CHAT_ATTEMPTS = 1


def _open_chat_stream_direct(credential: dict, realm: str, body: dict, *,
                            account_id: int = 0):
    """打开模型网关的 SSE 流，**原样**返回 `HTTPResponse` 交给上层转发。

    「不指定出口」的那一半实现：出口由 `open_chat_stream()` 绑好再进来，
    这里只管发请求。
    

    ⚠ 不要在这里读流：读干了上层就没东西可转发（插件侧踩过的同一个坑）。
    上层负责 finally 调 `close_stream()`。

    ⚠ 撞 `4100404` 时**不重试**（2026-11-20）：一发即止，原样抛给上层去做
    平台级退避 + 账号级冷却。整组重签重发只会把同一份 body 的第二份特征喂给
    风控引擎（见下方 risk 分支的注释）。
    """
    acct = _acct(credential)
    url = endpoints.chat_url(realm)
    data = json.dumps(body, ensure_ascii=False).encode()
    # bus_info 的 `model=` 段必须是**请求体原文**（不是显示名、不是别名），
    # 所以从这里透传下去，别让 endpoints 用自己的默认值。
    # 上游 8aba19a 口径：**只认请求体里的 model 原文**，缺省就是空串 ——
    # 不拿 endpoints.EXT_MODEL 去猜，否则 bus_info 的 model= 段与 body 不一致，
    # 服务端复核 nonce 时直接判非法客户端（4100404）。
    model = str(body.get("model") or "")
    for attempt in range(CHAT_ATTEMPTS):
        req = urllib.request.Request(url, data=data, method="POST")
        for key, value in endpoints.llm_headers(acct, model=model).items():
            req.add_header(str(key), str(value))
        try:
            resp = _opener().open(req, timeout=STREAM_TIMEOUT)
        except urllib.error.HTTPError as exc:
            payload = ""
            try:
                payload = exc.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                pass
            risk = _biz_code_of(payload) == RISK_CONTROL_CODE
            if risk:
                # **不再同号重签重试**（2026-11-20）。
                #
                # 旧行为（CHAT_ATTEMPTS = 2）：撞 4100404 之后立刻整组换新
                # cid/rid/nonce/uskey 再打一发。后果是 1 秒内把同一份 body 的
                # 第二份特征喂给风控引擎，而那一发必然还撞同一个判定；18:19 的
                # 现场就是这么把 4100404 推成 4100403（登录态被打死）的。
                # 一发的信息量已经够了：定性、平台退避、账号冷却都在上层做，
                # 这里只上报，不重发。
                try:
                    exc.close()   # 别把这一发的连接晾着（以前是重发前才关）
                except Exception:  # noqa: BLE001
                    pass
                logger.warning(
                    "Marvis 模型网关 4100404（account=%s），按 2026-11-20 口径"
                    "不再同号重签重试：原样抛给上层（平台退避 + 账号冷却）",
                    account_id)
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
        # 200 但回 application/json：不是 SSE，是上游业务错误体（见下）。
        return _reject_json_body(resp, body, realm, account_id)


def _classify_json_body(payload: str) -> str:
    """200 + application/json 的归类（上游 PR #55 / keepalive 测试口径）。

    ⚠ 与 _classify() 故意不同：HTTP 200 说明传输层正常，这里只按业务码定性。
    4100406 在 200+JSON 上是内容风控（content_block，不换号）；而在 403 上
    仍按 _classify() 归 upstream_business（同号可重试，见
    tests/test_marvis_platform.py 与 tests/test_marvis_risk_backoff.py）。
    """
    blob = str(payload or "")
    low = blob.lower()
    code = _biz_code_of(blob)
    if code == "4100406" or "4100406" in low or "非法请求" in blob:
        return "content_block"
    if code == "4100404" or "4100404" in low or "risk" in low or "风控" in blob:
        return "risk_control"
    if (code == "4100403" or "4100403" in low
            or "登录态过期" in blob or "未登录" in blob):
        return "auth"
    return "http_error"


def open_chat_stream(credential: dict, realm: str, body: dict, *,
                     account_id: int = 0):
    """按账号固定出口打开 SSE 流；**出口不可用时回退平台出口**。

    出口由 `core/egress.py::pick(account_id)` 决定 —— 同一账号恒定同一出口。
    池没配（默认）时 `pick()` 返回 ""，这里直接走
    `_open_chat_stream_direct()`，行为与改动前一致。

    ## 回退的边界（只在**传输层**失败时回退）

    `kind == "network"` 意味着请求没到达上游（连不上出口、TLS 断了、
    超时），这时换出口重发是安全的。`4xx/5xx` 与业务错误**已经到过上游**，
    重发就是二次记账 + 可能触发更重的风控 —— 那类错误原样抛给上层，走既有
    的换号 / 冷却路径。
    """
    proxy = _egress_proxy(account_id)
    if not proxy:
        return _open_chat_stream_direct(credential, realm, body,
                                        account_id=account_id)
    try:
        with _egress_bound(proxy):
            return _open_chat_stream_direct(credential, realm, body,
                                            account_id=account_id)
    except UpstreamError as exc:
        if exc.kind != "network":
            raise
        logger.warning("出口 %s 不可用，回退平台出口 | acc=%s err=%s",
                       proxy, account_id, exc.detail or exc.message)
    with _egress_bound(""):
        return _open_chat_stream_direct(credential, realm, body,
                                        account_id=account_id)


def _reject_json_body(resp, body: dict, realm: str, account_id: int):
    """流式请求拿到 `200 + application/json` 时，把它当**业务错误体**处理。

    ## 为什么必须有这一步（照 loomy 的同款事故补的，2026-11-18）

    loomy 那边 2026-11-13 线上「对话发不出去」的根因就是这个形态：账号被
    停用时不回 SSE，而是 `200 application/json` 回一坨业务错误体。客户端
    按 `data:` 行解析，这一整行 JSON 既不以 `data:` 开头也没有 `choices`，
    于是被**静默丢弃** —— 生成器直接结束，客户端只看到
    `Stream ended without finish_reason`，用户侧就是「发不出去」，面板上还
    记成一次成功（0 token）。

    Marvis 有一模一样的形态：`4100403 登录态过期` / `4100404 风控` /
    `4100406 内容风控` 这几个码在**模型网关**上通常回 4xx，但凭证刚过期、
    号刚被限流的那一下会以 200 回一个 JSON 体。此时 `_iter_upstream` 会把
    这个 JSON 原样当 SSE 转给客户端 —— `_scan_stream` 也认不出（它只看
    `data:` 行），客户端解析不出任何帧，也是空回复。

    识别出来之后走 `_attempt` 既有的「换号 / 冷却 / 文案」路径，用户能拿到
    一句能照做的话，而不是一条空流。

    ## 边界的保守程度（宁可不拦，不可误拦）

    只在**三个条件同时成立**时才拦：

      1. 请求体里 `stream` 为真（非流式请求回 JSON 是正常的，不能拦）；
      2. `content-type` 明确含 `application/json`（空 / `text/plain` /
         其它未知形态一律放行，交给上层按流读 —— 有些网关会混着发）；
      3. 读得出内容。

    命中后 `resp` 已经被读完并关闭，因此**必须抛**；不能让调用方再读它。
    """
    if not isinstance(body, dict) or not body.get("stream"):
        return resp
    try:
        ctype = str(resp.headers.get("content-type") or "").lower()
    except Exception:  # noqa: BLE001 - 拿不到头就当不是 JSON
        return resp
    if "application/json" not in ctype:
        return resp
    payload = ""
    try:
        payload = resp.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        pass
    close_stream(resp)
    kind = _classify_json_body(payload)
    note_sentinel(realm, account_id, kind, code=kind, status=200, detail=payload)
    detail = payload.strip()[:400] or "（空响应体）"
    raise UpstreamError(
        "模型网关没有返回 SSE 流，而是回了一段 JSON（上游业务错误）：%s" % detail,
        status=502 if kind in ("http_error", "upstream_business") else 200,
        code=kind, kind=kind, detail=payload)


def close_stream(resp) -> None:
    """关掉 SSE 连接。**绝不抛**（已经在 finally 里）。"""
    try:
        if resp is not None:
            resp.close()
    except Exception:  # noqa: BLE001
        pass


#: 上游业务码里属于「登录态 / 鉴权」的那几个 —— **只有命中这里才归 `auth`**。
#:
#: `4100403 登录态过期/未登录`（真机实测：同一份过期凭证打钱包接口回
#: `data: null`，打模型网关就回这个码，两条路径指同一件事）。
#:
#: ⚠ `4100404` **不在**这张表里：它是设备/IP 风控（见 endpoints.py 的 UA
#: 约束），必须继续归 `risk_control` —— 归成 `auth` 会让真风控静默，还会把
#: 一个好号冷却 600 秒，而真正的处置是换出口 / 换设备指纹。
BIZ_AUTH_CODES = frozenset({"4100403"})

#: 上游业务码里属于「**额度耗尽**」的那几个。
#:
#: `3000001 用户名下token不足` —— 2026-11-17 实测：账号 189 余额只剩 5%
#: （510504 / 10000000），上游 402 回这个码，6 小时内打了 **1501 次**，
#: 占全部失败的 98%。旧实现把它归成 `upstream_business`（不冷却、不隔离、
#: 不换号），于是 `select_account` 按 `last_used_at` 升序永远先选到它，
#: 每个请求都先白撞一次 402 才轮到好号。
#:
#: 归到 `quota_exceeded` 之后走 `shared.note_failure(zero_balance=True)` 的
#: **降级窗口**（degrade_until，QUOTA_DEGRADE_SECONDS = 1800 秒）：30 分钟内
#: 它不再进候选集，窗口到期 + 下一次余额同步自动回到池子里（不需要人工解禁）。
#:
#: 1000007「积分余额不足」与 3000100 同源（都是「额度族」）：号没坏、Key 也没错，
#: 只是这个号此刻拿不出这次请求要的额度。归到同一个 kind 才能走同一条
#: 「换号」处置 —— 归成 upstream_business 只会原样抛错，用户看到的是一句
#: 看不出原因的「请求失败」。gateway 侧再用 upstream_code 区分文案。
BIZ_QUOTA_CODES = frozenset({"3000001", "1000007"})

#: 上游「**输入 + max_tokens** 超出预算」：`3000100 max input token error`。
#:
#: 2026-11-19 线上样本：`bytes=24203 / msgs=2 / sys_chars=1058` —— 请求体只有
#: 24 KB，却被拒；同一账号在实时窗口内又成功处理过 `bytes=527389 / msgs=98`
#: 的更大形状 20 次 ⇒ **体积不是判据**（旧解释「输入 token + max_tokens 合计
#: 一条预算」已被 25 发实测证伪）。
#:
#: 归因后剩下的唯一维度：`max_tokens` 与该号该模型**当次剩余可用预算**的对照。
#: 网关仍然先做一次 max_tokens 降级（大多数情况能救回来）；降级后仍失败就
#: 标 (账号×模型) 额度紧张并换号，只有池子用完才报给用户。
#:
#: 所以归成独立 kind `max_input_tokens`：网关据此识别「这是额度/预算问题」，
#: 而不是「请求太大」—— 后者会把人引向拆消息，改了也没用。
BIZ_MAX_INPUT_CODES = frozenset({"3000100"})

#: 上游「**内部错误**」—— 与账号、凭证、请求形状都无关的那几个码。
#:
#: `5100007 服务端通用错误:服务端严重报错`。2026-11-19 线上 3 次失败的因果链：
#: 一个 2 MB 的合成请求打在 acc=192 上，网关把它当普通 5xx，于是
#: 「同号重试 → 换号 → 再换号」，192 → 191 → 194 **三个号各失败一次**，
#: 最后报「已无更多可用账号」。三个号都被写了一次 err_count。
#:
#: 实测结论：这个码是**上游服务端**在同一份 body 上给出的确定性拒绝，
#: 换任何一个号都会得到同一个答案。换号唯一的实际效果是污染整个池子的失败计数。
#:
#: ⇒ 归成 `upstream_internal`：同号重试一次（1–2 s 退避），仍失败就直接报错；
#: 不换号、不计 err_count、不打冷却（见 gateway.`_attempt` 与
#: shared.`note_failure(count_error=False)`）。
BIZ_INTERNAL_CODES = frozenset({"5100007"})

#: 从错误体里抠业务码：`{"code":4100406,...}` / `{"code":"4100403",...}`。
_BIZ_CODE_RE = re.compile(r'"code"\s*:\s*"?(-?[A-Za-z0-9_.]+)"?')


def _biz_code_of(text: str) -> str:
    """解析上游错误体里的业务码（返回**字符串**；解析不出返回 ""）。

    上游的错误信封实测有两种写法：

        {"code":4100406,"msg":"非法请求"}     ← 数字
        {"code":"4100403", ...}               ← 字符串

    还可能被包一层（`{"error": {"code": ...}}`）。先按 JSON 解，
    解不动退回正则抠第一处 `"code":` —— 错误体里混了别的文本时也要能认。
    """
    raw = str(text or "").strip()
    if not raw:
        return ""
    start = raw.find("{")
    if start > 0:
        raw = raw[start:]
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        if isinstance(data, dict):
            code = data.get("code")
            if code is None:
                err = data.get("error")
                if isinstance(err, dict):
                    code = err.get("code")
            if isinstance(code, bool):
                code = None
            if code is not None:
                return str(code).strip()
    match = _BIZ_CODE_RE.search(raw)
    return match.group(1).strip() if match else ""


def _classify(status: int, text: str) -> str:
    """把上游错误归成一类 —— 上层据此决定冷却 / 禁用 / 换号。

    ## 业务码不能吞（2026-11-15 线上误判的直接根因）

    上游 403 的真实报文是 {"code":4100406,"msg":"非法请求"} —— **有业务码**。
    旧实现只看 HTTP 状态，403 一律 signature_invalid，于是：

      · 上层按「签名错」处理（同号重试 / 换号），但
        /v1/chat/completions **根本不签名**（core/endpoints.py:15-19），
        换号不可能修好；
      · signature_invalid 在 sentinel 里是 RISK 级（sentinel.py:174），
        一次瞬时 403 就能把一个好平台推进熔断；
      · 下游（DSH）把 403 渲染成「API key is invalid」，用户去翻 key，
        而真实原因是请求被上游业务层拒绝。

    现在：解析业务码 —— 命中已知鉴权码才归 auth，其余一律
    upstream_business（已在 SENTINEL_KINDS 里，按 throttle 记录，不熔断、
    不冷却、不重试）。

    ⚠ 风控（4100404）与登录失效（401）仍必须分开：前者是**设备/IP** 的问题，
    换号没用还会把好号一起打挂；后者才是账号问题。

    ## 本次合并的分工（两侧口径都要保住，别改回单边）

      · 403 + 4100406 -> upstream_business（同号可重试一次），
        见 tests/test_marvis_platform.py / test_marvis_risk_backoff.py；
      · 200 + application/json 里的 4100406 才是内容风控 content_block，
        由 _classify_json_body() 产出（上游 PR #55 / keepalive 测试口径）；
      · signer_unavailable：签名器不可用（本端故障），网关据此 fail-closed、
        不 note_failure —— 上游 8aba19a 新增，必须能从这里产出。
    """
    blob = str(text or "")
    low = blob.lower()
    code = _biz_code_of(blob)
    # 签名器不可用：本端故障，与账号 / 风控无关，必须在 403 / risk 判定之前。
    if (code == "signer_unavailable" or "signer_unavailable" in low
            or "签名不可用" in blob or "beacon 未就绪" in blob):
        return "signer_unavailable"
    if code == "4100404" or "4100404" in low or "risk" in low or "风控" in blob:
        return "risk_control"
    # 额度耗尽必须在 auth 之前判：402 + 3000001 与凭证无关，
    # 归错就会变成「永不隔离的号永远排第一，每次请求先白撞一次」。
    if code in BIZ_QUOTA_CODES:
        return "quota_exceeded"
    # 输入 + max_tokens 超预算：**必须在 auth / upstream_business 之前判**。
    if code in BIZ_MAX_INPUT_CODES:
        return "max_input_tokens"
    # 上游内部错误：与账号无关，必须在 auth(401) 判定之前拦下。
    if code in BIZ_INTERNAL_CODES:
        return "upstream_internal"
    # 登录态过期（4100403）：模型网关通常回 401，凭证刚过期时会 200+JSON。
    if (code == "4100403" or "4100403" in low
            or "登录态过期" in blob or "未登录" in blob):
        return "auth"
    # ------------------------------------------------------------------
    # 业务码族：**不能只看 HTTP 状态**（402 / 400 / 500 都不是判据）
    # ------------------------------------------------------------------
    #
    # 上游把「额度不足」「预算不够」「服务端内部错误」全塞在 HTTP 4xx/5xx 里，
    # 真正的判据只在 body 的 `code` 上。只看状态的话这四种码一律落进
    # `upstream_business` → 网关「不重试、不冷却、原样抛」，用户拿到的是一句
    # 看不出原因的「模型网关失败（HTTP 402 / 500）」，然后去改一个没写错的请求体。
    #
    # ⚠ 插在 `status == 401` 之前是有意的：402 + 3000001 与登录态无关，
    # 归错就会变成「永不隔离的号永远排第一，每次请求先白撞一次」。
    # 4100404 / 4100406 / 4100403 的判定在上面，一个都不动。
    code = _biz_code_of(text)
    if code in BIZ_QUOTA_CODES:
        return "quota_exceeded"
    if code in BIZ_MAX_INPUT_CODES:
        return "max_input_tokens"
    if code in BIZ_INTERNAL_CODES:
        return "upstream_internal"
    if status == 401:
        return "auth"
    if code:
        # 业务码是**最可信**的信号：信封里有 code，就说明这是业务层拒绝，
        # 不是传输/协议错误。鉴权码之外一律 upstream_business。
        return "auth" if code in BIZ_AUTH_CODES else "upstream_business"
    if status == 403:
        # 没有业务码的 403：不是签名问题（这个端点不签名），按业务拒绝处理。
        return "upstream_business"
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



