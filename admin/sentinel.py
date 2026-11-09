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

#: 业务码 -> 人类可读原因（控制台直接展示）——**旧版全局表，仅作兜底**。
#:
#: ⚠️ 不同平台的码空间**完全独立**：11140 是 WorkBuddy 的账号级风控，
#: 10605 是 Qoder 的排队码，4015 是 Trae 的风控码。把它们混在一张表里
#: 会出现两个真实问题：
#:   1. **同名不同义**：`1001` 在 Trae 是鉴权失败，在别处可能是别的含义；
#:   2. **同义不同码**：WorkBuddy 的「退出登录」是 12153，Qoder 是
#:      TOKEN_INVALID —— 混在一起就没法按平台统计。
#: 所以真正生效的是下面的 `PLATFORM_SIGNALS`（按平台分表）；
#: 这张表只服务于「未知平台 / 历史事件」，且不参与严重程度判定。
CODE_LABELS = {
    "11140": "账号级授权风控（11140）",
    "11142": "账号级授权风控（11142）",
    "14017": "账号级授权风控（14017）",
    "14015": "授权异常（14015）",
    "14016": "授权异常（14016）",
    "12153": "登录态失效（12153）",
    "11128": "渠道校验未通过（11128）",
    "403": "出口被拦（HTTP 403）",
    "4015": "Trae 账号/设备风控（4015）",
    "4019": "Trae 区域/设备风控（4019）",
    "4008": "Trae 额度耗尽（4008）",
    "1001": "Trae 鉴权失败（1001）",
    "4023": "Trae 模型不存在（4023）",
    "TOKEN_INVALID": "Qoder 凭证无效（TOKEN_INVALID）",
    "101": "Qoder 签名无效（101）",
    "10605": "Qoder 排队中（10605，可重试）",
    "Signature invalid": "Qoder 签名无效",
}

# ---------------------------------------------------------------------------
# 严重程度：**只有 risk 参与熔断**，其余一律只记录
# ---------------------------------------------------------------------------
#: 信号分四类。这不是"严重度排序"，而是**处置方式不同**：
#:
#:   risk      上游真在拦（WAF / 渠道指纹 / 账号级风控 / 签名错）
#:             → 计入熔断：同一窗口内多个账号中招，说明出口或批次被抓了。
#:   throttle  限速 / 限流 / 排队 / 额度耗尽
#:             → **只记录**：这是"现在打不进去"，不是"号要没了"。
#:               拿它熔断会把整个平台误关，而且几分钟后自己就好了。
#:   auth      登录失效 / 退出登录 / token 过期
#:             → **只记录**：用户说的「退出登录不算风控，但可以记录」。
#:               它需要的是重新登录，不是止损。
#:   info      其它值得留痕但无处置动作的信号
SEVERITY_RISK = "risk"
SEVERITY_THROTTLE = "throttle"
SEVERITY_AUTH = "auth"
SEVERITY_INFO = "info"

SEVERITIES = (SEVERITY_RISK, SEVERITY_THROTTLE, SEVERITY_AUTH, SEVERITY_INFO)

#: 全局 kind -> severity 兜底映射。
#:
#: 各平台自己的 `PLATFORM_SIGNALS` 优先；这里只兜住"各平台同名同义"的通用 kind。
#: kind 名字直接取自各平台的分类函数，例如 WorkBuddy 的
#: `admin/routers/proxy.py::_classify_error` 会返回
#: `soft_rate` / `session_dead` / `account_fault` / `waf` / `upstream_internal`。
KIND_SEVERITY = {
    # 真封控
    "waf": SEVERITY_RISK,
    "channel_blocked": SEVERITY_RISK,
    "account_fault": SEVERITY_RISK,
    "risk_control": SEVERITY_RISK,
    "signature_invalid": SEVERITY_RISK,
    "upstream_auth_anomaly": SEVERITY_RISK,
    # 限速 / 限流 / 排队 / 额度
    # `soft_rate` 是 WorkBuddy 的「软限流」：可重试、不封号，只该记录。
    "soft_rate": SEVERITY_THROTTLE,
    "rate_limit": SEVERITY_THROTTLE,
    "throttle": SEVERITY_THROTTLE,
    "queue": SEVERITY_THROTTLE,
    "quota_exceeded": SEVERITY_THROTTLE,
    "upstream_business": SEVERITY_THROTTLE,
    # 登录态
    "auth": SEVERITY_AUTH,
    "session_dead": SEVERITY_AUTH,
    "logout": SEVERITY_AUTH,
    "token_expired": SEVERITY_AUTH,
    # 纯留痕（请求/配置问题，换号也没用）
    "model_unknown": SEVERITY_INFO,
    "invalid_request": SEVERITY_INFO,
    "upstream_internal": SEVERITY_INFO,
}

