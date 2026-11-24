"""Admin IP block list and client activity statistics."""
from __future__ import annotations

import copy
import json
import logging
import os
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import case, func, or_, select, union_all
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from admin.db import get_db
from admin.ip_access import invalidate_ip_cache
from admin.models import BlockedIP, IPRequestBucket, UsageLog, ClientIdentity, ClientIdentitySeen, RequestAudit
from admin.client_ip import normalize_ip, request_ip
from admin.ip_activity import flush
from admin.money import money_avg
from admin.security import audit_secret_access, require_admin


router = APIRouter(prefix="/api/ip", tags=["ip-management"])
_logger = logging.getLogger("admin.ip_management")
_OK_KINDS = ("", "success")

#: The activity view is an expensive UNION over usage history that admins page
#: through repeatedly. Two cores cannot absorb a re-run per click, so results
#: are reused briefly and identical concurrent reads share one execution.
_ACTIVITY_CACHE_TTL = 15.0
_ACTIVITY_CACHE_MAX = 32
_activity_cache: "OrderedDict" = OrderedDict()
_activity_cache_lock = threading.Lock()
_activity_inflight: dict = {}
_activity_inflight_lock = threading.Lock()


def _activity_cache_key(*values):
    return json.dumps(values, sort_keys=True, default=str, separators=(",", ":"))


def activity_cache_status():
    """活动视图缓存观测（/api/stats/memory 汇总用）。"""
    with _activity_cache_lock:
        return {"name": "ip_management.activity", "entries": len(_activity_cache),
                "max_entries": _ACTIVITY_CACHE_MAX, "ttl_seconds": _ACTIVITY_CACHE_TTL,
                "inflight": len(_activity_inflight)}


def _activity_cache_get(key):
    now = time.monotonic()
    with _activity_cache_lock:
        entry = _activity_cache.get(key)
        if entry is None:
            return None
        expires, value = entry
        if expires <= now:
            _activity_cache.pop(key, None)
            return None
        _activity_cache.move_to_end(key)
        return copy.deepcopy(value)


def _activity_cache_put(key, value):
    with _activity_cache_lock:
        _activity_cache[key] = (time.monotonic() + _ACTIVITY_CACHE_TTL, copy.deepcopy(value))
        _activity_cache.move_to_end(key)
        while len(_activity_cache) > _ACTIVITY_CACHE_MAX:
            _activity_cache.popitem(last=False)


def _activity_counts(db: Session, start: datetime):
    """Per-IP totals as one derived table, so page rows and totals share it."""
    first = (db.query(IPRequestBucket.ip.label("ip"),
                      func.min(IPRequestBucket.first_seen).label("first_seen"))
             .group_by(IPRequestBucket.ip).subquery())
    historical = (select(UsageLog.client_ip.label("ip"),
                         func.count(UsageLog.id).label("requests"),
                         func.sum(case((func.coalesce(UsageLog.error_kind, "").in_(_OK_KINDS), 0),
                                       else_=1)).label("failed"),
                         (func.count(UsageLog.id) * 0).label("blocked_requests"),
                         func.max(UsageLog.created_at).label("last_seen"))
                  .outerjoin(first, UsageLog.client_ip == first.c.ip)
                  .where(UsageLog.created_at >= start, UsageLog.client_ip.isnot(None),
                         UsageLog.client_ip.notin_(["", "unknown"]),
                         or_(first.c.first_seen.is_(None), UsageLog.created_at < first.c.first_seen))
                  .group_by(UsageLog.client_ip))
    ingress = (select(IPRequestBucket.ip.label("ip"),
                      func.sum(IPRequestBucket.requests).label("requests"),
                      func.sum(IPRequestBucket.failed).label("failed"),
                      func.sum(IPRequestBucket.blocked).label("blocked_requests"),
                      func.max(IPRequestBucket.last_seen).label("last_seen"))
               .where(IPRequestBucket.hour >= start).group_by(IPRequestBucket.ip))
    combined = union_all(historical, ingress).subquery()
    totals = (select(combined.c.ip, func.sum(combined.c.requests).label("requests"),
                     func.sum(combined.c.failed).label("failed"),
                     func.sum(combined.c.blocked_requests).label("blocked_requests"),
                     func.max(combined.c.last_seen).label("last_seen"))
              .group_by(combined.c.ip).subquery())
    usage = (db.query(UsageLog.client_ip.label("ip"),
                      func.sum(UsageLog.credits).label("credits"),
                      # 第 5 轮：func.avg 不继承列类型，MySQL DECIMAL 上会回 Decimal → 标成 Money
                      money_avg(UsageLog.credits).label("avg_credits"),
                      func.avg(UsageLog.total_tokens).label("avg_tokens"),
                      func.count(UsageLog.total_tokens).label("known_usage"),
                      func.sum(UsageLog.total_tokens).label("tokens"))
             .filter(UsageLog.created_at >= start,
                     func.coalesce(UsageLog.error_kind, "").in_(_OK_KINDS))
             .group_by(UsageLog.client_ip).subquery())
    # Flatten counts+usage once; the router only reads these columns afterwards.
    return (select(totals.c.ip, totals.c.requests, totals.c.failed,
                   totals.c.blocked_requests, totals.c.last_seen,
                   func.coalesce(usage.c.credits, 0).label("credits"),
                   usage.c.avg_credits.label("avg_credits"),
                   usage.c.avg_tokens.label("avg_tokens"),
                   usage.c.known_usage.label("known_usage"),
                   func.coalesce(usage.c.tokens, 0).label("tokens"))
            .outerjoin(usage, totals.c.ip == usage.c.ip)
            .subquery())


