"""登录设备管理：列表 / 踢下线 / 一键踢其它 / 拉黑 / 解黑。

## 为什么需要它

用户的原话：「要么就做踢下线的强制其它设备登录的退出登录，要么是允许多台
设备登录，系统再做一个登录日志管理就行了，可以踢下线、拉黑，表格记录 ip、
最近登录时间等信息就好了」。

本版把「允许（默认）」和「可管理」两件一起做了：
  * 默认多设备在线（`SESSION_SINGLE=0`）；
  * 凭证仍然不可搬运 —— 会话行绑设备指纹，Cookie 拷到别的机器过不了校验；
  * 需要处置时来这里：踢掉某台、一键踢其它、把设备拉黑。

拉黑与踢下线的区别：踢下线只废掉**当前这次登录**，那台机器下次还能登；
拉黑是这台机器的**所有后续登录**都被拒（登录接口在签发会话前就查黑名单）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from admin import session_store
from admin.db import SessionLocal
from admin.security import require_admin

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


class BanIn(BaseModel):
    sid: str = ""
    reason: str = ""
    #: 只拉黑某个指纹（还没登录过的设备）时用
    fp: str = ""


def _current_sid(request: Request) -> str:
    """从当前请求的会话 Cookie 里解析 sid（用于标出「本机」）。"""
    token = request.cookies.get("admin_session") or ""
    if not token:
        return ""
    try:
        from admin.security import decode_admin_token

        return str(decode_admin_token(token).get("sid") or "")
    except Exception:  # noqa: BLE001 - 解不出来就不标「本机」
        return ""


@router.get("")
def list_sessions(request: Request, include_revoked: int = 0,
                  _: bool = Depends(require_admin)):
    """当前账号的登录设备列表（按最近活跃排序）。"""
    from admin.config import settings

    sid = _current_sid(request)
    db = SessionLocal()
    try:
        rows = session_store.list_sessions(
            db, settings.ADMIN_USERNAME, current_sid=sid,
            include_revoked=bool(include_revoked))
        bans = session_store.list_bans(db)
    finally:
        db.close()
    active = [r for r in rows if r["state"] == "active"]
    return {
        "ok": True,
        "multi_device": not settings.SESSION_SINGLE,
        "max_devices": getattr(settings, "SESSION_MAX_DEVICES", 0),
        "active": len(active),
        "items": rows,
        "bans": bans,
    }


@router.post("/{sid}/revoke")
def revoke_session(sid: str, _: bool = Depends(require_admin)):
    """踢掉一台设备（服务端吊销会话行，那台机器下一个请求就 401）。"""
    db = SessionLocal()
    try:
        ok = session_store.revoke_sid(db, sid, "kick_by_admin")
    finally:
        db.close()
    if not ok:
        raise HTTPException(404, detail="会话不存在或已失效")
    return {"ok": True, "sid": sid}


@router.post("/revoke-others")
def revoke_others(request: Request, _: bool = Depends(require_admin)):
    """一键踢掉除当前设备外的全部登录。"""
    from admin.config import settings

    sid = _current_sid(request)
    if not sid:
        raise HTTPException(400, detail="无法识别当前会话，请刷新后台后重试")
    db = SessionLocal()
    try:
        count = session_store.revoke_others(db, settings.ADMIN_USERNAME, sid)
    finally:
        db.close()
    return {"ok": True, "revoked": count}


@router.post("/{sid}/ban")
def ban_by_sid(sid: str, body: BanIn | None = None,
               _: bool = Depends(require_admin)):
    """把某条会话对应的**设备**拉黑，并立刻踢下线。"""
    body = body or BanIn()
    db = SessionLocal()
    try:
        row = (db.query(session_store.AdminSession)
               .filter(session_store.AdminSession.sid == sid).first())
        if row is None:
            raise HTTPException(404, detail="会话不存在")
        if not row.bind_hash and not row.fp:
            raise HTTPException(
                400, detail="这条会话没有设备标识（可能是脚本/CLI 登录），无法按设备拉黑")
        ban = session_store.ban_device(
            db, bind_hash=row.bind_hash, fp=row.fp,
            label=row.device_label or "", ip=row.ip or "",
            reason=body.reason or "手动拉黑")
        return {"ok": True, "ban_id": ban.id, "label": ban.label}
    finally:
        db.close()


@router.post("/ban-fp")
def ban_fingerprint(body: BanIn, _: bool = Depends(require_admin)):
    """按指纹拉黑（设备还没登录过、只知道指纹时用）。"""
    fp = str(body.fp or "").strip()
    if not fp:
        raise HTTPException(400, detail="需要 fp")
    db = SessionLocal()
    try:
        ban = session_store.ban_device(db, bind_hash="", fp=fp,
                                       reason=body.reason or "手动拉黑指纹")
        return {"ok": True, "ban_id": ban.id}
    finally:
        db.close()


@router.delete("/ban/{ban_id}")
def unban(ban_id: int, _: bool = Depends(require_admin)):
    """解除拉黑（之后这台设备可以正常登录）。"""
    db = SessionLocal()
    try:
        ok = session_store.unban_device(db, ban_id)
    finally:
        db.close()
    if not ok:
        raise HTTPException(404, detail="记录不存在或已解除")
    return {"ok": True}