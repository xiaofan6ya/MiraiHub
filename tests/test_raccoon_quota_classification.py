"""小浣熊额度错误分类：400 + 字符串业务码的回归测试。

## 锁的是什么

真机上游（2026-11）对余额耗尽返回的是 **HTTP 400 + 字符串码**：

    {"error":{"code":"insufficient_points",
              "message":"积分余额不足，请充值后重试",
              "type":"invalid_request_error"}}

旧分类器三个特征全踩空：`_code_of()` 把字符串码 `int()` 成 None、
`status == 402` 不成立、文案「积分余额不足」既不含「额度」也不含连续
子串「积分不足」。于是 `kind=upstream`：

  * `gateway._cool_seconds` 对 upstream 返回 0 → 空号不冷却；
  * `gateway._attempt` 对 400 + 非(auth/quota/rate) 立即 break 不换号。

结果同一个空号被反复选中、每次 400 —— 这才是「小浣熊经常无法使用」的
根因，不是网络问题。下面每条断言都对应一个「改回去就静默复现」的点：

  1. 真机原样 400 → quota（字符串码路径）；
  2. 英文 insufficient points → quota（文案路径）；
  3. 参数错的 400 → **不是** quota（禁止把 400 一概归额度）；
  4. 402 + 整数码仍 quota、401 仍 auth（不回归）；
  5. 脱敏短句里没有额度字样、只有 raw 有 → 仍 quota（本次 bug 的直接锁）。
"""
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

