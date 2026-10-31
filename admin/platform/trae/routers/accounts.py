"""Trae 后台管理接口：`/api/trae/*`。

与 `admin/routers/accounts.py` 的分工：那边是 WorkBuddy 专属（.info 凭据、
手机号登录、成长计划等），这边只保留 Trae 用得上的能力，并且**写同一张
`accounts` 表**（`platform='trae'`）——因此用量、积分、Key 绑定这些模块
不需要任何改动就能直接统计到 Trae 的号。
"""
from __future__ import annotations

import json
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from admin.db import get_db
from admin.models import Account, AccountModelCooldown
from admin.platform.registry import get as get_platform
from admin.security import require_admin
from admin.platform.trae.core import catalog, client
from admin.platform.trae.services import accounts as trae_accounts
from admin.platform.trae.services import desktop_catalog
from admin.platform.trae.services import pool as trae_pool

logger = logging.getLogger("admin.platform.trae.routers")

router = APIRouter(prefix="/api/trae", tags=["trae"])

PLATFORM = "trae"

#: 对外平台 id -> 存储区域。国内版/国际版现在是两个平台，
#: 但后台接口一直接受 `realm=` 参数，因此这里做一层映射，
#: 让「按平台调用」与「按区调用」两种写法都能用。
PLATFORM_REALMS = {"trae-cn": "cn", "trae-intl": "intl"}

#: 存储层的两个区域。**不要**从 `spec.realm_keys()` 推：单区平台的
#: `realms` 是空元组（区域放在 `spec.realm` 里），推出来会是空的。
REALM_KEYS = ("cn", "intl")


def _spec():
    """平台声明。国内版/国际版共用同一套实现，取任意一个即可。"""
    return get_platform("trae-cn") or get_platform(PLATFORM)


def _realm_or_400(value: str | None) -> str:
    """把任意写法归一成存储用区域（`cn` / `intl`）。

    必须用**无固定区域**的规范化逻辑：`_spec()` 取到的是 `trae-cn`，
    而它的 `normalize_realm()` 因为 `spec.realm` 固定而**永远返回 cn** ——
    用它归一 `intl` 会得到 `cn`，表现为「切到国际版看的还是国内版模型」。
    这里直接按存储区域白名单判断。
    """
    raw = str(value or "").strip().lower()
    if raw in PLATFORM_REALMS:          # trae-cn / trae-intl
        return PLATFORM_REALMS[raw]
    if raw in ("cn", "china", "zh", "国内", "国内版", "中国"):
        return "cn"
    if raw in ("intl", "us", "global", "sg", "国际", "国际版", "oversea", "overseas"):
        return "intl"
    return "cn"


# ---------------------------------------------------------------------------
# 平台信息
# ---------------------------------------------------------------------------
@router.get("/meta")
def meta(_: bool = Depends(require_admin)):
    """平台能力声明：控制台切换条与各面板用它决定展示什么。"""
    spec = _spec()
    if spec is None:
        raise HTTPException(503, "trae 平台未登记")
    return {
        "id": spec.id,
        "label": spec.label,
        "realms": [{"key": r.key, "label": r.label, "default": r.default}
                   for r in spec.realms],
        "supports_local_scan": spec.supports_local_scan,
        "notes": spec.notes,
        # 授权形态声明：PKCE + 浏览器回调带 AuthCode 回来。
        # 桌面版回调走本机端口；服务器部署必须配 WB_OAUTH_PUBLIC_BASE，
        # 否则授权完成后浏览器会跳到用户自己电脑而打不开。
        "auth_mode": "callback",
        "auth_explain": ("Trae 用浏览器授权 + 回调：桌面版回调走本机；"
                         "服务器部署需设置 WB_OAUTH_PUBLIC_BASE=https://你的域名，"
                         "否则授权完成后跳转会失败。"),
        "endpoints": sorted(client.REALM_PATHS) if hasattr(client, "REALM_PATHS")
        else ["/v1/chat/completions", "/v1/responses", "/v1/messages",
              "/v1/messages/count_tokens", "/v1/models", "/v1/balance"],
        "storage": trae_accounts.appdata_hint(),
    }


# ---------------------------------------------------------------------------
# 账号
# ---------------------------------------------------------------------------
@router.get("/accounts")
def list_accounts(realm: str = "", _: bool = Depends(require_admin),
                  db: Session = Depends(get_db)):
    """号池列表（含额度与到期快照）。"""
    realm = _realm_or_400(realm) if realm else ""
    rows = trae_pool.accounts_for(db, realm)
    items = []
    for row in rows:
        view = trae_accounts.public_view(row)
        # 额度口径与快照一起回给前端：国内版是积分，国际版是请求额度百分比，
        # 两者混显示会让用户以为「国际版只剩 100 分」。
        quota = _quota_view(row)
        items.append({
            "id": row.id,
            "name": row.name,
            "uid": row.uid,
            "status": row.status,
            "realm": row.domain or view["realm"],
            "balance_total": int(row.balance_total or 0),
            "balance_remain": int(row.balance_remain or 0),
            "balance_unit": quota.get("unit") or "credit",
            "quota": quota,
            "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
            "last_sync_at": row.last_sync_at.isoformat() if row.last_sync_at else None,
            "cool_until": row.cool_until.isoformat() if row.cool_until else None,
            "cool_kind": row.cool_kind or "",
            "last_err_msg": row.last_err_msg or "",
            **view,
        })
    return {"items": items, "summary": trae_pool.summarize(rows)}


