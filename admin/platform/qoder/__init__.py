"""Qoder 平台网关（中国版 / 国际版）。

## 目录职责

    admin/platform/qoder/
      __init__.py            平台声明 + install()
      gateway.py             /v1/* 转发实现
      platform_gateway.py    Gateway 契约（账号池 / 任务 / 探测 / 卡片）
      core/                  协议层（cosy 签名 / endpoints / client / protocol）
      services/              状态层（accounts / scan / checkin / chat）

## 上游两套鉴权（不能混）

| 族 | 路径 | 鉴权 |
|---|---|---|
| openapi | `/api/v1/*`、`/api/v2/*` | Bearer + 基础 Cosy 头 |
| sash | `/sash/api/v1/me/*` | Bearer + `cosy-clienttype: 10`，无签名 |
| 推理 | `/algo/*` | **COSY 自签名**（见 `core/cosy.py`） |

## 两个平台而不是一个平台两个区

国内与国际是**两家独立 provider**：不同 openapi 主机、不同网关、
不同凭证（`qoder.com.cn` vs `qoder.sh`）。
存储上共用 `platform='qoder'` + `domain='cn'|'intl'`，
对外暴露成 `qoder` / `qoder-intl` 两个 PlatformSpec ——
这样「切到国内版就只看到国内的号」。
"""
from __future__ import annotations

import logging

from admin.platform.gateway import AccountOps, ClientProbe, Gateway
from admin.platform.registry import PlatformSpec

logger = logging.getLogger("admin.platform.qoder")

PLATFORM = "qoder"
#: 落库区域（`accounts.domain`）
REALMS = ("cn", "intl")
#: 区域 -> 对外平台 id
REALM_IDS = {"cn": "qoder", "intl": "qoder-intl"}
#: 对外平台 id -> 区域
ID_REALMS = {v: k for k, v in REALM_IDS.items()}


def _task_runners() -> dict:
    """本平台支持的定时任务。

    目前只有签到：额度刷新由网关层的通用任务覆盖，
    不在这里重复登记（重复会让调度面板出现两条同名任务）。
    """
    from admin.platform.qoder.services import checkin

    return {"qoder_checkin": checkin.run_daily_checkin}


def _realms() -> tuple:
    return ()


def install() -> None:
    """登记两个 PlatformSpec（幂等）。"""
    from admin.platform import registry as _registry

    for realm in REALMS:
        platform_id = REALM_IDS[realm]
        if _registry.get(platform_id) is not None:
            continue
        conf_label = "中国版" if realm == "cn" else "国际版"
        _registry.register(PlatformSpec(
            id=platform_id,
            label="Qoder %s" % conf_label,
            realms=(),
            enabled=True,
                # Qoder 的登录态加密在客户端原生层（auth.v1.dat），
                # 本机扫得到账号但**导不出来** —— 标 false，前端只留授权登录。
                supports_local_scan=False,
            # 转发实现：dispatch 看到非默认平台会调用它
            gateway="admin.platform.qoder.gateway",
            # 管理契约
            platform_gateway="admin.platform.qoder.platform_gateway:QoderGateway",
            storage_platform=PLATFORM,
            auth_callback="admin.platform.qoder.auth",
            realm=realm,
            icon="qoder",
            notes="Qoder %s（%s）。COSY 自签名鉴权；模型格式为 OpenAI，"
                  "支持 Chat Completions / Responses / Anthropic Messages 互转。"
                  % (conf_label, "qoder.com.cn" if realm == "cn" else "qoder.sh"),
            extra={
            "task_runners": list(_task_runners().keys()),
            # Qoder 的凭证加密在原生层：扫描能定位账号但读不出 token，
            # 所以「导入」实际走 PAT / 设备流（见 routers 的 import）
            # begin-auth: 设备授权登录（PKCE）。Qoder 的登录态加密在原生层
            # （auth.v1.dat），本地读不出 -> 只能走设备授权。
            # 不再声明 `import`：Qoder 的凭证加密在客户端原生层
            # （auth.v1.dat），本地导入必然 400「需要 pat 或 credential」。
            # 导入入口统一走 begin-auth（设备授权），避免用户点到死按钮。
            "actions": ["scan", "refresh-quota", "checkin",
                        "usage", "begin-auth"],
        },
        ))
        logger.debug("Qoder 平台已登记：%s", platform_id)


install()
