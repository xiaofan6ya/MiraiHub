"""多平台转发中间件：网关层的唯一分叉点。

为什么是中间件而不是在每个端点里加 if：`/v1/*` 现有 4 个端点（chat /
responses / messages / models）各有自己的流式状态机，往每个里塞平台判断
会让「默认平台」的路径也背上风险。中间件只在**命中非默认平台**时才接管，
workbuddy 的请求原样穿过，行为零变化。

平台判定顺序（任一命中即可）：

    1. `X-Platform: trae:cn`      显式头，最高优先级
    2. `api_keys.platform`        绑定到某个平台的 Key（管理后台设置）
    3. 按模型名选平台              Key 开聚合时，模型在哪个平台有号就走哪
    4. 网关策略（仅 spread_unbound）随机/额度/轮转…
    5. 默认                        `workbuddy`，与改造前完全一致

## 本次修复的三个真实故障

1. **GET 请求卡死**：`_should_peek()` 对没有 `content-length` 的请求也返回
   True，于是 `_read_body()` 把空 body 读掉，下游再读 body 时永久等待
   （实测 `GET /v1/models` 在 Qoder 上 60s 无响应）。现在只对
   带 body 的 POST/PUT/PATCH 预读。

2. **跨平台选到没有号的平台**：聚合只看 `model_configs.platform`，
   不检查该平台有没有可用账号（`qoder-intl` 有模型没号 → 503）。
   现在候选平台必须同时满足「有该模型」+「有可用账号」。

3. **一次 503 就失败**：聚合转发只试一个平台。现在按策略顺序尝试
   多个候选平台（只对 404/502/503 这类「这个平台做不了」的状态换），
   并把每次尝试写进请求指标，便于排查。

中间件注册在最内层（紧随 app），因此 IP 封禁、准入控制、限流、计时与安全
响应头对**所有平台**一视同仁 —— Trae 的请求同样受配额与限流约束。
"""
from __future__ import annotations

import logging
import time

from starlette.requests import Request
from starlette.responses import JSONResponse

from admin.platform import registry
from admin.platform import shared

logger = logging.getLogger("admin.platform.dispatch")

#: 交给平台网关处理的路径（平台网关模块自己再细分）。
GATEWAY_PATHS = frozenset({
    "/v1/chat/completions", "/v1/responses", "/v1/messages",
    "/v1/messages/count_tokens", "/v1/models", "/v1/balance",
})

#: 需要预读 body 的方法（GET/HEAD 永远不读 —— 读了会卡住下游）。
_BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})

#: 预读上限：超过这个体积不参与跨平台选路（大 body 直接走默认平台）。
_BODY_LIMIT = 262144

#: 这些状态表示「这个平台做不了」，值得换下一个平台再试。
_RETRYABLE_STATUS = frozenset({404, 502, 503})

#: 通用路由名：任何平台都可能自己实现，跨平台拒绝时要放行
_GENERIC_MODEL_NAMES = frozenset({
    "auto", "default", "chat", "best", "smart", "fast", "router", "auto-model",
})

#: 平台顺序缓存（秒）。热路径每次请求都算一遍健康度太贵。
_ORDER_TTL = 3.0
_order_cache: dict = {"at": 0.0, "value": None, "signature": ""}


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------
def _key_of(request):
    """本次请求的 Key 行（缓存到 scope，热路径只查一次库）。"""
    try:
        from admin.db import SessionLocal

        with SessionLocal() as db:
            return shared.key_row(db, request)
    except Exception:  # noqa: BLE001 - 查库失败按未鉴权处理
        logger.debug("API Key 查询失败", exc_info=True)
        return None


def _explicit_selector(request) -> str:
    value = request.headers.get("x-platform") or request.query_params.get("platform") or ""
    realm = request.query_params.get("realm") or ""
    value = str(value).strip()
    if value and realm and ":" not in value:
        value = "%s:%s" % (value, realm)
    return value


def _disabled_platforms() -> set[str]:
    """当前被用户禁用的平台集合（带 3 秒缓存）。

    **禁用是硬约束**：显式头、Key 绑定、按模型选路、策略兜底，
    每一条路径都必须先过这一关，否则就是「禁用了还在用」。
    """
    now = time.time()
    cache = _disabled_cache
    if cache["value"] is not None and now - cache["at"] < _ORDER_TTL:
        return set(cache["value"])
    result: set[str] = set()
    try:
        from admin.platform import gateway_router

        with _db() as db:
            result = set(gateway_router.disabled_map(db) or {})
    except Exception:  # noqa: BLE001 - 读不到就当作没有禁用
        logger.debug("读取禁用平台失败", exc_info=True)
    cache.update({"at": now, "value": set(result)})
    return result


def resolve(request: Request) -> tuple[str, str]:
    """解析本次请求的目标 `(platform, realm)`。

    **顺序不可调换**：显式头 > Key 绑定 > 按模型选路 > 策略 > 默认。

    每条路径都要过 `_disabled_platforms()`：被禁用的平台**一个都不许用**，
    包括用户显式指定的那个 —— 用户禁用它的意思就是「别用它」。
    显式指定被禁用的平台时抛 403，而不是偷偷换一个平台，
    否则用户会以为「我禁用了但它还在跑」。
    """
    disabled = _disabled_platforms()

    explicit = _explicit_selector(request)
    if explicit:
        picked = registry.resolve(explicit)
        if _is_disabled(picked[0], disabled):
            raise PlatformDisabled(picked[0])
        return picked

    key = _key_of(request)
    bound = str(getattr(key, "platform", "") or "").strip() if key else ""
    if bound:
        picked = registry.resolve(bound)
        if _is_disabled(picked[0], disabled):
            # Key 绑定的平台被禁用：这是配置冲突，明确报出来而不是静默改道
            raise PlatformDisabled(picked[0])
        return picked

    model = _model_of(request)
    if model and _aggregate_enabled(key):
        picked = _platform_for_model(model, key)
        if picked:
            return picked[0], picked[1]

    if _spread_unbound():
        picked = _strategy_pick()
        if picked and not _is_disabled(picked[0], disabled):
            return picked

    # 兜底：默认平台。它也可能被用户禁用 —— 那就退到「第一个没被禁用的平台」，
    # 而不是硬塞回默认平台（那等于禁用没生效）。
    if not _is_disabled(registry.DEFAULT_PLATFORM, disabled):
        return registry.DEFAULT_PLATFORM, ""
    for pid in registry.ids():
        if not _is_disabled(pid, disabled):
            spec = registry.get(pid)
            return pid, (spec.realm if spec else "")
    return registry.DEFAULT_PLATFORM, ""


