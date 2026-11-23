/**
 * S29 智能体：会话分组 / 搜索 / Markdown 净化 / 流式事件归并 / 历史消息映射 的单测。
 *
 * 数据形状照抄后端真实响应：
 *   · GET /api/agent/conversations → admin/routers/agent.py:_conversation_view（889 行）
 *   · POST /api/agent/chat SSE     → _run_turn（720–864 行）：meta/delta/reasoning/tool_start/tool_end/notice/media/done/error
 * 契约见 docs/ui-v2/contracts/agent.md。
 */
import { describe, expect, it } from 'vitest'
import {
  applyChatEvent,
  assetUrl,
  conversationTitle,
  emptyAssistantMessage,
  filterConversations,
  groupConversations,
  renderMarkdown,
  toChatMessages,
  userChatMessage,
  type AgentConversation,
  type AgentStoredMessage,
} from '@/api/agent'

function conversation(overrides: Partial<AgentConversation> & { conversation_id: string }): AgentConversation {
  return {
    title: '会话 ' + overrides.conversation_id,
    model: 'gpt-4o',
    mode: 'chat',
    reasoning: '',
    thinking: null,
    pinned: false,
    pinned_at: '',
    updated_at: '2026-11-23T04:00:00',
    created_at: '2026-11-23T04:00:00',
    ...overrides,
    conversation_id: overrides.conversation_id,
  }
}

const NOW = Date.parse('2026-11-23T05:00:00Z')

describe('groupConversations：置顶 / 今天 / 更早', () => {
  it('按北京时间自然日分组，置顶优先', () => {
    const groups = groupConversations(
      [
        conversation({ conversation_id: 'a', updated_at: '2026-11-23T04:00:00' }),
        conversation({ conversation_id: 'b', updated_at: '2026-11-20T04:00:00' }),
        conversation({ conversation_id: 'c', pinned: true, updated_at: '2026-11-01T04:00:00' }),
      ],
      NOW,
    )
    expect(groups.map((group) => group.key)).toEqual(['pinned', 'today', 'earlier'])
    expect(groups[0].items.map((item) => item.conversation_id)).toEqual(['c'])
    expect(groups[1].items.map((item) => item.conversation_id)).toEqual(['a'])
    expect(groups[2].items.map((item) => item.conversation_id)).toEqual(['b'])
  })

  it('空列表返回空分组；只有今天时只保留今天', () => {
    expect(groupConversations([], NOW)).toEqual([])
    const groups = groupConversations([conversation({ conversation_id: 'x' })], NOW)
    expect(groups).toHaveLength(1)
    expect(groups[0].label).toBe('今天')
  })
})

describe('filterConversations：标题搜索', () => {
  const items = [
    conversation({ conversation_id: 'a', title: '生成城市夜景视频' }),
    conversation({ conversation_id: 'b', title: 'Write a poem' }),
  ]

  it('空关键词返回全部；大小写不敏感', () => {
    expect(filterConversations(items, '')).toHaveLength(2)
    expect(filterConversations(items, 'POEM').map((item) => item.conversation_id)).toEqual(['b'])
    expect(filterConversations(items, '夜景').map((item) => item.conversation_id)).toEqual(['a'])
    expect(filterConversations(items, '不存在')).toEqual([])
  })
})

describe('renderMarkdown：marked + DOMPurify', () => {
  it('渲染粗体、行内代码与代码块', () => {
    const html = renderMarkdown('**粗体** 和 `code`\n\n```js\nconst a = 1\n```')
    expect(html).toContain('<strong>粗体</strong>')
    expect(html).toContain('<code>code</code>')
    expect(html).toContain('<pre><code')
    expect(html).toContain('const a = 1')
  })

  it('剥离 script / onerror / javascript: 链接', () => {
    const html = renderMarkdown('<script>alert(1)</script><img src=x onerror="alert(1)"><a href="javascript:alert(1)">x</a>')
    expect(html.toLowerCase()).not.toContain('<script')
    expect(html.toLowerCase()).not.toContain('onerror')
    expect(html.toLowerCase()).not.toContain('javascript:')
  })

  it('空文本返回空串', () => {
    expect(renderMarkdown('')).toBe('')
    expect(renderMarkdown('   ')).toBe('')
  })
})

