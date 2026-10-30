"""WorkBuddy / CodeBuddy 平台网关。

## 这个模块负责什么

WorkBuddy 的全部平台知识：

  * 凭证（`auth` / `account` 字段、加密编解码、Turing 令牌）
  * 上游转发（腾讯 craft / chat 端点）
  * 账号池 11 项操作（手机号登录、导入、扫描、刷新、保活、
    签到、积分、用量、健康检查、注入客户端、导出）
  * 定时任务（签到、成长计划、余额刷新、封号复核 …）
  * 客户端探测（`.info` 目录、`client_profile`）

## admin 不再知道什么

改造前这些知识散在 `admin/backend.py`、`admin/wb_login.py`、
`admin/routers/proxy.py`、`admin/scheduler.py` 等处，admin 被迫理解
WorkBuddy 的内部细节。现在收进本目录，admin 只通过
`admin.platform.gateway.Gateway` 契约调用。

## 目录职责

    admin/platform/workbuddy/
      __init__.py        平台声明 + Gateway + AccountOps + ClientProbe
      core/              纯实现层（凭证、登录、转发、指纹），无平台无关逻辑

## 迁移状态

`core/` 里的实现已从 `admin/` 搬入（`admin/backend.py` 等旧路径保留为
模块别名，见 `admin/__init__.py`）。转发入口仍在 `admin/routers/proxy.py`，
由本模块的 `Gateway` 委托调用 —— 后续可继续下沉。
"""
from __future__ import annotations

import logging

from admin.platform.gateway import AccountOps, ClientProbe, Gateway
from admin.platform.registry import PlatformSpec

logger = logging.getLogger("admin.platform.workbuddy")

#: 落库平台名。WorkBuddy 没有「区域」细分（那套是 Trae 的概念），
#: 所以 `realm` 留空，`storage_platform` 与对外 id 相同。
PLATFORM = "workbuddy"
LABEL = "WorkBuddy"


def _task_runners() -> dict:
    """本平台支持的定时任务。

    **任务名到函数的映射由 WorkBuddy 自己声明**，admin 的调度器
    只保留分派逻辑（`run_task` 查表）。
    """
    from admin import scheduler

    names = (
        "refresh_balances",
        "refresh_credits",
        "sync_models",
        "daily_checkin",
        "refresh_growth_tasks",
        "run_growth_tasks",
        "run_growth_plan",
        "keepalive_tokens",
        "ban_recheck",
    )
    table = getattr(scheduler, "TASK_RUNNERS", {})
    return {name: table[name] for name in names if table.get(name)}


class WorkBuddyAccountOps:
    """WorkBuddy 账号池的 11 项操作。

    迁移期实现委托给 `admin/routers/accounts.py` 与 `core/wb_login.py`。
    契约先立起来（admin 可以按契约调用），实现再逐步搬进本目录。
    """

    def list(self, db):
        from admin.routers import accounts as accounts_router

        return accounts_router.list_accounts(request=None, _=True, db=db)

    def scan(self):
        from admin.routers import accounts as accounts_router

        return accounts_router.scan_local(_=True)

    def import_local(self, db):
        from admin.routers import accounts as accounts_router

        return accounts_router.import_local(_=True, db=db)

    def import_record(self, db, record):
        from admin.platform.workbuddy.core import wb_login

        return wb_login.import_record(db, record)

    def refresh(self, db, account_id, *, force=False):
        from admin.routers import accounts as accounts_router

        return accounts_router.refresh_account(account_id, _=True, db=db)

    def keepalive(self, db):
        from admin import scheduler

        return scheduler.run_keepalive_tokens(db)

    def checkin(self, db):
        from admin import scheduler

        return scheduler.run_daily_checkin(db, None)

    def credits(self, db):
        from admin.routers import credits as credits_router

        return credits_router.expiry(_=True, db=db)

    def usage(self, db, **filters):
        from admin.routers import stats as stats_router

        return stats_router.usage(**filters, _=True, db=db)

    def health(self, db, account_id):
        from admin.routers import accounts as accounts_router

        return accounts_router.health_check(account_id, _=True, db=db)

    def inject(self, db, account_id):
        from admin import client_profile

        return client_profile.inject(db, account_id)

    def export(self, db, ids=None):
        from admin.routers import accounts as accounts_router

        body = accounts_router.ExportIn(ids=ids or [], include_disabled=True,
                                        format="backup")
        return accounts_router.export_accounts(body=body, response=None,
                                               request=None, _=True, db=db)


class WorkBuddyClientProbe:
    """WorkBuddy 客户端探测（`.info` 目录 + client_profile）。"""

    def dirs(self) -> list[str]:
        from admin.platform.workbuddy.core import wb_paths

        return [p for p in wb_paths.client_dirs() if p]

    def probe(self) -> dict:
        from admin.platform.workbuddy.core import wb_paths

        return wb_paths.probe()

    def schema(self) -> list[dict]:
        from admin import client_profile

        return client_profile.schema()

    def read(self, key: str):
        from admin import client_profile

        return client_profile.read(key)

    def write(self, key: str, value) -> dict:
        from admin import client_profile

        return client_profile.write(key, value)


