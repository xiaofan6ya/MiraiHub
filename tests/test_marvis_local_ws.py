"""Marvis 本机 WS **诊断工具**的自身能力回归。

⚠ 方向：本机 WS **不是生产传输**（gateway 依旧直连上游 HTTP）。这些用例只锁
诊断工具本身：token 解析、帧构造、delta 累加、用量提取。

⚠ 不碰真的 10123（测试环境没有 Marvis 客户端）：token 用临时日志，
帧收发用本机起的假 WS server。
"""
import base64
import hashlib
import json
import os
import socket
import struct
import sys
import tempfile
import threading
import unittest
from pathlib import Path

# 必须早于 admin.* 导入：admin/db.py 在模块级按 settings 建引擎。
os.environ.setdefault(
    "ADMIN_DATABASE_URL",
    "sqlite:///" + str(Path(os.environ.get("TEMP", "/tmp")) / "marvis_local_ws_test.db"),
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from admin.platform.marvis import local_ws  # noqa: E402

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

TOKEN_A = "11111111-2222-3333-4444-555555555555"
TOKEN_B = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

HOST_STD_LOG = """2026-11-17 22:20:01.001 [info] MarvisHostStd starting
2026-11-17 22:20:01.442 [info] config initialized, local_ws_url=ws://127.0.0.1:10123/?token=%s&handler=openclaw
2026-11-17 22:20:01.443 [info] ready
""" % TOKEN_A

MARVIS_LOG = """2026-11-17 22:20:01.441 [info] base.init ok
2026-11-17 22:20:01.442 [info] AddGatewayTokenListener {"token":"%s","port":10123}
""" % TOKEN_B


# ---------------------------------------------------------------------------
# ① token：两种日志格式都要认
# ---------------------------------------------------------------------------
class TokenTests(unittest.TestCase):
    def test_parse_host_std_log_url(self):
        self.assertEqual(local_ws.parse_token_text(HOST_STD_LOG),
                         (TOKEN_A, 10123))

    def test_parse_marvis_log_json(self):
        self.assertEqual(local_ws.parse_token_text(MARVIS_LOG),
                         (TOKEN_B, 10123))

    def test_garbled_lines_are_skipped(self):
        text = "noise\nlocal_ws_url=ws://127.0.0.1:10123/?token=&\n" + MARVIS_LOG
        self.assertEqual(local_ws.parse_token_text(text), (TOKEN_B, 10123))

    def test_custom_port_is_kept(self):
        text = "x local_ws_url=ws://127.0.0.1:23456/?token=%s&handler=openclaw" % TOKEN_A
        self.assertEqual(local_ws.parse_token_text(text), (TOKEN_A, 23456))

    def test_later_line_wins(self):
        """同一进程重建过网关时，日志里最后一条才是当前有效的。"""
        text = HOST_STD_LOG + "\n" + MARVIS_LOG
        self.assertEqual(local_ws.parse_token_text(text), (TOKEN_B, 10123))

    def test_no_token_returns_none(self):
        self.assertIsNone(local_ws.parse_token_text("nothing here"))
        self.assertIsNone(local_ws.parse_token_text(""))

    def test_read_token_from_explicit_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "MarvisHostStd.log"
            path.write_text(HOST_STD_LOG, encoding="utf-8")
            self.assertEqual(local_ws.read_token(str(path)), (TOKEN_A, 10123))

    def test_read_token_missing_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nope.log"
            with self.assertRaises(local_ws.LocalWSError):
                local_ws.read_token(str(path))

    def test_read_token_file_without_token_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.log"
            path.write_text("just noise\n", encoding="utf-8")
            with self.assertRaises(local_ws.LocalWSError):
                local_ws.read_token(str(path))

    def test_build_ws_url_has_no_path_slash(self):
        """实测 /?token= 会被客户端网关 404；必须是 host:port?token=。"""
        url = local_ws.build_ws_url("ws://127.0.0.1:10123", "abc")
        self.assertEqual(url, "ws://127.0.0.1:10123?token=abc")
        self.assertNotIn("/?token=", url)
        self.assertEqual(url.count("token="), 1)

    def test_build_ws_url_keeps_existing_query(self):
        url = local_ws.build_ws_url("ws://127.0.0.1:10123/?a=1", "abc")
        self.assertTrue(url.endswith("&token=abc"), url)

    def test_port_of(self):
        self.assertEqual(local_ws.port_of("ws://127.0.0.1:18888"), 18888)
        self.assertEqual(local_ws.port_of("ws://127.0.0.1", 10123), 10123)


# ---------------------------------------------------------------------------
# ② 消息扁平化：agent.run 只收一条 message
# ---------------------------------------------------------------------------
class FlattenTests(unittest.TestCase):
    def test_single_user_is_verbatim(self):
        """最常见路径不能加 role: 前缀（加了就是无谓噪声）。"""
        self.assertEqual(
            local_ws.flatten_messages([{"role": "user", "content": "你好"}]),
            "你好")

    def test_system_goes_first_with_blank_line(self):
        text = local_ws.flatten_messages([
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "你好"},
        ])
        self.assertEqual(text, "你是助手\n\nuser: 你好")

    def test_multiple_system_blocks(self):
        text = local_ws.flatten_messages([
            {"role": "system", "content": "A"},
            {"role": "system", "content": "B"},
            {"role": "user", "content": "Q"},
        ])
        self.assertEqual(text, "A\n\nB\n\nuser: Q")

    def test_history_keeps_every_role(self):
        text = local_ws.flatten_messages([
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"},
        ])
        self.assertEqual(text, "user: a\nassistant: b\nuser: c")

    def test_tool_role_and_result_kept(self):
        text = local_ws.flatten_messages([
            {"role": "user", "content": "几点了"},
            {"role": "assistant", "content": "查一下"},
            {"role": "tool", "content": "12:00"},
        ])
        self.assertIn("tool: 12:00", text)
        self.assertIn("assistant: 查一下", text)

    def test_multimodal_parts(self):
        text = local_ws.flatten_messages([{
            "role": "user",
            "content": [{"type": "text", "text": "看图"},
                        {"type": "image_url", "image_url": {"url": "x"}}],
        }])
        self.assertEqual(text, "看图[image]")

    def test_assistant_tool_calls_are_visible(self):
        text = local_ws.flatten_messages([
            {"role": "user", "content": "几点了"},
            {"role": "assistant", "content": "",
             "tool_calls": [{"function": {"name": "get_time", "arguments": "{}"}}]},
        ])
        self.assertIn("[tool_calls] get_time({})", text)

    def test_empty_messages(self):
        self.assertEqual(local_ws.flatten_messages([]), "")
        self.assertEqual(local_ws.flatten_messages(None), "")
        self.assertEqual(
            local_ws.flatten_messages([{"role": "user", "content": "   "}]), "")


