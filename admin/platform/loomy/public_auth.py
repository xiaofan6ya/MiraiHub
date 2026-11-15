"""Loomy 公开注册入口（短信验证码，免客户端）。

协议包装：`services.auth.send_code / verify`（讯飞 passport，控制台
「授权登录」用的是同一套），这里只做**公开端整形**：

* 会话由 `admin.public_auth` 统一登记（sid 绑 IP、TTL、并发上限）；
* `msgid` 只存服务端会话里，客户端回传 sid 即可，不能拿它验证别的手机号；
* 新号需补密码（`need_password`）原样透传，前端补一次输入再 verify；
* 入库成功 → 显示名去毒（`clean_display_name`）→ 打码回显 → 消耗邀请码。
"""
from __future__ import annotations

import logging

from admin.platform.loomy.services import auth as loomy_auth
from admin.platform.loomy.services import accounts as loomy_accounts
from admin.security import clean_display_name

logger = logging.getLogger("admin.public_auth.loomy")

spec = {"platform": "loomy", "label": "Loomy", "modes": ["sms"], "order": 20}


def sms_send(mobile: str, country: str, db, ip: str) -> dict:
    result = loomy_auth.send_code(mobile)
    from admin import public_auth

    sid = public_auth.register_session("loomy", "sms", ip, {
        "phone": result["phone"], "msgid": result["msgid"],
    })
    return {"session": sid, "expires_in": int(result.get("expire") or 300),
            "masked_mobile": _mask_mobile(result["phone"])}


def sms_verify(session_id: str, info: dict, code: str, password: str,
               db, ip: str) -> dict:
    data = info.get("data") or {}
    result = loomy_auth.verify(
        db, phone=str(data.get("phone") or ""), code=code,
        msgid=str(data.get("msgid") or ""), password=password)
    if result.get("need_password"):
        return {"status": "need_password",
                "need_register": bool(result.get("need_register"))}
    if not result.get("ok"):
        return {"status": "pending"}
    _sanitize(db, int(result.get("account_id") or 0))
    return {"status": "done",
            "account": {"name": _mask_mobile(str(result.get("phone") or "")),
                        "created": bool(result.get("created"))}}


def poll(session_id: str, db, info: dict) -> dict:
    # 短信模式没有后台轮询状态：verify 是同步的，这里只报会话仍在
    return {"status": "pending"}


def cancel(session_id: str, info: dict) -> None:
    return None


def _sanitize(db, account_id: int) -> None:
    try:
        from admin.models import Account

        acc = db.query(Account).filter(Account.id == account_id).first()
        if acc is None:
            return
        safe = clean_display_name(acc.name, "") or \
            clean_display_name(acc.uid, "") or "Loomy 账号"
        if safe != acc.name:
            acc.name = safe
        db.commit()
        # 入库收尾：确保启用 + 立即刷新额度（否则选号永远挑不到这个号）
        from admin import public_auth

        public_auth.refresh_account_quota(db, "loomy", acc)
    except Exception:  # noqa: BLE001 - 去毒失败不影响入库结果
        logger.debug("Loomy 公开入口账号名去毒失败", exc_info=True)


def _mask_mobile(phone: str) -> str:
    p = (phone or "").strip()
    if len(p) < 7:
        return "***"
    return p[:3] + "****" + p[-4:]
