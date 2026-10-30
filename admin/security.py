"""安全：管理后台 JWT 登录 + API Key 校验与配额判断。"""
import base64
import hashlib
import hmac
import os
from datetime import datetime, timedelta

import jwt
from fastapi import Header, HTTPException, Request

from admin.config import settings
from admin.db import SessionLocal
from admin.models import ApiKey


# ---------------------------------------------------------------------------
# 管理后台登录
# ---------------------------------------------------------------------------

def create_admin_token() -> str:
    exp = datetime.utcnow() + timedelta(hours=settings.JWT_EXPIRE_HOURS)
    return jwt.encode({"sub": settings.ADMIN_USERNAME, "exp": exp},
                      settings.JWT_SECRET, algorithm="HS256")


def verify_admin_token(token: str | None):
    if not token:
        raise HTTPException(status_code=401, detail="未登录")
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
        local_runtime = os.getenv("WORKBUDDY_DESKTOP") == "1" or os.getenv("WORKBUDDY_ANDROID") == "1"
        if "exp" not in payload and not (local_runtime and payload.get("local_shell") is True):
            raise HTTPException(status_code=401, detail="无效令牌")
        if payload.get("sub") != settings.ADMIN_USERNAME:
            raise HTTPException(status_code=401, detail="无效令牌")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="无效令牌")


def require_admin(request: Request,
                  x_admin_token: str = Header(default=None, alias="X-Admin-Token")):
    """FastAPI 依赖：校验令牌，并用同源会话 Cookie 兜底。"""
    try:
        verify_admin_token(x_admin_token)
    except HTTPException:
        # A browser refresh can retain the HttpOnly session while its old
        # localStorage token has expired. Keep the session usable in that case.
        cookie_token = request.cookies.get("admin_session")
        if not cookie_token or cookie_token == x_admin_token:
            raise
        verify_admin_token(cookie_token)
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