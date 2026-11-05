"""join 总闸的后台接口 + 公开的票据兑换端点。

后台（require_admin）：
    GET  /api/join/gate/status     当前是否开启、代次、剩余票据/邀请码
    POST /api/join/gate/toggle     开 / 关（每次都推进代次）
    POST /api/join/gate/tickets    签一次性访问链接

公开（只认票据，不认 Cookie）：
    POST /api/join/ticket/exchange 票据 → join_access HttpOnly Cookie

为什么兑换端点必须独立于后台鉴权
--------------------------------
分享链接是给**还没有任何身份**的人点的。它不能要求管理员密码，
所以它自己要成为一道窄门：10 位短码、按时间有效（默认 24h）、
有效期内可重复兑换，只换一枚只能访问 /join 的 Cookie；关闸再开会让整代凭证作废。
"""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from admin import join_gate
from admin.config import settings
from admin.db import get_db
from admin.ratelimit import client_ip_from_request
from admin.security import require_admin

_logger = logging.getLogger("admin.join_gate_router")

router = APIRouter(tags=["join-gate"])


class ToggleIn(BaseModel):
    enable: bool


class TicketsIn(BaseModel):
    count: int = Field(default=1, ge=1, le=50)


class ExchangeIn(BaseModel):
    ticket: str = Field(default="", max_length=2048)


def _public_base(request: Request) -> str:
    """拼接对外访问地址。

    优先 `ADMIN_PUBLIC_BASE_URL`（生产显式配置，最可信）；否则用反向代理
    覆写过的 Host 头。`X-Forwarded-Host` 只有在 nginx 显式覆盖时才可信，
    `deploy/nginx-prod.conf` 里已按这个口径配置。
    """
    configured = (os.getenv("ADMIN_PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if configured:
        return configured
    host = (request.headers.get("x-forwarded-host")
            or request.headers.get("host") or "").strip()
    proto = (request.headers.get("x-forwarded-proto")
             or request.url.scheme or "https").strip()
    if not host:
        return str(request.base_url).rstrip("/")
    return f"{proto}://{host}"


@router.get("/api/join/gate/status")
def gate_status(db: Session = Depends(get_db), _=Depends(require_admin)):
    return {
        "open": join_gate.is_open(db),
        "epoch": join_gate.epoch(db),
        "active_tickets": join_gate.active_ticket_count(db),
        "active_invites": join_gate.active_invite_count(db),
        "ticket_ttl": int(getattr(settings, "JOIN_TICKET_TTL", 900)),
        "access_ttl": int(getattr(settings, "JOIN_ACCESS_TTL", 3600)),
    }


@router.post("/api/join/gate/toggle")
def gate_toggle(body: ToggleIn, request: Request,
                db: Session = Depends(get_db), _=Depends(require_admin)):
    try:
        result = join_gate.toggle(
            db, body.enable,
            operator="admin", ip=client_ip_from_request(request))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    result["ok"] = True
    return result


@router.post("/api/join/gate/tickets")
def gate_tickets(body: TicketsIn, request: Request,
                 db: Session = Depends(get_db), _=Depends(require_admin)):
    """签发访问链接。**明文只在这里返回一次**，服务端只留 jti。"""
    try:
        tickets = join_gate.issue_tickets(db, body.count, operator="admin")
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    base = _public_base(request)
    return {
        "ok": True,
        "expires_in": int(getattr(settings, "JOIN_TICKET_TTL", 900)),
        "urls": [f"{base}/j#k={t}" for t in tickets],
        "warning": "链接在有效期内可重复打开（默认 24 小时）；通过可信渠道发送，关闸即全部失效。",
    }


@router.post("/api/join/ticket/exchange")
def ticket_exchange(body: ExchangeIn, request: Request,
                    db: Session = Depends(get_db)):
    """票据换 Cookie。失败一律 404，不区分"过期/已用/入口关闭"。"""
    cookie_value = None
    try:
        cookie_value = join_gate.exchange(db, (body.ticket or "").strip())
    except RuntimeError:
        # 生产未配置 JOIN_TICKET_SECRET —— 启动断言已挡住，这里兜底。
        _logger.error("join 票据密钥不可用，拒绝兑换")
    if not cookie_value:
        _logger.warning("join 票据兑换失败 ip=%s", client_ip_from_request(request))
        raise HTTPException(status_code=404, detail="页面不存在")

    https = (request.url.scheme == "https"
             or (request.headers.get("x-forwarded-proto") or "").lower() == "https")
    response = JSONResponse({"ok": True})
    response.set_cookie(
        key=join_gate.COOKIE_NAME, value=cookie_value, httponly=True,
        samesite="lax", secure=https, path="/",
        max_age=int(getattr(settings, "JOIN_ACCESS_TTL", 3600)))
    return response
