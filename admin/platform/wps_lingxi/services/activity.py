"""WPS 灵犀「每日活跃对话」：让每个号每天真的被用一次（模拟真人）。

## 为什么需要它

签到只证明「这个号今天点过按钮」，刷新额度只证明「有人查了余额」。
上游看的不是这两件事，而是**账号自己的行为画像**：

    真人号：每天发几条对话写周报 / 改文案 / 问问题，偶尔看余额、偶尔签到
    只签到 + 刷额度的号：管理接口天天有，conversation 一条没有
            → 标准的「机器在养号」形状

所以保活必须包含**真实对话**：走灵犀自己的三步通路（建 cowork 会话 →
领 credits run → agent-runtime SSE），问一句正常人会问的话，拿到回答，
并把这次调用如实记进 `usage_logs`（使用日志里能看到，和人用出来的一样）。

## 怎么做得不像机器

1. **串行 + 抖动**：人不会同时操作几十个号，间隔走 `humanize.human_gap`；
2. **话术随机**：从一批办公场景问题里挑，不是每天发 "hi"；
3. **不是 100% 每天必做**：默认 85% 概率，偶尔跳过一天更像人；
4. **量要小但真实**：`max_tokens` 默认 96，真的生成一小段；
5. **偶尔追问一句**：20% 概率在同一会话里补一句，像真人接着问；
6. **会话生命周期照客户端走**：`running` → `completed` 上报，不是发完就跑。

## 与其它任务的分工

    wps_lingxi_checkin     每天领智点（任务中心 claim）
    wps_lingxi_keepalive   校验 Cookie + 同步余额（轻量、可高频）
    wps_lingxi_activity    每天真发一条对话（本文，**不能省**）
    wps_lingxi_ban_recheck 失效/禁用账号复检（能通就恢复）

一次活动只花一次对话的钱（一个 run 内多次调用只扣一次，见 README），
比起号被封了重新搞，这个成本可以忽略。
"""
from __future__ import annotations

import logging
import os
import random
import time
from datetime import datetime, timedelta

from admin import humanize
from admin.platform.wps_lingxi.core import endpoints
from admin.platform.wps_lingxi.core import protocol
from admin.platform.wps_lingxi.core import client
from admin.platform.wps_lingxi.services import accounts as lingxi_accounts

logger = logging.getLogger("admin.platform.wps_lingxi.activity")

TASK_NAME = "wps_lingxi_activity"

#: 每天做这件事的概率（真人也会几天不打开）。
DEFAULT_PROBABILITY = 0.85
#: 号与号之间的基准间隔（秒），会再叠 humanize 抖动。
DEFAULT_GAP_SECONDS = 2.6
#: 单次对话的 max_tokens：太小像探针，太大烧智点。
DEFAULT_MAX_TOKENS = 96
#: 追问概率（同一 run 内再发一句，成本不变，画像更真）。
FOLLOW_UP_PROBABILITY = 0.20

#: 办公场景提问池：灵犀是 WPS 的办公助手，问这些最自然。
PROMPTS = (
    "帮我把这句话写得更正式一点：这个方案我们下周再对一下。",
    "用三句话总结一下周报该怎么写。",
    "把「收到，我马上处理」改成更礼貌的说法。",
    "帮我想三个会议邀请的标题。",
    "这段话有没有语病：我们团队最近在做一个新的功能，然后就是想让大家提提意见。",
    "帮我把这段话压缩到 30 字以内：这次改版主要是把首页的加载速度提上去，同时把按钮位置调整得更顺手。",
    "给我列一下项目复盘通常包含哪几个部分。",
    "帮我把这个需求描述得更清楚：用户希望能批量导出数据。",
    "怎么把一份 20 页的 PPT 精简到 10 页？给个思路。",
    "帮我想两个表格的列名：一个是费用明细，一个是审批记录。",
    "把这段英文翻成中文：Please review the attached document by Friday.",
    "帮我把这句话改成更委婉的催办：你那个材料什么时候能给我？",
    "写一段 50 字左右的产品更新说明，主题是支持批量导入。",
    "帮我把这个待办拆成三步：整理数据、核对数据、提交报告。",
    "用一句话解释什么是回归测试。",
    "帮我想几个适合月度总结的小标题。",
    "这段话说得太长了，帮我精简：由于近期服务器压力比较大，所以我们决定在周末的凌晨做一次维护，维护期间可能会短暂影响使用，请大家提前保存好工作内容。",
    "帮我把这封邮件改得更简短：领导您好，我这边想问一下上次提交的报销单大概什么时候能审批下来，因为月底需要结算。",
    "给我一个整理会议纪要的模板。",
    "帮我把这段话里的口语去掉：这个事儿吧，咱们就先这样，回头再说。",
)


