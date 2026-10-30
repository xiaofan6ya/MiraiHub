"""Trae 浏览器授权登录（PKCE）。

## 为什么必须有它

Trae 的登录态在 `storage.json` 里是加密的，只能靠本机客户端。
但官方支持一条**标准 OAuth PKCE 授权链**：打开授权页 → 用户点同意
→ 浏览器回调 `http://127.0.0.1:<port>/authorize?code=...&state=...`
→ 用 code + verifier 换 access token。

这条链跑通后**不需要手动填 Cookie** —— 用户点一次链接就完成授权。

## 授权 URL 长什么样（来自真实客户端抓包）

    https://www.trae.ai/authorization
        ?login_version=1&auth_from=trae&login_channel=native_ide
        &plugin_version=2.3.93439&auth_type=local&client_id=ono9krqynydwx5
        &redirect=0&auth_callback_url=http://127.0.0.1:50069/authorize
        &machine_id=...&device_id=...&x_device_id=...&x_machine_id=...
        &x_device_brand=...&x_device_type=windows&x_os_version=...
        &code_challenge=<S256(verifier)>&code_challenge_method=S256
        &channel_name=common

关键点：
  · `code_challenge` 必须是 `base64url(sha256(verifier))`，verifier 自己留着换 token
  · `auth_callback_url` 必须是**本机端口**，授权完浏览器会跳回来
  · `device_id` / `machine_id` 用账号自己的（或固定默认值）都行
  · 换 token 走 `POST /cloudide/api/v3/trae/oauth/ExchangeToken`

## 与 Qoder 的关系

Qoder 已经用同一套 PKCE 思路（`admin/platform/qoder/core/oauth.py`），
这里把参数换成 Trae 的即可。两边不要互相 import，各自独立。
"""
from __future__ import annotations

import base64
import hashlib
import os
import json
import logging
import secrets
import socket
import time
import urllib.parse
from admin.platform.auth import CallbackSession, parse_callback_query

logger = logging.getLogger("admin.platform.trae.oauth")

#: 授权页 host：**国内版和国际版是两台不同的站点**，不能混。
#:
#:   cn   -> https://www.trae.cn   （的地址）
#:   intl -> https://www.trae.ai
#:
#: 之前写死成 trae.ai，导致国内版账号被送到国际站授权，
#: 页面直接报 `Login Failed / Something went wrong`。
AUTH_HOSTS = {
    "cn": "https://www.trae.cn",
    "intl": "https://www.trae.ai",
}


def auth_host(realm: str) -> str:
    return AUTH_HOSTS.get("intl" if str(realm).lower() == "intl" else "cn")
#: 客户端 id（来自真实客户端，固定值）
CLIENT_ID = "ono9krqynydwx5"
#: 换 token 的端点（相对 core host）
EXCHANGE_PATH = "/trae/api/v3/oauth/ExchangeToken"   # 注意不是 cloudide
#: 默认设备指纹（与 solo.py 保持一致）
DEFAULT_DEVICE_ID = "1149420231460112"
DEFAULT_MACHINE_ID = ("d979d81c1deda7a803cb6b76be69cff63b40dd24ea60d3fabbd"
                      "865607f7ee695")
DEFAULT_BRAND = "NH5x_NH7x_HHx_HJx_HKx"
DEFAULT_OS = "Windows 11 Pro"
APP_VERSION = "3.4.11"
PLUGIN_VERSION = "2.3.98528"

#: 授权会话（nonce -> session），进程内保存，10 分钟过期
_SESSIONS: dict = {}
_SESSION_TTL = 600


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


#: 主服务的监听地址（回调要落到它上面）
MAIN_HOST = os.environ.get("WB_OAUTH_CALLBACK_HOST", "127.0.0.1")
MAIN_PORT = os.environ.get("WB_OAUTH_CALLBACK_PORT", "8790")
#: 回调**路径**必须是 `/authorize` —— 上游按路径校验，
#: 换成 `/api/...` 这种自定义路径会直接 Login Failed。
#: 我们用主服务的 8790 端口，但路径保持客户端原样。
CALLBACK_PATH = "/authorize"


