/**
 * 智能体（/agent）的接口、SSE 事件与数据转换。
 *
 * 契约：docs/ui-v2/contracts/agent.md（本文件首次交付）
 * 设计稿：docs/ui-v2/06-pages-d.md P21
 * 后端：admin/routers/agent.py（bootstrap / chat(SSE) / conversations / assets / estimate / merge）
 *       admin/agent.py（工具事件 tool_start / tool_end）
 *
 * 事件顺序（见 agent.py:_run_turn）：
 *   meta → reasoning* → delta* → tool_start → tool_end → media? → notice* → … → done | error
 * SSE 帧只有 data: 行（没有 event: 名），由 http.ts:stream 解析成 JSON。
 */
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { http, stream } from './http'
import { beijingParts, toDate } from '@/utils/format'

/* -------------------------------------------------------------------------- */
/* 后端形状                                                                     */
/* -------------------------------------------------------------------------- */

export interface AgentKeyInfo {
  id: number
  name: string
  unlimited: boolean
  credit_limit: number
  credit_used: number
}

export interface AgentVideoModel {
  id: string
  /** 秒数上限等由后端目录给出；未知字段原样保留。 */
  [key: string]: unknown
}

export interface AgentPreset {
  id: string
  label: string
  prompt: string
  size?: string
  seconds?: number
  resolution?: string
  aspect_ratio?: string
}

export interface AgentBootstrap {
  key: AgentKeyInfo | null
  models: { chat: string[]; image: string[]; video: string[] }
  video: AgentVideoModel[]
  estimates: {
    video_per_second?: number
    video_factors?: Record<string, number>
    image_per_unit?: number
  }
  merge?: Record<string, unknown>
  presets: { image?: AgentPreset[]; video?: AgentPreset[] }
  limits?: { max_tool_steps?: number; context_budget?: number }
}

export interface AgentUsage {
  credits?: number
  prompt_tokens?: number
  completion_tokens?: number
  total_tokens?: number
}

export interface AgentStoredMessage {
  role: string
  content?: string | null
  reasoning_content?: string | null
  tool_calls?: unknown[]
  usage?: AgentUsage
}

export interface AgentContextUsage {
  used_tokens: number
  budget_tokens: number
  percent: number
  messages: number
}

export interface AgentConversation {
  conversation_id: string
  title: string
  model: string
  mode: string
  reasoning: string
  thinking: boolean | null
  pinned: boolean
  pinned_at: string
  updated_at: string
  created_at: string
  messages?: AgentStoredMessage[]
  context?: AgentContextUsage
}

export interface AgentAsset {
  asset_id: string
  kind: 'image' | 'video' | string
  model: string
  bytes?: number
  duration_s?: number
  credits?: number
  prompt?: string
  created_at?: string
}

/* -------------------------------------------------------------------------- */
/* SSE 事件                                                                     */
/* -------------------------------------------------------------------------- */

export type AgentStreamEvent =
  | { type: 'meta'; conversation_id?: string; model?: string; mode?: string; reasoning?: string; thinking?: boolean | null; context?: AgentContextUsage; tools?: string[] }
  | { type: 'delta'; text?: string }
  | { type: 'reasoning'; text?: string }
  | { type: 'tool_start'; tool?: string; label?: string; args?: Record<string, unknown> }
  | { type: 'tool_end'; tool?: string; label?: string; ok?: boolean; ms?: number; summary?: string }
  | { type: 'notice'; text?: string }
  | { type: 'media'; asset?: AgentAsset }
  | { type: 'done'; credits?: number; usage?: AgentUsage; steps?: number; conversation_id?: string; messages?: number; context?: AgentContextUsage }
  | { type: 'error'; message?: string }

export interface ChatToolTrace {
  tool: string
  label: string
  status: 'running' | 'ok' | 'failed'
  summary: string
  ms?: number
}

/** 页面上的一条消息（用户 / 助手）。助手消息带流式过程中的思考、工具、产物。 */
export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  reasoning: string
  tools: ChatToolTrace[]
  notices: string[]
  assets: AgentAsset[]
  credits: number
  streaming: boolean
  error: string
  /** 附件只作为文本标注随消息发送（后端 /chat 没有附件字段）。 */
  attachments: string[]
}

