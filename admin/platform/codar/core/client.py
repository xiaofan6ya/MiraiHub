"""科搭 Codar 上游 HTTP 客户端：错误分类、目录缓存、每账号并发闸门。

## 这里为什么要分四种「失败」

上游把四种完全不同的处境回成相似的 4xx，混成一句「登录失效」就会把
用户指向错误的修法：

| 上游说什么 | 真实含义 | 用户该做什么 |
|---|---|---|
| `403 {"detail":{"code":"account_restricted"}}` | 厂商限制了这个账号的 API 访问 | 找厂商支持；**重新登录没有用** |
| `403 {"detail":{"code":"client_not_supported",…}}` | 厂商要求第三方客户端改用 API key | 换成受支持的 API key；重登也没用 |
| `401` / 体内 `code:401` | 令牌过期 / 被吊销 | 打开客户端刷新登录态后再导入 |
| `429 concurrent_limit_exceeded` | 每账号在途请求超上限 | **等一下**（不是账号坏了） |

所以 `UpstreamError.kind` 有明确取值，调用方按 kind 决定「冷却 / 重试 /
停用 / 换号」，文案也各写各的。

## 并发闸门为什么放在这一层

上游实测每账号 3 个在途请求，第 4 个回 429（厂商不公开这个数字）。
框架的在途租约（`admin.pool`）能在**选号**时避开满号，但不能让请求排队：
池子只有一个号时它只会回「没有可用账号」。这里用一个账号一把
`BoundedSemaphore`，拿不到就在**队列里等**，等到超时才回 429 ——
「等着」比「打一个注定被拒的请求」对用户友好得多。
"""
from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
from contextlib import contextmanager

import httpx

from admin.platform.codar.core import endpoints

logger = logging.getLogger("admin.platform.codar.client")

#: read=600s 是**帧间隔**上限而不是总时长：思考型模型会静默很久，
#: 总时长上限会把正常的长回答掐断。
TIMEOUT = httpx.Timeout(connect=15.0, read=600.0, write=60.0, pool=15.0)
#: 目录 / 余额 / 签到这些短请求各自的上限（卡片的等待比对话短得多）。
SHORT_TIMEOUT = httpx.Timeout(connect=10.0, read=20.0, write=20.0, pool=10.0)

#: 排队等并发空位的最长时间（秒）。超过就如实回 429 + Retry-After，
#: 让客户端待会儿再来 —— 不把「忙」说成「号坏了」。
QUEUE_TIMEOUT = 30.0

#: 目录缓存时长（秒）。目录按账号实时拉，短期缓存只为挡住面板的连续刷新。
CATALOG_TTL = 60.0

#: 上游答案大小上限：目录是几十 KB，超过 8 MiB 的「答案」不是答案。
MAX_ANSWER_BYTES = 8 * 1024 * 1024


class UpstreamError(Exception):
    """上游业务 / 传输错误（带分类，供冷却、换号、错误文案使用）。"""

    def __init__(self, message: str, *, status: int = 0, kind: str = "upstream",
                 code: str = "", detail: str = "", retry_after: int = 0,
                 body=None):
        super().__init__(message)
        self.message = message
        self.status = int(status or 0)
        self.kind = kind
        self.code = str(code or "")
        self.detail = detail
        self.retry_after = int(retry_after or 0)
        self.body = body

    @property
    def retryable(self) -> bool:
        """换号 / 重试有没有意义。

        `concurrent` 与 `rate` 是「等一会儿」而不是「这个号坏了」；
        `auth` / `account_restricted` / `client_gated` / `request` 换号也一样。
        """
        return self.kind in ("rate", "concurrent", "upstream", "network")

    def to_dict(self) -> dict:
        return {"message": self.message, "status": self.status,
                "kind": self.kind, "code": self.code,
                "retry_after": self.retry_after}

    def anthropic_error(self) -> dict:
        return {"type": "error",
                "error": {"type": _anthropic_type(self.kind),
                          "message": self.message}}

    def openai_error(self) -> dict:
        return {"error": {"type": _openai_type(self.kind),
                          "code": self.code or self.kind,
                          "message": self.message}}


def _anthropic_type(kind: str) -> str:
    return {
        "auth": "authentication_error",
        "account_restricted": "permission_error",
        "client_gated": "permission_error",
        "rate": "rate_limit_error",
        "concurrent": "rate_limit_error",
        "quota": "rate_limit_error",
        "request": "invalid_request_error",
        "model": "not_found_error",
    }.get(kind, "api_error")


