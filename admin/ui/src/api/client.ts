/**
 * 客户端参数档案（/api/client-profile）。
 *
 * 后端：admin/routers/client_profile.py（GET / PUT / detect / reset / export / import）
 *       admin/client_profile.py（effective / saved / sources / runtime_diagnostics / normalize）
 *       admin/routers/client_paths.py（手动路径覆盖，本页只读展示扫描目录）
 *
 * 档案是 **WorkBuddy 的全局设置**（GET 的 platform 参数目前不影响返回内容），
 * 由「内置兜底 ← 现场探测/已保存 ← 环境变量」合成；UA 始终由版本号现拼。
 */
import { http } from './http'
import { t, type MessageKey } from '@/i18n'

function tx(key: string, params?: Record<string, string | number>): string {
  return t(key as MessageKey, params)
}

export type ProfileSource = 'auto' | 'saved'

export interface ClientFieldDef {
  key: string
  label: string
  /** int | text */
  type: 'text' | 'int'
  hint?: string
}

/** 版本与归属（旧版 _CP_FIELDS，标签与说明原样保留）。 */
export const VERSION_FIELDS: ClientFieldDef[] = [
  { key: 'desktop_version', get label() { return tx('page.client.field.desktopVersion') }, type: 'text', get hint() { return tx('page.client.hint.desktopVersion') } },
  { key: 'cli_version', get label() { return tx('page.client.field.cliVersion') }, type: 'text', get hint() { return tx('page.client.hint.cliVersion') } },
  { key: 'ide_name', get label() { return tx('page.client.field.ideName') }, type: 'text', hint: 'X-IDE-Name' },
  { key: 'ide_type', get label() { return tx('page.client.field.ideType') }, type: 'text', hint: 'X-IDE-Type' },
  { key: 'product', get label() { return tx('page.client.field.product') }, type: 'text', get hint() { return tx('page.client.hint.product') } },
  { key: 'application_name', get label() { return tx('page.client.field.applicationName') }, type: 'text', get hint() { return tx('page.client.hint.applicationName') } },
  { key: 'turing_channel_id', get label() { return tx('page.client.field.turingChannelId') }, type: 'int', get hint() { return tx('page.client.hint.turingChannelId') } },
  { key: 'turing_product_name', get label() { return tx('page.client.field.turingProductName') }, type: 'text', get hint() { return tx('page.client.hint.turingProductName') } },
  { key: 'commit', label: 'commit', type: 'text', get hint() { return tx('page.client.hint.commit') } },
  { key: 'release_date_ms', get label() { return tx('page.client.field.releaseDateMs') }, type: 'int', get hint() { return tx('page.client.hint.releaseDateMs') } },
  { key: 'node_version', get label() { return tx('page.client.field.nodeVersion') }, type: 'text', get hint() { return tx('page.client.hint.nodeVersion') } },
  { key: 'stainless_package_version', get label() { return tx('page.client.field.stainlessPackageVersion') }, type: 'text', get hint() { return tx('page.client.hint.stainlessPackageVersion') } },
]

/** 桌面事件指纹（旧版 _CP_FP_FIELDS）。 */
export const FINGERPRINT_FIELDS: ClientFieldDef[] = [
  { key: 'fp_product', get label() { return tx('page.client.field.fpProduct') }, type: 'text', get hint() { return tx('page.client.hint.fpProduct') } },
  { key: 'ext_name', get label() { return tx('page.client.field.extName') }, type: 'text', hint: 'extName' },
  { key: 'os', get label() { return tx('page.client.field.os') }, type: 'text', get hint() { return tx('page.client.hint.os') } },
  { key: 'arch', get label() { return tx('page.client.field.arch') }, type: 'text', hint: 'x64 / arm64' },
  { key: 'os_version', get label() { return tx('page.client.field.osVersion') }, type: 'text', hint: 'osVersion' },
  { key: 'cpu_cores', get label() { return tx('page.client.field.cpuCores') }, type: 'int' },
  { key: 'memory_size', get label() { return tx('page.client.field.memorySize') }, type: 'int' },
  { key: 'timezone', get label() { return tx('page.client.field.timezone') }, type: 'text' },
  { key: 'report_delay', get label() { return tx('page.client.field.reportDelay') }, type: 'int' },
]

