from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
METADATA = json.loads((ROOT / "desktop/version.json").read_text(encoding="utf-8"))
VERSION = METADATA["version"]
REPOSITORY = METADATA["repository"]
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"


def data_dir() -> Path:
    path = Path(os.environ.get("WORKBUDDY_DESKTOP_DATA") or
                str(Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "WorkBuddy2API"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def atomic_json(path: Path, value: dict) -> None:
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
