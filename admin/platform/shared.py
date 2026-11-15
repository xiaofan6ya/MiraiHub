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
import os
import threading
import time
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func, or_
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
# 批量执行（签到 / 保活 / 刷余额这类「每个号一次上游调用」的任务）
# ---------------------------------------------------------------------------
#: 定时批处理的并发度。
#:
#: 为什么是 8：这些任务 100% 是**网络等待**（每个号 1~3 次上游 HTTP，几百毫秒
#: 到几秒），串行时 100 个号就是几分钟；并发到 8 能把墙钟时间压到 1/6 左右，
#: 又不至于把上游风控和本地连接池打满（实测 16 并发时上游开始回 429，
#: 反而更慢）。可用 `ADMIN_BATCH_CONCURRENCY` 覆盖。
def batch_concurrency() -> int:
    import os

    try:
        value = int(os.getenv("ADMIN_BATCH_CONCURRENCY", "8") or 8)
    except (TypeError, ValueError):
        value = 8
    return max(1, min(32, value))


def iter_account_batches(db: Session, *, storage: str, realm: str = "",
                         status: str = "active", batch: int = 20):
    """**按主键分批**产出账号 id 列表（不把整个号池读进内存）。

    ## 为什么不能 `query(Account).all()`

    几千个号一次性 `.all()` 意味着：几千个 ORM 对象常驻（每个还带
    `auth_json` 几百 KB 的整条凭证）、身份映射不清、后续遍历期间任何
    关系加载都会再触发查询。号池越大越卡，最终表现为「点一下签到，
    服务先卡住十几秒」。

    这里用**keyset 分页**（`id > last_id ORDER BY id LIMIT n`），
    主键索引上走，内存里同时在手的只有一批 id。
    """
    from admin.models import Account

    last_id = 0
    while True:
        query = (db.query(Account.id)
                 .filter(Account.platform == storage, Account.id > last_id))
        if status:
            query = query.filter(Account.status == status)
        realm_cond = realm_domain_cond(realm)
        if realm_cond is not None:
            query = query.filter(realm_cond)
        rows = query.order_by(Account.id.asc()).limit(max(1, batch)).all()
        if not rows:
            return
        ids = [int(row[0]) for row in rows]
        last_id = ids[-1]
        yield ids


def run_account_batch(account_ids, worker, *, concurrency: int | None = None,
                      label: str = "") -> list[dict]:
    """在**线程池**里并发处理一批账号；每个任务用**自己的 Session**。

    `worker(session, account)` 负责单个账号的全部动作（含提交），返回一个
    轻量 dict（**不要返回 ORM 对象** —— 那会把对象挂在使用者线程的身份映射上，
    跨线程用必炸，且拖住整批对象不释放）。

    异常一律收敛成 `{"ok": False, "error": ...}`，绝不让一个坏号打断整批 ——
    「批量任务跑到一半整个挂掉」是这类后台作业最常见的事故。

    线程数固定为 `concurrency`（默认见 `batch_concurrency()`）：
    任务本身是 I/O 等待，线程是廉价且最直接的并发手段，不需要 async 改造；
    SQLAlchemy 的同步 Session 也**不能跨线程共享**，每任务一个才是对的。
    """
    from concurrent.futures import ThreadPoolExecutor

    from admin.db import SessionLocal
    from admin.models import Account

    ids = [int(x) for x in (account_ids or []) if x]
    if not ids:
        return []
    workers = max(1, int(concurrency or batch_concurrency()))

    def one(account_id: int) -> dict:
        session = SessionLocal()
        try:
            account = session.get(Account, account_id)
            if account is None:
                return {"account_id": account_id, "ok": False,
                        "error": "账号已不存在"}
            result = worker(session, account)
            if isinstance(result, dict):
                return result
            return {"account_id": account_id, "ok": True}
        except Exception as exc:  # noqa: BLE001 - 单个号失败不能拖垮整批
            logger.debug("%s 处理账号 %s 失败", label or "batch", account_id,
                         exc_info=True)
            return {"account_id": account_id, "ok": False,
                    "error": ("%s: %s" % (type(exc).__name__, exc))[:200]}
        finally:
            try:
                session.rollback()      # 未提交的改动一律丢弃，避免脏写
            except Exception:  # noqa: BLE001
                pass
            session.close()

    # 线程数不得超过任务数：100 个号配 8 线程没问题，2 个号别开 8 个空转
    workers = min(workers, len(ids))
    with ThreadPoolExecutor(max_workers=workers,
                            thread_name_prefix=(label or "batch")) as pool:
        # `map` 保持输入顺序，结果天然稳定（前端按账号 id 排就好读）
        return list(pool.map(one, ids))


