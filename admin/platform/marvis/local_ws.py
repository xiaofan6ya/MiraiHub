r"""Marvis **本机客户端 WS** 诊断工具 —— 仅供排障 / 抓包复现，**不是生产传输**。

## 定位（2026-11-17 方向变更）

本机客户端网关（127.0.0.1:10123）真机验证过能拿到真模型回复，但**这条路线
已被否决**：生产网关必须像以前一样**直连上游 HTTP 接口**，不依赖用户桌面上的
Marvis 客户端（客户端一关就断、只能单账号、token 每次重启都变）。

所以本模块只作为**诊断工具**保留，两个用途：

  · read_token()：从日志里现读本机网关 token（排障 / 验证）；
  · run_agent()：直接驱动客户端跑一次真实对话 —— 抓包复现时用它造一条真实
    请求，看清客户端到底发到哪里、发了什么。

⛔ **gateway.py 不引用本模块**：没有 MIRAIHUB_MARVIS_VIA_LOCAL_WS 分支，
   chat_completions / _attempt / _acquire 的主流程保持直连上游。

## 实测链路（仅供理解协议）

    MiraiHub 网关 --ws--> 127.0.0.1:10123 (Marvis 客户端) --> 腾讯云端

## 协议（2026-11-17 真机实测一次通过）

    连接   ws://127.0.0.1:10123?token=<36 位 UUID>
           ⚠ 是 :10123?token=，**不是** :10123/?token=；也不传 subprotocol
    token  %LOCALAPPDATA%\Temp\Tencent\Marvis\logs\MarvisHostStd.log
           'config initialized, local_ws_url=ws://127.0.0.1:10123/?token=...'
           备用 Marvis.log 'AddGatewayTokenListener {"token":"...","port":10123}'
    帧     {"event": "<name>", "requestId": "<递增字符串>", "payload": {...}}
    ACK    {"type": "ack", "requestId": "1", "data": {"ok": true, ...}}

    1. 连上后等 {"event":"gateway.connected","data":{"agentConnectionStatus":"Ready"}}
    2. agent.action / conversations.create  → ACK data.data.conversation_id
    3. agent.run {conversation_id, message, response_id, attachments, ...}
    4. 收 ag_ui_event 流：
         TEXT_MESSAGE_CONTENT  .delta                → 正文增量
         REASONING_CONTENT     .delta                → 思考链增量（可选）
         CUSTOM name=message_complete .value.content → 终稿全文
         TEXT_MESSAGE_END      .rawEvent.token_usage → 用量
         RUN_FINISHED                                → 结束

## 为什么是**同步**实现

诊断是「跑一次、看结果」：脚本 / 测试里直接调最省事，不需要事件循环。
真要在 async 上下文里用，套一层 asyncio.to_thread 即可。

## 为什么是**手写 RFC6455**（零新依赖）

容器镜像里没有 websocket-client（requirements.txt 也没有），而仓库偏好
极简依赖。WS 客户端侧需要的只有：HTTP Upgrade 握手 + 4 字节掩码 + 帧解析，
不到 150 行，比多引一个包可控。

⚠ 本模块**不打印 token**：异常与日志里只有 host:port，连接串里的 token=
在 display_url 里已经被剥掉。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import socket
import ssl
import struct
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

#: 本机 Marvis 客户端的默认 WS 端口（实测固定 10123）。
DEFAULT_PORT = 10123
#: 建会话时用的标题（会显示在客户端的会话列表里，只用于排障辨认）。
DEFAULT_TITLE = "miraihub"
#: Sec-WebSocket-Accept 的固定 GUID（RFC6455 §1.3）。
_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
#: 握手 / 单帧读取的兜底超时（秒）；整体等待由 run_agent(timeout=...) 管。
_HANDSHAKE_TIMEOUT = 15.0


class LocalWSError(RuntimeError):
    """本机 Marvis 客户端不可用：token 读不到 / 连不上 / 握手失败 / 流中断。"""


# ---------------------------------------------------------------------------
# token：每次客户端重启都会变，所以**每次请求现读**
# ---------------------------------------------------------------------------
#: local_ws_url=ws://127.0.0.1:10123/?token=<uuid>&handler=openclaw
_URL_RE = re.compile(r"local_ws_url\s*=\s*(wss?://[^\s\"'<>]+)")
#: {"token":"<uuid>","port":10123}（AddGatewayTokenListener 那行）
_JSON_RE = re.compile(r"\{[^{}]*\"token\"[^{}]*\}")


def default_token_files() -> list[str]:
    """默认要扫的日志（按优先级）。

    MarvisHostStd.log 是首选：它在启动时打一行 config initialized,
    local_ws_url=...，token 与端口都在这一行里。Marvis.log 是备用：
    AddGatewayTokenListener 只带 token + port。
    """
    roots: list[Path] = []
    local_appdata = str(os.environ.get("LOCALAPPDATA") or "").strip()
    if local_appdata:
        roots.append(Path(local_appdata) / "Temp")
    # 兜底：服务以别的账户跑、或 LOCALAPPDATA 没继承时按用户目录推。
    roots.append(Path.home() / "AppData" / "Local" / "Temp")
    out: list[str] = []
    for root in roots:
        logs = root / "Tencent" / "Marvis" / "logs"
        for name in ("MarvisHostStd.log", "Marvis.log"):
            candidate = str(logs / name)
            if candidate not in out:
                out.append(candidate)
    return out


def _read_log(path: Path) -> str:
    """读日志文本。Windows 日志可能混着控制字符，**只要求能扫到子串**。"""
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", "replace")


def parse_token_text(text: str) -> tuple[str, int] | None:
    """从日志文本里抽出 (token, port)；两种格式都认，**后出现的优先**。

    「后出现优先」不是随手写的：同一个进程里客户端可能重建过网关
    （换 token / 换端口），日志是追加的，最后一条才是当前有效的。
    """
    if not isinstance(text, str) or not text:
        return None
    best: tuple[int, str, int] | None = None
    for match in _URL_RE.finditer(text):
        raw = match.group(1).rstrip("&,)")
        try:
            parts = urlsplit(raw)
        except ValueError:
            continue
        token = (parse_qs(parts.query).get("token") or [""])[0].strip()
        if not token:
            continue
        port = parts.port or DEFAULT_PORT
        if best is None or match.start() >= best[0]:
            best = (match.start(), token, int(port))
    for match in _JSON_RE.finditer(text):
        try:
            parsed = json.loads(match.group(0))
        except ValueError:
            continue
        if not isinstance(parsed, dict):
            continue
        token = str(parsed.get("token") or "").strip()
        if not token:
            continue
        try:
            port = int(parsed.get("port") or DEFAULT_PORT)
        except (TypeError, ValueError):
            port = DEFAULT_PORT
        if best is None or match.start() >= best[0]:
            best = (match.start(), token, port)
    if best is None:
        return None
    return best[1], best[2]


def read_token(token_file: str | None = None) -> tuple[str, int]:
    """读本机 Marvis 网关的 (token, port)。

    token_file 给定时只读它（MIRAIHUB_MARVIS_LOCAL_TOKEN_FILE）；否则按
    default_token_files() 顺序扫。**读不到就抛 LocalWSError** —— 调用方
    （gateway）据此回 503，绝不落回「换号 / 冷却」那条路径。
    """
    if token_file:
        candidates = [Path(token_file)]
    else:
        candidates = [Path(path) for path in default_token_files()]
    errors: list[str] = []
    for path in candidates:
        try:
            text = _read_log(path)
        except OSError as exc:
            errors.append("%s: %s" % (path, exc.strerror or exc))
            continue
        found = parse_token_text(text)
        if found is not None:
            return found
        errors.append("%s: 没有 local_ws_url / AddGatewayTokenListener" % path)
    raise LocalWSError(
        "读不到 Marvis 本机网关 token（先确认 Marvis 客户端在跑）："
        + "；".join(errors[:4]))


def build_ws_url(base: str, token: str) -> str:
    """拼连接串。**必须是 host:port?token=，不是 host:port/?token=**。

    实测带路径斜杠的 /?token= 会被客户端网关按 404 拒掉。
    """
    raw = (base or "").strip() or "ws://127.0.0.1:%d" % DEFAULT_PORT
    # 抹掉对端可能配进来的路径尾部斜杠；有自定义 path 时保留 path 本身。
    raw = raw.rstrip("/")
    separator = "&" if "?" in raw else "?"
    return "%s%stoken=%s" % (raw, separator, quote(str(token), safe=""))


def port_of(url: str, fallback: int = DEFAULT_PORT) -> int:
    """从连接串里取端口（MIRAIHUB_MARVIS_LOCAL_WS_URL 可能带了自定义端口）。"""
    try:
        return int(urlsplit(str(url or "")).port or fallback)
    except (TypeError, ValueError):
        return int(fallback)


# ---------------------------------------------------------------------------
# 最小 RFC6455 客户端（同步）
# ---------------------------------------------------------------------------
class _WSConnection:
    """够用就好的 WS 客户端：文本帧、分片聚合、ping/pong、close。"""

    def __init__(self, url: str, *, timeout: float = _HANDSHAKE_TIMEOUT):
        self.url = url
        self.timeout = float(timeout)
        self.sock: socket.socket | None = None
        self._buf = bytearray()
        parts = urlsplit(url)
        #: **不含 token** 的连接串，只用于日志 / 异常文案。
        self.display_url = "%s://%s:%d" % (
            parts.scheme or "ws", parts.hostname or "127.0.0.1",
            parts.port or DEFAULT_PORT)

    # -- 握手 --------------------------------------------------------------
    def connect(self) -> None:
        parts = urlsplit(self.url)
        host = parts.hostname or "127.0.0.1"
        port = parts.port or (443 if parts.scheme == "wss" else DEFAULT_PORT)
        target = parts.path or "/"
        if parts.query:
            target += "?" + parts.query
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        try:
            sock = socket.create_connection((host, port), timeout=self.timeout)
        except OSError as exc:
            raise LocalWSError(
                "连不上本机 Marvis 客户端 %s:%d（%s）"
                % (host, port, exc.strerror or exc)) from None
        if parts.scheme == "wss":
            context = ssl.create_default_context()
            sock = context.wrap_socket(sock, server_hostname=host)
        self.sock = sock
        try:
            request = (
                "GET %s HTTP/1.1\r\n"
                "Host: %s:%d\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                "Sec-WebSocket-Key: %s\r\n"
                "Sec-WebSocket-Version: 13\r\n"
                "\r\n" % (target, host, port, key))
            sock.sendall(request.encode("ascii"))
            status, headers = self._read_handshake()
        except OSError as exc:
            self.close()
            raise LocalWSError(
                "与 %s:%d 的 WS 握手失败（%s）"
                % (host, port, getattr(exc, "strerror", None) or exc)) from None
        expected = base64.b64encode(
            hashlib.sha1((key + _WS_GUID).encode("ascii")).digest()).decode("ascii")
        if status != 101:
            self.close()
            raise LocalWSError("WS 握手被拒：HTTP %s" % status)
        if headers.get("sec-websocket-accept") != expected:
            self.close()
            raise LocalWSError("WS 握手校验失败：Sec-WebSocket-Accept 不匹配")

    def _read_handshake(self) -> tuple[int, dict[str, str]]:
        while b"\r\n\r\n" not in self._buf:
            if len(self._buf) > 65536:
                raise LocalWSError("WS 握手响应头过长")
            chunk = self.sock.recv(4096) if self.sock else b""
            if not chunk:
                raise LocalWSError("WS 握手时连接被关闭")
            self._buf += chunk
        head, _, rest = bytes(self._buf).partition(b"\r\n\r\n")
        self._buf = bytearray(rest)
        text = head.decode("iso-8859-1")
        lines = text.split("\r\n")
        status = 0
        if lines and lines[0].startswith("HTTP/"):
            try:
                status = int(lines[0].split()[1])
            except (IndexError, ValueError):
                status = 0
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if ":" not in line:
                continue
            name, value = line.split(":", 1)
            headers[name.strip().lower()] = value.strip()
        return status, headers

    # -- 收发 --------------------------------------------------------------
    def _fill(self, size: int) -> None:
        while len(self._buf) < size:
            if self.sock is None:
                raise LocalWSError("WS 连接已关闭")
            chunk = self.sock.recv(max(65536, size - len(self._buf)))
            if not chunk:
                raise LocalWSError("WS 连接被本机 Marvis 客户端关闭")
            self._buf += chunk

    def _read_exact(self, size: int) -> bytes:
        self._fill(size)
        data = bytes(self._buf[:size])
        del self._buf[:size]
        return data

    def send_frame(self, opcode: int, payload: bytes) -> None:
        if self.sock is None:
            raise LocalWSError("WS 连接已关闭")
        self.send_frame_to(self.sock, opcode, payload)

    def send_text(self, text: str) -> None:
        self.send_frame(0x1, text.encode("utf-8"))

    def read_frame(self, deadline: float | None = None) -> tuple[bool, int, bytes]:
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LocalWSError("等待本机 Marvis 响应超时")
            if self.sock is not None:
                self.sock.settimeout(max(0.1, remaining))
        head = self._read_exact(2)
        first, second = head[0], head[1]
        fin = bool(first & 0x80)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._read_exact(8))[0]
        mask = self._read_exact(4) if masked else b"\x00\x00\x00\x00"
        payload = bytearray(self._read_exact(length)) if length else bytearray()
        if masked:
            for index in range(length):
                payload[index] ^= mask[index & 3]
        return fin, opcode, bytes(payload)

    def recv_message(self, deadline: float | None = None) -> tuple[int, bytes]:
        chunks = bytearray()
        opcode = 0x1
        while True:
            fin, current, payload = self.read_frame(deadline)
            if current == 0x8:                      # close
                raise LocalWSError("本机 Marvis 客户端关闭了 WS 连接")
            if current == 0x9:                      # ping → pong
                self.send_frame(0xA, payload)
                continue
            if current == 0xA:                      # pong
                continue
            if current in (0x1, 0x2):
                chunks = bytearray(payload)
                opcode = current
            elif current == 0x0:                    # continuation
                chunks += payload
            if fin:
                return opcode, bytes(chunks)

    def recv_json(self, deadline: float | None = None):
        """读下一条能解析的 JSON 消息；非 JSON / 非文本帧直接跳过。"""
        while True:
            opcode, payload = self.recv_message(deadline)
            if opcode != 0x1:
                continue
            try:
                return json.loads(payload.decode("utf-8", "replace"))
            except ValueError:
                continue

    def close(self) -> None:
        sock, self.sock = self.sock, None
        if sock is None:
            return
        try:
            self.send_frame_to(sock, 0x8, b"")
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass

    @staticmethod
    def send_frame_to(sock: socket.socket, opcode: int, payload: bytes) -> None:
        length = len(payload)
        header = bytearray([0x80 | (opcode & 0x0F)])
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header += struct.pack("!H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack("!Q", length)
        mask = os.urandom(4)
        header += mask
        sock.sendall(bytes(header)
                     + bytes(byte ^ mask[index & 3]
                             for index, byte in enumerate(payload)))


# ---------------------------------------------------------------------------
# 消息扁平化：agent.run 只收**单条** message
# ---------------------------------------------------------------------------
def _content_text(content) -> str:
    """OpenAI 的 content 可能是字符串，也可能是多模态 part 列表。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                kind = str(block.get("type") or "")
                if kind in ("text", "input_text", "output_text") or "text" in block:
                    parts.append(str(block.get("text") or ""))
                elif kind in ("image_url", "input_image", "image"):
                    # 本机 WS 没有附件通道，图片只能留占位，否则用户会以为图发出去了。
                    parts.append("[image]")
        return "".join(parts)
    if isinstance(content, dict):
        return str(content.get("text") or "")
    return ""


