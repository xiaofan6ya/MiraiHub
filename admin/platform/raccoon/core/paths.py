"""小浣熊客户端路径常量（只用于「装没装」的探测）。

本项目**不依赖**客户端：凭证来自导入。这里保留路径表，是为了
「客户端参数」页能回答「你的小浣熊装在哪」，而不是一片空白。

> 路径表放在平台模块里，不放 admin —— 加/删平台时共享文件一行不用改。
"""
from __future__ import annotations

import os

#: Electron 主程序候选（用户可能装在不同位置）
APP_EXE_CANDIDATES = [
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\raccoon\Raccoon.exe"),
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\xiaohuanxiong\小浣熊.exe"),
    os.path.expandvars(r"%LOCALAPPDATA%\raccoon\Raccoon.exe"),
    os.path.expandvars(r"%PROGRAMFILES%\Raccoon\Raccoon.exe"),
    os.path.expandvars(r"%PROGRAMFILES(X86)%\Raccoon\Raccoon.exe"),
]

#: 数据目录候选（Electron userData）
DATA_DIR_CANDIDATES = [
    os.path.expandvars(r"%APPDATA%\raccoon"),
    os.path.expandvars(r"%APPDATA%\xiaohuanxiong"),
    os.path.expandvars(r"%APPDATA%\小浣熊"),
    os.path.expandvars(r"%LOCALAPPDATA%\raccoon\User Data"),
]

#: UA 里的产品名（探测用）
UA_PRODUCT = "Raccoon"
