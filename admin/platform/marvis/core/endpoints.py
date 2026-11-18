"""腾讯 Marvis 端点表 + 请求签名 —— 上游只有一套 host，但**两套鉴权**。

## 上游摘要（逆向自 magpie-marvis 插件，2026-11-08）

    host            https://yybadaccess.3g.qq.com
    模型网关        POST /v1/chat/completions      OpenAI 协议，SSE 流式
    令牌钱包        POST /v3/marvis_get_wallet     业务签名接口
    续签            POST /marvis_client_login/marvis_refresh_token        （见下）
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
import logging
import os
import random
import time
import uuid

from . import uskey_cache
from . import uskey_signer

log = logging.getLogger(__name__)

#: 缓存层默认按哪个 model 建条目（调用方没给 model 时用）。
#:
#: 必须与 `MODEL_TABLE` 里的槽位名一致 —— uskey 的 bus_info 签了 model，
#: 换个名字就是另一份签名，缓存分桶也就跟着变。
_DEFAULT_SIGN_MODEL = "deepseek-v4-pro-external"

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

#: uskey 签名用的 busiid。
#:
#: ⚠ 与 `BUSINESS_ID` 不是一回事：`beacon_get_uskey` 的 busiid 参数
#: 有 9 字节上限（实测 —— 超过 9 字符会让输出从 376 变 392 字节，
#: 服务端按长度校验会判非法）。正版客户端传的是短数字串。
USKEY_BUSIID = "7800255"

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

#: 客户端版本，随 `Ual-Access-MarvisExt` 上报。
#:
#: ## 为什么是常量、不探测本机客户端（2026-11-18）
#:
#: 上游会拿它和另外几个指纹交叉校验，报一个不存在的版本等于自曝伪造。
#: 但「去本机客户端目录读版本」这件事本身就是一条**对客户端的依赖**：
#: 网关要能脱离 Marvis 客户端独立跑（换机器、扔服务器、客户端卸载），
#: 那些场景下 `E:\Marvis\Application\*` 根本不存在。
#:
#: 所以取值顺序是：
#:     1. 环境变量 `MARVIS_CLIENT_VERSION` —— 上游改了版本要求时改这个，
#:        不用动代码，也不用装客户端；
#:     2. 下面这个常量 —— 与客户端 1.60.3400.35 同源，实测被上游接受。
#:
#: ⚠ 不要改回「扫描安装目录」。那会让网关在干净机器上探不到版本、
#:    退化成一个可能已失效的兜底值，故障点还藏在客户端装没装上。
CLIENT_VERSION_FALLBACK = "1.60.3400.35"


def _detect_client_version() -> str:
    """取上报用的版本号 —— **只认环境变量和常量，不碰客户端目录**。

    历史：这个函数一度去扫 `E:\\Marvis\\Application\\*` 和注册表求最新版本。
    在装了客户端的机器上跑得挺好，但它把「网关能不能工作」和「客户端在哪」
    绑在了一起 —— 挪机器就失效。既然目标是不依赖客户端，探测就必须去掉。
    """
    override = (os.getenv("MARVIS_CLIENT_VERSION") or "").strip()
    if override:
        return override
    return CLIENT_VERSION_FALLBACK


CLIENT_VERSION = _detect_client_version()

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
#:
#: ⚠ 这份表是**按实测收敛过**的（2026-11-09，真实账号逐个打通）：
#: 只保留确实能出字的槽位。上游按账号等级下发模型权限，免费号拿不到的那几个
#: 在 `UNAVAILABLE_SLOTS` 里留档，换高等级账号时可以搬回来。
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
    ("hunyuan-turbos-vision", "HY-Vision-2.0-instruct", 32_000,
     {"vision": True}),
)

#: 客户端目录里有、但**当前等级账号调不动**的槽位。
#:
#: 实测（2026-11-09）这三个一律回 `{"code":4130001,"msg":"...无效模型id"}`，
#: 不是本网关的问题也不是参数问题 —— 是上游按账号等级下发的权限。
#:
#: 为什么不直接删掉：换到高等级账号时把它们从这张表搬回 `MODEL_TABLE` 即可，
#: 不用再去翻上游注册表。
UNAVAILABLE_SLOTS = {
    "claude-sonnet-4-6": "claude-sonnet-4-6（实测 4130001 无效模型id，需更高等级账号）",
    "claude-opus-4-6": "claude-opus-4-6（实测 4130001 无效模型id，需更高等级账号）",
    "gpt-5.4": "gpt-5.4-2026-03-05（实测 4130001 无效模型id，需更高等级账号）",
}

DEFAULT_OUTPUT_TOKENS = 32_000

#: 默认模型槽位（智能路由兜底）
DEFAULT_MODEL = "main-auto"

#: 槽位名 → 显示名
DISPLAY_BY_SLOT = {slot: name for slot, name, _ctx, _cap in MODEL_TABLE}
#: 显示名 → 槽位名
SLOT_BY_DISPLAY = {name: slot for slot, name, _ctx, _cap in MODEL_TABLE}

#: 槽位名 → **对外可读名**（客户端 / 控制台看到的模型名）。
#:
#: 为什么需要这一层：`main-auto` 是上游的**槽位名**（它只认这个），但把它
#: 当成模型名展示给用户是荒谬的 —— 用户在客户端里看到 `main-auto-marvis`
#: 完全不知道背后是 DeepSeek V4.1 Flash（2026-11-15 用户当场质疑）。
#: 而 `deepseek-v4-pro-external` 看着像模型名，所以只有 `main-auto` /
#: `hy3-preview-agent` 这几个槽位名「露馅」，表现为「Pro 没问题、Flash 有问题」。
#:
#: 映射到**真实模型名**后：
#:   * `/v1/models` 的 `id` 是 `deepseek-v4.1-flash`（+ 平台后缀）；
#:   * 客户端发这个名字时，`models_router.MODEL_NAME_MAP['marvis']` 的反查
#:     会把它换回槽位名再打上游（与 Qoder 的 `dfmodel` 同一套机制）。
READABLE_BY_SLOT = {
    "main-auto": "deepseek-v4.1-flash",        # deepseek-v4-1-flash-261017
    "deepseek-v4-pro-external": "deepseek-v4-pro",   # deepseek-v4-pro-202606
    "hy3-preview-agent": "deepseek-v4-flash",  # deepseek-v4-flash-ga-260731
    "hy3-preview-agent-real": "hy3",
    "hunyuan-turbos-vision": "hunyuan-vision-2.0",   # HY-Vision-2.0-instruct
}
#: 可读名 → 槽位名（反查；大小写不敏感）
SLOT_BY_READABLE = {name.lower(): slot for slot, name in READABLE_BY_SLOT.items()}
#: 小写槽位名 → 槽位名（客户端把模型名大写化时也要能认）
SLOT_BY_LOWER = {slot.lower(): slot for slot in DISPLAY_BY_SLOT}
#: 上游真实模型名 → 槽位名（`deepseek-v4-1-flash-261017` → `main-auto`）。
#: 客户端从文档/别处抄来上游真名时也要能打，否则只能靠人肉换算槽位。
SLOT_BY_UPSTREAM = {name.lower(): slot for slot, name, _c, _cap in MODEL_TABLE}

#: 旧版把目录存成数组下标（"0"…"7"），保留映射让老路由继续可用
ALIAS_BY_INDEX = {str(index): slot for index, (slot, *_rest) in enumerate(MODEL_TABLE)}

#: 已确认持续失败、本地直接拦掉的模型（上游 500 RPC / 空响应）
UNAVAILABLE_MODELS = frozenset({"kimi-k2.5"})


def canonical_model_id(model) -> str:
    """把用户传的 model 归一成上游槽位名。

    认四种写法：可读名（`deepseek-v4.1-flash`）/ 上游真实名
    （`deepseek-v4-1-flash-261017`）/ 旧数字下标（`"0"`）/ 已是槽位（`main-auto`）。
    前两种是**对外**的写法，后两种是历史与内部的写法，四条路都得通。
    """
    raw = str(model or "")
    if not raw:
        return raw
    return (ALIAS_BY_INDEX.get(raw)
            or SLOT_BY_DISPLAY.get(raw)
            or SLOT_BY_READABLE.get(raw.strip().lower())
            or SLOT_BY_LOWER.get(raw.strip().lower())
            or SLOT_BY_UPSTREAM.get(raw.strip().lower())
            or raw)


def readable_model_id(model) -> str:
    """槽位名 → 对外可读名（查不到就原样返回）。"""
    slot = canonical_model_id(model)
    return READABLE_BY_SLOT.get(slot, UNAVAILABLE_SLOTS.get(slot, slot))


def display_model_id(model) -> str:
    slot = canonical_model_id(model)
    return DISPLAY_BY_SLOT.get(slot, UNAVAILABLE_SLOTS.get(slot, slot))


def is_unavailable_model(model) -> bool:
    """本地就要拦掉的模型：显式 denylist + 当前等级拿不到的槽位。"""
    slot = canonical_model_id(model).strip()
    if slot.lower() in UNAVAILABLE_MODELS:
        return True
    return slot in UNAVAILABLE_SLOTS


def models_catalog(realm: str = "") -> list[dict]:
    """模型目录（供 `/v1/models` 与选号排序用）。

    `id` 用**槽位名**（`main-auto`），因为：

      * 上游只认槽位名 —— 发真实模型名会 400 无效模型 id；
      * MiraiHub 的 `model_configs.model_id` 就是用户写进 `model` 字段的值，
        dispatch 也按它校验「模型属不属于这个平台」。

    真实模型名放 `name`（卡片上显示的就是实际在跑什么），不放进 `id`。

    ## ⚠️ `id` 也不能是**槽位名**（2026-11-15 修）

    改造前 `id` 直接给槽位名，于是客户端里出现 `main-auto-marvis`——
    用户完全看不出这是 DeepSeek V4.1 Flash（真人反馈原话：
    「怎么模型能叫这个名称呢？它不是 deep sea V4.1 Flash 吗」）。
    `deepseek-v4-pro-external` 恰好长得像模型名，所以只有 Flash 那几个
    「露馅」，表现为「Pro 没问题、Flash 有问题」。

    现在 `id` 给**可读名**（`deepseek-v4.1-flash`），槽位名只留在 `api_id`
    里给上游用；客户端发可读名进来，`canonical_model_id()` 会换回槽位。
    """
    out = []
    for slot, name, context, cap in MODEL_TABLE:
        out.append({
            "id": readable_model_id(slot),
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


def uskey_of(*, openid: str = "", busiid: str = USKEY_BUSIID,
             conversation_id: str = "", response_id: str = "",
             scene: str = "", model: str = "", nonce: str = "",
             agent_tag: str = "") -> str:
    """现签一个 uskey —— 直调本机 Beacon SDK，与正版客户端同源。

    走 `uskey_signer`：DLL 在就用 `beacon_get_uskey` 真签，
    不在就退回 376 字节随机填充（网关不断链，只在日志留 WARNING）。

    ⚠ `busiid` 必须 ≤ 9 字符，否则输出会多 16 字节。
    """
    return uskey_signer.uskey(
        openid=openid,
        busiid=busiid[:9],
        conversation_id=conversation_id,
        response_id=response_id,
        scene=scene,
        model=model,
        nonce=nonce,
        agent_tag=agent_tag,
    )


def uskey_status() -> dict:
    """uskey 签名器的状态（给控制台/自检看是真签还是降级）。"""
    return uskey_signer.status()


def uskey_identity(acct: dict, *, scene: str = "", agent_tag: str = "",
                   conversation_id: str = "", response_id: str = "",
                   model: str = "", busiid: str = USKEY_BUSIID) -> dict:
    """本次请求要用的**整组身份四件套** —— uskey / nonce / conv / rid。

    ## 为什么必须打包返回（2026-11-18 事故）

    改造前的样子是：`marvis_ext()` 内部读缓存拿到 (uskey, nonce, conv, rid)，
    用这一组签出 uskey；而 `llm_headers()` 那边**自己**又生成了新的
    conv/rid 放进请求头。

    结果：uskey 的 bus_info 里写的是 A 组 id，请求头带的是 B 组 id。
    上游拿 nonce 复核时对不上 —— 直接 403 4100404。
    现场表现就是「网关好好的突然全部 429 风控」，而且改提示词、
    换账号、换 Key 全都没用，因为坏的是身份自证本身。

    实测证据（probe_uskey_validation.py 组 E）：
        旧 uskey 配新 nonce  ->  403
    uskey 和 nonce/conv/rid 是一个不可拆的原子组。

    所以现在只有一个入口产出这组值，谁要发请求就整组拿走，
    不允许任何调用方自己再造一半。
    """
    openid = str(acct.get("openid") or "")
    version = str(acct.get("client_version") or CLIENT_VERSION)
    sign_model = model or _DEFAULT_SIGN_MODEL

    def _sign(bus_info: str) -> str:
        """只返回**真签**结果；签不出来返回空串。

        ## 为什么不能直接用 `uskey_signer.uskey()`

        那个函数在 DLL 缺失时会返回 376 字节随机填充 —— 这是为了「网关不断链」
        做的降级，但它会把**随机值**当成功结果交出去。

        随机值写进历史文件后，诊断时会误以为「签过了」，
        排查时容易往错误方向找。所以这里只认真签结果。
        """
        if not uskey_signer._beacon.available:  # noqa: SLF001
            log.warning("beacon 不可用，本次无法真签 uskey")
            return ""
        try:
            return uskey_signer._beacon.sign(busiid, bus_info)  # noqa: SLF001
        except Exception as exc:  # noqa: BLE001
            log.warning("uskey 真签失败：%s", exc)
            return ""

    signed = uskey_cache.get_or_create(
        model=sign_model,
        openid=openid,
        sign_fn=_sign,
        scene=scene or "agent_chat",
        agent_tag=agent_tag or "main",
        conversation_id=conversation_id,
        response_id=response_id,
        client_version=version,
    )

    if signed is not None:
        uskey, nonce, cid, rid = signed
        return {"uskey": uskey, "nonce": nonce,
                "conversation_id": cid, "response_id": rid,
                "client_version": version, "cached": False}

    # beacon 不可用 —— 降级为随机填充（会被上游拒，但至少暴露问题）
    nonce = str(uuid.uuid4())
    cid = conversation_id or str(uuid.uuid4())
    rid = response_id or uuid.uuid4().hex
    uskey = uskey_of(
        openid=openid, busiid=busiid, conversation_id=cid, response_id=rid,
        scene=scene, model=model, nonce=nonce, agent_tag=agent_tag,
    )
    return {"uskey": uskey, "nonce": nonce,
            "conversation_id": cid, "response_id": rid,
            "client_version": version, "cached": False}


def marvis_ext(acct: dict, *, scene: str = "", agent_tag: str = "",
               conversation_id: str = "", response_id: str = "",
               model: str = "", busiid: str = USKEY_BUSIID,
               identity: dict | None = None) -> str:
    """`Ual-Access-MarvisExt` 的值 —— 紧凑 JSON，**不能有空格**（服务端按原文比对）。

    `identity` 给了就直接用（调用方已经拿过整组四件套，避免重复签）；
    没给就现取一份。

    ⚠ 请求头里的 `Marvis-Conversation-ID` / `Marvis-Response-ID` 必须
    取自**同一个 identity**，否则 uskey 与头对不上会 403 —— 见
    `uskey_identity()` 的说明。
    """
    ident = identity or uskey_identity(
        acct, scene=scene, agent_tag=agent_tag,
        conversation_id=conversation_id, response_id=response_id,
        model=model, busiid=busiid,
    )
    payload = {
        "osVersion": "Windows-10-10.0.26200",
        "guid": str(acct.get("guid") or ""),
        "uskey": ident["uskey"],
        "nonce": ident["nonce"],
        "reqScene": 1,
        "taskType": "user",
        "clientVersion": ident["client_version"],
        "isIoa": False,
    }
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


# ---------------------------------------------------------------------------
# 鉴权头
# ---------------------------------------------------------------------------
def llm_headers(acct: dict, *, scene: str = "agent_chat",
                agent_tag: str = "main",
                conversation_id: str = "", response_id: str = "",
                model: str = "") -> dict:
    """模型网关请求头（`/v1/chat/completions`）。

    ⚠ 不要签名、不要 Authorization 真实 token —— 这里 `Bearer placeholder`
    是 Marvis agent 自己的写法，鉴权靠 `Ual-Access-*` 那几个头。

    ## 默认值为什么是 agent_chat / main（2026-11-18 三组对照实测）

        网关旧默认 scene=preload_summary + 空 conv/rid  -> 403 4100404
        scene=agent_chat + agent_tag=main + 空 conv/rid -> 403 4100404
        scene=agent_chat + agent_tag=main + 有 conv/rid -> 200 ✅

    → **`Marvis-Conversation-ID` 与 `Marvis-Response-ID` 不能为空**，
      上游把空 id 当非法客户端直接判风控。

    ## ⚠ conv/rid 必须与 uskey 同源（2026-11-18 事故修复）

    改造前这里是「缺省时自己生成一对」：

        conversation_id = conversation_id or str(uuid.uuid4())
        response_id = response_id or uuid.uuid4().hex

    但 `marvis_ext()` 内部读缓存拿到的是**另一组** id 并签进了 uskey。
    于是请求头带 A 组 id、uskey 里写 B 组 id —— 上游复核时对不上，
    直接 403 4100404。

    现在先调 `uskey_identity()` 拿整组四件套，uskey 和这两个头
    取自同一份，物理上不可能再错位。
    """
    ident = uskey_identity(
        acct, scene=scene, agent_tag=agent_tag,
        conversation_id=conversation_id, response_id=response_id, model=model,
    )
    conversation_id = ident["conversation_id"]
    response_id = ident["response_id"]
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
        "Ual-Access-MarvisExt": marvis_ext(acct, identity=ident),
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


# ---------------------------------------------------------------------------
# 续签请求体
# ---------------------------------------------------------------------------
#: 续签 body 里 `loginType` 的取值：**恒为字面量 `"Marvis"`**。
#:
#: 它不是 `Ual-Access-Login-Type`（那个是 QC→"1" / WX→"2"）。写成 "2" 会回
#: `{"code":-109,"msg":"RefreshToken missing required param (openid, guid, token)"}`
#: —— 「参数明明都给了却说缺参数」的真因就在这一层（2026-11-16 依 magpie
#: 客户端实现复核，见 `refresh_body` docstring）。
REFRESH_LOGIN_TYPE = "Marvis"


def refresh_body(acct: dict) -> str:
    """续签请求体 —— **原样字符串**，签名对象就是这串字节。

    ## 形状是打通的唯一关键（一次嵌套都不能少）

        {"userInfo":{"openId":"…","refreshToken":"…","accessToken":"…",
                     "loginType":"Marvis"}}

    ## 为什么以前一直 -109

    上游读参数的路径是 `body.userInfo.{openId,refreshToken,accessToken}`：

      · 少一层 `userInfo` 嵌套 → 读不到；
      · 字段名写 `openid`（全小写）→ 读不到（要 `openId`）；
      · `loginType` 写成 `"1"` / `"2"`（登录方式码）→ 读不到（要 `"Marvis"`）。

    三种都回同一句 `RefreshToken missing required param (openid, guid, token)`，
    所以从错误文案反推参数位置永远推不出来 —— 必须照客户端自己的形状发。
    """
    payload = {
        "userInfo": {
            "openId": str(acct.get("openid") or ""),
            "refreshToken": str(acct.get("refresh_token") or ""),
            "accessToken": str(acct.get("access_token") or ""),
            "loginType": REFRESH_LOGIN_TYPE,
        },
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def authorize_url(login_type: str = "QC", guid: str = "", state: str = "") -> str:
    """生成授权页地址（用户在浏览器里登 QQ / 微信，拿到 code）。

    ⚠ 三个细节都是实测踩出来的：

      · `redirect_uri` 必须带 `?login_type=XX`，否则服务端取不到登录方式；
      · QQ 的 `state` 必须与渲染层一致是**小数**（`Math.random()*100`），
        写成整数会被判参数无效。
      · **`state` 必须由调用方传进来并原样保存** —— 这个函数以前自己
        `repr(random.random()*100)` 生成一个，而 `auth_start` 又另外算了
        一个存进会话，换码时带的是后者。两者不一致 → QQ 侧判
        `-109 RC_PARAMS_INVALID`（2026-11-15 真机复现），现象就是
        「扫码授权成功了，一提交就报参数无效」。参数一旦传进来，这里
        绝不再改它的值。
    """
    from urllib.parse import urlencode, quote

    redirect = "%s?login_type=%s" % (oauth_url(), login_type)
    if login_type.upper() == "WX":
        params = {
            "appid": WX_APP_ID,
            "redirect_uri": redirect,
            "response_type": "code",
            "scope": "snsapi_login",
            # 微信拿设备 guid 当 state（与 magpie 渲染层一致）
            "state": state or guid or "",
        }
        return WX_AUTHORIZE + "?" + urlencode(params, quote_via=quote)
    params = {
        "which": "Login",
        "display": "pc",
        "response_type": "code",
        "client_id": QQ_APP_ID,
        "redirect_uri": redirect,
        # 兜底也保持「小数」形态：没传 state 时自己算一个，但调用方
        # 应该传 —— 只有它能同时把同一个值存进会话。
        "state": state or ("%.7f" % (random.random() * 100)),
    }
    return QQ_AUTHORIZE + "?" + urlencode(params, quote_via=quote)


def new_oauth_state(login_type: str, guid: str) -> str:
    """本次授权要用的 `state`（`auth_start` / 授权地址 / 换码三处必须一致）。

    QQ：小数（渲染层 `Math.random()*100` 同口径）。
    微信：设备 guid —— 微信会把 state 原样回传给 redirect_uri，用 guid
    既能归因，也保证「授权地址里的 state」＝「回调里的 state」＝「换码时发的
    state」三者完全一致。
    """
    if str(login_type).upper() == "WX":
        return guid or ""
    return "%.7f" % (random.random() * 100)