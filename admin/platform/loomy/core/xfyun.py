"""讯飞账号服务（`account.xfinfr.com`）—— 登录 / 续期的**唯一入口**。

## 为什么需要这个模块

Loomy 的业务网关只认 `session`，而 session **没有 refresh 接口**
（`/session/refresh` 等 12 个候选路径实测全是 404）。想换一张新票，
只有一条路：**用手机号 + 密码重新登录**（`POST /login/account/byPwd`）。

参考实现（loomy2api）的「自动续期」就是这个：
后台线程每轮检查，session 剩余不足 N 天时用存好的密码重登一次。
本文档/模块复刻的就是这一层。

## 签名

账号服务用讯飞自己的 HMAC-SHA1 签名（不是 Bearer）：

    string_to_sign = "\n".join([
        METHOD, ESCAPED_PATH, ESCAPED_QUERY, Content-MD5,
        Content-Type, Date, Nonce, SignedHeaders, CanonicalizedHeaders,
    ])
    Authorization: "account <accessKeyId>:<base64(hmac-sha1)>"

`accessKeyId/Secret` 是**客户端自带**的公开常量（`resources/.env.prod`
里那段用内嵌口令 AES-GCM 加密的内容），不是谁的私钥。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from urllib.parse import quote

logger = logging.getLogger("admin.platform.loomy.xfyun")

#: 账号服务地址与客户端自带的签名常量（可通过环境变量覆盖）。
ACCOUNT_BASE = os.getenv("LOOMY_XFYUN_BASE_URL", "https://account.xfinfr.com")
ACCESS_KEY_ID = os.getenv("LOOMY_XFYUN_ACCESS_KEY_ID", "2thryby66wxi53sk")
ACCESS_KEY_SECRET = os.getenv("LOOMY_XFYUN_ACCESS_KEY_SECRET",
                              "zsak6eadrbawz683wf5r3m2snrwj868r")
APP_ID = os.getenv("LOOMY_XFYUN_APP_ID", "GM3LOOMY")

#: 客户端 `_makeBase()` 的固定字段。`devid` 是可变的身份位 ——
#: 新登录的号用独立 devid，已导入的号沿用客户端原值（`web`），
#: 免得「同一个号换设备」被上游当成异常。
DEFAULT_DEVID = "web"
DEVICE_UA = "Loomy|Desktop|Electron|macOS"
MODEL_ID = "Web"
CLIENT_VERSION = "1.0.0"

#: 登录时申请的 session 有效期（客户端固定 14 天，这里保持一致）。
SESSION_EXPIRE_SECONDS = 14 * 24 * 3600

#: 业务码
BIZ_OK = "000000"
AUTH_ERROR_CODES = frozenset({"020002", "100002"})


class XfyunError(Exception):
    """账号服务调用失败。`code` 是上游业务码（非 HTTP 状态）。"""

    def __init__(self, message: str, *, code: str = "", status: int = 0,
                 auth: bool = False):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status
        self.auth = auth


# ---------------------------------------------------------------------------
# 签名
# ---------------------------------------------------------------------------
def _escape(value: str) -> str:
    return (quote(str(value), safe="~")
            .replace("!", "%21").replace("'", "%27")
            .replace("(", "%28").replace(")", "%29").replace("*", "%2A"))


def _escaped_path(path: str) -> str:
    clean = path if path.startswith("/") else "/" + path
    if len(clean) > 1 and clean.endswith("/"):
        clean = clean[:-1]
    return "/".join(_escape(seg) if seg else "" for seg in clean.split("/"))


def sign_headers(method: str, path: str, body: str,
                 content_type: str = "application/json") -> dict:
    """生成一次请求的签名头（Date/Nonce 每次都要新）。"""
    date = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
    nonce = str(uuid.uuid4())
    md5 = (base64.b64encode(hashlib.md5(body.encode()).digest()).decode()
           if body else "")
    parts = [method.upper(), _escaped_path(path), "", md5, content_type,
             date, nonce, "", ""]
    digest = hmac.new(ACCESS_KEY_SECRET.encode(),
                      "\n".join(parts).encode(), hashlib.sha1).digest()
    headers = {
        "Authorization": "account %s:%s" % (
            ACCESS_KEY_ID, base64.b64encode(digest).decode()),
        "Date": date,
        "Nonce": nonce,
        "Content-Type": content_type,
    }
    if md5:
        headers["Content-MD5"] = md5
    return headers


def _base(devid: str = "") -> dict:
    return {
        "appid": APP_ID,
        "modelid": MODEL_ID,
        "version": CLIENT_VERSION,
        "devid": devid or DEFAULT_DEVID,
        "ua": DEVICE_UA,
        "traceid": uuid.uuid4().hex,
    }


def call(path: str, param: dict, *, devid: str = "", timeout: float = 20.0) -> dict:
    """POST 到账号服务，返回 `data` 段（业务成功时）。"""
    body = json.dumps({"base": _base(devid), "param": param},
                      ensure_ascii=False, separators=(",", ":"))
    request = urllib.request.Request(ACCOUNT_BASE + path, data=body.encode(),
                                     method="POST")
    for key, value in sign_headers("POST", path, body).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", "replace")
            status = resp.status
    except urllib.error.HTTPError as exc:
        try:
            text = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            text = ""
        raise XfyunError("账号服务 HTTP %s" % exc.code, status=exc.code,
                         code=str(exc.code)) from None
    except Exception as exc:  # noqa: BLE001
        raise XfyunError("账号服务网络失败：%s" % exc) from None

    try:
        payload = json.loads(text)
    except ValueError:
        raise XfyunError("账号服务返回非 JSON", status=status) from None
    if not isinstance(payload, dict):
        raise XfyunError("账号服务返回结构异常", status=status)
    code = str(payload.get("code") or "")
    if code and code != BIZ_OK:
        desc = str(payload.get("desc") or payload.get("message") or "上游业务错误")
        raise XfyunError(desc, code=code, status=status,
                         auth=code in AUTH_ERROR_CODES)
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# RSA（密码加密）—— 纯标准库实现，避免为一个登录引第三方依赖
# ---------------------------------------------------------------------------
def _der_read(buf: bytes, pos: int):
    tag = buf[pos]
    pos += 1
    length = buf[pos]
    pos += 1
    if length & 0x80:
        count = length & 0x7F
        if count == 0 or count > 4:
            raise ValueError("unsupported DER length")
        length = int.from_bytes(buf[pos:pos + count], "big")
        pos += count
    return tag, pos, length


def der_to_numbers(der: bytes):
    """从 DER SubjectPublicKeyInfo 取 `(n, e)`。"""
    tag, pos, _ = _der_read(der, 0)
    if tag != 0x30:
        raise ValueError("not a DER SEQUENCE")
    tag, pos, alg_len = _der_read(der, pos)
    if tag != 0x30:
        raise ValueError("missing AlgorithmIdentifier")
    pos += alg_len
    tag, body, _length = _der_read(der, pos)
    if tag != 0x03:
        raise ValueError("missing BIT STRING")
    if der[body] != 0x00:
        raise ValueError("unexpected unused bits")
    pos = body + 1
    tag, pos, _ = _der_read(der, pos)
    if tag != 0x30:
        raise ValueError("missing RSAPublicKey")
    values = []
    for _ in range(2):
        tag, body, length = _der_read(der, pos)
        if tag != 0x02:
            raise ValueError("expected INTEGER")
        values.append(int.from_bytes(der[body:body + length], "big"))
        pos = body + length
    return values[0], values[1]


def rsa_encrypt(pubkey_b64: str, plaintext: str) -> str:
    """RSA/PKCS#1 v1.5 加密（客户端就是这么传密码的）。"""
    der = base64.b64decode(pubkey_b64 + "=" * (-len(pubkey_b64) % 4))
    n, e = der_to_numbers(der)
    k = (n.bit_length() + 7) // 8
    message = plaintext.encode("utf-8")
    if len(message) > k - 11:
        raise ValueError("密码过长，超出 RSA 分组上限")
    ps_len = k - len(message) - 3
    while True:
        padding = os.urandom(ps_len)
        if 0 not in padding:
            break
    block = b"\x00\x02" + padding + b"\x00" + message
    cipher = pow(int.from_bytes(block, "big"), e, n)
    return base64.b64encode(cipher.to_bytes(k, "big")).decode()


