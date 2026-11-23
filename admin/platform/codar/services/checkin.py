"""科搭 Codar 签到（滚动 24 小时）+ 免费模型活动（尽力而为）。

## 签到为什么不是「自然日」

上游的窗口是**滚动 24 小时**：领一次之后，下一次可领的时刻是「这次领取
之后 24 小时」，不是「明天 0 点」。所以：

  * 判据是上游回的 `retry_after`（秒），不是日期比较；
  * `until` 只能由「现在 + retry_after」算出来，**上游没给就不猜** ——
    猜一个时间会让面板显示一个错误的下次可领时刻，比不显示更糟；
  * retry_after 超过一周一律不认（那是异常值，可能是年、可能是别的窗口）。

## 三种结果，别混

    claimed   本次领到了
    already   已领过（回 until；没有可信时间就不回）
    failed    上游拒绝 / 网络错误（文案照上游，或指明是登录还是限流）

## 免费模型活动是「加菜」

`GET /api/codar/activities/free-model` 说 `status: "available"` 且有
`activity_id` 时才 `POST .../claim`（无请求体）。它是**写操作**，所以：

  * 只在签到流程里做，失败不影响签到结果；
  * 同一个 activity_id 在本进程里最多领一次（不会因为面板刷新而反复写）；
  * 上游拒绝（已领 / 没开 / 登录过期）就安静跳过，不改动签到的答案。
"""
from __future__ import annotations

import logging
import time

from sqlalchemy.orm import Session

from admin.platform.codar.core import client, endpoints
from admin.platform.codar.services import accounts as codar_accounts

logger = logging.getLogger("admin.platform.codar.services.checkin")

#: 签到的等待只认这个范围内的数：滚动窗口最多 24 小时，一周是硬上限
MAX_WAIT_SECONDS = 7 * 24 * 3600

#: 本进程已尝试领取的活动 id（写操作只做一次）
_CLAIMED_ACTIVITIES: set[str] = set()


# ---------------------------------------------------------------------------
# 读数
# ---------------------------------------------------------------------------
def _wait_seconds(*values) -> int:
    """从若干候选里取第一个可信的等待秒数（没有给 0）。"""
    for value in values:
        seconds = client.retry_after_seconds(value)
        if seconds and seconds <= MAX_WAIT_SECONDS:
            return int(round(seconds))
    return 0


def _body(payload):
    return client.unwrapped(payload)


def sign_state(payload):
    """签到态读数：`data` 是布尔，或对象里四种拼写之一。

    返回 True / False / None（None = 上游这次没表态，**不要**当成没签）。
    """
    body = _body(payload)
    if isinstance(body, bool):
        return body
    if not isinstance(body, dict):
        return None
    named = False
    for name in ("isSigned", "signed", "hasSigned", "has_signed", "is_signed"):
        if name not in body:
            continue
        named = True
        value = body.get(name)
        if isinstance(value, bool):
            return value
    return False if named else None


def status_of(db: Session, account) -> dict:
    """读签到态（`/v1/daily-sign/status`）→ {signed, until, wait_seconds}。"""
    token = codar_accounts.token_for_call(db, account)
    username = codar_accounts.username_of_account(account)
    payload = client.request_json(
        "POST", endpoints.account_url(endpoints.SIGN_STATUS_PATH), token=token,
        json_body={"username": username},
        account_id=int(getattr(account, "id", 0) or 0), context="签到状态")
    body = _body(payload)
    wait = _wait_seconds(
        body.get("retry_after") if isinstance(body, dict) else None,
        payload.get("retry_after") if isinstance(payload, dict) else None)
    signed = sign_state(payload)
    return {"signed": signed if signed is not None else False,
            "state_known": signed is not None,
            "wait_seconds": wait,
            "until": time.time() + wait if wait else None}


# ---------------------------------------------------------------------------
# 签到
# ---------------------------------------------------------------------------
def _already(payload, status: int, wait: int, words: str) -> bool:
    """这次回答是「今日已领」吗。

    两条独立判据，任一成立：
      1. 措辞命中（中英文都认）；
      2. **400 + 可读的 retry_after** —— 滚动窗口里几乎只可能是已领，
         但「限流/风控/维护」这类 400 也带秒数，那些措辞必须否决它。
    """
    if client.already_claimed_text(words, client.said_by(payload)):
        return True
    if status == 400 and wait and not client.failed_text(words):
        return True
    return False


def _points_of(payload):
    body = _body(payload)
    if not isinstance(body, dict):
        return None
    for name in ("points", "reward", "credit", "points_added",
                 "granted_points", "sign_points"):
        value = body.get(name)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str) and value.strip().replace(".", "", 1).isdigit():
            return float(value.strip())
    return None


