"""兼容层：实现已搬到 `admin/workbuddy/core/backend.py`。

**这是模块别名，不是复制**：`sys.modules[__name__]` 直接指向真实现，
因此 `from admin.backend import X` 与 `from admin.workbuddy.core.backend import X`
拿到的是同一个对象（`is` 比较为真），不会出现两份模块状态。

为什么不用 `from ... import *`：那会在本模块导入期间执行新模块，
而新模块可能再 import 回本路径，此时本模块尚未初始化完，会 ImportError。

迁移完成后删除本文件。
"""
import sys as _sys

from admin.platform.workbuddy.core import backend as _impl

_sys.modules[__name__] = _impl