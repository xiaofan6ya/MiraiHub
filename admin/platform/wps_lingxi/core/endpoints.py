"""WPS 灵犀（Lingxi）端点表与请求头。

## 上游事实（取自 `@kevinfx/magpie-wps-lingxi@0.3.3` 源码，非猜测）

    账号面 base : https://lingxi.wps.cn   （kdocs 账号回落 https://lingxi.kdocs.cn）
    对话 base   : 硬编码 [wps.cn, kdocs.cn] 顺序回落，成功后把实际域缓存进 run

    建会话  POST /api/aioffice/v1/cowork/sessions        → data.session_id
    领 run  POST /api/public/v1/credits/runs             → data.run_id / data.group_id
    对话    POST /api/public/v1/agent-runtime/chat/completions   SSE（自定义帧）
    资料    GET  /api/public/v1/account/profile          → data.id / data.name
    目录    GET  /api/aioffice/v1/sessions/plans         → data.plans / data.models
    额度    GET  /api/public/v1/credits/balance          → data.total_balance …
    任务    GET  /api/public/v1/tasks                    → data.tasks
    领取    POST /api/public/v1/tasks/{key}/claim

## 鉴权

只有 `wps_sid`（可选伴生 `uid` / `copilot_server_branch`）。Cookie 上再追加
三段 `x-kso-*` 伪装桌面客户端（安装包 1.3.31 的取证值）：

    x-kso-app-name=win-lingxi-desktop
    x-kso-app-version=1.3.31
    x-kso-device-id=<sha256("magpie-lingxi-device")>

## 请求头四套（别合并）

账号面 / agent 面 / 对话面（追加 x-cc-session-id / x-cc-run-id）/ 上报面。
"""
from __future__ import annotations

import hashlib
import os
import uuid

#: 落库平台标识 = 对外平台 id。
#:
#: 这里刻意**不用下划线**：控制台是按 `Account.platform` 拼管理接口前缀的
#: （`/api/${storage}/accounts/{id}/refresh`、`/api/${storage}?page_size=…`）。
#: 如果落库写 `wps_lingxi`、路由挂 `/api/wps-lingxi`，点「刷新余额」和
#: 「用量明细」都会 404 —— 而且 404 会伪装成「功能没做」。
#: 让 id 与 storage 相同（和 minimax / codearts 一致），前缀天然对齐。
PLATFORM = "wps-lingxi"
PUBLIC = "wps-lingxi"

BASE = "https://lingxi.wps.cn"
KDOCS_BASE = "https://lingxi.kdocs.cn"

#: 对话通路回落顺序。**账号面不吃这个顺序**，见 `account_bases()`。
CHAT_BASES = (BASE, KDOCS_BASE)
ACCOUNT_BASES = (BASE, KDOCS_BASE)

CLIENT_TYPE = "lingxi_claw"
CLIENT_LANG = "zh-CN"
DEFAULT_MODEL = "deepseek-v4.1-flash"

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# ---- 路径 ----
PROFILE_PATH = "/api/public/v1/account/profile"
PLANS_PATH = "/api/aioffice/v1/sessions/plans"
BALANCE_PATH = "/api/public/v1/credits/balance"
TASKS_PATH = "/api/public/v1/tasks"
CLAIM_PATH = "/api/public/v1/tasks/%s/claim"
SESSIONS_PATH = "/api/aioffice/v1/cowork/sessions"
RUNS_PATH = "/api/public/v1/credits/runs"
CHAT_PATH = "/api/public/v1/agent-runtime/chat/completions"
EXEC_STATUS_PATH = "/api/public/v1/sessions/%s/execution_status"
SESSION_LIST_PATH = "/api/public/v1/session_list"
#: 用量明细：智点收支流水（`since` / `before` 是 `YYYY-MM-DD`，双向游标）
TRANSACTIONS_PATH = "/api/public/v1/credits/transaction_summary"
#: 使用日志：该账号的 agent run 记录（上游侧的「调用明细」）
RUN_RECORDS_PATH = "/api/public/v1/agents/run_records"

