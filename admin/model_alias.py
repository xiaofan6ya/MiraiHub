"""模型名归一：把同一模型在不同平台的别名收敛到同一个 key。

## 用户的核心诉求

「把相同模型的不同平台模型聚合显示……这样我假如调用 deepseek-v4.1-flash
就可以走多平台网关流量转发了」

## 实测到的真实别名（同一模型）

    平台A   DeepSeek-V4.1-Flash
    平台B   deepseek-v4.1-flash
    平台C   deepseek-v4.1-flash

    平台A   GLM-5.3-Flash
    平台B   gfmodel（显示名 GLM-5.3-Flash）
    平台C   glm-5.3-flash

## 归一规则（按可靠性排序）

1. **去掉厂商前缀/后缀噪音**：`:intl`、`-Official`、`-volc`、空格
2. 全小写 + 去掉所有非字母数字（`DeepSeek-V4.1-Flash` -> `deepseekv41flash`）
3. 版本号规范化：`v4.1` / `v4-1` / `v41` 都收敛成 `v41`
4. 常见别名合并表（少数无法靠规则收敛的）

## 为什么不能只靠 2

`DeepSeek-V4-Flash`（某平台的旧名）和 `DeepSeek-V4.1-Flash` 是同一个模型，
但去标点后是 `deepseekv4flash` vs `deepseekv41flash` —— 规则收敛不了。
所以需要第 4 条：一张**显式别名表**，由人工确认过的等价关系组成。
"""
from __future__ import annotations

import json
import re

#: 需要从名字里剔除的噪音片段（全小写比较）
NOISE_TOKENS = (
    ":intl", "-intl", "_intl", "intl",
    "-official", "_official", "official",
    "-volc", "_volc",
    "-preview", "_preview",
    "(official)",
    "-latest", "_latest",
)

#: 人工确认过的等价关系（归一 key -> 该组的所有写法，全小写）
KNOWN_ALIASES = {
    # DeepSeek **V4.1** Flash（注意：不含 V4，那是另一条产品线）
    #
    # `DeepSeek-Flash` 某个平台的展示名，实际指向 V4.1-Flash ——
    # 依据是它自己的模型描述写着
    # 「深度求索正式版模型（DeepSeek-V4.1-Flash-1017）」。
    # 这类「展示名不带版本号」的情况只能靠人工确认，规则收敛不了。
    "deepseekv41flash": (
        "deepseek-v4.1-flash", "deepseek-v41-flash", "deepseek-v4-1-flash",
        "deepseekv4.1flash", "DeepSeek-V4.1-Flash", "DeepSeek-V4.1-Flash-1017",
        "DeepSeek-Flash", "deepseek-flash",
    ),
    # DeepSeek **V4** Flash —— **独立产品线，不与 V4.1 合并**。
    # 「4.1 才是 v4，就是 v4 不是 4.1 也不是 DeepSeek-Flash」
    "deepseekv4flash": (
        "deepseek-v4-flash", "DeepSeek-V4-Flash", "deepseekv4flash",
        "deepseek-v4-flash-0731",
    ),
    # GLM 5.3 Flash
    "glm53flash": (
        "glm-5.3-flash", "glm53-flash", "GLM-5.3-Flash", "gfmodel",
    ),
    # DeepSeek **V4.1** Pro
    "deepseekv41pro": (
        "deepseek-v4.1-pro", "deepseek-v41-pro", "DeepSeek-V4.1-Pro",
    ),
    # DeepSeek **V4** Pro —— 独立产品线，不与 V4.1 合并
    "deepseekv4pro": (
        "deepseek-v4-pro", "DeepSeek-V4-Pro", "deepseek-v4-pro-0813",
    ),
}

#: 归一 key -> 展示名（任意平台调用的首选模型名）
DISPLAY_NAMES = {
    "deepseekv41flash": "DeepSeek-V4.1-Flash",
    "deepseekv4flash": "DeepSeek-V4-Flash",
    "glm53flash": "GLM-5.3-Flash",
    "deepseekv41pro": "DeepSeek-V4.1-Pro",
    "deepseekv4pro": "DeepSeek-V4-Pro",
}