def identity_items(db, ips=None, key_id=None, start=None):
    query = db.query(ClientIdentity, ClientIdentitySeen).outerjoin(ClientIdentitySeen,
        (ClientIdentity.kind == ClientIdentitySeen.kind) & (ClientIdentity.digest == ClientIdentitySeen.digest))
    if ips is not None:
        query = query.filter(ClientIdentitySeen.ip.in_(ips))
    if key_id is not None:
        query = query.filter(ClientIdentitySeen.key_id == key_id)
    if start is not None:
        query = query.filter(ClientIdentitySeen.last_seen >= start)
    grouped = {}
    for identity, seen in query.order_by(ClientIdentitySeen.last_seen.desc()).limit(2000):
        marker = (identity.kind, identity.digest)
        item = grouped.setdefault(marker, {"kind": identity.kind, "digest": identity.digest,
            "value": identity.value, "blocked": bool(identity.blocked), "note": identity.note,
            "source": "client_reported", "ips": [], "last_seen": seen.last_seen.isoformat() if seen else None})
        if seen and seen.ip not in item['ips']:
            item['ips'].append(seen.ip)
    return list(grouped.values())


class IdentityIn(BaseModel):
    kind: Literal['device_id', 'fingerprint', 'mac', 'browser']
    value: str = Field(min_length=1, max_length=128)
    blocked: bool = True
    note: str = Field(default='', max_length=255)


@router.get('/devices')
def devices(_: bool = Depends(require_admin), db: Session = Depends(get_db)):
    return {'items': identity_items(db), 'limit': 2000, 'source': 'client_reported'}


@router.post('/devices')
def update_device(body: IdentityIn, _: bool = Depends(require_admin), db: Session = Depends(get_db)):
    from admin.client_identity import normalize_identity
    try:
        value, digest = normalize_identity(body.kind, body.value)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    row = db.get(ClientIdentity, (body.kind, digest))
    if row is None:
        row = ClientIdentity(kind=body.kind, digest=digest, value=value)
        db.add(row)
    row.blocked = int(body.blocked)
    row.note = body.note.strip()
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, '设备状态发生冲突，请刷新后重试') from None
    invalidate_ip_cache()
    return {'ok': True, 'kind': row.kind, 'digest': row.digest, 'blocked': bool(row.blocked)}


def _normalize_ip(value: str) -> str:
    raw = (value or "").strip()
    try:
        return normalize_ip(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="请输入有效的 IPv4 或 IPv6 地址") from None


# ---------------------------------------------------------------------------
# 封禁到期（2026-11-24 第 3 轮）
# ---------------------------------------------------------------------------
# blocked_ips.expires_at 早已有列（db.py 幂等迁移 `_ensure_column`），检查侧
# ip_access.is_blocked_ip 也已过滤过期记录；缺的是：接口收不到时长、列表不回字段、
# 手动重新封禁会沿用上一次自动封禁留下的到期时间（手动封禁被悄悄变成限时）。

