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
