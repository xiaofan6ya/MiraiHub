<script setup lang="ts">
import TitleTip from '@/components/base/TitleTip.vue'

/**
 * IP 管理 · 活跃 IP（服务端分页 + 服务端排序）。
 *
 * 一行工具栏：时间范围 · 搜索 IP · 状态 · 流量筛选 · 最少请求数（· 平均上限）… 统计 · 重置 · 刷新。
 * 第 2 轮审查：数字框只显示「0」没有标签 → 0 时留空显示占位「最少请求数」；排序列整列灰底 → 去掉；
 * 积分 / Token 两列拼接过多（「0均 0.0000」「1.3B均 224.7K · 5,675 次有用量」）→ 主值 + 灰色副值两行，副值没有意义时不显示；
 * 行内「封禁」常驻 → 收进 ⋯，只露「查看日志」。
 */
import { computed, h, onBeforeUnmount, ref, watch, type VNodeChild } from 'vue'
import { NInput, NInputNumber, NPopover, NSelect } from 'naive-ui'
import { RefreshCw, RotateCw, Search } from 'lucide-vue-next'
import {
  Badge,
  Btn,
  Card,
  Copyable,
  DataTable,
  Empty,
  ErrorBlock,
  IconBtn,
  RowActions,
  type SorterPayload,
  type TableColumn,
  type TableRow,
} from '@/components'
import type { useTableQuery } from '@/composables'
import { FMT_EMPTY, fmtCompact, fmtCredit, fmtFullTime, fmtInt, fmtRelative } from '@/utils/format'
import { useI18n, type MessageKey } from '@/i18n'
import {
  IP_DAYS_OPTIONS,
  filterActivityRows,
  toActivityRows,
  type IpActivityPayload,
  type IpActivityRow,
} from '@/api/ip'

type TableQuery = ReturnType<typeof useTableQuery>

const props = defineProps<{
  table: TableQuery
  payload: IpActivityPayload | null
  loading: boolean
  /** 第 5 轮：二次进入的静默刷新（DataTable `refreshing`：保留旧数据，只在工具栏转小圈）。 */
  refreshing?: boolean
  error: { message: string; detail: string } | null
}>()

const emit = defineEmits<{
  (event: 'action', row: IpActivityRow, key: string): void
  (event: 'open', row: IpActivityRow): void
  (event: 'refresh'): void
  (event: 'retry'): void
}>()

const { t } = useI18n()

const DAYS_KEYS: Record<string, MessageKey> = {
  '1': 'page.ip.days.d1',
  '7': 'page.ip.days.d7',
  '30': 'page.ip.days.d30',
  '90': 'page.ip.days.d90',
}
const daysOptions = computed(() =>
  IP_DAYS_OPTIONS.map((option) => ({ label: t(DAYS_KEYS[option.value]), value: option.value as string })),
)
const usageOptions = computed(() => [
  { label: t('page.ip.usage.all'), value: '' },
  { label: t('page.ip.usage.tokens'), value: 'tokens' },
  { label: t('page.ip.usage.credits'), value: 'credits' },
])
const statusOptions = computed(() => [
  { label: t('page.ip.filter.statusAll'), value: '' },
  { label: t('page.ip.status.blocked'), value: 'blocked' },
  { label: t('page.ip.status.allowed'), value: 'allowed' },
])

const filters = computed(() => props.table.state.filters)
const rows = computed(() => toActivityRows(props.payload?.items, props.payload?.current_ip))
const visibleRows = computed(() => filterActivityRows(rows.value, { status: filters.value.status }))
const firstLoading = computed(() => props.loading && props.payload === null)

const hasFilters = computed(() => {
  const f = filters.value
  return Boolean(f.q || f.status || f.usage || (f.days && f.days !== '1') || (f.min && f.min !== '0'))
})

const summary = computed(() => {
  const payload = props.payload
  if (!payload) return ''
  const total = fmtInt(payload.total_ips)
  if (filters.value.status) return t('page.ip.activity.summaryFiltered', { shown: visibleRows.value.length, total })
  return t('page.ip.activity.summary', { total, requests: fmtInt(payload.total_requests) })
})

