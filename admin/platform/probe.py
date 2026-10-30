"""通用探测工具（**不含任何平台特定的路径表**）。

## 为什么改成这样

用户指出：「你怎么又写死到 platform/probe.py 总网关里面了，
这样之后不好维护的，应该由各个平台网关探测上报（这样是动态的）」

对。平台特定的安装路径 / 数据目录 / UA 前缀属于**平台自己的知识**，
放在总网关的字典里有两个问题：

  * 加平台要改总网关（违反「加平台只改 loader 一行」的约定）
  * 一个平台的路径变化会牵动共享文件

## 现在只剩「与平台无关」的工具

  * `exe_version(path)`       读任意 exe 的版本信息
  * `find_files(roots, ...)`  在给定根目录下找匹配的文件
  * `build_ua(product, ver)`  按「产品名/版本」拼 UA

平台自己通过 `ClientProbe.probe()` 上报：

    {"app_path": "...", "app_version": "0.4.11",
     "data_dirs": [...], "ua": "Qoder/0.4.11"}

总网关只负责**组装**，不持有任何路径表。
"""
from __future__ import annotations

import logging
import os
import pathlib
import subprocess
import sys

logger = logging.getLogger("admin.platform.probe")


def exe_version(path) -> dict:
    """读 exe 的版本信息。

    与平台无关的通用工具 —— 任何 exe 都能读。
    读不到返回 `{}`，**不伪造版本号**。

    返回 `{"product_version", "file_version", "product_name", "company", "source"}`
    """
    target = pathlib.Path(str(path))
    if not target.is_file():
        return {}
    try:
        if sys.platform == "win32":
            # 用 PowerShell 读，避免引入 pywin32 依赖
            script = (
                "$v=(Get-Item -LiteralPath '%s').VersionInfo;"
                "Write-Output ($v.ProductVersion + '|' + $v.FileVersion + '|' "
                "+ $v.ProductName + '|' + $v.CompanyName)"
            ) % str(target).replace("'", "''")
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True, text=True, timeout=15)
            text = (result.stdout or "").strip()
            if "|" in text:
                parts = [part.strip() for part in text.split("|")]
                return {
                    "product_version": parts[0] if len(parts) > 0 else "",
                    "file_version": parts[1] if len(parts) > 1 else "",
                    "product_name": parts[2] if len(parts) > 2 else "",
                    "company": parts[3] if len(parts) > 3 else "",
                    "source": str(target),
                }
    except Exception:  # noqa: BLE001 - 读不到就算了
        logger.debug("读 exe 版本失败: %s", target, exc_info=True)
    return {}


def expand(pattern) -> pathlib.Path:
    """展开 `~` 与环境变量。"""
    text = str(pattern or "")
    return pathlib.Path(os.path.expandvars(
        text.replace("~", os.path.expanduser("~"))))


def find_files(roots, *, pattern: str = "*.exe",
               depth: int = 2, limit: int = 4) -> list[str]:
    """在给定根目录下找匹配的文件（限深度，避免扫全盘）。

    **候选根目录由平台自己给出**（那是平台知识），这个函数只负责找。
    """
    found: list[str] = []
    for raw in roots or []:
        root = expand(raw)
        if not root.is_dir():
            continue
        base_depth = len(root.parts)
        try:
            for current, dirs, files in os.walk(root):
                if len(pathlib.Path(current).parts) - base_depth > depth:
                    dirs[:] = []
                    continue
                for name in files:
                    if pathlib.PurePath(name).match(pattern):
                        found.append(str(pathlib.Path(current) / name))
                        if len(found) >= limit:
                            return found
        except OSError:
            continue
    return found[:limit]


def build_ua(product: str, version: str) -> str:
    """按「产品名/版本」拼 UA。

    这是**最小可信值** —— 各平台真实 UA 的完整格式只有抓包才知道。
    版本号截掉末尾的 `.0`（`0.4.11.0` -> `0.4.11`），与客户端显示一致。
    """
    text = str(version or "").strip()
    if not text or not product:
        return ""
    parts = text.split(".")
    if len(parts) == 4 and parts[-1] == "0":
        text = ".".join(parts[:3])
    return "%s/%s" % (product, text)

def enrich(payload: dict, *, exe_candidates, data_dirs,
           product: str) -> dict:
    """给探测结果补 `app_path` / `app_version` / `ua`。

    **各平台自己传候选路径**（平台知识），这个函数只负责
    「读版本 + 拼 UA + 合并目录」，与具体平台无关。

    参数：
        payload          平台 `probe()` 已收集的结果
        exe_candidates   该平台的 exe 候选路径（平台自己给）
        data_dirs        该平台的数据目录候选（平台自己给）
        product          UA 前缀用的产品名（平台自己给）

    返回合并后的 payload（原地修改也会同步）。
    """
    result = dict(payload or {})

    # 找 exe 并读版本
    exe_path = ""
    version_info: dict = {}
    for candidate in exe_candidates or []:
        path = expand(candidate)
        if path.is_file():
            version_info = exe_version(path)
            if version_info:
                exe_path = str(path)
                break
    if exe_path:
        result.setdefault("app_path", exe_path)
        result.setdefault("app_version",
                          version_info.get("product_version") or "")
        ua = build_ua(product, version_info.get("product_version") or "")
        if ua:
            result.setdefault("ua", ua)

    # 合并数据目录（保留平台自己已有的）
    existing = list(result.get("data_dirs") or [])
    for candidate in data_dirs or []:
        path = expand(candidate)
        if path.is_dir():
            text = str(path)
            if text not in existing:
                existing.append(text)
    if existing:
        result["data_dirs"] = existing

    return result
