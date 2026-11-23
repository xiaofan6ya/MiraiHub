/**
 * API Keys（/api/keys/*）与 Key 页需要的辅助接口。
 *
 * 旧版实现：admin/console/console-app.js 的 loadKeys / keyFormData / openAddKey / editKey /
 * toggleKeyStatus / deleteKey / loadShareInfo / renderShareAddress，以及 key-ip.js（来源 IP 统计）。
 * 后端：admin/routers/keys.py、admin/routers/credits.py（selection-policy）、
 * admin/routers/models.py（aggregate）、admin/routers/releases.py（network）、admin/routers/groups.py。
 *
 * 约定（05-patterns §1）：字段名与后端一致，不做驼峰转换；页面只消费本文件的纯函数结果。
 */
import { http } from './http'
import { FMT_EMPTY, fmtCredit, fmtTime, maskSecret } from '@/utils/format'

/* ------------------------------------------------------------------ *
 * 类型
 * ------------------------------------------------------------------ */

/** /api/keys 列表里每一项的 IP 摘要（keys.py:_key_ip_summaries）。 */
export interface KeyIpSummary {
  days: number
  unique_ips: number
  requests: number
  active_ips: number
  active_requests: number
  active_ip_counts?: Record<string, number>
  top_ips?: Array<{ ip: string; active_requests: number; requests: number }>
  active_scope?: string
}

/** GET /api/keys items[]（keys.py:list_keys）。 */
export interface ApiKeyItem {
  id: number
  name: string
  key_prefix: string | null
  /** 后端已打码：前 10 + **** + 后 4。 */
  masked: string
  credit_limit: number
  credit_used: number
  unlimited: boolean
  max_concurrent: number | null
  rate_per_minute: number | null
  /** active | revoked */
  status: string
  note: string
  group_id: number
  group_name: string
  account_id: number | null
  account_ids: number[]
  platform: string
  aggregate: boolean
  account_name: string
  created_at: string | null
  ip_summary: KeyIpSummary
}

export interface ApiKeyListPayload {
  items: ApiKeyItem[]
}

/** POST /api/keys 请求体（KeyIn）。 */
export interface KeyCreateInput {
  name: string
  /** 不限额时置 undefined，由 unlimited 决定。 */
  credit_limit?: number | null
  unlimited?: boolean
  note?: string
  group_id?: number
  account_id?: number | null
  account_ids?: number[]
  platform?: string
  aggregate?: boolean
  max_concurrent?: number | null
  rate_per_minute?: number | null
}

/** POST /api/keys 响应：明文 key 只在这一个响应里出现（但之后可 /view 再查）。 */
export interface KeyCreatePayload {
  id: number
  name: string
  key: string
  key_prefix: string
  credit_limit: number
  unlimited: boolean
  max_concurrent: number | null
  rate_per_minute: number | null
  group_id: number
  account_id: number | null
  account_ids: number[]
  platform: string
  aggregate: boolean
}

/** GET /api/keys/{id}/view（410 = 该 Key 创建于查看功能上线前）。 */
export interface KeyViewPayload {
  id: number
  name: string
  key: string
  key_prefix: string
  created_at: string | null
}

/** GET /api/keys/{id}/usage。 */
export interface KeyUsagePayload {
  id: number
  name: string
  credit_limit: number
  credit_used: number
  credit_remaining: number | null
  usage_percent: number | null
  unlimited: boolean
  status: string
  created_at: string | null
}

export interface KeyIpModelUse {
  model: string
  requests: number
  tokens: number
  last_seen: string | null
  recent: boolean
  live?: boolean
  live_requests?: number
}

export interface KeyIpRow {
  ip: string
  requests: number
  failed: number
  credits: number
  tokens: number
  last_seen: string | null
  models: KeyIpModelUse[]
  active_requests: number
  blocked: boolean
  blocked_id: number | null
}

