"""风控哨兵：实时监测上游封控信号，必要时**主动止损**。

## 为什么必须有它

上游封号往往是**连坐**的：一次违规对话可能让整批账号被禁，甚至新号上去
一被识别又立刻再封一轮。等用户发现「全挂了」再处理，往往已经损失整池。

因此这里做两件事：
  1. **记录**每个账号当前处于什么状态（403 / 11140 / 额度耗尽 / 冷却中…），
     以及这些状态出现在哪个时间点、来自哪条日志 —— 事后能定位；
  2. **熔断**：同一出口在短窗口内连续出现多个账号被封控时，判定为
     「出口/批次级封控」，直接暂停该平台的新请求，避免继续送人头。

## 为什么不用数据库

风控信号是**高频、短命、只用于判断**的数据：每分钟可能有几十条，
但只需要看最近几分钟。写进 `usage_logs` 会让日志表迅速膨胀，而查询
「最近 5 分钟有多少 11140」还要扫全表。

所以：
  * 明细放 **Redis**（ZSET/LIST + TTL），过期自动清理，不需要运维介入；
  * Redis 不可用时**降级到进程内存**（进程重启即丢，但不会因为缓存挂了
    就把请求全拒了 —— 保命优先于精确）；
  * **只有真正需要长期留痕的事件**（账号被禁用、批次熔断）才写库
    （`last_upstream_error` / `cool_kind` 等既有字段），不新增表。

## 性能约束

热路径上只做 **O(1) 的 Redis 写**，不做任何范围查询。统计与判定放在
读取端（`state()` / `status()`），只在控制台轮询时才计算 —— 转发请求
本身绝不为哨兵付查询代价。
"""
from __future__ import annotations

import json
import logging
import threading
import time

from admin.config import settings

logger = logging.getLogger("admin.sentinel")

#: Redis key 前缀
PREFIX = "wb:sentinel:"

#: 明细保留时长（秒）。控制台看「最近 10 分钟」足够定位，再久就是日志的活。
EVENT_TTL = 900

#: 每个平台最多保留多少条明细（防止单个高流量平台把内存吃满）
MAX_EVENTS = 500

#: 熔断判定窗口（秒）与阈值
BREACH_WINDOW = 300
#: 窗口内出现多少个**不同账号**被风控即判定为批次级封控
BREACH_ACCOUNTS = 3

#: 哨兵**接受所有平台的上报**（不再有白名单）。
#:
#: ## 为什么取消白名单
#:
#: 早先这里限定只有 workbuddy 能进哨兵，理由是「Trae 的 4015/4019 语义未确认，
#: 误停整个平台代价太大」。但那个顾虑的正确解法不是**拒绝上报**，而是
#: **让平台自己判断什么该报**：
#:
#:   · 哨兵只负责聚合、去重、告警、熔断状态；
#:   · 「哪个业务码代表账号被封」是平台知识，由各平台网关决定是否调用
#:     `note_failure`（见 `admin.platform.gateway` 的职责边界）；
#:   · 熔断判定天然按 platform 分键（`wb:sentinel:<platform>:events`），
#:     所以 A 平台熔断**不会**影响 B 平台。
#:
#: 典型表现：「那边 B 平台炸了用不了，A 平台照样没问题，
#: 这个做进风控哨兵即可，不同平台的网关上报进去」—— 这正是当前设计。
#:
#: 保留这个常量为 `None` 是为了向后兼容（老代码可能引用它做判断），
#: 语义已改为「不限制任何平台」。
SENTINEL_PLATFORMS = None  # None = 不限平台

