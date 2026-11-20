#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小浣熊额度失败的**对外表态**与**落库**（2026-11-19）。

## 事故

裸名 deepseek-v4.1-flash 被路由到 raccoon（积分耗尽）后，客户端收到的是
上游原样错误：HTTP 400 + code=1000007「积分余额不足」。两个问题：

1. **语义误导**：400 看起来是「你的请求有错」，而真正的解法是换平台/换模型
   （这个平台的积分没了，换号无效）；还会触发客户端自己的重试风暴。
2. **控制台看不见**：失败路径一行 usage_logs 都没写（只有成功才记账），
   于是「某个模型在这个平台上一直 1000007」在面板上完全不可见。

## 本次锁的是什么

1. kind=quota -> 402 + code=quota_exhausted（不再透传 400/1000007）；
2. scope 分层：platform:<平台名>（整池额度耗尽，换号无效）/ account（账号级）；
3. 上游业务码另放 upstream_code，供排障对齐，不作为对外 code；
4. 非额度失败**不受影响**（502/upstream 照旧透传）；
5. 失败也写一行 usage_logs，error_kind='quota'；
6. 额度冷却落在 (账号 × 模型) 维度（account_model_cool），不再整号 600s。

运行：
  python tests/test_raccoon_quota_surface.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault(
    "ADMIN_DATABASE_URL",
    "sqlite:///" + str(Path(tempfile.gettempdir()) / "raccoon_quota_surface_test.db"),
)

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import admin.db as admin_db  # noqa: E402
from admin.db import Base  # noqa: E402
from admin.models import Account, AccountModelCooldown, UsageLog  # noqa: E402
from admin.platform.raccoon import gateway as raccoon_gateway  # noqa: E402
from admin.platform.raccoon.core import client  # noqa: E402

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


def quota_error(scope: str = "platform:raccoon"):
    """造一个真机形态的额度失败：HTTP 400 + 业务码 1000007。"""
    exc = client.UpstreamError("raccoon#1 上游错误（HTTP 400）",
                               status=400, kind="quota", code=1000007)
    exc.quota_scope = scope
    exc.account_id = 1
    exc.model = "deepseek-v4.1-flash"
    return exc


def body_of(response) -> dict:
    return json.loads(response.body.decode("utf-8"))


def build_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    db.add(Account(platform="raccoon", name="test-account", uid="t-1",
                   auth_json="{}", status="active", domain="cn",
                   balance_total=100, balance_remain=100))
    db.commit()
    return db, Session


class _Request:
    """最小 request 替身：记账只读 scope / headers / query_params。"""

    def __init__(self, model: str = "deepseek-v4.1-flash"):
        self.headers = {"x-platform": "raccoon"}
        self.query_params = {}
        self.scope = {"_wb_body": ('{"model": "%s"}' % model).encode()}


def main() -> int:
    db, Session = build_db()
    saved_session = admin_db.SessionLocal
    admin_db.SessionLocal = Session
    try:
        print("\n== 1) 平台级额度耗尽 -> 402 quota_exhausted + scope=platform:raccoon ==")
        response = raccoon_gateway._fail(quota_error("platform:raccoon"))
        payload = body_of(response)["error"]
        check("HTTP 402（不再透传上游 400）", response.status_code == 402,
              response.status_code)
        check("code=quota_exhausted", payload.get("code") == "quota_exhausted", payload)
        check("scope=platform:raccoon", payload.get("scope") == "platform:raccoon", payload)
        check("platform 字段点名平台", payload.get("platform") == "raccoon", payload)
        check("上游业务码放 upstream_code（不再占用 code）",
              payload.get("upstream_code") == 1000007, payload)
        check("文案讲清「换号无效、换平台/模型」",
              "换号无效" in payload.get("message", ""), payload)

        print("\n== 2) 账号级额度耗尽 -> scope=account ==")
        response = raccoon_gateway._fail(quota_error("account"))
        payload = body_of(response)["error"]
        check("HTTP 402", response.status_code == 402, response.status_code)
        check("scope=account", payload.get("scope") == "account", payload)
        check("platform 级专属字段不出现", "platform" not in payload, payload)

        print("\n== 3) 非额度失败不受影响（不把所有 4xx 都改写成 402）==")
        other = client.UpstreamError("raccoon#1 上游错误（HTTP 502）",
                                     status=502, kind="upstream")
        response = raccoon_gateway._fail(other)
        check("HTTP 502 照旧", response.status_code == 502, response.status_code)
        check("kind/upstream 照旧透传", body_of(response)["error"].get("type") == "upstream",
              body_of(response))

        print("\n== 4) 失败也落 usage_logs（error_kind=quota）==")
        before = db.query(UsageLog).count()
        raccoon_gateway._bill_error(
            SimpleNamespace(id=3), _Request(), {"model": "deepseek-v4.1-flash"},
            quota_error("platform:raccoon"))
        rows = (db.query(UsageLog).order_by(UsageLog.id.desc()).limit(3).all())
        check("新增了一行", db.query(UsageLog).count() == before + 1,
              db.query(UsageLog).count())
        if rows:
            row = rows[0]
            check("error_kind='quota'", (row.error_kind or "") == "quota", row.error_kind)
            check("platform='raccoon'", (row.platform or "") == "raccoon", row.platform)
            check("model 记的是请求的模型名",
                  (row.model or "") == "deepseek-v4.1-flash", row.model)
            check("account_id 归属失败账号", int(row.account_id or 0) == 1, row.account_id)
            check("失败不扣额度", float(row.credits or 0) == 0.0, row.credits)

        print("\n== 5) 额度冷却落在 (账号 × 模型) 维度，不整号 600s ==")
        account = db.query(Account).filter(Account.platform == "raccoon").first()
        account.cool_until = None
        db.commit()
        raccoon_gateway._cool_quota_model(db, account, "deepseek-v4.1-flash")
        row = (db.query(AccountModelCooldown)
               .filter(AccountModelCooldown.account_id == account.id,
                       AccountModelCooldown.model == "deepseek-v4.1-flash").first())
        check("写出 account_model_cool 行", row is not None)
        if row is not None:
            check("kind='quota'", (row.kind or "") == "quota", row.kind)
            check("until 在未来", row.until is not None, row.until)
        db.refresh(account)
        check("号本身没有被整号冷却（cool_until 仍为空）",
              account.cool_until is None, account.cool_until)

        src = Path(raccoon_gateway.__file__).read_text(encoding="utf-8")
        check("_attempt 里额度失败走模型维度冷却且不整号冷却",
              "_cool_quota_model(db, account, model)" in src and "cool = 0" in src)
        check("_attempt 把 scope/账号/模型钉在异常上（402 scope 的来源）",
              "exc.quota_scope" in src and "exc.account_id" in src)
    finally:
        admin_db.SessionLocal = saved_session
        db.close()

    print("\n== 结果：%d 通过 / %d 失败 ==" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
