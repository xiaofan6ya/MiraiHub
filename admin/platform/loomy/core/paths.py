"""Loomy 的客户端路径与 UA 前缀。

**这份表属于本平台**，不放总网关 —— 加平台不用改共享文件。
"""
from __future__ import annotations

#: 可执行文件候选（找得到就用它的版本拼 UA）
APP_EXE_CANDIDATES = (
    r"D:\Loomy\Loomy.exe",
    r"C:\Program Files\Loomy\Loomy.exe",
    r"%LOCALAPPDATA%\Programs\Loomy\Loomy.exe",
    r"%LOCALAPPDATA%\Loomy\Loomy.exe",
)

#: 数据目录候选（扫凭据用）
DATA_DIR_CANDIDATES = (
    r"%APPDATA%\loomy",
    r"%LOCALAPPDATA%\Loomy",
)

#: UA 前缀用的产品名（真实完整 UA 格式只有抓包才知道，
#: 这里给「产品名/版本」这个最小可信值）
UA_PRODUCT = "Loomy"
