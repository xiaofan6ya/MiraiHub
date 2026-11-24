/**
 * 平台管理（/platforms）的数据层：卡片合并、动作声明、批量扫描/导入/导出。
 * r4：平台分级（夯 / 人上人 / npc / 拉完了）功能已从前端删除；后端 /api/platforms 若仍返回 rating.tier，前端直接忽略。
 *
 * 契约：docs/ui-v2/contracts/platforms.md（末尾「## 实现对照」是本页的验收表）
 * 设计稿：docs/ui-v2/06-pages-a.md P4
 * 后端：
 *   GET  /api/platforms                      admin/platform/router.py:list_platforms（账号数随列表下发）
 *   GET  /api/platforms/current | PUT …      router.py:get_current / set_current
 *   GET  /api/platforms/export-all           router.py:export_all_platforms（mode=backup|plain，platform=落库名）
 *   GET  /api/platform/speed?hours=24        bench_router.py:platform_speed（成功率是 0–100）
 *   GET  /api/gateway/config                 gateway_api.py:get_config（strategy/priority/disabled）
 *   POST /api/gateway/disable|enable         gateway_api.py:disable / enable（禁用只影响调度）
 *   POST /api/accounts/import-local          accounts.py:import_local（workbuddy 系列扫描+入库一步）
 *   GET  /api/<平台>/scan-local              accounts.py:scan_local（各平台包挂 /api/<storage>/）
 *   POST /api/<平台>/import-local            accounts.py:import_local
 *   POST /api/accounts/import-async          accounts.py:import_accounts_async（返回 job_id）
 *   GET  /api/accounts/job/{id}              accounts.py:account_job
 *
 * 三条必须守住的语义：
 *   1. 能力由平台声明（/api/platforms 的 actions / supports_local_scan / auth_mode），前端不写 if(id)。
 *   2. 启用/禁用是**调度开关**（gateway disabled map），账号仍然可管理。
 *   3. 平台跑分的成功率为 0–100 的百分数（不是 0–1），不要再过 fmtPct。
 */
import { http } from './http'
import type { PlatformSpec } from './platforms'
import { t, type MessageKey } from '@/i18n'

/** 新键合并进字典前 MessageKey 里还没有，这里收窄一次类型（r3，键见 tmp/quick_round/i18n_pagesB.json）。 */
function tx(key: string, params?: Record<string, string | number>): string {
  return t(key as MessageKey, params)
}

/* -------------------------------------------------------------------------- */
/* 后端形状                                                                    */
/* -------------------------------------------------------------------------- */

/** GET /api/platform/speed 的一行（bench_router.platform_speed）。 */
export interface PlatformSpeedRow {
  platform: string
  label: string
  rank: number
  requests: number
  ok_requests: number
  /** 0–100 的百分数。 */
  success_rate: number
  ttfb_p50: number
  ttfb_avg: number
  latency_avg: number
  latency_p50: number
  latency_p95: number
  tps_p50: number
  tps_avg: number
  jitter: number
  samples: number
  low_sample: boolean
  score: number
}

export interface PlatformSpeedPayload {
  hours: number
  model: string
  items: PlatformSpeedRow[]
  fastest: string
  steadiest: string
  observed_at: string
}

export interface GatewayDisabledInfo {
  at?: number
  reason?: string
}

/** GET /api/gateway/config（routing_config + platforms）。 */
export interface GatewayConfigPayload {
  strategy?: string
  spread_unbound?: boolean
  priority?: Record<string, number>
  disabled?: Record<string, GatewayDisabledInfo>
  platforms?: Array<{ id: string; label: string; managed: boolean }>
}

/** GET /api/accounts?all_platforms=1 里本页用到的字段。 */
export interface AccountSnapshotRow {
  id: number
  platform?: string
  domain?: string | null
  status?: string
  balance_remain?: number | null
  credits_remaining?: number | null
}

/* -------------------------------------------------------------------------- */
/* 接口                                                                        */
/* -------------------------------------------------------------------------- */

