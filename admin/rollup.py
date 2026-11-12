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
import threading
import time
from datetime import datetime, timedelta

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from admin.models import UsageLog, UsageRollup, UsageRollupState

logger = logging.getLogger("admin.rollup")

OK_KINDS = ("", "success")

#: 回填每批处理多少小时：48 小时 ≈ 2 天，单批在 20 万行的库上不到 1 秒。
_BACKFILL_CHUNK_HOURS = 48
#: 单次 `ensure_coverage` 的最长时间预算（秒）。超了就留给后台线程。
_BACKFILL_BUDGET = 2.0

_lock = threading.Lock()
_thread = None

#: 汇总字段（同时用于 upsert 的加减、以及两段结果的合并）。
_SUM_FIELDS = ("requests", "successes", "prompt_tokens", "completion_tokens",
               "total_tokens", "cached_tokens", "credits", "latency_sum",
               "latency_count", "ttfb_sum", "ttfb_count")

#: 只有 credits 是浮点，其余全是整数计数。
_FLOAT_FIELDS = ("credits",)


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
            "ttfb_sum": int(ttfb_ms or 0) if success else 0,
            "ttfb_count": 1 if (success and ttfb_ms is not None) else 0,
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


def _aggregate_raw(db: Session, start: datetime, end: datetime) -> list[dict]:
    """把 `[start, end)` 的原表行按 (hour, platform, model) 聚成汇总行。"""
    ok = UsageLog.error_kind.in_(OK_KINDS)
    hour = func.date_format(UsageLog.created_at, "%Y-%m-%d %H:00") \
        if db.get_bind().dialect.name != "sqlite" \
        else func.strftime("%Y-%m-%d %H:00", UsageLog.created_at)
    rows = (
        db.query(
            hour.label("h"),
            UsageLog.platform,
            UsageLog.model,
            func.count(UsageLog.id),
            func.sum(case((ok, 1), else_=0)),
            func.sum(case((ok, UsageLog.prompt_tokens), else_=0)),
            func.sum(case((ok, UsageLog.completion_tokens), else_=0)),
            func.sum(case((ok, UsageLog.total_tokens), else_=0)),
            func.sum(case((ok, UsageLog.cached_tokens), else_=0)),
            func.sum(case((ok, UsageLog.credits), else_=0.0)),
            func.sum(case((ok, UsageLog.latency_ms), else_=0)),
            func.sum(case((ok, UsageLog.latency_ms.isnot(None)), else_=0)),
            func.sum(case((ok, UsageLog.ttfb_ms), else_=0)),
            func.sum(case((ok, UsageLog.ttfb_ms.isnot(None)), else_=0)),
        )
        .filter(UsageLog.created_at >= start, UsageLog.created_at < end)
        .group_by("h", UsageLog.platform, UsageLog.model)
        .all()
    )
    out = []
    for row in rows:
        parsed = datetime.strptime(row[0], "%Y-%m-%d %H:00")
        out.append({
            "hour": parsed,
            "platform": (row[1] or "").lower(),
            "model": row[2] or "",
            "requests": int(row[3] or 0),
            "successes": int(row[4] or 0),
            "prompt_tokens": int(row[5] or 0),
            "completion_tokens": int(row[6] or 0),
            "total_tokens": int(row[7] or 0),
            "cached_tokens": int(row[8] or 0),
            "credits": float(row[9] or 0),
            "latency_sum": int(row[10] or 0),
            "latency_count": int(row[11] or 0),
            "ttfb_sum": int(row[12] or 0),
            "ttfb_count": int(row[13] or 0),
        })
    return out


def ensure_coverage(db: Session, *, until: datetime | None = None,
                    budget: float = _BACKFILL_BUDGET) -> tuple[datetime | None, datetime | None]:
    """补齐 `usage_logs` 到汇总表的缺口。**只补缺口，不重扫全表。**

    返回补完后的覆盖区间（半开）。超过 `budget` 就停手：剩下的交给后台线程，
    读请求先用「已完成的部分走汇总、剩下的走原表」照样能出数。
    """
    deadline = time.monotonic() + max(0.0, budget)
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
            time.sleep(interval)

    _thread = threading.Thread(target=worker, name="usage-rollup-backfill", daemon=True)
    _thread.start()


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------

def segments(start: datetime, end: datetime,
             covered: tuple[datetime | None, datetime | None]):
    """把窗口切成「走汇总表」与「走原表」的段（半开区间）。

    汇总表只覆盖**已走完的整点**，所以当前这一小时永远是原表段 ——
    看板的「今日」才不会比日志页少一小时的数。
    """
    covered_from, covered_to = covered
    out = []
    if covered_from is None or covered_to is None:
        return [("raw", start, end)]
    roll_start = max(start, covered_from)
    roll_end = min(end, covered_to)
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
    ok = UsageLog.error_kind.in_(OK_KINDS)
    return (
        db.query(
            UsageLog.platform,
            UsageLog.model,
            func.count(UsageLog.id),
            func.sum(case((ok, 1), else_=0)),
            func.sum(case((ok, UsageLog.prompt_tokens), else_=0)),
            func.sum(case((ok, UsageLog.completion_tokens), else_=0)),
            func.sum(case((ok, UsageLog.total_tokens), else_=0)),
            func.sum(case((ok, UsageLog.cached_tokens), else_=0)),
            func.sum(case((ok, UsageLog.credits), else_=0.0)),
            func.sum(case((ok, UsageLog.latency_ms), else_=0)),
            func.sum(case((ok, UsageLog.latency_ms.isnot(None)), else_=0)),
            func.sum(case((ok, UsageLog.ttfb_ms), else_=0)),
            func.sum(case((ok, UsageLog.ttfb_ms.isnot(None)), else_=0)),
        )
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
            ok = UsageLog.error_kind.in_(OK_KINDS)
            query = (
                db.query(
                    hour.label("h"),
                    UsageLog.model,
                    func.count(UsageLog.id),
                    func.sum(case((ok, 1), else_=0)),
                    func.sum(case((ok, UsageLog.prompt_tokens), else_=0)),
                    func.sum(case((ok, UsageLog.completion_tokens), else_=0)),
                    func.sum(case((ok, UsageLog.total_tokens), else_=0)),
                    func.sum(case((ok, UsageLog.cached_tokens), else_=0)),
                    func.sum(case((ok, UsageLog.credits), else_=0.0)),
                    func.sum(case((ok, UsageLog.latency_ms), else_=0)),
                    func.sum(case((ok, UsageLog.latency_ms.isnot(None)), else_=0)),
                    func.sum(case((ok, UsageLog.ttfb_ms), else_=0)),
                    func.sum(case((ok, UsageLog.ttfb_ms.isnot(None)), else_=0)),
                )
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