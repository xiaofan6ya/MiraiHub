#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Marvis 授权兑换的两个坑（2026-11-16 用户实测）。

## 坑一：跟随 302 会把 Set-Cookie 丢掉

上游成功形态是 **302 + Set-Cookie**（token 在 cookie 里，不在 body）。
如果 urllib 跟了重定向，我们拿到的就是落地页那个响应 —— `Set-Cookie`
已经没了，body 是 HTML，于是 access/openid 全空，报一句
「登录接口没有下发 accesstoken / openid」，用户完全不知道发生了什么
（其实凭证已经发下来了，只是被我们扔了）。

线上曾经就是这种状态（client.py 是 10:58 的过渡版本，没有
`_NoRedirect`），表现为「怎么都授权不进来」。

## 坑二：302 的 body 被跳过

原来写 `if status != 302: read()`。理由是「302 没 body」，但上游会把
JSON 错误码夹在 302 里一起回来 —— 不读就只剩空 info，`biz_code=0`，
掉到「没有下发」那个兜底分支，`-107` 这个真实原因被吞掉。

## 坑三（本次用户遇到）：code 一次性

用户贴的 code 用上游直接打，回的是 `-107 RC_NO_AUTH_ERR` —— 这个 code
已经被兑换过了。所以文案必须说「一次性、可能已经成功过」，而不是让用户
以为是系统坏了。

运行：
  python tests/test_marvis_oauth_exchange.py
"""
from __future__ import annotations

import http.server
import io
import os
import socketserver
import sys
import threading
import urllib.error
import urllib.request
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


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/oauth"):
            self.send_response(302)
            self.send_header("Set-Cookie",
                             "accesstoken=mv_LIVE; Path=/; HttpOnly")
            self.send_header("Set-Cookie", "openid=oLIVE; Path=/; HttpOnly")
            self.send_header("Location", "/final")
            self.end_headers()
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html>ok</html>")


def test_redirect_keeps_cookies() -> None:
    print("\n== 302 必须停在原地，否则 Set-Cookie 丢失 ==")
    with socketserver.TCPServer(("127.0.0.1", 0), _Handler) as srv:
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = "http://127.0.0.1:%d/oauth" % port

        # 默认 opener（会跟随）—— 复现线上旧行为
        r = urllib.request.urlopen(url, timeout=10)
        followed_cookies = r.headers.get_all("Set-Cookie") or []
        check("跟随 302 后拿不到 Set-Cookie（这就是误报的根因）",
              not followed_cookies, followed_cookies)
        check("跟随后的 body 是 HTML 而不是 JSON（解析必然失败）",
              b"<html>" in r.read())

        # _NoRedirect —— 正确做法
        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *_a, **_k):
                return None

        opener = urllib.request.build_opener(_NoRedirect)
        try:
            r2 = opener.open(url, timeout=10)
            status2 = r2.status
        except urllib.error.HTTPError as exc:
            r2 = exc
            status2 = exc.code
        cookies2 = r2.headers.get_all("Set-Cookie") or []
        check("不跟随则停在 302", status2 == 302, status2)
        check("不跟随能拿到 Set-Cookie", bool(cookies2), cookies2)
        joined = " ".join(cookies2)
        check("里头有 accesstoken", "accesstoken=mv_LIVE" in joined, joined)
        check("里头有 openid", "openid=oLIVE" in joined, joined)
        srv.shutdown()


def test_302_body_read() -> None:
    print("\n== 302 的 body 也要读（错误码可能夹在里面）==")
    src = (ROOT / "admin" / "platform" / "marvis" / "core" / "client.py"
           ).read_text(encoding="utf-8")
    start = src.index("def exchange_code")
    body = src[start:start + 6000]
    check("不再用 status != 302 跳过读取",
          "getattr(resp, \"status\", 0) != 302" not in body)
    check("统一尝试读取 body", "body_text = resp.read()" in body)


def test_no_redirect_present() -> None:
    print("\n== 兑换请求必须禁用重定向 ==")
    src = (ROOT / "admin" / "platform" / "marvis" / "core" / "client.py"
           ).read_text(encoding="utf-8")
    start = src.index("def exchange_code")
    body = src[start:start + 6000]
    # handler 可能在模块级（共享 opener 的性能优化）也可能在函数内，
    # 关键是 exchange_code **必须用一个不跟随重定向的 opener**。
    check("存在不跟随重定向的 handler",
          "_NoRedirect" in src, "文件里没有 _NoRedirect")
    check("exchange_code 用的是不跟随的 opener",
          "_no_redirect_opener()" in body or "build_opener(_NoRedirect)" in body,
          "exchange_code 里没用到不跟随 opener")
    check("没有退回默认 opener（会跟随）",
          "urllib.request.urlopen(req" not in body,
          "用了裸 urlopen，会跟随重定向")


def test_error_messages() -> None:
    print("\n== 错误文案要说清「一次性」 ==")
    src = (ROOT / "admin" / "platform" / "marvis" / "core" / "client.py"
           ).read_text(encoding="utf-8")
    check("-107 文案提到一次性", "一次性" in src)
    check("-107 文案提到可能已成功", "已经成功过" in src or "已入库" in src)
    check("兜底文案带出上游原文", "仍然不行请把下面这段反馈" in src)
    check("-107 有独立分支", 'code="-107"' in src)
    check("-109 有独立分支", 'code="-109"' in src)


def test_clean_detached_class() -> None:
    print("\n== _NoRedirect 不能依赖模块级状态 ==")
    src = (ROOT / "admin" / "platform" / "marvis" / "core" / "client.py"
           ).read_text(encoding="utf-8")
    check("在函数内定义（避免全局 opener 被别处复用）",
          "class _NoRedirect" in src)


if __name__ == "__main__":
    test_redirect_keeps_cookies()
    test_302_body_read()
    test_no_redirect_present()
    test_error_messages()
    test_clean_detached_class()
    print()
    print(f"通过 {PASS} / {PASS + FAIL}")
    sys.exit(1 if FAIL else 0)
