#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模型名改写回归：可读名不该被改写成槽位名（2026-11-15 线上 404）。

## 事故

线上 `deepseek-v4.1-flash-marvis` 一直 404，日志：

    dispatch 分叉 | raw_model='main-auto' -> platform='marvis'
    dispatch 模型不属于目标平台 model=main-auto platform=marvis

链路：`alias_to_upstream()` 把 `deepseek-v4.1-flash` 改写成槽位名
`main-auto`，紧接着 `_model_for_platform()` 按 `model_configs` 校验归属 ——
而同步后的清单里存的是**可读名**，`main-auto` 不在其中 → 判「不属于该平台」。

修法：平台已把可读名当作自己的 `model_configs.model_id` 时，**不**再做别名改写
（`is_native_model_id`）。Qoder 的 `deepseek-flash` 不是原生 id，照旧改写，
所以别的平台不受影响。

运行：
  python tests/test_marvis_model_naming.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(ROOT))

from admin.platform import model_suffix  # noqa: E402

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


def test_marvis_no_rewrite() -> None:
    print("\n== Marvis：可读名/wiki 槽位名不再被改写 ==")
    for name in ("deepseek-v4.1-flash-marvis", "deepseek-v4.1-flash",
                 "deepseek-v4-pro-marvis", "hy3-marvis"):
        got = model_suffix.alias_to_upstream(name)
        check(f"{name} 不改写", got == ("", ""), got)


def test_marvis_suffix() -> None:
    print("\n== Marvis：后缀照常剥离 ==")
    pid, bare, matched = model_suffix.resolve("deepseek-v4.1-flash-marvis")
    check("后缀识别为 marvis", pid == "marvis" and matched, pid)
    check("裸名正确", bare == "deepseek-v4.1-flash", bare)


def test_other_platforms_intact() -> None:
    print("\n== 其它平台的别名改写不受影响 ==")
    # Qoder 的配置 id 是缩写，必须继续改写，否则上游 400
    check("deepseek-flash-qoder -> dfmodel",
          model_suffix.alias_to_upstream("deepseek-flash-qoder") == ("qoder", "dfmodel"),
          model_suffix.alias_to_upstream("deepseek-flash-qoder"))
    check("deepseek-v4-pro-qoder -> dmodel",
          model_suffix.alias_to_upstream("deepseek-v4-pro-qoder") == ("qoder", "dmodel"),
          model_suffix.alias_to_upstream("deepseek-v4-pro-qoder"))
    # 已经是原生 id 的不动
    for name in ("dfmodel-qoder", "kmodel-qoder", "spark-x2.6-loomy",
                 "glm-5.3-trae-cn", "auto-qoder"):
        got = model_suffix.alias_to_upstream(name)
        check(f"{name} 原生 id 不改写", got == ("", ""), got)


def test_guard_source() -> None:
    print("\n== 防护实现（静态检查）==")
    suffix_src = (ROOT / "admin" / "platform" / "model_suffix.py").read_text(encoding="utf-8")
    check("alias_to_upstream 调用了原生 id 判定",
          "is_native_model_id" in suffix_src)
    check("判定发生在改写之前",
          suffix_src.index("is_native_model_id") <
          suffix_src.index("MODEL_NAME_MAP"), "顺序不对")

    router_src = (ROOT / "admin" / "platform" / "models_router.py").read_text(encoding="utf-8")
    check("is_native_model_id 有定义", "def is_native_model_id" in router_src)
    # 这个函数内部必须自己 import ModelConfig —— 本模块是函数内局部导入的，
    # 少了这一句 NameError 会被 except 吞掉，防护永远返回 False。
    body = router_src[router_src.index("def is_native_model_id"):]
    body = body[:body.index("\ndef ")]
    check("函数内自行导入 ModelConfig",
          "from admin.models import ModelConfig" in body)
    check("异常时记日志而不是静默",
          "logger.warning" in body, body[:200])


if __name__ == "__main__":
    test_marvis_no_rewrite()
    test_marvis_suffix()
    test_other_platforms_intact()
    test_guard_source()
    print()
    print(f"通过 {PASS} / {PASS + FAIL}")
    sys.exit(1 if FAIL else 0)
