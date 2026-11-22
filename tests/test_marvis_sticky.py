"""PR #48 回归：Marvis 会话亲和（sticky_key）选号。

锁定三件事，都属「写错了不会报错、只会静默变慢/变贵」的那类：

  1. 同一会话在 TTL 内回到**上次成功的号**，而不是每次都按「最闲」重排；
  2. 绑定的号已不可用（被排除 / 冷却）时**自动回落**正常排序，
     不把一个会话钉死在坏号上；
  3. 不传 `sticky_key` 时行为与改动前一致（默认空串 = 完全不粘）。

另外锁 `_sticky_key_for()` 的**平台前缀**：各平台 `session_key_for()` 算法相同，
不带前缀会让同一段对话在 marvis / loomy 之间共用一条绑定。
"""
import os
import sys
import unittest
from unittest import mock
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from admin.db import Base
from admin.models import Account
from admin.pool import POOL, StickyRouter
from admin.platform import shared
from admin.platform.marvis import gateway as marvis_gateway

KEY = "marvis|conv-a"


class StickySelect(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.db.add_all([
            Account(platform="marvis", uid=uid, name=uid, auth_json="{}",
                    status="active", balance_remain=10)
            for uid in ("u0", "u1", "u2")
        ])
        self.db.commit()
        self.accounts = {a.uid: a for a in self.db.query(Account).all()}
        # 每个用例一条干净的绑定表，避免互相污染。
        POOL.sticky = StickyRouter()

    def tearDown(self):
        self.db.close()
        POOL.sticky = StickyRouter()

    def pick(self, **kw):
        return shared.select_account(
            self.db, storage="marvis", min_balance=0, **kw)

    # -- 1) 命中 ---------------------------------------------------------
    def test_hit_returns_bound_account_not_least_busy(self):
        POOL.sticky.bind(KEY, "u2")
        picked = self.pick(sticky_key=KEY)
        self.assertIsNotNone(picked)
        self.assertEqual(picked.uid, "u2",
                         "同会话必须回到绑定的号，而不是按最闲重排")

    def test_hit_marks_picked(self):
        POOL.sticky.bind(KEY, "u2")
        picked = self.pick(sticky_key=KEY)
        self.assertIsNotNone(picked.last_picked_at,
                             "粘性命中也要写 last_picked_at（面板按它归因）")

    # -- 2) 回落 ---------------------------------------------------------
    def test_bound_account_excluded_falls_back_and_unbinds(self):
        POOL.sticky.bind(KEY, "u2")
        picked = self.pick(sticky_key=KEY, exclude_ids=[self.accounts["u2"].id])
        self.assertIsNotNone(picked)
        self.assertNotEqual(picked.uid, "u2",
                            "排除掉的号不能被粘性命中")
        self.assertIsNone(POOL.sticky.resolve(KEY, {"u0", "u1", "u2"}),
                          "命中失败时应解绑，避免下次继续钉在不可用的号上")

    def test_bound_account_cooled_falls_back(self):
        self.accounts["u2"].cool_until = datetime.utcnow() + timedelta(minutes=10)
        self.db.commit()
        POOL.sticky.bind(KEY, "u2")
        picked = self.pick(sticky_key=KEY)
        self.assertIsNotNone(picked)
        self.assertNotEqual(picked.uid, "u2", "冷却中的号不能被粘住")

    def test_binding_does_not_leak_across_platforms(self):
        POOL.sticky.bind("loomy|conv-a", "u2")
        picked = self.pick(sticky_key=KEY)
        self.assertIsNotNone(picked)
        self.assertNotEqual(picked.uid, "u2",
                            "平台前缀必须隔离，不能共用同一条绑定")

    # -- 3) 默认不变 -----------------------------------------------------
    def test_no_sticky_key_keeps_legacy_order(self):
        POOL.sticky.bind(KEY, "u2")
        picked = self.pick()
        self.assertIsNotNone(picked)
        self.assertEqual(picked.uid, "u0",
                         "不传 sticky_key 时选号顺序必须与改动前一致")

    # -- 4) 网关接线 -----------------------------------------------------
    def test_sticky_key_has_platform_prefix(self):
        body = {"metadata": {"conversation_id": "conv-a"}}
        self.assertEqual(marvis_gateway._sticky_key_for(body), KEY)
        self.assertEqual(marvis_gateway._sticky_key_for({}), "",
                         "算不出会话键时返回空串（退化成不粘）")

    def test_bind_sticky_writes_binding(self):
        marvis_gateway._bind_sticky(KEY, self.accounts["u2"])
        self.assertEqual(POOL.sticky.resolve(KEY, {"u0", "u1", "u2"}), "u2")

    def test_bind_sticky_tolerates_missing_uid(self):
        marvis_gateway._bind_sticky(KEY, None)
        marvis_gateway._bind_sticky("", self.accounts["u2"])
        self.assertIsNone(POOL.sticky.resolve(KEY, {"u0", "u1", "u2"}))


class StreamAttemptBindsSession(unittest.TestCase):
    """流式请求也必须建立会话亲和绑定。

    回归：`_bind_sticky` 曾经只在非流式分支调用，流式分支在
    `open_chat_stream()` 之后直接 `return`，于是**每一次 SSE 请求都是
    sticky miss** —— 同一段对话在号池里逐请求轮换账号。此时 agent 类客户端
    （全程流式）永远享受不到亲和，而只用非流式验证的测试完全看不到这个问题。
    """

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.db.add_all([
            Account(platform="marvis", uid=uid, name=uid, auth_json="{}",
                    status="active", balance_remain=10)
            for uid in ("u0", "u1", "u2")
        ])
        self.db.commit()
        POOL.sticky = StickyRouter()

    def tearDown(self):
        self.db.close()
        POOL.sticky = StickyRouter()

    def test_stream_path_binds_sticky(self):
        body = {"model": "main-auto",
                "messages": [{"role": "user", "content": "一段固定的对话"}]}
        sticky_key = marvis_gateway._sticky_key_for(body)
        self.assertTrue(sticky_key, "首条 user 文本相同 → 必须算得出会话键")

        picked = []

        def fake_stream(data, realm, payload, *, account_id=0):
            picked.append(account_id)
            return object()

        with mock.patch.object(marvis_gateway.marvis_accounts, "ensure_token",
                               lambda db, account: {}), \
             mock.patch.object(marvis_gateway.client, "open_chat_stream",
                               fake_stream), \
             mock.patch.object(marvis_gateway, "risk_backoff_remaining",
                               lambda: 0):
            marvis_gateway._attempt(self.db, None, None, dict(body), "", stream=True)

        self.assertEqual(len(picked), 1, "流式只发一次上游")
        used = self.db.get(Account, picked[0])
        self.assertIsNotNone(used)
        self.assertEqual(
            POOL.sticky.resolve(sticky_key, {"u0", "u1", "u2"}), used.uid,
            "流式成功后必须把会话绑到刚成功的账号上")

    def test_stream_bind_is_not_shared_across_sessions(self):
        """不同对话绑不同号：不能因为都走流式就退化成全局单号。"""
        def stream_body(text):
            return {"model": "main-auto",
                    "messages": [{"role": "user", "content": text}]}

        def run(b):
            with mock.patch.object(marvis_gateway.marvis_accounts, "ensure_token",
                                   lambda db, account: {}), \
                 mock.patch.object(marvis_gateway.client, "open_chat_stream",
                                   lambda data, realm, payload, *, account_id=0: object()), \
                 mock.patch.object(marvis_gateway, "risk_backoff_remaining",
                                   lambda: 0):
                marvis_gateway._attempt(self.db, None, None, dict(b), "", stream=True)

        run(stream_body("对话甲"))
        run(stream_body("对话乙"))
        ka = marvis_gateway._sticky_key_for(stream_body("对话甲"))
        kb = marvis_gateway._sticky_key_for(stream_body("对话乙"))
        self.assertNotEqual(ka, kb)
        self.assertIsNotNone(POOL.sticky.resolve(ka, {"u0", "u1", "u2"}))
        self.assertIsNotNone(POOL.sticky.resolve(kb, {"u0", "u1", "u2"}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
