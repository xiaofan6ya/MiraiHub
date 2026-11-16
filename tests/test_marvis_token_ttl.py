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
    print("\n== TTL 分档 ==")
    # 微信档：我自己实测 2 小时 10 分失效（13:34 授权 → 15:44 401）
    check("WX 档 2 小时",
          macc.TOKEN_TTL_BY_LOGIN["WX"] == 7200, macc.TOKEN_TTL_BY_LOGIN["WX"])
    check("WX 与默认档一致",
          macc.TOKEN_TTL_SECONDS == macc.TOKEN_TTL_BY_LOGIN["WX"],
          macc.TOKEN_TTL_SECONDS)
    # QQ 档：据 issue #41 报告人实测（4h29m 后仍有效），按 30 天保守估
    check("QC 档 30 天",
          macc.TOKEN_TTL_BY_LOGIN["QC"] == 30 * 24 * 3600,
          macc.TOKEN_TTL_BY_LOGIN["QC"])
    check("QC 档远长于 WX（不能把微信的 2h 套到 QQ 上）",
          macc.TOKEN_TTL_BY_LOGIN["QC"] > macc.TOKEN_TTL_BY_LOGIN["WX"] * 100)
    check("预警提前 10 分钟", macc.TOKEN_WARN_SECONDS == 600,
          macc.TOKEN_WARN_SECONDS)
    check("预警小于最短档",
          macc.TOKEN_WARN_SECONDS < min(macc.TOKEN_TTL_BY_LOGIN.values()))


def test_ttl_by_login_type() -> None:
    print("\n== 按登录方式取 TTL ==")
    check("login_type='WX' → 2h",
          macc.token_ttl_seconds({"login_type": "WX"}) == 7200)
    check("login_type='QC' → 30d",
          macc.token_ttl_seconds({"login_type": "QC"}) == 30 * 24 * 3600)
    # 历史数据里 login_type 存的是数字
    check("login_type='2'（数字）按微信算",
          macc.token_ttl_seconds({"login_type": "2"}) == 7200)
    check("login_type='1'（数字）按 QQ 算",
          macc.token_ttl_seconds({"login_type": "1"}) == 30 * 24 * 3600)
    check("缺 login_type 默认微信档",
          macc.token_ttl_seconds({}) == 7200)
    check("login_type_of 归一 QC",
          macc.login_type_of({"login_type": "qc"}) == "QC")
    check("login_type_of 归一 WX",
          macc.login_type_of({"login_type": "2"}) == "WX")


def test_qc_not_falsely_warned() -> None:
    print("\n== QQ 号不该被 2 小时档误报 ==")
    now = int(time.time())
    # 签发 3 小时：微信已过期，QQ 仍 fresh
    qc = macc.token_expiry_hint({"issued_at": now - 3 * 3600, "login_type": "QC"})
    wx = macc.token_expiry_hint({"issued_at": now - 3 * 3600, "login_type": "WX"})
    check("QQ 3 小时后仍 fresh", qc["state"] == "fresh", qc)
    check("微信 3 小时后已 expired", wx["state"] == "expired", wx)
    check("hint 里带 login_type", qc.get("login_type") == "QC", qc)


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
    test_ttl_by_login_type()
    test_qc_not_falsely_warned()
    test_age()
    test_hint_states()
    test_error_mapping()
    print()
    print(f"通过 {PASS} / {PASS + FAIL}")
    sys.exit(1 if FAIL else 0)