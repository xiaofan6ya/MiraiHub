/**
 * 设置页（/settings）的接口与数据转换。
 *
 * 契约：docs/ui-v2/contracts/settings.md（末尾「## 实现对照（S28）」）
 * 后端：
 *   GET/PUT /api/runtime-config                 admin/routers/runtime_config.py + admin/runtime_settings.py
 *   GET/POST/PATCH/DELETE /api/proxies…         admin/routers/proxies.py
 *   GET/PUT /api/settings/invitation            admin/server.py:130/136 → admin/invitation.py
 *   PATCH   /api/admin/password                 admin/server.py:936
 *   /api/app-source/*                           admin/routers/app_source.py
 *
 * 三条口径：
 *   1. 运行配置以字段目录（CATALOG）为准：分组、类型、上下界、重启标记都由后端下发，前端不硬编码任何 attr 语义；
 *   2. 代理地址 API 永不返回明文（列表里是 ***），修改必须整段重填 —— 前端不要把打码值当输入提交；
 *   3. 密码规则前端只做「一致 + 最小长度」两件可提前发现的事，其余（旧密码是否正确）由后端 403 说明。
 */
import { http } from './http'
import { t as defaultTranslate, type Translate } from '@/i18n'

/* -------------------------------------------------------------------------- */
/* 运行配置                                                                    */
/* -------------------------------------------------------------------------- */

export interface RuntimeField {
  attr: string
  label: string
  group: string
  kind: 'bool' | 'int' | 'float' | 'str' | string
  desc: string
  min: number | null
  max: number | null
  step: number
  /** true = 值已持久化，但要重启才真正生效。 */
  restart: boolean
  value: unknown
  default: unknown
  overridden: boolean
}

export interface RuntimeConfigPayload {
  fields: RuntimeField[]
}

export interface RuntimeSaveResult {
  changed: string[]
  reverted: string[]
  errors: Record<string, string>
  fields: RuntimeField[]
}

export function getRuntimeConfig(signal?: AbortSignal): Promise<RuntimeConfigPayload> {
  return http<RuntimeConfigPayload>('GET', '/api/runtime-config', { signal })
}

/** 只提交改动项；value 为 null/空串 = 清除覆盖，回落 .env 默认。 */
export function saveRuntimeConfig(updates: Record<string, unknown>): Promise<RuntimeSaveResult> {
  return http<RuntimeSaveResult>('PUT', '/api/runtime-config', { body: { updates } })
}

export interface RuntimeGroup {
  group: string
  items: RuntimeField[]
}

/** 按后端 group 分组（保持 CATALOG 顺序，不排序、不合并）。 */
export function groupRuntimeFields(
  fields: RuntimeField[] | null | undefined,
  translate?: Translate,
): RuntimeGroup[] {
  const tr = translate ?? defaultTranslate
  const groups: RuntimeGroup[] = []
  for (const field of fields || []) {
    const name = field.group || tr('page.settings.runtime.otherGroup')
    let target = groups.find((item) => item.group === name)
    if (!target) {
      target = { group: name, items: [] }
      groups.push(target)
    }
    target.items.push(field)
  }
  return groups
}

/** 保存返回的 changed 里，有哪些是「重启后才生效」的字段。 */
export function restartPendingOf(
  fields: RuntimeField[] | null | undefined,
  changed: string[] | null | undefined,
): RuntimeField[] {
  const set = new Set(changed || [])
  return (fields || []).filter((field) => field.restart && set.has(field.attr))
}

/** 表单草稿 vs 服务端值：返回发生变化的 attr → 新值（空串保留，表示回落默认）。 */
export function diffRuntimeFields(
  fields: RuntimeField[] | null | undefined,
  draft: Record<string, string | number | boolean | null>,
): Record<string, unknown> {
  const updates: Record<string, unknown> = {}
  for (const field of fields || []) {
    if (!(field.attr in draft)) continue
    const next = draft[field.attr]
    const current = field.value
    if (next === null || next === undefined) continue
    const same =
      field.kind === 'bool'
        ? Boolean(next) === Boolean(current)
        : String(next) === (current === null || current === undefined ? '' : String(current))
    if (!same) updates[field.attr] = next
  }
  return updates
}

/* -------------------------------------------------------------------------- */
/* 出口代理节点                                                                */
/* -------------------------------------------------------------------------- */

export interface ProxyNode {
  id: number
  label: string
  /** 打码后的地址（user:***@host），env: 引用原样。 */
  url: string
  enabled: boolean
  max_concurrency: number
  pool: string
  blocked: boolean
  blocked_until: string | null
  last_ok_at: string | null
  last_error: string
  state: 'ready' | 'blocked' | 'disabled' | string
}

export interface ProxySummary {
  total: number
  ready: number
  blocked: number
  disabled: number
  slots: number
}

