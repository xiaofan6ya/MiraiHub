"""请求体大小限制关闭（2026-11-24）：compose 默认 0、dispatch 预读上限可设 0。"""
import asyncio
import os
import re
import unittest
from unittest import mock

from admin.platform import dispatch
from admin.platform.marvis import gateway as marvis_gateway

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _receive_from(chunks):
    msgs = [{"type": "http.request", "body": c, "more_body": i < len(chunks) - 1}
            for i, c in enumerate(chunks)]

    async def receive():
        return msgs.pop(0)
    return receive


class _Req:
    def __init__(self, headers, method="POST"):
        self.scope = {"method": method,
                      "headers": [(k.encode(), v.encode()) for k, v in headers.items()]}


class ComposeDefaults(unittest.TestCase):
    def test_compose_defaults_are_unlimited(self):
        with open(os.path.join(ROOT, "docker-compose.yml"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertRegex(text, r"MIRAIHUB_MARVIS_MAX_BODY_BYTES:\s*\$\{MIRAIHUB_MARVIS_MAX_BODY_BYTES:-0\}")
        self.assertRegex(text, r"MIRAIHUB_DISPATCH_PEEK_MAX_BYTES:\s*\$\{MIRAIHUB_DISPATCH_PEEK_MAX_BYTES:-0\}")
        self.assertNotIn("6291456}", text)

    def test_nginx_v1_has_no_body_cap(self):
        with open(os.path.join(ROOT, "deploy", "nginx-prod.conf"), encoding="utf-8") as fh:
            text = fh.read()
        block = re.search(r"location /v1/ \{(.*?)\n    \}", text, re.S).group(1)
        self.assertIn("client_max_body_size 0;", block)


class MarvisZeroDisables(unittest.TestCase):
    def test_zero_means_off(self):
        with mock.patch.dict(os.environ, {marvis_gateway.MAX_BODY_ENV: "0"}):
            self.assertLessEqual(marvis_gateway._max_body_bytes(), 0)


class DispatchPeekCap(unittest.TestCase):
    def test_env_parsing(self):
        cases = {None: dispatch._DEFAULT_BODY_HARD_CAP, "": dispatch._DEFAULT_BODY_HARD_CAP,
                 "0": 0, "-1": -1, "1048576": 1048576, "abc": dispatch._DEFAULT_BODY_HARD_CAP}
        for raw, want in cases.items():
            env = {} if raw is None else {dispatch.PEEK_MAX_ENV: raw}
            with mock.patch.dict(os.environ, env, clear=False):
                if raw is None:
                    os.environ.pop(dispatch.PEEK_MAX_ENV, None)
                self.assertEqual(dispatch._peek_cap_from_env(), want, raw)

    def test_read_body_unlimited_when_zero(self):
        body = asyncio.run(dispatch._read_body(_receive_from([b"x" * 40, b"y" * 40]), 0))
        self.assertEqual(len(body), 80)

    def test_read_body_still_limits_when_positive(self):
        with self.assertRaises(dispatch.BodyTooLarge):
            asyncio.run(dispatch._read_body(_receive_from([b"x" * 40, b"y" * 40]), 32))

    def test_should_peek_large_declared_when_zero(self):
        req = _Req({"content-length": str(64 * 1024 * 1024)})
        with mock.patch.object(dispatch, "_BODY_HARD_CAP", 0):
            self.assertTrue(dispatch._should_peek(req))
        with mock.patch.object(dispatch, "_BODY_HARD_CAP", 32):
            self.assertFalse(dispatch._should_peek(req))


if __name__ == "__main__":
    unittest.main()