def _message_line(message) -> tuple[str, str]:
    if not isinstance(message, dict):
        return "", ""
    role = str(message.get("role") or "user").strip().lower() or "user"
    text = _content_text(message.get("content"))
    tool_calls = message.get("tool_calls")
    if role == "assistant" and isinstance(tool_calls, list) and tool_calls:
        calls: list[str] = []
        for call in tool_calls:
            function = call.get("function") if isinstance(call, dict) else None
            if not isinstance(function, dict):
                continue
            name = str(function.get("name") or "tool")
            arguments = str(function.get("arguments") or "")
            calls.append("%s(%s)" % (name, arguments[:500]))
        if calls:
            prefix = text + "\n" if text else ""
            text = prefix + "[tool_calls] " + "; ".join(calls)
    return role, text.strip()


def flatten_messages(messages) -> str:
    """把 OpenAI 的 messages[] 渲染成 agent.run 能吃的**一段文本**。

    规则（越自然越好，模型看到的就是一段普通对话记录）：

      · system 正文**原样放最前**，多条之间用空行（两个换行）分隔；
      · 其余角色按 role: content 逐行拼接（user / assistant / tool 都保留，
        少一个角色模型就少一段上下文）；
      · **只有一条 user 消息且没有 system** 时直接用原文，不加任何前缀 ——
        这是最常见的路径，加了 user: 只是无谓的噪声。
    """
    if not isinstance(messages, list):
        return ""
    cleaned = [line for line in (_message_line(item) for item in messages)
               if line[0] and line[1]]
    if not cleaned:
        return ""
    if len(cleaned) == 1 and cleaned[0][0] == "user":
        return cleaned[0][1]
    system_parts = [text for role, text in cleaned if role == "system"]
    turns = ["%s: %s" % (role, text) for role, text in cleaned if role != "system"]
    head = "\n\n".join(system_parts)
    body = "\n".join(turns)
    if head and body:
        return head + "\n\n" + body
    return head or body


