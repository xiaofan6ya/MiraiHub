"""模型 ID 的**平台后缀**：让「调哪个平台」写进模型名里。

## 要解决的问题

同一个模型 ID 在多个平台都存在（实测 13 个，例如
`deepseek-v4.1-flash` 同时属于 Trae 和 WorkBuddy）。不传 `X-Platform`
时，网关按倍率/健康度自己挑 —— **落点是黑盒**，用户无法控制。

## 方案

在模型名后面加一段「-平台名」后缀，用户就能把落点写进 model 字段：

    deepseek-v4.1-flash-workbuddy   -> 只发 WorkBuddy，上游收到 deepseek-v4.1-flash
    deepseek-v4.1-flash-trae-cn     -> 只发 Trae 国内版
    dfmodel-qoder                   -> Qoder 中国版

网关识别、剥离后缀、按平台转发；上游永远只看到裸 id。
**用户不需要记住任何缩写** —— 后缀就是平台在控制台里的 id。

## 匹配规则

从右往左拼接试匹配，所以 `trae-cn` / `qoder-intl` 这种带连字符的
平台名也能正确识别（不会被 `-cn` 抢先切走）。

## 与显式头的优先级

显式 `X-Platform` 高于后缀（头是更强的意图表达）。两者都写且矛盾时
**以后缀为准**并记一条警告 —— 用户在模型名里写平台，通常是因为
客户端不方便加 header。
"""
from __future__ import annotations

import logging

logger = logging.getLogger("admin.platform.suffix")

#: 后缀分隔符。用 `-` 与平台 id 自身的连字符保持一致。
SEP = "-"

#: 平台 id 按长度降序，保证 `trae-cn` 先于 `trae` 匹配
_cache: dict = {"ids": [], "at": 0.0}
_CACHE_TTL = 30.0


def platform_ids() -> list[str]:
    """所有可用平台 id（按长度降序，长的优先匹配）。"""
    import time

    now = time.time()
    if _cache["ids"] and now - _cache["at"] < _CACHE_TTL:
        return list(_cache["ids"])
    ids: list[str] = []
    try:
        from admin.platform import registry

        from admin.platform.loader import load_platforms

        load_platforms()
        for pid in registry.ids():
            spec = registry.get(pid)
            if spec is not None and spec.enabled:
                ids.append(str(pid))
    except Exception:  # noqa: BLE001 - 读不到就不做后缀解析
        logger.debug("读取平台 id 失败", exc_info=True)
    ids.sort(key=len, reverse=True)
    _cache.update({"ids": ids, "at": now})
    return list(ids)


def invalidate() -> None:
    """平台注册变化后调用。"""
    _cache.update({"ids": [], "at": 0.0})


#: 可读名 -> 上游真实 id 的反向表（由 models_router 的映射自动生成）
def alias_to_upstream(name: str) -> tuple[str, str]:
    """把可读模型名还原成 `(storage, 上游 id)`。

    `deepseek-flash-qoder` -> `("qoder", "dfmodel")`

    这样用户既能用可读名调用，也能用原始 id 调用，两条路都通。
    返回 `("", "")` 表示不是别名。
    """
    from admin.platform import models_router as mr

    raw = str(name or "")
    if not raw:
        return "", ""
    # 先剥平台后缀
    pid, bare, matched = resolve(raw)
    storage = ""
    if matched:
        try:
            from admin.platform import registry

            spec = registry.get(pid)
            storage = str(spec.storage()).lower() if spec else ""
        except Exception:  # noqa: BLE001
            storage = ""
    for plat_storage, table in mr.MODEL_NAME_MAP.items():
        if storage and storage != plat_storage:
            continue
        for upstream_id, readable in table.items():
            if bare == readable or bare == readable.lower():
                if not storage:
                    # ⚠️ **真实模型 id 优先于别名**。
                    #
                    # `MODEL_NAME_MAP` 里 qoder 那张表写的是「缩写 -> 可读名」，
                    # 于是 `deepseek-v4-pro` 既可能是**别人的真实 model_id**
                    # （WorkBuddy 就有这个名字），也可能是 qoder 的 `dmodel`
                    # 的可读名。没有平台后缀时以前一律当成别名反查，
                    # 结果把 `deepseek-v4-pro` 改写成 `dmodel` 发给上游：
                    #
                    #     400 模型 'dmodel' 不存在或已被禁用   ← 线上真实故障
                    #
                    # 所以：能查到这个名字是某个已启用模型时，**不做别名还原**，
                    # 让它按真实模型走下去（qoder 的可读名另有 platform_of_model
                    # + upstream_of 那条路负责，不受影响）。
                    try:
                        if mr.platform_of_model(bare):
                            return "", ""
                    except Exception:  # noqa: BLE001 - 查询失败就按别名处理
                        logger.debug("platform_of_model 查询失败 %s", bare, exc_info=True)
                return plat_storage, upstream_id
    return "", ""