# ---------------------------------------------------------------------------
# 参数
# ---------------------------------------------------------------------------
def _enabled() -> bool:
    return (os.getenv("ADMIN_WPS_LINGXI_ACTIVITY") or "1").strip() not in ("0", "false", "no")


def _probability() -> float:
    try:
        value = float(os.getenv("ADMIN_WPS_LINGXI_ACTIVITY_PROB") or DEFAULT_PROBABILITY)
    except ValueError:
        value = DEFAULT_PROBABILITY
    return min(1.0, max(0.0, value))


def _gap_seconds() -> float:
    try:
        value = float(os.getenv("ADMIN_WPS_LINGXI_ACTIVITY_GAP") or DEFAULT_GAP_SECONDS)
    except ValueError:
        value = DEFAULT_GAP_SECONDS
    return max(0.5, value)


def _max_tokens() -> int:
    try:
        value = int(os.getenv("ADMIN_WPS_LINGXI_ACTIVITY_MAX_TOKENS") or DEFAULT_MAX_TOKENS)
    except ValueError:
        value = DEFAULT_MAX_TOKENS
    return max(16, min(512, value))


# ---------------------------------------------------------------------------
# 模型选择
# ---------------------------------------------------------------------------
#: 进程内「最近可用模型」记忆：避免每次都挑到不可用的那个。
_WORKING_MODEL = ""


def _models_of(account) -> list[str]:
    """这个号能用的模型：优先上游目录，失败回落内置快照。"""
    cookie = lingxi_accounts.cookie_of(account)
    if cookie:
        try:
            catalog = client.fetch_plans(cookie, lingxi_accounts.base_of(account))
            ids = [str(item.get("id") or item.get("model") or "").strip()
                   for item in catalog if isinstance(item, dict)]
            ids = [item for item in ids if item]
            if ids:
                return ids
        except client.UpstreamError as exc:
            logger.debug("灵犀模型目录拉取失败 acc=%s：%s", account.id, exc.message)
    return [str(item.get("id")) for item in endpoints.DEFAULT_MODELS
            if isinstance(item, dict) and item.get("id")]


def _pick_model(account) -> str:
    """挑一个模型：上次成功的 > 内置默认 > 目录第一个。"""
    global _WORKING_MODEL
    models = _models_of(account)
    if not models:
        return endpoints.DEFAULT_MODEL
    if _WORKING_MODEL and _WORKING_MODEL in models:
        return _WORKING_MODEL
    if endpoints.DEFAULT_MODEL in models:
        return endpoints.DEFAULT_MODEL
    return models[0]


# ---------------------------------------------------------------------------
# 「今天活跃过没有」
# ---------------------------------------------------------------------------
def _active_today(db, account, now: datetime | None = None) -> bool:
    """本地账本判定：今天有没有**非探针**的对话记录。

    只看 `usage_logs`：签到 / 余额同步不写这张表，所以它天生只反映
    「这个号今天有没有被真正调用过」。不查上游 —— 灵犀没有稳定的
    用量查询接口，多打一次就是多一次机器特征。
    """
    from admin.models import UsageLog

    today = (now or datetime.utcnow()).replace(hour=0, minute=0, second=0, microsecond=0)
    row = (db.query(UsageLog.id)
           .filter(UsageLog.platform == lingxi_accounts.PLATFORM,
                   UsageLog.account_id == account.id,
                   UsageLog.created_at >= today,
                   UsageLog.model != "")
           .first())
    return row is not None


# ---------------------------------------------------------------------------
# 记账
# ---------------------------------------------------------------------------
def _record_usage(db, account, model: str, result: dict, *,
                  prompt: str, started: float, error: str = "") -> None:
    """把这次活动写进 usage_logs（使用日志 / 用量详情都靠它）。"""
    from admin.models import UsageLog

    usage = (result or {}).get("usage") or {}
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    total = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))
    latency = int((time.perf_counter() - started) * 1000)
    row = UsageLog(
        api_key_id=0,
        account_id=account.id,
        model=model,
        credits=0,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total,
        cached_tokens=int(((usage.get("prompt_tokens_details") or {})
                           .get("cached_tokens")) or 0),
        client_ip="",
        platform=lingxi_accounts.PLATFORM,
        use_case="daily_activity",
        latency_ms=latency,
        error_kind=error[:32] if error else None,
        diagnostic_json=("" if not error and not prompt else
                         __import__("json").dumps(
                             {"prompt": prompt[:120], "error": error[:200]},
                             ensure_ascii=False)[:2000]),
        created_at=datetime.utcnow(),
    )
    try:
        db.add(row)
        db.commit()
    except Exception:  # noqa: BLE001 - 记账失败不影响活动本身
        db.rollback()
        logger.debug("灵犀活跃记账失败 acc=%s", account.id, exc_info=True)


