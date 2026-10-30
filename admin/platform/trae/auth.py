"""Trae 回调协议适配；HTTP 入口与会话隔离由公共授权模块提供。"""
from admin.platform.trae.core.oauth import callback_payload as parse_callback
from admin.platform.trae.core.oauth import get as get_session

session_keys = ("nonce", "login_trace_id", "loginTraceID", "state")
