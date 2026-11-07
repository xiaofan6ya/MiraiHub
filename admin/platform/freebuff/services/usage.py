"""Freebuff 用量详情与使用日志（**只读本地账本**）。

## 为什么只有本地

Freebuff 上游只有两个端点：`/models`（免鉴权）与 `/chat/completions`。
`/usage`、`/credits`、`/me`、`/balance`、`/health`、`/billing` 实测全部 404，
所以**没有上游用量接口可查**。

这不是缺陷而是事实：额度以 freebuff.com 控制台为准，本网关只负责把自己
转发过的每一次调用记清楚（`UsageLog`），并据此给出「本网关视角」的用量。
接口里 `upstream: {"supported": False}` 就是在明说这件事，不假装有数据。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from admin.platform.freebuff.services import accounts as fb_accounts

logger = logging.getLogger("admin.platform.freebuff.usage")

#: 上游没有用量接口 —— 所有「上游视角」的字段统一挂这句。
UPSTREAM_NOTE = ("Freebuff 上游只有 /models 与 /chat/completions，"
                 "没有用量/余额接口；额度请以 freebuff.com 控制台为准")


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float(value) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _text(value, limit: int = 120) -> str:
    return str(value or "")[:limit]


def _since(days: int) -> datetime:
    return datetime.utcnow() - timedelta(days=max(1, int(days or 1)))


def account_detail(db: Session, account, *, days: int = 30) -> dict:
    """单账号用量：本地账本 + 该账号最近的请求。"""
    from admin.models import UsageLog

    since = _since(days)
    rows = (db.query(
        UsageLog.model,
        func.count(UsageLog.id).label("requests"),
        func.sum(UsageLog.total_tokens).label("tokens"),
        func.sum(UsageLog.credits).label("credits"),
    ).filter(UsageLog.platform == fb_accounts.PLATFORM,
             UsageLog.account_id == int(account.id),
             UsageLog.created_at >= since)
        .group_by(UsageLog.model).all())

    by_model = [{
        "model": row.model or "-",
        "requests": _int(row.requests),
        "tokens": _int(row.tokens),
        "credits": _float(row.credits),
    } for row in rows]
    by_model.sort(key=lambda item: item["tokens"], reverse=True)

    recent = (db.query(UsageLog)
              .filter(UsageLog.platform == fb_accounts.PLATFORM,
                      UsageLog.account_id == int(account.id))
              .order_by(UsageLog.id.desc()).limit(10).all())

    data = fb_accounts.credentials_of(account)
    return {
        "account_id": account.id,
        "name": account.name,
        "key_tail": str(data.get("key_tail") or ""),
        "status": getattr(account, "status", "") or "",
        "verify_state": str(data.get("verify_state") or "unverified"),
        "last_activity_at": data.get("last_activity_at"),
        "days": int(days),
        "local": {
            "requests": sum(item["requests"] for item in by_model),
            "tokens": sum(item["tokens"] for item in by_model),
            "credits": round(sum(item["credits"] for item in by_model), 2),
            "by_model": by_model,
        },
        "recent": [{
            "id": row.id,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "model": _text(row.model, 80),
            "prompt_tokens": _int(row.prompt_tokens),
            "completion_tokens": _int(row.completion_tokens),
            "total_tokens": _int(row.total_tokens),
            "credits": _float(row.credits),
            "latency_ms": _int(row.latency_ms),
            "use_case": _text(row.use_case, 40),
            "error_kind": _text(row.error_kind, 40),
        } for row in recent],
        "upstream": {"supported": False, "note": UPSTREAM_NOTE},
    }


def realm_detail(db: Session, realm: str = "cn", *, days: int = 30) -> dict:
    """平台视角用量：按模型 / 按账号 / 按天。"""
    from admin.models import Account, UsageLog

    since = _since(days)
    account_ids = [row[0] for row in db.query(Account.id).filter(
        Account.platform == fb_accounts.PLATFORM,
        Account.domain == (realm or "cn")).all()]
    if not account_ids:
        return {"days": int(days), "accounts": 0, "totals": {},
                "by_model": [], "by_account": [], "by_day": [],
                "upstream": {"supported": False, "note": UPSTREAM_NOTE}}

    base = db.query(UsageLog).filter(UsageLog.platform == fb_accounts.PLATFORM,
                                     UsageLog.account_id.in_(account_ids),
                                     UsageLog.created_at >= since)

    by_model = [{
        "model": row.model or "-",
        "requests": _int(row.requests),
        "tokens": _int(row.tokens),
        "credits": _float(row.credits),
    } for row in (db.query(UsageLog.model,
                           func.count(UsageLog.id).label("requests"),
                           func.sum(UsageLog.total_tokens).label("tokens"),
                           func.sum(UsageLog.credits).label("credits"))
                  .filter(UsageLog.platform == fb_accounts.PLATFORM,
                          UsageLog.account_id.in_(account_ids),
                          UsageLog.created_at >= since)
                  .group_by(UsageLog.model).all())]
    by_model.sort(key=lambda item: item["tokens"], reverse=True)

    names = {row[0]: row[1] for row in db.query(Account.id, Account.name).filter(
        Account.id.in_(account_ids)).all()}
    by_account = [{
        "account_id": row.account_id,
        "name": names.get(row.account_id, "-"),
        "requests": _int(row.requests),
        "tokens": _int(row.tokens),
        "credits": _float(row.credits),
    } for row in (db.query(UsageLog.account_id,
                           func.count(UsageLog.id).label("requests"),
                           func.sum(UsageLog.total_tokens).label("tokens"),
                           func.sum(UsageLog.credits).label("credits"))
                  .filter(UsageLog.platform == fb_accounts.PLATFORM,
                          UsageLog.account_id.in_(account_ids),
                          UsageLog.created_at >= since)
                  .group_by(UsageLog.account_id).all())]
    by_account.sort(key=lambda item: item["tokens"], reverse=True)

    day_expr = func.date(UsageLog.created_at)
    by_day = [{
        "day": str(row.day),
        "requests": _int(row.requests),
        "tokens": _int(row.tokens),
    } for row in (db.query(day_expr.label("day"),
                           func.count(UsageLog.id).label("requests"),
                           func.sum(UsageLog.total_tokens).label("tokens"))
                  .filter(UsageLog.platform == fb_accounts.PLATFORM,
                          UsageLog.account_id.in_(account_ids),
                          UsageLog.created_at >= since)
                  .group_by(day_expr).all())]
    by_day.sort(key=lambda item: item["day"])

    total_requests = base.count()
    total_tokens = _int(base.with_entities(
        func.sum(UsageLog.total_tokens)).scalar())
    total_credits = _float(base.with_entities(func.sum(UsageLog.credits)).scalar())
    return {
        "days": int(days),
        "accounts": len(account_ids),
        "totals": {"requests": total_requests, "tokens": total_tokens,
                   "credits": total_credits},
        "by_model": by_model,
        "by_account": by_account,
        "by_day": by_day,
        "upstream": {"supported": False, "note": UPSTREAM_NOTE},
    }


def local_logs(db: Session, *, days: int = 7, page: int = 1, page_size: int = 20,
               account_id: int = 0, model: str = "",
               errors_only: bool = False) -> dict:
    """使用日志（分页）。控制台的「使用日志」页读这里。"""
    from admin.models import Account, UsageLog

    page = max(1, int(page or 1))
    page_size = max(1, min(200, int(page_size or 20)))
    since = _since(days)
    query = db.query(UsageLog).filter(
        UsageLog.platform == fb_accounts.PLATFORM,
        UsageLog.created_at >= since)
    if account_id:
        query = query.filter(UsageLog.account_id == int(account_id))
    if model:
        query = query.filter(UsageLog.model == model)
    if errors_only:
        query = query.filter(UsageLog.error_kind != "")

    total = query.count()
    rows = (query.order_by(UsageLog.id.desc())
            .offset((page - 1) * page_size).limit(page_size).all())
    names = {row[0]: row[1] for row in db.query(Account.id, Account.name).filter(
        Account.platform == fb_accounts.PLATFORM).all()}
    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": max(1, (total + page_size - 1) // page_size),
        "items": [{
            "id": row.id,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "account_id": row.account_id,
            "account": names.get(row.account_id, "-"),
            "model": _text(row.model, 80),
            "prompt_tokens": _int(row.prompt_tokens),
            "completion_tokens": _int(row.completion_tokens),
            "total_tokens": _int(row.total_tokens),
            "cached_tokens": row.cached_tokens,
            "credits": _float(row.credits),
            "ttfb_ms": _int(row.ttfb_ms),
            "latency_ms": _int(row.latency_ms),
            "use_case": _text(row.use_case, 40),
            "error_kind": _text(row.error_kind, 40),
        } for row in rows],
        "upstream": {"supported": False, "note": UPSTREAM_NOTE},
    }


def request_usage(db: Session, account, *, days: int = 1,
                  page_size: int = 20) -> dict:
    """某账号最近的请求（控制台账号详情里的「最近调用」）。"""
    from admin.models import UsageLog

    since = _since(days)
    rows = (db.query(UsageLog)
            .filter(UsageLog.platform == fb_accounts.PLATFORM,
                    UsageLog.account_id == int(account.id),
                    UsageLog.created_at >= since)
            .order_by(UsageLog.id.desc())
            .limit(max(1, min(100, int(page_size or 20)))).all())
    return {
        "account_id": account.id,
        "days": int(days),
        "items": [{
            "id": row.id,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "model": _text(row.model, 80),
            "total_tokens": _int(row.total_tokens),
            "latency_ms": _int(row.latency_ms),
            "error_kind": _text(row.error_kind, 40),
        } for row in rows],
    }