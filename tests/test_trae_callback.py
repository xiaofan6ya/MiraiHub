"""Local regression tests; upstream exchange is mocked, SQLite is temporary."""
import json
import os
import tempfile
import time
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from urllib.parse import urlencode
import httpx

_temp = tempfile.TemporaryDirectory(prefix="miraihub-callback-")
os.environ["ADMIN_DATABASE_URL"] = "sqlite:///" + _temp.name + "/test.db"
os.environ["ADMIN_USERNAME"] = "callback-test"
os.environ["ADMIN_JWT_SECRET"] = "callback-test-secret-for-local-tests-only"

from fastapi import FastAPI
from fastapi.testclient import TestClient
from admin.db import Base, SessionLocal, engine
from admin.models import Account
from admin.platform.trae.core import oauth
from admin.platform.trae.routers import accounts
from admin.security import create_admin_token

app = FastAPI()
app.include_router(accounts.router)
app.add_api_route("/authorize", accounts.device_callback, methods=["GET"])
Base.metadata.create_all(engine)


class CallbackTests(unittest.TestCase):
    def setUp(self):
        oauth._SESSIONS.clear()
        with SessionLocal() as db:
            db.query(Account).delete()
            db.commit()
        self.client = TestClient(app)
        self.headers = {"X-Admin-Token": create_admin_token()}

    def tearDown(self):
        self.client.close()

    def start(self, realm="cn"):
        response = self.client.post("/api/trae/device/start", params={"realm": realm},
                                    headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def submit(self, nonce, params=None, url=None, headers=None):
        url = url or "http://127.0.0.1:8790/authorize?" + urlencode(params or {})
        return self.client.post("/api/trae/device/callback",
                                headers=self.headers if headers is None else headers,
                                json={"nonce": nonce, "callback_url": url})

    def poll(self, nonce):
        response = self.client.get('/api/trae/device/poll', params={'nonce': nonce}, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def complete(self, nonce):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = self.poll(nonce)
            if not result.get('pending'):
                return result
            time.sleep(.01)
        self.fail('Background OAuth task did not complete')

    def test_manual_trae_callback_imports_account_through_poll(self):
        start = self.start()
        nonce = start["nonce"]
        params = {
            "authCodeInfo": json.dumps({"AuthCode": "code+with/slash=&中文"}),
            "userInfo": json.dumps({"UserID": "user-1", "ScreenName": "回调用户", "TenantID": "t1"}),
            "loginTraceID": nonce, "host": "https://api.trae.com.cn", "userRegion": "cn",
        }
        with patch.object(oauth, "exchange_token", return_value={
                "Token": "test-access", "RefreshToken": "test-refresh"}) as exchange:
            response = self.submit(nonce, params)
            self.assertEqual(response.status_code, 200, response.text)
            exchange.assert_not_called()
            self.assertTrue(self.complete(nonce)["ok"])
            self.assertEqual(exchange.call_args.args[0], "code+with/slash=&中文")
            self.assertEqual(exchange.call_args.args[1], oauth.get(nonce).verifier)
            self.assertEqual(exchange.call_args.kwargs["host"], "https://api.trae.com.cn")
        with SessionLocal() as db:
            row = db.query(Account).one()
            self.assertEqual((row.platform, row.domain, row.uid, row.name),
                             ("trae", "cn", "user-1", "回调用户"))
            self.assertEqual(json.loads(row.auth_json)["refresh_token"], "test-refresh")

    def test_standard_code_without_state_uses_explicit_session(self):
        older = self.start("intl")["nonce"]
        newer = self.start()["nonce"]
        self.assertEqual(self.submit(older, {"code": "international-code"}).status_code, 200)
        self.assertEqual(oauth.get(older).wait()["realm"], "intl")
        self.assertIsNone(oauth.get(newer).wait())

    def test_admin_is_required(self):
        nonce = self.start()["nonce"]
        self.assertEqual(self.submit(nonce, {"code": "code"}, headers={}).status_code, 401)
        self.assertIsNone(oauth.get(nonce).wait())

    def test_mismatched_and_expired_sessions_are_rejected(self):
        nonce = self.start()["nonce"]
        for key in ("nonce", "login_trace_id", "loginTraceID", "state"):
            with self.subTest(key=key):
                self.assertEqual(self.submit(nonce, {"code": "code", key: "other"}).status_code, 400)
                self.assertIsNone(oauth.get(nonce).wait())
        oauth.get(nonce).created_at = int(time.time()) - 601
        self.assertEqual(self.submit(nonce, {"code": "code"}).status_code, 404)
        self.assertEqual(self.submit("unknown", {"code": "code"}).status_code, 404)

    def test_malformed_links_do_not_consume_session_and_can_retry(self):
        start = self.start()
        nonce = start["nonce"]
        urls = ["not-a-url", "javascript:alert(1)", "http://[bad", start["auth_url"],
                "http://localhost/authorize?code=one&code=two"]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.submit(nonce, url=url).status_code, 400)
                self.assertIsNone(oauth.get(nonce).wait())
        for params in ({"authCodeInfo": "{"}, {"authCodeInfo": "[]"},
                       {"authCodeInfo": '{"AuthCode":{}}'},
                       {"code": "code", "userInfo": "[]"}):
            self.assertEqual(self.submit(nonce, params).status_code, 400)
            self.assertIsNone(oauth.get(nonce).wait())
        self.assertEqual(self.submit(nonce, {"authCodeInfo": '{"authCode":"valid"}'}).status_code, 200)

    def test_callback_host_cannot_redirect_exchange_to_arbitrary_server(self):
        nonce = self.start()["nonce"]
        for host in ("http://127.0.0.1", "https://trae.ai.attacker.example",
                     "https://api.trae.ai@attacker.example", "https://api.trae.ai/path",
                     "https://api.trae.ai:bad", "https://api.trae.ai?override=1"):
            self.assertEqual(self.submit(nonce, {"code": "code", "host": host}).status_code, 400)
            self.assertIsNone(oauth.get(nonce).wait())

    def test_manual_denial_reaches_poll(self):
        nonce = self.start()["nonce"]
        self.assertEqual(self.submit(nonce, {"error": "access_denied", "error_description": "用户取消"}).status_code, 200)
        poll = self.client.get("/api/trae/device/poll", params={"nonce": nonce}, headers=self.headers)
        self.assertEqual(poll.json(), {"ok": False, "error": "用户取消"})

    def test_automatic_and_manual_duplicate_does_not_overwrite(self):
        nonce = self.start()["nonce"]
        callback = self.client.get("/authorize", params={"code": "code", "state": nonce})
        self.assertIn("授权完成", callback.text)
        self.assertEqual(self.submit(nonce, {"code": "code"}).status_code, 200)
        self.assertEqual(self.submit(nonce, {"code": "replacement"}).status_code, 409)
        self.assertEqual(oauth.get(nonce).code, "code")

    def test_automatic_trae_callback_still_parses_user_info(self):
        nonce = self.start()["nonce"]
        callback = self.client.get("/authorize", params={
            "authCodeInfo": '{"AuthCode":"auto-code"}', "loginTraceID": nonce,
            "userInfo": '{"UserID":"auto-user"}', "host": "https://api.trae.com.cn"})
        self.assertIn("授权完成", callback.text)
        result = oauth.get(nonce).wait()
        self.assertEqual(result["code"], "auto-code")
        self.assertEqual(result["user_info"]["UserID"], "auto-user")

    def test_automatic_unknown_session_does_not_capture_latest(self):
        nonce = self.start()["nonce"]
        result = self.client.get("/authorize", params={"code": "stale", "state": "expired"})
        self.assertIn("会话已失效", result.text)
        self.assertIsNone(oauth.get(nonce).wait())

    def test_automatic_callback_without_identifier_still_works(self):
        nonce = self.start()["nonce"]
        self.client.get("/authorize", params={"code": "legacy-code"})
        self.assertEqual(oauth.get(nonce).code, "legacy-code")

    def test_callback_error_html_is_escaped(self):
        nonce = self.start()["nonce"]
        response = self.client.get("/authorize", params={
            "state": nonce, "error": "denied", "error_description": "<script>alert(1)</script>"})
        self.assertNotIn("<script>", response.text)
        self.assertIn("&lt;script&gt;", response.text)

    def test_concurrent_delivery_accepts_only_one_code(self):
        session = oauth.get(self.start()["nonce"])
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda i: session.deliver(code=str(i)), range(20)))
        self.assertEqual(sum(results), 1)
        self.assertTrue(session.wait()["code"])

    def test_slow_exchange_does_not_block_polls_or_repeat_exchange(self):
        nonce = self.start()['nonce']
        self.submit(nonce, {'code': 'slow-test-code'})
        started, release = threading.Event(), threading.Event()
        def slow_exchange(*args, **kwargs):
            started.set()
            if not release.wait(5):
                raise RuntimeError('Test did not release exchange')
            return {'Token': 'access', 'RefreshToken': 'refresh', 'UserId': 'slow-user'}
        with patch.object(oauth, 'exchange_token', side_effect=slow_exchange) as exchange:
            try:
                before = time.monotonic()
                result = self.poll(nonce)
                self.assertTrue(result['pending'])
                self.assertEqual(result['stage'], 'exchanging')
                self.assertLess(time.monotonic() - before, 1)
                self.assertTrue(started.wait(1))
                with ThreadPoolExecutor(max_workers=6) as pool:
                    results = list(pool.map(lambda _: self.poll(nonce), range(12)))
                self.assertTrue(all(r.get('pending') for r in results))
                exchange.assert_called_once()
            finally:
                release.set()
            final = self.complete(nonce)
            self.assertTrue(final['ok'])
            self.assertEqual(self.poll(nonce), final)
            self.assertEqual(self.poll(nonce), final)
            exchange.assert_called_once()
        with SessionLocal() as db:
            self.assertEqual(db.query(Account).count(), 1)

    def test_failure_is_cached_and_credentials_are_not_logged(self):
        nonce = self.start()['nonce']
        self.submit(nonce, {'code': 'sensitive-auth-code'})
        with patch.object(oauth, 'exchange_token', side_effect=RuntimeError('连接 Trae 授权服务失败或超时，请稍后重新发起授权')) as exchange:
            with self.assertLogs('admin.platform.trae.routers', level='WARNING') as logs:
                result = self.complete(nonce)
            self.assertFalse(result['ok'])
            self.assertIn('超时', result['error'])
            self.assertEqual(self.poll(nonce), result)
            exchange.assert_called_once()
            self.assertNotIn('sensitive-auth-code', '\n'.join(logs.output))
            self.assertNotIn(oauth.get(nonce).verifier, '\n'.join(logs.output))

    def test_database_failure_finishes_background_task(self):
        nonce = self.start()['nonce']
        self.submit(nonce, {'code': 'code'})
        with patch.object(oauth, 'exchange_token', return_value={'Token': 'access'}), \
             patch.object(accounts, '_finish_trae_account', side_effect=RuntimeError('private DB details')):
            result = self.complete(nonce)
        self.assertFalse(result['ok'])
        self.assertIn('保存失败', result['error'])
        self.assertNotIn('private', result['error'])


