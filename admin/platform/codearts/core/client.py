"""CodeArts HTTP 客户端（华为云 AK/SK 签名）。

## 上游形态（实测 2026-11-04）

    POST /api/v2/chat/completions
      {"model":"deepseek-v4.1-flash","messages":[...],"stream":false}
      → {"id","object":"chat.completion","model","created",
         "choices":[{"message":{"role","content","reasoning_content"},
                     "finish_reason"}],
         "usage":{"prompt_tokens","completion_tokens","total_tokens",
                  "completion_tokens_details":{"reasoning_tokens"}}}

    stream: true → text/event-stream，帧格式与 OpenAI 一致（`data: {...}`）。

所以这个平台**不需要协议适配层**：请求体直传，响应直回。真正的工作量在
签名（见 `signer.py`）和错误分类上。

## 错误分类为什么重要

上游的失败分成两种完全不同的处置：

| 现象 | 真实含义 | 处置 |
|---|---|---|
| `APIG.0301 verify ak sk signature fail` | 临时凭证过期/被撤销 | **换号**，并给该号冷却 |
| `APIG.0201` / 429 | 限流 | 退避后换号 |
| 402 / `insufficient balance` | 积分耗尽 | 冷却 + 换号（**不是坏号**） |
| `TM.00001041` 并发会话超限 | **该账号的会话窗口满了** | 退避 + 换号（**可重试**） |
| 5xx | 上游抖动 | 可重试 |
| 400 参数错 | 请求本身有问题 | **不重试**（换号也一样错） |

把 400 当可重试会让一次参数错误把整个号池轮一遍；把凭证过期当不可重试
则会让「只有一个号过期」变成「整平台不可用」。两边的代价都很实在。

## `TM.00001041` 为什么必须单独拎出来（实测 2026-11-04）

上游这条错误的含义**不是「请求参数错」**，而是「**这个账号的并发会话数
已达上限（3 个）**」——它是一个**限流信号**，被上游用 HTTP 400 表达。

实测（直接打上游，同一个账号）：

```
stream=False  连发 6 次 + 并发 4 次  → 全部 200，从不触发
stream=True   连发第 1/2/3 次      → 200
              连发第 4 次           → 400 TM.00001041
              等 30s 后             → 恢复
```

三个关键事实：

1. **限的是「会话」不是「请求」。** 只有流式请求会建立会话（chunk id 形如
   `as-xxxxxxxx`）；非流式请求连发 6 次全部通过。
2. **会话在请求结束后仍滞留 ~30 秒。** 顺序连发（间隔 0.5s）照样第 4 次被拒 ——
   这时并没有真正的「并发」，但上游的会话窗口还没释放。
3. **会话无法复用。** 把 chunk id 通过 `session_id` / `conversation_id` /
   `previous_response_id` / 各种 `X-Session-*` 请求头回传，上游**每次仍新建
   会话**（返回的 id 每次都不同），窗口照旧被占满。

所以按「400 = 参数错 = 不重试」处理它，代价是：

* 网关直接把这个 400 返回给客户端（`bad_request` 不可重试 → 不换号、不退避）；
* 客户端（Claude Code 一类）看到 400 就**反复重试**，每次重试再撞一次；
* 而换号其实**是有用的** —— 会话是**按账号**计的，换一个号就是另一个窗口。

因此它必须归入 `rate_limit`（可重试 + 换号 + 短冷却），而不是 `bad_request`。
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

from . import endpoints, signer

logger = logging.getLogger("admin.platform.codearts.client")

#: 帧间隔上限（socket read timeout），不是总时长 —— 深度思考模型会在
#: 静默期想几分钟才吐第一帧，按 120s 掐会把「正常工作中」误判成超时。
DEFAULT_TIMEOUT = 600.0

#: 上报哨兵的 kind
SENTINEL_KINDS = frozenset({"auth", "quota_exceeded", "network",
                            "rate_limit", "signature_invalid"})

#: 上游「凭证不可用」的错误码（APIG 网关层）
_AUTH_CODES = frozenset({"APIG.0301", "APIG.0302", "APIGW.0301"})
#: 上游「限流」
_RATE_CODES = frozenset({"APIG.0308", "APIGW.0308"})
#: 上游「并发会话数超限」（实测 2026-11-04）。
#:
#: 上游用 **HTTP 400** 表达它，但语义是限流而不是参数错：
#: `{"error_code":"TM.00001041","error_msg":"并发会话数已达上限(3个)，请关闭部分会话后重试。"}`
#:
#: 它按**账号**计数，且会话在请求结束后仍滞留约 30 秒（详见模块 docstring）。
#: 所以处置必须是「退避 + 换号」—— 换号之所以有用，正是因为会话按账号分窗口。
_CONCURRENCY_CODES = frozenset({"TM.00001041"})


# ---------------------------------------------------------------------------
# 请求体归一化（实测 2026-11-04）
# ---------------------------------------------------------------------------
#: CodeArts 上游只认 system / user / assistant / tool。Codex、Mirai 一类
#: 客户端用 OpenAI 较新的 ``developer`` 角色下发系统指令，上游解析不出 role，
#: 于是回一条 **HTTP 200 的 SSE 错误帧**：
#:
#:     {"text":"[DONE]","error_code":"InferHub.001001044.400",
#:      "error_msg":"Message role cannot empty","model":"deepseek-v4.1-flash"}
#:
#: 这帧既没有 choices 也没有 finish_reason。原样透传后客户端只会看到
#: 「Stream ended without finish_reason」并反复重试，网关却记成成功。
_ROLE_ALIASES = {"developer": "system"}


def normalize_body(body: dict) -> dict:
    """把上游不认识的消息角色映射成它认识的；返回新 dict，不改原对象。"""
    messages = body.get("messages")
    if not isinstance(messages, list):
        return body
    out: list = []
    changed = False
    for msg in messages:
        if not isinstance(msg, dict):
            out.append(msg)
            continue
        role = str(msg.get("role") or "").strip().lower()
        mapped = _ROLE_ALIASES.get(role) or role or "user"
        if mapped != str(msg.get("role") or ""):
            item = dict(msg)
            item["role"] = mapped
            out.append(item)
            changed = True
        else:
            out.append(msg)
    return {**body, "messages": out} if changed else body


class UpstreamError(Exception):
    """上游失败（带 kind，供网关决定「换号 / 冷却 / 直接返回」）。"""

    def __init__(self, message: str, *, status: int = 0, code: str = "",
                 kind: str = "http_error", detail: str = ""):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.kind = kind
        self.detail = detail

    def retryable(self) -> bool:
        """值不值得换号重试。

        * `auth` —— 必换（这个号的临时凭证已经不可用）
        * `quota_exceeded` —— 必换（换成还有积分的号）
        * `rate_limit` / `upstream_error` / `network` —— 换号有意义
        * `bad_request` —— **不换**（请求体本身错，换谁都一样）
        """
        return self.kind in ("auth", "quota_exceeded", "rate_limit",
                             "upstream_error", "network", "signature_invalid")

    def to_dict(self) -> dict:
        return {"status": self.status, "code": self.code, "kind": self.kind,
                "message": self.message, "detail": self.detail[:400]}


def _inband_error(chunk: dict) -> "UpstreamError | None":
    """识别「HTTP 200 里包着的错误信封」。

    上游有两种写法：OpenAI 风格 ``{"error": {...}}``，以及华为云风格
    ``{"error_code":..., "error_msg":..., "text":"[DONE]"}``。后者**没有**
    ``error`` 键，旧判断只看 ``chunk.get("error")`` 会整个漏掉，把错误帧
    当正常 chunk 转发（它没有 choices / finish_reason，客户端必然报错重试）。
    正常 chunk 都带非空 ``choices``，据此排除误判。
    """
    if not isinstance(chunk, dict) or chunk.get("choices"):
        return None
    error = chunk.get("error")
    code = str(chunk.get("error_code") or "")
    msg = str(chunk.get("error_msg") or "")
    if isinstance(error, dict):
        code = code or str(error.get("code") or "")
        msg = msg or str(error.get("message") or "")
    elif isinstance(error, str):
        msg = msg or error
    if not (code or msg):
        return None
    # 会话窗口超限与其它路径保持同一口径（rate_limit），其余按业务拒绝处理。
    kind = "rate_limit" if code in _CONCURRENCY_CODES else "upstream_business"
    return UpstreamError(msg or "上游返回业务错误", status=200, code=code,
                         kind=kind,
                         detail=json.dumps(chunk, ensure_ascii=False)[:400])


def note_sentinel(account_id: int, kind: str, *, code: str = "",
                  status: int = 0, detail: str = "") -> None:
    """上报风控哨兵。**绝不抛异常**。"""
    if kind not in SENTINEL_KINDS:
        return
    try:
        from admin import sentinel

        sentinel.note_failure(endpoints.PLATFORM, int(account_id or 0), kind,
                              code=str(code or ""), status=int(status or 0),
                              detail=str(detail or "")[:200])
    except Exception:  # noqa: BLE001
        logger.debug("哨兵上报失败 kind=%s", kind, exc_info=True)


# ---------------------------------------------------------------------------
# 底层请求
# ---------------------------------------------------------------------------
def credential_headers(cred: dict, *, agent_type: str = "") -> dict:
    """构造参与签名的业务头（**不含 Authorization**，由 signer 补）。"""
    headers = {
        "Content-Type": "application/json",
        "X-Security-Token": str(cred.get("security_token") or ""),
        "X-Language": endpoints.LANGUAGE,
    }
    if agent_type:
        headers["Agent-Type"] = agent_type
    return headers


def _classify(status: int, payload: str) -> UpstreamError:
    """把 HTTP 错误映射成带 kind 的 `UpstreamError`。"""
    code = ""
    message = ""
    try:
        data = json.loads(payload) if payload else {}
        if isinstance(data, dict):
            code = str(data.get("error_code") or data.get("code") or "")
            message = str(data.get("error_msg") or data.get("message") or "")
            error = data.get("error")
            if isinstance(error, dict):
                code = code or str(error.get("code") or "")
                message = message or str(error.get("message") or "")
                # OpenAI 风格：`{"error": {"type": "insufficient_quota"}}`
                if error.get("type") == "insufficient_quota":
                    return UpstreamError(
                        message or "积分不足", status=status, code=code or "402",
                        kind="quota_exceeded", detail=payload[:800])
    except (ValueError, TypeError):
        message = payload.strip()[:200]

    if status in (401, 403) or code in _AUTH_CODES:
        kind = "signature_invalid" if "signature" in message.lower() else "auth"
        return UpstreamError(message or "凭证不可用（HTTP %d）" % status,
                             status=status, code=code or "401", kind=kind,
                             detail=payload[:800])
    if status == 402:
        return UpstreamError(message or "积分不足（HTTP 402）", status=status,
                             code=code or "402", kind="quota_exceeded",
                             detail=payload[:800])
    # ⚠️ 必须放在 400 兜底之前：上游用 HTTP 400 表达「并发会话数超限」，
    # 但它的语义是**限流**（按账号计数、会话滞留 ~30s），不是「参数错」。
    # 归成 bad_request 的后果是「不换号、不退避、直接把 400 抛给客户端」，
    # 客户端于是反复重试、每次再撞一次；而换号本来是有用的（换号=换窗口）。
    if code in _CONCURRENCY_CODES:
        return UpstreamError(
            message or "上游并发会话数已达上限",
            status=status, code=code, kind="rate_limit",
            detail=payload[:800])
    if status == 429 or code in _RATE_CODES:
        return UpstreamError(message or "上游限流（HTTP 429）", status=status,
                             code=code or "429", kind="rate_limit",
                             detail=payload[:800])
    if status >= 500:
        return UpstreamError(message or "上游错误（HTTP %d）" % status,
                             status=status, code=code or str(status),
                             kind="upstream_error", detail=payload[:800])
    return UpstreamError(message or "请求被上游拒绝（HTTP %d）" % status,
                         status=status, code=code or str(status),
                         kind="bad_request", detail=payload[:800])


def request_json(cred: dict, path: str, *, method: str = "GET",
                 body: dict | None = None, account_id: int = 0,
                 agent_type: str = "", timeout: float = 60.0) -> dict:
    """签名并发一次请求，返回解析后的 JSON。

    `body` 会被**只序列化一次**：签名用的字节与实际发送的字节必须是同一份，
    否则 body hash 对不上（表现就是 `verify ak sk signature fail`）。
    """
    url = endpoints.url(path)
    payload = ""
    data = None
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        data = payload.encode("utf-8")
    headers = signer.sign(method, url, credential_headers(cred, agent_type=agent_type),
                          payload if method != "GET" else "",
                          str(cred.get("access_key") or ""),
                          str(cred.get("secret_key") or ""))
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            text = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            pass
        error = _classify(exc.code, detail)
        note_sentinel(account_id, error.kind, code=error.code,
                      status=error.status, detail=error.detail)
        raise error from exc
    except Exception as exc:  # noqa: BLE001 - 网络层
        note_sentinel(account_id, "network", detail=str(exc)[:200])
        raise UpstreamError("网络失败", kind="network",
                            detail=str(exc)[:200]) from exc

    if not text.strip():
        return {}
    try:
        value = json.loads(text)
    except ValueError as exc:
        raise UpstreamError("返回非 JSON", kind="bad_response",
                            detail=text[:300]) from exc
    if isinstance(value, dict):
        inband = _inband_error(value)
        if inband is not None:
            raise inband
    return value if isinstance(value, dict) else {"data": value}


# ---------------------------------------------------------------------------
# 对话
# ---------------------------------------------------------------------------
def chat_completions(cred: dict, body: dict, *, account_id: int = 0) -> dict:
    """非流式对话。"""
    payload = normalize_body(dict(body))
    payload["stream"] = False
    return request_json(cred, endpoints.CHAT, method="POST", body=payload,
                        account_id=account_id, timeout=DEFAULT_TIMEOUT)


def chat_stream(cred: dict, body: dict, *, account_id: int = 0,
                stats: dict | None = None):
    """流式对话：逐帧解析上游 SSE，产出 `dict`（已去掉 `data:` 前缀）。

    `stats`（可选）会被写入 `ttfb_ms` / `total_ms`：首个**内容帧**到达时间与
    结束时间。只有把首帧单独记下来，TPS 才能按纯解码时长算 —— 拿总耗时
    当 TTFB 会让 TPS 差一个量级（这个坑 Loomy 那边踩过）。
    """
    url = endpoints.url(endpoints.CHAT)
    payload = normalize_body(dict(body))
    payload["stream"] = True
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    headers = signer.sign("POST", url, credential_headers(cred), text,
                          str(cred.get("access_key") or ""),
                          str(cred.get("secret_key") or ""))
    request = urllib.request.Request(url, data=text.encode("utf-8"),
                                     headers=headers, method="POST")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    started = time.perf_counter()
    try:
        response = opener.open(request, timeout=DEFAULT_TIMEOUT)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            pass
        error = _classify(exc.code, detail)
        note_sentinel(account_id, error.kind, code=error.code,
                      status=error.status, detail=error.detail)
        raise error from exc
    except Exception as exc:  # noqa: BLE001
        note_sentinel(account_id, "network", detail=str(exc)[:200])
        raise UpstreamError("网络失败", kind="network",
                            detail=str(exc)[:200]) from exc

    def generator():
        first_at = None
        last_at = None
        try:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line or line.startswith(":"):
                    continue
                if line.startswith("data:"):
                    line = line[5:].strip()
                if not line:
                    continue
                if line == "[DONE]":
                    break
                try:
                    chunk = json.loads(line)
                except ValueError:
                    continue
                # 上游把错误也塞进 200 的流里：既有 `{"error": {...}}`，
                # 也有 `{"error_code":..., "error_msg":..., "text":"[DONE]"}`。
                # 后者没有 error 键，旧判断会漏掉并把它当正常 chunk 转发。
                inband = _inband_error(chunk)
                if inband is not None:
                    logger.warning("CodeArts 流内错误 code=%s msg=%s",
                                   inband.code, inband.message[:200])
                    raise inband
                if isinstance(chunk, dict):
                    choices = chunk.get("choices") or []
                    if any((choice.get("delta") or {}).get("content")
                           or (choice.get("delta") or {}).get("reasoning_content")
                           for choice in choices if isinstance(choice, dict)):
                        now = time.perf_counter()
                        if first_at is None:
                            first_at = now
                            if stats is not None:
                                stats["ttfb_ms"] = int((first_at - started) * 1000)
                        # 解码段终点取**最后一个 token**，而不是生成器收尾：
                        # 收尾还包含 [DONE] / usage 帧的网络时间。
                        last_at = now
                yield chunk
        finally:
            if stats is not None:
                stats.setdefault("ttfb_ms", 0)
                stats["total_ms"] = int(((last_at or time.perf_counter())
                                         - started) * 1000)
            try:
                response.close()
            except Exception:  # noqa: BLE001
                pass

    return generator()


# ---------------------------------------------------------------------------
# 模型目录
# ---------------------------------------------------------------------------
def fetch_models(cred: dict, *, account_id: int = 0) -> list[dict]:
    """拉内置模型目录，转成 OpenAI `/v1/models` 条目。

    `Agent-Type: PromptCenter` 是硬要求（缺了 400「请求头Agent-Type为空」），
    所以这里固定传，不交给调用方记得。
    """
    data = request_json(cred, endpoints.MODELS, account_id=account_id,
                        agent_type=endpoints.AGENT_TYPE)
    raw = data.get("builtinModels")
    if not isinstance(raw, list):
        raw = ((data.get("result") or {}).get("models")
               if isinstance(data.get("result"), dict) else None)
    out: list[dict] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("model_id") or item.get("model_name") or "")
        if not model_id:
            continue
        category = str(item.get("model_category") or "")
        out.append({
            "id": model_id,
            "object": "model",
            "created": 0,
            "owned_by": endpoints.PLATFORM,
            "name": str(item.get("model_name") or model_id),
            "display_name": str(item.get("model_name") or model_id),
            "description": str(item.get("model_desc") or ""),
            "context_window": int(item.get("context_window") or 0),
            "max_tokens": int(item.get("max_tokens") or 0),
            # 「图片理解」= 多模态；其余按文本处理
            "capabilities": {
                "vision": category == "图片理解",
                "text": True,
            },
            "enabled": bool(item.get("enable", True)),
            "category": category,
        })
    out.sort(key=lambda row: row["id"])
    return out


# ---------------------------------------------------------------------------
# 额度
# ---------------------------------------------------------------------------
def fetch_quota(cred: dict, *, account_id: int = 0) -> dict:
    """拉积分额度（`snap-manager/v1/statistics/plugin`）。

    ## 口径（实测）

    ```json
    {"metrics": [
       {"name": "usageTotalPackageCredit",
        "package_credit_amount": 4600.0,
        "package_credit_used": 0.0,
        "package_credit_remain": 4600.0,
        "package_credit_expiring_amount": 100.0},
       ...],
     "package": {"package_name_cn": "体验版", "spec_code": "...trial"},
     "start_date": "2026-11-04", "end_date": "2026-12-05"}
    ```

    `usageTotalPackageCredit` 是**总额**（基础包 + 赠送包 + 按需），
    直接拿它的 remain 当「可用积分」；同时把各分包也留下来，
    概览卡片要显示「其中基础 500 / 赠送 4100」这类拆分。
    """
    data = request_json(cred, endpoints.QUOTA, account_id=account_id)
    metrics = data.get("metrics") if isinstance(data.get("metrics"), list) else []
    by_name: dict[str, dict] = {}
    for metric in metrics:
        if isinstance(metric, dict) and metric.get("name"):
            by_name[str(metric["name"])] = metric

    def _num(metric: dict | None, key: str) -> float:
        value = (metric or {}).get(key)
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    total = by_name.get("usageTotalPackageCredit") or {}
    basic = by_name.get("usageBasicPackageCredit") or {}
    bonus = by_name.get("usageBonusPackageCredit") or {}
    ondemand = by_name.get("usageOnDemandPackageCredit") or {}
    tokens = by_name.get("usageTokenChatMessages") or {}
    package = data.get("package") if isinstance(data.get("package"), dict) else {}

    remain = _num(total, "package_credit_remain")
    amount = _num(total, "package_credit_amount")
    if not amount:
        # 没有 total 时用分包求和兜底，避免「额度全显示 0」
        amount = sum(_num(m, "package_credit_amount")
                     for m in (basic, bonus, ondemand))
        remain = remain or sum(_num(m, "package_credit_remain")
                               for m in (basic, bonus, ondemand))

    return {
        "remain": int(round(remain)),
        "total": int(round(amount)),
        "used": int(round(_num(total, "package_credit_used"))),
        "expiring": int(round(_num(total, "package_credit_expiring_amount"))),
        "basic_remain": int(round(_num(basic, "package_credit_remain"))),
        "basic_total": int(round(_num(basic, "package_credit_amount"))),
        "bonus_remain": int(round(_num(bonus, "package_credit_remain"))),
        "bonus_total": int(round(_num(bonus, "package_credit_amount"))),
        "ondemand_remain": int(round(_num(ondemand, "package_credit_remain"))),
        "tokens": int(_num(tokens, "usage_token_num")),
        "requests": int(_num(tokens, "package_token_amount")),
        "package_name": str(package.get("package_name_cn")
                            or package.get("package_name_en") or ""),
        "spec_code": str(package.get("spec_code") or ""),
        "start_date": str(data.get("start_date") or ""),
        "end_date": str(data.get("end_date") or ""),
        "unit": "credits",
        "raw": data,
    }


def fetch_identity(cred: dict, *, account_id: int = 0) -> dict:
    """`/v5/caller-identity` —— 探活用（签名通了就说明凭证还活着）。"""
    return request_json(cred, endpoints.IDENTITY, account_id=account_id)