# ---------------------------------------------------------------------------
# 事件解析：对帧的嵌套形状**宽容**（payload / data 两处都认）
# ---------------------------------------------------------------------------
#: ag_ui_event 里我们需要关心的事件名。
_AG_UI_TYPES = frozenset({
    "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_DELTA", "REASONING_CONTENT",
    "REASONING_MESSAGE_CONTENT", "TEXT_MESSAGE_START", "TEXT_MESSAGE_END",
    "RUN_STARTED", "RUN_FINISHED", "CUSTOM", "TOOL_CALL_START", "TOOL_CALL_END",
})


def _walk(node, depth: int = 0):
    """深度优先遍历嵌套 dict/list（限深，避免异常帧把栈打爆）。"""
    if depth > 8:
        return
    yield node
    if isinstance(node, dict):
        for value in node.values():
            yield from _walk(value, depth + 1)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value, depth + 1)


def _find_by_key(node, key: str):
    """在帧里找第一个带 key 的 dict（token_usage / value 这类）。"""
    for item in _walk(node):
        if isinstance(item, dict) and isinstance(item.get(key), (dict, list)):
            return item[key]
    return None


def _ag_ui_event(frame) -> dict | None:
    """找出帧里那个 ag_ui 事件体（{"type": "...", "delta": ...}）。"""
    for node in _walk(frame):
        if not isinstance(node, dict):
            continue
        for key in ("type", "event"):
            value = node.get(key)
            if isinstance(value, str) and value in _AG_UI_TYPES:
                return node
    return None