def callback_url() -> str:
    """授权完成后的回调地址（指向主服务，不单开端口）。

    用环境变量可覆盖 —— 部署在反代后面时公网地址和本地不同。
    """
    return "http://%s:%s%s" % (MAIN_HOST, MAIN_PORT, CALLBACK_PATH)


def free_port() -> int:
    """找一个可用端口给回调用。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


def callback_payload(params: dict[str, str]) -> dict:
    """解析自动回调和手动提交共用的参数，不访问回调 URL。"""
    def object_param(name: str) -> dict:
        raw = params.get(name, "")
        if not raw:
            return {}
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            raise ValueError("回调中的 %s 不是有效 JSON" % name) from None
        if not isinstance(value, dict):
            raise ValueError("回调中的 %s 必须是 JSON 对象" % name)
        return value

    error = params.get("error", "").strip()
    if error:
        return {"error": params.get("error_description", "").strip() or error}
    code = params.get("code", "").strip()
    if not code:
        info = object_param("authCodeInfo")
        code = info.get("AuthCode") or info.get("authCode") or ""
    if not isinstance(code, str) or not code.strip():
        raise ValueError("回调缺少授权码，请复制授权后跳转页面的完整地址，而不是授权页链接")

    host = params.get("host", "").strip().rstrip("/")
    if host:
        parsed = urllib.parse.urlsplit(host)
        hostname = (parsed.hostname or "").lower()
        if (parsed.scheme != "https" or parsed.username or parsed.password
                or parsed.port not in (None, 443) or parsed.path
                or parsed.query or parsed.fragment
                or not any(hostname == domain or hostname.endswith("." + domain)
                           for domain in ("trae.cn", "trae.com.cn", "trae.ai"))):
            raise ValueError("回调中的 host 不是有效的 Trae 服务地址")
    return {"code": code.strip(), "host": host,
            "user_info": object_param("userInfo")}


def parse_callback_url(value: str, nonce: str) -> dict:
    """兼容调用方：公共 URL 校验 + 平台参数解析。"""
    return callback_payload(parse_callback_query(
        value, nonce, ("nonce", "login_trace_id", "loginTraceID", "state")))


class DeviceLogin(CallbackSession):
    """一次 PKCE 授权会话。"""

    def __init__(self, realm: str = "cn", *, device_id: str = "",
                 machine_id: str = "", port: int = 0):
        super().__init__("trae", "intl" if str(realm).lower() == "intl" else "cn", ttl=_SESSION_TTL)
        # 客户端源码：randomBytes(48).toString('base64url')，48 字节而非 32
        self.verifier = _b64url(secrets.token_bytes(48))
        self.challenge = _b64url(hashlib.sha256(self.verifier.encode()).digest())
        self.device_id = str(device_id or DEFAULT_DEVICE_ID)
        self.machine_id = str(machine_id or DEFAULT_MACHINE_ID)
        self.port = int(port or free_port())

    # ---- URL ----
    @property
    def auth_url(self) -> str:
        params = {
            "login_version": "1",
            "auth_from": "trae",
            "login_channel": "native_ide",
            "plugin_version": PLUGIN_VERSION,
            "auth_type": "local",
            "client_id": CLIENT_ID,
            "redirect": "0",
            # 回调走**主服务**（8790）的固定路由，不再单开端口：
            # 单开端口需要额外起线程、还要处理 favicon 抢先退出等问题，
            # 用主服务既省事又不会有端口被占的风险。
            # 回调地址**不要**带自定义 query：上游对 auth_callback_url
            # 做前缀比对，多一个 `?nonce=` 就可能不认。
            # 会话靠 `login_trace_id` 关联 —— 它会被上游原样带回到回调里。
            "auth_callback_url": callback_url(),
            "machine_id": self.machine_id,
            "device_id": self.device_id,
            "x_device_id": self.device_id,
            "x_machine_id": self.machine_id,
            # 客户端取 `getSystemInformation().deviceModel`，拿不到就是空串；
            # 写死品牌串反而和真实机型不符。
            "x_device_brand": "",
            "x_device_type": "windows",
            "x_os_version": DEFAULT_OS,
            # 客户端在没有 BOE 环境时传 `undefined`（序列化后为空）
            "x_env": "",
            "x_app_version": APP_VERSION,
            "x_app_type": "stable",
            "code_challenge": self.challenge,
            "code_challenge_method": "S256",
            "channel_name": "common",
            # 随机串防重放；回调里会原样带回
            "login_trace_id": self.nonce,
        }
        return auth_host(self.realm) + "/authorization?" + urllib.parse.urlencode(params)

    def as_dict(self) -> dict:
        # 不再返回 `port`：回调走主服务的固定路由，没有独立端口了。
        return {
            "realm": self.realm,
            "nonce": self.nonce,
            "auth_url": self.auth_url,
            "callback_url": callback_url(),
            "expires_in": _SESSION_TTL,
            "summary": "打开授权链接 → 选择账号登录 → 自动回调；跳转失败时可手动粘贴回调链接",
        }

    # ---- 回调服务器 ----
    def start(self) -> None:
        """占位：回调由**主服务路由**接收，这里不再单开端口。

        早期实现起了一个 `http.server` 线程监听随机端口，有两个问题：
          1. 浏览器会先请求 `/favicon.ico`，`handle_request()` 处理完
             第一个请求就退出，真正的 `/authorize` 回调收不到；
          2. 多一层端口、多一层线程，还要用户放行防火墙。
        改成主服务的固定路由后两个问题都没了。
        """
        return None

    @property
    def code(self) -> str:
        return str((super().wait() or {}).get("code") or "")

    def wait(self, timeout: float = 0.0) -> dict | None:
        result = super().wait(timeout)
        if result is not None:
            result.update(verifier=self.verifier, realm=self.realm)
        return result


def _device_keypair() -> tuple[str, str]:
    """生成 EC P-256 密钥对（PEM）。

    客户端源码 `main.js` 里是这么做的：

        generateKeyPairSync("ec", {{ namedCurve: "P-256",
            publicKeyEncoding: {{ type: "spki", format: "pem" }},
            privateKeyEncoding: {{ type: "pkcs8", format: "pem" }} }})

    公钥会作为 `DeviceInfo.DevicePublicKey` 上报给上游，
    所以要生成真实密钥对而不是编一个字符串。
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    priv = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    pub = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    return priv, pub


