"""成长中心快照与审计记录。读取页面不触发对话/领奖。"""
import hashlib
import json
from datetime import datetime, timezone

from admin.models import Account, GrowthPlanRecord, Schedule, SystemSetting


def identity(account):
    return hashlib.sha256(str(account.uid or f"account:{account.id}").encode()).hexdigest()[:32]


def snapshot_key(account):
    return "growth_plan:snapshot:" + identity(account)


def load_snapshot(db, account):
    row = db.get(SystemSetting, snapshot_key(account))
    try:
        data = json.loads(row.value) if row else {}
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def merge_snapshot(current, incoming):
    """按分项时间合并，刷新页和巡检并行时不丢掉另一方刚获取的分项。"""
    merged = {**current, **incoming}
    sections = dict(current.get("sections") or {})
    for key, value in (incoming.get("sections") or {}).items():
        if value.get("updated_at", "") >= sections.get(key, {}).get("updated_at", ""):
            sections[key] = value
    merged["sections"] = sections
    merged["updated_at"] = max(current.get("updated_at", ""), incoming.get("updated_at", ""))
    return merged


def store_snapshot(db, account, snapshot):
    key = snapshot_key(account)
    row = db.get(SystemSetting, key)
    if row is None:
        row = SystemSetting(key=key)
        db.add(row)
    else:
        db.refresh(row)
        try:
            snapshot = merge_snapshot(json.loads(row.value), snapshot)
        except (TypeError, ValueError, AttributeError):
            pass
    row.value = json.dumps(snapshot, ensure_ascii=False)
    db.commit()


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record_result(db, account_id, result, *, source="schedule", action="巡检"):
    import uuid
    result = dict(result)
    result.setdefault("credit_event_key", "reward:" + uuid.uuid4().hex)
    record = GrowthPlanRecord(account_id=account_id, source=source, action=action,
                             ok=int(bool(result.get("ok"))), credit=int(result.get("credit") or 0),
                             result_json=json.dumps(result, ensure_ascii=False))
    db.add(record)
    db.flush()
    from admin.credit_journal import record_rewards
    record_rewards(db, record)
    db.commit()
    return record.id


def import_last_run(db):
    """为升级前已运行的实例补入最后一轮结果；更早结果当时未存，无法恢复。"""
    rows = db.query(Schedule).filter(Schedule.task == "run_growth_plan").all()
    known = {aid for (aid,) in db.query(GrowthPlanRecord.account_id).distinct()}
    accounts = {aid for (aid,) in db.query(Account.id)}
    for schedule in rows:
        try:
            result = json.loads(schedule.last_result or "{}")
        except (ValueError, TypeError):
            continue
        for detail in result.get("detail") or []:
            aid = detail.get("account_id")
            if aid in known or aid not in accounts:
                continue
            safe = {"ok": bool(detail.get("ok")), "credit": int(detail.get("credit") or 0),
                    "steps": [fields(step, "step ok message reward") for step in detail.get("steps", [])],
                    "reason": str(detail.get("reason") or "")[:5000]}
            db.add(GrowthPlanRecord(account_id=aid, source="schedule", action="升级前最后巡检",
                                   ok=int(safe["ok"]), credit=safe["credit"],
                                   result_json=json.dumps(safe, ensure_ascii=False),
                                   created_at=schedule.last_run_at or datetime.utcnow()))
            known.add(aid)
    db.commit()


def fields(value, names):
    value = value if isinstance(value, dict) else {}
    return {name: value[name] for name in names.split() if name in value}