def _frame_name(frame) -> str:
    if not isinstance(frame, dict):
        return ""
    for key in ("event", "type"):
        value = frame.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _expect_ack(conn: "_WSConnection", request_id: str, deadline: float,
                what: str) -> dict:
    """读到指定 requestId 的 ACK；ok=false 直接抛（带原因，不带 token）。"""
    while True:
        frame = conn.recv_json(deadline)
        if not isinstance(frame, dict):
            continue
        if str(frame.get("type") or "") != "ack":
            continue
        if str(frame.get("requestId") or "") != request_id:
            continue
        data = frame.get("data")
        data = data if isinstance(data, dict) else {}
        if data.get("ok") is False:
            raise LocalWSError("%s失败：%s" % (
                what, json.dumps(data, ensure_ascii=False)[:300]))
        return data


def _wait_connected(conn: "_WSConnection", deadline: float) -> None:
    """等 gateway.connected。实测 ~0.04s，但冷启动可能更久。"""
    while True:
        frame = conn.recv_json(deadline)
        if _frame_name(frame) == "gateway.connected":
            return
        if _frame_name(frame) in ("gateway.error", "agent.error"):
            raise LocalWSError("本机 Marvis 网关报错：%s"
                               % json.dumps(frame, ensure_ascii=False)[:300])


