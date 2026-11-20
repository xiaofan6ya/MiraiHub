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

## TLV 形状（2026-11-20 新增）

    真 DLL 在本机签出的 TLV 是 **336 字节 / 头 014e / 设备串 47+153**，
    本模块历史模板是 **320 / 013e / 47+152**。所以 TLV 长度、两处设备串偏移
    和头两个字节都进了形状表（`SHAPES`），由 `MARVIS_USKEY_TLV_SHAPE` 选：

        320（默认，线上当前就是这个）  320 字节 TLV，段布局由真品抓包逐字节反解
        336（真 DLL 同构）             长度 / 头 / 两处偏移与实测吻合，
                                       但多出来的 16 字节内容**没有样本**，位置只能推

    默认仍是 320：改成 336 等于改线上字节，验证过再切（见 TLV_GAP_* 的说明）。

## 与侧车的关系

本模块**不需要**侧车。dev18 每次随机；qimei36 是账号侧的设备串
（签名时必须由调用方显式传入，见 core/uskey.py 的 qimei36()）。
core/uskey.py 只在本地 mint 抛异常时才回落侧车。

## 依赖

只用 base64 / hashlib / logging / os / struct / time —— AES-128 与 TEA 都是本文件内的
纯 Python 实现（为的是镜像里不需要 cryptography）。
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import struct
import time

log = logging.getLogger(__name__)

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

# ---------------------------------------------------------------------------
# 短版（208 字节 TLV / 264 字节 uskey）—— **上游真正认的那一版**
# ---------------------------------------------------------------------------
#: 2026-11-19 实测：线上 wine 签出 264 字节 → 200；本地 376 字节 → 403。
#: 264 = 24(header) + 8(rand) + 1(flag) + 208(TLV) + 7(tail)
TLV_SHORT_BYTES = 208
PAYLOAD_SHORT_BYTES = 224
USKEY_SHORT_BYTES = 264
USKEY_SHORT_CHARS = 352

#: 短版尾部 16 字节的偏移（TLV 内）。320 版这里是 md5("")（302:320），
#: 短版没有 JSON 段，尾部紧接着 189，写 md5(bus_info)。
OFF_SHORT_MD5 = 189

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

#: TLV 模板里那两段设备串的**占位值**。`build_tlv()` 会把它整段覆盖成调用方
#: 给的值，所以它不是"默认设备串"，也不承载任何身份 —— 这里钉死成合成值，
#: 是为了让 `selfcheck()` 能发现"有人把真机值焊回模板"这种改动。
SYNTHETIC_QIMEI36 = "0123456789abcdef012345673000deadbeef"

#: KAT 夹具设备串（36 hex = 18 字节）。默认就是这个合成值，**不是任何真实设备**。
#: 要和真机抓包逐字节对照时用 `MARVIS_KAT_QIMEI36` 注入：真值留在你自己的
#: 机器 / CI secret 里，别写回代码（写回来的那一刻它就是公开的）。
KAT_FIXTURE_QIMEI36 = os.environ.get("MARVIS_KAT_QIMEI36") or SYNTHETIC_QIMEI36

#: 载荷第 8 字节（TLV 前的那个 flag）。真品是 0x5a，不是随机数。
STREAM_FLAG = 0x5A

# ---------------------------------------------------------------------------
# TLV 形状表：320（历史真品，默认）/ 336（真 DLL 同构，内容未验）
# ---------------------------------------------------------------------------
#: 形状选择的环境变量。写错值 → warning + 回落到 320。
SHAPE_ENV = "MARVIS_USKEY_TLV_SHAPE"
#: 默认形状 = **320**：线上当前跑的就是它。改默认值等于改线上字节，验证过再动。
SHAPE_DEFAULT = "320"
#: 真 DLL 形状：2026-11-20 本机 Windows x64 实测 524 字符 / 392 字节 / TLV 336 /
#: 设备串 47+153 / 头 014e / magic 030057040111（magic 与 320 相同）。
SHAPE_DLL = "336"

