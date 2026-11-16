"""腾讯 Marvis HTTP 客户端。

## 四件事必须做对

1. 错误分类（`kind` 决定上层是冷却、禁用还是换号）
2. 网络异常上报哨兵（连续失败 = 平台不可用）
3. 不要静默吞异常（否则「查不到」和「没有」分不清）
4. **上游会话 id 必须稳定派生**：用 `session_id_for()`（内部走
   `pool.stable_session_id`），从下游真实会话标识按「账号 + 对话」算出，
   同一账号同一对话恒定。**绝不许 `uuid4()`** —— 每请求换 id 会让上游
   前缀缓存永远打不中，还会让一个号几分钟内冒出几十个会话（风控特征）。
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from . import endpoints

logger = logging.getLogger("admin.platform.marvis.client")
DEFAULT_TIMEOUT = 30.0

#: 哪些 kind 要上报哨兵（区分「账号问题」与「实现问题」）
SENTINEL_KINDS = frozenset({"auth", "quota_exceeded", "network",
                             "signature_invalid", "upstream_business"})


class UpstreamError(Exception):
    def __init__(self, message: str, *, status: int = 0, code: str = "",
                 kind: str = "http_error", detail: str = ""):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.kind = kind
        self.detail = detail

    def retryable(self) -> bool:
        return self.status in (408, 429) or self.status >= 500

    def to_dict(self) -> dict:
        return {"message": self.message, "status": self.status,
                "code": self.code, "kind": self.kind, "detail": self.detail[:300]}


def note_sentinel(realm: str, account_id: int, kind: str, *,
                  code: str = "", status: int = 0, detail: str = "") -> None:
    """上报风控哨兵。**绝不抛异常**（哨兵坏了不能阻断转发）。"""
    if kind not in SENTINEL_KINDS:
        return
    try:
        from admin import sentinel

        sentinel.note_failure("marvis", int(account_id or 0), kind,
                              code=str(code or ""), status=int(status or 0),
                              detail=str(detail or "")[:200])
    except Exception:  # noqa: BLE001
        logger.debug("哨兵上报失败 kind=%s", kind, exc_info=True)


def session_id_for(account_id: int, body: dict, *, prefix: str = "",
                   size: int = 16) -> str:
    """**稳定派生**的上游会话 id —— 不要在这里用 `uuid4()`。

    规则见 `admin/pool.py::stable_session_id`：`sha256(平台 | 账号 | 会话键)`。
    会话键优先取客户端自己的会话标识，没有就取首条 user 文本的哈希 ——
    两种都是**确定性**的，所以同一段对话永远得到同一个 id。

    为什么必须这样：
      * 上游前缀缓存按会话算，id 每换一次就是一次全新对话（首字慢、费用高、
        面板命中率永远是 0）；
      * 一个号短时间内"新建"大量会话，是风控最敏感的形状。

    注意：`MsgId` / `TurnId` / `requestId` 是**另一回事**，它们必须每次唯一
    （上游拿它们去重，复用会导致少记账）。
    """
    try:
        from admin import pool

        return pool.stable_session_id("marvis", int(account_id or 0), body,
                                      prefix=prefix, size=size)
    except Exception:  # noqa: BLE001 - 派生失败退回空串，不能挡住调用
        logger.debug("session id 派生失败", exc_info=True)
        return ""


def _request(url: str, *, method: str = "GET", body: dict | None = None,
             raw_body: str | None = None,
             headers: dict | None = None, timeout: float = DEFAULT_TIMEOUT,
             account_id: int = 0, realm: str = "") -> dict:
    """发一个 JSON 请求。

    `raw_body` 与 `body` 二选一 —— 需要签名的接口（`/v3/*`）必须用 `raw_body`：
    签名对象是**实际发出的那串字节**，先序列化再签名和先签名再序列化，
    只要有一个空格或键序不同，上游就判签名错误。
    """
    if raw_body is not None:
        data = raw_body.encode("utf-8")
    else:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=data, method=method.upper())
    for key, value in (headers or {}).items():
        req.add_header(str(key), str(value))
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", "replace")
            return json.loads(text) if text.strip() else {}
    except urllib.error.HTTPError as exc:
        payload = ""
        try:
            payload = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            pass
        kind = ("auth" if exc.code in (401, 403)
                else "rate_limit" if exc.code == 429
                else "upstream_error" if exc.code >= 500 else "http_error")
        note_sentinel(realm, account_id, kind, code=str(exc.code),
                      status=exc.code, detail=payload)
        raise UpstreamError("%s失败（HTTP %d）" % (method, exc.code),
                            status=exc.code, code=str(exc.code), kind=kind,
                            detail=payload) from exc
    except Exception as exc:  # noqa: BLE001
        note_sentinel(realm, account_id, "network", detail=str(exc)[:200])
        raise UpstreamError("网络失败", status=0, kind="network",
                            detail=str(exc)[:200]) from exc


# ---------------------------------------------------------------------------
# 具体接口（Marvis 上游）
# ---------------------------------------------------------------------------
#: 模型网关的 SSE 读超时：一次长回答可能要几分钟，不能按普通接口的 30s 算。
STREAM_TIMEOUT = 300.0


def _acct(credential: dict) -> dict:
    """把落库的凭证规整成 endpoints 期望的形状（snake_case）。"""
    data = credential or {}
    return {
        "openid": str(data.get("openid") or data.get("openId") or ""),
        "access_token": str(data.get("access_token") or data.get("accessToken") or ""),
        "guid": str(data.get("guid") or ""),
        "login_type": str(data.get("login_type") or data.get("loginType") or "1"),
        "client_version": str(data.get("client_version") or ""),
    }


def fetch_wallet(credential: dict, realm: str = "cn", *, account_id: int = 0) -> dict:
    """令牌钱包 —— 卡片上的额度来源。

    上游 `POST /v3/marvis_get_wallet`，返回 `{data: {total_tokens, used_tokens,
    frozen_tokens, avail_tokens, total_cost, status}}`。

    ⚠ 签名对象是**请求体原文**（这里是 `{}`），不是 URL。
    """
    acct = _acct(credential)
    body_text = "{}"
    data = _request(
        endpoints.wallet_url(realm),
        method="POST",
        raw_body=body_text,
        headers=endpoints.business_headers(acct, query=body_text, method="POST"),
        realm=realm, account_id=account_id)
    inner = data.get("data") if isinstance(data, dict) else None
    if not isinstance(inner, dict):
        # ⚠ **`data: null` 是登录态失效的信号，不是「业务失败」**
        # （2026-11-15 真机实测）：access_token 过期后，钱包接口仍然回
        # `{"code": 0, "msg": "success", "data": null}` —— HTTP 200、业务码 0，
        # 唯一异常是 data 为空。同一份凭证打模型网关会明确回
        # `4100403 登录态过期/未登录`，两条路径指同一件事。
        #
        # 以前这里按 `upstream_business` 归类，后果有二：
        #   · 账号不会被标成「凭证失效」，控制台只显示「钱包接口没有返回 data
        #     字段」——用户完全不知道要去重新扫码；
        #   · 网关到最后仍然报「没有可用账号，请先在控制台导入凭证」，
        #     而账号明明是 active、凭证也在，只是过期了。
        # 现在归成 `auth`：上层据此冷却 + 控制台显示「登录态已过期」。
        raise UpstreamError(
            "Marvis 登录态已过期（钱包接口无数据），请在控制台重新扫码登录",
            status=401, kind="auth",
            detail=json.dumps(data, ensure_ascii=False)[:200])
    return inner


def open_chat_stream(credential: dict, realm: str, body: dict, *,
                     account_id: int = 0):
    """打开模型网关的 SSE 流，**原样**返回 `HTTPResponse` 交给上层转发。

    ⚠ 不要在这里读流：读干了上层就没东西可转发（插件侧踩过的同一个坑）。
    上层负责 finally 调 `close_stream()`。
    """
    acct = _acct(credential)
    url = endpoints.chat_url(realm)
    data = json.dumps(body, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    for key, value in endpoints.llm_headers(acct).items():
        req.add_header(str(key), str(value))
    try:
        return urllib.request.urlopen(req, timeout=STREAM_TIMEOUT)
    except urllib.error.HTTPError as exc:
        payload = ""
        try:
            payload = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            pass
        kind = _classify(exc.code, payload)
        note_sentinel(realm, account_id, kind, code=str(exc.code),
                      status=exc.code, detail=payload)
        raise UpstreamError("模型网关失败（HTTP %d）" % exc.code,
                            status=exc.code, code=str(exc.code), kind=kind,
                            detail=payload) from exc
    except Exception as exc:  # noqa: BLE001
        note_sentinel(realm, account_id, "network", detail=str(exc)[:200])
        raise UpstreamError("模型网关网络失败", status=0, kind="network",
                            detail=str(exc)[:200]) from exc


def close_stream(resp) -> None:
    """关掉 SSE 连接。**绝不抛**（已经在 finally 里）。"""
    try:
        if resp is not None:
            resp.close()
    except Exception:  # noqa: BLE001
        pass


def _classify(status: int, text: str) -> str:
    """把上游错误归成一类 —— 上层据此决定冷却 / 禁用 / 换号。

    ⚠ 风控（403 + 4100404）与登录失效（401）必须分开：前者是**设备/IP**
    的问题，换号没用还会把好号一起打挂；后者才是账号问题。
    """
    blob = (text or "").lower()
    if "4100404" in blob or "risk" in blob or "风控" in blob:
        return "risk_control"
    if status == 401:
        return "auth"
    if status == 403:
        # 403 但没有风控码 —— 签名或参数问题
        return "signature_invalid"
    if status == 429:
        return "rate_limit"
    if status >= 500:
        return "upstream_error"
    return "http_error"


def chat_completions(credential: dict, realm: str, body: dict, *,
                     account_id: int = 0) -> dict:
    """非流式对话（上层通常走流式；这里给健康检查与单发用）。"""
    stream = open_chat_stream(credential, realm, body, account_id=account_id)
    try:
        text = stream.read().decode("utf-8", "replace")
        return json.loads(text) if text.strip() else {}
    finally:
        close_stream(stream)


def list_models(_credential: dict | None = None, realm: str = "") -> list[dict]:
    """模型目录。

    Marvis 的模型网关**没有** `/v1/models`（实测 404/无响应），目录是客户端
    内置的槽位表 —— 所以这里返回静态目录，不猜上游。
    """
    out = []
    for item in endpoints.models_catalog(realm):
        out.append({
            "id": item["name"],
            "object": "model",
            "created": 0,
            "owned_by": "marvis",
            "name": item["name"],
            "api_id": item["api_id"],
            "context": item["context"],
        })
    return out


def refresh_credentials(refresh_token: str, realm: str = "cn", *,
                        account_id: int = 0, credential: dict | None = None) -> dict:
    """续签登录态。

    ## 接口事实（2026-11-16 真机复核，见 issue #41）

    路径 `POST /marvis_client_login/marvis_refresh_token`（**POST，不是 GET**）：

      · GET 该路径 → 恒 400 空体（不带签名头）或 200 + `-109`（带签名头）；
      · POST 该路径 → 200 + 业务码，说明**方法确实是 POST**。
        这一条是 issue #41 纠正对了的地方，此前的端点表和注释都写成了 GET。

    ## 仍然没打通的部分（不要假装它能用）

    参数该放哪儿**目前没有找到**。真机穷举过这些位置，全部回
    `-109 RefreshToken missing required param (openid, guid, token)`：

      · body：`{}` / `{openid,guid,token}` / `{openid,guid,refreshToken}` /
        驼峰全量 userInfo / `{userInfo:{…}}` / `{data:{…}}` / 表单编码
      · query：`?openid&guid&token`（带签名则 403，签名口径不适用于此路径）
      · header：`openid/guid/token` 裸名、`Ual-Access-*` 全套（含 Guid）

    也就是说 issue 里「POST 带完整 userInfo body 返回 `-107`」这条**未能复现**，
    「`-109` 是漏了 `Ual-Access-Guid` 头」也不成立 —— 带着该头同样 `-109`。
    `-107 RC_NO_AUTH_ERR` 的语义（凭证无效）本身合理，只是本机走不到那一步。

    所以这里**不做假实现**：真的发出 POST，把上游原文带回给调用方；
    只有拿到 `user_info` 才认为续签成功。失败时抛出可操作的错误，
    而不是返回一份伪造的凭证。
    """
    data = dict(credential or {})
    openid = str(data.get("openid") or "").strip()
    guid = str(data.get("guid") or "").strip()
    token = str(data.get("access_token") or "").strip()
    refresh = str(refresh_token or data.get("refresh_token") or "").strip()
    if not (openid and guid and token):
        raise UpstreamError(
            "续签需要完整凭证（openid + guid + access_token），"
            "只有 refresh_token 换不出来",
            status=401, kind="auth")

    body = json.dumps({
        "openid": openid,
        "guid": guid,
        "token": token,
        "refreshToken": refresh,
        "loginType": endpoints.LOGIN_TYPE.get(
            str(data.get("login_type") or "WX").upper(), "2"),
    }, ensure_ascii=False, separators=(",", ":"))

    headers = endpoints.business_headers({**data, "access_token": token},
                                         query=body, method="POST")
    headers["Ual-Access-Guid"] = guid

    payload = _request(endpoints.refresh_url(realm), method="POST",
                       raw_body=body, headers=headers, realm=realm,
                       account_id=account_id)
    inner = payload.get("user_info") if isinstance(payload, dict) else None
    if isinstance(inner, dict) and (inner.get("openid") or inner.get("accessToken")
                                    or inner.get("access_token")):
        return inner
    code = payload.get("code") if isinstance(payload, dict) else None
    msg = str((payload or {}).get("msg") or "") if isinstance(payload, dict) else ""
    raise UpstreamError(
        "Marvis 续签未成功（上游 code=%s %s）——该接口的参数约定尚未打通，"
        "请重新扫码登录或导入刷新后的凭证" % (code, msg),
        status=401, code=str(code or ""), kind="auth",
        detail=json.dumps(payload, ensure_ascii=False)[:200])


def exchange_code(code: str, login_type: str, realm: str = "cn", *,
                  state: str = "", guid: str = "") -> dict:
    """用授权码换登录态。

    ⚠ 三个必须做对的细节（插件侧实测）：
      · `query` 里**只能有** `login_type` / `code` / `state`，多一个参数就 -109；
      · 成功形态是 **302 + Set-Cookie**，跟随重定向等于把登录态扔掉；
      · 请求必须带 `Ual-Access-Guid`，不带一律 -109。
    """
    if not code:
        raise UpstreamError("没有授权码", kind="auth")
    if not guid:
        raise UpstreamError("缺少设备 guid（不带它上游一律 -109）", kind="auth")

    from urllib.parse import urlencode

    params = {"login_type": login_type, "code": code}
    if state:
        params["state"] = str(state)
    url = endpoints.oauth_url(realm) + "?" + urlencode(params)

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):  # noqa: D401
            return None

    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, method="GET")
    req.add_header("Accept",
                   "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8")
    req.add_header("User-Agent", endpoints.BROWSER_UA)
    req.add_header("Ual-Access-Guid", str(guid))
    try:
        resp = opener.open(req, timeout=DEFAULT_TIMEOUT)
    except urllib.error.HTTPError as exc:
        # 302 在 urllib 里会以 HTTPError 抛出 —— 这里正是要它的 Set-Cookie
        resp = exc
    except Exception as exc:  # noqa: BLE001
        raise UpstreamError("换取登录态失败", kind="network",
                            detail=str(exc)[:200]) from exc

    cookies = _cookies_of(resp)
    body_text = ""
    if getattr(resp, "status", 0) != 302:
        try:
            body_text = resp.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            body_text = ""

    info = {}
    if body_text.strip():
        try:
            payload = json.loads(body_text)
        except Exception:  # noqa: BLE001
            payload = {}
        info = payload.get("user_info") or payload.get("data") or payload or {}

    if not isinstance(info, dict):
        info = {}

    # ---- 上游错误码 → 人话（2026-11-15 真机逐条对照出来的）----
    #
    #   -109 RC_PARAMS_INVALID : **缺 `Ual-Access-Guid` 请求头**（或缺
    #       login_type / code）。浏览器直接打开 `marvis_oauth?code=…` 时
    #       就是这个 —— 浏览器没有 guid。**这一步不会消耗 code**（参数校验在
    #       兑换之前），所以「浏览器显示 -109」是正常噪音，不影响我们兑换。
    #   -107 RC_NO_AUTH_ERR   : 参数齐全但 code 无效 —— 已过期或已被用过。
    #       QQ/微信的 code 只有几分钟寿命，必须马上提交。
    #
    # 这两条以前都被压成一句「没有下发 accesstoken」，用户完全不知道该干嘛。
    biz_code = 0
    if isinstance(info, dict) and info.get("code") is not None:
        try:
            biz_code = int(info.get("code"))
        except (TypeError, ValueError):
            biz_code = 0
    if biz_code == -109:
        raise UpstreamError(
            "上游判参数无效（-109）：这是浏览器直接打开回调页时的正常提示，"
            "不影响提交；请确认你贴的是完整回调地址（含 code=…），并在授权后 1 分钟内提交。",
            status=400, code="-109", kind="auth",
            detail=body_text[:200])
    if biz_code == -107:
        raise UpstreamError(
            "授权码已过期或已被使用（-107）：QQ / 微信的 code 只有几分钟有效期。"
            "请重新扫码，拿到回调地址后立刻贴进来提交（别再刷新那个页面）。",
            status=400, code="-107", kind="auth",
            detail=body_text[:200])
    if biz_code not in (0, 200):
        raise UpstreamError(
            "上游返回错误（%s）：%s" % (biz_code,
                                   str(info.get("msg") or info.get("message") or "")[:120]),
            status=400, code=str(biz_code), kind="auth", detail=body_text[:200])

    # ⚠ 字段名是**小写无下划线**：上游实测返回
    #   `{"openid": "...", "accesstoken": "mv_...", "refreshtoken": "...",
    #     "expires_in": "5184000", "appid": "1903794758"}`
    #   只认 `access_token` 会把一次成功的登录判成失败（凭证其实已经发下来了）。
    access = str(info.get("accesstoken") or info.get("access_token")
                 or info.get("accessToken") or cookies.get("accesstoken")
                 or cookies.get("access_token") or "")
    openid = str(info.get("openid") or info.get("open_id")
                 or info.get("openId") or cookies.get("openid") or "")
    refresh = str(info.get("refreshtoken") or info.get("refresh_token")
                  or info.get("refreshToken") or cookies.get("refreshtoken")
                  or cookies.get("refresh_token") or "")
    if not access or not openid:
        raise UpstreamError(
            "登录接口没有下发 accesstoken / openid",
            kind="auth",
            detail=(body_text or str(cookies))[:200])

    # `expires_in` 是**相对秒数**（QQ 给 5184000 = 60 天）。直接当绝对时间戳用
    # 会得到一个 1970 年的过期时间，下次选号立刻判死。
    expires_at = 0
    raw_expires = info.get("expires_in") or info.get("expiresIn")
    try:
        if raw_expires is not None:
            seconds = float(raw_expires)
            # 大于 1e9 的视为已经是绝对时间戳（秒），否则按相对秒数换算
            expires_at = int(seconds) if seconds > 1e9 else int(time.time() + seconds)
    except (TypeError, ValueError):
        expires_at = 0
    if not expires_at:
        raw_at = info.get("expires_at") or info.get("expiresAt")
        try:
            expires_at = int(float(raw_at)) if raw_at else 0
        except (TypeError, ValueError):
            expires_at = 0

    return {
        "openid": openid,
        "access_token": access,
        "refresh_token": refresh,
        "nickname": str(info.get("nick_name") or info.get("nickname") or ""),
        "avatar": str(info.get("head_img_url") or ""),
        "login_type": endpoints.LOGIN_TYPE.get(
            str(info.get("login_type") or "").upper(), login_type),
        "guid": str(guid),
        "expires_at": expires_at,
        "appid": str(info.get("appid") or ""),
    }


def _cookies_of(resp) -> dict:
    """从响应头里抠出所有 Set-Cookie 的 k=v（不分域，Marvis 只在一个 host 上）。"""
    out: dict[str, str] = {}
    try:
        headers = getattr(resp, "headers", None)
        if headers is None:
            return out
        raw = headers.get_all("Set-Cookie") if hasattr(headers, "get_all") else None
        items = raw or ([headers.get("Set-Cookie")] if headers.get("Set-Cookie") else [])
    except Exception:  # noqa: BLE001
        return out
    for item in items:
        for piece in str(item).split(","):
            head = piece.split(";")[0].strip()
            if "=" not in head:
                continue
            key, _, value = head.partition("=")
            key = key.strip()
            if key:
                out[key] = value.strip()
    return out
