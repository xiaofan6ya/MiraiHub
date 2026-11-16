"""多平台公开注册入口（聚合层，`/api/open/*`）。

## 定位

`/join`（WorkBuddy 单平台自助端）的多平台泛化：

* 门禁**复用 join_gate**（总闸 + 访问票据 Cookie），默认关闭、零开关差异；
* 每个平台网关在自己的 `admin/platform/<p>/public_auth.py` 里实现公开登录
  能力（`spec` + `begin/poll/cancel/sms_*`），本模块只做聚合：
  平台清单、路由分发、限流、会话、审计、响应整形；
* 需要**桌面客户端**的平台（wps-lingxi / trae / qoder / codearts）一律不注册
  —— 公开页的用户全是手机，没有客户端；
* **没有任何上传接口**；前端只轮询，上游由后端代理。

## 安全设计（2026-11-14，上一次 XSS 事故的整改延伸）

1. **响应整形**：对外字段固定、错误一律通用文案；上游原始响应体
   （含官方昵称等用户可控字段）**绝不透传**，账号名入库前统一过
   `clean_display_name`（stored XSS 的根治点），回显前统一 `_mask_name` 打码。
2. **限流**：Redis 固定窗口（INCR+EXPIRE），Redis 不可用降级进程内计数。
   桶：begin 6/10min、poll 90/min、sms 3/10min、verify 6/10min、cancel 20/min、
   select 6/10min，全部按 IP。
3. **会话**：服务端 128-bit 随机 sid，绑定 IP，TTL 10 分钟，单 IP 并发 ≤3，
   过期/用毕即焚；poll 凭 sid 即可（sid 即能力凭证，泄密面=单会话）。
4. **门禁**：`/api/open/*` 与 `/api/join/*` 同一把总闸；变更类端点全部要求
   有效邀请码（校验失败 **404**，不暴露入口存在性）。
5. **审计**：begin/成功/失败/取消都记一条结构化日志（IP + 平台 + 结果），
   不含任何 Cookie / token / 完整手机号。
"""
from __future__ import annotations

import logging
import secrets
import threading
import time
from datetime import datetime
from typing import Any, Callable

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from admin.db import get_db
from admin.security import clean_display_name

logger = logging.getLogger("admin.public_auth")

router = APIRouter(prefix="/api/open", tags=["public-entry"])

SESSION_TTL = 600          # 单个登录会话 10 分钟
MAX_SESSIONS_PER_IP = 3    # 单 IP 并发登录会话上限

#: 限流桶：名称 → (次数, 窗口秒)
LIMITS = {
    "begin": (6, 600),
    "poll": (90, 60),
    "sms": (3, 600),
    "verify": (6, 600),
    "cancel": (20, 600),
    "select": (6, 600),
    "platforms": (30, 60),
}


# ---------------------------------------------------------------------------
# 限流（Redis 固定窗口，进程内兜底）
# ---------------------------------------------------------------------------
_memory_buckets: dict[str, tuple[int, float]] = {}
_rl_lock = threading.Lock()


def _rate_hit(bucket: str, ip: str) -> bool:
    """计一次限流；返回 False = 超限。Redis 挂了自动退进程内计数。"""
    limit, window = LIMITS.get(bucket, (30, 60))
    key = "pub:%s:%s" % (bucket, ip)
    try:
        import redis as redis_mod

        from admin.config import settings

        client = redis_mod.Redis.from_url(settings.REDIS_URL,
                                          socket_timeout=1.5,
                                          socket_connect_timeout=1.5)
        pipe = client.pipeline()
        pipe.incr(key)
        pipe.expire(key, window)
        count = int(pipe.execute()[0] or 0)
        if count == 1:
            pipe.expire(key, window)
            pipe.execute()
        return count <= limit
    except Exception:  # noqa: BLE001 - Redis 不可用退内存
        now = time.time()
        with _rl_lock:
            count, expire_at = _memory_buckets.get(key, (0, 0.0))
            if now > expire_at:
                count, expire_at = 0, now + window
            count += 1
            _memory_buckets[key] = (count, expire_at)
            # 顺手清过期
            if len(_memory_buckets) > 4096:
                for k in [k for k, (_, exp) in _memory_buckets.items()
                          if now > exp]:
                    _memory_buckets.pop(k, None)
        return count <= limit