#: 定时封禁最长 366 天（再长就该用永久）。
_MAX_BLOCK_SECONDS = 366 * 86400
#: 自动封禁（note 以 auto: 开头）过期多少天后从表里删掉；0 = 不删。
_AUTO_PURGE_DAYS = max(0, int(os.getenv("IP_BLOCK_AUTO_PURGE_DAYS", "7") or 0))
#: 「顺手清理」节流：列表接口最多每 60 秒做一次。
_SWEEP_INTERVAL = 60.0
_sweep_at = 0.0
_sweep_lock = threading.Lock()
AUTO_NOTE_PREFIX = "auto:"


def to_naive_utc(value: Optional[datetime]) -> Optional[datetime]:
    """带时区 → 去时区的 UTC；不带时区的按 UTC 理解（库里一律存 naive UTC）。"""
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def iso_utc(value: Optional[datetime]) -> Optional[str]:
    """naive UTC → 带 Z 的 ISO 串（前端 new Date() 不会把它当本地时间）。"""
    if value is None:
        return None
    return to_naive_utc(value).replace(microsecond=0).isoformat() + "Z"


def resolve_expiry(expires_at: Optional[datetime], duration_seconds: Optional[int],
                   now: datetime) -> Optional[datetime]:
    """请求里的到期参数 → 落库的 naive UTC；None = 永久。

    `duration_seconds` 优先于 `expires_at`。到期时间必须晚于 now、不超过 366 天，
    否则 ValueError（中文原因）。
    """
    if duration_seconds is not None:
        seconds = int(duration_seconds)
        if seconds <= 0:
            raise ValueError("封禁时长必须大于 0")
        if seconds > _MAX_BLOCK_SECONDS:
            raise ValueError("封禁时长不能超过 366 天，需要更久请选永久")
        return now + timedelta(seconds=seconds)
    value = to_naive_utc(expires_at)
    if value is None:
        return None
    if value <= now:
        raise ValueError("到期时间必须晚于当前时间")
    if value - now > timedelta(seconds=_MAX_BLOCK_SECONDS):
        raise ValueError("到期时间不能超过 366 天，需要更久请选永久")
    return value


def block_is_active(enabled, expires_at: Optional[datetime], now: datetime) -> bool:
    """与 ip_access.is_blocked_ip 同一口径：启用 且（永久 或 未到期）。"""
    if not enabled:
        return False
    value = to_naive_utc(expires_at)
    return value is None or value > now


def is_auto_note(note: Optional[str]) -> bool:
    """风控自动封禁的备注：`auto:` 开头（忽略前导空白与大小写，与前端 isAutoBlock 一致）。"""
    return str(note or "").strip().lower().startswith(AUTO_NOTE_PREFIX)


class BlockedIPIn(BaseModel):
    ip: str = Field(min_length=1, max_length=64)
    note: str = Field(default="", max_length=255)
    #: 到期时间（ISO，建议带 Z）。不传 / null = 永久。
    expires_at: Optional[datetime] = None
    #: 或者给时长（秒），优先于 expires_at。
    duration_seconds: Optional[int] = Field(default=None, ge=1, le=_MAX_BLOCK_SECONDS)


class BlockedIPPatch(BaseModel):
    enabled: Optional[bool] = None
    note: Optional[str] = Field(default=None, max_length=255)
    expires_at: Optional[datetime] = None
    duration_seconds: Optional[int] = Field(default=None, ge=1, le=_MAX_BLOCK_SECONDS)
    #: true = 改成永久（清掉到期时间）。
    permanent: Optional[bool] = None


class BlockedBatchDeleteIn(BaseModel):
    """批量删除：按 ids，或按来源（只认 `auto:` 前缀）。两者都给时取并集。"""
    ids: list[int] = Field(default_factory=list)
    note_prefix: Optional[str] = Field(default=None, max_length=16)
    #: 只删已失效（已解除或已过期）的记录。
    only_inactive: bool = False


