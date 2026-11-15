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

小浣熊客户端（Electron）把登录态写成**明文** `auth.json`
（`~/.office-raccoon/agent/config/auth.json`，IAM JWT + office_identity），
所以本平台 `supports_local_scan=True`，三种入库方式都支持：

  1. 扫描本机 → 读取上面那个 auth.json（`services/scan.py`）
  2. 导入文件 → 本地 JSON / JSONL / TXT（agent2api 备份、裸 JWT 都吃）
  3. 授权登录 → 官方 IAM 小程序扫码，免客户端

历史备注：PR #9 曾判定「凭证在 Electron LevelDB 里，扫不出来」，
后续逆向 `boxAgentAuthFile.js` 确认那是旧版结论，现行版本就是明文 JSON。
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
            # 客户端登录态是明文 auth.json（逆向 boxAgentAuthFile.js 实测，
            # ~/.office-raccoon/agent/config/auth.json）—— 支持本机扫描。
            # PR #9 时代的 "Electron LevelDB 扫不出来" 结论不成立。
            supports_local_scan=True,
            # 授权登录 = 官方 IAM 小程序扫码（免客户端/免滑块/免短信），
            # 由 extra.actions 里的 begin-auth 声明按钮。
            auth_mode="device-auth",
            gateway="admin.platform.raccoon.gateway",
            platform_gateway="admin.platform.raccoon.platform_gateway:RaccoonGateway",
            storage_platform=PLATFORM,
            realm=realm,
            icon="raccoon",
            notes="小浣熊（xiaohuanxiong.com）：Bearer JWT，积分制；支持本机扫描 + 扫码授权 + 导入",
            rating={
                "tier": "elite",
                "reason": "不算很稳定，时快时慢，但给的量还行、够用。",
            },
            extra={
                "task_runners": list(_task_runners().keys()),
                # 契约词表内的动作：扫描本机 / 导入（文件或粘贴）/
                # 导出（备份含凭证 / 脱敏清单）/ 授权登录（扫码）/ 刷新额度。
                # `import-file` 已并入 `import`（两者都打开同一个导入弹窗，
                # 同时挂着会出现两个一模一样的按钮）。
                "actions": ["scan", "import", "export",
                            "begin-auth", "refresh-quota"],
            },
        ))
        logger.debug("小浣熊平台已登记：%s", platform_id)


install()
