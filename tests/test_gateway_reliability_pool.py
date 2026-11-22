# -*- coding: utf-8 -*-
# PERSISTENT REGRESSION TESTS — keep this file.
"""空 UID 账号参与并发租约计数 —— 合成回归测试（无网络、无真实上游、临时 sqlite）。

## 根因

InFlight 用 account.uid 当计数键，而调用侧（PoolRuntime.available /
acquire_account / release、shared.select_account 的忙碌排序、shared.release、
pool.expiry_weighted_pick 的 busy 查询）一律把空 UID 归一成空串 ""。
InFlight 对空键是**「不计数」的旧语义**（acquire 直接放行、full 恒 False、
count 恒 0、release 直接返回），于是：

  * 所有空 UID 账号共用同一个「不计数」通道 —— 在途上限对它们完全失效；
  * 它们在任何时候看起来都最闲（count 恒 0），选号永远压在它们身上；
  * 释放也落在 "" 上，即便计数曾经生效也扣不回来。

## 修法

账号身份键 account_key(account) / PoolRuntime.key_for(account)（双命名空间）：

  * uid 非空      -> "uid:<uid>"（**重复 uid 继续共享计数**，保持原行为）；
  * uid 空、id 有 -> "id:<id>"（稳定且一账号一键）；
  * 两者都空      -> ""（保持「不计数」的旧语义）。

"uid:" 前缀不是装饰：没有它，字面量恰好是 "id:42" 的合法 UID 会和「uid 空、
id=42」的账号算出同一个键（见 test_uid_namespace_never_collides_with_fallback）。

account_key / key_for 的返回值是 LeaseKey（str 子类，类型标记）：release/acquire 只
按**类型**区分「已算好的键」与「旧式原始 uid」，不再用字符串前缀猜 —— 见
test_normalize_key_is_type_based_not_prefix_based 与
test_raw_uid_looking_like_fallback_never_hits_fallback_key。

InFlight 各方法仍然只认原始 key 字符串，语义不变。
"""
import os
import pathlib
import sys
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import admin.pool as pool  # noqa: E402
from admin.pool import InFlight, PoolRuntime  # noqa: E402

POOL = pool.POOL


# ---------------------------------------------------------------------------
# 替身与夹具
# ---------------------------------------------------------------------------
class FakeAccount:
    """最小账号替身：只带 pool 会读的字段（uid / id / 在途上限 / 活跃度）。"""

    def __init__(self, id, uid="", max_in_flight=None):
        self.id = id
        self.uid = uid
        self.max_in_flight = max_in_flight
        # last_used_at / created_at 都为 None -> is_cold() 为 False，不走冷号回温。
        self.last_used_at = None
        self.created_at = None
        self.balance_remain = 100
        self.credits_expiring_soon = 0
        self.credits_soonest_expire_at = None
        # 后台链路（account_health.check_account 等）会读的字段：保持「健康、启用」。
        self.name = ""
        self.platform = "workbuddy"
        self.status = "active"
        self.cool_kind = ""
        self.auth_json = "{}"


@pytest.fixture()
def runtime():
    """每个用例一个干净的 PoolRuntime（不碰全局 POOL）。"""
    return PoolRuntime()


@pytest.fixture()
def global_pool():
    """临时清空全局 POOL 的在途计数，结束后原样还原（避免污染其它用例）。"""
    saved_counts = POOL.inflight.snapshot()
    saved_max = POOL.max_in_flight
    # 测试隔离需要重置内部计数器；这里显式操作私有状态，不走公共 API 以免影响生产语义。
    with POOL.inflight._lock:
        POOL.inflight._counts.clear()
    try:
        yield POOL
    finally:
        with POOL.inflight._lock:
            POOL.inflight._counts.clear()
            POOL.inflight._counts.update(saved_counts)
        POOL.max_in_flight = saved_max


def _key_of(account):
    """取账号身份键（修复点 API）。缺失即用例失败，而不是静默降级。"""
    assert hasattr(pool, "account_key"), "admin.pool 缺少 account_key()"
    return pool.account_key(account)


