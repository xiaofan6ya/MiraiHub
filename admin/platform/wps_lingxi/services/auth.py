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


# ---------------------------------------------------------------------------
# CDP 读 Cookie（主路径）
# ---------------------------------------------------------------------------
#
# 为什么必须走 CDP：新版 Chromium（Edge 127+ / Chrome 127+）给 Cookie 上了
# **App-Bound Encryption**（密文前缀 `v20`），密钥绑在浏览器自己的服务上，
# DPAPI + AES-GCM 解不开 —— 直接读 Cookies 库只会拿到空结果，表现就是
# 「登录完了也不入库」。CDP 的 `Network.getAllCookies` 由浏览器自己解好再给，
# 这是官方 Magpie 插件用的同一条路。
def _devtools_port(profile: str, timeout: float = 15.0) -> int:
    """等 `DevToolsActivePort` 出现并读出端口。"""
    path = os.path.join(profile, "DevToolsActivePort")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as handle:
                    first = (handle.readline() or "").strip()
                if first.isdigit():
                    return int(first)
            except OSError:
                pass
        time.sleep(0.4)
    return 0


def _cdp_cookies(profile: str) -> list[dict]:
    """通过 CDP 拿该 profile 当前所有 Cookie（浏览器已解密的明文）。"""
    port = _devtools_port(profile, timeout=5.0)
    if not port:
        return []
    import json as _json
    import urllib.request

    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/json/list" % port,
                                    timeout=5) as resp:
            targets = _json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 - 浏览器还在起，下一轮再说
        return []
    if not isinstance(targets, list):
        return []
    pages = [t for t in targets if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
    if not pages:
        return []
    target = next((t for t in pages if "lingxi" in str(t.get("url") or "")
                   or "wps.cn" in str(t.get("url") or "")), pages[0])

    try:
        import websocket  # websocket-client
    except Exception:  # noqa: BLE001 - 没装就退回直读 Cookies 库
        logger.debug("没有 websocket-client，跳过 CDP")
        return []

    ws = None
    try:
        ws = websocket.create_connection(target["webSocketDebuggerUrl"],
                                         timeout=5, suppress_origin=True)
        ws.send(_json.dumps({"id": 1, "method": "Network.enable"}))
        ws.send(_json.dumps({"id": 2, "method": "Network.getAllCookies"}))
        deadline = time.time() + 6
        while time.time() < deadline:
            raw = ws.recv()
            if not raw:
                continue
            try:
                frame = _json.loads(raw)
            except Exception:  # noqa: BLE001
                continue
            if frame.get("id") != 2:
                continue
            cookies = ((frame.get("result") or {}).get("cookies")
                       or frame.get("cookies") or [])
            return [c for c in cookies if isinstance(c, dict)]
    except Exception:  # noqa: BLE001 - CDP 失败不致命，退回直读
        logger.debug("CDP 读 Cookie 失败", exc_info=True)
        return []
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass
    return []


def _cookies_of(session: dict) -> dict:
    """当前浏览器会话里的 `wps_sid` / `uid`（CDP 优先，直读兜底）。"""
    values: dict = {}
    for cookie in _cdp_cookies(session.get("profile") or ""):
        name = str(cookie.get("name") or "")
        domain = str(cookie.get("domain") or "").lower()
        if name not in ("wps_sid", "uid"):
            continue
        if "wps.cn" not in domain and "kdocs" not in domain:
            continue
        value = str(cookie.get("value") or "").strip()
        if not value:
            continue
        if name == "wps_sid":
            # 同名 Cookie 多个域时优先 wps.cn
            if values.get("wps_sid") and "wps.cn" not in domain:
                continue
            values["wps_sid"] = value
        elif "uid" not in values:
            values["uid"] = value
    if values.get("wps_sid"):
        return values

    # 兜底：老版本 Chromium（无 App-Bound Encryption）直接读库也能解
    db_path = _profile_db(session.get("profile") or "")
    if os.path.isfile(db_path):
        try:
            return lingxi_accounts._read_cookies_safe(db_path) or {}  # noqa: SLF001
        except Exception:  # noqa: BLE001
            return {}
    return {}


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
            [exe, "--user-data-dir=%s" % profile, "--remote-debugging-port=0", "--no-first-run",
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

    # CDP 优先（新版 Chromium 的 Cookie 用 App-Bound Encryption，
    # 直接读库解不开）；拿不到再退回直读 Cookies 库。
    values = _cookies_of(session)
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