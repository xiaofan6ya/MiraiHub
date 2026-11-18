"""Marvis uskey 的**纯标准库**本地实现（无侧车、无第三方包）。

## 它是什么

上游 POST /v1/chat/completions 的 Ual-Access-MarvisExt.uskey 是一块
device-derived 密文。2026-11-18 把整条流水线逆出来了，这里是逐字节复现版：

    uskey    = base64_A( header24 || AES-128-CBC(K_AES, IV=K_AES, P224, PKCS7) )
    header24 = 030057040111 || dev18(18B)
    P224     = TEA_dualfeedback(stream, K_TEA) ^ (C_CONST ^ dev18[2:18])   # 16B 循环
    stream   = rand8(padflag=6) || X1 || TLV(208) || bytes(7)

base64_A 用的是自定义 64 项字母表（不是标准 base64 顺序）。

## 验证状态（2026-11-18）

* 4/4 条真实 uskey 逐字节 re-encode 相等（与 uskey_mint.py selftest 同口径）。
* 纯 Python mint 的 uskey 直接打上游 HTTP 200（真实账号 190）。
* 全随机 dev18（os.urandom(18)）同样 200 ⇒ 不依赖侧车进程的任何状态。

## 与侧车的关系

本模块**不需要**侧车。dev18 每次随机；qimei36 是账号侧的设备串
（签名时必须由调用方显式传入，见 core/uskey.py 的 qimei36()）。
core/uskey.py 只在本地 mint 抛异常时才回落侧车。

## 依赖

只用 base64 / hashlib / os / struct / time —— AES-128 与 TEA 都是本文件内的
纯 Python 实现（为的是镜像里不需要 cryptography）。
"""
from __future__ import annotations

import base64
import hashlib
import os
import struct
import time

# ---------------------------------------------------------------------------
# 三层 key
# ---------------------------------------------------------------------------
K_AES = bytes.fromhex("b3641ea93bf54cf4338bcd6c20121641")
K_TEA = bytes.fromhex("b3641ea93bf54cf4338bcd6c20164178")
C_CONST = bytes.fromhex("b3641e533bf54534358bcd6520111645")

#: 自定义 base64 字母表（uskey 外层编码）
ALPHA = "CDABEFIJHGKLOPMNQRSTWXUVYZabecdfighlmjpqknouyzvrstwx0312546978+/"
STD = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
#: uskey 字符 -> 标准 base64 字符（解码用）
INV = {ALPHA[i]: STD[i] for i in range(64)}
#: 标准 base64 字符 -> uskey 字符（编码用）
REV = {STD[i]: ALPHA[i] for i in range(64)}

# ---------------------------------------------------------------------------
# TEA（16 轮 / delta 0x9e3779b9 / key 按大端拆 4 个 dword）
# ---------------------------------------------------------------------------
DELTA = 0x9E3779B9
_M32 = 0xFFFFFFFF


def _tea_encrypt_block(w0: int, w1: int, k) -> tuple:
    s = DELTA
    for _ in range(16):
        w0 = (w0 + ((((w1 << 4) & _M32) + k[0]) ^ ((w1 + s) & _M32)
                    ^ (((w1 >> 5) + k[1]) & _M32))) & _M32
        w1 = (w1 + ((((w0 << 4) & _M32) + k[2]) ^ ((w0 + s) & _M32)
                    ^ (((w0 >> 5) + k[3]) & _M32))) & _M32
        s = (s + DELTA) & _M32
    return w0, w1


def _tea_decrypt_block(w0: int, w1: int, k) -> tuple:
    s = (DELTA * 16) & _M32
    for _ in range(16):
        w1 = (w1 - ((((w0 << 4) & _M32) + k[2]) ^ ((w0 + s) & _M32)
                    ^ (((w0 >> 5) + k[3]) & _M32))) & _M32
        w0 = (w0 - ((((w1 << 4) & _M32) + k[0]) ^ ((w1 + s) & _M32)
                    ^ (((w1 >> 5) + k[1]) & _M32))) & _M32
        s = (s - DELTA) & _M32
    return w0, w1


