<script setup lang="ts">
/**
 * S10 IP 管理 /ip（模板 B：PageHeader + 统计条 + 三页签 + FilterBar + DataTable + FormDrawer）
 *
 * 契约：docs/ui-v2/contracts/ip.md
 *
 * 三个视图共用一套骨架，差异只在列定义与取数方式：
 *   · 活跃 IP：服务端分页 + 服务端排序（limit/offset/sort/direction），
 *     查询参数由 api/ip.ts:activityQueryFromState 统一映射；「状态」后端无参数，只过滤当前页。
 *   · 封禁名单 / 设备关联：后端一次返回全部，筛选/排序/分页在客户端纯函数里做。
 *
 * 路由 query：view（页签）、page/size/sort/order + 各页签自己的筛选字段；
 * 三处 useTableQuery 的 key 不同，每页条数偏好互不影响。
 *
 * 旧版位置：admin/console/index.html #panel-ip（1845–1888）+ ip-management.js 全文。
 */
import { computed, h, onBeforeUnmount, onMounted, ref, watch, type VNodeChild } from 'vue'
import { useRoute, useRouter, type LocationQuery } from 'vue-router'
import { NDatePicker, NInput, NInputNumber, NPopover, NSelect, NSkeleton } from 'naive-ui'
import { Ban, RefreshCw } from 'lucide-vue-next'
import PageHeader from '@/layout/PageHeader.vue'
import {
  Badge,
  Btn,
  Card,
  Chip,
  Copyable,
  DataTable,
  Empty,
  ErrorBlock,
  FilterBar,
  FormDrawer,
  FormField,
  HelpTip,
  RowActions,
  Segmented,
  StatStrip,
  type FilterChip,
  type SegmentedOption,
  type SorterPayload,
  type StatItem,
  type TableColumn,
  type TableRow,
} from '@/components'
import { useConfirm, useTableQuery, useToast } from '@/composables'
import { FMT_EMPTY, fmtCompact, fmtCredit, fmtExpiry, fmtFullTime, fmtInt, fmtRelative, fmtTime, expiryTone } from '@/utils/format'
import { errorMessage, failureText } from '@/utils/error'
import { ApiError, isAbortError } from '@/api/http'
import {
  BLOCK_DURATION_OPTIONS,
  IP_DAYS_OPTIONS,
  IP_USAGE_OPTIONS,
  activityQueryFromState,
  blockIp,
  deleteBlockedIp,
  expiryFromDuration,
  filterActivityRows,
  filterBlockedRows,
  filterDeviceRows,
  getIpActivity,
  listBlockedIps,
  listIpDevices,
  paginateRows,
  parseBlockTarget,
  patchBlockedIp,
  sortBlockedRows,
  sortDeviceRows,
  toActivityRows,
  toBlockedRows,
  toDeviceRows,
  updateIpDevice,
  type BlockDuration,
  type BlockedIpRow,
  type IpActivityPayload,
  type IpActivityRow,
  type IpDeviceRow,
} from '@/api/ip'

/** 旧版 #panel-ip 的子标题 + 四条 ip-notes 原文（收进 ⓘ，不占主视线）。 */
const HELP_TEXT =
  '封禁后，该 IP 的全部 /v1 与 /gw 请求直接返回 403「该 IP 已被封禁」，在鉴权和转发上游之前拦截。\n' +
  '入口次数包含鉴权失败、限流和封禁请求；历史数据从用量日志补充。\n' +
  '按小时汇总，时间范围包含起始小时；失败数按 HTTP 状态统计，积分与 Token 来自用量日志。\n' +
  '点击表头切换排序；点击 IP 查看该地址的用量明细。\n' +
  '低消耗筛选按成功用量记录计算均值，未返回 Token 的请求不作为低消耗。'

/** 页签 2 的说明（设计稿 P13 原文）。 */
const BLOCKED_HELP = '封禁后该 IP 的全部 /v1 与 /gw 请求直接 403'
const BLOCKED_EXPIRY_HELP =
  '到期时间由 blocked_ips.expires_at 决定，为空表示永久；已过期的记录后端视为未封禁。'

/** 页签 3 的说明（旧版 panel-note 原文）。 */
const DEVICES_HELP = '按标识独立封禁'
const DEVICES_NOTE =
  '设备 ID、指纹和 MAC 均由客户端上报，未经硬件验证。相同标识换 IP 后仍会拦截；' +
  '未上报或重置标识的设备无法可靠关联。普通 UA 不作为封禁依据。最多展示最近 2000 条关联。'
const DEVICE_KEY_HELP =
  '/api/ip/devices 不返回关联 Key；按 Key 追查请在用量日志按客户端 IP 过滤。'

type IpView = 'active' | 'blocked' | 'devices'

const VIEW_OPTIONS: SegmentedOption<string>[] = [
  { label: '活跃 IP', value: 'active' },
  { label: '封禁名单', value: 'blocked' },
  { label: '设备关联', value: 'devices' },
]

const daysOptions = IP_DAYS_OPTIONS.map((option) => ({ label: option.label, value: option.value }))
const usageOptions = IP_USAGE_OPTIONS.map((option) => ({ label: option.label, value: option.value }))
const statusOptions = [
  { label: '状态：全部', value: '' },
  { label: '已封禁', value: 'blocked' },
  { label: '允许', value: 'allowed' },
]
const blockedStatusOptions = [
  { label: '状态：全部', value: '' },
  { label: '已封禁', value: 'enabled' },
  { label: '已解除', value: 'disabled' },
]
const deviceStatusOptions = [
  { label: '状态：全部', value: '' },
  { label: '已封禁', value: 'blocked' },
  { label: '允许', value: 'allowed' },
]
const durationOptions = BLOCK_DURATION_OPTIONS.map((option) => ({ label: option.label, value: option.value }))

const route = useRoute()
const router = useRouter()
const toast = useToast()
const confirm = useConfirm()

/* ------------------------------------------------------------------ *
 * 视图状态（页签 + 三张表的查询状态）
 * ------------------------------------------------------------------ */

function normalizeView(raw: unknown): IpView {
  const value = String(raw ?? '')
  return value === 'blocked' || value === 'devices' ? value : 'active'
}

const view = ref<IpView>(normalizeView(route.query.view))

watch(
  () => route.query.view,
  (raw) => {
    const next = normalizeView(raw)
    if (next !== view.value) view.value = next
  },
)

const activeTable = useTableQuery('ip', {
  pageSize: 20,
  sortBy: 'requests',
  sortOrder: 'desc',
  filters: { days: '1', q: '', status: '', usage: '', min: '0', max: '' },
})
const blockedTable = useTableQuery('ip-blocked', { pageSize: 20, filters: { bq: '', bs: '' } })
const deviceTable = useTableQuery('ip-devices', { pageSize: 20, filters: { dq: '', ds: '' } })

