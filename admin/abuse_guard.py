"""异常行为检测与自动封禁（abuse guard）。

要解决什么问题
--------------
真实事故：攻击者凌晨对 `/api/accounts` 打了 **697,875 次**请求（其中 695,367 次
401），一次都没被拦。根因是三个缺口叠在一起：

1. `IPBlockMiddleware` 第一行就判断 `_is_public_gateway_path()`，只对 `/v1/*`
   生效 —— 打 `/api/*` 时**连「这个 IP 是否已被封」的检查都不执行**；
2. 全项目没有任何一处**自动**写 `blocked_ips`，封禁只能靠后台手动点；
3. `ratelimit.py` 只统计「登录失败」，普通 API 请求完全不计。

于是形成最坏组合：**高频扫描不计数、计数了不判定、判定了没人执行**。

本模块补中间那一段：**观察 → 判定 → 封禁**。判定用滑动窗口，封禁带过期时间，
且本机地址永不封（否则一键把自己锁在门外）。

设计取舍
--------
* **纯内存**：单进程 uvicorn 下内存统计是准确且最快的。多进程部署时各进程各算
  一份，阈值相当于被放大 N 倍 —— 这是已知的、可接受的降级（我们本来就是单进程）。
* **判定放在响应阶段**：只有拿到真实状态码，才知道这次请求是「正常」还是「401
  扫描」。在 `http.response.start` 时记录，不额外增加请求延迟。
* **封禁失败不阻断请求**：写库异常只记日志，绝不让风控把正常业务打成 500。
"""
from __future__ import annotations

import logging
import os
import time
from collections import deque
from datetime import datetime, timedelta
from threading import Lock

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 策略参数（全部可用环境变量覆盖，便于线上按实际流量调参）
# ---------------------------------------------------------------------------


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default


#: 统计窗口（秒）。60 秒足够区分「扫描」与「正常浏览」。
WINDOW_SECONDS = _env_int("ABUSE_WINDOW_SECONDS", 60)

#: 单 IP 窗口内总请求上限。超过即判定为洪水。
MAX_REQUESTS = _env_int("ABUSE_MAX_REQUESTS", 300)

#: 窗口内 401/403 上限。凭据猜解、无令牌扫接口都会在这里堆高。
MAX_AUTH_FAILURES = _env_int("ABUSE_MAX_AUTH_FAILURES", 60)

#: 窗口内 404 上限。目录/路径爆破的特征。
MAX_NOT_FOUND = _env_int("ABUSE_MAX_NOT_FOUND", 80)

#: 首次封禁时长（秒），重复命中按倍数递增，上限 BAN_MAX_SECONDS。
BAN_SECONDS = _env_int("ABUSE_BAN_SECONDS", 600)
BAN_MAX_SECONDS = _env_int("ABUSE_BAN_MAX_SECONDS", 86400)

#: 统计表最大条目数，防止海量随机 IP 撑爆内存。
MAX_TRACKED_IPS = _env_int("ABUSE_MAX_TRACKED_IPS", 20000)

#: 永不封禁的地址（本机 + 局域网），避免把自己锁死。
_NEVER_BAN = frozenset(
    x.strip() for x in os.getenv(
        "ABUSE_NEVER_BAN",
        "127.0.0.1,::1,localhost").split(",") if x.strip()
)

_NEVER_BAN_PREFIXES = ("10.", "192.168.", "172.16.", "172.17.", "172.18.",
                       "172.19.", "172.2", "172.30.", "172.31.")


# ---------------------------------------------------------------------------
# 统计状态
# ---------------------------------------------------------------------------

_lock = Lock()
#: ip -> deque[(ts, status)]
_hits: dict[str, deque] = {}
#: ip -> (解封时间戳, 累计命中次数)
_banned: dict[str, tuple[float, int]] = {}


def _is_never_ban(ip: str) -> bool:
    if not ip:
        return True
    if ip in _NEVER_BAN:
        return True
    return ip.startswith(_NEVER_BAN_PREFIXES)


def _prune(bucket: deque, now: float) -> None:
    """丢弃窗口外的记录。"""
    edge = now - WINDOW_SECONDS
    while bucket and bucket[0][0] < edge:
        bucket.popleft()


def _trim_global(now: float) -> None:
    """表超过上限时，清掉窗口内无记录的空桶。"""
    if len(_hits) <= MAX_TRACKED_IPS:
        return
    edge = now - WINDOW_SECONDS
    for ip in list(_hits):
        b = _hits.get(ip)
        if b is None:
            continue
        if not b or b[-1][0] < edge:
            _hits.pop(ip, None)


def is_banned(ip: str) -> tuple[bool, int]:
    """该 IP 是否处于封禁中。返回 (是否封禁, 剩余秒数)。"""
    if _is_never_ban(ip):
        return False, 0
    now = time.time()
    with _lock:
        entry = _banned.get(ip)
        if not entry:
            return False, 0
        until, _count = entry
        if until <= now:
            _banned.pop(ip, None)
            return False, 0
        return True, int(until - now)


