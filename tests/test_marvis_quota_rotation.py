"""Marvis 3000100 / 5100007 的处置链（PR-02）。

自跑（仓库没有 pytest 基础设施）：`python tests/test_marvis_quota_rotation.py`

锁的是两条**相反**的处置：

  · 3000100（max_input_tokens）= 额度族：
        第一次命中 -> 就地降 max_tokens 到 32768，**同号**重试一次；
        降级后仍命中 -> 标 (账号 × 模型) 额度紧张，**换号**（新号直接用 32768）；
        账号耗尽 -> 才把 3000100 返回给用户。
  · 5100007（上游服务端内部错误）：
        **同号**重试一次，不换号、不 note_failure、不落 (账号 × 模型) 隔离。

这套用例在改动前的实现上应当全部失败：那时 3000100 走的是「原样重发 4 次」，
5100007 走的是「换号 + note_failure」，两者都拿不到这里的断言。
"""
import json
import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from admin.platform.marvis import gateway as gw            # noqa: E402
from admin.platform.marvis.core import client             # noqa: E402


# ---------------------------------------------------------------------------
# 测试替身（只替「选号 / 租约 / 上游调用 / 落库」四件事，逻辑本身真跑）
# ---------------------------------------------------------------------------
class _Account:
    """够 _attempt + select_account 用的账号对象。"""

    def __init__(self, account_id: int):
        self.id = account_id
        self.uid = "uid-%02d" % account_id
        self.platform = "marvis"
        self.status = "active"
        self.balance_remain = 100
        self.balance_total = 100
        self.cool_until = None
        self.degrade_until = None
        self.err_count = 0
        self.consecutive_fails = 0
        self.last_err_msg = ""
        self.last_used_at = None
        self.max_in_flight = None


class _FakeQuery:
    """把 filter(AccountModelCooldown.account_id == X, ... .model == Y) 记成键值对。"""

    def __init__(self, db):
        self._db = db
        self._criteria = {}

    def filter(self, *exprs, **_kwargs):
        for expr in exprs:
            try:
                self._criteria[expr.left.key] = expr.right.value
            except AttributeError:      # 不是简单的 == 表达式就忽略（测试用不到）
                continue
        return self

    def first(self):
        key = (self._criteria.get("account_id"), self._criteria.get("model"))
        return self._db.rows.get(key)


class _FakeDB:
    """只够 _note_model_quota_tight() 用：query().filter().first() / add / commit。

    按 (account_id, model) 存行 —— 与真实表的语义一致；否则第二个号会"命中"
    第一个号那行，测试就看不出到底标了几个号。
    """

    def __init__(self):
        self.rows = {}
        self.added = []
        self.commits = 0

    def query(self, _model):
        return _FakeQuery(self)

    def add(self, row):
        self.added.append(row)
        self.rows[(row.account_id, row.model)] = row

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass


def _quota_error():
    """上游 400 + code=3000100（client 把 HTTP 状态填进 exc.code）。"""
    payload = json.dumps({"code": 3000100, "msg": "max input token error"})
    return client.UpstreamError(
        "模型网关失败（HTTP 400）", status=400, code="400",
        kind="http_error", detail=payload)


def _internal_error():
    """上游 500 + code=5100007。"""
    payload = json.dumps({"code": 5100007, "msg": "server internal error"})
    return client.UpstreamError(
        "模型网关失败（HTTP 500）", status=500, code="500",
        kind="upstream_error", detail=payload)


def _ok_result():
    return {"choices": [{"message": {"role": "assistant", "content": "ok"},
                         "finish_reason": "stop"}]}


