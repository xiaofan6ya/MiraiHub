"""科搭微信扫码授权登录（免客户端、免密码）。

## 协议（与科搭自己的网页端同一套）

    POST {GATEWAY}/v1/wechat/login/qrcode   {}              → {scene, qr_url}
    POST {GATEWAY}/v1/wechat/login/status   {"scene": "…"}  → {status, token, username}

* `qr_url` 是微信自己的二维码图片地址（`mp.weixin.qq.com/cgi-bin/showqrcode?ticket=…`），
  不是我们要渲染的内容 —— 所以这里**代理取字节**再回给控制台：既绕开图片外链，
  也顺手把「上游给什么就显示什么」的注入面收窄到只允许白名单主机。
* 状态词按网页端自己的读法：`SUCCESS` / `AUTHORIZED` / `COMPLETED` 都算完成；
  `EXPIRED` 或业务码 404 是二维码过期；`*BIND_PHONE*` / `*IP_VERIFY*` 是上游
  要求先绑手机号 / 先过短信验证 —— 这两种要原样告诉用户，别含糊成「扫码失败」。

## 为什么不复用 `client.request_json`

那两个端点在**未登录**时调用，没有 token；而 `request_json` 的错误分类是按
「已登录的业务码」写的（401 就是登录失效、429 就是限流）。扫码阶段的
`{"code":404}` 意思是「二维码过期」，套进去会被说成「账号登录已过期」——
所以这里用最小 HTTP 调用，只把状态词交给自己解释。

## 会话

一次扫码一个会话，内存里放 5 分钟；`poll` 命中完成就换票并入库，之后
会话标记 done（重复轮询不会重复入库）。同一时刻只保留少量会话，过期回收。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.parse

import httpx
from sqlalchemy.orm import Session

from admin.platform.codar.core import endpoints, paths
from admin.platform.codar.services import accounts as codar_accounts

logger = logging.getLogger("admin.platform.codar.webauth")

QR_PATH = "/v1/wechat/login/qrcode"
STATUS_PATH = "/v1/wechat/login/status"

#: 二维码有效期（上游自己大约 5 分钟，这里只做本地兜底回收）
TTL_S = 300
#: 状态轮询超时（一次请求）
POLL_TIMEOUT_S = 12
#: 二维码图片体积上限（正常十几 KB）
MAX_QR_BYTES = 512 * 1024

#: 完成态（网页端把状态词大写后比这三组）
DONE_STATES = {"SUCCESS", "AUTHORIZED", "COMPLETED"}
BIND_STATES = {"REQUIRED_BIND_PHONE", "BIND_PHONE_REQUIRED", "BIND_PHONE",
               "REQUIRE_PHONE_BINDING"}
IP_STATES = {"REQUIRED_IP_VERIFY", "IP_VERIFY_REQUIRED", "IP_VERIFY",
             "REQUIRE_IP_VERIFY"}

#: 二维码图片只允许这些主机（微信自己的图片口 + 科搭的 web 域）
QR_HOSTS = {"mp.weixin.qq.com", "codar-web.speedai.com", "api.kuaipaper.com"}

_SESSIONS: dict[str, dict] = {}
_LOCK = threading.Lock()


class AuthError(Exception):
    """扫码授权失败（带用户能照做的一句话）。

    `kind` 区分「上游拒绝」和「还没回音」：状态接口是**长轮询**，手机还没扫的
    时候它不回包，读超时属于正常等待，不能当成失败弹给用户。
    """

    def __init__(self, message: str, *, kind: str = "upstream"):
        super().__init__(message)
        self.kind = kind


def _client(timeout: float | None = None) -> httpx.Client:
    # 不走环境代理：与模块内其它上游调用保持一致（出口 IP 变化会进风控桶）
    return httpx.Client(trust_env=False, timeout=timeout or POLL_TIMEOUT_S,
                        headers={"User-Agent": endpoints.USER_AGENT,
                                 "Accept": "application/json"})


def _post(path: str, body: dict | None = None, *,
          timeout: float | None = None) -> dict:
    # 扫码这两个端点在**账号域**（api.kuaipaper.com），不在模型网关
    # （api.claw.speedai.com）—— 挂错域名的表现是干净的 404。
    url = endpoints.ACCOUNT_BASE.rstrip("/") + path
    try:
        with _client(timeout) as client:
            response = client.post(url, json=body) if body is not None else client.post(url)
    except httpx.HTTPError as exc:
        kind = "timeout" if isinstance(exc, httpx.TimeoutException) else "network"
        raise AuthError("连接科搭授权服务失败（%s）" % type(exc).__name__,
                        kind=kind) from exc
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        raise AuthError("科搭授权服务返回了非 JSON（HTTP %d）" % response.status_code)
    if response.status_code >= 400 and not payload.get("data"):
        raise AuthError("科搭授权服务返回 HTTP %d" % response.status_code)
    return payload


def _unwrapped(payload: dict) -> dict:
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


def _business_code(payload: dict):
    code = payload.get("code")
    if isinstance(code, bool):
        return None
    if isinstance(code, int):
        return code
    if isinstance(code, str) and code.strip().isdigit():
        return int(code.strip())
    return None


def qr_url_allowed(url: str) -> bool:
    """这张图能不能显示：https、无 userinfo、无端口、主机在白名单里。"""
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    if parsed.scheme != "https" or parsed.username or parsed.password:
        return False
    if parsed.port:
        return False
    return (parsed.hostname or "").lower() in QR_HOSTS


def _sweep() -> None:
    now = time.time()
    with _LOCK:
        for auth_id, session in list(_SESSIONS.items()):
            if session["status"] in ("pending", "scanned") and now - session["created"] > TTL_S:
                session["status"] = "expired"
            if now - session["created"] > TTL_S * 2:
                _SESSIONS.pop(auth_id, None)


def begin() -> dict:
    """开一个扫码会话，返回 `{auth_id, scene, qr_url, expires_in}`。"""
    _sweep()
    payload = _post(QR_PATH)
    data = _unwrapped(payload)
    scene = str(data.get("scene") or "").strip()
    qr_url = str(data.get("qr_url") or data.get("qrUrl") or "").strip()
    if not scene:
        raise AuthError("科搭没有返回扫码会话标识（scene）")
    if not qr_url_allowed(qr_url):
        raise AuthError("科搭返回的二维码地址不在允许的白名单里")
    auth_id = os.urandom(12).hex()
    with _LOCK:
        _SESSIONS[auth_id] = {
            "auth_id": auth_id,
            "scene": scene,
            "qr_url": qr_url,
            "created": time.time(),
            "status": "pending",
            "account_id": 0,
            "account": "",
            "error": "",
        }
    logger.info("科搭扫码会话已创建：%s", auth_id)
    return {"auth_id": auth_id, "scene": scene, "qr_url": qr_url,
            "expires_in": TTL_S}


def _public(session: dict) -> dict:
    return {
        "auth_id": session["auth_id"],
        "status": session["status"],
        "expires_in": max(0, int(TTL_S - (time.time() - session["created"]))),
        "account_id": session["account_id"],
        "account": session["account"],
        "error": session["error"],
    }


def poll(db: Session, auth_id: str) -> dict:
    """查一次状态；完成时换票并入库（幂等）。"""
    _sweep()
    with _LOCK:
        session = _SESSIONS.get(auth_id)
    if session is None:
        return {"status": "expired", "error": "扫码会话不存在或已过期"}
    if session["status"] in ("done", "failed", "expired", "canceled"):
        return _public(session)

    # 状态接口是长轮询：手机没扫的时候它**不回包**。给一个短超时，
    # 超时就是「还没扫」，不是错误。
    try:
        payload = _post(STATUS_PATH, {"scene": session["scene"]}, timeout=4.0)
    except AuthError as exc:
        if exc.kind == "timeout":
            return _public(session)
        raise
    data = _unwrapped(payload)
    said = str(data.get("status") or "").strip().upper()
    message = str(payload.get("message") or data.get("message") or "").strip()

    if said in DONE_STATES:
        token = str(data.get("token") or "").strip()
        username = str(data.get("username") or "").strip()
        if not token:
            session.update(status="failed",
                           error=message or "扫码登录没有返回令牌")
            return _public(session)
        try:
            verified = paths.verify_token(token)
        except paths.ScanError as exc:
            session.update(status="failed", error=str(exc))
            return _public(session)
        try:
            account, _created = codar_accounts.import_record(
                db, {"token": verified["token"],
                     "username": username or verified["username"],
                     "source": "qr"})
            session.update(status="done", account_id=int(account.id or 0),
                           account=account.name or "", error="")
        except Exception as exc:  # noqa: BLE001 - 入库失败要能看见
            logger.exception("科搭扫码令牌入库失败")
            session.update(status="failed", error="凭据已换到但入库失败：%s" % exc)
        return _public(session)

    if said == "EXPIRED" or _business_code(payload) == 404:
        session.update(status="expired", error="二维码已过期，请重新获取")
        return _public(session)
    if said in BIND_STATES:
        session.update(status="failed",
                       error=message or "该微信还没绑定科搭账号：请先登录科搭绑定手机号")
        return _public(session)
    if said in IP_STATES:
        session.update(status="failed",
                       error=message or "该账号当前网络需要短信安全验证，请改用粘贴令牌导入")
        return _public(session)
    if said == "FAILED":
        session.update(status="failed", error=message or "扫码登录失败")
        return _public(session)

    # 其余都当「还没扫 / 扫了没确认」，与网页端一致
    if said in ("SCANNED", "SCAN"):
        session["status"] = "scanned"
    return _public(session)


#: 允许回给浏览器的图片类型（按 magic 判，不信上游的 content-type）
_IMAGE_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF8", "image/gif"),
)


def _image_type(body: bytes) -> str:
    for magic, said in _IMAGE_MAGIC:
        if body.startswith(magic):
            return said
    if body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return "image/webp"
    return ""


def qr_image(auth_id: str) -> tuple[bytes, str]:
    """取二维码图片字节（服务端代理，避免控制台直接外链微信）。

    ⚠️ 微信的 `showqrcode` 回的是 **JPEG**（`content-type: image/jpg`），
    不是 PNG —— 按扩展名或想当然判类型会让「扫码框」显示成破图。
    这里按 magic 判真实类型，认不出来才算失败。
    """
    with _LOCK:
        session = _SESSIONS.get(auth_id)
    if session is None:
        raise AuthError("扫码会话不存在或已过期")
    url = session["qr_url"]
    if not qr_url_allowed(url):
        raise AuthError("二维码地址不在允许的白名单里")
    try:
        with httpx.Client(trust_env=False, timeout=POLL_TIMEOUT_S) as client:
            response = client.get(url, follow_redirects=False)
    except httpx.HTTPError as exc:
        raise AuthError("二维码图片下载失败（%s）" % type(exc).__name__) from exc
    if response.status_code != 200:
        raise AuthError("二维码图片下载失败（HTTP %d）" % response.status_code)
    body = response.content
    if len(body) > MAX_QR_BYTES:
        raise AuthError("二维码图片异常地大")
    media = _image_type(body)
    if not media:
        raise AuthError("二维码图片不是可识别的图片格式")
    return body, media


def cancel(auth_id: str) -> dict:
    with _LOCK:
        session = _SESSIONS.get(auth_id)
    if session is None:
        return {"status": "expired"}
    if session["status"] in ("pending", "scanned"):
        session["status"] = "canceled"
    return _public(session)
