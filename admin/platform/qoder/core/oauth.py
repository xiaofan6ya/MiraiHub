"""Qoder PKCE 设备授权（照参考实现补齐，之前只有 PAT）。

## 为什么需要它

用户：「你扫不出来导入跟保活的话后期会很麻烦，不能多账户了」

Qoder 的登录态加密在 `auth.v1.dat`（原生层），本地读不出来。
参考实现（Agent2API）的做法是 **PKCE 设备授权**，不是读文件：

    1. 生成 verifier（随机）+ challenge = base64url(sha256(verifier))
    2. 打开 `https://<web_origin>/device/selectAccounts
              ?challenge=..&challenge_method=S256&machine_id=..&nonce=..`
    3. 用户在浏览器里选账号授权
    4. 轮询 `{open_api}/api/v1/deviceToken/poll
              ?nonce=..&verifier=..&challenge_method=S256`
       - 202 / 404 -> 继续等
       - 200 -> 拿到 token / refresh_token / user_id / expires_at
    5. 把 refresh 拼成 `<refresh>|<user_id>|<machine_id>`（参考实现的格式）

## 端点（实测自参考实现 endpoints.rs）

    DEVICE_LOGIN_PATH  = "/device/selectAccounts"
    DEVICE_POLL_PATH   = "/api/v1/deviceToken/poll"
    USER_INFO_PATH     = "/api/v1/userinfo"

## 和 PAT 的关系

两条路并存：
  * PAT：手动生成，永久（用户在 qoder.com.cn/account/integrations 生成）
  * 设备授权：点一次链接，自动拿 token（**可多账户**）

设备授权拿到的 refresh_token 可以续期，PAT 换的也是 `pat|...` 形态。
"""
from __future__ import annotations

import getpass
import base64
import hashlib
import json
import logging
import secrets
import time
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid

from admin.platform.auth import PollingSession
from admin.platform.qoder.core import endpoints as ep
from admin.platform.qoder.core.client import UpstreamError

logger = logging.getLogger("admin.platform.qoder.oauth")

#: 设备授权页路径
DEVICE_LOGIN_PATH = "/device/selectAccounts"
#: 轮询路径
DEVICE_POLL_PATH = "/api/v1/deviceToken/poll"
#: 用户信息
USER_INFO_PATH = "/api/v1/userinfo"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def machine_id_of_local() -> str:
    """本机设备标识。

    参考实现用 `machine::machine_id()`（读系统信息生成稳定的 UUID）。
    这里用一个稳定的派生值：用户名 + 主机名 的 UUID5 ——
    同一台机器上恒定，重装系统会变。
    """
    import os
    import platform

    seed = "%s|%s" % (os.environ.get("USERNAME") or getpass.getuser(),
                      platform.node() or "")
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, seed))


