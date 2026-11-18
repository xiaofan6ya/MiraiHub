"""Linux 侧的 uskey 签名客户端 —— 走 wine 签名服务，支持连接复用。

## 为什么需要它

    `beacon_wrapper.dll` 是 Windows x64 PE，Linux 的 ctypes 加载不了。
    所以在 Linux 上把「签名」这一步交给 wine 里跑的
    `tools/uskey_sign_service.py`，网关只发 HTTP。

    架构：

        Linux 原生 python（网关）
            │  HTTP POST /sign  {busiid, bus_info}   ← 持久连接
            ▼
        127.0.0.1:8791  （wine python 跑的签名服务）
            │  ctypes
            ▼
        beacon_wrapper.dll + qimei.dll

## 为什么坚持连接复用

    服务端已开 HTTP/1.1 keep-alive。实测：

        每次新建连接   30.26 ms/次   33 次/秒
        复用连接        2.69 ms/次  371 次/秒

    uskey 是**每个上游请求都要签一次**的，这条路径的延迟会直接加到
    网关的响应时间上。所以这里必须复用连接，不能用 urllib 的一次性
    opener。

## 线程安全

    uvicorn 的工作线程会并发调用。用一个锁保护单条连接；
    连接坏了（异常）就丢弃重建，不影响后续请求。
"""
from __future__ import annotations

import http.client
import json
import logging
import os
import threading
import urllib.parse

log = logging.getLogger(__name__)

#: 签名服务地址。可用环境变量覆盖。
#:
#: 默认端口 8792 而不是 8791 —— 线上 8791 已被 `wb2api-upgrade-gate`
#: 占用（`scripts.upgrade_gate:app`），把签名服务放在 8791 会直接抢端口失败。
#: 实测端口占用情况：
#:     8790  workbuddy2api（网关本体）
#:     8791  wb2api-upgrade-gate（升级等待代理）
#:     8792  marvis-uskey（本服务）
_DEFAULT_URL = "http://127.0.0.1:8792/sign"

#: 单次签名超时（本地 loopback，给足余量但别卡死网关）
_TIMEOUT = float(os.environ.get("MARVIS_USKEY_TIMEOUT", "8"))


class _Client:
    """一条持久连接 + 自动重连。"""

    __slots__ = ("_conn", "_host", "_port", "_path", "_lock")

    def __init__(self, url: str):
        self._lock = threading.Lock()
        self._conn: http.client.HTTPConnection | None = None
        parsed = urllib.parse.urlparse(url)
        self._host = parsed.hostname or "127.0.0.1"
        self._port = parsed.port or 80
        self._path = parsed.path or "/sign"

    # ------------------------------------------------------------------
    def _new_conn(self) -> http.client.HTTPConnection:
        return http.client.HTTPConnection(self._host, self._port,
                                          timeout=_TIMEOUT)

    def _drop(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass
            self._conn = None

    # ------------------------------------------------------------------
    def request(self, payload: bytes) -> dict:
        """发一次请求。连接异常时重连重试一次。"""
        headers = {"Content-Type": "application/json",
                   "Content-Length": str(len(payload))}
        last: Exception | None = None
        for attempt in (0, 1):
            with self._lock:
                if self._conn is None:
                    self._conn = self._new_conn()
                conn = self._conn
                try:
                    conn.request("POST", self._path, payload, headers)
                    resp = conn.getresponse()
                    raw = resp.read()
                    if resp.status != 200:
                        # 业务错误：不重试，直接把上游信息带回去
                        try:
                            data = json.loads(raw.decode("utf-8", "replace"))
                        except Exception:  # noqa: BLE001
                            data = {"ok": False,
                                    "error": "HTTP %d" % resp.status}
                        raise _ServiceError(data.get("error")
                                            or "HTTP %d" % resp.status)
                    return json.loads(raw.decode("utf-8", "replace"))
                except _ServiceError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    # 连接层问题：丢弃连接，下一轮重建
                    last = exc
                    self._drop()
                    if attempt == 1:
                        break
        raise RuntimeError("uskey 签名服务不可达：%s" % (last or "未知"))

    # ------------------------------------------------------------------
    def healthy(self) -> bool:
        try:
            with self._lock:
                if self._conn is None:
                    self._conn = self._new_conn()
                self._conn.request("GET", "/health")
                resp = self._conn.getresponse()
                resp.read()
                return resp.status == 200
        except Exception:  # noqa: BLE001
            with self._lock:
                self._drop()
            return False


class _ServiceError(RuntimeError):
    """服务端明确报错（不是连接问题）。"""


_client: _Client | None = None
_client_url = ""
_client_lock = threading.Lock()


def _service_url() -> str:
    return (os.environ.get("MARVIS_USKEY_SERVICE") or _DEFAULT_URL).strip()


def _get_client() -> _Client:
    """按当前环境变量取客户端；URL 变了就重建。"""
    global _client, _client_url
    url = _service_url()
    with _client_lock:
        if _client is None or _client_url != url:
            _client = _Client(url)
            _client_url = url
        return _client


# ----------------------------------------------------------------------
def available() -> bool:
    """探活。"""
    return _get_client().healthy()


def sign(busiid: str, bus_info: str) -> str:
    """请签名服务签一枚 uskey。失败抛异常，由调用方降级。"""
    payload = json.dumps({"busiid": busiid, "bus_info": bus_info},
                         ensure_ascii=False).encode("utf-8")
    data = _get_client().request(payload)
    if not data.get("ok"):
        raise RuntimeError("uskey 签名失败：%s" % (data.get("error") or "?"))
    uskey = str(data.get("uskey") or "")
    if not uskey:
        raise RuntimeError("uskey 签名服务返回空")
    return uskey


def status() -> dict:
    ok = available()
    return {"mode": "remote", "url": _service_url(),
            "available": ok,
            "error": "" if ok else "签名服务不可达"}
