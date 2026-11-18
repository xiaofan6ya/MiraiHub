# -*- coding: utf-8 -*-
"""Marvis 流式转发的**出字时机**与 usage 抓取回归。

## 事故背景（2026-11-18 定位）

`gateway._iter_upstream` 原来用 `resp.read(8192)` 读上游 SSE。`HTTPResponse.
read(n)` 的语义是**读满 n 字节或 EOF 才返回**（底层 `BufferedReader.read`），
而 SSE 一帧只有几十到几百字节 —— 8KB 的门槛永远攒不满，于是**整条流要等
连接关闭才一次性吐给客户端**。用户看到的现象是「转圈几十秒，全文突然出现」，
而不是逐字出；短回答（< 8KB，占绝大多数）100% 命中这条路径。

同一处还有一个记账缺陷：原实现把累积 buffer 截成 `[-8192:]`，跨界的 usage
帧会被切成两半，那一帧就此丢失 —— 长回答的 token 账悄悄少记一次。

## 本测试守什么

1. `_iter_upstream` 必须逐块出字（首块时间 << 全流时长），不能攒；
2. 没有 `read1` 的响应对象（测试桩 / 非 http.client）要能退回 `read`；
3. `_absorb_usage` 必须能捡到**跨块切半**的 usage 帧，且与旧逻辑结果一致；
4. 端到端走一遍 `chat_completions`：首块延迟、转发字节一致、记账拿到 usage。
"""
import asyncio
import http.server
import io
import json
import socketserver
import sys
import threading
import time
import pathlib
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from admin.platform.marvis import gateway as gw  # noqa: E402

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("PASS  " + name)
    else:
        fail += 1
        print("FAIL  " + name + ("  " + str(extra) if extra else ""))


# ---------------------------------------------------------------------------
# 慢速 SSE 上游：40 帧 × 间隔 20ms，全流约 800ms，总字节数远小于 8KB
# ---------------------------------------------------------------------------
FRAMES = 40
FRAME = b'event: delta\ndata: {"choices":[{"delta":{"content":"\xe5\xad\x97"}}]}\n\n'
GAP = 0.02
USAGE_LINE = (b'data: {"choices":[],"usage":{"prompt_tokens":1234,'
              b'"completion_tokens":5678,"total_tokens":6912}}\n\n')


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        try:
            for _ in range(FRAMES):
                self.wfile.write(FRAME)
                self.wfile.flush()
                time.sleep(GAP)
            self.wfile.write(USAGE_LINE)
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


class _Server:
    def __enter__(self):
        self.srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Handler)
        self.srv.daemon_threads = True
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *a):
        self.srv.shutdown()
        self.srv.server_close()

    def open(self):
        return urllib.request.urlopen(
            "http://127.0.0.1:%d/v1/chat/completions" % self.port, timeout=10)


async def _drain(resp):
    first = None
    chunks = []
    t0 = time.perf_counter()
    async for chunk in gw._iter_upstream(resp):
        if first is None:
            first = time.perf_counter() - t0
        chunks.append(chunk)
    return first, chunks, time.perf_counter() - t0


print("=== 1) _iter_upstream 必须逐块出字 ===")
with _Server() as srv:
    resp = srv.open()
    try:
        first, chunks, total = asyncio.run(_drain(resp))
    finally:
        resp.close()
print("  首块 %.0f ms / %d 块 / 全流 %.0f ms / 共 %d 字节"
      % (first * 1000, len(chunks), total * 1000, sum(len(c) for c in chunks)))
check("首块远早于全流结束（没攒满 8KB 才吐）", first < 0.25,
      "%.3fs —— read() 语义又回来了？" % first)