def _create_conversation(conn: "_WSConnection", deadline: float, title: str) -> str:
    conn.send_text(json.dumps({
        "event": "agent.action",
        "requestId": "1",
        "payload": {"action": "conversations.create",
                    "title": title or DEFAULT_TITLE},
    }, ensure_ascii=False))
    data = _expect_ack(conn, "1", deadline, "创建 Marvis 会话")
    inner = data.get("data")
    inner = inner if isinstance(inner, dict) else {}
    conversation_id = str(
        inner.get("conversation_id") or inner.get("conversationId") or "").strip()
    if not conversation_id:
        raise LocalWSError("创建会话的 ACK 里没有 conversation_id：%s"
                           % json.dumps(data, ensure_ascii=False)[:300])
    return conversation_id


def _handle_ag_ui(event: dict, state: dict, on_delta) -> bool:
    """吃一条 ag_ui 事件；返回 True 表示 RUN_FINISHED（流结束）。"""
    kind = str(event.get("type") or event.get("event") or "")
    if kind in ("TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_DELTA"):
        delta = event.get("delta")
        if isinstance(delta, str) and delta:
            state["content"].append(delta)
            if callable(on_delta):
                on_delta(delta)
    elif kind in ("REASONING_CONTENT", "REASONING_MESSAGE_CONTENT"):
        delta = event.get("delta")
        if isinstance(delta, str) and delta:
            state["reasoning"].append(delta)
    elif kind == "TEXT_MESSAGE_END":
        usage = _find_by_key(event.get("rawEvent") or event, "token_usage")
        if isinstance(usage, dict) and usage:
            state["usage"] = usage
    elif kind == "CUSTOM" and str(event.get("name") or "") == "message_complete":
        value = event.get("value")
        value = value if isinstance(value, dict) else {}
        content = value.get("content")
        if isinstance(content, str) and content:
            state["content_final"] = content
    elif kind == "RUN_FINISHED":
        usage = _find_by_key(event, "usage") or _find_by_key(event, "token_usage")
        if isinstance(usage, dict) and usage and not state["usage"]:
            state["usage"] = usage
        return True
    return False