def _tea_block(block8: bytes, key: bytes, decrypt: bool = False) -> bytes:
    k = struct.unpack(">4I", key)
    w0 = int.from_bytes(block8[0:4], "big")
    w1 = int.from_bytes(block8[4:8], "big")
    if decrypt:
        w0, w1 = _tea_decrypt_block(w0, w1, k)
    else:
        w0, w1 = _tea_encrypt_block(w0, w1, k)
    return w0.to_bytes(4, "big") + w1.to_bytes(4, "big")


def _xor(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b))


def tea_dualfeedback(data: bytes, key: bytes = K_TEA) -> bytes:
    """**双反馈** TEA（不是标准 CBC，也不是 PCBC）：

        S_i = M_i ^ C_{i-1}        (C_{-1} = 0)
        C_i = TEA(S_i) ^ S_{i-1}   (S_{-1} = 0)

    两个反馈同时存在 —— 这正是最初用 CBC / PCBC 都拟合不上的原因。
    """
    if len(data) % 8:
        raise ValueError("tea_dualfeedback 要求 8 字节对齐：%d" % len(data))
    out = bytearray()
    s_prev = bytes(8)
    c_prev = bytes(8)
    for i in range(0, len(data), 8):
        s = _xor(data[i:i + 8], c_prev)
        c = _xor(_tea_block(s, key), s_prev)
        out += c
        s_prev, c_prev = s, c
    return bytes(out)


def tea_dualfeedback_decrypt(data: bytes, key: bytes = K_TEA) -> bytes:
    """tea_dualfeedback 的逆（解码 / 自检用）。"""
    if len(data) % 8:
        raise ValueError("tea_dualfeedback_decrypt 要求 8 字节对齐：%d" % len(data))
    out = bytearray()
    s_prev = bytes(8)
    c_prev = bytes(8)
    for i in range(0, len(data), 8):
        c = data[i:i + 8]
        s = _tea_block(_xor(c, s_prev), key, decrypt=True)
        out += _xor(s, c_prev)
        s_prev, c_prev = s, c
    return bytes(out)

# ---------------------------------------------------------------------------
# AES-128（纯 Python；S-box 由 GF(2^8) 求逆 + 仿射变换现算，避免手抄 256 项出错）
# ---------------------------------------------------------------------------


def _rotl8(x: int, n: int) -> int:
    return ((x << n) | (x >> (8 - n))) & 0xFF


def _gen_sbox() -> bytes:
    box = [0] * 256
    p = q = 1
    while True:
        p = (p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)) & 0xFF
        q ^= q << 1
        q ^= q << 2
        q ^= q << 4
        q &= 0xFF
        if q & 0x80:
            q ^= 0x09
        box[p] = (q ^ _rotl8(q, 1) ^ _rotl8(q, 2) ^ _rotl8(q, 3)
                  ^ _rotl8(q, 4) ^ 0x63) & 0xFF
        if p == 1:
            break
    box[0] = 0x63
    return bytes(box)


SBOX = _gen_sbox()
_INV = [0] * 256
for _i, _v in enumerate(SBOX):
    _INV[_v] = _i
INV_SBOX = bytes(_INV)


def _xt(a: int) -> int:
    return ((a << 1) ^ (0x1B if a & 0x80 else 0)) & 0xFF


def _gmul(a: int, b: int) -> int:
    r = 0
    while b:
        if b & 1:
            r ^= a
        a = _xt(a)
        b >>= 1
    return r


_MUL2 = bytes(_gmul(i, 2) for i in range(256))
_MUL3 = bytes(_gmul(i, 3) for i in range(256))
_MUL9 = bytes(_gmul(i, 9) for i in range(256))
_MUL11 = bytes(_gmul(i, 11) for i in range(256))
_MUL13 = bytes(_gmul(i, 13) for i in range(256))
_MUL14 = bytes(_gmul(i, 14) for i in range(256))


def _expand_key(key: bytes):
    if len(key) != 16:
        raise ValueError("只支持 AES-128（16 字节 key），收到 %d" % len(key))
    w = list(key)
    rcon = 1
    for i in range(16, 176, 4):
        t = w[i - 4:i]
        if i % 16 == 0:
            t = [SBOX[t[1]] ^ rcon, SBOX[t[2]], SBOX[t[3]], SBOX[t[0]]]
            rcon = _xt(rcon)
        for j in range(4):
            w.append(w[i - 16 + j] ^ t[j])
    return w


