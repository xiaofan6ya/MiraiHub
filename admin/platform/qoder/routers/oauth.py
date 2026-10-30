"""Qoder 设备授权路由（正确写法：独立 router + 在 accounts 里 import）。

## 为什么改掉上一版

上一版用 `def register(router)` 在函数里定义路由 —— 那样 FastAPI 的
依赖注入拿不到，而且不利于测试。改成标准模块级 router。
"""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from admin.db import get_db
from admin.platform.auth import SessionStore
from admin.platform.qoder.core import oauth
from admin.platform.qoder.core.client import UpstreamError
from admin.security import require_admin

logger = logging.getLogger("admin.platform.qoder.oauth_router")
router = APIRouter(prefix="/api/qoder", tags=["qoder"])

#: 进行中的授权会话（nonce -> DeviceLogin）。单进程内存即可：
#: 授权是一次性交互，重启丢失可接受。
_SESSION_STORE = SessionStore()
_SESSIONS: dict = _SESSION_STORE.items

#: 会话过期时间（秒）
SESSION_TTL = 600


def _prune() -> None:
    now = int(time.time())
    for key in [k for k, v in _SESSIONS.items()
                if now - v.created_at > SESSION_TTL]:
        _SESSIONS.pop(key, None)


@router.post("/device/start")
def device_start(realm: str = "cn", _: bool = Depends(require_admin)):
    """生成设备授权链接。

    返回 `auth_url` —— 前端把它展示成可点击链接/二维码，
    用户在浏览器里选账号授权后，前端开始轮询 `/device/poll`。
    """
    value = (realm or "cn").strip().lower()
    if value not in ("cn", "intl"):
        raise HTTPException(400, detail="realm 必须是 cn 或 intl")
    try:
        session = oauth.DeviceLogin(value)
    except Exception as exc:  # noqa: BLE001
        logger.exception("生成设备授权链接失败")
        raise HTTPException(502,
                            detail="生成授权链接失败：%s" % str(exc)[:120]) from exc
    _SESSION_STORE.add(session)
    payload = session.as_dict()
    payload["summary"] = "打开链接 → 选择账号 → 授权，然后本页会自动完成"
    return payload


@router.get("/device/poll")
def device_poll(nonce: str, _: bool = Depends(require_admin)):
    """轮询一次（前端每 3 秒一次）。"""
    session = _SESSIONS.get(nonce)
    if session is None or session.expired:
        raise HTTPException(404,
                            detail="授权会话不存在或已过期，请重新开始")
    try:
        credentials = session.poll_once()
    except UpstreamError as exc:
        raise HTTPException(502, detail=exc.message) from exc
    if credentials is None:
        return {"pending": True,
                "summary": "等待授权…请在浏览器里选择账号并确认"}
    credentials["nonce"] = nonce
    return {"pending": False, "credentials": credentials,
            "summary": "授权成功，正在保存账号…"}


class ClaimIn(BaseModel):
    credentials: dict


@router.post("/device/claim")
def device_claim(body: ClaimIn, _: bool = Depends(require_admin),
                 db: Session = Depends(get_db)):
    """把拿到的凭证落成账号。"""
    from admin.platform.qoder.services import accounts as qoder_accounts

    payload = dict(body.credentials or {})
    nonce = str(payload.pop("nonce", "") or "")
    try:
        result = qoder_accounts.import_record(db, payload)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    if nonce:
        _SESSIONS.pop(nonce, None)
    return {"ok": True, **result}
