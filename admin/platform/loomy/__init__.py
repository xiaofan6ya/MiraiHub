"""Loomy 平台网关。

## 目录职责

    admin/platform/loomy/
      __init__.py            平台声明 + install()
      gateway.py             /v1/* 转发实现
      platform_gateway.py    Gateway 契约（账号池 / 任务 / 探测 / 卡片）
      core/endpoints.py      端点表
      core/client.py         HTTP 客户端
      services/accounts.py   凭证与账号池
      services/scan.py       本机扫描
      routers/accounts.py    /api/loomy/*

## 接入来源

拆包侦察结论见 `docs/loomy-recon.md`（如有）。
**实现前先跑 skill 里的脚本**：

    python admin/platform/skill/scripts/recon.py "<安装目录>"
    python admin/platform/skill/scripts/probe.py --recon recon-report.json
    python admin/platform/skill/scripts/creds.py --source "<unpacked 目录>"
"""
from __future__ import annotations

import logging

from admin.platform.gateway import AccountOps, ClientProbe, Gateway
from admin.platform.registry import PlatformSpec

logger = logging.getLogger("admin.platform.loomy")

PLATFORM = "loomy"
#: 落库区域（没有多区就留一个空串）
REALMS = ("cn", )
#: 区域 -> 对外平台 id
REALM_IDS = {"cn": "loomy"}


def _task_runners() -> dict:
    """本平台支持的定时任务。

    `loomy_checkin` —— 每日签到，领取当日赠送额度（5000 积分）。
    实测 `POST /points/first-login` 幂等：当天第一次调触发发放，
    之后返回 `alreadyProcessed: true`，所以每天调一次就够。

    `loomy_onboarding` —— 新手任务（一次性，共 10000 积分）。
    服务端 `/onboarding/tasks/complete` 是幂等的（重复调用 alreadyCompleted），
    所以每天跑一次很安全：既能补齐新号，也能兜住上次"跑到一半失败"。
    注意客户端的同款功能默认是 **mock 后端**（只写本地 json），
    界面上显示完成并不等于服务端记账 —— 这也是必须由网关直接调服务端的原因。
    """
    from admin.platform.loomy.services import checkin
    from admin.platform.loomy.services import onboarding

    def _run_onboarding(db, schedule=None):
        """调度层适配：**必须只暴露 `(db, schedule)` 两个位置参数**。

        踩过的坑：直接把 `onboarding.run_all` 注册进来时，它的签名是
        `run_all(db, realm="", *, limit, dry_run)` —— 调度层
        `_adapt_runner()` 看到「有第二个位置参数」就当它是 `schedule`，
        于是把 `Schedule` 对象塞进 `realm`，最后拼出
        `WHERE accounts.domain = <Schedule object>`：
        `SQL expression element or literal value expected, got <admin.models.Schedule …>`。

        这个包装把 realm 收回关键字参数，调度层无论怎么调都安全；
        手动触发走路由（显式传 realm），也不受影响。
        """
        return onboarding.run_all(db)

    return {"loomy_checkin": checkin.run_daily_checkin,
            "loomy_onboarding": _run_onboarding}


def install() -> None:
    """登记 PlatformSpec（幂等）。"""
    from admin.platform import registry as _registry

    for realm in REALMS:
        platform_id = REALM_IDS[realm]
        if _registry.get(platform_id) is not None:
            continue
        _registry.register(PlatformSpec(
            id=platform_id,
            label="Loomy",
            realms=(),
            enabled=True,
            supports_local_scan=True,
            # 转发入口（dispatch 调用）；不需要转发就留空
            gateway="admin.platform.loomy.gateway",
            # 管理契约（账号池 / 任务 / 探测 / 卡片）
            platform_gateway="admin.platform.loomy.platform_gateway:LoomyGateway",
            storage_platform=PLATFORM,
            realm=realm,
            icon="loomy",
            notes="Loomy",
            rating={
                "tier": "hang",
                "reason": "稳定模型速度最快，新用户给的积分很足，每天签到还能领 5000 积分；"
                          "美中不足是积分用得偏快。",
            },
            extra={
            "task_runners": list(_task_runners().keys()),
            # Loomy 凭证是明文 JSON —— 扫描即导入，用户零操作。
            # checkin：每日签到领 5000 积分（POST /points/first-login，幂等）。
            # **export**：导出号池（备份 / 脱敏两档）。缺它时控制台只有
            # 「导入账号」没有「导出」，用户没法把号池搬去另一台机器
            # （issue #25 报的「loomy 只有导入」）。
            "actions": ["scan", "import", "export", "refresh-quota", "checkin",
                        # 新手任务（一次性 10000 积分）：网关直接调服务端，
                        # 不依赖客户端那个默认 mock 的本地记账。
                        "onboarding"],
        },
        ))
        logger.debug("Loomy 平台已登记：%s", platform_id)


install()
