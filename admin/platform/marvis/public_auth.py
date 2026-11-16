"""腾讯 Marvis 公开注册入口（扫码 → 粘贴回调地址，免客户端）。

协议与 `admin/platform/marvis/routers/accounts.py` 的控制台授权登录**同源**：
`core.client.authorize_url()` 拼授权页 → 用户手机扫码登录 → 上游 302 到
`marvis_oauth?code=…` → 用户把那条地址粘回来 → `exchange_code()` 换登录态
→ `import_record()` 入库（幂等）。

## 为什么公开入口必须用「粘贴回调」而不是纯二维码

QQ / 微信的登录页有滑块与设备校验，服务端无头浏览器跑不通；而回调地址
（`marvis_oauth?code=…`）是**上游 API 地址**，在浏览器里直接打开只会看到
`-109`（缺 guid）。所以二维码只负责「让用户扫码登录」，真正换票要靠用户
把浏览器地址栏那条回调贴回来 —— 这与控制台的流程完全一致。

## 安全约束

· `state` 由服务端生成、与会话绑定，回调里必须能对上（防串号）；
· `guid` 每个会话独立生成，不跨会话复用；
· 授权码一次性：同一 code 重复提交直接返回上次结果（`reused`）。
"""
from __future__ import annotations

import logging
import re
import uuid

from admin.platform.marvis.core import client as marvis_client
from admin.platform.marvis.core import endpoints as marvis_endpoints
from admin.platform.marvis.services import accounts as marvis_accounts
from admin.security import clean_display_name

logger = logging.getLogger("admin.public_auth.marvis")

spec = {"platform": "marvis", "label": "腾讯 Marvis", "modes": ["qr"],
        "order": 60}

#: 从回调地址 / 裸 code / JSON 里提取 code（与控制台同一套宽松规则）。
_CODE_RE = re.compile(r"(?:^|[?&#])code=([A-Za-z0-9_\-]+)", re.I)
_BARE_RE = re.compile(r"^[A-Za-z0-9_\-]{8,}$")


def _extract_code(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    hit = _CODE_RE.search(raw)
    if hit:
        return hit.group(1)
    # 裸 code（用户只复制了那一串）
    if _BARE_RE.match(raw):
        return raw
    return ""


def begin(mode: str, db, ip: str) -> dict:
    """开一个扫码会话：返回授权页地址 + 二维码素材。

    `login_type` 固定 QC（QQ）—— 微信的扫码页对未登录设备要求更严，
    公开入口先只放 QQ 这条成功率高的路径；要开微信在控制台里开。
    """
    from admin import public_auth

    login_type = "QC"
    guid = str(uuid.uuid4())
    state = marvis_endpoints.new_oauth_state(login_type, guid)
    auth_url = marvis_endpoints.authorize_url(login_type, guid, state=state)

    sid = public_auth.register_session("marvis", "qr", ip, {
        "login_type": login_type,
        "guid": guid,
        "state": state,
        "auth_url": auth_url,
    })
    return {"session": sid,
            "auth_url": auth_url,
            "login_type": login_type,
            # 前端据此提示「登录完把地址栏那条地址复制回来」
            "need_callback": True,
            "expires_in": 600}


def submit(session_id: str, callback: str, db, info: dict) -> dict:
    """用用户粘贴回来的回调换登录态并入库。"""
    code = _extract_code(callback)
    if not code:
        return {"status": "failed",
                "error": "回调里没有 code=（请把整条回调地址复制回来）"}

    data = info.get("data") or {}
    guid = str(data.get("guid") or "")
    state = str(data.get("state") or "")
    login_type = str(data.get("login_type") or "QC")

    try:
        credential = marvis_client.exchange_code(
            code, login_type, "cn", state=state, guid=guid)
    except marvis_client.UpstreamError as exc:
        logger.info("Marvis 公开入口换取失败 code=%s", getattr(exc, "code", ""))
        return {"status": "failed", "error": exc.message}

    try:
        account, created = marvis_accounts.import_record(db, credential)
    except ValueError as exc:
        return {"status": "failed", "error": str(exc)}

    _sanitize_and_consume(db, account)
    return {"status": "done",
            "account": {"name": _mask(account.name), "created": created}}


def poll(session_id: str, db, info: dict) -> dict:
    """公开入口是「粘贴回调」式，没有服务端可轮询的状态 —— 永远 pending。

    保留这个函数是因为 `public_auth` 的通用轮询分支会调用它；用户提交
    回调后由 `submit` 直接把会话标记为 done（见 `public_auth` 的 submit 路由）。
    """
    return {"status": "pending"}


def cancel(session_id: str, info: dict) -> None:
    return None


def _sanitize_and_consume(db, account) -> None:
    """入库收尾：显示名去毒 + 额度同步（失败不影响入库结果）。"""
    try:
        safe = (clean_display_name(account.name, "")
                or clean_display_name(account.uid, "") or "Marvis 账号")
        if safe != account.name:
            account.name = safe
        db.commit()
    except Exception:  # noqa: BLE001
        logger.debug("Marvis 公开入口账号名去毒失败", exc_info=True)
    try:
        from admin import public_auth

        public_auth.refresh_account_quota(db, "marvis", account)
    except Exception:  # noqa: BLE001
        logger.debug("Marvis 公开入口额度同步失败", exc_info=True)


def _mask(name: str) -> str:
    s = (name or "").strip()
    if not s:
        return ""
    if len(s) <= 4:
        return s[:1] + "**"
    return s[:2] + "***" + s[-2:]
