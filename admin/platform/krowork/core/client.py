"""KroWork 上游客户端：模型表 + 三端转发 + SSE。

## 上游长什么样（实测）

模型表：`GET https://llm.krowork.com/config/model-map`（Bearer id_token）

    {
      "version": 19, "min_agent_version": "1.4.0",
      "models": { "<backend_id>": {provider_type, proxy_path, max_output_tokens,
                                   window, capabilities, extra_body, enabled} },
      "slots":  { "cn": { "krow-model-1": {model_id, display_name, tier, description} },
                  "global": { ... } },
      "defaults": { "default_slot_per_region": {"cn": "krow-model-1", ...} }
    }

对话（**按 provider 分前缀**，不是统一 `/v1`）：

    openai-chat / dashscope   POST {proxy_path}/chat/completions   ← OpenAI 协议
    anthropic                 POST /anthropic/v1/messages          ← 原生 Anthropic
    openai                    POST /openai/v1/responses            ← Azure Responses

## 三条硬约束

1. **`model` 字段发 backend id**（`ds4-pro`），不是 slot（`krow-model-1`）。
   发 slot 也能通（上游会查 slots），但日志与计费对不上号。
2. **`x-model` 头必须与 body 的 `model` 一致**，缺了上游按空模型路由。
3. 错一个头（尤其 `x-model` / `x-app`）会被当成别的客户端，实测 401/403 或空流。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import threading
import time
from dataclasses import dataclass, field

import httpx

from . import endpoints, token as token_mod

logger = logging.getLogger("admin.platform.krowork.client")

#: 连接 15s / 读 600s（长推理会超过 5 分钟）/ 写 60s
TIMEOUT = httpx.Timeout(connect=15.0, read=600.0, write=60.0, pool=15.0)
#: 模型表缓存寿命（上游 `model_map_version` 变化才会真正变）
CATALOG_TTL = 300.0
#: 模型表拉取失败后的重试间隔（别每次请求都打上游）
CATALOG_RETRY = 20.0

_UNSET = object()


class UpstreamError(Exception):
    """上游失败。`kind` 决定网关怎么回：auth → 换号，permission → 503，其余透传。"""

    def __init__(self, message: str, *, status: int = 0, kind: str = "upstream",
                 code: str = "", detail: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.kind = kind
        self.code = code
        self.detail = detail

    def __str__(self) -> str:  # pragma: no cover - 便于日志
        return "%s(%s) %s" % (self.kind, self.status, self.message)


# ---------------------------------------------------------------------------
# 设备标识
# ---------------------------------------------------------------------------
_device_lock = threading.Lock()
_device_cache = ""


def machine_guid() -> str:
    """Windows MachineGuid（其它平台返回空）。客户端用它派生设备指纹。"""
    if os.name != "nt":
        return ""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Cryptography") as key:
            return str(winreg.QueryValueEx(key, "MachineGuid")[0] or "")
    except Exception:  # noqa: BLE001
        return ""


def device_id() -> str:
    """稳定的 64 hex 设备号（`x-device-id`）。

    上游不校验具体值，但**同一个号频繁换设备号会触发风控**，所以必须稳定：
    取 `MachineGuid + krowd_token` 的 sha256。
    """
    global _device_cache
    with _device_lock:
        if _device_cache:
            return _device_cache
        guid = machine_guid()
        secret = ""
        try:
            path = token_mod.data_dir() / ".krowd_token"
            if path.is_file():
                secret = path.read_text(encoding="utf-8").strip()
        except OSError:
            secret = ""
        raw = "|".join((guid, secret, platform.node())) or "krowork-gateway"
        _device_cache = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return _device_cache


# ---------------------------------------------------------------------------
# 请求头
# ---------------------------------------------------------------------------
def headers(token: str, *, model: str = "", user_id: str = "",
            extra_body: dict | None = None) -> dict:
    """构造上游请求头。

    实测必需项（少一个就打不通或行为异常）：

        Authorization: Bearer <id_token>
        x-model:       <backend id>        ← 与 body.model 一致
        x-source:      krow-openai
        x-app:         <app_name>
        x-user-id:     <jwt.sub>
        x-device-id:   <64 hex>
        x-extra:       {"source","app_name","client_version","lang"}
    """
    sub = user_id or token_mod.token_sub(token)
    head = {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "x-source": endpoints.CLIENT_SOURCE,
        "x-app": endpoints.CLIENT_APP,
        "x-user-id": sub,
        "x-device-id": device_id(),
        "x-extra": json.dumps({
            "source": endpoints.CLIENT_SOURCE,
            "app_name": endpoints.CLIENT_APP,
            "client_version": endpoints.CLIENT_VERSION,
            "lang": "python",
        }, ensure_ascii=False),
    }
    if model:
        head["x-model"] = model
    if extra_body:
        head["x-extra-body"] = json.dumps(extra_body, ensure_ascii=False)
    return head


# ---------------------------------------------------------------------------
# 错误解析
# ---------------------------------------------------------------------------
def _error_of(resp: httpx.Response, *, tag: str = "") -> UpstreamError:
    text = ""
    try:
        text = resp.text
    except Exception:  # noqa: BLE001
        pass
    message = text[:400]
    code = ""
    kind = "upstream"
    try:
        body = json.loads(text)
    except Exception:  # noqa: BLE001
        body = None
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            message = str(err.get("message") or message)
            code = str(err.get("code") or "")
        elif isinstance(err, str):
            # 上游的形状之一：{"error":"missing token"}
            message = err
            code = err.replace(" ", "_")
    status = resp.status_code
    if status == 401:
        kind = "auth"
    elif status == 403:
        kind = "permission"
    elif status == 429:
        kind = "rate_limit"
    elif status in (402,):
        kind = "quota"
    elif status >= 500:
        kind = "server"
    logger.warning("KroWork 上游失败 %s status=%s code=%s msg=%s",
                   tag or "-", status, code, message[:200])
    return UpstreamError(message or ("HTTP %s" % status), status=status,
                         kind=kind, code=code, detail=text[:1000])


# ---------------------------------------------------------------------------
# 模型表
# ---------------------------------------------------------------------------
@dataclass
class ModelRef:
    """一个可调用的模型（backend 视角）。"""

    id: str                      # backend id，如 `ds4-pro`
    slot: str = ""               # 对外槽位，如 `krow-model-1`
    display_name: str = ""
    description: str = ""
    tier: str = ""
    provider_type: str = ""
    proxy_path: str = ""
    max_output_tokens: int = 0
    window: dict = field(default_factory=dict)
    capabilities: dict = field(default_factory=dict)
    extra_body: dict = field(default_factory=dict)
    enabled: bool = True

    # -- 端点 -------------------------------------------------------------
    @property
    def path(self) -> str:
        return endpoints.endpoint_of(self.provider_type, self.proxy_path)

    @property
    def is_anthropic(self) -> bool:
        return (self.provider_type or "").lower() == endpoints.ANTHROPIC

    @property
    def is_responses(self) -> bool:
        return (self.provider_type or "").lower() == endpoints.OPENAI_RESPONSES

    @property
    def context_window(self) -> int:
        win = self.window or {}
        for key in ("nominal", "max_input", "context", "context_window"):
            try:
                value = int(win.get(key) or 0)
            except (TypeError, ValueError):
                value = 0
            if value:
                return value
        return 0

    @property
    def readable(self) -> str:
        """原厂模型名（`deepseek-v4-pro`、`qwen3.8-max`、`kimi-k3`）。

        槽位号（`krow-model-1`）对用户没意义，先翻译成这个。
        """
        return endpoints.readable_name(self.id) or self.id

    @property
    def branded(self) -> str:
        """**网关对外 model id**：原厂模型名 + `-krowork`（命名规范）。

            ds4-pro  →  deepseek-v4-pro-krowork

        这是 `/v1/models`、控制台、客户端配置里唯一该出现的形态。
        转发时仍发 backend id（`_payload_with_body` 负责换回去）。
        """
        return endpoints.brand_model_id(self.readable or self.id)

    def public(self, *, alias_in_platform: str = "") -> dict:
        """`/v1/models` 的一条（对齐 DSH 客户端读的能力字段）。

        `id` 用 **`原厂名-krowork`**（`deepseek-v4-pro-krowork`）：
          * 一排 `krow-model-n` 谁也分不出是哪个模型（用户实测反馈）；
          * 不带平台名会跟别家的 `deepseek-v4-pro` 撞名，看不出落点；
          * 带品牌后缀的名字能被 `resolve()` 反查回 backend，调用不受影响。

        槽位名与 backend id 都放进附加字段，方便排障对账。
        """
        caps = self.capabilities or {}
        public_id = alias_in_platform or self.branded or self.readable or self.id
        item = {
            "id": public_id,
            "object": "model",
            "created": 0,
            "owned_by": "krowork",
            "name": self.display_name or self.readable,
            "display_name": self.display_name or self.readable,
            "description": self.description,
            # ---- 对账用（转发层与排障看这几个）----
            "upstream_id": self.id,
            "slot": self.slot,
            "readable_id": self.readable,
            "aliases": [a for a in (self.slot, self.id, self.readable,
                                    self.branded) if a],
            "context_window": self.context_window,
            "contextWindow": self.context_window,
            "max_output_tokens": self.max_output_tokens or 0,
            "max_tokens": self.max_output_tokens or 0,
            "provider": self.provider_type,
            "supports_tools": bool(caps.get("supports_tool_calling")),
            "supports_parallel_tools": bool(caps.get("supports_parallel_tool_calls")),
            "supports_reasoning": bool(caps.get("supports_thinking")),
            "supports_vision": bool(caps.get("supports_image_input")),
            "supports_multimodal": bool(caps.get("supports_multimodal")),
            "supports_response_schema": bool(caps.get("supports_json_schema")),
        }
        return item


@dataclass
class ModelMap:
    version: int = 0
    region: str = "cn"
    models: dict = field(default_factory=dict)     # backend id -> ModelRef
    slots: dict = field(default_factory=dict)      # slot -> ModelRef
    default_slot: str = ""
    fetched_at: float = 0.0
    raw: dict = field(default_factory=dict)

    def by_any(self, name: str) -> ModelRef | None:
        """按 **slot / 可读名** / backend id / display_name / 大小写变体找模型。

        ## 匹配顺序为什么是「槽位优先」（2026-11-20）

        `models` 里有个 backend 恰好就叫 `deepseek-v4-pro`（上游真有这条），
        而槽位 `krow-model-1` 的可读名也是 `deepseek-v4-pro`。若先查 backend，
        `/v1/models` 发的 `deepseek-v4-pro` 会落到那条**裸 backend**上，
        而不是用户实际能调的槽位 —— 路由对得上但计费/能力字段是另一套。

        所以：**先槽位（对外名字），再 backend（内部名字）**。
        """
        key = (name or "").strip()
        if not key:
            return None
        low = key.lower()
        # 网关自己的平台后缀先剥掉（`deepseek-v4-pro-krowork`）
        for suffix in ("-krowork", "-kro"):
            if low.endswith(suffix):
                low = low[: -len(suffix)]
                break
        # 1) 槽位优先（对外名字）：slot 名 / 可读名 / 显示名
        #
        # ⚠️ 这一层必须排在 `models.get()` **之前**：`models` 里有个 backend
        # 恰好也叫 `deepseek-v4-pro`，先查 backend 会让对外名字落到那条裸
        # backend 上（路由能通但能力/计费是另一套）。
        ref = self.slots.get(key) or self.slots.get(low)
        if ref is not None:
            return ref
        for ref in self.slots.values():
            if (ref.slot or "").lower() == low:
                return ref
        for ref in self.slots.values():
            if (ref.readable or "").lower() == low:
                return ref
        for ref in self.slots.values():
            if (ref.display_name or "").lower() == low:
                return ref
        # 2) backend（内部名字）
        ref = self.models.get(key) or self.models.get(low)
        if ref is not None:
            return ref
        for ref in self.models.values():
            if ref.id.lower() == low:
                return ref
        for ref in self.models.values():
            if (ref.readable or "").lower() == low:
                return ref
        for ref in self.models.values():
            if (ref.display_name or "").lower() == low:
                return ref
        # 3) canonical 兜底（客户端可能把可读名再 slug 化一次）
        canon = endpoints.canonical_model_id(low)
        if canon and canon != low:
            for ref in list(self.slots.values()) + list(self.models.values()):
                if endpoints.canonical_model_id(ref.id) == canon:
                    return ref
        # 4) 前缀兜底：`krow-model-1:ds4-pro` 这种「槽位:变体」写法
        if ":" in key:
            head, tail = key.split(":", 1)
            ref = self.models.get(tail) or self.slots.get(head)
            if ref is not None:
                return ref
        return None

    def default(self) -> ModelRef | None:
        if self.default_slot:
            ref = self.slots.get(self.default_slot)
            if ref is not None:
                return ref
        for ref in self.models.values():
            if ref.enabled:
                return ref
        return None

    def catalog(self) -> list[ModelRef]:
        """对外可调列表：**只给槽位**。

        上游 `models` 里有 61 个后端，但那是给不同套餐/内部链路用的 ——
        免费账号实际只能调 `slots[region]` 里那几个（cn 区 7 个）。
        把后端全列出去，客户端会选到一个必然 403/502 的模型，
        而错误长成「上游 authentication failed」，用户根本猜不到是套餐问题。

        需要按后端名直连的场景（`ds4-pro`）仍可用 `resolve()`，
        只是不出现在 `/v1/models` 里。
        """
        return list(self.slots.values())


_model_lock = threading.Lock()
_model_cache: dict[str, ModelMap] = {}
_model_next_try: dict[str, float] = {}


def _catalog_file(region: str):
    """模型表的落盘缓存。

    为什么必须落盘：模型表是**静态配置**（一版跑几周），而凭证只有 30 分钟。
    如果表只存内存，网关一重启又赶上客户端没登录，`/v1/models` 就空了 ——
    客户端连模型都选不到，看起来像「整个平台挂了」。
    """
    return token_mod.data_dir() / ("model-map-%s.json" % (region or "cn"))


def _load_catalog(region: str) -> ModelMap | None:
    try:
        path = _catalog_file(region)
        if path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and payload.get("models"):
                return _parse_map(payload, region)
    except Exception:  # noqa: BLE001
        return None
    return None


def _save_catalog(region: str, payload: dict) -> None:
    try:
        path = _catalog_file(region)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _parse_map(payload: dict, region: str) -> ModelMap:
    models_raw = payload.get("models") or {}
    slots_raw = (payload.get("slots") or {})
    region_slots = slots_raw.get(region) or slots_raw.get("global") or {}
    defaults = payload.get("defaults") or {}
    default_slot = str((defaults.get("default_slot_per_region") or {}).get(region) or "")

    result = ModelMap(version=int(payload.get("version") or 0), region=region,
                      default_slot=default_slot, fetched_at=time.time(), raw=payload)

    # 1) backend 层
    for mid, spec in models_raw.items():
        if not isinstance(spec, dict):
            continue
        if spec.get("enabled") is False:
            continue
        ref = ModelRef(
            id=str(mid),
            display_name=str(spec.get("display_name") or ""),
            provider_type=str(spec.get("provider_type") or ""),
            proxy_path=str(spec.get("proxy_path") or ""),
            max_output_tokens=int(spec.get("max_output_tokens") or 0),
            window=dict(spec.get("window") or {}),
            capabilities=dict(spec.get("capabilities") or {}),
            extra_body=dict(spec.get("extra_body") or {}),
            enabled=spec.get("enabled") is not False,
        )
        if not ref.id:
            continue
        result.models[ref.id] = ref

    # 2) slot 层（同一个 backend 可能被多个 slot 复用，slot 是「对外的名字」）
    for slot, spec in region_slots.items():
        if not isinstance(spec, dict):
            continue
        mid = str(spec.get("model_id") or "")
        base = result.models.get(mid)
        if base is None:
            # slot 指向的 backend 不在 models 里：仍然登记（避免整表丢弃）
            base = ModelRef(id=mid or str(slot), provider_type="openai-chat",
                            proxy_path="/ks/v1")
            result.models.setdefault(base.id, base)
        ref = ModelRef(
            id=base.id,
            slot=str(slot),
            display_name=str(spec.get("display_name") or base.display_name or slot),
            description=str(spec.get("description") or base.description or ""),
            tier=str(spec.get("tier") or ""),
            provider_type=base.provider_type,
            proxy_path=base.proxy_path,
            max_output_tokens=base.max_output_tokens,
            window=base.window,
            capabilities=base.capabilities,
            extra_body=base.extra_body,
            enabled=True,
        )
        result.slots[str(slot)] = ref

    if not result.default_slot:
        for slot in result.slots:
            result.default_slot = slot
            break
    return result


def fetch_model_map(*, force: bool = False) -> ModelMap:
    """拿模型表（带内存缓存；失败不抛，返回上一次的快照）。

    **不要在请求线程里同步打上游**（skill §12 铁律 8）——
    这里只在上游可用时刷新，失败就退回缓存，最坏情况是模型列表短暂为空。
    """
    snap = token_mod.current()
    region = "cn"
    try:
        cfg = token_mod.data_dir() / "config.json"
        if cfg.is_file():
            region = str(json.loads(cfg.read_text(encoding="utf-8")).get("region") or "cn")
    except Exception:  # noqa: BLE001
        region = "cn"

    with _model_lock:
        cached = _model_cache.get(region)
        fresh = cached is not None and (time.time() - cached.fetched_at) < CATALOG_TTL
        if fresh and not force:
            return cached
        if not force and time.time() < _model_next_try.get(region, 0):
            return cached or ModelMap(region=region)

    if not snap.id_token:
        # 没有凭证也别让目录空掉：磁盘缓存里有就直接用（静态配置，不会过期）。
        # ⚠️ 这里**不能**设 `_model_next_try` —— 那会让接下来 20 秒的调用
        # 走进「刚试过、跳过」分支，而 `_model_cache` 还是空的，于是返回空表
        # （实测：/v1/models 先有一次 7 个，紧接着 20 秒内全是 0 个）。
        cached = _model_cache.get(region) or _load_catalog(region)
        if cached is not None:
            with _model_lock:
                _model_cache[region] = cached
            return cached
        with _model_lock:
            _model_next_try[region] = time.time() + CATALOG_RETRY
        return ModelMap(region=region)

    url = endpoints.host_of(region) + endpoints.PATH_MODEL_MAP
    try:
        from admin import http_shared
        with httpx.Client(**http_shared.client_kwargs("krowork", timeout=TIMEOUT)) as client:  # P3：共享连接池 + 共享 TLS 上下文 + 建连 10s（cookie 仍按请求隔离）
            resp = client.get(url, headers=headers(snap.id_token))
        if resp.status_code >= 400:
            raise _error_of(resp, tag="model-map")
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("KroWork 模型表拉取失败 region=%s：%s（回落到缓存）", region, exc)
        cached = _model_cache.get(region) or _load_catalog(region)
        if cached is not None:
            with _model_lock:
                _model_cache[region] = cached
                _model_next_try[region] = time.time() + CATALOG_RETRY
            return cached
        with _model_lock:
            _model_next_try[region] = time.time() + CATALOG_RETRY
        return ModelMap(region=region)

    parsed = _parse_map(payload if isinstance(payload, dict) else {}, region)
    with _model_lock:
        _model_cache[region] = parsed
        _model_next_try.pop(region, None)
    # 把上游真实的槽位 → backend 关系灌进端点的归一表，让
    # `canonical_model_id()`（models_router 用它定对外 id）与运行时一致。
    try:
        endpoints.refresh_slot_map([(ref.slot, ref.id)
                                    for ref in parsed.slots.values() if ref.slot])
    except Exception:  # noqa: BLE001 - 归一表刷新失败不影响转发
        logger.debug("槽位归一表刷新失败", exc_info=True)
    _save_catalog(region, payload if isinstance(payload, dict) else {})
    logger.info("KroWork 模型表已更新 region=%s version=%s models=%d slots=%d",
                region, parsed.version, len(parsed.models), len(parsed.slots))
    return parsed


def resolve(name: str, *, model_map: ModelMap | None = None) -> ModelRef | None:
    """把用户给的模型名解析成上游 backend。"""
    table = model_map or fetch_model_map()
    ref = table.by_any(name)
    if ref is not None:
        return ref
    # 去掉网关自己的平台后缀（`ds4-pro-krowork` → `ds4-pro`）
    if name and "-" in name:
        trimmed = name.rsplit("-", 1)[0]
        ref = table.by_any(trimmed)
        if ref is not None:
            return ref
    return None


# ---------------------------------------------------------------------------
# 调用
# ---------------------------------------------------------------------------
def _payload_with_body(body: dict, ref: ModelRef) -> dict:
    """把请求体改写成上游要的形状。

    ## 两件必做的事

    1. **`model` 一律换成 backend id**。上游不认网关的 `-krowork` 后缀，
       也不认槽位号（部分端点容忍但日志/计费对不上号）。
       `/v1/models` 发的是 `deepseek-v4-pro-krowork`，转发前必须还原成 `ds4-pro`。
    2. 补上模型级的 `extra_body`（如 `thinking: {type: disabled}`）——
       上游把这类开关放在模型配置里，不透传等于「思考档位」被网关抹平。
    """
    payload = dict(body)
    payload["model"] = ref.id
    for key, value in (ref.extra_body or {}).items():
        payload.setdefault(key, value)
    return payload


def call_json(path: str, payload: dict, *, ref: ModelRef, realm: str = "cn",
              user_id: str = "", attempt_token: str = "") -> dict:
    """非流式调用。返回上游原始 JSON（OpenAI / Anthropic 各自的原生形状）。"""
    snap = token_mod.current()
    tok = attempt_token or snap.id_token
    if not tok:
        raise UpstreamError("没有可用的 KroWork 凭证（请先打开客户端或导入）",
                            status=401, kind="auth", code="missing_token")
    url = endpoints.host_of(realm) + path
    head = headers(tok, model=ref.id, user_id=user_id)
    body = _payload_with_body(payload, ref)
    try:
        from admin import http_shared
        with httpx.Client(**http_shared.client_kwargs("krowork", timeout=TIMEOUT)) as client:  # P3：共享连接池 + 共享 TLS 上下文 + 建连 10s（cookie 仍按请求隔离）
            resp = client.post(url, json=body, headers=head)
    except httpx.HTTPError as exc:
        raise UpstreamError("上游连接失败：%s" % exc, status=502, kind="network") from None
    if resp.status_code >= 400:
        err = _error_of(resp, tag=path)
        if err.status == 401:
            token_mod.STORE.note_unauthorized()
        raise err
    try:
        data = resp.json()
    except ValueError:
        raise UpstreamError("上游返回的不是 JSON：%s" % resp.text[:200],
                            status=502, kind="protocol") from None
    return data if isinstance(data, dict) else {}


def open_stream(path: str, payload: dict, *, ref: ModelRef, realm: str = "cn",
                user_id: str = "", attempt_token: str = ""):
    """打开 SSE 流，返回 `(client, response)`；调用方负责 close。

    单独返回 client 是因为 `httpx.stream` 的上下文生命周期必须跟流走完 ——
    用 `with` 包在函数里会让流在生成器返回时就被关掉（典型症状：
    客户端收到第一帧后卡死）。
    """
    snap = token_mod.current()
    tok = attempt_token or snap.id_token
    if not tok:
        raise UpstreamError("没有可用的 KroWork 凭证（请先打开客户端或导入）",
                            status=401, kind="auth", code="missing_token")
    url = endpoints.host_of(realm) + path
    head = headers(tok, model=ref.id, user_id=user_id)
    body = _payload_with_body(payload, ref)
    from admin import http_shared
    client = httpx.Client(**http_shared.client_kwargs("krowork", timeout=TIMEOUT))  # P3：共享连接池 + 共享 TLS 上下文 + 建连 10s（cookie 仍按请求隔离）
    request = client.build_request("POST", url, json=body, headers=head)
    try:
        resp = client.send(request, stream=True)
    except httpx.HTTPError as exc:
        client.close()
        raise UpstreamError("上游连接失败：%s" % exc, status=502, kind="network") from None
    if resp.status_code >= 400:
        try:
            text = resp.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            text = ""
        try:
            resp.close()   # 读错误体失败时也要关：共享连接池里漏关的连接不会被 GC 带走
        finally:
            client.close()
        fake = httpx.Response(resp.status_code, text=text)
        err = _error_of(fake, tag=path)
        if err.status == 401:
            token_mod.STORE.note_unauthorized()
        raise err
    return client, resp


def iter_sse(resp) -> "object":
    """逐行产出上游 SSE 的**原始字节**（`data: {...}\\n\\n`）。

    不在这里解析 JSON：OpenAI / Anthropic 两条上游的帧格式不同，
    解析交给上层对应的转换器。
    """
    for raw in resp.iter_lines():
        if raw:
            yield (raw + "\n").encode("utf-8")
        else:
            yield b"\n"


# ---------------------------------------------------------------------------
# 额度 / 积分（**实测路径，不是猜的**）
# ---------------------------------------------------------------------------
#: 积分候选路径 —— 第一位是从 web dashboard 打包产物里扒出来的**真路径**，
#: 后面几个是兜底（上游改版时还能侥幸命中）。
#:
#: 全部只发 GET，不会产生 token 消耗。
CREDIT_PATHS = (
    endpoints.PATH_CREDIT_BALANCE,
    "/krow/commerce/credit/balance",
    "/krow/api/credits",
    "/krow/api/user/credits",
)

_credit_lock = threading.Lock()
_credit_path = ""
_credit_cache: dict[str, tuple[float, dict]] = {}
CREDIT_TTL = 60.0


def _envelope(body):
    """解 `{"status":200,"message":"成功","data":…}` 信封。

    上游把业务码放在 **body 里**，HTTP 一律 200 —— 所以判断成功不能看
    `resp.status_code`，必须看 `body["status"]`。这一点搞错会把
    「Missing request header」这种业务错误当成成功数据吞下去。
    """
    if not isinstance(body, dict):
        return None
    status = body.get("status")
    if status is None:
        return body  # 没有信封的接口（少数）
    try:
        code = int(status)
    except (TypeError, ValueError):
        return None
    if code != 200:
        return None
    data = body.get("data")
    return data if data is not None else {}


def commerce_get(path: str, tok: str = "", realm: str = "cn", *,
                 params: dict | None = None, headers: dict | None = None,
                 timeout: float = 15.0) -> dict:
    """GET `api.krowork.com/krow/commerce/*`，解信封后返回 `data`。

    失败抛 `UpstreamError`（`kind` 区分 auth / business），调用方决定是否降级。
    """
    snap = token_mod.current()
    token = tok or snap.id_token
    if not token:
        raise UpstreamError("没有可用的 KroWork 凭证", status=401, kind="auth",
                            code="missing_token")
    head = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    if headers:
        head.update(headers)
    try:
        from admin import http_shared

        with httpx.Client(**http_shared.client_kwargs(
                "krowork", timeout=httpx.Timeout(timeout))) as http:
            resp = http.get(endpoints.api_of(realm) + path, headers=head,
                            params=params or None)
    except httpx.HTTPError as exc:
        raise UpstreamError("积分接口连接失败：%s" % exc, status=502,
                            kind="network") from None
    if resp.status_code >= 400:
        raise _error_of(resp, tag=path)
    try:
        body = resp.json()
    except ValueError:
        raise UpstreamError("积分接口返回非 JSON：%s" % resp.text[:160],
                            status=502, kind="protocol") from None
    data = _envelope(body)
    if data is None:
        message = str((body or {}).get("message") or "")[:160]
        raise UpstreamError(message or "积分接口业务失败", status=502,
                            kind="business", code=str((body or {}).get("status") or ""),
                            detail=json.dumps(body, ensure_ascii=False)[:600])
    return data if isinstance(data, dict) else {"items": data}


def fetch_credits(realm: str = "cn", *, force: bool = False,
                  tok: str = "") -> dict:
    """读积分余额 + 订阅套餐（只发 GET）。命中一次后缓存 60 秒。

    返回 `{"ok","balance","total_credits","remain_credits","items","plan",…}`。
    失败**不抛** —— 额度读不到不等于账号不能用（skill 铁律 5）。
    """
    snap = token_mod.current()
    if not tok and not snap.ok:
        return {"ok": False, "detail": snap.detail or "没有可用凭证",
                "remain_credits": 0.0, "total_credits": 0.0, "items": []}
    key = "%s|%s" % (realm, (tok or snap.id_token)[-12:])
    with _credit_lock:
        cached = _credit_cache.get(key)
        if cached and not force and (time.time() - cached[0]) < CREDIT_TTL:
            return cached[1]

    # 1) 积分余额（主数据）
    try:
        data = commerce_get(endpoints.PATH_CREDIT_BALANCE, tok=tok, realm=realm)
    except UpstreamError as exc:
        if exc.kind == "auth":
            token_mod.STORE.note_unauthorized()
        result = {"ok": False, "detail": str(exc.message)[:200],
                  "remain_credits": 0.0, "total_credits": 0.0, "items": []}
        with _credit_lock:
            _credit_cache[key] = (time.time(), result)
        return result

    items = []
    total = 0.0
    for item in (data.get("items") or []):
        if not isinstance(item, dict):
            continue
        try:
            credits = float(item.get("credits") or 0)
        except (TypeError, ValueError):
            credits = 0.0
        total += credits
        items.append({
            "plan_id": item.get("planId"),
            "type": item.get("creditType") or "",
            "type_en": item.get("creditTypeEn") or "",
            "credits": credits,
            "origin": float(item.get("originCredits") or 0),
            "expire_time": item.get("expireTime") or "",
        })
    try:
        remain = float(data.get("totalCredits") or total)
    except (TypeError, ValueError):
        remain = total

    # 2) 订阅 / 套餐（读不到不影响余额）
    plan: dict = {}
    try:
        subs = commerce_get(endpoints.PATH_SUBSCRIPTION, tok=tok, realm=realm)
        if isinstance(subs, dict) and subs:
            plan = {"subscription": subs}
    except Exception:  # noqa: BLE001
        plan = {}

    result = {
        "ok": True,
        "path": endpoints.PATH_CREDIT_BALANCE,
        "remain_credits": remain,
        "total_credits": total or remain,
        "items": items,
        "source": "api.krowork.com/krow/commerce/credit/balance",
        "plan": plan,
    }
    with _credit_lock:
        _credit_cache[key] = (time.time(), result)
    return result


def fetch_checkin_status(realm: str = "cn", tok: str = "") -> dict:
    """签到状态（上游强制要求 `X-User-Timezone`，缺了回 status=500）。"""
    try:
        return commerce_get(endpoints.PATH_CHECKIN_STATUS, tok=tok, realm=realm,
                            headers={"X-User-Timezone": "Asia/Shanghai"})
    except UpstreamError as exc:
        return {"supported": False, "detail": str(exc.message)[:160]}


def claim_checkin(realm: str = "cn", tok: str = "") -> dict:
    snap = token_mod.current()
    token = tok or snap.id_token
    if not token:
        raise UpstreamError("没有可用的 KroWork 凭证", status=401, kind="auth")
    try:
        from admin import http_shared

        with httpx.Client(**http_shared.client_kwargs(
                "krowork", timeout=httpx.Timeout(20.0))) as http:
            resp = http.post(
                endpoints.api_of(realm) + endpoints.PATH_CHECKIN_CLAIM,
                headers={"Authorization": "Bearer " + token,
                         "Accept": "application/json",
                         "X-User-Timezone": "Asia/Shanghai"},
                json=None)
    except httpx.HTTPError as exc:
        raise UpstreamError("签到请求失败：%s" % exc, status=502,
                            kind="network") from None
    if resp.status_code >= 400:
        raise _error_of(resp, tag=endpoints.PATH_CHECKIN_CLAIM)
    body = resp.json() if resp.content else {}
    data = _envelope(body)
    if data is None:
        raise UpstreamError(str((body or {}).get("message") or "签到失败"),
                            status=502, kind="business")
    return data if isinstance(data, dict) else {"items": data}


def fetch_plan_info(realm: str = "cn", tok: str = "") -> list[dict]:
    """套餐清单（含每个档位的月度积分与价格）。"""
    data = commerce_get(endpoints.PATH_PLAN_INFO, tok=tok, realm=realm)
    items = data if isinstance(data, list) else (data.get("items") or [])
    out = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        out.append({
            "id": item.get("id"),
            "name": item.get("displayName") or "",
            "plan_type": item.get("planType") or "",
            "credits": item.get("credits") or 0,
            "price": item.get("price") or 0,
            "currency": item.get("currency") or "",
        })
    return out


# ---------------------------------------------------------------------------
# 探针
# ---------------------------------------------------------------------------
def probe(realm: str = "cn") -> dict:
    """免鉴权健康检查：判断上游活着，与凭证无关。"""
    url = endpoints.host_of(realm) + endpoints.PATH_HEALTH
    try:
        with httpx.Client(timeout=httpx.Timeout(10.0)) as client:
            resp = client.get(url)
        return {"ok": resp.status_code == 200,
                "status": resp.status_code,
                "body": resp.text[:100]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "status": 0, "body": str(exc)[:200]}


async def aprobe(realm: str = "cn") -> dict:
    import asyncio

    return await asyncio.get_running_loop().run_in_executor(None, probe, realm)
