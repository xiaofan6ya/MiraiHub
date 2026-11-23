/**
 * 积分流水（/api/maintenance/credit-events）。
 *
 * 旧版实现：admin/console/review-improvements.js 的 loadJournal / resetJournal / journalRow /
 * sourceLabel（#panel-journal 的上一页/下一页）。
 * 后端：admin/routers/maintenance.py:credit_events（模型 admin/models.py:CreditEvent）。
 *
 * 关键事实（决定页面怎么画）：
 *   · CreditEvent 只有 account_id，没有平台列 —— 平台归属由请求头 X-Platform 的
 *     平台作用域决定（platform/scope.py），因此「平台」筛选就是切全局平台上下文；
 *   · 分页是 page/page_size 偏移（不是真游标），total 每次都 COUNT；
 *   · 没有搜索参数，搜索只在当前页做客户端过滤（页面上标注）。
 */
import { http } from './http'
import { FMT_EMPTY, fmtInt, fmtTime } from '@/utils/format'

/* ------------------------------------------------------------------ *
 * 类型
 * ------------------------------------------------------------------ */

/** 一条积分流水（maintenance.py:credit_events 的 items[]）。 */
export interface CreditEventItem {
  id: number
  account_id: number
  account_name: string
  /** confirmed | observed */
  kind: string
  /** 来源串，可能带 "schedule · " / "manual · " / "task · " 前缀。 */
  source: string
  delta: number
  balance_after: number | null
  /** ISO（后端补 Z）。 */
  created_at: string
}

export interface CreditEventAccount {
  id: number
  name: string
}

export interface CreditEventPayload {
  items: CreditEventItem[]
  total: number
  page: number
  page_size: number
  /** 历史记录还在导入中（旧版提示「请刷新继续」）。 */
  importing: boolean
  /** 可选来源（当前平台作用域内出现过的 source）。 */
  sources: string[]
  /** 可选账号（历史流水里的账号 + 现存账号）。 */
  accounts: CreditEventAccount[]
}

export interface CreditEventQuery {
  accountId?: number | null
  source?: string
  /** confirmed / observed；空 = 全部。 */
  kind?: string
  /** ISO 时间（后端按 UTC 解析）。 */
  start?: string
  end?: string
  page?: number
  pageSize?: number
  signal?: AbortSignal
}

/* ------------------------------------------------------------------ *
 * 接口
 * ------------------------------------------------------------------ */

export function listCreditEvents(query: CreditEventQuery = {}): Promise<CreditEventPayload> {
  return http<CreditEventPayload>('GET', '/api/maintenance/credit-events', {
    query: {
      account_id: query.accountId ?? undefined,
      source: query.source || undefined,
      kind: query.kind || undefined,
      start: query.start || undefined,
      end: query.end || undefined,
      page: query.page ?? 1,
      page_size: query.pageSize ?? 50,
    },
    signal: query.signal,
  })
}

/* ------------------------------------------------------------------ *
 * 纯函数：接口响应 → 展示数据（单测覆盖）
 * ------------------------------------------------------------------ */

/** 旧版 sourceLabel：把 "schedule · 每日签到" 的前缀换成中文。 */
export const SOURCE_PREFIX_LABELS: Record<string, string> = {
  schedule: '定时',
  manual: '手动',
  task: '任务',
}

export function sourceLabel(source: string | null | undefined): string {
  const text = String(source || '').trim()
  if (!text) return FMT_EMPTY
  const index = text.indexOf(' · ')
  if (index <= 0) return text
  const prefix = text.slice(0, index)
  const rest = text.slice(index + 3).trim()
  const label = SOURCE_PREFIX_LABELS[prefix]
  return label ? label + ' · ' + rest : text
}

/** 记录类型（后端只有两种 kind；设计稿说的「消耗/奖励/采集/修正」在本后端不存在）。 */
export interface KindMeta {
  label: string
  tone: 'ok' | 'brand' | 'neutral'
}

export const KIND_META: Record<string, KindMeta> = {
  confirmed: { label: '确认奖励', tone: 'ok' },
  observed: { label: '余额观察', tone: 'brand' },
}

export function kindMeta(kind: string | null | undefined): KindMeta {
  return KIND_META[String(kind || '')] || { label: String(kind || '未知'), tone: 'neutral' }
}

