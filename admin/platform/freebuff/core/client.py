"""Freebuff HTTP 客户端（OpenAI 兼容端点）。

## 端点很少，但语义要分清

    GET  /models              200，**不需要鉴权** —— 所以「能不能拉到模型」
                              不能证明 key 有效，key 是否有效只有真对话
                              （401 invalid_api_key）才能确认。
    POST /chat/completions    需要 `Authorization: Bearer <Developer API key>`

`/usage`、`/credits`、`/me`、`/balance`、`/health` 等一律 404（返回 HTML），
所以这个平台**没有余额接口**，额度只能看本地账本。

## 错误分档

    401 → auth             （key 无效/被撤销）—— 标记账号失效，换号
    410 → update_required  （freebuff_update_required：免费额度只给官方客户端，
                            换号也没用，直接报给调用方）
    429 → rate             （限流，短冷却后换号）
    5xx → upstream
    网络 → network
    400 + 额度字眼 → quota （账号级，换号）
    其余 4xx → fatal       （请求本身有问题）
"""
from __future__ import annotations

import json
import logging
import threading
import time

import httpx

from admin.platform.freebuff.core import endpoints

logger = logging.getLogger("admin.platform.freebuff.client")

_QUOTA_MARKERS = ("insufficient", "quota", "credit", "balance", "exceeded")

_TRANSIENT = ("UNEXPECTED_EOF_WHILE_READING", "EOF occurred in violation of protocol",
              "Connection reset by peer", "Server disconnected", "ConnectError",
              "ReadTimeout", "ConnectTimeout", "RemoteProtocolError", "SSL")
_RETRY_TIMES = 3
_BACKOFF = (0.3, 0.9, 1.6)


class UpstreamError(Exception):
    def __init__(self, message: str, *, status: int = 0, kind: str = "upstream",
                 code: str = "", detail: str = ""):
        super().__init__(message)
        self.message = message
        self.status = status
        self.kind = kind
        self.code = code
        self.detail = detail

    def to_dict(self) -> dict:
        return {"kind": self.kind, "status": self.status,
                "code": self.code, "message": self.message}


def classify(status: int, body: str = "") -> str:
    low = (body or "").lower()
    if "freebuff_update_required" in low:
        return "update_required"
    if status == 401:
        return "auth"
    if status == 429:
        return "rate"
    if status >= 500:
        return "upstream"
    if status == 0:
        return "network"
    if status == 410:
        return "update_required"
    if any(marker in low for marker in _QUOTA_MARKERS):
        return "quota"
    return "fatal"


def reason_of(status: int, body: str) -> str:
    """从错误体里挖一句话（OpenAI 形状：{"error":{"message","type","code"}}）。"""
    try:
        data = json.loads(body)
    except Exception:  # noqa: BLE001
        return (body or "").strip()[:300] or ("HTTP %d" % status)
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()[:300]
        if isinstance(error, str) and error.strip():
            return error.strip()[:300]
        for key in ("message", "detail", "msg"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:300]
    return (body or "").strip()[:300] or ("HTTP %d" % status)


def error_code_of(body: str) -> str:
    try:
        data = json.loads(body)
    except Exception:  # noqa: BLE001
        return ""
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            code = error.get("code") or error.get("type")
            if isinstance(code, str):
                return code[:60]
        if isinstance(data.get("code"), str):
            return data["code"][:60]
    return ""


def _client(timeout: float) -> httpx.Client:
    """四项 timeout 全给（只给 connect 会让 read 变成不限，实测会 TLS 卡死）。"""
    return httpx.Client(
        timeout=httpx.Timeout(connect=20.0, read=timeout,
                              write=timeout, pool=20.0),
        follow_redirects=False,
    )


def _transient(exc: BaseException) -> bool:
    text = "%s: %s" % (type(exc).__name__, exc)
    return any(marker in text for marker in _TRANSIENT)


# ---------------------------------------------------------------------------
# 模型目录（进程内缓存）
# ---------------------------------------------------------------------------
_catalog_cache: dict = {"at": 0.0, "rows": None}
_catalog_lock = threading.Lock()


def fetch_catalog(*, force: bool = False, timeout: float = endpoints.API_TIMEOUT):
    """拉 `GET /models`。失败抛 `UpstreamError`（调用方自己回落内置快照）。"""
    now = time.time()
    with _catalog_lock:
        cached = _catalog_cache.get("rows")
        if not force and cached and (now - float(_catalog_cache.get("at") or 0)) < \
                endpoints.CATALOG_TTL:
            return cached

    url = endpoints.base_url() + endpoints.MODELS_PATH
    data = request_json(url, headers=endpoints.headers_of(),
                        timeout=timeout, what="models")
    rows = data.get("data")
    rows = rows if isinstance(rows, list) else []
    with _catalog_lock:
        _catalog_cache["rows"] = rows
        _catalog_cache["at"] = now
    return rows


