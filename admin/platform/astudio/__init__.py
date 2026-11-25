"""AStudio（讯飞 Astron Studio）平台网关。

## 目录职责

    admin/platform/astudio/
      __init__.py            平台声明 + install()
      gateway.py             /v1/* 转发实现（OpenAI Responses 上游）
      platform_gateway.py    Gateway 契约（账号池 / 任务 / 探测 / 卡片）
      core/endpoints.py      端点表 / 模型目录表 / 北京时间窗口
      core/client.py         上游 HTTP（模型网关 + 账号面 Cookie 会话）
      core/paths.py          本机登录态扫描（astron-session.json / config.toml）
      services/accounts.py   账号池 / 导入 / 额度同步
      services/checkin.py    每日登录积分签到（流水实证）
      routers/accounts.py    /api/astudio/*

## 上游摘要（2026-11-13 逆向桌面端 + 网页后台实测）

    模型调用 POST {gw}/model-gateway/v1/responses
              **OpenAI Responses 协议**（与桌面端内核 wire_api 一致），
              Bearer modelBearerToken（JWT，约 3 年，换发后旧 token 仍可用）
    模型目录 GET {gw}/api/v1/model-manager/models
              不计费，按账号实时拉；目录滞后于桌面端时用内置补位表
    账号面   https://agent.xfyun.cn/xingchen-studio/…
              Cookie 会话（ssoSessionId + account_id 必需）+ 必需身份头
              clientType / studioVersion；信封 {flag, code, desc, data}
    签到     只读当日 CREDIT 流水：出现 GRANT「每日登录赠送」即已发放
              （零写）；否则 complete 待领取弹窗里的 DAILY_REWARD_DIALOG
              那一条，再以流水认定（短延迟复读一次，仍没有记 failed）

## 凭证从哪来

AStudio 桌面端把登录态写成明文 JSON：
`<DataRoot>/userdata/astron-session.json`（modelBearerToken + 网页会话
token/ssoSessionId/accountId/uid），其次 `<DataRoot>/runtime/acode-home/
config.toml`（只有模型凭据，无网页会话 → 积分/签到不可用）。DataRoot
按 ASTUDIO_HOME/SYNARA_HOME → Windows 注册表 → `~/.acode/acode/` 顺序找。
"""
from __future__ import annotations

import logging

from admin.platform.registry import PlatformSpec

logger = logging.getLogger("admin.platform.astudio")

PLATFORM = "astudio"
REALMS = ("cn",)
REALM_IDS = {"cn": "astudio"}


def _task_runners() -> dict:
    from admin.platform.astudio import platform_gateway as _pg

    return {"astudio_checkin": _pg._run_checkin,
            "astudio_refresh_quota": _pg._run_refresh_quota}


def install() -> None:
    """登记 PlatformSpec（幂等）。"""
    from admin.platform import registry as _registry

    for realm in REALMS:
        platform_id = REALM_IDS[realm]
        if _registry.get(platform_id) is not None:
            continue
        _registry.register(PlatformSpec(
            id=platform_id,
            label="AStudio",
            realms=(),
            enabled=True,
            # 客户端登录态是明文 astron-session.json（本机扫描），
            # 也可直接粘贴 modelBearerToken；无设备授权接口。
            supports_local_scan=True,
            auth_mode="local-scan",
            gateway="admin.platform.astudio.gateway",
            platform_gateway="admin.platform.astudio.platform_gateway:AStudioGateway",
            storage_platform=PLATFORM,
            realm=realm,
            icon="astudio",
            notes="AStudio（讯飞 Astron Studio）：OpenAI Responses 协议上游，"
                  "Bearer JWT（约 3 年）；每日登录积分签到（流水实证）；"
                  "支持本机扫描 + 导入",
            rating={
                "tier": "npc",
                "reason": "模型以编码为主，含限免档；积分制且有每日签到，"
                          "聊胜于无。",
            },
            extra={
                "task_runners": list(_task_runners().keys()),
                # begin-auth = 授权登录引导：打开官网 → 桌面端登录 → 自动导入
                "actions": ["begin-auth", "scan", "import", "export",
                            "refresh-quota", "checkin"],
            },
        ))
        logger.debug("AStudio 平台已登记：%s", platform_id)


install()