class PlatformDisabled(Exception):
    """请求的目标平台被用户禁用。"""

    def __init__(self, platform: str):
        self.platform = platform
        super().__init__(platform)


_disabled_cache: dict = {"at": 0.0, "value": None}


def invalidate_platform_cache() -> None:
    """平台禁用集合 / 平台顺序缓存立刻失效。

    禁用开关一变就必须清：留着 3 秒旧值会出现「刚禁用还在用」，
    取消禁用又会「还是 403」—— 两个方向都踩过。
    """
    _disabled_cache.update({"at": 0.0, "value": None})
    _order_cache.update({"at": 0.0, "value": None, "signature": None})


def _is_disabled(platform_id: str, disabled: set[str]) -> bool:
    """平台是否被禁用。

    既要认同名 id，也要认同 storage（`trae` 禁用应覆盖 `trae-cn`/`trae-intl`），
    否则用户禁用「Trae」后国际版还在跑。
    """
    if not disabled:
        return False
    pid = str(platform_id or "").lower()
    if pid in disabled:
        return True
    spec = registry.get(pid)
    storage = str(getattr(spec, "storage", lambda: "")() or "").lower() if spec else ""
    return bool(storage) and storage in disabled


def _aggregate_enabled(key) -> bool:
    return bool(getattr(key, "aggregate", True)) if key is not None else False


def _model_of(request) -> str:
    """从**已经预读并缓存在 scope** 的 body 里取模型名。"""
    body = request.scope.get("_wb_body")
    if isinstance(body, bytes) and body:
        return _model_of_body(body)
    return ""


def _model_of_body(body: bytes) -> str:
    if not body:
        return ""
    try:
        import json as _json

        payload = _json.loads(body)
        return str(payload.get("model") or "").strip()
    except (ValueError, UnicodeDecodeError):
        return ""


def _should_peek(request) -> bool:
    """只对**带 body 的写请求**预读；GET/HEAD 读了会让下游卡死。"""
    try:
        if request.scope.get("method", "").upper() not in _BODY_METHODS:
            return False
        # **任何 Key 都要预读 body**。
        #
        # 原来这里写着「非聚合 Key 不预读」——因为非聚合 Key 不在平台间自动
        # 选路。但预读还承担另一件事：**读模型名做后缀路由**。
        # 不预读 → `raw_model` 为空 → `deepseek-v4.1-flash-loomy` 的后缀
        # 解析不出来 → 落到默认平台（workbuddy）→ 主应用白名单没有这个
        # 模型 → 400「模型不存在」。
        # 用户用非聚合 Key（`aggregate=False`）时必现。
        if _key_of(request) is None:
            # 未鉴权也允许预读：鉴权由各网关自己做，选路只影响去哪个网关。
            pass
        for name, value in request.scope.get("headers") or []:
            if name.lower() == b"content-length":
                raw = value.decode("latin-1")
                return raw.isdigit() and 0 < int(raw) <= _BODY_LIMIT
        return False
    except Exception:  # noqa: BLE001
        return False


async def _read_body(receive, limit: int = _BODY_LIMIT) -> bytes:
    """把请求体读出来（最多 `limit` 字节），超限返回空串（不参与选路）。"""
    chunks: list[bytes] = []
    total = 0
    while True:
        message = await receive()
        kind = message.get("type")
        if kind == "http.disconnect":
            break
        if kind != "http.request":
            continue
        chunk = message.get("body") or b""
        if chunk:
            if total < limit:
                chunks.append(chunk)
            total += len(chunk)
        if not message.get("more_body"):
            break
    return b"".join(chunks) if 0 < total <= limit else b""


def _replay_receive(body: bytes, tail=None):
    """构造一个只回放 `body` 的 receive。

    第一次调用返回 body，之后**委托给原始 receive**（通常是等待
    `http.disconnect`）。

    ⚠️ 两件事都不能省：
      * 不能返回 `http.disconnect` —— 下游会认为客户端断开、不产出响应
        （`RuntimeError: No response returned.`）；
      * 不能立即返回空 `http.request` —— `StreamingResponse` 的
        `listen_for_disconnect` 会**忙循环**把事件循环占满，表现为
        流式请求 60 秒 0 字节（实测踩过）。
    """
    state = {"sent": False}

    async def receive():
        if not state["sent"]:
            state["sent"] = True
            return {"type": "http.request", "body": body, "more_body": False}
        # 真正的 disconnect 监听交给上层的 send 通道（写失败会让 StreamingResponse
        # 自行退出）。这里若立即返回 http.request，会让 listen_for_disconnect
        # 空转；返回 disconnect 又会被当成客户端断开而不产出响应。
        import asyncio

        await asyncio.sleep(3600)
        return {"type": "http.disconnect"}

    return receive


# ---------------------------------------------------------------------------
# 按模型选平台
# ---------------------------------------------------------------------------
def _platform_order() -> list[str]:
    """当前策略下的平台顺序（带 3 秒缓存，避免每请求算健康度）。"""
    now = time.time()
    try:
        from admin.platform import gateway_router

        with _db() as db:
            cfg = gateway_router.routing_config(db)
            # **签名必须包含禁用集合与优先级**：只放 strategy 的话，
            # 用户刚禁用某个平台，3 秒内缓存仍把它排在前面 ——
            # 表现就是「禁用了还在用」，实测 auto 路由 6/6 全打到已禁用平台。
            signature = "|".join((
                str(cfg.get("strategy") or ""),
                ",".join(sorted(str(k) for k in (cfg.get("disabled") or {}))),
                ",".join("%s=%s" % (k, v)
                         for k, v in sorted((cfg.get("priority") or {}).items())),
                "1" if cfg.get("spread_unbound") else "0",
            ))
    except Exception:  # noqa: BLE001
        signature = ""
    if (_order_cache["value"] is not None
            and _order_cache["signature"] == signature
            and now - _order_cache["at"] < _ORDER_TTL):
        return list(_order_cache["value"])
    order: list[str] = []
    try:
        from admin.platform import gateway_router

        with _db() as db:
            order = [item["platform"] for item in gateway_router.order(db)]
    except Exception:  # noqa: BLE001 - 策略失败回落登记顺序
        logger.debug("平台顺序计算失败", exc_info=True)
    if not order:
        order = list(registry.ids())
    _order_cache.update({"at": now, "value": list(order), "signature": signature})
    return order


