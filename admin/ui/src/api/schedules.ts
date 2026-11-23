/**
 * 定时任务（/api/schedules）+ 出口代理组（/api/proxies）。
 *
 * 后端：
 *   admin/routers/schedules.py —— list_schedules / list_task_catalog / create_schedule /
 *     patch_schedule / toggle_schedule / run_now / delete_schedule / get|set_global_proxy
 *   admin/routers/proxies.py:list_proxies —— 抽屉里「出口代理组」下拉的数据源（pools）
 *   admin/schedule_timing.py —— ScheduleTiming / cycle_label / display_name（日历周期按北京时间）
 *
 * 契约：docs/ui-v2/contracts/schedules.md
 *
 * 必须留在这个文件里的规则（页面与单测共用同一份实现）：
 *   1. `last_result` 是**字符串**（后端 _dump_result 的 JSON 文本），历史数据可能是被截断的
 *      半截 JSON —— 解析失败要如实显示「旧格式」，不能当成"无结果"。
 *   2. `ok` 字段有两种形态：布尔（`{"ok": true}`）与计数（`{"ok": 12, "fail": 1}`）。
 *   3. 下次执行时间由后端算好（`next_run_at`，naive UTC），前端只做「今天/明天 + HH:MM」的展示。
 *   4. 新建/编辑提交时 platform 由任务目录决定（任务 -> 平台），不能靠用户填。
 */
import { http } from './http'
import { FMT_EMPTY, beijingParts, fmtTime, toDate } from '@/utils/format'

/* ------------------------------------------------------------------ *
 * 接口类型（字段名与后端一致，snake_case 原样保留）
 * ------------------------------------------------------------------ */

export type ScheduleMode = 'preset' | 'interval' | 'daily' | 'weekly' | 'monthly'

/** admin/schedule_timing.py:ScheduleTiming。 */
export interface ScheduleTiming {
  mode: ScheduleMode
  /** HH:MM（日历周期按北京时间）。 */
  time: string
  /** 周一 = 0（datetime.weekday()）。 */
  weekdays: number[]
  monthday: number
}

/** GET /api/schedules 的 items[] 一项（admin/routers/schedules.py:list_schedules）。 */
export interface ScheduleItem {
  id: number
  /** 用户填的名称（可能为空串）。 */
  name: string
  /** 后端算好的展示名：没自定义名称时是「每 1 天 · 刷新平台总积分」。 */
  display_name: string
  timing: ScheduleTiming
  /** 周期的人话文案（「每天 09:00」「每 1 天」）。 */
  cycle_label: string
  task: string
  task_label: string
  /** 落库平台 id（workbuddy / trae / qoder…）。 */
  platform: string
  /** 平台展示名（「Trae（中国版 + 国际版）」这种双区标注也在这里）。 */
  platform_label: string
  interval_minutes: number
  enabled: boolean
  last_run_at: string | null
  next_run_at: string | null
  /** _dump_result 的 JSON 文本；空串表示尚未运行。 */
  last_result: string
  stop_after: string | null
  proxy_pool: string
  concurrency: number
  time_budget_minutes: number
  stale_minutes: number
  /** enabled 且 next_run_at <= now（后端判定）。 */
  due_soon: boolean
}

export interface ScheduleListPayload {
  items: ScheduleItem[]
}

/** GET /api/schedules/tasks 的 items[] 一项。 */
export interface ScheduleTaskOption {
  value: string
  label: string
  /** 落库平台 id。 */
  platform: string
  platform_label: string
  /** 该任务可归属的落库平台列表。 */
  platforms: string[]
}

export interface ScheduleTaskCatalogPayload {
  items: ScheduleTaskOption[]
}

/** GET/PUT /api/schedules/global-proxy。 */
export interface ScheduleGlobalProxy {
  proxy_pool: string
  concurrency: number
  time_budget_minutes: number
  stale_minutes: number
}

/** POST /api/schedules（ScheduleIn）。 */
export interface ScheduleInput {
  name: string
  task: string
  platform: string
  interval_minutes: number
  timing: ScheduleTiming
  enabled: number
  stop_after: string | null
  proxy_pool: string
  concurrency: number
  time_budget_minutes: number
  stale_minutes: number
}

/** PATCH /api/schedules/{id}（SchedulePatch：字段全可选，这里按整表提交）。 */
export type SchedulePatch = Partial<ScheduleInput>

export interface ScheduleSavePayload {
  id: number
  ok: boolean
}

export interface ScheduleTogglePayload {
  id: number
  enabled: boolean
}

export interface ScheduleDeletePayload {
  id: number
  ok: boolean
}

/** POST /api/schedules/{id}/run：result 是 run_task 的原样返回值。 */
export interface ScheduleRunPayload {
  id: number
  result: Record<string, unknown>
}