const emptyText = computed(() => (hasFilters.value ? t('page.ip.activity.emptyFiltered') : t('page.ip.activity.empty')))

/* ---------- 防抖输入 ---------- */
const search = ref(filters.value.q || '')
let searchTimer: ReturnType<typeof setTimeout> | null = null
watch(
  () => filters.value.q,
  (value) => {
    if (!searchTimer) search.value = value || ''
  },
)
function onSearch(value: string): void {
  search.value = value ?? ''
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    searchTimer = null
    const next = search.value.trim()
    if (next !== (filters.value.q || '')) props.table.setFilter('q', next)
  }, 300)
}

let numberTimer: ReturnType<typeof setTimeout> | null = null
function onNumber(name: 'min' | 'max', value: number | null): void {
  if (numberTimer) clearTimeout(numberTimer)
  numberTimer = setTimeout(() => {
    numberTimer = null
    const next = value === null || !Number.isFinite(value) ? (name === 'min' ? '0' : '') : String(Math.max(0, value))
    props.table.setFilter(name, next)
  }, 300)
}

onBeforeUnmount(() => {
  if (searchTimer) clearTimeout(searchTimer)
  if (numberTimer) clearTimeout(numberTimer)
})

/** 0 = 不限：输入框留空显示占位文字，而不是孤零零的「0」。 */
const minValue = computed(() => {
  const n = Number(filters.value.min || 0)
  return n > 0 ? n : null
})
const maxValue = computed(() => {
  const raw = filters.value.max
  if (raw === '' || raw === undefined) return null
  const n = Number(raw)
  return Number.isFinite(n) ? n : null
})

function setStatus(value: string | number | null): void {
  props.table.setFilter('status', value === null || value === undefined ? '' : String(value))
}

/** 流量筛选切换时给一组与旧版一致的默认上限（0.01 积分 / 100 Token）。 */
function setUsage(value: string | number | null): void {
  const next = value === null || value === undefined ? '' : String(value)
  props.table.setFilters({
    usage: next,
    max: next === 'credits' ? '0.01' : next === 'tokens' ? '100' : '',
    min: next ? '10' : '0',
  })
}

function reset(): void {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = null
  search.value = ''
  props.table.setFilters({ days: '1', q: '', status: '', usage: '', min: '0', max: '' })
  props.table.setSort('requests', 'desc')
}

/* ---------- 排序 ---------- */
const SORT_KEY_MAP: Record<string, string> = {
  ip: 'ip',
  requests: 'requests',
  failed: 'failed',
  blocked_requests: 'blocked_requests',
  credits: 'credits',
  tokens: 'tokens',
  last_seen: 'last_seen',
}

function sortOrderOf(key: string): 'ascend' | 'descend' | false {
  const state = props.table.state
  if (state.sortBy !== key || !state.sortOrder) return false
  return state.sortOrder === 'asc' ? 'ascend' : 'descend'
}

function onSorter(sorter: SorterPayload | null): void {
  const key = sorter && sorter.order ? SORT_KEY_MAP[sorter.columnKey] : ''
  if (!sorter || !sorter.order || !key) {
    props.table.setSort('requests', 'desc')
    return
  }
  props.table.setSort(key, sorter.order === 'ascend' ? 'asc' : 'desc')
}

/* ---------- 单元格 ---------- */
function asRow(row: TableRow): IpActivityRow {
  return row as IpActivityRow
}

function renderIp(row: TableRow): VNodeChild {
  const item = asRow(row)
  const ip = h(
    NPopover,
    { trigger: 'hover', placement: 'right-start', showArrow: false, delay: 300 },
    {
      trigger: () => h('span', { class: 'ia-ip' }, item.ip || FMT_EMPTY),
      default: () =>
        h('div', { class: 'ia-pop' }, [
          h('p', { class: 'ia-pop__title' }, t('page.ip.cell.clientTitle')),
          h('pre', { class: 'ia-pop__text' }, item.client.tooltip),
          h(Copyable, { value: item.ip, muted: true }),
        ]),
    },
  )
  return h('div', { class: 'ia-cell' }, [
    h('div', { class: 'ia-cell__main' }, [
      ip,
      item.isCurrent ? h(Badge, { tone: 'brand' }, { default: () => t('page.ip.cell.currentShort') }) : null,
    ]),
    item.client.client ? h('small', { class: 'ia-sub' }, item.client.client) : null,
  ])
}