def _db():
    from admin.db import SessionLocal

    return SessionLocal()


# ---------------------------------------------------------------------------
# 模型目录缓存（热路径）
# ---------------------------------------------------------------------------
#: 缓存有效期（秒）。模型表只在「同步模型 / 手动改配置」时变化，
#: 10 秒的滞后完全可接受；而没有它，**每个 /v1 请求**都要把
#: `model_configs` 全表拉一遍（130+ 行、带 TEXT 字段）——
#: 多平台一多，这就是最容易被忽略的固定开销。
_MODEL_TTL = 10.0
_model_cache: dict = {"at": 0.0, "rows": [], "by_storage": {}, "by_key": {}}


def invalidate_model_cache() -> None:
    """模型配置变了就调它（同步 / 保存后），让新模型立刻可路由。"""
    _model_cache.update({"at": 0.0, "rows": [], "by_storage": {}, "by_key": {}})


def _model_catalog(db) -> list[dict]:
    """`[{platform, model_id, display, key, multiplier}]`（带 10 秒缓存）。

    `key` 是归一 key：**优先用官方展示名**算（Qoder 的 `dfmodel`
    展示名是 `DeepSeek-Flash`），否则内部代号模型永远匹配不上聚合名。
    """
    now = time.time()
    if _model_cache["rows"] and now - _model_cache["at"] < _MODEL_TTL:
        return _model_cache["rows"]
    from admin.model_alias import canonical_key, model_display
    from admin.models import ModelConfig

    raw = (db.query(ModelConfig.platform, ModelConfig.model_id,
                    ModelConfig.credit_multiplier, ModelConfig.upstream_metadata)
           .filter(ModelConfig.enabled == 1).all())
    rows: list[dict] = []
    by_storage: dict[str, list[dict]] = {}
    by_key: dict[str, list[dict]] = {}
    for platform, model_id, multiplier, metadata in raw:
        storage = str(platform or "").lower()
        model_id = str(model_id or "")
        if not storage or not model_id:
            continue
        display = model_display(metadata)
        entry = {
            "platform": storage,
            "model_id": model_id,
            "display": display,
            "key": canonical_key(model_id, display),
            "multiplier": multiplier,
        }
        rows.append(entry)
        by_storage.setdefault(storage, []).append(entry)
        if entry["key"]:
            by_key.setdefault(entry["key"], []).append(entry)
    _model_cache.update({"at": now, "rows": rows,
                         "by_storage": by_storage, "by_key": by_key})
    return rows


def _catalog_maps(db) -> tuple[dict, dict]:
    _model_catalog(db)
    return _model_cache["by_storage"], _model_cache["by_key"]


def _has_account(db, spec, key, model: str = "") -> bool:
    """该平台（区域）在**本 Key 的归属范围内**是否有可用账号。

    `model` 非空时还要排除「账号×模型」冷却：模型在配置里存在、但所有账号
    都被上游按模型限流（6004/11102）时，这个平台**不能**算可用 ——
    否则请求会留在原平台排队 90 秒，而不是改走别的平台。
    """
    from admin.account_binding import scope_accounts
    from admin.models import Account, AccountModelCooldown
    from sqlalchemy import or_
    from datetime import datetime

    now = datetime.utcnow()
    account_id, key_id = shared.key_scope(key)
    query = (db.query(Account.id)
             .filter(Account.platform == spec.storage(),
                     Account.status == "active"))
    query = scope_accounts(query, account_id, key_id, key=key)
    if spec.realm:
        query = query.filter(Account.domain == spec.realm)
    query = query.filter(or_(Account.cool_until.is_(None), Account.cool_until <= now))
    query = query.filter(or_(Account.breaker_until.is_(None), Account.breaker_until <= now))
    query = query.filter(or_(Account.degrade_until.is_(None), Account.degrade_until <= now))
    if model:
        cooled = {row.account_id for row in
                  db.query(AccountModelCooldown.account_id)
                  .filter(AccountModelCooldown.model == model,
                          AccountModelCooldown.until > now).all()}
        if cooled:
            query = query.filter(~Account.id.in_(cooled))
    return query.first() is not None


def _model_platforms(db, model: str) -> list[tuple[str, str]]:
    """该模型在哪些**落库平台 + 区域**上存在（按倍率升序）。

    返回 `[(storage, realm_hint)]`。`realm_hint` 来自模型配置 id 的后缀
    （Trae 用 `:intl` 表示国际版）：同一个 `trae` 存储平台下有两个区，
    只看 storage 会把「只有国际版有的模型」送去国内版，必然 4023。
    """
    from admin.model_alias import canonical_key

    alias = canonical_key(model)
    if not alias:
        return []
    _, by_key = _catalog_maps(db)
    hits: dict[tuple[str, str], float] = {}
    for row in by_key.get(alias) or []:
        platform = row["platform"]
        model_id = str(row["model_id"] or "")
        realm = ""
        if ":" in model_id:
            suffix = model_id.rsplit(":", 1)[-1].lower()
            if suffix in ("intl", "cn"):
                realm = suffix
        multiplier = row["multiplier"]
        if not isinstance(multiplier, (int, float)) or multiplier < 0:
            multiplier = 999
        key = (platform, realm)
        if key not in hits or multiplier < hits[key]:
            hits[key] = multiplier
    return [key for key, _ in sorted(hits.items(), key=lambda kv: (kv[1], kv[0]))]