/* ------------------------------------------------------------------ *
 * 取数
 * ------------------------------------------------------------------ */

const activity = ref<IpActivityPayload | null>(null)
const activityLoading = ref(false)
const activityError = ref<{ message: string; detail: string } | null>(null)

const blocked = ref<BlockedIpRow[] | null>(null)
const blockedLoading = ref(false)
const blockedError = ref<{ message: string; detail: string } | null>(null)

const devices = ref<IpDeviceRow[] | null>(null)
const deviceLoading = ref(false)
const deviceError = ref<{ message: string; detail: string } | null>(null)

let activityController: AbortController | null = null
let blockedController: AbortController | null = null
let deviceController: AbortController | null = null

function prettyDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail
  try {
    return JSON.stringify(detail, null, 2)
  } catch {
    return String(detail)
  }
}

function toErrorState(error: unknown): { message: string; detail: string } {
  return {
    message: errorMessage(error),
    detail: error instanceof ApiError && error.detail ? prettyDetail(error.detail) : '',
  }
}

const activityQuery = computed(() => activityQueryFromState(activeTable.state))

async function loadActivity(): Promise<void> {
  activityController?.abort()
  const current = new AbortController()
  activityController = current
  activityLoading.value = true
  activityError.value = null
  try {
    const payload = await getIpActivity(activityQuery.value, current.signal)
    activity.value = payload
    // 后端 offset 越界时不返错，只是空页；按 total_ips 把页码纠正回来。
    const total = Number(payload?.total_ips) || 0
    const maxPage = Math.max(1, Math.ceil(total / activeTable.state.pageSize))
    if (activeTable.state.page > maxPage) activeTable.setPage(maxPage)
  } catch (error) {
    if (isAbortError(error)) return
    activityError.value = toErrorState(error)
    toast.error(failureText(activity.value === null ? '加载 IP 活动' : '刷新 IP 活动', error))
  } finally {
    if (activityController === current) {
      activityController = null
      activityLoading.value = false
    }
  }
}

async function loadBlocked(): Promise<void> {
  blockedController?.abort()
  const current = new AbortController()
  blockedController = current
  blockedLoading.value = true
  blockedError.value = null
  try {
    const payload = await listBlockedIps(current.signal)
    blocked.value = toBlockedRows(Array.isArray(payload?.items) ? payload.items : [])
  } catch (error) {
    if (isAbortError(error)) return
    blockedError.value = toErrorState(error)
    toast.error(failureText(blocked.value === null ? '加载封禁名单' : '刷新封禁名单', error))
  } finally {
    if (blockedController === current) {
      blockedController = null
      blockedLoading.value = false
    }
  }
}

async function loadDevices(): Promise<void> {
  deviceController?.abort()
  const current = new AbortController()
  deviceController = current
  deviceLoading.value = true
  deviceError.value = null
  try {
    const payload = await listIpDevices(current.signal)
    devices.value = toDeviceRows(Array.isArray(payload?.items) ? payload.items : [])
  } catch (error) {
    if (isAbortError(error)) return
    deviceError.value = toErrorState(error)
    toast.error(failureText(devices.value === null ? '加载设备关联' : '刷新设备关联', error))
  } finally {
    if (deviceController === current) {
      deviceController = null
      deviceLoading.value = false
    }
  }
}

/** 刷新：当前页签 + 统计依赖的数据（活跃页签要同时刷封禁/设备，统计条才对得上）。 */
async function refreshCurrent(): Promise<void> {
  if (view.value === 'blocked') {
    await Promise.all([loadBlocked(), loadActivity()])
    return
  }
  if (view.value === 'devices') {
    await Promise.all([loadDevices(), loadActivity()])
    return
  }
  await Promise.all([loadActivity(), loadBlocked(), loadDevices()])
}

const refreshing = computed(() => {
  if (view.value === 'blocked') return blockedLoading.value
  if (view.value === 'devices') return deviceLoading.value
  return activityLoading.value
})

/* 筛选/排序/分页变化 → 重新取数（AbortController 只保留最后一次）。 */
watch(
  () => JSON.stringify(activityQuery.value),
  () => {
    void loadActivity()
  },
)

/* ------------------------------------------------------------------ *
 * 统计条
 * ------------------------------------------------------------------ */

const statItems = computed<StatItem[]>(() => {
  const payload = activity.value
  const blockedCount = payload
    ? Number(payload.blocked_count) || 0
    : blocked.value
      ? blocked.value.filter((row) => row.enabled).length
      : null
  return [
    {
      label: '活跃 IP',
      value: payload ? fmtInt(payload.total_ips) : FMT_EMPTY,
      foot: '所选时间内有入口请求的 IP 数',
    },
    {
      label: '入口请求',
      value: payload ? fmtCompact(payload.total_requests) : FMT_EMPTY,
      foot: '含鉴权失败、限流与封禁拦截',
    },
    {
      label: '封禁 IP',
      value: blockedCount === null ? FMT_EMPTY : fmtInt(blockedCount),
      tone: blockedCount ? 'danger' : 'default',
      foot: '启用中的封禁记录',
    },
    {
      label: '设备标识',
      value: devices.value ? fmtInt(devices.value.length) : FMT_EMPTY,
      foot: '客户端上报 · 未验证',
    },
  ]
})

/* ------------------------------------------------------------------ *
 * 页签 1：活跃 IP
 * ------------------------------------------------------------------ */

const activityRows = computed(() => toActivityRows(activity.value?.items, activity.value?.current_ip))

const visibleActivityRows = computed(() =>
  filterActivityRows(activityRows.value, { status: activeTable.state.filters.status }),
)

const activityFirstLoading = computed(() => activityLoading.value && activity.value === null)
const activityHasFilters = computed(() => {
  const filters = activeTable.state.filters
  return Boolean(
    filters.q ||
      filters.status ||
      filters.usage ||
      (filters.days && filters.days !== '1') ||
      (filters.min && filters.min !== '0'),
  )
})

const activitySummary = computed(() => {
  const payload = activity.value
  if (!payload) return ''
  const total = fmtInt(payload.total_ips)
  if (activeTable.state.filters.status) {
    return '本页 ' + visibleActivityRows.value.length + ' / 共 ' + total + ' 个 IP（状态筛选仅作用于当前页）'
  }
  return '共 ' + total + ' 个 IP · ' + fmtInt(payload.total_requests) + ' 次请求'
})

const activityEmptyText = computed(() =>
  activityHasFilters.value ? '所选时间内没有符合筛选的 IP。' : '所选时间内暂无请求。',
)