/** GET /api/keys/{id}/ips。 */
export interface KeyIpsPayload {
  id: number
  name: string
  days: number
  sort: string
  direction: string
  offset: number
  limit: number
  total_ips: number
  total_requests: number
  active_ips: number
  active_requests: number
  active_scope: string
  active: Array<{ ip: string; active_requests: number; blocked: boolean; blocked_id: number | null }>
  items: KeyIpRow[]
}

/** GET /api/credits/selection-policy（keys 页头部展示当前选号策略）。 */
export interface SelectionPolicyPayload {
  mode: string
  upstream_start_interval_ms: number
  modes: Array<{ value: string; label: string; description: string }>
}

/** GET /api/app/network。 */
export interface NetworkPayload {
  lan_ips: string[]
  port: number
}

export interface AggregateModelPlatform {
  platform: string
  /** 落库模型 id（上游原始 id）。 */
  model_id: string
  /** 调用名（总带平台后缀）；老字段可能缺失，用 clientModelId() 兜底。 */
  client_id?: string
  multiplier?: number | null
  description?: string
  enabled?: boolean
  platform_label?: string
}

/** GET /api/models/aggregate items[]（同一模型跨平台合一）。 */
export interface AggregateModelItem {
  key: string
  name: string
  platform_count: number
  min_multiplier?: number | null
  free_any?: boolean
  platforms: AggregateModelPlatform[]
}

export interface AggregateModelsPayload {
  items: AggregateModelItem[]
  total_models?: number
  total_groups?: number
  multi_platform_groups?: number
}

export interface GroupOption {
  id: number
  name: string
  effective_count?: number
}

export interface AccountOption {
  id: number
  name?: string
  uid?: string
  platform?: string
  status?: string
  balance_remain?: number
  bound_key_id?: number | null
}

export interface AccountListPayload {
  items?: AccountOption[]
  accounts?: AccountOption[]
  total?: number
}

export interface PlatformOption {
  storage: string
  label: string
}

/* ------------------------------------------------------------------ *
 * 接口
 * ------------------------------------------------------------------ */

export function listKeys(signal?: AbortSignal): Promise<ApiKeyListPayload> {
  return http<ApiKeyListPayload>('GET', '/api/keys', { signal })
}

export function createKey(body: KeyCreateInput): Promise<KeyCreatePayload> {
  return http<KeyCreatePayload>('POST', '/api/keys', { body })
}

export function viewKey(id: number): Promise<KeyViewPayload> {
  return http<KeyViewPayload>('GET', '/api/keys/' + id + '/view')
}

export function keyUsage(id: number): Promise<KeyUsagePayload> {
  return http<KeyUsagePayload>('GET', '/api/keys/' + id + '/usage')
}

export function keyIps(
  id: number,
  options: { days?: number; sort?: string; direction?: 'asc' | 'desc'; limit?: number; offset?: number } = {},
): Promise<KeyIpsPayload> {
  return http<KeyIpsPayload>('GET', '/api/keys/' + id + '/ips', {
    query: {
      days: options.days ?? 1,
      sort: options.sort ?? 'requests',
      direction: options.direction ?? 'desc',
      limit: options.limit ?? 100,
      offset: options.offset ?? 0,
    },
  })
}

/** PATCH /api/keys/{id}：编辑、启停、重置已用都走它（后端按字段增量处理）。 */
export interface KeyPatchInput {
  name?: string
  note?: string
  group_id?: number
  account_id?: number | null
  account_ids?: number[]
  platform?: string
  aggregate?: boolean
  credit_limit?: number | null
  unlimited?: boolean
  max_concurrent?: number | null
  rate_per_minute?: number | null
  status?: 'active' | 'revoked'
  reset_used?: boolean
}

export function patchKey(id: number, body: KeyPatchInput): Promise<{ id: number; ok: boolean }> {
  return http('PATCH', '/api/keys/' + id, { body })
}

