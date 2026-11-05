"""主机身份信息（服务 / 容器 / 无控制终端环境下也绝不能抛异常）。

## 背景：线上 errno 6

`os.getlogin()` 依赖**进程的控制终端**（utmp/utmpx）。以 systemd / supervisord /
Docker / nohup 方式运行的后台服务没有控制终端，Linux 上会抛

    OSError: [Errno 6] No such device or address

Qoder 设备授权在 `DeviceLogin.__init__` 里调 `machine_id_of_local()` 生成
machine_id，于是「点发起授权」直接 502 报「生成授权链接失败：[Errno 6] ...」。
而 Windows 桌面端恰好有 `USERNAME` 环境变量短路了 `os.getlogin()`，
所以本地开发复现不出来 —— 典型的「只有线上炸」。

## 约定

* 用户名：先看环境变量（`USERNAME` / `LOGNAME` / `USER` / `LNAME`），
  再用 `getpass.getuser()`（它查 pwd 模块，不需要终端），最后回落 `unknown`。
* machine_id：允许运维显式指定 `QODER_MACHINE_ID`；否则优先读持久化文件
  （跨重启稳定，设备流 refresh_token 与 machine_id 绑定），写不了文件时
  退化为「用户名 + 主机名」的 UUID5。任何一步失败都只降级，不中断授权。
"""
from __future__ import annotations

import getpass
import logging
import os
import platform
import uuid
from pathlib import Path

logger = logging.getLogger("admin.platform.host_identity")


def current_username() -> str:
    """当前运行用户；无控制终端 / 容器里也不会抛异常。"""
    for key in ("USERNAME", "LOGNAME", "USER", "LNAME"):
        value = (os.environ.get(key) or "").strip()
        if value:
            return value
    try:
        return (getpass.getuser() or "").strip() or "unknown"
    except Exception:  # noqa: BLE001 - 任何平台怪相都按 unknown 处理
        return "unknown"


def _data_dir() -> Path:
    """项目内的持久化目录（admin/data）。"""
    return Path(__file__).resolve().parents[1] / "data"


def _candidate_files(filename: str) -> list[Path]:
    """候选持久化路径（按优先级）。

    运维显式指定 `QODER_MACHINE_ID_FILE` 时以它为准，**不再**去读项目目录里
    的历史文件 —— 否则显式路径形同虚设，排障时根本不知道 id 从哪来的。
    """
    override = (os.environ.get("QODER_MACHINE_ID_FILE") or "").strip()
    home: list[Path] = []
    try:
        home = [Path.home() / ".miraihub" / filename]
    except Exception:  # noqa: BLE001
        pass
    if override:
        return [Path(override), *home]
    return [_data_dir() / filename, *home]


def stable_machine_id(namespace: str, filename: str,
                       *, env_key: str = "") -> str:
    """返回一个**跨重启稳定**的 machine id，绝不抛异常。

    优先级：环境变量显式指定 > 持久化文件 > 由 user/host 派生的 UUID5。
    生产镜像若把代码目录挂成只读，会自动退到 `~/.miraihub/`；再不行就
    用派生值（重启不变，但换机器会变 —— 至少授权本身能正常发起）。
    """
    if env_key:
        override = (os.environ.get(env_key) or "").strip()
        if override:
            return override[:128]

    files = _candidate_files(filename)
    for path in files:
        try:
            raw = path.read_text(encoding="utf-8").strip()
            if raw and len(raw) <= 128:
                return raw
        except (OSError, UnicodeError):
            continue

    user = current_username()
    try:
        host = (platform.node() or "").strip()
    except Exception:  # noqa: BLE001
        host = ""
    derived = str(uuid.uuid5(uuid.NAMESPACE_DNS,
                             "%s|%s|%s" % (namespace, user, host)))

    for path in files:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(derived, encoding="utf-8")
            tmp.replace(path)
            return derived
        except (OSError, UnicodeError):
            continue
    logger.warning("machine_id 无法持久化，使用派生值（%s）", namespace)
    return derived
