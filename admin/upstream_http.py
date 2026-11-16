"""Share TLS configuration while keeping each request's cookies isolated."""
from contextlib import asynccontextmanager
import asyncio
import hashlib
import logging
import os
import ssl
import sys
import threading
from datetime import datetime, timezone

import certifi
import httpx
import httpcore  # `_prepare_upload` 的 trace 里要按 httpcore 的网络/超时异常分类
from admin import request_metrics, upstream_dispatch

_ssl_context = None
_ssl_lock = threading.Lock()
_logger = logging.getLogger(__name__)
UPLOAD_CHUNK_BYTES = 64 * 1024


def upstream_trust_env() -> bool:
    """上游请求是否继承系统代理/环境变量（HTTP_PROXY 等）。

    默认 **False**：上游请求直连，不读 Windows 注册表里的系统代理
    （Steam++、各类加速器会把 `127.0.0.1:10793` 之类塞进注册表做 TLS 中间人，
    于是每个上游请求都证书验证失败 —— 见 issue #5）。

    需要走代理才能出网的环境显式置
    `ADMIN_UPSTREAM_TRUST_ENV=1` 开启。
    """
    return os.environ.get("ADMIN_UPSTREAM_TRUST_ENV", "").strip().lower() in (
        "1", "true", "yes", "on")


import urllib.request  # noqa: E402  (延迟导入，仅给 urllib 系平台复用)


def build_opener(trust_env: bool | None = None, *extra_handlers):
    """构造带「系统+ certifi 信任库」且默认**不走系统代理**的 urllib opener。

    平台模块（`admin/platform/<name>/`）的上游调用走 `urllib.request` 而非 httpx，
    同样要躲开加速器做的 TLS 中间人（issue #5）。

    `trust_env=None` 时按 `upstream_trust_env()` 决定要不要读系统代理。

    ## `extra_handlers`：必须在这里传，不能在返回后 `add_handler`

    `urllib.request.build_opener()` 会把默认处理器（含 `HTTPRedirectHandler`）
    一并装进去。之后再 `opener.add_handler(X)` 只是**追加**，同一个 302
    默认处理器先命中就先处理 —— 于是「禁止跟随重定向」这类覆盖根本不生效。

    只有把自定义 handler 作为 `build_opener()` 的位置参数传进去，urllib 才会
    用「子类替换父类」的规则把它顶替掉默认处理器。

    2026-11-16 真机踩到：Marvis 换登录态时上游回 302 + Set-Cookie（token 在
    cookie 里），`_NoRedirect` 是用 `add_handler` 加的、没顶替默认处理器，
    请求照样被跟随到落地页「登录成功」HTML，cookie 丢掉，报
    「登录接口没有下发 accesstoken」。
    """
    if trust_env is None:
        trust_env = upstream_trust_env()
    handlers = [urllib.request.HTTPSHandler(context=ssl_context())]
    if not trust_env:
        handlers.append(urllib.request.ProxyHandler({}))
    for handler in extra_handlers:
        handlers.append(handler)
    return urllib.request.build_opener(*handlers)


_opener = None


def urllib_opener():
    global _opener
    if _opener is None:
        _opener = build_opener()
    return _opener


class _UploadStream(httpx.AsyncByteStream):
    """Let write timeouts measure stalled progress, even for large JSON bodies.

    HTTPX encodes json= as one chunk. A single write can then exceed the timeout
    while a slow connection is still making progress. Keep Content-Length and
    bytes unchanged; only bound the size of each socket write.
    """
    def __init__(self, stream, evidence):
        self.stream = stream
        self.evidence = evidence

    async def __aiter__(self):
        async for data in self.stream:
            for offset in range(0, len(data), UPLOAD_CHUNK_BYTES):
                chunk = data[offset:offset + UPLOAD_CHUNK_BYTES]
                yield chunk
                # The transport resumed us after accepting this chunk. This is
                # local write progress, not proof that the upstream processed it.
                self.evidence['written_bytes'] += len(chunk)
        self.evidence['complete'] = True

    async def aclose(self):
        await self.stream.aclose()



_ssl_lock = threading.Lock()

#: `install_ssl_defaults()` 是否已经接管过默认上下文（幂等标记）。
_SSL_DEFAULT_INSTALLED = False


def ssl_context():
    """上游 TLS 信任库 = **系统证书库 + certifi**。

    只信任 certifi 会在「系统代理自动装入根证书」的机器（企业/加速器）
    上把本来能连通的请求判成证书失败；只信任系统库又会把连通性押在
    操作系统证书集合上（容器/精简系统常缺失）。两者叠加最稳。

    Windows 上额外把系统 ROOT 存储并入（CPython 私有 API，失败不影响主流程）。
    """
    global _ssl_context
    with _ssl_lock:
        if _ssl_context is None:
            ctx = ssl.create_default_context()
            try:
                ctx.load_verify_locations(cafile=certifi.where())
            except Exception:  # noqa: BLE001 - certifi 缺失时退回系统库
                pass
            if sys.platform == "win32":
                try:
                    ssl._load_windows_store_certs(ctx, "ROOT")  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001 - 私有 API，环境差异大
                    pass
            _ssl_context = ctx
        return _ssl_context


