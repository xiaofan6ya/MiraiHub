"""腾讯 Marvis 本机扫描。

## 找凭证的正确顺序（按可行性）

1. **读源码找存储名**：`new Store({ name: 'auth-session' })` → `<userData>/auth-session.json`
2. **找自定义 userDataPath**：很多产品不用 `%APPDATA%/<产品名>`
3. 找到明文 JSON → 直接读
4. 加密（DPAPI/v10）→ 记下位置，提示用户需要重新登录
5. 都没有 → 走设备流授权

## 实测教训

* 产品名 ≠ 数据目录名（Qoder：`com.qodercn.app.stable`）
* 自定义 userData 的根目录取环境变量（Loomy：`%PUBLIC%/Loomy/<hash>`），
  **不要写 `C:/Users/Public`** —— 系统盘不一定是 C
* 安装目录名固定、盘符与父文件夹动态 → 一律走 `core/paths.py` + `install_paths`
* 业务文件可能只有几百字节
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import pathlib
import sys

logger = logging.getLogger("admin.platform.marvis.scan")
PLATFORM = "marvis"

#: 固定的目录名与位置解析统一放在 `core/paths.py`（本文件不写任何盘符）
#: electron-store 的凭证文件名（源码里的 `name`）
CRED_FILE = "auth-session.json"


def mask(value: str, keep: int = 4) -> str:
    text = str(value or "")
    if len(text) <= keep * 2:
        return text[:keep] + "***" if text else ""
    return text[:keep] + "*" * min(8, len(text) - keep * 2) + text[-keep:]


def user_data_dirs() -> list[pathlib.Path]:
    """候选数据目录（由本平台 `core/paths.py` 动态解析，不写死盘符）。"""
    from admin.platform.marvis.core import paths

    out = []
    for raw in paths.data_dir_candidates(all_matches=True):
        path = pathlib.Path(raw)
        if path.is_dir() and path not in out:
            out.append(path)
    return out


def read_credentials(data_dir: pathlib.Path) -> dict | None:
    """读明文凭证文件。

    返回 {session, userid, phone} 或 None。
    **加密就返回 None** —— 不要尝试解密后再失败，那会浪费几分钟。
    """
    path = data_dir / CRED_FILE
    if not path.is_file():
        return None
    try:
        raw = path.read_bytes()[:4096]
    except OSError:
        return None
    if raw[:3] == b"v10" or raw[:1] not in (b"{", b"["):
        logger.info("%s 存在但不是明文 JSON（可能是加密）", path.name)
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    if not (data.get("session") or data.get("token")):
        return None
    return data


def discover() -> dict:
    """扫描本机（**结果不含完整 token**）。"""
    dirs = user_data_dirs()
    items: list[dict] = []
    hint = ""

    for data_dir in dirs:
        creds = read_credentials(data_dir)
        if creds:
            items.append({
                "source": str(data_dir / CRED_FILE),
                "session": mask(str(creds.get("session") or creds.get("token") or "")),
                "userid": mask(str(creds.get("userid") or "")),
                "phone": mask(str(creds.get("phone") or "")),
                "has_plain_credential": True,
            })
        else:
            items.append({
                "source": str(data_dir),
                "has_plain_credential": False,
            })

    if not items:
        hint = ("没找到 腾讯 Marvis 的数据目录。"
                "请先运行 skill 的 creds.py 定位真实路径"
                "（产品名常与目录名不同）。")
    elif not any(i.get("has_plain_credential") for i in items):
        hint = ("找到数据目录但没有可读凭证（可能加密）。"
                "请在客户端登录一次后重试；或走设备流授权。")

    return {
        "platform": PLATFORM,
        "data_dirs": [str(d) for d in dirs],
        "items": items,
        "can_import": any(i.get("has_plain_credential") for i in items),
        "hint": hint,
        "errors": [],
    }