export function getPlatformSpeed(hours = 24, signal?: AbortSignal): Promise<PlatformSpeedPayload> {
  return http<PlatformSpeedPayload>('GET', '/api/platform/speed', { query: { hours }, signal })
}

export function getGatewayConfig(signal?: AbortSignal): Promise<GatewayConfigPayload> {
  return http<GatewayConfigPayload>('GET', '/api/gateway/config', { signal })
}

/** 启停平台网关（只影响调度，账号保留）。platform 传**落库平台名**。 */
export function setPlatformEnabled(platform: string, enabled: boolean, reason = ''): Promise<{ ok: boolean }> {
  return http<{ ok: boolean }>('POST', '/api/gateway/' + (enabled ? 'enable' : 'disable'), {
    body: { platform, reason },
  })
}

/** 账号池快照（卡片上的「可用账号 / 剩余额度」来自它）。 */
export function getAccountsSnapshot(signal?: AbortSignal): Promise<{ items: AccountSnapshotRow[] }> {
  return http<{ items: AccountSnapshotRow[] }>('GET', '/api/accounts', {
    query: { all_platforms: 1 },
    signal,
  })
}

/** 导出完整凭证的二次确认请求头（后端 export_guard，r3）。 */
export const EXPORT_CONFIRM_HEADER = 'X-Export-Confirm'
export const EXPORT_CONFIRM_VALUE = 'include-secrets'

/**
 * 导出全部平台账号。
 *   · plain：脱敏，仅用于核对；
 *   · backup：含可直接登录的完整凭证，必须先在界面上确认，再带 `X-Export-Confirm: include-secrets`；
 *     没带确认头时后端回 428 `export_confirm_required`（见 isExportConfirmRequired）。
 */
export function exportPlatformsAll(
  mode: 'backup' | 'plain' = 'plain',
  platform = '',
  confirmed = false,
): Promise<Record<string, unknown>> {
  return http<Record<string, unknown>>('GET', '/api/platforms/export-all', {
    query: { mode, platform: platform || '' },
    headers: confirmed ? { [EXPORT_CONFIRM_HEADER]: EXPORT_CONFIRM_VALUE } : undefined,
  })
}

/** 后端要求确认才肯导出完整凭证（428 + detail.code = export_confirm_required）。 */
export function isExportConfirmRequired(error: unknown): boolean {
  if (!error || typeof error !== 'object') return false
  const status = (error as { status?: unknown }).status
  if (status !== 428) return false
  const detail = (error as { detail?: unknown }).detail
  if (!detail || typeof detail !== 'object') return true
  const inner = (detail as { detail?: unknown }).detail
  const code = (detail as { code?: unknown }).code ?? (inner && typeof inner === 'object' ? (inner as { code?: unknown }).code : undefined)
  return code === undefined || code === 'export_confirm_required'
}

/* -------------------------------------------------------------------------- */
/* 平台动作（platform-actions.js 的移植）                                       */
/* -------------------------------------------------------------------------- */

export interface PlatformActionItem {
  name: string
  label: string
  /** 同组只保留一个主按钮（旧版行为）。 */
  primary: boolean
}

/** 动作 → 显示名（只有展示信息，能力由后端 actions 决定）。getter：每次读取按当前语言取词。 */
export const ACTION_LABELS: Record<string, string> = {
  get scan() { return tx('page.platforms.actionName.scan') },
  get import() { return tx('page.platforms.actionName.import') },
  get 'import-file'() { return tx('page.platforms.actionName.importFile') },
  get export() { return tx('page.platforms.actionName.export') },
  get 'refresh-quota'() { return tx('page.platforms.actionName.refreshQuota') },
  get usage() { return tx('page.platforms.actionName.usage') },
  get 'web-cookie'() { return tx('page.platforms.actionName.webCookie') },
  get checkin() { return tx('page.platforms.actionName.checkin') },
  get activity() { return tx('page.platforms.actionName.activity') },
  get onboarding() { return tx('page.platforms.actionName.onboarding') },
  get invite() { return tx('page.platforms.actionName.invite') },
  get 'ban-recheck'() { return tx('page.platforms.actionName.banRecheck') },
  get 'begin-auth'() { return tx('page.platforms.actionName.beginAuth') },
  get inject() { return tx('page.platforms.actionName.inject') },
  get keepalive() { return tx('page.platforms.actionName.keepalive') },
}