def warm_ssl_context() -> bool:
    """在**后台线程**里预建共享 SSL 上下文；返回是否真的起了预热线程。

    ## 为什么必须预热

    `ssl_context()` 里那句 `create_default_context()` 在 Windows 上会
    `load_default_certs()` → `_load_windows_store_certs()`，把系统 ROOT / CA
    证书库整个枚举一遍。这台机器实测单次 **3~13 秒**（Personal 存储里有几百张
    证书，且 USB-Key 驱动会让枚举更慢）。

    放在请求路径上就是「点一下全站卡住十几秒」；放在启动的后台线程里，
    只影响启动本身，等客户端连上来时上下文早就建好了。
    """
    if _ssl_context is not None:
        return False

    def _warm() -> None:
        try:
            ssl_context()
            _logger.info("共享 SSL 上下文预热完成（系统证书库 + certifi）")
        except Exception:  # noqa: BLE001 - 预热失败不影响启动，请求时还会再试
            _logger.warning("共享 SSL 上下文预热失败（首连可能变慢）",
                            exc_info=True)

    threading.Thread(target=_warm, name="ssl-warm", daemon=True).start()
    return True


def install_ssl_defaults() -> bool:
    """让**所有**没显式传 `context` 的 HTTPS 连接复用 `ssl_context()`。

    ## 事故现场（2026-11-16）

    `admin.loop_watch` 抓到的主线程栈有两条，其实是同一件事：

        ssl.py _load_windows_store_certs  ← 13.3 秒
        ssl.py read                      ← 13.5 秒

    第一条是 `http.client.HTTPSConnection.__init__`：`context=None` 时它调
    `ssl._create_default_https_context()`，**每次新连接都新建一个默认上下文**，
    于是 Windows 证书库被反复枚举。

    第二条是平台客户端用 `urllib.request.urlopen()` 同步等上游响应 ——
    单 worker 的 uvicorn 事件循环被钉住，控制台、导入、/v1 全部无响应。

    ## 补丁做了什么

    把「默认 HTTPS 上下文」换成共享的 `ssl_context()`（系统库 + certifi，
    建一次缓存）。于是：

      * 新连接不再重扫证书库（13 秒 → 0）；
      * 信任库口径统一（不会「这个平台认系统证书、那个平台只认 certifi」）。

    只改**默认**上下文：显式传 `context=` / `verify=` 的调用方（httpx、
    `build_opener()`）完全不受影响。
    """
    global _SSL_DEFAULT_INSTALLED
    original = getattr(ssl, "_create_default_https_context", None)
    if original is None:            # 非 CPython / 被裁剪的解释器：不需要补
        return False
    with _ssl_lock:
        if _SSL_DEFAULT_INSTALLED:
            return False

        def _shared_default(*args, **kwargs):
            try:
                return ssl_context()
            except Exception:       # noqa: BLE001 - 建不出来就退回标准行为
                return original(*args, **kwargs)

        ssl._create_default_https_context = _shared_default  # type: ignore[attr-defined]
        _SSL_DEFAULT_INSTALLED = True
    _logger.info("已接管默认 HTTPS 上下文：新连接复用共享信任库（不再重扫系统证书库）")
    return True


class _BorrowedTransport(httpx.AsyncBaseTransport):
    def __init__(self, transport, *, recovery=None, budget=None, limits=None, scope=None):
        self.transport = transport
        self.recovery = recovery if recovery is not None else {'used': False}
        self.budget = budget
        self.limits = limits
        self.private = []
        self.scope = scope if scope is not None else {}

    async def handle_async_request(self, request):
        captured = await _prepare_upload(request, self.scope)
        try:
            response = await self.transport.handle_async_request(request)
            if captured is not None:
                captured['http_status'] = response.status_code
            request_metrics.mark_upstream_response(response.status_code)
            return response
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadError,
                httpx.RemoteProtocolError) as error:
            if captured is not None:
                captured['error'] = type(error).__name__
            request_metrics.mark_network_error(error)
            # Only replay an in-memory request before ANY response headers.
            # Read/write timeouts and errors while consuming a response are not
            # replayed here. A lost response can still mean upstream accepted
            # the POST; this is bounded recovery, not exactly-once delivery.
            try:
                request.content
            except httpx.RequestNotRead:
                raise error
            if self.recovery['used'] or (self.budget is not None and not self.budget.available()):
                raise
            self.recovery['used'] = True
            if self.budget is not None:
                self.budget.consume()
            request_metrics.mark_connection_recovery()
            await asyncio.sleep(.2)
            fresh = httpx.AsyncHTTPTransport(limits=self.limits, verify=ssl_context(),
                                             trust_env=upstream_trust_env())
            self.private.append(fresh)
            # A new pool guarantees the retry cannot reuse the broken socket.
            try:
                captured = await _prepare_upload(request, self.scope)
                response = await fresh.handle_async_request(request)
                if captured is not None:
                    captured['http_status'] = response.status_code
                request_metrics.mark_upstream_response(response.status_code)
                return response
            except httpx.TransportError as retry_error:
                if captured is not None:
                    captured['error'] = type(retry_error).__name__
                request_metrics.mark_network_error(retry_error)
                raise
        except httpx.TransportError as error:
            if captured is not None:
                captured['error'] = type(error).__name__
            raise

    async def aclose(self):
        # The application owns the pool, not an individual request.
        for transport in self.private:
            await transport.aclose()
        self.private.clear()


