"""workbuddy2api 管理后台（多账号反代 + API Key 网关）。

## 为什么这里要预热模块别名

WorkBuddy 的实现已搬进 `admin/workbuddy/core/`，`admin/backend.py` 等旧路径
保留为**模块别名**（`sys.modules[__name__] = 真模块`）。

别名有一个时序陷阱：如果第一次 `import admin.backend` 发生在
`client_profile` 正在初始化的过程中（`client_profile` 内部有
`from admin.backend import stable_device_id`），就会出现
「部分初始化的模块」→ ImportError。

在包初始化时**先**把真模块装进 `sys.modules`，后续任何时序都拿到完整对象。
这一步只是固定导入顺序，不改变任何行为。
"""
from __future__ import annotations


def _install_module_aliases() -> None:
    """把 WorkBuddy 的旧模块路径指向新实现（幂等）。

    只处理「已搬家」的模块；新模块不存在时静默跳过，
    这样裁剪掉 workbuddy 目录的部署仍然可用。
    """
    import importlib
    import sys

    pairs = (
        # 旧路径 -> 新路径
        ("admin.backend", "admin.platform.workbuddy.core.backend"),
        ("admin.wb_login", "admin.platform.workbuddy.core.wb_login"),
        ("admin.credential_codec", "admin.platform.workbuddy.core.credential_codec"),
        ("admin.turing_token", "admin.platform.workbuddy.core.turing_token"),
        ("admin.wb_paths", "admin.platform.workbuddy.core.wb_paths"),
        ("admin.upstream_identity", "admin.platform.workbuddy.core.upstream_identity"),
    )
    for old, new in pairs:
        try:
            module = importlib.import_module(new)
        except Exception:  # noqa: BLE001 - 新模块缺失时保持旧路径原样
            continue
        sys.modules[old] = module


_install_module_aliases()