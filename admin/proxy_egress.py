"""全局出口（代理模块）：对话主链路挂代理、自动轮换、手动切换、连通性体检。

## 补的是 issue #39 的那个缺口

`admin/proxy_pool.py` 早就有「节点 × 单节点并发」的槽位分配，但只有
**定时任务 / 批量账号操作**在用它；对话主链路 `/v1/chat/completions`
创建 `AccountSession` 时没传 `proxy`，日常请求仍然从本机出口 IP 出去。
号一多，上游看到的就是「一个 IP 上几十上百个号在活动」。

这个模块把最后一段接上，并且把「出口怎么换」做成运行时可调的：

    · **全局开关**：打开后对话主链路的每个 `AccountSession` 都挂出口；
    · **自动轮换（auto）**：按 `rotate_seconds` 周期换节点，也可以
      `rotate_per_request=1` 每条请求换；上游 WAF 判定 IP 被拦
      （`admin/pool.py` 的 `POOL.waf`）时**立刻**换，不等周期；
    · **手动切换（fixed）**：锁定某个节点，运营想走哪条就走哪条，
      节点被拉黑/停用时自动落到下一个可用节点，**绝不静默退回本机 IP**；
    · **连通性体检**：真正发一次请求（默认 `https://api.ipify.org`），
      拿回延迟、状态码、**出口 IP**，面板上直接显示。

## 三个刻意的取舍

1. **账号绑定优先**：账号 `auth_json.proxy` 里单独配了出口的，仍然走它。
   同一个号固定一个 IP 比每次换 IP 更像真人，这个既有优先级不动。
2. **状态放进程内存**：当前节点、轮换计数、最近成功/失败都在内存里 ——
   热路径只多两次**读**（配置一条、可用节点一条），不写库、不 commit。
   多 worker 部署时每个 worker 各自持有当前节点，要全局一致就用 `fixed`
   模式（锁定节点在库里，所有 worker 读到同一份）。
3. **绝不静默直连**：开了全局出口但一个可用节点都没有时返回空串之前
   先把 `last_reason` 记成 `no-nodes`，`status()` 会带 warning 出来。
   「配了代理却还是从本机 IP 出去」是这类模块最容易骗过运营的故障。
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime

from sqlalchemy.orm import Session

_logger = logging.getLogger("admin.proxy_egress")

#: SystemSetting 的键（单键 JSON，和 `schedule:global_proxy` 同一套做法）。
SETTINGS_KEY = "proxy:egress"

#: 支持的两种模式。`auto` 自动轮换、`fixed` 锁定节点（手动切换）。
MODES = ("auto", "fixed")

DEFAULT_SETTINGS: dict = {
    "enabled": 0,            # 0 = 直连（保持原行为），1 = 对话主链路走出口
    "mode": "auto",          # auto | fixed
    "endpoint_id": 0,        # fixed 模式锁定的节点 id（0 = 未指定）
    "pool": "",              # 只用哪个代理组（空 = 全部）
    "rotate_seconds": 300,   # auto 模式轮换周期（秒）
    "rotate_per_request": 0,  # 1 = 每条请求换节点（比按周期更激进）
}

#: 探测目标：默认打 ipify 拿出口 IP，可按部署用环境变量换掉。
_PROBE_ENV = "PROXY_PROBE_URL"
_DEFAULT_PROBE_TARGETS = (
    "https://api.ipify.org?format=json",
    "http://api.ipify.org",
    "https://ifconfig.me/ip",
)

#: 探测超时（秒）。代理不通时不要让面板转圈超过这个数。
PROBE_TIMEOUT = 8.0

_LOCK = threading.RLock()

#: 进程内的「当前出口」与计数（热路径只读这里，不碰库）。
_STATE: dict = {
    "endpoint_id": 0,
    "label": "",
    "url_masked": "",
    "pool": "",
    "since": 0.0,            # monotonic 时间戳（算「还有多久轮换」用）
    "since_iso": "",
    "rotations": 0,
    "requests": 0,
    "last_reason": "",
    "last_error": "",
    "last_error_at": "",
    "last_ok_at": "",
}

#: 最近一次探测结果（按节点 id），`GET /api/proxies` 会附在每一项上。
_PROBES: dict[int, dict] = {}


# --------------------------------------------------------------------------- #
# 配置读写
# --------------------------------------------------------------------------- #

def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


#: 布尔开关的宽松写法：面板/环境变量/JSON 里都可能出现这些值。
_TRUE_WORDS = {"1", "true", "yes", "on", "enabled", "是"}


def _as_flag(value, default: int = 0) -> int:
    """把 `1 / "1" / true / "yes" / "on"` 统一成 1，其余为 0。

    坏值不抛错：设置里填错一个布尔不该让对话链路 500。
    """
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return 1 if value else 0
    text = str(value or "").strip().lower()
    if not text:
        return default
    return 1 if text in _TRUE_WORDS else 0


def _normalize(data: dict | None) -> dict:
    """把任意来源的配置收敛成合法值。

    坏数据不抛错：面板上填错一个数字不该让对话链路直接 500，
    一律回落到默认值 —— 这一层是「配置的防火墙」。
    """
    src = data if isinstance(data, dict) else {}
    mode = str(src.get("mode") or "auto").strip().lower()
    out = {
        "enabled": _as_flag(src.get("enabled")),
        "mode": mode if mode in MODES else "auto",
        "endpoint_id": max(0, _as_int(src.get("endpoint_id"))),
        "pool": str(src.get("pool") or "").strip()[:32],
        "rotate_seconds": max(5, min(86400, _as_int(src.get("rotate_seconds"), 300))),
        "rotate_per_request": _as_flag(src.get("rotate_per_request")),
    }
    return out


def load_settings(db: Session) -> dict:
    """读全局出口配置（一条主键查询 + JSON 解析），坏数据回落默认值。"""
    from admin.models import SystemSetting

    data: dict = {}
    try:
        row = db.query(SystemSetting).filter(SystemSetting.key == SETTINGS_KEY).first()
        if row and row.value:
            parsed = json.loads(row.value)
            if isinstance(parsed, dict):
                data = parsed
    except Exception:  # noqa: BLE001 - 配置读不出来不能挡对话
        _logger.warning("全局出口配置读取失败，按默认值处理", exc_info=True)
        data = {}
    return _normalize(data)


def save_settings(db: Session, patch: dict) -> dict:
    """合并式保存：只改传进来的键，其余保持原值。"""
    from admin.models import SystemSetting

    current = load_settings(db)
    merged = dict(current)
    for key in DEFAULT_SETTINGS:
        if isinstance(patch, dict) and patch.get(key) is not None:
            merged[key] = patch[key]
    merged = _normalize(merged)
    try:
        row = db.query(SystemSetting).filter(SystemSetting.key == SETTINGS_KEY).first()
        payload = json.dumps(merged, ensure_ascii=False)
        if row is None:
            db.add(SystemSetting(key=SETTINGS_KEY, value=payload))
        else:
            row.value = payload
        db.commit()
    except Exception:
        db.rollback()
        raise
    return merged


# --------------------------------------------------------------------------- #
# 节点选择
# --------------------------------------------------------------------------- #

def _model():
    from admin.models import ProxyEndpoint

    return ProxyEndpoint


def _rows(db: Session, pool: str) -> list:
    """可用节点（启用 + 不在静默期），按 id 升序。"""
    from admin import proxy_pool

    return proxy_pool.available(db, pool)


def _next_after(rows: list, current_id: int):
    """按 id 环状取下一个节点：轮换而不是随机，方便运营对着面板复现。"""
    if not rows:
        return None
    ids = [int(row.id) for row in rows]
    if current_id and current_id in ids:
        index = (ids.index(current_id) + 1) % len(rows)
        return rows[index]
    return rows[0]


def _waf_active() -> bool:
    """上游是否已经把当前出口 IP 判成 WAF 拦截（`admin/pool.py` 的滑动窗）。"""
    try:
        from admin.pool import POOL

        return bool(POOL.waf.active())
    except Exception:  # noqa: BLE001 - 拿不到闸门状态就当没拦
        return False


def snapshot() -> dict:
    """当前出口快照（只读副本，调用方改不到内部状态）。"""
    with _LOCK:
        return dict(_STATE)


def probe_cache() -> dict[int, dict]:
    """最近的节点探测结果（列表页/面板展示用）。"""
    with _LOCK:
        return {int(k): dict(v) for k, v in _PROBES.items()}


def _set_current(row, reason: str, force_reset: bool = False) -> None:
    """把某个节点设为当前出口，并把展示字段一起算好。

    `force_reset` 只重置「轮换计时」：单节点时的「轮换」等于没换，
    不重置的话每个请求都会重新走一遍轮换判断（白白查一次库）。
    """
    from admin import secret_box

    plain = ""
    try:
        plain = secret_box.resolve(getattr(row, "url", "") or "")
    except Exception:  # noqa: BLE001 - 解密失败不影响 id/名称展示
        plain = ""
    masked = secret_box.mask(plain) if plain else ""
    with _LOCK:
        same = int(_STATE["endpoint_id"]) == int(row.id)
        _STATE["endpoint_id"] = int(row.id)
        _STATE["label"] = str(getattr(row, "label", "") or "")
        _STATE["url_masked"] = masked
        _STATE["pool"] = str(getattr(row, "pool", "") or "")
        _STATE["last_reason"] = reason
        if not same:
            _STATE["since"] = time.monotonic()
            _STATE["since_iso"] = _utc_iso()
            _STATE["rotations"] += 1
        elif force_reset:
            _STATE["since"] = time.monotonic()
            _STATE["since_iso"] = _utc_iso()


def _set_empty(reason: str) -> None:
    with _LOCK:
        _STATE["endpoint_id"] = 0
        _STATE["label"] = ""
        _STATE["url_masked"] = ""
        _STATE["pool"] = ""
        _STATE["last_reason"] = reason


def _utc_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _account_bound_proxy(account) -> str:
    """账号自己绑定的出口（`auth_json.proxy`）—— 优先级最高。"""
    if account is None:
        return ""
    try:
        data = json.loads(getattr(account, "auth_json", "") or "{}")
        if isinstance(data, dict):
            return str(data.get("proxy") or "").strip()
    except (TypeError, ValueError):
        return ""
    return ""


def resolve(db: Session | None, account=None) -> tuple[str, int]:
    """给一次对话请求挑出口：返回 `(代理地址, 节点 id)`。

    调用方是 `admin/routers/proxy.py::_account_session_safe`，也就是
    **所有对话主链路建会话的唯一入口**。空地址 = 直连。

    优先级：
      1. 账号绑定（`auth_json.proxy`）—— 既有行为，不动；
      2. 全局出口关闭 / 没有可用节点 —— 直连（并记下原因）；
      3. `fixed` —— 锁定节点；节点不可用就顺延到下一个可用节点；
      4. `auto` —— 周期到 / 每条请求 / WAF 拦了当前 IP / 节点掉了，都换。
    """
    bound = _account_bound_proxy(account)
    if bound:
        try:
            from admin import proxy_pool

            url = proxy_pool.expand(bound)
        except Exception:  # noqa: BLE001
            url = ""
        if url:
            return url, 0

    if db is None:
        return "", 0

    try:
        settings = load_settings(db)
    except Exception:  # noqa: BLE001
        return "", 0
    if not settings["enabled"]:
        return "", 0

    try:
        rows = _rows(db, settings["pool"])
    except Exception:  # noqa: BLE001
        _set_empty("db-error")
        return "", 0
    if not rows:
        _set_empty("no-nodes")
        return "", 0

    from admin import proxy_pool

    current_id = snapshot()["endpoint_id"]
    now = time.monotonic()

    if settings["mode"] == "fixed" and settings["endpoint_id"]:
        chosen = next((row for row in rows if int(row.id) == settings["endpoint_id"]), None)
        reason = "fixed"
        if chosen is None:
            # 锁定的节点被停用 / 静默 / 删除：顺延到下一个可用节点。
            # 这里**不能**退回直连 —— 运营以为在走代理、实际裸奔本机 IP，
            # 是这个模块最危险的失败方式。
            chosen = _next_after(rows, current_id)
            reason = "fixed-fallback"
    else:
        same = next((row for row in rows if int(row.id) == current_id), None)
        age = now - float(snapshot()["since"] or 0.0)
        rotate = (
            not current_id
            or same is None
            or bool(settings["rotate_per_request"])
            or age >= float(settings["rotate_seconds"])
            or _waf_active()
        )
        if rotate and same is not None and len(rows) == 1 and not _waf_active():
            # 只有一个节点时「轮换」等于没换，但仍要刷新计时，
            # 否则每个请求都会重新走一遍轮换判断。
            chosen, reason = same, "single-node"
        elif rotate:
            chosen = _next_after(rows, current_id if same is not None else 0)
            reason = "rotate"
        else:
            chosen, reason = same, "keep"

    if chosen is None:
        _set_empty("no-nodes")
        return "", 0

    try:
        url = proxy_pool.expand(chosen.url)
    except Exception:  # noqa: BLE001
        url = ""
    if not url:
        _set_empty("bad-url")
        return "", 0

    previous_id = current_id
    _set_current(chosen, reason, force_reset=reason in ("rotate", "single-node", "fixed-fallback"))
    if int(chosen.id) != int(previous_id):
        _logger.info("全局出口轮换 → #%s（%s）", chosen.id, reason)
    with _LOCK:
        _STATE["requests"] += 1
    return url, int(chosen.id)


def ensure_current(db: Session) -> dict | None:
    """开启全局出口时**立刻**选一个当前节点，别等第一条对话请求。

    为什么要有这一步：面板上「当前出口」空着，运营会以为配置没生效；
    而实际上只是还没人发请求。开启那一刻就选定，状态和流量才对得上。
    """
    settings = load_settings(db)
    if not settings["enabled"]:
        return None
    if snapshot()["endpoint_id"]:
        return _current_view()
    rows = _rows(db, settings["pool"])
    if not rows:
        _set_empty("no-nodes")
        return None
    chosen = None
    if settings["mode"] == "fixed" and settings["endpoint_id"]:
        chosen = next((row for row in rows if int(row.id) == settings["endpoint_id"]), None)
    chosen = chosen or rows[0]
    _set_current(chosen, "select")
    return _current_view()


def rotate(db: Session, endpoint_id: int = 0, reason: str = "manual") -> dict:
    """手动切换：`endpoint_id=0` 表示「切到下一个」，非 0 表示锁定该节点。

    返回 `{"ok": bool, "error": str, "current": {...}}`，路由层只负责翻译成 HTTP。
    """
    settings = load_settings(db)
    rows = _rows(db, settings["pool"])
    if not rows:
        return {"ok": False, "error": "没有可用节点（全部停用或处于静默期）", "current": None}

    current_id = snapshot()["endpoint_id"]
    if endpoint_id:
        chosen = next((row for row in rows if int(row.id) == int(endpoint_id)), None)
        if chosen is None:
            return {"ok": False, "error": "节点不存在、已停用或处于静默期", "current": None}
    else:
        chosen = _next_after(rows, current_id)
    if chosen is None:
        return {"ok": False, "error": "没有可用节点", "current": None}

    _set_current(chosen, reason, force_reset=True)
    _logger.info("全局出口切换 → #%s（%s）", chosen.id, reason)
    return {"ok": True, "error": "", "current": _current_view()}


def note_ok(endpoint_id: int) -> None:
    """记一次出口成功（只更新内存，不写库 —— 热路径）。"""
    if not endpoint_id:
        return
    with _LOCK:
        if int(endpoint_id) == int(_STATE["endpoint_id"]):
            _STATE["last_ok_at"] = _utc_iso()
            _STATE["last_error"] = ""


def note_error(endpoint_id: int, status: int = 0, text: str = "") -> None:
    """记一次出口失败，并在「像 IP 被拉黑」时把节点打进静默期。"""
    if not endpoint_id:
        return
    from admin import proxy_pool, secret_box

    try:
        message = secret_box.scrub(str(text or ""))[:200]
    except Exception:  # noqa: BLE001
        message = str(text or "")[:200]
    with _LOCK:
        if int(endpoint_id) == int(_STATE["endpoint_id"]):
            _STATE["last_error"] = message
            _STATE["last_error_at"] = _utc_iso()

    if not proxy_pool.looks_ip_blocked(status, text):
        return
    try:
        from admin.db import SessionLocal

        with SessionLocal() as session:
            proxy_pool.mark_blocked(session, endpoint_id, message or "ip blocked")
    except Exception:  # noqa: BLE001 - 记账失败不影响请求
        _logger.warning("标记出口节点静默失败 #%s", endpoint_id, exc_info=True)


def _current_view() -> dict | None:
    state = snapshot()
    if not state["endpoint_id"]:
        return None
    return {
        "endpoint_id": state["endpoint_id"],
        "label": state["label"] or ("#%s" % state["endpoint_id"]),
        "url": state["url_masked"],
        "pool": state["pool"],
        "since": state["since_iso"],
        "reason": state["last_reason"],
    }


def active_endpoint_id(db: Session) -> int:
    """当前**生效**的出口节点 id；全局出口关着时恒为 0。

    为什么单独抽一个：内存里会保留「上次用过的节点」方便重新打开时沿用，
    但面板/列表不该在关闭状态下把某个节点标成「当前」—— 那是误导。
    """
    try:
        if not load_settings(db)["enabled"]:
            return 0
    except Exception:  # noqa: BLE001
        return 0
    return int(snapshot()["endpoint_id"] or 0)


# --------------------------------------------------------------------------- #
# 状态汇总
# --------------------------------------------------------------------------- #

def status(db: Session) -> dict:
    """面板 / 健康检查用的一份完整状态：配置 + 当前节点 + 计数 + 下一次轮换。"""
    settings = load_settings(db)
    try:
        rows = _rows(db, settings["pool"])
    except Exception:  # noqa: BLE001
        rows = []
    state = snapshot()
    now = time.monotonic()

    next_rotate_in = None
    if settings["enabled"] and settings["mode"] == "auto":
        if settings["rotate_per_request"]:
            next_rotate_in = 0
        elif state["since"]:
            next_rotate_in = max(0, int(round(
                float(settings["rotate_seconds"]) - (now - float(state["since"])))))

    warnings = []
    if settings["enabled"] and not rows:
        warnings.append("全局出口已开启，但没有任何可用节点，对话会退回直连")
    if settings["mode"] == "fixed" and settings["endpoint_id"] and \
            not any(int(row.id) == settings["endpoint_id"] for row in rows):
        warnings.append("锁定的节点当前不可用，出口会顺延到下一个可用节点")
    if state["last_reason"] == "fixed-fallback":
        warnings.append("锁定的节点掉了，已自动顺延 —— 确认后请重新选节点")
    if settings["enabled"] and rows and not state["endpoint_id"]:
        warnings.append("全局出口已开启，下一条对话请求会自动选一个节点；"
                        "想立刻选定就点「立即轮换」")

    return {
        "settings": settings,
        "current": _current_view() if settings["enabled"] else None,
        "available": len(rows),
        "slots": _total_slots(db, settings["pool"]),
        "next_rotate_in": next_rotate_in,
        "runtime": {
            "requests": state["requests"],
            "rotations": state["rotations"],
            "last_ok_at": state["last_ok_at"],
            "last_error": state["last_error"],
            "last_error_at": state["last_error_at"],
            "last_reason": state["last_reason"],
            "waf_blocked": _waf_active(),
        },
        "warnings": warnings,
        "probe_target": _probe_targets()[0],
    }


def _total_slots(db: Session, pool: str) -> int:
    try:
        from admin import proxy_pool

        return proxy_pool.total_slots(db, pool)
    except Exception:  # noqa: BLE001
        return 0


# --------------------------------------------------------------------------- #
# 连通性体检
# --------------------------------------------------------------------------- #

_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_IPV6 = re.compile(r"\b(?:[0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F]{1,4}\b")


def _probe_targets() -> list[str]:
    custom = str(os.getenv(_PROBE_ENV, "") or "").strip()
    if custom:
        return [custom]
    return list(_DEFAULT_PROBE_TARGETS)


def _extract_ip(text: str) -> str:
    """从探测响应里抠出口 IP：兼容 `{"ip":...}` / `{"query":...}` / 纯文本。"""
    body = (text or "").strip()
    if not body:
        return ""
    if body.startswith("{"):
        try:
            data = json.loads(body)
            if isinstance(data, dict):
                for key in ("ip", "query", "origin", "Address"):
                    value = data.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
        except ValueError:
            pass
    match = _IPV4.search(body) or _IPV6.search(body)
    return match.group(0) if match else ""


def _mask_plain(url: str) -> str:
    from admin import secret_box

    try:
        return secret_box.mask(url)
    except Exception:  # noqa: BLE001
        return ""


def probe(url: str, timeout: float = PROBE_TIMEOUT) -> dict:
    """真的拨一次：经这个出口访问探测目标，返回延迟 / 状态码 / 出口 IP。

    **这是「测试连接」的唯一实现** —— 面板上的绿灯必须来自真实请求，
    不能是「配置存在 = 健康」。代理挂了而上游 407/超时，这里必须是红的。
    """
    import httpx

    raw = str(url or "").strip()
    result = {
        "ok": False, "latency_ms": None, "status": 0, "egress_ip": "",
        "via": "", "target": "", "error": "", "at": _utc_iso(),
    }
    if not raw:
        result["error"] = "代理地址为空"
        return result

    # 库里存的是 `enc:v1:...` 密文（或 `env:NAME` 引用），所以**先解析再校验协议**。
    # 顺序反了会把每个已落库节点都判成「协议不合法」—— 面板上全红、明明是通的。
    try:
        from admin import proxy_pool

        resolved = proxy_pool.expand(raw)
    except Exception as exc:  # noqa: BLE001
        result["error"] = "代理地址无法解析（解密失败或密钥已轮换）：%s" % exc
        return result
    if not resolved:
        result["error"] = "代理地址无法解析（密文为空或密钥不匹配）"
        return result
    if not re.match(r"^(https?|socks5h?)://", resolved):
        result["error"] = "代理地址必须是以 http:// / https:// / socks5:// 开头的地址"
        return result
    result["via"] = _mask_plain(resolved)

    last_error = ""
    started = time.perf_counter()
    for target in _probe_targets():
        attempt = time.perf_counter()
        try:
            with httpx.Client(proxy=resolved, timeout=timeout, trust_env=False,
                              verify=False, follow_redirects=True) as client:
                response = client.get(target)
            elapsed = round((time.perf_counter() - attempt) * 1000, 1)
            result["target"] = target
            result["status"] = int(response.status_code)
            result["latency_ms"] = elapsed
            if response.status_code >= 400:
                last_error = "探测目标返回 HTTP %s" % response.status_code
                continue
            body = response.text or ""
            result["egress_ip"] = _extract_ip(body)
            result["ok"] = True
            result["error"] = ""
            result["total_ms"] = round((time.perf_counter() - started) * 1000, 1)
            return result
        except Exception as exc:  # noqa: BLE001 - 任何异常都是「这个出口不通」
            last_error = _scrub_error(exc)
            result["target"] = target
            continue

    result["error"] = last_error or "探测失败"
    result["total_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return result


def _scrub_error(exc: Exception) -> str:
    """异常文案脱敏：httpx 的报错里会带完整代理 URL（含账密）。"""
    from admin import secret_box

    text = "%s: %s" % (type(exc).__name__, exc)
    try:
        text = secret_box.scrub(text)
    except Exception:  # noqa: BLE001
        pass
    return text[:240]


def test_endpoint(db: Session, endpoint_id: int) -> dict:
    """测试一个已落库的节点，并把结果写回 `last_ok_at` / `last_error`。"""
    ProxyEndpoint = _model()
    row = db.get(ProxyEndpoint, int(endpoint_id))
    if row is None:
        return {"ok": False, "error": "节点不存在", "probe": None, "endpoint_id": endpoint_id}

    result = probe(row.url)
    now = datetime.utcnow()
    try:
        if result["ok"]:
            row.last_ok_at = now
            row.last_error = ""
        else:
            from admin import secret_box

            row.last_error = secret_box.scrub(result.get("error") or "probe failed")[:255]
        db.commit()
    except Exception:  # noqa: BLE001 - 写不回状态不影响本次探测结论
        db.rollback()

    with _LOCK:
        _PROBES[int(endpoint_id)] = dict(result)
        if result["ok"] and int(endpoint_id) == int(_STATE["endpoint_id"]):
            _STATE["last_ok_at"] = result["at"]
            _STATE["last_error"] = ""
        elif not result["ok"] and int(endpoint_id) == int(_STATE["endpoint_id"]):
            _STATE["last_error"] = str(result.get("error") or "")[:200]
            _STATE["last_error_at"] = result["at"]
    return {"ok": bool(result["ok"]), "error": result.get("error") or "",
            "probe": result, "endpoint_id": int(endpoint_id)}


def test_url(url: str) -> dict:
    """测试一个还没落库的地址（新增节点前先试一下通不通）。"""
    result = probe(url)
    return {"ok": bool(result["ok"]), "error": result.get("error") or "", "probe": result}
