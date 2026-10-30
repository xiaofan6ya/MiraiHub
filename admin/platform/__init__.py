"""网关平台抽象层（gateway platform layer）。

为什么需要这一层：改造前整个后端只有一个隐式上游（WorkBuddy/CodeBuddy）——
`settings.BACKEND` 是模块级常量，`accounts` 表没有任何平台判别列，`/v1/*`
是唯一一套转发实现。要接入 Trae 国内版/国际版，且后续还会持续接新平台，
必须先把「平台」变成一等公民，否则每接一个平台就要复制一遍号池/选号/记账。

本包只负责**注册与解析**，不含任何上游协议：

    register(spec)            平台自我登记（由各平台包在 import 时调用）
    get(pid)                  按 id 取 spec
    all_platforms()           全部已登记平台（按登记顺序，DEFAULT 恒在首位）
    resolve(selector)         把 "trae:cn" / "trae" / "" 解析成 (platform, realm)

上游协议实现放在各平台自己的目录里（例如 `admin/trae/`），通过
`PlatformSpec.gateway` 声明的模块路径懒加载，因此这里不会产生循环导入。
"""
from admin.platform.registry import (
    DEFAULT_PLATFORM,
    PlatformSpec,
    Realm,
    all_platforms,
    get,
    ids,
    normalize_realm,
    register,
    resolve,
    spec_of_account,
)

__all__ = [
    "DEFAULT_PLATFORM",
    "PlatformSpec",
    "Realm",
    "all_platforms",
    "get",
    "ids",
    "normalize_realm",
    "register",
    "resolve",
    "spec_of_account",
]