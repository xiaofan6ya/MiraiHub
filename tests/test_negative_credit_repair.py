#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""issue #78 回归：负倍率不该把 Key 的「已用额度」写成负数。

## 事故形态（用户截图）

Key「蜂王浆」限额 1500，控制台显示：

    已用    -61.75
    剩余    1561.75     ← 比限额还多
    使用率  -4.1%

「已用」在语义上只增不减，负值只可能是记账写坏。

## 根因

`model_configs.credit_multiplier < 0` 是约定里的**「本平台没有倍率概念、
按上游 usage 计量」标记**（见 `admin/routers/models.py` 的
`_get_free_models`）。Trae 整张表、CodeArts、MiniMax、Loopy 以及
workbuddy 的 `hunyuan-chat` / `kling-video-v3` 都存的是 `-1`。

而 `admin/routers/proxy.py` 的 `_record_usage()` 用的是**真值判断**：

    if mc is not None and mc.credit_multiplier:   # ← -1 也是真
    ...
    if mult:                                       # ← -1 也是真
        credits = total_tokens * mult / 1000.0

于是每条走这两条路径的请求都算出 `tokens * -1 / 1000` 的**负积分**，
再 `credit_used += credits` → 已用额度被反向冲减，越用越小，
最终变成负数，`剩余 = 限额 - 已用` 也跟着超过限额本身。

同一个 bug 在三个记账点各写了一遍：`admin/routers/proxy.py:467`、
`admin/platform/trae/services/pool.py:385`、`admin/routers/videos.py:665`。

运行：
  python tests/test_negative_credit_repair.py
