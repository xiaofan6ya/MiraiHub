"""MiniMax 公开注册入口（设备授权码，RFC 8628，免客户端）。

协议包装：`core.client.device_begin / device_poll_token`（控制台「授权登录」
同源，PKCE S256）。公开端形态：

* `begin` 返回 `auth_url`（`verification_uri_complete`）—— 用户**手机直接打开**
  即可授权，人机验证全在官方页面完成，不需要客户端；
* `poll` 轮询 `/oauth2/token`：pending 时让前端继续，成功时 token 直接入库
  （`import_record` 幂等 + 自动补刷积分）；
* 入库收尾：显示名去毒 + 邀请码消耗 + 额度刷新。
"""
from __future__ import annotations

import logging

from admin.platform.minimax.core import client as minimax_client
from admin.platform.minimax.services import accounts as minimax_accounts
from admin.security import clean_display_name

logger = logging.getLogger("admin.public_auth.minimax")

spec = {"platform": "minimax", "label": "MiniMax", "modes": ["auth_url"],
        "order": 50}


def begin(mode: str, db, ip: str) -> dict:
    result = minimax_client.device_begin("cn")
    from admin import public_auth

    sid = public_auth.register_session("minimax", "device", ip, {
        "device_code": str(result.get("device_code") or ""),
        "code_verifier": str(result.get("code_verifier") or ""),
        "realm": str(result.get("realm") or "cn"),
    })
    return {"session": sid,
            "auth_url": str(result.get("verification_uri_complete")
                            or result.get("verification_uri") or ""),
            "user_code": str(result.get("user_code") or ""),
            "expires_in": int(result.get("expires_in") or 300)}


def poll(session_id: str, db, info: dict) -> dict:
    data = info.get("data") or {}
    result = minimax_client.device_poll_token(
        str(data.get("realm") or "cn"),
        str(data.get("device_code") or ""),
        str(data.get("code_verifier") or ""))
    if result.get("status") in ("pending", "slow_down"):
        return {"status": "pending"}
    record = {"token": result.get("access_token") or "",
              "refresh_token": result.get("refresh_token") or "",
              "expires_at_ms": result.get("expires_at_ms") or 0,
              "realm": str(data.get("realm") or "cn"),
              "source": "public_entry"}
    account, created = minimax_accounts.import_record(db, record)
    _sanitize_and_consume(db, account, info)
    return {"status": "done",
            "account": {"name": _mask(account.name), "created": created}}


def cancel(session_id: str, info: dict) -> None:
    # 设备授权码有上游 TTL（默认 300s），无需显式撤销
    return None


def _sanitize_and_consume(db, account, info: dict) -> None:
    try:
        safe = clean_display_name(account.name, "") or \
            clean_display_name(account.uid, "") or "MiniMax 账号"
        if safe != account.name:
            account.name = safe
        db.commit()
    except Exception:  # noqa: BLE001
        logger.debug("MiniMax 公开入口账号名去毒失败", exc_info=True)
    try:
        from admin import public_auth

        public_auth.refresh_account_quota(db, "minimax", account)
    except Exception:  # noqa: BLE001
        pass
    try:
        from admin import public_auth

    except Exception:  # noqa: BLE001
        pass


def _mask(name: str) -> str:
    s = (name or "").strip()
    if not s:
        return ""
    if len(s) <= 4:
        return s[:1] + "**"
    return s[:2] + "***" + s[-2:]
