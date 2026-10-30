"""Loomy HTTP 客户端。

## 上游形态（实测 2026-10-28）

    GET  {base}/models
      → {"object":"list","data":[{id, reasoning_efforts, default..., }]}

    POST {base}/chat/completions
      → {"id","object":"chat.completion","model",
         "choices":[{"message":{"role","content","reasoning_content"},
                     "finish_reason"}],
         "usage":{"prompt_tokens","completion_tokens","total_tokens",
                  "points_consumed"}}

## 两个必须处理的点

1. **双 token 头 + traceparent** —— 缺 traceparent 会挂死到超时
2. `usage.points_consumed` 是计费字段，要带回给上层记账
3. `reasoning_content` 与 `content` 分开（思考内容不能拼进正文）
"""
from __future__ import annotations

import json
import time
import logging
import re
import urllib.error
import urllib.request

from . import endpoints

logger = logging.getLogger("admin.platform.loomy.client")
DEFAULT_TIMEOUT = 120.0

#: 哪些 kind 上报哨兵
SENTINEL_KINDS = frozenset({"auth", "quota_exceeded", "network",
                            "signature_invalid", "upstream_business"})

#: 上游业务码 -> 处置
#: `100002` 在客户端源码里归类为 AUTH_ERROR_CODES（认证失效）
BIZ_AUTH_CODES = frozenset({"020002", "100002"})
#: 成功码（Loomy 用六位零）
BIZ_OK_CODES = frozenset({"", "0", "200", "000000"})
#: 业务提示但不算故障（如「当前未切换到团队」）
BIZ_SOFT_CODES = frozenset({"100001"})


class UpstreamError(Exception):
    def __init__(self, message: str, *, status: int = 0, code: str = "",
                 kind: str = "http_error", detail: str = ""):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.kind = kind
        self.detail = detail

    def retryable(self) -> bool:
        """这次失败值不值得换号重试。

        ## 为什么 400 也要重试

        DashScope 在**瞬时状态**下会回 400 `invalid_request_error`，
        消息里自己就写着「请求有误，请重试；若多次出现请更新应用版本」：

            {"code":400,"type":"invalid_request_error",
             "metadata":{"provider_name":"alibaba-dashscope"}}

        同一请求体过几秒再发就成功（实测无法稳定复现）。原逻辑把 400
        当「参数错、重试无意义」，直接放弃并抛给客户端 —— 表现就是
        「同一个模型明明能用，偶尔整次请求直接 400 失败」。

        但要**排除真正由我校验错误的 400**（鉴权类 401/403 已单独分类）：
        这里只把「上游明确建议重试」的 400 视为可重试。
        """
        if self.status in (408, 429) or self.status >= 500:
            return True
        if self.status == 400:
            detail = str(self.detail or "")
            return ("请重试" in detail or "retry" in detail.lower()
                    or "try again" in detail.lower())
        return False

    def to_dict(self) -> dict:
        return {"message": self.message, "status": self.status,
                "code": self.code, "kind": self.kind,
                "detail": self.detail[:300]}


def note_sentinel(realm: str, account_id: int, kind: str, *,
                  code: str = "", status: int = 0, detail: str = "") -> None:
    """上报风控哨兵。**绝不抛异常**。"""
    if kind not in SENTINEL_KINDS:
        return
    try:
        from admin import sentinel

        sentinel.note_failure("loomy", int(account_id or 0), kind,
                              code=str(code or ""), status=int(status or 0),
                              detail=str(detail or "")[:200])
    except Exception:  # noqa: BLE001
        logger.debug("哨兵上报失败 kind=%s", kind, exc_info=True)


#: 最近一次请求的分段耗时（毫秒）。`_bill` 把它们分别记进 usage_logs，
#: 这样 TPS 才能按「纯解码耗时」算，而不是被首包等待稀释。
#:
#: ⚠️ **必须用 contextvars 按请求隔离**：原来用模块级全局变量，
#: 并发时后一个请求会覆盖前一个的时间，`_bill` 于是拿到别的时间戳，
#: `latency - ttfb` 变成几毫秒，TPS 算出 6 万~26 万这种荒谬值。
import contextvars as _ctxvars

LAST_TTFB_MS: "_ctxvars.ContextVar[int]" = _ctxvars.ContextVar(
    "loomy_ttfb_ms", default=0)
LAST_TOTAL_MS: "_ctxvars.ContextVar[int]" = _ctxvars.ContextVar(
    "loomy_total_ms", default=0)


