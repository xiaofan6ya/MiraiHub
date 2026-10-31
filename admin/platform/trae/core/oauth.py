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
import threading
import time
import urllib.parse

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
#: 客户端版本号 —— 必须和用户实际装的客户端一致。
#:
#: ## 这两个值从哪来（别再猜了）
#:
#: `%INSTALL%/resources/app/product.json` 里有两个**不同**的字段：
#:
#:     "tronBuildVersion": "2.3.98528"   -> 授权链接的 plugin_version
#:     "appVersion":       "3.4.11"      -> x_app_version / IDEVersion
#:
#: 真实抓包（`%APPDATA%/Trae CN/logs/*/main.log` 的
#: `[exchangeTokenByAuthCode] request`）里也是这一对，且上游回
#: `[exchangeToken] response success`。**注意 package.json 的 version
#: 是 1.107.1，那是 VSCode 内核版本，不是 Trae 自己的 appVersion** ——
#: 拿错一个就回 `10101 无效参数`，而那个错误文案是没填好的模板占位符
#: （`无效参数：{__Message.field}`），光看码根本看不出是版本号。
#:
#: 取值顺序：环境变量 > 自动读本机 product.json > 内置兜底。
_ENV_APP_VERSION = "TRAE_APP_VERSION"
_ENV_PLUGIN_VERSION = "TRAE_PLUGIN_VERSION"
_FALLBACK_APP_VERSION = "3.4.11"
_FALLBACK_PLUGIN_VERSION = "2.3.98528"

#: 可能的安装根目录（相对各自平台）
_APP_ROOTS = (
    r"%LOCALAPPDATA%\Programs\Trae CN",
    r"%LOCALAPPDATA%\Programs\Trae",
    r"C:\Program Files\Trae CN",
    r"C:\Program Files\Trae",
    r"D:\Trae CN\Trae CN",
    r"D:\Trae CN",
    r"D:\Trae",
    "/opt/trae-cn",
    "/opt/trae",
    "/Applications/Trae CN.app",
    "/Applications/Trae.app",
)


