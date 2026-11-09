"""Loomy 新手任务（一次性，共 10000 积分）。

## 为什么值得做

客户端里那 10000 分的「新手任务」（初识 Loomy 3000 / 打造你的专属 Loomy 3500 /
遇见你的 AI 搭子 3500）是**一次性大额**，比每天签到 5000 的边际收益更高，
而且做完就永久留在账号上。

## 关键事实：客户端默认不真的记账

`app.asar.unpacked/electron/onboarding-service.js` 有两个后端：

    backend = 'mock'（默认）  读写 userData/onboarding-tasks.json，**不发请求**
    backend = 'real'          GET/POST /api/v1/onboarding/tasks[/complete]

也就是说：用户在客户端里点完任务、界面显示「已获得 10000 积分」，
服务端完全可能还是 0 —— 而客户端**看不出区别**。

实测（2026-11-09，账号 #2）：服务端这两个接口是真的，完成后
`/points/balance` 的 `availableBalance` 立即 +1000。所以网关直接调服务端，
既能把分真正赚到，又能一次跑完整个号池。

## 三个安全约束

1. **幂等**：`alreadyCompleted: true` 是正常结果（重复调用不重复加分），
   不算失败；批量跑要据此统计「本次真的赚到多少」。
2. **只认 8 个 key**：服务端对空/未知 key 回 `100001` 直接拒绝，
   所以本地先按注册表过滤，避免每个号都白撞一次。
3. **串行 + 抖动**：这是"一次性任务"，没有时效压力；串行能把
   「几十个号同时完成同一批任务」这种机器特征摊开。
"""
from __future__ import annotations

import logging
import time

from sqlalchemy.orm import Session

from admin.models import Account
from admin.platform.loomy.core import client, endpoints
from admin.platform.loomy.services import accounts as loomy_accounts

logger = logging.getLogger("admin.platform.loomy.onboarding")

TASK_NAME = "loomy_onboarding"

#: 单号两次请求之间的最小间隔（秒）—— 8 个 key + 2 次读状态，共 10 次，
#: 全零秒连发等于一个明显的脚本特征。
STEP_GAP = 0.6


def _keys() -> list[str]:
    return list(endpoints.ONBOARDING_TASKS)


def total_points() -> int:
    return int(sum(endpoints.ONBOARDING_TASKS.values()))


def status_of(account: Account) -> dict:
    """单个账号的任务进度（读服务端，不信客户端本地记账）。"""
    data = loomy_accounts.credentials_of(account)
    token = loomy_accounts.token_of(data)
    realm = loomy_accounts.realm_of(account)
    if not token:
        return {"account_id": account.id, "ok": False,
                "reason": "账号缺少 session token", "earned": 0,
                "pending": _keys()}
    try:
        progress = client.fetch_onboarding(token, realm, account.id)
    except client.UpstreamError as exc:
        return {"account_id": account.id, "ok": False,
                "kind": exc.kind, "reason": str(exc.message)[:160],
                "earned": 0, "pending": _keys()}
    return {
        "account_id": account.id,
        "account": account.name or str(account.id),
        "ok": True,
        "earned": progress["earned"],
        "total": progress["total"],
        "tasks": progress["tasks"],
        "pending": progress["pending"],
        # 中文任务名：控制台直接显示，避免把 first_message 这类内部 key 抛给用户
        "pending_labels": [label_of(key) for key in progress["pending"]],
        "missing_points": max(0, progress["total"] - progress["earned"]),
    }


def point_table() -> dict:
    """key -> 积分（给控制台渲染任务清单）。"""
    return dict(endpoints.ONBOARDING_TASKS)


def task_catalog() -> list[dict]:
    """任务清单（**中文**，按客户端三段分组）。

    控制台只展示这个；`key` 仍然原样给前端，用于提交 `complete`。
    """
    out: list[dict] = []
    for gid, gtitle, gpoints, keys in endpoints.ONBOARDING_GROUPS:
        out.append({
            "group": gid,
            "group_label": gtitle,
            "group_points": gpoints,
            "tasks": [{"key": key,
                       "label": endpoints.ONBOARDING_TASK_LABELS.get(key, key),
                       "points": endpoints.ONBOARDING_TASKS.get(key, 0)}
                      for key in keys],
        })
    return out