export interface DeviceTokenStatus {
  available?: boolean
  source?: string
  checked?: boolean
  node_available?: boolean
  helper_available?: boolean
  cached_failure?: boolean
  error?: string
}

export interface RuntimeDiagnostics {
  platform?: string
  device_token?: DeviceTokenStatus
  header_groups?: Record<string, string[]>
}

/** GET /api/client-profile 的响应（_payload 的真实字段）。 */
export interface ClientProfilePayload {
  effective: Record<string, unknown>
  saved: Record<string, unknown>
  detected: Record<string, unknown>
  detected_origins: Record<string, string>
  detected_package: Record<string, unknown>
  detected_system: Record<string, unknown>
  /** 每个键当前取自哪一层（排障用）。 */
  sources: Record<string, string>
  source: ProfileSource
  defaults: Record<string, unknown>
  has_client: boolean
  diff_saved_vs_effective: Record<string, unknown>
  runtime_diagnostics: RuntimeDiagnostics
  dirs: string[]
  ua: string
  platform: string
  product_version: string
}

/** POST /api/client-profile/detect 的响应。 */
export interface ClientDetectPayload {
  ok: boolean
  platform: string
  kind?: string
  detected?: Record<string, unknown>
  ua?: string
  fields?: ClientFieldDef[]
  dirs?: string[]
  detected_origins?: Record<string, string>
  detected_package?: Record<string, unknown>
  detected_system?: Record<string, unknown>
  has_client?: boolean
  runtime_diagnostics?: RuntimeDiagnostics
  errors?: string[]
  message?: string
  hint?: string
}

export interface SaveProfileResult extends ClientProfilePayload {
  ok: boolean
  errors: string[]
}

/* ------------------------------------------------------------------ *
 * 接口
 * ------------------------------------------------------------------ */

export function getClientProfile(platform = '', signal?: AbortSignal): Promise<ClientProfilePayload> {
  return http<ClientProfilePayload>('GET', '/api/client-profile', {
    query: { platform: platform || undefined },
    signal,
  })
}

/** 现场探测本机客户端（只读，不写库）。 */
export function detectClient(platform = '', signal?: AbortSignal): Promise<ClientDetectPayload> {
  return http<ClientDetectPayload>('POST', '/api/client-profile/detect', {
    query: { platform: platform || undefined },
    signal,
  })
}

/**
 * 保存档案（merge 语义：只传要改的字段）。id='' 表示不改取值策略。
 */
export function saveClientProfile(input: {
  profile: Record<string, unknown>
  source?: ProfileSource
  merge?: boolean
}): Promise<SaveProfileResult> {
  return http<SaveProfileResult>('PUT', '/api/client-profile', {
    body: {
      profile: input.profile,
      source: input.source,
      merge: input.merge !== false,
    },
  })
}

/** 清空保存值，回到「优先现场探测 + 内置兜底」。 */
export function resetClientProfile(): Promise<ClientProfilePayload & { ok: boolean }> {
  return http<ClientProfilePayload & { ok: boolean }>('POST', '/api/client-profile/reset')
}

/* ------------------------------------------------------------------ *
 * 纯函数：表单 / 脏判定 / 校验
 * ------------------------------------------------------------------ */

function asText(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'number') return String(value)
  return String(value)
}

/** 生效值 → 表单（数字也转字符串，空值保持空串）。 */
export function profileForm(
  effective: Record<string, unknown> | null | undefined,
  fields: ClientFieldDef[],
): Record<string, string> {
  const source = effective || {}
  const form: Record<string, string> = {}
  for (const field of fields) form[field.key] = asText(source[field.key])
  return form
}

export interface BuildProfileResult {
  profile: Record<string, unknown>
  errors: string[]
}

/**
 * 表单 → PUT /api/client-profile 的 profile。
 *
 * 留空的字段**不提交**（后端 normalize 拒绝空字符串，用「删除该字段」清空；
 * 本页不提供清空单项，清空请用「重置」）。
 */
export function buildProfilePayload(form: Record<string, string>, fields: ClientFieldDef[]): BuildProfileResult {
  const profile: Record<string, unknown> = {}
  const errors: string[] = []
  for (const field of fields) {
    const raw = String(form[field.key] ?? '').trim()
    if (!raw) continue
    if (field.type === 'int') {
      const number = Number(raw)
      if (!Number.isInteger(number) || number < 0) {
        errors.push(tx('page.client.v.nonNegInt', { label: field.label }))
        continue
      }
      profile[field.key] = number
      continue
    }
    if (raw.length > 512) {
      errors.push(tx('page.client.v.maxLen', { label: field.label }))
      continue
    }
    profile[field.key] = raw
  }
  return { profile, errors }
}

