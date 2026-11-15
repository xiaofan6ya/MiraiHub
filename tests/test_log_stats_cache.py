"""Caching and single-flight behaviour for the log/IP endpoints."""
import threading
import time
import unittest

from admin import stats_cache
from admin.routers import ip_management, logs


class StatsCacheTests(unittest.TestCase):
    def setUp(self):
        # 缓存已收敛到 admin/stats_cache.py（L1 + L2 + 单飞），旧用例还在清
        # 已经不存在的 `logs._stats_cache` —— 这里直接清共享实现。
        stats_cache._l1.clear()
        stats_cache._inflight.clear()

    def tearDown(self):
        stats_cache._l1.clear()
        stats_cache._inflight.clear()

    def test_second_read_is_served_from_cache(self):
        calls = []

        def compute():
            calls.append(1)
            return {'value': len(calls)}

        key = logs._stats_cache_key('unit', ('a',))
        first = logs._cached_stats(key, compute)
        second = logs._cached_stats(key, compute)
        self.assertEqual(len(calls), 1)
        self.assertEqual(first, second)

    def test_cached_value_is_a_copy(self):
        # `_freeze()` 只做**浅拷贝**（深拷贝在 summary 上是 0.61ms/次，缓存命中
        # 反而更慢）：调用方可以随意增删顶层键，但不该改嵌套结构。
        key = logs._stats_cache_key('unit', ('copy',))
        logs._stats_cache_put(key, {'items': [1, 2, 3]})
        value = logs._stats_cache_get(key)
        self.assertEqual(value['items'], [1, 2, 3])
        value['extra'] = 1                      # 顶层可改
        again = logs._stats_cache_get(key)
        self.assertNotIn('extra', again)
        self.assertEqual(again['items'], [1, 2, 3])

    def test_concurrent_identical_reads_share_one_computation(self):
        calls = []
        release = threading.Event()

        def slow():
            calls.append(1)
            release.wait(5)
            return {'value': 1}

        key = logs._stats_cache_key('unit', ('flight',))
        results = []
        threads = [threading.Thread(target=lambda: results.append(logs._cached_stats(key, slow)))
                   for _ in range(5)]
        for thread in threads:
            thread.start()
        time.sleep(0.3)
        release.set()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(len(calls), 1, 'waiters must reuse the leader result')
        self.assertEqual(len(results), 5)

    def test_failed_leader_unblocks_waiters(self):
        key = logs._stats_cache_key('unit', ('failure',))
        release = threading.Event()
        errors = []

        def boom():
            release.wait(5)
            raise RuntimeError('query failed')

        def follower():
            try:
                logs._cached_stats(key, lambda: {'value': 'fallback'})
            except Exception as exc:  # pragma: no cover - defensive
                errors.append(exc)

        leader = threading.Thread(target=lambda: _quiet(logs._cached_stats, key, boom))
        follower_thread = threading.Thread(target=follower)
        leader.start()
        time.sleep(0.2)
        follower_thread.start()
        release.set()
        leader.join(timeout=10)
        follower_thread.join(timeout=10)
        self.assertFalse(follower_thread.is_alive(), 'follower must not hang after a failure')
        self.assertEqual(errors, [])


def _quiet(fn, *args):
    try:
        fn(*args)
    except Exception:
        pass


class ActivityCacheTests(unittest.TestCase):
    def setUp(self):
        ip_management._activity_cache.clear()

    def tearDown(self):
        ip_management._activity_cache.clear()

    def test_activity_cache_round_trip_and_copy(self):
        key = ip_management._activity_cache_key(1, 'requests')
        ip_management._activity_cache_put(key, {'items': [{'ip': '1.1.1.1'}]})
        first = ip_management._activity_cache_get(key)
        self.assertEqual(first['items'][0]['ip'], '1.1.1.1')
        first['items'][0]['ip'] = 'mutated'
        second = ip_management._activity_cache_get(key)
        self.assertEqual(second['items'][0]['ip'], '1.1.1.1')

    def test_cache_is_bounded(self):
        for index in range(ip_management._ACTIVITY_CACHE_MAX + 10):
            ip_management._activity_cache_put(
                ip_management._activity_cache_key(index), {'items': []})
        self.assertLessEqual(len(ip_management._activity_cache),
                             ip_management._ACTIVITY_CACHE_MAX)


class StableWindowTests(unittest.TestCase):
    def test_derived_window_start_is_stable_within_a_minute(self):
        """A per-call utcnow() made every request a cache miss."""
        first = logs._stable_window_start(None, 1)
        second = logs._stable_window_start(None, 1)
        self.assertEqual(first, second)
        self.assertEqual(first.second, 0)
        self.assertEqual(first.microsecond, 0)

    def test_explicit_start_is_untouched(self):
        explicit = '2026-10-01T00:00:00Z'
        self.assertEqual(logs._stable_window_start(explicit, 1), explicit)

    def test_unlimited_range_has_no_window(self):
        self.assertIsNone(logs._stable_window_start(None, 0))

    def test_same_request_twice_hits_the_cache(self):
        calls = []

        def compute():
            calls.append(1)
            return {'ok': True}

        window = logs._stable_window_start(None, 1)
        key = logs._stats_cache_key('ip_summary', (None, None, None, None, None,
                                                   None, window, None, None, 100, 1,
                                                   'requests', 50))
        logs._cached_stats(key, compute, ttl=logs._STATS_CACHE_TTL_HEAVY)
        logs._cached_stats(key, compute, ttl=logs._STATS_CACHE_TTL_HEAVY)
        self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
