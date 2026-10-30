"""平台网关调度：多平台之间怎么选。

## 这一层解决什么问题

系统已经能做到「一个 Key 调同一个模型，跨平台转发」，但**选哪个平台**
此前是隐含的 —— 谁有号就用谁。用户要的是可配置的策略：

    是随机？还是按优先级？让额度多的平台先用？还是快过期的先用？

并且要能**禁用某个平台** —— 禁用后它的模型权益不再参与调度，
但账号数据保留（随时可恢复）。

## 与账号级选号的分工

| 层 | 负责 | 落点 |
|---|---|---|
| **平台级**（本模块） | 一次请求该交给哪个**平台** | 先按策略排平台优先级 |
| 账号级 | 选定平台后该用哪个**账号** | `admin/routing_policy`（7 种模式） |

两层串联：平台优先级 -> 该平台的账号优先级。这样「跨平台」和
「平台内轮转」各自可配，互不干扰。

## 策略说明

| 模式 | 说明 |
|---|---|
| `priority` | 按用户设的显式优先级（数字小的先） |
| `random` | 随机，用于把负载摊到多个平台 |
| `balance` | 剩余额度多的平台优先（避免某个平台先耗尽） |
| `expiring` | 快到期/快结束的平台优先（先把要浪费的用掉） |
| `throughput` | 当前在途请求少的平台优先（降低延迟） |
| `round_robin` | 轮转，均匀分摊 |

## 禁用

禁用状态存 `system_settings`（跨重启保留），key：
    gateway:disabled = {"trae-cn": {"at": ..., "reason": "..."}}

被禁用的平台：
  * 不参与调度（`order()` 直接跳过）
  * 其账号仍可查看/刷新（管理操作不受影响）
  * `/v1/models` 不返回它的模型（避免客户端选到调不通的）
"""
from __future__ import annotations

import json
import logging
import random
import threading
import time
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger("admin.platform.gateway_router")

#: 策略 id -> (标签, 说明)。前端直接渲染这个列表。
STRATEGIES = (
    ("priority", "优先级", "按手动设置的优先级排序，数字小的平台先用。适合「主力平台 + 兜底平台」。"),
    ("balance", "额度优先", "剩余额度多的平台先用，避免某一个平台先被耗干。"),
    ("expiring", "到期优先", "快到期/快结束的平台先用 —— 先把即将浪费的额度用掉。"),
    ("throughput", "吞吐优先", "当前在途请求少的平台先用，降低排队延迟。"),
    ("round_robin", "轮转", "各平台平均分摊，不偏向任何一个。"),
    ("random", "随机", "随机选择，最简单也最均匀。"),
)

STRATEGY_IDS = tuple(item[0] for item in STRATEGIES)
DEFAULT_STRATEGY = "priority"

#: 存放在 `system_settings` 的键
SETTING_KEY = "gateway:routing"
DISABLED_KEY = "gateway:disabled"


# ---------------------------------------------------------------------------
# 配置读写
# ---------------------------------------------------------------------------
def _load_json(db: Session, key: str, default: Any) -> Any:
    from admin.models import SystemSetting

    row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    if not row or not row.value:
        return default
    try:
        data = json.loads(row.value)
    except (ValueError, TypeError):
        return default
    return data if isinstance(data, type(default)) else default


def _save_json(db: Session, key: str, value: Any) -> None:
    from admin.models import SystemSetting

    row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    payload = json.dumps(value, ensure_ascii=False)
    if row is None:
        db.add(SystemSetting(key=key, value=payload))
    else:
        row.value = payload
    db.commit()


def routing_config(db: Session) -> dict:
    """当前调度配置。

    返回：
        {"strategy": "priority",
         "priority": {"workbuddy": 10, "trae-cn": 20, ...},
         "disabled": {"trae-intl": {"at": ..., "reason": "..."}}}

    `priority` 数字越小越优先；未设置的平台排在最后（默认 100）。
    """
    raw = _load_json(db, SETTING_KEY, {})
    strategy = str(raw.get("strategy") or DEFAULT_STRATEGY)
    if strategy not in STRATEGY_IDS:
        strategy = DEFAULT_STRATEGY
    priority = raw.get("priority") or {}
    if not isinstance(priority, dict):
        priority = {}
    return {
        "strategy": strategy,
        "priority": {str(k): int(v) for k, v in priority.items()
                     if isinstance(v, (int, float))},
        "disabled": disabled_map(db),
        # 无头请求（不带 X-Platform / 无绑定 Key）是否参与负载分摊。
        #
        # **默认 False**：`resolve()` 的语义是「没指定 -> 默认平台」。
        # 打开后 random/balance 会把老客户端的请求随机分到别的平台，
        # 表现为「模型全 404」且日志里看不出原因（实测踩过）。
        # 只有「多平台聚合网关」那种场景才该打开。
        "spread_unbound": bool(raw.get("spread_unbound")),
        "strategies": [{"value": v, "label": l, "description": d}
                       for v, l, d in STRATEGIES],
    }


