"""最小 WebSocket 客户端（只用标准库，服务于 CDP）。

## 为什么要自己写

授权登录要靠 CDP 的 `Network.getAllCookies` / `Runtime.evaluate` 从浏览器
里取凭据。新版 Chromium（Edge 127+ / Chrome 127+）给 Cookie 上了
**App-Bound Encryption**（密文前缀 `v20`），密钥绑在浏览器自己的服务进程上，
DPAPI + AES-GCM 解不开 —— 直接读 Cookies 库只会拿到空结果。

`websocket-client` 不是本项目依赖（`requirements.txt` 里没有），而这里只需要
「连 127.0.0.1 发两条 JSON 读两条 JSON」，用标准库写 100 行比加一个依赖划算，
也避免「库没装 → CDP 静默跳过 → 登录完不入库」这种难查的坑。
"""
from __future__ import annotations

import base64
import os
import socket
import struct
import time
from urllib.parse import urlparse

_OP_CONT = 0x0
_OP_TEXT = 0x1
_OP_CLOSE = 0x8
_OP_PING = 0x9
_OP_PONG = 0xA


class WebSocketError(Exception):
    pass


class WebSocket:
    """极简 ws:// 客户端（够 CDP 用：发文本帧、收文本帧、应答 ping）。"""

    def __init__(self, url: str, timeout: float = 5.0):
        parsed = urlparse(url)
        if parsed.scheme != "ws":
            raise WebSocketError("只支持 ws://，收到 %s" % url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 80
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            "GET %s HTTP/1.1\r\n"
            "Host: %s:%d\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: %s\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n" % (path, host, port, key)
        )
        self._sock.sendall(request.encode("ascii"))
        response = self._read_until(b"\r\n\r\n")
        head = response.decode("latin-1", "replace")
        if "101" not in head.split("\r\n", 1)[0]:
            raise WebSocketError("握手失败：%s" % head.split("\r\n", 1)[0])

    # ---- 底层 ----
    def _read_until(self, marker: bytes) -> bytes:
        buffer = b""
        while marker not in buffer:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise WebSocketError("连接在握手阶段被关闭")
            buffer += chunk
        return buffer

    def _recv_exact(self, size: int) -> bytes:
        data = b""
        while len(data) < size:
            chunk = self._sock.recv(size - len(data))
            if not chunk:
                raise WebSocketError("连接已关闭")
            data += chunk
        return data

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        header = bytearray()
        header.append(0x80 | opcode)  # FIN + opcode
        mask = os.urandom(4)
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < (1 << 16):
            header.append(0x80 | 126)
            header.extend(struct.pack(">H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack(">Q", length))
        header.extend(mask)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self._sock.sendall(bytes(header) + masked)

    # ---- 对外 ----
    def send(self, text: str) -> None:
        self._send_frame(_OP_TEXT, text.encode("utf-8"))

    def recv(self, timeout: float | None = None) -> str:
        """读一条文本消息（自动应答 ping、处理分片）。"""
        if timeout is not None:
            self._sock.settimeout(timeout)
        chunks: list[bytes] = []
        while True:
            first = self._recv_exact(2)
            opcode = first[0] & 0x0F
            fin = bool(first[0] & 0x80)
            masked = bool(first[1] & 0x80)
            length = first[1] & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._recv_exact(8))[0]
            mask = self._recv_exact(4) if masked else b""
            payload = self._recv_exact(length) if length else b""
            if masked:
                payload = bytes(byte ^ mask[index % 4]
                                for index, byte in enumerate(payload))
            if opcode == _OP_PING:
                self._send_frame(_OP_PONG, payload)
                continue
            if opcode == _OP_PONG:
                continue
            if opcode == _OP_CLOSE:
                raise WebSocketError("对端关闭连接")
            chunks.append(payload)
            if fin:
                return b"".join(chunks).decode("utf-8", "replace")

    def close(self) -> None:
        try:
            self._send_frame(_OP_CLOSE, b"")
        except Exception:  # noqa: BLE001 - 关连接失败不影响结果
            pass
        try:
            self._sock.close()
        except Exception:  # noqa: BLE001
            pass


def wait_port_file(path: str, timeout: float = 15.0) -> int:
    """等 Chromium 写出 `DevToolsActivePort`，返回调试端口（失败返回 0）。"""
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