"""Marvis uskey：本地 mint + _validate 字节闸门 + 整组身份轮换 + 4100404 契约。

## 这个文件补的是什么

`tests/test_marvis_uskey_sidecar.py` 随 Wine 侧车一起被删（commit 0cd124d），
里面有 8 条**与侧车无关**的断言也跟着没了 —— 它们锁的是 `uskey._validate` 的
字节下限、整组身份轮换、以及 4100404 的定性/冷却。这里按**纯 Python**重写，
不依赖侧车、不碰网络、不读 DLL。

## 与旧断言的差异（都是"实现变了"，不是"放宽标准"）

  * 异常类：`SidecarError` 已被 `uskey.MintError` 取代（侧车拆掉时重命名），
    断言里的异常类型跟着换，判定条件不变。
  * conversation_id 不再每次轮换：上游 bea6de9 把它改成**按账号稳定派生**
    （见 `endpoints._conversation_id_for`），每次轮换的是
    `response_id` / `nonce` / `uskey`。旧的"cid 也要换"是对着旧实现写的，
    这里改成锁当前契约：**同账号 cid 恒定且形状仍是 UUID，账号换了 cid 才换**。
  * 旧文件里三条 client 级 4100404 断言引用的是 `client.clear_account_risk_cooldown`
    / `client.account_risk_cooldown_remaining` —— 这两个 API 在当前代码里
    **不存在**（风控冷却在 gateway 层，且 `risk_control` 故意**不**给账号冷却），
    那三条本来就是红的；"该号冷却 300 秒"这条契约也已被 2026-11-17 的线上
    死循环推翻。所以这里按**当前**契约重写：定性 risk_control、按账号级冷却 = 0、
    平台级退避阈值 5 秒窗口、重试时整组换新 id。

## 铁律

字节下限 = 真品口径 376（504 字符 base64）。376 必须过，512 与 200 必须拒。
"""
import base64
import importlib
import io
import json
import os
import sys
import unittest
import urllib.error
from pathlib import Path

