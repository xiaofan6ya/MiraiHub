"""把 Qoder 的额度 / 套餐 / 签到接入账号服务。

## 额度口径（实测 2026-10-29）

`GET /api/v2/quota/usage` 返回**两个桶**：

```json
{
  "userQuota":  {"total": 300, "used": 1, "remaining": 299, "unit": "credits"},
  "addOnQuota": {"total": 100, "used": 0, "remaining": 100, "unit": "credits"},
  "expiresAt":  1794368045643,
  "userType":   "personal_professional_trial"
}
```

* `userQuota`  套餐额度
* `addOnQuota` **签到领的资源包**（每日 100 Credits 落在这里）
* 可用总额 = 两个 `remaining` 之和

之前只读了 `userQuota`，所以签到拿到的 100 在面板上**看不到**。

## 签到口径（活动形式，不是 daily-check-in）

```
GET  {open_api}/sash/api/v1/me/campaigns?forceRefresh=true
  -> {uid, showCampaign, claimable,
      campaigns:[{campaignId, actionType, claimStatus,
                  benefit:{kind:"CREDITS", amount:100, validity:{...}}}]}

POST {open_api}/sash/api/v1/me/campaigns/{campaignId}/claim   body {}
  -> {status:"CLAIMED", benefit:{...}, claimedAt, grantedAt, expiresAt}
```

三条实测经验（参考实现记录 + 本轮验证）：

1. **`claimable` 顶层标志不能当判据** —— 领完之后它仍可能是 `true`
   （还有可领的促销活动）。必须按 `actionType == "CLAIM_BENEFIT"` 逐行筛。
2. **领完活动行不消失**，`claimStatus` 从 `CLAIMABLE` 变 `CLAIMED`。
   所以「今天已领」直接读活动列表，不需要本地记账。
3. **国际版需要风控头**（`Cosy-MachineToken` 等，由 UMID 生成）。
   不带头时活动列表里**永远只有 `VIEW_DETAILS` 促销** ——
   这不是「国际版没有签到」，而是缺头。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from admin.models import Account
from admin.platform.qoder.core import client, endpoints
from admin.platform.qoder.services import accounts as qoder_accounts

logger = logging.getLogger("admin.platform.qoder.checkin")
PLATFORM = "qoder"

#: 活动类型：可领取权益（促销是 `VIEW_DETAILS`，不能领）
ACTION_CLAIM_BENEFIT = "CLAIM_BENEFIT"
STATUS_CLAIMABLE = "CLAIMABLE"
STATUS_CLAIMED = "CLAIMED"


def _number(value) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return 0.0
    return 0.0


def quota_of(raw: dict) -> dict:
    """把 usage 响应归一成统一额度结构。

    **两个桶都要读** —— 只读 `userQuota` 会让签到领的
    `addOnQuota` 在面板上看不到。
    """
    wallets: list[dict] = []
    available = 0.0
    unit = str(raw.get("usageType") or "credits")

    for key, label in (("userQuota", "套餐额度"), ("addOnQuota", "资源包")):
        bucket = raw.get(key)
        if not isinstance(bucket, dict):
            continue
        bucket_unit = str(bucket.get("unit") or unit)
        total = _number(bucket.get("total"))
        used = _number(bucket.get("used"))
        remaining = _number(bucket.get("remaining"))
        wallets.append({"type": "%s_total" % key, "name": "%s 总额" % label,
                        "balance": total, "unit": bucket_unit})
        wallets.append({"type": "%s_used" % key, "name": "%s 已用" % label,
                        "balance": used, "unit": bucket_unit})
        wallets.append({"type": "%s_remaining" % key,
                        "name": "%s 剩余" % label,
                        "balance": remaining, "unit": bucket_unit})
        available += remaining

    return {
        "available": available,
        "wallets": wallets,
        "unit": unit,
        "expire_at": int(_number(raw.get("expiresAt"))),
        "user_type": str(raw.get("userType") or ""),
        "is_exceeded": bool(raw.get("isQuotaExceeded")),
        "raw": raw,
    }


def sync_account_quota(db: Session, account: Account) -> dict:
    """同步一个账号的额度 + 套餐，写回 DB 列。"""
    data = qoder_accounts.ensure_token(db, account)
    token = qoder_accounts.token_of(data)
    realm = qoder_accounts.realm_of(account)

    try:
        raw = client.fetch_usage(token, realm)
    except client.UpstreamError as exc:
        if exc.kind == "auth":
            account.status = "disabled"
            db.commit()
        return {"ok": False, "error": exc.to_dict()}

    quota = quota_of(raw)

    # 套餐名（失败不影响额度）
    plan_name = ""
    try:
        plan = client.fetch_plan(token, realm)
        plan_name = str(plan.get("plan_tier_name")
                        or plan.get("planName") or "").strip()
    except client.UpstreamError:
        pass

    account.balance_remain = int(round(quota["available"]))
    account.balance_total = int(round(sum(
        w["balance"] for w in quota["wallets"] if w["type"].endswith("_total"))))
    account.credits_synced_at = datetime.utcnow()
    account.last_sync_at = datetime.utcnow()
    account.credits_snapshot = json.dumps({
        "unit": quota["unit"],
        "quota_kind": "balance",
        "available": quota["available"],
        "wallets": quota["wallets"],
        "expire_at": quota["expire_at"],
        "user_type": quota["user_type"],
        "plan_name": plan_name,
        "is_exceeded": quota["is_exceeded"],
    }, ensure_ascii=False)[:20000]
    db.commit()
    return {"ok": True, "quota": quota, "plan_name": plan_name}


def _reward_text(benefit: dict) -> str:
    """把权益渲染成一行文案，如 `+100 Credits`。

    `kind` 目前见过 `CREDITS`；未知 kind 就只显示数字，
    不要硬编成「积分」（可能是天数/次数）。
    """
    amount = _number((benefit or {}).get("amount"))
    kind = str((benefit or {}).get("kind") or "").strip()
    if not amount:
        return ""
    label = {"CREDITS": "Credits"}.get(kind, kind.title() if kind else "")
    if amount == int(amount):
        number = str(int(amount))
    else:
        number = ("%.2f" % amount).rstrip("0").rstrip(".")
    return ("+%s %s" % (number, label)).strip()


def campaigns_of(campaigns: dict) -> list[dict]:
    """所有**权益类**活动（`CLAIM_BENEFIT`），含已领取的。

    用它判断「今天已领」和做 UI 展示。
    **不能用顶层 `claimable` 标志** —— 领完之后它仍可能是 `true`
    （同账号还有别的可领促销活动）。
    """
    out = []
    for item in campaigns.get("campaigns") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("actionType") or "") != ACTION_CLAIM_BENEFIT:
            continue
        campaign_id = str(item.get("campaignId") or "")
        if not campaign_id:
            continue
        status = str(item.get("claimStatus") or "")
        benefit = item.get("benefit") if isinstance(item.get("benefit"), dict) else {}
        out.append({
            # 上游原名（测试与参考实现都用这个）
            "campaignId": campaign_id,
            "campaignKey": str(item.get("campaignKey") or ""),
            # snake_case 别名（下游代码在用）
            "campaign_id": campaign_id,
            "campaign_key": str(item.get("campaignKey") or ""),
            "status": status,
            "claimable": status == STATUS_CLAIMABLE,
            "claimed": status == STATUS_CLAIMED,
            "amount": _number(benefit.get("amount")),
            "kind": str(benefit.get("kind") or ""),
            "start_at": item.get("startAt"),
            "end_at": item.get("endAt"),
        })
    return out


def claimable_of(campaigns: dict) -> list[dict]:
    """**只**返回当前可领取的权益活动。

    函数名就是 `claimable` —— 已领取的不该出现在返回值里。
    「今天已领过没」用 `campaigns_of()` 看 `claimed` 字段。
    """
    return [row for row in campaigns_of(campaigns) if row["claimable"]]


def run_daily_checkin(db: Session, schedule=None, realm: str = "") -> dict:
    """每日签到：领取所有 `CLAIM_BENEFIT` 活动。

    返回汇总。**「没有活动」不算失败** —— Free 套餐本来就不下发，
    报成失败会让用户以为接口坏了。
    """
    target = (realm or "").strip().lower()
    accounts = qoder_accounts.accounts_for(db, target)
    items: list[dict] = []
    claimed = already = 0
    failed: list[dict] = []

    for account in accounts:
        if (getattr(account, "status", "") or "") != "active":
            continue
        account_realm = qoder_accounts.realm_of(account)
        try:
            data = qoder_accounts.ensure_token(db, account)
            token = qoder_accounts.token_of(data)
            campaigns = client.fetch_campaigns(token, account_realm)
        except client.UpstreamError as exc:
            failed.append({"account_id": account.id, "account": account.name,
                           "error": exc.message[:160]})
            continue

        rows = campaigns_of(campaigns)
        granted_amount = 0.0
        granted_now = 0
        for row in rows:
            if not row["claimable"]:
                continue
            try:
                result = client.claim_campaign(token, account_realm,
                                               row["campaign_id"])
                if str(result.get("status") or "") == STATUS_CLAIMED:
                    granted_amount += row["amount"]
                    granted_now += 1
            except client.UpstreamError as exc:
                failed.append({"account_id": account.id, "account": account.name,
                               "campaign": row["campaign_id"],
                               "error": exc.message[:120]})

        if granted_now:
            claimed += 1
        elif any(r["claimed"] for r in rows):
            already += 1

        # 领完刷一次额度（签到的积分为 addOnQuota）
        quota_result = sync_account_quota(db, account)

        items.append({
            "account_id": account.id,
            "account": account.name,
            "realm": account_realm,
            "campaigns": len(rows),
            "granted_now": granted_now,
            "granted_amount": granted_amount,
            "already_claimed": sum(1 for r in rows if r["claimed"]),
            "available": (quota_result.get("quota") or {}).get("available"),
            "plan_name": quota_result.get("plan_name") or "",
            "no_campaign": not rows,
        })

    total = len(items) + len(failed)
    if total == 0:
        summary = "没有可签到的账号"
    elif claimed:
        summary = "签到完成：%d 个账号，新领取 %d 个权益" % (total, claimed)
    elif already:
        summary = "签到完成：%d 个账号，今日已领" % total
    else:
        summary = ("没找到可领取的签到活动（%d 个账号）——"
                   "Free 套餐通常不下发活动，这不是错误" % total)
    if failed:
        summary += "，失败 %d" % len(failed)

    logger.info("Qoder %s", summary)
    return {
        "ok": not failed,
        "supported": True,
        "platform": PLATFORM,
        "realm": target,
        "total": total,
        "granted": claimed,
        "already": already,
        "failed": failed,
        "items": items,
        "summary": summary,
    }