def _shift_rows(s):
    for r in (1, 2, 3):
        row = [s[4 * c + r] for c in range(4)]
        row = row[r:] + row[:r]
        for c in range(4):
            s[4 * c + r] = row[c]


def _inv_shift_rows(s):
    for r in (1, 2, 3):
        row = [s[4 * c + r] for c in range(4)]
        row = row[-r:] + row[:-r]
        for c in range(4):
            s[4 * c + r] = row[c]


def _mix_columns(s):
    for c in range(4):
        a0, a1, a2, a3 = s[4 * c:4 * c + 4]
        s[4 * c + 0] = _MUL2[a0] ^ _MUL3[a1] ^ a2 ^ a3
        s[4 * c + 1] = a0 ^ _MUL2[a1] ^ _MUL3[a2] ^ a3
        s[4 * c + 2] = a0 ^ a1 ^ _MUL2[a2] ^ _MUL3[a3]
        s[4 * c + 3] = _MUL3[a0] ^ a1 ^ a2 ^ _MUL2[a3]


def _inv_mix_columns(s):
    for c in range(4):
        a0, a1, a2, a3 = s[4 * c:4 * c + 4]
        s[4 * c + 0] = _MUL14[a0] ^ _MUL11[a1] ^ _MUL13[a2] ^ _MUL9[a3]
        s[4 * c + 1] = _MUL9[a0] ^ _MUL14[a1] ^ _MUL11[a2] ^ _MUL13[a3]
        s[4 * c + 2] = _MUL13[a0] ^ _MUL9[a1] ^ _MUL14[a2] ^ _MUL11[a3]
        s[4 * c + 3] = _MUL11[a0] ^ _MUL13[a1] ^ _MUL9[a2] ^ _MUL14[a3]


def _add_round_key(s, w, rnd: int):
    off = 16 * rnd
    for i in range(16):
        s[i] ^= w[off + i]


def aes128_encrypt_block(block: bytes, w) -> bytes:
    s = list(block)
    _add_round_key(s, w, 0)
    for rnd in range(1, 10):
        for i in range(16):
            s[i] = SBOX[s[i]]
        _shift_rows(s)
        _mix_columns(s)
        _add_round_key(s, w, rnd)
    for i in range(16):
        s[i] = SBOX[s[i]]
    _shift_rows(s)
    _add_round_key(s, w, 10)
    return bytes(s)


def aes128_decrypt_block(block: bytes, w) -> bytes:
    s = list(block)
    _add_round_key(s, w, 10)
    for rnd in range(9, 0, -1):
        _inv_shift_rows(s)
        for i in range(16):
            s[i] = INV_SBOX[s[i]]
        _add_round_key(s, w, rnd)
        _inv_mix_columns(s)
    _inv_shift_rows(s)
    for i in range(16):
        s[i] = INV_SBOX[s[i]]
    _add_round_key(s, w, 0)
    return bytes(s)


def _pkcs7_pad(data: bytes) -> bytes:
    pad = 16 - (len(data) % 16)
    return data + bytes([pad]) * pad


def _pkcs7_unpad(data: bytes) -> bytes:
    if not data or len(data) % 16:
        raise ValueError("密文长度不是 16 的倍数：%d" % len(data))
    pad = data[-1]
    if not 1 <= pad <= 16 or data[-pad:] != bytes([pad]) * pad:
        raise ValueError("PKCS#7 填充不合法")
    return data[:-pad]


def aes128_cbc_encrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    """AES-128-CBC + PKCS#7（uskey 的第三层）。"""
    w = _expand_key(key)
    padded = _pkcs7_pad(data)
    prev = iv
    out = bytearray()
    for i in range(0, len(padded), 16):
        prev = aes128_encrypt_block(_xor(padded[i:i + 16], prev), w)
        out += prev
    return bytes(out)


def aes128_cbc_decrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    """aes128_cbc_encrypt 的逆（解码 / 自检用）。"""
    if len(data) % 16:
        raise ValueError("密文长度不是 16 的倍数：%d" % len(data))
    w = _expand_key(key)
    prev = iv
    out = bytearray()
    for i in range(0, len(data), 16):
        blk = data[i:i + 16]
        out += _xor(aes128_decrypt_block(blk, w), prev)
        prev = blk
    return _pkcs7_unpad(bytes(out))