# ---------------------------------------------------------------------------
# 1. 身份键口径
# ---------------------------------------------------------------------------
def test_account_key_uid_wins_then_id_then_empty():
    assert _key_of(FakeAccount(id=7, uid="u-abc")) == "uid:u-abc"
    assert _key_of(FakeAccount(id=7, uid="")) == "id:7"
    assert _key_of(FakeAccount(id=None, uid="")) == ""
    # uid 为 None（历史脏数据）等同空
    assert _key_of(FakeAccount(id=9, uid=None)) == "id:9"


def test_pool_runtime_key_for_matches_account_key(runtime):
    account = FakeAccount(id=11, uid="")
    assert runtime.key_for(account) == _key_of(account) == "id:11"


# ---------------------------------------------------------------------------
# 2. 空 UID 账号受在途上限约束，且释放后可再租
# ---------------------------------------------------------------------------
def test_empty_uid_account_respects_limit(runtime):
    runtime.max_in_flight = 1
    account = FakeAccount(id=1, uid="")

    assert runtime.acquire_account(account) is True
    assert runtime.inflight.count(_key_of(account)) == 1
    assert runtime.acquire_account(account) is False, "空 UID 账号第二次租约必须被上限拒绝"

    runtime.release(runtime.key_for(account))
    assert runtime.inflight.count(_key_of(account)) == 0
    assert runtime.acquire_account(account) is True, "释放后必须可以再次租到"


def test_release_is_idempotent_and_never_negative(runtime):
    runtime.max_in_flight = 1
    account = FakeAccount(id=2, uid="")
    key = runtime.key_for(account)

    assert runtime.acquire_account(account) is True
    runtime.release(key)
    runtime.release(key)          # 重复释放：幂等
    assert runtime.inflight.count(key) == 0, "重复释放不能把计数扣成负数"

    # 原始 key 是空串时（账号完全无身份）仍是「不计数」的旧语义
    runtime.release("")
    assert runtime.inflight.count("") == 0


# ---------------------------------------------------------------------------
# 3. 不同空 UID 账号不共用计数键
# ---------------------------------------------------------------------------
def test_two_empty_uid_accounts_have_independent_budgets(runtime):
    runtime.max_in_flight = 1
    a = FakeAccount(id=101, uid="")
    b = FakeAccount(id=102, uid="")

    assert runtime.acquire_account(a) is True
    assert runtime.acquire_account(b) is True, "不同空 UID 账号不能因共用计数键而互相挤占"
    assert runtime.inflight.count(_key_of(a)) == 1
    assert runtime.inflight.count(_key_of(b)) == 1
    assert runtime.acquire_account(a) is False
    assert runtime.acquire_account(b) is False


# ---------------------------------------------------------------------------
# 4. limit=0：只计数、永不拒绝
# ---------------------------------------------------------------------------
def test_unlimited_limit_still_counts(runtime):
    runtime.max_in_flight = 0
    account = FakeAccount(id=3, uid="")
    key = runtime.key_for(account)

    for expected in (1, 2, 3):
        assert runtime.acquire_account(account) is True
        assert runtime.inflight.count(key) == expected

    runtime.release(key)
    assert runtime.inflight.count(key) == 2


# ---------------------------------------------------------------------------
# 5. 非空 UID / 重复 UID：现有行为不变（共享计数）
# ---------------------------------------------------------------------------
def test_duplicate_nonempty_uid_still_shares_counter(runtime):
    runtime.max_in_flight = 1
    a = FakeAccount(id=201, uid="same-uid")
    b = FakeAccount(id=202, uid="same-uid")

    assert runtime.acquire_account(a) is True
    assert runtime.acquire_account(b) is False, "重复 UID 继续共享同一计数键"
    assert runtime.inflight.count("uid:same-uid") == 1

    runtime.release(runtime.key_for(b))
    assert runtime.acquire_account(b) is True, "同 UID 的另一个账号释放后应能租到"


def test_inflight_raw_key_contract_unchanged():
    """InFlight 仍只认原始 key：空 key 不计数，非空 key 计数 + 上限拒绝。"""
    inflight = InFlight()
    assert inflight.acquire("", 1) is True
    assert inflight.acquire("", 1) is True
    assert inflight.count("") == 0
    assert inflight.full("", 1) is False

    assert inflight.acquire("k", 1) is True
    assert inflight.acquire("k", 1) is False
    assert inflight.full("k", 1) is True
    inflight.release("k")
    assert inflight.count("k") == 0
    inflight.release("k")
    assert inflight.count("k") == 0


