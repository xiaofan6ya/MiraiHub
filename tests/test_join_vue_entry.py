"""Run the actual join route and gate in isolation, without starting platform workers.

合并后（旧控制台下线）这条回归锁的三件事不变：
  * 注册入口 `/join`、`/join/`、`/static/join.html` 三者同一份 Vue 产物、同一道门禁；
  * 未通过门禁一律 404 且返回体与"页面不存在"一致；
  * 登录壳的私有页（index.html）不能靠公开图片的放行规则被读到。

变化：路由函数从 `ui_v2_asset` 改名为 `ui_file`（`/admin/v2/*` 只剩 301 兼容），
产物路径前缀由 `/admin/v2/` 收为 `/admin/`，常量名同步去掉 `V2`。
"""
import ast
import os
import contextlib
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.testclient import TestClient

server_path = Path(__file__).resolve().parents[1] / 'admin/server.py'
source = server_path.read_text(encoding='utf-8')
tree = ast.parse(source)

class JoinVueRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.page = self.root / 'join.html'
        self.page.write_text('<div id="join-app">Vue entry</div>', encoding='utf-8')
        (self.root / 'logos').mkdir()
        (self.root / 'logos/platform.svg').write_text('<svg/>', encoding='utf-8')
        (self.root / 'favicon.svg').write_text('<svg/>', encoding='utf-8')
        (self.root / 'index.html').write_text('private console shell', encoding='utf-8')
        app = FastAPI()
        app.get("/api/open/platforms")(lambda: {"ok": True})
        fake_gate = types.ModuleType('admin.join_gate')
        fake_gate.COOKIE_NAME = 'join_ticket'
        fake_gate.check_access = lambda db, ticket: ticket == 'valid-ticket'
        # Stub only the gate storage adapter; execute the real middleware and route body.
        self.addCleanup(patch.stopall)
        fake_admin = types.ModuleType('admin')
        fake_admin.join_gate = fake_gate
        patch.dict(sys.modules, {'admin': fake_admin, 'admin.join_gate': fake_gate}).start()
        ns = dict(app=app, os=os, Path=Path, Session=object, Request=Request, Depends=Depends, get_db=lambda: None,
                  SessionLocal=lambda: contextlib.nullcontext(None),
                  UI_DIST=self.root, HTTPException=HTTPException, FileResponse=FileResponse,
                  _console_allowed=lambda request: False,
                  _UI_HTML_HEADERS={"Cache-Control": "no-cache"},
                  _UI_ASSET_HEADERS={"Cache-Control": "public, max-age=31536000, immutable"},
                  JSONResponse=JSONResponse, _ADMIN_CSP="default-src 'self'",
                  _log_ui_missing=lambda: None)
        selected = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in ('_join_gate_middleware', 'join_index', 'ui_file')]
        self.assertEqual(len(selected), 3)
        exec(compile(ast.Module(body=selected, type_ignores=[]), str(server_path), 'exec'), ns)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
    def test_invitation_gate_still_covers_every_entry(self):
        for path in ['/join', '/join/', '/static/join.html']:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 404, path)
            self.assertEqual(response.json(), {'detail': 'Not Found'})
    def test_valid_invitation_serves_same_vue_entry_with_no_cache(self):
        self.client.cookies.set('join_ticket', 'valid-ticket')
        for path in ['/join', '/join/', '/static/join.html']:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertIn('id="join-app"', response.text)
            self.assertIn('no-store', response.headers['cache-control'])
            self.assertEqual(response.headers['content-security-policy'], "default-src 'self'")
    def test_missing_build_does_not_fall_back_to_legacy_html(self):
        self.client.cookies.set('join_ticket', 'valid-ticket')
        self.page.unlink()
        self.assertEqual(self.client.get('/join').status_code, 404)
    def test_public_images_load_without_exposing_console_pages(self):
        for path in ['/admin/favicon.svg', '/admin/logos/platform.svg']:
            self.assertEqual(self.client.get(path).status_code, 200, path)
        for path in ['/admin/index.html', '/admin/logos/missing.svg', '/admin/logos/private.html']:
            self.assertEqual(self.client.get(path).status_code, 404, path)
    def test_public_auth_keeps_the_same_gate(self):
        self.assertEqual(self.client.get('/api/open/platforms').json(), {'detail': 'Not Found'})
        self.client.cookies.set('join_ticket', 'valid-ticket')
        self.assertEqual(self.client.get('/api/open/platforms').json(), {'ok': True})

if __name__ == '__main__':
    unittest.main()
