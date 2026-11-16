# -*- coding: utf-8 -*-
"""事件循环不被上游阻塞的回归测试。

## 事故背景（2026-11-05 线上）

控制台「导入账号」点了没反应，实际是**整个服务**都不响应：单进程
uvicorn 的事件循环被 Loomy 网关的同步 `urlopen` 钉死（py-spy 抓到的栈
是 `_request → ssl.read`），`DEFAULT_TIMEOUT` 是 600 秒，最坏要等
10 分钟才自己恢复。用户看到的现象是「导入卡死」。

本测试用「慢上游」复现该场景：把 `_attempt` / `client.chat_stream`
换成 sleep 的函数，然后检查**同一时间**事件循环还能不能正常调度。
改造前这段断言必然失败（drift ≈ sleep 时长），改造后 drift 接近 0。
"""
import asyncio
import pathlib
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from admin.platform.loomy import gateway as gw  # noqa: E402

SLOW = 1.2          # 模拟卡住的上游
DRIFT_BUDGET = 0.5  # 允许的调度抖动（远小于 SLOW）


class _FakeClient:
    host = "127.0.0.1"


class _FakeRequest:
    """只提供 `_realm` / `_client_ip` 需要的最小表面。"""

    def __init__(self):
        self.headers = {}
        self.query_params = {}
        self.client = _FakeClient()
        self.url = "http://test.local/v1/chat/completions"


def _measure_drift(coro_factory):
    """跑一个会阻塞 SLOW 秒的协程，返回事件循环的最大调度延迟。"""

    async def scenario():
        task = asyncio.create_task(coro_factory())
        worst = 0.0
        deadline = time.perf_counter() + SLOW
        # 只要事件循环没被占住，这个循环每 20ms 就能转一圈
        while time.perf_counter() < deadline:
            t0 = time.perf_counter()
            await asyncio.sleep(0.02)
            worst = max(worst, time.perf_counter() - t0 - 0.02)
        await task
        return worst

    return asyncio.run(scenario())


class LoopNotBlockedTests(unittest.TestCase):
    def test_forward_runs_upstream_off_loop(self):
        """非流式转发：慢上游不能占住事件循环。"""
        def slow_attempt(db, request, key, body, realm):
            time.sleep(SLOW)
            return ({"choices": [{"message": {"content": "ok"}}]},
                    object(), time.perf_counter(),
                    {"ttfb_ms": 1, "total_ms": 2})

        def noop(*a, **kw):
            return None

        with mock.patch.object(gw.shared, "authenticate",
                                        lambda *a, **kw: object()), \
                mock.patch.object(gw, "_attempt", slow_attempt), \
                mock.patch.object(gw, "_settle", noop), \
                mock.patch.object(gw, "_bill", noop):
            drift = _measure_drift(
                lambda: gw._forward({"model": "m", "messages": []},
                                    _FakeRequest()))
        self.assertLess(
            drift, DRIFT_BUDGET,
            "事件循环被上游阻塞了 %.2fs —— 同步 urlopen 又跑回主线程了" % drift)

    def test_stream_open_runs_off_loop(self):
        """流式建连（urlopen 等响应头）同样不能占住事件循环。"""
        def slow_open(db, request, key, body, realm, stats, model):
            time.sleep(SLOW)
            # `_open_stream` 返回 5 元组（chunks, account, last_error,
            # started, served_model）。这里必须跟着改，否则
            # `_forward_stream` 解包就 ValueError —— 测出来的「阻塞」
            # 其实是解包失败，属于假阳性。
            return iter(()), object(), None, time.perf_counter(), model

        def noop(*a, **kw):
            return None

        with mock.patch.object(gw.shared, "authenticate",
                                        lambda *a, **kw: object()), \
                mock.patch.object(gw, "_open_stream", slow_open), \
                mock.patch.object(gw, "_bill", noop):
            drift = _measure_drift(
                lambda: gw._forward_stream({"model": "m", "messages": []},
                                           _FakeRequest()))
        self.assertLess(
            drift, DRIFT_BUDGET,
            "流式建连阻塞了事件循环 %.2fs" % drift)

    def test_attempt_returns_ttfb_stats(self):
        """TTFB 必须由工作线程带回，否则记账会退化成 ttfb==latency。"""
        class _Account:
            id = 7

        class _Stored:
            pass

        def noop(*a, **kw):
            return None

        def fake_complete(*a, **kw):
            return {"choices": []}

        with mock.patch.object(gw.shared, "select_account",
                                        lambda *a, **kw: _Account()), \
                mock.patch.object(gw.shared, "lease",
                                           lambda acc: True), \
                mock.patch.object(gw.loomy_accounts, "ensure_token",
                                           lambda db, acc: _Stored()), \
                mock.patch.object(gw.loomy_accounts, "token_of",
                                           lambda data: "tok"), \
                mock.patch.object(gw.client, "chat_completions",
                                           fake_complete):
            _result, _account, _started, stats = gw._attempt(
                None, _FakeRequest(), object(), {"model": "m"}, "cn")
        self.assertIn("ttfb_ms", stats)
        self.assertIn("total_ms", stats)


