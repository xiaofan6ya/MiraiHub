"""腾讯 Marvis 端点表 + 请求签名 —— 上游只有一套 host，但**两套鉴权**。

## 上游摘要（逆向自 magpie-marvis 插件，2026-11-08）

    host            https://yybadaccess.3g.qq.com
    模型网关        POST /v1/chat/completions      OpenAI 协议，SSE 流式
    令牌钱包        POST /v3/marvis_get_wallet     业务签名接口
    续签            GET  /marvis_client_login/marvis_refresh_token
    登录回调        GET  /marvis_client_login/marvis_oauth?login_type=&code=&state=
    QQ 授权         https://graph.qq.com/oauth2.0/authorize  (client_id=1903794758)
    微信授权        https://open.weixin.qq.com/connect/qrconnect (appid=wxb650708317d56f88)

## 两套鉴权**不能混**

    /v1/chat/completions   只要 Ual-Access-Openid / Access-Token / Login-Type / Guid
                           + Ual-Access-MarvisExt（uskey 指纹）。**不要**签名。
    /v3/*                  要 md5(query + timestamp + accessKey + nonce) 签名。

把签名头加到模型网关上是**错的**：会从 403 风控变成 401 登录态过期（插件侧实测）。

## 两个 UA 也不能混

    模型网关  必须是 `AsyncOpenAI/Python 2.32.0`（Marvis agent 自己的 httpx UA），
              换成浏览器 UA 会被风控拦（4100404）
    业务接口  用 Marvis 客户端的浏览器 UA（`Marvis/1.60.3300.408 ... Electron/44.7.0`），
              换成普通 Chrome UA 会被判参数无效
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import time
import uuid

PLATFORM = "marvis"

#: 单区。Marvis 只有国内站点，没有国际版。
REGIONS = {
    "cn": {
        "label": "腾讯 Marvis",
        "base_url": "https://yybadaccess.3g.qq.com",
    },
}

REALM_IDS = {"cn": "marvis"}

#: 业务签名用的 accessKey —— 硬编码在 Marvis 渲染层，不是密钥管理下发的。
#: 少了它 /v3/* 一律 403，且错误信息只说「签名错误」。
ACCESS_KEY = "fpzkeTlcGLVP37qiOWd29MyaOqaJYxMi"

#: 业务 id，随每个 /v3/* 请求走 `Ual-Access-Businessid`
BUSINESS_ID = "marvis_client"

#: 两个 OAuth 应用的 app id
QQ_APP_ID = "1903794758"
WX_APP_ID = "wxb650708317d56f88"

#: 模型网关 UA（**必须**是这个，见模块 docstring）
CLIENT_UA = "AsyncOpenAI/Python 2.32.0"

#: 业务接口 UA
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Marvis/1.60.3300.408 Chrome/152.0.7977.130 "
    "Electron/44.7.0 Safari/537.36"
)

#: 客户端版本，随 `Ual-Access-MarvisExt` 上报
CLIENT_VERSION = "1.60.3300.765"

#: 登录方式 → `Ual-Access-Login-Type`
LOGIN_TYPE = {"QC": "1", "WX": "2", "WXAPP": "2"}

# ---- 路径 ----
CHAT_PATH = "/v1/chat/completions"
WALLET_PATH = "/v3/marvis_get_wallet"
REFRESH_PATH = "/marvis_client_login/marvis_refresh_token"
OAUTH_PATH = "/marvis_client_login/marvis_oauth"

QQ_AUTHORIZE = "https://graph.qq.com/oauth2.0/authorize"
WX_AUTHORIZE = "https://open.weixin.qq.com/connect/qrconnect"


def base_of(realm: str = "") -> str:
    conf = REGIONS.get(realm or "cn") or REGIONS["cn"]
    return str(conf["base_url"]).rstrip("/")


def chat_url(realm: str = "") -> str:
    return base_of(realm) + CHAT_PATH


def wallet_url(realm: str = "") -> str:
    return base_of(realm) + WALLET_PATH


def refresh_url(realm: str = "") -> str:
    return base_of(realm) + REFRESH_PATH


def oauth_url(realm: str = "") -> str:
    return base_of(realm) + OAUTH_PATH


# ---------------------------------------------------------------------------
# 模型目录
# ---------------------------------------------------------------------------
#: `[槽位 id, 显示名, 上下文, 能力]`
#:
#: **真实模型名不能直接当 `model` 传**（上游回 400 无效模型 id），只能填槽位名；
#: 显示名是给人看的真实模型名。槽位表来自 Marvis 的 `config_defaults.yaml`
#: 注册表，只列真实存在的，不猜、不补。
#:
#: 不列 `file-auto` / `computer-auto` / `browser-auto` / `app-auto`：实测这四个
#: 全部路由到与 `main-auto` 相同的模型，列出来只会在列表里重复同名条目。
MODEL_TABLE = (
    # ── DeepSeek ──
    ("main-auto", "deepseek-v4-1-flash-261017", 1_000_000,
     {"reasoning": True, "tools": True}),
    ("deepseek-v4-pro-external", "deepseek-v4-pro-202606", 1_000_000,
     {"reasoning": True, "tools": True}),
    ("hy3-preview-agent", "deepseek-v4-flash-ga-260731", 1_000_000,
     {"reasoning": True, "tools": True}),
    # ── 其他厂商 ──
    ("hy3-preview-agent-real", "hy3", 1_000_000,
     {"reasoning": True, "tools": True}),
    ("claude-sonnet-4-6", "claude-sonnet-4-6", 1_000_000,
     {"reasoning": True, "tools": True, "vision": True}),
    ("claude-opus-4-6", "claude-opus-4-6", 1_000_000,
     {"reasoning": True, "tools": True, "vision": True}),
    ("gpt-5.4", "gpt-5.4-2026-03-05", 1_000_000,
     {"reasoning": True, "tools": True, "vision": True}),
    ("hunyuan-turbos-vision", "HY-Vision-2.0-instruct", 32_000,
     {"vision": True}),
)

DEFAULT_OUTPUT_TOKENS = 32_000

#: 默认模型槽位（智能路由兜底）
DEFAULT_MODEL = "main-auto"

#: 槽位名 → 显示名
DISPLAY_BY_SLOT = {slot: name for slot, name, _ctx, _cap in MODEL_TABLE}
#: 显示名 → 槽位名
SLOT_BY_DISPLAY = {name: slot for slot, name, _ctx, _cap in MODEL_TABLE}
#: 旧版把目录存成数组下标（"0"…"7"），保留映射让老路由继续可用
ALIAS_BY_INDEX = {str(index): slot for index, (slot, *_rest) in enumerate(MODEL_TABLE)}

#: 已确认持续失败、本地直接拦掉的模型（上游 500 RPC）
UNAVAILABLE_MODELS = frozenset({"kimi-k2.5"})


def canonical_model_id(model) -> str:
    """把用户传的 model 归一成上游槽位名。支持显示名 / 旧数字下标 / 已是槽位。"""
    raw = str(model or "")
    return ALIAS_BY_INDEX.get(raw) or SLOT_BY_DISPLAY.get(raw) or raw


def display_model_id(model) -> str:
    slot = canonical_model_id(model)
    return DISPLAY_BY_SLOT.get(slot, slot)


def is_unavailable_model(model) -> bool:
    return canonical_model_id(model).strip().lower() in UNAVAILABLE_MODELS


def models_catalog(realm: str = "") -> list[dict]:
    """模型目录（供 `/v1/models` 与选号排序用）。

    `id` 用**槽位名**（`main-auto`），因为：

      * 上游只认槽位名 —— 发真实模型名会 400 无效模型 id；
      * MiraiHub 的 `model_configs.model_id` 就是用户写进 `model` 字段的值，
        dispatch 也按它校验「模型属不属于这个平台」。

    真实模型名放 `name`（卡片上显示的就是实际在跑什么），不放进 `id`。
    """
    out = []
    for slot, name, context, cap in MODEL_TABLE:
        out.append({
            "id": slot,
            "api_id": slot,
            "name": name,
            "context": context,
            "max_output": DEFAULT_OUTPUT_TOKENS,
            "reasoning": bool(cap.get("reasoning")),
            "tools": bool(cap.get("tools")),
            "vision": bool(cap.get("vision")),
            "searches": bool(cap.get("search")),
        })
    return out


# ---------------------------------------------------------------------------
# 设备指纹
# ---------------------------------------------------------------------------
#: uskey 的字节长度（上游按长度校验格式）
USKEY_BYTES = 376


def uskey_of(_business_id: str = BUSINESS_ID) -> str:
    """现签一个 uskey。

    Marvis 客户端用自带 Beacon SDK 签真值；这里按上游格式本地生成
    （376 字节的 base64）。实测本地值可以通过模型网关的格式校验。
    """
    return base64.b64encode(os.urandom(USKEY_BYTES)).decode("ascii")


def marvis_ext(acct: dict) -> str:
    """`Ual-Access-MarvisExt` 的值 —— 紧凑 JSON，**不能有空格**（服务端按原文比对）。"""
    payload = {
        "osVersion": "Windows-10-10.0.26200",
        "guid": str(acct.get("guid") or ""),
        "uskey": uskey_of(),
        "nonce": str(uuid.uuid4()),
        "reqScene": 1,
        "taskType": "user",
        "clientVersion": str(acct.get("client_version") or CLIENT_VERSION),
        "isIoa": False,
    }
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


# ---------------------------------------------------------------------------
# 鉴权头
# ---------------------------------------------------------------------------
def llm_headers(acct: dict, *, scene: str = "preload_summary",
                agent_tag: str = "auto_discovery",
                conversation_id: str = "", response_id: str = "") -> dict:
    """模型网关请求头（`/v1/chat/completions`）。

    ⚠ 不要签名、不要 Authorization 真实 token —— 这里 `Bearer placeholder`
    是 Marvis agent 自己的写法，鉴权靠 `Ual-Access-*` 那几个头。
    """
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": CLIENT_UA,
        "X-Stainless-Lang": "python",
        "X-Stainless-Package-Version": "2.32.0",
        "X-Stainless-OS": "Windows",
        "X-Stainless-Arch": "other:amd64",
        "X-Stainless-Runtime": "CPython",
        "X-Stainless-Runtime-Version": "3.11.9",
        "X-Stainless-Async": "async:asyncio",
        "X-Stainless-Raw-Response": "true",
        "Authorization": "Bearer placeholder",
        "Ual-Access-Openid": str(acct.get("openid") or ""),
        "Ual-Access-Access-Token": str(acct.get("access_token") or ""),
        "Ual-Access-Login-Type": str(acct.get("login_type") or "1"),
        "Ual-Access-Guid": str(acct.get("guid") or ""),
        "Marvis-AgentTag": agent_tag,
        "Marvis-Conversation-ID": conversation_id,
        "Marvis-Response-ID": response_id,
        "Marvis-Scene": scene,
        "Marvis-Protocol-Version": "1000",
        "X-Device-GUID": str(acct.get("guid") or ""),
        "Ual-Access-MarvisExt": marvis_ext(acct),
    }


def ual_signature(query: str, timestamp: str, nonce: str) -> str:
    """`/v3/*` 的签名：`md5(query + timestamp + accessKey + nonce)`。"""
    raw = (query or "") + timestamp + ACCESS_KEY + nonce
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def business_headers(acct: dict, *, query: str, method: str = "GET") -> dict:
    """`/v3/*` 业务接口请求头（带签名）。

    `query` 的取值：GET 是查询串（不含 `?`），POST 是**请求体原文**。
    签名对象必须与实际发出的字节一致，否则 403。
    """
    timestamp = str(int(time.time() * 1000))
    nonce = str(1000 + random.randint(0, 8999))
    return {
        "Accept": "*/*",
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": BROWSER_UA,
        "Ual-Access-Businessid": BUSINESS_ID,
        "Ual-Access-Timestamp": timestamp,
        "Ual-Access-Nonce": nonce,
        "Ual-Access-Signature": ual_signature(query, timestamp, nonce),
        "Ual-Access-Guid": str(acct.get("guid") or ""),
        "Ual-Access-Access-Token": str(acct.get("access_token") or ""),
        "Ual-Access-Login-Type": str(acct.get("login_type") or "1"),
        "Ual-Access-Openid": str(acct.get("openid") or ""),
    }


def authorize_url(login_type: str = "QC", guid: str = "") -> str:
    """生成授权页地址（用户在浏览器里登 QQ / 微信，拿到 code）。

    ⚠ 两个细节都是实测踩出来的：
      · `redirect_uri` 必须带 `?login_type=XX`，否则服务端取不到登录方式；
      · QQ 的 `state` 必须与渲染层一致是**小数**（`Math.random()*100`），
        写成整数会被判参数无效；微信则用设备 guid 当 state。
    """
    from urllib.parse import urlencode, quote

    redirect = "%s?login_type=%s" % (oauth_url(), login_type)
    if login_type.upper() == "WX":
        params = {
            "appid": WX_APP_ID,
            "redirect_uri": redirect,
            "response_type": "code",
            "scope": "snsapi_login",
            "state": guid or "",
        }
        return WX_AUTHORIZE + "?" + urlencode(params, quote_via=quote)
    params = {
        "which": "Login",
        "display": "pc",
        "response_type": "code",
        "client_id": QQ_APP_ID,
        "redirect_uri": redirect,
        "state": repr(random.random() * 100),
    }
    return QQ_AUTHORIZE + "?" + urlencode(params, quote_via=quote)