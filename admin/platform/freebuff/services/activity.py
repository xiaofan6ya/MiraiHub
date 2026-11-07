"""Freebuff 每日活跃对话 + 失效账号复检。

## 为什么这个平台尤其需要「活跃」

Freebuff 的关键凭据是 Developer API key，而**上游没有任何轻量接口能验证
key 是否有效**（`/models` 免鉴权，`/usage` `/me` 一类全 404）。也就是说：
列表里所有账号都是 `unverified`，直到有人真发一次对话。

所以这里的「活跃对话」一箭双雕：

1. **保活**：让 key 保持被使用状态（Freebuff 是按 API key 计费的服务，
   长期不用的 key 更容易在控制台被清理）；
2. **验证**：顺手把 `verify_state` 从 `unverified` 翻成 `ok`，或者发现
   它已经被撤销（401 → `rejected`）。

默认**关闭**：这会花用户额度。要跑请设 `ADMIN_FREEBUFF_ACTIVITY=1`。

## 相关环境变量

    ADMIN_FREEBUFF_ACTIVITY=1        开启每日活跃（默认关）
    ADMIN_FREEBUFF_ACTIVITY_PROB=0.7 每天每号执行概率（真人也不会天天来）
    ADMIN_FREEBUFF_ACTIVITY_MAX_TOKENS=64  单次 max_tokens（够验证、少花钱）
    ADMIN_FREEBUFF_ACTIVITY_LIMIT=0  单次最多处理几个号（0=全部）
"""
from __future__ import annotations

import json
import logging
import os
import random
import time
from datetime import datetime

from sqlalchemy.orm import Session

from admin.platform.freebuff.core import client, endpoints
from admin.platform.freebuff.services import accounts as fb_accounts

logger = logging.getLogger("admin.platform.freebuff.activity")

#: 提问池：Freebuff 是面向开发者的英文站，用这些最像真人日常。
PROMPTS = (
    "Which is safer in Python: list or tuple for a fixed config? One sentence.",
    "Give me a one-line regex that strips trailing spaces in JavaScript.",
    "What's the quickest way to see which process is holding a port on Windows?",
    "Explain the difference between git fetch and git pull in one sentence.",
    "Is it fine to return a mutable default argument in Python? Why?",
    "Recommend a short checklist before force-pushing to a shared branch.",
    "What does HTTP 429 usually mean and what should a client do?",
    "One sentence: when should I use a Set instead of a List?",
)

#: 追问概率（同一账号再问一句，成本翻倍所以概率低）。
FOLLOW_UP_PROB = 0.25

#: 进程内「这个模型上次能用」的记忆，避免每次都撞同一个坏模型。
_GOOD_MODEL: dict[str, str] = {}

#: 活跃候选模型（便宜、非推理优先）。
_CHEAP_MODELS = ("gpt-5-nano", "gemini-3.8-flash", "deepseek-v4-flash",
                 "glm-4.5-flash", "llama-3.1-8b-instruct-turbo", "qwen3-5-4b")


def _enabled() -> bool:
    return (os.getenv("ADMIN_FREEBUFF_ACTIVITY") or "").strip() in ("1", "true", "yes")


def _probability() -> float:
    try:
        value = float(os.getenv("ADMIN_FREEBUFF_ACTIVITY_PROB") or 0.7)
    except (TypeError, ValueError):
        return 0.7
    return max(0.0, min(1.0, value))


def _max_tokens() -> int:
    try:
        value = int(os.getenv("ADMIN_FREEBUFF_ACTIVITY_MAX_TOKENS") or 64)
    except (TypeError, ValueError):
        return 64
    return max(8, min(512, value))


def _limit() -> int:
    try:
        return int(os.getenv("ADMIN_FREEBUFF_ACTIVITY_LIMIT") or 0)
    except (TypeError, ValueError):
        return 0


