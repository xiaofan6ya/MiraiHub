# -*- coding: utf-8 -*-
"""后台前端静态挂载（`/admin`，合并后唯一入口）的门禁、缓存与穿越防护回归锁定。

历史：本文件原名 `test_ui_v2_serving.py`，锁的是「v1(console) / v2(ui)
双轨 + `?ui=v1` 逃生 + `MIRAIHUB_CONSOLE` 开关」那一套。合并后只剩新版
（`admin/ui/dist`，Vite 产物），旧控制台 `admin/console` 不再被任何路由服务。

这些路由写错时**不会报错**，后果全是静默的：未登录也能拿到整套后台 JS，
或者发版后用户永远停在白屏。所以这里用临时目录伪造 UI_DIST
（不依赖 pnpm build 是否跑过），把几件事钉死：

1. 未登录 `/admin` → 发同一份构建里的 `login.html`（200，带 CSP、不缓存）；
2. 已登录 `/admin` → 发 `index.html`；
3. `/admin/assets/*` **公开可读**（登录页要加载自己的 JS/CSS），且 immutable 长缓存；
4. `../` 穿越 → 404，且**不**参与 SPA 回退（否则任意路径都能变 200）；
5. 深链：已登录回退入口页，未登录 404（不暴露"这个文件存在只是你没权限"）；
6. 构建产物缺失时 404（而不是 500 或者一个空页）；
7. 旧的两套开关彻底失效：`?ui=v1` 与 `MIRAIHUB_CONSOLE` 不再改变任何东西；
8. `/admin/v2/*` 只保留 301 兼容跳转；
9. 旧控制台资源（`/admin/assets/console-app.js`）不再可达。

「已登录」用 monkeypatch `_local_runtime` 模拟：它与会话 Cookie 走的是同一道
`_console_allowed()` 闸门，只是另一条分支；真去建会话行就要连数据库，
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
    '<script type="module" src="/admin/assets/index-abc123.js"></script>'
    '</head><body><div id="app"></div></body></html>'
)


@pytest.fixture()
def dist(tmp_path, monkeypatch):
    """伪造一份 Vite 产物，并把 UI_DIST 指过去。"""
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (root / "login.html").write_text("<!doctype html><title>登录</title>", encoding="utf-8")
    (root / "join.html").write_text("<!doctype html><title>注册</title>", encoding="utf-8")
    (root / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (root / "assets" / "index-abc123.js").write_text("console.log('ui');", encoding="utf-8")
    (root / "assets" / "index-abc123.css").write_text("body{margin:0}", encoding="utf-8")
    monkeypatch.setattr(server, "UI_DIST", root)
    return root


@pytest.fixture()
def client():
    # 上游的 LifecycleMiddleware 在未发布就绪时对业务路径回 503；这里把进程内
    # 生命周期显式标成 ready，让这些静态路由回归只测路由本身。
    from admin.lifecycle import get_lifecycle
    state = get_lifecycle()
    state.reset()
    state.mark_ready()
    # 不进 with：不触发 startup（建库 / SSL 预热），本组用例不碰数据库。
    return TestClient(server.app, raise_server_exceptions=False)


@pytest.fixture()
def logged_in(monkeypatch):
    """模拟「已登录」：本地运行时分支，闸门仍然是真实的 `_console_allowed()`。"""
    monkeypatch.setattr(server, "_local_runtime", lambda: True)


def test_anonymous_entry_serves_login_page(client, dist):
    """未登录 `/admin` 发登录页 —— 不再 303 到别处，登录页只有这一份。"""
    r = client.get("/admin", follow_redirects=False)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "登录" in r.text
    assert r.headers["cache-control"] == "no-store, no-cache, must-revalidate"
    assert "content-security-policy" in r.headers
    # `/admin/` 与 `/admin` 等价，别让尾斜杠把登录页打成 404。
    assert client.get("/admin/", follow_redirects=False).status_code == 200


def test_logged_in_entry_serves_index_html(client, dist, logged_in):
    r = client.get("/admin", follow_redirects=False)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert r.text.strip() == INDEX_HTML.strip()
    # 入口 HTML 每次都回源：否则发版后旧页面指向已被删掉的 chunk
    assert r.headers["cache-control"] == "no-store, no-cache, must-revalidate"


def test_login_page_assets_are_public(client, dist):
    """登录页引用的 JS/CSS 必须公开，否则未登录访客拿到的是白屏 + 一串 404。"""
    (dist / "assets" / "login-abc.css").write_text("/* login */", encoding="utf-8")
    for path in ("/admin/assets/index-abc123.js",
                 "/admin/assets/login-abc.css",
                 "/admin/login.html"):
        assert client.get(path, follow_redirects=False).status_code == 200, path
    # 图标与平台 logo 也是公开入口的一部分（登录页 / 注册页共用）。
    assert client.get("/admin/favicon.svg", follow_redirects=False).status_code == 200
    assert client.get("/admin/join.html", follow_redirects=False).status_code == 200


def test_public_images_under_logos(client, dist):
    logos = dist / "logos"
    logos.mkdir()
    (logos / "trae.svg").write_text("<svg/>", encoding="utf-8")
    assert client.get("/admin/logos/trae.svg", follow_redirects=False).status_code == 200
    # 非图片后缀不放行：logos/ 下不该出现可执行内容。
    (logos / "evil.js").write_text("alert(1)", encoding="utf-8")
    assert client.get("/admin/logos/evil.js", follow_redirects=False).status_code == 404


def test_anonymous_deep_route_is_404(client, dist):
    """未登录访问深链 / 入口页本身 → 404，不告诉探测者文件是否存在。"""
    for path in ("/admin/index.html", "/admin/deep/route"):
        assert client.get(path, follow_redirects=False).status_code == 404, path
    assert client.get("/admin/assets/nope-000.js", follow_redirects=False).status_code == 404


def test_traversal_is_404(client, dist, logged_in):
    """登录态下穿越也必须 404 —— 匿名 404 证明不了前缀校验有效。"""
    for path in ("/admin/../server.py",
                 "/admin/%2e%2e/server.py",
                 "/admin/..%2Fserver.py"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 404, path


def test_traversal_guard_is_inside_the_route(dist, logged_in):
    """直接调 handler：确认 404 来自路由内的前缀校验，而不是客户端把 URL 规范化掉了。"""
    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/admin/../server.py",
        "query_string": b"",
        "headers": [(b"host", b"test")],
        "scheme": "http",
        "server": ("test", 80),
        "root_path": "",
    })
    with pytest.raises(HTTPException) as exc:
        server.ui_file("../server.py", request)
    assert exc.value.status_code == 404


def test_asset_cache_split(client, dist, logged_in):
    js = client.get("/admin/assets/index-abc123.js", follow_redirects=False)
    assert js.status_code == 200
    assert js.headers["cache-control"] == "public, max-age=31536000, immutable"
    # 资源响应不该被当成 HTML 文档挂 CSP
    assert "content-security-policy" not in js.headers
    # 非 assets/ 的静态页走 no-cache：改了立刻能拿到
    login = client.get("/admin/login.html", follow_redirects=False)
    assert login.status_code == 200
    assert login.headers["cache-control"] == "no-cache"


def test_entry_carries_full_csp(client, dist, logged_in):
    """Vite 产物的每一项依赖都要在 CSP 里放行（字体 / 图片 / 注入样式）。"""
    csp = client.get("/admin", follow_redirects=False).headers["content-security-policy"]
    for token in ("default-src 'self'",
                  "script-src 'self' 'unsafe-inline'",
                  "style-src 'self' 'unsafe-inline'",
                  "img-src 'self' data: blob:",
                  "font-src 'self' data:",
                  "connect-src 'self'",
                  "object-src 'none'",
                  "frame-ancestors 'none'"):
        assert token in csp, token


def test_spa_fallback_returns_index(client, dist, logged_in):
    r = client.get("/admin/deep/route", follow_redirects=False)
    assert r.status_code == 200
    assert "MiraiHub 管理后台" in r.text
    assert r.headers["cache-control"] == "no-store, no-cache, must-revalidate"


def test_missing_asset_does_not_fall_back(client, dist, logged_in):
    """assets/ 下缺文件是「构建不完整」，不能拿入口 HTML 糊过去。"""
    assert client.get("/admin/assets/gone-999.js",
                      follow_redirects=False).status_code == 404


def test_unbuilt_dist_is_404(client, tmp_path, monkeypatch, logged_in):
    monkeypatch.setattr(server, "UI_DIST", tmp_path / "never-built")
    assert client.get("/admin", follow_redirects=False).status_code == 404


def test_unbuilt_dist_anonymous_is_404(client, tmp_path, monkeypatch):
    """没构建时匿名也不该拿到 200 + 空页：直说 404 才好排查。"""
    monkeypatch.setattr(server, "UI_DIST", tmp_path / "never-built")
    assert client.get("/admin", follow_redirects=False).status_code == 404


def test_legacy_console_assets_are_gone(client, dist, logged_in):
    """旧控制台已下线：`admin/console` 的任何资源都不再可达。"""
    for path in ("/admin/assets/console-app.js",
                 "/admin/assets/console.css",
                 "/admin/assets/index.html"):
        assert client.get(path, follow_redirects=False).status_code == 404, path


def test_v1_switch_is_gone(client, dist, logged_in, monkeypatch):
    """`?ui=v1` 与 `MIRAIHUB_CONSOLE` 都不再改变行为 —— 合并后没有回退开关。"""
    for query in ("?ui=v1", "?ui=v2", ""):
        r = client.get("/admin" + query, follow_redirects=False)
        assert r.status_code == 200, query
        assert r.text.strip() == INDEX_HTML.strip(), query
    monkeypatch.setenv("MIRAIHUB_CONSOLE", "v1")
    assert client.get("/admin", follow_redirects=False).text.strip() == INDEX_HTML.strip()
    monkeypatch.setenv("MIRAIHUB_CONSOLE", "v2")
    assert client.get("/admin", follow_redirects=False).text.strip() == INDEX_HTML.strip()


def test_admin_v2_redirects_to_admin(client, dist, logged_in):
    """老书签 `/admin/v2/...` 走 301 兼容跳转，别 404。"""
    r = client.get("/admin/v2", follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == "/admin/"
    r = client.get("/admin/v2/assets/index-abc123.js", follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == "/admin/assets/index-abc123.js"
    # 带查询串的旧链接（`?tab=keys`）也要带过去
    r = client.get("/admin/v2/?tab=keys", follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == "/admin/?tab=keys"


def test_ui_assets_are_gzipped(client, dist, logged_in):
    """静态资源要 gzip：预览/直连 uvicorn 没有 nginx 压缩时，LCP 才不会砸在明文上。"""
    big = dist / "assets" / "big-0123456789.js"
    big.write_text("console.log('x');\n" * 400, encoding="utf-8")
    r = client.get("/admin/assets/big-0123456789.js",
                   headers={"Accept-Encoding": "gzip"}, follow_redirects=False)
    assert r.status_code == 200
    assert r.headers.get("content-encoding") == "gzip"
    assert "accept-encoding" in r.headers.get("vary", "").lower()


def test_api_stream_paths_are_not_gzipped(client):
    """API/SSE 不能被 UI 的 gzip 中间件碰到。"""
    r = client.get("/api/app/version", headers={"Accept-Encoding": "gzip"}, follow_redirects=False)
    assert r.status_code == 200
    assert "content-encoding" not in r.headers
