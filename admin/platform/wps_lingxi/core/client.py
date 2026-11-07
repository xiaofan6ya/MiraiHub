"""WPS 灵犀 HTTP 客户端：三步通路 + SSE 帧。

## 通路（上游桌面客户端同款）

    ① POST /api/aioffice/v1/cowork/sessions           → session_id
    ② POST /api/public/v1/credits/runs                → run_id / group_id
    ③ POST /api/public/v1/agent-runtime/chat/completions（SSE）

计费按 run：一个 run 内多次调用只扣一次智点，所以 ①② 的结果按
「账号 Cookie × 模型」缓存 1 小时（**不滑动续期**，到点就重建）。

## 两个 base 的区别（最容易写成同一个）

账号面（profile / balance / tasks / plans）用 `resolveBase`，对话面用
硬编码 `[wps.cn, kdocs.cn]` 顺序回落，成功后把实际域缓存进 run。
写成"两处都用同一个"会在 kdocs 账号上表现成「余额能取、对话 404」。

## 错误分档

    401/403 + 鉴权字眼 → auth     （登录态失效）
    AuditFailed        → audit    （审核拦截，不换域、不重建会话）
    400 + 余额字眼      → quota    （这号没智点了，换号）
    会话不存在          → stale    （重建一次会话再试）
    其它 4xx           → fatal    （请求本身有问题，换号无意义）
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time

import httpx

from admin.platform.wps_lingxi.core import endpoints, protocol

logger = logging.getLogger("admin.platform.wps_lingxi.client")

#: 鉴权失败的字眼（只有命中才判 auth，否则上游 401 会被误判成"登录失效"）。
_AUTH_MARKERS = ("unauthor", "登录", "凭据", "credential", "cookie", "sid",
                 "登录态")

#: 余额不足的字眼：这是**账号级**问题，必须换号而不是中断。
_QUOTA_MARKERS = ("insufficient", "余额不足", "积分不足", "智点不足",
                  "quota", "balance", "no_credit", "points")

#: 会话失效的字眼（重建一次再试）。
_STALE_MARKERS = ("sqlnorows", "no rows", "session not found")
_STALE_CN = ("会话不存在", "数据不存在")


class UpstreamError(Exception):
    def __init__(self, message: str, *, status: int = 0, kind: str = "upstream",
                 code: str = "", detail: str = ""):
        super().__init__(message)
        self.message = message
        self.status = status
        self.kind = kind
        self.code = code
        self.detail = detail

    def to_dict(self) -> dict:
        return {"kind": self.kind, "status": self.status,
                "code": self.code, "message": self.message}


def classify(status: int, body: str = "") -> str:
    low = (body or "").lower()
    if protocol.AUDIT_PATTERN.search(body or ""):
        return "audit"
    if status in (401, 403):
        if any(marker in low for marker in _AUTH_MARKERS):
            return "auth"
        return "auth" if status == 401 else "fatal"
    if status == 429:
        return "rate"
    if status >= 500:
        return "upstream"
    if status == 0:
        return "network"
    if any(marker.lower() in low for marker in _QUOTA_MARKERS):
        return "quota"
    return "fatal"


def looks_stale(message: str) -> bool:
    """上游报「会话/run 不存在」—— 重建一次会话再试就好。"""
    text = str(message or "")
    low = text.lower()
    if any(marker in low for marker in _STALE_MARKERS):
        return True
    return any(marker in text for marker in _STALE_CN)


def is_audit(message: str) -> bool:
    return bool(protocol.AUDIT_PATTERN.search(str(message or "")))


def _reason(status: int, body: str) -> str:
    try:
        data = json.loads(body)
    except Exception:  # noqa: BLE001
        return (body or "").strip()[:300] or ("HTTP %d" % status)
    if isinstance(data, dict):
        text = protocol.text_of_payload(data)
        if text:
            return text
    return (body or "").strip()[:300] or ("HTTP %d" % status)


def _client(timeout: float) -> httpx.Client:
    """四项 timeout 全给。

    `httpx.Timeout(600, connect=30)` 只设了 connect，其余留 None（不限），
    实测在 OpenWrt 上表现为 TLS 握手 `UNEXPECTED_EOF_WHILE_READING` ——
    报错看起来像上游掐连接，实际是客户端超时配置畸形。
    """
    return httpx.Client(
        timeout=httpx.Timeout(connect=20.0, read=timeout,
                              write=timeout, pool=20.0),
        follow_redirects=True,
    )


#: TLS 握手的瞬时失败（上游 nginx 偶发）。只对这些重试，4xx/5xx 不重试。
_TRANSIENT = ("UNEXPECTED_EOF_WHILE_READING", "EOF occurred in violation of protocol",
              "Connection reset by peer", "Server disconnected", "ConnectError",
              "ReadTimeout", "ConnectTimeout", "RemoteProtocolError",
              "TLS", "SSL")
_RETRY_TIMES = 4
_BACKOFF = (0.4, 1.0, 1.8, 2.6)


def _transient(exc: BaseException) -> bool:
    text = "%s: %s" % (type(exc).__name__, exc)
    return any(marker in text for marker in _TRANSIENT)


def _device_env() -> dict:
    try:
        name = os.getenv("USERNAME") or os.getenv("USER") or "magpie"
    except Exception:  # noqa: BLE001
        name = "magpie"
    try:
        work_dir = tempfile.gettempdir()
    except Exception:  # noqa: BLE001
        work_dir = "."
    return {"device_id": endpoints.DEVICE_ID,
            "device_name": str(name),
            "work_space_dir": work_dir}


# ---------------------------------------------------------------------------
# 基础请求
# ---------------------------------------------------------------------------
def request_json(url: str, *, headers: dict, method: str = "GET",
                 body: dict | None = None, timeout: float = endpoints.API_TIMEOUT,
                 what: str = "") -> dict:
    def _do():
        with _client(timeout) as client:
            return client.request(method, url, headers=headers, json=body)

    last: BaseException | None = None
    for attempt in range(1, _RETRY_TIMES + 1):
        try:
            resp = _do()
            break
        except Exception as exc:  # noqa: BLE001
            if not _transient(exc):
                raise UpstreamError("网络错误: %s" % str(exc)[:200], status=0,
                                    kind="network") from exc
            last = exc
            if attempt >= _RETRY_TIMES:
                raise UpstreamError("网络错误: %s" % str(exc)[:200], status=0,
                                    kind="network") from exc
            time.sleep(_BACKOFF[min(attempt - 1, len(_BACKOFF) - 1)])
    else:  # pragma: no cover - 循环必然 break 或 raise
        raise UpstreamError("网络错误: %s" % str(last)[:200], status=0,
                            kind="network")

    text = resp.text or ""
    if resp.status_code >= 400:
        kind = classify(resp.status_code, text)
        message = _reason(resp.status_code, text)
        raise UpstreamError(message, status=resp.status_code, kind=kind,
                            detail=text[:800])
    try:
        data = json.loads(text)
    except Exception as exc:  # noqa: BLE001
        raise UpstreamError("响应不是 JSON: %s" % text[:200],
                            status=resp.status_code, kind="fatal") from exc
    if not isinstance(data, dict):
        raise UpstreamError("响应不是对象", status=resp.status_code, kind="fatal")

    # HTTP 200 也可能是未登录 / 审核拦截，必须看 body
    if is_audit(json.dumps(data, ensure_ascii=False)[:4000]):
        raise UpstreamError(protocol.text_of_payload(data) or "需求被审核拦截",
                            status=400, kind="audit", code="audit_blocked",
                            detail=json.dumps(data, ensure_ascii=False)[:800])
    error = protocol.frame_error(data)
    if error is not None:
        code, message = error
        kind = "auth" if code == "UserUnauthorized" else "upstream"
        raise UpstreamError(message, status=401 if kind == "auth" else 502,
                            kind=kind, code=code,
                            detail=json.dumps(data, ensure_ascii=False)[:800])
    return data


# ---------------------------------------------------------------------------
# 账号面
# ---------------------------------------------------------------------------
def fetch_profile(cookie: str) -> dict:
    """校验 Cookie 归属哪个域，返回 `{base, uid, name}`。

    两个域都打一次（并发意义不大，顺序即可），成功判据是
    `result != "UserUnauthorized"` —— **HTTP 200 + UserUnauthorized 算失败**。
    """
    errors: list[str] = []
    for base in endpoints.account_bases():
        try:
            data = request_json(base + endpoints.PROFILE_PATH,
                                headers=endpoints.account_headers(cookie, base),
                                what="profile")
        except UpstreamError as exc:
            errors.append("%s: %s" % (base, exc.message))
            continue
        result = str(data.get("result") or "")
        if result and result.lower() == "userunauthorized":
            errors.append("%s: 登录态无效" % base)
            continue
        payload = data.get("data") if isinstance(data.get("data"), dict) else {}
        return {"ok": True, "base": base,
                "uid": str(payload.get("id") or ""),
                "name": str(payload.get("name") or "").strip()}
    raise UpstreamError("Cookie 无效或已过期（%s）" % ("；".join(errors[:2])
                                                     or "上游未返回资料"),
                        status=401, kind="auth", code="sign_in_required")


def fetch_plans(cookie: str, base: str = "") -> list[dict]:
    """拉模型目录（plans + models）。失败返回空列表，由调用方回落快照。"""
    target = base or endpoints.base_of()
    try:
        data = request_json(target + endpoints.PLANS_PATH,
                            headers=endpoints.account_headers(cookie, target),
                            what="plans")
    except UpstreamError as exc:
        logger.debug("灵犀模型目录拉取失败：%s", exc.message)
        return []
    payload = data.get("data") if isinstance(data.get("data"), dict) else {}
    out: list[dict] = []
    for item in (payload.get("plans") or []):
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if not key:
            continue
        efforts = item.get("reasoning_efforts")
        out.append({
            "id": key,
            "name": str(item.get("name") or key),
            "description": str(item.get("desc") or ""),
            "context_window": item.get("max_context_tokens") or 1048576,
            "max_tokens": item.get("max_new_tokens") or 65536,
            "multiplier": "模式",
            "reasoning": True,
            "efforts": [str(x) for x in efforts] if isinstance(efforts, list) else [],
            "vision": bool(item.get("support_multimodal")),
        })
    for item in (payload.get("models") or []):
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if not key:
            continue
        efforts = item.get("reasoning_efforts")
        out.append({
            "id": key,
            "name": str(item.get("name") or key),
            "description": str(item.get("desc") or item.get("intro") or ""),
            "context_window": item.get("max_context_tokens") or 1048576,
            "max_tokens": item.get("max_new_tokens") or 65536,
            "multiplier": str(item.get("multiplier") or ""),
            "reasoning": bool(efforts),
            "efforts": [str(x) for x in efforts] if isinstance(efforts, list) else [],
            "vision": bool(item.get("support_multimodal")),
        })
    return out


def catalog_of(models: list[dict]) -> list[dict]:
    """把目录条目归一：没条目就用内置快照。"""
    if not models:
        return endpoints.default_catalog()
    out = []
    for item in models:
        out.append({
            "id": item["id"],
            "name": item.get("name") or item["id"],
            "description": item.get("description") or "",
            "context_window": item.get("context_window"),
            "max_tokens": item.get("max_tokens"),
            "multiplier": item.get("multiplier") or "",
            "reasoning": bool(item.get("reasoning")),
            "vision": bool(item.get("vision")),
            "efforts": item.get("efforts") or [],
            "default": item["id"] == endpoints.DEFAULT_MODEL,
        })
    return out


def fetch_balance(cookie: str, base: str = "") -> dict:
    """智点余额。

    `total_balance` 读不到回落 `balance`；`bonus_summary` 用来算已用比例；
    临时积分（15 天内到期）由 `bonus_credits[]` 逐批判定。
    """
    target = base or endpoints.base_of()
    data = request_json(target + endpoints.BALANCE_PATH,
                        headers=endpoints.account_headers(cookie, target),
                        what="balance")
    payload = data.get("data") if isinstance(data.get("data"), dict) else {}
    total = payload.get("total_balance")
    if total is None:
        total = payload.get("balance")
    try:
        total = int(float(total))
    except (TypeError, ValueError):
        total = 0
    if total < 0:
        total = 0

    summary = payload.get("bonus_summary")
    summary = summary if isinstance(summary, dict) else {}
    try:
        bonus_total = int(float(summary.get("total_value") or 0))
    except (TypeError, ValueError):
        bonus_total = 0
    consumed = summary.get("total_consumed")
    try:
        bonus_used = None if consumed is None else int(float(consumed))
    except (TypeError, ValueError):
        bonus_used = None

    items = payload.get("bonus_credits")
    if not isinstance(items, list) or not items:
        inner = summary.get("items")
        items = inner if isinstance(inner, list) else []

    expiring = _summarize_expiring(items, _temp_days())
    used_pct = 0
    if bonus_total > 0 and bonus_used is not None:
        used_pct = int(max(0, min(100, round(bonus_used / bonus_total * 100))))
    return {
        "total": total,
        "remain": total,
        "bonus_total": bonus_total,
        "bonus_used": bonus_used,
        "used_pct": used_pct,
        "expiring_balance": expiring["balance"],
        "expiring_value": expiring["value"],
        "expiring_earliest": expiring["earliest"],
        "batches": len(items),
    }


def _temp_days() -> int:
    try:
        value = int(os.getenv("ADMIN_WPS_LINGXI_TEMP_DAYS") or 15)
    except (TypeError, ValueError):
        return 15
    return value if value > 0 else 15


def _parse_expire(value: str) -> float:
    """`YYYY-MM-DD HH:mm:ss`（北京时区）→ epoch 秒。解析不了返回 0。"""
    text = str(value or "").strip()
    if not text:
        return 0
    if text.endswith("Z"):
        text = text[:-1]
    candidate = text.replace("T", " ")
    if candidate.endswith("+08:00"):
        candidate = candidate[:-6]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            import datetime as _dt

            parsed = _dt.datetime.strptime(candidate, fmt)
            # 上游时间戳是北京时区（UTC+8）
            return parsed.replace(tzinfo=_dt.timezone(_dt.timedelta(hours=8))) \
                .timestamp()
        except ValueError:
            continue
    return 0


def _summarize_expiring(items: list, days: int) -> dict:
    """15 天内到期的那部分智点（三条判据全满足才算）。"""
    now = time.time()
    window = days * 86400 if days > 0 else 0
    balance = value = 0
    earliest = ""
    for item in items:
        if not isinstance(item, dict):
            continue
        expire = _parse_expire(item.get("expire_time"))
        if not expire or expire <= now:
            continue
        if window > 0 and (expire - now) > window:
            continue
        try:
            batch_balance = int(float(item.get("balance") or 0))
            batch_value = int(float(item.get("value") or 0))
        except (TypeError, ValueError):
            continue
        balance += max(0, batch_balance)
        value += max(0, batch_value)
        stamp = str(item.get("expire_time") or "")
        if not earliest or _parse_expire(stamp) < _parse_expire(earliest):
            earliest = stamp
    return {"balance": balance, "value": value, "earliest": earliest}


def fetch_tasks(cookie: str, base: str = "") -> list[dict]:
    target = base or endpoints.base_of()
    data = request_json(target + endpoints.TASKS_PATH,
                        headers=endpoints.account_headers(cookie, target),
                        what="tasks")
    payload = data.get("data") if isinstance(data.get("data"), dict) else {}
    tasks = payload.get("tasks")
    return [item for item in tasks if isinstance(item, dict)] \
        if isinstance(tasks, list) else []


def claim_task(cookie: str, key: str, date: str = "", base: str = "") -> dict:
    target = base or endpoints.base_of()
    url = target + (endpoints.CLAIM_PATH % key)
    if date:
        url += "?date=%s" % date
    headers = endpoints.account_headers(cookie, target)
    headers["Content-Type"] = "application/json"
    return request_json(url, headers=headers, method="POST", body={},
                        what="claim")


# ---------------------------------------------------------------------------
# 用量明细 / 使用日志（上游）
# ---------------------------------------------------------------------------
def _day_start(days: int) -> str:
    """`since` 参数：N 天前的 0 点，格式严格 `YYYY-MM-DD`。

    上游对格式**挑剔**（`invalid since date %q, expected YYYY-MM-DD`），
    所以这里只产出日期，不带时间、不带时区。
    """
    from datetime import datetime, timedelta

    days = max(1, min(365, int(days or 30)))
    return (datetime.now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")


def _today_str() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d")


def _query(params: dict) -> str:
    from urllib.parse import urlencode

    items = [(key, value) for key, value in params.items()
             if value is not None and value != ""]
    return ("?" + urlencode(items)) if items else ""


def fetch_transactions(cookie: str, base: str = "", *, days: int = 30,
                       page_size: int = 20, income_cursor: str = "",
                       expense_cursor: str = "") -> dict:
    """智点收支流水（`credits/transaction_summary`）—— 用量明细的上游来源。

    返回 `{items, income_cursor, expense_cursor, raw}`。字段名上游没有稳定文档，
    归一化交给 `services/usage.py` 做，这里只保证**不编造**：
    不认识的结构原样放进 `raw`，由上层决定要不要展示。
    """
    target = base or endpoints.base_of()
    url = target + endpoints.TRANSACTIONS_PATH + _query({
        "since": _day_start(days),
        "before": _today_str(),
        "page_size": max(1, min(100, int(page_size or 20))),
        "income_cursor": income_cursor,
        "expense_cursor": expense_cursor,
    })
    data = request_json(url, headers=endpoints.account_headers(cookie, target),
                        what="transactions")
    payload = data.get("data") if isinstance(data.get("data"), dict) else {}
    items = (payload.get("items") or payload.get("transactions")
             or payload.get("records") or payload.get("list") or [])
    return {
        "items": [item for item in items if isinstance(item, dict)]
        if isinstance(items, list) else [],
        "income_cursor": str(payload.get("income_cursor") or ""),
        "expense_cursor": str(payload.get("expense_cursor") or ""),
        "raw": payload,
    }


def fetch_run_records(cookie: str, base: str = "", *, days: int = 30,
                      page_size: int = 20, offset: int = 0) -> list[dict]:
    """该账号的 agent run 记录（`agents/run_records`）—— 使用日志的上游来源。

    上游没有公开文档，参数按 Go 侧 `AgentRunRecordInput` 推断：
    `since` / `before` / `page_size` / `offset`。**没有记录时返回空列表**，
    不抛异常 —— 「上游没有记录」和「请求失败」必须能区分开。
    """
    target = base or endpoints.base_of()
    url = target + endpoints.RUN_RECORDS_PATH + _query({
        "since": _day_start(days),
        "before": _today_str(),
        "page_size": max(1, min(100, int(page_size or 20))),
        "offset": int(offset or 0),
    })
    data = request_json(url, headers=endpoints.account_headers(cookie, target),
                        what="run_records")
    payload = data.get("data") if isinstance(data.get("data"), dict) else {}
    for key in ("records", "run_records", "items", "list"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    # 有些实现直接把数组放在顶层 data
    if isinstance(data.get("data"), list):
        return [item for item in data["data"] if isinstance(item, dict)]
    return []


# ---------------------------------------------------------------------------
# 对话三步
# ---------------------------------------------------------------------------
def create_session(cookie: str, base: str, *, client_type: str = "",
                   session_mode: str = "") -> str:
    payload: dict = {"session_type": "cowork_client", "agent_version": 2,
                     "env": _device_env()}
    if client_type:
        payload["client_type"] = client_type
    if session_mode:
        payload["session_mode"] = session_mode
    data = request_json(base + endpoints.SESSIONS_PATH,
                        headers=endpoints.agent_headers(cookie, base),
                        method="POST", body=payload, what="session")
    inner = data.get("data") if isinstance(data.get("data"), dict) else {}
    session_id = str(inner.get("session_id") or "").strip()
    if not session_id:
        raise UpstreamError("灵犀未返回 session_id", status=502, kind="upstream",
                            detail=json.dumps(data, ensure_ascii=False)[:600])
    return session_id


def last_user_text(messages: list) -> str:
    """取最后一条 user 文本的前 60 字（上游用它做 run 的标题）。"""
    for item in reversed(messages or []):
        if not isinstance(item, dict) or str(item.get("role")) != "user":
            continue
        content = item.get("content")
        text = ""
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = "".join(str(block.get("text") or "") for block in content
                           if isinstance(block, dict))
        text = text.strip()
        if text:
            return text[:60]
    return "灵犀会话"


def create_run(cookie: str, base: str, session_id: str, model: str, *,
               messages: list | None = None, client_type: str = "",
               current_mode: str = "agent") -> dict:
    payload = {
        "client_request_id": str(int(time.time() * 1000)),
        "session_id": session_id,
        "model_provider": "lingxi",
        "model_id": model,
        "agent_input": {
            "text": last_user_text(messages or []),
            "files": [],
            "file_ids": [],
            "expect_model": model,
            "refs": [],
            "permission_mode": "default",
            "current_mode": current_mode or "agent",
            "browser_plugin_enabled": False,
        },
    }
    if client_type:
        payload["client_type"] = client_type
    data = request_json(base + endpoints.RUNS_PATH,
                        headers=endpoints.agent_headers(cookie, base),
                        method="POST", body=payload, what="run")
    inner = data.get("data") if isinstance(data.get("data"), dict) else {}
    run_id = str(inner.get("run_id") or "").strip()
    if not run_id:
        raise UpstreamError("灵犀未返回 run_id", status=502, kind="upstream",
                            detail=json.dumps(data, ensure_ascii=False)[:600])
    group_id = str(inner.get("group_id") or "").strip() or run_id
    return {"run_id": run_id, "group_id": group_id, "session_id": session_id,
            "base": base, "at": time.time()}


#: 进程内 run 缓存：key = sha256(cookie)[:16]|model|client_type|session_mode
_RUNS: dict[str, dict] = {}
_RUNS_LOCK = threading.Lock()


def _run_key(cookie: str, model: str, client_type: str, session_mode: str) -> str:
    return "|".join((protocol.identity_of(cookie), model, client_type or "",
                     session_mode or ""))


def forget_run(cookie: str, model: str, client_type: str = "",
               session_mode: str = "") -> None:
    with _RUNS_LOCK:
        _RUNS.pop(_run_key(cookie, model, client_type, session_mode), None)


def ensure_run(cookie: str, model: str, *, messages: list | None = None,
               client_type: str = "", session_mode: str = "",
               current_mode: str = "agent") -> dict:
    """拿一个可用的 run：命中缓存且未过期就用，否则新建。

    `at` 只在创建时写，**不滑动续期** —— 一个 run 最多活 1 小时，
    之后必然重建（上游按 run 计费，复用太久反而会在上游侧过期）。
    """
    key = _run_key(cookie, model, client_type, session_mode)
    with _RUNS_LOCK:
        cached = _RUNS.get(key)
    if cached and (time.time() - float(cached.get("at") or 0)) < endpoints.RUN_TTL:
        return cached

    last_error: UpstreamError | None = None
    for base in endpoints.chat_bases():
        try:
            session_id = create_session(cookie, base, client_type=client_type,
                                        session_mode=session_mode)
            run = create_run(cookie, base, session_id, model, messages=messages,
                             client_type=client_type, current_mode=current_mode)
        except UpstreamError as exc:
            if exc.kind == "audit":
                raise
            last_error = exc
            continue
        with _RUNS_LOCK:
            _RUNS[key] = run
        return run
    if last_error is not None:
        raise last_error
    raise UpstreamError("灵犀没有可用的对话域", status=502, kind="upstream")


def chat_stream(cookie: str, run: dict, body: dict, *, model: str,
                timeout: float = endpoints.CHAT_TIMEOUT, prime: bool = True):
    """逐帧 yield 上游解析后的 dict。

    `prime=True` 时先建连并读第一帧，把上游 4xx/5xx 变成可捕获的异常
    （否则错误只会变成「200 + 空流」，客户端拿不到原因）。
    """
    base = run["base"]
    headers = endpoints.agent_headers(cookie, base, extra={
        "x-cc-session-id": run["session_id"],
        "x-cc-run-id": run["run_id"],
    })
    payload = dict(body)
    payload["stream"] = True

    def _frames():
        with _client(timeout) as client:
            with client.stream("POST", base + endpoints.CHAT_PATH,
                               headers=headers, json=payload) as resp:
                if resp.status_code >= 400:
                    raw = resp.read().decode("utf-8", "replace")
                    raise UpstreamError(_reason(resp.status_code, raw),
                                        status=resp.status_code,
                                        kind=classify(resp.status_code, raw),
                                        detail=raw[:800])
                yield from _iter_frames(resp)

    if not prime:
        yield from _frames()
        return

    gen = _frames()
    first = None
    for attempt in range(1, _RETRY_TIMES + 1):
        try:
            first = next(gen, None)
            break
        except UpstreamError:
            raise
        except Exception as exc:  # noqa: BLE001
            if not _transient(exc):
                raise UpstreamError("网络错误: %s" % str(exc)[:200], status=0,
                                    kind="network") from exc
            if attempt >= _RETRY_TIMES:
                raise UpstreamError("网络错误: %s" % str(exc)[:200], status=0,
                                    kind="network") from exc
            time.sleep(_BACKOFF[min(attempt - 1, len(_BACKOFF) - 1)])
            gen = _frames()
    if first is not None:
        yield first
    yield from gen


def _iter_frames(resp):
    """按行解析上游 SSE。

    上游每帧是**单行** `data:{...}`，所以逐行处理即可（不要用
    按 `\\n\\n` 分帧的写法：上游有时会连着发多行 `data:`，那样会吞帧）。
    """
    finish_seen = False
    for raw in resp.iter_lines():
        if raw is None:
            continue
        line = str(raw).strip()
        if not line:
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:") or line.startswith("id:"):
            continue
        if line.startswith("data:"):
            line = line[5:].strip()
        if not line:
            continue
        if line == "[DONE]":
            finish_seen = True
            return
        try:
            frame = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(frame, dict):
            continue
        if is_audit(json.dumps(frame, ensure_ascii=False)[:4000]):
            raise UpstreamError(protocol.text_of_payload(frame) or "需求被审核拦截",
                                status=400, kind="audit", code="audit_blocked",
                                detail=json.dumps(frame, ensure_ascii=False)[:600])
        error = protocol.frame_error(frame)
        if error is not None:
            code, message = error
            raise UpstreamError("灵犀上游返回 %s：%s" % (code, message),
                                status=502, kind="upstream", code=code,
                                detail=json.dumps(frame, ensure_ascii=False)[:600])
        choice = (frame.get("choices") or [{}])[0]
        if isinstance(choice, dict) and str(choice.get("finish_reason") or "").strip():
            finish_seen = True
            yield frame
            return
        yield frame
    if not finish_seen:
        # 上游在完成事件之前断流：如实报错，不伪造成功收尾
        raise UpstreamError("灵犀上游流在完成事件之前结束（响应被截断）",
                            status=502, kind="upstream", code="truncated")


def collect(cookie: str, run: dict, body: dict, *, model: str) -> dict:
    """非流式：把整条流收完折叠成 `chat.completion`。"""
    folder = protocol.FoldAccumulator(model)
    for frame in chat_stream(cookie, run, body, model=model):
        folder.feed(frame)
    return folder.result()


def update_execution_status(cookie: str, run: dict, status: str) -> None:
    """上报 run 的执行状态（任务流程依赖它翻转，失败静默）。"""
    base = run.get("base") or endpoints.base_of()
    url = base + (endpoints.EXEC_STATUS_PATH % run["session_id"])
    body: dict = {"group_id": run.get("group_id") or run.get("run_id"),
                  "status": status,
                  "run_token": run.get("group_id") or run.get("run_id")}
    if status == "running":
        body["lease_seconds"] = 90
    else:
        body["terminal_reason"] = "completed"
    try:
        request_json(url, headers=endpoints.plain_headers(cookie, base),
                     method="PUT", body=body, timeout=10.0, what="status")
    except Exception as exc:  # noqa: BLE001 - 上报失败不影响主流程
        logger.debug("灵犀执行状态上报失败：%s", exc)