export function deleteKey(id: number): Promise<{ id: number; ok: boolean }> {
  return http('DELETE', '/api/keys/' + id)
}

export function getSelectionPolicy(): Promise<SelectionPolicyPayload> {
  return http<SelectionPolicyPayload>('GET', '/api/credits/selection-policy')
}

export function getNetwork(): Promise<NetworkPayload> {
  return http<NetworkPayload>('GET', '/api/app/network')
}

export function listAggregateModels(): Promise<AggregateModelsPayload> {
  return http<AggregateModelsPayload>('GET', '/api/models/aggregate')
}

export function listGroupOptions(): Promise<{ items: GroupOption[] }> {
  return http('GET', '/api/groups')
}

/** 账号候选：只取一页（旧版一样，见 keyFormData 的注释）。 */
export function listAccountOptions(params: { search?: string; page?: number; pageSize?: number } = {}): Promise<AccountListPayload> {
  return http<AccountListPayload>('GET', '/api/accounts', {
    query: {
      all_platforms: 1,
      page: params.page ?? 1,
      page_size: params.pageSize ?? 50,
      search: params.search || '',
    },
  })
}

export function listPlatformOptions(): Promise<{ items: Array<Record<string, unknown>> }> {
  return http('GET', '/api/platforms')
}

/* ------------------------------------------------------------------ *
 * 纯函数：接口响应 → 展示数据（单测覆盖）
 * ------------------------------------------------------------------ */

/** 表格行（DataTable 的行是 Record<string, unknown>）。 */
export interface KeyRow extends Record<string, unknown> {
  id: number
  name: string
  masked: string
  keyPrefix: string
  limitText: string
  usedText: string
  usageRatio: number
  usageTone: 'ok' | 'warn' | 'danger'
  usageText: string
  unlimited: boolean
  concurrencyText: string
  rateText: string
  ipText: string
  ipBadges: Array<{ ip: string; text: string }>
  groupLabel: string
  statusLabel: string
  statusTone: 'ok' | 'neutral'
  accountLabel: string
  /** 后端没有 Key 到期字段：这一列如实显示 —。 */
  expireText: string
  note: string
  platform: string
  aggregate: boolean
  groupId: number
  createdAt: string
  status: string
  creditLimit: number
  creditUsed: number
  maxConcurrent: number | null
  ratePerMinute: number | null
  accountIds: number[]
  accountId: number | null
  accountName: string
  ipSummary: KeyIpSummary
}

function pctText(ratio: number): string {
  return (Math.round(ratio * 1000) / 10).toFixed(1) + '%'
}

