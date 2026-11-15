"""腾讯 Marvis 管理接口：`/api/marvis/*`。

路由命名与其它平台保持一致（`/api/<平台>/accounts...`），控制台按同一套约定
渲染账号池页 —— 这样新增平台不需要改前端。
"""
from __future__ import annotations

import json
import logging
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from admin.db import get_db
from admin.platform.marvis.core import client, endpoints
from admin.platform.marvis.services import accounts as marvis_accounts
from admin.security import require_admin

logger = logging.getLogger("admin.platform.marvis.routers")
router = APIRouter(prefix="/api/marvis", tags=["marvis"])


@router.get("/meta")
def meta(_: bool = Depends(require_admin)):
    return {
        "platform": endpoints.PLATFORM,
        "label": "腾讯 Marvis",
        "regions": [{"key": key, "label": conf["label"],
                     "base_url": conf["base_url"]}
                    for key, conf in endpoints.REGIONS.items()],
        "auth": "Ual-Access-Openid / Access-Token / Login-Type / Guid + MarvisExt",
        "quota_kind": "tokens",
        "note": "令牌钱包制：每号每日 1000 万计费 token（缓存命中免费、输入输出五折）；"
                "支持 QQ / 微信扫码登录与凭证导入",
        "login_types": [{"key": "QC", "label": "QQ 扫码"},
                        {"key": "WX", "label": "微信扫码"}],
        "models": len(endpoints.MODEL_TABLE),
    }


@router.get("/models")
def list_models(_: bool = Depends(require_admin)):
    """静态模型目录（上游没有 /v1/models）。"""
    return {"items": endpoints.models_catalog()}


# ---------------------------------------------------------------------------
# 账号读写
# ---------------------------------------------------------------------------
class ImportIn(BaseModel):
    """导入：`text` 接受 JSON 数组 / JSONL / 单个对象。"""

    text: str = ""
    credential: dict | None = None


@router.post("/import")
def import_account(body: ImportIn, _: bool = Depends(require_admin),
                   db: Session = Depends(get_db)):
    raw = body.text or (json.dumps(body.credential, ensure_ascii=False)
                        if body.credential else "")
    if not raw.strip():
        raise HTTPException(400, detail="需要 text 或 credential")
    result = marvis_accounts.import_batch(db, raw)
    if result.get("total") == 0:
        raise HTTPException(400, detail="没有解析到任何凭证")
    return result


@router.post("/import-batch")
def import_batch(body: ImportIn, _: bool = Depends(require_admin),
                 db: Session = Depends(get_db)):
    """与 `/import` 同义 —— 控制台「导入账号」按平台的 import-batch 惯例调用。"""
    return import_account(body, _, db)


class AccountPatch(BaseModel):
    status: str | None = None
    name: str | None = None


@router.get("/accounts")
def list_accounts(realm: str = "", _: bool = Depends(require_admin),
                  db: Session = Depends(get_db)):
    from admin.platform.marvis.platform_gateway import MarvisAccountOps

    return MarvisAccountOps(realm).list(db)


@router.post("/accounts/{acc_id}/refresh")
def refresh(acc_id: int, _: bool = Depends(require_admin),
            db: Session = Depends(get_db)):
    from admin.platform.marvis.platform_gateway import MarvisAccountOps

    try:
        return MarvisAccountOps("").refresh(db, acc_id, force=True)
    except ValueError as exc:
        raise HTTPException(404, detail=str(exc)) from exc


@router.post("/accounts/{acc_id}/test")
def test_account(acc_id: int, _: bool = Depends(require_admin),
                 db: Session = Depends(get_db)):
    """健康检查：读一次令牌钱包 + 发一次 1 token 的真实对话。

    只读钱包也可能过 —— 有些号钱包能读但模型调不动。所以两步都做，
    结果分开报，用户能看出是「号没额度」还是「号被风控」。
    """
    account = db.get(__import__("admin.models", fromlist=["Account"]).Account,
                     acc_id)
    if account is None or account.platform != endpoints.PLATFORM:
        raise HTTPException(404, detail="账号不存在")
    result: dict = {"id": acc_id, "ok": False, "wallet": None, "chat": None,
                    "errors": []}
    try:
        result["wallet"] = marvis_accounts.sync_quota(db, account)
        result["ok"] = True
    except client.UpstreamError as exc:
        result["errors"].append({"step": "wallet", **exc.to_dict()})
    try:
        data = marvis_accounts.ensure_token(db, account)
        probe = client.chat_completions(
            data, marvis_accounts.realm_of(account),
            {"model": "main-auto",
             "messages": [{"role": "user", "content": "ping"}],
             "max_tokens": 1, "stream": False},
            account_id=acc_id)
        result["chat"] = {"ok": True,
                          "usage": (probe or {}).get("usage") or {}}
        result["ok"] = True
    except client.UpstreamError as exc:
        result["chat"] = {"ok": False, "error": exc.to_dict()}
        result["errors"].append({"step": "chat", **exc.to_dict()})
    return result


