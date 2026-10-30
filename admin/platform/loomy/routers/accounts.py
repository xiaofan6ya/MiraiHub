"""Loomy 管理接口：`/api/loomy/*`。

    GET  /api/loomy/meta            平台元信息
    GET  /api/loomy/scan-local      扫描本机（含 baseURL / 模型清单）
    POST /api/loomy/import-local    **从本机明文凭证一键导入**
    POST /api/loomy/import          导入指定凭证
    GET  /api/loomy/accounts        账号列表（按平台隔离）
    POST /api/loomy/accounts/{id}/refresh   探活并刷新
    POST /api/loomy/accounts/{id}/test      连通性检查
    GET  /api/loomy/usage           用量（本网关账本）
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from admin.db import get_db
from admin.platform.loomy.services import accounts as loomy_accounts
from admin.platform.loomy.services import scan as loomy_scan
from admin.security import require_admin

logger = logging.getLogger("admin.platform.loomy.routers")
router = APIRouter(prefix="/api/loomy", tags=["loomy"])

REALM_KEYS = ("cn",)


def _realm_or_400(realm: str) -> str:
    value = (realm or "").strip().lower()
    if value and value not in REALM_KEYS:
        raise HTTPException(400, detail="realm 必须是 cn")
    return value


@router.get("/meta")
def meta(_: bool = Depends(require_admin)):
    from admin.platform.loomy.core import endpoints

    return {
        "platform": "loomy",
        "regions": [{"key": k, "label": v["label"],
                     "base_url": v["base_url"]}
                    for k, v in endpoints.REGIONS.items()],
        "auth": "双 token 头（Authorization + token）+ traceparent",
        "quota_kind": "metered",
        "note": "Loomy 按对话计量 points_consumed，无剩余额度；无 refresh 机制",
    }


@router.get("/scan-local")
def scan_local(_: bool = Depends(require_admin)):
    """扫描本机（**结果不含完整 token**）。"""
    return loomy_scan.discover()


@router.post("/import-local")
def import_local(_: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """从本机 `auth-session.json` 一键导入（用户零操作）。"""
    return loomy_accounts.import_from_local(db)


class ImportIn(BaseModel):
    credential: dict | None = None


@router.post("/import")
def import_account(body: ImportIn, _: bool = Depends(require_admin),
                   db: Session = Depends(get_db)):
    if not body.credential:
        raise HTTPException(400, detail="需要 credential")
    try:
        return loomy_accounts.import_record(db, body.credential)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc


@router.get("/accounts")
def list_accounts(realm: str = "", _: bool = Depends(require_admin),
                  db: Session = Depends(get_db)):
    from admin.platform.loomy.platform_gateway import LoomyAccountOps

    return LoomyAccountOps(_realm_or_400(realm)).list(db)


@router.post("/accounts/{acc_id}/refresh")
def refresh(acc_id: int, _: bool = Depends(require_admin),
            db: Session = Depends(get_db)):
    from admin.platform.loomy.platform_gateway import LoomyAccountOps

    try:
        return LoomyAccountOps("").refresh(db, acc_id, force=True)
    except ValueError as exc:
        raise HTTPException(404, detail=str(exc)) from exc


@router.post("/accounts/{acc_id}/test")
def test(acc_id: int, _: bool = Depends(require_admin),
         db: Session = Depends(get_db)):
    from admin.platform.loomy.platform_gateway import LoomyAccountOps

    try:
        return LoomyAccountOps("").health(db, acc_id)
    except ValueError as exc:
        raise HTTPException(404, detail=str(exc)) from exc


@router.get("/usage")
def usage(realm: str = "", days: int = 30, _: bool = Depends(require_admin),
          db: Session = Depends(get_db)):
    from admin.platform.loomy.platform_gateway import LoomyAccountOps

    return LoomyAccountOps(_realm_or_400(realm)).usage(db, days=days)


@router.post("/checkin")
def checkin(realm: str = "", _: bool = Depends(require_admin),
            db: Session = Depends(get_db)):
    """Loomy 没有签到 —— 返回 `supported: False`（不是错误）。"""
    return {"supported": False,
            "summary": "Loomy 没有签到接口；额度按对话计量"}