/** 单条 Key → 表格行。所有格式化口径集中在这里，页面只渲染。 */
export function toKeyRow(item: ApiKeyItem): KeyRow {
  const unlimited = Boolean(item.unlimited)
  const limit = Number(item.credit_limit || 0)
  const used = Number(item.credit_used || 0)
  const ratio = unlimited || limit <= 0 ? 0 : Math.min(1, Math.max(0, used / limit))
  const usageTone: 'ok' | 'warn' | 'danger' = unlimited ? 'ok' : ratio >= 0.9 ? 'danger' : ratio >= 0.6 ? 'warn' : 'ok'
  const summary = item.ip_summary || { days: 1, unique_ips: 0, requests: 0, active_ips: 0, active_requests: 0 }
  const maxConcurrent = item.max_concurrent ?? null
  const ratePerMinute = item.rate_per_minute ?? null
  const accountIds = Array.isArray(item.account_ids) ? item.account_ids.map(Number).filter((id) => id > 0) : []
  const masked = item.masked || (item.key_prefix ? maskSecret(item.key_prefix) : FMT_EMPTY)
  return {
    id: item.id,
    name: item.name || '未命名',
    masked,
    keyPrefix: item.key_prefix || '',
    limitText: unlimited ? '∞' : fmtCredit(limit),
    usedText: fmtCredit(used),
    usageRatio: ratio,
    usageTone,
    usageText: unlimited ? '不限额度' : pctText(ratio),
    unlimited,
    concurrencyText: maxConcurrent === 0 ? '不限' : maxConcurrent == null ? '全局' : String(maxConcurrent),
    rateText: ratePerMinute === 0 ? '不限频率' : ratePerMinute == null ? '频率随全局' : ratePerMinute + ' 次/分',
    ipText: (Number(summary.unique_ips) || 0) + ' 个 / 24h',
    ipBadges: (summary.top_ips || []).slice(0, 3).map((entry) => ({
      ip: entry.ip,
      text: entry.ip + ' · ' + (Number(entry.active_requests) || 0) + ' 并发 · ' + (Number(entry.requests) || 0) + ' 次',
    })),
    groupLabel: item.group_name || '全部模型',
    statusLabel: item.status === 'active' ? '正常' : '已停用',
    statusTone: item.status === 'active' ? 'ok' : 'neutral',
    accountLabel: accountIds.length
      ? '独占 ' + accountIds.length + ' 个账号'
      : item.account_id
        ? '独占账号：' + (item.account_name || '#' + item.account_id)
        : '公共账号池',
    expireText: FMT_EMPTY,
    note: item.note || '',
    platform: item.platform || '',
    aggregate: Boolean(item.aggregate),
    groupId: Number(item.group_id || 0),
    createdAt: item.created_at || '',
    status: item.status,
    creditLimit: limit,
    creditUsed: used,
    maxConcurrent,
    ratePerMinute,
    accountIds,
    accountId: item.account_id,
    accountName: item.account_name || '',
    ipSummary: summary,
  }
}

export function toKeyRows(items: ApiKeyItem[] | null | undefined): KeyRow[] {
  return Array.isArray(items) ? items.map(toKeyRow) : []
}

export interface KeyFilters {
  q?: string
  group?: string
  status?: string
}

/** 名称搜索 + 分组 + 状态（旧版只有名称搜索；分组/状态是新版补的）。 */
export function filterKeyRows(rows: KeyRow[], filters: KeyFilters): KeyRow[] {
  const term = String(filters.q || '').trim().toLowerCase()
  const group = String(filters.group || '')
  const status = String(filters.status || '')
  return rows.filter((row) => {
    if (term) {
      const hay = [row.name, row.note, row.accountName, row.platform].join(' ').toLowerCase()
      if (!hay.includes(term)) return false
    }
    if (group === 'none' && row.groupId !== 0) return false
    if (group && group !== 'none' && String(row.groupId) !== group) return false
    if (status && row.status !== status) return false
    return true
  })
}

export type KeySortKey = 'name' | 'limit' | 'used' | 'concurrency' | 'created'

export function sortKeyRows(rows: KeyRow[], sortBy: string, order: string): KeyRow[] {
  if (!sortBy || (order !== 'asc' && order !== 'desc')) return rows
  const factor = order === 'asc' ? 1 : -1
  const rank = (row: KeyRow): number => {
    if (sortBy === 'name') return 0
    if (sortBy === 'limit') return row.unlimited ? Number.MAX_SAFE_INTEGER : row.creditLimit
    if (sortBy === 'used') return row.creditUsed
    if (sortBy === 'concurrency') return row.maxConcurrent == null ? -1 : row.maxConcurrent
    if (sortBy === 'created') return Date.parse(row.createdAt || '') || 0
    return 0
  }
  return [...rows].sort((a, b) => {
    if (sortBy === 'name') return a.name.localeCompare(b.name, 'zh-CN') * factor
    return (rank(a) - rank(b)) * factor
  })
}

export function paginateKeyRows(rows: KeyRow[], page: number, pageSize: number): { items: KeyRow[]; total: number; page: number; pageCount: number } {
  const size = Math.max(1, Math.floor(pageSize || 20))
  const total = rows.length
  const pageCount = Math.max(1, Math.ceil(total / size))
  const current = Math.min(Math.max(1, Math.floor(page || 1)), pageCount)
  return { items: rows.slice((current - 1) * size, current * size), total, page: current, pageCount }
}

