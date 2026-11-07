#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""网关对话 Harness：三协议 × 工具调用 × 用量字段，一次跑完。

## 为什么需要它

「能回一句话」早就不算验收了。新平台接进来，必须同时确认：

  1. **真工具调用**：模型返回 `tool_calls` / `tool_use` / `function_call`，
     参数是合法 JSON；把工具结果喂回去之后，模型能继续给出最终答复
     （不是重复喊工具、也不是说「我没有工具」）。
  2. **三协议一致**：`/v1/chat/completions`、`/v1/messages`、`/v1/responses`
     都要能跑通同一条工具链，输出结构符合各自协议。
  3. **用量真的回来**：input/output/total tokens 必须 > 0；缓存命中字段
     （OpenAI `prompt_tokens_details.cached_tokens`、Anthropic
     `cache_read_input_tokens`、Responses `input_tokens_details.cached_tokens`）
     有就报、没有就标 `MISSING`，**绝不伪造**。

## 用法

    python admin/platform/skill/scripts/harness_chat.py \
        --platform wps-lingxi \
        --model deepseek-v4.1-flash-wps-lingxi \
        --base http://127.0.0.1:8790 \
        --user admin --password '***'

`--key` 可直接给现成 API Key；不给就用 `--user/--password` 登录后台建一个临时
Key，跑完自动删掉。所有断言都打在退出码上：全绿 0，有失败 1。
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SYSTEM_PROMPT = (
    "你是一名严谨的项目助理。回答必须简短。"
    "涉及天气等外部信息时必须调用提供的工具，不要凭记忆编造；"
    "拿到工具结果后直接用结果作答。"
)

TOOLS = [
    {
        "name": "get_weather",
        "description": "查询指定城市的实时天气",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "城市名，例如 北京"},
                "unit": {"type": "string", "enum": ["c", "f"]},
            },
            "required": ["city"],
        },
    },
    {
        "name": "summarize_rows",
        "description": "对一组数值做汇总（条数/合计/均值）",
        "parameters": {
            "type": "object",
            "properties": {"rows": {"type": "array", "items": {"type": "number"}}},
            "required": ["rows"],
        },
    },
]

PROMPT = "帮我查一下北京现在的天气，用摄氏度表示。查完给一句结论。"

RESULTS: list[dict] = []


def record(protocol: str, name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append({"protocol": protocol, "check": name, "ok": ok,
                    "detail": detail})
    flag = "ok  " if ok else "FAIL"
    print("  [%s] %-34s %s" % (flag, name, detail))


class Gateway:
    def __init__(self, base: str, platform: str) -> None:
        self.base = base.rstrip("/")
        self.platform = platform

    def post(self, path: str, payload: dict, key: str, timeout: int = 240):
        body = json.dumps(payload).encode()
        headers = {"Content-Type": "application/json",
                   "Authorization": "Bearer " + key,
                   "X-Platform": self.platform}
        req = urllib.request.Request(self.base + path, data=body, method="POST",
                                     headers=headers)
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
                return resp.status, raw, round(time.perf_counter() - started, 2)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            return exc.code, raw, round(time.perf_counter() - started, 2)


def _usage_of(data: dict) -> dict:
    usage = data.get("usage") or {}
    prompt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    completion = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    total = int(usage.get("total_tokens") or (prompt + completion))
    cached = 0
    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict):
        cached = int(details.get("cached_tokens") or 0)
    cached = cached or int(usage.get("cache_read_input_tokens") or 0)
    inner = usage.get("input_tokens_details")
    if isinstance(inner, dict):
        cached = cached or int(inner.get("cached_tokens") or 0)
    return {"prompt": prompt, "completion": completion, "total": total,
            "cached": cached, "raw": usage}


def check_usage(protocol: str, data: dict) -> dict:
    usage = _usage_of(data)
    record(protocol, "usage.input_tokens > 0", usage["prompt"] > 0,
           "prompt=%d" % usage["prompt"])
    record(protocol, "usage.output_tokens > 0", usage["completion"] > 0,
           "completion=%d" % usage["completion"])
    record(protocol, "usage.total_tokens > 0", usage["total"] > 0,
           "total=%d" % usage["total"])
    if usage["cached"] > 0:
        record(protocol, "usage.cached_tokens 命中", True,
               "cached=%d" % usage["cached"])
    else:
        # 0 是合法值（这次没命中缓存），但字段必须存在，否则说明没透传
        has_field = ("cached_tokens" in json.dumps(usage["raw"]) or
                     "cache_read_input_tokens" in json.dumps(usage["raw"]))
        record(protocol, "usage.cached_tokens 字段存在", has_field,
               "本次未命中（0）" if has_field else "字段缺失")
    return usage