class MarvisLoopTests(unittest.TestCase):
    """Marvis 网关（2026-11-16 事故）：`urlopen` 不能跑在事件循环上。

    现场栈两条，同源：

        http/client.py getresponse → ssl.read                  （13.5 秒）
        urllib/request.py https_open → ssl._load_windows_store_certs （13.3 秒）

    第一条：`chat_completions` 里同步调 `_attempt`（选号 + `urlopen` 等响应头）。
    第二条：裸 `urlopen` 给每条新连接重建默认 SSL 上下文，Windows 上要重扫
    证书库。单 worker 的 uvicorn 被钉住 → 控制台 / 导入 / 全部 /v1 一起无响应。
    """

    @staticmethod
    def _request(payload: dict):
        class _Req:
            headers = {}
            query_params = {}
            client = _FakeClient()

            async def json(self):
                return payload

        return _Req()

    def test_chat_completions_offloads_upstream(self):
        """非流式：鉴权 + 选号 + 建连必须离开事件循环。"""
        from admin.platform.marvis import gateway as mgw

        def slow_acquire(request, body, realm, *, stream):
            time.sleep(SLOW)
            return (object(), {"choices": [{"message": {"content": "ok"}}]},
                    object(), time.perf_counter())

        async def noop_bill(*_a, **_kw):
            return None

        with mock.patch.object(mgw, "_validate_model", lambda body: "main-auto"), \
                mock.patch.object(mgw, "_acquire", slow_acquire), \
                mock.patch.object(mgw, "_bill_async", noop_bill):
            drift = _measure_drift(
                lambda: mgw.chat_completions(
                    self._request({"model": "deepseek-v4.1-flash",
                                   "messages": [{"role": "user", "content": "hi"}]})))
        self.assertLess(
            drift, DRIFT_BUDGET,
            "Marvis 非流式把事件循环钉住 %.2fs —— 同步 urlopen 又回到主线程了" % drift)

    def test_stream_open_offloads_upstream(self):
        """流式：`urlopen` 等响应头同样不能占住事件循环。"""
        from admin.platform.marvis import gateway as mgw

        class _Resp:
            def read(self, _size=-1):
                return b""

            def close(self):
                return None

        def slow_acquire(request, body, realm, *, stream):
            self.assertTrue(stream, "流式请求必须走 stream=True 的建连分支")
            time.sleep(SLOW)
            return object(), _Resp(), object(), time.perf_counter()

        async def noop_bill(*_a, **_kw):
            return None

        with mock.patch.object(mgw, "_validate_model", lambda body: "main-auto"), \
                mock.patch.object(mgw, "_acquire", slow_acquire), \
                mock.patch.object(mgw, "_bill_async", noop_bill):
            drift = _measure_drift(
                lambda: mgw.chat_completions(
                    self._request({"model": "deepseek-v4.1-flash", "stream": True,
                                   "messages": [{"role": "user", "content": "hi"}]})))
        self.assertLess(
            drift, DRIFT_BUDGET,
            "Marvis 流式建连阻塞了事件循环 %.2fs" % drift)

    def test_count_tokens_is_callable(self):
        """`dispatch()` 会调 `count_tokens()` —— 它以前是接在 return 后的死代码。"""
        from admin.platform.marvis import gateway as mgw

        self.assertTrue(callable(getattr(mgw, "count_tokens", None)),
                        "count_tokens 不存在，/v1/messages/count_tokens 会 NameError")


class SslContextReuseTests(unittest.TestCase):
    """默认 HTTPS 上下文必须共享，否则每条新连接都要重扫 Windows 证书库。"""

    ROOT = pathlib.Path(__file__).resolve().parent.parent

    def test_default_https_context_is_shared(self):
        import ssl

        from admin import upstream_http

        fake = object()
        with mock.patch.object(upstream_http, "_ssl_context", fake), \
                mock.patch.object(upstream_http, "_SSL_DEFAULT_INSTALLED", False):
            self.assertTrue(upstream_http.install_ssl_defaults())
            first = ssl._create_default_https_context()
            second = ssl._create_default_https_context()
        self.assertIs(first, fake, "默认上下文必须来自共享的 ssl_context()")
        self.assertIs(first, second,
                      "两次新建连接拿到两个上下文 —— 等于每次重扫系统证书库")

    def test_marvis_client_uses_shared_opener(self):
        """静态检查：Marvis 客户端不许再出现裸 `urllib.request.urlopen(`。"""
        source = (self.ROOT / "admin" / "platform" / "marvis" / "core"
                  / "client.py").read_text(encoding="utf-8")
        self.assertNotIn("urllib.request.urlopen(", source,
                         "裸 urlopen 会给每条连接重建 SSL 上下文（3~13 秒/次）")
        self.assertIn("_opener()", source)


class LoopWatchTests(unittest.TestCase):
    """探针本身要能报出「谁把主线程占了」，否则下次还得装 py-spy。"""

    def test_watchdog_dumps_stack_when_loop_blocked(self):
        import logging
        from admin import loop_watch

        records = []

        class _Collect(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = _Collect()
        log = logging.getLogger("admin.loop_watch")
        log.addHandler(handler)
        level = log.level
        log.setLevel(logging.DEBUG)

        async def scenario():
            loop_watch.start()
            time.sleep(0.8)          # 人为钉死事件循环
            await asyncio.sleep(0.3)  # 让探针线程完成判定与日志

        try:
            with mock.patch.object(loop_watch, "BEAT", 0.05), \
                    mock.patch.object(loop_watch, "THRESHOLD", 0.3), \
                    mock.patch.object(loop_watch, "WARMUP", 0.0), \
                    mock.patch.object(loop_watch, "DUMP_GAP", 0.0):
                asyncio.run(scenario())
                asyncio.run(loop_watch.stop())
        finally:
            log.removeHandler(handler)
            log.setLevel(level)

        hits = [r for r in records if "卡死" in r.getMessage()]
        self.assertTrue(hits, "事件循环被钉死 0.8s，探针却没报警")
        self.assertIn("File", hits[0].getMessage(),
                      "报警里必须带调用栈，否则等于没说")


if __name__ == "__main__":
    unittest.main(verbosity=2)
