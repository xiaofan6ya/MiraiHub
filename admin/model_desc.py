"""模型描述：上游不提供时，基于能力字段**生成**一条有信息量的说明。

## 实测结论

查了 Loomy / Qoder 的 `/models`，**都没有 description 字段**：

    Loomy:  id / name / object / created / owned_by / type / protocol /
            context_length / max_output_tokens / capabilities /
            reasoning_efforts / default_reasoning_effort / is_default_model

    Qoder:  key / status / format / source / enable / display_name /
            is_vl / is_reasoning / is_default / price_factor /
            max_input_tokens / is_sensitive / context_config

也搜了客户端 asar，没有独立的模型详情接口。

## 所以不该显示「上游暂未提供模型描述」

那等于把「我没整理」说成「上游没给」。上游给了**能力信息**
（上下文长度、是否推理、是否视觉、是否支持函数调用），
这些正是用户选模型时要看的。

## 做法

`describe_model(model)` 按优先级取：

  1. 上游真的给了 description -> 用它
  2. 否则**由能力字段拼**：`上下文 1M · 支持推理 · 支持工具调用`
  3. 全都没有 -> 空（不编造）
"""
from __future__ import annotations

import json

#: 上下文长度的可读写法
def _human_tokens(value) -> str:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return ""
    if number <= 0:
        return ""
    if number >= 1_000_000:
        text = "%.0fM" % (number / 1_000_000)
    elif number >= 1000:
        text = "%.0fK" % (number / 1000)
    else:
        return str(number)
    return text


def describe_model(model: dict, platform: str = "") -> str:
    """给一个模型条目生成描述。

    优先用上游给的 `description*`；没有就由能力字段拼一条，
    **不编造**（拼不出来就返回空串）。
    """
    if not isinstance(model, dict):
        return ""

    # 1) 上游真的给了描述
    for key in ("descriptionZh", "descriptionEn", "description",
                "desc", "summary", "intro"):
        value = model.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    # 嵌套在 x_<platform> 里的
    for key, value in model.items():
        if isinstance(key, str) and key.startswith("x_") and isinstance(value, dict):
            for sub in ("description", "desc", "summary"):
                text = value.get(sub)
                if isinstance(text, str) and text.strip():
                    return text.strip()

    # 2) 用能力字段拼
    #
    # ⚠️ 各平台把能力字段放在命名空间子对象（`x_loomy` / `x_qoder`）里，
    # 因为那些字段不属于 OpenAI `/v1/models` 标准。解析时必须看子对象。
    scopes = [model]
    for key, value in model.items():
        if isinstance(key, str) and key.startswith("x_") and isinstance(value, dict):
            scopes.append(value)

    def get(*keys):
        for scope in scopes:
            for name in keys:
                value = scope.get(name)
                if value is not None and value != "":
                    return value
        return None

    parts: list[str] = []

    # 上下文长度（三个来源都试，最后看 context_config）
    context = get("context_length", "max_input_tokens", "max_context_tokens")
    if not context:
        config = get("context_config")
        if isinstance(config, dict):
            for item in config.values():
                if isinstance(item, dict) and item.get("is_default"):
                    context = item.get("token_count")
                    break
            if not context:
                for item in config.values():
                    if isinstance(item, dict) and item.get("token_count"):
                        context = item["token_count"]
                        break
    readable = _human_tokens(context)
    if readable:
        parts.append("上下文 %s" % readable)

    # 能力（两种形状：顶层布尔 / capabilities 子对象）
    caps = get("capabilities")
    caps = caps if isinstance(caps, dict) else {}

    def flag(name: str, label: str) -> None:
        value = get(name)
        if value is None:
            value = caps.get(name)
        if value is True:
            parts.append(label)

    flag("is_reasoning", "支持推理")
    flag("reasoning", "支持推理")
    flag("is_vl", "支持视觉")
    flag("vision", "支持视觉")
    flag("function_calling", "支持工具调用")
    flag("streaming", "支持流式")
    flag("system_message", "支持系统提示")

    # 输出上限
    max_out = _human_tokens(get("max_output_tokens"))
    if max_out:
        parts.append("输出上限 %s" % max_out)

    if get("is_default_model", "is_default"):
        parts.append("默认模型")

    return " · ".join(parts)
