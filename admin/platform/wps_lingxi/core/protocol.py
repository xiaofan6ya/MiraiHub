"""WPS 灵犀协议层：Cookie 归一、请求体整形、SSE 帧翻译、提示词裁剪。

这里的每个函数都对应插件 `index.mjs` 里的一处实现，注释写清
「为什么这么做」——因为踩过的坑都写在那儿，改了就会坏。

## 四个最容易写错的地方（照抄不要发挥）

1. **`total_tokens` 必须自算**：上游给的是「当前上下文大小」（含缓存命中），
   直接透传会让使用方按轮累加，总量虚高几十倍。
2. **`cached_tokens` / `reasoning_tokens` 是扁平字段**，要写进嵌套的
   `prompt_tokens_details` / `completion_tokens_details`——方向和 OpenAI 相反。
3. **`max_tokens` 要塞进 `base_llm_arguments`**，`reasoning_effort` 顶层与
   `llm_arguments` 各写一份；写成 OpenAI 的顶层 `max_tokens` 上游不理。
4. **工具 schema 必须压平**：`anyOf` / `$ref` / 数组型 `type` 会让上游整轮
   失败，只回一句「AI服务异常」。
"""
from __future__ import annotations

import json
import re
import time

from admin.platform.wps_lingxi.core import endpoints

# ---------------------------------------------------------------------------
# Cookie 归一
# ---------------------------------------------------------------------------
def parse_cookie(raw: str) -> dict:
    """把用户粘进来的任意形状归一成凭据对象。

    接受：裸 `wps_sid` 值 / `wps_sid=…` / 整段 Cookie / 带 `Cookie: ` 前缀 /
    外层引号。只保留 `wps_sid`、`uid`、`copilot_server_branch` 三个键，
    `kso_sid` / `rtk` / `lang` 之类一律丢弃（上游不看，留着只会把日志弄脏）。
    """
    text = str(raw or "").strip()
    if text.lower().startswith("cookie:"):
        text = text[7:].strip()
    text = text.strip().strip('"').strip("'").strip()
    if not text:
        raise ValueError("Cookie 为空")

    parts: dict[str, str] = {}
    for chunk in text.split(";"):
        name, sep, value = chunk.strip().partition("=")
        if not sep:
            continue
        key = name.strip()
        if key:
            parts[key] = value.strip()

    sid = parts.get("wps_sid") or ""
    if not sid:
        # 裸值：不含 `=` 且不含空白的整段，按 wps_sid 处理
        if "=" not in text and " " not in text and ";" not in text:
            sid = text
        else:
            raise ValueError("没有找到 wps_sid（请粘贴 wps_sid 的值或整段 Cookie）")
    if len(sid) < 16:
        raise ValueError("wps_sid 太短（至少 16 个字符），可能没粘全")

    uid = parts.get("uid") or ""
    region = parts.get("copilot_server_branch") or ""
    cookie = "wps_sid=%s" % sid
    if uid:
        cookie += "; uid=%s" % uid
    if region:
        cookie += "; copilot_server_branch=%s" % region
    return {
        "sid": sid,
        "uid": uid,
        "region": region,
        "cookie": cookie,
        "account_id": "wps-%s" % (uid or sid[-8:]),
        "tail": sid[-4:],
    }


def identity_of(cookie: str) -> str:
    """会话缓存的身份键：整段归一 Cookie 的 sha256 前 16 位。

    **不是 uid、不是 sid**：Cookie 轮换后旧 run 会失效，用 uid 做键会让
    两个不同 Cookie 互相踩。
    """
    import hashlib

    return hashlib.sha256(str(cookie or "").encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# 模型名
# ---------------------------------------------------------------------------
def normalize_model(name: str) -> str:
    """去掉客户端可能加的前缀，空值回落到默认模型。

    只去 `wps-lingxi/` 与 `lingxi-` 两个前缀——插件里真正生效的是这条内联
    版本（`lingxi/` 前缀是死代码里的多余分支，别照抄）。
    """
    value = str(name or "").strip()
    if value.startswith("wps-lingxi/"):
        value = value[len("wps-lingxi/"):]
    if value.startswith("lingxi-"):
        value = value[len("lingxi-"):]
    value = value.strip()
    if not value or value.lower() == "default":
        return endpoints.DEFAULT_MODEL
    return value


# ---------------------------------------------------------------------------
# usage
# ---------------------------------------------------------------------------
def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def normalize_usage(usage: dict | None) -> dict:
    """上游 usage → OpenAI usage。

    上游帧里的 `total_tokens` 是**当前上下文大小**（`cached + prompt + completion`
    恰好等于它，实测 43563），不是本轮用量。这里一律自算。
    """
    if not isinstance(usage, dict):
        return {}
    prompt = _int(usage.get("prompt_tokens"))
    completion = _int(usage.get("completion_tokens"))
    out = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }
    cached = _int(usage.get("cached_tokens"))
    if not cached:
        nested = usage.get("prompt_tokens_details")
        if isinstance(nested, dict):
            cached = _int(nested.get("cached_tokens"))
    # 0 也要写：0 是「本次没命中缓存」的真实值，缺字段会让调用方以为没透传
    out["prompt_tokens_details"] = {"cached_tokens": cached}
    reasoning = _int(usage.get("reasoning_tokens"))
    if not reasoning:
        nested = usage.get("completion_tokens_details")
        if isinstance(nested, dict):
            reasoning = _int(nested.get("reasoning_tokens"))
    if reasoning > 0:
        out["completion_tokens_details"] = {"reasoning_tokens": reasoning}
    return out


