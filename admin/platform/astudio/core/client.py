"""AStudio 上游 HTTP 客户端：模型网关（Responses）/ 账号面（Cookie 会话）。

## 两个域的鉴权与错误语义完全不同

模型网关（model-gateway/v1）：
    Authorization: Bearer <modelBearerToken>，OpenAI 形态错误体
    （{"error": {message, type}}）。401/403 是凭据问题，429 限流，
    402/403 带「额度/积分」字样是额度耗尽。

账号面（agent.xfyun.cn/xingchen-studio/）：
    Cookie（ssoSessionId + account_id 必需，token 有则带）+ 必需身份头
    clientType / studioVersion。响应信封 {flag, code, desc, data}：
      * HTTP 401/403，或 code === 80000，或 desc 含「重新登录」→ 会话失效；
      * flag === false 或 code !== 0 → 业务错误。
    只发 Cookie 不发 clientType 会被拒（code 11001001）。

## message / detail 分工（安全约束，与其它平台同款）

`message` 给人看、不带上游原文；`detail` 给日志、已脱敏。上游 401 的
错误体会原样回显请求头里的 Bearer，拼进 message 就是凭证外泄。
"""
from __future__ import annotations

import json
import logging

import time
import urllib.parse

import httpx

from admin.platform.astudio.core import endpoints
from admin.platform.astudio.core.endpoints import (
    ACCOUNT_PATHS, CLIENT_TYPE, MODEL_MANAGER_URL, PUBLIC, STUDIO_API_BASE,
    STUDIO_VERSION)

logger = logging.getLogger("admin.platform.astudio.client")

#: 模型网关 read=600s：帧间隔上限而非总时长 —— 上游思考静默期不能被掐。
GATEWAY_TIMEOUT = httpx.Timeout(connect=15.0, read=600.0, write=60.0, pool=15.0)
#: 账号面都是短请求。
STUDIO_TIMEOUT = httpx.Timeout(connect=15.0, read=30.0, write=30.0, pool=15.0)


class UpstreamError(Exception):
    """上游业务/传输错误（带分类，供冷却与哨兵使用）。"""

    def __init__(self, message: str, *, status: int = 0, kind: str = "upstream",
                 code=None, payload=None, detail: str = ""):
        super().__init__(message)
        self.message = message
        self.status = int(status or 0)
        self.kind = kind
        self.code = code
        self.payload = payload
        self.detail = detail

    @property
    def retryable(self) -> bool:
        return self.kind in ("rate", "upstream")

    def to_dict(self) -> dict:
        return {"message": self.message, "status": self.status,
                "kind": self.kind, "code": self.code}


#: 「额度/积分用完」的体内关键词（中英各一组）。
_QUOTA_HINTS = ("credit", "quota", "insufficient", "balance", "plan limit",
                "limit reached", "allowance", "积分", "额度", "余额", "配额")


def _mentions_quota(low: str) -> bool:
    return any(hint in low for hint in _QUOTA_HINTS)


def _redact(text: str, limit: int = 400) -> str:
    """把可能承载凭证的片段打码（Bearer / token 字样）。"""
    import re
    text = re.sub(r"(Bearer\s+)\S+", r"\1***", str(text or ""))
    text = re.sub(r"(token[=:]\s*)\S+", r"\1***", text, flags=re.IGNORECASE)
    return text[:limit]


def _classify(status: int, text: str) -> str:
    """模型网关错误分类（OpenAI 形态错误体）。"""
    low = str(text or "").lower()
    if status in (402, 403, 429) and _mentions_quota(low):
        return "quota"
    if status in (401, 403):
        return "auth"
    if status == 402:
        return "quota"
    if status == 429:
        return "rate"
    if status == 404:
        return "model"
    if "authentication" in low or "invalid token" in low or "api key" in low:
        return "auth"
    if _mentions_quota(low):
        return "quota"
    if "rate limit" in low or "too many requests" in low:
        return "rate"
    if "model" in low and ("not found" in low or "不存在" in low):
        return "model"
    return "upstream"