export interface ProxyListPayload {
  items: ProxyNode[]
  summary: ProxySummary
  pools: string[]
  note: string
}

export interface ProxyInput {
  label?: string
  url?: string
  enabled?: number
  max_concurrency?: number
  pool?: string
}

export function listProxies(signal?: AbortSignal): Promise<ProxyListPayload> {
  return http<ProxyListPayload>('GET', '/api/proxies', { signal })
}

export function createProxy(body: ProxyInput): Promise<{ ok: boolean; item: ProxyNode }> {
  return http<{ ok: boolean; item: ProxyNode }>('POST', '/api/proxies', { body })
}

export function patchProxy(id: number, body: ProxyInput): Promise<{ ok: boolean; item: ProxyNode }> {
  return http<{ ok: boolean; item: ProxyNode }>('PATCH', '/api/proxies/' + id, { body })
}

export function deleteProxy(id: number): Promise<{ ok: boolean; id: number }> {
  return http<{ ok: boolean; id: number }>('DELETE', '/api/proxies/' + id)
}

export function unblockProxy(id: number): Promise<{ ok: boolean; id: number }> {
  return http<{ ok: boolean; id: number }>('POST', '/api/proxies/' + id + '/unblock')
}

export const PROXY_STATE_LABELS: Record<string, string> = {
  ready: '可用',
  blocked: '静默中',
  disabled: '已停用',
}

export function proxyStateOf(
  node: ProxyNode | null | undefined,
  translate?: Translate,
): {
  label: string
  tone: 'ok' | 'warn' | 'neutral'
} {
  const tr = translate ?? defaultTranslate
  const state = String(node?.state || '')
  if (state === 'ready') return { label: tr('proxy.state.ready'), tone: 'ok' }
  if (state === 'blocked') return { label: tr('proxy.state.blocked'), tone: 'warn' }
  return { label: tr('proxy.state.disabled'), tone: 'neutral' }
}

/** 「共 N 个节点，可用 A，静默 B，停用 C · 可并发槽位 S」。 */
export function proxySummaryText(
  summary: ProxySummary | null | undefined,
  translate?: Translate,
): string {
  if (!summary) return ''
  return (translate ?? defaultTranslate)('proxy.summary', {
    total: Number(summary.total || 0),
    ready: Number(summary.ready || 0),
    blocked: Number(summary.blocked || 0),
    disabled: Number(summary.disabled || 0),
    slots: Number(summary.slots || 0),
  })
}

/** 按分组 / 关键字过滤节点（名称、地址、分组都参与匹配）。 */
export function filterProxies(
  nodes: ProxyNode[] | null | undefined,
  options: { pool?: string; q?: string } = {},
): ProxyNode[] {
  const pool = String(options.pool || '')
  const keyword = String(options.q || '').trim().toLowerCase()
  return (nodes || []).filter((node) => {
    if (pool && (node.pool || '') !== pool) return false
    if (!keyword) return true
    return (
      String(node.label || '').toLowerCase().includes(keyword) ||
      String(node.url || '').toLowerCase().includes(keyword) ||
      String(node.pool || '').toLowerCase().includes(keyword)
    )
  })
}

/** 新增节点表单校验（与后端一致：地址 8 字符起、并发 1–32）。 */
export function validateProxyInput(
  input: { url?: string; max_concurrency?: number },
  translate?: Translate,
): string {
  const tr = translate ?? defaultTranslate
  const url = String(input.url || '').trim()
  if (!url) return tr('settings.validation.urlRequired')
  if (url.length < 8) return tr('settings.validation.urlShort')
  if (url.includes('***')) return tr('settings.validation.urlMasked')
  if (!/^https?:\/\//i.test(url) && !/^socks5h?:\/\//i.test(url) && !url.startsWith('env:')) {
    return tr('settings.validation.urlScheme')
  }
  const conc = Number(input.max_concurrency ?? 1)
  if (!Number.isFinite(conc) || conc < 1 || conc > 32) return tr('settings.validation.concurrency')
  return ''
}

/* -------------------------------------------------------------------------- */
/* WorkBuddy 邀请绑定                                                          */
/* -------------------------------------------------------------------------- */

export interface InvitationPayload {
  invite_code: string
  invite_url: string
  enabled: boolean
}

export function getInvitation(signal?: AbortSignal): Promise<InvitationPayload> {
  return http<InvitationPayload>('GET', '/api/settings/invitation', { signal })
}

/** value 为空 = 停用（后端把覆盖值清空并回落未启用）。 */
export function saveInvitation(value: string): Promise<InvitationPayload> {
  return http<InvitationPayload>('PUT', '/api/settings/invitation', { body: { value } })
}

/* -------------------------------------------------------------------------- */
/* 修改登录密码                                                                */
/* -------------------------------------------------------------------------- */

