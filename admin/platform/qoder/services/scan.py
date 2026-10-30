"""Qoder 本机扫描：从客户端 / CLI 找到已登录账号。

## 数据在哪（实测定位）

主进程的 `--user-data-dir` 就是真位置，**产品名和目录名不一样**：

    %APPDATA%\\com.qodercn.app.stable      ← 客户端（不是 "Qoder CN"！）
    ~/.qoder-cn                            ← CLI
        .models/<uid>/catalog-v7           ← 模型目录（QMC 容器）
        .auth/machine_id                   ← 机器标识
        .qoder-app-status.json

## 凭证为什么读不到明文

`auth.v1.dat`（547 字节，开头 `v10`）是**加密**的，解密实现在原生模块里
（JS bundle 里搜不到 `auth.v1` / `safeStorage` / `keytar` 的实现）。
所以扫描走**另一条路**：从 CLI 侧的明文文件 + 主 sqlite 的账号标识拼出
「有哪些账号」，token 交给用户补（PAT 或设备流）。

## 前提：先拿到 user-data-dir

不要猜产品名 —— 从运行中的进程命令行读 `--user-data-dir`，
这是唯一可靠的方式（实测 `Qoder CN` 目录只有 Launcher，真正的数据在
`com.qodercn.app.stable`）。
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import re
import sqlite3
import subprocess
import sys

logger = logging.getLogger("admin.platform.qoder.scan")

PLATFORM = "qoder"

#: 候选数据目录名（按优先级）。**注意 `com.qodercn.app.stable` 在第一位** ——
#: 它是实测的真实位置，`Qoder CN` 那个只有 Launcher。
DATA_DIR_CANDIDATES = (
    "com.qodercn.app.stable",
    "com.qoder.app.stable",
    "Qoder",
    "Qoder CN",
)

#: CLI 侧目录
CLI_DIR_CANDIDATES = (".qoder-cn", ".qoder")


def mask(value: str, keep: int = 4) -> str:
    """脱敏：只留头尾，中间打码。

    扫描结果会进日志和界面，**绝不能带出完整 uid / email**。
    """
    text = str(value or "")
    if len(text) <= keep * 2:
        return text[:keep] + "***" if text else ""
    return text[:keep] + "*" * min(8, len(text) - keep * 2) + text[-keep:]


def _appdata() -> pathlib.Path:
    return pathlib.Path(os.environ.get("APPDATA") or pathlib.Path.home() / "AppData/Roaming")


def _home() -> pathlib.Path:
    return pathlib.Path.home()


def user_data_dirs() -> list[pathlib.Path]:
    """候选数据目录（存在的才算）。"""
    root = _appdata()
    out = []
    for name in DATA_DIR_CANDIDATES:
        path = root / name
        if path.is_dir():
            out.append(path)
    return out


def live_user_data_dir() -> pathlib.Path | None:
    """从运行中的进程读 `--user-data-dir`（最可靠）。

    产品名和目录名不一致时，猜目录会全部落空 —— 实测 `Qoder CN`
    下面只有 Launcher，真正数据在 `com.qodercn.app.stable`。
    """
    if sys.platform != "win32":
        return None
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Process -Filter \"Name='Qoder CN.exe'\" "
             "| Select-Object -ExpandProperty CommandLine) -join \"`n\""],
            capture_output=True, text=True, timeout=15)
    except Exception:  # noqa: BLE001
        return None
    for line in (result.stdout or "").splitlines():
        match = re.search(r'--user-data-dir=(?:"([^"]+)"|(\S+))', line)
        if match:
            path = pathlib.Path((match.group(1) or match.group(2)).strip())
            if path.is_dir():
                return path
    return None


def cli_dirs() -> list[pathlib.Path]:
    home = _home()
    return [home / name for name in CLI_DIR_CANDIDATES if (home / name).is_dir()]


# ---------------------------------------------------------------------------
# 账号识别
# ---------------------------------------------------------------------------
def accounts_from_sqlite(data_dir: pathlib.Path) -> list[dict]:
    """从 `main.sqlite` 的 `account_profiles` 读账号标识。

    表里只有 `account_id`（形如 `qoder:prod:cn:<hash>`），**没有 token** ——
    但足以回答「本机登录过哪个区的哪个账号」。
    """
    db_path = data_dir / "main.sqlite"
    if not db_path.is_file():
        return []
    out: list[dict] = []
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True, timeout=5)
        try:
            rows = conn.execute(
                "SELECT account_id, work_role, updated_at FROM account_profiles"
            ).fetchall()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug("读 Qoder main.sqlite 失败：%s", exc)
        return []

    for account_id, work_role, updated_at in rows:
        text = str(account_id or "")
        parts = text.split(":")
        # 形如 qoder:prod:cn:<hash> / qoder:prod:global:<hash>
        realm = "cn"
        if len(parts) >= 3:
            realm = "intl" if parts[2] in ("global", "intl") else "cn"
        out.append({
            "account_id": text,
            "uid_hint": mask(parts[3]) if len(parts) > 3 else "",
            "realm": realm,
            "work_role": str(work_role or ""),
            "updated_at": updated_at,
            "source": str(db_path),
        })
    return out


def catalog_dirs() -> list[dict]:
    """CLI 侧 `.models/<uid>/catalog-v7` —— 有它就说明这个 uid 登录过。

    文件名是随机目录名，但 `default` 文件里有真实 uid。
    """
    out: list[dict] = []
    for cli in cli_dirs():
        models = cli / ".models"
        if not models.is_dir():
            continue
        default_file = models / "default"
        uid = ""
        if default_file.is_file():
            try:
                data = json.loads(default_file.read_text(encoding="utf-8"))
                uid = str(data.get("uid") or "")
            except (ValueError, OSError):
                pass
        for sub in models.iterdir():
            if not sub.is_dir():
                continue
            catalog = sub / "catalog-v7"
            if not catalog.is_file():
                continue
            out.append({
                "dir": str(sub),
                "uid": uid or sub.name,
                "catalog_bytes": catalog.stat().st_size,
                "has_catalog": True,
            })
    return out


def machine_id_from_cli() -> str:
    """CLI 侧 `.auth/machine_id`（有就复用，让同一台机器的签名稳定）。"""
    for cli in cli_dirs():
        path = cli / ".auth" / "machine_id"
        if path.is_file():
            try:
                text = path.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if text:
                return text
    return ""


def app_status() -> dict:
    for cli in cli_dirs():
        path = cli / ".qoder-app-status.json"
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            if isinstance(data, dict):
                return data
    return {}


# ---------------------------------------------------------------------------
# 对外：扫描
# ---------------------------------------------------------------------------
def discover() -> dict:
    """扫描本机 Qoder 登录态。

    返回的是**可识别信息**（哪个区、哪个账号、有没有凭证文件），
    **不含任何 token**。

    为什么扫不到 token：`auth.v1.dat` 是加密的（解密在原生模块里），
    这不是「没写扫描」，是上游把凭证保护在加密层。
    要拿到可用 token，需要 PAT 或设备流授权。
    """
    live = live_user_data_dir()
    dirs = user_data_dirs()
    if live and live not in dirs:
        dirs.insert(0, live)

    accounts: list[dict] = []
    errors: list[str] = []

    for data_dir in dirs:
        try:
            found = accounts_from_sqlite(data_dir)
        except Exception as exc:  # noqa: BLE001
            errors.append("%s: %s" % (data_dir.name, str(exc)[:120]))
            continue
        for item in found:
            item["data_dir"] = str(data_dir)
            accounts.append(item)
        auth_file = data_dir / "auth.v1.dat"
        if auth_file.is_file():
            accounts.append({
                "account_id": "",
                "realm": "",
                "credential_file": str(auth_file),
                "credential_encrypted": True,
                "credential_bytes": auth_file.stat().st_size,
                "source": str(auth_file),
                "note": "凭证文件加密，需 PAT 或设备流授权才能取得可用令牌",
            })

    catalogs = catalog_dirs()
    cli_machine = machine_id_from_cli()
    status = app_status()

    return {
        "platform": PLATFORM,
        "data_dirs": [str(d) for d in dirs],
        "cli_dirs": [str(d) for d in cli_dirs()],
        "live_user_data_dir": str(live) if live else "",
        "machine_id": cli_machine,
        "app_status": {k: status[k] for k in ("version", "channel", "locale")
                       if k in status},
        "catalogs": [{**c, "uid": mask(c["uid"])} for c in catalogs],
        "items": accounts,
        "can_import": False,
        "hint": ("Qoder 的登录凭证加密在原生层（auth.v1.dat），"
                 "无法直接读出。请在 "
                 "https://qoder.com.cn/account/integrations 生成个人访问令牌（PAT），"
                 "或用「设备授权」在本机登录。"),
        "errors": errors,
    }
