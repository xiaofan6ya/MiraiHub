"""出站代理池：给批量任务分配「不同的 IP」，并按节点限并发。

## 要解决的具体问题（2026-11-06 用户实测）

> ip 被上游拉黑太多了，一个 ip 请求多个一直会拉黑 ip。
> 如果 1 并发查 100 个要 10 分钟，然后等下次余额刷新又是 10 分钟。

两件事都得解决：

1. **不能全从一个 IP 出去** —— 几百上千个账号共用一个出口 IP，
   上游看到的就是「一个 IP 上几百个账号在活动」，必封。
2. **并发不能凭感觉拍** —— 每个出口能承受几个并发是**出口自己的属性**
   （住宅代理通常 1~2，机房可以 5~10），所以并发上限 =
   `Σ(每节点 max_concurrency)`，而不是全局一个数字。

## 分配方式

```
可用节点: A(并发2) B(并发2) C(并发1)     → 总并发 5
线程 1 → A   线程 2 → A
线程 3 → B   线程 4 → B
线程 5 → C
```

每个 worker 线程**独占一个槽位**，槽位绑定了具体节点，所以同一个节点
上最多同时跑 `max_concurrency` 个账号。

## 节点被拉黑怎么办

`mark_blocked()` 给节点置 `blocked_until`，该节点立刻从可用集里消失，
其它节点继续跑。**不会因为一个 IP 挂掉整批停摆** —— 这正是
「余额刷新卡死」的一半成因（另一半是全量重扫，见 `scheduler.py`）。
"""
from __future__ import annotations

import logging
import random
import time
import uuid
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

_logger = logging.getLogger("admin.proxy_pool")

#: 默认「一个节点被判定拉黑后静默多久」（秒）。住宅代理恢复慢，给 30 分钟。
DEFAULT_BLOCK_SECONDS = 1800


def _model():
    from admin.models import ProxyEndpoint

    return ProxyEndpoint


def available(db: Session, pool: str = "") -> list:
    """当前可用的节点（启用 + 不在静默期）。

    `pool` 为空表示「不挑组」—— 所有节点都可以用。
    """
    ProxyEndpoint = _model()
    now = datetime.utcnow()
    query = db.query(ProxyEndpoint).filter(ProxyEndpoint.enabled == 1)
    if pool:
        query = query.filter(ProxyEndpoint.pool == pool)
    rows = query.order_by(ProxyEndpoint.id.asc()).all()
    return [row for row in rows
            if not (row.blocked_until and row.blocked_until > now)]


def total_slots(db: Session, pool: str = "") -> int:
    """总并发槽 = 各节点 `max_concurrency` 之和。

    没有任何可用节点时返回 **0** —— 调用方据此判断「只能直连」，
    而不是硬算成 1（那会让「配了代理但全挂了」的情况静默退回本机 IP）。
    """
    rows = available(db, pool)
    return sum(max(1, int(row.max_concurrency or 1)) for row in rows)


def expand(url: str) -> str:
    """把 `{session}` / `{rand}` 占位符换成随机串，并解开加密/环境变量引用。

    轮换型代理（如 `http://user-session-{session}:pass@host:port`）靠会话
    标识切换出口 IP。**每个并发槽要拿到不同的会话**，否则 5 个线程还是
    同一个出口。

    ## 为什么解析放在这里

    库里存的是 `enc:v1:...` 密文（见 `admin/secret_box.py`），
    只有真正要发请求时才解密 —— 这样密钥不参与列表查询、日志、
    导出备份这些路径。
    """
    from admin import secret_box

    text = secret_box.resolve(url)
    if not text:
        return ""
    token = uuid.uuid4().hex[:12]
    return (text.replace("{session}", token)
            .replace("{rand}", token)
            .replace("{uuid}", uuid.uuid4().hex))


