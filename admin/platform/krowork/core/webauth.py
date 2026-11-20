"""KroWork 手机号授权登录（PKCE + Keycloak SMS 表单）—— 全自动取票。

## 为什么要自己走一遍登录

`krow_openai` 的 sidecar 只能**借**客户端已经登好的态；一旦客户端登出或
换了账号，网关就跟着失明（实测 `KrowAIError: No credentials available`）。
本模块把「手机号 + 短信验证码」这条路走通，网关就能**独立拿票**，
不依赖客户端是否登录 —— 只需要用户提供一次验证码。

## 协议（全部实测，2026-11-19）

Keycloak realm `krow`，client `krow-app`，**公开客户端**（无 client_secret）。
`redirect_uri` 是**写死的白名单**：只接受 `http://127.0.0.1:17321/callback`
（17320 / 45123 / 8080 实测全 400）。而这个端口被 `krow-app` 自己监听，
所以网关**不绑端口**，而是在 HTTP 客户端里**拦下 302** 直接读 `code`。

```
1) GET  /realms/krow/protocol/openid-connect/auth?response_type=code&client_id=krow-app
        &state=…&code_challenge=<S256(verifier)>&code_challenge_method=S256
        &redirect_uri=http://127.0.0.1:17321/callback&scope=openid
   → 200 登录页，同时下发 AUTH_SESSION_ID / KC_AUTH_SESSION_HASH / KC_RESTART
   → 页面里 `<form id="kc-form-login" action="…/login-actions/authenticate?session_code=…">`

2) POST /realms/krow/sms/send-code          {"phoneNumber","countryCode","scene":"LOGIN"}
   → {"success":true}  /  {"success":false,"errorCode":"…"}

3) POST /realms/krow/sms/verify             {"phoneNumber","countryCode","code","scene":"LOGIN"}
   → {"success":true}   ← 只是校验证码，不等于登录

4) POST <form action>  (form-urlencoded)
        phoneNumber=<countryCode+phone>&smsCode=<code>&action=smsLogin
   → 302 Location: http://127.0.0.1:17321/callback?code=<code>&state=<state>
   （登录页 JS 的 `submitSmsLogin` 就是这么提交的，字段名逐字照抄）

5) POST /realms/krow/protocol/openid-connect/token
        grant_type=authorization_code&client_id=krow-app&code=<code>
        &code_verifier=<verifier>&redirect_uri=http://127.0.0.1:17321/callback
   → {id_token, access_token, refresh_token, expires_in, …}
```

## 三个坑

* **`redirect_uri` 不能改端口**：白名单只认 17321，而它被客户端占着 →
  必须拦 302，不能指望回调。
* **`code_verifier` 长度 43~128**（客户端里那条 Rust 断言）：用 72 字节
  base64url 正好 96 字符。
* **验证码校验和登录是两步**：`/sms/verify` 返回 true 只代表码对，
  真正建立会话的是第 4 步那个表单 POST。
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from . import endpoints, token as token_mod

logger = logging.getLogger("admin.platform.krowork.webauth")

#: 白名单里唯一可用的回调（写死，不能改端口）
REDIRECT_URI = "http://127.0.0.1:17321/callback"
CLIENT_ID = "krow-app"
#: ⚠ **必须带 `offline_access`**（2026-11-20 修）。
#:
#: 不带它时 Keycloak 只发**会话内** refresh_token：30 分钟后随 SSO 会话一起作废，
#: 再拿它去 `/token` 只会得到
#: `invalid_grant: Session doesn't have required client` ——
#: 表现就是「自动续签根本没生效，一觉醒来整池 401」。
#:
#: 实测该 client 允许 `offline_access`（登录页 scope=openid offline_access 返回 200），
#: 加上之后就拿到 offline 型 refresh_token（寿命 7 天，`typ=Offline`）。
SCOPE = "openid offline_access"
#: 会话寿命（用户填验证码的时间窗口）
SESSION_TTL = 600.0
#: 短信验证码有效期（上游通常 5 分钟，这里按 4 分钟给前端倒计时）
CODE_TTL = 240.0

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

_lock = threading.Lock()
_sessions: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# HTTP：带 cookie 罐 + 可中断的重定向
# ---------------------------------------------------------------------------
class _StopRedirect(urllib.request.HTTPRedirectHandler):
    """不跟随跳转，把 Location 记下来 —— `code` 就在里面。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        req._captured_location = newurl  # noqa: SLF001
        return None


