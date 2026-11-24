<script setup lang="ts">
/**
 * IP 管理 · 封禁名单（后端一次返回全部，筛选 / 排序 / 分页在客户端）。
 *
 * 一行工具栏：搜索 IP / 原因 · 状态 · 来源（自动 / 手动）……… 统计 · 重置 · 清理自动封禁 · 刷新。
 * 「自动」= 备注以 auto: 开头（admin/abuse_guard.py 风控自动写入），原因列去掉前缀显示，来源列单独标出；
 * 「清理自动封禁」只发事件，确认框与逐条删除由页面做（见 IpPage.cleanAutoBlocks）。
 * 后端不返回 expires_at 时（老后端）不显示「到期」列，避免界面写着到期而实际永久封禁。
 * r3：来源优先用后端 source；启用但已过期的行标「已过期」；定时封禁可在 ⋯ 里「改为永久」。
 */
import { computed, h, onBeforeUnmount, ref, watch, type VNodeChild } from 'vue'
import { NInput, NSelect } from 'naive-ui'
import { Ban, Eraser, RefreshCw, RotateCw, Search } from 'lucide-vue-next'
import {
  Badge,
  Btn,
  Card,
  DataTable,
  Empty,
  ErrorBlock,
  HelpTip,
  IconBtn,
  RowActions,
  type SorterPayload,
  type TableColumn,
  type TableRow,
} from '@/components'
import type { useTableQuery } from '@/composables'
import { FMT_EMPTY, expiryTone, fmtExpiry, fmtFullTime, fmtInt, fmtRelative } from '@/utils/format'
import { useI18n, type MessageKey } from '@/i18n'
import {
  autoBlockReason,
  blockedSourceStats,
  filterBlockedBySource,
  filterBlockedRows,
  paginateRows,
  sortBlockedRows,
  type BlockedIpRow,
} from '@/api/ip'

type TableQuery = ReturnType<typeof useTableQuery>

const props = withDefaults(
  defineProps<{
    table: TableQuery
    rows: BlockedIpRow[] | null
    loading: boolean
    /** 第 5 轮：二次进入的静默刷新（DataTable `refreshing`：保留旧数据，只在工具栏转小圈）。 */
    refreshing?: boolean
    error: { message: string; detail: string } | null
    expirySupported: boolean
    /** 正在清理自动封禁时的进度；null = 没在清理。 */
    cleaning?: { done: number; total: number } | null
  }>(),
  { cleaning: null, refreshing: false },
)

const emit = defineEmits<{
  (event: 'action', row: BlockedIpRow, key: string): void
  (event: 'cleanAuto'): void
  (event: 'create'): void
  (event: 'refresh'): void
  (event: 'retry'): void
}>()

const { t } = useI18n()

const filters = computed(() => props.table.state.filters)
const firstLoading = computed(() => props.loading && props.rows === null)
const stats = computed(() => blockedSourceStats(props.rows))

const statusOptions = computed(() => [
  { label: t('page.ip.filter.statusAll'), value: '' },
  { label: t('page.ip.status.blocked'), value: 'enabled' },
  { label: t('page.ip.status.revoked'), value: 'disabled' },
])
const sourceOptions = computed(() => [
  { label: t('page.ip.filter.sourceAll'), value: '' },
  { label: t('page.ip.source.auto'), value: 'auto' },
  { label: t('page.ip.source.manual'), value: 'manual' },
])

const filtered = computed(() =>
  filterBlockedBySource(
    filterBlockedRows(props.rows || [], { q: filters.value.bq, status: filters.value.bs }),
    filters.value.src || '',
  ),
)
const sorted = computed(() => sortBlockedRows(filtered.value, props.table.state.sortBy, props.table.state.sortOrder))
const pageData = computed(() => paginateRows(sorted.value, props.table.state.page, props.table.state.pageSize))

const hasFilters = computed(() => Boolean(filters.value.bq || filters.value.bs || filters.value.src))

const summary = computed(() => {
  const total = stats.value.total
  const enabled = stats.value.enabled
  if (hasFilters.value) return t('page.ip.blocked.summaryFiltered', { shown: filtered.value.length, total, enabled })
  return t('page.ip.blocked.summary', { total, enabled })
})