# ---------------------------------------------------------------------------
# 工具 schema 压平
# ---------------------------------------------------------------------------
_MAX_DEPTH = 24


def _type_of(value) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "string"


def _branch_score(node: dict) -> tuple[int, int]:
    """`anyOf` / `oneOf` 选支：object > array > 标量，同级取字段多的。"""
    kind = str(node.get("type") or "")
    rank = {"object": 3, "array": 2}.get(kind, 1)
    return (rank, len(node))


def sanitize_schema(schema, defs: dict | None = None, seen=None, depth: int = 0):
    """把任意 JSON Schema 压成上游能吃的朴素形状。

    上游的 schema 转换器只认最朴素的子集，出现 `anyOf` / `$ref` 就回
    `AIError`（对外文案「AI服务异常」），`type` 用数组则 400 `InvalidArgument`。
    """
    if depth > _MAX_DEPTH:
        return {"type": "string"}
    if not isinstance(schema, dict):
        return {"type": _type_of(schema)}
    defs = defs if isinstance(defs, dict) else {}
    seen = set() if seen is None else seen

    node = dict(schema)

    # $defs / definitions 只作为展开素材，不往上传
    local_defs = node.pop("$defs", None)
    if isinstance(local_defs, dict):
        defs = {**defs, **local_defs}
    extra_defs = node.pop("definitions", None)
    if isinstance(extra_defs, dict):
        defs = {**defs, **extra_defs}

    node.pop("not", None)

    # $ref：仅展开本地指针 `#/...`
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        if ref in seen:
            return {"type": "string"}
        # `#/$defs/X` 与 `#/definitions/X` 都落到同一张 defs 表上：
        # defs 已经被 pop 出来了，指针头两段要跳过，否则永远查不到。
        path = [token.replace("~1", "/").replace("~0", "~")
                for token in ref[2:].split("/")]
        if path and path[0] in ("$defs", "definitions"):
            path = path[1:]
        target = defs
        for token in path:
            if isinstance(target, dict) and token in target:
                target = target[token]
            else:
                target = None
                break
        if isinstance(target, dict):
            merged = dict(target)
            merged.update({k: v for k, v in node.items() if k != "$ref"})
            return sanitize_schema(merged, defs, seen | {ref}, depth + 1)
        node.pop("$ref", None)
    else:
        node.pop("$ref", None)

    # const → 单元素 enum
    if "const" in node:
        node.pop("const")
        node.setdefault("enum", [schema["const"]])

    # allOf → 合并
    all_of = node.pop("allOf", None)
    if isinstance(all_of, list):
        merged: dict = {}
        for item in all_of:
            piece = sanitize_schema(item, defs, seen, depth + 1)
            if not isinstance(piece, dict):
                continue
            if isinstance(piece.get("properties"), dict):
                merged.setdefault("properties", {}).update(piece["properties"])
            if isinstance(piece.get("required"), list):
                have = merged.setdefault("required", [])
                for name in piece["required"]:
                    if name not in have:
                        have.append(name)
            for key, value in piece.items():
                if key not in ("properties", "required", "type"):
                    merged.setdefault(key, value)
            if piece.get("type") == "object":
                merged["type"] = "object"
        for key, value in node.items():
            if key not in merged:
                merged[key] = value
        node = merged

    # anyOf / oneOf → 保留信息量最大的一支
    for union in ("anyOf", "oneOf"):
        branches = node.pop(union, None)
        if not isinstance(branches, list) or not branches:
            continue
        usable = []
        for item in branches:
            piece = sanitize_schema(item, defs, seen, depth + 1)
            if not isinstance(piece, dict):
                continue
            if str(piece.get("type") or "") == "null":
                continue
            usable.append(piece)
        if not usable:
            node.setdefault("type", "string")
            continue
        best = max(usable, key=_branch_score)
        node = {**best, **{k: v for k, v in node.items() if k not in best}}

    # type 是数组 → 第一个非 null
    raw_type = node.get("type")
    if isinstance(raw_type, list):
        picked = next((str(t) for t in raw_type if str(t) != "null"), "")
        if picked:
            node["type"] = picked
        else:
            node.pop("type", None)
    elif isinstance(raw_type, str) and raw_type == "null":
        node.pop("type", None)

    # 递归子结构
    if isinstance(node.get("properties"), dict):
        node["properties"] = {
            key: sanitize_schema(value, defs, seen, depth + 1)
            for key, value in node["properties"].items()
        }
    if isinstance(node.get("items"), (dict, list)):
        items = node["items"]
        node["items"] = ([sanitize_schema(item, defs, seen, depth + 1)
                          for item in items] if isinstance(items, list)
                         else sanitize_schema(items, defs, seen, depth + 1))
    if isinstance(node.get("additionalProperties"), dict):
        node["additionalProperties"] = sanitize_schema(
            node["additionalProperties"], defs, seen, depth + 1)

    # 每个节点必须有 type
    if not str(node.get("type") or "").strip():
        if isinstance(node.get("properties"), dict) or "required" in node \
                or "additionalProperties" in node:
            node["type"] = "object"
        elif "items" in node:
            node["type"] = "array"
        elif isinstance(node.get("enum"), list) and node["enum"]:
            node["type"] = _type_of(node["enum"][0])
        else:
            node["type"] = "string"
    return node


