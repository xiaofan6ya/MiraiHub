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

import atexit
import contextlib
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# 数据库引导（2026-11-22）：这个脚本做的是纯函数断言，却间接依赖 model_configs
#
#   · `is_native_model_id()` 不传 db 时自开 `admin.db.SessionLocal()`；
#   · `alias_to_upstream()` 的「原生 id 优先」判定用它；
#   · 裸名分支还要 `platform_of_model()` 查同一张表。
#
# 本机没有 MySQL 时那条查询直接抛 OperationalError，被 except 吞掉后**静默
# 返回 False** —— 「原生 id 优先」于是被误判成失败（长期 16/18）。
#
# 这里在 import admin.db **之前**把 URL 指到本轮专属临时 SQLite（直接赋值，
# 不用 setdefault，避免继承外部配置），建表并插入脚本假定的目录行。
# 断言一个字都没改，只是把环境补齐。
# ---------------------------------------------------------------------------
_TMP_DIR = Path(tempfile.mkdtemp(prefix="marvis_model_naming_"))
os.environ["ADMIN_DATABASE_URL"] = "sqlite:///" + (
    _TMP_DIR / "naming.db").as_posix()

#: 脚本断言所假定的目录 `(platform, model_id)`。
#: Marvis 是**槽位名**版目录（线上 model_configs 存的就是槽位），
#: Qoder 存缩写 id，Loomy / Trae 存它们自己的写法。
_CATALOG = (
    ("marvis", "main-auto"),
    ("marvis", "deepseek-v4-pro-external"),
    ("marvis", "hy3-preview-agent-real"),
    ("qoder", "dfmodel"),
    ("qoder", "dmodel"),
    ("qoder", "kmodel"),
    ("qoder", "auto"),
    ("loomy", "spark-x2.6"),
    ("trae", "glm-5.3"),
)

from admin.platform import model_suffix  # noqa: E402

PASS = 0
FAIL = 0


def _bootstrap_catalog() -> None:
    """建表 + 插入脚本假定的目录行（幂等：先清后插）。

    必须在 `admin.db` 已经绑定到本轮临时 SQLite **之后**调用；
    `admin.db` 是在这里才第一次 import 的，环境变量已经设好了。
    """
    from admin import db as admin_db
    from admin.models import ModelConfig

    admin_db.Base.metadata.create_all(bind=admin_db.engine)
    with admin_db.SessionLocal() as db:
        db.query(ModelConfig).delete()
        for platform, model_id in _CATALOG:
            db.add(ModelConfig(platform=platform, model_id=model_id,
                               enabled=1, credit_multiplier=1.0,
                               upstream_metadata=""))
        db.commit()


def _teardown() -> None:
    """释放引擎并删临时目录（Windows 上 sqlite 文件被持有时先 dispose 再删）。"""
    with contextlib.suppress(Exception):
        from admin import db as admin_db

        admin_db.engine.dispose()
    for _attempt in range(10):
        shutil.rmtree(_TMP_DIR, ignore_errors=True)
        if not _TMP_DIR.exists():
            return
        time.sleep(0.1)


# 模块导入即建目录：这样 `python tests/xxx.py` 与 pytest 收集两种入口都能跑。
_bootstrap_catalog()
atexit.register(_teardown)


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [ok] {name}")
    else:
        FAIL += 1
        print(f"  [!!] {name}  {detail}")


def test_marvis_no_rewrite() -> None:
    """Marvis 的可读名 / 槽位名不再被**跨平台改名**，但仍会回平台归属。

    ## 2026-11-20 跟着 PR #59 改了

        旧断言：`alias_to_upstream(name) == ("", "")` —— 表示「完全不改写」。

        PR #59 加了「裸模型名按平台归属路由」之后，带 marvis 后缀的模型名
        会返回 `("marvis", "<槽位名>")`：这里的第二个元素不是给上游的**改名**，
        而是该模型在 marvis 平台上的槽位 —— 路由用它决定打到哪个平台。

        所以要区分两件事：
          · **跨平台改名**（把 `xxx-marvis` 改成别家平台的模型 id）→ 仍然不许
          · **平台归属解析**（认出这是 marvis 的哪个槽位）→ PR #59 之后正常

    下面同时锁这两条：平台必须是 marvis，槽位必须落在合法表里。
    """
    print("\n== Marvis：可读名/wiki 槽位名不再被跨平台改写，但要正确归属 ==")
    expected = {
        "deepseek-v4.1-flash-marvis": "main-auto",
        "deepseek-v4-pro-marvis": "deepseek-v4-pro-external",
        "hy3-marvis": "hy3-preview-agent-real",
    }
    for name in ("deepseek-v4.1-flash-marvis", "deepseek-v4.1-flash",
                 "deepseek-v4-pro-marvis", "hy3-marvis"):
        got = model_suffix.alias_to_upstream(name)
        if name in expected:
            check(f"{name} 归属 marvis 且槽位正确",
                  got == ("marvis", expected[name]), got)
        else:
            # 不带后缀的裸名：不在 merivs 后缀表里，保持原样
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
    try:
        test_marvis_no_rewrite()
        test_marvis_suffix()
        test_other_platforms_intact()
        test_guard_source()
    finally:
        _teardown()
    print()
    print(f"通过 {PASS} / {PASS + FAIL}")
    sys.exit(1 if FAIL else 0)