const PRIMARY_ACTIONS = new Set(['import', 'import-file', 'begin-auth'])

/** 该平台能做哪些动作（后端 actions 声明 + auth_mode/supports_local_scan 修正）。 */
export function platformActionsOf(spec: PlatformSpec | null | undefined): PlatformActionItem[] {
  if (!spec) return []
  let declared = Array.isArray(spec.actions) ? spec.actions.slice() : []
  const authMode = String(spec.auth_mode || '')
  const supportsScan = spec.supports_local_scan !== false
  if (authMode === 'import-only') {
    // 只能粘贴/导入凭证：保留 import，去掉 scan 与 begin-auth
    declared = declared.filter((name) => name !== 'scan' && name !== 'begin-auth')
    if (!declared.includes('import')) declared = ['import', ...declared]
  } else if (!supportsScan) {
    // 不支持本地扫描：账号总得有入口 —— 至少给授权登录
    declared = declared.filter((name) => name !== 'scan' && name !== 'import')
    if (!declared.includes('begin-auth')) declared = ['begin-auth', ...declared]
  }
  let primaryTaken = false
  return declared
    .filter((name) => Boolean(name))
    .map((name) => {
      const primary = PRIMARY_ACTIONS.has(name) && !primaryTaken
      if (primary) primaryTaken = true
      return { name, label: ACTION_LABELS[name] || name, primary }
    })
}

/* -------------------------------------------------------------------------- */
/* 卡片合并                                                                    */
/* -------------------------------------------------------------------------- */

export interface PlatformAccountAggregate {
  total: number
  active: number
  credits: number
}

export interface PlatformCard {
  id: string
  label: string
  /** 落库平台名（trae-cn / trae-intl 都是 trae）。 */
  storage: string
  icon: string
  enabled: boolean
  disabledReason: string
  realmLabel: string
  realm: string
  priority: number | null
  accountCount: number
  activeAccounts: number | null
  credits: number | null
  quotaUnit: string
  quotaUnitLabel: string
  requests: number
  /** 0–100 的百分数；没有样本时 null。 */
  successRate: number | null
  ttfbMs: number | null
  tps: number | null
  lowSample: boolean
  actions: PlatformActionItem[]
  managed: boolean
  notes: string
  authMode: string
  supportsScan: boolean
  realms: Array<{ key: string; label: string; default?: boolean }>
}

/** 账号池快照 → 落库平台聚合（可用账号 + 剩余额度）。 */
export function aggregateAccounts(
  rows: AccountSnapshotRow[] | null | undefined,
): Record<string, PlatformAccountAggregate> {
  const out: Record<string, PlatformAccountAggregate> = {}
  for (const row of rows || []) {
    const key = String(row.platform || '').toLowerCase()
    if (!key) continue
    const bucket = out[key] || { total: 0, active: 0, credits: 0 }
    bucket.total += 1
    // 剩余额度只算 active 账号（与账号池「可用额度」同口径；禁用/失效号不算）
    if (String(row.status || '') === 'active') {
      bucket.active += 1
      const left = Number(row.balance_remain ?? row.credits_remaining ?? 0)
      if (Number.isFinite(left) && left > 0) bucket.credits += left
    }
    out[key] = bucket
  }
  return out
}

function realmLabelOf(spec: PlatformSpec): string {
  if (spec.realm_label) return spec.realm_label
  if (spec.realm === 'cn') return '国内版'
  if (spec.realm === 'intl') return '国际版'
  if (spec.realms?.length) return spec.realms.map((item) => item.label).join(' / ')
  return ''
}

