"""请求体上限的回归测试：默认值 / 0 与负数关闭 / 超限本地 413。

守住三件事：

  * 默认上限是 256 MiB —— 真实长对话（6.5 MB 级）必须能过，
    不能被旧的 4 MB 挡住；
  * `MIRAIHUB_MARVIS_MAX_BODY_BYTES=0` 或负数 = **关闭检查**，
    而不是旧实现里的「非法值退回默认」；
  * 超限时在本网关就地 413，**一次都不打上游**（不选号、不建连、不占配额）。

零网络：所有用例都不碰上游，`_acquire` 被换成「一被调用就抛」的假实现。
"""
import asyncio
import contextlib
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

# 必须早于 `admin.*` 导入：`admin/db.py` 在模块级按 settings 建引擎（默认 MySQL）。
os.environ.setdefault(
    "ADMIN_DATABASE_URL",
    "sqlite:///" + str(Path(os.environ.get("TEMP", "/tmp")) / "marvis_test.db"),
)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from starlette.requests import Request  # noqa: E402

from admin.platform.marvis import gateway  # noqa: E402

MIB = 1024 * 1024


@contextlib.contextmanager
def body_env(value):
    """把 `MAX_BODY_ENV` 临时设成 value；None = 删除，模拟「未设置」。"""
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop(gateway.MAX_BODY_ENV, None)
        if value is not None:
            os.environ[gateway.MAX_BODY_ENV] = value
        yield


def make_request(payload=b"{}", *, declared="auto", path="/v1/chat/completions"):
    """造一个 Starlette `Request`。

    declared：
      * "auto"  → 用 `len(payload)` 当 Content-Length；
      * None    → **不带** 这个头（走「读 body 再量」那条分支）；
      * 整数    → 用一个和 payload 不一致的声明值（省内存，测头分支）。
    """
    size = len(payload) if declared == "auto" else declared
    headers = [(b"content-type", b"application/json")]
    if size is not None:
        headers.append((b"content-length", str(size).encode()))
    reads = []

    async def receive():
        reads.append(1)
        return {"type": "http.request", "body": payload, "more_body": False}

    scope = {
        "type": "http", "http_version": "1.1", "method": "POST", "path": path,
        "raw_path": path.encode(), "query_string": b"", "scheme": "http",
        "server": ("testserver", 80), "client": ("127.0.0.1", 12345),
        "headers": headers,
    }
    request = Request(scope, receive=receive)
    request.body_reads = reads      # 供用例断言「body 到底有没有被读」
    return request


class DefaultLimitTests(unittest.TestCase):
    """默认值：256 MiB，且真实长对话能过。"""

    def test_env_name_is_the_documented_one(self):
        self.assertEqual(gateway.MAX_BODY_ENV, "MIRAIHUB_MARVIS_MAX_BODY_BYTES")

    def test_default_is_256_mib(self):
        with body_env(None):
            self.assertEqual(gateway.DEFAULT_MAX_BODY_BYTES, 256 * MIB)
            self.assertEqual(gateway._max_body_bytes(), 256 * MIB)
            # 旧名字保留为别名，外部脚本 / 历史代码引用不会炸
            self.assertEqual(gateway.MAX_BODY_DEFAULT_BYTES,
                             gateway.DEFAULT_MAX_BODY_BYTES)

    def test_invalid_value_falls_back_to_default(self):
        for raw in ("", "   ", "abc", "12MB"):
            with body_env(raw):
                self.assertEqual(gateway._max_body_bytes(), 256 * MIB, raw)

    def test_real_long_conversation_passes(self):
        """6.5 MB 的真实长对话在默认配置下必须放行（旧的 4 MB 会 413）。"""
        payload = b"x" * int(6.5 * MIB)
        with body_env(None):
            self.assertIsNone(
                asyncio.run(gateway._reject_oversized(make_request(payload))))


