"""uskey 纯 Python 实现的回归测试：KAT + 结构 + 长度。零 mock，零网络。"""
import hashlib
import unittest

from admin.platform.marvis.core import uskey_local as U

#: 合成夹具设备串（36 hex = 18 字节）。**不是任何真实设备。**
FIX_Q = "0123456789abcdef012345673000deadbeef"
FIX_Q_BYTES = bytes.fromhex(FIX_Q)

BUS = (
    "marvisAgentTag=main&marvisConversationID=conv_" + "a" * 32 +
    "&marvisResponseID=11111111-2222-3333-4444-555555555555"
    "&marvisScene=agent_chat&model=main-auto"
    "&nonce=66666666-7777-8888-9999-aaaaaaaaaaaa"
    "&originOpenID=" + "0" * 32
)


def decode_stream(uskey: str) -> bytes:
    """把 uskey 还原成 336 字节明文流（测试与排障用）。

    三层顺序不能反：外层 custom_b64 -> AES-128-CBC -> XOR 掩 -> TEA 双反馈。
    """
    raw = U.b64_custom_decode(uskey)
    dev18 = raw[6:24]
    plain = U.aes128_cbc_decrypt(U.K_AES, U.K_AES, raw[24:])
    ks = bytes(a ^ b for a, b in zip(U.C_CONST, dev18[2:18]))
    masked = bytes(x ^ ks[i % len(ks)] for i, x in enumerate(plain))
    return U.tea_dualfeedback_decrypt(masked)


class AesKatTests(unittest.TestCase):
    """AES-128 必须与 FIPS-197 附录 B 一致。"""

    def test_fips197_block(self):
        # CBC + 全零 IV 的第一块等价于 ECB 单块
        out = U.aes128_cbc_encrypt(
            bytes.fromhex("000102030405060708090a0b0c0d0e0f"),
            bytes(16),
            bytes.fromhex("00112233445566778899aabbccddeeff"))
        self.assertEqual(out[:16].hex(), "69c4e0d86a7b0430d8cdb78070b4c55a")

    def test_pkcs7_full_block(self):
        # 336 % 16 == 0 -> 补满块 -> 352
        self.assertEqual(
            len(U.aes128_cbc_encrypt(bytes(16), bytes(16), bytes(U.PAYLOAD_BYTES))),
            U.PAYLOAD_BYTES + 16)


class TeaTests(unittest.TestCase):
    def test_roundtrip(self):
        data = bytes(range(64))
        self.assertEqual(
            U.tea_dualfeedback_decrypt(U.tea_dualfeedback(data)), data)

    def test_length_preserved(self):
        for n in (8, 16, 224, 336):
            self.assertEqual(len(U.tea_dualfeedback(bytes(n))), n)


class StructureTests(unittest.TestCase):
    """产物必须是 504 字符 / 376 字节，且内部结构自洽。"""

    def test_lengths(self):
        u = U.mint_uskey(FIX_Q, BUS)
        self.assertEqual(len(u), U.USKEY_CHARS)
        self.assertEqual(len(U.b64_custom_decode(u)), U.USKEY_BYTES)

    def test_header_prefix(self):
        raw = U.b64_custom_decode(U.mint_uskey(FIX_Q, BUS))
        self.assertEqual(raw[:6].hex(), "030057040111")

    def test_b64_roundtrip(self):
        raw = U.b64_custom_decode(U.mint_uskey(FIX_Q, BUS))
        self.assertEqual(U.b64_custom_decode(U.b64_custom_encode(raw)), raw)

    def test_stream_length(self):
        self.assertEqual(len(decode_stream(U.mint_uskey(FIX_Q, BUS))),
                         U.PAYLOAD_BYTES)

    def test_tlv_length_marker(self):
        s = decode_stream(U.mint_uskey(FIX_Q, BUS))
        self.assertEqual(s[9:11], (U.TLV_BYTES - 2).to_bytes(2, "big"))

    def test_tlv_contains_md5_of_bus_info(self):
        s = decode_stream(U.mint_uskey(FIX_Q, BUS))
        tlv = s[9:9 + U.TLV_BYTES]
        self.assertIn(hashlib.md5(BUS.encode()).digest(), tlv,
                      "TLV 里必须有 md5(bus_info)：上游靠它把这封信绑到这次请求")

    def test_qimei_appears_twice(self):
        s = decode_stream(U.mint_uskey(FIX_Q, BUS))
        tlv = s[9:9 + U.TLV_BYTES]
        self.assertEqual(tlv.count(FIX_Q_BYTES), 2,
                         "设备串必须在 TLV 里出现两次且同值")

    def test_dev18_randomized(self):
        a = U.b64_custom_decode(U.mint_uskey(FIX_Q, BUS))[6:24]
        b = U.b64_custom_decode(U.mint_uskey(FIX_Q, BUS))[6:24]
        self.assertNotEqual(a, b, "dev18 必须每次随机，否则 keystream 恒定")

    def test_two_mints_differ(self):
        self.assertNotEqual(U.mint_uskey(FIX_Q, BUS), U.mint_uskey(FIX_Q, BUS))


if __name__ == "__main__":
    unittest.main()