def _client_ip(request: Request) -> str:
    try:
        return request.client.host if request.client else "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


# ---------------------------------------------------------------------------
# 会话表（进程内；本服务单 worker 部署）
# ---------------------------------------------------------------------------
_sessions: dict[str, dict] = {}
_s_lock = threading.Lock()


def register_session(platform: str, kind: str, ip: str,
                     data: dict) -> str:
    """登记一个登录会话，返回对外 sid。超上限抛 429。"""
    now = time.time()
    with _s_lock:
        stale = [k for k, v in _sessions.items() if now - v["created"] > SESSION_TTL]
        for k in stale:
            _sessions.pop(k, None)
        active = [k for k, v in _sessions.items()
                  if v["ip"] == ip and now - v["created"] <= SESSION_TTL]
        if len(active) >= MAX_SESSIONS_PER_IP:
            raise HTTPException(status_code=429, detail="尝试过于频繁，请稍后再试")
        sid = "po-" + secrets.token_urlsafe(24)
        _sessions[sid] = {"platform": platform, "kind": kind, "ip": ip,
                          "created": now, "data": data}
    return sid


def get_session(sid: str, platform: str, ip: str) -> dict:
    """取会话；不存在 / 跨平台 / 换 IP / 过期都按「不存在」处理（404）。"""
    now = time.time()
    with _s_lock:
        info = _sessions.get(sid)
    if not info or info.get("platform") != platform:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")
    if now - info["created"] > SESSION_TTL:
        with _s_lock:
            _sessions.pop(sid, None)
        raise HTTPException(status_code=404, detail="会话不存在或已过期")
    if ip and info.get("ip") != ip:
        logger.warning("公开入口会话 IP 漂移拒绝 plat=%s sid_prefix=%s",
                       platform, sid[:8])
        raise HTTPException(status_code=404, detail="会话不存在或已过期")
    return info


def drop_session(sid: str) -> None:
    with _s_lock:
        _sessions.pop(sid, None)


# ---------------------------------------------------------------------------
# 平台适配器发现（平台目录可被裁剪，缺哪个静默跳哪个）
# ---------------------------------------------------------------------------
class _Adapter:
    __slots__ = ("spec", "begin", "poll", "cancel", "sms_send", "sms_verify",
                 "select", "submit")

    def __init__(self, mod):
        self.spec = getattr(mod, "spec", {})
        self.begin: Callable = getattr(mod, "begin", None)
        self.poll: Callable = getattr(mod, "poll", None)
        self.cancel: Callable = getattr(mod, "cancel", None)
        self.sms_send: Callable = getattr(mod, "sms_send", None)
        self.sms_verify: Callable = getattr(mod, "sms_verify", None)
        self.select: Callable = getattr(mod, "select", None)
        #: 「扫码后把回调地址贴回来」的平台（Marvis）专用；其余平台没有这个方法。
        self.submit: Callable = getattr(mod, "submit", None)


def _load_adapters() -> dict[str, _Adapter]:
    out: dict[str, _Adapter] = {}
    #: 豆包已从网关整体移除（2026-11-15），公开入口也不再提供它的注册。
    #: marvis（腾讯 Marvis）2026-11-16 接入：扫码 + 粘贴回调，与控制台同源。
    for name in ("workbuddy", "loomy", "raccoon", "minimax", "marvis"):
        try:
            mod = __import__("admin.platform.%s.public_auth" % name,
                             fromlist=["spec"])
        except Exception:  # noqa: BLE001 - 平台被裁剪时不出现
            continue
        try:
            out[name] = _Adapter(mod)
        except Exception:  # noqa: BLE001
            continue
    return out


_ADAPTERS = _load_adapters()