class DisableTests(unittest.TestCase):
    """0 / 负数 = 关闭检查（语义反转：旧实现把 0 当非法值退回默认）。"""

    def test_zero_disables(self):
        with body_env("0"):
            self.assertEqual(gateway._max_body_bytes(), 0)
            request = make_request(b"{}", declared=20 * MIB)
            self.assertIsNone(asyncio.run(gateway._reject_oversized(request)))
            self.assertEqual(request.body_reads, [], "关闭检查时不该读 body")

    def test_zero_no_longer_means_invalid(self):
        with body_env("0"):
            self.assertNotEqual(gateway._max_body_bytes(),
                                gateway.DEFAULT_MAX_BODY_BYTES)

    def test_negative_disables(self):
        for raw in ("-1", "-1048576", "-0.5"):
            with body_env(raw):
                self.assertLessEqual(gateway._max_body_bytes(), 0, raw)
                self.assertIsNone(asyncio.run(gateway._reject_oversized(
                    make_request(b"{}", declared=20 * MIB))), raw)


class RejectTests(unittest.TestCase):
    """超限：就地 413，且**没有**调用上游。"""

    def test_boundary_exactly_at_limit(self):
        with body_env("1024"):
            self.assertIsNone(asyncio.run(gateway._reject_oversized(
                make_request(b"x" * 1024))))
            response = asyncio.run(gateway._reject_oversized(
                make_request(b"x" * 1025)))
            self.assertEqual(response.status_code, 413)

    def test_missing_content_length_measures_body(self):
        """没有 Content-Length（chunked）时按实际字节数量。"""
        with body_env("1024"):
            allowed = make_request(b"x" * 1024, declared=None)
            self.assertIsNone(asyncio.run(gateway._reject_oversized(allowed)))
            self.assertEqual(allowed.body_reads, [1], "缺头时必须读 body 才量得出")
            rejected = make_request(b"x" * 1025, declared=None)
            self.assertEqual(
                asyncio.run(gateway._reject_oversized(rejected)).status_code, 413)

    def test_oversized_is_rejected_locally_without_upstream(self):
        with body_env("1048576"):                 # 1 MiB
            request = make_request(b"{}", declared=2 * MIB)
            acquire_calls = []
            parsed = []

            def fake_acquire(*args, **kwargs):
                acquire_calls.append((args, kwargs))
                raise AssertionError("超限请求打到了上游：_acquire 被调用")

            async def fake_body_of(request):
                parsed.append(request)
                return {}

            with mock.patch.object(gateway, "_acquire", fake_acquire), \
                    mock.patch.object(gateway, "_body_of", fake_body_of):
                response = asyncio.run(gateway.chat_completions(request))

            self.assertEqual(response.status_code, 413)
            payload = json.loads(response.body)
            error = payload["error"]
            self.assertEqual(error["type"], "invalid_request_error")
            self.assertEqual(error["code"], "body_too_large")
            self.assertIn(gateway.MAX_BODY_ENV, error["message"])
            self.assertIn("设为 0", error["message"])
            self.assertEqual(acquire_calls, [], "超限时不得选号 / 建连")
            self.assertEqual(parsed, [], "超限时不得解析 body")
            self.assertEqual(request.body_reads, [], "只看 Content-Length，不读 body")

    def test_all_three_entrypoints_reject(self):
        """chat/completions、messages、responses 三条入口都要在打上游之前拦住。"""
        for entry in ("chat_completions", "messages", "responses"):
            with body_env("1024"):
                request = make_request(b"{}", declared=4096, path="/v1/" + entry)
                acquire_calls = []

                def fake_acquire(*args, **kwargs):
                    acquire_calls.append(entry)
                    raise AssertionError("超限请求打到了上游：" + entry)

                with mock.patch.object(gateway, "_acquire", fake_acquire):
                    response = asyncio.run(getattr(gateway, entry)(request))
                self.assertEqual(response.status_code, 413, entry)
                self.assertEqual(
                    json.loads(response.body)["error"]["code"], "body_too_large", entry)
                self.assertEqual(acquire_calls, [], entry)


if __name__ == "__main__":
    unittest.main()
