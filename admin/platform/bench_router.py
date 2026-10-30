"""平台跑分：按平台聚合响应速度与稳定性，供「平台管理」底部卡片使用。

为什么单独一个接口而不是复用 `/api/stats/model-health`：
后者按**模型**聚合（哪个模型可用），而这里要回答的是
「同一个模型，哪个平台更快更稳」—— 维度是平台。

数据来源 `usage_logs`（真实经过网关的请求），指标：

    requests     窗口内请求数（含失败）
    success_rate 成功率
    ttfb_p50     首响应中位数（用户感知的"开始出字"时间）
    latency_p50/p95  总耗时中位数 / 95 分位
    jitter       延迟标准差（越小越稳）
    score        综合分（越低越好）：延迟为主、抖动与失败率为罚
  
`score` 是给排序用的，不对外承诺绝对数值 —— 前端展示相对排名即可。
"""
from __future__ import annotations

import statistics
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from admin.db import get_db
from admin.models import UsageLog
from admin.security import require_admin

router = APIRouter(prefix="/api/platform", tags=["platform-bench"])

#: 视为成功的 error_kind
_OK_KINDS = ("", "success")


def _percentile(values: list[int], q: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
    return int(ordered[idx])


@router.get("/speed")
def platform_speed(hours: int = Query(default=24, ge=1, le=720),
                   model: Optional[str] = None,
                   _: bool = Depends(require_admin),
                   db: Session = Depends(get_db)):
    """按平台聚合速度与稳定性（默认近 24 小时）。

    返回按 `score` 升序（最快最稳在前），并附带每个平台的样本量，
    样本太少的平台会被标注 —— 避免「1 个请求跑得快」就排第一。
    """
    since = datetime.utcnow() - timedelta(hours=hours)
    q = (db.query(
            UsageLog.platform,
            UsageLog.error_kind,
            UsageLog.ttfb_ms,
            UsageLog.latency_ms,
            UsageLog.completion_tokens,
        )
        .filter(UsageLog.created_at >= since))
    if model:
        q = q.filter(UsageLog.model == model)

    buckets: dict[str, dict] = {}
    for platform, kind, ttfb, latency, completion in q.all():
        key = (platform or "").strip().lower() or "unknown"
        bucket = buckets.setdefault(key, {
            "platform": key,
            "requests": 0,
            "ok": 0,
            "ttfb": [],
            "latency": [],
            "tps": [],
        })
        bucket["requests"] += 1
        if (kind or "") in _OK_KINDS:
            bucket["ok"] += 1
            if ttfb:
                bucket["ttfb"].append(int(ttfb))
            if latency:
                bucket["latency"].append(int(latency))
            # 输出速度：completion_tokens / **纯解码耗时**。
            #
            # 算法对齐 DeepSeek Harness（`app.asar` turn-metrics）：
            #     tps = decodeTokens / (decodeMs / 1000)
            #     decodeMs = completedTime - firstTokenTime   ← 不含首包等待
            #
            # ⚠️ **绝不能退回整段 latency**。
            # 非流式上游（Loomy / Trae 的 llm_raw_chat）把 ttfb 和 latency
            # 记成同一个值，此时 `latency - ttfb = 0`。曾经的兜底是「退回
            # 整段 latency」，结果 TPS 被严重低估：
            #     上游真实 214 tok/s  ->  面板显示 44 tok/s
            # 因为整段耗时里包含了首包等待（1.5s 里 1.4s 是在等）。
            #
            # 现在的口径：
            #   · 有分段数据（gen_ms > 0）  -> 用 gen_ms，最准
            #   · 无分段数据（非流式）      -> **不参与 TPS 统计**，
            #     宁可少一个样本，也不要给一个错一个数量级的数字
            gen_ms = int(latency or 0) - int(ttfb or 0)
            # **下限保护**：非流式上游（Loomy）的 ttfb 和 latency 只差几毫秒，
            # 直接相除会算出 12000 tok/s 这种荒谬值。
            # 物理上任何模型都不可能快过 ~1000 tok/s，低于 100ms 的「生成段」
            # 一律视为没有分段数据（那几毫秒是网络/解析抖动，不是解码时间）。
            MIN_DECODE_MS = 100
            MAX_TPS = 1000.0
            if completion and gen_ms >= MIN_DECODE_MS:
                tps = float(completion) / (gen_ms / 1000.0)
                if 0 < tps <= MAX_TPS:
                    bucket["tps"].append(tps)
                else:
                    bucket["tps_missing"] = bucket.get("tps_missing", 0) + 1
            elif completion:
                bucket["tps_missing"] = bucket.get("tps_missing", 0) + 1

    rows = []
    for bucket in buckets.values():
        requests = bucket["requests"]
        ok = bucket["ok"]
        latency = bucket["latency"]
        ttfb = bucket["ttfb"]
        success_rate = (ok / requests * 100.0) if requests else 0.0
        p50 = _percentile(latency, 0.5) if latency else 0
        p95 = _percentile(latency, 0.95) if latency else 0
        jitter = int(statistics.pstdev(latency)) if len(latency) > 1 else 0
        # 综合分：延迟(秒) * 1 + 抖动(秒) * 0.6 + 失败率(%) * 0.05
        # 只是排序用的相对值；样本 < 3 时加惩罚，避免偶然值霸榜。
        score = (p50 / 1000.0) + (jitter / 1000.0) * 0.6 + (100 - success_rate) * 0.05
        if ok < 3:
            score += 5
        rows.append({
            "platform": bucket["platform"],
            "requests": requests,
            "ok_requests": ok,
            "success_rate": round(success_rate, 1),
            "ttfb_p50": _percentile(ttfb, 0.5) if ttfb else 0,
            # 平均值也要给：用户想「看出哪个平台快」，均值比中位数更直观，
            # 中位数只用来避免个别超时把结论带偏。
            "ttfb_avg": int(statistics.mean(ttfb)) if ttfb else 0,
            "latency_avg": int(statistics.mean(latency)) if latency else 0,
            # TTFT / TPS 是用户最关心的两个观感指标：
            #   ttfb_p50  多久开始出字
            #   tps_p50   出字有多快（tok/s）
            "tps_p50": round(statistics.median(bucket["tps"]), 1)
                       if bucket["tps"] else 0,
            "tps_avg": round(statistics.mean(bucket["tps"]), 1)
                       if bucket["tps"] else 0,
            "latency_p50": p50,
            "latency_p95": p95,
            "jitter": jitter,
            "samples": len(latency),
            "low_sample": ok < 3,
            "score": round(score, 2),
        })

    # 丢掉「没有成功样本」的行：`unknown` 是没落到任何平台的失败请求
    # （准入拒绝 / 无号 503），把它排进榜单既没意义还会挤掉真正的第一名。
    rows = [r for r in rows if r["ok_requests"] > 0 and r["platform"] != "unknown"]
    rows.sort(key=lambda r: r["score"])
    for index, row in enumerate(rows, start=1):
        row["rank"] = index

    labels = {}
    try:
        from admin.routers.stats import platform_labels

        labels = platform_labels(db)
    except Exception:  # noqa: BLE001 - 标签失败不影响数值
        labels = {}
    for row in rows:
        row["label"] = labels.get(row["platform"]) or row["platform"]

    fastest = rows[0] if rows and not rows[0]["low_sample"] else None
    steadiest = min(
        (r for r in rows if r["samples"] >= 3),
        key=lambda r: r["jitter"], default=None)
    return {
        "hours": hours,
        "model": model or "",
        "items": rows,
        "fastest": fastest["platform"] if fastest else "",
        "steadiest": steadiest["platform"] if steadiest else "",
        "observed_at": datetime.utcnow().isoformat() + "Z",
    }