function numberCell(main: string, sub?: string, tone?: 'danger' | 'warn' | 'faint'): VNodeChild {
  return h('div', { class: 'ia-num' }, [
    h('span', { class: 'ia-num__main' + (tone ? ' is-' + tone : '') }, main),
    sub ? h('small', { class: 'ia-sub' }, sub) : null,
  ])
}

function renderCredits(row: TableRow): VNodeChild {
  const item = asRow(row)
  if (!item.credits) return numberCell(FMT_EMPTY, undefined, 'faint')
  const avg = item.avgCredits === null ? '' : t('page.ip.cell.avgCredits', { value: item.avgCredits.toFixed(4) })
  return numberCell(fmtCredit(item.credits), avg)
}

function renderTokens(row: TableRow): VNodeChild {
  const item = asRow(row)
  if (!item.tokens) return numberCell(FMT_EMPTY, undefined, 'faint')
  const avg = item.avgTokens === null ? '' : t('page.ip.cell.avgTokens', { value: fmtCompact(item.avgTokens) })
  return h(TitleTip, { content: String((t('page.ip.cell.knownUsage', { count: fmtInt(item.knownUsage) })) ?? '') }, { default: () => h('div', { class: 'ia-num' }, [
    h('span', { class: 'ia-num__main' }, fmtCompact(item.tokens)),
    avg ? h('small', { class: 'ia-sub' }, avg) : null,
  ]) })
}

function renderLastSeen(row: TableRow): VNodeChild {
  const item = asRow(row)
  if (!item.lastSeen) return h('span', { class: 'ia-faint' }, FMT_EMPTY)
  return h(TitleTip, { content: String((fmtFullTime(item.lastSeen)) ?? '') }, { default: () => h('span', { class: 'ia-time' }, fmtRelative(item.lastSeen)) })
}

function renderStatus(row: TableRow): VNodeChild {
  const item = asRow(row)
  return item.blocked
    ? h(Badge, { tone: 'danger', dot: true }, { default: () => t('page.ip.status.blocked') })
    : h('span', { class: 'ia-allowed' }, t('page.ip.status.allowed'))
}

function renderActions(row: TableRow): VNodeChild {
  const item = asRow(row)
  return h(RowActions, {
    actions: [
      { key: 'logs', label: t('page.ip.action.viewLogs') },
      item.blocked
        ? { key: 'toggle', label: t('page.ip.action.unblock') }
        : { key: 'toggle', label: t('page.ip.action.block'), danger: true },
    ],
    max: 1,
    onSelect: (key: string) => emit('action', item, key),
  })
}

const columns = computed<TableColumn[]>(() => [
  { key: 'ip', title: t('page.ip.col.ip'), mobile: 'title', minWidth: 200, sortable: true, sortOrder: sortOrderOf('ip'), render: renderIp },
  {
    key: 'requests', title: t('page.ip.col.requests'), mobile: 'meta', width: 104, numeric: true, sortable: true,
    sortOrder: sortOrderOf('requests'), render: (row: TableRow) => numberCell(fmtInt(asRow(row).requests)),
  },
  {
    key: 'failed', title: t('page.ip.col.failed'), mobile: 'meta', width: 96, numeric: true, sortable: true,
    sortOrder: sortOrderOf('failed'),
    render: (row: TableRow) => {
      const item = asRow(row)
      return numberCell(fmtInt(item.failed), undefined, item.failed > 0 ? 'danger' : 'faint')
    },
  },
  {
    key: 'blocked_requests', title: t('page.ip.col.blockedRequests'), mobile: 'meta', width: 104, numeric: true, sortable: true,
    sortOrder: sortOrderOf('blocked_requests'),
    render: (row: TableRow) => {
      const item = asRow(row)
      return numberCell(fmtInt(item.blockedRequests), undefined, item.blockedRequests > 0 ? 'warn' : 'faint')
    },
  },
  { key: 'credits', title: t('page.ip.col.credits'), mobile: 'meta', width: 128, numeric: true, sortable: true, sortOrder: sortOrderOf('credits'), render: renderCredits },
  { key: 'tokens', title: 'Token', mobile: 'meta', width: 128, numeric: true, sortable: true, sortOrder: sortOrderOf('tokens'), render: renderTokens },
  { key: 'last_seen', title: t('page.ip.col.lastSeen'), mobile: 'meta', width: 120, sortable: true, sortOrder: sortOrderOf('last_seen'), render: renderLastSeen },
  { key: 'status', title: t('page.ip.col.status'), mobile: 'subtitle', width: 96, render: renderStatus },
  { key: 'actions', title: '', mobile: 'actions', width: 132, hideable: false, fixed: 'right', render: renderActions },
])

