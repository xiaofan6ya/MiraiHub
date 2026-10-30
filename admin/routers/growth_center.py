"""成长中心：账号快照、刷新状态、分页记录与有明确入口的单次操作。"""
import json
import threading
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from admin.backend import AccountSession
from admin.db import SessionLocal, get_db
from admin.growth_center import (fields, identity, load_snapshot, record_result,
                                  snapshot_key, stamp, store_snapshot, update_snapshot)
from admin.growth_plan import _DAY_TZ, _Lease, _read, _write
from admin.models import Account, GrowthPlanRecord, Schedule, SystemSetting
from admin.security import require_admin
from admin.schedule_timing import cycle_label

router = APIRouter(prefix="/api/growth-center", tags=["growth-center"],
                   dependencies=[Depends(require_admin)])
_jobs = {}
_jobs_lock = threading.Lock()
_refresh_slots = threading.BoundedSemaphore(2)


def _account(db, aid):
    account = db.get(Account, aid)
    if account is None:
        raise HTTPException(404, "账号不存在")
    return account


def _status(aid):
    with _jobs_lock:
        return dict(_jobs.get(aid) or {"running": False})


@router.get("/accounts")
def list_accounts(db: Session = Depends(get_db)):
    from admin.scheduler import _account_label
    rows = db.query(Account).order_by(Account.id).all()
    keys = [snapshot_key(a) for a in rows]
    cached = {s.key: s.value for s in db.query(SystemSetting).filter(SystemSetting.key.in_(keys))} if keys else {}
    items = []
    for account in rows:
        try:
            snapshot = json.loads(cached.get(snapshot_key(account), "{}"))
            sections = snapshot.get("sections", {})
        except (ValueError, AttributeError):
            sections = {}
        streak = (sections.get("streak", {}).get("data", {}).get("streak") or {}).get("days")
        items.append({"id": account.id, "name": _account_label(account), "status": account.status,
                      "streak_days": streak,
                      "energy": sections.get("energy", {}).get("data", {}).get("balance")})
    return {"items": items}


@router.get("/accounts/{aid}")
def overview(aid: int, db: Session = Depends(get_db)):
    from admin.scheduler import _account_label
    account = _account(db, aid)
    latest = (db.query(GrowthPlanRecord).filter(GrowthPlanRecord.account_id == aid)
              .order_by(GrowthPlanRecord.id.desc()).first())
    schedule = db.query(Schedule).filter(Schedule.task == "run_growth_plan").order_by(Schedule.id).first()
    snapshot = load_snapshot(db, account)
    job = _status(aid)
    job["running"] = job.get("running") or bool(snapshot.get("refresh_running") and snapshot.get("refresh_expires_at", 0) > time.time())
    return {"account": {"id": aid, "name": _account_label(account), "status": account.status},
            "snapshot": snapshot, "refresh": job,
            "latest_run": _record(latest) if latest else None,
            "schedule": {"id": schedule.id, "enabled": bool(schedule.enabled),
                         "interval_minutes": schedule.interval_minutes,
                         "cycle_label": cycle_label(schedule),
                         "next_run_at": _iso(schedule.next_run_at)} if schedule else None,
            "today": datetime.now(_DAY_TZ).date().isoformat()}


@router.post("/accounts/{aid}/refresh")
def refresh(aid: int, db: Session = Depends(get_db)):
    account = _account(db, aid)
    snapshot = load_snapshot(db, account)
    if snapshot.get("refresh_running") and snapshot.get("refresh_expires_at", 0) > time.time():
        return {"ok": True, "running": True}
    with _jobs_lock:
        if (_jobs.get(aid) or {}).get("running"):
            return {"ok": True, "running": True}
        if not _refresh_slots.acquire(blocking=False):
            raise HTTPException(429, "正在刷新其他账号，请稍后重试")
        for key in list(_jobs):
            if not _jobs[key].get("running") and time.time() - _jobs[key].get("finished_at", 0) > 300:
                _jobs.pop(key, None)
        _jobs[aid] = {"running": True}
    snapshot.update(refresh_running=True, refresh_expires_at=time.time() + 240)
    try:
        store_snapshot(db, account, snapshot)
    except Exception:
        with _jobs_lock:
            _jobs.pop(aid, None)
        _refresh_slots.release()
        raise

    def worker():
        try:
            with SessionLocal() as worker_db:
                account = worker_db.get(Account, aid)
                if account is None:
                    return
                original_auth = account.auth_json
                last_auth = original_auth
                snapshot = load_snapshot(worker_db, account)
                with AccountSession(original_auth) as session:
                    def save(value):
                        nonlocal last_auth
                        # 刷新期间用户可能重新登录：不覆盖新的凭证。
                        worker_db.refresh(account)
                        if account.auth_json == last_auth:
                            account.auth_json = session.updated_json()
                            last_auth = account.auth_json
                        value["refresh_running"] = True
                        value["refresh_expires_at"] = time.time() + 240
                        store_snapshot(worker_db, account, value)
                    update_snapshot(session, snapshot, save)
                    snapshot["refresh_running"] = False
                    store_snapshot(worker_db, account, snapshot)
            with _jobs_lock:
                _jobs[aid] = {"running": False, "finished_at": time.time()}
        except Exception as exc:
            try:
                with SessionLocal() as cleanup_db:
                    account = cleanup_db.get(Account, aid)
                    if account:
                        snapshot = load_snapshot(cleanup_db, account)
                        snapshot["refresh_running"] = False
                        store_snapshot(cleanup_db, account, snapshot)
            except Exception:
                pass
            with _jobs_lock:
                _jobs[aid] = {"running": False, "finished_at": time.time(),
                              "error": f"刷新失败（{type(exc).__name__}），可稍后重试"}
        finally:
            with _jobs_lock:
                if (_jobs.get(aid) or {}).get("running"):
                    _jobs[aid] = {"running": False, "finished_at": time.time()}
            _refresh_slots.release()

    try:
        threading.Thread(target=worker, daemon=True, name=f"wb-growth-view-{aid}").start()
    except Exception:
        with _jobs_lock:
            _jobs.pop(aid, None)
        _refresh_slots.release()
        raise HTTPException(503, "刷新任务无法启动")
    return {"ok": True, "running": True}


