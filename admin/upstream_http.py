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

#: 按平台读出口代理的环境变量名模板：`MIRAIHUB_<PLATFORM>_PROXY`。
PLATFORM_PROXY_ENV_TEMPLATE = "MIRAIHUB_%s_PROXY"


def platform_proxy_env(platform: str) -> str:
    """平台代理的环境变量名（平台名大写，非字母数字换成下划线）。"""
    name = "".join(ch if ch.isalnum() else "_"
                   for ch in str(platform or "")).upper()
    return PLATFORM_PROXY_ENV_TEMPLATE % name


def platform_proxy_url(platform: str) -> str:
    """按平台读出口代理；没配返回 ""（= 维持「无代理」直连）。

    ## 为什么按平台分环境变量

    上游会按**出口 IP** 做风控（同一出口 + 多账号容易被聚簇），而
    `build_opener()` 默认**硬禁用**代理（`ProxyHandler({})`）—— 代码层面
    没有任何办法换出口。这里开一个**按平台**的口子：只有显式配了
    `MIRAIHUB_<平台>_PROXY` 的平台才走代理，其余平台**完全保持原样**
    （直连、不读系统代理、不读全局 HTTP_PROXY），不会因为某一个平台要换
    IP 而把全站的出网路径改掉。

    ## 为什么不用 `proxy_endpoints` 表

    那张表是**按池**分的（`pool` 列 + `max_concurrency` 槽位），服务对象是
    批量任务调度，没有平台列。网关的上游调用是同步、每请求一条，套那张表
    要引入「每请求查库 + Session 跨线程」，收益不对等 —— 网关要的是
    「这个平台走哪个出口」，不是「这个任务用哪个池」。

    代理 URL 属**部署数据**，一律走环境变量，不写进仓库。
    """
    return str(os.environ.get(platform_proxy_env(platform)) or "").strip()


