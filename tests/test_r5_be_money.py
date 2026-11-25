# -*- coding: utf-8 -*-
"""第 5 轮 r5_be：MySQL DECIMAL 金额列的读出边界（第 4 轮 A4）。

用 `Decimal` 构造「MySQL 驱动的返回值」，覆盖 helper、聚合表达式的结果类型、
Key 限额、Key 用量、统计缓存、IP 活跃度的 avg。

运行：python -m pytest tests/test_r5_be_money.py -q
"""
from __future__ import annotations

import json
import os
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(ROOT))

from admin import money  # noqa: E402


# ---- 纯函数 ----

def test_as_float_variants():
    assert money.as_float(Decimal("1.234567")) == pytest.approx(1.234567)
    assert isinstance(money.as_float(Decimal("2")), float)
    assert money.as_float(None) == 0.0 and money.as_float(None, 5.0) == 5.0
    assert money.as_float("x") == 0.0 and money.as_float("2.5") == 2.5
    assert money.as_float(float("nan")) == 0.0 and money.as_float(Decimal("NaN")) == 0.0
    assert money.as_float(float("inf"), -1.0) == -1.0
    # Decimal + float 本来会 TypeError，经 as_float 后正常
    with pytest.raises(TypeError):
        Decimal("0.25") + 0.5  # noqa: B018
    assert 0.5 + money.as_float(Decimal("0.25")) == 0.75


def test_money_round_and_floatify_and_json():
    value = money.money_round(Decimal("1.23456"), 2)
    assert value == 1.23 and isinstance(value, float)
    out = money.floatify({"a": Decimal("1.5"), "b": [Decimal("2"), {"c": (Decimal("3.25"), "s")}],
                          "n": 1, "s": "t", "none": None})
    assert out == {"a": 1.5, "b": [2.0, {"c": (3.25, "s")}], "n": 1, "s": "t", "none": None}
    assert type(out["a"]) is float and type(out["n"]) is int
    text = json.dumps({"x": Decimal("0.1"), "o": object()}, default=money.json_default)
    data = json.loads(text)
    assert data["x"] == 0.1 and isinstance(data["o"], str)


# ---- SQLAlchemy 表达式在 MySQL 方言下的结果处理器 ----

sa = pytest.importorskip("sqlalchemy")


def _mysql_processor(expr):
    from sqlalchemy.dialects import mysql

    dialect = mysql.dialect()
    impl = expr.type.dialect_impl(dialect)
    return impl.result_processor(dialect, None)


def _converts_to_float(expr) -> bool:
    proc = _mysql_processor(expr)
    if proc is None:
        return False
    out = proc(Decimal("1.250000"))
    return isinstance(out, float) and out == 1.25


def test_typed_aggregates_return_float_on_mysql():
    from sqlalchemy import case, func

    from admin.models import ApiKey, UsageLog

    ok = UsageLog.error_kind.in_(("", "success"))
    exprs = [
        UsageLog.credits,
        ApiKey.credit_used,
        func.sum(UsageLog.credits),
        func.coalesce(func.sum(UsageLog.credits), 0.0),
        func.coalesce(func.sum(case((ok, UsageLog.credits), else_=0)), 0),
        func.sum(case((ok, UsageLog.credits), else_=0.0)),
        func.max(UsageLog.credits),
        func.coalesce(ApiKey.credit_used, 0),
        money.money_avg(UsageLog.credits),
    ]
    for expr in exprs:
        assert _converts_to_float(expr), str(expr)


def test_plain_avg_loses_type_and_money_avg_fixes_it():
    from sqlalchemy import func

    from admin.models import UsageLog

    assert _converts_to_float(money.money_avg(UsageLog.credits))
    if _converts_to_float(func.avg(UsageLog.credits)):
        pytest.skip("当前 SQLAlchemy 版本已给 avg 推断类型")