const activityChips = computed<FilterChip[]>(() => {
  const filters = activeTable.state.filters
  const list: FilterChip[] = []
  const daysLabel = daysOptions.find((option) => option.value === filters.days)?.label
  if (daysLabel && filters.days !== '1') list.push({ key: 'days', label: '时间范围', value: daysLabel })
  if (filters.q) list.push({ key: 'q', label: 'IP', value: filters.q })
  if (filters.status) {
    const label = statusOptions.find((option) => option.value === filters.status)?.label
    list.push({ key: 'status', label: '状态', value: label || filters.status })
  }
  if (filters.usage) {
    const label = usageOptions.find((option) => option.value === filters.usage)?.label
    list.push({ key: 'usage', label: '流量', value: label || filters.usage })
  }
  if (filters.min && filters.min !== '0') list.push({ key: 'min', label: '至少请求', value: filters.min })
  return list
})

const SORT_KEY_MAP: Record<string, string> = {
  ip: 'ip',
  requests: 'requests',
  failed: 'failed',
  blocked_requests: 'blocked_requests',
  credits: 'credits',
  tokens: 'tokens',
  last_seen: 'last_seen',
}

function sortOrderOf(stateKey: string): 'ascend' | 'descend' | false {
  if (activeTable.state.sortBy !== stateKey || !activeTable.state.sortOrder) return false
  return activeTable.state.sortOrder === 'asc' ? 'ascend' : 'descend'
}

function onActivitySorter(sorter: SorterPayload | null): void {
  if (!sorter || !sorter.order) {
    activeTable.setSort('requests', 'desc')
    return
  }
  const stateKey = SORT_KEY_MAP[sorter.columnKey]
  if (!stateKey) {
    activeTable.setSort('requests', 'desc')
    return
  }
  activeTable.setSort(stateKey, sorter.order === 'ascend' ? 'asc' : 'desc')
}

function renderIp(row: TableRow): VNodeChild {
  const item = row as IpActivityRow
  const trigger = h(
    NPopover,
    { trigger: 'hover', placement: 'right-start', showArrow: false },
    {
      trigger: () =>
        h('span', { class: 'ip-mono', title: item.ip }, item.ip || FMT_EMPTY),
      default: () =>
        h('div', { class: 'ip-pop' }, [
          h('p', { class: 'ip-pop__title' }, '客户端识别'),
          h('pre', { class: 'ip-pop__text' }, item.client.tooltip),
          h(Copyable, { value: item.ip, muted: true }),
        ]),
    },
  )
  const nodes: VNodeChild[] = [trigger, h(HelpTip, { content: item.client.tooltip, label: '客户端识别' })]
  if (item.isCurrent) nodes.push(h('small', { class: 'ip-current' }, '当前访问 IP'))
  return h('div', { class: 'ip-cell' }, nodes)
}

function renderNumber(value: string, sub?: string, tone?: 'danger' | 'warn'): VNodeChild {
  return h('div', { class: 'ip-numcell' }, [
    h('span', { class: 'ip-num' + (tone ? ' is-' + tone : '') }, value),
    sub ? h('small', { class: 'ip-sub' }, sub) : null,
  ])
}

function renderRequests(row: TableRow): VNodeChild {
  return renderNumber(fmtInt((row as IpActivityRow).requests))
}

function renderFailed(row: TableRow): VNodeChild {
  const item = row as IpActivityRow
  return renderNumber(fmtInt(item.failed), undefined, item.failed > 0 ? 'danger' : undefined)
}

function renderBlockedRequests(row: TableRow): VNodeChild {
  const item = row as IpActivityRow
  return renderNumber(fmtInt(item.blockedRequests), undefined, item.blockedRequests > 0 ? 'warn' : undefined)
}

function renderCredits(row: TableRow): VNodeChild {
  const item = row as IpActivityRow
  const avg = item.avgCredits === null ? FMT_EMPTY : item.avgCredits.toFixed(4)
  return renderNumber(fmtCredit(item.credits), '均 ' + avg)
}

function renderTokens(row: TableRow): VNodeChild {
  const item = row as IpActivityRow
  const avg = item.avgTokens === null ? FMT_EMPTY : fmtCompact(item.avgTokens)
  return renderNumber(fmtCompact(item.tokens), '均 ' + avg + ' · ' + fmtInt(item.knownUsage) + ' 次有用量')
}

function renderLastSeen(row: TableRow): VNodeChild {
  const item = row as IpActivityRow
  if (!item.lastSeen) return h('span', { class: 'ip-dash' }, FMT_EMPTY)
  return h('span', { title: fmtFullTime(item.lastSeen) }, fmtTime(item.lastSeen))
}

function renderStatus(row: TableRow): VNodeChild {
  const item = row as IpActivityRow
  return h(
    Badge,
    { tone: item.blocked ? 'danger' : 'ok', dot: true },
    { default: () => (item.blocked ? '已封禁' : '允许') },
  )
}

function renderActivityActions(row: TableRow): VNodeChild {
  const item = row as IpActivityRow
  return h(RowActions, {
    actions: [
      { key: 'toggle', label: item.blocked ? '解除封禁' : '封禁' },
      { key: 'logs', label: '查看日志' },
    ],
    max: 1,
    onSelect: (key: string) => {
      void onActivityAction(item, key)
    },
  })
}

const activityColumns = computed<TableColumn[]>(() => [
  {
    key: 'ip',
    title: 'IP 地址',
    mobile: 'title',
    width: 200,
    sortable: true,
    sortOrder: sortOrderOf('ip'),
    render: renderIp,
  },
  {
    key: 'requests',
    title: '请求次数',
    mobile: 'meta',
    width: 110,
    numeric: true,
    sortable: true,
    sortOrder: sortOrderOf('requests'),
    render: renderRequests,
  },
  {
    key: 'failed',
    title: '失败次数',
    mobile: 'meta',
    width: 104,
    numeric: true,
    sortable: true,
    sortOrder: sortOrderOf('failed'),
    render: renderFailed,
  },
  {
    key: 'blocked_requests',
    title: '封禁拦截',
    mobile: 'meta',
    width: 104,
    numeric: true,
    sortable: true,
    sortOrder: sortOrderOf('blocked_requests'),
    render: renderBlockedRequests,
  },
  {
    key: 'credits',
    title: '积分',
    mobile: 'meta',
    width: 132,
    numeric: true,
    sortable: true,
    sortOrder: sortOrderOf('credits'),
    render: renderCredits,
  },
  {
    key: 'tokens',
    title: 'Token',
    mobile: 'meta',
    width: 172,
    numeric: true,
    sortable: true,
    sortOrder: sortOrderOf('tokens'),
    render: renderTokens,
  },
  {
    key: 'last_seen',
    title: '最近请求',
    mobile: 'meta',
    width: 132,
    numeric: true,
    sortable: true,
    sortOrder: sortOrderOf('last_seen'),
    render: renderLastSeen,
  },
  {
    key: 'status',
    title: '状态',
    mobile: 'subtitle',
    width: 96,
    render: renderStatus,
  },
  {
    key: 'actions',
    title: '操作',
    mobile: 'actions',
    width: 132,
    hideable: false,
    fixed: 'right',
    render: renderActivityActions,
  },
])