def _run_agent_stream(conn: "_WSConnection", deadline: float,
                      conversation_id: str, message: str,
                      state: dict, on_delta) -> None:
    conn.send_text(json.dumps({
        "event": "agent.run",
        "requestId": "2",
        "payload": {
            "conversation_id": conversation_id,
            "message": message,
            "response_id": str(uuid.uuid4()),
            "attachments": [],
            "mcp_manage_tools_enabled": True,
        },
    }, ensure_ascii=False))
    while True:
        frame = conn.recv_json(deadline)
        if not isinstance(frame, dict):
            continue
        name = _frame_name(frame)
        if name == "ack":
            continue
        if name in ("agent.error", "gateway.error"):
            raise LocalWSError("本机 Marvis 运行失败：%s"
                               % json.dumps(frame, ensure_ascii=False)[:300])
        event = _ag_ui_event(frame)
        if event is None:
            continue
        if _handle_ag_ui(event, state, on_delta):
            return


def run_agent(message: str, *, token: str, port: int = DEFAULT_PORT,
              title: str = DEFAULT_TITLE, timeout: float = 300.0,
              on_delta=None, url: str | None = None) -> dict:
    """建会话 → agent.run → 收流，返回
    {content, reasoning, usage, conversation_id}。

    · on_delta(str)：每收到一段 TEXT_MESSAGE_CONTENT 就回调一次（流式用）；
    · timeout：**整段**上限（秒），覆盖建连 / 建会话 / 正文生成；
    · 任何失败都抛 LocalWSError（含 token 读不到、连接被拒、对端报错）。
    """
    if not isinstance(message, str) or not message.strip():
        raise LocalWSError("发送给 Marvis 的消息为空")
    base = url or ("ws://127.0.0.1:%d" % port_of("", port))
    deadline = time.monotonic() + max(1.0, float(timeout))
    conn = _WSConnection(build_ws_url(base, token), timeout=_HANDSHAKE_TIMEOUT)
    state = {"content": [], "reasoning": [], "content_final": "", "usage": {}}
    conversation_id = ""
    try:
        conn.connect()
        _wait_connected(conn, deadline)
        conversation_id = _create_conversation(conn, deadline, title)
        _run_agent_stream(conn, deadline, conversation_id, message, state, on_delta)
    except socket.timeout:
        raise LocalWSError("等待本机 Marvis 响应超时（%.0fs）" % timeout) from None
    except OSError as exc:
        raise LocalWSError("本机 Marvis WS 通信失败（%s）"
                           % (getattr(exc, "strerror", None) or exc)) from None
    finally:
        conn.close()
    content = state["content_final"] or "".join(state["content"])
    return {
        "content": content,
        "reasoning": "".join(state["reasoning"]),
        "usage": state["usage"] if isinstance(state["usage"], dict) else {},
        "conversation_id": conversation_id,
    }


