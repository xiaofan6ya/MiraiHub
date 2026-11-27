"""Qoder HTTP 客户端：令牌交换/续期、用户资料、额度、模型目录、对话流。"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
import uuid

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


#: 长连接池里的平台名（开关 `ADMIN_URLLIB_KEEPALIVE_QODER` / 总开关 `ADMIN_URLLIB_KEEPALIVE`）。
KEEPALIVE_PLATFORM = "qoder"


def _open(url: str, data: bytes | None, headers: dict,
          timeout: float, method: str, *, account_id: int = 0):
    """`account_id` 非 0 且开关打开时，对话请求走按账号长连接池（r3 第 1 项，默认关）。

    代理语义同 `_upstream_opener()`（读环境变量代理）；池不支持的代理形态回落原 opener。
    """
    req = urllib.request.Request(url, data=data, method=method)
    for key, value in headers.items():
        req.add_header(str(key), str(value))
    if data:
        req.add_header("Content-Type", "application/json")
    if account_id:
        try:
            from admin import http_keepalive
        except Exception:  # noqa: BLE001 - 独立脚本里没有 admin 包
            http_keepalive = None
        if http_keepalive is not None and http_keepalive.enabled(KEEPALIVE_PLATFORM):
            resp = http_keepalive.open_request(KEEPALIVE_PLATFORM, req, timeout=timeout,
                                               account=int(account_id), proxy=None,
                                               trust_env=True)
            if resp is not None:
                return resp
    return _upstream_opener().open(req, timeout=timeout)


def _is_delivery_unknown(exc) -> bool:
    return type(exc).__name__ == "DeliveryUnknown"


def _delivery_unknown(exc) -> "UpstreamError":
    """请求已写出、连接在响应头前中断：status=0 → `retryable()` 为 False，不进哨兵。"""
    return UpstreamError("对话请求交付状态未知（请求已发出，连接在收到响应前中断）",
                         status=0, kind="delivery_unknown",
                         detail=str(getattr(exc, "detail", "") or exc)[:200])


def _client_gone() -> "UpstreamError":
    return UpstreamError("客户端已断开，停止读取上游", status=0,
                         kind="client_disconnected")


class _AbortGuard:
    """把上游响应登记到 `abort` 句柄（见 `http_keepalive.AbortHandle`）。

    `with _AbortGuard(abort, resp) as ok:` —— 退出时先 detach（**早于** `with resp`
    的 close / 归还连接池），保证消费端的 abort 不会打到别的请求复用的连接上。
    """

    __slots__ = ("_abort", "_resp")

    def __init__(self, abort, resp):
        self._abort = abort
        self._resp = resp

    def __enter__(self) -> bool:
        if self._abort is None:
            return True
        return bool(self._abort.attach(self._resp))

    def __exit__(self, *_exc):
        if self._abort is not None:
            self._abort.detach()
        return False


def _aborted(abort) -> bool:
    return bool(abort is not None and getattr(abort, "aborted", False))


_OPENER = None


def _upstream_opener():
    """进程内复用的 opener（P3）：共享 TLS 上下文 + 建连短超时。

    `upstream_http.build_opener(True)` 不装 `ProxyHandler({})`，urllib 会照旧读
    环境变量代理 —— 与原来裸 `urlopen` 的代理语义一致（只多了建连 10 秒上限）。
    """
    global _OPENER
    if _OPENER is None:
        try:
            from admin.upstream_http import build_opener as _build

            _OPENER = _build(True)
        except Exception:  # noqa: BLE001 - 独立脚本里没有 admin 包时退回默认 opener
            _OPENER = urllib.request.build_opener()
    return _OPENER


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
        raise _error_from_payload(status, text, action, account_id, realm)
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise UpstreamError("%s返回非 JSON" % action, status=status,
                            detail=text[:200]) from exc
    return data if isinstance(data, dict) else {"data": data}


def _error_from_payload(status: int, text: str, action: str,
                        account_id: int = 0, realm: str = "") -> "UpstreamError":
    """把「已读出的错误体」转成 `UpstreamError`（不抛，由调用方 raise）。

    **空 404 = 路由不存在**（2026-11-26）：`/model/v1/chat/completions` 现在连
    不带凭证的请求都回空 body 的 404（GET/POST/OPTIONS、http1.1/http2 同结果），
    说明边缘层没有这条路由 —— 是端点被撤，不是模型名写错。
    以前这里一律落 `_classify(404)` -> `model_unknown`，用户看到「模型不存在」，
    账号还被记一次失败。判据只有一条：**404 且 body 为空**。
    有 body 的 404 仍然是上游的业务 404（那才可能是模型不存在）。
    """
    if status == 404 and not text.strip():
        return UpstreamError(
            "%s失败（HTTP 404，空响应：端点不存在）" % action,
            status=status, code="404", kind="endpoint_gone", detail="")
    try:
        data = json.loads(text) if text.strip().startswith("{") else {}
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    code = str(data.get("code") or data.get("errorCode") or status)
    message = str(data.get("message") or data.get("errorMessage") or text[:200])
    kind = _classify(status, code, message)
    note_sentinel(realm or "cn", account_id, kind, code=code,
                  status=status, detail=text)
    return UpstreamError("%s失败（HTTP %d）" % (action, status),
                         status=status, code=code, kind=kind, detail=text)


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
    return _json_error_from_payload(exc, payload, action, account_id, realm)


def _json_error_from_payload(exc: urllib.error.HTTPError, payload: bytes,
                             action: str, account_id: int = 0,
                             realm: str = "") -> UpstreamError:
    """`_json_error` 的显式 body 版本（调用方已经读过 body 时用）。"""
    return _error_from_payload(exc.code, payload.decode("utf-8", "replace"),
                               action, account_id, realm)


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


def fetch_campaigns(token: str, realm: str, *, uid: str = "",
                    umid: str = "") -> dict:
    """活动列表（签到入口）。

    ⚠️ 不要用 legacy 的 `/sash/api/v1/me/daily-check-in/status`：
    两个地区都已停用（国际版 404、中国版恒 `DISABLED`）。

    `umid` 非空时带上 `Cosy-Machine*` 风控头 —— 不带这些头，活动列表里
    永远只有 `VIEW_DETAILS` 促销，看起来就像「这个号没有签到」。
    """
    conf = endpoints.REGIONS.get(realm) or endpoints.REGIONS["cn"]
    return request_json(
        conf["open_api"] + endpoints.CAMPAIGNS_PATH + endpoints.CAMPAIGNS_QUERY,
        action="活动列表查询",
        headers=endpoints.sash_headers(token, uid=uid, umid=umid))


def claim_campaign(token: str, realm: str, campaign_id: str, *,
                   uid: str = "", umid: str = "") -> dict:
    conf = endpoints.REGIONS.get(realm) or endpoints.REGIONS["cn"]
    return request_json(
        "%s%s/%s/claim" % (conf["open_api"], endpoints.CAMPAIGNS_PATH,
                           campaign_id),
        action="领取权益", method="POST", body={},
        headers=endpoints.sash_headers(token, uid=uid, umid=umid))


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


def _classify_model_server_error(payload: str, status: int):
    """把 model server 的错误载荷分类成 (message, status, code, kind)。

    分类规则（2026-11-07 实测）：
      * `pricingUrl`                    → 无套餐/额度耗尽（quota_exceeded/402），
        选号层据此冷却 1800s 换号，不再反复撞同一个号；
      * `agent_router` / `flow nodes`   → 账号 agent 配置未初始化（理论上新
        链路不会再出现，保留以防上游回退），报错里直接给自愈方法；
      * `invalid_model_error`           → 模型名不被接受（model_unknown/404）；
      * 其余                             → upstream_business 原样上抛。
    """
    try:
        data = json.loads(payload)
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    message = str(data.get("message") or data.get("errorMessage") or payload[:200])
    code = str(data.get("code") or data.get("errorCode") or status)
    if "pricingUrl" in payload:
        return "该账号没有可用套餐或额度已耗尽（上游返回定价页）", 402, code, "quota_exceeded"
    if "agent_router" in payload or "flow nodes" in payload:
        return ("该账号上游 agent 配置未初始化（用官方 Qoder 客户端登录此账号"
                "并随便发一条消息即可恢复）"), status, code, "upstream_business"
    if "invalid_model" in payload:
        return "模型不存在：%s" % message, 404, code, "model_unknown"
    return message or "上游业务错误", status, code, "upstream_business"


def iter_model_chat_stream(token: str, realm: str, *, body: dict,
                           timeout: float = 600.0,  # 帧间隔上限（思考静默不掐）
                           account_id: int = 0, abort=None):
    """对话流 · **model server 新链路**（2026-11-07）。

    `POST {model_base}/model/v1/chat/completions`：标准 OpenAI
    `chat.completion.chunk` SSE，**无信封、无 COSY 签名**，
    `Bearer <作业令牌>` 直接可用（实测 dt- 令牌 200）。

    body 里必须带 `metadata`（client_type="6" + business 块），
    缺了上游会把请求判成 Unsupported model / All backends failed
    —— 组包在 `services.chat.prepare` 里做，这里只负责发与解析。
    """
    context = ((body.get("metadata") or {}).get("context") or {})
    request_id = str(context.get("request_id") or uuid.uuid4())
    session_id = str(context.get("session_id") or ("sess-" + uuid.uuid4().hex[:16]))
    base = endpoints.model_base(realm)
    url = base + endpoints.MODEL_CHAT_PATH
    encoded = json.dumps(body, ensure_ascii=False).encode()
    headers = {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "X-Request-ID": request_id,
        "X-Session-ID": session_id,
        "User-Agent": "Qoder",
    }
    try:
        resp = _open(url, encoded, headers, timeout, "POST", account_id=account_id)
    except urllib.error.HTTPError as exc:
        # **路由级 404：这条端点没了，不是模型错、更不是账号错**（2026-11-26）。
        # 空 body 的 404 由 `_error_from_payload` 统一判成 `endpoint_gone`
        # （见那里的注释）；这里只负责把它换成一句带端点的说明，
        # 好让日志/面板直接看出「是链路要换，不是模型名写错」。
        payload = b""
        try:
            payload = exc.read()
        except Exception:  # noqa: BLE001 - 读不到 body 就按空处理
            payload = b""
        err = _json_error_from_payload(exc, payload, "对话请求", account_id, realm)
        if err.kind == "endpoint_gone":
            raise UpstreamError(
                "model server 路由不存在（%s 返回空 404，该端点已被上游撤掉）"
                % endpoints.MODEL_CHAT_PATH,
                status=404, code="404", kind="endpoint_gone",
                detail=url) from exc
        payload_text = getattr(err, "detail", "") or ""
        if "pricingUrl" in payload_text or exc.code == 402:
            raise UpstreamError(
                "该账号没有可用套餐或额度已耗尽（上游返回定价页）",
                status=402, code=str(exc.code), kind="quota_exceeded",
                detail=payload_text[:400]) from exc
        raise err from exc
    except Exception as exc:  # noqa: BLE001
        if _is_delivery_unknown(exc):
            raise _delivery_unknown(exc) from exc
        note_sentinel(realm, account_id, "network", detail=str(exc)[:200])
        raise UpstreamError("对话请求网络失败", status=0, kind="network",
                            detail=str(exc)[:200]) from exc

    with resp, _AbortGuard(abort, resp) as attached:
        if not attached:
            raise _client_gone()
        try:
            yield from _model_events(resp, realm, account_id)
        except UpstreamError:
            raise
        except Exception:
            # 消费端 abort 打断了阻塞读（socket 被 shutdown）：按客户端断开收尾
            if _aborted(abort):
                raise _client_gone() from None
            raise
        if _aborted(abort):
            raise _client_gone()


def _model_events(resp, realm: str, account_id: int):
    """model server SSE → chunk dict（从 `iter_model_chat_stream` 拆出来，逻辑不变）。"""
    if True:
        # 标准 SSE 事件聚合：**同一个事件可以拆成多个 `data:` 行**（SSE 规范
        # 规定以 \n 连接后才是完整载荷）。上游的收尾帧（finish + usage +
        # telemetry，好几百字符）实测会被拆成 2~3 行 —— 按行单独
        # json.loads 必然失败丢帧，「usage 永远是空」就是这么来的。
        # 这里缓冲到空行（事件边界）再整体解析。
        event_name = ""
        data_parts: list[str] = []

        def _dispatch():
            """聚合出当前事件；返回 (payload, is_error_event)。"""
            nonlocal event_name, data_parts
            if not data_parts:
                return "", False
            payload = "\n".join(data_parts).strip()
            parts, ev = data_parts, event_name
            data_parts = []
            event_name = ""
            return payload, ev == "error"

        for raw_line in resp:
            line = raw_line.decode("utf-8", "replace").rstrip("\r\n")
            if line == "":
                # 空行 = 事件边界：分发缓冲
                payload, is_err_event = _dispatch()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    chunk = json.loads(payload)
                except ValueError:
                    logger.debug("model server SSE 非法 JSON 丢弃: %s",
                                 payload[:120])
                    continue
                if not isinstance(chunk, dict):
                    continue
                # 错误帧：`event: error`，或 data.type 带 *_error 后缀
                # （invalid_model_error / provider_error / upstream_error…），
                # 或裸 error 键 / pricingUrl 载荷。
                ctype = str(chunk.get("type") or "")
                if (is_err_event or chunk.get("error") or ctype == "error"
                        or ctype.endswith("_error") or "pricingUrl" in payload):
                    message, status, code, kind = _classify_model_server_error(
                        payload, 500)
                    note_sentinel(realm, account_id, kind, code=code,
                                  status=status, detail=payload[:200])
                    raise UpstreamError(message, status=status, code=code,
                                        kind=kind, detail=payload[:400])
                yield chunk
                continue
            if line.startswith("event:"):
                event_name = line[6:].strip()
                continue
            if line.startswith("data:"):
                data_parts.append(line[5:].strip())
                continue
            if data_parts:
                # **裸续行**（不以 data:/event: 开头）：上游把超长的
                # finish+usage 帧按固定长度切成裸行续体（实测两帧里就有
                # 一帧如此），拼回上一个 data 段才是完整 JSON。
                # 直接字符串拼接（无分隔符）——续行是从 JSON 中间切开的。
                data_parts[-1] += line
                continue
            # 其它字段（id:/retry:/注释行）忽略
            continue
        # 流没有以空行收尾时把缓冲里剩的事件也分发掉
        payload, is_err_event = _dispatch()
        if payload and payload != "[DONE]":
            try:
                chunk = json.loads(payload)
            except ValueError:
                chunk = None
            if isinstance(chunk, dict):
                ctype = str(chunk.get("type") or "")
                if (is_err_event or chunk.get("error") or ctype == "error"
                        or ctype.endswith("_error") or "pricingUrl" in payload):
                    message, status, code, kind = _classify_model_server_error(
                        payload, 500)
                    raise UpstreamError(message, status=status, code=code,
                                        kind=kind, detail=payload[:400])
                yield chunk


def iter_chat_stream(token: str, realm: str, *, uid: str, body: dict,
                     name: str = "", email: str = "",
                     machine_id: str = "", timeout: float = 600.0,  # 帧间隔上限（思考静默不掐）
                     account_id: int = 0, abort=None):
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
        resp = _open(url, encoded, headers, timeout, "POST", account_id=account_id)
    except urllib.error.HTTPError as exc:
        raise _json_error(exc, "对话请求", account_id, realm) from exc
    except Exception as exc:  # noqa: BLE001
        if _is_delivery_unknown(exc):
            raise _delivery_unknown(exc) from exc
        note_sentinel(realm, account_id, "network", detail=str(exc)[:200])
        raise UpstreamError("对话请求网络失败", status=0, kind="network",
                            detail=str(exc)[:200]) from exc

    with resp, _AbortGuard(abort, resp) as attached:
        if not attached:
            raise _client_gone()
        try:
            yield from _envelope_events(resp, payload_realm=realm, account_id=account_id)
        except UpstreamError:
            raise
        except Exception:
            if _aborted(abort):
                raise _client_gone() from None
            raise
        if _aborted(abort):
            raise _client_gone()


def _envelope_events(resp, *, payload_realm: str, account_id: int):
    """COSY 信封 SSE → 内层 chunk dict（从 `iter_chat_stream` 拆出来，逻辑不变）。"""
    realm = payload_realm
    if True:
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
                # ---- 业务错误分类（2026-11-07）----
                # 上游把「无套餐/额度耗尽」包成 pricingUrl 载荷（HTTP 仍是
                # 200），此前落成「对话失败：{json}」——用户看不懂，账号也
                # 没被当成额度问题冷却，下一轮还被选出来继续撞。
                # kind=quota_exceeded → 网关 _cool_seconds 给 1800s 冷却，
                # 池子自动跳过它换别的号。
                has_pricing = ("pricingUrl" in payload
                               or (isinstance(inner, dict) and inner.get("pricingUrl")))
                if has_pricing:
                    raise UpstreamError(
                        "该账号没有可用套餐或额度已耗尽（上游返回定价页）",
                        status=402, code=str(status), kind="quota_exceeded",
                        detail=payload[:400])
                # 「flow nodes found for router agent_router」= 上游按账号的
                # agent 配置路由对话，这个号没有 flow 配置。协议参数怎么改
                # 都一样（实测），只能用官方 Qoder 客户端登录该号发一条消息
                # 完成初始化。把做法写进报错，别让用户以为网关坏了。
                if "agent_router" in payload:
                    raise UpstreamError(
                        "该账号上游 agent 配置未初始化（用官方 Qoder 客户端登录"
                        "此账号并随便发一条消息即可恢复）",
                        status=int(status), code=str(status),
                        kind="upstream_business", detail=payload[:400])
                # **10605 = 排队中**（`isQueued: true` + `queueType: p4`）。
                # 这是可重试的容量状态，不是账号坏了 —— 原来落成
                # `upstream_business` 会让 `_attempt()` 把这个号冷却 + 计一次
                # 错误，几次排队就把好号挤出池子（国际版 qmodel_latest 实测）。
                # `_CODE_KINDS` 里早就定义了 `queued`，只是信封这条链路没用上。
                if "10605" in payload or "isQueued" in payload:
                    raise UpstreamError(
                        "上游排队中（10605，稍后自动重试）",
                        status=429, code="10605", kind="queued",
                        detail=payload[:400])
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
