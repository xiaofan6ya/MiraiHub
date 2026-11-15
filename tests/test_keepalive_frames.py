#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SSE 保活帧的协议合规回归（PR #40 的收尾）。

## 背景

长思考请求里上游可能几十秒无字节。原来每 8 秒发 `: ping` **注释帧** ——
SSE 规范里注释行会被解析器丢弃、不产生任何事件，客户端的 idle 计时器
不会重置，于是「服务端还在干活，客户端已经判超时断开」。

PR #40 把保活换成合法 data 帧，方向正确。但原实现缺 `created` / `model`
两个 OpenAI chunk **必填**字段：SDK 默认 `construct_type()` 宽松解析能过，
开 `strict_response_validation=True` 就抛 ValidationError —— 保活帧反而
把流打断。本测试锁住「帧形状 + 严格校验」这两条。

运行：
  python tests/test_keepalive_frames.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(ROOT))

from admin.platform import protocol_bridge as bridge  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [ok] {name}")
    else:
        FAIL += 1
        print(f"  [!!] {name}  {detail}")


def test_chat_frame() -> None:
    print("\n== chat 保活帧 ==")
    raw = bridge.CHAT_PING.decode("utf-8")
    check("是 data 帧（不是注释帧）", raw.startswith("data: "), raw[:20])
    payload = json.loads(raw[len("data: "):].strip())

    check("object 正确",
          payload.get("object") == "chat.completion.chunk", payload.get("object"))
    check("id 有 chatcmpl 前缀",
          str(payload.get("id") or "").startswith("chatcmpl"),
          payload.get("id"))
    check("created 是合理时间戳",
          isinstance(payload.get("created"), int)
          and payload["created"] > 1_600_000_000, payload.get("created"))
    check("model 非空",
          isinstance(payload.get("model"), str)
          and bool(payload["model"].strip()), payload.get("model"))
    choices = payload.get("choices")
    check("单个 choice", isinstance(choices, list) and len(choices) == 1, choices)
    if isinstance(choices, list) and choices:
        one = choices[0]
        check("delta 为空（不带内容）", one.get("delta") == {}, one.get("delta"))
        check("finish_reason 为 None", one.get("finish_reason") is None,
              one.get("finish_reason"))
        check("index 为 0", one.get("index") == 0, one.get("index"))


def test_strict_sdk() -> None:
    print("\n== 严格客户端校验（openai SDK，可选）==")
    try:
        from openai.types.chat import ChatCompletionChunk
    except Exception:  # noqa: BLE001 - 没装 SDK 就跳过
        print("  [--] 未安装 openai SDK，跳过")
        return
    raw = bridge.CHAT_PING.decode("utf-8")
    payload = json.loads(raw[len("data: "):].strip())
    try:
        chunk = ChatCompletionChunk.model_validate(payload)
        check("strict 校验通过", True)
        check("解析后 created/model 不为 None",
              chunk.created is not None and chunk.model is not None,
              (chunk.created, chunk.model))
    except Exception as exc:  # noqa: BLE001
        check("strict 校验通过", False, f"{type(exc).__name__}: {exc}")


def test_fallback_switch() -> None:
    print("\n== 回退开关 ==")
    src = (ROOT / "admin" / "platform" / "protocol_bridge.py").read_text(
        encoding="utf-8")
    check("有 MIRAIHUB_KEEPALIVE_DATA_FRAME 开关",
          "MIRAIHUB_KEEPALIVE_DATA_FRAME" in src)
    check("置 0 时回退注释帧", "COMMENT_PING" in src)
    # 环境变量解析必须在帧生成函数里（而不是只在一个已求值的常量上）
    fn = src[src.index("def _chat_ping_frame"):]
    fn = fn[:fn.index("\ndef ")]
    check("帧生成函数内部判断开关", "KEEPALIVE_DATA_FRAME" in fn)


def test_anthropic_ping_untouched() -> None:
    print("\n== Anthropic 官方 ping 事件不变 ==")
    raw = bridge.ANTHROPIC_PING.decode("utf-8")
    check("仍是 event: ping", raw.startswith("event: ping"), raw[:20])
    payload = json.loads(raw.split("data: ", 1)[1].strip())
    check("payload type=ping", payload.get("type") == "ping", payload)


if __name__ == "__main__":
    test_chat_frame()
    test_strict_sdk()
    test_fallback_switch()
    test_anthropic_ping_untouched()
    print()
    print(f"通过 {PASS} / {PASS + FAIL}")
    sys.exit(1 if FAIL else 0)