def filter_tools(tools) -> list[dict]:
    """丢掉上游不认的工具项，并压平 parameters。"""
    out: list[dict] = []
    if not isinstance(tools, list):
        return out
    for item in tools:
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "function") != "function":
            continue
        function = item.get("function")
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        entry = {"type": "function",
                 "function": {"name": name.strip()}}
        if isinstance(function.get("description"), str):
            entry["function"]["description"] = function["description"]
        params = function.get("parameters")
        entry["function"]["parameters"] = sanitize_schema(
            params if isinstance(params, dict) else {"type": "object",
                                                     "properties": {}})
        out.append(entry)
    return out


# ---------------------------------------------------------------------------
# 提示词裁剪（四步）
# ---------------------------------------------------------------------------
def _byte_len(value) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        return 0


def _cut_bytes(text: str, limit: int) -> tuple[str, str]:
    """按 UTF-8 字节在安全边界切开，绝不切坏多字节字符。"""
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text, ""
    head_len = int(limit * 0.6)
    tail_len = limit - head_len
    head = raw[:head_len]
    while head:
        try:
            head_text = head.decode("utf-8")
            break
        except UnicodeDecodeError:
            head = head[:-1]
    else:
        head_text = ""
    tail = raw[-tail_len:] if tail_len > 0 else b""
    while tail:
        try:
            tail_text = tail.decode("utf-8")
            break
        except UnicodeDecodeError:
            tail = tail[1:]
    else:
        tail_text = ""
    return head_text, tail_text


def _shrink_message(message: dict, limit: int) -> tuple[dict, bool]:
    """掐头去尾一条超长消息。返回 `(新消息, 是否改过)`。"""
    content = message.get("content")
    if isinstance(content, str):
        if len(content.encode("utf-8")) <= limit:
            return message, False
        head, tail = _cut_bytes(content, limit)
        note = "…[本条消息过长，已截断（原 %d 字节）]…" % len(
            content.encode("utf-8"))
        return dict(message, content=head + "\n" + note + "\n" + tail), True
    if isinstance(content, list):
        parts = []
        changed = False
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                text = block["text"]
                if len(text.encode("utf-8")) > limit:
                    head, tail = _cut_bytes(text, limit)
                    note = "…[本条消息过长，已截断（原 %d 字节）]…" % len(
                        text.encode("utf-8"))
                    block = dict(block, text=head + "\n" + note + "\n" + tail)
                    changed = True
            parts.append(block)
        return (dict(message, content=parts), True) if changed else (message, False)
    return message, False


def fit_messages(body: dict) -> tuple[dict, int, int]:
    """把 messages 裁到上游能接受的体积。

    四步（顺序不能换，否则会出现「留下孤儿 tool 消息」被上游拒）：

    1. 单条超 48KB → 掐头去尾（保留前 60% / 后 40%，中间插标记）；
    2. 还超 → 从最老一端整条丢，开头的 `system` 连续段一律保留；
    3. 丢完若头一条是 `tool` 结果 → 继续丢（tool_calls 与结果必须成对）；
    4. 插一行压缩标记（它自己也占字节，所以插完要再量一次、放不下再丢）。

    返回 `(新 body, 丢掉的条数, 截断的条数)`；无改动时两个计数都是 0。
    """
    messages = body.get("messages")
    if not isinstance(messages, list) or len(messages) <= 1:
        return body, 0, 0

    prompt_limit = endpoints.max_prompt_bytes()
    message_limit = endpoints.max_message_bytes()

    fixed = _byte_len({k: v for k, v in body.items() if k != "messages"})
    total = fixed + sum(_byte_len(item) for item in messages)
    if total <= prompt_limit:
        return body, 0, 0

    # 1) 掐长条
    elided = 0
    shrunk: list[dict] = []
    for item in messages:
        if isinstance(item, dict):
            message, changed = _shrink_message(item, message_limit)
            if changed:
                elided += 1
            shrunk.append(message)
        else:
            shrunk.append(item)
    messages = shrunk
    total = fixed + sum(_byte_len(item) for item in messages)
    if total <= prompt_limit:
        return dict(body, messages=messages), 0, elided

    # 2) 从最老一端丢，保留开头 system 段
    head = 0
    last = len(messages) - 1
    while head < last and isinstance(messages[head], dict) \
            and str(messages[head].get("role") or "") == "system":
        head += 1
    start = head
    while start < last and total > prompt_limit:
        total -= _byte_len(messages[start])
        start += 1

    # 3) 丢掉孤儿 tool 结果
    while start < last and isinstance(messages[start], dict) \
            and str(messages[start].get("role") or "") == "tool":
        total -= _byte_len(messages[start])
        start += 1

    # 4) 插标记，回退到真的装得下
    dropped = start - head
    while dropped > 0:
        note = _note_of(dropped)
        candidate = list(messages[:head]) + [note] + list(messages[start:])
        if fixed + sum(_byte_len(item) for item in candidate) <= prompt_limit:
            return dict(body, messages=candidate), dropped, elided
        if start >= last:
            break
        total -= _byte_len(messages[start])
        start += 1
        while start < last and isinstance(messages[start], dict) \
                and str(messages[start].get("role") or "") == "tool":
            start += 1
        dropped = start - head
    return dict(body, messages=messages[: head] + list(messages[start:])), \
        max(0, start - head), elided