def _quota_view(row: Account) -> dict:
    try:
        snapshot = json.loads(row.credits_snapshot or "{}")
    except ValueError:
        snapshot = {}
    if not isinstance(snapshot, dict):
        snapshot = {}
    return {
        "unit": snapshot.get("unit") or "credit",
        "plan": snapshot.get("plan") or "",
        "total": snapshot.get("total"),
        "remain": snapshot.get("remain"),
        "limits": snapshot.get("limits") or {},
    }


@router.get("/scan-local")
def scan_local(_: bool = Depends(require_admin)):
    """扫描本机 Trae 客户端登录态（国内版 + 国际版）。"""
    result = trae_accounts.discover()
    result["storage"] = trae_accounts.appdata_hint()
    return result


# ---------------------------------------------------------------------------
# 浏览器授权登录（PKCE）
# ---------------------------------------------------------------------------
@router.post("/device/start")
def device_start(realm: str = "cn", request: Request = None,
                 _: bool = Depends(require_admin),
                 db: Session = Depends(get_db)):
    """起一次浏览器授权：返回授权链接，用户点开登录即可。

    Trae 的登录态存在 `storage.json` 里且加密，只能靠本机客户端读；
    但官方支持标准 PKCE 授权链（见 `core/oauth.py`），
    走这条链就不需要用户手动贴 Cookie。
    """
    from admin.platform.trae.core import oauth as trae_oauth

    value = str(realm or "cn").strip().lower()
    if value not in ("cn", "intl"):
        raise HTTPException(400, detail="realm 只能是 cn 或 intl")

    # **优先用本机客户端真实的 device_id / machine_id**：
    # 上游会把它们和账号绑定，用假值虽然也能进授权页，
    # 但换 token 后可能被判「异常设备」。扫一下本机凭证即可拿到。
    device_id = machine_id = ""
    try:
        from admin.platform.trae.services import desktop_catalog  # noqa: F401
        from admin.platform.trae.core import client as trae_client

        rows = (db.query(Account)
                .filter(Account.platform == PLATFORM,
                        Account.status == "active").all())
        for row in rows:
            data = trae_client.as_mapping(row.auth_json or "{}")
            dev = str(data.get("device_id") or "").strip()
            mid = str(data.get("machine_id") or "").strip()
            # **只要求 device_id**：很多账号只存了它、没有 machine_id，
            # 之前要求两者都有，结果永远拿不到真实值、退回假指纹。
            if dev:
                device_id = dev
                machine_id = mid          # 可能为空，交给 oauth 用默认值
                break
    except Exception:  # noqa: BLE001 - 拿不到就用默认值
        logger.debug("读取本机设备指纹失败", exc_info=True)

    try:
        session = trae_oauth.start(value, device_id=device_id,
                                   machine_id=machine_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, detail="开授权会话失败：%s" % str(exc)[:160]) from exc
    payload = session.as_dict()
    # 把部署形态一并告诉前端：服务器部署且没配公网基址时，
    # 授权链接里的回调是 127.0.0.1 —— 用户浏览器在别的机器上，跳回来必然失败。
    # 与其让他授权完看着「打不开」，不如在点之前就提示。
    callback = payload.get("callback_url") or ""
    loopback = callback.startswith("http://127.0.0.1") or callback.startswith("http://localhost")
    # 回环地址在两种场景下都**没问题**：
    #   · 桌面版：浏览器和网关同机
    #   · 管理员用本机浏览器打开 localhost 的管理台（源码部署也常这样）
    # 只有「从**别的机器**的浏览器访问管理台」时，回环才会断开 ——
    # 这一点用请求来源判断才是准的，不能只看有没有配 WORKBUDDY_DESKTOP。
    from_remote = False
    try:
        host = (request.client.host or "") if request is not None else ""
        from_remote = host not in ("127.0.0.1", "::1", "localhost", "")
    except Exception:
        from_remote = False
    payload["callback_reachable"] = (not loopback) or not from_remote
    payload["callback_hint"] = (
        "" if payload["callback_reachable"]
        else "当前回调地址是本机回环（%s），而你是从别的机器打开的管理台 ——"
             "授权完成后浏览器会跳到你**自己电脑**的这个地址，打不开。"
             "请设置环境变量 WB_OAUTH_PUBLIC_BASE=https://你的域名 后重启服务。" % callback)
    return payload


