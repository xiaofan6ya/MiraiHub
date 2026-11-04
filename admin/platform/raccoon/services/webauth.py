"""小浣熊扫码授权（免客户端 / 免滑块 / 免短信）。

## 协议（逆向 web 登录页 `/_next/static/chunks` 实测）

官方登录页的「小程序扫码登录」走 IAM 服务：

    POST https://xiaohuanxiong.com/api/iam/v1/auth/mp/qrcode
         {}  ->  {code:"OK", data:{qrcode_code, scan_path:"/login/mp",
                                    expires_in:300, poll_interval:2}}
    POST /api/iam/v1/auth/mp/qrcode/login  {qrcode_code}
         ->  {status: pending|logging|canceled|success,
              access_token?, token_type?:"bearer"}   # success 时携带
    PUT  /api/iam/v1/auth/mp/qrcode  {qrcode_code, status:"canceled"}

二维码内容（照抄官方前端 `5348` chunk 的构造函数）：

    https://xiaohuanxiong.com/login/mp?code=<qrcode_code>&appname=<brand>&source=platform

用户用微信 / 商汤小浣熊 App 扫码并在手机上确认 —— **滑块与验证码都由
官方体系在手机端处理**，网页端没有任何反爬环节，也不需要客户端。
轮询到 `success` 即拿到 IAM JWT（与客户端 auth.json 同形态），直接入库。

## 会话管理

`begin` 创建会话（内存表 + 过期清理），返回 `auth_id` 给前端；
前端轮询 `poll(auth_id)`；`cancel` 显式取消。扫码成功后**立刻入库**
（复用 services.accounts.import_record 的幂等逻辑），轮询会返回入库结果。
"""
from __future__ import annotations

import logging
import threading
import time
import urllib.error
import urllib.request
from typing import Any

from admin.platform.raccoon.core import endpoints

logger = logging.getLogger("admin.platform.raccoon.webauth")

PLATFORM = "raccoon"
#: 扫码内容里的品牌名（官方登录页 chunk `6428` 的 brand 值）
BRAND = "商汤小浣熊"
IAM_BASE = "https://xiaohuanxiong.com/api/iam/v1"
SITE_ORIGIN = "https://xiaohuanxiong.com"
TIMEOUT_S = 15.0

#: 会话上限（防内存膨胀）；过期的由下一次 begin/poll 顺带清理
_MAX_SESSIONS = 200
_sessions: dict[str, dict] = {}
_lock = threading.Lock()


def _cleanup_locked(now: float) -> None:
    stale = [k for k, v in _sessions.items() if v.get("expires_at", 0) < now - 60]
    for k in stale:
        _sessions.pop(k, None)
    overflow = len(_sessions) - _MAX_SESSIONS
    if overflow > 0:
        for k in sorted(_sessions, key=lambda k: _sessions[k].get("created_at", 0))[:overflow]:
            _sessions.pop(k, None)


def _iam_request(method: str, path: str, body: dict | None = None) -> dict:
    """调官方 IAM 接口（信封 `{code:"OK", data:...}`，失败抛 RuntimeError）。"""
    data = None if body is None else json_dumps(body).encode("utf-8")
    req = urllib.request.Request(IAM_BASE + path, data=data, method=method.upper())
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    req.add_header("Origin", SITE_ORIGIN)
    req.add_header("Referer", SITE_ORIGIN + "/login")
    req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            payload = json_loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:200]
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError("IAM HTTP %s %s" % (exc.code, detail)) from exc
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("IAM 网络错误（%s）" % type(exc).__name__) from exc
    if not isinstance(payload, dict) or payload.get("code") != "OK":
        raise RuntimeError("IAM 业务错误：%s" % (payload.get("message") or payload.get("code")))
    return payload.get("data") or {}


def json_dumps(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)


def json_loads(text: str):
    import json
    return json.loads(text)


def begin() -> dict:
    """创建扫码授权会话。返回前端渲染所需的一切。"""
    data = _iam_request("POST", "/auth/mp/qrcode", {})
    qrcode_code = str(data.get("qrcode_code") or "")
    scan_path = str(data.get("scan_path") or "/login/mp")
    expires_in = int(data.get("expires_in") or 300)
    poll_interval = max(1, int(data.get("poll_interval") or 2))
    if not qrcode_code:
        raise RuntimeError("官方未返回 qrcode_code")
    # 二维码内容照抄官方前端：站点 origin + scan_path + code/appname/source
    qr_value = "%s%s?code=%s&appname=%s&source=platform" % (
        SITE_ORIGIN, scan_path, qrcode_code, urllib_parse_quote(BRAND))
    auth_id = "raccoon-auth-%d-%s" % (int(time.time() * 1000), qrcode_code[-8:])
    now = time.time()
    with _lock:
        _cleanup_locked(now)
        _sessions[auth_id] = {
            "auth_id": auth_id,
            "qrcode_code": qrcode_code,
            "qr_value": qr_value,
            "created_at": now,
            "expires_at": now + expires_in,
            "poll_interval": poll_interval,
            "status": "pending",
            "account": None,
        }
    return {
        "auth_id": auth_id,
        "qr_value": qr_value,
        "expires_in": expires_in,
        "poll_interval": poll_interval,
        "status": "pending",
    }


