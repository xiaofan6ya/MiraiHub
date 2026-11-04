#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""与平台无关的「客户端安装目录 / 数据目录」快速定位。

## 前提（用户明确过）

**上游目录名是固定的**（``WorkBuddy`` / ``CodeArts`` / ``Qoder`` /
``Trae CN`` / ``Loomy`` / ``codearts`` …），会变的只有两样：

1. 用户把客户端装在哪个**盘**（不一定是 D，系统盘也不一定是 C）；
2. 用户在盘里**新建的父文件夹**（``E:\\我的软件\\AI工具\\WorkBuddy``）。

所以这里不做按内容特征的全盘反查、不调 PowerShell 查进程/注册表 ——
**直接按固定名字，在动态盘符下做有限深度的目录名定位**，命中即停。
几百次 `scandir` 就能覆盖常见布局，实测冷启动 < 50ms、之后走缓存 ~0ms。

## 优先级

1. 显式覆盖：``<PID>_INSTALL_DIR`` / ``<PID>_USER_DATA_DIR`` /
   ``<PID>_CONFIG_ROOT`` / ``<PID>_APP_EXE``，或后台保存值
   （``system_settings.client_paths``）。环境变量最高（运维逃生门）。
2. 常见环境变量基目录 + 固定目录名（``%LOCALAPPDATA%\\Programs``、
   ``%PROGRAMFILES%``、``%APPDATA%``、``%PUBLIC%`` …）。
3. **实际存在**的所有盘符 + 盘下一到三层动态父文件夹 + 固定目录名。
4. 平台自带的兜底模板（只含 `%VAR%` / `~`，不含盘符）。

## 慢 I/O 约束（前车之鉴）

后台保存值要走数据库，冷启动一次约 0.3s，**不能放在展示接口的请求线程**。
`warmup()` 在服务启动时用后台线程预加载；同步调用只读进程内缓存。
扫描结果同样带短 TTL 缓存，连续刷新不重复扫盘。

平台知识（产品名、特征文件、exe 名）仍放在各平台自己的 `core/paths.py`，
本文件不持有任何平台路径表 —— 加平台不改这里。
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import re
import string
import sys
import threading
import time

_logger = logging.getLogger("admin.platform.install_paths")

# --------------------------------------------------------------------------- #
# 覆盖值
# --------------------------------------------------------------------------- #
#: 允许的覆盖项
KEYS = ("install_dir", "user_data_dir", "config_root", "app_exe")

_DB_KEY = "client_paths"
_SAVED_TTL = 300.0

_lock = threading.RLock()
_saved_cache: dict = {}
_saved_at = 0.0
_saved_loading = False

#: 覆盖路径填错时的提示（诊断接口显示，不抛异常）
warnings: list[str] = []


def env_name(platform: str, key: str) -> str:
    """`codearts` + `user_data_dir` -> `CODEARTS_USER_DATA_DIR`。"""
    pid = re.sub(r"[^A-Za-z0-9]+", "_", str(platform or "").strip()).upper()
    return "%s_%s" % (pid, str(key).upper())


def expand(raw) -> pathlib.Path:
    """展开路径：引号、`~`、`%VAR%`（Windows 写法）、`$VAR` / `${VAR}`。

    `os.path.expandvars` 在非 Windows 上不认 `%VAR%`，所以补一次正则 ——
    用户从资源管理器复制路径常带引号，`.env` 里常写 `%LOCALAPPDATA%`。
    """
    text = str(raw or "").strip().strip('"').strip("'").strip()
    if not text:
        return pathlib.Path("")
    text = os.path.expandvars(text)
    text = re.sub(r"%([^%]+)%",
                  lambda m: os.getenv(m.group(1)) or m.group(0), text)
    try:
        return pathlib.Path(text).expanduser()
    except (OSError, ValueError):
        return pathlib.Path(text)


