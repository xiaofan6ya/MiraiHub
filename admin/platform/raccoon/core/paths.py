"""小浣熊客户端路径常量（只用于「装没装」的探测）。

本项目**不依赖**客户端：凭证来自导入。这里保留路径表，是为了
「客户端参数」页能回答「你的小浣熊装在哪」，而不是一片空白。

目录名固定，盘符 / 父文件夹由 `install_paths` 动态定位；
凭证配置目录取 `~` / `BOX_AGENT_CONFIG_DIR`，不写死任何盘。

> 路径表放在平台模块里，不放 admin —— 加/删平台时共享文件一行不用改。
"""
from __future__ import annotations

import os

from admin.platform import install_paths as ip

PLATFORM = "raccoon"

INSTALL_NAMES = ("raccoon", "Raccoon", "xiaohuanxiong", "小浣熊")
EXE_NAMES = ("Raccoon.exe", "小浣熊.exe")
DATA_NAMES = ("raccoon", "xiaohuanxiong", "小浣熊")

UA_PRODUCT = "Raccoon"

#: 兼容旧调用方：不含盘符的环境变量模板
APP_EXE_CANDIDATES = [
    r"%LOCALAPPDATA%\Programs\raccoon\Raccoon.exe",
    r"%LOCALAPPDATA%\Programs\xiaohuanxiong\小浣熊.exe",
    r"%PROGRAMFILES%\Raccoon\Raccoon.exe",
]
DATA_DIR_CANDIDATES = [
    r"%APPDATA%\raccoon",
    r"%APPDATA%\xiaohuanxiong",
    r"%LOCALAPPDATA%\raccoon\User Data",
]


def install_dirs() -> list[str]:
    return ip.resolve(PLATFORM, kind="install_dir", names=INSTALL_NAMES,
                      exe_names=EXE_NAMES)


def app_exe_candidates() -> list[str]:
    return ip.resolve(PLATFORM, kind="app_exe", names=INSTALL_NAMES,
                      exe_names=EXE_NAMES, fallback=APP_EXE_CANDIDATES)


def data_dir_candidates(*, all_matches: bool = False) -> list[str]:
    return ip.resolve(PLATFORM, kind="user_data_dir", names=DATA_NAMES,
                      roots=(r"%APPDATA%", r"%LOCALAPPDATA%"),
                      fallback=DATA_DIR_CANDIDATES, max_depth=0,
                      stop_after_first=not all_matches)


def config_dir_candidates() -> list[str]:
    """CLI / Agent 的配置目录（环境变量优先，名字固定）。"""
    out: list[str] = []
    for env in ("BOX_AGENT_CONFIG_DIR", "BOX_AGENT_HOME"):
        raw = (os.getenv(env) or "").strip()
        if raw:
            path = ip.expand(raw)
            out.append(str(path / "config" if env.endswith("HOME") else path))
    home = ip.expand("~")
    for name in (".office-raccoon", ".box-agent"):
        # 两种历史布局都保留，由 scan.py 按「目录是否存在」过滤
        out.append(str(home / name / "agent" / "config"))
        out.append(str(home / name / "config"))
    return list(dict.fromkeys(out))
