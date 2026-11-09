"""轻量后台定时任务调度器（无第三方依赖）。

用守护线程每 15s 轮询 schedules 表，到点（next_run_at <= now）的任务就执行，
执行完更新 last_run_at / next_run_at / last_result。

支持的任务：
  - refresh_balances：遍历 active 账号刷新余额（统计平台总积分）
  - sync_models：从后端拉取最新模型列表并 upsert 倍率
"""
import json
import logging
import threading
import time
from datetime import datetime, timedelta

from admin.db import SessionLocal
from admin.models import Schedule
from admin.config import settings
from admin import maintenance
from sqlalchemy import func, or_
from admin.schedule_timing import timing_of, next_run_at
from collections import Counter

_logger = logging.getLogger(__name__)

#: last_result 是 TEXT，存得下完整 JSON；但仍给一个上限，
#: 防止某个任务的明细异常膨胀把这一行撑爆。
_RESULT_LIMIT = 20000


def _account_label(a) -> str:
    """给账号起个**人认得出**的短标签。

    后台只显示 `acc12` 的话，会看到「acc12 没领成功」还得自己去号池里
    对 id，等于没说。优先用账号名（多数是手机号或昵称），没有才退回 id。
    手机号做脱敏：中间四位打码，既够辨认又不把完整号码写进结果里
    （结果会被截图、也会随日志流转）。
    """
    name = str(getattr(a, "name", "") or "").strip()
    if name:
        if len(name) == 11 and name.isdigit():
            return f"{name[:3]}****{name[-4:]}"
        return name[:20]
    return f"账号#{getattr(a, 'id', '?')}"


def _short_error(e) -> str:
    """把异常压成一句可读原因（去掉类名前缀与多余空白）。"""
    msg = str(e).strip() or e.__class__.__name__
    return msg[:160]


#: 结果里可能出现的「逐项明细」字段名。
#:
#: ⚠️ 各平台/各任务用的名字**不统一**：WorkBuddy 用 `detail`，Loomy 原来用
#: `items`，还有 `accounts` / `rows` / `failed`。原来的裁剪逻辑只认 `detail`，
#: 于是 Loomy 的 100 条明细完全砍不掉，最后走到「按字节硬截断」——
#: 截出来的是**非法 JSON**，前端 `JSON.parse` 失败，只能把整段原文糊出来
#: （就是「结果样式有问题 / 一整坨 json」）。这里把所有可能的字段都列上，
#: 按体积从大到小砍，保证结构永远完整。
_DETAIL_FIELDS = ("detail", "items", "accounts", "rows", "results", "failed")


def _slim_detail(entry) -> object:
    """把一条明细压缩到「前端画表只需要的那几个标量」。

    明细里最占地方的是嵌套结构（上游原始响应、凭证片段）。
    截断时先把这些去掉，往往一条就从几 KB 降到几百字节，
    这样能保住**更多条数**，而不是一刀砍到 0 条。
    """
    if not isinstance(entry, dict):
        return str(entry)[:200]
    keep = {}
    for key, value in entry.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            keep[key] = value[:200] if isinstance(value, str) else value
        elif isinstance(value, list) and len(value) <= 6:
            keep[key] = value
    return keep