def _openai_type(kind: str) -> str:
    return {
        "auth": "authentication_error",
        "account_restricted": "permission_error",
        "client_gated": "permission_error",
        "rate": "rate_limit_error",
        "concurrent": "rate_limit_error",
        "quota": "insufficient_quota",
        "request": "invalid_request_error",
        "model": "invalid_request_error",
    }.get(kind, "upstream_error")


# ---------------------------------------------------------------------------
# 应答读取（信封 / 拒绝 / 文案）
# ---------------------------------------------------------------------------
def _plain(value) -> bool:
    return isinstance(value, dict)


def unwrapped(payload, depth: int = 8):
    """沿 `data` 往里走到最深一层对象（拒绝可能藏在里面）。

    上游好几个接口都用 `{code, data:{…}}` 包一层，而拒绝会写在里层
    （`{code:200, data:{code:401}}`）—— 只看外层就会把一次拒绝读成
    「字段缺失」。
    """
    node = payload
    for _ in range(depth):
        if not _plain(node):
            return node
        inner = node.get("data")
        if not _plain(inner):
            return node
        node = inner
    return node


def _code_number(value):
    """业务码转数字（不是数字给 None，**不**对对象调 int()）。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return None if not math.isfinite(float(value)) else float(value)
    if isinstance(value, str):
        text = value.strip()
        if re.fullmatch(r"[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", text or ""):
            try:
                return float(text)
            except ValueError:
                return None
    return None


def body_code(payload):
    """信封里的业务码：数字给数字，单词给单词，没有给 None。"""
    if not _plain(payload):
        return None
    for node in (payload, unwrapped(payload)):
        if not _plain(node):
            continue
        if "code" in node:
            raw = node.get("code")
            number = _code_number(raw)
            if number is not None:
                return number
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
    return None


def error_word(payload) -> str:
    """拒绝的**词形业务码**（`account_restricted` / `client_not_supported` …）。"""
    if not _plain(payload):
        return ""
    nodes = [payload, payload.get("error"), payload.get("detail"),
             payload.get("data"), (payload.get("data") or {}).get("error")
             if _plain(payload.get("data")) else None,
             (payload.get("data") or {}).get("detail")
             if _plain(payload.get("data")) else None]
    for node in nodes:
        if not _plain(node):
            continue
        # **只读 `code`**：Anthropic 的成功应答也带 `type`（"message"），
        # 把 `type` 当业务码会把一次正常回复读成拒绝。
        raw = node.get("code")
        if isinstance(raw, str) and raw.strip() and _code_number(raw) is None:
            return raw.strip()
    return ""


def said_by(payload) -> str:
    """拒绝的文案：顶层与外层 `data` 的 message/msg/error/detail 里第一段文字。"""
    if not _plain(payload):
        return ""

    def words(value) -> str:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if _plain(value):
            for key in ("message", "msg", "error", "detail", "reason"):
                inner = value.get(key)
                if isinstance(inner, str) and inner.strip():
                    return inner.strip()
        return ""

    data = payload.get("data") if _plain(payload.get("data")) else None
    for candidate in (payload.get("message"), payload.get("msg"),
                      payload.get("error"), payload.get("detail"),
                      payload.get("reason"),
                      data.get("message") if data else None,
                      data.get("msg") if data else None,
                      data.get("error") if data else None,
                      data.get("detail") if data else None):
        got = words(candidate)
        if got:
            return got
    return ""


def _status_of_business(code) -> int:
    """体内业务码 → 用来分类的状态码（HTTP 200 里的 401 也是登录失效）。"""
    number = _code_number(code)
    if number is None:
        return 400
    whole = int(number)
    return whole if 400 <= whole <= 599 else 400


#: 「本日已领」的中英文说法。上游两种语言都会回，也都可能挂在 200 上。
ALREADY_CLAIMED = re.compile(
    r"(?<!\d)24\s*小时(?:内)?[^。；;]{0,8}?(?:(?:仅|只能|才)\s*可?\s*(?:领取|签到)|"
    r"(?:领取|签到)\s*(?:一次|1\s*次))"
    r"|(?:仅|只能)可?(?:领取)?一次"
    r"|once\s+(?:every|in|per)\s+(?:a\s+)?(?:24\s*(?:hours?|h)|day)"
    r"|already\s+(?:claimed|signed|checked\s*in)"
    r"|(?:今[日天]|本次)\s*已经?\s*(?:签到|领取|签过|领过)"
    r"|已经?\s*(?:签到|领取|签过|领过)过"
    r"|(?:请?勿|无需|不用|不必)?重复(?:签到|领取)",
    re.IGNORECASE,
)

#: 「失败」类措辞：带等待秒数的 400 若是这些话，就不是「今日已领」。
FAILED_WORDS = re.compile(
    r"(?:风险|风控|限流|繁忙|维护|失败|错误|error|failed|rate\s*limit|"
    r"too\s*many|busy|maintenance|unavailable|denied|forbidden)",
    re.IGNORECASE,
)


def already_claimed_text(*texts) -> bool:
    return any(ALREADY_CLAIMED.search(str(text or "")) for text in texts)


def failed_text(*texts) -> bool:
    return any(FAILED_WORDS.search(str(text or "")) for text in texts)


def _retry_after_of(value) -> int:
    """`Retry-After`：秒数或 HTTP 日期，读不出给 0（**不猜**）。"""
    text = str(value or "").strip()
    if not text:
        return 0
    if re.fullmatch(r"\d+", text):
        return int(text)
    try:
        from email.utils import parsedate_to_datetime

        when = parsedate_to_datetime(text)
        if when is None:
            return 0
        delta = int(when.timestamp() - time.time())
        return delta if delta > 0 else 0
    except Exception:  # noqa: BLE001 - 读不出就当上游没说
        return 0


def retry_after_seconds(value) -> float:
    """等待秒数（可带小数）：0 表示没给，负值/垃圾值也归 0。"""
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) and number > 0 else 0.0
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        number = float(text)
    except ValueError:
        return 0.0
    return number if math.isfinite(number) and number > 0 else 0.0


#: 上游拒绝里可能出现令牌回显，日志与错误文案都要先擦掉。
_TOKEN_PATTERNS = (
    re.compile(r"(Bearer\s+)\S+", re.IGNORECASE),
    re.compile(r"(X-Api-Key:\s*)\S+", re.IGNORECASE),
    re.compile(r"\beyJ[A-Za-z0-9_\-.]{10,}"),
)


def redact(text: str, limit: int = 400) -> str:
    out = str(text or "")
    for pattern in _TOKEN_PATTERNS:
        out = pattern.sub(lambda m: (m.group(1) + "***") if m.groups() else "***",
                          out)
    return out[:limit]


def _message_of(payload, status: int, text: str) -> str:
    said = said_by(payload)
    if said:
        return redact(said, 200)
    if text and len(text) < 200 and "<" not in text:
        return redact(text, 200)
    return ""


def classify(status: int, payload, text: str,
             retry_after: int = 0) -> UpstreamError:
    """上游应答 → `UpstreamError`（**四种语义分开**，文案各说各的）。"""
    word = error_word(payload)
    said = _message_of(payload, status, text)
    code_word = word.lower()
    joined = (str(word) + " " + str(said)).lower()

    if "account_restricted" in joined:
        return UpstreamError(
            "账号被上游限制访问 API（account_restricted）：这不是登录失效，"
            "重新登录也不会恢复，需要联系厂商支持",
            status=status or 403, kind="account_restricted", code="account_restricted",
            detail=redact(text), body=payload)
    if "client_not_supported" in joined or (
            "api key" in joined and "codar" in joined):
        return UpstreamError(
            "上游要求第三方客户端改用 API key（client_not_supported）："
            "桌面登录态不能用于本网关，重新登录也无法解决",
            status=status or 403, kind="client_gated", code="client_not_supported",
            detail=redact(text), body=payload)
    if status == 429 or code_word in ("429", "too_many_requests"):
        concurrent = "concurrent" in joined or "并发" in joined
        return UpstreamError(
            "上游并发数超过账号上限（每账号 3 个在途请求），请稍后重试"
            if concurrent else "上游限流，请稍后重试",
            status=429, kind="concurrent" if concurrent else "rate",
            code="concurrent_limit_exceeded" if concurrent else "rate_limited",
            retry_after=retry_after, detail=redact(text), body=payload)
    if status in (401, 403) or code_word in ("401", "403"):
        if status == 403:
            return UpstreamError(
                "上游拒绝了这个令牌（HTTP 403）：令牌可能已被吊销或不属于本账号",
                status=403, kind="auth", code=word or "forbidden",
                detail=redact(text), body=payload)
        return UpstreamError(
            "登录态已失效（HTTP 401）：打开科搭客户端刷新登录后再导入",
            status=status or 401, kind="auth", code=word or "unauthorized",
            detail=redact(text), body=payload)
    if "quota" in joined or "insufficient" in joined or "余额" in joined or "点数不足" in joined:
        return UpstreamError(said or "账号点数不足",
                             status=status or 402, kind="quota",
                             code=word or "insufficient_quota",
                             detail=redact(text), body=payload)
    if status == 404:
        return UpstreamError(said or "模型不存在或不可用（404）",
                             status=404, kind="model", code=word or "not_found",
                             detail=redact(text), body=payload)
    if status in (400, 422):
        return UpstreamError(said or "上游拒绝了请求参数（HTTP %d）" % status,
                             status=status, kind="request",
                             code=word or "invalid_request",
                             detail=redact(text), body=payload)
    if status >= 500:
        return UpstreamError("上游错误（HTTP %d）" % status, status=status,
                             kind="upstream", code=word, detail=redact(text),
                             body=payload)
    return UpstreamError(said or "上游错误（HTTP %d）" % status,
                         status=status or 502, kind="upstream", code=word,
                         detail=redact(text), body=payload)


def _parse_json(text: str):
    body = (text or "").lstrip("\ufeff")
    if not body.strip():
        return None
    try:
        return json.loads(body)
    except ValueError:
        return None


def _read_response(resp: httpx.Response):
    """读一次应答：文本、解析后的 JSON、错误分类。"""
    try:
        resp.read()
    except Exception:  # noqa: BLE001 - 读不到正文也要能把状态码说清楚
        pass
    raw = resp.content or b""
    if len(raw) > MAX_ANSWER_BYTES:
        return "", None
    text = raw.decode("utf-8", "replace")
    return text, _parse_json(text)


#: 跟随的同主机跳数上限（跨主机一律拒绝：那会把令牌发给别人）。
MAX_REDIRECT_HOPS = 2
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


def _host_of(url: str) -> str:
    try:
        return (httpx.URL(url).host or "").lower()
    except Exception:  # noqa: BLE001
        return ""


def _send(client: httpx.Client, method: str, url: str, *,
          headers: dict, json_body=None, params=None) -> httpx.Response:
    """发一次请求；只跟随同主机重定向，跨主机直接报错。"""
    at = url
    for hop in range(MAX_REDIRECT_HOPS + 1):
        resp = client.request(method, at, headers=headers, json=json_body,
                              params=params, follow_redirects=False)
        if resp.status_code not in REDIRECT_STATUSES:
            return resp
        location = resp.headers.get("location") or ""
        if not location or hop >= MAX_REDIRECT_HOPS:
            return resp
        target = str(httpx.URL(at).join(location))
        if _host_of(target) != _host_of(at):
            raise UpstreamError(
                "上游把请求重定向到了另一台主机（%s → %s），令牌不会跟过去"
                % (_host_of(at), _host_of(target)),
                status=resp.status_code, kind="upstream")
        at = target
        params = None
        if resp.status_code in (301, 302, 303) and method.upper() == "POST":
            method = "GET"
            json_body = None
    return resp


# ---------------------------------------------------------------------------
# 请求
# ---------------------------------------------------------------------------
def _new_client(timeout) -> httpx.Client:
    """建一个 httpx 客户端。

    **唯一**的建连点：测试用它注入 `httpx.MockTransport`，于是全部用例离线
    （不触网、不需要真令牌）。生产不需要切换任何开关。
    """
    return httpx.Client(timeout=timeout)

def request_json(method: str, url: str, *, token: str = "",
                 protocol: str = "chat", json_body=None, params=None,
                 account_id=None, context: str = "",
                 timeout: httpx.Timeout | None = None):
    """发一次 JSON 请求，返回解析后的对象。

    HTTP 非 2xx → `classify()` 的异常；HTTP 2xx 但**体内业务码拒绝**
    （`code != 200` / `success:false`）也是拒绝 —— 上游好几个接口都用
    「HTTP 200 + 体内拒绝」表达失败（余额读不到时曾因此被当成 0）。
    """
    headers = endpoints.headers_for(protocol, token)
    if json_body is not None:
        headers["Content-Type"] = "application/json"
    tag = "codar#%s" % (account_id if account_id is not None else "-")
    try:
        with _new_client(timeout or SHORT_TIMEOUT) as client:
            resp = _send(client, method, url, headers=headers,
                         json_body=json_body, params=params)
    except httpx.HTTPError as exc:
        raise UpstreamError("%s 网络错误（%s）" % (tag, type(exc).__name__),
                            kind="network", detail=redact(str(exc))) from exc
    text, payload = _read_response(resp)
    if resp.status_code >= 400:
        raise classify(resp.status_code, payload, text,
                       _retry_after_of(resp.headers.get("retry-after")))
    code = body_code(payload)
    word = error_word(payload)
    if word and word.lower() != "already_claimed":
        # 词形业务码在 200 里也是拒绝（`{"code":"account_restricted"}`）
        raise classify(400, payload, text)
    if code is not None and _code_number(code) not in (None, 200):
        # 业务码要按它自己的语义分类：`{"code":401}` 是登录失效，
        # `{"code":500}` 是上游故障 —— 拿 HTTP 200 去分类会一律读成
        # 「上游错误」，用户就看不到「该刷新登录了」。
        raise classify(_status_of_business(code), payload, text)
    if _plain(payload) and payload.get("success") is False:
        raise classify(resp.status_code, payload, text)
    if text and payload is None:
        raise UpstreamError("%s 返回非 JSON（HTTP %d）" % (tag, resp.status_code),
                            status=resp.status_code, kind="upstream",
                            detail=redact(text))
    return payload


def open_stream(url: str, *, token: str, protocol: str = "chat",
                json_body=None, account_id=None, stats: dict | None = None,
                timeout: httpx.Timeout | None = None):
    """打开上游 SSE（原样字节）。返回 httpx.Response（stream 模式）。

    `Accept-Encoding: identity` 是必须的：被要求压缩时，整段 SSE 会被压成
    一个 gzip member 直到回答结束才 flush，`iter_lines()` 拿到的是
    「已经结束的整块」—— 流式会静默退化成非流式（首字等到最后一 token）。
    """
    headers = endpoints.headers_for(protocol, token)
    headers["Content-Type"] = "application/json"
    headers["Accept"] = "text/event-stream"
    headers["Accept-Encoding"] = "identity"
    tag = "codar#%s" % (account_id if account_id is not None else "-")
    client = _new_client(timeout or TIMEOUT)
    started = time.perf_counter()
    try:
        request = client.build_request("POST", url, headers=headers,
                                       json=json_body)
        resp = client.send(request, stream=True)
    except httpx.HTTPError as exc:
        client.close()
        raise UpstreamError("%s 网络错误（%s）" % (tag, type(exc).__name__),
                            kind="network", detail=redact(str(exc))) from exc
    if resp.status_code >= 400:
        text, payload = _read_response(resp)
        error = classify(resp.status_code, payload, text,
                         _retry_after_of(resp.headers.get("retry-after")))
        resp.close()
        client.close()
        raise error
    if stats is not None:
        try:
            stats["ttfb_ms"] = int((time.perf_counter() - started) * 1000)
        except Exception:  # noqa: BLE001 - 统计失败不影响转发
            pass
    # httpx 的 client 由我们自己开，close 时一起收
    resp._codar_client = client  # type: ignore[attr-defined]
    return resp


def sse_lines(resp):
    """上游字节流 → 单行 bytes（保留事件行）。"""
    for raw in resp.iter_lines():
        yield raw if isinstance(raw, bytes) else str(raw).encode("utf-8")


def close_stream(resp) -> None:
    try:
        resp.close()
    except Exception:  # noqa: BLE001
        pass
    client = getattr(resp, "_codar_client", None)
    if client is not None:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# 每账号并发闸门（拿不到就排队，排不到才拒）
# ---------------------------------------------------------------------------
_GATE_LOCK = threading.Lock()
#: 每个账号一把（key = uid）。**不能共用一把**：上限是「每账号」的，
#: 全局一把会把 10 个号的池子压成 3 路总并发。
_GATES: dict[str, threading.BoundedSemaphore] = {}


def _gate(uid: str) -> threading.BoundedSemaphore:
    key = str(uid or "").strip()
    with _GATE_LOCK:
        gate = _GATES.get(key)
        if gate is None:
            gate = threading.BoundedSemaphore(endpoints.max_concurrency())
            _GATES[key] = gate
        return gate


def acquire_slot(uid: str, *, timeout: float = QUEUE_TIMEOUT) -> bool:
    """给这个账号占一个在途名额；拿到 True，排队超时 False。"""
    gate = _gate(uid)
    started = time.monotonic()
    if gate.acquire(timeout=max(0.0, timeout)):
        logger.debug("codar 账号 %s 取得在途名额（等 %.2fs）",
                     uid, time.monotonic() - started)
        return True
    return False


def release_slot(uid: str) -> None:
    gate = _gate(uid)
    try:
        gate.release()
    except ValueError:
        # 重复释放只会在本模块记账出错时发生，别让它变成 500
        logger.debug("codar 账号 %s 在途名额重复释放（忽略）", uid)


@contextmanager
def slot(uid: str, *, timeout: float = QUEUE_TIMEOUT):
    """带排队的在途名额。

    排队超时抛 429（`kind="concurrent"` + `Retry-After`），而不是回
    「没有可用账号」—— 号是好的，只是此刻排不上。
    """
    if not acquire_slot(uid, timeout=timeout):
        raise UpstreamError(
            "本账号在途请求已满（每账号 %d 个并发），排队 %.0f 秒仍无空位"
            % (endpoints.max_concurrency(), timeout),
            status=429, kind="concurrent", code="concurrent_limit_exceeded",
            retry_after=5)
    try:
        yield
    finally:
        release_slot(uid)


def inflight(uid: str = "") -> int:
    """某账号当前占用（观测用；不传 uid 给所有账号的合计）。"""
    size = endpoints.max_concurrency()
    with _GATE_LOCK:
        gates = dict(_GATES) if not uid else {str(uid).strip(): _GATES.get(str(uid).strip())}
    total = 0
    for gate in gates.values():
        if gate is None:
            continue
        total += max(0, size - gate._value)  # noqa: SLF001 - 只读观测
    return total


# ---------------------------------------------------------------------------
# 目录缓存与协议选路
# ---------------------------------------------------------------------------
_CATALOG_LOCK = threading.Lock()
_CATALOG: dict = {"rows": [], "at": 0.0, "fallback": True,
                  "vendor_concurrency": 0}


def remember_catalog(rows, *, fallback: bool = False,
                     vendor_concurrency: int = 0) -> None:
    with _CATALOG_LOCK:
        _CATALOG["rows"] = list(rows or [])
        _CATALOG["at"] = time.time()
        _CATALOG["fallback"] = bool(fallback)
        if vendor_concurrency:
            _CATALOG["vendor_concurrency"] = int(vendor_concurrency)


def cached_catalog() -> dict:
    with _CATALOG_LOCK:
        return {"rows": list(_CATALOG["rows"]), "at": _CATALOG["at"],
                "fallback": _CATALOG["fallback"],
                "vendor_concurrency": int(_CATALOG["vendor_concurrency"] or 0)}


def catalog_fresh() -> bool:
    with _CATALOG_LOCK:
        return bool(_CATALOG["rows"]) and (time.time() - _CATALOG["at"]) < CATALOG_TTL


def protocol_of(model: str) -> str:
    """按目录选协议；目录里没有这条（或没拉过）就按模型名兜底。"""
    name = str(model or "").strip()
    with _CATALOG_LOCK:
        rows = list(_CATALOG["rows"])
    for row in rows:
        if row.get("id") == name:
            return row.get("protocol") or endpoints.protocol_by_name(name)
    return endpoints.protocol_by_name(name)


def model_limits(model: str) -> dict:
    """模型的上限（目录里没有就给空 dict，**不编**默认值）。"""
    name = str(model or "").strip()
    with _CATALOG_LOCK:
        rows = list(_CATALOG["rows"])
    for row in rows:
        if row.get("id") == name:
            return {"context": int(row.get("context") or 0),
                    "max_output": int(row.get("max_output") or 0)}
    return {}


# ---------------------------------------------------------------------------
# 目录解析
# ---------------------------------------------------------------------------
def _tokens(value) -> int:
    number = _code_number(value)
    if number is None or number <= 0 or number > 10_000_000:
        return 0
    return int(number)


def _kinds(value) -> list[str]:
    known = ("text", "image", "audio", "video")
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out = [str(item).strip().lower() for item in value
           if str(item).strip().lower() in known]
    return out


def _rate(value) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) and number > 0 else 0.0
    match = re.fullmatch(r"\s*[x×]?\s*(\d+(?:\.\d+)?)\s*[x×]?\s*",
                         str(value or ""), re.IGNORECASE)
    return float(match.group(1)) if match else 0.0


def node_refuses(node) -> bool:
    """这个应答节点自己是否在说「拒绝」。

    上游把拒绝写在三个层次上：信封（`{code:500}`）、嵌套体
    （`{catalog:{code:500,data:[…]}}`）、或者只有 `success:false`。
    取列表前逐层过一遍 —— 把一次拒绝里残留的列表当目录，会把账号的模型
    整体换成拒绝体里的内容。
    """
    if not _plain(node):
        return False
    code = body_code(node)
    number = _code_number(code)
    if number is not None and number != 200:
        return True
    if error_word(node):
        return True
    return node.get("success") is False


def catalog_records(payload) -> list[dict] | None:
    """目录应答 → 归一记录；**读不出列表给 None**（不是空列表）。

    空列表是「这个账号没有可用模型」，读不出是「这次没用」—— 两者混用
    会让一次故障把目录清空。
    """
    if isinstance(payload, list):
        items = payload
    elif _plain(payload):
        for node in (payload, payload.get("catalog"), payload.get("data")):
            if node_refuses(node):
                return None
        items = None
        for candidate in (payload.get("data"), payload.get("catalog"),
                          (payload.get("catalog") or {}).get("data")
                          if _plain(payload.get("catalog")) else None,
                          (payload.get("catalog") or {}).get("records")
                          if _plain(payload.get("catalog")) else None,
                          payload.get("models"), payload.get("records")):
            if isinstance(candidate, list):
                items = candidate
                break
            if _plain(candidate):
                for key in ("data", "records", "models", "list"):
                    if isinstance(candidate.get(key), list):
                        items = candidate[key]
                        break
                if items is not None:
                    break
        if items is None:
            return None
    else:
        return None

    rows: list[dict] = []
    for item in items:
        if not _plain(item):
            continue
        model_id = str(item.get("id") or item.get("model") or "").strip()
        if not model_id:
            continue
        protocol = endpoints.protocol_word(item.get("protocol"))
        if not protocol:
            # 目录点了名而本网关没有那条线（例如 `openai_responses`）：
            # 当作 chat 发出去就是一整类必然失败的请求，宁可不上架。
            continue
        schema = item.get("request_schema")
        max_out = 0
        if _plain(schema):
            properties = schema.get("properties")
            if _plain(properties):
                max_tokens = properties.get("max_tokens")
                if _plain(max_tokens):
                    max_out = _tokens(max_tokens.get("maximum"))
        rows.append({
            "id": model_id,
            "name": str(item.get("name") or model_id).strip() or model_id,
            "context": _tokens(item.get("context_window")),
            "max_output": max_out,
            "input": _kinds(item.get("input_modalities")) or ["text"],
            "output": _kinds(item.get("output_modalities")) or ["text"],
            "protocol": protocol,
            "reasoning": bool(item.get("reasoning")),
            "rate": _rate(item.get("rate_badge")),
        })
    return rows


def vendor_concurrency(payload) -> int:
    """目录自己声明的并发上限（取它说过的最小值；没说给 0）。"""
    found = 0

    def take(value) -> None:
        nonlocal found
        number = _code_number(value)
        if number is None or not math.isfinite(number):
            return
        whole = int(number)
        if whole <= 0 or whole > 1000:
            return
        if not found or whole < found:
            found = whole

    def walk(node) -> None:
        if _plain(node):
            if "max_concurrency" in node:
                take(node.get("max_concurrency"))
            for key in ("catalog", "data", "records", "models"):
                walk(node.get(key))
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return found


def fetch_catalog(token: str, *, account_id=None) -> tuple[list[dict], int]:
    """拉目录 → (记录, 厂商声明的并发)。读不出列表时抛错。"""
    payload = request_json("GET", endpoints.gateway_url(endpoints.CATALOG_PATH),
                           token=token, account_id=account_id,
                           context="模型目录")
    rows = catalog_records(payload)
    if rows is None:
        raise UpstreamError("模型目录的应答不是列表",
                            kind="upstream", detail=redact(str(payload))[:300])
    return rows, vendor_concurrency(payload)