#: 平台清单：workbuddy 是既有 /join 流（前端直连旧端点），永远排第一。
WORKBUDDY_SPEC = {"id": "workbuddy", "label": "WorkBuddy", "modes": ["sms"],
                  "order": 10, "legacy": True}

#: 每平台默认开放（管理页可单独关掉某一个）
DEFAULT_PLATFORM_FLAGS = {"workbuddy": True, "loomy": True,
                          "raccoon": True, "minimax": True, "marvis": True}


# ---------------------------------------------------------------------------
# 入口配置（管理页可调）：平台开关 + 访问链接时效，存 SystemSetting
# ---------------------------------------------------------------------------
CONFIG_KEY = "public_entry_config"
TTL_MIN, TTL_MAX = 600, 604800  # 10 分钟 ~ 7 天

#: 公开页文案默认值（管理页可改）。**只存纯文本**：前端一律 textContent
#: 渲染，唯一的「样式」是 `**加粗**` 标记（前端拆分成 <b> 文本节点，
#: 不经过任何 HTML 解析），其余 JS/链接/标签在存储层就进不来。
COPY_DEFAULTS = {
    "title": "MiraiHub 注册",
    "sub": "",
    "intro": [],
    "foot": "",
}


def _sanitize_copy(raw) -> dict:
    """公开页文案消毒：纯文本、控长度、去控制字符。

    前端对文案只做 textContent 渲染，因此 HTML/JS 字符本身无害；
    这里仍然做硬约束（长度上限 + 控制字符剔除），把存储层的输入
    收敛成「可预测的纯文本」，杜绝任何旁路渲染的可能。
    """
    import re as _re

    def _clean_line(v: str, limit: int) -> str:
        text = _re.sub("[\x00-\x1f\x7f]", " ", str(v or "")).strip()
        return text[:limit]

    def _lines(arr, limit_each: int, max_lines: int) -> list:
        out = []
        for line in (arr or [])[:max_lines]:
            cleaned = _clean_line(line, limit_each)
            if cleaned:
                out.append(cleaned)
        return out

    raw = raw if isinstance(raw, dict) else {}
    return {
        "title": _clean_line(raw.get("title"), 40) or COPY_DEFAULTS["title"],
        "sub": _clean_line(raw.get("sub"), 120) or COPY_DEFAULTS["sub"],
        "intro": _lines(raw.get("intro"), 200, 6) or COPY_DEFAULTS["intro"],
        "foot": _clean_line(raw.get("foot"), 160) or COPY_DEFAULTS["foot"],
    }


def _load_config(db: Session) -> dict:
    import json as _json

    from admin.models import SystemSetting

    try:
        row = (db.query(SystemSetting)
               .filter(SystemSetting.key == CONFIG_KEY).first())
        raw = _json.loads(row.value) if row and row.value else {}
    except Exception:  # noqa: BLE001 - 配置行缺失/损坏时用默认值
        logger.warning("公开入口配置读取失败，使用默认", exc_info=True)
        raw = {}
    cfg = {"platforms": dict(DEFAULT_PLATFORM_FLAGS), "ticket_ttl": 86400,
           "copy": dict(COPY_DEFAULTS)}
    if isinstance(raw, dict):
        if isinstance(raw.get("copy"), dict):
            cfg["copy"] = _sanitize_copy(raw["copy"])
        plats = raw.get("platforms")
        if isinstance(plats, dict):
            for k, v in plats.items():
                cfg["platforms"][str(k)] = bool(v)
        ttl = raw.get("ticket_ttl")
        if ttl is not None:
            try:
                n = int(ttl)
            except (TypeError, ValueError):
                n = 86400
            # 0 = 永久（公告里长期挂的链接）。其余钳制在 10 分钟 ~ 7 天。
            cfg["ticket_ttl"] = 0 if n <= 0 else max(TTL_MIN, min(n, TTL_MAX))
    return cfg


