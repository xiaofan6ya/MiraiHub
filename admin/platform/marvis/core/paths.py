"""腾讯 Marvis 的本机路径表（平台知识，不放共享模块）。

目录名固定（见 INSTALL_NAMES / DATA_NAMES），**位置动态**：
由 `admin.platform.install_paths` 在「所有实际存在的盘符」+
「盘下 1~3 层用户新建的父文件夹」里按名字快速定位，命中即停。

禁止在本文件写「盘符开头的绝对路径」（D 盘根目录、系统 Program Files、
公共用户目录的绝对路径都不行）：用户不一定装在同一块盘，
也不一定用默认文件夹名。
覆盖：`<PID>_INSTALL_DIR` / `_USER_DATA_DIR` / `_APP_EXE` 环境变量，
或后台「客户端路径」设置（`/api/client-paths`）。
"""
from __future__ import annotations

from admin.platform import install_paths as ip

PLATFORM = "marvis"

#: 按 recon 结论改：固定的安装目录名 / 数据目录名 / 主程序名
INSTALL_NAMES = ("腾讯 Marvis", "marvis")
EXE_NAMES = ("腾讯 Marvis.exe",)
DATA_NAMES = ("marvis",)
#: 确认「同名目录确实是本平台」的特征（可留空；泛化特征要放进 require
#: 而不是 markers，否则会把其它 Electron 应用也算进来）
INSTALL_REQUIRE = ("resources/app.asar",)
DATA_REQUIRE = ()

UA_PRODUCT = "腾讯 Marvis"

#: 兜底模板只能含 %VAR% / ~，**不含盘符**
APP_EXE_CANDIDATES = (r"%LOCALAPPDATA%\Programs\腾讯 Marvis\腾讯 Marvis.exe",)
DATA_DIR_CANDIDATES = (r"%APPDATA%\marvis",)


def install_dirs() -> list[str]:
    return ip.resolve(PLATFORM, kind="install_dir", names=INSTALL_NAMES,
                      require=INSTALL_REQUIRE)


def app_exe_candidates() -> list[str]:
    return ip.resolve(PLATFORM, kind="app_exe", names=INSTALL_NAMES,
                      exe_names=EXE_NAMES, require=INSTALL_REQUIRE,
                      fallback=APP_EXE_CANDIDATES)


def data_dir_candidates(*, all_matches: bool = False) -> list[str]:
    return ip.resolve(PLATFORM, kind="user_data_dir", names=DATA_NAMES,
                      require=DATA_REQUIRE, roots=(r"%APPDATA%",),
                      fallback=DATA_DIR_CANDIDATES, max_depth=0,
                      stop_after_first=not all_matches)
