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


def checkin_one(db: Session, account: Account) -> dict:
    """**单个账号**签到（账号行内「签到」按钮用）。

    批次版 `run_daily_checkin` 走线程池 + 私有 Session，适合全池；
    这里只有一条号，直接在当前 Session 上跑，返回与批次行同构的结果，
    前端两个入口共用一套渲染。
    """
    label = account.name or ("账号#%s" % account.id)
    realm = loomy_accounts.realm_of(account)
    try:
        data = loomy_accounts.ensure_token(db, account)
        result = client.first_login(
            loomy_accounts.token_of(data), realm, account.id)
    except client.UpstreamError as exc:
        return {"account_id": account.id, "account": label, "ok": False,
                "status": "error", "reason": exc.message[:160]}

    payload = result.get("data") if isinstance(result.get("data"), dict) else {}
    processed = bool(payload.get("alreadyProcessed"))
    quota = {
        "balance": int(payload.get("permanentBalance") or 0),
        "daily_balance": int(payload.get("dailyBalance") or 0),
        "available": int(payload.get("currentBalance") or 0),
        "models": 0,
    }
    try:
        loomy_accounts.apply_quota(account, quota)
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
    return {
        "account_id": account.id,
        "account": label,
        "ok": True,
        "status": "already" if processed else "claimed",
        "reason": "今日已领过" if processed else "签到成功",
        "already_processed": processed,
        "credit": (0 if processed else int(payload.get("dailyQuota") or 0)),
        "daily_quota": int(payload.get("dailyQuota") or 0),
        "current_balance": quota["available"],
        "daily_balance": quota["daily_balance"],
        "daily_consumed": int(payload.get("dailyConsumed") or 0),
        "cycle_date": str(payload.get("dailyCycleDate") or ""),
    }