def test_ip_activity_uses_money_avg():
    src = (ROOT / "admin/routers/ip_management.py").read_text(encoding="utf-8")
    assert "money_avg(UsageLog.credits)" in src
    assert "func.avg(UsageLog.credits)" not in src


# ---- Key 限额 / Key 用量 ----

def _key(**kw):
    base = dict(status="active", unlimited=False, credit_used=Decimal("0"), credit_limit=Decimal("10"))
    base.update(kw)
    return SimpleNamespace(**base)


def test_check_quota_with_decimal_and_null():
    pytest.importorskip("fastapi")
    from fastapi import HTTPException

    from admin.security import check_quota

    check_quota(_key(credit_used=Decimal("9.999999")))
    with pytest.raises(HTTPException) as exc:
        check_quota(_key(credit_used=Decimal("10.000000")))
    assert exc.value.status_code == 402
    # NULL 以前 `None >= x` TypeError → 500；现在按 0 处理
    check_quota(_key(credit_used=None, credit_limit=Decimal("1")))
    with pytest.raises(HTTPException):
        check_quota(_key(credit_used=Decimal("0"), credit_limit=None))


def test_key_usage_decimal_payload_is_float():
    pytest.importorskip("fastapi")
    from admin.routers import keys

    row = SimpleNamespace(id=1, name="k", credit_limit=Decimal("100.000000"),
                          credit_used=Decimal("12.345678"), unlimited=False,
                          status="active", created_at=None)

    class _Q:
        def filter(self, *a, **k):
            return self

        def first(self):
            return row

    db = SimpleNamespace(query=lambda *a, **k: _Q())
    out = keys.key_usage(1, True, db)
    assert out["credit_used"] == 12.35 and type(out["credit_used"]) is float
    assert type(out["credit_limit"]) is float
    assert out["credit_remaining"] == 87.65 and out["usage_percent"] == 12.3
    json.dumps(out)  # 不含 Decimal
    row.credit_limit = None
    out = keys.key_usage(1, True, db)
    assert out["usage_percent"] is None


# ---- 统计缓存 ----

def test_stats_cache_normalizes_decimal(monkeypatch):
    from admin import stats_cache

    stored = {}

    class _Redis:
        def setex(self, key, ttl, payload):
            stored[key] = payload

    monkeypatch.setattr(stats_cache, "_rclient", lambda: _Redis())
    key = ("r5", "decimal-test")
    stats_cache.cache_put(key, {"credits": Decimal("1.5"), "rows": [{"c": Decimal("2.25")}]}, 30)
    payload = json.loads(next(iter(stored.values())))
    assert payload == {"credits": 1.5, "rows": [{"c": 2.25}]}  # 以前 default=str → "1.5"
    hit = stats_cache.cache_get(key)
    assert type(hit["credits"]) is float

    key2 = ("r5", "decimal-cached")
    value = stats_cache.cached(key2, lambda: {"credits": Decimal("3")}, 30)
    assert value == {"credits": 3.0} and type(value["credits"]) is float


# ---- 其它静态检查 ----

def _code_only(path: Path) -> str:
    """只取非注释行。

    这几处写法在文件里被**注释引用**过（说明「原来是 Python or，改成 SQL
    coalesce」），直接对全文做子串断言会把注释当成违规代码。
    """
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        code = raw.split("#", 1)[0]
        if code.strip():
            lines.append(code)
    return "\n".join(lines)


def test_videos_credit_used_coalesce():
    src = _code_only(ROOT / "admin/routers/videos.py")
    assert "(ApiKey.credit_used or 0)" not in src
    assert "func.coalesce(ApiKey.credit_used, 0) + abs(credits)" in src


def test_proxy_real_credit_delta_uses_as_float():
    src = (ROOT / "admin/routers/proxy.py").read_text(encoding="utf-8")
    assert "delta = real - log.credits" not in src
    assert "delta = real - as_float(log.credits)" in src
