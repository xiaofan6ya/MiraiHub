#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""4100404（风控拦截）的平台级退避语义回归。

锁定三件事（2026-11-18 全站 503 事故之后的行为）：

  1. **单次** 4100404 只是「这一条请求」失败：不给账号打冷却、不换号，
     客户端拿到可重试的 503，平台**不**进入退避；
  2. 窗口内**连续**撞满阈值才进入平台级退避，退避期间请求在选号之前就被拒，
     一个字节都不打上游；
  3. 上游成功一次就把连击计数清零 —— 阈值判的是「连续」，不是历史累计。

不联网、不连库：选号 / 租约 / 上游调用 / 记账全部替换成本地假实现。

运行：
  python tests/test_marvis_risk_backoff.py
  python -m pytest -q tests/test_marvis_risk_backoff.py
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(ROOT))

from admin.platform import shared  # noqa: E402
from admin.platform.marvis import gateway  # noqa: E402
from admin.platform.marvis.core import client as marvis_client  # noqa: E402


def _risk_error() -> marvis_client.UpstreamError:
    """上游 403 的 4100404 在网关里就是 `kind="risk_control"`。"""
    return marvis_client.UpstreamError(
        "模型网关失败（HTTP 403）", status=403, code="4100404",
        kind="risk_control",
        detail='{"code":4100404,"msg":"客户端通用错误:请求被风控拦截"}')


class _FakeAccount:
    """够 shared.note_failure / shared.note_success 用的最小账号。"""

    def __init__(self, account_id: int):
        self.id = account_id
        self.status = "active"
        self.balance_remain = 510504
        self.balance_total = 10000000
        self.cool_until = None
        self.cool_kind = ""
        self.degrade_until = None
        self.err_count = 0
        self.last_err_at = None
        self.last_err_msg = ""
        self.consecutive_fails = 0
        self.breaker_fails = 0
        self.session_dead_fails = 0


class _FakeDB:
    def commit(self):
        pass

    def rollback(self):
        pass