# ---------------------------------------------------------------------------
# TLV（320 字节定长模板）—— 2026-11-18 按真客户端 504 字符 uskey 反解逐字节重建
# ---------------------------------------------------------------------------
#: 段布局（偏移都相对 TLV 起点；多数段是 u16-BE 长度 + 值，少数段前还有一段
#: 0000 空标记 —— 那是真品的原样形态，别"清理"掉）：
#:
#:     0:2      013e                = 320 - 2（剩余长度；模板长度变了必须重算）
#:     2:6      0000 0459           SDK 常量
#:     6:24     0010 + 16B          busid "0WIN06Y06DKV1IUQ"
#:     24:36    000a + 10B          SDK 版本 "1.60.34.10"
#:     36:45    0007 + 7B           "7800255"
#:     45:65    0012 + 18B          qimei36 #1
#:     65:83    0010 + 16B          md5(bus_info)（真品实测吻合）
#:     83:87    0000 0003           常量
#:     87:95    8B BE               epoch 毫秒
#:     95:112   000f + 15B          BCD(epoch 秒,5B) + 10B 随机
#:     112:122  0008 + 8B           随机 8B
#:     122:131  0007 + 7B           "5.1.4.2"
#:     131:150  0000 + 000f + 15B   "MarvisAgent.exe"
#:     150:170  0012 + 18B          qimei36 #2（与 #1 同值）
#:     170:183  0000 + 0009 + 9B    "TX Gaming"
#:     183:189  0000 0002           常量
#:     189:302  0071 + 113B         周计数 JSON
#:     302:320  0010 + 16B          md5("")
TLV_TEMPLATE = bytes.fromhex(
    "013e0000045900103057494e3036593036444b5631495551000a312e36302e"
    "33342e313000073738303032353500120123456789abcdef012345673000de"
    "adbeef001050ff06148d833a5e72813bad5ee9ba8200000003000001a1e9f0"
    "847c000f179492450109739ef1cc1bc86f31d0000867a9ff9fb81ae1170007"
    "352e312e342e320000000f4d61727669734167656e742e6578650012012345"
    "6789abcdef012345673000deadbeef0000000954582047616d696e67000000"
    "0200717b2231223a31392c223130223a302c223131223a302c223132223a30"
    "2c223133223a302c223134223a22222c223135223a302c2232223a31373934"
    "3932343530312c2233223a332c2234223a312c2235223a302c2236223a302c"
    "2237223a302c2238223a224d61727669732e657865227d0010d41d8cd98f00"
    "b204e9800998ecf8427e"
)

#: 长度常量（真品实测：TLV 320 / 载荷 336 / 整块 376 字节 = 504 字符）
TLV_BYTES = 320
PAYLOAD_BYTES = 336
USKEY_BYTES = 376
USKEY_CHARS = 504

#: 字段偏移（**按 320 字节真品模板**校准；2026-11-18 之前的 44/64/85/94/111 那组
#: 是按旧 208 字节模板量的，SDK 版本串从 "1.0.0.0"(7) 变成 "1.60.34.10"(10)
#: 之后整体后移 3 字节，毫秒那处还多错 2 字节 —— 那组数会写歪 TLV，别再用）。
OFF_LEN = 0        # u16 BE = len(TLV) - 2
OFF_BUSID = 8      # 16B
OFF_SDK = 26       # 10B "1.60.34.10"
OFF_APPID = 38     # 7B "7800255"
OFF_QIMEI1 = 47    # 18B
OFF_MD5 = 67       # 16B md5(bus_info)
OFF_MS = 87        # 8B BE epoch 毫秒
OFF_BLOB = 97      # 15B = BCD(秒,5) + 10B 随机
OFF_RND = 114      # 8B 随机
OFF_VER2 = 124     # 7B "5.1.4.2"
OFF_PROC = 135     # 15B "MarvisAgent.exe"
OFF_QIMEI2 = 152   # 18B
OFF_PRODUCT = 174  # 9B "TX Gaming"
OFF_JSON = 189     # 113B 周计数 JSON
JSON_BYTES = 113

#: 身份常量（真品原值；我们历史上的 1.0.0.0 / node.exe / Not Specified 是仿的）
BUSID = "0WIN06Y06DKV1IUQ"
SDK_VERSION = "1.60.34.10"
APP_ID = "7800255"
ENGINE_VERSION = "5.1.4.2"
PROC_NAME = "MarvisAgent.exe"
PRODUCT_NAME = "TX Gaming"
WEEK_EXE = "Marvis.exe"

