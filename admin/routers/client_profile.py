#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""客户端参数档案的 HTTP 接口（探测 / 查看 / 保存 / 重置）。

背景：出站请求要伪装成官方桌面端，涉及 UA、版本号、风控头、桌面指纹等一批
参数。这些值需要「本地探测 → 后台保存 → 线上同步」这条链路，所以给它们
一套 REST 接口（见 `admin/client_profile.py` 的说明）。

接口一览::

    GET  /api/client-profile              当前生效值 + 每项来源 + 已保存值
    POST /api/client-profile/detect       现场探测本机客户端（不保存）
    PUT  /api/client-profile              保存（merge 语义，可只改一个字段）
    POST /api/client-profile/reset        清空保存值，回到纯探测

安全：全部需要管理员鉴权（`require_admin`）。档案本身不含凭据，但
「伪装参数」被随意改动会直接影响线上稳定性，所以不对外开放。
"""
from __future__ import annotations

import logging
import json

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from admin import client_profile as cprofile
from admin.db import get_db
from admin.security import require_admin

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/client-profile", tags=["client-profile"])
PORTABLE_FORMAT = "workbuddy-client-profile"


class SaveIn(BaseModel):
    """保存请求。

    profile 只需给出要改的字段（merge 语义）：比如只改 os_version，
    其余保持不动。merge=False 则整体替换。
    """

    profile: dict = Field(default_factory=dict)
    source: str | None = Field(
        default=None,
        description="auto=优先现场探测 / saved=钉住已保存值（线上实例应选 saved）")
    merge: bool = True


def _spec_for(storage: str):
    """按落库平台名找 PlatformSpec。"""
    from admin.platform import registry

    target = (storage or "").strip().lower()
    if not target:
        return None
    for pid in registry.ids():
        item = registry.get(pid)
        if item is not None and item.storage() == target:
            return item
    return None


def _workbuddy_dirs() -> list[str]:
    """WorkBuddy 的数据目录 —— 由**平台自己**上报。

    路径表在 `admin/platform/workbuddy/core/wb_paths.py`，
    总网关只取结果（不再持有任何平台路径表）。

    要求：「应该由各个平台网关探测上报（这样是动态的）」。
    """
    try:
        from admin.platform.workbuddy.core import wb_paths

        return list(wb_paths.client_dirs() or [])
    except Exception:  # noqa: BLE001 - 探测失败不该 500
        return []


def _platform_probe(storage: str) -> dict | None:
    """用平台自己的 `client_probe()` 探测。

    WorkBuddy 不走这里（它的探测有 UA/风控头/桌面指纹，见
    `admin.client_profile`）；其它平台走统一契约。
    """
    from admin.platform import registry

    target = (storage or "").strip().lower()
    if not target or target == "workbuddy":
        return None
    spec = _spec_for(target)
    if spec is None:
        return None
    gateway = registry.load_platform_gateway(spec, spec.realm)
    if gateway is None or not hasattr(gateway, "client_probe"):
        return None
    probe = gateway.client_probe()
    if probe is None:
        return None

    fields: list[dict] = []
    try:
        for item in (probe.schema() or []):
            if isinstance(item, dict):
                fields.append(item)
    except Exception:  # noqa: BLE001 - 探测失败不该 500
        pass

    payload: dict = {}
    try:
        detected = probe.probe() or {}
    except Exception as exc:  # noqa: BLE001
        detected = {"errors": [str(exc)[:200]]}

    # ---- 版本 / UA 全部由**平台自己上报** ----
    #
    # 用户指出：「你怎么又写死到 platform/probe.py 总网关里面了，
    # 这样之后不好维护的，应该由各个平台网关探测上报（这样是动态的）」
    #
    # 对。路径表已下沉到各平台的 `core/paths.py` / `wb_paths.py`，
    # 总网关只读 `probe()` 的返回值。
    if detected.get("app_version"):
        detected.setdefault("product_version", detected["app_version"])
    if detected.get("app_path"):
        detected.setdefault("exe_path", detected["app_path"])

    # 数据目录同样来自平台上报
    platform_dirs = list(detected.get("data_dirs") or [])
    if platform_dirs:
        existing = list(payload.get("dirs") or [])
        for path in platform_dirs:
            if path not in existing:
                existing.append(path)
        payload["dirs"] = existing

    payload["detected"] = detected
    payload["ua"] = detected.get("ua") or ""
    payload["fields"] = fields
    try:
        payload["dirs"] = list(probe.dirs() or [])
    except Exception:  # noqa: BLE001
        payload["dirs"] = []
    return payload


def _payload(db: Session) -> dict:
    """组装给前端的完整视图。"""
    eff = cprofile.effective(db)
    sav = cprofile.saved()
    detail = cprofile.snapshot_detail()
    det = detail.get("values") or {}
    # ⚠️ `dirs` / `ua` 也要给 —— 前端「扫描位置」卡片读的是 `data.dirs`。
    # 数据来自 WorkBuddy 平台自己的上报（`_workbuddy_dirs()`）。

    return {
        "effective": eff,
        "saved": sav,
        "detected": det,
        # 每个探测键的来源：安装包（product.json 等）或本机系统（OS/硬件/时区）
        "detected_origins": detail.get("origins") or {},
        "detected_package": detail.get("package") or {},
        "detected_system": detail.get("system") or {},
        "sources": cprofile.sources(db),
        "source": cprofile.source(),
        "defaults": cprofile.DEFAULTS,
        # 有探测值 = 这台机器上确实装了客户端（线上通常为空）
        "has_client": bool(det),
        "diff_saved_vs_effective": cprofile.diff(sav, eff),
        "runtime_diagnostics": cprofile.runtime_diagnostics(),
        # 扫描位置（数据目录）+ 动态 UA —— 都从 WorkBuddy 平台自己上报取
        "dirs": _workbuddy_dirs(),
        "ua": eff.get("user_agent") or "",
        "platform": "workbuddy",
        # 探测到的版本（来自安装包，客户端升级后自动跟着变）
        "product_version": det.get("desktop_version") or "",
    }


@router.get("")
def get_profile(platform: str = "", _: bool = Depends(require_admin),
                db: Session = Depends(get_db)):
    return _payload(db)


@router.post("/detect-all")
def detect_all(_: bool = Depends(require_admin)):
    """全平台一键探测。

    返回每个平台的结果（WorkBuddy 走原路径，其它走 `client_probe`）。
    **单个平台失败不影响其它** —— 缺客户端是常态。
    """
    from admin.platform import registry

    results: list[dict] = []
    # WorkBuddy 排最前（默认平台）
    storages: list[str] = [registry.DEFAULT_PLATFORM]
    for pid in registry.ids():
        item = registry.get(pid)
        if item is None:
            continue
        storage = item.storage()
        if storage not in storages:
            storages.append(storage)

    for storage in storages:
        entry: dict = {"platform": storage, "ok": False}
        item = _spec_for(storage)
        entry["label"] = (
            item.label if item is not None
            else ("WorkBuddy" if storage == "workbuddy" else storage))
        try:
            if storage == registry.DEFAULT_PLATFORM:
                # `admin.client_profile` 没有 detect()，探测入口是
                # snapshot_detail()（返回 values/origins/package/system）
                detail = cprofile.snapshot_detail()
                values = detail.get("values") or {}
                entry.update({
                    "ok": True,
                    "kind": "workbuddy",
                    "dirs": list(detail.get("dirs") or []),
                    "values": values,
                    "detected_origins": detail.get("origins") or {},
                    "has_client": bool(values),
                    "errors": [],
                })
            else:
                payload = _platform_probe(storage)
                if payload is None:
                    entry["errors"] = ["该平台未实现客户端探测"]
                else:
                    entry.update({
                        "ok": True,
                        "kind": "probe",
                        "dirs": payload.get("dirs") or [],
                        "detected": payload.get("detected") or {},
                        "fields": payload.get("fields") or [],
                    })
        except Exception as exc:  # noqa: BLE001
            entry["errors"] = [str(exc)[:200]]
        results.append(entry)
    return {"items": results, "total": len(results)}


@router.post("/detect")
def detect(platform: str = "", _: bool = Depends(require_admin)):
    """现场探测本机客户端（只读，不写库）。

    返回安装包 + 本机系统两类只读值，并按 origin 标明每个值的出处；
    线上服务器上探测结果通常为空 —— 这正是需要「本地探测后同步过去」的原因。
    """
    # 非 WorkBuddy 平台走统一的 client_probe 契约
    target = (platform or "").strip().lower()
    if target and target != "workbuddy":
        payload = _platform_probe(target)
        if payload is None:
            return {"ok": False, "platform": target,
                    "errors": ["该平台未实现客户端探测"],
                    "has_client": False}
        detected = payload.get("detected") or {}
        return {
            "ok": True,
            "platform": target,
            "kind": "probe",
            "detected": detected,
            "ua": payload.get("ua") or "",
            "fields": payload.get("fields") or [],
            "dirs": payload.get("dirs") or [],
            "has_client": bool(detected.get("items")
                               or detected.get("config_root")
                               or detected.get("data_dirs")),
            "errors": detected.get("errors") or [],
            "hint": detected.get("hint") or "",
        }

    detail = cprofile.snapshot_detail()
    det = detail.get("values") or {}
    # UA 不在探测值里 —— 它是 `desktop_version` + `cli_version` 由
    # `client_profile.ua()` 拼出来的三段式字符串，原来只在
    # `GET /api/client-profile` 的 effective 里输出。
    # 统一布局后前端从 detected.ua 读，所以要在这里补上。
    try:
        ua_value = cprofile.ua()
    except Exception:  # noqa: BLE001 - 拼不出来就留空
        ua_value = ""
    # WorkBuddy 的数据目录由它自己的 `wb_paths.client_dirs()` 上报。
    wb_dirs = _workbuddy_dirs()
    return {
        "ok": True,
        "platform": "workbuddy",
        "detected": det,
        "ua": ua_value,
        "dirs": wb_dirs,
        "detected_origins": detail.get("origins") or {},
        "detected_package": detail.get("package") or {},
        "detected_system": detail.get("system") or {},
        "has_client": bool(det),
        "runtime_diagnostics": cprofile.runtime_diagnostics(),
        "message": ("探测到本机客户端" if det else
                    "未探测到本机 WorkBuddy 桌面端（线上实例属正常；"
                    "请在装了客户端的机器上探测后同步过来）"),
    }


@router.put("")
def save(body: SaveIn, _: bool = Depends(require_admin),
         db: Session = Depends(get_db)):
    # profile 可以为空 —— 只切换取值策略（source）也是合法操作：
    # 「钉住已保存值」与「优先现场探测」的切换不涉及任何字段改动。
    if not isinstance(body.profile, dict):
        raise HTTPException(status_code=400, detail="profile 必须是对象")
    if not body.profile and body.source is None:
        raise HTTPException(status_code=400,
                            detail="profile 与 source 不能同时为空")
    if body.source is not None and body.source not in cprofile._VALID_SOURCES:
        raise HTTPException(
            status_code=400,
            detail=f"source 只能是 {list(cprofile._VALID_SOURCES)}")
    res = cprofile.save(body.profile, new_source=body.source, db=db,
                        merge=body.merge)
    if not res.get("ok"):
        raise HTTPException(status_code=400,
                            detail="；".join(res.get("errors") or ["保存失败"]))
    _logger.info("客户端参数档案已更新（source=%s，改动=%s）",
                 res.get("source"), sorted((body.profile or {}).keys()))
    return {"ok": True, "errors": res.get("errors") or [],
            "source": res.get("source"), **_payload(db)}


@router.post("/reset")
def reset(_: bool = Depends(require_admin), db: Session = Depends(get_db)):
    cprofile.reset(db)
    return {"ok": True, **_payload(db)}


@router.get("/export")
def export_profile(response: Response, _: bool = Depends(require_admin), db: Session = Depends(get_db)):
    profile = dict(cprofile.effective(db))
    profile.pop("user_agent", None)  # Derived again from the imported versions.
    clean, errors = cprofile.normalize(profile)
    if errors:
        raise HTTPException(400, detail="；".join(errors))
    response.headers['Cache-Control'] = 'no-store'
    return {"format": PORTABLE_FORMAT, "version": 1, "source": "saved", "profile": clean}


@router.post("/import")
def import_profile(body: dict, _: bool = Depends(require_admin), db: Session = Depends(get_db)):
    if body.get("format") != PORTABLE_FORMAT or type(body.get("version")) is not int or body.get("version") != 1:
        raise HTTPException(400, detail="请选择客户端参数导出的 JSON 文件（格式版本 1）")
    if len(json.dumps(body, ensure_ascii=False).encode('utf-8')) > 256 * 1024:
        raise HTTPException(400, detail="客户端参数文件不能超过 256 KB")
    clean, errors = cprofile.normalize(body.get("profile"))
    if errors or not clean:
        raise HTTPException(400, detail="；".join(errors or ["档案不能为空"]))
    for name, value in clean.get('headers', {}).items():
        if any(c in name + value for c in '\r\n'):
            raise HTTPException(400, detail="请求头不能包含换行")
    # Validate the whole document before replacing anything; importing also
    # activates saved mode so an absent/stale installation cannot override it.
    result = cprofile.save(clean, new_source=cprofile.SOURCE_SAVED, db=db, merge=False)
    if not result.get("ok"):
        raise HTTPException(400, detail="；".join(result.get("errors") or ["导入失败"]))
    return {"ok": True, **_payload(db)}
