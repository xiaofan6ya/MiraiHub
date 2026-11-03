"""小浣熊定时任务。

## 为什么只有保活

小浣熊**没有签到接口**（不像 Loomy 的 `/points/first-login`）。
`/api/web/office/v3/setting_info` 在 agent2api 的源码里被标注为「每日积分
发放触发器」，但那是**打开 App 时的隐式副作用**，主动调用可能被判定为
异常行为。本项目不替用户做这件事 —— 需要每日额度就让官方客户端自己跑。

`raccoon_keepalive` 做两件事：
  1. 续期临期 JWT（距 exp < 6h 时）
  2. 探一次 `/points/v1/balance`，把死号尽早标出来

失败不抛异常：定时任务抛异常会让调度器把整轮标记为失败，
而「一个号挂了」不该影响其它号。
"""
from __future__ import annotations

import logging

from admin.platform.raccoon.services import accounts as raccoon_accounts

logger = logging.getLogger("admin.platform.raccoon.tasks")


def run_keepalive(db) -> dict:
    """批量保活 + 探活。

    ## `partial`（凭证有效、积分不可读）不算 dead

    当前上游对该批账号的积分接口统一返回 403，而模型目录正常。
    如果这里只判 `ok`，`partial` 会掉进 else 被记成 dead ——
    保活任务「上线即全红」，运维会以为号池挂了，实际 50 个号都能调模型。
    """
    renewed = 0
    ok = 0
    partial = 0
    dead: list[int] = []
    errors: list[str] = []

    for account in raccoon_accounts.accounts_for(db):
        if (getattr(account, "status", "") or "") != "active":
            continue
        try:
            before = raccoon_accounts.credentials_of(account).get("token")
            raccoon_accounts.ensure_token(db, account)
            after = raccoon_accounts.credentials_of(account).get("token")
            if before != after:
                renewed += 1
            result = raccoon_accounts.sync_quota(db, account)
            if result.get("ok"):
                ok += 1
            elif result.get("partial"):
                partial += 1
                ok += 1
            else:
                dead.append(account.id)
        except Exception as exc:  # noqa: BLE001 - 单号异常不打断整轮
            logger.warning("小浣熊保活失败 account=%s：%s", account.id,
                           type(exc).__name__)
            errors.append("账号 %s：%s" % (account.id, type(exc).__name__))

    return {
        "supported": True,
        "renewed": renewed,
        "alive": ok,
        "partial": partial,
        "dead": dead,
        "errors": errors,
        "summary": "续期 %d 个，存活 %d 个（其中额度不可读 %d 个），失效 %d 个"
                   % (renewed, ok, partial, len(dead)),
    }


def run_expiry_sweep(db) -> dict:
    """把已过期账号标 disabled（保留记录，不删除）。"""
    result = raccoon_accounts.prune_expired(db)
    return {"supported": True, **result,
            "summary": "标记过期 %d 个" % result.get("marked", 0)}
