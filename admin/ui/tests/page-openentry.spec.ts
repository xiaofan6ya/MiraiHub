import { describe, expect, it } from 'vitest'
import {
  COPY_LIMITS,
  TTL_OPTIONS,
  buildTicketUrl,
  copyPreviewLines,
  copyValidation,
  gateMetaText,
  gateStateOf,
  normalizeCopy,
  platformFlagSummary,
  ticketExpiryText,
  ttlLabel,
  type GateStatus,
  type OpenPlatform,
  type OpenTicket,
} from '@/api/openentry'

/**
 * 契约 docs/ui-v2/contracts/openentry.md 的数据转换。
 * 测试数据按后端真实响应形状造：
 *   GET /api/join/gate/status      → admin/routers/join_gate.py:gate_status
 *   GET /api/open/admin/config     → admin/public_auth.py:open_admin_config
 *   GET /api/open/admin/tickets    → public_auth.py:open_admin_tickets
 */

function gate(overrides: Partial<GateStatus> = {}): GateStatus {
  return { open: true, epoch: 7, active_tickets: 3, active_invites: 1, ticket_ttl: 86400, access_ttl: 3600, ...overrides }
}

function platform(overrides: Partial<OpenPlatform> = {}): OpenPlatform {
  return { id: 'workbuddy', label: 'WorkBuddy', modes: ['sms'], order: 10, enabled: true, ...overrides }
}

describe('链接时效文案 ttlLabel', () => {
  it('0 = 永久；天/小时/分钟三档', () => {
    expect(ttlLabel(0)).toBe('永久')
    expect(ttlLabel(null)).toBe('永久')
    expect(ttlLabel(604800)).toBe('7 天')
    expect(ttlLabel(86400)).toBe('1 天')
    expect(ttlLabel(21600)).toBe('6 小时')
    expect(ttlLabel(3600)).toBe('1 小时')
    expect(ttlLabel(90)).toBe('2 分钟')
  })

  it('下拉档位与旧版一致（永久 + 1h/6h/24h/3d/7d）', () => {
    expect(TTL_OPTIONS.map((item) => item.value)).toEqual(['0', '3600', '21600', '86400', '259200', '604800'])
  })
})

describe('访问链接：拼接与状态', () => {
  it('origin + /j#k= + jti（去掉末尾斜杠）', () => {
    expect(buildTicketUrl('Ab3dE9xQ2k', 'https://hub.example.com')).toBe('https://hub.example.com/j#k=Ab3dE9xQ2k')
    expect(buildTicketUrl(' Ab3d ', 'https://hub.example.com/')).toBe('https://hub.example.com/j#k=Ab3d')
  })

  it('永久 / 剩余有效期', () => {
    const permanent: OpenTicket = { code: 'x', permanent: true, expires_in: 0 }
    const temp: OpenTicket = { code: 'y', permanent: false, expires_in: 82800 }
    expect(ticketExpiryText(permanent)).toBe('永久有效')
    expect(ticketExpiryText(temp)).toBe('剩余 23 小时')
    expect(ticketExpiryText(null)).toBe('—')
  })
})

describe('入口状态 / 元信息 / 平台摘要', () => {
  it('状态 Badge 与代次信息', () => {
    expect(gateStateOf(gate())).toEqual({ label: '已开放', tone: 'ok' })
    expect(gateStateOf(gate({ open: false }))).toEqual({ label: '已关闭', tone: 'neutral' })
    expect(gateStateOf(null)).toEqual({ label: '已关闭', tone: 'neutral' })
    expect(gateMetaText(gate())).toBe('第 7 轮 · 有效链接 3 条 · 有效邀请码 1 个')
    expect(gateMetaText(null)).toBe('')
  })

  it('平台开关摘要', () => {
    const platforms = [platform(), platform({ id: 'loomy', label: 'Loomy', enabled: false }), platform({ id: 'raccoon', enabled: true })]
    expect(platformFlagSummary(platforms)).toBe('已开放 2 / 3 · 切换立即生效')
    expect(platformFlagSummary(null)).toBe('已开放 0 / 0 · 切换立即生效')
  })
})

describe('公开页文案：归一 / 校验 / 预览', () => {
  it('归一：去首尾空白、丢空行、最多 6 行', () => {
    const copy = normalizeCopy({
      title: '  MiraiHub 注册 ',
      sub: ' 内测 ',
      intro: [' 第一行 ', '', '   ', '第二行', ...Array.from({ length: 8 }, (_, i) => 'line' + i)],
      foot: ' 页脚 ',
    })
    expect(copy.title).toBe('MiraiHub 注册')
    expect(copy.intro.slice(0, 2)).toEqual(['第一行', '第二行'])
    expect(copy.intro).toHaveLength(COPY_LIMITS.introLines)
  })

  it('校验：标题/副标题/页脚长度与说明行数/单行长度', () => {
    expect(copyValidation({ title: 'ok', sub: '', intro: [], foot: '' })).toBe('')
    expect(copyValidation({ title: 'x'.repeat(COPY_LIMITS.title + 1) })).toContain('标题不能超过')
    expect(copyValidation({ title: 'ok', sub: 'x'.repeat(COPY_LIMITS.sub + 1) })).toContain('副标题不能超过')
    expect(copyValidation({ title: 'ok', foot: 'x'.repeat(COPY_LIMITS.foot + 1) })).toContain('页脚不能超过')
    expect(
      copyValidation({ title: 'ok', intro: Array.from({ length: COPY_LIMITS.introLines + 1 }, () => 'line') }),
    ).toContain('最多 6 行')
    expect(copyValidation({ title: 'ok', intro: ['a', 'x'.repeat(COPY_LIMITS.introLine + 1)] })).toBe(
      '说明第 2 行超过 200 字',
    )
  })

  it('预览行：标题缺省给占位，空行不渲染', () => {
    expect(copyPreviewLines({})).toEqual(['（未填标题，使用默认）'])
    expect(copyPreviewLines({ title: '标题', sub: '副标题', intro: ['一', '二'], foot: '脚' })).toEqual([
      '标题', '副标题', '一', '二', '脚',
    ])
  })
})
