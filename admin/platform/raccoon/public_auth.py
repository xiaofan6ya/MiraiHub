"""小浣熊公开注册入口（小程序扫码授权，免客户端免滑块免短信）。

协议包装：`services.webauth`（IAM 小程序扫码，手机端处理全部人机验证）。
二维码内容是官方登录页 URL —— 这里用服务端的 `qr_svg` 生成 SVG 并转
data URI 交给前端 `<img>`，前端**不接触任何上游原始字段**。
"""
from __future__ import annotations

import base64
import logging

from admin.platform.raccoon.services import accounts as raccoon_accounts
from admin.platform.raccoon.services import webauth
from admin.security import clean_display_name

logger = logging.getLogger("admin.public_auth.raccoon")

spec = {"platform": "raccoon", "label": "小浣熊", "modes": ["qr"], "order": 40}


def begin(mode: str, db, ip: str) -> dict:
    result = webauth.begin()
    from admin import public_auth

    sid = public_auth.register_session("raccoon", "qr", ip, {
        "inner": result.get("auth_id") or "",
    })
    svg = webauth.qr_svg(result.get("qr_value") or "")
    return {"session": sid,
            "qr_svg_data_uri": "data:image/svg+xml;base64," + base64.b64encode(
                svg.encode("utf-8")).decode("ascii"),
            "expires_in": int(result.get("expires_in") or 300),
            "poll_interval": max(2, int(result.get("poll_interval") or 2))}


def poll(session_id: str, db, info: dict) -> dict:
    result = webauth.poll(db, str(info.get("data", {}).get("inner") or ""))
    if result.get("confirm_required"):
        return {"status": "failed",
                "message": "扫码成功但上游会话不可用，请改用其它平台或联系管理员"}
    if result.get("done"):
        _sanitize_and_consume(db, result.get("account"), info)
        return {"status": "done", "account": _masked_view(result.get("account"))}
    if str(result.get("status")) == "failed":
        return {"status": "failed",
                "message": "授权未完成，请重新开始"}
    if str(result.get("status")) == "expired":
        return {"status": "expired"}
    return {"status": "pending"}


def cancel(session_id: str, info: dict) -> None:
    try:
        webauth.cancel(str(info.get("data", {}).get("inner") or ""))
    except Exception:  # noqa: BLE001
        pass


def _sanitize_and_consume(db, account_view, info: dict) -> None:
    acc = None
    try:
        uid = str((account_view or {}).get("uid") or "")
        if uid:
            acc = db.query(raccoon_accounts.Account).filter(
                raccoon_accounts.Account.platform == "raccoon",
                raccoon_accounts.Account.uid == uid).first()
            if acc is not None:
                safe = clean_display_name(acc.name, "") or \
                    clean_display_name(acc.uid, "") or "小浣熊账号"
                if safe != acc.name:
                    acc.name = safe
                db.commit()
    except Exception:  # noqa: BLE001
        logger.debug("小浣熊公开入口账号名去毒失败", exc_info=True)
    try:
        from admin import public_auth

    except Exception:  # noqa: BLE001
        pass
    if acc is not None:
        # 入库收尾：确保启用 + 立即刷新额度（否则选号永远挑不到这个号）
        public_auth.refresh_account_quota(db, "raccoon", acc)
        pass


def _masked_view(account_view) -> dict:
    view = dict(account_view or {})
    view["name"] = _mask(view.get("name") or "")
    view.pop("uid", None)
    return view


def _mask(name: str) -> str:
    s = (name or "").strip()
    if not s:
        return ""
    if len(s) <= 4:
        return s[:1] + "**"
    return s[:2] + "***" + s[-2:]
