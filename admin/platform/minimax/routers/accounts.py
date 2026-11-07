"""MiniMax Code 管理接口（/api/minimax/*）。

授权登录走官方 OAuth **设备授权流**（与官方客户端同款）：
    1. POST /api/minimax/device/start   → {verification_uri_complete, user_code}
       （前端把 verification_uri_complete 生成二维码，用户手机扫码确认）
    2. GET  /api/minimax/device/poll    → pending | success(已入库)
    3. POST /api/minimax/device/cancel
"""
from __future__ import annotations

import threading
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from admin.platform.minimax.core import client, paths
from admin.platform.minimax.core.endpoints import PUBLIC
from admin.platform.minimax.services import accounts as minimax_accounts
from admin.security import require_admin
from admin.db import get_db

router = APIRouter(prefix="/api/minimax", tags=["minimax"])

#: 设备授权会话（内存表；官方 expires_in=300s）
_device_sessions: dict[str, dict] = {}
_DEVICE_LOCK = threading.Lock()


def _cleanup_devices_locked(now: float) -> None:
    stale = [k for k, v in _device_sessions.items()
             if v.get("expires_at", 0) < now - 60]
    for key in stale:
        _device_sessions.pop(key, None)


class ImportIn(BaseModel):
    text: str = ""
    files: list[str] = []
    realm: str = ""


class AccountPatch(BaseModel):
    status: str | None = None
    name: str | None = None


@router.get("/meta")
def meta(_: bool = Depends(require_admin)):
    return {"platform": PUBLIC, "label": "MiniMax Code",
            "auth_home": paths.auth_home(),
            "capabilities": {"scan": True, "import": True, "device_auth": True,
                             "checkin": True, "keepalive": True,
                             "credits": True}}


@router.get("/accounts")
def list_accounts(realm: str = "", _: bool = Depends(require_admin),
                  db=Depends(get_db)):
    return {"platform": PUBLIC,
            "items": [minimax_accounts.public_view(row)
                      for row in minimax_accounts.accounts_for(db, realm)]}


class ImportLocalIn(BaseModel):
    """通用扫描确认弹窗的导入入参（`platformImportConfirm` 发 `{uids, all}`）。"""
    uids: list[str] = []
    all: bool = False
    realm: str = ""


@router.get("/scan-local")
def scan_local(realm: str = "", _: bool = Depends(require_admin)):
    items = paths.discover()
    if realm:
        want = paths.normalize_region(realm)
        items = [it for it in items if it.get("realm") == want]
    return {"platform": PUBLIC, "items": items,
            "can_import": bool(items),
            "auth_home": paths.auth_home(),
            "hint": "MiniMax 客户端登录态是明文 auth.json，勾选后直接入库"
                    if items else "未找到登录态：请先在 MiniMax Code 客户端登录一次"}


@router.post("/import-local")
def import_local(body: ImportLocalIn, _: bool = Depends(require_admin),
                 db=Depends(get_db)):
    result = minimax_accounts.import_local(
        db, body.realm,
        uids=None if (body.all or not body.uids) else body.uids)
    result["platform"] = PUBLIC
    return result


@router.post("/import")
def import_accounts(body: ImportIn, _: bool = Depends(require_admin),
                    db=Depends(get_db)):
    if not body.text.strip():
        raise HTTPException(status_code=400, detail="没有提供任何凭证")
    result = minimax_accounts.import_batch(db, body.text)
    result["platform"] = PUBLIC
    return result


@router.post("/accounts/{account_id}/refresh")
def refresh_account(account_id: int, _: bool = Depends(require_admin),
                    db=Depends(get_db)):
    account = (db.query(minimax_accounts.Account)
               .filter(minimax_accounts.Account.id == account_id,
                       minimax_accounts.Account.platform == PUBLIC).first())
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    try:
        result = minimax_accounts.sync_quota(db, account)
    except client.UpstreamError as exc:
        raise HTTPException(status_code=502, detail=exc.message)
    return {"ok": True, "remain": result.get("remain"),
            "total": result.get("total")}


@router.post("/accounts/{account_id}/test")
def test_account(account_id: int, _: bool = Depends(require_admin),
                 db=Depends(get_db)):
    """分段连通性检查：凭证 → 用户 → 对话。"""
    from admin.platform.minimax.platform_gateway import MinimaxAccountOps

    result = MinimaxAccountOps().health(db, account_id)
    return {"ok": bool(result.get("ok")), "steps": result.get("steps") or {}}


@router.patch("/accounts/{account_id}")
def patch_account(account_id: int, body: AccountPatch,
                  _: bool = Depends(require_admin), db=Depends(get_db)):
    account = (db.query(minimax_accounts.Account)
               .filter(minimax_accounts.Account.id == account_id,
                       minimax_accounts.Account.platform == PUBLIC).first())
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
    account = (db.query(minimax_accounts.Account)
               .filter(minimax_accounts.Account.id == account_id,
                       minimax_accounts.Account.platform == PUBLIC).first())
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    db.delete(account)
    db.commit()
    return {"ok": True}


