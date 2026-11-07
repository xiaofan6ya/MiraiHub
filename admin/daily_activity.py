"""每日「活跃对话」：让每个号每天真的被用一次。

## 问题（用户实测反馈，2026-11-06）

> 被 11140 的账号都是那种用过我们系统之后没怎么继续经常用的账号，
> 反而更容易被封。

这不是错觉。上游看账号，看的是**账号自己的行为画像**：

    真人号：每天发几条对话、偶尔看余额、偶尔签到
            → 对话请求占了绝大多数
    我们闲置的号：token 天天在用，但只打 `/userinfo`、`/balance`、
                  `/points`、`/models` 这类**管理接口**，一条对话都不发
            → 完美的「机器在养号」形状

所以「保活」只刷 token 是不够的 —— 那只是让 token 不过期，
账号画像反而更假。**要让它每天真的说一句话**。

## 怎么做才不像机器

1. **串行、带抖动**：人不会同时操作 100 个号。间隔走 `humanize.human_gap`，
   不是固定 2 秒。
2. **话术随机**：从一批正常提问里挑，不是每次都发 "hi"。
3. **不是 100% 每天都做**：给一个概率（默认 85%），偶尔跳过一天更像人。
4. **量要小但真实**：`max_tokens` 给 64，让它真的生成一小段，
   而不是 `max_tokens=1` 那种探针形状。

## 成本

按最便宜的模型算，一个号一天一条 64 token 的回复，101 个号
一天不到 1 万分（Loomy）/ 几美分（默认平台）。比起被封号重新搞号，
这个成本可以忽略。

## 和 `ban_recheck` 的区别

    ban_recheck.probe_model_capability  被封号后**探测**能不能用（1 token，发 "hi"）
    daily_activity.run_daily_activity   没被封的号**日常使用**（真提问，64 token）

两者不能互相替代：探针的请求形状本身就是机器特征。
"""
from __future__ import annotations

import json
import logging
import os
import random
import time
from datetime import datetime

from admin import humanize

_logger = logging.getLogger("admin.daily_activity")

#: 每天做这件事的概率。不是 100% —— 真人也会有几天不打开。
DEFAULT_PROBABILITY = 0.85

#: 每个号之间等待的基准秒数（会再叠 humanize 抖动）。
DEFAULT_GAP_SECONDS = 2.2

#: 单次对话的 max_tokens。太小像探针，太大烧额度。
DEFAULT_MAX_TOKENS = 64

#: 话术池。都是正常人会问的一句话，长度参差（机器生成的话术长度太整齐）。
#:
#: ## 为什么混入「技能型」请求（2026-11-06 用户要求）

#: > 调用一下客户端的真实技能啊专家啥的，但是不带入 chat 之类的上下文会污染
#:
#: 只用纯聊天保活，账号画像会变成「一个只会闲聊的号」—— 那也不像真的在用
#: Loomy/默认平台的人（真人会写文档、做 PPT、设定时任务、装技能）。
#: 所以池子里按约 1/3 混入这些**客户端本身就有的用法**。
#:
#: 注意：这些都是**单轮、无上下文**的请求，不会在账号里留下长期会话，
#: 用完即弃 —— 用户明确要求不要污染 chat 上下文。
PROMPTS = (
    "帮我把这段话改得通顺一点：今天我们开会讨论了新的上线计划，大家觉得时间有点紧。",
    "解释一下什么是幂等，用一句话。",
    "写一个 Python 函数，判断一个字符串是不是回文。",
    "今天适合出门吗？随便聊聊。",
    "把「用户登录失败」这个报错可能的原因列三条。",
    "用一句话说明 TCP 和 UDP 的区别。",
    "帮我起三个产品名字，要简短好记。",
    "什么是负载均衡？说人话。",
    "给一段 Shell 命令，统计当前目录下有多少个 .py 文件。",
    "解释一下为什么缓存能提速。",
    "帮我想一个周报的开头。",
    "这段正则 \\d{3}-\\d{4} 匹配什么？",
    "用三句话介绍你自己。",
    "数据库索引为什么不能建太多？",
    "帮我把「我明天可能要迟到」改得委婉一点。",
    # ---- 技能型（对应客户端真实用法，不是闲聊）----
    "帮我整理一份本周工作小结，分三点写。",
    "把下面这句话翻译成英文并保持语气正式：请在下周三之前提交材料。",
    "给这段 JSON 写一个字段说明表格：{\"name\":\"a\",\"count\":2}",
    "帮我把这段话拆成待办项：先修登录 bug，再补单元测试，最后发版。",
    "用表格对比一下 MySQL 和 PostgreSQL 的适用场景。",
)