#: 336 版的长度是**推得出来的**，不是抄的：
#:
#:     stream = 8 + 1 + 336 + 7 = 352
#:     body   = AES-128-CBC(352) = 368（352 已被 16 整除，PKCS#7 补满一整块）
#:     uskey  = 24 + 368 = 392 字节 → base64_A 后 524 字符
#:
#: 392 / 524 与实测逐项吻合 —— 所以长度这一环没有悬念，有悬念的是里面那 16 字节。
TLV_BYTES_336 = 336
PAYLOAD_BYTES_336 = 352
USKEY_BYTES_336 = 392
USKEY_CHARS_336 = 524

#: 336 - 320 = 16 字节，但实测偏移说明**不是一整块**：
#:
#:     0:65      两版同长（qimei1 的值都在 47 ⇒ busid / sdk / appid 三段长度不变）
#:     65:152    DLL 段 A 多 1 字节（于是 qimei2 的值从 152 落到 153）
#:     152:170   qimei2 的值段（320 版坐标）
#:     170:320   DLL 段 B 多 15 字节
#:
#: 那 1 + 15 字节**是什么字段、落在段内哪个位置，目前定不了** —— 手上没有一份
#: 真 DLL 的 392 字节 TLV 原文。下面四个常量是**位置假设**（A 取 65 这个最早段边界，
#: B 取尾部 0010+md5 段之前），内容默认 0x00。拿到真样本后：设
#: `MARVIS_USKEY_TLV_336_UNKNOWN=<32 hex>` 原样灌进来，或改这里的常量 ——
#: 别去改 build_tlv 的组装逻辑。
TLV_GAP_A_AT = 65      # 320 坐标：qimei1 段之后、md5(bus_info) 段之前
TLV_GAP_A_LEN = 1
TLV_GAP_B_AT = 302     # 320 坐标：尾部 0010+md5("") 段之前
TLV_GAP_B_LEN = 15
#: 未知道字节的外部注入口（1 + 15 = 16 字节 = 32 个 hex 字符）。
TLV_GAP_ENV = "MARVIS_USKEY_TLV_336_UNKNOWN"
GAP_BYTES = TLV_GAP_A_LEN + TLV_GAP_B_LEN

#: 尾部那一段 0010 + md5("") 在 320 模板里的起点（build_tlv 不重写它，保持模板原值）。
OFF_MD5_TAIL = 302


def gap_bytes() -> bytes:
    """336 形状那 16 字节未知内容的**当前取值**（env 覆盖 > 全 0）。

    env 只接受 32 个 hex 字符；写错就当没设（warning + 全 0）——"猜错的字节"
    和"没有字节"对这个形状是同一件事：都过不了上游。
    """
    raw = (os.environ.get(TLV_GAP_ENV) or "").strip()
    if not raw:
        return bytes(GAP_BYTES)
    try:
        got = bytes.fromhex(raw)
    except ValueError:
        log.warning("%s=%r 不是 hex，按全 0 处理", TLV_GAP_ENV, raw)
        return bytes(GAP_BYTES)
    if len(got) != GAP_BYTES:
        log.warning("%s 必须 %d 字节（%d 个 hex），收到 %d，按全 0 处理",
                    TLV_GAP_ENV, GAP_BYTES, GAP_BYTES * 2, len(got))
        return bytes(GAP_BYTES)
    return got


def tlv_336_template(unknown: bytes = None) -> bytes:
    """320 模板 → 336 模板：在 A / B 两处插入未知道字节（默认取 gap_bytes()）。

    拼完顺手把开头的长度字段改成 336-2 = 0x014e：模板本身也要自洽，
    否则把模板 dump 出来对照时，头还是 013e 会误导人。
    """
    gap = gap_bytes() if unknown is None else bytes(unknown)
    if len(gap) != GAP_BYTES:
        raise ValueError("336 形状的未知道字节必须 %d 字节，收到 %d"
                         % (GAP_BYTES, len(gap)))
    out = bytearray(TLV_TEMPLATE[:TLV_GAP_A_AT] + gap[:TLV_GAP_A_LEN]
                    + TLV_TEMPLATE[TLV_GAP_A_AT:TLV_GAP_B_AT] + gap[TLV_GAP_A_LEN:]
                    + TLV_TEMPLATE[TLV_GAP_B_AT:])
    out[0:2] = (len(out) - 2).to_bytes(2, "big")
    return bytes(out)