@router.post("/accounts/{account_id}/checkin")
def checkin_one(account_id: int, _: bool = Depends(require_admin),
                db=Depends(get_db)):
    account = (db.query(minimax_accounts.Account)
               .filter(minimax_accounts.Account.id == account_id,
                       minimax_accounts.Account.platform == PUBLIC).first())
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    try:
        data = minimax_accounts.ensure_token(db, account)
        token = minimax_accounts.token_of(data)
        realm = minimax_accounts.realm_of(account)
        result = client.signin_claim(token, realm,
                                     account_id=int(account.id or 0))
    except client.UpstreamError as exc:
        raise HTTPException(status_code=502, detail=exc.message)
    return {"ok": True, "points": result.get("points"),
            "claim_result": result.get("claim_result")}


@router.get("/checkin/status")
def checkin_status(_: bool = Depends(require_admin), db=Depends(get_db)):
    rows = [row for row in minimax_accounts.accounts_for(db)
            if row.status == "active"]
    if not rows:
        return {"platform": PUBLIC, "items": []}
    items = []
    for row in rows[:20]:
        try:
            data = minimax_accounts.ensure_token(db, row)
            status = client.signin_status(
                minimax_accounts.token_of(data),
                minimax_accounts.realm_of(row),
                account_id=int(row.id or 0))
            days = status.get("days") if isinstance(status.get("days"), list) else []
            today = next((d for d in days
                          if isinstance(d, dict) and d.get("is_today")), {})
            items.append({"account_id": row.id, "name": row.name,
                          "today_status": today.get("status"),
                          "claimable": today.get("status") == 3,
                          "points": today.get("points")})
        except client.UpstreamError as exc:
            items.append({"account_id": row.id, "name": row.name,
                          "error": exc.message})
    return {"platform": PUBLIC, "items": items}


# ---------------------------------------------------------------------------
# 设备授权（OAuth device flow）
# ---------------------------------------------------------------------------
@router.post("/device/start")
def device_start(realm: str = "cn", _: bool = Depends(require_admin)):
    from pydantic import BaseModel as _BM

    auth = client.device_begin(realm)
    auth_id = "minimax-auth-%d" % int(time.time() * 1000)
    now = time.time()
    with _DEVICE_LOCK:
        _cleanup_devices_locked(now)
        _device_sessions[auth_id] = {
            "auth": auth, "created_at": now,
            "expires_at": now + auth["expires_in"], "status": "pending",
            "account": None}
    uri = auth.get("verification_uri_complete") or auth.get("verification_uri")
    return {"auth_id": auth_id, "user_code": auth["user_code"],
            "verification_uri": auth.get("verification_uri"),
            "verification_uri_complete": auth.get("verification_uri_complete"),
            "qr_value": uri, "expires_in": auth["expires_in"],
            "interval": auth["interval"], "realm": auth["realm"],
            "status": "pending"}


@router.get("/device/poll")
def device_poll(auth_id: str, _: bool = Depends(require_admin),
                db=Depends(get_db)):
    with _DEVICE_LOCK:
        sess = _device_sessions.get(auth_id)
        if sess is None:
            return {"status": "expired", "done": False}
        if sess["status"] in ("done", "expired", "denied"):
            return _device_public(sess)
        if sess["expires_at"] < time.time():
            sess["status"] = "expired"
            return _device_public(sess)
        auth = sess["auth"]
    try:
        grant = client.device_poll_token(auth["realm"], auth["device_code"],
                                         auth["code_verifier"])
    except client.UpstreamError as exc:
        with _DEVICE_LOCK:
            sess = _device_sessions.get(auth_id)
            if sess is not None and exc.kind != "upstream":
                sess["status"] = "denied"
                sess["error"] = exc.message
        return _device_public(_device_sessions.get(auth_id, {"status": "pending"}))
    if grant.get("status") in ("pending", "slow_down"):
        return _device_public(_device_sessions.get(auth_id, {"status": "pending"}))
    # 成功：验证凭证可用（拉一次 profile），再入库
    token = str(grant.get("access_token") or "")
    realm = auth["realm"]
    profile = {}
    try:
        profile = client.fetch_profile(token, realm)
    except client.UpstreamError as exc:
        with _DEVICE_LOCK:
            sess = _device_sessions.get(auth_id)
            if sess is not None:
                sess["status"] = "failed"
                sess["error"] = "凭证验证失败：%s" % exc.message
        return _device_public(sess)
    uid = str(profile.get("user_id") or "")
    record = {"token": token,
              "refresh_token": str(grant.get("refresh_token") or ""),
              "expires_at_ms": int(grant.get("expires_at_ms") or 0),
              "uid": uid,
              "name": str(profile.get("user_name") or ""),
              "realm": realm, "source": "device-auth"}
    account, _created = minimax_accounts.import_record(db, record)
    db.commit()
    # 新票回写官方客户端 auth.json（轮换票互踢根治的一部分）：
    # 客户端与网关共享同一张活票，谁续签谁都同步
    try:
        paths.write_back_credential(
            realm,
            access_token=token,
            refresh_token=str(grant.get("refresh_token") or ""),
            expires_at_ms=int(grant.get("expires_at_ms") or 0))
    except Exception:  # noqa: BLE001 - 回写失败不影响入库
        pass
    with _DEVICE_LOCK:
        sess = _device_sessions.get(auth_id)
        if sess is not None:
            sess["status"] = "done"
            sess["account"] = {"uid": uid, "name": account.name,
                               "account_id": account.id}
    return _device_public(_device_sessions.get(auth_id, {"status": "done"}))


