"""科搭 Codar 管理接口（/api/codar/*）。

控制台的通用流程按 `auth_mode` 决定显示哪些入口：这里是 `local-scan`
（扫描 / 导入本机 + 粘贴令牌），没有密码登录、验证码、扫码那套流程，
所以也不提供那些端点。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from admin.db import get_db
from admin.platform.codar.core import client, endpoints, paths
from admin.platform.codar.services import accounts as codar_accounts
from admin.platform.codar.services import checkin as codar_checkin
from admin.security import require_admin

router = APIRouter(prefix="/api/codar", tags=["codar"])

PLATFORM = endpoints.PUBLIC


class ImportIn(BaseModel):
    text: str = ""
    files: list[str] = []
    realm: str = ""


class ImportLocalIn(BaseModel):
    """通用扫描确认弹窗的入参（`{uids, all}`）。"""

    uids: list[str] = []
    all: bool = False
    realm: str = ""


class AccountPatch(BaseModel):
    status: str | None = None
    name: str | None = None


def _account(db, account_id: int):
    from admin.models import Account

    account = (db.query(Account)
               .filter(Account.id == int(account_id),
                       Account.platform == endpoints.PLATFORM).first())
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    return account


@router.get("/meta")
def meta(_: bool = Depends(require_admin)):
    return {
        "platform": PLATFORM,
        # 单区平台；保留 regions 字段让控制台的区域选择器有一份声明
        "regions": [{"key": "cn", "label": "中国版",
                     "base_url": endpoints.GATEWAY_BASE}],
        "auth": "Bearer JWT（账号侧接口另需 username 参数）",
        "quota_kind": "points",
        "note": "点数制；余额有主/副两个读数，签到按滚动 24 小时；"
                "每账号 3 个在途请求，超出排队",
        "auth_mode": "local-scan",
        "auth_explain": (
            "两种入库方式：①「扫描本机」读科搭客户端的登录态"
            "（~/.codar/config.json 的 provider.speedai.token，只读不回写）；"
            "②直接粘贴令牌导入。令牌是 JWT，导入前会校验账号名与是否过期 ——"
            "过期请先打开科搭客户端刷新登录。本平台不支持密码/验证码/扫码登录。"
        ),
    }


@router.get("/accounts")
def list_accounts(realm: str = "", _: bool = Depends(require_admin),
                  db=Depends(get_db)):
    return {"platform": PLATFORM,
            "items": [codar_accounts.public_view(row)
                      for row in codar_accounts.accounts_for(db, realm)]}


@router.get("/scan-local")
def scan_local(realm: str = "", _: bool = Depends(require_admin)):
    items = paths.discover()
    return {"platform": PLATFORM,
            "items": items,
            "can_import": any(item.get("valid") for item in items),
            "auth_home": paths.config_home(),
            "config": paths.config_path(),
            "hint": ("已找到本机登录态，勾选后导入" if items
                     else "没找到科搭登录态：先在客户端登录一次，"
                          "或粘贴令牌导入")}


@router.post("/import-local")
def import_local(body: ImportLocalIn, _: bool = Depends(require_admin),
                 db=Depends(get_db)):
    result = codar_accounts.import_local(
        db, body.realm, uids=None if (body.all or not body.uids) else body.uids)
    result["platform"] = PLATFORM
    return result


@router.post("/import")
def import_accounts(body: ImportIn, _: bool = Depends(require_admin),
                    db=Depends(get_db)):
    if not body.text.strip():
        raise HTTPException(status_code=400, detail="没有提供任何凭证")
    result = codar_accounts.import_batch(db, body.text)
    result["platform"] = PLATFORM
    return result


@router.post("/accounts/{account_id}/refresh")
def refresh_account(account_id: int, _: bool = Depends(require_admin),
                    db=Depends(get_db)):
    from admin.platform.codar.platform_gateway import CodarAccountOps

    result = CodarAccountOps().refresh(db, account_id)
    if not result.get("ok"):
        raise HTTPException(status_code=502,
                            detail=result.get("error") or "刷新失败")
    return result


@router.post("/accounts/{account_id}/test")
def test_account(account_id: int, _: bool = Depends(require_admin),
                 db=Depends(get_db)):
    from admin.platform.codar.platform_gateway import CodarAccountOps

    result = CodarAccountOps().health(db, account_id)
    return {"ok": bool(result.get("ok")), "steps": result.get("steps") or {}}


@router.patch("/accounts/{account_id}")
def patch_account(account_id: int, body: AccountPatch,
                  _: bool = Depends(require_admin), db=Depends(get_db)):
    account = _account(db, account_id)
    if body.status:
        account.status = body.status
    if body.name:
        account.name = body.name
    db.commit()
    return {"ok": True}


@router.delete("/accounts/{account_id}")
def delete_account(account_id: int, _: bool = Depends(require_admin),
                   db=Depends(get_db)):
    account = _account(db, account_id)
    db.delete(account)
    db.commit()
    return {"ok": True}


@router.post("/accounts/{account_id}/checkin")
def checkin_one(account_id: int, _: bool = Depends(require_admin),
                db=Depends(get_db)):
    account = _account(db, account_id)
    result = codar_checkin.sign_in(db, account)
    return result


@router.get("/checkin/status")
def checkin_status(_: bool = Depends(require_admin), db=Depends(get_db)):
    rows = [row for row in codar_accounts.accounts_for(db)
            if row.status == "active"][:20]
    items = []
    for row in rows:
        try:
            state = codar_checkin.status_of(db, row)
            items.append({"account_id": row.id, "name": row.name,
                          "signed": state.get("signed"),
                          "state_known": state.get("state_known"),
                          "wait_seconds": state.get("wait_seconds"),
                          "until": state.get("until"),
                          "points": row.balance_remain})
        except client.UpstreamError as exc:
            items.append({"account_id": row.id, "name": row.name,
                          "error": exc.message, "kind": exc.kind})
    return {"platform": PLATFORM, "items": items}


@router.post("/checkin")
def checkin_all(realm: str = "", _: bool = Depends(require_admin),
                db=Depends(get_db)):
    """批量签到（控制台「签到」按钮）；返回 {summary, items, failed}。"""
    return codar_checkin.checkin_all(db, realm=realm)


@router.get("/models")
def list_models(refresh: bool = False, _: bool = Depends(require_admin),
                db=Depends(get_db)):
    info = codar_accounts.catalog(db, force=refresh)
    return {"platform": PLATFORM, "fallback": bool(info.get("fallback")),
            "error": info.get("error") or "",
            "vendor_concurrency": info.get("vendor_concurrency") or 0,
            "max_concurrency": endpoints.max_concurrency(),
            "models": info["rows"]}


@router.get("/overview")
def overview(_: bool = Depends(require_admin), db=Depends(get_db)):
    from admin.platform.codar.platform_gateway import CodarGateway

    return {"platform": PLATFORM,
            "cards": CodarGateway().overview_cards(db)}


@router.get("/usage")
def usage(limit: int = 50, _: bool = Depends(require_admin),
          db=Depends(get_db)):
    from admin.models import UsageLog

    rows = (db.query(UsageLog).filter(UsageLog.platform == PLATFORM)
            .order_by(UsageLog.id.desc()).limit(max(1, min(limit, 200))).all())
    return {"platform": PLATFORM, "recent": [
        {"id": row.id, "model": row.model, "credits": row.credits,
         "prompt_tokens": row.prompt_tokens,
         "completion_tokens": row.completion_tokens,
         "created_at": row.created_at.isoformat() if row.created_at else None}
        for row in rows]}


@router.get("/export")
def export_accounts(mode: str = "backup", realm: str = "",
                    _: bool = Depends(require_admin), db=Depends(get_db)):
    """导出：`backup` 含令牌；`plain` 是脱敏清单（可直接贴进 issue）。"""
    rows = codar_accounts.accounts_for(db, realm)
    accounts = []
    for row in rows:
        if mode == "plain":
            accounts.append({"id": row.id, "name": row.name, "uid": row.uid,
                             "realm": "cn", "status": row.status,
                             "balance_remain": row.balance_remain,
                             "balance_total": row.balance_total})
        else:
            accounts.append({"id": row.id, "name": row.name, "uid": row.uid,
                             "realm": "cn", "status": row.status,
                             "item": row.auth_json})
    return {"platform": PLATFORM, "format": "codar-" + mode,
            "count": len(accounts), "accounts": accounts}


@router.get("/activities")
def activities(_: bool = Depends(require_admin), db=Depends(get_db)):
    """免费模型活动（当前仍在跑的）。"""
    rows = [row for row in codar_accounts.accounts_for(db)
            if row.status == "active"][:10]
    items = []
    for row in rows:
        items.append({"account_id": row.id, "name": row.name,
                      "activities": codar_checkin.activities(db, row)})
    return {"platform": PLATFORM, "items": items}


# ---------------------------------------------------------------------------
# 微信扫码授权登录（免客户端）
# ---------------------------------------------------------------------------
class AuthIdIn(BaseModel):
    auth_id: str = ""


@router.post("/auth/begin")
def auth_begin(_: bool = Depends(require_admin)):
    """开一个扫码会话：返回 auth_id + 二维码图片地址（图片由本网关代理）。"""
    from admin.platform.codar.services import webauth

    try:
        return {"ok": True, **webauth.begin()}
    except webauth.AuthError as exc:
        raise HTTPException(502, detail=str(exc)) from exc


@router.get("/auth/poll")
def auth_poll(auth_id: str = "", _: bool = Depends(require_admin),
              db=Depends(get_db)):
    """轮询扫码状态；完成时已换票并入库。"""
    from admin.platform.codar.services import webauth

    if not (auth_id or "").strip():
        raise HTTPException(400, detail="缺少 auth_id")
    try:
        return webauth.poll(db, auth_id.strip())
    except webauth.AuthError as exc:
        raise HTTPException(502, detail=str(exc)) from exc


@router.get("/auth/qr.png")
def auth_qr_png(auth_id: str = "", _: bool = Depends(require_admin)):
    """二维码图片（服务端代理微信图片口，控制台只同源加载）。"""
    from fastapi.responses import Response

    from admin.platform.codar.services import webauth

    try:
        body, media = webauth.qr_image(auth_id.strip())
    except webauth.AuthError as exc:
        raise HTTPException(404, detail=str(exc)) from exc
    return Response(body, media_type=media,
                    headers={"Cache-Control": "no-store"})


@router.post("/auth/cancel")
def auth_cancel(body: AuthIdIn, _: bool = Depends(require_admin)):
    from admin.platform.codar.services import webauth

    return webauth.cancel((body.auth_id or "").strip())