export function emptyAssistantMessage(): ChatMessage {
  return {
    role: 'assistant',
    content: '',
    reasoning: '',
    tools: [],
    notices: [],
    assets: [],
    credits: 0,
    streaming: true,
    error: '',
    attachments: [],
  }
}

export function userChatMessage(content: string, attachments: string[] = []): ChatMessage {
  return {
    role: 'user',
    content,
    reasoning: '',
    tools: [],
    notices: [],
    assets: [],
    credits: 0,
    streaming: false,
    error: '',
    attachments,
  }
}

/** 历史消息（后端落库的 role/content/reasoning_content/usage）→ 页面消息。 */
export function toChatMessages(stored: AgentStoredMessage[] | null | undefined): ChatMessage[] {
  const out: ChatMessage[] = []
  for (const item of stored || []) {
    if (!item || (item.role !== 'user' && item.role !== 'assistant')) continue
    out.push({
      role: item.role,
      content: String(item.content || ''),
      reasoning: String(item.reasoning_content || ''),
      tools: [],
      notices: [],
      assets: [],
      credits: Number(item.usage?.credits) || 0,
      streaming: false,
      error: '',
      attachments: [],
    })
  }
  return out
}

/**
 * 流式事件 → 新的助手消息（纯函数：页面每收到一帧就替换最后一条）。
 *
 * 不修改入参：Vue 依赖对象身份变化触发更新，原地改字段在深层 watcher 里容易漏。
 */
export function applyChatEvent(message: ChatMessage, event: AgentStreamEvent): ChatMessage {
  const next: ChatMessage = {
    ...message,
    tools: message.tools.map((tool) => ({ ...tool })),
    notices: message.notices.slice(),
    assets: message.assets.slice(),
  }
  switch (event.type) {
    case 'delta':
      next.content += String(event.text || '')
      break
    case 'reasoning':
      next.reasoning += String(event.text || '')
      break
    case 'tool_start': {
      const tool = String(event.tool || 'tool')
      next.tools.push({
        tool,
        label: String(event.label || tool),
        status: 'running',
        summary: '',
      })
      break
    }
    case 'tool_end': {
      const tool = String(event.tool || 'tool')
      let trace = [...next.tools].reverse().find((item) => item.tool === tool && item.status === 'running')
      if (!trace) {
        trace = { tool, label: String(event.label || tool), status: 'running', summary: '' }
        next.tools.push(trace)
      }
      trace.status = event.ok === false ? 'failed' : 'ok'
      trace.summary = String(event.summary || '')
      trace.ms = Number(event.ms) || undefined
      break
    }
    case 'notice':
      if (event.text) next.notices.push(String(event.text))
      break
    case 'media':
      if (event.asset) next.assets.push({ ...event.asset })
      break
    case 'done':
      next.streaming = false
      next.credits = Number(event.credits) || next.credits
      break
    case 'error':
      next.streaming = false
      next.error = String(event.message || '模型调用失败')
      break
    default:
      break
  }
  return next
}

/* -------------------------------------------------------------------------- */
/* 接口                                                                         */
/* -------------------------------------------------------------------------- */

export function getBootstrap(signal?: AbortSignal): Promise<AgentBootstrap> {
  return http<AgentBootstrap>('GET', '/api/agent/bootstrap', signal ? { signal } : {})
}

export function listConversations(limit = 40, signal?: AbortSignal): Promise<{ items: AgentConversation[] }> {
  return http('GET', '/api/agent/conversations', { query: { limit }, signal })
}

export function getConversation(id: string, signal?: AbortSignal): Promise<AgentConversation> {
  return http<AgentConversation>('GET', '/api/agent/conversations/' + encodeURIComponent(id), {
    signal,
  })
}

export function patchConversation(
  id: string,
  body: { title?: string; pinned?: boolean; model?: string; mode?: string; reasoning?: string; thinking?: boolean | null },
): Promise<AgentConversation & { ok: boolean }> {
  return http('PATCH', '/api/agent/conversations/' + encodeURIComponent(id), { body })
}

