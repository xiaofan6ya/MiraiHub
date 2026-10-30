"""内置对话智能体：上下文管理 + 工具调度 + agent 循环。

设计目标
--------
用户要的是一个**像客户端一样能对话、能生图、能生视频**的内置页面，而且
「token 共享、免登录、和 Key 绑定共享一切账号权益」。

关键决定
--------
1. **不引入第三方 agent 框架**。本项目已经有完整的账号池、重试、错误分类、
   计费与流式聚合（`admin/routers/proxy.py`）。再叠一层框架只会让「为什么
   这次请求用了哪个号、扣了多少积分」变得不可追踪。这里的循环只是一个
   while + tool dispatch，几十行就能读完。

2. **上下文由服务端持有**（`AgentConversation.messages`），因为内置页面
   刷新后必须能接着聊。但上下文**必须做预算控制**：工具结果动辄上千 token
   （视频任务、合并报告），不裁剪几轮之后就会稳定超上游窗口。

3. **工具结果一律是 JSON 字符串**。模型对结构化文本的解析远好于自由文本，
   前端也能直接渲染。工具失败不抛异常中断循环，而是回一个带 `ok:false`
   的结果让模型自己决定下一步 —— 这是 agent 与「一次性 RPC」的核心区别。

4. **媒体产出进 `AgentAsset`**，不塞进对话上下文。上下文里只放引用
   （`asset_id` + 简短描述），否则一张 2.6 MB 的视频会让后续每轮都变慢。
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from datetime import datetime, timedelta

_logger = logging.getLogger(__name__)

#: 上下文预算：估算 token 上限。上游窗口通常 >= 128K，但历史越长越慢越贵，
#: 这里按「保留最近若干轮 + 首轮系统提示」的策略压到 24K 左右。
CONTEXT_TOKEN_BUDGET = 24_000
#: 单条工具结果的最大字符数（超出截断并标记）。视频/合并报告可能很长。
TOOL_RESULT_LIMIT = 6_000
#: 单轮最多工具调用次数，防止模型陷入「无限调工具」。
MAX_TOOL_STEPS = 6
#: 会话保留时间。
CONVERSATION_TTL_HOURS = 24 * 7
#: 资产保留时间。
ASSET_TTL_HOURS = 24 * 7


# ---------------------------------------------------------------------------
# 上下文管理
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    """粗估 token 数。

    刻意用「字符数 / 2.6」这种粗略口径而不是精确 tokenizer：
      * 精确 tokenizer 要引入依赖（tiktoken 在 Android 上不可用）；
      * 这里只用来决定「要不要裁剪」，误差 20% 完全不影响判断。
    中文约 1 字 ≈ 1 token，英文约 4 字符 ≈ 1 token，取 2.6 是两者的折中。
    """
    if not text:
        return 0
    return max(1, int(len(str(text)) / 2.6))


def _message_tokens(message: dict) -> int:
    total = 0
    for key in ('content', 'reasoning_content'):
        value = message.get(key)
        if isinstance(value, str):
            total += estimate_tokens(value)
    tool_calls = message.get('tool_calls')
    if isinstance(tool_calls, list):
        try:
            total += estimate_tokens(json.dumps(tool_calls, ensure_ascii=False))
        except (TypeError, ValueError):
            pass
    return total


def trim_context(messages, budget: int = CONTEXT_TOKEN_BUDGET) -> list[dict]:
    """把历史裁到预算内，同时保证**工具调用配对完整**。

    裁剪规则（顺序很重要）：
      1. 始终保留第一条 system 消息（人格/规则不能丢）；
      2. 从**最新**往前累计，超预算就停 —— 最近的对话最重要；
      3. 若切点落在 assistant(tool_calls) 与后续 role:tool 之间，整体前移
         到该 assistant 之前。留下半个工具链，上游会直接 400（11148）。

    第 3 点不是理论风险：上游对孤儿 tool_call 是硬拒绝，而且这段坏历史会
    被客户端每轮重放，导致会话彻底死掉。
    """
    if not messages:
        return []
    rows = [dict(message) for message in messages if isinstance(message, dict)]
    head: list[dict] = []
    body = rows
    if rows and rows[0].get('role') == 'system':
        head = [rows[0]]
        body = rows[1:]

    kept: list[dict] = []
    used = sum(_message_tokens(message) for message in head)
    for message in reversed(body):
        cost = _message_tokens(message)
        if kept and used + cost > budget:
            break
        kept.append(message)
        used += cost
    kept.reverse()

    # 切点若把「assistant + 它的 tool 结果」拆开，就往前挪到成对的位置。
    while kept and kept[0].get('role') == 'tool':
        kept.pop(0)
    # 尾部若是一个「发起工具调用但还没有结果」的 assistant，丢掉它：
    # 那是上一轮异常中断留下的半截状态。
    while kept and kept[-1].get('role') == 'assistant' and kept[-1].get('tool_calls'):
        kept.pop()

    return head + kept


def summarize_dropped(messages, kept) -> str:
    """给「被裁掉的历史」留一句可读摘要。

    直接丢弃会让模型完全忘记之前聊过什么（用户会看到「你刚才说的那个」
    突然答不上来）。这里只保留极轻量的线索：轮次数 + 对话里的前几个
    主题词，成本约几十 token。
    """
    dropped = len(messages) - len(kept)
    if dropped <= 0:
        return ''
    hints: list[str] = []
    for message in messages:
        if message.get('role') != 'user' or not isinstance(message.get('content'), str):
            continue
        text = message['content'].strip()
        if not text:
            continue
        hints.append(text[:24])
        if len(hints) >= 3:
            break
    line = f'（此前还有 {dropped} 条更早的消息已从上下文移除'
    if hints:
        line += '，其中用户提过：' + ' / '.join(hints)
    return line + '）'


# ---------------------------------------------------------------------------
# 工具注册与调度
# ---------------------------------------------------------------------------

class Tool:
    """一个可被模型调用的工具。

    `parameters` 必须是 OpenAI function-calling 的 JSON Schema 片段。
    `run(**kwargs)` 返回可 JSON 序列化的 dict；失败请返回
    `{"ok": False, "error": "..."}` 而不是抛异常。
    """

    def __init__(self, name: str, description: str, parameters: dict, run, label: str = ''):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.run = run
        self.label = label or name

    def spec(self) -> dict:
        return {
            'type': 'function',
            'function': {
                'name': self.name,
                'description': self.description,
                'parameters': self.parameters,
            },
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def add(self, tool: Tool) -> Tool:
        self._tools[tool.name] = tool
        return tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def specs(self, names=None) -> list[dict]:
        if names is None:
            return [tool.spec() for tool in self._tools.values()]
        return [self._tools[name].spec() for name in names if name in self._tools]

    def names(self) -> list[str]:
        return list(self._tools)


def _clip(value: str, limit: int = TOOL_RESULT_LIMIT) -> str:
    text = value if isinstance(value, str) else str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + f'…（内容过长，已截断，共 {len(text)} 字符）'


def parse_tool_arguments(raw) -> tuple[dict, str]:
    """解析模型给出的 arguments。

    模型经常返回三种坏形态，都必须兜住：
      * 空字符串 / None；
      * 双重编码的 JSON 字符串（`"{\\"prompt\\":...}"`）；
      * 完全不合法的文本。
    解析失败返回 (空 dict, 原因)，让调用方把原因回给模型而不是崩掉。
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return {}, '模型没有提供参数'
    value = raw
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError) as exc:
            return {}, f'参数不是合法 JSON：{exc}'
    if isinstance(value, str):
        # 双重编码：再解一次
        try:
            value = json.loads(value)
        except (ValueError, TypeError) as exc:
            return {}, f'参数字符串内层不是合法 JSON：{exc}'
    if not isinstance(value, dict):
        return {}, f'参数必须是 JSON 对象，收到 {type(value).__name__}'
    return value, ''


