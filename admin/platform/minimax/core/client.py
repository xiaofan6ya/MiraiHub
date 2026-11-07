"""MiniMax Code 上游 HTTP 客户端：对话（Anthropic 协议）/ 身份 / 签到 / 积分 / OAuth。

## 与 raccoon/loomy 客户端的关键差异

MiniMax 的对话上游是 **Anthropic Messages 协议**（含 SSE 事件流），不是
OpenAI chat.completions —— 所以这里只有「透传 + 状态码判错」，没有
chat.completions 形态的 `request_json`。流式响应由 gateway 的
`_forward_stream` 直接消费 `open_stream()` 的字节流。

## message / detail 分工（安全约束，与 raccoon 同款）

`message` 给人看、不带上游原文；`detail` 给日志、已脱敏。上游 401 的
错误体会原样回显请求头里的 Bearer，拼进 message 就是凭证外泄。
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import time
import urllib.parse

import httpx

from admin.platform.minimax.core import endpoints
from admin.platform.minimax.core.endpoints import account_base, agent_base, auth_headers

logger = logging.getLogger("admin.platform.minimax.client")

# read=600s：帧间隔上限而非总时长 —— 上游思考静默期不能被掐
TIMEOUT = httpx.Timeout(connect=15.0, read=600.0, write=60.0, pool=15.0)
OAUTH_TIMEOUT = httpx.Timeout(connect=15.0, read=30.0, write=30.0, pool=15.0)


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


def _classify(status: int, text: str) -> str:
    """按 HTTP 状态与错误体关键词分类（Anthropic error 形态）。"""
    if status in (401, 403):
        return "auth"
    if status == 402:
        return "quota"
    if status == 429:
        return "rate"
    if status == 404:
        return "model"
    low = str(text or "").lower()
    if "authentication" in low or "invalid token" in low or "api key" in low:
        return "auth"
    if "quota" in low or "insufficient" in low or "积分不足" in text:
        return "quota"
    if "rate limit" in low or "too many requests" in low:
        return "rate"
    if "model" in low and ("not found" in low or "不存在" in text):
        return "model"
    return "upstream"


def _redact(text: str, limit: int = 400) -> str:
    """把可能承载凭证的片段打码（Bearer / token= / mmoat_ 前缀）。"""
    import re
    text = re.sub(r"(Bearer\s+)\S+", r"\1***", str(text or ""))
    text = re.sub(r"(mmoat_|mmort_)\S+", r"\1***", text)
    return text[:limit]


def _error_from_anthropic(resp: httpx.Response, tag: str) -> UpstreamError:
    """上游错误响应 → UpstreamError（Anthropic error 形态优先）。"""
    text = resp.text or ""
    message = ""
    try:
        payload = json.loads(text)
        err = (payload or {}).get("error")
        if isinstance(err, dict):
            message = str(err.get("message") or "")
        elif isinstance(payload, dict):
            message = str(payload.get("message") or "")
    except ValueError:
        payload = None
    kind = _classify(resp.status_code, text)
    if kind == "auth":
        shown = ("凭证无效或已被轮换吊销（上游 401）：请在 MiniMax 客户端登录一次后"
                 "「扫描本机」重新入库，或用「授权登录」重新扫码 —— "
                 "轮换票一旦被其它端续签，旧 access/refresh 一并作废")
    elif kind == "quota":
        shown = "账号额度/积分不足（上游 %d）" % resp.status_code
    elif kind == "rate":
        shown = "上游限流，请稍后重试（429）"
    elif kind == "model":
        shown = "模型不存在或不可用（404）"
    elif resp.status_code == 400 and message:
        # 400 是请求参数问题：上游 message（如
        # `invalid params, messages must not be empty (2013)`）不含凭证，
        # 直接透出 —— 笼统的「上游错误（HTTP 400）」让调用方无从排查
        # （2026-11-07 实测教训）。
        shown = "上游拒绝请求（400）：%s" % message
    else:
        shown = "上游错误（HTTP %d）" % resp.status_code
    return UpstreamError(shown, status=resp.status_code, kind=kind,
                         payload=payload if isinstance(payload, dict) else None,
                         detail=_redact(text))


def request_json(method: str, url: str, *, token: str, realm: str = "cn",
                 json_body=None, params=None, account_id=None,
                 context: str = ""):
    """非流式 JSON 请求（agent 域，Bearer）。成功返回解析后的 JSON。"""
    headers = auth_headers(token, realm)
    if json_body is not None:
        headers["Content-Type"] = "application/json"
    tag = "minimax#%s" % (account_id if account_id is not None else "-")
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
            resp = client.request(method, url, headers=headers,
                                  json=json_body, params=params)
    except httpx.HTTPError as exc:
        raise UpstreamError("%s 网络错误（%s）" % (tag, type(exc).__name__),
                            kind="upstream", detail=_redact(str(exc))) from exc
    if resp.status_code >= 400:
        raise _error_from_anthropic(resp, tag)
    text = resp.text or ""
    if not text.strip():
        return None
    try:
        return json.loads(text)
    except ValueError:
        raise UpstreamError("%s 返回非 JSON（HTTP %d）" % (tag, resp.status_code),
                            kind="upstream",
                            detail=_redact(text)) from None


def open_stream(url: str, *, token: str, realm: str = "cn", json_body=None,
                account_id=None, stats: dict | None = None):
    """打开上游 SSE 流（Anthropic 事件流，原样字节）。

    返回 httpx.Response（stream 模式）；HTTP 错误立即抛 UpstreamError。
    调用方负责 `close()`。
    """
    headers = auth_headers(token, realm)
    headers["Content-Type"] = "application/json"
    headers["Accept"] = "text/event-stream"
    tag = "minimax#%s" % (account_id if account_id is not None else "-")
    started = time.perf_counter()
    client = httpx.Client(timeout=TIMEOUT, follow_redirects=True)
    try:
        resp = client.send(client.build_request(
            "POST", url, headers=headers, json=json_body), stream=True)
    except httpx.HTTPError as exc:
        client.close()
        raise UpstreamError("%s 网络错误（%s）" % (tag, type(exc).__name__),
                            kind="upstream", detail=_redact(str(exc))) from exc
    if resp.status_code >= 400:
        body = resp.read().decode("utf-8", "replace")
        resp.close()
        client.close()
        raise _error_from_anthropic(resp, tag)
    if stats is not None:
        try:
            stats["ttfb_ms"] = int((time.perf_counter() - started) * 1000)
        except Exception:  # noqa: BLE001 - 统计失败不影响转发
            pass
    # 把 client 挂在 response 上，调用方 close() 时一并释放
    resp._minimax_client = client  # type: ignore[attr-defined]
    return resp


def close_stream(resp) -> None:
    try:
        resp.close()
    except Exception:  # noqa: BLE001
        pass
    client = getattr(resp, "_minimax_client", None)
    if client is not None:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# 身份 / 签到 / 积分
# ---------------------------------------------------------------------------
def fetch_profile(token: str, realm: str = "cn", account_id=None) -> dict:
    """GET /matrix/api/v1/user/profile → {user_id, user_name, email, ...}。"""
    data = request_json("GET", agent_base(realm) + endpoints.PROFILE_PATH,
                        token=token, realm=realm, account_id=account_id,
                        context="用户信息")
    return data if isinstance(data, dict) else {}


def fetch_credit_details(token: str, realm: str = "cn", account_id=None) -> dict:
    """GET /minimax-cloud/api/v1/credit/details → 积分明细。

    余额口径（与官方「签到积分 30 天有效，仅 MiniMax Code 使用」一致）：
      * remain  = Σ remaining_amount
      * total   = Σ granted_amount（累计发放）
      * expire  = 明细里最近的 expire_at_ms（还有余额的）
    """
    data = request_json("GET", agent_base(realm) + endpoints.CREDIT_DETAILS_PATH,
                        token=token, realm=realm, account_id=account_id,
                        context="积分明细")
    if not isinstance(data, dict):
        raise UpstreamError("积分明细返回空结构（余额未知，保留旧快照）",
                            kind="upstream")
    details = data.get("details") if isinstance(data.get("details"), list) else []
    remain = total = 0.0
    nearest_expire = 0
    for row in details:
        if not isinstance(row, dict):
            continue
        try:
            granted = float(row.get("granted_amount") or 0)
            remaining = float(row.get("remaining_amount") or 0)
        except (TypeError, ValueError):
            continue
        total += granted
        remain += remaining
        if remaining > 0:
            exp = int(row.get("expire_at_ms") or 0)
            if exp and (not nearest_expire or exp < nearest_expire):
                nearest_expire = exp
    return {"remain": round(remain, 2), "total": round(total, 2),
            "expire_at_ms": nearest_expire, "details": details}


def signin_status(token: str, realm: str = "cn", account_id=None) -> dict:
    """GET signin/status → {days:[{day_no,points,status,is_today}], scene}。

    status 语义（官方 i18n）：1=未到 2=不可签 3=今日可签 4=已签。
    """
    data = request_json("GET", agent_base(realm) + endpoints.SIGNIN_STATUS_PATH,
                        token=token, realm=realm, account_id=account_id,
                        params={"timezone_id": endpoints.conf_of(realm)["timezone_id"]},
                        context="签到状态")
    return data if isinstance(data, dict) else {}


def signin_claim(token: str, realm: str = "cn", account_id=None) -> dict:
    """POST signin/claim → {claim_id, claim_result, points, panel}。"""
    data = request_json("POST", agent_base(realm) + endpoints.SIGNIN_CLAIM_PATH,
                        token=token, realm=realm, json_body={},
                        account_id=account_id,
                        params={"timezone_id": endpoints.conf_of(realm)["timezone_id"]},
                        context="每日签到")
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# OAuth 设备授权 / 续签（account.<region>，表单信封）
# ---------------------------------------------------------------------------
def _oauth_post(url: str, form: dict) -> dict:
    body = urllib.parse.urlencode(form).encode("utf-8")
    req_headers = {"Content-Type": "application/x-www-form-urlencoded",
                   "Accept": "application/json",
                   "User-Agent": "MiniMaxCode/3.1.6"}
    try:
        with httpx.Client(timeout=OAUTH_TIMEOUT, follow_redirects=True) as client:
            resp = client.post(url, content=body, headers=req_headers)
    except httpx.HTTPError as exc:
        raise UpstreamError("OAuth 网络错误（%s）" % type(exc).__name__,
                            kind="upstream", detail=_redact(str(exc))) from exc
    text = resp.text or ""
    try:
        payload = json.loads(text) if text.strip() else {}
    except ValueError:
        raise UpstreamError("OAuth 返回非 JSON（HTTP %d）" % resp.status_code,
                            status=resp.status_code,
                            detail=_redact(text)) from None
    if not isinstance(payload, dict):
        raise UpstreamError("OAuth 响应不是对象", status=resp.status_code)
    if resp.status_code >= 400 or payload.get("error"):
        err = str(payload.get("error") or "")
        # RFC 8628 设备流轮询状态不是错误：pending/slow_down 继续等
        if err in ("authorization_pending", "slow_down"):
            return {"status": err}
        if err in ("access_denied", "expired_token"):
            raise UpstreamError("设备授权被拒绝或已过期（%s）" % err,
                                status=resp.status_code, kind="auth",
                                detail=_redact(text))
        if payload.get("status") in ("pending", "slow_down", "denied", "expired"):
            return {"status": str(payload["status"])}
        raise UpstreamError("OAuth 请求失败（%s）" % (err or resp.status_code),
                            status=resp.status_code, kind="auth" if resp.status_code in (401, 403) else "upstream",
                            detail=_redact(text))
    return payload


def device_begin(realm: str = "cn") -> dict:
    """发起设备授权：POST /oauth2/device/code（PKCE S256）。

    返回 {device_code, user_code, verification_uri, verification_uri_complete,
          expires_in, interval, code_verifier, realm}。
    `code_verifier` 必须由调用方保存，poll 换 token 时要用。
    """
    verifier = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    payload = _oauth_post(account_base(realm) + "/oauth2/device/code", {
        "client_id": endpoints.OAUTH_CLIENT_ID,
        "scope": " ".join(endpoints.OAUTH_SCOPES),
        "audience": endpoints.OAUTH_AUDIENCE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    device_code = str(payload.get("device_code") or "")
    user_code = str(payload.get("user_code") or "")
    if not device_code or not user_code:
        raise UpstreamError("官方未返回 device_code/user_code", kind="upstream")
    return {
        "device_code": device_code,
        "user_code": user_code,
        "verification_uri": str(payload.get("verification_uri")
                                or (account_base(realm) + "/oauth-authorize")),
        "verification_uri_complete": str(payload.get("verification_uri_complete") or ""),
        "expires_in": int(payload.get("expires_in") or 300),
        "interval": max(1, int(payload.get("interval") or 3)),
        "code_verifier": verifier,
        "realm": endpoints.normalize_region(realm),
    }


def device_poll_token(realm: str, device_code: str, code_verifier: str) -> dict:
    """轮询设备授权 token：POST /oauth2/token（grant_type=device_code）。

    返回 {"status": "pending"/"slow_down"} 或
    {access_token, refresh_token, expires_in, ...}（成功）。

    上游是标准 RFC 8628：pending 时返回 **HTTP 400**
    `{"error":"authorization_pending"}`（实测 2026-11-07），slow_down 同形。
    `_oauth_post` 已把它归一成 `{"status": ...}` —— 两个名字都要认。
    """
    payload = _oauth_post(account_base(realm) + "/oauth2/token", {
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "device_code": device_code,
        "client_id": endpoints.OAUTH_CLIENT_ID,
        "code_verifier": code_verifier,
    })
    if payload.get("status") in ("pending", "slow_down", "authorization_pending"):
        return {"status": "pending" if payload["status"] == "authorization_pending"
                else payload["status"]}
    access = str(payload.get("access_token") or "")
    if not access:
        raise UpstreamError("授权未返回 access_token", kind="upstream")
    return _token_grant(payload)


def refresh_credentials(refresh_token: str, realm: str = "cn") -> dict:
    """续签：POST /oauth2/token（grant_type=refresh_token）。"""
    payload = _oauth_post(account_base(realm) + "/oauth2/token", {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": endpoints.OAUTH_CLIENT_ID,
        "scope": " ".join(endpoints.OAUTH_SCOPES),
        "audience": endpoints.OAUTH_AUDIENCE,
    })
    return _token_grant(payload)


def _token_grant(payload: dict) -> dict:
    access = str(payload.get("access_token") or "")
    refresh = str(payload.get("refresh_token") or "")
    if not access:
        raise UpstreamError("OAuth 响应缺 access_token", kind="upstream")
    return {"access_token": access,
            "refresh_token": refresh,
            "token_type": str(payload.get("token_type") or "Bearer"),
            "expires_in": int(payload.get("expires_in") or 0),
            "expires_at_ms": int(time.time() * 1000)
                             + int(payload.get("expires_in") or 0) * 1000}
