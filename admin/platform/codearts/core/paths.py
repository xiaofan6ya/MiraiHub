"""CodeArts 的本机路径表（平台知识，不放进共享模块）。

## 固定的是名字，动态的是位置

安装目录名 ``CodeArts``、数据目录名 ``codearts``、主程序 ``CodeArts.exe``
是客户端写死的，不会变；会变的是**装在哪个盘**和**用户新建的父文件夹**，
由 `admin.platform.install_paths` 动态定位（所有实际存在的盘 + 三层父目录）。

```
<任意盘>/<任意父目录>/CodeArts/
  CodeArts.exe
  resources/app.asar
  resources/build/icon.png
%APPDATA%/codearts/                        ← userData（登录态）
  Local State                            ← OSCrypt 主密钥（DPAPI 保护）
  secretStorage/<sha256>.enc             ← authSession:cn-north-4
```

覆盖（任选其一，立即生效）：环境变量 ``CODEARTS_INSTALL_DIR`` /
``CODEARTS_USER_DATA_DIR`` / ``CODEARTS_APP_EXE``，或后台「客户端路径」设置。
"""
from __future__ import annotations

from admin.platform import install_paths as ip

PLATFORM = "codearts"

#: 固定的目录名 / 文件名（**不要写盘符**）
INSTALL_NAMES = ("CodeArts", "codearts")
EXE_NAMES = ("CodeArts.exe",)
DATA_NAMES = ("codearts",)
#: 用于确认「这个同名目录确实是 CodeArts」
INSTALL_REQUIRE = ("resources/app.asar",)
DATA_REQUIRE = ("Local State", "secretStorage")

#: UA 前缀用的产品名
UA_PRODUCT = "CodeArts"

#: 兼容旧调用方：只保留**不含盘符**的环境变量模板
APP_EXE_CANDIDATES = (
    r"%LOCALAPPDATA%\Programs\CodeArts\CodeArts.exe",
    r"%PROGRAMFILES%\CodeArts\CodeArts.exe",
)
DATA_DIR_CANDIDATES = (r"%APPDATA%\codearts",)


def install_dirs() -> list[str]:
    return ip.resolve(PLATFORM, kind="install_dir", names=INSTALL_NAMES,
                      require=INSTALL_REQUIRE)


def app_exe_candidates() -> list[str]:
    """主程序绝对路径列表（找不到返回空列表，交给 probe 照常兜底）。"""
    return ip.resolve(PLATFORM, kind="app_exe", names=INSTALL_NAMES,
                      exe_names=EXE_NAMES, require=INSTALL_REQUIRE,
                      fallback=(r"%LOCALAPPDATA%\Programs\CodeArts\CodeArts.exe",
                                r"%PROGRAMFILES%\CodeArts\CodeArts.exe"))


def data_dir_candidates(*, all_matches: bool = False) -> list[str]:
    """userData 目录（存在的优先）。"""
    return ip.resolve(PLATFORM, kind="user_data_dir", names=DATA_NAMES,
                      require=DATA_REQUIRE, roots=(r"%APPDATA%",),
                      fallback=DATA_DIR_CANDIDATES, max_depth=0,
                      stop_after_first=not all_matches)


def data_dir() -> str:
    dirs = data_dir_candidates()
    return dirs[0] if dirs else str(ip.expand(DATA_DIR_CANDIDATES[0]))