# ---------------------------------------------------------------------------
# 单号活跃
# ---------------------------------------------------------------------------
def activate_account(db, account, *, prompt: str | None = None,
                     max_tokens: int | None = None) -> dict:
    """给一个号发一条真实对话。**不抛异常**，失败写进返回值。"""
    cookie = lingxi_accounts.cookie_of(account)
    if not cookie:
        return {"account_id": account.id, "ok": False, "status": "skipped",
                "reason": "账号缺少 Cookie"}

    model = _pick_model(account)
    text = prompt or random.choice(PROMPTS)
    tokens = max_tokens or random.randint(max(16, _max_tokens() // 2), _max_tokens())
    started = time.perf_counter()
    run = None
    result: dict = {}

    try:
        body = {"model": model,
                "messages": [{"role": "user", "content": text}],
                "max_tokens": tokens, "stream": False}
        payload, dropped, elided = protocol.prepare_agent_body(dict(body))
        run = client.ensure_run(cookie, model,
                                messages=body["messages"])
        try:
            client.update_execution_status(cookie, run, "running")
        except Exception:  # noqa: BLE001 - 状态上报失败不阻塞对话
            pass
        result = client.collect(cookie, run, payload, model=model)

        # 偶尔追问一句：同一 run 内多一轮，成本不变，画像更像真人
        follow_up = ""
        if humanize.human_probability(FOLLOW_UP_PROBABILITY):
            follow_up = "再简短一点，控制在两句话内。"
            follow_body = {"model": model,
                           "messages": [{"role": "user", "content": text},
                                        {"role": "assistant",
                                         "content": _first_text(result)},
                                        {"role": "user", "content": follow_up}],
                           "max_tokens": max(16, tokens // 2), "stream": False}
            follow_payload, _, _ = protocol.prepare_agent_body(dict(follow_body))
            client.collect(cookie, run, follow_payload, model=model)
        try:
            client.update_execution_status(cookie, run, "completed")
        except Exception:  # noqa: BLE001
            pass

        answer = _first_text(result)
        if not answer:
            _record_usage(db, account, model, result, prompt=text,
                          started=started, error="empty")
            return {"account_id": account.id, "ok": False, "status": "error",
                    "model": model, "reason": "上游返回 200 但内容为空"}
    except client.UpstreamError as exc:
        _record_usage(db, account, model, result, prompt=text,
                      started=started, error=exc.kind or "upstream")
        if exc.kind == "auth":
            lingxi_accounts._count_failure(db, account, exc)  # noqa: SLF001
        logger.info("灵犀活跃失败 acc=%s kind=%s：%s", account.id, exc.kind, exc.message)
        return {"account_id": account.id, "ok": False, "status": "error",
                "model": model, "reason": exc.message[:160], "kind": exc.kind}
    except Exception as exc:  # noqa: BLE001 - 单号异常不拖垮整批
        _record_usage(db, account, model, result, prompt=text,
                      started=started, error="exception")
        logger.exception("灵犀活跃异常 acc=%s", account.id)
        return {"account_id": account.id, "ok": False, "status": "error",
                "model": model, "reason": str(exc)[:160], "kind": "exception"}

    _record_usage(db, account, model, result, prompt=text, started=started)
    account.last_used_at = datetime.utcnow()
    try:
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()

    usage = (result.get("usage") or {})
    return {"account_id": account.id, "ok": True, "status": "done", "model": model,
            "prompt": text[:40], "follow_up": bool(follow_up),
            "tokens": int(usage.get("total_tokens") or 0),
            "answer": _first_text(result)[:80]}


def _first_text(result: dict) -> str:
    try:
        choices = result.get("choices") or []
        message = (choices[0] or {}).get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            return "".join(str(block.get("text") or "") for block in content
                           if isinstance(block, dict)).strip()
    except Exception:  # noqa: BLE001
        return ""
    return ""


# ---------------------------------------------------------------------------
# 批量入口（定时任务）
# ---------------------------------------------------------------------------
def run_daily_activity(db, realm: str = "", *, limit: int | None = None,
                       force: bool = False) -> dict:
    """给该平台每个启用账号发一条自然对话（**串行 + 抖动**）。

    `force=True` 用于**手动点按钮**：跳过「今天没打开」的概率掷骰 ——
    用户既然点了「活跃对话」，就是要现在发一条，不是来看「概率跳过」的。
    「今天已经活跃过」仍然跳过（补发没有意义，只会多一条机器特征）。
    """
    if not _enabled():
        return {"task": TASK_NAME, "ok": True, "done": 0, "skipped": 0,
                "failed": 0, "summary": "每日活跃已关闭（ADMIN_WPS_LINGXI_ACTIVITY=0）"}

    started = time.perf_counter()
    now = datetime.utcnow()
    accounts = [account for account in lingxi_accounts.accounts_for(db, realm)
                if (getattr(account, "status", "") or "") == "active"]
    if limit:
        accounts = accounts[:int(limit)]
    if not accounts:
        return {"task": TASK_NAME, "ok": True, "total": 0,
                "summary": "没有启用的 WPS 灵犀账号"}

    # 手动触发时不做「今天没打开」的概率跳过；定时任务仍按概率走。
    probability = 1.0 if force else _probability()
    gap = _gap_seconds()
    done = skipped = failed = 0
    detail: list[dict] = []

    # 定时任务撞在同一秒开跑本身就是机器特征：先随机等一下
    humanize.human_sleep(humanize.human_start_delay(15.0), floor=0.0)

    for index, account in enumerate(accounts, 1):
        item = {"account_id": account.id, "account": account.name or str(account.id),
                "ok": False, "status": "skipped", "reason": ""}
        cooling = any(getattr(account, field, None) and getattr(account, field) > now
                      for field in ("cool_until", "breaker_until", "degrade_until"))
        if cooling:
            item["reason"] = "冷却中，本次不打扰"
            skipped += 1
            detail.append(item)
            continue
        if not humanize.human_probability(probability):
            item["reason"] = "今天没打开（概率跳过）"
            skipped += 1
            detail.append(item)
            continue
        if _active_today(db, account, now):
            item["reason"] = "今天已活跃，不补发"
            skipped += 1
            detail.append(item)
            continue

        result = activate_account(db, account)
        item.update(result)
        item["account"] = account.name or str(account.id)
        if result.get("ok"):
            done += 1
        else:
            failed += 1
        detail.append(item)

        if index < len(accounts):
            humanize.human_sleep(gap, floor=0.4, ceil=gap * 6)

    elapsed = round(time.perf_counter() - started, 1)
    summary = ("灵犀每日活跃：完成 %d 个，跳过 %d 个，失败 %d 个（用时 %ss）"
               % (done, skipped, failed, elapsed))
    logger.info("%s", summary)
    return {"task": TASK_NAME, "ok": failed == 0, "done": done, "skipped": skipped,
            "failed": failed, "accounts": len(accounts), "items": detail[:60],
            "elapsed_s": elapsed, "summary": summary,
            "note": "串行 + 抖动 + 概率跳过：目的是让每个号每天有真实对话请求，"
                    "不是每天定时刷一次管理接口"}


# ---------------------------------------------------------------------------
# 失效账号复检
# ---------------------------------------------------------------------------
def recheck_disabled(db, realm: str = "") -> dict:
    """把 disabled 的号探一次：Cookie 又有效了就恢复，避免永久躺平。

    灵犀没有 refresh 接口，Cookie 过期只能重新粘贴；但这个号可能只是
    上次探活时网络抖动被判失效。每小时复用一次，比人工点「刷新」靠谱。
    """
    accounts = [account for account in lingxi_accounts.accounts_for(db, realm)
                if (getattr(account, "status", "") or "") != "active"]
    recovered: list[dict] = []
    still_bad = 0
    for account in accounts:
        result = lingxi_accounts.sync_quota(db, account)
        if result.get("ok"):
            # 显式恢复，不依赖 sync_quota 内部的副作用：探活通过 = 这个号能用
            if (getattr(account, "status", "") or "") != "active":
                account.status = "active"
                try:
                    db.commit()
                except Exception:  # noqa: BLE001
                    db.rollback()
            recovered.append({"account_id": account.id,
                              "name": account.name,
                              "balance": int(getattr(account, "balance_remain", 0) or 0)})
        else:
            still_bad += 1
    summary = "失效复检：恢复 %d 个，仍不可用 %d 个" % (len(recovered), still_bad)
    return {"task": TASK_NAME.replace("activity", "ban_recheck"),
            "ok": True, "recovered": recovered, "still_bad": still_bad,
            "summary": summary}