def _models_of(account) -> list[str]:
    """按「便宜优先 + 上次可用优先」排出候选模型。"""
    catalog, _note = fb_accounts.catalog_rows()
    available = {row["id"] for row in catalog or []}
    ordered: list[str] = []
    remembered = _GOOD_MODEL.get(str(account.uid or ""))
    if remembered and (not available or remembered in available):
        ordered.append(remembered)
    for model_id in _CHEAP_MODELS:
        if model_id not in ordered and (not available or model_id in available):
            ordered.append(model_id)
    if not ordered:
        ordered = [row["id"] for row in (catalog or [])
                   if not row.get("reasoning")][:5] or \
            [endpoints.DEFAULT_MODEL]
    return ordered


def _active_today(account, now: datetime | None = None) -> bool:
    data = fb_accounts.credentials_of(account)
    last = data.get("last_activity_at") or 0
    if not last:
        return False
    try:
        moment = datetime.utcfromtimestamp(int(last))
    except (TypeError, ValueError, OSError):
        return False
    now = now or datetime.utcnow()
    return moment.date() == now.date()


def _mark_activity(db: Session, account) -> None:
    data = fb_accounts.credentials_of(account)
    data["last_activity_at"] = int(time.time())
    account.auth_json = json.dumps(data, ensure_ascii=False)[:20000]
    try:
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()


def activate_account(db: Session, account, *, prompt: str | None = None,
                     follow_up: bool | None = None) -> dict:
    """对单个账号发一次（或两次）极小对话，返回结果。"""
    key = fb_accounts.key_of(account)
    if not key:
        return {"account_id": account.id, "ok": False, "reason": "缺少 API key"}
    if endpoints.is_session_token(key):
        return {"account_id": account.id, "ok": False,
                "reason": "这是会话 token，不是 Developer API key",
                "outcome": "session_token"}

    text = prompt or random.choice(PROMPTS)
    max_tokens = _max_tokens()
    last_error = None
    for model_id in _models_of(account):
        body = {
            "model": model_id,
            "messages": [{"role": "user", "content": text}],
            "max_tokens": max_tokens,
        }
        started = time.perf_counter()
        try:
            result = client.chat_completions(key, body)
        except client.UpstreamError as exc:
            last_error = exc
            if exc.kind == "auth":
                fb_accounts.mark_key_rejected(db, account, exc.message)
                return {"account_id": account.id, "ok": False,
                        "outcome": "rejected", "reason": exc.message}
            if exc.kind in ("fatal", "update_required"):
                return {"account_id": account.id, "ok": False,
                        "outcome": exc.kind, "reason": exc.message}
            logger.debug("Freebuff 活跃对话失败 model=%s：%s",
                         model_id, exc.message)
            continue

        usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
        _record_usage(db, account, model_id, usage, started)
        fb_accounts.mark_key_ok(db, account)
        _GOOD_MODEL[str(account.uid or "")] = model_id
        follow = FOLLOW_UP_PROB if follow_up is None else (1.0 if follow_up else 0.0)
        asked = 1
        if random.random() < follow:
            try:
                client.chat_completions(key, {
                    "model": model_id,
                    "messages": [
                        {"role": "user", "content": text},
                        {"role": "assistant",
                         "content": _first_text(result) or "OK."},
                        {"role": "user", "content": "Thanks — and one caveat?"},
                    ],
                    "max_tokens": max_tokens,
                })
                asked += 1
            except client.UpstreamError:
                pass
        return {"account_id": account.id, "ok": True, "outcome": "active",
                "model": model_id, "turns": asked,
                "tokens": int(usage.get("total_tokens") or 0)}
    reason = (last_error.message if last_error is not None else "没有可用模型")
    return {"account_id": account.id, "ok": False, "outcome": "failed",
            "reason": str(reason)[:200]}


def _first_text(result: dict) -> str:
    choices = result.get("choices") or []
    if not choices:
        return ""
    message = (choices[0] or {}).get("message") or {}
    text = message.get("content")
    return text if isinstance(text, str) else ""