def run_daily_checkin(db: Session, schedule=None, realm: str = "cn") -> dict:
    """每日签到：给所有 active 账号调一次 first-login。

    返回汇总：`{"ok", "total", "granted", "already", "failed", ...}`

    ## 为什么要并发 + 分批（2026-11-06 重写）

    原实现是 `for account in accounts:` 串行，每个号要打 **1~2 次上游**
    （可选探活 + first-login），100 个号实测要几分钟；期间还持有整份
    ORM 列表并逐号 `db.commit()` —— 号池一大就同时吃内存和数据库。

    现在：
      · `iter_account_batches` 按主键**分批取 id**（不把号池读进内存）；
      · `run_account_batch` 用线程池并发（默认 8，`ADMIN_BATCH_CONCURRENCY`
        可调），每个号**独立 Session + 短事务**；
      · 结果只留聚合 + 失败明细，逐号明细最多 `_DETAIL_KEEP` 条 ——
        100 条明细塞进 `schedules.last_result` 会被截断成非法 JSON
        （前端就只剩一堆原文，这正是「结果样式有问题」的根因）。

    签到是**幂等**的（`first_login` 当天第二次返回 alreadyProcessed），
    所以并发不会有重复领取的问题。
    """
    import time as _time

    started = _time.perf_counter()

    # 逐号明细留多少条：够排查即可，不必把 100 个号全塞进结果字段
    _DETAIL_KEEP = 30

    accounts = [
        int(row[0]) for row in
        (db.query(Account.id)
         .filter(Account.platform == PLATFORM, Account.status == "active",
                 Account.domain == realm)
         .order_by(Account.id.asc()).all())
    ]
    if not accounts:
        return {"ok": True, "platform": PLATFORM, "realm": realm, "total": 0,
                "granted": 0, "already": 0, "failed": [],
                "summary": "签到完成：没有可签到的账号"}

    counters = {"granted": 0, "already": 0, "failed": 0, "credits": 0,
                "quota_granted": 0, "quota_already": 0}
    failures: list[dict] = []
    samples: list[dict] = []

    def worker(session: Session, account: Account) -> dict:
        """单个账号：签到 + 回写余额。跑在线程池里，Session 是本任务私有的。"""
        label = account.name or ("账号#%s" % account.id)
        try:
            data = loomy_accounts.ensure_token(session, account)
            result = client.first_login(
                loomy_accounts.token_of(data), realm, account.id)
        except client.UpstreamError as exc:
            return {"account_id": account.id, "account": label,
                    "ok": False, "error": exc.message[:160]}

        payload = result.get("data") if isinstance(result.get("data"), dict) else {}
        processed = bool(payload.get("alreadyProcessed"))
        quota = {
            "balance": int(payload.get("permanentBalance") or 0),
            "daily_balance": int(payload.get("dailyBalance") or 0),
            "available": int(payload.get("currentBalance") or 0),
            "models": 0,
        }
        try:
            loomy_accounts.apply_quota(account, quota)
            session.commit()
        except Exception:  # noqa: BLE001 - 写回失败不影响签到结论
            session.rollback()
        return {
            "account_id": account.id,
            "account": label,
            "ok": True,
            # 与 WorkBuddy / Trae 统一：状态和一句人话都**由后端给**，
            # 前端不要再去猜 `already_processed` 是什么意思
            # （猜错就出现「状态列空白」或者「积分列写今日已领」的重复）。
            "status": "already" if processed else "claimed",
            "reason": "今日已领过" if processed else "签到成功",
            "already_processed": processed,
            # ---- 积分（2026-11-06 修：原来这里写死 0，用户看到「积分 —」）----
            #
            # Loomy 的签到**不发放可累加积分**，发的是「当日额度」（dailyQuota）。
            # 原来把 `credit` 写死 0，前端就只能显示破折号 —— 用户的原话是
            # 「逐账号结果都是空的我无法确保账号积分有真实获取到签到权益」。
            #
            # 所以这里把上游给的三个数如实透出：
            #   daily_quota     今日发放的额度（**这就是「签到权益」**）
            #   daily_consumed  今天已经用掉多少
            #   daily_balance   今天还剩多少
            # `credit` 保留为「本轮新到账」：已领过就是 0，新领就是 daily_quota，
            # 这样汇总里的「新增积分」才有意义。
            "credit": (0 if processed else int(payload.get("dailyQuota") or 0)),
            "daily_quota": int(payload.get("dailyQuota") or 0),
            "current_balance": quota["available"],
            "daily_balance": quota["daily_balance"],
            "daily_quota": int(payload.get("dailyQuota") or 0),
            "daily_consumed": int(payload.get("dailyConsumed") or 0),
            "cycle_date": str(payload.get("dailyCycleDate") or ""),
            # ↑ 这些都是标量，前端能直接画成表格
        }

    from admin.platform import shared as platform_shared

    rows = platform_shared.run_account_batch(
        accounts, worker, label="loomy-checkin")

    for row in rows:
        if row.get("ok"):
            if row.get("already_processed"):
                counters["already"] += 1
                counters["quota_already"] += int(row.get("daily_quota") or 0)
            else:
                counters["granted"] += 1
                counters["quota_granted"] += int(row.get("daily_quota") or 0)
            counters["credits"] += int(row.get("credit") or 0)
            if len(samples) < _DETAIL_KEEP:
                samples.append(row)
        else:
            counters["failed"] += 1
            failures.append({"account_id": row.get("account_id"),
                             "account": row.get("account") or "",
                             "error": row.get("error") or "未知错误"})

    total = len(rows)
    elapsed = round(_time.perf_counter() - started, 1)
    # 「今日权益合计」= 本轮新领 + 今日已领 —— 这才是用户想要的
    # 「这 101 个号今天一共领到多少」。只报「新领取 0」会让人以为白跑了。
    quota_total = counters["quota_granted"] + counters["quota_already"]
    summary = ("签到完成：%d 个账号，新领取 %d，今日已领 %d"
               % (total, counters["granted"], counters["already"]))
    if counters["failed"]:
        summary += "，失败 %d" % counters["failed"]
    summary += " · 今日权益合计 %s（新到账 %s）" % (
        _fmt_points(quota_total), _fmt_points(counters["quota_granted"]))
    summary += "（%.1fs）" % elapsed
    logger.info("Loomy %s", summary)
    return {
        "ok": not failures,
        "platform": PLATFORM,
        "realm": realm,
        "total": total,
        "granted": counters["granted"],
        "already": counters["already"],
        # ---- 积分口径（前端汇总卡直接用）----
        "quota_total": quota_total,          # 今日权益合计（新领 + 已领）
        "quota_granted": counters["quota_granted"],   # 本轮新到账
        "quota_already": counters["quota_already"],   # 今日已领部分
        "credits": counters["credits"],      # = quota_granted（同义，保留兼容）
        "failed": failures,
        "failed_count": len(failures),
        # 逐号明细**只留样本**：前端展示用，全量明细不该进结果字段
        "detail": samples,
        "detail_truncated": len(rows) > len(samples),
        "elapsed_s": elapsed,
        "summary": summary,
        "checked_at": datetime.utcnow().isoformat(),
    }


def _fmt_points(value: int) -> str:
    """积分显示：大的加千分位，避免 505000 看成一串数字。"""
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return "0"
    return "{:,}".format(number)


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
