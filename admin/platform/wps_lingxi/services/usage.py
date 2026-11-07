"""WPS 灵犀的「用量详情」与「使用日志」。

## 两件事别混

| 视图 | 数据来源 | 回答什么问题 |
|---|---|---|
| 用量详情 | 上游 `credits/balance` + `credits/transaction_summary` | 智点还剩多少、怎么进出的、什么时候到期 |
| 使用日志 | 上游 `agents/run_records` + 本地 `usage_logs` | 这个号什么时候被调用过、花了多少、多久返回 |

上游字段没有稳定文档（Go 侧 struct 里字段名对得上，但返回体可能有空值），
所以这里统一走**宽松归一化**：认识的字段映射成统一结构，不认识的原样放进
`raw`，查不到就如实留空 —— **不编造**。

## 为什么还要本地账本

上游 run_records 有延迟、也可能被清理；而网关自己的 `usage_logs` 是实时的。
两个都返回，前端可以对照：上游有、本地没有 = 别人在用这个号；
本地有、上游没有 = 上游还没结算。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from admin.platform.wps_lingxi.core import client
from admin.platform.wps_lingxi.services import accounts as lingxi_accounts

logger = logging.getLogger("admin.platform.wps_lingxi.usage")


# ---------------------------------------------------------------------------
# 归一化
# ---------------------------------------------------------------------------
def _pick(row: dict, *keys, default=None):
    """按顺序取第一个非空值（上游同一个意思可能有好几个字段名）。"""
    for key in keys:
        if key in row and row[key] not in (None, "", 0, "0"):
            return row[key]
    return default


def _int(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _text(value, limit: int = 120) -> str:
    text = str(value or "").strip()
    return text[:limit]


def _normalize_transaction(row: dict) -> dict:
    """一条智点流水 → 统一结构。"""
    direction = _text(_pick(row, "direction", "type", "op_type", "kind",
                            "transaction_type", default=""))
    amount = _float(_pick(row, "credits_cost", "amount", "credits", "value",
                          "change", default=0))
    title = _text(_pick(row, "title", "name", "desc", "description", "reason",
                        "source", "biz_type", default=""))
    occurred = _text(_pick(row, "created_at", "time", "create_time", "occurred_at",
                           "transaction_time", default=""), 40)
    expire = _text(_pick(row, "expire_time", "expired_at", "expires_at",
                         default=""), 40)
    model = _text(_pick(row, "model", "model_name", default=""))
    credits_balance = _int(_pick(row, "credits_balance", "balance_after",
                                 "total_balance", default=0))
    return {
        "time": occurred,
        "direction": direction or ("expense" if amount < 0 else "income"),
        "amount": amount,
        "title": title,
        "model": model,
        "balance": credits_balance,
        "expire_at": expire,
        "raw": {key: row[key] for key in list(row)[:14]},
    }


def _normalize_run(row: dict) -> dict:
    """一条上游 run 记录 → 使用日志行。"""
    created = _text(_pick(row, "created_at", "create_time", "started_at", "time",
                          default=""), 40)
    finished = _text(_pick(row, "finished_at", "end_time", "completed_at",
                           default=""), 40)
    input_tokens = _int(_pick(row, "input_tokens", "prompt_tokens", default=0))
    output_tokens = _int(_pick(row, "output_tokens", "completion_tokens", default=0))
    total_tokens = _int(_pick(row, "total_tokens", default=0)) or (
        input_tokens + output_tokens)
    return {
        "id": _text(_pick(row, "run_id", "id", default=""), 64),
        "session_id": _text(_pick(row, "session_id", default=""), 64),
        "model": _text(_pick(row, "model_name", "model", default="")),
        "created_at": created,
        "finished_at": finished,
        "duration_ms": _int(_pick(row, "duration_ms", "latency_ms", "cost_ms",
                                   default=0)),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cached_tokens": _int(_pick(row, "cached_tokens", default=0)),
        "reasoning_tokens": _int(_pick(row, "reasoning_tokens", default=0)),
        "credits": _float(_pick(row, "credits_cost", "credits", "cost", default=0)),
        "request_id": _text(_pick(row, "client_request_id", "request_id",
                                  default=""), 64),
        "raw": {key: row[key] for key in list(row)[:14]},
    }


def _balance_summary(quota: dict) -> dict:
    """`credits/balance` 已经归一化过（client.fetch_balance），这里补齐明细字段。"""
    return {
        "total": _int((quota or {}).get("total")),
        "expiring": _int((quota or {}).get("expiring_balance")),
        "evergreen": _int((quota or {}).get("evergreen_balance")),
        "batches": (quota or {}).get("items") or [],
    }


# ---------------------------------------------------------------------------
# 用量详情
# ---------------------------------------------------------------------------
def account_detail(db: Session, account, *, days: int = 30,
                   page_size: int = 20, with_transactions: bool = True) -> dict:
    """单个账号的用量详情（上游优先，失败回落本地）。"""
    days = max(1, min(365, int(days or 30)))
    cookie = lingxi_accounts.cookie_of(account)
    base = lingxi_accounts.base_of(account)
    result = {
        "account_id": account.id,
        "name": account.name,
        "days": days,
        "balance": _balance_summary({
            "total": getattr(account, "balance_remain", 0),
            "expiring_balance": getattr(account, "credits_expiring_soon", 0),
            "evergreen_balance": getattr(account, "balance_total", 0),
        }),
        "transactions": [],
        "upstream_ok": False,
        "error": "",
    }
    if not cookie:
        result["error"] = "账号缺少 Cookie，读不到上游用量"
        return result

    try:
        quota = client.fetch_balance(cookie, base)
        result["balance"] = _balance_summary(quota)
    except client.UpstreamError as exc:
        result["error"] = exc.message[:160]
        if exc.kind == "auth":
            return result

    if with_transactions:
        try:
            page = client.fetch_transactions(cookie, base, days=days,
                                             page_size=min(100, page_size * 2))
            result["transactions"] = [_normalize_transaction(item)
                                      for item in page.get("items") or []][:100]
            result["cursors"] = {"income": page.get("income_cursor", ""),
                                 "expense": page.get("expense_cursor", "")}
            result["upstream_ok"] = True
        except client.UpstreamError as exc:
            result["error"] = result["error"] or exc.message[:160]
            logger.debug("灵犀流水读取失败 acc=%s：%s", account.id, exc.message)

    # 本地账本：上游流水缺位时至少能看出「最近花在哪里」
    result["local"] = _local_summary(db, days=days, account_id=account.id)
    return result


def _local_summary(db: Session, *, days: int, account_id: int | None = None) -> dict:
    from sqlalchemy import func

    from admin.models import Account, UsageLog

    since = datetime.utcnow() - timedelta(days=days)
    query = db.query(
        UsageLog.model,
        func.count(UsageLog.id).label("requests"),
        func.sum(UsageLog.total_tokens).label("tokens"),
        func.sum(UsageLog.prompt_tokens).label("prompt_tokens"),
        func.sum(UsageLog.completion_tokens).label("completion_tokens"),
        func.sum(UsageLog.credits).label("credits"),
    ).filter(UsageLog.platform == lingxi_accounts.PLATFORM,
             UsageLog.created_at >= since)
    if account_id:
        query = query.filter(UsageLog.account_id == int(account_id))
    rows = query.group_by(UsageLog.model).all()
    items = [{"model": row.model or "-",
              "requests": _int(row.requests),
              "tokens": _int(row.tokens),
              "prompt_tokens": _int(row.prompt_tokens),
              "completion_tokens": _int(row.completion_tokens),
              "credits": round(_float(row.credits), 2)} for row in rows]
    items.sort(key=lambda item: item["tokens"], reverse=True)
    return {
        "items": items,
        "totals": {"requests": sum(i["requests"] for i in items),
                   "tokens": sum(i["tokens"] for i in items),
                   "credits": round(sum(i["credits"] for i in items), 2)},
    }


def realm_detail(db: Session, realm: str = "", *, days: int = 30) -> dict:
    """整平台的用量详情：账号列表 + 合计 + 本地按模型聚合。"""
    accounts = lingxi_accounts.accounts_for(db, realm)
    rows = []
    for account in accounts:
        rows.append({
            "account_id": account.id,
            "name": account.name,
            "status": getattr(account, "status", "") or "",
            "balance_remain": _int(getattr(account, "balance_remain", 0)),
            "balance_total": _int(getattr(account, "balance_total", 0)),
            "expiring": _int(getattr(account, "credits_expiring_soon", 0)),
            "last_sync_at": account.last_sync_at.isoformat()
            if account.last_sync_at else None,
        })
    return {
        "platform": lingxi_accounts.PLATFORM,
        "days": max(1, min(365, int(days or 30))),
        "accounts": rows,
        "totals": {
            "accounts": len(rows),
            "active": sum(1 for row in rows if row["status"] == "active"),
            "balance_remain": sum(row["balance_remain"] for row in rows),
            "expiring": sum(row["expiring"] for row in rows),
        },
        "local": _local_summary(db, days=days),
    }


# ---------------------------------------------------------------------------
# 使用日志
# ---------------------------------------------------------------------------
def local_logs(db: Session, *, days: int = 7, page: int = 1, page_size: int = 20,
               account_id: int | None = None) -> dict:
    """本地账本的调用记录（分页，最新在前）。

    这就是「使用日志」的本地视图：网关自己记的，实时、不会丢；
    和上游 run_records 对照着看最准。
    """
    from admin.models import Account, UsageLog

    days = max(1, min(365, int(days or 7)))
    page = max(1, int(page or 1))
    page_size = max(1, min(200, int(page_size or 20)))
    since = datetime.utcnow() - timedelta(days=days)
    query = db.query(UsageLog).filter(
        UsageLog.platform == lingxi_accounts.PLATFORM,
        UsageLog.created_at >= since)
    if account_id:
        query = query.filter(UsageLog.account_id == int(account_id))
    total = query.count()
    rows = (query.order_by(UsageLog.id.desc())
            .offset((page - 1) * page_size).limit(page_size).all())
    names = {row.id: row.name for row in db.query(Account.id, Account.name)
             .filter(Account.platform == lingxi_accounts.PLATFORM).all()}
    items = []
    for row in rows:
        items.append({
            "id": row.id,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "account_id": row.account_id,
            "account": names.get(row.account_id, ""),
            "model": row.model or "",
            "prompt_tokens": _int(row.prompt_tokens),
            "completion_tokens": _int(row.completion_tokens),
            "total_tokens": _int(row.total_tokens),
            "credits": round(_float(row.credits), 2),
            "latency_ms": _int(row.latency_ms),
            "use_case": row.use_case or "",
            "status": "error" if (row.error_kind or "") else "ok",
            "error_kind": row.error_kind or "",
        })
    return {"items": items, "total": total, "page": page,
            "page_size": page_size, "days": days}


def upstream_logs(account, *, days: int = 7, page_size: int = 20,
                  offset: int = 0) -> dict:
    """上游 run 记录（使用日志的上游视图）。"""
    cookie = lingxi_accounts.cookie_of(account)
    if not cookie:
        return {"ok": False, "items": [], "error": "账号缺少 Cookie"}
    try:
        rows = client.fetch_run_records(cookie, lingxi_accounts.base_of(account),
                                        days=days, page_size=page_size,
                                        offset=offset)
    except client.UpstreamError as exc:
        return {"ok": False, "items": [], "error": exc.message[:160],
                "kind": exc.kind}
    items = [_normalize_run(row) for row in rows]
    return {"ok": True, "items": items[:page_size],
            "total": len(items), "source": "upstream"}


def request_usage(account, *, days: int = 1, page_size: int = 20,
                  offset: int = 0) -> dict:
    """控制台「上游明细」用：一次 JSON 里带上游记录 + 本地兜底。

    返回结构兼容控制台两种取法：`payload.data` 与 `payload.data.data`。
    """
    upstream = upstream_logs(account, days=days, page_size=page_size, offset=offset)
    rows = upstream.get("items") or []
    if rows:
        formatted = [{
            "requestTime": row.get("created_at") or row.get("finished_at") or "",
            "model": row.get("model") or "—",
            "credit": row.get("credits") or 0,
            "tokens": row.get("total_tokens") or 0,
            "durationMs": row.get("duration_ms") or 0,
            "inputTrunc": (row.get("request_id") or row.get("id") or "—")[:60],
            "source": "upstream",
        } for row in rows]
        return {"ok": True, "source": "upstream", "data": formatted}

    # 上游没给（无记录 / 接口不可用）→ 本地账本兜底，并说明来源
    local_rows = []
    try:
        from admin.db import SessionLocal

        with SessionLocal() as session:
            page = local_logs(session, days=max(days, 1), page=1,
                              page_size=page_size, account_id=account.id)
            local_rows = page.get("items") or []
    except Exception:  # noqa: BLE001 - 兜底失败就返回空，不抛
        local_rows = []
    formatted = [{
        "requestTime": row.get("created_at") or "",
        "model": row.get("model") or "—",
        "credit": row.get("credits") or 0,
        "tokens": row.get("total_tokens") or 0,
        "durationMs": row.get("latency_ms") or 0,
        "inputTrunc": (row.get("use_case") or "调用")[:60],
        "source": "local",
    } for row in local_rows]
    return {"ok": True, "source": "local",
            "note": upstream.get("error") or "上游未返回记录，显示本站日志",
            "data": formatted}