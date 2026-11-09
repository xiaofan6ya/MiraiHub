"""Loomy 手机号授权登录（验证码 / 密码）→ 凭证入库。

## 与 WorkBuddy 的关系

用户的原话是「直接跟 workbuddy 一样发送手机号验证码登录授权之后 auth 凭证入库」。
`admin/routers/login.py` 那套是 WorkBuddy 专用的（发码走 WorkBuddy 的短信网关、
换票走它自己的 OAuth），不能复用；这里按 Loomy 自己的账号服务重做一份，
**交互形态保持一致**：发码 → 填码 → 入库，前端一个弹窗搞定。

## Loomy 账号服务的三条登录路（`core/xfyun.py`，全部实测过）

    POST /login/phone/sendMsgCode   {ccode, phone, expire}      → {msgid}
    POST /login/phone/checkCode     {ccode, phone, mcode, msgid, expire} → {session, userid}
    POST /register/phone/submit     {phone, code, password?}    → {session, userid}
    POST /login/account/byPwd       {loginid, password(rsa), rcode, type, expire}

## 为什么要「先 checkCode，失败再 register」

同一个手机号可能是**已注册的老号**，也可能是**新号**：

- 老号：`checkCode` 直接给 session，注册接口会报「已注册」；
- 新号：`checkCode` 报验证码/账号不存在，`register/phone/submit` 才建号。

前端不该让用户自己判断「我是老号还是新号」，所以这里自动走：先登录，失败
再注册。注册时**必须带密码**——账号服务没有「给已有号补设密码」的接口，
注册那一刻是唯一能设密码的机会，而有了密码才能 14 天到期后自动续期。

## devid

每个新登录的号分配独立 `devid`（`web-<rand>`）。同一个 devid 登录多个账号
是最明显的「同一设备多开」特征；已导入的号沿用原 devid，改动反而像异常。
"""
from __future__ import annotations

import logging
import time

from sqlalchemy.orm import Session

from admin.platform.loomy.core import xfyun
from admin.platform.loomy.services import accounts as loomy_accounts

logger = logging.getLogger("admin.platform.loomy.auth")

#: 发码时申请的有效期（秒）——与客户端一致
CODE_TTL = 300

#: 判「这个号还没注册」的提示词。上游文案不固定，按关键字兜住；
#: 命中就走注册分支，否则把原始错误抛给用户（别瞎注册）。
_NEW_ACCOUNT_HINTS = ("未注册", "不存在", "没有找到", "未找到", "not exist",
                      "not found", "unregistered", "no such")


def _looks_like_new_account(message: str) -> bool:
    text = str(message or "").lower()
    return any(word.lower() in text for word in _NEW_ACCOUNT_HINTS)


def send_code(phone: str) -> dict:
    """下发登录/注册验证码。返回 `{msgid, expire}`（`msgid` 下一步要回传）。"""
    phone = str(phone or "").strip()
    if not phone:
        raise ValueError("手机号不能为空")
    if not phone.lstrip("+").isdigit() or len(phone.lstrip("+")) < 6:
        raise ValueError("手机号格式不对（只填数字，不含空格与横线）")
    data = xfyun.send_sms_code(phone)
    msgid = str(data.get("msgid") or "")
    if not msgid:
        raise ValueError("验证码已发但上游没返回 msgid，无法继续（请重试）")
    return {"msgid": msgid, "expire": int(data.get("expire") or CODE_TTL),
            "phone": phone}


def _post_login_automation(account_id: int) -> None:
    """入库后台动作：签到 + 新手任务（一次性 10000 分）。

    ## 为什么授权后要自动跑

    用户的原话：「新增进来的账号没自动完成任务签到之类的」。
    新号如果只入库、不领，那 10000 分新手奖励和当天 5000 签到额度就白放着
    —— 而这两件事都是**幂等**的（`first-login` 返回 alreadyProcessed、
    `onboarding/complete` 返回 alreadyCompleted），重复跑没有副作用，
    所以放手让它在后台自动做。

    放在**独立线程 + 独立 Session**里：登录请求要秒回，不能让用户等
    8 个任务 key 的领取（约 5 秒）；失败也不影响「已入库」这个结果。
    """
    import threading

    def worker() -> None:
        db = None
        try:
            from admin.db import SessionLocal
            from admin.models import Account
            from admin.platform.loomy.services import checkin, onboarding

            db = SessionLocal()
            account = db.query(Account).filter(Account.id == account_id).first()
            if account is None:
                return
            try:
                checkin.run_daily_checkin(db, None, account.domain or "cn")
            except Exception:  # noqa: BLE001 - 签到失败不能影响任务领取
                logger.debug("Loomy 入库后签到失败 acc=%s", account_id, exc_info=True)
            try:
                account = db.query(Account).filter(Account.id == account_id).first()
                if account is not None:
                    onboarding.run_all(db, account.domain or "cn", limit=0)
            except Exception:  # noqa: BLE001
                logger.debug("Loomy 入库后新手任务失败 acc=%s", account_id, exc_info=True)
        except Exception:  # noqa: BLE001 - 后台线程异常只记日志
            logger.debug("Loomy 入库后自动化失败 acc=%s", account_id, exc_info=True)
        finally:
            try:
                if db is not None:
                    db.close()
            except Exception:  # noqa: BLE001
                pass

    threading.Thread(target=worker, name="loomy-post-login-%s" % account_id,
                     daemon=True).start()