"""
from __future__ import annotations

import os
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from admin import db as db_module  # noqa: E402
from admin.db import Base  # noqa: E402
from admin.models import Account, ApiKey, ModelConfig, UsageLog  # noqa: E402
from admin.routers import proxy  # noqa: E402

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


def make_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add(Account(id=1, name="acc", uid="acc", auth_json="{}",
                       platform="workbuddy", balance_remain=1000))
        db.add(ApiKey(id=1, name="bee", key_hash="h", credit_limit=1500,
                      credit_used=0, unlimited=0))
        # workbuddy 里真实存在的负倍率行（-1 = 无倍率概念）
        db.add(ModelConfig(model_id="hunyuan-chat", platform="workbuddy",
                           enabled=1, credit_multiplier=-1.0))
        # 正倍率的对照组
        db.add(ModelConfig(model_id="glm-5.2", platform="workbuddy",
                           enabled=1, credit_multiplier=1.5))
        db.commit()
    return engine, factory


def bill(factory, model, credits=None, total=2000, platform="workbuddy"):
    with ExitStack() as stack:
        stack.enter_context(patch.object(proxy, "SessionLocal", factory))
        proxy._record_usage(1, 1, model, credits, None,
                            prompt_tokens=total // 2, completion_tokens=total // 2,
                            total_tokens=total, error_kind="success",
                            platform=platform)


def test_negative_multiplier_is_not_billing():
    print("\n== 负倍率（-1）不参与计费 ==")
    engine, factory = make_db()
    try:
        for _ in range(3):
            bill(factory, "hunyuan-chat")
        with factory() as db:
            key = db.get(ApiKey, 1)
            acc = db.get(Account, 1)
            rows = db.query(UsageLog).all()
        check("负数倍率不再产生负积分",
              all((r.credits or 0) >= 0 for r in rows),
              [(r.model, r.credits) for r in rows])
        check("Key 的「已用」不为负", (key.credit_used or 0) >= 0, key.credit_used)
        check("账号余额没有被反向加多", (acc.balance_remain or 0) <= 1000,
              acc.balance_remain)
        check("负倍率模型的用量仍落库（token 统计不丢）", len(rows) == 3, len(rows))
    finally:
        engine.dispose()


def test_positive_multiplier_still_bills():
    print("\n== 正倍率照旧计费（不能顺手把计费改没）==")
    engine, factory = make_db()
    try:
        bill(factory, "glm-5.2", total=1000)
        with factory() as db:
            row = db.query(UsageLog).one()
            key = db.get(ApiKey, 1)
        check("1000 token × 1.5 = 1.5 积分", abs(float(row.credits) - 1.5) < 1e-9, row.credits)
        check("Key 的「已用」同步 +1.5", abs(float(key.credit_used) - 1.5) < 1e-6,
              key.credit_used)
    finally:
        engine.dispose()


def test_upstream_negative_credit_is_debited_absolutely():
    print("\n== 上游回负 credit 时按绝对值扣 ==")
    engine, factory = make_db()
    try:
        # Hunyuan 的图/视频会回 -1 这类「标记值」，不是「送你积分」
        bill(factory, "hunyuan-chat", credits=-5.0, total=0)
        with factory() as db:
            key = db.get(ApiKey, 1)
            acc = db.get(Account, 1)
        check("已用不为负", (key.credit_used or 0) >= 0, key.credit_used)
        check("按 |−5| 扣，而不是加 5", abs(float(key.credit_used) - 5.0) < 1e-6,
              key.credit_used)
        check("账号余额同步扣减 5", (acc.balance_remain or 0) == 995,
              acc.balance_remain)
    finally:
        engine.dispose()


def test_history_repair():
    print("\n== 启动时修复历史负值（幂等）==")
    engine, factory = make_db()
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("UPDATE api_keys SET credit_used = -61.754 WHERE id = 1")
        original = db_module.engine
        db_module.engine = engine
        try:
            db_module._repair_negative_key_credits()
            with factory() as db:
                after = db.get(ApiKey, 1).credit_used
            check("历史负值归零", after == 0, after)
            # 再跑一次不能把正常值也抹掉
            db_module._repair_negative_key_credits()
            with factory() as db:
                db.get(ApiKey, 1).credit_used = 12.5
                db.commit()
            db_module._repair_negative_key_credits()
            with factory() as db:
                check("重跑不覆盖正值", db.get(ApiKey, 1).credit_used == 12.5,
                      db.get(ApiKey, 1).credit_used)
        finally:
            db_module.engine = original
    finally:
        engine.dispose()


def test_no_truthiness_on_multiplier():
    print("\n== 源码里不许再用真值判断倍率 ==")
    import inspect

    def code_only(src: str) -> str:
        """去掉注释行，只留真正会执行的代码（注释里会引用旧写法做说明）。"""
        out = []
        for line in src.splitlines():
            body = line.split("#", 1)[0]
            if body.strip():
                out.append(body)
        return "\n".join(out)

    src = inspect.getsource(proxy._record_usage)
    code = code_only(src)
    check("估算分支判 mult > 0", "mult > 0" in code, "找不到 `mult > 0`")
    check("不再用 `if mult:`（-1 会漏过）", "if mult:" not in code,
          [l for l in code.splitlines() if "if mult:" in l])
    check("不再有 `mc.credit_multiplier` 真值判断",
          "if mc is not None and mc.credit_multiplier:" not in code)
    check("扣减取绝对值", "abs(float(credits or 0))" in code)

    from admin.platform.trae.services import pool as trae_pool
    tcode = code_only(inspect.getsource(trae_pool.record_usage))
    check("Trae 侧判 multiplier > 0", "multiplier > 0" in tcode)
    check("Trae 侧按绝对值累加", "ApiKey.credit_used + abs(credits)" in tcode)


if __name__ == "__main__":
    test_negative_multiplier_is_not_billing()
    test_positive_multiplier_still_bills()
    test_upstream_negative_credit_is_debited_absolutely()
    test_history_repair()
    test_no_truthiness_on_multiplier()
    print()
    print(f"通过 {PASS} / {PASS + FAIL}")
    sys.exit(1 if FAIL else 0)
