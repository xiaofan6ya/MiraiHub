"""科搭 Codar 端点表、两套协议的请求头与内置模型目录。

## 上游摘要（协议事实，逐条核对过）

科搭的桌面应用把流量分给两套主机，网关这里照同一条分界走：

    对话 / 目录 / 活动   https://api.claw.speedai.com
    账号 / 点数 / 签到   https://api.kuaipaper.com

**两套对话协议并存**是这个平台最主要的特殊性：

  * 绝大多数模型是 OpenAI 协议 —— `POST /v1/chat/completions`，
    `Authorization: Bearer <token>`；
  * Claude 系模型是 Anthropic 协议 —— `POST /v1/messages`，
    鉴权走 `X-Api-Key: <token>` + `Anthropic-Version`，
    **不是** Bearer（带错头等于没有凭证）。

走哪套由模型目录的 `protocol.type` 决定（`anthropic_messages` /
`openai_chat_completions`），目录里没说才按模型名兜底（`claude-*`）。

## 其余端点

    目录     GET  {gateway}/v1/codar/models                       Bearer
    主余额   POST {account}/v1/gettoken        {username, token:0}  Bearer
    副余额   POST {account}/v1/agent/balance   {username}           Bearer
    签到     POST {account}/v1/daily-sign      {username}           Bearer
    签到态   POST {account}/v1/daily-sign/status {username}         Bearer
    续期     POST {account}/v1/token/refresh                        Bearer
    活动     GET  {gateway}/api/codar/activities/mine               Bearer
    免费模型 GET  {gateway}/api/codar/activities/free-model         Bearer
             POST {gateway}/api/codar/activities/free-model/claim   Bearer

余额有**两个来源**：`/v1/gettoken` 的 `token` 是通用点数、`agent_token`
是 agent 点数；`/v1/agent/balance` 是另一份读数（`agent_balance`），
主读数缺 agent 字段时才用它补。两条都失败才算余额未知 —— 一条死了还
有一条能出数，卡片不该因此空白。

签到是**滚动 24 小时**（不是自然日），所以 `retry_after` 是「距下次可领
多少秒」，不是「到明天几点」。
"""
from __future__ import annotations

import os

PLATFORM = "codar"
#: 对外平台 id（单平台，没有区域概念）
PUBLIC = "codar"

#: 对话 / 目录 / 活动所在主机（桌面端把它当 speedai 目标）
GATEWAY_BASE = "https://api.claw.speedai.com"
#: 账号 / 点数 / 签到 / 续期所在主机
ACCOUNT_BASE = "https://api.kuaipaper.com"

# ---- 对话 ----
CHAT_PATH = "/v1/chat/completions"
MESSAGES_PATH = "/v1/messages"

# ---- 目录 ----
CATALOG_PATH = "/v1/codar/models"

# ---- 账号面 ----
POINTS_PATH = "/v1/gettoken"
AGENT_BALANCE_PATH = "/v1/agent/balance"
SIGN_PATH = "/v1/daily-sign"
SIGN_STATUS_PATH = "/v1/daily-sign/status"
REFRESH_PATH = "/v1/token/refresh"

# ---- 活动 ----
ACTIVITIES_PATH = "/api/codar/activities/mine"
FREE_MODEL_PATH = "/api/codar/activities/free-model"
FREE_MODEL_CLAIM_PATH = "/api/codar/activities/free-model/claim"

#: Anthropic 协议版本头。上游按这个值决定请求体接受的字段集，
#: 缺了或被当成 OpenAI 请求处理。
ANTHROPIC_VERSION = "2023-06-01"

#: 每账号在途请求上限。实测（2026-10-27）第 4 个并发请求上游回
#: 429 `concurrent_limit_exceeded`，上游不公开这个数字；目录里若给出
#: `max_concurrency` 则以目录为准（取最小的那个）。
DEFAULT_MAX_CONCURRENCY = 3
#: 允许环境变量覆盖，但钳在 1..8：再高只是把排队换成 429。
MAX_CONCURRENCY_CAP = 8


def max_concurrency() -> int:
    raw = os.environ.get("CODAR_MAX_CONCURRENCY", "").strip()
    try:
        value = int(raw) if raw else DEFAULT_MAX_CONCURRENCY
    except ValueError:
        value = DEFAULT_MAX_CONCURRENCY
    return max(1, min(value, MAX_CONCURRENCY_CAP))


#: 本网关自己的 UA。不冒充桌面客户端：这个平台对第三方客户端有明确的
#: 拒绝语义（`client_not_supported`），伪装既解决不了它，也让排障失真。
USER_AGENT = "MiraiHub/codar"

#: 两套协议的请求头（**鉴权头不同，这是最容易写错的一处**）
BEARER_HEADERS = {
    "Accept": "application/json",
    "User-Agent": USER_AGENT,
}
ANTHROPIC_HEADERS = {
    "Accept": "application/json",
    "User-Agent": USER_AGENT,
    "Anthropic-Version": ANTHROPIC_VERSION,
}


def bearer_headers(token: str) -> dict:
    """OpenAI 侧 / 账号侧的请求头。"""
    headers = dict(BEARER_HEADERS)
    token = str(token or "").strip()
    if token:
        headers["Authorization"] = "Bearer " + token
    return headers


