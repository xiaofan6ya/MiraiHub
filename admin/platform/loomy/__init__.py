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
    """
    from admin.platform.loomy.services import checkin

    return {"loomy_checkin": checkin.run_daily_checkin}


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
            extra={
            "task_runners": list(_task_runners().keys()),
            # Loomy 凭证是明文 JSON —— 扫描即导入，用户零操作。
            # checkin：每日签到领 5000 积分（POST /points/first-login，幂等）。
            "actions": ["scan", "import", "refresh-quota", "checkin"],
        },
        ))
        logger.debug("Loomy 平台已登记：%s", platform_id)


install()
