"""Loomy 签到：每日赠送额度刷新为 5000 积分。

## 接口（实测 2026-10-29）

    POST {base}/points/first-login
    headers: {"token": <session>}

响应（`code: 000000`）：

```json
{"code":"000000","desc":"成功","data":{
   "alreadyProcessed": true,
   "currentBalance": 10500,
   "permanentBalance": 5500,     // 长期积分
   "dailyBalance": 5000,          // 当日额度
   "dailyQuota": 5000,            // 当日上限
   "dailyConsumed": 0,            // 当日已消耗
   "dailyCycleDate": "2026-10-29",
   "registerReward": 0,
   "inviteeReward": 0,
   "inviterReward": 0,
   "invitationCodes": [{...}]
}}
```

## 关键语义

* 每日赠送 **5000** 积分，登录后自动刷新（`dailyBalance` 每天重置到 `dailyQuota`）
* 消耗顺序：**活动/兑换码积分 → 普通长期积分**
  （所以 `permanentBalance` 不会每天清零，`dailyBalance` 会）
* `alreadyProcessed: true` = 今天已经领过（幂等，不是错误）

## 签到任务该做什么

`first-login` 是**幂等**的：每天第一次调用触发发放，之后返回
`alreadyProcessed: true`。所以每日定时调一次即可 —— 不需要判断「今天领过没」。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from admin.models import Account
from admin.platform.loomy.core import client, endpoints
from admin.platform.loomy.services import accounts as loomy_accounts

logger = logging.getLogger("admin.platform.loomy.checkin")
PLATFORM = "loomy"


def run_daily_checkin(db: Session, schedule=None, realm: str = "cn") -> dict:
    """每日签到：给所有 active 账号调一次 first-login。

    返回汇总：`{"ok", "total", "granted", "already", "failed", "items", "summary"}`
    """
    accounts = loomy_accounts.accounts_for(db, realm)
    items: list[dict] = []
    granted = already = 0
    failed: list[dict] = []

    for account in accounts:
        if (getattr(account, "status", "") or "") != "active":
            continue
        try:
            data = loomy_accounts.ensure_token(db, account)
            result = client.first_login(
                loomy_accounts.token_of(data), realm, account.id)
        except client.UpstreamError as exc:
            failed.append({"account_id": account.id, "account": account.name,
                           "error": exc.message[:160]})
            continue

        payload = result.get("data") if isinstance(result.get("data"), dict) else {}
        processed = bool(payload.get("alreadyProcessed"))
        if processed:
            already += 1
        else:
            granted += 1

        # 顺手把最新余额写回 DB（签到后余额变了）
        quota = {
            "balance": int(payload.get("permanentBalance") or 0),
            "daily_balance": int(payload.get("dailyBalance") or 0),
            "available": int(payload.get("currentBalance") or 0),
            "models": 0,
        }
        try:
            loomy_accounts.apply_quota(account, quota)
            db.commit()
        except Exception:  # noqa: BLE001 - 写回失败不影响签到结果
            db.rollback()

        items.append({
            "account_id": account.id,
            "account": account.name,
            "already_processed": processed,
            "current_balance": quota["available"],
            "permanent_balance": quota["balance"],
            "daily_balance": quota["daily_balance"],
            "daily_quota": int(payload.get("dailyQuota") or 0),
            "daily_consumed": int(payload.get("dailyConsumed") or 0),
            "cycle_date": str(payload.get("dailyCycleDate") or ""),
        })

    total = len(items) + len(failed)
    summary = ("签到完成：%d 个账号，新领取 %d，今日已领 %d"
               % (total, granted, already))
    if failed:
        summary += "，失败 %d" % len(failed)
    logger.info("Loomy %s", summary)
    return {
        "ok": not failed,
        "platform": PLATFORM,
        "realm": realm,
        "total": total,
        "granted": granted,
        "already": already,
        "failed": failed,
        "items": items,
        "summary": summary,
        "checked_at": datetime.utcnow().isoformat(),
    }


def daily_bonus_info(db: Session, realm: str = "cn") -> dict:
    """查询当日额度状态（不领取）。给前端展示用。"""
    accounts = loomy_accounts.accounts_for(db, realm)
    rows = []
    for account in accounts:
        try:
            snapshot = json.loads(account.credits_snapshot or "{}")
        except ValueError:
            snapshot = {}
        rows.append({
            "account_id": account.id,
            "account": account.name,
            "available": int(getattr(account, "balance_remain", 0) or 0),
            "permanent": int((snapshot or {}).get("balance") or 0),
            "daily": int((snapshot or {}).get("daily_balance") or 0),
        })
    return {"items": rows, "daily_grant": 5000,
            "note": "每日登录赠送 5000 积分；消耗优先用活动/兑换码积分，再扣长期积分"}
