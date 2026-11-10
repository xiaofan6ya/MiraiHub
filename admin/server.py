"""管理后台 FastAPI 入口：登录、挂载路由、托管前端静态页、启动时建库建表。"""
import hashlib
import logging
import os
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
                           proxy, proxies, schedules, sessions as sessions_router,
                           stats, sync)
from admin.ratelimit import (client_ip_from_request, clear_failures,
                             is_locked, record_failure)
from admin.security import (
    create_admin_token,
    hash_password,
    require_admin,
    verify_admin_token,
    verify_password,
)
from admin.gateway_limits import GatewayAdmissionMiddleware
from admin.ip_access import IPBlockMiddleware
from admin.routers import join_invites
join_invites_router = join_invites.router
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

    覆盖 `/join` 与全部 `/api/join/*`，但**排除**三类：
      * `/api/join/ticket/exchange` —— 它就是来换 Cookie 的；
      * `/api/join/gate/*`         —— 后台总闸，自己要求管理员鉴权；
      * `/api/join/invites*`       —— 后台邀请码管理，同上。

    返回体与"页面不存在"完全一致：不告诉探测者"入口存在但你没票"。
    """
    path = request.url.path
    is_join = (path in ("/join", "/join/", "/static/join.html")
               or path.startswith("/api/join/"))
    if is_join and not (
        path == "/api/join/ticket/exchange"
        or path.startswith("/api/join/gate/")
        or path.startswith("/api/join/invites")
    ):
        try:
            from admin import join_gate as _jg
            with SessionLocal() as db:
                allowed = _jg.check_access(db, request.cookies.get(_jg.COOKIE_NAME, ""))
        except Exception:  # noqa: BLE001 - 数据库不可用时 fail-closed
            allowed = False
        if not allowed:
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
    return await call_next(request)


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
        (_p.startswith("/admin") and not _p.startswith("/admin/assets"))
        or _p in ("/join", "/join/", "/", "/j")
        or (_p.startswith("/static/") and _p.endswith(".html"))
    )
    if _is_html_doc:
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; "
            "font-src 'self' data:; "
            "connect-src 'self'; "
            "media-src 'self' blob:; "
            "object-src 'none'; base-uri 'self'; "
            "frame-ancestors 'none'; form-action 'self'",
        )
    return response


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
app.add_middleware(IPBlockMiddleware)

# 异常行为守卫必须注册在**最后**（= 最外层）。
#
# 原因：`add_middleware` 是前插，越晚注册越靠外。守卫要在所有业务逻辑之前
# 完成「是否已被封」的判定，否则被封的 IP 仍会打穿到应用层再被拒。
#
# 为什么已有的 `IPBlockMiddleware` 不够：它内部用 `_is_public_gateway_path()`
# 过滤，只保护 `/v1/*`；而事故中攻击者扫的是 `/api/accounts`，两者路径体系
# 完全不同，等于完全没有风控。本中间件不区分路径，全站生效。
from admin.abuse_guard import AbuseGuardMiddleware
app.add_middleware(AbuseGuardMiddleware)


@app.on_event("shutdown")
async def _flush_ip_activity():
    import asyncio
    from admin import ip_activity
    if ip_activity._task is not None and not ip_activity._task.done():
        ip_activity._task.cancel()
        await asyncio.gather(ip_activity._task, return_exceptions=True)
    await asyncio.to_thread(ip_activity.flush)


@app.on_event("startup")
async def _start_upstream_connections():
    # Own the embedded runtime's pool; recreate it after a service restart.
    if os.getenv("WORKBUDDY_ANDROID") == "1":
        from admin import upstream_http
        await upstream_http.start(app, proxy.HTTP_LIMITS)
    # 「设置 → 运行配置」的覆盖值不在这里套用：这个 startup 钩子注册在
    # `_startup` 之前，此刻 init_db 还没跑，首次启动（尤其是全新安装的 APK）
    # 必然撞上 `no such table: system_settings`，覆盖值要等到下次重启才生效。
    # 套用动作已挪到 `_startup` 里 init_db 之后，见 `_apply_runtime_overrides`。


@app.on_event("shutdown")
async def _stop_upstream_connections():
    from admin import upstream_http
    await upstream_http.stop(app)


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


@app.on_event("shutdown")
async def _stop_loop_watch():
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
if os.getenv("WORKBUDDY_ANDROID") != "1" and not env_guard.IS_PROD:
    # 逆向产物管理（任意目录创建 + 递归读取）：**生产不挂载**。
    # 中间件还有一道 404，双保险 —— 见 env_guard.PROD_BLOCKED_PREFIXES。
    from admin.routers import app_source
    app.include_router(app_source.router)
app.include_router(login_router.router)
app.include_router(releases.router)
# 自助上传页（无需后台登录态）：复刻 WorkBuddy 手机号登录页，
# 让「发给朋友」这一诉求成立 —— 对方不需要知道后台口令。
app.include_router(login_router.public_router)
app.include_router(join_invites_router)
# join 总闸：状态 / 开关 / 签发访问链接 + 公开的票据兑换端点。
from admin.routers import join_gate as join_gate_api
app.include_router(join_gate_api.router)


@app.on_event("startup")
def _startup():
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

    # 环境画像：出问题时第一眼要能确认「这台到底是 prod 还是 dev」。
    logger.info(
        "运行环境 APP_ENV=%s prod=%s 会话单例=%s 设备绑定=%s CSRF=%s 文件系统运维=%s",
        env_guard.APP_ENV, env_guard.IS_PROD,
        settings.SESSION_SINGLE, settings.SESSION_BIND_DEVICE,
        settings.CSRF_ENABLED, env_guard.fs_ops_allowed(),
    )

    # 数据库不可用（例如 MySQL 被 OOM 杀掉正在重启）时**不能**让启动失败：
    # 这里抛异常会让 uvicorn 直接退出进程，nginx 侧变成 502，且需要人工重启。
    # 改为重试 + 降级启动，进程保持存活，数据库恢复后自动可用。
    db_ok = wait_database_ready()
    if db_ok:
        try:
            init_db()
        except Exception:
            logger.exception("建表/迁移失败，服务继续启动（数据库可用后重启即可）")
    else:
        logger.error("数据库当前不可用，服务以降级模式启动：请求将返回 503，恢复后自动自愈")

    # 套用「设置 → 运行配置」里保存的覆盖值（exe/apk 没有 .env，全靠这）。
    # 位置必须在 init_db 之后：首次启动时表还不存在，早跑一次只会打一条
    # `no such table: system_settings` 的告警，然后覆盖值静默失效到下次重启。
    if db_ok:
        try:
            from admin.runtime_settings import apply_overrides
            with SessionLocal() as db:
                applied = apply_overrides(db)
            if applied:
                logger.info("运行配置已套用 %d 项覆盖", applied)
        except Exception as exc:  # 库表异常不影响启动
            logger.warning("运行配置套用失败：%s", exc)

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

    # 把后台保存的路径覆盖注入 wb_install（安装目录 / 逆向产物目录）。
    # 必须在下面「客户端版本发现」之前：否则首个请求拿到的还是扫描结果，
    # 后台改过的安装目录要等一次 refresh 才生效。
    # 环境变量优先级仍高于这里的覆盖值（见 wb_install._ov）。
    if db_ok:
        try:
            from admin import wb_paths
            ov = wb_paths.load_into_wb()
            if any(ov.values()):
                logger.info("已应用后台路径设置：%s", ov)
        except Exception:
            logger.exception("应用后台路径设置失败（继续用自动扫描）")

    # 打印客户端版本发现结果：出站 UA 与桌面事件指纹都靠它。
    # 显示「兜底」就说明没找到本机安装包 —— 服务能跑，但 UA 报的版本
    # 不一定与真实客户端一致，属于需要关注的状态，所以要在启动日志里可见。
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
    except Exception:
        logger.exception("客户端版本发现失败（不影响启动）")

    # 调度器只在数据库可用时启动（否则它每 15s 都会失败一次，纯属噪音）
    if db_ok:
        # 会话表清理：只删「过期超过 30 天」的历史行，活跃会话不受影响。
        try:
            with SessionLocal() as db:
                session_store.purge(db)
        except Exception:
            logger.exception("清理历史会话失败（不影响启动）")
        from admin.scheduler import start_scheduler
        start_scheduler()
        try:
            from admin.post_login import resume
            resume()
        except Exception:
            logger.exception("恢复登录后自动任务失败（不影响服务启动）")


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


@app.post("/api/login")
def login(
    username: str = Form(...),
    password: str = Form(...),
    request: Request = None,
):
    # 权威 IP：仅当直连对端在 ADMIN_TRUSTED_PROXY_IPS 内时才采信反代头的
    # X-Real-IP / X-Forwarded-For（nginx 侧须 proxy_set_header X-Real-IP
    # $remote_addr 覆写）。否则所有请求看起来都是反代 IP，登录锁会退化成
    # 「全局锁」，任何人打几次错误密码就能把真管理员锁在门外。
    ip = client_ip_from_request(request)
    fingerprint = _login_fingerprint(request) if request else None
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
        record_failure(ip, username, fingerprint)
        logger.warning("后台登录失败 ip=%s username=%s fp=%s", ip, username[:40], fingerprint or "-")
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    # 密码正确：清空成对与 IP 全局计数（指纹维度也一并清，避免误伤后续正常登录）。
    clear_failures(ip, username, fingerprint)
    logger.info("后台登录成功 ip=%s fp=%s", ip, fingerprint or "-")

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

    response = JSONResponse({
        "ok": True,
        "expires_in": settings.JWT_EXPIRE_HOURS * 3600,
        # 会话标识只用于排障与「当前设备」展示，不是凭证。
        "sid": session_row.sid[:8],
        # 多设备模式：明确告诉前端「当前在线设备数」，方便它提示
        "multi_device": not settings.SESSION_SINGLE,
    })
    _attach_auth_cookies(response, request, token, device_id, new_device)
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
                         device_id: str, new_device: bool) -> None:
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
    csrf = request.cookies.get(session_store.CSRF_COOKIE) or session_store.new_csrf_token()
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


@app.post("/api/login/logout")
@app.post("/outlogin")
def login_logout(request: Request):
    """**真实退出**：服务端销毁会话行，再清 Cookie。

    与升级前的区别：原来只 `delete_cookie`，JWT 依旧有效 —— 谁在那之前
    复制过 Cookie，退出之后照样能用到过期。现在 `revoked_at` 一落库，
    同一张令牌在任何设备上都立刻失效。
    """
    revoked = _revoke_current_session(request)
    logger.info("后台退出登录 revoked=%s", revoked)
    response = JSONResponse({"ok": True, "revoked": revoked})
    _clear_auth_cookies(response)
    return response


@app.get("/outlogin", include_in_schema=False)
def outlogin_page(request: Request):
    """浏览器地址栏直接访问的退出入口（GET 便于收藏/书签）。

    只做「吊销当前会话 + 跳回登录页」；跨站 `<img>` 这类子资源请求不会被
    浏览器带上 SameSite=Lax 的 Cookie，所以不会被静默踢下线。
    """
    _revoke_current_session(request)
    response = RedirectResponse(url="/admin", status_code=303)
    _clear_auth_cookies(response)
    return response


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
    return {"ok": True, "revoked_sessions": revoked}


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
    return (request.url.scheme == "https"
            or (request.headers.get("x-forwarded-proto") or "").lower() == "https")


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


@app.get("/admin")
@app.get("/admin/")
def admin_index(request: Request):
    """登录门禁：有会话 Cookie → 完整控制台；否则只给中性登录壳。"""
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


@app.get("/join")
@app.get("/join/")
def join_index(request: Request, db: Session = Depends(get_db)):
    """公开注册页（复刻 WorkBuddy 手机号登录页）。

    **门禁已上移到 `_join_gate_middleware`**：这里不再用「库里有没有邀请码」
    当开关 —— 那套隐式判据会和 `/api/join/*`、`join_invites` 三处漂移。
    现在总闸状态只有一个来源（`admin/join_gate.py`），本函数只负责发文件。
    """
    # 资源 `?v=` 同样由服务端按内容哈希注入（见 stamp_asset_urls），
    # 免得改完 phone-session.js / vendor 库还要手工改版本号。
    return HTMLResponse(_stamped_page(STATIC_DIR / "join.html"),
                        headers={"Cache-Control": "no-store, no-cache, must-revalidate"})


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