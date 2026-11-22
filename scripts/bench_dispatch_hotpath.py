#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dispatch 热路径度量：每请求 SQL 数 / model_configs 语句数 / 真·全表读取 / 排名次数 / p50-p95。

用途：给「平台作用域模型解析」计划（任务 5 §七 第 90-91 行）提供**可重复**的
前后对照数字。改动调度代码前后各跑一次，同一份固定数据即可对比。

## 怎么做到「固定数据 + 零公网」

* 临时 SQLite：4 个平台（workbuddy / alpha / beta-cn / beta-intl，后两者共用
  storage=beta 的号池）+ 128 条 model_configs。
* 假上游：平台网关换成进程内响应器，主应用（默认平台）也是进程内响应器 ——
  计时里不含任何网络延迟。
* 计数：SQLAlchemy before_cursor_execute 事件逐条统计 SQL。
* 排名次数：包一层 gateway_router.order / gateway_router.health 调用计数。

## 两个「模型表」指标必须分开（2026-11-23 修的口径 bug）

| 指标 | 含义 |
|---|---|
| cfg_statements | 本次请求发出的、涉及 model_configs 的 SQL **条数**（不管有没有 WHERE） |
| full_scans | 其中**完全没有 WHERE 子句**的条数 —— 这才是计划 §七 第 91 行说的「全平台模型表扫描」 |

历史版本用一个纯字符串子串判断全表读取：

    " where " not in sql.lower()      # ← 错

SQLAlchemy 渲染的是**多行大写**形式，WHERE 前面是换行不是空格：

    SELECT model_configs.model_id, model_configs.upstream_metadata
    FROM model_configs
    WHERE model_configs.platform = ? AND model_configs.enabled = ?

小写化后是 "\nwhere ..."，" where " 永不命中 —— 于是**任何带 WHERE 的
model_configs 查询都被算成全表读取**（误报）。现在判定器改为单词边界正则
（_is_full_table_scan），并有单测钉住（tests/test_gateway_reliability_scheduling.py）。

## 四条路径

| key | 请求 | 说明 |
|---|---|---|
| default_direct | wb-model-00 | 裸名直达默认平台（走主应用） |
| suffix_fixed | alpha-model-00-alpha | 模型名带平台后缀，固定到 alpha |
| auto_aggregate | shared-v4-flash | 无后缀无绑定、Key 开聚合 -> 按策略分摊 |
| retry_after_503 | retry-v4-flash | 聚合候选里第一个明确 503，第二次落 beta-cn |

每条路径都测两类样本：**冷启动**（清空 dispatch / gateway_router / models_router
的所有缓存之后的第一次请求）与**预热后 N 次**（默认 100 次，取均值 + p50/p95）。

## 运行（仓库根；本脚本必须在独立进程里跑）

    python -B scripts/bench_dispatch_hotpath.py              # 人类可读表格
    python -B scripts/bench_dispatch_hotpath.py --json       # 只输出 JSON（给测试用）
    python -B scripts/bench_dispatch_hotpath.py --dump-sql   # 额外打印 model_configs 语句与判定

可选参数：--models 128（种多少条 model_configs）、--warm 100（预热后请求数）、
--out PATH（把 JSON 写成 UTF-8 文件）。

注意：模块导入本身**没有副作用**（不建临时库、不改环境变量），所以单测可以
直接 import 它来测 _is_full_table_scan / _sql_stats 这两个纯函数。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: 与 admin.platform.registry.DEFAULT_PLATFORM 一致；运行时断言（下面 _register_fixture）。
DEFAULT_PLATFORM = "workbuddy"
CHAT_PATH = "/v1/chat/completions"

#: 4 个平台：(id, label, storage_platform, realm)。后两者共用 storage=beta 的号池。
PLATFORM_FIXTURE = (
    (DEFAULT_PLATFORM, "WorkBuddy", "", ""),
    ("alpha", "Alpha", "", ""),
    ("beta-cn", "Beta 国内版", "beta", "cn"),
    ("beta-intl", "Beta 国际版", "beta", "intl"),
)

#: 号池种子：(storage, realm, 账号数)。realm 同时写进 Account.domain。
ACCOUNT_FIXTURE = (
    (DEFAULT_PLATFORM, "", 2),
    ("alpha", "", 2),
    ("beta", "cn", 2),
    ("beta", "intl", 1),
)