def clear_catalog_cache() -> None:
    with _catalog_lock:
        _catalog_cache["rows"] = None
        _catalog_cache["at"] = 0.0


# ---------------------------------------------------------------------------
# 基础请求
# ---------------------------------------------------------------------------
def request_json(url: str, *, headers: dict, method: str = "GET",
                 body: dict | None = None, timeout: float = endpoints.API_TIMEOUT,
                 what: str = "") -> dict:
    last: BaseException | None = None
    resp = None
    for attempt in range(1, _RETRY_TIMES + 1):
        try:
            with _client(timeout) as client:
                resp = client.request(method, url, headers=headers, json=body)
            break
        except Exception as exc:  # noqa: BLE001
            if not _transient(exc):
                raise UpstreamError("网络错误: %s" % str(exc)[:200], status=0,
                                    kind="network") from exc
            last = exc
            if attempt >= _RETRY_TIMES:
                raise UpstreamError("网络错误: %s" % str(exc)[:200], status=0,
                                    kind="network") from exc
            time.sleep(_BACKOFF[min(attempt - 1, len(_BACKOFF) - 1)])
    if resp is None:  # pragma: no cover
        raise UpstreamError("网络错误: %s" % str(last)[:200], status=0,
                            kind="network")

    text = resp.text or ""
    if resp.status_code >= 400:
        kind = classify(resp.status_code, text)
        raise UpstreamError(reason_of(resp.status_code, text),
                            status=resp.status_code, kind=kind,
                            code=error_code_of(text), detail=text[:800])
    try:
        data = json.loads(text)
    except Exception as exc:  # noqa: BLE001
        raise UpstreamError("响应不是 JSON: %s" % text[:200],
                            status=resp.status_code, kind="fatal") from exc
    if not isinstance(data, dict):
        raise UpstreamError("响应不是对象", status=resp.status_code, kind="fatal")
    return data


# ---------------------------------------------------------------------------
# 对话
# ---------------------------------------------------------------------------
def chat_completions(key: str, body: dict, *,
                     timeout: float = endpoints.CHAT_TIMEOUT) -> dict:
    url = endpoints.base_url() + endpoints.CHAT_PATH
    payload = dict(body)
    payload["stream"] = False
    payload.pop("stream_options", None)
    return request_json(url, headers=endpoints.chat_headers(key),
                        method="POST", body=payload, timeout=timeout,
                        what="chat")


def chat_stream(key: str, body: dict, *, timeout: float = endpoints.CHAT_TIMEOUT,
                prime: bool = True):
    """透传上游 SSE，逐帧 yield `(下发字节, 解析后的 dict 或 None)`。

    `prime=True` 时先建连并读第一帧，把上游 4xx/5xx 变成可捕获异常
    （否则错误只会变成「200 + 空流」，客户端拿不到原因）。
    """
    url = endpoints.base_url() + endpoints.CHAT_PATH
    headers = endpoints.chat_headers(key)
    payload = dict(body)
    payload["stream"] = True

    def _frames():
        with _client(timeout) as client:
            with client.stream("POST", url, headers=headers, json=payload) as resp:
                if resp.status_code >= 400:
                    raw = resp.read().decode("utf-8", "replace")
                    raise UpstreamError(reason_of(resp.status_code, raw),
                                        status=resp.status_code,
                                        kind=classify(resp.status_code, raw),
                                        code=error_code_of(raw), detail=raw[:800])
                for line in resp.iter_lines():
                    if line is None:
                        continue
                    text = str(line).rstrip()
                    if not text.strip():
                        continue
                    if text.startswith(":"):
                        continue
                    data = text[5:].strip() if text.startswith("data:") else text
                    if data == "[DONE]":
                        yield (b"data: [DONE]\n\n", None)
                        return
                    try:
                        obj = json.loads(data)
                    except Exception:  # noqa: BLE001
                        yield (("%s\n\n" % text).encode("utf-8"), None)
                        continue
                    yield (("data: %s\n\n"
                            % json.dumps(obj, ensure_ascii=False)).encode("utf-8"),
                           obj if isinstance(obj, dict) else None)

    if not prime:
        yield from _frames()
        return

    gen = _frames()
    first = None
    for attempt in range(1, _RETRY_TIMES + 1):
        try:
            first = next(gen, None)
            break
        except UpstreamError:
            raise
        except Exception as exc:  # noqa: BLE001
            if not _transient(exc):
                raise UpstreamError("网络错误: %s" % str(exc)[:200], status=0,
                                    kind="network") from exc
            if attempt >= _RETRY_TIMES:
                raise UpstreamError("网络错误: %s" % str(exc)[:200], status=0,
                                    kind="network") from exc
            time.sleep(_BACKOFF[min(attempt - 1, len(_BACKOFF) - 1)])
            gen = _frames()
    if first is not None:
        yield first
    yield from gen