def _request(url: str, *, method: str = "GET", body: dict | None = None,
             token: str = "", timeout: float = DEFAULT_TIMEOUT,
             account_id: int = 0, realm: str = "",
             headers: dict | None = None) -> dict:
    """通用请求。

    `headers` 显式给出时**完全覆盖**默认鉴权头 ——
    积分接口只要 `token` 一个头，不能混入 chat 那套双头。
    """
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=data, method=method.upper())
    effective = headers if headers is not None else endpoints.auth_headers(token)
    for key, value in effective.items():
        req.add_header(str(key), str(value))
    if data:
        req.add_header("Content-Type", "application/json")

    start_at = time.perf_counter()
    first_byte_at = None
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            # `urlopen` 返回时**响应头已经到了**，这一步的时间就是可测的
            # 首字节时间（TTFT 的近似值）。之后 `read()` 才是真正的等待
            # 传输/生成，两者相减就是**纯解码耗时** —— 有它才能算出
            # 与 DeepSeek Harness 同口径的 TPS（参考 `bench_router`）。
            first_byte_at = time.perf_counter()
            text = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        payload = ""
        try:
            payload = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            pass
        kind = ("auth" if exc.code in (401, 403)
                else "rate_limit" if exc.code == 429
                else "upstream_error" if exc.code >= 500 else "http_error")
        note_sentinel(realm, account_id, kind, code=str(exc.code),
                      status=exc.code, detail=payload)
        raise UpstreamError("请求失败（HTTP %d）" % exc.code, status=exc.code,
                            code=str(exc.code), kind=kind,
                            detail=payload) from exc
    except Exception as exc:  # noqa: BLE001
        note_sentinel(realm, account_id, "network", detail=str(exc)[:200])
        raise UpstreamError("网络失败", status=0, kind="network",
                            detail=str(exc)[:200]) from exc

    # 分段耗时写进 contextvar（按请求/协程隔离，并发安全）
    if first_byte_at is not None:
        LAST_TTFB_MS.set(int((first_byte_at - start_at) * 1000))
        LAST_TOTAL_MS.set(int((time.perf_counter() - start_at) * 1000))

    if not text.strip():
        return {}

    try:
        data = json.loads(text)
    except ValueError as exc:
        raise UpstreamError("返回非 JSON", status=0, kind="bad_response",
                            detail=text[:200]) from exc
    if not isinstance(data, dict):
        return {"data": data}

    # 业务码检查：Loomy 的失败也可能是 HTTP 200 + code
    #
    # ⚠️ 成功码是 **`000000`**（六位零），不是 `0` / `200`。
    # 漏了它会把「成功」当失败，抛出的错误信息就是「成功」两字。
    code = str(data.get("code") or "")
    if code and code not in BIZ_OK_CODES:
        kind = "auth" if code in BIZ_AUTH_CODES else "upstream_business"
        message = str(data.get("desc") or data.get("message") or "上游业务错误")
        # `100001` 这类是业务提示（如「当前未切换到团队」），不算故障
        if code in BIZ_SOFT_CODES:
            return data
        note_sentinel(realm, account_id, kind, code=code, status=200,
                      detail=message)
        raise UpstreamError(message, status=200, code=code, kind=kind,
                            detail=json.dumps(data, ensure_ascii=False)[:300])
    return data


# ---------------------------------------------------------------------------
# 接口
# ---------------------------------------------------------------------------
#: 显示名末尾的倍率：` x0.5`
_NAME_SUFFIX_MULT_RE = re.compile(r"\s+[xX](\d+(?:\.\d+)?)\s*$")
#: 末尾括号（半角或全角）
_NAME_PAREN_RE = re.compile(r"^(.*?)\s*(?:[（(]([^（）()]*)[）)])\s*$")
#: 括号里恰好是 `x0.5`
_PAREN_MULT_RE = re.compile(r"^[xX](\d+(?:\.\d+)?)$")


