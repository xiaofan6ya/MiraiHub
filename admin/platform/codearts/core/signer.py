"""华为云 API 网关签名（SDK-HMAC-SHA256）。

## 为什么必须自己实现

CodeArts 的每一个业务接口（对话、模型目录、额度）都不是 Bearer Token，
而是**华为云 AK/SK 请求签名**：

    Authorization: SDK-HMAC-SHA256 Access=<AK>, SignedHeaders=<...>, Signature=<hex>
    X-Sdk-Date:    20261104T050000Z
    X-Security-Token: <STS 临时凭证>

签名串是标准的「规范请求 -> 待签字符串 -> HMAC-SHA256」三段式。
参数错一个字，服务端只回一句 `verify ak sk signature fail`，**不告诉你是
哪一段错了** —— 所以这里把每一步都做成可独立断言的纯函数，便于测试。

## 实测踩到的两个坑（都写过一遍才通）

1. **规范 URI 要补尾斜杠**：客户端 `sign()` 里是
   `path.split('/').map(encodeURIComponent).join('/')` 再补 `/`，
   所以 `/api/v2/chat/completions` 的规范 URI 是
   `/api/v2/chat/completions/`（**结尾多一个斜杠**）。少了它 = 签名失败。

2. **拼接路径段必须用 `/`**：写成 `''.join(...)` 会得到
   `apiv2chatcompletions/`，同样只回 `signature fail`。
   这类错误没有提示，只能靠「与客户端逐字节对比规范请求」发现。

## 参考实现来源

`<安装目录>/resources/app.asar` → `out/main/index.js` 的 `sign()`。
本文件是它的等价 Python 版本（含同样的规范化细节）。
"""
from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import parse_qsl, quote, urlparse

#: 固定算法名（华为云 APIG 只认这一个）
ALGORITHM = "SDK-HMAC-SHA256"


def _canonical_uri(path: str) -> str:
    """规范 URI：逐段 URL 编码后拼回，并**补尾斜杠**。

    客户端实现是 `parts.map(encodeURIComponent).join("/")` + 补 `/`，
    所以首段（空串）保留，得到以 `/` 开头的路径。
    """
    parts = str(path or "/").split("/")
    encoded = "/".join(quote(part, safe="") if index else part
                       for index, part in enumerate(parts))
    if not encoded.startswith("/"):
        encoded = "/" + encoded
    return encoded if encoded.endswith("/") else encoded + "/"


def _canonical_query(query: str) -> str:
    """规范查询串：按 `key=value` 编码后**字典序排序**用 `&` 连接。"""
    if not query:
        return ""
    pairs = ["%s=%s" % (quote(str(k), safe=""), quote(str(v), safe=""))
             for k, v in parse_qsl(query, keep_blank_values=True)]
    return "&".join(sorted(pairs))


def _canonical_headers(headers: dict) -> tuple[str, str]:
    """规范头 + 参与签名的头名列表（小写、按字典序、`key:value\\n`）。"""
    items = sorted((str(k).lower(), str(v).strip()) for k, v in headers.items())
    canonical = "".join("%s:%s\n" % (key, value) for key, value in items)
    signed = ";".join(key for key, _ in items)
    return canonical, signed


def _utc_stamp(now: float | None = None) -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now if now is not None else time.time()))


def sign(method: str, url: str, headers: dict | None = None,
         body: str | bytes = "", access_key: str = "",
         secret_key: str = "", *, now: float | None = None) -> dict:
    """给一次请求签名，返回**要真正发出去的头**（含 Authorization）。

    `body` 必须与实际发送的字节**完全一致**（JSON 分隔符、空格、键序都算），
    否则服务端算出的 body hash 对不上。调用方应先把 body 序列化成字符串
    再交给本函数，并原样发送。
    """
    payload = body.encode("utf-8") if isinstance(body, str) else (body or b"")
    parsed = urlparse(url)
    timestamp = _utc_stamp(now)

    merged = {str(k): str(v) for k, v in (headers or {}).items()}
    merged["host"] = parsed.netloc
    merged["X-Sdk-Date"] = timestamp

    canonical_headers, signed_headers = _canonical_headers(merged)
    # 上游允许用 `X-Sdk-Content-Sha256` 覆盖 body hash（大文件场景）。
    # 这里照抄：带了就用它，否则现算。
    override = next((str(v) for k, v in merged.items()
                     if k.lower() == "x-sdk-content-sha256"), "")
    body_hash = override or hashlib.sha256(payload).hexdigest()

    canonical_request = "\n".join([
        method.upper(),
        _canonical_uri(parsed.path),
        _canonical_query(parsed.query),
        canonical_headers,
        signed_headers,
        body_hash,
    ])
    string_to_sign = "%s\n%s\n%s" % (
        ALGORITHM, timestamp,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest())
    signature = hmac.new(secret_key.encode("utf-8"),
                         string_to_sign.encode("utf-8"),
                         hashlib.sha256).hexdigest()

    out = {key: value for key, value in merged.items() if key != "host"}
    out["Authorization"] = "%s Access=%s, SignedHeaders=%s, Signature=%s" % (
        ALGORITHM, access_key, signed_headers, signature)
    return out


def canonical_request(method: str, url: str, headers: dict | None = None,
                      body: str | bytes = "", *,
                      now: float | None = None) -> str:
    """只产出规范请求串（排障/测试用：与客户端逐字节比对就看它）。"""
    payload = body.encode("utf-8") if isinstance(body, str) else (body or b"")
    parsed = urlparse(url)
    merged = {str(k): str(v) for k, v in (headers or {}).items()}
    merged["host"] = parsed.netloc
    merged["X-Sdk-Date"] = _utc_stamp(now)
    canonical_headers, signed_headers = _canonical_headers(merged)
    return "\n".join([
        method.upper(),
        _canonical_uri(parsed.path),
        _canonical_query(parsed.query),
        canonical_headers,
        signed_headers,
        hashlib.sha256(payload).hexdigest(),
    ])