#: 被视为「风控信号」的错误种类。
#:
#: ## 为什么这张表装了两个平台的 kind
#:
#: `kind` 是**各平台自己的分类名**，哨兵不该要求它们统一 —— 那会让
#: 平台向 WorkBuddy 的命名妥协。所以这里做的是「联合表」：
#:
#:   WorkBuddy（`admin/routers/proxy.py::_classify_error`）：
#:     waf / account_fault / session_dead / upstream_auth_anomaly / channel_blocked
#:
#:   Trae（`trae/core/client.py::ERROR_MAP`）：
#:     risk_control（4015/4019 账号/设备风控）
#:     auth（1001 鉴权失败）
#:     quota_exceeded（4008 额度耗尽 —— 不是风控，但对熔断判定有意义）
#:
#: 新增平台时，把它自己的封控类 kind 加进来即可，不必改名。
RISK_KINDS = frozenset({
    # --- WorkBuddy ---
    "waf",                    # 出口 IP 被拦（403 无业务信封）
    "account_fault",          # 11140 / 11142 / 14017 账号级授权风控
    "session_dead",           # 登录态失效
    "upstream_auth_anomaly",  # 上游鉴权异常
    "channel_blocked",        # 11128 渠道校验未通过（客户端指纹被识破）
    # --- Trae ---
    "risk_control",           # 4015 / 4019 账号或设备风控
    "auth",                   # 1001 鉴权失败（token 失效/被吊销）
    "quota_exceeded",         # 4008 额度耗尽（参与熔断判定：全池耗尽也是故障）
    # --- Qoder ---
    "signature_invalid",      # COSY 签名错（配置问题，一出现就是全平台不可用）
    "upstream_business",      # SSE 信封里的业务错误（额度耗尽等）
})

#: 业务码 -> 人类可读原因（控制台直接展示）
CODE_LABELS = {
    "11140": "账号级授权风控（11140）",
    "11142": "账号级授权风控（11142）",
    "14017": "账号级授权风控（14017）",
    "14015": "授权异常（14015）",
    "14016": "授权异常（14016）",
    "12153": "登录态失效（12153）",
    # 11128：`Illegal API invocation from an unapproved channel`
    # —— 请求没通过上游的「官方客户端」渠道校验。这不是账号被封，
    # 而是**客户端指纹/渠道标识**被识破。性质同样严重：不改指纹就会被
    # 持续拒绝，所以必须进哨兵，否则用户只看到一连串 400 而不知根因。
    "11128": "渠道校验未通过（11128）",
    "403": "出口被拦（HTTP 403）",
    # --- Trae ---
    # 这几个码是 Trae 特有的，前端哨兵面板直接显示中文，
    # 不必让运维去翻上游文档。
    "4015": "Trae 账号/设备风控（4015）",
    "4019": "Trae 区域/设备风控（4019）",
    "4008": "Trae 额度耗尽（4008）",
    "1001": "Trae 鉴权失败（1001）",
    "4023": "Trae 模型不存在（4023）",
    # --- Qoder ---
    "TOKEN_INVALID": "Qoder 凭证无效（TOKEN_INVALID）",
    "101": "Qoder 签名无效（101）",
    "10605": "Qoder 排队中（10605，可重试）",
    "Signature invalid": "Qoder 签名无效",
}

_lock = threading.Lock()
_redis = None
_redis_ready = False
#: Redis 不可用时的进程内降级存储：platform -> [(ts, dict), ...]
_memory: dict[str, list[tuple[float, dict]]] = {}


# ---------------------------------------------------------------------------
# 存储层
# ---------------------------------------------------------------------------
def _client():
    """惰性建 Redis 连接；失败返回 None（走内存降级）。"""
    global _redis, _redis_ready
    with _lock:
        if _redis_ready:
            return _redis
        _redis_ready = True
        try:
            import redis
            # protocol=2：新版本 redis-py 默认发 `HELLO 3` 切 RESP3，
            # 而本项目跑的是**旧版 Redis 服务**（<6），会直接报
            # `unknown command HELLO`。固定 RESP2 才兼容。
            _redis = redis.Redis.from_url(
                settings.REDIS_URL, decode_responses=True,
                socket_connect_timeout=1.0, socket_timeout=1.0,
                protocol=2)
            _redis.ping()
            logger.info("风控哨兵已接入 Redis：%s", settings.REDIS_URL)
        except TypeError:
            # 老版本 redis-py 没有 protocol 参数
            try:
                import redis
                _redis = redis.Redis.from_url(
                    settings.REDIS_URL, decode_responses=True,
                    socket_connect_timeout=1.0, socket_timeout=1.0)
                _redis.ping()
                logger.info("风控哨兵已接入 Redis：%s", settings.REDIS_URL)
            except Exception as exc:  # noqa: BLE001 - 降级到内存
                _redis = None
                logger.warning("风控哨兵降级为进程内存（Redis 不可用：%s）", exc)
        except Exception as exc:  # noqa: BLE001 - 降级到内存
            _redis = None
            logger.warning("风控哨兵降级为进程内存（Redis 不可用：%s）", exc)
        return _redis