#: 会话/run 缓存 TTL：上游按 run 计费，一个 run 内多次调用只扣一次智点。
RUN_TTL = 3600

#: 模型目录缓存（上游 plans 变化不频繁）。
CATALOG_TTL = 600

#: 上游对外 timeout：对话按 600s（推理模型可能很久），账号面 30s。
CHAT_TIMEOUT = 600.0
API_TIMEOUT = 30.0

#: 提示词字节预算（上游按 token 限量，超了只回「AI服务异常」不说原因）。
MAX_PROMPT_KB = 320
MAX_MESSAGE_KB = 48


def _kb(name: str, default: int, floor: int) -> int:
    try:
        value = int(os.getenv(name) or default)
    except (TypeError, ValueError):
        return default
    return value if value >= floor else floor


def max_prompt_bytes() -> int:
    return _kb("ADMIN_WPS_LINGXI_MAX_PROMPT_KB", MAX_PROMPT_KB, 32) * 1024


def max_message_bytes() -> int:
    return _kb("ADMIN_WPS_LINGXI_MAX_MESSAGE_KB", MAX_MESSAGE_KB, 4) * 1024


#: 桌面客户端伪装用的固定设备号（与插件同源，可复现同一值）。
DEVICE_ID = hashlib.sha256(b"magpie-lingxi-device").hexdigest()


def base_of(realm: str = "") -> str:
    """账号面 base，允许环境变量覆盖（联调/回归用）。"""
    value = (os.getenv("ADMIN_WPS_LINGXI_BASE") or "").strip()
    return (value or BASE).rstrip("/")


def chat_bases(realm: str = "") -> tuple[str, ...]:
    """对话通路的回落顺序。

    `ADMIN_WPS_LINGXI_CHAT_BASES` 可覆盖成逗号分隔列表（联调/回归用）。
    """
    value = (os.getenv("ADMIN_WPS_LINGXI_CHAT_BASES") or "").strip()
    if value:
        items = [item.strip().rstrip("/") for item in value.split(",") if item.strip()]
        if items:
            return tuple(items)
    return CHAT_BASES


def account_bases(realm: str = "") -> tuple[str, ...]:
    value = (os.getenv("ADMIN_WPS_LINGXI_BASE") or "").strip()
    if value:
        return (value.rstrip("/"), )
    return ACCOUNT_BASES


# ---------------------------------------------------------------------------
# Cookie 与请求头
# ---------------------------------------------------------------------------
def desktop_cookie(cookie: str) -> str:
    """在归一 Cookie 后追加三段 `x-kso-*`（桌面客户端伪装）。

    `ADMIN_WPS_LINGXI_NO_KSO=1` 时整段不追加（排障用：有些账号在
    不带 kso 时反而更稳，带上就走审核）。
    """
    if (os.getenv("ADMIN_WPS_LINGXI_NO_KSO") or "").strip() == "1":
        return cookie
    stamp = (
        "x-kso-app-name=win-lingxi-desktop; "
        "x-kso-app-version=1.3.31; "
        "x-kso-device-id=%s" % DEVICE_ID
    )
    base = (cookie or "").strip().rstrip(";")
    return "%s; %s" % (base, stamp) if base else stamp


def cookie_region(cookie: str) -> str:
    """从归一 Cookie 里挖出 `copilot_server_branch`（灰度标记）。"""
    for part in (cookie or "").split(";"):
        name, _, value = part.strip().partition("=")
        if name.strip() == "copilot_server_branch":
            return value.strip()
    return ""


def account_headers(cookie: str, base: str) -> dict:
    """账号面请求头（profile / balance / tasks / plans / claim）。"""
    return {
        "Cookie": desktop_cookie(cookie),
        "User-Agent": USER_AGENT,
        "Referer": base + "/",
        "Accept": "application/json",
    }


