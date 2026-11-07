"""平台元信息接口：`/api/platforms`。

控制台在账号池上方渲染「平台切换模块」时只需要这一个接口：

    GET  /api/platforms              已登记平台 + 区域 + 能力声明
    GET  /api/platforms/current      当前选中的平台（服务端默认值）
    PUT  /api/platforms/current      记住当前选择（按登录会话共享，落 system_settings）

为什么不让前端硬编码平台列表：接入下一个平台时，前端不该改一行 ——
这正是「网关平台架构」要买到的东西。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from admin.db import get_db
from admin.models import Account, SystemSetting
from admin.platform import registry
from admin.platform.loader import load_platforms, status as loader_status
from admin.security import require_admin

logger = logging.getLogger("admin.platform.router")

router = APIRouter(prefix="/api/platforms", tags=["platforms"])

CURRENT_KEY = "platform:current"


def _serialize(spec) -> dict:
    return {
        "id": spec.id,
        "label": spec.label,
        "enabled": spec.enabled,
        "supports_local_scan": spec.supports_local_scan,
        # 凭证获取方式：控制台据此决定「加号」入口。
        # `import-only` 表示既不能扫本机也没有授权接口，只能粘贴导入 ——
        # 前端必须保留 `import`、且**不要**注入 `begin-auth`（否则点了 404）。
        "auth_mode": spec.effective_auth_mode(),
        "default_realm": spec.default_realm(),
        "notes": spec.notes,
        # 图标 key：前端据此在 platform-icons.js 里取 logo。
        "icon": spec.icon or spec.id,
        # 落库平台与固定区域：前端展示「这个平台其实存在哪」时用得到。
        "storage_platform": spec.storage(),
        # 固定区域：单区平台（trae-cn / trae-intl）要靠它显示「国内版/国际版」
        "realm": spec.realm,
        "realm_label": {
            "cn": "国内版", "intl": "国际版",
        }.get(spec.realm, ""),
        "realms": [{"key": r.key, "label": r.label, "default": r.default}
                   for r in spec.realms],
        "proxied": bool(spec.gateway),
        "extra": spec.extra or {},
        # 该平台是否提供管理契约（账号池 / 任务 / 客户端探测 / 概览卡片）。
        # 前端据此决定要不要显示「管理」入口，避免点了 404。
        "managed": bool(spec.platform_gateway),
        # 平台能做的动作（扫描/导入/刷新/签到…）。
        #
        # **由平台声明，不是前端写死**：改造前前端只有一个
        # `if (storage !== "trae") return []`，导致新增平台完全没有
        # 扫描/导入入口 —— 用户没法把账号导进来。
        #
        # 取值：scan / import / refresh-quota / usage / web-cookie /
        #       checkin / begin-auth
        "actions": list((spec.extra or {}).get("actions") or []),
    }


@router.get("/{platform_id}/overview")
def platform_overview(platform_id: str, _: bool = Depends(require_admin),
                      db: Session = Depends(get_db)):
    """某个平台的概览卡片。

    **卡片内容由平台自己声明**（`Gateway.overview_cards`），admin 只做
    转发与兜底。这样新增平台时前端一行不用改，删除平台时卡片自动消失。

    平台没实现、或实现抛异常时返回空列表 + `reason` ——
    前端显示「该平台暂未提供概览」，而不是整页报错。
    """
    load_platforms()
    spec = registry.get(platform_id)
    if spec is None:
        raise HTTPException(404, detail="平台不存在")
    gateway = registry.load_platform_gateway(spec, spec.realm)
    if gateway is None or not callable(getattr(gateway, "overview_cards", None)):
        return {"platform": platform_id, "label": spec.label, "cards": [],
                "reason": "该平台未提供概览卡片"}
    try:
        cards = gateway.overview_cards(db) or []
    except Exception as exc:  # noqa: BLE001 - 单平台失败不影响其它平台
        logger.warning("平台 %s 概览卡片生成失败：%s", platform_id, exc)
        return {"platform": platform_id, "label": spec.label, "cards": [],
                "reason": "概览生成失败：%s" % str(exc)[:120]}
    return {"platform": platform_id, "label": spec.label, "cards": cards}


@router.get("")
def list_platforms(_: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """已登记平台 + 每平台/每区的账号数。

    账号数在这里一并返回，是为了让控制台切换卡片**一次请求**就能显示
    「哪个平台有几个号」—— 否则每加一个平台前端就要多打一次接口，
    平台多了会变成 N 次串行请求。
    """
    load_platforms()
    specs = registry.all_platforms()
    counts: dict[str, int] = {}
    realm_counts: dict[str, int] = {}
    # 分区功能上线前的老账号 `domain` 为空 = 「区域未知」。它们在**每个区**
    # 都可能可用（见 shared.realm_domain_cond），所以按区计数时也计入，
    # 否则平台卡显示 0、账号池页却有号 —— 2026-11-07 的 Qoder 误报就是这样。
    unknown: dict[str, int] = {}
    for platform_id, realm in db.query(Account.platform, Account.domain).all():
        pid = platform_id or registry.DEFAULT_PLATFORM
        counts[pid] = counts.get(pid, 0) + 1
        if realm:
            key = "%s:%s" % (pid, realm)
            realm_counts[key] = realm_counts.get(key, 0) + 1
        else:
            unknown[pid] = unknown.get(pid, 0) + 1
    items = []
    for spec in specs:
        payload = _serialize(spec)
        # 账号数按**落库平台**统计：`trae-cn` / `trae-intl` 落库都是 `trae`，
        # 但各自只算自己那个区。直接用 spec.id 会永远是 0。
        storage = spec.storage()
        if spec.realm:
            count = (realm_counts.get("%s:%s" % (storage, spec.realm), 0)
                     + unknown.get(storage, 0))
            realms = {realm.key: (count if realm.key == spec.realm else 0)
                      for realm in spec.realms}
        else:
            count = counts.get(storage, 0)
            realms = {realm.key: realm_counts.get("%s:%s" % (storage, realm.key), 0)
                      for realm in spec.realms}
        payload["account_count"] = count
        payload["realm_counts"] = realms
        items.append(payload)
    return {
        "items": items,
        "default": registry.DEFAULT_PLATFORM,
        "loader": loader_status(),
    }


@router.get("/current")
def get_current(_: bool = Depends(require_admin), db: Session = Depends(get_db)):
    row = db.query(SystemSetting).filter(SystemSetting.key == CURRENT_KEY).first()
    value = (row.value if row and row.value else registry.DEFAULT_PLATFORM)
    platform, realm = registry.resolve(value)
    return {"platform": platform, "realm": realm, "raw": value}


class CurrentIn(BaseModel):
    platform: str
    realm: str = ""


@router.put("/current")
def set_current(body: CurrentIn, _: bool = Depends(require_admin),
                db: Session = Depends(get_db)):
    spec = registry.get(body.platform)
    if spec is None:
        return {"ok": False, "error": f"未知平台：{body.platform}"}
    realm = spec.normalize_realm(body.realm) if body.realm else spec.default_realm()
    value = f"{spec.id}:{realm}" if realm else spec.id
    row = db.query(SystemSetting).filter(SystemSetting.key == CURRENT_KEY).first()
    if row is None:
        db.add(SystemSetting(key=CURRENT_KEY, value=value))
    else:
        row.value = value
    db.commit()
    return {"ok": True, "platform": spec.id, "realm": realm, "raw": value}

@router.get("/export-all")
def export_all_platforms(mode: str = "backup", platform: str = "",
                         _: bool = Depends(require_admin),
                         db: Session = Depends(get_db)):
    """导出账号（默认**全部平台**；`?platform=` 指定落库平台名单平台导出）。

    形状（`format=miraihub-all-platforms`）：

        {"platforms": {"loomy": {"label": "Loomy", "count": 3,
                                 "accounts": [{...平台自己的备份记录...}]},
                       ...}}

    每个平台的 `accounts` 就是它**自己备份格式**的记录列表（凭证主体取自
    `Account.auth_json` —— 各平台 import 时存的就是这个 JSON，回灌时
    逐条序列化成 JSONL 喂回 `import_batch` / import-async 即可无损还原）。
    `workbuddy` / `codebuddy` 的 `auth_json` 原样保留（含 auth/account 结构）。

    `mode=plain` 输出脱敏清单：尽量移除 token 类字段，只留核对用的信息。
    平台记录形态各异，脱敏按「常见凭证键名」做，宁可漏删不可错删，
    所以 plain 模式响应里带 `sanitized: true` 提示**不要**当凭证回灌。

    `platform`（2026-11-07）：落库平台名（qoder / loomy / trae …），
    只导该平台；「导出全部平台」是整机迁移用，日常按平台备份用这个。
    不认识的值 = 空结果（不静默回全量，防止误把所有凭证落盘）。
    """
    import json as _json
    from datetime import datetime as _dt

    TOKEN_KEYS = ("token", "accesstoken", "access_token", "refreshtoken",
                  "refresh_token", "session", "sessionkey", "session_key",
                  "apikey", "api_key", "secret", "password", "cookie")
    _SENSITIVE = {k.lower() for k in TOKEN_KEYS}

    def _sanitize(record: dict) -> dict:
        out = {}
        for key, value in record.items():
            if key.lower() in _SENSITIVE and isinstance(value, str) and value:
                out[key] = value[:6] + "…"
            else:
                out[key] = value
        return out

    items: dict[str, dict] = {}
    # 一次拉全表再按平台分组：账号量级在几千内，比按平台逐个查询更快也更简单。
    rows = db.query(Account).all()
    grouped: dict[str, list[Account]] = {}
    for row in rows:
        grouped.setdefault(str(row.platform or "").lower(), []).append(row)

    # 单平台导出：不认识的值直接空响应（防误全量）
    wanted = str(platform or "").strip().lower()
    load_platforms()
    for platform_id in registry.ids():
        spec = registry.get(platform_id)
        if spec is None:
            continue
        storage = str(spec.storage()).lower()
        if storage in items:
            continue
        if wanted and storage != wanted:
            continue
        accounts = []
        for row in grouped.get(storage, []):
            try:
                record = _json.loads(row.auth_json or "{}")
            except ValueError:
                record = {"raw": str(row.auth_json or "")}
            if not isinstance(record, dict):
                record = {"raw": str(record)}
            record.setdefault("name", row.name or "")
            if str(mode).lower() == "plain":
                record = _sanitize(record)
            accounts.append(record)
        if not accounts:
            continue
        items[storage] = {"label": spec.label, "count": len(accounts),
                          "accounts": accounts}
    return {
        "format": "miraihub-all-platforms",
        "version": 1,
        "mode": "backup" if str(mode).lower() != "plain" else "plain",
        "sanitized": str(mode).lower() == "plain",
        "platform_scope": wanted or "all",
        "exported_at": _dt.now().strftime("%Y-%m-%d %H:%M:%S"),
        "platforms": items,
        "note": ("已移除 token 类字段，仅用于清单核对，不要当凭证回灌"
                 if str(mode).lower() == "plain"
                 else "含明文凭证，请按机密文件保管；可在「导入全部平台」原样回灌"),
    }