def _split_display_multiplier(value: str) -> tuple[str, str]:
    """从显示名拆出 `(纯名称, 倍率)`。

    照抄客户端 `splitModelDisplayName` 的逻辑（`app.asar` 实测）：

        "DeepSeek V4.1 Flash(x2.0)"        -> ("DeepSeek V4.1 Flash", "2.0")
        "MiniMax M3 （x4.0）"               -> ("MiniMax M3", "4.0")
        "Spark-X2.6 正式版(限时免费)(x0.0)"  -> ("Spark-X2.6 正式版", "0.0")
        "Kimi k2.6 x6.5"                   -> ("Kimi k2.6", "6.5")

    注意两种情况：
      * 全角括号 `（）` 与半角 `()` 混用
      * 两层后缀：徽标 + 倍率，如 `(限时免费)(x0.0)`
    """
    original = str(value or "").strip()
    if not original:
        return "", ""
    name = original
    multiplier = ""
    for _ in range(2):
        match = _NAME_SUFFIX_MULT_RE.search(name)
        if match:
            multiplier = match.group(1)
            name = name[:match.start()].strip()
            continue
        parent = _NAME_PAREN_RE.match(name)
        if not parent:
            break
        next_name = (parent.group(1) or "").strip()
        next_badge = (parent.group(2) or "").strip()
        if not next_name or not next_badge:
            break
        name = next_name
        mult_match = _PAREN_MULT_RE.match(next_badge)
        if mult_match:
            multiplier = mult_match.group(1)
    return (name or original), multiplier



#: 图像模型（不走 `/chat/completions`，对它们发对话会 404）
IMAGE_MODEL_MARKERS = ("image", "seedream", "hy-image", "draw", "vision-gen")


def _is_chat_model(model_id: str, item: dict) -> bool:
    """该模型能否用于 `/chat/completions`。

    判据（实测）：
      * 有 `reasoning_efforts` → 文本模型
      * 名字含 image/seedream → 图像模型，不走 chat
    """
    lowered = model_id.lower()
    if any(marker in lowered for marker in IMAGE_MODEL_MARKERS):
        return False
    efforts = item.get("reasoning_efforts")
    if isinstance(efforts, list) and efforts:
        return True
    # 没有 efforts 但有 max_tokens 之类也当文本模型
    return bool(item.get("context_length") or item.get("max_tokens"))


def fetch_models(token: str, realm: str = "cn",
                 account_id: int = 0, *, chat_only: bool = True) -> list[dict]:
    """模型清单 → OpenAI `/v1/models` 形状。

    `chat_only=True` 时**只返回能走 /chat/completions 的模型** ——
    图像模型（`Hy-Image-*` / `doubao-seedream-*` / `qwen-image-*`）
    对对话端点会返回 404，暴露给客户端只会让用户选到必然失败的模型。
    """
    data = _request(endpoints.base_of(realm) + endpoints.MODELS_PATH,
                    token=token, account_id=account_id, realm=realm)
    items = data.get("data") or data.get("models") or []
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or item.get("name") or "")
        if not model_id:
            continue
        if chat_only and not _is_chat_model(model_id, item):
            continue
        # ★ 上游的 `name` 里带倍率：`"DeepSeek V4.1 Flash(x2.0)"`。
        # 不能覆盖成 model_id —— 那会把倍率信息丢掉（实测踩过：
        # 因此误判成「Loomy 没有倍率概念」）。
        display_name = str(item.get("displayName")
                           or item.get("display_name")
                           or item.get("name")
                           or model_id)
        clean_name, multiplier_text = _split_display_multiplier(display_name)
        out.append({
            "id": model_id,
            "object": "model",
            "created": 0,
            "owned_by": "loomy",
            # 保留上游原名（含倍率后缀），前端/同步层都能看到
            "name": display_name,
            "x_loomy": {
                "display_name": display_name,
                "clean_name": clean_name,
                # "2.0" / "0.0" / "" —— 给同步层直接用
                "multiplier": multiplier_text,
                "reasoning_efforts": item.get("reasoning_efforts") or [],
                "default_reasoning_effort": item.get("default_reasoning_effort"),
                # 能力字段：`describe_model` 靠这些拼描述
                "context_length": item.get("context_length"),
                "max_output_tokens": item.get("max_output_tokens"),
                "capabilities": item.get("capabilities") or {},
                "type": item.get("type") or "",
                "is_default_model": bool(item.get("is_default_model")),
            },
        })
    return out


#: 实测「思考链会吃满 max_tokens 且正文为空」的模型。
#:
#: `/no_think` 前缀 + `enable_thinking=false` 对它们**都无效**
#: （实测 spark-x2.6 仍然 finish=length + content=''）。
#: 所以：默认不把它们作为首选（选号时靠后），并给调用方一个明确标记。
THINKING_HUNGRY = frozenset({"spark-x2.6", "spark-x", "auto"})


def is_thinking_hungry(model_id: str) -> bool:
    return model_id in THINKING_HUNGRY


