"""Loomy 管理接口：`/api/loomy/*`。

    GET  /api/loomy/meta            平台元信息
    GET  /api/loomy/scan-local      扫描本机（含 baseURL / 模型清单）
    POST /api/loomy/import-local    **从本机明文凭证一键导入**
    POST /api/loomy/import          导入指定凭证
    GET  /api/loomy/accounts        账号列表（按平台隔离）
    POST /api/loomy/accounts/{id}/refresh   探活并刷新
    POST /api/loomy/accounts/{id}/test      连通性检查
    POST /api/loomy/accounts/{id}/password  **存手机号+密码（开启自动续期）**
    POST /api/loomy/accounts/{id}/renew     **立刻用密码换一张新 session**
    POST /api/loomy/renew-batch             批量续期（并发，到期前自动跑）
    GET  /api/loomy/usage           用量（本网关账本）
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
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
        # Loomy 上游只有 models / chat / points 几条业务接口，**没有任何登录
        # 或授权端点**（实测 /auth/login、/session/refresh 等全部 404）。
        # 凭证只有一个明文 session，来源是本机 auth-session.json。
        # 所以它做不到「在线授权登录」—— 不是我们没实现，是上游不提供。
        # 前端据此隐藏授权入口并解释原因。
        "auth_mode": "local-session-only",
        "auth_explain": ("Loomy 没有开放授权接口，凭证只能来自官方客户端在本机生成的 "
                         "auth-session.json。请先在官方 App 登录，再用「扫描本机」导入。"),
        # ---- 续期能力（前端据此决定要不要显示「不可续期」的提示）----
        #
        # 这一段是**硬事实**，不是策略选择：
        #   · 账号服务 17 个端点里没有任何 refresh / setPwd；
        #   · 12 个候选续期路径实测全部 404；
        #   · session 只在登录时签发，有效期写死 14 天。
        # 所以只有「拿到过密码」的号才能在网关侧续期。
        "renewal": {
            "auto": "password",
            "window_days": 14,
            "renew_before_days": loomy_accounts.SESSION_RENEW_BEFORE_DAYS,
            "requires_password": True,
            "has_refresh_token": False,
            "note": ("只有用**手机号+密码**注册/登录过的号才支持自动续签"
                     "（剩余不足 3 天时用 byPwd 重登）。"
                     "接码手机号注册且没设密码的号，14 天到期后只能："
                     "① 重新用 /api/loomy/register 注册并设置密码；"
                     "② 用短信验证码重登（/api/loomy/accounts/sms-login，需能再收到码）；"
                     "③ 到期淘汰。"),
            "paths": [
                {"id": "register", "label": "注册时设密码（一劳永逸）",
                 "api": "POST /api/loomy/register"},
                {"id": "sms", "label": "短信验证码重登（14 天一次）",
                 "api": "POST /api/loomy/accounts/sms-login"},
                {"id": "password", "label": "给已导入的号补密码",
                 "api": "POST /api/loomy/accounts/{id}/password"},
            ],
        },
    }


@router.get("/scan-local")
def scan_local(_: bool = Depends(require_admin)):
    """扫描本机（**结果不含完整 token**）。"""
    return loomy_scan.discover()


class ImportLocalIn(BaseModel):
    """本机导入参数。

    uids：前端「扫描 → 勾选 → 确认」里用户选中的账号 userid。
    省略 body（或 uids=None）= 全部导入 —— 兼容旧调用方。
    """

    uids: list[str] | None = None


@router.get("/export")
def export(realm: str = "", mode: str = "", reveal: int = 0,
           _: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """导出号池（issue #25：Loomy 之前只有导入、没有导出）。

    ## 两档，口径与控制台通用导出按钮一致

    `mode=plain`（默认）→ **脱敏清单**：uid / 手机号打码 / 余额 / 到期时间，
    可以直接发给别人做台账，不含任何能登录的东西。
    `mode=backup`       → **完整凭证**（含 session token），只能自己留档。

    `reveal=1` 是老参数，等同 `mode=backup`。

    返回体同时给 `accounts` 数组（通用导出按钮读它）和 `text`（JSONL，
    与 `/import-batch` 严格对称）—— 导出的文件可以直接喂回导入，
    实现「换机器搬号池」。
    """
    from admin.platform.loomy.services import accounts as loomy_accounts

    full = bool(reveal) or str(mode or "").strip().lower() == "backup"
    rows = loomy_accounts.accounts_for(db, _realm_or_400(realm))
    records: list[dict] = []
    for account in rows:
        record = loomy_accounts.export_record(account, reveal=full)
        if record:
            records.append(record)
    return {"realm": realm, "count": len(records), "mode": "backup" if full else "plain",
            "reveal": full, "format": "jsonl",
            "accounts": records,
            "text": "\n".join(json.dumps(item, ensure_ascii=False) for item in records),
            "note": ("完整凭证含登录态，请私密保存"
                     if full else "已脱敏：不含 session token，不能直接导入同一账号")}


@router.post("/import-local")
def import_local(body: ImportLocalIn | None = None,
                 _: bool = Depends(require_admin),
                 db: Session = Depends(get_db)):
    """从本机 `auth-session.json` 导入（前端两步确认后调用）。"""
    return loomy_accounts.import_from_local(
        db, uids=body.uids if body else None)


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


class ImportBatchIn(BaseModel):
    """手机版备份：JSONL（每行一条）或 JSON 数组。

    形态 `{"realm","token","uid","phone","expires_at"}`，不是 WorkBuddy 的
    `auth/account` 客户端凭证 —— 通用 `/api/accounts/batch` 会拒收，
    所以这里走 Loomy 自己的导入逻辑（issue #8）。
    """
    text: str = ""


@router.post("/import-batch")
def import_batch(body: ImportBatchIn, _: bool = Depends(require_admin),
                 db: Session = Depends(get_db)):
    return loomy_accounts.import_batch(db, body.text)


@router.get("/accounts")
def list_accounts(realm: str = "", _: bool = Depends(require_admin),
                  db: Session = Depends(get_db), request: Request = None):
    from admin.platform.loomy.platform_gateway import LoomyAccountOps
    from admin.platform.shared import list_page_params

    lp = list_page_params(request)
    return LoomyAccountOps(_realm_or_400(realm)).list(
        db, page=lp["page"] if lp["paginated"] else None,
        page_size=lp["page_size"], search=lp["search"], status=lp["status"],
        sort=lp["sort"], min_credits=lp["min_credits"],
        max_credits=lp["max_credits"])


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


class CredentialIn(BaseModel):
    loginid: str = ""
    password: str = ""


class SmsCodeIn(BaseModel):
    phone: str = ""


class AuthSmsIn(BaseModel):
    phone: str = ""


class AuthVerifyIn(BaseModel):
    phone: str = ""
    code: str = ""
    msgid: str = ""
    password: str = ""
    name: str = ""


class AuthPasswordIn(BaseModel):
    phone: str = ""
    password: str = ""
    name: str = ""


@router.post("/auth/sms")
def auth_send_sms(body: AuthSmsIn, _: bool = Depends(require_admin)):
    """① 发登录验证码（手机号，和 WorkBuddy 的登录流程同一个手感）。"""
    from admin.platform.loomy.services import auth as loomy_auth

    try:
        return loomy_auth.send_code(body.phone)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - 上游 SDK 异常也转成 400
        raise HTTPException(400, detail="发送验证码失败：%s" % str(exc)[:160]) from exc


@router.post("/auth/verify")
def auth_verify(body: AuthVerifyIn, _: bool = Depends(require_admin),
                db: Session = Depends(get_db)):
    """② 验证码 → 登录（老号）或注册（新号）→ 凭证直接入库。

    新号必须带 `password`（账号服务没有补设密码的接口）；缺密码时返回
    `need_password: true`，前端补一次输入再调一次即可。
    """
    from admin.platform.loomy.services import auth as loomy_auth

    try:
        return loomy_auth.verify(db, phone=body.phone, code=body.code,
                                 msgid=body.msgid, password=body.password,
                                 name=body.name)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, detail="授权失败：%s" % str(exc)[:160]) from exc


@router.post("/auth/password")
def auth_password(body: AuthPasswordIn, _: bool = Depends(require_admin),
                  db: Session = Depends(get_db)):
    """手机号 + 密码登录（不需要短信）——换机 / 续期都走这条。"""
    from admin.platform.loomy.services import auth as loomy_auth

    try:
        return loomy_auth.password_login(db, phone=body.phone,
                                          password=body.password,
                                          name=body.name)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, detail="登录失败：%s" % str(exc)[:160]) from exc


class SmsLoginIn(BaseModel):
    phone: str = ""
    code: str = ""
    msgid: str = ""


class RegisterIn(BaseModel):
    phone: str = ""
    code: str = ""
    password: str = ""
    name: str = ""


@router.post("/register/sms")
def register_sms(body: SmsCodeIn, _: bool = Depends(require_admin)):
    """给注册用的手机号发验证码（接码号在这一步把码填进来）。"""
    from admin.platform.loomy.core import xfyun

    phone = str(body.phone or "").strip()
    if not phone:
        raise HTTPException(400, detail="手机号不能为空")
    try:
        data = xfyun.send_sms_code(phone)
    except xfyun.XfyunError as exc:
        raise HTTPException(400, detail="%s（%s）" % (exc.message, exc.code or "?")) from exc
    return {"ok": True, "msgid": data.get("msgid") or "", "expire": data.get("expire") or 300}


@router.post("/register")
def register(body: RegisterIn, _: bool = Depends(require_admin),
             db: Session = Depends(get_db)):
    """**手机号注册并立刻设密码**，然后导入号池。

    为什么这条接口是接码号的唯一出路：

     - 账号服务 17 个端点里没有「给已有号补设密码」，也没有 refresh；
     - 短信注册那一刻是**唯一**能设密码的机会。

    接码号收不到第二次短信，所以：

       ✗ 没密码 → session 14 天后报废，续不了
       ✓ 注册时带密码 → 之后一直用密码自动续期，永不过期

    流程：`POST /register/sms` 拿码 → `POST /register`（phone + code + password）
    → 自动导入号池 → 保活任务接手，剩余 <3 天自动重登。
    """
    from admin.platform.loomy.core import xfyun

    phone = str(body.phone or "").strip()
    code = str(body.code or "").strip()
    password = str(body.password or "")
    if not phone or not code:
        raise HTTPException(400, detail="手机号与验证码都要填")
    if not password:
        raise HTTPException(
            400,
            detail="必须设置密码：接码号收不到第二次短信，没有密码就无法续期")

    devid = xfyun.new_device_id()
    try:
        got = xfyun.register_by_phone(phone, code, password, devid=devid)
    except xfyun.XfyunError as exc:
        raise HTTPException(400, detail="%s（%s）" % (
            exc.message, exc.code or "?")) from exc

    record = {
        "realm": "cn",
        "token": got["session"],
        "uid": got.get("userid") or "",
        "phone": got.get("phone") or phone,
        "name": body.name or ("Loomy %s" % (phone[-4:] or "account")),
        "updatedAt": int(__import__("time").time() * 1000),
        # 绑一个独立设备标识，之后每次续期都复用它
        "devid": devid,
        "loginid": phone,
        "password": password,
    }
    try:
        result = loomy_accounts.import_record(db, record)
    except Exception as exc:  # noqa: BLE001 - 导入失败要如实报，别吞
        raise HTTPException(400, detail="注册成功但导入失败：%s" % exc) from exc

    account = None
    try:
        from admin.models import Account

        account = db.get(Account, result.get("account_id"))
        if account is not None:
            data = loomy_accounts.credentials_of(account)
            data.update({"loginid": phone, "password": password,
                         "devid": devid,
                         "updatedAt": record["updatedAt"]})
            import json as _json

            account.auth_json = _json.dumps(data, ensure_ascii=False)
            db.commit()
    except Exception:  # noqa: BLE001 - 密码写回失败不影响注册结果
        db.rollback()

    return {
        "ok": True,
        "account_id": result.get("account_id"),
        "uid": got.get("userid"),
        "phone": got.get("phone") or phone,
        "has_password": bool(password),
        "session_days_left": 14.0,
        "renewable": True,
        "note": "已注册并设置密码：之后由保活任务在剩余 <3 天时自动重登续期",
    }


@router.post("/accounts/sms-login")
def sms_login(body: SmsLoginIn, _: bool = Depends(require_admin),
              db: Session = Depends(get_db)):
    """短信验证码登录并导入/更新号池（**给能收码、但没设密码的号用**）。

    每 14 天做一次即可：`POST /register/sms` 拿码 → 本接口。
    能设密码的号优先走 `/register`（一次搞定，之后永久自动续期）。
    """
    from admin.platform.loomy.core import xfyun

    phone = str(body.phone or "").strip()
    code = str(body.code or "").strip()
    msgid = str(body.msgid or "").strip()
    if not phone or not code or not msgid:
        raise HTTPException(400, detail="手机号、验证码、msgid 都要填（msgid 来自发码接口）")

    devid = xfyun.new_device_id()
    try:
        got = xfyun.login_by_sms(phone, code, msgid, devid=devid)
    except xfyun.XfyunError as exc:
        raise HTTPException(400, detail="%s（%s）" % (
            exc.message, exc.code or "?")) from exc

    record = {
        "realm": "cn",
        "token": got["session"],
        "uid": got.get("userid") or "",
        "phone": got.get("phone") or phone,
        "name": "Loomy %s" % (phone[-4:] or "account"),
        "updatedAt": int(__import__("time").time() * 1000),
        "devid": devid,
        # 没有密码 —— 下次到期还得再收一次码
        "loginid": phone,
    }
    try:
        result = loomy_accounts.import_record(db, record)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, detail="登录成功但导入失败：%s" % exc) from exc

    return {
        "ok": True,
        "account_id": result.get("account_id"),
        "uid": got.get("userid"),
        "phone": got.get("phone") or phone,
        "renewable": False,
        "session_days_left": 14.0,
        "note": "已用短信登录换到新 session（14 天）。这个号没密码，"
                "下次到期还得再收一次码；建议改用 /api/loomy/register 注册时设密码。",
    }


@router.post("/accounts/{acc_id}/password")
def save_password(acc_id: int, body: CredentialIn,
                  _: bool = Depends(require_admin),
                  db: Session = Depends(get_db)):
    """存手机号 + 密码，**开启该号的自动续期**。

    Loomy 服务端没有 refresh token（`/session/refresh` 实测 404），
    唯一的「续期」就是用密码重新登录换一张新 14 天的票。
    存了密码的号会在剩余不足 3 天时由保活任务自动重登 —— 号池可以无人值守。

    ⚠️ 密码明文存在 `auth_json`（和同类工具一致）。导出账号备份时会一起带出，
    请按凭据对待。
    """
    import json

    from admin.models import Account

    account = db.get(Account, acc_id)
    if account is None or (account.platform or "").lower() != "loomy":
        raise HTTPException(404, detail="账号不存在")
    loginid = str(body.loginid or "").strip()
    password = str(body.password or "")
    if not loginid or not password:
        raise HTTPException(400, detail="手机号与密码都要填")
    data = loomy_accounts.set_password(account, loginid, password)
    account.auth_json = json.dumps(data, ensure_ascii=False)
    db.commit()
    # 立刻试一次：密码对不对当场就知道，不用等到期
    result = {"ok": True, "saved": True}
    if str(body.password) != "":
        try:
            result["renew"] = loomy_accounts.renew_with_password(db, account)
        except Exception as exc:  # noqa: BLE001 - 存下来本身就是成功
            result["ok"] = False
            result["renew_error"] = ("%s: %s" % (
                type(exc).__name__, getattr(exc, "message", exc)))[:200]
    db.refresh(account)
    result["session_days_left"] = loomy_accounts.session_days_left(
        loomy_accounts.credentials_of(account))
    return result


@router.post("/accounts/{acc_id}/renew")
def renew(acc_id: int, _: bool = Depends(require_admin),
          db: Session = Depends(get_db)):
    """立刻用存好的密码换一张新 session（手动触发用）。"""
    from admin.models import Account

    account = db.get(Account, acc_id)
    if account is None or (account.platform or "").lower() != "loomy":
        raise HTTPException(404, detail="账号不存在")
    try:
        return loomy_accounts.renew_with_password(db, account)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, detail=("%s: %s" % (
            type(exc).__name__, getattr(exc, "message", exc)))[:300]) from exc


@router.post("/renew-batch")
def renew_batch(_: bool = Depends(require_admin),
                db: Session = Depends(get_db)):
    """批量续期：把所有**存了密码**的号跑一遍（并发，单号失败不影响其它）。

    只处理 `needs_renewal()` 为真的号（剩余 <3 天或已过期），
    没存的号会被明确列出来（它们到期只能重新扫描导入）。
    """
    from admin.models import Account
    from admin.platform import shared as platform_shared

    accounts = [int(row[0]) for row in
                (db.query(Account.id)
                 .filter(Account.platform == "loomy",
                         Account.status == "active")
                 .order_by(Account.id.asc()).all())]
    if not accounts:
        return {"ok": True, "total": 0, "renewed": 0, "skipped": 0,
                "failed": [], "no_password": []}

    counters = {"renewed": 0, "skipped": 0}
    failed: list[dict] = []
    no_password: list[dict] = []

    def worker(session, account):
        data = loomy_accounts.credentials_of(account)
        if not loomy_accounts.has_password(data):
            return {"account_id": account.id, "account": account.name,
                    "ok": True, "skipped": True, "reason": "没有保存密码"}
        if not loomy_accounts.needs_renewal(data):
            return {"account_id": account.id, "account": account.name,
                    "ok": True, "skipped": True, "reason": "还没到续期时间",
                    "days_left": loomy_accounts.session_days_left(data)}
        try:
            result = loomy_accounts.renew_with_password(session, account)
            result.update({"account_id": account.id, "account": account.name,
                           "ok": True})
            return result
        except Exception as exc:  # noqa: BLE001 - 单号失败不影响整批
            return {"account_id": account.id, "account": account.name,
                    "ok": False,
                    "error": ("%s: %s" % (type(exc).__name__,
                                          getattr(exc, "message", exc)))[:200]}

    rows = platform_shared.run_account_batch(
        accounts, worker, label="loomy-renew")

    for row in rows:
        if not row.get("ok"):
            failed.append(row)
        elif row.get("skipped"):
            counters["skipped"] += 1
            if "密码" in str(row.get("reason") or ""):
                no_password.append({"account_id": row.get("account_id"),
                                    "account": row.get("account")})
        else:
            counters["renewed"] += 1

    summary = ("续期完成：续期 %d 个，跳过 %d 个，失败 %d 个"
               % (counters["renewed"], counters["skipped"], len(failed)))
    if no_password:
        summary += "；另有 %d 个号没存密码（到期只能重扫）" % len(no_password)
    return {"ok": not failed, "total": len(rows), **counters,
            "failed": failed, "no_password": no_password,
            "summary": summary}


class InviteCodeIn(BaseModel):
    code: str = ""


@router.post("/invite/mutual")
def invite_mutual(realm: str = "", max_redeems: int = 60, max_seconds: int = 300,
                  max_inviters: int = 20, dry_run: int = 0,
                  _: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """**账号池互邀**：轮换使用各号自己的邀请码，去给还没绑过的号兑换。

    官方的 20 人上限是**按邀请人**算的（`maxUses=20`，满了就是 exhausted），
    所以想让整池都吃到邀请奖励，只能换邀请人 —— 这就是这个接口做的事。

    六条终止条件（参见 `services/invite.mutual_invite` 文档）保证不会死循环；
    `dry_run=1` 可先看计划。
    """
    from admin.platform.loomy.services import invite as invite_svc

    return invite_svc.mutual_invite(
        db, _realm_or_400(realm), max_redeems=max_redeems,
        max_seconds=max_seconds, max_inviters=max_inviters,
        dry_run=bool(dry_run))


@router.get("/invite/codes")
def invite_codes(_: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """列出号池里每个号**自己**的邀请码与剩余次数（互邀前先看可用量）。"""
    from admin.platform.loomy.services import invite as invite_svc

    rows = [a for a in loomy_accounts.accounts_for(db, "")
            if (getattr(a, "status", "") or "") == "active"]
    items = []
    total_left = 0
    for account in rows:
        got = invite_svc.invitation_codes_of(account)
        for entry in got.get("codes") or []:
            total_left += int(entry.get("left") or 0)
        items.append({"account_id": account.id,
                      "account": account.name or str(account.id),
                      "ok": bool(got.get("ok")),
                      "reason": got.get("reason") or "",
                      "codes": got.get("codes") or []})
    return {"accounts": len(rows), "remaining_slots": total_left,
            "items": items[:120],
            "summary": "可用邀请名额合计 %d 个（每号最多 20 人）" % total_left}


@router.get("/invite")
def invite_status(realm: str = "", _: bool = Depends(require_admin),
                  db: Session = Depends(get_db)):
    """邀请码总览：默认邀请码 + 每个号当前的激活状态（读本地快照）。"""
    from admin.platform.loomy.services import invite as invite_svc

    rows = [a for a in loomy_accounts.accounts_for(db, _realm_or_400(realm))
            if (getattr(a, "status", "") or "") == "active"]
    states = []
    activated = pending = unknown = 0
    codes: dict[str, int] = {}
    for account in rows:
        state = loomy_accounts._invite_state(account)  # noqa: SLF001 - 同包
        if not state.get("known"):
            unknown += 1
        elif state.get("activated"):
            activated += 1
            code = state.get("code") or ""
            if code:
                codes[code] = codes.get(code, 0) + 1
        else:
            pending += 1
        states.append({"account_id": account.id,
                       "account": account.name or str(account.id), **state})
    return {
        "supported": True,
        "default_code": invite_svc.read_default_code(db),
        "inviter_limit": invite_svc.INVITER_LIMIT,
        "accounts": len(rows),
        "activated": activated,
        "pending": pending,
        "unknown": unknown,
        "codes": codes,
        "items": states[:200],
        "summary": ("已激活 %d 个 · 未激活 %d 个 · 未同步 %d 个（邀请人上限 %d）"
                    % (activated, pending, unknown, invite_svc.INVITER_LIMIT)),
    }


@router.put("/invite")
def invite_set_default(body: InviteCodeIn, _: bool = Depends(require_admin),
                       db: Session = Depends(get_db)):
    """保存**默认邀请码**：之后入库的新号会自动绑它。"""
    from admin.platform.loomy.services import invite as invite_svc

    saved = invite_svc.write_default_code(db, body.code)
    return {"ok": True, "default_code": saved,
            "note": "已保存：之后新入库的 Loomy 账号会自动绑定这个邀请码"}


@router.post("/invite/sync")
def invite_sync(realm: str = "", limit: int | None = None,
                _: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """同步整个号池的邀请状态（逐号查上游，写进快照供列表展示）。"""
    from admin.platform.loomy.services import invite as invite_svc

    return invite_svc.sync_all(db, _realm_or_400(realm), limit=limit)


@router.post("/invite/bind")
def invite_bind(realm: str = "", code: str = "", limit: int | None = None,
                _: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """把邀请码批量绑到所有启用账号（`code` 为空则用默认邀请码）。

    ⚠️ 上游两条硬规则（实测）：一个号只能兑换一次邀请码；**邀请人上限 20**。
    超限时绑定请求照样回成功，但 `activated` 不变 —— 本接口会回读状态，
    只有真生效才算成功。
    """
    from admin.platform.loomy.services import invite as invite_svc

    return invite_svc.bind_all(db, code, _realm_or_400(realm), limit=limit)


@router.post("/accounts/{acc_id}/invite")
def invite_bind_one(acc_id: int, body: InviteCodeIn,
                    _: bool = Depends(require_admin),
                    db: Session = Depends(get_db)):
    """单账号绑定邀请码（账号行内操作）。"""
    from admin.platform.loomy.services import invite as invite_svc

    account = db.query(loomy_accounts.Account).filter_by(id=acc_id).first()
    if account is None or str(account.platform or "") != loomy_accounts.PLATFORM:
        raise HTTPException(404, detail="Loomy 账号不存在")
    code = str(body.code or "").strip() or invite_svc.read_default_code(db)
    if not code:
        raise HTTPException(400, detail="请填写邀请码，或先在平台上设置默认邀请码")
    return invite_svc.bind_one(db, account, code)


@router.post("/redeem")
def redeem_batch(code: str = "", realm: str = "", limit: int | None = None,
                 _: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """批量兑换码换积分（每个启用账号各兑一次同一个码）。"""
    from admin.platform.loomy.services import invite as invite_svc

    return invite_svc.redeem_all(db, code, _realm_or_400(realm), limit=limit)


@router.post("/accounts/{acc_id}/redeem")
def redeem_one(acc_id: int, body: InviteCodeIn,
               _: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """单账号兑换码换积分（账号行内操作）。"""
    from admin.platform.loomy.services import invite as invite_svc

    account = db.query(loomy_accounts.Account).filter_by(id=acc_id).first()
    if account is None or str(account.platform or "") != loomy_accounts.PLATFORM:
        raise HTTPException(404, detail="Loomy 账号不存在")
    if not str(body.code or "").strip():
        raise HTTPException(400, detail="请填写兑换码")
    return invite_svc.redeem_one(db, account, body.code)


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


@router.get("/onboarding")
def onboarding_status(realm: str = "", _: bool = Depends(require_admin),
                      db: Session = Depends(get_db)):
    """新手任务进度（8 个一次性任务，共 10000 积分）。

    读的是**服务端**状态，不是客户端本地 json —— 客户端默认 mock 后端，
    界面上"已获得 N 积分"完全可能服务端一分都没有。
    """
    from admin.platform.loomy.services import onboarding as onboarding_svc

    rows = onboarding_svc.status_of_each(db, _realm_or_400(realm))
    return {
        "supported": True,
        "total_points": onboarding_svc.total_points(),
        # 中文任务清单（按客户端三段分组）：key 只用于提交，展示用 label
        "catalog": onboarding_svc.task_catalog(),
        "tasks": [{"key": k, "label": onboarding_svc.label_of(k), "points": v}
                  for k, v in onboarding_svc.point_table().items()],
        "accounts": rows,
        "summary": "共 %d 个账号，仍有 %d 积分未领取"
                   % (len(rows), sum(max(0, r.get("missing_points") or 0)
                                     for r in rows)),
    }


@router.post("/onboarding")
def onboarding_run(realm: str = "", dry_run: int = 0,
                   limit: int | None = None,
                   _: bool = Depends(require_admin),
                   db: Session = Depends(get_db)):
    """把所有启用账号的新手任务补齐（幂等，可反复点）。

    `dry_run=1` 只统计不领取 —— 用来先看「还有多少分没赚」。
    一次跑 8 个 key × N 个号，串行 + 抖动，几十个号需要一两分钟。
    """
    from admin.platform.loomy.services import onboarding as onboarding_svc

    return onboarding_svc.run_all(db, _realm_or_400(realm),
                                  limit=limit, dry_run=bool(dry_run))
