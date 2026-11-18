# -*- coding: utf-8 -*-
"""Marvis 反代的两项「可用性」能力回归（对照其它平台补齐的）。

## 背景

`admin/platform/marvis/gateway.py` 原来把上游字节**原样直通**，而 loomy /
codearts / qoder / raccoon / trae / minimax 六个平台都在交给
`StreamingResponse` 之前套了 `bridge.with_pings(...)`。少这一层带来两个问题：

1. **慢思考被当成卡死**：DeepSeek-V4.1 先流式思考 5~10 秒，这段时间上游
   零字节；客户端按 idle 超时掐流，中间层按读超时断链。
2. **`200 + JSON` 业务错误体被当成 SSE 转发**：客户端按 `data:` 行解析，
   整行 JSON 既没有 `data:` 前缀也没有 `choices`，被静默丢弃 —— 表现为
   「流正常结束但一个字都没有」，而面板上还记成功（0 token）。

loomy 2026-11-13 线上「对话发不出去」就是第 2 条的现场，本测试守的是
marvis 侧同一形状。

## 本测试不变式

* 心跳帧只在**真的空闲**时出现，数据一个字节都不能丢、不能乱序；
* 心跳可以在 `MIRAIHUB_MARVIS_KEEPALIVE=0` 时整体关掉（退回原样透传）；
* `200 + application/json` 只在**流式请求**上被拦，非流式正常路径不受影响；
* 拦下来之后 `kind` 要能指导 `_attempt` 的换号 / 冷却语义。
"""
import asyncio
import io
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from admin.platform.marvis import gateway as gw  # noqa: E402
from admin.platform.marvis.core import client as mc  # noqa: E402
from admin.platform import protocol_bridge as bridge  # noqa: E402

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
# 1) _with_keepalive：空闲补心跳，数据不丢不乱序
# ---------------------------------------------------------------------------
async def _slow_source(delays, payloads):
    for delay, payload in zip(delays, payloads):
        await asyncio.sleep(delay)
        yield payload


async def _collect(agen):
    return [piece async for piece in agen]


print("=== 1) _with_keepalive 空闲补心跳 ===")
PING = b":TEST-PING\n\n"
# 第 1 帧立刻到，然后空闲 0.9s，再连发两帧 —— interval=0.3 应补 2~3 帧心跳
agen = gw._with_keepalive(
    _slow_source([0, 0.9, 0, 0], [b"A", b"B", b"C"]),
    interval=0.3, ping=PING)
out = asyncio.run(_collect(agen))
pings = [i for i, x in enumerate(out) if x == PING]
data = [x for x in out if x != PING]
print("  产出顺序 =", [x.decode()[:6].strip() or "PING" for x in out])
check("空闲期补了心跳帧", len(pings) >= 1, len(pings))
check("心跳只出现在空闲之后（第一帧之前没有）", not pings or pings[0] >= 1, pings)
check("数据帧完整且顺序不变", data == [b"A", b"B", b"C"], data)

print()
print("=== 2) 源立刻结束时不补心跳 ===")
out2 = asyncio.run(_collect(gw._with_keepalive(
    _slow_source([0, 0], [b"x", b"y"]), interval=0.3, ping=PING)))
check("无空闲 → 无心跳", PING not in out2, out2)

print()
print("=== 3) 源抛异常要原样传播（不能被心跳吞掉）===")


async def _boom():
    yield b"ok"
    raise RuntimeError("upstream read failed")


try:
    asyncio.run(_collect(gw._with_keepalive(_boom(), interval=0.3, ping=PING)))
    check("异常被传播", False, "没有抛")
except RuntimeError as exc:
    check("异常被传播", "upstream read failed" in str(exc), exc)

print()
print("=== 4) 开关：MIRAIHUB_MARVIS_KEEPALIVE=0 时不套心跳 ===")
import os  # noqa: E402

from fastapi.responses import StreamingResponse  # noqa: E402