export interface PlatformCardsInput {
  platforms: PlatformSpec[] | null | undefined
  speed?: PlatformSpeedPayload | null
  gateway?: GatewayConfigPayload | null
  accounts?: Record<string, PlatformAccountAggregate> | null
}

/** 平台列表 + 跑分 + 网关开关 + 账号聚合 → 卡片数据（纯函数，可单测）。 */
export function toPlatformCards(input: PlatformCardsInput): PlatformCard[] {
  const list = input.platforms || []
  const speed = new Map<string, PlatformSpeedRow>()
  for (const row of input.speed?.items || []) {
    speed.set(String(row.platform || '').toLowerCase(), row)
  }
  const disabled = input.gateway?.disabled || {}
  const priority = input.gateway?.priority || {}
  const accounts = input.accounts || {}
  return list.map((spec) => {
    const storage = String(spec.storage_platform || spec.id || '').toLowerCase()
    const id = String(spec.id || storage)
    const speedRow = speed.get(storage) || speed.get(id.toLowerCase()) || null
    const disabledInfo = disabled[storage] || disabled[id.toLowerCase()] || null
    const priorityValue = priority[storage] ?? priority[id.toLowerCase()]
    const account = accounts[storage] || null
    return {
      id,
      label: spec.label || id,
      storage,
      icon: spec.icon || id,
      enabled: !disabledInfo,
      disabledReason: String(disabledInfo?.reason || ''),
      realmLabel: realmLabelOf(spec),
      realm: spec.realm || '',
      priority: priorityValue === undefined || priorityValue === null ? null : Number(priorityValue),
      accountCount: Number(spec.account_count ?? account?.total ?? 0),
      activeAccounts: account ? account.active : null,
      credits: account ? account.credits : null,
      quotaUnit: spec.quota_unit || 'credits',
      quotaUnitLabel: spec.quota_unit_label || '',
      requests: Number(speedRow?.requests || 0),
      successRate: speedRow ? Number(speedRow.success_rate) : null,
      ttfbMs: speedRow && speedRow.ttfb_avg ? Number(speedRow.ttfb_avg) : null,
      tps: speedRow && speedRow.tps_avg ? Number(speedRow.tps_avg) : null,
      lowSample: Boolean(speedRow?.low_sample),
      actions: platformActionsOf(spec),
      managed: Boolean(spec.managed),
      notes: spec.notes || '',
      authMode: String(spec.auth_mode || ''),
      supportsScan: spec.supports_local_scan !== false,
      realms: (spec.realms || []).map((item) => ({ key: item.key, label: item.label, default: item.default })),
    }
  })
}

export type PlatformStateFilter = 'all' | 'enabled' | 'disabled'

export function countByState(cards: PlatformCard[]): Record<PlatformStateFilter, number> {
  const enabled = cards.filter((card) => card.enabled).length
  return { all: cards.length, enabled, disabled: cards.length - enabled }
}

/** 状态页签 + 搜索过滤（平台名 / id / 落库名 / 区域都参与匹配）。 */
export function filterPlatforms(
  cards: PlatformCard[],
  options: { state?: PlatformStateFilter | string; q?: string } = {},
): PlatformCard[] {
  const state = (options.state || 'all') as PlatformStateFilter
  const keyword = String(options.q || '').trim().toLowerCase()
  return cards.filter((card) => {
    if (state === 'enabled' && !card.enabled) return false
    if (state === 'disabled' && card.enabled) return false
    if (!keyword) return true
    return (
      card.label.toLowerCase().includes(keyword) ||
      card.id.toLowerCase().includes(keyword) ||
      card.storage.includes(keyword) ||
      card.realmLabel.toLowerCase().includes(keyword)
    )
  })
}

/** 成功率/延迟的着色口径（卡片与跑分表共用）。 */
export type MetricTone = 'ok' | 'warn' | 'danger' | 'muted'

export function successTone(rate: number | null | undefined): MetricTone {
  if (rate === null || rate === undefined || !Number.isFinite(Number(rate))) return 'muted'
  const value = Number(rate)
  if (value >= 90) return 'ok'
  if (value >= 60) return 'warn'
  return 'danger'
}