# ---------------------------------------------------------------------------
# 6. POOL.available 排除已满账号（含空 UID 账号）
# ---------------------------------------------------------------------------
def test_pool_available_excludes_full_empty_uid_account(global_pool):
    global_pool.max_in_flight = 1
    a = FakeAccount(id=301, uid="")
    b = FakeAccount(id=302, uid="")
    c = FakeAccount(id=303, uid="uid-c")

    assert global_pool.acquire_account(a) is True
    available = global_pool.available([a, b, c])
    assert a not in available, "在途已满的空 UID 账号必须被 POOL.available() 排除"
    assert b in available and c in available

    assert global_pool.acquire_account(c) is True
    available = global_pool.available([a, b, c])
    assert c not in available
    assert available == [b]


# ---------------------------------------------------------------------------
# 7. expiry_weighted_pick 的忙碌查询走账号身份键
# ---------------------------------------------------------------------------
class _SpyInflight:
    def __init__(self):
        self.keys = []

    def count(self, key):
        self.keys.append(key)
        return 0


def test_expiry_weighted_pick_busy_lookup_uses_account_key():
    spy = _SpyInflight()
    a = FakeAccount(id=401, uid="")
    b = FakeAccount(id=402, uid="uid-402")

    pool.expiry_weighted_pick([a, b], inflight=spy)

    assert "id:401" in spy.keys, "空 UID 账号的忙碌查询必须用 id 键，而不是空串"
    assert "uid:uid-402" in spy.keys, "非空 UID 账号的忙碌查询走 uid: 命名空间"
    assert "" not in spy.keys


# ---------------------------------------------------------------------------
# 8. shared.lease / shared.release 走同一身份键
# ---------------------------------------------------------------------------
def test_shared_release_releases_empty_uid_key(global_pool):
    from admin.platform import shared

    global_pool.max_in_flight = 1
    account = FakeAccount(id=501, uid="")

    assert shared.lease(account) is True
    assert shared.lease(account) is False, "shared.lease 必须受空 UID 账号的在途上限约束"
    shared.release(account)
    assert shared.lease(account) is True, "shared.release 必须释放 id 键而不落到空串上"
    shared.release(account)
    shared.release(account)       # 幂等


# ---------------------------------------------------------------------------
# 9. shared.select_account 的忙碌排序走账号身份键（临时 sqlite）
# ---------------------------------------------------------------------------
@pytest.fixture()
def sqlite_db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from admin.db import Base

    path = os.path.join(tempfile.gettempdir(),
                        "gateway_rel_pool_%d.db" % os.getpid())
    if os.path.exists(path):
        os.remove(path)
    engine = create_engine("sqlite:///" + path)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()
        try:
            os.remove(path)
        except OSError:
            pass


def test_select_account_prefers_idle_empty_uid_account(global_pool, sqlite_db):
    from admin.models import Account
    from admin.platform import shared

    busy = Account(name="busy", platform="workbuddy", uid="", auth_json="{}",
                   status="active", max_in_flight=2)
    idle = Account(name="idle", platform="workbuddy", uid="", auth_json="{}",
                   status="active", max_in_flight=2)
    sqlite_db.add_all([busy, idle])
    sqlite_db.commit()

    # busy 的在途 = 1（未满，所以不会被 POOL.available 滤掉），必须由排序把它排到后面。
    assert global_pool.acquire_account(busy) is True
    try:
        picked = shared.select_account(sqlite_db, storage="workbuddy")
        assert picked is not None
        assert picked.id == idle.id, (
            "忙碌排序必须读到空 UID 账号的在途数，最闲的号应被选中")
    finally:
        global_pool.release(busy)

# ---------------------------------------------------------------------------
# 10. 调用点契约：release(account) / held 键（proxy、trae holder 形态）
# ---------------------------------------------------------------------------
def test_release_accepts_account_and_clears_id_key(global_pool):
    """POOL.release(account) 直接吃账号对象：空 UID 账号释放的是 "id:<id>" 键。"""
    global_pool.max_in_flight = 1
    account = FakeAccount(id=601, uid="")
    key = global_pool.key_for(account)
    assert key == "id:601"

    assert global_pool.acquire_account(account) is True
    assert global_pool.inflight.count(key) == 1
    global_pool.release(account)                      # 调用点新形态
    assert global_pool.inflight.count(key) == 0, "release(account) 必须释放 id 键"
    assert global_pool.acquire_account(account) is True, "释放后必须能再次租到"
    global_pool.release(account)