def _save_config(db: Session, cfg: dict) -> None:
    import json as _json

    from admin.models import SystemSetting

    row = (db.query(SystemSetting)
           .filter(SystemSetting.key == CONFIG_KEY).first())
    payload = _json.dumps(cfg, ensure_ascii=False)
    if row is None:
        db.add(SystemSetting(key=CONFIG_KEY, value=payload))
    else:
        row.value = payload
    db.commit()
    # 会话 expire_on_commit=False：同会话再读会拿到 identity map 里的旧对象，
    # 提交后主动失效，保证本请求后续读取（以及测试）拿到新值。
    db.expire_all()


def _platform_list(db: Session | None = None) -> list[dict]:
    cfg = _load_config(db) if db is not None else {"platforms": {}}
    flags = cfg.get("platforms") or {}
    items = []
    for pid, ad in sorted(_ADAPTERS.items(),
                          key=lambda kv: int(kv[1].spec.get("order") or 99)):
        if not flags.get(pid, True):
            continue
        items.append({"id": pid, "label": str(ad.spec.get("label") or pid),
                      "modes": list(ad.spec.get("modes") or []),
                      "order": int(ad.spec.get("order") or 99)})
    return items


def _adapter_of(platform: str) -> _Adapter:
    ad = _ADAPTERS.get(platform)
    if ad is None:
        # 404 而不是 400：不暴露「平台存在但未开放」这类信息
        raise HTTPException(status_code=404, detail="页面不存在")
    return ad


# ---------------------------------------------------------------------------
# 响应整形 / 入库收尾
# ---------------------------------------------------------------------------
def _mask_name(name: str) -> str:
    s = (name or "").strip()
    if not s:
        return ""
    if len(s) <= 4:
        return s[:1] + "**"
    return s[:2] + "***" + s[-2:]


def _sanitize_account_name(account) -> None:
    """入库后的统一去毒点：账号显示名必须过 clean_display_name。

    上一次 stored XSS 就是官方昵称原样入库导致的 —— 所有公开入口的
    入库路径都必须过这一关（这里兜底，适配器内部也不该裸写）。
    """
    try:
        if account is None:
            return
        safe = clean_display_name(account.name, "") or \
            clean_display_name(account.uid, "") or "账号"
        if safe != account.name:
            account.name = safe
    except Exception:  # noqa: BLE001
        pass


def _fail_plain(status: int = 400) -> HTTPException:
    """对外统一失败文案：不区分上游原因，杜绝错误信息侧信道。"""
    return HTTPException(status_code=status, detail="操作未完成，请稍后重试")


def _audit(event: str, platform: str, ip: str, **kw) -> None:
    logger.info("公开入口 %s plat=%s ip=%s %s",
                event, platform, ip,
                " ".join("%s=%s" % (k, v) for k, v in kw.items()))


# ---------------------------------------------------------------------------
# 请求体
# ---------------------------------------------------------------------------
class BeginIn(BaseModel):
    mode: str = "qr"


class SmsSendIn(BaseModel):
    mobile: str = ""
    country: str = "86"


class SmsVerifyIn(BaseModel):
    session: str = ""
    code: str = ""
    password: str = ""


class SelectIn(BaseModel):
    session: str = ""
    sec_uid: str = ""


class CancelIn(BaseModel):
    session: str = ""


class SubmitIn(BaseModel):
    """「扫码后把回调地址贴回来」的提交体（Marvis）。"""

    session: str = ""
    callback: str = ""     # 回调地址 / 裸 code


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------
@router.get("/platforms")
def open_platforms(request: Request, db: Session = Depends(get_db)):
    ip = _client_ip(request)
    if not _rate_hit("platforms", ip):
        raise _fail_plain(429)
    items = _platform_list(db)
    return {"ok": True, "platforms": items,
            "default": (items[0]["id"] if items else ""),
            "copy": _load_config(db).get("copy") or dict(COPY_DEFAULTS)}