# ---------------------------------------------------------------------------
# 登录
# ---------------------------------------------------------------------------
def get_public_key(devid: str = "") -> tuple[str, str]:
    """`(pukey, rcode)` —— 零副作用，适合当探活的第一跳。"""
    data = call("/login/account/getPuKey", {}, devid=devid)
    pukey = str(data.get("pukey") or "")
    rcode = str(data.get("rcode") or "")
    if not pukey:
        raise XfyunError("getPuKey 未返回公钥", code="NO_PUKEY")
    return pukey, rcode


def login_by_password(loginid: str, password: str, *,
                      devid: str = "") -> dict:
    """手机号 + 密码登录 → `{session, userid, phone}`。

    这是**唯一**能拿到新 session 的路径（没有 refresh token）。
    """
    pukey, rcode = get_public_key(devid)
    encrypted = rsa_encrypt(pukey, password)
    data = call("/login/account/byPwd", {
        "loginid": str(loginid or "").strip(),
        "password": encrypted,
        "rcode": rcode,
        "type": 1,
        "expire": SESSION_EXPIRE_SECONDS,
    }, devid=devid)
    session = str(data.get("session") or "")
    if not session:
        raise XfyunError("登录成功但没有返回 session", code="NO_SESSION")
    return {
        "session": session,
        "userid": str(data.get("userid") or data.get("uid") or ""),
        "phone": str(data.get("phone") or ""),
    }