def _gateway_error(status: int, text: str, tag: str) -> UpstreamError:
    """模型网关错误响应 → UpstreamError。"""
    payload = None
    message = ""
    try:
        payload = json.loads(text)
        err = (payload or {}).get("error")
        if isinstance(err, dict):
            message = str(err.get("message") or "")
    except ValueError:
        payload = None
    kind = _classify(status, text)
    if kind == "auth":
        shown = ("模型凭据无效或被吊销（上游 %d）：请在 AStudio 客户端重新登录后"
                 "「扫描本机」重新入库" % status)
    elif kind == "quota":
        shown = "账号额度/积分不足（上游 %d）" % status
    elif kind == "rate":
        shown = "上游限流，请稍后重试（429）"
    elif kind == "model":
        shown = "模型不存在或不可用（404）"
    elif status == 400 and message:
        shown = "上游拒绝请求（400）：%s" % message
    else:
        shown = "上游错误（HTTP %d）" % status
    return UpstreamError(shown, status=status, kind=kind,
                         payload=payload if isinstance(payload, dict) else None,
                         detail=_redact(text))


def _request_json(url: str, *, headers: dict, json_body=None, params=None,
                  timeout: httpx.Timeout, tag: str, follow_redirects: bool = False):
    """发一次 JSON 请求，返回 (status, text)。网络错误转 UpstreamError。"""
    method = "POST" if json_body is not None else "GET"
    try:
        from admin import http_shared
        with httpx.Client(follow_redirects=follow_redirects,
                          **http_shared.client_kwargs("astudio", timeout=timeout)) as client:  # P3：共享连接池 + 共享 TLS 上下文 + 建连 10s（cookie 仍按请求隔离）
            resp = client.request(method, url, headers=headers,
                                  json=json_body, params=params)
    except httpx.HTTPError as exc:
        raise UpstreamError("%s 网络错误（%s）" % (tag, type(exc).__name__),
                            kind="upstream", detail=_redact(str(exc))) from exc
    return resp.status_code, resp.text or ""


# ---------------------------------------------------------------------------
# 模型网关
# ---------------------------------------------------------------------------
def _gateway_headers(token: str, *, stream: bool = False) -> dict:
    headers = {
        "Authorization": "Bearer " + str(token or ""),
        "Accept": "text/event-stream" if stream else "application/json",
        "Content-Type": "application/json",
    }
    if stream:
        # 上游被要求压缩时可能把整段 SSE 压成一个 gzip member，直到回答
        # 结束才 flush —— 首字要等最后一个 token。禁用压缩保证真流式。
        headers["Accept-Encoding"] = "identity"
    return headers


def request_responses(token: str, body: dict, *, account_id=None) -> dict:
    """非流式模型调用：POST /model-gateway/v1/responses → Responses 对象。"""
    url = endpoints.responses_url()
    tag = "%s#%s" % (PUBLIC, account_id if account_id is not None else "-")
    status, text = _request_json(
        url, headers=_gateway_headers(token), json_body=body,
        timeout=GATEWAY_TIMEOUT, tag=tag)
    if status >= 400:
        raise _gateway_error(status, text, tag)
    if not text.strip():
        raise UpstreamError("%s 返回空响应（HTTP %d）" % (tag, status),
                            kind="upstream")
    try:
        payload = json.loads(text)
    except ValueError:
        raise UpstreamError("%s 返回非 JSON（HTTP %d）" % (tag, status),
                            kind="upstream", detail=_redact(text)) from None
    if not isinstance(payload, dict):
        raise UpstreamError("%s 响应不是对象" % tag, kind="upstream")
    return payload


def open_responses_stream(token: str, body: dict, *, account_id=None,
                          stats: dict | None = None):
    """打开模型网关 SSE 流（Responses 事件流，原样字节）。

    返回 httpx.Response（stream 模式）；HTTP 错误立即抛 UpstreamError。
    调用方负责 `close()`（用 `close_stream` 一并释放 client）。
    """
    url = endpoints.responses_url()
    tag = "%s#%s" % (PUBLIC, account_id if account_id is not None else "-")
    started = time.perf_counter()
    from admin import http_shared
    client = httpx.Client(**http_shared.client_kwargs("astudio", timeout=GATEWAY_TIMEOUT))  # P3：共享连接池 + 共享 TLS 上下文 + 建连 10s（cookie 仍按请求隔离）
    try:
        resp = client.send(client.build_request(
            "POST", url, headers=_gateway_headers(token, stream=True),
            json=body), stream=True)
    except httpx.HTTPError as exc:
        client.close()
        raise UpstreamError("%s 网络错误（%s）" % (tag, type(exc).__name__),
                            kind="upstream", detail=_redact(str(exc))) from exc
    if resp.status_code >= 400:
        try:
            resp.read()
        except Exception:  # noqa: BLE001 - 读不到错误体也要关连接
            pass
        error = _gateway_error(resp.status_code, resp.text or "", tag)
        resp.close()
        client.close()
        raise error
    if stats is not None:
        try:
            stats["ttfb_ms"] = int((time.perf_counter() - started) * 1000)
        except Exception:  # noqa: BLE001
            pass
    resp._astudio_client = client  # type: ignore[attr-defined]
    return resp