# ---------------------------------------------------------------------------
# ③ RFC6455 帧构造：客户端帧必须带掩码（RFC 硬要求）
# ---------------------------------------------------------------------------
class _Capture:
    """只收 sendall 的假 socket。"""

    def __init__(self):
        self.data = b""

    def sendall(self, payload):
        self.data += bytes(payload)


def _unmask(frame: bytes) -> bytes:
    length = frame[1] & 0x7F
    offset = 2
    if length == 126:
        length = struct.unpack("!H", frame[2:4])[0]
        offset = 4
    elif length == 127:
        length = struct.unpack("!Q", frame[2:10])[0]
        offset = 10
    mask = frame[offset:offset + 4]
    payload = frame[offset + 4:offset + 4 + length]
    return bytes(byte ^ mask[index & 3] for index, byte in enumerate(payload))


class FrameTests(unittest.TestCase):
    def test_short_text_frame_is_masked(self):
        capture = _Capture()
        local_ws._WSConnection.send_frame_to(capture, 0x1, b"hello")
        self.assertEqual(capture.data[0], 0x81)
        self.assertEqual(capture.data[1], 0x85, "客户端帧必须置掩码位")
        self.assertEqual(len(capture.data), 2 + 4 + 5)
        self.assertEqual(_unmask(capture.data), b"hello")

    def test_medium_and_long_lengths(self):
        for size, marker in ((300, 126), (70000, 127)):
            capture = _Capture()
            local_ws._WSConnection.send_frame_to(capture, 0x1, b"x" * size)
            self.assertEqual(capture.data[1] & 0x7F, marker, size)
            self.assertEqual(_unmask(capture.data), b"x" * size)

    def test_read_frame_accepts_unmasked_server_frame(self):
        conn = local_ws._WSConnection("ws://127.0.0.1:1")
        conn._buf = bytearray(b"\x81\x05hello")
        fin, opcode, payload = conn.read_frame()
        self.assertTrue(fin)
        self.assertEqual(opcode, 0x1)
        self.assertEqual(payload, b"hello")

    def test_read_frame_decodes_masked_frame(self):
        payload = b"masked!"
        mask = b"\x01\x02\x03\x04"
        masked = bytes(byte ^ mask[index & 3] for index, byte in enumerate(payload))
        conn = local_ws._WSConnection("ws://127.0.0.1:1")
        conn._buf = bytearray(bytes([0x81, 0x80 | len(payload)]) + mask + masked)
        _fin, opcode, decoded = conn.read_frame()
        self.assertEqual(opcode, 0x1)
        self.assertEqual(decoded, payload)

    def test_ag_ui_delta_and_usage_are_parsed(self):
        state = {"content": [], "reasoning": [], "content_final": "", "usage": {}}
        seen = []
        local_ws._handle_ag_ui(
            {"type": "TEXT_MESSAGE_CONTENT", "delta": "你"}, state, seen.append)
        local_ws._handle_ag_ui(
            {"type": "REASONING_CONTENT", "delta": "想"}, state, seen.append)
        local_ws._handle_ag_ui(
            {"type": "TEXT_MESSAGE_END",
             "rawEvent": {"token_usage": {"input_tokens": 7, "output_tokens": 2}}},
            state, seen.append)
        self.assertEqual(seen, ["你"])
        self.assertEqual("".join(state["content"]), "你")
        self.assertEqual("".join(state["reasoning"]), "想")
        self.assertEqual(state["usage"]["input_tokens"], 7)


