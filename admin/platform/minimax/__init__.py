"""MiniMax Code 平台网关。

## 目录职责

    admin/platform/minimax/
      __init__.py            平台声明 + install()
      gateway.py             /v1/* 转发实现（Anthropic 上游）
      platform_gateway.py    Gateway 契约（账号池 / 任务 / 探测 / 卡片）
      core/endpoints.py      端点表（cn/en 双区主机）
      core/client.py         上游 HTTP（对话/身份/签到/积分/OAuth 设备流）
      core/paths.py          本机凭证扫描（~/.minimax）
      services/accounts.py   账号池 / 凭证续期 / 积分同步
      routers/accounts.py    /api/minimax/*

## 上游摘要（2026-11-07 逆向官方客户端 3.1.6 + 本机凭证实测）

    对话     POST {agent}/mavis/api/v1/llm/v1/messages
             **Anthropic Messages 协议**（本网关第一个非 OpenAI 上游），
             Bearer mmoat_ token；SSE 标准事件含 thinking / tool_use
    模型     MiniMax-M2.7 / M2.7-highspeed / M3 / M3.1-Flash（静态目录，
             受管端点无模型列表接口，实测 GET /llm/v1/models 503）
    签到     GET/POST {agent}/minimax-cloud/api/v1/signin/{status,claim}
             ?timezone_id=Asia/Shanghai；连续 7 天 400/1000/400...积分
    积分     GET  {agent}/minimax-cloud/api/v1/credit/details
             可用积分 = Σ remaining_amount（签到积分 30 天有效）
    设备授权 POST {account}/oauth2/device/code（PKCE S256，
             client_id=mcode-public）→ verification_uri_complete 扫码
    续签     POST {account}/oauth2/token grant_type=refresh_token

## 凭证从哪来

MiniMax Code 客户端（Electron）把 OAuth 票写成**明文** JSON：
`~/.minimax/auth/prod/<env>/<region>/mcode-public/auth.json`
（accessToken mmoat_ / refreshToken mmort_ / expiresAtMs）。三种入库：

  1. 扫描本机 → 读上面的 auth.json（core/paths.py）
  2. 导入文件 → JSONL / JSON / 单对象（refresh token 或完整凭证）
  3. 授权登录 → OAuth 设备流扫码（官方同款 PKCE 参数）
"""
from __future__ import annotations

import logging

from admin.platform.registry import PlatformSpec

logger = logging.getLogger("admin.platform.minimax")

PLATFORM = "minimax"
REALMS = ("cn",)
REALM_IDS = {"cn": "minimax"}


def _task_runners() -> dict:
    """本平台定时任务：每日签到 / 续签保活 / 积分刷新。"""
    from admin.platform.minimax import platform_gateway as _pg

    return {"minimax_checkin": _pg._run_checkin,
            "minimax_keepalive": _pg._run_keepalive,
            "minimax_refresh_quota": _pg._run_refresh_quota}


def install() -> None:
    """登记 PlatformSpec（幂等）。"""
    from admin.platform import registry as _registry

    for realm in REALMS:
        platform_id = REALM_IDS[realm]
        if _registry.get(platform_id) is not None:
            continue
        _registry.register(PlatformSpec(
            id=platform_id,
            label="MiniMax Code",
            realms=(),
            enabled=True,
            # 客户端登录态是明文 auth.json（~/.minimax/auth/prod/...），
            # 支持本机扫描；授权登录走官方 OAuth 设备流扫码。
            supports_local_scan=True,
            auth_mode="device-auth",
            gateway="admin.platform.minimax.gateway",
            platform_gateway="admin.platform.minimax.platform_gateway:MinimaxGateway",
            storage_platform=PLATFORM,
            realm=realm,
            icon="minimax",
            notes="MiniMax Code（agent.minimax.cn）：Anthropic 协议上游，"
                  "Bearer OAuth；积分制（签到 30 天有效）；"
                  "支持本机扫描 + 设备流扫码授权 + 导入",
            rating={
                "tier": "elite",
                "reason": "只有自家模型，额度消耗不快，速度还行。",
            },
            extra={
                "task_runners": list(_task_runners().keys()),
                "actions": ["scan", "import", "export",
                            "begin-auth", "refresh-quota", "checkin"],
            },
        ))
        logger.debug("MiniMax Code 平台已登记：%s", platform_id)


install()
