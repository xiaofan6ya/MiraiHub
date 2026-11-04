"""Qoder 的客户端路径与 UA 前缀（平台知识，不放总网关）。

目录名固定（``Qoder`` / ``Qoder CN`` 安装目录，``com.qodercn.app.stable``
等数据目录），盘符和用户新建的父文件夹由 `install_paths` 动态定位。

注意：真正的数据目录**不叫 Qoder CN** —— 那个目录下通常只有 Launcher，
登录态在 `%APPDATA%/com.qodercn.app.stable`（CLI 侧还有 `~/.qoder-cn`）。
所以数据候选按真实目录名 + 特征文件确认，而不是按产品名猜。

覆盖：`QODER_INSTALL_DIR` / `QODER_USER_DATA_DIR` / `QODER_APP_EXE`。
"""
from __future__ import annotations

from admin.platform import install_paths as ip

PLATFORM = "qoder"

INSTALL_NAMES = ("Qoder", "Qoder CN")
EXE_NAMES = ("Qoder.exe", "Qoder CN.exe")
#: 顺序即优先级：实测真实位置在最前
DATA_NAMES = ("com.qodercn.app.stable", "com.qoder.app.stable",
              "Qoder", "Qoder CN")
#: 数据目录里至少有其一（加密凭证或账号库），排除 Launcher 空目录
DATA_MARKERS = ("main.sqlite", "auth.v1.dat", "User/settings.json")
INSTALL_REQUIRE = ("resources/app.asar",)

UA_PRODUCT = "Qoder"

#: 兼容旧调用方：不含盘符的环境变量模板
APP_EXE_CANDIDATES = (
    r"%LOCALAPPDATA%\Programs\Qoder\Qoder.exe",
    r"%PROGRAMFILES%\Qoder\Qoder.exe",
)
DATA_DIR_CANDIDATES = (
    r"%APPDATA%\com.qodercn.app.stable",
    r"%APPDATA%\com.qoder.app.stable",
    r"~\.qoder",
    r"~\.qoder-cn",
)


def install_dirs() -> list[str]:
    return ip.resolve(PLATFORM, kind="install_dir", names=INSTALL_NAMES,
                      require=INSTALL_REQUIRE)


def app_exe_candidates() -> list[str]:
    return ip.resolve(PLATFORM, kind="app_exe", names=INSTALL_NAMES,
                      exe_names=EXE_NAMES, require=INSTALL_REQUIRE,
                      fallback=(r"%LOCALAPPDATA%\Programs\Qoder\Qoder.exe",
                                r"%PROGRAMFILES%\Qoder\Qoder.exe"))


def data_dir_candidates(*, all_matches: bool = True) -> list[str]:
    """所有存在的 userData 目录（`probe.enrich` 会合并展示）。"""
    return ip.resolve(PLATFORM, kind="user_data_dir", names=DATA_NAMES,
                      markers=DATA_MARKERS, roots=(r"%APPDATA%",),
                      fallback=DATA_DIR_CANDIDATES, max_depth=0,
                      stop_after_first=not all_matches)


def cli_dir_candidates() -> list[str]:
    """CLI 侧 `~/.qoder-cn` / `~/.qoder`（名字固定，位置随用户目录）。"""
    return ip.resolve(PLATFORM, kind="user_data_dir",
                      names=(".qoder-cn", ".qoder"), roots=("~",),
                      max_depth=0, stop_after_first=False)