@router.post("/{platform}/begin")
def open_begin(platform: str, body: BeginIn, request: Request,
               db: Session = Depends(get_db)):
    """发起非短信模式（qr / auth_url）。返回会话与二维码等渲染素材。"""
    ip = _client_ip(request)
    if not _rate_hit("begin", ip):
        raise _fail_plain(429)
    ad = _adapter_of(platform)
    if ad.begin is None:
        raise _fail_plain(404)
    mode = str(body.mode or "qr")
    if mode not in (ad.spec.get("modes") or []):
        raise _fail_plain(404)
    try:
        result = ad.begin(mode, db, ip)
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 - 上游细节绝不外泄
        _audit("begin_error", platform, ip)
        raise _fail_plain(502)
    _audit("begin", platform, ip, mode=mode)
    return {"ok": True, **result}


@router.get("/{platform}/poll")
def open_poll(platform: str, session: str, request: Request, db: Session = Depends(get_db)):
    ip = _client_ip(request)
    if not _rate_hit("poll", ip):
        raise _fail_plain(429)
    ad = _adapter_of(platform)
    if ad.poll is None:
        raise _fail_plain(404)
    info = get_session(str(session or ""), platform, ip)
    try:
        result = ad.poll(str(session or ""), db, info)
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001
        _audit("poll_error", platform, ip)
        return {"ok": True, "status": "pending"}
    if result.get("status") == "done":
        drop_session(str(session or ""))
        _audit("done", platform, ip)
    return {"ok": True, **result}


@router.post("/{platform}/submit")
def open_submit(platform: str, body: SubmitIn, request: Request,
                db: Session = Depends(get_db)):
    """提交回调地址换登录态（Marvis 这类「扫码后把回调贴回来」的平台专用）。

    与轮询分支的区别：`poll` 是服务端替用户去问上游「授权完了吗」，
    而这个平台拿不到那种可轮询的凭据 —— 换票必须用浏览器地址栏那条回调
    里的一次性 `code`，只能由用户贴回来。所以单独给一条提交入口。
    """
    ip = _client_ip(request)
    if not _rate_hit("submit", ip):
        raise _fail_plain(429)
    ad = _adapter_of(platform)
    if getattr(ad, "submit", None) is None:
        raise _fail_plain(404)
    info = get_session(str(body.session or ""), platform, ip)
    try:
        result = ad.submit(str(body.session or ""), str(body.callback or ""),
                           db, info)
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001
        _audit("submit_error", platform, ip)
        logger.debug("公开入口 submit 异常 platform=%s", platform, exc_info=True)
        return {"ok": True, "status": "failed", "error": "提交失败，请重试"}
    if result.get("status") == "done":
        drop_session(str(body.session or ""))
        _audit("done", platform, ip)
    return {"ok": True, **result}


@router.post("/{platform}/cancel")
def open_cancel(platform: str, body: CancelIn, request: Request):
    ip = _client_ip(request)
    if not _rate_hit("cancel", ip):
        raise _fail_plain(429)
    ad = _adapter_of(platform)
    try:
        info = get_session(str(body.session or ""), platform, ip)
    except HTTPException:
        return {"ok": True, "status": "cancelled"}
    try:
        if ad.cancel is not None:
            ad.cancel(str(body.session or ""), info)
    except Exception:  # noqa: BLE001
        pass
    drop_session(str(body.session or ""))
    _audit("cancel", platform, ip)
    return {"ok": True, "status": "cancelled"}


@router.post("/{platform}/sms/send")
def open_sms_send(platform: str, body: SmsSendIn, request: Request,
                  db: Session = Depends(get_db)):
    ip = _client_ip(request)
    if not _rate_hit("sms", ip):
        raise _fail_plain(429)
    ad = _adapter_of(platform)
    if ad.sms_send is None:
        raise _fail_plain(404)
    mobile = str(body.mobile or "").strip()
    if not mobile.isdigit() or not 5 <= len(mobile) <= 15:
        raise _fail_plain(400)
    try:
        result = ad.sms_send(mobile, str(body.country or "86"), db, ip)
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001
        _audit("sms_error", platform, ip)
        raise _fail_plain(502)
    _audit("sms_sent", platform, ip)
    return {"ok": True, **result}