def poll(db, auth_id: str) -> dict:
    """轮询扫码状态；`success` 时直接入库并返回账号摘要。"""
    with _lock:
        sess = _sessions.get(auth_id)
        if sess is None:
            return {"status": "expired", "done": False}
        if sess["status"] in ("done", "canceled", "expired"):
            return _public(sess)
        if sess["expires_at"] < time.time():
            sess["status"] = "expired"
            return _public(sess)
        qrcode_code = sess["qrcode_code"]
    try:
        data = _iam_request("POST", "/auth/mp/qrcode/login", {"qrcode_code": qrcode_code})
    except RuntimeError as exc:
        logger.warning("扫码轮询失败 auth_id=%s: %s", auth_id, exc)
        with _lock:
            sess = _sessions.get(auth_id)
            if sess is not None:
                sess["error"] = str(exc)
        return _public(_sessions.get(auth_id, {"status": "pending"}))

    status = str((data or {}).get("status") or "pending")
    with _lock:
        sess = _sessions.get(auth_id)
        if sess is None:
            return {"status": "expired", "done": False}
        if status == "success":
            token = str(data.get("access_token") or "")
            if token:
                # ⚠️ 入库前必须**先验证**。
                #
                # 实测（2026-11-04）：扫码拿到的 IAM 会话对业务接口一律返回
                # `200901 iam_confirm_required`（目录 / 积分 / 权益全一样），
                # 而客户端 auth.json 里那张票是好的。直接入库的后果是
                # **把能用的凭证覆盖成不能用的**：账号还在，但刷新余额报错、
                # 对话 502，用户只会看到「授权成功了却什么都用不了」。
                #
                # 这里用最便宜的目录接口探一次：不通就不落库，把原因回给前端。
                from admin.platform.raccoon.core import client as raccoon_client

                try:
                    models = raccoon_client.fetch_models(token, "cn", None)
                except raccoon_client.UpstreamError as exc:
                    sess["status"] = "failed"
                    sess["error"] = str(exc.message or "扫码会话不可用")
                    sess["confirm_required"] = (getattr(exc, "kind", "") == "confirm")
                    logger.warning("扫码会话验证失败 auth_id=%s kind=%s",
                                   auth_id, getattr(exc, "kind", ""))
                    return _public(sess)
                if not models:
                    sess["status"] = "failed"
                    sess["error"] = ("扫码成功但拿不到模型目录（上游返回空清单），"
                                     "该会话暂不可用；请改用客户端凭证扫描/导入")
                    return _public(sess)

                # 直接入库（import_record 幂等：同 uid 更新）
                from admin.platform.raccoon.services import accounts as raccoon_accounts

                claims = _jwt_claims(token)
                uid = str(claims.get("sub") or "")
                exp = claims.get("exp")
                record = {"token": token, "uid": uid,
                          "expiresAt": int(exp * 1000) if isinstance(exp, (int, float)) and exp else None,
                          "source": "webauth"}
                try:
                    raccoon_accounts.import_record(db, record)
                    db.commit()
                    sess["status"] = "done"
                    sess["account"] = {"uid": uid, "name": "小浣熊 " + (uid[-4:] if len(uid) >= 4 else "auth")}
                except ValueError as exc:
                    sess["status"] = "failed"
                    sess["error"] = "凭证入库失败：%s" % exc
            else:
                sess["status"] = "failed"
                sess["error"] = "官方返回 success 但没有 access_token"
        elif status in ("logging", "pending", "canceled"):
            sess["status"] = status
    return _public(_sessions.get(auth_id, {"status": "pending"}))


def cancel(auth_id: str) -> dict:
    with _lock:
        sess = _sessions.pop(auth_id, None)
    if sess is None:
        return {"canceled": False}
    try:
        _iam_request("PUT", "/auth/mp/qrcode",
                     {"qrcode_code": sess["qrcode_code"], "status": "canceled"})
    except Exception:  # noqa: BLE001 - 取消失败不影响本地状态清理
        pass
    return {"canceled": True}


def qr_svg(qr_value: str) -> str:
    """二维码内容 → SVG 字符串（`qrcode` 库已随生产环境安装）。"""
    import qrcode
    import qrcode.constants
    import qrcode.image.svg

    img = qrcode.make(qr_value, image_factory=qrcode.image.svg.SvgPathImage,
                      border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
    out = img.to_string()
    return out.decode("utf-8") if isinstance(out, bytes) else out


def _jwt_claims(token: str) -> dict:
    import base64

    parts = str(token or "").split(".")
    if len(parts) != 3:
        return {}
    try:
        pad = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json_loads(base64.urlsafe_b64decode(pad))
        return claims if isinstance(claims, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _public(sess: dict) -> dict:
    status = sess.get("status", "pending")
    return {
        "status": status,
        "done": status == "done",
        "account": sess.get("account"),
        "error": sess.get("error"),
        # 扫码会话缺 IAM 确认时单独标出来：前端据此提示
        # 「去客户端登录一次 / 改用扫描入库」，而不是笼统地说「授权失败」。
        "confirm_required": bool(sess.get("confirm_required")),
        "expires_in": max(0, int(sess.get("expires_at", 0) - time.time())),
        "poll_interval": sess.get("poll_interval", 2),
    }


def urllib_parse_quote(value: str) -> str:
    import urllib.parse
    return urllib.parse.quote(value, safe="")
