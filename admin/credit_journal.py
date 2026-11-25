"""Reward provenance comes from action results, never from a balance delta."""
import json
import uuid
from datetime import datetime

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from admin.models import Account, CreditEvent, GrowthPlanRecord


def record_rewards(db, record):
    try:
        result = json.loads(record.result_json or "{}")
    except (TypeError, ValueError):
        result = {}
    if not isinstance(result, dict):
        result = {}
    prefix = str(result.get("credit_event_key") or f"growth:{record.id}") + ":"
    if db.query(CreditEvent.id).filter(CreditEvent.event_key.like(prefix + "%")).first():
        return
    account = db.get(Account, record.account_id)
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


def backfill(db):
    """Only import rewards actually saved by older versions, in bounded batches."""
    from admin.growth_plan import _Lease
    lease = _Lease(db, "credit_journal:import_lease")
    if not lease.acquire():
        return True
    try:
        return _backfill_batch(db)
    finally:
        lease.release()


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
    for record in records:
        record_rewards(db, record)
    if records:
        if state is None:
            state = SystemSetting(key=key)
            db.add(state)
        state.value = str(records[-1].id)
        db.commit()
    return len(records) == 500


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
        db.add(CreditEvent(event_key="balance:" + uuid.uuid4().hex, account_id=account.id,
                           account_name=account.name or account.uid or f"#{account.id}",
                           kind="observed", source="余额观察（来源未确认）", delta=int(new)-int(old), balance_after=int(new)))