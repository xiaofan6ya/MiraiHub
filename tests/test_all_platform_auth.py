"""All shipped channels expose functional manual authorization adapters."""
import json
import time
import unittest
from types import SimpleNamespace
from urllib.parse import urlencode, urlsplit, parse_qs
from unittest.mock import Mock, patch

from test_trae_callback import app, TestClient, create_admin_token
from admin.platform.router import router
from admin.platform.loader import load_platforms
from admin.platform import registry
from admin.platform.auth import PollingSession, SessionStore
from admin.platform.qoder import auth as qoder
from admin.platform.workbuddy import auth as workbuddy
from admin.platform.loomy import auth as loomy

if not any(getattr(r, 'path', '') == '/api/platforms/{platform_id}/auth/start' for r in app.routes):
    app.include_router(router)
load_platforms()
CFG = {'base_url': 'https://account.xfinfr.com', 'access_key_id': 'test-id',
       'access_key_secret': 'test-secret', 'app_id': 'test-app', 'wechat_app_id': 'test-wx',
       'redirect_uri': loomy.REDIRECT}


class AllPlatformAuthTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.headers = {'X-Admin-Token': create_admin_token()}
        self.cfg = patch.object(loomy, 'configuration', return_value=CFG.copy())
        self.cfg.start()
        self.addCleanup(self.cfg.stop)
        self.native = SimpleNamespace(plugin_state='workbuddy-test-state',
            plugin_auth_url='https://www.workbuddy.cn/auth?state=workbuddy-test-state',
            official_token=None, close=Mock())
        self.start_mock = patch('admin.wb_login.start_official_login', return_value=self.native)
        self.start_mock.start()
        self.addCleanup(self.start_mock.stop)

    def tearDown(self):
        self.client.close()
        qoder.routes._SESSIONS.clear()
        workbuddy._sessions.items.clear()
        loomy._sessions.items.clear()

    def start(self, pid, realm=''):
        r = self.client.post('/api/platforms/'+pid+'/auth/start', params={'realm': realm}, headers=self.headers)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def submit(self, pid, nonce, url, realm=''):
        return self.client.post('/api/platforms/'+pid+'/auth/callback', headers=self.headers,
            json={'nonce': nonce, 'realm': realm, 'callback_url': url})

    def poll(self, pid, nonce):
        for _ in range(100):
            r = self.client.get('/api/platforms/'+pid+'/auth/poll', params={'nonce': nonce}, headers=self.headers)
            self.assertEqual(r.status_code, 200, r.text)
            data = r.json()
            if not data.get('pending') or data.get('stage') == 'bind_phone':
                return data
            time.sleep(.01)
        self.fail('authorization did not complete')

    def test_all_six_channels_have_lifecycle_and_callback(self):
        for pid in ('trae-cn', 'trae-intl', 'qoder', 'qoder-intl', 'workbuddy', 'loomy'):
            with self.subTest(pid=pid):
                self.assertTrue(registry.get(pid).auth_callback)
                result = self.start(pid)
                self.assertTrue(result['auth_url'])
                self.assertTrue(result['nonce'])
        self.assertIn('begin-auth', registry.get('workbuddy').extra['actions'])
        self.assertIn('begin-auth', registry.get('loomy').extra['actions'])

    def test_authentication_and_region_required(self):
        self.assertEqual(self.client.post('/api/platforms/qoder/auth/start').status_code, 401)
        self.assertEqual(self.client.get('/api/platforms/qoder/auth/poll?nonce=x').status_code, 401)
        self.assertEqual(self.client.post('/api/platforms/qoder/auth/start?realm=intl', headers=self.headers).status_code, 400)
        data = self.start('qoder')
        self.assertEqual(self.client.get('/api/platforms/qoder-intl/auth/poll', params={'nonce': data['nonce']}, headers=self.headers).status_code, 404)

    def test_qoder_both_regions_manual_completion_and_single_import(self):
        for pid in ('qoder', 'qoder-intl'):
            data = self.start(pid)
            nonce = data['nonce']
            session = qoder.get_session(nonce)
            url = 'qoder://auth/callback?nonce='+nonce
            self.assertEqual(self.submit(pid, nonce, url).status_code, 200)
            self.assertEqual(self.submit(pid, nonce, url).status_code, 200)
            with patch.object(session, 'poll_once', return_value={'token': 'test', 'uid': 'user', 'realm': session.realm}) as poll, patch('admin.platform.qoder.services.accounts.import_record', return_value={'id': 8}) as save:
                self.assertTrue(self.poll(pid, nonce)['ok'])
                self.assertTrue(self.poll(pid, nonce)['ok'])
                self.assertEqual(poll.call_count, 1)
                self.assertEqual(save.call_count, 1)

    def test_qoder_rejects_wrong_link_without_network(self):
        data = self.start('qoder')
        for url in (data['auth_url'], 'qoder://auth?nonce=other',
                    'https://evil.example/return?nonce='+data['nonce'],
                    'workbuddy://auth?nonce='+data['nonce'],
                    'qoder://auth?nonce='+data['nonce']+'&nonce='+data['nonce']):
            self.assertEqual(self.submit('qoder', data['nonce'], url).status_code, 400)

    def test_workbuddy_manual_and_auto_share_official_state(self):
        data = self.start('workbuddy')
        self.assertEqual(self.submit('workbuddy', data['nonce'], 'workbuddy://auth?state='+data['nonce']).status_code, 200)
        with patch('admin.wb_login.fetch_credential_by_state', return_value={'accessToken': 'test'}) as fetch, patch('admin.wb_login.build_credential_from_token', return_value=('{}', {})), patch('admin.routers.login._store_verified_credential', return_value={'account': {'id': 9}}) as save:
            self.assertEqual(self.poll('workbuddy', data['nonce'])['account_id'], 9)
            self.poll('workbuddy', data['nonce'])
            fetch.assert_called_once_with(data['nonce'])
            save.assert_called_once()

    def test_loomy_bound_account_exchanges_code_once(self):
        data = self.start('loomy')
        self.assertEqual(parse_qs(urlsplit(data['auth_url']).query)['state'], [data['nonce']])
        link = loomy.REDIRECT+'?'+urlencode({'code': 'test-code', 'state': data['nonce']})
        self.assertEqual(self.submit('loomy', data['nonce'], link).status_code, 200)
        self.assertEqual(self.submit('loomy', data['nonce'], link).status_code, 200)
        with patch.object(loomy, 'account_request', side_effect=[{'bind': 1, 'rcode': 'r'}, {'session': 'test', 'userid': 'u'}]) as exchange, patch('admin.platform.loomy.services.accounts.import_record', return_value={'id': 10}) as save:
            self.assertTrue(self.poll('loomy', data['nonce'])['ok'])
            self.poll('loomy', data['nonce'])
            self.assertEqual(exchange.call_count, 2)
            save.assert_called_once()
        self.assertEqual(self.submit('loomy', data['nonce'], link.replace('test-code', 'different')).status_code, 409)

    def test_loomy_unbound_account_requires_explicit_sms_action(self):
        data = self.start('loomy')
        nonce = data['nonce']
        self.submit('loomy', nonce, loomy.REDIRECT+'?code=c&state='+nonce)
        with patch.object(loomy, 'account_request', side_effect=[{'bind': 0, 'rcode': 'r'}, {'msgid': 'm'}, {'session': 's', 'userid': 'u'}]) as exchange, patch('admin.platform.loomy.services.accounts.import_record', return_value={'id': 11}) as save:
            self.assertEqual(self.poll('loomy', nonce)['stage'], 'bind_phone')
            self.assertEqual(exchange.call_count, 1)
            payload = {'nonce': nonce, 'phone': '13800138000'}
            self.assertEqual(self.client.post('/api/platforms/loomy/auth/bind/send', headers=self.headers,json=payload).status_code, 200)
            self.assertEqual(self.client.post('/api/platforms/loomy/auth/bind/send', headers=self.headers,json=payload).status_code, 429)
            payload['code'] = '123456'
            self.assertEqual(self.client.post('/api/platforms/loomy/auth/bind/verify', headers=self.headers,json=payload).status_code, 200)
            self.assertTrue(self.poll('loomy', nonce)['ok'])
            self.assertEqual(exchange.call_count, 3)
            save.assert_called_once()

    def test_loomy_rejects_cross_session_host_and_expired(self):
        data = self.start('loomy');nonce = data['nonce']
        for url in (loomy.REDIRECT+'?code=c&state=wrong', 'https://evil.example/?code=c&state='+nonce, loomy.REDIRECT+'?code=c'):
            self.assertEqual(self.submit('loomy', nonce, url).status_code, 400)
        loomy.get_session(nonce).created_at -= 601
        self.assertEqual(self.submit('loomy', nonce, loomy.REDIRECT+'?code=c&state='+nonce).status_code, 404)

    def test_qoder_token_poll_result_cached(self):
        data = self.start('qoder');session = qoder.get_session(data['nonce'])
        with patch.object(session, '_poll_once', return_value={'token': 'test'}) as call:
            self.assertEqual(session.poll_once(), session.poll_once())
            call.assert_called_once()
        session.created_at -= 601
        with self.assertRaises(Exception): session.poll_once()

    def test_pending_background_poll_can_retry(self):
        session = PollingSession('test')
        complete = Mock(side_effect=[{'pending': True}, {'ok': True}])
        for _ in range(100):
            result = session.poll_background(complete)
            if result.get('ok'): break
            time.sleep(.01)
        self.assertTrue(result['ok'])
        self.assertEqual(complete.call_count, 2)