def save_routing(db: Session, *, strategy: str | None = None,
                 priority: dict | None = None,
                 spread_unbound: bool | None = None,
                 disabled: dict | None = None) -> dict:
    """保存网关策略。

    `spread_unbound`（默认 False）：无头请求是否参与负载分摊。
    打开后 random/balance 会把不带 X-Platform 的请求分到别的平台 ——
    只适合「多平台聚合」场景，模型 ID 不一致时会 404。
    """
    raw = _load_json(db, SETTING_KEY, {})
    if strategy is not None:
        if strategy not in STRATEGY_IDS:
            raise ValueError("未知策略：%s" % strategy)
        raw["strategy"] = strategy
    if priority is not None:
        cleaned = {}
        for key, value in (priority or {}).items():
            try:
                cleaned[str(key)] = max(0, min(9999, int(value)))
            except (TypeError, ValueError):
                continue
        raw["priority"] = cleaned
    if spread_unbound is not None:
        raw["spread_unbound"] = bool(spread_unbound)
    _save_json(db, SETTING_KEY, raw)

    # 禁用开关是**独立的一份设置**（DISABLED_KEY），但前端习惯一次提交
    # 整个 config。这里一并处理，否则 `PUT /config {disabled:{...}}` 会被
    # 静默丢弃 —— 表现就是「点了禁用没反应」。
    if disabled is not None:
        current = disabled_map(db)
        for platform, flag in (disabled or {}).items():
            pid = str(platform or "").strip().lower()
            if not pid:
                continue
            if flag:
                current.setdefault(pid, {"at": int(time.time()),
                                         "reason": "界面禁用"})
            else:
                current.pop(pid, None)
        _save_json(db, DISABLED_KEY, current)

    invalidate()   # 配置变了，缓存立刻失效
    return routing_config(db)


def disabled_map(db: Session) -> dict:
    """被禁用的平台 -> 禁用信息。"""
    raw = _load_json(db, DISABLED_KEY, {})
    return raw if isinstance(raw, dict) else {}


def set_enabled(db: Session, platform_id: str, enabled: bool,
                reason: str = "") -> dict:
    """启用 / 禁用某个平台网关。

    禁用只影响**调度**：账号仍可查看、刷新、导入。
    这样「临时停用某个平台」不会逼用户删数据。
    """
    data = disabled_map(db)
    if enabled:
        data.pop(platform_id, None)
    else:
        data[platform_id] = {"at": time.time(),
                             "reason": (reason or "手动禁用")[:200]}
    _save_json(db, DISABLED_KEY, data)
    invalidate()   # 禁用/启用会改变可用平台集合
    return data


# ---------------------------------------------------------------------------
# 健康状态
# ---------------------------------------------------------------------------
def health(db: Session, platform_id: str) -> dict:
    """单平台健康度（供策略排序与界面展示）。

    指标都是**已有数据**的汇总，不做额外的上游请求
    （那会让「打开页面」变成一次探测风暴）。
    """
    from admin.models import Account
    from admin.platform import registry

    spec = registry.get(platform_id)
    if spec is None:
        return {"platform": platform_id, "ok": False, "error": "未知平台"}

    rows = db.query(Account).filter(
        Account.platform == spec.storage()).all()
    if spec.realm:
        rows = [r for r in rows if (r.domain or "") == spec.realm]

    now = time.time()
    active = [r for r in rows if (getattr(r, "status", "") or "") == "active"]
    cooling = 0
    remain = 0
    limit = 0
    soonest = None
    for row in rows:
        cool_until = getattr(row, "cool_until", None)
        if cool_until and cool_until.timestamp() > now:
            cooling += 1
        remain += int(getattr(row, "balance_remain", 0) or 0)
        limit += int(getattr(row, "balance_total", 0) or 0)
        expiring = int(getattr(row, "credits_expiring_soon", 0) or 0)
        if expiring > 0:
            ts = getattr(row, "credits_soonest_expire_at", None)
            if ts is not None:
                secs = ts.timestamp()
                if soonest is None or secs < soonest:
                    soonest = secs

    usable = len(active) - cooling
    return {
        "platform": platform_id,
        "label": spec.label,
        "ok": usable > 0,
        "accounts": len(rows),
        "active": len(active),
        "cooling": cooling,
        "usable": max(0, usable),
        "balance_remain": remain,
        "balance_total": limit,
        # 剩余比例：策略排序用；总额为 0 时给 None（不冒充 0%）
        "balance_ratio": (remain / limit) if limit > 0 else None,
        "soonest_expire_at": soonest,
        "disabled": platform_id in disabled_map(db),
    }


