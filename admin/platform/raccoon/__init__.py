"""小浣熊（Raccoon）平台网关。

## 目录职责

    admin/platform/raccoon/
      __init__.py            平台声明 + install()
      gateway.py             /v1/* 转发实现
      platform_gateway.py    Gateway 契约（账号池 / 任务 / 探测 / 卡片）
      core/endpoints.py      端点表
      core/client.py         HTTP 客户端 + 信封拆解 + 错误分类
      services/accounts.py   凭证归一 / 账号池 / 额度
      routers/accounts.py    /api/raccoon/*

## 上游摘要

    站点     https://xiaohuanxiong.com
    LLM      POST /api/web/llm/v2/chat/completions   Bearer JWT（HS256）
    目录     GET  /api/web/llm/v2/model_catalog      → 9 个模型
    积分     GET  /api/web/points/v1/balance         → available/daily/reward
    权益     GET  /api/web/auth/v1/entitlement_info
    续期     POST /api/web/auth/v1/refresh

## 凭证从哪来

小浣熊客户端用 Electron LevelDB 存登录态，不是明文 JSON，且不是每台机器都装。
所以本平台**没有本机扫描**（`supports_local_scan=False`），凭证靠导入：
agent2api 导出的 `agent2api-account` JSON / 裸 JWT / 原生对象都接受。

该「明确不支持」要好过「假装能扫然后返回空」——后者会让用户以为客户端没登录。
"""
from __future__ import annotations

import logging

from admin.platform.gateway import AccountOps, ClientProbe, Gateway
from admin.platform.registry import PlatformSpec

logger = logging.getLogger("admin.platform.raccoon")

PLATFORM = "raccoon"
#: 落库区域（小浣熊只有国内站）
REALMS = ("cn",)
#: 区域 -> 对外平台 id
REALM_IDS = {"cn": "raccoon"}


def _task_runners() -> dict:
    """本平台支持的定时任务。

    `raccoon_keepalive` —— 续期临期 JWT + 探活。

    注意：小浣熊的 refreshToken 与 accessToken 实测同值（50/50），
    所以续期是「同票换新票」，能推迟 exp，救不回被吊销的会话。

    `raccoon_expiry_sweep` 只在本模块注册（`platform_gateway.task_runners`
    里追加），**不进** `extra["task_runners"]` —— 那个字段是给控制台
    「新建任务」下拉框用的，列太多反而干扰。两份清单的差异在
    `Gateway.task_runners()` 里显式说明，避免漂移。
    """
    from admin.platform.raccoon.services import tasks

    return {"raccoon_keepalive": tasks.run_keepalive}


def install() -> None:
    """登记 PlatformSpec（幂等）。"""
    from admin.platform import registry as _registry

    for realm in REALMS:
        platform_id = REALM_IDS[realm]
        if _registry.get(platform_id) is not None:
            continue
        _registry.register(PlatformSpec(
            id=platform_id,
            label="小浣熊",
            realms=(),
            enabled=True,
            # 凭证来自导入（Electron LevelDB，非明文），不做本机扫描
            supports_local_scan=False,
            # **关键**：既无本机明文凭证、也无授权登录接口。
            # 不声明这一项，控制台会按「不支持扫描 ⇒ 一定有设备授权」
            # 强制注入 begin-auth（→ /api/raccoon/device/start 404），
            # 同时把 import 剔除，结果这个平台的账号**永远加不进来**。
            auth_mode="import-only",
            gateway="admin.platform.raccoon.gateway",
            platform_gateway="admin.platform.raccoon.platform_gateway:RaccoonGateway",
            storage_platform=PLATFORM,
            realm=realm,
            icon="raccoon",
            notes="小浣熊（xiaohuanxiong.com）：Bearer JWT，积分制；凭证靠导入，无本机扫描",
            extra={
                "task_runners": list(_task_runners().keys()),
                # 只声明契约词表内的动作（scan / import / refresh-quota /
                # usage / web-cookie / checkin / begin-auth）。
                # `export` 不在词表里 —— 声明了前端会渲染出一个点了没反应的
                # 英文按钮，导出能力改由 /api/raccoon/export 直接调用。
                "actions": ["import", "refresh-quota"],
            },
        ))
        logger.debug("小浣熊平台已登记：%s", platform_id)


install()