/** 变动着色：正数 ok、负数 danger、0 neutral。 */
export type DeltaTone = 'ok' | 'danger' | 'neutral'

export function deltaTone(delta: number | null | undefined): DeltaTone {
  const value = Number(delta)
  if (!Number.isFinite(value) || value === 0) return 'neutral'
  return value > 0 ? 'ok' : 'danger'
}

export interface JournalRow extends Record<string, unknown> {
  id: number
  key: string
  time: string
  fullTime: string
  accountId: number
  accountName: string
  accountText: string
  platform: string
  platformText: string
  kind: string
  kindLabel: string
  kindTone: 'ok' | 'brand' | 'neutral'
  delta: number
  deltaText: string
  deltaTone: DeltaTone
  balanceText: string
  balanceRecorded: boolean
  source: string
  sourceText: string
  note: string
}

/**
 * 单条流水 → 表格行。
 *
 * platformText 由调用方传入当前平台作用域的展示名：CreditEvent 没有平台列，
 * 跨平台时无法逐行归属（不猜），这一点在契约里写明。
 */
export function toJournalRow(item: CreditEventItem, platformText = ''): JournalRow {
  const meta = kindMeta(item.kind)
  const delta = Number(item.delta) || 0
  const sourceText = sourceLabel(item.source)
  const balanceRecorded = item.balance_after !== null && item.balance_after !== undefined
  return {
    id: Number(item.id) || 0,
    key: String(item.id || item.created_at || 'row'),
    time: fmtTime(item.created_at),
    fullTime: String(item.created_at || ''),
    accountId: Number(item.account_id) || 0,
    accountName: item.account_name || '',
    accountText: (item.account_name || '账号') + (item.account_id ? ' #' + item.account_id : ''),
    platform: '',
    platformText: platformText || FMT_EMPTY,
    kind: item.kind || '',
    kindLabel: meta.label,
    kindTone: meta.tone,
    delta,
    deltaText: (delta > 0 ? '+' : '') + fmtInt(delta),
    deltaTone: deltaTone(delta),
    balanceText: balanceRecorded ? fmtInt(item.balance_after) : '未记录',
    balanceRecorded,
    source: item.source || '',
    sourceText,
    note: meta.label + (sourceText && sourceText !== FMT_EMPTY ? ' · ' + sourceText : ''),
  }
}

export function toJournalRows(items: CreditEventItem[] | null | undefined, platformText = ''): JournalRow[] {
  return Array.isArray(items) ? items.map((item) => toJournalRow(item, platformText)) : []
}

export interface JournalFilters {
  q?: string
}

/**
 * 搜索（后端没有 search 参数）：只在当前页做客户端过滤。
 * 匹配账号名 / 来源 / 说明 / 变动 / 余额。
 */
export function filterJournalRows(rows: JournalRow[], filters: JournalFilters): JournalRow[] {
  const term = String(filters.q || '').trim().toLowerCase()
  if (!term) return rows
  return rows.filter((row) =>
    [row.accountText, row.sourceText, row.note, row.deltaText, row.balanceText, row.kindLabel]
      .join(' ')
      .toLowerCase()
      .includes(term),
  )
}

export interface JournalSummary {
  total: number
  loaded: number
  netDelta: number
  netText: string
  confirmed: number
  observed: number
  text: string
}

/** 「已筛选 chips + 结果统计」里的统计口径（当前页净变化 + 全量条数）。 */
export function summarize(rows: JournalRow[], total: number): JournalSummary {
  const net = rows.reduce((sum, row) => sum + (Number(row.delta) || 0), 0)
  const confirmed = rows.filter((row) => row.kind === 'confirmed').length
  const observed = rows.filter((row) => row.kind === 'observed').length
  return {
    total: Number(total) || 0,
    loaded: rows.length,
    netDelta: net,
    netText: (net > 0 ? '+' : '') + fmtInt(net),
    confirmed,
    observed,
    text:
      '共 ' + fmtInt(total) + ' 条 · 本页净变化 ' + (net > 0 ? '+' : '') + fmtInt(net) +
      ' · 确认奖励 ' + confirmed + ' 笔 · 余额观察 ' + observed + ' 笔',
  }
}

export interface JournalPageState {
  page: number
  pageSize: number
  total: number
  pageCount: number
  hasPrev: boolean
  hasNext: boolean
}