def agent_headers(cookie: str, base: str, extra: dict | None = None) -> dict:
    """agent 面请求头（建会话 / 领 run / 对话）。"""
    headers = {
        "Cookie": desktop_cookie(cookie),
        "Content-Type": "application/json",
        "X-Client-Type": CLIENT_TYPE,
        "X-Client-Lang": CLIENT_LANG,
        "X-Cc-Agent-Type": CLIENT_TYPE,
        "X-Request-Id": str(uuid.uuid4()),
        "Referer": base + "/",
        "User-Agent": USER_AGENT,
    }
    region = cookie_region(cookie)
    if region:
        headers["X-Cc-Region"] = region
    if extra:
        headers.update(extra)
    return headers


def plain_headers(cookie: str, base: str) -> dict:
    """执行状态上报面：只有 Cookie / Content-Type / User-Agent。"""
    return {
        "Cookie": desktop_cookie(cookie),
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }


# ---------------------------------------------------------------------------
# 内置模型快照（未登录 / 目录不可达时兜底，与客户端 1.3.31 一致）
# ---------------------------------------------------------------------------
DEFAULT_MODELS = (
    {"id": "auto", "name": "自动推荐 (Auto)", "context": 1048576,
     "multiplier": "1.0x", "reasoning": True, "vision": False},
    {"id": "deepseek-v4.1-flash", "name": "DeepSeek V4.1 Flash", "context": 1048576,
     "multiplier": "0.12x", "reasoning": True, "vision": True},
    {"id": "deepseek-v4-flash-0731", "name": "DeepSeek V4 Flash", "context": 1048576,
     "multiplier": "0.09x", "reasoning": True, "vision": False},
    {"id": "deepseek-v4-pro-0813", "name": "DeepSeek V4 Pro", "context": 1048576,
     "multiplier": "0.40x", "reasoning": True, "vision": False},
    {"id": "kimi-k3", "name": "Kimi-K3", "context": 1048576,
     "multiplier": "1.60x", "reasoning": True, "vision": True},
    {"id": "GLM-5.3", "name": "GLM-5.3", "context": 1048576,
     "multiplier": "0.75x", "reasoning": True, "vision": False},
    {"id": "GLM-5.3-Flash", "name": "GLM-5.3-Flash", "context": 1048576,
     "multiplier": "0.11x", "reasoning": True, "vision": True},
    {"id": "qwen3.8_max", "name": "Qwen3.8-Max", "context": 1048576,
     "multiplier": "0.80x", "reasoning": True, "vision": True},
    {"id": "qwen3.8_flash", "name": "Qwen3.8-Flash", "context": 1048576,
     "multiplier": "0.08x", "reasoning": True, "vision": True},
    {"id": "minimax_m3", "name": "MiniMax-M3", "context": 1048576,
     "multiplier": "0.20x", "reasoning": False, "vision": True},
    {"id": "doubao_seed_2_1_pro_260628", "name": "Doubao-Seed-2.1 Pro",
     "context": 262144, "multiplier": "0.50x", "reasoning": True, "vision": True},
    {"id": "mimo-v2.6-pro", "name": "Xiaomi MiMo-V2.6 Pro", "context": 1048576,
     "multiplier": "0.10x", "reasoning": False, "vision": True},
    {"id": "plan_pro", "name": "Pro 模式 (深度推理)", "context": 1048576,
     "multiplier": "1.0x", "reasoning": True, "vision": False},
    {"id": "plan_max", "name": "Max 模式 (复杂任务)", "context": 1048576,
     "multiplier": "1.0x", "reasoning": True, "vision": False},
)


def default_catalog() -> list[dict]:
    """内置快照 → 统一的模型列表形状。"""
    out = []
    for item in DEFAULT_MODELS:
        out.append({
            "id": item["id"],
            "name": item["name"],
            "description": "",
            "context_window": item["context"],
            "max_tokens": 65536,
            "multiplier": item["multiplier"],
            "reasoning": bool(item["reasoning"]),
            "vision": bool(item["vision"]),
            "default": item["id"] == DEFAULT_MODEL,
        })
    return out