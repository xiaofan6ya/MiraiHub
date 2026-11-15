#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`/v1/model-market`（API Key 视角的模型广场）回归测试（不联网、不连库）。

锁定「只返回这个 Key 能用的模型」这条硬约束，以及字段契约。

运行：
  python tests/test_key_model_market.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(ROOT))

from admin.routers import model_market_key as mm  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [ok] {name}")
    else:
        FAIL += 1
        print(f"  [!!] {name}  {detail}")


def test_health() -> None:
    print("\n== 健康度分档 ==")
    check("无可用账号 = down", mm._health_label(None, 0, 0) == "down")
    check("无数据 = no_data", mm._health_label(None, 0, 2) == "no_data")
    check("样本太少不下结论", mm._health_label(1.0, 3, 2) == "degraded")
    check("高成功率 = ok", mm._health_label(1.0, 10, 2) == "ok")
    check("70% 以上 = degraded", mm._health_label(0.8, 10, 2) == "degraded")
    check("低于 70% = down", mm._health_label(0.5, 10, 2) == "down")


def test_quota() -> None:
    print("\n== Key 额度口径 ==")

    class K:
        unlimited = 0
        credit_limit = 1000.0
        credit_used = 42.5

    q = mm._key_quota(K())
    check("有限额度：limit", q["limit"] == 1000.0, q)
    check("有限额度：used", q["used"] == 42.5, q)
    check("有限额度：remaining", q["remaining"] == 957.5, q)
    check("有限额度：unlimited=False", q["unlimited"] is False, q)

    class U:
        unlimited = 1
        credit_limit = 0
        credit_used = 99.0

    q2 = mm._key_quota(U())
    check("无限额度：remaining=None", q2["remaining"] is None, q2)
    check("无限额度：仍报 used", q2["used"] == 99.0, q2)


def test_auth_shape() -> None:
    print("\n== 鉴权取值 ==")
    check("X-API-Key 优先",
          mm._read_key("sk-x", "Bearer sk-y") == "sk-x")
    check("Bearer 兜底",
          mm._read_key(None, "Bearer sk-y") == "sk-y")
    check("Bearer 大小写不敏感",
          mm._read_key(None, "bearer sk-z") == "sk-z")
    check("都没有 -> 空", mm._read_key(None, None) == "")
    check("非 Bearer 的 Authorization 不认",
          mm._read_key(None, "Basic abc") == "")


def test_endpoint_contract() -> None:
    print("\n== 端点契约（静态检查源码）==")
    src = (ROOT / "admin" / "routers" / "model_market_key.py").read_text(encoding="utf-8")
    check("挂了 /v1/model-market", '"/v1/model-market"' in src)
    check("挂了别名 /v1/models/market", '"/v1/models/market"' in src)
    check("用 API Key 鉴权而不是 admin",
          "get_key_row" in src and "require_admin" not in src)
    check("复用代理的 Key 白名单逻辑（不另算一套）",
          "_key_allowed_models" in src)
    check("按 Key 收窄账号池", "scope_accounts" in src)
    check("用量只查这个 Key", "UsageLog.api_key_id == int(key_id)" in src)
    check("hours 上限 168", "MAX_HOURS = 168" in src)
    # 字段契约
    for field in ("requests", "success_rate", "tokens", "credits",
                  "avg_latency_ms", "avg_ttfb_ms", "active_accounts",
                  "exhausted_accounts", "available", "status", "health",
                  "key_usage", "key_used_credits", "key_quota_remaining"):
        if field == "key_usage":
            continue
        check(f"返回含字段 {field}", f'"{field}"' in src)

    print("\n== 负数倍率（Trae 的 -1）不当免费 ==")
    check("负倍率输出 null 而不是 -1",
          "float(raw_multiplier) if known else None" in src)


if __name__ == "__main__":
    test_health()
    test_quota()
    test_auth_shape()
    test_endpoint_contract()
    print()
    print(f"通过 {PASS} / {PASS + FAIL}")
    sys.exit(1 if FAIL else 0)