class _NoFollow(urllib.request.HTTPErrorProcessor):
    def http_response(self, request, response):
        if 300 <= response.status < 400:
            response._captured = response.headers.get("Location", "")  # noqa: SLF001
        return response


class Caller:
    """一次登录会话专用的 HTTP 客户端（自带 cookie 罐）。"""

    def __init__(self) -> None:
        self.jar = urllib.request.HTTPCookieProcessor()
        self._cookie_jar = __import__("http.cookiejar", fromlist=["CookieJar"]).CookieJar()
        handlers = [
            urllib.request.HTTPSHandler(context=_ssl_ctx()),
            urllib.request.HTTPCookieProcessor(self._cookie_jar),
            _StopRedirect(),
        ]
        self._opener = urllib.request.build_opener(*handlers)
        self._opener.addheaders = [("User-Agent", _UA),
                                   ("Accept-Language", "zh-CN,zh;q=0.9")]

    # -- 请求 -------------------------------------------------------------
    def get(self, url: str, *, timeout: float = 25.0) -> tuple[int, str, str]:
        return self._do(urllib.request.Request(url), timeout)

    def post_json(self, url: str, payload: dict, *,
                  timeout: float = 25.0) -> tuple[int, str, str]:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/json"})
        return self._do(req, timeout)

    def post_form(self, url: str, fields: dict, *,
                  timeout: float = 25.0) -> tuple[int, str, str]:
        data = urllib.parse.urlencode(fields).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        return self._do(req, timeout)

    # -- 内部 -------------------------------------------------------------
    def _do(self, req, timeout: float) -> tuple[int, str, str]:
        try:
            with self._opener.open(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", "replace")
                location = getattr(resp, "_captured", "") or \
                    getattr(req, "_captured_location", "") or ""
                return resp.status, body, location
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                pass
            location = getattr(exc, "_captured_location", "") or \
                getattr(exc, "_captured", "") or exc.headers.get("Location", "") or ""
            return exc.code, body, location
        except Exception as exc:  # noqa: BLE001
            return 0, "%s: %s" % (type(exc).__name__, exc), ""

    def cookies(self) -> str:
        return "; ".join("%s=%s" % (c.name, c.value) for c in self._cookie_jar)


def _ssl_ctx():
    import ssl

    return ssl.create_default_context()


# ---------------------------------------------------------------------------
# PKCE
# ---------------------------------------------------------------------------
def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def new_verifier() -> str:
    """43~128 字符（客户端里那条 Rust 断言），72 字节 → 96 字符。"""
    return _b64url(secrets.token_bytes(72))


def challenge_of(verifier: str) -> str:
    return _b64url(hashlib.sha256(verifier.encode()).digest())


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------
@dataclass
class Session:
    auth_id: str
    verifier: str
    state: str
    realm: str
    auth_url: str = ""
    action: str = ""
    caller: Caller = field(default_factory=Caller)
    status: str = "init"          # init | ready | code_sent | done | failed
    detail: str = ""
    expires_at: float = 0.0
    code_sent_at: float = 0.0
    account_id: int = 0

    def public(self) -> dict:
        return {
            "auth_id": self.auth_id,
            "status": self.status,
            "detail": self.detail,
            "auth_url": self.auth_url,
            "redirect_uri": REDIRECT_URI,
            "expires_in": max(0, int(self.expires_at - time.time())),
            "code_retry_in": max(0, int(self.code_sent_at + 60 - time.time()))
            if self.code_sent_at else 0,
            "account_id": self.account_id,
        }


def _cleanup() -> None:
    now = time.time()
    for key in [k for k, v in _sessions.items() if v.expires_at < now - 60]:
        _sessions.pop(key, None)


def begin(realm: str = "cn") -> dict:
    """建会话：拿登录页、解析 form action、生成授权链接。

    返回 `auth_url` 可直接丢给 Qt/浏览器打开；网关自己并不等回调
    （`redirect_uri` 被客户端占着），所以真正的收码走 `submit()`。
    """
    _cleanup()
    verifier = new_verifier()
    state = _b64url(secrets.token_bytes(24))
    auth_base = endpoints.auth_of(realm) + "/auth"
    query = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": CLIENT_ID,
        "state": state,
        "code_challenge": challenge_of(verifier),
        "code_challenge_method": "S256",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
    })
    auth_url = auth_base + "?" + query

    caller = Caller()
    status, html, _ = caller.get(auth_url)
    session = Session(auth_id=secrets.token_urlsafe(18), verifier=verifier,
                      state=state, realm=realm, auth_url=auth_url, caller=caller,
                      expires_at=time.time() + SESSION_TTL)
    if status != 200:
        session.status = "failed"
        session.detail = "登录页打不开（%s）：%s" % (status, html[:160])
        with _lock:
            _sessions[session.auth_id] = session
        return session.public()

    action = _form_action(html)
    if not action:
        session.status = "failed"
        session.detail = "登录页里没找到 kc-form-login 的 action（上游改版？）"
    else:
        session.action = action
        session.status = "ready"
    with _lock:
        _sessions[session.auth_id] = session
    logger.info("KroWork 授权会话已建：%s status=%s", session.auth_id, session.status)
    return session.public()