def build_opener(trust_env: bool | None = None, *extra_handlers,
                 proxy: str | None = None):
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

    ## `proxy`：显式出口（默认 None = 维持「硬禁代理」）

    传了 `proxy` 就装 `ProxyHandler({'http': ..., 'https': ...})` —— 必须同时
    给出两个键：上游请求全是 https，只配 `http` 等于没配。不传时行为与改动
    前**逐字节一致**（`ProxyHandler({})`，不读系统代理、不读 `HTTP_PROXY`）。
    """
    if trust_env is None:
        trust_env = upstream_trust_env()
    handlers = [_split_timeout_https_handler()]
    proxy_url = str(proxy or "").strip()
    if proxy_url:
        # 显式代理（按平台或按账号配的）：http / https 都走它。
        handlers.append(urllib.request.ProxyHandler(
            {"http": proxy_url, "https": proxy_url}))
    elif not trust_env:
        # 没配代理：保持历史行为（硬禁代理）。
        handlers.append(urllib.request.ProxyHandler({}))
    for handler in extra_handlers:
        handlers.append(handler)
    return urllib.request.build_opener(*handlers)


def _split_timeout_https_handler():
    """带「建连短超时」的 HTTPS 处理器（关闭开关时退回标准处理器）。"""
    if connect_timeout() <= 0:
        return urllib.request.HTTPSHandler(context=ssl_context())
    return _SplitTimeoutHTTPSHandler(context=ssl_context())


_opener = None


def urllib_opener():
    global _opener
    if _opener is None:
        _opener = build_opener()
    return _opener


#: 按平台的 opener 缓存。键里带**代理 URL 本身** —— 改过环境变量后下一次
#: 调用就会建新的 opener，不需要重启进程（排障时改 env 立刻生效）。
_PLATFORM_OPENERS: dict = {}
_PLATFORM_OPENERS_LOCK = threading.Lock()


def platform_opener(platform: str, *extra_handlers):
    """按平台取 opener（带缓存），代理来自 `MIRAIHUB_<平台>_PROXY`。

    没配代理时与 `urllib_opener()` 行为一致（直连、不读系统代理）。
    缓存键里带上 `extra_handlers` 的类型名，避免「禁跟随重定向」与「普通」
    两个 opener 互相串（换登录态必须用前者）。
    """
    proxy = platform_proxy_url(platform)
    key = (str(platform or ""), proxy,
           tuple(type(handler).__name__ for handler in extra_handlers))
    with _PLATFORM_OPENERS_LOCK:
        opener = _PLATFORM_OPENERS.get(key)
        if opener is None:
            opener = build_opener(None, *extra_handlers, proxy=proxy or None)
            _PLATFORM_OPENERS[key] = opener
        return opener


def reset_platform_openers() -> None:
    """丢掉按平台的 opener 缓存（测试用；改过 env 想立刻重建也用它）。"""
    with _PLATFORM_OPENERS_LOCK:
        _PLATFORM_OPENERS.clear()


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


# ---------------------------------------------------------------------------
# 建连超时与读超时分离（P3）
# ---------------------------------------------------------------------------
#
# urllib 只有一个 `timeout`：它同时是建连（TCP + CONNECT + TLS）超时和每次读的超时。
# 平台客户端为了等长生成传 600 秒，于是上游节点一挂，**建连也能等 600 秒**，
# 一个请求占着一个线程干等 10 分钟。这里让 `build_opener()` 出来的 opener 在建连
# 阶段用 `ADMIN_UPSTREAM_CONNECT_TIMEOUT`（默认 10 秒，0 = 关闭、沿用调用方的
# timeout），连上之后把 socket 改回调用方给的读超时 —— 读超时语义完全不变。

import http.client as _http_client  # noqa: E402
import socket as _socket  # noqa: E402


def connect_timeout() -> float:
    """上游建连超时（秒）；0 表示不单独限制。

    与 httpx 侧（`http_shared.cap_timeout`）共用同一个值：`ADMIN_UPSTREAM_CONNECT_TIMEOUT`，
    默认 10 秒。
    """
    from admin import http_shared

    return http_shared.connect_timeout()


class _SplitTimeoutHTTPSConnection(_http_client.HTTPSConnection):
    """建连用短超时、读用调用方超时的 HTTPSConnection。"""

    def connect(self):
        read_timeout = self.timeout
        limit = connect_timeout()
        default = read_timeout is _socket._GLOBAL_DEFAULT_TIMEOUT
        if limit and (default or read_timeout is None or read_timeout > limit):
            self.timeout = limit
        try:
            super().connect()
        finally:
            self.timeout = read_timeout
        if self.sock is not None:
            self.sock.settimeout(_socket.getdefaulttimeout() if default else read_timeout)


class _SplitTimeoutHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        kwargs = {"context": self._context}
        check = getattr(self, "_check_hostname", None)
        if check is not None and sys.version_info < (3, 12):
            kwargs["check_hostname"] = check
        return self.do_open(_SplitTimeoutHTTPSConnection, req, **kwargs)


# ---------------------------------------------------------------------------
# 共享的同步 httpx 连接池（P3）→ 见 `admin/http_shared.py`
# ---------------------------------------------------------------------------
#
# 上一轮放在这里的 `shared_sync_transport()` 用 `httpx.Client(transport=...)` 接入，
# 而 httpx 在传了 `transport=` 时**不再读环境代理**（HTTPS_PROXY / Windows 系统代理），
# 依赖环境代理出网的部署会直连失败；它还给每个平台加了 64 条的新并发上限。
# 现已移到 `http_shared.client_kwargs()`（`mounts=` 接入，代理语义与改动前一致）。


def tune_thread_limiter() -> int | None:
    """调大 anyio 默认线程令牌（P4）：默认 40 个，被流式请求占满后所有同步接口排队。

    Starlette 的同步 `def` 接口、`Depends(get_db)`、同步生成器的 StreamingResponse
    都走 anyio 的默认 CapacityLimiter；非 Marvis 平台每条流整个生命周期占一个令牌，
    并发约 40 条流时管理台和主链路一起卡住。默认调到 160，可用
    `ADMIN_ANYIO_THREAD_TOKENS` 覆盖（只会调大，不会调小）。

    **必须在事件循环线程上调用**（limiter 绑定在事件循环上）。返回生效值；失败 None。
    """
    raw = (os.environ.get("ADMIN_ANYIO_THREAD_TOKENS", "") or "").strip()
    try:
        tokens = int(raw) if raw else 160
    except ValueError:
        tokens = 160
    tokens = max(40, min(1000, tokens))
    try:
        import anyio.to_thread

        limiter = anyio.to_thread.current_default_thread_limiter()
        if limiter.total_tokens < tokens:
            limiter.total_tokens = tokens
        return int(limiter.total_tokens)
    except Exception:  # noqa: BLE001 - 不在事件循环里 / anyio 版本差异：不调
        return None


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
    # 顺带调大 anyio 线程令牌（P4）：本函数由 server 的 **async** 启动钩子调用，
    # 正好在事件循环线程上 —— limiter 只能在这里设置。
    try:
        tokens = tune_thread_limiter()
        if tokens:
            _logger.info("anyio 线程令牌 %d 个（同步接口不再被流式请求挤占）", tokens)
    except Exception:  # noqa: BLE001
        pass
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

    def _pick(self):
        """按账号复用连接池时，按本次请求的上游账号取池；取不到退回本请求私有池。"""
        if not isinstance(self.transport, _AccountPools):
            return self.transport
        picked = self.transport.for_key(self.scope.get('_upstream_account_key'))
        if picked is not None:
            return picked
        fresh = httpx.AsyncHTTPTransport(limits=self.limits, verify=ssl_context(),
                                         trust_env=upstream_trust_env())
        self.private.append(fresh)
        return fresh

    async def handle_async_request(self, request):
        captured = await _prepare_upload(request, self.scope)
        try:
            response = await self._pick().handle_async_request(request)
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


# ---------------------------------------------------------------------------
# WorkBuddy 主链路：按上游账号复用连接（perf 审计 R2 #23 / R1 B9，默认关闭）
# ---------------------------------------------------------------------------
#
# 服务器部署时 `client()` 每个请求都新建 `AsyncHTTPTransport`、用完 aclose：首字前
# 必付一次 DNS + TCP + TLS（悉尼到上游估 100–300 ms）。安卓版在 startup 建了共享池。
# 服务器上多个账号共用一条连接是风控特征，所以这里**按账号**建池（键 = 请求 scope 的
# `_upstream_account_key`，见 `select_account`），LRU 上限 `ADMIN_WB_KEEPALIVE_ACCOUNTS`
# （默认 64）；被淘汰的池等在途请求结束后才关，不会掐断正在跑的流。
# 建连前断开的复用连接由 `_BorrowedTransport` 原有的「换新池重发一次」兜底。
#
# `ADMIN_WB_KEEPALIVE=1` 开启（默认 0 = 旧行为）。先灰度：观察 4xx/风控与
# `request_metrics` 的 connection_recovery 次数再全量。

def _wb_keepalive_enabled() -> bool:
    return os.environ.get("ADMIN_WB_KEEPALIVE", "").strip().lower() in (
        "1", "true", "yes", "on")


def _env_int(name: str, default: int, low: int, high: int) -> int:
    raw = (os.environ.get(name, "") or "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = default
    return max(low, min(high, value))


class _CountedStream(httpx.AsyncByteStream):
    """响应体关闭时把所属池的在途计数减一（淘汰的池要等它归零才关）。"""

    def __init__(self, stream, done):
        self._stream = stream
        self._done = done

    async def __aiter__(self):
        async for chunk in self._stream:
            yield chunk

    async def aclose(self):
        try:
            await self._stream.aclose()
        finally:
            done, self._done = self._done, None
            if done is not None:
                done()


class _AccountPool(httpx.AsyncBaseTransport):
    def __init__(self, owner, transport):
        self.owner = owner
        self.transport = transport
        self.inflight = 0
        self.retired = False

    async def handle_async_request(self, request):
        self.inflight += 1
        try:
            response = await self.transport.handle_async_request(request)
        except BaseException:
            self._release()
            raise
        return httpx.Response(status_code=response.status_code, headers=response.headers,
                              stream=_CountedStream(response.stream, self._release),
                              extensions=response.extensions)

    def _release(self):
        self.inflight = max(0, self.inflight - 1)
        if self.retired and self.inflight == 0:
            self.owner.close_later(self.transport)


class _AccountPools(httpx.AsyncBaseTransport):
    """按上游账号键的 `AsyncHTTPTransport` LRU（只在事件循环线程上使用）。"""

    def __init__(self, limits, loop):
        from collections import OrderedDict

        self.loop = loop
        self.limits = httpx.Limits(
            max_connections=limits.max_connections,
            max_keepalive_connections=_env_int("ADMIN_WB_KEEPALIVE_PER_ACCOUNT", 2, 1, 16),
            keepalive_expiry=float(_env_int("ADMIN_WB_KEEPALIVE_EXPIRY", 30, 1, 300)))
        self.max_accounts = _env_int("ADMIN_WB_KEEPALIVE_ACCOUNTS", 64, 1, 1024)
        self.pools = OrderedDict()
        self.closing = set()

    def for_key(self, key):
        if not key:
            return None
        pool = self.pools.get(key)
        if pool is None:
            pool = _AccountPool(self, httpx.AsyncHTTPTransport(
                limits=self.limits, verify=ssl_context(), trust_env=upstream_trust_env()))
            self.pools[key] = pool
            while len(self.pools) > self.max_accounts:
                _old_key, old = self.pools.popitem(last=False)
                old.retired = True
                if old.inflight == 0:
                    self.close_later(old.transport)
        else:
            self.pools.move_to_end(key)
        return pool

    def close_later(self, transport):
        try:
            task = self.loop.create_task(transport.aclose())
        except RuntimeError:            # 事件循环已关闭
            return
        self.closing.add(task)
        task.add_done_callback(self.closing.discard)

    async def handle_async_request(self, request):  # 只经 _BorrowedTransport._pick 使用
        raise RuntimeError("_AccountPools 需要经 _BorrowedTransport 按账号路由")

    async def aclose(self):
        pools = list(self.pools.values())
        self.pools.clear()
        for pool in pools:
            try:
                await pool.transport.aclose()
            except Exception:  # noqa: BLE001
                pass


def _account_pools(state, limits):
    loop = asyncio.get_running_loop()
    pools = getattr(state, 'upstream_account_pools', None)
    if pools is None or pools.loop is not loop:
        pools = _AccountPools(limits, loop)
        state.upstream_account_pools = pools
    return pools


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
    pools = getattr(app.state, 'upstream_account_pools', None)
    app.state.upstream_account_pools = None
    if pools is not None:
        await pools.aclose()


@asynccontextmanager
async def client(request, timeout, limits, *, retry_budget=None):
    # Direct route tests can create a Request without an application in scope.
    app = request.scope.get('app') if hasattr(request, 'scope') else getattr(request, 'app', None)
    transport = getattr(getattr(app, 'state', None), 'upstream_transport', None)
    kwargs = {'timeout': timeout, 'limits': limits}
    scope = getattr(request, 'scope', {})
    recovery = scope.setdefault('_upstream_recovery', {'used': False})
    owned = transport is None
    state = getattr(app, 'state', None)
    if owned and state is not None and _wb_keepalive_enabled():
        # 服务器部署按账号复用连接（默认关，见 `_AccountPools`）；不归本请求所有
        transport = _account_pools(state, limits)
        owned = False
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