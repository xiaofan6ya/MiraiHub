#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Marvis 凭证时效与错误文案的回归测试（不联网、不连库）。

锁定 2026-11-15 实测出来的两件事：
  1. QQ / 微信登录**不下发 `expires_in`**，token 实测只活约 2 小时；
  2. `data: null` 是登录态失效的信号，不是业务失败。

运行：
  python tests/test_marvis_token_ttl.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(ROOT))

from admin.platform.marvis.services import accounts as macc  # noqa: E402

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


def test_ttl_constant() -> None:
    print("\n== TTL 常量 ==")
    # 实测 2 小时 10 分失效；取 2 小时 + 10 分钟预警余量
    check("TTL 是 2 小时", macc.TOKEN_TTL_SECONDS == 7200,
          macc.TOKEN_TTL_SECONDS)
    check("预警提前 10 分钟", macc.TOKEN_WARN_SECONDS == 600,
          macc.TOKEN_WARN_SECONDS)
    check("预警小于 TTL", macc.TOKEN_WARN_SECONDS < macc.TOKEN_TTL_SECONDS)


def test_age() -> None:
    print("\n== 凭证年龄 ==")
    now = int(time.time())
    check("缺 issued_at 返回 None", macc.token_age_seconds({}) is None)
    check("issued_at=0 返回 None",
          macc.token_age_seconds({"issued_at": 0}) is None)
    check("issued_at 非法返回 None",
          macc.token_age_seconds({"issued_at": "abc"}) is None)
    age = macc.token_age_seconds({"issued_at": now - 600})
    check("600 秒前签发 → 约 600", 595 <= (age or 0) <= 610, age)


def test_hint_states() -> None:
    print("\n== 时效状态分档 ==")
    now = int(time.time())

    fresh = macc.token_expiry_hint({"issued_at": now - 60})
    check("刚签发 → fresh", fresh["state"] == "fresh", fresh)
    check("fresh 剩余约 TTL-60",
          abs((fresh["remaining_seconds"] or 0) - (7200 - 60)) <= 5, fresh)

    soon = macc.token_expiry_hint({"issued_at": now - (7200 - 300)})
    check("剩 5 分钟 → soon", soon["state"] == "soon", soon)

    expired = macc.token_expiry_hint({"issued_at": now - 7500})
    check("超 TTL → expired", expired["state"] == "expired", expired)
    check("expired 剩余为 0", expired["remaining_seconds"] == 0, expired)

    unknown = macc.token_expiry_hint({})
    check("无 issued_at → unknown", unknown["state"] == "unknown", unknown)


def test_error_mapping() -> None:
    print("\n== 错误文案（静态检查源码）==")
    client_src = (ROOT / "admin" / "platform" / "marvis" / "core" / "client.py"
                  ).read_text(encoding="utf-8")
    check("data:null 归为 auth 而不是业务失败",
          'kind="auth"' in client_src and "登录态已过期" in client_src)
    check("不再报「钱包接口没有返回 data 字段」",
          "钱包接口没有返回 data 字段" not in client_src)

    gateway_src = (ROOT / "admin" / "platform" / "marvis" / "gateway.py"
                   ).read_text(encoding="utf-8")
    check("401 提示引导去重新扫码",
          "重新扫码" in gateway_src and "credential_expired" in gateway_src)
    check("不再笼统说「请先在控制台导入凭证」",
          "请先在控制台导入凭证" not in gateway_src)
    check("区分「账号都在冷却」与「没有账号」",
          "accounts_cooling" in gateway_src and "no_available_account" in gateway_src)
    check("全池过期时给专门文案",
          "登录态已全部过期" in gateway_src)

    service_src = (ROOT / "admin" / "platform" / "marvis" / "services"
                   / "accounts.py").read_text(encoding="utf-8")
    check("入库时记录 issued_at", 'data.setdefault("issued_at"' in service_src)
    check("账号视图带 token_expiry", '"token_expiry"' in service_src)


if __name__ == "__main__":
    test_ttl_constant()
    test_age()
    test_hint_states()
    test_error_mapping()
    print()
    print(f"通过 {PASS} / {PASS + FAIL}")
    sys.exit(1 if FAIL else 0)