"""公共回调契约：独立协议适配器、跨平台/区域隔离、Trae 兼容。"""
import json
import sys
import time
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode
from unittest.mock import patch

from test_trae_callback import app, TestClient, SessionLocal, Account, create_admin_token, oauth
from admin.platform import registry
from admin.platform.auth import CallbackSession
from admin.platform.loader import load_platforms
from admin.platform.router import router, _serialize

app.include_router(router)
load_platforms()


class PlatformCallbackTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.headers = {'X-Admin-Token': create_admin_token()}
        self.sessions = {'alpha': {}, 'beta': {}}
        self.modules = {}
        self.specs = []
        for platform in self.sessions:
            adapter = types.ModuleType('_callback_test_' + platform)
            adapter.get_session = self.sessions[platform].get
            # 与 Trae 不同的回调参数；公共入口不应理解这些字段。
            def parse(params):
                if params.get('error'):
                    return {'error': params['error']}
                if not params.get('ticket'):
                    raise ValueError('缺少 ticket')
                return {'authorization_code': params['ticket']}
            adapter.parse_callback = parse
            adapter.session_keys = ('request_id', 'state', 'nonce')
            self.modules[platform] = adapter
            sys.modules[adapter.__name__] = adapter
            for realm in ('east', 'west'):
                spec = registry.PlatformSpec(id=platform + '-' + realm, label='测试平台',
                    storage_platform=platform, realm=realm, auth_callback=adapter.__name__)
                registry.register(spec)
                self.specs.append(spec)

    def tearDown(self):
        self.client.close()
        for spec in self.specs:
            registry._REGISTRY.pop(spec.id, None)
        for module in self.modules.values():
            sys.modules.pop(module.__name__, None)

    def session(self, platform='alpha', realm='east'):
        session = CallbackSession(platform, realm)
        self.sessions[platform][session.nonce] = session
        return session

    def submit(self, session, platform_id=None, params=None, headers=None, **body):
        return self.client.post('/api/platforms/' + (platform_id or session.platform + '-' + session.realm) + '/auth/callback',
            headers=self.headers if headers is None else headers,
            json={'nonce': session.nonce, 'callback_url': 'http://localhost/return?' + urlencode(
                params if params is not None else {'ticket': 'test-ticket', 'request_id': session.nonce}), **body})

    def test_metadata_declares_capability_without_exposing_adapter(self):
        for pid in ('alpha-east', 'beta-west', 'trae-cn', 'trae-intl'):
            data = _serialize(registry.get(pid))
            self.assertTrue(data['auth']['manual_callback'])
            self.assertNotIn('admin.platform.', json.dumps(data['auth']))
        for pid in ('qoder', 'qoder-intl', 'loomy', 'workbuddy'):
            self.assertTrue(_serialize(registry.get(pid))['auth']['manual_callback'])

    def test_auth_unknown_and_unsupported_platforms(self):
        session = self.session()
        self.assertEqual(self.submit(session, headers={}).status_code, 401)
        self.assertEqual(self.submit(session, 'does-not-exist').status_code, 404)
        self.assertEqual(self.submit(session, 'qoder').status_code, 404)
        self.assertIsNone(session.wait())

    def test_platform_and_region_isolation(self):
        session = self.session()
        self.assertEqual(self.submit(session, 'beta-east').status_code, 404)
        self.assertEqual(self.submit(session, 'alpha-west').status_code, 404)
        self.assertEqual(self.submit(session, realm='west').status_code, 400)
        self.assertEqual(self.submit(session, realm='unknown').status_code, 400)
        self.assertIsNone(session.wait())
        self.assertEqual(self.submit(session).status_code, 200)

    def test_same_nonce_in_another_platform_is_independent(self):
        alpha = self.session()
        beta = CallbackSession('beta', 'east')
        beta.nonce = alpha.nonce
        self.sessions['beta'][beta.nonce] = beta
        self.assertEqual(self.submit(alpha).status_code, 200)
        self.assertIsNone(beta.wait())
        self.assertEqual(self.submit(beta, params={'ticket': 'beta-code'}).status_code, 200)
        self.assertEqual(alpha.wait()['authorization_code'], 'test-ticket')
        self.assertEqual(beta.wait()['authorization_code'], 'beta-code')

    def test_adapter_cannot_return_a_foreign_or_wrong_session(self):
        session = self.session()
        self.modules['beta'].get_session = lambda nonce: session
        self.assertEqual(self.submit(session, 'beta-east').status_code, 404)
        other = self.session()
        self.modules['alpha'].get_session = lambda nonce: other
        self.assertEqual(self.submit(session).status_code, 404)
        self.assertIsNone(other.wait())

    def test_expiry_is_enforced_even_if_adapter_does_not_prune(self):
        session = self.session()
        session.created_at -= 601
        self.assertEqual(self.submit(session).status_code, 404)
        self.assertFalse(session.deliver(authorization_code='expired'))
        self.assertIsNone(session.wait())

    def test_invalid_link_and_all_identifiers_leave_session_retryable(self):
        session = self.session()
        for key in ('state', 'nonce', 'request_id'):
            self.assertEqual(self.submit(session, params={'ticket': 'code', key: 'different'}).status_code, 400)
        for url in ('https://host/return?ticket=a&ticket=b', 'https://host/return#ticket=a',
                    'file:///tmp/callback', 'http://user:pass@localhost/return?ticket=a',
                    'https://host:bad/return?ticket=a', 'http://[bad'):
            self.assertEqual(self.submit(session, callback_url=url).status_code, 400)
        self.assertEqual(self.submit(session, params={'wrong-field': 'code'}).status_code, 400)
        self.assertIsNone(session.wait())
        self.assertEqual(self.submit(session).status_code, 200)

    def test_duplicate_delivery_does_not_overwrite(self):
        session = self.session()
        self.assertEqual(self.submit(session).status_code, 200)
        self.assertEqual(self.submit(session).status_code, 200)
        self.assertEqual(self.submit(session, params={'ticket': 'different'}).status_code, 409)
        self.assertEqual(session.wait(), {'authorization_code': 'test-ticket'})

    def test_multiple_protocols_use_one_endpoint_and_shared_completion(self):
        for platform in ('alpha', 'beta'):
            session = self.session(platform)
            self.assertEqual(self.submit(session).status_code, 200)
            calls = []
            def finish():
                calls.append(session.wait()['authorization_code'])
                return {'ok': True, 'account_id': platform}
            with ThreadPoolExecutor(max_workers=6) as pool:
                list(pool.map(lambda _: session.exchange_once(finish), range(12)))
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                result = session.exchange_once(finish)
                if not result.get('pending'):
                    break
                time.sleep(.01)
            self.assertEqual(result, {'ok': True, 'account_id': platform})
            self.assertEqual(calls, ['test-ticket'])
            self.assertEqual(session.exchange_once(finish), result)

    def test_failed_adapter_is_contained(self):
        session = self.session()
        def fail(params):
            raise RuntimeError('private-code-or-verifier')
        self.modules['alpha'].parse_callback = fail
        response = self.submit(session)
        self.assertEqual(response.status_code, 500)
        self.assertNotIn('private', response.text)
        self.assertEqual(self.submit(self.session('beta')).status_code, 200)

    def test_disabled_platform_rejects_submission(self):
        registry.get('alpha-east').enabled = False
        self.assertEqual(self.submit(self.session()).status_code, 404)

    def test_multi_realm_and_unregional_platforms(self):
        spec = registry.PlatformSpec(id='alpha-regions', label='多区域测试', storage_platform='alpha',
            realms=(registry.Realm('east', 'East', True), registry.Realm('west', 'West')),
            auth_callback=self.modules['alpha'].__name__)
        registry.register(spec)
        self.specs.append(spec)
        session = self.session('alpha', 'west')
        self.assertEqual(self.submit(session, spec.id).status_code, 404)
        self.assertEqual(self.submit(session, spec.id, realm='invalid').status_code, 400)
        self.assertEqual(self.submit(session, spec.id, realm='west').status_code, 200)
        spec = registry.PlatformSpec(id='beta-global', label='无区域测试', storage_platform='beta',
            auth_callback=self.modules['beta'].__name__)
        registry.register(spec)
        self.specs.append(spec)
        session = self.session('beta', '')
        self.assertEqual(self.submit(session, spec.id, realm='east').status_code, 400)
        self.assertEqual(self.submit(session, spec.id).status_code, 200)

    def test_expired_session_does_not_begin_exchange(self):
        session = self.session()
        session.deliver(authorization_code='valid')
        session.created_at -= 601
        with patch('builtins.print') as complete:
            result = session.exchange_once(complete)
            complete.assert_not_called()
        self.assertFalse(result['ok'])
        self.assertIn('过期', result['error'])

    def test_bad_background_result_is_a_cached_failure(self):
        session = self.session()
        calls = []
        def invalid():
            calls.append(1)
            return None
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            result = session.exchange_once(invalid)
            if not result.get('pending'):
                break
            time.sleep(.01)
        self.assertFalse(result['ok'])
        self.assertEqual(session.exchange_once(invalid), result)
        self.assertEqual(calls, [1])

    def test_trae_uses_shared_endpoint_and_keeps_real_import_flow(self):
        start = self.client.post('/api/trae/device/start?realm=intl', headers=self.headers).json()
        session = oauth.get(start['nonce'])
        body = {'nonce': session.nonce, 'callback_url': 'http://127.0.0.1:8790/authorize?' + urlencode({
            'authCodeInfo': json.dumps({'AuthCode': 'generic-test-code'}),
            'loginTraceID': session.nonce, 'userInfo': json.dumps({'UserID': 'shared-callback-test'})})}
        self.assertEqual(self.client.post('/api/platforms/trae-cn/auth/callback', json=body, headers=self.headers).status_code, 404)
        with patch.object(oauth, 'exchange_token', return_value={'Token': 'access', 'RefreshToken': 'refresh'}) as exchange:
            self.assertEqual(self.client.post('/api/platforms/trae-intl/auth/callback', json=body, headers=self.headers).status_code, 200)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                result = self.client.get('/api/trae/device/poll?nonce=' + session.nonce, headers=self.headers).json()
                if not result.get('pending'):
                    break
                time.sleep(.01)
            self.assertTrue(result['ok'])
            exchange.assert_called_once()
        with SessionLocal() as db:
            row = db.query(Account).filter(Account.uid == 'shared-callback-test').one()
            self.assertEqual((row.platform, row.domain), ('trae', 'intl'))
            db.delete(row)
            db.commit()


if __name__ == '__main__':
    unittest.main()