#: 技能型话术的下标区间（用于统计/调试时区分「闲聊 vs 技能用法」）。
SKILL_PROMPT_INDEX = 15


def _enabled() -> bool:
    return os.getenv("ADMIN_DAILY_ACTIVITY", "1") != "0"


def _probability() -> float:
    try:
        value = float(os.getenv("ADMIN_DAILY_ACTIVITY_PROBABILITY",
                                str(DEFAULT_PROBABILITY)))
    except (TypeError, ValueError):
        value = DEFAULT_PROBABILITY
    return max(0.0, min(1.0, value))


def _gap_seconds() -> float:
    try:
        return max(0.0, float(os.getenv("ADMIN_DAILY_ACTIVITY_GAP",
                                        str(DEFAULT_GAP_SECONDS))))
    except (TypeError, ValueError):
        return DEFAULT_GAP_SECONDS


def _max_tokens() -> int:
    try:
        return max(1, int(os.getenv("ADMIN_DAILY_ACTIVITY_MAX_TOKENS",
                                    str(DEFAULT_MAX_TOKENS))))
    except (TypeError, ValueError):
        return DEFAULT_MAX_TOKENS


def _candidate_models(db) -> list[str]:
    """候选模型（**按便宜优先排序，随机打散前几名**）。

    为什么给一串而不是一个：模型白名单是**全局**的，不按账号区分。
    实测某账号对 `sn-sensenova-6-8-flash` 直接回
    `service info not found` —— 上游这个号根本没有该模型。

    所以每条对话都从候选里挑一个，失败就换下一个（最多试 `MAX_TRY` 个）。
    这比先给每个号查一遍模型目录便宜得多，也更像真人（真人也会换个模型再问）。
    """
    try:
        from admin.routers.models import _get_enabled_models, _get_free_models

        free = sorted(_get_free_models(db))
        enabled = sorted(_get_enabled_models(db))
    except Exception as exc:  # noqa: BLE001 - 选模型失败不该让任务挂掉
        _logger.debug("daily_activity 选模型失败: %s", exc)
        return []
    # 免费模型优先（零成本），再从启用列表里补几个常见的兜底
    pool = [m for m in free if m not in _DEAD_MODELS]
    for name in enabled:
        if name not in pool and name not in _DEAD_MODELS:
            pool.append(name)
    if not pool:
        return []
    # 已经验证能跑的模型排最前面（省掉反复试错 —— 试错本身就是可疑流量）
    proven = [m for m in _WORKING_MODELS if m in pool]
    rest = [m for m in pool if m not in proven]
    head = rest[:6]
    random.shuffle(head)
    return proven + head + rest[6:]


#: 一条对话最多试几个模型。再多就变成「批量扫模型」了，反而可疑。
MAX_TRY = 3

#: 进程内「已验证可用」的模型（成功过的排前面，优先复用）。
_WORKING_MODELS: list[str] = []

#: 进程内「已知不可用」的模型 —— 本轮别再浪费请求在它们身上。
_DEAD_MODELS: set[str] = set()

#: 空结果 ---- 这些错误说明「这个模型对该账号不存在」，值得换一个再试。
_MODEL_MISSING_HINTS = (
    "service info not found", "model not found", "model_not_found",
    "modelnotfound", "no such model", "invalid model", "unsupported model",
    "does not exist",
)


def _upstream_active_today(account, now: datetime | None = None) -> bool | None:
    """**上游**账本里，这个号今天有没有真实请求？

    返回 `True` / `False` / `None`（上游查不了，调用方退回本地）。

    ## 为什么优先问上游

    本地 `usage_logs` 只记录**经过我们网关**的请求。用户自己用客户端、
    或用别的工具发过请求，本地是不知道的 —— 而上游的用量表知道。
    只按本地判断会导致「今天其实已经用过了，我们还补发一条」，
    这反而是多余流量；也可能反过来漏判。

    上游接口：`POST /billing/meter/get-user-request-usage`
    （`backend.fetch_request_usage`，需要账号登录态）。
    """
    from admin.config import settings
    try:
        from admin import backend

        today = (now or datetime.utcnow()).date()
        start = "%s 00:00:00" % today.isoformat()
        end = "%s 23:59:59" % today.isoformat()
        with backend.AccountSession(account.auth_json) as sess:
            payload = sess.fetch_request_usage(start, end, 1, 1)
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, dict):
            total = data.get("total") or data.get("totalCount") or data.get("totalSize")
            if total is not None:
                return int(total) > 0
            rows = data.get("list") or data.get("records") or data.get("rows")
            if isinstance(rows, list):
                return len(rows) > 0
        # 结构不认识：当作查不了，让调用方退回本地
        return None
    except Exception as exc:  # noqa: BLE001 - 上游查不了就退回本地
        _logger.debug("daily_activity 查上游用量失败 account=%s: %s",
                      getattr(account, "id", "?"), exc)
        return None


