"""WPS 灵犀签到（任务中心的每日签到 + 可直接领取的一次性任务）。

## 上游任务中心

    GET  <base>/api/public/v1/tasks                     → data.tasks[]
    POST <base>/api/public/v1/tasks/{task_key}/claim    → data.reward_amount

每日签到键固定为 `daily_check_in`，状态只有 `claimed` / `locked` 两种
（代码仍兼容 `available`）。

## 判定链（照抄插件，顺序不能换）

1. `tasks` 401/403 或 `result=UserUnauthorized` → `expired`（登录态失效）；
2. 找不到 `daily_check_in` → 失败（如实说没有签到，不伪造成功）；
3. `status == claimed` → `done`（今日已签）；
4. 否则 claim，用**领前/领后余额差**算奖励（上游的 `reward_amount` 只作兜底）；
5. 差值 `<= 0` → 视为「幂等重复领取」，报 done 而不是 claimed。

一次性任务只在 `status == claimable` 时直接领；需要「做动作」才能领的
（专家模式 / 建群 / 下载客户端）本期不做——如实报出来，不假装签到过。
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from admin.platform.wps_lingxi.core import client
from admin.platform.wps_lingxi.services import accounts as lingxi_accounts

logger = logging.getLogger("admin.platform.wps_lingxi.checkin")

TASK_NAME = "wps_lingxi_checkin"


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _claim_date(daily: dict) -> str:
    """按上游给的档位挑日期：今天 > `available` 档 > 今天。"""
    today = _today()
    extra = daily.get("extra") if isinstance(daily.get("extra"), dict) else {}
    days = extra.get("check_in_days")
    if isinstance(days, list):
        for item in days:
            if isinstance(item, dict) and str(item.get("date") or "") == today:
                return today
        for item in days:
            if isinstance(item, dict) and str(item.get("status") or "") == "available":
                return str(item.get("date") or today)
    return today


def checkin_account(db: Session, account, *, claim_one_time: bool = True) -> dict:
    """单个账号签到。**不抛异常**，失败写进返回值。"""
    cookie = lingxi_accounts.cookie_of(account)
    if not cookie:
        return {"account_id": account.id, "ok": False, "outcome": "failed",
                "detail": "账号缺少 Cookie"}
    base = lingxi_accounts.base_of(account)

    before = None
    try:
        before = client.fetch_balance(cookie, base).get("total")
    except client.UpstreamError as exc:
        if exc.kind == "auth":
            return {"account_id": account.id, "ok": False, "outcome": "expired",
                    "detail": "登录态已失效，请重新粘贴 Cookie"}
        logger.debug("灵犀余额查询失败 acc=%s：%s", account.id, exc.message)

    try:
        tasks = client.fetch_tasks(cookie, base)
    except client.UpstreamError as exc:
        outcome = "expired" if exc.kind == "auth" else "failed"
        return {"account_id": account.id, "ok": False, "outcome": outcome,
                "detail": exc.message}

    daily = None
    one_time: list[dict] = []
    for item in tasks:
        if str(item.get("task_key") or "") == "daily_check_in":
            daily = item
        elif str(item.get("type") or "") == "one_time":
            one_time.append(item)

    if daily is None:
        return {"account_id": account.id, "ok": False, "outcome": "failed",
                "detail": "上游任务中心里没有每日签到",
                "one_time": len(one_time)}

    extra = daily.get("extra") if isinstance(daily.get("extra"), dict) else {}
    streak = int(extra.get("consecutive_days") or 0)
    status = str(daily.get("status") or "")
    credit = 0
    detail = ""

    if status == "claimed":
        outcome = "done"
        detail = "今日已签到"
        streak = streak
    else:
        try:
            claim = client.claim_task(cookie, "daily_check_in",
                                      _claim_date(daily), base)
        except client.UpstreamError as exc:
            outcome = "expired" if exc.kind == "auth" else "failed"
            return {"account_id": account.id, "ok": False, "outcome": outcome,
                    "detail": exc.message, "streak": streak}
        result = str(claim.get("result") or "")
        if result and result.lower() != "ok":
            return {"account_id": account.id, "ok": False, "outcome": "failed",
                    "detail": str(claim.get("hint") or ("result=%s" % result)),
                    "streak": streak}
        after = None
        try:
            after = client.fetch_balance(cookie, base).get("total")
        except client.UpstreamError:
            after = None
        if before is not None and after is not None:
            credit = max(0, int(after) - int(before))
        if credit <= 0:
            payload = claim.get("data") if isinstance(claim.get("data"), dict) else {}
            inner = payload.get("extra") if isinstance(payload.get("extra"), dict) else {}
            credit = int(inner.get("today_reward")
                         or payload.get("reward_amount")
                         or daily.get("reward_amount") or 0)
        if credit > 0:
            outcome = "claimed"
            detail = "签到成功"
            streak = streak + 1
        else:
            outcome = "done"
            detail = "今日已签到（上游幂等，余额未变化）"

    # 一次性任务：只领已经 claimable 的，不做需要「动作」的那几个
    claimed_one_time: list[dict] = []
    skipped_one_time: list[dict] = []
    if claim_one_time:
        for item in one_time:
            key = str(item.get("task_key") or "")
            if not key:
                continue
            state = str(item.get("status") or "")
            if state == "claimed":
                continue
            if state != "claimable":
                skipped_one_time.append({"key": key, "status": state,
                                         "action": str(item.get("action") or "")})
                continue
            try:
                claim = client.claim_task(cookie, key, "", base)
            except client.UpstreamError as exc:
                skipped_one_time.append({"key": key, "status": state,
                                         "error": exc.message[:120]})
                continue
            payload = claim.get("data") if isinstance(claim.get("data"), dict) else {}
            reward = int(payload.get("reward_amount") or 0)
            credit += max(0, reward)
            claimed_one_time.append({"key": key, "reward": reward})

    if outcome in ("claimed", "done"):
        try:
            lingxi_accounts.sync_quota(db, account)
        except Exception:  # noqa: BLE001 - 余额同步失败不影响签到结论
            logger.debug("签到后同步余额失败 acc=%s", account.id, exc_info=True)

    return {
        "account_id": account.id,
        "name": account.name,
        "ok": outcome in ("claimed", "done"),
        "outcome": outcome,
        "detail": detail,
        "credit": credit,
        "streak": streak,
        "one_time_claimed": claimed_one_time,
        "one_time_skipped": skipped_one_time,
    }


def run_daily_checkin(db: Session, schedule=None, realm: str = "") -> dict:
    """定时任务入口：给该平台所有启用账号签一遍。"""
    rows = [account for account in lingxi_accounts.accounts_for(db, realm)
            if (getattr(account, "status", "") or "") == "active"]
    if not rows:
        return {"task": TASK_NAME, "ok": True, "total": 0,
                "summary": "没有启用的灵犀账号"}

    items: list[dict] = []
    total = 0
    failures = 0
    for account in rows:
        try:
            result = checkin_account(db, account)
        except Exception as exc:  # noqa: BLE001 - 单号异常不影响批量
            logger.exception("灵犀签到异常 acc=%s", account.id)
            result = {"account_id": account.id, "ok": False, "outcome": "failed",
                      "detail": str(exc)[:200]}
        items.append(result)
        total += int(result.get("credit") or 0)
        if not result.get("ok"):
            failures += 1

    claimed = sum(1 for item in items if item.get("outcome") == "claimed")
    done = sum(1 for item in items if item.get("outcome") == "done")
    expired = sum(1 for item in items if item.get("outcome") == "expired")
    summary = ("签到 %d 号：成功 %d · 已签 %d · 失效 %d · 失败 %d，共 +%d 智点"
               % (len(items), claimed, done, expired,
                  len(items) - claimed - done - expired, total))
    return {"task": TASK_NAME, "ok": failures == 0, "total": total,
            "accounts": len(items), "claimed": claimed, "done": done,
            "expired": expired, "failed": failures,
            "items": items, "summary": summary}