def _device_public(sess: dict) -> dict:
    status = sess.get("status", "pending")
    return {"status": status, "done": status == "done",
            "account": sess.get("account"), "error": sess.get("error"),
            "expires_in": max(0, int(sess.get("expires_at", 0) - time.time()))}


@router.post("/device/cancel")
def device_cancel(auth_id: str, _: bool = Depends(require_admin)):
    with _DEVICE_LOCK:
        sess = _device_sessions.pop(auth_id, None)
    if sess is None:
        return {"canceled": False}
    return {"canceled": True}


@router.get("/auth/qr.svg")
def auth_qr_svg(value: str = "", _: bool = Depends(require_admin)):
    """设备授权二维码（后端渲染 SVG，同源资源不受 CSP 限制）。"""
    if not value.strip():
        raise HTTPException(400, detail="缺少 value")
    import qrcode
    import qrcode.constants
    import qrcode.image.svg

    img = qrcode.make(value.strip(), image_factory=qrcode.image.svg.SvgPathImage,
                      border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
    out = img.to_string()
    svg = out.decode("utf-8") if isinstance(out, bytes) else out
    from fastapi.responses import Response
    return Response(svg, media_type="image/svg+xml",
                    headers={"Cache-Control": "no-store"})


@router.get("/overview")
def overview(_: bool = Depends(require_admin), db=Depends(get_db)):
    from admin.platform.minimax.platform_gateway import MinimaxGateway
    return {"cards": MinimaxGateway().overview_cards(db)}


@router.post("/checkin")
def checkin_all(realm: str = "", _: bool = Depends(require_admin),
                db=Depends(get_db)):
    """批量签到（前端「签到」按钮）：返回与其它平台同形的 {summary, items}。"""
    rows = [row for row in minimax_accounts.accounts_for(db, realm)
            if row.status == "active"]
    items = []
    failed = []
    claimed = already = 0
    for row in rows[:50]:
        label = row.name or ("#" + str(row.id))
        try:
            data = minimax_accounts.ensure_token(db, row)
            token = minimax_accounts.token_of(data)
            acct_realm = minimax_accounts.realm_of(row)
            status = client.signin_status(token, acct_realm,
                                          account_id=int(row.id or 0))
            days = status.get("days") if isinstance(status.get("days"), list) else []
            today = next((d for d in days
                          if isinstance(d, dict) and d.get("is_today")), {})
            if int(today.get("status") or 0) != 3:
                already += 1
                items.append({"account": label, "already_processed": True,
                              "current_balance": row.balance_remain,
                              "daily_balance": 0})
                continue
            result = client.signin_claim(token, acct_realm,
                                         account_id=int(row.id or 0))
            claimed += 1
            items.append({"account": label, "already_processed": False,
                          "current_balance": row.balance_remain,
                          "daily_balance": result.get("points") or 0})
        except client.UpstreamError as exc:
            failed.append({"account": label, "error": exc.message})
    summary = "签到 %d · 已签 %d · 失败 %d（共 %d 号）" % (
        claimed, already, len(failed), len(rows))
    return {"platform": PUBLIC, "ok": not failed, "claimed": claimed,
            "already": already, "items": items, "failed": failed,
            "summary": summary}


@router.get("/export")
def export_accounts(mode: str = "backup", realm: str = "",
                    _: bool = Depends(require_admin), db=Depends(get_db)):
    """导出（backup=含凭证 / plain=脱敏清单），前端通用导出弹窗用。"""
    rows = minimax_accounts.accounts_for(db, realm)
    accounts = []
    for row in rows:
        if mode == "plain":
            accounts.append({"id": row.id, "name": row.name, "uid": row.uid,
                             "realm": minimax_accounts.realm_of(row),
                             "status": row.status,
                             "balance_remain": row.balance_remain,
                             "balance_total": row.balance_total})
        else:
            accounts.append({"id": row.id, "name": row.name, "uid": row.uid,
                             "realm": minimax_accounts.realm_of(row),
                             "status": row.status,
                             "item": row.auth_json})
    return {"platform": PUBLIC, "format": "minimax-" + mode,
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