def close_stream(resp) -> None:
    try:
        resp.close()
    except Exception:  # noqa: BLE001
        pass
    client = getattr(resp, "_astudio_client", None)
    if client is not None:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# 账号面（Cookie 会话）
# ---------------------------------------------------------------------------
def build_cookie_header(session: dict) -> str:
    """网页会话 → Cookie 头（桌面端 buildAstronCookieHeader 同款）。

    `ssoSessionId` 写双份（ssoSessionId / sso_sessionid 两个名字），
    桌面端一直这么带；`account_id` 必需，`token` 有则带。
    """
    parts = []
    sso = str(session.get("sso_session_id") or session.get("ssoSessionId") or "").strip()
    if sso:
        parts.append("ssoSessionId=%s" % sso)
        parts.append("sso_sessionid=%s" % sso)
    account_id = str(session.get("account_id") or session.get("accountId") or "").strip()
    if account_id:
        parts.append("account_id=%s" % account_id)
    token = str(session.get("token") or "").strip()
    if token:
        parts.append("token=%s" % token)
    return "; ".join(parts)


def _studio_headers(session: dict, *, has_body: bool = False) -> dict:
    headers = {
        "Accept": "application/json",
        "clientType": CLIENT_TYPE,
        "studioVersion": STUDIO_VERSION,
        "Cookie": build_cookie_header(session),
    }
    if has_body:
        headers["Content-Type"] = "application/json"
    return headers


def _studio_call(path: str, session: dict, *, method: str = "GET", body=None,
                 operation: str = "账号接口", account_id=None) -> dict:
    """账号面一次调用：信封判定 + 错误分类。

    返回信封的 `data`。HTTP 401/403、code 80000、desc 含「重新登录」→
    kind="auth"（会话失效，需要重新登录）；flag false / code != 0 → 业务错误。
    """
    tag = "%s#%s" % (PUBLIC, account_id if account_id is not None else "-")
    url = urllib.parse.urljoin(STUDIO_API_BASE, path)
    status, text = _request_json(
        url, headers=_studio_headers(session, has_body=body is not None),
        json_body=body, timeout=STUDIO_TIMEOUT, tag=tag)
    if status in (401, 403):
        raise UpstreamError("AStudio 会话已失效（HTTP %d），请重新登录 AStudio"
                            % status, status=status, kind="auth",
                            detail=_redact(text))
    if not text.strip():
        raise UpstreamError("%s 返回空响应（HTTP %d）" % (tag, status),
                            status=status, kind="upstream")
    try:
        payload = json.loads(text)
    except ValueError:
        raise UpstreamError("%s 返回的不是 JSON（HTTP %d）" % (tag, status),
                            status=status, kind="upstream",
                            detail=_redact(text)) from None
    envelope = payload if isinstance(payload, dict) and not isinstance(payload, list) else {}
    code = envelope.get("code")
    desc = str(envelope.get("desc") or "")
    if status >= 400 or envelope.get("flag") is False \
            or (code is not None and code != 0):
        if code == 80000 or "重新登录" in desc:
            raise UpstreamError("AStudio 会话已失效，请重新登录 AStudio",
                                status=status, kind="auth", code=code,
                                detail=_redact(text))
        raise UpstreamError(
            "%s 失败（HTTP %d%s）" % (operation, status,
                                     "：%s" % desc if desc else ""),
            status=status, kind="upstream", code=code, detail=_redact(text))
    data = envelope.get("data")
    if isinstance(data, (dict, list)):
        return data
    return envelope


def exchange_model_token(session: dict, *, account_id=None) -> str:
    """网页会话 → 换发模型凭据（POST auth/bearer-token）。

    返回 modelBearerToken（JWT，约 3 年；换发后旧 token 仍可用）。
    """
    data = _studio_call(ACCOUNT_PATHS["exchange_token"], session, method="POST",
                        body={}, operation="换发模型凭据", account_id=account_id)
    token = str((data or {}).get("modelBearerToken") or "")
    if not token:
        raise UpstreamError("换发模型凭据失败：返回里没有 modelBearerToken",
                            kind="upstream")
    return token