#: 设备串是 mint_uskey() 的参数，不要改模板。
#: 仅用于 KAT 自检的合成夹具（36 hex = 18 字节），不是任何真实设备。
KAT_FIXTURE_QIMEI36 = "0123456789abcdef012345673000deadbeef"  # 合成夹具，非任何真实设备

#: 载荷第 8 字节（TLV 前的那个 flag）。真品是 0x5a，不是随机数。
STREAM_FLAG = 0x5A


def bcd_seconds(sec: int) -> bytes:
    """10 位十进制秒数 -> 5 字节 BCD（真品 blob 的前 5 字节就是这么来的）。"""
    s = "%010d" % int(sec)
    if len(s) != 10 or not s.isdigit():
        raise ValueError("秒数必须能写成 10 位十进制：%r" % (sec,))
    return bytes(((int(s[i]) << 4) | int(s[i + 1])) for i in range(0, 10, 2))


def build_week_json(sec: int, count: int = 19, exe: str = WEEK_EXE) -> bytes:
    """113 字节定长周计数 JSON（真品结构逐键照抄，只动 1 号和 2 号）。

    长度必须恒定在 113 —— 它是 TLV 定长模板里的一段，长度一变整块错位。
    """
    text = ('{"1":%02d,"10":0,"11":0,"12":0,"13":0,"14":"","15":0,'
            '"2":%d,"3":3,"4":1,"5":0,"6":0,"7":0,"8":"%s"}' % (count, int(sec), exe))
    out = text.encode("utf-8")
    if len(out) != JSON_BYTES:
        raise ValueError("周计数 JSON 必须 %d 字节，实际 %d：%r"
                         % (JSON_BYTES, len(out), text))
    return out


def build_tlv(bus_info: str, qimei36: str, now_ms: int = None,
              rnd8: bytes = None, blob: bytes = None,
              week_count: int = 19, anchor_sec: int = None) -> bytes:
    """拼 320 字节 TLV。

    变量只有 5 处：md5(bus_info)、两处 qimei36、8 字节毫秒、15 字节 blob
    （= BCD(秒) + 10 随机）、8 字节随机、以及 JSON 里的 1/2 号。
    """
    qb = bytes.fromhex(qimei36)
    if len(qb) != 18:
        raise ValueError("qimei36 必须是 36 个 hex 字符（18 字节）：%r" % qimei36)
    ts = int(time.time() * 1000) if now_ms is None else int(now_ms)
    #: 真品里 JSON 的 "2" 和 blob 的 BCD 秒是**同一个**锚点（= 令牌/session 出生时间），
    #: 与毫秒字段不是同一个源（抓包里差 1715 s）。默认都取当前秒，重放时单独指定。
    sec = ts // 1000 if anchor_sec is None else int(anchor_sec)
    tlv = bytearray(TLV_TEMPLATE)
    tlv[OFF_LEN:OFF_LEN + 2] = (TLV_BYTES - 2).to_bytes(2, "big")
    tlv[OFF_BUSID:OFF_BUSID + 16] = BUSID.encode("ascii")
    tlv[OFF_QIMEI1:OFF_QIMEI1 + 18] = qb
    tlv[OFF_QIMEI2:OFF_QIMEI2 + 18] = qb
    tlv[OFF_MD5:OFF_MD5 + 16] = hashlib.md5(str(bus_info).encode("utf-8")).digest()
    tlv[OFF_MS:OFF_MS + 8] = ts.to_bytes(8, "big")
    tlv[OFF_BLOB:OFF_BLOB + 15] = (blob if blob is not None
                                   else bcd_seconds(sec) + os.urandom(10))
    tlv[OFF_RND:OFF_RND + 8] = rnd8 if rnd8 is not None else os.urandom(8)
    tlv[OFF_JSON:OFF_JSON + JSON_BYTES] = build_week_json(sec, week_count)
    # qimei36 必须三处一致：header 的 Ual-Access-MarvisExt + TLV 两处。
    # 任意一处不同，上游会判定「设备串与签名不匹配」并拒掉这条请求。
    assert tlv[OFF_QIMEI1:OFF_QIMEI1 + 18] == qb
    assert tlv[OFF_QIMEI2:OFF_QIMEI2 + 18] == qb
    return bytes(tlv)


