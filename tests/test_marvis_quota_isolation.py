#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Marvis 额度耗尽账号的隔离（回归）。

## 锁的是什么

`shared.note_failure(zero_balance=True)` 是「把打光额度的号从候选里摘掉」的
唯一入口 —— `select_account()` 的候选条件是
`balance_remain > 0 OR balance_remain IS NULL`，不落这个标记的号会一直停在
旧的正数余额上；加上 `last_used_at` 只在 `note_success()` 里更新，这些号因为
从没成功过而恒排在队首，于是**每条请求都要先白撞它们一次**。

loomy 网关传了 `zero_balance`（`loomy/gateway.py`：「402 先复核余额再决定
是否判死」），marvis 网关漏传。线上实测（2026-11-24）：四个额度已耗尽的号
（2/4/5/10）`balance_remain` 仍是 228781 / 765429 / 229860 / 145608，
`err_count` 涨到 568 / 454 / 145 / 501，单日 `quota_exceeded` 3582 次。

## 判定口径

  · `quota_exceeded`（3000001 / 1000007）→ `zero_balance=True`
    （`note_failure` 内部再按 `balance_total` 分流：有配额痕迹的号走
    `degrade_until` 降级窗口，没有配额痕迹的才把余额写成 0）
  · 其它失败一路不得带上这个标记（对照组）：4100406 内容风控本来就不记
    账号失败，4100404 风控与普通 400 走各自的分支

运行：
  python tests/test_marvis_quota_isolation.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(ROOT))

from admin.platform import shared  # noqa: E402
from admin.platform.marvis import gateway  # noqa: E402
from admin.platform.marvis.core import client  # noqa: E402

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


def _account(acct_id: int = 7) -> SimpleNamespace:
    return SimpleNamespace(
        id=acct_id, uid="uid-%d" % acct_id, platform="marvis",
        status="active", balance_remain=228781, balance_total=10000000,
        cool_until=None, last_err_msg="", cool_kind="",
        last_used_at=None, last_picked_at=None,
    )


def _run(exc: client.UpstreamError) -> list[dict]:
    """让 `_attempt` 在「选到号 → 上游报错」这条路上跑一遍，收集 note_failure 的入参。"""
    calls: list[dict] = []

    def _capture(db, account, **kw):  # noqa: ANN001 - 与 note_failure 同形
        calls.append(kw)

    with mock.patch.object(shared, "select_account", return_value=_account()), \
            mock.patch.object(shared, "lease", return_value=True), \
            mock.patch.object(shared, "release", return_value=None), \
            mock.patch.object(shared, "note_failure", side_effect=_capture), \
            mock.patch.object(gateway.marvis_accounts, "ensure_token",
                              return_value={}), \
            mock.patch.object(client, "chat_completions", side_effect=exc):
        try:
            gateway._attempt(None, None, None, {"model": "main-auto"}, "cn",
                             stream=False)
        except Exception:  # noqa: BLE001 - 这条路的终点就是抛错
            pass
    return calls


def test_quota_exceeded_marks_balance() -> None:
    print("\n== 额度耗尽（3000001）→ zero_balance=True ==")
    exc = client.UpstreamError(
        "模型网关失败（HTTP 402）", status=402, code="402",
        kind="quota_exceeded",
        detail='{"code":3000001,"msg":"用户名下token不足"}')
    calls = _run(exc)
    check("note_failure 被调用", bool(calls), repr(calls))
    check("zero_balance=True",
          bool(calls) and calls[0].get("zero_balance") is True,
          repr(calls[0] if calls else None))
    check("kind 原样透传",
          bool(calls) and calls[0].get("kind") == "quota_exceeded",
          repr(calls[0] if calls else None))
    check("换号重试三次都带标记",
          len(calls) == gateway.MAX_ATTEMPTS
          and all(c.get("zero_balance") is True for c in calls),
          f"calls={len(calls)}")


def test_insufficient_quota_marks_balance() -> None:
    print("\n== 额度不足（1000007）→ zero_balance=True ==")
    exc = client.UpstreamError(
        "模型网关失败（HTTP 400）", status=400, code="400",
        kind="quota_exceeded",
        detail='{"code":1000007,"msg":"积分余额不足"}')
    calls = _run(exc)
    check("zero_balance=True",
          bool(calls) and calls[0].get("zero_balance") is True,
          repr(calls[0] if calls else None))


def test_other_failures_do_not_mark_balance() -> None:
    print("\n== 对照组：其它失败不得清零余额 ==")
    # 4100406 内容风控是**请求侧**问题：换号无效，连账号失败都不记。
    calls = _run(client.UpstreamError(
        "非法请求", status=403, code="403", kind="content_block",
        detail='{"code":4100406,"msg":"非法请求"}'))
    check("content_block（4100406）→ 不记账号失败", calls == [], repr(calls))

    for exc, label in (
        (client.UpstreamError("请求被风控拦截", status=403, code="403",
                              kind="risk_control",
                              detail='{"code":4100404}'),
         "risk_control（4100404）"),
        (client.UpstreamError("bad request", status=400, code="400",
                              kind="http_error",
                              detail='{"code":4000001,"msg":"bad request"}'),
         "普通 400"),
    ):
        calls = _run(exc)
        ok = bool(calls) and all(not c.get("zero_balance") for c in calls)
        check(f"{label} → 不带 zero_balance", ok,
              repr(calls[0] if calls else None))


if __name__ == "__main__":
    test_quota_exceeded_marks_balance()
    test_insufficient_quota_marks_balance()
    test_other_failures_do_not_mark_balance()
    print()
    print(f"通过 {PASS} / {PASS + FAIL}")
    sys.exit(1 if FAIL else 0)
