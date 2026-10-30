#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""上游「客户端身份 + 调用追踪」头：让上游用量明细能正确归因。

为什么需要这个模块
------------------
上游 `/billing/meter/get-user-request-usage` 的每一行有两个列是由**请求头**
决定的（2026-10 用真实凭证逐项探针确认，见 tests/test_upstream_identity.py）：

    client         <- X-IDE-Name      （缺 → 用量明细「使用端」整列为空）
    agentPurpose   <- X-Agent-Purpose （缺 → 用途列为空）
    requestId      <- X-Conversation-Request-ID
                      缺 → 上游自造 `crb-<32hex>`，既不可关联也不可去重
    （去重键）      <- X-Conversation-Request-ID
                      同一值第二次请求不再单独记账 → 复用即静默少记账

探针矩阵（本地真实账号，同一张用量表）：

    A 仅鉴权头                        client=''          requestId=crb-...
    C 仅身份头                        client='WorkBuddy' requestId=crb-...
    D 仅 purpose                      client=''          requestId=crb-...
    E 身份头 + X-Request-ID           client='WorkBuddy' requestId=crb-...
    I 身份头 + X-Conversation-Request-ID
                                      client='WorkBuddy' requestId=该值
    H 五个 id 头各不相同              requestId=Conversation-Request-ID 的值
    M/N 共用同一个 Conversation-Request-ID
                                      只落一行（N 的输入消失）

所以「带一个 X-Request-ID」是**不够**的：上游看的是
`X-Conversation-Request-ID`。而它同时是去重键，必须**每次上游调用唯一**，
否则多轮工具链 / 换号重试 / 换模型重发会被折叠成一条，积分少记。

分工
----
    identity_headers(purpose)   谁在调用（X-IDE-* / X-Product / X-Agent-*）
    trace_headers(conversation, turn)
                                这一次调用（四个 id 头，每次调用现场生成）

身份头取自「客户端参数档案」（admin/client_profile.py），与 UA / 桌面指纹同源，
后台改版本号即时生效；trace 头一律现场随机（UUIDv7，与官方 messageId 同形状：
32 位十六进制、无连字符、按时间单调）。
"""
from __future__ import annotations

import os
import time
import uuid

from admin import client_profile as cprofile

#: 官方内嵌 CLI 在建连时的默认值（cli/dist 源码：`$.meta["codebuddy.ai/mode"] ?? "craft"`）。
DEFAULT_INTENT = "craft"
#: `X-Agent-Runtime` 官方取值之一（本地客户端）。
DEFAULT_RUNTIME = "local-agent"

DEFAULT_PURPOSE = "conversation"

#: 各端点应报的上游用途。与官方客户端自己的取值一致，便于用量表按用途聚合。
#: 左列是**内部端点名**（路由层沿用的老写法，如 `image-generation`），右侧是
#: 上游用量表里真正出现的用途串；两种写法都接受，免得调用方记两套。
PURPOSES = {
    "chat": "conversation",
    "responses": "conversation",
    "messages": "conversation",
    "anthropic": "conversation",
    "agent": "conversation",
    "images": "image_generation",
    "image-generation": "image_generation",
    "image_generation": "image_generation",
    "videos": "video_generation",
    "video-generation": "video_generation",
    "video_generation": "video_generation",
    "audio": "audio_generation",
    "audio-generation": "audio_generation",
}


def enabled() -> bool:
    """身份头开关。默认开启：缺它就等于在用量明细里隐身。"""
    return os.getenv("ADMIN_UPSTREAM_IDENTITY_HEADERS", "1") != "0"


def purpose_for(kind: str) -> str:
    """把内部端点名映射成上游用途串；未知端点按会话处理。"""
    mapped = PURPOSES.get((kind or "").strip().lower())
    if mapped:
        return mapped
    configured = os.getenv("ADMIN_UPSTREAM_AGENT_PURPOSE", "").strip()
    return configured or DEFAULT_PURPOSE


def identity_headers(purpose: str = DEFAULT_PURPOSE) -> dict:
    """客户端归属头；上游用它填用量明细的「使用端 / 用途」两列。

    只发官方**内嵌 CLI** 在模型请求里真正会发的归属头。刻意不合并
    `client_profile` 里的浏览器风味头（X-Requested-With / X-CodeBuddy-Request
    等）：那是 web 一侧的流量，混进 CLI 请求反而制造出官方不存在的组合。

    值全部来自客户端参数档案（探测到的真实安装包 / 后台保存值 / 兜底），
    与 UA、桌面指纹同源，因此三者不会互相矛盾。
    """
    if not enabled():
        return {}
    try:
        base = cprofile.risk_headers()
    except Exception:  # 档案异常绝不能挡住转发
        base = {}
    headers = {
        "X-IDE-Name": str(base.get("X-IDE-Name") or "WorkBuddy"),
        "X-IDE-Type": str(base.get("X-IDE-Type") or "WorkBuddy"),
        "X-IDE-Version": str(base.get("X-IDE-Version") or cprofile.DEFAULTS["desktop_version"]),
        # X-Product 是部署形态（SaaS），不是产品名 —— 档案里已按官方口径取值。
        "X-Product": str(base.get("X-Product") or cprofile.DEFAULTS["product"]),
        "X-Agent-Intent": DEFAULT_INTENT,
        "X-Agent-Runtime": DEFAULT_RUNTIME,
    }
    if purpose:
        headers["X-Agent-Purpose"] = purpose
    return headers


def new_id() -> str:
    """UUIDv7（32 位十六进制、无连字符），与官方 messageId 同形状。

    必须按时间单调：上游按 requestId 排序/检索，随机 v4 会让同一次会话的
    多条记录在时间轴上乱序。
    """
    millis = int(time.time() * 1000) & ((1 << 48) - 1)
    rand = int.from_bytes(os.urandom(10), "big")
    value = millis << 80
    value |= 0x7 << 76                      # version 7
    value |= ((rand >> 68) & 0xFFF) << 64   # rand_a
    value |= 0x2 << 62                      # variant RFC 4122
    value |= rand & ((1 << 62) - 1)
    return uuid.UUID(int=value).hex


def trace_headers(conversation_id: str = "", turn_id: str = "") -> dict:
    """本次上游调用的追踪头。**每次调用都要重新生成一次。**

    `X-Conversation-Request-ID` 是上游用量表的 requestId，同时是去重键：
    复用同一个值会让第二次调用不落账（探针 M/N 实证）。所以每次真正发往
    上游的请求（含换号 / 换模型后的每次尝试）都必须拿到新的值。

    与它相对，`turn_id` 在**一次网关请求内**保持不变：它标记「这是同一次
    用户发送派生出的第 N 次尝试」，方便把多行用量还原成一整轮。
    """
    if not enabled():
        return {}
    attempt = new_id()
    message = turn_id or attempt
    conversation = conversation_id or message
    return {
        "X-Request-ID": attempt,
        # 上游用量表的 requestId 取这一个；缺它就回退成 crb- 前缀的自造值。
        "X-Conversation-Request-ID": attempt,
        "X-Conversation-Message-ID": message,
        "X-Root-Request-ID": message,
        "X-Conversation-ID": conversation,
    }


def turn_id(body: dict | None = None) -> str:
    """一次网关请求的稳定标识（同一次请求的所有上游尝试共用）。"""
    del body
    return new_id()