"""Freebuff 端点表与内置模型快照。

## 上游事实（实测 2026-11-07）

    Base     : https://freebuff.com/api/v1/paid
    模型目录 : GET  /api/v1/paid/models        → 200，**不需要鉴权**（280 个模型）
    对话     : POST /api/v1/paid/chat/completions  → 401 invalid_api_key（端点存在）

* 只有 `/models` 与 `/chat/completions` 两个端点：`/usage`、`/me`、`/credits`、
  `/balance`、`/health`、`/billing`、`/models/{id}` 全部 404 且返回 HTML
  （说明它们根本不存在，不是权限问题）—— 所以**没有余额接口，也没有
  「校验 key 是否有效」的轻量端点**。
* 目录里每个模型只有 `capabilities.{chat,streaming,toolCalls}`，
  没有名字、上下文长度、输出上限。280 个模型里 **只有 `deepseek-v4.1-flash`
  的 `streaming` 是 true**，其余 279 个是 false —— 这决定了本平台的流式策略
  （见 `gateway.py`：非流式模型由本地合成 SSE 帧）。

## 鉴权

Developer API key（形如 `fb_...`），放在 `Authorization: Bearer <key>`。
**Freebuff CLI/Desktop 的会话 token 是无用凭据**（UUID 形状）：免费额度
只给它自己那个经过校验的客户端，任何第三方程序都会被 `410
freebuff_update_required` 拒掉。所以本平台在**本地**就把它拦下来并给出
可操作的提示，不浪费一次上游请求。
"""
from __future__ import annotations

import os
import re

PLATFORM = "freebuff"
PUBLIC = "freebuff"

DEFAULT_BASE = "https://freebuff.com/api/v1/paid"

#: 账户页（授权登录用：登录后这里能拿到 Developer API key）。
ACCOUNT_URL = "https://freebuff.com/account/api"

MODELS_PATH = "/models"
CHAT_PATH = "/chat/completions"

#: 照抄插件的 UA：上游按它识别调用方，实测这个 UA 下 401 语义正常。
USER_AGENT = "magpie-freebuff-plugin/1.1.0"

#: Freebuff CLI/Desktop 的会话 token 是 UUID 形状，不能用来调 API。
SESSION_TOKEN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

SESSION_TOKEN_MESSAGE = (
    "这是一个 Freebuff 的会话 token（CLI/Desktop 登录态），不是 Developer API key。"
    "Freebuff 把免费额度限定在它自己的客户端上，会话 token 无法驱动外部程序。"
    "请到 https://freebuff.com/account/api 创建 Developer API key 再导入。")

DEFAULT_MODEL = "deepseek-v4.1-flash"

#: 目录/对话 timeout。
API_TIMEOUT = 30.0
CHAT_TIMEOUT = 600.0

#: 模型目录缓存（目录不需要鉴权，拉取便宜，但没必要每请求一次）。
CATALOG_TTL = 600

#: 目录里 `streaming=false` 的模型占 279/280 —— 本地合成 SSE 帧时才用。
SSE_CHUNK = 48


def base_url() -> str:
    value = (os.getenv("ADMIN_FREEBUFF_BASE") or "").strip()
    return (value or DEFAULT_BASE).rstrip("/")


def is_session_token(key: str) -> bool:
    return bool(SESSION_TOKEN.match(str(key or "").strip()))


def normalize_key(raw: str) -> str:
    """把用户粘进来的各种形状归一成一个 key。

    接受：裸 key / `Bearer <key>` / `Authorization: Bearer <key>` /
    `api_key=<key>` / 外层引号。
    """
    text = str(raw or "").strip().strip('"').strip("'").strip()
    if text.lower().startswith("authorization:"):
        text = text.split(":", 1)[1].strip()
    if text.lower().startswith("bearer "):
        text = text[7:].strip()
    if "=" in text and " " not in text and ";" not in text:
        name, _, value = text.partition("=")
        if name.strip().lower() in ("api_key", "apikey", "key", "token"):
            text = value.strip()
    return text


def headers_of(key: str = "") -> dict:
    """请求头。

    `key` 为空时也照发（`/models` 不需要鉴权；上游对空 Authorization
    的表现与不带一样）。
    """
    headers = {
        "accept": "application/json",
        "user-agent": USER_AGENT,
    }
    if key:
        headers["authorization"] = "Bearer " + key
    return headers


def chat_headers(key: str) -> dict:
    headers = headers_of(key)
    headers["content-type"] = "application/json"
    return headers