def health_all(db: Session) -> list[dict]:
    """全部平台的健康度。"""
    from admin.platform import registry
    from admin.platform.loader import load_platforms

    load_platforms()
    return [health(db, pid) for pid in registry.ids()]


# ---------------------------------------------------------------------------
# 调度：排序
# ---------------------------------------------------------------------------
def order(db: Session, platform_ids: list[str] | None = None, *,
          include_unavailable: bool = False) -> list[dict]:
    """按当前策略给出平台优先级顺序。

    `include_unavailable=True` 时**保留没有可用账号的平台**，并标
    `available=False` + `reason="无可用账号"`。

    ## 为什么需要这个开关

    默认（False）会跳过不可用平台 —— 这是**调度**要的语义：排上去只会白跑。
    但**界面展示**需要相反的行为：一个多平台网关如果只列出「当前有账号」的
    那一个平台，用户会以为其他平台没接进来。

    所以调度与展示各用各的：调度保持严格过滤，展示用 include_unavailable=True。

    返回值是 `[{platform, label, reason, available, ...health}]`。
    """
    from admin.platform import registry
    from admin.platform.loader import load_platforms

    load_platforms()
    targets = platform_ids or list(registry.ids())
    cfg = routing_config(db)
    disabled = cfg["disabled"]
    strategy = cfg["strategy"]
    priority = cfg["priority"]

    pool = []
    for pid in targets:
        if pid in disabled:
            if include_unavailable:
                item = health(db, pid)
                item["available"] = False
                item["reason"] = "已禁用"
                pool.append(item)
            continue
        item = health(db, pid)
        item["available"] = bool(item.get("ok"))
        if not item.get("ok"):
            # 默认跳过：没有可用账号的平台排上去只会白跑一轮。
            # 展示场景保留它，前端据此显示「无可用账号」。
            if include_unavailable:
                item["reason"] = "无可用账号"
                pool.append(item)
            continue
        pool.append(item)

    from admin.platform import registry as _registry

    default_platform = getattr(_registry, "DEFAULT_PLATFORM", "workbuddy")

    def _priority_key(item):
        """排序键：显式优先级 > 是否默认平台 > 字典序。

        中间那个 `is_default` 是必须的：没有它，新增一个字母序靠前的平台
        会悄悄接管所有「未指定平台」的请求 —— 表现是「加了个平台，
        老请求全跑到新平台去了」，而且从日志里极难看出原因。
        """
        return (priority.get(item["platform"], 100),
                0 if item["platform"] == default_platform else 1,
                item["platform"])

    if strategy == "priority":
        # 只给**可用**的平台写策略原因；不可用的保留「无可用账号 / 已禁用」，
        # 否则界面上会出现「无可用账号」却写着「优先级 100」的矛盾文案。
        pool.sort(key=_priority_key)
        for item in pool:
            if item.get("available", True):
                item["reason"] = "优先级 %d" % priority.get(item["platform"], 100)

    elif strategy == "balance":
        # 剩余额度多的先：比例优先（避免大额度平台永远压着小平台）。
        #
        # ⚠️ 但**默认平台必须最靠前**：`resolve()` 的语义是
        # 「没指定平台 -> 默认平台」。让 balance 把默认平台顶掉，
        # 会导致老客户端的无头请求全打到新平台（模型 ID 完全不同 -> 全 404），
        # 且从日志看不出原因（请求成功了，只是走了别的平台）。
        pool.sort(key=lambda i: (
            0 if i["platform"] == default_platform else 1,
            -(i.get("balance_ratio") or 0),
            -(i.get("balance_remain") or 0),
            _priority_key(i)))
        for item in pool:
            ratio = item.get("balance_ratio")
            item["reason"] = ("剩余 %s" % ("%.0f%%" % (ratio * 100) if ratio is not None
                                          else item.get("balance_remain")))

    elif strategy == "expiring":
        # 快过期的先（借用额度）。
        #
        # ⚠️ 同样要**默认平台最靠前** —— 理由与 balance 一致：
        # `resolve()` 语义是「无头请求 -> 默认平台」。
        pool.sort(key=lambda i: (
            0 if i["platform"] == default_platform else 1,
            i.get("soonest_expire_at") is None,
            i.get("soonest_expire_at") or 0,
            _priority_key(i)))
        for item in pool:
            ts = item.get("soonest_expire_at")
            item["reason"] = (
                "最近到期 %s" % time.strftime("%m-%d", time.localtime(ts))
                if ts else "无临期额度")
    elif strategy == "throughput":
        # 在途少的先。`usable` 是可用账号数，作为简单近似（避免引入额外锁）
        pool.sort(key=lambda i: (-i.get("usable") or 0, _priority_key(i)))
        for item in pool:
            item["reason"] = "%d 个可用账号" % item.get("usable", 0)

    elif strategy == "round_robin":
        # 用「分钟数 % 平台数」做确定性轮转，避免每次请求都变
        if pool:
            slot = int(time.time() // 60) % len(pool)
            pool = pool[slot:] + pool[:slot]
        for index, item in enumerate(pool):
            item["reason"] = "轮转位次 %d" % (index + 1)

    elif strategy == "random":
        random.shuffle(pool)
        for item in pool:
            item["reason"] = "随机"

    # 上面每个策略分支都会给池里**所有**项写 reason（它们默认都是可用的）。
    # 开了 include_unavailable 之后池里混进了不可用的平台，如果不兜这一下，
    # 界面上就会出现「无可用账号」却写着「优先级 100 / 随机」的矛盾文案。
    # 不可用的原因只在这里写一次，优先级最高。
    for item in pool:
        if not item.get("available", True):
            item["reason"] = "已禁用" if item["platform"] in disabled else "无可用账号"

    return pool


def pick(db: Session, platform_ids: list[str] | None = None) -> dict | None:
    """按策略选出**最优先的一个平台**。没有可用平台时返回 None。"""
    ordered = order(db, platform_ids)
    return ordered[0] if ordered else None


# ---------------------------------------------------------------------------
# 热路径缓存
# ---------------------------------------------------------------------------
#: 缓存有效期（秒）。见模块内 `cached_pick` 的说明。
CACHE_TTL = 5.0

_cache_lock = threading.Lock()
_cache: dict = {"at": 0.0, "value": None, "generation": 0}
#: 每次配置变更自增；缓存记录代次，不一致就失效（比时间戳更精确）
_generation = 0


def invalidate() -> None:
    """配置变更后调用：让下一次 `cached_pick` 重新计算。

    `save_routing` / `set_enabled` 内部已自动调用，外部不必手动调。
    """
    global _generation
    with _cache_lock:
        _generation += 1
        _cache["value"] = None
        _cache["at"] = 0.0
    # dispatch 里还有两份缓存（禁用集合、平台顺序），必须一起清，
    # 否则「刚禁用还在用 / 取消禁用还是 403」。
    try:
        from admin.platform import dispatch

        dispatch.invalidate_platform_cache()
    except Exception:  # noqa: BLE001 - 循环导入或未加载时忽略
        pass


def cached_pick() -> tuple[str, str] | None:
    """`pick()` 的缓存版本，供转发热路径使用。

    返回 `(platform_id, realm)` 或 None。**不抛异常** ——
    策略坏了不能挡住转发，调用方会回落默认平台。
    """
    global _cache

    now = time.time()
    with _cache_lock:
        snapshot = dict(_cache)
        gen = _generation

    # 命中：代次一致且未过期
    if (snapshot.get("value") is not None
            and snapshot.get("generation") == gen
            and now - float(snapshot.get("at") or 0) < CACHE_TTL):
        return snapshot["value"]

    # 未命中：查库（单飞 —— 同时刻只让一个线程查）
    try:
        from admin.db import SessionLocal

        with SessionLocal() as db:
            best = pick(db)
    except Exception:  # noqa: BLE001 - 策略失败降级
        logger.debug("平台策略计算失败", exc_info=True)
        return None

    value = None
    if best and best.get("platform"):
        from admin.platform import registry

        spec = registry.get(str(best["platform"]))
        if spec is not None:
            value = (str(best["platform"]), spec.realm)

    with _cache_lock:
        # 只有代次没被别的线程推进时才写回，避免把旧结果盖在新配置上
        if _generation == gen:
            _cache = {"at": time.time(), "value": value, "generation": gen}
    return value