def _record_usage(db: Session, account, model: str, usage: dict,
                  started: float) -> None:
    """把这次活跃对话记进本地账本（这样「用量日志」里能看到它）。

    走 `shared.record_usage` 而不是自己写 `UsageLog`：口径（倍率、缓存字段、
    credits 估算）必须与网关主链路一致，否则同一个平台在日志里会出现两种算法。
    """
    try:
        from admin.platform import shared

        elapsed = int((time.perf_counter() - started) * 1000)
        shared.record_usage(
            db,
            key_id=0,
            account_id=int(account.id or 0),
            model=model,
            usage=usage,
            public_platform=fb_accounts.PLATFORM,
            storage=fb_accounts.PLATFORM,
            use_case="freebuff-activity",
            seq=1,
            ttfb_ms=elapsed,
            latency_ms=elapsed,
        )
    except Exception:  # noqa: BLE001 - 记账失败不影响活跃结论
        logger.debug("Freebuff 活跃对话记账失败", exc_info=True)


def run_daily_activity(db: Session, realm: str = "cn",
                       limit: int | None = None) -> dict:
    """每日活跃：按概率给启用账号各发一次极小对话。"""
    if not _enabled():
        return {"task": "freebuff_activity", "ok": True, "skipped": True,
                "reason": "未开启（设 ADMIN_FREEBUFF_ACTIVITY=1 才会跑；"
                          "这会花额度）"}
    rows = [account for account in fb_accounts.accounts_for(db, realm)
            if (getattr(account, "status", "") or "") == "active"]
    cap = limit if limit is not None else _limit()
    probability = _probability()
    items: list[dict] = []
    active = rejected = skipped = failed = 0
    for index, account in enumerate(rows):
        if cap and index >= cap:
            break
        if _active_today(account):
            skipped += 1
            items.append({"account_id": account.id, "skipped": "today_done"})
            continue
        if random.random() > probability:
            skipped += 1
            items.append({"account_id": account.id, "skipped": "probability"})
            continue
        try:
            result = activate_account(db, account)
        except Exception as exc:  # noqa: BLE001 - 单号异常不影响批量
            logger.exception("Freebuff 活跃对话异常 acc=%s", account.id)
            result = {"account_id": account.id, "ok": False,
                      "reason": str(exc)[:200]}
        if result.get("ok"):
            active += 1
            _mark_activity(db, account)
        elif result.get("outcome") == "rejected":
            rejected += 1
        else:
            failed += 1
        items.append(result)
        time.sleep(random.uniform(0.5, 2.0))

    summary = ("活跃 %d 号：成功 %d · key 失效 %d · 失败 %d · 跳过 %d"
               % (len(items), active, rejected, failed, skipped))
    return {"task": "freebuff_activity", "ok": failed == 0,
            "accounts": len(items), "active": active, "rejected": rejected,
            "failed": failed, "skipped": skipped,
            "items": items, "summary": summary}


def recheck_disabled(db: Session, realm: str = "cn") -> dict:
    """失效账号复检：被 401 禁用的 key 又活了就恢复。

    只发一次极小对话（不重试、不追问），成本最低。
    """
    rows = [account for account in fb_accounts.accounts_for(db, realm)
            if (getattr(account, "status", "") or "") == "disabled"]
    recovered: list[dict] = []
    still_bad = 0
    for account in rows:
        try:
            result = activate_account(db, account, prompt="ping",
                                      follow_up=False)
        except Exception:  # noqa: BLE001
            logger.exception("Freebuff 复检异常 acc=%s", account.id)
            still_bad += 1
            continue
        if result.get("ok"):
            account.status = "active"
            account.consecutive_fails = 0
            account.err_count = 0
            try:
                db.commit()
            except Exception:  # noqa: BLE001
                db.rollback()
            recovered.append({"account_id": account.id,
                              "model": result.get("model")})
        else:
            still_bad += 1
    summary = ("复检 %d 个失效账号：恢复 %d · 仍然失效 %d"
               % (len(rows), len(recovered), still_bad))
    return {"task": "freebuff_ban_recheck", "ok": True,
            "checked": len(rows), "recovered": len(recovered),
            "items": recovered, "still_bad": still_bad, "summary": summary}