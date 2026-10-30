"""管理后台 FastAPI 入口：登录、挂载路由、托管前端静态页、启动时建库建表。"""
import hashlib
import logging
import os
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from admin.config import settings
from admin.db import SessionLocal, get_db, init_db, wait_database_ready
from admin.models import SystemSetting
from admin.routers import (accounts, credits, groups,
                           growth, keys, login as login_router, logs, models,
                           proxy, schedules, stats, sync)
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
async def _security_headers(request: Request, call_next):
    """统一安全响应头：防点击劫持 / MIME 嗅探 / 敏感头泄露。"""
    if request.url.path in _DOCS_PATHS and not settings.ENABLE_DOCS:
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    if request.url.path.startswith(("/api", "/admin")):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


app.add_middleware(
    GatewayAdmissionMiddleware,
    wait_timeout=getattr(settings, 'GATEWAY_WAIT_TIMEOUT', 15),
    request_timeout=getattr(settings, 'GATEWAY_REQUEST_TIMEOUT', 160),
    dynamic_settings=True,
)
from admin.join_limits import JoinAdmissionMiddleware
app.add_middleware(JoinAdmissionMiddleware, max_active=settings.JOIN_MAX_CONCURRENT)

from admin.request_metrics import TimingMiddleware
app.add_middleware(TimingMiddleware, persist=True)
app.add_middleware(IPBlockMiddleware)


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
    # 套用「设置 → 运行配置」里保存的覆盖值（exe/apk 无 .env，全靠这）。
    try:
        from admin.db import SessionLocal
        from admin.runtime_settings import apply_overrides
        with SessionLocal() as db:
            applied = apply_overrides(db)
        if applied:
            logging.getLogger("admin.server").info("运行配置已套用 %d 项覆盖", applied)
    except Exception as exc:  # 库表尚未初始化等情况不影响启动
        logging.getLogger("admin.server").warning("运行配置套用失败：%s", exc)


@app.on_event("shutdown")
async def _stop_upstream_connections():
    from admin import upstream_http
    await upstream_http.stop(app)

if os.getenv("WORKBUDDY_ANDROID") == "1":
    accounts.router.routes[:] = [route for route in accounts.router.routes
                                if getattr(route, "path", "") not in
                                ("/api/accounts/scan-local", "/api/accounts/import-local", "/api/accounts/{acc_id}/inject")]
app.include_router(accounts.router)
app.include_router(keys.router)
app.include_router(models.router)
app.include_router(proxy.router)
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
app.include_router(maintenance.router)
from admin.routers import runtime_config
app.include_router(runtime_config.router)
app.include_router(growth.router)
app.include_router(growth_center.router)
app.include_router(ip_management.router)
from admin.routers import client_profile
app.include_router(client_profile.router)
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
if os.getenv("WORKBUDDY_ANDROID") != "1":
    from admin.routers import app_source
    app.include_router(app_source.router)
app.include_router(login_router.router)
app.include_router(releases.router)
# 自助上传页（无需后台登录态）：复刻 WorkBuddy 手机号登录页，
# 让「发给朋友」这一诉求成立 —— 对方不需要知道后台口令。
app.include_router(login_router.public_router)


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
    token = create_admin_token()
    # 会话 Cookie：让浏览器后续的 /admin 文档请求能通过门禁拿到完整控制台。
    # （API 调用仍走 X-Admin-Token；Cookie 只服务于页面下发，HttpOnly 不可被 JS 读取。）
    response = JSONResponse({"token": token})
    response.set_cookie(**_admin_cookie_params(request, settings.JWT_EXPIRE_HOURS * 3600), value=token)
    return response


@app.post("/api/login/logout")
def login_logout(request: Request):
    """退出登录：清会话 Cookie（幂等，无需鉴权）。"""
    response = JSONResponse({"ok": True})
    response.delete_cookie(_ADMIN_COOKIE, path="/")
    return response


@app.get("/api/login/session")
def login_session(request: Request, _: bool = Depends(require_admin)):
    """用有效令牌或会话 Cookie 换取/续期会话 Cookie（登录壳升级用）。"""
    token = request.headers.get("X-Admin-Token") or ""
    cookie_token = request.cookies.get(_ADMIN_COOKIE) or ""
    if cookie_token and token:
        try:
            verify_admin_token(token)
        except HTTPException:
            # require_admin may have accepted the newer cookie after an old
            # localStorage token failed; do not overwrite it with the stale one.
            token = cookie_token
    elif not token:
        token = cookie_token
    response = JSONResponse({"ok": True})
    response.set_cookie(**_admin_cookie_params(request, settings.JWT_EXPIRE_HOURS * 3600), value=token)
    return response


