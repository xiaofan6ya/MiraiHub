# -*- coding: utf-8 -*-
"""导入并发化回归测试。

## 背景

用户反馈「导出/导入太卡，才一二十个账号就卡，几百个卡死」。服务端实测：
  · 导出 100 个号 0.3 秒（不是瓶颈）；
  · **单账号一次上游往返 1.2 秒**（fetch_balance 0.61s + 额度明细 0.59s）；
  · 而导入原先对每个号**串行**打上游 —— 20 个号 24 秒起，几百个号几分钟。

本测试锁住「并发」这个性质：把慢操作换成 sleep，断言总耗时远小于串行之和。
一旦有人把并发改回 `for` 循环，这里立刻失败。
"""
import asyncio
import pathlib
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

SLOW = 0.25       # 单条"上游"耗时
N = 8             # 条数
SERIAL_MIN = SLOW * N          # 串行至少要这么久
BUDGET = SERIAL_MIN * 0.5      # 并发后应当远低于串行的一半


class RefreshManyTests(unittest.TestCase):
    def test_refresh_many_runs_concurrently(self):
        from admin.routers import accounts as acc_mod

        def slow_one(acc_id, with_expiry=True):
            time.sleep(SLOW)
            return acc_id, True, ""

        with mock.patch.object(acc_mod, "_refresh_one_account", slow_one):
            t0 = time.perf_counter()
            out = acc_mod._refresh_many(list(range(1, N + 1)), with_expiry=True,
                                        workers=N)
            cost = time.perf_counter() - t0
        self.assertEqual(len(out), N)
        self.assertLess(cost, BUDGET,
                        "%d 条并发刷新用了 %.2fs（串行约 %.2fs）—— 又变回串行了？"
                        % (N, cost, SERIAL_MIN))

    def test_refresh_many_single_item_still_works(self):
        from admin.routers import accounts as acc_mod

        def slow_one(acc_id, with_expiry=True):
            return acc_id, False, "上游未返回余额"

        with mock.patch.object(acc_mod, "_refresh_one_account", slow_one):
            out = acc_mod._refresh_many([7])
        self.assertEqual(out, [(7, False, "上游未返回余额")])


class RefreshNameCollisionTests(unittest.TestCase):
    """锁住「导入刷新函数不能被同名函数覆盖」这件事。

    2026-11-06 线上事故：提速提交新增 `_refresh_one_account(acc_id,
    with_expiry)`，但文件后面早就有一个同名的 `_refresh_one_account(db,
    account)`（刷新全部任务用）。Python 后定义覆盖前定义，import-local
    把 bool 当成 Account，账号已 commit 却返回 500 —— 「报错了但号进来了」。
    原来的并发测试用 mock.patch 按名字替换，恰好绕过了覆盖，没能拦住它。
    """
    def test_import_refresh_function_is_not_shadowed(self):
        import inspect
        from admin.routers import accounts as acc_mod

        self.assertEqual(
            list(inspect.signature(acc_mod._refresh_one_account).parameters)[:2],
            ["acc_id", "with_expiry"])
        self.assertEqual(
            list(inspect.signature(acc_mod._refresh_batch_account).parameters)[:2],
            ["db", "account"])

    def test_refresh_one_is_defined_only_once(self):
        import re
        src = (pathlib.Path(__file__).resolve().parent.parent
               / "admin" / "routers" / "accounts.py").read_text(encoding="utf-8")
        hits = re.findall(r"^def _refresh_one_account\(", src, re.M)
        self.assertEqual(len(hits), 1,
                         "_refresh_one_account 被重复定义，后定义会覆盖导入刷新逻辑")

    def test_pending_refresh_does_not_block_http_response(self):
        """余额刷新必须离开请求关键路径，否则上游一慢前端又报「导入失败」。"""
        from admin.routers import accounts as acc_mod

        def slow_one(acc_id, with_expiry=True):
            time.sleep(SLOW)
            return acc_id, True, ""

        with mock.patch.object(acc_mod, "_refresh_one_account", slow_one):
            t0 = time.perf_counter()
            acc_mod._refresh_pending_async([1, 2])
            cost = time.perf_counter() - t0
        self.assertLess(cost, SLOW,
                        "%.2fs 才返回，余额刷新又回到 HTTP 关键路径了" % cost)


class _FakeQuery:
    def filter(self, *a, **k):
        return self

    def first(self):
        return None


class _FakeDB:
    def query(self, *a, **k):
        return _FakeQuery()

    def rollback(self):
        pass


class QoderPrefetchTests(unittest.TestCase):
    """qoder 的 import_batch：profile 必须**先并发预取**再逐条落库。"""

    def test_profiles_prefetched_concurrently(self):
        from admin.platform.qoder.services import accounts as qa

        calls = []

        def slow_profile(token, realm):
            calls.append(token)
            time.sleep(SLOW)
            return {"id": "u-" + token, "name": "n-" + token}

        records = [{"token": "t%d" % i, "realm": "cn", "uid": ""} for i in range(N)]
        with mock.patch.object(qa.client, "fetch_profile", slow_profile):
            t0 = time.perf_counter()
            got = qa._prefetch_profiles(records, workers=N)
            cost = time.perf_counter() - t0
        self.assertEqual(len(got), N)
        self.assertLess(cost, BUDGET,
                        "%d 条 profile 预取用了 %.2fs（串行约 %.2fs）"
                        % (N, cost, SERIAL_MIN))

    def test_import_batch_passes_prefetched_profile(self):
        from admin.platform.qoder.services import accounts as qa

        seen = []

        def fake_import(db, record, profile=None):
            seen.append((record["token"], profile))
            return {"ok": True}

        records = [{"token": "a", "realm": "cn"}, {"token": "b", "realm": "cn"}]
        text = "\n".join(__import__("json").dumps(r) for r in records)
        with mock.patch.object(qa, "import_record", fake_import), \
                mock.patch.object(qa, "_prefetch_profiles",
                                  lambda recs, workers=16: {0: {"id": "A"}}):
            res = qa.import_batch(_FakeDB(), text)
        self.assertEqual(res["added"], 2)
        self.assertEqual(seen[0][1], {"id": "A"})
        self.assertIsNone(seen[1][1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