class DeviceLogin(PollingSession):
    """一次设备授权会话。

    用法：

        login = DeviceLogin("cn")
        # 把 login.auth_url 给用户点
        creds = login.poll()      # 轮询直到 200 或超时
    """

    def __init__(self, realm: str = "cn"):
        super().__init__("qoder", realm or "cn")
        self.verifier = _b64url(secrets.token_bytes(32))
        digest = hashlib.sha256(self.verifier.encode()).digest()
        self.challenge = _b64url(digest)
        self.nonce = uuid.uuid4().hex
        self.machine_id = machine_id_of_local()
        self.created_at = int(time.time())
        self.auth_url = self._build_auth_url()
        self.poll_url = self._build_poll_url()
        self._poll_lock = threading.Lock()
        self._credential_cache = None

    # ---- URL 构造 ----
    def _build_auth_url(self) -> str:
        base = ep.web_origin(self.realm).rstrip("/") + DEVICE_LOGIN_PATH
        query = urllib.parse.urlencode({
            "challenge": self.challenge,
            "challenge_method": "S256",
            "machine_id": self.machine_id,
            "nonce": self.nonce,
        })
        return base + "?" + query

    def _build_poll_url(self) -> str:
        base = ep.open_api(self.realm).rstrip("/") + DEVICE_POLL_PATH
        query = urllib.parse.urlencode({
            "nonce": self.nonce,
            "verifier": self.verifier,
            "challenge_method": "S256",
        })
        return base + "?" + query

    def as_dict(self) -> dict:
        return {
            "realm": self.realm,
            "auth_url": self.auth_url,
            "nonce": self.nonce,
            "machine_id": self.machine_id,
            "created_at": self.created_at,
            "expires_in": 600,
        }

    # ---- 轮询 ----
    def poll_once(self) -> dict | None:
        with self._poll_lock:
            if self.expired:
                raise UpstreamError("授权会话已过期", status=408, kind="timeout")
            if self._credential_cache is None:
                self._credential_cache = self._poll_once()
            return dict(self._credential_cache) if self._credential_cache else None

    def _poll_once(self) -> dict | None:
        """轮询一次。

        * HTTP 202 / 404 -> `None`（用户还没授权）
        * HTTP 200        -> 凭证 dict
        * 其它            -> raise UpstreamError
        """
        request = urllib.request.Request(self.poll_url, method="GET")
        for key, value in ep.open_api_headers(None).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                status = response.status
                text = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            if exc.code in (202, 404):
                return None
            payload = ""
            try:
                payload = exc.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                pass
            raise UpstreamError("设备授权轮询失败（HTTP %d）" % exc.code,
                                status=exc.code, kind="http_error",
                                detail=payload[:300]) from exc
        except Exception as exc:  # noqa: BLE001
            raise UpstreamError("设备授权轮询网络失败", status=0,
                                kind="network", detail=str(exc)[:200]) from exc

        if status in (202, 404):
            return None
        if status != 200:
            raise UpstreamError("设备授权轮询异常（HTTP %d）" % status,
                                status=status, kind="http_error")

        try:
            payload = json.loads(text) if text.strip() else {}
        except ValueError as exc:
            raise UpstreamError("设备授权响应非 JSON", status=0,
                                kind="bad_response", detail=text[:200]) from exc
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload

        token = str(data.get("token") or data.get("access_token") or "").strip()
        if not token:
            raise UpstreamError("设备授权响应缺少 token", status=0,
                                kind="bad_response",
                                detail=json.dumps(data, ensure_ascii=False)[:300])
        refresh = str(data.get("refresh_token") or "").strip()
        user_id = str(data.get("user_id") or data.get("userId") or "").strip()
        expires_at = _timestamp(data.get("expires_at"))

        return {
            "realm": self.realm,
            "token": token,
            "refresh_token": _pack_refresh(refresh, user_id, self.machine_id),
            "uid": user_id,
            "machine_id": self.machine_id,
            "expires_at": expires_at or (int(time.time()) + 30 * 86400) * 1000,
            "source": "device",
        }

    def poll(self, *, timeout: float = 300.0, interval: float = 3.0) -> dict:
        """轮询直到拿到凭证或超时（同步阻塞，给后台线程用）。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            result = self.poll_once()
            if result is not None:
                return result
            time.sleep(interval)
        raise UpstreamError("设备授权超时（%d 秒内未完成）" % int(timeout),
                            status=408, kind="timeout")


def _pack_refresh(refresh: str, user_id: str, machine_id: str) -> str:
    """把 refresh 拼成参考实现的格式：`<refresh>|<user>|<machine>`。

    参考实现里 `Credentials::from_payload` 按 `|` 切分，最后两段固定是
    「用户 | 设备」。PAT 来源是 `pat|<PAT>|<job_refresh>|<user>|<machine>`。
    """
    if not refresh:
        return ""
    if "|" in refresh:
        parts = refresh.split("|")
        if len(parts) >= 3:
            prefix = "|".join(parts[:-2])
            return "%s|%s|%s" % (prefix, user_id, machine_id)
        return refresh
    return "%s|%s|%s" % (refresh, user_id, machine_id)


def _timestamp(value) -> int:
    """把各种时间格式转成毫秒时间戳（0 表示未知）。"""
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        number = int(value)
        return number * 1000 if number < 100_000_000_000 else number
    text = str(value).strip()
    if not text:
        return 0
    try:
        number = int(text)
        return number * 1000 if number < 100_000_000_000 else number
    except ValueError:
        pass
    try:
        from datetime import datetime

        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return int(parsed.timestamp() * 1000)
    except ValueError:
        return 0
