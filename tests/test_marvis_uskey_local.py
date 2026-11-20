"""uskey 纯 Python 实现的回归测试：KAT + 结构 + 长度。零 mock，零网络。"""
import hashlib
import subprocess
import sys
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


class KatTests(unittest.TestCase):
    """KAT：固定输入 -> 固定字节，且默认夹具永远是合成值。"""

    def test_selfcheck_ok(self):
        report = U.selfcheck()
        self.assertTrue(report["ok"])
        self.assertEqual(report["uskey_chars"], U.USKEY_CHARS)
        self.assertEqual(report["uskey_bytes"], U.USKEY_BYTES)

    def test_replayable(self):
        self.assertEqual(U.kat_mint(), U.kat_mint(),
                         "固定输入必须得到固定字节，否则 KAT 没有意义")

    def test_default_fixture_is_synthetic(self):
        self.assertEqual(U.KAT_FIXTURE_QIMEI36, U.SYNTHETIC_QIMEI36,
                         "不带 MARVIS_KAT_QIMEI36 时夹具必须是合成值")

    def test_template_holds_no_identity(self):
        baked = U.TLV_TEMPLATE[U.OFF_QIMEI1:U.OFF_QIMEI1 + 18]
        self.assertEqual(baked, bytes.fromhex(U.SYNTHETIC_QIMEI36),
                         "模板里的占位设备串被换成了非合成值")

    def test_env_can_inject_fixture(self):
        # 用子进程隔离 env，别污染本进程已 import 的模块状态。
        fixture = "ffeeddccbbaa998877665544" + "3000" + "11223344"
        code = (
            "import os;"
            "os.environ['MARVIS_KAT_QIMEI36']=%r;"
            "from admin.platform.marvis.core import uskey_local as U;"
            "print(U.KAT_FIXTURE_QIMEI36);"
            "print(U.selfcheck()['ok'])" % fixture
        )
        out = subprocess.run([sys.executable, "-X", "utf8", "-c", code],
                             capture_output=True, text=True,
                             encoding="utf-8", errors="replace")
        self.assertEqual(out.returncode, 0, (out.stderr or "")[-2000:])
        lines = out.stdout.strip().splitlines()
        self.assertEqual(lines[0], fixture)
        self.assertEqual(lines[-1], "True")


if __name__ == "__main__":
    unittest.main()