function str(value: string | number | null): string {
  return value === null || value === undefined ? '' : String(value)
}
</script>

<template>
  <Card flush class="ia" :motion-pending="firstLoading">
    <ErrorBlock
      v-if="error && payload === null"
      class="ia-error"
      :title="t('page.ip.error.activity', { error: error.message })"
      :detail="error.detail"
      @retry="emit('retry')"
    />
    <div v-else-if="firstLoading" class="ia-skeleton" aria-busy="true" :aria-label="t('page.ip.loading.activity')">
      <span v-for="index in 6" :key="index" class="mh-skeleton ia-skeleton__row" />
    </div>
    <DataTable
      v-else
      class="ia-table"
      table-id="ip-active"
      :columns="columns"
      :data="visibleRows"
      row-key="key"
      remote
      :loading="loading"
      :refreshing="refreshing"
      :page="table.state.page"
      :page-size="table.state.pageSize"
      :item-count="Number(payload?.total_ips || 0)"
      :page-sizes="[20, 50, 100]"
      :empty-description="emptyText"
      clickable-rows
      @update:page="table.setPage"
      @update:page-size="table.setPageSize"
      @update:sorter="onSorter"
      @row-click="(row: TableRow) => emit('open', asRow(row))"
    >
      <template #toolbar>
        <div class="ia-bar">
          <NSelect
            class="ia-days"
            :value="filters.days || '1'"
            :options="daysOptions"
            :aria-label="t('page.ip.filter.daysAria')"
            @update:value="(value: string | number | null) => table.setFilter('days', value === null ? '1' : String(value))"
          />
          <NInput
            class="ia-search"
            :value="search"
            :placeholder="t('page.ip.filter.searchIp')"
            clearable
            :aria-label="t('page.ip.filter.searchIp')"
            @update:value="onSearch"
          >
            <template #prefix><Search :size="14" class="ia-icon" /></template>
          </NInput>
          <NSelect class="ia-select" :value="filters.status" :options="statusOptions" :aria-label="t('page.ip.chip.status')" @update:value="setStatus" />
          <NSelect
            class="ia-select"
            :value="filters.usage"
            :options="usageOptions"
            :aria-label="t('page.ip.chip.usage')"
            @update:value="(value: string | number | null) => setUsage(str(value))"
          />
          <NInputNumber
            class="ia-number"
            :value="minValue"
            :min="0"
            :max="1000000"
            :show-button="false"
            clearable
            :placeholder="t('page.ip.filter.minRequests')"
            :aria-label="t('page.ip.filter.minRequests')"
            @update:value="(value: number | null) => onNumber('min', value)"
          >
            <template #prefix><span class="ia-prefix">≥</span></template>
          </NInputNumber>
          <NInputNumber
            v-if="filters.usage"
            class="ia-number"
            :value="maxValue"
            :min="0"
            :show-button="false"
            :placeholder="filters.usage === 'credits' ? t('page.ip.filter.maxCredits') : t('page.ip.filter.maxTokens')"
            :aria-label="filters.usage === 'credits' ? t('page.ip.filter.maxCredits') : t('page.ip.filter.maxTokens')"
            @update:value="(value: number | null) => onNumber('max', value)"
          >
            <template #prefix><span class="ia-prefix">≤</span></template>
          </NInputNumber>
          <div class="ia-end">
            <span class="ia-summary tnum">{{ summary }}</span>
            <Btn v-if="hasFilters" variant="quiet" size="sm" :icon="RotateCw" @click="reset">{{ t('filter.reset') }}</Btn>
            <IconBtn :label="t('common.refresh')" :icon="RefreshCw" :loading="loading" @click="emit('refresh')" />
          </div>
        </div>
      </template>
      <template #empty>
        <Empty :description="emptyText">
          <Btn v-if="hasFilters" variant="default" size="sm" @click="reset">{{ t('page.ip.clearFilters') }}</Btn>
        </Empty>
      </template>
    </DataTable>
  </Card>
