#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""客户端路径覆盖：把「安装目录 / 逆向产物目录」也变成后台可改的配置。

要解决什么问题
--------------
`wb_install` 的路径来源原本只有环境变量（`WORKBUDDY_INSTALL_DIR` 等）。
这在部署上是好的（不写死、可注入），但日常使用有两个别扭之处：

1. **改一个路径要改 `.env` 并重启进程**。用户想把客户端换到另一个安装目录，
   得停服务、编辑、再起。
2. **路径填错的反馈很晚**。填错了要等下次发请求才发现版本号不对。

于是把这三个路径也做成「后台可改、立即生效」：
``install_dir``（安装基目录）/ ``asar_path``（直接指定 app.asar）/
``source_dir``（逆向产物输出目录）。

优先级（**环境变量仍然最高**）
------------------------------
    环境变量 > 后台保存值 > 自动扫描

这个顺序是刻意的：环境变量是运维逃生门，容器 / systemd 注入的值不该被
后台操作悄悄盖掉；反过来，后台改完能立即压住「自动扫描」的结果。

持久化位置：`system_settings` 表（与同步密钥、客户端参数档案同一处），
键名见 `KEYS`。启动时由 `load_into_wb()` 注入 `wb_install.WB`。
"""
from __future__ import annotations

import json
import logging
import threading

_logger = logging.getLogger(__name__)

#: system_settings 里的键名
_K_PATHS = "wb_paths"

#: 档案里允许的键（与 WB.set_overrides 的参数对应）
KEYS = ("install_dir", "asar_path", "source_dir")

#: 键 -> 对应环境变量名（用于提示「当前值其实来自环境变量」）
ENV_FOR = {
    "install_dir": "WORKBUDDY_INSTALL_DIR",
    "asar_path": "WORKBUDDY_ASAR_PATH",
    "source_dir": "WORKBUDDY_SOURCE_DIR",
}

_lock = threading.RLock()


def _load_raw(db=None) -> dict:
    """从 DB 读原始覆盖值（失败返回空 dict，绝不抛）。"""
    from admin.db import SessionLocal
    from admin.models import SystemSetting

    own = db is None
    if own:
        db = SessionLocal()
    try:
        row = db.query(SystemSetting).filter(
            SystemSetting.key == _K_PATHS).first()
        if not row or not row.value:
            return {}
        obj = json.loads(row.value)
        return obj if isinstance(obj, dict) else {}
    except Exception as e:
        _logger.debug("读取 wb_paths 失败：%s", e)
        return {}
    finally:
        if own:
            db.close()


def _save_raw(data: dict, db=None) -> None:
    from admin.db import SessionLocal
    from admin.models import SystemSetting

    own = db is None
    if own:
        db = SessionLocal()
    try:
        row = db.query(SystemSetting).filter(
            SystemSetting.key == _K_PATHS).first()
        val = json.dumps(data, ensure_ascii=False)
        if row:
            row.value = val
        else:
            db.add(SystemSetting(key=_K_PATHS, value=val))
        db.commit()
    finally:
        if own:
            db.close()


def load_into_wb(db=None) -> dict:
    """把库里的覆盖值注入 `wb_install.WB`（启动时与保存后调用）。

    只注入「非空」的键；空串表示「该项不覆盖」，让自动扫描照常工作。
    """
    data = _load_raw(db)
    try:
        from wb_install import WB
        WB.set_overrides(
            install_dir=(data.get("install_dir") or "").strip() or None,
            asar_path=(data.get("asar_path") or "").strip() or None,
            source_dir=(data.get("source_dir") or "").strip() or None,
        )
    except Exception as e:
        _logger.warning("注入路径覆盖失败：%s", e)
    return data


def save(data: dict, db=None) -> dict:
    """保存覆盖值并**立即注入生效**（无需重启）。"""
    clean: dict = {}
    for k in KEYS:
        if k in data:
            v = data.get(k)
            clean[k] = str(v).strip() if v else ""
    with _lock:
        cur = _load_raw(db)
        cur.update(clean)
        # 全空等于「不用覆盖」，直接清掉这个键，保持库里干净
        cur = {k: v for k, v in cur.items() if v}
        _save_raw(cur, db)
    load_into_wb(db)
    return cur


def clear(db=None) -> dict:
    """清空全部覆盖（回到纯环境变量 + 自动扫描）。"""
    with _lock:
        _save_raw({}, db)
    load_into_wb(db)
    return {}


def current(db=None) -> dict:
    """当前覆盖值 + 每项的真实来源（排障用）。"""
    import os
    data = _load_raw(db)
    out = {"saved": {k: (data.get(k) or "") for k in KEYS}, "sources": {}}
    for k in KEYS:
        env = ENV_FOR[k]
        if (os.getenv(env) or "").strip():
            out["sources"][k] = f"环境变量 {env}"
        elif (data.get(k) or "").strip():
            out["sources"][k] = "后台设置"
        else:
            out["sources"][k] = "自动扫描"
    return out

# ---------------------------------------------------------------------------
# 客户端探测（WorkBuddy 自己的路径表，不放总网关）
# ---------------------------------------------------------------------------
#: 可执行文件候选（找得到就用它的版本拼 UA）
APP_EXE_CANDIDATES = (
    r"%LOCALAPPDATA%\WorkBuddy\WorkBuddy.exe",
    r"%LOCALAPPDATA%\Programs\WorkBuddy\WorkBuddy.exe",
    r"C:\Program Files\WorkBuddy\WorkBuddy.exe",
    r"C:\Program Files (x86)\WorkBuddy\WorkBuddy.exe",
)

#: 数据目录候选（扫凭据 / 登录态用）
DATA_DIR_CANDIDATES = (
    r"%LOCALAPPDATA%\CodeBuddyExtension\Data\Public",
    r"%LOCALAPPDATA%\CodeBuddyExtension",
    r"~\.codebuddy",
    r"%APPDATA%\WorkBuddy",
    r"%LOCALAPPDATA%\WorkBuddy",
)

#: UA 前缀用的产品名
UA_PRODUCT = "WorkBuddy"


def client_dirs() -> list[str]:
    """本机上存在的 WorkBuddy 数据目录。

    `__init__.py` 的 `WorkBuddyClientProbe.dirs()` 调这个 ——
    之前这个函数不存在，导致整个客户端探测报错。
    """
    from admin.platform.probe import expand

    out = []
    for pattern in DATA_DIR_CANDIDATES:
        path = expand(pattern)
        if path.is_dir():
            out.append(str(path))
    return out


def probe() -> dict:
    """WorkBuddy 的客户端探测。

    返回 `app_path` / `app_version` / `ua` / `data_dirs`，
    由 `admin.platform.probe.enrich` 统一补版本与 UA。
    """
    from admin.platform.probe import enrich

    result = {
        "platform": "workbuddy",
        "data_dirs": client_dirs(),
        # WorkBuddy 的客户端参数（UA / 版本 / 风控头）由
        # `admin.client_profile` 统一管理，这里只报路径与版本
    }
    return enrich(result,
                  exe_candidates=APP_EXE_CANDIDATES,
                  data_dirs=DATA_DIR_CANDIDATES,
                  product=UA_PRODUCT)
