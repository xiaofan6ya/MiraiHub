# -*- coding: utf-8 -*-
"""新版控制台静态挂载（/admin/v2，S02）的门禁、缓存与穿越防护回归锁定。

这两条路由写错时**不会报错**，后果全是静默的：未登录也能拿到整套后台 JS，
或者发版后用户永远停在白屏。所以这里用临时目录伪造 UI_DIST
（不依赖 npm run build 是否跑过），把几件事钉死：

1. 未登录 /admin/v2 → 303 回旧版 /admin（登录页全站只留一份）；
2. 未登录 /admin/v2/assets/* 与 login.html → 200（登录页依赖），入口与深链仍 303/404；
3. ../ 穿越 → 404，且**不**参与 SPA 回退（否则任意路径都能变 200）；
4. 通过闸门后：入口 HTML 不缓存并带 CSP，assets/ 下的内容哈希文件
   immutable 长缓存，其它文件 no-cache，深链回退到入口 HTML；
5. 构建产物缺失时 404（而不是 500 或者一个空页）；
6. 旧版 /admin 与 /admin/assets/* 行为不变。

「已登录」用 monkeypatch _local_runtime 模拟：它与会话 Cookie 走的是同一道
_console_allowed() 闸门，只是另一条分支；真去建会话行就要连数据库，
与本路由的逻辑无关。
"""
import os
import sys

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import admin.server as server  # noqa: E402

INDEX_HTML = (
    '<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8">'
    '<title>MiraiHub 管理后台</title>'
    '<script type="module" src="/admin/v2/assets/index-abc123.js"></script>'
    '</head><body><div id="app"></div></body></html>'
)


@pytest.fixture()
def dist(tmp_path, monkeypatch):
    """伪造一份 Vite 产物，并把 UI_DIST 指过去。"""
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (root / "login.html").write_text("<!doctype html><title>登录</title>", encoding="utf-8")
    (root / "assets" / "index-abc123.js").write_text("console.log('v2');", encoding="utf-8")
    (root / "assets" / "index-abc123.css").write_text("body{margin:0}", encoding="utf-8")
    monkeypatch.setattr(server, "UI_DIST", root)
    return root


@pytest.fixture()
def client():
    # 不进 with：不触发 startup（建库 / SSL 预热），本组用例不碰数据库。
    return TestClient(server.app, raise_server_exceptions=False)


@pytest.fixture()
def logged_in(monkeypatch):
    """模拟「已登录」：本地运行时分支，闸门仍然是真实的 _console_allowed()。"""
    monkeypatch.setattr(server, "_local_runtime", lambda: True)


def test_anonymous_entry_redirects_to_legacy_console(client, dist):
    """未登录不该看到 404：把请求交给旧版，由旧版发登录壳。"""
    for path in ("/admin/v2", "/admin/v2/"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 303, path
        assert r.headers["location"] == "/admin", path


def test_anonymous_login_assets_are_public(client, dist):
    """登录页引用的 JS/CSS 必须公开，否则 /admin 发出去的登录页会白屏。

    入口 HTML / 深层路径仍要求会话；缺失资源仍然 404。
    """
    (dist / "assets" / "login-abc.css").write_text("/* login */", encoding="utf-8")
    for path in ("/admin/v2/assets/index-abc123.js",
                 "/admin/v2/assets/login-abc.css",
                 "/admin/v2/login.html"):
        assert client.get(path, follow_redirects=False).status_code == 200, path
    assert client.get("/admin/v2/", follow_redirects=False).status_code == 303
    assert client.get("/admin/v2/deep/route", follow_redirects=False).status_code == 404
    assert client.get("/admin/v2/assets/nope-000.js", follow_redirects=False).status_code == 404


def test_traversal_is_404(client, dist, logged_in):
    """登录态下穿越也必须 404 —— 匿名 404 证明不了前缀校验有效。"""
    for path in ("/admin/v2/../server.py",
                 "/admin/v2/%2e%2e/server.py",
                 "/admin/v2/..%2Fserver.py"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 404, path


def test_traversal_guard_is_inside_the_route(dist, logged_in):
    """直接调 handler：确认 404 来自路由内的前缀校验，而不是客户端把 URL 规范化掉了。"""
    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/admin/v2/../server.py",
        "query_string": b"",
        "headers": [(b"host", b"test")],
        "scheme": "http",
        "server": ("test", 80),
        "root_path": "",
    })
    with pytest.raises(HTTPException) as exc:
        server.ui_v2_asset("../server.py", request)
    assert exc.value.status_code == 404


def test_logged_in_entry_serves_index_html(client, dist, logged_in):
    r = client.get("/admin/v2", follow_redirects=False)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert r.text.strip() == INDEX_HTML.strip()
    # 入口 HTML 每次都回源：否则发版后旧页面指向已被删掉的 chunk
    assert r.headers["cache-control"] == "no-store, no-cache, must-revalidate"


def test_entry_carries_full_csp(client, dist, logged_in):
    """Vite 产物的每一项依赖都要在 CSP 里放行（字体 / 图片 / 注入样式）。"""
    csp = client.get("/admin/v2", follow_redirects=False).headers["content-security-policy"]
    for token in ("default-src 'self'",
                  "script-src 'self' 'unsafe-inline'",
                  "style-src 'self' 'unsafe-inline'",
                  "img-src 'self' data: blob:",
                  "font-src 'self' data:",
                  "connect-src 'self'",
                  "object-src 'none'",
                  "frame-ancestors 'none'"):
        assert token in csp, token


