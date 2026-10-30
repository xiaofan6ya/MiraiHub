"""风控哨兵接口 + 全局暂停开关。

    GET  /api/sentinel/status     当前信号汇总（含熔断判定与明细）
    GET  /api/sentinel/events     最近明细（可按平台过滤）
    POST /api/sentinel/pause      手动暂停某平台（止损）
    POST /api/sentinel/resume     恢复
    POST /api/sentinel/reset      清空信号（排障用）

## 关于「暂停」

暂停状态存 `system_settings`（`sentinel:pause`），因为这是**运维决定**，
必须跨重启保留；而信号明细存 Redis（高频、短命）。两者职责不同，
不要混在一个存储里。

暂停生效点在网关鉴权之后、选号之前：直接回 503 + `Retry-After`，
**不消耗账号额度、不打上游**。这正是「及时止损」要的效果 ——
宁可让客户端等一下，也不要继续把号送上去被封。
"""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from admin import sentinel
from admin.db import get_db
from admin.models import SystemSetting
from admin.platform import registry
from admin.security import require_admin

router = APIRouter(prefix="/api/sentinel", tags=["sentinel"])

PAUSE_KEY = "sentinel:pause"


def _load_pause(db: Session) -> dict:
    row = db.query(SystemSetting).filter(SystemSetting.key == PAUSE_KEY).first()
    if not row or not row.value:
        return {}
    try:
        data = json.loads(row.value)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def paused(platform: str = "") -> dict | None:
    """该平台是否处于暂停中。返回暂停信息或 None。

    热路径可调用：只查 system_settings 一行，且被调用方缓存。
    """
    from admin.db import SessionLocal
    try:
        with SessionLocal() as db:
            data = _load_pause(db)
    except Exception:  # noqa: BLE001 - 查不到就当没暂停，不能因此拒服务
        return None
    if not data:
        return None
    item = data.get(platform or "all") or data.get("all")
    if not item:
        return None
    until = float(item.get("until") or 0)
    if until and until <= time.time():
        return None
    return item


@router.get("/status")
def sentinel_status(platform: str = Query(default=""),
                    window: int = Query(default=300, ge=30, le=3600),
                    _: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """信号汇总。控制台「风控哨兵」面板轮询这个接口。"""
    platform = str(platform or "")
    data = sentinel.status(platform, window)
    data["pause"] = _load_pause(db)
    data["now"] = time.time()
    try:
        from admin import traffic_stats

        traffic = traffic_stats.snapshot(window=3600)
        if platform:
            traffic["platforms"] = [row for row in traffic["platforms"]
                                    if row.get("platform") == platform]
            traffic["recent"] = [row for row in traffic["recent"]
                                 if row.get("platform") == platform]
        data["traffic"] = traffic
    except Exception:  # noqa: BLE001 - 统计坏了不能影响哨兵面板
        logger.debug("实时流量读取失败", exc_info=True)
    # 熔断触发时推一条预警。
    #
    # 为什么放在这里而不是热路径：哨兵在转发的关键路径上，绝不能发 HTTP。
    # 这里只被控制台轮询调用（频率低），且 `alerting.notify` 自带去重
    # （同平台同业务码 10 分钟内只发一次），不会因为轮询而刷屏。
    if data.get("tripped"):
        try:
            from admin import alerting
            from admin.platform import scope as platform_scope

            platform_id, realm = platform_scope.parse(platform)
            data["alert"] = alerting.notify({
                "platform": platform,
                "platform_label": platform_scope.label(platform_id, realm),
                "tripped": True,
                "level": "danger",
                "accounts_affected": data.get("accounts_affected"),
                "codes": data.get("by_code") or {},
                "suggested_pause_seconds": data.get("suggested_pause_seconds"),
                "summary": "最近 %d 分钟内有 %s 个账号被上游封控，疑似批次连坐。"
                           % (max(1, window // 60), data.get("accounts_affected") or 0),
            })
        except Exception:  # noqa: BLE001 - 通知失败绝不能影响面板
            logger.debug("哨兵预警发送失败", exc_info=True)
    return data


@router.get("/events")
def sentinel_events(platform: str = Query(default=""),
                    window: int = Query(default=600, ge=30, le=3600),
                    limit: int = Query(default=100, ge=1, le=500),
                    _: bool = Depends(require_admin)):
    return {"items": sentinel.events(platform, window)[:limit]}


@router.get("/traffic")
def sentinel_traffic(window: int = Query(default=3600, ge=60, le=86400),
                     _: bool = Depends(require_admin)):
    """实时流量：每平台**今日累计** + 最近请求明细（含超时 / 503 / 报错）。

    数据来自 `admin.traffic_stats`（Redis 按天分键，失败降级内存）：
    这里不查 `usage_logs` —— 账本只记真正打到上游的调用，
    准入拒绝与超时根本不会写账本行。
    """
    from admin import traffic_stats

    return traffic_stats.snapshot(window)


@router.post("/traffic/reset")
def reset_traffic(_: bool = Depends(require_admin)):
    from admin import traffic_stats

    traffic_stats.reset()
    return {"ok": True, "reset": "traffic"}


class PauseIn(BaseModel):
    #: 平台 id；留空 = 暂停所有平台
    platform: str = ""
    #: 暂停时长（秒）；0 = 直到手动恢复
    seconds: int = 600
    reason: str = ""


@router.post("/pause")
def pause(body: PauseIn, _: bool = Depends(require_admin),
          db: Session = Depends(get_db)):
    """手动暂停：止损开关。"""
    target = body.platform or "all"
    if target != "all" and registry.get(target) is None:
        raise HTTPException(400, f"未知平台：{target}")
    data = _load_pause(db)
    data[target] = {
        "until": (time.time() + body.seconds) if body.seconds > 0 else 0,
        "reason": (body.reason or "手动暂停")[:200],
        "at": time.time(),
    }
    row = db.query(SystemSetting).filter(SystemSetting.key == PAUSE_KEY).first()
    payload = json.dumps(data, ensure_ascii=False)
    if row is None:
        db.add(SystemSetting(key=PAUSE_KEY, value=payload))
    else:
        row.value = payload
    db.commit()
    return {"ok": True, "platform": target, "pause": data[target]}


@router.post("/resume")
def resume(platform: str = "", _: bool = Depends(require_admin),
           db: Session = Depends(get_db)):
    data = _load_pause(db)
    target = platform or "all"
    data.pop(target, None)
    if target != "all":
        data.pop("all", None)
    row = db.query(SystemSetting).filter(SystemSetting.key == PAUSE_KEY).first()
    payload = json.dumps(data, ensure_ascii=False)
    if row is None:
        db.add(SystemSetting(key=PAUSE_KEY, value=payload))
    else:
        row.value = payload
    db.commit()
    return {"ok": True, "resumed": target}


@router.post("/reset")
def reset_signals(platform: str = "", _: bool = Depends(require_admin)):
    """清空信号明细（排障用；不影响账号状态）。"""
    import redis

    from admin.config import settings as app_settings
    try:
        client = redis.Redis.from_url(app_settings.REDIS_URL, decode_responses=True,
                                      protocol=2, socket_connect_timeout=1.0)
        keys = (list(client.scan_iter(match="wb:sentinel:%s*:events"
                                      % (platform or ""), count=200)))
        if keys:
            client.delete(*keys)
    except Exception:  # noqa: BLE001 - Redis 不可用时清内存
        pass
    sentinel.reset()
    return {"ok": True, "platform": platform or "all"}