#: 授权完成后展示给用户的页面（不依赖前端框架，纯静态 HTML）
_CALLBACK_PAGE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} · MiraiHub</title>
<style>
  :root {{ color-scheme: light dark; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; min-height: 100vh; display: flex; align-items: center;
    justify-content: center; padding: 24px;
    font: 15px/1.6 -apple-system, "Segoe UI", "PingFang SC", sans-serif;
    background: {bg}; color: {fg};
  }}
  .card {{
    width: 100%; max-width: 420px; text-align: center;
    background: {surface}; border: 1px solid {border};
    border-radius: 16px; padding: 40px 32px 32px;
    box-shadow: 0 20px 50px rgba(0,0,0,.10);
  }}
  .icon {{
    width: 64px; height: 64px; margin: 0 auto 20px; border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    background: {iconBg};
  }}
  .icon svg {{ width: 32px; height: 32px; stroke: {iconFg}; stroke-width: 2.5;
    fill: none; stroke-linecap: round; stroke-linejoin: round; }}
  h1 {{ margin: 0 0 10px; font-size: 19px; font-weight: 600; letter-spacing: .01em; }}
  p {{ margin: 0; color: {muted}; font-size: 13.5px; }}
  .detail {{
    margin-top: 18px; padding: 12px 14px; border-radius: 10px;
    background: {codeBg}; color: {muted}; font-size: 12px;
    font-family: ui-monospace, Consolas, monospace; word-break: break-all;
    text-align: left;
  }}
  .hint {{ margin-top: 20px; font-size: 12px; color: {subtle}; }}
</style></head>
<body><div class="card">
  <div class="icon">{icon}</div>
  <h1>{title}</h1>
  <p>{message}</p>
  {detail}
  <p class="hint">这个页面可以关闭了，回到控制台会看到结果。</p>