check("块数接近帧数（逐帧转发）", len(chunks) >= FRAMES // 2, len(chunks))
check("字节总数不变（没有丢/重复）",
      b"".join(chunks).count(b"data:") == FRAMES + 2,
      b"".join(chunks).count(b"data:"))

print()
print("=== 2) 没有 read1 的响应对象要退回 read ===")


class _ReadOnly:
    def __init__(self, data):
        self.buf = data

    def read(self, size=-1):
        out, self.buf = self.buf[:size], self.buf[size:]
        return out

    def close(self):
        pass


async def _collect(resp):
    return [c async for c in gw._iter_upstream(resp)]


got = asyncio.run(_collect(_ReadOnly(b"abc" * 1000)))
check("fallback 走 read 也能读完", len(got) == 1 and got[0] == b"abc" * 1000,
      len(got))

print()
print("=== 3) _scan_stream：跨块切半的 usage / finish / 流内错误 ===")
stream = b"".join([FRAME] * 5) + USAGE_LINE
for size in (7, 64, 8192):
    state: dict = {}
    pending = b""
    for i in range(0, len(stream), size):
        pending = gw._scan_stream(pending, stream[i:i + size], state)
    check("按 %d 字节切块也能捡到 usage" % size,
          (state.get("usage") or {}).get("total_tokens") == 6912,
          state.get("usage"))

# 流内错误帧：HTTP 200、没有 choices、只有 error —— 必须能识别（否则静默成功）
state = {}
gw._scan_stream(b"", b'data: {"error":{"code":3000101,"message":"bad"}}\n\n', state)
check("流内错误帧被识别", "3000101" in str(state.get("error") or ""),
      state.get("error"))

# 带 choices 的帧即使带 error 字段也不算错误（网关扩展，不误判）
state = {}
gw._scan_stream(b"", b'data: {"choices":[{"delta":{"content":"x"}}],'
                    b'"error":"warn"}\n\n', state)
check("有 choices 的帧不当错误", not state.get("error"), state.get("error"))

state = {}
gw._scan_stream(b"", b"data: [DONE]\n\n", state)
check("[DONE] 被识别", bool(state.get("done")), state)

state = {}
gw._scan_stream(b"", b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
                state)
check("finish_reason 被识别", bool(state.get("finish")), state)

# 跨块切半的**流内错误帧**同样不能漏（按 chunk 独立解析就会漏）
err_line = b'data: {"error":{"code":3000101,"msg":"cut"}}\n\n'
state = {}
pending = b""
for i in range(0, len(err_line), 5):
    pending = gw._scan_stream(pending, err_line[i:i + 5], state)
check("切半的错误帧也能识别", "3000101" in str(state.get("error") or ""),
      state.get("error"))

# 与旧实现（每块重扫全尾）逐字节对照
def _old_usage(chunks):
    usage = {}
    buffer = b""
    for chunk in chunks:
        buffer += chunk
        if b'"usage"' in buffer:
            for line in buffer.split(b"\n"):
                line = line.strip()
                if not line.startswith(b"data:"):
                    continue
                payload = line[5:].strip()
                if payload in (b"", b"[DONE]"):
                    continue
                try:
                    obj = json.loads(payload)
                except Exception:
                    continue
                if isinstance(obj, dict) and isinstance(obj.get("usage"), dict):
                    usage = obj["usage"]
            buffer = buffer[-8192:]
    return usage


def _new_usage(chunks):
    state: dict = {}
    pending = b""
    for chunk in chunks:
        pending = gw._scan_stream(pending, chunk, state)
    return state.get("usage") or {}


chunks_8k = [stream[i:i + 8192] for i in range(0, len(stream), 8192)]
check("8KB 切块时与旧实现结果一致",
      _old_usage(chunks_8k) == _new_usage(chunks_8k) == {
          "prompt_tokens": 1234, "completion_tokens": 5678,
          "total_tokens": 6912}, _new_usage(chunks_8k))

print()
print("=== 4) 端到端：chat_completions 流式路径 ===")


class _Client:
    host = "127.0.0.1"


class _Req:
    headers = {}
    query_params = {}
    client = _Client()

    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


billed = {}


async def _fake_bill(*args, **kwargs):
    billed["usage"] = args[3] if len(args) > 3 else kwargs.get("usage")
    billed["stats"] = kwargs.get("stats")


class _Account:
    id = 1
    uid = "u1"


with _Server() as srv:
    resp = srv.open()
    orig = (gw._validate_model, gw._acquire, gw._bill_async,
            gw.shared.note_success, gw.shared.release)
    gw._validate_model = lambda body: "main-auto"
    gw._acquire = lambda request, body, realm, *, stream: (
        object(), resp, _Account(), time.perf_counter())
    gw._bill_async = _fake_bill
    gw.shared.note_success = lambda account: None
    gw.shared.release = lambda account: None
    try:
        async def run():
            r = await gw.chat_completions(
                _Req({"model": "deepseek-v4.1-flash", "stream": True,
                      "messages": [{"role": "user", "content": "hi"}]}))
            assert isinstance(r, gw.StreamingResponse), type(r)
            t0 = time.perf_counter()
            chunks = []
            first = None
            async for piece in r.body_iterator:
                if first is None:
                    first = time.perf_counter() - t0
                chunks.append(piece)
            return first, chunks, time.perf_counter() - t0

        first, chunks, total = asyncio.run(run())
    finally:
        (gw._validate_model, gw._acquire, gw._bill_async,
         gw.shared.note_success, gw.shared.release) = orig
        resp.close()

print("  端到端首块 %.0f ms / 全流 %.0f ms / %d 块"
      % (first * 1000, total * 1000, len(chunks)))
check("端到端首块早于全流结束", first < 0.25, "%.3fs" % first)
check("端到端转发字节完整",
      b"".join(chunks).count(b"data:") == FRAMES + 2,
      b"".join(chunks).count(b"data:"))
check("记账拿到了 usage.total_tokens=6912",
      isinstance(billed.get("usage"), dict)
      and billed["usage"].get("total_tokens") == 6912, billed.get("usage"))
check("记账拿到了 ttfb 统计（下限 1，不会被兜底成整条流时长）",
      isinstance(billed.get("stats"), dict)
      and billed["stats"].get("ttfb_ms", 0) >= 1
      and billed["stats"].get("total_ms", 0) > 100,
      billed.get("stats"))

print()
print("=== 5) _prepare_body 的纯 CPU 改写不能占住事件循环 ===")


def _measure_drift(coro_factory, budget):
    async def scenario():
        task = asyncio.create_task(coro_factory())
        worst = 0.0
        deadline = time.perf_counter() + budget
        while time.perf_counter() < deadline:
            t0 = time.perf_counter()
            await asyncio.sleep(0.02)
            worst = max(worst, time.perf_counter() - t0 - 0.02)
        await task
        return worst

    return asyncio.run(scenario())


SLOW = 1.0
orig2 = (gw._validate_model, gw._prepare_body, gw._acquire, gw._bill_async)
gw._validate_model = lambda body: "main-auto"
gw._prepare_body = lambda body, slot: (time.sleep(SLOW), dict(body))[1]
gw._acquire = lambda request, body, realm, *, stream: (
    object(), {"choices": []}, _Account(), time.perf_counter())
gw._bill_async = _fake_bill
try:
    drift = _measure_drift(
        lambda: gw.chat_completions(
            _Req({"model": "deepseek-v4.1-flash",
                  "messages": [{"role": "user", "content": "hi"}]})), SLOW)
finally:
    (gw._validate_model, gw._prepare_body, gw._acquire, gw._bill_async) = orig2

print("  事件循环最大调度抖动 %.0f ms（改写耗时 %.0f ms）"
      % (drift * 1000, SLOW * 1000))
check("_prepare_body 已离开事件循环", drift < 0.3,
      "抖动 %.2fs —— 改写又回到主线程了" % drift)

print()
print("%d/%d passed" % (ok, ok + fail))
sys.exit(1 if fail else 0)
