#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按模型归属选平台（2026-11-19）：裸模型名不许被丢到「没有这个模型」的平台。

## 事故

不带 X-Platform 打 model=deepseek-v4.1-flash -> HTTP 400 code=1000007「积分余额不足」。
真因不是 marvis 没额度，而是 dispatch.resolve() 把它路由到了 raccoon：

    X-Platform > Key 绑定 > 按模型选路（**仅 key.aggregate=1**） > 策略分摊 > 默认平台
    生产 key：platform='' + aggregate=0
      -> 跳过按模型选路 -> 落「无头分摊」-> throughput 策略按可用号数选中 raccoon
         （raccoon 27 个号 > marvis 8 个号；而 raccoon 的积分早就耗尽）

## 本次锁的是什么

1. 裸名 + 无 X-Platform + Key 无绑定 -> 按「谁登记了这个模型」选平台，
   **与 aggregate 开关无关**；
2. 命中唯一 -> 直接路由该平台；
3. 命中多个 -> 只在命中集合内按策略分摊（集合外的平台一律不许进）；
4. 谁都没有 -> 404 model_not_found，错误体写明「模型在哪些平台存在」+ 可选平台；
5. 显式 X-Platform 仍然最高优先；
6. Key 绑定的平台仍然优先于按模型选路；
7. **不恢复跨平台改道**：选中后平台钉死（本文件最后一条断言锁这个）。

运行：
  python tests/test_dispatch_model_ownership.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault(
    "ADMIN_DATABASE_URL",
    "sqlite:///" + str(Path(tempfile.gettempdir()) / "dispatch_ownership_test.db"),
)

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from admin.db import Base  # noqa: E402
from admin.models import Account, ModelConfig, SystemSetting  # noqa: E402
from admin.platform import dispatch, registry  # noqa: E402
from admin.platform.loader import load_platforms  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [ok] %s" % name)
    else:
        FAIL += 1
        print("  [!!] %s  %s" % (name, detail))


class _Req:
    """最小 Request 替身：resolve() 只读 headers / query_params / scope。"""

    def __init__(self, model: str = "", headers=None, query=None):
        self.headers = dict(headers or {})
        self.query_params = dict(query or {})
        self.scope = {"_wb_body": ('{"model": "%s"}' % model).encode() if model else b""}


def build_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    # 线上形态（2026-11-19 生产库实测）：
    #   deepseek-v4.1-flash: workbuddy / raccoon / loomy / marvis / trae 都"声明"了，
    #   但 raccoon 是**私有槽位改名**（sn-deepseek-v4-1-flash-1017），
    #   而 loomy / trae 没有可用账号。
    db.add_all([
        ModelConfig(platform="marvis", model_id="deepseek-v4.1-flash", enabled=1),
        ModelConfig(platform="workbuddy", model_id="deepseek-v4.1-flash", enabled=1),
        ModelConfig(platform="raccoon", model_id="sn-deepseek-v4-1-flash-1017", enabled=1),
        ModelConfig(platform="loomy", model_id="DeepSeek-V4.1-Flash", enabled=1),
        ModelConfig(platform="trae", model_id="deepseek-v4.1-flash", enabled=1),
        # 单一声明者：只 marvis 有
        ModelConfig(platform="marvis", model_id="hunyuan-vision-2.0", enabled=1),
        # 多声明者：三家都精确登记
        ModelConfig(platform="marvis", model_id="multi-hit-model", enabled=1),
        ModelConfig(platform="workbuddy", model_id="multi-hit-model", enabled=1),
        ModelConfig(platform="raccoon", model_id="multi-hit-model", enabled=1),
    ])

    def add_accounts(platform: str, count: int, realm: str = "cn") -> None:
        for i in range(count):
            db.add(Account(platform=platform, name="%s-%02d" % (platform, i),
                           uid="%s-%02d" % (platform, i), auth_json="{}",
                           status="active", domain=realm,
                           balance_total=100, balance_remain=100))

    add_accounts("marvis", 8)
    add_accounts("workbuddy", 3, realm="")
    add_accounts("raccoon", 27)
    add_accounts("loomy", 0)
    add_accounts("trae", 0)
    # qoder 号很多、但**一个模型都没登记**：用来证明"全局策略"想选它也不行
    add_accounts("qoder", 50)

    # 生产策略：throughput + spread_unbound（按可用号数排，号最多的先上）
    db.add(SystemSetting(key="gateway:routing",
                         value='{"strategy": "throughput", "priority": {"raccoon": 1},'
                               ' "spread_unbound": true}'))
    db.commit()
    return db, Session


