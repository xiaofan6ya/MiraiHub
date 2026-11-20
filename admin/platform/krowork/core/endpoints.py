"""KroWork 上游端点表（协议还原结果，2026-11-19）。

## 拆包结论

| 组件 | 形态 | 职责 |
|---|---|---|
| `krow-app.exe`   | Tauri（Rust）51 MB | 桌面壳；本地控制面 `127.0.0.1:17322` |
| `krow-server.exe`| Nuitka（Python 3.12）111/255 MB | agent 后端 `127.0.0.1:17323`（FastAPI，`/health` 免鉴权） |
| `krow-shim.exe`  | Rust CLI 4 MB | 外部进程入口，走 `17322` + `.runtime-token` |
| `resources/packages/*.whl` | Cython `.pyd` | `krow_openai`（模型/对话）、`krow_kro_protocol`（kro 入口协议） |

## 上游（全部实测，见文件末尾「实测记录」）

    身份     https://auth.krowork.com/realms/krow/protocol/openid-connect   （Keycloak）
    对话     https://llm.krowork.com                                          （Go 网关）
    业务     https://api.krowork.com        fuse.krowork.com

对话端点是**按 provider 分前缀**的，前缀取自模型表里每个模型的 `proxy_path`：

    {proxy_path}/chat/completions     provider_type ∈ {openai-chat, dashscope, openai}
    /anthropic/v1/messages            provider_type = anthropic（原生 Anthropic 协议）
    /openai/v1/responses              provider_type = openai（Azure 后端）

`/ks/v1` 是通用聚合前缀（国内模型都走它），`/deepseek/v2`、`/glm/v1`、
`/qwen/compatible-mode/v1`、`/minimax/v1`、`/tokenverse/v1`、`/ali/api/v1`
是各家直连前缀。

## 模型目录是「两层」

    slot（对外，krow-model-1）  ──slots[region][slot].model_id──▶  backend（ds4-pro）
    backend                    ──models[backend].proxy_path──▶   实际端点前缀

网关对外只暴露 **backend 名**（`ds4-pro`）与 **slot 名**（`krow-model-1`）两套别名，
两者都能用：slot 先经 `slots` 还原成 backend，再按 `models[backend].proxy_path` 拼路径。
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# 主机
# ---------------------------------------------------------------------------
#: 国内区（`config.json` 的 `region=cn`）
HOST_CN = "https://llm.krowork.com"
#: 国际区
HOST_GLOBAL = "https://llm.kro.work"
#: 业务 API（模型表、应用、论坛）
API_CN = "https://api.krowork.com"
API_GLOBAL = "https://api.kro.work"
#: Keycloak realm
AUTH_CN = "https://auth.krowork.com/realms/krow/protocol/openid-connect"
AUTH_GLOBAL = "https://auth.kro.work/realms/krow/protocol/openid-connect"

REALM_HOST = {"cn": HOST_CN, "global": HOST_GLOBAL}
REALM_API = {"cn": API_CN, "global": API_GLOBAL}
REALM_AUTH = {"cn": AUTH_CN, "global": AUTH_GLOBAL}

# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------
#: 模型表（鉴权：Bearer id_token）。返回 version / models / slots / defaults。
PATH_MODEL_MAP = "/config/model-map"
#: 免鉴权探针（实测 200 `ok`）
PATH_HEALTH = "/health"
#: 原生 Anthropic 端点（注意有 `/v1`，`/anthropic/messages` 是 404）
PATH_ANTHROPIC_MESSAGES = "/anthropic/v1/messages"
#: OpenAI Responses（Azure 后端；上游偶发 502，网关侧要能回落到 chat 转写）
PATH_RESPONSES = "/openai/v1/responses"
#: OpenAI 原生 chat 前缀（provider_type=openai 走 /openai/v1，但**没有** /chat/completions）
PATH_OPENAI_CHAT = "/openai/v1/chat/completions"
#: 通用聚合前缀
PATH_KS_CHAT = "/ks/v1/chat/completions"

#: 业务 API 的免鉴权探针（实测 200，返回 service=krow-api-app 的时钟）
PATH_APP_CHECK = "/krow/api/app/check"

# ---------------------------------------------------------------------------
# 商务 / 积分（`api.krowork.com/krow/commerce/*`）
#
# 这批路径是从 web dashboard 的打包产物里扒出来的（不是猜的）：
#   https://h4.static.yximgs.com/.../dashboard/assets/request-xghCvCNO.js
#     → axios.create({baseURL: "https://api.krowork.com/"})
#     → Authorization: Bearer <keycloak token>
#       （同一个 Keycloak 的 token，与对话端点**通用**）
#   credits-R7wPK0Le.js
#     → GET /krow/commerce/credit/balance          当前积分
#     → GET /krow/commerce/credit/getCreditStat    积分流水
#     → GET /krow/commerce/plan/info               套餐清单
#     → GET /krow/commerce/plan/subscription       我的订阅
#     → GET /krow/commerce/checkin/status          签到状态（需 X-User-Timezone）
#     → POST /krow/commerce/checkin/claim          签到领取
#     → GET /krow/commerce/referral/myInfo         邀请码
#
# 响应统一信封：{"status":200,"message":"成功","data":{...}}
# 注意 **HTTP 也是 200**，业务失败看 body 里的 `status`（500/401 都在 200 里）。
# ---------------------------------------------------------------------------
PATH_CREDIT_BALANCE = "/krow/commerce/credit/balance"
PATH_CREDIT_STAT = "/krow/commerce/credit/getCreditStat"
PATH_PLAN_INFO = "/krow/commerce/plan/info"
PATH_SUBSCRIPTION = "/krow/commerce/plan/subscription"
PATH_CHECKIN_STATUS = "/krow/commerce/checkin/status"
PATH_CHECKIN_CLAIM = "/krow/commerce/checkin/claim"
PATH_REFERRAL = "/krow/commerce/referral/myInfo"

# ---------------------------------------------------------------------------
# 请求头（实测抓包所得，缺一不可）
# ---------------------------------------------------------------------------
#: 网关自报身份。上游按 `x-app` / `x-source` 归因，`miraihub` 是网关自己的名字。
CLIENT_APP = "miraihub"
CLIENT_SOURCE = "krow-openai"
CLIENT_VERSION = "0.3.2"

#: `provider_type` → 端点的固定映射（除 anthropic / openai 外都走 chat/completions）
ANTHROPIC = "anthropic"
OPENAI_RESPONSES = "openai"

#: 上游对 `model` 字段的两种写法都接受：backend 名（`ds4-pro`）或 slot（`krow-model-1`）
SLOT_PREFIX = "krow-model-"

#: **网关对外模型 id 的品牌后缀**（命名规范，操作者要求 2026-11-20）
#:
#:     /v1/models 的 id = 原厂模型名 + "-krowork"
#:
#: 例：`deepseek-v4-pro-krowork`、`glm-5.3-flash-krowork`、`kimi-k3-krowork`
#: 一眼看出「是哪个模型 + 属于哪个平台」，同时不会跟别家平台的同名模型混淆。
BRAND_SUFFIX = "-krowork"

# ---------------------------------------------------------------------------
# 槽位 → 可读模型名
#
# 上游只给 `krow-model-1` / `krow-model-n` 这种**槽位号**，控制台和客户端看到
# 一排 `krow-model-n` 根本分不出是哪个模型（2026-11-20 用户反馈）。
# 这里把槽位翻译成模型业界通用名，`/v1/models` 与控制台都用可读名。
#
# 三条规则（按优先级）：
#   1. 显式表 —— `ds4-*` 这种猜不出来的必须写死
#   2. 剥厂商噪声后缀 —— `qwen3.8-max-klink-check` → `qwen3.8-max`
#   3. 兜底保留 backend id（宁可难读，不能丢信息）
#
# **只翻译显示与调用名，上游请求体里的 model 仍然是 backend id**
# （`resolve()` 负责反查），所以这张表改动不会影响连通性。
# ---------------------------------------------------------------------------
#: 猜不出来的映射（`ds4` 是 DeepSeek V4 的内部缩写）
BACKEND_ALIASES = {
    "ds4-pro": "deepseek-v4-pro",
    "ds4-flash": "deepseek-v4-flash",
    "ds4-pro-global": "deepseek-v4-pro",
    "ds4-flash-global": "deepseek-v4-flash",
    "ds4-flash-klink": "deepseek-v4-flash",
    "ds4-flash-global-klink": "deepseek-v4-flash",
    "deepseek-v4-flash": "deepseek-v4-flash",
    "deepseek-v4-pro": "deepseek-v4-pro",
}

#: 上游加在 backend id 尾部的「链路标记」，不是模型名的一部分
_NOISE_SUFFIXES = (
    "-global-klink-check",
    "-klink-check",
    "-global-klink",
    "-klink",
    "-check",
)


def readable_name(backend_id: str) -> str:
    """backend id → 可读模型名（`ds4-pro` → `deepseek-v4-pro`）。

    只做两件事：查显式表、剥噪声后缀。**不做小写化** —— 交给
    `models_router.slugify_model_id` 统一处理（那里有整套规范规则）。
    """
    raw = str(backend_id or "").strip()
    if not raw:
        return ""
    if raw in BACKEND_ALIASES:
        return BACKEND_ALIASES[raw]
    low = raw.lower()
    if low in BACKEND_ALIASES:
        return BACKEND_ALIASES[low]
    for suffix in _NOISE_SUFFIXES:
        if low.endswith(suffix):
            return raw[: -len(suffix)]
    return raw


def canonical_model_id(name: str) -> str:
    """这个名字在 KroWork 里规范成什么（供 `models_router` 的两个钩子用）。

    同一个模型的所有写法都归一到**同一个可读名**：

        `krow-model-1` == `ds4-pro` == `deepseek-v4-pro`
                        == `deepseek-v4-pro-krowork`  →  `deepseek-v4-pro`

    两个消费方：

    * `models_router.readable_model_id()` —— 决定 `/v1/models` 与控制台
      展示的**对外 id**（不归一，界面就是一排 `krow-model-n`）；
    * `models_router.platform_of_model()` —— 判断客户端裸写的名字
      属不属于这个平台（不归一，`deepseek-v4-pro` 会被判成「无归属」）。

    ⚠️ 槽位名也**必须**归一（不能原样返回）：否则列表里 7 行分别是
    `krow-model-1` … `krow-model-n`，用户根本分不出哪个是哪个模型。
    """
    text = str(name or "").strip()
    if not text:
        return ""
    low = text.lower()
    # 网关自己的平台后缀先剥掉
    for suffix in ("-krowork", "-kro"):
        if low.endswith(suffix):
            low = low[: -len(suffix)]
            break
    # 槽位名：`krow-model-1` → 它的可读名
    slot_map = _SLOT_READABLE
    if low in slot_map:
        return slot_map[low]
    return readable_name(low).lower()


#: 槽位 → 可读名。上游 slots 是动态下发的，但 cn 区这 7 个是固定的
#: （见 `CASE-KROWORK.md` §4 的实测表）；拿到模型表后 `refresh_slot_map()`
#: 会用真实数据覆盖它，所以这里写死只是**冷启动兜底**。
_SLOT_READABLE = {
    "krow-model-1": "deepseek-v4-pro",
    "krow-model-n": "deepseek-v4-flash",
    "krow-model-i": "qwen3.8-max",
    "krow-model-j": "qwen3.8-flash",
    "krow-model-k": "glm-5.3",
    "krow-model-l": "glm-5.3-flash",
    "krow-model-m": "kimi-k3",
}


def refresh_slot_map(pairs) -> None:
    """用上游真实模型表覆盖槽位表（`pairs` 是 `[(slot, backend_id), …]`）。"""
    for slot, backend in pairs:
        slot_name = str(slot or "").strip().lower()
        if not slot_name:
            continue
        _SLOT_READABLE[slot_name] = readable_name(str(backend or "")).lower()


def endpoints_readable(name: str) -> str:
    """内部帮助函数：`readable_name` 的小写版（避免上面递归导入）。"""
    return readable_name(name).lower()


def brand_model_id(name: str) -> str:
    """任意写法 → **网关对外 model id**（原厂模型名 + `-krowork`）。

        `krow-model-1` / `ds4-pro` / `deepseek-v4-pro`
            →  `deepseek-v4-pro-krowork`

    入参可以是**任何一套名字**（槽位号 / backend / 原厂名 / 已带后缀），
    先进 `canonical_model_id` 归一，再补品牌后缀 —— 否则 `krow-model-1`
    会变成 `krow-model-1-krowork` 这种既难读又不规范的 id。

    这是 `/v1/models`、控制台、客户端配置**唯一**该出现的形态；
    上游请求体里仍然是 backend id（`client._payload_with_body` 负责换回去）。
    """
    base = canonical_model_id(str(name or ""))
    if not base:
        return ""
    if base.endswith(BRAND_SUFFIX):
        return base
    return base + BRAND_SUFFIX


def host_of(realm: str) -> str:
    return REALM_HOST.get((realm or "cn").lower(), HOST_CN)


def api_of(realm: str) -> str:
    return REALM_API.get((realm or "cn").lower(), API_CN)


def auth_of(realm: str) -> str:
    return REALM_AUTH.get((realm or "cn").lower(), AUTH_CN)


def chat_path(proxy_path: str) -> str:
    """按模型的 `proxy_path` 拼 chat/completions 路径。

    `proxy_path` 已经是 `/ks/v1`、`/deepseek/v2` 这种带版本的前缀，
    直接拼 `/chat/completions` 即可；缺省走 `/ks/v1`（通用聚合）。
    """
    base = (proxy_path or "").rstrip("/") or "/ks/v1"
    return base + "/chat/completions"


def endpoint_of(provider_type: str, proxy_path: str) -> str:
    """返回该 provider 的端点路径（不含 host）。

    | provider_type | 端点 |
    |---|---|
    | `anthropic` | `/anthropic/v1/messages` |
    | `openai` | `/openai/v1/responses`（Azure 只有 responses） |
    | 其它（openai-chat / dashscope / image 系） | `{proxy_path}/chat/completions` |
    """
    kind = (provider_type or "").strip().lower()
    if kind == ANTHROPIC:
        return PATH_ANTHROPIC_MESSAGES
    if kind == OPENAI_RESPONSES:
        return PATH_RESPONSES
    return chat_path(proxy_path)


# ---------------------------------------------------------------------------
# 本地（客户端）路径
# ---------------------------------------------------------------------------
#: 本机控制面（krow-app，Bearer `.runtime-token`）
LOCAL_HOST_APP = "http://127.0.0.1:17322"
#: 本机 agent 后端（krow-server，`/health` 免鉴权）
LOCAL_HOST_SERVER = "http://127.0.0.1:17323"
#: 本地 daemon 管道（id_token 的唯一活水源）
IPC_PIPE = r"\\.\pipe\krowd"

#: 数据目录（`%LOCALAPPDATA%\krow`）
DATA_DIR_NAME = "krow"

# ---------------------------------------------------------------------------
# 实测记录（2026-11-19，cn 区，账号 2217898929@qq.com）
# ---------------------------------------------------------------------------
#: ```
#: GET  /config/model-map                   200  models=61 slots=2区 defaults
#: POST /deepseek/v2/chat/completions       200  tools → tool_calls, reasoning_content, usage
#: POST /ks/v1/chat/completions             200  qwen/glm/kimi 走这里
#: POST /qwen/compatible-mode/v1/chat/...   200
#: POST /glm/v1/chat/completions            200
#: POST /anthropic/v1/messages              200  原生 Anthropic（msg_ 前缀 / stop_reason）
#: POST /openai/v1/responses                500  上游 Azure Bad Gateway（路径存在，转发正常）
#: POST /openai/v1/chat/completions         404  provider=openai 只有 responses
#: GET  /health                             200  "ok"（免鉴权）
#: GET  https://api.krowork.com/krow/api/app/check  200 免鉴权探针
#: ```
#: 鉴权头形状实测：无 → `{"error":"missing token"}`；`Bearer junk` → `{"error":"invalid token"}`
#: （`x-api-key` 不被识别，仍是 missing token）。