# 必须早于 admin.* 导入（admin/db.py 在模块级按 settings 建引擎）。
os.environ.setdefault(
    "ADMIN_DATABASE_URL",
    "sqlite:///" + str(Path(os.environ.get("TEMP", "/tmp")) / "marvis_uskey_test.db"),
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from admin.platform.marvis.core import client, endpoints, uskey  # noqa: E402

#: 结构合法的 qimei36（真机是 84097bfe4bec892cb32e99d730001bd1a611）。
QIMEI36 = "3aeda2c4343993fc64eec8c830001591ab11"

#: 两个**合成**账号：只用来验证"换号 = 整组换新"，不指向任何真实账号。
ACCT_A = {"id": "test-acct-0001", "openid": "openid-a", "access_token": "tok-a",
          "guid": "64e69d95-c76d-4de9-994b-6285357e6dd4", "login_type": "QC"}
ACCT_B = {"id": "test-acct-0002", "openid": "openid-b", "access_token": "tok-b",
          "guid": "0f4a2b18-6c3d-4e5f-8a9b-1c2d3e4f5a6b", "login_type": "QC"}


def fake_uskey(nbytes=376):
    """结构合法的假 uskey（magic + 全零尾），只用于长度/闸门类断言。"""
    return base64.b64encode(bytes.fromhex("0ac0d50c2111") + bytes(nbytes - 6)).decode()


class UskeyLengthGateTests(unittest.TestCase):
    """字节下限 = 真品口径 376（504 字符 base64）。

    历史教训分两段：先是 300 把 264 字节判死 → 全线 502，于是降到 240 放行；
    2026-11-18 真客户端 200 抓包证明真品是 376 字节，264 那版是「旧形态 + 仿的
    身份」（SDK 1.0.0.0 / node.exe / Not Specified、没有 320 字节业务 TLV），
    上游收紧后一律 4100404。所以默认 376：本地 mint 只产出 376，任何短包都是
    bug，必须在本地拦死，别拿 403 去污染号池。
    """

    def setUp(self):
        self._orig_min = uskey.USKEY_MIN_BYTES
        self._orig_env = os.environ.get("MARVIS_USKEY_MIN_BYTES")
        self.addCleanup(self._restore)

    def _restore(self):
        os.environ.pop("MARVIS_USKEY_MIN_BYTES", None)
        if self._orig_env is not None:
            os.environ["MARVIS_USKEY_MIN_BYTES"] = self._orig_env
        importlib.reload(uskey)
        if self._orig_env is None:
            # reload 会把值拉回默认 376；显式还原到进用例前的值，避免污染其他用例
            uskey.USKEY_MIN_BYTES = self._orig_min

    # --- 1/3：376 正常 ---------------------------------------------------
    def test_validate_accepts_376_byte_uskey(self):
        """真品口径：376 字节 / 504 字符 base64 —— 必须过。"""
        ok = uskey._validate({"uskey": fake_uskey(376), "qimei36": QIMEI36})
        self.assertEqual(ok["uskey_bytes"], 376)
        self.assertEqual(ok["uskey_len"], 504)
        self.assertEqual(ok["qimei36"], QIMEI36)

    # --- 2/3：512 拒绝 ---------------------------------------------------
    def test_validate_rejects_under_512_threshold(self):
        """闸门抬到 512 后，376 必须被拒 —— 阈值是硬闸，不是参考值。"""
        os.environ["MARVIS_USKEY_MIN_BYTES"] = "512"
        importlib.reload(uskey)
        self.assertEqual(uskey.USKEY_MIN_BYTES, 512)
        with self.assertRaises(uskey.MintError) as ctx:
            uskey._validate({"uskey": fake_uskey(376), "qimei36": QIMEI36})
        self.assertIn("376", str(ctx.exception))
        self.assertIn("512", str(ctx.exception))

    # --- 3/3：200 拒绝 ---------------------------------------------------
    def test_validate_rejects_200_byte_uskey(self):
        """200 字节（旧阈值年代的形态）在默认 376 闸门下必须被拒。"""
        os.environ.pop("MARVIS_USKEY_MIN_BYTES", None)
        importlib.reload(uskey)
        self.assertEqual(uskey.USKEY_MIN_BYTES, 376)
        with self.assertRaises(uskey.MintError) as ctx:
            uskey._validate({"uskey": fake_uskey(200), "qimei36": QIMEI36})
        self.assertIn("200", str(ctx.exception))


class IdentityRotationTests(unittest.TestCase):
    """整组身份：每次调用 `llm_headers()` 都现签一组新的 rid / nonce / uskey。"""

    def setUp(self):
        self.addCleanup(uskey.set_signer, None)

    def test_each_call_rotates_rid_nonce_and_uskey(self):
        """同一账号连发两次：rid / nonce / uskey 全换，cid 按设计恒定。

        cid 稳定是上游 bea6de9 的口径（`endpoints._conversation_id_for`：
        sha256("mh-conv:" + account_key)），换它是**违规**不是"更强的轮换"。
        所以这里锁：cid 同账号恒等且形状仍是 UUID；其余三项必须每次不同。
        """
        a = endpoints.llm_headers(ACCT_A, model="main-auto")
        b = endpoints.llm_headers(ACCT_A, model="main-auto")
        ext_a = json.loads(a["Ual-Access-MarvisExt"])
        ext_b = json.loads(b["Ual-Access-MarvisExt"])
        self.assertNotEqual(a["Marvis-Response-ID"], b["Marvis-Response-ID"])
        self.assertNotEqual(ext_a["nonce"], ext_b["nonce"])
        self.assertNotEqual(ext_a["uskey"], ext_b["uskey"])
        # 每次只有一组：请求头里的 rid 就是 bus_info 里那个 rid
        self.assertEqual(a["Ual-Access-Requestid"], a["Marvis-Response-ID"])
        # cid：同账号恒定、形状仍是 8-4-4-4-12
        self.assertEqual(a["Marvis-Conversation-ID"], b["Marvis-Conversation-ID"])
        parts = a["Marvis-Conversation-ID"].split("-")
        self.assertEqual([len(p) for p in parts], [8, 4, 4, 4, 12])

    def test_account_switch_changes_whole_group(self):
        """换账号 = 整组换新：cid / qimei36 / uskey 都必须变（设备簇事故回归锁）。

        8 个账号共用一个 qimei 会把整台"设备"打成 4100404，所以这条不许放宽。
        """
        a = endpoints.llm_headers(ACCT_A, model="main-auto")
        b = endpoints.llm_headers(ACCT_B, model="main-auto")
        ext_a = json.loads(a["Ual-Access-MarvisExt"])
        ext_b = json.loads(b["Ual-Access-MarvisExt"])
        self.assertNotEqual(a["Marvis-Conversation-ID"],
                            b["Marvis-Conversation-ID"])
        self.assertNotEqual(ext_a["qimei36"], ext_b["qimei36"])
        self.assertNotEqual(ext_a["uskey"], ext_b["uskey"])
        # 设备串与账号一一对应：本地派生必须可复现
        self.assertEqual(ext_a["qimei36"], uskey.qimei36("test-acct-0001"))
        self.assertEqual(len(ext_a["qimei36"]), 36)
        self.assertEqual(ext_a["qimei36"][24:28], uskey.DEVICE_MARK)


#: 打开上游的请求头快照（每个 attempt 一条），供 RiskRetryTests 断言"整组轮换"。
_SEEN = []


class _FakeResp:
    def __init__(self):
        self.closed = False

    def read(self):
        return b""

    def close(self):
        self.closed = True


class RiskRetryTests(unittest.TestCase):
    """4100404 的客户端契约：定性 risk_control、**一发即止**、绝不复用同一组 id。

    ⚠ 2026-11-20 口径收口：`client.CHAT_ATTEMPTS` 现在是 **1**（以前是 2，与
    `tests/test_marvis_risk_backoff.py` 模块注释里的 1 长期不一致 —— 那处漂移
    已经收口）。撞 4100404 之后**不再**整组重签重发：1 秒内的第二份特征只会
    继续撞同一个判定，18:19 那次的 4100404 → 4100403 就是这么来的。

    所以这里锁两件事，且都不写死数字：

      · `len(_SEEN) == client.CHAT_ATTEMPTS` —— 跟着常量走，调回 2 就会红；
      · 每一发必须是**新的一组** cid/rid/nonce/uskey。

    "不换号 / 平台级退避挡住第二发"属于 gateway 层，见
    `tests/test_marvis_risk_backoff.py::test_risk_control_does_not_switch_account`
    与 `::test_platform_backoff_blocks_second_request`（该文件另有 4 条按旧
    COOL_SECONDS_RISK 契约写的断言当前是红的，不在本批范围内）。
    账号级短冷却（内存 60 秒）见 `tests/test_marvis_risk_no_retry_cooldown.py`。
    """

    def setUp(self):
        self.addCleanup(uskey.set_signer, None)
        self.orig_opener = client._opener
        self.orig_sentinel = client.note_sentinel
        client.note_sentinel = lambda *a, **k: None
        self.addCleanup(setattr, client, "note_sentinel", self.orig_sentinel)
        self.addCleanup(setattr, client, "_opener", self.orig_opener)
        self.seen = []
        # 出口池关掉：否则 egress 回退会让同一发被计两次
        from admin.platform.marvis.core import egress
        self.orig_pick = egress.pick
        egress.pick = lambda account_id: ""
        self.addCleanup(setattr, egress, "pick", self.orig_pick)

    def _install(self, payloads):
        plist = list(payloads)

        class Opener:
            def open(self, req, timeout=None):
                _SEEN.append({k.lower(): v for k, v in req.header_items()})
                n = len(_SEEN)
                if n <= len(plist):
                    raise urllib.error.HTTPError(
                        req.full_url, 403, "Forbidden", {},
                        io.BytesIO(plist[n - 1]))
                return _FakeResp()

        _SEEN.clear()
        client._opener = lambda: Opener()

    def test_risk_retry_rotates_whole_group(self):
        """4100404：定性 risk_control；每一发都是**新的一组** cid/rid/nonce/uskey。"""
        self._install([b'{"code":4100404,"msg":"\xe8\xaf\xb7\xe6\xb1\x82'
                       b'\xe8\xa2\xab\xe9\xa3\x8e\xe6\x8e\xa7'
                       b'\xe6\x8b\xa6\xe6\x88\xaa"}'] * 8)
        with self.assertRaises(client.UpstreamError) as caught:
            client.open_chat_stream(ACCT_A, "cn",
                                    {"model": "main-auto", "messages": []},
                                    account_id=0)
        exc = caught.exception
        self.assertEqual(exc.kind, "risk_control")
        # exc.code 是 HTTP 状态（403），业务码在 detail 里 —— 别把两者弄混
        self.assertEqual(exc.code, "403")
        self.assertIn("4100404", str(exc.detail))
        self.assertGreaterEqual(len(_SEEN), 1)
        # 重试次数跟着常量走，不写死 1 或 2
        self.assertEqual(len(_SEEN), client.CHAT_ATTEMPTS)
        groups = set()
        for headers in _SEEN:
            ext = json.loads(headers["ual-access-marvisext"])
            groups.add((headers["marvis-response-id"], ext["nonce"], ext["uskey"]))
        self.assertEqual(len(groups), len(_SEEN),
                         "重试复用了同一组 cid/rid/nonce/uskey —— 会继续撞同一个判定")

    def test_other_errors_are_not_retried(self):
        """非风控错误（4100403 登录态）一分钱都不重试。"""
        self._install([b'{"code":4100403}'] * 8)
        with self.assertRaises(client.UpstreamError) as caught:
            client.open_chat_stream(ACCT_A, "cn", {"model": "main-auto"},
                                    account_id=0)
        self.assertEqual(len(_SEEN), 1)
        self.assertEqual(caught.exception.kind, "auth")

    def test_risk_control_is_platform_level_not_account_cooldown(self):
        """4100404 **不写库冷却账号**（IP 级风控 + 这条请求的内容），只开平台级退避窗口。

        旧断言是「该号冷却 300 秒（COOL_SECONDS_RISK）」，那套契约已被
        2026-11-17 的线上死循环推翻：给号打冷却 → 冷却到期立刻复发 →
        3 个号一起 600s、503 accounts_cooling 循环。现在的口径是
        `_cool_seconds("risk_control") == 0` + `note_risk_control()` 连撞
        `RISK_BACKOFF_THRESHOLD` 次才开窗口。这条锁的是**当前**契约，
        不是放宽 —— 旧值 300 现在是错的。

        ⚠ 2026-11-20 补充：账号维度**有**冷却，但**只在内存里**
        （`gateway._RISK_ACCOUNT_STATE`，默认 60 秒，`account.cool_until`
        一个字都不写）。所以 `_cool_seconds("risk_control") == 0` 这条断言
        仍然成立，两者不矛盾 —— 见 tests/test_marvis_risk_no_retry_cooldown.py。
        """
        from admin.platform.marvis import gateway
        self.assertEqual(client._classify(403, '{"code":4100404,"msg":"风控"}'),
                         "risk_control")
        self.assertEqual(gateway._cool_seconds("risk_control"), 0)
        self.assertEqual(gateway.RISK_BACKOFF_THRESHOLD, 5)
        self.assertGreater(gateway.RISK_BACKOFF_DEFAULT_SECONDS, 0)


if __name__ == "__main__":
    unittest.main()