#: 通用信号名（跨平台语义一致的那几个），前端按它显示中文。
SIGNAL_LABELS = {
    "risk_control": "账号/设备风控",
    "channel_blocked": "渠道校验未通过",
    "waf": "出口 IP 被拦",
    "signature_invalid": "请求签名无效",
    "account_fault": "账号授权异常",
    "rate_limit": "限速",
    "throttle": "限流",
    "queue": "上游排队",
    "quota_exceeded": "额度耗尽",
    "auth_expired": "登录态失效",
    "logout": "退出登录",
    "unknown": "未归类信号",
}

# ---------------------------------------------------------------------------
# 按平台分表：**每个网关上报自己的真实码**
# ---------------------------------------------------------------------------
#: platform(storage) -> {code: {"label": str, "signal": str, "severity": str}}
#:
#: 这张表是"事实来源"：平台网关只需要把上游返回的**原始码**传给
#: `record()` / `note_failure()`，中文解释、归一化信号名、严重程度都在这里定
#: —— 避免每个调用点各写一套，也避免跨平台混用码空间。
#:
#: 没收录的码不会丢：`resolve()` 会退回 kind 的严重程度，并用
#: `"<码> · <kind>"` 作为标签，同时把 `unmapped` 标出来，方便补表。
PLATFORM_SIGNALS = {
    # ---- WorkBuddy ----
    "workbuddy": {
        "11128": {"label": "渠道校验未通过（客户端指纹被识破）",
                  "signal": "channel_blocked", "severity": SEVERITY_RISK},
        "11140": {"label": "账号级授权风控", "signal": "risk_control",
                  "severity": SEVERITY_RISK},
        "11142": {"label": "账号级授权风控", "signal": "risk_control",
                  "severity": SEVERITY_RISK},
        "14017": {"label": "账号级授权风控", "signal": "risk_control",
                  "severity": SEVERITY_RISK},
        "14015": {"label": "授权异常", "signal": "account_fault",
                  "severity": SEVERITY_RISK},
        "14016": {"label": "授权异常", "signal": "account_fault",
                  "severity": SEVERITY_RISK},
        # **退出登录**：用户明确要求「记录，但不算风控」
        "12153": {"label": "登录态失效（需重新登录）",
                  "signal": "auth_expired", "severity": SEVERITY_AUTH},
        "403": {"label": "出口被拦（HTTP 403）", "signal": "waf",
                "severity": SEVERITY_RISK},
    },
    # ---- Trae（国内版/国际版共用） ----
    "trae": {
        "4015": {"label": "账号/设备风控", "signal": "risk_control",
                 "severity": SEVERITY_RISK},
        "4019": {"label": "区域/设备风控", "signal": "risk_control",
                 "severity": SEVERITY_RISK},
        "4008": {"label": "额度耗尽", "signal": "quota_exceeded",
                 "severity": SEVERITY_THROTTLE},
        "1001": {"label": "鉴权失败（token 失效或被吊销）",
                 "signal": "auth_expired", "severity": SEVERITY_AUTH},
        "4023": {"label": "模型不存在（请求写错了）", "signal": "unknown",
                 "severity": SEVERITY_INFO},
    },
    # ---- Qoder ----
    "qoder": {
        "10605": {"label": "上游排队中（可重试）", "signal": "queue",
                  "severity": SEVERITY_THROTTLE},
        "TOKEN_INVALID": {"label": "凭证无效（需重新登录）",
                          "signal": "auth_expired", "severity": SEVERITY_AUTH},
        "101": {"label": "请求签名无效", "signal": "signature_invalid",
                "severity": SEVERITY_RISK},
        "Signature invalid": {"label": "请求签名无效",
                              "signal": "signature_invalid",
                              "severity": SEVERITY_RISK},
    },
    # ---- Loomy ----
    "loomy": {
        "402": {"label": "积分不足", "signal": "quota_exceeded",
                "severity": SEVERITY_THROTTLE},
        "invalid_api_key": {"label": "凭证无效（需重新登录）",
                            "signal": "auth_expired",
                            "severity": SEVERITY_AUTH},
        "429": {"label": "限流", "signal": "throttle",
                "severity": SEVERITY_THROTTLE},
    },
    # ---- 小浣熊 ----
    "raccoon": {
        "200901": {"label": "需要 IAM 二次确认", "signal": "unknown",
                   "severity": SEVERITY_INFO},
    },
    # ---- WPS 灵犀 ----
    "wps-lingxi": {
        "AIError": {"label": "AI 服务异常", "signal": "unknown",
                    "severity": SEVERITY_INFO},
        "InvalidArgument": {"label": "参数错误（请求问题）",
                            "signal": "unknown", "severity": SEVERITY_INFO},
        "401": {"label": "登录态失效（需重新登录）", "signal": "auth_expired",
                "severity": SEVERITY_AUTH},
    },
}