@router.post("/{platform}/sms/verify")
def open_sms_verify(platform: str, body: SmsVerifyIn, request: Request,
                    db: Session = Depends(get_db)):
    ip = _client_ip(request)
    if not _rate_hit("verify", ip):
        raise _fail_plain(429)
    ad = _adapter_of(platform)
    if ad.sms_verify is None:
        raise _fail_plain(404)
    info = get_session(str(body.session or ""), platform, ip)
    code = str(body.code or "").strip()
    if not code or len(code) > 10:
        raise _fail_plain(400)
    try:
        result = ad.sms_verify(str(body.session or ""), info, code,
                               str(body.password or ""), db, ip)
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001
        _audit("verify_error", platform, ip)
        raise _fail_plain(502)
    if result.get("status") == "done":
        drop_session(str(body.session or ""))
        _audit("done", platform, ip, via="sms")
    return {"ok": True, **result}


@router.post("/{platform}/select")
def open_select(platform: str, body: SelectIn, request: Request,
                db: Session = Depends(get_db)):
    """多账号选择（如豆包一个手机号挂多个账号）。"""
    ip = _client_ip(request)
    if not _rate_hit("select", ip):
        raise _fail_plain(429)
    ad = _adapter_of(platform)
    if ad.select is None:
        raise _fail_plain(404)
    info = get_session(str(body.session or ""), platform, ip)
    sec_uid = str(body.sec_uid or "").strip()
    if not sec_uid or len(sec_uid) > 128 or not sec_uid.isalnum() and \
            not sec_uid.replace("-", "").replace("_", "").isalnum():
        raise _fail_plain(400)
    try:
        result = ad.select(str(body.session or ""), info, sec_uid, db, ip)
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001
        _audit("select_error", platform, ip)
        raise _fail_plain(502)
    if result.get("status") == "done":
        drop_session(str(body.session or ""))
        _audit("done", platform, ip, via="select")
    return {"ok": True, **result}


# ---------------------------------------------------------------------------
# 邀请码（与 /join 同一套，失败一律 404）
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# 管理端点（控制台公开入口管理页；要求管理员，不走 join 票据）
# ---------------------------------------------------------------------------
class PlatformFlagIn(BaseModel):
    platforms: dict[str, bool] = {}
    ticket_ttl: int | None = None
    page_copy: dict | None = None


def _admin_guard(request: Request,
                 x_admin_token: str | None = Header(default=None,
                                                    alias="X-Admin-Token")) -> None:
    """管理员校验（浏览器 Cookie 与编程客户端令牌都认）。

    ⚠️ 这里原来直接 `require_admin(request)`：作为**普通函数**调用时，
    FastAPI 的 `Header(...)` 默认值不会被注入，`x_admin_token` 恒为 None，
    于是带 `X-Admin-Token` 的请求永远 401（控制台走 Cookie 没事，脚本/CLI
    全被挡）。参数必须显式声明在这个函数上，由 FastAPI 注入后再透传。
    """
    from admin.security import require_admin

    require_admin(request, x_admin_token=x_admin_token)


@router.get("/admin/config")
def open_admin_config(request: Request, db: Session = Depends(get_db),
                      _guard: None = Depends(_admin_guard)):
    cfg = _load_config(db)
    plats = []
    base = []
    for pid, ad in sorted(_ADAPTERS.items(),
                          key=lambda kv: int(kv[1].spec.get("order") or 99)):
        base.append({"id": pid, "label": str(ad.spec.get("label") or pid),
                     "modes": list(ad.spec.get("modes") or []),
                     "order": int(ad.spec.get("order") or 99)})
    for p in base:
        plats.append({**p, "enabled": bool(cfg["platforms"].get(p["id"], True))})
    return {"ok": True, "platforms": plats,
            "ticket_ttl": cfg["ticket_ttl"],
            "ttl_range": [TTL_MIN, TTL_MAX],
            "copy": cfg["copy"]}


