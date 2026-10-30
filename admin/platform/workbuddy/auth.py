"""Manual desktop callback completion backed by the official state poll."""
from admin.platform.auth import PollingSession, SessionStore, completion_link

_sessions = SessionStore()
get_session = _sessions.get


class Login(PollingSession):
    def __init__(self, native):
        super().__init__('workbuddy')
        self.native = native
        self.nonce = native.plugin_state

    def close(self):
        self.native.close()


def start(realm):
    from admin import wb_login
    session = Login(wb_login.start_official_login())
    try:
        _sessions.add(session)
    except Exception:
        session.close()
        raise
    return {'nonce': session.nonce, 'auth_url': session.native.plugin_auth_url,
            'expires_in': session.ttl,
            'callback_hint': '在官方页面完成登录后，粘贴 workbuddy:// 或 codebuddy:// 回调链接（需含本次 state）。也可等待自动完成。'}


def parse_callback_url(value, session):
    return completion_link(value, session,
        schemes=('http', 'https', 'workbuddy', 'codebuddy'),
        hosts=('www.workbuddy.cn', 'workbuddy.cn', 'www.codebuddy.cn', 'codebuddy.cn'),
        reject_paths=('/login', '/auth', '/authorize'))


def poll(session):
    def complete():
        from admin import wb_login
        native = session.native
        token = native.official_token or wb_login.fetch_credential_by_state(native.plugin_state)
        if not token:
            return {'pending': True}
        native.official_token = token
        credential, meta = wb_login.build_credential_from_token(token)
        from admin.db import SessionLocal
        from admin.routers.login import _store_verified_credential
        with SessionLocal() as db:
            result = _store_verified_credential(credential, meta, db)
        native.close()
        return {'ok': True, 'account_id': (result.get('account') or {}).get('id')}
    return session.poll_background(complete)