const cleanLabel = computed(() => {
  if (props.cleaning) return t('page.ip.blocked.cleaning', { done: props.cleaning.done, total: props.cleaning.total })
  return stats.value.auto > 0
    ? t('page.ip.blocked.cleanAutoCount', { count: fmtInt(stats.value.auto) })
    : t('page.ip.blocked.cleanAuto')
})

/* ---------- 搜索防抖 ---------- */
const search = ref(filters.value.bq || '')
let timer: ReturnType<typeof setTimeout> | null = null
watch(
  () => filters.value.bq,
  (value) => {
    if (!timer) search.value = value || ''
  },
)
function onSearch(value: string): void {
  search.value = value ?? ''
  if (timer) clearTimeout(timer)
  timer = setTimeout(() => {
    timer = null
    const next = search.value.trim()
    if (next !== (filters.value.bq || '')) props.table.setFilter('bq', next)
  }, 300)
}
onBeforeUnmount(() => {
  if (timer) clearTimeout(timer)
})

function str(value: string | number | null): string {
  return value === null || value === undefined ? '' : String(value)
}

function reset(): void {
  if (timer) clearTimeout(timer)
  timer = null
  search.value = ''
  props.table.setFilters({ bq: '', bs: '', src: '' })
  props.table.setPage(1)
}

/* ---------- 排序 ---------- */
const SORT_MAP: Record<string, string> = { ip: 'ip', enabled: 'enabled', created_at: 'createdAt' }

function sortOrderOf(key: string): 'ascend' | 'descend' | false {
  const state = props.table.state
  if (state.sortBy !== key || !state.sortOrder) return false
  return state.sortOrder === 'asc' ? 'ascend' : 'descend'
}

function onSorter(sorter: SorterPayload | null): void {
  const key = sorter && sorter.order ? SORT_MAP[sorter.columnKey] : ''
  if (!sorter || !sorter.order || !key) {
    props.table.setSort('', '')
    return
  }
  props.table.setSort(key, sorter.order === 'ascend' ? 'asc' : 'desc')
}

/* ---------- 单元格 ---------- */
function asRow(row: TableRow): BlockedIpRow {
  return row as BlockedIpRow
}

function renderIp(row: TableRow): VNodeChild {
  const item = asRow(row)
  return h('span', { class: 'ib-ip', title: item.ip }, item.ip || FMT_EMPTY)
}

function renderSource(row: TableRow): VNodeChild {
  const item = asRow(row)
  const reason = autoBlockReason(item.note)
  // 后端 source 优先（r3）；老后端没有 source 时 toBlockedRows 已按 auto: 前缀推算
  if (item.source !== 'auto') return h('span', { class: 'ib-muted' }, t('page.ip.source.manual'))
  return h(
    Badge,
    { tone: 'neutral', title: t('page.ip.source.autoTitle', { reason: reason || FMT_EMPTY }) },
    { default: () => t('page.ip.source.auto') },
  )
}

function renderNote(row: TableRow): VNodeChild {
  const item = asRow(row)
  const reason = autoBlockReason(item.note)
  const text = reason === null ? item.note : reason
  if (!text) return h('span', { class: 'ib-faint' }, FMT_EMPTY)
  return h('span', { class: 'ib-note', title: item.note }, text)
}

function renderCreated(row: TableRow): VNodeChild {
  const item = asRow(row)
  if (!item.createdAt) return h('span', { class: 'ib-faint' }, FMT_EMPTY)
  return h('span', { class: 'ib-time', title: fmtFullTime(item.createdAt) }, fmtRelative(item.createdAt))
}

function renderExpiry(row: TableRow): VNodeChild {
  const item = asRow(row)
  if (item.expiry === 'permanent') return h('span', { class: 'ib-muted' }, t('page.ip.blocked.permanent'))
  return h(
    'span',
    { class: 'ib-expiry is-' + expiryTone(item.expiresAt), title: fmtFullTime(item.expiresAt) },
    fmtExpiry(item.expiresAt),
  )
}