def _local_active_today(db, account, now: datetime | None = None) -> bool:
    """本地账本里今天有没有请求（上游查不到时的兜底）。"""
    try:
        from admin.models import UsageLog

        today = (now or datetime.utcnow()).date()
        start = datetime(today.year, today.month, today.day)
        exists = (db.query(UsageLog.id)
                  .filter(UsageLog.account_id == int(account.id),
                          UsageLog.created_at >= start)
                  .first())
        return exists is not None
    except Exception as exc:  # noqa: BLE001
        _logger.debug("daily_activity 查本地用量失败 account=%s: %s",
                      getattr(account, "id", "?"), exc)
        return False


def _active_today(db, account, now: datetime | None = None) -> tuple[bool, str]:
    """今天是否已经活跃过 → `(是否活跃, 数据来源)`。

    数据来源：`upstream`（上游账本，最准） / `local`（本地账本，兜底）。
    """
    upstream = _upstream_active_today(account, now)
    if upstream is not None:
        return upstream, "upstream"
    return _local_active_today(db, account, now), "local"


def _models_for_account(db, account) -> list[str]:
    """**这个号最近真的成功用过哪些模型**（最可信的候选，排最前）。

    ## 为什么必须按账号取

    模型白名单是**全局**的，不按账号区分。实测同一个模型名：
      · 有的号能用；
      · 有的号回 `service info not found`（上游这个号没开）；
      · 国际版号打国内 backend 直接 401（region 不匹配）。

    与其盲试（试错本身就是可疑流量），不如直接问账本：
    「这个号上一次成功调用用的是什么模型」。真人用的就是它。
    """
    try:
        from admin.models import UsageLog

        rows = (db.query(UsageLog.model)
                .filter(UsageLog.account_id == int(account.id))
                .order_by(UsageLog.id.desc())
                .limit(40).all())
    except Exception as exc:  # noqa: BLE001 - 查账本失败就退回全局候选
        _logger.debug("daily_activity 查历史模型失败 account=%s: %s", account.id, exc)
        return []
    seen: list[str] = []
    for (model,) in rows:
        name = str(model or "").strip()
        if name and name not in seen:
            seen.append(name)
        if len(seen) >= 4:
            break
    return seen
    """**这个号最近真的成功用过哪些模型**（最可信的候选，排最前）。

    ## 为什么必须按账号取

    模型白名单是**全局**的，不按账号区分。实测同一个模型名：
      · 有的号能用；
      · 有的号回 `service info not found`（上游这个号没开）；
      · 国际版号打国内 backend 直接 401（region 不匹配）。

    与其盲试（试错本身就是可疑流量），不如直接问账本：
    「这个号上一次成功调用用的是什么模型」。真人用的就是它。
    """
    try:
        from admin.models import UsageLog

        rows = (db.query(UsageLog.model)
                .filter(UsageLog.account_id == int(account.id))
                .order_by(UsageLog.id.desc())
                .limit(40).all())
    except Exception as exc:  # noqa: BLE001 - 查账本失败就退回全局候选
        _logger.debug("daily_activity 查历史模型失败 account=%s: %s", account.id, exc)
        return []
    seen: list[str] = []
    for (model,) in rows:
        name = str(model or "").strip()
        if name and name not in seen:
            seen.append(name)
        if len(seen) >= 4:
            break
    return seen


def _is_foreign_credential(account) -> bool:
    """国际版凭证打国内 backend 必 401 —— 别拿它去撞。

    ## 判据不要自己编，直接复用唯一权威来源

    国际版的真实标识在 `auth_json["auth"]["domain"]`，取值为官方域名
    （默认平台客户端写入的 `*.ai` 域，判据在 `admin/post_login.py`）。
    顶层那个 `domain` 字段是**平台 realm**（cn/intl），不是这里要的东西。

    `admin/post_login.py::_resource_region_issue()` 已经把这个判断写死了，
    而且带维护过的注释（"国际版账号凭证无法访问当前国内接口"）。
    这里复用它 —— 两处各写一份，改一处漏一处就会漏掉一整批号。
    """
    try:
        from admin.post_login import _resource_region_issue

        return bool(_resource_region_issue(getattr(account, "auth_json", "") or ""))
    except Exception:  # noqa: BLE001 - 判不出来就当它不是国际版
        return False