def sign_in(db: Session, account) -> dict:
    """给一个账号签到。返回 {ok, outcome, until, wait_seconds, message, points}。"""
    account_id = int(getattr(account, "id", 0) or 0)
    label = str(getattr(account, "name", "") or ("#" + str(account_id)))
    try:
        token = codar_accounts.token_for_call(db, account)
    except client.UpstreamError as exc:
        return {"ok": False, "outcome": "failed", "account": label,
                "message": exc.message, "kind": exc.kind, "until": None,
                "wait_seconds": 0, "points": None}
    username = codar_accounts.username_of_account(account)
    if not username:
        return {"ok": False, "outcome": "failed", "account": label,
                "message": "账号没有用户名，无法签到", "kind": "auth",
                "until": None, "wait_seconds": 0, "points": None}

    # 先读状态：已领过的号不必发一次写请求，直接回下次可领时间
    try:
        state = status_of(db, account)
    except client.UpstreamError as exc:
        if exc.kind in ("auth", "account_restricted", "client_gated"):
            return _failure(label, exc)
        state = {"signed": False, "state_known": False, "wait_seconds": 0,
                 "until": None}
    if state.get("signed"):
        return {"ok": True, "outcome": "already", "account": label,
                "message": "今日已签到" + (
                    "，%d 小时 %d 分后可再领"
                    % (state["wait_seconds"] // 3600,
                       state["wait_seconds"] % 3600 // 60)
                    if state.get("wait_seconds") else ""),
                "until": state.get("until"), "wait_seconds": state.get("wait_seconds") or 0,
                "points": None}

    try:
        payload = client.request_json(
            "POST", endpoints.account_url(endpoints.SIGN_PATH), token=token,
            json_body={"username": username}, account_id=account_id,
            context="签到")
    except client.UpstreamError as exc:
        body = exc.body if isinstance(exc.body, dict) else None
        wait = 0
        words = ""
        if body:
            words = client.said_by(body)
            wait = _wait_seconds(
                (body.get("data") or {}).get("retry_after")
                if isinstance(body.get("data"), dict) else None,
                body.get("retry_after"), exc.retry_after)
        if exc.retry_after and not wait:
            wait = exc.retry_after if exc.retry_after <= MAX_WAIT_SECONDS else 0
        # 上游把「已领」也会用 400 表达：措辞或「400 + 秒数」都算
        if body and _already(body, exc.status, wait, words):
            return _already_result(label, wait)
        if words and client.already_claimed_text(words):
            return _already_result(label, wait)
        return _failure(label, exc, wait=wait)

    body = payload if isinstance(payload, dict) else {}
    wait = _wait_seconds(
        (body.get("data") or {}).get("retry_after")
        if isinstance(body.get("data"), dict) else None,
        body.get("retry_after"))
    words = client.said_by(payload)
    # 200 里也可能写着「已领」（上游两种形态都出现过）
    if client.already_claimed_text(words):
        return _already_result(label, wait)
    if body.get("success") is False:
        return {"ok": False, "outcome": "failed", "account": label,
                "message": words or "签到被上游拒绝", "kind": "upstream",
                "until": None, "wait_seconds": wait, "points": None}
    result = {"ok": True, "outcome": "claimed", "account": label,
              "message": words or "签到成功",
              "until": time.time() + wait if wait else None,
              "wait_seconds": wait, "points": _points_of(payload)}
    free = claim_free_model(db, account)
    if free:
        result["free_model"] = free
        result["message"] = (result["message"] or "签到成功") + "；已领取免费模型"
    return result


def _already_result(label: str, wait: int) -> dict:
    return {"ok": True, "outcome": "already", "account": label,
            "message": "今日已签过（滚动 24 小时）",
            "until": time.time() + wait if wait else None,
            "wait_seconds": wait, "points": None}


def _failure(label: str, exc: client.UpstreamError, *, wait: int = 0) -> dict:
    message = exc.message
    if exc.kind == "concurrent" and wait:
        message = "%s（约 %d 秒后可重试）" % (message, wait)
    return {"ok": False, "outcome": "failed", "account": label,
            "message": message, "kind": exc.kind,
            "until": time.time() + wait if wait else None,
            "wait_seconds": wait, "points": None}


# ---------------------------------------------------------------------------
# 免费模型活动
# ---------------------------------------------------------------------------
def _amount_or_none(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def running_activities(payload) -> list[dict]:
    """还在跑的免费模型活动：`free_until` 在未来、额度没耗尽。

    判据只看这两个字段（上游的 `status` 在活动结束后仍可能是 active），
    额度是 null 表示不限量，读不出来则**不上报** —— 把它画成「不限量」
    等于替上游做了一个它没做的承诺。
    """
    if isinstance(payload, dict):
        if client.error_word(payload):
            return []
        if payload.get("success") is False:
            return []
        items = payload.get("data")
        if not isinstance(items, list):
            return []
    elif isinstance(payload, list):
        items = payload
    else:
        return []
    now = time.time()
    out: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        until = _amount_or_none(item.get("free_until"))
        if until is None or until <= now:
            continue
        quota = _amount_or_none(item.get("remaining_quota"))
        if item.get("remaining_quota") is not None and (quota is None or quota <= 0):
            continue
        out.append({
            "name": str(item.get("name") or item.get("title")
                        or item.get("activity_name") or "免费模型活动"),
            "activity_id": str(item.get("activity_id") or item.get("id") or ""),
            "free_until": int(until),
            "remaining_quota": quota,
            "models": item.get("models") if isinstance(item.get("models"), list) else [],
        })
    return out


def activities(db: Session, account) -> list[dict]:
    """读活动列表（失败给空表：这是附加信息，不该让卡片报错）。"""
    try:
        token = codar_accounts.token_for_call(db, account)
        payload = client.request_json(
            "GET", endpoints.gateway_url(endpoints.ACTIVITIES_PATH), token=token,
            account_id=int(getattr(account, "id", 0) or 0), context="活动")
    except client.UpstreamError as exc:
        logger.debug("科搭活动读取失败：%s", exc.message)
        return []
    return running_activities(payload)


def _campaign(db: Session, account) -> dict | None:
    try:
        token = codar_accounts.token_for_call(db, account)
        payload = client.request_json(
            "GET", endpoints.gateway_url(endpoints.FREE_MODEL_PATH),
            token=token, account_id=int(getattr(account, "id", 0) or 0),
            context="免费模型活动")
    except client.UpstreamError:
        return None
    if not isinstance(payload, dict) or client.error_word(payload) \
            or payload.get("success") is False:
        return None
    body = client.unwrapped(payload)
    if not isinstance(body, dict):
        return None
    status = str(body.get("status") or "").strip().lower()
    activity_id = body.get("activity_id")
    named = (isinstance(activity_id, str) and activity_id.strip()) \
        or (isinstance(activity_id, (int, float)) and not isinstance(activity_id, bool))
    if status != "available" or not named:
        return None
    return {"activity_id": str(activity_id).strip(),
            "free_until": int(_amount_or_none(body.get("free_until")) or 0)}


def claim_free_model(db: Session, account) -> dict | None:
    """领一次免费模型活动（尽力而为）：领到了给 {activity_id, free_until}。"""
    campaign = _campaign(db, account)
    if not campaign:
        return None
    activity_id = campaign["activity_id"]
    if activity_id in _CLAIMED_ACTIVITIES:
        return None
    try:
        token = codar_accounts.token_for_call(db, account)
        payload = client.request_json(
            "POST", endpoints.gateway_url(endpoints.FREE_MODEL_CLAIM_PATH),
            token=token, account_id=int(getattr(account, "id", 0) or 0),
            context="领取免费模型")
    except client.UpstreamError:
        _CLAIMED_ACTIVITIES.add(activity_id)
        return None
    _CLAIMED_ACTIVITIES.add(activity_id)
    if isinstance(payload, dict) and (client.error_word(payload)
                                      or payload.get("success") is False):
        return None
    return {"activity_id": activity_id,
            "free_until": campaign.get("free_until") or 0}


# ---------------------------------------------------------------------------
# 批量（定时任务 / 手动按钮）
# ---------------------------------------------------------------------------
def checkin_all(db: Session, *, realm: str = "", limit: int = 100) -> dict:
    """批量签到 → {ok, summary, items, failed, claimed, already}（控制台同款）。"""
    rows = [row for row in codar_accounts.accounts_for(db, realm)
            if row.status == "active"][:limit]
    items = []
    failed = []
    claimed = already = 0
    for row in rows:
        result = sign_in(db, row)
        label = str(row.name or ("#" + str(row.id)))
        if result.get("outcome") == "claimed":
            claimed += 1
            items.append({"account": label, "already_processed": False,
                          "current_balance": row.balance_remain,
                          "daily_balance": result.get("points") or 0,
                          "until": result.get("until")})
        elif result.get("outcome") == "already":
            already += 1
            items.append({"account": label, "already_processed": True,
                          "current_balance": row.balance_remain,
                          "daily_balance": 0, "until": result.get("until")})
        else:
            failed.append({"account": label, "error": result.get("message")
                           or "签到失败"})
    summary = "签到 %d · 已签 %d · 失败 %d（共 %d 号）" % (
        claimed, already, len(failed), len(rows))
    if not rows:
        summary = "没有启用中的科搭账号"
    return {"platform": endpoints.PLATFORM, "ok": not failed,
            "checked": len(rows), "claimed": claimed, "already": already,
            "failed": failed, "items": items, "summary": summary,
            "supported": True}


def _run_checkin(db: Session, schedule=None) -> dict:
    """定时任务入口（`codar_checkin`）。"""
    result = checkin_all(db)
    return {"ok": bool(result.get("ok")), "checked": result["checked"],
            "claimed": result["claimed"], "already": result["already"],
            "failed": len(result["failed"]), "summary": result["summary"]}
