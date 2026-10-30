"""Trae 后台路由集合。

沿用 `admin/routers/` 的约定：一个文件一组端点，`router` 常量由
`admin/server.py` 统一挂载。当前只有一个 `accounts.router`（前缀
`/api/trae`），后续加面板时按同样形状追加，不需要改主服务。
"""
from admin.platform.trae.routers import accounts

__all__ = ["accounts"]