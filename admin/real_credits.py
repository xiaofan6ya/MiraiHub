"""WorkBuddy 真实积分回填：**一个**后台线程 + 延时队列，按账号批量查上游账单（N6）。

## 原来的两个问题（admin/routers/proxy.py::_start_real_credits_backfill）

1. **每个估算积分的请求起一个线程**，先 `sleep(60)` 再查上游。1 rps 时同时挂着约
   60 个线程，10 rps 时 600 个；每个都握着 job 租约，排空要等它们全部醒来。
2. **时区错位**：`usage_logs.created_at` 存的是 UTC，原实现直接把它格式化成
   `"%Y-%m-%d %H:%M:%S"` 去查上游 `/billing/meter/get-user-request-usage`，而上游账单的
   `requestTime` 是**北京时间**（与官方客户端账单页一致）。查询窗口整体偏早 8 小时，
   requestId 永远对不上；按时间兜底匹配时又拿北京时间减 UTC，「最近的一条」其实是
   8 小时前的别的请求。

## 现在

* `enqueue()` 只把任务放进一个按到期时间排序的堆里（仍在入队前取 job 租约，语义与
  原来一致：封口后不再入队，排空会等队列里的任务处理完）；
* 唯一的 `wb-real-credits` 线程按到期时间取任务，**同一账号同一批**只查一次上游
  （窗口取这批请求的最早 / 最晚时间 ±5 分钟，最多翻 5 页）；
* 查询窗口按 **北京时间** 发；requestId 没找全时再用旧的（UTC）窗口补查一次 ——
  在拿到一条真实账单核对时区之前，两种解释都覆盖，不会因为猜错时区而整批落空；
* 按 `requestTime` 兜底匹配时先把它换算回 UTC 再和 `created_at` 比。

`WB_BILL_TZ_OFFSET_HOURS`（默认 8）：上游账单时间相对 UTC 的偏移；
`WB_REAL_CREDITS_DELAY_S`（默认 60）：请求结束后多久再去查（上游有分钟级延迟）；
`WB_REAL_CREDITS_QUEUE`（默认 1）：0 = 调用方继续用旧的「一请求一线程」实现（回退开关）。

## 接入（`admin/routers/proxy.py` 归 gateway 线，本模块只提供函数）

`_start_real_credits_backfill()` 的函数体开头加三行即可，签名、返回值（租约 / None）不变：

    from admin import real_credits
    if real_credits.queue_enabled():
        return real_credits.enqueue(log_id, account_id, auth_json, model,
                                    estimated_credits, created_at, request_ids)
"""
from __future__ import annotations

import heapq
import itertools
import logging
import os
import threading
import time
from datetime import datetime, timedelta

logger = logging.getLogger("admin.real_credits")

_heap: list = []
_seq = itertools.count()
_cond = threading.Condition()
_thread = None
_PAGE_SIZE = 50
_MAX_PAGES = 5


def _env_float(name: str, default: float) -> float:
    try:
        return float((os.getenv(name, "") or "").strip() or default)
    except ValueError:
        return default


def bill_offset() -> timedelta:
    """上游账单时间相对 UTC 的偏移（默认 +8，北京时间）。"""
    return timedelta(hours=_env_float("WB_BILL_TZ_OFFSET_HOURS", 8.0))


def delay_seconds() -> float:
    return max(0.0, _env_float("WB_REAL_CREDITS_DELAY_S", 60.0))


def to_bill_time(created_at: datetime) -> datetime:
    """UTC naive -> 上游账单口径（北京时间 naive）。"""
    return created_at + bill_offset()


def from_bill_time(value: datetime) -> datetime:
    """上游账单时间（北京时间 naive）-> UTC naive。"""
    return value - bill_offset()


def queue_enabled() -> bool:
    """`WB_REAL_CREDITS_QUEUE=0` 时返回 False（调用方回到旧实现）。默认开。"""
    return (os.getenv("WB_REAL_CREDITS_QUEUE", "1") or "1").strip().lower() not in (
        "0", "false", "no", "off")


