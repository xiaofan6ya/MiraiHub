"""Qoder COSY 请求签名。

## /algo/* 那一族为什么不能只发 Bearer

Qoder 的推理网关不认普通 Bearer 令牌，而是一套**自签名**鉴权：

    1. 组装身份 JSON（uid / security_oauth_token / name / aid / email）
    2. 生成一次性 AES-128 密钥，用它把身份 JSON 加密成 `info`
    3. 用客户端内置 RSA 公钥把该 AES 密钥加密成 `cosy_key`
    4. {version, requestId, info, cosyVersion, ideVersion} → base64 = payload
    5. MD5(payload \\n key \\n 时间戳 \\n 请求体 \\n 签名路径) = 签名
    6. Authorization: Bearer COSY.<payload>.<签名> + 一组 Cosy-* 头

## 三个必须照抄的细节（改了就会被判 Signature invalid）

* **签名路径去掉 `/algo` 前缀、不含查询串** —— 服务端按同一规则校验
* **请求体参与签名**，且是**编码后**的字节（带 `Encode=1` 时）
  所以顺序必须是「先 `encode_body` 再 `build_auth_headers`」
* **身份 JSON 的键序固定** —— 它被 AES 加密、再 base64 进 payload，
  整条字节串参与签名。键序变了密文就变（虽然上游按 JSON 语义读，
  但保持逐字节一致才能靠对拍证伪）

## 密码学说明

* AES-128-CBC 的 **key 同时当 IV** —— 这是上游的既有做法，不是这里的选择
* RSA 是 PKCS#1 v1.5，指数固定 65537，模数 1024 位
  PS 必须是**非零随机**字节：固定 PS 会让 RSA 退化成确定性加密，
  那是可被指纹化的特征
* 公钥是客户端内置的**公开材料**，只用于加密一次性 AES 密钥，不是机密
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import sys
import time
import uuid
from urllib.parse import urlparse

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# ---------------------------------------------------------------------------
# 常量（照抄客户端，改动会导致签名不匹配）
# ---------------------------------------------------------------------------
#: 推理网关 COSY 协议版本
GATEWAY_COSY_VERSION = "1.1.38"
#: 客户端类型（5 = CLI 形态）
CLIENT_TYPE = "5"
#: 数据策略：不同意用于训练
DATA_POLICY = "disagree"
#: 登录版本标识
LOGIN_VERSION = "v2"
#: COSY 头里的机器类型
MACHINE_TYPE = "5"
#: Cosy-Clientip 固定回环（上游不校验实际来源）
CLIENT_IP = "127.0.0.1"

#: 客户端内置 RSA 公钥模数（1024 位 hex，无前导零）。
#: 公开材料 —— 只用它加密一次性 AES 密钥。
RSA_MODULUS_HEX = (
    "c0f22307e5cd362e296bb04470f6de8fbf935ce24e8fcf511a0e2701329769c4"
    "a76e499bb938036a52af1eaf818cf79a2600620e3ce87e371d2ca6d85803606a"
    "1b3fa5e874643c9ed2db7e85673ef7227fca56e2e7c08f0927609bb896a9f24b"
    "e1782099a66016a5bfdc3f1ff756bfc9e88d7b5dc5be30bf45a0223a00ebcecf"
)
RSA_EXPONENT = 65537

#: 请求体编码用的自定义字母表（与标准 base64 逐字符对应）
CUSTOM_ALPHABET = b"_doRTgHZBKcGVjlvpC,@aFSx#DPuNJme&i*MzLOEn)sUrthbf%Y^w.(kIQyXqWA!"
STD_ALPHABET = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"


# ---------------------------------------------------------------------------
# 原语
# ---------------------------------------------------------------------------
def _aes128_cbc_b64(key16: str, plaintext: bytes) -> str:
    """AES-128-CBC + PKCS#7，**key 同时作为 IV**。"""
    if len(key16) != 16:
        raise ValueError("AES 密钥必须是 16 字符")
    key = key16.encode()
    pad = 16 - (len(plaintext) % 16)
    data = plaintext + bytes([pad]) * pad
    cipher = Cipher(algorithms.AES(key), modes.CBC(key))
    enc = cipher.encryptor()
    return base64.b64encode(enc.update(data) + enc.finalize()).decode()


def _rsa_encrypt_b64(plaintext: str) -> str:
    """RSA PKCS#1 v1.5 加密。

    用手写模幂而不是 cryptography 的 padding：指数固定 65537（约 17 次平方），
    比引入完整 rsa 依赖代价更低，也不依赖 padding 模式（本项目实测
    `cryptography` 没有可用的 `NoPadding` 用于原始模幂）。
    """
    modulus = int(RSA_MODULUS_HEX, 16)
    key_len = (modulus.bit_length() + 7) // 8
    message = plaintext.encode()
    if len(message) + 11 > key_len:
        raise ValueError("待加密内容过长")

    ps_len = key_len - 3 - len(message)
    ps = bytearray(secrets.token_bytes(ps_len))
    for i, byte in enumerate(ps):
        if byte == 0:
            ps[i] = 1                      # PKCS#1 要求 PS 全非零
    encoded = b"\x00\x02" + bytes(ps) + b"\x00" + message

    cipher = pow(int.from_bytes(encoded, "big"), RSA_EXPONENT, modulus)
    return base64.b64encode(cipher.to_bytes(key_len, "big")).decode()