/* -------------------------------------------------------------------------- */
/* 扫描 / 导入                                                                 */
/* -------------------------------------------------------------------------- */

export interface PlatformScanPlan {
  storage: string
  /** accounts-import：/api/accounts/import-local 一步到位（扫描+入库） */
  kind: 'accounts-import' | 'scan-then-import' | 'scan-only'
  /** 接口前缀（/api/accounts 或 /api/<storage>）。 */
  prefix: string
  realm: string
}

/** 扫描策略：与旧版 platformScanAll 的分支一致（workbuddy 一步、qoder 只扫描、其它扫描+入库）。 */
export function scanPlanFor(spec: PlatformSpec | null | undefined): PlatformScanPlan | null {
  if (!spec) return null
  const storage = String(spec.storage_platform || spec.id || '').toLowerCase()
  if (!storage) return null
  const realm = String(spec.realm || '')
  if (storage === 'workbuddy' || storage === 'codebuddy') {
    return { storage, kind: 'accounts-import', prefix: '/api/accounts', realm }
  }
  if (storage === 'qoder') {
    return { storage, kind: 'scan-only', prefix: '/api/qoder', realm }
  }
  return { storage, kind: 'scan-then-import', prefix: '/api/' + storage, realm }
}

export interface PlatformScanResult {
  storage: string
  label: string
  scanned: number
  imported: number
  skipped: number
  note: string
}

function realmQuery(realm: string): Record<string, string> | undefined {
  return realm ? { realm } : undefined
}

/** 对一个平台执行扫描（按 plan 分派；错误转成 note，不抛出）。 */
export async function runPlatformScan(
  spec: PlatformSpec,
  plan: PlatformScanPlan,
): Promise<PlatformScanResult> {
  const result: PlatformScanResult = {
    storage: plan.storage,
    label: spec.label || plan.storage,
    scanned: 0,
    imported: 0,
    skipped: 0,
    note: '',
  }
  try {
    if (plan.kind === 'accounts-import') {
      const data = await http<{ added?: number; skipped?: number; exists?: boolean }>(
        'POST',
        '/api/accounts/import-local',
        { body: { all: true } },
      )
      result.imported = Number(data?.added || 0)
      result.skipped = Number(data?.skipped || 0)
      if (data?.exists === false) result.note = tx('page.platforms.scanNote.noDir')
      else if (!result.imported && !result.skipped) result.note = tx('page.platforms.scanNote.noNew')
      return result
    }
    const scanned = await http<{ items?: Array<Record<string, unknown>> }>(
      'GET',
      plan.prefix + '/scan-local',
      { query: realmQuery(plan.realm) },
    )
    const items = Array.isArray(scanned?.items) ? scanned.items : []
    result.scanned = items.length
    if (!items.length) {
      result.note = plan.kind === 'scan-only' ? tx('page.platforms.scanNote.noLogin') : tx('page.platforms.scanNote.noReadable')
      return result
    }
    if (plan.kind === 'scan-only') {
      result.note = tx('page.platforms.scanNote.foundScanOnly', { n: result.scanned })
      return result
    }
    const uids = items
      .map((item) => item.uid_full || item.userid || item.uid || item.account_id || item.uid_hint)
      .filter(Boolean)
    const imported = await http<{ imported?: number; skipped?: number; hint?: string; reason?: string }>(
      'POST',
      plan.prefix + '/import-local',
      {
        query: realmQuery(plan.realm),
        body: uids.length ? { uids, all: false } : { all: true },
      },
    )
    result.imported = Number(imported?.imported || 0)
    result.skipped = Number(imported?.skipped || 0)
    if (!result.imported && !result.skipped) {
      result.note = String(imported?.hint || imported?.reason || tx('page.platforms.scanNote.nothing'))
    }
    return result
  } catch (error) {
    result.note = tx('page.platforms.scanNote.failed', { reason: (error as Error)?.message || String(error) })
    return result
  }
}