describe('applyChatEvent：SSE 事件归并（纯函数）', () => {
  it('delta / reasoning 增量拼接；done 收尾并记录积分', () => {
    let message = emptyAssistantMessage()
    expect(message.streaming).toBe(true)
    message = applyChatEvent(message, { type: 'delta', text: '你' })
    message = applyChatEvent(message, { type: 'delta', text: '好' })
    message = applyChatEvent(message, { type: 'reasoning', text: '先想' })
    message = applyChatEvent(message, { type: 'done', credits: 3.5 })
    expect(message.content).toBe('你好')
    expect(message.reasoning).toBe('先想')
    expect(message.credits).toBe(3.5)
    expect(message.streaming).toBe(false)
  })

  it('tool_start / tool_end 配对更新状态与摘要', () => {
    let message = emptyAssistantMessage()
    message = applyChatEvent(message, { type: 'tool_start', tool: 'generate_image', label: '生成图片' })
    expect(message.tools).toEqual([{ tool: 'generate_image', label: '生成图片', status: 'running', summary: '' }])
    message = applyChatEvent(message, {
      type: 'tool_end',
      tool: 'generate_image',
      label: '生成图片',
      ok: true,
      ms: 1200,
      summary: '已生成 1 张图片',
    })
    expect(message.tools[0]).toMatchObject({ status: 'ok', summary: '已生成 1 张图片', ms: 1200 })
  })

  it('失败工具标 failed；没有 tool_start 的 tool_end 也会补一条', () => {
    let message = applyChatEvent(emptyAssistantMessage(), {
      type: 'tool_end',
      tool: 'generate_video',
      label: '生成视频',
      ok: false,
      summary: '上游超时',
    })
    expect(message.tools).toHaveLength(1)
    expect(message.tools[0]).toMatchObject({ status: 'failed', summary: '上游超时' })
  })

  it('notice / media / error 都落到消息上', () => {
    let message = emptyAssistantMessage()
    message = applyChatEvent(message, { type: 'notice', text: '正在生成视频…' })
    message = applyChatEvent(message, {
      type: 'media',
      asset: { asset_id: 'a1', kind: 'image', model: 'img-1', credits: 2, prompt: '猫' },
    })
    message = applyChatEvent(message, { type: 'error', message: '模型调用失败' })
    expect(message.notices).toEqual(['正在生成视频…'])
    expect(message.assets).toHaveLength(1)
    expect(message.assets[0].asset_id).toBe('a1')
    expect(message.error).toBe('模型调用失败')
    expect(message.streaming).toBe(false)
  })

  it('不修改入参（Vue 依赖对象身份更新）', () => {
    const original = emptyAssistantMessage()
    const next = applyChatEvent(original, { type: 'delta', text: 'abc' })
    expect(original.content).toBe('')
    expect(next.content).toBe('abc')
    expect(next).not.toBe(original)
  })
})

describe('toChatMessages：历史消息映射', () => {
  const stored: AgentStoredMessage[] = [
    { role: 'system', content: '你是助手' },
    { role: 'user', content: '你好' },
    { role: 'assistant', content: '在的', reasoning_content: '想想', usage: { credits: 1.25 } },
    { role: 'tool', content: '{"ok":true}' },
  ]

  it('只保留 user/assistant，带思考与积分', () => {
    const messages = toChatMessages(stored)
    expect(messages).toHaveLength(2)
    expect(messages[0]).toMatchObject({ role: 'user', content: '你好' })
    expect(messages[1]).toMatchObject({ role: 'assistant', content: '在的', reasoning: '想想', credits: 1.25 })
  })

  it('空 / null 输入返回空数组', () => {
    expect(toChatMessages(null)).toEqual([])
    expect(toChatMessages([])).toEqual([])
  })
})

describe('杂项：标题 / 资产地址 / 用户消息', () => {
  it('空标题回「新会话」；资产 id 需要 URL 编码', () => {
    expect(conversationTitle(conversation({ conversation_id: 'a', title: '' }))).toBe('新会话')
    expect(assetUrl('a b/c')).toBe('/api/agent/assets/a%20b%2Fc')
  })

  it('用户消息带附件名，assistant 初始为 streaming', () => {
    const message = userChatMessage('看图', ['a.png'])
    expect(message).toMatchObject({ role: 'user', content: '看图', attachments: ['a.png'], streaming: false })
    expect(emptyAssistantMessage().streaming).toBe(true)
  })
})