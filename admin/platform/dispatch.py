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

import os

import contextlib
import logging
import re
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

#: 预读上限：超过这个体积**不参与**跨平台选路（避免单个请求把内存打爆）。
#:
#: 原来是 256KB，长上下文 / 带图 / 多轮工具调用的请求很容易越线，
#: 越线就静默丢掉平台后缀 → 落到主应用 → 400 模型不存在。
#: 提到 32MB 后，正常对话不会触发这条降级。
_BODY_LIMIT = 262144  # 保留旧名，供外部引用
#: 环境变量 `MIRAIHUB_DISPATCH_PEEK_MAX_BYTES`：未设置 / 非法 → 32 MiB；
#: **0 或负数 = 不设上限**（任何体积都预读、做后缀路由，chunked 也不 413）。
PEEK_MAX_ENV = "MIRAIHUB_DISPATCH_PEEK_MAX_BYTES"
_DEFAULT_BODY_HARD_CAP = 32 * 1024 * 1024


def _peek_cap_from_env() -> int:
    raw = str(os.environ.get(PEEK_MAX_ENV) or "").strip()
    if not raw:
        return _DEFAULT_BODY_HARD_CAP
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return _DEFAULT_BODY_HARD_CAP


_BODY_HARD_CAP = _peek_cap_from_env()

#: 这些状态表示「这个平台做不了」，值得换下一个平台再试。
_RETRYABLE_STATUS = frozenset({404, 502, 503})

#: 「平台内部异常、这次请求结果不确定」的私有标记（只挂在 Response 对象上）。
#:
#: 为什么不能只看状态码：`_run_platform` 把普通异常也转成 502，而 502 恰好
#: 在 `_RETRYABLE_STATUS` 里 —— 候选循环于是把**同一个请求**再交给下一个平台。
#: 但异常发生时第一个平台可能已经向上游发出了请求（副作用已产生：双倍计费、
#: 群里收到两条消息）。平台**明确**返回的 502/503/404 说明它自己有把握没做成，
#: 换平台是安全的；异常的语义恰恰相反。所以标记只打在异常分支上。
_UNCERTAIN_ATTR = "_wb_platform_result_uncertain"


def _mark_uncertain(response):
    """标记「这次平台调用的结果不确定」（异常路径专用）。"""
    try:
        setattr(response, _UNCERTAIN_ATTR, True)
    except Exception:  # noqa: BLE001 - 标记失败不能影响响应本身
        pass
    return response


def _is_uncertain(response) -> bool:
    """该响应是否来自「平台内部异常」（结果不确定，不许换平台重放）。"""
    return bool(getattr(response, _UNCERTAIN_ATTR, False))

#: 通用路由名：任何平台都可能自己实现，跨平台拒绝时要放行
_GENERIC_MODEL_NAMES = frozenset({
    "auto", "default", "chat", "best", "smart", "fast", "router", "auto-model",
})

#: **已整体下线**的平台 → 模型名前缀。
#:
#: 用途只有一个：认出「客户端配置里还留着的旧模型名」，直接回 404 讲清楚，
#: 绝不让它落到默认平台去打真上游（见 `_retired_platform_of`）。
#: 豆包网关 2026-11-15 删除；以后删平台在这里补一行即可。
RETIRED_PLATFORM_PREFIXES = {
    "doubao-": "doubao",
}

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


#: 历史测试替身 / 外部调用方可能仍是三参签名：按签名决定要不要传 scope。
_ROUTE_SCOPE_SUPPORT: dict = {}


def _route_with_scope(model, key, disabled, scope):
    """调用 `_route_by_model_ownership`，兼容只接受三参的替身。

    生产实现接受 `scope=`（用来把候选与排序结果缓存到本次请求里，避免
    选择/改道/重试三个阶段各算一次）。签名判定结果按函数对象缓存，热路径
    只是一次字典查找。
    """
    import inspect

    fn = _route_by_model_ownership
    takes_scope = _ROUTE_SCOPE_SUPPORT.get(fn)
    if takes_scope is None:
        try:
            takes_scope = "scope" in inspect.signature(fn).parameters
        except (TypeError, ValueError):
            takes_scope = False
        _ROUTE_SCOPE_SUPPORT[fn] = takes_scope
    if takes_scope:
        return fn(model, key, disabled, scope=scope)
    return fn(model, key, disabled)


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

    # ---- 3) 按模型归属选平台（**与 Key 是否开聚合无关**）----
    #
    # 2026-11-19 事故：裸名 `deepseek-v4.1-flash` 被分到积分耗尽的 raccoon，
    # 而上游错误码 1000007 直接冒到客户端（换 `X-Platform: marvis` 就 200）。
    # 根因就是这一步只在 `aggregate=1` 时才做：
    #
    #     key.platform='' + key.aggregate=0
    #       -> 跳过「按模型选路」-> 落「无头分摊」-> 策略按"可用号数"选中 raccoon
    #          （raccoon 27 个号 > marvis 8 个号，throughput 策略必然先选它）
    #
    # 现在只要「没有 X-Platform 且 Key 没有平台绑定」（上面两个分支都没返回）
    # 就先做一次成员校验：哪些平台**声明**了这个模型。
    #
    # ⚠️ 这一层只解决**选择阶段**的归属问题，选中后平台钉死：
    # 不存在「P 平台失败就改走 Q 平台」的运行期改道（用户明确禁止过）。
    model = _model_of(request)
    if model:
        picked = _route_with_scope(model, key, disabled, request.scope)
        if picked is not None:
            return picked[0], picked[1]

    # 通用路由名（`auto` 等）不走「无头分摊」：它没有模型归属，
    # 分摊到哪个平台就等于让那个平台自己解释 `auto` —— 有的平台认、
    # 有的平台直接 404。这类请求固定落默认平台，由主应用的自动选模型
    # 接住，行为才可预期（issue #38）。
    if _spread_unbound() and not (model and model.strip().lower() in _GENERIC_MODEL_NAMES):
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


def _storage_of(platform_id: str) -> str:
    """平台 id -> 落库平台（`trae-cn` -> `trae`，`workbuddy` -> `workbuddy`）。

    判断「这是不是同一个平台」必须比落库平台：`trae-cn` 与 `trae-intl` 是
    两个对外 id、一份账号池，拿 id 比会把「绑了 trae 的 Key」误判成不匹配。
    """
    spec = registry.get(str(platform_id or "").lower())
    if spec is not None:
        return str(spec.storage()).lower()
    return str(platform_id or "").strip().lower()


class _KeyView:
    """判「改道可行性」用的 Key 视图：**去掉独占平台约束**，其余照旧。

    ## 为什么要脱掉 platform

    `scope_accounts` 在 Key 绑了平台时会再加一条 `Account.platform = 该平台`，
    于是「判断 loomy 能不能服务这个模型」永远得到 False —— 改道判断就此失去
    意义，Key 绑了 workbuddy 就连 loomy 专属模型也调不动了（线上在用）。

    `account_id` / `account_ids` **保留**：独占账号是用户对「用哪些号」的
    硬约束，改道也不能借用别的号。
    """

    __slots__ = ("id", "account_id", "account_ids", "platform", "group_id", "aggregate")

    def __init__(self, key):
        self.id = int(getattr(key, "id", 0) or 0)
        self.account_id = getattr(key, "account_id", None)
        self.account_ids = getattr(key, "account_ids", "") or ""
        self.platform = ""
        self.group_id = int(getattr(key, "group_id", 0) or 0)
        self.aggregate = bool(getattr(key, "aggregate", True))


class _ReroutedKey:
    """改道**执行**时的 Key 视图：只脱掉 `platform`，其余属性全部委托。

    ## 为什么只有判可行还不够

    `_KeyView` 只影响改道前的候选判断。改道确认后，请求由**目标平台网关**
    执行，而网关选号（`shared.select_account` -> `scope_accounts`）拿的是
    **真实 Key**：Key 绑了 workbuddy，就再加一条 `Account.platform='workbuddy'`，
    与目标平台的 `Account.platform='loomy'` 交集为空 —— 必然选不到号，
    改道 100% 落成 503「没有可用账号」。

    改道本身就是「用户想要的平台做不了这个模型」的兜底，平台绑定约束在
    改道场景下必须豁免；**独占账号 / 分组 / 额度 / 状态照旧**，网关里其余
    读 Key 的地方（配额、日志归属、trae 的 realm 推断）拿到的值不变。
    """

    __slots__ = ("_key", "platform")

    def __init__(self, key):
        self._key = key
        self.platform = ""

    def __getattr__(self, name):
        # 只在 `_key` 上缺属性时才走到这里（platform 在 __slots__ 里）。
        return getattr(self._key, name)


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
    # 大 body 不做整段 json 解析：一轮长对话几 MB，每跳中间件都 parse
    # 一次会明显拖慢首字延迟。顶层 `model` 字段几乎总在最前面，
    # 先在前 8KB 里正则取；取不到再退回完整解析（正确性优先）。
    head = body[:8192]
    match = re.search(rb'"model"\s*:\s*"([^"\\]{1,200})"', head)
    if match:
        try:
            value = match.group(1).decode("utf-8")
        except UnicodeDecodeError:
            value = match.group(1).decode("utf-8", "replace")
        if value.strip():
            return value.strip()
    try:
        import json as _json

        payload = _json.loads(body)
        return str(payload.get("model") or "").strip()
    except (ValueError, UnicodeDecodeError):
        return ""


def _should_peek(request) -> bool:
    """只对**带 body 的写请求**预读；GET/HEAD 读了会让下游卡死。

    预读只做两件事：读模型名做后缀路由、本进程内存保护。契约（不对称是故意的，
    `_BODY_HARD_CAP` 是预读/选路上限，不是全局请求体上限）：

      * 已声明 Content-Length 且 > cap -> False：不预读、不 413，交给下游自己的
        体积检查；没读到模型名最多是「不做后缀路由」。
      * 没有 Content-Length（chunked）-> True：边读边判，读至 cap 由
        `_read_body` 抛 `BodyTooLarge`，中间件回 413。
      * 无长度也要读：只认 Content-Length ≤ 上限会让 chunked 和长上下文静默
        退化成「不做后缀路由」（带平台后缀的名字落到默认平台 → 400）。
    """
    try:
        if request.scope.get("method", "").upper() not in _BODY_METHODS:
            return False
        for name, value in request.scope.get("headers") or []:
            if name.lower() == b"content-length":
                raw = value.decode("latin-1")
                if not raw.isdigit():
                    return True          # 长度不可信：照样读，读完再看
                size = int(raw)
                # 超大请求体不预读（避免把内存打爆），但**必须留日志**，
                # 否则又是一次「无缘无故 400」。
                if _BODY_HARD_CAP > 0 and size > _BODY_HARD_CAP:
                    logger.warning(
                        "请求体 %d 字节超过预读上限 %d，本次不做平台后缀路由"
                        "（model 里带 -平台 后缀会落到默认平台）", size, _BODY_HARD_CAP)
                    return False
                return size > 0
        # 没有 Content-Length（chunked）：以前直接 return False，
        # 现在照样预读 —— `_read_body` 会把整段读完并回放给下游。
        return True
    except Exception:  # noqa: BLE001
        return False


