"""`usage_logs` 的小时级预聚合（查询加速层）。

## 为什么

数据看板 / 模型广场要的都是「窗口内按模型、按平台、按小时」的统计。直接扫原表，
30 天窗口在本机（MySQL, 20 万行）实测 8.6 秒，2 核 2G 的机器上更慢 —— 首屏打不开。
按小时预聚合之后同样的窗口只读 `小时数 × 模型数` 行（30 天 × 8 模型 ≈ 5.7k 行），
毫秒级。

## 一致性策略

* **写入**：每个请求落库 `usage_logs` 之后，对 `(hour, platform, model)` 做一次
  原子 upsert（`ON DUPLICATE KEY UPDATE` / `ON CONFLICT DO UPDATE`）。多一次
  单行写，换统计页两个数量级的提速。
* **回填**：`ensure_coverage()` 只补缺口（按 `usage_rollup_state` 的水位），
  同时有**后台线程**在首次读取时补历史，读请求永远不会被回填拖住。
* **读取**：`segments()` 把窗口切成「汇总表覆盖的整点段」和「剩下的原表段」，
  两段分别聚合再合并。当前这一小时（还没走完）永远走原表，所以数字不会滞后。
* **口径**：与 `usage_logs` 完全一致 —— 请求数含失败；Token/积分/延迟只算成功
  （`error_kind in ('', 'success')`）。`*_sum` + `*_count` 用来还原平均值，
  跨小时/跨模型合并时才不会算错。

## 兜底

任何一步失败（表不存在、upsert 冲突、回填异常）都只降级成「走原表」，
不影响记账，也不影响请求返回。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta

from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import Session

from admin.models import UsageLog, UsageRollup, UsageRollupState

logger = logging.getLogger("admin.rollup")

OK_KINDS = ("", "success")

#: 回填每批处理多少小时：48 小时 ≈ 2 天，单批在 20 万行的库上不到 1 秒。
_BACKFILL_CHUNK_HOURS = 48
#: 单次 `ensure_coverage` 的最长时间预算（秒）。超了就留给后台线程。
_BACKFILL_BUDGET = 2.0

#: 可重入锁：读路径 `ensure_coverage(blocking=False)` 拿到锁后会一路持有到结束，
#: 中途 `ensure_schema -> rebuild` 还要再拿一次（同线程），普通 Lock 会自锁。
_lock = threading.RLock()
_thread = None
_schema_ready = False


def ensure_schema(db: Session) -> None:
    """老库补列：`tps_sum` / `tps_count` 是 2026-11-12 新加的。

    已有的汇总行里没有逐请求 t/s，补列后必须清空重算，否则看板会一直显示 0。
    清空只是丢一次回填的活，后台线程会按新口径重建（前台先走原表）。
    """
    global _schema_ready
    if _schema_ready:
        return
    from sqlalchemy import inspect as _inspect

    engine = db.get_bind()
    names = set(_inspect(engine).get_table_names())
    if "usage_rollups" not in names:
        _schema_ready = True
        return
    columns = {column["name"] for column in _inspect(engine).get_columns("usage_rollups")}
    if "tps_sum" not in columns:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "ALTER TABLE usage_rollups ADD COLUMN tps_sum FLOAT NOT NULL DEFAULT 0")
            connection.exec_driver_sql(
                "ALTER TABLE usage_rollups ADD COLUMN tps_count BIGINT NOT NULL DEFAULT 0")
            connection.exec_driver_sql("DELETE FROM usage_rollups")
            try:
                connection.exec_driver_sql(
                    "UPDATE usage_rollup_state SET covered_from=NULL, covered_to=NULL")
            except Exception:  # noqa: BLE001 - 状态表缺失时由下次回填自建
                pass
        logger.info("usage_rollups 已补 tps 列，历史汇总已清空等待重算")
    # 一次性重建（N1）：修 count 之前写进汇总表的行 count 恒为 1，必须重算。
    # 标记落在 system_settings，所以每个库只重建一次；失败下次启动再试。
    try:
        if not _marker_done(db, REBUILD_MARKER):
            rebuild(db, reason="count 列类型修复（N1）一次性重建")
            _mark_done(db, REBUILD_MARKER)
    except Exception as exc:  # noqa: BLE001 - 重建失败只影响看板精度，不影响启动
        logger.warning("usage_rollups 一次性重建失败（下次启动重试）：%s", exc)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
    _schema_ready = True


#: 一次性重建的标记（`system_settings.key`）。版本号变了就会再重建一次。
#: v2（2026-11-24 numbers 线）：在 count 修复之外，首字口径改为「只认流式样本」（N2），
#: 汇总里旧口径的 ttfb_sum / ttfb_count 也必须重算。v1 从未上线，直接跳到 v2。
REBUILD_MARKER = "migrate:usage_rollups:count_fix_v2"


def _marker_done(db: Session, key: str) -> bool:
    from admin.models import SystemSetting

    row = db.get(SystemSetting, key)
    return bool(row is not None and str(row.value or "") not in ("", "0"))


def _mark_done(db: Session, key: str) -> None:
    from admin.models import SystemSetting

    row = db.get(SystemSetting, key)
    if row is None:
        row = SystemSetting(key=key)
        db.add(row)
    row.value = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    db.commit()


def rebuild(db: Session, *, reason: str = "") -> dict:
    """重建汇总表（幂等）：清掉「原表还在」的那段汇总行，水位退回，让回填重算。

    * **只删 `hour >= 原表最早一条` 的行**：开了数据保留（retention）之后，更早的
      原表行已经删了，那段汇总行是唯一的历史，不能一起丢；
    * 水位退回到原表最早时刻，后台线程（或下一次 `ensure_coverage`）会按新口径
      重新聚合。重算期间读路径自动降级走原表，数字是对的，只是慢一点；
    * 多次调用结果一样（删的是可重算的部分），管理员可随时手动触发
      （`POST /api/stats/rollup/rebuild`）。
    """
    with _lock:
        earliest = _earliest_log(db)
        table = UsageRollup.__table__
        if earliest is None:
            deleted = db.execute(table.delete()).rowcount
        else:
            deleted = db.execute(table.delete().where(table.c.hour >= earliest)).rowcount
        older = db.query(func.min(UsageRollup.hour)).scalar()
        state = _state(db)
        if older is not None and earliest is not None:
            # 更早的汇总行保留：覆盖区间从它们开始，尾部从原表最早小时续算。
            state.covered_from = hour_start(older)
            state.covered_to = earliest
        else:
            state.covered_from = None
            state.covered_to = None
        db.commit()
    logger.info("usage_rollups 已重建：删除 %s 行，水位 %s ~ %s（%s）", deleted,
                state.covered_from, state.covered_to, reason or "手动")
    return {"deleted": int(deleted or 0),
            "covered_from": state.covered_from.isoformat() if state.covered_from else None,
            "covered_to": state.covered_to.isoformat() if state.covered_to else None}

#: 汇总字段（同时用于 upsert 的加减、以及两段结果的合并）。
_SUM_FIELDS = ("requests", "successes", "prompt_tokens", "completion_tokens",
               "total_tokens", "cached_tokens", "credits", "latency_sum",
               "latency_count", "ttfb_sum", "ttfb_count", "tps_sum", "tps_count")

#: 只有 credits / tps_sum 是浮点，其余全是整数计数。
_FLOAT_FIELDS = ("credits", "tps_sum")


def _request_tps(completion_tokens, latency_ms, ttfb_ms):
    """单请求生成速度，与日志页/`bench_router` 完全同口径。

    分母是**纯解码段**（latency − ttfb），不是整段耗时；0.3 秒以下视为
    没有有效分段，300 t/s 以上视为整包突发（都不计入）。

    `ttfb_ms is None`（非流式 / 没有首字时间）直接返回 None：SQL 侧 `_tps_expr`
    里 `latency_ms - ttfb_ms` 遇 NULL 传播、该行被排除，两边口径必须一致
    （以前按 `ttfb or 0` 会把「tokens / 总耗时」算进小时 tps_sum）。
    """
    if ttfb_ms is None:
        return None
    try:
        tokens = float(completion_tokens or 0)
        decode = float(latency_ms or 0) - float(ttfb_ms or 0)
    except (TypeError, ValueError):
        return None
    if tokens <= 0 or decode < 300:
        return None
    value = tokens * 1000.0 / decode
    return value if 0 < value <= 300 else None


def _coerce(name: str, value):
    """把 MySQL 的 `SUM()` 结果（`Decimal`）收敛成 int/float。

    MySQL 对 BIGINT/FLOAT 求 SUM 会返回 `Decimal`，而 `Decimal / float`
    在 Python 里直接 TypeError —— 接口就变成一个纯文本 500，
    前端 `JSON.parse("Internal Server Error")` 当场炸（2026-11-12 线上复现）。
    """
    if value is None:
        return 0.0 if name in _FLOAT_FIELDS else 0
    return float(value) if name in _FLOAT_FIELDS else int(value)


def hour_start(value: datetime) -> datetime:
    return value.replace(minute=0, second=0, microsecond=0)


def _empty() -> dict:
    return {name: 0 for name in _SUM_FIELDS}


def _add(target: dict, name: str, value) -> None:
    target[name] = target.get(name, 0) + (value or 0)


# ---------------------------------------------------------------------------
# 写入
# ---------------------------------------------------------------------------

def _upsert(db: Session, values: dict, *, replace: bool) -> None:
    """按方言选 upsert：SQLite 用 ON CONFLICT，MySQL 用 ON DUPLICATE KEY。"""
    table = UsageRollup.__table__
    keys = ("hour", "platform", "model")
    payload = {name: values.get(name, 0) for name in _SUM_FIELDS}
    payload.update({key: values[key] for key in keys})

    dialect = db.get_bind().dialect.name
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as _insert

        stmt = _insert(table).values(**payload)
        source = stmt.excluded
        update = {
            name: (source[name] if replace else table.c[name] + source[name])
            for name in _SUM_FIELDS
        }
        db.execute(stmt.on_conflict_do_update(index_elements=list(keys), set_=update))
    else:
        from sqlalchemy.dialects.mysql import insert as _insert

        stmt = _insert(table).values(**payload)
        source = stmt.inserted
        update = {
            name: (source[name] if replace else table.c[name] + source[name])
            for name in _SUM_FIELDS
        }
        db.execute(stmt.on_duplicate_key_update(**update))


def record(db: Session, *, platform: str, model: str, created_at: datetime,
           credits: float = 0, prompt_tokens=None, completion_tokens=None,
           total_tokens=None, cached_tokens=None, latency_ms=None,
           ttfb_ms=None, error_kind: str = "") -> None:
    """把一个已落库的请求累加进汇总表。**失败只记日志，绝不抛出。**

    在 `usage_logs.commit()` 之后调用（独立事务）。统计少一行可以接受，
    记账被统计拖垮不行。
    """
    try:
        success = 1 if (error_kind or "") in OK_KINDS else 0
        tps = _request_tps(completion_tokens, latency_ms, ttfb_ms) if success else None
        ttfb_ok = bool(success) and streamed_ttfb(ttfb_ms, latency_ms)
        values = {
            "hour": hour_start(created_at or datetime.utcnow()),
            "platform": (platform or "").lower(),
            "model": model or "",
            "requests": 1,
            "successes": success,
            "prompt_tokens": prompt_tokens or 0 if success else 0,
            "completion_tokens": completion_tokens or 0 if success else 0,
            "total_tokens": total_tokens or 0 if success else 0,
            "cached_tokens": cached_tokens or 0 if success else 0,
            "credits": float(credits or 0) if success else 0.0,
            "latency_sum": int(latency_ms or 0) if success else 0,
            "latency_count": 1 if (success and latency_ms is not None) else 0,
            "ttfb_sum": int(ttfb_ms or 0) if ttfb_ok else 0,
            "ttfb_count": 1 if ttfb_ok else 0,
            "tps_sum": float(tps) if tps else 0.0,
            "tps_count": 1 if tps else 0,
        }
        _upsert(db, values, replace=False)
        db.commit()
    except Exception as exc:  # noqa: BLE001 - 统计永远不能打断记账
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        logger.warning("用量汇总写入失败（已降级为原表统计）：%s", exc)


def adjust_credits(db: Session, *, platform: str, model: str,
                   created_at: datetime, delta: float) -> None:
    """上游真实积分回填后校正汇总行（估算值 → 实际值）。

    只动 `credits` 一列，其余字段保持原样；`delta = 实际 - 估算`，
    这样失败请求（原本记 0）也不会被误加。
    """
    if not delta:
        return
    try:
        values = {name: 0 for name in _SUM_FIELDS}
        values.update({
            "hour": hour_start(created_at or datetime.utcnow()),
            "platform": (platform or "").lower(),
            "model": model or "",
            "credits": float(delta),
        })
        _upsert(db, values, replace=False)
        db.commit()
    except Exception as exc:  # noqa: BLE001
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        logger.warning("用量汇总积分校正失败：%s", exc)


# ---------------------------------------------------------------------------
# 覆盖水位与回填
# ---------------------------------------------------------------------------

def _state(db: Session) -> UsageRollupState:
    row = db.get(UsageRollupState, 1)
    if row is None:
        row = UsageRollupState(id=1)
        db.add(row)
        db.commit()
    return row


def coverage(db: Session) -> tuple[datetime | None, datetime | None]:
    state = _state(db)
    return state.covered_from, state.covered_to


def _earliest_log(db: Session) -> datetime | None:
    value = db.query(func.min(UsageLog.created_at)).scalar()
    return hour_start(value) if value else None


#: `ADMIN_TTFB_STREAM_ONLY=0` 回到旧口径（只要 ttfb 非空就计入首字统计）。
#: 默认 1：只是统计口径，不影响记账；关掉只为对比新旧数字。
_TTFB_STREAM_ONLY = (os.getenv("ADMIN_TTFB_STREAM_ONLY", "1") or "1").strip().lower() not in (
    "0", "false", "no", "off")


def streamed_ttfb(ttfb_ms, latency_ms) -> bool:
    """这条请求的 ttfb 能不能算进「首字时间」统计（N2，纯函数）。

    非流式请求没有「首字」：网关现在把它写成 `ttfb = latency`（Marvis `_bill`、
    主应用 Responses / Anthropic 非流式），聚合模式甚至 ttfb ≥ latency；Marvis
    非流式还会先写 0。这些样本混进平均值，「平均首字」就变成「平均总耗时」。
    `usage_logs` 没有 is_stream 列，统计层用数据本身判断：

      * ttfb 为空 / ≤ 0 —— 不算；
      * latency 已知且 ttfb ≥ latency —— 视为非流式或口径失效，不算；
      * 其余（流式，ttfb < latency）—— 算。
    """
    if ttfb_ms is None:
        return False
    try:
        ttfb = int(ttfb_ms)
    except (TypeError, ValueError):
        return False
    if not _TTFB_STREAM_ONLY:
        return True
    if ttfb <= 0:
        return False
    if latency_ms is None:
        return True
    try:
        return ttfb < int(latency_ms)
    except (TypeError, ValueError):
        return True


def ttfb_valid_clause():
    """SQL 版 `streamed_ttfb`：首字统计（sum / count / avg）只认这些行。"""
    if not _TTFB_STREAM_ONLY:
        return UsageLog.ttfb_ms.isnot(None)
    return and_(UsageLog.ttfb_ms.isnot(None), UsageLog.ttfb_ms > 0,
                or_(UsageLog.latency_ms.is_(None), UsageLog.ttfb_ms < UsageLog.latency_ms))


# ---------------------------------------------------------------------------
# 统计用的「自然日」时区（N5）
# ---------------------------------------------------------------------------
#
# 概览 / 看板 / 模型限额的「今日」都按同一个本地时区切日。原来三处各写各的
# `+8`；现在统一从这里读，部署在别的时区时设一个环境变量就行：
#   * ADMIN_STATS_TZ_OFFSET_HOURS —— 相对 UTC 的小时数（默认 8 = 北京时间，可为小数）
#   * ADMIN_STATS_TZ_NAME         —— 只用于展示的名字（默认按偏移推断）
# 用固定偏移而不是 zoneinfo：Windows / 精简镜像上常常没有 tzdata，北京时间也没有夏令时。


def local_offset() -> timedelta:
    raw = (os.getenv("ADMIN_STATS_TZ_OFFSET_HOURS", "") or "").strip()
    try:
        hours = float(raw) if raw else 8.0
    except ValueError:
        hours = 8.0
    return timedelta(hours=max(-12.0, min(14.0, hours)))


def local_tz_name() -> str:
    name = (os.getenv("ADMIN_STATS_TZ_NAME", "") or "").strip()
    if name:
        return name
    hours = local_offset().total_seconds() / 3600.0
    if hours == 8:
        return "Asia/Shanghai"
    return "UTC%+g" % hours


def local_day_start(days_back: int = 0, now: datetime | None = None) -> datetime:
    """本地时区「今天 0 点」往前 `days_back` 天，换算成 UTC naive（与库里的值可比）。"""
    offset = local_offset()
    local = (now or datetime.utcnow()) + offset
    midnight = datetime(local.year, local.month, local.day) - timedelta(days=max(0, int(days_back)))
    return midnight - offset


def _tps_expr():
    """SQL 侧的逐请求 t/s（纯解码段），与 Python 版 `_request_tps` 同公式。"""
    decode = UsageLog.latency_ms - UsageLog.ttfb_ms
    return (func.coalesce(UsageLog.completion_tokens, 0) * 1000.0
            / func.nullif(decode, 0))


def _tps_ok():
    decode = UsageLog.latency_ms - UsageLog.ttfb_ms
    return and_(UsageLog.error_kind.in_(OK_KINDS),
                func.coalesce(UsageLog.completion_tokens, 0) > 0,
                decode >= 300, _tps_expr() <= 300)


def _raw_sum_columns():
    ok = UsageLog.error_kind.in_(OK_KINDS)
    tps_ok = _tps_ok()
    columns = {
        "requests": func.count(UsageLog.id),
        "successes": func.sum(case((ok, 1), else_=0)),
        "prompt_tokens": func.sum(case((ok, UsageLog.prompt_tokens), else_=0)),
        "completion_tokens": func.sum(case((ok, UsageLog.completion_tokens), else_=0)),
        "total_tokens": func.sum(case((ok, UsageLog.total_tokens), else_=0)),
        "cached_tokens": func.sum(case((ok, UsageLog.cached_tokens), else_=0)),
        "credits": func.sum(case((ok, UsageLog.credits), else_=0.0)),
        "latency_sum": func.sum(case((ok, UsageLog.latency_ms), else_=0)),
        # ⚠ count 列的 THEN 必须是**整数 1**，不能是 `X.isnot(None)`（2026-11-24 N1）。
        #
        # `case()` 的结果类型取自 THEN 分支：写成布尔表达式时整列类型是
        # Boolean，`func.sum` 原样继承。MySQL / SQLite 方言没有原生布尔，结果
        # 处理器会把 SQL 正确算出的 `SUM = 523` 转成 `True`，`_coerce` 再转成
        # **1** —— 每个（小时 × 平台 × 模型）桶的 count 恒为 1，而 sum 是完整的，
        # 看板 / 模型广场的平均耗时、首字时间被放大成「每桶请求数」倍（约 1000 倍）。
        "latency_count": func.sum(case((and_(ok, UsageLog.latency_ms.isnot(None)), 1),
                                       else_=0)),
        # 首字只认「流式样本」（N2）：非流式请求被网关写成 ttfb == latency，
        # 混进来会把平均首字拉成平均总耗时。条件见 `ttfb_valid_clause()`。
        "ttfb_sum": func.sum(case((and_(ok, ttfb_valid_clause()), UsageLog.ttfb_ms), else_=0)),
        "ttfb_count": func.sum(case((and_(ok, ttfb_valid_clause()), 1), else_=0)),
        "tps_sum": func.sum(case((tps_ok, _tps_expr()), else_=0.0)),
        "tps_count": func.sum(case((tps_ok, 1), else_=0)),
    }
    return [columns[name] for name in _SUM_FIELDS]


def _aggregate_raw(db: Session, start: datetime, end: datetime) -> list[dict]:
    """把 `[start, end)` 的原表行按 (hour, platform, model) 聚成汇总行。"""
    hour = func.date_format(UsageLog.created_at, "%Y-%m-%d %H:00") \
        if db.get_bind().dialect.name != "sqlite" \
        else func.strftime("%Y-%m-%d %H:00", UsageLog.created_at)
    rows = (
        db.query(hour.label("h"), UsageLog.platform, UsageLog.model, *_raw_sum_columns())
        .filter(UsageLog.created_at >= start, UsageLog.created_at < end)
        .group_by("h", UsageLog.platform, UsageLog.model)
        .all()
    )
    return [
        {"hour": datetime.strptime(row[0], "%Y-%m-%d %H:00"),
         "platform": (row[1] or "").lower(), "model": row[2] or "",
         **{name: _coerce(name, row[3 + index])
            for index, name in enumerate(_SUM_FIELDS)}}
        for row in rows
    ]


def ensure_coverage(db: Session, *, until: datetime | None = None,
                    budget: float = _BACKFILL_BUDGET,
                    blocking: bool = True) -> tuple[datetime | None, datetime | None]:
    """补齐 `usage_logs` 到汇总表的缺口。**只补缺口，不重扫全表。**

    返回补完后的覆盖区间（半开）。超过 `budget` 就停手：剩下的交给后台线程，
    读请求先用「已完成的部分走汇总、剩下的走原表」照样能出数。

    `blocking=False`（读路径用）：后台线程正握着锁回填时**不等**，直接返回现有
    覆盖区间 —— 原来读请求会被 30 秒预算的后台回填整段挡住（重启 / 刚重建后
    看板白屏半分钟）。
    """
    if not blocking:
        # 拿不到锁 = 后台线程正在回填：直接用现有水位。拿到了就**一直持有**到
        # 结束（RLock，下面 `with _lock` 同线程可重入）—— 原来 acquire 后立刻
        # release，再去阻塞式 `with _lock`，中间被后台线程抢走就照样等 30 秒。
        if not _lock.acquire(blocking=False):
            return coverage(db)
        try:
            return ensure_coverage(db, until=until, budget=budget, blocking=True)
        finally:
            _lock.release()
    deadline = time.monotonic() + max(0.0, budget)
    try:
        ensure_schema(db)
    except Exception as exc:  # noqa: BLE001 - 补列失败就继续走原表
        logger.warning("usage_rollups 补列失败：%s", exc)
    now = datetime.utcnow()
    # 只回填**已经走完**的整点：当前这一小时还在写，进汇总就会滞后一小时。
    target_to = hour_start(until or now)
    with _lock:
        state = _state(db)
        earliest = _earliest_log(db)
        if earliest is None:
            if state.covered_to is None:
                state.covered_to = target_to
                db.commit()
            return state.covered_from, state.covered_to

        # 1) 先补「尾部」（最近的缺口）—— 看板最常看的是今天。
        cursor = state.covered_to or earliest
        if state.covered_to is None:
            cursor = earliest
        while cursor < target_to and time.monotonic() < deadline:
            chunk_end = min(cursor + timedelta(hours=_BACKFILL_CHUNK_HOURS), target_to)
            for row in _aggregate_raw(db, cursor, chunk_end):
                _upsert(db, row, replace=True)
            state.covered_to = chunk_end
            if state.covered_from is None or state.covered_from > cursor:
                state.covered_from = cursor
            db.commit()
            cursor = chunk_end

        # 2) 再补「头部」（窗口比已有覆盖更早的那一段）。
        if state.covered_from is not None:
            start = state.covered_from
            while start > earliest and time.monotonic() < deadline:
                chunk_start = max(earliest, start - timedelta(hours=_BACKFILL_CHUNK_HOURS))
                for row in _aggregate_raw(db, chunk_start, start):
                    _upsert(db, row, replace=True)
                state.covered_from = chunk_start
                db.commit()
                start = chunk_start
        return state.covered_from, state.covered_to


def start_backfill_thread(*, interval: float = 300.0) -> None:
    """后台把历史补齐：首屏不必等回填，读路径按水位自动降级。"""
    global _thread
    if _thread is not None and _thread.is_alive():
        return

    def worker():
        from admin.db import SessionLocal
        while True:
            db = SessionLocal()
            try:
                ensure_coverage(db, budget=30.0)
            except Exception as exc:  # noqa: BLE001 - 后台任务不能把进程带崩
                logger.warning("用量汇总后台回填失败：%s", exc)
            finally:
                db.close()
            # 数据保留（默认关闭，ADMIN_RETENTION_ENABLED=1 打开）：每天最多一轮，
            # 只删已被汇总表覆盖的明细，见 admin/retention.py。顺带在这个线程里跑，
            # 不再多开一个常驻线程。
            try:
                from admin import retention
                retention.maybe_run()
            except Exception as exc:  # noqa: BLE001
                logger.warning("数据保留后台任务失败：%s", exc)
            time.sleep(interval)

    _thread = threading.Thread(target=worker, name="usage-rollup-backfill", daemon=True)
    _thread.start()


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------

def segments(start: datetime, end: datetime,
             covered: tuple[datetime | None, datetime | None]):
    """把窗口切成「走汇总表」与「走原表」的段（半开区间）。

    只有查询窗口和覆盖水位都包含的完整小时才用汇总表；
    窗口两端不足一小时的部分仍按原始时间戳查询。
    """
    covered_from, covered_to = covered
    out = []
    if covered_from is None or covered_to is None:
        return [("raw", start, end)]
    first_full_hour = hour_start(start)
    if first_full_hour < start:
        first_full_hour += timedelta(hours=1)
    roll_start = max(first_full_hour, covered_from)
    roll_end = min(hour_start(end), covered_to)
    if roll_start < roll_end:
        if start < roll_start:
            out.append(("raw", start, roll_start))
        out.append(("rollup", roll_start, roll_end))
        if roll_end < end:
            out.append(("raw", roll_end, end))
    else:
        out.append(("raw", start, end))
    return out


def _rollup_rows(db: Session, start: datetime, end: datetime) -> list:
    columns = [getattr(UsageRollup, name) for name in _SUM_FIELDS]
    return (
        db.query(UsageRollup.platform, UsageRollup.model,
                 *[func.coalesce(func.sum(column), 0) for column in columns])
        .filter(UsageRollup.hour >= start, UsageRollup.hour < end)
        .group_by(UsageRollup.platform, UsageRollup.model)
        .all()
    )


def _raw_rows(db: Session, start: datetime, end: datetime):
    return (
        db.query(UsageLog.platform, UsageLog.model, *_raw_sum_columns())
        .filter(UsageLog.created_at >= start, UsageLog.created_at < end)
        .group_by(UsageLog.platform, UsageLog.model)
        .all()
    )


def iter_buckets(db: Session, start: datetime, end: datetime,
                 covered: tuple[datetime | None, datetime | None] | None = None,
                 **filters):
    """逐行产出 `(hour, platform, model, 指标dict)`，供看板一次扫完算全部维度。

    看板同时要「按模型 / 按平台 / 按小时的堆叠序列」三个视图。分别查三次
    就是三次扫描；这里一次产出，调用方在同一个循环里累加三个视图 ——
    30 天窗口的扫描次数直接减半（2c2g 上这一步省 40~50%）。
    """
    if covered is None:
        covered = coverage(db)
    platform = (filters.get("platform") or "").strip().lower()
    model = (filters.get("model") or "").strip()

    for kind, seg_start, seg_end in segments(start, end, covered):
        if seg_start >= seg_end:
            continue
        if kind == "rollup":
            columns = [getattr(UsageRollup, name) for name in _SUM_FIELDS]
            query = (
                db.query(UsageRollup.hour, UsageRollup.platform, UsageRollup.model,
                         *[func.coalesce(func.sum(column), 0) for column in columns])
                .filter(UsageRollup.hour >= seg_start, UsageRollup.hour < seg_end)
                .group_by(UsageRollup.hour, UsageRollup.platform, UsageRollup.model)
            )
            if platform:
                query = query.filter(func.lower(UsageRollup.platform) == platform)
            if model:
                query = query.filter(UsageRollup.model == model)
            for row in query.all():
                yield (row[0], (row[1] or "").lower(), row[2] or "",
                       {name: _coerce(name, row[3 + index])
                        for index, name in enumerate(_SUM_FIELDS)})
        else:
            for row in _aggregate_raw(db, seg_start, seg_end):
                if platform and row["platform"] != platform:
                    continue
                if model and row["model"] != model:
                    continue
                yield (row["hour"], row["platform"], row["model"],
                       {name: row[name] for name in _SUM_FIELDS})


def window_rows(db: Session, start: datetime, end: datetime,
                covered: tuple[datetime | None, datetime | None] | None = None,
                **filters) -> dict[tuple[str, str], dict]:
    """窗口内按 (platform, model) 聚合。返回 `{(platform, model): 指标}`。

    `filters` 支持 `platform=` / `model=`（与看板筛选一致）；其余维度
    （Key / 账号 / 端点）没有进汇总表，调用方需要那些维度时请继续用原表。
    """
    if covered is None:
        covered = coverage(db)
    out: dict[tuple[str, str], dict] = {}

    def _merge(key, values) -> None:
        target = out.setdefault(key, _empty())
        for index, name in enumerate(_SUM_FIELDS):
            target[name] += _coerce(name, values[index])

    platform = (filters.get("platform") or "").strip().lower()
    model = (filters.get("model") or "").strip()

    for kind, seg_start, seg_end in segments(start, end, covered):
        if seg_start >= seg_end:
            continue
        if kind == "rollup":
            rows = _rollup_rows(db, seg_start, seg_end)
        else:
            rows = _raw_rows(db, seg_start, seg_end)
        for row in rows:
            key = ((row[0] or "").lower(), row[1] or "")
            if platform and key[0] != platform:
                continue
            if model and key[1] != model:
                continue
            _merge(key, row[2:])
    return out


def window_series(db: Session, start: datetime, end: datetime, granularity: str,
                  covered: tuple[datetime | None, datetime | None] | None = None,
                  **filters) -> list[dict]:
    """窗口内按 (桶, 模型) 的序列，供堆叠图使用。

    只做「原表 + 汇总表」两段合并，不做 Top N 截断：调用方还要用同一批值算排行。
    """
    if covered is None:
        covered = coverage(db)
    buckets: dict[tuple[str, str], dict] = {}

    def _key(dt: datetime) -> str:
        return dt.strftime("%Y-%m-%d %H:00" if granularity == "hour" else "%Y-%m-%d")

    def _add(key, values, dt) -> None:
        target = buckets.setdefault(key, {**_empty(), "hour": dt})
        for index, name in enumerate(_SUM_FIELDS):
            target[name] += _coerce(name, values[index])

    platform = (filters.get("platform") or "").strip().lower()
    model = (filters.get("model") or "").strip()

    for kind, seg_start, seg_end in segments(start, end, covered):
        if seg_start >= seg_end:
            continue
        if kind == "rollup":
            # 在 SQL 里先按 (小时, 模型) 聚合：30 天窗口原始行是
            # 「小时 × 平台 × 模型」≈ 2 万多行，聚合后只剩 5 千多行。
            # 别把明细 ORM 对象拉到 Python 里再循环 —— 2c2g 上那一步就要 2.5 秒。
            columns = [func.coalesce(func.sum(getattr(UsageRollup, name)), 0)
                       for name in _SUM_FIELDS]
            query = (
                db.query(UsageRollup.hour, UsageRollup.model, *columns)
                .filter(UsageRollup.hour >= seg_start, UsageRollup.hour < seg_end)
            )
            if platform:
                query = query.filter(func.lower(UsageRollup.platform) == platform)
            if model:
                query = query.filter(UsageRollup.model == model)
            for row in query.group_by(UsageRollup.hour, UsageRollup.model).all():
                _add((_key(row[0]), row[1] or ""), row[2:], row[0])
        else:
            hour = func.date_format(UsageLog.created_at, "%Y-%m-%d %H:00") \
                if db.get_bind().dialect.name != "sqlite" \
                else func.strftime("%Y-%m-%d %H:00", UsageLog.created_at)
            query = (
                db.query(hour.label("h"), UsageLog.model, *_raw_sum_columns())
                .filter(UsageLog.created_at >= seg_start, UsageLog.created_at < seg_end)
            )
            if platform:
                query = query.filter(func.lower(UsageLog.platform) == platform)
            if model:
                query = query.filter(UsageLog.model == model)
            for row in query.group_by("h", UsageLog.model).all():
                try:
                    dt = datetime.strptime(row[0], "%Y-%m-%d %H:00")
                except (TypeError, ValueError):
                    continue
                _add((_key(dt), row[1] or ""), row[2:], dt)

    return [
        {"bucket": key[0] if granularity == "hour" else key[0][:10],
         "model": key[1], **{name: value for name, value in item.items() if name != "hour"}}
        for key, item in buckets.items()
    ]

# ---------------------------------------------------------------------------
# 命令行：python -m admin.rollup --rebuild   （N1：手动重建汇总表，幂等）
# ---------------------------------------------------------------------------
def _main(argv=None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(description="usage_rollups 汇总表维护")
    parser.add_argument("--rebuild", action="store_true",
                        help="清掉原表仍在的那段汇总并退回水位（幂等，可重复执行）")
    parser.add_argument("--budget", type=float, default=120.0,
                        help="重建后立刻回填的时间预算（秒），剩下的交给服务里的后台线程")
    args = parser.parse_args(argv)
    from admin.db import SessionLocal

    db = SessionLocal()
    try:
        out = {}
        if args.rebuild:
            out["rebuild"] = rebuild(db, reason="命令行")
        covered = ensure_coverage(db, budget=max(0.0, args.budget))
        out["covered_from"] = covered[0].isoformat() if covered[0] else None
        out["covered_to"] = covered[1].isoformat() if covered[1] else None
        print(json.dumps(out, ensure_ascii=False))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(_main())
