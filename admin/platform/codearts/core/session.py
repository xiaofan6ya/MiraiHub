"""CodeArts 本机登录态读取与令牌续期。

## 凭证到底存在哪（实测路径）

```
%APPDATA%/codearts/Local State                     ← os_crypt.encrypted_key（DPAPI）
%APPDATA%/codearts/secretStorage/<sha256(key)>.enc ← authSession:cn-north-4
```

`secretStorage` 是客户端自己的 `FileSecretStorageService`：
文件名是 **key 的 sha256**，内容是 **base64(Chromium OSCrypt 密文)**。
Electron 在 Windows 上走的是 Chromium 的 `v10` 格式，不是裸 DPAPI：

```
v10 | nonce(12) | AES-256-GCM 密文（含 16 字节 tag）
        ↑ AES key 来自 Local State 里被 DPAPI 保护的 os_crypt.encrypted_key
```

所以解密是两步：DPAPI 解出 AES key（`DPAPI` 前缀要去掉 5 字节），
再用 AES-GCM 解 `.enc`（`v10` 前缀要去掉 3 字节，剩下 12 字节 nonce）。

## 存的是什么

```json
{"session": {"accessKey","secretKey","securityToken","refreshToken",
             "expiresAt","userId","userName","domainId","clientId", ...},
 "loginContext": {"pkcePair": {"codeVerifier": "..."},
                  "dpopKeyPair": {"privateKey": "{JWK}", "publicKey": "{JWK}"}}}
```

`loginContext` 里那两样是**续期的钥匙**：华为云 STS 的刷新要求
`code_verifier` + `DPoP` 头（ES256 JWT，公钥内嵌在头里）。
它们都随登录态一起落盘，所以本网关可以**独立续期**，不必依赖客户端在跑。

## 三个必须自觉的点

1. 只读，不写客户端文件 —— 注入/篡改是另一个能力（workbuddy 才有），
   CodeArts 这里绝不回写，避免把用户客户端搞坏。
2. 客户端每小时会刷新并重写这份文件；我们每次用之前重新读一遍，
   拿到的永远是「当下有效」的那份。
3. 非 Windows 或路径不存在时，返回结构化错误而不是抛裸异常 ——
   `scan()` 要能区分「客户端没装」和「装了但没登录」。
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field

logger = logging.getLogger("admin.platform.codearts.session")

#: 客户端产品名（`app.getPath("userData")` 的目录名，实测全小写）
APP_DIR_NAME = "codearts"

#: 会话在 secretStorage 里的逻辑 key（区域后缀来自 product.json 的 auth.region）
SESSION_KEY = "authSession:cn-north-4"

#: STS 令牌端点（product.json 的 `auth.iamStsHost` + `/v1/oauth2/tokens`）
STS_TOKEN_URL = "https://sts.cn-north-4.myhuaweicloud.com/v1/oauth2/tokens"
#: OAuth 客户端 id（product.json 的 `auth.clientId`）
CLIENT_ID = "codearts"


class SessionError(Exception):
    """读不到 / 解不开本机登录态。`kind` 供上层区分展示文案。"""

    def __init__(self, message: str, *, kind: str = "unavailable",
                 detail: str = ""):
        super().__init__(message)
        self.message = message
        self.kind = kind
        self.detail = detail


# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------
def app_data_dir() -> str:
    """客户端 userData 目录（动态定位，可由 ``CODEARTS_USER_DATA_DIR`` 覆盖）。

    不再拼死 `%APPDATA%/codearts`：用户可能改了 userData 目录名、用便携版
    `--user-data-dir`，或系统根本不在 C 盘。解析规则见 `core/paths.py`。
    """
    from admin.platform.codearts.core import paths

    return paths.data_dir()


def secret_file(key: str = SESSION_KEY) -> str:
    return os.path.join(app_data_dir(), "secretStorage",
                        hashlib.sha256(key.encode("utf-8")).hexdigest() + ".enc")


def local_state_file() -> str:
    return os.path.join(app_data_dir(), "Local State")


def installed() -> bool:
    return os.path.isdir(app_data_dir())


# ---------------------------------------------------------------------------
# 解密
# ---------------------------------------------------------------------------
def _dpapi_unprotect(data: bytes) -> bytes:
    """Windows DPAPI 解密（CryptUnprotectData）。"""
    if sys.platform != "win32":
        raise SessionError("当前系统不支持 DPAPI 解密", kind="unsupported")
    import ctypes
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    buffer = ctypes.create_string_buffer(data, len(data))
    blob_in = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    blob_out = _Blob()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out))
    if not ok:
        raise SessionError("DPAPI 解密失败（换用户/换机器都会这样）",
                           kind="dpapi", detail=str(ctypes.GetLastError()))
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _aes_key() -> bytes:
    """从 `Local State` 取 Chromium 的 OSCrypt 主密钥（32 字节）。"""
    path = local_state_file()
    if not os.path.isfile(path):
        raise SessionError("找不到客户端的 Local State（客户端未登录或未装）",
                           kind="no_client", detail=path)
    try:
        state = json.loads(open(path, encoding="utf-8").read() or "{}")
    except (OSError, ValueError) as exc:
        raise SessionError("Local State 读取失败", kind="no_client",
                           detail=str(exc)) from exc
    encrypted = str((state.get("os_crypt") or {}).get("encrypted_key") or "")
    if not encrypted:
        raise SessionError("Local State 里没有 encrypted_key",
                           kind="no_client", detail=path)
    blob = base64.b64decode(encrypted)
    # Chromium 会在密文前加 5 字节 "DPAPI" 标记
    if blob[:5] == b"DPAPI":
        blob = blob[5:]
    key = _dpapi_unprotect(blob)
    if len(key) != 32:
        raise SessionError("OSCrypt 主密钥长度异常（%d）" % len(key),
                           kind="dpapi")
    return key


def decrypt_blob(raw: bytes, key: bytes) -> bytes:
    """解 Chromium OSCrypt 密文（`v10` + nonce + GCM）。"""
    if raw[:3] == b"v10":
        nonce, ciphertext = raw[3:15], raw[15:]
    else:
        # 老格式（DPAPI 直存）：极少见，但保留兜底
        return _dpapi_unprotect(raw)
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:  # pragma: no cover - 环境缺库
        raise SessionError("缺少 cryptography 依赖，无法解密登录态",
                           kind="unsupported", detail=str(exc)) from exc
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, None)
    except Exception as exc:  # noqa: BLE001 - 密钥不匹配/密文损坏
        raise SessionError("登录态解密失败（密钥与密文不匹配）",
                           kind="dpapi", detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------
@dataclass
class LocalSession:
    """本机登录态（已归一成网关内部字段名）。"""

    access_key: str = ""
    secret_key: str = ""
    security_token: str = ""
    refresh_token: str = ""
    expires_at: int = 0            # 毫秒时间戳
    uid: str = ""
    user_name: str = ""
    domain_id: str = ""
    client_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    region: str = "cn-north-4"
    #: 续期用：DPoP 私钥与 PKCE code_verifier（JWK 原始 JSON 字符串）
    dpop_private_key: str = ""
    dpop_public_key: str = ""
    pkce_code_verifier: str = ""
    raw: dict = field(default_factory=dict)

    def to_credential(self) -> dict:
        """转成落库的 `auth_json` 结构（**不含**客户端私有字段）。"""
        return {
            "realm": "cn",
            "region": self.region,
            "access_key": self.access_key,
            "secret_key": self.secret_key,
            "security_token": self.security_token,
            "refresh_token": self.refresh_token,
            "expires_at": int(self.expires_at or 0),
            "uid": self.uid,
            "user_name": self.user_name,
            "domain_id": self.domain_id,
            "client_id": self.client_id or CLIENT_ID,
            # 续期材料：没有它们账号过期就只能重新登录
            "dpop_private_key": self.dpop_private_key,
            "dpop_public_key": self.dpop_public_key,
            "pkce_code_verifier": self.pkce_code_verifier,
            "source": "local",
        }

    @property
    def expires_in(self) -> int:
        """距离过期还有多少秒（负数=已过期）。"""
        if not self.expires_at:
            return 0
        return int(self.expires_at / 1000 - time.time())


def load_session(key: str = SESSION_KEY) -> LocalSession:
    """读并解密本机登录态。失败一律抛 `SessionError`（带 kind）。"""
    if not installed():
        raise SessionError("本机没有 CodeArts 客户端数据目录", kind="no_client",
                           detail=app_data_dir())
    path = secret_file(key)
    if not os.path.isfile(path):
        raise SessionError("客户端没有已保存的登录态（可能未登录）",
                           kind="no_login", detail=path)
    try:
        raw = base64.b64decode(open(path, "rb").read())
    except OSError as exc:
        raise SessionError("登录态文件读取失败", kind="no_login",
                           detail=str(exc)) from exc
    plain = decrypt_blob(raw, _aes_key())
    try:
        payload = json.loads(plain.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise SessionError("登录态不是合法 JSON", kind="no_login",
                           detail=str(exc)) from exc

    session = payload.get("session") or {}
    context = payload.get("loginContext") or {}
    pkce = context.get("pkcePair") or {}
    dpop = context.get("dpopKeyPair") or {}
    if not session.get("accessKey") or not session.get("secretKey"):
        raise SessionError("登录态缺少 AK/SK（客户端需要重新登录）",
                           kind="no_login", detail="keys=%s" % list(session))

    return LocalSession(
        access_key=str(session.get("accessKey") or ""),
        secret_key=str(session.get("secretKey") or ""),
        security_token=str(session.get("securityToken") or ""),
        refresh_token=str(session.get("refreshToken") or ""),
        expires_at=int(session.get("expiresAt") or 0),
        uid=str(session.get("userId") or ""),
        user_name=str(session.get("userName") or ""),
        domain_id=str(session.get("domainId") or ""),
        client_id=str(session.get("clientId") or CLIENT_ID),
        created_at=str(session.get("createdAt") or ""),
        updated_at=str(session.get("updatedAt") or ""),
        dpop_private_key=str(dpop.get("privateKey") or ""),
        dpop_public_key=str(dpop.get("publicKey") or ""),
        pkce_code_verifier=str(pkce.get("codeVerifier") or ""),
        raw=session,
    )


# ---------------------------------------------------------------------------
# 续期（DPoP + PKCE）
# ---------------------------------------------------------------------------
def _b64u(data: bytes) -> bytes:
    return base64.urlsafe_b64encode(data).rstrip(b"=")


def _ec_private_key(jwk_text: str):
    """从 JWK（P-256）还原私钥对象。"""
    from cryptography.hazmat.primitives.asymmetric import ec

    jwk = json.loads(jwk_text)
    if jwk.get("kty") != "EC" or jwk.get("crv") != "P-256":
        raise SessionError("DPoP 私钥不是 P-256 EC", kind="refresh")
    raw = lambda name: int.from_bytes(  # noqa: E731 - 三行内更清楚
        base64.urlsafe_b64decode(jwk[name] + "=="), "big")
    public = ec.EllipticCurvePublicNumbers(raw("x"), raw("y"), ec.SECP256R1())
    return ec.EllipticCurvePrivateNumbers(raw("d"), public).private_key()


def dpop_proof(private_jwk: str, public_jwk: str, method: str, url: str, *,
               now: float | None = None) -> str:
    """生成 DPoP 头（ES256 JWT，公钥内嵌在 protected header）。

    ⚠️ JWS 的 ES256 签名是 **r||s 各 32 字节裸拼**，不是 DER ——
    直接把 `cryptography` 的返回值塞进去会得到「签名格式非法」。
    """
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    public = json.loads(public_jwk)
    header = {
        "alg": "ES256",
        "typ": "dpop+jwt",
        "jwk": {key: public[key] for key in ("kty", "crv", "x", "y")},
    }
    payload = {
        "htm": method.upper(),
        "htu": url,
        "iat": int(now if now is not None else time.time()),
        "jti": os.urandom(8).hex(),
    }
    signing_input = b".".join([
        _b64u(json.dumps(header, separators=(",", ":")).encode("utf-8")),
        _b64u(json.dumps(payload, separators=(",", ":")).encode("utf-8")),
    ])
    der = _ec_private_key(private_jwk).sign(signing_input, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return (signing_input + b"." + _b64u(signature)).decode("ascii")


def refresh_session(credential: dict) -> dict:
    """用 refresh_token 换一组新的 STS 临时凭证。

    返回**新的凭证字典**（与入参同结构，已替换 AK/SK/STS/refresh_token）。
    失败抛 `SessionError(kind="refresh_dead")` —— 这种只能重新登录。

    注意：刷新不需要签名，但**必须**带 DPoP + code_verifier。
    """
    import urllib.error
    import urllib.parse
    import urllib.request

    refresh_token = str(credential.get("refresh_token") or "")
    verifier = str(credential.get("pkce_code_verifier") or "")
    private = str(credential.get("dpop_private_key") or "")
    public = str(credential.get("dpop_public_key") or "")
    if not (refresh_token and verifier and private and public):
        raise SessionError(
            "缺少续期材料（refresh_token / code_verifier / DPoP 密钥）",
            kind="refresh_dead")

    body = urllib.parse.urlencode({
        "client_id": str(credential.get("client_id") or CLIENT_ID),
        "code_verifier": verifier,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }).encode("utf-8")
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "DPoP": dpop_proof(private, public, "POST", STS_TOKEN_URL),
    }
    request = urllib.request.Request(STS_TOKEN_URL, data=body, headers=headers,
                                     method="POST")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=40) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:  # noqa: BLE001
            pass
        raise SessionError("续期被上游拒绝（HTTP %d）" % exc.code,
                           kind="refresh_dead", detail=detail) from exc
    except Exception as exc:  # noqa: BLE001 - 网络
        raise SessionError("续期请求失败", kind="network",
                           detail=str(exc)[:200]) from exc

    # ⚠️ 字段名是 `access_key_id` / `secret_access_key` / `expiration`(ISO)，
    # 不是 `access_key` / `secret_key` / `expires_in` —— 照客户端
    # `handleCallback()` 的映射抄，猜错就是「续期成功但 AK 为空」。
    credentials = payload.get("credentials") or {}
    access_key = str(credentials.get("access_key_id")
                     or credentials.get("access_key") or "")
    secret_key = str(credentials.get("secret_access_key")
                     or credentials.get("secret_key") or "")
    security_token = str(credentials.get("security_token") or "")
    if not (access_key and secret_key):
        raise SessionError("续期响应缺少 AK/SK", kind="refresh_dead",
                           detail=json.dumps(payload, ensure_ascii=False)[:300])

    expires_at = _iso_to_ms(str(credentials.get("expiration") or ""))
    if not expires_at:
        # 兜底：有些环境只回 expires_in（秒）
        expires_in = int(payload.get("expires_in") or 0)
        expires_at = int((time.time() + expires_in) * 1000) if expires_in else 0
    updated = dict(credential)
    updated.update({
        "access_key": access_key,
        "secret_key": secret_key,
        "security_token": security_token,
        # 注意：上游可能返回新的 refresh_token（轮转），不回写就会在下次失效
        "refresh_token": str(payload.get("refresh_token") or refresh_token),
        "expires_at": expires_at,
    })
    return updated


def _iso_to_ms(value: str) -> int:
    """`2026-11-04T07:04:36.628Z` -> 毫秒时间戳（解析失败返回 0）。"""
    text = (value or "").strip()
    if not text:
        return 0
    try:
        from datetime import datetime

        return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return 0