def chat_round(gw: Gateway, model: str, key: str) -> None:
    proto = "chat"
    print("\n=== /v1/chat/completions ===")
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": PROMPT}],
        "tools": [{"type": "function",
                   "function": {"name": t["name"], "description": t["description"],
                                "parameters": t["parameters"]}} for t in TOOLS],
        "tool_choice": "auto",
        "max_tokens": 256,
        "stream": False,
    }
    status, raw, secs = gw.post("/v1/chat/completions", payload, key)
    record(proto, "HTTP 200", status == 200, "status=%s %.2fs" % (status, secs))
    if status != 200:
        print("    body:", raw[:300])
        return
    data = json.loads(raw)
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    calls = message.get("tool_calls") or []
    record(proto, "返回 tool_calls", bool(calls), "count=%d" % len(calls))
    city = ""
    if calls:
        fn = calls[0].get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
            city = str(args.get("city") or "")
            record(proto, "工具名与参数可解析",
                   fn.get("name") == "get_weather" and bool(city),
                   "%s %s" % (fn.get("name"), fn.get("arguments")))
        except Exception:  # noqa: BLE001
            record(proto, "工具名与参数可解析", False, str(fn.get("arguments"))[:80])
    check_usage(proto, data)

    if not calls:
        return
    # 第二轮：把工具结果喂回去，必须给出最终答复
    follow = {
        "model": model,
        "messages": payload["messages"] + [
            {"role": "assistant", "content": message.get("content") or "",
             "tool_calls": calls},
            {"role": "tool", "tool_call_id": calls[0].get("id") or "call_0",
             "content": json.dumps({"city": city or "北京", "temp_c": 7,
                                    "condition": "晴", "source": "harness-stub"},
                                   ensure_ascii=False)},
        ],
        "tools": payload["tools"],
        "max_tokens": 256,
        "stream": False,
    }
    status, raw, secs = gw.post("/v1/chat/completions", follow, key)
    record(proto, "工具结果回灌 200", status == 200, "status=%s %.2fs" % (status, secs))
    if status != 200:
        print("    body:", raw[:300])
        return
    data = json.loads(raw)
    text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    record(proto, "回灌后给出最终答复", bool(text.strip()), text[:60].replace("\n", " "))
    record(proto, "答复未声称「没有工具」",
           "没有可用的" not in text and "没有工具" not in text, "")


def messages_round(gw: Gateway, model: str, key: str) -> None:
    proto = "messages"
    print("\n=== /v1/messages ===")
    payload = {
        "model": model,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": PROMPT}],
        "tools": [{"name": t["name"], "description": t["description"],
                   "input_schema": t["parameters"]} for t in TOOLS],
        "max_tokens": 256,
    }
    status, raw, secs = gw.post("/v1/messages", payload, key)
    record(proto, "HTTP 200", status == 200, "status=%s %.2fs" % (status, secs))
    if status != 200:
        print("    body:", raw[:300])
        return
    data = json.loads(raw)
    blocks = data.get("content") or []
    uses = [b for b in blocks if b.get("type") == "tool_use"]
    texts = [b.get("text") or "" for b in blocks if b.get("type") == "text"]
    record(proto, "返回 tool_use 块", bool(uses),
           "count=%d stop=%s" % (len(uses), data.get("stop_reason")))
    city = ""
    if uses:
        city = str((uses[0].get("input") or {}).get("city") or "")
        record(proto, "tool_use.input 是对象",
               isinstance(uses[0].get("input"), dict) and bool(city),
               json.dumps(uses[0].get("input"), ensure_ascii=False)[:80])
    record(proto, "stop_reason=tool_use", data.get("stop_reason") == "tool_use",
           str(data.get("stop_reason")))
    check_usage(proto, data)

    if not uses:
        return
    follow = {
        "model": model,
        "system": SYSTEM_PROMPT,
        "max_tokens": 256,
        "tools": payload["tools"],
        "messages": [
            {"role": "user", "content": PROMPT},
            {"role": "assistant", "content": blocks},
            {"role": "user", "content": [{
                "type": "tool_result",
                "tool_use_id": uses[0].get("id") or "toolu_0",
                "content": json.dumps({"city": city or "北京", "temp_c": 7,
                                       "condition": "晴"}), }]},
        ],
    }
    status, raw, secs = gw.post("/v1/messages", follow, key)
    record(proto, "工具结果回灌 200", status == 200, "status=%s %.2fs" % (status, secs))
    if status != 200:
        print("    body:", raw[:300])
        return
    data = json.loads(raw)
    text = "".join(b.get("text") or "" for b in (data.get("content") or [])
                   if b.get("type") == "text")
    record(proto, "回灌后给出最终答复", bool(text.strip()), text[:60].replace("\n", " "))