def _note_of(dropped: int) -> dict:
    kb = max(1, endpoints.max_prompt_bytes() // 1024)
    return {
        "role": "system",
        "content": "[历史已压缩：更早的 %d 条消息被省略"
                   "（上游单次请求长度上限 %dKB）]" % (dropped, kb),
    }


# ---------------------------------------------------------------------------
# 上游请求体
# ---------------------------------------------------------------------------
def normalize_messages(messages: list) -> list[dict]:
    """把各家客户端的 messages 收成上游只认的形状。

    ## 为什么必须收

    不同客户端发过来的消息「方言」不一样，而上游 agent-runtime 只认
    `system / user / assistant / tool` 这四种 role、只认字符串 content：

      * **`role: "developer"`** —— OpenAI 新规范（o 系列 / 新 SDK 都这么发）。
        DSH 这类客户端首发就是 developer 角色，上游直接回 400「参数错误」。
        处理：并入 `system`（语义等价，上游没有 developer 这一档）。
      * **content 是块数组**（`[{"type":"text","text":...}]`）—— 上游要字符串，
        传数组同样 400。处理：抽出 text 拍平。
      * **`tool` 消息缺 `tool_call_id`** —— 上游按「找不到对应调用」拒绝。
      * 多余的字段（`name` / `cache_control` / `annotations`）上游不认，
        一律丢掉，只留它认识的键。

    这条链路的历史教训与 MiniMax 平台完全一致（见 1.0.7 的 developer 根因修复）：
    **别指望客户端守上游的规矩，网关负责翻译。**
    """
    out: list[dict] = []
    systems: list[str] = []

    def _text_of(content) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict):
                    kind = str(block.get("type") or "")
                    if kind in ("text", "input_text", "output_text"):
                        value = block.get("text")
                        if isinstance(value, str) and value:
                            parts.append(value)
                    elif isinstance(block.get("content"), str):
                        parts.append(block["content"])
            return "\n".join(parts)
        if isinstance(content, dict):
            value = content.get("text")
            return value if isinstance(value, str) else json.dumps(
                content, ensure_ascii=False)
        return str(content)

    for item in messages or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "user").strip().lower()
        if role in ("developer", "system"):
            text = _text_of(item.get("content")).strip()
            if text:
                systems.append(text)
            continue
        if role not in ("user", "assistant", "tool"):
            role = "user"
        text = _text_of(item.get("content"))
        msg: dict = {"role": role, "content": text}
        if role == "assistant":
            calls = item.get("tool_calls")
            if isinstance(calls, list) and calls:
                cleaned = []
                for index, call in enumerate(calls):
                    if not isinstance(call, dict):
                        continue
                    fn = call.get("function") if isinstance(call.get("function"), dict) else {}
                    args = fn.get("arguments")
                    if not isinstance(args, str):
                        args = json.dumps(args if args is not None else {},
                                          ensure_ascii=False)
                    cleaned.append({
                        "id": str(call.get("id") or "call_%d" % index),
                        "type": "function",
                        "function": {"name": str(fn.get("name") or ""),
                                     "arguments": args},
                    })
                if cleaned:
                    msg["tool_calls"] = cleaned
        elif role == "tool":
            msg["tool_call_id"] = str(item.get("tool_call_id") or "")
        out.append(msg)

    # system 合并成一条放最前面（上游对多条 system 的处理不一致）
    if systems:
        out.insert(0, {"role": "system", "content": "\n\n".join(systems)})
    return out


def build_agent_body(body: dict, *, client_type: str = "",
                     session_mode: str = "") -> dict:
    """客户端请求 → 上游 agent-runtime 请求体。

    形状是上游自己的：`max_tokens` 包进 `base_llm_arguments`，
    `reasoning_effort` 顶层与 `llm_arguments` 各写一份。
    """
    model = str(body.get("model") or "")
    return dict(
        {"stream": True, "model": model},
        **{
            "messages": normalize_messages(body.get("messages") or []),
            **({"tools": body["tools"]} if body.get("tools") else {}),
            **({"tool_choice": body["tool_choice"]}
               if body.get("tool_choice") is not None and body.get("tools") else {}),
            **({"temperature": body["temperature"]}
               if isinstance(body.get("temperature"), (int, float))
               and not isinstance(body.get("temperature"), bool) else {}),
            **({"base_llm_arguments": {"max_tokens": int(body["max_tokens"])}}
               if isinstance(body.get("max_tokens"), int)
               and not isinstance(body.get("max_tokens"), bool)
               and body["max_tokens"] > 0 else {}),
        },
    )