def _remember_working(model: str) -> None:
    if model and model not in _WORKING_MODELS:
        _WORKING_MODELS.insert(0, model)
        del _WORKING_MODELS[8:]


def _remember_dead(model: str) -> None:
    if model:
        _DEAD_MODELS.add(model)


def _model_missing(result: dict) -> bool:
    """这次失败是不是「模型不存在」类（换个别家就能成）。"""
    text = str(result.get("message") or "").lower()
    return any(hint in text for hint in _MODEL_MISSING_HINTS)


def _pick_model(db) -> str | None:
    """兼容旧调用：取一个候选。"""
    candidates = _candidate_models(db)
    return candidates[0] if candidates else None


def _send_one(sess, model: str, prompt: str, max_tokens: int) -> dict:
    """发一条真实对话（流式，读完即断）。返回 `{ok, code, message}`。"""
    from admin import upstream_errors
    from admin.routers.proxy import _classify_error

    from admin.config import settings

    body = {
        "model": model,
        "stream": True,
        "max_tokens": max_tokens,
        # 用温度让每次回复不一样；真人不会每次得到一模一样的结果
        "temperature": round(random.uniform(0.6, 0.95), 2),
        "messages": [{"role": "user", "content": prompt}],
    }
    try:
        from admin import upstream_identity as identity

        extra = identity.identity_headers(identity.purpose_for("chat"))
        extra.update(identity.trace_headers())
    except Exception:  # noqa: BLE001
        extra = {}

    headers = sess.get_headers(extra=extra)
    with sess.http_client(timeout=45) as client:
        with client.stream("POST", f"{settings.BACKEND}/v2/chat/completions",
                           headers=headers, json=body) as response:
            if response.status_code >= 400:
                raw = response.read()
                text = (raw[:65536].decode(errors="ignore")
                        if isinstance(raw, bytes) else str(raw)[:65536])
                info = upstream_errors.error_info(text, response.status_code)
                return {"ok": False, "code": info["code"],
                        "message": (info["message"] or text)[:200],
                        "kind": _classify_error(response.status_code, text),
                        "status": response.status_code}
            # 读一小段就够：我们要的是「这条对话真的发生了」，
            # 不是完整答案（多读只会多烧 token 和带宽）。
            chunk = next(response.iter_bytes(2048), b"")
    text = chunk.decode(errors="ignore") if isinstance(chunk, bytes) else str(chunk)
    if text.strip():
        kind = _classify_error(200, text)
        # 200 里包着错误信封（SSE 内 error 事件）也要如实报
        if kind not in ("client", "transport"):
            info = upstream_errors.error_info(text, 200)
            return {"ok": False, "code": info["code"],
                    "message": (info["message"] or text)[:200],
                    "kind": kind, "status": 200}
    return {"ok": True, "code": None, "message": "", "kind": "", "status": 200}