export interface KeyStats {
  total: number
  active: number
  revoked: number
  unlimited: number
}

export function keyStats(rows: KeyRow[]): KeyStats {
  return {
    total: rows.length,
    active: rows.filter((row) => row.status === 'active').length,
    revoked: rows.filter((row) => row.status !== 'active').length,
    unlimited: rows.filter((row) => row.unlimited).length,
  }
}

/** 能力探测：该 Key 的明文是否还能再查（旧版始终显示「查看完整 Key」按钮）。 */
export function canRevealKey(row: KeyRow): boolean {
  return Boolean(row.keyPrefix)
}

export function statusPatch(status: string): KeyPatchInput {
  return { status: status === 'active' ? 'revoked' : 'active' }
}

/* ------------------------------------------------------------------ *
 * 对接信息：连接地址 / 模型 chip / curl 示例
 * ------------------------------------------------------------------ */

/** 调用名：总带平台后缀（旧版 modelClientId 的移植，注意不同平台会同名）。 */
export function clientModelId(modelId: string, platform: string): string {
  const base = String(modelId || '')
  const pid = String(platform || '')
  if (!base || !pid) return base
  return base.toLowerCase().endsWith('-' + pid.toLowerCase()) ? base : base + '-' + pid
}

export interface ModelChipView {
  key: string
  name: string
  platformCount: number
  /** 点击复制的内容（第一个平台的可调用 id）。 */
  copyValue: string
  /** 悬停提示：各平台 id 与倍率。 */
  title: string
}

/** 可用模型 chip：只显示前 limit 个（设计稿 12），其余进「全部」抽屉。 */
export function modelChips(payload: AggregateModelsPayload | null, limit = 12): { chips: ModelChipView[]; total: number; rest: number } {
  const items = (payload?.items || []).filter((item) => (item.platforms || []).some((p) => p.enabled !== false))
  const chips = items.slice(0, Math.max(0, limit)).map((item) => {
    const platforms = (item.platforms || []).filter((p) => p.enabled !== false)
    const first = platforms[0]
    const title = (platforms.length
      ? platforms
          .map((p) => {
            const id = p.client_id || clientModelId(p.model_id, p.platform)
            const mult = p.multiplier == null ? FMT_EMPTY : Number(p.multiplier) === 0 ? 'FREE' : '×' + p.multiplier
            return (p.platform_label || p.platform) + ' · ' + id + ' · ' + mult
          })
          .join('\n')
      : '暂无平台明细') + '\n点击复制调用名（带平台后缀）'
    return {
      key: item.key || String(item.name),
      name: item.name,
      platformCount: platforms.length || Number(item.platform_count || 0),
      copyValue: first ? first.client_id || clientModelId(first.model_id, first.platform) : '',
      title,
    }
  })
  return { chips, total: items.length, rest: Math.max(0, items.length - chips.length) }
}

export interface ModelSearchHit {
  key: string
  name: string
  platformCount: number
  rows: Array<{ platform: string; platformLabel: string; callId: string; upstreamId: string; multiplierText: string }>
}