def slots(db: Session, pool: str = "") -> list[dict]:
    """展开成「槽位列表」，每个元素是一个可直接用的出口。

    返回 `[{"endpoint_id": 1, "label": "A", "url": "http://..."}]`，
    长度 = 总并发。worker 按下标取自己的槽。
    """
    result: list[dict] = []
    for row in available(db, pool):
        count = max(1, int(row.max_concurrency or 1))
        for index in range(count):
            url = expand(row.url)
            if not url:
                continue
            result.append({
                "endpoint_id": row.id,
                "label": (row.label or ("#%s" % row.id)) + (
                    "" if count == 1 else "-%d" % (index + 1)),
                "url": url,
            })
    # 打散：固定按 id 排队会让「前 N 个号永远走第一个节点」，
    # 上游看到的就是某个 IP 上恒定的一批账号。
    random.shuffle(result)
    return result


def pick(db: Session, pool: str = "") -> str:
    """随手取一个出口（单次请求用，不占槽）。没有节点时返回空串 = 直连。"""
    rows = slots(db, pool)
    return random.choice(rows)["url"] if rows else ""


def mark_ok(db: Session, endpoint_id: int | None) -> None:
    """记一次成功（只更新时间，用于判断「这个节点还活不活」）。"""
    if not endpoint_id:
        return
    try:
        ProxyEndpoint = _model()
        row = db.get(ProxyEndpoint, int(endpoint_id))
        if row is None:
            return
        row.last_ok_at = datetime.utcnow()
        row.last_error = ""
        db.commit()
    except Exception:  # noqa: BLE001 - 记账失败不影响业务
        db.rollback()


def mark_blocked(db: Session, endpoint_id: int | None, reason: str = "",
                 seconds: int = DEFAULT_BLOCK_SECONDS) -> None:
    """把节点打进静默期。

    ## 什么时候该调

    只有**确定是这个出口的问题**才调：
      · 403 且响应体像「IP 被拒」；
      · 同一节点上**多个不同账号**连续 403 / 11140（说明是连坐，不是号的问题）。

    **单个账号的 401 不要动节点** —— 那是账号自己的 token 废了，
    换出口也没用，反而会把好节点一个个打进黑洞。
    """
    if not endpoint_id:
        return
    try:
        ProxyEndpoint = _model()
        row = db.get(ProxyEndpoint, int(endpoint_id))
        if row is None:
            return
        row.blocked_until = datetime.utcnow() + timedelta(seconds=max(60, seconds))
        # 脱敏后再落库：上游的错误体里经常原样回显请求 URL（含账密），
        # 直接写进 last_error 就等于把代理凭据存了一份明文。
        from admin import secret_box

        row.last_error = secret_box.scrub(reason or "blocked")[:255]
        db.commit()
        _logger.warning("代理节点 #%s 进入静默期 %ds：%s",
                        endpoint_id, seconds, secret_box.scrub(reason or "-"))
    except Exception:  # noqa: BLE001
        db.rollback()


def looks_ip_blocked(status: int, text: str) -> bool:
    """这次失败像不像「IP 被拉黑」。

    刻意**保守**：宁可漏判（继续用这个节点、它自己会再撞一次）也不要
    误判（把一个好节点打进 30 分钟静默，整批变慢）。
    """
    if status not in (403, 429):
        return False
    body = (text or "").lower()
    hints = ("ip", "forbidden", "blocked", "denied", "too many",
             "rate limit", "unapproved channel", "request illegal")
    return any(hint in body for hint in hints)


def resolve_url(account, pool: str = "", db: Session | None = None) -> str:
    """给一个账号挑出口：**账号绑定优先**，其次池内随机。

    账号表上如果配了 `proxy`（单独绑定的出口），就用它 —— 同一个号
    始终从同一个 IP 出去，比每次换 IP 更像真人。
    """
    bound = ""
    try:
        import json

        data = json.loads(getattr(account, "auth_json", "") or "{}")
        if isinstance(data, dict):
            bound = str(data.get("proxy") or "").strip()
    except (TypeError, ValueError):
        bound = ""
    if bound:
        return expand(bound)
    if db is None:
        return ""
    return pick(db, pool)