def _form_action(html: str) -> str:
    import re

    m = re.search(r'<form[^>]*id="kc-form-login"[^>]*action="([^"]+)"', html)
    if not m:
        m = re.search(r'<form[^>]*action="([^"]*authenticate[^"]*)"', html)
    return m.group(1).replace("&amp;", "&") if m else ""


def get_session(auth_id: str) -> Session | None:
    with _lock:
        return _sessions.get(auth_id)


def cancel(auth_id: str) -> dict:
    with _lock:
        session = _sessions.pop(auth_id, None)
    if session is None:
        return {"ok": False, "detail": "会话不存在"}
    return {"ok": True, "auth_id": auth_id}


# ---------------------------------------------------------------------------
# 发码
# ---------------------------------------------------------------------------
def _sms_base(realm: str) -> str:
    # SMS 端点挂在 realm 下，不在 openid-connect 路径里
    return endpoints.auth_of(realm).rsplit("/protocol/", 1)[0]


def send_code(auth_id: str, phone: str, country: str = "+86") -> dict:
    """发短信验证码（`POST /realms/krow/sms/send-code`）。"""
    session = get_session(auth_id)
    if session is None:
        return {"ok": False, "detail": "会话不存在，请重新开始授权"}
    phone = (phone or "").strip()
    if not phone:
        return {"ok": False, "detail": "请填写手机号"}
    url = _sms_base(session.realm) + "/sms/send-code"
    status, body, _ = session.caller.post_json(url, {
        "phoneNumber": phone, "countryCode": country, "scene": "LOGIN"})
    try:
        data = json.loads(body) if body.strip().startswith("{") else {}
    except ValueError:
        data = {}
    if status == 200 and data.get("success"):
        session.status = "code_sent"
        session.code_sent_at = time.time()
        session.detail = "验证码已发送"
        return {"ok": True, "detail": "验证码已发送", "auth_id": auth_id,
                "retry_in": 60}
    mapping = {
        "userNotFound": "该手机号未注册（请先在客户端注册）",
        "smsTooFast": "发送太频繁，请稍后再试",
        "invalidPhoneNumber": "手机号格式不正确",
        "missingPhoneNumber": "请输入手机号",
    }
    code = str(data.get("errorCode") or "")
    detail = mapping.get(code) or data.get("message") or body[:160] or "发送失败"
    session.detail = detail
    return {"ok": False, "detail": detail, "error_code": code, "auth_id": auth_id}


