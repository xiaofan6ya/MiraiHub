"""KroWork 平台声明 + `install()`。

## 目录

    admin/platform/krowork/
      __init__.py            平台声明 + install()
      gateway.py             /v1/* 三端转发
      platform_gateway.py    Gateway 契约（账号池 / 任务 / 探测 / 卡片）
      core/endpoints.py      端点表（拆包结论 + 实测记录）
      core/client.py         上游 HTTP（模型表 / 三端调用 / SSE）
      core/token.py          凭证（env / 文件 / sidecar / refresh 四级链）
      services/accounts.py   账号池
      routers/accounts.py    /api/krowork/*

## 与其它平台的关键差异

| 项 | KroWork | 其它平台 |
|---|---|---|
| 凭证 | Keycloak id_token（**30 分钟**） | Cookie / 天级 token |
| 取票 | 本地 daemon 管道 `\\\\.\\pipe\\krowd`（`krow_openai` 内部） | 明文 JSON / 扫码 |
| 模型目录 | 上游 `GET /config/model-map`（61 后端 + 分 region 槽位） | 固定清单 / 目录接口 |
| 端点 | **按 provider 分前缀**（`/deepseek/v2`、`/ks/v1`、`/anthropic/v1` …） | 单一 base_url |
| 空额度 | 无额度接口（`/v1/balance` 不支持） | 积分 / 余额 |
| 签到 | 没有 | Trae/Qoder 有 |
"""
from __future__ import annotations

import logging

from admin.platform.registry import PlatformSpec

logger = logging.getLogger("admin.platform.krowork")

PLATFORM = "krowork"
#: 落库区域（槽位表按 cn / global 分，但网关只用一个存储区）
REALMS = ("cn",)
REALM_IDS = {"cn": "krowork"}


def _task_runners() -> dict:
    """本平台的定时任务。

    四个都要，各有分工：

    | 任务 | 周期 | 为什么必须有 |
    |---|---|---|
    | `krowork_keepalive` | 10 分钟 | id_token 30 分钟寿命，不续就是每半小时全 401 |
    | `krowork_checkin` | 每日 | 每日签到 200 积分（实测到账） |
    | `krowork_refresh_quota` | 60 分钟 | 把积分写回 `balance_*` 列，账号池才不显示 0/0 |
    | `krowork_refresh_catalog` | 360 分钟 | 上游 `model_map_version` 变了我这边才知道 |

    **全部不依赖客户端**：凭证走号池里的 refresh_token（需带 `offline_access`），
    sidecar（借客户端 3.12 环境）只是额外的新鲜来源，客户端不开也能跑。
    """
    from admin.platform.krowork.platform_gateway import (
        KroworkAccountOps, _run_quota)

    ops = KroworkAccountOps()
    return {
        "krowork_keepalive": ops.keepalive,
        "krowork_checkin": ops.checkin,
        "krowork_refresh_quota": _run_quota,
        "krowork_refresh_catalog": _refresh_catalog,
    }


def _refresh_catalog(db, schedule=None) -> dict:
    from admin.platform.krowork.platform_gateway import _run_refresh_catalog

    return _run_refresh_catalog(db, schedule)


def _probe(db, schedule=None) -> dict:
    from admin.platform.krowork.platform_gateway import _run_probe

    return _run_probe(db, schedule)


def install() -> None:
    """登记 PlatformSpec（幂等）。"""
    from admin.platform import registry as _registry

    for realm in REALMS:
        platform_id = REALM_IDS[realm]
        if _registry.get(platform_id) is not None:
            continue
        _registry.register(PlatformSpec(
            id=platform_id,
            label="KroWork",
            realms=(),
            enabled=True,
            # 凭证只能来自本机客户端（没有对第三方开放的授权端点）
            supports_local_scan=True,
            gateway="admin.platform.krowork.gateway",
            platform_gateway="admin.platform.krowork.platform_gateway:KroworkGateway",
            storage_platform=PLATFORM,
            realm=realm,
            icon="krowork",
            notes="KroWork（Kro）本地 AI 客户端；Keycloak id_token + 分 provider 端点",
            auth_mode="local-scan",
            rating={
                "tier": "npc",
                "reason": "上游模型多（DeepSeek V4 / Qwen 3.8 / GLM 5.3 / Kimi K3），"
                          "但登录态只有 30 分钟，要靠客户端保活。",
            },
            extra={
                "task_runners": list(_task_runners().keys()),
                # 授权登录排第一：KroWork 的客户端登录态只有 30 分钟，
                # 「扫描本机」在客户端登出后直接失效，凭手机号授权才是不依赖客户端的路。
                "actions": ["begin-auth", "scan", "import", "export",
                            "refresh-quota", "usage", "checkin"],
            },
        ))
        logger.debug("KroWork 平台已登记：%s", platform_id)


install()