/* -------------------------------------------------------------------------- */
/* 导入全部平台                                                                */
/* -------------------------------------------------------------------------- */

export interface PlatformBackupFile {
  format?: string
  version?: number
  mode?: string
  platforms?: Record<string, { label?: string; count?: number; accounts?: Array<Record<string, unknown>> }>
}

export interface ParsedBackup {
  /** 落库平台名 → 账号记录。 */
  byPlatform: Record<string, Array<Record<string, unknown>>>
  /** 解析过程中的提示（格式不对被跳过的文件等）。 */
  notes: string[]
}

/** 「导出全部平台」文件 → {落库平台: 账号[]}。格式不对的文件记 note 后跳过。 */
export function parsePlatformBackup(texts: Array<{ name: string; text: string }>): ParsedBackup {
  const byPlatform: Record<string, Array<Record<string, unknown>>> = {}
  const notes: string[] = []
  for (const file of texts) {
    const raw = String(file.text || '').replace(/^\uFEFF/, '').trim()
    if (!raw) {
      notes.push(tx('page.platforms.importNote.empty', { name: file.name }))
      continue
    }
    let parsed: PlatformBackupFile
    try {
      parsed = JSON.parse(raw) as PlatformBackupFile
    } catch (error) {
      notes.push(tx('page.platforms.importNote.badJson', { name: file.name }))
      continue
    }
    if (!parsed || typeof parsed !== 'object' || !parsed.platforms) {
      notes.push(tx('page.platforms.importNote.badFormat', { name: file.name }))
      continue
    }
    for (const [storage, pack] of Object.entries(parsed.platforms)) {
      const accounts = Array.isArray(pack?.accounts) ? pack.accounts : []
      if (!accounts.length) continue
      const key = String(storage || '').toLowerCase()
      byPlatform[key] = (byPlatform[key] || []).concat(accounts)
    }
  }
  return { byPlatform, notes }
}

/** 单个平台的导入请求体（workbuddy 系列走 items，其它平台走 JSONL 原文）。 */
export function importPayloadFor(storage: string, accounts: Array<Record<string, unknown>>): Record<string, unknown> {
  const key = String(storage || '').toLowerCase()
  if (key === 'workbuddy' || key === 'codebuddy') {
    return {
      storage: key,
      verify: true,
      items: accounts.map((item) => ({
        name: typeof item.name === 'string' ? item.name : '',
        auth_json: JSON.stringify(item),
      })),
    }
  }
  return { storage: key, text: accounts.map((item) => JSON.stringify(item)).join('\n') }
}

/** 提交某平台的导入后台任务，返回 job_id（失败抛出）。 */
export async function submitPlatformImport(
  storage: string,
  accounts: Array<Record<string, unknown>>,
): Promise<string> {
  const data = await http<{ job_id?: string }>('POST', '/api/accounts/import-async', {
    body: importPayloadFor(storage, accounts),
  })
  if (!data?.job_id) throw new Error(tx('page.platforms.importNote.noJob'))
  return data.job_id
}

/** 导出文件下载（浏览器）。 */
export function saveJson(value: unknown, filename: string): void {
  if (typeof document === 'undefined' || typeof URL === 'undefined' || typeof URL.createObjectURL !== 'function') return
  const blob = new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' })
  const href = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = href
  link.download = filename
  link.style.display = 'none'
  document.body.appendChild(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(href), 0)
}

/** 导出文件名：miraihub-<scope>-<mode>-<时间戳>.json（与旧版一致）。 */
export function exportFilename(mode: 'backup' | 'plain', scope = 'all-platforms', now = new Date()): string {
  const pad = (value: number): string => String(value).padStart(2, '0')
  const stamp =
    now.getFullYear() + pad(now.getMonth() + 1) + pad(now.getDate()) + '-' +
    pad(now.getHours()) + pad(now.getMinutes()) + pad(now.getSeconds())
  return 'miraihub-' + (scope || 'all-platforms') + '-' + mode + '-' + stamp + '.json'
}