def _db_load() -> dict:
    try:
        from admin.db import SessionLocal
        from admin.models import SystemSetting

        db = SessionLocal()
        try:
            row = db.query(SystemSetting).filter(
                SystemSetting.key == _DB_KEY).first()
            if row and row.value:
                obj = json.loads(row.value)
                if isinstance(obj, dict):
                    return obj
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001 - 无 DB / 表未建都得能跑
        _logger.debug("读取客户端路径覆盖失败：%s", exc)
    return {}


def warmup() -> None:
    """后台预加载覆盖值（服务启动时调一次，请求线程零数据库 I/O）。"""
    global _saved_loading
    with _lock:
        if _saved_loading or _saved_at:
            return
        _saved_loading = True

    def _run():
        global _saved_at, _saved_loading
        data = _db_load()
        with _lock:
            _saved_cache.update(data)
            _saved_at = time.time()
            _saved_loading = False

    threading.Thread(target=_run, name="client-paths-warmup", daemon=True).start()


def _saved() -> dict:
    """只读缓存；没预热过就返回空（由 warmup 后台补），**不阻塞请求**。"""
    with _lock:
        return dict(_saved_cache)


def saved_overrides() -> dict:
    """后台保存的全部覆盖（按平台分组）；诊断接口用，会同步读一次库。"""
    data = _db_load()
    with _lock:
        global _saved_at
        _saved_cache.clear()
        _saved_cache.update(data)
        _saved_at = time.time()
    return data


def _write_saved(data: dict) -> None:
    try:
        from admin.db import SessionLocal
        from admin.models import SystemSetting

        db = SessionLocal()
        try:
            row = db.query(SystemSetting).filter(
                SystemSetting.key == _DB_KEY).first()
            value = json.dumps(data, ensure_ascii=False)
            if row:
                row.value = value
            else:
                db.add(SystemSetting(key=_DB_KEY, value=value))
            db.commit()
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        _logger.warning("保存客户端路径覆盖失败：%s", exc)
        raise
    with _lock:
        global _saved_at
        _saved_cache.clear()
        _saved_cache.update(data)
        _saved_at = time.time()
        _scan_cache.clear()


def save(platform: str, values: dict) -> dict:
    """保存某平台的覆盖（merge 语义，空串 = 清除该项），立即生效。"""
    pid = str(platform or "").strip().lower()
    data = _saved()
    cur = dict(data.get(pid) or {})
    for key in KEYS:
        if key in values:
            text = str(values.get(key) or "").strip()
            if text:
                cur[key] = text
            else:
                cur.pop(key, None)
    if cur:
        data[pid] = cur
    else:
        data.pop(pid, None)
    _write_saved(data)
    return cur


def clear(platform: str) -> dict:
    """清空某平台的全部覆盖，回到自动发现。"""
    pid = str(platform or "").strip().lower()
    data = _saved()
    data.pop(pid, None)
    _write_saved(data)
    return {}


def override(platform: str, key: str) -> tuple[str, str]:
    """取某项覆盖，返回 `(路径, 来源)`；没有则 `("", "")`。

    优先级：环境变量 > 后台保存值。只读内存 / 环境变量，不查库。
    """
    name = env_name(platform, key)
    env = (os.getenv(name) or "").strip()
    if env:
        return str(expand(env)), "环境变量 " + name
    pid = str(platform or "").strip().lower()
    val = str((_saved().get(pid) or {}).get(key) or "").strip()
    if val:
        return str(expand(val)), "后台设置"
    return "", ""


def invalidate_cache() -> None:
    """覆盖值变化 / 用户装了新客户端后，丢弃定位缓存。"""
    with _lock:
        _scan_cache.clear()
        warnings.clear()


