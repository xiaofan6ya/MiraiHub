"""Trae iCube 凭证封装解密（AES-128-CBC + SHA512 校验）。

用途：本机装了 Trae 桌面版时，`storage.json` 里的登录态是加密的
（`iCubeAuthInfo://icube.cloudide`），必须解出明文才能拿到 token 导入号池。
Linux / Docker deploy 没有桌面端，走「手工导入 refresh token」，本模块不参与。

封装格式（静态逆向 + 真机复现）：

    0..1   't','c'            magic
    2      version = 5
    3      0x10
    4..5   0,0
    6..37  key material (32B)
    38..   AES-128-CBC 密文

派生：
    outer = SHA512(key_material)
    mixed = ULE ^ DLE            （客户端 byteCrypto.js 的两份 64 字节常量）
    seed  = outer || mixed; seed[0:64] = SHA512(seed)
    key   = seed[0:16]; iv = seed[16:32]
    明文  = SHA512(body)(64B) || body    —— 摘要必须与正文匹配，否则判定解错

下面的 AES 实现是**逐字节复刻**（从已验证实现机械搬运），不要"顺手优化"：
S 盒常量、密钥扩展与轮函数任何一处改动都需要真机回归。
"""
from __future__ import annotations

import base64
import hashlib
import json

def _gmul(a, b):
    """GF(2^8) 乘法（不可约多项式 0x11B）。"""
    product = 0
    for _ in range(8):
        if b & 1:
            product ^= a
        high = a & 0x80
        a = (a << 1) & 0xFF
        if high:
            a ^= 0x1B
        b >>= 1
    return product


def _rotl8(value, shift):
    return ((value << shift) | (value >> (8 - shift))) & 0xFF


def _build_sbox():
    """按 FIPS-197 定义现场生成 S-box（逆元 + 仿射变换）。"""
    inverse = [0] * 256
    for i in range(1, 256):
        for x in range(1, 256):
            if _gmul(i, x) == 1:
                inverse[i] = x
                break
    sbox = []
    for i in range(256):
        v = inverse[i]
        sbox.append(v ^ _rotl8(v, 1) ^ _rotl8(v, 2) ^ _rotl8(v, 3)
                    ^ _rotl8(v, 4) ^ 0x63)
    return sbox


_SBOX = _build_sbox()
_INV_SBOX = [0] * 256
for _index, _value in enumerate(_SBOX):
    _INV_SBOX[_value] = _index
_RCON = (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36)


