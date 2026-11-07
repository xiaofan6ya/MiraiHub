"""Chrome DevTools Protocol 的最小封装（供授权登录读凭据）。

只做三件事：

    GET  http://127.0.0.1:<port>/json/list        找页面 target
    WS   Network.enable + Network.getAllCookies   拿浏览器**自己解密后**的 Cookie
    WS   Runtime.evaluate                         在页面里跑 JS 取 API key

**为什么必须走 CDP**：新版 Chromium 的 Cookie 使用 App-Bound Encryption
（密文前缀 `v20`），密钥绑在浏览器服务进程上，DPAPI + AES-GCM 解不开；
直接读 Cookies 库只会得到空结果，表现是「登录完了也不入库」。
CDP 由浏览器自己解密后回话，是可靠路径。
"""
from __future__ import annotations

import json
import logging
import time
import urllib.request

from admin.platform.freebuff.core.ws import WebSocket, WebSocketError, wait_port_file

logger = logging.getLogger("admin.platform.freebuff.cdp")


def devtools_port(profile: str, timeout: float = 15.0) -> int:
    import os

    return wait_port_file(os.path.join(profile, "DevToolsActivePort"), timeout)


def targets(port: int, timeout: float = 5.0) -> list[dict]:
    if not port:
        return []
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/json/list" % port,
                                    timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 - 浏览器还没起完，下一轮再来
        return []
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def pick_page(items: list[dict], prefer: tuple[str, ...] = ()) -> dict | None:
    """挑一个 page target（优先 URL 命中关键词的那个）。"""
    pages = [item for item in items
             if item.get("type") == "page" and item.get("webSocketDebuggerUrl")]
    if not pages:
        return None
    for keyword in prefer:
        for page in pages:
            if keyword in str(page.get("url") or ""):
                return page
    return pages[0]


class Session:
    """一条 CDP WebSocket 会话。"""

    def __init__(self, url: str, timeout: float = 6.0):
        self._ws = WebSocket(url, timeout=timeout)
        self._next = 0

    def call(self, method: str, params: dict | None = None,
             timeout: float = 6.0) -> dict:
        self._next += 1
        call_id = self._next
        self._ws.send(json.dumps({"id": call_id, "method": method,
                                  "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            remaining = max(0.1, deadline - time.time())
            try:
                raw = self._ws.recv(timeout=remaining)
            except WebSocketError:
                raise
            except Exception:  # noqa: BLE001 - 超时/中断按空结果处理
                break
            if not raw:
                continue
            try:
                frame = json.loads(raw)
            except Exception:  # noqa: BLE001
                continue
            if frame.get("id") != call_id:
                continue  # 事件帧，忽略
            if frame.get("error"):
                raise WebSocketError(str(frame["error"])[:200])
            result = frame.get("result")
            return result if isinstance(result, dict) else {}
        return {}

    def close(self) -> None:
        self._ws.close()


def all_cookies(port: int, *, timeout: float = 6.0,
                prefer: tuple[str, ...] = ()) -> list[dict]:
    """`Network.getAllCookies`：返回浏览器解密后的 Cookie 列表。"""
    page = pick_page(targets(port), prefer)
    if page is None:
        return []
    session = None
    try:
        session = Session(page["webSocketDebuggerUrl"], timeout=timeout)
        session.call("Network.enable", timeout=timeout)
        result = session.call("Network.getAllCookies", timeout=timeout)
        cookies = result.get("cookies")
        return [item for item in cookies if isinstance(item, dict)] \
            if isinstance(cookies, list) else []
    except Exception:  # noqa: BLE001 - CDP 失败不致命，调用方会兜底
        logger.debug("CDP 取 Cookie 失败", exc_info=True)
        return []
    finally:
        if session is not None:
            session.close()


def evaluate(port: int, expression: str, *, timeout: float = 8.0,
             prefer: tuple[str, ...] = ()) -> str:
    """在页面里执行 JS，返回字符串结果（失败返回空串）。"""
    page = pick_page(targets(port), prefer)
    if page is None:
        return ""
    session = None
    try:
        session = Session(page["webSocketDebuggerUrl"], timeout=timeout)
        result = session.call("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True,
        }, timeout=timeout)
        value = (result.get("result") or {}).get("value")
        return value if isinstance(value, str) else ""
    except Exception:  # noqa: BLE001
        logger.debug("CDP evaluate 失败", exc_info=True)
        return ""
    finally:
        if session is not None:
            session.close()


def navigate(port: int, url: str, *, timeout: float = 8.0,
             prefer: tuple[str, ...] = ()) -> bool:
    page = pick_page(targets(port), prefer)
    if page is None:
        return False
    session = None
    try:
        session = Session(page["webSocketDebuggerUrl"], timeout=timeout)
        session.call("Page.navigate", {"url": url}, timeout=timeout)
        return True
    except Exception:  # noqa: BLE001
        return False
    finally:
        if session is not None:
            session.close()