"""Reward provenance comes from action results, never from a balance delta."""
import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from admin.models import Account, CreditEvent, GrowthPlanRecord


logger = logging.getLogger("admin.credit_journal")


def _result_of(record) -> dict:
    try:
        result = json.loads(record.result_json or "{}")
    except (TypeError, ValueError):
        result = {}
    return result if isinstance(result, dict) else {}


def _prefix_of(record, result) -> str:
    return str(result.get("credit_event_key") or f"growth:{record.id}") + ":"


def record_rewards(db, record, *, existing=None, accounts=None):
    """把一条成长记录里的奖励落成 `confirmed` 流水（幂等）。

    `existing` / `accounts` 是批量回填预取的缓存（前缀集合 / `{id: Account}`）：
    逐条 `LIKE prefix%` + `db.get(Account)` 是 500 条一批的 N+1（R1 6.5）。
    不传时行为与原来一致（单条调用方不受影响）。
    """
    result = _result_of(record)
    prefix = _prefix_of(record, result)
    if existing is not None:
        if prefix in existing:
            return
    elif db.query(CreditEvent.id).filter(CreditEvent.event_key.like(prefix + "%")).first():
        return
    account = (accounts.get(record.account_id) if accounts is not None
               else db.get(Account, record.account_id))
    # Partial patrols can award credits even when the overall result failed.
    def positive(value):
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError, OverflowError):
            return 0
    steps = [s for s in (result.get("steps") or []) if isinstance(s, dict) and s.get("ok") and positive(s.get("reward")) > 0]
    if not steps and record.credit > 0:
        steps = [{"step": record.action, "reward": record.credit}]
    for index, step in enumerate(steps):
        db.add(CreditEvent(event_key=prefix + str(index), account_id=record.account_id,
                           account_name=(account.name or account.uid) if account else f"已删除账号 #{record.account_id}",
                           kind="confirmed", source=f"{record.source} · {step.get('step') or record.action}"[:120],
                           delta=positive(step["reward"]), balance_after=result.get("balance_after") if index == len(steps)-1 else None,
                           created_at=record.created_at or datetime.utcnow()))
    db.flush()
    if existing is not None and steps:
        existing.add(prefix)


#: 后台回填状态（进程内）：done=True 表示游标已追平，GET 不再做任何事。
_BACKFILL = {"thread": None, "done": False}
_BACKFILL_LOCK = threading.Lock()


def backfill(db):
    """积分流水页（GET）调用：返回「是否还在导入历史奖励」。

    **不在请求里干活**（P8 / R1 6.5）：原来每次 GET 都取租约、处理 500 条
    GrowthPlanRecord（每条一次 LIKE 查询 + 一次 get，N+1）再 commit，
    积分流水首屏 3~9 秒。现在 GET 只做两次主键级查询判断「追平没有」，
    没追平就拉起**一个**后台线程分批导入，立刻返回 True（前端照旧显示「导入中」）。
    """
    if _BACKFILL["done"]:
        return False
    try:
        if _caught_up(db):
            _BACKFILL["done"] = True
            return False
    except Exception:  # noqa: BLE001 - 判断失败就交给后台线程去试
        pass
    start_background_backfill()
    return True


def _caught_up(db) -> bool:
    from admin.models import SystemSetting

    state = db.get(SystemSetting, "credit_journal:import_cursor")
    try:
        cursor = int(state.value) if state else 0
    except (TypeError, ValueError):
        cursor = 0
    latest = db.query(GrowthPlanRecord.id).order_by(GrowthPlanRecord.id.desc()).limit(1).scalar()
    return latest is None or int(latest) <= cursor


def start_background_backfill() -> bool:
    """拉起唯一的后台导入线程（已在跑就什么都不做）。返回是否新起了线程。"""
    with _BACKFILL_LOCK:
        thread = _BACKFILL.get("thread")
        if thread is not None and thread.is_alive():
            return False
        thread = threading.Thread(target=_backfill_worker, name="credit-journal-import",
                                  daemon=True)
        _BACKFILL["thread"] = thread
        thread.start()
        return True