def _expand_key(key):
    """AES 密钥扩展（16/24/32 字节；Trae 凭证固定 16 字节）。"""
    size = len(key)
    if size == 16:
        words, rounds = [list(key[i:i + 4]) for i in range(0, 16, 4)], 44
    elif size == 24:
        words, rounds = [list(key[i:i + 4]) for i in range(0, 24, 4)], 52
    elif size == 32:
        words, rounds = [list(key[i:i + 4]) for i in range(0, 32, 4)], 60
    else:
        raise ValueError("AES 密钥长度必须是 16/24/32 字节，实际 %d" % size)
    nk = size // 4
    for i in range(nk, rounds):
        temp = list(words[i - 1])
        if i % nk == 0:
            temp = temp[1:] + temp[:1]
            temp = [_SBOX[b] for b in temp]
            temp[0] ^= _RCON[i // nk - 1]
        elif nk > 6 and i % nk == 4:
            temp = [_SBOX[b] for b in temp]
        words.append([words[i - nk][j] ^ temp[j] for j in range(4)])
    round_keys = []
    for r in range(rounds // 4):
        rk = []
        for c in range(4):
            rk.extend(words[r * 4 + c])
        round_keys.append(rk)
    return round_keys


def _decrypt_block(block, round_keys):
    """AES 单块解密（InvSubBytes+InvShiftRows -> AddRoundKey -> InvMixColumns）。"""
    nr = len(round_keys) - 1
    state = [block[i] ^ round_keys[nr][i] for i in range(16)]

    def inv_sub_shift(s):
        out = [0] * 16
        for c in range(4):
            for r in range(4):
                out[r + 4 * ((c + r) % 4)] = _INV_SBOX[s[r + 4 * c]]
        return out

    def inv_mix_col(s):
        out = [0] * 16
        for c in range(4):
            a = s[4 * c:4 * c + 4]
            out[4 * c + 0] = (_gmul(a[0], 14) ^ _gmul(a[1], 11)
                              ^ _gmul(a[2], 13) ^ _gmul(a[3], 9))
            out[4 * c + 1] = (_gmul(a[0], 9) ^ _gmul(a[1], 14)
                              ^ _gmul(a[2], 11) ^ _gmul(a[3], 13))
            out[4 * c + 2] = (_gmul(a[0], 13) ^ _gmul(a[1], 9)
                              ^ _gmul(a[2], 14) ^ _gmul(a[3], 11))
            out[4 * c + 3] = (_gmul(a[0], 11) ^ _gmul(a[1], 13)
                              ^ _gmul(a[2], 9) ^ _gmul(a[3], 14))
        return out

    for rnd in range(nr - 1, 0, -1):
        state = inv_sub_shift(state)
        state = [state[i] ^ round_keys[rnd][i] for i in range(16)]
        state = inv_mix_col(state)
    state = inv_sub_shift(state)
    return bytes(state[i] ^ round_keys[0][i] for i in range(16))


def _encrypt_block(block, round_keys):
    """AES 单块加密（SubBytes+ShiftRows -> MixColumns -> AddRoundKey）。"""
    nr = len(round_keys) - 1
    state = [block[i] ^ round_keys[0][i] for i in range(16)]

    def sub_shift(s):
        out = [0] * 16
        for c in range(4):
            for r in range(4):
                out[r + 4 * c] = _SBOX[s[r + 4 * ((c + r) % 4)]]
        return out

    def mix_col(s):
        out = [0] * 16
        for c in range(4):
            a = s[4 * c:4 * c + 4]
            out[4 * c + 0] = _gmul(a[0], 2) ^ _gmul(a[1], 3) ^ a[2] ^ a[3]
            out[4 * c + 1] = a[0] ^ _gmul(a[1], 2) ^ _gmul(a[2], 3) ^ a[3]
            out[4 * c + 2] = a[0] ^ a[1] ^ _gmul(a[2], 2) ^ _gmul(a[3], 3)
            out[4 * c + 3] = _gmul(a[0], 3) ^ a[1] ^ a[2] ^ _gmul(a[3], 2)
        return out

    for rnd in range(1, nr):
        state = sub_shift(state)
        state = mix_col(state)
        state = [state[i] ^ round_keys[rnd][i] for i in range(16)]
    state = sub_shift(state)
    return bytes(state[i] ^ round_keys[nr][i] for i in range(16))


def aes_cbc_encrypt(plain, key, iv):
    """AES-CBC 加密 + PKCS#7 填充（仅为测试构造合成 iCube 封装，运行时不用）。"""
    if len(key) not in (16, 24, 32):
        raise ValueError("AES 密钥长度非法：%d" % len(key))
    if len(iv) != 16:
        raise ValueError("AES IV 必须是 16 字节")
    pad = 16 - len(plain) % 16
    data = bytes(plain) + bytes([pad]) * pad
    round_keys = _expand_key(key)
    out = bytearray()
    prev = iv
    for offset in range(0, len(data), 16):
        block = bytes(a ^ b for a, b in zip(data[offset:offset + 16], prev))
        encrypted = _encrypt_block(block, round_keys)
        out.extend(encrypted)
        prev = encrypted
    return bytes(out)


def aes_cbc_decrypt(data, key, iv):
    """AES-CBC 解密 + 严格 PKCS#7 校验（只解密方向，够用即止）。"""
    if len(key) not in (16, 24, 32):
        raise ValueError("AES 密钥长度非法：%d" % len(key))
    if len(iv) != 16:
        raise ValueError("AES IV 必须是 16 字节")
    if not data or len(data) % 16:
        raise ValueError("密文长度 %d 不是 16 的整数倍" % len(data))
    round_keys = _expand_key(key)
    out = bytearray()
    prev = iv
    for offset in range(0, len(data), 16):
        block = data[offset:offset + 16]
        decrypted = _decrypt_block(block, round_keys)
        out.extend(a ^ b for a, b in zip(decrypted, prev))
        prev = block
    pad = out[-1]
    if pad < 1 or pad > 16 or bytes(out[-pad:]) != bytes([pad] * pad):
        raise ValueError("PKCS#7 填充校验失败")
    return bytes(out[:-pad])


def _aes_self_check():
    """NIST FIPS-197 附录 C 测试向量（AES-128 单块 ECB 解密）。

    用单块解密直接比对，不经过 CBC/PKCS#7：CBC 自检需要构造"密文 + 合法填充块"，
    很容易写成假阳性/假阴性，单块 ECB 才是密码学层面的权威向量。
    """
    key = bytes(range(16))
    plain = bytes.fromhex("00112233445566778899aabbccddeeff")
    cipher = bytes.fromhex("69c4e0d86a7b0430d8cdb78070b4c55a")
    return _decrypt_block(cipher, _expand_key(key)) == plain


# Trae 客户端 byteCrypto.js 里的两份 64 字节常量（PROTOCOL 第四节）
_ULE = bytes([82, 9, 106, 213, 48, 54, 165, 56, 191, 64, 163, 158, 129, 243,
              215, 251, 124, 227, 57, 130, 155, 47, 255, 135, 52, 142, 67, 68,
              196, 222, 233, 203, 84, 123, 148, 50, 166, 194, 35, 61, 238, 76,
              149, 11, 66, 250, 195, 78, 8, 46, 161, 102, 40, 217, 36, 178,
              118, 91, 162, 73, 109, 139, 209, 37])
_DLE = bytes([31, 221, 168, 51, 136, 7, 199, 49, 177, 18, 16, 89, 39, 128,
              236, 95, 96, 81, 127, 169, 25, 181, 74, 13, 45, 229, 122, 159,
              147, 201, 156, 239, 160, 224, 59, 77, 174, 42, 245, 176, 200,
              235, 187, 60, 131, 83, 153, 97, 23, 43, 4, 126, 186, 119, 214,
              38, 225, 105, 20, 99, 85, 33, 12, 125])

ICUBE_MAGIC = b"tc"

# ---------------------------------------------------------------------------
# iCube 封装
# ---------------------------------------------------------------------------
ICUBE_MAGIC = b"tc"


def decrypt_blob(value) -> dict:
    """解密 `iCubeAuthInfo://icube.cloudide` 的值，返回明文 body（dict）。

    任何不匹配都抛 `ValueError`，绝不返回半成品 —— 半个凭证进号池比导入
    失败更难排查。
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError("iCubeAuthInfo 值不是非空字符串")
    try:
        raw = base64.b64decode(value.strip())
    except Exception as exc:
        raise ValueError("base64 解码失败：%s" % exc)
    if len(raw) < 38 + 16:
        raise ValueError("封装过短（%d 字节）" % len(raw))
    if raw[:2] != ICUBE_MAGIC:
        raise ValueError("magic 不是 tc（实际 %r）" % raw[:2])
    if raw[2] != 5:
        raise ValueError("未知封装版本：%d" % raw[2])
    key_material = raw[6:38]
    cipher = raw[38:]
    outer = hashlib.sha512(key_material).digest()
    mixed = bytes(x ^ y for x, y in zip(_ULE, _DLE))
    seed = bytearray(outer + mixed)
    seed[0:64] = hashlib.sha512(bytes(seed)).digest()
    plain = aes_cbc_decrypt(cipher, bytes(seed[0:16]), bytes(seed[16:32]))
    digest, body = plain[:64], plain[64:]
    if hashlib.sha512(body).digest() != digest:
        raise ValueError("正文 SHA512 摘要校验失败（密钥或封装不匹配）")
    try:
        data = json.loads(body.decode("utf-8"))
    except Exception as exc:
        raise ValueError("正文不是合法 JSON：%s" % exc)
    if not isinstance(data, dict):
        raise ValueError("正文 JSON 不是对象")
    return data