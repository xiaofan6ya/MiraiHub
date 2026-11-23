"""AStudio「授权登录」：引导浏览器登录 + 本机登录态自动导入。

## 为什么这里没有「换票」

AStudio 的登录态是**网页会话 Cookie**（`ssoSessionId` + `account_id` + `token`），
官方没有给第三方留设备码 / OAuth 端点 —— 它自己的桌面端也是「打开登录页、
登录完把 Cookie 和换来的模型凭据写进 `userdata/astron-session.json`」。
所以网关能拿到凭据的唯一入口，就是**本机的那个文件**。

于是这里的「授权登录」做成一条引导链：

    点「授权登录」 → 弹窗给官网/客户端入口
        → 用户在桌面端登录一次
        → 网关盯着 `astron-session.json` 的 mtime/大小
        → 一变化就自动入库并回「成功」，不用再点「扫描本机」

对用户来说还是「登录一次就好」，只是那一次发生在官方客户端里，而不是在我们
自己的浏览器窗口里 —— 这点在弹窗文案里写清楚，不假装是网页授权。

## 没有客户端怎么办

`begin()` 会先扫一遍：本机根本没有 AStudio 数据目录时，返回值里带
`installed: false`，前端据此直接显示「先装客户端」这一步，而不是让用户
傻等 5 分钟。
"""
from __future__ import annotations

import logging
import os
import threading
import time

from sqlalchemy.orm import Session

from admin.platform.astudio.core import paths
from admin.platform.astudio.services import accounts as astudio_accounts

logger = logging.getLogger("admin.platform.astudio.webauth")

#: 官网（含桌面端下载入口）
OFFICIAL_URL = "https://agent.xfyun.cn/"
#: 一次「去登录」最多等多久
TTL_S = 300

_SESSIONS: dict[str, dict] = {}
_LOCK = threading.Lock()


def _snapshot() -> dict:
    """当前本机登录态文件的 (mtime, size) 快照。"""
    snapshot = {}
    for path in paths.session_files() + paths.config_files():
        try:
            stat = os.stat(path)
        except OSError:
            continue
        snapshot[path] = (int(stat.st_mtime), int(stat.st_size))
    return snapshot


def _sweep() -> None:
    now = time.time()
    with _LOCK:
        for auth_id, session in list(_SESSIONS.items()):
            if session["status"] == "pending" and now - session["created"] > TTL_S:
                session["status"] = "expired"
            if now - session["created"] > TTL_S * 2:
                _SESSIONS.pop(auth_id, None)


def begin() -> dict:
    """记录基线并返回引导信息（前端据此展示「去登录」）。"""
    _sweep()
    baseline = _snapshot()
    auth_id = os.urandom(12).hex()
    with _LOCK:
        _SESSIONS[auth_id] = {
            "auth_id": auth_id,
            "created": time.time(),
            "baseline": baseline,
            "status": "pending",
            "account_id": 0,
            "account": "",
            "error": "",
        }
    logger.info("AStudio 授权引导已开始：%s（基线 %d 个登录态文件）",
                auth_id, len(baseline))
    return {
        "auth_id": auth_id,
        "url": OFFICIAL_URL,
        "installed": bool(baseline),
        "expires_in": TTL_S,
        "hint": ("在本机 AStudio 桌面端登录一次，登录完成会自动导入"
                 if baseline else
                 "这台机器上没找到 AStudio 登录态：先装桌面端并登录一次"),
    }


def _public(session: dict) -> dict:
    return {
        "auth_id": session["auth_id"],
        "status": session["status"],
        "expires_in": max(0, int(TTL_S - (time.time() - session["created"]))),
        "account_id": session["account_id"],
        "account": session["account"],
        "error": session["error"],
    }


def poll(db: Session, auth_id: str) -> dict:
    """看登录态文件有没有变；变了就导入。"""
    _sweep()
    with _LOCK:
        session = _SESSIONS.get(auth_id)
    if session is None:
        return {"status": "expired", "error": "授权会话不存在或已过期"}
    if session["status"] != "pending":
        return _public(session)

    current = _snapshot()
    changed = {path: stamp for path, stamp in current.items()
               if session["baseline"].get(path) != stamp}
    if not changed:
        return _public(session)

    # 文件变了 == 客户端刚登录/刚续期。复用扫描入库那条路径（它会按 uid 幂等），
    # 只导入变化的那几份，避免把用户手工禁用的老号又翻出来。
    imported = 0
    errors: list[str] = []
    for path in changed:
        record = (paths.read_config_token(path)
                  if path.endswith("config.toml") else paths.read_session(path))
        if not record or not record.get("key"):
            continue
        try:
            account, _created = astudio_accounts.import_record(db, record)
            imported += 1
            session["account_id"] = int(account.id or 0)
            session["account"] = account.name or ""
        except Exception as exc:  # noqa: BLE001 - 单条失败不拖垮整次授权
            errors.append(str(exc)[:160])
            logger.warning("AStudio 登录态导入失败 %s：%s", path, exc)
    if imported:
        session.update(status="done", error="")
    elif errors:
        session.update(status="failed", error="；".join(errors)[:300])
    return _public(session)


def cancel(auth_id: str) -> dict:
    with _LOCK:
        session = _SESSIONS.get(auth_id)
    if session is None:
        return {"status": "expired"}
    if session["status"] == "pending":
        session["status"] = "canceled"
    return _public(session)
