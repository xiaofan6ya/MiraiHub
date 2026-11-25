from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
METADATA = json.loads((ROOT / "desktop/version.json").read_text(encoding="utf-8"))
VERSION = METADATA["version"]
REPOSITORY = METADATA["repository"]
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"


def data_dir() -> Path:
    """客户端数据目录（账号库、日志、运行时都在这里）。

    三端各自的惯例位置，**Windows 的默认值保持原样**（老用户升级后
    不能因为路径变了而「账号全丢」）：

        Windows   %LOCALAPPDATA%\\WorkBuddy2API
        macOS     ~/Library/Application Support/MiraiHub
        Linux     $XDG_DATA_HOME/miraihub（默认 ~/.local/share/miraihub）

    `WORKBUDDY_DESKTOP_DATA` 仍然优先级最高（便携版 / 迁移盘用）。
    """
    override = os.environ.get("WORKBUDDY_DESKTOP_DATA")
    if override:
        path = Path(override)
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / "MiraiHub"
    elif os.name == "nt":
        path = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "WorkBuddy2API"
    else:
        path = Path(os.environ.get("XDG_DATA_HOME",
                                   str(Path.home() / ".local" / "share"))) / "miraihub"
    path.mkdir(parents=True, exist_ok=True)
    return path


def atomic_json(path: Path, value: dict) -> None:
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
