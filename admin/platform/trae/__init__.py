"""Trae 平台接入（国内版 / 国际版**各自作为一个平台**）。

为什么要拆成两个平台而不是「一个平台两个区」：

    从运营视角看，国内版与国际版是**完全独立的两套东西** ——
    不同上游主机（trae-api-cn.mchost.guru vs core-normal.trae.ai）、
    不同客户端（Trae CN / Trae）、不同凭证（互不通用）、
    不同模型目录（国内无 Gemini/Claude 系）、不同额度口径
    （国内是积分、国际按请求次数）。
    典型表现：「国际版跟国内应该也算是两个平台」—— 这是对的：
    他要的是「切到国内版就只看到国内的号」，而不是「切到 Trae 再切一次区」。

    但对**存储**而言它们是同一套协议实现的两种配置，因此共用
    `accounts.platform='trae'` + `domain='cn'|'intl'`，只是对外暴露成
    两个 PlatformSpec（`storage_platform` + `realm` 把两者映射起来）。
    这样既不会为了「换个区」复制一份号池代码，界面上又能彻底分开。

目录结构参照 `admin/`，新增平台沿用同一形状：

    admin/trae/
      __init__.py          平台登记（3 个 PlatformSpec）
      gateway.py           /v1/* 分发入口（由 admin.platform.dispatch 转交）
      core/                纯协议层：上游客户端、指纹、目录、协议转换
      services/            状态层：账号池、选号、额度、记账、客户端目录
      routers/             出口层：/api/trae/* 后台接口
      data/                目录快照（仅兜底）
"""
from admin.platform.registry import PlatformSpec, register

#: 存储层的平台 id（两个区共用）
PLATFORM_ID = "trae"

#: 对外暴露的平台 id
CN_ID = "trae-cn"
INTL_ID = "trae-intl"


def install() -> list[PlatformSpec]:
    return [
        register(
            PlatformSpec(
                id=CN_ID,
                label="Trae 国内版",
                enabled=True,
                supports_local_scan=True,
                gateway="admin.platform.trae.gateway",
                platform_gateway="admin.platform.trae.platform_gateway:TraeGateway",
                icon="trae",
                notes="Trae 国内版（trae-api-cn.mchost.guru）。独立凭证与模型目录；"
                      "支持 Chat Completions / Responses / Anthropic Messages 互转。",
                storage_platform=PLATFORM_ID,
                realm="cn",
                extra={"kind": "proxied", "realm_key": "cn",
                   # begin-auth：Trae 没有设备授权端点，这里的「授权登录」
                   # 打开官方登录页，登录后回到本机点扫描导入 IDE 登录态。
                   # web-cookie 只服务「用量明细」，不是登录手段。
                   "actions": ["begin-auth", "scan", "import",
                               "refresh-quota", "usage", "web-cookie"]},
            )
        ),
        register(
            PlatformSpec(
                id=INTL_ID,
                label="Trae 国际版",
                enabled=True,
                supports_local_scan=True,
                gateway="admin.platform.trae.gateway",
                platform_gateway="admin.platform.trae.platform_gateway:TraeGateway",
                icon="trae",
                notes="Trae 国际版（core-normal.trae.ai）。独立凭证与模型目录；"
                      "额度按请求次数限额。",
                storage_platform=PLATFORM_ID,
                realm="intl",
                extra={"kind": "proxied", "realm_key": "intl",
                   "actions": ["begin-auth", "scan", "import",
                               "refresh-quota", "usage", "web-cookie"]},
            )
        ),
    ]


def spec(platform_id: str = CN_ID):
    from admin.platform.registry import get
    return get(platform_id)