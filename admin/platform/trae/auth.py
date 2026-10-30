"""Trae 回调协议适配；HTTP 入口与会话隔离由公共授权模块提供。"""
from admin.platform.trae.core.oauth import callback_payload as parse_callback
from admin.platform.trae.core.oauth import get as get_session

session_keys = ("nonce", "login_trace_id", "loginTraceID", "state")


def start(realm):
    from admin.platform.trae.routers.accounts import device_start
    from admin.db import SessionLocal
    with SessionLocal() as db:
        return device_start(realm=realm, _=True, db=db)


def poll(session):
    from admin.platform.trae.routers.accounts import device_poll
    return device_poll(nonce=session.nonce, _=True)