def resolve(platform: str, code: str = "", kind: str = "") -> dict:
    """把 (平台, 码, kind) 解析成 `{label, signal, severity, unmapped}`。

    查找顺序：本平台码表 → 该平台其它别名的码表（`trae-cn` 归一到 `trae`）
    → kind 的通用严重程度 → 兜底 info。
    """
    platform = str(platform or "").strip().lower()
    code = str(code or "").strip()
    kind = str(kind or "").strip()
    table = PLATFORM_SIGNALS.get(platform) or {}
    if not table:
        # 单区平台：`trae-cn` / `trae-intl` / `qoder-intl` 归一到 storage 名
        base = platform.replace("-cn", "").replace("-intl", "")
        if base != platform:
            table = PLATFORM_SIGNALS.get(base) or {}
    entry = table.get(code) if code else None
    if isinstance(entry, dict):
        return {
            "label": str(entry.get("label") or code),
            "signal": str(entry.get("signal") or kind or "unknown"),
            "severity": str(entry.get("severity") or SEVERITY_INFO),
            "unmapped": False,
        }
    severity = KIND_SEVERITY.get(kind)
    if severity is None and code in ("401", "403"):
        severity = SEVERITY_RISK
    signal = kind or "unknown"
    label = CODE_LABELS.get(code) or (kind and SIGNAL_LABELS.get(kind, "")) or ""
    if not label:
        label = ("%s %s" % (code, kind)).strip() or "未知信号"
    return {
        "label": label,
        "signal": signal,
        "severity": severity or SEVERITY_INFO,
        # 码没收录进平台表：前端会给个"未归类"角标，方便回头补 PLATFORM_SIGNALS
        "unmapped": bool(code),
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
           model: str = "", at: float | None = None,
           signal: str = "", severity: str = "") -> dict:
    """记录一条上游信号。

    这是**唯一**在转发路径上调用的函数，必须便宜：一次 Redis `ZADD` 或
    一次内存 append，不做任何查询。任何异常都被吞掉 —— 哨兵坏了不能反过来
    影响转发。

    **不限平台**：任何平台上报都记录。熔断按 platform 分键，A 平台不影响 B。

    ## 平台必须自己上报真实语义

    `signal` / `severity` 由**各平台网关**给出（它最清楚自己的码是什么意思）：

        signal    归一后的信号名（rate_limit / queue / quota_exceeded /
                  auth_expired / risk_control …）
        severity  risk / throttle / auth / info，**只有 risk 参与熔断**

    调用方不传时按 `PLATFORM_SIGNALS[平台][码]` 解析（`resolve()`），
    所以平台只要把原始码传进来就已经有正确分类。
    """
    platform = str(platform or "").strip().lower()
    if SENTINEL_PLATFORMS is not None and platform not in SENTINEL_PLATFORMS:
        return {"skipped": "platform_not_monitored", "platform": platform}
    now = at if at is not None else time.time()
    resolved = resolve(platform, code, kind)
    final_signal = str(signal or resolved["signal"] or kind or "unknown")
    final_severity = str(severity or resolved["severity"] or SEVERITY_INFO)
    if final_severity not in SEVERITIES:
        final_severity = SEVERITY_INFO
    # 标签带上原始码：面板上光看「账号/设备风控」没法对账上游，
    # 补一个 `（4015）` 才好和客户端日志 / 抓包对上。
    final_label = label or resolved["label"]
    code_text = str(code or "")
    if code_text and code_text not in final_label:
        final_label = "%s（%s）" % (final_label, code_text)
    event = {
        "ts": round(now, 3),
        "account_id": int(account_id or 0),
        "kind": str(kind or ""),
        "code": code_text,
        # 标签：调用方显式给了就用它（平台自己的文案优先），否则用平台表
        "label": final_label,
        "signal": final_signal,
        "signal_label": SIGNAL_LABELS.get(final_signal, final_signal),
        "severity": final_severity,
        "unmapped": bool(resolved.get("unmapped")),
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
                 model: str = "", signal: str = "",
                 severity: str = "") -> dict | None:
    """按错误种类决定是否值得记录。返回事件（不记录时返回 None）。

    ## 两件事都要做（2026-11-09 修正）

    1. **什么都记**：限速 / 限流 / 排队 / 额度耗尽 / 登录失效都要留痕 ——
       用户明确要求「退出登录不算风控，但可以记录」。
    2. **只有 `severity == "risk"` 参与熔断**：把「排队」「退出登录」算进
       熔断会在一个正常波动的平台上误关整个平台。

    也就是说：**记录**是宽口径，**熔断**是窄口径。以前用一个
    `RISK_KINDS` 同时管两件事，那个 5 个账号 `100002 登录失败` 的截图就是
    被误判成「批次级封控」的样子。
    """
    kind = str(kind or error_kind or "")
    code = str(code or "")
    severity_hint = str(severity or "")
    signal_hint = str(signal or "")

    # 11128（渠道校验未通过）在很多调用点被归成 invalid_request，
    # 只看 kind 会漏掉它 —— 但这是**真风控**，必须记。
    if kind in ("invalid_request", "") and code == "11128":
        kind = "channel_blocked"

    resolved = resolve(platform, code, kind)
    final_severity = severity_hint or resolved["severity"]
    final_signal = signal_hint or resolved["signal"]
    if not final_severity:
        return None
    # 什么信号都记：severity 决定它是否参与熔断，而不是决定"要不要记"。
    return record(platform, account_id, kind, code=code, status=status,
                  detail=detail, model=model,
                  signal=final_signal, severity=final_severity)


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