</template>

<style scoped>
.ia {
  min-width: 0;
}

.ia :deep(.mh-card__body) {
  padding: var(--sp-3) var(--sp-4) var(--sp-4);
}

.ia-error,
.ia-skeleton {
  margin: var(--sp-2) 0;
}

.ia-skeleton {
  display: grid;
  gap: var(--sp-2);
}

.ia-skeleton__row {
  height: 36px;
}

.ia-table :deep(.mh-table__toolbar-left) {
  flex: 1;
}

/* 排序列不再整列铺灰底（第 2 轮审查 P2），只保留表头箭头高亮 */
.ia-table :deep(.n-data-table) {
  --n-merged-td-color-sorting: var(--n-merged-td-color) !important;
}

.ia-bar {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
  flex: 1;
  min-width: 0;
}

.ia-days {
  width: 128px;
}

.ia-search {
  width: 200px;
}

.ia-select {
  width: 140px;
}

.ia-number {
  width: 132px;
}

.ia-icon,
.ia-prefix {
  color: var(--fg-faint);
}

.ia-end {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-2);
  margin-left: auto;
}

.ia-summary {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  white-space: nowrap;
}

.ia-table :deep(.ia-cell) {
  display: grid;
  gap: 2px;
  min-width: 0;
}

.ia-table :deep(.ia-cell__main) {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  min-width: 0;
}

.ia-table :deep(.ia-ip) {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--font-mono);
  font-weight: 500;
  color: var(--fg);
  cursor: default;
}

.ia-table :deep(.ia-sub) {
  font-size: var(--fs-tiny);
  line-height: var(--lh-tiny);
  color: var(--fg-muted);
  white-space: nowrap;
}

.ia-table :deep(.ia-num) {
  display: grid;
  gap: 2px;
  justify-items: end;
}

.ia-table :deep(.ia-num__main) {
  font-variant-numeric: tabular-nums;
  color: var(--fg);
}

.ia-table :deep(.ia-num__main.is-danger) {
  color: var(--danger);
}

.ia-table :deep(.ia-num__main.is-warn) {
  color: var(--warn);
}

.ia-table :deep(.ia-num__main.is-faint),
.ia-table :deep(.ia-faint) {
  color: var(--fg-faint);
}

.ia-table :deep(.ia-time),
.ia-table :deep(.ia-allowed) {
  font-size: var(--fs-small);
  color: var(--fg-2);
  white-space: nowrap;
}

@media (max-width: 640px) {
  .ia-days,
  .ia-search,
  .ia-select,
  .ia-number {
    width: 100%;
  }

  .ia-end {
    margin-left: 0;
    flex-wrap: wrap;
  }
}
</style>

<style>
/* IP 悬停浮层挂在 body 下，带不上 scope 属性，所以不用 scoped。 */
.ia-pop {
  display: grid;
  gap: var(--sp-2);
  max-width: 340px;
}

.ia-pop__title {
  margin: 0;
  font-size: var(--fs-small);
  font-weight: 600;
  color: var(--fg);
}

.ia-pop__text {
  margin: 0;
  font-family: var(--font-mono);
  font-size: var(--fs-tiny);
  line-height: var(--lh-tiny);
  color: var(--fg-2);
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