def test_release_account_equals_release_uid_for_nonempty_uid(global_pool):
    """非空 UID：release(account) 与 release(uid) 等价（原行为不变）。"""
    global_pool.max_in_flight = 1
    account = FakeAccount(id=602, uid="uid-602")

    assert global_pool.acquire_account(account) is True
    global_pool.release(account)
    assert global_pool.inflight.count("uid:uid-602") == 0

    assert global_pool.acquire_account(account) is True
    global_pool.release("uid-602")                    # 旧式原始 uid 字符串
    assert global_pool.inflight.count("uid:uid-602") == 0, "原始 uid 释放也必须对上命名空间键"
    assert global_pool.inflight.count(global_pool.key_for(account)) == 0


def test_proxy_style_held_key_release_without_uid(global_pool):
    """proxy 形态：held = POOL.key_for(acc)，随后只靠 held 释放（不回看 uid）。

    空 UID 账号的 held 必须是非空键（否则 finally 里的 if held_uid: 守卫会跳过释放，
    名额只借不还）。
    """
    global_pool.max_in_flight = 1
    account = FakeAccount(id=603, uid="")

    leased = global_pool.acquire_account(account)
    held = global_pool.key_for(account) if leased else ""
    assert leased is True
    assert held, "空 UID 账号的租约键不能是空串"
    assert global_pool.inflight.count(held) == 1

    if held:                                          # 与 finally 分支同形
        global_pool.release(held)
    assert global_pool.inflight.count(held) == 0


def test_trae_holder_key_takes_precedence(global_pool):
    """trae holder 形态：holder 里同时有 uid 与 key 时，_release 用的是 key。"""
    global_pool.max_in_flight = 1
    account = FakeAccount(id=604, uid="")

    assert global_pool.acquire_account(account) is True
    holder = {"uid": account.uid, "key": global_pool.key_for(account)}
    # 与 admin/platform/trae/gateway.py::_release 同一取值口径
    global_pool.release(holder.get("key") or holder.get("uid") or "")
    assert global_pool.inflight.count("id:604") == 0

# ---------------------------------------------------------------------------
# 11. 双命名空间：fallback 键绝不与合法 UID 相撞
# ---------------------------------------------------------------------------
def test_uid_namespace_never_collides_with_fallback(runtime):
    """uid 字面量就是 "id:42" 的账号，不能和「uid 空、id=42」的账号共用计数键。"""
    literal = FakeAccount(id=999, uid="id:42")     # 合法 UID 恰好长得像 fallback 键
    fallback = FakeAccount(id=42, uid="")

    key_literal = _key_of(literal)
    key_fallback = _key_of(fallback)
    assert key_literal == "uid:id:42"
    assert key_fallback == "id:42"
    assert key_literal != key_fallback, "两个不同账号不能算出同一个计数键"

    runtime.max_in_flight = 1
    assert runtime.acquire_account(literal) is True
    assert runtime.acquire_account(fallback) is True, "命名空间分离后两者互不挤占"
    assert runtime.inflight.count(key_literal) == 1
    assert runtime.inflight.count(key_fallback) == 1
    assert runtime.acquire_account(literal) is False
    assert runtime.acquire_account(fallback) is False

    runtime.release(literal)
    assert runtime.inflight.count(key_literal) == 0
    assert runtime.inflight.count(key_fallback) == 1, "释放一个字面量像 fallback 的 UID 不能误扣另一账号"
    runtime.release(fallback)