def _strip_noise(name: str) -> str:
    text = str(name or "").strip().lower()
    # 展示名里常带计价后缀（`DeepSeek V4.1 Flash(x2.0)`）：它不属于模型名，
    # 留着会把同一模型拆成两组。
    text = re.sub(r"[（(]\s*[x×]\s*[\d.]+\s*[)）]", "", text)
    for token in NOISE_TOKENS:
        text = text.replace(token, "")
    return text.strip()


def _squash(name: str) -> str:
    """全小写 + 去所有非字母数字 + 版本号收敛。

    `DeepSeek-V4.1-Flash` -> `deepseekv41flash`
    """
    text = re.sub(r"[^a-z0-9]", "", _strip_noise(name))
    # v4.1 已被去点成 v41；再处理 v4-1 -> v41（去标点已覆盖）
    return text


def model_display(upstream_metadata) -> str:
    """从 `model_configs.upstream_metadata` 取**官方展示名**。

    为什么必须用它做归一：Qoder 这类平台的模型 id 是内部代号
    （`dfmodel` / `dmodel` / `gfmodel`），真正的名字在展示名里
    （`DeepSeek-Flash` / `DeepSeek-V4-Pro` / `GLM-5.3-Flash`）。
    只按 id 归一，这些模型永远聚不到一起去 —— 表现就是
    「同一个 DeepSeek-V4.1-Flash 在 Qoder 里还是单独一条」。

    `note` 字段不能拿来当依据：那是**用户备注**，可能被改成任意文本。
    """
    if isinstance(upstream_metadata, dict):
        data = upstream_metadata
    else:
        text = str(upstream_metadata or "").strip()
        if not text.startswith("{"):
            return ""
        try:
            data = json.loads(text)
        except ValueError:
            return ""
    if not isinstance(data, dict):
        return ""
    name = str(data.get("name") or data.get("display_name") or "").strip()
    return name


#: 平台内部代号（Qoder 的 `dfmodel` / `qmodel_38max` 等）。
#: 这类 id 本身不含模型信息，必须靠官方展示名归一。
_OPAQUE_ID = re.compile(r"^(?:[a-z]{0,4})?model(?:_?[a-z0-9]+)?$")


def _entitlement_note(note) -> str:
    """从备注里挑出**对调用有用**的那部分（开通情况）。

    备注在历史上有两种写法：模型显示名、以及「该账号未开通（上游 4023）」
    这类实测结论。前端 tooltip 只需要后者。
    """
    text = str(note or "").strip()
    if not text:
        return ""
    parts = [part.strip() for part in text.split("|") if part.strip()]
    keep = [part for part in parts
            if any(word in part for word in ("未开通", "4023", "无此模型", "不属于"))]
    return " | ".join(keep)


def canonical_key(model_id: str, display: str = "") -> str:
    """模型名 -> 归一 key（同一模型跨平台得到同一个 key）。

    `display` 是上游官方展示名：很多平台的 id 是内部代号
    （Qoder 的 `dfmodel` 展示名就是 `DeepSeek-Flash`），只按 id 归一
    这些模型永远聚不到一起。

    **但展示名不能无条件优先** —— Loomy 的展示名是
    `DeepSeek V4.1 Flash(x2.0)`，规则归一后变成 `deepseekv41flashx20`，
    反而把它从 `DeepSeek-V4.1-Flash` 组里拆出去了（实测踩过）。
    所以顺序是：先看哪一个能命中**人工别名表**，命中者胜；
    都没命中时以稳定度更高的 `model_id` 为准。
    """
    keys = []
    for candidate in (model_id, display):
        text = str(candidate or "").strip()
        if not text:
            keys.append("")
            continue
        lowered = _strip_noise(text)
        matched = ""
        for key, names in KNOWN_ALIASES.items():
            for name in names:
                if lowered == _strip_noise(name):
                    matched = key
                    break
            if matched:
                break
        keys.append(matched or _squash(text))
    from_id, from_display = keys[0], keys[1]
    # 命中人工别名表的一方优先（id 命中时用 id，展示名命中时用展示名）
    for key in (from_id, from_display):
        if key and key in KNOWN_ALIASES:
            return key
    # 内部代号（`kmodel` / `qmodel_38max`）没有模型信息：以展示名为准
    if from_display and _OPAQUE_ID.match(str(model_id or "").strip().lower()):
        return from_display
    return from_id or from_display