def build_stream(tlv: bytes, head8: bytes = None, flag: int = None) -> bytes:
    """336 字节载荷：8B 头（低 3 位打 pad 标记 6）+ 1B flag + TLV(320) + 7B。"""
    if len(tlv) != TLV_BYTES:
        raise ValueError("TLV 必须 %d 字节，收到 %d" % (TLV_BYTES, len(tlv)))
    head = bytearray(head8 if head8 is not None else os.urandom(8))
    head[0] = (head[0] & 0xF8) | 0x06
    return bytes(head) + bytes([STREAM_FLAG if flag is None else int(flag)]) + tlv + bytes(7)


def build_payload(stream: bytes, dev18: bytes) -> bytes:
    """第二层密文：TEA 双反馈再按 (C_CONST ^ dev18[2:18]) 循环异或。"""
    if len(dev18) != 18:
        raise ValueError("dev18 必须是 18 字节，收到 %d" % len(dev18))
    raw = tea_dualfeedback(stream)
    ks = _xor(dev18[2:16 + 2], C_CONST)
    return bytes(x ^ ks[i % 16] for i, x in enumerate(raw))


#: 旧名保留（长度无关，只是历史叫法沿用到 336 字节载荷上）
build_p224 = build_payload


def b64_custom_encode(raw: bytes) -> str:
    """标准 base64 再按字母表 A 换字（'=' 原样保留）。"""
    std = base64.b64encode(raw).decode("ascii")
    return "".join(REV.get(c, c) for c in std)


def b64_custom_decode(text: str) -> bytes:
    """字母表 A -> 标准 base64 -> 原始字节（补回被丢掉的 '=' 填充）。"""
    s = "".join(INV[c] for c in text if c in INV)
    s += "=" * (-len(s) % 4)
    return base64.b64decode(s)


def mint_uskey(qimei36: str, bus_info: str, dev18: bytes = None,
               now_ms: int = None, rnd8: bytes = None,
               blob: bytes = None, head8: bytes = None,
               flag: int = None, week_count: int = 19,
               anchor_sec: int = None) -> str:
    """造一块 uskey。dev18 为 None 时随机；其余参数只为自检/重放留口。

    末尾断言产出必须是真品口径（376 字节 / 504 字符）—— 短一截的包上游直接 403，
    与其拿去污染号池，不如在这里当场炸掉。
    """
    if dev18 is None:
        dev18 = os.urandom(18)
    if len(dev18) != 18:
        raise ValueError("dev18 必须是 18 字节，收到 %d" % len(dev18))
    tlv = build_tlv(bus_info, qimei36, now_ms=now_ms, rnd8=rnd8, blob=blob,
                    week_count=week_count, anchor_sec=anchor_sec)
    stream = build_stream(tlv, head8=head8, flag=flag)
    payload = build_payload(stream, dev18)
    header = bytes.fromhex("030057040111") + dev18
    out = b64_custom_encode(header + aes128_cbc_encrypt(K_AES, K_AES, payload))
    raw = b64_custom_decode(out)
    assert len(raw) == USKEY_BYTES, "uskey 必须 %d 字节，实际 %d" % (USKEY_BYTES, len(raw))
    assert len(out) == USKEY_CHARS, "uskey 必须 %d 字符，实际 %d" % (USKEY_CHARS, len(out))
    return out


def decode_uskey(uskey: str) -> dict:
    """uskey -> 各层中间值（排障 / 自检用，密钥与本模块同源）。"""
    raw = b64_custom_decode(uskey)
    header, body = raw[:24], raw[24:]
    dev18 = header[6:24]
    ks = _xor(dev18[2:16 + 2], C_CONST)
    payload = aes128_cbc_decrypt(K_AES, K_AES, body)
    scrambled = bytes(x ^ ks[i % 16] for i, x in enumerate(payload))
    stream = tea_dualfeedback_decrypt(scrambled)
    return {"raw": raw, "header": header, "dev18": dev18, "payload": payload,
            "p224": payload, "stream": stream,
            "tlv": stream[9:9 + TLV_BYTES] if len(stream) >= 329 else b""}
