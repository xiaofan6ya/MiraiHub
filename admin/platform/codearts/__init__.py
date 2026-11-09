"""CodeArts（华为云 CodeArts Doer）平台网关。

## 目录

    admin/platform/codearts/
      __init__.py            平台声明 + install()
      gateway.py             /v1/* 转发（上游本身就是 OpenAI 协议）
      platform_gateway.py    Gateway 契约（账号池 / 任务 / 探测 / 卡片）
      core/signer.py         华为云 AK/SK 签名（SDK-HMAC-SHA256）
      core/client.py         对话 / 模型 / 额度 / 身份
      core/session.py        本机登录态解密（OSCrypt+DPAPI）与 DPoP 续期
      core/paths.py          安装与数据目录
      services/accounts.py   凭证与账号池
      services/scan.py       本机扫描（不含秘密）
      routers/accounts.py    /api/codearts/*

## 拆包结论（`<安装目录>/resources/app.asar`，安装目录动态定位）

* 主进程 `out/main/index.js`，认证走华为云 IAM STS（OAuth2 + PKCE + DPoP）；
* 登录态在 `%APPDATA%/codearts/secretStorage/<sha256("authSession:cn-north-4")>.enc`，
  Chromium OSCrypt（v10 + AES-GCM），主密钥由 DPAPI 保护在 `Local State`；
* 业务接口全部要求 AK/SK 请求签名：
  对话 `/api/v2/chat/completions`、模型 `/v1/model/builtin`（需 Agent-Type）、
  额度 `/snap-manager/v1/statistics/plugin`。

## 与其它平台的关键差异

| 项 | CodeArts | 其它 |
|---|---|---|
| 鉴权 | AK/SK 签名（每请求现算） | Bearer token |
| 凭证寿命 | ~6 小时（STS 临时凭证） | 天~周 |
| 续期 | refresh_token + DPoP + PKCE **可自动续** | 多数只能重登 |
| 额度 | 积分包（有起止日期） | 积分/次数 |
| 凭证来源 | 本机加密登录态 | 明文 JSON / 扫码 |

## 接入纪律（与 skill 一致）

* 列表接口**只读 DB 快照**，刷新丢后台（`shared.kick_background_refresh`）；
* 选号用 `shared.select_account(min_balance=0)` —— 0 积分的号不进候选；
* 402/401 必须**可重试并冷却**，否则会「一直撞同一个坏号」；
* 有借有还：`shared.lease` / `shared.release` 包在每个上游调用外层。
"""
from __future__ import annotations

import logging

from admin.platform.gateway import AccountOps, ClientProbe, Gateway
from admin.platform.registry import PlatformSpec

logger = logging.getLogger("admin.platform.codearts")

PLATFORM = "codearts"
REALMS = ("cn",)
REALM_IDS = {"cn": "codearts"}


def _task_runners() -> dict:
    """本平台的定时任务。

    保活：STS 临时凭证 6 小时过期，有 refresh_token 就能一直续 ——
    **必须**定时续，否则一觉醒来整池都是 401。
    签到（2026-11-07 新增）：客户端 BenefitService 的权益领取
    （benefit claim + policyCenter welfare），协议还原见 services/benefit.py。
    """
    from admin.platform.codearts.platform_gateway import CodeArtsAccountOps

    ops = CodeArtsAccountOps("cn")
    return {"codearts_keepalive": ops.keepalive,
            "codearts_checkin": ops.checkin}


def install() -> None:
    """登记 PlatformSpec（幂等）。"""
    from admin.platform import registry as _registry

    for realm in REALMS:
        platform_id = REALM_IDS[realm]
        if _registry.get(platform_id) is not None:
            continue
        _registry.register(PlatformSpec(
            id=platform_id,
            label="CodeArts",
            realms=(),
            enabled=True,
            # 凭证只能来自本机客户端登录态（没有公开的授权端点给第三方用）
            supports_local_scan=True,
            gateway="admin.platform.codearts.gateway",
            platform_gateway="admin.platform.codearts.platform_gateway:CodeArtsGateway",
            storage_platform=PLATFORM,
            realm=realm,
            icon="codearts",
            notes="华为云 CodeArts（AK/SK 签名）",
            auth_mode="local-scan",
            rating={
                "tier": "npc",
                "reason": "保活续签比较麻烦，额度不算多但够用，速度不快。",
            },
            extra={
                "task_runners": list(_task_runners().keys()),
                # 控制台按钮：扫描 / 导入 / 导出 / 签到 / 刷新额度。
                # `export` 端点 `/api/codearts/export` 早就实现了，但这里
                # 一直没声明 —— 于是控制台不渲染「导出」按钮，用户看到的是
                # 「codearts 只有账号导入」（issue #25）。
                "actions": ["scan", "import", "export", "checkin", "refresh-quota"],
            },
        ))
        logger.debug("CodeArts 平台已登记：%s", platform_id)


install()
