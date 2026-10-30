"""Official model capacity observations, cached independently of account cooldowns."""
from datetime import datetime
import logging
import threading
import time

LABELS = {"idle": "空闲", "normal": "空闲", "busy": "轻微拥堵",
          "congested": "极度拥堵", "unavailable": "不可用", "unknown": "未知"}
CACHE_SECONDS = 50
#: 缓存条目上限（每个被查询过的模型一条，不随账号规模增长）
_CACHE_MAX = 128
_lock = threading.Lock()
_cache = {}
_logger = logging.getLogger(__name__)


def normalize_response(models, response):
    if not isinstance(response, dict) or response.get("code") != 0 or not isinstance(response.get("data"), list):
        raise ValueError("Invalid model busy response")
    levels = {model: "unknown" for model in models}
    for item in response["data"]:
        if isinstance(item, dict) and isinstance(item.get("model"), str) and item["model"] in levels:
            value = item.get("busy_level")
            levels[item["model"]] = value if isinstance(value, str) and value in LABELS else "unknown"
    return levels


def get_busy_levels(db, models):
    from admin import backend, maintenance
    from admin.models import Account
    from admin.account_binding import scope_accounts
    from sqlalchemy import or_

    models = sorted(set(models))
    now = datetime.utcnow()
    candidates = scope_accounts(db.query(Account)).filter(Account.status == "active", *[
        or_(getattr(Account, field).is_(None), getattr(Account, field) <= now)
        for field in ("cool_until", "breaker_until", "degrade_until")
    ]).order_by(Account.id).all()
    unknown = {m: "unknown" for m in models}
    # Include every eligible identity so a skipped first account cannot hide
    # healthy accounts, and account-binding scopes never share observations.
    key = (tuple((a.id, a.uid) for a in candidates), tuple(models))
    with _lock:
        cached = _cache.get(key)
        if cached and time.monotonic() - cached["checked"] < cached.get("ttl", CACHE_SECONDS):
            return {k: v for k, v in cached.items() if k not in ("checked", "ttl")}
        attempts = 0
        result = {"levels": cached["levels"] if cached else unknown,
                  "observed_at": cached["observed_at"] if cached else None, "stale": True}
        for account in candidates if models else []:
            try:
                with maintenance.account_slot(account, db) as reason:
                    if reason:
                        continue
                    attempts += 1
                    original = account.auth_json
                    levels = {}
                    with backend.AccountSession(original) as session:
                        try:
                            for offset in range(0, len(models), 50):
                                batch = models[offset:offset + 50]
                                levels.update(normalize_response(batch, session.fetch_model_busy(batch)))
                        finally:
                            updated = session.updated_json()
                            db.query(Account).filter(Account.id == account.id, Account.auth_json == original).update(
                                {Account.auth_json: updated}, synchronize_session=False)
                            db.commit()
                    result = {"levels": levels, "observed_at": datetime.utcnow().isoformat() + "Z", "stale": False}
                    break
            except Exception as error:
                db.rollback()
                maintenance.note_failure(db, account, error)
                db.commit()
                _logger.warning("上游模型繁忙程度采集失败（%s），尝试其他可用账号", type(error).__name__)
                if attempts >= 3:
                    break
        # A temporary reservation/invalid identity should be retried promptly.
        _cache[key] = {**result, "checked": time.monotonic(), "ttl": 5 if result["stale"] and not attempts else CACHE_SECONDS}
        while len(_cache) > _CACHE_MAX:
            _cache.pop(next(iter(_cache)))
        return result


def status():
    """缓存观测（/api/stats/memory 汇总用）。"""
    with _lock:
        return {"name": "model_busy", "entries": len(_cache),
                "max_entries": _CACHE_MAX, "ttl_seconds": CACHE_SECONDS}