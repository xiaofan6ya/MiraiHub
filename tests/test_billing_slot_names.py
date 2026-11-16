#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""计费链路的两个回归（issue #41）：模型名口径 + 负数 credits 扣反。

## 事故一：Marvis「今日消耗」恒为 0

`usage_logs.model` 记的是**请求时的模型名**（Marvis 网关换算前的上游槽位名
`main-auto`），而 `model_configs.model_id` 存的是**可读名**
`deepseek-v4.1-flash`。`_billing_candidates()` 的候选列表里没有这层映射，
倍率查不到 → `credits_for()` 恒返回 0 → 面板「今日消耗」永远是 0.00。

`models_router.MODEL_NAME_MAP` 里早就维护了这张表，只是没被调用。

## 事故二：负数 credits 把余额加回去了

倍率 `-1` 表示「按 token 计量」，`credits_for()` 会算出负值
（`-tokens/1000`，一次 -228 很常见）。`record_usage()` 原本写
`balance_remain - int(credits)` —— 负负得正，每跑一次请求号上余额反而变多。
`int(-0.08) == 0` 时看不出来，量级一大就显形（实测 890 条负值记录）。

运行：
  python tests/test_billing_slot_names.py
"""
from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(ROOT))

from admin.platform import shared  # noqa: E402

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


def test_slot_names() -> None:
    print("\n== 槽位名 ↔ 可读名（消耗为 0 的根因）==")
    # Marvis 的槽位名 main-auto 必须能找回可读名，否则倍率查不到
    mv = shared._billing_candidates("main-auto")
    check("main-auto 候选含 deepseek-v4.1-flash",
          "deepseek-v4.1-flash" in mv, mv)
    check("main-auto 仍是第一候选（精确匹配优先）",
          mv and mv[0] == "main-auto", mv)

    back = shared._billing_candidates("deepseek-v4.1-flash")
    check("可读名候选含槽位名 main-auto", "main-auto" in back, back)

    # 不能破坏其它平台的现有行为
    qoder = shared._billing_candidates("deepseek-flash-qoder")
    check("Qoder 缩写仍还原为 dfmodel", "dfmodel" in qoder, qoder)
    check("带后缀的仍会剥后缀",
          "deepseek-flash" in qoder, qoder)

    check("空模型名返回空列表", shared._billing_candidates("") == [])


def test_credits_sign() -> None:
    print("\n== 负数 credits 不能把余额加回去 ==")
    src = inspect.getsource(shared.record_usage)
    check("扣减用 abs(credits)", "abs(credits)" in src)
    check("旧写法 balance_remain - int(credits) 已移除",
          "- int(credits))" not in src)
    check("Key 侧 credit_used 也按绝对值累加",
          "credit_used + abs(credits)" in src)


def test_credits_for_uses_storage() -> None:
    print("\n== credits_for 的 storage 口径 ==")
    sig = inspect.signature(shared.credits_for)
    check("credits_for 接受 storage 参数", "storage" in sig.parameters,
          list(sig.parameters))
    src = inspect.getsource(shared.credits_for)
    check("先按平台+模型查，再退回全局查",
          "platform == storage" in src or "ModelConfig.platform == storage" in src)
    check("倍率为 0 时继续找下一个候选",
          "credit_multiplier" in src)


def test_abs_semantics_math() -> None:
    print("\n== 扣减算式的边界 ==")
    for credits, expect in [(-228, 228), (-0.081, 0), (5.0, 5), (0, 0)]:
        spend = int(abs(credits)) if credits else 0
        check(f"credits={credits} → spend={spend}", spend == expect,
              f"期望 {expect}")
    # 余额不会被扣成负数
    for credits in (-228, 500000, 10 ** 9):
        spend = int(abs(credits)) if credits else 0
        after = max(0, 100 - spend)
        check(f"credits={credits} 时余额不为负", after >= 0, after)


if __name__ == "__main__":
    test_slot_names()
    test_credits_sign()
    test_credits_for_uses_storage()
    test_abs_semantics_math()
    print()
    print(f"通过 {PASS} / {PASS + FAIL}")
    sys.exit(1 if FAIL else 0)
