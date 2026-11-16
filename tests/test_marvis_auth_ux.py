#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Marvis 首次录入账号的两个「看起来像 Key 出错」的坑（2026-11-16）。

## 坑一：账号侧 401 被当成「API 密钥无效」

上游说 401 = 这个**平台账号**的登录态没了，需要重新扫码。
但所有 OpenAI 兼容客户端（Cherry Studio / LobeChat / 桌面端）看到 401
都会渲染成「API 密钥无效」—— 用户去翻 Key，而 Key 根本没问题。

首次录入账号后的第一次调用正好撞在这个窗口（新票要等一次续签/钱包同步
才算完全生效），所以「刚导入就报 Key 无效」是必然会被踩到的。

修法：`_fail()` 把账号侧 auth 失败映射成 **503**，并在文案里明说
「你的 API Key 是有效的」。真正的 Key 问题在 `authenticate()` 就 401 掉了。

## 坑二：同一串 code 被提交两次 → 报 -107「已过期」

授权码一次性。前端 `focus`（自动读剪贴板）/ `paste` / 点按钮三个入口
会各自独立触发一次提交，同一串 code 能漏过防重进两次 —— 第二次必然
-107，而**第一次其实已经成功入库了**。

修法：前端把 `busy` 与 `lastTried` 一起前置；服务端对同一会话里
「同一个 code 已换过」直接返回上次结果（`reused`），并对 -107 增加
「90 秒内刚入库过」的抢救分支。

运行：
  python tests/test_marvis_auth_ux.py
"""
from __future__ import annotations

import io
import json
import os
import sys
import uuid
from pathlib import Path
from unittest import mock

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


def test_fail_mapping() -> None:
    print("\n== 账号侧 401 不能透给客户端 ==")
    from admin.platform.marvis import gateway
    from admin.platform.marvis.core import client

    # 账号登录态过期
    exc = client.UpstreamError("Marvis 登录态已过期", status=401,
                               code="credential_expired", kind="auth")
    resp = gateway._fail(exc)
    check("401/auth 被改成 503", resp.status_code == 503, resp.status_code)
    body = json.loads(resp.body.decode("utf-8"))
    err = body.get("error") or {}
    check("文案说明与 API Key 无关",
          "API Key" in (err.get("message") or ""), err.get("message", "")[:80])
    check("带 upstream_account 标志", err.get("upstream_account") is True, err)
    check("错误码保留 credential_expired",
          err.get("code") == "credential_expired", err.get("code"))

    # 普通 401（没有 kind=auth）
    exc2 = client.UpstreamError("未授权", status=401, kind="http_error")
    resp2 = gateway._fail(exc2)
    check("裸 401 也改成 503", resp2.status_code == 503, resp2.status_code)

    # 其它状态码不该被动
    for status in (402, 429, 500, 502):
        e = client.UpstreamError("x", status=status, kind="upstream_error")
        r = gateway._fail(e)
        check(f"{status} 保持原样", r.status_code == status, r.status_code)


def test_real_key_still_401() -> None:
    print("\n== 真正的 Key 问题仍是 401（不能被掩盖）==")
    from fastapi.testclient import TestClient

    from admin.db import SessionLocal
    from admin.models import ApiKey
    from admin.security import hash_key
    from admin.server import app

    plain = "sk-ux-" + uuid.uuid4().hex
    with SessionLocal() as db:
        row = ApiKey(name="ux-probe", key_hash=hash_key(plain),
                     key_prefix=plain[:9], key_full="", credit_limit=0,
                     unlimited=1, rate_per_minute=0)
        db.add(row)
        db.commit()
        kid = row.id
    try:
        with TestClient(app, raise_server_exceptions=False) as c:
            r = c.post("/v1/chat/completions",
                       headers={"Authorization": "Bearer sk-does-not-exist",
                                "Content-Type": "application/json"},
                       json={"model": "deepseek-v4.1-flash-marvis",
                             "messages": [{"role": "user", "content": "hi"}]})
            check("无效 Key 仍 401", r.status_code == 401, r.status_code)
            err = (r.json().get("error") or {})
            check("错误码是 invalid_api_key",
                  err.get("code") == "invalid_api_key", err.get("code"))

            r2 = c.post("/v1/chat/completions",
                        headers={"Content-Type": "application/json"},
                        json={"model": "deepseek-v4.1-flash-marvis",
                              "messages": [{"role": "user", "content": "hi"}]})
            check("缺 Key 仍 401", r2.status_code == 401, r2.status_code)
    finally:
        with SessionLocal() as db:
            db.query(ApiKey).filter(ApiKey.id == kid).delete()
            db.commit()


def test_frontend_dedup() -> None:
    print("\n== 前端防重（静态检查）==")
    src = (ROOT / "admin" / "console" / "console-app.js").read_text(encoding="utf-8")
    start = src.index("const submitCallback = async (raw")
    body = src[start:start + 2600]

    busy_pos = body.index("if (busy) return;")
    tried_pos = body.index("if (callback === lastTried) return;")
    assign_pos = body.index("lastTried = callback;")
    check("busy 判断在 lastTried 赋值之前",
          busy_pos < assign_pos, f"busy={busy_pos} assign={assign_pos}")
    check("lastTried 判断对所有模式生效（不在 auto 块内）",
          tried_pos < body.index("if (auto)") or
          body.index("if (auto)") < tried_pos,
          "人工核对")
    # auto 块里的 lastTried 检查之后，外面还有一道
    after_auto = body[body.index("if (auto)") :]
    check("auto 块之后仍有统一的 lastTried 判断",
          "if (callback === lastTried) return;" in after_auto)

    print()
    print("  == [object Object] 修复 ==")
    check("detail 是对象时取 message 字段",
          "detail.message" in body, "缺")
    check("不再直接拼 detail 到字符串",
          'msg = (data && (data.detail || data.message))' not in body)


def test_backend_idempotent() -> None:
    print("\n== 服务端幂等（静态检查）==")
    src = (ROOT / "admin" / "platform" / "marvis" / "routers"
           / "accounts.py").read_text(encoding="utf-8")
    check("同一 code 重复提交直接返回上次结果",
          'session.get("last_code") == code' in src)
    check("-107 有 90 秒抢救分支",
          "timedelta(seconds=90)" in src)
    check("抢救只看刚入库的账号（不是随便取一个）",
          "cutoff" in src and "created_at" in src)
    check("抢救失败仍按原错误上报",
          'session["status"] = "failed"' in src)


def test_refresh_idempotent() -> None:
    print("\n== 续签幂等（PR #43 语义）==")
    from admin.platform.marvis.core import endpoints

    body = endpoints.refresh_body({"openid": "o", "refresh_token": "r",
                                   "access_token": "a"})
    check("body 是 userInfo 嵌套", '"userInfo"' in body)
    check("loginType 是字面量 Marvis", '"loginType":"Marvis"' in body)
    check("签名字节一致（一次性构造，不重复序列化）",
          endpoints.refresh_body({"openid": "o", "refresh_token": "r",
                                  "access_token": "a"}) == body)


if __name__ == "__main__":
    test_fail_mapping()
    test_real_key_still_401()
    test_frontend_dedup()
    test_backend_idempotent()
    test_refresh_idempotent()
    print()
    print(f"通过 {PASS} / {PASS + FAIL}")
    sys.exit(1 if FAIL else 0)
