"""Qoder 端点表与地区定义。

## 两个地区 = 两个平台（与 Trae 同一套做法）

```
qoder        中国版    openapi.qoder.com.cn    gateway.qoder.com.cn
qoder-intl   国际版    openapi.qoder.sh        api3.qoder.sh / api2.qoder.sh
```

存储上共用 `platform='qoder'` + `domain='cn'|'intl'`，
对外暴露成两个 PlatformSpec。

## 国际版的对话链路按令牌前缀分流（上游的硬约束）

| 前缀 | 含义 | 推理主机 |
|---|---|---|
| `dt-` | 设备流令牌 | `api3.qoder.sh` |
| `jt-` | PAT 换来的作业令牌 | `api2.qoder.sh` |

把 `jt-` 打到 api3 会被判 `Login expired`（403）。
中国版只有一台网关，两种令牌都收，不需要分流。

## 两族接口的鉴权口径完全不同

| 族 | 路径 | 鉴权 |
|---|---|---|
| openapi | `/api/v1/*`、`/api/v2/*` | `Bearer <token>` + `Cosy-Version` 等基础头 |
| sash | `/sash/api/v1/me/*` | `Bearer <token>` + `cosy-clienttype: 10`，**不打 COSY 签名** |
| 推理 | `/algo/*` | **COSY 签名**（见 `cosy.py`） |
"""
from __future__ import annotations

PLATFORM = "qoder"

#: 地区 key -> 端点集合
REGIONS = {
    "cn": {
        "label": "中国版",
        "open_api": "https://openapi.qoder.com.cn",
        "web_origin": "https://qoder.com.cn",
        "gateway": "https://gateway.qoder.com.cn",
        # 中国版只有一台网关，两种令牌都收
        "inference_intl_split": False,
    },
    "intl": {
        "label": "国际版",
        "open_api": "https://openapi.qoder.sh",
        "web_origin": "https://qoder.com",
        "gateway": "https://api3.qoder.sh",
        # 国际版按令牌前缀分流
        "inference_intl_split": True,
        "gateway_jt": "https://api2.qoder.sh",
    },
}

#: 落库区域 -> 对外平台 id
REALM_IDS = {"cn": "qoder", "intl": "qoder-intl"}

# ---------------------------------------------------------------------------
# openapi 一族（Bearer + 基础头）
# ---------------------------------------------------------------------------
USER_INFO_PATH = "/api/v1/userinfo"
USAGE_PATH = "/api/v2/quota/usage"
PLAN_PATH = "/api/v2/user/plan"
#: PAT → 作业令牌
EXCHANGE_PATH = "/api/v1/jobToken/exchange"
#: 作业令牌续期
JOB_REFRESH_PATH = "/api/v1/jobToken/refresh"
#: 设备流轮询 / 续期
DEVICE_POLL_PATH = "/api/v1/deviceToken/poll"
DEVICE_REFRESH_PATH = "/api/v1/deviceToken/refresh"
#: 设备授权页（在 web_origin 上）
DEVICE_LOGIN_PATH = "/device/selectAccounts"

# ---------------------------------------------------------------------------
# sash 一族（活动/签到；Bearer + cosy-clienttype: 10，无签名）
# ---------------------------------------------------------------------------
CAMPAIGNS_PATH = "/sash/api/v1/me/campaigns"
#: `forceRefresh=true` 让上游跳过缓存给实时 claimStatus
CAMPAIGNS_QUERY = "?forceRefresh=true"
#: `cosy-clienttype` 的值：**桌面端形态**。
#: 实测同一个账号用 5（IDE 形态）会返回 `showCampaign:false` + 空活动列表，
#: 换成 10 才给出完整活动包（含可领取的签到活动）。
SASH_CLIENT_TYPE = "10"
SASH_USER_AGENT = "Qoder"


def web_origin(realm: str) -> str:
    """门户主机（人看的页面）：设备授权链接挂在它上面。

    与 `open_api` **是两台不同的主机** ——
    门户负责「人点链接」，API 负责「轮询换 token」。
    """
    conf = REGIONS.get(realm or "cn") or REGIONS["cn"]
    return str(conf.get("web_origin") or "https://qoder.com.cn").rstrip("/")


def open_api(realm: str) -> str:
    """OpenAPI 主机（设备流轮询、token 续期、userinfo）。"""
    conf = REGIONS.get(realm or "cn") or REGIONS["cn"]
    return str(conf.get("open_api") or "https://openapi.qoder.com.cn").rstrip("/")


# ---------------------------------------------------------------------------
# 推理一族（COSY 签名）
# ---------------------------------------------------------------------------
MODEL_LIST_PATH = "/algo/api/v2/model/list"
CHAT_PATH = "/algo/api/v2/service/pro/sse/agent_chat_generation"


# ---------------------------------------------------------------------------
# 头构造
# ---------------------------------------------------------------------------
def open_api_headers(token: str | None = None) -> dict:
    """openapi 一族的请求头。"""
    headers = {
        "Cosy-Version": "1.0.1",
        "Cosy-ClientType": "5",
        "User-Agent": "qoder-local-proxy",
        "Accept": "application/json",
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    return headers


def sash_headers(token: str) -> dict:
    """活动/签到一族的请求头（**最小集、无签名**）。

    小写头名照抄抓包结果 —— HTTP 头名大小写不敏感，但保持一致便于对拍。
    """
    return {
        "authorization": "Bearer " + token,
        "cosy-clienttype": SASH_CLIENT_TYPE,
        "accept": "application/json",
        "accept-language": "zh-CN",
        "user-agent": SASH_USER_AGENT,
    }


def refresh_headers() -> dict:
    """续期请求的头：**不带签名、不带 Authorization**。

    两条续期路径的凭据都在请求体里（`{"refresh_token": "…"}`），
    带旧令牌没有意义。
    """
    return {"User-Agent": "qoder-local-proxy"}


def inference_base(realm: str, token: str) -> str:
    """**对话链路**实际使用的推理基址。

    国际版有两台：`api3` 认设备流令牌（`dt-`），`api2` 认作业令牌（`jt-`）。
    打错会被判 `Login expired`（403），而错误信息不会告诉你是主机选错了。
    """
    conf = REGIONS.get(realm or "cn") or REGIONS["cn"]
    if conf.get("inference_intl_split") and token.startswith("jt-"):
        return str(conf["gateway_jt"]).rstrip("/")
    return str(conf["gateway"]).rstrip("/")