def _off_336(off: int) -> int:
    """320 偏移 → 336 偏移（按上面两个 gap 平移；不手抄偏移表）。"""
    return (off + (TLV_GAP_A_LEN if off >= TLV_GAP_A_AT else 0)
            + (TLV_GAP_B_LEN if off >= TLV_GAP_B_AT else 0))


def _offsets_320() -> dict:
    return {"len": OFF_LEN, "busid": OFF_BUSID, "sdk": OFF_SDK, "appid": OFF_APPID,
            "qimei1": OFF_QIMEI1, "md5": OFF_MD5, "ms": OFF_MS, "blob": OFF_BLOB,
            "rnd": OFF_RND, "ver2": OFF_VER2, "proc": OFF_PROC,
            "qimei2": OFF_QIMEI2, "product": OFF_PRODUCT, "json": OFF_JSON,
            "md5_tail": OFF_MD5_TAIL}


def _offsets_336() -> dict:
    return {name: _off_336(off) for name, off in _offsets_320().items()}


#: 336 版模板（gap 全 0 的**基线**；设了 TLV_GAP_ENV 时由 shape_spec() 现拼）
TLV_TEMPLATE_336 = tlv_336_template(bytes(GAP_BYTES))

SHAPES = {
    SHAPE_DEFAULT: {
        "key": SHAPE_DEFAULT,
        "tlv_bytes": TLV_BYTES,
        "payload_bytes": PAYLOAD_BYTES,
        "uskey_bytes": USKEY_BYTES,
        "uskey_chars": USKEY_CHARS,
        "head": (TLV_BYTES - 2).to_bytes(2, "big"),
        "template": TLV_TEMPLATE,
        "offsets": _offsets_320(),
        "layout_verified": True,
        "layout_note": "2026-11-18 真客户端 504 字符 uskey 逐字节反解；KAT 锁着",
    },
    SHAPE_DLL: {
        "key": SHAPE_DLL,
        "tlv_bytes": TLV_BYTES_336,
        "payload_bytes": PAYLOAD_BYTES_336,
        "uskey_bytes": USKEY_BYTES_336,
        "uskey_chars": USKEY_CHARS_336,
        "head": (TLV_BYTES_336 - 2).to_bytes(2, "big"),
        "template": TLV_TEMPLATE_336,
        "offsets": _offsets_336(),
        "layout_verified": False,
        "layout_note": ("长度 336 / 头 014e / 设备串 47+153 三项与实测吻合；"
                        "多出的 16 字节内容与位置未验 —— 需要一份真 DLL 的 392 字节样本"),
    },
}


def shape_spec(value=None) -> dict:
    """解析 TLV 形状：显式参数 > `MARVIS_USKEY_TLV_SHAPE` > 默认 320。

    写错值（含 env 写错）一律 `log.warning` 后按 320 处理 —— 形状是字节级契约，
    猜错不如不动。
    """
    if value is None or value == "":
        raw = (os.environ.get(SHAPE_ENV) or "").strip()
        key = raw or SHAPE_DEFAULT
        source = "%s=%r" % (SHAPE_ENV, raw)
    else:
        key = str(value).strip()
        source = "shape=%r" % (key,)
    if key not in SHAPES:
        log.warning("%s 不认识（可选 %s），按 %s 处理",
                    source, "/".join(SHAPES), SHAPE_DEFAULT)
        key = SHAPE_DEFAULT
    spec = SHAPES[key]
    if key == SHAPE_DLL:
        gap = gap_bytes()
        if gap != bytes(GAP_BYTES):
            spec = dict(spec, template=tlv_336_template(gap))
    return spec