def _iso(value):
    return value.replace(tzinfo=timezone.utc).isoformat() if value else None


def _record(row):
    try:
        result = json.loads(row.result_json)
    except (TypeError, ValueError):
        result = {}
    return {"id": row.id, "source": row.source, "action": row.action,
            "ok": bool(row.ok), "credit": row.credit, "created_at": _iso(row.created_at),
            "steps": result.get("steps", []), "message": result.get("reason", "")}


@router.get("/accounts/{aid}/records")
def records(aid: int, before: int | None = None, limit: int = Query(20, ge=1, le=50),
            db: Session = Depends(get_db)):
    _account(db, aid)
    query = db.query(GrowthPlanRecord).filter(GrowthPlanRecord.account_id == aid)
    if before:
        query = query.filter(GrowthPlanRecord.id < before)
    rows = query.order_by(GrowthPlanRecord.id.desc()).limit(limit + 1).all()
    more = len(rows) > limit
    return {"items": [_record(r) for r in rows[:limit]], "has_more": more,
            "next_before": rows[limit - 1].id if more else None}


@router.get("/accounts/{aid}/official-records")
def official_records(aid: int, kind: str = "travel", page: int = Query(1, ge=1, le=1000),
                     db: Session = Depends(get_db)):
    paths = {"travel": "/activity/growth/buddy/travel/records",
             "draws": "/activity/growth/lottery/draws",
             "rewards": "/activity/growth/lottery/rewards"}
    if kind not in paths:
        raise HTTPException(400, "未知记录类型")
    account = _account(db, aid)
    with AccountSession(account.auth_json) as sess:
        result = sess._growth("GET", f"{paths[kind]}?page={page}&page_size=10")
        account.auth_json = sess.updated_json()
        db.commit()
    if not result.get("ok"):
        raise HTTPException(502, "官方领取记录暂时不可用，请稍后重试")
    payload = result.get("data") or {}
    items = payload.get("records" if kind == "travel" else "items") or []
    safe = []
    for item in items:
        row = fields(item, "id record_id draw_id reward_id prize_name prize_type credit_amount reward_credit "
                           "credit_granted energy_granted cards_granted chances_granted created_at drawn_at "
                           "claimed_at acquired_at arrive_at depart_at travel_date status prize_code")
        row["location"] = fields(item.get("location"), "name code")
        row["prize"] = fields(item.get("prize"), "prize_name name prize_type type credit_amount")
        safe.append(row)
    return {"items": safe, "page": page, "total": payload.get("total", len(safe)), "page_size": 10}


class ActionIn(BaseModel):
    action: str
    tier: str = Field(default="", max_length=30)