# 必须早于 `admin.*` 导入：`admin/db.py` 在模块级按 settings 建引擎。
os.environ.setdefault(
    "ADMIN_DATABASE_URL",
    "sqlite:///" + str(Path(os.environ.get("TEMP", "/tmp"))
                       / "raccoon_quota_test.db"),
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from admin.platform.raccoon.core import client  # noqa: E402


def _upstream_quota_payload():
    """真机抓到的额度不足响应体（逐字段照抄）。"""
    return {"error": {"code": "insufficient_points",
                      "message": "积分余额不足，请充值后重试",
                      "param": None,
                      "type": "invalid_request_error"}}


class _FakeResponse:
    def __init__(self, status, body: dict):
        self.status_code = status
        self.text = json.dumps(body, ensure_ascii=False)
        self.content = self.text.encode("utf-8")
        self.headers = {"content-type": "application/json"}

    def read(self):
        return self.content

    def close(self):
        pass


class _FakeHttpClient:
    """顶替 `httpx.Client`：`request_json` 只用 request + 上下文管理。"""

    def __init__(self, response):
        self._response = response

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def request(self, method, url, headers=None, json=None, params=None):
        return self._response


class _FakeStreamClient:
    """顶替 `httpx.Client`：`open_stream` 走 build_request/send。"""

    def __init__(self, response):
        self._response = response

    def build_request(self, *args, **kwargs):
        return object()

    def send(self, request, stream=True):
        return self._response

    def close(self):
        pass


def _call_request_json(status: int, body: dict):
    """用假 httpx 走一遍 `request_json`，拿它抛出的 UpstreamError。"""
    fake = _FakeHttpClient(_FakeResponse(status, body))
    with mock.patch.object(client.httpx, "Client", lambda *a, **k: fake):
        return client.request_json(
            "POST", "https://example.invalid/chat/completions",
            token="tok-secret", account_id=153)


class RealUpstreamQuotaTests(unittest.TestCase):
    """真机形态：HTTP 400 + `code="insufficient_points"`。"""

    def test_400_insufficient_points_is_quota(self):
        with self.assertRaises(client.UpstreamError) as ctx:
            _call_request_json(400, _upstream_quota_payload())
        exc = ctx.exception
        self.assertEqual(exc.kind, "quota")
        # 字符串码在整数路径上必然是 None —— 旧实现就丢在这里
        self.assertIsNone(exc.code)
        self.assertEqual(exc.status, 400)
        # 对外短句必须保持脱敏，不得回显上游原文
        self.assertEqual(exc.message, "raccoon#153 上游错误（HTTP 400）")
        self.assertNotIn("积分", exc.message)
        # 原文只进 detail（日志侧，供排障）
        self.assertIn("insufficient_points", exc.detail)

    def test_english_insufficient_points_is_quota(self):
        body = {"error": {"code": "insufficient_points",
                          "message": "insufficient points, please top up",
                          "type": "invalid_request_error"}}
        with self.assertRaises(client.UpstreamError) as ctx:
            _call_request_json(400, body)
        self.assertEqual(ctx.exception.kind, "quota")

    def test_string_code_alone_is_enough_case_insensitive(self):
        """文案完全判不了时，字符串码自己也要能定性（且不区分大小写）。

        两层都要断言：走 request_json 时大小写靠抽取器归一；直接调
        _classify 时靠分类器自己归一 —— 只锁前一层的话，把归一化从
        分类器里删掉不会有任何测试变红。
        """
        body = {"error": {"code": "Insufficient_Points",
                          "message": "请求失败",
                          "type": "invalid_request_error"}}
        with self.assertRaises(client.UpstreamError) as ctx:
            _call_request_json(400, body)
        self.assertEqual(ctx.exception.kind, "quota")
        # 直接调用（不经抽取器）同样必须认大小写
        self.assertEqual(
            client._classify(None, 400, "", code_str="Insufficient_Points"),
            "quota")
        self.assertEqual(
            client._classify(None, 400, "", code_str="  INSUFFICIENT_QUOTA  "),
            "quota")

    def test_400_ordinary_param_error_stays_upstream(self):
        """参数错的 400 不能被吞成额度：否则会误冷却健康号、还不重试。"""
        body = {"error": {"code": "invalid_parameter",
                          "message": "content 不能为空",
                          "type": "invalid_request_error"}}
        with self.assertRaises(client.UpstreamError) as ctx:
            _call_request_json(400, body)
        exc = ctx.exception
        self.assertEqual(exc.kind, "upstream")
        self.assertNotEqual(exc.kind, "quota")

    def test_400_max_tokens_error_is_not_quota_nor_auth(self):
        """锁住 `_classify` 注释里的老坑：含 token 的参数错不能判 auth/quota。"""
        body = {"error": {"code": "invalid_request_error",
                          "message": "maximum context length is 8192 tokens",
                          "type": "invalid_request_error"}}
        with self.assertRaises(client.UpstreamError) as ctx:
            _call_request_json(400, body)
        self.assertEqual(ctx.exception.kind, "upstream")

    def test_stream_path_400_insufficient_points_is_quota(self):
        """流式是 chat 的主路径，同样必须判 quota。"""
        fake = _FakeStreamClient(_FakeResponse(400, _upstream_quota_payload()))
        with mock.patch.object(client.httpx, "Client", lambda *a, **k: fake):
            with self.assertRaises(client.UpstreamError) as ctx:
                client.open_stream(
                    "https://example.invalid/chat/completions", token="tok")
        self.assertEqual(ctx.exception.kind, "quota")


class LegacyClassificationTests(unittest.TestCase):
    """向后兼容：整数码 / 状态码的老口径不能回归。"""

    def test_integer_quota_codes_still_quota(self):
        self.assertEqual(client._classify(402, 400, ""), "quota")
        self.assertEqual(client._classify(40201, 400, ""), "quota")
        self.assertEqual(client._classify(None, 402, ""), "quota")

    def test_401_and_forbidden_still_auth(self):
        self.assertEqual(client._classify(None, 401, ""), "auth")
        self.assertEqual(client._classify(None, 403, ""), "auth")
        self.assertEqual(client._classify(40101, 400, ""), "auth")

    def test_quota_string_code_set_covers_all_known_spellings(self):
        for code in ("insufficient_points", "insufficient_quota",
                     "insufficient_balance", "quota_exceeded", "no_quota"):
            self.assertEqual(client._classify(None, 400, "", code_str=code),
                             "quota", code)

    def test_429_still_rate_and_404_still_model(self):
        self.assertEqual(client._classify(None, 429, ""), "rate")
        self.assertEqual(client._classify(None, 404, ""), "model")


class RawTextRegressionLockTests(unittest.TestCase):
    """分类必须看上游原文：脱敏短句里没有额度语义。"""

    def test_short_message_plus_raw_text_is_quota(self):
        kind = client._classify(None, 400, "raccoon#153 上游错误（HTTP 400）",
                                raw="积分余额不足，请充值后重试")
        self.assertEqual(kind, "quota")

    def test_short_message_alone_is_not_quota(self):
        """短句里没有额度字样 —— 这正是必须单传 raw 的原因，
        同时也锁住「不得把 400 一概归 quota」。"""
        kind = client._classify(None, 400, "raccoon#153 上游错误（HTTP 400）")
        self.assertEqual(kind, "upstream")

    def test_unwrap_envelope_string_code_is_quota(self):
        """信封路径（HTTP 200 + 业务错误）也要认字符串码。"""
        payload = {"code": "insufficient_points",
                   "message": "积分余额不足，请充值后重试"}
        with self.assertRaises(client.UpstreamError) as ctx:
            client.unwrap(payload, status=400)
        exc = ctx.exception
        self.assertEqual(exc.kind, "quota")
        self.assertNotIn("积分", exc.message)

    def test_code_and_raw_extractors(self):
        payload = _upstream_quota_payload()
        self.assertIsNone(client._code_of(payload))
        self.assertEqual(client._code_string_of(payload), "insufficient_points")
        self.assertEqual(client._raw_message_of(payload),
                         "积分余额不足，请充值后重试")
        # 整数码没有字符串码；字符串码输入也不应被 _code_of 误读
        self.assertIsNone(client._code_string_of({"code": 0}))
        self.assertIsNone(client._code_string_of({"error": {"code": 402}}))


if __name__ == "__main__":
    unittest.main()