def test_normalize_key_is_type_based_not_prefix_based(runtime):
    """normalize_key 按类型判断：LeaseKey 原样，其它非空字符串一律当原始 uid。"""
    from admin.pool import LeaseKey, normalize_key

    assert normalize_key("") == ""
    assert normalize_key(None) == ""
    # 原始 uid 形态：补 "uid:"。注意 "uid:abc"/"id:42" 作为**原始 uid** 也要补前缀，
    # 这正是「不靠前缀猜」的关键 —— 前缀猜法会在这两个值上漏判。
    assert normalize_key("abc") == "uid:abc"
    assert normalize_key("uid:abc") == "uid:uid:abc"
    assert normalize_key("id:42") == "uid:id:42"

    # 计算键（LeaseKey）原样返回，且保留类型
    calc = _key_of(FakeAccount(id=42, uid=""))
    assert isinstance(calc, LeaseKey), "account_key 必须返回 LeaseKey 类型标记"
    assert normalize_key(calc) == "id:42"
    assert isinstance(normalize_key(calc), LeaseKey)
    # 同一个字面量，字符串形态与计算形态必须落在不同键上
    assert normalize_key("id:42") != normalize_key(calc)

    # POOL.acquire(原始 uid) 与 POOL.release(同一原始 uid) 落在同一个键上
    runtime.max_in_flight = 1
    assert runtime.acquire("abc") is True
    assert runtime.inflight.count("uid:abc") == 1
    assert runtime.acquire("abc") is False, "同一原始 uid 第二次必须被上限拒绝"
    # 旧的「字符串前缀即键」猜法会认为 "uid:abc" 已是键；现在它只是另一个原始 uid
    assert runtime.inflight.count("uid:uid:abc") == 0
    runtime.release("abc")
    assert runtime.inflight.count("uid:abc") == 0


def test_raw_uid_looking_like_fallback_never_hits_fallback_key(runtime):
    """原始 uid 字面量为 "id:42" 的旧式调用点，绝不能碰到 fallback 账号的 "id:42"。"""
    from admin.pool import LeaseKey

    runtime.max_in_flight = 1
    fallback = FakeAccount(id=42, uid="")
    fallback_key = runtime.key_for(fallback)
    assert isinstance(fallback_key, LeaseKey)

    assert runtime.acquire_account(fallback) is True
    assert runtime.inflight.count(fallback_key) == 1

    # 旧式调用点：POOL.acquire("id:42") —— 原始 uid 语义，落到 "uid:id:42"
    assert runtime.acquire("id:42") is True
    assert runtime.inflight.count("uid:id:42") == 1
    assert runtime.inflight.count(fallback_key) == 1, "原始 uid 不能加到 fallback 计数上"

    # 旧式调用点：POOL.release("id:42") —— 也不能扣 fallback 账号的名额
    runtime.release("id:42")
    assert runtime.inflight.count("uid:id:42") == 0
    assert runtime.inflight.count(fallback_key) == 1, "原始 uid 释放不能误扣 fallback 账号"

    # 同一个字面量作为 LeaseKey 时，精确命中 fallback 键
    runtime.release(fallback_key)
    assert runtime.inflight.count(fallback_key) == 0


# ---------------------------------------------------------------------------
# 12. maintenance 键与会话租约键一致
# ---------------------------------------------------------------------------
def test_maintenance_key_matches_lease_key(global_pool):
    """account_health / ban_recheck / maintenance 三处 maintenance 键 = key_for(account)。"""
    global_pool.max_in_flight = 1
    account = FakeAccount(id=701, uid="")
    key = global_pool.key_for(account)
    assert key == "id:701"

    # 会话租约在途 → 维护保留必须被拒（drain=False 分支看的就是这个键）
    assert global_pool.acquire_account(account) is True
    assert global_pool.inflight.acquire_maintenance(key) is False, (
        "维护键与会话租约键不一致时这里会误判为空闲")
    global_pool.release(account)

    # 空闲 → 可保留；保留期间会话租约也拿不到名额
    assert global_pool.inflight.acquire_maintenance(key) is True
    assert global_pool.acquire_account(account) is False, "维护保留期间不得再发新会话租约"
    global_pool.inflight.release_maintenance(key)
    assert global_pool.acquire_account(account) is True
    global_pool.release(account)


def test_maintenance_call_sites_use_pool_key_for():
    """三个维护调用点必须用 POOL.key_for(account)，不再自造 "account:<id>" 键。"""
    sources = {
        "admin/account_health.py": 'f"account:{account.id}"',
        "admin/ban_recheck.py": 'f"account:{acc.id}"',
        "admin/maintenance.py": 'f"account:{account.id}"',
    }
    for rel, legacy in sources.items():
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert legacy not in text, "%s 仍在使用 %s 身份键" % (rel, legacy)
        assert "POOL.key_for(" in text, "%s 未使用 POOL.key_for()" % rel

