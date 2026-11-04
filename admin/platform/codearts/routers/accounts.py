"""CodeArts 管理接口：`/api/codearts/*`。

    GET  /api/codearts/meta            平台元信息
    GET  /api/codearts/scan-local      扫描本机登录态（不含 AK/SK）
    POST /api/codearts/import-local    从本机客户端登录态导入
    POST /api/codearts/import          导入单条（粘贴/导出文件里的一条）
    POST /api/codearts/import-batch    批量导入（JSON 数组 / JSONL / 导出文件）
    GET  /api/codearts/accounts        账号列表（服务端分页）
    POST /api/codearts/accounts/{id}/refresh  刷新凭证与额度
    POST /api/codearts/accounts/{id}/test     分段连通性检查
    GET  /api/codearts/export          导出（含完整凭证）
    GET  /api/codearts/models          模型目录（走真实账号拉一次）
    GET  /api/codearts/usage           用量（本网关账本）
    POST /api/codearts/checkin         签到（明确返回 supported=False）
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from admin.db import get_db
from admin.platform.codearts.core import endpoints
from admin.platform.codearts.services import accounts as ca
from admin.platform.codearts.services import scan as ca_scan
from admin.security import require_admin

logger = logging.getLogger("admin.platform.codearts.routers")
router = APIRouter(prefix="/api/codearts", tags=["codearts"])

REALM_KEYS = ("cn",)


def _realm_or_400(realm: str) -> str:
    value = (realm or "").strip().lower()
    if value and value not in REALM_KEYS:
        raise HTTPException(400, detail="realm 必须是 cn")
    return value


@router.get("/meta")
def meta(_: bool = Depends(require_admin)):
    return {
        "platform": endpoints.PLATFORM,
        "regions": [{"key": "cn", "label": "华为云（cn-north-4）",
                     "base_url": endpoints.BASE}],
        "auth": "华为云 AK/SK 请求签名（SDK-HMAC-SHA256）+ X-Security-Token",
        "quota_kind": "credits",
        "note": ("凭证是 STS 临时 AK/SK（约 6 小时），网关会用 refresh_token + DPoP "
                 "自动续期；续期失败的号需要重新登录客户端"),
        # 控制台的「加号」入口按这个字段决定给哪些按钮
        "auth_mode": "local-session-only",
        "auth_explain": ("CodeArts 的登录态由客户端在 %APPDATA%/codearts/secretStorage "
                         "下加密保存。请在客户端登录后，用「扫描本机」导入；"
                         "网关只读，不回写客户端。") ,
    }


@router.get("/scan-local")
def scan_local(_: bool = Depends(require_admin)):
    return ca_scan.discover()


class ImportLocalIn(BaseModel):
    uids: list[str] | None = None


@router.post("/import-local")
def import_local(body: ImportLocalIn | None = None,
                 _: bool = Depends(require_admin),
                 db: Session = Depends(get_db)):
    from admin.platform.codearts.platform_gateway import CodeArtsAccountOps

    return CodeArtsAccountOps("").import_local(
        db, uids=body.uids if body else None)


class ImportIn(BaseModel):
    credential: dict | None = None
    name: str = ""


@router.post("/import")
def import_account(body: ImportIn, _: bool = Depends(require_admin),
                   db: Session = Depends(get_db)):
    if not body.credential:
        raise HTTPException(400, detail="需要 credential")
    record = dict(body.credential)
    if body.name:
        record.setdefault("name", body.name)
    try:
        return ca.import_record(db, record)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc


class ImportBatchIn(BaseModel):
    text: str = ""


@router.post("/import-batch")
def import_batch(body: ImportBatchIn, _: bool = Depends(require_admin),
                 db: Session = Depends(get_db)):
    return ca.import_batch(db, body.text)


@router.get("/accounts")
def list_accounts(realm: str = "", _: bool = Depends(require_admin),
                  db: Session = Depends(get_db), request: Request = None):
    from admin.platform.codearts.platform_gateway import CodeArtsAccountOps
    from admin.platform.shared import list_page_params

    params = list_page_params(request)
    return CodeArtsAccountOps(_realm_or_400(realm)).list(
        db, page=params["page"] if params["paginated"] else None,
        page_size=params["page_size"], search=params["search"],
        status=params["status"], sort=params["sort"],
        min_credits=params["min_credits"], max_credits=params["max_credits"])


@router.post("/accounts/{acc_id}/refresh")
def refresh(acc_id: int, _: bool = Depends(require_admin),
            db: Session = Depends(get_db)):
    from admin.platform.codearts.platform_gateway import CodeArtsAccountOps

    try:
        return CodeArtsAccountOps("").refresh(db, acc_id, force=True)
    except ValueError as exc:
        raise HTTPException(404 if "不存在" in str(exc) else 502,
                            detail=str(exc)) from exc


@router.post("/accounts/{acc_id}/test")
def test(acc_id: int, _: bool = Depends(require_admin),
         db: Session = Depends(get_db)):
    from admin.platform.codearts.platform_gateway import CodeArtsAccountOps

    try:
        return CodeArtsAccountOps("").health(db, acc_id)
    except ValueError as exc:
        raise HTTPException(404, detail=str(exc)) from exc


@router.get("/export")
def export(ids: str = "", _: bool = Depends(require_admin),
           db: Session = Depends(get_db)):
    parsed = [int(part) for part in ids.split(",") if part.strip().isdigit()]
    return ca.export_accounts(db, parsed or None)


@router.get("/models")
def models(_: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """用池里第一个可用账号拉一次真实模型目录（排障用）。"""
    from admin.platform.codearts.core import client

    account = ca.select(db, "cn")
    if account is None:
        return {"models": [], "note": "号池没有可用账号"}
    try:
        credential = ca.ensure_credential(db, account)
        rows = client.fetch_models(credential, account_id=int(account.id or 0))
    except client.UpstreamError as exc:
        raise HTTPException(502, detail=exc.message) from exc
    return {"models": rows, "account": account.name, "count": len(rows)}


@router.get("/usage")
def usage(realm: str = "", days: int = 30, _: bool = Depends(require_admin),
          db: Session = Depends(get_db)):
    from admin.platform.codearts.platform_gateway import CodeArtsAccountOps

    return CodeArtsAccountOps(_realm_or_400(realm)).usage(db, days=days)


@router.post("/checkin")
def checkin(_: bool = Depends(require_admin)):
    return {"supported": False,
            "summary": "CodeArts 没有签到接口；额度来自购买的积分包"}
