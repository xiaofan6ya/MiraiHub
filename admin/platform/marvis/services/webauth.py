"""腾讯 Marvis 扫码授权（免客户端 / 免复制回调地址）。

## 它解决什么

原来的授权流程是「拼一条 authorize_url → 用户扫码 → 浏览器跳到
`…/marvis_client_login/marvis_oauth?code=…` → 这个地址在浏览器里打不开
（它是上游 API，不是网页）→ 用户手工把地址栏整段复制回来提交」。
真正卡人的是最后那步人工搬运。

## 为什么不能自己拼 ptlogin 轮询（2026-11-16 实测，别再试一遍）

看起来可行的做法是照 QQ 的 ptlogin 协议自己拉码：

    xlogin → ptqrshow（拿 qrsig）→ ptqrlogin 轮询 → check_sig → login_jump → code

这条**走不通**：

  · `ptqrtoken` 必须按 JS 的 int32 语义算（`e = (e<<5) + charCode` 每步回绕）。
    用 Python 大整数算出来的 token 与 QQ 对不上，`ptqrlogin` 会一直回
    `66`（二维码未失效）—— 手机扫码、点确认都正常，服务端永远看不到 `65`，
    现象就是「二维码没人扫」。这一步修对了**也仍然是 66**，说明还有一层
    （IP / 设备 / 风控）在拦服务端直接发起的扫码会话。
  · 即使拿到 `check_sig`，下一跳 `login_jump` 单独 GET 也只回 200 空页
    —— 它要在完整 OAuth 上下文里才会继续往 `redirect_uri?code=…` 跳。

## 真正能跑通的路径：真浏览器 + DevTools 协议

让**浏览器**去走完整授权（它本来就该这么做），我们只负责把
`marvis_oauth?code=…` 这条 URL 拦下来：

    headless Edge/Chrome（独立 profile）
      → Page.navigate 到 authorize_url
      → 截图交给用户扫（二维码在浏览器里是活的，过期就自动重拍）
      → 用户扫码 + 手机确认
      → 浏览器自己跳到 marvis_oauth?code=…
      → CDP 的 Network.requestWillBeSent / Page.frameNavigated 里拦到它
      → client.exchange_code(code) → accounts.import_record → 入库

2026-11-16 实测 PASS：扫码 → 换登录态 → 入库 → 读钱包全链路走通
（验证用的账号与额度不写进仓库）。

## 依赖

只用标准库（`socket` 手写最小的 CDP WebSocket 客户端），不新增包。
需要本机装有 Edge 或 Chrome —— 桌面版 MiraiHub 天然满足；纯服务器部署没有
浏览器时会明确报错，让调用方回落到「授权页 + 回调提交」那条老路。

## 会话管理

`begin()` 起会话（内存表 + 过期清理 + 后台线程），`poll()` 查状态，
`qr_png()` 取当前的授权页截图，`cancel()` 显式收尾（杀浏览器、删 profile）。
扫码成功后**立刻入库**（复用 `services.accounts.import_record` 的幂等逻辑）。
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid

from admin.platform.marvis.core import client, endpoints

logger = logging.getLogger("admin.platform.marvis.webauth")

PLATFORM = "marvis"

#: 会话存活上限（秒）。浏览器会自己刷码，所以给得比一张码长得多。
SESSION_TTL_S = 600
#: 页面里的二维码约 100 秒失效，到点重拍一张（用户不用管）。
REFRESH_EVERY_S = 90
POLL_INTERVAL_S = 2

#: 浏览器候选路径（按顺序找第一个存在的）
BROWSER_CANDIDATES = (
    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
)

#: 授权页截图尺寸（用户要能扫得清，所以给大一点）
VIEWPORT = {"width": 720, "height": 900, "deviceScaleFactor": 2, "mobile": False}

#: 只截二维码那一块 —— 整页截下来缩到 176px 展示时二维码只有 26px，扫不出来（实测）。
#: 坐标是 CSS 像素，实测二维码落在 (192,152)-(273,237)；这里用 125×125 的框
#: 把它包住并留 ~17px 余量（页面重排时不会切边），放大 4 倍输出 1000×1000。
QR_CLIP = {"x": 175, "y": 130, "width": 125, "height": 125, "scale": 4}

_MAX_SESSIONS = 20
_sessions: dict[str, dict] = {}
_lock = threading.Lock()
_port_lock = threading.Lock()
_next_port = [9411]


# ---------------------------------------------------------------------------
# 最小 CDP WebSocket 客户端（标准库实现，不引第三方包）
# ---------------------------------------------------------------------------
class _CDP:
    """只做三件事：握手、发命令、收事件。

    为什么手写而不是用 `websockets`：`requirements.txt`（服务器版）里没有它，
    为一个授权通道去加运行时依赖不划算；而且这里只需要 text frame，
    分片 / 扩展 / ping 都可以就地处理。
    """

    def __init__(self, host: str, port: int, path: str, timeout: float = 20.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            "GET %s HTTP/1.1\r\n"
            "Host: %s:%d\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: %s\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "Origin: http://%s:%d\r\n"
            "\r\n" % (path, host, port, key, host, port)
        )
        self.sock.sendall(request.encode("ascii"))
        buffer = b""
        while b"\r\n\r\n" not in buffer:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("CDP WebSocket 握手失败（连接被关闭）")
            buffer += chunk
        head, _, rest = buffer.partition(b"\r\n\r\n")
        first = head.split(b"\r\n")[0].decode("latin-1")
        if "101" not in first:
            raise RuntimeError("CDP WebSocket 握手被拒：%s" % first[:120])
        self._buffer = rest
        self._next_id = 0

    def _send_frame(self, text: str) -> None:
        payload = text.encode("utf-8")
        header = bytearray([0x81])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header += length.to_bytes(2, "big")
        else:
            header.append(0x80 | 127)
            header += length.to_bytes(8, "big")
        mask = os.urandom(4)
        header += mask
        masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def _read_exact(self, count: int) -> bytes:
        while len(self._buffer) < count:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("CDP 连接被关闭")
            self._buffer += chunk
        out, self._buffer = self._buffer[:count], self._buffer[count:]
        return out

    def _recv_frame(self) -> str:
        first = self._read_exact(2)
        opcode = first[0] & 0x0F
        masked = bool(first[1] & 0x80)
        length = first[1] & 0x7F
        if length == 126:
            length = int.from_bytes(self._read_exact(2), "big")
        elif length == 127:
            length = int.from_bytes(self._read_exact(8), "big")
        mask = self._read_exact(4) if masked else b""
        payload = self._read_exact(length) if length else b""
        if masked:
            payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        if opcode == 0x8:                      # close
            raise RuntimeError("CDP 主动关闭了连接")
        if opcode == 0x9:                      # ping → pong
            self.sock.sendall(b"\x8a\x00")
            return ""
        if opcode not in (0x1, 0x0):
            return ""
        return payload.decode("utf-8", "replace")

    def send(self, method: str, params: dict | None = None) -> int:
        self._next_id += 1
        self._send_frame(json.dumps(
            {"id": self._next_id, "method": method, "params": params or {}}))
        return self._next_id

    def recv(self, timeout: float = 1.0) -> dict | None:
        """收一条消息；超时返回 None（调用方靠它做心跳与退出检查）。"""
        old = self.sock.gettimeout()
        self.sock.settimeout(timeout)
        try:
            text = self._recv_frame()
        except (socket.timeout, TimeoutError):
            return None
        finally:
            try:
                self.sock.settimeout(old)
            except OSError:
                pass
        if not text:
            return None
        try:
            return json.loads(text)
        except ValueError:
            return None

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def _event_url(msg: dict) -> str:
    """从 CDP 事件里取 URL —— 回调可能在请求、响应、或导航任一处出现。"""
    method = msg.get("method") or ""
    params = msg.get("params") or {}
    if method == "Network.requestWillBeSent":
        return (params.get("request") or {}).get("url") or ""
    if method == "Page.frameNavigated":
        return ((params.get("frame") or {}).get("url")) or ""
    if method == "Network.responseReceived":
        return ((params.get("response") or {}).get("url")) or ""
    return ""


# ---------------------------------------------------------------------------
# 浏览器驱动
# ---------------------------------------------------------------------------
def browser_path() -> str:
    """找一个可用的 Chromium 内核浏览器；找不到返回空串。"""
    for pattern in BROWSER_CANDIDATES:
        path = os.path.expandvars(pattern)
        if path and os.path.isfile(path):
            return path
    for name in ("msedge.exe", "chrome.exe", "chromium", "chromium-browser",
                 "google-chrome", "microsoft-edge"):
        found = shutil.which(name)
        if found:
            return found
    return ""


def _alloc_port() -> int:
    with _port_lock:
        port = _next_port[0]
        _next_port[0] += 1
        if _next_port[0] > 9600:
            _next_port[0] = 9411
    return port


def _wait_target(port: int, deadline: float) -> str:
    """等 CDP 起来，返回第一个 page target 的 WebSocket 路径。"""
    last = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(
                    "http://127.0.0.1:%d/json/list" % port, timeout=2) as resp:
                targets = json.loads(resp.read().decode("utf-8", "replace"))
        except Exception as exc:  # noqa: BLE001
            last = str(exc)
            time.sleep(0.4)
            continue
        for target in targets or []:
            if target.get("type") == "page":
                page_url = str(target.get("webSocketDebuggerUrl") or "")
                match = re.search(r"^wss?://[^/]+(/.*)$", page_url)
                if match:
                    return match.group(1)
        time.sleep(0.4)
    raise RuntimeError("CDP 没起来（%s）" % (last or "没有 page target"))


def _grab_code(url: str) -> str:
    import urllib.parse

    found = re.search(r"[?&#]code=([^&\s\"'<>]+)", str(url or ""))
    return urllib.parse.unquote(found.group(1)) if found else ""


def _reload_and_shoot(cdp: _CDP, sess: dict) -> None:
    """重新加载授权页并截一张图（首张 + 过期重拍都走这里）。"""
    cdp.send("Page.navigate", {"url": sess["auth_url"]})
    # 等 iframe 里的二维码渲染完 —— 8 秒足够（实测授权页 3 秒内出码）
    end = time.time() + 12
    while time.time() < end and not sess.get("stop"):
        cdp.recv(timeout=0.5)
    shot_id = cdp.send("Page.captureScreenshot", {"format": "png", "clip": QR_CLIP})
    end = time.time() + 15
    while time.time() < end:
        msg = cdp.recv(timeout=0.8)
        if not msg:
            continue
        if msg.get("id") != shot_id:
            url = _event_url(msg)
            code = _grab_code(url) if "marvis_oauth" in (url or "") else ""
            if code:
                sess["_early_code"] = code
            continue
        data = ((msg.get("result") or {}).get("data")) or ""
        if data:
            with _lock:
                sess["shot"] = base64.b64decode(data)
                sess["shot_version"] = int(sess.get("shot_version") or 0) + 1
                sess["refreshed_at"] = time.time()
            sess["ready"].set()
        return
    sess["ready"].set()


def _drive(auth_id: str) -> None:
    """后台线程：起浏览器、等扫码、抓 code、入库、收尾。"""
    sess = _sessions.get(auth_id)
    if sess is None:
        return

    browser = browser_path()
    if not browser:
        with _lock:
            sess.update({"status": "failed",
                         "error": "本机没有 Edge/Chrome，无法用浏览器完成 QQ 授权；"
                                  "请改用「微信/QQ 授权页 + 回调提交」那条路"})
        sess["ready"].set()
        return

    port = _alloc_port()
    profile = tempfile.mkdtemp(prefix="marvis-auth-")
    proc = None
    cdp = None
    try:
        proc = subprocess.Popen(
            [browser, "--headless=new", "--disable-gpu", "--no-sandbox",
             "--remote-debugging-port=%d" % port, "--remote-allow-origins=*",
             "--user-data-dir=%s" % profile, "--no-first-run",
             "--no-default-browser-check", "--disable-extensions",
             "--hide-scrollbars", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        path = _wait_target(port, time.time() + 40)
        cdp = _CDP("127.0.0.1", port, path)
        for method, params in (
                ("Network.enable", None),
                ("Page.enable", None),
                ("Emulation.setDeviceMetricsOverride", VIEWPORT)):
            cdp.send(method, params)
        _reload_and_shoot(cdp, sess)

        deadline = time.time() + SESSION_TTL_S
        while not sess.get("stop") and time.time() < deadline:
            early = sess.pop("_early_code", "")
            if early:
                _finish(auth_id, early)
                return
            msg = cdp.recv(timeout=1.0)
            if msg:
                url = _event_url(msg)
                found = _grab_code(url) if "marvis_oauth" in (url or "") else ""
                if found:
                    _finish(auth_id, found)
                    return
            if time.time() - float(sess.get("refreshed_at") or 0) > REFRESH_EVERY_S:
                _reload_and_shoot(cdp, sess)
        with _lock:
            if sess.get("status") not in ("done", "failed"):
                sess["status"] = "expired"
                sess["error"] = "二维码会话已超时，请重新发起"
    except Exception as exc:  # noqa: BLE001 - 后台线程必须自己兜住
        logger.exception("扫码授权线程异常 auth_id=%s", auth_id)
        with _lock:
            sess.update({"status": "failed", "error": "浏览器授权失败：%s" % exc})
        sess["ready"].set()
    finally:
        if cdp is not None:
            cdp.close()
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=8)
            except Exception:  # noqa: BLE001
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass
        shutil.rmtree(profile, ignore_errors=True)


def _finish(auth_id: str, code: str) -> None:
    """拿到 code：换登录态 → 入库 → 读一次钱包。"""
    sess = _sessions.get(auth_id)
    if sess is None:
        return
    try:
        credential = client.exchange_code(
            code, sess.get("login_type") or "QC", "cn", state="",
            guid=sess.get("guid") or "")
    except client.UpstreamError as exc:
        with _lock:
            sess.update({"status": "failed",
                         "error": exc.message or "换取登录态失败"})
        return
    try:
        from admin import db as admin_db
        from admin.platform.marvis.services import accounts as marvis_accounts

        session = admin_db.SessionLocal()
        try:
            account, created = marvis_accounts.import_record(session, credential)
            try:
                marvis_accounts.sync_quota(session, account)
            except Exception:  # noqa: BLE001 - 读钱包失败不影响登录结果
                logger.debug("扫码登录后额度同步失败", exc_info=True)
            view = marvis_accounts.public_view(
                account, quota=marvis_accounts.snapshot_of(account))
        finally:
            session.close()
    except Exception as exc:  # noqa: BLE001
        logger.exception("扫码后入库失败 auth_id=%s", auth_id)
        with _lock:
            sess.update({"status": "failed", "error": "凭证入库失败：%s" % exc})
        return

    with _lock:
        sess.update({"status": "done", "account": view, "error": "",
                     "created": bool(created)})
    logger.info("扫码授权完成 auth_id=%s openid=…%s",
                auth_id, str(view.get("openid", ""))[-6:])


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------
def _cleanup_locked(now: float) -> None:
    stale = [k for k, v in _sessions.items()
             if v.get("expires_at", 0) < now - 60 or v.get("stop")]
    for key in stale:
        _sessions.pop(key, None)
    overflow = len(_sessions) - _MAX_SESSIONS
    if overflow > 0:
        for key in sorted(_sessions, key=lambda k: _sessions[k].get("created_at", 0))[:overflow]:
            _sessions.pop(key, None)


def begin(login_type: str = "QC", guid: str = "") -> dict:
    """建会话：起一个 headless 浏览器打开真实授权页，并截好第一张图。"""
    kind = (login_type or "QC").upper()
    if kind != "QC":
        raise RuntimeError("免客户端扫码只支持 QQ；微信请走授权页 + 回调提交流程")
    if not browser_path():
        raise RuntimeError(
            "本机没有 Edge/Chrome，无法用浏览器完成 QQ 授权；"
            "请改用「授权页 + 回调提交」那条路")

    now = time.time()
    guid = str(guid or uuid.uuid4())
    auth_id = "marvis-qr-%d-%s" % (int(now * 1000), uuid.uuid4().hex[:8])
    sess = {
        "auth_id": auth_id,
        "login_type": kind,
        "guid": guid,
        "auth_url": endpoints.authorize_url(kind, guid),
        "created_at": now,
        "expires_at": now + SESSION_TTL_S,
        "refreshed_at": 0.0,
        "poll_interval": POLL_INTERVAL_S,
        "status": "pending",
        "account": None,
        "error": "",
        "shot": b"",
        "shot_version": 0,
        "ready": threading.Event(),
        "stop": False,
    }
    with _lock:
        _cleanup_locked(now)
        _sessions[auth_id] = sess

    threading.Thread(target=_drive, args=(auth_id,), daemon=True,
                     name="marvis-auth-%s" % auth_id[-6:]).start()

    # ⚠ 这里**不等截图**：浏览器启动 + 授权页渲染要十几秒，同步等会让前端的
    # `POST /auth/begin` 超时（实测页面直接显示裂图）。改成先返回 auth_id，
    # 前端轮询 `shot_version`，图好了再换 `<img>` 的 src。
    return {
        "auth_id": auth_id,
        "login_type": kind,
        "status": "pending",
        "qr": "/api/marvis/auth/qr.png?auth_id=%s&v=%s" % (
            auth_id, sess.get("shot_version") or 0),
        "expires_in": SESSION_TTL_S,
        "poll_interval": POLL_INTERVAL_S,
        "mode": "browser",
        "hint": "正在启动浏览器打开 QQ 授权页，出图后扫码并在手机上确认；"
                "确认后自动入库，不用复制任何地址。二维码过期会自动刷新。",
    }


def qr_png(auth_id: str) -> bytes:
    """当前的授权页截图（前端直接当图显示，用户扫的就是它）。

    `begin()` 不阻塞等图，所以第一次请求这里可能要等十几秒（浏览器启动 +
    渲染）。给足等待时间，别让浏览器端拿到裂图。
    """
    auth_id = str(auth_id or "").strip()
    deadline = time.time() + 40
    while time.time() < deadline:
        with _lock:
            sess = _sessions.get(auth_id)
            if sess is None:
                raise KeyError("授权会话不存在或已过期")
            shot = sess.get("shot") or b""
            failed = sess.get("status") == "failed"
            error = sess.get("error") or ""
        if shot:
            return shot
        if failed:
            raise KeyError(error or "浏览器授权失败")
        time.sleep(0.3)
    raise KeyError("授权页还没渲染出来，请稍后重试")


def session_exists(auth_id: str) -> bool:
    """这个 auth_id 是不是本模块建的（`/auth/poll` 据此分流）。"""
    with _lock:
        return str(auth_id or "").strip() in _sessions


def poll(db, auth_id: str) -> dict:
    """查一次状态。入库在后台线程里完成，这里只读。"""
    auth_id = str(auth_id or "").strip()
    with _lock:
        sess = _sessions.get(auth_id)
        if sess is None:
            return {"status": "expired", "done": False,
                    "error": "授权会话不存在或已过期，请重新发起"}
        return _public(sess)


def cancel(auth_id: str) -> dict:
    auth_id = str(auth_id or "").strip()
    with _lock:
        sess = _sessions.get(auth_id)
        if sess is not None:
            sess["stop"] = True
        removed = _sessions.pop(auth_id, None)
    return {"canceled": removed is not None}


def _public(sess: dict) -> dict:
    status = sess.get("status") or "pending"
    return {
        "status": status,
        "done": status == "done",
        "account": sess.get("account"),
        "error": sess.get("error") or "",
        "created": bool(sess.get("created")),
        "login_type": sess.get("login_type") or "QC",
        "expires_in": max(0, int(sess.get("expires_at", 0) - time.time())),
        "poll_interval": sess.get("poll_interval") or POLL_INTERVAL_S,
        "shot_version": int(sess.get("shot_version") or 0),
    }
