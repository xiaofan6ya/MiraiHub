"""join 入口总闸：显式开关 + 代次（epoch） + 短码访问票据。

为什么需要它
------------
升级前 `/join` 的开关是**隐式**的：库里有没有一张有效邀请码，就等于开没开。
这带来三个问题：

1. 没有审计 —— 谁在什么时候把入口打开了，日志里查不到；
2. 没有"切换即全量作废" —— 关掉再打开，之前发出去的码继续有效（最长 90 天）；
3. 判据分散在 `/join`、`/api/join/*`、`join_invites` 三处，很容易漂移。

现在改成三层，职责单一：

    总闸（本模块）   只回答"入口开不开"，显式开关 + 代次
    访问票据         只回答"能不能打开这个页面"，10 位短码、按时间有效、可重复打开
    邀请码           只回答"能不能完成注册"，限次、≤7 天、绑定代次

任意一次 toggle（包括 开→关→开）都会推进 epoch，之前签发的
票据 / 邀请码 / Cookie **立即全部失效**。epoch 只增不减，所以回滚
不会让旧凭证复活。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import string
import time
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from admin import env_guard
from admin.config import settings
from admin.models import JoinInvite, JoinTicket, SystemSetting

_logger = logging.getLogger("admin.join_gate")

GATE_ENABLED_KEY = "join_gate_enabled"
GATE_EPOCH_KEY = "join_gate_epoch"
COOKIE_NAME = "join_access"


def _get(db: Session, key: str, default: str = "") -> str:
    row = db.get(SystemSetting, key)
    return (row.value if row is not None and row.value is not None else default)


def _set(db: Session, key: str, value) -> None:
    row = db.get(SystemSetting, key)
    if row is None:
        db.add(SystemSetting(key=key, value=str(value)))
    else:
        row.value = str(value)


# ---------------------------------------------------------------------------
# 状态
# ---------------------------------------------------------------------------

def is_open(db: Session) -> bool:
    """总闸是否打开。**默认关闭**（fail-closed）。"""
    return _get(db, GATE_ENABLED_KEY, "0") == "1"


def epoch(db: Session) -> int:
    try:
        return max(1, int(_get(db, GATE_EPOCH_KEY, "1") or "1"))
    except (TypeError, ValueError):
        return 1


def active_ticket_count(db: Session) -> int:
    now = datetime.utcnow()
    return (db.query(JoinTicket)
            .filter(JoinTicket.epoch == epoch(db),
                    JoinTicket.used_at.is_(None),
                    JoinTicket.expires_at > now)
            .count())


def active_invite_count(db: Session) -> int:
    now = datetime.utcnow()
    rows = (db.query(JoinInvite)
            .filter(JoinInvite.enabled == 1, JoinInvite.epoch == epoch(db))
            .all())
    return sum(1 for r in rows
               if (r.expires_at is None or r.expires_at >= now)
               and (int(r.max_uses or 0) <= 0
                    or int(r.used_count or 0) < int(r.max_uses or 0)))


# ---------------------------------------------------------------------------
# 签名
# ---------------------------------------------------------------------------

def _secret() -> bytes:
    """票据签名密钥。

    生产必须独立配置 `JOIN_TICKET_SECRET`（启动断言会检查），**不复用**
    会话密钥：票据 15 分钟、会话 4 小时，共用一个密钥意味着任一泄露点
    同时打穿两套体系。本地开发允许派生一份，方便零配置跑起来。
    """
    configured = (os.getenv("JOIN_TICKET_SECRET") or "").strip()
    if len(configured) >= 32:
        return configured.encode("utf-8")
    if env_guard.IS_PROD:
        raise RuntimeError("JOIN_TICKET_SECRET 必须配置为 >=32 字节的独立随机串")
    base = (os.getenv("ADMIN_JWT_SECRET") or "dev-insecure-jwt-secret-change-me")
    return ("join-ticket|" + base).encode("utf-8")


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(payload: dict) -> str:
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    sig = _b64(hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{sig}"


def _verify(token: str) -> dict | None:
    try:
        body, sig = (token or "").split(".", 1)
        want = _b64(hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, want):
            return None
        payload = json.loads(_unb64(body))
        if int(payload.get("exp") or 0) < int(time.time()):
            return None
        return payload
    except Exception:  # noqa: BLE001 - 任何解析失败都按无效票据处理
        return None


# ---------------------------------------------------------------------------
# 操作
# ---------------------------------------------------------------------------

def toggle(db: Session, enable: bool, *, operator: str = "", ip: str = "") -> dict:
    """开 / 关入口。**无论开关方向，都推进代次并作废旧凭证。**"""
    old_epoch = epoch(db)
    new_epoch = old_epoch + 1
    now = datetime.utcnow()

    # 双保险：代次已经能让旧码失效，这里再显式置 enabled=0，
    # 方便后台列表一眼看出"这批码是上一代的"。
    revoked_invites = (db.query(JoinInvite)
                       .filter(JoinInvite.enabled == 1)
                       .update({JoinInvite.enabled: 0}, synchronize_session=False))
    revoked_tickets = (db.query(JoinTicket)
                       .filter(JoinTicket.used_at.is_(None))
                       .update({JoinTicket.used_at: now}, synchronize_session=False))

    _set(db, GATE_EPOCH_KEY, new_epoch)
    _set(db, GATE_ENABLED_KEY, "1" if enable else "0")
    db.commit()

    _logger.warning(
        "join 总闸切换 open=%s epoch %s->%s 邀请码失效=%s 票据作废=%s by=%s ip=%s",
        bool(enable), old_epoch, new_epoch, revoked_invites, revoked_tickets,
        operator or "-", ip or "-")
    return {
        "open": bool(enable),
        "epoch": new_epoch,
        "previous_epoch": old_epoch,
        "revoked_invites": int(revoked_invites or 0),
        "revoked_tickets": int(revoked_tickets or 0),
        "revoked_all": True,
    }


#: 短码字符集：去掉 0/O/1/l/I 这类肉眼易混的字符。
#: 10 位 ≈ 49 bit 熵 —— 24 小时内暴力枚举不可能的规模，同时又能手抄/口述。
_CODE_ALPHABET = "".join(c for c in (string.ascii_uppercase + string.digits)
                         if c not in "O0I1L")
_CODE_LEN = 10


def _new_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LEN))


def issue_tickets(db: Session, count: int = 1, *, operator: str = "") -> list[str]:
    """签发访问短码（明文只在这里出现一次）。

    2026-11-05 改动：
      * **短码代替签名串** —— 原来是 ~180 字符的 base64 签名，发微信/短信都被
        截断，复制也容易漏字符；现在是 10 位大写字母数字（如 `K7M2QX9RTB`）。
      * **按时间失效，不再一次性** —— 有效期内可以重复打开；作废靠
        「关闸 / 再开闸（代次 +1）」或到期。一次性票据在"发出去对方没立刻点"
        的场景下只会逼管理员反复签发。
    安全性：码是随机 10 位（≈49 bit），库里只存原文（要按码查询，无法哈希），
    但表里没有任何凭证价值 —— 拿到码也只能打开注册页，还要邀请码才能注册。
    """
    if not is_open(db):
        raise RuntimeError("join 未开启，拒绝签发票据")
    e = epoch(db)
    ttl = int(getattr(settings, "JOIN_TICKET_TTL", 86400))
    out: list[str] = []
    for _ in range(max(1, min(int(count or 1), 50))):
        code = _new_code()
        db.add(JoinTicket(
            jti=code, epoch=e, created_by=(operator or "")[:120],
            expires_at=datetime.utcnow() + timedelta(seconds=ttl),
        ))
        out.append(code)
    db.commit()
    return out


def exchange(db: Session, token: str) -> str | None:
    """访问码 → `join_access` Cookie 值。失败返回 None（对外一律 404）。

    兼容两种形态：
      * **短码**（新）：直接按 `jti` 查；有效期内可重复使用，不写 used_at；
      * **签名串**（旧，含 `.`）：走原来的校验，保持一次性语义。
    """
    raw = (token or "").strip()
    if not raw or not is_open(db):
        return None
    now = datetime.utcnow()
    current_epoch = epoch(db)

    if "." in raw:                      # 旧版签名票据（兼容窗口）
        payload = _verify(raw)
        if not payload or int(payload.get("e") or 0) != current_epoch:
            return None
        row = (db.query(JoinTicket)
               .filter(JoinTicket.jti == payload.get("j"),
                       JoinTicket.epoch == payload.get("e"),
                       JoinTicket.used_at.is_(None))
               .first())
        if row is None or row.expires_at is None or row.expires_at < now:
            return None
        row.used_at = now
        db.commit()
        jti = row.jti
    else:                               # 新版短码：可重复使用
        row = (db.query(JoinTicket)
               .filter(JoinTicket.jti == raw,
                       JoinTicket.epoch == current_epoch,
                       JoinTicket.used_at.is_(None))
               .first())
        if row is None or row.expires_at is None or row.expires_at < now:
            return None
        jti = row.jti

    return _sign({"e": current_epoch, "j": jti,
                  "exp": int(time.time()) + int(getattr(settings, "JOIN_ACCESS_TTL", 14400))})


def check_access(db: Session, cookie: str) -> bool:
    """`/join` 与 `/api/join/*` 的统一门禁。"""
    if not is_open(db):
        return False
    payload = _verify(cookie)
    if not payload:
        return False
    try:
        return int(payload.get("e") or 0) == epoch(db)
    except (TypeError, ValueError):
        return False