def chat_completions(token: str, realm: str, body: dict, *,
                     account_id: int = 0, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """对话（非流式）。"""
    return _request(endpoints.base_of(realm) + endpoints.CHAT_PATH,
                    method="POST", body=body, token=token,
                    timeout=timeout, account_id=account_id, realm=realm)


def chat_stream(token: str, realm: str, body: dict, *,
                account_id: int = 0, timeout: float = DEFAULT_TIMEOUT,
                stats: dict | None = None):
    """对话（**真流式**）：逐行产出上游 SSE 的原始 `data:` 负载（dict）。

    ## 为什么必须有这个函数

    原来 Loomy 走 `_request` → `resp.read()`，那是**一次性读完整个响应**。
    上游明明支持 SSE（`stream: true` 返回 `text/event-stream`，实测首帧
    1.6 秒就到），我们却等它全部生成完才把内容吐给客户端 ——
    看到的是「先干等几十秒，然后一大坨文字瞬间出现」，
    并且 `TTFT` 被记成「全部生成完的时间」，TPS 也完全算不出来。

    这里保持流式：上游来一帧就解析一帧、yield 一帧。网关上层据此
    边转边发，客户端才能像直连一样看到逐字输出。

    产出的是**上游原始 chunk**（已经 `json.loads` 过），不做协议转换 ——
    转换交给 `gateway._sse_from_chunks`，职责分离。
    """
    url = endpoints.base_of(realm) + endpoints.CHAT_PATH
    data = json.dumps(body, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    for key, value in endpoints.auth_headers(token).items():
        req.add_header(str(key), str(value))
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "text/event-stream")

    start_at = time.perf_counter()
    first_byte_at = None
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        payload = ""
        try:
            payload = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            pass
        kind = ("auth" if exc.code in (401, 403)
                else "rate_limit" if exc.code == 429
                else "upstream_error" if exc.code >= 500 else "http_error")
        note_sentinel(realm, account_id, kind, code=str(exc.code),
                      status=exc.code, detail=payload)
        raise UpstreamError("请求失败（HTTP %d）" % exc.code, status=exc.code,
                            code=str(exc.code), kind=kind,
                            detail=payload) from exc
    except Exception as exc:  # noqa: BLE001
        note_sentinel(realm, account_id, "network", detail=str(exc)[:200])
        raise UpstreamError("网络失败", status=0, kind="network",
                            detail=str(exc)[:200]) from exc

    def gen():
        first_at = None       # 首个 chunk（含 reasoning）到达
        first_content_at = None   # **首个正文**到达（TTFT 的准确定义）
        last_content_at = None    # **最后一个正文**到达（解码段终点）
        last_at = None
        try:
            for raw_line in resp:
                line = raw_line.decode("utf-8", "replace").strip()
                if not line:
                    continue
                if line.startswith("event:"):
                    continue
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    chunk = json.loads(payload)
                except ValueError:
                    continue
                if not isinstance(chunk, dict):
                    continue
                now = time.perf_counter()
                if first_at is None:
                    first_at = now
                last_at = now
                # 只有**正文**才算解码进度：思考链（reasoning_content）
                # 属于「首包等待」的一部分，DSH 的 ttft 也是到第一个
                # 正文 token 才截止。
                _has_text = False
                for _c in (chunk.get("choices") or []):
                    if not isinstance(_c, dict):
                        continue
                    _d = _c.get("delta") or {}
                    if _d.get("content") or _d.get("tool_calls"):
                        _has_text = True
                        break
                if _has_text:
                    if first_content_at is None:
                        first_content_at = now
                    last_content_at = now
                yield chunk
        finally:
            try:
                resp.close()
            except Exception:  # noqa: BLE001
                pass
            # 分段耗时写 contextvar（并发安全）。
            # `LAST_TTFB_MS` = **首个正文字**到达（TTFT）
            # `LAST_TOTAL_MS` = **最后一个正文字**到达（解码段终点）
            # 两者相减才是纯解码耗时；用首帧/末帧会把思考链算进解码段。
            _ttft_at = first_content_at or first_at
            _end_at = last_content_at or last_at
            _ttft = (int((_ttft_at - start_at) * 1000)
                     if _ttft_at is not None else 0)
            _end = (int((_end_at - start_at) * 1000)
                    if _end_at is not None else 0)
            if _ttft_at is not None:
                LAST_TTFB_MS.set(_ttft)
            if _end_at is not None:
                LAST_TOTAL_MS.set(_end)
            # **同时写进调用方传进来的 dict**：`_bill` 在另一个协程里跑，
            # contextvar 拿不到这里的值（`ttfb == latency` 就是这么来的）。
            # 显式传值最可靠。
            if isinstance(stats, dict):
                stats["ttfb_ms"] = _ttft
                stats["total_ms"] = _end

    return gen()


def fetch_points_balance(token: str, realm: str = "cn",
                         account_id: int = 0) -> dict:
    """积分余额（**真实可用余额在这里**）。

    ## 关键：走 chat 的同一个 base，但路径与鉴权都不同

        GET {base}/points/balance
        headers: {"token": <session>}          ← 不是 Authorization: Bearer

    返回（实测）：
        {"code":"000000","data":{
           "balance": 5500,          # 基础余额
           "dailyBalance": 4960,     # 当日额度（每日重置）
           "availableBalance": 10460 # ★ 可用总额 = 两者之和
        }}

    `availableBalance` 才是看到的「余额」，不能只看 `balance`。
    """
    url = endpoints.base_of(realm) + endpoints.POINTS_BALANCE_PATH
    data = _request(url, token="", timeout=30.0,
                    account_id=account_id, realm=realm,
                    headers=endpoints.points_headers(token))
    payload = data.get("data") if isinstance(data.get("data"), dict) else data
    balance = int(payload.get("balance") or 0)
    daily = int(payload.get("dailyBalance") or 0)
    available = int(payload.get("availableBalance") or (balance + daily))
    return {"balance": balance, "daily_balance": daily,
            "available": available, "unit": "points",
            "raw": payload}


def first_login(token: str, realm: str = "cn",
                account_id: int = 0) -> dict:
    """每日签到：领取当日赠送额度（5000 积分）。

    实测：`POST /points/first-login`，鉴权只要 `token` 头。

    响应：
        {"code":"000000","data":{
           "alreadyProcessed": true,      # 今天已领过（幂等，不是错误）
           "currentBalance": 10500,       # 可用总额
           "permanentBalance": 5500,      # 长期积分（不清零）
           "dailyBalance": 5000,          # 当日额度（每天重置）
           "dailyQuota": 5000,            # 当日上限
           "dailyConsumed": 0,
           "dailyCycleDate": "2026-10-29"
        }}

    ## 为什么定时任务每天调一次就够

    这个接口**幂等**：当天第一次调用触发发放，之后返回
    `alreadyProcessed: true`。不需要先判断「今天领过没」。

    ## 消耗顺序

    活动/兑换码积分**优先**消耗，然后才是普通长期积分。
    所以 `permanentBalance` 不会因每日刷新而清零。
    """
    url = endpoints.base_of(realm) + endpoints.POINTS_FIRST_LOGIN_PATH
    return _request(url, method="POST", body={}, token="",
                    timeout=30.0, account_id=account_id, realm=realm,
                    headers=endpoints.points_headers(token))


def fetch_points_records(token: str, realm: str = "cn", *, page: int = 1,
                         page_size: int = 20, account_id: int = 0) -> dict:
    """积分流水（按页）。"""
    url = "%s%s?pageNo=%d&pageSize=%d" % (
        endpoints.base_of(realm), endpoints.POINTS_RECORDS_PATH,
        max(1, int(page)), max(1, min(int(page_size), 100)))
    data = _request(url, token="", timeout=30.0,
                    account_id=account_id, realm=realm,
                    headers=endpoints.points_headers(token))
    return data.get("data") if isinstance(data.get("data"), dict) else {}


def fetch_quota(token: str, realm: str = "cn", account_id: int = 0) -> dict:
    """额度。

    Loomy 没有独立的额度接口 —— 计费信息在每轮对话的
    `usage.points_consumed` 里。所以这里的「额度」由本网关账本累计，
    上游只提供一个「是否能调通」的探针。

    返回 `{"reachable": bool, "models": n}`。
    """
    models = fetch_models(token, realm, account_id)
    return {"reachable": True, "models": len(models),
            "unit": "points", "note": "Loomy 无独立额度接口，计费见每轮 points_consumed"}


def refresh_token(refresh: str, realm: str = "cn") -> dict:
    """Loomy **没有 refresh 机制**（`/session/refresh` 是 404）。

    显式抛错，让上层走「请重新登录」。
    """
    raise UpstreamError(
        "Loomy 无 token 续期机制，session 失效需在客户端重新登录",
        status=401, kind="auth")