def anthropic_headers(token: str) -> dict:
    """Anthropic 侧（`/v1/messages`）的请求头。"""
    headers = dict(ANTHROPIC_HEADERS)
    token = str(token or "").strip()
    if token:
        headers["X-Api-Key"] = token
    return headers


def headers_for(protocol: str, token: str) -> dict:
    """按协议给鉴权头：anthropic 走 X-Api-Key，其余走 Bearer。"""
    return (anthropic_headers(token) if protocol == "anthropic"
            else bearer_headers(token))


def gateway_url(path: str) -> str:
    return GATEWAY_BASE + path


def account_url(path: str) -> str:
    return ACCOUNT_BASE + path


# ---------------------------------------------------------------------------
# 协议选路
# ---------------------------------------------------------------------------
PROTOCOL_CHAT = "chat"
PROTOCOL_ANTHROPIC = "anthropic"

#: 目录里的协议名 → 本网关的协议名。目录同时有「对象形态」（{type}）与
#: 「裸字符串」两种写法，两种都认。本网关只会发 chat 与 messages 两种线，
#: 目录若给出别的东西（`openai_responses`）就不该把它当 chat 发。
PROTOCOL_ALIASES = {
    "openai_chat_completions": PROTOCOL_CHAT,
    "chat": PROTOCOL_CHAT,
    "openai": PROTOCOL_CHAT,
    "anthropic_messages": PROTOCOL_ANTHROPIC,
    "anthropic": PROTOCOL_ANTHROPIC,
    "messages": PROTOCOL_ANTHROPIC,
}


def protocol_word(raw) -> str:
    """目录记录里的 `protocol` 归一成 `chat` / `anthropic`（认不出给空串）。"""
    said = raw
    if isinstance(raw, dict):
        said = raw.get("type")
    text = str(said or "").strip().lower()
    return PROTOCOL_ALIASES.get(text, "")


def protocol_by_name(model: str) -> str:
    """目录里没有这条记录时的兜底：Claude 系走 Anthropic，其余走 chat。"""
    return (PROTOCOL_ANTHROPIC if str(model or "").strip().lower().startswith("claude")
            else PROTOCOL_CHAT)


# ---------------------------------------------------------------------------
# 内置模型目录（目录接口不可达时的兜底；`fallback` 标记由调用方给出）
# ---------------------------------------------------------------------------
#: (id, contextWindow, maxOutput, 输入模态, protocol, reasoning, rate)
#:
#: `rate` 是厂商自己的积分倍率（目录里的 `rate_badge`），只用于展示；
#: 价格是人民币口径，不折算美元。
_FALLBACK_TABLE = (
    ("auto", 1000000, 393216, "text+image", "chat", True, 0.2),
    ("claude-opus-5-5", 1000000, 128000, "text+image", "anthropic", True, 1.37),
    ("claude-opus-5-5-kiro", 1000000, 128000, "text+image", "anthropic", True, 0.55),
    ("claude-sonnet-5-5", 1000000, 128000, "text+image", "anthropic", True, 0.93),
    ("claude-sonnet-5-5-kiro", 1000000, 128000, "text+image", "anthropic", True, 0.37),
    ("deepseek-v4-flash", 1000000, 393216, "text+image", "chat", True, 0.2),
    ("deepseek-v4-pro", 1000000, 393216, "text", "chat", True, 0.95),
    ("deepseek-v4.1-flash", 1000000, 393216, "text+image", "chat", True, 0.2),
    ("deepseek-v4.1-flash-economy", 1000000, 393216, "text+image", "chat", True, 0.12),
    ("gemini-4-argon", 1048576, 65536, "text+image+audio", "chat", True, 0.55),
    ("gemini-4-argon-official", 1048576, 65536, "text+image+audio", "chat", True, 3.28),
    ("glm-5.2", 1000000, 131072, "text", "chat", True, 2.57),
    ("glm-5.3", 1000000, 131072, "text", "chat", True, 2.57),
    ("glm-5.3-flash", 1000000, 131072, "text+image", "chat", True, 0.29),
    ("gpt-5.6-sol", 1050000, 128000, "text+image", "chat", True, 0.77),
    ("gpt-6-astra", 1050000, 128000, "text+image", "chat", True, 1.48),
    ("gpt-6-sol", 1050000, 128000, "text+image", "chat", True, 0.3),
    ("gpt-6.1-sol", 1050000, 128000, "text+image", "chat", True, 0.3),
    ("gpt-6.2-astra", 1050000, 128000, "text+image", "chat", True, 0.87),
    ("gpt-6.2-sol", 1050000, 128000, "text+image", "chat", True, 0.3),
)


def _kinds_of(cell: str) -> list[str]:
    """`"text+image"` → `["text", "image"]`；只认网关认识的几种模态。"""
    known = ("text", "image", "audio", "video")
    out = [kind for kind in str(cell or "").split("+") if kind in known]
    return out or ["text"]


def fallback_catalog() -> list[dict]:
    """内置目录（每次给新对象，调用方改动不会污染模块常量）。"""
    rows = []
    for model_id, context, max_out, kinds, protocol, reasoning, rate in _FALLBACK_TABLE:
        rows.append({
            "id": model_id,
            "name": model_id,
            "context": context,
            "max_output": max_out,
            "input": _kinds_of(kinds),
            "output": ["text"],
            "protocol": protocol,
            "reasoning": bool(reasoning),
            "rate": rate,
        })
    return rows


FALLBACK_IDS = frozenset(row[0] for row in _FALLBACK_TABLE)
