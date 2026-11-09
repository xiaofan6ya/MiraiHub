"""Loomy 端点表（以**实际运行配置**为准）。

## 关键：两个 baseURL 的区别（我踩过这个坑）

    ossptest.voicecloud.cn/loomy/integration/api/v1   测试环境（模板里写的）
    loomyad.xunfei.cn/api/v1                          真实运行环境 ★

模板 `opencode-home-template/opencode.json` 里是前者，
**实际生效的是 `<configRoot>/opencode/opencode.json`**（运行时写入）。
读模板会拿到测试地址 → 所有请求返回 `100002 登录已失效`，
看起来像「凭证过期」，其实是**地址错了**。

> 教训：配置有两个位置时，读**运行时那一份**。
> 判断方法：比大小（模板 6.9 KB vs 运行时 21.3 KB）、比 mtime。

## 鉴权（三个都不能少）

    Authorization: Bearer <session>
    token: <session>                     ← 双头，只发一个会失败
    traceparent: 00-<32hex>-<16hex>-01   ← 缺了会**挂死**，不是报错

## session 来源

`<configRoot>/userData/auth-session.json`（明文 JSON，137 字节）
其中 `configRoot = %PUBLIC%/Loomy/<sha256(username)[:12]>`（父目录取环境变量）
"""
from __future__ import annotations

import secrets

PLATFORM = "loomy"

REGIONS = {
    "cn": {
        "label": "Loomy",
        "base_url": "https://loomyad.xunfei.cn/api/v1",
        "account_base": "https://accounttest.xfinfr.com",
    },
}

REALM_IDS = {"cn": "loomy"}

# ---- 接口 ----
MODELS_PATH = "/models"
CHAT_PATH = "/chat/completions"
#: 积分余额。**注意鉴权只要 `token` 头**（不带 Authorization）。
POINTS_BALANCE_PATH = "/points/balance"
POINTS_RECORDS_PATH = "/points/records"
#: 每日签到（领取当日赠送额度）
POINTS_FIRST_LOGIN_PATH = "/points/first-login"

# ---- 新手任务（一次性，共 10000 积分）----
#:
#: 契约来源：客户端 `app.asar.unpacked/electron/onboarding-service.js`
#: （`backend === 'real'` 分支），envelope 是 `{code, desc, trace_id, data}`，
#: 成功 `code == "000000"`，鉴权失效 `100002`。
#:
#: ⚠️ 客户端**默认 backend='mock'**：只写 `userData/onboarding-tasks.json`，
#: 根本不发请求 —— 所以"客户端显示已获得 500"并不代表服务端记了账。
#: 实测（2026-11-09）服务端这两个接口是**真的**：完成后余额立即增加。
#: 因此网关直接调它，比让用户一个个点客户端更省事。
ONBOARDING_TASKS_PATH = "/onboarding/tasks"
ONBOARDING_COMPLETE_PATH = "/onboarding/tasks/complete"

# ---- 邀请码 / 兑换码（2026-11-09，从客户端 points-service.js 挖出）----
#:
#: 邀请码绑定（"激活"）：
#:     GET  /points/activation          查当前绑定状态
#:     POST /points/activation          {inviteCode, deviceId?} 绑定邀请码
#:     POST /points/first-login         {inviteCode?, deviceId?, channel?, visitedId?}
#:                                      首次登录初始化，**也能顺带绑定邀请码**
#: 兑换码换积分：
#:     POST /points/redemption-codes/redeem  {code}   → 兑换积分
#:     GET  /points/invitation-codes          查自己的邀请码
#:
#: 鉴权一律只要 `token` 头（与 points 族其它接口一致）。
POINTS_ACTIVATION_PATH = "/points/activation"
POINTS_REDEEM_PATH = "/points/redemption-codes/redeem"
#: 路径按客户端**原文**抄：`queryInvitationCodes()` 打的是
#: `/api/v1/invitation-codes`（**不在** `points/` 下面）。写成
#: `/points/invitation-codes` 会 404 —— 实测过。
POINTS_INVITATION_CODES_PATH = "/invitation-codes"

#: key -> 积分。与客户端 `TASK_POINTS` 完全一致（8 个 key，合计 10000）。
#: 顺序按客户端分组：初识 Loomy(3000) / 打造你的专属 Loomy(3500) /
#: 遇见你的 AI 搭子(3500)。
ONBOARDING_TASKS = {
    "first_message": 500,      # 发送你的第一条消息
    "pick_skill": 1000,        # 尝试选择一个技能
    "generate_ppt": 1500,      # 生成一份 PPT
    "set_schedule": 1000,      # 设置定时任务
    "install_skill": 1500,     # 在技能广场安装一个技能
    "configure_remote": 1000,  # 配置远程控制
    "create_soul": 1500,       # 创建你的第一个搭子
    "share_soul": 2000,        # 把搭子分享给朋友
}

