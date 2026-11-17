# -*- coding: utf-8 -*-
"""Anthropic `tool_result` 内嵌 image 必须真正送到上游（WorkBuddy 链路）。

## 背景

Claude Code 的 `Read` 工具读本地图片时，图片是以
`tool_result.content = [{type:"text"},{type:"image"}]` 的形状回传的。

`admin/platform/protocol_bridge.py` 的 `anthropic_to_openai()` 已经处理了
这种形状（见 tests/test_anthropic_tool_result_image.py），但 WorkBuddy
平台的 `/v1/messages` 实际走的是**根目录 `anthropic_adapter.py`** 的
`anthropic_request_to_chat()`（`admin/routers/proxy.py::anthropic_messages`）。
这条链路里 `_tool_result_to_text()` 会把 image 块 `json.dumps(...)[:500]`，
模型只能读到一段被截断的 base64 —— 于是答「图中没有任何文字」。

## 修复契约（本文件锁定的就是这四条）

1. `role="tool"` 的 content 仍是**字符串**（OpenAI 硬要求，塞对象会 400）；
2. tool_result 里的 image 块提升到紧随其后的 user 消息 content 顶层；
3. 顺序不变：`assistant(tool_calls)` → `tool` → `user(image/<提问>)`；
4. 非法 image source 不抛异常、不产生 `[图片]` 占位（宁可无图也不误导模型）。
"""
import base64
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from anthropic_adapter import anthropic_request_to_chat  # noqa: E402
from desensitize import desensitize_body  # noqa: E402
from upstream_compat import normalize_harness_body  # noqa: E402

# 1x1 PNG
PNG_B64 = base64.b64encode(
    bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
                  "0000000a49444154789c6300010000050001"
                  "0d0a2db40000000049454e44ae426082")).decode()
IMAGE_BLOCK = {"type": "image",
               "source": {"type": "base64", "media_type": "image/png",
                          "data": PNG_B64}}


def build_body(tool_result_content, same_message_text=None):
    """Claude Code `Read` 的真实请求形状。

    `same_message_text` 为 None 时按单轮独立 user 消息构造（复现脚本形状），
    否则把提问文本与 tool_result 放进同一条 user 消息（客户端实际形状）。
    """
    tail = ([{"type": "text", "text": same_message_text}]
            if same_message_text is not None
            else [{"type": "text", "text": "图中写着什么？"}])
    msgs = [
        {"role": "user", "content": [{"type": "text", "text": "Read this image."}]},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_1", "name": "Read",
             "input": {"file_path": "repro.png"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1",
             "content": tool_result_content}] + tail},
    ]
    return {"model": "deepseek-v4.1-flash", "max_tokens": 128, "messages": msgs}


def _image_urls(msg):
    content = msg.get("content")
    if not isinstance(content, list):
        return []
    return [b["image_url"]["url"] for b in content
            if isinstance(b, dict) and b.get("type") == "image_url"]


