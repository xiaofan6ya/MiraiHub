# PERSISTENT REGRESSION TESTS — keep this file in the repo.
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""聚合视图 /v1/models 缓存的 view/group 独立 TTL 与 metadata 失效回归（合成）。

## 两个线上症状

1. 刷新 A 延长了 B 的旧缓存
   models_router._cache 原来是「一个全局 at/signature + 一个 payload 字典」。
   _store(slot, ...) 每次都把全局 at 改成 now —— 于是只要有人请求了一次
   「聚合视图」，另一个 10 秒没刷新的「全量视图 / 其它分组」也会被判成新鲜，
   继续拿到旧清单（分组刚改完却还是老内容）。

2. 改 note / caps / upstream_metadata 不刷新
   指纹只算 count(id) / max(id) / sum(enabled) —— 行内容变了但行数、最大 id、
   启用数都没变，指纹相同 -> 缓存命中 -> 客户端看到旧展示名、旧上下文长度，
   要等 10 秒 TTL 才「自己好」。PATCH /{config_id} 改的正是这些字段。

## 本文件验证

* 同一 view 改 upstream_metadata / note / _override 能力后立刻重建；
* 两个 view（agg / full）与两个分组各自的 TTL 相互独立：B 过期后刷新 A，
  B 仍然必须重建而不是复用旧对象；
* invalidate() 清空所有 slot；
* admin.routers.models._invalidate_routing_cache() 会同时失效这份缓存；
* 分组换了成员（哪怕 sqlite 复用 member id、分组名/备注不变）时，**另一个
  session/worker** 也必须立刻看到新清单；成员查不到时受限清单 fail closed。

全部用临时 sqlite + 假时钟，不碰网络、不碰真实库。

运行：
  python tests/test_gateway_reliability_models.py
  pytest tests/test_gateway_reliability_models.py -q