/** 与基线比较，返回有改动的字段 key（trim 后比较）。 */
export function dirtyFields(
  form: Record<string, string>,
  baseline: Record<string, string>,
  fields: ClientFieldDef[],
): string[] {
  return fields
    .filter((field) => String(form[field.key] ?? '').trim() !== String(baseline[field.key] ?? '').trim())
    .map((field) => field.key)
}

/** 来源文案 → 徽章语气（旧版来源徽标的语义）。 */
export function sourceTone(source: string | null | undefined): 'ok' | 'warn' | 'brand' | 'neutral' {
  const text = String(source || '')
  if (text.startsWith('环境变量')) return 'warn'
  if (text.includes('安装包') || text.includes('本机系统') || text === '现场探测') return 'ok'
  if (text === '已保存') return 'brand'
  return 'neutral'
}

/** 来源的短标签（完整来源放 title）。 */
export function sourceLabel(source: string | null | undefined): string {
  const text = String(source || '')
  // 入参是后端下发的中文来源串（只做匹配），输出走字典
  if (text.startsWith('环境变量')) return tx('page.client.src.env')
  if (text.includes('安装包')) return tx('page.client.src.installer')
  if (text.includes('本机系统')) return tx('page.client.src.system')
  if (text === '现场探测') return tx('page.client.src.probe')
  if (text === '已保存') return tx('page.client.src.saved')
  if (text === '内置兜底') return tx('page.client.src.fallback')
  if (text.startsWith('合并')) return tx('page.client.src.merged')
  return text || '—'
}

/* ------------------------------------------------------------------ *
 * 纯函数：运行时诊断 / 扫描位置 / 请求头表 / 来源表
 * ------------------------------------------------------------------ */

export interface RuntimeRow {
  label: string
  value: string
  tone: 'ok' | 'warn' | 'neutral' | 'brand'
  detail?: string
}

export interface RuntimeView {
  rows: RuntimeRow[]
  groups: Array<{ name: string; headers: string[] }>
  warning: boolean
  note: string
}

const GROUP_ORIGIN: Record<string, string> = {
  get client_profile() { return tx('page.client.origin.profile') },
  get sdk_stainless() { return tx('page.client.origin.sdk') },
  get account_session() { return tx('page.client.origin.session') },
  get request_trace() { return tx('page.client.origin.trace') },
}

/** 运行时诊断 → KV 行 + 请求头字段组 + 是否异常。 */
export function runtimeView(diag: RuntimeDiagnostics | null | undefined): RuntimeView {
  const data = diag || {}
  const token = data.device_token || {}
  const tokenOk = token.available === true
  const tokenText = tokenOk
    ? tx('page.client.rt.tokenOk')
    : !token.checked
      ? tx('page.client.rt.tokenNone')
      : token.cached_failure
        ? tx('page.client.rt.tokenCachedFail')
        : tx('page.client.rt.unavailable')
  const rows: RuntimeRow[] = [
    { label: tx('page.client.rt.platform'), value: data.platform || '—', tone: 'neutral' },
    { label: tx('page.client.rt.deviceToken'), value: tokenText, tone: tokenOk ? 'ok' : 'warn', detail: token.source || '' },
    {
      label: tx('page.client.rt.node'),
      value: token.node_available ? tx('page.client.rt.available') : tx('page.client.rt.unavailable'),
      tone: token.node_available ? 'ok' : 'warn',
    },
    {
      label: 'Native helper',
      value: token.helper_available ? tx('page.client.rt.available') : tx('page.client.rt.unavailable'),
      tone: token.helper_available ? 'ok' : 'warn',
    },
  ]
  const groups = Object.keys(data.header_groups || {})
    .sort()
    .map((name) => ({
      name,
      headers: Array.isArray(data.header_groups?.[name]) ? (data.header_groups?.[name] as string[]) : [],
    }))
  return {
    rows,
    groups,
    warning: !tokenOk || !token.node_available || !token.helper_available,
    note: tx('page.client.rt.note'),
  }
}

/** 扫描位置视图：目录、凭据位置、上游地址、状态。 */
export interface ScanItem {
  where: string
  who: string
  plain: boolean
}