export function deleteConversation(id: string): Promise<{ ok: boolean }> {
  return http('DELETE', '/api/agent/conversations/' + encodeURIComponent(id))
}

export function listAssets(kind = '', limit = 60, signal?: AbortSignal): Promise<{ items: AgentAsset[] }> {
  return http('GET', '/api/agent/assets', { query: { kind, limit }, signal })
}

export function assetUrl(assetId: string): string {
  return '/api/agent/assets/' + encodeURIComponent(assetId)
}

export function estimateCredits(body: Record<string, unknown>): Promise<Record<string, unknown>> {
  return http('POST', '/api/agent/estimate', { body })
}

export interface MergeResult { ok?: boolean; asset_id?: string; summary?: string; [key: string]: unknown }

export function mergeAssets(assetIds: string[]): Promise<MergeResult> {
  return http<MergeResult>('POST', '/api/agent/merge', { body: { asset_ids: assetIds } })
}

export interface ChatRequestBody {
  message: string
  conversation_id?: string
  model?: string
  mode?: string
  reasoning?: string
  thinking?: boolean | null
  image?: Record<string, unknown>
  video?: Record<string, unknown>
}

/** 一轮对话：POST /api/agent/chat（SSE），每帧回调一次。 */
export function chatStream(
  body: ChatRequestBody,
  onEvent: (event: AgentStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  return stream<AgentStreamEvent>('/api/agent/chat', body, (event) => onEvent(event), signal)
}

/* -------------------------------------------------------------------------- */
/* 纯函数：会话分组 / 搜索 / Markdown                                           */
/* -------------------------------------------------------------------------- */

function dayKey(value: string | null | undefined): string {
  const date = toDate(value)
  if (!date) return ''
  const parts = beijingParts(date)
  return parts.y + '-' + String(parts.mo).padStart(2, '0') + '-' + String(parts.d).padStart(2, '0')
}

export interface ConversationGroup {
  key: 'pinned' | 'today' | 'earlier'
  label: string
  items: AgentConversation[]
}

/** 左栏分组：置顶 → 今天 → 更早（今天按北京时间自然日）。 */
export function groupConversations(
  items: AgentConversation[] | null | undefined,
  now: number = Date.now(),
): ConversationGroup[] {
  const list = (items || []).slice()
  const today = dayKey(new Date(now).toISOString())
  const groups: ConversationGroup[] = [
    { key: 'pinned', label: '置顶', items: [] },
    { key: 'today', label: '今天', items: [] },
    { key: 'earlier', label: '更早', items: [] },
  ]
  for (const item of list) {
    if (item.pinned) groups[0].items.push(item)
    else if (dayKey(item.updated_at || item.created_at) === today) groups[1].items.push(item)
    else groups[2].items.push(item)
  }
  return groups.filter((group) => group.items.length > 0)
}

/** 左栏搜索：标题大小写不敏感。 */
export function filterConversations(
  items: AgentConversation[] | null | undefined,
  keyword: string,
): AgentConversation[] {
  const text = String(keyword || '').trim().toLowerCase()
  if (!text) return (items || []).slice()
  return (items || []).filter((item) =>
    String(item.title || '').toLowerCase().includes(text) ||
    String(item.conversation_id || '').toLowerCase().includes(text),
  )
}

marked.setOptions({ gfm: true, breaks: true })

/** Markdown → 安全 HTML（marked 渲染后必须过 DOMPurify）。 */
export function renderMarkdown(text: string): string {
  const raw = String(text || '')
  if (!raw.trim()) return ''
  let html = ''
  try {
    html = marked.parse(raw, { async: false }) as string
  } catch {
    return ''
  }
  return DOMPurify.sanitize(html, {
    FORBID_TAGS: ['style', 'iframe', 'form', 'input', 'button'],
    FORBID_ATTR: ['style', 'onerror', 'onload'],
  })
}

/** 会话标题（空标题回「新会话」）。 */
export function conversationTitle(item: AgentConversation): string {
  return String(item.title || '').trim() || '新会话'
}