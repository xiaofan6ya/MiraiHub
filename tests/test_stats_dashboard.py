# -*- coding: utf-8 -*-
"""数据看板（/api/stats/dashboard）的回归锁定。

看板是「一屏给结论」的页面，最怕两件事：

1. **口径错**：失败请求被算进 Token/积分（消费虚高），或者成功率把失败漏掉
   永远显示 100%。这里用「成功 + 失败」混合样本把两条口径都钉住。
2. **图形对不上卡片**：堆叠分布的桶必须连续（没有请求的小时也要留空位），
   否则三天的空白会被压成一条线，看起来像服务一直在忙。

另外锁住两条策略：
* 超过 7 天自动降级为按天 —— 30 天 × 24 桶 = 720 根柱子没有信息量；
* 「其他 N 个模型」必须把 Top N 之外的量真实滚进去，不能凭空丢掉。
"""
import os
import sys
import unittest
from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from admin import stats_cache  # noqa: E402
from admin.db import get_db  # noqa: E402
from admin.models import (Account, Base, ModelConfig, UsageLog)  # noqa: E402
from admin.routers import stats as stats_routes  # noqa: E402
from admin.security import require_admin  # noqa: E402


def make_app():
    app = FastAPI()
    app.include_router(stats_routes.router)
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def _override():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[require_admin] = lambda: True
    app.dependency_overrides[get_db] = _override
    return app, session_factory


class StatsDashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app, cls.session_factory = make_app()
        cls.client = TestClient(cls.app)
        # 本机若开了 Redis，L2 会把上一次运行的看板结果留在库里 ——
        # 用例之间必须隔离，所以这里把 L2 直接钉成「不可用」（只测计算口径）。
        stats_cache._redis = None
        stats_cache._redis_ready = True

    def setUp(self):
        # 统计缓存是进程级共享的：不清会把上一个用例的结果带进来。
        stats_cache._l1.clear()
        db = self.session_factory()
        db.query(UsageLog).delete()
        db.commit()
        now = datetime.utcnow()
        rows = []
        # 24 条成功 + 6 条失败，分布在两个平台 / 三个模型上。
        for i in range(24):
            rows.append(UsageLog(
                api_key_id=1, account_id=1 + (i % 2),
                model=["dfmodel", "kmodel", "gpt-4.1"][i % 3],
                platform="workbuddy" if i % 2 else "loomy",
                use_case="chat", credits=0.5,
                prompt_tokens=1000, completion_tokens=500, total_tokens=1500,
                cached_tokens=200, ttfb_ms=400, latency_ms=12000,
                error_kind="", created_at=now - timedelta(minutes=11 * i),
            ))
        for i in range(6):
            rows.append(UsageLog(
                api_key_id=1, account_id=1, model="dfmodel", platform="workbuddy",
                use_case="chat", credits=0, prompt_tokens=None, completion_tokens=None,
                total_tokens=None, cached_tokens=None, ttfb_ms=None, latency_ms=None,
                error_kind="server", created_at=now - timedelta(minutes=17 * i),
            ))
        db.add_all(rows)
        db.commit()
        db.close()

    def get(self, **params):
        response = self.client.get("/api/stats/dashboard", params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_kpi_separates_success_and_failure(self):
        data = self.get(days=1)
        summary = data["summary"]
        self.assertEqual(summary["requests"], 30)
        self.assertEqual(summary["successes"], 24)
        self.assertEqual(summary["failures"], 6)
        self.assertAlmostEqual(summary["success_rate"], 0.8, places=4)
        # 失败请求没有 Token/积分，绝不能算进消费
        self.assertEqual(summary["total_tokens"], 24 * 1500)
        self.assertAlmostEqual(summary["credits"], 24 * 0.5, places=2)
        self.assertGreater(summary["throughput_tps"], 0)
        self.assertAlmostEqual(summary["cache_hit_rate"], 0.2, places=3)
        # RPM/TPM 是「窗口内每分钟」，量级要落在合理区间而不是除以请求数
        self.assertGreater(summary["rpm"], 0)
        self.assertGreater(summary["tpm"], 0)
        self.assertEqual(data["errors"], [{"kind": "server", "count": 6}])

    def test_distribution_buckets_are_contiguous(self):
        data = self.get(days=1)
        buckets = data["distribution"]["buckets"]
        self.assertGreaterEqual(len(buckets), 20)          # 24 小时窗口按 1 小时铺满
        self.assertEqual(len(buckets), len(set(buckets)))   # 不重复
        self.assertEqual(buckets, sorted(buckets))          # 按时间正序
        series = data["distribution"]["series"]
        self.assertTrue(series)
        # 每个系列的 values 长度必须与桶数一致，否则前端堆叠会错位
        for item in series:
            self.assertEqual(len(item["values"]), len(buckets))

    def test_other_models_rollup_keeps_totals(self):
        data = self.get(days=1, top_models=1)
        series = data["distribution"]["series"]
        self.assertEqual(len(series), 2, "Top1 之后必须有「其他 N 个模型」")
        self.assertTrue(series[-1]["name"].startswith("其他"))
        stacked = sum(round(sum(item["values"]), 2) for item in series)
        self.assertAlmostEqual(stacked, data["summary"]["credits"], places=2)

    def test_ranking_shares_add_up(self):
        data = self.get(days=1)
        ranking = data["ranking"]
        self.assertEqual(len(ranking), 3)
        self.assertAlmostEqual(sum(row["share"] for row in ranking), 1.0, places=3)
        self.assertEqual(ranking[0]["requests"], 14)       # dfmodel: 8 成功 + 6 失败

    def test_long_window_falls_back_to_days(self):
        data = self.get(days=30, granularity="hour")
        self.assertEqual(data["window"]["granularity"], "day")
        self.assertLessEqual(len(data["distribution"]["buckets"]), 31)
        self.assertEqual(data["summary"]["requests"], 30)

    def test_platform_filter(self):
        data = self.get(days=1, platform="loomy")
        self.assertEqual(data["platform_label"], data["platforms"][0]["label"])
        self.assertEqual(len(data["platforms"]), 1)
        self.assertEqual(data["summary"]["requests"], 12)  # i%2==0 的 12 条成功请求
        self.assertEqual(data["summary"]["failures"], 0)

    def test_empty_window_is_safe(self):
        data = self.get(days=1, model="not-exist-model")
        self.assertEqual(data["summary"]["requests"], 0)
        self.assertIsNone(data["summary"]["success_rate"])
        self.assertEqual(data["ranking"], [])
        self.assertTrue(data["distribution"]["series"] == [])

    def test_sum_coercion_handles_mysql_decimal(self):
        """MySQL 的 SUM() 返回 Decimal；直接拿它除 float 会 500（线上已复现）。"""
        from decimal import Decimal

        from admin import rollup

        latency = rollup._coerce("latency_sum", Decimal("12000"))
        self.assertEqual(latency, 12000)
        self.assertIsInstance(latency, int)
        credits = rollup._coerce("credits", Decimal("0.5"))
        self.assertAlmostEqual(credits, 0.5)
        self.assertIsInstance(credits, float)
        # None 也要能落地（空窗口 / 全失败）
        self.assertEqual(rollup._coerce("requests", None), 0)
        self.assertEqual(rollup._coerce("credits", None), 0.0)
        # 复现事故现场：int/Decimal 混算必须不出异常
        self.assertAlmostEqual(latency / float("0.5"), 24000.0)

    def test_model_market_joins_pricing_and_availability(self):
        """模型广场：用量来自汇总、价格来自配置、可用性看活跃账号 − 冷却。"""
        db = self.session_factory()
        try:
            db.add(ModelConfig(level="system", platform="workbuddy",
                               model_id="dfmodel", enabled=1, credit_multiplier=0.5))
            db.add(ModelConfig(level="system", platform="workbuddy",
                               model_id="never-used-model", enabled=1,
                               credit_multiplier=0))
            db.add(Account(platform="workbuddy", name="a1", uid="u1",
                           auth_json="{}", status="active"))
            db.commit()
        finally:
            db.close()

        response = self.client.get("/api/stats/model-market", params={"hours": 24})
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["source"], "rollup")
        by_key = {item["key"]: item for item in data["items"]}

        # dfmodel 有真实调用：请求数、成功率、延迟都要在
        hit = next(item for item in data["items"] if "dfmodel" in item["models"])
        self.assertGreater(hit["requests"], 0)
        self.assertFalse(hit["free"])
        self.assertAlmostEqual(hit["min_multiplier"], 0.5, places=3)
        self.assertTrue(hit["available"])
        self.assertGreater(hit["avg_latency_ms"], 0)
        # never-used-model 没有流量也要出现在广场里（0 次调用），且标记为免费
        zero = next(item for item in data["items"]
                    if "never-used-model" in item["models"])
        self.assertEqual(zero["requests"], 0)
        self.assertTrue(zero["free"])
        self.assertIsNone(zero["success_rate"])
        self.assertTrue(by_key)


if __name__ == "__main__":
    unittest.main(verbosity=2)