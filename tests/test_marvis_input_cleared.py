#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Marvis 授权弹窗的输入框清空（2026-11-16 用户实测指出）。

## 现象

用户提交授权回调后，成功或失败**都不清空输入框**。授权码是一次性的，
框里留着的那条已经作废，用户再点一次「提交并入库」必然又报
`-107 已过期或已被使用` —— 表现就是「怎么老是弹这个错」，
而正确动作应该是重扫拿新 code。

## 必须成立的三件事

1. 失败分支清空输入框（否则下次误提交刷 -107）；
2. 成功分支也清空（code 已消耗，留着没意义）；
3. 两处都要清 `lastTried`（否则新 code 恰好等于旧串时会被防重挡掉，
   看起来像「点了没反应」）。

另外页面提示别再说「授权码没有被消耗」——那句话会诱导用户反复提交
同一条，与事实相反。

运行：
  python tests/test_marvis_input_cleared.py
"""
from __future__ import annotations

import io
import os
import re
import sys
from pathlib import Path

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

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


def main() -> None:
    path = ROOT / "admin" / "console" / "console-app.js"
    src = path.read_text(encoding="utf-8")

    print("== 输入框清空（至少 3 处：失败 / 成功 / 异常）==")
    clears = re.findall(r'cbInput\.value\s*=\s*""', src)
    check("清空语句 >= 3 处", len(clears) >= 3, f"找到 {len(clears)} 处")

    # 定位 marvis 的 submitCallback
    start = src.index("const submitCallback = async (raw")
    end = src.index("if (submitBtn) {", start)
    body = src[start:end]

    check("失败分支清空", 'cbInput.value = ""' in body)
    check("同时清 lastTried", 'lastTried = ""' in body)
    check("清空后把焦点交回输入框", "cbInput.focus()" in body)

    # 成功分支（closeModal 之前）也要清
    cm = body.find("closeModal()")
    check("成功分支在 closeModal 前清空",
          cm > 0 and body.rfind('cbInput.value = ""', 0, cm) > 0,
          "closeModal 之前没找到清空")

    # catch 分支清空
    catch_idx = body.find("} catch (e) {")
    check("catch 分支也清空",
          catch_idx > 0 and 'cbInput.value = ""' in body[catch_idx:],
          "catch 里没有清空")

    print()
    print("== 页面提示不能再骗人 ==")
    check("不再说「授权码没有被消耗」",
          "授权码没有被消耗" not in src,
          "这句会诱导用户反复提交同一条")
    check("明确说明「一个授权码只能用一次」",
          "一个授权码只能用一次" in src)
    check("提示重扫而不是重复提交",
          "重新扫码拿新的" in src)
    check("保留 -109 是正常的解释",
          "RC_PARAMS_INVALID" in src and "是正常的" in src)

    print()
    print(f"通过 {PASS} / {PASS + FAIL}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