def verify_shapes() -> dict:
    """形状表自洽检查（长度 / 头字节 / 两处设备串占位 / 载荷与 uskey 长度换算）。

    全是**推得出来的**不变量：头 = 长度-2、载荷 = 长度+16、uskey = 24+载荷+补块、
    字符数 = 4*ceil(bytes/3)。任何一条不成立说明形状表被改错了。
    """
    seen = {}
    for key, spec in SHAPES.items():
        n = spec["tlv_bytes"]
        tpl = spec["template"]
        if len(tpl) != n:
            raise AssertionError("形状 %s：模板 %d 字节 != 声明 %d" % (key, len(tpl), n))
        want_head = (n - 2).to_bytes(2, "big")
        if spec["head"] != want_head:
            raise AssertionError("形状 %s：头 %s != 长度-2=%s"
                                 % (key, spec["head"].hex(), want_head.hex()))
        if tpl[:2] != spec["head"]:
            raise AssertionError("形状 %s：模板头 %s != 声明 %s"
                                 % (key, tpl[:2].hex(), spec["head"].hex()))
        off = spec["offsets"]
        one = tpl[off["qimei1"]:off["qimei1"] + 18]
        two = tpl[off["qimei2"]:off["qimei2"] + 18]
        if one != two:
            raise AssertionError("形状 %s：模板两处设备串不相等（%s / %s）"
                                 % (key, one.hex()[:8], two.hex()[:8]))
        if one != bytes.fromhex(SYNTHETIC_QIMEI36):
            raise AssertionError("形状 %s：模板里的占位不是合成值" % (key,))
        if off["qimei1"] != OFF_QIMEI1:
            raise AssertionError("形状 %s：第一处设备串偏移 %d != 实测 %d"
                                 % (key, off["qimei1"], OFF_QIMEI1))
        if spec["payload_bytes"] != n + 16:
            raise AssertionError("形状 %s：载荷 %d != TLV %d + 16"
                                 % (key, spec["payload_bytes"], n))
        pad = 16 - spec["payload_bytes"] % 16
        if spec["uskey_bytes"] != 24 + spec["payload_bytes"] + pad:
            raise AssertionError("形状 %s：uskey %d != 24 + 载荷 + 补块"
                                 % (key, spec["uskey_bytes"]))
        chars = 4 * -(-spec["uskey_bytes"] // 3)
        if spec["uskey_chars"] != chars:
            raise AssertionError("形状 %s：字符 %d != base64 口径 %d"
                                 % (key, spec["uskey_chars"], chars))
        seen[key] = {"tlv": n, "head": spec["head"].hex(),
                     "qimei2": off["qimei2"], "verified": spec["layout_verified"]}
    if seen[SHAPE_DEFAULT]["qimei2"] != OFF_QIMEI2:
        raise AssertionError("320 形状的第二处设备串偏移漂了：%d"
                             % (seen[SHAPE_DEFAULT]["qimei2"],))
    return seen


#: import 时就校验一遍：形状表是派生量，改错了必须立刻炸，别发一块坏包出去。
SHAPE_REPORT = verify_shapes()


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


def build_tlv_short(bus_info: str, qimei36: str, now_ms: int = None,
                    rnd8: bytes = None, blob: bytes = None,
                    anchor_sec: int = None) -> bytes:
    """拼 208 字节 TLV 的**对照实验版本** —— ⚠ 别当修复用。

    ## 已被实测否定（2026-11-20 复盘，见 docs/marvis-4100404-postmortem.md）

        写这个函数的依据是：「线上那枚 264 字节能 200，本地 376 字节不能，
        所以 264 才是上游认的形态」。

        **那个结论是错的。** 当时线上能过是因为账号恰好在封禁窗口外，
        与长度无关。后来实测：

            本地 DLL 签 504 字符 / 376 字节  →  连打 5 次全 200

        ⇒ **长度不是判据**，上游只看 uskey 是否由真 SDK 产出。

    ## 保留它的理由

        1. 历史对照：想知道「短包是什么结构」时可以拿出来比；
        2. 它确实能产出结构合法（magic / TLV / 长度都对）的包 ——
           结构合法是必要条件，只是不充分。

    ## 别做的事

        · 不要把它设成默认（`mint_uskey(short=True)`）；
        · 417 行那个 `short=True` 的默认值是 `False`，改动前先读复盘文档；
        · 遇到 403 先去查账号状态（`accounts.status` / `cool_kind`），
          不要先怀疑长度。

    原始说明（保留供对照）：

        264 - 24(header) = 240 body，按 8(rand) + 1(flag) + TLV + 7(tail) 拆：

            240 = 8 + 1 + 208 + 7   ⇒ TLV = 208

        320 版去掉「周计数 JSON」(189:302, 113B) 得 207 字节 ——
        差 1 字节正好是 qimei 不可用时 DLL 写的那个占位字节，于是 208。
    """
    qb = bytes.fromhex(qimei36)
    if len(qb) != 18:
        raise ValueError("qimei36 必须是 36 个 hex 字符（18 字节）：%r" % qimei36)
    ts = int(time.time() * 1000) if now_ms is None else int(now_ms)
    sec = ts // 1000 if anchor_sec is None else int(anchor_sec)

    # 前 189 字节直接复用长版模板（两版布局相同），再补 19 字节尾部
    tlv = bytearray(TLV_TEMPLATE[:189]) + bytearray(19)

    tlv[OFF_LEN:OFF_LEN + 2] = (TLV_SHORT_BYTES - 2).to_bytes(2, "big")
    tlv[OFF_BUSID:OFF_BUSID + 16] = BUSID.encode("ascii")
    tlv[OFF_QIMEI1:OFF_QIMEI1 + 18] = qb
    tlv[OFF_QIMEI2:OFF_QIMEI2 + 18] = qb
    md5_bus = hashlib.md5(str(bus_info).encode("utf-8")).digest()
    tlv[OFF_MD5:OFF_MD5 + 16] = md5_bus
    tlv[OFF_MS:OFF_MS + 8] = ts.to_bytes(8, "big")
    tlv[OFF_BLOB:OFF_BLOB + 15] = (blob if blob is not None
                                   else bcd_seconds(sec) + os.urandom(10))
    tlv[OFF_RND:OFF_RND + 8] = rnd8 if rnd8 is not None else os.urandom(8)
    # 尾部 16 字节：短版仍写 md5(bus_info)，与 320 版的 md5("") 不同键
    tlv[OFF_SHORT_MD5:OFF_SHORT_MD5 + 16] = md5_bus

    assert len(tlv) == TLV_SHORT_BYTES, len(tlv)
    assert tlv[OFF_QIMEI1:OFF_QIMEI1 + 18] == qb
    assert tlv[OFF_QIMEI2:OFF_QIMEI2 + 18] == qb
    return bytes(tlv)


def build_tlv(bus_info: str, qimei36: str, now_ms: int = None,
              rnd8: bytes = None, blob: bytes = None,
              week_count: int = 19, anchor_sec: int = None,
              shape=None) -> bytes:
    """按形状拼一块 TLV（默认 320 字节；`shape="336"` 出真 DLL 同构的 336 字节）。

    长度、头两字节、两处设备串偏移、各段偏移**全部来自形状表**（`SHAPES`），
    本函数里不再出现字面偏移。

    变量只有 6 处：md5(bus_info)、两处 qimei36、8 字节毫秒、15 字节 blob
    （= BCD(秒) + 10 随机）、8 字节随机、以及 JSON 里的 1/2 号。

    ⚠ 336 那一版：长度 / 头 / 两处偏移与实测吻合，但多出的 16 字节**内容未知**
      （见 TLV_GAP_*）。它结构自洽，语义未验 —— 别当"已经修好 403"用。
    """
    spec = shape_spec(shape)
    off = spec["offsets"]
    qb = bytes.fromhex(qimei36)
    if len(qb) != 18:
        raise ValueError("qimei36 必须是 36 个 hex 字符（18 字节）：%r" % qimei36)
    ts = int(time.time() * 1000) if now_ms is None else int(now_ms)
    #: 真品里 JSON 的 "2" 和 blob 的 BCD 秒是**同一个**锚点（= 令牌/session 出生时间），
    #: 与毫秒字段不是同一个源（抓包里差 1715 s）。默认都取当前秒，重放时单独指定。
    sec = ts // 1000 if anchor_sec is None else int(anchor_sec)
    tlv = bytearray(spec["template"])
    tlv[off["len"]:off["len"] + 2] = (spec["tlv_bytes"] - 2).to_bytes(2, "big")
    tlv[off["busid"]:off["busid"] + 16] = BUSID.encode("ascii")
    tlv[off["qimei1"]:off["qimei1"] + 18] = qb
    tlv[off["qimei2"]:off["qimei2"] + 18] = qb
    tlv[off["md5"]:off["md5"] + 16] = hashlib.md5(
        str(bus_info).encode("utf-8")).digest()
    tlv[off["ms"]:off["ms"] + 8] = ts.to_bytes(8, "big")
    tlv[off["blob"]:off["blob"] + 15] = (blob if blob is not None
                                        else bcd_seconds(sec) + os.urandom(10))
    tlv[off["rnd"]:off["rnd"] + 8] = rnd8 if rnd8 is not None else os.urandom(8)
    tlv[off["json"]:off["json"] + JSON_BYTES] = build_week_json(sec, week_count)
    assert len(tlv) == spec["tlv_bytes"], len(tlv)
    # qimei36 必须三处一致：header 的 Ual-Access-MarvisExt + TLV 两处。
    # 任意一处不同，上游会判定「设备串与签名不匹配」并拒掉这条请求。
    assert tlv[off["qimei1"]:off["qimei1"] + 18] == qb
    # 第二处偏移随形状漂（320→152 / 336→153），所以这里按表断言，不写字面量。
    assert tlv[off["qimei2"]:off["qimei2"] + 18] == qb
    assert tlv[off["qimei1"]:off["qimei1"] + 18] == tlv[off["qimei2"]:off["qimei2"] + 18]
    return bytes(tlv)


def build_stream_short(tlv: bytes, head8: bytes = None, flag: int = None) -> bytes:
    """224 字节载荷（短版）：8B 头 + 1B flag + TLV(208) + 7B。"""
    if len(tlv) != TLV_SHORT_BYTES:
        raise ValueError("短版 TLV 必须 %d 字节，收到 %d"
                         % (TLV_SHORT_BYTES, len(tlv)))
    head = bytearray(head8 if head8 is not None else os.urandom(8))
    head[0] = (head[0] & 0xF8) | 0x06
    return bytes(head) + bytes([STREAM_FLAG if flag is None else int(flag)]) + tlv + bytes(7)


def build_stream(tlv: bytes, head8: bytes = None, flag: int = None,
                 shape=None) -> bytes:
    """载荷：8B 头（低 3 位打 pad 标记 6）+ 1B flag + TLV + 7B。

    320 形状 → 336 字节载荷；336 形状 → 352 字节载荷（= TLV + 16）。
    """
    spec = shape_spec(shape)
    if len(tlv) != spec["tlv_bytes"]:
        raise ValueError("形状 %s 的 TLV 必须 %d 字节，收到 %d"
                         % (spec["key"], spec["tlv_bytes"], len(tlv)))
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
               anchor_sec: int = None, short: bool = False,
               shape=None) -> str:
    """造一块 uskey。

    ## 形状：`shape` / `MARVIS_USKEY_TLV_SHAPE`（2026-11-20 新增）

        320（默认，线上当前这一版）  320 TLV / 376 字节 / 504 字符 / 设备串 47+152
        336（真 DLL 同构）          336 TLV / 392 字节 / 524 字符 / 设备串 47+153

        336 那套的长度、头两字节（014e）、两处设备串偏移都是实测口径推出来的，
        但**多出来的 16 字节内容没有样本**（见 TLV_GAP_*）：它结构自洽、语义未验。
        所以默认仍是 320；`MARVIS_USKEY_TLV_SHAPE` 写错值 → warning + 按 320。

        `short=True`（208 TLV）是第三条对照路径，优先级高于 shape。

    ## `short` 参数要不要开（2026-11-19 实测记录，**别急着开**）

        同一账号、同一机器、同一出口的对照：

            线上 wine 里那枚 DLL 签出的 uskey（264 字节）  → 200 ✅
            本地本进程 mint 的 uskey（376 字节，320 TLV）  → 403 ✗

        于是按 264 口径写了一版短模板（`short=True`，208 TLV /
        352 字符 / magic `0ac0d50c2111`），**结果仍然 403**。

        进一步对比才发现：线上那枚用本地密钥**根本解不开**
        （`PKCS#7 填充不合法`），说明它不是同一套算法换个长度，
        而是 wine 里那份 DLL 内部走了另一条分支 —— 密钥或结构都不同。

    ## 所以

        `short=False`（376 字节）仍是**默认**，它至少是本地能自洽、
        能解开、能过 KAT 的那一版。

        `short=True` 保留着供对照实验用 —— 但**别把它当修复**：
        已验证它对上游无效（403）。

        真正能把本地救回来的只有两条：
          ① 想办法让本地 DLL 也走 wine 那条分支（目前 Windows 下
             无论换 bus_info / busiid / 缓冲大小 / 干净目录，都稳定 376）；
          ② 长期用线上签名服务（`_scratch/tunnel_sign.py`）。

    `dev18` 为 None 时随机；其余参数只为自检/重放留口。
    """
    if dev18 is None:
        dev18 = os.urandom(18)
    if len(dev18) != 18:
        raise ValueError("dev18 必须是 18 字节，收到 %d" % len(dev18))

    if short:
        tlv = build_tlv_short(bus_info, qimei36, now_ms=now_ms, rnd8=rnd8,
                              blob=blob, anchor_sec=anchor_sec)
        stream = build_stream_short(tlv, head8=head8, flag=flag)
        payload = build_payload(stream, dev18)
        # 短版 header 用真机魔数 0ac0d50c2111（线上那枚就是这样）
        header = bytes.fromhex("0ac0d50c2111") + dev18
        want_bytes, want_chars = USKEY_SHORT_BYTES, USKEY_SHORT_CHARS
    else:
        spec = shape_spec(shape)
        tlv = build_tlv(bus_info, qimei36, now_ms=now_ms, rnd8=rnd8, blob=blob,
                        week_count=week_count, anchor_sec=anchor_sec,
                        shape=spec["key"])
        stream = build_stream(tlv, head8=head8, flag=flag, shape=spec["key"])
        payload = build_payload(stream, dev18)
        # magic 与形状无关：真 DLL 与 mint 在本机都是 030057040111（别拿它分辨来源）
        header = bytes.fromhex("030057040111") + dev18
        want_bytes, want_chars = spec["uskey_bytes"], spec["uskey_chars"]

    out = b64_custom_encode(header + aes128_cbc_encrypt(K_AES, K_AES, payload))
    raw = b64_custom_decode(out)
    assert len(raw) == want_bytes, "uskey 必须 %d 字节，实际 %d" % (want_bytes, len(raw))
    assert len(out) == want_chars, "uskey 必须 %d 字符，实际 %d" % (want_chars, len(out))
    return out


