# -*- coding: utf-8 -*-
"""SSE 帧对齐回归：空闲心跳不能把半个 JSON 帧撕开（marvis 流式）。

## 缺陷（2026-11-19 定位；上游 origin/main 3c7fce8 自身就有）

_iter_upstream 把 read1() 的**原始碎片**直接 yield，_with_keepalive 在超过
PING_INTERVAL_S（8.0s）没有新块时插一帧心跳 —— 两层都没有「帧」的概念，
于是心跳会落在半帧中间：

    data: {"choices":[{"delta":{"content":"hel     <- 半帧
    data: {"id":"chatcmpl-...","choices":[...]}   <- 心跳插在中间
    f"}}]}\n\n

客户端按行 json.loads → 那一行必抛 JSONDecodeError（实测：半帧 + 静默 9s +
半帧 + 静默 9s ⇒ 2 条 data 行解析失败、2 处一行内有多个 data: 前缀）。

第二种形态的触发条件**很窄**：只有上游最后一段 data: [DONE] 后面
**连一个换行都没有**时，_scan_stream 尾巴缓存里那一行永远不会被判读，
state["done"] 为假 → generator 补 _terminal_frame() →
data: [DONE]data: {...} 粘成一行。

注意 data: [DONE]\n（有换行、没有空行）**不触发**：_scan_stream 按 \n 切行、
只把最后一段当尾巴，这一行已经落在 lines 里、能正常判读。本文件把它作为
对照组钉住 —— 精确触发条件是 PR 正文的一部分。

## 本测试守什么

1. 半帧 → 静默（> 心跳间隔）→ 补齐；再静默 → 裸 [DONE]：输出按 \n\n 可切，
   每段去掉 data: 前缀后必须 json.loads 成功（[DONE] 除外）；
2. 任何一行里不能出现两个 data: 前缀；
3. [DONE] 之后不得再追加 _terminal_frame（data: [DONE] 只出现一次、且在末尾）；
4. 静默期心跳确实发出来了（否则用例根本没走到撕裂路径）；
5. 第二道防线：源已确认是分帧 SSE 时，半帧中间不插心跳。

## 自证（红 → 绿）

修复前（_iter_upstream 直接透传原始碎片）第 1 组在 1/2/3 上必红；给
_iter_upstream 加帧对齐缓冲、generator 补 EOF flush 后全绿。这不是「改断言
凑绿」：断言本身就是「按 \n\n 切出来的每一段必须是完整 JSON」—— 也就是任何
SSE 客户端按行解析的前提。

运行：
  python tests/test_marvis_stream_framing.py
"""
from __future__ import annotations

import asyncio
import io
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
if __name__ == "__main__" and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace")

from admin.platform import protocol_bridge as bridge  # noqa: E402
from admin.platform.marvis import gateway as gw  # noqa: E402

PING = bridge.CHAT_PING
#: 生产值是 bridge.PING_INTERVAL_S = 8.0；测试压到 0.12s，语义不变。
INTERVAL = 0.12
#: 2.5 × INTERVAL，保证每段静默期至少插得进一帧心跳。
SILENCE = 0.30

OK = 0
BAD = 0


def check(name, cond, detail=""):
    global OK, BAD
    if cond:
        OK += 1
        print("PASS  " + name)
    else:
        BAD += 1
        print("FAIL  " + name + ("  " + str(detail) if detail else ""))


# ---------------------------------------------------------------------------
# 假上游：按脚本吐固定碎片，碎片之间可以 sleep（模拟上游静默）
# ---------------------------------------------------------------------------
class _Scripted:
    """(delay, part) 序列：先睡 delay，再返回 part；part 为空表示只静默。"""

    def __init__(self, script):
        self.script = list(script)
        self.closed = False

    def read1(self, _n=-1):
        while self.script:
            delay, part = self.script.pop(0)
            if delay:
                time.sleep(delay)
            if part:
                return part
        return b""

    # 没有 read1 的响应对象会退回 read —— 这里两个都给，行为一致
    def read(self, n=-1):
        return self.read1(n)

    def close(self):
        self.closed = True


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


def run_stream(script, interval=INTERVAL):
    """走一遍 chat_completions 的流式路径，返回产出的所有块。"""
    saved = (gw._validate_model, gw._prepare_body, gw._acquire, gw._bill_async,
             gw.shared.note_success, gw.shared.release,
             bridge.PING_INTERVAL_S)
    gw._validate_model = lambda body: "main-auto"
    gw._prepare_body = lambda body, slot: dict(body)
    gw._acquire = lambda request, body, realm, *, stream: (
        object(), _Scripted(script), _Account(), time.perf_counter())

    async def _bill(*_a, **_kw):
        return None

    gw._bill_async = _bill
    gw.shared.note_success = lambda account: None
    gw.shared.release = lambda account: None
    bridge.PING_INTERVAL_S = interval
    try:
        async def go():
            resp = await gw.chat_completions(
                _Req({"model": "deepseek-v4.1-flash", "stream": True,
                      "messages": [{"role": "user", "content": "hi"}]}))
            return [piece async for piece in resp.body_iterator]

        return asyncio.run(go())
    finally:
        (gw._validate_model, gw._prepare_body, gw._acquire, gw._bill_async,
         gw.shared.note_success, gw.shared.release,
         bridge.PING_INTERVAL_S) = saved


# ---------------------------------------------------------------------------
# 断言工具
# ---------------------------------------------------------------------------
def frames_of(pieces):
    """按帧分隔符切分输出；空段（末尾那个）不算帧。"""
    return [seg for seg in b"".join(pieces).split(b"\n\n") if seg]


def payload_of(seg):
    """取 data: 前缀后的负载；不是 data 行时返回 None。"""
    text = seg.strip()
    if not text.startswith(b"data:"):
        return None
    return text[5:].strip()


