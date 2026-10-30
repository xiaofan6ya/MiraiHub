"""平台网关管理接口：`/api/gateway/*`。

    GET  /api/gateway/config        调度策略 + 优先级 + 禁用列表
    PUT  /api/gateway/config        保存策略与优先级
    GET  /api/gateway/health        全部平台健康度（含排序结果）
    GET  /api/gateway/order         当前策略下的实际调度顺序（排障用）
    POST /api/gateway/disable       禁用某平台（退出调度）
    POST /api/gateway/enable        恢复某平台

## 与「平台管理」页的区别

  · `/api/platforms`        —— 平台**元信息**（有哪些平台、几个账号）
  · `/api/gateway/*`（本文件）—— 平台**之间怎么调度**（策略/优先级/禁用）

前者回答「我有什么」，后者回答「怎么用」。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from admin.db import get_db
from admin.platform import gateway_router
from admin.platform import registry
from admin.platform.loader import load_platforms
from admin.security import require_admin

logger = logging.getLogger("admin.platform.gateway_api")

router = APIRouter(prefix="/api/gateway", tags=["gateway"])


class RoutingIn(BaseModel):
    #: 策略 id（见 `gateway_router.STRATEGY_IDS`）；留空表示不改
    strategy: str = ""
    #: 平台 -> 优先级（数字小优先）；留空表示不改
    priority: dict[str, int] | None = None
    #: 无头请求是否参与负载分摊；None 表示不改
    #:
    #: 关闭（默认）时，不带 X-Platform 且无绑定 Key 的请求一律走默认平台。
    #: 打开后会被 random/balance 分摊 —— 只适合「多平台聚合」场景，
    #: 模型 ID 不一致时老客户端会 404。
    spread_unbound: bool | None = None
    #: 平台 -> 是否禁用；None 表示不改。
    #:
    #: 前端「平台网关」页的开关直接用这个字段批量改，不必逐个调
    #: `/disable` `/enable`。之前这个字段不存在，前端传 `disabled` 会被
    #: **静默忽略**（pydantic 默认丢弃未知字段），表现是「点了禁用没反应」。
    disabled: dict[str, bool] | None = None


class ToggleIn(BaseModel):
    platform: str
    reason: str = ""


@router.get("/config")
def get_config(_: bool = Depends(require_admin), db: Session = Depends(get_db)):
    load_platforms()
    cfg = gateway_router.routing_config(db)
    # 附上平台列表，前端渲染优先级输入框时不用再打一次接口
    cfg["platforms"] = [
        {"id": spec.id, "label": spec.label,
         "managed": bool(spec.platform_gateway)}
        for spec in registry.all_platforms()
    ]
    return cfg


@router.put("/config")
def put_config(body: RoutingIn, _: bool = Depends(require_admin),
               db: Session = Depends(get_db)):
    """保存调度策略。"""
    try:
        cfg = gateway_router.save_routing(
            db,
            strategy=body.strategy or None,
            priority=body.priority,
            spread_unbound=body.spread_unbound,
            disabled=body.disabled,
        )
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    return {"ok": True, "config": cfg}


@router.get("/health")
def get_health(_: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """全部平台健康度 + 当前调度顺序。"""
    items = gateway_router.health_all(db)
    # 展示型接口：把没有可用账号的平台也列出来（否则多平台网关看起来只剩一个）
    ordered = gateway_router.order(db, include_unavailable=True)
    rank = {item["platform"]: index + 1 for index, item in enumerate(ordered)}
    for item in items:
        item["rank"] = rank.get(item["platform"])
    return {"items": items,
            "strategy": gateway_router.routing_config(db)["strategy"]}


@router.get("/resolve")
def resolve_model(model: str, platform: str = "",
                  _: bool = Depends(require_admin),
                  db: Session = Depends(get_db)):
    """预演「这个模型 ID 会被路由到哪个平台」。

    背景：**13 个模型 ID 在多个平台同名**（`deepseek-v4.1-flash`、`glm-5.3`
    `kimi-k2.6` …）。用户不带 `X-Platform` 调用时，网关按倍率/健康度自己挑，
    落点不直观，很容易「以为调的是 A 平台，其实是 B 平台」。

    这个接口把决策摊开：
        candidates  所有能提供该模型的平台（含倍率、是否被禁用）
        chosen      当前会选中的那个
        ambiguous   >1 个候选时为 true —— 提示用户最好显式指定平台
    """
    from admin.platform import dispatch

    text = str(model or "").strip()
    if not text:
        raise HTTPException(400, detail="model 不能为空")

    with dispatch._db() as raw_db:
        platforms = dispatch._model_platforms(raw_db, text)
    disabled = dispatch._disabled_platforms()

    candidates = []
    for storage, realm_hint in platforms:
        candidates.append({
            "platform": storage,
            "realm": realm_hint or "",
            "disabled": dispatch._is_disabled(storage, disabled),
        })

    chosen = ""
    try:
        with dispatch._db() as raw_db:
            picked = dispatch._candidates_for_model(raw_db, text, None)
        if picked:
            chosen = picked[0][0]
    except Exception:  # noqa: BLE001 - 预演失败不影响主流程
        chosen = ""

    return {
        "model": text,
        "platform_arg": platform or "",
        "candidates": candidates,
        "chosen": chosen or (candidates[0]["platform"] if candidates else ""),
        # 同名多平台是常态，但用户要知道：这里 true 就意味着「落点不确定」
        "ambiguous": len([c for c in candidates if not c["disabled"]]) > 1,
        "note": ("该 ID 在多个平台存在，未指定 X-Platform 时由网关按策略挑选；"
                 "想固定落点请显式传 X-Platform")
                if len(candidates) > 1 else "该 ID 只在一个平台存在",
    }


@router.get("/order")
def get_order(_: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """当前策略下的实际调度顺序。

    带上**没有可用账号**的平台（标 `available=False`）—— 否则界面上
    「当前调度顺序」只剩有账号的那一个，看着像其它平台没接进来。
    """
    return {"items": gateway_router.order(db, include_unavailable=True)}


@router.post("/disable")
def disable(body: ToggleIn, _: bool = Depends(require_admin),
            db: Session = Depends(get_db)):
    """禁用某平台网关：它不再参与调度，但其账号数据保留。"""
    load_platforms()
    if registry.get(body.platform) is None:
        raise HTTPException(404, detail="平台不存在")
    gateway_router.set_enabled(db, body.platform, False, body.reason)
    logger.info("平台网关已禁用：%s（%s）", body.platform, body.reason or "-")
    return {"ok": True, "platform": body.platform, "enabled": False}


@router.post("/enable")
def enable(body: ToggleIn, _: bool = Depends(require_admin),
           db: Session = Depends(get_db)):
    """恢复某平台网关。"""
    load_platforms()
    if registry.get(body.platform) is None:
        raise HTTPException(404, detail="平台不存在")
    gateway_router.set_enabled(db, body.platform, True)
    logger.info("平台网关已恢复：%s", body.platform)
    return {"ok": True, "platform": body.platform, "enabled": True}
