"""首次初始化：没有管理员账号时引导用户创建（桌面端 / 安卓端重点路径）。

## 为什么需要这条公开路由

桌面 exe 与安卓 apk 不带 `.env`，用户也没有「编辑配置文件再重启」的能力。
以前启动器随机生成一个口令塞进环境变量，用户**永远不知道密码**，只能在壳内
免登录使用；想从局域网里的浏览器登录管理台就彻底做不到。

现在未配置时页面跳到 `/setup` 引导创建账号，创建完立刻拿到令牌进入控制台。

## 安全边界（重要）

这是一个**未鉴权**的写接口，唯一的准入门槛是「当前还没有管理员凭据」
（见 :func:`admin.admin_account.set_credentials` 的 fail-closed 检查）。
如果只靠这一点，任何人在任何时刻先于管理员访问一次就能把后台据为己有。
因此再叠两道限制：

1. **默认只允许本机**（直连对端为回环地址，或直连对端在 ``ADMIN_TRUSTED_PROXY_IPS``
   内 —— 反代场景下才采信 ``X-Real-IP`` / ``X-Forwarded-For``，且必须是回环）。
   服务器部署要从外部初始化时，显式设 ``ADMIN_SETUP_ALLOW_REMOTE=1``。
2. 复用登录失败计数（IP + 用户名维度）。创建成功后入口永久关闭
   （``POST`` 返回 409，``GET /api/setup/state`` 也不再说「可创建」）。

本机判定必须和登录限流用同一套 IP 解析（``client_ip_from_request``），
否则会出现「限流认为你在 A，初始化认为你在 B」这类自相矛盾的绕过。
"""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from admin import admin_account
from admin.db import get_db
from admin.ratelimit import (clear_failures, client_ip_from_request, is_locked,
                             record_failure)
from admin.security import create_admin_token

logger = logging.getLogger("admin.setup")

router = APIRouter(prefix="/api/setup", tags=["setup"])

#: 用户名/密码长度下限。与前端表单保持一致，后端才是权威。
USERNAME_MIN = 3
USERNAME_MAX = 64
PASSWORD_MIN = 8
PASSWORD_MAX = 128

#: 弱口令黑名单：这些口令一旦被用上，后台基本等于敞开（服务默认监听 0.0.0.0）。
_WEAK_PASSWORDS = {
    "12345678", "123456789", "1234567890", "password", "passw0rd", "qwertyui",
    "admin123", "admin1234", "administrator", "iloveyou", "abc12345",
    "11111111", "00000000", "88888888", "miraihub", "workbuddy",
}


class SetupIn(BaseModel):
    username: str = Field(default="", max_length=USERNAME_MAX)
    password: str = Field(default="", max_length=PASSWORD_MAX)


def _loopback(value: str) -> bool:
    value = (value or "").strip().strip("[]")
    if not value:
        return False
    if value in ("::1", "localhost"):
        return True
    return value.startswith("127.")


def _remote_allowed() -> bool:
    return os.getenv("ADMIN_SETUP_ALLOW_REMOTE", "0") == "1"


def _client_is_local(request: Request) -> bool:
    """直连对端是否为本机（反代场景用可信 IP 解析结果）。"""
    direct = (request.client.host if request.client else "") or ""
    if not _loopback(direct):
        return False
    # 直连是反代时，真正来源要看 client_ip_from_request；它只在可信代理时才采信头。
    return _loopback(client_ip_from_request(request))


def _guard(request: Request) -> str:
    """校验「可以初始化」并返回权威 IP。不通过一律抛 HTTPException。"""
    ip = client_ip_from_request(request)
    if not _remote_allowed() and not _client_is_local(request):
        # 404 而非 403：不向外部探测者确认「这个实例还没初始化」。
        logger.warning("初始化接口被非本机请求拒绝 ip=%s", ip)
        raise HTTPException(status_code=404, detail="页面不存在")
    locked, wait = is_locked(ip, "setup")
    if locked:
        raise HTTPException(status_code=429, detail=f"尝试过于频繁，请 {wait} 秒后再试",
                            headers={"Retry-After": str(wait)})
    return ip


def _validate(body: SetupIn) -> tuple[str, str]:
    username = (body.username or "").strip()
    password = body.password or ""
    if len(username) < USERNAME_MIN:
        raise HTTPException(status_code=400, detail=f"用户名至少 {USERNAME_MIN} 个字符")
    if len(password) < PASSWORD_MIN:
        raise HTTPException(status_code=400, detail=f"密码至少 {PASSWORD_MIN} 个字符")
    if password.lower() in _WEAK_PASSWORDS:
        raise HTTPException(status_code=400, detail="该密码过于常见，请换一个")
    if password.lower() == username.lower():
        raise HTTPException(status_code=400, detail="密码不能与用户名相同")
    # 用户名会进 JWT 的 sub 与日志，收敛成可见字符集，避免日志注入与显示歧义。
    if not all(ch.isalnum() or ch in "._-@ " for ch in username):
        raise HTTPException(status_code=400,
                            detail="用户名只能包含字母、数字与 . _ - @ 空格")
    return username, password


@router.get("/state")
def setup_state(request: Request):
    """公开：前端据此决定「显示引导」还是「显示登录」。

    刻意只回布尔与来源，不回用户名 —— 未初始化时是公开接口，
    回显已有用户名等于给爆破者送一个已知的一半。
    """
    status = admin_account.state()
    if status["configured"]:
        return {"configured": True, "needs_setup": False,
                "source": status["source"], "local": _client_is_local(request)}
    # 未初始化：只有本机（或显式放开）才有资格看到引导入口。
    local = _client_is_local(request) or _remote_allowed()
    return {"configured": False, "needs_setup": local, "source": "",
            "local": _client_is_local(request),
            "username_min": USERNAME_MIN, "password_min": PASSWORD_MIN,
            "db_ready": status["db_ready"]}


@router.post("")
def create_admin(body: SetupIn, request: Request, db: Session = Depends(get_db)):
    """创建首个管理员账号，成功即返回可直接使用的后台令牌。"""
    ip = _guard(request)
    # 先判重、后校验入参：已配置的实例对任何输入都只该回「已存在」，
    # 否则一堆 400 会变成「这个实例可以初始化」的探测信号。
    if admin_account.state(force=True)["configured"]:
        raise HTTPException(status_code=409, detail="管理员账号已存在，请直接登录")
    username, password = _validate(body)
    try:
        admin_account.set_credentials(username, password)
    except admin_account.AlreadyConfigured:
        raise HTTPException(status_code=409, detail="管理员账号已存在，请直接登录") from None
    except Exception:
        record_failure(ip, "setup")
        logger.exception("初始化管理员账号失败")
        raise HTTPException(status_code=500, detail="保存失败，请检查数据库后重试") from None

    clear_failures(ip, "setup")
    logger.info("管理员账号已创建 username=%s ip=%s", username, ip)
    # 与 /api/login 一致：同时回令牌与 HttpOnly 会话 Cookie，
    # 这样浏览器后续的 /admin 文档请求也能通过门禁。
    from admin.server import _admin_cookie_params
    from admin.config import settings
    from fastapi.responses import JSONResponse

    token = create_admin_token()
    response = JSONResponse({"ok": True, "token": token, "username": username})
    response.set_cookie(**_admin_cookie_params(request, settings.JWT_EXPIRE_HOURS * 3600),
                        value=token)
    return response
