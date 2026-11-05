"""后台会话存储：签发 / 校验 / 吊销 / 设备绑定。

一句话概括这个模块存在的理由
--------------------------
**JWT 只能证明「这张票是我们签的」，不能证明「它现在还有效」。**

2026-11-02 的事故里，攻击者从 localStorage 拿到 JWT 之后，管理员改密码、
刷新页面、甚至重新登录都没有用 —— 那张票在 24 小时内始终可兑换。
`logout` 也只是前端删掉本地副本，服务端一无所知。

现在把权威状态放进 `admin_sessions` 表：

    login   → 吊销该账号全部旧会话 → 新建一行 → 把 sid 写进 JWT
    verify  → JWT 签名/有效期 + 会话行未吊销/未过期 + 设备绑定
    logout  → revoked_at = now（服务端销毁，不是前端"假装"退出）

设备绑定用一枚独立的 `wb_device` Cookie（不是 UA 指纹）：
UA 会随浏览器升级变化，指纹会随分辨率变化，拿它们做硬绑定等于随机踢人。
一枚随机 Cookie 在「复制会话 Cookie 到另一台机器」时必然缺失，正好挡住
票据搬运；而正常用户的浏览器会一直带着它。
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from admin.models import AdminSession

_logger = logging.getLogger("admin.session_store")

#: 会话 Cookie 名（HttpOnly，JS 不可读）。
SESSION_COOKIE = "admin_session"
#: 设备标识 Cookie 名（HttpOnly，只用于绑定，不承载权限）。
DEVICE_COOKIE = "wb_device"
#: CSRF 双提交 Cookie 名（**必须**可被同源 JS 读取，故非 HttpOnly）。
CSRF_COOKIE = "wb_csrf"
#: CSRF 请求头名。
CSRF_HEADER = "X-CSRF-Token"

#: 已吊销/过期的会话保留天数（用于事后审计），之后物理删除。
_RETAIN_DAYS = 30


# ---------------------------------------------------------------------------
# 摘要工具
# ---------------------------------------------------------------------------

def token_digest(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def _ua_digest(ua: str) -> str:
    return hashlib.sha256((ua or "").encode("utf-8", "replace")).hexdigest()[:32]


def bind_digest(device_id: str) -> str:
    """设备绑定摘要。空设备 ID 一律返回空串（表示不绑定）。"""
    if not device_id:
        return ""
    return hashlib.sha256(("wb-device|" + device_id).encode("utf-8")).hexdigest()


def new_device_id() -> str:
    return secrets.token_urlsafe(32)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def new_sid() -> str:
    return secrets.token_urlsafe(24)


# ---------------------------------------------------------------------------
# 签发
# ---------------------------------------------------------------------------

def revoke_all(db: Session, subject: str, reason: str,
               except_sid: str | None = None) -> int:
    """吊销某账号的全部有效会话。返回吊销条数。

    `except_sid` 用于「续期当前会话」这种场景，登录流程一律不传 ——
    登录的含义就是「之前那些票作废」。
    """
    now = datetime.utcnow()
    q = (db.query(AdminSession)
         .filter(AdminSession.subject == subject,
                 AdminSession.revoked_at.is_(None)))
    if except_sid:
        q = q.filter(AdminSession.sid != except_sid)
    rows = q.all()
    for row in rows:
        row.revoked_at = now
        row.revoked_reason = (reason or "revoked")[:48]
    if rows:
        db.commit()
        _logger.info("已吊销 %d 个旧会话 subject=%s reason=%s", len(rows), subject, reason)
    return len(rows)


def create_session(db: Session, *, subject: str, token: str, ip: str = "",
                   user_agent: str = "", fingerprint: str = "",
                   device_id: str = "", ttl_seconds: int = 4 * 3600,
                   single: bool = True, sid: str | None = None) -> AdminSession:
    """新建一条会话；`single=True` 时先吊销该账号的全部旧会话。"""
    if single:
        revoke_all(db, subject, "superseded_by_new_login")
    now = datetime.utcnow()
    row = AdminSession(
        sid=sid or new_sid(),
        subject=subject,
        token_hash=token_digest(token),
        bind_hash=bind_digest(device_id),
        ip=(ip or "")[:64],
        ua_hash=_ua_digest(user_agent),
        fp=(fingerprint or "")[:64],
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(seconds=max(60, int(ttl_seconds))),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------

class SessionRejected(Exception):
    """会话不可用。`reason` 只进服务端日志，不回给调用方。"""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def verify(db: Session, *, sid: str, token: str, subject: str = "",
           device_id: str | None = None, require_bind: bool = False) -> AdminSession:
    """校验会话行。任何异常都抛 `SessionRejected`，由上层转 401。"""
    if not sid:
        raise SessionRejected("missing_sid")
    row = db.query(AdminSession).filter(AdminSession.sid == sid).first()
    if row is None:
        raise SessionRejected("unknown_sid")
    if subject and row.subject != subject:
        raise SessionRejected("subject_mismatch")
    # 令牌必须与签发时那一张**逐字节一致**：防止有人拿同一个 sid 重新
    # 签一张 JWT（例如 JWT_SECRET 泄露后伪造），旧会话行仍然能挡住。
    if not secrets.compare_digest(row.token_hash, token_digest(token)):
        raise SessionRejected("token_mismatch")
    if row.revoked_at is not None:
        raise SessionRejected("revoked:" + (row.revoked_reason or "-"))
    if row.expires_at is not None and row.expires_at <= datetime.utcnow():
        raise SessionRejected("expired")
    if require_bind and row.bind_hash:
        if not device_id or not secrets.compare_digest(row.bind_hash, bind_digest(device_id)):
            # 绑定不匹配 = 会话 Cookie 被搬到了另一台设备。
            # 直接吊销，避免攻击者反复试探。
            row.revoked_at = datetime.utcnow()
            row.revoked_reason = "device_mismatch"
            db.commit()
            _logger.warning("会话设备绑定不匹配，已吊销 sid=%s", row.sid)
            raise SessionRejected("device_mismatch")
    return row


#: 活跃心跳的最小写入间隔（秒）。每个请求都 UPDATE 会把只读面板变成写库风暴。
_TOUCH_EVERY = 60.0


def touch(db: Session, row: AdminSession) -> None:
    """记录最近活跃时间。失败不影响请求。"""
    try:
        now = datetime.utcnow()
        if row.last_seen_at and (now - row.last_seen_at).total_seconds() < _TOUCH_EVERY:
            return
        row.last_seen_at = now
        db.commit()
    except Exception:  # noqa: BLE001 - 心跳写失败不该让业务请求失败
        db.rollback()


def revoke_sid(db: Session, sid: str, reason: str = "logout") -> bool:
    row = db.query(AdminSession).filter(AdminSession.sid == sid).first()
    if row is None or row.revoked_at is not None:
        return False
    row.revoked_at = datetime.utcnow()
    row.revoked_reason = (reason or "logout")[:48]
    db.commit()
    return True


def revoke_by_token(db: Session, token: str, reason: str = "logout") -> bool:
    """按令牌原文吊销（logout 用：调用方不一定还持有可解码的 JWT）。"""
    h = token_digest(token)
    row = (db.query(AdminSession)
           .filter(AdminSession.token_hash == h)
           .first())
    if row is None or row.revoked_at is not None:
        return False
    row.revoked_at = datetime.utcnow()
    row.revoked_reason = (reason or "logout")[:48]
    db.commit()
    return True


def active_session_of(db: Session, subject: str) -> AdminSession | None:
    return (db.query(AdminSession)
            .filter(AdminSession.subject == subject,
                    AdminSession.revoked_at.is_(None),
                    AdminSession.expires_at > datetime.utcnow())
            .order_by(AdminSession.id.desc())
            .first())


def purge(db: Session) -> int:
    """物理清理过期超过保留期的历史会话（定时任务调用）。"""
    cutoff = datetime.utcnow() - timedelta(days=_RETAIN_DAYS)
    rows = (db.query(AdminSession)
            .filter(AdminSession.created_at < cutoff)
            .delete(synchronize_session=False))
    if rows:
        db.commit()
        _logger.info("清理历史会话 %d 条", rows)
    return rows or 0