class QuotaRotationTests(unittest.TestCase):
    def setUp(self):
        self.db = _FakeDB()
        self.accounts = []
        self.behaviours = []
        self.calls = []          # 每次上游调用的 (account_id, max_tokens)
        self.select_ids = []     # select_account 依次选中的账号 id
        self.select_calls = 0
        self.note_failure = mock.Mock()
        self.note_success = mock.Mock()
        self.release = mock.Mock()
        patches = [
            mock.patch.object(gw.shared, "select_account",
                              side_effect=self._select),
            mock.patch.object(gw.shared, "lease", return_value=True),
            mock.patch.object(gw.shared, "release", self.release),
            mock.patch.object(gw.shared, "note_success", self.note_success),
            mock.patch.object(gw.shared, "note_failure", self.note_failure),
            mock.patch.object(gw.marvis_accounts, "ensure_token",
                              side_effect=lambda _db, _acc: {"access_token": "t"}),
            # 退避置 0：单测不等真实睡眠
            mock.patch.object(gw, "INTERNAL_RETRY_BASE", 0.0),
            mock.patch.object(gw, "INTERNAL_RETRY_JITTER", 0.0),
            mock.patch.object(gw.client, "chat_completions",
                              side_effect=self._upstream),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    # --- 替身实现 -------------------------------------------------------
    def _select(self, _db, **_kwargs):
        self.select_calls += 1
        if self.select_ids or self.select_calls <= len(self.accounts):
            index = self.select_calls - 1
            if index < len(self.accounts):
                account = self.accounts[index]
                self.select_ids.append(account.id)
                return account
        return None

    def _upstream(self, _data, _realm, body, *, account_id=0):
        self.calls.append((account_id, body.get("max_tokens")))
        behaviour = self.behaviours.pop(0)
        if isinstance(behaviour, Exception):
            raise behaviour
        return behaviour

    def _run(self, body=None, stream=False):
        body = body if body is not None else {
            "model": "main-auto",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 128000,
        }
        return gw._attempt(self.db, None, None, body, "cn", stream=stream)

    # --- a) 首次 3000100：同号降级重试 --------------------------------
    def test_a_quota_first_hit_degrades_and_retries_same_account(self):
        account = _Account(1)
        self.accounts = [account]
        self.behaviours = [_quota_error(), _ok_result()]
        body = {"model": "main-auto",
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 128000}

        result, picked, _started = self._run(body)

        # 同号两次：原值 -> 32768
        self.assertEqual(self.calls, [(1, 128000), (1, 32768)])
        self.assertIs(picked, account)
        self.assertEqual(result["choices"][0]["message"]["content"], "ok")
        # 降级是就地改 body：值必须跟着换号走
        self.assertEqual(body["max_tokens"], 32768)
        self.assertEqual(self.select_calls, 1)
        self.note_failure.assert_not_called()
        self.assertEqual(self.db.added, [])

    # --- b) 降级后仍 3000100：换号，且新号也用 32768 --------------------
    def test_b_quota_after_degrade_rotates_account_with_degraded_value(self):
        first, second = _Account(1), _Account(2)
        self.accounts = [first, second]
        self.behaviours = [_quota_error(), _quota_error(), _ok_result()]

        _result, picked, _started = self._run()

        self.assertEqual(self.calls, [(1, 128000), (1, 32768), (2, 32768)])
        self.assertIs(picked, second)
        self.assertEqual(self.select_calls, 2)
        # 只标「被降级后仍失败」的那个号
        self.assertEqual(len(self.db.added), 1)
        row = self.db.added[0]
        self.assertEqual(row.account_id, 1)
        self.assertEqual(row.model, "main-auto")
        self.assertEqual(row.kind, "model_quota")
        remaining = row.until - datetime.utcnow()
        self.assertGreater(remaining, timedelta(seconds=1700))
        self.assertLessEqual(remaining, timedelta(seconds=1800))
        self.note_failure.assert_not_called()

    # --- c) 账号耗尽：3000100 原样返回给用户 ---------------------------
    def test_c_quota_returns_3000100_when_accounts_run_out(self):
        self.accounts = [_Account(1), _Account(2)]
        self.behaviours = [_quota_error(), _quota_error(), _quota_error()]

        with self.assertRaises(client.UpstreamError) as caught:
            self._run()

        # 第一个号：原值 + 降级值；第二个号：直接用 32768
        self.assertEqual(self.calls, [(1, 128000), (1, 32768), (2, 32768)])
        # 选号三次 = 两个号 + 一次「没号了」
        self.assertEqual(self.select_calls, 3)
        self.assertIn("3000100", caught.exception.detail)
        self.assertEqual([row.account_id for row in self.db.added], [1, 2])
        self.note_failure.assert_not_called()

    # --- d) 5100007：同号重试一次，账号选择次数不变 ---------------------
    def test_d_internal_error_retries_same_account_only(self):
        account = _Account(1)
        self.accounts = [account]
        self.behaviours = [_internal_error(), _internal_error()]
        body = {"model": "main-auto",
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 128000}

        with self.assertRaises(client.UpstreamError) as caught:
            self._run(body)

        # 同号两次、不再换号
        self.assertEqual(self.calls, [(1, 128000), (1, 128000)])
        self.assertEqual(self.select_calls, 1)
        self.assertIn("5100007", caught.exception.detail)
        # 不计数、不冷却、不落 (账号 × 模型) 隔离
        self.note_failure.assert_not_called()
        self.assertEqual(self.release.call_count, 1)
        self.assertEqual(self.db.added, [])
        self.assertEqual(account.err_count, 0)

    # --- e) 失败画像带 max_tokens 与 degraded --------------------------
    def test_e_failure_profile_logs_max_tokens_and_degraded(self):
        self.accounts = [_Account(1), _Account(2)]
        self.behaviours = [_quota_error(), _quota_error(), _quota_error()]

        with self.assertLogs("admin.platform.marvis.gateway", level="WARNING") as logs:
            with self.assertRaises(client.UpstreamError):
                self._run()

        profiles = [line for line in logs.output if "失败画像" in line]
        self.assertEqual(len(profiles), 3)
        for line in profiles:
            self.assertIn("max_tokens=", line)
            self.assertIn("degraded=", line)
        # 第一次：原值、未降级；第二次：降级值、已降级；第三个号：请求已处于降级形态
        self.assertIn("max_tokens=128000 degraded=0", profiles[0])
        self.assertIn("max_tokens=32768 degraded=1", profiles[1])
        self.assertIn("max_tokens=32768 degraded=1", profiles[2])


if __name__ == "__main__":
    unittest.main(verbosity=2)