#: 四条路径的请求模型。特殊模型（shared/retry）单独种行，见 _seed_models()。
PATHS = {
    "default_direct": {"model": "%s-model-00" % DEFAULT_PLATFORM},
    "suffix_fixed": {"model": "alpha-model-00-alpha"},
    "auto_aggregate": {"model": "shared-v4-flash"},
    "retry_after_503": {"model": "retry-v4-flash", "fail_first": "alpha",
                        "expect_platform": "beta-cn"},
}

#: 假上游的「这次谁来 503」。由 _FakeGateway 读取，每条请求都会复位。
#: trace 记录本次请求真实打到哪些目标（"main" = 主应用 / 平台 id = 假网关）。
_PLAN: dict = {"fail_platform": "", "fail_once": False, "trace": []}

_KEY = SimpleNamespace(id=1, name="bench-key", label="bench", platform="",
                       aggregate=True, group_id=0, account_id=None,
                       account_ids="", status="active")


# ---------------------------------------------------------------------------
# 纯函数：SQL 分类（无任何 admin / DB 依赖，单测直接吃字符串）
# ---------------------------------------------------------------------------
_CFG_TABLE = "model_configs"
_WHERE_RE = re.compile(r"\bwhere\b", re.I)


def _is_full_table_scan(sql: str) -> bool:
    """这条 SQL 是否是「涉及 model_configs 且**没有任何 WHERE**」的全表读取。

    ⚠️ 2026-11-23 修：旧实现是 " where " not in sql.lower()。SQLAlchemy 渲染
    多行 SQL 时 WHERE 前是换行（"\nWHERE ..."），而 WHERE 前面也可能紧跟别的
    标识符（"ON model_configs.x WHERE"），" where " 这种带空格的子串判断会
    漏判 —— 结果是**所有带 WHERE 的 model_configs 查询都被误报成全表读取**。
    用单词边界正则才与「有没有 WHERE 子句」等价（大小写不敏感、跨行有效）。
    """
    text = str(sql or "")
    if _CFG_TABLE not in text.lower():
        return False
    return _WHERE_RE.search(text) is None


def _sql_stats(statements) -> dict:
    """把一批 SQL 语句分成「总数 / 涉及 model_configs 的条数 / 真·全表读取」。

    两个模型表指标**分开返回**，不合并：合起来就分不清「多查了几条带条件的
    小查询」和「又全表扫了一遍」。
    """
    rows = list(statements or ())
    cfg = [s for s in rows if _CFG_TABLE in str(s).lower()]
    return {
        "sql": len(rows),
        "cfg_statements": len(cfg),
        "full_scans": sum(1 for s in cfg if _is_full_table_scan(s)),
    }


# ---------------------------------------------------------------------------
# 临时库环境（显式调用，不在 import 时发生）
# ---------------------------------------------------------------------------
_TMP_DIR: Path | None = None


def prepare_environment() -> Path:
    """建临时目录并把 ADMIN_DATABASE_URL 指过去。

    **必须在 import admin.db 之前调用**（所以不能在模块顶层做）。同时在已经
    导入了非 sqlite 的 admin.db 时直接报错 —— 这个脚本绝不允许写真实库。
    """
    global _TMP_DIR
    existing = sys.modules.get("admin.db")
    if existing is not None:
        url = str(getattr(getattr(existing, "engine", None), "url", ""))
        if url and not url.startswith("sqlite"):
            raise RuntimeError(
                "当前进程里的 admin.db 已指向非 sqlite 库（%s）：本脚本必须"
                "在独立进程里运行，否则会把种子数据写进真实库" % url)
    if _TMP_DIR is None:
        _TMP_DIR = Path(tempfile.mkdtemp(prefix="miraihub_bench_dispatch_"))
        os.environ["ADMIN_DATABASE_URL"] = "sqlite:///" + (
            _TMP_DIR / "bench.db").as_posix()
        os.environ.pop("REDIS_URL", None)
    return _TMP_DIR


def _cleanup(engine) -> None:
    global _TMP_DIR
    try:
        engine.dispose()
    except Exception:  # noqa: BLE001
        pass
    if _TMP_DIR is not None:
        shutil.rmtree(_TMP_DIR, ignore_errors=True)
        _TMP_DIR = None


