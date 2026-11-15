# -*- coding: utf-8 -*-
"""平台自评等级（`PlatformSpec.rating`）的契约锁定。

控制台「平均级」徽标的数据来自平台自己声明（`/api/platforms` 的 `rating`），
前端 `platform-tiers.js` 只留兜底表。这三条必须一直成立：

1. 四个档位之外的 tier 一律**丢掉**（否则徽标没有样式，成了空壳）；
2. `reason` 要原样透给前端（悬停展示的依据）；
3. 没声明 rating 的平台，`/api/platforms` 给空 dict（前端才会回落兜底表）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from admin.platform import registry, loader  # noqa: E402
from admin.platform import router as platform_router  # noqa: E402
from admin.platform.registry import PlatformSpec  # noqa: E402

TIERS = ("hang", "elite", "npc", "bad")


class RatingContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        loader.load_platforms()

    def test_known_tiers_survive(self):
        for tier in TIERS:
            spec = PlatformSpec(id="probe-" + tier, label="probe",
                                rating={"tier": tier, "reason": "为什么"})
            payload = platform_router._rating_of(spec)
            self.assertEqual(payload["tier"], tier)
            self.assertEqual(payload["reason"], "为什么")

    def test_unknown_tier_is_dropped(self):
        spec = PlatformSpec(id="probe-bad-tier", label="probe",
                            rating={"tier": "good", "reason": "写错了"})
        self.assertEqual(platform_router._rating_of(spec), {},
                         "未知档位必须丢弃 —— 否则前端会出现没样式的空徽标")

    def test_missing_rating_is_empty(self):
        spec = PlatformSpec(id="probe-none", label="probe")
        self.assertEqual(platform_router._rating_of(spec), {})
        self.assertEqual(platform_router._serialize(spec)["rating"], {})

    def test_serialize_passes_rating_through(self):
        spec = PlatformSpec(id="probe-serialize", label="probe",
                            rating={"tier": "elite", "reason": "够用"})
        self.assertEqual(platform_router._serialize(spec)["rating"]["tier"], "elite")

    def test_builtin_platforms_declare_valid_rating(self):
        """内置平台都要有合法的自评，别让卡片一半有一半没有。"""
        for pid in registry.ids():
            spec = registry.get(pid)
            if spec is None:
                continue
            rating = spec.rating or {}
            self.assertIn(rating.get("tier"), TIERS,
                          "%s 的 rating.tier 非法或缺失：%r" % (pid, rating))
            self.assertTrue(str(rating.get("reason") or "").strip(),
                            "%s 的 rating 缺少 reason（悬停会是空的）" % pid)


if __name__ == "__main__":
    unittest.main(verbosity=2)