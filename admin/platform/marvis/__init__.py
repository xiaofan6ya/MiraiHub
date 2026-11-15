"""腾讯 Marvis 平台网关。

## 目录职责

    admin/platform/marvis/
      __init__.py            平台声明 + install()
      gateway.py             /v1/* 转发实现
      platform_gateway.py    Gateway 契约（账号池 / 任务 / 探测 / 卡片）
      core/endpoints.py      端点表
      core/client.py         HTTP 客户端
      services/accounts.py   凭证与账号池
      services/scan.py       本机扫描
      routers/accounts.py    /api/marvis/*

## 接入来源

拆包侦察结论见 `docs/marvis-recon.md`（如有）。
**实现前先跑 skill 里的脚本**：

    python admin/platform/skill/scripts/recon.py "<安装目录>"
    python admin/platform/skill/scripts/probe.py --recon recon-report.json
    python admin/platform/skill/scripts/creds.py --source "<unpacked 目录>"
"""
from __future__ import annotations

import logging

from admin.platform.gateway import AccountOps, ClientProbe, Gateway
from admin.platform.registry import PlatformSpec

logger = logging.getLogger("admin.platform.marvis")

PLATFORM = "marvis"
#: 落库区域（没有多区就留一个空串）
REALMS = ("cn", )
#: 区域 -> 对外平台 id
REALM_IDS = {"cn": "marvis"}


def _task_runners() -> dict:
    """本平台支持的定时任务：额度同步（令牌钱包每天重置）。"""
    from admin.platform.marvis import platform_gateway as _pg

    return {"marvis_refresh_quota": _pg._run_refresh_quota}


def install() -> None:
    """登记 PlatformSpec（幂等）。"""
    from admin.platform import registry as _registry

    for realm in REALMS:
        platform_id = REALM_IDS[realm]
        if _registry.get(platform_id) is not None:
            continue
        _registry.register(PlatformSpec(
            id=platform_id,
            label="腾讯 Marvis",
            realms=(),
            enabled=True,
            # Marvis 没有 PC 客户端可供扫描（它是应用宝里的 AI 助手），
            # 凭证只能靠「扫码登录」或「粘贴导入」。
            supports_local_scan=False,
            # 授权登录走 QQ / 微信扫码，不是设备码也不是密码。
            auth_mode="qr-auth",
            # 转发入口（dispatch 调用）
            gateway="admin.platform.marvis.gateway",
            # 管理契约（账号池 / 任务 / 探测 / 卡片）
            platform_gateway="admin.platform.marvis.platform_gateway:MarvisGateway",
            storage_platform=PLATFORM,
            realm=realm,
            icon="marvis",
            notes="腾讯 Marvis（应用宝 AI 助手）：OpenAI 协议上游，"
                  "Ual-Access 头鉴权；令牌钱包制（每号每日 1000 万计费 token，"
                  "缓存免费、输入输出五折）；支持扫码登录 + 凭证导入",
            extra={
                "task_runners": list(_task_runners().keys()),
                "actions": ["import", "export", "begin-auth", "refresh-quota"],
            },
        ))
        logger.debug("腾讯 Marvis 平台已登记：%s", platform_id)


install()