# --------------------------------------------------------------------------- #
# 动态盘符 / 动态父文件夹
# --------------------------------------------------------------------------- #
#: 不进入的系统目录（用户不会把客户端装在这里，进去只浪费 I/O）
_SKIP = {
    "$recycle.bin", "system volume information", "windows", "windows.old",
    "programdata", "recovery", "perflogs", "node_modules", ".git", "appdata",
    "temp", "tmp", "cache", "windowsapps", "packagecache", "$windows.~ws",
    "documents and settings", "config.msi", "msocache", "intel", "amd",
    "nvidia", "drivers", "boot", "efi", "sources", "users",
}
#: 单次定位最多遍历多少目录（防止某个盘被塞满目录时拖慢展示接口）
_MAX_DIRS = 200
#: 从盘根往下最多走几层（覆盖 E:\我的软件\AI工具\WorkBuddy 这类布局）
_MAX_DEPTH = 3
#: 每层最多展开多少个子目录（命中即停，通常到不了这个上限）
_MAX_PER_LEVEL = 120


def active_drives() -> list[pathlib.Path]:
    """**实际存在**的逻辑盘（不是写死的 C,D,E,F,G）。"""
    if sys.platform == "win32":
        try:
            import ctypes

            mask = ctypes.windll.kernel32.GetLogicalDrives()
            return [pathlib.Path(ch + ":\\")
                    for i, ch in enumerate(string.ascii_uppercase)
                    if mask & (1 << i)]
        except Exception:  # noqa: BLE001
            pass
    out = []
    for raw in ("/", "/opt", "/usr/local", "/Applications",
                str(pathlib.Path.home())):
        path = pathlib.Path(raw)
        if path.is_dir():
            out.append(path)
    return out


def _env_bases() -> list[pathlib.Path]:
    """常见安装/数据基目录（全部来自环境变量，没有一个盘符）。"""
    out = []
    for key in ("LOCALAPPDATA", "APPDATA", "ProgramFiles", "ProgramW6432",
                "ProgramFiles(x86)", "USERPROFILE", "HOME", "PUBLIC",
                "ALLUSERSPROFILE", "XDG_DATA_HOME"):
        raw = os.getenv(key)
        if raw:
            out.append(pathlib.Path(raw))
    la = os.getenv("LOCALAPPDATA")
    if la:
        out.append(pathlib.Path(la) / "Programs")
    return _dedup(out)