def fetch_balance(session: dict, *, account_id=None) -> dict:
    """GET points/balance → {totalBalance, activityBalance, sparkTotalBalance, …}。"""
    data = _studio_call(ACCOUNT_PATHS["balance"], session,
                        operation="积分余额", account_id=account_id)
    return data if isinstance(data, dict) else {}


def fetch_membership(session: dict, *, account_id=None) -> dict:
    """GET membership/me → {planName, uid, endTime, …}。"""
    data = _studio_call(ACCOUNT_PATHS["membership"], session,
                        operation="会员信息", account_id=account_id)
    return data if isinstance(data, dict) else {}


def fetch_plan_config(session: dict, *, account_id=None) -> dict:
    """GET membership/plan-config → {dailyLoginEnabled, …}。"""
    data = _studio_call(ACCOUNT_PATHS["plan_config"], session,
                        operation="签到配置", account_id=account_id)
    return data if isinstance(data, dict) else {}


def fetch_ledger(session: dict, start: str, end: str, *, page: int = 1,
                 page_size: int = 50, account_id=None) -> list:
    """GET points/details → 获得流水 items。

    时间窗口按**北京时间**解释（startTime/endTime 格式 `YYYY-MM-DD HH:mm:ss`，
    服务端对格式敏感）。
    """
    query = urllib.parse.urlencode({
        "direction": "CREDIT",
        "startTime": start,
        "endTime": end,
        "pageNum": page,
        "pageSize": page_size,
    })
    data = _studio_call(ACCOUNT_PATHS["details"] + "?" + query, session,
                        operation="获得明细", account_id=account_id)
    items = data.get("items") if isinstance(data, dict) else None
    return items if isinstance(items, list) else []


def fetch_popups(session: dict, *, account_id=None) -> list:
    """GET client-popups/pending → 待领取弹窗列表。"""
    data = _studio_call(ACCOUNT_PATHS["popups_pending"], session,
                        operation="每日奖励弹窗", account_id=account_id)
    if isinstance(data, dict) and isinstance(data.get("popups"), list):
        return data["popups"]
    if isinstance(data, list):
        return data
    return []


def complete_popup(session: dict, popup: dict, *, account_id=None) -> dict:
    """POST client-popups/complete —— 桌面端「知道了」按钮同款写请求。"""
    data = _studio_call(
        ACCOUNT_PATHS["popups_complete"], session, method="POST",
        body={"popupId": popup.get("popupId"), "instanceKey": popup.get("instanceKey")},
        operation="每日奖励领取", account_id=account_id)
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# 模型目录
# ---------------------------------------------------------------------------
def fetch_catalog(token: str, *, account_id=None) -> list:
    """GET model-manager/models → 目录条目列表（不计费）。

    401/403 判 auth；非 2xx 判 upstream。目录形状异常（不是
    `data.models` 的列表、有条目缺 slug）同样抛错，由调用方回落静态表。
    """
    tag = "%s#%s" % (PUBLIC, account_id if account_id is not None else "-")
    try:
        from admin import http_shared

        with httpx.Client(**http_shared.client_kwargs("astudio", timeout=STUDIO_TIMEOUT)) as client:
            resp = client.get(MODEL_MANAGER_URL, headers={
                "Authorization": "Bearer " + str(token or ""),
                "Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise UpstreamError("%s 目录读取网络错误（%s）" % (tag, type(exc).__name__),
                            kind="upstream", detail=_redact(str(exc))) from exc
    if resp.status_code in (401, 403):
        raise UpstreamError("AStudio 拒绝了这份凭据（HTTP %d），请重新登录"
                            % resp.status_code, status=resp.status_code,
                            kind="auth", detail=_redact(resp.text or ""))
    if resp.status_code >= 400:
        raise UpstreamError("AStudio 模型目录读取失败（HTTP %d）"
                            % resp.status_code, status=resp.status_code,
                            kind="upstream", detail=_redact(resp.text or ""))
    try:
        payload = json.loads(resp.text or "")
    except ValueError:
        raise UpstreamError("AStudio 模型目录不是有效 JSON", kind="upstream")
    if not isinstance(payload, dict):
        raise UpstreamError("AStudio 模型目录格式异常", kind="upstream")
    data = payload.get("data")
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, list):
        raise UpstreamError("AStudio 模型目录格式异常（没有 data.models）",
                            kind="upstream")
    if any(not isinstance(m, dict) or not str(m.get("slug") or "").strip()
           for m in models):
        raise UpstreamError("AStudio 模型目录格式异常（有条目缺 slug）",
                            kind="upstream")
    return models
