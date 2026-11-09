# -*- coding: utf-8 -*-
"""Android 签名身份管理（`scripts/android_signing.py`）的回归锁定。

## 为什么这些断言值得存在

APK 覆盖安装的判据是「包名 + 签名证书」。换一把私钥 → 用户只能卸载，
而卸载会带走本地账号库。历史上出过两次：

1. 签名材料放在 `build/` 里，打包脚本每次重建该目录 → 每版换一把钥匙；
2. `signing.json`（密码）还在、`release.jks` 被删 → 用同一密码**重新生成
   一把新私钥**，全程无报错，直到用户装更新失败。

所以这里的核心是三条「必须中止构建」的规则，而不是「能生成 keystore」。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import android_signing as signing  # noqa: E402


class ExpectedFingerprintParseTests(unittest.TestCase):
    """"`expected-signing-cert.txt` 的解析必须只认 `sha256=` 行。"""

    def _parse(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "expected-signing-cert.txt"
            target.write_text(text, encoding="utf-8")
            with mock.patch.object(signing, "EXPECTED_CERT_FILE", target):
                return signing.expected_sha256()

    def test_reads_sha256_key_not_alias(self):
        text = ("# 注释\n"
                "alias=workbuddy2api\n"
                "sha256=0F850D0E5B5DC35A84F1CACDCA284673BB11F94CC0BB646B2435D2375B68C3B9\n")
        self.assertEqual(
            self._parse(text),
            "0F850D0E5B5DC35A84F1CACDCA284673BB11F94CC0BB646B2435D2375B68C3B9")

    def test_tolerates_colons(self):
        spaced = ":".join(["0F", "85", "0D"] + ["AB"] * 29)
        self.assertEqual(self._parse("sha256=%s\n" % spaced),
                         spaced.replace(":", "").upper())

    def test_bare_hex_line_still_accepted(self):
        bare = "AB" * 32
        self.assertEqual(self._parse(bare + "\n"), bare)

    def test_missing_file_is_empty(self):
        with mock.patch.object(signing, "EXPECTED_CERT_FILE",
                               Path(tempfile.gettempdir()) / "nope-does-not-exist"):
            self.assertEqual(signing.expected_sha256(), "")


class GuardTests(unittest.TestCase):
    """三条「必须中止」的规则。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.env = mock.patch.dict(os.environ,
                                   {"WB_ANDROID_SIGNING_DIR": str(self.dir)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self._tmp.cleanup()

    def test_config_without_keystore_never_regenerates(self):
        """配置在、私钥没了 → 报错，**绝不**用旧密码重新生成一把新钥匙。"""
        signing._write_config(self.dir / "signing.json",
                              {"password": "pw", "store_password": "pw",
                               "key_password": "pw", "alias": "workbuddy2api"})
        with self.assertRaises(signing.SigningError) as caught:
            signing.ensure_identity(quiet=True)
        self.assertIn("丢失", str(caught.exception))

    def test_keystore_without_config_reports_loudly(self):
        """私钥在、密码丢了 → 明确报错（JKS 密码不可反推）。"""
        (self.dir / "release.jks").write_bytes(b"not-a-real-jks")
        with self.assertRaises(signing.SigningError) as caught:
            signing.ensure_identity(quiet=True)
        self.assertIn("signing.json", str(caught.exception))

    def test_verify_requires_complete_identity(self):
        with self.assertRaises(signing.SigningError):
            signing.verify()

    def test_legacy_dir_is_migrated(self):
        legacy = self.dir / "legacy"
        legacy.mkdir()
        (legacy / "release.jks").write_bytes(b"x")
        with mock.patch.object(signing, "LEGACY_SIGNING_DIR", legacy):
            target = self.dir / "migrated"
            signing._migrate_legacy(target)
        self.assertTrue((target / "release.jks").is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)