def by_account(platform: str = "", window: int = 600,
               *, risk_only: bool = False) -> dict[int, dict]:
    """按账号聚合：每个账号最近一次信号 + 累计次数。

    `risk_only=True` 只统计 `severity == "risk"` 的事件 —— 熔断判定用它，
    避免「5 个号同时退出登录」被当成批次封控。
    """
    result: dict[int, dict] = {}
    for event in events(platform, window):
        if risk_only and str(event.get("severity") or "") != SEVERITY_RISK:
            continue
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
                "signal": event.get("signal"),
                "signal_label": event.get("signal_label"),
                "severity": event.get("severity"),
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

    判据：**不同账号**在窗口内被**真风控**（`severity=risk`）的数量 >= 阈值。

    为什么按账号数而不是事件数：一个账号重试 5 次会产生 5 条事件，
    按事件数会立刻误判；按账号数才是「辐射范围」。

    为什么只算 risk：限速/排队/额度/登录失效都是**运营态**，
    全池同时出现是正常的。把它们算进来，一次上游抖动就会把自己的平台关掉。
    """
    window = max(30, int(window))
    per_account = by_account(platform, window, risk_only=True)
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
    """控制台用的汇总：状态 + 明细 + 熔断判定。

    ## 分平台、分信号、分严重程度

    面板上必须能看出「**哪个平台**的号、因为**什么信号**」，
    而不是一列混在一起的 `100002 auth`：
      · `by_platform` 平台 -> 事件数（含 label，前端直接显示平台名）
      · `by_signal`   归一信号名 -> 次数（限速/排队/额度/风控…）
      · `by_severity` risk / throttle / auth / info 四档计数
      · `by_code`     保留：原始码分布，排障时对账用
    """
    state = breach(platform, window)
    per_account = by_account(platform, 600)
    recent = events(platform, 600)
    codes: dict[str, int] = {}
    signals: dict[str, int] = {}
    severities: dict[str, int] = {key: 0 for key in SEVERITIES}
    platforms: dict[str, dict] = {}
    for event in recent:
        code_key = str(event.get("code") or event.get("kind") or "unknown")
        codes[code_key] = codes.get(code_key, 0) + 1
        signal = str(event.get("signal") or "unknown")
        signals[signal] = signals.get(signal, 0) + 1
        severity = str(event.get("severity") or SEVERITY_INFO)
        severities[severity] = severities.get(severity, 0) + 1
        pid = str(event.get("platform") or "unknown")
        bucket = platforms.setdefault(pid, {"platform": pid, "count": 0,
                                            "risk": 0, "last_ts": 0})
        bucket["count"] += 1
        if severity == SEVERITY_RISK:
            bucket["risk"] += 1
        bucket["last_ts"] = max(bucket["last_ts"], float(event.get("ts") or 0))
    # 熔断只看真风控：面板上的"涉及账号"也要同口径，否则用户会以为
    # 5 个退出登录的号 = 批次封控。
    risk_accounts = by_account(platform, 600, risk_only=True)
    return {
        "tripped": state["tripped"],
        "accounts_affected": state["accounts"],
        "accounts_total": len(per_account),
        "accounts_risk": len(risk_accounts),
        "threshold": state["threshold"],
        "window_seconds": state["window"],
        "suggested_pause_seconds": state["suggested_pause_seconds"],
        "total_events": len(recent),
        "by_code": codes,
        "by_signal": signals,
        "signal_labels": SIGNAL_LABELS,
        "by_severity": severities,
        "by_platform": sorted(platforms.values(),
                              key=lambda item: item["count"], reverse=True),
        "accounts": sorted(per_account.values(),
                           key=lambda item: item.get("last_ts") or 0, reverse=True)[:50],
        "recent": recent[:80],
        "redis": _client() is not None,
    }