def resolve(model: str) -> tuple[str, str, bool]:
    """解析模型名里的平台后缀。

    返回 `(platform_id, clean_model, matched)`：
        platform_id  匹配到的平台 id；没匹配为空串
        clean_model  去掉后缀的模型名（转发给上游用这个）
        matched      是否命中后缀

    只在**后缀恰好等于某个平台 id** 时才剥离，避免把正常模型名
    （`deepseek-v4-1-flash`、`gpt-4.1-2025-04-14`）误切。
    """
    text = str(model or "").strip()
    if not text:
        return "", text, False

    lowered = text.lower()
    for pid in platform_ids():
        suffix = SEP + pid.lower()
        if not lowered.endswith(suffix):
            continue
        clean = text[: -len(suffix)].rstrip()
        # 剥完必须还剩东西，否则 `-trae-cn` 这种奇怪输入会变成空模型
        if not clean:
            continue
        return pid, clean, True

    # **按 storage 简称兜底**：多区平台的 id 是 `trae-cn` / `trae-intl`，
    # 用户按直觉写成 `xxx-trae` 时上面的精确匹配落空，于是报
    # `模型 'xxx-trae' 不存在`（线上实测就有这种调用）。
    # 这里允许用 storage 名（trae / qoder）代替，落到该 storage 的**默认区**。
    for storage, default_pid in _storage_default_ids():
        suffix = SEP + storage.lower()
        if not lowered.endswith(suffix):
            continue
        clean = text[: -len(suffix)].rstrip()
        if not clean:
            continue
        # 只有当这个 storage 确实存在（有默认区）时才认
        if default_pid:
            return default_pid, clean, True
    return "", text, False


def _storage_default_ids() -> list[tuple[str, str]]:
    """storage 名 -> 该 storage 的默认平台 id（`trae` -> `trae-cn`）。

    多区时优先取 `cn`（与 registry.DEFAULT 的口径一致），否则取第一个。
    结果缓存 30 秒，避免每次解析都遍历注册表。
    """
    import time

    now = time.time()
    cached = _cache.get("storage") or {}
    if cached.get("at") and now - cached["at"] < _CACHE_TTL:
        return list(cached.get("items") or [])
    items: list[tuple[str, str]] = []
    try:
        from admin.platform import registry
        from admin.platform.loader import load_platforms

        load_platforms()
        by_storage: dict[str, list[str]] = {}
        for pid in registry.ids():
            spec = registry.get(pid)
            if spec is None or not spec.enabled:
                continue
            by_storage.setdefault(str(spec.storage()).lower(), []).append(str(pid))
        for storage, pids in by_storage.items():
            # storage 本身就是平台 id（workbuddy / loomy）：不需要别名
            if any(storage == str(p).lower() for p in pids):
                continue
            pids.sort(key=lambda x: 0 if x.endswith("-cn") else 1)
            items.append((storage, pids[0]))
        # 长的优先，避免 `qoder` 抢在 `qoder-intl` 前面
        items.sort(key=lambda kv: -len(kv[0]))
    except Exception:  # noqa: BLE001
        logger.debug("读取 storage 默认区失败", exc_info=True)
    _cache["storage"] = {"items": items, "at": now}
    return items


def apply(platform: str, model: str) -> str:
    """反向操作：给模型名加上平台后缀（生成「精确指向」的名字）。"""
    pid = str(platform or "").strip()
    text = str(model or "").strip()
    if not pid or not text:
        return text
    if text.lower().endswith(SEP + pid.lower()):
        return text
    return "%s%s%s" % (text, SEP, pid)