/** GET /api/proxies 的 items[] 一项（本页只消费 pool 相关字段）。 */
export interface ProxyEndpointItem {
  id: number
  label: string
  url: string
  enabled: boolean
  max_concurrency: number
  pool: string
  blocked: boolean
  blocked_until: string | null
  last_ok_at: string | null
  last_error: string
  state: 'disabled' | 'blocked' | 'ready'
}

export interface ProxyListPayload {
  items: ProxyEndpointItem[]
  summary: { total: number; ready: number; blocked: number; disabled: number; slots: number }
  /** 现有的全部代理组名（升序）。 */
  pools: string[]
  note: string
}

/* ------------------------------------------------------------------ *
 * 接口函数
 * ------------------------------------------------------------------ */

/** 任务列表。后端一次返回全部（不按平台收窄，也不分页）。 */
export function listSchedules(signal?: AbortSignal): Promise<ScheduleListPayload> {
  return http<ScheduleListPayload>('GET', '/api/schedules', signal ? { signal } : {})
}

/** 可选任务清单（内建 + 各平台专属）。 */
export function listScheduleTasks(signal?: AbortSignal): Promise<ScheduleTaskCatalogPayload> {
  return http<ScheduleTaskCatalogPayload>('GET', '/api/schedules/tasks', signal ? { signal } : {})
}

/** 全局出口与并发默认值。 */
export function getGlobalScheduleProxy(signal?: AbortSignal): Promise<ScheduleGlobalProxy> {
  return http<ScheduleGlobalProxy>('GET', '/api/schedules/global-proxy', signal ? { signal } : {})
}

export function putGlobalScheduleProxy(input: ScheduleGlobalProxy): Promise<ScheduleGlobalProxy> {
  return http<ScheduleGlobalProxy>('PUT', '/api/schedules/global-proxy', {
    body: {
      proxy_pool: input.proxy_pool,
      concurrency: input.concurrency,
      time_budget_minutes: input.time_budget_minutes,
      stale_minutes: input.stale_minutes,
    },
  })
}

export function createSchedule(input: ScheduleInput): Promise<ScheduleSavePayload> {
  return http<ScheduleSavePayload>('POST', '/api/schedules', { body: input })
}

export function updateSchedule(id: number, patch: SchedulePatch): Promise<ScheduleSavePayload> {
  return http<ScheduleSavePayload>('PATCH', '/api/schedules/' + encodeURIComponent(String(id)), {
    body: patch,
  })
}

/** 启停（后端取反）。 */
export function toggleSchedule(id: number): Promise<ScheduleTogglePayload> {
  return http<ScheduleTogglePayload>(
    'POST',
    '/api/schedules/' + encodeURIComponent(String(id)) + '/toggle',
  )
}

/** 立即执行一次；返回的结果与调度器写库的 last_result 同源。 */
export function runSchedule(id: number): Promise<ScheduleRunPayload> {
  return http<ScheduleRunPayload>(
    'POST',
    '/api/schedules/' + encodeURIComponent(String(id)) + '/run',
  )
}

export function deleteSchedule(id: number): Promise<ScheduleDeletePayload> {
  return http<ScheduleDeletePayload>('DELETE', '/api/schedules/' + encodeURIComponent(String(id)))
}

/** 代理节点与分组（抽屉下拉用）。 */
export function listProxyEndpoints(signal?: AbortSignal): Promise<ProxyListPayload> {
  return http<ProxyListPayload>('GET', '/api/proxies', signal ? { signal } : {})
}

/* ------------------------------------------------------------------ *
 * 纯函数：接口响应 → 展示数据（单测覆盖）
 * ------------------------------------------------------------------ */

export type ScheduleResultTone = 'ok' | 'warn' | 'danger' | 'neutral' | 'brand'

/** last_result 解析结果。tone 与 Badge 的 tone 取值一致。 */
export interface ScheduleResultView {
  tone: ScheduleResultTone
  /** Badge 文字：成功 / 部分失败 / 失败 / 执行中 / 已顺延 / 已跳过 / 旧格式 / 尚未运行。 */
  label: string
  /** 一行摘要（去掉 markdown 强调符）。 */
  summary: string
  running: boolean
  raw: string
  parsed: Record<string, unknown> | null
  /** 逐账号明细（后端 detail 数组）。 */
  detail: Array<Record<string, unknown>>
}

