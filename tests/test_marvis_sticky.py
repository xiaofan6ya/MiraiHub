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


if __name__ == "__main__":
    unittest.main(verbosity=2)
