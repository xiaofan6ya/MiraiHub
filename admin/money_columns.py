"""MySQL 金额列 FLOAT -> DECIMAL(18,6) 的一次性迁移（修复批次 3 · D8，审查 B-3）。

## 为什么

SQLAlchemy `Float()` 在 MySQL 上是 4 字节单精度 `FLOAT`（约 7 位有效数字）：
`api_keys.credit_used` 累计到 20 万左右时，一次 0.01 积分的扣费会被舍掉，
Key 限额永远到不了；汇总表的 `credits` 也会出现肉眼可见的误差。
ORM 已改为 `models.Money()`（MySQL 上是 `DECIMAL(18,6)`），但 `create_all`
不会改已存在的列，老库需要执行一次 `ALTER TABLE ... MODIFY COLUMN`。

## 幂等 / 安全

  * 只在 MySQL / MariaDB 上执行；SQLite（REAL 本来就是双精度）直接跳过。
  * 先查 `INFORMATION_SCHEMA.COLUMNS`：已经是 DECIMAL 的列跳过，重复启动零成本。
  * 改之前查 `MAX(ABS(col))`：超出 DECIMAL(18,6) 上限（约 1e12）的列**不改**，
    只打警告（避免严格模式下 ALTER 失败或数据被截断）。
  * 单列失败只记日志，不影响其它列、不影响启动。

## 大表

`usage_logs`（几十万行起）的 ALTER 需要整表拷贝，会锁写入几秒到几分钟，
**默认不自动迁移**。由环境变量控制：

  * `ADMIN_MONEY_DECIMAL_MIGRATION` 未设置 / `1` / `small`：迁移除 `usage_logs` 以外的列
    （`api_keys` / `model_configs` / `usage_rollups` / `video_tasks` / `agent_assets`）
  * `all`：连 `usage_logs.credits` 一起迁移（请放在低峰期重启时设置）
  * `0`：全部跳过

## 接入

`admin/db.py` 由性能工作流负责，本模块只提供函数；需要在 `init_db()._do()` 末尾
（`_repair_negative_key_credits()` 之后）加一行：

    from admin.money_columns import migrate_money_columns; migrate_money_columns(engine)
"""
from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger("admin.money_columns")

#: DECIMAL(18,6) 能存的最大绝对值（整数部分 12 位）
DECIMAL_LIMIT = 999_999_999_999.0
TARGET_TYPE = "DECIMAL(18,6)"

#: (表, 列, 可空, 默认值) —— 与 models.py 中改为 Money() 的列一一对应
MONEY_COLUMNS: tuple[tuple[str, str, bool, str], ...] = (
    ("api_keys", "credit_limit", True, "0"),
    ("api_keys", "credit_used", True, "0"),
    ("model_configs", "credit_multiplier", True, "0"),
    ("usage_rollups", "credits", False, "0"),
    ("video_tasks", "credits", True, "0"),
    ("agent_assets", "credits", True, "0"),
    ("usage_logs", "credits", True, "0"),
)
#: 默认不自动迁移的大表
LARGE_TABLES = frozenset({"usage_logs"})

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _ident(name: str) -> str:
    if not _IDENT_RE.match(name):
        raise ValueError("非法 SQL 标识符: %r" % (name,))
    return name


def migration_mode() -> str:
    """`small`（默认）/ `all` / `off`。"""
    raw = os.getenv("ADMIN_MONEY_DECIMAL_MIGRATION", "").strip().lower()
    if raw in ("0", "off", "false", "no", "none"):
        return "off"
    if raw == "all":
        return "all"
    return "small"


def planned_columns(mode: str | None = None) -> list[tuple[str, str, bool, str]]:
    """按模式要迁移的列（纯函数，便于单测）。"""
    mode = mode or migration_mode()
    if mode == "off":
        return []
    return [c for c in MONEY_COLUMNS if mode == "all" or c[0] not in LARGE_TABLES]


def needs_change(data_type: str | None) -> bool:
    """INFORMATION_SCHEMA 的 DATA_TYPE 是否还是浮点（需要迁移）。"""
    return str(data_type or "").strip().lower() in ("float", "double", "real")


def alter_sql(table: str, column: str, nullable: bool, default: str) -> str:
    null_sql = "NULL" if nullable else "NOT NULL"
    default_sql = " DEFAULT %s" % default if default != "" else ""
    return "ALTER TABLE `%s` MODIFY COLUMN `%s` %s %s%s" % (
        _ident(table), _ident(column), TARGET_TYPE, null_sql, default_sql)


def migrate_money_columns(engine=None) -> dict:
    """执行迁移；返回 `{"dialect", "mode", "changed", "skipped", "errors"}`。绝不抛异常。"""
    result = {"dialect": "", "mode": migration_mode(), "changed": [], "skipped": [], "errors": []}
    try:
        if engine is None:
            from admin.db import engine as default_engine

            engine = default_engine
        dialect = str(engine.dialect.name or "").lower()
        result["dialect"] = dialect
        if dialect not in ("mysql", "mariadb"):
            return result
        columns = planned_columns(result["mode"])
        if not columns:
            return result
        from sqlalchemy import text

        for table, column, nullable, default in columns:
            label = "%s.%s" % (table, column)
            try:
                with engine.connect() as conn:
                    row = conn.execute(text(
                        "SELECT DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS "
                        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = :c"),
                        {"t": table, "c": column}).first()
                    if row is None:
                        result["skipped"].append(label + "（列不存在）")
                        continue
                    if not needs_change(row[0]):
                        continue
                    peak = conn.execute(text(
                        "SELECT MAX(ABS(`%s`)) FROM `%s`" % (_ident(column), _ident(table)))).scalar()
                    if peak is not None and float(peak) > DECIMAL_LIMIT:
                        logger.warning("金额列 %s 存在超出 DECIMAL(18,6) 的值（%s），跳过迁移", label, peak)
                        result["skipped"].append(label + "（数值超限）")
                        continue
                    logger.info("迁移金额列 %s：%s -> %s", label, row[0], TARGET_TYPE)
                    conn.execute(text(alter_sql(table, column, nullable, default)))
                    conn.commit()
                    result["changed"].append(label)
            except Exception as exc:  # noqa: BLE001 - 单列失败不影响其它列与启动
                logger.warning("金额列 %s 迁移失败（不影响启动）：%s", label, exc)
                result["errors"].append("%s: %s" % (label, str(exc)[:160]))
        if result["mode"] != "all":
            result["skipped"].extend("%s.%s（大表，需 ADMIN_MONEY_DECIMAL_MIGRATION=all）" % (t, c)
                                     for t, c, _n, _d in MONEY_COLUMNS if t in LARGE_TABLES)
        if result["changed"]:
            logger.warning("已把 %d 个 MySQL 金额列迁移为 %s：%s", len(result["changed"]),
                           TARGET_TYPE, "、".join(result["changed"]))
    except Exception as exc:  # noqa: BLE001
        logger.warning("金额列迁移整体失败（不影响启动）：%s", exc)
        result["errors"].append(str(exc)[:200])
    return result