def _read_product_json() -> dict:
    """读本机 Trae 的 `product.json`，读不到返回空 dict。"""
    import glob as _glob
    for root in _APP_ROOTS:
        expanded = os.path.expandvars(root.rstrip("\\/"))
        for pattern in (
            os.path.join(expanded, "resources", "app", "product.json"),
            os.path.join(expanded, "Contents", "Resources", "app", "product.json"),
        ):
            for path in _glob.glob(pattern):
                try:
                    with open(path, "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                    if isinstance(data, dict):
                        return data
                except Exception:  # noqa: BLE001 - 探测失败不致命
                    continue
    return {}


def app_version() -> str:
    """`x_app_version` / `IDEVersion` / `DeviceInfo.ClientVersion`。

    注意读的是 `product.json.appVersion`，**不是** `package.json.version`
    （后者是 VSCode 内核版本 1.107.1，用它会被上游拒）。
    """
    explicit = (os.environ.get(_ENV_APP_VERSION) or "").strip()
    if explicit:
        return explicit
    detected = str(_read_product_json().get("appVersion") or "").strip()
    return detected or _FALLBACK_APP_VERSION


def plugin_version() -> str:
    """授权链接的 `plugin_version` —— 对应 `product.json.tronBuildVersion`。"""
    explicit = (os.environ.get(_ENV_PLUGIN_VERSION) or "").strip()
    if explicit:
        return explicit
    detected = str(_read_product_json().get("tronBuildVersion") or "").strip()
    return detected or _FALLBACK_PLUGIN_VERSION


#: 兼容旧引用：模块级常量保留，但授权/换 token 都走函数。
APP_VERSION = _FALLBACK_APP_VERSION
PLUGIN_VERSION = _FALLBACK_PLUGIN_VERSION

#: 授权会话（nonce -> session），进程内保存，10 分钟过期
_SESSIONS: dict = {}
_SESSION_TTL = 600


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


#: 回调**路径**必须是 `/authorize` —— 上游按路径校验，
#: 换成 `/api/...` 这种自定义路径会直接 Login Failed。
CALLBACK_PATH = "/authorize"

#: 桌面版启动后会把自己的真实端口写进这个环境变量。
#: 桌面版并不固定用 8790：`select_port()` 在端口被占时会换一个，
#: 所以回调地址**必须**跟着实际端口走。
_PORT_ENV = "WB_OAUTH_CALLBACK_PORT"
_HOST_ENV = "WB_OAUTH_CALLBACK_HOST"


def main_port() -> str:
    """当前主服务真正监听的端口。

    ## 为什么不能写死 8790

    桌面版每个用户的端口都可能不同（8790 被占就换 8791/8792…，极端情况
    系统随机分配）。回调地址写死 8790 时，服务实际跑在 54293 的用户点
    「授权登录」→ 浏览器跳 `http://127.0.0.1:8790/authorize` → **连不上**，
    表现为「授权完成后一直转圈 / 打不开页面」。

    取值顺序：
      1. `ADMIN_PORT`（运行期真实监听端口，桌面版由 `--backend <port>` 注入）
      2. `WB_OAUTH_CALLBACK_PORT`（显式覆盖）
      3. 最后才回落到 8790（源码直接跑、没设环境变量时）
    """
    for key in ("ADMIN_PORT", _PORT_ENV):
        value = (os.environ.get(key) or "").strip()
        if value.isdigit() and 1 <= int(value) <= 65535:
            return value
    return "8790"


#: 显式指定的「对外可达」回调基址（含协议与端口），优先级最高。
#: 部署在服务器 / 反代后面时设它，例如 https://2api.example.com
_PUBLIC_ENV = "WB_OAUTH_PUBLIC_BASE"


def public_base() -> str:
    """对外可达的回调基址；空串表示没有配置，退化为本机回环。"""
    return (os.environ.get(_PUBLIC_ENV) or "").strip().rstrip("/")


def main_host() -> str:
    """回调用的主机名。

    ## 三种部署形态

    · **桌面版**：浏览器和网关在同一台机器，`127.0.0.1` 最省事，也不会把
      授权入口暴露到局域网。
    · **服务器部署**：浏览器在用户自己电脑上，`127.0.0.1` 指的是**用户本机**
      而不是服务器 —— 回调必然打不开。这时必须配 `WB_OAUTH_PUBLIC_BASE`。
    · **反代 / 域名**：同上，填公网域名。

    判断「用户浏览器会不会和网关在同一台机器」不现实，所以这里只按配置走：
    配了公网基址就用它，没配就是本机。
    """
    return (os.environ.get(_HOST_ENV) or "").strip() or "127.0.0.1"


def callback_url(base: str = "") -> str:
    """授权页要用的回调地址 —— **上游强制要求本机回环，不能是域名**。

    ## 这是个死结，必须认清（2026-10-31 反复实测）

        callback = http://127.0.0.1:<port>/authorize  -> 授权页正常，能授权成功
        callback = https://你的域名/authorize          -> 授权页「登录失败 / 网络错误」

    依据：Trae 客户端源码里这个参数是**硬编码**的 ——

        const k = `http://127.0.0.1:${i}/authorize`;
        ... &auth_type=local&client_id=...&auth_callback_url=${k}&...

    `auth_type` 恒为 `local`，没有第二种取值；授权页全部 29 个 JS chunk 里
    也搜不到任何「允许域名回调」的分支。所以这不是配置问题，是上游设计：
    **它假设调用方是桌面客户端，回调用本机端口接。**

    ## 那服务器部署怎么完成授权

    回环地址在**用户自己电脑**上，服务器收不到 —— 所以「自动回调」
    在服务器部署下永远不可能成立。唯一可行路径是**手动提交回调链接**：

        1. 用户在自己浏览器完成授权
        2. 浏览器跳到 http://127.0.0.1:<port>/authorize?...&authCodeInfo=...
           （打不开是正常的，但**地址栏里那条 URL 带着完整授权码**）
        3. 把那条 URL 复制回控制台提交
        4. 服务端解析 AuthCode + 自己会话里的 verifier → 换 token

    实现见 `routers/accounts.py`：
      · `POST /api/trae/device/callback` —— 手动提交
      · `GET  /authorize`               —— 自动回调（桌面版走这条）

    `base` 参数保留但**不参与拼授权链接**，只用于展示提示。
    """
    return "http://%s:%s%s" % (main_host(), main_port(), CALLBACK_PATH)


def free_port() -> int:
    """找一个可用端口给回调用。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


class DeviceLogin:
    """一次 PKCE 授权会话。"""

    def __init__(self, realm: str = "cn", *, device_id: str = "",
                 machine_id: str = "", port: int = 0, callback: str = ""):
        self.realm = "intl" if str(realm).lower() == "intl" else "cn"
        #: 回调地址 —— 上游**只接受回环**，所以这里不采用传入值，
        #: 一律用 `callback_url()`（见它的注释：域名会被授权页直接拒）。
        #: 参数留着只为兼容调用方，不参与 `auth_url`。
        self.public_hint = str(callback or "").strip()
        # 客户端源码：randomBytes(48).toString("base64url")，48 字节而非 32
        self.verifier = _b64url(secrets.token_bytes(48))
        self.challenge = _b64url(hashlib.sha256(self.verifier.encode()).digest())
        self.nonce = str(__import__("uuid").uuid4())
        self.device_id = str(device_id or DEFAULT_DEVICE_ID)
        self.machine_id = str(machine_id or DEFAULT_MACHINE_ID)
        self.port = int(port or free_port())
        self.created_at = int(time.time())
        self.code = ""
        self.error = ""
        #: 回调里带的 userInfo（换 token 后若接口不回 UserID 可作为兜底）
        self.user_info: dict = {}
        #: 回调里带的 host（换 token 用这个域名最稳）
        self.host = ""
        self._done = threading.Event()
        self._server: threading.Thread | None = None

    def callback_url(self) -> str:
        """拼进授权链接的回调地址 —— 固定回环，上游强制。"""
        return callback_url()

    def display_callback(self) -> str:
        """给前端展示的回调地址：就是回环那个（不美化，避免误导用户）。"""
        return self.callback_url()

    # ---- URL ----
    @property
    def auth_url(self) -> str:
        params = {
            "login_version": "1",
            "auth_from": "trae",
            "login_channel": "native_ide",
            "plugin_version": plugin_version(),
            "auth_type": "local",
            "client_id": CLIENT_ID,
            "redirect": "0",
            # 回调走**主服务**（8790）的固定路由，不再单开端口：
            # 单开端口需要额外起线程、还要处理 favicon 抢先退出等问题，
            # 用主服务既省事又不会有端口被占的风险。
            # 回调地址**不要**带自定义 query：上游对 auth_callback_url
            # 做前缀比对，多一个 `?nonce=` 就可能不认。
            # 会话靠 `login_trace_id` 关联 —— 它会被上游原样带回到回调里。
            "auth_callback_url": self.callback_url(),
            "machine_id": self.machine_id,
            "device_id": self.device_id,
            "x_device_id": self.device_id,
            "x_machine_id": self.machine_id,
            # **必须是机型串**，不能给空。真实客户端日志里就是
            # `x_device_brand=NH5x_NH7x_HHx_HJx_HKx`（即 DeviceInfo.DeviceModel），
            # 之前这里给空串，和换 token 时上报的 DeviceModel 对不上 ——
            # 上游很可能把「授权时上报的指纹」和「换 token 时上报的 DeviceInfo」
            # 做一致性校验，不一致就回 10101。
            "x_device_brand": DEFAULT_BRAND,
            "x_device_type": "windows",
            "x_os_version": DEFAULT_OS,
            # 客户端在没有 BOE 环境时传 `undefined`（序列化后为空）
            "x_env": "",
            "x_app_version": app_version(),
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
            # 注意：这里给的是**展示用**地址（可能是公网域名），
            # 和 `auth_url` 里真正用的回环地址不是一回事 —— 见 `callback_url()`。
            "callback_url": self.display_callback(),
            "expires_in": _SESSION_TTL,
            "summary": "打开授权链接 → 选择账号登录 → 页面会自动回调并完成授权",
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

    # ---- 换 token ----
    def deliver(self, code: str = "", error: str = "", host: str = "") -> None:
        """主服务的回调路由收到 code 时调它。"""
        self.code = str(code or "")
        self.error = str(error or "")
        if host:
            self.host = str(host)
        self._done.set()

    def wait(self, timeout: float = 0.0) -> dict | None:
        """取回调结果；还没到就返回 None（**不要等待**，轮询接口自己控制节奏）。"""
        if timeout > 0:
            self._done.wait(timeout)
        if not self._done.is_set():
            return None
        return {"code": self.code, "verifier": self.verifier,
                "error": self.error, "realm": self.realm,
                "host": self.host, "user_info": self.user_info}


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
                 version: str = "") -> dict:
    """拼 `DeviceInfo` —— 逐字段对齐真实抓包。

    真实报文（`main.log` 的 `[exchangeTokenByAuthCode] request`）：

        {"DeviceID":"1637612576065600",
         "MachineID":"b54e128e...dcf3",
         "PlatformCode":"IDE_PC",
         "DeviceType":"PC",
         "DeviceName":"Administrator的电脑",
         "DeviceModel":"NH5x_NH7x_HHx_HJx_HKx",     ← 是机型串，不是空
         "ClientVersion":"3.4.11",
         "DevicePublicKey":"-----BEGIN PUBLIC KEY-----\\n...",
         "DeviceBrand":"HASEE Computer",             ← 真实品牌
         "DeviceCPU":"11th Gen Intel(R) Core(TM) i5-11260H",
         "OSInfo":"windows",                         ← **小写**
         "OSVersion":"Windows 11 Pro"}

    ## 三处曾经写错的地方

    · `DeviceModel` 原来给空串，真实值是品牌机型串 `NH5x_...`
    · `OSInfo` 原来给 `"Windows"`（大写 W），真实值是 `"windows"`
    · `DeviceBrand` / `DeviceCPU` 原来给空串，真实值是具体硬件

    这些看起来「无害」的空值，上游照样算进参数校验里 ——
    校验失败一律回 `10101 无效参数：{__Message.field}`（模板占位符
    没被填充），所以只能对着真实报文一比一还原，没法靠猜。

    取不到真实硬件信息时用 `DEFAULT_BRAND` / 一个常见 CPU 串兜底：
    宁可上报一个「普通机器」，也不要上报空值。
    """
    version = str(version or app_version())
    return {
        "DeviceID": str(device_id or DEFAULT_DEVICE_ID),
        "MachineID": str(machine_id or DEFAULT_MACHINE_ID),
        "PlatformCode": "IDE_PC",
        "DeviceType": "PC",
        "DeviceName": os.environ.get("COMPUTERNAME", "Windows-PC"),
        "DeviceModel": DEFAULT_BRAND,
        "ClientVersion": version,
        "DevicePublicKey": public_key_pem,
        "DeviceBrand": _detect_brand(),
        "DeviceCPU": _detect_cpu(),
        "OSInfo": "windows",
        "OSVersion": DEFAULT_OS,
    }


#: 取不到硬件信息时的兜底（都是真实客户端上报过的普通机器配置）
_FALLBACK_BRAND = "HASEE Computer"
_FALLBACK_CPU = "11th Gen Intel(R) Core(TM) i5-11260H"


def _detect_brand() -> str:
    """本机品牌（Windows 读注册表，失败用兜底）。"""
    if os.name != "nt":
        return _FALLBACK_BRAND
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"HARDWARE\DESCRIPTION\System\BIOS")
        try:
            value, _ = winreg.QueryValueEx(key, "SystemManufacturer")
            return str(value or "").strip() or _FALLBACK_BRAND
        finally:
            key.Close()
    except Exception:  # noqa: BLE001 - 探测失败不致命
        return _FALLBACK_BRAND


def _detect_cpu() -> str:
    """本机 CPU 型号（Windows 读注册表，失败用兜底）。"""
    if os.name != "nt":
        return _FALLBACK_CPU
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
        try:
            value, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            return str(value or "").strip() or _FALLBACK_CPU
        finally:
            key.Close()
    except Exception:  # noqa: BLE001
        return _FALLBACK_CPU


def exchange_token(code: str, verifier: str, *, realm: str = "cn",
                   device_id: str = "", machine_id: str = "",
                   host: str = "", timeout: float = 60.0) -> dict:
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

    ## 请求头要带 `x-cloudide-token: ""`

    客户端 `_doExchangeToken` 的 headers 来自 `getHeaders(t)`，它无条件返回
    `{{"Content-Type": "application/json", "x-cloudide-token": t}}`，
    `exchangeTokenByAuthCode` 传的是**空串**。少了这个头会掉进上游另一条
    校验分支，同样回 10101，看不出区别。

    ## 版本号必须和客户端一致

    `IDEVersion` / `DeviceInfo.ClientVersion` 用 `app_version()` 自动探测
    本机真实版本；写死旧号（原 `3.4.11`）会被当成过期客户端。
    """
    import urllib.error
    import urllib.request

    base = str(host or "").strip().rstrip("/")
    if not base:
        base = "https://api.trae.com.cn" if realm == "cn" else "https://api.trae.ai"

    version = app_version()
    _priv_pem, pub_pem = _device_keypair()
    payload = {
        "ClientID": CLIENT_ID,
        "AuthCode": str(code or ""),
        "CodeVerifier": str(verifier or ""),
        "DeviceInfo": _device_info(device_id, machine_id, pub_pem, version),
        "IDEVersion": version,
    }
    req = urllib.request.Request(
        base + EXCHANGE_PATH,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            # 客户端真实请求里就有这个空头（`getHeaders("")`），
            # 少了它会掉进上游的另一条校验分支，同样回 10101 看不出区别。
            "x-cloudide-token": "",
            "User-Agent": "Trae/%s" % version,
        })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read(1200).decode("utf-8", "replace")
        raise RuntimeError("ExchangeToken HTTP %s: %s" % (exc.code, detail[:800]))

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
    return _SESSIONS.get(str(nonce or ""))