def responses_round(gw: Gateway, model: str, key: str) -> None:
    proto = "responses"
    print("\n=== /v1/responses ===")
    payload = {
        "model": model,
        "instructions": SYSTEM_PROMPT,
        "input": PROMPT,
        "tools": [{"type": "function", "name": t["name"],
                   "description": t["description"],
                   "parameters": t["parameters"]} for t in TOOLS],
        "max_output_tokens": 256,
    }
    status, raw, secs = gw.post("/v1/responses", payload, key)
    record(proto, "HTTP 200", status == 200, "status=%s %.2fs" % (status, secs))
    if status != 200:
        print("    body:", raw[:300])
        return
    data = json.loads(raw)
    output = data.get("output") or []
    calls = [item for item in output if item.get("type") == "function_call"]
    record(proto, "返回 function_call", bool(calls), "count=%d" % len(calls))
    args = {}
    if calls:
        try:
            args = json.loads(calls[0].get("arguments") or "{}")
        except Exception:  # noqa: BLE001
            args = {}
        record(proto, "function_call.arguments 可解析", bool(args),
               json.dumps(args, ensure_ascii=False)[:80])
    check_usage(proto, data)

    if not calls:
        return
    follow = {
        "model": model,
        "instructions": SYSTEM_PROMPT,
        "max_output_tokens": 256,
        "tools": payload["tools"],
        "input": [
            {"role": "user", "content": PROMPT},
            {"type": "function_call", "call_id": calls[0].get("call_id") or "call_0",
             "name": calls[0].get("name") or "get_weather",
             "arguments": calls[0].get("arguments") or "{}"},
            {"type": "function_call_output",
             "call_id": calls[0].get("call_id") or "call_0",
             "output": json.dumps({"city": args.get("city") or "北京",
                                   "temp_c": 7, "condition": "晴"})},
        ],
    }
    status, raw, secs = gw.post("/v1/responses", follow, key)
    record(proto, "工具结果回灌 200", status == 200, "status=%s %.2fs" % (status, secs))
    if status != 200:
        print("    body:", raw[:300])
        return
    data = json.loads(raw)
    text = ""
    for item in data.get("output") or []:
        if item.get("type") == "message":
            text += "".join(part.get("text") or "" for part in
                            (item.get("content") or [])
                            if part.get("type") == "output_text")
    record(proto, "回灌后给出最终答复", bool(text.strip()), text[:60].replace("\n", " "))


def admin_client(base: str, user: str, password: str):
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar))
    data = urllib.parse.urlencode({"username": user, "password": password}).encode()
    opener.open(urllib.request.Request(
        base + "/api/login", data=data, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"}))
    token = next((c.value for c in jar if c.name == "wb_csrf"), "")

    def call(method: str, path: str, payload=None):
        body = json.dumps(payload).encode() if payload is not None else None
        headers = {"X-CSRF-Token": token}
        if body:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(base + path, data=body, method=method,
                                     headers=headers)
        with opener.open(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))

    return call


def main() -> int:
    parser = argparse.ArgumentParser(description="网关三协议 Harness")
    parser.add_argument("--base", default="http://127.0.0.1:8790")
    parser.add_argument("--platform", required=True, help="平台 id，如 wps-lingxi")
    parser.add_argument("--model", required=True, help="带平台后缀的模型 id")
    parser.add_argument("--key", default="", help="现成 API Key（可省）")
    parser.add_argument("--user", default="", help="后台用户名（无 --key 时用）")
    parser.add_argument("--password", default="", help="后台密码")
    parser.add_argument("--keep-key", action="store_true", help="保留临时 Key")
    args = parser.parse_args()

    key = args.key
    temp_key_id = None
    call = None
    if not key:
        if not (args.user and args.password):
            print("需要 --key，或 --user/--password 建临时 Key")
            return 2
        call = admin_client(args.base, args.user, args.password)
        created = call("POST", "/api/keys",
                       {"name": "harness-%s-%s" % (args.platform,
                                                   int(time.time())),
                        "unlimited": True})
        key = created.get("key") or ""
        temp_key_id = created.get("id")
        if not key:
            print("建临时 Key 失败")
            return 2

    print("平台:", args.platform, "| 模型:", args.model)
    gw = Gateway(args.base, args.platform)
    try:
        chat_round(gw, args.model, key)
        messages_round(gw, args.model, key)
        responses_round(gw, args.model, key)
    finally:
        if temp_key_id and not args.keep_key and call:
            try:
                call("DELETE", "/api/keys/%s" % temp_key_id)
            except Exception:  # noqa: BLE001
                pass

    total = len(RESULTS)
    failed = [item for item in RESULTS if not item["ok"]]
    print("\n" + "=" * 62)
    for proto in ("chat", "messages", "responses"):
        rows = [item for item in RESULTS if item["protocol"] == proto]
        bad = [item for item in rows if not item["ok"]]
        print("%-10s %2d/%2d 通过%s" % (proto, len(rows) - len(bad), len(rows),
                                        "" if not bad else
                                        "  ✗ " + ", ".join(x["check"] for x in bad)))
    print("总计：%d 项，失败 %d" % (total, len(failed)))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())