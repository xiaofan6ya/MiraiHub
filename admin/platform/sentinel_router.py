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

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
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


@router.get("/traffic/stream")
async def sentinel_traffic_stream(window: int = Query(default=3600, ge=60, le=86400),
                                  request: Request = None,
                                  _: bool = Depends(require_admin)):
    """实时流量 **SSE 推送**（替代前端轮询）。

    ## 为什么用 SSE 而不是轮询

    轮询是「每 N 秒问一次」，代价有两块：

      1. **空转**：没有请求的时候也在打接口，一个打开的页面就是一条
         持续的 QPS；多开几个标签页更明显。
      2. **延迟**：事件发生在两次轮询之间时要等到下一拍，最坏 2N。

    SSE 是「有事就推」，连接保持一条长连接，服务端每 500ms 采样一次，
    **只有内容变了才发一帧** —— 空闲时几乎零流量，有请求时首帧延迟
    不超过一个采样周期。

    ## 为什么发 diff 而不是每次全量

    全量快照包含最近 1 小时的明细（最多上百条），每秒重发一遍纯属浪费。
    这里只推「变了的行」+ 「平台计数」，前端按 `rid` 合并。

    ## 断线处理

    客户端断开（关页面 / 网络断）时 `request.is_disconnected()` 为真，
    生成器立即退出，不会留下僵尸任务。nginx 侧建议关掉对该路径的
    缓冲（`proxy_buffering off`），否则会攒包一起发。
    """
    import asyncio
    import json as _json
    from admin import traffic_stats

    async def event_stream():
        last_rows: dict[str, dict] = {}
        last_counts: str = ""
        last_running = -1
        # 首帧带一份全量，前端不用额外再打一次 /traffic
        first = True
        idle = 0
        while True:
            if request is not None and await request.is_disconnected():
                break
            try:
                snap = traffic_stats.snapshot(window)
            except Exception as exc:  # noqa: BLE001 - 采样失败不该断流
                yield "event: error\ndata: %s\n\n" % _json.dumps(
                    {"message": str(exc)[:200]}, ensure_ascii=False)
                await asyncio.sleep(2)
                continue

            rows = snap.get("recent") or []
            current = {str(r.get("rid") or ""): r for r in rows if r.get("rid")}
            counts = _json.dumps(snap.get("platforms") or [], ensure_ascii=False, sort_keys=True)
            running = int(snap.get("running") or 0)

            payload = None
            if first:
                payload = {"type": "snapshot", "platforms": snap.get("platforms") or [],
                           "recent": rows, "running": running,
                           "totals": snap.get("totals") or {},
                           "now": snap.get("now") or 0}
                first = False
                idle = 0
            else:
                # 新增或状态变化的行（running -> completed 会被覆盖）
                changed = [r for rid, r in current.items()
                           if rid not in last_rows or last_rows[rid] != r]
                removed = [rid for rid in last_rows if rid not in current]
                if changed or removed or counts != last_counts or running != last_running:
                    payload = {"type": "delta", "changed": changed,
                               "removed": removed, "running": running,
                               "now": snap.get("now") or 0}
                    if counts != last_counts:
                        payload["platforms"] = snap.get("platforms") or []
                        payload["totals"] = snap.get("totals") or {}
                    idle = 0
                else:
                    idle += 1

            last_rows = current
            last_counts = counts
            last_running = running

            if payload is not None:
                yield "data: %s\n\n" % _json.dumps(payload, ensure_ascii=False)
            elif idle and idle % 30 == 0:
                # 每 15 秒一个注释心跳，防止中间层（nginx / 代理）掐闲置连接
                yield ": keep-alive\n\n"

            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"},
    )


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