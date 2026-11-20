#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""uskey dev18 稳定化（2026-11-20）：同一个账号必须一直报**同一台设备**。

## 锁的是什么

`uskey._local_sign` 以前每次 `os.urandom(18)` 现造 dev18 —— 同一个 qimei
下面每请求换一个 dev18，上游看到的是「一台设备配无穷多个 dev18」。
而 `device.for_account(openid)` 本来就有稳定的每账号 dev18，只是没被用。
dev18 落在 uskey 明文头 `raw[6:24]` 并参与 keystream，所以这里一律从
**成品字节**里取（`uskey_local.decode_uskey` 按 TLV 头算长度，不硬切）。

## 三个不变量

  1. 同一账号连签两次：dev18 相同、qimei36 相同；
  2. 不同账号：dev18 不同（否则又是一个共享的设备维度）；
  3. `MARVIS_USKEY_STABLE_DEV18=0`：回到随机（两条 dev18 必须不同）——
     留这条路只为能做 A/B 对照。

运行：`python -m pytest tests/test_marvis_uskey_dev18_stable.py -q`
"""
import os
import sys
import unittest
from pathlib import Path

# 必须早于 admin.* 导入（admin/db.py 在模块级按 settings 建引擎）。
os.environ.setdefault(
    "ADMIN_DATABASE_URL",
    "sqlite:///" + str(Path(os.environ.get("TEMP", "/tmp")) / "marvis_dev18_test.db"),
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from admin.platform.marvis.core import device, uskey, uskey_local  # noqa: E402

#: 两个**合成**账号：openid 决定稳定的 dev18，account_key 决定 qimei36 的派生。
ACCT_A = {"account_key": "test-acct-0001", "openid": "openid-a"}
ACCT_B = {"account_key": "test-acct-0002", "openid": "openid-b"}


def mint(acct):
    """走真实的 `sign()`（本地 mint 分支），不是直接调 mint_uskey。"""
    return uskey.sign(
        agent_tag="main",
        conversation_id="conv_" + "a" * 32,
        response_id="11111111-2222-3333-4444-555555555555",
        scene="agent_chat", model="main-auto",
        nonce="66666666-7777-8888-9999-aaaaaaaaaaaa",
        openid=acct["openid"], account_key=acct["account_key"])


def dev18_of(result):
    """从成品字节取 dev18（raw[6:24]），并核对 decode 口径与它一致。"""
    raw = uskey_local.b64_custom_decode(result["uskey"])
    decoded = uskey_local.decode_uskey(result["uskey"])["dev18"]
    assert decoded == raw[6:24], "decode_uskey 的 dev18 与 raw[6:24] 不一致"
    return raw[6:24]


class Dev18StabilityTests(unittest.TestCase):
    def setUp(self):
        # dll 后端没有 dev18 输入口（dev18 由 SDK 内部产生），这里只测 mint 路径。
        self._env = {name: os.environ.get(name) for name in
                     ("MARVIS_USKEY_BACKEND", uskey.STABLE_DEV18_ENV)}
        os.environ["MARVIS_USKEY_BACKEND"] = "mint"
        os.environ.pop(uskey.STABLE_DEV18_ENV, None)   # 走默认值（= 开）
        self.addCleanup(uskey.set_signer, None)
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        for name, value in self._env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    # --- 1/3：同一账号连签两次 -------------------------------------------
    def test_same_account_two_mints_share_dev18(self):
        """同一账号连签两次：raw[6:24] 必须逐字节相同。"""
        a, b = mint(ACCT_A), mint(ACCT_A)
        self.assertEqual(len(dev18_of(a)), 18)
        self.assertEqual(
            dev18_of(a), dev18_of(b),
            "同一账号连签两次的 dev18 不同 —— 又在每请求报一台新设备")

    def test_stable_dev18_comes_from_device_for_account(self):
        """稳定值就是 device.for_account(openid) 的那 18 字节，不是另编一份。"""
        self.assertEqual(dev18_of(mint(ACCT_A)),
                         device.for_account(ACCT_A["openid"])[1])

    def test_same_account_two_mints_share_qimei(self):
        """qimei36 也必须稳（header 与 TLV 同源，换一个就是自相矛盾）。"""
        a, b = mint(ACCT_A), mint(ACCT_A)
        self.assertEqual(a["qimei36"], b["qimei36"])
        self.assertEqual(a["qimei36"], uskey.qimei36(ACCT_A["account_key"]))

    # --- 2/3：不同账号 ---------------------------------------------------
    def test_different_accounts_get_different_dev18(self):
        """不同账号必须拿到不同 dev18：共享设备维度正是被整片标记的形态。"""
        self.assertNotEqual(dev18_of(mint(ACCT_A)), dev18_of(mint(ACCT_B)))

    # --- 3/3：开关 -------------------------------------------------------
    def test_switch_off_restores_random_dev18(self):
        """`MARVIS_USKEY_STABLE_DEV18=0` → 回到每请求随机（对照实验用）。"""
        os.environ[uskey.STABLE_DEV18_ENV] = "0"
        a, b = mint(ACCT_A), mint(ACCT_A)
        self.assertNotEqual(dev18_of(a), dev18_of(b),
                            "开关=0 时 dev18 仍然是稳定的（开关没生效）")

    def test_switch_parsing(self):
        """默认开；只认明确的关；写错不静默关掉加固。"""
        os.environ.pop(uskey.STABLE_DEV18_ENV, None)
        self.assertTrue(uskey.stable_dev18_enabled())
        for off in ("0", "false", "OFF", "no", "disabled"):
            os.environ[uskey.STABLE_DEV18_ENV] = off
            self.assertFalse(uskey.stable_dev18_enabled(), off)
        for on in ("1", "yes", "true", "on", "随便写的"):
            os.environ[uskey.STABLE_DEV18_ENV] = on
            self.assertTrue(uskey.stable_dev18_enabled(), on)

    # --- 兜底：取不到稳定值不许抛 ---------------------------------------
    def test_missing_openid_falls_back_random_with_warning(self):
        """没有 openid → 回落随机 + 一条 WARNING，绝不抛。"""
        with self.assertLogs("admin.platform.marvis.uskey", level="WARNING") as logs:
            one = uskey.dev18_for("")
            two = uskey.dev18_for("   ")
        self.assertEqual(len(one), 18)
        self.assertEqual(len(two), 18)
        self.assertNotEqual(one, two)
        self.assertTrue(any("openid" in line for line in logs.output), logs.output)

    def test_derivation_failure_falls_back_random(self):
        """device.for_account 抛异常时同样回落随机 + WARNING。"""
        from unittest import mock  # noqa: PLC0415

        with mock.patch.object(device, "for_account",
                               side_effect=RuntimeError("boom")):
            with self.assertLogs("admin.platform.marvis.uskey",
                                 level="WARNING") as logs:
                one = uskey.dev18_for("openid-a")
                two = uskey.dev18_for("openid-a")
        self.assertEqual(len(one), 18)
        self.assertNotEqual(one, two)
        self.assertTrue(any("boom" in line for line in logs.output), logs.output)

    # --- 回归：稳定 dev18 不许把容器搞坏 ---------------------------------
    def test_container_shape_unchanged(self):
        """dev18 换了来源，成品形态必须还是 504 字符 / 376 字节 / 320 TLV。"""
        out = mint(ACCT_A)
        raw = uskey_local.b64_custom_decode(out["uskey"])
        self.assertEqual(len(raw), uskey_local.USKEY_BYTES)
        self.assertEqual(len(out["uskey"]), uskey_local.USKEY_CHARS)
        self.assertEqual(uskey_local.decode_uskey(out["uskey"])["tlv_len"], 320)