# ---------------------------------------------------------------------------
# 交码 + 换票
# ---------------------------------------------------------------------------
def submit(auth_id: str, code: str, phone: str = "", country: str = "+86") -> dict:
    """校验验证码 → 建立会话 → 拦 302 取 code → 换 token → 入号池。"""
    session = get_session(auth_id)
    if session is None:
        return {"ok": False, "detail": "会话不存在，请重新开始授权"}
    if session.status == "done":
        return {"ok": True, "detail": "该会话已完成", "account_id": session.account_id}
    code = (code or "").strip()
    if not code:
        return {"ok": False, "detail": "请填写验证码"}

    base = _sms_base(session.realm)

    # 1) 校验证码（这步只校验，不登录；失败能给出明确原因）
    status, body, _ = session.caller.post_json(base + "/sms/verify", {
        "phoneNumber": phone, "countryCode": country, "code": code,
        "scene": "LOGIN"})
    try:
        verified = json.loads(body) if body.strip().startswith("{") else {}
    except ValueError:
        verified = {}
    if status != 200 or not verified.get("success"):
        detail = verified.get("message") or "验证码错误或已过期"
        return {"ok": False, "detail": detail, "stage": "verify"}

    # 2) 提交表单建立会话（字段名与登录页 JS 逐字一致）
    if not session.action:
        return {"ok": False, "detail": "会话缺少 form action，请重新开始授权",
                "stage": "form"}
    status, body, location = session.caller.post_form(session.action, {
        "phoneNumber": country + phone,
        "smsCode": code,
        "action": "smsLogin",
    })
    auth_code = _code_from(location)
    if not auth_code:
        # 有的部署会直接 200 回登录页（验证失败但仍 200）
        session.detail = ("没拿到授权码（status=%s，location=%s）"
                          % (status, location[:120]))
        return {"ok": False, "detail": "验证码被拒或会话过期，请重试",
                "stage": "authenticate", "upstream": body[:200]}

    # 3) 换 token（PKCE：必须带 verifier）
    token_url = endpoints.auth_of(session.realm) + "/token"
    status, body, _ = session.caller.post_form(token_url, {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "code": auth_code,
        "code_verifier": session.verifier,
        "redirect_uri": REDIRECT_URI,
    })
    if status != 200:
        session.status = "failed"
        session.detail = "换票失败（%s）：%s" % (status, body[:200])
        return {"ok": False, "detail": session.detail, "stage": "token"}
    try:
        payload = json.loads(body)
    except ValueError:
        session.status = "failed"
        session.detail = "换票返回不是 JSON：%s" % body[:160]
        return {"ok": False, "detail": session.detail, "stage": "token"}

    id_token = str(payload.get("id_token") or "")
    refresh = str(payload.get("refresh_token") or "")
    if not id_token:
        session.status = "failed"
        session.detail = "换票成功但响应里没有 id_token"
        return {"ok": False, "detail": session.detail, "stage": "token"}

    session.status = "done"
    session.detail = "登录成功"
    token_mod.STORE.invalidate()

    # 4) 入号池（先验证再入库的规矩在这里同样适用：能换到票就说明票可用）
    account_id = 0
    try:
        from admin.db import SessionLocal
        from admin.platform.krowork.services import accounts as ka

        with SessionLocal() as db:
            account, _created = ka._upsert(db, {
                "id_token": id_token,
                "refresh_token": refresh,
                "expires_at": int(token_mod.token_exp(id_token) or 0),
                "source": "sms-login",
                "region": session.realm,
            })
            db.commit()
            account_id = int(account.id or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("KroWork 授权成功但入库失败：%s", exc)
        session.detail = "登录成功，但写库失败：%s" % exc

    session.account_id = account_id
    claims = token_mod.jwt_payload(id_token)
    return {
        "ok": True,
        "detail": "登录成功",
        "auth_id": auth_id,
        "account_id": account_id,
        "sub": str(claims.get("sub") or "")[:8] + "…",
        "email": claims.get("preferred_username") or claims.get("email") or "",
        "expires_at": int(token_mod.token_exp(id_token) or 0),
        "token": token_mod.status(),
    }


def _code_from(location: str) -> str:
    if not location:
        return ""
    try:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)
    except Exception:  # noqa: BLE001
        return ""
    return str((query.get("code") or [""])[0])


# ---------------------------------------------------------------------------
# 手动粘贴回调 / 授权码（客户端已经登过，只想导入）
# ---------------------------------------------------------------------------
def redeem(callback_url: str, verifier: str, realm: str = "cn") -> dict:
    """用「回调 URL + verifier」换票（用户手动走完浏览器流程时用）。

    `callback_url` 可以是完整的 `http://127.0.0.1:17321/callback?code=…&state=…`；
    只给 `code` 也行。
    """
    code = callback_url.strip()
    if "code=" in code:
        code = _code_from(code)
    if not code:
        return {"ok": False, "detail": "没有解析出 code"}
    if not verifier:
        return {"ok": False, "detail": "缺少 code_verifier（PKCE 必须）"}
    caller = Caller()
    status, body, _ = caller.post_form(endpoints.auth_of(realm) + "/token", {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": REDIRECT_URI,
    })
    if status != 200:
        return {"ok": False, "detail": "换票失败（%s）：%s" % (status, body[:200])}
    payload = json.loads(body)
    id_token = str(payload.get("id_token") or "")
    if not id_token:
        return {"ok": False, "detail": "响应里没有 id_token"}
    token_mod.STORE.invalidate()
    return {"ok": True, "id_token": id_token,
            "refresh_token": payload.get("refresh_token") or "",
            "expires_at": int(token_mod.token_exp(id_token) or 0)}
