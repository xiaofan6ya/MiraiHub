"""Qoder CN/intl manual completion; tokens still come from PKCE device polling."""
from admin.platform.auth import completion_link
from admin.platform.qoder.routers import oauth as routes
from admin.platform.qoder.core import endpoints


def get_session(nonce):
    return routes._SESSIONS.get(nonce)


def start(realm):
    result = routes.device_start(realm=realm, _=True)
    result['callback_hint'] = '授权完成后可粘贴带本次 nonce/state 的跳转链接。凭证仍由官方设备授权接口核验；也可等待自动完成。'
    return result


def parse_callback_url(value, session):
    from urllib.parse import urlsplit
    host = urlsplit(endpoints.web_origin(session.realm)).hostname
    return completion_link(value, session, schemes=('http', 'https', 'qoder', 'qoder-cn', 'qoder-intl'),
                           hosts=(host,), reject_paths=('/device/selectAccounts',))


def poll(session):
    def complete():
        credentials = session.poll_once()
        if credentials is None:
            return {'pending': True}
        from admin.db import SessionLocal
        from admin.platform.qoder.services.accounts import import_record
        with SessionLocal() as db:
            return {'ok': True, **import_record(db, credentials)}
    return session.poll_background(complete)
