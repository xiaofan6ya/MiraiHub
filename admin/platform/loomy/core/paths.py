"""Loomy 的客户端路径与 UA 前缀（平台知识，不放总网关）。

安装目录名固定（``Loomy``），盘符和用户新建的父文件夹由 `install_paths`
动态定位。注意**凭证不在安装目录**：客户端 `config-path.js` 把登录态写在
``%PUBLIC%/Loomy/<sha256(username)[:12]>/userData``，那部分由
`services/scan.py` 负责（父目录取 `%PUBLIC%` 环境变量，不写死 C 盘）。

覆盖：`LOOMY_INSTALL_DIR` / `LOOMY_USER_DATA_DIR` / `LOOMY_CONFIG_ROOT` /
`LOOMY_APP_EXE`。
"""
from __future__ import annotations

from admin.platform import install_paths as ip

PLATFORM = "loomy"

INSTALL_NAMES = ("Loomy",)
EXE_NAMES = ("Loomy.exe",)
DATA_NAMES = ("loomy", "Loomy")
INSTALL_REQUIRE = ("resources/app.asar",)

UA_PRODUCT = "Loomy"

APP_EXE_CANDIDATES = (
    r"%LOCALAPPDATA%\Programs\Loomy\Loomy.exe",
    r"%PROGRAMFILES%\Loomy\Loomy.exe",
)
DATA_DIR_CANDIDATES = (r"%APPDATA%\loomy", r"%LOCALAPPDATA%\Loomy")

#: 配置根父目录（**环境变量**，不是写死的 `C:/Users/Public`）
CONFIG_ROOT_PARENT = r"%PUBLIC%\Loomy"


def install_dirs() -> list[str]:
    return ip.resolve(PLATFORM, kind="install_dir", names=INSTALL_NAMES,
                      require=INSTALL_REQUIRE)


def app_exe_candidates() -> list[str]:
    return ip.resolve(PLATFORM, kind="app_exe", names=INSTALL_NAMES,
                      exe_names=EXE_NAMES, require=INSTALL_REQUIRE,
                      fallback=APP_EXE_CANDIDATES)


def data_dir_candidates(*, all_matches: bool = False) -> list[str]:
    return ip.resolve(PLATFORM, kind="user_data_dir", names=DATA_NAMES,
                      roots=(r"%APPDATA%", r"%LOCALAPPDATA%"),
                      fallback=DATA_DIR_CANDIDATES, max_depth=0,
                      stop_after_first=not all_matches)


def config_root_parent():
    """配置根的**父**目录：`%PUBLIC%/Loomy`（位置随系统环境变量）。

    注意 `LOOMY_CONFIG_ROOT` 覆盖的是**配置根本身**（含 `userData/` 的那层），
    由 `services/scan.py::config_root()` 消费，不在这里读。
    """
    return ip.expand(CONFIG_ROOT_PARENT)