def reset() -> None:
    """清空内存状态并重连。

    同时清掉 Redis 里的明细 —— 只清内存的话，`reset()` 之后读到的还是
    Redis 里的旧事件（表现是「清空后计数不为 0」）。测试与「清空信号」
    按钮都依赖这个语义。
    """
    global _redis, _redis_ready
    client = _client()
    if client is not None:
        try:
            keys = list(client.scan_iter(match=PREFIX + "*:events", count=200))
            if keys:
                client.delete(*keys)
        except Exception:  # noqa: BLE001 - 清不掉也不该崩
            logger.debug("哨兵清理 Redis 失败", exc_info=True)
    with _lock:
        _redis = None
        _redis_ready = False
        _memory.clear()


def _key(platform: str, kind: str) -> str:
    return "%s%s:%s" % (PREFIX, platform or "unknown", kind)


# ---------------------------------------------------------------------------
# 写入（热路径，只做 O(1)）
# ---------------------------------------------------------------------------
def record(platform: str, account_id: int, kind: str, *, code: str = "",
           status: int = 0, label: str = "", detail: str = "",
           model: str = "", at: float | None = None) -> dict:
    """记录一条风控信号。

    这是**唯一**在转发路径上调用的函数，必须便宜：一次 Redis `ZADD` 或
    一次内存 append，不做任何查询。任何异常都被吞掉 —— 哨兵坏了不能反过来
    影响转发。

    **不限平台**：任何平台上报都记录。「哪个码算封控」由平台自己决定
    （见 `SENTINEL_PLATFORMS` 的注释），哨兵只做聚合。
    熔断按 platform 分键，所以 A 平台熔断不影响 B 平台。
    """
    platform = str(platform or "").strip().lower()
    if SENTINEL_PLATFORMS is not None and platform not in SENTINEL_PLATFORMS:
        return {"skipped": "platform_not_monitored", "platform": platform}
    now = at if at is not None else time.time()
    event = {
        "ts": round(now, 3),
        "account_id": int(account_id or 0),
        "kind": str(kind or ""),
        "code": str(code or ""),
        "status": int(status or 0),
        "label": label or CODE_LABELS.get(str(code), "") or kind or "未知信号",
        "detail": (detail or "")[:300],
        "model": str(model or ""),
        "platform": platform,
    }
    payload = json.dumps(event, ensure_ascii=False)
    client = _client()
    if client is not None:
        try:
            key = _key(platform, "events")
            # score = 时间戳：既能按时间取，也能自动过期清理旧数据
            client.zadd(key, {payload: now})
            client.expire(key, EVENT_TTL)
            # 只保留最近 MAX_EVENTS 条，防止高频平台把 Redis 撑大
            client.zremrangebyrank(key, 0, -(MAX_EVENTS + 1))
            return event
        except Exception:  # noqa: BLE001 - 降级
            logger.debug("哨兵写入 Redis 失败，改写内存", exc_info=True)
    with _lock:
        bucket = _memory.setdefault(platform or "unknown", [])
        bucket.append((now, event))
        cutoff = now - EVENT_TTL
        _memory[platform or "unknown"] = [item for item in bucket if item[0] >= cutoff][-MAX_EVENTS:]
    return event


def note_failure(platform: str, account_id: int, kind: str, *, error_kind: str = "",
                 code: str = "", status: int = 0, detail: str = "",
                 model: str = "") -> dict | None:
    """按错误种类决定是否要记为风控信号。返回事件（未记录时返回 None）。

    只记 RISK_KINDS —— 429、模型不存在、客户端的参数错都不算风控，
    记进来只会淹没真正的信号。
    """
    kind = str(kind or error_kind or "")
    # 按业务码兜底识别：11128（渠道校验未通过）在很多调用点会被归类成
    # `invalid_request` 之类的普通 kind，只靠 kind 过滤会漏掉它。
    # 这个码本质是风控（渠道指纹被识破），必须记下来。
    if not code and status in (401, 403):
        code = str(status)
    if kind not in RISK_KINDS:
        if str(code or "") == "11128":
            kind = "channel_blocked"
        else:
            return None
    return record(platform, account_id, kind, code=code, status=status,
                  detail=detail, model=model)