function renderStatus(row: TableRow): VNodeChild {
  const item = asRow(row)
  // r3：启用但已过到期时间 = 不再拦截（后端 expired / active），单独标出，避免误以为仍在封禁。
  if (item.enabled && item.expired) {
    return h(
      Badge,
      { tone: 'warn', dot: true, title: fmtFullTime(item.expiresAt) },
      { default: () => t('page.ip.status.expired' as MessageKey) },
    )
  }
  return item.enabled
    ? h(Badge, { tone: 'danger', dot: true }, { default: () => t('page.ip.status.blocked') })
    : h(Badge, { tone: 'neutral', dot: true }, { default: () => t('page.ip.status.revoked') })
}

function renderActions(row: TableRow): VNodeChild {
  const item = asRow(row)
  return h(RowActions, {
    actions: [
      { key: 'toggle', label: item.enabled ? t('page.ip.action.unblock') : t('page.ip.action.reblock') },
      { key: 'logs', label: t('page.ip.action.viewLogs') },
      ...(props.expirySupported && item.expiry === 'timed'
        ? [{ key: 'permanent', label: t('page.ip.action.makePermanent' as MessageKey) }]
        : []),
      { key: 'delete', label: t('page.ip.action.deleteRecord'), danger: true },
    ],
    max: 1,
    onSelect: (key: string) => emit('action', item, key),
  })
}

const columns = computed<TableColumn[]>(() => {
  const list: TableColumn[] = [
    { key: 'ip', title: t('page.ip.blocked.colIp'), mobile: 'title', width: 200, sortable: true, sortOrder: sortOrderOf('ip'), render: renderIp },
    { key: 'source', title: t('page.ip.blocked.colSource'), mobile: 'meta', width: 96, render: renderSource },
    { key: 'note', title: t('page.ip.blocked.colNote'), mobile: 'subtitle', minWidth: 220, render: renderNote },
    { key: 'created_at', title: t('page.ip.blocked.colCreated'), mobile: 'meta', width: 132, sortable: true, sortOrder: sortOrderOf('createdAt'), render: renderCreated },
  ]
  if (props.expirySupported) {
    list.push({ key: 'expires', title: t('page.ip.blocked.colExpires'), mobile: 'meta', width: 132, render: renderExpiry })
  }
  list.push(
    { key: 'enabled', title: t('page.ip.col.status'), mobile: 'meta', width: 104, sortable: true, sortOrder: sortOrderOf('enabled'), render: renderStatus },
    { key: 'actions', title: '', mobile: 'actions', width: 132, hideable: false, fixed: 'right', render: renderActions },
  )
  return list
})
</script>