export interface ScheduleRow extends Record<string, unknown> {
  key: string
  id: number
  /** 用户填的名称（编辑回填用）。 */
  name: string
  /** 展示名（后端 display_name 优先）。 */
  displayName: string
  task: string
  taskLabel: string
  platform: string
  platformLabel: string
  cycleText: string
  /** 日历周期的时间口径提示（北京时间 / 系统时区）；非日历周期为空串。 */
  zoneHint: string
  intervalMinutes: number
  timing: ScheduleTiming
  nextRunAt: string | null
  /** 「今天 09:00」/「明天 09:00」/「10-30 09:00」/「已停用」/「—」。 */
  nextText: string
  lastRunAt: string | null
  lastRunText: string
  result: ScheduleResultView
  running: boolean
  enabled: boolean
  dueSoon: boolean
  proxyPool: string
  /** 「代理组 residential」/「跟随全局（代理组 lan）」/「直连」。 */
  proxyText: string
  concurrency: number
  budgetMinutes: number
  staleMinutes: number
  stopAfter: string | null
}

const RESULT_TONE_OK = 'ok'

function text(value: unknown): string {
  if (value === null || value === undefined) return ''
  return String(value).trim()
}

function int(value: unknown, fallback = 0): number {
  const n = Number(value)
  return Number.isFinite(n) ? Math.trunc(n) : fallback
}