@router.patch("/accounts/{acc_id}")
def patch_account(acc_id: int, body: AccountPatch,
                  _: bool = Depends(require_admin),
                  db: Session = Depends(get_db)):
    from admin.models import Account

    account = db.get(Account, acc_id)
    if account is None or account.platform != endpoints.PLATFORM:
        raise HTTPException(404, detail="账号不存在")
    if body.status in ("active", "disabled"):
        account.status = body.status
        if body.status == "active":
            account.cool_until = None
            account.cool_kind = ""
    if body.name:
        account.name = body.name[:120]
    db.commit()
    return marvis_accounts.public_view(account)


@router.delete("/accounts/{acc_id}")
def delete_account(acc_id: int, _: bool = Depends(require_admin),
                   db: Session = Depends(get_db)):
    from admin.models import Account, AccountModelCooldown

    account = db.get(Account, acc_id)
    if account is None or account.platform != endpoints.PLATFORM:
        raise HTTPException(404, detail="账号不存在")
    db.query(AccountModelCooldown).filter(
        AccountModelCooldown.account_id == acc_id).delete(
        synchronize_session=False)
    db.delete(account)
    db.commit()
    return {"id": acc_id, "ok": True}


@router.get("/export")
def export_accounts(mode: str = "backup", realm: str = "",
                    _: bool = Depends(require_admin),
                    db: Session = Depends(get_db)):
    """导出凭证（默认只给最小字段，`mode=full` 才带 token 原文）。"""
    rows = marvis_accounts.accounts_for(db, realm)
    items = []
    for account in rows:
        data = marvis_accounts.credentials_of(account)
        if mode != "full":
            data = {k: v for k, v in data.items()
                    if k in ("openid", "login_type", "nickname", "guid")}
        items.append(data)
    return {"platform": endpoints.PLATFORM, "mode": mode,
            "count": len(items), "accounts": items}


# ---------------------------------------------------------------------------
# 额度 / 用量 / 概览
# ---------------------------------------------------------------------------
@router.get("/credits")
def credits(realm: str = "", _: bool = Depends(require_admin),
            db: Session = Depends(get_db)):
    rows = marvis_accounts.accounts_for(db, realm)
    items = [{"id": a.id, "name": a.name,
              "available": int(a.balance_remain or 0),
              "total": int(a.balance_total or 0),
              "unit": "tokens",
              "status": a.status,
              "last_err": a.last_err_msg or ""}
             for a in rows]
    summary = marvis_accounts.summarize_db(db, realm)
    return {"items": items,
            "summary": {"available": summary["balance_remain"],
                        "total": summary["balance_total"],
                        "accounts": summary["accounts"]}}


@router.get("/overview")
def overview(realm: str = "", _: bool = Depends(require_admin),
             db: Session = Depends(get_db)):
    from admin.platform.marvis.platform_gateway import MarvisGateway

    rows = marvis_accounts.accounts_for(db, realm)
    return {
        "platform": endpoints.PLATFORM,
        "label": "腾讯 Marvis",
        "accounts": len(rows),
        "active": sum(1 for a in rows if a.status == "active"),
        "balance_total": sum(int(a.balance_total or 0) for a in rows),
        "balance_remain": sum(int(a.balance_remain or 0) for a in rows),
        "models": len(endpoints.MODEL_TABLE),
        "cards": MarvisGateway(realm).overview_cards(db),
    }


