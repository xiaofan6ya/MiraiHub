"""读取客户端 dynamic-text 里的模型描述（这才是的「描述」）。

## 用户指出的

「我说的描述是这里的，你那个描述是参数到模型列表的，
这个加载能自动识别这个模型支持的类型模式上下文的」

用户截图弹窗里的长描述：

    深度求索正式版模型（DeepSeek-V4.1-Flash-1017），Agent 能力、世界知识
    与推理性能全面领先。个人版限时每日赠送免费使用额度，至 11月01 日 00:00。

我之前生成的「上下文 180K · 支持视觉」只是**参数**，不是这句话。

## 真正来源（已定位）

    <安装目录>/resources/dynamic-text/<productId>.json

结构：

    {"zh": {"model.<key>.description": "...",
            "model.<key>.label": "..."},
     "en": {...}}

客户端逻辑（asar 里 `GI1` 函数）：

    description: Bw(i[`model.${key}.description`]) ?? model.description

即「dynamic-text 覆盖文案」优先，其次是模型自带的 description。

## 版本差异（重要）

本地文件是**旧版**：`DeepSeek-V4-Flash-0731`
用户截图（服务端下发）是：`DeepSeek-V4.1-Flash-1017`

所以本地文件只能作为**兜底**。优先顺序应该是：
  1. 服务端 dynamicText 接口（拿不到就算了）
  2. 本地 dynamic-text 文件
  3. 由能力字段拼（我原来的做法）

**不能拿本地旧文案当权威** —— 它会把版本号说错。
"""
from __future__ import annotations

import json
import logging
import os
import pathlib

logger = logging.getLogger("admin.dynamic_text")

#: 缓存（安装目录 -> {lang: {key: text}}）
_CACHE: dict[str, dict] = {}

#: 候选安装目录（各平台自己可能有）
INSTALL_HINTS = (
    r"D:\Qoder",
    r"C:\Program Files\Qoder",
    r"C:\Program Files (x86)\Qoder",
    r"%LOCALAPPDATA%\Programs\Qoder",
)


def _candidate_files() -> list[pathlib.Path]:
    out: list[pathlib.Path] = []
    for hint in INSTALL_HINTS:
        root = pathlib.Path(os.path.expandvars(hint))
        if not root.is_dir():
            continue
        folder = root / "resources" / "dynamic-text"
        if not folder.is_dir():
            continue
        for item in folder.glob("*.json"):
            out.append(item)
    return out


def load(product_id: str = "qoder", lang: str = "zh") -> dict:
    """读该产品的 dynamic-text（`{key: text}`）。

    读不到返回 `{}` —— 调用方回落到「由能力字段拼」。
    """
    cache_key = "%s:%s" % (product_id, lang)
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    result: dict = {}
    for path in _candidate_files():
        if path.stem.lower() != product_id.lower():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (ValueError, OSError):
            continue
        section = data.get(lang)
        if isinstance(section, dict):
            result.update({str(k): str(v) for k, v in section.items()
                           if isinstance(v, str)})
        if result:
            break

    _CACHE[cache_key] = result
    if result:
        logger.debug("已加载 %s 的 dynamic-text（%d 条）", product_id, len(result))
    return result


def model_description(model_key: str, *, product_id: str = "qoder",
                      lang: str = "zh") -> str:
    """取某个模型的「营销描述」。

    键名约定：`model.<key>.description`。
    """
    if not model_key:
        return ""
    return load(product_id, lang).get("model.%s.description" % model_key, "")


def model_label(model_key: str, *, product_id: str = "qoder",
                lang: str = "zh") -> str:
    """取某个模型的展示名（dynamic-text 可能覆盖）。"""
    if not model_key:
        return ""
    return load(product_id, lang).get("model.%s.label" % model_key, "")


def clear_cache() -> None:
    _CACHE.clear()