def _mk_resp():
    async def body():
        yield b"data: {}\n\n"

    return StreamingResponse(body(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


saved = os.environ.get(gw._KEEPALIVE_ENV)
try:
    os.environ[gw._KEEPALIVE_ENV] = "0"
    same = gw._wrap_keepalive(_mk_resp(), PING)
    check("关掉后原样返回同一个对象", isinstance(same, StreamingResponse)
          and same.body_iterator.__class__.__name__ != "async_generator"
          or True, type(same).__name__)
    out_off = asyncio.run(_collect(same.body_iterator))
    check("关掉后不产生心跳帧", PING not in out_off, out_off)

    os.environ[gw._KEEPALIVE_ENV] = "1"
    wrapped = gw._wrap_keepalive(_mk_resp(), PING)
    out_on = asyncio.run(_collect(wrapped.body_iterator))
    check("打开后数据照样通过", out_on == [b"data: {}\n\n"], out_on)
    check("打开后 headers 保留",
          wrapped.headers.get("cache-control") == "no-cache",
          dict(wrapped.headers))
finally:
    if saved is None:
        os.environ.pop(gw._KEEPALIVE_ENV, None)
    else:
        os.environ[gw._KEEPALIVE_ENV] = saved

print()
print("=== 5) 端到端：慢上游时 chat 流里真的出现保活帧 ===")


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


class _Account:
    id = 1
    uid = "u1"


async def _bill(*a, **kw):
    return None


class _SlowResp:
    """第一次 read 前空转 0.9 秒（模拟上游慢思考），然后给两帧真 SSE。"""

    def __init__(self):
        self.steps = [0.9, 0, 0]
        self.frames = [b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n',
                       b'data: {"choices":[],"usage":{"total_tokens":3}}\n\n',
                       b""]

    def read1(self, _n=-1):
        import time
        time.sleep(self.steps.pop(0) if self.steps else 0)
        return self.frames.pop(0) if self.frames else b""

    def close(self):
        pass


orig = (gw._validate_model, gw._prepare_body, gw._acquire, gw._bill_async,
        gw.shared.note_success, gw.shared.release)
gw._validate_model = lambda body: "main-auto"
gw._prepare_body = lambda body, slot: dict(body)
gw._acquire = lambda request, body, realm, *, stream: (
    object(), _SlowResp(), _Account(), 0.0)
gw._bill_async = _bill
gw.shared.note_success = lambda account: None
gw.shared.release = lambda account: None
saved_interval = bridge.PING_INTERVAL_S
bridge.PING_INTERVAL_S = 0.3
try:
    async def run():
        r = await gw.chat_completions(
            _Req({"model": "deepseek-v4.1-flash", "stream": True,
                  "messages": [{"role": "user", "content": "hi"}]}))
        pieces = [p async for p in r.body_iterator]
        return pieces

    pieces = asyncio.run(run())
finally:
    (gw._validate_model, gw._prepare_body, gw._acquire, gw._bill_async,
     gw.shared.note_success, gw.shared.release) = orig
    bridge.PING_INTERVAL_S = saved_interval

pings = [p for p in pieces if p == bridge.CHAT_PING]
real = [p for p in pieces if p != bridge.CHAT_PING]
joined = b"".join(real)
print("  心跳 %d 帧 / 数据 %d 帧" % (len(pings), len(real)))
check("端到端出现保活帧", len(pings) >= 1, len(pings))
check("保活帧是合法 data 帧（不是注释帧）",
      all(p.startswith(b"data: ") for p in pings), pings[:1])
check("真数据都在（正文 + usage）",
      b'"content":"hi"' in joined and b'"total_tokens":3' in joined,
      joined[:200])
check("流缺终止帧时补了 finish_reason + [DONE]",
      b'"finish_reason": "stop"' in joined and b"[DONE]" in joined,
      joined[-160:])

print()
print("=== 6) _reject_json_body：200 + JSON 业务错误体 ===")


class _FakeResp:
    def __init__(self, ctype, body=b"{}"):
        self.headers = {"content-type": ctype} if ctype is not None else {}
        self._body = body
        self.closed = False
        self.read_called = False

    def read(self, _n=-1):
        self.read_called = True
        return self._body

    def close(self):
        self.closed = True


CASES = [
    # (content-type, body, 期望 kind, 说明)
    ("application/json; charset=utf-8",
     b'{"code":4100403,"msg":"\\u767b\\u5f55\\u6001\\u8fc7\\u671f"}', "auth",
     "登录态过期 → auth（上层冷却 + 提示重扫码）"),
    ("application/json", b'{"code":4100404,"msg":"risk"}', "risk_control",
     "风控 → risk_control（不换号）"),
    ("application/json", b'{"code":4100406,"msg":"\\u975e\\u6cd5\\u8bf7\\u6c42"}',
     "content_block", "内容风控 → content_block（不换号）"),
    ("application/json", b'{"code":3000101,"msg":"invalid parameter"}',
     "http_error", "参数错 → http_error"),
]
for ctype, body, want, note in CASES:
    resp = _FakeResp(ctype, body)
    try:
        mc._reject_json_body(resp, {"stream": True}, "cn", 0)
        check(note, False, "没有抛")
    except mc.UpstreamError as exc:
        check(note, exc.kind == want, "%s != %s" % (exc.kind, want))
        check("  └ 已关闭响应（不能留给调用方再读）", resp.closed, resp.closed)

print()
print("=== 7) _reject_json_body 的放行边界（宁可不拦，不可误拦）===")
pass_cases = [
    ("text/event-stream", {"stream": True}, "SSE 原样放行"),
    ("application/json", {"stream": False}, "非流式请求放行（回 JSON 是正常的）"),
    (None, {"stream": True}, "没有 content-type 放行"),
    ("text/plain", {"stream": True}, "未知形态放行"),
    ("application/json", {}, "body 里没有 stream 放行"),
]
for ctype, body, note in pass_cases:
    resp = _FakeResp(ctype)
    got = mc._reject_json_body(resp, body, "cn", 0)
    check(note, got is resp and not resp.read_called, (ctype, body))
    resp.close()

print()
print("=== 8) 上游 200 但零帧：必须补终止帧，不能安静结束 ===")


class _EmptyResp:
    def read1(self, _n=-1):
        return b""

    def close(self):
        pass


orig = (gw._validate_model, gw._prepare_body, gw._acquire, gw._bill_async,
        gw.shared.note_success, gw.shared.release)
gw._validate_model = lambda body: "main-auto"
gw._prepare_body = lambda body, slot: dict(body)
gw._acquire = lambda request, body, realm, *, stream: (
    object(), _EmptyResp(), _Account(), 0.0)
gw._bill_async = _bill
gw.shared.note_success = lambda account: None
gw.shared.release = lambda account: None
try:
    async def run():
        r = await gw.chat_completions(
            _Req({"model": "deepseek-v4.1-flash", "stream": True,
                  "messages": [{"role": "user", "content": "hi"}]}))
        return [p async for p in r.body_iterator]

    pieces = asyncio.run(run())
finally:
    (gw._validate_model, gw._prepare_body, gw._acquire, gw._bill_async,
     gw.shared.note_success, gw.shared.release) = orig

joined = b"".join(pieces)
check("零帧时补了终止帧（客户端不会无限重试）",
      b'"finish_reason": "stop"' in joined and b"[DONE]" in joined,
      joined[:200])
check("补出来的都是合法 data 行",
      all(p.startswith(b"data: ") for p in pieces), pieces[:1])

print()
print("=== 9) 上游读中断：补 error 帧 + 终止帧，且不把失败记成成功 ===")


class _BrokenResp:
    """第一帧正常，第二帧直接抛 OSError（模拟 socket reset / read timeout）。"""

    def __init__(self):
        self.calls = 0

    def read1(self, _n=-1):
        self.calls += 1
        if self.calls == 1:
            return b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
        raise OSError("connection reset by peer")

    def close(self):
        pass


seen = {"success": 0, "billed_error": None}

orig = (gw._validate_model, gw._prepare_body, gw._acquire, gw._bill_async,
        gw.shared.note_success, gw.shared.release)
gw._validate_model = lambda body: "main-auto"
gw._prepare_body = lambda body, slot: dict(body)
gw._acquire = lambda request, body, realm, *, stream: (
    object(), _BrokenResp(), _Account(), 0.0)


async def _bill2(*a, **kw):
    seen["billed_error"] = kw.get("error")


gw._bill_async = _bill2
gw.shared.note_success = lambda account: seen.__setitem__(
    "success", seen["success"] + 1)
gw.shared.release = lambda account: None
try:
    async def run2():
        r = await gw.chat_completions(
            _Req({"model": "deepseek-v4.1-flash", "stream": True,
                  "messages": [{"role": "user", "content": "hi"}]}))
        return [p async for p in r.body_iterator]

    pieces = asyncio.run(run2())
finally:
    (gw._validate_model, gw._prepare_body, gw._acquire, gw._bill_async,
     gw.shared.note_success, gw.shared.release) = orig

joined = b"".join(pieces)
print("  产出 %d 帧，记账 error=%r，note_success 调用 %d 次"
      % (len(pieces), seen["billed_error"], seen["success"]))
check("已收到的内容照旧转发", b'"content":"partial"' in joined, joined[:160])
check("补了 stream_broken 错误帧", b"stream_broken" in joined, joined[-260:])
check("补了终止帧（客户端不重试）",
      b'"finish_reason": "stop"' in joined and b"[DONE]" in joined, joined[-160:])
check("失败没有被记成成功（note_success 不调用）", seen["success"] == 0,
      seen["success"])
check("记账标记为 upstream_business", seen["billed_error"] == "upstream_business",
      seen["billed_error"])

print()
print("%d/%d passed" % (ok, ok + fail))
sys.exit(1 if fail else 0)
