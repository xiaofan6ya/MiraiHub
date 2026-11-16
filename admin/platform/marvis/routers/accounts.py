"""腾讯 Marvis 管理接口：`/api/marvis/*`。

路由命名与其它平台保持一致（`/api/<平台>/accounts...`），控制台按同一套约定
渲染账号池页 —— 这样新增平台不需要改前端。
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timedelta

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
# 扫码登录
# ---------------------------------------------------------------------------
# 两条链路，按登录方式分开（2026-11-16）：
#
#   QQ   —— **免客户端**（`POST /auth/begin` + `GET /auth/poll`）：
#           服务端拉起本机 headless 浏览器打开真实授权页，把二维码区域截图给
#           用户扫，再用 DevTools 协议把 `marvis_oauth?code=…` 拦下来自动入库。
#           用户不用复制任何地址，机器上也不用装 Marvis 客户端。
#           为什么不能自己拼 ptlogin 轮询、协议细节与实测记录见
#           `services/webauth.py` 的模块 docstring。
#
#   微信 —— 只能走老链路（`/auth/start` + `/auth/submit`）：
#           该 appid 不是开放平台网站应用（`jslogin` 实测回 `code=400`），
#           服务端拿不到扫码 uuid，只能让用户在授权页里登录、把回调地址交回来。
#
# 老链路（微信，以及 QQ 扫码失败时的兜底）：
#
#   1. POST /auth/start      生成 authorize_url（+ 二维码），用户去那边登录
#   2. 用户在浏览器里登完，被重定向到 marvis_oauth?login_type=..&code=..
#      —— 这个地址在浏览器里打不开（它是上游 API），所以要用户**复制回来**
#   3. POST /auth/submit     把回调地址（或裸 code）交回来，服务端换登录态入库
#   4. GET  /auth/poll       拿最终结果（控制台轮询这条，与灵犀一致）
_AUTH_SESSIONS: dict[str, dict] = {}
_AUTH_TTL = 15 * 60


class AuthBeginIn(BaseModel):
    login_type: str = "QC"
    guid: str = ""


@router.post("/auth/begin")
def auth_begin(body: AuthBeginIn | None = None,
               _: bool = Depends(require_admin)):
    """建一个**免客户端**扫码会话：服务端拉起浏览器打开真实 QQ 授权页。

    返回 `auth_id` + 同源截图地址；前端轮询 `/auth/poll`，扫完自动入库。

    ⚠ 这个接口**不等截图**就返回：拉起浏览器 + 渲染授权页要十几秒，同步等
    会让前端 fetch 超时、页面显示裂图（实测）。图好了由 `shot_version`
    带出来，前端据此换 `<img>` 的 src。
    """
    from admin.platform.marvis.services import webauth

    kind = ((body.login_type if body else "QC") or "QC").upper()
    if kind != "QC":
        raise HTTPException(
            400, detail="免客户端扫码只支持 QQ；微信请用 /auth/start 走授权页流程")
    try:
        return webauth.begin(kind, (body.guid if body else "") or "")
    except RuntimeError as exc:
        raise HTTPException(502, detail="浏览器授权不可用：%s" % exc) from exc


@router.get("/auth/qr.png")
def auth_qr_png(auth_id: str = "", _: bool = Depends(require_admin)):
    """免客户端扫码会话的二维码（授权页里二维码区域的截图）。

    它是**截图**而不是本地渲染的二维码：这一版的码由 QQ 在浏览器里下发，
    服务端拿不到内容，只能把显示出来的那块裁下来。
    """
    from admin.platform.marvis.services import webauth
    from fastapi.responses import Response

    try:
        png = webauth.qr_png(auth_id)
    except KeyError as exc:
        raise HTTPException(404, detail=str(exc)) from exc
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "no-store"})


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
    # `state` 必须是**同一个值**：授权地址里带的、会话里存的、换码时发的。
    # 以前授权地址内部自己又生成一个，结果换码时 state 对不上，上游回
    # `-109 RC_PARAMS_INVALID`（2026-11-15 真机复现）。
    state = endpoints.new_oauth_state(login_type, guid)
    auth_id = uuid.uuid4().hex
    url = endpoints.authorize_url(login_type, guid, state=state)
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

    依赖 `requirements.txt` 里的 `qrcode>=7.4`（与小浣熊的扫码授权同一个包，
    SVG 工厂用 lxml）。PR 原文写的「仓库内联 _vendor/qrcode」实际没有落地 ——
    线上装漏了这个包时这里会 500，所以错误信息直接说明缺的是什么。
    """
    if not text.strip():
        raise HTTPException(400, detail="text 不能为空")
    try:
        import qrcode
        import qrcode.image.svg as svg_factory
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            500,
            detail="二维码库不可用（需要 qrcode>=7.4 与 lxml）：%s" % exc) from exc

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
    # ⚠ **不要在这里按 `status == done` 无脑返回 reused**（2026-11-16）。
    #
    # 原来这里有一句：
    #     if session.get("status") == "done":
    #         return {"ok": True, "account": ..., "reused": True}
    # 它的本意是「同一会话重复提交就复用结果」，但副作用很坏：只要这个会话
    # 成功过一次，**之后提交任何 code 都会被直接判成功**，连上游都不打。
    # 用户重扫后拿新 code 进来，看到的还是上一次的账号 —— 而真正的失败
    # （新 code 无效 / 过期 / 串了会话）被彻底吞掉。
    #
    # 正确做法是下面那条**精确**判断：只有「同一会话 + 同一个 code」才复用
    # （见 `last_code`）。不同 code 一律真的去兑换。
    code = _extract_code(body.callback)
    if not code:
        raise HTTPException(400, detail="回调里没有 code=（请把整条回调地址复制回来）")
    # **同一个 code 只许换一次**（2026-11-16）。
    #
    # 授权码是一次性的：换成功后再换一次，上游必然回 `-107 已过期或已被使用`。
    # 前端有 focus / paste / 点按钮三个入口，历史上出现过同一串 code 被提交
    # 两次的情况 —— 结果是「账号明明入库了，弹窗却报 -107」，用户以为失败又
    # 重扫，越搞越乱。
    #
    # 这里在服务端兜底：同一个会话里已经成功换过、且这次又是一模一样的 code，
    # 直接把那次成功的账号返回（`reused`），不打上游、不报错。
    if session.get("status") == "done" and session.get("last_code") == code:
        return {"ok": True, "account": session.get("account"), "reused": True}
    try:
        credential = client.exchange_code(
            code, session["login_type"], "cn",
            state=session.get("state") or "", guid=session["guid"])
    except client.UpstreamError as exc:
        # -107 还有一个常见来源：用户**上一次其实已经成功了**，但前端没收到
        # 响应（关弹窗/刷新），于是拿同一个 code 再交一次。这种情况不能只报
        # 「已过期」，得先查库里是不是**刚刚**入库过。
        #
        # ⚠ 判据必须收紧：只看「session 开立之后 + 90 秒内创建/更新」的账号。
        # 早期版本按「池里最后一个 active 号」返回，那会把完全无关的旧账号
        # 当成这次的结果 —— 真正的失败被伪装成成功，比报错更坏。
        if str(getattr(exc, "code", "")) == "-107":
            try:
                started = datetime.utcfromtimestamp(
                    float(session.get("at") or 0))
                cutoff = max(started, datetime.utcnow() - timedelta(seconds=90))

                def _recent(a) -> bool:
                    """这个号是不是**刚刚**新建或刚更新过。

                    ⚠ 括号必须写清：`A and B or C` 在 Python 里是
                    `(A and B) or C`，会把「任意近期更新过的号」都算进来 ——
                    一个正在跑业务的旧号每几秒就更新一次 `updated_at`，
                    于是任何 code 都会被判成「已经成功过」，把真正的失败
                    伪装成成功（2026-11-16 实测踩到）。
                    """
                    if (getattr(a, "status", "") or "") != "active":
                        return False
                    created = getattr(a, "created_at", None) or datetime.min
                    updated = getattr(a, "updated_at", None) or datetime.min
                    return created >= cutoff or updated >= cutoff

                fresh = [a for a in marvis_accounts.accounts_for(db, "cn")
                         if _recent(a)]
                if fresh:
                    # ⚠ **不返回成功**（2026-11-16 二次修正）。
                    #
                    # 原来这里 return ok=True + reused=True，本意是「上次其实
                    # 成功了，别让用户再扫一遍」。但代价是：只要 90 秒内有任何
                    # 账号更新过（跑业务的号每几秒就更新一次 `updated_at`），
                    # **任意失效 code 都会被伪装成成功** —— 用户以为换号成功了，
                    # 实际根本没换。这比报错坏得多。
                    #
                    # 现在只把「可能已经成功」这个信息**附在错误里**，让用户
                    # 自己去账号列表确认，绝不代替判断。
                    logger.info(
                        "Marvis -107 且 %.0f 秒内有号更新过 acc=%s（仅提示，不判成功）",
                        (datetime.utcnow() - cutoff).total_seconds(),
                        fresh[-1].id)
                    raise HTTPException(
                        400,
                        detail={
                            "message": exc.message,
                            "step": "exchange",
                            "hint": "注意：账号池里刚有账号变动（%s）。"
                                    "如果那是你上一次授权的号，说明上次已经"
                                    "成功入库，直接去列表确认即可；否则请重新"
                                    "扫码。" % (fresh[-1].name or fresh[-1].id),
                        }) from exc
            except HTTPException:
                # ⚠ **必须单独放行**：`HTTPException` 继承自 `Exception`，
                # 上面那个 `except Exception` 会把它当成「抢救查询失败」吞掉，
                # 于是带 hint 的 400 变成下面那句不带 hint 的通用 400 ——
                # 排查时看到的现象就是「明明有 hint 分支，用户却拿不到」
                # （2026-11-16 自测踩到）。
                raise
            except Exception:  # noqa: BLE001 - 抢救查询失败就按原错误报
                logger.debug("Marvis -107 抢救查询失败", exc_info=True)
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
    # **记下这次真正兑换成功的 code**：前端有 focus / paste / 点按钮三个入口，
    # 同一串 code 有可能被提交两次（第二次上游必然回 -107）。记下来之后，
    # 上面那条幂等判断就能把第二次直接返回上次结果，而不是报一个会吓到用户的
    # 「授权码已过期」。只有真兑换成功才写，抢救分支绝不写。
    session.update({"status": "done", "account": view, "error": "",
                    "last_code": code})
    return {"ok": True, "created": created, "account": view}


@router.get("/auth/poll")
def auth_poll(auth_id: str = "", _: bool = Depends(require_admin),
              db: Session = Depends(get_db)):
    """轮询扫码结果（控制台按灵犀的同一套约定调用）。

    两种会话共用一个入口：
      · `marvis-qr-…`（`/auth/begin` 建的）→ 免客户端扫码，浏览器自己走完授权，
        **成功即入库**，返回 `status=done` + 账号 + `shot_version`；
      · 其它（`/auth/start` 建的）→ 老链路，等 `/auth/submit` 把回调交回来。
    """
    if not auth_id:
        raise HTTPException(400, detail="需要 auth_id")
    from admin.platform.marvis.services import webauth

    if webauth.session_exists(auth_id):
        result = webauth.poll(db, auth_id)
        result["auth_id"] = auth_id
        return result

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
        from admin.platform.marvis.services import webauth

        webauth.cancel(auth_id)
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