def prepare_agent_body(body: dict, *, client_type: str = "",
                       session_mode: str = "") -> tuple[dict, int, int]:
    """构造 + 裁剪。返回 `(上游 body, 丢掉条数, 截断条数)`。"""
    payload = build_agent_body(body, client_type=client_type,
                               session_mode=session_mode)
    effort = body.get("reasoning_effort")
    if isinstance(effort, str) and effort.strip():
        payload["reasoning_effort"] = effort.strip()
        payload["llm_arguments"] = {"reasoning_effort": effort.strip()}
    if payload.get("tools"):
        payload["tools"] = filter_tools(payload["tools"])
    return fit_messages(payload)


# ---------------------------------------------------------------------------
# 上游 SSE 帧 → OpenAI chunk
# ---------------------------------------------------------------------------
AUDIT_PATTERN = re.compile(
    r"\bAuditFailed\b|\baudit_blocked\b|暂时无法回答此问题", re.I)


def text_of_payload(payload) -> str:
    """从上游错误体里挖一条人类可读的话（顺序照抄插件）。"""
    if not isinstance(payload, dict):
        return str(payload or "")[:300]
    for key in ("hint", "msg", "message"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:300]
    error = payload.get("error")
    if isinstance(error, dict):
        value = error.get("message")
        if isinstance(value, str) and value.strip():
            return value.strip()[:300]
    if isinstance(error, str) and error.strip():
        return error.strip()[:300]
    return ""


def is_audit_frame(frame: dict) -> bool:
    """审核拦截识别（三个字段拼起来匹配，任一命中即算）。"""
    if not isinstance(frame, dict):
        return False
    parts = [frame.get("code"), frame.get("result"), frame.get("message"),
             text_of_payload(frame)]
    error = frame.get("error")
    if isinstance(error, dict):
        parts.append(error.get("code"))
    text = " ".join(str(p) for p in parts if p)
    return bool(AUDIT_PATTERN.search(text))


def frame_error(frame: dict) -> tuple[str, str] | None:
    """帧内业务错误 → `(code, message)`；正常帧返回 None。"""
    if not isinstance(frame, dict):
        return None
    code = frame.get("code")
    if isinstance(code, str) and code.strip() and code.strip().lower() != "success":
        message = text_of_payload(frame) or ("灵犀上游返回 %s" % code.strip())
        return code.strip(), message
    result = frame.get("result")
    if isinstance(result, str) and result.strip() and result.strip().lower() != "ok":
        message = text_of_payload(frame) or ("result=%s" % result.strip())
        return result.strip(), message
    return None


class FrameTranslator:
    """把上游帧翻成 OpenAI `chat.completion.chunk`。

    上游帧的形状是 `choices[].text` / `reasoning_content` / `tool_calls`，
    **不是** OpenAI 的 `delta.content`；收尾靠 `finish_reason` 非空（上游
    不发 `[DONE]`，本地补）。
    """

    def __init__(self, model: str):
        self.model = model
        self.created = int(time.time())
        self.id = "chatcmpl-lingxi-%d" % int(self.created)
        self.usage: dict = {}
        self.finish_reason = ""
        self.finished = False
        self._started = False

    def _chunk(self, delta: dict, finish=None) -> bytes:
        payload = {
            "id": self.id,
            "object": "chat.completion.chunk",
            "created": self.created,
            "model": self.model,
            "choices": [{"index": 0, "delta": delta,
                         "finish_reason": finish}],
        }
        return ("data: %s\n\n" % json.dumps(payload, ensure_ascii=False)).encode()

    def start(self) -> list[bytes]:
        if self._started:
            return []
        self._started = True
        return [self._chunk({"role": "assistant", "content": ""})]

    def feed(self, frame: dict) -> list[bytes]:
        """吃一帧上游数据，吐出要下发的 chunk 列表。"""
        out: list[bytes] = []
        if not isinstance(frame, dict):
            return out
        usage = frame.get("usage")
        if isinstance(usage, dict) and usage:
            self.usage = normalize_usage(usage)
        choice = (frame.get("choices") or [{}])[0]
        if not isinstance(choice, dict):
            choice = {}
        text = choice.get("text")
        reasoning = choice.get("reasoning_content")
        delta: dict = {}
        if isinstance(reasoning, str) and reasoning:
            delta["reasoning_content"] = reasoning
        if isinstance(text, str) and text:
            delta["content"] = text
        tool_calls = choice.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            delta["tool_calls"] = [self._tool_call(item) for item in tool_calls
                                   if isinstance(item, dict)]
        if delta:
            out.append(self._chunk(delta))
        finish = choice.get("finish_reason")
        if isinstance(finish, str) and finish.strip():
            self.finish_reason = finish.strip()
            self.finished = True
        return out

    @staticmethod
    def _tool_call(item: dict) -> dict:
        out: dict = {}
        if item.get("index") is not None:
            try:
                out["index"] = int(item["index"])
            except (TypeError, ValueError):
                out["index"] = 0
        if isinstance(item.get("id"), str) and item["id"]:
            out["id"] = item["id"]
            out["type"] = "function"
        function = item.get("function")
        if isinstance(function, dict):
            piece = {}
            if isinstance(function.get("name"), str) and function["name"]:
                piece["name"] = function["name"]
            if isinstance(function.get("arguments"), str):
                piece["arguments"] = function["arguments"]
            if piece:
                out["function"] = piece
        return out

    def tail(self) -> list[bytes]:
        """收尾：finish 帧 + usage 帧 + `[DONE]`（本地补，上游不发）。"""
        out: list[bytes] = []
        if not self.finished:
            return out
        out.append(self._chunk({}, self.finish_reason or "stop"))
        if self.usage:
            out.append(("data: %s\n\n" % json.dumps({
                "id": self.id, "object": "chat.completion.chunk",
                "created": self.created, "model": self.model,
                "choices": [], "usage": self.usage,
            }, ensure_ascii=False)).encode())
        out.append(b"data: [DONE]\n\n")
        return out

    def fold(self) -> dict:
        """把已喂过的帧折叠成非流式 `chat.completion`。"""
        return {
            "id": self.id,
            "object": "chat.completion",
            "created": self.created,
            "model": self.model,
            "choices": [{"index": 0, "message": {}, "finish_reason": "stop"}],
            "usage": self.usage or {"prompt_tokens": 0, "completion_tokens": 0,
                                    "total_tokens": 0},
        }