/** 「全部模型」抽屉：搜索 + 每个模型展开各平台明细（点击复制调用名）。 */
export function searchModelChips(payload: AggregateModelsPayload | null, term: string): ModelSearchHit[] {
  const query = String(term || '').trim().toLowerCase()
  // 与 modelChips 一致：一个可用平台都没有的模型不进列表（否则搜索框里会出现空条目）。
  return (payload?.items || [])
    .filter((item) => (item.platforms || []).some((p) => p.enabled !== false))
    .map((item) => {
      const platforms = (item.platforms || []).filter((p) => p.enabled !== false)
      return {
        key: item.key || String(item.name),
        name: item.name,
        platformCount: platforms.length,
        rows: platforms.map((p) => ({
          platform: p.platform,
          platformLabel: p.platform_label || p.platform,
          callId: p.client_id || clientModelId(p.model_id, p.platform),
          upstreamId: p.model_id,
          multiplierText: p.multiplier == null ? FMT_EMPTY : Number(p.multiplier) === 0 ? 'FREE' : '×' + p.multiplier,
        })),
      }
    })
    .filter((item) => {
      if (!query) return true
      if (item.name.toLowerCase().includes(query)) return true
      return item.rows.some((row) => row.callId.toLowerCase().includes(query) || row.upstreamId.toLowerCase().includes(query))
    })
}