function clamp(value: unknown, min: number, max: number): number {
  const n = int(value, min)
  return Math.min(max, Math.max(min, n))
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function pad2(n: number): string {
  return String(n).padStart(2, '0')
}

/** 去掉结果摘要里的 markdown 强调符（旧数据里带 `**`，表格不渲染 markdown）。 */
function plain(textValue: string): string {
  return textValue.replace(/\*\*/g, '').replace(/\s+/g, ' ').trim()
}

/** 日历周期的时间口径（admin/schedule_timing.py 模块首行：日历周期用北京时间）。 */
export const CALENDAR_ZONE_HINT = '北京时间 · UTC+8'

export const MODE_LABELS: Record<ScheduleMode, string> = {
  preset: '系统预设',
  interval: '固定间隔',
  daily: '每天',
  weekly: '每周',
  monthly: '每月',
}

export const MODE_OPTIONS: Array<{ label: string; value: ScheduleMode }> = (
  ['preset', 'interval', 'daily', 'weekly', 'monthly'] as ScheduleMode[]
).map((mode) => ({ label: MODE_LABELS[mode], value: mode }))

export const WEEKDAY_LABELS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']

export const WEEKDAY_OPTIONS = WEEKDAY_LABELS.map((label, value) => ({ label, value }))

export const INTERVAL_UNIT_OPTIONS = [
  { label: '分钟', value: 1 },
  { label: '小时', value: 60 },
  { label: '天', value: 1440 },
  { label: '周', value: 10080 },
]

/** 日历周期（按时刻执行）。 */
export function isCalendarMode(mode: ScheduleMode): boolean {
  return mode === 'daily' || mode === 'weekly' || mode === 'monthly'
}

/** 签到类任务（旧版正则：所有平台的签到都以 checkin 结尾）。 */
export function isCheckinTask(task: string): boolean {
  const value = text(task)
  return /(^|_)checkin$/.test(value) || value === 'daily_checkin'
}

/** 后端 timing 兜底：字段缺失/非法时给出可用的默认值（老数据、手改库）。 */
export function normalizeTiming(timing: ScheduleTiming | null | undefined): ScheduleTiming {
  const raw = timing && typeof timing === 'object' ? timing : ({} as ScheduleTiming)
  const mode = (Object.keys(MODE_LABELS) as ScheduleMode[]).includes(raw.mode as ScheduleMode)
    ? (raw.mode as ScheduleMode)
    : 'preset'
  const time = /^([01]\d|2[0-3]):[0-5]\d$/.test(text(raw.time)) ? text(raw.time) : '09:00'
  const weekdays = Array.isArray(raw.weekdays)
    ? Array.from(
        new Set(
          raw.weekdays
            .map((day) => int(day, -1))
            .filter((day) => day >= 0 && day <= 6),
        ),
      ).sort((a, b) => a - b)
    : []
  return {
    mode,
    time,
    weekdays: weekdays.length ? weekdays : [0],
    monthday: clamp(raw.monthday, 1, 31) || 1,
  }
}

/** 周期文案（后端 cycle_label 缺失时的兜底，口径与 admin/schedule_timing.py:cycle_label 一致）。 */
export function buildCycleLabel(timing: ScheduleTiming, intervalMinutes: number): string {
  const safe = normalizeTiming(timing)
  if (safe.mode === 'preset' || safe.mode === 'interval') {
    const minutes = Math.max(1, int(intervalMinutes, 60))
    const units: Array<[number, string]> = [
      [10080, '周'],
      [1440, '天'],
      [60, '小时'],
    ]
    for (const [size, unit] of units) {
      if (minutes % size === 0) return '每 ' + minutes / size + ' ' + unit
    }
    return '每 ' + minutes + ' 分钟'
  }
  if (safe.mode === 'daily') return '每天 ' + safe.time
  if (safe.mode === 'weekly') {
    const days = safe.weekdays.map((day) => WEEKDAY_LABELS[day]).join('、')
    return '每' + days + ' ' + safe.time
  }
  return '每月 ' + safe.monthday + ' 日 ' + safe.time
}

/** 下次执行时间 → 「今天 09:00」/「明天 09:00」/「后天 09:00」/「10-30 09:00」/「2025-10-30 09:00」。 */
export function formatNextRun(value: unknown, now = Date.now()): string {
  const date = toDate(value)
  if (!date) return FMT_EMPTY
  const p = beijingParts(date)
  const q = beijingParts(new Date(now))
  const dayOf = (parts: { y: number; mo: number; d: number }): number =>
    Math.round(Date.UTC(parts.y, parts.mo - 1, parts.d) / 86400000)
  const diff = dayOf(p) - dayOf(q)
  const hm = pad2(p.h) + ':' + pad2(p.mi)
  if (diff === 0) return '今天 ' + hm
  if (diff === 1) return '明天 ' + hm
  if (diff === 2) return '后天 ' + hm
  if (diff === -1) return '昨天 ' + hm
  if (p.y === q.y) return pad2(p.mo) + '-' + pad2(p.d) + ' ' + hm
  return p.y + '-' + pad2(p.mo) + '-' + pad2(p.d) + ' ' + hm
}

/** 结果里的失败数：failed / fail / failed_count，再退化为 detail 里 ok=false 的条数。 */
function failedCountOf(parsed: Record<string, unknown>, detail: Array<Record<string, unknown>>): number {
  for (const key of ['failed', 'fail', 'failed_count']) {
    const raw = parsed[key]
    if (Array.isArray(raw)) return raw.length
    if (raw !== undefined && raw !== null && raw !== '') {
      const n = Number(raw)
      if (Number.isFinite(n) && n > 0) return Math.trunc(n)
    }
  }
  return detail.filter((item) => item.ok === false).length
}

/** 没有 summary 的历史结果：按字段拼一句人话（旧版 scheduleResultText 的口径）。 */
function fallbackSummary(parsed: Record<string, unknown>, failed: number): string {
  const bits: string[] = []
  const claimed = int(parsed.claimed, 0)
  const credit = Number(parsed.credit)
  const already = int(parsed.skipped_already, 0)
  const refreshed = int(parsed.refreshed, 0)
  const added = int(parsed.added, 0)
  const updated = int(parsed.updated, 0)
  const alive = int(parsed.alive, 0)
  const total = int(parsed.total, 0)
  if (claimed) bits.push('领取 ' + claimed + ' 个')
  if (Number.isFinite(credit) && credit) bits.push('+' + credit + ' 积分')
  if (already) bits.push(already + ' 个今日已领过')
  if (refreshed) bits.push('刷新 ' + refreshed + ' 个')
  if (added || updated) bits.push('新增 ' + added + ' · 更新 ' + updated)
  if (alive || total) bits.push('成功 ' + alive + ' / ' + total)
  if (failed) bits.push('失败 ' + failed)
  return bits.join(' · ')
}

/**
 * 解析 last_result（字符串 JSON）。
 *
 * 判定的顺序是按「用户最需要先知道的事」排的：
 * 执行中 → 已顺延 → 失败（error）→ 已跳过 → 部分失败 → 成功。
 * 解析失败的旧数据（后端曾把 JSON 截断）单独标成「旧格式」，不当成无结果。
 */
export function parseScheduleResult(raw: unknown): ScheduleResultView {
  const rawText = typeof raw === 'string' ? raw : raw === null || raw === undefined ? '' : String(raw)
  if (!rawText.trim()) {
    return {
      tone: 'neutral',
      label: '尚未运行',
      summary: '',
      running: false,
      raw: '',
      parsed: null,
      detail: [],
    }
  }
  let parsed: unknown = null
  try {
    parsed = JSON.parse(rawText)
  } catch {
    parsed = null
  }
  if (!isRecord(parsed)) {
    return {
      tone: 'warn',
      label: '旧格式',
      summary: '历史结果无法解析，点「结果详情」查看原文',
      running: false,
      raw: rawText,
      parsed: null,
      detail: [],
    }
  }
  const detail = Array.isArray(parsed.detail)
    ? parsed.detail.filter(isRecord)
    : []
  const failed = failedCountOf(parsed, detail)
  const summaryText = plain(text(parsed.summary))
  const elapsed = Number(parsed.elapsed_s)
  const withElapsed = (value: string): string =>
    value && Number.isFinite(elapsed) && elapsed > 0 ? value + ' · ' + elapsed + 's' : value

  if (parsed.running === true) {
    return {
      tone: 'brand',
      label: '执行中',
      summary: summaryText || '后台执行中，完成后自动刷新',
      running: true,
      raw: rawText,
      parsed,
      detail,
    }
  }
  if (parsed.deferred === true) {
    return {
      tone: 'warn',
      label: '已顺延',
      summary: summaryText || text(parsed.error) || '出口保护期间顺延，稍后自动重试',
      running: false,
      raw: rawText,
      parsed,
      detail,
    }
  }
  if (parsed.error) {
    return {
      tone: 'danger',
      label: '失败',
      summary: withElapsed(plain(text(parsed.error))),
      running: false,
      raw: rawText,
      parsed,
      detail,
    }
  }
  if (parsed.skipped && parsed.ok !== false) {
    return {
      tone: 'neutral',
      label: '已跳过',
      summary: plain(text(parsed.skipped)),
      running: false,
      raw: rawText,
      parsed,
      detail,
    }
  }
  if (parsed.ok === false) {
    return {
      tone: 'danger',
      label: '失败',
      summary: withElapsed(summaryText || fallbackSummary(parsed, failed) || '任务返回失败'),
      running: false,
      raw: rawText,
      parsed,
      detail,
    }
  }
  if (failed > 0) {
    return {
      tone: 'warn',
      label: '部分失败',
      summary: withElapsed(summaryText || fallbackSummary(parsed, failed)),
      running: false,
      raw: rawText,
      parsed,
      detail,
    }
  }
  return {
    tone: RESULT_TONE_OK,
    label: '成功',
    summary: withElapsed(summaryText || fallbackSummary(parsed, 0) || '运行完成'),
    running: false,
    raw: rawText,
    parsed,
    detail,
  }
}

export interface ToScheduleRowsOptions {
  /** 全局出口与并发（决定「跟随全局」的具体值）。 */
  globalProxy?: ScheduleGlobalProxy | null
  /** 判定「今天/明天」的基准时刻（单测注入）。 */
  now?: number
}

/** 接口 items[] → 表格行。 */
export function toScheduleRows(
  items: ScheduleItem[] | null | undefined,
  options: ToScheduleRowsOptions = {},
): ScheduleRow[] {
  const now = options.now ?? Date.now()
  const global = options.globalProxy ?? null
  return (items || []).map((item) => {
    const timing = normalizeTiming(item.timing)
    const intervalMinutes = Math.max(1, int(item.interval_minutes, 60))
    const result = parseScheduleResult(item.last_result)
    const enabled = item.enabled !== false
    const proxyPool = text(item.proxy_pool)
    const globalPool = text(global?.proxy_pool)
    const globalConcurrency = int(global?.concurrency, 0)
    const proxyText = proxyPool
      ? '代理组 ' + proxyPool
      : globalPool
        ? '跟随全局（代理组 ' + globalPool + '）'
        : globalConcurrency > 0
          ? '跟随全局（并发 ' + globalConcurrency + '）'
          : '直连'
    const nextText = !enabled
      ? '已停用'
      : result.running
        ? '执行中'
        : formatNextRun(item.next_run_at, now)
    return {
      key: String(item.id),
      id: item.id,
      name: text(item.name),
      displayName: text(item.display_name) || text(item.name) || '# ' + item.id,
      task: text(item.task),
      taskLabel: text(item.task_label) || text(item.task),
      platform: text(item.platform),
      platformLabel: text(item.platform_label) || text(item.platform) || 'WorkBuddy',
      cycleText: text(item.cycle_label) || buildCycleLabel(timing, intervalMinutes),
      zoneHint: isCalendarMode(timing.mode) ? CALENDAR_ZONE_HINT : '',
      intervalMinutes,
      timing,
      nextRunAt: item.next_run_at ?? null,
      nextText,
      lastRunAt: item.last_run_at ?? null,
      lastRunText: fmtTime(item.last_run_at),
      result,
      running: result.running,
      enabled,
      dueSoon: Boolean(item.due_soon),
      proxyPool,
      proxyText,
      concurrency: int(item.concurrency, 0),
      budgetMinutes: int(item.time_budget_minutes, 0),
      staleMinutes: int(item.stale_minutes, 0),
      stopAfter: item.stop_after ?? null,
    }
  })
}

export interface ScheduleFilters {
  /** 搜索任务名 / 任务类型 / 平台名。 */
  q?: string
  /** 落库平台 id；'' = 全部。 */
  platform?: string
  /** 任务类型（task）；'' = 全部。 */
  task?: string
  /** '' | 'enabled' | 'disabled' | 'due' */
  state?: string
  /** '' | 'ok' | 'warn' | 'danger' | 'none' */
  result?: string
}

/** 筛选（大小写不敏感）。返回新数组。 */
export function filterScheduleRows(rows: ScheduleRow[], filters: ScheduleFilters = {}): ScheduleRow[] {
  const keyword = text(filters.q).toLowerCase()
  const platform = text(filters.platform)
  const task = text(filters.task)
  const state = text(filters.state)
  const result = text(filters.result)
  return rows.filter((row) => {
    if (keyword) {
      const haystack = [row.displayName, row.name, row.taskLabel, row.platformLabel, row.proxyPool]
        .join('\n')
        .toLowerCase()
      if (!haystack.includes(keyword)) return false
    }
    if (platform && row.platform !== platform) return false
    if (task && row.task !== task) return false
    if (state === 'enabled' && !row.enabled) return false
    if (state === 'disabled' && row.enabled) return false
    if (state === 'due' && !(row.enabled && row.dueSoon)) return false
    if (result === 'none' && row.result.parsed !== null) return false
    if (result && result !== 'none' && row.result.tone !== result) return false
    return true
  })
}

export type ScheduleSortOrder = 'asc' | 'desc' | ''

export const SCHEDULE_SORT_KEYS = ['name', 'platform', 'next_run_at', 'last_run_at'] as const
export type ScheduleSortKey = (typeof SCHEDULE_SORT_KEYS)[number]

function timeOf(value: string | null): number | null {
  const date = toDate(value)
  return date ? date.getTime() : null
}

/**
 * 时间比较：空值（停用 / 未运行）**恒排最后**，与升降序无关 ——
 * 「下次执行」按倒序时把停用任务顶到最上面是最难用的一种顺序。
 */
function compareTime(a: number | null, b: number | null, direction: number): number {
  if (a === null && b === null) return 0
  if (a === null) return 1
  if (b === null) return -1
  return (a - b) * direction
}

function compareRows(a: ScheduleRow, b: ScheduleRow, sortBy: ScheduleSortKey, direction: number): number {
  if (sortBy === 'name') return a.displayName.localeCompare(b.displayName, 'zh-CN') * direction
  if (sortBy === 'platform') {
    const diff = a.platformLabel.localeCompare(b.platformLabel, 'zh-CN')
    return diff !== 0 ? diff * direction : a.id - b.id
  }
  if (sortBy === 'next_run_at') return compareTime(timeOf(a.nextRunAt), timeOf(b.nextRunAt), direction)
  return compareTime(timeOf(a.lastRunAt), timeOf(b.lastRunAt), direction)
}

/** 排序。sortBy 为空 → 后端顺序（Schedule.id ASC）。同值按 id 升序稳定。 */
export function sortScheduleRows(
  rows: ScheduleRow[],
  sortBy: string,
  order: ScheduleSortOrder,
): ScheduleRow[] {
  const key = text(sortBy) as ScheduleSortKey
  if (!key || !SCHEDULE_SORT_KEYS.includes(key) || (order !== 'asc' && order !== 'desc')) {
    return rows.slice()
  }
  const direction = order === 'desc' ? -1 : 1
  return rows.slice().sort((a, b) => {
    const diff = compareRows(a, b, key, direction)
    return diff !== 0 ? diff : a.id - b.id
  })
}

export interface PaginatedScheduleRows {
  items: ScheduleRow[]
  total: number
  pageCount: number
  page: number
}

export function paginateScheduleRows(
  rows: ScheduleRow[],
  page: number,
  pageSize: number,
): PaginatedScheduleRows {
  const size = Math.max(1, Math.trunc(Number(pageSize) || 20))
  const total = rows.length
  const pageCount = Math.ceil(total / size)
  const current = pageCount ? Math.min(Math.max(1, Math.trunc(Number(page) || 1)), pageCount) : 1
  const start = (current - 1) * size
  return { items: rows.slice(start, start + size), total, pageCount, page: current }
}

export interface ScheduleStats {
  total: number
  enabled: number
  disabled: number
  /** 启用且后端判定已到期（due_soon）。 */
  due: number
  /** 最近结果失败或部分失败的启用任务数。 */
  problem: number
  /** 正在执行的任务数。 */
  running: number
}

export function scheduleStats(rows: ScheduleRow[]): ScheduleStats {
  let enabled = 0
  let due = 0
  let problem = 0
  let running = 0
  for (const row of rows) {
    if (row.enabled) enabled += 1
    if (row.enabled && row.dueSoon) due += 1
    if (row.enabled && (row.result.tone === 'danger' || row.result.tone === 'warn')) problem += 1
    if (row.running) running += 1
  }
  return { total: rows.length, enabled, disabled: rows.length - enabled, due, problem, running }
}

/* ------------------------------ 执行间隔 ------------------------------ */

export interface IntervalParts {
  value: number
  /** 1 / 60 / 1440 / 10080。 */
  unit: number
}

/** 分钟 → 表单里的「数值 + 单位」（取能整除的最大单位，与旧版一致）。 */
export function intervalParts(minutes: unknown): IntervalParts {
  const raw = int(minutes, 60)
  const total = raw > 0 ? raw : 60
  for (const unit of [10080, 1440, 60]) {
    if (total % unit === 0) return { value: total / unit, unit }
  }
  return { value: total, unit: 1 }
}

/** 「数值 + 单位」→ 分钟（夹紧到后端的 1..5256000）。 */
export function intervalOf(value: unknown, unit: unknown): number {
  const size = [1, 60, 1440, 10080].includes(int(unit, 1)) ? int(unit, 1) : 1
  const count = Math.max(1, int(value, 1))
  return Math.min(5256000, Math.max(1, count * size))
}

/* ------------------------------ 表单 ------------------------------ */

export interface ScheduleForm {
  /** null = 新建。 */
  id: number | null
  name: string
  task: string
  mode: ScheduleMode
  intervalValue: number
  intervalUnit: number
  /** HH:MM（日历周期）。 */
  time: string
  weekdays: number[]
  monthday: number
  proxyPool: string
  concurrency: number
  budgetMinutes: number
  staleMinutes: number
  /** 'YYYY-MM-DDTHH:mm'（datetime-local 口径的本地时间）；空串 = 不限。 */
  stopAfter: string
  enabled: boolean
}

export const DEFAULT_PROXY_FORM = { proxyPool: '', concurrency: 0, budgetMinutes: 0, staleMinutes: 0 }

/** 新建表单：与旧版 openAddSchedule 的默认值一致（启用、每 60 分钟、refresh_balances）。 */
export function emptyScheduleForm(task = 'refresh_balances'): ScheduleForm {
  return {
    id: null,
    name: '',
    task,
    mode: 'preset',
    intervalValue: 60,
    intervalUnit: 1,
    time: '09:00',
    weekdays: [0],
    monthday: 1,
    proxyPool: '',
    concurrency: 0,
    budgetMinutes: 0,
    staleMinutes: 0,
    stopAfter: '',
    enabled: true,
  }
}

/** 编辑表单：从列表行（或接口项）回填。 */
export function scheduleFormOf(item: ScheduleItem | ScheduleRow): ScheduleForm {
  const timing = normalizeTiming(item.timing)
  const interval = intervalParts(item.interval_minutes)
  const stopAfter = item.stop_after ? String(item.stop_after).replace(' ', 'T').slice(0, 16) : ''
  return {
    id: item.id,
    name: text(item.name),
    task: text(item.task),
    mode: timing.mode,
    intervalValue: interval.value,
    intervalUnit: interval.unit,
    time: timing.time,
    weekdays: timing.weekdays.slice(),
    monthday: timing.monthday,
    proxyPool: text(item.proxy_pool),
    concurrency: int(item.concurrency, 0),
    budgetMinutes: int(item.time_budget_minutes, 0),
    staleMinutes: int(item.stale_minutes, 0),
    stopAfter,
    enabled: item.enabled !== false,
  }
}

/** 前端校验（错误 key 与 FormField 一一对应）；通过返回空对象。 */
export function validateScheduleForm(form: ScheduleForm): Record<string, string> {
  const errors: Record<string, string> = {}
  if (text(form.name).length > 64) errors.name = '任务名称最多 64 个字符'
  if (!text(form.task)) errors.task = '请选择任务类型'
  if (isCalendarMode(form.mode)) {
    if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(text(form.time))) {
      errors.time = '执行时间格式必须为 HH:MM（例如 09:00）'
    }
  } else {
    const minutes = intervalOf(form.intervalValue, form.intervalUnit)
    if (!Number.isFinite(minutes) || minutes < 1 || minutes > 5256000) {
      errors.interval = '执行间隔必须在 1 分钟到 10 年之间'
    }
  }
  if (form.mode === 'weekly' && !form.weekdays.length) errors.weekdays = '请至少选择一天'
  if (form.mode === 'monthly' && (form.monthday < 1 || form.monthday > 31)) {
    errors.monthday = '每月执行日必须在 1–31 之间'
  }
  if (form.stopAfter && !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(form.stopAfter)) {
    errors.stopAfter = '停止时间格式不对，请重新选择'
  }
  const concurrency = int(form.concurrency, 0)
  if (concurrency < 0 || concurrency > 64) errors.concurrency = '并发数必须在 0–64 之间'
  const budget = int(form.budgetMinutes, 0)
  if (budget < 0 || budget > 720) errors.budget = '单轮时间预算必须在 0–720 分钟之间'
  const stale = int(form.staleMinutes, 0)
  if (stale < 0 || stale > 10080) errors.stale = 'stale 阈值必须在 0–10080 分钟之间'
  return errors
}