def pending() -> int:
    with _cond:
        return len(_heap)


def enqueue(log_id: int, account_id: int, auth_json: str, model: str,
            estimated_credits: float, created_at: datetime,
            request_ids: list[str] | None = None, *, delay: float | None = None):
    """登记一条「稍后用上游真实积分校正」的任务。返回 job 租约（封口时 None）。

    租约在**入队前**取、在任务处理完（成功 / 失败 / 跳过）后释放：排空会等队列清空，
    封口后不再接新任务 —— 与原来「一个请求一个线程」的语义一致，只是不再有线程风暴。
    """
    from admin import jobrunner

    lease = jobrunner.take_job_lease()
    if lease is None:
        logger.info("服务已封口，跳过真实用量回填 log=%s（保留估算值 credits=%s）",
                    log_id, estimated_credits)
        return None
    item = {"log_id": int(log_id), "account_id": int(account_id or 0),
            "auth_json": auth_json, "model": model or "",
            "estimated": float(estimated_credits or 0), "created_at": created_at,
            "request_ids": [str(i) for i in (request_ids or []) if i], "lease": lease}
    due = time.monotonic() + (delay_seconds() if delay is None else max(0.0, delay))
    try:
        with _cond:
            heapq.heappush(_heap, (due, next(_seq), item))
            _ensure_thread()
            _cond.notify()
    except BaseException:
        jobrunner.release_job_lease(lease)
        raise
    return lease


def _ensure_thread() -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _thread = threading.Thread(target=_worker, name="wb-real-credits", daemon=True)
    _thread.start()


def _take_due() -> list[dict]:
    """阻塞到至少一个任务到期，取出**所有**已到期任务。"""
    with _cond:
        while True:
            if not _heap:
                _cond.wait(timeout=30.0)
                continue
            wait = _heap[0][0] - time.monotonic()
            if wait > 0:
                _cond.wait(timeout=min(wait, 30.0))
                continue
            out = []
            now = time.monotonic()
            while _heap and _heap[0][0] <= now:
                out.append(heapq.heappop(_heap)[2])
            return out


def _worker() -> None:
    while True:
        try:
            batch = _take_due()
        except Exception:  # noqa: BLE001
            time.sleep(1.0)
            continue
        groups: dict[int, list[dict]] = {}
        for item in batch:
            groups.setdefault(item["account_id"], []).append(item)
        for items in groups.values():
            try:
                process_group(items)
            except Exception as exc:  # noqa: BLE001 - 一个账号失败不影响别的账号
                logger.exception("真实积分回写失败 acc=%s: %s", items[0]["account_id"], exc)
            finally:
                from admin import jobrunner
                for item in items:
                    jobrunner.release_job_lease(item.get("lease"))


def _fetch_window(sess, start: datetime, end: datetime) -> list[dict]:
    rows: list[dict] = []
    fmt = "%Y-%m-%d %H:%M:%S"
    for page in range(1, _MAX_PAGES + 1):
        result = sess.fetch_request_usage(start.strftime(fmt), end.strftime(fmt),
                                          page_num=page, page_size=_PAGE_SIZE)
        data = ((result or {}).get("data") or {}).get("data") or []
        rows.extend(r for r in data if isinstance(r, dict))
        if len(data) < _PAGE_SIZE:
            break
    return rows


