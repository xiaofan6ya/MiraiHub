"""科搭 Codar 平台网关（api.claw.speedai.com / api.kuaipaper.com）。

## 目录职责

    admin/platform/codar/
      __init__.py            平台声明 + install()
      gateway.py             /v1/* 转发（两套上游协议按模型选路）
      platform_gateway.py    Gateway 契约（账号池 / 任务 / 探测 / 卡片）
      core/endpoints.py      端点表、两套协议的请求头、内置模型目录
      core/client.py         上游 HTTP + 错误分类 + 目录缓存 + 并发闸门
      core/paths.py          本机登录态（~/.codar/config.json，只读）
      services/accounts.py   账号池 / 点数同步 / 令牌续期
      services/checkin.py    签到（滚动 24 小时）+ 免费模型活动
      routers/accounts.py    /api/codar/*

## 上游摘要

    对话     POST {gateway}/v1/chat/completions   OpenAI 协议，Bearer
             POST {gateway}/v1/messages           Anthropic 协议，
                                                  X-Api-Key + Anthropic-Version
    目录     GET  {gateway}/v1/codar/models       Bearer，按账号实时拉（60s 缓存）
    点数     POST {account}/v1/gettoken           主读数（通用 + agent）
             POST {account}/v1/agent/balance      副读数（agent）
    签到     POST {account}/v1/daily-sign         滚动 24 小时
             POST {account}/v1/daily-sign/status  签到态
    续期     POST {account}/v1/token/refresh      同一枚令牌换新
    活动     GET  {gateway}/api/codar/activities/{mine,free-model}
             POST {gateway}/api/codar/activities/free-model/claim

## 凭证从哪来

客户端把 JWT 写在 `~/.codar/config.json` 的 `provider.speedai.token`
（`CODAR_HOME` 可覆盖，文件按 `utf-8-sig` 读）。两种入库：扫描本机、粘贴
令牌。令牌导入前校验「有账号名 + 未过期」，过期就明确要求先刷新登录态。

## 三条容易被写错的语义

1. **两套协议**：Claude 系必须走 `/v1/messages` 且用 `X-Api-Key`；按目录
   的 `protocol` 字段选路，目录没拉过才按模型名兜底。
2. **每账号 3 个在途**：超出在上游是 429 `concurrent_limit_exceeded`，
   所以模块内做闸门并排队，而不是把「忙」报成「没有可用账号」。
3. **错误不能都叫「登录失效」**：`account_restricted`（厂商限制账号）与
   `client_not_supported`（要求第三方改用 API key）重登都无效，
   `429` 只是要等 —— 三种文案各自不同。
"""
from __future__ import annotations

import logging

from admin.platform.registry import PlatformSpec

logger = logging.getLogger("admin.platform.codar")

PLATFORM = "codar"


def _task_runners() -> dict:
    """本平台定时任务：签到 / 点数刷新 / 令牌续期。"""
    from admin.platform.codar import platform_gateway as _pg

    return {"codar_checkin": _pg._run_checkin,
            "codar_refresh_quota": _pg._run_refresh_quota,
            "codar_keepalive": _pg._run_keepalive}


def install() -> None:
    """登记 PlatformSpec（幂等；已在注册表里就跳过）。"""
    from admin.platform import registry as _registry

    if _registry.get(PLATFORM) is not None:
        return
    _registry.register(PlatformSpec(
        id=PLATFORM,
        label="科搭 Codar",
        realms=(),
        enabled=True,
        # 客户端登录态是明文 config.json（~/.codar），支持扫描本机
        supports_local_scan=True,
        # 没有密码/验证码/扫码那套登录接口：凭证只能来自本机或粘贴
        auth_mode="local-scan",
        gateway="admin.platform.codar.gateway",
        platform_gateway="admin.platform.codar.platform_gateway:CodarGateway",
        storage_platform=PLATFORM,
        realm="cn",
        icon="codar",
        notes="科搭 Codar（api.claw.speedai.com）：OpenAI + Anthropic 双协议上游，"
              "Bearer JWT；点数制，签到按滚动 24 小时；"
              "支持本机扫描与粘贴导入（令牌过期需先在客户端刷新）",
        rating={
            "tier": "npc",
            "reason": "模型挺全（含 Claude 系），但每账号只允许 3 个在途请求，"
                      "高并发时要排队。",
        },
        extra={
            "task_runners": list(_task_runners().keys()),
            "actions": ["scan", "import", "export", "checkin",
                        "refresh-quota"],
            "protocols": ["openai_chat_completions", "anthropic_messages"],
            "max_concurrency": 3,
        },
    ))
    logger.debug("科搭 Codar 平台已登记：%s", PLATFORM)


install()