def _backfill_worker() -> None:
    from admin.db import SessionLocal
    from admin.growth_plan import _Lease

    for _ in range(10000):          # 上限：500 × 10000 条，防止游标异常时死循环
        db = SessionLocal()
        try:
            lease = _Lease(db, "credit_journal:import_lease")
            if not lease.acquire():
                return               # 别的进程在导：它会负责追平
            try:
                more = _backfill_batch(db)
            finally:
                lease.release()
            if not more:
                _BACKFILL["done"] = True
                return
        except Exception as exc:  # noqa: BLE001 - 后台导入失败不能影响服务
            logger.warning("积分流水历史导入失败（下次打开积分流水页再试）：%s", exc)
            return
        finally:
            db.close()
        time.sleep(0.2)              # 批间让出写锁


def attach_balance(db, record_ids, balance):
    """Attach the post-action observation without inventing a per-step balance."""
    if not record_ids:
        return
    for record in db.query(GrowthPlanRecord).filter(GrowthPlanRecord.id.in_(record_ids)):
        result = json.loads(record.result_json or "{}")
        result["balance_after"] = int(balance)
        record.result_json = json.dumps(result, ensure_ascii=False)
        prefix = str(result.get("credit_event_key") or f"growth:{record.id}") + ":"
        last = db.query(CreditEvent).filter(CreditEvent.event_key.like(prefix + "%")).order_by(CreditEvent.id.desc()).first()
        if last:
            last.balance_after = int(balance)


def _backfill_batch(db):
    from admin.models import SystemSetting
    key = "credit_journal:import_cursor"
    state = db.get(SystemSetting, key)
    try:
        cursor = int(state.value) if state else 0
    except (TypeError, ValueError):
        cursor = 0
    records = db.query(GrowthPlanRecord).filter(GrowthPlanRecord.id > cursor).order_by(GrowthPlanRecord.id).limit(500).all()
    # 批量预取（去掉 N+1）：每条奖励的第一个流水键是 `<prefix>0`，
    # 有它就说明这条记录已经导入过；账号一次取齐。
    prefixes = {record.id: _prefix_of(record, _result_of(record)) for record in records}
    existing = set()
    keys = [prefix + "0" for prefix in prefixes.values()]
    for start in range(0, len(keys), 200):
        existing.update(key[:-1] for (key,) in db.query(CreditEvent.event_key)
                        .filter(CreditEvent.event_key.in_(keys[start:start + 200])).all())
    account_ids = sorted({record.account_id for record in records if record.account_id})
    accounts = {acc.id: acc for acc in db.query(Account).filter(Account.id.in_(account_ids)).all()} \
        if account_ids else {}
    for record in records:
        record_rewards(db, record, existing=existing, accounts=accounts)
    if records:
        if state is None:
            state = SystemSetting(key=key)
            db.add(state)
        state.value = str(records[-1].id)
        db.commit()
    return len(records) == 500


#: `CREDIT_JOURNAL_SKIP_LOCAL_DEBITS=1`：只在真正同步余额时记「余额观察」。
_SKIP_LOCAL_DEBITS = (os.getenv("CREDIT_JOURNAL_SKIP_LOCAL_DEBITS", "") or "").strip().lower() in (
    "1", "true", "yes", "on")


@event.listens_for(Session, "before_flush")
def observe_balance(db, _context, _instances):
    for account in list(db.dirty):
        if not isinstance(account, Account):
            continue
        history = inspect(account).attrs.balance_remain.history
        if not history.has_changes() or not history.deleted or not history.added:
            continue
        # New/imported accounts establish a baseline rather than a fake grant.
        sync = inspect(account).attrs.last_sync_at.history
        previous_sync = (sync.deleted[0] if sync.deleted else None) if sync.has_changes() else account.last_sync_at
        if previous_sync is None:
            continue
        old, new = history.deleted[0], history.added[0]
        if old is None or new is None or old == new:
            continue
        # 可选（默认关）：本地记账扣减（同一次 flush 里没有同步余额）不再写「余额观察」。
        # 原来每个请求都会多插一行 credit_events，表的增长速度接近 usage_logs（R1 #7）。
        if _SKIP_LOCAL_DEBITS and not sync.has_changes() and int(new) < int(old):
            continue
        db.add(CreditEvent(event_key="balance:" + uuid.uuid4().hex, account_id=account.id,
                           account_name=account.name or account.uid or f"#{account.id}",
                           kind="observed", source="余额观察（来源未确认）", delta=int(new)-int(old), balance_after=int(new)))