class ToolResultImageAdapterTests(unittest.TestCase):
    def _convert(self, content, same_message_text=None):
        return anthropic_request_to_chat(
            build_body(content, same_message_text))["messages"]

    def test_image_is_promoted_to_user_message(self):
        msgs = self._convert([{"type": "text", "text": "Image file:"}, IMAGE_BLOCK])
        assistant_at = next(i for i, m in enumerate(msgs) if m.get("tool_calls"))

        tool_msg = msgs[assistant_at + 1]
        self.assertEqual(tool_msg["role"], "tool")
        # 1) tool.content 必须是字符串
        self.assertIsInstance(tool_msg["content"], str)

        # 2) 图片在紧随其后的 user 消息顶层
        after = msgs[assistant_at + 2]
        self.assertEqual(after["role"], "user")
        urls = _image_urls(after)
        self.assertTrue(urls, "图片块丢了 —— 模型只会看到一串 base64")
        self.assertTrue(urls[0].startswith("data:image/png;base64,"))

    def test_image_promoted_when_question_in_same_message(self):
        """tool_result 与提问同条消息时，图片仍要在 user 顶层。"""
        msgs = self._convert([{"type": "text", "text": "Image file:"}, IMAGE_BLOCK],
                             same_message_text="图中写着什么？只回答文字。")
        user_msg = next(m for m in msgs if _image_urls(m))
        self.assertEqual(user_msg["role"], "user")
        texts = [b.get("text") for b in user_msg["content"]
                 if isinstance(b, dict) and b.get("type") == "text"]
        self.assertTrue(any("图中写着什么" in (t or "") for t in texts))

    def test_no_base64_dumped_into_tool_content(self):
        """tool.content 里不能再出现被截断的 base64 JSON。"""
        msgs = self._convert([{"type": "text", "text": "Image file:"}, IMAGE_BLOCK])
        tool_msg = next(m for m in msgs if m.get("role") == "tool")
        self.assertNotIn("image/png", tool_msg["content"])
        self.assertNotIn(PNG_B64[:40], tool_msg["content"])

    def test_text_only_tool_result_has_no_extra_user_message(self):
        msgs = self._convert([{"type": "text", "text": "plain output"}])
        assistant_at = next(i for i, m in enumerate(msgs) if m.get("tool_calls"))
        self.assertEqual(msgs[assistant_at + 1]["content"], "plain output")
        # 只有 tool_call_id 配对的 tool 消息，没有多出来的图片 user 消息
        self.assertEqual(len(_image_urls(msgs[assistant_at + 1])), 0)
        self.assertFalse(any(_image_urls(m) for m in msgs))

    def test_top_level_image_still_works(self):
        """回归：user content 顶层的 image 不能被这次改动弄坏。"""
        body = {
            "model": "deepseek-v4.1-flash", "max_tokens": 128,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "看图"}, IMAGE_BLOCK]}],
        }
        msgs = anthropic_request_to_chat(body)["messages"]
        self.assertTrue(any(_image_urls(m) for m in msgs))

    def test_invalid_image_source_does_not_raise_or_placeholder(self):
        """非法 source：不抛异常，也不留 `[图片]` 误导模型。"""
        bad = {"type": "image",
               "source": {"type": "base64", "media_type": "text/plain",
                          "data": "zzz"}}
        msgs = self._convert([{"type": "text", "text": "bad:"}, bad])
        tool_msg = next(m for m in msgs if m.get("role") == "tool")
        self.assertIsInstance(tool_msg["content"], str)
        self.assertNotIn("[图片]", tool_msg["content"])
        self.assertFalse(any(_image_urls(m) for m in msgs))

    def test_pairing_order_preserved(self):
        """assistant(tool_calls) → tool → user(image)：配对不能被插队破坏。"""
        msgs = self._convert([{"type": "text", "text": "Image file:"}, IMAGE_BLOCK])
        roles = [m["role"] for m in msgs]
        at = roles.index("assistant")
        self.assertEqual(roles[at + 1], "tool")
        self.assertEqual(roles[at + 2], "user")
        self.assertEqual(msgs[at + 1]["tool_call_id"],
                         msgs[at]["tool_calls"][0]["id"])

    def test_parallel_tool_calls_images_are_numbered(self):
        """并行 Read：两个 tool_result 各带一张图，占位符要能区分开。"""
        body = {
            "model": "m", "max_tokens": 64, "messages": [
                {"role": "user", "content": [{"type": "text", "text": "read two"}]},
                {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "A", "name": "Read", "input": {}},
                    {"type": "tool_use", "id": "B", "name": "Read", "input": {}}]},
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "A", "content": [IMAGE_BLOCK]},
                    {"type": "tool_result", "tool_use_id": "B", "content": [IMAGE_BLOCK]},
                    {"type": "text", "text": "对比这两张"}]},
            ]}
        msgs = anthropic_request_to_chat(body)["messages"]
        tools = [m for m in msgs if m.get("role") == "tool"]
        self.assertEqual(len(tools), 2)
        self.assertIn("#1", tools[0]["content"])
        self.assertIn("#2", tools[1]["content"])
        # 两张图都在末尾的 user 消息里，且顺序与工具返回一致
        last = msgs[-1]
        self.assertEqual(len(_image_urls(last)), 2)

    def test_multi_image_note_does_not_precede_images(self):
        """顺序说明必须挂在文本上，不能插到 image_url 之前。"""
        body = {
            "model": "m", "max_tokens": 64, "messages": [
                {"role": "user", "content": [{"type": "text", "text": "go"}]},
                {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "A", "name": "Read", "input": {}},
                    {"type": "tool_use", "id": "B", "name": "Read", "input": {}}]},
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "A", "content": [IMAGE_BLOCK]},
                    {"type": "tool_result", "tool_use_id": "B", "content": [IMAGE_BLOCK]},
                    {"type": "text", "text": "看这两张"}]},
            ]}
        last = anthropic_request_to_chat(body)["messages"][-1]
        self.assertEqual(last["content"][0]["type"], "image_url",
                         "说明被插到了图片前面，部分上游会因此读不到图")
        self.assertEqual(last["content"][-1]["type"], "text")

    def test_single_tool_result_multiple_images_numbered_sequentially(self):
        """单个 tool_result 含多张图时，占位符必须按 #1/#2/#3 连续递增，不能全标成 #1。"""
        body = {
            "model": "m", "max_tokens": 64, "messages": [
                {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "A", "name": "Read", "input": {}},
                    {"type": "tool_use", "id": "B", "name": "Read", "input": {}}]},
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "A",
                     "content": [IMAGE_BLOCK, IMAGE_BLOCK]},
                    {"type": "tool_result", "tool_use_id": "B",
                     "content": [IMAGE_BLOCK]}]},
            ]}
        msgs = anthropic_request_to_chat(body)["messages"]
        tools = [m for m in msgs if m.get("role") == "tool"]
        self.assertEqual(tools[0]["content"], "[图片 #1][图片 #2]")
        self.assertEqual(tools[1]["content"], "[图片 #3]")
        last = msgs[-1]
        self.assertEqual(len(_image_urls(last)), 3)
        self.assertIn("#1..#3", last["content"][-1]["text"])

    def test_mixed_invalid_and_valid_images_in_single_tool_result(self):
        """同一 tool_result 混有非法图和合法图时，只为合法图生成占位符。"""
        bad = {"type": "image",
               "source": {"type": "base64", "media_type": "text/plain",
                          "data": "zzz"}}
        msgs = self._convert([bad, IMAGE_BLOCK])
        tool_msg = next(m for m in msgs if m.get("role") == "tool")
        self.assertEqual(tool_msg["content"], "[图片 #1]")
        user_msg = msgs[-1]
        self.assertEqual(len(_image_urls(user_msg)), 1)

    def test_top_level_image_combined_with_tool_result_images(self):
        """同一条 user 消息既有顶层图又有工具图时，工具图排在前面且与 #1..#N 一一对应。"""
        img_top = {"type": "image", "source": {"type": "url", "url": "https://example.com/top.png"}}
        img_1 = {"type": "image", "source": {"type": "url", "url": "https://example.com/t1.png"}}
        img_2 = {"type": "image", "source": {"type": "url", "url": "https://example.com/t2.png"}}
        body = {
            "model": "m", "max_tokens": 64, "messages": [
                {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "A", "name": "Read", "input": {}},
                    {"type": "tool_use", "id": "B", "name": "Read", "input": {}}]},
                {"role": "user", "content": [
                    img_top,
                    {"type": "tool_result", "tool_use_id": "A", "content": [img_1]},
                    {"type": "tool_result", "tool_use_id": "B", "content": [img_2]},
                    {"type": "text", "text": "对比"}]},
            ]}
        msgs = anthropic_request_to_chat(body)["messages"]
        last = msgs[-1]
        urls = _image_urls(last)
        self.assertEqual(urls, [
            "https://example.com/t1.png",
            "https://example.com/t2.png",
            "https://example.com/top.png",
        ])
        self.assertIn("前 2 张图片", last["content"][-1]["text"])
        self.assertIn("其后为用户附带图片", last["content"][-1]["text"])

    def test_tool_result_only_without_user_text(self):
        """user 消息仅含 tool_result(image)、无任何 text 块时，不生成空 text 块。"""
        body = {
            "model": "m", "max_tokens": 64, "messages": [
                {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "A", "name": "Read", "input": {}}]},
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "A", "content": [IMAGE_BLOCK]}]},
            ]}
        msgs = anthropic_request_to_chat(body)["messages"]
        last = msgs[-1]
        self.assertEqual(last["role"], "user")
        self.assertEqual(len(last["content"]), 1)
        self.assertEqual(last["content"][0]["type"], "image_url")

    def test_empty_or_none_text_and_harness_reminder_pipeline(self):
        """空/None 文本、前置与未闭合 <system-reminder> 经过兼容+脱敏链路后不丢图、不丢说明、不崩溃。"""
        body = {
            "model": "m", "max_tokens": 64, "messages": [
                {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "A", "name": "Read", "input": {}},
                    {"type": "tool_use", "id": "B", "name": "Read", "input": {}}]},
                {"role": "user", "content": [
                    {"type": "text", "text": None},
                    {"type": "text", "text": ""},
                    {"type": "text", "text": "<system-reminder>pre</system-reminder>"},
                    {"type": "tool_result", "tool_use_id": "A", "content": [IMAGE_BLOCK]},
                    {"type": "tool_result", "tool_use_id": "B", "content": [IMAGE_BLOCK]},
                    {"type": "text", "text": "<system-reminder>unclosed"}]},
            ]}
        chat_body = anthropic_request_to_chat(body)
        normalize_harness_body(chat_body)
        chat_body = desensitize_body(
            chat_body,
            roles=("system", "developer"),
            desensitize_harness_user=True,
            desensitize_tools=True,
            compact_harness=True,
        )
        last = chat_body["messages"][-1]
        self.assertEqual(last["content"][0]["type"], "image_url")
        self.assertEqual(last["content"][1]["type"], "image_url")
        texts = [p["text"] for p in last["content"] if p.get("type") == "text"]
        self.assertTrue(all(t != "" for t in texts))
        self.assertTrue(any("共 2 张图片" in t for t in texts))

    def test_error_tool_result_and_whitespace_tool_use_id(self):
        """is_error=True 仍保留图；空白 tool_use_id 应被丢弃。"""
        body = {
            "model": "m", "max_tokens": 64, "messages": [
                {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "A", "name": "Read", "input": {}}]},
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "   ", "content": [IMAGE_BLOCK]},
                    {"type": "tool_result", "tool_use_id": "A", "is_error": True,
                     "content": [{"type": "text", "text": "partial "}, IMAGE_BLOCK]}]},
            ]}
        msgs = anthropic_request_to_chat(body)["messages"]
        tools = [m for m in msgs if m.get("role") == "tool"]
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["content"], "Error: partial [图片 #1]")
        self.assertEqual(len(_image_urls(msgs[-1])), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