/**
 * 表单 → 请求体。
 *
 * platform 由任务目录决定（后端按它找平台的 runner；平台专属任务不带就是
 * 「该平台未注册此任务」）。stop_after 只对签到类任务有意义，其它任务一律 null。
 */
export function buildScheduleInput(
  form: ScheduleForm,
  catalog: ScheduleTaskOption[] | null | undefined,
): ScheduleInput {
  const timing = normalizeTiming({
    mode: form.mode,
    time: form.time,
    weekdays: form.weekdays,
    monthday: form.monthday,
  })
  const task = text(form.task)
  const hit = (catalog || []).find((item) => item.value === task)
  const isCalendar = isCalendarMode(timing.mode)
  return {
    name: text(form.name),
    task,
    platform: hit ? text(hit.platform) : '',
    interval_minutes: intervalOf(form.intervalValue, form.intervalUnit),
    timing: {
      mode: timing.mode,
      time: isCalendar ? timing.time : '09:00',
      weekdays: timing.mode === 'weekly' ? timing.weekdays : [0],
      monthday: timing.mode === 'monthly' ? clamp(timing.monthday, 1, 31) : 1,
    },
    enabled: form.enabled ? 1 : 0,
    stop_after:
      isCheckinTask(task) && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(form.stopAfter)
        ? form.stopAfter.slice(0, 16)
        : null,
    proxy_pool: text(form.proxyPool).slice(0, 32),
    concurrency: clamp(form.concurrency, 0, 64),
    time_budget_minutes: clamp(form.budgetMinutes, 0, 720),
    stale_minutes: clamp(form.staleMinutes, 0, 10080),
  }
}