class WorkBuddyGateway:
    """网关主体：转发 + 目录 + 账号池 + 任务 + 客户端探测 + 概览卡片。"""

    platform_id = PLATFORM

    # ---- 转发（迁移期仍指向 admin/routers/proxy.py）----
    def chat_completions(self, request, body):
        from admin.routers import proxy

        return proxy.chat_completions(request, body)

    def responses(self, request, body):
        from admin.routers import proxy

        return proxy.responses(request, body)

    def messages(self, request, body):
        from admin.routers import proxy

        return proxy.messages(request, body)

    def count_tokens(self, request, body):
        from admin.routers import proxy

        return proxy.count_tokens(request, body)

    # ---- 目录 ----
    def list_models(self, request):
        from admin.routers import models as models_router

        return models_router.list_models(request, _=True)

    def balance(self, request):
        from admin.routers import proxy

        return proxy.balance(request)

    # ---- 账号池 ----
    def account_ops(self) -> AccountOps:
        return WorkBuddyAccountOps()

    # ---- 定时任务 ----
    def task_runners(self) -> dict:
        return _task_runners()

    # ---- 客户端探测 ----
    def client_probe(self) -> ClientProbe:
        return WorkBuddyClientProbe()

    # ---- 概览卡片 ----
    def overview_cards(self, db) -> list[dict]:
        """WorkBuddy 的统计卡片。

        四张卡与改造前前端写死的内容一致（账号数 / 积分 / 7 天内到期 /
        接入方式），只是现在由平台自己声明 —— 前端不再需要
        `if isWorkbuddy { ... }` 分支。

        **不走 `list_accounts(request=None)`**：那个函数靠请求头判断平台，
        传 None 会退化成「全部平台」（实测把 Trae 的 3 个号也算进来，
        显示 5 而不是 2）。这里直接按落库平台过滤。
        """
        from admin.models import Account

        try:
            rows = (db.query(Account)
                    .filter(Account.platform == PLATFORM)
                    .all())
        except Exception:  # noqa: BLE001 - 统计失败不该让概览打不开
            rows = []

        total = len(rows)
        active = sum(1 for r in rows if (getattr(r, "status", "") or "") == "active")
        disabled = total - active
        remain = sum(int(getattr(r, "balance_remain", 0) or 0) for r in rows)
        limit = sum(int(getattr(r, "balance_total", 0) or 0) for r in rows)
        expiring = sum(int(getattr(r, "credits_expiring_soon", 0) or 0) for r in rows)
        expiring_accounts = sum(
            1 for r in rows if (getattr(r, "credits_expiring_soon", 0) or 0) > 0)

        def _credit(value) -> str:
            try:
                return "{:,.2f}".format(float(value or 0))
            except (TypeError, ValueError):
                return "0.00"

        return [
            {"key": "accounts", "label": "账号总数", "value": total,
             "sub": "启用 %d · 已禁用 %d" % (active, disabled),
             "icon": "fa-users", "tone": ""},
            {"key": "credits", "label": "可用积分", "value": _credit(remain),
             "sub": "总额度 %s" % _credit(limit),
             "icon": "fa-wallet", "tone": "brand"},
            {"key": "expiring", "label": "7 天内到期", "value": _credit(expiring),
             "sub": "%d 个账号" % expiring_accounts,
             "icon": "fa-fire", "tone": "warn" if expiring > 0 else "brand"},
            {"key": "mode", "label": "接入方式", "value": "内置默认",
             "sub": "手机号登录 + 本机扫描", "icon": "fa-cube", "tone": ""},
        ]


def install() -> None:
    """注册平台声明（幂等）。

    **幂等**：模块 import 时自动执行一次，loader 再次调用时必须安静返回 ——
    否则装配阶段会报「平台已登记」并把它当成失败。
    """
    from admin.platform import registry as _registry

    if _registry.get(PLATFORM) is not None:
        return
    _registry.register(PlatformSpec(
        id=PLATFORM,
        label=LABEL,
        realms=(),
        # 转发入口：WorkBuddy 是默认平台，/v1 由 admin 的 proxy 直接处理，
        # 所以 `gateway` 留空（dispatch 看到默认平台会原样放行）。
        gateway="",
        # 管理契约：账号池 11 项操作 + 任务清单 + 客户端探测 + 概览卡片。
        platform_gateway="admin.platform.workbuddy:WorkBuddyGateway",
        storage_platform=PLATFORM,
        realm="",
        icon=PLATFORM,
        supports_local_scan=True,
        notes="腾讯 CodeBuddy / WorkBuddy，内置默认平台；/v1 由本网关直接处理。",
        extra={
            "task_runners": list(_task_runners().keys()),
            # 动作能力（前端据此渲染按钮）
            # checkin 不在这里：WorkBuddy 的签到走定时任务 daily_checkin，
            # 没有手动接口。声明了但没实现 -> 点了 404。
            "actions": ["scan", "import", "refresh-quota", "usage"],
        },
    ))
    logger.debug("WorkBuddy 平台已登记")


install()