class AgentContext:
    """一次请求内传给工具的上下文（谁在调用、属于哪个会话）。"""

    def __init__(self, *, api_key_id: int, conversation_id: str, mode: str,
                 model: str, on_event=None, cancelled=None):
        self.api_key_id = api_key_id
        self.conversation_id = conversation_id
        self.mode = mode
        self.model = model
        self.on_event = on_event or (lambda event: None)
        self.cancelled = cancelled or (lambda: False)

    def emit(self, event: dict) -> None:
        if self.cancelled():
            return
        try:
            self.on_event(event)
        except Exception:
            _logger.warning('智能体事件回调失败', exc_info=True)


def dispatch_tool(registry: ToolRegistry, name: str, arguments, context: AgentContext) -> dict:
    """执行一次工具调用，**永不抛异常**。

    失败必须变成工具结果回给模型，否则一次工具错误就会终结整个对话，
    用户体验等同于「页面报错」。异常只写日志。
    """
    tool = registry.get(name)
    if tool is None:
        return {'ok': False, 'error': f'没有名为 {name} 的工具',
                'available': registry.names()}
    params, reason = parse_tool_arguments(arguments)
    if reason:
        return {'ok': False, 'error': reason, 'tool': name}
    started = time.perf_counter()
    context.emit({'type': 'tool_start', 'tool': name, 'label': tool.label, 'args': params})
    try:
        result = tool.run(context=context, **params)
        if not isinstance(result, dict):
            result = {'ok': True, 'result': result}
    except TypeError as exc:
        # 参数名对不上是最常见的模型错误，单独给可读提示。
        result = {'ok': False, 'error': f'工具 {name} 的参数不匹配：{exc}'}
    except Exception as exc:
        _logger.exception('智能体工具 %s 执行失败', name)
        result = {'ok': False, 'error': f'工具 {name} 执行失败：{exc}'}
    elapsed = round((time.perf_counter() - started) * 1000)
    result.setdefault('ok', True)
    if tool.name not in ('list_models',):
        context.emit({'type': 'tool_end', 'tool': name, 'label': tool.label,
                      'ok': bool(result.get('ok')), 'ms': elapsed,
                      'summary': _tool_summary(result)})
    return result


def _tool_summary(result: dict) -> str:
    """给前端广播用的短摘要（不放全量结果，避免把大 JSON 推给浏览器）。"""
    if not isinstance(result, dict):
        return ''
    if not result.get('ok'):
        return str(result.get('error') or '失败')[:160]
    for key in ('summary', 'message', 'prompt'):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:160]
    return ''


def now_iso() -> str:
    return datetime.utcnow().isoformat(timespec='seconds') + 'Z'


def new_id(prefix: str) -> str:
    return prefix + '_' + uuid.uuid4().hex[:20]


def safe_filename(name: str, fallback: str = 'asset') -> str:
    """把任意字符串收敛成安全文件名（只留字母数字与 -_）。"""
    cleaned = re.sub(r'[^A-Za-z0-9._-]+', '-', str(name or '')).strip('.-')
    return (cleaned or fallback)[:80]


def asset_expiry() -> datetime:
    return datetime.utcnow() + timedelta(hours=ASSET_TTL_HOURS)


def conversation_expiry() -> datetime:
    return datetime.utcnow() + timedelta(hours=CONVERSATION_TTL_HOURS)