class RiskBackoffTests(unittest.TestCase):
    ENV_VARS = (gateway.RISK_BACKOFF_ENV, gateway.RISK_BACKOFF_THRESHOLD_ENV,
                gateway.RISK_HIT_WINDOW_ENV)

    def setUp(self) -> None:
        # 每个用例都从「没有退避、没有 env 覆盖」开始。
        self._saved_env = {name: os.environ.pop(name, None)
                           for name in self.ENV_VARS}
        gateway.reset_risk_backoff()
        self.addCleanup(gateway.reset_risk_backoff)
        self.addCleanup(self._restore_env)

        self.note_calls: list[dict] = []
        self.selected: list[frozenset] = []
        self.accounts = [_FakeAccount(189), _FakeAccount(190),
                         _FakeAccount(191)]
        self._real_note_failure = shared.note_failure
        # 上游调用的可切换桩：先失败、后成功都靠改这个 dict。
        self._call = {"error": None,
                      "result": {"choices": [{"message": {"role": "assistant",
                                                          "content": "ok"}}]}}

    def _restore_env(self) -> None:
        for name, value in self._saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    # -- 本地假实现 ---------------------------------------------------
    def _start(self, target, attr: str, value) -> None:
        patcher = mock.patch.object(target, attr, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _install_pool(self) -> None:
        """把选号 / 租约 / 取 token / 上游调用 / 记账换成本地实现。"""

        def select_account(db, **kwargs):
            excluded = set(kwargs.get("exclude_ids") or ())
            self.selected.append(frozenset(excluded))
            for account in self.accounts:
                if account.id not in excluded:
                    return account
            return None

        def chat_completions(data, realm, body, account_id=0):
            if self._call["error"] is not None:
                raise self._call["error"]
            return self._call["result"]

        def note_failure(db, account, **kwargs):
            # 记下入参，但**真的**走 shared.note_failure：冷却 / 降级两条写回
            # 逻辑不能被打桩掉，否则「有没有给账号打冷却」就测不出来了。
            self.note_calls.append(dict(kwargs))
            kwargs.pop("public_platform", None)
            return self._real_note_failure(db, account, public_platform="",
                                          **kwargs)

        self._start(gateway.shared, "select_account", select_account)
        self._start(gateway.shared, "lease", lambda account: True)
        self._start(gateway.shared, "release", lambda account: None)
        self._start(gateway.shared, "note_success", lambda account: None)
        self._start(gateway.shared, "note_failure", note_failure)
        self._start(gateway.marvis_accounts, "ensure_token",
                    lambda db, account: {"openid": "openid-%s" % account.id})
        self._start(gateway.client, "chat_completions", chat_completions)

    def _attempt(self, stream: bool = False):
        return gateway._attempt(_FakeDB(), None, None,
                                {"model": "main-auto"}, "cn", stream=stream)

    # -- ① 单次命中 = 请求级失败 --------------------------------------
    def test_risk_control_does_not_switch_account(self) -> None:
        """单次 4100404：不换号、不 exclude、不打账号冷却，也不锁平台。"""
        self._install_pool()
        self._call["error"] = _risk_error()

        with self.assertRaises(shared.PlatformRejected) as caught:
            self._attempt()
        rejected = caught.exception

        self.assertEqual(rejected.status, 503)
        self.assertEqual(rejected.code, "risk_control_request")
        self.assertIn("这一条请求被拦", rejected.message)
        self.assertEqual(len(self.selected), 1,
                         "风控触发了换号（同一份 body 被重放给别的号）")
        self.assertEqual(self.selected[0], frozenset(),
                         "失败号被加进了 exclude 集合")
        self.assertEqual(self.note_calls[0]["cool_seconds"], 0,
                         "给账号打了冷却（好号会被一起停掉）")
        self.assertEqual(gateway.risk_hits(), 1)
        self.assertEqual(gateway.risk_backoff_remaining(), 0,
                         "单次命中就把平台锁进了退避")

    # -- ② 连续撞满阈值 = 平台级退避 ----------------------------------
    def test_platform_backoff_blocks_second_request(self) -> None:
        """撞满阈值后：第二个请求在选号之前就被拒，且不再打上游。"""
        with mock.patch.dict(os.environ,
                             {gateway.RISK_BACKOFF_THRESHOLD_ENV: "1"}):
            self._install_pool()
            self._call["error"] = _risk_error()

            with self.assertRaises(shared.PlatformRejected) as first:
                self._attempt()
            self.assertEqual(first.exception.code, "risk_control_backoff")
            remaining = gateway.risk_backoff_remaining()
            self.assertGreater(remaining, 0)
            self.assertLessEqual(remaining, gateway.RISK_BACKOFF_DEFAULT_SECONDS)

            selected_before = len(self.selected)
            with self.assertRaises(shared.PlatformRejected) as second:
                self._attempt()
            rejected = second.exception

        self.assertEqual(rejected.status, 503)
        self.assertEqual(rejected.code, "risk_control_backoff")
        self.assertGreaterEqual(int(rejected.headers["Retry-After"]), 1)
        self.assertIn("进入退避", rejected.message)
        self.assertEqual(len(self.selected), selected_before,
                         "退避期间仍然去选了号（会打到上游）")

    def test_backoff_window_and_threshold_are_env_tunable(self) -> None:
        """窗口 / 阈值都能用 env 覆盖（运维现场只改环境变量）。"""
        with mock.patch.dict(os.environ, {gateway.RISK_BACKOFF_ENV: "5",
                                          gateway.RISK_BACKOFF_THRESHOLD_ENV: "1"}):
            gateway.note_risk_control()
            remaining = gateway.risk_backoff_remaining()
        self.assertGreater(remaining, 0)
        self.assertLessEqual(remaining, 5)
        self.assertGreater(gateway.risk_backoff_remaining(), 0,
                           "退避在函数返回后被清掉了")

    # -- ③ 客户端看到的错误面 -----------------------------------------
    def test_risk_control_maps_to_503_for_client(self) -> None:
        """客户端看到的是 503（可重试），不是看起来像 Key 错的 403；两档文案。"""
        single = gateway._risk_control_rejected(
            '{"code":4100404,"msg":"客户端通用错误:请求被风控拦截"}').openai()
        self.assertEqual(single.status_code, 503)
        self.assertGreaterEqual(int(single.headers["retry-after"]), 1)
        payload = json.loads(single.body)["error"]
        self.assertEqual(payload["code"], "risk_control_request")
        self.assertIn("4100404", payload["message"])
        self.assertIn("这一条请求被拦", payload["message"])
        self.assertNotIn("进入退避", payload["message"])

        with mock.patch.dict(os.environ,
                             {gateway.RISK_BACKOFF_THRESHOLD_ENV: "1"}):
            gateway.note_risk_control()
            backing_off = gateway._risk_control_rejected().openai()
        self.assertEqual(backing_off.status_code, 503)
        self.assertGreaterEqual(int(backing_off.headers["retry-after"]), 1)
        self.assertEqual(json.loads(backing_off.body)["error"]["code"],
                         "risk_control_backoff")
        message = json.loads(backing_off.body)["error"]["message"]
        self.assertIn("进入退避", message)
        self.assertNotIn("这一条请求被拦", message)

    # -- ④ 成功即清零 --------------------------------------------------
    def test_success_clears_consecutive_hits(self) -> None:
        """成功一次 ⇒ 连击归零：阈值判的是连续，不是历史累计。"""
        self._install_pool()
        self._call["error"] = _risk_error()
        # 阈值 - 1 次：计数器在涨，平台**还没有**退避（所以下一次成功能够打到上游）。
        for _ in range(gateway.RISK_BACKOFF_THRESHOLD - 1):
            with self.assertRaises(shared.PlatformRejected):
                self._attempt()
        self.assertEqual(gateway.risk_hits(), gateway.RISK_BACKOFF_THRESHOLD - 1)
        self.assertEqual(gateway.risk_backoff_remaining(), 0)

        self._call["error"] = None
        result, _account, _started = self._attempt()
        self.assertIs(result, self._call["result"])
        self.assertEqual(gateway.risk_hits(), 0)
        self.assertEqual(gateway.risk_backoff_remaining(), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
