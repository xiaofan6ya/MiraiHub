"""后台管理自助注册凭证（邀请码）。

这一组端点全部要求管理端鉴权（``require_admin``），是**唯一**能开通自助
注册入口的地方 —— 没有它，`/join` 与 `/api/join/*` 全部关闭。

语义是 fail-closed：
    * 后台一张有效凭证都没有 → 自助入口整体关闭（404，不泄露原因）
    * 管理员生成凭证后 → 请求必须携带其中一张才放行
    * 撤销立刻生效，不需要重启
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from admin.db import get_db
from admin.join_invite import (check_invite, create_invite, delete_invite,
                               has_any_invite, invite_status, list_invites,
                               revoke_invite)
from admin.security import require_admin

_logger = logging.getLogger("admin.join_invite_router")

router = APIRouter(prefix="/api/join/invites", tags=["join-invites"])


class InviteCreateIn(BaseModel):
    """生成一张凭证。"""
    note: str = Field(default="", max_length=200)
    #: 最大可用次数（默认 1，即一次性）。
    max_uses: int = Field(default=1, ge=1, le=1000)
    #: 有效天数（默认 7，上限 90）。
    valid_days: int = Field(default=7, ge=1, le=90)


@router.get("")
def invite_list(db: Session = Depends(get_db), _=Depends(require_admin)):
    """列出全部凭证（明文不可见，只有前缀与用量）。"""
    rows = list_invites(db)
    return {
        "open": has_any_invite(db),
        "total": len(rows),
        "items": [invite_status(r) for r in rows],
    }


@router.post("")
def invite_create(body: InviteCreateIn, request: Request,
                  db: Session = Depends(get_db), _=Depends(require_admin)):
    """生成一张凭证。

    **明文邀请码只在这里返回一次**，之后无法再取回（库里只存哈希）。
    """
    row, code = create_invite(
        db,
        note=body.note,
        max_uses=body.max_uses,
        valid_days=body.valid_days,
        created_by="admin",
    )
    return {
        "ok": True,
        "code": code,
        "warning": "明文邀请码仅显示这一次，请立即保存。",
        "item": invite_status(row),
    }


@router.post("/{invite_id}/revoke")
def invite_revoke(invite_id: int, db: Session = Depends(get_db),
                  _=Depends(require_admin)):
    """撤销一张凭证（保留记录，标记失效）。"""
    if not revoke_invite(db, invite_id):
        raise HTTPException(status_code=404, detail="凭证不存在")
    return {"ok": True, "open": has_any_invite(db)}


@router.delete("/{invite_id}")
def invite_delete(invite_id: int, db: Session = Depends(get_db),
                  _=Depends(require_admin)):
    """彻底删除一张凭证。"""
    if not delete_invite(db, invite_id):
        raise HTTPException(status_code=404, detail="凭证不存在")
    return {"ok": True, "open": has_any_invite(db)}
