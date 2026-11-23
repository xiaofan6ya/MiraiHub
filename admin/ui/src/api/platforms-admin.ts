/**
 * 平台管理（/platforms）的数据层：卡片合并、动作声明、tier、批量扫描/导入/导出。
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

export function exportPlatformsAll(
  mode: 'backup' | 'plain' = 'backup',
  platform = '',
): Promise<Record<string, unknown>> {
  return http<Record<string, unknown>>('GET', '/api/platforms/export-all', {
    query: { mode, platform: platform || '' },
  })
}

/* -------------------------------------------------------------------------- */
/* tier（platform-tiers.js 的移植 + 后端 rating 优先）                          */
/* -------------------------------------------------------------------------- */

export type PlatformTierKey = 'hang' | 'elite' | 'npc' | 'bad'

export interface PlatformTier {
  key: PlatformTierKey
  label: string
  rank: number
  reason: string
  source: string
}

export const TIER_ORDER: Array<{ key: PlatformTierKey; label: string; rank: number }> = [
  { key: 'hang', label: '夯', rank: 4 },
  { key: 'elite', label: '人上人', rank: 3 },
  { key: 'npc', label: 'npc', rank: 2 },
  { key: 'bad', label: '拉完了', rank: 1 },
]

/** 平台没自报 rating 时的兜底表（键是**落库平台名**，trae-cn/intl 共用）。 */
export const TIER_FALLBACK: Record<string, { tier: PlatformTierKey; reason: string }> = {
  workbuddy: {
    tier: 'hang',
    reason: '模型调用稳定、免排队，调用速度快；每月都有免费模型与低倍率模型，新用户最高可免费领 4000 积分。',
  },
  trae: { tier: 'npc', reason: '可能需要排队，额度给得不多，速度较慢。' },
  qoder: { tier: 'bad', reason: '需要排队，额度不多，而且巨慢。' },
  loomy: {
    tier: 'hang',
    reason: '稳定模型速度最快，新用户给的积分很足，每天签到还能领 5000 积分；美中不足是积分用得偏快。',
  },
  raccoon: { tier: 'elite', reason: '不算很稳定，时快时慢，但给的量还行、够用。' },
  codearts: { tier: 'npc', reason: '保活续签比较麻烦，额度不算多但够用，速度不快。' },
  minimax: { tier: 'elite', reason: '只有自家模型，额度消耗不快，速度还行。' },
  'wps-lingxi': { tier: 'npc', reason: '模型给得多，但每个量的额度不大。' },
}

function tierMeta(key: string): { key: PlatformTierKey; label: string; rank: number } | null {
  const found = TIER_ORDER.find((item) => item.key === String(key || '').toLowerCase())
  return found ? { ...found } : null
}

/** 平台等级：平台自报（rating）优先，其次前端兜底表，都没有就不显示。 */
export function tierOf(spec: PlatformSpec | null | undefined, storage?: string): PlatformTier | null {
  if (!spec) return null
  const self = spec.rating || {}
  const declared = tierMeta(String(self.tier || ''))
  if (declared) {
    return { ...declared, reason: String(self.reason || ''), source: String(self.source || 'platform') }
  }
  const key = String(storage || spec.storage_platform || spec.id || '')
    .toLowerCase()
    .replace(/-(cn|intl)$/, '')
  const fallback = TIER_FALLBACK[key]
  if (!fallback) return null
  const meta = tierMeta(fallback.tier)
  if (!meta) return null
  return { ...meta, reason: fallback.reason, source: 'fallback' }
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

/** 动作 → 中文名（只有展示信息，能力由后端 actions 决定）。 */
export const ACTION_LABELS: Record<string, string> = {
  scan: '扫描本机',
  import: '导入账号',
  'import-file': '导入账号',
  export: '导出',
  'refresh-quota': '刷新额度',
  usage: '用量明细',
  'web-cookie': '用量 Cookie',
  checkin: '签到领取',
  activity: '活跃对话',
  onboarding: '新手任务',
  invite: '邀请码',
  'ban-recheck': '失效复检',
  'begin-auth': '授权登录',
  inject: '注入客户端',
  keepalive: '保活校验',
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
  tier: PlatformTier | null
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
      tier: tierOf(spec, storage),
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
      if (data?.exists === false) result.note = '本机没有登录目录'
      else if (!result.imported && !result.skipped) result.note = '本机没有新凭证'
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
      result.note = plan.kind === 'scan-only' ? '本机没有该平台的登录' : '本机没有可读凭证'
      return result
    }
    if (plan.kind === 'scan-only') {
      result.note = '发现 ' + result.scanned + ' 个本机登录，请用「授权登录」逐个导入'
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
      result.note = String(imported?.hint || imported?.reason || '没有可导入凭证')
    }
    return result
  } catch (error) {
    result.note = '失败：' + ((error as Error)?.message || String(error))
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
      notes.push(file.name + '：空文件，已跳过')
      continue
    }
    let parsed: PlatformBackupFile
    try {
      parsed = JSON.parse(raw) as PlatformBackupFile
    } catch (error) {
      notes.push(file.name + '：不是合法 JSON，已跳过')
      continue
    }
    if (!parsed || typeof parsed !== 'object' || !parsed.platforms) {
      notes.push(file.name + '：不是「导出全部平台」的格式（缺 platforms 字段），已跳过')
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
  if (!data?.job_id) throw new Error('后端没有返回 job_id')
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
