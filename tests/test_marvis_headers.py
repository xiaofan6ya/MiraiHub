"""腾讯 Marvis：Ual-Access-* 请求头对齐真客户端抓包（PR-05 回归）。

这些断言锁的都是「值错了上游只回 4100404、不告诉你是哪个字段错」的地方：

  * `Ual-Access-Login-Type`：模型网关（`/v1/chat/completions`）发 6，
    业务接口（`/v3/*`）才是 QC→1 / WX→2；
  * `Ual-Access-Requestid`：必须存在，且与 `Marvis-Response-ID` 同源；
  * `Ual-Access-MarvisExt`：键序与取值必须与真机抓包一致（reqScene=0、
    osVersion=.29695、带 clientPlatform / clientPlatformVersion）。

测试里的账号字段与设备串全是占位值：openid / guid / token 用 `*-test`，
设备串用 36 个 0，不含任何真实账号或真实设备身份。
"""
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from admin.platform.marvis.core import endpoints  # noqa: E402

#: 账号占位：全部 test 值，不含真实 openid / guid / token。
ACCT = {"openid": "openid-test", "access_token": "token-test",
        "guid": "guid-test", "login_type": "QC"}

#: 合成设备串（36 个 hex）：qimei 只从 MARVIS_QIMEI36 取，测试给的是占位值。
QIMEI = "0" * 36

#: 固定身份四件套：测试不真签 uskey（DLL / 侧车 / 本地 mint 都不参与）。
IDENTITY = {"uskey": "uskey-test", "nonce": "nonce-test",
            "conversation_id": "conv-test", "response_id": "rid-test",
            "client_version": "1.60.3400.35", "cached": False}

#: 真机 200 抓包的键序（MarvisExt 按原文比对，顺序不能变）。
EXT_KEY_ORDER = ["qimei36", "osVersion", "guid", "uskey", "nonce", "reqScene",
                 "taskType", "clientVersion", "clientPlatform",
                 "clientPlatformVersion", "isIoa"]


class PatchedTestCase(unittest.TestCase):
    """钉住签名入口与环境变量：测试不依赖 DLL、侧车、真实设备串。"""

    def setUp(self):
        env = mock.patch.dict(os.environ, {"MARVIS_QIMEI36": QIMEI})
        env.start()
        self.addCleanup(env.stop)
        ident = mock.patch.object(
            endpoints, "uskey_identity", return_value=dict(IDENTITY))
        ident.start()
        self.addCleanup(ident.stop)


class LoginTypeTests(PatchedTestCase):
    def test_qc_maps_to_1(self):
        self.assertEqual(endpoints.login_type_header({"login_type": "QC"}), "1")

    def test_wx_maps_to_2(self):
        self.assertEqual(endpoints.login_type_header({"login_type": "WX"}), "2")
        self.assertEqual(endpoints.login_type_header({"login_type": "wxapp"}), "2")

    def test_numeric_passthrough(self):
        self.assertEqual(endpoints.login_type_header({"login_type": "6"}), "6")

    def test_unknown_and_empty_fall_back_to_1(self):
        for raw in ("", None, "weird"):
            self.assertEqual(
                endpoints.login_type_header({"login_type": raw}), "1", repr(raw))
        self.assertEqual(endpoints.login_type_header({}), "1")

    def test_llm_headers_use_the_llm_value(self):
        headers = endpoints.llm_headers(ACCT)
        self.assertEqual(headers["Ual-Access-Login-Type"],
                         endpoints.LOGIN_TYPE_LLM)
        self.assertEqual(headers["Ual-Access-Login-Type"], "6")

    def test_business_headers_use_the_mapped_value(self):
        self.assertEqual(
            endpoints.business_headers(ACCT, query="{}")["Ual-Access-Login-Type"],
            "1")
        wx = dict(ACCT, login_type="WX")
        self.assertEqual(
            endpoints.business_headers(wx, query="{}")["Ual-Access-Login-Type"],
            "2")


class LlmRequestIdTests(PatchedTestCase):
    def test_requestid_equals_response_id(self):
        headers = endpoints.llm_headers(ACCT)
        self.assertEqual(headers["Ual-Access-Requestid"], IDENTITY["response_id"])
        self.assertEqual(headers["Marvis-Response-ID"], IDENTITY["response_id"])
        self.assertEqual(headers["Ual-Access-Requestid"],
                         headers["Marvis-Response-ID"])

    def test_conversation_id_comes_from_the_same_identity(self):
        headers = endpoints.llm_headers(ACCT)
        self.assertEqual(headers["Marvis-Conversation-ID"],
                         IDENTITY["conversation_id"])

    def test_ual_access_header_set(self):
        headers = endpoints.llm_headers(ACCT)
        for key in ("Ual-Access-Openid", "Ual-Access-Access-Token",
                    "Ual-Access-Login-Type", "Ual-Access-Guid",
                    "Ual-Access-Requestid", "Ual-Access-MarvisExt",
                    "Marvis-Conversation-ID", "Marvis-Response-ID",
                    "Marvis-Protocol-Version"):
            self.assertIn(key, headers)

    def test_identity_is_signed_once_per_request(self):
        """uskey 与 conv/rid 必须出自同一次 uskey_identity()。"""
        with mock.patch.object(endpoints, "uskey_identity",
                               return_value=dict(IDENTITY)) as signer:
            endpoints.llm_headers(ACCT)
        self.assertEqual(signer.call_count, 1)


class MarvisExtTests(PatchedTestCase):
    def _ext(self, acct=None):
        raw = endpoints.marvis_ext(acct or ACCT, identity=dict(IDENTITY))
        self.assertNotIn(" ", raw, "含空格会被上游按原文比对判无效")
        return json.loads(raw)

    def test_key_order_matches_capture(self):
        self.assertEqual(list(self._ext().keys()), EXT_KEY_ORDER)

    def test_values_match_capture(self):
        ext = self._ext()
        self.assertEqual(ext["osVersion"], "Windows-10-10.0.29695")
        self.assertEqual(ext["reqScene"], 0)
        self.assertEqual(ext["taskType"], "user")
        self.assertEqual(ext["clientVersion"], "1.60.3400.795")
        self.assertEqual(ext["clientPlatform"], "Windows")
        self.assertEqual(ext["clientPlatformVersion"], "1.60.3400.35")
        self.assertIs(ext["isIoa"], False)
        self.assertEqual(ext["uskey"], IDENTITY["uskey"])
        self.assertEqual(ext["nonce"], IDENTITY["nonce"])
        self.assertEqual(ext["guid"], ACCT["guid"])

    def test_qimei36_is_the_env_value_not_derived_from_account(self):
        first = self._ext()
        second = self._ext({"openid": "openid-other", "guid": "guid-other"})
        self.assertEqual(first["qimei36"], QIMEI)
        self.assertEqual(second["qimei36"], QIMEI,
                         "设备串不得按账号派生（只认 MARVIS_QIMEI36）")

    def test_qimei36_key_omitted_when_env_missing(self):
        env = {k: v for k, v in os.environ.items() if k != "MARVIS_QIMEI36"}
        with mock.patch.dict(os.environ, env, clear=True):
            ext = self._ext()
        self.assertNotIn("qimei36", ext, "没配设备串时不要编一个值")
        self.assertEqual(ext["osVersion"], "Windows-10-10.0.29695")


if __name__ == "__main__":
    unittest.main(verbosity=2)
