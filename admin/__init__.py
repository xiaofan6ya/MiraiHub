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

    **必须跳过 `converter` 还在初始化的情况**：
    `admin/platform/workbuddy/core/backend.py` 在导入期就
    `from converter import BACKEND, CredentialError, CredentialManager`。
    如果这次 `import admin` 是被 `converter` 自己触发的（converter 顶部
    `from admin.upstream_errors import ...` 就会触发），那么此刻 converter
    模块对象已存在、但只执行了一半，`CredentialManager` 还没定义 ——
    `importlib.import_module` 抛 ImportError 后被下面的 `except` 吞掉，
    于是别名**静默装不上**。

    「吞异常」在这里是陷阱：装配失败长得和「平台目录被裁掉」一模一样，
    排查时完全看不出区别。所以 converter 正在初始化时，只跳过 backend
    这一项（它是唯一反向依赖 converter 的），其余别名照装。
    """
    import importlib
    import sys


    #: converter 是否仍在初始化中（模块对象在、但关键属性还没建出来）。
    _pending = sys.modules.get("converter")
    converter_loading = _pending is not None and not hasattr(
        _pending, "CredentialManager")

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
        if converter_loading and new.endswith(".backend"):
            continue
        try:
            module = importlib.import_module(new)
        except Exception:  # noqa: BLE001 - 新模块缺失时保持旧路径原样
            continue
        sys.modules[old] = module
        # **同时挂成包属性**。只写 `sys.modules` 时，`import admin.backend`
        # 能拿到模块，但 `admin.backend` 这个属性访问是另一回事：
        # `patch('admin.backend.AccountSession')` 走的是
        # `getattr(admin, 'backend')` → AttributeError: module 'admin' has no
        # attribute 'backend'，于是所有以字符串路径 patch 的测试集体报错。
        #
        # 正常 `import x.y` 结束后解释器会自动做 `setattr(x, 'y', ...)`，
        # 但这里我们是**手动**改写 `sys.modules`，绕过了那一步，必须自己补。
        parent_name, _, leaf = old.rpartition(".")
        parent = sys.modules.get(parent_name) if parent_name else None
        if parent is not None:
            setattr(parent, leaf, module)


_install_module_aliases()
