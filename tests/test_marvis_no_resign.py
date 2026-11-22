"""持久回归测试：HTTP 403 / 4100404 不重发、不自动重新签名。

真实执行 client 的 HTTP 错误边界和 endpoints 的请求头构造；账号、签名、
设备与出口均为合成值，不启动 admin app、不加载 DLL、不读写诊断缓存。

运行：python -B -m unittest discover -s tests -p test_marvis_no_resign.py
"""
import importlib
import io
import json
import sys
import types
import unittest
import urllib.error
from contextlib import ExitStack
from email.message import Message
from pathlib import Path
from unittest import mock


def _load_client():
    """私有包加载相对导入，避开 admin/__init__.py 的应用启动副作用。"""
    name = "_marvis_no_resign_regression"
    package = types.ModuleType(name)
    package.__path__ = [str(Path(__file__).resolve().parents[1]
                            / "admin/platform/marvis/core")]
    sys.modules[name] = package
    try:
        return importlib.import_module(name + ".client")
    finally:
        for module_name in tuple(sys.modules):
            if module_name == name or module_name.startswith(name + "."):
                del sys.modules[module_name]


client = _load_client()
endpoints = client.endpoints

ACCOUNT_ID = 17
CREDENTIAL = {"openid": "openid-synthetic", "access_token": "token-synthetic",
              "guid": "guid-synthetic", "login_type": "QC"}
CHAT_URL = "https://marvis.invalid/v1/chat/completions"
CONVERSATION_ID = "00000000-0000-4000-8000-000000000017"
SYNTHETIC_QIMEI = "0" * 24 + "3000" + "0" * 8
RISK_TEXT = '{"code":4100404,"msg":"synthetic risk rejection"}'


class NoResignOnRiskRegressionTests(unittest.TestCase):
    """一次上层请求只生成一组身份、只发送一次，业务风控不回退出口。"""

    def _assert_risk_is_not_replayed(self, proxy):
        body = {"model": "main-auto", "stream": True,
                "messages": [{"role": "user", "content": "synthetic request"}]}
        sent = []

        def synthetic_identity(_acct, **kwargs):
            return {"uskey": "uskey-synthetic", "nonce": "nonce-synthetic",
                    "conversation_id": kwargs["conversation_id"],
                    "response_id": kwargs["response_id"],
                    "client_version": "1.60.3400.35", "cached": False}

        def reject(request, **kwargs):
            sent.append((client._EGRESS_VAR.get(), request, kwargs))
            headers = Message()
            headers["Content-Type"] = "application/json"
            raise urllib.error.HTTPError(
                request.full_url, 403, "Forbidden", headers,
                io.BytesIO(RISK_TEXT.encode("utf-8")))

        opener = mock.Mock()
        opener.open.side_effect = reject
        before = client._EGRESS_VAR.get()
        with ExitStack() as stack:
            stack.enter_context(mock.patch.dict(
                "os.environ", {"MARVIS_DEBUG_HEADERS": "0"}))
            choose_proxy = stack.enter_context(mock.patch.object(
                client, "_egress_proxy", return_value=proxy))
            get_opener = stack.enter_context(mock.patch.object(
                client, "_opener_for", return_value=opener))
            stack.enter_context(mock.patch.object(client, "note_sentinel"))
            stack.enter_context(mock.patch.object(
                client, "conversation_id_for", return_value=CONVERSATION_ID))
            stack.enter_context(mock.patch.object(
                endpoints, "chat_url", return_value=CHAT_URL))
            stack.enter_context(mock.patch.object(
                endpoints, "ext_qimei36", return_value=SYNTHETIC_QIMEI))
            signer = stack.enter_context(mock.patch.object(
                endpoints, "uskey_identity", side_effect=synthetic_identity))

            with self.assertRaises(client.UpstreamError) as caught:
                client.open_chat_stream(
                    CREDENTIAL, "cn", body, account_id=ACCOUNT_ID)

        error = caught.exception
        self.assertEqual((error.status, error.code, error.kind),
                         (403, "403", "risk_control"))
        self.assertEqual(error.detail, RISK_TEXT)
        signer.assert_called_once()
        choose_proxy.assert_called_once_with(ACCOUNT_ID)
        get_opener.assert_called_once_with(proxy)
        opener.open.assert_called_once()
        self.assertEqual(len(sent), 1)
        bound_proxy, request, kwargs = sent[0]
        self.assertEqual(bound_proxy, proxy)
        self.assertEqual(client._EGRESS_VAR.get(), before)
        self.assertEqual(request.full_url, CHAT_URL)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(json.loads(request.data), body)
        self.assertEqual(kwargs, {"timeout": client.STREAM_TIMEOUT})
        headers = {key.lower(): value for key, value in request.header_items()}
        ext = json.loads(headers["ual-access-marvisext"])
        self.assertEqual(ext["uskey"], "uskey-synthetic")
        self.assertEqual(ext["nonce"], "nonce-synthetic")
        self.assertEqual(headers["marvis-conversation-id"], CONVERSATION_ID)
        self.assertEqual(headers["marvis-response-id"],
                         signer.call_args.kwargs["response_id"])
        self.assertEqual(signer.call_args.kwargs["model"], "main-auto")

    def test_direct_403_4100404_is_not_resent_or_resigned(self):
        self._assert_risk_is_not_replayed("")

    def test_egress_bound_403_4100404_does_not_resign_or_fallback(self):
        self._assert_risk_is_not_replayed("http://egress-synthetic.invalid:8080")


if __name__ == "__main__":
    unittest.main()
