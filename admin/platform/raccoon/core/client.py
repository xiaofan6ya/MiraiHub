"""小浣熊 HTTP 客户端。

## 与其它平台的关键差异

小浣熊所有接口都是 `{"code": 0, "message": "success", "data": {...}}` 信封，
**HTTP 200 也可能代表业务失败**（例如 token 过期返回 200 + code=401xx）。
所以这里统一做信封拆解，把业务错误转成 `UpstreamError`，避免上层拿到
一个「成功但 data 为空」的假结果。

## 错误分类

    kind="auth"      凭证失效 → 上层把账号标 disabled，不再选它
    kind="quota"     额度不足 → 冷却一段时间
    kind="rate"      限流     → 短冷却后重试
    kind="model"     模型不存在/不可用
    kind="upstream"  其它上游错误

分类口径沿用 Loomy 的 `UpstreamError`，这样 admin 的哨兵与冷却逻辑
不需要为小浣熊写特例。
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time

import httpx

from admin.platform.raccoon.core import endpoints

logger = logging.getLogger("admin.platform.raccoon.client")

# read=600s：帧间隔上限而非总时长 —— 上游思考静默期（3~4 分钟）不能被掐
# （见 admin/platform/loomy/core/client.py 同款注释）
TIMEOUT = httpx.Timeout(connect=15.0, read=600.0, write=60.0, pool=15.0)

#: 业务码 → 错误类别
_AUTH_CODES = {401, 403, 40101, 40102, 40103, 100002}
_QUOTA_CODES = {402, 40201, 40202, 40203}
_RATE_CODES = {429, 42901, 42902}
_MODEL_CODES = {404, 40401, 4023}
#: `200901 iam_confirm_required` —— **扫码授权拿到的会话还没被确认**。
#:
#: 实测（2026-11-04）：同一个 JWT 打 `/model_catalog`、`/points/balance`、
#: `/entitlement_info` 一律返回
#: `400 {"code":200901,"message":"iam_confirm_required"}`；
#: 而客户端 `auth.json` 里那张票（另一个 `sid`）同样的请求是 200。
#: 也就是说**扫码得到的 IAM 会话缺一步确认，业务接口全部不可用**。
#:
#: 不能归进 `_AUTH_CODES`：那会让 `sync_quota` 把它当「凭证失效」直接
#: `disabled`，而这其实是「换个入口取凭证」就能解决的事，禁用反而更难理解。
_CONFIRM_CODES = {200901}


def _confirm_hint() -> str:
    """`iam_confirm_required` 给人看的解释（两处错误分支共用）。"""
    return ("登录会话未完成 IAM 确认（上游 200901）：扫码得到的会话不能直接调接口，"
            "请在小浣熊客户端登录后用「扫描本机 / 导入文件」入库")


def _redact(text: str, limit: int = 500) -> str:
    """把可能承载凭证的片段打码，再截断。

    上游错误体、httpx 异常原文都可能回显 `Authorization: Bearer <JWT>`。
    这些文本只进服务端日志（`detail`），但日志本身也是泄漏面 ——
    运维会翻日志、日志会被打包发给别人。所以在这里统一抹掉。
    """
    import re

    text = str(text or "")
    # Authorization / token / refresh_token / 裸 JWT 字面量
    text = re.sub(r"(?i)(authorization\s*[:=]\s*)(\S+)", r"\1<redacted>", text)
    text = re.sub(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-]+", r"\1 <redacted>", text)
    text = re.sub(r'\b(access_?token|refresh_?token|token|session|password)"?\s*[:=]\s*"?[^",}\s]+',
                  r"\1=<redacted>", text)
    text = re.sub(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]*",
                  "<jwt>", text)
    return text[:limit]


def _code_of(payload) -> int | None:
    """尽力从上游 payload 里取业务码。"""
    if not isinstance(payload, dict):
        return None
    for key in ("code", "biz_code", "errcode"):
        value = payload.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    err = payload.get("error")
    if isinstance(err, dict):
        for key in ("code", "status"):
            try:
                return int(err.get(key))
            except (TypeError, ValueError):
                continue
    return None


class UpstreamError(Exception):
    """上游业务/传输错误（带分类，供冷却与哨兵使用）。

    ## message 与 detail 的分工（安全约束）

    * `message`：给**人**看的短句，**不得包含上游原文**。
      它会进日志、回给 `/v1` 调用方、写进 `accounts.last_err_msg`、进哨兵。
    * `detail`：给**日志/排障**用的上游原文，已过 `_redact()`。
      只由 `gateway._fail` 写日志，**不进响应体**。

    Loomy 就是这个分工；把两者合成一个字段是凭证外泄的主因。
    """

    def __init__(self, message: str, *, status: int = 0, kind: str = "upstream",
                 code: int | None = None, payload=None, detail: str = ""):
        super().__init__(message)
        self.message = message
        self.status = int(status or 0)
        self.kind = kind
        self.code = code
        #: 已脱敏的上游原文（仅日志）
        self.detail = detail
        #: 原始 payload：**全仓无读取点**，保留只为调试时按需检查
        self.payload = payload

    @property
    def retryable(self) -> bool:
        """是否值得换号重试。"""
        return self.kind in ("rate", "upstream")

    def to_dict(self) -> dict:
        return {"message": self.message, "status": self.status,
                "kind": self.kind, "code": self.code}


#: 上游文案里的 `retry after`（`retry after: 9s` / `retry-after: 500ms`）
_RETRY_AFTER_RE = re.compile(
    r"(?:retry[\s-]*after\s*:?\s*)(\d+(?:\.\d+)?)\s*(ms|s)?", re.IGNORECASE)

#: rate 类冷却的夹逼区间：上游给的秒数可能离谱（0 或 9999），
#: 夹到 [5, 600] —— 下界保证真的退避，上界与 auth/quota 的 600 同量级。
_RATE_COOL_MIN_SECONDS = 5
_RATE_COOL_MAX_SECONDS = 600

#: quota 文案的共现判据：`insufficient` 必须与额度语义同现，避免把
#: `context window is insufficient` 这类上下文长度报错判成「额度耗尽」。
_QUOTA_CONTEXT_TOKENS = ("point", "credit", "balance", "quota", "token",
                         "额度", "积分")

#: rate 文案判据（`text` 已 lower，ASCII 项必须小写；中文不受 lower 影响）。
#:
#: 刻意**不收裸 `retry after`** —— 普通重试提示（如「请 retry after 稍后」）
#: 不该被判成限流；只有与 limit/exceed 共现时才认。
_RATE_TEXT_HINTS = ("限流", "过于频繁", "rate limit", "too many requests",
                    "limiter_request_error", "exceed max time",
                    "频率限制", "频率超出", "使用量已超出",
                    "frequency limit", "usage exceeds", "重置")


def retry_after_seconds(text) -> int | None:
    """解析上游文案里的 `retry after` 秒数；解析不到返回 None。

    只解析、**不夹逼**（夹逼在 `gateway._cool_seconds`），便于无依赖单测。
    单位 `ms` 向下取整到秒；`s` 或缺省按秒。
    """
    match = _RETRY_AFTER_RE.search(str(text or ""))
    if match is None:
        return None
    value = float(match.group(1))
    if (match.group(2) or "").lower() == "ms":
        return int(value // 1000)
    return int(value)


def _classify(code: int | None, status: int, message: str) -> str:
    """错误分类。

    ## 为什么不能用子串匹配 `"token"`

    第一版的兜底是 `if "token" in text: return "auth"`。但客户端发错参数时
    最常见的两条报错都含 token：

        "maximum context length is 8192 tokens"     → 被判 auth
        "max_tokens exceeds the limit"              → 被判 auth

    被判 auth 的后果不是「这次请求失败」，而是：
      `note_failure(cool_seconds=600)` + `sync_quota` 里
      `account.status = "disabled"` —— **把健康的号永久禁用**。
    管理员点一次「刷新额度」就会踩。

    所以这里只认**明确的鉴权语义**，不用裸 `token` 子串。
    """
    if code in _AUTH_CODES or status in (401, 403):
        return "auth"
    if code in _CONFIRM_CODES:
        return "confirm"
    if code in _QUOTA_CODES or status == 402:
        return "quota"
    if code in _RATE_CODES or status == 429:
        return "rate"
    if code in _MODEL_CODES or status == 404:
        return "model"
    text = str(message or "").lower()
    # 只认明确的鉴权措辞，不用裸 "token"
    if ("登录已失效" in message or "未授权" in message
            or "unauthorized" in text or "invalid token" in text
            or "invalid_token" in text or "token expired" in text
            or "authentication" in text and "fail" in text):
        return "auth"
    if ("额度" in message or "积分不足" in message or "quota" in text
            or ("insufficient" in text
                and any(token in text for token in _QUOTA_CONTEXT_TOKENS))):
        return "quota"
    if (any(hint in text for hint in _RATE_TEXT_HINTS)
            or ("retry after" in text
                and ("limit" in text or "exceed" in text))):
        return "rate"
    if "model" in text and ("not found" in text or "不存在" in message):
        return "model"
    return "upstream"


def unwrap(payload, *, status: int = 200, context: str = ""):
    """拆 `{code,message,data}` 信封，失败抛 `UpstreamError`。"""
    if not isinstance(payload, dict):
        return payload
    if "code" not in payload:
        return payload
    code = payload.get("code")
    try:
        code_int = int(code)
    except (TypeError, ValueError):
        code_int = None
    if code_int == 0:
        return payload.get("data")
    message = str(payload.get("message") or payload.get("msg") or "上游返回异常")
    kind = _classify(code_int, status, message)
    # 上游 message 进 `detail`（已脱敏），响应侧只给固定的短句 ——
    # 见 `request_json` 里「message/detail 分离」的说明。
    #
    # `iam_confirm_required` 是**唯一**要给出可操作提示的错误：
    # 用户看到「上游返回业务错误」只会反复点刷新，而真实解法是
    # 「换一种入口取凭证」。
    hint = ""
    if kind == "confirm":
        hint = _confirm_hint()
    raise UpstreamError((context + hint) if (kind == "confirm" and context)
                        else (hint or context or "上游返回业务错误"),
                        status=status, kind=kind, code=code_int, payload=payload,
                        detail=_redact("code=%s message=%s" % (code_int, message)))


def request_json(method: str, url: str, *, token: str, realm: str = "cn",
                 json_body=None, params=None, account_id: int | None = None,
                 context: str = ""):
    """发一个请求并返回拆掉信封后的 `data`。

    ## 状态码必须先判，再拆信封

    小浣熊的 LLM 网关是**标准 OpenAI 形态**：出错时返回的是
    `{"error": {"message": ...}}`，**没有 `code` 键**。所以只靠
    `unwrap()` 拆信封会把 401/429/500 原样放行，上层拿到一个
    「200 成功但内容是错误体」的假结果：

      * `_forward` 把它包成 HTTP 200 → 客户端解析 `choices[0]` 失败；
      * 网关侧不重试、不冷却、`error_kind=""`、积分记 0；
      * `fetch_points` 对失效 token 返回全 0 → `apply_quota` **把真实余额清零**。

    实测（2026-11-03）：无效 token 返回 **HTTP 403 + 空 body**，
    空 body 连 JSON 都不是，却一路走到 `apply_quota` 把 `remain`
    从 100 覆盖成 0，账号仍然是 `active` —— 失效号永远标不出来。

    这与 `open_stream()` 的语义必须一致（那边本来就判了状态码）。

    ## message / detail 分离（防凭证外泄）

    `message` 会**同时**进日志、回给 `/v1` 调用方、并落进
    `accounts.last_err_msg` 与哨兵事件。上游若在任何错误里回显
    `Authorization`，token 就会一次泄漏到四处。
    所以：`message` 只放**不含上游原文**的短句，上游原文放 `detail`
    （只进日志，由 `_fail` 控制）。这与 loomy 的约定一致。
    """
    headers = endpoints.auth_headers(token, realm)
    if json_body is not None:
        headers["Content-Type"] = "application/json"
    tag = "raccoon#%s" % (account_id if account_id is not None else "-")
    started = time.perf_counter()
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
            resp = client.request(method, url, headers=headers,
                                  json=json_body, params=params)
    except httpx.HTTPError as exc:
        # ⚠️ 不把 `exc` 原文拼进 message：httpx 的 LocalProtocolError /
        # RequestError 会回显完整请求头，里面有 `Authorization: Bearer ...`。
        raise UpstreamError("%s 网络错误（%s）" % (tag, type(exc).__name__),
                            kind="upstream", detail=_redact(str(exc))) from exc
    elapsed = int((time.perf_counter() - started) * 1000)
    text = resp.text or ""
    status = resp.status_code

    if not text.strip():
        # 空 body：不能当成 `{}` 成功返回。403/401 的空 body 上游确实会发。
        kind = _classify(None, status, "")
        if status >= 400 or status == 0:
            raise UpstreamError(
                "%s 上游返回空响应（HTTP %d）" % (tag, status),
                status=status, kind=kind,
                detail="empty body from %s %s" % (method, url))
        return None

    try:
        payload = json.loads(text)
    except ValueError:
        raise UpstreamError(
            "%s 返回非 JSON（HTTP %d）" % (tag, status),
            status=status, kind=_classify(None, status, text),
            detail=_redact("%s %s -> %s" % (method, url, text))) from None

    logger.debug("%s %s %s %sms", tag, method, url, elapsed)

    # 先按状态码判类型，再拆信封：两者都要过。
    if status >= 400:
        message = ""
        if isinstance(payload, dict):
            err = payload.get("error")
            if isinstance(err, dict):
                message = str(err.get("message") or "")
            message = message or str(payload.get("message") or "")
        code = _code_of(payload)
        kind = _classify(code, status, message)
        # ⚠️ 这里**必须**再判一次 confirm：上游把 `iam_confirm_required`
        # 放在 HTTP 400 里，走的是这个分支（不是 `unwrap`）。
        # 只改 unwrap 的话，实测仍然拿到「上游错误（HTTP 400）」这种
        # 什么也没说的文案。
        raise UpstreamError(
            (context + _confirm_hint() if kind == "confirm" else "")
            or "%s 上游错误（HTTP %d）" % (tag, status),
            status=status, kind=kind, code=code,
            detail=_redact("%s %s -> HTTP %d %s" % (method, url, status, text)),
            payload=payload)
    return unwrap(payload, status=status, context=context)



# ---------------------------------------------------------------------------
# 业务接口
# ---------------------------------------------------------------------------
def _has_payload(raw) -> bool:
    """这一行 SSE 里有没有真正的输出内容（content / reasoning / tool_calls）。

    只用于 TTFT 判定：`data: {"choices":[{"delta":{"role":"assistant"}}]}` 这种
    角色帧不算「开始出字」，但 `reasoning_content` 算 —— 思考块也是用户能
    看到的输出，而且 `completion_tokens` 里包含 reasoning_tokens，口径要一致。
    """
    try:
        text = (raw.decode("utf-8", "replace").strip()
                if isinstance(raw, (bytes, bytearray)) else str(raw).strip())
        if not text.startswith("data:"):
            return False
        payload = text[5:].strip()
        if not payload or payload == "[DONE]":
            return False
        chunk = json.loads(payload)
        for choice in (chunk.get("choices") or []):
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta") or {}
            if (delta.get("content") or delta.get("reasoning_content")
                    or delta.get("reasoning") or delta.get("tool_calls")):
                return True
    except Exception:  # noqa: BLE001 - 解析失败按"没内容"处理
        return False
    return False


class _StreamHandle:
    """流式响应的轻量包装：按行产出 + 关闭。

    为什么要包一层而不是直接用 `httpx.stream()`：
    `httpx.Client` 是上下文管理器，直接 `with client.stream(...)` 会把
    连接的生命周期绑死在 `with` 块上 —— 而我们是要**边读边转发**给客户端，
    转发持续几秒到几十秒。这里手动持有 client/response，由调用方在
    `finally` 里 `close()`，避免「生成器早就返回了但连接还挂着」。
    """

    def __init__(self, client_obj, response, stats: dict | None = None,
                 started_at: float | None = None):
        self._client = client_obj
        self._response = response
        self._stats = stats if isinstance(stats, dict) else None
        self._first = True
        #: 计时起点用**发起请求之前**，不是开始读行时。
        #:
        #: ✅ 实测教训：只从 `iter_lines()` 起算会得到 89ms 的 TTFT，
        #: 而客户端实际 2.5 秒才看到第一个字 —— 因为 httpx 在
        #: `send(stream=True)` 返回时已经把响应头和缓冲的首批字节收下了。
        #: 那个 89ms 是"读缓冲"的时间，不是上游首字时间。
        self._started_at = started_at if started_at else time.perf_counter()
        #: 读/关互斥。
        #:
        #: ## 为什么必须加锁
        #:
        #: 客户端断连时，Starlette 的 `BackgroundTask` 会**先于**生成器的
        #: `finally` 执行清理（生成器要等 GC）。实测时序：
        #:     order=['bg']  gen=0 bg=1     ← 断连时实际只有 bg 在清理
        #: 也就是说 `close()` 会在**另一线程仍阻塞在 `iter_lines()` 里**时被调用。
        #: httpx 的 `Response` 不是线程安全的，两个线程无锁并发访问
        #: （一个在读、一个在关）属于未定义行为。
        #: 这把锁保证「读完之后才允许关」。
        self._lock = threading.Lock()
        self._closed = False

    def iter_lines(self):
        started = self._started_at
        try:
            for line in self._response.iter_lines():
                # TTFT 只认**第一个带内容的 delta**，不是第一行 SSE ——
                # 第一行常常是 role/metadata 帧，早记会虚高一点点。
                if self._first and _has_payload(line):
                    self._first = False
                    if self._stats is not None:
                        self._stats["ttfb_ms"] = int((time.perf_counter() - started) * 1000)
                yield line
        finally:
            if self._stats is not None and not self._stats.get("total_ms"):
                self._stats["total_ms"] = int((time.perf_counter() - started) * 1000)

    def close(self):
        """幂等关闭；与 `iter_lines()` 互斥。"""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        try:
            self._response.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._client.close()
        except Exception:  # noqa: BLE001
            pass

    def __del__(self):
        # 兜底：调用方漏了 close 时也不要把连接吊死
        try:
            self.close()
        except Exception:  # noqa: BLE001
            pass


def open_stream(url: str, *, token: str, realm: str = "cn", json_body=None,
                account_id: int | None = None, stats: dict | None = None) -> _StreamHandle:
    """发起流式请求，返回可逐行读取的句柄。

    上游要先看首帧：如果第一行就是 `{"code": 401xx}` 的信封错误，
    必须在**开始转发之前**就抛出来，否则客户端会收到一个「200 + 空流」。
    """
    headers = endpoints.auth_headers(token, realm)
    headers["Content-Type"] = "application/json"
    headers["Accept"] = "text/event-stream"
    # 计时起点：**发起请求之前**（不是开始读行时）。httpx 的 send 返回时
    # 已经收下响应头和首批缓冲字节，从那里起算会把 TTFT 记成几十毫秒。
    started = time.perf_counter()
    client_obj = httpx.Client(timeout=TIMEOUT, follow_redirects=True)
    try:
        request = client_obj.build_request("POST", url, headers=headers,
                                           json=json_body)
        response = client_obj.send(request, stream=True)
    except httpx.HTTPError as exc:
        client_obj.close()
        raise UpstreamError("小浣熊流式请求失败（%s）" % type(exc).__name__,
                            kind="upstream", detail=_redact(str(exc))) from exc

    status = response.status_code
    if status >= 400:
        body = b""
        try:
            body = response.read()
        except Exception:  # noqa: BLE001
            pass
        response.close()
        client_obj.close()
        text = body.decode("utf-8", "replace")
        try:
            payload = json.loads(text)
        except ValueError:
            payload = {}
        code_int = _code_of(payload)
        message = ""
        if isinstance(payload, dict):
            err = payload.get("error")
            if isinstance(err, dict):
                message = str(err.get("message") or "")
            message = message or str(payload.get("message") or "")
        raise UpstreamError("小浣熊流式上游错误（HTTP %d）" % status,
                            status=status,
                            kind=_classify(code_int, status, message),
                            code=code_int, payload=payload,
                            detail=_redact("%s -> HTTP %d %s" % (url, status, text)))

    ctype = (response.headers.get("content-type") or "").lower()
    if "json" in ctype:
        # 上游对 stream:true 也可能直接返回一个 JSON（无流式能力时）
        text = response.read().decode("utf-8", "replace")
        response.close()
        client_obj.close()
        try:
            payload = json.loads(text)
        except ValueError:
            raise UpstreamError("小浣熊返回非预期内容（HTTP %d）" % status,
                                status=status, kind="upstream",
                                detail=_redact(text)) from None
        unwrap(payload, status=status)  # 信封错误在这里抛
        raise UpstreamError("小浣熊未返回流（content-type=%s）" % ctype,
                            status=status, kind="upstream",
                            payload=payload, detail=_redact(text))

    return _StreamHandle(client_obj, response, stats, started_at=started)


def fetch_models(token: str, realm: str = "cn", account_id: int | None = None) -> list[dict]:
    """模型目录。

    返回扁平化的模型列表：
        [{"name": "raccoon-090dd3", "category": "chat",
          "display": "...", "description": "...", "visible": bool,
          "billing_multiplier": 1.0, "billing_effective_multiplier": 0.0,
          "billing_status": "limited_free", "tags": [...], "context_window": ...}]

    上游结构是 `data.categories[].models[]`，这里拍平 —— 上层只关心
    「有哪些模型可调」，不关心它的分类树。

    ## 为什么要把计费字段一起带出来（2026-11-04 修正）

    上游 `/model_catalog` 每个模型都带 `billing_multiplier`（标价倍率）与
    `billing_effective_multiplier`（**实际扣减**倍率，限免/折扣后），
    第一版 `fetch_models` 只留了 name/display/description/visible，
    于是模型配置页的小浣熊行倍率全是「无」（`-1.0`）——
    而客户端里明明看得见 ×0.25 / ×0.5 这些倍率。

    两个值的区别是实打实的钱：
        sn-sensenova-6-8-flash  标价 0.5，`billing_status=limited_free` 实扣 0
    所以两个都留着，上层用 effective 作为「此刻要花多少」，标价用来解释限免。
    """
    url = endpoints.llm_base_of(realm) + endpoints.MODELS_PATH
    data = request_json("GET", url, token=token, realm=realm, account_id=account_id)
    categories = (data or {}).get("categories") if isinstance(data, dict) else None
    flat: list[dict] = []
    for cat in categories or []:
        ctype = str(cat.get("type") or "chat")
        for item in cat.get("models") or []:
            name = str(item.get("name") or item.get("model_name") or "").strip()
            if not name:
                continue
            multipliers = _multipliers_of(item)
            params = item.get("params") if isinstance(item.get("params"), dict) else {}
            flat.append({
                "name": name,
                "category": ctype,
                "display": str(item.get("display_name")
                               or item.get("description") or name),
                "description": str(item.get("display_description")
                                   or item.get("description") or ""),
                "visible": bool(item.get("visible", True)),
                # 计费：标价 / 实扣 / 分类 / 状态
                "billing_multiplier": multipliers[0],
                "billing_effective_multiplier": multipliers[1],
                "billing_category": str(item.get("billing_category") or ""),
                "billing_status": str(item.get("billing_status") or ""),
                "billing_status_note": str(item.get("billing_status_note") or ""),
                # 能力：标签与上下文长度（模型配置页用来解释「这模型是什么」）
                "tags": [str(t) for t in (item.get("tags") or []) if t],
                "context_window": params.get("context_window"),
                "max_tokens": params.get("max_tokens"),
                "ability_level": item.get("ability_level"),
            })
    return flat


def _multipliers_of(item: dict) -> tuple[float | None, float | None]:
    """从上游模型条目取 `(标价倍率, 实扣倍率)`。

    缺失/非法一律 `None`（= 该平台没有倍率概念），**不要**回落到 0 ——
    0 在模型配置页会被当成「免费」，把收费模型标成免费比不显示更糟。
    """
    def _num(value):
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.strip().rstrip("xX×"))
            except ValueError:
                return None
        return None

    listed = _num(item.get("billing_multiplier"))
    effective = _num(item.get("billing_effective_multiplier"))
    return listed, (effective if effective is not None else listed)


def fetch_points(token: str, realm: str = "cn", account_id: int | None = None) -> dict:
    """积分余额 → 统一口径。

        available = available_points（可用总额，含当日额度）
        daily     = daily_points（当日额度，会重置）
        reward    = reward_points

    ## 「空结构」必须抛错，不能当成「余额为 0」

    HTTP 200 + 空 body 时 `request_json` 返回 `None`。如果这里把它变成
    `{}` 再逐字段取 0，`apply_quota` 会把真实余额覆盖成 0、账号仍标记
    有效 —— 界面上看起来是「这个号没钱了」，实际只是没读到。

    所以：拿到的不是 dict，或者一个余额字段都没有，一律抛 `UpstreamError`。
    调用方（`sync_quota`）会按 partial 处理：保留上次快照、不写 0。
    「余额真的是 0」时上游会返回完整的字段集合（available_points=0 等），
    能正常走通这条路径，不受影响。
    """
    url = endpoints.origin_of(realm) + endpoints.POINTS_BALANCE_PATH
    data = request_json("GET", url, token=token, realm=realm,
                        account_id=account_id, context="积分查询失败：")
    if not isinstance(data, dict) or not data:
        raise UpstreamError(
            "积分查询返回空结构（HTTP 200）",
            status=200, kind="upstream",
            detail="points balance returned empty payload (account=%s)"
                   % (account_id,))

    _POINTS_KEYS = ("available_points", "total_chargeable_points",
                    "daily_points", "monthly_points", "reward_points")
    if not any(k in data for k in _POINTS_KEYS):
        raise UpstreamError(
            "积分查询返回未知结构（缺少余额字段）",
            status=200, kind="upstream",
            detail="unexpected points keys: %s" % ",".join(sorted(data)[:12]))

    def _i(key: str) -> int:
        try:
            return int(data.get(key) or 0)
        except (TypeError, ValueError):
            return 0

    available = _i("available_points")
    if not available:
        # 老版本字段名兜底
        available = _i("total_chargeable_points")
    return {
        "available": available,
        "balance": available,
        "daily_balance": _i("daily_points"),
        "monthly_balance": _i("monthly_points"),
        "reward": _i("reward_points"),
        "topup": _i("topup_points"),
        "raw": {k: data.get(k) for k in
                ("available_points", "daily_points", "monthly_points",
                 "reward_points", "topup_points", "total_chargeable_points")},
    }


def fetch_entitlement(token: str, realm: str = "cn", account_id: int | None = None) -> dict:
    """订阅权益（只读展示用，失败不影响主流程）。"""
    url = endpoints.origin_of(realm) + endpoints.ENTITLEMENT_PATH
    try:
        data = request_json("GET", url, token=token, realm=realm,
                            account_id=account_id, context="权益查询失败：")
    except UpstreamError as exc:
        logger.debug("权益查询跳过：%s", exc.message)
        return {}
    data = data if isinstance(data, dict) else {}
    office = data.get("office") if isinstance(data.get("office"), dict) else {}
    plan = office.get("active_plan") if isinstance(office.get("active_plan"), dict) else {}
    return {
        "pro": bool(office.get("pro_enable")),
        "pro_expired_at": office.get("pro_expired_time"),
        "plan_type": plan.get("type"),
        "subscription_expired_at": plan.get("subscription_expired_at"),
    }


def refresh_credentials(refresh_token: str, realm: str = "cn",
                        account_id: int | None = None) -> dict:
    """刷新凭证：`POST /api/web/auth/v1/refresh`。

    ⚠️ 实测这批凭证的 `refreshToken` 与 `accessToken` **完全相同**
    （50/50），说明上游签发的是一张「同票自续」的 JWT。拿到新票后
    必须两者一起覆盖，否则下次刷新会用旧票而失败。
    """
    url = endpoints.origin_of(realm) + endpoints.AUTH_REFRESH_PATH
    data = request_json("POST", url, token=refresh_token, realm=realm,
                        json_body={"refresh_token": refresh_token},
                        account_id=account_id, context="刷新凭证失败：")
    data = data if isinstance(data, dict) else {}
    access = str(data.get("access_token") or data.get("token") or "")
    refresh = str(data.get("refresh_token") or access)
    expires = data.get("expires_at") or data.get("expires_in")
    return {"access_token": access, "refresh_token": refresh, "expires": expires}