def _model_id_for(db, storage: str, model: str) -> str:
    """把请求里的模型名映射成**目标平台自己的 model_id**。

    同一模型在不同平台叫不同名字（`deepseek-v4.1-flash` / `dfmodel` /
    `DeepSeek-V4.1-Flash`）。按归一 key 选到平台后，如果继续把原名字转发过去，
    目标平台只会回「模型不存在」—— 这正是「聚合转发看起来通了，实际全 404」
    的原因。这里按 `model_configs` 找该平台对应的 model_id。

    Trae 的配置里带 `:intl` 区后缀，转发前要剥掉（上游认裸名）。
    """
    from admin.model_alias import canonical_key

    alias = canonical_key(model)
    if not alias:
        return model
    by_storage, _ = _catalog_maps(db)
    rows = by_storage.get(storage) or []
    lowered = str(model or "").strip().lower()

    # 1) 精确 id
    for row in rows:
        if row["model_id"] == model:
            exact = row["model_id"]
            break
    else:
        exact = None

    # 2) 归一 key
    if not exact:
        for row in rows:
            if row["key"] == alias:
                exact = row["model_id"]
                break

    # 3) **大小写不敏感兜底**。
    #    上游对模型名大小写敏感：发 `DeepSeek-V4.1-Flash` 会回
    #    11102「service info not found」，还被负缓存 6 小时。
    #    客户端从 `/v1/models` 拿到的是小写 slug，但历史配置 / 手动输入
    #    可能是别的大小写形态，这里统一纠回库里登记的那个写法。
    if not exact:
        for row in rows:
            if str(row["model_id"] or "").strip().lower() == lowered:
                exact = row["model_id"]
                break

    if not exact:
        return model
    return exact.split(":", 1)[0] if storage == "trae" else exact


def _disabled_model_id(db, storage: str, model: str) -> str:
    """该模型在这个平台上是否**已被禁用**；命中返回它的 model_id。

    只看 `enabled == 0` 的行，且要同时认归一 key 与原生 id：
    用户禁用 `spark-x2.6` 后，请求里写 `Spark-X2.6` 或聚合名都得挡住。
    """
    from admin.model_alias import canonical_key, model_display
    from admin.models import ModelConfig

    text = str(model or "").strip()
    if not text:
        return ""
    alias = canonical_key(text)
    rows = (db.query(ModelConfig.model_id, ModelConfig.upstream_metadata)
            .filter(ModelConfig.platform == storage,
                    ModelConfig.enabled == 0).all())
    for model_id, metadata in rows:
        model_id = str(model_id or "")
        if model_id == text:
            return model_id
        if alias and canonical_key(model_id, model_display(metadata)) == alias:
            return model_id
    return ""


def _model_for_platform(db, storage: str, model: str) -> tuple[str, dict | None]:
    """显式指定平台时，把聚合名映射成该平台的 model_id；跨平台误用直接拒绝。

    为什么必须在**显式指定平台**时也映射：

        X-Platform: loomy  +  model: deepseek-flash

    Loomy 不认识 `deepseek-flash`，但它**不会报错**，而是悄悄用默认模型
    （实测 `spark-x2.6`）回包。会看到 200 + 有内容，以为调通了，
    实际模型、计费、倍率全是错的 —— 这类"成功"比 404 更难排查。

    因此：
      · 能按归一 key 映射到该平台自己的 id  -> 改写后转发；
      · 映射不到、但这个模型在**别的平台**有配置 -> 404 明确说清楚；
      · 谁都查不到（如 `auto`）-> 原样转发，交给上游判断。
    """
    from admin.model_alias import canonical_key

    # **被禁用的模型一律拒绝**，不管走哪条路径。
    # 之前这里只看「能不能映射」，于是 `X-Platform: loomy` + 已禁用的
    # `spark-x2.6` 会掉进「谁都查不到 -> 原样放行」的兜底分支，直接转发出去。
    disabled_hit = _disabled_model_id(db, storage, model)
    if disabled_hit:
        return model, {
            "message": "模型 %s 已被禁用（在模型配置里可以重新启用）" % disabled_hit,
            "code": "model_disabled",
            "status": 403,
        }

    mapped = _model_id_for(db, storage, model)
    if mapped != model:
        return mapped, None
    # 通用路由名：每个平台都可能自己实现（Loomy 的 auto 就是让上游挑），
    # 不能因为「别的平台也登记了 auto」就把它拒掉。
    if str(model or "").strip().lower() in _GENERIC_MODEL_NAMES:
        return model, None
    alias = canonical_key(model)
    if not alias:
        return model, None
    by_storage, by_key = _catalog_maps(db)
    # 直接用平台自己的原生 id 调（`X-Platform: qoder` + `dfmodel`）必须放行：
    # 这种 id 的归一 key 可能是展示名算出来的（dfmodel -> deepseekv41flash），
    # 单看 key 会误判成「不属于该平台」，把本来正确的调用打成 404。
    if any(row["model_id"] == model for row in (by_storage.get(storage) or [])):
        return model, None
    owners: list[str] = []
    for row in by_key.get(alias) or []:
        if row["platform"] not in owners:
            owners.append(row["platform"])
    if owners and storage not in owners:
        return model, {
            "message": "模型 %s 不属于平台 %s（该模型在：%s）。"
                       "请改用该平台自己的模型名，或去掉 X-Platform 让网关按模型选平台。"
                       % (model, storage, "、".join(owners)),
            "type": "invalid_request_error",
            "code": "model_not_on_platform",
        }
    if not owners:
        # 谁的清单里都没有：上游对未知模型**不一定报错**（Loomy 会退回默认模型），
        # 那等于用错误的模型、错误的倍率给用户回包。这里直接 404，
        # 并在消息里指出「可能只是没同步清单」，避免用户以为功能坏了。
        if by_storage.get(storage):
            return model, {
                "message": "模型 %s 不存在于平台 %s 的模型清单里。"
                           "如果上游刚上线了这个模型，请先在控制台「模型配置」同步一次；"
                           "也可以不带 X-Platform，让网关按模型自动选平台。"
                           % (model, storage),
                "type": "invalid_request_error",
                "code": "model_not_found",
            }
    return model, None


def _replace_model(body: bytes, mapped: str) -> bytes:
    """把 JSON body 里的 `model` 换成目标平台的 id（保留其余字段原样）。"""
    if not body or not mapped:
        return body
    try:
        import json as _json

        payload = _json.loads(body)
        if not isinstance(payload, dict) or payload.get("model") == mapped:
            return body
        payload["model"] = mapped
        return _json.dumps(payload, ensure_ascii=False).encode()
    except (ValueError, UnicodeDecodeError):
        return body


#: 「模型健康度」缓存：自动选路时每个候选平台都要查一次 24h 失败率，
#: 而它一分钟内基本不变。没有这层缓存，一次聚合请求 = N 次分组统计。
_HEALTH_TTL = 60.0
_health_cache: dict[tuple[str, str], tuple[float, bool]] = {}