#: key -> 中文任务名（**控制台展示用**）。
#:
#: 界面上一律显示中文：`first_message` 这种内部 key 对用户毫无意义，
#: 而且和客户端界面的「发送你的第一条消息」对不上，用户没法判断
#: 「我到底做完哪几个」。key 本身仍然只用于调接口，不做翻译。
ONBOARDING_TASK_LABELS = {
    "first_message": "发送你的第一条消息",
    "pick_skill": "尝试选择一个技能",
    "generate_ppt": "生成一份 PPT",
    "set_schedule": "设置定时任务",
    "install_skill": "在技能广场安装一个技能",
    "configure_remote": "配置远程控制",
    "create_soul": "创建你的第一个搭子",
    "share_soul": "把搭子分享给朋友",
}

#: 分组（与客户端三段对应，控制台按组展示进度）。
ONBOARDING_GROUPS = (
    ("getting-started", "初识 Loomy", 3000,
     ("first_message", "pick_skill", "generate_ppt")),
    ("make-it-yours", "打造你的专属 Loomy", 3500,
     ("set_schedule", "install_skill", "configure_remote")),
    ("create-share", "遇见你的 AI 搭子", 3500,
     ("create_soul", "share_soul")),
)

# ---- 配置目录（userDataPath 是算出来的，不是 %APPDATA%/<产品名>）----
#: `config-path.js`：%PUBLIC%/Loomy/<sha256(username)[:12]>/userData
#: 只记录「名字」，位置由 services/scan.py 从环境变量动态解析
CONFIG_ROOT_PARENT = r"%PUBLIC%\Loomy"
#: 凭证文件名（源码 `new Store({ name: 'auth-session' })`）
CRED_FILE = "auth-session.json"
#: 运行时 provider 配置（真实 baseURL 在这里，**不是模板**）
OPENCODE_CONFIG = "opencode/opencode.json"


def auth_headers(token: str, version: str = "1.0.0") -> dict:
    """鉴权头。

    ⚠️ `traceparent` 缺失时上游不报错、**直接挂到超时** ——
    这类「静默挂死」最难查（源码注释里有实测记录）。
    """
    return {
        "Authorization": "Bearer " + token,
        "token": token,
        "Accept": "application/json",
        "traceparent": "00-%s-%s-01" % (secrets.token_hex(16),
                                        secrets.token_hex(8)),
        "loomy-version": version,
    }


def points_headers(token: str) -> dict:
    """积分接口的头。

    ⚠️ 与 chat 链路不同：**只带 `token`**，不带 `Authorization`。
    实测带 Bearer 也能通，但官方只发这一个头，保持一致最稳。
    """
    return {"token": token, "Accept": "application/json"}


def chat_headers(token: str, version: str = "1.0.0", session_id: str = "") -> dict:
    """chat 链路的完整请求头：鉴权 + **归因头**。

    官方客户端（`utils/request-headers.js`）对每个模型请求都注入
    `ChatId` / `MsgId` / `TurnId` / `InvokeOrigin` / `X-Loomy-Request-Purpose`。
    服务端靠它们做计费归因 —— 源码注释原话：缺了这些头的子代理请求
    「连 ChatId 都没有」，积分归属会失败。

    ⚠️ `ChatId` 是**会话**标识，必须按「账号 + 对话」稳定（由
    `pool.stable_session_id` 派生后经 `session_id` 传入）。原先每请求
    `uuid4()` 的写法等于每轮都新建一个会话：上游前缀缓存命中不了，
    且一个号几分钟内冒出几十个会话，很容易被当成脚本。

    `MsgId` / `TurnId` 仍然是**每请求唯一**：它们标识「一条消息/一轮」，
    重复值会让上游把两条不同的消息当成同一条（去重/覆盖）。
    """
    import uuid as _uuid

    headers = auth_headers(token, version)
    chat_id = session_id or ("gwchat-" + _uuid.uuid4().hex[:16])
    msg_id = "gwmsg-" + _uuid.uuid4().hex[:16]
    headers.update({
        "ChatId": chat_id,
        "MsgId": msg_id,
        "TurnId": msg_id,
        "InvokeOrigin": "user",
        "X-Loomy-Request-Purpose": "chat.message",
    })
    return headers


def base_of(realm: str) -> str:
    """上游 base_url。

    允许用环境变量覆盖：`ADMIN_LOOMY_BASE_URL`（或按区分
    `ADMIN_LOOMY_BASE_URL_CN`）。

    为什么要这个口子：联调/回归时需要一个**可控的上游**来复现
    「工具调用参数被截断」「finish_reason 分帧」这类故障 —— 真上游
    不会按你的节奏出错。把它指向本地 mock 就能稳定复现与验证。
    生产环境不设这个变量，行为与原来完全一致。
    """
    import os

    conf = REGIONS.get(realm or "cn") or REGIONS["cn"]
    keys = []
    if realm:
        keys.append("ADMIN_LOOMY_BASE_URL_" + str(realm).upper())
    keys.append("ADMIN_LOOMY_BASE_URL")
    for key in keys:
        value = (os.getenv(key) or "").strip()
        if value:
            return value.rstrip("/")
    return str(conf["base_url"]).rstrip("/")
