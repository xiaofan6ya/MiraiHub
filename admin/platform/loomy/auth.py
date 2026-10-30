"""Loomy WeChat OAuth, matching the official desktop account protocol.

Application signing configuration lives outside source control. Never fetch a
submitted callback URL; exchange its code only at the configured account host.
"""
import base64
from email.utils import formatdate
import hashlib
import hmac
import json
import os
from pathlib import Path
import threading
import time
from urllib.parse import urlencode, urlsplit
import uuid

import httpx
from fastapi import HTTPException
from admin.platform.auth import PollingSession, SessionStore, parse_callback_query

_sessions = SessionStore()
get_session = _sessions.get
REDIRECT = 'https://loomy.xunfei.cn/oauth/wechat/callback'


def configuration():
    path = Path(os.getenv('LOOMY_AUTH_CONFIG_PATH', '/data/loomy-oauth.json'))
    try:
        cfg = json.loads(path.read_text())
    except (OSError, ValueError):
        raise HTTPException(503, 'Loomy 授权配置缺失，请配置官方客户端的账号服务参数') from None
    if not all(cfg.get(k) for k in ('base_url', 'access_key_id', 'access_key_secret', 'app_id', 'wechat_app_id')):
        raise HTTPException(503, 'Loomy 授权配置不完整')
    if cfg['base_url'] not in ('https://account.xfinfr.com', 'https://accounttest.xfinfr.com'):
        raise HTTPException(503, 'Loomy 账号服务地址无效')
    cfg['redirect_uri'] = cfg.get('redirect_uri') or REDIRECT
    return cfg


def account_request(cfg, path, param):
    body = json.dumps({'base': {'appid': cfg['app_id'], 'modelid': 'Web', 'version': '1.0.0',
        'devid': 'web', 'ua': 'Loomy|Desktop|Electron|macOS', 'traceid': uuid.uuid4().hex},
        'param': param}, ensure_ascii=False, separators=(',', ':')).encode()
    date, nonce = formatdate(usegmt=True), str(uuid.uuid4())
    md5 = base64.b64encode(hashlib.md5(body).digest()).decode()
    canonical = '\n'.join(('POST', path, '', md5, 'application/json', date, nonce, '', ''))
    signature = base64.b64encode(hmac.new(cfg['access_key_secret'].encode(), canonical.encode(), hashlib.sha1).digest()).decode()
    headers = {'Authorization': 'account '+cfg['access_key_id']+':'+signature,
               'Date': date, 'Nonce': nonce, 'Content-MD5': md5, 'Content-Type': 'application/json'}
    try:
        with httpx.Client(timeout=30, follow_redirects=False) as client:
            response = client.post(cfg['base_url']+path, content=body, headers=headers)
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError):
        raise ValueError('Loomy 账号服务请求失败，请重试') from None
    if str(data.get('code') or '000000') not in ('000000', '0'):
        raise ValueError('Loomy 授权未通过（业务码 %s）' % str(data.get('code'))[:32])
    result = data.get('data')
    if not isinstance(result, dict):
        raise ValueError('Loomy 授权响应缺少结果')
    return result


class Login(PollingSession):
    def __init__(self, cfg):
        super().__init__('loomy', 'cn')
        self.cfg = cfg
        self.auth_result = None
        self.credentials = None
        self.msgid = ''
        self.last_sms = 0.0
        self.bind_lock = threading.Lock()


def start(realm):
    session = _sessions.add(Login(configuration()))
    query = urlencode({'appid': session.cfg['wechat_app_id'], 'redirect_uri': session.cfg['redirect_uri'],
                       'response_type': 'code', 'scope': 'snsapi_login', 'state': session.nonce})
    return {'nonce': session.nonce, 'expires_in': session.ttl,
            'auth_url': 'https://open.weixin.qq.com/connect/qrconnect?'+query+'#wechat_redirect',
            'callback_hint': '微信扫码确认后，复制跳转到 Loomy 的完整回调链接并提交。首次登录如需绑定手机号，本页会继续显示验证表单。'}


def parse_callback_url(value, session):
    parsed, expected = urlsplit(value.strip()), urlsplit(session.cfg['redirect_uri'])
    if (parsed.scheme, parsed.netloc, parsed.path) != (expected.scheme, expected.netloc, expected.path):
        raise ValueError('请粘贴 Loomy 微信授权完成后的回调链接')
    params = parse_callback_query(value, session.nonce)
    if params.get('state') != session.nonce:
        raise ValueError('回调链接不属于当前授权会话')
    if not params.get('code') or params.get('error') or params.get('errmsg'):
        raise ValueError('微信授权未返回有效 code，请重新授权')
    return {'code': params['code']}


def _complete(session):
    payload = session.wait()
    if payload is None:
        return {'pending': True, 'summary': '等待提交微信授权回调链接…'}
    with session.bind_lock:
        if session.auth_result is None:
            param = {'type': 'wx', 'tcode': {'code': payload['code']}}
            if session.cfg.get('wechat_authconf'):
                param['authconf'] = session.cfg['wechat_authconf']
            session.auth_result = account_request(session.cfg, '/login/thirdAccount/bind/auth', param)
        result = session.auth_result
        if not result.get('rcode'):
            raise ValueError('Loomy 授权响应缺少 rcode')
        if session.credentials is None:
            if str(result.get('bind')) != '1':
                return {'pending': True, 'stage': 'bind_phone', 'summary': '微信授权成功，请绑定手机号以完成登录'}
            session.credentials = account_request(session.cfg, '/login/thirdAccount/bind/skip',
                                                   {'rcode': result['rcode'], 'expire': 14*86400})
        credentials = session.credentials
        if not credentials.get('session') or not credentials.get('userid'):
            raise ValueError('Loomy 授权响应缺少登录凭证')
        from admin.db import SessionLocal
        from admin.platform.loomy.services.accounts import import_record
        with SessionLocal() as db:
            return {'ok': True, **import_record(db, credentials)}


def poll(session):
    # Surface the binding stage between background polls without consuming code again.
    if session.auth_result and str(session.auth_result.get('bind')) != '1' and not session.credentials:
        return {'pending': True, 'stage': 'bind_phone', 'summary': '微信授权成功，请绑定手机号以完成登录'}
    return session.poll_background(lambda: _complete(session))


def bind(session, action, body):
    with session.bind_lock:
        if not session.auth_result or not session.auth_result.get('rcode'):
            raise HTTPException(409, '请先提交微信授权回调')
        if session.credentials:
            return {'ok': True}
        rcode = session.auth_result['rcode']
        try:
            if action == 'send':
                if not body.phone.isdigit() or len(body.phone) != 11:
                    raise HTTPException(400, '请输入 11 位手机号')
                if time.monotonic()-session.last_sms < 60:
                    raise HTTPException(429, '请等待 60 秒后再发送验证码')
                session.last_sms = time.monotonic()
                result = account_request(session.cfg, '/login/thirdAccount/bind/sendMsg',
                                         {'rcode': rcode, 'phone': body.phone, 'ccode': '86', 'expire': 300})
                session.msgid = str(result.get('msgid') or '')
                if not session.msgid:
                    raise ValueError('验证码发送结果无效')
            else:
                if not session.msgid or not body.code.isdigit() or not 4 <= len(body.code) <= 8:
                    raise HTTPException(400, '请先发送并填写短信验证码')
                session.credentials = account_request(session.cfg, '/login/thirdAccount/bind/checkCode',
                    {'rcode': rcode, 'mcode': body.code, 'msgid': session.msgid, 'expire': 14*86400})
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
    return {'ok': True}