def clean_section(name, value):
    """白名单投影，避免快照/领奖记录意外包含邮箱、物流地址或凭证。"""
    value = value if isinstance(value, dict) else {}
    if name == "heatmap":
        return {"cells": [fields(v, "date score has_new_buddy") for v in value.get("cells", [])],
                "today": fields(value.get("today"), "date score is_active status_text"),
                "range": fields(value.get("range"), "start_date end_date days"),
                "updated_at": value.get("updated_at", "")}
    if name == "streak":
        redemption = fields(value.get("redemption_status"),
            "tier_7d_status tier_14d_status tier_28d_status tier_7d_count tier_14d_count tier_28d_count remaining_days")
        tiers = (value.get("redemption_status") or {}).get("tiers") or []
        redemption["tiers"] = [fields(v, "tier days credit energy cards chances") for v in tiers]
        for tier in tiers:
            key = f"tier_{tier.get('tier', '')}_status"
            if key in (value.get("redemption_status") or {}):
                redemption[key] = value["redemption_status"][key]
        return {"streak": fields(value.get("streak"), "days month_total_days next_tier next_tier_remaining makeup_dates"),
                "redemption_status": redemption,
                "makeup_cards": fields(value.get("makeup_cards"), "balance max"),
                "timezone": value.get("timezone", "Asia/Shanghai")}
    if name == "buddy":
        return {"buddy": fields(value.get("buddy"), "instance_id template_id name personality rarity thumbnail_url base_static_url") or None}
    if name == "buddies":
        return {"buddies": [fields(v, "instance_id name personality rarity thumbnail_url base_static_url acquired_at source current_buddy")
                            for v in value.get("buddies", [])], "count": value.get("count", 0)}
    if name == "travel":
        return {**fields(value, "state depart_at arrive_at server_now daily_limit_reached reward_credit"),
                "location": fields(value.get("location"), "id code name duration_hours")}
    if name == "lottery":
        return {"chances": value.get("chances", 0), "module": fields(value.get("module"), "enabled")}
    columns = {
        "energy": "balance total_consumed total_earned",
        "quota": "balance affordable cost_per_open max_open_count",
        "profile": "level level_name completed total max_level",
        "redeem": "starter_count advanced_count legendary_count remaining_days month_total_days",
        "checkin": "active today_checked_in daily_credit today_credit streak_days end_time",
    }
    return fields(value, columns.get(name, ""))


SECTIONS = {
    "heatmap": ("全年活跃", "/activity/growth/heatmap"),
    "streak": ("连续登录", "/activity/growth/streak"),
    "energy": ("能量", "/activity/growth/energy"),
    "quota": ("盲盒额度", "/activity/growth/buddy/quota"),
    "buddy": ("当前宠物", "/activity/growth/buddy/info"),
    "travel": ("旅行礼物", "/activity/growth/buddy/travel/status"),
    "lottery": ("抽奖次数", "/activity/growth/lottery/summary"),
    "redeem": ("兑换统计", "/activity/growth/redeem/summary"),
    "buddies": ("宠物图鉴", "/activity/growth/buddy/list"),
    "profile": ("成长等级", "/v2/activity/growth/profile"),
}


def observe(snapshot, name, data):
    snapshot.setdefault("sections", {})[name] = {"data": clean_section(name, data), "updated_at": stamp()}
    snapshot.setdefault("errors", {}).pop(name, None)
    snapshot["updated_at"] = stamp()


def update_snapshot(sess, snapshot, save):
    for name, (label, path) in SECTIONS.items():
        try:
            result = sess._growth("GET", path)
            if result.get("ok"):
                observe(snapshot, name, result.get("data"))
            else:
                snapshot.setdefault("errors", {})[name] = f"{label}暂时不可用（HTTP {result.get('status', 0)}）"
        except Exception as exc:
            snapshot.setdefault("errors", {})[name] = f"{label}暂时不可用（{type(exc).__name__}）"
        save(snapshot)
    try:
        data = sess.get_checkin_status()
        if isinstance(data, dict) and "active" in data:
            observe(snapshot, "checkin", data)
        else:
            snapshot.setdefault("errors", {})["checkin"] = "签到状态暂时不可用"
    except Exception as exc:
        snapshot.setdefault("errors", {})["checkin"] = f"签到状态暂时不可用（{type(exc).__name__}）"
    snapshot["checked_at"] = stamp()
    save(snapshot)
    return snapshot