# ---------------------------------------------------------------------------
# 13. 行为回归：maintenance 保留 / token_slot / account_health / stats
# ---------------------------------------------------------------------------
def test_maintenance_reservation_excludes_from_pool_available(global_pool):
    """维护保留（acquire_maintenance）必须让账号从 POOL.available() 里消失。"""
    global_pool.max_in_flight = 1
    account = FakeAccount(id=801, uid="")
    key = global_pool.key_for(account)

    assert account in global_pool.available([account])
    assert global_pool.inflight.acquire_maintenance(key) is True
    try:
        assert account not in global_pool.available([account]), (
            "维护保留中的账号不能被并发选号选中")
    finally:
        global_pool.inflight.release_maintenance(key)
    assert account in global_pool.available([account]), "释放维护保留后必须重新可选"


def _capture_maintenance(monkeypatch, *, result=False):
    """把 POOL.inflight.acquire_maintenance 换成记录 key 的假函数。"""
    captured = []

    def fake(uid, *, drain=False):
        captured.append((uid, drain))
        return result

    monkeypatch.setattr(POOL.inflight, "acquire_maintenance", fake)
    return captured


def test_token_slot_uses_pool_key_for(monkeypatch):
    """maintenance.token_slot 的 maintenance 保留键必须是 POOL.key_for(account)。"""
    import threading as _threading

    from admin import maintenance

    account = FakeAccount(id=802, uid="")
    monkeypatch.setattr(maintenance, "_batch_lock", _threading.Lock())
    monkeypatch.setattr(maintenance.settings, "KEEPALIVE_ACCOUNT_GAP", 0)
    captured = _capture_maintenance(monkeypatch, result=False)

    with maintenance.token_slot(account, None) as reason:
        assert reason == "inflight", "保留失败时必须返回 inflight，而不是继续换 token"

    assert captured, "token_slot 必须尝试 acquire_maintenance"
    assert captured[0][0] == POOL.key_for(account), (
        "token_slot 的 maintenance 键必须与会话租约键一致（空 UID = id:<id>）")
    assert captured[0][1] is True, "token_slot 是 drain 语义"


def test_account_health_uses_pool_key_for(monkeypatch):
    """account_health.check_account 的 maintenance 保留键同样是 POOL.key_for(account)。"""
    import threading as _threading

    from admin import account_health, maintenance

    account = FakeAccount(id=803, uid="")
    monkeypatch.setattr(POOL.waf, "active", lambda: False)
    monkeypatch.setattr(maintenance, "_batch_lock", _threading.Lock())
    captured = _capture_maintenance(monkeypatch, result=False)

    result = account_health.check_account(None, account)

    assert captured, "check_account 必须尝试 acquire_maintenance"
    assert captured[0][0] == POOL.key_for(account)
    assert result["status"] == "skipped"
    assert "在途" in result["reason"] or "维护" in result["reason"]


def test_stats_uses_key_for_not_raw_uid(global_pool):
    """stats 的「可用账号」判据必须走 POOL.key_for(a)，且空 UID 账号能被判满。"""
    source = (ROOT / "admin/routers/stats.py").read_text(encoding="utf-8")
    hits = [line for line in source.splitlines() if "POOL.inflight.full(" in line]
    assert hits, "stats.py 里应存在 POOL.inflight.full(...) 判据"
    for line in hits:
        assert "POOL.key_for(a)" in line, "stats 的在途判据必须用 POOL.key_for(a)：%s" % line.strip()
        assert 'a.uid or ""' not in line, "stats 不得再按原始 uid 判在途：%s" % line.strip()

    # 行为侧：limit=1 的空 UID 账号借满后，stats 的 usable 判据必须为 False
    global_pool.max_in_flight = 1
    account = FakeAccount(id=804, uid="")
    assert global_pool.inflight.full(
        global_pool.key_for(account), global_pool.limit_for(account)) is False
    assert global_pool.acquire_account(account) is True
    try:
        assert global_pool.inflight.full(
            global_pool.key_for(account), global_pool.limit_for(account)) is True
    finally:
        global_pool.release(account)