def label_of(key: str) -> str:
    return endpoints.ONBOARDING_TASK_LABELS.get(str(key), str(key))


def status_of_each(db: Session, realm: str = "",
                   limit: int | None = None) -> list[dict]:
    """批量读进度（控制台面板用）。串行，但每号只有 1 次请求。"""
    rows = [a for a in loomy_accounts.accounts_for(db, realm)
            if (getattr(a, "status", "") or "") == "active"]
    if limit:
        rows = rows[:int(limit)]
    return [status_of(account) for account in rows]


def run_all(db: Session, realm: str = "", *, limit: int | None = None,
            dry_run: bool = False) -> dict:
    """把该平台所有启用账号的新手任务**补齐**（幂等，可反复跑）。

    `dry_run=True` 只统计，不发 complete（用来看"还有多少分没赚"）。
    """
    started = time.perf_counter()
    accounts = [a for a in loomy_accounts.accounts_for(db, realm)
                if (getattr(a, "status", "") or "") == "active"]
    if limit:
        accounts = accounts[:int(limit)]
    if not accounts:
        return {"task": TASK_NAME, "ok": True, "total": 0,
                "summary": "没有启用的 Loomy 账号"}

    items: list[dict] = []
    completed_accounts = 0
    gained_total = 0
    already_total = 0
    failed = 0

    for account in accounts:
        data = loomy_accounts.credentials_of(account)
        token = loomy_accounts.token_of(data)
        realm_of_account = loomy_accounts.realm_of(account)
        item = {"account_id": account.id, "account": account.name or str(account.id),
                "ok": False, "earned_before": 0, "gained": 0, "tasks": [],
                "reason": ""}
        if not token:
            item["reason"] = "账号缺少 session token"
            failed += 1
            items.append(item)
            continue
        try:
            progress = client.fetch_onboarding(token, realm_of_account, account.id)
            item["earned_before"] = progress["earned"]
            pending = [k for k in progress["pending"] if k in endpoints.ONBOARDING_TASKS]
            if dry_run or not pending:
                item.update({"ok": True, "pending": pending,
                             "reason": "" if pending else "已全部完成"})
                if not pending:
                    already_total += 1
                items.append(item)
                continue
            for key in pending:
                try:
                    result = client.complete_onboarding(
                        token, realm_of_account, key, account_id=account.id)
                    item["tasks"].append({
                        "key": key,
                        "label": label_of(key),
                        "points": result["points"],
                        "already": result["already"],
                    })
                    if not result["already"]:
                        item["gained"] += result["points"]
                except client.UpstreamError as exc:
                    item["tasks"].append({"key": key, "label": label_of(key),
                                          "points": 0,
                                          "error": str(exc.message)[:100]})
                    logger.debug("Loomy 任务失败 acc=%s key=%s：%s",
                                 account.id, key, exc.message[:120])
                time.sleep(STEP_GAP)
            item["ok"] = True
            gained_total += item["gained"]
            if item["gained"]:
                completed_accounts += 1
        except client.UpstreamError as exc:
            item["reason"] = str(exc.message)[:160]
            failed += 1
            logger.info("Loomy 新手任务读取失败 acc=%s：%s", account.id,
                        str(exc.message)[:120])
        items.append(item)

    elapsed = round(time.perf_counter() - started, 1)
    summary = ("Loomy 新手任务：%d 个号新完成，共 +%d 积分（%d 个号本就完成，%d 个失败，用时 %ss）"
               % (completed_accounts, gained_total, already_total, failed, elapsed))
    logger.info("%s", summary)
    return {
        "task": TASK_NAME, "ok": failed == 0 or gained_total > 0,
        "accounts": len(accounts), "completed": completed_accounts,
        "gained": gained_total, "already": already_total, "failed": failed,
        "total_points": total_points(), "dry_run": bool(dry_run),
        "elapsed_s": elapsed, "summary": summary, "items": items[:80],
        "note": "一次性任务：每个 key 只能领一次，重复调用返回 alreadyCompleted 不会重复加分",
    }