def select_account(scope, account):
    # Duplicate pool rows for one UID share the same upstream clock.
    uid = str(account.uid or '').strip()
    scope['_upstream_account_key'] = ('uid:' + hashlib.sha256(uid.encode()).hexdigest()
                                      if uid else 'id:' + str(account.id))


async def _prepare_upload(request, scope=None):
    captured = None
    try:
        from admin.request_payload import capture_upstream
        captured = capture_upstream(request)
    except Exception:
        _logger.warning('Optional upstream request capture failed')
    account_key = (scope or {}).get('_upstream_account_key')
    length = request.headers.get('content-length', '')
    evidence = {'body_bytes': int(length) if length.isdigit() else None,
                'written_bytes': 0, 'complete': False}
    if captured is not None:
        captured['upload'] = evidence
    record = request_metrics.current.get()
    if record is not None:
        uploads = record.setdefault('upstream_uploads', [])
        if len(uploads) < 12:
            uploads.append(evidence)
    stream = request.stream.stream if isinstance(request.stream, _UploadStream) else request.stream
    request.stream = _UploadStream(stream, evidence)
    # Attach timings to the actual request; no URLs, headers or bodies retained.
    existing_trace = request.extensions.get('_upload_original_trace', request.extensions.get('trace'))
    request.extensions['_upload_original_trace'] = existing_trace
    sync_trace = request_metrics.network_trace()
    async def trace(name, info):
        # Gate at the real HTTP write, after DNS/TCP/TLS/pool acquisition.
        # Gating before connection setup can bunch different-speed sockets.
        if name in ('http11.send_request_headers.started', 'http2.send_request_headers.started'):
            await upstream_dispatch.before_send(scope or {}, account_key)
            evidence['dispatch_at'] = datetime.now(timezone.utc).isoformat(timespec='milliseconds')
            if captured is not None:
                captured['dispatch_at'] = evidence['dispatch_at']
        if name in ('http11.receive_response_headers.complete', 'http2.receive_response_headers.complete') and captured is not None:
            returned = info.get('return_value')
            if isinstance(returned, tuple) and len(returned) > 1 and isinstance(returned[1], int):
                captured['http_status'] = returned[1]
        sync_trace(name, info)
        if name.endswith('.failed') and isinstance(info.get('exception'),
                                                   (httpcore.NetworkError, httpcore.TimeoutException)):
            evidence['error'] = type(info['exception']).__name__
            if name.endswith('send_request_body.failed'):
                _logger.warning('Upstream upload failed (%s): body_bytes=%s written_bytes=%s',
                                evidence['error'], evidence['body_bytes'], evidence['written_bytes'])
        if existing_trace is not None:
            await existing_trace(name, info)
    request.extensions['trace'] = trace
    return captured


async def start(app, limits):
    # Reuse a few recent connections instead of doing DNS/TCP/TLS on every
    # mobile call. Recovery below is limited to one pre-header disconnect.
    mobile_limits = httpx.Limits(max_connections=limits.max_connections,
                               max_keepalive_connections=4, keepalive_expiry=30)
    app.state.upstream_transport = httpx.AsyncHTTPTransport(
        limits=mobile_limits, verify=ssl_context(), trust_env=upstream_trust_env())


async def stop(app):
    transport = getattr(app.state, 'upstream_transport', None)
    app.state.upstream_transport = None
    if transport is not None:
        await transport.aclose()


@asynccontextmanager
async def client(request, timeout, limits, *, retry_budget=None):
    # Direct route tests can create a Request without an application in scope.
    app = request.scope.get('app') if hasattr(request, 'scope') else getattr(request, 'app', None)
    transport = getattr(getattr(app, 'state', None), 'upstream_transport', None)
    kwargs = {'timeout': timeout, 'limits': limits}
    scope = getattr(request, 'scope', {})
    recovery = scope.setdefault('_upstream_recovery', {'used': False})
    owned = transport is None
    if owned:
        transport = httpx.AsyncHTTPTransport(limits=limits, verify=ssl_context(),
                                             trust_env=upstream_trust_env())
    kwargs['transport'] = _BorrowedTransport(transport, recovery=recovery,
                                             budget=retry_budget, limits=limits, scope=scope)
    try:
        async with httpx.AsyncClient(**kwargs) as connection:
            yield connection
    finally:
        if owned:
            await transport.aclose()