@router.post("/accounts/{aid}/action")
def action(aid: int, body: ActionIn, db: Session = Depends(get_db)):
    if body.action not in ("redeem", "open", "gift", "draw", "checkin"):
        raise HTTPException(400, "未知成长操作")
    account = _account(db, aid)
    if account.status != "active":
        raise HTTPException(400, "请先启用账号或重新登录")
    lease = _Lease(db)
    if not lease.acquire():
        raise HTTPException(409, "成长巡检正在执行，请等待完成后操作")
    labels = {"redeem": "连登奖励兑换", "open": "开启盲盒", "gift": "领取礼物",
              "draw": "限时抽奖", "checkin": "每日打卡"}
    label = labels[body.action]
    today = datetime.now(_DAY_TZ).date().isoformat()
    key = "growth_plan:account:" + identity(account)
    state = _read(db, key)
    state["identity"] = identity(account)
    snapshot = load_snapshot(db, account)
    try:
        with AccountSession(account.auth_json) as sess:
            try:
                if body.action == "redeem":
                    current = sess.growth_streak()
                    tiers = (current.get("data") or {}).get("redemption_status") or {}
                    if not current.get("ok"):
                        raise HTTPException(502, "无法核对奖励档位，请稍后重试")
                    if tiers.get(f"tier_{body.tier}_status") != "available":
                        raise HTTPException(400, "该档位尚未达标或本月已领取")
                    token = "redeem-" + uuid.uuid5(uuid.NAMESPACE_URL, identity(account) + ":" + today[:7] + ":" + body.tier).hex
                    result = sess.growth_redeem(body.tier, token)
                elif body.action == "open":
                    quota = sess.growth_buddy_quota()
                    if not quota.get("ok") or int((quota.get("data") or {}).get("affordable") or 0) < 1:
                        raise HTTPException(400, "能量不足或盲盒额度暂时不可用")
                    state["box_attempt_day"] = today
                    _write(db, key, state)
                    result = sess.growth_buddy_open(1)
                    if result.get("ok"):
                        state["box_confirmed_day"] = today
                elif body.action == "gift":
                    if sess.travel_status().get("state") != "arrived":
                        raise HTTPException(400, "宠物尚未到站或礼物已领取")
                    result = sess.travel_claim()
                elif body.action == "draw":
                    lottery = sess._growth("GET", "/activity/growth/lottery/summary")
                    if not lottery.get("ok") or not ((lottery.get("data") or {}).get("module") or {}).get("enabled"):
                        raise HTTPException(400, "限时抽奖暂未开启")
                    chance = sess.growth_lottery_chances()
                    if not chance.get("ok") or not (chance.get("data") or {}).get("balance"):
                        if not state.get("draw_token"):
                            raise HTTPException(400, "没有可用免费抽奖次数")
                    state.setdefault("draw_token", "draw-" + uuid.uuid4().hex)
                    _write(db, key, state)
                    result = sess.growth_lottery_draw(state["draw_token"])
                    if result.get("ok"):
                        state.pop("draw_token", None)
                        if state.get("draw_day") != today:
                            state["draw_day"], state["draw_count"] = today, 0
                        state["draw_count"] = int(state.get("draw_count") or 0) + 1
                else:
                    status = sess.get_checkin_status()
                    if status.get("today_checked_in"):
                        raise HTTPException(400, "今日已经打卡领取积分")
                    if status.get("active") is not True:
                        raise HTTPException(400, "签到活动未开启或状态暂时不可用")
                    result = sess.claim_daily_checkin()
                    if result.get("ok"):
                        state["checkin_day"] = today
                data = result.get("data") or result
                credit = int(data.get("credit_granted") or data.get("reward_credit") or data.get("credit") or 0) if result.get("ok") else 0
                if credit > 0:
                    from admin.routers.accounts import _sync_credit_snapshot
                    try:
                        balance = sess.fetch_balance()
                        account.balance_total = int(balance.get("total") or 0)
                        account.balance_remain = int(balance.get("remain") or 0)
                        account.last_sync_at = datetime.utcnow()
                        result["journal_balance_after"] = account.balance_remain
                        _sync_credit_snapshot(account, sess.fetch_credit_details())
                    except Exception:
                        snapshot["resources_stale"] = True
                message = "已完成" if result.get("ok") else "操作未确认，请刷新状态后核对"
                if body.action == "redeem" and data.get("cards_overflow"):
                    message += f"，补登卡溢出 {data['cards_overflow']} 张"
                if body.action == "open" and result.get("ok"):
                    buddies = [fields(v.get("template"), "name rarity") for v in data.get("results", [])]
                    message += "：" + "、".join(v.get("name", "Buddy") for v in buddies)
                if body.action == "draw" and result.get("ok"):
                    prize = data.get("prize") or data
                    message += "：" + str(prize.get("prize_name") or prize.get("name") or "请查看官方抽奖记录")
                response = {"ok": bool(result.get("ok")), "credit": credit, "reason": message,
                            "balance_after": result.get("journal_balance_after"),
                            "steps": [{"step": label, "ok": bool(result.get("ok")), "message": message, "reward": credit}]}
                record_result(db, aid, response, source="manual", action=label)
                _write(db, key, state)
            finally:
                account.auth_json = sess.updated_json()
                db.commit()
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        response = {"ok": False, "credit": 0, "reason": f"操作未确认（{type(exc).__name__}），请刷新后核对"}
        record_result(db, aid, response, source="manual", action=label)
    finally:
        snapshot["checked_at"] = None
        try:
            store_snapshot(db, account, snapshot)
        finally:
            lease.release()
    return response