def test_asset_cache_split(client, dist, logged_in):
    js = client.get("/admin/v2/assets/index-abc123.js", follow_redirects=False)
    assert js.status_code == 200
    assert js.headers["cache-control"] == "public, max-age=31536000, immutable"
    # 资源响应不该被当成 HTML 文档挂 CSP（与旧版 /admin/assets 同等对待）
    assert "content-security-policy" not in js.headers
    # 非 assets/ 的静态页走 no-cache：改了立刻能拿到
    login = client.get("/admin/v2/login.html", follow_redirects=False)
    assert login.status_code == 200
    assert login.headers["cache-control"] == "no-cache"


def test_spa_fallback_returns_index(client, dist, logged_in):
    r = client.get("/admin/v2/deep/route", follow_redirects=False)
    assert r.status_code == 200
    assert "MiraiHub 管理后台" in r.text
    assert r.headers["cache-control"] == "no-store, no-cache, must-revalidate"


def test_missing_asset_does_not_fall_back(client, dist, logged_in):
    """assets/ 下缺文件是「构建不完整」，不能拿入口 HTML 糊过去。"""
    assert client.get("/admin/v2/assets/gone-999.js",
                      follow_redirects=False).status_code == 404


def test_unbuilt_dist_is_404(client, tmp_path, monkeypatch, logged_in):
    monkeypatch.setattr(server, "UI_DIST", tmp_path / "never-built")
    assert client.get("/admin/v2", follow_redirects=False).status_code == 404


def test_legacy_admin_behaviour_unchanged(client, dist):
    """旧版不受新版挂载影响：登录壳照发，私有资源照旧 404。"""
    anon = client.get("/admin", follow_redirects=False)
    assert anon.status_code == 200
    assert anon.headers["content-type"].startswith("text/html")
    assert client.get("/admin/assets/console-app.js",
                      follow_redirects=False).status_code == 404
def test_console_v2_logged_in_redirects(client, dist, monkeypatch, logged_in):
    """MIRAIHUB_CONSOLE=v2：已登录访问 /admin → 302 到 /admin/v2/。"""
    monkeypatch.setenv("MIRAIHUB_CONSOLE", "v2")
    r = client.get("/admin", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/admin/v2/"


def test_console_v2_anonymous_serves_new_login(client, dist, monkeypatch):
    """未登录且新版已构建 → 发 admin/ui/dist/login.html，不再发旧登录壳。"""
    monkeypatch.setenv("MIRAIHUB_CONSOLE", "v2")
    (dist / "login.html").write_text("<!doctype html><title>v2-login</title>", encoding="utf-8")
    r = client.get("/admin", follow_redirects=False)
    assert r.status_code == 200
    assert "v2-login" in r.text
    assert r.headers["cache-control"] == "no-store, no-cache, must-revalidate"
    assert "content-security-policy" in r.headers


def test_console_ui_v1_forces_legacy(client, dist, monkeypatch, logged_in):
    """?ui=v1 在 v2 模式下也必须回旧版：开关坏了要能一条链接逃生。"""
    monkeypatch.setenv("MIRAIHUB_CONSOLE", "v2")
    r = client.get("/admin?ui=v1", follow_redirects=False)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "/admin/assets/" in r.text


def test_console_v2_without_build_falls_back_to_legacy(client, tmp_path, monkeypatch, logged_in):
    """写对了 v2 但忘了构建/打包漏了 dist：回退旧版，后台不能 404。"""
    monkeypatch.setenv("MIRAIHUB_CONSOLE", "v2")
    monkeypatch.setattr(server, "UI_DIST", tmp_path / "never-built")
    r = client.get("/admin", follow_redirects=False)
    assert r.status_code == 200
    assert "/admin/assets/" in r.text


def test_console_invalid_mode_falls_back_to_v1(client, dist, monkeypatch):
    monkeypatch.setenv("MIRAIHUB_CONSOLE", "v3")
    r = client.get("/admin", follow_redirects=False)
    assert r.status_code == 200
    assert "v2-login" not in r.text

def test_ui_assets_are_gzipped(client, dist, logged_in):
    """v2 静态资源要 gzip：预览/直连 uvicorn 没有 nginx 压缩时，LCP 才不会砸在 1.2MB 明文上。"""
    big = dist / "assets" / "big-0123456789.js"
    big.write_text("console.log('x');\n" * 400, encoding="utf-8")
    r = client.get("/admin/v2/assets/big-0123456789.js",
                   headers={"Accept-Encoding": "gzip"}, follow_redirects=False)
    assert r.status_code == 200
    assert r.headers.get("content-encoding") == "gzip"
    assert "accept-encoding" in r.headers.get("vary", "").lower()


def test_api_stream_paths_are_not_gzipped(client):
    """API/SSE 不能被 UI 的 gzip 中间件碰到。"""
    r = client.get("/api/app/version", headers={"Accept-Encoding": "gzip"}, follow_redirects=False)
    assert r.status_code == 200
    assert "content-encoding" not in r.headers