/* ------------------------------------------------------------------ *
 * 页签 2：封禁名单
 * ------------------------------------------------------------------ */

const blockedFiltered = computed(() =>
  filterBlockedRows(blocked.value || [], {
    q: blockedTable.state.filters.bq,
    status: blockedTable.state.filters.bs,
  }),
)

const blockedSorted = computed(() =>
  sortBlockedRows(blockedFiltered.value, blockedTable.state.sortBy, blockedTable.state.sortOrder),
)

const blockedPage = computed(() =>
  paginateRows(blockedSorted.value, blockedTable.state.page, blockedTable.state.pageSize),
)

const blockedFirstLoading = computed(() => blockedLoading.value && blocked.value === null)
const blockedCount = computed(() => (blocked.value || []).filter((row) => row.enabled).length)

const blockedSummary = computed(() => {
  const total = blocked.value?.length ?? 0
  const enabled = blockedCount.value
  if (blockedTable.state.filters.bq || blockedTable.state.filters.bs) {
    return '筛选出 ' + blockedFiltered.value.length + ' / 共 ' + total + ' 条 · 启用中 ' + enabled + ' 条'
  }
  return '共 ' + total + ' 条封禁记录 · 启用中 ' + enabled + ' 条'
})

const blockedChips = computed<FilterChip[]>(() => {
  const filters = blockedTable.state.filters
  const list: FilterChip[] = []
  if (filters.bq) list.push({ key: 'bq', label: '搜索', value: filters.bq })
  if (filters.bs) {
    const label = blockedStatusOptions.find((option) => option.value === filters.bs)?.label
    list.push({ key: 'bs', label: '状态', value: label || filters.bs })
  }
  return list
})

function blockedSortOrder(key: string): 'ascend' | 'descend' | false {
  if (blockedTable.state.sortBy !== key || !blockedTable.state.sortOrder) return false
  return blockedTable.state.sortOrder === 'asc' ? 'ascend' : 'descend'
}

const BLOCKED_SORT_MAP: Record<string, string> = { ip: 'ip', enabled: 'enabled', created_at: 'createdAt' }

function onBlockedSorter(sorter: SorterPayload | null): void {
  if (!sorter || !sorter.order) {
    blockedTable.setSort('', '')
    return
  }
  const stateKey = BLOCKED_SORT_MAP[sorter.columnKey]
  if (!stateKey) {
    blockedTable.setSort('', '')
    return
  }
  blockedTable.setSort(stateKey, sorter.order === 'ascend' ? 'asc' : 'desc')
}

function renderBlockedIp(row: TableRow): VNodeChild {
  const item = row as BlockedIpRow
  return h('code', { class: 'ip-mono' }, item.ip || FMT_EMPTY)
}

function renderBlockedNote(row: TableRow): VNodeChild {
  const item = row as BlockedIpRow
  if (!item.note) return h('span', { class: 'ip-dash' }, FMT_EMPTY)
  return h('span', { class: 'ip-note', title: item.note }, item.note)
}

function renderBlockedCreated(row: TableRow): VNodeChild {
  const item = row as BlockedIpRow
  if (!item.createdAt) return h('span', { class: 'ip-dash' }, FMT_EMPTY)
  return h('span', { title: fmtFullTime(item.createdAt) }, fmtTime(item.createdAt))
}

function renderBlockedExpiry(row: TableRow): VNodeChild {
  const item = row as BlockedIpRow
  if (item.expiry === 'permanent') return h('span', { class: 'ip-permanent' }, '永久')
  const tone = expiryTone(item.expiresAt)
  return h(
    'span',
    { class: 'ip-expiry is-' + tone, title: fmtFullTime(item.expiresAt) },
    fmtExpiry(item.expiresAt),
  )
}

function renderBlockedStatus(row: TableRow): VNodeChild {
  const item = row as BlockedIpRow
  return h(
    Badge,
    { tone: item.enabled ? 'danger' : 'neutral', dot: true },
    { default: () => (item.enabled ? '已封禁' : '已解除') },
  )
}

function renderBlockedActions(row: TableRow): VNodeChild {
  const item = row as BlockedIpRow
  return h(RowActions, {
    actions: [
      { key: 'toggle', label: item.enabled ? '解除封禁' : '重新封禁' },
      { key: 'delete', label: '删除记录', danger: true },
    ],
    max: 1,
    onSelect: (key: string) => {
      void onBlockedAction(item, key)
    },
  })
}

const blockedColumns = computed<TableColumn[]>(() => [
  { key: 'ip', title: 'IP / 网段', mobile: 'title', width: 200, sortable: true, sortOrder: blockedSortOrder('ip'), render: renderBlockedIp },
  { key: 'note', title: '原因', mobile: 'subtitle', minWidth: 180, render: renderBlockedNote },
  { key: 'created_at', title: '封禁时间', mobile: 'meta', width: 140, sortable: true, sortOrder: blockedSortOrder('created_at'), render: renderBlockedCreated },
  { key: 'expires', title: '到期', mobile: 'meta', width: 140, render: renderBlockedExpiry },
  { key: 'enabled', title: '状态', mobile: 'meta', width: 104, sortable: true, sortOrder: blockedSortOrder('enabled'), render: renderBlockedStatus },
  { key: 'actions', title: '操作', mobile: 'actions', width: 132, hideable: false, fixed: 'right', render: renderBlockedActions },
])

/* ------------------------------------------------------------------ *
 * 页签 3：设备关联
 * ------------------------------------------------------------------ */

const deviceFiltered = computed(() =>
  filterDeviceRows(devices.value || [], {
    q: deviceTable.state.filters.dq,
    status: deviceTable.state.filters.ds,
  }),
)

const deviceSorted = computed(() =>
  sortDeviceRows(deviceFiltered.value, deviceTable.state.sortBy, deviceTable.state.sortOrder),
)

const devicePage = computed(() =>
  paginateRows(deviceSorted.value, deviceTable.state.page, deviceTable.state.pageSize),
)

const deviceFirstLoading = computed(() => deviceLoading.value && devices.value === null)
const deviceBlockedCount = computed(() => (devices.value || []).filter((row) => row.blocked).length)

const deviceSummary = computed(() => {
  const total = devices.value?.length ?? 0
  if (deviceTable.state.filters.dq || deviceTable.state.filters.ds) {
    return '筛选出 ' + deviceFiltered.value.length + ' / 共 ' + total + ' 个 · 已封禁 ' + deviceBlockedCount.value + ' 个'
  }
  return '共 ' + total + ' 个设备标识 · 已封禁 ' + deviceBlockedCount.value + ' 个'
})