def broken_frames(pieces):
    """返回不能当帧解析的段：非 data 行，或去掉 data: 后不是合法 JSON。"""
    out = []
    for seg in frames_of(pieces):
        body = payload_of(seg)
        if body is None:
            out.append(("不是 data: 行", seg))
            continue
        if body == b"[DONE]":
            continue
        try:
            json.loads(body.decode("utf-8", "replace"))
        except Exception as exc:  # noqa: BLE001 - 解析失败就是要报的错
            out.append((type(exc).__name__, seg))
    return out


def multi_data_lines(pieces):
    """一行里出现多个 data: 前缀 —— 粘连帧的特征。"""
    joined = b"".join(pieces)
    return [line for line in joined.split(b"\n") if line.count(b"data:") > 1]


# ---------------------------------------------------------------------------
# 1) 主用例：半帧 + 静默 + 补齐 + 静默 + 裸 [DONE]
# ---------------------------------------------------------------------------
print("=== 1) 半帧 + 静默 + 补齐 + 静默 + 裸 [DONE]：必须帧对齐 ===")
pieces = run_stream([
    (0, b'data: {"choices":[{"delta":{"content":"hel'),  # 半帧（上游正常切块）
    (SILENCE, b'f"}}]}\n\n'),                           # 静默之后补齐
    (SILENCE, b""),                                     # 补齐之后再静默一次
    (0, b'data: [DONE]'),                               # 裸 [DONE]：无换行
])
joined = b"".join(pieces)
pings = [p for p in pieces if p == PING]
print("  产出 %d 块 / %d 字节 / 心跳 %d 帧" % (len(pieces), len(joined), len(pings)))
bad = broken_frames(pieces)
check("每段去掉 data: 前缀后都能 json.loads（或 [DONE]）", not bad, bad[:2])
check("没有一行里塞了两个 data: 前缀", not multi_data_lines(pieces),
      multi_data_lines(pieces)[:2])
check("[DONE] 只出现一次（没再补终止帧）",
      joined.count(b"data: [DONE]") == 1, joined.count(b"data: [DONE]"))
check("末尾就是 [DONE]（终止帧没粘在后面）",
      joined.rstrip().endswith(b"data: [DONE]"), joined[-160:])
check("静默期真的发过心跳（确实走到了撕裂路径）", len(pings) >= 2, len(pings))

# ---------------------------------------------------------------------------
# 2) 对照组：data: [DONE] 后面有换行、没有空行 → 本来就不粘
# ---------------------------------------------------------------------------
print()
print("=== 2) 对照组：data: [DONE] 带换行、无空行 → 不触发粘连 ===")
pieces = run_stream([
    (0, b'data: {"choices":[{"delta":{"content":"x"},"finish_reason":"stop"}]}\n\n'),
    (0, b'data: [DONE]\n'),
])
joined = b"".join(pieces)
check("每段都可解析", not broken_frames(pieces), broken_frames(pieces)[:2])
check("没有一行两帧", not multi_data_lines(pieces), multi_data_lines(pieces)[:2])
check("[DONE] 只出现一次", joined.count(b"data: [DONE]") == 1,
      joined.count(b"data: [DONE]"))

# ---------------------------------------------------------------------------
# 3) 对照组：整帧 + 每帧之间静默 > interval 也不该撕
# ---------------------------------------------------------------------------
print()
print("=== 3) 对照组：整帧 + 帧间静默 > interval → 心跳落在帧边界上 ===")
FRAME = b'data: {"choices":[{"delta":{"content":"z"}}]}\n\n'
pieces = run_stream([(0, FRAME)] + [(SILENCE, FRAME)] * 3)
joined = b"".join(pieces)
pings = [p for p in pieces if p == PING]
print("  心跳 %d 帧 / 数据帧 %d 个" % (len(pings), joined.count(b'"content":"z"')))
check("每段都可解析", not broken_frames(pieces), broken_frames(pieces)[:2])
check("没有一行两帧", not multi_data_lines(pieces), multi_data_lines(pieces)[:2])
check("静默期发过心跳", len(pings) >= 2, len(pings))
check("4 个数据帧都在", joined.count(b'"content":"z"') == 4,
      joined.count(b'"content":"z"'))

# ---------------------------------------------------------------------------
# 4) 第二道防线：源已确认分帧时，半帧中间不插心跳
# ---------------------------------------------------------------------------
print()
print("=== 4) 第二道防线：半帧中间不插心跳 ===")
HALF = b'data: {"choices":[{"delta":{"content":"b"'


async def _half_frame_source():
    yield b'data: {"choices":[{"delta":{"content":"a"}}]}\n\n'
    await asyncio.sleep(SILENCE)
    yield HALF                                  # 半帧
    await asyncio.sleep(SILENCE * 2)            # 半帧中间的空闲
    yield b'}}]}\n\n'                           # 补齐
    await asyncio.sleep(SILENCE)                # 补齐之后再空闲


async def _collect(agen):
    return [piece async for piece in agen]


out = asyncio.run(_collect(gw._with_keepalive(
    _half_frame_source(), interval=INTERVAL, ping=PING)))
print("  产出顺序 = %r" % (["PING" if x == PING else x[:26] for x in out],))
i_half = out.index(HALF)
i_close = out.index(b'}}]}\n\n')
check("半帧之前可以心跳（上一块停在帧边界）", PING in out[:i_half], out[:i_half])
check("半帧中间不插心跳", PING not in out[i_half + 1:i_close],
      out[i_half + 1:i_close])
check("补齐之后恢复心跳", PING in out[i_close + 1:], out[i_close + 1:])

print()
print("%d/%d passed" % (OK, OK + BAD))
sys.exit(1 if BAD else 0)
