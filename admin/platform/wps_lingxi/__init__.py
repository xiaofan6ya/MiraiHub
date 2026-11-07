"""WPS 灵犀（Lingxi）平台网关。

## 目录职责

    admin/platform/wps_lingxi/
      __init__.py            平台声明 + install()
      gateway.py             /v1/* 转发实现
      platform_gateway.py    Gateway 契约（账号池 / 任务 / 探测 / 卡片）
      core/endpoints.py      端点表、请求头、内置模型快照
      core/client.py         三步通路（建会话 → 领 run → 对话 SSE）
      core/protocol.py       Cookie 归一 / usage / SSE 翻译 / 提示词裁剪
      services/accounts.py   凭证与账号池（含本机客户端扫描）
      services/checkin.py    签到
      routers/accounts.py    /api/wps-lingxi/*

## 上游（取自 `@kevinfx/magpie-wps-lingxi@0.3.3` 源码）

    账号面  https://lingxi.wps.cn   （kdocs 账号回落 https://lingxi.kdocs.cn）
    对话    POST /api/aioffice/v1/cowork/sessions      → session_id
            POST /api/public/v1/credits/runs           → run_id
            POST /api/public/v1/agent-runtime/chat/completions（自定义 SSE 帧）
    鉴权    Cookie: wps_sid=<sid>[; uid=…][; copilot_server_branch=…]
            + x-kso-app-name / x-kso-app-version / x-kso-device-id（桌面客户端伪装）

## 凭证来源

1. 粘贴 `wps_sid` 或整段 Cookie（最稳，跨平台）；
2. 导入本机 WPS 灵犀客户端的登录态（Windows DPAPI + AES-GCM，macOS 明文）；
3. 批量导入 JSON。
"""
from __future__ import annotations

import logging

from admin.platform.registry import PlatformSpec

logger = logging.getLogger("admin.platform.wps_lingxi")

PLATFORM = "wps-lingxi"
#: 落库区域（单一区域，没有多区）
REALMS = ("cn", )
#: 区域 -> 对外平台 id
#:
#: 落库标识与对外 id 相同，理由见 `core/endpoints.PLATFORM` 的注释：
#: 控制台按 `Account.platform` 拼 `/api/<platform>/...`，两者必须一致。
REALM_IDS = {"cn": "wps-lingxi"}


def _task_runners() -> dict:
    from admin.platform.wps_lingxi.platform_gateway import WpsLingxiAccountOps

    ops = WpsLingxiAccountOps("")
    return {"wps_lingxi_keepalive": ops.keepalive,
            "wps_lingxi_checkin": ops.checkin,
            "wps_lingxi_activity": ops.activity,
            "wps_lingxi_ban_recheck": ops.ban_recheck}


def install() -> None:
    """登记 PlatformSpec（幂等）。"""
    from admin.platform import registry as _registry

    for realm in REALMS:
        platform_id = REALM_IDS[realm]
        if _registry.get(platform_id) is not None:
            continue
        _registry.register(PlatformSpec(
            id=platform_id,
            label="WPS 灵犀",
            realms=(),
            enabled=True,
            supports_local_scan=True,
            gateway="admin.platform.wps_lingxi.gateway",
            platform_gateway="admin.platform.wps_lingxi.platform_gateway:WpsLingxiGateway",
            storage_platform=PLATFORM,
            realm=realm,
            icon="wps",
            notes="WPS 灵犀（Lingxi / 灵犀 Claw）；Cookie 登录 + 桌面 agent-runtime 通路",
            extra={
                "task_runners": list(_task_runners().keys()),
                "actions": ["begin-auth", "scan", "import", "export", "checkin",
                            "refresh-quota", "usage", "activity",
                            "ban-recheck"],
            },
        ))
        logger.debug("WPS 灵犀平台已登记：%s", platform_id)


install()