@router.get("/usage")
def usage(limit: int = 50, _: bool = Depends(require_admin),
          db: Session = Depends(get_db)):
    from admin.models import UsageLog

    rows = (db.query(UsageLog)
            .filter(UsageLog.platform == endpoints.PLATFORM)
            .order_by(UsageLog.id.desc()).limit(max(1, min(limit, 500))).all())
    return {"items": [{"id": r.id, "model": r.model,
                       "total_tokens": r.total_tokens,
                       "credits": r.credits,
                       "at": r.created_at.isoformat() if r.created_at else None}
                      for r in rows]}


# ---------------------------------------------------------------------------
# 扫码登录（QQ / 微信）
# ---------------------------------------------------------------------------
# 流程（与 QQ / 微信 OAuth 的固有形态一致，不能省步骤）：
#
#   1. POST /auth/start      生成 authorize_url（+ 二维码），用户去那边登录
#   2. 用户在浏览器里登完，被重定向到 marvis_oauth?login_type=..&code=..
#      —— 这个地址在浏览器里打不开（它是上游 API），所以要用户**复制回来**
#   3. POST /auth/submit     把回调地址（或裸 code）交回来，服务端换登录态入库
#   4. GET  /auth/poll       拿最终结果（控制台轮询这条，与灵犀一致）
#
# 为什么不自己把浏览器流程自动化：QQ / 微信的登录页有滑块与设备校验，
# 在服务端无头浏览器里跑成功率极低，而且要把用户密码交给网关 —— 不做。
_AUTH_SESSIONS: dict[str, dict] = {}
_AUTH_TTL = 15 * 60


def _gc_auth() -> None:
    now = time.time()
    for key in [k for k, v in _AUTH_SESSIONS.items()
                if now - float(v.get("at") or 0) > _AUTH_TTL]:
        _AUTH_SESSIONS.pop(key, None)


class AuthStartIn(BaseModel):
    login_type: str = "QC"          # QC=QQ / WX=微信


@router.post("/auth/start")
def auth_start(body: AuthStartIn | None = None,
               _: bool = Depends(require_admin)):
    """开一个扫码会话，返回授权页地址与二维码地址。

    ⚠ 这里**不发任何网络请求** —— 只拼 URL。拉模型 / 探测必须等用户真的登完，
    否则「点登录」会转圈很久（magpie 插件侧踩过的同一个坑）。
    """
    _gc_auth()
    login_type = ((body.login_type if body else "QC") or "QC").upper()
    if login_type not in ("QC", "WX"):
        raise HTTPException(400, detail="login_type 只能是 QC 或 WX")
    guid = str(uuid.uuid4())
    # QQ 的 state 必须与渲染层一致是**小数**，写整数会被判参数无效（实测）。
    state = "" if login_type == "WX" else ("%.7f" % (time.time() % 100))
    auth_id = uuid.uuid4().hex
    url = endpoints.authorize_url(login_type, guid)
    _AUTH_SESSIONS[auth_id] = {
        "login_type": login_type, "guid": guid, "state": state,
        "at": time.time(), "status": "pending", "account": None, "error": "",
    }
    return {
        "auth_id": auth_id,
        "login_type": login_type,
        "guid": guid,
        "url": url,
        "authorize_url": url,
        "qr": "/api/marvis/auth/qr.svg?text=%s" % _quote(url),
        "hint": "用手机 QQ / 微信扫码或打开授权页完成登录；"
                "登录后浏览器会跳到 marvis_oauth?code=…（页面打不开是正常的），"
                "把那条地址整段复制回来提交即可。",
    }


@router.get("/auth/url")
def auth_url(login_type: str = "QC", _: bool = Depends(require_admin)):
    """只要授权页地址（不开会话，给「先看一眼」用）。"""
    kind = (login_type or "QC").upper()
    if kind not in ("QC", "WX"):
        raise HTTPException(400, detail="login_type 只能是 QC 或 WX")
    url = endpoints.authorize_url(kind, str(uuid.uuid4()))
    return {"login_type": kind, "url": url,
            "qr": "/api/marvis/auth/qr.svg?text=%s" % _quote(url)}


