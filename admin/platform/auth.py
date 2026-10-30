"""公共手动回调：平台适配器负责协议，框架负责会话隔离与交付。

适配器模块需提供 get_session(nonce)、parse_callback(params)，以及可选的
session_keys。模块路径只来自 PlatformSpec，不能由请求指定。
"""
from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
import importlib
import logging
import threading
import time
from urllib.parse import parse_qs, urlsplit
import uuid

from fastapi import HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger("admin.platform.auth")


class ManualCallbackIn(BaseModel):
    nonce: str = Field(min_length=1, max_length=128)
    callback_url: str = Field(min_length=1, max_length=32768)
    realm: str = Field(default="", max_length=64)


def parse_callback_query(value: str, nonce: str,
                         session_keys: tuple[str, ...] = ("state", "nonce"),
                         *, schemes: tuple[str, ...] = ("http", "https")) -> dict[str, str]:
    """只解析链接，不访问链接；绑定显式会话，拒绝重复或冲突参数。"""
    try:
        parsed = urlsplit(value.strip())
        if (parsed.scheme not in schemes or not parsed.hostname
                or parsed.username or parsed.password or parsed.fragment):
            raise ValueError
        # 触发非法端口检查，不对回调目标发起请求。
        _ = parsed.port
        query = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=64)
    except ValueError:
        raise ValueError("请粘贴以 http:// 或 https:// 开头、不含片段的完整回调链接") from None
    if any(len(values) != 1 for values in query.values()):
        raise ValueError("回调链接包含重复参数，请重新复制完整地址")
    params = {key: values[0] for key, values in query.items()}
    for key in session_keys:
        if params.get(key, "").strip() and params[key].strip() != nonce:
            raise ValueError("回调链接不属于当前授权会话，请使用本次授权后跳转的链接")
    return params


class CallbackSession:
    """任意回调平台可复用的交付、去重和后台结果缓存。

    platform 使用存储平台 id；realm 必须与平台注册声明对应。
    协议字段（例如 PKCE verifier）由平台子类保管，不进入公开结果。
    """

    def __init__(self, platform: str, realm: str = "", *, ttl: int = 600):
        self.platform = platform
        self.realm = realm
        self.nonce = str(uuid.uuid4())
        self.created_at = int(time.time())
        self.ttl = ttl
        self._done = threading.Event()
        self._delivery_lock = threading.Lock()
        self._payload: dict | None = None
        self._exchange_lock = threading.Lock()
        self._exchange_started = False
        self._exchange_result: dict | None = None

    @property
    def expired(self) -> bool:
        return time.time() - self.created_at > self.ttl

    def deliver(self, **payload) -> bool:
        with self._delivery_lock:
            if self.expired:
                return False
            if self._done.is_set():
                return self._payload == payload
            self._payload = deepcopy(payload)
            self._done.set()
            return True

    def wait(self, timeout: float = 0.0) -> dict | None:
        if timeout > 0:
            self._done.wait(timeout)
        with self._delivery_lock:
            return deepcopy(self._payload)

    def exchange_once(self, complete: Callable[[], dict]) -> dict:
        """后台只执行一次；重试查询只读取结果，不重新消费授权码。"""
        with self._exchange_lock:
            if self.expired:
                return {"ok": False, "error": "授权会话已过期，请重新开始"}
            if self._exchange_result is not None:
                return deepcopy(self._exchange_result)
            if not self._exchange_started:
                self._exchange_started = True

                def run() -> None:
                    try:
                        result = complete()
                        if not isinstance(result, dict):
                            raise TypeError("Authorization completion must return a dict")
                    except Exception:
                        logger.error("授权任务异常，platform=%s，realm=%s", self.platform, self.realm)
                        result = {"ok": False, "error": "授权处理失败，请重新发起授权"}
                    with self._exchange_lock:
                        self._exchange_result = deepcopy(result)

                try:
                    threading.Thread(target=run, name="oauth-callback", daemon=True).start()
                except RuntimeError:
                    self._exchange_started = False
                    raise
        return {"pending": True, "stage": "exchanging", "summary": "回调已接收，正在完成授权…"}