def _device_info(device_id: str, machine_id: str, public_key_pem: str,
                 app_version: str) -> dict:
    """拼 `DeviceInfo`（字段名逐个对齐客户端源码 `_buildDeviceInfo`）。"""
    return {
        "DeviceID": str(device_id or DEFAULT_DEVICE_ID),
        "MachineID": str(machine_id or DEFAULT_MACHINE_ID),
        "PlatformCode": "IDE_PC",
        "DeviceType": "PC",
        "DeviceName": os.environ.get("COMPUTERNAME", "Windows-PC"),
        "DeviceModel": "",
        "ClientVersion": app_version,
        "DevicePublicKey": public_key_pem,
        "DeviceBrand": "",
        "DeviceCPU": "",
        "OSInfo": "Windows",
        "OSVersion": DEFAULT_OS,
    }


def exchange_token(code: str, verifier: str, *, realm: str = "cn",
                   device_id: str = "", machine_id: str = "",
                   host: str = "", timeout: float = 20.0) -> dict:
    """用授权码换 token。

    ## 端点（这是最关键的一处）

        POST {{host}}/trae/api/v3/oauth/ExchangeToken

    注意是 `/trae/api/v3/oauth/...`，**不是** `/cloudide/api/v3/trae/oauth/...`。
    后者只接受 `RefreshToken`，拿 AuthCode 去调永远是 `10101 无效参数`
    （我按错误的路径试了 6 种字段组合，全部同一个错，才回头去翻客户端源码）。

    ## Body 结构（照抄客户端 `exchangeTokenByAuthCode`）

        {{
          "ClientID":     "ono9krqynydwx5",
          "AuthCode":     "<回调里的 authCodeInfo.AuthCode>",
          "CodeVerifier": "<我们生成的 verifier>",
          "DeviceInfo":   {{ DeviceID, MachineID, PlatformCode, DeviceType,
                            DeviceName, DeviceModel, ClientVersion,
                            DevicePublicKey, ... }},
          "IDEVersion":   "<app version>"
        }}

    `DeviceInfo.DevicePublicKey` 必须是真实的 EC P-256 公钥（PEM），
    客户端用 `generateKeyPairSync("ec", {{namedCurve:"P-256"}})` 生成。
    """
    import httpx

    base = str(host or "").strip().rstrip("/")
    if not base:
        base = "https://api.trae.com.cn" if realm == "cn" else "https://api.trae.ai"

    _priv_pem, pub_pem = _device_keypair()
    payload = {
        "ClientID": CLIENT_ID,
        "AuthCode": str(code or ""),
        "CodeVerifier": str(verifier or ""),
        "DeviceInfo": _device_info(device_id, machine_id, pub_pem, APP_VERSION),
        "IDEVersion": APP_VERSION,
    }
    # 保留部署方的环境代理；只重试尚未发送授权码的建连失败。
    # 读取超时、HTTP 错误不能自动重放一次性授权码。
    for attempt in range(2):
        try:
            with httpx.Client(timeout=httpx.Timeout(timeout, connect=min(10.0, timeout))) as connection:
                response = connection.post(base + EXCHANGE_PATH, json=payload, headers={
                    "Accept": "application/json", "User-Agent": "Trae/%s" % APP_VERSION,
                })
            break
        except (httpx.ConnectTimeout, httpx.ConnectError) as exc:
            if attempt == 0:
                logger.warning("Trae 授权连接失败，重新建立连接一次（%s）", type(exc).__name__)
                continue
            raise RuntimeError("连接 Trae 授权服务失败或超时，请稍后重新发起授权") from None
        except httpx.TimeoutException:
            raise RuntimeError("Trae 授权服务响应超时，请重新发起授权") from None
        except httpx.RequestError:
            raise RuntimeError("与 Trae 授权服务的连接中断，请重新发起授权") from None
    if response.is_error:
        raise RuntimeError("Trae 授权服务返回 HTTP %s，请重新发起授权" % response.status_code)
    try:
        data = response.json()
    except ValueError:
        raise RuntimeError("Trae 授权服务返回了无效响应，请稍后重新发起授权") from None
    if not isinstance(data, dict):
        raise RuntimeError("Trae 授权服务返回了无效响应，请稍后重新发起授权")

    # 上游把错误放在 `ResponseMetadata.Error`，结果放在 `Result`
    error = ((data.get("ResponseMetadata") or {}).get("Error") or {})
    code_val = error.get("Code")
    # 不能用 `not in (None, "", 0)`：Code 可能是 dict，`in` 会哈希直接崩
    if code_val is not None and code_val is not False \
            and not (isinstance(code_val, str) and not code_val.strip()) \
            and not (isinstance(code_val, (int, float)) and code_val == 0) \
            and not (isinstance(code_val, (dict, list)) and not code_val):
        raise RuntimeError("ExchangeToken 拒绝：%s %s"
                           % (error.get("Code"), error.get("Message") or ""))
    result = data.get("Result") or data.get("result") or data
    return result if isinstance(result, dict) else {}


def prune() -> None:
    """清理过期会话。"""
    now = int(time.time())
    for key in [k for k, v in _SESSIONS.items()
                if now - v.created_at > _SESSION_TTL]:
        _SESSIONS.pop(key, None)


def start(realm: str = "cn", **kwargs) -> DeviceLogin:
    """创建并启动一个授权会话（同时注册到进程内表）。"""
    prune()
    session = DeviceLogin(realm, **kwargs)
    session.start()
    _SESSIONS[session.nonce] = session
    return session


def latest_pending() -> DeviceLogin | None:
    """找最近一个**还没拿到 code** 的会话。

    上游回调不保证带回 `login_trace_id` / `state`（实测有时只有 `code`），
    这时按「最近的未完成会话」匹配 —— 同一时刻用户只会发起一次授权，
    这个假设是安全的，而且比直接报「会话已失效」好得多。
    """
    prune()
    pending = [s for s in _SESSIONS.values() if not s._done.is_set()]
    if not pending:
        return None
    return max(pending, key=lambda s: s.created_at)


def get(nonce: str) -> DeviceLogin | None:
    prune()
    return _SESSIONS.get(str(nonce or ""))