</div></body></html>"""

_ICON_OK = ('<svg viewBox="0 0 24 24"><path d="M20 6 9 17l-5-5"/></svg>')
_ICON_FAIL = ('<svg viewBox="0 0 24 24"><path d="M18 6 6 18"/>'
              '<path d="m6 6 12 12"/></svg>')


def _callback_page(ok: bool, title: str, message: str, detail: str = "") -> HTMLResponse:
    """渲染授权结果页（跟随系统深浅色）。"""
    if ok:
        colors = dict(bg="#f6f7f9", fg="#111827", surface="#ffffff",
                      border="#e5e7eb", muted="#6b7280", subtle="#9ca3af",
                      iconBg="#dcfce7", iconFg="#15803d", codeBg="#f9fafb")
        icon = _ICON_OK
    else:
        colors = dict(bg="#f6f7f9", fg="#111827", surface="#ffffff",
                      border="#fecaca", muted="#6b7280", subtle="#9ca3af",
                      iconBg="#fee2e2", iconFg="#b91c1c", codeBg="#fef2f2")
        icon = _ICON_FAIL
    html = _CALLBACK_PAGE.format(
        title=title, message=message, icon=icon,
        detail=(f'<div class="detail">{detail}</div>' if detail else ""),
        bg=colors["bg"], fg=colors["fg"], surface=colors["surface"],
        border=colors["border"], muted=colors["muted"],
        subtle=colors["subtle"], iconBg=colors["iconBg"],
        iconFg=colors["iconFg"], codeBg=colors["codeBg"])
    return HTMLResponse(html, status_code=200)


@router.get("/device/callback")
def device_callback(code: str = "", state: str = "", nonce: str = "",
                    login_trace_id: str = "", loginTraceID: str = "",
                    authCodeInfo: str = "", userInfo: str = "", host: str = "",
                    userRegion: str = "",
                    error: str = "", error_description: str = ""):
    """Trae 授权完成后的回调（走**主服务 8790**，不单开端口）。

    上游把用户浏览器重定向到这里，带上 `code`。
    这里只负责把 code 交给对应的会话，真正的换 token 由前端轮询触发 ——
    这样浏览器能立刻看到一个好看的「授权完成」页。
    """
    from admin.platform.trae.core import oauth as trae_oauth

    # ---- 1) 从真实回调里取授权码 ----
    # Trae 的回调**不是** OAuth 标准形状，它的参数是：
    #   authCodeInfo = {"AuthCode":"...","ExpireAt":...,"ExpireDuration":...}
    #   userInfo     = {"UserID":"...","ScreenName":"...","TenantID":"..."}
    #   loginTraceID / host / userRegion
    # 只找 `code` 会永远拿不到码。
    import json as _json

    real_code = str(code or "").strip()
    if not real_code and authCodeInfo:
        try:
            info = _json.loads(authCodeInfo)
            real_code = str(info.get("AuthCode") or info.get("authCode") or "").strip()
        except Exception:  # noqa: BLE001 - 解析失败按空处理
            logger.debug("authCodeInfo 解析失败", exc_info=True)

    # ---- 2) 从 userInfo 里预取账号信息（换 token 后仍以接口返回为准）----
    user_info: dict = {}
    if userInfo:
        try:
            user_info = _json.loads(userInfo) or {}
        except Exception:  # noqa: BLE001
            user_info = {}

    # ---- 3) 找会话 ----
    key = (nonce or login_trace_id or loginTraceID or state or "").strip()
    session = trae_oauth.get(key) if key else None
    if session is None:
        # 上游**不保证**回传 login_trace_id / state（实测常常只有 code）。
        # 这时退到「最近的未完成会话」—— 用户同一时刻只会发起一次授权，
        # 直接报「会话已失效」会让真实授权永远完不成。
        session = trae_oauth.latest_pending()
    if session is None:
        return _callback_page(
            False, "授权会话已失效",
            "这个链接对应的授权已经结束或超时了，请回到控制台重新发起授权。")
    if error:
        session.deliver(error=error_description or error)
        return _callback_page(
            False, "授权未完成",
            "上游返回了错误，请回到控制台重试。", error_description or error)
    if not real_code:
        session.deliver(error="回调缺少授权码")
        return _callback_page(
            False, "授权参数不完整",
            "回调里没有拿到授权码，请回到控制台重新发起。")
    # 把 userInfo 一起交给会话（换 token 后如果接口不回 UserID，用它兜底）
    session.user_info = user_info
    session.deliver(code=real_code, host=host)
    return _callback_page(
        True, "授权完成",
        "账号正在写入号池，请回到控制台查看结果。")


@router.get("/device/poll")
def device_poll(nonce: str, _: bool = Depends(require_admin),
                db: Session = Depends(get_db)):
    """查一次授权结果（外层包异常，任何错都把完整栈返回给前端）。"""
    try:
        return _device_poll_impl(nonce, db)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        import traceback

        logger.exception("Trae device_poll 崩溃（完整栈见上）")
        raise HTTPException(500, detail={
            "error": "%s: %s" % (type(exc).__name__, str(exc)[:300]),
            "trace": traceback.format_exc()[-1500:],
        }) from exc


def _device_poll_impl(nonce: str, db: Session):
    """查一次授权结果。

    - 还没回调 -> `{pending: true}`
    - 回调拿到 code -> 立刻换 token，写入账号，返回 `{ok: true, account_id}`
    - 用户拒绝 / 超时 -> 明确错误
    """
    from admin.platform.trae.core import oauth as trae_oauth

    session = trae_oauth.get(nonce)
    if session is None:
        raise HTTPException(404, detail="授权会话不存在或已过期，请重新开始")

    result = session.wait(timeout=0.2)
    if result is None:
        return {"pending": True, "summary": "等待浏览器完成授权…"}
    if result.get("error"):
        return {"ok": False, "error": str(result["error"])[:200]}
    code = str(result.get("code") or "")
    if not code:
        return {"pending": True, "summary": "等待授权回调…"}

    try:
        token_data = trae_oauth.exchange_token(
            code, result.get("verifier") or "", realm=result.get("realm") or "cn",
            device_id=session.device_id, machine_id=session.machine_id,
            # 回调里带的 host 最准（`https://api.trae.com.cn`），优先用它
            host=result.get("host") or "")
    except Exception as exc:  # noqa: BLE001
        # 把「用哪个字段换、失败在哪」写进日志：
        # 换 token 的字段名靠猜不可靠，留下记录才能对照上游要求修正。
        # 换 token 的字段名/端点靠猜不可靠 —— 把**完整**的 code 和 verifier
        # 都写进日志（10 分钟内有效），这样才能拿真实配对去逐个试端点定位。
        # 用 exception() 而不是 warning()：它会把**完整栈**打进日志，
        # 只写 str(exc) 的话 `unhashable type: 'dict'` 根本看不出是哪一行。
        logger.exception("Trae 换 token 失败 | AuthCode=%s | verifier=%s | host=%s",
                         code, result.get("verifier") or "",
                         result.get("host") or "-")
        raise HTTPException(502, detail="换 token 失败：%s" % str(exc)[:240]) from exc

    # 从这里开始到写库结束，任何异常都要留下完整栈 ——
    # 之前只报 `换 token 失败: xxx` 一句话，无法定位是哪一行。
    try:
        return _finish_trae_account(db, session, token_data, result)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Trae 授权收尾失败（完整栈见上）")
        raise HTTPException(502, detail="授权收尾失败：%s: %s"
                            % (type(exc).__name__, str(exc)[:200])) from exc


def _finish_trae_account(db: Session, session, token_data: dict,
                         result: dict) -> dict:
    """换到 token 之后的收尾：解析字段 → 写 accounts 表。"""
    # 上游返回的字段名有多种写法，逐个兜。
    # **注意**：有的字段值本身可能是 dict（比如上游把 `Token` 包成对象），
    # 直接拿去当 dict key 会报 `unhashable type: 'dict'`，
    # 所以统一转成字符串/标量再返回。
    def pick(*names, default=""):
        for name in names:
            for key in (name, name.lower(), name.capitalize()):
                val = token_data.get(key)
                # **不能用 `val in (None, "", 0, False)`**：
                # `in` 会对 val 做哈希，val 是 dict/list 时直接抛
                # `unhashable type: 'dict'`。改成先判类型再判空值。
                if val is None or val is False:
                    continue
                if isinstance(val, str) and not val.strip():
                    continue
                if isinstance(val, (int, float)) and val == 0:
                    continue
                if isinstance(val, (dict, list, tuple)) and not val:
                    continue
                if isinstance(val, dict):
                    # 嵌套对象优先取常见子字段，都没有就序列化
                    for sub in ("Value", "value", "Token", "token",
                                "AccessToken", "access_token"):
                        inner = val.get(sub)
                        if isinstance(inner, (str, int, float)) and inner != "":
                            return str(inner)
                    return json.dumps(val, ensure_ascii=False)
                if isinstance(val, (list, tuple)):
                    return json.dumps(val, ensure_ascii=False)
                return val
        return default

    access = str(pick("Token", "AccessToken", "access_token") or "")
    refresh = str(pick("RefreshToken", "refresh_token") or "")
    uid = str(pick("UserId", "user_id", "UID") or "")
    # 接口没回 UserID 时用回调里的 userInfo 兜底 ——
    # 那个 JSON 里有 `UserID` / `ScreenName` / `TenantID`，比空值可靠。
    info = result.get("user_info") or {}
    if not uid:
        uid = str(info.get("UserID") or info.get("UserId") or "")
    screen = str(pick("ScreenName", "screen_name") or
                 info.get("ScreenName") or "")
    tenant = str(info.get("TenantID") or "")
    if not access:
        raise HTTPException(502, detail="上游没有返回 access token：%s"
                            % json.dumps(token_data, ensure_ascii=False)[:240])

    auth_json = json.dumps({
        "token": access,
        "refresh_token": refresh,
        "uid": uid,
        "realm": result.get("realm") or "cn",
        "device_id": session.device_id,
        "machine_id": session.machine_id,
        "source": "oauth",
        "screen_name": screen,
        "tenant_id": tenant,
        "imported_at": int(time.time()),
        "expiredAt": pick("TokenExpireAt", "expires_at", default=""),
        "refreshExpiredAt": pick("RefreshExpireAt", "refresh_expires_at", default=""),
    }, ensure_ascii=False)

    account = (db.query(Account)
               .filter(Account.platform == PLATFORM, Account.uid == str(uid)).first())
    if account is None:
        account = Account(platform=PLATFORM, uid=str(uid),
                          name=screen or str(uid) or "Trae",
                          auth_json=auth_json, status="active",
                          domain=result.get("realm") or "cn")
        db.add(account)
    else:
        account.auth_json = auth_json
        account.status = "active"
    db.commit()
    db.refresh(account)
    return {"ok": True, "account_id": account.id, "uid": uid,
            "summary": "授权成功，账号已写入号池"}


@router.post("/import-local")
def import_local(_: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """把本机扫描到的凭证导入统一号池。"""
    return trae_accounts.import_local(db)


class ImportIn(BaseModel):
    #: 单条凭证或凭证数组（支持 Trae 桌面端 storage.json 解出的 dict）。
    credential: dict | list[dict] | None = None
    realm: str = ""


@router.post("/import")
def import_credential(body: ImportIn, _: bool = Depends(require_admin),
                      db: Session = Depends(get_db)):
    """手工导入凭证（粘贴 refresh token / token / 备份文件）。"""
    if not body.credential:
        raise HTTPException(400, "credential 不能为空")
    records = body.credential if isinstance(body.credential, list) else [body.credential]
    realm = _realm_or_400(body.realm) if body.realm else ""
    added = updated = 0
    errors = []
    for record in records:
        if not isinstance(record, dict):
            errors.append({"error": "凭证必须是对象"})
            continue
        payload = dict(record)
        if realm:
            payload["realm"] = realm
        try:
            _account, created = trae_accounts.import_record(db, payload)
            added += 1 if created else 0
            updated += 0 if created else 1
        except Exception as exc:  # noqa: BLE001
            errors.append({"error": str(exc)})
    return {"added": added, "updated": updated, "errors": errors}


class PatchIn(BaseModel):
    name: str | None = None
    status: str | None = None
    realm: str | None = None


@router.patch("/accounts/{acc_id}")
def patch_account(acc_id: int, body: PatchIn, _: bool = Depends(require_admin),
                  db: Session = Depends(get_db)):
    account = _get(db, acc_id)
    if body.name is not None:
        account.name = body.name[:120]
    if body.status in ("active", "disabled"):
        account.status = body.status
        if body.status == "active":
            account.session_dead_fails = 0
            account.consecutive_fails = 0
            account.cool_until = None
            account.cool_kind = ""
    if body.realm:
        account.domain = _realm_or_400(body.realm)
    db.commit()
    return {"ok": True, "account": trae_accounts.public_view(account)}


@router.delete("/accounts/{acc_id}")
def delete_account(acc_id: int, _: bool = Depends(require_admin),
                   db: Session = Depends(get_db)):
    account = _get(db, acc_id)
    db.delete(account)
    db.commit()
    return {"ok": True}


@router.post("/accounts/{acc_id}/refresh")
def refresh_account(acc_id: int, force: bool = False,
                    _: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """刷新 token 并同步额度。"""
    account = _get(db, acc_id)
    try:
        if force or not client.token_of(_auth_json(account)):
            trae_pool.credentials(db, account, force_refresh=True)
    except client.UpstreamError as exc:
        raise HTTPException(502, {"message": exc.message, "upstream": exc.to_dict()}) from None
    info = trae_pool.refresh_quota(db, account)
    db.refresh(account)
    return {"ok": bool(info.get("ok")), **info,
            "account": trae_accounts.public_view(account),
            "balance_remain": int(account.balance_remain or 0),
            "balance_total": int(account.balance_total or 0)}


@router.post("/refresh-all")
def refresh_all(realm: str = "", _: bool = Depends(require_admin),
                db: Session = Depends(get_db)):
    """批量刷新（串行，避免同出口并发把上游打急）。"""
    realm = _realm_or_400(realm) if realm else ""
    rows = trae_pool.accounts_for(db, realm)
    ok = failed = 0
    results = []
    for row in rows:
        if row.status != "active":
            continue
        info = trae_pool.refresh_quota(db, row)
        if info.get("ok"):
            ok += 1
        else:
            failed += 1
        results.append({"id": row.id, "name": row.name, "ok": bool(info.get("ok")),
                        "error": (info.get("error") or {}).get("message") if info.get("error") else ""})
    return {"ok": ok, "failed": failed, "items": results}


@router.post("/accounts/{acc_id}/test")
def test_account(acc_id: int, model: str = "", _: bool = Depends(require_admin),
                 db: Session = Depends(get_db)):
    """真实对话连通性检查：额度 -> 模型清单 -> 一次最小对话。"""
    account = _get(db, acc_id)
    realm = client.normalize_realm(account.domain or
                                   json.loads(account.auth_json or "{}").get("realm"))
    stages: dict = {}
    try:
        creds = trae_pool.credentials(db, account)
        stages["token"] = {"ok": True, "expires_at": int(client.expiry_of(creds) or 0)}
    except client.UpstreamError as exc:
        return {"ok": False, "stages": {"token": {"ok": False, **exc.to_dict()}}}

    try:
        rows = client.fetch_model_list(creds, realm)
        catalog.note_live_rows(realm, [r for r in rows if isinstance(r, dict)])
        stages["models"] = {"ok": True, "count": len(rows),
                            "models": [str(r.get("name") or "") for r in rows][:30]}
    except client.UpstreamError as exc:
        stages["models"] = {"ok": False, **exc.to_dict()}

    candidates = [model] if model else [
        str(r.get("name") or "") for r in (stages.get("models", {}).get("models") or [])]
    candidates = [c for c in candidates if c][:3]
    stages["conversation"] = {"ok": False, "error": "无可试模型"}
    for candidate in candidates:
        try:
            body = client.build_chat_body(candidate, [
                {"role": "user", "content": "只回答两个字：连通"}], max_tokens=32)
            resp = client.open_chat(creds, realm, body, timeout=60)
            out = client.collect(resp, idle_timeout=60)
        except client.UpstreamError as exc:
            stages["conversation"] = {"ok": False, "model": candidate, **exc.to_dict()}
            continue
        if out["errors"]:
            err = out["errors"][0]
            stages["conversation"] = {"ok": False, "model": candidate,
                                      "code": err.get("code"),
                                      "message": err.get("message")}
            continue
        stages["conversation"] = {"ok": bool(out["text"]), "model": candidate,
                                  "text": out["text"][:120],
                                  "usage": out["usage"]}
        if out["text"]:
            break
    quota = trae_pool.refresh_quota(db, account)
    stages["quota"] = {"ok": bool(quota.get("ok")),
                       "remain": quota.get("remain"), "total": quota.get("total"),
                       "plan": quota.get("plan") or ""}
    ok = all(stage.get("ok") for stage in stages.values())
    return {"ok": ok, "realm": realm, "stages": stages}


@router.post("/accounts/{acc_id}/cooling/clear")
def clear_cooling(acc_id: int, _: bool = Depends(require_admin),
                  db: Session = Depends(get_db)):
    account = _get(db, acc_id)
    account.cool_until = None
    account.cool_kind = ""
    account.breaker_until = None
    account.breaker_fails = 0
    account.consecutive_fails = 0
    account.err_count = 0
    db.query(AccountModelCooldown).filter(
        AccountModelCooldown.account_id == acc_id).delete()
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# 模型
# ---------------------------------------------------------------------------
@router.get("/models")
def list_models(realm: str = "", live: bool = False, include_paid: bool = False,
                _: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """模型清单。

    `live=true` 时刷新数据源（客户端缓存 + 上游清单），否则用已加载的目录。
    **返回里带 `source` 说明这批模型是哪儿来的** —— 客户端目录与上游接口
    的模型集合差别很大（前者几十个新模型、后者 6~9 个旧的），
    不标来源的话，「为什么模型变少了」会变成猜谜。
    """
    realm = _realm_or_400(realm) if realm else (_spec().default_realm() if _spec() else "")
    live_names: set[str] = set()
    note = ""
    source = "snapshot"
    desktop_count = 0
    if live:
        desktop = desktop_catalog.read_models(realm)
        desktop_count = len(desktop["models"])
        if desktop["models"]:
            catalog.note_live_rows(
                realm, [{**r, "source": "desktop"} for r in desktop["models"]])
            live_names = {m["name"] for m in desktop["models"]}
            source = "desktop"
        if desktop["error"]:
            note = desktop["error"]
        # 客户端不可用时退回上游接口
        if not desktop["models"]:
            try:
                account = trae_pool.select(db, realm=realm, mark_picked=False)
                if account is None:
                    note = note or "号池里没有可用账号，仅返回目录"
                else:
                    creds = trae_pool.credentials(db, account)
                    rows = client.fetch_model_list(creds, realm)
                    catalog.note_live_rows(
                        realm, [{**r, "source": "upstream"} for r in rows])
                    live_names = {str(r.get("name") or "") for r in rows}
                    source = "upstream"
            except client.UpstreamError as exc:
                note = note or ("上游模型清单获取失败：%s" % exc.message)
            except Exception as exc:  # noqa: BLE001
                note = note or ("上游模型清单获取失败：%s" % exc)
    items = []
    for item in catalog.realm_models(realm):
        upstream = str(item.get("upstream") or "")
        items.append({**item,
                      "available": (upstream in live_names) if live_names else None})
    # `free` 只在客户端目录条目里（identity_list 推导）。快照兜底的条目
    # 没有这个信息，此时给 None 表示「未知」，前端不要显示「需会员」。
    for item in items:
        item.setdefault("free", None)
    # 默认**不列会员模型**：本系统的号几乎全是 free，列出来只会让人去调
    # 一个必然失败的模型。排查时可显式带 `include_paid=true`。
    paid_count = sum(1 for i in items if i.get("free") is False)
    shown = items if include_paid else [i for i in items if i.get("free") is not False]
    return {"realm": realm, "items": shown, "note": note, "source": source,
            "desktop_count": desktop_count,
            "free_count": sum(1 for i in items if i.get("free") is True),
            "paid_count": paid_count,
            "hidden_paid": 0 if include_paid else paid_count,
            "live": sorted(live_names) if live_names else []}


@router.post("/models/pull")
def pull_models(realm: str = "", _: bool = Depends(require_admin),
                db: Session = Depends(get_db)):
    """把**真实**模型目录并入目录表，并登记进公共模型表。

    数据源优先级（后写覆盖先写）：
      1. 上游 `model_list`   —— 只有 6~9 个旧模型，仅作兜底
      2. 客户端 `state.vscdb` —— **权威全量**，几十个新模型

    为什么要登记进 `model_configs`：模型配置**故意保持一张公共表**，
    但要有平台归属 —— 界面按平台分组后，才分得清「这几十个模型分别是
    哪个平台的」。
    """
    spec = _spec()
    # ⚠️ `spec.realm_keys()` 对**单区平台**（trae-cn / trae-intl）返回**空元组**：
    # 它把区域表达成 `spec.realm` 而不是 `realms` 元组。原来写
    # `list(spec.realm_keys() ...)` 会得到空列表 -> 一个区都不处理 ->
    # `registered` 恒为 0，界面上的 Trae 模型永远停在最早那 15 个旧模型上。
    targets = [_realm_or_400(realm)] if realm else list(REALM_KEYS)
    result = {}
    registered = 0
    for target in targets:
        upstream_rows: list[dict] = []
        upstream_error = ""
        try:
            account = trae_pool.select(db, realm=target, mark_picked=False)
            if account is None:
                upstream_error = "号池里没有可用账号"
            else:
                creds = trae_pool.credentials(db, account)
                upstream_rows = [r for r in client.fetch_model_list(creds, target)
                                 if isinstance(r, dict)]
                catalog.note_live_rows(
                    target, [{**r, "source": "upstream"} for r in upstream_rows])
        except client.UpstreamError as exc:
            upstream_error = exc.message

        # 客户端目录：权威来源，覆盖上面的旧模型
        desktop = desktop_catalog.read_models(target)
        if desktop["models"]:
            catalog.note_live_rows(
                target, [{**r, "source": "desktop"} for r in desktop["models"]])
            rows = desktop["models"]
        else:
            rows = upstream_rows

        registered += register_models(db, target, rows)
        result[target] = {
            "ok": bool(rows),
            "count": len(rows),
            "free_count": sum(1 for r in rows if r.get("free") is not False),
            "source": "desktop" if desktop["models"] else "upstream",
            "upstream_count": len(upstream_rows),
            "upstream_error": upstream_error,
            "desktop_count": len(desktop["models"]),
            "desktop_error": desktop["error"],
            "groups": desktop["groups"],
            "names": [str(r.get("name") or "") for r in rows],
        }
    result["_registered"] = registered
    # 清掉历史遗留的会员模型
    result["_purged_paid"] = purge_paid_models(db)
    return result


def register_models(db: Session, realm: str, rows) -> int:
    """把该区模型登记进公共 `model_configs`（`platform='trae'`）。

    幂等：已存在的 model_id 只补齐备注与元数据，**不动** enabled 与倍率
    —— 那两列是运维手工调过的，每次同步都覆盖等于把配置清空。
    同一个模型同时属于两个区时，ID 会带上区域后缀，避免互相覆盖。
    """
    from admin.models import ModelConfig

    spec = _spec()
    default_realm = spec.default_realm() if spec else "cn"
    existing = {row.model_id: row for row in
                db.query(ModelConfig).filter(ModelConfig.platform == PLATFORM).all()}
    added = 0
    skipped_paid = 0
    for row in rows or ():
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        # **会员专属模型不入库**：本系统的号几乎全是 free，录进来也只会在
        # 模型配置里摆一排永远调不通的条目，还会被自动选型选中。
        # `free is False` 才跳过；`None`（不适用/未知）照常录入。
        if row.get("free") is False:
            skipped_paid += 1
            continue
        # 默认区用裸名（这是客户端最常见写法），非默认区加 `:realm` 后缀，
        # 这样两区同名的模型不会互相覆盖。
        model_id = name if realm == default_realm else "%s:%s" % (name, realm)
        config = existing.get(model_id)
        if config is None:
            config = ModelConfig(platform=PLATFORM, level="system",
                                 model_id=model_id, enabled=1, note="",
                                 credit_multiplier=0)
            db.add(config)
            added += 1
        display = str(row.get("display_name") or name)
        if not config.note:
            config.note = display[:255]
        try:
            metadata = json.loads(config.upstream_metadata or "{}")
        except ValueError:
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        metadata.update({
            "realm": realm,
            "display_name": display,
            "model_type": str(row.get("model_type") or "chat_model"),
            "multimodal": bool(row.get("multimodal")),
            "vendor": "trae",
            # 免费可用性：来自客户端的 identity_list（见 desktop_catalog）。
            # 落库是为了让模型配置面板能标出「需会员」，不必每次回读 vscdb。
            "free": bool(row.get("free", True)),
            "identity_list": row.get("identity_list") or [],
        })
        config.upstream_metadata = json.dumps(metadata, ensure_ascii=False)
    db.commit()
    return added


def purge_paid_models(db) -> int:
    """清掉已入库的会员专属模型（`metadata.free is False`）。

    只在 `free` 明确为 False 时删 —— `None` 是「不适用/未知」，不能动。
    返回删除条数。`pull_models` 在登记后调用它，避免历史遗留的付费模型
    一直挂在模型配置里。
    """
    import json as _json

    from admin.models import ModelConfig

    removed = 0
    for row in db.query(ModelConfig).filter(ModelConfig.platform == PLATFORM).all():
        try:
            meta = _json.loads(row.upstream_metadata or "{}")
        except ValueError:
            continue
        if isinstance(meta, dict) and meta.get("free") is False:
            db.delete(row)
            removed += 1
    if removed:
        db.commit()
    return removed


# ---------------------------------------------------------------------------
# 额度总览
# ---------------------------------------------------------------------------
@router.get("/overview")
def overview(realm: str = "", _: bool = Depends(require_admin),
             db: Session = Depends(get_db)):
    """Trae 总览。

    `realm` 可选：国内版/国际版现在是**两个平台**，切到其中一个时
    底部统计必须只算那个区 —— 不传则返回两区合计（老调用方兼容）。
    """
    spec = _spec()
    realm = _realm_or_400(realm) if realm else ""
    rows = trae_pool.accounts_for(db, realm)
    per_realm = {}
    # 同 register_models：不能用 `spec.realm_keys()`（单区平台为空）
    for key in REALM_KEYS:
        subset = [r for r in rows if (r.domain or "") == key]
        per_realm[key] = trae_pool.summarize(subset)
    return {"platform": PLATFORM, "realm": realm,
            "summary": trae_pool.summarize(rows),
            "realms": per_realm,
            "storage": trae_accounts.appdata_hint(),
            "now": None}


# ---------------------------------------------------------------------------
# 用量明细（需要网页会话 cookie）
# ---------------------------------------------------------------------------
@router.get("/usage")
def usage(realm: str = "", days: int = 30, _: bool = Depends(require_admin),
          db: Session = Depends(get_db)):
    """按会话分组的用量明细。

    ⚠️ 上游这个接口**只认网页会话 cookie**。用 IDE token 调会返回
    `total=0`（实测 6 个时间窗 × 8 组参数，传错参数报 9004 证明鉴权通过）。
    所以返回值必须能区分「没用量」与「查不到」：

      need_web_cookie=true -> 缺 Cookie，前端显示「去补充」
      total=0 且 neeed=false -> 真的没有记录

    `local` 字段始终返回**经过本网关**的请求统计，与上游账单口径不同，
    但排障时更有用。
    """
    from admin.platform.trae.platform_gateway import TraeGateway

    gateway_realm = _realm_or_400(realm) if realm else ""
    ops = TraeGateway(gateway_realm).account_ops()
    return ops.usage(db, days=days)


class WebCookieIn(BaseModel):
    #: 账号 id（留空 = 给该区全部账号设置）
    account_id: int = 0
    #: 浏览器里复制的 Cookie 请求头原文
    cookie: str = ""


@router.post("/web-cookie")
def save_web_cookie(body: WebCookieIn, _: bool = Depends(require_admin),
                    db: Session = Depends(get_db)):
    """保存网页会话 cookie（用于查询上游用量明细）。

    Cookie 从浏览器开发者工具复制：登录 trae.ai → F12 → Network →
    任意请求 → 复制 `Cookie` 请求头整行。
    """
    cookie = (body.cookie or "").strip()
    if not cookie:
        raise HTTPException(400, detail="cookie 不能为空")

    query = db.query(Account).filter(Account.platform == PLATFORM)
    if body.account_id:
        query = query.filter(Account.id == body.account_id)
    rows = query.all()
    if not rows:
        raise HTTPException(404, detail="没有匹配的 Trae 账号")

    updated = []
    for account in rows:
        try:
            data = json.loads(account.auth_json or "{}")
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        data["web_cookie"] = cookie
        account.auth_json = json.dumps(data, ensure_ascii=False)
        updated.append(account.id)
    db.commit()
    return {"ok": True, "updated": updated}


def _get(db: Session, acc_id: int) -> Account:
    account = db.query(Account).filter(Account.id == acc_id,
                                       Account.platform == PLATFORM).first()
    if account is None:
        raise HTTPException(404, f"Trae 账号 #{acc_id} 不存在")
    return account


def _auth_json(account: Account) -> dict:
    try:
        data = json.loads(account.auth_json or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}