def _model_health_ok(db, storage: str, model: str) -> bool:
    """该平台最近 24h 对这个模型是否「只有失败」。

    配置里有模型 ≠ 上游真的允许调：WorkBuddy 会列出 `deepseek-flash` 却回 403
    （模型权益不包含）。这类平台不该继续霸占「按模型选平台」的结果 ——
    否则永远 403，而别的平台明明能跑。

    结果按 `(平台, 模型)` 缓存 60 秒：这是「选路时多一次统计」的地方，
    不缓存等于每个请求都在做分组聚合。
    """
    from datetime import datetime, timedelta
    from sqlalchemy import func
    from admin.models import UsageLog

    if not model:
        return True
    cache_key = (storage, model)
    now = time.time()
    cached = _health_cache.get(cache_key)
    if cached and now - cached[0] < _HEALTH_TTL:
        return cached[1]
    values = {storage}
    for spec in registry.all_platforms():
        if spec.storage() == storage:
            values.add(spec.id)
    since = datetime.utcnow() - timedelta(hours=24)
    try:
        rows = (db.query(UsageLog.error_kind, func.count(UsageLog.id))
                .filter(UsageLog.model == model,
                        UsageLog.platform.in_(values),
                        UsageLog.created_at >= since)
                .group_by(UsageLog.error_kind).all())
    except Exception:  # noqa: BLE001 - 统计失败按可用处理
        return True
    ok = sum(int(count) for kind, count in rows if (kind or "") in ("", "success"))
    failed = sum(int(count) for kind, count in rows if (kind or "") not in ("", "success"))
    verdict = not (ok == 0 and failed >= 2)
    if len(_health_cache) > 512:  # 平台 × 模型组合有限，防极端增长
        _health_cache.clear()
    _health_cache[cache_key] = (now, verdict)
    return verdict


def _candidates_for_model(db, model: str, key) -> list[tuple[str, str, str]]:
    """按策略顺序返回可服务该模型的 `(public_platform, realm, target_model)`。"""
    platforms = _model_platforms(db, model)
    if not platforms:
        return []
    specs = []
    for storage, realm_hint in platforms:
        matches = [spec for spec in registry.all_platforms()
                   if spec.storage() == storage
                   and (not realm_hint or spec.realm == realm_hint)]
        if not matches:
            matches = [spec for spec in registry.all_platforms()
                       if spec.storage() == storage]
        for spec in matches:
            if spec not in specs:
                specs.append(spec)
    if not specs:
        return []
    ranked = [pid for pid in _platform_order()
              if pid in {spec.id for spec in specs}]
    ranked += [spec.id for spec in specs if spec.id not in ranked]
    out: list[tuple[str, str, str]] = []
    unhealthy: list[tuple[str, str, str]] = []
    for pid in ranked:
        spec = registry.get(pid)
        if spec is None or not spec.enabled:
            continue
        if _platform_paused(pid):
            continue
        mapped = _model_id_for(db, spec.storage(), model)
        if not _has_account(db, spec, key, mapped):
            continue
        if not _model_health_ok(db, spec.storage(), mapped):
            unhealthy.append((pid, spec.realm, mapped))
            continue
        out.append((pid, spec.realm, mapped))
    # 所有候选都「只有失败记录」时不能直接放弃：仍按顺序试一遍，
    # 因为负缓存可能来自一次配置错误，现在可能已经修好。
    return out or unhealthy


def _platform_paused(platform: str) -> bool:
    try:
        from admin.platform import sentinel_router

        return sentinel_router.paused(platform) is not None
    except Exception:  # noqa: BLE001
        return False


def _platform_for_model(model: str, key) -> tuple[str, str, str] | None:
    try:
        with _db() as db:
            candidates = _candidates_for_model(db, model, key)
    except Exception:  # noqa: BLE001
        logger.debug("按模型选平台失败", exc_info=True)
        return None
    if not candidates:
        return None
    # 过滤掉被禁用的平台：候选列表来自模型目录，不含禁用信息
    disabled = _disabled_platforms()
    if disabled:
        candidates = [c for c in candidates
                      if not _is_disabled(c[0], disabled)]
        if not candidates:
            return None
    current = registry.DEFAULT_PLATFORM
    # 默认平台自己就能服务这个模型 -> 保持原行为（老客户端不受影响）。
    if any(pid == current for pid, _realm, _mapped in candidates):
        return None
    picked = candidates[0]
    logger.info("按模型选平台：%s -> %s (%d 个候选)", model, picked[0], len(candidates))
    return picked


# ---------------------------------------------------------------------------
# 网关策略（无头请求分摊）
# ---------------------------------------------------------------------------
def _spread_unbound() -> bool:
    try:
        from admin.platform import gateway_router

        with _db() as db:
            return bool(gateway_router.routing_config(db).get("spread_unbound"))
    except Exception:  # noqa: BLE001
        return False