class PasswordChangeIn(BaseModel):
    old_password: str
    new_password: str


@app.patch("/api/admin/password")
def change_password(body: PasswordChangeIn, _: bool = Depends(require_admin)):
    if not verify_password(body.old_password, _get_stored_hash()):
        raise HTTPException(status_code=403, detail="当前密码不正确")
    if len(body.new_password) < 6:
        raise HTTPException(status_code=400, detail="新密码长度不能少于 6 位")
    db = SessionLocal()
    try:
        row = db.query(SystemSetting).filter(SystemSetting.key == "admin_password").first()
        if row:
            row.value = hash_password(body.new_password)
        else:
            db.add(SystemSetting(key="admin_password", value=hash_password(body.new_password)))
        db.commit()
    finally:
        db.close()
    return {"ok": True}


@app.get("/")
def index_redirect():
    return RedirectResponse(url="/admin")


CONSOLE_DIR = Path(__file__).resolve().parent / "console"
#: 会话 Cookie 名。控制台本体在 admin/console/（**不经** /static 暴露），
#: 只有携带有效会话 Cookie 的请求才能拿到 —— 未登录的访客只会看到
#: static/login.html 这个中性壳页，HTML 源码里不再出现任何产品与内部实现信息。
_ADMIN_COOKIE = "admin_session"


def _local_runtime() -> bool:
    return (os.getenv("WORKBUDDY_DESKTOP") == "1"
            or os.getenv("WORKBUDDY_ANDROID") == "1")


def _console_allowed(request: Request) -> bool:
    """是否向该请求下发完整控制台：本地运行时，或带有效会话 Cookie。"""
    if _local_runtime():
        return True
    from admin.security import verify_admin_token
    try:
        verify_admin_token(request.cookies.get(_ADMIN_COOKIE))
        return True
    except Exception:
        return False


def _admin_cookie_params(request: Request, max_age: int):
    https = (request.url.scheme == "https"
             or request.headers.get("x-forwarded-proto") == "https")
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
    return FileResponse(STATIC_DIR / "login.html",
                        headers={"Cache-Control": "no-store, no-cache, must-revalidate"})


#: 控制台 HTML 的内存缓存：键为 (绝对路径, mtime, 大小)。
#: 每次请求都读 500KB 文件再拼字符串是纯浪费，而这份文件在进程生命周期内
#: 基本不变（升级会重启进程）。
_CONSOLE_SHELL_CACHE: dict = {}


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
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    cached = _CONSOLE_SHELL_CACHE.get(key)
    if cached is not None:
        return cached
    html = path.read_text(encoding="utf-8")
    marker = 'lang="zh-CN"'
    if marker in html:
        html = html.replace(marker, marker + ' class="console-authed"', 1)
    elif "<html" in html:
        html = html.replace("<html", '<html class="console-authed"', 1)
    _CONSOLE_SHELL_CACHE.clear()
    _CONSOLE_SHELL_CACHE[key] = html
    return html



@app.get("/admin/assets/{file_path:path}")
def console_asset(file_path: str, request: Request):
    """控制台私有静态资源（admin/console/*）。无会话一律 404，不暴露存在性。"""
    if not _console_allowed(request):
        raise HTTPException(status_code=404)
    root = CONSOLE_DIR.resolve()
    target = (root / file_path).resolve()
    if not str(target).startswith(str(root) + os.sep) or not target.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(target)


@app.get("/join")
@app.get("/join/")
def join_index():
    """公开注册页（复刻 WorkBuddy 手机号登录页）。

    这是要「直接发给别人」的地址：对方打开即可用手机号 + 验证码完成注册，
    服务端自动完成整条 auth 授权 —— **无需后台口令、无需邀请码、无需客户端**，
    手机浏览器即可完成。

    页面本身不含任何敏感数据；服务端唯一的防线是发码节流
    （见 admin/routers/login.py 的 `_check_sms_quota`）。
    """
    return FileResponse(STATIC_DIR / "join.html")


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