def submit_callback(platform_id: str, body: ManualCallbackIn) -> dict:
    from admin.platform import registry
    from admin.platform.loader import load_platforms

    load_platforms()
    spec = registry.get(platform_id)
    if spec is None or not spec.enabled:
        raise HTTPException(404, detail="平台不存在或未启用")
    if not spec.auth_callback:
        raise HTTPException(400, detail="该平台不支持手动回调，请使用其现有授权方式")
    realm = body.realm.strip() or spec.default_realm()
    if ((spec.realm and realm != spec.realm)
            or (spec.realms and realm not in spec.realm_keys())
            or (not spec.realm and not spec.realms and realm)):
        raise HTTPException(400, detail="授权区域与平台不匹配")

    try:
        adapter = importlib.import_module(spec.auth_callback)
        session = adapter.get_session(body.nonce.strip())
        if (not isinstance(session, CallbackSession) or session.expired
                or session.nonce != body.nonce.strip()
                or session.platform != spec.storage() or session.realm != realm):
            raise HTTPException(404, detail="授权会话不存在或已过期，请重新开始")
        if hasattr(adapter, "parse_callback_url"):
            payload = adapter.parse_callback_url(body.callback_url, session)
        else:
            params = parse_callback_query(body.callback_url, session.nonce,
                                          getattr(adapter, "session_keys", ("state", "nonce")))
            payload = adapter.parse_callback(params)
        if not session.deliver(**payload):
            raise HTTPException(409, detail="本次授权已接收其他回调，请查看授权结果或重新开始")
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from None
    except Exception as exc:
        logger.error("授权回调处理失败，platform=%s，错误类型=%s", spec.id, type(exc).__name__)
        raise HTTPException(500, detail="授权回调处理失败，请稍后重试") from None
    return {"ok": True, "summary": "回调已接收，正在完成授权…"}


def adapter_for(platform_id: str, realm: str = ""):
    """Resolve lifecycle operations with the same platform/realm isolation."""
    from admin.platform import registry
    from admin.platform.loader import load_platforms
    load_platforms()
    spec = registry.get(platform_id)
    if spec is None or not spec.enabled:
        raise HTTPException(404, "平台不存在或未启用")
    if not spec.auth_callback:
        raise HTTPException(400, "该平台未接入授权回调")
    target = realm.strip() or spec.default_realm()
    if ((spec.realm and target != spec.realm)
            or (spec.realms and target not in spec.realm_keys())
            or (not spec.realm and not spec.realms and target)):
        raise HTTPException(400, "授权区域与平台不匹配")
    return spec, target, importlib.import_module(spec.auth_callback)


def checked_session(spec, realm, adapter, nonce):
    session = adapter.get_session(nonce)
    if (not isinstance(session, CallbackSession) or session.expired
            or session.platform != spec.storage() or session.realm != realm
            or session.nonce != nonce):
        raise HTTPException(404, "授权会话不存在或已过期，请重新开始")
    return session


class SessionStore:
    """Bounded, expiring adapter sessions; eviction never leaks client handles."""
    def __init__(self):
        self.items = {}
        self.lock = threading.RLock()

    def add(self, session):
        with self.lock:
            for key, old in list(self.items.items()):
                if old.expired:
                    self.items.pop(key)
                    close = getattr(old, "close", None)
                    if close:
                        close()
            if len(self.items) >= 100:
                raise HTTPException(429, "进行中的授权会话过多，请稍后重试")
            self.items[session.nonce] = session
        return session

    def get(self, nonce):
        with self.lock:
            return self.items.get(nonce)


class PollingSession(CallbackSession):
    """Poll upstream in one background worker; cache terminal results once."""
    def poll_background(self, complete):
        with self._exchange_lock:
            if self.expired:
                return {"ok": False, "error": "授权会话已过期，请重新开始"}
            if self._exchange_result is not None:
                return deepcopy(self._exchange_result)
            if not self._exchange_started:
                self._exchange_started = True
                def run():
                    try:
                        result = complete()
                        if not isinstance(result, dict):
                            raise TypeError("Invalid authorization result")
                    except Exception:
                        logger.warning("授权处理失败，platform=%s", self.platform)
                        result = {"ok": False, "error": "授权处理失败，请重新发起授权"}
                    with self._exchange_lock:
                        if not result.get("pending"):
                            self._exchange_result = deepcopy(result)
                        self._exchange_started = False
                try:
                    threading.Thread(target=run, daemon=True, name="platform-auth").start()
                except RuntimeError:
                    self._exchange_started = False
                    raise
        return {"pending": True, "summary": "正在查询授权结果…"}


def completion_link(value, session, *, schemes, hosts, reject_paths=()):
    """Accept only a platform completion URL bound to this authorization."""
    parsed = urlsplit(value.strip())
    if (parsed.scheme in ("http", "https") and parsed.hostname not in hosts
            or parsed.path.rstrip("/") in reject_paths):
        raise ValueError("请粘贴本平台授权完成后的链接，不要粘贴授权页地址")
    params = parse_callback_query(value, session.nonce, schemes=schemes)
    if not any(params.get(k) == session.nonce for k in ("state", "nonce")):
        raise ValueError("回调链接缺少当前授权会话的 state/nonce")
    if params.get("error") or params.get("errmsg"):
        raise ValueError("上游未完成授权，请重新授权")
    return {"authorized": True}
