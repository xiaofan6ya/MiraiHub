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

from admin.models import AdminDeviceBan, AdminSession

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

def _revoke_row(db: Session, row: AdminSession, reason: str) -> None:
    """吊销单条会话（不提交，由调用方决定事务边界）。"""
    if row.revoked_at is not None:
        return
    row.revoked_at = datetime.utcnow()
    row.revoked_reason = (reason or "revoked")[:48]


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
                   single: bool = True, sid: str | None = None,
                   max_devices: int = 0, device_label: str = "") -> AdminSession:
    """新建一条会话；`single=True` 时先吊销该账号的全部旧会话。

    ## 多设备（2026-11-10）

    `single=False` 时不再踢掉旧会话 —— 手机、平板、几台电脑可以同时在线。
    「凭证不可共享」这件事由**设备绑定**保证（Cookie 拷到别的机器过不了
    `bind_hash` 校验），不需要靠「只许一台设备」来实现。

    `max_devices > 0` 时保留上限：超出就按 `last_seen_at` 从旧到新踢掉最老的
    一条。防的是「同一账号被反复登录刷出几十条会话」，不是防多设备。
    """
    if single:
        revoke_all(db, subject, "superseded_by_new_login")
    elif max_devices > 0:
        active = (db.query(AdminSession)
                  .filter(AdminSession.subject == subject,
                          AdminSession.revoked_at.is_(None),
                          AdminSession.expires_at > datetime.utcnow())
                  .order_by(AdminSession.last_seen_at.asc())
                  .all())
        # 给「这条新会话」留一个位置
        overflow = len(active) - max_devices + 1
        for row in active[:max(0, overflow)]:
            _logger.info("设备数超上限，吊销最旧会话 sid=%s subject=%s",
                         row.sid[:12], subject)
            _revoke_row(db, row, "device_limit")
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
        device_label=(device_label or describe_ua(user_agent))[:120],
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def describe_ua(user_agent: str) -> str:
    """把 UA 压成「浏览器 / 系统」，给登录管理界面看。

    不做精细解析：这里只需要「一眼认出是哪台机器」，
    真正的设备身份由 `wb_device` Cookie 的摘要承担。
    """
    ua = str(user_agent or "")
    if not ua:
        return "未知设备"
    low = ua.lower()
    if "android" in low:
        os_name = "Android"
    elif "iphone" in low or "ipad" in low or "ios" in low:
        os_name = "iOS"
    elif "windows" in low:
        os_name = "Windows"
    elif "mac os" in low or "macintosh" in low:
        os_name = "macOS"
    elif "linux" in low:
        os_name = "Linux"
    else:
        os_name = "其它"
    for token, name in (("edg/", "Edge"), ("chrome/", "Chrome"),
                        ("firefox/", "Firefox"), ("safari/", "Safari"),
                        ("okhttp", "OkHttp"), ("python-requests", "脚本")):
        if token in low:
            browser = name
            break
    else:
        browser = "未知浏览器"
    # 安卓 WebView / 客户端外壳里常带 App 标识
    if "miraihub" in low:
        browser = "MiraiHub 客户端"
    return "%s / %s" % (browser, os_name)


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


# ---------------------------------------------------------------------------
# 登录管理：设备列表 / 踢下线 / 拉黑
# ---------------------------------------------------------------------------
def list_sessions(db: Session, subject: str, *, current_sid: str = "",
                  include_revoked: bool = False, limit: int = 50) -> list[dict]:
    """登录设备一览（按最近活跃排序）。

    `current_sid` 用来标出「就是你现在这台」，前端据此不给自己画踢人按钮。
    """
    now = datetime.utcnow()
    q = db.query(AdminSession).filter(AdminSession.subject == subject)
    if not include_revoked:
        q = q.filter(AdminSession.revoked_at.is_(None))
    rows = (q.order_by(AdminSession.last_seen_at.desc())
            .limit(max(1, min(int(limit or 50), 200)))
            .all())
    out: list[dict] = []
    for row in rows:
        expired = row.expires_at is not None and row.expires_at <= now
        if row.revoked_at is not None:
            state = "revoked"
        elif expired:
            state = "expired"
        else:
            state = "active"
        out.append({
            "sid": row.sid,
            "current": bool(current_sid) and row.sid == current_sid,
            "state": state,
            "label": row.device_label or "未知设备",
            "ip": row.ip or "",
            "fp": (row.fp or "")[:12],
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
            "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
            "revoked_reason": row.revoked_reason or "",
        })
    return out


def revoke_others(db: Session, subject: str, keep_sid: str) -> int:
    """踢掉除当前会话外的所有设备（"一键登出其它设备"）。"""
    now = datetime.utcnow()
    rows = (db.query(AdminSession)
            .filter(AdminSession.subject == subject,
                    AdminSession.revoked_at.is_(None),
                    AdminSession.sid != keep_sid)
            .all())
    for row in rows:
        row.revoked_at = now
        row.revoked_reason = "kick_others"
    if rows:
        db.commit()
        _logger.info("已踢下线 %d 台设备 subject=%s", len(rows), subject)
    return len(rows)


def ban_device(db: Session, *, bind_hash: str, fp: str = "", label: str = "",
               ip: str = "", reason: str = "", subject: str = "") -> AdminDeviceBan:
    """把一台设备拉黑，并立刻吊销它现有的会话。"""
    row = (db.query(AdminDeviceBan)
           .filter(AdminDeviceBan.bind_hash == bind_hash,
                   AdminDeviceBan.revoked_at.is_(None))
           .first())
    if row is None:
        row = AdminDeviceBan(bind_hash=bind_hash or "", fp=(fp or "")[:64],
                             label=(label or "")[:120], ip=(ip or "")[:64],
                             reason=(reason or "")[:200])
        db.add(row)
    else:
        row.reason = (reason or row.reason or "")[:200]
        row.revoked_at = None
    # 该设备的现有会话立刻失效
    if bind_hash:
        live = (db.query(AdminSession)
                .filter(AdminSession.bind_hash == bind_hash,
                        AdminSession.revoked_at.is_(None))
                .all())
        for session in live:
            _revoke_row(db, session, "device_banned")
    db.commit()
    db.refresh(row)
    _logger.warning("设备已拉黑 subject=%s label=%s ip=%s", subject, label, ip)
    return row


def unban_device(db: Session, ban_id: int) -> bool:
    row = db.query(AdminDeviceBan).filter(AdminDeviceBan.id == ban_id).first()
    if row is None or row.revoked_at is not None:
        return False
    row.revoked_at = datetime.utcnow()
    db.commit()
    return True


def banned_for(db: Session, *, bind_hash: str = "", fp: str = "") -> AdminDeviceBan | None:
    """这台设备是否在黑名单里（登录前调用）。"""
    q = db.query(AdminDeviceBan).filter(AdminDeviceBan.revoked_at.is_(None))
    if bind_hash:
        row = q.filter(AdminDeviceBan.bind_hash == bind_hash).first()
        if row is not None:
            return row
    if fp:
        row = q.filter(AdminDeviceBan.fp == fp).first()
        if row is not None:
            return row
    return None


def list_bans(db: Session, *, active_only: bool = True, limit: int = 100) -> list[dict]:
    q = db.query(AdminDeviceBan)
    if active_only:
        q = q.filter(AdminDeviceBan.revoked_at.is_(None))
    rows = q.order_by(AdminDeviceBan.created_at.desc()).limit(limit).all()
    return [{
        "id": row.id, "label": row.label or "未知设备", "ip": row.ip or "",
        "fp": (row.fp or "")[:12], "reason": row.reason or "",
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
    } for row in rows]


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