def run_daily_activity(db, *, limit: int | None = None,
                       only_account_ids: list[int] | None = None) -> dict:
    """给每个 active 账号发一条自然对话（**串行 + 抖动**）。

    返回 `{"ok", "done", "skipped", "failed", "detail", "summary", "elapsed_s"}`。

    设计取舍：
      · **串行**：这是本模块最重要的一条。100 个号并发打招呼，比不发更糟。
      · **不抢前台**：只看 `status == "active"` 且不在冷却里的号。
      · **失败不重试**：一次失败就记下原因，明天再说 —— 重试会放大机器特征。
    """
    from admin.models import Account
    from admin import backend
    from admin.platform import registry as platform_registry

    if not _enabled():
        return {"ok": True, "done": 0, "skipped": 0, "failed": 0,
                "detail": [], "summary": "每日活跃已关闭（ADMIN_DAILY_ACTIVITY=0）"}

    started = time.perf_counter()
    now = datetime.utcnow()

    query = db.query(Account).filter(
        Account.platform == platform_registry.DEFAULT_PLATFORM,
        Account.status == "active")
    if only_account_ids:
        query = query.filter(Account.id.in_([int(i) for i in only_account_ids]))
    accounts = query.order_by(Account.id.asc()).all()
    if limit:
        accounts = accounts[:int(limit)]

    model = _pick_model(db)
    candidates = _candidate_models(db)
    if not candidates:
        return {"ok": False, "done": 0, "skipped": 0, "failed": 0,
                "detail": [], "summary": "没有可用模型，跳过本次每日活跃"}

    probability = _probability()
    gap = _gap_seconds()
    max_tokens = _max_tokens()
    done, skipped, failed = 0, 0, 0
    detail: list[dict] = []

    # 启动前随机等一下：定时任务如果都在同一秒开跑，本身就是机器特征
    humanize.human_sleep(humanize.human_start_delay(12.0), floor=0.0)

    for index, account in enumerate(accounts, 1):
        item = {"account_id": account.id, "account": account.name or str(account.id),
                "ok": False, "status": "error", "reason": ""}
        # 国际版凭证打国内 backend 必 401：不是被封，是根本不该发。
        # 这类号直接跳过 —— 否则每天都会造一批无意义的 401 流量。
        if _is_foreign_credential(account):
            item.update(status="skipped", reason="国际版凭证，与当前后端 region 不匹配，已跳过")
            skipped += 1
            detail.append(item)
            continue
        # 冷却 / 熔断中的号不要去碰（它们正在恢复期）
        cooling = any(getattr(account, field, None) and getattr(account, field) > now
                      for field in ("cool_until", "breaker_until", "degrade_until"))
        if cooling:
            item.update(status="skipped", reason="冷却中，本次不打扰")
            skipped += 1
            detail.append(item)
            continue
        # 不是每天必做：概率性跳过，像真人偶尔不开机
        if not humanize.human_probability(probability):
            item.update(status="skipped", reason="今天没打开（概率跳过）")
            skipped += 1
            detail.append(item)
            continue

        prompt = random.choice(PROMPTS)
        # 先问「今天到底用过没有」：**上游账本优先**，查不到退回本地。
        # 已经活跃过就不补发 —— 目的是「不要长期不用」，不是「每天刷一次」。
        active, source = _active_today(db, account, now)
        if active:
            item.update(status="skipped",
                        reason="今天已活跃（来源：%s），不补发" %
                               ("上游账本" if source == "upstream" else "本地账本"))
            skipped += 1
            detail.append(item)
            continue
        # 候选顺序：这个号**历史上真的用过**的模型 > 全局候选。
        # 前者几乎必然可用，后者只是兜底。
        history = _models_for_account(db, account)
        order = [m for m in history if m not in _DEAD_MODELS]
        for candidate in candidates:
            if candidate not in order:
                order.append(candidate)
        result = {"ok": False, "kind": "model_unavailable", "code": None,
                  "message": "没有可用的模型", "status": 0}
        used_model = ""
        for candidate in order[:MAX_TRY]:
            used_model = candidate
            try:
                sess = backend.AccountSession(account.auth_json)
                try:
                    result = _send_one(
                        sess, candidate, prompt,
                        random.randint(max(8, max_tokens // 2), max_tokens))
                finally:
                    try:
                        sess.close()
                    except Exception:  # noqa: BLE001
                        pass
            except Exception as exc:  # noqa: BLE001 - 单号异常不拖垮整批
                result = {"ok": False, "kind": "transport",
                          "message": ("%s: %s" % (type(exc).__name__, exc))[:200],
                          "code": None, "status": 0}
            if result.get("ok"):
                break
            # 模型不存在 → 记下来，换下一个
            if _model_missing(result):
                _remember_dead(candidate)
                continue
            # 其它错误（401 / 额度 / 风控）换模型也没用，直接停
            break

        if result.get("ok"):
            _remember_working(used_model)
            account.last_used_at = datetime.utcnow()
            item.update(status="done", ok=True, reason="", model=used_model)
            done += 1
        else:
            item.update(status="error", model=used_model,
                        reason=(result.get("message") or "未知错误")[:160],
                        code=result.get("code"), kind=result.get("kind"))
            failed += 1
        try:
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
        detail.append(item)

        # 最后一个号之后不要再睡
        if index < len(accounts):
            humanize.human_sleep(gap, floor=0.4, ceil=gap * 6)

    elapsed = round(time.perf_counter() - started, 1)
    summary = ("每日活跃：完成 %d 个，跳过 %d 个，失败 %d 个（候选模型 %d 个，用时 %ss）"
               % (done, skipped, failed, len(candidates), elapsed))
    _logger.info("%s", summary)
    return {"ok": failed == 0, "done": done, "skipped": skipped, "failed": failed,
            "model": model, "detail": detail[:60], "elapsed_s": elapsed,
            "summary": summary,
            "note": ("串行 + 抖动执行：目的是让每个号每天有真实对话请求，"
                     "避免「只刷管理接口、从不发对话」的养号特征")}