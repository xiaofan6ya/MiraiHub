"""Qoder HTTP 客户端：令牌交换/续期、用户资料、额度、模型目录、对话流。"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from . import endpoints
from .cosy import build_auth_headers, encode_body

logger = logging.getLogger("admin.platform.qoder.client")

DEFAULT_TIMEOUT = 30.0


#: 哪些 kind 要上报哨兵。
#:
#: 判据：**是不是「这个账号/这个平台不能用了」**。
#: 用户输错模型名（`model_unknown`）不该进 —— 那是调用方的问题，
#: 上报只会污染信号。排队（`queued`）同理。
SENTINEL_KINDS = frozenset({
    "auth",                 # 凭证失效 / 被吊销
    "quota_exceeded",       # 额度耗尽
    "signature_invalid",    # COSY 签名错（实现问题，全平台挂）
    "upstream_business",    # SSE 信封里的业务错误
    "network",              # 连续网络失败也算平台不可用
})


def note_sentinel(realm: str, account_id: int, kind: str, *,
                  code: str = "", status: int = 0,
                  detail: str = "", model: str = "") -> None:
    """向风控哨兵上报一条信号。

    **绝不抛异常**：哨兵坏了不能反过来影响转发。
    平台维度传落库值 `qoder`（两个区共用），因为熔断的对象是
    「这一个上游」而不是「某一个区」—— 国内/国际共用一套风控策略。
    """
    if str(kind or "") not in SENTINEL_KINDS:
        return
    try:
        from admin import sentinel

        sentinel.note_failure("qoder", int(account_id or 0), kind,
                              code=str(code or ""), status=int(status or 0),
                              detail=str(detail or "")[:200],
                              model=str(model or ""))
    except Exception:  # noqa: BLE001 - 哨兵绝不阻断转发
        logger.debug("Qoder 哨兵上报失败 kind=%s", kind, exc_info=True)


class UpstreamError(Exception):
    """上游错误。`kind` 用于上层决定是冷却、禁用还是换号。"""

    def __init__(self, message: str, *, status: int = 0, code: str = "",
                 kind: str = "http_error", detail: str = ""):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.kind = kind
        self.detail = detail

    def retryable(self) -> bool:
        if self.status in (408, 429):
            return True
        if self.status >= 500:
            return True
        # 排队态（10605）是可重试的，不是账号失效
        return self.code == "10605"

    def to_dict(self) -> dict:
        return {"message": self.message, "status": self.status,
                "code": self.code, "kind": self.kind,
                "detail": self.detail[:400]}


def _open(url: str, data: bytes | None, headers: dict,
          timeout: float, method: str):
    req = urllib.request.Request(url, data=data, method=method)
    for key, value in headers.items():
        req.add_header(str(key), str(value))
    if data:
        req.add_header("Content-Type", "application/json")
    return urllib.request.urlopen(req, timeout=timeout)


#: 业务码 -> 错误 kind（**区分「账号问题」与「实现问题」**）。
#:
#: `101` / `Signature invalid` 是 COSY 签名算错 —— 那是**网关实现**的问题，
#: 一出现就是全平台不可用，必须进哨兵。把它归到 `auth` 会误导人
#: 以为是账号过期，去重新登录一遍没用。
_CODE_KINDS = {
    "101": "signature_invalid",
    "signature invalid": "signature_invalid",
    "token_invalid": "auth",
    "10605": "queued",              # 排队中：可重试，不是故障
}


def _classify(status: int, code: str, message: str) -> str:
    """按状态码 + 业务码给出错误种类。"""
    lowered = "%s %s" % (code, message)
    lowered = lowered.lower()
    for marker, kind in _CODE_KINDS.items():
        if marker in lowered:
            return kind
    if status in (401, 403):
        return "auth"
    if status == 404:
        return "model_unknown"
    if status == 429:
        return "rate_limit"
    if status >= 500:
        return "upstream_error"
    return "http_error"


def _json_or_error(status: int, payload: bytes, action: str,
                   account_id: int = 0, realm: str = "") -> dict:
    text = payload.decode("utf-8", "replace")
    if status >= 400:
        try:
            data = json.loads(text)
        except ValueError:
            data = {}
        code = str(data.get("code") or data.get("errorCode") or status)
        message = str(data.get("message") or data.get("errorMessage") or text[:200])
        kind = _classify(status, code, message)
        note_sentinel(realm or "cn", account_id, kind, code=code,
                      status=status, detail=text)
        raise UpstreamError("%s失败（HTTP %d）" % (action, status),
                            status=status, code=code, kind=kind, detail=text)
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise UpstreamError("%s返回非 JSON" % action, status=status,
                            detail=text[:200]) from exc
    return data if isinstance(data, dict) else {"data": data}


def request_json(url: str, *, action: str, method: str = "GET",
                 body: dict | None = None, headers: dict | None = None,
                 timeout: float = DEFAULT_TIMEOUT,
                 account_id: int = 0, realm: str = "") -> dict:
    """普通 JSON 请求（openapi / sash 一族）。

    `account_id` / `realm` 只用于**哨兵归因**（哪条账号出问题），
    不影响请求本身。
    """
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    try:
        with _open(url, data, headers or {}, timeout, method) as resp:
            return _json_or_error(resp.status, resp.read(), action)
    except UpstreamError:
        raise
    except urllib.error.HTTPError as exc:
        raise _json_error(exc, action, account_id, realm) from exc
    except Exception as exc:  # noqa: BLE001
        note_sentinel(realm or "cn", account_id, "network",
                      detail=str(exc)[:200])
        raise UpstreamError("%s网络失败" % action, status=0, kind="network",
                            detail=str(exc)[:200]) from exc


def _json_error(exc: urllib.error.HTTPError, action: str,
                account_id: int = 0, realm: str = "") -> UpstreamError:
    payload = b""
    try:
        payload = exc.read()
    except Exception:  # noqa: BLE001
        pass
    try:
        return _json_or_error(exc.code, payload, action, account_id, realm)
    except UpstreamError as err:
        return err


# ---------------------------------------------------------------------------
# 令牌
# ---------------------------------------------------------------------------
def exchange_pat(pat: str, realm: str) -> dict:
    """PAT → 作业令牌。

    返回 `{token, refresh_token, expires_at, ...}`。
    这是**唯一**能凭空得到可用令牌的方式（设备流需要浏览器交互）。
    """
    conf = endpoints.REGIONS.get(realm) or endpoints.REGIONS["cn"]
    return request_json(
        conf["open_api"] + endpoints.EXCHANGE_PATH,
        action="PAT 换取令牌", method="POST",
        body={"personal_token": pat},
        headers=endpoints.open_api_headers(None))


def refresh_job_token(refresh_token: str, realm: str) -> dict:
    """作业令牌续期（`jrt-` → 新 `jt-`）。"""
    conf = endpoints.REGIONS.get(realm) or endpoints.REGIONS["cn"]
    return request_json(
        conf["open_api"] + endpoints.JOB_REFRESH_PATH,
        action="作业令牌续期", method="POST",
        body={"refresh_token": refresh_token},
        headers=endpoints.refresh_headers())


def refresh_device_token(refresh_token: str, realm: str) -> dict:
    """设备流令牌续期（`drt-` → 新 `dt-`）。

    与作业令牌端点**不可互换**：把 `drt-` 交给 jobToken/refresh、
    或把 `jrt-` 交给 deviceToken/refresh，上游都按「令牌无效」拒绝。
    """
    conf = endpoints.REGIONS.get(realm) or endpoints.REGIONS["cn"]
    return request_json(
        conf["open_api"] + endpoints.DEVICE_REFRESH_PATH,
        action="设备令牌续期", method="POST",
        body={"refresh_token": refresh_token},
        headers=endpoints.refresh_headers())


# ---------------------------------------------------------------------------
# 账号信息
# ---------------------------------------------------------------------------
def fetch_profile(token: str, realm: str) -> dict:
    conf = endpoints.REGIONS.get(realm) or endpoints.REGIONS["cn"]
    return request_json(conf["open_api"] + endpoints.USER_INFO_PATH,
                        action="用户信息查询",
                        headers=endpoints.open_api_headers(token))


def fetch_plan(token: str, realm: str) -> dict:
    conf = endpoints.REGIONS.get(realm) or endpoints.REGIONS["cn"]
    return request_json(conf["open_api"] + endpoints.PLAN_PATH,
                        action="套餐查询",
                        headers=endpoints.open_api_headers(token))


def fetch_usage(token: str, realm: str) -> dict:
    """额度用量。

    返回里有两个桶，**不能只看一个**：

        userQuota   套餐额度（Pro Trial 是 300）
        addOnQuota  资源包 / 签到发的额度（签到 100 落这里）
    """
    conf = endpoints.REGIONS.get(realm) or endpoints.REGIONS["cn"]
    return request_json(conf["open_api"] + endpoints.USAGE_PATH,
                        action="额度查询",
                        headers=endpoints.open_api_headers(token))


def fetch_campaigns(token: str, realm: str) -> dict:
    """活动列表（签到入口）。

    ⚠️ 不要用 legacy 的 `/sash/api/v1/me/daily-check-in/status`：
    两个地区都已停用（国际版 404、中国版恒 `DISABLED`）。
    """
    conf = endpoints.REGIONS.get(realm) or endpoints.REGIONS["cn"]
    return request_json(
        conf["open_api"] + endpoints.CAMPAIGNS_PATH + endpoints.CAMPAIGNS_QUERY,
        action="活动列表查询",
        headers=endpoints.sash_headers(token))


def claim_campaign(token: str, realm: str, campaign_id: str) -> dict:
    conf = endpoints.REGIONS.get(realm) or endpoints.REGIONS["cn"]
    return request_json(
        "%s%s/%s/claim" % (conf["open_api"], endpoints.CAMPAIGNS_PATH,
                           campaign_id),
        action="领取权益", method="POST", body={},
        headers=endpoints.sash_headers(token))


# ---------------------------------------------------------------------------
# 模型目录
# ---------------------------------------------------------------------------
def fetch_model_catalog(token: str, realm: str, *, uid: str, name: str = "",
                        email: str = "", machine_id: str = "") -> dict:
    """模型目录（需要 COSY 签名）。

    返回是**按场景分组**的字典：

        {"chat": [...], "developer": [...], "assistant": [...], ...}

    每个模型的关键字段：`key` / `display_name` / `format` /
    `is_reasoning` / `is_vl` / `max_input_tokens` / `price_factor`。
    `format` 目前全是 `openai`（所以协议转换成本极低）。
    """
    base = endpoints.inference_base(realm, token)
    url = base + endpoints.MODEL_LIST_PATH
    headers = build_auth_headers(url, uid=uid, token=token, name=name,
                                 email=email, machine_id=machine_id)
    return request_json(url, action="模型目录查询", headers=headers)


def iter_chat_stream(token: str, realm: str, *, uid: str, body: dict,
                     name: str = "", email: str = "",
                     machine_id: str = "", timeout: float = 300.0,
                     account_id: int = 0):
    """对话流（需要 COSY 签名 + 请求体编码）。

    产出**已拆信封**的内层 chunk（OpenAI `chat.completion.chunk`）。

    上游的信封：
        {"headers": {...}, "body": "<JSON 字符串>", "statusCodeValue": 200}

    ⚠️ 业务错误**不体现在 HTTP 状态码上**（永远 200），
    而在 `statusCodeValue` 里。所以必须在流内检查，否则会把
    「额度耗尽」当成「空回复」。
    """
    base = endpoints.inference_base(realm, token)
    url = base + endpoints.CHAT_PATH + "?Encode=1"
    encoded = encode_body(json.dumps(body, ensure_ascii=False).encode())
    headers = build_auth_headers(url, uid=uid, token=token, name=name,
                                 email=email, machine_id=machine_id,
                                 body=encoded)
    headers["Content-Type"] = "application/json"
    headers["Accept"] = "text/event-stream"

    try:
        resp = _open(url, encoded, headers, timeout, "POST")
    except urllib.error.HTTPError as exc:
        raise _json_error(exc, "对话请求", account_id, realm) from exc
    except Exception as exc:  # noqa: BLE001
        note_sentinel(realm, account_id, "network", detail=str(exc)[:200])
        raise UpstreamError("对话请求网络失败", status=0, kind="network",
                            detail=str(exc)[:200]) from exc

    with resp:
        for raw_line in resp:
            line = raw_line.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                envelope = json.loads(payload)
            except ValueError:
                continue
            if not isinstance(envelope, dict):
                continue

            status = envelope.get("statusCodeValue")
            inner = envelope.get("body")
            if isinstance(inner, str):
                try:
                    inner = json.loads(inner)
                except ValueError:
                    inner = None

            # 业务错误在信封里：转成带状态码的异常，让上层换号/冷却
            if status and int(status) != 200:
                message = ""
                if isinstance(inner, dict):
                    message = str(inner.get("message") or inner.get("msgInfo") or "")
                # 信封里的错误：HTTP 是 200，但业务失败 —— 必须上报，
                # 否则「额度耗尽」会被渲染成「空回复」，无人知道原因
                note_sentinel(realm, account_id, "upstream_business",
                              code=str(status), status=int(status),
                              detail=payload[:200])
                raise UpstreamError(
                    "对话失败：%s" % (message or "上游业务错误"),
                    status=int(status), code=str(status), kind="upstream_business",
                    detail=payload[:400])

            if isinstance(inner, dict):
                yield inner