# ---------------------------------------------------------------------------
# 14. LeaseKey 非碰撞的结构性证明
# ---------------------------------------------------------------------------
def test_account_key_proof_no_fallback_collision():
    """结构性证明：uid 键与前缀 fallback 键分属不同前缀集合，**不可能**相等。

    1. account_key 对**任意非空 uid** 都返回 "uid:" + uid（LeaseKey），
       因此其首 4 个字符恒为 "uid:"；
    2. account_key 对 uid 空、id 存在的账号恒返回 "id:" + id，首 3 个字符恒为 "id:"；
    3. "uid:" 不是 "id:" 的前缀，两个前缀集合不相交 —— 任意 uid 键都不可能等于
       任意 fallback 键，无论 uid 字面量长成 "id:42" / "uid:1" / 含 NUL 都改变不了
       它落在 "uid:" 命名空间这一事实；
    4. 空 uid 且无 id 时为 ""，与任何非空键也不相等。
    """
    from admin.pool import LeaseKey

    adversarial_uids = ["id:42", "id:", "uid:1", "uid:", "id:42\x00"]
    fallback = FakeAccount(id=42, uid="")
    fallback_key = _key_of(fallback)
    assert fallback_key == "id:42"
    assert isinstance(fallback_key, LeaseKey)

    uid_keys = set()
    for uid in adversarial_uids:
        key = _key_of(FakeAccount(id=900, uid=uid))
        assert isinstance(key, LeaseKey)
        assert key.startswith("uid:"), "非空 uid 一律落在 uid: 命名空间"
        uid_keys.add(str(key))

    assert fallback_key.startswith("id:")
    assert uid_keys.isdisjoint({str(fallback_key)}), "uid 键集合与 fallback 键不相交"
    # 空 uid + 无 id -> ""
    assert _key_of(FakeAccount(id=None, uid="")) == ""

# ---------------------------------------------------------------------------
# 15. proxy 粘性绑定必须用原始 UID（回归：绑 LeaseKey 会让 resolve 永远失配）
# ---------------------------------------------------------------------------
def test_proxy_sticky_binds_raw_uid_not_lease_key():
    """bind 存原始 UID 才能被 resolve 命中；绑 LeaseKey 是 t5 引入的旧 bug 形态。"""
    from admin.pool import StickyRouter

    router = StickyRouter(ttl_s=60.0, enabled=True)   # 隔离实例，不启动 GC 线程
    account = FakeAccount(id=901, uid="uid-901")
    lease_key = POOL.key_for(account)
    assert lease_key == "uid:uid-901"

    # 正确形态：绑原始 UID
    router.bind("conv-ok", account.uid)
    assert router.resolve("conv-ok", {"uid-901"}) == "uid-901", (
        "绑原始 UID 后粘性必须命中")

    # 旧 bug 形态：绑租约键（LeaseKey）—— resolve 的可用集合是原始 UID，永远匹配不上
    router.bind("conv-bug", lease_key)
    assert router.resolve("conv-bug", {"uid-901"}) is None, (
        "把 LeaseKey 绑进粘性会让会话亲和静默失效（本次回归要防的正是这个）")


def test_proxy_sticky_bind_sites_use_raw_uid():
    """proxy 源码级护栏：5 处 bind 都传 acc_i.uid，且不出现 held_uid。"""
    source = (ROOT / "admin/routers/proxy.py").read_text(encoding="utf-8")
    bind_lines = [ln for ln in source.splitlines() if "POOL.sticky.bind" in ln]
    assert len(bind_lines) >= 5, "chat/responses×2/anthropic×2 共 5 处粘性绑定"
    for line in bind_lines:
        assert "acc_i.uid" in line, "粘性绑定必须用成功账号的原始 UID：%s" % line.strip()
        assert "held_uid" not in line, "粘性绑定不得再传租约键 held_uid：%s" % line.strip()

    release_lines = [ln for ln in source.splitlines() if "POOL.release(held_uid)" in ln]
    assert len(release_lines) >= 5, "held_uid 仍必须用于释放租约（键必须与 acquire 一致）"