def query_base_info(session: str, *, devid: str = "") -> dict:
    """查登录态（客户端用它确认 session 是否还有效）。"""
    return call("/userinfo/query/baseInfo", {"session": session}, devid=devid)


def send_sms_code(phone: str, *, scene: str = "login", devid: str = "") -> dict:
    """下发登录/注册短信验证码。返回里带 `msgid`（下一步要回传）。"""
    return call("/login/phone/sendMsgCode", {
        "ccode": "86",
        "phone": str(phone or "").strip(),
        "expire": 300,
    }, devid=devid)


def login_by_sms(phone: str, code: str, msgid: str, *,
                 devid: str = "") -> dict:
    """短信验证码登录 → `{session, userid, phone}`。

    给「还能收到码、但没设密码」的号用：每 14 天重登一次即可。
    能设密码的话优先走 `register_by_phone`（一次搞定，永久续期）。
    """
    data = call("/login/phone/checkCode", {
        "ccode": "86",
        "phone": str(phone or "").strip(),
        "mcode": str(code or "").strip(),
        "msgid": str(msgid or "").strip(),
        "expire": SESSION_EXPIRE_SECONDS,
    }, devid=devid)
    session = str(data.get("session") or "")
    if not session:
        raise XfyunError("登录成功但没有返回 session", code="NO_SESSION")
    return {
        "session": session,
        "userid": str(data.get("userid") or data.get("uid") or ""),
        "phone": str(data.get("phone") or phone or ""),
    }


def register_by_phone(phone: str, code: str, password: str = "",
                      *, channel: str = "", devid: str = "") -> dict:
    """手机号注册 —— **顺手把密码设上**（这是接码号唯一能长期活着的办法）。

    ## 为什么这条路径值得单独暴露

    已注册的号**没有**任何「补设密码」接口（账号服务只有 17 个端点，
    其中没有 setPwd/modifyPwd）。因此短信注册那一刻就是**唯一**能给账号
    设密码的时机。

    接码号收不到第二次短信、也没有密码 —— 不在这里设密码的话，这张 14 天的
    session 到期就是彻底报废，不可能续。反过来：**注册时带上 password，
    之后就能一直用密码续期，永不过期**。

    返回 `{session, userid, phone}`，调用方直接导入号池并保存密码即可。
    """
    param = {
        "phone": str(phone or "").strip(),
        "code": str(code or "").strip(),
    }
    if password:
        param["password"] = str(password)
    if channel:
        param["channel"] = str(channel)
    data = call("/register/phone/submit", param, devid=devid)
    session = str(data.get("session") or "")
    if not session:
        raise XfyunError("注册成功但没有返回 session", code="NO_SESSION")
    return {
        "session": session,
        "userid": str(data.get("userid") or data.get("uid") or ""),
        "phone": str(data.get("phone") or phone or ""),
    }


def new_device_id() -> str:
    """给**新登录**的号生成独立设备标识。

    为什么必须每号一个：同一个 `devid` 上登录 N 个账号，是最明显的
    「同一设备多开」特征。已导入的号沿用客户端原值（`web`），
    因为它们的 session 就是用那个 devid 签发的，改反而像异常。
    """
    return "web-%s" % uuid.uuid4().hex[:16]