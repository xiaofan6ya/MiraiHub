"""WorkBuddy 公开注册入口（短信 + 官方页面接力，免客户端）。

**这是统一公开入口（/api/open/*）的 WorkBuddy 适配器，不是第二套实现**：
登录会话、凭证校验、入库（`_verify_and_store`）、短信节流（`_send_guarded`
的按号 60s / 按 IP 10每小时）、官方页面接力（`_official_poll_core`）全部
复用 `admin.routers.login` 里的既有机制 —— 两条入口对「什么算合法凭证」
的判定永远一致，不会出现「公开端宽松一点」的后门。

本适配器只做三件事：
1. 把 wb_login 自己的会话 sid 包进统一会话（绑 IP / TTL / 并发上限）；
2. 把结果整形为统一协议（masked 账号名，绝不回显 uid / 账号 id）；
3. 入库成功后消耗邀请码（official 轮询核心内部已消耗，不重复）。
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

logger = logging.getLogger("admin.public_auth.workbuddy")

spec = {"platform": "workbuddy", "label": "WorkBuddy",
        "modes": ["sms", "official"], "order": 10}


def _login_mod():
    from admin.routers import login as mod

    return mod


def _wb():
    from admin.routers.login import wb_login

    return wb_login


def _inner(info: dict) -> str:
    return str((info.get("data") or {}).get("inner") or "")


def begin(mode: str, db, ip: str) -> dict:
    if mode == "official":
        sess = _wb().start_official_login()
        payload = {"auth_url": str(sess.plugin_auth_url or "")}
        expires = 360
    else:
        sess = _wb().start_login()
        payload = {}
        expires = 600
    from admin import public_auth

    sid = public_auth.register_session("workbuddy", mode, ip, {
        "inner": sess.sid,
    })
    return {"session": sid, "expires_in": expires, **payload}


def sms_send(mobile: str, country: str, db, ip: str) -> dict:
    mod = _login_mod()
    # 每次发送都起新的 wb 会话（与旧流程「start → send」等价，合并为一次调用）
    sess = _wb().start_login()
    body = SimpleNamespace(sid=sess.sid, mobile=mobile,
                           country_code=country or "86")
    result = mod._send_guarded(body, ip)
    from admin import public_auth

    sid = public_auth.register_session("workbuddy", "sms", ip, {
        "inner": sess.sid,
        "mobile": mobile, "country": country or "86",
    })
    return {"session": sid,
            "expires_in": int(result.get("expires_in") or 60),
            "retry_after": int(result.get("retry_after") or 60)}


def sms_verify(session_id: str, info: dict, code: str, password: str,
               db, ip: str) -> dict:
    mod = _login_mod()
    try:
        sess = _wb().get_session(_inner(info))
    except _wb().WbLoginError as e:  # noqa: UP038 - 兼容旧式异常对象
        raise mod._fail(e, 502)
    with mod._session_slot(sess, "verify"), mod._upstream_slot("verify"):
        out = mod._verify_and_store(sess, code, db)
    # 公开入口入库收尾：确保启用状态（额度由既有定时任务同步）
    try:
        from admin.models import Account

        acc = db.query(Account).filter(
            Account.id == int(out.get("account_id") or 0)).first()
        if acc is not None and acc.status != "active":
            acc.status = "active"
            db.commit()
    except Exception:  # noqa: BLE001
        pass
    return {"status": "done",
            "account": {"name": mod._mask_name(
                (out["account"] or {}).get("name") or out.get("uid") or "")}}


def poll(session_id: str, db, info: dict) -> dict:
    if info.get("kind") != "official":
        # 短信模式没有后台轮询：verify 同步返回
        return {"status": "pending"}
    mod = _login_mod()
    result = mod._official_poll_core(
        db, _inner(info), "")
    if not result.get("done"):
        return {"status": "pending"}
    # 官方接力核心内部已消耗邀请码；这里只整形
    return {"status": "done",
            "account": {"name": str(result.get("masked") or "")}}


def cancel(session_id: str, info: dict) -> None:
    # wb_login 没有 cancel 语义：会话自带 expired()，到期自然失效。
    # 这里只需要丢掉统一侧的包装会话（聚合层 cancel 已做）。
    return None


def _mask_mobile(phone: str) -> str:  # pragma: no cover - 预留
    p = (phone or "").strip()
    return p[:3] + "****" + p[-4:] if len(p) >= 7 else "***"
