/**
 * 公开注册入口（/openentry）的接口与数据转换。
 *
 * 契约：docs/ui-v2/contracts/openentry.md
 * 后端：
 *   GET  /api/join/gate/status      admin/routers/join_gate.py:gate_status
 *   POST /api/join/gate/toggle      join_gate.py:gate_toggle → admin/join_gate.py:toggle
 *   POST /api/join/gate/tickets     join_gate.py:gate_tickets（明文只返回一次）
 *   GET  /api/open/admin/config     admin/public_auth.py:open_admin_config
 *   POST /api/open/admin/config     public_auth.py:open_admin_config_save
 *   GET  /api/open/admin/tickets    public_auth.py:open_admin_tickets（只回 jti）
 *
 * 三条口径：
 *   1. 开关是「代次」语义：每次 toggle 旧链接与邀请码全部作废，确认框必须写明；
 *   2. 单条链接无法撤销（后端只有整代失效），页面不伪造；
 *   3. 文案上限与后端 _sanitize_copy 完全一致，前端先拦并指行号。
 */
import { http } from './http'

/* -------------------------------------------------------------------------- */
/* 后端形状                                                                    */
/* -------------------------------------------------------------------------- */

export interface GateStatus {
  open: boolean
  epoch: number
  active_tickets: number
  active_invites: number
  /** 0 = 永久。 */
  ticket_ttl: number
  access_ttl: number
}

export interface GateToggleResult {
  ok: boolean
  open: boolean
  epoch: number
  revoked_tickets?: number
  revoked_invites?: number
}

export interface IssueTicketsPayload {
  ok: boolean
  expires_in: number
  urls: string[]
  warning: string
}

export interface OpenPlatform {
  id: string
  label: string
  modes: string[]
  order: number
  enabled: boolean
}

export interface PageCopy {
  title: string
  sub: string
  intro: string[]
  foot: string
}

export interface OpenConfigPayload {
  ok: boolean
  platforms: OpenPlatform[]
  ticket_ttl: number
  ttl_range: [number, number]
  copy: PageCopy
}

export interface OpenTicket {
  code: string
  permanent: boolean
  /** 剩余秒数（permanent 时为 0）。 */
  expires_in: number
}

export interface AdminTicketsPayload {
  ok: boolean
  open: boolean
  epoch: number
  count: number
  tickets: OpenTicket[]
}

/* -------------------------------------------------------------------------- */
/* 接口                                                                        */
/* -------------------------------------------------------------------------- */

export function getGateStatus(signal?: AbortSignal): Promise<GateStatus> {
  return http<GateStatus>('GET', '/api/join/gate/status', { signal })
}

export function toggleGate(enable: boolean): Promise<GateToggleResult> {
  return http<GateToggleResult>('POST', '/api/join/gate/toggle', { body: { enable } })
}

export function issueTickets(count = 1): Promise<IssueTicketsPayload> {
  return http<IssueTicketsPayload>('POST', '/api/join/gate/tickets', { body: { count } })
}

export function getOpenConfig(signal?: AbortSignal): Promise<OpenConfigPayload> {
  return http<OpenConfigPayload>('GET', '/api/open/admin/config', { signal })
}

export function saveOpenConfig(body: {
  platforms?: Record<string, boolean>
  ticket_ttl?: number
  page_copy?: Partial<PageCopy>
}): Promise<OpenConfigPayload> {
  return http<OpenConfigPayload>('POST', '/api/open/admin/config', { body })
}

export function getAdminTickets(signal?: AbortSignal): Promise<AdminTicketsPayload> {
  return http<AdminTicketsPayload>('GET', '/api/open/admin/tickets', { signal })
}

/* -------------------------------------------------------------------------- */
/* 纯函数                                                                      */
/* -------------------------------------------------------------------------- */

/** 秒 → 人话（0 = 永久；≥1 天按天、≥1 小时按小时、其余按分钟）。 */
export function ttlLabel(seconds: unknown): string {
  const n = Number(seconds)
  if (!Number.isFinite(n) || n <= 0) return '永久'
  if (n >= 86400) return Math.round(n / 86400) + ' 天'
  if (n >= 3600) return Math.round(n / 3600) + ' 小时'
  return Math.max(1, Math.round(n / 60)) + ' 分钟'
}

