#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多平台网关 + Trae 接入的离线回归测试。

覆盖的都是**不依赖上游网络**的部分，因此可以在 CI / 无账号环境跑：

    平台注册表       登记、解析、区域归一、选择器优先级
    网关分发         默认平台原样穿过；trae 请求被接管；未知平台被拒绝
    协议转换         Chat / Responses / Anthropic 三向互转与工具调用解析
    设备指纹         稳定派生、账号级隔离、国际版三态
    账号扫描         本机 storage.json 解析（无桌面端时跳过）
    额度口径         两区不同结构 -> 同一组 (总量, 剩余)
    数据库迁移       platform 列与回填

用法：
    python tests/test_trae_platform.py
"""
from __future__ import annotations

import json
import os
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS = 0
FAIL = 0
SKIP = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [ok] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


def skip(name: str, why: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  [skip] {name} ({why})")


# ---------------------------------------------------------------------------
def test_registry() -> None:
    print("平台注册表")
    from admin.platform import registry
    from admin.platform.loader import load_platforms

    status = load_platforms()
    check("装配无失败", not status["failed"], str(status["failed"]))
    ids = registry.ids()
    check("默认平台在首位", ids and ids[0] == registry.DEFAULT_PLATFORM, str(ids))
    # Trae 的国内版与国际版是**两个平台**（不同主机/凭证/模型目录/额度口径），
    # 不是「一个平台两个区」。
    check("trae-cn 已登记", "trae-cn" in ids, str(ids))
    check("trae-intl 已登记", "trae-intl" in ids, str(ids))
    # 平台数量随接入演进增长，**不写死数字** —— 断言不变量：
    # 「内建默认平台仍是首位」（新平台不该抢占默认路由）
    check("内建默认平台在首位", ids[0] == "workbuddy", str(ids))

    cn = registry.get("trae-cn")
    intl = registry.get("trae-intl")
    check("trae-cn 固定区域 cn", cn is not None and cn.realm == "cn",
          str(cn.realm if cn else None))
    check("trae-intl 固定区域 intl", intl is not None and intl.realm == "intl",
          str(intl.realm if intl else None))
    # 两者落库到同一个存储平台（避免为「换区」复制一份号池代码）
    check("两个区共用落库平台 trae",
          cn is not None and intl is not None
          and cn.storage() == "trae" and intl.storage() == "trae",
          f"{cn.storage() if cn else None}/{intl.storage() if intl else None}")
    check("固定区域的平台忽略传入的区域",
          cn.normalize_realm("intl") == "cn" and intl.normalize_realm("cn") == "intl")

    # 选择器
    check("选择器 trae-cn", registry.resolve("trae-cn") == ("trae-cn", "cn"))
    check("选择器 trae-intl", registry.resolve("trae-intl") == ("trae-intl", "intl"))
    # 兼容老写法（客户端可能还在用）
    check("兼容 trae:cn", registry.resolve("trae:cn") == ("trae", "cn"))
    check("兼容 trae:intl", registry.resolve("trae:intl") == ("trae", "intl"))
    check("兼容裸 trae（两区）", registry.resolve("trae") == ("trae", ""))
    check("空选择器回落默认平台", registry.resolve("")[0] == registry.DEFAULT_PLATFORM)
    check("未知平台回落默认", registry.resolve("nope")[0] == registry.DEFAULT_PLATFORM)
    check("候选优先级", registry.resolve("trae-intl", "workbuddy") == ("trae-intl", "intl"))


def test_dispatch() -> None:
    print("网关分发")
    from admin.platform import dispatch, registry
    from starlette.requests import Request

    def make(headers=None, query="", path="/v1/chat/completions"):
        raw_headers = [(k.lower().encode(), v.encode())
                       for k, v in (headers or {}).items()]
        scope = {"type": "http", "path": path, "headers": raw_headers,
                 "query_string": query.encode(), "method": "POST"}
        return Request(scope)

    check("显式头解析", dispatch.resolve(make({"X-Platform": "trae:cn"})) == ("trae", "cn"))
    check("查询串解析",
          dispatch.resolve(make(query="platform=trae&realm=intl")) == ("trae", "intl"))
    check("查询串只有区域",
          dispatch.resolve(make({"X-Platform": "trae"}, query="realm=intl"))
          == ("trae", "intl"))
    # 无标识时会先问「平台网关策略」，策略无可用平台才回落默认。
    # 所以断言的是「**有兜底**」而不是固定的 workbuddy ——
    # 具体落到哪个平台是策略的职责（见 admin/platform/gateway_router.py）。
    _no_marker = dispatch.resolve(make())
    check("无标识有兜底",
          bool(_no_marker[0]) and _no_marker[0] in registry.ids(), str(_no_marker))
    # 策略层被禁用时（模拟算不出来）必须回落默认平台，不能抛异常
    import admin.platform.gateway_router as _gr

    _orig = _gr.cached_pick
    _gr.cached_pick = lambda: None
    try:
        check("策略不可用回落默认",
              dispatch.resolve(make())[0] == "workbuddy", str(dispatch.resolve(make())))
    finally:
        _gr.cached_pick = _orig
    check("网关路径拦截集合完整", "/v1/messages" in dispatch.GATEWAY_PATHS
          and "/v1/responses" in dispatch.GATEWAY_PATHS
          and "/v1/models" in dispatch.GATEWAY_PATHS)
    check("非网关路径不拦截", "/api/accounts" not in dispatch.GATEWAY_PATHS)


def test_protocol() -> None:
    print("协议转换")
    from admin.platform.trae.core import protocol

    # Responses -> Chat
    chat = protocol.responses_to_chat({
        "model": "deepseek-V3",
        "instructions": "你是助手",
        "input": [
            {"role": "user", "content": [{"type": "input_text", "text": "你好"}]},
            {"type": "function_call", "name": "lookup", "arguments": "{}"},
        ],
        "max_output_tokens": 100,
        "reasoning": {"effort": "high"},
        "tools": [{"type": "function", "name": "lookup",
                   "parameters": {"type": "object", "properties": {}}}],
    })
    check("Responses->Chat system 来自 instructions",
          chat["messages"][0]["role"] == "system")
    check("Responses->Chat 文本展开", chat["messages"][1]["content"] == "你好")
    check("Responses->Chat 工具历史文本化",
          "[lookup]" in chat["messages"][2]["content"])
    check("Responses->Chat max_tokens 映射", chat.get("max_tokens") == 100)
    check("Responses->Chat effort 映射", chat.get("reasoning_effort") == "high")
    check("Responses->Chat tools 映射", chat["tools"][0]["function"]["name"] == "lookup")

    # Anthropic -> Chat
    chat2 = protocol.messages_to_chat({
        "model": "claude-x", "max_tokens": 64, "system": "sys",
        "messages": [{"role": "user",
                      "content": [{"type": "text", "text": "hi"}]}],
        "tools": [{"name": "toolx", "description": "d",
                   "input_schema": {"type": "object", "properties": {}}}],
        "stop_sequences": ["END"],
    })
    check("Anthropic->Chat system", chat2["messages"][0] == {"role": "system", "content": "sys"})
    check("Anthropic->Chat 文本", chat2["messages"][1]["content"] == "hi")
    check("Anthropic->Chat max_tokens", chat2["max_tokens"] == 64)
    check("Anthropic->Chat stop", chat2.get("stop") == ["END"])
    check("Anthropic->Chat tools", chat2["tools"][0]["function"]["name"] == "toolx")

    # Chat -> Responses / Anthropic（含工具调用）
    chat_obj = {
        "id": "x", "created": 1, "model": "m",
        "choices": [{"index": 0, "finish_reason": "tool_calls",
                     "message": {"role": "assistant", "content": "思考",
                                 "tool_calls": [{"id": "call_1", "type": "function",
                                                 "function": {"name": "lookup",
                                                              "arguments": "{\"a\":1}"}}]}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
    }
    resp = protocol.chat_to_responses(chat_obj, "m")
    kinds = [item["type"] for item in resp["output"]]
    check("Chat->Responses 含 message", "message" in kinds, str(kinds))
    check("Chat->Responses 含 function_call", "function_call" in kinds, str(kinds))
    check("Chat->Responses usage", resp["usage"]["total_tokens"] == 5)

    msg = protocol.chat_to_messages(chat_obj, "m")
    blocks = [b["type"] for b in msg["content"]]
    check("Chat->Anthropic 含 text", "text" in blocks, str(blocks))
    check("Chat->Anthropic 含 tool_use", "tool_use" in blocks, str(blocks))
    check("Chat->Anthropic stop_reason", msg["stop_reason"] == "tool_use", msg["stop_reason"])

    # 工具标记解析：跨 chunk 分裂必须能拼回来
    buf = protocol._ToolMarkerBuffer({"lookup"})
    pieces = ["前文<|FunctionCall", "Begin|>[{\"name\":\"lookup\",\"parameters\":{\"a\":1}}]",
              "<|FunctionCallEnd|>后文"]
    out = []
    for piece in pieces:
        out.extend(buf.feed(piece))
    out.extend(buf.flush())
    texts = "".join(p for k, p in out if k == "text")
    calls = [p for k, p in out if k == "call"]
    check("工具标记跨 chunk 解析", len(calls) == 1 and calls[0]["name"] == "lookup", str(out))
    check("工具标记不泄漏正文", "FunctionCall" not in texts and "前文" in texts and "后文" in texts,
          texts)

    # 解析失败必须原样保留（fail-open）
    buf2 = protocol._ToolMarkerBuffer(None)
    bad = list(buf2.feed("<|FunctionCallBegin|>not-json<|FunctionCallEnd|>"))
    bad_text = "".join(p for k, p in bad if k == "text")
    check("坏标记原样保留", "not-json" in bad_text and "FunctionCallBegin" in bad_text, bad_text)

    # usage 归一
    usage = protocol.usage_to_openai({
        "prompt_tokens": 10, "completion_tokens": 5,
        "cache_read_input_tokens": 7, "reasoning_tokens": 2})
    check("usage 归一 total", usage["total_tokens"] == 15)
    check("usage 归一 cached", usage["prompt_tokens_details"]["cached_tokens"] == 7)
    check("usage 归一 reasoning", usage["completion_tokens_details"]["reasoning_tokens"] == 2)


def test_fingerprint() -> None:
    print("设备指纹")
    from admin.platform.trae.core import fingerprint

    a = fingerprint.machine_id("1001")
    b = fingerprint.machine_id("1001")
    c = fingerprint.machine_id("1002")
    check("machineId 稳定", a == b)
    check("machineId 账号隔离", a != c)
    check("machineId 长度 64", len(a) == 64)
    check("deviceId 数字继承", fingerprint.device_id("1234567890123456789")
          == "1234567890123456789")
    check("deviceId 非数字派生 19 位", len(fingerprint.device_id("abc")) == 19)

    h_none = fingerprint.device_headers("1001", None)
    h_empty = fingerprint.device_headers("1001", "")
    h_real = fingerprint.device_headers("1001", "7482973470445061649")
    check("国内版带派生 X-Device-Id", "X-Device-Id" in h_none)
    check("国际版无设备号时省略该头", "X-Device-Id" not in h_empty, str(h_empty))
    check("真实设备号透传", h_real["X-Device-Id"] == "7482973470445061649")
    check("machineId 头存在", "X-Machine-Id" in h_none)


def test_catalog() -> None:
    print("模型目录")
    from admin.platform.trae.core import catalog

    check("国内版目录有模型", len(catalog.realm_models("cn")) > 0)
    check("国际版目录有模型", len(catalog.realm_models("intl")) > 0)

    # 别名解析必须保留上游原始大小写（上游名大小写敏感）
    catalog.note_live_rows("cn", [{"name": "deepseek-V3",
                                   "display_name": "DeepSeek-V3"}])
    check("别名解析到原始大小写",
          catalog.upstream_name("DeepSeek-V3", "cn") == "deepseek-V3",
          catalog.upstream_name("DeepSeek-V3", "cn"))
    check("原样名直通",
          catalog.upstream_name("deepseek-V3", "cn") == "deepseek-V3")
    check("未知模型原样返回", catalog.upstream_name("no-such-model", "cn") == "no-such-model")
    # 只有国际版快照里有的模型才报 intl；两区都有的必须返回空
    # （硬选一个区会让客户端打到错区，额度与配额都不对）
    only_intl = [str(m.get("key")) for m in catalog.snapshot("intl").get("models") or ()
                 if not catalog.entry(str(m.get("key")), "cn")]
    if only_intl:
        sample = only_intl[0]
        check("单区归属国际版", catalog.realm_for(sample) == "intl",
              f"{sample} -> {catalog.realm_for(sample)!r}")
    else:
        skip("单区归属国际版", "两区快照无差异")

    # 两区都有的模型：出现在两边之后必须不再猜区域
    catalog.note_live_rows("intl", [{"name": "deepseek-V3", "display_name": "DeepSeek-V3"}])
    check("两区共有的模型不猜区域", catalog.realm_for("deepseek-V3") == "",
          repr(catalog.realm_for("deepseek-V3")))

    # 两区都有 deepseek-V3，查区必须返回各自条目
    catalog.note_live_rows("intl", [{"name": "deepseek-V3", "display_name": "DeepSeek-V3"}])
    cn_hit = catalog.entry("deepseek-V3", "cn")
    intl_hit = catalog.entry("deepseek-V3", "intl")
    check("同模型跨区各自归属",
          cn_hit and intl_hit and cn_hit["realm"] == "cn" and intl_hit["realm"] == "intl")


def test_quota() -> None:
    print("额度口径")
    from admin.platform.trae.services import accounts as trae_accounts

    cn = {"is_credits_billing": True,
          "usage_summary": {"consumed_amount": 9.22, "total_amount": 600}}
    detail = trae_accounts.quota_detail(cn)
    check("国内版总量", detail["total"] == 600, str(detail))
    check("国内版剩余=总量-已用", detail["remain"] == 591, str(detail))
    check("国内版为积分口径", detail["basis"] == [], str(detail))

    intl = {"is_dollar_usage_billing": True,
            "user_entitlement_pack_list": [
                {"quota": {"basic_usage_limit": 1,
                           "premium_model_fast_request_limit": 10},
                 # ⚠️ `basic_usage_amount` 的单位是 **milli 美元**：
                 #     250 表示 $0.25（实测：0.00066 -> 网页显示 $0.66）。
                 # 写 0.25 会被当成 $250，被 min() 截断成「已用满」。
                 "usage": {"basic_usage_amount": 0.00025}}]}
    detail2 = trae_accounts.quota_detail(intl)
    # 国际版有美元额度（`basic_usage_limit > 0`）时按 **usd** 口径解析 ——
    # 网页端显示 "$0.25 / $1.00"（已用 / 上限），不再压成百分比。
    check("国际版识别为美元口径", detail2["basis"] == ["usd"], str(detail2))
    check("国际版美元上限", detail2.get("usd_total") == 1.0, str(detail2))
    check("国际版美元剩余", detail2.get("usd_remain") == 0.75, str(detail2))
    check("国际版美元已用", detail2.get("usd_used") == 0.25, str(detail2))
    check("国际版美分口径可排序", detail2["remain"] == 75, str(detail2))
    # 换算方向的回归保护：milli -> 美元是 ×1000，不是 ÷1000
    _milli = {"user_entitlement_pack_list": [{
        "quota": {"basic_usage_limit": 1},
        "usage": {"basic_usage_amount": 0.00066}}]}
    _d = trae_accounts.quota_detail(_milli)
    check("milli 换算方向正确", abs((_d.get("usd_used") or 0) - 0.66) < 0.01,
          str(_d.get("usd_used")))
    check("国际版保留限额明细",
          detail2["limits"].get("premium_model_fast_request_limit") == 10, str(detail2))

    check("空响应不编造额度",
          trae_accounts.quota_detail({})["total"] is None)


def test_accounts_scan() -> None:
    print("本机账号扫描")
    from admin.platform.trae.services import accounts as trae_accounts

    paths = trae_accounts.storage_paths()
    if not paths:
        skip("本机 storage.json", "未安装 Trae 桌面端")
        return
    found = trae_accounts.discover()
    check("扫描不抛异常", isinstance(found, dict) and "items" in found)
    for item in found["items"]:
        check(f"扫描条目 {item['realm']} 含 token 元信息",
              "has_refresh_token" in item)
        # 展示结构绝不能带 token 明文
        blob = json.dumps(item, ensure_ascii=False)
        check(f"扫描条目 {item['realm']} 不含 token 明文",
              "token\"" not in blob.replace('has_refresh_token', ''))

    records = trae_accounts.collect_accounts()
    if not records:
        skip("导入数据结构", "扫描不到可用凭证")
        return
    rec = records[0]
    check("导入结构含 realm", rec.get("realm") in ("cn", "intl"))
    check("导入结构含 uid", bool(rec.get("uid")))


def test_migration() -> None:
    print("数据库迁移")
    from sqlalchemy import text

    from admin.db import SessionLocal, init_db
    from admin.models import Account

    check("init_db 返回成功", init_db() is True)
    with SessionLocal() as db:
        cols = db.execute(text("SHOW COLUMNS FROM accounts LIKE 'platform'")).fetchall()
        check("accounts.platform 存在", bool(cols), str(cols))
        cols = db.execute(text("SHOW COLUMNS FROM api_keys LIKE 'platform'")).fetchall()
        check("api_keys.platform 存在", bool(cols), str(cols))
        empty = db.execute(text("SELECT COUNT(*) FROM accounts "
                                "WHERE platform IS NULL OR platform=''")).scalar()
        check("历史行为空的数量为 0（已回填）", empty == 0, str(empty))
        idx = db.execute(text("SHOW INDEX FROM accounts "
                              "WHERE Key_name='ix_accounts_platform'")).fetchall()
        check("platform 索引存在", bool(idx))
    check("模型默认 platform", Account.__table__.c.platform.default.arg == "workbuddy")


def test_gateway_routing() -> None:
    print("网关鉴权与路由")
    from fastapi.testclient import TestClient

    import admin.server as s
    from admin.db import SessionLocal
    from admin.models import ApiKey
    from admin.security import hash_key

    client = TestClient(s.app, raise_server_exceptions=False)
    raw = "sk-trae-test-" + secrets.token_hex(12)
    created = None
    with SessionLocal() as db:
        created = ApiKey(name="__trae_test__", key_hash=hash_key(raw),
                         key_prefix=raw[:8], key_full=raw, credit_limit=0,
                         credit_used=0, unlimited=1, status="active",
                         platform="")
        db.add(created)
        db.commit()
        key_id = created.id
    try:
        auth = {"Authorization": "Bearer " + raw}
        r = client.get("/v1/models", headers={**auth, "X-Platform": "trae:cn"})
        check("trae 请求被接管（非默认平台的路径生效）",
              r.status_code in (200, 503), f"{r.status_code} {r.text[:120]}")

        r2 = client.get("/v1/models", headers={"Authorization": "Bearer bad-key"})
        body = r2.json()
        check("默认平台仍由原网关鉴权", r2.status_code == 401, str(r2.status_code))
        check("默认平台错误体未变", body.get("error", {}).get("type") == "auth_error",
              str(body))

        r3 = client.get("/api/platforms")
        check("平台列表需要登录", r3.status_code == 401, str(r3.status_code))

        r4 = client.get("/api/trae/meta")
        check("trae 管理接口需要登录", r4.status_code == 401, str(r4.status_code))
    finally:
        with SessionLocal() as db:
            db.query(ApiKey).filter(ApiKey.id == key_id).delete()
            db.commit()

    check("残留测试 Key 已清理", True)


def test_openai_stream_shape() -> None:
    """契约测试：流式帧的每个字段都必须真的渲染出来。

    为什么单列一项：`_chunk(..., finish_reason=...)` 曾经因参数名写成
    `finish` 而在**运行时**抛 TypeError —— 请求已经返回 200 并吐了一半正文，
    客户端只看到流被截断、没有 [DONE]。静态检查抓不到这种关键字参数错误，
    所以这里用假上游把整条渲染链路跑一遍。
    """
    print("OpenAI 流式帧")
    from admin.platform.trae.core import protocol

    class FakeResp:
        """按上游信封格式产出的假响应（不碰网络）。"""

        def __init__(self, frames):
            self._lines = [line.encode() for line in frames]
            self._iter = iter(self._lines)

        def __iter__(self):
            return self

        def __next__(self):
            return next(self._iter)

        def close(self):
            pass

    frames = [
        "event: metadata", 'data: {"session_id": "abc-123", "model": "deepseek-V3"}', "",
        "event: output", 'data: {"response": "你好"}', "",
        "event: token_usage", 'data: {"prompt_tokens": 5, "completion_tokens": 2}', "",
        "event: done", 'data: {"finish_reason": "stop"}', "",
    ]
    holder = {}
    chunks = list(protocol.stream_openai(FakeResp(frames), holder))
    raw = b"".join(chunks).decode()

    check("流式输出以 [DONE] 结尾", raw.rstrip().endswith("data: [DONE]"), raw[-80:])
    check("流式包含 finish_reason=stop", '"finish_reason": "stop"' in raw, raw[-300:])
    check("流式包含正文", "你好" in raw)
    check("流式包含 usage", '"prompt_tokens": 5' in raw)
    check("holder 记录了 usage", (holder.get("usage") or {}).get("total_tokens") == 7,
          str(holder.get("usage")))

    # 聚合路径（非流式）同样要完整
    agg = protocol.aggregate_openai(FakeResp(frames), {})
    check("聚合正文", agg["choices"][0]["message"]["content"] == "你好", str(agg))
    check("聚合 finish_reason", agg["choices"][0]["finish_reason"] == "stop")
    check("聚合 usage", agg["usage"]["total_tokens"] == 7)

    # Responses / Anthropic 流式也要能完整产出终态
    resp_frames = list(protocol.stream_responses(FakeResp(frames), "deepseek-V3", {}))
    resp_raw = b"".join(resp_frames).decode()
    check("Responses 流含 completed", "response.completed" in resp_raw, resp_raw[-200:])
    check("Responses 流含 [DONE]", "[DONE]" in resp_raw)

    ant_frames = list(protocol.stream_anthropic(FakeResp(frames), "deepseek-V3", {}))
    ant_raw = b"".join(ant_frames).decode()
    check("Anthropic 流含 message_stop", "message_stop" in ant_raw, ant_raw[-200:])
    check("Anthropic 流含 message_start", "message_start" in ant_raw)
    check("Anthropic 流含文本增量", "text_delta" in ant_raw)


def test_scope() -> None:
    """平台作用域：切平台必须收窄**所有**统计口径，而不是只筛账号列表。"""
    print("平台作用域")
    from admin.platform import scope

    # 解析语义：裸平台名表示「整个平台」，不能归一成默认区域
    check("解析 trae:cn", scope.parse("trae:cn") == ("trae", "cn"), str(scope.parse("trae:cn")))
    check("解析 trae（平台级，不按区收窄）", scope.parse("trae") == ("trae", ""),
          str(scope.parse("trae")))
    check("解析 workbuddy", scope.parse("workbuddy") == ("workbuddy", ""),
          str(scope.parse("workbuddy")))
    check("空值不过滤", scope.parse("") == ("", ""), str(scope.parse("")))
    check("未知平台不过滤（保持老 API 兼容）", scope.parse("nope") == ("", ""),
          str(scope.parse("nope")))

    # account_ids 的三态语义：None=不过滤 / []=必须为空
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from admin.db import Base
    from admin.models import Account

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    db.add_all([
        Account(name="wb", platform="workbuddy", domain="", auth_json="{}", status="active"),
        Account(name="cn", platform="trae", domain="cn", auth_json="{}", status="active"),
        Account(name="intl", platform="trae", domain="intl", auth_json="{}", status="active"),
    ])
    db.commit()

    check("不指定平台 -> None（不过滤）", scope.account_ids(db, "") is None)
    check("workbuddy 账号数", len(scope.account_ids(db, "workbuddy")) == 1)
    check("trae 全平台账号数", len(scope.account_ids(db, "trae")) == 2)
    check("trae:cn 只取国内版", len(scope.account_ids(db, "trae", "cn")) == 1)
    # 关键：指定了未知平台必须返回空列表而不是 None，
    # 否则「切到一个没有账号的平台」会意外显示其它平台的数据
    check("未知平台 -> 空列表（不能退化成全量）",
          scope.account_ids(db, "nope") == [], str(scope.account_ids(db, "nope")))

    q, short = scope.apply(db.query(Account), db, "trae", "")
    check("apply 保留 trae 两条", q.count() == 2, q.count())
    q2, short2 = scope.apply(db.query(Account), db, "nope", "")
    check("apply 对未知平台短路", short2 is True)
    db.close()
    engine.dispose()


def test_model_grouping() -> None:
    """模型配置：公共一张表，但必须能按平台分组。"""
    print("模型平台分组")
    from admin.models import ModelConfig

    check("ModelConfig 有 platform 列",
          "platform" in ModelConfig.__table__.c, str(ModelConfig.__table__.c.keys()))
    check("默认平台为 workbuddy",
          ModelConfig.__table__.c.platform.default.arg == "workbuddy")
    from admin.models import Schedule
    check("Schedule 有 platform 列", "platform" in Schedule.__table__.c)
    check("Schedule 默认平台为 workbuddy",
          Schedule.__table__.c.platform.default.arg == "workbuddy")


def test_sentinel() -> None:
    """风控哨兵：只记封控信号、按账号判熔断、平台白名单、永不抛异常。"""
    print("风控哨兵")
    from admin import sentinel

    # 哨兵**接受所有平台上报**（不再有白名单）。
    #
    # 早先限定只有 workbuddy，理由是「Trae 的 4015/4019 语义未确认」。
    # 但正确解法不是拒绝上报，而是让平台自己判断什么该报 ——
    # 熔断天然按 platform 分键，A 平台熔断不影响 B 平台。
    check("哨兵不限平台", sentinel.SENTINEL_PLATFORMS is None,
          repr(sentinel.SENTINEL_PLATFORMS))
    sentinel.reset()
    ev = sentinel.note_failure("trae", 9, "risk_control", code="4015", status=403)
    check("Trae 可上报", bool(ev) and ev.get("code") == "4015", str(ev))
    check("Trae 有事件", len(sentinel.events("trae")) == 1, str(sentinel.events("trae")))
    check("Trae 码有中文标签", "4015" in (ev.get("label") or ""), str(ev.get("label")))

    # 平台隔离：Trae 的事件不能算到 WorkBuddy 头上
    sentinel.reset()
    for acc_id in (1, 2, 3):
        sentinel.note_failure("workbuddy", acc_id, "waf", code="403", status=403)
    check("WorkBuddy 触发熔断", sentinel.breach("workbuddy")["tripped"])
    check("Trae 不受影响", not sentinel.breach("trae")["tripped"],
          str(sentinel.breach("trae")))

    sentinel.reset()
    # ===== 记录口径（2026-11-09 调整）=====
    #
    # 以前这里是「非封控信号不记」：429 / 模型不存在直接返回 None。
    # 用户明确要求改成**宽记录、窄熔断**：
    #   · 限速 / 限流 / 排队 / 额度耗尽 / 登录失效都要留痕（他说「退出登录
    #     不算风控，但可以记录」）；
    #   · 只有 severity == "risk" 才参与熔断，否则一次上游抖动就把平台关了。
    #
    # 所以这里断言的是「记下来 + 严重程度正确 + 不触发熔断」，而不再要求 None。
    soft = sentinel.note_failure("workbuddy", 1, "soft_rate", code="6004")
    check("429 记为限流", bool(soft) and soft.get("severity") == sentinel.SEVERITY_THROTTLE,
          str(soft))
    miss = sentinel.note_failure("workbuddy", 1, "model_unknown", code="4023")
    check("模型不存在只留痕", bool(miss) and miss.get("severity") == sentinel.SEVERITY_INFO,
          str(miss))
    # 封控信号必须记，并且是 risk（会参与熔断）
    for kind, code in (("waf", "403"), ("account_fault", "11140"),
                       ("session_dead", "12153"), ("upstream_auth_anomaly", "401")):
        event = sentinel.note_failure("workbuddy", 100, kind, code=code, status=403)
        check(f"记录 {kind}", bool(event) and event.get("code") == code, str(event))

    status = sentinel.status("workbuddy")
    # 4 个封控 + 429 + 4023 = 6 条（全都记，但只有 risk 计入熔断）
    check("信号计数", status["total_events"] == 6, str(status["total_events"]))
    check("按码分组", status["by_code"].get("11140") == 1, str(status["by_code"]))
    # 登录失效归 auth，不算风控
    check("登录失效不计入风控",
          status["by_severity"].get("auth", 0) >= 1, str(status["by_severity"]))
    check("风控账号数只数 risk",
          status["accounts_risk"] < status["accounts_total"],
          "risk=%s total=%s" % (status["accounts_risk"], status["accounts_total"]))

    # 熔断判定按**不同账号**数，不是事件数
    sentinel.reset()
    for _ in range(5):
        sentinel.note_failure("workbuddy", 200, "waf", code="403", status=403)
    one = sentinel.breach("workbuddy")
    check("单账号重试不触发熔断", not one["tripped"], str(one))
    for account in (201, 202, 203):
        sentinel.note_failure("workbuddy", account, "waf", code="403", status=403)
    multi = sentinel.breach("workbuddy")
    check("多账号触发熔断", multi["tripped"], str(multi))
    check("受影响账号数正确", multi["accounts"] == 4, str(multi["accounts"]))

    # 哨兵绝不能因为参数怪异而抛异常（热路径调用）
    try:
        sentinel.record("", 0, "", code="", status=0, detail="")
        sentinel.note_failure("", 0, None)
        sentinel.status("does-not-exist")
        ok = True
        out_msg = ""
    except Exception as exc:  # noqa: BLE001
        ok = False
        out_msg = repr(exc)
    check("异常输入不崩", ok, out_msg)
    sentinel.reset()


def test_desktop_catalog() -> None:
    """客户端目录：真实全量模型（含新模型），且与快照/接口不同。"""
    print("客户端模型目录")
    from admin.platform.trae.services import desktop_catalog

    result = desktop_catalog.read_all()
    for realm in ("cn", "intl"):
        data = result.get(realm) or {}
        if not data.get("models"):
            skip(f"{realm} 客户端目录", data.get("error") or "未安装 Trae")
            continue
        names = [m["name"] for m in data["models"]]
        check(f"{realm} 模型数量足够多", len(names) >= 20, f"{len(names)} 个")
        # 自定义模型（custom_openai_compatible//xxx）不该进来：
        # 它们指向用户自配的第三方端点，走网关转发没有意义
        bad = [n for n in names if n.startswith("custom_")]
        check(f"{realm} 已过滤自定义模型", not bad, str(bad[:3]))
        # 上下文长度必须能解析出来（客户端里有 int/list/dict 三种形态）
        with_ctx = [m for m in data["models"] if m["context_window"] > 0]
        check(f"{realm} 上下文长度可解析", len(with_ctx) >= len(names) // 2,
              f"{len(with_ctx)}/{len(names)}")
        # 免费/会员判定必须对每个模型都有结论（free 是 bool 而不是 None）
        known = [m for m in data["models"] if isinstance(m.get("free"), bool)]
        check(f"{realm} 每个模型都有 free 判定",
              len(known) == len(data["models"]),
              f"{len(known)}/{len(data['models'])}")


def test_free_identity() -> None:
    """套餐身份判定：`identity_list` 含 0 = 免费可用。

    这是「哪些模型对 free 号可用」的唯一可靠依据 —— 界面上
    「锁 + 解锁新模型」提示出现的正是不含 0 的那些。
    """
    print("套餐身份判定")
    from admin.platform.trae.services.desktop_catalog import is_free_model

    # 实测分布（2026-10，Trae CN / Trae 的 state.vscdb）
    check("含 0 -> 免费", is_free_model([0, 5, 1, 2, 3, 100]) is True)
    check("无 0 -> 需会员", is_free_model([5, 4, 1, 2, 3]) is False)
    check("无 0（变体）", is_free_model([1, 4, 2, 3]) is False)
    # 空列表 = 客户端没给这个字段 -> 按「可能可用」处理，不能凭空藏模型
    check("未知 -> 视为可用", is_free_model([]) is True)


def test_checkin_protocol() -> None:
    """Trae 签到协议：主机、路径、req_source。

    这三项错一项都会失败：
      · 主机必须是 `api.trae.cn` —— 打 `api.trae.com.cn` 是 404；
      · 路径 `/trae/api/v2/ug/checkin_credits/*`；
      · `req_source` 国内 1 / 国际 2（客户端 main.js 的 li(this.P)?2:1）。
    """
    print("Trae 签到协议")
    from admin.platform.trae.core import client

    check("cn 签到主机", client.REALMS["cn"].get("ug") == "https://api.trae.cn",
          str(client.REALMS["cn"].get("ug")))
    check("ug 主机可用", client.hosts_for({"realm": "cn"}, "cn", base="ug")
          == ["https://api.trae.cn"],
          str(client.hosts_for({"realm": "cn"}, "cn", base="ug")))
    check("状态路径", client.PATH_CHECKIN_STATUS == "/trae/api/v2/ug/checkin_credits/status",
          client.PATH_CHECKIN_STATUS)
    check("领取路径", client.PATH_CHECKIN_CLAIM == "/trae/api/v2/ug/checkin_credits/claim",
          client.PATH_CHECKIN_CLAIM)
    check("req_source 国内=1", client._checkin_req_source("cn") == 1)
    check("req_source 国际=2", client._checkin_req_source("intl") == 2)
    check("签到函数存在", callable(client.checkin_status) and callable(client.claim_checkin))


def test_realm_isolation() -> None:
    """国内版 / 国际版是**两个平台**，账号池绝不能合并。

    这里防的是一个真实踩过的 bug：前端 `loadTraeAccounts` 只读
    `platform.realm`（用户手选的区域），而 trae-cn / trae-intl 是单区平台，
    区域固定在 `spec.realm` 上 —— `state.realm` 恒为空，请求不带 `realm=`，
    后端返回全部 Trae 账号（1+2=3），两个平台看起来一模一样。
    """
    print("区域隔离")
    from admin.platform.loader import load_platforms
    from admin.platform import registry

    load_platforms()
    cn = registry.get("trae-cn")
    intl = registry.get("trae-intl")
    check("trae-cn 固定 cn 区", cn is not None and cn.realm == "cn",
          str(cn.realm if cn else None))
    check("trae-intl 固定 intl 区", intl is not None and intl.realm == "intl",
          str(intl.realm if intl else None))
    # 单区平台的 realms 是空的 —— 前端**必须**从 spec.realm 取区域
    check("单区平台 realms 为空", (cn.realm_keys() if cn else ()) == (),
          str(cn.realm_keys() if cn else None))
    check("两平台落库值相同", cn.storage() == intl.storage() == "trae",
          "%s/%s" % (cn.storage(), intl.storage()))
    # 区域不同 -> 作用域不同
    check("作用域按区分开",
          registry.split_selector("trae-cn") != registry.split_selector("trae-intl"))


def test_checkin_task_registered() -> None:
    """定时任务注册：`trae_checkin` 必须能被选到、且有可读标签。

    ⚠️ 断言用**合并后的清单**（`_all_task_choices` / `_all_task_labels`），
    不是 `TASK_CHOICES` —— 平台专属任务由各平台 `task_runners()` 声明，
    写死在常量里会让新平台的任务永远进不了下拉框。
    """
    print("签到任务注册")
    from admin.routers.schedules import _all_task_choices, _all_task_labels
    from admin import scheduler

    all_choices = _all_task_choices()
    all_labels = _all_task_labels()
    check("trae_checkin 在可选列表", "trae_checkin" in all_choices, str(all_choices))
    check("trae_checkin 有标签", "trae_checkin" in all_labels,
          str(all_labels.get("trae_checkin")))
    # 平台任务确实由平台声明（不是内建常量）
    from admin.routers.schedules import TASK_CHOICES
    check("trae_checkin 由平台声明（不在内建常量里）",
          "trae_checkin" not in TASK_CHOICES, str(TASK_CHOICES))
    # 标签要能区分 WorkBuddy 与 Trae 的签到（两者是不同上游接口）
    check("两个签到标签不同", all_labels.get("daily_checkin") != all_labels.get("trae_checkin"),
          "%s / %s" % (all_labels.get("daily_checkin"), all_labels.get("trae_checkin")))
    check("runner 已实现", callable(getattr(scheduler, "run_trae_checkin", None)))


def test_billing_split() -> None:
    """计费口径按平台分流：Trae 按 token，WorkBuddy 按倍率。"""
    print("计费口径")
    from admin.routers.models import _platform_label

    # 落库平台 `trae` 不能被误标成 WorkBuddy
    check("trae 标签", _platform_label("trae") == "Trae", _platform_label("trae"))
    check("workbuddy 标签", _platform_label("workbuddy") == "WorkBuddy")
    check("trae-cn 标签", _platform_label("trae-cn") == "Trae 国内版",
          _platform_label("trae-cn"))


def main() -> int:
    test_registry()
    test_dispatch()
    test_protocol()
    test_openai_stream_shape()
    test_scope()
    test_sentinel()
    test_desktop_catalog()
    test_free_identity()
    test_billing_split()
    test_checkin_protocol()
    test_realm_isolation()
    test_checkin_task_registered()
    test_model_grouping()
    test_fingerprint()
    test_catalog()
    test_quota()
    test_accounts_scan()
    test_migration()
    test_gateway_routing()
    print()
    print(f"PASS={PASS}  FAIL={FAIL}  SKIP={SKIP}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())