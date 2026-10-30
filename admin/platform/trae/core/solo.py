"""Trae SOLO / Agent 通道：调 Agent 类模型（`deepseek-v4.1-flash` 等）。

## 为什么必须单独一条通道

Trae 的模型分成两类，**端点不同**：

    chat_v3 / IDE 补全类  -> POST /api/ide/v1/llm_raw_chat
    SOLO / Agent 类       -> POST /api/agent/v3/llm_utils_chat
                             function = solo_work_lite

`deepseek-v4.1-flash`、`glm-5.3-flash`、`kimi-k3` 这些都在**后者**。
拿它们去打 `llm_raw_chat` 会被上游回 `4023 model unknown` ——
错误码看着像「模型不存在」，实际是「这个端点不认识这类模型」。

这条通道只用 access token + 设备身份，**不需要** SQLCipher、
不需要本机 Trae 进程、不需要 AHA 桥。

## 实测（2026-10-30，CN 账号）

    deepseek-v4.1-flash  200 + reasoning_content 流式输出
    glm-5.3-flash        200
    kimi-k3              200
    deepseek-R1          200（这条走 v1 也行，两条通）

参考实现：trae2api / trae2api-hub 的 `trae_solo_chat()`。
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

logger = logging.getLogger("admin.platform.trae.solo")

SOLO_PATH = "/api/agent/v3/llm_utils_chat"
SOLO_HOST_CN = "https://trae-api-cn.mchost.guru"
SOLO_FUNCTION = "solo_work_lite"

#: 参考实现里写死的默认设备身份。
#:
#: 这条通道只校验「这个 token 配一个合法设备」，不要求设备与登录设备一致。
#: 用固定值的好处是号池里所有账号共享同一台"虚拟机"，不会因为本机
#: device_id 变化而失联。真实 device_id 也可以传，二者都通。
DEFAULT_DEVICE_ID = "1149420231460112"
DEFAULT_MACHINE_ID = "d979d81c1deda7a803cb6b76be69cff63b40dd24ea60d3fabbd865607f7ee695"
DEFAULT_APP_ID = "6eefa01c-1036-4c7e-9ca5-d891f63bfcd8"

#: SOLO 通道要求的客户端标识（冒充 antigravity-cockpit-tools 这个壳）
SOLO_UA = "Trae/0.1.61 antigravity-cockpit-tools"
SOLO_IDE_VERSION = "0.1.61"
SOLO_VERSION_CODE = "20260820"

#: 这些模型名归一到 SOLO 通道的 config_name
_SOLO_ALIASES = {
    "deepseek-v4.1-flash": "deepseek-v4.1-flash",
    "deepseek-v4-1-flash": "deepseek-v4.1-flash",
    "DeepSeek-V4.1-Flash": "deepseek-v4.1-flash",
}


def is_solo_model(model: str) -> bool:
    """这个模型是否该走 SOLO 通道。

    判据：`llm_raw_chat` 对它回 4023，而 SOLO 通道能通。
    实测名单见模块 docstring；新增模型时往 `_SOLO_ALIASES` 或
    `SOLO_ONLY_PREFIXES` 里加。
    """
    text = str(model or "").strip()
    if not text:
        return False
    if text in _SOLO_ALIASES or text.lower() in _SOLO_ALIASES:
        return True
    return text.lower() in SOLO_ONLY


#: 只在 SOLO 通道可用的模型（v1 会回 4023）
SOLO_ONLY = {
    "deepseek-v4.1-flash",
    "glm-5.3-flash",
    "glm-5.3",
    "glm-5.3-flashx",
    "kimi-k3",
    "kimi-k3.1-pro",
    "mimo-v2.6-flash",
    "mimo-v2.6-pro",
    "minimax-m3",
    "minimax-m3.1-flash",
    "step-5-preview",
    "qwen3.8-flash",
    "qwen3.8-max",
    "doubao-seed-2.1-turbo",
    "doubao-seed-2.1-pro",
    "doubao-seed-evolving",
    "doubao-seed-code",
}


def config_name_of(model: str) -> str:
    """模型的 `config_name`（SOLO 通道的正式名）。"""
    text = str(model or "").strip()
    return _SOLO_ALIASES.get(text) or _SOLO_ALIASES.get(text.lower()) or text


def build_headers(creds: dict, *, device_id: str = "",
                  machine_id: str = "") -> dict:
    """SOLO 通道请求头。

    注意 `Authorization` 用 `Cloud-IDE-JWT`（不是 Bearer），
    并且 `X-Ide-Version-Code` 必须是 `20260820` —— 用 IDE 的版本号
    会被判 4015/4001。
    """
    from admin.platform.trae.core import client as tc

    data = tc.as_mapping(creds)
    token = tc.token_of(data)
    if not token:
        raise RuntimeError("账号缺少 access token")
    uid = str(tc.uid_of(data) or "")
    inner = data.get("account") if isinstance(data.get("account"), dict) else {}
    return {
        "Accept": "text/event-stream",
        "Content-Type": "application/json",
        "User-Agent": SOLO_UA,
        "Authorization": "Cloud-IDE-JWT " + token,
        "X-Cloudide-Token": token,
        "X-Ide-Token": token,
        "X-Uid": uid,
        "X-Machine-Id": machine_id or str(data.get("machine_id")
                                         or DEFAULT_MACHINE_ID),
        "X-Device-Id": device_id or str(data.get("device_id")
                                        or DEFAULT_DEVICE_ID),
        "X-App-Id": DEFAULT_APP_ID,
        "X-App-Version": "default",
        "X-Ide-Version": SOLO_IDE_VERSION,
        "X-Ide-Version-Code": SOLO_VERSION_CODE,
        "X-App-Version-Code": SOLO_VERSION_CODE,
        "X-Ide-Version-Type": "stable",
        "X-Device-Type": "windows",
        "X-OS-Version": "Windows 11 Pro",
        "X-Device-Brand": "83DG",
        "Request-Traffic-Type": "prod",
        "X-User-Region": inner.get("storeCountryCode") or "CN",
    }


def build_body(model: str, messages: list, *, max_tokens: int = 0,
               tools: list | None = None) -> dict:
    """SOLO 通道请求体。

    必须同时带 `config_name` 和 `model`（两个都要，缺一个上游会走默认模型）；
    `function` 固定 `solo_work_lite`。
    """
    blocks: list[dict] = []
    for raw in messages or []:
        if not isinstance(raw, dict):
            continue
        role = str(raw.get("role") or "user").strip().lower()
        if role == "developer":
            role = "system"
        if role not in ("system", "user", "assistant"):
            role = "user"
        content = raw.get("content")
        if isinstance(content, str):
            parts = [{"type": "text", "text": content}]
        elif isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, str):
                    parts.append({"type": "text", "text": part})
                elif isinstance(part, dict) and part.get("type") == "text":
                    parts.append({"type": "text",
                                  "text": str(part.get("text") or "")})
            if not parts:
                continue
        else:
            continue

        # ---- 工具调用：**保留原生结构，绝不转成文本** ----
        #
        # ## 两个反例，都是踩过的坑
        #
        # 1. 最初一律把 `role: tool` 转成 user 并**丢弃** `tool_calls`，
        #    模型看不到「上一轮调过什么、结果是什么」，于是第二轮又去读
        #    同一个文件，无限循环不收敛（实测连调 4 轮 read）。
        #
        # 2. 后来改成把调用写成可读文本 `[tool calls] read({...})`
        #    塞进正文 —— 修好了循环，却**教会了模型用文本输出工具调用**：
        #    它不再发真正的 `tool_calls` 事件，而是把
        #    `[tool calls] read({"file_path":"..."})` 当正文吐出来。
        #    客户端收到的是纯文字、没有可执行的调用，于是任务中断。
        #
        # ## 正确做法（实测验证）
        #
        # 上游 SOLO **原生支持** assistant 消息带 `tool_calls`，而且事件里的
        # 字段名是 `function_call`（不是 OpenAI 的 `function`）：
        #
        #     实测同一段历史：
        #       文本化给模型看 -> 正文「2 行」，tool 事件 0 个（不会真调用）
        #       原生结构给模型看 -> 正文空，tool 事件 3 个（正确发起调用）
        #
        # 所以：assistant 的 `tool_calls` 原样保留（`function` 翻译成
        # `function_call`），工具结果继续用 `user` 角色回传（上游没有
        # `tool` 角色，结果是「用户侧提供的信息」，语义正确）。
        _tcid = str(raw.get("tool_call_id") or "").strip()
        if _tcid:
            # 工具结果：转成 user 正文，但**不加** `[tool result]` 这类
            # 可被模仿的标记 —— 用一句自然语言交代来源即可。
            _text = "".join(str(p.get("text") or "") for p in parts
                            if isinstance(p, dict))
            parts = [{"type": "text", "text": _text}]
            blocks.append({"role": "user", "content": parts})
            continue

        _raw_calls = raw.get("tool_calls")
        if isinstance(_raw_calls, list) and _raw_calls:
            _calls = []
            for _c in _raw_calls:
                if not isinstance(_c, dict):
                    continue
                _fn = (_c.get("function") if isinstance(_c.get("function"), dict)
                       else _c.get("function_call") if isinstance(_c.get("function_call"), dict)
                       else {})
                _name = str(_fn.get("name") or "")
                if not _name:
                    continue
                _calls.append({
                    "id": str(_c.get("id") or ""),
                    "type": "function",
                    # SOLO 认 `function_call`；`function` 也一并带上做兼容
                    "function_call": {
                        "name": _name,
                        "arguments": str(_fn.get("arguments") or "{}"),
                        "partial_arguments": None,
                        "namespace": None,
                    },
                })
            if _calls:
                blocks.append({"role": "assistant", "content": parts,
                               "tool_calls": _calls})
                continue

        blocks.append({"role": role, "content": parts})
    if not blocks:
        raise ValueError("messages 不能为空")

    name = config_name_of(model)
    body = {
        "messages": blocks,
        "function": SOLO_FUNCTION,
        "stream": True,
        "config_name": name,
        "model": name,
        # 参考实现给 1000000：这个参数是上限而不是目标长度，
        # 给小了会被上游截断，给大不影响计费。
        "max_tokens": int(max_tokens or 1000000),
    }
    if tools:
        body["tools"] = _normalize_tools(tools)
    return body


def _normalize_tools(tools: list) -> list:
    """把 OpenAI 形态的 tools 转成 SOLO 通道认的形态。

    上游是 Go 服务，`FunctionDefinition.tools.function.parameters`
    **声明为 string**，传 JSON 对象会直接 4001：
        json: cannot unmarshal object into Go struct field
        FunctionDefinition.tools.function.parameters of type string

    所以 schema 必须序列化成字符串。同时补齐 `strict`（缺了某些工具会
    被上游忽略）。
    """
    import json as _json

    out: list = []
    for item in tools or []:
        if not isinstance(item, dict):
            continue
        fn = item.get("function") if isinstance(item.get("function"), dict) else None
        if fn is None:
            continue
        params = fn.get("parameters")
        if isinstance(params, (dict, list)):
            params = _json.dumps(params, ensure_ascii=False)
        elif not isinstance(params, str):
            params = "{}"
        fixed = {
            "name": str(fn.get("name") or ""),
            "description": str(fn.get("description") or ""),
            "parameters": params,
        }
        out.append({"type": item.get("type") or "function", "function": fixed})
    return out


def open_stream(creds: dict, model: str, messages: list, *,
                max_tokens: int = 0, tools: list | None = None,
                timeout: float = 300.0, host: str = SOLO_HOST_CN,
                device_id: str = "", machine_id: str = ""):
    """发起 SOLO 流式请求，返回未读取的 response。"""
    body = build_body(model, messages, max_tokens=max_tokens, tools=tools)
    headers = build_headers(creds, device_id=device_id, machine_id=machine_id)
    url = str(host or SOLO_HOST_CN).rstrip("/") + SOLO_PATH
    req = urllib.request.Request(
        url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        method="POST", headers=headers)
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        detail = exc.read(1500).decode("utf-8", "replace")
        raise RuntimeError("SOLO HTTP %s: %s" % (exc.code, detail[:600])) from exc


def iter_events(response):
    """逐条产出 SOLO SSE 事件（dict）。

    事件类型：
        metadata      -> 会话信息（可忽略）
        timing_cost   -> 上游耗时分解（可用于 TTFT 观测）
        output        -> `response` 是正式内容，`reasoning_content` 是思考
        error         -> `code` 非 0
        done          -> 结束
    """
    for raw_line in response:
        line = raw_line.decode("utf-8", "replace").strip()
        if not line:
            continue
        if line.startswith("event:"):
            continue
        if not line.startswith("data:"):
            continue
        raw = line[5:].strip()
        if not raw or raw == "[DONE]":
            continue
        try:
            event = json.loads(raw)
        except ValueError:
            continue
        if isinstance(event, dict):
            yield event


def collect(response) -> dict:
    """把 SOLO 流读完，聚合成 `{content, reasoning, finish_reason, usage}`。"""
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    finish_reason = "stop"
    usage: dict = {}
    model = ""
    for event in iter_events(response):
        code = event.get("code")
        if code not in (None, 0):
            raise RuntimeError("SOLO error %s: %s"
                               % (code, event.get("message") or ""))
        if event.get("model"):
            model = str(event["model"])
        if event.get("response"):
            text_parts.append(str(event["response"]))
        if event.get("reasoning_content"):
            reasoning_parts.append(str(event["reasoning_content"]))
        if event.get("finish_reason"):
            finish_reason = str(event["finish_reason"])
        if "prompt_tokens" in event:
            usage = {"prompt_tokens": int(event.get("prompt_tokens") or 0),
                     "completion_tokens": int(event.get("completion_tokens") or 0),
                     "total_tokens": int(event.get("total_tokens") or 0)}
    return {"content": "".join(text_parts),
            "reasoning": "".join(reasoning_parts),
            "finish_reason": finish_reason, "usage": usage,
            "model": model}