def verify(db: Session, *, phone: str, code: str, msgid: str,
           password: str = "", name: str = "") -> dict:
    """验证码登录 →（必要时）注册 → 凭证入库。

    `password` 留空时只登录；**新号必须先给密码**（否则 14 天后无法续期），
    这种情况会返回 `need_password=True` 让前端补一次输入，而不是默默建个死号。
    """
    phone = str(phone or "").strip()
    code = str(code or "").strip()
    msgid = str(msgid or "").strip()
    password = str(password or "")
    if not phone or not code:
        raise ValueError("手机号与验证码都要填")
    if not msgid:
        raise ValueError("缺少 msgid：请先点「发送验证码」")

    devid = xfyun.new_device_id()
    got: dict = {}
    registered = False
    sms_error: str = ""

    try:
        got = xfyun.login_by_sms(phone, code, msgid, devid=devid)
    except xfyun.XfyunError as exc:
        sms_error = exc.message
        if not _looks_like_new_account(exc.message):
            # 不是「未注册」，就是真的错了（验证码错/过期/风控），如实抛出
            raise ValueError("%s（%s）" % (exc.message, exc.code or "?")) from exc
        if not password:
            # 新号 + 没密码：不能建号（建了也续不了）。让前端补密码。
            return {"ok": False, "need_password": True, "need_register": True,
                    "reason": "该手机号还没有 Loomy 账号，注册需要先设一个密码"
                              "（否则 14 天后无法自动续期）"}
        try:
            got = xfyun.register_by_phone(phone, code, password, devid=devid)
            registered = True
        except xfyun.XfyunError as reg_exc:
            raise ValueError("注册失败：%s（%s）" % (
                reg_exc.message, reg_exc.code or "?")) from reg_exc
    if not got.get("session"):
        raise ValueError("上游未返回 session（%s）" % (sms_error or "未知原因"))

    record = {
        "realm": "cn",
        "token": got["session"],
        "uid": got.get("userid") or "",
        "phone": got.get("phone") or phone,
        "name": name or ("Loomy %s" % (phone[-4:] if len(phone) >= 4 else phone)),
        "updatedAt": int(time.time() * 1000),
        # 独立设备标识：续期时复用，避免「同一账号换设备」
        "devid": devid,
        "loginid": phone,
    }
    if password:
        record["password"] = password
    result = loomy_accounts.import_record(db, record)
    account_id = result.get("account_id")
    logger.info("Loomy 手机号授权成功 acc=%s phone=%s registered=%s",
                account_id, phone[-4:], registered)
    # 入库后自动绑默认邀请码（配了才绑，失败不影响入库）
    if account_id:
        from admin.platform.loomy.services import invite as invite_svc

        invite_svc.auto_bind_on_import(
            db, db.query(loomy_accounts.Account).filter_by(id=account_id).first())
    # 入库后自动签到 + 领新手任务（幂等，后台线程，不拖慢登录）
    if account_id:
        _post_login_automation(int(account_id))

    return {
        "ok": True,
        "account_id": account_id,
        # `created` 让前端能说清「新增了一条」还是「更新了已有的同一手机号」——
        # 用户报过「我的账号入到哪里去了，账号池没看见」：其实是同一 uid 的
        # 老号被就地更新了，界面没有任何提示。
        "created": bool(result.get("created")),
        "uid": got.get("userid") or "",
        "phone": got.get("phone") or phone,
        "registered": registered,
        # 有密码才谈得上「自动续期」；没密码就是 14 天的临时票
        "renewable": bool(password),
        "session_days_left": 14.0,
        "probe": result.get("probe") or {},
    }


def password_login(db: Session, *, phone: str, password: str,
                   name: str = "") -> dict:
    """手机号 + 密码登录（老号续期 / 换机重登用，不需要短信）。"""
    phone = str(phone or "").strip()
    password = str(password or "")
    if not phone or not password:
        raise ValueError("手机号与密码都要填")
    devid = xfyun.new_device_id()
    try:
        got = xfyun.login_by_password(phone, password, devid=devid)
    except xfyun.XfyunError as exc:
        raise ValueError("%s（%s）" % (exc.message, exc.code or "?")) from exc
    record = {
        "realm": "cn",
        "token": got["session"],
        "uid": got.get("userid") or "",
        "phone": got.get("phone") or phone,
        "name": name or ("Loomy %s" % (phone[-4:] if len(phone) >= 4 else phone)),
        "updatedAt": int(time.time() * 1000),
        "devid": devid,
        "loginid": phone,
        "password": password,
    }
    result = loomy_accounts.import_record(db, record)
    account_id = result.get("account_id")
    if account_id:
        from admin.platform.loomy.services import invite as invite_svc

        invite_svc.auto_bind_on_import(
            db, db.query(loomy_accounts.Account).filter_by(id=account_id).first())
        _post_login_automation(int(account_id))
    return {"ok": True, "account_id": account_id,
            "created": bool(result.get("created")),
            "uid": got.get("userid") or "", "phone": phone,
            "renewable": True, "session_days_left": 14.0}


def save_password(db: Session, account, password: str, loginid: str = "") -> dict:
    """给已入库的号补密码（用户手上有密码、但当初是扫码/短信导入的）。"""
    password = str(password or "")
    if not password:
        raise ValueError("密码不能为空")
    data = loomy_accounts.credentials_of(account)
    loginid = str(loginid or data.get("loginid") or data.get("phone") or "").strip()
    if not loginid:
        raise ValueError("这个号没记录手机号，请先填写登录手机号")
    loomy_accounts.set_password(account, loginid, password)
    db.commit()
    return {"ok": True, "account_id": account.id, "renewable": True}