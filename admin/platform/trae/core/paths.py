"""Trae 的客户端路径与 UA 前缀（平台知识，不放总网关）。

目录名固定（``Trae CN`` / ``Trae``），盘符与用户新建的父文件夹由
`install_paths` 动态定位。`core/oauth.py` 读 `product.json` 里的 appVersion
也走这里的安装目录，不再只认 `D:\\Trae CN`。

覆盖：`TRAE_INSTALL_DIR` / `TRAE_USER_DATA_DIR` / `TRAE_APP_EXE`。
"""
from __future__ import annotations

from admin.platform import install_paths as ip

PLATFORM = "trae"

INSTALL_NAMES = ("Trae CN", "Trae")
EXE_NAMES = ("Trae CN.exe", "Trae.exe")
DATA_NAMES = ("Trae CN", "Trae")
#: VSCode 系安装包的共有特征
INSTALL_REQUIRE = ("resources/app/product.json",)

UA_PRODUCT = "Trae"

APP_EXE_CANDIDATES = (
    r"%LOCALAPPDATA%\Programs\Trae CN\Trae CN.exe",
    r"%LOCALAPPDATA%\Programs\Trae\Trae.exe",
    r"%PROGRAMFILES%\Trae CN\Trae CN.exe",
    r"%PROGRAMFILES%\Trae\Trae.exe",
)
DATA_DIR_CANDIDATES = (r"%APPDATA%\Trae CN", r"%APPDATA%\Trae")


def install_dirs() -> list[str]:
    return ip.resolve(PLATFORM, kind="install_dir", names=INSTALL_NAMES,
                      require=INSTALL_REQUIRE)


def app_exe_candidates() -> list[str]:
    return ip.resolve(PLATFORM, kind="app_exe", names=INSTALL_NAMES,
                      exe_names=EXE_NAMES, require=INSTALL_REQUIRE,
                      fallback=APP_EXE_CANDIDATES)


def data_dir_candidates(*, all_matches: bool = False) -> list[str]:
    return ip.resolve(PLATFORM, kind="user_data_dir", names=DATA_NAMES,
                      roots=(r"%APPDATA%",), fallback=DATA_DIR_CANDIDATES,
                      max_depth=0, stop_after_first=not all_matches)


def product_json_candidates() -> list[str]:
    """product.json 的完整路径（Windows / macOS 两种布局）。"""
    from pathlib import Path

    out: list[str] = []
    # 非 Windows 的固定安装位置（盘符概念不适用，但目录名仍固定）
    roots = list(install_dirs()) + [
        "/opt/trae-cn", "/opt/trae",
        "/Applications/Trae CN.app", "/Applications/Trae.app",
    ]
    for root in roots:
        base = Path(root)
        out.append(str(base / "resources" / "app" / "product.json"))
        out.append(str(base / "Contents" / "Resources" / "app" / "product.json"))
    return list(dict.fromkeys(out))