const deviceChips = computed<FilterChip[]>(() => {
  const filters = deviceTable.state.filters
  const list: FilterChip[] = []
  if (filters.dq) list.push({ key: 'dq', label: '搜索', value: filters.dq })
  if (filters.ds) {
    const label = deviceStatusOptions.find((option) => option.value === filters.ds)?.label
    list.push({ key: 'ds', label: '状态', value: label || filters.ds })
  }
  return list
})

function deviceSortOrder(key: string): 'ascend' | 'descend' | false {
  if (deviceTable.state.sortBy !== key || !deviceTable.state.sortOrder) return false
  return deviceTable.state.sortOrder === 'asc' ? 'ascend' : 'descend'
}

const DEVICE_SORT_MAP: Record<string, string> = {
  kind_label: 'kindLabel',
  last_seen: 'lastSeen',
  blocked: 'blocked',
}

function onDeviceSorter(sorter: SorterPayload | null): void {
  if (!sorter || !sorter.order) {
    deviceTable.setSort('', '')
    return
  }
  const stateKey = DEVICE_SORT_MAP[sorter.columnKey]
  if (!stateKey) {
    deviceTable.setSort('', '')
    return
  }
  deviceTable.setSort(stateKey, sorter.order === 'ascend' ? 'asc' : 'desc')
}

function renderDeviceKind(row: TableRow): VNodeChild {
  const item = row as IpDeviceRow
  return h('div', { class: 'ip-device' }, [
    h('span', { class: 'ip-device__kind' }, item.kindLabel || FMT_EMPTY),
    h('code', { class: 'ip-mono', title: item.value }, item.value || FMT_EMPTY),
  ])
}

function renderDeviceIps(row: TableRow): VNodeChild {
  const item = row as IpDeviceRow
  if (!item.ips.length) return h('span', { class: 'ip-dash' }, FMT_EMPTY)
  const shown = item.ips.slice(0, 3)
  const nodes: VNodeChild[] = shown.map((ip) =>
    h(Chip, { key: ip, title: ip }, { default: () => ip }),
  )
  if (item.ips.length > shown.length) {
    nodes.push(
      h(Chip, { key: 'more', title: item.ips.join('、') }, { default: () => '+' + (item.ips.length - shown.length) }),
    )
  }
  return h('div', { class: 'ip-chips' }, nodes)
}

function renderDeviceKeys(): VNodeChild {
  return h('span', { class: 'ip-dash-row' }, [
    h('span', { class: 'ip-dash' }, FMT_EMPTY),
    h(HelpTip, { content: DEVICE_KEY_HELP, label: '关联 Key 说明' }),
  ])
}

function renderDeviceSeen(row: TableRow): VNodeChild {
  const item = row as IpDeviceRow
  if (!item.lastSeen) return h('span', { class: 'ip-dash' }, FMT_EMPTY)
  return h('span', { title: fmtFullTime(item.lastSeen) }, fmtRelative(item.lastSeen))
}

function renderDeviceStatus(row: TableRow): VNodeChild {
  const item = row as IpDeviceRow
  return h('div', { class: 'ip-chips' }, [
    h(
      Badge,
      { key: 'state', tone: item.blocked ? 'danger' : 'ok', dot: true },
      { default: () => (item.blocked ? '已封禁' : '允许') },
    ),
    h(Badge, { key: 'source', tone: 'neutral' }, { default: () => '客户端上报' }),
  ])
}

function renderDeviceActions(row: TableRow): VNodeChild {
  const item = row as IpDeviceRow
  return h(RowActions, {
    actions: [{ key: 'toggle', label: item.blocked ? '解除设备封禁' : '封禁设备标识' }],
    max: 1,
    onSelect: (key: string) => {
      if (key === 'toggle') void toggleDevice(item)
    },
  })
}

const deviceColumns = computed<TableColumn[]>(() => [
  { key: 'kind_label', title: '设备标识', mobile: 'title', minWidth: 220, sortable: true, sortOrder: deviceSortOrder('kind_label'), render: renderDeviceKind },
  { key: 'ips', title: '关联 IP', mobile: 'meta', minWidth: 200, render: renderDeviceIps },
  { key: 'keys', title: '关联 Key', mobile: 'meta', width: 120, render: renderDeviceKeys },
  { key: 'last_seen', title: '最近活动', mobile: 'meta', width: 140, sortable: true, sortOrder: deviceSortOrder('last_seen'), render: renderDeviceSeen },
  { key: 'blocked', title: '状态', mobile: 'subtitle', width: 176, sortable: true, sortOrder: deviceSortOrder('blocked'), render: renderDeviceStatus },
  { key: 'actions', title: '操作', mobile: 'actions', width: 140, hideable: false, fixed: 'right', render: renderDeviceActions },
])

/* ------------------------------------------------------------------ *
 * 筛选 / 重置
 * ------------------------------------------------------------------ */

function onActivityFilterChange(values: Record<string, unknown>): void {
  const next: Record<string, string> = {}
  for (const [name, value] of Object.entries(values)) next[name] = String(value ?? '')
  activeTable.setFilters(next)
}

function onBlockedFilterChange(values: Record<string, unknown>): void {
  const next: Record<string, string> = {}
  for (const [name, value] of Object.entries(values)) next[name] = String(value ?? '')
  blockedTable.setFilters(next)
}

function onDeviceFilterChange(values: Record<string, unknown>): void {
  const next: Record<string, string> = {}
  for (const [name, value] of Object.entries(values)) next[name] = String(value ?? '')
  deviceTable.setFilters(next)
}

function resetActivityFilters(): void {
  activeTable.setFilters({ days: '1', q: '', status: '', usage: '', min: '0', max: '' })
  activeTable.setSort('requests', 'desc')
  activeTable.setPage(1)
}

function resetBlockedFilters(): void {
  blockedTable.setFilters({ bq: '', bs: '' })
  blockedTable.setPage(1)
}

function resetDeviceFilters(): void {
  deviceTable.setFilters({ dq: '', ds: '' })
  deviceTable.setPage(1)
}

/** 状态是客户端过滤，改完要回到第 1 页（否则当前页可能整页为空）。 */
function setActivityStatus(value: string | number | null): void {
  activeTable.setFilter('status', value === null || value === undefined ? '' : String(value))
}

/** 流量筛选切换时给一组与旧版一致的默认上限（旧版：0.01 积分 / 100 Token）。 */
function setActivityUsage(value: string | number | null): void {
  const next = value === null || value === undefined ? '' : String(value)
  activeTable.setFilters({
    usage: next,
    max: next === 'credits' ? '0.01' : next === 'tokens' ? '100' : '',
    min: next ? '10' : '0',
  })
}