"""
from __future__ import annotations

import atexit
import contextlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(ROOT))

#: 本轮临时目录（%TEMP% 下独立文件），结束时整个删掉。
_TMP_ROOT = Path(tempfile.mkdtemp(prefix="miraihub-models-cache-"))
#: pytest 模式不走 main()，所以再挂一个 atexit 保证临时库一定被删掉。
atexit.register(shutil.rmtree, _TMP_ROOT, True)
#: 必须在 import admin.db 之前设置：它按这个 URL 建引擎。
os.environ["ADMIN_DATABASE_URL"] = "sqlite:///" + (_TMP_ROOT / "admin-unused.db").as_posix()

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from admin.db import Base  # noqa: E402
from admin.models import ModelConfig  # noqa: E402
from admin.platform import models_router as mr  # noqa: E402

PASS = 0
FAIL = 0


def _new_db(name: str):
    """独立的临时 sqlite 文件（不复用 admin.db 的引擎）。"""
    path = _TMP_ROOT / ("%s.db" % name)
    engine = create_engine("sqlite:///" + path.as_posix(), future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return Session(), engine


@contextlib.contextmanager
def _sessions(name: str):
    """同一个临时库上的**多个独立 session**（模拟不同 worker 各拿一个请求会话）。"""
    engine = create_engine("sqlite:///" + (_TMP_ROOT / ("%s.db" % name)).as_posix(),
                           future=True)
    Base.metadata.create_all(engine)
    try:
        yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    finally:
        engine.dispose()


class _Clock:
    """假时钟：替代 models_router 模块里的 time 模块。"""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = float(now)

    def time(self) -> float:
        return self.now


@contextlib.contextmanager
def _frozen(clock: _Clock):
    saved = mr.time
    mr.time = clock
    try:
        yield clock
    finally:
        mr.time = saved


def _model_row(**kwargs) -> ModelConfig:
    data = {
        "platform": "workbuddy",
        "model_id": "deepseek-v4.1-flash",
        "enabled": 1,
        "note": "",
        "upstream_metadata": json.dumps({"name": "DeepSeek V4.1 Flash"},
                                        ensure_ascii=False),
    }
    data.update(kwargs)
    return ModelConfig(**data)


def _only_entry(payload: dict) -> dict:
    data = payload.get("data") or []
    assert len(data) == 1, "期望单条条目，实际 %d 条：%r" % (len(data), data)
    return data[0]


def _ids(payload: dict) -> list:
    return [item.get("id") for item in (payload.get("data") or [])]


def _cache_entry(slot: str):
    """当前 slot 的缓存条目（接线后是 dict）。"""
    entry = mr._cache.get(slot)
    assert isinstance(entry, dict), "slot %r 没写进独立缓存条目：%r" % (slot, entry)
    return entry


# ---------------------------------------------------------------------------
# 1. 同一 view：metadata / note / 能力覆盖改动立刻可见
# ---------------------------------------------------------------------------

def test_metadata_change_rebuilds_same_view() -> None:
    mr.invalidate()
    db, engine = _new_db("metadata")
    try:
        clock = _Clock(1000.0)
        with _frozen(clock):
            row = _model_row()
            db.add(row)
            db.commit()

            first = mr.build_payload(db, aggregate_only=True)
            assert _only_entry(first)["display_name"] == "DeepSeek V4.1 Flash"
            sig_before = mr._signature(db)

            row.upstream_metadata = json.dumps({"name": "DeepSeek V4.2 Flash"},
                                               ensure_ascii=False)
            db.commit()

            assert mr._signature(db) != sig_before, (
                "指纹必须反映 upstream_metadata 变化（updated_at 聚合）：%r" % sig_before)

            second = mr.build_payload(db, aggregate_only=True)
            assert second is not first, "metadata 变了却复用了旧对象"
            assert _only_entry(second)["display_name"] == "DeepSeek V4.2 Flash"
    finally:
        db.close()
        engine.dispose()


def test_note_change_rebuilds_same_view() -> None:
    mr.invalidate()
    db, engine = _new_db("note")
    try:
        clock = _Clock(1000.0)
        with _frozen(clock):
            row = _model_row(note="旧备注")
            db.add(row)
            db.commit()

            first = mr.build_payload(db, aggregate_only=False)
            sig_before = mr._signature(db)

            row.note = "新备注"
            db.commit()

            assert mr._signature(db) != sig_before, "指纹必须反映 note 变化"
            second = mr.build_payload(db, aggregate_only=False)
            assert second is not first, "note 变了却复用了旧缓存"
    finally:
        db.close()
        engine.dispose()


def test_capability_override_rebuilds_same_view() -> None:
    mr.invalidate()
    db, engine = _new_db("caps")
    try:
        clock = _Clock(1000.0)
        with _frozen(clock):
            row = _model_row()
            db.add(row)
            db.commit()

            first = mr.build_payload(db, aggregate_only=False)
            assert _only_entry(first)["contextWindow"] != 64000

            row.upstream_metadata = json.dumps(
                {"name": "DeepSeek V4.1 Flash",
                 "_override": {"context_window": 64000, "supports_images": False}},
                ensure_ascii=False)
            db.commit()

            second = mr.build_payload(db, aggregate_only=False)
            entry = _only_entry(second)
            assert entry["contextWindow"] == 64000, entry
            assert entry["caps"]["context_window"] == 64000, entry
            assert entry["caps"].get("overridden") is True, entry
    finally:
        db.close()
        engine.dispose()


# ---------------------------------------------------------------------------
# 2. 两个 view 的 TTL 相互独立
# ---------------------------------------------------------------------------

def test_ttl_independent_between_views() -> None:
    mr.invalidate()
    db, engine = _new_db("ttl")
    try:
        clock = _Clock(1000.0)
        with _frozen(clock):
            db.add(_model_row())
            db.commit()

            agg_first = mr.build_payload(db, aggregate_only=True)
            full_first = mr.build_payload(db, aggregate_only=False)
            assert _cache_entry("agg")["at"] == 1000.0
            assert _cache_entry("full")["at"] == 1000.0

            # 5 秒后命中缓存：命中不该刷新 at（滑窗只会让旧清单活得更久）
            clock.now = 1005.0
            assert mr.build_payload(db, aggregate_only=True) is agg_first, (
                "10 秒内 agg 不该重建")
            assert _cache_entry("agg")["at"] == 1000.0

            # 12 秒：两个 view 都过期了。先重建 agg ——
            # 旧实现会顺手把全局 at 刷成 1012，让 full 看起来「新鲜」。
            clock.now = 1012.0
            agg_second = mr.build_payload(db, aggregate_only=True)
            assert agg_second is not agg_first, "agg 过期后必须重建"
            assert _cache_entry("agg")["at"] == 1012.0

            # 紧接着请求 full：它自己已经 12 秒没刷新，必须重建，
            # 不能因为刚刷过 agg 就复用旧 payload。
            full_second = mr.build_payload(db, aggregate_only=False)
            assert full_second is not full_first, (
                "刷新 agg 不能延长 full 的 TTL：full 必须自己重建")
            assert _cache_entry("full")["at"] == 1012.0, mr._cache.get("full")

            # 反向也不行：full 的重建不该把 agg 的 at 再往后推
            clock.now = 1016.0
            assert mr.build_payload(db, aggregate_only=True) is agg_second
            assert _cache_entry("agg")["at"] == 1012.0, mr._cache.get("agg")
    finally:
        db.close()
        engine.dispose()


def test_ttl_independent_between_groups() -> None:
    mr.invalidate()
    db, engine = _new_db("groups")
    try:
        clock = _Clock(2000.0)
        with _frozen(clock):
            db.add(_model_row())
            db.commit()

            g1_first = mr.build_payload(db, aggregate_only=True, group_id=1)
            g2_first = mr.build_payload(db, aggregate_only=True, group_id=2)
            assert _cache_entry("agg:g1")["at"] == 2000.0
            assert _cache_entry("agg:g2")["at"] == 2000.0

            clock.now = 2005.0
            assert mr.build_payload(db, aggregate_only=True, group_id=1) is g1_first
            assert _cache_entry("agg:g1")["at"] == 2000.0

            # 12 秒：两个分组都过期。先重建 g1，再要求 g2 自己重建。
            clock.now = 2012.0
            g1_second = mr.build_payload(db, aggregate_only=True, group_id=1)
            assert g1_second is not g1_first, "g1 过期后必须重建"
            g2_second = mr.build_payload(db, aggregate_only=True, group_id=2)
            assert g2_second is not g2_first, "刷新 g1 不能延长 g2 的 TTL"
            assert _cache_entry("agg:g2")["at"] == 2012.0
            assert _cache_entry("agg:g1")["at"] == 2012.0
    finally:
        db.close()
        engine.dispose()


# ---------------------------------------------------------------------------
# 3. invalidate() 清空所有 view
# ---------------------------------------------------------------------------

def test_invalidate_clears_every_slot() -> None:
    mr.invalidate()
    db, engine = _new_db("invalidate")
    try:
        clock = _Clock(3000.0)
        with _frozen(clock):
            db.add(_model_row())
            db.commit()

            agg_first = mr.build_payload(db, aggregate_only=True)
            full_first = mr.build_payload(db, aggregate_only=False)
            group_first = mr.build_payload(db, aggregate_only=True, group_id=7)
            assert set(mr._cache) >= {"agg", "full", "agg:g7"}, set(mr._cache)

            mr.invalidate()
            assert mr._cache == {}, "invalidate() 必须清空所有 slot：%r" % (mr._cache,)

            clock.now = 3001.0
            assert mr.build_payload(db, aggregate_only=True) is not agg_first
            assert mr.build_payload(db, aggregate_only=False) is not full_first
            assert mr.build_payload(db, aggregate_only=True, group_id=7) is not group_first
    finally:
        db.close()
        engine.dispose()


# ---------------------------------------------------------------------------
# 4. 管理端 PATCH 走的 helper 要一起失效这份缓存
# ---------------------------------------------------------------------------

def test_routing_cache_helper_invalidates_models_cache() -> None:
    mr.invalidate()
    db, engine = _new_db("router")
    try:
        clock = _Clock(4000.0)
        with _frozen(clock):
            db.add(_model_row())
            db.commit()
            mr.build_payload(db, aggregate_only=True)
            assert mr._cache, "前置条件：缓存里应有内容"

            from admin.routers.models import _invalidate_routing_cache

            _invalidate_routing_cache()
            assert mr._cache == {}, (
                "PATCH /{config_id} 调的 helper 没让 /v1/models 缓存失效：%r"
                % (mr._cache,))
    finally:
        db.close()
        engine.dispose()


# ---------------------------------------------------------------------------
# 5. 分组换成员：另一个 worker 必须立刻看到（sqlite 复用 member id 也算变化）
# ---------------------------------------------------------------------------

def test_group_update_visible_to_other_worker_session() -> None:
    """读端先预热 ["sample-a"]，写端在**另一个 session** 里换成 ["sample-b"]。

    分组成员是 delete+insert，sqlite 会复用被删行的 rowid；分组名与备注不变时
    count / max(id) / updated_at 也都不变 —— 指纹看不出来。旧实现因此继续返回
    旧清单（sample-a），必须靠「读真实成员并参与缓存校验」修掉。
    """
    from admin.routers.groups import GroupIn, create_group, update_group

    mr.invalidate()
    with _sessions("group-other-worker") as S:
        clock = _Clock(5000.0)
        with _frozen(clock):
            writer = S()
            for mid in ("sample-a", "sample-b"):
                writer.add(_model_row(model_id=mid,
                                      upstream_metadata=json.dumps({"name": mid})))
            writer.commit()
            gid = create_group(GroupIn(name="demo", models=["sample-a"]),
                               True, writer)["id"]

            reader = S()
            warm = mr.build_payload(reader, group_id=gid)
            reader.close()
            assert _ids(warm) == ["sample-a"], warm

            update_group(gid, GroupIn(name="demo", note="", models=["sample-b"]),
                         True, writer)

            # 换一个全新的读会话：等价于「没处理这次更新的另一个 worker」
            reader2 = S()
            fresh = mr.build_payload(reader2, group_id=gid)
            reader2.close()
            assert fresh is not warm, "成员变了却复用同一个 payload 对象"
            assert _ids(fresh) == ["sample-b"], (
                "分组成员换了却仍返回旧清单：%r" % (_ids(fresh),))
            writer.close()


def test_group_membership_lookup_failure_fails_closed() -> None:
    """成员读不到时，受限清单必须 fail closed，且不能把全量结果写进缓存。"""
    mr.invalidate()
    with _sessions("group-failclosed") as S:
        db = S()
        clock = _Clock(6000.0)
        with _frozen(clock):
            db.add(_model_row(model_id="sample-a",
                              upstream_metadata=json.dumps({"name": "sample-a"})))
            db.commit()
            # 先建一份未受限的全量缓存，证明 fail closed 不会把它喂给分组
            assert _ids(mr.build_payload(db)) == ["sample-a"]

            from admin.routers import groups as groups_router

            def boom(*args, **kwargs):
                raise RuntimeError("membership unavailable")

            with patch.object(groups_router, "group_models", boom):
                limited = mr.build_payload(db, group_id=42)
                assert limited["data"] == [], (
                    "成员表读失败时必须返回空清单，不能退化成全量：%r" % (limited,))
                assert "full:g42" not in mr._cache, (
                    "fail closed 的结果不能写缓存：%r" % (mr._cache,))
                # 未分组路径不该查成员表，读失败也必须照常
                assert _ids(mr.build_payload(db)) == ["sample-a"]
        db.close()


def test_group_membership_ids_with_commas_do_not_collide() -> None:
    """模型 id 里带逗号时，缓存身份必须是结构而不是拼出来的字符串。

    {"a,b"} 与 {"a","b"} 用逗号拼接得到同一个串 —— 拼字符串的写法会让两次
    不同的分组内容互相命中缓存，把「一个叫 a,b 的模型」当成「a 和 b 两个」。
    """
    from admin.routers.groups import GroupIn, create_group, update_group

    mr.invalidate()
    with _sessions("group-comma-ids") as S:
        clock = _Clock(7000.0)
        with _frozen(clock):
            writer = S()
            for mid in ("a", "b", "a,b"):
                writer.add(_model_row(model_id=mid,
                                      upstream_metadata=json.dumps({"name": mid})))
            writer.commit()
            gid = create_group(GroupIn(name="comma", models=["a,b"]), True, writer)["id"]

            reader = S()
            one = mr.build_payload(reader, group_id=gid)
            reader.close()
            # 可读 id 会做 slug（"a,b" -> "a-b"），条目仍是那一个模型
            assert _ids(one) == ["a-b"], one
            assert one["data"][0]["base_model"] == "a,b", one["data"][0]

            # 换成两个模型：拼字符串会是同一个 "a,b"
            update_group(gid, GroupIn(name="comma", note="", models=["a", "b"]),
                         True, writer)

            reader2 = S()
            two = mr.build_payload(reader2, group_id=gid)
            reader2.close()
            assert _ids(two) == ["a", "b"], (
                "成员 id 含逗号时缓存身份撞车：%r" % (_ids(two),))
            writer.close()

# ---------------------------------------------------------------------------
# 6. 管理台聚合：每个模型组的 key 必须是稳定字符串（§六 第 79 行）
# ---------------------------------------------------------------------------

def _group_rows() -> list[dict]:
    return [
        {"model_id": "deepseek-v4-pro", "platform": "workbuddy",
         "display_name": "DeepSeek-V4-Pro", "credit_multiplier": 0.3,
         "enabled": True},
        {"model_id": "deepseek-v4.1-flash", "platform": "workbuddy",
         "display_name": "DeepSeek-V4.1-Flash", "credit_multiplier": 0.1,
         "enabled": True},
    ]


def test_group_models_key_is_string_per_group() -> None:
    """同一平台的两个不同模型，各自拿到自己的归一字符串 key。

    旧实现里内层 key = (platform, realm) 覆盖了外层分组变量，于是
    out.append({"key": key, ...}) 把元组序列化成 ["workbuddy","cn"] ——
    两个不同模型在管理台拿到同一个数组 key。
    """
    from admin.model_alias import group_models

    out = group_models(_group_rows())
    keys = {item["name"]: item["key"] for item in out}
    assert keys.get("DeepSeek-V4-Pro") == "deepseekv4pro", keys
    assert keys.get("DeepSeek-V4.1-Flash") == "deepseekv41flash", keys
    for item in out:
        assert isinstance(item["key"], str) and item["key"], (
            "组 key 必须是稳定字符串；元组会被序列化成数组：%r" % (item["key"],))
    dumped = json.loads(json.dumps(out, ensure_ascii=False))
    assert [item["key"] for item in dumped] == ["deepseekv4pro",
                                               "deepseekv41flash"], dumped


def test_group_models_keeps_realm_details() -> None:
    """修 key 覆盖时不能顺手把平台 + 区域明细压掉。"""
    from admin.model_alias import group_models

    rows = [
        {"model_id": "deepseek-v4.1-flash", "platform": "trae",
         "display_name": "DeepSeek-V4.1-Flash", "credit_multiplier": 0.1,
         "enabled": True},
        {"model_id": "deepseek-v4.1-flash:intl", "platform": "trae",
         "display_name": "DeepSeek-V4.1-Flash", "credit_multiplier": 0.2,
         "enabled": True},
        {"model_id": "deepseek-v4.1-flash", "platform": "workbuddy",
         "display_name": "DeepSeek-V4.1-Flash", "credit_multiplier": 0.5,
         "enabled": True},
    ]
    out = group_models(rows)
    assert len(out) == 1, out
    item = out[0]
    assert item["key"] == "deepseekv41flash", item["key"]
    # 国内 / 国际是两个区、两份额度、两套倍率 —— 必须分开保留
    assert item["platform_count"] == 3, item["platforms"]
    assert sorted(p["multiplier"] for p in item["platforms"]) == [0.1, 0.2, 0.5]
    assert item["min_multiplier"] == 0.1, item


# ---------------------------------------------------------------------------
# 7. 聚合公开 ID 稳定：平台增删不改名（§六 第 80 行）
# ---------------------------------------------------------------------------

def _platform_row(platform: str, model_id: str, metadata=None) -> ModelConfig:
    return ModelConfig(platform=platform, model_id=model_id, enabled=1,
                       upstream_metadata=json.dumps(metadata or {}))


def _add_same_model_everywhere(db) -> tuple:
    """WorkBuddy 原拼写 / Qoder 缩写 / Marvis 槽位 —— 同一个模型的三份登记。"""
    wb = _platform_row("workbuddy", "deepseek-v4.1-flash",
                       {"name": "DeepSeek V4.1 Flash"})
    qd = _platform_row("qoder", "dfmodel", {"name": "DeepSeek-Flash"})
    mv = _platform_row("marvis", "main-auto", {})
    db.add_all([wb, qd, mv])
    db.commit()
    return wb, qd, mv


def test_aggregate_public_id_stable_across_platform_changes() -> None:
    """依次启用 WorkBuddy → 加入 Qoder → 加入 Marvis → 移除其中一个。

    旧实现用 slugify(head.readable) 当聚合 ID，head 取决于行顺序与平台
    字母序：只有 WorkBuddy 时是 deepseek-v4.1-flash，加入 Qoder 的
    dfmodel（可读名 deepseek-flash）后就变成 deepseek-flash。
    """
    mr.invalidate()
    db, engine = _new_db("agg-stable")
    try:
        clock = _Clock(8100.0)
        with _frozen(clock):
            def agg_ids() -> list:
                mr.invalidate()
                clock.now += 1.0
                return _ids(mr.build_payload(db, aggregate_only=True))

            def add(platform: str, model_id: str, metadata=None) -> None:
                db.add(_platform_row(platform, model_id, metadata))
                db.commit()

            def drop(platform: str, model_id: str) -> None:
                db.query(ModelConfig).filter(
                    ModelConfig.platform == platform,
                    ModelConfig.model_id == model_id).delete()
                db.commit()

            # 1) 只有 WorkBuddy 的 V4.1 Flash
            add("workbuddy", "deepseek-v4.1-flash",
                {"name": "DeepSeek V4.1 Flash"})
            assert agg_ids() == ["deepseek-v4.1-flash"], "只有 WorkBuddy 时"

            # 2) 加入 Qoder 的 dfmodel（可读名 deepseek-flash）
            add("qoder", "dfmodel", {"name": "DeepSeek-Flash"})
            assert agg_ids() == ["deepseek-v4.1-flash"], (
                "加入 Qoder 的 dfmodel 后聚合 ID 变了（旧实现变成 deepseek-flash）")

            # 3) 加入 Marvis 的槽位名 main-auto
            add("marvis", "main-auto")
            assert agg_ids() == ["deepseek-v4.1-flash"], "加入 Marvis 后"

            # 4) 移除其中一个：这一组仍然发布同一个 ID
            drop("marvis", "main-auto")
            assert agg_ids() == ["deepseek-v4.1-flash"], "移除 Marvis 后"
            drop("qoder", "dfmodel")
            assert agg_ids() == ["deepseek-v4.1-flash"], "再移除 Qoder 后"

            # 5) 没有明确等价关系的组保留现有可调用 ID（不按相似名强行改名）
            add("loomy", "hy3")
            assert sorted(agg_ids()) == ["deepseek-v4.1-flash", "hy3"]
    finally:
        db.close()
        engine.dispose()


def test_historical_alias_and_platform_suffix_still_resolve() -> None:
    """历史公开拼写继续作为入站别名；精确后缀专名仍交给指定平台。"""
    from admin.platform import model_suffix

    mr.invalidate()
    db, engine = _new_db("agg-alias")
    try:
        clock = _Clock(8200.0)
        with _frozen(clock):
            _wb, qd, _mv = _add_same_model_everywhere(db)
            published = _ids(mr.build_payload(db, aggregate_only=True))
            assert published == ["deepseek-v4.1-flash"], published

            # 1) 聚合 ID 能回到组内每个平台的真实 model_id
            hits = mr.resolve_public_targets("deepseek-v4.1-flash", db)
            assert {h["model_id"] for h in hits} == {
                "deepseek-v4.1-flash", "dfmodel", "main-auto"}, hits
            assert {h["platform"] for h in hits} == {
                "workbuddy", "qoder", "qoder-intl", "marvis"}, hits

            # 2) 历史裸名仍是可调用别名，且不落到别家的拼法上
            for alias in ("deepseek-flash", "DeepSeek-Flash",
                          "deepseek-v41-flash"):
                assert mr.upstream_of(alias, db) in {
                    "dfmodel", "deepseek-v4.1-flash", "main-auto"}, alias
                assert mr.platform_of_model(alias, db) in {
                    "workbuddy", "qoder", "marvis"}, alias

            # 3) 精确的平台专名仍交给该平台
            assert model_suffix.resolve("deepseek-flash-qoder") == (
                "qoder", "deepseek-flash", True)
            assert model_suffix.alias_to_upstream("deepseek-flash-qoder") == (
                "qoder", "dfmodel")
            assert mr.upstream_of_in_platform(
                "deepseek-v4.1-flash", "qoder", db) == "dfmodel"

            # 4) Qoder 行被删掉后，历史裸名依然可调用（不能因为缺那一行就 404）
            db.delete(qd)
            db.commit()
            assert mr.upstream_of("deepseek-flash", db) == (
                "deepseek-v4.1-flash"), "只剩 WorkBuddy 行时历史裸名解析失败"
            assert mr.platform_of_model("deepseek-flash", db) == "workbuddy"
    finally:
        db.close()
        engine.dispose()


# ---------------------------------------------------------------------------
# 8. 公开落点的轻量计算（§六 第 81 行）
# ---------------------------------------------------------------------------

def test_public_targets_reports_group_and_realms() -> None:
    """每行 -> 公开平台/区 + 模型组；目录与 dispatch 共用同一份计算。"""
    mr.invalidate()
    db, engine = _new_db("public-targets")
    try:
        db.add(_platform_row("qoder", "dfmodel", {"name": "DeepSeek-Flash"}))
        db.add(_platform_row("trae", "sample-trae", {"name": "Sample Trae"}))
        db.add(_platform_row("trae", "sample-trae:intl", {"name": "Sample Trae"}))
        db.commit()
        rows = db.query(ModelConfig).order_by(ModelConfig.id).all()
        targets = mr.public_targets(rows)

        got = {(t["platform"], t["model_id"], t["realm"]) for t in targets}
        # 共用一份模型表的平台（Qoder）展开成两个区
        assert ("qoder", "dfmodel", "cn") in got, got
        assert ("qoder-intl", "dfmodel", "intl") in got, got
        # 分两套模型表的平台（Trae）无后缀那条只属于国内
        assert ("trae-cn", "sample-trae", "cn") in got, got
        assert ("trae-intl", "sample-trae", "cn") not in got, got
        assert ("trae-intl", "sample-trae:intl", "intl") in got, got

        for t in targets:
            assert t["group"], t          # 每行都有模型组
            assert t["readable"], t       # 每行都有对外可读名
        assert {t["group"] for t in targets if t["model_id"] == "dfmodel"} == {
            "deepseekv41flash"}

        # 目录用的就是这一份计算
        full = mr.build_payload(db, aggregate_only=False)
        assert {(e["platform"], e["base_model"]) for e in full["data"]} == {
            (t["platform"], t["model_id"]) for t in targets}, full

        # dispatch 手里是查询结果（dict），同样算得出来
        dict_targets = mr.public_targets([{"platform": "qoder",
                                          "model_id": "dfmodel",
                                          "display": "DeepSeek-Flash"}])
        assert {(t["platform"], t["group"]) for t in dict_targets} == {
            ("qoder", "deepseekv41flash"), ("qoder-intl", "deepseekv41flash")}
    finally:
        db.close()
        engine.dispose()


# ---------------------------------------------------------------------------
# 9. 三条路径的往返与分组授权（§六 第 82 行）
# ---------------------------------------------------------------------------

def test_directory_ids_round_trip_through_gateway_resolution() -> None:
    """聚合视图 / 全量视图 / 受分组 Key：列表取到的 ID 都要能送回网关解析。"""
    from admin.platform import model_suffix
    from admin.routers.groups import (GroupIn, create_group,
                                      effective_group_models)

    mr.invalidate()
    db, engine = _new_db("round-trip")
    try:
        clock = _Clock(8300.0)
        with _frozen(clock):
            _add_same_model_everywhere(db)
            db.add(_platform_row("loomy", "spark-x2.6", {}))
            db.commit()

            # (a) 聚合视图：一条 id 代表整个模型组
            agg = mr.build_payload(db, aggregate_only=True)
            assert len(agg["data"]) == 2, _ids(agg)
            for entry in agg["data"]:
                hits = mr.resolve_public_targets(entry["id"], db)
                assert hits, "聚合 ID %r 解析不回任何落点" % (entry["id"],)
                assert {h["platform"] for h in hits} == set(
                    entry["platforms"]), (entry["id"], hits, entry["platforms"])

            # (b) 全量视图：带平台后缀的 id 必须落回它标注的那个平台
            full = mr.build_payload(db, aggregate_only=False)
            for entry in full["data"]:
                pid, _bare, matched = model_suffix.resolve(entry["id"])
                if matched:
                    assert pid == entry["platform"], (
                        entry["id"], pid, entry["platform"])
                hits = mr.resolve_public_targets(entry["id"], db)
                assert (entry["platform"], entry["base_model"]) in {
                    (h["platform"], h["model_id"]) for h in hits}, (entry, hits)

            # (c) 受分组 Key 的清单：只发布组内行，且解析不得越权
            gid = create_group(GroupIn(name="qoder-only", models=["dfmodel"]),
                               True, db)["id"]
            scoped = mr.build_payload(db, aggregate_only=False, group_id=gid)
            assert set(_ids(scoped)) == {"deepseek-flash-qoder",
                                         "deepseek-flash-qoder-intl"}, _ids(scoped)
            allowed = effective_group_models(db, gid)
            assert allowed == {"dfmodel"}, allowed
            for entry in scoped["data"]:
                hits = mr.resolve_public_targets(entry["id"], db,
                                                 allowed_ids=allowed)
                assert hits, entry["id"]
                assert {(h["platform"], h["model_id"])
                        for h in hits} <= {(entry["platform"],
                                            entry["base_model"])}, (entry, hits)
                assert "workbuddy" not in {h["platform"] for h in hits}, hits
    finally:
        db.close()
        engine.dispose()


def test_group_scope_cannot_be_widened_by_alias_or_suffix() -> None:
    """手输别名 / 固定后缀 / 规范公开名都不能通过分组授权。"""
    from admin.routers.groups import (GroupIn, create_group,
                                      effective_group_models)

    mr.invalidate()
    db, engine = _new_db("group-auth")
    try:
        clock = _Clock(8350.0)
        with _frozen(clock):
            _add_same_model_everywhere(db)
            gid = create_group(GroupIn(name="qoder-only", models=["dfmodel"]),
                               True, db)["id"]
            allowed = effective_group_models(db, gid)
            assert allowed == {"dfmodel"}, allowed

            # 组内只有一个平台的私有 ID：跨平台别名不能把范围扩到别家
            for name in ("deepseek-v4.1-flash", "deepseek-flash",
                         "deepseek-flash-qoder", "dfmodel-qoder"):
                hits = mr.resolve_public_targets(name, db, allowed_ids=allowed)
                assert hits, name
                assert {h["model_id"] for h in hits} == {"dfmodel"}, (name, hits)
                assert {h["platform"] for h in hits} <= {"qoder", "qoder-intl"}, (
                    name, hits)

            # 授权判定按组内登记的 model_id 精确比对：别名不扩大授权
            for name in ("deepseek-v4.1-flash", "deepseek-flash",
                         "deepseek-flash-qoder"):
                assert name not in allowed, name
            assert "dfmodel" in allowed

            # 同一批名字在不受限时确实能到别家 —— 证明收窄来自分组
            wide = mr.resolve_public_targets("deepseek-v4.1-flash", db)
            assert {"workbuddy", "marvis"} <= {h["platform"] for h in wide}, wide
    finally:
        db.close()
        engine.dispose()


def test_strict_serialization_and_detail_share_public_ids() -> None:
    """detail=1 与 to_openai_strict 的字段白名单：ID 一致、扩展字段被裁掉。"""
    mr.invalidate()
    db, engine = _new_db("strict-whitelist")
    try:
        clock = _Clock(8360.0)
        with _frozen(clock):
            _add_same_model_everywhere(db)
            for aggregate_only in (True, False):
                detail = mr.build_payload(db, aggregate_only=aggregate_only)
                strict = mr.to_openai_strict(detail)
                assert [e["id"] for e in strict["data"]] == [
                    e["id"] for e in detail["data"]], (aggregate_only,)
                allowed_keys = {"id", "object", "created"} | set(
                    mr._STRICT_EXTRA_KEYS)
                for entry in strict["data"]:
                    assert set(entry) <= allowed_keys, entry
                    assert "caps" not in entry and "platform" not in entry
                for entry in detail["data"]:
                    assert "base_model" in entry, entry
                    assert "caps" in entry, entry
                if aggregate_only:
                    assert all("platforms" in entry for entry in detail["data"])
    finally:
        db.close()
        engine.dispose()


# ---------------------------------------------------------------------------
# 10. 能力字段不得高报（§六 第 83 行）
# ---------------------------------------------------------------------------

def test_aggregate_caps_intersect_all_members() -> None:
    """一个能看图、一个纯文本：聚合项只能发布共同支持的能力。"""
    mr.invalidate()
    db, engine = _new_db("caps-intersect")
    try:
        clock = _Clock(8400.0)
        with _frozen(clock):
            # 能看图的成员排在前面（loomy < workbuddy）：旧实现取「首个成员」
            # 的 caps，于是整组对外宣称 1M / 能看图 —— 落到纯文本平台就失败。
            db.add(_platform_row("loomy", "deepseek-v4.1-flash", {
                "name": "DeepSeek V4.1 Flash",
                "maxInputTokens": 1000000, "maxOutputTokens": 128000,
                "supportsImages": True, "supportsReasoning": True,
                "reasoning": {"supportedEfforts": ["low", "high", "max"]},
            }))
            db.add(_platform_row("workbuddy", "deepseek-v4.1-flash", {
                "name": "DeepSeek V4.1 Flash",
                "maxInputTokens": 64000, "maxOutputTokens": 8000,
                "supportsImages": False, "supportsReasoning": False,
            }))
            db.commit()

            agg = mr.build_payload(db, aggregate_only=True)
            entry = _only_entry(agg)
            assert entry["id"] == "deepseek-v4.1-flash", entry["id"]
            # 数值上限取共同下限
            assert entry["contextWindow"] == 64000, entry
            assert entry["maxTokens"] == 8000, entry
            # 一半成员能看图 ≠ 整组能看图
            assert entry["inputModalities"] == ["text"], entry
            assert entry["caps"]["supports_images"] is False, entry["caps"]
            assert "reasoningEfforts" not in entry, entry
            assert "defaultReasoningEffort" not in entry, entry

            # 严格序列化之后也不能因为空数组 / 缺字段扩大承诺
            strict = _only_entry(mr.to_openai_strict(agg))
            assert strict["inputModalities"] == ["text"], strict
            assert "reasoningEfforts" not in strict, strict

            # 各平台详情用自己能力
            full = mr.build_payload(db, aggregate_only=False)
            by = {e["platform"]: e for e in full["data"]}
            assert by["loomy"]["contextWindow"] == 1000000, by["loomy"]
            assert "image" in by["loomy"]["inputModalities"]
            assert by["loomy"]["reasoningEfforts"] == ["low", "high", "max"]
            assert by["workbuddy"]["contextWindow"] == 64000, by["workbuddy"]
            assert by["workbuddy"]["inputModalities"] == ["text"]
            assert "reasoningEfforts" not in by["workbuddy"], by["workbuddy"]
    finally:
        db.close()
        engine.dispose()


# ---------------------------------------------------------------------------
# 11. 缓存失效：清单 slot 与派生索引一起失效（§六 第 84 行）
# ---------------------------------------------------------------------------

def test_invalidate_clears_native_model_index() -> None:
    """invalidate() 只清清单 slot 时，is_native_model_id 仍会在 10 秒内把
    刚删掉的模型当成原生 id（于是可读名被当成别名改写，上游 400）。"""
    mr.invalidate()
    with _sessions("native-index") as S:
        clock = _Clock(8500.0)
        with _frozen(clock):
            writer = S()
            writer.add(_model_row())
            writer.commit()

            reader = S()
            assert mr.is_native_model_id(
                "workbuddy", "deepseek-v4.1-flash", reader) is True
            assert mr._native_cache.get("ids"), "前置：原生 id 索引应已建立"

            writer.query(ModelConfig).delete()
            writer.commit()
            # 索引仍在有效期内：这就是只清清单 slot 留下来的真实窗口
            assert mr.is_native_model_id(
                "workbuddy", "deepseek-v4.1-flash", reader) is True

            mr.invalidate()
            assert mr._native_cache.get("at") == 0.0, (
                "invalidate() 必须让 is_native_model_id 的索引一起失效：%r"
                % (mr._native_cache,))
            assert mr.is_native_model_id(
                "workbuddy", "deepseek-v4.1-flash", reader) is False, (
                "删掉的行在失效之后仍被当成原生 id")

            # 索引只装模型名：不许把 Key 专属候选 / 冷却 / 账号可用性塞进来
            assert set(mr._native_cache) == {"at", "ids"}, mr._native_cache

            # 管理端 PATCH 走的 helper 同样要让这份索引失效
            from admin.routers.models import _invalidate_routing_cache

            mr._native_cache.update({"at": clock.now,
                                     "ids": {"workbuddy": {"x"}}})
            _invalidate_routing_cache()
            assert mr._native_cache.get("at") == 0.0, mr._native_cache

            writer.close()
            reader.close()


def test_list_aggregate_annotates_public_id_and_client_id() -> None:
    """管理台聚合视图：key/client_id/public_id 与公共目录对得上。"""
    from admin.routers.models import list_aggregate

    mr.invalidate()
    db, engine = _new_db("aggregate-view")
    try:
        clock = _Clock(8600.0)
        with _frozen(clock):
            _add_same_model_everywhere(db)
            # 被禁用的行仍要能在管理台看到（下架模型要能重新启用）
            db.add(ModelConfig(platform="qoder", model_id="gmodel", enabled=0,
                               upstream_metadata=json.dumps({"name": "GLM-5.3"})))
            db.commit()
            # 一个免费成员：倍率明细与 free_any 不能因为聚合而丢
            db.query(ModelConfig).filter(
                ModelConfig.platform == "qoder").update(
                    {"credit_multiplier": 0.0})
            db.commit()
            mr.invalidate()

            view = list_aggregate(level=None, _=True, db=db)
            published = {e["id"] for e in
                         mr.build_payload(db, aggregate_only=True)["data"]}
            flash = next(item for item in view["items"]
                         if item["public_id"] == "deepseek-v4.1-flash")
            # 管理台 key 是稳定字符串（不是 (platform, realm) 数组）
            assert all(isinstance(item["key"], str) for item in view["items"])
            assert flash["key"] == "deepseekv41flash", flash["key"]
            # 聚合公开 ID 就是公共目录会发布的那一个
            assert flash["public_id"] in published, (flash["public_id"], published)
            # 倍率与免费标记仍按明细展示
            assert flash["free_any"] is True, flash
            assert flash["min_multiplier"] == 0, flash

            for item in view["items"]:
                for entry in item["platforms"]:
                    assert entry["public_id"] == item["public_id"], entry
                    pid = entry.get("platform_id") or entry["platform"]
                    if not entry.get("enabled"):
                        continue  # 禁用行不可调用，只作为管理端明细
                    hits = mr.resolve_public_targets(entry["client_id"], db)
                    assert hits, entry["client_id"]
                    assert (pid, entry["model_id"]) in {
                        (h["platform"], h["model_id"]) for h in hits}, (
                        entry["client_id"], hits)

            # 禁用模型不进公共目录，只留在管理台
            assert not [mid for mid in published if mid.startswith("glm")]
            assert "gmodel" in {entry["model_id"] for item in view["items"]
                                for entry in item["platforms"]}
    finally:
        db.close()
        engine.dispose()


# ---------------------------------------------------------------------------
# 12. 分组 Key 的「清单取 ID → 调用」往返（§六 第 82 行）
# ---------------------------------------------------------------------------

def test_group_key_accepts_public_ids_from_directory() -> None:
    """分组只登记 qoder 的 dfmodel 时，从清单复制的公开 ID 不再 403。

    修复前：allowed={"dfmodel"} + requested "deepseek-v4.1-flash"
    （聚合视图下发的那个 ID）-> requested 不在 allowed -> 403 model_not_in_group。
    """
    from admin.routers.proxy import _pick_best_model

    mr.invalidate()
    db, engine = _new_db("group-roundtrip")
    try:
        clock = _Clock(8700.0)
        with _frozen(clock):
            _add_same_model_everywhere(db)
            allowed = {"dfmodel"}
            assert _pick_best_model(db, "deepseek-v4.1-flash", allowed) == "dfmodel"
            assert _pick_best_model(db, "deepseek-flash", allowed) == "dfmodel"
            assert _pick_best_model(db, "deepseek-flash-qoder", allowed) == "dfmodel"
            assert _pick_best_model(db, "dfmodel-qoder", allowed) == "dfmodel"
            # 上下文标记先剥离，再命中组内模型（原有语义不变）
            assert _pick_best_model(db, "deepseek-v4.1-flash[1m]", allowed) == "dfmodel"
            # 组内没有的模型 / 指向组外平台的写法：一律维持拒绝，不扩权
            for name in ("deepseek-v4-pro", "gpt-4o", "main-auto",
                         "deepseek-v4.1-flash-workbuddy", "main-auto-marvis"):
                assert _pick_best_model(db, name, allowed) is None, name
    finally:
        db.close()
        engine.dispose()


def test_group_key_exact_hit_wins_over_alias() -> None:
    """精确命中优先：组内直接有这个名字时原样返回，不做多余换算。"""
    from admin.routers.proxy import _pick_best_model

    mr.invalidate()
    db, engine = _new_db("group-exact-first")
    try:
        clock = _Clock(8750.0)
        with _frozen(clock):
            _add_same_model_everywhere(db)
            allowed = {"dfmodel", "deepseek-v4.1-flash"}
            assert _pick_best_model(db, "dfmodel", allowed) == "dfmodel"
            # 请求的正是 workbuddy 的原生 id：不得被换成 qoder 的 dfmodel
            assert _pick_best_model(db, "deepseek-v4.1-flash", allowed) == (
                "deepseek-v4.1-flash")
    finally:
        db.close()
        engine.dispose()


def test_group_suffix_pins_platform_inside_group() -> None:
    """带平台后缀只能落该平台那一行，不能被换算成别的组内平台。"""
    from admin.routers.proxy import _pick_best_model

    mr.invalidate()
    db, engine = _new_db("group-suffix-pin")
    try:
        clock = _Clock(8800.0)
        with _frozen(clock):
            _add_same_model_everywhere(db)
            allowed = {"dfmodel", "deepseek-v4.1-flash"}
            hits = mr.resolve_public_targets("deepseek-v4.1-flash-workbuddy", db)
            assert {(h["platform"], h["model_id"]) for h in hits} == {
                ("workbuddy", "deepseek-v4.1-flash")}, hits
            got = _pick_best_model(db, "deepseek-v4.1-flash-workbuddy", allowed)
            assert got == "deepseek-v4.1-flash", got
            assert got != "dfmodel", "带 workbuddy 后缀却被换算成 qoder 的模型"
            # 同一组里 qoder 的专名只能落 qoder
            assert _pick_best_model(db, "deepseek-flash-qoder", allowed) == "dfmodel"
    finally:
        db.close()
        engine.dispose()


def test_group_alias_resolution_is_deterministic_and_fail_closed() -> None:
    """多个组内候选时结果稳定；目录解析异常/空分组时退回 None（不 500）。"""
    from admin.routers import proxy

    mr.invalidate()
    db, engine = _new_db("group-deterministic")
    try:
        clock = _Clock(8850.0)
        with _frozen(clock):
            _add_same_model_everywhere(db)
            allowed = {"dfmodel", "deepseek-v4.1-flash", "main-auto"}
            first = proxy._pick_best_model(db, "deepseek-flash", allowed)
            second = proxy._pick_best_model(db, "deepseek-flash", allowed)
            assert first == second, (first, second)
            assert first in allowed, first
            # 目录解析抛异常 -> fail closed（None），不把 500 冒出去
            with patch.object(mr, "resolve_public_targets",
                              side_effect=RuntimeError("boom")):
                assert proxy._pick_best_model(db, "deepseek-flash", allowed) is None
            # 空分组：与修复前一致，一律拒绝
            assert proxy._pick_best_model(db, "deepseek-flash", set()) is None
    finally:
        db.close()
        engine.dispose()


def test_group_key_allowed_none_and_auto_paths_unchanged() -> None:
    """allowed=None（未绑分组）与 auto 分支的行为一个字节都不变。"""
    from admin.routers import proxy

    mr.invalidate()
    db, engine = _new_db("group-unchanged")
    try:
        clock = _Clock(8900.0)
        with _frozen(clock):
            _add_same_model_everywhere(db)
            # 未绑分组：白名单里有的原样放行、没有的照旧拒绝
            assert proxy._pick_best_model(
                db, "deepseek-v4.1-flash", None) == "deepseek-v4.1-flash"
            assert proxy._pick_best_model(db, "gpt-4o", None) is None
            # auto：仍走「组内免费优先」的老逻辑
            assert proxy._pick_best_model(db, "auto", {"dfmodel"}) == "dfmodel"
    finally:
        db.close()
        engine.dispose()


def test_group_alias_fix_covers_all_three_entries() -> None:
    """三条入口共用 _pick_best_model：集中修一处即覆盖三处（源码级接线）。"""
    src = (ROOT / "admin" / "routers" / "proxy.py").read_text(encoding="utf-8")
    for entry in ("async def chat_completions(",
                  "async def responses_proxy(",
                  "async def anthropic_messages("):
        start = src.index(entry)
        ends = [pos for pos in (src.find("\nasync def ", start + 1),
                                src.find("\n@router.", start + 1)) if pos > 0]
        body = src[start:min(ends) if ends else len(src)]
        assert "_pick_best_model" in body, entry
        assert "_key_allowed_models" in body, entry


# ---------------------------------------------------------------------------
# 13. F6：组内登记的大小写与目录行不一致（同行归一，不得跨平台顶替）
# ---------------------------------------------------------------------------

def test_group_member_case_mismatch_normalized_to_catalog() -> None:
    """组里登记展示名写法 DeepSeek-V4.1-Flash，目录行是 deepseek-v4.1-flash。

    修复前 effective_group_models() 做精确集合交集，成员被丢光 -> 该 Key 对
    所有模型 403 model_not_in_group。修复后按「同平台同区」的目录拼写归一。
    """
    from admin.routers import proxy
    from admin.routers.groups import (GroupIn, create_group,
                                      effective_group_models)

    mr.invalidate()
    db, engine = _new_db("group-case")
    try:
        clock = _Clock(9100.0)
        with _frozen(clock):
            db.add(_platform_row("workbuddy", "deepseek-v4.1-flash",
                                 {"name": "DeepSeek V4.1 Flash"}))
            db.commit()
            gid = create_group(
                GroupIn(name="case-mismatch", models=["DeepSeek-V4.1-Flash"]),
                True, db)["id"]
            allowed = effective_group_models(db, gid)
            assert allowed == {"deepseek-v4.1-flash"}, (
                "大小写不一致时组内成员被丢光（整组 403）：%r" % (allowed,))
            # 授权求交与目录可见性都吃这个集合：客户端发目录拼写必须放行
            assert proxy._pick_best_model(
                db, "deepseek-v4.1-flash", allowed) == "deepseek-v4.1-flash"
    finally:
        db.close()
        engine.dispose()


def test_group_member_case_mismatch_does_not_widen_other_platform() -> None:
    """反向：归一不等于「同款模型全放行」——别家平台的拼写仍被拒。"""
    from admin.routers import proxy
    from admin.routers.groups import (GroupIn, create_group,
                                      effective_group_models)

    mr.invalidate()
    db, engine = _new_db("group-case-neg")
    try:
        clock = _Clock(9150.0)
        with _frozen(clock):
            db.add(_platform_row("workbuddy", "deepseek-v4.1-flash",
                                 {"name": "DeepSeek V4.1 Flash"}))
            db.add(_platform_row("qoder", "dfmodel", {"name": "DeepSeek-Flash"}))
            db.add(_platform_row("marvis", "main-auto", {}))
            db.commit()
            gid = create_group(
                GroupIn(name="case-neg", models=["DeepSeek-V4.1-Flash"]),
                True, db)["id"]
            allowed = effective_group_models(db, gid)
            assert allowed == {"deepseek-v4.1-flash"}, allowed
            # 同一个逻辑模型在别家平台的原生 id（拼写不同）不得被带进来
            assert "dfmodel" not in allowed and "main-auto" not in allowed
            # 手输平台专名 / 后缀也不能借归一扩权
            assert proxy._pick_best_model(
                db, "deepseek-flash-qoder", allowed) is None
            assert proxy._pick_best_model(db, "main-auto-marvis", allowed) is None
    finally:
        db.close()
        engine.dispose()


def test_group_member_twin_realm_is_not_substituted() -> None:
    """trae 的 :intl 孪生不得顶替国内行，国内行也不得顶替 :intl 行。"""
    from admin.routers.groups import (GroupIn, create_group,
                                      effective_group_models)

    mr.invalidate()
    db, engine = _new_db("group-twin")
    try:
        clock = _Clock(9200.0)
        with _frozen(clock):
            db.add(_platform_row("trae", "deepseek-v4.1-flash"))
            db.add(_platform_row("trae", "deepseek-v4.1-flash:intl"))
            db.commit()

            gid_cn = create_group(
                GroupIn(name="twin-cn", models=["DeepSeek-V4.1-Flash"]),
                True, db)["id"]
            assert effective_group_models(db, gid_cn) == {
                "deepseek-v4.1-flash"}, "国内成员被国际行顶替"

            gid_intl = create_group(
                GroupIn(name="twin-intl",
                        models=["DeepSeek-V4.1-Flash:intl"]), True, db)["id"]
            assert effective_group_models(db, gid_intl) == {
                "deepseek-v4.1-flash:intl"}, "国际成员被国内行顶替"
    finally:
        db.close()
        engine.dispose()


def test_group_members_missing_disabled_or_empty_stay_minimal() -> None:
    """成员不在目录里 / 已禁用 / 空组：沿用现有 effective 语义，不放大也不清空。"""
    from admin.routers.groups import (GroupIn, create_group,
                                      effective_group_models)

    mr.invalidate()
    db, engine = _new_db("group-edge")
    try:
        clock = _Clock(9250.0)
        with _frozen(clock):
            db.add(_platform_row("workbuddy", "deepseek-v4.1-flash",
                                 {"name": "DeepSeek V4.1 Flash"}))
            db.add(ModelConfig(platform="loomy", model_id="spark-x2.6",
                               enabled=0, upstream_metadata=json.dumps({})))
            db.commit()
            gid = create_group(
                GroupIn(name="edge",
                        models=["DeepSeek-V4.1-Flash", "no-such-model",
                                "spark-x2.6"]), True, db)["id"]
            allowed = effective_group_models(db, gid)
            # 大小写不一致的成员归一后留下；目录里没有的与已禁用的丢掉
            assert allowed == {"deepseek-v4.1-flash"}, allowed
            assert "no-such-model" not in allowed and "spark-x2.6" not in allowed
            # 空组（不存在的分组 == 没有成员）仍然拒绝一切
            assert effective_group_models(db, 999999) == set()
    finally:
        db.close()
        engine.dispose()


def test_group_case_fold_never_spans_platforms() -> None:
    """t16 越权判据的镜像：原串相等的那家优先，别家的大小写变体不许被带进来。

    两个平台同一个模型、库内写法只差大小写（alpha demo-model / beta
    DEMO-MODEL）。组里登记的正是 alpha 的写法 -> 只授权 alpha 那行；
    beta 那行虽然大小写不敏感地「同名」，也**不得**被归一放行。
    """
    from admin.routers.groups import (GroupIn, create_group,
                                      effective_group_models)

    mr.invalidate()
    db, engine = _new_db("group-case-anchor")
    try:
        clock = _Clock(9300.0)
        with _frozen(clock):
            db.add(_platform_row("alpha", "demo-model"))
            db.add(_platform_row("beta", "DEMO-MODEL"))
            db.commit()
            gid = create_group(GroupIn(name="anchor", models=["demo-model"]),
                               True, db)["id"]
            assert effective_group_models(db, gid) == {"demo-model"}, (
                "大小写归一跨平台扩权了")

            # 反过来：组里登记 beta 的写法，同样只授权 beta 自己那行
            gid_beta = create_group(
                GroupIn(name="anchor-beta", models=["DEMO-MODEL"]),
                True, db)["id"]
            assert effective_group_models(db, gid_beta) == {"DEMO-MODEL"}

            # 整张目录都没有原串相等的行时才退一步：多平台同名只取一家，
            # 且按 storage 名排序（确定性），不做多平台并集
            gid_fold = create_group(
                GroupIn(name="fold-multi", models=["Demo-Model"]),
                True, db)["id"]
            assert effective_group_models(db, gid_fold) == {"demo-model"}, (
                "无原串锚点时没有收敛到确定的一家")
    finally:
        db.close()
        engine.dispose()


# ---------------------------------------------------------------------------
# 14. F6 另一半：分组大小写不一致时 /v1/models 清单必须能看到该模型
# ---------------------------------------------------------------------------

def test_group_directory_visible_when_member_case_mismatched() -> None:
    """组登记 DeepSeek-V4.1-Flash、目录行 deepseek-v4.1-flash。

    修复前 build_payload 用**原始登记字符串**做 SQL 过滤（model_id IN ...），
    清单是空的：调用侧 t19 已放行，但客户端看不到、选不了，交付目标第 3 行
    那一环没闭环。
    """
    from admin.routers.groups import GroupIn, create_group

    mr.invalidate()
    db, engine = _new_db("group-catalog-case")
    try:
        clock = _Clock(9400.0)
        with _frozen(clock):
            db.add(_platform_row("workbuddy", "deepseek-v4.1-flash",
                                 {"name": "DeepSeek V4.1 Flash"}))
            db.commit()
            gid = create_group(
                GroupIn(name="case-catalog", models=["DeepSeek-V4.1-Flash"]),
                True, db)["id"]

            full = mr.build_payload(db, aggregate_only=False, group_id=gid)
            assert _ids(full) == ["deepseek-v4.1-flash"], (
                "大小写不一致的分组在清单里看不到模型：%r" % (_ids(full),))

            detail = mr.build_payload(db, aggregate_only=True, group_id=gid)
            assert _ids(detail) == ["deepseek-v4.1-flash"], _ids(detail)
            assert detail["data"][0]["base_model"] == "deepseek-v4.1-flash"
            assert detail["data"][0]["platforms"] == ["workbuddy"]

            strict = mr.to_openai_strict(detail)
            assert _ids(strict) == ["deepseek-v4.1-flash"], _ids(strict)
            assert set(strict["data"][0]) <= {"id", "object", "created"} | set(
                mr._STRICT_EXTRA_KEYS), strict["data"][0]
    finally:
        db.close()
        engine.dispose()


def test_group_directory_excludes_models_outside_group() -> None:
    """反向：组外模型一条都不许出现在分组清单里（归一不得放宽权限）。"""
    from admin.routers.groups import GroupIn, create_group

    mr.invalidate()
    db, engine = _new_db("group-catalog-scope")
    try:
        clock = _Clock(9450.0)
        with _frozen(clock):
            db.add(_platform_row("workbuddy", "deepseek-v4.1-flash",
                                 {"name": "DeepSeek V4.1 Flash"}))
            db.add(_platform_row("qoder", "dfmodel", {"name": "DeepSeek-Flash"}))
            db.add(_platform_row("marvis", "main-auto", {}))
            db.commit()
            gid = create_group(
                GroupIn(name="case-scope", models=["DeepSeek-V4.1-Flash"]),
                True, db)["id"]

            for aggregate_only in (False, True):
                payload = mr.build_payload(db, aggregate_only=aggregate_only,
                                           group_id=gid)
                ids = _ids(payload)
                assert ids == ["deepseek-v4.1-flash"], (
                    aggregate_only, ids)
                dumped = json.dumps(payload)
                for outside in ("dfmodel", "main-auto", "deepseek-flash-qoder",
                                "main-auto-marvis"):
                    assert outside not in dumped, (
                        "组外模型出现在分组清单里", outside, dumped[:400])
    finally:
        db.close()
        engine.dispose()


def test_group_directory_empty_and_missing_member() -> None:
    """空组 / 成员在目录里不存在：返回空清单，不 500、也不退化成全量。"""
    from admin.routers.groups import GroupIn, create_group

    mr.invalidate()
    db, engine = _new_db("group-catalog-edge")
    try:
        clock = _Clock(9500.0)
        with _frozen(clock):
            db.add(_platform_row("workbuddy", "deepseek-v4.1-flash",
                                 {"name": "DeepSeek V4.1 Flash"}))
            db.commit()
            gid_missing = create_group(
                GroupIn(name="catalog-missing", models=["no-such-model"]),
                True, db)["id"]
            assert mr.build_payload(db, group_id=gid_missing)["data"] == []
            # 不存在/空分组：仍然空清单（不许退化成全量）
            assert mr.build_payload(db, group_id=999999)["data"] == []
            # 未分组视图不受影响
            assert _ids(mr.build_payload(db)) == ["deepseek-v4.1-flash"]
    finally:
        db.close()
        engine.dispose()


def test_group_directory_cache_follows_normalized_members() -> None:
    """缓存指纹吃的是**归一后**的成员集合：改了组内写法/成员，清单立刻跟着变。"""
    from admin.routers.groups import GroupIn, create_group, update_group

    mr.invalidate()
    db, engine = _new_db("group-catalog-cache")
    try:
        clock = _Clock(9550.0)
        with _frozen(clock):
            db.add(_platform_row("workbuddy", "deepseek-v4.1-flash",
                                 {"name": "DeepSeek V4.1 Flash"}))
            db.commit()
            gid = create_group(
                GroupIn(name="catalog-cache", models=["DeepSeek-V4.1-Flash"]),
                True, db)["id"]
            first = mr.build_payload(db, aggregate_only=True, group_id=gid)
            assert _ids(first) == ["deepseek-v4.1-flash"], _ids(first)

            # 只改大小写写法：归一结果不变 -> 缓存命中也没问题（清单相同）
            update_group(gid, GroupIn(name="catalog-cache", note="",
                                      models=["deepseek-v4.1-flash"]), True, db)
            clock.now += 1.0
            same = mr.build_payload(db, aggregate_only=True, group_id=gid)
            assert _ids(same) == ["deepseek-v4.1-flash"], _ids(same)

            # 换成目录里没有的成员：归一集合变了 -> 必须立刻变空，不许拿旧清单
            update_group(gid, GroupIn(name="catalog-cache", note="",
                                      models=["no-such-model"]), True, db)
            clock.now += 1.0
            after = mr.build_payload(db, aggregate_only=True, group_id=gid)
            assert after["data"] == [], ("分组变了还返回旧清单", _ids(after))
    finally:
        db.close()
        engine.dispose()



def _run_all() -> int:
    global PASS, FAIL
    tests = [
        test_metadata_change_rebuilds_same_view,
        test_note_change_rebuilds_same_view,
        test_capability_override_rebuilds_same_view,
        test_ttl_independent_between_views,
        test_ttl_independent_between_groups,
        test_invalidate_clears_every_slot,
        test_routing_cache_helper_invalidates_models_cache,
        test_group_update_visible_to_other_worker_session,
        test_group_membership_lookup_failure_fails_closed,
        test_group_membership_ids_with_commas_do_not_collide,
        test_group_models_key_is_string_per_group,
        test_group_models_keeps_realm_details,
        test_aggregate_public_id_stable_across_platform_changes,
        test_historical_alias_and_platform_suffix_still_resolve,
        test_public_targets_reports_group_and_realms,
        test_directory_ids_round_trip_through_gateway_resolution,
        test_group_scope_cannot_be_widened_by_alias_or_suffix,
        test_strict_serialization_and_detail_share_public_ids,
        test_aggregate_caps_intersect_all_members,
        test_invalidate_clears_native_model_index,
        test_list_aggregate_annotates_public_id_and_client_id,
        test_group_key_accepts_public_ids_from_directory,
        test_group_key_exact_hit_wins_over_alias,
        test_group_suffix_pins_platform_inside_group,
        test_group_alias_resolution_is_deterministic_and_fail_closed,
        test_group_key_allowed_none_and_auto_paths_unchanged,
        test_group_alias_fix_covers_all_three_entries,
        test_group_member_case_mismatch_normalized_to_catalog,
        test_group_member_case_mismatch_does_not_widen_other_platform,
        test_group_member_twin_realm_is_not_substituted,
        test_group_members_missing_disabled_or_empty_stay_minimal,
        test_group_case_fold_never_spans_platforms,
        test_group_directory_visible_when_member_case_mismatched,
        test_group_directory_excludes_models_outside_group,
        test_group_directory_empty_and_missing_member,
        test_group_directory_cache_follows_normalized_members,
    ]
    for fn in tests:
        try:
            fn()
        except AssertionError as exc:
            FAIL += 1
            print("  [!!] %s: %s" % (fn.__name__, exc))
        except Exception as exc:  # noqa: BLE001
            FAIL += 1
            print("  [!!] %s: %s: %s" % (fn.__name__, type(exc).__name__, exc))
        else:
            PASS += 1
            print("  [ok] %s" % fn.__name__)
    print("\n通过 %d / 失败 %d" % (PASS, FAIL))
    return 1 if FAIL else 0


def main() -> int:
    try:
        return _run_all()
    finally:
        shutil.rmtree(_TMP_ROOT, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
