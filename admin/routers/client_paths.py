#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「客户端路径」手动指定接口（自动发现找不着时的兜底）。

自动发现按**固定目录名 + 动态盘符/父文件夹**定位（`admin.platform.install_paths`），
绝大多数机器直接就能找到。但绿色版解压到极深目录、把 userData 迁到网络盘、
或系统环境变量被改过时，仍需要用户手工指定一次。

接口::

    GET    /api/client-paths                 平台列表 + 已保存覆盖 + 动态盘符
    GET    /api/client-paths/{platform}     某平台的覆盖与可用键
    PUT    /api/client-paths/{platform}     保存（merge，空串=清除该项）
    DELETE /api/client-paths/{platform}     清空，回到自动发现
    POST   /api/client-paths/refresh        丢弃定位缓存，立即重新扫描

全部需要管理员鉴权。环境变量优先级高于这里的保存值（运维逃生门），
详见 `install_paths.override()`。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from admin.platform import install_paths as ip
from admin.security import require_admin

router = APIRouter(prefix="/api/client-paths", tags=["client-paths"])


class SaveIn(BaseModel):
    """`values` 只给要改的键；空串表示清除该项。"""

    values: dict = Field(default_factory=dict)


def _platform_ids() -> list[str]:
    try:
        from admin.platform import registry

        return list(registry.ids())
    except Exception:  # noqa: BLE001 - 注册失败也要能读写覆盖
        return []


@router.get("")
def list_all(_admin=Depends(require_admin)):
    return {
        "platforms": _platform_ids(),
        "saved": ip.saved_overrides(),
        "drives": [str(p) for p in ip.active_drives()],
        "keys": list(ip.KEYS),
        "env_template": "<平台ID大写>_<INSTALL_DIR|USER_DATA_DIR|CONFIG_ROOT|APP_EXE>",
        "warnings": list(ip.warnings[-5:]),
    }


@router.get("/{platform}")
def detail(platform: str, _admin=Depends(require_admin)):
    pid = platform.strip().lower()
    saved = ip.saved_overrides().get(pid) or {}
    return {
        "platform": pid,
        "saved": saved,
        "env_names": {k: ip.env_name(pid, k) for k in ip.KEYS},
        "sources": {k: ip.override(pid, k)[1] for k in ip.KEYS},
    }


@router.put("/{platform}")
def update(platform: str, body: SaveIn, _admin=Depends(require_admin)):
    saved = ip.save(platform, body.values)
    return {"ok": True, "platform": platform.strip().lower(), "saved": saved}


@router.delete("/{platform}")
def clear(platform: str, _admin=Depends(require_admin)):
    ip.clear(platform)
    return {"ok": True, "platform": platform.strip().lower(), "saved": {}}


@router.post("/refresh")
def refresh(_admin=Depends(require_admin)):
    ip.invalidate_cache()
    return {"ok": True}