# ---------------------------------------------------------------------------
# 命令行：一次真实调用（抓包复现时用它造请求）
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    """python -m admin.platform.marvis.local_ws '你好'

    打印正文 / 用量 / 会话 id，**不打印 token**（token 只在内存里传给
    run_agent）。这是抓包复现时造真实请求的最短路径。
    """
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("用法：python -m admin.platform.marvis.local_ws <消息>")
        return 2
    message = " ".join(args)
    token_file = os.environ.get("MIRAIHUB_MARVIS_LOCAL_TOKEN_FILE") or None
    ws_url = os.environ.get("MIRAIHUB_MARVIS_LOCAL_WS_URL") or None
    try:
        timeout = float(os.environ.get("MIRAIHUB_MARVIS_LOCAL_WS_TIMEOUT") or 300)
    except ValueError:
        timeout = 300.0
    try:
        token, port = read_token(token_file)
        result = run_agent(message, token=token, port=port, url=ws_url,
                           timeout=timeout)
    except LocalWSError as exc:
        print("本机 Marvis 调用失败：%s" % exc, file=sys.stderr)
        return 1
    print(json.dumps({
        "content": result["content"],
        "reasoning": result["reasoning"],
        "usage": result["usage"],
        "conversation_id": result["conversation_id"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover - 手工诊断入口
    raise SystemExit(main())