@router.get("/auth/qr.svg")
def auth_qr(text: str = "", _: bool = Depends(require_admin)):
    """把任意文本（这里就是授权页地址）渲染成 SVG 二维码。

    用仓库内联的 `qrcode`（`_vendor/qrcode`）—— 路由器上没有这个包，
    所以不能依赖系统安装。
    """
    if not text.strip():
        raise HTTPException(400, detail="text 不能为空")
    try:
        import qrcode
        import qrcode.image.svg as svg_factory
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, detail="二维码库不可用：%s" % exc) from exc

    image = qrcode.make(text, image_factory=svg_factory.SvgPathImage, box_size=10,
                        border=2)
    from io import BytesIO

    buffer = BytesIO()
    image.save(buffer)
    from fastapi.responses import Response

    return Response(content=buffer.getvalue(), media_type="image/svg+xml",
                    headers={"Cache-Control": "no-store"})


class AuthSubmitIn(BaseModel):
    auth_id: str
    callback: str = ""      # 回调地址 / 裸 code / 含 code 的 JSON


@router.post("/auth/submit")
def auth_submit(body: AuthSubmitIn, _: bool = Depends(require_admin),
                db: Session = Depends(get_db)):
    """用回调里的 code 换登录态并入库。"""
    session = _AUTH_SESSIONS.get(body.auth_id)
    if session is None:
        raise HTTPException(404, detail="扫码会话不存在或已过期，请重新开始")
    if session.get("status") == "done":
        return {"ok": True, "account": session.get("account"), "reused": True}
    code = _extract_code(body.callback)
    if not code:
        raise HTTPException(400, detail="回调里没有 code=（请把整条回调地址复制回来）")
    try:
        credential = client.exchange_code(
            code, session["login_type"], "cn",
            state=session.get("state") or "", guid=session["guid"])
    except client.UpstreamError as exc:
        session["status"] = "failed"
        session["error"] = exc.message
        raise HTTPException(400, detail={"message": exc.message,
                                         "step": "exchange"}) from exc
    try:
        account, created = marvis_accounts.import_record(db, credential)
    except ValueError as exc:
        session["status"] = "failed"
        session["error"] = str(exc)
        raise HTTPException(400, detail=str(exc)) from exc
    # 登录后顺手读一次钱包，卡片立刻有数（失败不影响登录结果）
    try:
        marvis_accounts.sync_quota(db, account)
    except Exception:  # noqa: BLE001
        logger.debug("登录后额度同步失败", exc_info=True)
    view = marvis_accounts.public_view(account)
    session.update({"status": "done", "account": view, "error": ""})
    return {"ok": True, "created": created, "account": view}


@router.get("/auth/poll")
def auth_poll(auth_id: str = "", _: bool = Depends(require_admin)):
    """轮询扫码结果（控制台按灵犀的同一套约定调用）。"""
    if not auth_id:
        raise HTTPException(400, detail="需要 auth_id")
    session = _AUTH_SESSIONS.get(auth_id)
    if session is None:
        raise HTTPException(404, detail="扫码会话不存在或已过期")
    return {"auth_id": auth_id,
            "status": session.get("status") or "pending",
            "login_type": session.get("login_type"),
            "account": session.get("account"),
            "error": session.get("error") or ""}


@router.post("/auth/cancel")
def auth_cancel(auth_id: str = "", _: bool = Depends(require_admin)):
    if auth_id:
        _AUTH_SESSIONS.pop(auth_id, None)
    return {"ok": True}


def _quote(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")


def _extract_code(raw: str) -> str:
    """从回调地址 / 纯 code / JSON 里抠出授权码。"""
    text = (raw or "").strip()
    if not text:
        return ""
    from urllib.parse import urlparse, parse_qs, unquote

    try:
        parsed = urlparse(text)
        values = parse_qs(parsed.query or "")
        if values.get("code"):
            return unquote(values["code"][0])
    except Exception:  # noqa: BLE001
        pass
    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except Exception:  # noqa: BLE001
            return ""
        for candidate in (payload.get("code"),
                          (payload.get("user_info") or {}).get("code"),
                          (payload.get("data") or {}).get("code")):
            if isinstance(candidate, str) and len(candidate) > 4:
                return candidate
        # 解析成功但没有字符串 code —— 多半是失败响应（`{"code":-109}`）。
        # 数字是业务错误码，不是授权码，不能拿去换登录态。
        return ""
    if text.startswith("code="):
        return unquote(text[5:].split("&")[0])
    return text