class FoldAccumulator:
    """把上游帧累积成一个非流式答复（`stream:false` 时用）。"""

    def __init__(self, model: str):
        self.model = model
        self.created = int(time.time())
        self.id = "chatcmpl-lingxi-%d" % int(self.created)
        self.text: list[str] = []
        self.reasoning: list[str] = []
        self.tool_calls: dict[int, dict] = {}
        self.usage: dict = {}
        self.finish_reason = ""

    def feed(self, frame: dict) -> None:
        if not isinstance(frame, dict):
            return
        usage = frame.get("usage")
        if isinstance(usage, dict) and usage:
            self.usage = normalize_usage(usage)
        choice = (frame.get("choices") or [{}])[0]
        if not isinstance(choice, dict):
            return
        if isinstance(choice.get("text"), str) and choice["text"]:
            self.text.append(choice["text"])
        if isinstance(choice.get("reasoning_content"), str) \
                and choice["reasoning_content"]:
            self.reasoning.append(choice["reasoning_content"])
        for item in (choice.get("tool_calls") or []):
            if not isinstance(item, dict):
                continue
            try:
                index = int(item.get("index") or 0)
            except (TypeError, ValueError):
                index = 0
            slot = self.tool_calls.setdefault(
                index, {"id": "", "type": "function",
                        "function": {"name": "", "arguments": ""}})
            if isinstance(item.get("id"), str) and item["id"]:
                slot["id"] = item["id"]
            function = item.get("function")
            if isinstance(function, dict):
                if isinstance(function.get("name"), str) and function["name"]:
                    slot["function"]["name"] += function["name"]
                if isinstance(function.get("arguments"), str):
                    slot["function"]["arguments"] += function["arguments"]
        finish = choice.get("finish_reason")
        if isinstance(finish, str) and finish.strip():
            self.finish_reason = finish.strip()

    def result(self) -> dict:
        message: dict = {"role": "assistant",
                         "content": "".join(self.text)}
        if self.reasoning:
            message["reasoning_content"] = "".join(self.reasoning)
        if self.tool_calls:
            message["tool_calls"] = [self.tool_calls[index]
                                     for index in sorted(self.tool_calls)]
        return {
            "id": self.id,
            "object": "chat.completion",
            "created": self.created,
            "model": self.model,
            "choices": [{"index": 0, "message": message,
                         "finish_reason": self.finish_reason or "stop"}],
            "usage": self.usage or {"prompt_tokens": 0, "completion_tokens": 0,
                                    "total_tokens": 0},
        }