def decode_uskey(uskey: str) -> dict:
    """uskey -> 各层中间值（排障 / 自检用，密钥与本模块同源）。

    TLV 长度取自**流里的 TLV 头**（+2），不再按 320 硬切：真 DLL 那块是 336，
    硬切会把它截断成 320，然后偏移全错（历史上 152 与 153 的 1 字节之争就是这来的）。
    """
    raw = b64_custom_decode(uskey)
    header, body = raw[:24], raw[24:]
    dev18 = header[6:24]
    ks = _xor(dev18[2:16 + 2], C_CONST)
    payload = aes128_cbc_decrypt(K_AES, K_AES, body)
    scrambled = bytes(x ^ ks[i % 16] for i, x in enumerate(payload))
    stream = tea_dualfeedback_decrypt(scrambled)
    want = int.from_bytes(stream[9:11], "big") + 2 if len(stream) >= 11 else 0
    tlv = stream[9:9 + want] if 0 < want <= len(stream) - 9 else b""
    return {"raw": raw, "header": header, "dev18": dev18, "payload": payload,
            "p224": payload, "stream": stream, "tlv": tlv, "tlv_len": len(tlv)}

# ---------------------------------------------------------------------------
# KAT：固定输入 -> 固定字节
# ---------------------------------------------------------------------------
#: KAT 的固定 bus_info（七字段顺序固定；内容本身无所谓，只要恒定）。
KAT_BUS_INFO = (
    "marvisAgentTag=main&marvisConversationID=conv_" + "a" * 32
    + "&marvisResponseID=11111111-2222-3333-4444-555555555555"
    "&marvisScene=agent_chat&model=main-auto"
    "&nonce=66666666-7777-8888-9999-aaaaaaaaaaaa"
    "&originOpenID=" + "0" * 32
)
#: 固定时间戳（毫秒）/ 随机源：KAT 要能逐字节重放，就不能读时钟和 urandom。
KAT_NOW_MS = 1730000000000