def display_name(key: str, fallback: str = "") -> str:
    """归一 key -> 展示名。"""
    return DISPLAY_NAMES.get(key) or fallback or key


def group_models(rows: list[dict]) -> list[dict]:
    """把模型配置按归一 key 聚合。

    输入：`[{model_id, platform, credit_multiplier, description, enabled,
             display_name?, ...}]`
    输出：`[{key, name, platforms: [...], min_multiplier, free_any, ...}]`

    每个聚合项保留**各平台的明细**，供悬停展示。
    """
    buckets: dict[str, dict] = {}
    for row in rows or []:
        model_id = str(row.get("model_id") or "")
        if not model_id:
            continue
        # 展示名优先：Qoder 的 `dfmodel` 展示名是 `DeepSeek-Flash`，
        # 只按 id 归一会把它单独列一组。
        display = str(row.get("display_name") or "")
        key = canonical_key(model_id, display)
        if not key:
            continue
        platform = str(row.get("platform") or "").lower()
        bucket = buckets.setdefault(key, {
            "key": key,
            "name": "",
            "platforms": [],
            "model_ids": [],
        })
        bucket["platforms"].append({
            "platform": platform,
            "model_id": model_id,
            "display_name": display,
            "multiplier": row.get("credit_multiplier"),
            "description": row.get("description") or "",
            # `note` 在本项目里被复用成两种含义：显示名（如 DeepSeek-V4.1-Flash）
            # 与实测结论（「该账号未开通（上游 4023）」）。只有后者对用户有用，
            # 直接把显示名塞进 tooltip 会变成无意义重复，所以只透出后者。
            "note": _entitlement_note(row.get("note")),
            "enabled": bool(row.get("enabled")),
            "billing_mode": row.get("billing_mode") or "",
        })
        bucket["model_ids"].append(model_id)
        if not bucket["name"]:
            bucket["name"] = display_name(key, display or model_id)

    out = []
    for key, bucket in buckets.items():
        # 同一平台内可能有多个变体（`xxx` 国内 / `xxx:intl` 国际）——
        # 那是**两个区、两份额度、两套倍率**，必须分开保留：
        # 之前每平台只留一个代表，国际版的 0 倍率会把国内版的 0.12 挤掉
        # （或反过来），表现就是「国内国际混了」。
        # 现在按 (平台, 区) 去重，同区多条时再按 enabled / 倍率挑一个。
        by_platform: dict[tuple[str, str], dict] = {}
        for item in bucket["platforms"]:
            model_id = str(item.get("model_id") or "")
            realm = "intl" if model_id.rstrip().endswith(":intl") else "cn"
            key = (str(item["platform"]), realm)
            current = by_platform.get(key)
            if current is None:
                by_platform[key] = item
                continue

            def rank(entry: dict) -> tuple:
                return (
                    0 if entry.get("enabled") else 1,
                    entry.get("multiplier") if isinstance(
                        entry.get("multiplier"), (int, float)) else 999,
                )

            if rank(item) < rank(current):
                by_platform[key] = item

        platforms = list(by_platform.values())
        multipliers = [p["multiplier"] for p in platforms
                       if isinstance(p["multiplier"], (int, float))]
        positive = [m for m in multipliers if m > 0]
        out.append({
            "key": key,
            "name": bucket["name"],
            "platforms": platforms,
            "model_ids": bucket["model_ids"],
            "platform_count": len(platforms),
            # 聚合时按「最便宜的可用平台」展示倍率
            "min_multiplier": min(positive) if positive else (
                0 if multipliers and all(m == 0 for m in multipliers) else None),
            "free_any": any(m == 0 for m in multipliers),
            "enabled_any": any(p["enabled"] for p in platforms),
        })
    # 平台数多的先（能走多平台的更值得看）
    out.sort(key=lambda item: (-item["platform_count"], item["name"].lower()))
    return out


def aggregate_payload(rows: list[dict]) -> dict:
    """给 API 的完整聚合结果（含未聚合的原始列表）。"""
    groups = group_models(rows)
    multi = [item for item in groups if item["platform_count"] > 1]
    return {
        "items": groups,
        "total_models": len(rows or []),
        "total_groups": len(groups),
        "multi_platform_groups": len(multi),
        "note": "同名模型已按平台聚合；展开可看各平台的具体模型名",
    }