# ---------------------------------------------------------------------------
# 假上游
# ---------------------------------------------------------------------------
def _fixture_spec(registry, platform_id: str, realm: str = ""):
    """dispatch._spec_with_gateway 的替身：只认 4 个固定平台（含 storage 形态）。"""
    spec = registry.get(platform_id)
    if spec is not None:
        return spec
    for candidate in registry.all_platforms():
        if candidate.storage() == platform_id and (
                not realm or candidate.realm == realm):
            return candidate
    return None


class _FakeGateway:
    """进程内假上游：读取已回放的 body，按 _PLAN 决定「先 503 还是直接 200」。"""

    def __init__(self, platform_id: str):
        self.platform_id = platform_id

    async def dispatch(self, request, path):
        from starlette.responses import JSONResponse

        try:
            payload = json.loads(await request.body() or b"{}")
        except Exception:                            # noqa: BLE001
            payload = {}
        model = str(payload.get("model") or "")
        _PLAN["trace"].append(self.platform_id)
        if _PLAN["fail_once"] and _PLAN["fail_platform"] == self.platform_id:
            _PLAN["fail_once"] = False
            return JSONResponse(
                {"error": {"message": "bench fake 503", "code": "bench_503"}},
                status_code=503)
        return JSONResponse({"id": "bench-%s" % self.platform_id,
                             "platform": self.platform_id, "model": model})


_GATEWAYS = {spec[0]: _FakeGateway(spec[0]) for spec in PLATFORM_FIXTURE}


async def _main_app(scope, receive, send):
    """默认平台（WorkBuddy）在真实进程里就是主应用：这里换成进程内响应器。"""
    from starlette.responses import JSONResponse

    _PLAN["trace"].append("main")
    response = JSONResponse({"id": "bench-main-app",
                             "platform": DEFAULT_PLATFORM})
    await response(scope, receive, send)


# ---------------------------------------------------------------------------
# 计数
# ---------------------------------------------------------------------------
class _SqlCounter:
    """逐条记录 SQL 语句（before_cursor_execute）。"""

    def __init__(self, bind):
        self.bind = bind
        self.statements: list = []

    def __enter__(self):
        from sqlalchemy import event

        event.listen(self.bind, "before_cursor_execute", self._on, named=True)
        return self

    def __exit__(self, *exc):
        from sqlalchemy import event

        event.remove(self.bind, "before_cursor_execute", self._on)
        return False

    def _on(self, conn, cursor, statement, parameters, context, executemany):
        self.statements.append(statement)

    def reset(self) -> None:
        self.statements.clear()


class _CallCounter:
    """排名 / 健康度统计的调用次数（确定性，不受计时抖动影响）。"""

    def __init__(self):
        self.order = 0
        self.health = 0
        self._patches: list = []

    def __enter__(self, gateway_router):
        orig_order = gateway_router.order
        orig_health = gateway_router.health
        counter = self

        def _order(db, platform_ids=None, **kwargs):
            counter.order += 1
            return orig_order(db, platform_ids, **kwargs)

        def _health(db, platform_id):
            counter.health += 1
            return orig_health(db, platform_id)

        self._patches = [
            mock.patch.object(gateway_router, "order", _order),
            mock.patch.object(gateway_router, "health", _health),
        ]
        for patch in self._patches:
            patch.start()
        return self

    def __exit__(self, *exc):
        for patch in reversed(self._patches):
            patch.stop()
        return False

    def reset(self) -> None:
        self.order = 0
        self.health = 0


