<script setup lang="ts">
/**
 * IP 管理 · 设备关联（客户端上报的设备 ID / 指纹 / MAC；后端一次返回最近 2000 条，客户端筛排分页）。
 * 一行工具栏：搜索标识 / IP · 状态 ……… 统计 · 重置 · 刷新。
 */
import { computed, h, onBeforeUnmount, ref, watch, type VNodeChild } from 'vue'
import { NInput, NSelect } from 'naive-ui'
import { MonitorSmartphone, RefreshCw, RotateCw, Search } from 'lucide-vue-next'
import {
  Badge,
  Btn,
  Card,
  Chip,
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
import { FMT_EMPTY, fmtFullTime, fmtRelative } from '@/utils/format'
import { useI18n } from '@/i18n'
import { filterDeviceRows, paginateRows, sortDeviceRows, type IpDeviceRow } from '@/api/ip'

type TableQuery = ReturnType<typeof useTableQuery>

const props = defineProps<{
  table: TableQuery
  rows: IpDeviceRow[] | null
  loading: boolean
  /** 第 5 轮：二次进入的静默刷新（DataTable `refreshing`：保留旧数据，只在工具栏转小圈）。 */
  refreshing?: boolean
  error: { message: string; detail: string } | null
}>()

const emit = defineEmits<{
  (event: 'toggle', row: IpDeviceRow): void
  (event: 'refresh'): void
  (event: 'retry'): void
}>()

const { t } = useI18n()

const filters = computed(() => props.table.state.filters)
const firstLoading = computed(() => props.loading && props.rows === null)
const statusOptions = computed(() => [
  { label: t('page.ip.filter.statusAll'), value: '' },
  { label: t('page.ip.status.blocked'), value: 'blocked' },
  { label: t('page.ip.status.allowed'), value: 'allowed' },
])

const filtered = computed(() => filterDeviceRows(props.rows || [], { q: filters.value.dq, status: filters.value.ds }))
const sorted = computed(() => sortDeviceRows(filtered.value, props.table.state.sortBy, props.table.state.sortOrder))
const pageData = computed(() => paginateRows(sorted.value, props.table.state.page, props.table.state.pageSize))
const blockedCount = computed(() => (props.rows || []).filter((row) => row.blocked).length)
const hasFilters = computed(() => Boolean(filters.value.dq || filters.value.ds))

const summary = computed(() => {
  const total = props.rows?.length ?? 0
  if (hasFilters.value) {
    return t('page.ip.devices.summaryFiltered', { shown: filtered.value.length, total, blocked: blockedCount.value })
  }
  return t('page.ip.devices.summary', { total, blocked: blockedCount.value })
})

const search = ref(filters.value.dq || '')
let timer: ReturnType<typeof setTimeout> | null = null
watch(
  () => filters.value.dq,
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
    if (next !== (filters.value.dq || '')) props.table.setFilter('dq', next)
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
  props.table.setFilters({ dq: '', ds: '' })
  props.table.setPage(1)
}

const SORT_MAP: Record<string, string> = { kind_label: 'kindLabel', last_seen: 'lastSeen', blocked: 'blocked' }

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

function asRow(row: TableRow): IpDeviceRow {
  return row as IpDeviceRow
}

function renderKind(row: TableRow): VNodeChild {
  const item = asRow(row)
  return h('div', { class: 'id-kind' }, [
    h('span', { class: 'id-kind__value', title: item.value }, item.value || FMT_EMPTY),
    h('small', { class: 'id-sub' }, item.kindLabel || FMT_EMPTY),
  ])
}

function renderIps(row: TableRow): VNodeChild {
  const item = asRow(row)
  if (!item.ips.length) return h('span', { class: 'id-faint' }, FMT_EMPTY)
  const shown = item.ips.slice(0, 3)
  const nodes: VNodeChild[] = shown.map((ip) => h(Chip, { key: ip, mono: true, title: ip }, { default: () => ip }))
  if (item.ips.length > shown.length) {
    nodes.push(
      h(Chip, { key: 'more', title: item.ips.slice(3).join(', ') }, { default: () => '+' + (item.ips.length - shown.length) }),
    )
  }
  return h('div', { class: 'id-chips' }, nodes)
}

function renderSeen(row: TableRow): VNodeChild {
  const item = asRow(row)
  if (!item.lastSeen) return h('span', { class: 'id-faint' }, FMT_EMPTY)
  return h('span', { class: 'id-time', title: fmtFullTime(item.lastSeen) }, fmtRelative(item.lastSeen))
}

function renderStatus(row: TableRow): VNodeChild {
  const item = asRow(row)
  return item.blocked
    ? h(Badge, { tone: 'danger', dot: true }, { default: () => t('page.ip.status.blocked') })
    : h('span', { class: 'id-time' }, t('page.ip.status.allowed'))
}

function renderActions(row: TableRow): VNodeChild {
  const item = asRow(row)
  return h(RowActions, {
    actions: [
      item.blocked
        ? { key: 'toggle', label: t('page.ip.action.unblockDevice') }
        : { key: 'toggle', label: t('page.ip.action.blockDevice'), danger: true },
    ],
    max: item.blocked ? 1 : 0,
    onSelect: (key: string) => {
      if (key === 'toggle') emit('toggle', item)
    },
  })
}

const columns = computed<TableColumn[]>(() => [
  { key: 'kind_label', title: t('page.ip.devices.colKind'), mobile: 'title', minWidth: 240, sortable: true, sortOrder: sortOrderOf('kindLabel'), render: renderKind },
  { key: 'ips', title: t('page.ip.devices.colIps'), mobile: 'meta', minWidth: 240, render: renderIps },
  { key: 'last_seen', title: t('page.ip.devices.colLastSeen'), mobile: 'meta', width: 132, sortable: true, sortOrder: sortOrderOf('lastSeen'), render: renderSeen },
  { key: 'blocked', title: t('page.ip.col.status'), mobile: 'subtitle', width: 104, sortable: true, sortOrder: sortOrderOf('blocked'), render: renderStatus },
  { key: 'actions', title: '', mobile: 'actions', width: 140, hideable: false, fixed: 'right', render: renderActions },
])
</script>

<template>
  <Card flush class="id">
    <ErrorBlock
      v-if="error && rows === null"
      class="id-error"
      :title="t('page.ip.error.devices', { error: error.message })"
      :detail="error.detail"
      @retry="emit('retry')"
    />
    <div v-else-if="firstLoading" class="id-skeleton" aria-busy="true" :aria-label="t('page.ip.loading.devices')">
      <span v-for="index in 5" :key="index" class="mh-skeleton id-skeleton__row" />
    </div>
    <DataTable
      v-else
      class="id-table"
      table-id="ip-devices"
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
      :empty-description="t('page.ip.devices.empty')"
      @update:page="table.setPage"
      @update:page-size="table.setPageSize"
      @update:sorter="onSorter"
    >
      <template #toolbar>
        <div class="id-bar" role="search">
          <NInput
            class="id-search"
            :value="search"
            :placeholder="t('page.ip.devices.searchPlaceholder')"
            clearable
            :aria-label="t('page.ip.devices.searchPlaceholder')"
            @update:value="onSearch"
          >
            <template #prefix><Search :size="14" class="id-icon" /></template>
          </NInput>
          <NSelect
            class="id-select"
            :value="filters.ds"
            :options="statusOptions"
            :aria-label="t('page.ip.chip.status')"
            @update:value="(value: string | number | null) => table.setFilter('ds', str(value))"
          />
          <div class="id-end">
            <span class="id-summary tnum">{{ summary }}</span>
            <HelpTip :content="t('page.ip.devices.keyHelp')" :label="t('page.ip.devices.keyHelpLabel')" />
            <Btn v-if="hasFilters" variant="quiet" size="sm" :icon="RotateCw" @click="reset">{{ t('filter.reset') }}</Btn>
            <IconBtn :label="t('common.refresh')" :icon="RefreshCw" :loading="loading" @click="emit('refresh')" />
          </div>
        </div>
      </template>
      <template #empty>
        <Empty
          :description="hasFilters ? t('page.ip.devices.emptyFiltered') : t('page.ip.devices.emptyBody')"
          :icon="MonitorSmartphone"
        >
          <Btn v-if="hasFilters" variant="default" size="sm" @click="reset">{{ t('page.ip.clearFilters') }}</Btn>
        </Empty>
      </template>
    </DataTable>
  </Card>
</template>

<style scoped>
.id {
  min-width: 0;
}

.id :deep(.mh-card__body) {
  padding: var(--sp-3) var(--sp-4) var(--sp-4);
}

.id-error,
.id-skeleton {
  margin: var(--sp-2) 0;
}

.id-skeleton {
  display: grid;
  gap: var(--sp-2);
}

.id-skeleton__row {
  height: 36px;
}

.id-table :deep(.mh-table__toolbar-left) {
  flex: 1;
}

.id-table :deep(.n-data-table) {
  --n-merged-td-color-sorting: var(--n-merged-td-color) !important;
}

.id-bar {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
  flex: 1;
  min-width: 0;
}

.id-search {
  width: 240px;
}

.id-select {
  width: 136px;
}

.id-icon {
  color: var(--fg-faint);
}

.id-end {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-2);
  margin-left: auto;
}

.id-summary {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  white-space: nowrap;
}

.id-table :deep(.id-kind) {
  display: grid;
  gap: 2px;
  min-width: 0;
}

.id-table :deep(.id-kind__value) {
  font-family: var(--font-mono);
  font-weight: 500;
  color: var(--fg);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.id-table :deep(.id-sub) {
  font-size: var(--fs-tiny);
  line-height: var(--lh-tiny);
  color: var(--fg-muted);
}

.id-table :deep(.id-chips) {
  display: flex;
  flex-wrap: nowrap;
  align-items: center;
  gap: var(--sp-1);
  min-width: 0;
  overflow: hidden;
}

.id-table :deep(.id-time) {
  font-size: var(--fs-small);
  color: var(--fg-2);
  white-space: nowrap;
}

.id-table :deep(.id-faint) {
  color: var(--fg-faint);
}

@media (max-width: 640px) {
  .id-search,
  .id-select {
    width: 100%;
  }

  .id-end {
    margin-left: 0;
    flex-wrap: wrap;
  }
}
</style>
