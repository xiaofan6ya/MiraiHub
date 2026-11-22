# PERSISTENT REGRESSION TESTS — keep this file in the repo.
# -*- coding: utf-8 -*-
"""网关调度统计回归：health() 可用数 / order() 空语义 / 热路径确定性计数。

覆盖计划 docs/superpowers/plans/2026-11-22-platform-scoped-model-resolution.md
任务 5（§七 第 88-90 行）：

A. health() 的可用数必须**按行**统计（active 且未在冷却），不能用
   「active 数 - 全部冷却数」—— 后者会让一行「非 active 但在冷却」的账号
   抵消一行健康的 active 账号，平台被判成不可用（计划 §一 表格最后一行）。

B. 冷却判定必须和写入口径一致（朴素 UTC + utcnow）。旧实现拿
   cool_until.timestamp() 与 time.time() 比较，在 UTC+8 下把整个冷却窗口
   提前了 8 小时 —— 10 分钟级冷却永远统计不到。

C. 同一 storage 的两个区（cn / intl）各自独立统计：第一区的冷却号不得
   抵消第二区的健康号。

D. order(None) = 全体平台；order([]) = 空（旧实现 [] 被 or 扩成全平台）；
   传入集合只在该集合内排序，任何策略都不许把池外的平台加回来；
   include_unavailable 仍只决定「池内不可用项是否保留」。

E. 热路径确定性计数：预热后每次推理请求因名称映射产生的 model_configs
   全表扫描次数目标为 0。当前基线未达标，用 xfail 记录期望值（见
   scripts/bench_dispatch_hotpath.py 的前后对照数字）。

运行：
  python -B -m pytest -p no:cacheprovider -q tests/test_gateway_reliability_scheduling.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest

# ---- 环境隔离：必须在 import admin.* 之前落地 ------------------------------
# 用**覆盖**而不是 setdefault：外部 shell 的环境不算数，本文件只允许临时 SQLite。
_TMP_DIR = Path(tempfile.mkdtemp(prefix="miraihub_sched_test_"))
_TMP_DB = _TMP_DIR / "sched.db"
os.environ["ADMIN_DATABASE_URL"] = "sqlite:///" + _TMP_DB.as_posix()

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from admin.db import Base, SessionLocal, engine  # noqa: E402
from admin.models import Account, SystemSetting  # noqa: E402
from admin.platform import gateway_router, registry  # noqa: E402
from admin.platform.registry import PlatformSpec  # noqa: E402

BENCH = ROOT / "scripts" / "bench_dispatch_hotpath.py"

#: 固定平台集合。quad-cn / quad-intl 共用 storage=quad 的号池（同 Trae 两区）；
#: pz 故意不给账号 —— 用来验证「不可用项只在 include_unavailable 时保留」。
SPECS = (
    PlatformSpec(id="hb-solo", label="HB Solo"),
    PlatformSpec(id="quad-cn", label="Quad 国内",
                 storage_platform="quad", realm="cn"),
    PlatformSpec(id="quad-intl", label="Quad 国际",
                 storage_platform="quad", realm="intl"),
    PlatformSpec(id="pa", label="PA"),
    PlatformSpec(id="pb", label="PB"),
    PlatformSpec(id="pz", label="PZ"),
)


def teardown_module(module):  # noqa: ARG001 - pytest 钩子
    """释放连接池并清掉临时库（Windows 上 WAL 文件要先放开句柄）。"""
    engine.dispose()
    for suffix in ("", "-wal", "-shm"):
        try:
            (_TMP_DIR / ("sched.db" + suffix)).unlink()
        except OSError:
            pass
    try:
        _TMP_DIR.rmdir()
    except OSError:
        pass


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def platform_registry():
    """只登记本文件的固定平台：真实平台包会把数字和平台集合带漂。"""
    saved = dict(registry._REGISTRY)
    registry._REGISTRY.clear()
    for spec in SPECS:
        registry.register(spec, replace=True)
    with mock.patch("admin.platform.loader.load_platforms",
                    lambda: {"loaded": [], "failed": {}}):
        gateway_router.invalidate()
        try:
            yield
        finally:
            gateway_router.invalidate()
            registry._REGISTRY.clear()
            registry._REGISTRY.update(saved)


@pytest.fixture()
def db():
    """空库（每个用例一份干净数据）。"""
    from admin.models import AccountModelCooldown, ApiKey

    Base.metadata.create_all(bind=engine)
    with SessionLocal() as session:
        # 三张表一起清：SQLite 的 INTEGER PRIMARY KEY 在清空后会从 1 重新分配，
        # 残留的 api_keys.account_id 会和新账号 id 撞上 —— 公共池于是把新号
        # 当成「别的 Key 独占的号」排除掉（表现为 select_account 恒 None）。
        session.query(AccountModelCooldown).delete()
        session.query(ApiKey).delete()
        session.query(Account).delete()
        session.query(SystemSetting).delete()
        session.commit()
        yield session


def add_account(db, platform, *, status="active", domain="",
                cool_minutes=None, breaker_minutes=None, remain=700):
    """插一行账号。cool_minutes>0 = 未来冷却；<0 = 已过期冷却。"""
    now = datetime.utcnow()
    row = Account(
        platform=platform,
        name="%s-%s-%s" % (platform, domain or "all", status),
        uid="uid-%s-%d" % (platform, int(now.timestamp() * 1000) % 100000),
        domain=domain,
        auth_json="{}",
        status=status,
        balance_total=1000,
        balance_remain=remain,
        cool_until=(now + timedelta(minutes=cool_minutes)
                    if cool_minutes is not None else None),
        breaker_until=(now + timedelta(minutes=breaker_minutes)
                       if breaker_minutes is not None else None),
    )
    db.add(row)
    db.commit()
    return row


def health(db, platform):
    return gateway_router.health(db, platform)


# ---------------------------------------------------------------------------
# A. health() 可用数按行统计
# ---------------------------------------------------------------------------
def test_health_usable_counts_healthy_active_despite_non_active_cooling(db):
    """计划原文那组：一行健康 active + 一行非 active 且在冷却。"""
    add_account(db, "hb-solo")                                    # 健康 active
    add_account(db, "hb-solo", status="disabled", cool_minutes=10)  # 非 active 冷却中

    data = health(db, "hb-solo")

    assert (data["accounts"], data["active"], data["cooling"], data["usable"]) == \
        (2, 1, 1, 1), "非 active 的冷却号把健康 active 抵消了：%r" % (data,)
    assert data["ok"] is True


def test_health_usable_is_not_active_minus_cooling(db):
    """active 数 - 全部冷却数 的旧公式在「两个 active + 一个非 active 冷却」上会算错。"""
    add_account(db, "hb-solo")                                     # active 健康
    add_account(db, "hb-solo", cool_minutes=30)                    # active 冷却中
    add_account(db, "hb-solo", status="disabled", cool_minutes=30)  # 非 active 冷却中

    data = health(db, "hb-solo")

    assert (data["active"], data["cooling"], data["usable"]) == (2, 2, 1)
    assert data["ok"] is True


def test_health_all_active_cooling_means_unusable(db):
    add_account(db, "hb-solo", cool_minutes=30)
    data = health(db, "hb-solo")

    assert (data["active"], data["cooling"], data["usable"], data["ok"]) == \
        (1, 1, 0, False)


@pytest.mark.parametrize("minutes", [1, 10, 60, 480, 12 * 60])
def test_health_cooling_window_uses_utc_naive_deadline(db, minutes):
    """冷却判定必须按朴素 UTC 比较：短窗口（10 分钟级）以前统计不到。"""
    add_account(db, "hb-solo", cool_minutes=minutes)
    assert health(db, "hb-solo")["cooling"] == 1, "%d 分钟后的冷却没被统计到" % minutes


def test_health_expired_cooling_is_not_cooling(db):
    add_account(db, "hb-solo", cool_minutes=-5)
    data = health(db, "hb-solo")

    assert (data["cooling"], data["usable"], data["ok"]) == (0, 1, True)


def test_health_unknown_platform_contract_unchanged(db):
    data = health(db, "no-such-platform")
    assert data["ok"] is False and data["error"] == "未知平台"


# ---------------------------------------------------------------------------
# C. 同一 storage 的 cn / intl 独立统计
# ---------------------------------------------------------------------------
def test_health_realm_split_does_not_cancel_other_realm(db):
    """第一区（cn）的冷却号不得抵消第二区（intl）的健康号。"""
    add_account(db, "quad", domain="cn", cool_minutes=10)   # 国内：唯一号在冷却
    add_account(db, "quad", domain="intl")                  # 国际：健康 active

    cn = health(db, "quad-cn")
    intl = health(db, "quad-intl")

    assert (cn["accounts"], cn["active"], cn["cooling"], cn["usable"], cn["ok"]) == \
        (1, 1, 1, 0, False), "国内区本应不可用：%r" % (cn,)
    assert (intl["accounts"], intl["active"], intl["cooling"],
            intl["usable"], intl["ok"]) == (1, 1, 0, 1, True), \
        "国内区的冷却号抵消了国际区的健康号：%r" % (intl,)


def test_health_realm_sample_counts_are_independent(db):
    """每个区的 active / cooling 只统计本区（同 storage 也不串）。"""
    add_account(db, "quad", domain="cn")                    # 国内：健康
    add_account(db, "quad", domain="cn", status="disabled", cool_minutes=10)
    add_account(db, "quad", domain="intl", cool_minutes=10)  # 国际：冷却
    add_account(db, "quad", domain="intl")                   # 国际：健康

    cn = health(db, "quad-cn")
    intl = health(db, "quad-intl")

    assert (cn["accounts"], cn["active"], cn["cooling"], cn["usable"]) == (2, 1, 1, 1)
    assert (intl["accounts"], intl["active"], intl["cooling"], intl["usable"]) == (2, 2, 1, 1)


# ---------------------------------------------------------------------------
# D. order() 的空列表语义 / 池边界
# ---------------------------------------------------------------------------
def test_order_none_means_every_registered_platform(db):
    add_account(db, "hb-solo")
    add_account(db, "pa")
    add_account(db, "pb")

    ordered = [item["platform"] for item in
               gateway_router.order(db, include_unavailable=True)]

    assert set(ordered) == set(registry.ids()), \
        "order(None) 没有覆盖全部平台：%r" % (ordered,)
    assert len(ordered) == len(registry.ids())


def test_order_empty_list_is_empty(db):
    add_account(db, "hb-solo")
    add_account(db, "pa")

    assert gateway_router.order(db, []) == [], "空候选被扩成了全平台排序"
    assert gateway_router.order(db, [], include_unavailable=True) == []
    assert gateway_router.pick(db, []) is None


def test_order_only_sorts_within_given_pool(db):
    add_account(db, "pa")
    add_account(db, "pb")

    ordered = [item["platform"] for item in gateway_router.order(db, ["pa", "pb"])]

    assert len(ordered) == 2
    assert set(ordered) == {"pa", "pb"}, "排序把池外的平台加回来了：%r" % (ordered,)


def test_order_pool_boundary_holds_for_every_strategy(db):
    add_account(db, "pa")
    add_account(db, "pb")
    try:
        for strategy in gateway_router.STRATEGY_IDS:
            gateway_router.save_routing(db, strategy=strategy)
            ordered = [item["platform"] for item in
                       gateway_router.order(db, ["pa", "pb"])]
            assert len(ordered) == 2, "%s 把池子扩了：%r" % (strategy, ordered)
            assert set(ordered) == {"pa", "pb"}, "%s 混入池外平台：%r" % (strategy, ordered)
    finally:
        gateway_router.save_routing(db, strategy=gateway_router.DEFAULT_STRATEGY)


def test_order_skips_unavailable_inside_pool_by_default(db):
    add_account(db, "pa")
    # pz 没有账号 -> 不可用

    assert [i["platform"] for i in gateway_router.order(db, ["pa", "pz"])] == ["pa"]

    kept = gateway_router.order(db, ["pa", "pz"], include_unavailable=True)
    assert {i["platform"] for i in kept} == {"pa", "pz"}
    assert len(kept) == 2
    pz = [i for i in kept if i["platform"] == "pz"][0]
    assert pz["available"] is False and pz["reason"] == "无可用账号"


def test_order_never_adds_platforms_when_pool_items_unavailable(db):
    """池里只有不可用平台时，仍然不得补进池外的可用平台。"""
    add_account(db, "pa")
    # 池内只有 pz（无账号），池外 pa 有账号
    assert gateway_router.order(db, ["pz"]) == []
    kept = gateway_router.order(db, ["pz"], include_unavailable=True)
    assert [i["platform"] for i in kept] == ["pz"]
    assert kept[0]["available"] is False


def test_order_dedupes_repeated_platform_ids(db):
    add_account(db, "pa")
    assert [i["platform"] for i in gateway_router.order(db, ["pa", "pa"])] == ["pa"]


# ---------------------------------------------------------------------------
# F. throughput 排序键：usable 缺字段不得打挂 order()（2026-11-22 加固）
# ---------------------------------------------------------------------------
def _fake_health(usable_map):
    """health() 替身：usable 可以是 None（模拟统计缺字段 / 平台实现没给）。"""

    def _health(db, platform):
        return {"platform": platform, "label": platform, "ok": True,
                "accounts": 1, "active": 1, "cooling": 0,
                "usable": usable_map.get(platform, 0),
                "balance_remain": 0, "balance_total": 0, "balance_ratio": None,
                "soonest_expire_at": None, "disabled": False}

    return _health


def _throughput_order(db, usable_map, pool=("pa", "pb", "pc")):
    gateway_router.save_routing(db, strategy="throughput")
    try:
        with mock.patch.object(gateway_router, "health",
                               _fake_health(usable_map)):
            return gateway_router.order(db, list(pool))
    finally:
        gateway_router.save_routing(db, strategy=gateway_router.DEFAULT_STRATEGY)


def test_throughput_tolerates_missing_usable_and_sorts_it_last(db):
    """usable=None 时 order() 必须正常返回（不再抛 TypeError），且排在最后。

    修复前实测：TypeError: bad operand type for unary -: 'NoneType'
    （排序键 (-usable or 0) 让 None 进了一元负号）。
    """
    ordered = _throughput_order(db, {"pa": None, "pb": 2, "pc": 1})

    assert [i["platform"] for i in ordered] == ["pb", "pc", "pa"], \
        "usable 缺失的平台应排在「有可用账号」的平台之后：%r" % (ordered,)
    assert ordered[-1]["reason"] == "0 个可用账号"


def test_throughput_int_usable_order_unchanged(db):
    """usable 是 int 时排序结果与修复前逐项相同（可用账号多的先）。"""
    ordered = _throughput_order(db, {"pa": 3, "pb": 1, "pc": 2})

    assert [i["platform"] for i in ordered] == ["pa", "pc", "pb"]
    assert [i["reason"] for i in ordered] == [
        "3 个可用账号", "2 个可用账号", "1 个可用账号"]


def test_throughput_mixed_none_and_zero_usable(db):
    """None 与 0 都排在最后，且不互相破坏（0 是「真的没有号」）。"""
    ordered = _throughput_order(db, {"pa": None, "pb": 0, "pc": 5})

    assert [i["platform"] for i in ordered][0] == "pc"
    assert {i["platform"] for i in ordered[1:]} == {"pa", "pb"}


# ---------------------------------------------------------------------------
# G. 账号级选号：跨 Key 冷却隔离 / 账号×模型冷却 / 三类无号原因
# ---------------------------------------------------------------------------
STORAGE = "sched-pool"
COOL_MODEL = "model-a"
OTHER_MODEL = "model-b"


def _api_key(db, name, *, account_id=None, account_ids=""):
    from admin.models import ApiKey

    row = ApiKey(name=name, key_hash="hash-" + name, status="active",
                 platform="", account_id=account_id, account_ids=account_ids)
    db.add(row)
    db.commit()
    return row


def _pool_account(db, name, *, balance=500):
    row = Account(platform=STORAGE, name=name, uid="uid-" + name, domain="",
                  auth_json="{}", status="active",
                  balance_total=1000, balance_remain=balance)
    db.add(row)
    db.commit()
    return row


def _set_balance_null(db, account):
    """把余额写成**真 NULL**（= 未知）。

    直接 Account(balance_remain=None) 不行：该列有 Column(default=0)，
    SQLAlchemy 在 INSERT 时会把 None 替换成默认值 0 —— 于是「额度未知」
    会被写成「余额为零」，两者的选号判据完全不同（见下面三条断言）。
    历史数据里的 NULL 来自迁移/直连写入，所以这里也用 UPDATE 造。
    """
    from sqlalchemy import text

    db.execute(text("UPDATE accounts SET balance_remain = NULL WHERE id = :aid"),
               {"aid": account.id})
    db.commit()
    db.expire_all()


def _cool(db, account, model, *, kind="model_rate", minutes=30):
    """插一条账号×模型冷却（minutes<0 = 已过期）。"""
    from admin.models import AccountModelCooldown

    row = AccountModelCooldown(
        account_id=account.id, model=model,
        until=datetime.utcnow() + timedelta(minutes=minutes),
        kind=kind, reason="test")
    db.add(row)
    db.commit()
    return row


def _select(db, key, model, **kwargs):
    from admin.platform import shared

    return shared.select_account(db, storage=STORAGE, key=key, model=model,
                                 **kwargs)


def test_account_model_cooldown_does_not_leak_across_keys(db):
    """两个 Key 各只可用一个号；A 的号对该模型冷却，B 仍能走自己的号。"""
    a1 = _pool_account(db, "a1", balance=500)
    b1 = _pool_account(db, "b1", balance=500)
    key_a = _api_key(db, "key-a", account_id=a1.id)
    key_b = _api_key(db, "key-b", account_id=b1.id)
    _cool(db, a1, COOL_MODEL)

    assert _select(db, key_a, COOL_MODEL) is None, \
        "唯一号对该模型冷却时，这个 Key 应该拿不到号（而不是借用别人的）"
    picked = _select(db, key_b, COOL_MODEL)
    assert picked is not None and picked.id == b1.id, \
        "冷却串号了：另一个 Key 必须仍能走自己的账号"
    # 冷却只作用于 (账号, 模型)：换模型立刻可选
    assert _select(db, key_a, OTHER_MODEL).id == a1.id


def test_same_key_other_account_not_dragged_by_model_cooldown(db):
    """同一 Key 的多选账号里，一个号对该模型冷却，另一个号不受拖累。"""
    a2 = _pool_account(db, "a2", balance=500)
    a3 = _pool_account(db, "a3", balance=500)
    key_c = _api_key(db, "key-c", account_ids=json.dumps([a2.id, a3.id]))
    _cool(db, a2, COOL_MODEL)

    for _ in range(2):          # 重复调用必须稳定落到同一个未冷却号
        picked = _select(db, key_c, COOL_MODEL)
        assert picked is not None and picked.id == a3.id
    other = _select(db, key_c, OTHER_MODEL)
    assert other is not None and other.balance_remain == 500


def test_account_model_cooldown_is_per_model_and_expires(db):
    """（账号×模型）粒度：换模型不受影响；过期记录不再冷却。"""
    x = _pool_account(db, "x", balance=500)
    _cool(db, x, COOL_MODEL, minutes=30)
    _cool(db, x, "model-expired", minutes=-30)

    assert _select(db, None, COOL_MODEL) is None
    assert _select(db, None, "model-expired").id == x.id, \
        "已过期的 (账号, 模型) 冷却不该继续挡号"
    assert _select(db, None, OTHER_MODEL).id == x.id
    # model 为空 = 不做模型级过滤（现有语义，如实钉住）
    assert _select(db, None, "").id == x.id


def test_unknown_balance_is_selectable_but_ranked_last(db):
    """额度未知（NULL）不拦号，但排序上让位给「已知有余额」的号。"""
    unknown = _pool_account(db, "unknown")
    _set_balance_null(db, unknown)
    rich = _pool_account(db, "rich")

    assert _select(db, None, OTHER_MODEL, min_balance=0).id == rich.id
    picked = _select(db, None, OTHER_MODEL, min_balance=0,
                     exclude_ids=[rich.id])
    assert picked is not None and picked.id == unknown.id, \
        "余额未知（NULL）不该被 min_balance 误杀"
    assert picked.balance_remain is None


def test_zero_balance_is_filtered_by_min_balance(db):
    """余额真实为零：只被余额下限挡住，不是「没号」也不是「账号冷却」。"""
    zero = _pool_account(db, "zero", balance=0)

    assert _select(db, None, OTHER_MODEL, min_balance=0) is None
    picked = _select(db, None, OTHER_MODEL)
    assert picked is not None and picked.id == zero.id
    assert picked.balance_remain == 0 and picked.cool_until is None


def test_three_insufficient_causes_have_distinct_data(db):
    """额度未知 / 余额为零 / 模型未开通 = 三份不同的数据，选择层判据也不同。

    HTTP 错误码映射在各平台网关（marvis 的 accounts_cooling / insufficient_quota
    / model_not_found），不在本轮范围；这里只钉住「数据与判据不同」。
    """
    from admin.platform import shared

    unknown = _pool_account(db, "unknown-balance")
    _set_balance_null(db, unknown)
    zero = _pool_account(db, "zero-balance", balance=0)
    blocked = _pool_account(db, "model-blocked")
    _cool(db, blocked, COOL_MODEL, kind="model_block")

    # 1) 额度未知：放行；数据特征是 balance_remain IS NULL
    picked = shared.select_account(db, storage=STORAGE, key=None,
                                   model=OTHER_MODEL, min_balance=0,
                                   exclude_ids=[zero.id, blocked.id])
    assert picked is not None and picked.id == unknown.id
    assert picked.balance_remain is None

    # 2) 余额为零：被 min_balance 挡住；数据特征是 balance_remain == 0
    assert shared.select_account(db, storage=STORAGE, key=None,
                                 model=OTHER_MODEL, min_balance=0,
                                 exclude_ids=[unknown.id, blocked.id]) is None
    picked_zero = shared.select_account(db, storage=STORAGE, key=None,
                                        model=OTHER_MODEL,
                                        exclude_ids=[unknown.id, blocked.id])
    assert picked_zero is not None and picked_zero.id == zero.id
    assert picked_zero.balance_remain == 0

    # 3) 模型未开通：余额与账号冷却都正常，只有 (账号, 模型) 那条 model_block
    assert shared.select_account(db, storage=STORAGE, key=None,
                                 model=COOL_MODEL,
                                 exclude_ids=[unknown.id, zero.id]) is None
    ok = shared.select_account(db, storage=STORAGE, key=None,
                               model=OTHER_MODEL,
                               exclude_ids=[unknown.id, zero.id])
    assert ok is not None and ok.id == blocked.id
    assert ok.balance_remain == 500 and ok.cool_until is None

# ---------------------------------------------------------------------------
# E. 热路径确定性计数（基线未达标，xfail 记录期望值）
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def bench_report():
    """跑一次度量脚本（子进程，脚本自己用临时 SQLite + 假上游）。"""
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env.pop("ADMIN_DATABASE_URL", None)      # 脚本内部会覆盖成自己的临时库
    proc = subprocess.run(
        [sys.executable, "-B", str(BENCH), "--json", "--warm", "5"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
        env=env, timeout=600)
    assert proc.returncode == 0, "bench 脚本失败：%s" % (proc.stderr or "")[-3000:]
    return json.loads(proc.stdout)


def test_bench_report_covers_four_paths_and_metrics(bench_report):
    """度量脚本的契约：四条路径、四个指标（模型表两个指标分开）、落点正确。"""
    assert bench_report["meta"]["models"] == 128
    assert bench_report["meta"]["platforms"] == [
        "workbuddy", "alpha", "beta-cn", "beta-intl"]

    paths = bench_report["paths"]
    assert set(paths) == {"default_direct", "suffix_fixed",
                          "auto_aggregate", "retry_after_503"}
    for name, entry in paths.items():
        warm = entry["warm"]
        for key in ("sql_per_request", "cfg_statements_per_request",
                    "full_scans_per_request", "rank_calls_per_request",
                    "p50_ms", "p95_ms"):
            assert key in warm, "%s 缺少指标 %s" % (name, key)
        # 两个模型表指标必须是**分开**的字段（不许混成一个数字）
        assert warm["cfg_statements_per_request"] >= warm["full_scans_per_request"]
        assert warm["p50_ms"] > 0
        assert warm["statuses"] == [200], "%s 预热后状态异常：%r" % (name, warm["statuses"])

    assert paths["default_direct"]["warm"]["trace_sample"] == ["main"]
    assert paths["suffix_fixed"]["warm"]["trace_sample"] == ["alpha"]
    assert paths["retry_after_503"]["warm"]["trace_sample"] == ["alpha", "beta-cn"], \
        "503 之后没有换到下一个候选平台"


#: 预热后每条路径允许的「涉及 model_configs 的 SQL 条数」上界（实测值 +1 的余量）。
#: 目的：真·全表读取已经由 full_scans_per_request == 0 单独守住，这里再挡住
#: 「悄悄多加几条模型表查询」—— 2026-11-23 前的旧判定器把其中带 WHERE 的
#: 那些全算成「全表读取」，数字被污染，没法用来看这件事。
CFG_STATEMENT_BOUNDS = {
    "default_direct": 1,
    "suffix_fixed": 2,
    "auto_aggregate": 1,
    "retry_after_503": 1,
}


def test_warm_inference_request_scans_model_configs_zero_times(bench_report):
    """确定性期望值（计划 §七 第 91 行）：预热后四条路径的真·全表读取都为 0。

    口径说明（2026-11-23 修）：只看「涉及 model_configs 且**没有任何 WHERE**」
    的语句。旧判定器用 " where " 子串，而 SQLAlchemy 渲染的是
    "\nFROM model_configs \nWHERE ..."，于是带 WHERE 的语句也被算进来 ——
    历史上引用的「cfg 全表 3.00/1.00/2.00/2.00」因此是**被污染**的数字，
    不能当作「全表读取从 3 降到 0」的证据。现在两个指标分开：
    cfg_statements_per_request（条数）与 full_scans_per_request（真全表）。
    """
    paths = bench_report["paths"]
    offenders = {name: entry["warm"]["full_scans_total"]
                 for name, entry in paths.items()
                 if entry["warm"]["full_scans_total"] != 0}
    assert offenders == {}, \
        "预热后仍有 model_configs 真·全表读取（期望 0）：%r" % (offenders,)

    for name, entry in paths.items():
        statements = entry["warm"]["cfg_statements_per_request"]
        assert statements <= CFG_STATEMENT_BOUNDS[name], \
            "%s 的 model_configs 语句数涨到 %.2f/请求（上界 %d）：%r" % (
                name, statements, CFG_STATEMENT_BOUNDS[name],
                entry["warm"])

# ---------------------------------------------------------------------------
# H. 度量口径的判定器本身：纯函数单测（2026-11-23 修「全表读取」误报）
# ---------------------------------------------------------------------------
def _bench_module():
    """把度量脚本当模块导入。

    脚本已保证 import 无副作用（不建临时库、不改环境变量、不导入 admin.*），
    所以单测可以直接吃它的纯函数，不必再复制一份判定逻辑到测试里 ——
    复制的那份会和真实现漂移，等于没守住。
    """
    import importlib.util

    cached = sys.modules.get("bench_dispatch_hotpath")
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location("bench_dispatch_hotpath", BENCH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.modules["bench_dispatch_hotpath"] = module
    return module


#: 真实 SQLAlchemy 渲染形态（原文抄自 before_cursor_execute，含实际换行）
NO_WHERE_SQL = (
    "SELECT model_configs.platform AS model_configs_platform, "
    "model_configs.model_id AS model_configs_model_id \nFROM model_configs")
MULTILINE_WHERE_SQL = (
    "SELECT model_configs.model_id AS model_configs_model_id, "
    "model_configs.upstream_metadata AS model_configs_upstream_metadata \n"
    "FROM model_configs \n"
    "WHERE model_configs.platform = ? AND model_configs.enabled = ?")


def test_legacy_space_substring_detector_was_a_false_positive_source():
    """旧口径 " where " not in sql.lower() 会把多行 WHERE 语句判成全表读取。

    这条用例把「修复前为什么错」钉住：同一个字符串，旧判定 True、新判定 False。
    """
    bench = _bench_module()

    legacy_verdict = " where " not in MULTILINE_WHERE_SQL.lower()
    assert legacy_verdict is True, "旧口径应当误报（这条断言就是在描述那个 bug）"
    assert bench._is_full_table_scan(MULTILINE_WHERE_SQL) is False

    # 真·全表读取两版意见一致（这一侧本来就没坏）
    assert (" where " not in NO_WHERE_SQL.lower()) is True
    assert bench._is_full_table_scan(NO_WHERE_SQL) is True


@pytest.mark.parametrize("sql, expected", [
    # 无 WHERE：真·全表读取
    (NO_WHERE_SQL, True),
    ("SELECT model_configs.id \nFROM model_configs\n", True),
    # 多行 WHERE（旧口径的误报源）
    (MULTILINE_WHERE_SQL, False),
    ("select model_configs.id\nfrom model_configs\nwhere enabled = ?", False),
    # 大小写混合 / 单行
    ("SELECT * FROM model_configs WhErE model_configs.id = ?", False),
    # WHERE 只出现在子查询里
    ("SELECT model_configs.id \nFROM model_configs \nWHERE model_configs.id IN "
     "(SELECT account_id FROM account_model_cool WHERE until > ?)", False),
    # 不含 model_configs 的语句一律不算
    ("SELECT accounts.id \nFROM accounts", False),
    ("", False),
])
def test_is_full_table_scan_verdicts(sql, expected):
    bench = _bench_module()
    assert bench._is_full_table_scan(sql) is expected, \
        "判定器对这段 SQL 的结论不对：%r" % (sql,)


def test_sql_stats_keeps_statement_count_separate_from_full_scans():
    """两个模型表指标必须分开：条数 3、真全表 1（不是把 3 都算成全表）。"""
    bench = _bench_module()
    stats = bench._sql_stats([
        NO_WHERE_SQL,
        MULTILINE_WHERE_SQL,
        "select model_configs.id\nfrom model_configs\nwhere enabled = ?",
        "SELECT accounts.id \nFROM accounts",
    ])

    assert stats == {"sql": 4, "cfg_statements": 3, "full_scans": 1}
