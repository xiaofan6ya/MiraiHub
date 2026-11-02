"""自助注册入口的**后台生成凭证**（邀请码）。

为什么需要它
------------
``/join`` 与 ``/api/join/*`` 曾经是完全公开的入口：任何人都能把自己的
上游账号塞进本机号池。2026-11-02 的入侵就是这么发生的 ——
攻击者把自己的账号昵称改成 XSS 载荷，走这条通道入库；受害者一打开后台
控制台，载荷就执行，token 被外传到攻击者的 C2。

原来的 docstring 写着「改用邀请码鉴权（未配置邀请码则整个入口关闭）」，
但代码里**从来没有实现过**。这个模块补上它，并保证语义是 **fail-closed**：

    * 后台一张有效凭证都没生成 → **整个自助入口关闭**（返回 404，不泄露原因）
    * 生成了凭证 → 请求必须携带其中一张才放行
    * 凭证可以限次、限时间、随时撤销

设计取舍
--------
* **凭证只存哈希**（PBKDF2），数据库被拖走也无法反推出明文邀请码。
  管理员在后台生成时只看到一次明文，之后只能看到哈希前缀 —— 这是刻意的：
  凭证泄露的窗口 = 生成到使用的这段时间，而不是永久。
* **明文只在生成响应里返回一次**，不写日志、不进 URL（URL 会被浏览器历史、
  Referer、nginx access log 记录）。
* **校验用常量时间比较**，避免时序侧信道。
* **一次注册消耗一张凭证的次数**：次数耗尽即失效。
* **限次与限 IP 分离**：IP 维度另外由 ``join_limits`` 的速率限制兜底。
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import string
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from admin.models import JoinInvite

_logger = logging.getLogger("admin.join_invite")

#: 明文邀请码字符集：去掉容易混淆的 0/O/1/l/I。
_ALPHABET = "".join(c for c in string.ascii_letters + string.digits if c not in "O0lI1")

#: 邀请码前缀，便于肉眼识别与日志排查。
_CODE_PREFIX = "MH"

#: PBKDF2 迭代次数（与 admin/security.py 的密码哈希保持同一量级）。
_PBKDF2_ITER = 100_000

#: 凭证有效期上限（天）。防止管理员生成一张"永久有效"的码忘了撤销。
_MAX_VALID_DAYS = 90


def _hash_code(code: str) -> str:
    """返回可持久化的哈希串：pbkdf2$<iter>$<salt_b64>$<dk_b64>。"""
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", code.encode("utf-8"), salt, _PBKDF2_ITER)
    import base64
    return f"pbkdf2${_PBKDF2_ITER}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_code(code: str, stored_hash: str) -> bool:
    """常量时间比较口令与哈希。"""
    if not code or not stored_hash:
        return False
    try:
        _, iter_s, salt_b64, dk_b64 = stored_hash.split("$", 3)
        import base64
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(dk_b64)
        dk = hashlib.pbkdf2_hmac("sha256", code.encode("utf-8"), salt, int(iter_s))
    except Exception:
        return False
    return hmac.compare_digest(dk, expected)


def _random_code(length: int = 16) -> str:
    body = "".join(secrets.choice(_ALPHABET) for _ in range(length))
    return f"{_CODE_PREFIX}-{body}"


def generate_code() -> str:
    """生成一张邀请码的明文（仅在生成时返回一次）。"""
    return _random_code()


def create_invite(db: Session, *, note: str = "", max_uses: int = 1,
                  valid_days: int = 7, created_by: str = "") -> tuple[JoinInvite, str]:
    """在后台生成一张凭证。

    返回 ``(记录, 明文邀请码)``。明文**只在这里出现一次**，之后无法取回。
    """
    days = max(1, min(int(valid_days or 1), _MAX_VALID_DAYS))
    uses = max(1, int(max_uses or 1))
    code = generate_code()
    row = JoinInvite(
        code_hash=_hash_code(code),
        code_prefix=code[:8],
        note=(note or "")[:200],
        max_uses=uses,
        used_count=0,
        enabled=1,
        expires_at=datetime.utcnow() + timedelta(days=days),
        created_by=(created_by or "")[:120],
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    _logger.info("已生成邀请码 prefix=%s uses=%d days=%d by=%s",
                 row.code_prefix, uses, days, created_by or "-")
    return row, code


def list_invites(db: Session, limit: int = 200) -> list[JoinInvite]:
    """列出凭证（按创建时间倒序）。明文不在其中 —— 只有哈希前缀。"""
    return (db.query(JoinInvite)
            .order_by(JoinInvite.id.desc())
            .limit(max(1, min(int(limit or 200), 1000)))
            .all())


def revoke_invite(db: Session, invite_id: int) -> bool:
    """撤销一张凭证（置为失效）。返回是否命中。"""
    row = db.get(JoinInvite, int(invite_id))
    if row is None:
        return False
    row.enabled = 0
    db.commit()
    _logger.info("已撤销邀请码 id=%s prefix=%s", invite_id, row.code_prefix)
    return True


def delete_invite(db: Session, invite_id: int) -> bool:
    """彻底删除一张凭证。"""
    row = db.get(JoinInvite, int(invite_id))
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def _active_invite(db: Session, code: str) -> Optional[JoinInvite]:
    """找到与明文口令匹配、且当前有效的一张凭证。

    有效 = enabled 且未过期 且 次数未耗尽。
    匹配走常量时间比较；候选集通常只有后台生成的那几张。
    """
    if not code:
        return None
    now = datetime.utcnow()
    candidates = (db.query(JoinInvite)
                  .filter(JoinInvite.enabled == 1)
                  .order_by(JoinInvite.id.desc())
                  .limit(500)
                  .all())
    for row in candidates:
        if row.expires_at is not None and row.expires_at < now:
            continue
        if int(row.max_uses or 0) > 0 and int(row.used_count or 0) >= int(row.max_uses):
            continue
        if verify_code(code, row.code_hash):
            return row
    return None


def has_any_invite(db: Session) -> bool:
    """是否至少存在一张**当前可用**的凭证。

    这是 fail-closed 的判据：一张都没有 → 整个自助入口关闭。
    """
    now = datetime.utcnow()
    rows = (db.query(JoinInvite)
            .filter(JoinInvite.enabled == 1)
            .limit(500)
            .all())
    for row in rows:
        if row.expires_at is not None and row.expires_at < now:
            continue
        if int(row.max_uses or 0) > 0 and int(row.used_count or 0) >= int(row.max_uses):
            continue
        return True
    return False


def _matches_any_record(db: Session, code: str) -> bool:
    """这张码本身存在过（哪怕已撤销 / 已过期 / 次数耗尽）。

    用来把「码本身失效」与「入口整体关闭」区分开 —— 前者报 invalid，
    后者报 closed。两者对外都是 404，区别只体现在服务端日志里。
    """
    if not code:
        return False
    rows = (db.query(JoinInvite)
            .order_by(JoinInvite.id.desc())
            .limit(500)
            .all())
    for row in rows:
        if verify_code(code, row.code_hash):
            return True
    return False


def check_invite(db: Session, code: str) -> tuple[bool, str]:
    """校验一张凭证。

    返回 ``(是否通过, 原因)``。原因只用于服务端日志，不返回给调用方。

    原因取值：
      * ``missing``  —— 请求里根本没带凭证
      * ``closed``   —— 后台一张可用凭证都没有，整个自助入口处于关闭状态
      * ``invalid``  —— 凭证不存在 / 已撤销 / 已过期 / 次数耗尽
      * ``ok``       —— 通过
    """
    if not code:
        return False, "missing"
    row = _active_invite(db, code)
    if row is not None:
        return True, "ok"
    # 这张码本身存在过 → 是「这张码失效了」，不是「入口关了」。
    if _matches_any_record(db, code):
        return False, "invalid"
    # 码完全不认识：若后台一张可用凭证都没有，说明入口整体关闭。
    if not has_any_invite(db):
        return False, "closed"
    return False, "invalid"


def consume_invite(db: Session, code: str) -> bool:
    """消耗一次凭证次数（成功入库后调用）。失败返回 False。"""
    row = _active_invite(db, code)
    if row is None:
        return False
    row.used_count = int(row.used_count or 0) + 1
    db.commit()
    _logger.info("邀请码已消耗 prefix=%s used=%d/%s",
                 row.code_prefix, row.used_count, row.max_uses)
    return True


def invite_status(row: JoinInvite) -> dict:
    """给后台列表用的状态摘要。"""
    now = datetime.utcnow()
    expired = row.expires_at is not None and row.expires_at < now
    exhausted = int(row.max_uses or 0) > 0 and int(row.used_count or 0) >= int(row.max_uses)
    if not row.enabled:
        state = "disabled"
    elif expired:
        state = "expired"
    elif exhausted:
        state = "exhausted"
    else:
        state = "active"
    return {
        "id": row.id,
        "code_prefix": row.code_prefix,
        "note": row.note or "",
        "max_uses": row.max_uses,
        "used_count": row.used_count,
        "enabled": row.enabled,
        "state": state,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "created_by": row.created_by or "",
    }