@router.post("/admin/config")
def open_admin_config_save(body: PlatformFlagIn, request: Request,
                           db: Session = Depends(get_db),
                           _guard: None = Depends(_admin_guard)):
    cfg = _load_config(db)
    known = {"workbuddy"} | set(_ADAPTERS.keys())
    for pid, enabled in (body.platforms or {}).items():
        if str(pid) in known:
            cfg["platforms"][str(pid)] = bool(enabled)
    if body.ticket_ttl is not None:
        n = int(body.ticket_ttl)
        # 0 / 负数 = 永久（挂公告的长期链接）；正值钳制在 10 分钟 ~ 7 天。
        cfg["ticket_ttl"] = 0 if n <= 0 else max(TTL_MIN, min(n, TTL_MAX))
    if isinstance(body.page_copy, dict):
        cfg["copy"] = _sanitize_copy(body.page_copy)
    _save_config(db, cfg)
    _audit("config_saved", "-", _client_ip(request),
           plats=cfg["platforms"], ttl=cfg["ticket_ttl"])
    return {"ok": True, "platforms": cfg["platforms"],
            "ticket_ttl": cfg["ticket_ttl"], "copy": cfg["copy"]}


def refresh_account_quota(db: Session, platform: str, account) -> None:
    """公开入口账号入库后的**额度/状态后置处理**（隐性 bug 修复）。

    需求（2026-11-14）：不管账号从哪条路进来（授权登录 / 扫码 / 短信），
    入库即必须是 **active + 额度已知** —— 否则选号机制（min_balance 过滤、
    余额未知排后的排序因子）永远挑不到这些号，公开页贡献的账号等于白进。

    各平台用各自的同步协议；失败只记日志，不影响「已入库」结果
    （定时保活任务会继续补同步）。
    """
    if account is None:
        return
    try:
        if account.status != "active":
            account.status = "active"
        db.commit()
    except Exception:  # noqa: BLE001
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
    try:
        if platform == "raccoon":
            from admin.platform.raccoon.services import accounts as _acc

            _acc.sync_quota(db, account, mark_disabled=False)
        elif platform == "loomy":
            from admin.platform.loomy.services import accounts as _acc

            _acc.sync_quota(db, account, with_models=False)
        else:
            # workbuddy 等平台：额度由既有定时任务同步，这里只保证状态
            pass
        logger.info("公开入口账号额度已刷新 plat=%s acc=%s",
                    platform, getattr(account, "id", "?"))
    except Exception:  # noqa: BLE001 - 首次同步失败不阻塞入库
        logger.warning("公开入口账号额度刷新失败 plat=%s acc=%s",
                       platform, getattr(account, "id", "?"), exc_info=True)


@router.get("/admin/tickets")
def open_admin_tickets(request: Request, db: Session = Depends(get_db),
                       _guard: None = Depends(_admin_guard)):
    """列出**当前代次内仍有效**的访问链接（管理页展示用）。

    链接语义（2026-11-14 与需求对齐）：按时间有效、**公共共享、不限人数**
    —— 有效期内谁拿链接都能注册，不做一人一次的消耗；失效只来自
    过期 / 关闸 / 重新开闸（代次推进）。
    """
    import time as _time

    from admin.models import JoinTicket

    from admin import join_gate as _jg

    epoch = _jg.epoch(db)
    now = _time.time()
    rows = (db.query(JoinTicket)
            .filter(JoinTicket.epoch == epoch)
            .order_by(JoinTicket.id.desc()).limit(100).all())
    items = []
    for r in rows:
        if r.expires_at is None or r.expires_at < datetime.utcnow():
            continue
        permanent = _jg.is_permanent(r.expires_at)
        items.append({"code": r.jti,
                      "permanent": permanent,
                      "expires_in": 0 if permanent else max(
                          0, int((r.expires_at - datetime.utcnow()).total_seconds()))})
    return {"ok": True, "open": _jg.is_open(db), "epoch": epoch,
            "count": len(items), "tickets": items[:50]}