/* ------------------------------------------------------------------ *
 * 行操作
 * ------------------------------------------------------------------ */

function viewLogs(ip: string): void {
  void router.push({ path: '/logs', query: { client_ip: ip } })
}

async function onActivityAction(row: IpActivityRow, key: string): Promise<void> {
  if (key === 'logs') {
    viewLogs(row.ip)
    return
  }
  if (row.blocked) await unblockActivity(row)
  else openBlock(row.ip)
}

async function unblockActivity(row: IpActivityRow): Promise<void> {
  if (row.blockedId === null) {
    toast.warning('该 IP 没有可解除的封禁记录，请刷新列表')
    return
  }
  try {
    await patchBlockedIp(row.blockedId, { enabled: false })
    toast.success('已解除封禁')
    await Promise.all([loadActivity(), loadBlocked()])
  } catch (error) {
    toast.error(failureText('解除封禁', error))
  }
}

async function onBlockedAction(row: BlockedIpRow, key: string): Promise<void> {
  if (key === 'delete') {
    await removeBlocked(row)
    return
  }
  await toggleBlocked(row)
}

async function toggleBlocked(row: BlockedIpRow): Promise<void> {
  if (!row.enabled) {
    const ok = await confirm({
      title: '重新封禁 ' + row.ip + '？',
      message: '该 IP 的全部 /v1 请求将立即返回 403。',
      okText: '重新封禁',
      danger: true,
    })
    if (!ok) return
  }
  try {
    await patchBlockedIp(row.id, { enabled: !row.enabled })
    toast.success(row.enabled ? '已解除封禁' : '已重新封禁')
    await Promise.all([loadBlocked(), loadActivity()])
  } catch (error) {
    toast.error(failureText(row.enabled ? '解除封禁' : '重新封禁', error))
  }
}

async function removeBlocked(row: BlockedIpRow): Promise<void> {
  const ok = await confirm({
    title: '删除 ' + row.ip + ' 的封禁记录？',
    message: '删除后该 IP 将允许访问网关。',
    okText: '删除',
    danger: true,
  })
  if (!ok) return
  try {
    await deleteBlockedIp(row.id)
    toast.success('已删除封禁记录')
    await Promise.all([loadBlocked(), loadActivity()])
  } catch (error) {
    toast.error(failureText('删除封禁记录', error))
  }
}

async function toggleDevice(row: IpDeviceRow): Promise<void> {
  if (!row.blocked) {
    const ok = await confirm({
      title: '封禁设备标识？',
      message:
        '确认封禁 ' + row.value + '？携带这个上报标识的请求换 IP 后仍返回 403。该标识未经硬件验证。',
      okText: '封禁设备标识',
      danger: true,
    })
    if (!ok) return
  }
  try {
    await updateIpDevice({
      kind: row.kind,
      value: row.value,
      blocked: !row.blocked,
      note: row.note,
    })
    toast.success(row.blocked ? '已解除设备封禁' : '已封禁设备标识')
    await loadDevices()
  } catch (error) {
    toast.error(failureText(row.blocked ? '解除设备封禁' : '封禁设备标识', error))
  }
}

/* ------------------------------------------------------------------ *
 * 封禁抽屉
 * ------------------------------------------------------------------ */

const drawerOpen = ref(false)
const formIp = ref('')
const formNote = ref('')
const formDuration = ref<BlockDuration>('permanent')
const formCustomMs = ref<number | null>(null)
const formErrorIp = ref('')
const formErrorDuration = ref('')
const submitting = ref(false)

const drawerTitle = computed(() => '封禁 IP')

function openBlock(ip?: string): void {
  formIp.value = ip || ''
  formNote.value = ''
  formDuration.value = 'permanent'
  formCustomMs.value = null
  formErrorIp.value = ''
  formErrorDuration.value = ''
  drawerOpen.value = true
}

function onDurationChange(value: string | number | null): void {
  const next = String(value ?? 'permanent') as BlockDuration
  formDuration.value = next
  formErrorDuration.value = ''
  if (next !== 'custom') formCustomMs.value = null
}

async function submitBlock(): Promise<void> {
  if (submitting.value) return
  formErrorIp.value = ''
  formErrorDuration.value = ''

  const target = parseBlockTarget(formIp.value)
  if (target.kind !== 'ip') {
    formErrorIp.value = target.message
    return
  }
  const plan = expiryFromDuration(formDuration.value, Date.now(), formCustomMs.value)
  if (plan.error) {
    formErrorDuration.value = plan.error
    return
  }

  const currentIp = activity.value?.current_ip || ''
  const selfHint =
    currentIp && target.value === currentIp
      ? '这是当前访问 IP，封禁后也会影响你从此 IP 发起的 API 请求。'
      : ''
  const ok = await confirm({
    title: '封禁 ' + target.value + '？',
    message: '该 IP 的全部 /v1 与 /gw 请求将立即返回 403。' + selfHint,
    okText: '封禁 IP',
    danger: true,
  })
  if (!ok) return

  submitting.value = true
  try {
    await blockIp({ ip: target.value, note: formNote.value.trim(), expires_at: plan.expiresAt })
    toast.success('已封禁 IP ' + target.value)
    drawerOpen.value = false
    await Promise.all([loadActivity(), loadBlocked()])
  } catch (error) {
    toast.error(failureText('封禁 IP', error))
  } finally {
    submitting.value = false
  }
}

/* ------------------------------------------------------------------ *
 * 页签切换 / 生命周期
 * ------------------------------------------------------------------ */

function setView(raw: string | number): void {
  const next = normalizeView(raw)
  if (next === view.value) return
  view.value = next
  const query: LocationQuery = { ...route.query, view: next }
  delete query.page
  void router.replace({ query })
  if (next === 'active' && activity.value === null && !activityLoading.value) void loadActivity()
  if (next === 'blocked' && blocked.value === null && !blockedLoading.value) void loadBlocked()
  if (next === 'devices' && devices.value === null && !deviceLoading.value) void loadDevices()
}

onMounted(() => {
  void loadActivity()
  void loadBlocked()
  void loadDevices()
})

onBeforeUnmount(() => {
  activityController?.abort()
  blockedController?.abort()
  deviceController?.abort()
  activityController = null
  blockedController = null
  deviceController = null
})
</script>