def _parse_bill_time(text) -> datetime | None:
    try:
        return datetime.strptime(str(text or ""), "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


def match(item: dict, rows: list[dict], client_name: str):
    """在账单行里找这条请求：requestId 优先，没有 id 时按「同模型 + 客户端名 + 时间最近」。

    返回 `(row, matched_by)`；找不到返回 `(None, reason)`。时间比较统一换算成 UTC。
    """
    wanted = item.get("request_ids") or []
    by_id = {str(r.get("requestId") or ""): r for r in rows}
    best = next((by_id[i] for i in wanted if i in by_id), None)
    if best is not None:
        return best, "requestId"
    if wanted:
        # 带了 id 却没找到：账单延迟 / 分页没翻到。不能借别的请求的积分。
        return None, "request_id_not_found"
    candidates = [r for r in rows
                  if r.get("model") == item["model"] and client_name in (r.get("client") or "")]
    created = item["created_at"]

    def distance(row):
        stamp = _parse_bill_time(row.get("requestTime"))
        if stamp is None:
            return float("inf")
        return abs((from_bill_time(stamp) - created).total_seconds())

    candidates = [r for r in candidates if distance(r) <= 600]
    if not candidates:
        return None, "no_candidate"
    return min(candidates, key=distance), "requestTime"


def process_group(items: list[dict]) -> None:
    """同一账号的一批任务：查一次上游账单（必要时补查旧窗口），逐条匹配回写。"""
    from admin.config import settings
    from admin.platform.workbuddy.core.backend import AccountSession

    items = [item for item in items if item.get("auth_json")]
    if not items:
        return
    sess = AccountSession(items[-1]["auth_json"])
    try:
        earliest = min(item["created_at"] for item in items)
        latest = max(item["created_at"] for item in items)
        pad = timedelta(minutes=5)
        rows = _fetch_window(sess, to_bill_time(earliest) - pad, to_bill_time(latest) + pad)
        wanted = {i for item in items for i in item.get("request_ids") or []}
        found = {str(r.get("requestId") or "") for r in rows}
        if wanted - found and bill_offset():
            # 旧口径（把 UTC 当账单时间）补查一次：在用真实账单核对时区之前两种都覆盖。
            seen = {id(r) for r in rows}
            rows += [r for r in _fetch_window(sess, earliest - pad, latest + pad)
                     if id(r) not in seen]
        client_name = (getattr(settings, "UPSTREAM_CLIENT_NAME", "") or "WorkBuddy").strip() \
            or "WorkBuddy"
        for item in items:
            best, matched_by = match(item, rows, client_name)
            if best is None:
                logger.info("真实积分回写未找到匹配 log=%s model=%s reason=%s ids=%s",
                            item["log_id"], item["model"], matched_by, item["request_ids"][:3])
                continue
            real = float(best.get("credit") or 0)
            logger.info("真实积分匹配 log=%s model=%s real=%s est=%s by=%s requestTime=%s requestId=%s",
                        item["log_id"], item["model"], real, item["estimated"], matched_by,
                        best.get("requestTime"), best.get("requestId"))
            apply_real_credits(item["log_id"], real)
    finally:
        try:
            sess.close()
        except Exception:  # noqa: BLE001
            pass


def apply_real_credits(log_id: int, real: float) -> float | None:
    """把一条日志的积分改成上游真实值，并按差值校正 Key / 账号 / 汇总表。返回差值。"""
    from sqlalchemy import case, func

    from admin.db import SessionLocal
    from admin.models import Account, ApiKey, UsageLog

    db = SessionLocal()
    try:
        log = db.query(UsageLog).filter(UsageLog.id == log_id).with_for_update().first()
        if log is None:
            return None
        delta = float(real) - float(log.credits or 0)
        if abs(delta) < 0.0001:
            return 0.0
        log.credits = float(real)
        amount = func.coalesce(ApiKey.credit_used, 0) + delta
        db.query(ApiKey).filter(ApiKey.id == log.api_key_id).update(
            {ApiKey.credit_used: case((amount < 0, 0), else_=amount)}, synchronize_session=False)
        acc = db.query(Account).filter(Account.id == log.account_id).with_for_update().first()
        if acc is not None:
            acc.balance_remain = max(0, int(acc.balance_remain or 0) - int(delta))
        db.commit()
        try:
            from admin import rollup
            rollup.adjust_credits(db, platform=log.platform, model=log.model,
                                  created_at=log.created_at, delta=delta)
        except Exception:  # noqa: BLE001 - 汇总校正失败不影响已完成的回写
            pass
        logger.info("真实积分回写完成 log=%s real=%s delta=%s", log_id, real, delta)
        return delta
    finally:
        db.close()