export interface ScanView {
  stateText: string
  hasClient: boolean
  dirs: string[]
  items: ScanItem[]
  baseUrl: string
  hint: string
  errors: string[]
}

function pickText(source: Record<string, unknown> | null | undefined, keys: string[]): string {
  const data = source || {}
  for (const key of keys) {
    const value = data[key]
    if (value !== undefined && value !== null && value !== '') return String(value)
  }
  return ''
}

export function scanView(
  payload: ClientProfilePayload | null | undefined,
  detect: ClientDetectPayload | null | undefined,
): ScanView {
  const detected = (detect?.detected || payload?.detected || {}) as Record<string, unknown>
  const dirs = detect?.dirs || payload?.dirs || (detected.data_dirs as string[]) || []
  const rawItems = (detected.items as Array<Record<string, unknown>>) || []
  const items: ScanItem[] = rawItems.map((item) => ({
    where: pickText(item, ['source', 'data_dir', 'dir', 'path', 'account_id']),
    who: pickText(item, ['session', 'uid_hint', 'uid', 'phone', 'account_id']),
    plain: Boolean(item.has_plain_credential),
  }))
  const hasClient = Boolean(detect?.has_client ?? payload?.has_client)
  let stateText = tx('page.client.scan.none')
  if (dirs.length && items.length) stateText = tx('page.client.scan.found')
  else if (dirs.length) stateText = tx('page.client.scan.dirsOnly')
  else if (hasClient) stateText = tx('page.client.scan.paramsOnly')
  return {
    stateText,
    hasClient,
    dirs,
    items,
    baseUrl: pickText(detected, ['base_url']),
    hint: pickText(detected, ['hint']) || detect?.hint || detect?.message || '',
    errors: detect?.errors || [],
  }
}

/** 风控 / 审计请求头的表格行（值 + 来源）。 */
export interface HeaderRow {
  name: string
  value: string
  source: string
}

function derivedHeaderValue(effective: Record<string, unknown>, name: string): string {
  const map: Record<string, string> = {
    'X-IDE-Name': asText(effective.ide_name),
    'X-IDE-Type': asText(effective.ide_type),
    'X-Product': asText(effective.product),
    'X-IDE-Version': asText(effective.desktop_version),
  }
  return map[name] || '—'
}

/**
 * 请求头来源前缀 → Badge 语气（档案值 brand / SDK ok / 其它 neutral）。
 * 前缀判断留在 api 层，页面模板里不再出现中文前缀字面量。
 */
export function headerSourceTone(source: string | null | undefined): 'brand' | 'ok' | 'neutral' {
  const text = String(source || '')
  // 兼容后端中文前缀与本文件翻译后的来源名（英文界面下 source 已是英文）
  if (text.startsWith('档案值') || text === tx('page.client.origin.profile')) return 'brand'
  if (text.startsWith('SDK') || text === tx('page.client.origin.sdk')) return 'ok'
  return 'neutral'
}

export function headerRows(payload: ClientProfilePayload | null | undefined): HeaderRow[] {
  if (!payload) return []
  const eff = payload.effective || {}
  const extra = (eff.headers || {}) as Record<string, unknown>
  const groups = payload.runtime_diagnostics?.header_groups || {}
  const rows: HeaderRow[] = []
  const profileNames = groups.client_profile?.length
    ? groups.client_profile
    : Object.keys(extra)
  for (const name of profileNames) {
    rows.push({
      name,
      value: name in extra ? asText(extra[name]) : derivedHeaderValue(eff, name),
      source: tx('page.client.origin.profile'),
    })
  }
  for (const name of Object.keys(groups).sort()) {
    if (name === 'client_profile') continue
    for (const header of groups[name] || []) {
      rows.push({
        name: header,
        value: tx('page.client.header.runtime'),
        source: GROUP_ORIGIN[name] || name,
      })
    }
  }
  return rows
}

/** 取值来源（排障）：按键名排序。 */
export function sourceRows(sources: Record<string, string> | null | undefined): Array<{
  key: string
  source: string
  tone: 'ok' | 'warn' | 'brand' | 'neutral'
}> {
  const data = sources || {}
  return Object.keys(data)
    .filter((key) => key !== 'source_mode')
    .sort()
    .map((key) => ({ key, source: data[key], tone: sourceTone(data[key]) }))
}