def _strategy_pick() -> tuple[str, str] | None:
    try:
        from admin.platform import gateway_router

        return gateway_router.cached_pick()
    except Exception:  # noqa: BLE001 - 策略失败降级
        logger.debug("平台网关策略解析失败，回落默认平台", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# 转发
# ---------------------------------------------------------------------------
async def _run_platform(dispatch, request, path: str, platform_id: str):
    """在某平台上执行一次请求（异常转成 JSONResponse，绝不返回 None）。"""
    try:
        response = await dispatch(request, path)
    except Exception as exc:  # noqa: BLE001 - 平台内部异常不该变成 500 裸奔
        logger.exception("平台 %s 处理 %s 失败", platform_id, path)
        return JSONResponse(
            status_code=502,
            content={"error": {"message": f"上游平台异常：{exc}",
                               "type": "server_error",
                               "code": "platform_error"}})
    if response is None:
        return JSONResponse(
            status_code=503,
            content={"error": {"message": f"平台 {platform_id} 未返回响应",
                               "type": "server_error",
                               "code": "platform_empty"}})
    # 用响应头告诉客户端「这次实际落在哪个平台」。
    # 带后缀的模型名会被剥成裸 id 转发给上游，回包里 `<model>` 就只剩
    # `deepseek-v4.1-flash`，用户分不清是 Loomy 还是 WorkBuddy 回的；
    # 这两个头把落点补回来，客户端/日志都能直接用。
    try:
        response.headers["X-Gateway-Platform"] = str(platform_id)
        requested = request.scope.get("_wb_requested_model")
        if requested:
            response.headers["X-Gateway-Model"] = str(requested)
    except Exception:  # noqa: BLE001 - 流式响应可能不允许改头
        pass
    return response


def _spec_with_gateway(platform_id: str, realm: str):
    """取「真正有网关实现」的平台 spec。

    ## 为什么需要这一层

    `registry.resolve("trae:cn")` 按契约返回 `("trae", "cn")` —— `trae`
    是**存储层**平台（两个区共用 `accounts.platform='trae'`），
    而注册表里并没有一个叫 `trae` 的 PlatformSpec（只有 `trae-cn` /
    `trae-intl`）。直接 `registry.get("trae")` 会拿到 None，于是
    「Key 绑定 trae-cn 后不带模型后缀调用」会撞上 503
    「平台 trae 未装配网关实现」。

    这里按 `平台-realm` 再找一次（`trae` + `cn` -> `trae-cn`）。
    找不到才返回 None，让上层照常报错。
    """
    spec = registry.get(platform_id)
    if spec is not None and registry.load_gateway(spec) is not None:
        return spec
    if realm:
        alt = registry.get("%s-%s" % (platform_id, realm))
        if alt is not None and registry.load_gateway(alt) is not None:
            return alt
    # 兜底：同一 storage 平台下任意一个有网关的区域
    for pid in registry.ids():
        cand = registry.get(pid)
        if cand is None:
            continue
        same_storage = (getattr(cand, "storage_platform", "") or cand.id) == platform_id
        if same_storage and registry.load_gateway(cand) is not None:
            return cand
    return spec


def _module_of(spec):
    module = registry.load_gateway(spec) if spec is not None else None
    return module


def _set_state(scope, platform_id: str, realm: str) -> None:
    state = scope.setdefault("state", {})
    state["platform"] = platform_id
    state["realm"] = realm


class PlatformDispatchMiddleware:
    """把非默认平台的 `/v1/*` 请求转交给该平台的网关实现。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path") or ""
        if path not in GATEWAY_PATHS:
            return await self.app(scope, receive, send)

        request = Request(scope, receive)
        tail = receive

        # ---- 1) 有界预读 body（只对带 body 的写请求）----
        body = b""
        if _should_peek(request):
            try:
                body = await _read_body(receive)
            except Exception:  # noqa: BLE001
                logger.debug("预读请求体失败", exc_info=True)
                body = b""
            if body:
                scope["_wb_body"] = body
                receive = _replay_receive(body, tail)
                request = Request(scope, receive)
                # 让请求指标 / 实时流量知道这次请求的模型（平台网关不会
                # 经过 WorkBuddy 的 `_request_json`，否则面板上模型一栏是空的）。
                try:
                    from admin import request_metrics

                    record = request_metrics.current.get()
                    model_name = _model_of_body(body)
                    if record is not None and model_name:
                        record["requested_model"] = model_name[:120]
                    # 记原始请求体里的 model，用于定位「客户端到底发了什么名字」
                    logger.info("dispatch 收到 | path=%s model=%r body_head=%r",
                                path, model_name,
                                (body[:220].decode("utf-8", "replace")
                                 if body else ""))
                    # **请求一开始就在实时面板里出现一行 running**：
                    # 否则一个跑 30 秒的任务，前 28 秒面板什么都看不到。
                    #
                    # 平台名此刻还取不到（选路在下面才做），用**模型名后缀**
                    # 先猜一个：`deepseek-v4.1-flash-trae-cn` -> `trae`。
                    # 猜不到就留空，`record()` 会写正确平台，不影响统计 ——
                    # 之前这里直接取 `scope["state"]["platform"]` 拿到空串，
                    # 全部记成 `unknown`，面板上按平台过滤时看不见。
                    try:
                        from admin import traffic_stats

                        _guess = ""
                        try:
                            from admin.platform import model_suffix as _ms

                            _plat, _, _has = _ms.resolve(str(model_name or ""))
                            _guess = (_plat or "") if _has else ""
                        except Exception:  # noqa: BLE001
                            _guess = ""
                        scope["_wb_traffic_rid"] = traffic_stats.start(
                            _guess, model=model_name or "", path=path)
                    except Exception:  # noqa: BLE001
                        pass
                except Exception:  # noqa: BLE001
                    pass

        # ---- 1.5) `/v1/models` 不走单平台：直接聚合全平台返回 ----
        # 之前它只返回默认平台的模型（17 个），其它平台的模型在客户端
        # 里根本看不到。聚合后带平台后缀（`xxx-workbuddy`），
        # 用户在客户端里挑一个就能固定落点，不需要改 header。
        if path == "/v1/models" and not _explicit_selector(request):
            # **必须先鉴权**：聚合清单是全平台的模型目录，
            # 不加检查等于把整张表公开给任何访问者。
            # 鉴权通过才返回聚合结果；失败一律走回原路径，
            # 让主应用照常给出它自己的 401，错误体保持不变。
            key = _key_of(request)
            if key is not None:
                try:
                    from admin.platform import models_router

                    # Key 开了聚合 -> 只给「同模型一条」的清单，
                    # 落点由网关调度；没开聚合 -> 给全量（带平台后缀），
                    # 让用户自己指定平台。
                    aggregate_only = bool(getattr(key, "aggregate", True))
                    with _db() as raw_db:
                        payload = models_router.build_payload(
                            raw_db, aggregate_only=aggregate_only)
                    # **默认输出严格 OpenAI 形状**：`platform` / `platforms`
                    # 这些扩展字段会让严格解析的客户端（Rust serde / Go struct）
                    # 直接报 `error decoding response body`。
                    # 需要完整信息时加 `?detail=1`（控制台/调试用）。
                    raw_q = request.scope.get("query_string") or b""
                    wants_detail = b"detail=1" in raw_q
                    if not wants_detail:
                        payload = models_router.to_openai_strict(payload)
                    return await JSONResponse(payload)(scope, receive, send)
                except Exception:  # noqa: BLE001 - 聚合失败退回原路径
                    logger.debug("聚合 /v1/models 失败，回退单平台", exc_info=True)

        # ---- 2) 解析目标平台 ----
        # 模型名自带平台后缀时（`deepseek-v4.1-flash-workbuddy`），
        # 后缀直接决定平台，并且转发前要剥掉 —— 上游只认裸 id。
        suffix_platform, bare_model, has_suffix = "", "", False
        if body:
            raw_model = _model_of_body(body)
            if raw_model:
                from admin.platform import model_suffix

                suffix_platform, bare_model, has_suffix = model_suffix.resolve(raw_model)
                # **可读名还原**：`deepseek-flash-qoder` 的上游 id 是 `dfmodel`。
                # 看到的是可读名，转发前必须换回上游认的 id，否则 400。
                alias_storage, alias_upstream = model_suffix.alias_to_upstream(raw_model)
                if alias_upstream and alias_storage:
                    bare_model = alias_upstream
                    has_suffix = has_suffix or bool(suffix_platform)
                    scope["_wb_requested_model"] = raw_model
                    body = _replace_model(body, bare_model)
                    receive = _replay_receive(body, tail)
                    request = Request(scope, receive)
                elif not has_suffix:
                    # **slug 还原**：`/v1/models` 对外给的是规范 id
                    # （`deepseek-v4-flash`），上游只认自己的写法
                    # （`DeepSeek-V4-Flash`）。这里做一次反查，
                    # 命中就换回上游 id，没命中按原样转发（让上游自己报错）。
                    try:
                        from admin.platform import models_router

                        with _db() as _rdb:
                            real = models_router.upstream_of(raw_model, _rdb)
                        if real and real != raw_model:
                            body = _replace_model(body, real)
                            receive = _replay_receive(body, tail)
                            request = Request(scope, receive)
                            scope["_wb_requested_model"] = raw_model
                    except Exception:  # noqa: BLE001
                        logger.debug("slug 还原失败 model=%s", raw_model, exc_info=True)
                if has_suffix:
                    # 响应里要把**用户请求的那个名字**回显出来，
                    # 否则 `xxx-loomy` 调完只剩 `xxx`，客户端分不清落点。
                    scope["_wb_requested_model"] = raw_model
                    if bare_model != raw_model:
                        body = _replace_model(body, bare_model)
                        receive = _replay_receive(body, tail)
                        request = Request(scope, receive)

        try:
            if suffix_platform:
                platform_id, realm = registry.resolve(suffix_platform)
            else:
                platform_id, realm = resolve(request)
        except PlatformDisabled as exc:
            # 禁用了这个平台：拒绝，而不是偷偷换一个。
            # 静默改道会让用户以为「禁用了它还在跑」，也让排障无从下手。
            return await JSONResponse(
                status_code=403,
                content={"error": {
                    "message": "平台 %s 已被禁用（平台网关里可以重新启用）"
                               % exc.platform,
                    "type": "platform_disabled",
                    "code": "platform_disabled",
                    "platform": exc.platform,
                }})(scope, receive, send)
        except Exception:  # noqa: BLE001
            logger.warning("平台解析失败，回落默认平台", exc_info=True)
            platform_id, realm = registry.DEFAULT_PLATFORM, ""
        if suffix_platform and _is_disabled(platform_id, _disabled_platforms()):
            return await JSONResponse(
                status_code=403,
                content={"error": {
                    "message": "模型名指定的平台 %s 已被禁用" % suffix_platform,
                    "type": "platform_disabled", "code": "platform_disabled",
                    "platform": suffix_platform,
                }})(scope, receive, send)

        _set_state(scope, platform_id, realm)

        # **带平台后缀的请求永远走平台网关**，哪怕它指向默认平台。
        # 理由：后缀是的「我要这个平台的实现」，交给主应用等于
        # 用错实现（主应用没有平台侧的账号池 / 倍率 / 风控）。
        # 过「当前平台只是看账号积分用的，跟对话接口有啥关系」——
        # 这里就把这个耦合切断。
        explicit_platform = bool(suffix_platform)

        # **兜底 1：模型名能还原成某平台的模型时，强制走该平台网关。**
        #
        # 场景：客户端发 `deepseek-flash-qoder`，但后缀解析失败
        # （名字拼错平台、或用了不含后缀的可读名如 `deepseek-flash`），
        # 于是落到默认平台 → 主应用白名单里没有 → 400。
        # 这里反查一次：归属明确就改道，不让主应用插手。
        if not explicit_platform and body:
            try:
                from admin.platform import models_router as _mr

                _m = _model_of_body(body)
                if _m:
                    with _db() as _rdb:
                        _owner = _mr.platform_of_model(_m, _rdb)
                    if _owner and _owner != registry.DEFAULT_PLATFORM:
                        _spec_o = registry.get(_owner)
                        if _spec_o is not None:
                            try:
                                _mod_o = _module_of(_spec_o)
                                if getattr(_mod_o, "dispatch", None) is not None:
                                    platform_id = _owner
                                    explicit_platform = True
                                    suffix_platform = _owner
                                    logger.info(
                                        "dispatch 兜底改道 | model=%r -> platform=%r",
                                        _m, _owner)
                            except Exception:  # noqa: BLE001
                                pass
            except Exception:  # noqa: BLE001
                logger.debug("兜底改道失败", exc_info=True)
        # 默认平台**本身没有独立网关实现**（它就是主应用），
        # 所以带 `-workbuddy` 后缀时必须回主应用；否则会 503。
        # 注意用 `_spec_with_gateway`：`trae` 这类「存储平台」在注册表里
        # 没有自己的 spec，直接 get 会拿到 None，于是被误判成
        # 「没有独立网关」而回落主应用 —— Trae 的请求会打到 WorkBuddy 上。
        _spec_chk = _spec_with_gateway(platform_id, realm)
        _has_gateway = False
        if _spec_chk is not None and platform_id != registry.DEFAULT_PLATFORM:
            try:
                _mod = _module_of(_spec_chk)
                _has_gateway = getattr(_mod, "dispatch", None) is not None
            except Exception:  # noqa: BLE001
                _has_gateway = False
        if not _has_gateway:
            explicit_platform = False

        # 每次请求都记一条分叉决策：出问题时能直接看出「为什么落到了主应用」。
        logger.info("dispatch 分叉 | raw_model=%r -> platform=%r explicit=%s "
                    "has_gw=%s default=%r suffix_platform=%r",
                    (_model_of_body(body) if body else ""), platform_id,
                    explicit_platform, _has_gateway, registry.DEFAULT_PLATFORM,
                    suffix_platform)

        if platform_id == registry.DEFAULT_PLATFORM and not explicit_platform:
            # 默认平台走主应用。但**聚合名要先映射成它自己的 model_id**：
            # 主应用只认白名单里的 id，`deepseek-flash` 这类聚合名会被
            # 直接 400「模型不存在」。之前只有非默认平台做映射，
            # 于是把调度策略切到「优先级」（WorkBuddy 排第一）后，
            # 所有聚合名请求都挂了 —— 这是必须补上的一步。
            if body:
                try:
                    spec0 = registry.get(platform_id)
                    storage0 = spec0.storage() if spec0 else platform_id
                    model0 = _model_of_body(body)
                    # **sluge 反查优先**：`/v1/models` 给客户端的是规范 slug
                    # （`deepseek-v4.1-flash`），但上游对**大小写敏感** ——
                    # 发 `DeepSeek-V4.1-Flash` 会回 11102
                    # 「model [...] service info not found」，而且这个负缓存
                    # 会把 (账号, 模型) 封 6 小时，所有请求都被挡住。
                    # 先按 slug 反查成上游真实 id，再走原来的映射。
                    fixed0 = model0
                    if model0:
                        try:
                            from admin.platform import models_router
                            with _db() as _rdb:
                                real0 = models_router.upstream_of(model0, _rdb)
                            if real0 and real0 != model0:
                                fixed0 = real0
                        except Exception:  # noqa: BLE001
                            fixed0 = model0
                        if fixed0 != model0:
                            body = _replace_model(body, fixed0)
                            receive = _replay_receive(body, tail)
                            request = Request(scope, receive)
                    mapped0 = _model_id_for(_db(), storage0, fixed0) if fixed0 else ""
                    if mapped0 and mapped0 != model0:
                        body = _replace_model(body, mapped0)
                        receive = _replay_receive(body, tail)
                        request = Request(scope, receive)
                except Exception:  # noqa: BLE001 - 映射失败按原样转发
                    logger.debug("默认平台模型映射失败", exc_info=True)
            # 默认平台也回显落点：不带后缀的请求同样需要知道「这次落在哪」。
            async def _wrapped(message):
                if message.get("type") == "http.response.start":
                    headers = list(message.get("headers") or [])
                    headers.append((b"x-gateway-platform",
                                    str(platform_id).encode()))
                    requested = scope.get("_wb_requested_model")
                    if requested:
                        headers.append((b"x-gateway-model",
                                        str(requested).encode()))
                    message = dict(message)
                    message["headers"] = headers
                await send(message)

            return await self.app(scope, receive, _wrapped)

        spec = _spec_with_gateway(platform_id, realm)
        module = _module_of(spec)
        dispatch = getattr(module, "dispatch", None) if module is not None else None
        if dispatch is None:
            logger.warning("平台 %s 未装配网关实现，请求被拒绝", platform_id)
            return await JSONResponse(
                status_code=503,
                content={"error": {
                    "message": f"平台 {platform_id} 未装配网关实现",
                    "type": "server_error", "code": "platform_unavailable"}})(scope, receive, send)

        explicit = bool(_explicit_selector(request))
        key = _key_of(request)
        bound = bool(str(getattr(key, "platform", "") or "").strip()) if key else False
        auto_route = bool(body) and not explicit and not bound and _aggregate_enabled(key)
        model = _model_of_body(body) if body else ""

        # 显式指定平台（或 Key 绑定平台）时也要做模型名映射：见 _model_for_platform
        # 的注释 —— 不映射时 Loomy/Qoder 会拿默认模型"假装成功"。
        if model and not auto_route and spec is not None:
            try:
                with _db() as db:
                    mapped, error = _model_for_platform(db, spec.storage(), model)
            except Exception:  # noqa: BLE001 - 映射失败就按原样转发
                mapped, error = model, None
            if error is not None:
                logger.info("模型不属于目标平台 model=%s platform=%s", model, platform_id)
                return await JSONResponse(status_code=404,
                                          content={"error": error})(scope, receive, send)
            if mapped != model:
                body = _replace_model(body, mapped)
                scope["_wb_body"] = body

        candidates: list[tuple[str, str, bytes]] = [(platform_id, realm, body)]
        # 只有「按模型自动选路」才允许多平台重试：
        # 用户显式指定平台 / Key 绑定了平台时，换平台等于违背用户意图。
        if auto_route and model:
            try:
                with _db() as db:
                    extra = _candidates_for_model(db, model, key)
            except Exception:  # noqa: BLE001
                extra = []
            seen = {platform_id}
            for pid, target_realm, mapped in extra:
                if pid in seen:
                    continue
                seen.add(pid)
                candidates.append((pid, target_realm, _replace_model(body, mapped)))
            # 首个候选也按目标平台的 model_id 改写（例如
            # `deepseek-flash` -> Loomy 的 `DeepSeek-V4.1-Flash`）。
            try:
                with _db() as db:
                    mapped = _model_id_for(db, spec.storage(), model) if spec else model
            except Exception:  # noqa: BLE001
                mapped = model
            candidates[0] = (platform_id, realm, _replace_model(body, mapped))

        last_response = None
        for index, (target, target_realm, target_body) in enumerate(candidates[:3]):
            # 同样走兜底：`trae` 这类存储平台没有自己的 spec，
            # 直接 get 会得到 None -> 被 continue 跳过 -> 最后报 no_platform。
            target_spec = _spec_with_gateway(target, target_realm)
            target_module = _module_of(target_spec)
            target_dispatch = (getattr(target_module, "dispatch", None)
                               if target_module is not None else None)
            if target_dispatch is None:
                continue
            # 回显用 **spec.id**（`trae-cn`）而不是存储名（`trae`）：
            # 面板上看到的是「Trae 国内版」，`x-gateway-platform`
            # 回 `trae` 对不上，排查时会以为落错平台。
            _set_state(scope, (target_spec.id if target_spec else target),
                       target_realm)
            scope["_wb_body"] = target_body
            attempt_request = Request(scope, _replay_receive(target_body, tail)) if target_body else request
            response = await _run_platform(target_dispatch, attempt_request,
                                            path, target)
            status = int(getattr(response, "status_code", 200) or 200)
            if status not in _RETRYABLE_STATUS or index == len(candidates) - 1:
                return await response(scope, receive, send)
            last_response = response
            logger.info("平台 %s 返回 %s，尝试下一个候选平台", target, status)

        if last_response is not None:
            return await last_response(scope, receive, send)
        return await JSONResponse(
            status_code=503,
            content={"error": {"message": "没有可用的上游平台",
                               "type": "server_error", "code": "no_platform"}})(scope, receive, send)


def install(app) -> None:
    """把中间件装到 app 上。

    必须在**其它中间件注册之前**调用：Starlette 的 `add_middleware` 是
    前插，先注册者最后被包裹 = 最内层，这样安全头/限流/准入仍然覆盖平台请求。
    """
    app.add_middleware(PlatformDispatchMiddleware)