# ---------------------------------------------------------------------------
# Anthropic / Responses 入口翻译
# ---------------------------------------------------------------------------
def anthropic_to_openai(body: dict) -> dict:
    """Anthropic `/v1/messages` → OpenAI `chat/completions`。

    工具也要一起搬：Anthropic 的 `tools[].input_schema` 对应 OpenAI 的
    `tools[].function.parameters`；assistant 的 `tool_use` 块对应
    `tool_calls`；user 的 `tool_result` 块对应 `role=tool` 的消息。
    少搬任何一处，模型就会回「我没有可用的工具」。
    """
    out: dict = {"model": body.get("model") or ""}
    messages: list[dict] = []

    system = body.get("system")
    if isinstance(system, str) and system.strip():
        messages.append({"role": "system", "content": system})
    elif isinstance(system, list):
        text = "".join(str(block.get("text") or "") for block in system
                       if isinstance(block, dict))
        if text.strip():
            messages.append({"role": "system", "content": text})

    for item in (body.get("messages") or []):
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "user")
        content = item.get("content")
        texts: list[str] = []
        tool_calls: list[dict] = []
        tool_results: list[dict] = []
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                kind = block.get("type")
                if kind == "text":
                    texts.append(str(block.get("text") or ""))
                elif kind == "tool_use":
                    tool_calls.append({
                        "id": str(block.get("id") or "call_%d" % len(tool_calls)),
                        "type": "function",
                        "function": {
                            "name": str(block.get("name") or ""),
                            "arguments": json.dumps(block.get("input") or {},
                                                    ensure_ascii=False),
                        },
                    })
                elif kind == "tool_result":
                    inner = block.get("content")
                    if isinstance(inner, list):
                        inner = "".join(str(part.get("text") or "")
                                        for part in inner
                                        if isinstance(part, dict))
                    tool_results.append({
                        "role": "tool",
                        "tool_call_id": str(block.get("tool_use_id") or ""),
                        "content": inner if isinstance(inner, str) else "",
                    })
            content = "".join(texts)
        # 工具结果回应的是上一条 assistant 的 tool_calls，必须先落
        messages.extend(tool_results)
        if tool_calls and role == "assistant":
            messages.append({"role": "assistant",
                             "content": content if isinstance(content, str) else "",
                             "tool_calls": tool_calls})
            continue
        if content is None or (isinstance(content, str) and not content.strip()):
            continue
        messages.append({"role": role, "content": content})

    # 工具定义：Anthropic `input_schema` → OpenAI `function.parameters`
    tools: list[dict] = []
    for tool in (body.get("tools") or []):
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name") or "").strip()
        if not name:
            continue
        schema = tool.get("input_schema")
        if not isinstance(schema, dict):
            schema = {"type": "object", "properties": {}}
        tools.append({"type": "function",
                      "function": {"name": name,
                                   "description": str(tool.get("description") or ""),
                                   "parameters": schema}})
    if tools:
        out["tools"] = tools
        choice = body.get("tool_choice")
        if isinstance(choice, dict):
            kind = str(choice.get("type") or "")
            if kind == "any":
                out["tool_choice"] = "required"
            elif kind == "tool" and choice.get("name"):
                out["tool_choice"] = {"type": "function",
                                      "function": {"name": str(choice["name"])}}
            elif kind == "auto":
                out["tool_choice"] = "auto"
        elif isinstance(choice, str) and choice in ("auto", "any", "none"):
            out["tool_choice"] = "required" if choice == "any" else choice

    out["messages"] = messages
    for src, dst in (("max_tokens", "max_tokens"),
                     ("temperature", "temperature"),
                     ("top_p", "top_p"),
                     ("stop_sequences", "stop")):
        if body.get(src) is not None:
            out[dst] = body[src]
    return out
def _cached_tokens(usage: dict) -> int:
    """上游给过的缓存命中 token；没有就 0，不编造。"""
    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict):
        try:
            return int(details.get("cached_tokens") or 0)
        except (TypeError, ValueError):
            return 0
    try:
        return int(usage.get("cached_tokens") or 0)
    except (TypeError, ValueError):
        return 0


def _anthropic_usage(usage: dict) -> dict:
    """OpenAI usage → Anthropic usage（**缓存字段必须一直在**）。

    Anthropic 把缓存命中放在 `cache_read_input_tokens`、把写入放在
    `cache_creation_input_tokens`，**不是** `prompt_tokens_details`。

    ## 为什么 0 也要写出来（2026-11-09）

    原实现是「上游给 0 就不写这个键」，看起来更干净，但客户端（Claude Code /
    Claude Desktop）读的是**字段是否存在**：缺键 = 不支持缓存，于是它把
    `cache_read_input_tokens` 显示成「不支持」而不是 0，并据此判断
    「这个网关没有 prompt caching」—— 用户看到的就是「缓存命中丢了」。

    Anthropic 官方响应里这两个键**恒存在**（未命中时是 0），所以这里照抄。
    """
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    cached = _cached_tokens(usage)
    # 写入量：上游（OpenAI 形态）通常不回报，如实给 0，不编造
    created = int(usage.get("cache_creation_input_tokens") or 0)
    return {
        "input_tokens": prompt,
        "output_tokens": completion,
        "cache_read_input_tokens": cached,
        "cache_creation_input_tokens": created,
    }


def openai_to_anthropic(result: dict, model: str) -> dict:
    """OpenAI 结果 → Anthropic `/v1/messages` 响应（含 tool_use 块）。"""
    choices = result.get("choices") or []
    first = choices[0] if choices else {}
    message = first.get("message") or {}
    text = message.get("content")
    if not isinstance(text, str):
        text = ""
    usage = result.get("usage") or {}

    content_blocks: list[dict] = []
    if text.strip():
        content_blocks.append({"type": "text", "text": text})
    for index, call in enumerate(message.get("tool_calls") or []):
        if not isinstance(call, dict):
            continue
        fn = call.get("function") or {}
        raw_args = fn.get("arguments")
        if isinstance(raw_args, str):
            try:
                parsed = json.loads(raw_args) if raw_args.strip() else {}
            except Exception:  # noqa: BLE001 - 上游偶尔给半截 JSON
                parsed = {"_raw": raw_args}
        elif isinstance(raw_args, dict):
            parsed = raw_args
        else:
            parsed = {}
        content_blocks.append({
            "type": "tool_use",
            "id": str(call.get("id") or "toolu_%d" % index),
            "name": str(fn.get("name") or ""),
            "input": parsed,
        })
    if not content_blocks:
        content_blocks.append({"type": "text", "text": ""})

    finish = first.get("finish_reason")
    if message.get("tool_calls") or finish == "tool_calls":
        stop_reason = "tool_use"
    elif finish in (None, "stop"):
        stop_reason = "end_turn"
    elif finish == "length":
        stop_reason = "max_tokens"
    else:
        stop_reason = str(finish)

    return {
        "id": result.get("id") or ("msg_%d" % int(time.time())),
        "type": "message",
        "role": "assistant",
        "model": model or result.get("model") or "",
        "content": content_blocks,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": _anthropic_usage(usage),
    }
