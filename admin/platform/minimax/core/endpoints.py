"""MiniMax Code 平台端点表。

## 上游摘要（2026-11-07 逆向官方客户端 3.1.6 + 本机凭证实测）

    对话     POST {agent}/mavis/api/v1/llm/v1/messages    Anthropic Messages 协议
             认证：Authorization: Bearer mmoat_...（OAuth access token）
             SSE 与非流式都是标准 Anthropic 事件（含 thinking / tool_use）
    用户     GET  {agent}/matrix/api/v1/user/profile      Bearer
    签到     GET  {agent}/minimax-cloud/api/v1/signin/status?timezone_id=Asia/Shanghai
             POST {agent}/minimax-cloud/api/v1/signin/claim?timezone_id=...
    积分明细 GET  {agent}/minimax-cloud/api/v1/credit/details
             余额口径 = Σ remaining_amount（签到积分 30 天有效，仅 MiniMax Code 可用）
    设备授权 POST {account}/oauth2/device/code   (PKCE S256, client_id=mcode-public)
    续签     POST {account}/oauth2/token         grant_type=refresh_token

凭证源（明文）：`~/.minimax/auth/prod/<env>/<region>/mcode-public/auth.json`
（records 值含 accessToken=mmoat_ / refreshToken=mmort_ / expiresAtMs）。

region 与 host 的对应（auth-state.json 的 region 字段同款取值）：
    cn -> agent.minimax.cn / account.minimax.cn
    en -> agent.minimax.io / account.minimax.io
"""
from __future__ import annotations

PLATFORM = "minimax"
#: 对外平台 id（单平台，区域走账号 domain）
PUBLIC = "minimax"

REGIONS = {
    "cn": {
        "label": "中国版",
        "agent": "https://agent.minimax.cn",
        "account": "https://account.minimax.cn",
        "timezone_id": "Asia/Shanghai",
    },
    "en": {
        "label": "国际版",
        "agent": "https://agent.minimax.io",
        "account": "https://account.minimax.io",
        "timezone_id": "America/New_York",
    },
}
DEFAULT_REGION = "cn"

#: 设备授权公共参数（官方 @mavis/oauth-core contracts.js 逆向）
OAUTH_CLIENT_ID = "mcode-public"
OAUTH_SCOPES = ["agent.default"]
OAUTH_AUDIENCE = "agent-backend"

#: 受管对话端点（Anthropic Messages 协议，Bearer 认证）
LLM_MESSAGES_PATH = "/mavis/api/v1/llm/v1/messages"
PROFILE_PATH = "/matrix/api/v1/user/profile"
SIGNIN_STATUS_PATH = "/minimax-cloud/api/v1/signin/status"
SIGNIN_CLAIM_PATH = "/minimax-cloud/api/v1/signin/claim"
SIGNIN_CONFIG_PATH = "/minimax-cloud/api/v1/signin/config"
CREDIT_DETAILS_PATH = "/minimax-cloud/api/v1/credit/details"


def normalize_region(value) -> str:
    """'cn'/'CN'/'中国' -> cn；'en'/'intl'/'io' -> en；默认 cn。"""
    raw = str(value or "").strip().lower()
    if raw in ("en", "intl", "io", "overseas", "国际", "国际版"):
        return "en"
    return "cn"


def conf_of(realm: str) -> dict:
    return REGIONS.get(normalize_region(realm)) or REGIONS[DEFAULT_REGION]


def agent_base(realm: str) -> str:
    return conf_of(realm)["agent"]


def account_base(realm: str) -> str:
    return conf_of(realm)["account"]


def auth_headers(token: str, realm: str = "cn") -> dict:
    """上游统一请求头（Bearer OAuth access token）。"""
    return {
        "Authorization": "Bearer " + str(token or ""),
        "Accept": "application/json",
        "User-Agent": "MiniMaxCode/3.1.6",
    }
