# -*- coding: utf-8 -*-
"""issue #31：Anthropic `tool_result` 内嵌 image 必须真正送到上游。

## 现象

Claude Code 的 `Read` 工具读本地图片时，图片是以
`tool_result.content = [{type:"text"},{type:"image"}]` 回传的。
老实现给 `tool` 消息塞了一句 `[图片结果，已省略]` 占位符，
模型只看到「有一张图被省略了」，于是回
`Sorry, I can't help with that.`；同一张图放在 user content 顶层却正常 ——
这说明问题在 Anthropic → OpenAI 的转换层，而不是模型能力。

## 修复契约（本文件锁定的就是这三条）

1. `role="tool"` 的 content 仍是**字符串**（OpenAI 硬要求，塞对象会 400）；
2. 图片**提升到紧随其后的 user 消息 content 顶层**（Anthropic 等价语义）；
3. 顺序不能变：`assistant(tool_calls)` → `tool` → `user(image)`，
   中间插 user 会破坏 tool_call_id 配对。
"""
import base64
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from admin.platform.protocol_bridge import anthropic_to_openai  # noqa: E402

# 1x1 PNG
PNG_B64 = base64.b64encode(
    bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
                  "0000000a49444154789c6300010000050001"
                  "0d0a2db40000000049454e44ae426082")).decode()
IMAGE_BLOCK = {"type": "image",
               "source": {"type": "base64", "media_type": "image/png",
                          "data": PNG_B64}}


def build_body(tool_result_content):
    return {
        "model": "deepseek-v4.1-flash",
        "messages": [
            {"role": "user",
             "content": [{"type": "text", "text": "Read this image."}]},
            {"role": "assistant",
             "content": [{"type": "tool_use", "id": "toolu_1", "name": "Read",
                          "input": {"file_path": "repro.png"}}]},
            {"role": "user",
             "content": [
                 {"type": "tool_result", "tool_use_id": "toolu_1",
                  "content": tool_result_content},
                 {"type": "text", "text": "图中写着什么？"},
             ]},
        ],
    }


class ToolResultImageTests(unittest.TestCase):
    def _convert(self, content):
        return anthropic_to_openai(build_body(content))["messages"]

    def test_image_is_promoted_not_dropped(self):
        msgs = self._convert([{"type": "text", "text": "Image file:"}, IMAGE_BLOCK])
        assistant_at = next(i for i, m in enumerate(msgs) if m.get("tool_calls"))
        tool_msg = msgs[assistant_at + 1]
        self.assertEqual(tool_msg["role"], "tool")
        # 1) tool.content 必须是字符串
        self.assertIsInstance(tool_msg["content"], str)

        # 2) 图片在紧随其后的 user 消息顶层
        after = msgs[assistant_at + 2]
        self.assertEqual(after["role"], "user")
        self.assertIsInstance(after["content"], list)
        urls = [b["image_url"]["url"] for b in after["content"]
                if b.get("type") == "image_url"]
        self.assertTrue(urls, "图片块丢了 —— 模型只会看到「已省略」")
        self.assertTrue(urls[0].startswith("data:image/png;base64,"))

    def test_no_omitted_placeholder(self):
        msgs = self._convert([{"type": "text", "text": "Image file:"}, IMAGE_BLOCK])
        blob = str(msgs)
        self.assertNotIn("已省略", blob)

    def test_text_only_tool_result_has_no_extra_user_message(self):
        msgs = self._convert([{"type": "text", "text": "plain output"}])
        assistant_at = next(i for i, m in enumerate(msgs) if m.get("tool_calls"))
        self.assertEqual(msgs[assistant_at + 1]["content"], "plain output")
        # 后面那条 user 只带提问正文，没有 image 块
        tail = msgs[assistant_at + 2]
        self.assertEqual(tail["role"], "user")
        self.assertNotIsInstance(tail["content"], list)

    def test_top_level_image_still_works(self):
        """回归：user content 顶层的 image 不能被这次改动弄坏。"""
        body = build_body([{"type": "text", "text": "Image file:"}])
        body["messages"][-1]["content"].append(IMAGE_BLOCK)
        msgs = anthropic_to_openai(body)["messages"]
        self.assertTrue(any(
            isinstance(m.get("content"), list)
            and any(b.get("type") == "image_url" for b in m["content"])
            for m in msgs))


if __name__ == "__main__":
    unittest.main(verbosity=2)