/* ------------------------------ 出口代理组 ------------------------------ */

/** 用 type（不是 interface）：naive-ui 的 SelectBaseOption 带 `[k: string]: unknown`，
 *  只有 type 别名才有隐式索引签名，直接传给 NSelect 的 options 才不会报类型错。 */
export type ProxyPoolOption = {
  label: string
  value: string
  disabled?: boolean
}

/**
 * 「出口代理组」下拉选项。
 *
 * 第一项固定是「跟随全局」（空值 = 直连 / 用全局默认）；其后是 /api/proxies 的 pools，
 * 带「可用/总数」；若当前任务填的组已经不存在，也补一项（标「节点已不存在」），
 * 否则 NSelect 会把它显示成空白，保存时被静默清掉。
 */
export function buildProxyPoolOptions(
  payload: ProxyListPayload | null | undefined,
  global: ScheduleGlobalProxy | null | undefined,
  current: string,
): ProxyPoolOption[] {
  const pools = Array.isArray(payload?.pools) ? payload.pools.map(text).filter(Boolean) : []
  const items = Array.isArray(payload?.items) ? payload.items : []
  const counts = new Map<string, { total: number; ready: number }>()
  for (const item of items) {
    const pool = text(item.pool)
    const entry = counts.get(pool) || { total: 0, ready: 0 }
    entry.total += 1
    if (item.state === 'ready') entry.ready += 1
    counts.set(pool, entry)
  }
  const globalPool = text(global?.proxy_pool)
  const options: ProxyPoolOption[] = [
    {
      label: globalPool ? '跟随全局（当前：代理组 ' + globalPool + '）' : '跟随全局（当前：直连）',
      value: '',
    },
  ]
  for (const pool of pools) {
    const stat = counts.get(pool) || { total: 0, ready: 0 }
    options.push({ label: '代理组 ' + pool + ' · ' + stat.ready + '/' + stat.total + ' 可用', value: pool })
  }
  const currentPool = text(current)
  if (currentPool && !pools.includes(currentPool)) {
    options.push({ label: '代理组 ' + currentPool + ' · 节点已不存在', value: currentPool })
  }
  if (!pools.length) {
    options.push({ label: '还没有配置代理节点', value: '', disabled: true })
  }
  return options
}

/** 结果详情抽屉的键值：把后端字段名翻成中文（旧版 FIELD_LABELS 的口径）。 */
export const RESULT_FIELD_LABELS: Record<string, string> = {
  task: '任务',
  ok: '结果',
  platform: '平台',
  realm: '区域',
  total: '账号数',
  claimed: '新领取',
  granted: '新领取',
  skipped_already: '今日已领',
  failed: '失败',
  failed_count: '失败',
  fail: '失败',
  credit: '获得积分',
  quota_total: '今日权益合计',
  refreshed: '刷新成功',
  added: '新增模型',
  updated: '更新模型',
  total_in_db: '库内模型',
  alive: '保活成功',
  disabled: '已禁用',
  elapsed_s: '耗时（秒）',
  summary: '摘要',
  skipped: '跳过原因',
  error: '错误',
  deferred: '顺延',
  running: '执行中',
  detail_truncated: '明细已截断',
}