<template>
  <div class="ip-page">
    <PageHeader title="IP 管理" :help="HELP_TEXT">
      <template #actions>
        <Btn variant="quiet" :icon="RefreshCw" :loading="refreshing" @click="refreshCurrent()">
          刷新
        </Btn>
        <Btn variant="primary" :icon="Ban" @click="openBlock()">封禁 IP</Btn>
      </template>
    </PageHeader>

    <StatStrip :items="statItems" />

    <div class="ip-tabs">
      <Segmented
        :options="VIEW_OPTIONS"
        :model-value="view"
        aria-label="IP 视图"
        @update:model-value="setView"
      />
    </div>

    <!-- 活跃 IP -->
    <Card v-if="view === 'active'" flush class="ip-card">
      <div class="ip-filter">
        <FilterBar
          :chips="activityChips"
          :summary="activitySummary"
          :loading="activityLoading"
          @change="onActivityFilterChange"
          @reset="resetActivityFilters"
          @remove="(key: string) => activeTable.setFilter(key, '')"
        >
          <template #default="{ change }">
            <n-select
              class="ip-filter__select"
              :value="activeTable.state.filters.days || '1'"
              :options="daysOptions"
              @update:value="(value: string | number | null) => activeTable.setFilter('days', value === null ? '1' : String(value))"
            />
            <n-input
              class="ip-filter__search"
              :value="activeTable.state.filters.q"
              placeholder="搜索 IP"
              clearable
              @update:value="(value: string) => change('q', value)"
            />
            <n-select
              class="ip-filter__select"
              :value="activeTable.state.filters.status"
              :options="statusOptions"
              @update:value="setActivityStatus"
            />
            <n-select
              class="ip-filter__select"
              :value="activeTable.state.filters.usage"
              :options="usageOptions"
              @update:value="setActivityUsage"
            />
            <n-input-number
              class="ip-filter__number"
              :value="Number(activeTable.state.filters.min || 0)"
              :min="0"
              :max="1000000"
              :show-button="false"
              placeholder="至少请求"
              @update:value="(value: number | null) => change('min', value === null ? '0' : String(value))"
            />
            <n-input-number
              v-if="activeTable.state.filters.usage"
              class="ip-filter__number"
              :value="Number(activeTable.state.filters.max || 0)"
              :min="0"
              :show-button="false"
              :placeholder="activeTable.state.filters.usage === 'credits' ? '平均积分 ≤' : '平均 Token ≤'"
              @update:value="(value: number | null) => change('max', value === null ? '0' : String(value))"
            />
          </template>
        </FilterBar>
      </div>

      <div class="ip-body">
        <ErrorBlock
          v-if="activityError && activity === null"
          :title="'加载 IP 活动失败：' + activityError.message"
          :detail="activityError.detail"
          @retry="loadActivity()"
        />
        <div v-else-if="activityFirstLoading" class="ip-skeleton" aria-busy="true" aria-label="IP 活动加载中">
          <n-skeleton v-for="index in 6" :key="index" height="44px" />
        </div>
        <DataTable
          v-else
          table-id="ip-active"
          :columns="activityColumns"
          :data="visibleActivityRows"
          row-key="key"
          remote
          :loading="activityLoading"
          :page="activeTable.state.page"
          :page-size="activeTable.state.pageSize"
          :item-count="Number(activity?.total_ips || 0)"
          :page-sizes="[20, 50, 100]"
          :empty-description="activityEmptyText"
          @update:page="activeTable.setPage"
          @update:page-size="activeTable.setPageSize"
          @update:sorter="onActivitySorter"
        >
          <template #empty>
            <Empty :description="activityEmptyText">
              <Btn v-if="activityHasFilters" variant="default" size="sm" @click="resetActivityFilters">
                清除筛选
              </Btn>
            </Empty>
          </template>
        </DataTable>
      </div>
    </Card>

    <!-- 封禁名单 -->
    <Card v-else-if="view === 'blocked'" flush class="ip-card">
      <div class="ip-section">
        <h3 class="ip-section__title">封禁名单</h3>
        <p class="ip-section__desc">{{ BLOCKED_HELP }}</p>
        <HelpTip :content="BLOCKED_EXPIRY_HELP" label="到期说明" />
      </div>
      <div class="ip-filter">
        <FilterBar
          :chips="blockedChips"
          :summary="blockedSummary"
          :loading="blockedLoading"
          @change="onBlockedFilterChange"
          @reset="resetBlockedFilters"
          @remove="(key: string) => blockedTable.setFilter(key, '')"
        >
          <template #default="{ change }">
            <n-input
              class="ip-filter__search"
              :value="blockedTable.state.filters.bq"
              placeholder="搜索 IP / 原因"
              clearable
              @update:value="(value: string) => change('bq', value)"
            />
            <n-select
              class="ip-filter__select"
              :value="blockedTable.state.filters.bs"
              :options="blockedStatusOptions"
              @update:value="(value: string | number | null) => blockedTable.setFilter('bs', value === null ? '' : String(value))"
            />
          </template>
        </FilterBar>
      </div>
      <div class="ip-body">
        <ErrorBlock
          v-if="blockedError && blocked === null"
          :title="'加载封禁名单失败：' + blockedError.message"
          :detail="blockedError.detail"
          @retry="loadBlocked()"
        />
        <div v-else-if="blockedFirstLoading" class="ip-skeleton" aria-busy="true" aria-label="封禁名单加载中">
          <n-skeleton v-for="index in 5" :key="index" height="44px" />
        </div>
        <DataTable
          v-else
          table-id="ip-blocked"
          :columns="blockedColumns"
          :data="blockedPage.items"
          row-key="key"
          remote
          :loading="blockedLoading"
          :page="blockedPage.page"
          :page-size="blockedTable.state.pageSize"
          :item-count="blockedPage.total"
          :page-sizes="[20, 50, 100]"
          empty-description="暂无封禁记录"
          @update:page="blockedTable.setPage"
          @update:page-size="blockedTable.setPageSize"
          @update:sorter="onBlockedSorter"
        >
          <template #empty>
            <Empty
              :description="
                blockedChips.length
                  ? '没有符合筛选的封禁记录。'
                  : '暂无封禁记录。封禁后该 IP 的全部 /v1 请求直接 403。'
              "
            >
              <Btn v-if="blockedChips.length" variant="default" size="sm" @click="resetBlockedFilters">
                清除筛选
              </Btn>
              <Btn v-else variant="primary" size="sm" :icon="Ban" @click="openBlock()">封禁 IP</Btn>
            </Empty>
          </template>
        </DataTable>
      </div>
    </Card>

    <!-- 设备关联 -->
    <Card v-else flush class="ip-card">
      <div class="ip-section">
        <h3 class="ip-section__title">设备关联</h3>
        <p class="ip-section__desc">{{ DEVICES_HELP }}</p>
        <HelpTip :content="DEVICES_NOTE" label="设备标识说明" />
      </div>
      <div class="ip-filter">
        <FilterBar
          :chips="deviceChips"
          :summary="deviceSummary"
          :loading="deviceLoading"
          @change="onDeviceFilterChange"
          @reset="resetDeviceFilters"
          @remove="(key: string) => deviceTable.setFilter(key, '')"
        >
          <template #default="{ change }">
            <n-input
              class="ip-filter__search"
              :value="deviceTable.state.filters.dq"
              placeholder="搜索标识 / IP"
              clearable
              @update:value="(value: string) => change('dq', value)"
            />
            <n-select
              class="ip-filter__select"
              :value="deviceTable.state.filters.ds"
              :options="deviceStatusOptions"
              @update:value="(value: string | number | null) => deviceTable.setFilter('ds', value === null ? '' : String(value))"
            />
          </template>
        </FilterBar>
      </div>
      <div class="ip-body">
        <ErrorBlock
          v-if="deviceError && devices === null"
          :title="'加载设备关联失败：' + deviceError.message"
          :detail="deviceError.detail"
          @retry="loadDevices()"
        />
        <div v-else-if="deviceFirstLoading" class="ip-skeleton" aria-busy="true" aria-label="设备关联加载中">
          <n-skeleton v-for="index in 5" :key="index" height="44px" />
        </div>
        <DataTable
          v-else
          table-id="ip-devices"
          :columns="deviceColumns"
          :data="devicePage.items"
          row-key="key"
          remote
          :loading="deviceLoading"
          :page="devicePage.page"
          :page-size="deviceTable.state.pageSize"
          :item-count="devicePage.total"
          :page-sizes="[20, 50, 100]"
          empty-description="暂无客户端上报的设备标识"
          @update:page="deviceTable.setPage"
          @update:page-size="deviceTable.setPageSize"
          @update:sorter="onDeviceSorter"
        >
          <template #empty>
            <Empty
              :description="
                deviceChips.length
                  ? '没有符合筛选的设备标识。'
                  : '暂无客户端上报的设备标识。客户端上报设备 ID / 指纹 / MAC 后才会出现在这里。'
              "
            >
              <Btn v-if="deviceChips.length" variant="default" size="sm" @click="resetDeviceFilters">
                清除筛选
              </Btn>
            </Empty>
          </template>
        </DataTable>
      </div>
    </Card>

    <FormDrawer
      v-model:show="drawerOpen"
      :title="drawerTitle"
      submit-text="封禁 IP"
      :submit-loading="submitting"
      @submit="submitBlock"
    >
      <FormField
        label="IP 或 CIDR"
        required
        :error="formErrorIp"
        hint="IPv4 或 IPv6 单地址。CIDR 网段需要后端支持，当前接口只接受单个地址。"
      >
        <n-input
          v-model:value="formIp"
          placeholder="IPv4 或 IPv6"
          maxlength="64"
          clearable
          @update:value="formErrorIp = ''"
        />
      </FormField>

      <FormField label="原因" hint="可选。例如：异常刷请求。">
        <n-input v-model:value="formNote" placeholder="例如：异常刷请求" maxlength="255" show-count />
      </FormField>

      <FormField
        label="时长"
        :error="formErrorDuration"
        hint="到期由 blocked_ips.expires_at 判定；永久 = 不自动解封。"
      >
        <n-select :value="formDuration" :options="durationOptions" @update:value="onDurationChange" />
      </FormField>

      <FormField v-if="formDuration === 'custom'" label="到期时间" required :error="formErrorDuration">
        <n-date-picker
          v-model:value="formCustomMs"
          type="datetime"
          class="ip-datetime"
          clearable
          @update:value="formErrorDuration = ''"
        />
      </FormField>
    </FormDrawer>
  </div>
