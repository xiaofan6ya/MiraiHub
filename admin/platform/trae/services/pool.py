"""Trae 选号与记账：复用系统号池能力，只做平台相关的部分。

**复用**（不复制）：`admin.pool` 的在途租约 / 会话粘性 / 撞号窗口 / 退避，
`admin.account_binding.scope_accounts` 的 Key 归属语义，
`admin.routing_policy` 的选号策略，`admin.selection_order` 的手动优先级，
以及 `admin.usage_logs` + `api_keys.credit_used` 的记账结构。

**本文件独有的**：
  · 平台条件（`platform='trae'`）与区域条件（`domain` = cn/intl）；
  · token 过期即刷新（Trae 的 access token 只活几小时到两周，且刷新会
    轮换 refresh token，必须由账号服务原子落库）；
  · 上游错误码 -> 账号处置（Trae 的 1001/4015/4008 与 WorkBuddy 完全不同）。
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta

from sqlalchemy import or_
from sqlalchemy.orm import Session

from admin import pool, routing_policy, selection_order
from admin.account_binding import scope_accounts
from admin.models import Account, AccountModelCooldown, ApiKey, UsageLog
from admin.pool import POOL
from admin.platform.trae.core import catalog, client
from admin.platform.trae.services import accounts as trae_accounts
from admin.platform.trae.services import desktop_catalog

logger = logging.getLogger("admin.platform.trae.pool")

PLATFORM = "trae"

#: 单账号在途上限（与全局配置一致时用 settings 值；这里给平台默认）。
DEFAULT_MAX_IN_FLIGHT = 3

#: 认证/风控类失败的冷却时长（秒）。Trae 的 4015 是账号级风控，
#: 冷却太短会被反复触发放大；先给 10 分钟，连续失败由系统熔断器接管。
AUTH_COOL_SECONDS = 600
QUOTA_COOL_SECONDS = 1800
RISK_COOL_SECONDS = 900

#: 「模型不存在」的负缓存时长（秒）。**故意很短**：这个错误几乎总是客户端
#: 写错了模型名（上游名大小写敏感），惩罚的是请求而不是账号，冷却太久只会
#: 让一个写错名字的客户端把整个池子对该模型的可用性掐掉。
MODEL_BLOCK_SECONDS = 300

#: 风控哨兵：Trae 的错误种类 -> 是否属于封控信号。
#: 4008（额度耗尽）不是风控，但它同样意味着「这个号别再用」，
#: 因此也记进哨兵以便监控，只是不参与批次熔断判定。
SENTINEL_KINDS = frozenset({"risk_control", "auth", "quota_exceeded"})


def _now() -> datetime:
    return datetime.utcnow()


# ---------------------------------------------------------------------------
# 选号
# ---------------------------------------------------------------------------
def select(db: Session, *, realm: str = "", model: str = "", exclude_ids=None,
           sticky_key: str = "", account_id=None, key_id=None, key=None,
           mark_picked: bool = True) -> Account | None:
    """挑一个可用账号。

    过滤顺序与 WorkBuddy 一致（健康 -> 归属 -> 余额 -> 冷却 -> 模型冷却 ->
    在途 -> 手动优先级 -> 策略），只多了一层「平台 + 区域」。

    `key` 必须传：它带着 `account_ids`（多选独占）与 `platform`（独占平台）。
    只传 `account_id` 会让「多选绑定」和「独占平台」的 Key 选不到号 ——
    表现是绑了账号反而 503。
    """
    now = _now()
    query = db.query(Account).populate_existing().filter(
        Account.platform == PLATFORM, Account.status == "active")
    query = scope_accounts(query, account_id, key_id, key=key)
    if realm:
        query = query.filter(Account.domain == realm)
    if exclude_ids:
        query = query.filter(~Account.id.in_(exclude_ids))
    query = query.filter(or_(Account.cool_until.is_(None), Account.cool_until <= now))
    query = query.filter(or_(Account.breaker_until.is_(None), Account.breaker_until <= now))
    query = query.filter(or_(Account.degrade_until.is_(None), Account.degrade_until <= now))

    rows = query.all()
    if not rows:
        return None

    if model:
        upstream = catalog.upstream_name(model, realm or None)
        cooled = {row.account_id for row in db.query(AccountModelCooldown.account_id)
                  .filter(AccountModelCooldown.model.in_([model, upstream]),
                          AccountModelCooldown.until > now).all()}
        rows = [a for a in rows if a.id not in cooled]
        if not rows:
            return None

    rows = POOL.available(rows)
    if not rows:
        return None

    # 凭证明显失效（refresh token 也用不了）的号直接跳过，避免每轮都白跑一次。
    rows = [a for a in rows if _usable(a, now)]
    if not rows:
        return None

    priority = min(selection_order.manual_priority(a) for a in rows)
    if priority != float("inf"):
        rows = [a for a in rows if selection_order.manual_priority(a) == priority]

    strategy = routing_policy.selection_mode(db)
    if strategy == "throughput":
        loads = {a.id: POOL.inflight.count(a.uid or "") / max(1, POOL.limit_for(a))
                 for a in rows}
        lowest = min(loads.values())
        rows = [a for a in rows if loads[a.id] == lowest]

    if sticky_key:
        bound = POOL.sticky.resolve(sticky_key, {a.uid or "" for a in rows})
        if bound:
            hit = next((a for a in rows if (a.uid or "") == bound), None)
            if hit is not None:
                if mark_picked:
                    POOL.recent.mark(hit.uid or "")
                    hit.last_picked_at = now
                return hit

    candidates = rows
    fresh = [a for a in candidates if POOL.recent.fresh(a.uid or "")]
    candidates = fresh or candidates
    POOL.recent.prune()
    load_key = lambda a: (POOL.inflight.count(a.uid or ""),
                          a.last_used_at or datetime.min, a.id)
    if strategy == "expiry_weighted":
        account = _weighted_pick(candidates, now) or min(candidates, key=load_key)
    elif strategy == "remain":
        account = max(candidates, key=lambda a: (int(a.balance_remain or 0),))
    elif strategy == "oldest":
        account = min(candidates, key=lambda a: (a.created_at or datetime.min, *load_key(a)))
    else:  # throughput / lru / expiring / weighted 都退化为「最闲」
        account = min(candidates, key=load_key)

    if account is not None and mark_picked:
        POOL.recent.mark(account.uid or "")
        account.last_picked_at = now
    return account


def _weighted_pick(candidates: list, now: datetime):
    """按额度余量加权抽签（Trae 没有「临期积分」，权重只看余量与空闲）。"""
    canon = [{"uid": a.uid or "", "credits": int(a.balance_remain or 0),
              "credits_expiring": 0, "last_used_at": a.last_used_at, "acc": a}
             for a in candidates]
    try:
        picked = pool.weighted_pick(canon, now=now)
    except Exception:  # noqa: BLE001 - 权重算法异常退化为最闲
        return None
    return picked["acc"] if picked else None


def _usable(account: Account, now: datetime) -> bool:
    """凭证是否还值得一试：token 未过期，或还有 refresh token 可换。"""
    try:
        data = json.loads(account.auth_json or "{}")
    except ValueError:
        return False
    if not isinstance(data, dict):
        return False
    if client.token_of(data) and not client.is_expired(data):
        return True
    return bool(data.get("refresh_token") or data.get("refreshToken"))


# ---------------------------------------------------------------------------
# 凭证（进入上游前调用）
# ---------------------------------------------------------------------------
def credentials(db: Session, account: Account, *, force_refresh: bool = False) -> dict:
    """取出可直接用于请求的账号凭证；即将过期时先刷新并落库。"""
    data = json.loads(account.auth_json or "{}")
    if not isinstance(data, dict):
        raise client.UpstreamError("bad_credential", "账号凭证损坏，请重新导入", None)
    if force_refresh or not client.token_of(data) or client.is_expired(data):
        fresh = client.refresh_account(data)
        data.update(fresh)
        data["realm"] = client.normalize_realm(data.get("realm"))
        account.auth_json = json.dumps(data, ensure_ascii=False)
        account.cool_kind = ""
        db.commit()
        logger.info("trae 账号 %s token 已刷新（新到期 %s）",
                    account.id, client.expiry_of(data))
    return data


# ---------------------------------------------------------------------------
# 失败处置
# ---------------------------------------------------------------------------
def note_success(db: Session, account: Account) -> None:
    account.last_used_at = _now()
    account.consecutive_fails = 0
    account.breaker_fails = 0
    account.session_dead_fails = 0
    account.cool_until = None
    account.cool_kind = ""
    db.commit()


def note_failure(db: Session, account: Account, exc, model: str = "") -> dict:
    """按 Trae 错误码处置账号；返回处置结果供日志与响应头使用。

    `exc` 放宽成任意异常：SOLO 通道抛的是普通 `RuntimeError`
    （例如 401 认证失败）。以前这里直接读 `exc.kind`，会二次崩溃成
    `AttributeError: 'RuntimeError' object has no attribute 'kind'`，
    把真正的 401 盖掉，排障时完全看不到根因。
    """
    if not isinstance(exc, client.UpstreamError):
        message = str(exc)
        # 从 `SOLO HTTP 401: {...}` 里尽量还原 HTTP 语义
        status = 0
        for code in ("400", "401", "403", "404", "429", "500", "502", "503"):
            if ("HTTP %s" % code) in message:
                status = int(code)
                break
        kind = ("auth" if status in (401, 403)
                else "risk_control" if status == 429
                else "quota_exceeded" if status == 402
                else "upstream")
        exc = client.UpstreamError(kind, message[:400], status or None,
                                   kind=kind)

    now = _now()
    kind = exc.kind
    account.last_err_at = now
    account.last_err_msg = (exc.detail or exc.message or "")[:255]
    account.last_upstream_error = json.dumps(exc.to_dict(), ensure_ascii=False)
    account.err_count = int(account.err_count or 0) + 1
    outcome = {"kind": kind, "cool_until": None, "disable": False, "retry": exc.retryable()}

    # 风控哨兵：Trae 也上报。
    #
    # 只上报 `SENTINEL_KINDS` 里明确属于封控语义的 kind（risk_control /
    # auth / quota_exceeded）。哨兵按 platform 分键，Trae 熔断不会影响
    # WorkBuddy —— 这正是「B 平台炸了 A 平台照样能用」的实现方式。
    #
    # 传落库值 `trae`（两区共用）：熔断对象是「这一个上游」而不是
    # 「某一个区」，国内/国际共用同一套风控策略。
    try:
        from admin import sentinel

        if str(kind or "") in SENTINEL_KINDS:
            sentinel.note_failure(
                PLATFORM, account.id, kind,
                code=str(exc.code or ""), status=int(exc.status or 0),
                detail=(exc.detail or exc.message or "")[:200],
                model=model or "")
    except Exception:  # noqa: BLE001 - 哨兵绝不阻断转发
        logger.debug("trae 哨兵上报失败", exc_info=True)

    if kind == "auth":
        # 401 说明这条 access token 已死：先尝试刷新一次；刷不动才算账号问题。
        try:
            data = credentials(db, account, force_refresh=True)
            if client.token_of(data):
                outcome["retry"] = True
                outcome["refreshed"] = True
                db.commit()
                return outcome
        except client.UpstreamError as refresh_exc:
            account.last_err_msg = ("refresh: %s" % refresh_exc.message)[:255]
            account.session_dead_fails = int(account.session_dead_fails or 0) + 1
            if account.session_dead_fails >= 3:
                account.status = "disabled"
                outcome["disable"] = True
        outcome["retry"] = False
    elif kind == "risk_control":
        _cool(account, now, RISK_COOL_SECONDS, "risk_control")
        outcome["cool_until"] = RISK_COOL_SECONDS
    elif kind == "quota_exceeded":
        _cool(account, now, QUOTA_COOL_SECONDS, "quota")
        outcome["cool_until"] = QUOTA_COOL_SECONDS
    elif kind == "model_unknown":
        # 模型不存在是**请求**问题，不该惩罚账号：这里只登记一条短负缓存，
        # 让同一个错误模型名在几分钟内不再重复打上游（避免「一次写错模型名
        # 就把整个号池的该模型冷却 6 小时」这种自伤）。
        if model:
            upstream = catalog.upstream_name(model)
            _set_model_cool(db, account, upstream, now, MODEL_BLOCK_SECONDS,
                            "model_block", exc.detail)
            outcome["cool_until"] = MODEL_BLOCK_SECONDS
        outcome["retry"] = False
    elif kind == "invalid_request":
        outcome["retry"] = False
    else:
        # transport / 上游 5xx：指数退避，交给系统熔断器
        account.consecutive_fails = int(account.consecutive_fails or 0) + 1
        account.breaker_fails = int(account.breaker_fails or 0) + 1
        if account.breaker_fails >= 5:
            minutes = min(360, 2 ** (account.breaker_fails - 4))
            account.breaker_until = now + timedelta(minutes=minutes)
            outcome["cool_until"] = minutes * 60

    db.commit()
    return outcome


def _cool(account: Account, now: datetime, seconds: int, kind: str) -> None:
    until = now + timedelta(seconds=seconds)
    if account.cool_until is None or account.cool_until < until:
        account.cool_until = until
        account.cool_kind = kind


def _set_model_cool(db: Session, account: Account, model: str, now: datetime,
                    seconds: int, kind: str, reason: str = "") -> None:
    if not model:
        return
    row = db.query(AccountModelCooldown).filter(
        AccountModelCooldown.account_id == account.id,
        AccountModelCooldown.model == model).first()
    until = now + timedelta(seconds=seconds)
    if row is None:
        db.add(AccountModelCooldown(account_id=account.id, model=model,
                                    until=until, kind=kind, reason=reason[:255]))
    else:
        row.until = until
        row.kind = kind
        row.reason = reason[:255]
        row.hits = int(row.hits or 0) + 1
    db.commit()


# ---------------------------------------------------------------------------
# 记账（复用系统 usage_logs + api_keys 额度）
# ---------------------------------------------------------------------------
def record_usage(db: Session, *, key_id: int, account_id: int, model: str,
                 usage: dict | None, error_kind: str = "",
                 client_ip: str = "", use_case: str = "", seq: int = 0,
                 ttfb_ms: int | None = None, latency_ms: int | None = None,
                 reasoning_effort: str = "", realm: str = "") -> int | None:
    """写一条用量日志并按 token 估算扣除 Key 额度。

    Trae 没有 WorkBuddy 的 `credits` 字段，因此积分口径按
    `model_configs.credit_multiplier`（每千 token 积分）估算；未配置倍率的
    Trae 模型记 0 分而不吞掉 token 统计 —— 用量模块照样能出报表。
    """
    usage = usage if isinstance(usage, dict) else {}
    prompt = _int(usage.get("prompt_tokens"))
    completion = _int(usage.get("completion_tokens"))
    total = _int(usage.get("total_tokens")) or (prompt + completion)
    cached = _int((usage.get("prompt_tokens_details") or {}).get("cached_tokens")
                  if isinstance(usage.get("prompt_tokens_details"), dict) else 0)
    credits = 0.0
    try:
        from admin.models import ModelConfig
        row = db.query(ModelConfig).filter(ModelConfig.model_id == (model or ""),
                                           ModelConfig.platform == PLATFORM,
                                           ModelConfig.enabled == 1).first()
        if row is None:
            row = db.query(ModelConfig).filter(ModelConfig.model_id == (model or ""),
                                               ModelConfig.enabled == 1).first()
        multiplier = float(row.credit_multiplier or 0) if row else 0.0
        if multiplier and total:
            credits = total * multiplier / 1000.0
    except Exception:  # noqa: BLE001 - 估算失败不该阻断记账
        credits = 0.0

    try:
        if key_id:
            db.query(ApiKey).filter(ApiKey.id == key_id).update(
                {ApiKey.credit_used: ApiKey.credit_used + credits},
                synchronize_session=False)
        account = (db.query(Account).filter(Account.id == account_id)
                   .with_for_update().first() if account_id else None)
        if account is not None:
            account.last_used_at = _now()
            if credits:
                account.balance_remain = max(0, int(account.balance_remain or 0)
                                             - int(credits))
        log = UsageLog(api_key_id=key_id or 0, account_id=account_id or 0,
                       platform=("trae-cn" if realm == "cn" else
                                 "trae-intl" if realm == "intl" else PLATFORM),
                       model=model or "", credits=credits,
                       prompt_tokens=prompt, completion_tokens=completion,
                       total_tokens=total, cached_tokens=cached,
                       client_ip=client_ip or "", use_case=use_case or "",
                       reasoning_effort=reasoning_effort or "",
                       seq=seq, ttfb_ms=ttfb_ms, latency_ms=latency_ms,
                       error_kind=error_kind or "")
        db.add(log)
        db.commit()
        return log.id
    except Exception:  # noqa: BLE001 - 记账失败不能影响已返回的响应
        logger.exception("trae 记账失败 key=%s acc=%s model=%s", key_id, account_id, model)
        try:
            db.rollback()
        except Exception:
            pass
        return None


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def session_key(body: dict) -> str:
    """会话粘性键：与 WorkBuddy 共用同一套提取逻辑。"""
    try:
        return pool.session_key_for(body)
    except Exception:  # noqa: BLE001
        return ""


def accounts_for(db: Session, realm: str = "") -> list[Account]:
    query = db.query(Account).filter(Account.platform == PLATFORM)
    if realm:
        query = query.filter(Account.domain == realm)
    return query.order_by(Account.id.desc()).all()


def summarize(rows: list[Account]) -> dict:
    active = [a for a in rows if a.status == "active"]
    by_realm: dict[str, int] = {}
    for row in rows:
        by_realm[row.domain or "?"] = by_realm.get(row.domain or "?", 0) + 1
    return {
        "total": len(rows),
        "active": len(active),
        "disabled": len(rows) - len(active),
        "balance_remain": sum(int(a.balance_remain or 0) for a in active),
        "balance_total": sum(int(a.balance_total or 0) for a in active),
        "by_realm": by_realm,
        "cooling": sum(1 for a in rows if a.cool_until and a.cool_until > _now()),
    }


def refresh_quota(db: Session, account: Account) -> dict:
    """刷新额度；顺带保证 token 可用（过期先换），并同步模型目录。

    模型目录的**合并顺序很重要**（后写覆盖先写）：

        1. 上游 `model_list`   —— 只有 6~9 个旧模型（deepseek-V3 / gpt-4o）
        2. 客户端 `state.vscdb` —— 真实全量，几十个新模型（gpt-6.x / gemini-3.1）

    先灌接口清单、再灌客户端目录，客户端才会赢。反过来的话，面板里又只剩
    旧模型 —— 这正是「明明客户端有 GPT-6，网关里只有 GPT-5」的成因。
    """
    try:
        creds = credentials(db, account)
    except client.UpstreamError as exc:
        return {"ok": False, "error": exc.to_dict()}
    realm = client.normalize_realm(account.domain or creds.get("realm"))
    synced = 0
    try:
        rows = client.fetch_model_list(creds, realm)
        synced = catalog.note_live_rows(
            realm, [{**row, "source": "upstream"} for row in rows])
    except client.UpstreamError as exc:
        logger.info("trae 上游模型清单同步失败 realm=%s: %s", realm, exc.detail[:120])

    # 客户端目录（权威）：覆盖上面那批旧模型
    desktop = desktop_catalog.read_models(realm)
    if desktop["models"]:
        synced = catalog.note_live_rows(
            realm, [{**row, "source": "desktop"} for row in desktop["models"]])
    elif desktop["error"]:
        logger.info("trae 客户端目录不可用 realm=%s: %s", realm, desktop["error"])

    info = trae_accounts.sync_quota(db, account)
    return {"ok": True, "models_synced": synced,
            "desktop_models": len(desktop["models"]),
            "desktop_error": desktop["error"], **info}


def uptime_hint() -> dict:
    return {"platform": PLATFORM, "realms": list(client.REALMS),
            "now": time.time()}