def _percentile(values, q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = max(0, min(len(ordered) - 1,
                       int(math.ceil(q * len(ordered))) - 1))
    return ordered[index]


# ---------------------------------------------------------------------------
# 种子数据
# ---------------------------------------------------------------------------
def _seed_accounts(db, models):
    from admin.models import Account

    for storage, realm, count in ACCOUNT_FIXTURE:
        for index in range(count):
            db.add(Account(platform=storage,
                           name="%s-%s-%02d" % (storage, realm or "all", index),
                           uid="uid-%s-%s-%02d" % (storage, realm or "all", index),
                           domain=realm,
                           auth_json="{}",
                           status="active",
                           balance_total=1000,
                           balance_remain=800))


def _seed_models(db, models: int = 128) -> dict:
    """种 4 个平台要用的号池与 model_configs（默认共 128 行）。"""
    from admin.models import ModelConfig

    _seed_accounts(db, models)
    # 特殊模型：聚合（三平台同名）与重试（两平台同名）
    specials = [
        ("alpha", "shared-v4-flash"),
        ("beta", "shared-v4-flash"),
        ("alpha", "retry-v4-flash"),
        ("beta", "retry-v4-flash"),
    ]
    remaining = max(3, models - len(specials))
    per_platform = max(1, remaining // 3)
    leftover = max(0, remaining - per_platform * 3)
    plan = (
        (DEFAULT_PLATFORM, per_platform + leftover,
         "%s-model-%%02d" % DEFAULT_PLATFORM),
        ("alpha", per_platform, "alpha-model-%02d"),
        ("beta", per_platform, "beta-model-%02d"),
    )
    total = 0
    for storage, count, pattern in plan:
        for index in range(count):
            db.add(ModelConfig(platform=storage, model_id=pattern % index,
                               enabled=1, level="system",
                               upstream_metadata="{}",
                               credit_multiplier=0.0))
            total += 1
    for storage, model_id in specials:
        db.add(ModelConfig(platform=storage, model_id=model_id, enabled=1,
                           level="system", upstream_metadata="{}",
                           credit_multiplier=0.0))
        total += 1
    db.commit()
    return {"model_configs": total,
            "accounts": sum(count for _s, _r, count in ACCOUNT_FIXTURE),
            "specials": len(specials)}


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------
class Harness:
    """把 dispatch 中间件跑在「临时库 + 假上游」上，并逐请求统计。"""

    def __init__(self, models: int = 128, dump_sql: bool = False):
        self.models = models
        self.dump_sql = dump_sql
        self.patches: list = []
        self.seeded: dict = {}
        self._saved_registry: dict = {}
        self._seen_sql: dict = {key: set() for key in PATHS}

    # -- 装配 --
    def __enter__(self):
        prepare_environment()
        from admin.db import Base, SessionLocal, engine
        from admin.platform import dispatch, gateway_router, models_router, registry
        from admin.platform import model_suffix
        from admin.platform.registry import PlatformSpec

        if registry.DEFAULT_PLATFORM != DEFAULT_PLATFORM:
            raise RuntimeError("registry.DEFAULT_PLATFORM 变了（%r），请同步本脚本"
                               % (registry.DEFAULT_PLATFORM,))
        self.engine = engine
        self.dispatch = dispatch
        self.gateway_router = gateway_router
        self.models_router = models_router
        self.registry = registry
        self.model_suffix = model_suffix
        self.counter = _SqlCounter(engine)
        self.calls = _CallCounter()

        Base.metadata.create_all(bind=engine)
        with SessionLocal() as db:
            self.seeded = _seed_models(db, self.models)

        # 只留 4 个固定平台：装配真实平台包会让数字随仓库状态漂移
        self._saved_registry = dict(registry._REGISTRY)
        registry._REGISTRY.clear()
        for pid, label, storage, realm in PLATFORM_FIXTURE:
            registry.register(PlatformSpec(
                id=pid, label=label,
                storage_platform=storage, realm=realm), replace=True)

        self.patches = [
            mock.patch("admin.platform.loader.load_platforms",
                       lambda: {"loaded": [], "failed": {}}),
            mock.patch.object(dispatch, "_key_of", lambda request: _KEY),
            mock.patch.object(dispatch, "_spec_with_gateway",
                              lambda pid, realm="": _fixture_spec(registry, pid, realm)),
            mock.patch.object(dispatch, "_module_of",
                              lambda spec: _GATEWAYS.get(getattr(spec, "id", ""))),
            mock.patch.dict(sys.modules, {
                "admin.traffic_stats": SimpleNamespace(
                    annotate=lambda *a, **k: None,
                    mark_stage=lambda *a, **k: None,
                    start=lambda *a, **k: "rid-bench"),
                "admin.request_metrics": SimpleNamespace(
                    current=SimpleNamespace(get=lambda: None)),
            }),
        ]
        for patch in self.patches:
            patch.start()
        model_suffix.invalidate()
        models_router.invalidate()
        self.counter.__enter__()
        self.calls.__enter__(gateway_router)
        self.middleware = dispatch.PlatformDispatchMiddleware(_main_app)
        return self

    def __exit__(self, *exc):
        self.calls.__exit__(None, None, None)
        self.counter.__exit__(None, None, None)
        for patch in reversed(self.patches):
            patch.stop()
        self.registry._REGISTRY.clear()
        self.registry._REGISTRY.update(self._saved_registry)
        return False

    # -- 缓存复位（「冷启动」的定义）--
    def reset_caches(self) -> None:
        dispatch = self.dispatch
        dispatch.invalidate_model_cache()
        dispatch._order_cache.update({"at": 0.0, "value": None, "signature": ""})
        dispatch._disabled_cache.update({"at": 0.0, "value": None})
        dispatch._health_cache.clear()
        self.gateway_router._cache.update({"at": 0.0, "value": None, "generation": 0})
        self.gateway_router._generation += 1
        self.models_router.invalidate()
        self.model_suffix.invalidate()

    # -- 单次请求 --
    async def request(self, path_key: str) -> dict:
        spec = PATHS[path_key]
        _PLAN["fail_platform"] = spec.get("fail_first", "")
        _PLAN["fail_once"] = bool(spec.get("fail_first"))
        _PLAN["trace"].clear()
        body = json.dumps({"model": spec["model"],
                           "messages": [{"role": "user", "content": "ping"}]
                           }).encode()
        scope = {
            "type": "http", "http_version": "1.1", "method": "POST",
            "path": CHAT_PATH, "raw_path": CHAT_PATH.encode(),
            "query_string": b"", "scheme": "http",
            "server": ("bench", 80), "client": ("127.0.0.1", 1),
            "headers": [(b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode())],
        }
        state = {"sent": False}

        async def receive():
            if state["sent"]:
                return {"type": "http.disconnect"}
            state["sent"] = True
            return {"type": "http.request", "body": body, "more_body": False}

        collected: list = []

        async def send(message):
            collected.append(message)

        self.counter.reset()
        self.calls.reset()
        started = time.perf_counter()
        await self.middleware(scope, receive, send)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        status = None
        for message in collected:
            if message.get("type") == "http.response.start":
                status = int(message.get("status") or 0)
        stats = _sql_stats(self.counter.statements)
        if self.dump_sql:
            self._seen_sql[path_key].update(
                s for s in self.counter.statements
                if _CFG_TABLE in str(s).lower())
        stats.update({"ms": elapsed_ms, "status": status,
                      "rank_calls": self.calls.order,
                      "health_calls": self.calls.health,
                      "trace": list(_PLAN["trace"])})
        return stats

    # -- 冷 / 热 --
    async def measure(self, warm: int = 100) -> dict:
        result: dict = {}
        for path_key in PATHS:
            self.reset_caches()
            cold = await self.request(path_key)
            self.reset_caches()
            await self.request(path_key)        # 预热一次，让缓存进入稳态
            samples = [await self.request(path_key) for _ in range(warm)]
            times = [s["ms"] for s in samples]
            warm_stats = {
                "requests": len(samples),
                "sql_per_request": sum(s["sql"] for s in samples) / len(samples),
                "cfg_statements_per_request":
                    sum(s["cfg_statements"] for s in samples) / len(samples),
                "cfg_statements_total":
                    sum(s["cfg_statements"] for s in samples),
                "full_scans_per_request":
                    sum(s["full_scans"] for s in samples) / len(samples),
                "full_scans_total": sum(s["full_scans"] for s in samples),
                "rank_calls_per_request":
                    sum(s["rank_calls"] for s in samples) / len(samples),
                "health_calls_per_request":
                    sum(s["health_calls"] for s in samples) / len(samples),
                "p50_ms": _percentile(times, 0.50),
                "p95_ms": _percentile(times, 0.95),
                "max_ms": max(times),
                "statuses": sorted({s["status"] for s in samples}),
                "trace_sample": samples[-1]["trace"],
            }
            result[path_key] = {"cold": cold, "warm": warm_stats,
                                "model": PATHS[path_key]["model"]}
        return result


def run(models: int = 128, warm: int = 100, dump_sql: bool = False) -> dict:
    """跑完全部路径，返回可直接 JSON 序列化的结果。"""
    saved_disable = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    engine = None
    samples: dict = {}
    try:
        with Harness(models, dump_sql=dump_sql) as harness:
            paths = asyncio.run(harness.measure(warm))
            seeded = harness.seeded
            engine = harness.engine
            if dump_sql:
                samples = {key: sorted(rows)
                           for key, rows in harness._seen_sql.items()}
    finally:
        logging.disable(saved_disable)
        if engine is not None:
            _cleanup(engine)
    return {"meta": {"models": seeded["model_configs"],
                     "accounts": seeded["accounts"],
                     "platforms": [spec[0] for spec in PLATFORM_FIXTURE],
                     "warm": warm,
                     "python": sys.version.split()[0]},
            "paths": paths,
            "sql_samples": samples}


def _render(report: dict) -> str:
    lines = []
    meta = report["meta"]
    lines.append("dispatch 热路径基线（本地假上游，无公网）")
    lines.append("  平台：%s" % " / ".join(meta["platforms"]))
    lines.append("  种子：model_configs=%d，accounts=%d；预热后 %d 次/路径"
                 % (meta["models"], meta["accounts"], meta["warm"]))
    lines.append("")
    lines.append("%-16s %-5s %9s %11s %10s %9s %11s %8s %8s %6s"
                 % ("路径", "样本", "SQL/请求", "cfg语句/请求", "真全表/请求",
                    "排名/请求", "健康度/请求", "p50(ms)", "p95(ms)", "状态"))
    for key, entry in report["paths"].items():
        cold = entry["cold"]
        lines.append("%-16s %-5s %9d %11d %10d %9d %11d %8.2f %8.2f %6s"
                     % (key, "cold", cold["sql"], cold["cfg_statements"],
                        cold["full_scans"], cold["rank_calls"],
                        cold["health_calls"], cold["ms"], cold["ms"],
                        cold["status"]))
        warm = entry["warm"]
        lines.append("%-16s %-5s %9.2f %11.2f %10.2f %9.2f %11.2f %8.3f %8.3f %6s"
                     % ("", "warm", warm["sql_per_request"],
                        warm["cfg_statements_per_request"],
                        warm["full_scans_per_request"],
                        warm["rank_calls_per_request"],
                        warm["health_calls_per_request"],
                        warm["p50_ms"], warm["p95_ms"],
                        ",".join(str(s) for s in warm["statuses"])))
    lines.append("")
    for key, entry in report["paths"].items():
        lines.append("  %-16s 冷启动落点=%s 预热后落点=%s"
                     % (key, "->".join(entry["cold"]["trace"]),
                        "->".join(entry["warm"]["trace_sample"])))
    lines.append("")
    lines.append("预热后（两个指标分开，别混成一个数）：")
    lines.append("  cfg 语句/请求：%s"
                 % ", ".join("%s=%.2f" % (k, v["warm"]["cfg_statements_per_request"])
                             for k, v in report["paths"].items()))
    lines.append("  真·全表读取/请求（无任何 WHERE）：%s"
                 % ", ".join("%s=%.2f" % (k, v["warm"]["full_scans_per_request"])
                             for k, v in report["paths"].items()))
    total_full = sum(v["warm"]["full_scans_per_request"]
                     for v in report["paths"].values())
    lines.append("  四条路径合计：%.2f 次真·全表读取/请求（计划 §七 第 91 行目标：0）"
                 % (total_full / max(1, len(report["paths"]))))
    samples = report.get("sql_samples") or {}
    if samples:
        lines.append("")
        lines.append("涉及 model_configs 的 SQL 原文与判定（--dump-sql）：")
        for key, rows in samples.items():
            lines.append("  [%s]" % key)
            for statement in rows:
                flat = " ".join(str(statement).split())
                lines.append("    full_scan=%s | %s"
                             % (_is_full_table_scan(statement),
                                flat[:200] + ("…" if len(flat) > 200 else "")))
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="dispatch 热路径度量（离线）")
    parser.add_argument("--models", type=int, default=128,
                        help="model_configs 行数（默认 128）")
    parser.add_argument("--warm", type=int, default=100,
                        help="预热后重复请求数（默认 100）")
    parser.add_argument("--json", action="store_true",
                        help="只输出 JSON（供 pytest 断言用）")
    parser.add_argument("--out", default="",
                        help="把 JSON 结果写成 UTF-8 文件（绕开控制台编码）")
    parser.add_argument("--dump-sql", action="store_true",
                        help="额外打印涉及 model_configs 的 SQL 原文与全表判定")
    args = parser.parse_args(argv)

    report = run(models=args.models, warm=args.warm, dump_sql=args.dump_sql)
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        Path(args.out).write_text(payload, encoding="utf-8")
    if args.json:
        # 显式写 UTF-8 字节：管道里的 stdout 编码在 Windows 上不可靠
        sys.stdout.buffer.write(payload.encode("utf-8"))
        sys.stdout.buffer.flush()
    else:
        print(_render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
