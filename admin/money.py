"""金额读出边界的统一转换（2026-11-24 第 5 轮 r5_be，接第 4 轮 A4）。

背景：MySQL 上金额列是 `DECIMAL(18,6)`（`models.Money()`，`asdecimal=False`）。
ORM 列、`func.sum/coalesce/max/min(列)`、`case((…, 列))` 会沿用列类型，结果是 float；
但**丢失列类型**的表达式（`func.avg(列)`、`func.round(...)`、`literal_column`、
`text()` 原生 SQL、部分算术表达式）在 MySQL 上回的是 `decimal.Decimal`：

  * `Decimal + float` → TypeError（看板 / 统计里常见的累加）；
  * `json.dumps(Decimal)` → TypeError；`default=str` 兜底则变成字符串 "1.23"，
    前端拿到字符串、二次读缓存再做加法就炸；
  * `round(Decimal, n)` 仍是 Decimal。

本模块提供：
  * `as_float(v, default=0.0)`：None / Decimal / int / 数字字符串 → float；NaN/Inf/非法 → default；
  * `money_round(v, n)`：先 as_float 再 round；
  * `floatify(obj)`：递归把 dict / list / tuple 里的 Decimal 转 float（其余原样）；
  * `json_default(o)`：给 `json.dumps(default=...)` 用，Decimal → float，其余 → str；
  * `money_avg(col)`：`func.avg(col)` 但带 Money 结果类型（MySQL 上回 float）。
"""
from __future__ import annotations

import math
from decimal import Decimal
from typing import Any


def as_float(value: Any, default: float = 0.0) -> float:
    if value is None or isinstance(value, bool):
        return default if value is None else float(value)
    try:
        out = float(value)
    except (TypeError, ValueError, ArithmeticError):
        return default
    if math.isnan(out) or math.isinf(out):
        return default
    return out


def money_round(value: Any, ndigits: int = 4, default: float = 0.0) -> float:
    return round(as_float(value, default), ndigits)


def floatify(obj: Any, _depth: int = 0) -> Any:
    if isinstance(obj, Decimal):
        return as_float(obj)
    if _depth > 16:
        return obj
    if isinstance(obj, dict):
        return {k: floatify(v, _depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        return [floatify(v, _depth + 1) for v in obj]
    if isinstance(obj, tuple):
        return tuple(floatify(v, _depth + 1) for v in obj)
    return obj


def json_default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return as_float(obj)
    return str(obj)


def money_avg(column):
    """`AVG(金额列)`，结果类型标成 Money（MySQL 上 DECIMAL → float）。"""
    from sqlalchemy import func

    from admin.models import Money

    return func.avg(column, type_=Money())
