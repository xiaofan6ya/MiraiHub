"""持久回归测试：uskey 的 KAT、结构和声明长度。零 mock，零网络。"""
import hashlib
import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

# Direct-file loading avoids the admin package's startup/configuration side effects.
SOURCE = Path(__file__).resolve().parents[1] / "admin/platform/marvis/core/uskey_local.py"
_spec = importlib.util.spec_from_file_location("_marvis_uskey_local_regression", SOURCE)
U = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(U)

#: 合成夹具设备串（36 hex = 18 字节）。**不是任何真实设备。**
FIX_Q = "0123456789abcdef012345673000deadbeef"
FIX_Q_BYTES = bytes.fromhex(FIX_Q)

# Frozen synthetic KAT from base 04f9171, also matching 7b6f6f3's recorded raw
# SHA-256 16ad0880020f5775c5157e0e19a4bc39c3ead7dff461f2833e714c5a20550aea.
DEFAULT_KAT = (
    "CsDVDCERCCEACsQFDieHAQkLBC0MBtCRibEhWxx0JIk25rLlnDo5/WjuA9/nTprfdduuMi7sLtmRgxxEspCN"
    "80xQkgL+TFOygtEQ/Hhs9Kx5SZ0Q30A5d+8pqSom+E/kOvCP7w9gXPRqFWRaG18dJPDd0bHV/Y5Jsh7N0ICS"
    "BDt2nAfd/qDpaeLV+f4h5QQDklVOjqZlQzMInbNTQEdhIdjzIkKuH2TBXvTfLWfp5076Qgg0n3xEu1TkOMDX"
    "IjEBTEk2AthfkiZUUSdXhjPnKPW5Xbd+BLv6WivdYdMQTnddbJYzmJsA0bzMXzECshTyCQAyveaaFq9hvpYy"
    "+2WFsOgGmEM2O0Uvnx9E+ibx01HU+1+RRpSuHlSGURHTULtsUnApy2SB7nCxXZP0uF5Ih87iZ9TkQ4TVMRK/"
    "vJs8APP0gTxz2uX2Xn7AOuEpMLmO4qnDZMY1HXu9wY+6dLkFFnUMvGyMsfLVfzy0R/5G4Bhyl+VWZzeVpi=="
)

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

    def test_default_320_kat_stays_byte_for_byte_unchanged(self):
        self.assertEqual(U.kat_mint(FIX_Q), DEFAULT_KAT)

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
            "import importlib.util;"
            "s=importlib.util.spec_from_file_location('_uskey_fixture',%r);"
            "U=importlib.util.module_from_spec(s);s.loader.exec_module(U);"
            "print(U.KAT_FIXTURE_QIMEI36);"
            "print(U.selfcheck()['ok'])" % str(SOURCE)
        )
        out = subprocess.run([sys.executable, "-B", "-X", "utf8", "-c", code],
                             capture_output=True, text=True,
                             encoding="utf-8", errors="replace",
                             env={"MARVIS_KAT_QIMEI36": fixture,
                                  "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(out.returncode, 0, (out.stderr or "")[-2000:])
        lines = out.stdout.strip().splitlines()
        self.assertEqual(lines[0], fixture)
        self.assertEqual(lines[-1], "True")


class DeclaredTlvLengthTests(unittest.TestCase):
    """Persistent regression: decode opaque TLVs by the wire length, not 320.

    The 336-byte case is deliberately synthetic parser input. It is not a
    captured SDK layout or evidence for a 336-byte minting implementation.
    """

    @staticmethod
    def _encode_stream(stream):
        dev18 = bytes(range(18))
        payload = U.build_payload(stream, dev18)
        encrypted = U.aes128_cbc_encrypt(U.K_AES, U.K_AES, payload)
        return U.b64_custom_encode(bytes.fromhex("030057040111") + dev18 + encrypted)

    @classmethod
    def _opaque_token(cls, size, declared=None):
        rest = bytes(i % 256 for i in range(size - 2))
        tlv = ((size if declared is None else declared) - 2).to_bytes(2, "big") + rest
        stream = bytes.fromhex("06000000000000005a") + tlv + bytes(7)
        return cls._encode_stream(stream), tlv

    def test_decodes_existing_short_208_tlv(self):
        token = U.mint_uskey(
            FIX_Q, BUS, dev18=bytes(range(18)), now_ms=1730000000000,
            rnd8=bytes(8), blob=bytes(15), head8=bytes(8), short=True)
        info = U.decode_uskey(token)
        self.assertEqual(len(info["tlv"]), 208)
        self.assertEqual(info["tlv"][:2], b"\x00\xce")
        self.assertEqual(info["tlv_len"], 208)

    def test_decodes_complete_synthetic_336_tlv(self):
        token, tlv = self._opaque_token(336)
        info = U.decode_uskey(token)
        self.assertEqual(info["tlv"], tlv)
        self.assertEqual(info["tlv_len"], 336)

    def test_declared_length_cannot_consume_seven_byte_envelope_tail(self):
        token, _ = self._opaque_token(320, declared=321)
        info = U.decode_uskey(token)
        self.assertEqual(info["tlv"], b"")
        self.assertEqual(info["tlv_len"], 0)

    def test_oversized_declared_length_is_not_silently_truncated(self):
        token, _ = self._opaque_token(320, declared=400)
        info = U.decode_uskey(token)
        self.assertEqual(info["tlv"], b"")
        self.assertEqual(info["tlv_len"], 0)

    def test_stream_without_complete_tlv_length_header_has_no_tlv(self):
        token = self._encode_stream(bytes.fromhex("0600000000000000"))
        info = U.decode_uskey(token)
        self.assertEqual(info["tlv"], b"")
        self.assertEqual(info["tlv_len"], 0)


if __name__ == "__main__":
    unittest.main()