class TokenTransportTests(unittest.TestCase):
    def test_connect_timeout_retries_before_sending_code(self):
        response = httpx.Response(200, json={'Result': {'Token': 'access'}})
        with patch('httpx.Client') as client:
            post = client.return_value.__enter__.return_value.post
            post.side_effect = [httpx.ConnectTimeout('TLS handshake timed out'), response]
            result = oauth.exchange_token('auth-code', 'verifier')
            self.assertEqual(result, {'Token': 'access'})
            self.assertEqual(post.call_count, 2)
            self.assertEqual(post.call_args.kwargs['json']['AuthCode'], 'auth-code')
            self.assertEqual(post.call_args.kwargs['json']['CodeVerifier'], 'verifier')
            self.assertEqual(client.call_args.kwargs['timeout'].connect, 10)

    def test_connect_failures_have_bounded_retries(self):
        with patch('httpx.Client') as client:
            post = client.return_value.__enter__.return_value.post
            post.side_effect = httpx.ConnectTimeout('handshake')
            with self.assertRaisesRegex(RuntimeError, '连接 Trae 授权服务失败或超时'):
                oauth.exchange_token('auth-code', 'verifier')
            self.assertEqual(post.call_count, 2)

    def test_read_timeout_does_not_replay_authorization_code(self):
        with patch('httpx.Client') as client:
            post = client.return_value.__enter__.return_value.post
            post.side_effect = httpx.ReadTimeout('response timed out')
            with self.assertRaisesRegex(RuntimeError, '响应超时'):
                oauth.exchange_token('auth-code', 'verifier')
            post.assert_called_once()

    def test_gateway_html_is_not_returned_or_retried(self):
        with patch('httpx.Client') as client:
            post = client.return_value.__enter__.return_value.post
            post.return_value = httpx.Response(502, text='<html>Cloudflare error</html>')
            with self.assertRaisesRegex(RuntimeError, '^Trae 授权服务返回 HTTP 502，请重新发起授权$'):
                oauth.exchange_token('auth-code', 'verifier')
            post.assert_called_once()

    def test_non_json_success_is_reported_cleanly(self):
        with patch('httpx.Client') as client:
            client.return_value.__enter__.return_value.post.return_value = httpx.Response(200, text='<html>error</html>')
            with self.assertRaisesRegex(RuntimeError, '无效响应'):
                oauth.exchange_token('auth-code', 'verifier')


if __name__ == "__main__":
    unittest.main()