def main() -> int:
    load_platforms()
    db, Session = build_db()

    saved = (dispatch._key_of, dispatch._disabled_platforms, dispatch._db,
             dispatch._strategy_pick, dispatch._spread_unbound)
    # 生产 key：platform=''、aggregate=0（这两件事本身就是本次事故的一半成因）
    bare_key = SimpleNamespace(id=3, name="m", platform="", aggregate=0,
                               group_id=0, account_id=None, account_ids="")
    dispatch._key_of = lambda _r: bare_key
    dispatch._disabled_platforms = lambda: set()
    dispatch._db = Session
    # 全局策略（无头分摊）指到 raccoon：只要代码走了它，断言立刻失败
    dispatch._strategy_pick = lambda: ("raccoon", "cn")
    dispatch._spread_unbound = lambda: True

    try:
        print("\n== 1) 裸名 deepseek-v4.1-flash：无 X-Platform、Key 无绑定 ==")
        picked = dispatch.resolve(_Req("deepseek-v4.1-flash"))
        check("解析到 marvis（不是积分耗尽的 raccoon）", picked == ("marvis", "cn"), picked)
        check("确实不是 raccoon", picked[0] != "raccoon", picked)

        print("\n== 2) 裸名 + 模型只在单一平台存在 ==")
        picked = dispatch.resolve(_Req("hunyuan-vision-2.0"))
        check("hunyuan-vision-2.0 -> marvis", picked == ("marvis", "cn"), picked)

        print("\n== 3) 裸名 + 模型在多个平台存在：只在命中集合内分摊 ==")
        picked = dispatch.resolve(_Req("multi-hit-model"))
        check("落点属于命中集合 {marvis, workbuddy, raccoon}",
              picked[0] in ("marvis", "workbuddy", "raccoon"), picked)
        check("号最多但**没登记**这个模型的 qoder 不会被选中",
              picked[0] != "qoder", picked)

        print("\n== 4) 裸名 + 谁都没有 -> 404，错误体写清可用平台 ==")
        exc = None
        try:
            dispatch.resolve(_Req("no-such-model-zzz"))
        except dispatch.ModelUnknownOnAnyPlatform as err:
            exc = err
        check("抛出 ModelUnknownOnAnyPlatform", exc is not None)
        if exc is not None:
            payload = dispatch.model_not_found_payload(exc)
            err_body = payload["error"]
            check("code=model_not_found", err_body["code"] == "model_not_found", err_body)
            check("platforms 为空（模型不在任何平台）",
                  err_body["platforms"] == [], err_body)
            check("available_platforms 列出可选平台",
                  "marvis" in err_body["available_platforms"]
                  and "workbuddy" in err_body["available_platforms"], err_body)
            check("message 里点名了模型", "no-such-model-zzz" in err_body["message"],
                  err_body)

        print("\n== 5) X-Platform 显式指定仍然最高优先 ==")
        picked = dispatch.resolve(_Req("deepseek-v4.1-flash", headers={"x-platform": "raccoon"}))
        check("显式 X-Platform: raccoon 照旧", picked == ("raccoon", "cn"), picked)

        print("\n== 6) Key 平台绑定仍然优先于按模型选路 ==")
        bound_key = SimpleNamespace(id=2, name="2", platform="raccoon", aggregate=0,
                                    group_id=0, account_id=None, account_ids="")
        dispatch._key_of = lambda _r: bound_key
        picked = dispatch.resolve(_Req("deepseek-v4.1-flash"))
        check("绑了 raccoon 的 Key -> raccoon", picked == ("raccoon", "cn"), picked)
        dispatch._key_of = lambda _r: bare_key

        print("\n== 7) 不恢复跨平台改道：选中后平台钉死 ==")
        # _route_by_model_ownership 只返回**一个**平台；运行期"失败换平台"由
        # auto_route（仅 aggregate=1 时）决定，这里断言选择阶段不产生候选列表。
        result = dispatch._route_by_model_ownership("deepseek-v4.1-flash", bare_key, set())
        check("返回单个 (platform, realm)，不是候选列表",
              isinstance(result, tuple) and len(result) == 2, result)
        check("aggregate=0 时不会因为按模型选路打开多平台重试",
              dispatch._aggregate_enabled(bare_key) is False,
              dispatch._aggregate_enabled(bare_key))
    finally:
        (dispatch._key_of, dispatch._disabled_platforms, dispatch._db,
         dispatch._strategy_pick, dispatch._spread_unbound) = saved
        db.close()

    print("\n== 结果：%d 通过 / %d 失败 ==" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