export interface PasswordChangeResult {
  ok: boolean
  revoked_sessions?: number
}

export function changePassword(oldPassword: string, newPassword: string): Promise<PasswordChangeResult> {
  return http<PasswordChangeResult>('PATCH', '/api/admin/password', {
    body: { old_password: oldPassword, new_password: newPassword },
  })
}

/** 前端能提前发现的两件事：两次不一致、少于 6 位（后端规则同此）。 */
export function validatePasswordChange(
  oldPassword: string,
  newPassword: string,
  confirmPassword: string,
  translate?: Translate,
): string {
  const tr = translate ?? defaultTranslate
  if (!oldPassword) return tr('settings.validation.oldRequired')
  if (!newPassword) return tr('settings.validation.newRequired')
  if (newPassword.length < 6) return tr('settings.validation.newShort')
  if (newPassword !== confirmPassword) return tr('settings.validation.mismatch')
  if (newPassword === oldPassword) return tr('settings.validation.same')
  return ''
}

/* -------------------------------------------------------------------------- */
/* 逆向产物（客户端源码）                                                      */
/* -------------------------------------------------------------------------- */

export interface AppSourceStatus {
  ok?: boolean
  dev_tools?: boolean
  install_dir?: string
  resources_dir?: string
  asar?: string
  source_dir?: string
  can_unpack?: boolean
  python?: string
  node?: string
  source_stat?: Record<string, unknown>
  paths_config?: Record<string, unknown>
  message?: string
  reason?: string
}

export interface UnpackProgress {
  running?: boolean
  phase?: string
  percent?: number
  done?: number
  total?: number
  current?: string
  error?: string
  result?: Record<string, unknown>
}

export interface SourceHit {
  file: string
  line: number
  text: string
}

export interface SourceSearchPayload {
  ok: boolean
  files: SourceHit[]
  dir?: string
  reason?: string
}

export function getAppSourceStatus(signal?: AbortSignal): Promise<AppSourceStatus> {
  return http<AppSourceStatus>('GET', '/api/app-source/status', { signal })
}

export function detectAppSource(): Promise<AppSourceStatus> {
  return http<AppSourceStatus>('POST', '/api/app-source/detect')
}

/** 三种输入都会被后端补齐到安装根目录；save=false 只校验不落库。 */
export function locateAppSource(input: {
  install_dir?: string
  source_dir?: string
}): Promise<AppSourceStatus> {
  return http<AppSourceStatus>('POST', '/api/app-source/locate', {
    body: { install_dir: input.install_dir || '', source_dir: input.source_dir || '', save: true },
  })
}

export function planUnpack(): Promise<Record<string, unknown>> {
  return http<Record<string, unknown>>('POST', '/api/app-source/unpack/plan')
}

export function startUnpack(force = false): Promise<Record<string, unknown>> {
  return http<Record<string, unknown>>('POST', '/api/app-source/unpack', { body: { force } })
}

export function getUnpackProgress(): Promise<UnpackProgress> {
  return http<UnpackProgress>('GET', '/api/app-source/unpack/progress')
}

export function removeUnpackedSource(): Promise<AppSourceStatus> {
  return http<AppSourceStatus>('POST', '/api/app-source/remove')
}

export function searchAppSource(q: string, limit = 50): Promise<SourceSearchPayload> {
  return http<SourceSearchPayload>('GET', '/api/app-source/search', { query: { q, limit } })
}

/** 逆向产物的状态行（页面直接渲染）。 */
export interface SourceLine {
  label: string
  value: string
  mono?: boolean
}

export function sourceStatusLines(
  status: AppSourceStatus | null | undefined,
  translate?: Translate,
): SourceLine[] {
  if (!status) return []
  const tr = translate ?? defaultTranslate
  const text = (value: unknown): string => (value ? String(value) : '—')
  return [
    { label: tr('settings.source.install'), value: text(status.install_dir), mono: true },
    { label: tr('settings.source.asar'), value: text(status.asar), mono: true },
    { label: tr('settings.source.output'), value: text(status.source_dir), mono: true },
    { label: tr('settings.source.runtime'), value: text(status.python) + ' / ' + (status.node || '—') },
    {
      label: tr('settings.source.canUnpack'),
      value: status.can_unpack ? tr('settings.source.yes') : tr('settings.source.no'),
    },
  ]
}

/** 拆包进度百分比（优先后端 percent，其次 done/total）。 */
export function unpackPercent(progress: UnpackProgress | null | undefined): number {
  if (!progress) return 0
  if (typeof progress.percent === 'number' && Number.isFinite(progress.percent)) {
    return Math.max(0, Math.min(100, Math.round(progress.percent)))
  }
  const total = Number(progress.total || 0)
  const done = Number(progress.done || 0)
  if (total > 0) return Math.max(0, Math.min(100, Math.round((done / total) * 100)))
  return 0
}