class BodyTooLarge(Exception):
    """请求体在**读取过程中**就越过了上限（未知长度 / chunked 路径）。

    抛异常而不是返回空串：receive 已被消费，越线后既不能回放完整 body 也不该
    继续读，唯一正确的处理是让中间件直接回 413。
    """

    def __init__(self, total: int, limit: int):
        self.total = int(total)
        self.limit = int(limit)
        super().__init__("请求体超过上限 %d 字节（已读 %d）" % (self.limit, self.total))


class BodyReadError(Exception):
    """预读请求体失败。receive 已被消费，已读到的部分不能再交给下游。"""


class ClientDisconnected(BodyReadError):
    """预读阶段客户端断开：body 可能为空，也可能只有半截。"""


async def _read_body(receive, limit: int = _BODY_HARD_CAP) -> bytes:
    """把请求体整段读出来（最多 `limit` 字节），供回放给下游。

    ⚠️ **消费** receive：只要调用了就必须用 `_replay_receive` 还给下游，
    否则下游拿到空 body 直接 400「bad json」。

    异常：越线抛 `BodyTooLarge`；预读期断开抛 `ClientDisconnected`
    （断开**不是** EOF，返回值会被当成完整 body 回放）；其它读失败原样抛出。

    上限必须边读边判：先整段缓存再比大小等于没有上限。越线即停、不再调用
    receive，由中间件回 413。声明长度超 cap 的请求不会走到这里
    （`_should_peek` 返回 False，交给下游的体积检查）。
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        message = await receive()
        kind = message.get("type")
        if kind == "http.disconnect":
            # 断开不是 EOF：已收的 chunk 可能少于 Content-Length / 半截 JSON。
            raise ClientDisconnected("客户端在请求体读完前断开")
        if kind != "http.request":
            continue
        chunk = message.get("body") or b""
        if chunk:
            chunks.append(chunk)
            total += len(chunk)
            if limit > 0 and total > limit:
                # 越线即停：剩余 body 一个字节都不再读。（limit <= 0 = 不设上限）
                raise BodyTooLarge(total, limit)
        if not message.get("more_body"):
            break
    return b"".join(chunks)


def _replay_receive(body: bytes, tail=None):
    """构造一个回放 `body` 的 receive：首调给 body，之后委托 `tail`。

    预读消费掉了原始 receive，回放完必须回到它：`StreamingResponse` 的
    `listen_for_disconnect` 靠 `tail` 拿到真实的 `http.disconnect`，否则
    生成器跑满全程，上游连接 / 在途租约 / 断开统计都被拖住。

    `tail is None`（单测直调）才兜底等待：不能立刻回 `http.disconnect`
    （下游「No response returned」），也不能立刻回空 `http.request`
    （`listen_for_disconnect` 忙循环，流式请求 0 字节）。
    """
    state = {"sent": False}

    async def receive():
        if not state["sent"]:
            state["sent"] = True
            return {"type": "http.request", "body": body, "more_body": False}
        # 回放完成：把后续读取**原样交给原始 receive**（真实断连由此透传）。
        if tail is not None:
            return await tail()
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
    from admin.platform import models_router as _mr

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
        # **归一 key 用「可读名」算**（2026-11-15）：
        # 腾讯 Marvis 的 model_configs 里存的是上游槽位名（`main-auto`），
        # 它的归一 key 是 `mainauto` —— 与客户端发的 `deepseek-v4.1-flash`
        # （key `deepseekv41flash`）永远对不上，于是 dispatch 认为
        # 「Marvis 没有这个模型」，直接回
        # 「模型不属于平台 marvis（该模型在：workbuddy、loomy…）」。
        # 用 `readable_model_id()` 把槽位换成真实模型名后再算 key，
        # 聚合视图与自动选路才认得它；转发时仍用原始 model_id（上游只认槽位）。
        readable = _mr.readable_model_id(storage, model_id)
        key = canonical_key(readable or model_id, display)
        entry = {
            "platform": storage,
            "model_id": model_id,
            "readable": readable,
            "display": display,
            "key": key,
            "multiplier": multiplier,
        }
        rows.append(entry)
        by_storage.setdefault(storage, []).append(entry)
        # **同一条配置可能对应多个 key**（2026-11-18）：
        #
        # 上面算出的 `key` 用的是「可读名」（Marvis 的槽位 `main-auto` ->
        # `deepseek-v4.1-flash`）。客户端**直接发上游原生 id / 槽位名**
        # （`main-auto`，早期文档与老客户端配置里的写法）时归一 key 是
        # `mainauto`，与清单里的 `deepseekv41flash` 永远对不上 ——
        # `_model_platforms()` 拿不到候选，`resolve()` 于是掉进「无头分摊」
        # 策略（线上是 raccoon），那个平台的清单里没有该模型就 404。
        #
        # 所以再按**库里原始 model_id 自己的 key** 登记一次。这里故意不带
        # 展示名：`canonical_key(model_id, display)` 会优先采用展示名命中的
        # 人工别名，槽位名又会被收敛回可读名，等于什么都没加。
        #
        # 重复登记不会让候选变多：`_model_platforms()` 按 (platform, realm)
        # 归并，同一平台只会出现一次。
        raw_key = canonical_key(model_id)
        if raw_key and raw_key != key:
            by_key.setdefault(raw_key, []).append(entry)
        if key:
            by_key.setdefault(key, []).append(entry)
        # 还有第三种写法：**平台自报的规范名**。Marvis 的清单同步后只存
        # 可读名（`deepseek-v4.1-flash`），而它的规范名是槽位 `main-auto`
        # —— 客户端裸写槽位名时，只有这一条能把请求领回 marvis。
        # 没有 `canonical_model_id()` 钩子的平台这里恒为空，行为不变。
        canonical_id = _platform_canonical(storage, model_id)
        canonical_norm = canonical_key(canonical_id) if canonical_id else ""
        if canonical_norm and canonical_norm not in (key, raw_key):
            by_key.setdefault(canonical_norm, []).append(entry)
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
    realm_cond = shared.realm_domain_cond(spec.realm)
    if realm_cond is not None:
        query = query.filter(realm_cond)
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


def _retired_platform_of(model: str) -> str:
    """模型名指向一个**已整体下线**的平台时，返回那个平台 id。

    2026-11-15 豆包网关整体删除后，客户端配置里还留着
    `doubao-office-xxx` / `xxx-doubao` 这类名字。它们既不在任何平台
    模型清单里、也不在主应用白名单里 —— 如果不拦，请求会落到默认平台
    （WorkBuddy），拿一个不认识的模型去打真上游：表现是长时间挂起或
    上游报错，还白占默认平台的账号额度。
    """
    name = str(model or "").strip().lower()
    if not name:
        return ""
    for prefix, platform in RETIRED_PLATFORM_PREFIXES.items():
        if name.startswith(prefix) or name.endswith("-" + platform):
            return platform
    return ""


def _realm_of_model_id(model_id: str) -> str:
    """`xxx:intl` -> `intl`；没有区域后缀返回空串（共用行 / 默认区）。"""
    text = str(model_id or "")
    if ":" not in text:
        return ""
    suffix = text.rsplit(":", 1)[-1].strip().lower()
    return suffix if suffix in ("cn", "intl") else ""


def _strip_realm_suffix(storage: str, model_id: str) -> str:
    """转发前去掉区域标记：上游只认裸名（目前只有 Trae 用 `:intl` 这套）。"""
    text = str(model_id or "")
    if str(storage or "").lower() == "trae" and ":" in text:
        return text.split(":", 1)[0]
    return text


def _catalog_rows_in_target(db, storage: str, realm: str = "") -> list[dict]:
    """目标平台（可再按区域收窄）登记的目录行，顺序与数据库行迭代无关。

    `_catalog_maps()` 的 `by_storage` 保持数据库返回顺序 —— 那是执行计划，
    不是约定（计划 §一 第 3 行）。这里按 `model_id` 排序，让「同 key 多行」
    的消歧结果可复现：同一份配置换一次插入顺序不该改变转发出去的名字。
    """
    store = str(storage or "").strip().lower()
    if not store:
        return []
    by_storage, _ = _catalog_maps(db)
    rows = [row for row in (by_storage.get(store) or [])
            if str(row.get("model_id") or "")]
    if not rows:
        return []
    # 同一 storage 存在 `xxx:intl` 时，同名无后缀那条只属于国内区。
    # 孪生规则**不在这里重新实现**：直接问 models_router.intl_twins()，
    # 与目录（build_payload / _entry_targets）共用同一份判断，避免两处漂移
    # （计划 §六 第 81 行）。
    try:
        from admin.platform import models_router as _mr_twins

        twins = _mr_twins.intl_twins(rows)
    except Exception:  # noqa: BLE001 - 退回「没有孪生」的保守判断
        twins = set()
    intl_bases = {base for store_name, base in twins
                  if store_name == store}
    realm_key = str(realm or "").strip().lower()
    picked: list[tuple[int, str, dict]] = []
    for row in rows:
        model_id = str(row["model_id"])
        row_realm = _realm_of_model_id(model_id)
        if not row_realm and model_id in intl_bases:
            row_realm = "cn"
        # 区域收窄：共用行（""）对任何区都成立；有孪生时无后缀行只算国内区
        if realm_key and row_realm not in ("", realm_key):
            continue
        rank = 0 if (realm_key and row_realm == realm_key) else 1
        picked.append((rank, model_id, row))
    picked.sort(key=lambda item: (item[0], item[1]))
    return [row for _rank, _model_id, row in picked]


def _model_id_in_target(db, platform_id: str, storage: str, realm: str,
                        model: str) -> str:
    """把逻辑模型名映射成**目标平台（含区域）自己的目录拼写**。

    `platform_id` **只用于诊断/回溯**（debug 日志与调用点可读性）：真正的判别
    依据是 `storage` + `realm` —— 一个 storage 可能对应两个对外平台 id
    （qoder / qoder-intl），它们本来就是同一份目录。

    一次映射的入参就是「最终落点」的四元组（计划 §四 第 55 行）：
    `public platform id + storage + realm + 逻辑模型名`。命中顺序：

      1) 原串相等              -> 目录里那个 `model_id`（原样）
      2) 忽略大小写相等        -> 目录里的**原拼写**（Loomy 的
                                 `DeepSeek-V4.1-Flash` 按库内写法发出去）
      3) 本平台登记的可读名    -> 目录里的 `model_id`
                                 （Marvis 槽位 `main-auto`、Qoder `dfmodel`）
      4) 「槽位 -> 可读名」反查（老配置写槽位名，目录已改成可读名）
      5) 归一 key              -> 目录里的 `model_id`
                                 （公开 ID 落到别名平台：`deepseek-v4.1-flash`
                                 -> Qoder 的 `dfmodel`）；同 key 多行时优先
                                 「已登记的规范公开名」那一行，其余按区域与
                                 `model_id` 定序（不依赖行顺序）
      6) 平台 canonical 钩子   -> **只认确实登记在该目标的 `model_id`**

    返回空串 = **该目标没有这个模型**。调用方沿用入口原有行为（通用名 /
    未知模型 / 未同步目录），绝不拿别的平台原生 id 兜底。
    """
    text = str(model or "").strip()
    store = str(storage or "").strip().lower()
    if not text or not store:
        return ""
    try:
        rows = _catalog_rows_in_target(db, store, realm)
    except Exception:  # noqa: BLE001 - 目录读不到就按未命中处理（原样转发）
        logger.debug("目标目录读取失败 storage=%s platform=%s", store,
                     platform_id, exc_info=True)
        return ""
    if not rows:
        return ""
    lowered = text.lower()

    def _done(model_id: str) -> str:
        # 上游只认裸名：区域标记（Trae 的 `:intl`）在这里统一剥掉
        return _strip_realm_suffix(store, model_id)

    # 1) 原串相等
    for row in rows:
        if row["model_id"] == text:
            return _done(row["model_id"])
    # 2) 忽略大小写 -> 目录原拼写
    for row in rows:
        if str(row["model_id"]).strip().lower() == lowered:
            return _done(row["model_id"])
    # 3) 本平台登记的可读名（Qoder 缩写 -> 可读名、Marvis 槽位 -> 可读名）
    for row in rows:
        readable = str(row.get("readable") or "")
        if readable and readable.strip().lower() == lowered:
            return _done(row["model_id"])
    # 4) 「槽位 -> 可读名」反查：老客户端还写着槽位名，而目录已改成可读名
    try:
        from admin.platform import models_router as _mr_readable

        readable = str(_mr_readable.readable_model_id(store, text) or "")
    except Exception:  # noqa: BLE001
        readable = ""
    if readable and readable.strip().lower() != lowered:
        for row in rows:
            if str(row["model_id"]).strip().lower() == readable.strip().lower():
                return _done(row["model_id"])
    # 5) 归一 key（只用于**比较**，返回的仍是目录里的写法）
    try:
        from admin.model_alias import canonical_key

        alias = canonical_key(text)
    except Exception:  # noqa: BLE001
        alias = ""
    if alias:
        matched = [row for row in rows if row.get("key") == alias]
        if matched:
            # 同平台多行同一 key：优先「已登记的规范公开名」那一行
            canonical_public = ""
            try:
                from admin.platform import models_router as _mr_public

                canonical_public = str(_mr_public.public_model_id(alias) or "")
            except Exception:  # noqa: BLE001
                canonical_public = ""
            canonical_public = canonical_public.strip().lower()
            if canonical_public:
                for row in matched:
                    if str(row["model_id"]).strip().lower() == canonical_public:
                        return _done(row["model_id"])
            return _done(matched[0]["model_id"])
    # 6) 平台的 canonical 钩子：只有当它给出的名字**确实是本目标登记的
    #    model_id** 时才采用。Marvis 认槽位 / 上游真名 / 可读名，但槽位不是
    #    目录拼写时不能拿来转发（归属校验会 404）；槽位换算交给它的网关
    #    `_validate_model`。
    canonical = _platform_canonical(store, text)
    canonical_lower = canonical.strip().lower()
    if canonical_lower:
        for row in rows:
            model_id = str(row["model_id"])
            if (model_id.strip().lower() == canonical_lower
                    or _strip_realm_suffix(store, model_id).strip().lower()
                    == canonical_lower):
                return _done(model_id)
    return ""


def _model_id_for(db, storage: str, model: str) -> str:
    """把请求里的模型名映射成**目标平台自己的 model_id**（未命中返回原名）。

    同一模型在不同平台叫不同名字（`deepseek-v4.1-flash` / `dfmodel` /
    `DeepSeek-V4.1-Flash`）。按归一 key 选到平台后，如果继续把原名字转发过去，
    目标平台只会回「模型不存在」—— 这正是「聚合转发看起来通了，实际全 404」
    的原因。

    口径统一在 `_model_id_in_target()`：这里只带落库平台（调用点拿不到区），
    Trae 的 `:intl` 由它剥掉，上游只认裸名。
    """
    return _model_id_in_target(db, "", storage, "", model) or model


def _platform_canonical(storage: str, model: str) -> str:
    """问平台「这个名字对应你的哪个上游 id」（没有钩子就返回空串）。

    约定：平台包可提供 `core.endpoints.canonical_model_id(str) -> str`。
    异常一律吞掉 —— 兜底逻辑坏掉不能影响正常转发。
    """
    storage = str(storage or "").lower()
    if not storage or not model:
        return ""
    try:
        mod = __import__("admin.platform.%s.core.endpoints" % storage,
                         fromlist=["canonical_model_id"])
        fn = getattr(mod, "canonical_model_id", None)
        if callable(fn):
            value = str(fn(model) or "")
            return value if value and value != model else ""
    except Exception:  # noqa: BLE001 - 平台没有这个钩子 / 导入失败
        return ""
    return ""


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


def _model_for_platform(db, storage: str, model: str,
                        realm: str = "") -> tuple[str, dict | None]:
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

    mapped = _model_id_in_target(db, "", storage, realm, model) or model
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
    # **可读名也是原生 id**（2026-11-15）：Marvis 的 model_configs 存槽位名
    # （`main-auto`），但 `/v1/models` 对外发的是可读名（`deepseek-v4.1-flash`）。
    # 客户端拿着可读名回来时必须按同平台命中，否则会被判成「属于别的平台」。
    if any(str(row.get("readable") or "").lower() == str(model or "").strip().lower()
           for row in (by_storage.get(storage) or [])):
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
                "message": "模型 %s 在平台 %s 的清单里不存在。"
                           "可用 X-Platform 指定平台，或先在控制台"
                           "「模型配置」为 %s 同步该模型。"
                           % (model, storage, storage),
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
    """按策略顺序返回可服务该模型的 `(public_platform, realm, target_model)`。

    **全部硬限制在这里做完**（计划 §五 第 72 行）：平台/区域停用、管理员禁用
    （含 storage 禁用）、模型归属（该目标目录里真有这个模型）、Key 可用账号
    范围、账号×模型冷却、平台暂停。`_platform_order()` 只在**已合格**的集合
    内排序 —— 排序没有资格把被排除者加回来。
    """
    servable, degraded = _candidates_detail(db, model, key)
    # 所有候选都「只有历史失败记录」时不能直接放弃：仍按顺序试一遍，
    # 因为负缓存可能来自一次配置错误，现在可能已经修好。
    return servable or degraded


def _candidates_detail(db, model: str, key) -> tuple[list, list]:
    """`(servable, degraded)`：能服务的 / 只有历史失败记录的。

    `degraded` 里的目标**同样已经过了全部硬限制**（禁用、暂停、归属、
    Key 账号范围、账号×模型冷却），只是 24h 失败率偏高 —— 软健康降级只允许
    发生在这些集合内（计划 §五 第 73 行）。两者都为空才是「真没候选」。
    """
    platforms = _model_platforms(db, model)
    if not platforms:
        return [], []
    disabled = _disabled_platforms()
    allowed = _key_group_models(db, key)
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
        return [], []
    ranked = [pid for pid in _platform_order()
              if pid in {spec.id for spec in specs}]
    # 未出现在策略顺序里的 spec 追加到尾部 —— 它们仍要逐条过下面的硬限制
    ranked += [spec.id for spec in specs if spec.id not in ranked]
    servable: list[tuple[str, str, str]] = []
    degraded: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for pid in ranked:
        spec = registry.get(pid)
        if spec is None or not spec.enabled:
            continue
        realm = str(getattr(spec, "realm", "") or "")
        if (pid, realm) in seen:
            continue
        if _is_disabled(pid, disabled):
            continue
        if _platform_paused(pid):
            continue
        mapped = _model_id_in_target(db, pid, spec.storage(), realm, model)
        if not mapped:
            # 该目标（含区域）的目录里没有这个模型 —— 归属不符，不参与重试
            continue
        in_group, _group_mapped = _group_allows(
            db, pid, spec.storage(), realm, model, allowed)
        if not in_group:
            continue
        if not _has_account(db, spec, key, mapped):
            continue
        seen.add((pid, realm))
        if not _model_health_ok(db, spec.storage(), mapped):
            degraded.append((pid, realm, mapped))
            continue
        servable.append((pid, realm, mapped))
    return servable, degraded


def _candidates_cached(db, model: str, key, scope=None) -> tuple[list, list]:
    """本请求内复用候选与排序结果（计划 §五 第 75 行）。

    「选择 / 改道 / 重试」三个阶段必须用**同一份决定**：`random` /
    `round_robin` 不能各自重新挑首选。缓存挂在 request scope 上，
    **不是**全局缓存 —— 账号与冷却状态是动态的，跨请求复用会让刚恢复的
    平台一直排在后面。
    """
    text = str(model or "")
    if scope is not None and text:
        cached = scope.get("_wb_model_candidates")
        if isinstance(cached, tuple) and len(cached) == 3 and cached[0] == text:
            return list(cached[1]), list(cached[2])
    servable, degraded = _candidates_detail(db, model, key)
    if scope is not None and text:
        scope["_wb_model_candidates"] = (text, list(servable), list(degraded))
    return servable, degraded


def _platform_paused(platform: str) -> bool:
    try:
        from admin.platform import sentinel_router

        return sentinel_router.paused(platform) is not None
    except Exception:  # noqa: BLE001
        return False


def _storage_has_model(db, storage: str, model: str) -> bool:
    """该落库平台的**已启用**清单里有没有这个模型（认原生 id 与归一 key）。"""
    from admin.model_alias import canonical_key, model_display
    from admin.models import ModelConfig

    text = str(model or "").strip().lower()
    if not text:
        return False
    alias = canonical_key(text)
    rows = (db.query(ModelConfig.model_id, ModelConfig.upstream_metadata)
            .filter(ModelConfig.platform == storage,
                    ModelConfig.enabled == 1).all())
    for model_id, metadata in rows:
        mid = str(model_id or "")
        if mid.lower() == text:
            return True
        if alias and canonical_key(mid, model_display(metadata)) == alias:
            return True
    return False


def _reroute_target(db, model: str, key, disabled: set[str] | None = None,
                    current: tuple[str, str] | None = None,
                    scope=None) -> tuple[str, str, str] | None:
    """模型不属于「用户想要的平台」时，返回该改道到的 `(平台id, realm, 依据)`。

    返回 `None` = **不该改道**，保持当前选路结果（默认平台 / Key 绑定平台）。

    ## 为什么不是「查归属，查到就改道」（旧实现，线上事故）

    `platform_of_model()` **只取第一条**同名 `model_configs`：
    `deepseek-v4.1-flash` 在 trae(id=9) / workbuddy(57) / loomy(72) 都登记了，
    第一条是 trae，于是每条请求都被它抢去 trae —— Key 绑定的平台、显式
    `X-Platform`、平台禁用开关全部形同虚设（实测独占 workbuddy 的 Key
    两天内 trae 785 次 / workbuddy 52 次）。

    现在的判定顺序：

        1. 想要的平台（Key 绑定平台，否则默认平台）在这个模型的候选里
           -> 不改道（候选 = 有号 + 未冷却 + 健康）；
        2. 不在候选里、但有别的平台能做 -> 改道到该平台；
        3. 归一 key 匹配不上（如 raccoon 的 `sn-xxx-1017` 展示名与 id 不同）
           -> 退回按归属反查，**但仍然只在想要的平台根本没有这个模型时**才改道。

    禁用与网关实现由调用方/本函数最后一道 `_spec_with_gateway` 保证。
    """
    text = str(model or "").strip()
    if not text:
        return None
    # **通用路由名没有「归属平台」**（2026-11-16，issue #38）。
    # `auto` / `default` / `chat` 这类名字是「让网关自己挑模型」，
    # 每个平台都可以各自实现，`model_configs` 里谁登记了它纯属偶然。
    # 旧逻辑按 `platform_of_model()` 做归属反查，于是 `auto` 被抢到唯一
    # 登记了 auto 行的平台（线上是 Qoder），而上游并不认这个词 —— 表现
    # 就是「开聚合 + 用 auto → 恒 HTTP 404」，且用户完全看不出落在哪。
    if text.lower() in _GENERIC_MODEL_NAMES:
        return None
    bound = str(getattr(key, "platform", "") or "").strip() if key is not None else ""
    # **以「当前已选目标」为准**（计划 §五 第 71 行）：调用方可能已经按模型
    # 归属选中了别的平台，这里再按「想要的平台 = Key 绑定 / 默认平台」重算，
    # 等于把已经做出的决定推翻。`current` 为空才退回旧口径。
    if current and str(current[0] or "").strip():
        want = _storage_of(str(current[0]).strip())
    else:
        want = _storage_of(bound or registry.DEFAULT_PLATFORM)
    disabled = disabled or set()

    candidates, _degraded = _candidates_cached(
        db, text, _KeyView(key) if key is not None else None, scope)
    if candidates:
        if any(_storage_of(pid) == want for pid, _realm, _mapped in candidates):
            return None
        for pid, realm, _mapped in candidates:
            if _is_disabled(pid, disabled):
                continue
            spec = _spec_with_gateway(pid, realm)
            if spec is None:
                continue
            try:
                if getattr(_module_of(spec), "dispatch", None) is None:
                    continue
            except Exception:  # noqa: BLE001
                continue
            return str(getattr(spec, "id", "") or pid), realm, "候选里没有想要的平台"
        return None

    # 归一 key 对不上：先确认想要的平台确实没有这个模型，再按归属反查。
    if _storage_has_model(db, want, text):
        return None
    from admin.platform import models_router

    owner = models_router.platform_of_model(text, db)
    if not owner or _storage_of(owner) == want:
        return None
    if _is_disabled(owner, disabled):
        return None
    spec = _spec_with_gateway(owner, "")
    if spec is None:
        return None
    try:
        if getattr(_module_of(spec), "dispatch", None) is None:
            return None
    except Exception:  # noqa: BLE001
        return None
    return str(getattr(spec, "id", "") or owner), str(getattr(spec, "realm", "") or ""), "归属反查"


def _platform_for_model(model: str, key) -> tuple[str, str, str] | None:
    # 通用路由名不参与「按模型选平台」：`auto` 落在谁身上都该由那个平台
    # 自己解释（默认平台由主应用的自动选模型接住）。见 `_reroute_target`。
    if str(model or "").strip().lower() in _GENERIC_MODEL_NAMES:
        return None
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
# 选择阶段：按模型归属选平台（与 Key 的 aggregate 开关无关）
# ---------------------------------------------------------------------------
class ModelUnknownOnAnyPlatform(Exception):
    """模型在**任何平台**的清单里都不存在（选择阶段就拒绝）。

    以前这种情况会掉进「无头分摊」，被分到一个**根本没有这个模型**的平台，
    由那个平台先跑一轮（选号 / 建连 / 甚至打真上游）再回 400 —— 用户看到的
    是「模型不存在」，但落点不明，控制台也看不出是路由选错了平台。

    platforms  本次「模型存在于哪些平台」的结论（零命中时为空）；
    available  当时可选的平台 id，供错误体直接讲清楚能选什么。
    """

    def __init__(self, model: str, platforms, available):
        self.model = str(model or "")
        self.platforms = list(platforms or [])
        self.available = list(available or [])
        super().__init__(self.model)


class ModelNotInGroup(Exception):
    """模型不在该 Key 绑定分组的组内清单里（403 model_not_in_group）。

    为什么必须在 **dispatch** 就判（计划 §五 第 73 行 / t8 集成）：
    分组授权原先只在主应用（proxy）里做，平台网关路径不做。一旦 dispatch
    把请求判给了默认平台，proxy 会把组内登记的**原生 ID**（例如 Qoder 的
    `dfmodel`）写进 body 的 model 再发上游 —— WorkBuddy 收到别家的名字只会回
    400「模型不存在」，而不是 403。归属在这里就收窄，授权错误才不会伪装成
    模型错误。
    """

    def __init__(self, model: str):
        self.model = str(model or "")
        super().__init__(self.model)


def _key_group_models(db, key) -> set[str] | None:
    """Key 绑定分组的有效模型集合；None = 不限制（未绑 / 读不到）。

    读不到时**按不限制**处理：分组表异常不该把正常请求打成 403 ——
    主应用那一层仍会按分组拦，这里只是提前收窄归属。
    """
    gid = int(getattr(key, "group_id", 0) or 0) if key is not None else 0
    # **没有分组就直接返回，不查库**：无 group_id 的 Key 行为一个字节都不能变，
    # 更不能为它多出任何查询（t17 硬约束）。
    if not gid or db is None:
        return None
    # 同一请求最多查一次组内集合：`_key_of()` 把 Key 行缓存在 scope 里，所以把
    # 结果挂在这一个对象上即可覆盖「选路 / 候选 / 发送前」三处判定。
    cached = getattr(key, "_wb_group_models", None)
    if isinstance(cached, tuple) and cached[0] == gid:
        return set(cached[1])
    try:
        from admin.routers.groups import effective_group_models

        models = set(effective_group_models(db, gid) or ())
    except Exception:  # noqa: BLE001 - 读不到按不限制
        logger.debug("读取分组模型失败 group_id=%s", gid, exc_info=True)
        return None
    with contextlib.suppress(Exception):
        setattr(key, "_wb_group_models", (gid, frozenset(models)))
    return models


def _group_allows(db, platform_id: str, storage: str, realm: str, model: str,
                  allowed) -> tuple[bool, str]:
    """该目标是否被分组允许，并返回它要转发的原生 id。

    分组登记的是 `model_configs.model_id`（平台原生 ID）：先精确比对，
    再放宽到大小写不敏感（大小写不是授权语义）。
    """
    if allowed is None:
        return True, ""
    mapped = _model_id_in_target(db, platform_id, storage, realm, model)
    if not mapped:
        # 该目标（本区）根本没有这个模型：交给「模型不属于该平台」那条路，
        # 不要在这里判成「不在分组」。
        return False, ""
    # 分组登记的是 model_configs.model_id 的**原始字符串**：授权必须精确比对。
    # 放宽大小写会让「DEMO-MODEL」这种组外行借大小写混进组内（扩权）。
    # **按「本区」的目录行比对**：Trae 的 `xxx` 与 `xxx:intl` 是两行，
    # 转发前都要剥成裸名，但授权必须认原始那一条 —— 否则「组里只登记国内行」
    # 会被国际区借同样的裸名扩权（§六 第 82 行）。
    try:
        rows = _catalog_rows_in_target(db, storage, realm)
    except Exception:  # noqa: BLE001 - 读不到目录就按 mapped 比对
        rows = []
    hit = False
    for row in rows:
        raw_id = str(row.get("model_id") or "")
        if _strip_realm_suffix(storage, raw_id).strip().lower() != mapped.strip().lower():
            continue
        hit = True
        if raw_id in allowed:
            return True, mapped
    if hit:
        return False, mapped
    # 目录里没有对应行（例如平台 canonical 钩子给出的名字）：退回按 mapped 比对
    return mapped in allowed, mapped


def model_not_in_group_payload(model: str) -> dict:
    """`ModelNotInGroup` -> 403 错误体（与主应用同 code，客户端行为一致）。"""
    return {"error": {
        "message": "模型 '%s' 不在该 API Key 绑定的分组内" % model,
        "type": "model_not_in_group",
        "code": "model_not_in_group",
    }}


def _exact_declarers(db, model: str) -> set[str]:
    """model_id 字段**就是**这个模型名的落库平台（大小写不敏感）。

    ## 为什么要区分「精确登记」与「别名归一」

    _model_platforms() 走归一 key，把 raccoon 的私有槽位
    sn-deepseek-v4-1-flash-1017 也算成「声明了 deepseek-v4.1-flash」。
    它确实能服务这个名字，但那是一次**平台内部改名**：上游对裸名另有
    预检（实测 deepseek-v4-pro / v4-flash / hy3 在 raccoon 直接
    404 model_not_on_platform，只有 v4.1-flash 因为槽位映射漏过预检，
    结果把「积分余额不足」冒到了客户端）。

    所以「同一个模型名同时被精确登记和别名登记」时，优先精确登记的平台 ——
    客户端写什么名字，就先交给**同样用这个名字**的平台。别名登记只在
    没有精确登记者时才兜底（否则像 main-auto 这种只有槽位名的模型会被误杀）。
    """
    name = str(model or "").strip().lower()
    if not name:
        return set()
    by_storage, _unused = _catalog_maps(db)
    out: set[str] = set()
    for storage, rows in (by_storage or {}).items():
        for row in rows or ():
            if str(row.get("model_id") or "").strip().lower() == name:
                out.add(str(storage))
                break
    return out


def _model_membership(db, model: str) -> list[tuple[str, str, str]]:
    """**声明**了这个模型的平台：[(public_platform_id, realm, storage)]。

    口径 = model_configs 里有一行能把这个模型名归一过去（即 _model_platforms()），
    **与有没有可用账号无关** —— 那是下一步 _candidates_for_model() 的事。

    返回顺序沿用 _model_platforms()（倍率升序），同一平台只出现一次。
    """
    platforms = _model_platforms(db, model)
    if not platforms:
        return []
    specs: list = []
    for storage, realm_hint in platforms:
        matches = [spec for spec in registry.all_platforms()
                   if spec.storage() == storage
                   and (not realm_hint or spec.realm == realm_hint)]
        if not matches:
            matches = [spec for spec in registry.all_platforms()
                       if spec.storage() == storage]
        for spec in matches:
            if not spec.enabled or spec in specs:
                continue
            specs.append(spec)
    return [(str(spec.id), str(getattr(spec, "realm", "") or ""),
             str(spec.storage()).lower()) for spec in specs]


def model_not_found_payload(exc) -> dict:
    """`ModelUnknownOnAnyPlatform` -> 404 错误体（独立出来给单测直接用）。

    错误体里必须能看出两件事：
      * `platforms`：这个模型到底在哪些平台存在（零命中时为空）——
        用户照这个列表去改名字/改平台，而不是反复猜；
      * `available_platforms`：当时可选的平台（未禁用/未停用）。
    """
    declared = list(getattr(exc, "platforms", []) or [])
    available = list(getattr(exc, "available", []) or [])
    where = "、".join(declared) if declared else "没有任何平台登记这个模型"
    avail = "、".join(available) if available else "（无）"
    return {"error": {
        "message": "模型 %s 不在任何平台的模型清单里（%s）；当前可用平台：%s"
                   % (getattr(exc, "model", ""), where, avail),
        "type": "invalid_request_error",
        "code": "model_not_found",
        "model": str(getattr(exc, "model", "") or ""),
        "platforms": declared,
        "available_platforms": available,
    }}


def _available_platforms(disabled: set[str]) -> list[str]:
    """当时**可选**的平台 id（未停用、未禁用），用于 404 错误体。"""
    out = []
    for pid in registry.ids():
        spec = registry.get(pid)
        if spec is not None and not spec.enabled:
            continue
        if _is_disabled(pid, disabled):
            continue
        out.append(pid)
    return out


def _strategy_pick_within(platform_ids) -> tuple[str, str] | None:
    """在**给定集合内**按网关策略挑一个平台（集合外的平台不参与）。

    gateway_router.pick() 内部是 order(db, platform_ids)：先按策略排序再取第一个，
    并且会跳过「已禁用」与「没有可用账号」的平台 —— 所以命中集合里那些
    「只有模型、没号」的平台不会被选中。
    """
    ids = [str(pid) for pid in (platform_ids or []) if str(pid or "").strip()]
    if not ids:
        return None
    try:
        from admin.platform import gateway_router

        with _db() as db:
            best = gateway_router.pick(db, ids)
    except Exception:  # noqa: BLE001 - 策略坏了不能挡住转发
        logger.debug("集合内策略选平台失败", exc_info=True)
        return None
    if not best:
        return None
    pid = str(best.get("platform") or "")
    if not pid:
        return None
    spec = registry.get(pid)
    return pid, (str(getattr(spec, "realm", "") or "") if spec is not None else "")


def _route_by_model_ownership(model: str, key, disabled: set[str],
                              scope=None):
    """按「哪些平台声明了这个模型」选平台（**选择阶段**，不存在运行期改道）。

    分支（严格按顺序）：

      1. **一个平台都没声明** -> 抛 ModelUnknownOnAnyPlatform（上层回 404，
         错误体给出「模型存在于哪些平台」= 空，以及当时可选的平台）；
      2. 命中集合里**只剩一个可用** -> 直接路由过去；
      3. **多个可用** -> 只在命中集合内按既有策略（优先级 / 额度 / 轮转…）
         分摊，集合外的平台一个都不许进；
      4. 命中集合里有精确登记者 -> 只在精确登记者内分摊；没有才退回别名登记者
         （main-auto 这类只有槽位名的模型靠这条活下来）。

    返回 None = 不接管（通用路由名 auto / default … 交给默认平台自己解释）。
    """
    text = str(model or "").strip()
    if not text or text.lower() in _GENERIC_MODEL_NAMES:
        return None
    try:
        with _db() as db:
            declared = _model_membership(db, text)
            exact = _exact_declarers(db, text)
            # 一次请求只算一次候选与排序结果（选择 / 改道 / 重试共用）
            servable, degraded = _candidates_cached(db, text, key, scope)
            # **分组授权在选路阶段就收窄**：组内没有这个平台的行，它就没有
            # 资格承接（t8 的集成缺口：授权只在主应用做，会变成 400）
            allowed = _key_group_models(db, key)
            if allowed is not None and declared:
                in_group = []
                for pid, realm, storage in declared:
                    ok, _mapped = _group_allows(
                        db, pid, storage, realm, text, allowed)
                    if ok:
                        in_group.append((pid, realm, storage))
                if not in_group:
                    raise ModelNotInGroup(text)
                declared = in_group
    except ModelNotInGroup:
        raise
    except Exception:  # noqa: BLE001 - 归属查询失败不接管，走既有路径
        logger.debug("按模型归属选平台失败 model=%s", text, exc_info=True)
        return None
    if not declared:
        raise ModelUnknownOnAnyPlatform(text, [], _available_platforms(disabled))

    live = [item for item in declared if not _is_disabled(item[0], disabled)]
    if not live:
        # 声明者全被用户禁用：明确报 403，而不是偷偷换一个平台。
        raise PlatformDisabled(declared[0][0])

    # 命中集合再收窄到「当前真能服务这个模型」的平台（有号 + 未按模型冷却 + 健康）。
    # 四类结论分别可诊断（计划 §五 第 73 行）：
    #   无可用账号 -> 仍回声明集合，让平台的账号池给出它自己的可用性错误；
    #   只有历史失败记录 -> 在**已过硬限制**的集内做软降级重试；
    #   全被禁用 -> 上面已经 403；无人登记 -> 上面已经 404。
    servable_ids = {str(pid) for pid, _realm, _mapped in servable}
    pool = [item for item in live if item[0] in servable_ids]
    if not pool:
        degraded_ids = {str(pid) for pid, _realm, _mapped in degraded}
        if degraded_ids:
            pool = [item for item in live if item[0] in degraded_ids]
            logger.info("模型存在但候选仅有历史失败记录 model=%s -> 软降级重试", text)
        else:
            pool = live
            logger.info("模型存在但当前无可用账号 model=%s（交给平台自己的可用性错误）",
                        text)
    exact_pool = [item for item in pool if item[2] in exact]
    if exact_pool:
        pool = exact_pool
    if len(pool) == 1:
        logger.info("按模型归属选平台：%s -> %s（命中集合唯一）", text, pool[0][0])
        return pool[0][0], pool[0][1]
    picked = _strategy_pick_within([item[0] for item in pool])
    if picked is not None:
        logger.info("按模型归属选平台：%s -> %s（在 %d 个命中平台内按策略分摊）",
                    text, picked[0], len(pool))
        return picked[0], picked[1]
    first = pool[0]
    logger.info("按模型归属选平台：%s -> %s（策略无可用，退回首个命中平台）",
                text, first[0])
    return first[0], first[1]


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
        # **打上"结果不确定"标记**：异常可能发生在已经打过上游之后，
        # 候选循环看到它就直接返回，不再把同一请求重放到下一个平台。
        return _mark_uncertain(JSONResponse(
            status_code=502,
            content={"error": {"message": f"上游平台异常：{exc}",
                               "type": "server_error",
                               "code": "platform_error"}}))
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
    # 平台选定 = 已经过了选路阶段，把实时面板的阶段推到 upstream。
    # **不影响请求本身**：mark_stage 出错也不该中断转发。
    try:
        from admin import traffic_stats

        rid = scope.get("_wb_traffic_rid")
        if rid:
            # 平台定下来了：把 running 行里那个「选路中」换成真实平台。
            # 只改平台字段，不新建记录（一条请求只有一条 running）。
            traffic_stats.annotate(rid, platform=platform_id or "")
            traffic_stats.mark_stage(rid, "upstream")
    except Exception:  # noqa: BLE001
        pass


def _mark_waiting(scope) -> None:
    """把此次请求标成「排队中」（选号 / 等可用账号 / 等并发名额）。

    为什么需要它：面板的阶段枚举里一直有 `waiting`，但**从来没人写过** ——
    请求刚进来时是「已接收」，`_set_state()` 一被调用就直接跳到「转发上游」。
    于是「在号池里等一个可用账号」这段时间在面板上完全看不出来，
    用户只看到一条「已接收」的请求挂在那儿几十秒（Trae 排队时尤其明显）。

    真正的排队点有三处：
      · 选号（可能因为冷却/在途全满而 wait）
      · 平台网关里等上游连接建立
      · 上游已连接但还没吐出第一帧（这个属于 upstream/streaming 之间，
        由 `request_metrics` 在收到首个数据块时推进到 streaming）

    这里只处理第一处：进入选号之前先标 `waiting`。
    """
    try:
        from admin import traffic_stats

        rid = scope.get("_wb_traffic_rid")
        if rid:
            traffic_stats.mark_stage(rid, "waiting")
    except Exception:  # noqa: BLE001
        pass


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
                # 显式传上限：`_read_body` 的默认参数在定义时就绑定死了，
                # 显式传才能让上限成为可观测/可测试的单一来源。
                body = await _read_body(receive, _BODY_HARD_CAP)
            except BodyTooLarge as exc:
                # 未知长度（chunked）请求体已经越线：立刻停读并回 413。
                # **不能**降级成空 body 继续转发 —— 那会让下游收到残缺请求，
                # 也等于把"超限"变成了一次正常的平台调用。
                logger.warning("dispatch 请求体超过上限已拒绝 bytes=%d limit=%d",
                               exc.total, exc.limit)
                return await JSONResponse(
                    status_code=413,
                    content={"error": {
                        "message": "请求体超过网关预读上限（%d 字节），"
                                   "本次请求未被转发" % exc.limit,
                        "type": "invalid_request_error",
                        "code": "body_too_large",
                    }})(scope, receive, send)
            except ClientDisconnected:
                # 预读期断开：body 可能空、可能半截。既不能当 EOF 回放，也不能
                # 转发；客户端多半已经走了，回复尽力而为。
                logger.info("dispatch 预读前客户端断开，放弃本次请求 path=%s", path)
                with contextlib.suppress(Exception):
                    await JSONResponse(status_code=400, content={"error": {
                        "message": "客户端在请求体发送完成前断开，本次请求未被转发",
                        "type": "invalid_request_error",
                        "code": "client_disconnected",
                    }})(scope, receive, send)
                return
            except Exception:  # noqa: BLE001
                # 普通读失败：receive 已被消费掉一部分，交回下游等于转发半截
                # 请求（下游可能把残缺 JSON 当完整 body）。
                logger.warning("dispatch 预读请求体失败，请求不转发 path=%s", path,
                               exc_info=True)
                return await JSONResponse(status_code=400, content={"error": {
                    "message": "读取请求体失败，本次请求未被转发",
                    "type": "invalid_request_error",
                    "code": "body_read_failed",
                }})(scope, receive, send)
            # 预读成功就必须回放（**包括空 body**）：receive 已被消费，不回放
            # 下游就等不到 http.request。CancelledError 不在此列，照常上抛。
            scope["_wb_body"] = body
            receive = _replay_receive(body, tail)
            request = Request(scope, receive)
            if body:
                # 指标 / 实时流量只在**有 body** 时记（空 body 没有模型可记）。
                # 让请求指标 / 实时流量知道这次请求的模型（平台网关不会
                # 经过 WorkBuddy 的 `_request_json`，否则面板上模型一栏是空的）。
                try:
                    from admin import request_metrics

                    record = request_metrics.current.get()
                    model_name = _model_of_body(body)
                    if record is not None and model_name:
                        record["requested_model"] = model_name[:120]
                    # 记原始请求体里的 model，用于定位「客户端到底发了什么名字」
                    # **只记路由字段**（计划 §八 行6）：请求正文、凭证、
                    # 账号令牌都不许进日志。以前这里打的是 body 前 220 字节，
                    # 等于把 messages 正文写进 INFO 日志。
                    logger.info("dispatch 收到 | path=%s model=%r bytes=%d",
                                path, model_name, len(body))
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
                        # Key 展示名与客户端 IP：日志页的实时区要显示
                        # 「谁在用、从哪来」，之前这两列是空的。
                        #
                        # `_key_of()` 走 scope 缓存，**首次调用才查库**。
                        # 有些路径（聚合 /models、非聚合 Key 的预读分支）
                        # 不会提前调用它，于是这里拿到 None —— 表现为
                        # 「有的行有 Key 名、有的行是空的」。
                        # 所以缓存没命中时直接查一次库兜底（结果仍进 scope，
                        # 后续同一请求不会重复查）。
                        _key_label = ""
                        try:
                            _k = _key_of(request)
                            if _k is not None:
                                _key_label = str(getattr(_k, "label", "")
                                                 or getattr(_k, "name", "")
                                                 or "")[:64]
                            else:
                                # 兜底：从 Authorization 头取原始 Key 再查一次
                                _raw = ""
                                try:
                                    for _hk, _hv in (scope.get("headers") or []):
                                        if _hk == b"authorization":
                                            _txt = _hv.decode("latin-1", "replace")
                                            _raw = _txt.split(" ", 1)[-1].strip()
                                            break
                                        if _hk == b"x-api-key":
                                            _raw = _hv.decode("latin-1",
                                                              "replace").strip()
                                            break
                                except Exception:  # noqa: BLE001
                                    _raw = ""
                                if _raw:
                                    from admin.security import get_key_row

                                    with _db() as _raw_db:
                                        _row = get_key_row(_raw_db, _raw)
                                        if _row is not None:
                                            _key_label = str(getattr(_row, "name", "")
                                                             or "")[:64]
                        except Exception:  # noqa: BLE001
                            _key_label = ""
                        _client_ip = ""
                        try:
                            from admin.client_ip import request_ip

                            _client_ip = str(request_ip(request) or "")[:64]
                        except Exception:  # noqa: BLE001
                            _client_ip = ""
                        # **不要在这里再建一条 running 记录**。
                        #
                        # TimingMiddleware 在 dispatch **外层**（`add_middleware`
                        # 是前插，越晚注册越靠外），它已经为这条请求建过 rid 了。
                        # 以前这里无条件 `start()` 又建一条并覆盖
                        # `scope["_wb_traffic_rid"]` —— 外层那条就再也没人收尾，
                        # 永久留在 RUNNING_KEY 里。表现就是面板上堆积一堆
                        # 「已接收 / 选路中」的历史请求，看上去像排队不回收
                        # （实测一次排障里堆了 59 条，最早的是 1 小时前）。
                        #
                        # 现在只复用外层的 rid 补信息（平台猜测 / Key / IP），
                        # 一条请求从头到尾只有一条记录。
                        _rid = scope.get("_wb_traffic_rid")
                        if _rid:
                            traffic_stats.annotate(
                                _rid, platform=_guess or "", model=model_name or "",
                                key_label=_key_label, client_ip=_client_ip)
                        else:
                            # 极少见：没有外层埋点（例如直接调用中间件栈），
                            # 这时才自己建。
                            scope["_wb_traffic_rid"] = traffic_stats.start(
                                _guess, model=model_name or "", path=path,
                                key_label=_key_label, client_ip=_client_ip)
                        # 存进 scope，`request_metrics` 结束时好写进 record()
                        scope["_wb_traffic_key"] = _key_label
                    except Exception:  # noqa: BLE001
                        pass
                except Exception:  # noqa: BLE001
                    pass

        # ---- 1.4) 原始字节快照 + 统一的 body 替换口 ----
        #
        # 计划 §四 第 57 行：所有真实发送点（默认主应用、非默认首选、每个
        # 额外候选）都用**原始 body** 生成该次目标独有的 body；原名与目标名
        # 相同就复用原字节。`scope["_wb_body"]` 与 `_replay_receive(...)`
        # 必须是**同一份字节** —— 否则下一个 hop / 指标读到的是上一跳的名字，
        # 面板上看到的模型和上游实际收到的不一致。
        original_body = body
        original_model = _model_of_body(original_body)

        def _send_body(new_body: bytes) -> None:
            """换上本次目标独有的 body（scope 缓存与回放用同一份字节）。

            顺手补上**原始请求名**的回显值：只要本次改写换掉了 model，就把
            客户端原本写的名字记进 `scope["_wb_requested_model"]`
            （`setdefault`：带后缀的请求已经在上面记过带后缀的原名）。
            否则 `x-gateway-model` 与请求指标在裸名被映射成目标拼写时是空的
            （计划 §四 第 57 行「原始 model 留给请求指标及既有回显头」）。
            """
            nonlocal body, receive, request
            body = new_body
            if original_model and _model_of_body(body) != original_model:
                scope.setdefault("_wb_requested_model", original_model)
            scope["_wb_body"] = body
            receive = _replay_receive(body, tail)
            request = Request(scope, receive)

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
                    # **分组限制**：绑了分组的 Key 只能看到组内模型。
                    # 不传这个参数就会返回全量 —— 客户端能选、调用时又被
                    # `model_not_in_group` 拦掉，用户完全不知道能用哪些。
                    _gid = int(getattr(key, "group_id", 0) or 0)
                    with _db() as raw_db:
                        payload = models_router.build_payload(
                            raw_db, aggregate_only=aggregate_only,
                            group_id=_gid)
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

        # 接下来这段（后缀解析 / 平台反查 / 选号）就是用户眼里的「排队」：
        # 先把阶段推到 waiting，面板才不会一直停在「已接收」。
        _mark_waiting(scope)

        # ---- 2) 解析目标平台 ----
        # 模型名自带平台后缀时（`deepseek-v4.1-flash-workbuddy`），
        # 后缀直接决定平台，并且转发前要剥掉 —— 上游只认裸 id。
        suffix_platform, bare_model, has_suffix = "", "", False
        if body:
            raw_model = _model_of_body(body)
            if raw_model:
                from admin.platform import model_suffix

                suffix_platform, bare_model, has_suffix = model_suffix.resolve(raw_model)
                if has_suffix:
                    # 响应里要把**用户请求的那个名字**回显出来，
                    # 否则 `xxx-loomy` 调完只剩 `xxx`，客户端分不清落点。
                    scope["_wb_requested_model"] = raw_model
                    # **可读名还原**：`deepseek-flash-qoder` 的上游 id 是
                    # `dfmodel`。别名表本来就带平台口径，这里再按 storage 校
                    # 一次，防止把 A 平台的缩写发给 B 平台。
                    alias_storage, alias_upstream = model_suffix.alias_to_upstream(
                        raw_model)
                    if (alias_upstream and alias_storage
                            and _storage_of(suffix_platform) == alias_storage):
                        bare_model = alias_upstream
                    if bare_model and bare_model != raw_model:
                        _send_body(_replace_model(original_body, bare_model))
        # **不要在这里做全局反查**（原来的 `models_router.upstream_of()`）。
        # 目标还没定，而它是按行序返回「第一个匹配行的 raw id」：目录里先插
        # 了 Marvis 的槽位行（raw `main-auto`、可读名同样是
        # `deepseek-v4.1-flash`）时，WorkBuddy 的公开名就被反查成别人的槽位名，
        # 于是 WorkBuddy 上游收到 `main-auto`（计划 §一 截图那条）。
        # 「到哪个平台用哪个拼写」由下面的 (platform, storage, realm) 收窄映射
        # 负责，这里保持客户端原始拼写。

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
        except ModelNotInGroup as exc:
            # 分组里没有这个模型：选路阶段就拒绝，别让默认平台拿组内原生 ID
            # 去上游换回一个 400（t8 的结构性缺口）。
            logger.info("模型不在 Key 绑定的分组内 | model=%r", exc.model)
            return await JSONResponse(
                status_code=403,
                content=model_not_in_group_payload(exc.model))(
                    scope, receive, send)
        except ModelUnknownOnAnyPlatform as exc:
            # 选择阶段就判定「谁都没有这个模型」：给 404，并把
            # 「模型在哪些平台存在」与当时可选平台写进错误体，
            # 免得客户端以为是自己拼错了名字却查不到该用哪个平台。
            logger.info("模型不属于任何平台 | model=%r declared=%s available=%s",
                        exc.model, exc.platforms, exc.available)
            return await JSONResponse(
                status_code=404,
                content=model_not_found_payload(exc))(scope, receive, send)
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

        # ---- 2.4) 固定目标也要过**分组授权** ----
        #
        # 分组只在「按模型选路」里判是不够的：模型后缀 / X-Platform / 绑定平台
        # 都能把请求钉到一个组外平台，授权一旦只覆盖自动选路，手输后缀就是
        # 绕过通道（§六 第 82 行）。这里对**已定目标**再核一次：
        #   * 只看「该目标本区真有这个模型」的情形（否则交给下面的
        #     _model_for_platform 报 model_not_on_platform / 404）；
        #   * 拒绝就发生在 dispatch，别让主应用拿组内的原生 ID 去上游换 400。
        if body:
            _pin_model = _model_of_body(body)
            _pin_key = _key_of(request)
            if (_pin_model and _pin_key is not None
                    and _pin_model.strip().lower() not in _GENERIC_MODEL_NAMES
                    and int(getattr(_pin_key, "group_id", 0) or 0)):
                _pin_allowed = None
                _pin_ok = True
                try:
                    with _db() as _gdb:
                        _pin_allowed = _key_group_models(_gdb, _pin_key)
                        # 只有「这个模型确实登记在某处」时才按分组拒绝：
                        # 谁都没登记的名字仍交给下面的选路给 404 / 未同步提示，
                        # 免得把「名字写错了」说成「不在分组」。
                        _pin_declared = bool(_model_platforms(_gdb, _pin_model))
                        if _pin_allowed is not None and _pin_declared:
                            _pin_ok = _group_allows(
                                _gdb, platform_id, _storage_of(platform_id),
                                realm, _pin_model, _pin_allowed)[0]
                except Exception:  # noqa: BLE001 - 判不了就不误杀
                    _pin_ok = True
                if _pin_allowed is not None and not _pin_ok:
                    logger.info("模型不在 Key 绑定的分组内（固定目标）| "
                                "model=%r platform=%r realm=%r",
                                _pin_model, platform_id, realm)
                    return await JSONResponse(
                        status_code=403,
                        content=model_not_in_group_payload(_pin_model))(
                            scope, receive, send)

        # ---- 2.5) 模型名对到**目标平台自己的**真实 id ----
        #
        # 同一个模型在不同平台的写法不同：
        #     GLM-5.3-Flash   WorkBuddy: glm-5.3-flash | Loomy: GLM-5.3-Flash
        # `/v1/models` 为了跨平台一致，对外统一发小写 slug（可加平台后缀）。
        # 上游只认自己的写法，直接把 slug 转过去就是「模型不存在」400。
        # 所以平台定下来之后，按 (public platform id, storage, realm, model)
        # 收窄还原一次 —— 收窄是必须的：全局扫描同名时可能返回别家的拼法。
        if body:
            try:
                _spec_mp = registry.get(platform_id)
                _storage_mp = (str(_spec_mp.storage()).lower()
                               if _spec_mp is not None else "")
                _cur_mp = _model_of_body(body)
                if _cur_mp and _storage_mp:
                    with _db() as _rdb:
                        _real_mp = _model_id_in_target(
                            _rdb, platform_id, _storage_mp, realm, _cur_mp)
                    if _real_mp and _real_mp != _cur_mp:
                        _send_body(_replace_model(original_body, _real_mp))
                        logger.debug("模型名按平台还原 | %s -> %s (%s/%s)",
                                     _cur_mp, _real_mp, _storage_mp, realm)
            except Exception:  # noqa: BLE001 - 还原失败就按原样转发
                logger.debug("平台内模型还原失败 model=%s", _model_of_body(body),
                             exc_info=True)

        # **带平台后缀的请求永远走平台网关**，哪怕它指向默认平台。
        # 理由：后缀是的「我要这个平台的实现」，交给主应用等于
        # 用错实现（主应用没有平台侧的账号池 / 倍率 / 风控）。
        # 过「当前平台只是看账号积分用的，跟对话接口有啥关系」——
        # 这里就把这个耦合切断。
        explicit_platform = bool(suffix_platform)

        # **兜底 1（收敛版）：模型确实不属于「用户想要的平台」时才改道。**
        #
        # 场景：客户端发 `deepseek-flash-qoder`，但后缀解析失败
        # （名字拼错平台、或用了不含后缀的可读名如 `deepseek-flash`），
        # 于是落到当前平台 → 主应用白名单里没有 → 400。
        # 这里反查一次：确实做不了才改道，不让主应用插手。
        #
        # ## 旧实现为什么让「独占 workbuddy 的 Key」全部打到 trae
        #
        # 它用 `platform_of_model()` 判断归属，而那个函数**只取第一条**同名
        # `model_configs`。`deepseek-v4.1-flash` 在 trae(id=9) / workbuddy(57) /
        # loomy(72) 都有登记，第一条是 trae —— 于是每条请求都被改道到 trae：
        # Key 绑定的平台、显式 `X-Platform` 头、平台禁用开关全部被绕过。
        # 实测：独占 workbuddy 的 Key 两天内 trae 785 次 / workbuddy 52 次；
        # trae-cn 已在「平台网关」里禁用，却仍在承载全部流量。
        #
        # 现在改成和「按模型选路」同一套候选判断（有号 + 健康 + 未暂停），
        # 并加三道闸门：
        #   1. 显式 `X-Platform` 头：用户已表态，一个字节都不改；
        #   2. 用户想要的平台（Key 绑定平台，否则默认平台）**能服务**这个模型
        #      -> 不动。这就是「绑了 workbuddy 就该走 workbuddy」；
        #   3. 改道目标必须**没被禁用**且**有网关实现**。
        if not explicit_platform and body and not _explicit_selector(request):
            try:
                _m = _model_of_body(body)
                if _m:
                    _dis0 = _disabled_platforms()
                    with _db() as _rdb:
                        _target = _reroute_target(
                            _rdb, _m, _key_of(request), _dis0,
                            current=(platform_id, realm), scope=scope)
                    if _target is not None:
                        platform_id, realm = _target[0], _target[1] or realm
                        explicit_platform = True
                        suffix_platform = platform_id
                        # **改道后换掉 scope 里的 Key 缓存**：目标平台网关
                        # 选号走 `shared.key_row`（读 `_wb_api_key`），换成
                        # platform 已脱空的 `_ReroutedKey`，否则 `scope_accounts`
                        # 的 `Account.platform = Key绑定平台` 会把账号池锁回
                        # 原平台 —— 改道判定成功、执行必然 503。
                        # 独占账号 / 分组 / 额度全部保留（见 _ReroutedKey）。
                        _real_key = _key_of(request)
                        if _real_key is not None and str(
                                getattr(_real_key, "platform", "") or "").strip():
                            scope["_wb_api_key"] = _ReroutedKey(_real_key)
                        logger.info("dispatch 兜底改道 | model=%r -> platform=%r (%s)",
                                    _m, platform_id, _target[2])
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
        # **一次请求一条可核对的路由记录**（计划 §八 行6）：原始请求名 ->
        # 目标模型名 + 平台 + 区。只放路由字段，不含正文/凭证。
        logger.info("dispatch 分叉 | requested=%r target_model=%r platform=%r "
                    "realm=%r explicit=%s has_gw=%s default=%r "
                    "suffix_platform=%r",
                    original_model, (_model_of_body(body) if body else ""),
                    platform_id, realm, explicit_platform, _has_gateway,
                    registry.DEFAULT_PLATFORM, suffix_platform)

        if platform_id == registry.DEFAULT_PLATFORM and not explicit_platform:
            # **已下线平台的模型名：直接 404，不许漏进主应用。**
            # 没有任何平台认领它、主应用白名单里也没有；漏过去就是拿一个
            # 不存在的模型打真上游（挂起/报错，还白占默认平台额度）。
            if body:
                _retired_model = _model_of_body(body)
                _retired = _retired_platform_of(_retired_model)
                if _retired:
                    logger.info("已下线平台的模型请求被拒 | model=%r platform=%r",
                                _retired_model, _retired)
                    return await JSONResponse(
                        status_code=404,
                        content={"error": {
                            "message": "模型 %s 所属的平台（%s）已下线，请改用其它模型"
                                       % (_retired_model, _retired),
                            "type": "invalid_request_error",
                            "code": "model_not_found",
                        }})(scope, receive, send)
            # 默认平台走主应用。但**模型名要先按本平台目录收窄**：
            # 主应用只认白名单里的 id，`deepseek-flash` 这类聚合名会被直接
            # 400「模型不存在」。这里用**目标平台限定**的映射
            # （storage + realm）从原始字节生成 body，不再做全局反查 ——
            # 全局 `upstream_of()` 会按行序把 Marvis 的可读名反查成它的槽位
            # `main-auto`，于是 WorkBuddy 主应用收到的是别人的模型名
            # （计划 §一 截图那条，这是第二处同样的错）。
            if body:
                try:
                    spec0 = registry.get(platform_id)
                    storage0 = spec0.storage() if spec0 else platform_id
                    model0 = _model_of_body(body)
                    with _db() as db0:
                        mapped0 = _model_id_in_target(
                            db0, platform_id, storage0, realm, model0)
                    if mapped0 and mapped0 != model0:
                        _send_body(_replace_model(original_body, mapped0))
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
            if _retired_platform_of(str(platform_id) + "-") or str(platform_id) in \
                    RETIRED_PLATFORM_PREFIXES.values():
                # 显式点了已下线平台（`X-Platform: doubao`）：给 404 而不是 503，
                # 免得用户以为是暂时故障、反复重试。
                return await JSONResponse(
                    status_code=404,
                    content={"error": {
                        "message": "平台 %s 已下线，请改用其它平台" % platform_id,
                        "type": "invalid_request_error",
                        "code": "platform_retired"}})(scope, receive, send)
            logger.warning("平台 %s 未装配网关实现，请求被拒绝", platform_id)
            return await JSONResponse(
                status_code=503,
                content={"error": {
                    "message": f"平台 {platform_id} 未装配网关实现",
                    "type": "server_error", "code": "platform_unavailable"}})(scope, receive, send)

        # ---- 2.7) 本次请求的「意图事实」----
        #
        # 旧实现用 `explicit_platform` 一个变量承担三种含义（「回显后缀」/
        # 「不许回主应用」/「用户指定了平台」），而 `auto_route` 只看
        # `_explicit_selector()` 加**当时的** Key —— 改道后 scope 里的 Key 换成
        # 了 platform 已脱空的 `_ReroutedKey`，同一个请求会突然变成「可以跨平台
        # 重试」。这里把三个事实分开保存，并**在改道之前**取值：
        #
        #   client_platform  客户端用 X-Platform / ?platform= 指定了平台
        #   bound_platform   **原始** Key 绑定的平台
        #   suffix_platform  模型名自带的平台后缀
        #
        # 三者任一成立 = 目标已固定，换平台就是违背用户意图；再加聚合开关，
        # 才是「允许跨平台重试」的完整条件（计划 §五 第 70 行）。
        key = _key_of(request)
        client_platform = _explicit_selector(request)
        bound_platform = (str(getattr(key, "platform", "") or "").strip()
                          if key is not None else "")
        aggregate = _aggregate_enabled(key)
        auto_route = (bool(body) and not client_platform and not bound_platform
                      and not suffix_platform and aggregate)
        model = _model_of_body(body) if body else ""

        # 显式指定平台（或 Key 绑定平台）时也要做模型名映射：见 _model_for_platform
        # 的注释 —— 不映射时 Loomy/Qoder 会拿默认模型"假装成功"。
        if model and not auto_route and spec is not None:
            try:
                with _db() as db:
                    mapped, error = _model_for_platform(
                        db, spec.storage(), model, realm=realm)
            except Exception:  # noqa: BLE001 - 映射失败就按原样转发
                mapped, error = model, None
            if error is not None:
                logger.info("模型不属于目标平台 model=%s platform=%s", model, platform_id)
                # error 自带 status（被禁用是 403，找不到才是 404）。写死 404 会
                # 让「模型已禁用」伪装成「名字写错了」，客户端只会去改名字。
                code = 404
                payload_error = error
                if isinstance(error, dict):
                    code = int(error.get("status") or 404)
                    payload_error = {key: value for key, value in error.items()
                                     if key != "status"}
                return await JSONResponse(status_code=code,
                                          content={"error": payload_error})(scope, receive, send)
            if mapped != model:
                _send_body(_replace_model(original_body, mapped))

        # **每个候选用自己的目录拼写，且都从原始字节派生**（计划 §四 第 57 行）：
        # 拿「已经改写过的 body」当输入，会把前一个平台的拼写带到下一个平台。
        def _body_for_target(target_model: str) -> bytes:
            if not target_model or target_model == original_model:
                return original_body
            return _replace_model(original_body, target_model)

        # 首选目标自己的写法：显式目标已在 2.6) 映射过（幂等），自动选路那段
        # 被跳过，所以这里无条件再按 (platform, storage, realm) 收窄一次。
        primary_model = _model_of_body(body) or original_model
        if body:
            try:
                with _db() as db:
                    primary_model = _model_id_in_target(
                        db, platform_id, _storage_of(platform_id), realm,
                        primary_model) or primary_model
            except Exception:  # noqa: BLE001 - 映射失败按原样转发
                logger.debug("首选目标模型映射失败 platform=%s", platform_id,
                             exc_info=True)
        # 候选的 (public id, realm, 该目标自己的 model) 三元组。
        # 首选目标自己的写法：显式目标已在 2.6) 映射过（幂等），自动选路那段
        # 被跳过，所以这里无条件再按 (platform, storage, realm) 收窄一次。
        typed: list[tuple[str, str, str]] = [(platform_id, realm, primary_model)]
        # 「只有历史失败记录」的候选（软健康降级）。它们的**资格**与健康候选
        # 完全一样（都过了硬限制），区别只在**顺序**（计划 §五 第 73 行）。
        degraded_targets: set[tuple[str, str]] = set()
        # 只有「按模型自动选路」才允许多平台重试（计划 §五 第 70 行）：
        # 后缀 / 客户端头或查询参数 / Key 绑定平台，任一成立都换平台就是违背
        # 用户意图；聚合开关关闭时同样不跨平台。
        if auto_route and model:
            try:
                with _db() as db:
                    servable, degraded = _candidates_cached(db, model, key, scope)
            except Exception:  # noqa: BLE001
                servable, degraded = [], []
            degraded_targets = {(str(pid), str(realm))
                                for pid, realm, _mapped in degraded}
            seen = {(platform_id, realm)}
            # **软降级只影响顺序**：健康的排前面，degraded 追加在后面。
            # 两者都已过硬限制（禁用/暂停/归属/账号范围/冷却），所以把
            # degraded 放到尾部既不会把被排除者「加回来」，又让
            # 「唯一候选只有历史失败记录」仍有一次机会。
            for pid, target_realm, mapped in list(servable) + list(degraded):
                if (pid, target_realm) in seen:
                    continue
                seen.add((pid, target_realm))
                typed.append((pid, target_realm, mapped))

        # **只保留当前分支真能调用的目标**（计划 §五 第 74 行）：缺 adapter 的
        # 条目不能占掉重试名额 —— 否则「最后一次尝试」（index == len-1）永远
        # 落不到真正可用的最后一项上，可重试的 503 会被当成终态返回。
        usable: list[tuple[str, str, bytes, str, object]] = []
        for pid, target_realm, mapped in typed:
            target_spec = _spec_with_gateway(pid, target_realm)
            target_module = _module_of(target_spec)
            target_dispatch = (getattr(target_module, "dispatch", None)
                               if target_module is not None else None)
            if target_dispatch is None:
                logger.debug("候选平台 %s 未装配网关实现，跳过", pid)
                continue
            # 回显用 **spec.id**（`trae-cn`）而不是存储名（`trae`）：
            # 面板上看到的是「Trae 国内版」，`x-gateway-platform`
            # 回 `trae` 对不上，排查时会以为落错平台。
            usable.append((pid, target_realm, _body_for_target(mapped),
                           str(getattr(target_spec, "id", "") or pid),
                           target_dispatch))
        usable = usable[:3]
        if not usable:
            return await JSONResponse(
                status_code=503,
                content={"error": {"message": "没有可用的上游平台",
                                   "type": "server_error",
                                   "code": "no_platform"}})(scope, receive, send)

        # 本次请求**实际尝试**过的目标与状态：既是 §五 第 75 行的诊断依据，
        # 也是「所有候选只有历史失败记录」这一分类的判据。
        all_degraded = bool(usable) and all(
            (str(pid), str(target_realm)) in degraded_targets
            for pid, target_realm, _body, _public, _dispatch in usable)
        attempts: list[dict] = []

        async def _degraded_only():
            """「所有候选只有历史失败记录」的终态响应（四类结论之一）。

            只在**本次尝试全部是 degraded 目标**且都明确失败时使用：它必须与
            「无人登记 404」「全被禁用 403」「无可用账号（平台的可用性错误）」
            区分开，否则运维分不清该补账号、该解禁、还是该等冷却。
            """
            logger.info("候选平台都只有历史失败记录 model=%s attempts=%r",
                        model, attempts)
            return await JSONResponse(
                status_code=503,
                content={"error": {
                    "message": "模型 %s 的候选平台都只有历史失败记录"
                               "（已尝试 %d 个），请稍后重试或换平台"
                               % (model, len(attempts)),
                    "type": "server_error",
                    "code": "candidates_degraded",
                    "attempts": attempts,
                }})(scope, receive, send)

        last_response = None
        for index, (target, target_realm, target_body, public_id,
                    target_dispatch) in enumerate(usable):
            # 每个**待发的后续候选**再核一次禁用/暂停（计划 §五 第 72 行）：
            # 禁用集合与平台暂停都是 3 秒缓存/动态状态，选路之后可能变化。
            if index and (_is_disabled(target, _disabled_platforms())
                          or _platform_paused(target)):
                logger.info("候选平台 %s 已被禁用或暂停，跳过", public_id)
                continue
            # 一行日志读出「本次调用的 public id / 区 / 模型 / 尝试序号」
            # （计划 §五 第 75 行）。**不放请求正文、凭证、账号令牌。**
            logger.info("dispatch 尝试 | attempt=%d/%d public_platform=%r "
                        "realm=%r model=%r requested=%r",
                        index + 1, len(usable), public_id, target_realm,
                        _model_of_body(target_body), original_model)
            _set_state(scope, public_id, target_realm)
            scope["_wb_body"] = target_body
            # `target_body` 与 `scope["_wb_body"]` 是**同一份字节**。只有空 body 的
            # 请求才会回落到 `request`：多候选必然带 body（`auto_route` 要求有
            # 模型名），所以这里不存在「沿用上一跳 body」的窗口。
            attempt_request = (Request(scope, _replay_receive(target_body, tail))
                               if target_body else request)
            response = await _run_platform(target_dispatch, attempt_request,
                                            path, public_id)
            status = int(getattr(response, "status_code", 200) or 200)
            attempts.append({"platform": public_id, "status": status})
            if _is_uncertain(response):
                # 平台内部异常：副作用可能已经发生，**不许**换平台重放。
                # 哪怕状态码是「可重试」的 502，这次也和"平台明确说做不了"不同。
                logger.warning(
                    "平台 %s 内部异常，结果不确定，停止尝试其它候选平台", public_id)
                return await response(scope, receive, send)
            if status not in _RETRYABLE_STATUS or index == len(usable) - 1:
                # 最后一跳：全部候选都是 degraded 且给出可重试状态时，换成
                # 本次请求独有的诊断码（平台原始 503 正文里没有这个信息）。
                if all_degraded and status in _RETRYABLE_STATUS:
                    return await _degraded_only()
                return await response(scope, receive, send)
            last_response = response
            logger.info("平台 %s 返回 %s，尝试下一个候选平台", public_id, status)

        if last_response is not None:
            if all_degraded:
                return await _degraded_only()
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
