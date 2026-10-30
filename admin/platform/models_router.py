"""`GET /v1/models` 的**跨平台聚合**版本。

## 为什么需要

原来 `/v1/models` 只返回默认平台（WorkBuddy）的 17 个模型：
其他平台的模型在客户端里根本看不到，用户以为「网关只有这些模型」。

## 聚合策略

**不带 `X-Platform`（普通调用）** —— 返回全部平台的并集，并且：

* 同一个模型在多个平台都有时，为每个平台各出一条**带后缀**的 id
  （`deepseek-v4.1-flash-workbuddy` / `deepseek-v4.1-flash-trae-cn`），
  这样用户在客户端里选一个就能固定落点，不需要加 header。
* 只在一个平台存在的，直接用裸 id（不加无意义的后缀）。
* `owned_by` 填平台 id，客户端能直接看出归属。

**带 `X-Platform`** —— 保持原样，只返回该平台的模型（不聚合），
让「明确指定平台」的客户端行为不变。

## 与主应用的边界

默认平台的模型表由主应用（converter）维护，这里只是**读库**并合并，
不修改任何一方的行为。主应用仍然处理真正的推理请求。
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger("admin.platform.models_router")

#: 缓存（秒）：模型表不会秒级变化，而这里要查全库
_TTL = 10.0
_cache: dict = {"at": 0.0, "payload": {}, "signature": ""}


def _signature(db) -> str:
    """模型配置的指纹：启用数 + 最大 id，变了就重建缓存。"""
    from sqlalchemy import func

    from admin.models import ModelConfig

    row = (db.query(func.count(ModelConfig.id), func.max(ModelConfig.id),
                    func.sum(ModelConfig.enabled))
           .filter(ModelConfig.enabled == 1).one())
    return "%s|%s|%s" % (row[0], row[1], row[2])


#: 各平台的上游没有给能力字段时的兜底值。
#:
#: WorkBuddy 的 catalog 有完整 `supportsImages` / `maxInputTokens` /
#: `reasoning.supportedEfforts`，但 **Qoder / Loomy 的 metadata 是空的**，
#: 不兜底的话客户端会按「256K / 32K / 纯文本 / 不支持推理」显示。
#: 这里按「同款模型在别处的已知能力」给合理默认，宁可偏保守也不要留空。
_CAP_FALLBACK: dict[str, dict] = {
    "deepseek": {"context": 1000000, "output": 128000, "images": True, "reasoning": True},
    "glm":      {"context": 200000, "output": 32000,  "images": False, "reasoning": True},
    "kimi":     {"context": 256000, "output": 32000,  "images": False, "reasoning": True},
    "qwen":     {"context": 256000, "output": 32000,  "images": False, "reasoning": True},
    "minimax":  {"context": 512000, "output": 64000,  "images": False, "reasoning": True},
    "doubao":   {"context": 256000, "output": 32000,  "images": False, "reasoning": False},
    "seed":     {"context": 256000, "output": 32000,  "images": False, "reasoning": False},
    "hunyuan":  {"context": 192000, "output": 32000,  "images": False, "reasoning": True},
    "claude":   {"context": 200000, "output": 32000,  "images": True,  "reasoning": True},
    "gpt":      {"context": 128000, "output": 32000,  "images": True,  "reasoning": True},
    "gemini":   {"context": 1000000, "output": 32000, "images": True,  "reasoning": True},
    "step":     {"context": 128000, "output": 32000,  "images": False, "reasoning": False},
    "mimo":     {"context": 128000, "output": 32000,  "images": False, "reasoning": True},
}


def _num(value) -> int:
    """把上游的 `1000000` / `"1M"` / `"256K"` 之类统一成整数。"""
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value or "").strip().lower().replace(",", "")
    if not text:
        return 0
    mult = 1
    if text.endswith("k"):
        mult, text = 1000, text[:-1]
    elif text.endswith("m"):
        mult, text = 1000000, text[:-1]
    try:
        return int(float(text) * mult)
    except (TypeError, ValueError):
        return 0


def _capabilities_of(row) -> dict:
    """从一条 model_config 里提取能力信息。

    输出形状（客户端/控制台都用这套字段名）：
        context_window / max_output_tokens / supports_images /
        supports_reasoning / supports_tools / reasoning_efforts
    """
    import json as _json

    raw = getattr(row, "upstream_metadata", None)
    md: dict = {}
    if raw:
        try:
            md = _json.loads(raw) if isinstance(raw, str) else (raw or {})
        except Exception:  # noqa: BLE001
            md = {}
    if not isinstance(md, dict):
        md = {}

    caps: dict = {}
    ctx = _num(md.get("maxInputTokens")) or _num(md.get("contextWindow")) \
        or _num(md.get("maxInputToken"))
    out = _num(md.get("maxOutputTokens")) or _num(md.get("maxTokens")) \
        or _num(md.get("maxOutputToken"))
    if ctx:
        caps["context_window"] = ctx
    if out:
        caps["max_output_tokens"] = out

    img = md.get("supportsImages")
    if img is None:
        img = md.get("supportImage")
    if isinstance(img, bool):
        caps["supports_images"] = img

    reason = md.get("supportsReasoning")
    if isinstance(reason, bool):
        caps["supports_reasoning"] = reason
    efforts = []
    rinfo = md.get("reasoning")
    can_disable = None
    if isinstance(rinfo, dict):
        raw_efforts = rinfo.get("supportedEfforts") or rinfo.get("efforts") or []
        if isinstance(raw_efforts, list):
            efforts = [str(x) for x in raw_efforts if str(x).strip()]
        elif isinstance(raw_efforts, str) and raw_efforts.strip():
            # 有的平台把单档写成字符串（`effort: medium`）
            efforts = [raw_efforts.strip()]
        if "canDisableThinking" in rinfo:
            can_disable = bool(rinfo.get("canDisableThinking"))
            if "supports_reasoning" not in caps:
                caps["supports_reasoning"] = True
        if "defaultEffort" in rinfo:
            caps["default_reasoning_effort"] = str(rinfo["defaultEffort"])
    # `onlyReasoning: true` 表示**不能关闭思考**，等同于 canDisableThinking=false
    if can_disable is None and isinstance(md.get("onlyReasoning"), bool):
        can_disable = not md["onlyReasoning"]
    if can_disable is not None:
        caps["can_disable_thinking"] = can_disable
    if efforts:
        caps["reasoning_efforts"] = efforts

    tools = md.get("supportsToolCall")
    if isinstance(tools, bool):
        caps["supports_tools"] = tools

    # ---- 兜底 1：按模型名里的品牌推断 ----
    if "context_window" not in caps or "supports_images" not in caps:
        target = "%s %s" % (getattr(row, "model_id", "") or "", md.get("name") or "")
        low = target.lower()
        fallback = {}
        for brand, conf in _CAP_FALLBACK.items():
            if brand in low:
                fallback = conf
                break
        if fallback:
            caps.setdefault("context_window", fallback["context"])
            caps.setdefault("max_output_tokens", fallback["output"])
            caps.setdefault("supports_images", fallback["images"])
            caps.setdefault("supports_reasoning", fallback["reasoning"])

    # ---- 兜底 2：仍缺就填一组安全值 ----
    # 剩下的多是平台内部工具模型（`refactor_planner` / `code-review-judge`）
    # 和视频模型（`kling-video`）—— 上游根本没有能力元数据。
    # 留空会让客户端显示成灰块，不如给「纯文本 + 128K」这种保守值。
    caps.setdefault("context_window", 128000)
    caps.setdefault("max_output_tokens", 16000)
    caps.setdefault("supports_images", False)
    caps.setdefault("supports_reasoning", False)
    return caps


def _caps_flat(caps: dict, display_name: str = "") -> dict:
    """把 `caps` 展开成 DSH/OpenAI 兼容客户端认的顶层字段。

    DSH 的 `discoverModels` zod schema（从 app.asar 反编译确认）：

        {
          id: string,
          name?: string,
          contextWindow?: number,       // 正整数
          maxTokens?: number,           // 正整数
          inputModalities?: ("text"|"image")[]
        }

    注意 `inputModalities` 是**字符串数组**，不是布尔；
    缺省表示「未知」，所以只在确定支持时才给 `image`。
    """
    out: dict = {}
    name = str(display_name or "").strip()
    if name:
        out["name"] = name
    ctx = caps.get("context_window")
    if isinstance(ctx, int) and ctx > 0:
        out["contextWindow"] = ctx
    mt = caps.get("max_output_tokens")
    if isinstance(mt, int) and mt > 0:
        out["maxTokens"] = mt
    if "supports_images" in caps:
        mods = ["text"]
        if caps.get("supports_images"):
            mods.append("image")
        out["inputModalities"] = mods
    efforts = caps.get("reasoning_efforts")
    if isinstance(efforts, list) and efforts:
        out["reasoningEfforts"] = efforts
    elif caps.get("supports_reasoning"):
        # 上游没公布具体档位、但确实支持推理时，按模型名给一组**该系列
        # 真实存在**的档位。
        #
        # 不能一律给 `["low","medium","high"]`：
        #   · DeepSeek V4.1 上游给的是 `["low","high","max"]`（有 max 无 medium）
        #   · DeepSeek V4    上游给的是 `["high","xhigh"]`
        # 给错档位名，客户端选了它就在**本地**拦截，报
        # `UNSUPPORTED_REASONING_EFFORT`，请求根本发不到网关。
        out["reasoningEfforts"] = _default_efforts_for(name)
    # 默认档位 / 能否关闭思考：客户端用它们决定「推理等级」开关的初值
    default_effort = caps.get("default_reasoning_effort")
    if isinstance(default_effort, str) and default_effort:
        out["defaultReasoningEffort"] = default_effort
    if isinstance(caps.get("can_disable_thinking"), bool):
        out["canDisableThinking"] = caps["can_disable_thinking"]
    return out


def _default_efforts_for(name: str) -> list[str]:
    """按模型名给一组合理的推理档位（上游没公布时的兜底）。

    取值依据：同系列模型在**有元数据平台上**的真实 `supportedEfforts`。
    """
    key = str(name or "").lower()
    if "deepseek" in key:
        if "v4.1" in key:
            # WorkBuddy / Trae 的 v4.1 上游真实值就是 low/high/max
            return ["low", "high", "max"]
        if "v4" in key:
            # v4-flash / v4-pro 上游真实值 high/xhigh。
            # **额外补 max**：上游接受 max（映射到最高档），
            # 而我们没有该平台的档位元数据，多给一个比少给安全 ——
            # 少给会被客户端在本地拦掉，多给了上游自己会处理。
            return ["high", "xhigh", "max"]
        # R1 / V3 这类老模型上游不认 effort，给最小集合避免误拦
        return ["low", "high"]
    if "glm" in key or "kimi" in key or "qwen" in key or "minimax" in key:
        return ["low", "medium", "high"]
    if "claude" in key or "gpt" in key or "gemini" in key:
        return ["low", "medium", "high"]
    return ["low", "medium", "high"]


def slugify_model_id(name: str) -> str:
    """把任意模型名压成规范的调用 id：小写 + 连字符 + 纯 ASCII。

    `/v1/models` 的 `id` 是**调用标识**，不是展示名。规范形态应该是
    `deepseek-v4.1-flash`、`kimi-k3.1-pro` 这样：
      · 全小写
      · 只保留 `[a-z0-9.-]`，其余（空格、括号、中文、下划线）转 `-`
      · 连续 `-` 合并、首尾不留 `-`

    例：
        `DeepSeek V4.1 Flash(x2.0)`      -> `deepseek-v4.1-flash-x2.0`
        `Kimi k2.6 （x6.5）`             -> `kimi-k2.6-x6.5`
        `Doubao_1_5_thinking_pro`        -> `doubao-1-5-thinking-pro`
        `glm-5.3`                        -> `glm-5.3`（已经规范，原样）

    入参带 `:intl` / `:cn` 时区后缀会被保留（`xxx:intl`），
    因为调度层靠它区分国际/国内。
    """
    import re as _re

    text = str(name or "").strip()
    if not text:
        return ""
    realm = ""
    if ":" in text:
        head, tail = text.rsplit(":", 1)
        if tail.lower() in ("cn", "intl"):
            text, realm = head, tail.lower()
    # 1) 去掉括号里的补充说明：`x2.0` / `x6.5` 是**倍率标记**，
    #    不是模型名的一部分；`(Doubao-Seed-2.1-Turbo)` 只是别名。
    #    `Seed-2.1-Turbo (Doubao-Seed-2.1-Turbo)` 应该压成 `seed-2.1-turbo`。
    text = _re.sub(r"[(（][^)）]*[)）]", "", text)
    # 2) 去掉尾部孤立的倍率标记（`x2.0` / `x0.8` / `x6.5`）
    text = _re.sub(r"\s*x\d+(?:\.\d+)?\s*$", "", text, flags=_re.I)
    # 3) 非 ASCII（中文、全角符号）一律当分隔符
    text = _re.sub(r"[^A-Za-z0-9.\-]+", "-", text)
    text = _re.sub(r"-{2,}", "-", text).strip("-.").lower()
    if not text:
        return ""
    return "%s:%s" % (text, realm) if realm else text


#: 缩写 id -> 真实模型名的映射。
#:
#: 上游有很多平台用 `dfmodel` / `kmodel_latest` 这种内部缩写当模型 id，
#: 客户端界面上只显示 id，用户根本看不出是哪个模型。这里把常见的缩写
#: 换成真实模型名，让 `/v1/models` 返回可读的调用名。
#:
#: **只映射确实看不懂的缩写**，`hy3` / `glm-5.3` 这类本身已可读的不动，
#: 避免映射后重名（`hy3` 和 `hy3-x` 的显示名都是 `Hy3`）。
MODEL_NAME_MAP: dict[str, dict[str, str]] = {
    "qoder": {
        "auto": "auto",
        "dfmodel": "deepseek-flash",
        "dmodel": "deepseek-v4-pro",
        "gfmodel": "glm-5.3-flash",
        "gmodel": "glm-5.3",
        "kmodel": "kimi-k2.8-preview",
        "kmodel_latest": "kimi-k3.1-pro",
        "mmodel": "minimax-m3.1-flash",
        "qfmodel": "qwen3.8-flash",
        "qmodel_38max": "qwen3.8-max",
    },
}


def readable_model_id(storage: str, model_id: str) -> str:
    """把上游缩写 id 换成真实模型名（查不到就原样返回）。

    `storage` 是落库平台（`qoder`），不是带区的 id。
    """
    base = str(model_id or "")
    bare = base.split(":")[0] if ":" in base else base
    table = MODEL_NAME_MAP.get(str(storage or "").lower())
    if not table:
        return base
    mapped = table.get(bare) or table.get(bare.lower())
    return mapped or base


def _realm_platform_id(storage: str, realm: str) -> str:
    """把「storage + 区」还原成对外平台 id。

    `qoder` + `intl` -> `qoder-intl`；`trae` + `cn` -> `trae-cn`；
    找不到就退回 storage 本身。
    """
    from admin.platform import registry

    storage = str(storage or "").lower()
    realm = str(realm or "").lower()
    for spec in registry.all_platforms():
        if str(spec.storage()).lower() == storage and (spec.realm or "") == realm:
            return spec.id
    return storage


def _storage_realms() -> dict[str, list[str]]:
    """每个 storage 有哪些区（`{'qoder': ['intl','cn'], 'trae': ['intl','cn']}`）。

    **必须自己确保平台已加载**：服务启动早期 `load_platforms()` 可能还没跑，
    此时 registry 里只有默认平台，`_entry_targets` 会把 qoder 当成单区 →
    国际版整个消失。
    """
    from admin.platform import registry

    try:
        from admin.platform.loader import load_platforms

        load_platforms()
    except Exception:  # noqa: BLE001 - 加载失败按现有 registry 处理
        pass

    out: dict[str, list[str]] = {}
    for spec in registry.all_platforms():
        key = str(spec.storage()).lower()
        realm = spec.realm or ""
        out.setdefault(key, [])
        if realm not in out[key]:
            out[key].append(realm)
    return out


def _entry_targets(row: dict) -> list[tuple[str, str]]:
    """一条 model_configs 记录应该映射到哪些 `(platform_id, realm)`。

    三种情况：
      1. 模型串自带 `:intl`  -> 只对应国际区
      2. storage 只有一个区    -> 就是它
      3. storage 有多个区、模型串没后缀（Qoder 的 dfmodel）
         -> **展开成每个区一条**，这样国内版和国际版都能在列表里看到
    """
    storage = str(row["platform"] or "").lower()
    model_id = str(row["model_id"] or "")
    suffix = model_id.rsplit(":", 1)[-1].lower() if ":" in model_id else ""
    realms = _storage_realms().get(storage) or [""]

    if suffix in ("cn", "intl"):
        return [(_realm_platform_id(storage, suffix), suffix)]
    # 单区平台：**不要给 realm 值**，否则前端会给模型 id 加 `:cn` 后缀
    # （Loomy 只有国内一个区，`spark-x2.6:cn` 这种名字既多余又容易被误当成上游 id）
    if len(realms) <= 1:
        return [(_realm_platform_id(storage, realms[0] if realms else ""), "")]

    # 有多个区、且模型串没后缀 —— 要区分两种情况：
    #   a) 该 storage 的模型表**确实分两套**（Trae：国内有 `xxx`，国际有 `xxx:intl`）
    #      -> 无后缀这条只属于国内，不要再复制到国际
    #   b) 模型表**只有一份**（Qoder：国内国际共用 `dfmodel`）
    #      -> 展开成每个区一条
    # 判断是否有 `xxx:intl` 孪生记录（由调用方通过 `_has_intl_twin` 预先算好）
    if row.get("_has_intl_twin"):
        return [(_realm_platform_id(storage, "cn"), "cn")]

    return [(_realm_platform_id(storage, r), r) for r in realms]


def build_payload(db, *, aggregate_only: bool = False) -> dict:
    """模型清单（OpenAI `/v1/models` 形状）。

    `aggregate_only=True`：同一模型跨平台只出一条，落点交给网关调度。
    `aggregate_only=False`：每平台每区一条，多平台同名带 `-平台名` 后缀。
    """
    from admin.model_alias import canonical_key, model_display
    from admin.models import ModelConfig
    from admin.platform import model_suffix

    now = time.time()
    signature = _signature(db)
    slot_key = "aggregate" if aggregate_only else "full"
    cached = (_cache["payload"].get(slot_key)
              if isinstance(_cache["payload"], dict) else None)
    if (cached is not None and _cache["signature"] == signature
            and now - _cache["at"] < _TTL):
        return cached

    rows = (db.query(ModelConfig)
            .filter(ModelConfig.enabled == 1)
            .order_by(ModelConfig.platform.asc(), ModelConfig.model_id.asc())
            .all())

    # 先算出「哪些 (storage, model_id) 存在 :intl 孪生」——
    # 有孪生说明这个平台**分两套模型表**（Trae），无孪生是共用一份（Qoder）。
    # 注意 `mid[:-5]` 去掉的是 `:intl`（5 个字符）
    twins = {(str(r.platform or "").lower(), str(r.model_id or "")[:-5])
             for r in rows if str(r.model_id or "").endswith(":intl")}

    # 先把每条记录展开成 (platform_id, realm, model_id, display)
    expanded: list[dict] = []
    for row in rows:
        storage = str(row.platform or "").lower()
        if not storage:
            continue
        display = model_display(getattr(row, "upstream_metadata", None))
        mid = str(row.model_id or "")
        for pid, realm in _entry_targets({
                "platform": storage, "model_id": mid,
                "_has_intl_twin": (storage, mid) in twins}):
            expanded.append({
                "platform": pid,
                "storage": storage,
                "realm": realm,
                "model_id": str(row.model_id or ""),
                "display": display or str(row.model_id),
                # 能力信息（上下文/输出/图片/推理）来自上游 metadata，
                # 之前完全没输出，客户端只能看到模型名 —— 于是所有模型
                # 都显示成「文本 / 256K / 不支持推理」。
                "caps": _capabilities_of(row),
            })

    # 按归一 key 分组（用于判断「是否跨平台同名」）
    groups: dict[str, list[dict]] = {}
    for item in expanded:
        key = canonical_key(item["model_id"], item["display"]) or item["model_id"]
        groups.setdefault(key, []).append(item)

    entries: list[dict] = []
    seen: set[str] = set()

    if aggregate_only:
        for key_, members in groups.items():
            by_platform: dict[str, dict] = {}
            for item in members:
                by_platform.setdefault(item["platform"], item)
            head = next(iter(by_platform.values()))
            agg_id = slugify_model_id(head["model_id"])
            if not agg_id or agg_id in seen:
                continue
            seen.add(agg_id)
            entries.append({
                "id": agg_id,
                "object": "model",
                "created": 0,
                "owned_by": "gateway",
                "platform": "",
                "platform_label": "自动调度",
                "base_model": head["model_id"],
                "display_name": head["display"],
                "platforms": sorted(by_platform.keys()),
                # 能力信息（取组内第一条有数据的）
                **next(({"caps": it["caps"]} for it in by_platform.values()
                        if it.get("caps")), {}),
                **_caps_flat(
                    next((it["caps"] for it in by_platform.values()
                          if it.get("caps")), None) or {}, head["display"]),
            })
    else:
        for key_, members in groups.items():
            by_platform = {}
            for item in members:
                by_platform.setdefault(item["platform"], item)
            multi = len(by_platform) > 1
            for pid, item in by_platform.items():
                # 先把缩写换成真实模型名 → 加平台后缀 → 压成规范 id
                base_id = readable_model_id(item["storage"], item["model_id"])
                raw_id = (model_suffix.apply(pid, base_id)
                          if multi else base_id)
                model_id = slugify_model_id(raw_id) or slugify_model_id(base_id)
                display_id = "%s%s%s" % (
                    item["model_id"], ":" + item["realm"] if item["realm"] else "",
                    "|" + pid)
                uniq = model_id + "|" + pid
                if uniq in seen:
                    continue
                seen.add(uniq)
                spec = None
                try:
                    from admin.platform import registry

                    spec = registry.get(pid)
                except Exception:  # noqa: BLE001
                    pass
                # `display_name` 追加平台名：六个平台可能同名同显示名
                # （Qoder 国内外都是 `DeepSeek-Flash`），不写平台就分不清。
                label = spec.label if spec else pid
                display_name = item["display"]
                if label and label not in display_name:
                    display_name = "%s · %s" % (display_name, label)
                entries.append({
                    "id": model_id,
                    "object": "model",
                    "created": 0,
                    "owned_by": pid,
                    "platform": pid,
                    "platform_label": label,
                    "realm": item["realm"],
                    "base_model": item["model_id"],
                    "display_name": display_name,
                    # 上下文 / 输出 / 图片 / 推理 —— 客户端靠这些决定
                    # 「能不能传图」「推理等级面板要不要显示」。
                    "caps": item.get("caps") or {},
                    # **DSH / OpenAI 兼容客户端认的是这几个 camelCase 顶层字段**：
                    # DSH 反编译确认（app.asar `discoverModels` 的 zod schema）：
                    #   { id, name?, contextWindow?, maxTokens?, inputModalities? }
                    # 只放在 `caps` 里它读不到，界面上就是灰块。
                    **_caps_flat(item.get("caps") or {}, display_name),
                })

    entries.sort(key=lambda e: (e["owned_by"], e["id"]))
    payload = {"object": "list", "data": entries}
    _store(slot_key, payload, now, signature)
    return payload


def platform_of_model(name: str, db=None) -> str:
    """查一个模型名归属哪个平台（返回**平台 id**，查不到返回空串）。

    用途：客户端发来的名字没有平台后缀、但其实是某平台专属模型时，
    dispatch 靠它把请求改道到正确的平台网关，而不是交给主应用报 400。

    匹配顺序：
      1. 精确命中某条 model_config 的 slug
      2. 命中缩写映射后的可读名
    """
    target = str(name or "").strip().lower()
    if not target:
        return ""
    own_db = db is None
    try:
        if own_db:
            from admin.db import SessionLocal

            db = SessionLocal()
        from admin.models import ModelConfig

        rows = db.query(ModelConfig).filter(ModelConfig.enabled == 1).all()
        for row in rows:
            raw = str(row.model_id or "")
            if not raw:
                continue
            storage = str(row.platform or "").lower()
            candidates = {
                raw.lower(),
                slugify_model_id(raw),
                slugify_model_id(readable_model_id(storage, raw)),
            }
            if target not in candidates:
                continue
            # 该 storage 有多个区时无法确定是哪个区 —— 交给后缀，
            # 这里只处理单区或默认区
            realms = _storage_realms().get(storage) or [""]
            realm = "cn" if len(realms) > 1 else (realms[0] if realms else "")
            pid = _realm_platform_id(storage, realm)
            return pid or storage
        return ""
    except Exception:  # noqa: BLE001
        logger.debug("platform_of_model 失败 name=%s", name, exc_info=True)
        return ""
    finally:
        if own_db and db is not None:
            try:
                db.close()
            except Exception:  # noqa: BLE001
                pass


def upstream_of(slug: str, db=None) -> str:
    """把规范 id 还原成**上游真实 id**。

    `/v1/models` 对外给的是 slug（`deepseek-v4-flash`），但上游只认
    自己的 id（`DeepSeek-V4-Flash` / `dfmodel`）。转发前必须换回来，
    否则上游一律 400「模型不存在」。

    实现：遍历已启用的 model_configs，对每条算出它的 slug，命中即返回原 id。
    这是 O(n) 扫描，但 n 只有一百多、且结果会被 dispatch 层的
    `_model_id_for` 缓存，实测无压力。
    """
    target = str(slug or "").strip().lower()
    if not target:
        return ""
    own_db = db is None
    try:
        if own_db:
            from admin.db import SessionLocal

            db = SessionLocal()
        from admin.models import ModelConfig

        rows = db.query(ModelConfig).filter(ModelConfig.enabled == 1).all()
        for row in rows:
            raw = str(row.model_id or "")
            if not raw:
                continue
            if raw.lower() == target:
                return raw
            if slugify_model_id(raw) == target:
                return raw
            # 缩写映射后的名字也要能命中（`deepseek-flash` -> `dfmodel`）
            storage = str(row.platform or "").lower()
            readable = readable_model_id(storage, raw)
            if slugify_model_id(readable) == target:
                return raw
        return ""
    except Exception:  # noqa: BLE001
        logger.debug("slug 还原上游 id 失败 target=%s", slug, exc_info=True)
        return ""
    finally:
        if own_db and db is not None:
            try:
                db.close()
            except Exception:  # noqa: BLE001
                pass


def to_openai_strict(payload: dict, readable: bool = True) -> dict:
    """裁成**最严格**的 OpenAI `/v1/models` 形状。

    有些客户端（Rust 的 serde、Go 的 strict struct）遇到未知字段会直接
    `error decoding response body`，所以只保留 `id` / `object` / `created`
    三个标准字段。

    `readable=True` 时 `id` 用**可读名字**（`DeepSeek-Flash · Qoder 中国版`）
    而不是上游原始 id（`dfmodel-qoder`）。原因：客户端界面上只显示 `id`，
    上游 id 是 `dfmodel` / `dmodel` / `kmodel_latest` 这种缩写，用户根本
    看不出是哪个模型。
    """
    out = []
    seen: set[str] = set()
    for item in payload.get("data") or []:
        mid = str(item.get("id") or "")
        if not mid or mid in seen:
            continue
        seen.add(mid)
        # **只用 `id`，不要用 display_name**：
        # display_name 是给人看的（`DeepSeek V4.1 Flash(x2.0)`、
        # `Kimi k2.6 （x6.5）`），带空格、括号、中文，既不能当调用名，
        # 也会让客户端拼出 `模型 'DeepSeek-Flash · Qoder 中国版' 不存在`。
        # 可读性已经在 `id` 里做好了（`deepseek-flash-qoder`）。
        model_id = mid
        if model_id in seen and model_id != mid:
            # 极少见：两个不同 id 撞同一个显示名 —— 加 id 尾巴区分
            model_id = "%s (%s)" % (model_id, mid)
            if model_id in seen:
                continue
        seen.add(model_id)
        entry = {
            "id": model_id,
            "object": "model",
            "created": 0,
        }
        # **能力字段必须带上**：DSH 的 `discoverModels` 从这些顶层
        # camelCase 字段读上下文/输出/输入类型，读不到就显示成
        # 「256K / 32K / 纯文本 / 不支持推理」的灰块。
        # 这些字段都是 OpenAI 生态的常见扩展（vLLM / LiteLLM 也这么发），
        # 严格解析的客户端会忽略未知键，不会因此报错。
        for key in ("name", "contextWindow", "maxTokens",
                    "inputModalities", "reasoningEfforts",
                    "defaultReasoningEffort", "canDisableThinking"):
            val = item.get(key)
            if val not in (None, "", [], {}):
                entry[key] = val
        out.append(entry)
    return {"object": "list", "data": out}


def _store(slot: str, payload: dict, now: float, signature: str) -> None:
    """写入缓存（两种视图分开存，互不覆盖）。"""
    if not isinstance(_cache["payload"], dict):
        _cache["payload"] = {}
    _cache["payload"][slot] = payload
    _cache.update({"at": now, "signature": signature})


def invalidate() -> None:
    """模型配置变化后调用。"""
    _cache.update({"at": 0.0, "payload": {}, "signature": ""})
