"""Trae 设备指纹：由账号 uid 稳定派生，跨账号互不相同。

为什么必须是**稳定派生**而不是随机：官方客户端在同一账号上长期复用同一组
设备头；随机机器码会让每个请求看起来来自一台新机器，直接触发风控。
为什么必须**账号级隔离**：多个号共用一个 machineId 会形成可识别的关联面。

实测约束（国际版）：`X-Device-Id` 必须是该账号登录设备的真实 holderId，
用 uid 派生的 19 位数字打 `core-normal.trae.ai` 会稳定回 4015。
因此 `device_headers()` 的 `device_id` 支持三态（见下）。
"""
from __future__ import annotations

import functools
import hashlib
import os
import urllib.parse


def machine_id(uid: str) -> str:
    """64 位十六进制 machineId（与官方客户端同形）。"""
    seed = "trae-machine:%s" % (str(uid or "").strip() or "anonymous")
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def device_id(uid: str) -> str:
    """数值型 X-Device-Id（官方客户端直接复用 userId 的 19 位数字串）。"""
    raw = str(uid or "").strip()
    if raw.isdigit():
        return raw
    digest = int(hashlib.sha256(("trae-device:%s" % (raw or "anonymous"))
                                .encode("utf-8")).hexdigest()[:16], 16)
    return str(10 ** 18 + digest % (9 * 10 ** 18))


@functools.lru_cache(maxsize=1)
def hardware_brand() -> str:
    if os.name != "nt":
        return "unknown"
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"HARDWARE\DESCRIPTION\System\BIOS") as key:
            maker = str(winreg.QueryValueEx(key, "SystemManufacturer")[0] or "").strip()
            model = str(winreg.QueryValueEx(key, "SystemProductName")[0] or "").strip()
        return " ".join(x for x in (maker, model) if x).strip() or "Default string"
    except Exception:
        return "Default string"


@functools.lru_cache(maxsize=1)
def os_version() -> str:
    if os.name != "nt":
        return "Linux"
    product, edition = "Windows", ""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as key:
            product = str(winreg.QueryValueEx(key, "ProductName")[0] or "Windows").strip()
            try:
                edition = str(winreg.QueryValueEx(key, "EditionID")[0] or "").strip()
            except OSError:
                edition = ""
            try:
                build = int(str(winreg.QueryValueEx(key, "CurrentBuild")[0]))
            except (OSError, ValueError):
                build = 0
        if build >= 22000 and product.startswith("Windows 10"):
            product = product.replace("Windows 10", "Windows 11", 1)
        if edition and edition.lower() not in product.lower():
            product = "%s %s" % (product, edition)
    except Exception:
        pass
    return product or "Windows"


def device_headers(uid: str, device_id_value: str | None = None) -> dict:
    """IDE 数据面设备头（已按官方形态 URL 编码）。

    device_id_value 三态：
      None（默认） uid 派生值 —— 国内版一直如此；
      ""（空串）   **不发 X-Device-Id**，国际版无真实设备号时用这个；
      非空串       账号自己的真实设备号。
    """
    resolved = device_id(uid) if device_id_value is None else str(device_id_value).strip()
    headers = {
        "X-Device-Cpu": "Intel",
        "X-Machine-Id": machine_id(uid),
        "X-Device-Brand": urllib.parse.quote(hardware_brand(), safe=""),
        "X-Device-Type": "windows",
        "X-Os-Version": urllib.parse.quote(os_version(), safe=""),
    }
    if resolved:
        headers["X-Device-Id"] = resolved
    return headers


def describe(uid: str) -> dict:
    """指纹汇总（控制台 / 诊断展示，不含 token）。"""
    return {
        "userId": str(uid or ""),
        "machineId": machine_id(uid),
        "deviceId": device_id(uid),
        "deviceBrand": hardware_brand(),
        "osVersion": os_version(),
        "deviceType": "windows",
        "cpu": "Intel",
    }