"""KroWork 管理接口（`/api/krowork/*`）。

控制台「账号池」页用的全部端点。凭证只有一种形态（id_token），所以这里
比扫码类平台简单：扫描 → 导入 → 刷新 → 导出，四步闭环。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from admin.db import get_db
from admin.models import Account
from admin.platform.krowork.core import client
from admin.platform.krowork.core import token as token_mod
from admin.platform.krowork.services import accounts as ka
from admin.security import require_admin

router = APIRouter(prefix="/api/krowork", tags=["krowork"])


class ImportIn(BaseModel):
    text: str = ""
    items: list[dict] = []
    files: list[str] = []


class PatchIn(BaseModel):
    status: str | None = None
    name: str | None = None


class TokenIn(BaseModel):
    id_token: str = ""
    refresh_token: str = ""


@router.get("/meta")
def meta(_: bool = Depends(require_admin)):
    """平台元信息 + 当前凭证状态（**不含 token 原文**）。"""
    return {
        "platform": ka.PLATFORM,
        "region": "cn",
        "hosts": {
            "llm": "https://llm.krowork.com",
            "api": "https://api.krowork.com",
            "auth": "https://auth.krowork.com/realms/krow/protocol/openid-connect",
        },
        "endpoints": {
            "chat": "{provider_proxy_path}/chat/completions",
            "messages": "/anthropic/v1/messages",
            "responses": "/openai/v1/responses",
            "model_map": "/config/model-map",
            "health": "/health",
        },
        "token": token_mod.status(),
        "data_dir": str(token_mod.data_dir()),
    }


@router.get("/accounts")
def list_accounts(_: bool = Depends(require_admin), db=Depends(get_db)):
    ops = ka.accounts_for(db)
    return {"items": [ka.public_view(row) for row in ops],
            "summary": ka.summarize(db)}


@router.get("/scan-local")
def scan_local(_: bool = Depends(require_admin)):
    return ka.scan()


@router.post("/import-local")
def import_local(_: bool = Depends(require_admin), db=Depends(get_db)):
    result = ka.import_local(db)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/import")
def import_accounts(body: ImportIn, _: bool = Depends(require_admin),
                    db=Depends(get_db)):
    records: list[dict] = list(body.items)
    if body.text.strip():
        try:
            parsed = json.loads(body.text)
        except ValueError as exc:
            raise HTTPException(status_code=400,
                                detail="text 不是合法 JSON：%s" % exc) from None
        if isinstance(parsed, list):
            records.extend(parsed)
        elif isinstance(parsed, dict):
            records.append(parsed)
    imported, errors = [], []
    for record in records:
        try:
            account, created = ka.import_record(db, record)
            imported.append({"id": account.id, "created": created})
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
    return {"imported": len(imported), "items": imported, "errors": errors}


@router.post("/accounts/{account_id}/refresh")
def refresh_account(account_id: int, _: bool = Depends(require_admin),
                    db=Depends(get_db)):
    from admin.platform.krowork.platform_gateway import KroworkAccountOps

    return KroworkAccountOps().refresh(db, account_id, force=True)


@router.post("/accounts/{account_id}/test")
def test_account(account_id: int, _: bool = Depends(require_admin),
                 db=Depends(get_db)):
    from admin.platform.krowork.platform_gateway import KroworkAccountOps

    return KroworkAccountOps().health(db, account_id)


@router.patch("/accounts/{account_id}")
def patch_account(account_id: int, body: PatchIn,
                  _: bool = Depends(require_admin), db=Depends(get_db)):
    row = db.get(Account, account_id)
    if row is None or row.platform != ka.STORAGE:
        raise HTTPException(status_code=404, detail="账号不存在")
    if body.status in ("active", "disabled"):
        row.status = body.status
    if body.name is not None:
        row.name = body.name
    db.commit()
    return {"ok": True, "account": ka.public_view(row)}


@router.delete("/accounts/{account_id}")
def delete_account(account_id: int, _: bool = Depends(require_admin),
                   db=Depends(get_db)):
    row = db.get(Account, account_id)
    if row is None or row.platform != ka.STORAGE:
        raise HTTPException(status_code=404, detail="账号不存在")
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.get("/export")
def export_accounts(mode: str = "safe", _: bool = Depends(require_admin),
                    db=Depends(get_db)):
    from admin.platform.krowork.platform_gateway import KroworkAccountOps

    ops = KroworkAccountOps()
    ops.mode = "backup" if mode == "backup" else ""
    return ops.export(db)


@router.get("/usage")
def usage(days: int = 30, limit: int = 50, _: bool = Depends(require_admin),
          db=Depends(get_db)):
    return ka.usage_stats(db, days=days, limit=limit)


@router.get("/overview")
def overview(_: bool = Depends(require_admin), db=Depends(get_db)):
    from admin.platform.krowork.platform_gateway import KroworkGateway

    return {"cards": KroworkGateway("").overview_cards(db)}


# ---------------------------------------------------------------------------
# 凭证（手动注入 / 状态）
# ---------------------------------------------------------------------------
@router.get("/token")
def token_status(_: bool = Depends(require_admin)):
    return token_mod.status()


@router.post("/token")
def token_write(body: TokenIn, _: bool = Depends(require_admin)):
    """手动写入 id_token（客户端没开、sidecar 不可用时的兜底）。"""
    tok = (body.id_token or "").strip()
    if not tok:
        raise HTTPException(status_code=400, detail="id_token 为空")
    check = ka.verify_token(tok, "cn")
    if not check.get("ok"):
        raise HTTPException(
            status_code=400,
            detail="这枚 token 不可用（上游 %s）：%s"
                   % (check.get("status"), str(check.get("detail"))[:200]))
    path = token_mod.data_dir() / "id_token.txt"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(tok, encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500, detail="写入失败：%s" % exc) from None
    token_mod.STORE.invalidate()
    return {"ok": True, "path": str(path), "verify": check,
            "token": token_mod.status()}


@router.post("/catalog/refresh")
def refresh_catalog(_: bool = Depends(require_admin)):
    model_map = client.fetch_model_map(force=True)
    return {"version": model_map.version, "region": model_map.region,
            "models": len(model_map.models), "slots": len(model_map.slots),
            "items": [ref.public() for ref in model_map.catalog()]}


@router.get("/probe")
def probe(_: bool = Depends(require_admin)):
    return {"llm": client.probe("cn"), "token": token_mod.status()}


# ---------------------------------------------------------------------------
# 手机号授权登录（PKCE + Keycloak SMS）
#
# 为什么必须有这条路：sidecar 只能借客户端**已登好**的态，客户端一登出
# 网关就失明（`KrowAIError: No credentials available`）。这里让网关自己走
# 一遍 PKCE，独立拿票入池。
#
# 三步：begin（拿登录页）→ send-code（发短信）→ submit（交码换票入池）
# `redirect_uri` 白名单只认 http://127.0.0.1:17321/callback（被客户端占着），
# 所以网关**不收回调**，而是在 HTTP 客户端里拦 302 直接读 code。
# ---------------------------------------------------------------------------
class BeginIn(BaseModel):
    realm: str = "cn"


class SendCodeIn(BaseModel):
    auth_id: str
    phone: str = ""
    country: str = "+86"


class SubmitIn(BaseModel):
    auth_id: str
    code: str = ""
    phone: str = ""
    country: str = "+86"


class RedeemIn(BaseModel):
    callback_url: str = ""
    verifier: str = ""
    realm: str = "cn"


@router.post("/auth/begin")
def auth_begin(body: BeginIn = BeginIn(), _: bool = Depends(require_admin)):
    """建授权会话，返回可直接打开的 `auth_url`。"""
    from admin.platform.krowork.core import webauth

    result = webauth.begin(body.realm or "cn")
    if result.get("status") == "failed":
        raise HTTPException(status_code=502, detail=result.get("detail"))
    return result


@router.post("/auth/send-code")
def auth_send_code(body: SendCodeIn, _: bool = Depends(require_admin)):
    from admin.platform.krowork.core import webauth

    result = webauth.send_code(body.auth_id, body.phone, body.country or "+86")
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("detail"))
    return result


@router.post("/auth/submit")
def auth_submit(body: SubmitIn, _: bool = Depends(require_admin)):
    """交验证码 → 换 token → 入号池。成功后号池多一个 `source=sms-login` 的号。"""
    from admin.platform.krowork.core import webauth

    result = webauth.submit(body.auth_id, body.code, body.phone, body.country or "+86")
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("detail") or "登录失败")
    return result


@router.get("/auth/session")
def auth_session(auth_id: str, _: bool = Depends(require_admin)):
    from admin.platform.krowork.core import webauth

    session = webauth.get_session(auth_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")
    return session.public()


@router.post("/auth/cancel")
def auth_cancel(body: BeginIn = BeginIn(), auth_id: str = "",
                _: bool = Depends(require_admin)):
    from admin.platform.krowork.core import webauth

    return webauth.cancel(auth_id)


@router.post("/auth/redeem")
def auth_redeem(body: RedeemIn, _: bool = Depends(require_admin),
                db=Depends(get_db)):
    """手动兑换：把「回调 URL + verifier」换成票并入库（不经过短信）。"""
    from admin.platform.krowork.core import webauth

    result = webauth.redeem(body.callback_url, body.verifier, body.realm or "cn")
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("detail"))
    account, created = ka.import_record(db, {
        "id_token": result["id_token"],
        "refresh_token": result.get("refresh_token") or "",
        "source": "redeem",
        "region": body.realm or "cn",
    })
    token_mod.STORE.invalidate()
    return {"ok": True, "account_id": account.id, "created": created,
            "token": token_mod.status()}


class CallbackIn(BaseModel):
    auth_id: str = ""
    callback: str = ""


@router.post("/auth/submit-callback")
def auth_submit_callback(body: CallbackIn, _: bool = Depends(require_admin)):
    """**主路径**：粘贴浏览器回调地址，用会话里的 verifier 换票。

    为什么不要求用户手填 verifier：`begin` 时 verifier 存在服务端会话里，
    `redirect_uri` 白名单只认 `127.0.0.1:17321`（被客户端占着），
    所以用户只需要复制地址栏那条 URL —— 这是整条流程里唯一的人工动作。
    """
    from admin.platform.krowork.core import webauth

    session = webauth.get_session(body.auth_id) if body.auth_id else None
    # 没带 auth_id 时（会话过期/换设备），退回「无会话」模式：
    # 仍然要求 verifier，但能从 body 里带 —— 这里先只支持有会话的路径。
    if session is None:
        raise HTTPException(
            status_code=400,
            detail="授权会话已过期（10 分钟），请点「重新发起授权」再登一次")
    result = webauth.redeem(body.callback, session.verifier, session.realm)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("detail"))
    account_id = 0
    detail = ""
    try:
        from admin.db import SessionLocal
        from admin.platform.krowork.services import accounts as ka_local

        with SessionLocal() as db:
            account, _created = ka_local._upsert(db, {
                "id_token": result["id_token"],
                "refresh_token": result.get("refresh_token") or "",
                "expires_at": int(result.get("expires_at") or 0),
                "source": "callback",
                "region": session.realm,
            })
            db.commit()
            account_id = int(account.id or 0)
    except Exception as exc:  # noqa: BLE001
        detail = "登录成功，但写库失败：%s" % exc
    token_mod.STORE.invalidate()
    session.status = "done"
    session.account_id = account_id
    claims = token_mod.jwt_payload(result["id_token"])
    return {
        "ok": True,
        "account_id": account_id,
        "detail": detail or "登录成功，账号已入库",
        "email": claims.get("preferred_username") or claims.get("email") or "",
        "expires_at": int(result.get("expires_at") or 0),
        "token": token_mod.status(),
    }


# ---------------------------------------------------------------------------
# 扫描诊断（回答「为什么本地扫描拿不到票」）
# ---------------------------------------------------------------------------
@router.get("/diagnose")
def diagnose(_: bool = Depends(require_admin)):
    """逐段检查取票链，指出**断在哪一段**，不笼统报「失败」。"""
    from pathlib import Path

    folder = token_mod.data_dir()
    stages: dict[str, dict] = {}

    # 1) 客户端是否在跑
    running = False
    try:
        import subprocess

        res = subprocess.run(["tasklist", "/FI", "IMAGENAME eq krow-app.exe"],
                             capture_output=True, text=True, timeout=8)
        running = "krow-app.exe" in (res.stdout or "")
    except Exception:  # noqa: BLE001
        running = False
    stages["client"] = {
        "ok": running,
        "detail": "krow-app.exe 正在运行" if running else "客户端没在运行（sidecar 取不到票）",
    }

    # 2) 数据目录与关键文件
    files = {}
    for name in ("config.json", ".krowd_token", ".runtime-token",
                 "credentials.bin", "id_token.txt", "model-map-cn.json"):
        path = folder / name
        try:
            files[name] = path.stat().st_size if path.is_file() else None
        except OSError:
            files[name] = None
    stages["files"] = {
        "ok": files.get("config.json") is not None,
        "detail": "；".join("%s=%s" % (k, ("%dB" % v) if v is not None else "缺失")
                           for k, v in files.items()),
        "size": files,
    }

    # 3) sidecar 解释器
    exe = token_mod.sidecar_python()
    stages["sidecar_python"] = {
        "ok": bool(exe),
        "detail": exe or "没找到能 import krow_openai 的 Python 3.12 环境",
    }

    # 4) 实际取票
    snap = token_mod.current(force=True)
    stages["token"] = {
        "ok": snap.ok,
        "detail": ("来源 %s，剩余 %d 分钟" % (snap.source,
                                            max(0, int((snap.expires_at - __import__("time").time()) // 60))))
        if snap.ok else (snap.detail or token_mod.STORE.last_error or "取不到票"),
    }

    # 5) 上游可达性
    probe_result = client.probe("cn")
    stages["upstream"] = {
        "ok": bool(probe_result.get("ok")),
        "detail": "%s /health -> %s" % (client.endpoints.HOST_CN,
                                        probe_result.get("status")),
    }

    return {
        "ok": all(s.get("ok") for s in stages.values()),
        "stages": stages,
        "hint": ("客户端没登录时 sidecar 拿不到票 —— 用 /api/krowork/auth/begin "
                 "走手机号授权，网关自己取票，不依赖客户端。"),
    }
