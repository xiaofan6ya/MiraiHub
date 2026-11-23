"""AStudio（讯飞 Astron Studio）平台端点表。

## 上游摘要（2026-11-13 逆向桌面端 + 网页后台实测）

模型面（Bearer modelBearerToken，OpenAI Responses 协议）：

    POST {model_gateway}/responses     模型调用（与桌面端写进内核的
                                       base_url / wire_api 一致）
    GET  {model_manager}/models        模型目录（读取不计费，按账号实时拉）

账号面（Cookie 会话鉴权，{flag, code, desc, data} 信封）：

    POST auth/bearer-token             换发模型凭据（JWT，实测约 3 年；
                                       换发后旧 token 仍可用）
    GET  points/balance                通用积分 / 星火积分余额
    GET  points/summary                积分汇总
    GET  points/details                获得流水（direction=CREDIT，
                                       startTime/endTime 按**北京时间**解释）
    GET  membership/me                 会员档位
    GET  membership/plan-config        活动配置（dailyLoginEnabled）
    GET  client-popups/pending         待领取弹窗
    POST client-popups/complete        领取 {popupId, instanceKey}

身份头：`clientType` 是必需头且校验合法枚举（21/22 通过、缺失或 99 被拒），
`studioVersion` 桌面端也带，实测值不参与校验。

## 模型目录

`model-manager/models` 按账号实时返回（含思考档位、限免标记），但会**滞后
于桌面端目录**：网关已经能调、目录还没列出来的模型由内置补位表补上；
目录一旦列出同名 slug 就改用目录那一行，目录标 `hidden` /
`supported_in_api: false` 视为下架，补位表不复活它。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

PLATFORM = "astudio"
#: 对外平台 id（单平台，无区域概念）。
PUBLIC = "astudio"

#: 模型网关基址（桌面端 resolveAstronRuntimeProfile 的 production 分支）。
MODEL_GATEWAY_BASE = "https://astronstudio-api-volces-prod.xf-yun.com/model-gateway/v1"
RESPONSES_PATH = "/responses"
MODEL_MANAGER_URL = "https://astronstudio-api-volces-prod.xf-yun.com/api/v1/model-manager/models"
#: 账号面（积分/签到/会员）基址。
STUDIO_API_BASE = "https://agent.xfyun.cn/xingchen-studio/"

#: 账号面身份头。clientType 是必需头且校验合法枚举（21/22 通过、99/缺失
#: 被拒，服务端回 code 11001001）；studioVersion 桌面端也带，不参与校验。
def _is_darwin() -> bool:
    import sys
    return sys.platform == "darwin"


CLIENT_TYPE = "22" if _is_darwin() else "21"
STUDIO_VERSION = "3.5.5"

ACCOUNT_PATHS = {
    "exchange_token": "auth/bearer-token",
    "balance": "points/balance",
    "summary": "points/summary",
    "details": "points/details",
    "membership": "membership/me",
    "plan_config": "membership/plan-config",
    "popups_pending": "client-popups/pending",
    "popups_complete": "client-popups/complete",
}

#: 当日「每日登录赠送」流水的业务字面量（服务端语义，不改写）。
GRANT_REASON = "每日登录赠送"
GRANT_OPERATION = "GRANT"
DAILY_POPUP_COMPONENT = "DAILY_REWARD_DIALOG"

#: 星火积分只圈 spark- 开头的模型（限免的不算）；其它模型扣通用积分。
SPARK_MODEL_PREFIX = "spark-"

#: 目录接口读不到时的启动表（2026-11-01 实测目录快照；实时目录覆盖）。
#: 字段 = 目录接口同款语义的静态快照。
BOOTSTRAP_MODELS = [
    {"slug": "astronclaw-auto", "display_name": "Auto", "context_window": 262144,
     "max_output_tokens": 32768, "input_modalities": ["text"], "free": False},
    {"slug": "spark-x2.6", "display_name": "Spark-X2.6", "context_window": 262144,
     "max_output_tokens": 32768, "input_modalities": ["text"], "free": False},
    {"slug": "spark-x2.6-4b", "display_name": "Spark-X2.6-Lite",
     "context_window": 1048576, "max_output_tokens": 110000,
     "input_modalities": ["text"], "free": True},
    {"slug": "spark-x2.5", "display_name": "Spark-X2.5", "context_window": 262144,
     "max_output_tokens": 32768, "input_modalities": ["text"], "free": False},
    {"slug": "xopglm52", "display_name": "GLM-5.2", "context_window": 1048576,
     "max_output_tokens": 32768, "input_modalities": ["text"], "free": False},
    {"slug": "xopdeepseekv4pro0813", "display_name": "DeepSeek-V4-Pro",
     "context_window": 1048576, "max_output_tokens": 32768,
     "input_modalities": ["text"], "free": False},
    {"slug": "xopdsv4flash0731in", "display_name": "DeepSeek-V4-Flash",
     "context_window": 1048576, "max_output_tokens": 32768,
     "input_modalities": ["text"], "free": False},
    {"slug": "xdeepseek-flash-in", "display_name": "DeepSeek-V4.1-Flash",
     "context_window": 1048576, "max_output_tokens": 100000,
     "input_modalities": ["text"], "free": False},
]

#: 目录接口滞后于桌面端目录时补位的模型（目录字段形状，见 README 侧说明）。
#: 2026-11-13 实测：xdeepseek-flash-in 直调 /responses 返 200，同一时刻
#: 目录只给 7 个。上游补齐后删掉对应条目即可。
EXTRA_CATALOG = [
    {
        "slug": "xdeepseek-flash-in",
        "display_name": "DeepSeek-V4.1-Flash",
        "context_window": 1048576,
        "max_output_tokens": 100000,
        "input_modalities": ["text"],
        "supported_in_api": True,
        "visibility": "list",
        "supported_reasoning_levels": [
            {"effort": "none", "description": "关闭思考"},
            {"effort": "high", "description": "高强度思考"},
            {"effort": "max", "description": "最大强度思考"},
        ],
    },
]

#: 网关侧可调模型全集（启动表 + 补位表；实时目录只可能收窄/增补，这里
#: 只用于「这个模型到底是不是本平台的」判定）。
MODEL_SLUGS = frozenset(m["slug"] for m in BOOTSTRAP_MODELS)
MODEL_SLUGS |= frozenset(m["slug"] for m in EXTRA_CATALOG)

#: 北京时间偏移（东八区，与网页后台的 points/details 窗口同口径）。
CST_OFFSET = timedelta(hours=8)
_CST_TZ = timezone(CST_OFFSET)


def _is_darwin() -> bool:
    import sys
    return sys.platform == "darwin"


def cst_now() -> datetime:
    """当前北京时间（墙钟）。"""
    return datetime.now(_CST_TZ)


def cst_stamp(dt: datetime) -> str:
    """北京时间 → `YYYY-MM-DD HH:mm:ss`（服务端对格式敏感）。"""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def cst_day_window(now: datetime | None = None):
    """当日获得流水的查询窗口（北京时间当天 00:00:00 → 现在）。

    服务端按北京时间解释时间参数，本地时区（比如 UTC 机器）算出来的
    窗口会把当天的 GRANT 行排到窗口外，签到误报 inactive。
    """
    now = now or cst_now()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return cst_stamp(start), cst_stamp(now)


def is_spark_model(model: str) -> bool:
    return str(model or "").startswith(SPARK_MODEL_PREFIX)


def model_gateway_base() -> str:
    return MODEL_GATEWAY_BASE


def responses_url() -> str:
    return MODEL_GATEWAY_BASE + RESPONSES_PATH