# ---------------------------------------------------------------------------
# 凭证有效期（各平台共用的「四态」拼装）
# ---------------------------------------------------------------------------
#: 有效期告急阈值（天）。超过这个天数就不再提示。
RENEWAL_WARN_DAYS = 7.0


def parse_auth_json(auth_json) -> dict:
    """`auth_json`（字符串或已解析的 dict）→ dict，坏数据给空字典。"""
    if isinstance(auth_json, dict):
        return auth_json
    try:
        data = json.loads(auth_json or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def credential_window(*, expires_at=None, refresh_expires_at=None,
                      has_refresh: bool = False,
                      warn_days: float = RENEWAL_WARN_DAYS,
                      now: float | None = None) -> dict:
    """把「到期时间 + 有没有 refresh」摊成 **admin 侧统一消费的四态结构**。

    为什么要这个函数：`admin/credential_expiry.py` 只认识这一份形状。
    平台各自实现时最容易写歪的两处是「单位」（毫秒当秒）和「状态判定」
    （过期了还说 ok）—— 统一在这里算，平台只负责从凭证里把 `expires_at`
    取出来。

    所有时间参数都是 **epoch 秒**（毫秒请先 `seconds_from()` 或自己除 1000）。
    """
    now = now if now is not None else time.time()

    def _state(ts):
        if not ts:
            return "unknown", None
        days = round((float(ts) - now) / 86400.0, 1)
        if days <= 0:
            return "expired", days
        return ("expiring" if days <= warn_days else "ok"), days

    state, days_left = _state(expires_at)
    refresh_state, refresh_days_left = _state(refresh_expires_at)
    # 「还救不救得回来」：有 refresh 且续期窗口没过
    renewable = bool(has_refresh) and refresh_state != "expired"
    return {
        "supported": True,
        "expires_at": int(expires_at) if expires_at else None,
        "days_left": days_left,
        "state": state,
        "refresh_expires_at": int(refresh_expires_at) if refresh_expires_at else None,
        "refresh_days_left": refresh_days_left,
        "refresh_state": refresh_state,
        "renewal_known": bool(expires_at or refresh_expires_at),
        "renewable": renewable,
        "has_refresh": bool(has_refresh),
    }


def seconds_from(value) -> float:
    """把上游给的到期时间归一成 **epoch 秒**。

    上游单位极不统一（毫秒 / 秒 / 数字串 / ISO 串都见过）：
      * 数字 > 1e11 视为毫秒
      * 数字串走同一规则
      * 其它按 ISO8601 解析
    取不到一律返回 0 —— **不要**猜默认值，猜错会让「已过期」显示成「还早」。
    """
    if value is None or value == "":
        return 0.0
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        number = float(value)
        return number / 1000.0 if number > 1e11 else number
    text = str(value).strip()
    if not text:
        return 0.0
    if text.replace(".", "", 1).isdigit():
        try:
            return seconds_from(float(text))
        except ValueError:
            return 0.0
    try:
        from datetime import datetime

        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


_SNAPSHOT_REFRESH: dict[str, dict] = {}
_SNAPSHOT_REFRESH_LOCK = threading.Lock()


def import_refresh_concurrency() -> int:
    """导入后自动刷余额的并发数（环境变量 `ADMIN_IMPORT_REFRESH_CONCURRENCY`）。

    单账号刷新 = 1~2 次上游往返（实测 WorkBuddy ~1.2s/号），12 并发
    可以把 100 个号的导入补刷压到 ~15 秒内。可调上限钳到 24：再高
    上游会开始返回限流，反而拖慢整体。
    """
    raw = os.environ.get("ADMIN_IMPORT_REFRESH_CONCURRENCY", "").strip()
    try:
        value = int(raw) if raw else 12
    except ValueError:
        value = 12
    return max(1, min(value, 24))


#: 正在排队补刷的账号 id —— 同一个号一次导入只排一队，避免重复打上游
_IMPORT_REFRESH_QUEUED: set[int] = set()
_IMPORT_REFRESH_LOCK = threading.Lock()
#: 合批窗口：逐条 import_record 的批量导入在窗口内合并成一个后台批次，
#: 否则 500 条 = 500 个线程 × 线程池，纯浪费
_IMPORT_REFRESH_PENDING: dict[str, list[int]] = {}
_IMPORT_REFRESH_COALESCE_SEC = 0.75

#: 额度类失败（402）在「上游确实发过配额」的号上，用**降级窗口**隔离多久。
#:
#: 为什么不直接清零余额：一次瞬时 402 就把 `balance_remain` 写成 0，
#: 会让这个号永久退出候选集（`balance_total` 却还是 4800）——
#: 攒够几十个之后整个平台就只剩「所有账号额度已用尽」。
#: 降级窗口是可恢复的隔离：窗口内不选它，到期后给一次机会，
#: 期间任何一次余额同步都会把真实数字写回来。
#:
#: 30 分钟：比 600 秒的普通冷却长得多（能挡住「每 10 分钟白撞一次 402」），
#: 又不至于让一个只是被限流的号停摆一整天。
QUOTA_DEGRADE_SECONDS = 1800


def _spawn_import_refresh(platform_id: str, fresh: list[int]) -> None:
    """真正的后台批次（独立线程 + 线程池 + 独立 Session）。"""

    def _refresh_one(session, account_id: int) -> None:
        from admin.platform import registry as _registry

        spec = _registry.get(platform_id)
        if spec is None:
            raise RuntimeError("平台未注册：%s" % platform_id)
        gateway = _registry.load_platform_gateway(spec, spec.realm)
        if gateway is None:
            raise RuntimeError("平台未实现管理契约：%s" % platform_id)
        # 标准入口是 AccountOps.refresh（各平台余额口径不同，契约 §AccountOps）；
        # 个别平台把 refresh 直接挂在 gateway 上 —— 两种都认。
        ops = (gateway.account_ops()
               if hasattr(gateway, "account_ops") else None)
        if ops is not None and hasattr(ops, "refresh"):
            ops.refresh(session, account_id, force=True)
        elif hasattr(gateway, "refresh"):
            gateway.refresh(session, account_id, force=True)
        else:
            raise RuntimeError("平台未实现 refresh：%s" % platform_id)

    def _run() -> None:
        from admin.db import SessionLocal

        try:
            workers = min(import_refresh_concurrency(), len(fresh))
            from concurrent.futures import ThreadPoolExecutor

            def one(account_id: int) -> None:
                for attempt in (1, 2):
                    try:
                        with SessionLocal() as session:
                            _refresh_one(session, account_id)
                            session.commit()
                        break
                    except Exception:  # noqa: BLE001 - 单号失败不拖垮整批
                        if attempt == 2:
                            logger.warning("导入后余额刷新失败 平台=%s 账号=%s",
                                           platform_id, account_id, exc_info=True)
                        else:
                            time.sleep(2.0)
                    finally:
                        with _IMPORT_REFRESH_LOCK:
                            _IMPORT_REFRESH_QUEUED.discard(account_id)

            with ThreadPoolExecutor(max_workers=workers,
                                    thread_name_prefix="import-refresh") as pool:
                list(pool.map(one, fresh))
        except Exception:  # noqa: BLE001 - 后台任务绝不冒泡
            logger.exception("导入后余额刷新异常（不影响已入库账号）")
            with _IMPORT_REFRESH_LOCK:
                _IMPORT_REFRESH_QUEUED.difference_update(fresh)

    threading.Thread(target=_run, name="import-refresh-%s" % platform_id,
                     daemon=True).start()


def schedule_import_refresh(platform_id: str, account_ids, *,
                            with_retry: bool = True) -> int:
    """账号入库落库之后，把余额刷新丢到后台线程，HTTP 立即返回。

    ## 为什么需要它

    「扫描本机 / 导入文件」落库时只写凭证，余额列是列默认值 0 —— 各平台
    只有 WorkBuddy 自己实现了导入后补刷（`accounts.py:_refresh_pending_async`），
    Trae / 小浣熊等平台的导入是「纯落库」，用户进面板看到的就是 0，还得
    手动点一次「刷新余额」。这里提供一个**框架层**的通用补刷：平台自己
    不用再写线程池，挂一行调用即可。

    ## 刷新走谁

    调**该平台自己**的 `platform_gateway.refresh(db, account_id, force=True)`
    （Gateway 契约 §AccountOps）——各平台的余额口径不同（Trae 是美元额度、
    小浣熊是积分、MiniMax 是签到积分），框架层不认识任何一家的接口，
    只负责调度。平台没实现管理契约时安静返回 0（不报错）。

    约束：
      * **独立 Session**：SQLAlchemy Session 不能跨线程共享；
      * **去重**：同一个号已在补刷队列时不重复排队；
      * **合批**：`_IMPORT_REFRESH_COALESCE_SEC` 窗口内同平台的多次调用
        合并成一个后台批次（批量导入是逐条 `import_record` 的）；
      * **重试**：单号失败重试 1 次（导入的凭证刚落库，第一次可能撞上
        上游冷启动）；重试仍失败只记日志，绝不冒泡；
      * **并发可配**：`ADMIN_IMPORT_REFRESH_CONCURRENCY`（默认 12）。

    返回实际排入补刷的账号数。`with_retry=False` 预留给调用方自带重试的
    场景（当前内部批次恒定重试，参数仅为签名兼容保留）。
    """
    del with_retry  # 见 docstring：批次内恒定重试一次
    ids = []
    for value in (account_ids or []):
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue
    if not ids:
        return 0

    flush_at = time.monotonic() + _IMPORT_REFRESH_COALESCE_SEC
    del flush_at
    with _IMPORT_REFRESH_LOCK:
        fresh = [i for i in ids if i not in _IMPORT_REFRESH_QUEUED]
        _IMPORT_REFRESH_QUEUED.update(fresh)
        if fresh:
            _IMPORT_REFRESH_PENDING.setdefault(platform_id, []).extend(fresh)
            scheduled = len(_IMPORT_REFRESH_PENDING[platform_id])
        else:
            scheduled = 0
    if not fresh:
        return 0

    def _flush() -> None:
        with _IMPORT_REFRESH_LOCK:
            batch = _IMPORT_REFRESH_PENDING.pop(platform_id, [])
        if batch:
            _spawn_import_refresh(platform_id, batch)

    if scheduled == len(fresh):
        # 本平台当前没有待触发的批次 —— 由这次调用在窗口结束后触发；
        # 已有排队中的批次则等它一起带走（合批）。
        def _timer() -> None:
            time.sleep(_IMPORT_REFRESH_COALESCE_SEC)
            _flush()

        threading.Thread(target=_timer, daemon=True,
                         name="import-refresh-timer-%s" % platform_id).start()
    return len(fresh)


def kick_background_refresh(name: str, account_ids, worker, *,
                            max_items: int = 8, min_gap: float = 10.0) -> bool:
    """把「刷新过期的额度快照」丢到后台线程，**不阻塞当前请求**。

    ## 为什么额度刷新不能留在请求路径里

    账号列表接口（`/accounts`）是面板每次刷新都会打的。原实现会在请求线程里
    对**每个快照过期的号**同步调一次上游 HTTP 且串行 —— 50 个号的池子稳定
    2~3 秒，几百个号直接几十秒。用户看到的「这几个接口太慢」就是它。

    额度是**展示数据**：晚半秒没人受影响，但它挡住整页渲染就是问题。
    所以列表只读 DB 快照立刻返回，刷新丢到这里。

    约束（避免后台线程失控）：
      · **单飞**：同一个 `name` 同一时刻只有一个刷新线程；
      · **限频**：两次刷新至少间隔 `min_gap` 秒（翻页很频繁，不能每页都排）；
      · **限量**：一次最多刷 `max_items` 个号；
      · **绝不冒泡**：任何异常都吞掉，只回滚自己的 Session。

    `worker(session, account)` 负责真正的同步；本函数替它提交/回滚。
    """
    ids = [int(x) for x in (account_ids or []) if x]
    if not ids:
        return False
    now = time.monotonic()
    with _SNAPSHOT_REFRESH_LOCK:
        state = _SNAPSHOT_REFRESH.setdefault(name, {"running": False, "at": 0.0})
        if state["running"] or now - state["at"] < min_gap:
            return False
        state["running"] = True
        state["at"] = now
    targets = ids[:max_items]

    def run() -> None:
        from admin.db import SessionLocal
        from admin.models import Account

        try:
            with SessionLocal() as session:
                for account_id in targets:
                    try:
                        account = session.get(Account, account_id)
                        if account is None:
                            continue
                        worker(session, account)
                        session.commit()
                    except Exception:  # noqa: BLE001 - 单个号失败不影响其它
                        try:
                            session.rollback()
                        except Exception:  # noqa: BLE001
                            pass
        except Exception:  # noqa: BLE001 - 后台任务绝不冒泡
            logger.debug("后台刷新快照失败 name=%s", name, exc_info=True)
        finally:
            with _SNAPSHOT_REFRESH_LOCK:
                _SNAPSHOT_REFRESH[name]["running"] = False

    threading.Thread(target=run, name="%s-snapshot-refresh" % name,
                     daemon=True).start()
    return True


def realm_domain_cond(realm: str):
    """按区过滤账号的 where 条件（**空 domain 通配**），无区时返回 None。

    ## 为什么必须通配空 domain

    分区功能上线前导入的账号 `domain` 是空串 —— 严格相等过滤
    （`Account.domain == realm`）会让它们在**所有按区读取里隐身**：
    平台卡计数 0、`select_account` 选不到号（503「没有可用账号」）、
    调度器批量刷新跳过；而账号池页不按 domain 过滤，两边对不上。
    2026-11-07 实测误报：Qoder 国际版页面上有号，网关却 503。

    语义：空 domain = 「区域未知」。它在**任何一个区都可用**
    （发错区会被上游 403 → 冷却换号，与无分区平台的既有行为一致）；
    显式标了**其它区**的号仍然互不串，不会把国内流量发给国际号。
    新导入的账号都会写 domain，通配集合只减不增。
    """
    if not realm:
        return None
    from admin.models import Account

    return or_(Account.domain == realm,
               func.coalesce(Account.domain, "") == "")


def select_account(db: Session, *, storage: str, realm: str = "",
                   key: ApiKey | None = None, model: str = "",
                   exclude_ids=None, now: datetime | None = None,
                   min_balance: int | None = None,
                   ignore_capacity: bool = False) -> Account | None:
    """健康 + 归属 + 冷却 + 在途 + 余额 过滤后，选**最闲**的一个号。

    与 WorkBuddy 的 `_select_account` 同口径，只是平台/区域由调用方给定。
    之前 Qoder/Loomy 直接 `accounts_for()[0]`：不看在途、不看冷却、
    不看 Key 绑定 —— 并发一上来就全砸同一个号。

    ## `min_balance`（余额下限）

    余额为 0 的号**凭证是好的**，但调用必然被上游打回（Loomy 回 402
    insufficient_quota）。不在这里挡掉，就只能靠「撞一次 402 → 冷却 600 秒
    → 换号」，而冷却到期后它又会排回队首（这些号从没成功过，
    `last_used_at` 恒为 NULL，而选号按它升序）—— 于是每 10 分钟又要
    白撞几次 402。用户感受到的就是「一直 402，用不了」。

    `None` = 不过滤（默认，保持其它平台行为不变）；NULL 余额视为未知，
    仍然放行（历史数据没同步过余额，不该被误杀）。

    ## `ignore_capacity`（不要用 `None` 冒充"号用完了"）

    `POOL.available()` 会把**在途已满**的号滤掉。对 CodeArts 这种
    "只有 1 个号、并发靠排队"的平台，这会让 `select_account()` 返回 `None`，
    调用方于是报「没有可用账号」——号明明在，只是此刻都在途。
    置 True 就先返回最闲的那个（哪怕在途满），由调用方的
    `lease()` 失败分支去**排队**，而不是把"忙"说成"没有号"。
    """
    from admin.account_binding import scope_accounts
    from admin.pool import POOL

    now = now or datetime.utcnow()
    account_id, key_id = key_scope(key)
    query = (db.query(Account).populate_existing()
             .filter(Account.platform == storage, Account.status == "active"))
    query = scope_accounts(query, account_id, key_id, key=key)
    realm_cond = realm_domain_cond(realm)
    if realm_cond is not None:
        query = query.filter(realm_cond)
    if exclude_ids:
        query = query.filter(~Account.id.in_(exclude_ids))
    if min_balance is not None:
        query = query.filter(or_(Account.balance_remain.is_(None),
                                 Account.balance_remain > min_balance))
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

    rows = POOL.available(rows) if not ignore_capacity else rows
    if not rows:
        return None
    # 排序因子（从前到后）：
    #   1. 在途数 —— 优先最闲的号；
    #   2. **余额已知的优先** —— `min_balance` 只是「0 不进候选」，
    #      NULL（从没同步过余额）仍然放行，于是新导入的 100 个号里
    #      会混着一些其实是 0 积分的号，每次选中都白撞一个 402。
    #      把「已知有余额」排前面，未知的当兜底，失败率立刻降下来。
    #   3. 最久没用过的优先；最后按 id 稳定排序。
    account = min(rows, key=lambda row: (
        POOL.inflight.count(row.uid or ""),
        0 if row.balance_remain is not None else 1,
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


def exclude_identity(db: Session, exclude_ids: set[int], account: Account | None) -> None:
    """排除当前上游身份的全部重复数据库记录，避免换号仍命中同一 UID。"""
    if account is None:
        return
    if account.id:
        exclude_ids.add(int(account.id))
    uid = str(getattr(account, "uid", "") or "")
    if not uid:
        return
    rows = db.query(Account.id).filter(Account.uid == uid).all()
    exclude_ids.update(int(row[0]) for row in rows if row[0])


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
                 public_platform: str = "", cool_seconds: int = 0,
                 zero_balance: bool = False, code: str = "",
                 signal: str = "", severity: str = "") -> None:
    """把失败写回账号，并按需要进哨兵。**不抛异常**。

    ## `zero_balance`：额度类失败要**落一个 0 到余额列**

    只冷却（`cool_seconds`）是不够的：冷却到期后这个号会重新排进候选，
    于是每 10 分钟又白撞一次 402 —— 用户看到的就是「又切到那个额度已用尽的号」。

    `balance_remain > 0 OR IS NULL` 才是选号条件（见 `select_account` 的
    `min_balance`），所以**把余额写成 0 就等于把它从候选里摘掉**，而且不会
    影响凭证（号还是 active）。等下一次余额同步 / 每日签到刷新出真实额度，
    `apply_quota` 会把这一列覆盖回正数，号自动回到池子里 —— 不需要人工解禁。
    """
    if account is None:
        return
    try:
        now = datetime.utcnow()
        account.last_err_at = now
        account.last_err_msg = (detail or kind or "")[:255]
        account.err_count = int(account.err_count or 0) + 1
        if zero_balance:
            # 只降不升：已经记 0 的保持 0，不要把它改回未知（NULL）——
            # NULL 会被选号当成「余额未知，放行」，等于又把空号放回队列。
            #
            # ⚠️ **但「额度类失败」不等于「这个号没额度」**（2026-11-09 实测）：
            # 一次瞬时的 402（并发打满 / 单模型限额 / 上游限流）就会把
            # `balance_remain` 永久写成 0，而 `balance_total` 还是 4800。
            # 于是号池里几十个号全被这一行摘掉，请求直接回
            # 「所有账号的积分都已用尽」—— 用户看到的正是
            # 「账号池这么多账号都没额度了吗」。
            #
            # ## 判据：`balance_total` 是「上游发过多少配额」
            #
            # `apply_quota()` 写的是 `balance_remain = balance_total = available`。
            # 所以 **`balance_total > 0` 而 `balance_remain == 0` 只可能来自
            # 这里的清零**，上游同步永远不会产生这个组合。
            #
            # 规则因此变成：
            #   · 有配额痕迹（total > 0）→ **不动余额**（那是事实数据），
            #     改用**降级窗口**（`degrade_until`）把它挪出候选集；
            #     窗口到期 + 下次余额同步就能回来，不会永久判死；
            #   · 没有配额痕迹 → 才降成 0（这个号上游确实没给钱）。
            granted = int(account.balance_total or 0) > 0
            current = int(account.balance_remain or 0)
            if not granted or current <= 0:
                account.balance_remain = 0
            else:
                # 降级窗口是**可恢复**的隔离：冷却到期它会回来，
                # 但每次 402 都重新续 30 分钟，不会变成「每 10 分钟白撞一次」。
                until = now + __import__("datetime").timedelta(
                    seconds=QUOTA_DEGRADE_SECONDS)
                if account.degrade_until is None or account.degrade_until < until:
                    account.degrade_until = until
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
            # **不要把 HTTP 状态码当业务码传**（以前传的就是 `status`）：
            # 403 在 WorkBuddy 是「出口被拦」，在别的平台可能只是额度不足，
            # 混用会让哨兵给出错误的信号名与严重程度。
            # 平台自己知道真实码就传 `code`；不知道就留空，
            # 让哨兵按 `kind` 归类（限速/排队/额度/登录失效都有对应 severity）。
            sentinel.note_failure(public_platform, int(account.id or 0), kind,
                                  code=str(code or ""), status=int(status or 0),
                                  detail=(detail or "")[:200], model=model or "",
                                  signal=signal or "", severity=severity or "")
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
    # **缓存命中：上游没给就存 NULL，不要存 0**。
    #
    # 这一条是「Trae 命中率显示 0」的根因：原来默认 `cached = 0`，
    # 即使上游压根没回任何缓存字段，也会写一个 0 进库。前端拿到的就是
    # 「命中率 0.00%」，用户以为缓存失效 —— 实际是**上游不统计缓存**。
    #
    # 现在区分「真的 0」（上游明确回了 0）和「未知」（一个字段都没有）：
    #   · 上游给了 → 用真实值（包括 0）
    #   · 上游什么都没给 → NULL，前端渲染成 “—”
    cached = None
    try:
        from upstream_compat import cached_token_count

        cached = cached_token_count(usage)
    except Exception:  # noqa: BLE001 - 探测失败退回手工解析
        cached = None
    if cached is None:
        details = usage.get("prompt_tokens_details")
        if isinstance(details, dict) and "cached_tokens" in details:
            cached = _int(details.get("cached_tokens"))
        elif "cached_tokens" in usage:
            cached = _int(usage.get("cached_tokens"))
        elif "cache_read_input_tokens" in usage:
            cached = _int(usage.get("cache_read_input_tokens"))
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


#: 认可的下游思考档位（OpenAI 的 minimal~high + 一些客户端的 xhigh/max/ultra）。
REASONING_EFFORTS = frozenset({
    "off", "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra",
    "auto",
})


def reasoning_effort_of(body: dict) -> str:
    """从下游请求体里读出「思考程度」，只保留有限枚举，绝不落原始 payload。

    三种写法都要认（客户端的方言不统一）：

      * OpenAI：`reasoning_effort: "high"`
      * Responses：`reasoning: {"effort": "high"}`
      * Anthropic：`thinking: {"type": "enabled", "budget_tokens": 8192}`

    Anthropic 没有档位概念，只有预算，所以按预算记成 `budget:<n>`；
    这比"统一折算成 high"更诚实 —— 日志里要能看出客户端到底要了多少。

    **为什么这个函数必须放 shared**：以前只有 WorkBuddy 那条主链路
    （`proxy.py`）记了它，CodeArts / Loomy / Qoder / Trae / Raccoon 各自记账时
    一个字都没传，于是用量日志里这些平台永远是「默认 / 未记录」——
    看起来像"用户没设"，实际是"网关没记"。
    """
    if not isinstance(body, dict):
        return ""
    effort = body.get("reasoning_effort") or body.get("reasoningEffort")
    reasoning = body.get("reasoning")
    if not effort:
        effort = reasoning.get("effort") if isinstance(reasoning, dict) else reasoning
    if isinstance(effort, str) and effort.strip().lower() in REASONING_EFFORTS:
        return effort.strip().lower()
    thinking = body.get("thinking")
    if isinstance(thinking, dict):
        if thinking.get("type") == "disabled":
            return "off"
        budget = thinking.get("budget_tokens")
        if isinstance(budget, int) and not isinstance(budget, bool) and 0 <= budget <= 10**9:
            return "budget:%d" % budget
        if thinking.get("type") in ("enabled", "adaptive"):
            return str(thinking["type"])
    return ""


def reasoning_effort_of_request(request) -> str:
    """从请求作用域里取原始 body 再解析档位（记账用）。

    `PlatformDispatchMiddleware` 会把预读到的（并已按目标平台改写 model 的）
    body 缓存在 `scope["_wb_body"]` 里，所以记账时不必让每个调用点都把
    body 一路传下来 —— 平台网关的 `_bill()` 只有 request 没有 body。

    解析失败一律返回空串：档位是**观测字段**，绝不能因为它让记账失败。
    """
    try:
        raw = request.scope.get("_wb_body")
        if isinstance(raw, (bytes, bytearray)) and raw:
            return reasoning_effort_of(json.loads(raw))
    except Exception:  # noqa: BLE001
        pass
    return ""


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


# ---------------------------------------------------------------------------
# 账号列表：服务端分页 / 搜索 / 筛选（大号池的渲染与内存优化）
# ---------------------------------------------------------------------------
def list_page_params(request, *, default_page_size: int = 50,
                     max_page_size: int = 200) -> dict:
    """从 query 参数解析列表分页/筛选条件。

    **无 `page` 参数 = 旧的全量行为**（兼容既有调用方与测试）；
    前端传 `page` 后进入服务端分页模式 —— 号池几千几万条时，
    全量 JSON 响应就是「系统越用越卡」的主因。

    返回 dict：
        paginated / page / page_size / search / status / sort /
        min_credits / max_credits
    """
    return _list_page_params(request, default_page_size=default_page_size,
                             max_page_size=max_page_size)


def _list_page_params(request, *, default_page_size: int,
                      max_page_size: int) -> dict:
    out: dict = {"paginated": False, "page": 1, "page_size": default_page_size,
                 "search": "", "status": "", "sort": "id_desc",
                 "min_credits": None, "max_credits": None}
    try:
        qp = request.query_params
    except Exception:  # noqa: BLE001 - 测试桩可能没有 query_params
        return out
    try:
        page = int(str(qp.get("page") or "").strip() or 0)
    except (TypeError, ValueError):
        page = 0
    if page <= 0:
        return out
    out["paginated"] = True
    out["page"] = page
    try:
        out["page_size"] = min(max_page_size,
                               max(1, int(str(qp.get("page_size") or "")
                                          .strip() or default_page_size)))
    except (TypeError, ValueError):
        out["page_size"] = default_page_size
    out["search"] = str(qp.get("search") or qp.get("keyword") or "").strip()
    out["status"] = str(qp.get("status") or "").strip().lower()
    out["sort"] = str(qp.get("sort") or "").strip().lower() or "id_desc"
    for key in ("min_credits", "max_credits"):
        raw = str(qp.get(key) or "").strip()
        if raw:
            try:
                out[key] = float(raw)
            except (TypeError, ValueError):
                pass
    return out


def apply_account_filters(q, model, *, search: str = "", status: str = "",
                          min_credits=None, max_credits=None):
    """账号列表的通用过滤（搜索名称/UID + 状态 + 积分区间）。"""
    from sqlalchemy import func as _func

    if search:
        like = "%" + search + "%"
        q = q.filter(_func.coalesce(model.name, "").like(like)
                     | _func.coalesce(model.uid, "").like(like))
    if status in ("active", "disabled"):
        q = q.filter(model.status == status)
    if min_credits is not None:
        q = q.filter(model.balance_remain >= min_credits)
    if max_credits is not None:
        q = q.filter(model.balance_remain <= max_credits)
    return q


def account_order(q, model, sort: str = "id_desc"):
    """排序白名单（防注入：直接映射列，不拼字符串）。

    ## 为什么要这么多组合（2026-11-09）

    控制台账号池改成「点表头排序」后，前端只能传 `列_方向` 这一种形状。
    以前只有 5 个键（`id_asc` / `credits_desc` / `name_asc` / `synced_desc` …），
    点「名称」升序能排、再点一次想降序就落到白名单外，被**静默忽略**变回
    `id_desc` —— 用户看到的是「点了没反应/顺序乱跳」。

    所以这里按 `列_asc` / `列_desc` 成对补齐。名称用库里的 `name`，
    「状态」用 `status`（active < disabled 的字典序，配合方向够用）。
    """
    order = {
        # 默认 / 入库顺序
        "id_asc": model.id.asc(),
        "id_desc": model.id.desc(),
        # 名称
        "name_asc": model.name.asc(),
        "name_desc": model.name.desc(),
        # 状态（启用/停用）
        "status_asc": model.status.asc(),
        "status_desc": model.status.desc(),
        # 积分（剩余）：`credits` 与 `balance` 都是历史叫法，两个都认
        "credits_desc": model.balance_remain.desc(),
        "credits_asc": model.balance_remain.asc(),
        "balance_desc": model.balance_remain.desc(),
        "balance_asc": model.balance_remain.asc(),
        # 入库时间
        "created_desc": model.created_at.desc(),
        "created_asc": model.created_at.asc(),
        # 最近同步 / 最近使用
        "synced_desc": model.last_sync_at.desc(),
        "synced_asc": model.last_sync_at.asc(),
        "used_desc": model.last_used_at.desc(),
        "used_asc": model.last_used_at.asc(),
    }.get(str(sort or "").strip().lower())
    return q.order_by(order) if order is not None else q.order_by(model.id.desc())


def summarize_accounts_sql(q, model) -> dict:
    """账号列表 summary 的 **SQL 聚合版**。

    与 list_accounts 全量版的 Python 口径一致：**积分类合计只算
    active 账号**，已禁用账号的余额单独给 `disabled_balance_remain`。
    全量模式要 hydrated items 才能算（令牌统计等需要解析 JSON），
    分页模式用这里的 SQL 版 —— 几十万行也不会拖慢翻页。
    """
    from sqlalchemy import func as _func

    total = int(q.with_entities(_func.count(model.id)).scalar() or 0)
    aq = q.filter(model.status == "active")
    active = int(aq.with_entities(_func.count(model.id)).scalar() or 0)

    def _sum(q2, col):
        return int(q2.with_entities(_func.sum(col)).scalar() or 0)

    soonest = aq.filter(model.credits_soonest_expire_at.isnot(None)).with_entities(
        _func.min(model.credits_soonest_expire_at)).scalar()
    return {
        "total": total,
        "active": active,
        "disabled": total - active,
        "available": int(aq.filter(model.balance_remain > 0)
                         .with_entities(_func.count(model.id)).scalar() or 0),
        "balance_total": _sum(aq, model.balance_total),
        "balance_remain": _sum(aq, model.balance_remain),
        "disabled_balance_remain": _sum(
            q.filter(model.status != "active"), model.balance_remain),
        "credits_expiring_soon": _sum(aq, model.credits_expiring_soon),
        "credits_evergreen": _sum(aq, model.credits_evergreen),
        "credits_expired": _sum(aq, model.credits_expired),
        "expiring_accounts": int(aq.filter(model.credits_expiring_soon > 0)
                                 .with_entities(_func.count(model.id)).scalar() or 0),
        "soonest_expire_at": soonest.isoformat() if soonest else None,
    }
