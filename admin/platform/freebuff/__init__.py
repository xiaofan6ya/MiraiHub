"""Freebuff 平台网关。

## 目录职责

    admin/platform/freebuff/
      __init__.py            平台声明 + install()
      gateway.py             /v1/* 转发（OpenAI 兼容 + 非流式模型本地合成 SSE）
      platform_gateway.py    Gateway 契约（账号池 / 任务 / 探测 / 卡片）
      core/endpoints.py      端点表、UA、内置 17 个模型快照、会话 token 识别
      core/client.py         HTTP（目录免鉴权、对话需 Developer API key）
      core/protocol.py       目录合并 / usage 归一 / SSE 合成 / 三协议翻译
      services/accounts.py   凭证与账号池
      routers/accounts.py    /api/freebuff/*

## 上游（实测 2026-11-07）

    Base     https://freebuff.com/api/v1/paid
    目录     GET  /models             200，**免鉴权**，280 个模型
    对话     POST /chat/completions   Authorization: Bearer <Developer API key>

`/usage`、`/credits`、`/me`、`/balance`、`/health`、`/billing` 全部 404 ——
上游没有余额接口，也没有校验 key 的轻量端点。

## 凭证

只有 Developer API key（`fb_...`，从 https://freebuff.com/account/api 创建）。
CLI/Desktop 的会话 token（UUID 形状）在本地就被拒：Freebuff 把免费额度绑定在
它自己的校验客户端上，第三方程序用会话 token 只会拿到
`410 freebuff_update_required`。

参考实现：[`chengyixu/magpie-freebuff`](https://github.com/chengyixu/magpie-freebuff)
（Magpie / OpenCode 插件）。同样是按协议重写成 Python 后接进 MiraiHub。
"""
from __future__ import annotations

import logging

from admin.platform.registry import PlatformSpec

logger = logging.getLogger("admin.platform.freebuff")

PLATFORM = "freebuff"
REALMS = ("cn", )
REALM_IDS = {"cn": "freebuff"}


def _task_runners() -> dict:
    from admin.platform.freebuff.platform_gateway import FreebuffAccountOps

    ops = FreebuffAccountOps("cn")
    return {"freebuff_keepalive": ops.keepalive,
            "freebuff_activity": ops.activity,
            "freebuff_ban_recheck": ops.ban_recheck}


def install() -> None:
    """登记 PlatformSpec（幂等）。"""
    from admin.platform import registry as _registry

    for realm in REALMS:
        platform_id = REALM_IDS[realm]
        if _registry.get(platform_id) is not None:
            continue
        _registry.register(PlatformSpec(
            id=platform_id,
            label="Freebuff",
            realms=(),
            enabled=True,
            supports_local_scan=False,
            gateway="admin.platform.freebuff.gateway",
            platform_gateway="admin.platform.freebuff.platform_gateway:FreebuffGateway",
            storage_platform=PLATFORM,
            realm=realm,
            icon="freebuff",
            notes="Freebuff Developer API（OpenAI 兼容；无余额接口、无本机客户端）",
            extra={
                "task_runners": list(_task_runners().keys()),
                "actions": ["begin-auth", "import", "export", "refresh-quota",
                            "usage", "activity", "ban-recheck"],
            },
        ))
        logger.debug("Freebuff 平台已登记：%s", platform_id)


install()