def kat_mint(qimei36: str = "", shape=None) -> str:
    """用**全固定输入**签一块 uskey：同样输入必得同样字节（KAT / 回归用）。

    真签那条路每次都必须随机（`dev18` / 毫秒 / blob 的后 10 字节），
    所以这里显式固定它们 —— 只有 KAT 能这么用。

    `shape` 走 `shape_spec()`：默认 320；给 "336" 就出真 DLL 同构的那一套，
    两套都必须可重放（同一输入两次逐字节相等）。
    """
    device = qimei36 or KAT_FIXTURE_QIMEI36
    sec = KAT_NOW_MS // 1000
    return mint_uskey(
        device, KAT_BUS_INFO,
        dev18=bytes(range(18)),
        now_ms=KAT_NOW_MS,
        rnd8=bytes(range(8)),
        blob=bcd_seconds(sec) + bytes(10),
        head8=bytes(8),
        flag=STREAM_FLAG,
        anchor_sec=sec,
        shape=shape,
    )


def selfcheck(shape=None) -> dict:
    """KAT 自检：长度 / 两处设备串 / 确定性 / 模板不承载身份。

    返回可断言的报告；任何一条不成立就抛 `AssertionError`。这是"签名链路还能用"
    的最低门槛 —— 故意放在库里而不是测试里，线上排障也能直接调。

    `shape` 默认取 320（线上口径）。报告里带 `shape` / `shape_verified`：
    336 那套是 `shape_verified=False`，只证明结构自洽，不证明上游认。
    """
    spec = shape_spec(shape)
    off = spec["offsets"]
    device = KAT_FIXTURE_QIMEI36
    wanted = bytes.fromhex(device)
    one = kat_mint(shape=spec["key"])
    report = {
        "shape": spec["key"],
        "shape_verified": spec["layout_verified"],
        "tlv_bytes": spec["tlv_bytes"],
        "qimei36_masked": device[:8] + "…",
        "uskey_chars": len(one),
        "uskey_bytes": len(b64_custom_decode(one)),
    }
    if report["uskey_chars"] != spec["uskey_chars"]:
        raise AssertionError("形状 %s 的 uskey 必须 %d 字符，实际 %d"
                             % (spec["key"], spec["uskey_chars"],
                                report["uskey_chars"]))
    if report["uskey_bytes"] != spec["uskey_bytes"]:
        raise AssertionError("形状 %s 的 uskey 必须 %d 字节，实际 %d"
                             % (spec["key"], spec["uskey_bytes"],
                                report["uskey_bytes"]))
    if one != kat_mint(shape=spec["key"]):
        # 固定输入却不稳定 = 某处还在读时钟/urandom，KAT 就失去意义。
        raise AssertionError("KAT 不可重放：相同输入两次产出不同")
    tlv = decode_uskey(one)["tlv"]
    if len(tlv) != spec["tlv_bytes"]:
        raise AssertionError("形状 %s 的 TLV 必须 %d 字节，实际 %d"
                             % (spec["key"], spec["tlv_bytes"], len(tlv)))
    for offset, name in ((off["qimei1"], "qimei1"), (off["qimei2"], "qimei2")):
        if tlv[offset:offset + 18] != wanted:
            raise AssertionError("%s 处（偏移 %d）不是调用方给的设备串"
                                 % (name, offset))
    if tlv[off["qimei1"]:off["qimei1"] + 18] != tlv[off["qimei2"]:off["qimei2"] + 18]:
        raise AssertionError("两处设备串不一致")
    # 模板里那段占位必须还是合成值：真机值一旦被焊回模板，它就是公开的。
    baked = spec["template"][off["qimei1"]:off["qimei1"] + 18]
    if baked != bytes.fromhex(SYNTHETIC_QIMEI36):
        raise AssertionError(
            "TLV 模板里焊了非合成占位设备串（%s…）：build_tlv 会覆盖它，"
            "所以它只可能是一个泄漏出来的真值" % baked.hex()[:8])
    report["ok"] = True
    return report

