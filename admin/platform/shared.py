"""平台网关共用能力：鉴权、平台判定、选号、租约、记账、哨兵。

## 为什么需要这个模块

原来的三个平台网关是**各写各的**：Trae 有完整的鉴权/选号/记账，
Qoder 与 Loomy 只有「拿一个号打上游」——没有 Key 校验、没有账号绑定、
不写用量日志、不占租约、也不上报哨兵。表现就是：

  * 用别的平台的 Key 调 Qoder 也能成功（鉴权形同虚设）；
  * 用量日志里 Qoder/Loomy 的请求**一条都没有**（用户看不到走哪个平台）；
  * 同一个账号被并发打爆（没有在途租约，选号还只取第一个）；
  * 风控哨兵收不到这两个平台的信号。

这些能力**与上游协议无关**，属于网关通用职责。放在这里，平台目录只保留
「协议 + 错误码语义」，符合 `admin/platform/gateway.py` 的职责边界。

## 约定

  * 平台 id：`storage`（落库值，如 `trae` / `qoder`）与 `public`（对外
    id，如 `trae-cn`）分开；本模块统一用 **storage** 查号、**public** 记账。
  * 所有函数**不抛平台自身的异常**；拒绝用 `PlatformRejected`，
    由平台网关转成对应协议的错误体。
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from admin.models import Account, AccountModelCooldown, ApiKey, ModelConfig, UsageLog
from admin.platform import registry
from admin.security import check_quota, get_key_row, hash_key

logger = logging.getLogger("admin.platform.shared")


class PlatformRejected(Exception):
    """本地拒绝（鉴权 / 配额 / 暂停 / 无号），**不是上游失败**。

    `status` 直接作为 HTTP 状态；`payload` 是已经符合下游协议的错误体。
    """

    def __init__(self, status: int, message: str, *, kind: str = "invalid_request_error",
                 code: str = "", headers: dict | None = None):
        self.status = int(status)
        self.message = message
        self.kind = kind
        self.code = code
        self.headers = headers or {}
        super().__init__(message)

    def openai(self):
        from starlette.responses import JSONResponse

        body = {"error": {"message": self.message, "type": self.kind}}
        if self.code:
            body["error"]["code"] = self.code
        return JSONResponse(status_code=self.status, content=body,
                            headers=self.headers)

    def anthropic(self):
        from starlette.responses import JSONResponse

        return JSONResponse(
            status_code=self.status,
            content={"type": "error",
                     "error": {"type": self.kind, "message": self.message}},
            headers=self.headers)


# ---------------------------------------------------------------------------
# 平台判定
# ---------------------------------------------------------------------------
def request_token(request) -> str:
    token = request.headers.get("x-api-key") or ""
    if token:
        return token.strip()
    authorization = request.headers.get("authorization") or ""
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


#: scope 里的内部调用标记。
#:
#: **只能由进程内代码写入**：Starlette 为真实 HTTP 请求构造的 scope 里
#: 不会有这个 key，请求头也伪造不出来（我们不从任何头读它），
#: 所以外部请求无法通过它绕过鉴权。
INTERNAL_KEY = "_wb_internal"


def internal(request) -> bool:
    """本次调用是不是进程内自发的（模型同步、账号探测等）。

    为什么必须有它：`admin/routers/models._catalog_of()` 为了复用各平台的
    `list_models()`，构造了一个 `Request(scope)` 在**进程内**直接调用。
    那条路径的第一行就是 `_auth()` —— 内部调用没有、也不该有 API Key，
    于是每个非默认平台的「同步模型」都在这里 401 掉，而调用方只看到
    「平台 XX 未返回模型」，最后整个同步 502。

    实测症状（2026-10-31）：workbuddy 同步到 36 个模型，
    trae / qoder / loomy 一律 0 个，且不报错。
    """
    try:
        return bool(request.scope.get(INTERNAL_KEY))
    except Exception:  # noqa: BLE001 - 测试桩可能没有 scope
        return False


def public_platform_of(request, fallback: str) -> str:
    """本次请求实际命中的**对外平台 id**。

    中间件会把解析结果写进 `request.state.platform`；平台网关自己再读一次
    header 只作为兜底（直连测试 / 单测没有中间件）。缺了这一步，
    `qoder-intl` 的请求会被当成 `qoder`，区域串号后上游回 403。
    """
    state = getattr(request, "state", None)
    value = getattr(state, "platform", "") if state is not None else ""
    if not value:
        value = (request.headers.get("x-platform") or "").split(":")[0]
    return str(value or fallback).strip().lower() or fallback


def realm_of(request, spec) -> str:
    """本次请求的落库区域（`cn` / `intl` / 空）。"""
    state = getattr(request, "state", None)
    value = getattr(state, "realm", "") if state is not None else ""
    if not value:
        header = (request.headers.get("x-platform") or "")
        if ":" in header:
            value = header.split(":", 1)[1]
    if not value:
        value = request.headers.get("x-realm") or request.query_params.get("realm") or ""
    return registry.normalize_realm(spec, value) if spec is not None else str(value or "")


def locate(request, fallback: str) -> tuple[str, str, object]:
    """`(storage, realm, spec)`。"""
    public = public_platform_of(request, fallback)
    spec = registry.get(public) or registry.get(fallback)
    if spec is None:
        return fallback, "", None
    return spec.storage(), realm_of(request, spec), spec


# ---------------------------------------------------------------------------
# 鉴权
# ---------------------------------------------------------------------------
def key_row(db: Session, request) -> ApiKey | None:
    """按请求头取 Key 行；结果缓存到 scope，热路径只查一次库。"""
    cached = request.scope.get("_wb_api_key")
    if cached is not None:
        return cached or None
    token = request_token(request)
    row = get_key_row(db, token) if token else None
    request.scope["_wb_api_key"] = row or False
    return row


def authenticate(db: Session, request, *, platform: str = "") -> ApiKey | None:
    """校验 Key + 配额 + 哨兵暂停。

    平台网关的 /v1 是**对外入口**，必须与 WorkBuddy 同口径鉴权：
    没有 Key、Key 被停用、额度耗尽、平台被哨兵暂停，都在这里拒绝，
    绝不能直接拿号池里的账号去打上游。

    **例外：进程内自发的调用**（`internal(request)`，如「同步模型」经
    `_catalog_of` 复用平台 `list_models`）。它不来自网络，没有调用方身份
    可验，也不该消耗任何 Key 的配额，因此直接放行并返回 `None`。
    调用方对返回值的用法都是 `getattr(key, …, default)` 或判空，
    `None` 安全；需要 Key 才能决定的事（绑定账号 / 分组）本来也不该
    作用于内部调用。
    """
    if internal(request):
        return None
    token = request_token(request)
    if not token:
        raise PlatformRejected(401, "缺少 API Key", kind="auth_error",
                               code="missing_api_key")
    key = key_row(db, request)
    if key is None:
        raise PlatformRejected(401, "无效 API Key", kind="auth_error",
                               code="invalid_api_key")
    try:
        check_quota(key)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        error = detail.get("error") or {}
        raise PlatformRejected(
            exc.status_code,
            error.get("message") or "API Key 不可用",
            kind=error.get("type") or "invalid_request_error",
            code=error.get("code") or "") from None

    # 风控哨兵熔断：平台被暂停时直接拒绝，不打上游、不消耗额度。
    try:
        from admin.platform import sentinel_router

        target = public_platform_of(request, platform)
        hold = sentinel_router.paused(target)
        if hold:
            raise PlatformRejected(
                503,
                "该平台已被风控哨兵暂停：%s" % (hold.get("reason") or "疑似连坐封控"),
                kind="server_error", code="upstream_paused",
                headers={"Retry-After": "60", "X-Sentinel-Paused": "1"})
    except PlatformRejected:
        raise
    except Exception:  # noqa: BLE001 - 哨兵故障不能阻断转发
        pass
    return key


def key_scope(key: ApiKey | None) -> tuple[int | None, int | None]:
    """`scope_accounts` 需要的 `(account_id, key_id)`。"""
    if key is None:
        return None, None
    return getattr(key, "account_id", None), getattr(key, "id", None)


# ---------------------------------------------------------------------------
# 选号
# ---------------------------------------------------------------------------
def select_account(db: Session, *, storage: str, realm: str = "",
                   key: ApiKey | None = None, model: str = "",
                   exclude_ids=None, now: datetime | None = None) -> Account | None:
    """健康 + 归属 + 冷却 + 在途 过滤后，选**最闲**的一个号。

    与 WorkBuddy 的 `_select_account` 同口径，只是平台/区域由调用方给定。
    之前 Qoder/Loomy 直接 `accounts_for()[0]`：不看在途、不看冷却、
    不看 Key 绑定 —— 并发一上来就全砸同一个号。
    """
    from admin.account_binding import scope_accounts
    from admin.pool import POOL

    now = now or datetime.utcnow()
    account_id, key_id = key_scope(key)
    query = (db.query(Account).populate_existing()
             .filter(Account.platform == storage, Account.status == "active"))
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
        cooled = {row.account_id for row in
                  db.query(AccountModelCooldown.account_id)
                  .filter(AccountModelCooldown.model == model,
                          AccountModelCooldown.until > now).all()}
        rows = [row for row in rows if row.id not in cooled]
        if not rows:
            return None

    rows = POOL.available(rows)
    if not rows:
        return None
    account = min(rows, key=lambda row: (
        POOL.inflight.count(row.uid or ""),
        row.last_used_at or datetime.min,
        row.id))
    account.last_picked_at = now
    # 让「请求动态 / 请求审计」知道这次用的是哪个号。
    #
    # 以前只有 WorkBuddy 主链路调 `mark_attempt`，三个平台网关都没调 ——
    # 于是面板上平台请求的「账号」一列永远是 `—`，出问题时无法归因。
    # 这里挂在**唯一的选号出口**上，平台新增也不用再改一遍。
    try:
        from admin import request_metrics

        request_metrics.mark_attempt(account.id, model)
    except Exception:  # noqa: BLE001 - 观测失败不能影响选号
        pass
    return account


def lease(account: Account | None) -> bool:
    from admin.pool import POOL

    if account is None:
        return False
    return bool(POOL.acquire_account(account))


def release(account: Account | None) -> None:
    from admin.pool import POOL

    if account is None:
        return
    try:
        POOL.release(account.uid or "")
    except Exception:  # noqa: BLE001
        pass


def note_success(account: Account | None) -> None:
    if account is None:
        return
    account.last_used_at = datetime.utcnow()
    account.consecutive_fails = 0
    account.breaker_fails = 0
    account.session_dead_fails = 0
    account.cool_until = None
    account.cool_kind = ""


def note_failure(db: Session, account: Account | None, *, kind: str,
                 detail: str = "", status: int = 0, model: str = "",
                 public_platform: str = "", cool_seconds: int = 0) -> None:
    """把失败写回账号，并按需要进哨兵。**不抛异常**。"""
    if account is None:
        return
    try:
        now = datetime.utcnow()
        account.last_err_at = now
        account.last_err_msg = (detail or kind or "")[:255]
        account.err_count = int(account.err_count or 0) + 1
        if cool_seconds > 0:
            until = now + __import__("datetime").timedelta(seconds=cool_seconds)
            if account.cool_until is None or account.cool_until < until:
                account.cool_until = until
                account.cool_kind = kind[:16]
        db.commit()
    except Exception:  # noqa: BLE001 - 状态写回失败不能影响响应
        try:
            db.rollback()
        except Exception:
            pass
        return
    try:
        from admin import sentinel

        if public_platform:
            sentinel.note_failure(public_platform, int(account.id or 0), kind,
                                  code=str(status or ""), status=int(status or 0),
                                  detail=(detail or "")[:200], model=model or "")
    except Exception:  # noqa: BLE001
        logger.debug("哨兵上报失败 kind=%s", kind, exc_info=True)


# ---------------------------------------------------------------------------
# 记账
# ---------------------------------------------------------------------------
def _billing_candidates(model: str) -> list[str]:
    """一个请求模型名可能对应的**库内 model_id** 候选（按优先级）。

    为什么需要它：客户端发来的是 `/v1/models` 给的规范 slug，
    可能是 `deepseek-v4.1-flash-workbuddy`（带平台后缀）或
    `deepseek-flash-qoder`（缩写已替换）。而 `model_configs.model_id`
    存的是上游原始 id（`deepseek-v4.1-flash` / `dfmodel`）。
    只按字面量精确匹配 → 查不到 → 倍率 0 → **积分显示 0**
    。

    候选顺序：
      1. 原样
      2. 剥掉 `-<平台>` 后缀
      3. 去掉 `:cn` / `:intl` 区后缀
      4. 缩写映射还原（`deepseek-flash` -> `dfmodel`）
    """
    raw = str(model or "").strip()
    if not raw:
        return []
    out = [raw]
    # 2) 剥平台后缀（按长度降序，避免 `trae` 抢先匹配 `trae-cn`）
    try:
        from admin.platform import model_suffix

        pid, bare, matched = model_suffix.resolve(raw)
        if matched and bare and bare not in out:
            out.append(bare)
    except Exception:  # noqa: BLE001
        pass
    # 3) 去区后缀
    for item in list(out):
        if ":" in item:
            base = item.split(":", 1)[0]
            if base and base not in out:
                out.append(base)
    # 4) 缩写还原
    try:
        from admin.platform import model_suffix

        for item in list(out):
            storage, upstream = model_suffix.alias_to_upstream(item)
            if upstream and upstream not in out:
                out.append(upstream)
    except Exception:  # noqa: BLE001
        pass
    return out


def credits_for(db: Session, storage: str, model: str, total_tokens: int) -> float:
    """按平台模型倍率估算积分（每千 token）。

    **模型名必须做候选匹配**，不能只按字面量精确查 —— 客户端发的是
    slug（可能带平台后缀 / 缩写已替换），库里存的是上游原始 id。
    """
    if not model:
        return 0.0
    try:
        candidates = _billing_candidates(model)
        for name in candidates:
            row = (db.query(ModelConfig)
                   .filter(ModelConfig.model_id == name,
                           ModelConfig.platform == storage,
                           ModelConfig.enabled == 1).first())
            if row is None:
                row = (db.query(ModelConfig)
                       .filter(ModelConfig.model_id == name,
                               ModelConfig.enabled == 1).first())
            if row is None:
                continue
            multiplier = float(row.credit_multiplier or 0)
            if multiplier:
                return total_tokens * multiplier / 1000.0 if total_tokens else 0.0
        return 0.0
    except Exception:  # noqa: BLE001
        return 0.0


def record_usage(db: Session, *, key_id: int, account_id: int, model: str,
                 usage: dict | None, public_platform: str, storage: str = "",
                 error_kind: str = "", client_ip: str = "", use_case: str = "",
                 seq: int = 0, ttfb_ms: int | None = None,
                 latency_ms: int | None = None, reasoning_effort: str = "") -> int | None:
    """写一条带 `platform` 的用量日志，并扣 Key/账号额度。

    这是「用量日志里看不到 Qoder/Loomy」的直接修复点：两个网关以前根本没写。
    """
    usage = usage if isinstance(usage, dict) else {}
    # **分段耗时自洽性修正**：`latency` 必须 >= `ttfb`，且两者差距过小
    # （非流式一次性返回）时不能当「生成耗时」用 —— 否则 TPS 会算出
    # 几万这种物理上不可能的值。这里把不合理的情况标成「无分段」，
    # 让 `bench_router` 的边界保护统一处理。
    try:
        _t = int(ttfb_ms) if ttfb_ms is not None else None
        _l = int(latency_ms) if latency_ms is not None else None
        if _t is not None and _l is not None and _t > _l:
            ttfb_ms, latency_ms = _l, _t      # 换回来
    except (TypeError, ValueError):
        pass
    prompt = _int(usage.get("prompt_tokens") or usage.get("input_tokens"))
    completion = _int(usage.get("completion_tokens") or usage.get("output_tokens"))
    total = _int(usage.get("total_tokens")) or (prompt + completion)
    cached = 0
    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict):
        cached = _int(details.get("cached_tokens"))
    # 上游明确给了积分就用它（Qoder 的 `x_qoder_credits` 在模型目录里
    # 不代表真实计费，只有响应 usage 才是权威值）；没给才按倍率估算。
    reported = usage.get("credits")
    if isinstance(reported, (int, float)) and not isinstance(reported, bool) and reported >= 0:
        credits = float(reported)
    else:
        credits = credits_for(db, storage, model, total)
    try:
        if key_id:
            db.query(ApiKey).filter(ApiKey.id == key_id).update(
                {ApiKey.credit_used: ApiKey.credit_used + credits},
                synchronize_session=False)
        account = (db.query(Account).filter(Account.id == account_id)
                   .with_for_update().first() if account_id else None)
        if account is not None:
            account.last_used_at = datetime.utcnow()
            if credits:
                account.balance_remain = max(0, int(account.balance_remain or 0)
                                             - int(credits))
        log = UsageLog(api_key_id=key_id or 0, account_id=account_id or 0,
                       platform=_storage_platform(public_platform, storage),
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
        logger.exception("平台记账失败 platform=%s key=%s acc=%s model=%s",
                         public_platform, key_id, account_id, model)
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


def _storage_platform(public_platform: str, storage: str = "") -> str:
    """落库平台名统一成 **storage 平台**（trae / qoder / loomy / workbuddy）。

    同一个 Trae 的两个区共用一个账号池与一张模型表，但网关对外会报
    `trae-cn` / `trae-intl`。如果原样落库，用量日志/统计里就会出现
    「Trae 国内版 15 次」「Trae 国内版 8 次」两条同名列，
    平台筛选也变成两个互不相干的桶 —— 用户第一反应是数据算错了。

    优先用调用方显式给的 `storage`（网关自己知道），没有就按 registry 反查。
    """
    if storage:
        return str(storage).lower()
    name = str(public_platform or "").strip().lower()
    if not name:
        return ""
    try:
        from admin.platform import registry

        spec = registry.get(name)
        if spec is not None:
            return str(spec.storage()).lower()
    except Exception:  # noqa: BLE001 - 反查失败就按原值
        pass
    return name


# ---------------------------------------------------------------------------
# 模型目录（数据库兜底）
# ---------------------------------------------------------------------------
def catalog_from_db(db: Session, storage: str) -> list[dict]:
    """从 `model_configs` 生成 OpenAI `/v1/models` 条目。

    上游目录接口慢/失败时用它兜底 —— 返回 503 让客户端连模型都选不到，
    比返回一份「可能有个别模型调不通」的清单糟糕得多。
    """
    rows = (db.query(ModelConfig)
            .filter(ModelConfig.platform == storage, ModelConfig.enabled == 1)
            .order_by(ModelConfig.model_id.asc()).all())
    out = []
    for row in rows:
        out.append({
            "id": row.model_id,
            "object": "model",
            "created": 0,
            "owned_by": storage,
            "name": row.model_id,
        })
    return out


def model_known(db: Session, storage: str, model: str) -> bool:
    if not model:
        return False
    return (db.query(ModelConfig)
            .filter(ModelConfig.model_id == model,
                    ModelConfig.platform == storage,
                    ModelConfig.enabled == 1).first() is not None)