/** 偏移分页的页面状态（旧版 page/page_size + total）。 */
export function pageState(payload: Pick<CreditEventPayload, 'page' | 'page_size' | 'total' | 'items'> | null, fallbackSize = 50): JournalPageState {
  const page = Math.max(1, Number(payload?.page) || 1)
  const pageSize = Math.max(1, Number(payload?.page_size) || fallbackSize)
  const total = Math.max(0, Number(payload?.total) || 0)
  const pageCount = Math.max(1, Math.ceil(total / pageSize))
  return {
    page,
    pageSize,
    total,
    pageCount,
    hasPrev: page > 1,
    hasNext: page < pageCount,
  }
}

/** 时间区间（NDatePicker 的毫秒时间戳）→ 后端要的 ISO 串。 */
export function rangeToQuery(range: [number, number] | null): { start: string; end: string } {
  if (!range || range.length !== 2) return { start: '', end: '' }
  const [start, end] = range
  if (!Number.isFinite(start) || !Number.isFinite(end)) return { start: '', end: '' }
  return { start: new Date(start).toISOString(), end: new Date(end).toISOString() }
}

/** 账号下拉：全部 + 历史/现存账号（旧版 data.accounts）。 */
export function accountOptions(accounts: CreditEventAccount[] | null | undefined): Array<{ label: string; value: number }> {
  return [
    { label: '全部账号', value: 0 },
    ...(accounts || []).map((item) => ({
      label: (item.name || '账号') + ' · #' + item.id,
      value: Number(item.id),
    })),
  ]
}

/** 来源下拉：全部 + 出现过的来源（旧版 WBFilterCombobox 的 sources）。 */
export function sourceOptions(sources: string[] | null | undefined): Array<{ label: string; value: string }> {
  return [
    { label: '全部来源', value: '' },
    ...(sources || []).filter(Boolean).map((source) => ({ label: sourceLabel(source), value: source })),
  ]
}

/** 类型下拉（后端只有 confirmed / observed）。 */
export const KIND_OPTIONS: Array<{ label: string; value: string }> = [
  { label: '全部类型', value: '' },
  { label: '确认奖励', value: 'confirmed' },
  { label: '余额观察', value: 'observed' },
]

/** 快速翻页时用：没有总数就按「本页是否满」判断还有没有下一页。 */
export function canGoNext(fast: boolean, state: JournalPageState, loadedRows: number): boolean {
  if (!fast) return state.hasNext
  return loadedRows > 0 && loadedRows >= state.pageSize
}

/** 导出 CSV（后端没有流水导出接口，前端导当前页）。 */
const CSV_HEADER = ['时间', '账号', '平台', '类型', '变动', '余额', '说明', '来源']

function csvCell(value: unknown): string {
  const text = String(value ?? '').replace(/\r?\n/g, ' ')
  return /[",]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text
}

export function toCsv(rows: JournalRow[]): string {
  const lines = [CSV_HEADER.join(',')]
  for (const row of rows) {
    lines.push(
      [row.fullTime, row.accountText, row.platformText, row.kindLabel, row.deltaText, row.balanceText, row.note, row.sourceText]
        .map(csvCell)
        .join(','),
    )
  }
  return lines.join('\n')
}

export function csvFilename(date = new Date()): string {
  const pad = (value: number): string => String(value).padStart(2, '0')
  return (
    'miraihub-journal-' +
    date.getFullYear() + pad(date.getMonth() + 1) + pad(date.getDate()) +
    '-' + pad(date.getHours()) + pad(date.getMinutes()) + pad(date.getSeconds()) +
    '.csv'
  )
}

/** 触发浏览器保存（UTF-8 BOM 让 Excel 认中文）。 */
export function saveCsv(text: string, filename: string): void {
  if (typeof document === 'undefined' || typeof URL === 'undefined' || typeof URL.createObjectURL !== 'function') return
  const blob = new Blob(['\ufeff' + text], { type: 'text/csv;charset=utf-8' })
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

/** 历史导入中的提示（旧版 journalHint 的 importing）。 */
export function importingHint(payload: Pick<CreditEventPayload, 'importing'> | null): string {
  return payload?.importing ? '历史记录导入中，请刷新继续。' : ''
}
