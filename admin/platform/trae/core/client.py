"""Trae 上游协议层（国内版 / 国际版）。

复刻自 trae2api-hub 的 `trae_client.py`，只保留网关真正需要的能力：

    REALMS                        双区主机 / 客户端头常量
    UpstreamError                 统一上游错误
    auth_headers()                鉴权头 + 设备指纹头
    build_chat_body()             OpenAI messages -> llm_raw_chat 请求体
    open_chat()                   流式 POST，返回原始响应
    iter_events()                 逐帧解析 SSE
    refresh_account()             ExchangeToken 轮换刷新
    fetch_model_list()            model_list
    fetch_entitlement()           权益 / 付费状态（额度）

**刻意复刻、不去重写的地方**（这些是踩出来的事实，改错就是 400/4015）：
  - `messages[].content` 必须是 parts 数组，传字符串直接 4001；
  - 服务端永远回 SSE，`stream:false` 也一样，聚合由上层负责；
  - 国际版 raw_chat 用 `Authorization: Bearer`，国内版用 `Cloud-IDE-JWT`；
  - 国际版必须带当前客户端版本头，否则 4015；
  - 国际版 `X-Device-Id` 必须是该账号登录设备的真实 holderId，**宁可不带**；
  - 国际版 SG 账号打 US 主机会回 1001，主机按账号落点排序。

传输层用标准库 urllib：与本仓库其它上游调用（httpx）不冲突，且能原样复用
hub 里已验证的 header / 超时 / 代理语义。
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid

from admin.platform.trae.core import fingerprint

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
APP_ID = "6eefa01c-1036-4c7e-9ca5-d891f63bfcd8"
PLUGIN_CHANNEL = "icube-ai"
IDE_VERSION_CODE = "20260212"
CLIENT_ID = "ono9krqynydwx5"
CLIENT_SECRET = "-"

PATH_CHAT = "/api/ide/v1/llm_raw_chat"
PATH_MODEL_LIST = "/api/ide/v1/model_list"
PATH_SESSION_USAGE = "/api/v1/commercial/get_session_usage"
PATH_EXCHANGE_TOKEN = "/cloudide/api/v3/trae/oauth/ExchangeToken"
PATH_ENT_USAGE = "/trae/api/v1/pay/ide_user_ent_usage"
PATH_ENT_USAGE_V2 = "/trae/api/v2/pay/ide_user_ent_usage"
PATH_PAY_STATUS = "/trae/api/v1/pay/ide_user_pay_status"

DEFAULT_TIMEOUT = 300.0

#: 双区常量。改动任何一项都属于协议变更，必须先用真实账号回归。
REALMS = {
    "cn": {
        "key": "cn",
        "label": "国内版",
        # api.trae.com.cn 只承接 CloudIDE/OAuth/pay；/api/ide/v1/* 打过去是 404。
        "core": "https://trae-api-cn.mchost.guru",
        "core_fallbacks": (),
        "account": "https://api.trae.com.cn",
        "pay": "https://api.trae.com.cn",
        # 签到/活动接口（`/trae/api/v2/ug/*`）只注册在 api.trae.cn 上；
        # 打 api.trae.com.cn 会 404（trae2api-hub 实测 2026-10-19）。
        "ug": "https://api.trae.cn",
        "ua": "Trae CN/3.4.3",
        "version_code": IDE_VERSION_CODE,
        "region": "CN",
        "auth_scheme": "Cloud-IDE-JWT",
    },
    "intl": {
        "key": "intl",
        "label": "国际版",
        # 主机按账号落点选：SG 号打 .traeapi.us 会稳定 1001。
        "core": "https://core-normal.trae.ai",
        "core_fallbacks": ("https://coresg-normal.trae.ai",
                           "https://core-normal.traeapi.us"),
        "account": "https://grow-normal.trae.ai",
        "pay": "https://grow-normal.trae.ai",
        # 国际版没有签到（上游返回业务错误）。保留同主机，
        # 让 `checkin_status` 如实抛错而不是伪造状态。
        "ug": "https://grow-normal.trae.ai",
        "ua": "Trae/3.6.2",
        "version_code": IDE_VERSION_CODE,
        "region": "US",
        "auth_scheme": "Bearer",
    },
}

#: 上游业务码 -> (http_status, kind)。kind 决定网关「换号 / 不重试 / 冷却」。
ERROR_MAP = {
    1001: (401, "auth"),              # 鉴权失败 -> 刷新 token / 换号
    4001: (400, "invalid_request"),   # 参数非法 -> 直接回客户端，不重试
    3003: (502, "prompt_pipeline"),
    4015: (403, "risk_control"),      # 账号/出口风控 -> 冷却换号
    4019: (403, "risk_control"),
    4008: (429, "quota_exceeded"),    # 免费额度用尽，不是风控
    4023: (400, "model_unknown"),     # 模型未知 -> 用 model_list 的 name
}

#: 失败后可换号重试的 kind（其余属于请求本身有问题，换号没意义）。
RETRYABLE_KINDS = frozenset({"auth", "risk_control", "quota_exceeded",
                             "upstream_error", "idle_timeout", "network"})

#: 需要冷却账号的 kind。
COOL_KINDS = frozenset({"auth", "risk_control", "quota_exceeded"})


def normalize_realm(value) -> str:
    """'CN'/'china'/'国内版' -> 'cn'；其余一律 intl（双区只有两种）。"""
    raw = str(value or "").strip().lower()
    if raw in ("cn", "china", "zh", "国内", "国内版", "中国"):
        return "cn"
    return "intl"


def realm_config(realm) -> dict:
    return REALMS[normalize_realm(realm)]


# ---------------------------------------------------------------------------
# 错误
# ---------------------------------------------------------------------------
class UpstreamError(Exception):
    """上游统一错误：code 可能是业务码(int)或本层字符串码。"""

    def __init__(self, code, message="", status=None, *, kind="", detail=""):
        self.code = code
        self.message = str(message or "")
        self.status = status
        self.kind = kind or classify(code, self.message)[1]
        self.detail = detail or self.message
        super().__init__("%s %s" % (code, self.message))

    def retryable(self) -> bool:
        return self.kind in RETRYABLE_KINDS

    def cool(self) -> bool:
        return self.kind in COOL_KINDS

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message,
                "status": self.status, "kind": self.kind}


def classify(code, message="") -> tuple[int, str]:
    """业务码 -> (http_status, kind)。未知码按 502 处理。"""
    try:
        code_i = int(code)
    except (TypeError, ValueError):
        code_i = 0
    status, kind = ERROR_MAP.get(code_i, (502, "upstream_error"))
    return status, kind


def error_from_code(code, message="", error="") -> UpstreamError:
    status, kind = classify(code, message)
    text = str(message or "").strip() or str(error or "").strip()
    return UpstreamError(code, text or "(上游未返回说明)", status, kind=kind,
                         detail="trae %s %s: %s" % (code or "?", kind, text[:300]))


# ---------------------------------------------------------------------------
# 账号字段读取（账号池行 / 裸 dict 都支持）
# ---------------------------------------------------------------------------
def as_mapping(account) -> dict:
    """把账号对象归一成 dict（不修改入参）。

    账号在库里是 `accounts.auth_json` 文本，取出来反序列化后就是 dict；
    测试与诊断可能直接传 dict，因此这里两种都收。
    """
    if account is None:
        return {}
    if isinstance(account, dict):
        return dict(account)
    if isinstance(account, str):
        try:
            parsed = json.loads(account)
        except ValueError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    attrs = getattr(account, "__dict__", None)
    return {k: v for k, v in attrs.items() if not k.startswith("_")} if isinstance(attrs, dict) else {}


def token_of(account) -> str:
    data = as_mapping(account)
    for key in ("token", "access_token", "accessToken", "ide_token"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _blank(value) -> bool:
    """安全判空：**不能**用 `value in (None, "")`。

    `in` 会对 value 做哈希，value 是 dict/list 时直接抛
    `unhashable type: 'dict'`。上游把某些字段包成对象时就会踩到。
    """
    if value is None or value is False:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (int, float)):
        return value == 0
    if isinstance(value, (dict, list, tuple, set)):
        return not value
    return False


def uid_of(account) -> str:
    data = as_mapping(account)
    for key in ("uid", "userId", "user_id"):
        value = data.get(key)
        if not _blank(value):
            return str(value)
    inner = data.get("account")
    if isinstance(inner, dict):
        for key in ("userId", "uid"):
            if not _blank(inner.get(key)):
                return str(inner[key])
    return ""


def realm_of(account) -> str:
    data = as_mapping(account)
    explicit = str(data.get("realm") or "").strip().lower()
    if explicit in ("cn", "intl"):
        return explicit
    host = str(data.get("host") or "").lower()
    if "trae.com.cn" in host or "mchost.guru" in host:
        return "cn"
    region = data.get("user_region") or data.get("userRegion")
    if isinstance(region, dict):
        if str(region.get("region") or "").upper() == "CN":
            return "cn"
        if str(region.get("_aiRegion") or "").upper() == "CN":
            return "cn"
    inner = data.get("account")
    if isinstance(inner, dict):
        if str(inner.get("storeRegion") or "").upper().startswith("CN"):
            return "cn"
    return "intl"


def expiry_of(account) -> float:
    """access token 到期时间（epoch 秒）；取不到返回 0。"""
    data = as_mapping(account)
    for key in ("expired_at", "expiredAt", "expires_at"):
        value = data.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
        if isinstance(value, str) and value.strip().isdigit():
            return float(value.strip())
    return 0.0


def is_expired(account, skew: float = 120.0) -> bool:
    exp = expiry_of(account)
    return bool(exp) and time.time() >= exp - skew


# ---------------------------------------------------------------------------
# 网络层
# ---------------------------------------------------------------------------
_OPENER_LOCK = threading.Lock()
_OPENERS: dict = {}


def _opener(proxy: str | None):
    """按出口复用 opener；proxy 为空则走环境变量（含 no_proxy 语义）。"""
    key = (proxy or "").strip()
    with _OPENER_LOCK:
        opener = _OPENERS.get(key)
        if opener is not None:
            return opener
        if key:
            handler = urllib.request.ProxyHandler({"http": key, "https": key})
            opener = urllib.request.build_opener(handler)
        else:
            opener = urllib.request.build_opener()
        _OPENERS[key] = opener
        return opener


def _proxy_of(account) -> str | None:
    data = as_mapping(account)
    return (data.get("proxy") or data.get("egress")
            or os.environ.get("TRAE_PROXY") or None)


def _decode(raw) -> str:
    if isinstance(raw, bytes):
        return raw.decode("utf-8", "replace")
    return str(raw or "")


def _http_error(exc: urllib.error.HTTPError) -> UpstreamError:
    """HTTP 层错误 -> UpstreamError（尽量从响应体里取业务码）。"""
    try:
        body = _decode(exc.read())
    except Exception:
        body = ""
    code = exc.code
    message = body[:500]
    try:
        parsed = json.loads(body)
    except Exception:
        parsed = None
    if isinstance(parsed, dict):
        for key in ("code", "Code", "errorCode"):
            if not _blank(parsed.get(key)):
                code = parsed[key]
                break
        for key in ("message", "Message", "msg", "error"):
            if not _blank(parsed.get(key)):
                message = str(parsed[key])
                break
    status, kind = classify(code)
    # HTTP 401/403 但业务码未知时，按鉴权/风控处理，别一律当 502。
    if not kind or kind == "upstream_error":
        if exc.code == 401:
            status, kind = 401, "auth"
        elif exc.code == 403:
            status, kind = 403, "risk_control"
        elif exc.code == 429:
            status, kind = 429, "quota_exceeded"
        else:
            status = exc.code
    return UpstreamError(code, message or ("HTTP %s" % exc.code), status,
                         kind=kind, detail=message)


def _network_error(urls, exc) -> UpstreamError:
    return UpstreamError("network", "上游连接失败：%s" % (str(exc)[:200] or type(exc).__name__),
                         None, kind="network",
                         detail="tried: %s" % ", ".join(urls[:3]))


def _host_key(url: str) -> str:
    return str(url or "").split("://")[-1].split("/")[0].lower()


def hosts_for(account, realm, base: str = "core") -> list[str]:
    """候选基址：账号显式 override > 落点亲和 > 区域主选 > 备选（去重保序）。"""
    data = as_mapping(account)
    realm = normalize_realm(realm)
    cfg = REALMS[realm]
    overrides = {"core": ("core_host", "coreHost"),
                 "account": ("account_host", "accountHost"),
                 "pay": ("pay_host", "payHost"),
                 # `ug` = 用户成长类接口（每日签到 / 活动）。
                 # 关键事实：签到只注册在 `api.trae.cn` 上，
                 # 打到 `api.trae.com.cn` 是 404（trae2api-hub 实测 2026-10-19）。
                 "ug": ("ug_host", "ugHost")}[base]
    hosts: list[str] = []
    for key in overrides:
        value = str(data.get(key) or "").strip()
        if value:
            hosts.append(value)
    if base == "account":
        account_host = str(data.get("host") or "").strip()
        if account_host:
            hosts.append(account_host)
    hosts.append(cfg[base])
    hosts.extend(cfg.get(base + "_fallbacks") or ())
    seen, out = set(), []
    for host in hosts:
        host = host.rstrip("/")
        if host and host not in seen:
            seen.add(host)
            out.append(host)
    # 国际版落点亲和：SG 号打 US 主机会被判 1001。账号自己的边缘集群提前。
    if realm == "intl":
        family = _edge_family(data)
        if family:
            pinned = sum(1 for key in overrides if str(data.get(key) or "").strip())
            head, tail = out[:pinned], out[pinned:]
            rank = {family: 0, "default": 1}
            out = head + sorted(tail, key=lambda h: rank.get(_host_family(h), 2))
    return out


def _edge_family(data: dict) -> str:
    text = str(data.get("host") or "").lower()
    region = data.get("user_region") or data.get("userRegion")
    tag = ""
    if isinstance(region, dict):
        tag = str(region.get("region") or region.get("_aiRegion") or "").upper()
    if "traeapi.us" in text or tag == "US":
        return "us"
    if "sg-normal" in text or tag == "SG":
        return "sg"
    return ""


def _host_family(host: str) -> str:
    low = str(host or "").lower()
    if "traeapi.us" in low:
        return "us"
    if "sg-normal" in low:
        return "sg"
    return "default"


def _open(url: str, data: bytes | None, headers: dict, timeout: float,
          proxy: str | None = None, method: str = "POST"):
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    return _opener(proxy).open(req, timeout=timeout)


# ---------------------------------------------------------------------------
# 请求头
# ---------------------------------------------------------------------------
def auth_headers(account, realm=None) -> dict:
    """IDE 数据面鉴权头。

    `x-ide-token` 与 `Authorization` 携带**同一个** access token（实测结论）。
    设备头由 uid 稳定派生，保证同一账号长期同一台「虚拟物理机」。
    """
    data = as_mapping(account)
    realm = normalize_realm(realm or realm_of(data))
    cfg = REALMS[realm]
    token = token_of(data)
    if not token:
        raise UpstreamError("no_token", "账号缺少 access token（先导入或刷新）", None,
                            kind="auth")
    scheme = cfg["auth_scheme"]
    headers = {
        "X-App-Id": APP_ID,
        "x-plugin-channel": PLUGIN_CHANNEL,
        "x-ide-version-code": cfg["version_code"],
        "x-ide-token": token,
        "Authorization": "%s %s" % (scheme, token),
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": cfg["ua"],
    }
    headers.update(fingerprint.device_headers(uid_of(data),
                                              _resolve_device_id(data, realm)))
    headers["X-Request-Id"] = str(uuid.uuid4())
    return headers


def chat_headers(account, realm=None) -> dict:
    """raw_chat 专用头（国际版与通用鉴权头差异很大，单独组装）。"""
    data = as_mapping(account)
    realm = normalize_realm(realm or realm_of(data))
    if realm != "intl":
        headers = auth_headers(data, realm)
        headers["Accept"] = "text/event-stream"
        headers["Cache-Control"] = "no-cache"
        return headers
    token = token_of(data)
    if not token:
        raise UpstreamError("no_token", "账号缺少 access token（先导入或刷新）", None,
                            kind="auth")
    headers = auth_headers(data, realm)
    headers.update({
        "Authorization": "Bearer " + token,
        "x-ide-token": token,
        "x-ide-version-code": "20260924",
        "x-ide-version": "3.6.2",
        "x-app-version": "3.6.2",
        "x-app-version-code": "20260924",
        "User-Agent": "TraeClient/TTNet",
        "Accept": "text/event-stream",
        "Cache-Control": "no-cache",
    })
    # store-country-code 必须跟账号自己的商店地区；写死 us 会被判跨区。
    inner = data.get("account") if isinstance(data.get("account"), dict) else {}
    region = data.get("user_region") or data.get("userRegion") or {}
    store = str(inner.get("storeRegion")
                or (region.get("region") if isinstance(region, dict) else "")
                or "").strip().lower()
    headers["store-country-code"] = store or "us"
    return headers


def _resolve_device_id(data: dict, realm: str):
    """本账号该发的 X-Device-Id（三态）。

    None  国内版：沿用 uid 派生值
    ""    国际版但没有可信设备号 -> **省略该头**（伪造会稳定 4015）
    str   账号自己的真实设备号
    """
    explicit = data.get("device_id") or data.get("deviceId")
    if explicit:
        return str(explicit).strip()
    if realm != "intl":
        return None
    return ""


# ---------------------------------------------------------------------------
# 请求体
# ---------------------------------------------------------------------------
def _text_part(text) -> dict:
    return {"type": "text", "text": "" if text is None else str(text)}


def normalize_content(content) -> list[dict]:
    """把任意 content 变成 Trae 认的 parts 数组（传字符串会 4001）。"""
    if content is None:
        return [_text_part("")]
    if isinstance(content, str):
        return [_text_part(content)]
    if isinstance(content, dict):
        content = [content]
    if isinstance(content, (list, tuple)):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(_text_part(item))
            elif isinstance(item, dict):
                if "text" in item and not item.get("type"):
                    parts.append(_text_part(item.get("text")))
                elif item.get("type") == "text":
                    parts.append(_text_part(item.get("text")))
                else:
                    parts.append(dict(item))
        return parts or [_text_part("")]
    return [_text_part(content)]


ROLE_ALIASES = {"human": "user", "ai": "assistant", "model": "assistant",
                "developer": "system"}


def normalize_messages(messages) -> list[dict]:
    if isinstance(messages, dict):
        messages = [messages]
    if not isinstance(messages, (list, tuple)) or not messages:
        raise ValueError("messages 不能为空")
    out = []
    for index, raw in enumerate(messages):
        if isinstance(raw, str):
            out.append({"role": "user", "content": [_text_part(raw)]})
            continue
        if not isinstance(raw, dict):
            raise ValueError("messages[%d] 不是对象" % index)
        role = str(raw.get("role") or "user").strip().lower()
        role = ROLE_ALIASES.get(role, role)
        if role not in ("system", "user", "assistant"):
            role = "user"
        out.append({"role": role, "content": normalize_content(raw.get("content"))})
    return out


#: 实测可安全透传的采样参数；未记录的一律不发，避免 4001。
PASSTHROUGH_OPTIONS = ("temperature", "top_p", "max_tokens", "stop",
                       "presence_penalty", "frequency_penalty")


def build_chat_body(model, messages, **opts) -> dict:
    """构造 llm_raw_chat 请求体。

    上游只认 `model_name` + parts 数组；`stream` 恒发 True
    （`stream:false` 也回 SSE，聚合由网关负责）。
    """
    model_name = str(model or "").strip()
    if not model_name:
        raise ValueError("model 不能为空")
    body = {
        "model_name": model_name,
        "messages": normalize_messages(messages),
        "stream": True,
    }
    params = {}
    for key in PASSTHROUGH_OPTIONS:
        value = opts.get(key)
        if value is not None:
            params[key] = value
    if params.get("stop") and not isinstance(params["stop"], list):
        params["stop"] = [params["stop"]]
    if params:
        body["parameters"] = params
    return body


# ---------------------------------------------------------------------------
# 对话
# ---------------------------------------------------------------------------
def open_chat(account, realm=None, body=None, timeout: float = DEFAULT_TIMEOUT,
              proxy: str | None = None, only_host: str | None = None):
    """POST llm_raw_chat，返回**未读取**的原始响应（调用方负责关闭）。

    只在「连不上」时切换备选主机；HTTP 状态码/业务错误立即上抛，
    避免把 4015 风控误当网络抖动反复重放。
    """
    data = as_mapping(account)
    realm = normalize_realm(realm or realm_of(data))
    if body is None:
        raise ValueError("body 不能为空")
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = chat_headers(data, realm)
    if proxy is None:
        proxy = _proxy_of(data)
    hosts = hosts_for(data, realm, "core")
    if only_host:
        pinned = _host_key(only_host)
        hosts = [h for h in hosts if _host_key(h) == pinned] or [only_host]
    last_exc = None
    attempts = []
    for host in hosts:
        url = host + PATH_CHAT
        attempts.append(url)
        try:
            resp = _open(url, raw, headers, timeout, proxy=proxy)
        except urllib.error.HTTPError as exc:
            raise _http_error(exc)
        except (urllib.error.URLError, socket.timeout, TimeoutError,
                ConnectionError, OSError) as exc:
            last_exc = exc
            continue
        try:
            setattr(resp, "_trae_host", host)
        except Exception:
            pass
        return resp
    raise _network_error(attempts, last_exc)


def _json_or_none(text):
    try:
        return json.loads(text)
    except Exception:
        return None


def iter_events(resp, idle_timeout: float = 180.0, close: bool = True):
    """逐帧解析上游 SSE，产出 `(event, data)`。

    容错：CRLF、多行 data、注释行、非 JSON（跳过）、缺 event 名（按 message）。
    `idle_timeout`：连续 N 秒无数据产出一次 error 帧并停止，防止半死连接挂住 worker。
    """
    event = ""
    data_lines: list[str] = []
    started = time.monotonic()
    last = started
    try:
        for raw in resp:
            now = time.monotonic()
            if idle_timeout and now - last > idle_timeout:
                if data_lines:
                    payload = _json_or_none("\n".join(data_lines))
                    if payload is not None:
                        yield (event or "message", payload)
                yield ("error", {"code": "idle_timeout",
                                 "message": "上游 %s 秒无数据，判定连接挂死" % int(idle_timeout)})
                return
            last = now
            line = _decode(raw).rstrip("\r\n")
            if not line:
                if data_lines:
                    payload = _json_or_none("\n".join(data_lines))
                    if payload is not None:
                        yield (event or "message", payload)
                event, data_lines = "", []
                continue
            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                chunk = line[5:]
                data_lines.append(chunk[1:] if chunk.startswith(" ") else chunk)
        if data_lines:
            payload = _json_or_none("\n".join(data_lines))
            if payload is not None:
                yield (event or "message", payload)
    finally:
        if close and resp is not None:
            try:
                resp.close()
            except Exception:
                pass


def collect(resp, idle_timeout: float = 180.0) -> dict:
    """把整条 SSE 聚合成结构化结果（非流式网关 / 健康检查用）。"""
    out = {"text": "", "reasoning": "", "tool_calls": [], "usage": {},
           "finish_reason": None, "metadata": {}, "errors": [], "quota": None}
    for event, data in iter_events(resp, idle_timeout=idle_timeout):
        obj = data if isinstance(data, dict) else {}
        if event == "metadata":
            out["metadata"] = obj
        elif event in ("output", "message"):
            out["text"] += obj.get("response") or ""
            out["reasoning"] += obj.get("reasoning_content") or ""
            if obj.get("tool_calls"):
                out["tool_calls"].append(obj["tool_calls"])
        elif event in ("token_usage", "usage"):
            out["usage"] = obj
        elif event == "notify_usage":
            out["quota"] = obj
        elif event == "done":
            out["finish_reason"] = obj.get("finish_reason") or "stop"
        elif event == "error":
            out["errors"].append(obj)
    if out["finish_reason"] is None:
        out["finish_reason"] = "stop" if out["text"] else "error"
    return out


def chat_once(account, realm=None, model=None, messages=None, timeout=180, **opts) -> dict:
    """一次性聊天（非流式聚合），用于健康检查与联调。"""
    body = build_chat_body(model, messages or [], **opts)
    resp = open_chat(account, realm, body, timeout=timeout)
    return collect(resp, idle_timeout=timeout)


# ---------------------------------------------------------------------------
# 刷新 / 其它接口
# ---------------------------------------------------------------------------
def refresh_account(account, timeout: float = 30.0) -> dict:
    """ExchangeToken 轮换刷新，返回**新 dict**（旧 refresh token 立即失效）。

    调用方必须把返回值原子落库，否则下次刷新会失败。
    """
    data = as_mapping(account)
    realm = normalize_realm(realm_of(data))
    refresh = data.get("refresh_token") or data.get("refreshToken")
    if not isinstance(refresh, str):
        refresh = str(refresh) if isinstance(refresh, (int, float)) else ""
    if not refresh:
        raise UpstreamError("no_refresh_token", "账号缺少 refresh token，无法刷新", None,
                            kind="auth")
    payload = {
        "ClientID": CLIENT_ID,
        "RefreshToken": refresh,
        "ClientSecret": CLIENT_SECRET,
        "UserID": uid_of(data),
    }
    last_exc = None
    parsed = None
    hosts = hosts_for(data, realm, "account")
    for host in hosts:
        url = host + PATH_EXCHANGE_TOKEN
        try:
            with _open(url, json.dumps(payload).encode("utf-8"),
                       {"Content-Type": "application/json",
                        "Accept": "application/json",
                        "User-Agent": REALMS[realm]["ua"]},
                       timeout, proxy=_proxy_of(data)) as resp:
                parsed = _json_or_none(_decode(resp.read())) or {}
            break
        except urllib.error.HTTPError as exc:
            raise _http_error(exc)
        except (urllib.error.URLError, socket.timeout, TimeoutError,
                ConnectionError, OSError) as exc:
            last_exc = exc
            continue
    if parsed is None:
        raise _network_error([h + PATH_EXCHANGE_TOKEN for h in hosts], last_exc)

    result = parsed.get("Result") if isinstance(parsed, dict) else None
    if not isinstance(result, dict):
        result = parsed if isinstance(parsed, dict) else {}
    token = result.get("Token") or result.get("token")
    if not isinstance(token, str):
        # Token 可能是 dict / list（上游偶发包错），不能直接当字符串用
        token = str(token) if isinstance(token, (int, float)) else ""
    if not token:
        # `Code` / `Message` 也可能是 dict：`UpstreamError` 会拿 code 去拼串、
        # 部分路径还会当 key 用，直接传 dict 会 `unhashable type: 'dict'`。
        raw_code = parsed.get("Code") or parsed.get("code") or "refresh_failed"
        code = raw_code if isinstance(raw_code, (str, int)) else str(raw_code)
        raw_msg = (parsed.get("Message") or parsed.get("message")
                   or "ExchangeToken 未返回 Token")
        message = raw_msg if isinstance(raw_msg, str) else str(raw_msg)
        raise UpstreamError(code, str(message), None, kind="auth")

    fresh = dict(data)
    fresh["token"] = token
    fresh["refresh_token"] = (result.get("RefreshToken") or result.get("refresh_token")
                              or refresh)
    expire_ms = result.get("TokenExpireAt")
    if isinstance(expire_ms, (int, float)) and expire_ms > 0:
        fresh["expired_at"] = int(expire_ms // 1000)
    refresh_expire_ms = result.get("RefreshExpireAt")
    if isinstance(refresh_expire_ms, (int, float)) and refresh_expire_ms > 0:
        fresh["refresh_expired_at"] = int(refresh_expire_ms // 1000)
    fresh["realm"] = realm
    return fresh


def request_json(account, realm=None, path="", body=None, base="core",
                 timeout=60.0, method="POST", extra_headers=None):
    """带鉴权头的 JSON 请求（非对话接口用）。

    `extra_headers`：在 IDE 鉴权头**之上**追加/覆盖的头。
    主要用于「网页会话」类接口 —— 它们认浏览器 Cookie 而不是 IDE token，
    但公共头（X-App-Id 等）仍然要带，所以是追加而不是替换。
    """
    data = as_mapping(account)
    realm = normalize_realm(realm or realm_of(data))
    raw = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    proxy = _proxy_of(data)
    hosts = hosts_for(data, realm, base)
    headers = auth_headers(data, realm)
    if extra_headers:
        headers = {**headers, **{str(k): str(v) for k, v in extra_headers.items()}}
    last_exc = None
    payload_raw = None
    for host in hosts:
        url = host + path
        try:
            with _open(url, raw, headers, timeout, proxy=proxy, method=method) as resp:
                payload_raw = resp.read()
            break
        except urllib.error.HTTPError as exc:
            raise _http_error(exc)
        except (urllib.error.URLError, socket.timeout, TimeoutError,
                ConnectionError, OSError) as exc:
            last_exc = exc
            continue
    if payload_raw is None:
        raise _network_error([h + path for h in hosts], last_exc)
    text = _decode(payload_raw).strip()
    if not text:
        return {}
    parsed = _json_or_none(text)
    if parsed is None:
        raise UpstreamError("bad_json", "上游返回非 JSON：%s" % text[:200], None)
    return parsed


def fetch_model_list(account, realm=None, kind: str = "chat") -> list[dict]:
    """model_list；逐主机尝试（有的机器没有这个接口）。"""
    data = as_mapping(account)
    realm = normalize_realm(realm or realm_of(data))
    last_exc = None
    for host in hosts_for(data, realm, "core"):
        try:
            payload = request_json(data, realm, PATH_MODEL_LIST, {"type": kind},
                                   base="core", timeout=45, )
        except UpstreamError as exc:
            text = "%s %s" % (exc.code, exc.message)
            if "no schema found" in text or str(exc.status or "") == "404":
                last_exc = exc
                continue
            raise
        except Exception as exc:  # noqa: BLE001 - 传输层：换主机
            last_exc = exc
            continue
        rows = []
        if isinstance(payload, dict):
            rows = payload.get("model_configs") or payload.get("models") or []
        elif isinstance(payload, list):
            rows = payload
        rows = [m for m in rows if isinstance(m, dict)]
        if rows:
            return rows
        last_exc = UpstreamError("empty_model_list", "model_list 返回空清单", None)
    if isinstance(last_exc, UpstreamError):
        raise last_exc
    raise UpstreamError("model_list_unreachable",
                        "model_list 在所有主机上都失败：%s" % str(last_exc)[:200], None)


def fetch_entitlement(account, realm=None, timeout: float = 45.0) -> dict:
    """权益包 / 付费状态。任一失败都不影响主链路，错误收进 errors。"""
    data = as_mapping(account)
    realm = normalize_realm(realm or realm_of(data))
    out = {"realm": realm, "ent_usage": None, "pay_status": None, "errors": []}
    ent_path = PATH_ENT_USAGE_V2 if realm == "cn" else PATH_ENT_USAGE
    for key, path in (("ent_usage", ent_path), ("pay_status", PATH_PAY_STATUS)):
        try:
            out[key] = request_json(data, realm, path, {}, base="pay", timeout=timeout)
        except UpstreamError as exc:
            out["errors"].append({"endpoint": key, **exc.to_dict()})
        except Exception as exc:  # noqa: BLE001 - 额度接口绝不打断对话
            out["errors"].append({"endpoint": key, "code": "exception",
                                  "message": str(exc), "status": None})
    return out


def fetch_session_usage(account, realm=None, timeout: float = 45.0) -> dict:
    data = as_mapping(account)
    realm = normalize_realm(realm or realm_of(data))
    return request_json(data, realm, PATH_SESSION_USAGE, {}, base="core", timeout=timeout)


# ---------------------------------------------------------------------------
# 每日签到（仅国内版真实可用）
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 用量 / 权益（国际版网页端那两个接口）
# ---------------------------------------------------------------------------
#: 当前权益包（含美元用量）。`{"require_usage": true}` 才会带回 usage 字段。
#: 实测（2026-10-28，真实账号）：响应里的
#:     user_entitlement_pack_list[].usage.basic_usage_amount = 0.00066  # 已用美元
#:     ...entitlement_base_info.quota.basic_usage_limit      = 1        # 上限美元
#: 网页端把它显示成 "$0.65 / $1.00"。
PATH_ENTITLEMENT_LIST = "/trae/api/v1/pay/user_current_entitlement_list"

#: 按会话分组的用量明细。body 需要 start_time / end_time（unix 秒）、
#: page_size / page_num，以及 `usage_type`（5/6 = 对话类消耗）。
PATH_USAGE_BY_SESSION = "/trae/api/v1/pay/query_user_usage_group_by_session"

#: 用户统计（网页端也有，含请求数与 token 汇总）
PATH_USER_STATISTIC = "/cloudide/api/v3/trae/GetUserStasticData"

#: 签到状态 / 领取路径。只在 `api.trae.cn`（`base="ug"`）上注册。
PATH_CHECKIN_STATUS = "/trae/api/v2/ug/checkin_credits/status"
PATH_CHECKIN_CLAIM = "/trae/api/v2/ug/checkin_credits/claim"


def _checkin_req_source(realm) -> int:
    """`req_source`：客户端里国内版 1、国际版 2（`main.js` 的 `li(this.P)?2:1`）。"""
    return 2 if normalize_realm(realm) == "intl" else 1


def checkin_status(account, realm=None, timeout: float = 30.0) -> dict:
    """每日签到状态。

    **只有国内版真实可用**。实测响应（CN，2026-10-19）：
        {"checked_in":true,"code":0,"credits":100,"did_checked_in":false,
         "enable":true,"extra_credits":100,"message":"success"}

    国际版会返回 404 / 业务错误 —— 本函数**如实抛 UpstreamError**，
    绝不伪造「已签到」状态：假的成功比明确的失败更糟。
    """
    data = as_mapping(account)
    realm = normalize_realm(realm or realm_of(data))
    body = {"req_source": _checkin_req_source(realm)}
    result = request_json(data, realm, PATH_CHECKIN_STATUS, body,
                          base="ug", timeout=timeout)
    return result if isinstance(result, dict) else {}


def claim_checkin(account, realm=None, timeout: float = 30.0) -> dict:
    """领取每日签到积分。

    返回统一结构：
        {"ok", "checked_in", "already", "credits", "earned_credit",
         "message", "raw"}

    **幂等**：已签到直接返回 `already=True`，不重复领取。
    **不把「接口 200」当「已到账」**：领取后再查一次状态，用 credits
    差额算真实到账 —— 上游确实存在「返回成功但积分没变」的情况。
    """
    data = as_mapping(account)
    realm = normalize_realm(realm or realm_of(data))
    before = checkin_status(data, realm, timeout=timeout)
    if before.get("checked_in"):
        return {"ok": True, "checked_in": True, "already": True,
                "credits": int(before.get("credits") or 0),
                "earned_credit": 0,
                "message": "今日已签到（积分 %s）" % int(before.get("credits") or 0),
                "raw": before}
    body = {"req_source": _checkin_req_source(realm)}
    raw = request_json(data, realm, PATH_CHECKIN_CLAIM, body,
                       base="ug", timeout=timeout)
    after = checkin_status(data, realm, timeout=timeout)
    before_credits = int(before.get("credits") or 0)
    after_credits = int(after.get("credits") or 0)
    earned = max(0, after_credits - before_credits)
    ok = bool(after.get("checked_in")) and (
        not isinstance(raw, dict) or raw.get("code") in (0, None))
    return {
        "ok": bool(ok),
        "checked_in": bool(after.get("checked_in")),
        "already": False,
        "credits": after_credits,
        "earned_credit": earned,
        "message": ("签到成功，+%d 积分" % earned) if earned
                   else ("签到请求已接受，积分未变化（可能今日已领）" if ok else "签到失败"),
        "raw": raw,
    }


def fetch_entitlement_list(account, realm=None, timeout: float = 30.0,
                           require_usage: bool = True) -> dict:
    """当前权益包（含美元用量）。

    `require_usage=True` 时上游会带回 `usage.basic_usage_amount` ——
    那是**已用美元**，与 `quota.basic_usage_limit`（上限美元）一起构成
    网页端显示的 "$0.65 / $1.00"。不带这个参数就只有额度上限、没有已用量。
    """
    data = as_mapping(account)
    realm = normalize_realm(realm or realm_of(data))
    body = {"require_usage": True} if require_usage else {}
    result = request_json(data, realm, PATH_ENTITLEMENT_LIST, body,
                          base="ug", timeout=timeout)
    return result if isinstance(result, dict) else {}


def web_cookie_of(account) -> str:
    """从凭证里取网页会话 cookie（没有就是空串）。"""
    data = as_mapping(account)
    for key in ("web_cookie", "cookie", "web_session"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def fetch_usage_by_session(account, realm=None, *, days: int = 30,
                           page: int = 1, page_size: int = 20,
                           usage_type=None, timeout: float = 30.0) -> dict:
    """按会话分组的用量明细。

    ## 鉴权：必须用**网页会话 cookie**，不是 IDE token

    实测（2026-10-28，两个账号、6 个时间窗、8 组参数）：
    IDE token 调这个接口**永远** `total=0`，但传错参数会报 `9004`
    —— 证明鉴权通过、参数被校验，只是这个接口不给 IDE token 返数据。
    网页端能查到，因为它带浏览器 cookie。

    所以这里：
      · 有 `web_cookie` -> 用 cookie 调（正常拿到数据）
      · 没有            -> 返回 `need_web_cookie=True`，
                           **不要**让调用方把「没凭证」误读成「没用量」

    `usage_type` 默认 `[5, 6]`（对话类消耗，来自网页端实测）。
    """
    data = as_mapping(account)
    realm = normalize_realm(realm or realm_of(data))
    cookie = web_cookie_of(data)
    if not cookie:
        return {
            "need_web_cookie": True,
            "summary": ("查用量明细需要网页会话 cookie："
                        "登录 trae.ai 后从浏览器开发者工具复制 Cookie 头，"
                        "在账号里填入「网页 Cookie」。"),
            "total": None,
            "sessions": [],
        }

    now = int(time.time())
    start = now - max(1, int(days)) * 86400
    body = {
        "start_time": start,
        "end_time": now,
        "page_size": max(1, min(int(page_size), 200)),
        "page_num": max(1, int(page)),
        "usage_type": list(usage_type or (5, 6)),
    }
    result = request_json(data, realm, PATH_USAGE_BY_SESSION, body,
                          base="ug", timeout=timeout,
                          extra_headers={"Cookie": cookie})
    out = result if isinstance(result, dict) else {}
    out.setdefault("need_web_cookie", False)
    return out


def fetch_user_statistic(account, realm=None, timeout: float = 30.0) -> dict:
    """用户统计（请求数 / token 汇总）。上游结构不稳定，调用方需容错。"""
    data = as_mapping(account)
    realm = normalize_realm(realm or realm_of(data))
    result = request_json(data, realm, PATH_USER_STATISTIC, {},
                          base="ug", timeout=timeout)
    return result if isinstance(result, dict) else {}