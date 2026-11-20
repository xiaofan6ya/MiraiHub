#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""4100404 一发即止 + 账号级短冷却（2026-11-20）。

## 锁的四件事

  1. **客户端**：撞 4100404 只发 1 发（旧行为 2 发：整组重签立刻再打）。
     1 秒内把同一份 body 的第二份特征喂给风控引擎，只会继续撞同一个判定
     —— 18:19 的现场就是这样把 4100404 推成 4100403 的；
  2. **选号**：命中后这个 account_id 在 `MARVIS_RISK_ACCOUNT_COOLDOWN`（默认
     60 秒）内不再被选中。**内存表、不写库、不跨进程**：`account.cool_until`
     必须仍然是 None（号表不许留风控脏记录）；
  3. **开关**：`MARVIS_RISK_COOLDOWN_ON_4100404=0` 回到「命中后不冷却」；
  4. **回归**：其它错误码的同号重试次数一个都没变 —— network / timeout /
     4100406 → 3 发（1 + SAME_ACCOUNT_RETRIES），auth / 参数类 → 1 发。

运行：`python -m pytest tests/test_marvis_risk_no_retry_cooldown.py -q`
"""
import io
import json
import os
import sys
import time
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

# 必须早于 admin.* 导入（admin/db.py 在模块级按 settings 建引擎）。
os.environ.setdefault(
    "ADMIN_DATABASE_URL",
    "sqlite:///" + str(Path(os.environ.get("TEMP", "/tmp")) / "marvis_risk_cd_test.db"),
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from admin.platform import shared  # noqa: E402
from admin.platform.marvis import gateway  # noqa: E402
from admin.platform.marvis.core import client as marvis_client  # noqa: E402
from admin.platform.marvis.core import egress  # noqa: E402

#: 合成账号：只用来验「整组身份」与选号，不指向任何真实账号。
ACCT = {"openid": "openid-a", "access_token": "tok-a",
        "guid": "64e69d95-c76d-4de9-994b-6285357e6dd4", "login_type": "QC"}
RISK_BODY = '{"code":4100404,"msg":"请求被风控拦截"}'.encode()


def _risk_error():
    return marvis_client.UpstreamError(
        "模型网关失败（HTTP 403）", status=403, code="403",
        kind="risk_control", detail=RISK_BODY.decode())


# ---------------------------------------------------------------------------
# ① 客户端：4100404 只发 1 发
# ---------------------------------------------------------------------------
class ClientOneShotTests(unittest.TestCase):
    """数**真实发出的请求**：假上游每次 open 都记一笔，然后回同一个 4100404。"""

    def setUp(self):
        self._env = {name: os.environ.get(name) for name in ("MARVIS_USKEY_BACKEND",)}
        os.environ["MARVIS_USKEY_BACKEND"] = "mint"   # 别让测试依赖 DLL
        self._orig_opener = marvis_client._opener
        self._orig_sentinel = marvis_client.note_sentinel
        self._orig_pick = egress.pick
        marvis_client.note_sentinel = lambda *a, **k: None
        egress.pick = lambda account_id: ""   # 出口池关掉，否则同一发会被算两次
        self.addCleanup(self._restore)

    def _restore(self):
        marvis_client._opener = self._orig_opener
        marvis_client.note_sentinel = self._orig_sentinel
        egress.pick = self._orig_pick
        for name, value in self._env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def _count(self, body: bytes):
        seen = []

        class Opener:
            def open(self, req, timeout=None):
                seen.append({k.lower(): v for k, v in req.header_items()})
                raise urllib.error.HTTPError(
                    req.full_url, 403, "Forbidden", {}, io.BytesIO(body))

        marvis_client._opener = lambda: Opener()
        with self.assertRaises(marvis_client.UpstreamError) as caught:
            marvis_client.open_chat_stream(
                ACCT, "cn", {"model": "main-auto", "messages": []}, account_id=7)
        return seen, caught.exception

    def test_4100404_sends_exactly_one_request(self):
        seen, exc = self._count(RISK_BODY)
        self.assertEqual(marvis_client.CHAT_ATTEMPTS, 1,
                         "CHAT_ATTEMPTS 又被调回 2 了")
        self.assertEqual(exc.kind, "risk_control")
        self.assertIn("4100404", str(exc.detail))
        self.assertEqual(
            len(seen), 1,
            "4100404 又同号重签重发了（旧行为是 2 发）：%d 发" % len(seen))

    def test_auth_code_still_single_shot(self):
        """回归：4100403 一直是一发即止，这次改动不许把它碰成多次。"""
        seen, exc = self._count('{"code":4100403}'.encode())
        self.assertEqual(len(seen), 1)
        self.assertEqual(exc.kind, "auth")


# ---------------------------------------------------------------------------
# ② 回归：其它错误码的同号重试次数不变（gateway._call_upstream）
# ---------------------------------------------------------------------------
class SameAccountRetryRegressionTests(unittest.TestCase):
    """`_call_upstream` 的重试白名单：network / timeout / 4100406 → 3 发。"""

    def setUp(self):
        self._sleep = gateway.time.sleep
        gateway.time.sleep = lambda *_: None     # 退避是 0.3s 级，测试里不必真等
        self.addCleanup(setattr, gateway.time, "sleep", self._sleep)
        self._env = {name: os.environ.get(name) for name in
                     ("MIRAIHUB_MARVIS_DUMP_403",)}
        os.environ.pop("MIRAIHUB_MARVIS_DUMP_403", None)
        self.addCleanup(self._restore)

    def _restore(self):
        for name, value in self._env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def _calls(self, exc):
        calls = []

        def fake(*args, **kwargs):
            calls.append(1)
            raise exc

        with mock.patch.object(gateway.client, "chat_completions", fake):
            with self.assertRaises(marvis_client.UpstreamError):
                gateway._call_upstream(
                    {"openid": "openid-a"}, "cn",
                    {"model": "main-auto", "messages": []}, 7,
                    stream=False, profile=None)
        return len(calls)

    def test_risk_control_not_retried(self):
        self.assertEqual(self._calls(_risk_error()), 1)

    def test_auth_not_retried(self):
        self.assertEqual(
            self._calls(marvis_client.UpstreamError(
                "x", status=401, kind="auth", detail='{"code":4100403}')), 1)

    def test_network_retried_twice(self):
        self.assertEqual(
            self._calls(marvis_client.UpstreamError(
                "x", status=0, kind="network")),
            1 + gateway.SAME_ACCOUNT_RETRIES)

    def test_timeout_retried_twice(self):
        self.assertEqual(
            self._calls(marvis_client.UpstreamError(
                "x", status=0, kind="timeout")),
            1 + gateway.SAME_ACCOUNT_RETRIES)

    def test_4100406_retried_twice(self):
        """4100406 走的是 upstream_business + 业务码白名单，次数不变。"""
        self.assertEqual(
            self._calls(marvis_client.UpstreamError(
                "x", status=403, code="403", kind="upstream_business",
                detail='{"code":4100406,"msg":"非法请求"}')),
            1 + gateway.SAME_ACCOUNT_RETRIES)


# ---------------------------------------------------------------------------
# ③ 账号级冷却（内存表）：命中后 60 秒内不再选它
# ---------------------------------------------------------------------------
class _FakeAccount:
    """够 shared.note_failure / note_success 用的最小账号。"""

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


class _FakeDB:
    def commit(self):
        pass

    def rollback(self):
        pass


class RiskAccountCooldownTests(unittest.TestCase):
    def setUp(self):
        gateway.reset_risk_backoff()
        gateway.reset_risk_account_cooldown()
        self.addCleanup(gateway.reset_risk_backoff)
        self.addCleanup(gateway.reset_risk_account_cooldown)
        self._env = {}
        for name in (gateway.RISK_BACKOFF_ENV,
                     gateway.RISK_BACKOFF_THRESHOLD_ENV,
                     gateway.RISK_ACCOUNT_COOLDOWN_ENV,
                     gateway.RISK_ACCOUNT_COOLDOWN_SWITCH_ENV):
            self._env[name] = os.environ.pop(name, None)
        self.addCleanup(self._restore)
        self.note_calls = []
        self._patch("note_failure", self._record_note_failure)
        for name, value in (
                ("lease", lambda account: True),
                ("release", lambda account: None),
                ("note_success", lambda account: None)):
            self._patch(name, value)
        self._patch_on(gateway.marvis_accounts, "ensure_token",
                       lambda db, account: {"openid": "openid-%s" % account.id})

    def _restore(self):
        for name, value in self._env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def _record_note_failure(self, db, account, **kwargs):
        """记下来但**不写号表** —— 用来证明冷却没有走 DB 那条路。"""
        self.note_calls.append(dict(kwargs))
        return None

    def _patch(self, name, value):
        patcher = mock.patch.object(gateway.shared, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _patch_on(self, target, name, value):
        patcher = mock.patch.object(target, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _install_pool(self, accounts):
        selected = []

        def select_account(db, **kwargs):
            excluded = set(kwargs.get("exclude_ids") or ())
            selected.append(frozenset(excluded))
            for account in accounts:
                if account.id not in excluded:
                    return account
            return None

        self._patch("select_account", select_account)
        return selected

    def _install_upstream(self, behaviour):
        patcher = mock.patch.object(gateway, "_call_upstream", behaviour)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _attempt(self):
        return gateway._attempt(
            _FakeDB(), None, None, {"model": "main-auto"}, "cn", stream=False)

    # --- 命中就冷却 ------------------------------------------------------
    def test_hit_cools_account_in_memory_only(self):
        accounts = [_FakeAccount(189), _FakeAccount(190)]
        self._install_pool(accounts)

        def hit(*args, **kwargs):
            raise _risk_error()

        self._install_upstream(hit)
        with self.assertRaises(marvis_client.UpstreamError):
            self._attempt()

        self.assertEqual(gateway.risk_cooled_accounts(), frozenset({189}),
                         "撞墙的号没进账号级冷却")
        self.assertIsNone(accounts[0].cool_until,
                          "冷却被写进号表了（要求：内存 + 不写库）")
        self.assertIsNone(accounts[1].cool_until)
        left = gateway.risk_account_cooldown_remaining()
        self.assertTrue(55 <= left <= 61, left)   # 默认 60 秒

    def test_cooled_account_not_selected_within_window(self):
        accounts = [_FakeAccount(189), _FakeAccount(190)]
        selected = self._install_pool(accounts)

        def behaviour(data, realm, body, account_id, *, stream, profile=None):
            if account_id == 189:
                raise _risk_error()
            return {"choices": []}

        self._install_upstream(behaviour)
        with self.assertRaises(marvis_client.UpstreamError):
            self._attempt()

        # 第二次请求：189 还在冷却窗口里 ⇒ 选号时被排除，落到 190。
        result, account, _started = self._attempt()
        self.assertEqual(account.id, 190, "冷却窗口内又选中了刚撞墙的号")
        self.assertIn(189, selected[-1], "冷却窗口内选号没有排除刚撞墙的号")
        self.assertEqual(result, {"choices": []})

    def test_switch_off_restores_old_behaviour(self):
        os.environ[gateway.RISK_ACCOUNT_COOLDOWN_SWITCH_ENV] = "0"
        accounts = [_FakeAccount(189), _FakeAccount(190)]
        selected = self._install_pool(accounts)

        def behaviour(data, realm, body, account_id, *, stream, profile=None):
            if account_id == 189 and not selected[1:]:
                raise _risk_error()
            return {"choices": []}

        self._install_upstream(behaviour)
        with self.assertRaises(marvis_client.UpstreamError):
            self._attempt()

        self.assertEqual(gateway.risk_cooled_accounts(), frozenset(),
                         "开关=0 时仍然冷却了账号")
        self.assertEqual(gateway.risk_account_cooldown_remaining(), 0.0)
        # 回到旧行为：下一次仍然可能选中 189。
        result, account, _started = self._attempt()
        self.assertEqual(account.id, 189)

    def test_cooldown_seconds_env_tunable(self):
        os.environ[gateway.RISK_ACCOUNT_COOLDOWN_ENV] = "7"
        self.assertEqual(gateway._risk_account_cooldown_seconds(), 7)
        gateway.cool_account_after_risk(4242)
        left = gateway.risk_account_cooldown_remaining()
        self.assertTrue(5.5 <= left <= 7.5, left)

    def test_default_and_invalid_seconds(self):
        self.assertEqual(gateway._risk_account_cooldown_seconds(),
                         gateway.RISK_ACCOUNT_COOLDOWN_DEFAULT)
        self.assertEqual(gateway.RISK_ACCOUNT_COOLDOWN_DEFAULT, 60)
        for bad in ("abc", "0", "-5", ""):
            os.environ[gateway.RISK_ACCOUNT_COOLDOWN_ENV] = bad
            self.assertEqual(gateway._risk_account_cooldown_seconds(), 60, bad)

    def test_expired_entries_are_pruned(self):
        gateway._RISK_ACCOUNT_STATE[4242] = time.monotonic() - 1
        self.assertEqual(gateway.risk_cooled_accounts(), frozenset())
        self.assertEqual(gateway.risk_account_cooldown_remaining(), 0.0)

    def test_cooling_never_raises_on_bad_id(self):
        for bad in (0, None, "x"):
            self.assertEqual(gateway.cool_account_after_risk(bad), 0.0)

    # --- 全池都在冷却时的错误面 -----------------------------------------
    def test_all_accounts_cooled_reports_risk_cooling(self):
        accounts = [_FakeAccount(189)]
        self._install_pool(accounts)
        self._patch_on(gateway.marvis_accounts, "accounts_for",
                       lambda db, realm: [SimpleNamespace(
                           id=189, status="active", last_err_msg="",
                           balance_remain=100, balance_total=1000)])
        gateway.cool_account_after_risk(189)

        with self.assertRaises(shared.PlatformRejected) as caught:
            self._attempt()

        exc = caught.exception
        self.assertEqual(exc.status, 503)
        self.assertEqual(exc.code, "risk_control_cooling")
        self.assertIn("4100404", exc.message)
        self.assertGreaterEqual(int(exc.headers["Retry-After"]), 1)