# ---------------------------------------------------------------------------
# ④ 假 WS server：验证握手 / 帧 / delta 累加（不碰 10123）
# ---------------------------------------------------------------------------
class _FakeWSServer:
    """最小 WS 服务端：握手 + 按帧回放 ag_ui 事件流。"""

    def __init__(self, *, deltas=("你", "好"), final="你好", usage=None):
        self.deltas = deltas
        self.final = final
        self.usage = usage or {"input_tokens": 11, "output_tokens": 3,
                               "total_tokens": 14, "cached_tokens": 5,
                               "thinking_tokens": 1}
        self.received = []
        self.error = None
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.port = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc_info):
        try:
            self._sock.close()
        except OSError:
            pass
        self._thread.join(timeout=5)

    # -- 帧读写 ------------------------------------------------------------
    @staticmethod
    def _recv_exact(conn, size):
        buf = b""
        while len(buf) < size:
            chunk = conn.recv(size - len(buf))
            if not chunk:
                raise OSError("closed")
            buf += chunk
        return buf

    def _recv_frame(self, conn):
        first, second = self._recv_exact(conn, 2)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._recv_exact(conn, 2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._recv_exact(conn, 8))[0]
        mask = self._recv_exact(conn, 4) if masked else b"\x00\x00\x00\x00"
        payload = self._recv_exact(conn, length) if length else b""
        if masked:
            payload = bytes(byte ^ mask[index & 3]
                            for index, byte in enumerate(payload))
        return opcode, payload

    @staticmethod
    def _send_frame(conn, payload, opcode=0x1):
        length = len(payload)
        header = bytearray([0x80 | opcode])
        if length < 126:
            header.append(length)
        elif length < 65536:
            header.append(126)
            header += struct.pack("!H", length)
        else:
            header.append(127)
            header += struct.pack("!Q", length)
        conn.sendall(bytes(header) + payload)

    def _send_json(self, conn, obj):
        self._send_frame(conn, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _handshake(self, conn):
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += conn.recv(4096)
        head = buf.split(b"\r\n\r\n", 1)[0].decode("iso-8859-1")
        key = ""
        for line in head.split("\r\n")[1:]:
            name, _, value = line.partition(":")
            if name.strip().lower() == "sec-websocket-key":
                key = value.strip()
        accept = base64.b64encode(
            hashlib.sha1((key + GUID).encode("ascii")).digest()).decode("ascii")
        conn.sendall((
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Accept: %s\r\n\r\n" % accept).encode("ascii"))

    def _serve(self):
        try:
            conn, _ = self._sock.accept()
        except OSError:
            return
        try:
            conn.settimeout(10)
            self._handshake(conn)
            self._send_json(conn, {"event": "gateway.connected",
                                   "data": {"agentConnectionStatus": "Ready"}})
            while True:
                opcode, payload = self._recv_frame(conn)
                if opcode == 0x8:
                    return
                frame = json.loads(payload.decode("utf-8"))
                self.received.append(frame)
                if frame.get("event") == "agent.action":
                    self._send_json(conn, {
                        "type": "ack", "requestId": frame.get("requestId"),
                        "data": {"ok": True, "data": {"conversation_id": "conv_test"}}})
                elif frame.get("event") == "agent.run":
                    for delta in self.deltas:
                        self._send_json(conn, {"event": "ag_ui_event",
                                               "payload": {"type": "TEXT_MESSAGE_CONTENT",
                                                           "delta": delta}})
                    self._send_json(conn, {"event": "ag_ui_event", "payload": {
                        "type": "TEXT_MESSAGE_END",
                        "rawEvent": {"token_usage": self.usage}}})
                    self._send_json(conn, {"event": "ag_ui_event", "payload": {
                        "type": "CUSTOM", "name": "message_complete",
                        "value": {"content": self.final}}})
                    self._send_json(conn, {"event": "ag_ui_event",
                                           "payload": {"type": "RUN_FINISHED"}})
                    return
        except (OSError, ValueError) as exc:      # pragma: no cover - 排障用
            self.error = exc
        finally:
            try:
                conn.close()
            except OSError:
                pass


class FakeServerTests(unittest.TestCase):
    def test_run_agent_accumulates_deltas_and_usage(self):
        with _FakeWSServer() as server:
            seen = []
            result = local_ws.run_agent(
                "你好", token="tok", port=server.port, timeout=10,
                on_delta=seen.append)
        self.assertIsNone(server.error)
        self.assertEqual(seen, ["你", "好"])
        self.assertEqual(result["content"], "你好", "优先用 message_complete 终稿")
        self.assertEqual(result["conversation_id"], "conv_test")
        self.assertEqual(result["usage"]["input_tokens"], 11)
        self.assertEqual(result["usage"]["cached_tokens"], 5)
        self.assertEqual(result["usage"]["thinking_tokens"], 1)

    def test_client_frames_are_well_formed(self):
        """真机协议：requestId 递增，agent.run 必须带 conversation_id / response_id。"""
        with _FakeWSServer() as server:
            local_ws.run_agent("hi", token="tok", port=server.port, timeout=10)
        action, run = server.received[0], server.received[1]
        self.assertEqual(action["event"], "agent.action")
        self.assertEqual(action["payload"]["action"], "conversations.create")
        self.assertEqual(action["requestId"], "1")
        self.assertEqual(run["event"], "agent.run")
        self.assertEqual(run["requestId"], "2")
        payload = run["payload"]
        self.assertEqual(payload["conversation_id"], "conv_test")
        self.assertEqual(payload["message"], "hi")
        self.assertEqual(payload["attachments"], [])
        self.assertEqual(len(payload["response_id"]), 36, "response_id 必须是 uuid4")

    def test_connect_failure_is_local_ws_error(self):
        """连不上（端口没监听）必须抛 LocalWSError，不能是裸 OSError。"""
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        with self.assertRaises(local_ws.LocalWSError):
            local_ws.run_agent("hi", token="tok", port=port, timeout=3)

    def test_empty_message_rejected(self):
        with self.assertRaises(local_ws.LocalWSError):
            local_ws.run_agent("   ", token="tok", port=1, timeout=1)


# ---------------------------------------------------------------------------
# ⑤ 边界：生产网关**不**引用本模块（方向变更的回归锁）
# ---------------------------------------------------------------------------
class BoundaryTests(unittest.TestCase):
    def test_gateway_does_not_use_local_ws(self):
        gateway_path = Path(local_ws.__file__).with_name("gateway.py")
        source = gateway_path.read_text(encoding="utf-8")
        self.assertNotIn("local_ws", source, "gateway 不得接本机 WS 传输")
        self.assertNotIn("MIRAIHUB_MARVIS_VIA_LOCAL_WS", source,
                         "本机 WS 不能有生产开关")


if __name__ == "__main__":
    unittest.main()
