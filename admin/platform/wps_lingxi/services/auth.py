"""灵犀授权登录：唤起浏览器 → 自动读取登录态入库。

## 为什么这么做

灵犀没有设备码 / 扫码换票的公开协议（客户端走 WPS SSO，凭据就是
`wps_sid` Cookie）。官方 Magpie 插件（`@kevinfx/magpie-wps-lingxi`）的做法是：
用一个**独立的浏览器用户目录**拉起 Chrome，打开登录页，登录完成后自己
从那个目录里读 Cookie。

这里照做，但省掉 CDP：既然 `--user-data-dir` 是我们自己建的，登录后的
`Cookies` 库就在这个目录里，用平台已有的 DPAPI + AES-GCM 读取器直接解即可
（不需要 WebSocket，也不碰用户自己的浏览器资料）。

好处：
  * 用户点一次「授权登录」，浏览器自己弹出来，登录完账号**自动入库**；
  * 不污染用户正在用的浏览器（独立 profile，关掉即删）；
  * 读不到时还能退回「扫描本机 / 粘贴 Cookie」。
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from admin.platform.wps_lingxi.core import endpoints
from admin.platform.wps_lingxi.services import accounts as lingxi_accounts

logger = logging.getLogger("admin.platform.wps_lingxi.auth")

#: 一次授权会话最多活多久（秒）。超时自动关浏览器、删临时目录。
SESSION_TTL = 300.0
#: 并发授权会话上限（防止连点堆一堆浏览器进程）。
MAX_SESSIONS = 3

_SESSIONS: dict[str, dict] = {}
_LOCK = threading.Lock()


def _browser_candidates() -> list[str]:
    """本机可用的 Chromium 系浏览器（能读 Cookies 的那种）。"""
    local = os.getenv("LOCALAPPDATA") or ""
    prog = os.getenv("PROGRAMFILES") or ""
    prog86 = os.getenv("PROGRAMFILES(X86)") or ""
    paths = [
        os.path.join(local, "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(prog, "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(prog86, "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(local, "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(prog, "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(prog86, "Microsoft", "Edge", "Application", "msedge.exe"),
    ]
    return [path for path in paths if os.path.isfile(path)]


def _profile_db(profile: str) -> str:
    """独立 profile 下的 Cookies 库路径（Chrome/Edge 都是这个布局）。"""
    return os.path.join(profile, "Default", "Network", "Cookies")


def _kill(session: dict) -> None:
    proc = session.get("proc")
    if proc is not None:
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except Exception:  # noqa: BLE001 - 关不掉也不影响结果
            logger.debug("关闭授权浏览器失败", exc_info=True)
    profile = session.get("profile") or ""
    if profile and os.path.isdir(profile):
        shutil.rmtree(profile, ignore_errors=True)


def _sweep() -> None:
    """清理超时会话（每次 start/poll 顺手调一下，不另起线程）。"""
    now = time.time()
    with _LOCK:
        stale = [key for key, item in _SESSIONS.items()
                 if now - float(item.get("started") or 0) > SESSION_TTL]
        for key in stale:
            session = _SESSIONS.pop(key, None)
            if session:
                threading.Thread(target=_kill, args=(session,),
                                 daemon=True).start()


def start(db, *, browser: str = "") -> dict:
    """拉起浏览器打开灵犀登录页，返回 `{auth_id, browser, url}`。"""
    _sweep()
    with _LOCK:
        if len(_SESSIONS) >= MAX_SESSIONS:
            return {"ok": False, "reason": "已有授权会话在进行中，先完成或稍后再试"}
    candidates = _browser_candidates()
    exe = browser or (candidates[0] if candidates else "")
    if not exe or not os.path.isfile(exe):
        return {"ok": False, "reason": "没有找到 Chrome / Edge，请改用「扫描本机」或粘贴 Cookie",
                "hint": "也可以先用浏览器登录 https://lingxi.wps.cn 再点「扫描本机」"}
    profile = tempfile.mkdtemp(prefix="lingxi-auth-")
    url = endpoints.BASE
    try:
        proc = subprocess.Popen(
            [exe, "--user-data-dir=%s" % profile, "--no-first-run",
             "--no-default-browser-check", "--disable-features=Translate",
             url],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception as exc:  # noqa: BLE001
        shutil.rmtree(profile, ignore_errors=True)
        return {"ok": False, "reason": "启动浏览器失败：%s" % str(exc)[:120]}

    auth_id = uuid.uuid4().hex[:12]
    with _LOCK:
        _SESSIONS[auth_id] = {"proc": proc, "profile": profile, "started": time.time(),
                              "url": url, "browser": os.path.basename(exe)}
    logger.info("灵犀授权会话已开始 %s（%s）", auth_id, os.path.basename(exe))
    return {"ok": True, "auth_id": auth_id, "browser": os.path.basename(exe),
            "url": url,
            "hint": "浏览器已打开，登录后这里会自动检测并入库"}


def poll(db, auth_id: str) -> dict:
    """查授权结果。拿到 `wps_sid` 就入库并关掉浏览器。"""
    _sweep()
    with _LOCK:
        session = _SESSIONS.get(auth_id)
    if session is None:
        return {"status": "expired",
                "reason": "授权会话不存在或已超时，请重新发起"}

    db_path = _profile_db(session["profile"])
    if os.path.isfile(db_path):
        try:
            values = lingxi_accounts._read_cookies_safe(db_path)  # noqa: SLF001
        except Exception:  # noqa: BLE001 - 登录中库还没写完，下次再读
            values = {}
        sid = (values or {}).get("wps_sid") or ""
        if len(sid) >= 16:
            raw = "wps_sid=%s" % sid
            if values.get("uid"):
                raw += "; uid=%s" % values["uid"]
            try:
                result = lingxi_accounts.import_record(db, raw, probe=True)
            except Exception as exc:  # noqa: BLE001
                return {"status": "error", "reason": str(exc)[:160]}
            with _LOCK:
                _SESSIONS.pop(auth_id, None)
            threading.Thread(target=_kill, args=(session,), daemon=True).start()
            logger.info("灵犀授权成功入库 acc=%s", result.get("account_id"))
            return {"status": "done",
                    "account_id": result.get("account_id"),
                    "uid": result.get("uid"),
                    "name": result.get("account_id_label"),
                    "probe": result.get("probe")}

    elapsed = time.time() - float(session.get("started") or 0)
    return {"status": "pending", "elapsed_s": int(elapsed),
            "remaining_s": max(0, int(SESSION_TTL - elapsed))}


def cancel(auth_id: str) -> dict:
    with _LOCK:
        session = _SESSIONS.pop(auth_id, None)
    if session is None:
        return {"ok": True, "closed": False}
    _kill(session)
    return {"ok": True, "closed": True}