def _ban(ip: str, reason: str) -> None:
    """写入封禁（内存 + DB），并让 DB 缓存立即失效。"""
    now = time.time()
    with _lock:
        prev = _banned.get(ip)
        count = (prev[1] + 1) if prev else 1
        # 重复命中指数退避：10min → 20min → 40min …（封顶）
        seconds = min(BAN_SECONDS * (2 ** (count - 1)), BAN_MAX_SECONDS)
        _banned[ip] = (now + seconds, count)

    _logger.warning("abuse-guard: 封禁 %s %d 秒（%s，第 %d 次）", ip, seconds, reason, count)

    try:
        from admin.db import SessionLocal
        from admin.models import BlockedIP
        from admin.ip_access import invalidate_ip_cache

        with SessionLocal() as db:
            row = db.query(BlockedIP).filter(BlockedIP.ip == ip).first()
            expires_at = datetime.utcnow() + timedelta(seconds=seconds)
            note = f"auto: {reason}"[:255]
            if row:
                row.enabled = 1
                row.expires_at = expires_at
                row.note = note
            else:
                db.add(BlockedIP(ip=ip, enabled=1, note=note, expires_at=expires_at))
            db.commit()
        invalidate_ip_cache(ip)
    except Exception as exc:  # 风控写库失败绝不影响业务
        _logger.warning("abuse-guard: 写入封禁表失败 ip=%s err=%s", ip, exc)


def observe(ip: str, status: int) -> None:
    """记录一次请求结果，必要时触发封禁。

    在响应开始阶段调用。**不做任何阻塞操作**，判定与写库都很轻。
    """
    if _is_never_ban(ip):
        return
    # 已处于封禁中的 IP 不再累加。
    #
    # 这一步是必需的，否则会踩一个很隐蔽的坑：被封的 IP 若继续发请求，
    # 每次都会重新满足阈值、重新调 `_ban`，而 `_ban` 按「命中次数」做指数退避
    # —— 600s → 1200s → 2400s … 直接飙到上限，等于把「临时封禁」变成
    # 「准永久封禁」，且攻方只要持续打就能自己把封禁时间顶满。
    # 实测在未加此判断时，5 次命中就从 600s 涨到了 19200s。
    now = time.time()
    with _lock:
        entry = _banned.get(ip)
        if entry and entry[0] > now:
            return

    # 只关心确有意义的类别：成功请求只要不刷量就不该被针对。
    with _lock:
        bucket = _hits.get(ip)
        if bucket is None:
            if len(_hits) >= MAX_TRACKED_IPS:
                _trim_global(now)
            bucket = deque()
            _hits[ip] = bucket
        bucket.append((now, status))
        _prune(bucket, now)

        total = len(bucket)
        auth_fail = sum(1 for _, s in bucket if s in (401, 403))
        not_found = sum(1 for _, s in bucket if s == 404)

    reason = ""
    if total >= MAX_REQUESTS:
        reason = f"请求频率 {total}/{WINDOW_SECONDS}s"
    elif auth_fail >= MAX_AUTH_FAILURES:
        reason = f"鉴权失败 {auth_fail}/{WINDOW_SECONDS}s"
    elif not_found >= MAX_NOT_FOUND:
        reason = f"路径探测 {not_found}/{WINDOW_SECONDS}s"

    if reason:
        _ban(ip, reason)


def reset(ip: str | None = None) -> None:
    """清除统计（测试与运维用）。ip 为空则全清。"""
    with _lock:
        if ip:
            _hits.pop(ip, None)
            _banned.pop(ip, None)
        else:
            _hits.clear()
            _banned.clear()


def stats() -> dict:
    """当前风控水位（排障用）。"""
    now = time.time()
    with _lock:
        active = len(_banned)
        tracked = len(_hits)
        top = sorted(
            ((ip, len(b)) for ip, b in _hits.items()),
            key=lambda kv: kv[1], reverse=True)[:10]
    return {"tracked_ips": tracked, "banned_ips": active,
            "window_seconds": WINDOW_SECONDS, "ts": now, "top": top}


# ---------------------------------------------------------------------------
# 中间件
# ---------------------------------------------------------------------------


class AbuseGuardMiddleware:
    """全站异常行为守卫。

    与 `IPBlockMiddleware` 的分工：
      * `IPBlockMiddleware` 负责**执行已有封禁**，但只管 `/v1/*`；
      * 本中间件负责**发现并制造封禁**，覆盖**所有路径**（尤其 `/api/*`）。

    两者叠加后，一条请求会依次经过「是否已被封 → 业务 → 是否该被封」。
    之所以不合并：网关路径有额外的设备指纹校验，改动风险大；
    拆开可以在不动原有逻辑的前提下，把漏掉的 `/api/*` 补上。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)

        from fastapi import Request
        from fastapi.responses import JSONResponse
        from starlette.concurrency import run_in_threadpool
        from admin.client_ip import request_ip

        try:
            ip = request_ip(Request(scope))
        except Exception:
            ip = ""

        banned, left = is_banned(ip)
        if banned:
            response = JSONResponse(
                {"detail": f"请求过于频繁，请 {left} 秒后再试"},
                status_code=429,
                headers={"Retry-After": str(left), "Cache-Control": "no-store"})
            return await response(scope, receive, send)

        status_code = {"v": 500}

        async def observing_send(message):
            if message["type"] == "http.response.start":
                status_code["v"] = int(message.get("status", 500))
            await send(message)

        try:
            await self.app(scope, receive, observing_send)
        finally:
            # 统计放到线程池：写封禁表可能落盘，不能阻塞事件循环。
            # 失败一律吞掉——风控模块自身绝不能影响业务响应。
            try:
                await run_in_threadpool(observe, ip, status_code["v"])
            except Exception:
                pass