/** 连接地址候选：当前访问 + 本机 + 内网 IPv4（旧版 loadShareInfo 的 choices）。 */
export function originOptions(network: NetworkPayload | null, current: { origin: string; hostname: string; port: string }): Array<{ value: string; label: string }> {
  const choices = new Map<string, string>()
  if (current.origin) choices.set(current.origin, '当前访问 · ' + (current.origin.replace(/^https?:\/\//, '') || '当前域名'))
  const local = ['localhost', '127.0.0.1'].includes(current.hostname)
  const port = current.port || (local && network?.port ? String(network.port) : '')
  const suffix = port ? ':' + port : ''
  choices.set('http://127.0.0.1' + suffix, '本机 · 127.0.0.1' + suffix)
  for (const ip of network?.lan_ips || []) choices.set('http://' + ip + suffix, '局域网 · ' + ip + suffix)
  return [...choices].map(([value, label]) => ({ value, label }))
}

export interface CurlExamples {
  openai: string
  claude: string
  audioCurl: string
  audioPython: string
  imagesPython: string
  videoCurl: string
  videoPython: string
}

/**
 * 接入示例（旧版 renderShareAddress 的原文迁移）。
 *
 * 地址与模型自动填：OpenAI 系用 origin + /v1；Claude 系用 origin（SDK 自己拼 /v1/messages）。
 * 文本模型取当前第一个可用模型的调用名，没有则回落旧版的 auto。
 */
export function buildCurlExamples(origin: string, firstModel?: string): CurlExamples {
  const base = (origin || '').replace(/\/+$/, '') + '/v1'
  const root = (origin || '').replace(/\/+$/, '')
  const textModel = firstModel || 'auto'
  return {
    openai: [
      'curl ' + base + '/chat/completions \\',
      '  -H "Authorization: Bearer <你的 Key>" \\',
      '  -H "Content-Type: application/json" \\',
      '  -d \'{"model":"' + textModel + '","messages":[{"role":"user","content":"你好"}]}\'',
    ].join('\n'),
    claude: [
      'curl ' + root + '/v1/messages \\',
      '  -H "x-api-key: <你的 Key>" \\',
      '  -H "anthropic-version: 2023-06-01" \\',
      '  -H "Content-Type: application/json" \\',
      '  -d \'{"model":"claude-sonnet-4-5-20250929","max_tokens":1024,"messages":[{"role":"user","content":"你好"}]}\'',
    ].join('\n'),
    audioCurl: [
      'curl "' + base + '/audio/transcriptions" \\',
      '  -H "Authorization: Bearer <你的 Key>" \\',
      '  -F "file=@recording.webm" \\',
      '  -F "model=workbuddy-asr" \\',
      '  -F "response_format=json"',
    ].join('\n'),
    audioPython: [
      'from openai import OpenAI',
      'client = OpenAI(api_key="<你的 Key>", base_url="' + base + '")',
      'with open("recording.webm", "rb") as audio:',
      '    result = client.audio.transcriptions.create(model="workbuddy-asr", file=audio)',
      'print(result.text)',
    ].join('\n'),
    imagesPython: [
      'from openai import OpenAI',
      'client = OpenAI(api_key="<你的 Key>", base_url="' + base + '")',
      'result = client.images.generate(model="hunyuan-image-alpha", prompt="白色背景上的蓝色圆形", n=1)',
      'print(result.data[0].url)',
      'with open("input.png", "rb") as image:',
      '    edited = client.images.edit(model="hunyuan-image-alpha", image=image, prompt="把圆形改成绿色")',
      'print(edited.data[0].url)',
    ].join('\n'),
    videoCurl: [
      '# 1) 创建任务：立即返回 queued，不阻塞等待生成',
      'curl "' + base + '/videos" \\',
      '  -H "Authorization: Bearer <你的 Key>" \\',
      '  -H "Content-Type: application/json" \\',
      '  -d \'{"model":"kling-video-v3","prompt":"一只猫在弹钢琴","seconds":"4","size":"1280x720"}\'',
      '',
      '# 2) 轮询（用上一步返回的 id），直到 status=completed',
      'curl "' + base + '/videos/<视频 ID>" -H "Authorization: Bearer <你的 Key>"',
      '',
      '# 3) 下载 MP4',
      'curl "' + base + '/videos/<视频 ID>/content" -H "Authorization: Bearer <你的 Key>" -o output.mp4',
    ].join('\n'),
    videoPython: [
      'import time',
      'from openai import OpenAI',
      'client = OpenAI(api_key="<你的 Key>", base_url="' + base + '")',
      '',
      'video = client.videos.create(model="kling-video-v3", prompt="一只猫在弹钢琴", seconds="4", size="1280x720")',
      'print(video.id, video.status)          # queued',
      '',
      'while video.status not in ("completed", "failed"):',
      '    time.sleep(5)',
      '    video = client.videos.retrieve(video.id)',
      '',
      'if video.status == "completed":',
      '    content = client.videos.download_content(video.id)',
      '    content.write_to_file("output.mp4")',
    ].join('\n'),
  }
}

export interface KeyFormDraft {
  name: string
  note: string
  groupId: number
  unlimited: boolean
  creditLimit: string
  maxConcurrent: string
  ratePerMinute: string
  aggregate: boolean
  platform: string
  accountIds: number[]
  resetUsed: boolean
}

export interface KeyFormErrors {
  name?: string
  creditLimit?: string
  maxConcurrent?: string
  ratePerMinute?: string
}

function numberError(raw: string, label: string): string | undefined {
  const text = String(raw || '').trim()
  if (!text) return undefined
  const value = Number(text)
  if (!Number.isFinite(value)) return label + '必须是数字'
  if (value < 0) return label + '不能小于 0'
  if (!Number.isInteger(value)) return label + '必须是整数'
  return undefined
}

/** 表单校验：编辑时名称必填（旧版 submitEditKey 的行为）；数字项非负整数；额度非负数。 */
export function validateKeyForm(draft: KeyFormDraft, editing: boolean): KeyFormErrors {
  const errors: KeyFormErrors = {}
  if (editing && !String(draft.name || '').trim()) errors.name = '名称不能为空'
  if (!draft.unlimited) {
    const text = String(draft.creditLimit || '').trim()
    if (text) {
      const value = Number(text)
      if (!Number.isFinite(value)) errors.creditLimit = '额度必须是数字'
      else if (value < 0) errors.creditLimit = '额度不能小于 0'
    }
  }
  errors.maxConcurrent = numberError(draft.maxConcurrent, '并发上限')
  errors.ratePerMinute = numberError(draft.ratePerMinute, '每分钟请求数')
  return errors
}

export function hasFormErrors(errors: KeyFormErrors): boolean {
  return Object.values(errors).some(Boolean)
}

/** 表单 → 创建/编辑请求体（旧的 openAddKey/submitAddKey 口径）。 */
export function draftToInput(draft: KeyFormDraft): KeyCreateInput & KeyPatchInput {
  const numberOrNull = (raw: string): number | null => {
    const text = String(raw || '').trim()
    return text === '' ? null : Number(text)
  }
  const limitText = String(draft.creditLimit || '').trim()
  return {
    name: String(draft.name || '').trim(),
    note: String(draft.note || ''),
    group_id: Number(draft.groupId || 0),
    unlimited: draft.unlimited || limitText === '',
    credit_limit: limitText === '' ? null : Number(limitText),
    max_concurrent: numberOrNull(draft.maxConcurrent),
    rate_per_minute: numberOrNull(draft.ratePerMinute),
    aggregate: Boolean(draft.aggregate),
    platform: String(draft.platform || '').toLowerCase(),
    account_ids: draft.accountIds.slice(),
  }
}

export interface UsageView {
  limitText: string
  usedText: string
  remainingText: string
  percentText: string
  ratio: number
  tone: 'ok' | 'warn' | 'danger'
  unlimited: boolean
  createdText: string
}

/** 用量弹窗的展示口径（旧版 viewKeyUsage 的四格 + 进度条）。 */
export function toUsageView(payload: KeyUsagePayload): UsageView {
  const unlimited = Boolean(payload.unlimited)
  const percent = payload.usage_percent
  const ratio = unlimited || percent == null ? 0 : Math.min(1, Math.max(0, percent / 100))
  return {
    limitText: unlimited ? '∞（无限制）' : fmtCredit(payload.credit_limit),
    usedText: fmtCredit(payload.credit_used),
    remainingText: unlimited || payload.credit_remaining == null ? '∞' : fmtCredit(payload.credit_remaining),
    percentText: percent == null ? 'N/A' : percent.toFixed(1) + '%',
    ratio,
    tone: unlimited ? 'ok' : ratio >= 0.9 ? 'danger' : ratio >= 0.6 ? 'warn' : 'ok',
    unlimited,
    createdText: payload.created_at ? fmtTime(payload.created_at) : FMT_EMPTY,
  }
}

export interface IpOverviewView {
  totalIps: number
  requests: number
  activeIps: number
  activeRequests: number
  rows: Array<{
    ip: string
    requests: number
    failed: number
    credits: number
    tokens: number
    lastSeen: string
    models: string
    active: number
    blocked: boolean
  }>
}

/** 来源 IP 抽屉的展示口径（key-ip.js 的表格 + 汇总迁移）。 */
export function toIpOverview(payload: KeyIpsPayload): IpOverviewView {
  return {
    totalIps: Number(payload.total_ips || 0),
    requests: Number(payload.total_requests || 0),
    activeIps: Number(payload.active_ips || 0),
    activeRequests: Number(payload.active_requests || 0),
    rows: (payload.items || []).map((row) => ({
      ip: row.ip,
      requests: Number(row.requests || 0),
      failed: Number(row.failed || 0),
      credits: Number(row.credits || 0),
      tokens: Number(row.tokens || 0),
      lastSeen: row.last_seen ? fmtTime(row.last_seen) : FMT_EMPTY,
      models: (row.models || []).map((m) => m.model).filter(Boolean).join('、'),
      active: Number(row.active_requests || 0),
      blocked: Boolean(row.blocked),
    })),
  }
}

/**
 * 来源 IP 抽屉的空态（切换 Key / 取数前 / 取数失败时先用它清空）。
 *
 * 汇总四格与明细同源于 toIpOverview：切 Key 时若不重置，旧 Key 的「来源 IP / 调用次数」
 * 会一直挂到新 Key 的数据回来为止（新 Key 取数失败则永久串号）。
 */
export function emptyIpOverview(): IpOverviewView {
  return toIpOverview({
    id: 0,
    name: '',
    days: 1,
    sort: 'requests',
    direction: 'desc',
    offset: 0,
    limit: 0,
    total_ips: 0,
    total_requests: 0,
    active_ips: 0,
    active_requests: 0,
    active_scope: '',
    active: [],
    items: [],
  })
}
