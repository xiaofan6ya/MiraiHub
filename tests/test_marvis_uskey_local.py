"""uskey 纯 Python 实现的回归测试：KAT + 结构 + 长度 + TLV 形状。零 mock，零网络。"""
import hashlib
import os
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


class TlvShapeTests(unittest.TestCase):
    """两套 TLV 形状：320（默认 / 线上）与 336（真 DLL 同构）。

    336 这套只锁**能从实测推出来的三项**：长度 336、头 014e、两处设备串 47+153。
    多出来的 16 字节内容没有真样本，测试不替它编内容（见 uskey_local.TLV_GAP_*），
    只锁"灌进来以后能被原样写进正确位置"。
    """

    def _env(self, name, value):
        """改 env 并登记还原（用例之间不许互相污染形状）。"""
        old = os.environ.get(name)

        def restore():
            if old is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = old

        self.addCleanup(restore)
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value

    # --- 默认口径 ---------------------------------------------------------
    def test_default_shape_is_320(self):
        """不带任何 env / 参数时必须是 320：默认值等于线上字节，不许漂。"""
        self._env(U.SHAPE_ENV, None)
        spec = U.shape_spec()
        self.assertEqual(spec["key"], "320")
        self.assertEqual(spec["tlv_bytes"], 320)
        self.assertTrue(spec["layout_verified"])
        self.assertEqual(len(U.b64_custom_decode(U.mint_uskey(FIX_Q, BUS))), 376)

    def test_320_regression(self):
        """旧形状回归：376 字节 / 504 字符 / TLV 320 / 设备串 47+152。"""
        u = U.mint_uskey(FIX_Q, BUS, shape="320")
        s = decode_stream(u)
        tlv = s[9:9 + 320]
        self.assertEqual(len(u), 504)
        self.assertEqual(len(U.b64_custom_decode(u)), 376)
        self.assertEqual(len(s), 336)
        self.assertEqual(tlv[:2], (320 - 2).to_bytes(2, "big"))
        self.assertEqual(tlv[47:65], FIX_Q_BYTES)
        self.assertEqual(tlv[152:170], FIX_Q_BYTES)
        self.assertEqual(tlv.count(FIX_Q_BYTES), 2)

    # --- 新形状：真 DLL 同构 ----------------------------------------------
    def test_336_lengths(self):
        """392 字节 / 524 字符 / 载荷 352 —— 与真 DLL 实测逐项相同。"""
        u = U.mint_uskey(FIX_Q, BUS, shape="336")
        s = decode_stream(u)
        self.assertEqual(len(u), 524)
        self.assertEqual(len(U.b64_custom_decode(u)), 392)
        self.assertEqual(len(s), 352, "载荷 = 8 头 + 1 flag + 336 TLV + 7 尾")

    def test_336_header_and_offsets(self):
        """TLV 336 / 头 014e / 两处设备串 47 与 153，且两处相等。"""
        tlv = decode_stream(U.mint_uskey(FIX_Q, BUS, shape="336"))[9:9 + 336]
        self.assertEqual(len(tlv), 336)
        self.assertEqual(tlv[:2], b"\x01\x4e")
        self.assertEqual(int.from_bytes(tlv[:2], "big") + 2, 336)
        self.assertEqual(tlv[47:65], FIX_Q_BYTES)
        self.assertEqual(tlv[153:171], FIX_Q_BYTES)
        self.assertEqual(tlv[47:65], tlv[153:171], "两处设备串必须同值")
        self.assertEqual(tlv.count(FIX_Q_BYTES), 2)
        # 152 处确实已经漂了：这正是 320 那套偏移表不能拿来解 DLL 包的原因
        self.assertNotEqual(tlv[152:170], FIX_Q_BYTES)

    def test_decode_reads_tlv_length_from_header(self):
        """decode_uskey 的 TLV 长度取自流里的头，不按 320 硬切。"""
        info = U.decode_uskey(U.mint_uskey(FIX_Q, BUS, shape="336"))
        self.assertEqual(len(info["tlv"]), 336)
        self.assertEqual(info["tlv_len"], 336)
        self.assertEqual(info["tlv"][153:171], FIX_Q_BYTES)
        old = U.decode_uskey(U.mint_uskey(FIX_Q, BUS, shape="320"))
        self.assertEqual(len(old["tlv"]), 320)

    def test_shape_table_is_consistent(self):
        """形状表自洽：头=长度-2、载荷=TLV+16、uskey=24+载荷+补块。"""
        report = U.verify_shapes()
        self.assertEqual(report["320"], {"tlv": 320, "head": "013e",
                                         "qimei2": 152, "verified": True})
        self.assertEqual(report["336"], {"tlv": 336, "head": "014e",
                                         "qimei2": 153, "verified": False})
        self.assertEqual(U.SHAPE_REPORT, report)

    # --- 开关 -------------------------------------------------------------
    def test_env_switches_shape(self):
        """MARVIS_USKEY_TLV_SHAPE=336 时 mint_uskey 出 392 字节。"""
        self._env(U.SHAPE_ENV, "336")
        self.assertEqual(U.shape_spec()["key"], "336")
        u = U.mint_uskey(FIX_Q, BUS)
        self.assertEqual(len(U.b64_custom_decode(u)), 392)
        self.assertEqual(len(u), 524)

    def test_env_typo_falls_back_to_320(self):
        """写错值 → warning + 按 320（宁可不动，也不悄悄换成猜的形状）。"""
        self._env(U.SHAPE_ENV, "33")
        with self.assertLogs(U.log, level="WARNING") as caught:
            spec = U.shape_spec()
        self.assertEqual(spec["key"], "320")
        self.assertIn("33", caught.output[0])
        self.assertEqual(len(U.b64_custom_decode(U.mint_uskey(FIX_Q, BUS))), 376)

    # --- KAT：两套都要能逐字节重放 ----------------------------------------
    def test_kat_per_shape(self):
        for shape, chars, tlv_bytes, off2 in (("320", 504, 320, 152),
                                              ("336", 524, 336, 153)):
            one = U.kat_mint(shape=shape)
            self.assertEqual(len(one), chars, shape)
            self.assertEqual(one, U.kat_mint(shape=shape),
                             "KAT 必须可重放：" + shape)
            tlv = U.decode_uskey(one)["tlv"]
            self.assertEqual(len(tlv), tlv_bytes)
            self.assertEqual(tlv[47:65], FIX_Q_BYTES)
            self.assertEqual(tlv[off2:off2 + 18], FIX_Q_BYTES)

    def test_selfcheck_both_shapes(self):
        """selfcheck 自带形状口径；336 那套必须标 shape_verified=False。"""
        for shape, chars in (("320", 504), ("336", 524)):
            report = U.selfcheck(shape=shape)
            self.assertTrue(report["ok"], shape)
            self.assertEqual(report["shape"], shape)
            self.assertEqual(report["uskey_chars"], chars)
        self.assertTrue(U.selfcheck("320")["shape_verified"])
        self.assertFalse(U.selfcheck("336")["shape_verified"],
                         "336 的段内容没有真样本，不许标成已验证")

    # --- 那 16 字节未知内容：留注入口，不编内容 ----------------------------
    def test_gap_bytes_injectable(self):
        """拿到真 DLL 样本后不改代码就能灌：32 hex = 16 字节，落位 A=65 / B=303。"""
        self._env(U.TLV_GAP_ENV, "aa" + "bb" * 15)
        spec = U.shape_spec("336")
        self.assertEqual(len(spec["template"]), 336)
        self.assertEqual(spec["template"][65], 0xAA)
        self.assertEqual(spec["template"][303:318], b"\xbb" * 15)
        # 灌了字节也照旧是 392 / 524，且两处设备串仍被覆盖成调用方给的那串
        u = U.mint_uskey(FIX_Q, BUS, shape="336")
        self.assertEqual(len(u), 524)
        tlv = U.decode_uskey(u)["tlv"]
        self.assertEqual(tlv[47:65], FIX_Q_BYTES)
        self.assertEqual(tlv[153:171], FIX_Q_BYTES)
        self.assertEqual(tlv[65], 0xAA)

    def test_gap_env_bad_value_warns(self):
        """注入口写错 → warning + 全 0，不抛（形状本身仍然可用）。"""
        self._env(U.TLV_GAP_ENV, "zz")
        with self.assertLogs(U.log, level="WARNING"):
            gap = U.gap_bytes()
        self.assertEqual(gap, bytes(U.GAP_BYTES))
        self.assertEqual(len(U.b64_custom_decode(U.mint_uskey(FIX_Q, BUS, shape="336"))), 392)


if __name__ == "__main__":
    unittest.main()