def _dedup(paths) -> list[pathlib.Path]:
    seen = set()
    out = []
    for raw in paths:
        path = raw if isinstance(raw, pathlib.Path) else pathlib.Path(str(raw))
        key = str(path).rstrip("\\/").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def _children(path: pathlib.Path, limit: int = _MAX_PER_LEVEL):
    """列出一层子目录（跳过系统目录，带上限）。"""
    try:
        entries = sorted(path.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return []
    out = []
    for entry in entries:
        if len(out) >= limit:
            break
        name = entry.name.lower()
        if name in _SKIP or name.startswith("$"):
            continue
        try:
            if entry.is_dir():
                out.append(entry)
        except OSError:
            continue
    return out


def _marker_rel(marker) -> pathlib.Path:
    if isinstance(marker, (tuple, list)):
        return pathlib.Path(*[str(x) for x in marker])
    return pathlib.Path(str(marker).replace("\\", "/"))


def matches(path, markers=(), exe_names=(), require=()) -> bool:
    """确认目录是目标（可选）。

    `require` 里的相对路径必须**全部存在**，`markers` / `exe_names` 任一
    存在即可。都不传时只要目录存在就算命中 —— 目录名固定，这就够了。
    `Local State` 这类泛化特征必须放进 `require` 并搭配独有文件，
    否则会把 Edge WebView、钉钉等 Electron 目录也算进来。
    """
    try:
        if not path or not pathlib.Path(path).is_dir():
            return False
    except OSError:
        return False
    path = pathlib.Path(path)
    for rel in require or ():
        try:
            if not (path / _marker_rel(rel)).exists():
                return False
        except OSError:
            return False
    if not markers and not exe_names:
        return True
    for marker in markers or ():
        try:
            if (path / _marker_rel(marker)).exists():
                return True
        except OSError:
            continue
    for name in exe_names or ():
        try:
            if (path / str(name)).is_file():
                return True
        except OSError:
            continue
    return False


def find_named(names, *, roots=(), markers=(), exe_names=(), require=(),
               max_depth: int = _MAX_DEPTH, max_dirs: int = _MAX_DIRS,
               stop_after_first: bool = True, with_sources: bool = False):
    """按**固定目录名**在动态盘符 / 动态父文件夹里定位，命中即停。

    不是"遍历所有目录再比对内容"，而是**逐层直接拼路径**：

        D:\\WorkBuddy                     depth 0（盘根 + 固定名）
        E:\\AI\\WorkBuddy                 depth 1（用户新建的一层父文件夹）
        E:\\我的软件\\AI工具\\WorkBuddy      depth 2/3

    每层只做 `len(names)` 次 `is_dir()`；第一个命中就返回，所以常见布局
    是 <10ms。`roots` 为空时自动取「环境变量基目录 + 实际存在的盘符」。
    目录名大小写不敏感。`stop_after_first=False` 时收集所有名字变体。
    """
    wanted = tuple(dict.fromkeys(
        s for s in (str(n).strip() for n in (names or ())) if s))
    if not wanted:
        return []
    explicit = _dedup([expand(r) for r in (roots or ())])
    bases = explicit or _dedup(list(_env_bases()) + active_drives())
    env_roots = {str(p).rstrip("\\/").lower() for p in _env_bases()}
    results: list[tuple[str, str]] = []
    found_keys: set[str] = set()
    visited = 0

    def _record(path: pathlib.Path, source: str, key: str) -> None:
        if not matches(path, markers, exe_names, require):
            return
        text = str(path)
        if any(text.lower() == p.lower() for p, _ in results):
            return
        results.append((text, source))
        found_keys.add(key)

    frontier = [(base, "常见目录" if str(base).rstrip("\\/").lower() in env_roots
                 else "动态盘符") for base in bases]
    for depth in range(max_depth + 1):
        next_frontier: list[tuple[pathlib.Path, str]] = []
        for base, base_source in frontier:
            if visited >= max_dirs:
                break
            visited += 1
            source = ("动态父文件夹" if depth > 0 and base_source == "动态盘符"
                      else base_source)
            # roots 直接指向产品目录的场景（例如 %PUBLIC%/Loomy）
            key = base.name.lower()
            if key in {n.lower() for n in wanted} and key not in found_keys:
                _record(base, base_source, key)
                if stop_after_first and results:
                    return ([{"path": p, "source": s} for p, s in results]
                            if with_sources else [p for p, _ in results])
            for name in wanted:
                nkey = name.lower()
                if nkey in found_keys:
                    continue
                _record(base / name, source, nkey)
                if stop_after_first and results:
                    return ([{"path": p, "source": s} for p, s in results]
                            if with_sources else [p for p, _ in results])
            if depth >= max_depth:
                continue
            try:
                if not base.is_dir():
                    continue
                for child in _children(base):
                    next_frontier.append((child, base_source))
            except OSError:
                continue
        if not next_frontier:
            break
        frontier = next_frontier

    return ([{"path": p, "source": s} for p, s in results]
            if with_sources else [p for p, _ in results])


#: 定位结果缓存（按参数签名），避免连续刷新重复扫盘
_scan_cache: dict = {}
_SCAN_TTL = 20.0


def _cached_find(names, **kwargs) -> list[dict]:
    key = (tuple(map(str, names)),
           tuple(sorted((k, tuple(map(str, v)) if isinstance(v, (tuple, list))
                         else v) for k, v in kwargs.items())))
    with _lock:
        hit = _scan_cache.get(key)
        if hit and time.time() - hit[0] < _SCAN_TTL:
            return list(hit[1])
    found = find_named(names, with_sources=True, **kwargs)
    with _lock:
        _scan_cache[key] = (time.time(), list(found))
    return found


# --------------------------------------------------------------------------- #
# 对外：解析
# --------------------------------------------------------------------------- #
def resolve(platform: str, *, kind: str = "install_dir", names=(),
            markers=(), exe_names=(), require=(), roots=(), fallback=(),
            max_depth: int = _MAX_DEPTH, stop_after_first: bool = True,
            with_sources: bool = False):
    """解析某平台某类目录，返回路径列表（按可信度排序、去重）。

        platform    平台 id（决定覆盖键，如 ``CODEARTS_USER_DATA_DIR``）
        kind        ``install_dir`` / ``user_data_dir`` / ``config_root`` / ``app_exe``
        names       固定的产品/目录名（**这是定位依据**）
        require     必须同时存在的特征（可选，用于排除同名目录）
        markers     任一命中即可的特征（可选）
        exe_names   主程序文件名（可选；`kind="app_exe"` 时用于拼出 exe 路径）
        roots       额外搜索根（可含 `%VAR%`）
        fallback    内置兜底模板（只含 `%VAR%` / `~`，不含盘符）
    """
    key = kind if kind in KEYS else "install_dir"
    pid = str(platform or "").strip().lower()
    results: list[tuple[str, str]] = []

    def _add(raw, source: str) -> None:
        if not raw:
            return
        text = str(expand(raw))
        if not text or any(text.lower() == p.lower() for p, _ in results):
            return
        results.append((text, source))

    # 1) 显式覆盖：用户填了就用他的，命中即停（最快路径）
    value, source = override(pid, key)
    if value:
        path = expand(value)
        if path.exists():
            if key == "app_exe":
                if path.is_file():
                    _add(path, source)
                else:
                    for name in exe_names or ():
                        exe = path / str(name)
                        if exe.is_file():
                            _add(exe, source)
            else:
                _add(path, source)
            if results:
                return ([{"path": p, "source": s} for p, s in results]
                        if with_sources else [p for p, _ in results])
        else:
            note = "覆盖路径不存在，已忽略：%s=%s" % (env_name(pid, key), value)
            if note not in warnings:
                warnings.append(note)
            _logger.info(note)

    # 2) 固定名 + 动态盘符 / 动态父文件夹
    found = _cached_find(names, roots=roots, markers=markers,
                         exe_names=exe_names, require=require,
                         max_depth=max_depth,
                         stop_after_first=stop_after_first)
    for item in found:
        directory = pathlib.Path(item["path"])
        if key == "app_exe":
            for name in exe_names or ():
                exe = directory / str(name)
                if exe.is_file():
                    _add(exe, item["source"])
        else:
            _add(directory, item["source"])

    # 3) 内置兜底（相对模板，允许 `%VAR%`，**不含盘符**）
    for raw in fallback or ():
        path = expand(raw)
        if key == "app_exe":
            if path.is_file():
                _add(path, "内置候选")
        elif matches(path, markers, exe_names, require):
            _add(path, "内置候选")

    return ([{"path": p, "source": s} for p, s in results]
            if with_sources else [p for p, _ in results])


def describe(platform: str, *, kind: str = "install_dir", **kwargs) -> dict:
    """诊断：当前解析结果 + 每项来源 + 覆盖值 + 警告。"""
    pid = str(platform or "").strip().lower()
    value, source = override(pid, kind)
    return {
        "platform": pid,
        "kind": kind,
        "override": value,
        "override_source": source,
        "env_name": env_name(pid, kind),
        "resolved": resolve(pid, kind=kind, with_sources=True, **kwargs),
        "drives": [str(p) for p in active_drives()],
        "warnings": list(warnings[-5:]),
    }


__all__ = [
    "KEYS", "expand", "env_name", "override", "saved_overrides", "save", "clear",
    "warmup", "invalidate_cache", "active_drives", "matches", "find_named",
    "resolve", "describe", "warnings",
]
