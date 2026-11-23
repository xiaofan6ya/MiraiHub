"""AStudio 管理接口（/api/astudio/*）。

能力：扫描本机登录态 / 导入 / 导出 / 刷新额度 / 签到 / 用量。
没有设备授权（不做手机号/密码/扫码那套，范围外）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from admin.platform.astudio.core import client, paths
from admin.platform.astudio.core.endpoints import PUBLIC
from admin.platform.astudio.services import accounts as astudio_accounts
from admin.platform.astudio.services import checkin as astudio_checkin
from admin.security import require_admin
from admin.db import get_db

router = APIRouter(prefix="/api/astudio", tags=["astudio"])


class ImportIn(BaseModel):
    text: str = ""
    files: list[str] = []


class ImportLocalIn(BaseModel):
    """通用扫描确认弹窗的导入入参（`platformImportConfirm` 发 `{uids, all}`）。"""
    uids: list[str] = []
    all: bool = False


class AccountPatch(BaseModel):
    status: str | None = None
    name: str | None = None


@router.get("/meta")
def meta(_: bool = Depends(require_admin)):
    return {
        "platform": PUBLIC,
        "label": "AStudio",
        "regions": [{"key": "cn", "label": "中国版", "default": True}],
        "auth": {"mode": "local-scan",
                 "explain": "读取本机 AStudio 桌面端登录态（或粘贴 modelBearerToken）"},
        "quota_kind": "credits",
        "note": "AStudio（讯飞 Astron Studio）：模型凭据为 3 年 JWT；"
                "网页会话 Cookie 用于积分余额与每日签到",
        "auth_mode": "local-scan",
        "auth_explain": "本机装有并登录过 AStudio 桌面端即可扫描；"
                        "也可以直接粘贴 astron-session.json 的 modelBearerToken",
        "auth_home": paths.FALLBACK_DATA_ROOT,
        "capabilities": {"scan": True, "import": True, "checkin": True,
                         "refresh_quota": True, "credits": True},
    }


@router.get("/accounts")
def list_accounts(realm: str = "", _: bool = Depends(require_admin),
                  db=Depends(get_db)):
    return {"platform": PUBLIC,
            "items": [astudio_accounts.public_view(row)
                      for row in astudio_accounts.accounts_for(db, realm)]}


@router.get("/scan-local")
def scan_local(_: bool = Depends(require_admin)):
    items = paths.discover()
    return {"platform": PUBLIC, "items": items,
            "can_import": bool(items),
            "auth_home": paths.FALLBACK_DATA_ROOT,
            "hint": "AStudio 桌面端登录态是明文 JSON，勾选后直接入库"
                    if items else "未找到登录态：请先在 AStudio 桌面端登录一次"}


@router.post("/import-local")
def import_local(body: ImportLocalIn, _: bool = Depends(require_admin),
                 db=Depends(get_db)):
    result = astudio_accounts.import_local(
        db, uids=None if (body.all or not body.uids) else body.uids)
    result["platform"] = PUBLIC
    return result


@router.post("/import")
def import_accounts(body: ImportIn, _: bool = Depends(require_admin),
                    db=Depends(get_db)):
    if not body.text.strip():
        raise HTTPException(status_code=400, detail="没有提供任何凭证")
    result = astudio_accounts.import_batch(db, body.text)
    result["platform"] = PUBLIC
    return result


@router.post("/accounts/{account_id}/refresh")
def refresh_account(account_id: int, _: bool = Depends(require_admin),
                    db=Depends(get_db)):
    account = (db.query(astudio_accounts.Account)
               .filter(astudio_accounts.Account.id == account_id,
                       astudio_accounts.Account.platform == PUBLIC).first())
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    try:
        astudio_accounts.refresh_from_disk(db, account)
        result = astudio_accounts.sync_quota(db, account)
    except client.UpstreamError as exc:
        raise HTTPException(status_code=502, detail=exc.message)
    return {"ok": bool(result.get("ok")),
            "remain": result.get("remain"),
            "total": result.get("total"),
            "note": result.get("error") or ""}


@router.post("/accounts/{account_id}/test")
def test_account(account_id: int, _: bool = Depends(require_admin),
                 db=Depends(get_db)):
    """分段连通性检查：模型凭据 → 网页会话 → 对话（限免模型）。"""
    from admin.platform.astudio.platform_gateway import AStudioAccountOps

    result = AStudioAccountOps().health(db, account_id)
    return {"ok": bool(result.get("ok")), "steps": result.get("steps") or {}}


@router.patch("/accounts/{account_id}")
def patch_account(account_id: int, body: AccountPatch,
                  _: bool = Depends(require_admin), db=Depends(get_db)):
    account = (db.query(astudio_accounts.Account)
               .filter(astudio_accounts.Account.id == account_id,
                       astudio_accounts.Account.platform == PUBLIC).first())
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    if body.status:
        account.status = body.status
    if body.name:
        account.name = body.name
    db.commit()
    return {"ok": True}


@router.delete("/accounts/{account_id}")
def delete_account(account_id: int, _: bool = Depends(require_admin),
                   db=Depends(get_db)):
    account = (db.query(astudio_accounts.Account)
               .filter(astudio_accounts.Account.id == account_id,
                       astudio_accounts.Account.platform == PUBLIC).first())
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    db.delete(account)
    db.commit()
    return {"ok": True}


@router.post("/accounts/{account_id}/checkin")
def checkin_one(account_id: int, _: bool = Depends(require_admin),
                db=Depends(get_db)):
    account = (db.query(astudio_accounts.Account)
               .filter(astudio_accounts.Account.id == account_id,
                       astudio_accounts.Account.platform == PUBLIC).first())
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    result = astudio_checkin.checkin_one(db, account)
    result["platform"] = PUBLIC
    return result


@router.get("/checkin/status")
def checkin_status(_: bool = Depends(require_admin), db=Depends(get_db)):
    rows = [row for row in astudio_accounts.accounts_for(db)
            if row.status == "active"]
    if not rows:
        return {"platform": PUBLIC, "items": []}
    items = []
    for row in rows[:20]:
        result = astudio_checkin.checkin_one(db, row)
        items.append({"account_id": row.id, "name": row.name,
                      "outcome": result.get("outcome"),
                      "message": result.get("message") or "",
                      "error": result.get("error") or ""})
    return {"platform": PUBLIC, "items": items}


@router.post("/checkin")
def checkin_all(_: bool = Depends(require_admin), db=Depends(get_db)):
    """批量签到（前端「签到」按钮）：与其它平台同形的 {summary, items}。"""
    result = astudio_checkin.run_checkin(db)
    result["platform"] = PUBLIC
    return result


@router.get("/export")
def export_accounts(mode: str = "backup", _: bool = Depends(require_admin),
                    db=Depends(get_db)):
    """导出（backup=含凭证 / plain=脱敏清单），前端通用导出弹窗用。"""
    from admin.platform.astudio.platform_gateway import AStudioAccountOps

    ops = AStudioAccountOps()
    if mode == "plain":
        rows = astudio_accounts.accounts_for(db)
        accounts = [{"id": row.id, "name": row.name,
                     "uid": astudio_accounts.account_uid(row),
                     "realm": "cn", "status": row.status,
                     "balance_remain": row.balance_remain,
                     "balance_total": row.balance_total}
                    for row in rows]
    else:
        payload = ops.export(db)
        accounts = payload["items"]
    return {"platform": PUBLIC, "format": "astudio-" + mode,
            "count": len(accounts), "accounts": accounts}


@router.get("/usage")
def usage(limit: int = 50, _: bool = Depends(require_admin),
          db=Depends(get_db)):
    from admin.models import UsageLog

    rows = (db.query(UsageLog).filter(UsageLog.platform == PUBLIC)
            .order_by(UsageLog.id.desc()).limit(max(1, min(limit, 200))).all())
    return {"platform": PUBLIC, "recent": [
        {"id": row.id, "model": row.model, "credits": row.credits,
         "created_at": row.created_at.isoformat() if row.created_at else None}
        for row in rows]}


@router.get("/overview")
def overview(_: bool = Depends(require_admin), db=Depends(get_db)):
    from admin.platform.astudio.platform_gateway import AStudioGateway
    return {"cards": AStudioGateway().overview_cards(db)}