# ---------------------------------------------------------------------------
# 读取（控制台轮询才调用）
# ---------------------------------------------------------------------------
def events(platform: str = "", window: int = 600) -> list[dict]:
    """最近 `window` 秒内的明细，按时间倒序。"""
    since = time.time() - max(1, int(window))
    client = _client()
    if client is not None:
        try:
            if platform:
                keys = [_key(platform, "events")]
            else:
                keys = [k for k in client.scan_iter(match=PREFIX + "*:events", count=200)]
            out: list[dict] = []
            for key in keys:
                for raw in client.zrangebyscore(key, since, "+inf"):
                    try:
                        out.append(json.loads(raw))
                    except Exception:  # noqa: BLE001
                        continue
            out.sort(key=lambda item: item.get("ts", 0), reverse=True)
            return out
        except Exception:  # noqa: BLE001
            logger.debug("哨兵读取 Redis 失败，回退内存", exc_info=True)
    with _lock:
        names = [platform] if platform else list(_memory)
        out = []
        for name in names:
            for ts, event in _memory.get(name, []):
                if ts >= since:
                    out.append(event)
        out.sort(key=lambda item: item.get("ts", 0), reverse=True)
        return out


def by_account(platform: str = "", window: int = 600) -> dict[int, dict]:
    """按账号聚合：每个账号最近一次信号 + 累计次数。"""
    result: dict[int, dict] = {}
    for event in events(platform, window):
        acc = int(event.get("account_id") or 0)
        if not acc:
            continue
        item = result.get(acc)
        if item is None:
            result[acc] = {
                "account_id": acc,
                "count": 1,
                "first_ts": event.get("ts"),
                "last_ts": event.get("ts"),
                "kind": event.get("kind"),
                "code": event.get("code"),
                "label": event.get("label"),
                "detail": event.get("detail"),
                "platform": event.get("platform"),
            }
        else:
            item["count"] += 1
            # events() 已按时间倒序，第一条就是最新
    return result


def breach(platform: str = "", window: int = BREACH_WINDOW,
           threshold: int = BREACH_ACCOUNTS) -> dict:
    """判定是否处于「批次级封控」。

    判据：**不同账号**在窗口内被风控的数量 >= 阈值。
    为什么按账号数而不是事件数：一个账号重试 5 次会产生 5 条事件，
    按事件数会立刻误判；按账号数才是「辐射范围」。
    """
    window = max(30, int(window))
    per_account = by_account(platform, window)
    accounts = len(per_account)
    hit = accounts >= max(1, int(threshold))
    return {
        "tripped": hit,
        "accounts": accounts,
        "threshold": max(1, int(threshold)),
        "window": window,
        "since": time.time() - window,
        "sample": list(per_account.values())[:10],
        # 触发后建议的暂停时长：先短停观察，避免长期误封自己
        "suggested_pause_seconds": min(900, 120 * max(1, accounts // max(1, threshold))),
    }


def status(platform: str = "", window: int = BREACH_WINDOW) -> dict:
    """控制台用的汇总：状态 + 明细 + 熔断判定。"""
    state = breach(platform, window)
    per_account = by_account(platform, 600)
    recent = events(platform, 600)
    # 按 code 归类，给「哪个原因最多」一个直观分布
    codes: dict[str, int] = {}
    for event in recent:
        key = str(event.get("code") or event.get("kind") or "unknown")
        codes[key] = codes.get(key, 0) + 1
    return {
        "tripped": state["tripped"],
        "accounts_affected": state["accounts"],
        "threshold": state["threshold"],
        "window_seconds": state["window"],
        "suggested_pause_seconds": state["suggested_pause_seconds"],
        "total_events": len(recent),
        "by_code": codes,
        "accounts": sorted(per_account.values(),
                           key=lambda item: item.get("last_ts") or 0, reverse=True)[:50],
        "recent": recent[:80],
        "redis": _client() is not None,
    }