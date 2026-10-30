"""平台装配：import 各平台包，触发其 `register()`。

新增平台只需在这里追加一行 import —— 网关、控制台与选号代码都不用改。
单个平台装配失败**不得**影响主服务启动（平台目录可能被裁剪或依赖缺失），
因此这里逐项容错并记录原因，供 `/api/platforms` 暴露给控制台排障。
"""
from __future__ import annotations

import logging

logger = logging.getLogger("admin.platform")

#: 内建平台装配顺序。`workbuddy` 必须第一（默认平台，切换条首位）。
#:
#: 每个平台一个模块，模块内 `install()` 自行登记 `PlatformSpec`。
#: **新增平台只需在这里加一行** —— admin 其余代码一行不用改。
BUILTIN = (
    "admin.platform.workbuddy",
    "admin.platform.trae",
    "admin.platform.qoder",
    "admin.platform.loomy",
)

_loaded: list[str] = []
_failures: dict[str, str] = {}


def load_platforms() -> dict:
    """装配全部内建平台，返回装配结果（幂等，重复调用只装配一次）。"""
    if _loaded or _failures:
        return status()
    import importlib

    for module_name in BUILTIN:
        try:
            module = importlib.import_module(module_name)
            install = getattr(module, "install", None)
            if callable(install):
                install()
            _loaded.append(module_name)
        except Exception as exc:  # noqa: BLE001 - 平台缺失属可降级状态
            _failures[module_name] = f"{type(exc).__name__}: {exc}"
            logger.warning("平台装配失败 %s：%s", module_name, exc)
    return status()


def status() -> dict:
    return {"loaded": list(_loaded), "failed": dict(_failures)}


def reload_platforms() -> dict:
    """强制重新装配（测试与热修用；生产不调用）。"""
    _loaded.clear()
    _failures.clear()
    return load_platforms()