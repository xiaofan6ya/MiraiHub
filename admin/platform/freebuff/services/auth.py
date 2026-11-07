"""Freebuff 授权登录：唤起独立浏览器 → 自动抓 Developer API key 入库。

## 为什么这么做

Freebuff 没有「设备码 / 扫码换票」的公开协议，它的凭据就是账户页上那把
**Developer API key**（`fb_...`，https://freebuff.com/account/api）。
所以这里的做法是：用**独立的浏览器用户目录**拉起 Chrome/Edge，打开账户页，
用户正常登录（不用手抄），登录完成后我们自己从那个 profile 里把 key 读出来。

* 独立 profile：不碰用户正在用的浏览器资料，关掉即删；
* **走 CDP**：新版 Chromium 的 Cookie 用 App-Bound Encryption（`v20`），
  DPAPI + AES-GCM 解不开；CDP 由浏览器自己解密后回话；
* 三条抓取路径（页面文本 / 表单值 / localStorage），任一路径拿到完整 key 即入库；
* 抓不到就如实返回 `pending` 并给下一步提示，绝不假装成功。

## 与「粘贴导入」的关系

授权登录是**便利路径**，不是唯一路径：没装浏览器、或页面把 key 打了码
（`fb_1a2b…9z8y`）时，仍然可以「复制 key → 粘贴导入」。
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from admin.platform.freebuff.core import cdp, endpoints
from admin.platform.freebuff.services import accounts as fb_accounts

logger = logging.getLogger("admin.platform.freebuff.auth")

#: 一次授权会话最多活多久（秒）。超时自动关浏览器、删临时目录。
SESSION_TTL = 300.0
#: 并发授权会话上限（防止连点堆一堆浏览器进程）。
MAX_SESSIONS = 3

#: 账户页上 API key 的形状（`fb_` + 足够长的连续字符）。
#: 刻意排除 `*` `…` `.` 与空白：打码的 key 宁可抓不到，也不要把半截当 key。
KEY_PATTERN = re.compile(r"fb_[A-Za-z0-9_\-]{20,}")

_SESSIONS: dict[str, dict] = {}
_LOCK = threading.Lock()

#: 页面上要跑的那段 JS：把所有可能藏 key 的地方一次性吐出来。
_SCRAPE_JS = r"""
(function () {
  var out = {url: '', text: '', html: '', cookie: '', values: '', ls: '', ss: ''};
  try { out.url = location.href; } catch (e) {}
  try { out.text = document.body ? document.body.innerText : ''; } catch (e) {}
  try { out.html = (document.documentElement && document.documentElement.innerHTML || '').slice(0, 400000); } catch (e) {}
  try { out.cookie = document.cookie || ''; } catch (e) {}
  try {
    var vals = [];
    var nodes = document.querySelectorAll('input, textarea, code, pre');
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      if (n.value) { vals.push(n.value); }
      var dv = n.getAttribute && (n.getAttribute('data-key') || n.getAttribute('value'));
      if (dv) { vals.push(dv); }
    }
    out.values = vals.join('\n');
  } catch (e) {}
  try {
    var ls = [];
    for (var j = 0; j < localStorage.length; j++) {
      var k = localStorage.key(j);
      ls.push(k + '=' + localStorage.getItem(k));
    }
    out.ls = ls.join('\n');
  } catch (e) {}
  try {
    var ss = [];
    for (var m = 0; m < sessionStorage.length; m++) {
      var sk = sessionStorage.key(m);
      ss.push(sk + '=' + sessionStorage.getItem(sk));
    }
    out.ss = ss.join('\n');
  } catch (e) {}
  return JSON.stringify(out);
})()
"""


def _browser_candidates() -> list[str]:
    """本机可用的 Chromium 系浏览器（能开远程调试端口的）。"""
    local = os.getenv("LOCALAPPDATA") or ""
    prog = os.getenv("PROGRAMFILES") or ""
    prog86 = os.getenv("PROGRAMFILES(X86)") or ""
    paths = []
    if sys.platform == "win32":
        paths = [
            os.path.join(local, "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(prog, "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(prog86, "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(local, "Microsoft", "Edge", "Application", "msedge.exe"),
            os.path.join(prog, "Microsoft", "Edge", "Application", "msedge.exe"),
            os.path.join(prog86, "Microsoft", "Edge", "Application", "msedge.exe"),
            os.path.join(local, "BraveSoftware", "Brave-Browser", "Application",
                         "brave.exe"),
        ]
    elif sys.platform == "darwin":
        paths = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
    else:
        paths = [
            "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium", "/usr/bin/chromium-browser",
            "/usr/bin/microsoft-edge",
        ]
    extra = (os.getenv("ADMIN_FREEBUFF_BROWSER") or "").strip()
    if extra:
        paths.insert(0, extra)
    return [path for path in paths if os.path.isfile(path)]


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


def _scrape(port: int) -> dict:
    """在页面里跑一遍抓取脚本，返回原始文本集合。"""
    raw = cdp.evaluate(port, _SCRAPE_JS, prefer=("freebuff",))
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def _find_key(scraped: dict) -> str:
    """从抓取结果里挑一把完整 key（打码的一律不要）。"""
    if not scraped:
        return ""
    for field in ("values", "ls", "ss", "text", "html", "cookie"):
        text = scraped.get(field)
        if not isinstance(text, str) or not text:
            continue
        for match in KEY_PATTERN.finditer(text):
            candidate = match.group(0)
            # 打码形式会带省略号/星号，正则已排除；再挡一次「明显是缩短的」
            if "..." in candidate or "…" in candidate or "*" in candidate:
                continue
            return candidate
    return ""


def _login_cookies(port: int) -> dict:
    """浏览器当前的 freebuff 会话 Cookie（用来判断「登录了没」）。"""
    values: dict = {}
    for cookie in cdp.all_cookies(port, prefer=("freebuff",)):
        domain = str(cookie.get("domain") or "").lower()
        if "freebuff" not in domain:
            continue
        name = str(cookie.get("name") or "")
        if name:
            values[name] = str(cookie.get("value") or "")
    return values


def start(db, *, browser: str = "") -> dict:
    """拉起浏览器打开 Freebuff 账户页，返回 `{auth_id, browser, url}`。"""
    _sweep()
    with _LOCK:
        if len(_SESSIONS) >= MAX_SESSIONS:
            return {"ok": False,
                    "reason": "已有授权会话在进行中，先完成或稍后再试"}
    candidates = _browser_candidates()
    exe = browser or (candidates[0] if candidates else "")
    if not exe or not os.path.isfile(exe):
        return {"ok": False,
                "reason": "没有找到 Chrome / Edge，请改用「粘贴导入」",
                "hint": "也可以先在浏览器登录 freebuff.com/account/api，"
                        "复制 Developer API key 后粘进来"}
    profile = tempfile.mkdtemp(prefix="freebuff-auth-")
    url = endpoints.ACCOUNT_URL
    try:
        proc = subprocess.Popen(
            [exe, "--user-data-dir=%s" % profile,
             "--remote-debugging-port=0", "--no-first-run",
             "--no-default-browser-check", "--disable-features=Translate",
             url],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception as exc:  # noqa: BLE001
        shutil.rmtree(profile, ignore_errors=True)
        return {"ok": False, "reason": "启动浏览器失败：%s" % str(exc)[:120]}

    auth_id = uuid.uuid4().hex[:12]
    with _LOCK:
        _SESSIONS[auth_id] = {
            "proc": proc, "profile": profile, "started": time.time(),
            "url": url, "browser": os.path.basename(exe),
            "navigated": False,
        }
    logger.info("Freebuff 授权会话已开始 %s（%s）", auth_id, os.path.basename(exe))
    return {
        "ok": True, "auth_id": auth_id, "browser": os.path.basename(exe),
        "url": url,
        "hint": "浏览器已打开：登录后停在 https://freebuff.com/account/api，"
                "这里会自动检测；如果页面把 key 打了码，点一次「复制/显示」",
    }


def poll(db, auth_id: str) -> dict:
    """查授权结果。抓到完整 key 就入库并关掉浏览器。"""
    _sweep()
    with _LOCK:
        session = _SESSIONS.get(auth_id)
    if session is None:
        return {"status": "expired",
                "reason": "授权会话不存在或已超时，请重新发起"}

    profile = str(session.get("profile") or "")
    port = cdp.devtools_port(profile, timeout=4.0)
    if not port:
        elapsed = time.time() - float(session.get("started") or 0)
        return {"status": "pending", "stage": "browser_starting",
                "elapsed_s": int(elapsed),
                "remaining_s": max(0, int(SESSION_TTL - elapsed)),
                "hint": "浏览器还在启动"}

    scraped = _scrape(port)
    key = _find_key(scraped)
    cookies = _login_cookies(port)
    logged_in = bool(cookies)

    # 已登录但还停在首页 → 自动跳到账户页（用户可能只登录了没进 API 页）
    if not key and logged_in and not session.get("navigated"):
        current = str(scraped.get("url") or "")
        if "account/api" not in current:
            cdp.navigate(port, endpoints.ACCOUNT_URL, prefer=("freebuff",))
            session["navigated"] = True
            return {"status": "pending", "stage": "navigating_to_account",
                    "hint": "已登录，正在打开 API 页面…"}

    if key:
        try:
            result = fb_accounts.import_record(db, {"key": key}, probe_upstream=False)
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "reason": str(exc)[:160]}
        probe_result = fb_accounts.probe(db, _account_of(db, result.get("account_id")))
        with _LOCK:
            _SESSIONS.pop(auth_id, None)
        threading.Thread(target=_kill, args=(session,), daemon=True).start()
        logger.info("Freebuff 授权成功入库 acc=%s", result.get("account_id"))
        return {"status": "done",
                "account_id": result.get("account_id"),
                "key_tail": result.get("key_tail"),
                "probe": probe_result}

    elapsed = time.time() - float(session.get("started") or 0)
    hint = ("已登录，但页面上没有完整 key —— 在账户页点一次"
            "「显示 / 复制」后再等一下"
            if logged_in else
            "请在刚打开的浏览器里登录 Freebuff")
    return {"status": "pending", "stage": "logged_in" if logged_in else "not_logged_in",
            "elapsed_s": int(elapsed),
            "remaining_s": max(0, int(SESSION_TTL - elapsed)),
            "hint": hint}


def _account_of(db, account_id):
    from admin.models import Account

    return db.query(Account).filter(Account.id == int(account_id)).first()


def cancel(auth_id: str) -> dict:
    with _LOCK:
        session = _SESSIONS.pop(auth_id, None)
    if session is None:
        return {"ok": True, "closed": False}
    _kill(session)
    return {"ok": True, "closed": True}


def url() -> str:
    return endpoints.ACCOUNT_URL


def browser_available() -> dict:
    candidates = _browser_candidates()
    return {"ok": bool(candidates), "candidates": candidates}