# ---------------------------------------------------------------------------
# 内置模型快照（插件 `DEFAULT_MODELS` 的 17 条，字段取自插件构造逻辑）
#
# `reasoning` / `vision` 是插件里 `buildModel` 显式传值或正则推出来的**结果**，
# 这里直接写结果，不再跑正则（避免 `solar-pro4` 命中 `-pro` 这类隐式行为
# 在别处被误改）。
# ---------------------------------------------------------------------------
DEFAULT_MODELS = (
    {"id": "deepseek-v4.1-flash", "name": "DeepSeek V4.1 Flash",
     "context": 1048576, "output": 32768, "reasoning": True, "vision": False},
    {"id": "deepseek-v4-flash", "name": "DeepSeek V4 Flash",
     "context": 1048576, "output": 32768, "reasoning": True, "vision": False},
    {"id": "deepseek-v4-pro", "name": "DeepSeek V4 Pro",
     "context": 1048576, "output": 32768, "reasoning": True, "vision": False},
    {"id": "glm-5.3-flash", "name": "GLM 5.3 Flash",
     "context": 1000000, "output": 32768, "reasoning": True, "vision": False},
    {"id": "mimo-v2.6-flash", "name": "MiMo v2.6 Flash",
     "context": 131072, "output": 16384, "reasoning": True, "vision": True},
    {"id": "mimo-v2.6-pro", "name": "MiMo 2.6 Pro",
     "context": 131072, "output": 16384, "reasoning": True, "vision": True},
    {"id": "solar-mini4", "name": "Solar Mini 4",
     "context": 524288, "output": 16384, "reasoning": True, "vision": False},
    {"id": "solar-pro4", "name": "Solar Pro 4",
     "context": 524288, "output": 16384, "reasoning": True, "vision": False},
    {"id": "gpt-6-luna", "name": "GPT-6 Luna",
     "context": 1000000, "output": 32768, "reasoning": True, "vision": True},
    {"id": "gpt-6.1-sol", "name": "GPT-6.1 Sol",
     "context": 1000000, "output": 32768, "reasoning": True, "vision": True},
    {"id": "claude-sonnet-5-5", "name": "Claude Sonnet 5.5",
     "context": 200000, "output": 8192, "reasoning": False, "vision": True},
    {"id": "claude-opus-5-5", "name": "Claude Opus 5.5",
     "context": 200000, "output": 8192, "reasoning": False, "vision": True},
    {"id": "claude-haiku-5-5", "name": "Claude Haiku 5.5",
     "context": 200000, "output": 8192, "reasoning": False, "vision": True},
    {"id": "gemini-3.8-flash", "name": "Gemini 3.8 Flash",
     "context": 1000000, "output": 32768, "reasoning": True, "vision": True},
    {"id": "gemini-4-argon", "name": "Gemini 4 Argon",
     "context": 1000000, "output": 32768, "reasoning": True, "vision": True},
    {"id": "minimax-m3", "name": "MiniMax M3",
     "context": 524288, "output": 32768, "reasoning": False, "vision": False},
    {"id": "qwen3.8-max", "name": "Qwen 3.8 Max",
     "context": 131072, "output": 16384, "reasoning": True, "vision": True},
)

_TABLE = {item["id"]: item for item in DEFAULT_MODELS}

#: 插件里 `buildModel` 的推断规则（目录条目缺字段时才用）。
_REASONING_RE = re.compile(r"-(flash|pro|argon|sol|r1)", re.I)
_VISION_RE = re.compile(r"-(luna|sol|argon|flash|vl)", re.I)


def infer_capabilities(model_id: str) -> dict:
    value = str(model_id or "")
    return {
        "reasoning": bool(_REASONING_RE.search(value)),
        "vision": bool(_VISION_RE.search(value)),
    }


def known_model(model_id: str) -> dict | None:
    return _TABLE.get(str(model_id or ""))


def default_catalog() -> list[dict]:
    """内置快照 → 统一模型列表形状。"""
    out = []
    for item in DEFAULT_MODELS:
        out.append({
            "id": item["id"],
            "name": item["name"],
            "description": "",
            "context_window": item["context"],
            "max_tokens": item["output"],
            "reasoning": bool(item["reasoning"]),
            "vision": bool(item["vision"]),
            "toolcall": True,
            # 内置表里只有 deepseek-v4.1-flash 在上游被标记为支持流式，
            # 其余靠 gateway 本地合成帧（推断方式与实测一致）。
            "streaming": item["id"] == DEFAULT_MODEL,
            "default": item["id"] == DEFAULT_MODEL,
        })
    return out