</template>

<style scoped>
.ip-page {
  display: grid;
  gap: var(--sp-4);
  min-width: 0;
}

.ip-tabs {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  min-width: 0;
}

.ip-card {
  min-width: 0;
}

.ip-section {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--sp-2);
  padding: var(--sp-4) var(--sp-5) var(--sp-2);
}

.ip-section__title {
  margin: 0;
  font-size: var(--fs-h2);
  line-height: var(--lh-h2);
  font-weight: 600;
  color: var(--fg);
}

.ip-section__desc {
  margin: 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.ip-filter {
  padding: var(--sp-3) var(--sp-5);
  border-bottom: 1px solid var(--border-soft);
}

.ip-filter__search {
  width: 240px;
  max-width: 100%;
}

.ip-filter__select {
  width: 160px;
}

.ip-filter__number {
  width: 140px;
}

.ip-body {
  padding: var(--sp-3) var(--sp-5) var(--sp-5);
  min-width: 0;
}

.ip-skeleton {
  display: grid;
  gap: var(--sp-2);
  padding: var(--sp-3) 0;
}

.ip-cell {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--sp-1);
  min-width: 0;
}

.ip-mono {
  font-family: var(--font-mono);
  font-size: 12.5px;
  color: var(--fg);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ip-current {
  flex: 1 0 100%;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.ip-pop {
  display: grid;
  gap: var(--sp-2);
  max-width: 340px;
}

.ip-pop__title {
  margin: 0;
  font-size: var(--fs-small);
  font-weight: 600;
  color: var(--fg);
}

.ip-pop__text {
  margin: 0;
  font-family: var(--font-mono);
  font-size: 11.5px;
  line-height: 1.5;
  color: var(--fg-2);
  white-space: pre-wrap;
  word-break: break-word;
}

.ip-numcell {
  display: grid;
  gap: 2px;
  justify-items: end;
  min-width: 0;
}

.ip-num {
  font-variant-numeric: tabular-nums;
  color: var(--fg);
}

.ip-num.is-danger {
  color: var(--danger);
}

.ip-num.is-warn {
  color: var(--warn);
}

.ip-sub {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  white-space: nowrap;
}

.ip-note {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--fg-2);
}

.ip-dash {
  color: var(--fg-faint);
}

.ip-dash-row {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-1);
}

.ip-permanent {
  color: var(--fg-2);
}

.ip-expiry.is-neutral {
  color: var(--fg-2);
}

.ip-expiry.is-warn {
  color: var(--warn);
}

.ip-expiry.is-danger {
  color: var(--danger);
}

.ip-chips {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--sp-1);
  min-width: 0;
}

.ip-device {
  display: grid;
  gap: 2px;
  min-width: 0;
}

.ip-device__kind {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.ip-datetime {
  width: 100%;
}

@media (max-width: 640px) {
  .ip-filter {
    padding: var(--sp-3) var(--sp-4);
  }

  .ip-body {
    padding: var(--sp-2) var(--sp-4) var(--sp-4);
  }

  .ip-section {
    padding: var(--sp-3) var(--sp-4) var(--sp-2);
  }

  .ip-filter__search,
  .ip-filter__select,
  .ip-filter__number {
    width: 100%;
  }
}
</style>