def _dump_result(result: dict) -> str:
    """序列化任务结果。

    这里**绝不能截断 JSON 字符串**：以前是 `json.dumps(...)[:500]`，
    一截断就成了非法 JSON，前端 `JSON.parse` 直接失败，看到的是
    一段被腰斩的原始文本 —— 正是「别只显示一堆 json 数据」的成因之一。

    裁剪顺序（每一步都重新序列化，命中上限就停）：
      1. 明细数组去嵌套 → 逐步减少条数（50 / 30 / 10 / 0）；
      2. 仍然超长 → 丢掉全部明细，只留计数与 summary；
      3. 最后才动字节（正常情况下永远走不到这一步）。
    """
    try:
        text = json.dumps(result, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        try:
            text = json.dumps(result, ensure_ascii=False, default=str)
        except Exception:  # noqa: BLE001
            return json.dumps({"ok": False, "error": "结果无法序列化"},
                              ensure_ascii=False)
    if len(text) <= _RESULT_LIMIT:
        return text

    # ---- 1) 逐个明细字段压缩 + 减条 ----
    slim = dict(result)
    for field in _DETAIL_FIELDS:
        value = slim.get(field)
        if isinstance(value, list) and value:
            slim[field] = [_slim_detail(item) for item in value]
    for keep in (50, 30, 10, 0):
        candidate = dict(slim)
        for field in _DETAIL_FIELDS:
            value = candidate.get(field)
            if isinstance(value, list):
                candidate[field] = value[:keep] if keep else []
                if keep and len(value) > keep:
                    candidate["detail_truncated"] = True
        try:
            text = json.dumps(candidate, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            continue
        if len(text) <= _RESULT_LIMIT:
            return text
        slim = candidate

    # ---- 2) 丢掉全部明细，只留标量与 summary ----
    head = {key: value for key, value in slim.items()
            if not isinstance(value, list)}
    head["detail_truncated"] = True
    try:
        text = json.dumps(head, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = json.dumps({"ok": False, "error": "结果过大且无法序列化"},
                          ensure_ascii=False)
    if len(text) <= _RESULT_LIMIT:
        return text

    # ---- 3) 极端兜底：保住 JSON 结构，只留一句话 ----
    return json.dumps({
        "ok": head.get("ok", True),
        "summary": str(head.get("summary") or "")[:400],
        "detail_truncated": True,
        "note": "结果过大，已省略明细",
    }, ensure_ascii=False)



_current = threading.local()


def _current_schedule():
    """当前正在执行的 Schedule（线程内）。

    有些老 runner 的签名是 `()`（`run_growth_tasks` / `run_refresh_growth_tasks`），
    拿不到 schedule 就不知道该跑哪个平台的账号，历史上它们干脆全表跑一遍，
    结果把 Trae/Qoder/Loomy 的号也塞进 WorkBuddy 专属任务里。
    调度器在调用 runner 前把 schedule 放到线程变量里，这些函数即可读取。
    """
    return getattr(_current, "schedule", None)


def _platform_of(schedule) -> str:
    """该定时任务作用于哪个平台。老数据（platform 为空）按 workbuddy 处理。"""
    value = str(getattr(schedule, "platform", "") or "").strip()
    return value or "workbuddy"


def _active_accounts(db, schedule=None, *, platform_only: bool = True):
    """取该任务应当处理的账号。

    **平台隔离是必须的**：任务体都是 WorkBuddy 专属的（积分包、签到、
    成长任务、猫猫旅行）。若不加平台条件，`refresh_balances` 会把 Trae
    的号也拉一遍 —— 那些号没有 WorkBuddy 的积分接口，只会稳定报错，
    刷满日志并让健康度统计失真。

    `platform_only=False` 用于确实与平台无关的任务（例如同步 Key 状态）。
    """
    from admin.models import Account
    query = db.query(Account).filter(Account.status == "active")
    if platform_only:
        query = query.filter(Account.platform == _platform_of(schedule))
    return query.all()


def run_refresh_balances(db, schedule=None) -> dict:
    """刷新余额快照 —— **只刷 stale 的，按代理槽并发，有时间预算**。

    ## 为什么重写（2026-11-06 用户实测）

    > 如果 1 并发查 100 个要 10 分钟，然后等下次余额刷新又是 10 分钟。
    > 余额刷新会卡死，无限循环。

    原实现三个问题叠在一起：

      1. `_active_accounts(db, schedule)` **一次性读全表**并常驻内存
         （每个 ORM 对象还带几百 KB 的 auth_json）—— 几千个号直接卡住；
      2. 每轮都**全量重扫**，不管快照是不是刚更新过 —— 于是永远刷不完，
         下一轮又从头开始，看起来就是「无限循环」；
      3. 串行 + `account_slot` 的全局单飞锁，1 个 IP 上一个一个来。

    现在：

      · **只刷 stale**：`balance_synced_at` 落后 `stale_minutes`（默认 60）
        或从来没刷过的号。刚刷过的直接跳过 —— 池子再大也不会重复劳动；
      · **按主键分批**（`iter_account_batches`），不把号池读进内存；
      · **按代理槽并发**：并发上限 = Σ(节点 max_concurrency)，
        一个节点同时只跑它自己能承受的数量；
      · **时间预算**（默认 8 分钟）：到点就停，剩下的下一轮接着刷。
        **不追求一轮刷完** —— 这才是「不卡死」的正解。
    """
    from admin.models import Account
    from admin.platform import shared as platform_shared
    from admin import proxy_pool
    from admin.routers import accounts as acc_router

    started = time.monotonic()

    # ---- 参数：任务显式配置 > 全局默认 > 环境变量 > 内置默认 ----
    # 全局默认来自「定时任务 → 全局出口与并发」（SystemSetting 单键 JSON，
    # 2026-11-07 新增）：给所有没单独配置的任务统一出口/节奏，单个任务
    # 填了值仍然以任务自己的为准。
    gp = _schedule_global_proxy(db)
    stale_minutes = (int(getattr(schedule, "stale_minutes", 0) or 0)
                     or int(gp.get("stale_minutes") or 0)
                     or _env_int("ADMIN_BALANCE_STALE_MINUTES", 60))
    budget_minutes = (int(getattr(schedule, "time_budget_minutes", 0) or 0)
                      or int(gp.get("time_budget_minutes") or 0)
                      or _env_int("ADMIN_BALANCE_BUDGET_MINUTES", 8))
    pool_name = (str(getattr(schedule, "proxy_pool", "") or "")
                 or str(gp.get("proxy_pool") or ""))
    configured = (int(getattr(schedule, "concurrency", 0) or 0)
                  or int(gp.get("concurrency") or 0))

    slots = proxy_pool.slots(db, pool_name)
    auto = len(slots) if slots else _env_int("ADMIN_BALANCE_CONCURRENCY", 4)
    workers = configured or auto
    workers = max(1, min(64, workers))

    deadline = started + budget_minutes * 60
    cutoff = datetime.utcnow() - timedelta(minutes=max(1, stale_minutes))

    storage = (getattr(schedule, "platform", "") or "workbuddy").strip() or "workbuddy"
    counters = {"ok": 0, "fail": 0, "skipped": 0, "stale": 0, "budget_hit": False}
    failed_names: list[str] = []
    skip_reasons = Counter()

    # 槽位分配：每个 worker 线程按下标拿一个出口，销毁前不会变
    # （同一线程内的所有请求走同一个 IP，比每条请求换 IP 更像真人）
    def slot_for(index: int) -> str:
        if not slots:
            return ""
        return slots[index % len(slots)]["url"]

    def worker(session, account):
        nonlocal counters
        if time.monotonic() > deadline:
            counters["budget_hit"] = True
            return {"account_id": account.id, "ok": True, "skipped": "budget"}
        reason = maintenance._reason(account)
        if reason:
            return {"account_id": account.id, "ok": True, "skipped": reason}
        # 槽位按账号 id 取模：同一个号固定走同一个出口（避免「同一个号
        # 每次换 IP」这种比不用代理更可疑的形状）
        url = slots[account.id % len(slots)]["url"] if slots else ""
        ok = acc_router._refresh_balance(
            account, with_expiry=True, proxy=url,
            on_error=lambda error: maintenance.note_failure(session, account, error))
        try:
            session.commit()
        except Exception:  # noqa: BLE001
            session.rollback()
        endpoint_id = slots[account.id % len(slots)]["endpoint_id"] if slots else None
        if ok:
            proxy_pool.mark_ok(session, endpoint_id)
        return {"account_id": account.id, "ok": ok,
                "account": _account_label(account),
                "error": "" if ok else (getattr(account, "last_err_msg", "") or "")[:120],
                "endpoint_id": endpoint_id}

    # ---- 只捞 stale 的 id，按批处理 ----
    seen = 0
    for batch_ids in platform_shared.iter_account_batches(
            db, storage=storage, status="active",
            batch=max(workers * 4, 40)):
        if time.monotonic() > deadline:
            counters["budget_hit"] = True
            break
        # 过滤：只要 stale 的（`balance_synced_at` 空或早于 cutoff）。
        #
        # 顺便在 SQL 层就排掉「冷却 / 熔断 / 降权中」的号 —— 它们进去也是
        # 被 `_reason()` 跳过，白白占一轮的 stale 配额。池子上千时，
        # 光这一条就能把每轮的无效往返砍掉一大块。
        now_utc = datetime.utcnow()
        stale_ids = [int(row[0]) for row in
                     (db.query(Account.id)
                      .filter(Account.id.in_(batch_ids),
                              Account.status == "active",
                              or_(Account.balance_synced_at.is_(None),
                                  Account.balance_synced_at < cutoff),
                              or_(Account.cool_until.is_(None),
                                  Account.cool_until <= now_utc),
                              or_(Account.breaker_until.is_(None),
                                  Account.breaker_until <= now_utc))
                      .order_by(Account.id.asc()).all())]
        if not stale_ids:
            continue
        counters["stale"] += len(stale_ids)
        seen += len(stale_ids)
        rows = platform_shared.run_account_batch(
            stale_ids, worker, concurrency=workers, label="refresh-balances")
        for row in rows:
            if row.get("skipped"):
                counters["skipped"] += 1
                skip_reasons[str(row["skipped"])] += 1
                continue
            if row.get("ok"):
                counters["ok"] += 1
            else:
                counters["fail"] += 1
                if len(failed_names) < 10:
                    failed_names.append(row.get("account") or str(row.get("account_id")))

    elapsed = round(time.monotonic() - started, 1)
    totals = db.query(
        func.coalesce(func.sum(Account.credits_expiring), 0),
        func.coalesce(func.sum(Account.credits_expiring_soon), 0),
    ).filter(Account.status == "active").one()

    summary = ("刷新成功 %d 个" % counters["ok"])
    if counters["stale"]:
        summary += "（本轮 stale %d 个" % counters["stale"]
        if counters["budget_hit"]:
            summary += "，到 %d 分钟预算，剩余下轮继续" % budget_minutes
        summary += "）"
    if counters["skipped"]:
        summary += "，%d 个跳过" % counters["skipped"]
    if counters["fail"]:
        summary += "，%d 个失败：%s" % (counters["fail"], "、".join(failed_names[:3]))
    summary += " · %s" % ("%d 个出口" % len(slots) if slots else "直连")
    summary += " · 并发 %d · 用时 %ss" % (workers, elapsed)

    return {
        "task": "refresh_balances", "ok": True,
        "refreshed": counters["ok"], "failed": counters["fail"],
        "skipped": counters["skipped"], "skip_reasons": dict(skip_reasons),
        "stale_pending": counters["stale"],
        "budget_hit": counters["budget_hit"],
        "concurrency": workers, "proxy_slots": len(slots),
        "proxy_pool": pool_name or "直连",
        "credits_expiring": int(totals[0] or 0),
        "credits_expiring_soon": int(totals[1] or 0),
        "failed_accounts": failed_names,
        "elapsed_s": elapsed,
        "summary": summary,
    }


def _env_int(name: str, default: int) -> int:
    import os

    try:
        return int(os.getenv(name, default) or default)
    except (TypeError, ValueError):
        return default


def _schedule_global_proxy(db) -> dict:
    """「全局出口与并发」默认值（SystemSetting 单键 JSON）。

    读不到 / 坏数据一律返回全零 dict —— 零值在调用侧的含义是
    「这项没配，继续往下落（环境变量 / 内置默认）」。
    任务执行时才查库（一条主键查询，不在请求热路径上），改完立即生效。
    """
    from admin.models import SystemSetting

    try:
        row = db.query(SystemSetting).filter(
            SystemSetting.key == "schedule:global_proxy").first()
        if not row or not row.value:
            return {}
        data = json.loads(row.value)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 - 配置读不出来不能挡任务
        return {}


def run_refresh_credits(db, schedule=None) -> dict:
    """refresh_credits（从 run_task 抽出，行为不变）。"""
    from admin.routers import accounts as acc_router
    from admin.models import Account
    ok = fail = 0
    skipped = Counter()
    failed_names: list[str] = []
    # 逐号重采积分包明细 → 写回到期快照。选号的「先用快过期的」这条
    # 规则完全建立在这份快照上，所以它必须按自己的节奏刷新，
    # 而不是搭在余额刷新里「顺便做」—— 余额刷新频率高、开销敏感，
    # 一旦为了省钱把 with_expiry 关掉，调度依据就悄悄归零了
    # （这个 bug 真实发生过）。
    for a in _active_accounts(db, schedule):
        with maintenance.account_slot(a, db) as reason:
            if reason:
                skipped[reason] += 1
                continue
            try:
                sess = None
                try:
                    from admin.backend import AccountSession
                    sess = AccountSession(a.auth_json)
                    packages = sess.fetch_credit_details()
                    a.auth_json = sess.updated_json()
                finally:
                    if sess is not None:
                        try:
                            sess.close()
                        except Exception:
                            pass
                acc_router._sync_credit_snapshot(a, packages)
                collection = getattr(packages, "metadata", None)
                if collection and not collection["complete"]:
                    fail += 1
                    failed_names.append(_account_label(a))
                else:
                    ok += 1
            except Exception as error:
                maintenance.note_failure(db, a, error)
                fail += 1
                failed_names.append(_account_label(a))
            db.commit()
    rows = _active_accounts(db, schedule)
    expiring_soon = sum(int(a.credits_expiring_soon or 0) for a in rows)
    soonest = min((a.credits_soonest_expire_at for a in rows
                   if a.credits_soonest_expire_at), default=None)
    soonest_txt = ""
    if soonest is not None:
        import datetime as _dt
        left = (soonest - _dt.datetime.utcnow()).total_seconds() / 86400.0
        soonest_txt = (f"，最近一个 {int(left)} 天后到期" if left >= 1
                       else "，最近一个 24 小时内到期")
    return {"task": "refresh_credits", "ok": True, "refreshed": ok, "failed": fail,
            "skipped": sum(skipped.values()), "skip_reasons": dict(skipped),
            "credits_expiring_soon": expiring_soon,
            "failed_accounts": failed_names[:10],
            "summary": (f"已更新 {ok} 个账号的积分到期快照"
                        + (f"，7 天内到期 {expiring_soon} 积分" if expiring_soon else "")
                        + soonest_txt
                        + (f"，{fail} 个失败：{'、'.join(failed_names[:3])}"
                           if fail else "")
                        + (f"，{sum(skipped.values())} 个因保护或占用跳过" if skipped else ""))}


def run_sync_models(db, schedule=None) -> dict:
    """sync_models（从 run_task 抽出，行为不变）。"""
    from admin.routers import models as models_router
    res = models_router._do_sync_models(db)
    if isinstance(res, dict) and "summary" not in res:
        res["summary"] = (f"新增 {res.get('added', 0)} 个、"
                          f"更新 {res.get('updated', 0)} 个，"
                          f"共 {res.get('total_in_db', 0)} 个模型")
    return res


#: 任务名 -> 执行函数。
#:
#: **这是给平台模块导出的注册表**：平台通过 `Gateway.task_runners()`
#: 声明自己支持哪些任务，调度层据此分派，不再硬编码任务名。
#:
#: 迁移期先由 WorkBuddy 在这里登记它的全部任务；Trae 的任务
#: （`trae_checkin`）由 `admin/trae/__init__.py` 声明。
#: 最终形态是「每个平台自己维护一张表」，本文件只保留分派逻辑。
TASK_RUNNERS = {
    "refresh_balances": None,      # 下方函数定义后填入
    "refresh_credits": None,
    "sync_models": None,
    "daily_checkin": None,
    "refresh_growth_tasks": None,
    "run_growth_tasks": None,
    "run_growth_plan": None,
    "keepalive_tokens": None,
    "ban_recheck": None,
    "daily_activity": None,
}


def _fill_task_runners() -> None:
    """把已定义的函数填进 `TASK_RUNNERS`（幂等）。

    这里的关键是**把签名统一成 `(db, schedule)`**：现有 runner 的签名
    各不相同（有的只要 db、有的不接受参数、`run_growth_plan` 还有
    关键字参数）。统一包装后，平台模块只需 `{任务名: 函数}`，
    调度层永远按 `(db, schedule)` 调用 —— 调用方不必记各家的签名。

    用延迟填表而不是直接写引用，是为了让「表」与「实现」解耦：
    未来平台把自己的 runner 注册进来时，不需要 import 本模块的函数。
    """
    from admin.ban_recheck import run_ban_recheck
    from admin.growth_plan import run_growth_plan

    mapping = {
        "refresh_balances": run_refresh_balances,
        "refresh_credits": run_refresh_credits,
        "sync_models": run_sync_models,
        "daily_checkin": run_daily_checkin,
        "refresh_growth_tasks": run_refresh_growth_tasks,
        "run_growth_tasks": run_growth_tasks,
        "run_growth_plan": run_growth_plan,
        "keepalive_tokens": run_keepalive_tokens,
        "ban_recheck": run_ban_recheck,
        # 每日让每个号真的说一句话：只刷 token 不发对话的号，
        # 在上游眼里就是「机器在养号」，比不发请求更容易吃 11140。
        "daily_activity": _run_daily_activity,
    }
    for name, fn in mapping.items():
        if TASK_RUNNERS.get(name) is None:
            TASK_RUNNERS[name] = _adapt_runner(fn)


def _run_daily_activity(db, schedule=None):
    """调度层包装：把 `run_daily_activity(db)` 的签名对齐成 `(db, schedule)`。"""
    from admin.daily_activity import run_daily_activity

    return run_daily_activity(db)


def _adapt_runner(fn):
    """把任意签名的 runner 适配成 `(db, schedule) -> dict`。

    **已经适配过的直接返回**：WorkBuddy 的 `task_runners()` 把
    `scheduler.TASK_RUNNERS` 整表再交回来，于是同一个 runner 会被适配两次。
    第二次 `inspect.signature` 顺着 `__wrapped__` 看到原始函数的空签名
    （`run_growth_tasks()`），得出「不需要 db」的结论，调用时不传参 ——
    表现就是「runner() missing 1 required positional argument: 'db'」。

    `run_growth_plan` 有额外关键字参数，直接透传 `db` 即可（其余都有默认值）；
    不接受参数的（`run_growth_tasks` 等）要挡住多余的实参。
    """
    import inspect

    if getattr(fn, "_wb_adapted", False):
        return fn
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        params = {}

    names = list(params)
    takes_db = bool(names) and names[0] in ("db", "session")
    # 是否接受第二个位置参数（schedule）
    positional = [p for p in params.values()
                  if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    takes_schedule = len(positional) >= 2 or any(
        p.kind == p.VAR_POSITIONAL for p in params.values())

    def runner(db, schedule=None):
        if takes_db and takes_schedule:
            return fn(db, schedule)
        if takes_db:
            return fn(db)
        return fn()

    runner.__wrapped__ = fn
    runner.__name__ = getattr(fn, "__name__", "runner")
    runner._wb_adapted = True
    return runner


def run_task(task: str, db, schedule: "Schedule | None" = None) -> dict:
    """执行某个任务。

    **调度层不认识任何具体任务名** —— 全部通过 `TASK_RUNNERS` 分派。
    要加新任务，平台模块调 `register_task(名字, 函数)` 即可，
    本函数一行都不用改（见 `admin/platform/gateway.py` 的职责边界）。
    """
    from admin.pool import POOL
    if POOL.waf.active():
        return {"task": task, "ok": True, "deferred": True,
                "retry_after": max(1, POOL.waf.remaining()),
                "summary": "出口保护期间暂停后台上游任务"}

    # Trae 的任务由它自己的模块声明（见 admin/trae/__init__.py）；
    # 这里先查平台注册的 runner，再查全局表 —— 平台专属优先，
    # 这样同名任务也能被平台覆盖。
    runner = _platform_runner(task, schedule)
    if runner is None:
        runner = TASK_RUNNERS.get(task)
    if runner is None:
        return {"task": task, "ok": False,
                "error": "该平台未注册此任务：%s" % task,
                "summary": "未知任务类型"}
    previous = getattr(_current, "schedule", None)
    _current.schedule = schedule
    try:
        return runner(db, schedule)
    finally:
        _current.schedule = previous


def _platform_runner(task: str, schedule):
    """从平台网关取任务执行器；平台没登记就是 None。

    ## 必须用 `load_platform_gateway`

    `PlatformSpec` 上只有字符串字段 `platform_gateway`（形如
    `"pkg.mod:Attr"`），没有 `load_gateway()` 方法。
    原来这里写了 `spec.load_gateway()`，AttributeError 被 except 吞掉，
    于是**平台声明的任务一次都不会被执行**（Trae/Qoder 的签到就是这样失效的）。

    ## 为什么要适配 realm

    `load_platform_gateway(spec, spec.realm)` 会按区域实例化
    （Trae/Qoder 都是两个区共用一套实现）。
    """
    try:
        from admin.platform import registry

        platform = _platform_of(schedule)
        if not platform:
            return None
        realm_hint = str(getattr(schedule, "realm", "") or "").strip().lower()
        spec = registry.get(platform)
        # 「platform 是区级 id 还是落库平台名」不能只看 registry 命中：
        # Qoder 中国版的对外 id 恰好就是落库名 `qoder`（registry 直接命中
        # cn spec），只看命中会把双区任务锁死在中国版。**storage 等于
        # platform 本身 = 落库平台名**，才走多区/通配分支。
        if spec is not None and spec.storage() != platform:
            # 任务绑的是**区级对外 id**（qoder-intl / trae-cn）：只跑这个区
            gateway = registry.load_platform_gateway(spec, spec.realm)
        else:
            candidates = [sp for sp in registry.all_platforms()
                          if sp.storage() == platform]
            if spec is None and not candidates:
                return None
            spec = next((sp for sp in candidates if sp.realm == realm_hint), None)
            if spec is not None:
                gateway = registry.load_platform_gateway(spec, spec.realm)
            else:
                # **多区落库平台 + 任务未绑区（2026-11-07 修复）**：
                # 此前恒落默认区（qoder→cn），国际版账号的签到/保活整批
                # 漏跑（Qoder 9 个 intl 号每次 refreshed=0 就是这么来的）。
                # 不带区实例化，让平台 runner 按**账号自己的区**跑。
                if spec is None:
                    spec = candidates[0]
                gateway = registry.load_platform_gateway(spec, "")
        if gateway is None:
            _logger.debug("平台 %s 没有管理契约，无法取任务", platform)
            return None
        runners = gateway.task_runners() or {}
        runner = runners.get(task)
        if runner is None:
            _logger.debug("平台 %s 未登记任务 %s", platform, task)
            return None
        # 统一签名：平台给的是 (db, schedule[, realm])，这里收成 (db, schedule)
        return _adapt_runner(runner)
    except Exception:  # noqa: BLE001 - 平台加载失败不能挡住其它任务
        _logger.debug("平台 runner 解析失败 task=%s", task, exc_info=True)
        return None


def register_task(name: str, runner) -> None:
    """供平台模块登记自己的任务执行器。

    签名约定 `(db, schedule) -> dict`。若平台给的函数签名不同，
    这里自动适配（见 `_adapt_runner`）。
    """
    TASK_RUNNERS[name] = _adapt_runner(runner)


def _bootstrap_runners() -> None:
    """模块加载时把内建 runner 装进表。

    必须在**所有** runner 函数定义之后调用：`_fill_task_runners`
    引用的是模块级函数对象，此时它们才都存在。
    """
    try:
        _fill_task_runners()
    except Exception:  # noqa: BLE001 - 装表失败不该挡住模块导入
        _logger.exception("任务 runner 装配失败")


#: token 保活：连续多少次「session 已死」才把账号禁用。
#: 与 proxy 的 SESSION_DEAD_THRESHOLD 同源 —— 一次 12153 只说明这次刷新失败，
#: 可能是上游抖动；连续失败才说明这个号的登录态真的废了，需要人工重登。
_KEEPALIVE_DEAD_THRESHOLD = 3


#: 刷新/鉴权返回里代表「服务端会话已被删除」的标记 —— OAuth 标准语义，属**终态**。
#: 与文案式的 12153 抖动区分开：命中这些就没有重试价值，应立即禁用并提示重登。
_AUTH_DEAD_MARKERS = (
    "invalid_grant",
    "offline user session not found",
    "refresh token failed",
    "session not found",
)


class TrustedAuthFailure(RuntimeError):
    """Both trusted authenticated probes rejected the current credential."""

    trusted_auth_failure = True

    def __init__(self, errors):
        self.errors = list(errors)
        from admin.routers.proxy import _extract_http_status
        self.http_status = next(
            (_extract_http_status(str(error)) for error in self.errors
             if _extract_http_status(str(error)) in (401, 403)),
            0,
        )
        self.raw_body = str(self.errors[0])[:65536] if self.errors else ""
        super().__init__("trusted authentication probes both failed: " + self.raw_body)


def _access_token_of(sess) -> str:
    """从 AccountSession 里取当前 accessToken（用于判断是否真的刷新了）。"""
    try:
        return ((sess.cm._session().get("auth") or {}).get("accessToken")) or ""
    except Exception:
        return ""


def _access_token_alive(sess) -> bool:
    """Check trusted authenticated endpoints after refresh fails.

    The model-list route can reject a shared request shape while the bearer
    session remains valid.  Balance and account-profile routes are the
    authoritative probes for this decision; either one succeeding is enough.
    """
    try:
        sess.fetch_balance()
        return True
    except Exception:
        try:
            sess.fetch_account_profile()
            return True
        except Exception:
            return False


def _trusted_token_probe(sess) -> None:
    """Raise only when both trusted authenticated probes fail."""
    errors = []
    try:
        sess.fetch_balance()
        return
    except Exception as first:
        errors.append(first)
    try:
        sess.fetch_account_profile()
        return
    except Exception as second:
        errors.append(second)
    from admin.routers.proxy import _extract_http_status
    statuses = [_extract_http_status(str(error)) for error in errors]
    if statuses and all(status in (401, 403) for status in statuses):
        raise TrustedAuthFailure(errors)
    raise errors[0]


def run_keepalive_tokens(db, schedule=None) -> dict:
    """定时刷新所有活跃账号的 token，保持登录态存活（「保活」）。

    为什么需要：上游的登录态有绝对有效期。如果一个账号长期没有请求，它的
    refresh token 会在某天静默失效 —— 等到真有人来用，才在第一次请求时
    发现要重登。用户感知就是「号池里明明有余额的号，用的时候报错」。

    这也正是参考实现 internal/scheduler/scheduler.go 的 KeepaliveHours
    （默认 [22]，即每晚 22 点）在做的事：主动 refresh 一遍所有 token。

    实现要点（对齐参考实现，也贴合上游真实行为）：
      * **串行 + 节流**：账号之间有 KEEPALIVE_ACCOUNT_GAP 秒间隔。批量并发地
        刷新 token 是一个很明显的机器特征，节流后与真人逐个使用的节奏接近。
      * **只刷新不调用**：不发起对话、不消耗积分、不产生对话记录。
      * **必须真探测（本次修复的关键）**：原来只调 `get_headers()`，
        它只在**本地** expiresAt 临近时才刷新 token —— 而登录态被上游**吊销**时，
        本地 expiresAt 可能还有几千小时，于是保活一路报「存活 ✓」，
        真实请求却 401。这就是「xx 个号失效了但保活说全好」的根因。
        现在优先调余额或账号信息接口：这些接口必须接受登录态，
        能真实回答「这个登录态上游还认不认」。模型列表只用于路由，不作为
        自动禁用账号的唯一依据。
      * **区分 401 与网络抖动**：401/403 是登录态废了（终态）；
        HttpClient Stream error 之类只是抖动，**绝不能**计入失效计数，
        否则一次网络抖动会把好号累计到阈值然后误禁。
      * **连续计数后禁用**：失效达阈值才禁用，避免单次抖动误杀。
      * **成功清零**：探测成功即把 `session_dead_fails` 归零。

    返回结果摘要，写入 schedules.last_result 供后台查看。
    """

    # ---- 平台分派 ----
    #
    # 原实现直接 `from admin.backend import AccountSession` —— 那是
    # **WorkBuddy 的实现**。`_active_accounts` 已按平台过滤，所以对
    # qoder / loomy 的账号会拿 WorkBuddy 协议探活 -> 必然失败。
    #
    # 现在非 workbuddy 平台走该平台 Gateway 的 `keepalive()`。
    target_platform = _platform_of(schedule)
    if target_platform and target_platform != "workbuddy":
        from admin.platform import registry

        spec = None
        for _pid in registry.ids():
            _item = registry.get(_pid)
            if _item is not None and _item.storage() == target_platform:
                spec = _item
                break
        if spec is not None:
            gateway = registry.load_platform_gateway(spec, spec.realm)
            if gateway is not None:
                result = gateway.account_ops().keepalive(db)
                result.setdefault("platform", target_platform)
                return result

    from admin.backend import AccountSession
    from admin.models import Account
    from admin.models import SystemSetting

    # Persist only contested identities, so a one-minute retry never rotates
    # all already-refreshed accounts again and survives a process restart.
    pending_row = db.get(SystemSetting, "keepalive:pending")
    batch_row = db.get(SystemSetting, "keepalive:batch_started")
    try:
        pending_ids = json.loads(pending_row.value) if pending_row else []
    except (TypeError, ValueError):
        pending_ids = []
    try:
        batch_started = float(batch_row.value) if batch_row else 0
    except (TypeError, ValueError):
        batch_started = 0

    accounts = (db.query(Account).filter(Account.status == "active")
                .filter(Account.platform == "workbuddy")
                .order_by(Account.id.asc()).all())
    if pending_ids and time.time() - batch_started < 20 * 3600:
        accounts = [a for a in accounts if a.id in pending_ids]
    else:
        batch_started = time.time()
    if not accounts:
        if pending_row:
            pending_row.value = "[]"
            db.commit()
        return {"task": "keepalive_tokens", "ok": True, "total": 0, "msg": "没有活跃账号"}

    total = len(accounts)
    ok = 0
    refreshed = 0
    dead_disabled = 0
    failed = 0
    dead_ids: list[int] = []        # 本次判定为「登录态失效」的账号
    errors: list[str] = []

    skipped = Counter()
    pending = []
    for a in accounts:
        with maintenance.token_slot(a, db) as reason:
            if reason:
                skipped[reason] += 1
                if reason in ("inflight", "background_busy"):
                    pending.append(a.id)
                continue
            sess = None
            try:
                sess = AccountSession(a.auth_json)
                before = _access_token_of(sess)
                # **强制刷新**，而不是 `get_headers()`（后者只在本地 expiresAt 临近时才刷）。
                #
                # 这是本事故里最容易被忽略的一层：这些号的 expiresAt 写着 2027 年，
                # 于是 `get_headers()` 一直认为「还早」，永远不刷新 ——
                # 保活每天都报「成功 N/N、refreshed=0」，**实际上一个 token 都没动过**。
                # 而上游的 offline session 会因为长期没有刷新活动而失效，
                # 最终变成 `invalid_grant: Offline user session not found`。
                #
                # 官方客户端正是**按 24h 固定节奏刷新**（见 10.8.1），并不看是否临近过期。
                # 我们的「每晚一次」只要真的去刷，节奏就与官方一致。
                sess.cm._refresh()
                after = _access_token_of(sess)
                if after and after != before:
                    refreshed += 1
                # **真探测**：本地 expiresAt 不可信（登录态被吊销时它仍可能是
                # 几千小时之后）。余额或账号信息接口才是登录态的权威判据。
                # Save a rotated token before any optional probe can fail.
                a.auth_json = sess.updated_json()
                db.commit()
                # Cooling/WAF protects business calls; credential renewal is
                # independent and must not clear or extend those protections.
                protected = maintenance._reason(a) in ("waf", "account_cooling")
                if not protected:
                    _trusted_token_probe(sess)
                # **回写刷新后的凭证**。原实现漏了这一步：刷新出来的新 token 只存在
                # 临时文件里，`close()` 一删就没了，库里仍是旧 token ——
                # 等于「刷新了但白刷」，下次请求还得从头再来（或直接失败）。
                a.auth_json = sess.updated_json()
                ok += 1
                # 探测成功 → 清零失效连续计数
                if a.session_dead_fails:
                    a.session_dead_fails = 0
                if not protected:
                    a.last_err_at = None
                    a.last_err_msg = ""
                    # 陈旧证据一并清掉：last_upstream_error 没有时间字段、
                    # 不会自过期，留着会让 _looks_login_dead 把健康号判死。
                    a.last_upstream_error = ""
                db.commit()
            except Exception as e:
                msg = str(e)
                from admin.routers.proxy import _extract_http_status
                # 从异常文本里抠出真实状态码再分类。
                # 传 0 会把 401 归成 transport（网络抖动），于是失效号永远不被发现
                # ——这正是「xx 个号失效、保活却报全好」的最后一环。
                http_status = getattr(e, "http_status", 0) or _extract_http_status(msg)
                trusted_failure = bool(getattr(e, "trusted_auth_failure", False))
                if trusted_failure:
                    # This exception is produced only after both trusted
                    # account/balance probes rejected the credential.  Do not
                    # send it through the shared-401 anomaly gate.
                    from admin.upstream_errors import error_info
                    a.last_upstream_error = json.dumps(
                        error_info(getattr(e, "raw_body", msg), http_status),
                        ensure_ascii=False)
                    kind = "session_dead"
                else:
                    kind = maintenance.note_failure(db, a, e)
                if kind == "session_dead" and not trusted_failure:
                    from admin.upstream_auth_guard import observe
                    if observe(http_status, msg, getattr(a, "uid", "")):
                        kind = "upstream_auth_anomaly"
                # Only explicit session failures count toward disabling. Account
                # restrictions and WAF use the same cooling policy as conversations.
                if kind == "session_dead":
                    # 权威判定：登录态被上游**明确拒绝**，重试多少次都一样。
                    #
                    # 两种都算权威：
                    #   * HTTP 401/403；
                    #   * 刷新接口返回的 `invalid_grant` /
                    #     `Offline user session not found` —— 这是 OAuth 标准里
                    #     「该会话已在服务端被删除」的语义，属于**终态**，
                    #     与「文案式 12153 抖动」完全不同。
                    authoritative = (http_status in (401, 403)
                                     or any(m in msg.lower() for m in _AUTH_DEAD_MARKERS))
                    # 但「续签失败」≠「号不可用」：上游吊销离线会话（refresh
                    # invalid_grant）**不必然**吊销已签发的 access token ——
                    # 线上实测 #18：refresh 死了 7 周，access token（60 天长效）
                    # 的真实对话照样 200。只有 access token 实测**也被拒**时，
                    # 这个号才真正无法出流量，才值得禁用；否则保留在池里服务，
                    # 直到 access token 自然过期，探测失败后自会禁用。
                    token_still_alive = (not trusted_failure and authoritative
                                         and _access_token_alive(sess))
                    if token_still_alive:
                        a.last_err_at = datetime.utcnow()
                        a.cool_kind = ""
                        a.session_dead_fails = 0
                        a.last_err_msg = (
                            f"保活续签失败（refresh token 已失效，无法再续签）"
                            f"但 access token 实测仍可用，暂保留出流量；"
                            f"请尽快重新登录：{msg[:120]}"
                        )[:255]
                        db.commit()
                        failed += 1
                        if len(errors) < 5:
                            errors.append(f"#{a.id}: 续签失败但 token 实测可用，保留（需重登）")
                        continue
                    dead_ids.append(a.id)
                    a.session_dead_fails = (a.session_dead_fails or 0) + 1
                    a.cool_kind = "session_dead"
                    a.last_err_at = datetime.utcnow()
                    if authoritative:
                        a.last_err_msg = (
                            f"登录态被上游拒绝（HTTP {http_status}），需重新登录：{msg[:150]}"
                        )[:255]
                    else:
                        a.last_err_msg = (msg or "keepalive 探测失败")[:255]
                    # Keepalive is a health hint, not the authority for
                    # deleting a credential.  Require repeated failures; a
                    # shared route/header fault must never empty the pool.
                    threshold = max(3, _KEEPALIVE_DEAD_THRESHOLD)
                    if a.session_dead_fails >= threshold:
                        a.status = "disabled"
                        a.cool_until = None
                        a.breaker_until = None
                        a.degrade_until = None
                        dead_disabled += 1
                    db.commit()
                    failed += 1
                    if len(errors) < 5:
                        errors.append(f"#{a.id}: 登录态失效（{kind}"
                                      f"{'，HTTP ' + str(http_status) if authoritative else ''}）")
                else:
                    # Transport failures do not disable credentials; WAF/rate
                    # restrictions and shared auth anomalies have already been
                    # recorded without changing account status.
                    failed += 1
                    if len(errors) < 5:
                        errors.append(f"#{a.id}: {kind} {msg[:90]}")
            finally:
                if sess is not None:
                    try:
                        sess.close()
                    except Exception:
                        pass

    if pending_row is None:
        pending_row = SystemSetting(key="keepalive:pending", value="[]")
        db.add(pending_row)
    pending_row.value = json.dumps(pending)
    if batch_row is None:
        batch_row = SystemSetting(key="keepalive:batch_started", value="0")
        db.add(batch_row)
    batch_row.value = str(batch_started)
    db.commit()
    # 只有「真失效」才值得告警；纯网络失败不该给人「号废了」的错觉
    summary = f"保活成功 {ok}/{total}"
    if refreshed:
        summary += f"，其中 {refreshed} 个刷新了 token"
    if dead_ids:
        # 这里是**纯文本**摘要（直接渲染进表格单元格），不要写 markdown 星号：
        # 前端不会解析，页面上会原样露出 `**`。
        summary += (f"，{len(dead_ids)} 个登录态已失效"
                    f"（{'、'.join('#' + str(i) for i in dead_ids[:5])}"
                    f"{' 等' if len(dead_ids) > 5 else ''}，"
                    f"{dead_disabled} 个已自动禁用，其余待人工重登）")
    if failed and not dead_ids:
        summary += f"，{failed} 个探测失败"
    if pending:
        summary += f"，{len(pending)} 个在途占用待重试（1 分钟后自动补跑）"
    if skipped.get("inactive"):
        summary += f"，{skipped['inactive']} 个账号已停用"
    return {"task": "keepalive_tokens", "ok": True, "total": total,
            "alive": ok, "refreshed": refreshed, "failed": failed,
            "skipped": sum(skipped.values()), "skip_reasons": dict(skipped),
            "pending_ids": pending, "retry_pending": bool(pending),
            "disabled": dead_disabled, "dead_ids": dead_ids, "errors": errors,
            "summary": summary}



#: 执行成长任务前，若列表刚刷新过不足这个秒数，则等待补足。
#: 上游任务定义与账号进度之间存在同步延迟，刷新列表后立刻执行会拿到
#: 旧的任务定义/进度，导致「新任务没被识别」或「重复触发」。定时任务里
#: 把本任务排在刷新之后几分钟，就是这个原因。
_GROWTH_MIN_AFTER_REFRESH = 180


def run_growth_tasks() -> dict:
    """自动完成号池内所有账号可自动化的成长任务，并领取奖励。

    与后台「批量做任务」按钮走的是同一套逻辑（growth 路由的 run_accounts），
    因此串行执行、节流规则与「执行后自动领取」的行为完全一致。

    过滤规则（与手动执行一致）：
      - 只处理策略表里 actionable 的任务（其余为需客户端完成，跳过）
      - 已 claimed 的跳过，保证幂等，重复跑无副作用
      - 单次失败只记录、不重试，避免对注定失败的任务反复发请求
    """
    from admin.models import Account
    from admin.routers import growth as growth_router

    started = datetime.utcnow()
    db = SessionLocal()
    try:
        # 1) 先刷新任务定义，保证本轮基于最新的任务列表与进度
        try:
            growth_router.growth_tasks(refresh=True, db=db)
        except Exception:
            pass  # 刷新失败不阻断执行（可能只是网络抖动，用旧缓存继续）

        # 只跑**本平台**的账号：成长任务是 WorkBuddy 专属的
        # （每日活跃 / 签到 / 猫猫旅行 / 盲盒），拿 Trae/Qoder/Loomy 的号
        # 去跑必然失败 —— 之前没过滤，结果里 6 个其它平台的号全在 failed。
        accounts = (db.query(Account)
                    .filter(Account.status == "active",
                            Account.platform == _platform_of(_current_schedule()))
                    .order_by(Account.id.asc()).all())
        accounts = growth_eligible(accounts)
        ids = [a.id for a in accounts]
        if not ids:
            return {"task": "run_growth_tasks", "ok": True, "accounts": 0,
                    "msg": "没有可用账号（企业版账号没有成长任务）"}

        run_res = growth_router.run_accounts(
            ids, None, db,
            # 整批预算：定时任务在**调度线程**里同步跑，跑多久占多久。
            # 没有这个上限时，xx 个账号最坏能占住调度线程一个多小时，
            # 期间整点刷新余额 / 签到 / token 保活全部停摆
            # （表现为「定时任务卡住了」）。超出预算的账号如实记录、留待下次。
            soft_budget=growth_router._SCHEDULE_SOFT_BUDGET)
    except Exception as e:
        return {"task": "run_growth_tasks", "ok": False, "error": str(e)}
    finally:
        db.close()

    # 汇总（run_accounts 内部已逐个账号执行 + 领取，无需再单独 claim 一遍）
    results = run_res.get("results") or []
    credit = sum((r.get("credit") or 0) for r in results)
    energy = sum((r.get("energy") or 0) for r in results)
    fired = sum(1 for a in results
                for t in (a.get("tasks") or []) if t.get("ok") and not t.get("skipped"))
    failed_acc = [a["account_id"] for a in results if not a.get("ok")]

    # 逐账号明细：失败原因同样要落到具体账号上，而不是只给一个数字
    labels = {a.id: _account_label(a) for a in accounts}
    detail = []
    for r in results:
        aid = r.get("account_id")
        label = labels.get(aid, f"账号#{aid}")
        credit_i = int(r.get("credit") or 0)
        n_done = sum(1 for t in (r.get("tasks") or [])
                     if t.get("ok") and not t.get("skipped"))
        if r.get("ok"):
            detail.append({"account_id": aid, "account": label,
                           "ok": True, "credit": credit_i,
                           "tasks_done": n_done,
                           "reason": f"完成 {n_done} 个任务、+{credit_i} 积分"
                           if n_done else "无待做任务"})
        else:
            failures = [t.get("last_error") or t.get("accept_message") or t.get("skipped")
                        for t in r.get("tasks", []) if not t.get("ok")]
            detail.append({"account_id": aid, "account": label,
                           "ok": False, "credit": credit_i, "tasks_done": n_done,
                           "reason": _short_error(r.get("msg") or r.get("error")
                                                  or next((f for f in failures if f), None)
                                                  or ("奖励领取失败" if r.get("claim_failed") else "未知原因"))})

    result = {
        "task": "run_growth_tasks",
        "ok": True,
        "accounts": len(ids),
        "tasks_done": fired,
        "credit": credit,
        "energy": energy,
        "failed_accounts": failed_acc[:10],
        "elapsed_s": round((datetime.utcnow() - started).total_seconds(), 1),
        "detail": detail,
    }
    result["summary"] = _growth_summary(result)
    return result


def _growth_summary(r: dict) -> str:
    """成长任务结果的一句话摘要。"""
    parts = []
    done = r.get("tasks_done") or 0
    if done:
        parts.append(f"完成 {done} 个任务")
    if r.get("credit"):
        parts.append(f"+{r['credit']} 积分")
    if r.get("energy"):
        parts.append(f"+{r['energy']} 能量")
    if not done:
        parts.append("本轮无待做任务")
    bad = [d for d in (r.get("detail") or []) if not d.get("ok")]
    if bad:
        who = "；".join(f"{d['account']}（{d['reason']}）" for d in bad[:3])
        more = f" 等 {len(bad)} 个" if len(bad) > 3 else ""
        parts.append(f"{len(bad)} 个账号失败：{who}{more}")
    return "，".join(parts)



def run_refresh_growth_tasks() -> dict:
    """刷新成长任务定义缓存。

    任务定义对所有账号一致，所以只需一个可用登录态即可（不遍历账号，
    避免无谓的上游请求）。失败时保留旧缓存，不影响面板使用。
    """
    from admin.routers import growth as growth_router

    try:
        db = SessionLocal()
        try:
            data = growth_router.growth_tasks(refresh=True, db=db)
        finally:
            db.close()
    except Exception as e:
        return {"task": "refresh_growth_tasks", "ok": False, "error": str(e)}

    tasks = data.get("tasks") or []
    actionable = sum(1 for t in tasks if t.get("actionable"))
    if data.get("stale"):
        return {"task": "refresh_growth_tasks", "ok": False, "stale": True,
                "total": len(tasks), "actionable": actionable,
                "synced_at": data.get("synced_at"),
                "error": data.get("error") or "上游任务刷新失败",
                "summary": "刷新失败，当前保留旧任务列表"}
    return {
        "task": "refresh_growth_tasks",
        "ok": True,
        "total": len(tasks),
        "actionable": actionable,
        "synced_at": data.get("synced_at"),
        "source_account_id": data.get("source_account_id"),
        "summary": f"共 {len(tasks)} 个任务，其中 {actionable} 个可自动完成",
    }


def growth_eligible(accounts):
    """过滤掉**企业版账号**。

    企业版（`enterpriseId` 非空）在官方客户端里没有「签到 / 成长任务 /
    猫猫旅行」这些活动：它们属于个人版的运营玩法。用企业号去打这些接口
    只会拿到 1002/1003 之类的"无资格"，既浪费请求、又把结果页刷成一片红。

    判据与 `converter.ClientManager.is_enterprise()` 一致：读落库的
    `enterprise_id`，不额外发请求。
    """
    return [a for a in accounts if not (getattr(a, "enterprise_id", "") or "").strip()]


def run_daily_checkin(db, schedule: "Schedule | None" = None) -> dict:
    """遍历活跃账号执行每日签到领取积分。

    风控要点：
      - 请求只使用账号凭据和该接口实际需要的字段；不合成桌面设备令牌。
      - 若任务配置了 stop_after（下次停止领取时间），到达后直接跳过，不再发领取请求，
        避免活动下线后继续请求触发上游风控。
      - 若某账号领取返回 EventEnded(1003)，自动把 stop_after 设为今天，后续不再尝试。

    结果里除了计数，还逐账号记录「领到多少分 / 没领成功的原因」：
    只给一个 `failed: 3` 等于没说 —— 用户真正想知道的是**哪个号**失败了、**为什么**。

    ## 并发（2026-11-06）

    这里是**纯网络等待**（每号 2 次上游 HTTP），串行时 100 个号要几分钟，
    期间调度线程一直占着，别的任务只能排队 —— 用户看到的就是「签到一跑，
    整个后台发卡」。现在按批取号 + 线程池并发，每个号独立 Session，
    墙钟时间按并发度成比例下降，内存只保留在当前批。

    并发度默认 `ADMIN_CHECKIN_CONCURRENCY`（缺省 6，比平台通用批处理更保守）：
    WorkBuddy 的签到接口对同 IP 高频请求敏感，太快反而整批 429/风控。
    """
    from admin.models import Account
    from admin.backend import AccountSession
    from admin.platform import shared as platform_shared

    now = datetime.utcnow()

    # 停止领取时间：到达则跳过
    if schedule is not None and schedule.stop_after is not None:
        if now > schedule.stop_after:
            return {"task": "daily_checkin", "ok": True,
                    "skipped": "已超过停止领取时间，不再请求",
                    "stop_after": schedule.stop_after.isoformat()}

    storage = _platform_of(schedule)
    # 分批取号：号池再大，内存里也只有一批对象。
    eligible: list[int] = []
    for ids in platform_shared.iter_account_batches(
            db, storage=storage, status="active", batch=200):
        rows = db.query(Account).filter(Account.id.in_(ids)).all()
        # 企业版没有签到活动，先剔掉再判断「有没有账号」
        eligible.extend(a.id for a in growth_eligible(rows))
    if not eligible:
        return {"task": "daily_checkin", "ok": True, "total": 0,
                "msg": "没有可用账号（企业版账号不参与签到）"}

    import os

    def _concurrency() -> int:
        try:
            value = int(os.getenv("ADMIN_CHECKIN_CONCURRENCY", "6") or 6)
        except (TypeError, ValueError):
            value = 6
        return max(1, min(16, value))

    ended_box = {"ended": False}

    def worker(session, a) -> dict:
        """单个账号的签到（跑在线程池里，`session` 是本任务私有的）。"""
        label = _account_label(a)
        try:
            with AccountSession(a.auth_json) as sess:
                st = sess.get_checkin_status()
                if st.get("today_checked_in"):
                    # token 也可能在这里被刷新过，写回
                    a.auth_json = sess.updated_json()
                    session.commit()
                    return {"account_id": a.id, "account": label,
                            "ok": True, "status": "already",
                            "credit": 0, "reason": "今日已领过"}
                res = sess.claim_daily_checkin()
                if res.get("ok"):
                    credit = int(res.get("credit") or 0)
                    from admin.growth_center import record_result
                    reward_record = record_result(
                        session, a.id, {"ok": True, "credit": credit},
                        source="schedule", action="每日打卡")
                    note = ""
                    try:
                        from admin.routers.accounts import _sync_credit_snapshot
                        balance = sess.fetch_balance()
                        a.balance_total = int(balance.get("total") or 0)
                        a.balance_remain = int(balance.get("remain") or 0)
                        from admin.credit_journal import attach_balance
                        attach_balance(session, [reward_record],
                                       a.balance_remain)
                        a.last_sync_at = datetime.utcnow()
                        _sync_credit_snapshot(a, sess.fetch_credit_details())
                    except Exception as error:
                        note = "奖励已领取，余额或到期快照刷新失败"
                        _logger.warning(
                            "签到后资源刷新失败 account_id=%s: %s",
                            a.id, type(error).__name__)
                    a.auth_json = sess.updated_json()
                    session.commit()
                    return {"account_id": a.id, "account": label, "ok": True,
                            "status": "claimed", "credit": credit,
                            "streak_days": int(res.get("streak_days") or 0),
                            "reason": ("+%d 积分" % credit) + (
                                "（%s）" % note if note else "")}
                if res.get("status") == "event_ended":
                    ended_box["ended"] = True
                    a.auth_json = sess.updated_json()
                    session.commit()
                    return {"account_id": a.id, "account": label,
                            "ok": False, "status": "event_ended",
                            "credit": 0, "reason": "活动已结束"}
                reason = res.get("status") or res.get("msg") or "未知原因"
                a.auth_json = sess.updated_json()
                session.commit()
                return {"account_id": a.id, "account": label,
                        "ok": False, "status": "failed", "credit": 0,
                        "reason": str(reason)}
        except Exception as e:  # noqa: BLE001 - 单号失败不影响其它号
            return {"account_id": a.id, "account": label, "ok": False,
                    "status": "error", "credit": 0,
                    "reason": _short_error(e)}

    rows = platform_shared.run_account_batch(
        eligible, worker, concurrency=_concurrency(), label="wb-checkin")

    claimed = skipped_already = failed = 0
    total_credit = 0
    errors: list[str] = []
    detail: list[dict] = []
    for row in rows:
        status = row.get("status")
        if status == "claimed":
            claimed += 1
            total_credit += int(row.get("credit") or 0)
        elif status == "already":
            skipped_already += 1
        else:
            failed += 1
            errors.append("%s:%s" % (row.get("account") or row.get("account_id"),
                                     row.get("reason") or ""))
        if len(detail) < 60:
            detail.append(row)

    ended = ended_box["ended"]
    # 发现活动已结束：自动把停止时间设为今天，防止后续继续请求
    if ended and schedule is not None:
        schedule.stop_after = now
        db.commit()

    result = {
        "task": "daily_checkin",
        "ok": True,
        "total": len(rows),
        "claimed": claimed,
        "skipped_already": skipped_already,
        "failed": failed,
        "credit": total_credit,
        "activity_ended": ended,
        "errors": errors[:10],
        "detail": detail,
        "detail_truncated": len(rows) > len(detail),
    }
    result["summary"] = _checkin_summary(result)
    return result


def run_trae_checkin(db, schedule: "Schedule | None" = None) -> dict:
    """Trae 每日签到（**仅国内版真实可用**）。

    为什么与 `run_daily_checkin` 分开：
      · 上游接口不同 —— 签到走 `/trae/api/v2/ug/checkin_credits/*`，
        只在 `api.trae.cn` 上注册（打到 api.trae.com.cn 是 404）；
      · 返回结构不同 —— 上游给 `checked_in` / `credits` / `extra_credits`，
        没有「成长任务」那套概念；
      · 国际版**没有签到** —— 上游返回业务错误，本函数如实记为失败，
        绝不伪造「已签到」。

    幂等：上游 `checked_in=true` 时直接算「今日已领」，不重复 claim。
    """
    from admin.platform.trae.core import client as trae_client
    from admin.platform.trae.services import pool as trae_pool

    realm = "cn"
    if schedule is not None:
        # 任务若绑定了 Trae 国际版，直接说明「该平台不支持签到」，
        # 而不是打一次必然失败的上游请求。
        realm = str(getattr(schedule, "domain", "") or "cn") or "cn"
    if realm == "intl":
        return {"task": "trae_checkin", "ok": True, "total": 0,
                "claimed": 0, "skipped_already": 0, "failed": 0, "credit": 0,
                "detail": [],
                "summary": "Trae 国际版没有签到接口，已跳过"}

    # 并发执行：每个号一次上游 claim，串行时号池一多就慢得离谱
    # （与 WorkBuddy 签到同一套批处理：按批取号 + 线程池 + 独立 Session）。
    from admin.platform import shared as platform_shared

    rows = trae_pool.accounts_for(db, realm)
    active = [a.id for a in rows if (a.status or "") == "active"]
    if not active:
        return {"task": "trae_checkin", "ok": True, "total": 0,
                "claimed": 0, "skipped_already": 0, "failed": 0, "credit": 0,
                "detail": [], "summary": "没有可签到的账号"}

    def worker(session, account):
        item = {"account_id": account.id, "account": account.name or "",
                "ok": False, "status": "", "credit": 0, "reason": ""}
        try:
            creds = trae_pool.credentials(session, account)
            result = trae_client.claim_checkin(creds, realm)
            gained = int(result.get("earned_credit") or 0)
            item.update({
                "ok": bool(result.get("ok")),
                "credit": gained,
                "status": "already" if result.get("already") else "claimed",
                "reason": result.get("message") or "",
            })
        except trae_client.UpstreamError as exc:
            item.update({"status": "failed", "reason": "%s: %s" % (
                exc.code or exc.status, (exc.message or "")[:120])})
        except Exception as exc:  # noqa: BLE001 - 单号失败不影响其它号
            item.update({"status": "failed", "reason": str(exc)[:120]})
        return item

    results = platform_shared.run_account_batch(
        active, worker, label="trae-checkin")

    detail, claimed, already, failed, credit = [], 0, 0, 0, 0
    for item in results:
        credit += int(item.get("credit") or 0)
        status = item.get("status")
        if status == "already":
            already += 1
        elif item.get("ok"):
            claimed += 1
        else:
            failed += 1
        if len(detail) < 60:
            detail.append(item)

    bits = []
    if claimed:
        bits.append("签到成功 %d 个" % claimed + ("（+%d 积分）" % credit if credit else ""))
    if already:
        bits.append("%d 个今日已签" % already)
    if failed:
        bits.append("%d 个失败" % failed)
    return {
        "task": "trae_checkin",
        "ok": failed == 0,
        "total": len(active),
        "claimed": claimed,
        "skipped_already": already,
        "failed": failed,
        "credit": credit,
        "detail": detail,
        "summary": " · ".join(bits) or "没有可签到的账号",
    }


def _checkin_summary(r: dict) -> str:
    """把签到结果写成一句人话，后台直接显示这句。"""
    if r.get("skipped"):
        return f"已跳过：{r['skipped']}"
    if r.get("msg"):
        return r["msg"]
    parts = []
    claimed = r.get("claimed") or 0
    credit = r.get("credit") or 0
    if claimed:
        parts.append(f"今日新领 {claimed} 个账号，共 +{credit} 积分")
    already = r.get("skipped_already") or 0
    if already:
        parts.append(f"{already} 个今日已领过")
    failed = r.get("failed") or 0
    if failed:
        # 带上具体是哪个号、什么原因 —— 这才是用户要的信息
        bad = [d for d in (r.get("detail") or []) if not d.get("ok")]
        who = "；".join(f"{d['account']}（{d['reason']}）" for d in bad[:3])
        more = f" 等 {len(bad)} 个" if len(bad) > 3 else ""
        parts.append(f"{failed} 个未领成功：{who}{more}" if who
                     else f"{failed} 个未领成功")
    if r.get("activity_ended"):
        parts.append("活动已结束，已自动停止")
    return "，".join(parts) if parts else "无可领取账号"



def _run_one(s: Schedule, db, now: datetime):
    from admin.pool import POOL
    if POOL.waf.active() and s.task != "keepalive_tokens":
        s.next_run_at = now + timedelta(seconds=max(15, POOL.waf.remaining()))
        s.last_result = _dump_result({"task": s.task, "ok": True, "deferred": True,
                                     "summary": "出口保护期间顺延任务，保护解除后重试"})
        db.commit()
        return
    if s.task == "run_growth_plan":
        start_growth_plan(s, db)
        return
    # 保活是「每天在指定本地整点执行」而非「每 N 分钟执行」。
    #
    # 原实现（有 bug，已修）：`if not _is_keepalive_hour(now): 顺延到下一天; return`
    # —— 只要那一小时里服务不在线（重启 / 部署 / 夜里没人用），
    # 下次轮询就把 next_run_at 顺延到**第二天**，而 last_run_at 不动。
    # 结果是「每天 22 点刚好错过 → 永远错过」：实测 09-20 之后连续 18 天
    # 一次都没跑过，而界面上每天都显示「下次 22:00」，看起来完全正常。
    # 这直接导致 xx 个账号登录态失效后无人发现。
    #
    # 现在的语义是**到期窗口**：只要现在距上次成功执行已超过一个周期
    # （或超过了今天的计划时刻），就补跑一次。这样错过整点只会「晚跑」，
    # 不会「永远不跑」。
    if s.task == "keepalive_tokens" and timing_of(s).mode == "preset":
        from admin.models import SystemSetting
        pending = db.get(SystemSetting, "keepalive:pending")
        if not (pending and pending.value not in ("", "[]")) and not _keepalive_due(s, now):
            # 还没到今天的计划时刻。**但没有直接顺延**：
            #
            # 续期窗口（refreshToken，60 天）是账号唯一的死亡线，而这条预设
            # 节奏一旦在整点错过就是一整天。所以这里多做一件事：如果按兜底
            # 周期（ADMIN_RENEWAL_SWEEP_SECONDS）该扫了，且确实有号的窗口
            # 快见底/解析不出，就把 run_task 立刻跑一遍 —— 而不是等 22:00。
            #
            # 没有紧急号时一行都不打上游（`_renewal_urgent_ids` 只读凭证、
            # 不打网络），所以这个检查本身是零成本的。
            if _renewal_sweep_due(db, now):
                try:
                    urgent = _renewal_urgent_ids(db)
                except Exception:      # noqa: BLE001 - 扫描失败不影响正常排期
                    urgent = []
                _mark_renewal_sweep(db, now)
                if urgent:
                    result = run_task(s.task, db, s)
                    payload = dict(result)
                    payload["summary"] = (
                        "续期窗口兜底：" + str(result.get("summary") or "")
                        + f"（触发账号 {'、'.join('#' + str(i) for i in urgent[:5])}"
                        + (" 等" if len(urgent) > 5 else "") + "）")
                    s.last_result = _dump_result(payload)
                    s.last_run_at = now
                    s.next_run_at = _next_keepalive_at(now)
                    db.commit()
                    return
            s.next_run_at = _next_keepalive_at(now)
            db.commit()
            return
        # 到期（可能已经迟到）→ 照常执行；执行后把 next 排到下一个计划时刻
        result = run_task(s.task, db, s)
        s.last_result = _dump_result(result)
        s.last_run_at = now
        s.next_run_at = now + timedelta(minutes=1) if result.get("retry_pending") else _next_keepalive_at(now)
        db.commit()
        return
    result = {}
    try:
        result = run_task(s.task, db, s)
        s.last_result = _dump_result(result)
    except Exception as e:  # 单个任务失败不影响调度循环
        s.last_result = json.dumps(
            {"ok": False, "error": _short_error(e)}, ensure_ascii=False)[:2000]
    s.last_run_at = now
    s.next_run_at = (now + timedelta(minutes=1) if s.task == "keepalive_tokens"
                     and result.get("retry_pending") else next_run_at(s, now))
    db.commit()


#: 上次平台任务补种的时间（monotonic）。新平台接入 / 新账号导入后，
#: 不必重启进程就能拿到定时任务 —— 导入成功路径也会立刻补一次，这里只是兜底。
_last_platform_seed = 0.0


def _loop():
    while True:
        db = None
        try:
            db = SessionLocal()
            now = datetime.utcnow()
            global _last_platform_seed
            if time.monotonic() - _last_platform_seed > 900:
                _last_platform_seed = time.monotonic()
                try:
                    ensure_platform_schedules(db)
                except Exception:  # noqa: BLE001 - 兜底失败不能停调度
                    _logger.exception("平台定时任务兜底补种失败")
            schedules = db.query(Schedule).filter(Schedule.enabled == 1).all()
            # 先启动独立的成长巡检线程，再执行原有同步任务。旧的一次性
            # 成长任务可能很长，不能让它拖延已经到期的每日活跃巡检。
            schedules.sort(key=lambda s: s.task != "run_growth_plan")
            for s in schedules:
                if s.next_run_at is None or s.next_run_at <= now:
                    _run_one(s, db, now)
        except Exception:
            # 数据库短暂不可用（如 MySQL 重启）时静默跳过本轮，下一轮再试。
            # 原先的写法在 `SessionLocal()` 本身抛异常时会引用未赋值的 db，
            # 让异常从 except 块里再次抛出并终止调度线程。
            pass
        finally:
            if db is not None:
                try:
                    db.close()
                except Exception:
                    pass
        time.sleep(15)


def seed_defaults(db):
    """首次启动若无任何任务则写入默认任务（含每日签到）。"""
    if db.query(Schedule).count() == 0:
        now = datetime.utcnow()
        db.add(Schedule(name="整点刷新平台总积分", task="refresh_balances",
                        interval_minutes=60, enabled=1, next_run_at=now))
        # 积分到期快照：选号「先用快过期的」完全依赖它，单独成一个任务，
        # 免得被人为了省开销把 with_expiry 关掉时连调度依据一起搞丢。
        db.add(Schedule(name="每 2 小时更新积分到期快照", task="refresh_credits",
                        interval_minutes=120, enabled=1,
                        next_run_at=now + timedelta(minutes=3)))
        db.add(Schedule(name="每日同步模型列表", task="sync_models",
                        interval_minutes=1440, enabled=1, next_run_at=now))
        db.add(Schedule(name="每日签到领取积分", task="daily_checkin",
                        interval_minutes=1440, enabled=1, next_run_at=now))
        db.add(Schedule(name="每日更新成长任务列表", task="refresh_growth_tasks",
                        interval_minutes=1440, enabled=1, next_run_at=now))
        db.add(Schedule(name="每日自动做成长任务", task="run_growth_tasks",
                        interval_minutes=1440, enabled=1,
                        next_run_at=now + timedelta(seconds=_GROWTH_MIN_AFTER_REFRESH)))
        db.commit()


def ensure_credit_schedule(db):
    """补齐「积分到期快照」定时任务（幂等）。

    为什么必须有独立任务：选号策略里的「先用快过期的」读的是
    `accounts.credits_expiring_soon` / `credits_soonest_expire_at`。
    这两个值只在积分快照刷新时更新 —— 如果没人刷，它们会一直停在旧值，
    表现就是「明明有积分快到期了，调度却一直不动」，而且**没有任何报错**，
    属于最难发现的一类故障。默认每 2 小时一次：官方积分按自然日推进，
    2 小时的粒度足够让「7 天内到期」提前被看见，又不会把上游打得太密。
    """
    row = db.query(Schedule).filter(Schedule.task == "refresh_credits").first()
    if row is None:
        now = datetime.utcnow()
        db.add(Schedule(name="每 2 小时更新积分到期快照", task="refresh_credits",
                        interval_minutes=120, enabled=1,
                        next_run_at=now + timedelta(minutes=3)))
        db.commit()


def ensure_growth_plan_schedule(db):
    """逐账号成长巡检：每小时检查到站礼物，每天只补一次成功对话。"""
    if db.query(Schedule).filter(Schedule.task == "run_growth_plan").first() is None:
        db.add(Schedule(name="WorkBuddy 成长计划巡检", task="run_growth_plan",
                        interval_minutes=60, enabled=1,
                        next_run_at=datetime.utcnow() + timedelta(minutes=5)))
        db.commit()


def start_growth_plan(schedule, db):
    """独立后台线程执行，立即运行按钮和调度循环均无需等待整批账号。"""
    from admin.growth_plan import _Lease, run_growth_plan
    lease = _Lease(db)
    if not lease.acquire():
        # 不覆盖正在执行的进度；略延后其它同类型任务，避免每15秒重复调度。
        if schedule.next_run_at is None or schedule.next_run_at <= datetime.utcnow():
            schedule.next_run_at = datetime.utcnow() + timedelta(minutes=1)
            db.commit()
        return {"ok": True, "running": True, "summary": "成长计划正在执行"}
    try:
        sid = schedule.id
        lease_value = lease.value
        started = datetime.utcnow()
        initial = {"task": "run_growth_plan", "ok": True, "running": True,
                   "summary": "成长计划已开始，正在逐账号检查"}
        schedule.last_result = _dump_result(initial)
        schedule.last_run_at = started
        # 进程意外退出后租约十分钟过期，调度器可自动恢复。
        schedule.next_run_at = started + timedelta(minutes=10)
        db.commit()
    except Exception:
        db.rollback()
        lease.release()
        raise

    def worker():
        with SessionLocal() as worker_db:
            worker_lease = _Lease(worker_db)
            worker_lease.value = lease_value

            def progress(result):
                row = worker_db.get(Schedule, sid)
                if row:
                    row.last_result = _dump_result(result)
                    worker_db.commit()

            try:
                result = run_growth_plan(worker_db, progress=progress, lease=worker_lease)
            except Exception as exc:
                worker_db.rollback()
                worker_lease.release()
                result = {"ok": False, "error": f"成长巡检失败（{type(exc).__name__}）"}
            row = worker_db.get(Schedule, sid)
            if row:
                # 刷新以保留执行期间用户修改的间隔/启停配置。
                worker_db.refresh(row)
                row.last_result = _dump_result(result)
                finished = datetime.utcnow()
                row.next_run_at = (finished + timedelta(minutes=1) if result.get("pending")
                                   else next_run_at(row, finished))
                worker_db.commit()

    thread = threading.Thread(target=worker, daemon=True, name="wb-growth-plan")
    try:
        thread.start()
    except Exception:
        lease.release()
        schedule.last_result = _dump_result({"ok": False, "error": "成长巡检线程启动失败"})
        db.commit()
        raise
    return initial


def ensure_daily_checkin(db):
    """已存在其它任务但缺每日签到时，补一个默认签到任务（幂等）。

    保证「定期自动签到」在任意已运行实例上都有配置：今天已领的账号会被跳过，
    活动结束（EventEnded）时调度器自动把 stop_after 置为今天，不会误发请求触发风控。
    """
    if db.query(Schedule).filter(Schedule.task == "daily_checkin").count() == 0:
        now = datetime.utcnow()
        db.add(Schedule(name="每日签到领取积分", task="daily_checkin",
                        interval_minutes=1440, enabled=1, next_run_at=now))
        db.commit()


def ensure_daily_activity_schedule(db):
    """补齐「每日活跃对话」定时任务（幂等）。

    ## 为什么这个任务必须有（2026-11-06 用户实测反馈）

    > 被 11140 的账号都是那种用过我们系统之后没怎么继续经常用的账号。

    上游看的是账号**自己的**行为画像。我们闲置的号：token 天天刷，
    但只打 `/userinfo`、`/balance`、`/points` 这类管理接口，**一条对话都没有**
    —— 这就是最典型的「机器在养号」形状，比不发请求更容易被判风控。

    所以除了 token 保活，还要每天让每个号**真的说一句话**。
    间隔 24 小时 + 任务启动漂移，避免整点批量。

    环境变量 `ADMIN_DAILY_ACTIVITY=0` 可关闭。
    """
    if db.query(Schedule).filter(Schedule.task == "daily_activity").count():
        return
    from datetime import timedelta

    from admin import humanize

    now = datetime.utcnow()
    # 首次触发往后推一个随机量（0~90 分钟），别和别的任务挤在同一分钟
    first = now + timedelta(seconds=max(0.0, humanize.human_start_delay(5400.0)))
    db.add(Schedule(name="每日活跃对话（模拟真人使用）", task="daily_activity",
                    interval_minutes=1440, enabled=1, next_run_at=first))
    db.commit()


def ensure_growth_schedules(db):
    """补齐成长任务相关定时任务（幂等）。

    两个任务必须成对存在且有先后顺序：
      - refresh_growth_tasks：先刷新任务列表
      - run_growth_tasks：    几分钟后再执行
    执行任务排在后面是硬性要求，见 _GROWTH_MIN_AFTER_REFRESH 的说明。

    这里用 next_run_at 保证先后：刷新任务排在 now，执行任务排在 now+延迟；
    若执行任务已存在但时间早于刷新任务，直接顺延。
    """
    now = datetime.utcnow()
    changed = False

    refresh = (db.query(Schedule)
               .filter(Schedule.task == "refresh_growth_tasks").first())
    if refresh is None:
        refresh = Schedule(name="每日更新成长任务列表", task="refresh_growth_tasks",
                           interval_minutes=1440, enabled=1, next_run_at=now)
        db.add(refresh)
        db.flush()  # 拿到 id / 默认值
        changed = True

    run = db.query(Schedule).filter(Schedule.task == "run_growth_tasks").first()
    if run is None:
        base = refresh.next_run_at or now
        db.add(Schedule(name="每日自动做成长任务", task="run_growth_tasks",
                        interval_minutes=1440, enabled=1,
                        next_run_at=base + timedelta(seconds=_GROWTH_MIN_AFTER_REFRESH)))
        changed = True
    else:
        # 已存在则校正先后：执行必须晚于刷新
        base = refresh.next_run_at or now
        want = base + timedelta(seconds=_GROWTH_MIN_AFTER_REFRESH)
        if timing_of(run).mode == "preset" and (run.next_run_at is None or run.next_run_at < want):
            run.next_run_at = want
            changed = True

    if changed:
        db.commit()


def ensure_keepalive_schedule(db):
    """补齐 token 保活定时任务（幂等）。

    为什么用「每天的整点」而不是固定 interval：上游登录态的失效是按自然时间
    推进的，保活要在**没人用号的时候**做（夜里），这样既不影响白天请求，
    又保证第二天早上所有号都是热的。

    参考实现（internal/scheduler/scheduler.go）默认 KeepaliveHours=[22]，
    即每晚 22 点。我们沿用这个思路：默认 22 点，可用
    ADMIN_KEEPALIVE_HOURS 配置多个小时（逗号分隔），或设
    ADMIN_KEEPALIVE_ENABLED=0 关闭。

    这里把 interval_minutes 设为 1440（每天一次），并且只在到期时才真正执行
    —— 见 _keepalive_due。
    """
    if not settings.KEEPALIVE_ENABLED:
        # 显式关闭：若存在则禁用（不删除，保留后台可见与手动触发能力）
        row = db.query(Schedule).filter(Schedule.task == "keepalive_tokens").first()
        if row is not None and row.enabled:
            row.enabled = 0
            db.commit()
        return

    row = db.query(Schedule).filter(Schedule.task == "keepalive_tokens").first()
    if row is None:
        now = datetime.utcnow()
        target = _next_keepalive_at(now)
        db.add(Schedule(name="每日 token 保活刷新", task="keepalive_tokens",
                        interval_minutes=1440, enabled=1, next_run_at=target))
        db.commit()
        return

    # ---- 存量记录自愈 ----
    if timing_of(row).mode != "preset":
        return  # 用户指定的周期不被启动时的系统预设覆盖。
    # 旧版本用 **UTC** 的小时来计算 next_run_at，与现在的「本地时区」语义不符
    # （22 点被算成 UTC 22:00 = 北京次日 06:00）。部署新版后，库里那条旧记录
    # 会把下次执行排到「按新语义看还早得很」的时刻 —— 逻辑修好了却仍然不跑。
    # 更糟的是它可能已经逾期很久（本次故障就是 18 天）。
    #
    # 这里只做「**向前**对齐」：
    #   * 已经到期（含逾期很久）→ 把 next_run_at 设为现在，立即补跑；
    #   * 尚未到期但存量时间明显偏晚 → 拉回按新语义算出的计划时刻；
    #   * 绝不往后推 —— 推迟本来就该执行的保活是危险的。
    now = datetime.utcnow()
    want = now if _keepalive_due(row, now) else _next_keepalive_at(now)
    if row.next_run_at is None or row.next_run_at > want:
        if row.next_run_at is None or row.next_run_at - want > timedelta(minutes=1):
            row.next_run_at = want
            db.commit()


def _local_now() -> datetime:
    """当前**本地**时间。

    为什么需要：调度器其它地方统一用 `datetime.utcnow()`（库里存的也是 UTC），
    但 `ADMIN_KEEPALIVE_HOURS=22` 在配置注释里明确写的是「本地时区」，
    用户理解就是「晚上 10 点」。原实现直接拿 UTC 的 hour 去比，
    于是 22 点实际打到了 **UTC 22:00 = 北京次日 06:00** —— 用户设的夜间保活
    变成了清晨，与他「在没人用号的时候做」的意图不符。
    """
    return datetime.now()


def _tz_offset() -> timedelta:
    """本地时间与 UTC 的偏移（`本地 - UTC`）。

    **必须用同一瞬间的两个时钟来求**：`datetime.now()`（本地）与
    `datetime.utcnow()`（UTC）。早先的写法是
    `_local_now() - now`（把「真实当前时间」减去**调用方传入的时间**），
    两者不是同一瞬间 —— 于是偏移量变成了「真实时刻 − 参数」这个毫无意义的差值，
    判断自然全错（测试里表现为「已过计划时刻却算成没到」）。
    """
    return datetime.now() - datetime.utcnow()


def _next_keepalive_at(now: datetime) -> datetime:
    """返回下一个保活时刻（**UTC naive**，与 Schedule.next_run_at 同口径）。

    `now` 传入的是 UTC naive（调度循环统一用 utcnow）。
    这里把 now 换成本地时间判断「今天该跑的小时过没过」，再换回 UTC 存储。

    ## 为什么不是整点（2026-11-06）

    原实现严格落在 `22:00:00` —— 一台每天同一秒开始批量刷 token 的机器。
    现在按配置的小时 + **±28 分钟的随机偏移**，秒也随机：
    「今晚 22:14:37」和「明晚 21:36:02」看起来才像人手动点的保活。

    偏移用 `ADMIN_KEEPALIVE_JITTER_MINUTES` 调整，设 0 回到整点。
    """
    import os
    import random

    tz = _tz_offset()
    now_local = now + tz
    hours = sorted(set(h for h in settings.KEEPALIVE_HOURS if 0 <= h <= 23)) or [22]
    try:
        spread = max(0.0, float(os.getenv("ADMIN_KEEPALIVE_JITTER_MINUTES", "28")))
    except ValueError:
        spread = 28.0

    def _shift(moment: datetime) -> datetime:
        if spread <= 0:
            return moment
        return moment + timedelta(minutes=random.uniform(-spread, spread),
                                  seconds=random.uniform(-59, 59))

    for h in hours:
        cand_local = now_local.replace(hour=h, minute=0, second=0, microsecond=0)
        shifted = _shift(cand_local)
        # 抖动后可能落在「已经过去」—— 那就退回整点，避免任务被判当场到期
        if shifted > now_local:
            return shifted - tz
        if cand_local > now_local:
            return cand_local - tz
    nxt = (now_local + timedelta(days=1)).replace(
        hour=hours[0], minute=0, second=0, microsecond=0)
    shifted = _shift(nxt)
    return (shifted if shifted > now_local else nxt) - tz


def _keepalive_due(s: "Schedule", now: datetime) -> bool:
    """保活现在是否该跑。

    判据（任一成立即到期）：

    1. 已到/已过**今天**的计划时刻，且今天还没成功跑过
       —— 覆盖「22:00 那一刻服务不在线，23:30 才起来」的情况；
    2. 距上次成功执行已超过 `周期 + 宽限`（24h + 6h）
       —— 兜底「计划时刻总是错过」的极端情况，保证至少一天跑一次。

    这样错过整点只会**晚跑**，不会像原实现那样无限顺延。
    """
    tz = _tz_offset()
    now_local = now + tz
    hours = sorted(set(h for h in settings.KEEPALIVE_HOURS if 0 <= h <= 23)) or [22]
    today_plan_local = now_local.replace(
        hour=hours[0], minute=0, second=0, microsecond=0)
    today_plan = today_plan_local - tz          # 换回 UTC 比较

    last = s.last_run_at
    if last is not None:
        # 今天已经跑过（且是在今天的计划时刻之后跑的）→ 不重复
        if last >= today_plan:
            return False
        # 兜底：距上次超过 周期+宽限，即使计划时刻算错也要补一次
        grace = timedelta(hours=6)
        if now - last >= timedelta(days=1) + grace:
            return True
    # 计划时刻已到（今天还没跑）→ 到期
    return now >= today_plan


def _is_keepalive_hour(now: datetime) -> bool:
    """当前（本地）小时是否命中保活整点。

    .. deprecated:: 判断「该不该跑」请用 `_keepalive_due`。
       保留本函数只因它出现在既有调用方与文档里；语义已改为**本地时区**。
    """
    hours = set(settings.KEEPALIVE_HOURS)
    return _local_now().hour in hours


# ---------------------------------------------------------------------------
# 续期窗口兜底（renewal sweep）
# ---------------------------------------------------------------------------

def _renewal_urgent_ids(db) -> list[int]:
    """找出「续期窗口快要/已经见底」的账号。

    判据来自各平台的 `Gateway.credential_expiry()`：续期窗口的剩余天数
    低于该平台的告急阈值，或干脆解析不出来。

    为什么这件事不能只交给每日保活：保活是**预设整点**触发，错过就是一整天
    （历史上积压过 18 天）。而续期窗口是账号唯一的死亡线 —— 短期令牌过期
    刷新就回来，窗口过期则真的只能重新授权登录。兜底扫描把它变成
    「最多晚 N 秒」。

    只回 idle 名单，不在这里打上游：真正的刷新由保活那把全局锁串行执行，
    并发刷新是明显的机器特征，也可能触发上游风控。
    """
    from admin.credential_expiry import STATE_UNKNOWN, credential_expiry, renews_guard_days
    from admin.models import Account

    guard = renews_guard_days()
    urgent: list[int] = []
    # 老数据的 platform 可能是空串（历史行），`Account.platform == "workbuddy"`
    # 会把它们漏掉。这里不再按平台过滤 —— 有没有续期窗口由各平台自己回答，
    # 不支持的平台会返回 supported=False，直接跳过即可。
    rows = (db.query(Account)
            .filter(Account.status == "active")
            .order_by(Account.id.asc()).all())
    for acc in rows:
        info = credential_expiry(acc)
        if not info.get("supported"):
            continue
        if not info.get("has_refresh"):
            # 没有续期令牌的号根本救不回来，别浪费一次上游请求。
            continue
        days = info.get("refresh_days_left")
        if info.get("refresh_state") == STATE_UNKNOWN:
            # 解析不出到期时间（凭证格式异常）→ 值得试一次刷新，
            # 刷新成功会把两条时间线都写正。
            urgent.append(acc.id)
        elif days is not None and days < guard:
            urgent.append(acc.id)
    return urgent


def _renewal_sweep_due(db, now: datetime) -> bool:
    """兜底扫描现在该不该跑（按 SystemSetting 记录的上一轮时间）。"""
    from admin.models import SystemSetting

    interval = int(getattr(settings, "RENEWAL_SWEEP_SECONDS", 0) or 0)
    if interval <= 0:
        return False
    row = db.get(SystemSetting, "keepalive:renewal_sweep_at")
    try:
        last = float(row.value) if row else 0
    except (TypeError, ValueError):
        last = 0
    return (now.timestamp() - last) >= interval


def _mark_renewal_sweep(db, now: datetime) -> None:
    from admin.models import SystemSetting

    row = db.get(SystemSetting, "keepalive:renewal_sweep_at")
    if row is None:
        db.add(SystemSetting(key="keepalive:renewal_sweep_at",
                             value=str(now.timestamp())))
    else:
        row.value = str(now.timestamp())
    db.commit()


_scheduler_lock = threading.Lock()
_scheduler_started = False


#: 定时任务的默认周期与名称后缀。
#:
#: 为什么不再只认 `*_checkin` / `*_keepalive`：平台 `task_runners()` 声明的
#: 任务远不止这两个，整点刷新余额、积分到期快照、模型同步、成长任务都需要在
#: 库里有一条 Schedule 才会被调度线程执行。只种签到等于「平台接进来了，但只有
#: 签到会跑」。这里按任务名给出默认周期，已存在的记录一律不覆盖（用户改过的
#: 周期优先）。
_TASK_SCHEDULE_META = {
    "refresh_balances": ("整点刷新余额", 60),
    "refresh_credits": ("积分到期快照", 360),
    "sync_models": ("同步模型列表", 360),
    "daily_checkin": ("每日签到", 1440),
    "refresh_growth_tasks": ("更新成长任务", 360),
    "run_growth_tasks": ("自动成长任务", 1440),
    "run_growth_plan": ("成长计划", 240),
    "keepalive_tokens": ("令牌保活", 360),
    "ban_recheck": ("风控封号复核", 30),
    "daily_activity": ("每日活跃对话", 1440),
    # WPS 灵犀：签到/保活走默认后缀规则，这里只补两条语义不同的。
    # 活跃对话必须**每天一次**（360 默认会让一个号一天被用四次，反而更假）；
    # 失效复检每 2 小时一轮就够，Cookie 过期本来就要人工重粘。
    "wps_lingxi_activity": ("每日活跃对话", 1440),
    "wps_lingxi_ban_recheck": ("失效账号复检", 120),
    # Loomy 新手任务（一次性 10000 积分）：每天跑一次兜住半途失败，幂等。
    "loomy_onboarding": ("新手任务领积分", 1440),
}


def _schedule_meta(task: str, label: str) -> tuple[str, int]:
    """任务名 → (展示名, 周期分钟)。

    表里没有的任务名会把**原始 task 名**直接拼进展示名
    （`loomy_onboarding · Loomy`）—— 那就是「列表里出现英文」的成因。
    所以新增平台任务时，务必在 `_TASK_SCHEDULE_META` 里补一条中文名；
    漏了也只是名字难看，不影响执行。

    这里同时**复用前端那套中文名**（`TASK_LABELS`），并按后缀兜底，
    避免「同一个任务在定时任务页叫中文、在别处叫英文」。
    """
    name = str(task)
    if name in _TASK_SCHEDULE_META:
        title, minutes = _TASK_SCHEDULE_META[name]
        return f"{title} · {label}", minutes
    if name.endswith("_checkin"):
        return f"每日签到 · {label}", 1440
    if name.endswith("_keepalive"):
        return f"会话保活 · {label}", 360
    if name.endswith("_refresh_quota"):
        return f"刷新额度 · {label}", 360
    if name.endswith("_onboarding"):
        return f"新手任务领积分 · {label}", 1440
    if name.endswith("_activity"):
        return f"每日活跃对话 · {label}", 1440
    if name.endswith("_ban_recheck"):
        return f"失效账号复检 · {label}", 120
    return f"{name} · {label}", 360


def ensure_schedule_titles(db) -> int:
    """把**已经入库**的英文任务名改写成中文（幂等）。

    只在「平台任务名是原始英文」时改写 —— 用户手动改过名字的（`name`
    里已含中文）一律不动。这修的是历史遗留：`loomy_onboarding (Loomy)`
    这种直接用 task 名当标题的行。
    """
    fixed = 0
    try:
        # 必须先装载平台：否则 `registry.get()` 拿不到 spec，label 会退化成
        # 落库名（`· loomy` 而不是 `· Loomy`）—— 中文名对了但大小写难看。
        from admin.platform.loader import load_platforms

        load_platforms()
    except Exception:  # noqa: BLE001 - 平台装载失败也要继续，只是名字丑一点
        _logger.debug("定时任务名本地化：平台装载失败", exc_info=True)
    try:
        rows = db.query(Schedule).all()
    except Exception:  # noqa: BLE001 - 读不到就算了
        return 0
    for row in rows:
        task = str(row.task or "")
        current = str(row.name or "")
        if not task or not current:
            continue
        # 判断「这个名字是不是还没翻译」：取 ` · ` 之前的主体，
        # 只要**不含中文**就说明它是原始 task 名拼出来的（`loomy_onboarding · Loomy`）。
        # 之前用「名字里有 · 就跳过」把这类种子行全跳过了 —— 正是要改的那批。
        head = current.split("·")[0].strip()
        if any("\u4e00" <= ch <= "\u9fff" for ch in head):
            continue  # 已经是中文标题（或用户自己改过），不动
        platform = str(row.platform or "")
        if not platform:
            continue
        try:
            from admin.platform import registry

            spec = registry.get(platform)
            label = getattr(spec, "label", "") or platform
        except Exception:  # noqa: BLE001
            label = platform
        title, _minutes = _schedule_meta(task, label)
        if title and title != current:
            row.name = title
            fixed += 1
    if fixed:
        db.commit()
        _logger.info("已把 %d 条定时任务名本地化", fixed)
    return fixed


def ensure_platform_schedules(db):
    """把**各平台**的签到 / 保活任务补进定时任务表（幂等）。

    为什么需要：`seed_defaults` 只种 WorkBuddy 的任务；Qoder / Loomy / Trae
    的任务虽然由平台 `task_runners()` 声明、也能在「新建任务」里选到，
    但库里默认一条都没有 —— 看到的仍是「只有 WorkBuddy 的定时任务」。

    规则：
      · 只处理 `*_checkin` / `*_keepalive`；
      · 同 (platform, task) 已存在则跳过（不覆盖用户改过的周期）；
      · 签到每天一次、保活每 6 小时一次；
      · 平台没有可用账号时不建，免得列表里塞一堆必然失败的任务。
    """
    from admin.models import Account
    from admin.platform import registry
    from admin.platform.loader import load_platforms

    try:
        load_platforms()
    except Exception:  # noqa: BLE001
        return []
    created = []
    now = datetime.utcnow()
    seen_storage = set()
    for platform_id in registry.ids():
        spec = registry.get(platform_id)
        if spec is None or not spec.enabled:
            continue
        storage = str(spec.storage()).lower()
        # 同一 storage 的多个区只种一次（trae-cn / trae-intl 共用 runner）
        if storage in seen_storage:
            continue
        seen_storage.add(storage)
        try:
            gateway = registry.load_platform_gateway(spec, spec.realm)
            runners = (gateway.task_runners() if gateway else {}) or {}
        except Exception:  # noqa: BLE001
            continue
        has_account = (db.query(Account.id)
                       .filter(Account.platform == storage,
                               Account.status == "active").first()) is not None
        if not has_account:
            continue
        # 平台 `task_runners()` 声明的任务**全部**补种（不只是签到/保活）：
        # 各平台只声明自己的任务，不会把 WorkBuddy 的成长任务带进来。
        for task in runners:
            name = str(task)
            # ban_recheck 是全局任务，已有 ensure_ban_recheck_schedule 负责，
            # 这里再按平台种一遍只会变成 N 条重复探测。
            if name == "ban_recheck":
                continue
            exists = (db.query(Schedule)
                      .filter(Schedule.task == name,
                              Schedule.platform == storage).first())
            if exists is not None:
                continue
            title, minutes = _schedule_meta(name, spec.label)
            is_checkin = name.endswith("_checkin")
            db.add(Schedule(
                name=title,
                task=name,
                platform=storage,
                interval_minutes=minutes,
                enabled=1,
                # 签到错开 5 分钟先跑；其余 15 分钟后排队，避免启动瞬间并发
                next_run_at=now + (timedelta(minutes=5) if is_checkin
                                   else timedelta(minutes=15)),
            ))
            created.append("%s/%s" % (storage, name))
    if created:
        db.commit()
        _logger.info("已补种平台定时任务：%s", ", ".join(created))
    return created



def ensure_ban_recheck_schedule(db):
    """补齐「风控封号复核」定时任务（幂等）。

    每 30 分钟一轮；真正的探测频率由 admin/ban_recheck.py 的退避控制
    （每号 ≥ BAN_RECHECK_MIN_INTERVAL，持续仍封则指数退避）。
    ADMIN_BAN_RECHECK_ENABLED=0 时禁用任务（保留后台手动触发能力）。
    """
    row = db.query(Schedule).filter(Schedule.task == "ban_recheck").first()
    if not settings.BAN_RECHECK_ENABLED:
        if row is not None and row.enabled:
            row.enabled = 0
            db.commit()
        return
    if row is None:
        db.add(Schedule(name="风控封号复核与自动解封", task="ban_recheck",
                        interval_minutes=30, enabled=1,
                        next_run_at=datetime.utcnow() + timedelta(minutes=2)))
        db.commit()


def start_scheduler():
    """在 FastAPI 启动时调用：播种默认任务并拉起守护线程。

    幂等：重复调用只会有一个调度线程。uvicorn --reload 或多 worker 场景下
    会多次触发 startup，没有这个守卫就会起多个线程、把定时任务重复执行。
    """
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            return
        _scheduler_started = True
    try:
        db = SessionLocal()
        seed_defaults(db)
        ensure_daily_checkin(db)
        ensure_growth_schedules(db)
        ensure_keepalive_schedule(db)
        ensure_credit_schedule(db)
        ensure_ban_recheck_schedule(db)
        ensure_growth_plan_schedule(db)
        ensure_daily_activity_schedule(db)
        # 各平台（Qoder / Loomy / Trae）的签到与会话保活：缺就补一条
        try:
            ensure_platform_schedules(db)
        except Exception:  # noqa: BLE001 - 补种失败不能挡住调度线程
            _logger.exception("平台定时任务补种失败")
        # 把历史上用原始 task 名当标题的行改写成中文（幂等，只动英文行）
        try:
            ensure_schedule_titles(db)
        except Exception:  # noqa: BLE001
            _logger.exception("定时任务名本地化失败")
        # DeepSeek's time-limited compensation endpoint expired; retire old
        # schedules so a legacy database never keeps polling it.
        db.query(Schedule).filter(Schedule.task.in_(("claim_benefits", "claim_compensation"))).delete(
            synchronize_session=False)
        db.commit()
        from admin.growth_center import import_last_run
        import_last_run(db)
        db.close()
    except Exception:
        pass
    t = threading.Thread(target=_loop, daemon=True, name="wb-scheduler")
    t.start()


# 模块加载时装表。放在文件末尾 —— 此处所有 runner 函数都已定义。
# 不用装饰器自动注册：那会让「哪些任务支持」散落在文件各处，
# 而这张表要能被 `admin/workbuddy/__init__.py` 一眼读完。
_bootstrap_runners()