def _blocked_item(row: BlockedIP, now: Optional[datetime] = None) -> dict:
    now = now or datetime.utcnow()
    return {
        "id": row.id,
        "ip": row.ip,
        "enabled": bool(row.enabled),
        "note": row.note or "",
        "expires_at": iso_utc(row.expires_at),
        "expired": bool(row.expires_at is not None and to_naive_utc(row.expires_at) <= now),
        "active": block_is_active(row.enabled, row.expires_at, now),
        "source": "auto" if is_auto_note(row.note) else "manual",
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _expiry_or_400(expires_at, duration_seconds, now):
    try:
        return resolve_expiry(expires_at, duration_seconds, now)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


def _sweep_expired(db: Session, now: datetime, force: bool = False) -> None:
    """顺手清理：过期的启用记录置为已解除；过期很久的自动封禁直接删除。

    检查侧（is_blocked_ip）本来就过滤过期记录，这里只是让列表状态与实际一致、
    不让自动封禁无限堆积。失败只打日志。
    """
    global _sweep_at
    with _sweep_lock:
        mono = time.monotonic()
        if not force and mono < _sweep_at:
            return
        _sweep_at = mono + _SWEEP_INTERVAL
    try:
        changed = 0
        expired = (db.query(BlockedIP)
                   .filter(BlockedIP.expires_at.isnot(None), BlockedIP.expires_at <= now).all())
        cutoff = now - timedelta(days=_AUTO_PURGE_DAYS) if _AUTO_PURGE_DAYS else None
        for row in expired:
            if cutoff is not None and is_auto_note(row.note) and row.expires_at <= cutoff:
                db.delete(row)
                changed += 1
            elif row.enabled:
                row.enabled = 0
                changed += 1
        if changed:
            db.commit()
            invalidate_ip_cache()
    except Exception as exc:  # noqa: BLE001 - 清理失败不能影响列表
        db.rollback()
        _logger.warning("清理过期封禁失败：%s", exc)


def _client_details(db: Session, ips: list[str], start: datetime) -> dict[str, dict]:
    """Read bounded metadata for the current page, with no per-IP queries."""
    if not ips:
        return {}
    details = {ip: {"user_agent": "", "device_ids": [], "fingerprints": [],
                    "macs": [], "identity_source": "client_reported"} for ip in ips}
    latest = (db.query(RequestAudit.client_ip.label("ip"), func.max(RequestAudit.id).label("id"))
              .filter(RequestAudit.client_ip.in_(ips), RequestAudit.started_at >= start)
              .group_by(RequestAudit.client_ip).subquery())
    for ip, ua in (db.query(RequestAudit.client_ip, RequestAudit.user_agent)
                   .join(latest, RequestAudit.id == latest.c.id).all()):
        details[ip]["user_agent"] = ua or ""
    identities = (db.query(ClientIdentitySeen.ip, ClientIdentity.kind, ClientIdentity.value)
                  .join(ClientIdentity, (ClientIdentity.kind == ClientIdentitySeen.kind) &
                        (ClientIdentity.digest == ClientIdentitySeen.digest))
                  .filter(ClientIdentitySeen.ip.in_(ips), ClientIdentitySeen.last_seen >= start)
                  .order_by(ClientIdentitySeen.last_seen.desc()).limit(5000).all())
    fields = {"device_id": "device_ids", "fingerprint": "fingerprints", "mac": "macs"}
    for ip, kind, value in identities:
        field = fields.get(kind)
        if field and value not in details[ip][field] and len(details[ip][field]) < 3:
            details[ip][field].append(value)
    return details


@router.get("/blocked")
def list_blocked_ips(_: bool = Depends(require_admin), db: Session = Depends(get_db)):
    now = datetime.utcnow()
    _sweep_expired(db, now)
    rows = db.query(BlockedIP).order_by(BlockedIP.enabled.desc(), BlockedIP.created_at.desc()).all()
    return {"items": [_blocked_item(row, now) for row in rows], "expiry_supported": True,
            "auto_purge_days": _AUTO_PURGE_DAYS}


@router.post("/blocked")
def block_ip(body: BlockedIPIn, _: bool = Depends(require_admin), db: Session = Depends(get_db)):
    ip = _normalize_ip(body.ip)
    now = datetime.utcnow()
    # 每次封禁都按本次请求重设到期：不传 = 永久。原来不碰 expires_at，手动封禁一个
    # 刚被风控自动封过的 IP 时会沿用自动封禁的到期时间，到点悄悄解封。
    expires_at = _expiry_or_400(body.expires_at, body.duration_seconds, now)
    row = db.query(BlockedIP).filter(BlockedIP.ip == ip).first()
    if row is None:
        row = BlockedIP(ip=ip, enabled=1, note=body.note.strip(), expires_at=expires_at)
        db.add(row)
    else:
        row.enabled = 1
        row.note = body.note.strip()
        row.expires_at = expires_at
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        row = db.query(BlockedIP).filter(BlockedIP.ip == ip).first()
        if row is None:
            raise HTTPException(status_code=409, detail="该 IP 状态发生冲突，请重试") from None
        row.enabled = 1
        row.note = body.note.strip()
        row.expires_at = expires_at
        db.commit()
    db.refresh(row)
    invalidate_ip_cache(ip)
    return {"item": _blocked_item(row, now)}


@router.patch("/blocked/{blocked_id}")
def patch_blocked_ip(blocked_id: int, body: BlockedIPPatch,
                     _: bool = Depends(require_admin), db: Session = Depends(get_db)):
    row = db.query(BlockedIP).filter(BlockedIP.id == blocked_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="封禁记录不存在")
    now = datetime.utcnow()
    if body.enabled is not None:
        row.enabled = int(body.enabled)
    if body.note is not None:
        row.note = body.note.strip()
    if body.permanent:
        row.expires_at = None
    elif body.expires_at is not None or body.duration_seconds is not None:
        row.expires_at = _expiry_or_400(body.expires_at, body.duration_seconds, now)
    elif body.enabled and row.expires_at is not None and to_naive_utc(row.expires_at) <= now:
        # 「重新封禁」一条已过期的记录：不清掉旧到期时间的话启用了也立刻失效。
        row.expires_at = None
    db.commit()
    db.refresh(row)
    invalidate_ip_cache(row.ip)
    return {"item": _blocked_item(row, now)}


@router.delete("/blocked/{blocked_id}")
def delete_blocked_ip(blocked_id: int, _: bool = Depends(require_admin), db: Session = Depends(get_db)):
    row = db.query(BlockedIP).filter(BlockedIP.id == blocked_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="封禁记录不存在")
    ip = row.ip
    db.delete(row)
    db.commit()
    invalidate_ip_cache(ip)
    return {"id": blocked_id, "ok": True}


def select_batch_delete(rows, ids, note_prefix, only_inactive, now) -> list:
    """批量删除要删哪些行（纯函数，便于单测）。"""
    wanted = {int(i) for i in (ids or [])}
    by_prefix = bool(note_prefix)
    picked = []
    for row in rows:
        hit = row.id in wanted or (by_prefix and is_auto_note(row.note))
        if not hit:
            continue
        if only_inactive and block_is_active(row.enabled, row.expires_at, now):
            continue
        picked.append(row)
    return picked


@router.post("/blocked/batch-delete")
def batch_delete_blocked(body: BlockedBatchDeleteIn, request: Request,
                         _: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """一次删除多条封禁记录（「清理自动封禁」用，替代前端逐条 DELETE）。

    `note_prefix` 只接受 `auto:`（大小写不敏感）：不提供「按任意前缀删」，
    防止一个空串 / 常见词把手动封禁整表清掉。每次调用写一条审计。
    """
    prefix = (body.note_prefix or "").strip().lower()
    if prefix and prefix != AUTO_NOTE_PREFIX:
        raise HTTPException(status_code=400, detail="note_prefix 只支持 auto:（清理自动封禁）")
    if len(body.ids) > 2000:
        raise HTTPException(status_code=400, detail="一次最多删除 2000 条")
    if not body.ids and not prefix:
        raise HTTPException(status_code=400, detail="请提供要删除的 ids，或 note_prefix=auto:")
    now = datetime.utcnow()
    query = db.query(BlockedIP)
    if not prefix:
        query = query.filter(BlockedIP.id.in_(list({int(i) for i in body.ids})))
    rows = select_batch_delete(query.all(), body.ids, prefix, body.only_inactive, now)
    deleted = [row.id for row in rows]
    active = sum(1 for row in rows if block_is_active(row.enabled, row.expires_at, now))
    for row in rows:
        db.delete(row)
    db.commit()
    invalidate_ip_cache()
    missing = sorted({int(i) for i in body.ids} - set(deleted)) if body.ids else []
    audit_secret_access(request, "ip_block_batch_delete",
                        "deleted=%d active=%d prefix=%s ids=%s" % (
                            len(deleted), active, prefix or "-",
                            ",".join(str(i) for i in deleted[:20]) + ("…" if len(deleted) > 20 else "")))
    return {"ok": True, "deleted": len(deleted), "deleted_ids": deleted,
            "active_deleted": active, "not_found_ids": missing}


@router.get("/activity")
def ip_activity(
    request: Request,
    days: int = Query(default=1, ge=1, le=90),
    sort: Literal["requests", "failed", "blocked_requests", "credits", "tokens", "last_seen", "ip"] = "requests",
    direction: Literal["asc", "desc"] = "desc",
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    min_requests: int = Query(default=0, ge=0, le=1000000),
    max_avg_tokens: Optional[float] = Query(default=None, ge=0, le=1000000000),
    max_avg_credits: Optional[float] = Query(default=None, ge=0, le=1000000000),
    ip: Optional[str] = Query(default=None, max_length=64),
    _: bool = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Ingress counts, with usage history before ingress tracking began."""
    flush()
    start = (datetime.utcnow() - timedelta(days=days)).replace(minute=0, second=0, microsecond=0)
    cache_key = _activity_cache_key(days, sort, direction, limit, offset, min_requests,
                                    max_avg_tokens, max_avg_credits, ip)
    cached = _activity_cache_get(cache_key)
    if cached is not None:
        return cached
    # The same UNION is needed twice (page rows + totals). Aggregate once into a
    # derived table and reuse it: this is the single most expensive query on the
    # log/IP panel and the box only has two cores.
    counts = _activity_counts(db, start)
    credits = func.coalesce(counts.c.credits, 0)
    tokens = func.coalesce(counts.c.tokens, 0)
    sorts = {"requests": counts.c.requests, "failed": counts.c.failed,
             "blocked_requests": counts.c.blocked_requests, "credits": credits,
             "tokens": tokens, "last_seen": counts.c.last_seen, "ip": counts.c.ip}
    order = sorts[sort].asc() if direction == "asc" else sorts[sort].desc()
    conditions = [counts.c.requests >= min_requests]
    if max_avg_tokens is not None:
        conditions.append(counts.c.avg_tokens <= max_avg_tokens)
    if max_avg_credits is not None:
        conditions.extend([counts.c.known_usage > 0, counts.c.avg_credits <= max_avg_credits])
    if ip:
        try:
            normalized = normalize_ip(ip)
        except ValueError:
            normalized = None
        conditions.append(counts.c.ip == normalized if normalized
                          else counts.c.ip.contains(ip, autoescape=True))
    page = db.query(counts).filter(*conditions) \
        .order_by(order, counts.c.ip.asc()).offset(offset).limit(limit)
    rows = page.all()
    # Totals must reflect every active filter and ignore the page window.
    totals = db.query(func.count(), func.coalesce(func.sum(counts.c.requests), 0)) \
        .select_from(counts).filter(*conditions).one()
    _now = datetime.utcnow()
    blocked_rows = db.query(BlockedIP).filter(
        BlockedIP.enabled == 1,
        or_(BlockedIP.expires_at.is_(None), BlockedIP.expires_at > _now)).all()
    blocked_by_ip = {row.ip: row for row in blocked_rows}
    client_details = _client_details(db, [row.ip for row in rows], start)
    items = []
    for row in rows:
        ban = blocked_by_ip.get(row.ip)
        items.append({
            "ip": row.ip, "requests": int(row.requests or 0),
            "failed": int(row.failed or 0), "blocked_requests": int(row.blocked_requests or 0),
            "credits": round(float(row.credits or 0), 4), "tokens": int(row.tokens or 0),
            "avg_tokens": round(float(row.avg_tokens), 1) if row.avg_tokens is not None else None,
            "avg_credits": round(float(row.avg_credits), 6) if row.avg_credits is not None else None,
            "known_usage": int(row.known_usage or 0),
            "last_seen": row.last_seen.isoformat() if row.last_seen else None,
            "blocked": ban is not None, "blocked_id": ban.id if ban else None,
            "client_details": client_details[row.ip],
        })
    payload = {
        "days": days,
        "sort": sort, "direction": direction,
        "items": items,
        "total_ips": int(totals[0]), "total_requests": int(totals[1]),
        "current_ip": request_ip(request), "offset": offset, "limit": limit,
        "blocked_count": len(blocked_by_ip),
    }
    _activity_cache_put(cache_key, payload)
    return payload
