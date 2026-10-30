"""Pace background upstream calls and reserve idle account identities."""
from collections import Counter
from contextlib import contextmanager
from datetime import datetime
import threading
import time

from admin.config import settings
from admin.pool import POOL

_batch_lock = threading.Lock()
_state_lock = threading.Lock()
_last_finished = None
_skips = Counter()


def _reason(account):
    if POOL.waf.active():
        return "waf"
    if account.status != "active":
        return "inactive"
    now = datetime.utcnow()
    if any(getattr(account, field, None) and getattr(account, field) > now
           for field in ("cool_until", "breaker_until", "degrade_until")):
        return "account_cooling"
    from admin.routers.proxy import _looks_login_dead
    if _looks_login_dead(account, now):
        return "login_required"
    return None


_SKIP_REASON_TEXT = {
    'waf': '网关出口正在执行本地 WAF 保护',
    'inactive': '账号已停用',
    'account_cooling': '账号处于冷却 / 熔断 / 降权期',
    'login_required': '上游账号凭据需重新验证或更新',
    'background_busy': '后台批量任务正在运行',
    'inflight': '账号在途占用已满',
}


def skip_reason_text(reason, action='已跳过'):
    text = _SKIP_REASON_TEXT.get(reason, f'账号本轮不可用（{reason or "unknown"}）')
    return f'{text}，{action}'


@contextmanager
def account_slot(account, db=None):
    """Skip busy/restricted accounts; release reservations even on failure.

    The lock and lease are process local, matching the account pool. No waits
    are imposed on conversations. Background work skips a contested identity.
    """
    global _last_finished
    reason = _reason(account)
    locked = False
    reserved = False
    uid = account.uid or ""
    try:
        if reason is None:
            locked = _batch_lock.acquire(blocking=False)
            if not locked:
                reason = "background_busy"
        if reason is None:
            with _state_lock:
                last = _last_finished
            gap = min(60.0, max(0.0, settings.KEEPALIVE_ACCOUNT_GAP))
            if last is not None:
                remaining = gap - (time.monotonic() - last)
                if remaining > 0:
                    time.sleep(remaining)
            reason = _reason(account)
        if reason is None:
            # Pace before reserving, so an idle gap does not take capacity away
            # from conversations. Foreground arrivals win while we are waiting.
            reserved = POOL.inflight.acquire_maintenance(uid)
            if not reserved:
                reason = "inflight"
        if reason is None:
            if db is not None:
                # Reload after reserving: credentials or account state may have
                # changed while the batch waited or processed another identity.
                db.refresh(account)
            reason = _reason(account)
        if reason:
            with _state_lock:
                _skips[reason] += 1
        yield reason
    finally:
        if reserved:
            with _state_lock:
                _last_finished = time.monotonic()
            POOL.inflight.release_maintenance(uid)
        if locked:
            _batch_lock.release()


def note_failure(db, account, error):
    """Let background WAF/rate observations protect the conversation pool too."""
    from admin.routers.proxy import _apply_account_policy, _classify_error, _extract_http_status
    import json
    from admin.upstream_errors import error_info
    message = getattr(error, 'raw_body', str(error))
    status = getattr(error, 'http_status', 0) or _extract_http_status(str(error))
    account.last_upstream_error = json.dumps(error_info(message, status), ensure_ascii=False)
    kind = _classify_error(status, message)
    if kind in ("waf", "soft_rate", "account_fault"):
        from admin.model_limits import reset_from_body
        _apply_account_policy(db, account, kind, status, message,
                              reset_at=reset_from_body(message))
    return kind


def snapshot():
    with _state_lock:
        skipped = dict(_skips)
    return {"scope": "process_local", "skipped_since_start": skipped,
            "reserved_accounts": POOL.inflight.maintenance_count(),
            "account_gap_seconds": min(60.0, max(0.0, settings.KEEPALIVE_ACCOUNT_GAP))}


@contextmanager
def token_slot(account, db, wait_seconds=30):
    """Renew credentials even during cooling; drain conversations before rotation.

    Reserving first prevents a busy identity from being starved by new requests.
    A bounded wait leaves a pending account for the scheduler's short retry.
    """
    global _last_finished
    locked = _batch_lock.acquire(timeout=wait_seconds)
    reserved = False
    uid = account.uid or f"account:{account.id}"
    try:
        if not locked:
            yield "background_busy"
            return
        gap = min(60.0, max(0.0, settings.KEEPALIVE_ACCOUNT_GAP))
        with _state_lock:
            last = _last_finished
        if last is not None:
            time.sleep(max(0, gap - (time.monotonic() - last)))
        reserved = POOL.inflight.acquire_maintenance(uid, drain=True)
        if not reserved:
            yield "inflight"
            return
        deadline = time.monotonic() + wait_seconds
        while POOL.inflight.count(uid) and time.monotonic() < deadline:
            time.sleep(.1)
        db.refresh(account)
        yield ("inactive" if account.status != "active" else
               "inflight" if POOL.inflight.count(uid) else None)
    finally:
        if reserved:
            with _state_lock:
                _last_finished = time.monotonic()
            POOL.inflight.release_maintenance(uid)
        if locked:
            _batch_lock.release()