/** 链接时效下拉（与旧版一致的五档 + 永久）。 */
export const TTL_OPTIONS: Array<{ label: string; value: string }> = [
  { label: '永久', value: '0' },
  { label: '1 小时', value: '3600' },
  { label: '6 小时', value: '21600' },
  { label: '24 小时', value: '86400' },
  { label: '3 天', value: '259200' },
  { label: '7 天', value: '604800' },
]

/** 链接 = origin + '/j#k=' + jti（后端列表只回 jti，与旧版拼法一致）。 */
export function buildTicketUrl(code: string, origin: string): string {
  const base = String(origin || '').replace(/\/+$/, '')
  return base + '/j#k=' + String(code || '').trim()
}

/** 链接状态文案：永久有效 / 剩余 X。 */
export function ticketExpiryText(ticket: OpenTicket | null | undefined): string {
  if (!ticket) return '—'
  if (ticket.permanent) return '永久有效'
  return '剩余 ' + ttlLabel(ticket.expires_in)
}

/** 入口状态：Badge 文案与语气。 */
export function gateStateOf(status: GateStatus | null | undefined): {
  label: string
  tone: 'ok' | 'neutral'
} {
  return status?.open ? { label: '已开放', tone: 'ok' } : { label: '已关闭', tone: 'neutral' }
}

/** 「代次 7 · 可用票据 3 · 有效邀请码 1」。 */
export function gateMetaText(status: GateStatus | null | undefined): string {
  if (!status) return ''
  return (
    '代次 ' + Number(status.epoch || 0) +
    ' · 可用票据 ' + Number(status.active_tickets || 0) +
    ' · 有效邀请码 ' + Number(status.active_invites || 0)
  )
}

/** 「已开放 3 / 5 · 点击切换，立即生效」。 */
export function platformFlagSummary(platforms: OpenPlatform[] | null | undefined): string {
  const list = platforms || []
  const on = list.filter((item) => item.enabled).length
  return '已开放 ' + on + ' / ' + list.length + ' · 切换立即生效'
}

/** 文案草稿 → 提交形状（去首尾空白、丢空行，说明最多 6 行）。 */
export function normalizeCopy(copy: Partial<PageCopy> | null | undefined): PageCopy {
  return {
    title: String(copy?.title || '').trim(),
    sub: String(copy?.sub || '').trim(),
    intro: (copy?.intro || []).map((line) => String(line || '').trim()).filter(Boolean).slice(0, 6),
    foot: String(copy?.foot || '').trim(),
  }
}

export interface CopyLimits {
  title: number
  sub: number
  introLines: number
  introLine: number
  foot: number
}

/** 与后端 _sanitize_copy 完全一致的上限。 */
export const COPY_LIMITS: CopyLimits = { title: 40, sub: 120, introLines: 6, introLine: 200, foot: 160 }

/** 文案校验：返回错误文案（含行号），空串 = 通过。 */
export function copyValidation(copy: Partial<PageCopy> | null | undefined): string {
  const value = normalizeCopy(copy)
  if (value.title.length > COPY_LIMITS.title) return '标题不能超过 ' + COPY_LIMITS.title + ' 字'
  if (value.sub.length > COPY_LIMITS.sub) return '副标题不能超过 ' + COPY_LIMITS.sub + ' 字'
  if (value.foot.length > COPY_LIMITS.foot) return '页脚不能超过 ' + COPY_LIMITS.foot + ' 字'
  const rawLines = (copy?.intro || []).map((line) => String(line || '').trim()).filter(Boolean)
  if (rawLines.length > COPY_LIMITS.introLines) {
    return '说明最多 ' + COPY_LIMITS.introLines + ' 行，请合并后再保存'
  }
  for (let index = 0; index < rawLines.length; index += 1) {
    if (rawLines[index].length > COPY_LIMITS.introLine) {
      return '说明第 ' + (index + 1) + ' 行超过 ' + COPY_LIMITS.introLine + ' 字'
    }
  }
  return ''
}

/** 预览用的行（标题/副标题/说明/页脚），空值给占位提示。 */
export function copyPreviewLines(copy: Partial<PageCopy> | null | undefined): string[] {
  const value = normalizeCopy(copy)
  const lines: string[] = []
  lines.push(value.title || '（未填标题，使用默认）')
  if (value.sub) lines.push(value.sub)
  for (const line of value.intro) lines.push(line)
  if (value.foot) lines.push(value.foot)
  return lines
}