def responses_request_to_openai(body: dict) -> dict:
    """Responses 请求 → OpenAI chat 请求（含 instructions / tools）。"""
    model = str(body.get("model") or "")
    raw = body.get("input")
    messages: list[dict] = []
    if isinstance(raw, str):
        messages.append({"role": "user", "content": raw})
    elif isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("type") or "")
            if kind == "function_call_output":
                messages.append({"role": "tool",
                                 "tool_call_id": str(item.get("call_id") or ""),
                                 "content": str(item.get("output") or "")})
                continue
            if kind == "function_call":
                # Responses 的 function_call 必须变成 assistant.tool_calls，
                # 否则后面的 role=tool 会「没有对应的 tool_calls」——
                # 上游直接回 AI服务异常（502）。
                messages.append({
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": str(item.get("call_id") or item.get("id") or "call_0"),
                        "type": "function",
                        "function": {
                            "name": str(item.get("name") or ""),
                            "arguments": (item.get("arguments")
                                          if isinstance(item.get("arguments"), str)
                                          else json.dumps(item.get("arguments") or {},
                                                          ensure_ascii=False)),
                        },
                    }],
                })
                continue
            role = item.get("role") or "user"
            content = item.get("content")
            if isinstance(content, list):
                content = "".join(str(block.get("text") or "")
                                  for block in content
                                  if isinstance(block, dict))
            if content:
                messages.append({"role": role, "content": content})
    if not messages:
        messages.append({"role": "user", "content": ""})
    instructions = body.get("instructions")
    if isinstance(instructions, str) and instructions.strip():
        messages.insert(0, {"role": "system", "content": instructions})

    out = {"model": model, "messages": messages}
    if body.get("max_output_tokens") is not None:
        out["max_tokens"] = body["max_output_tokens"]
    elif body.get("max_tokens") is not None:
        out["max_tokens"] = body["max_tokens"]
    if body.get("temperature") is not None:
        out["temperature"] = body["temperature"]

    tools: list[dict] = []
    for tool in (body.get("tools") or []):
        if not isinstance(tool, dict):
            continue
        if str(tool.get("type") or "function") != "function":
            continue
        name = str(tool.get("name") or (tool.get("function") or {}).get("name") or "")
        if not name:
            continue
        params = tool.get("parameters")
        if not isinstance(params, dict):
            params = (tool.get("function") or {}).get("parameters")
        if not isinstance(params, dict):
            params = {"type": "object", "properties": {}}
        tools.append({"type": "function",
                      "function": {"name": name,
                                   "description": str(tool.get("description") or ""),
                                   "parameters": params}})
    if tools:
        out["tools"] = tools
        choice = body.get("tool_choice")
        if isinstance(choice, str) and choice in ("auto", "required", "none"):
            out["tool_choice"] = choice
        elif isinstance(choice, dict) and choice.get("name"):
            out["tool_choice"] = {"type": "function",
                                  "function": {"name": str(choice["name"])}}
    return out
def _responses_usage(usage: dict) -> dict:
    """OpenAI usage → Responses usage（缓存命中放 input_tokens_details）。"""
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    total = int(usage.get("total_tokens") or (prompt + completion))
    details = {"cached_tokens": _cached_tokens(usage)}
    return {"input_tokens": prompt,
            "input_tokens_details": details,
            "output_tokens": completion,
            "total_tokens": total}


def openai_to_responses(result: dict, model: str) -> dict:
    """OpenAI 结果 → Responses 对象（含 function_call 输出项）。"""
    choices = result.get("choices") or []
    first = choices[0] if choices else {}
    message = first.get("message") or {}
    text = message.get("content") or ""
    usage = result.get("usage") or {}
    rid = "resp_" + str(result.get("id") or int(time.time()))

    output_items: list[dict] = []
    if isinstance(text, str) and text.strip():
        output_items.append({
            "type": "message", "id": "msg_" + rid[-12:], "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": text,
                         "annotations": []}],
        })
    for index, call in enumerate(message.get("tool_calls") or []):
        if not isinstance(call, dict):
            continue
        fn = call.get("function") or {}
        raw_args = fn.get("arguments")
        output_items.append({
            "type": "function_call",
            "id": "fc_" + str(call.get("id") or index)[-12:],
            "call_id": str(call.get("id") or ""),
            "name": str(fn.get("name") or ""),
            "arguments": (raw_args if isinstance(raw_args, str)
                          else json.dumps(raw_args or {}, ensure_ascii=False)),
            "status": "completed",
        })
    if not output_items:
        output_items.append({
            "type": "message", "id": "msg_" + rid[-12:], "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "", "annotations": []}],
        })

    return {
        "id": rid,
        "object": "response",
        "created_at": result.get("created") or int(time.time()),
        "status": "completed",
        "model": model or result.get("model") or "",
        "output": output_items,
        "usage": _responses_usage(usage),
    }
def estimate_tokens(text: str) -> int:
    """粗估 token：中文约 1.5 字/token，英文约 4 字符/token。"""
    return max(1, int(len(text or "") / 2.2))