<template>
  <Card flush class="ib">
    <ErrorBlock
      v-if="error && rows === null"
      class="ib-error"
      :title="t('page.ip.error.blocked', { error: error.message })"
      :detail="error.detail"
      @retry="emit('retry')"
    />
    <div v-else-if="firstLoading" class="ib-skeleton" aria-busy="true" :aria-label="t('page.ip.loading.blocked')">
      <span v-for="index in 5" :key="index" class="mh-skeleton ib-skeleton__row" />
    </div>
    <DataTable
      v-else
      class="ib-table"
      table-id="ip-blocked"
      :columns="columns"
      :data="pageData.items"
      row-key="key"
      remote
      :loading="loading"
      :refreshing="refreshing"
      :page="pageData.page"
      :page-size="table.state.pageSize"
      :item-count="pageData.total"
      :page-sizes="[20, 50, 100]"
      :empty-description="t('page.ip.blocked.empty')"
      @update:page="table.setPage"
      @update:page-size="table.setPageSize"
      @update:sorter="onSorter"
    >
      <template #toolbar>
        <div class="ib-bar" role="search">
          <NInput
            class="ib-search"
            :value="search"
            :placeholder="t('page.ip.blocked.searchPlaceholder')"
            clearable
            :aria-label="t('page.ip.blocked.searchPlaceholder')"
            @update:value="onSearch"
          >
            <template #prefix><Search :size="14" class="ib-icon" /></template>
          </NInput>
          <NSelect
            class="ib-select"
            :value="filters.bs"
            :options="statusOptions"
            :aria-label="t('page.ip.chip.status')"
            @update:value="(value: string | number | null) => table.setFilter('bs', str(value))"
          />
          <NSelect
            class="ib-select"
            :value="filters.src || ''"
            :options="sourceOptions"
            :aria-label="t('page.ip.blocked.colSource')"
            @update:value="(value: string | number | null) => table.setFilter('src', str(value))"
          />
          <div class="ib-end">
            <span class="ib-summary tnum">{{ summary }}</span>
            <HelpTip
              v-if="!expirySupported"
              :content="t('page.ip.blocked.permanentOnly')"
              :label="t('page.ip.blocked.expiryHelpLabel')"
            />
            <Btn v-if="hasFilters" variant="quiet" size="sm" :icon="RotateCw" @click="reset">{{ t('filter.reset') }}</Btn>
            <Btn
              variant="default"
              size="sm"
              :icon="Eraser"
              :loading="Boolean(cleaning)"
              :disabled="!cleaning && stats.auto === 0"
              :title="t('page.ip.confirm.cleanAuto.body', { enabled: fmtInt(stats.autoEnabled) })"
              @click="emit('cleanAuto')"
            >
              {{ cleanLabel }}
            </Btn>
            <IconBtn :label="t('common.refresh')" :icon="RefreshCw" :loading="loading" @click="emit('refresh')" />
          </div>
        </div>
      </template>
      <template #empty>
        <Empty :description="hasFilters ? t('page.ip.blocked.emptyFiltered') : t('page.ip.blocked.emptyBody')" :icon="Ban">
          <Btn v-if="hasFilters" variant="default" size="sm" @click="reset">{{ t('page.ip.clearFilters') }}</Btn>
          <Btn v-else variant="default" size="sm" :icon="Ban" @click="emit('create')">{{ t('page.ip.action.blockIp') }}</Btn>
        </Empty>
      </template>
    </DataTable>
  </Card>
</template>

<style scoped>
.ib {
  min-width: 0;
}

.ib :deep(.mh-card__body) {
  padding: var(--sp-3) var(--sp-4) var(--sp-4);
}

.ib-error,
.ib-skeleton {
  margin: var(--sp-2) 0;
}

.ib-skeleton {
  display: grid;
  gap: var(--sp-2);
}

.ib-skeleton__row {
  height: 36px;
}

.ib-table :deep(.mh-table__toolbar-left) {
  flex: 1;
}

.ib-table :deep(.n-data-table) {
  --n-merged-td-color-sorting: var(--n-merged-td-color) !important;
}

.ib-bar {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
  flex: 1;
  min-width: 0;
}

.ib-search {
  width: 220px;
}

.ib-select {
  width: 136px;
}

.ib-icon {
  color: var(--fg-faint);
}

.ib-end {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-2);
  margin-left: auto;
}

.ib-summary {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  white-space: nowrap;
}

.ib-table :deep(.ib-ip) {
  font-family: var(--font-mono);
  font-weight: 500;
  color: var(--fg);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.ib-table :deep(.ib-note) {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--fg-2);
}

.ib-table :deep(.ib-time),
.ib-table :deep(.ib-muted) {
  font-size: var(--fs-small);
  color: var(--fg-2);
  white-space: nowrap;
}

.ib-table :deep(.ib-faint) {
  color: var(--fg-faint);
}

.ib-table :deep(.ib-expiry) {
  font-size: var(--fs-small);
  white-space: nowrap;
}

.ib-table :deep(.ib-expiry.is-neutral) {
  color: var(--fg-2);
}

.ib-table :deep(.ib-expiry.is-warn) {
  color: var(--warn);
}

.ib-table :deep(.ib-expiry.is-danger) {
  color: var(--danger);
}

@media (max-width: 640px) {
  .ib-search,
  .ib-select {
    width: 100%;
  }

  .ib-end {
    margin-left: 0;
    flex-wrap: wrap;
  }
}
</style>