def _json_text(value: str) -> str:
    """JSON 字符串形态（带引号与转义）。"""
    return json.dumps(value, ensure_ascii=False)


def signature_path(url: str) -> str:
    """参与签名的路径：**去掉 `/algo` 前缀、不含查询串**。

    这条规则写错的表现是 `{"code":"101","message":"Signature invalid"}`，
    从错误信息里看不出是路径的问题。
    """
    path = urlparse(url).path
    return path[len("/algo"):] if path.startswith("/algo") else path


def encode_body(payload: bytes) -> bytes:
    """请求体编码（上游要求 `Encode=1` 时）。

    三步都是纯字节变换，服务端按同一规则还原：

        1. 对 JSON 字节做标准 base64
        2. 把 base64 文本按「尾段 / 中段 / 首段」重排
           （各段长度 ⌊n/3⌋，余数留中段）
        3. 逐字符做字母表替换（`=` → `$`）

    **签名算在编码之后**，所以必须先调用本函数。
    """
    standard = base64.b64encode(payload)
    length = len(standard)
    third = length // 3

    table = bytearray(range(256))
    for index, ch in enumerate(STD_ALPHABET):
        table[ch] = CUSTOM_ALPHABET[index]
    table[ord("=")] = ord("$")

    out = bytearray()
    for i in range(length - third, length):        # 尾段
        out.append(table[standard[i]])
    for i in range(third, length - third):         # 中段
        out.append(table[standard[i]])
    for i in range(third):                         # 首段
        out.append(table[standard[i]])
    return bytes(out)


def machine_os() -> str:
    """机器操作系统标识（`{arch}_{platform}`）。"""
    arch = "aarch64" if sys.maxsize <= 2 ** 32 else "x86_64"
    platform = {"win32": "windows", "darwin": "darwin"}.get(sys.platform, "linux")
    return "%s_%s" % (arch, platform)


# ---------------------------------------------------------------------------
# 主函数
# ---------------------------------------------------------------------------
def build_auth_headers(url: str, *, uid: str, token: str, name: str = "",
                       email: str = "", machine_id: str = "",
                       body: bytes | None = None) -> dict:
    """构造一次 `/algo/*` 请求的全部 COSY 头。

    `body` 必须是**已编码**的请求体（`encode_body` 之后），因为签名覆盖它。
    GET 类请求传 `None`。
    """
    if not uid:
        raise ValueError("缺少 uid，无法签名")
    if not token:
        raise ValueError("缺少 token，无法签名")

    # 一次性 AES 密钥：16 字符
    aes_key = secrets.token_urlsafe(32)[:16]
    if len(aes_key) < 16:
        raise ValueError("无法生成签名密钥")

    # 键序固定：uid / security_oauth_token / name / aid / email
    user_info = ('{"uid":%s,"security_oauth_token":%s,"name":%s,"aid":"","email":%s}'
                 % (_json_text(uid), _json_text(token), _json_text(name),
                    _json_text(email)))
    info = _aes128_cbc_b64(aes_key, user_info.encode())
    cosy_key = _rsa_encrypt_b64(aes_key)

    request_id = str(uuid.uuid4())
    timestamp = str(int(time.time()))

    payload_src = ('{"version":"v1","requestId":"%s","info":"%s",'
                   '"cosyVersion":"%s","ideVersion":""}'
                   % (request_id, info, GATEWAY_COSY_VERSION))
    payload = base64.b64encode(payload_src.encode()).decode()

    sig_path = signature_path(url)
    body_bytes = body or b""

    md5 = hashlib.md5()
    md5.update(payload.encode())
    md5.update(b"\n")
    md5.update(cosy_key.encode())
    md5.update(b"\n")
    md5.update(timestamp.encode())
    md5.update(b"\n")
    md5.update(body_bytes)
    md5.update(b"\n")
    md5.update(sig_path.encode())
    signature = md5.hexdigest()

    return {
        "Authorization": "Bearer COSY.%s.%s" % (payload, signature),
        "Cosy-Key": cosy_key,
        "Cosy-User": uid,
        "Cosy-Date": timestamp,
        "Cosy-Version": GATEWAY_COSY_VERSION,
        "Cosy-Machineid": machine_id,
        "Cosy-Machinetoken": machine_id,
        "Cosy-Machinetype": MACHINE_TYPE,
        "Cosy-Machineos": machine_os(),
        "Cosy-Clienttype": CLIENT_TYPE,
        "Cosy-Clientip": CLIENT_IP,
        "Cosy-Bodyhash": hashlib.md5(body_bytes).hexdigest(),
        "Cosy-Bodylength": str(len(body_bytes)),
        "Cosy-Sigpath": sig_path,
        "Cosy-Data-Policy": DATA_POLICY,
        "Cosy-Organization-Id": "",
        "Cosy-Organization-Tags": "",
        "Login-Version": LOGIN_VERSION,
        "X-Request-Id": request_id,
    }
