<script setup lang="ts">
/**
 * 统一表格封装（04-components §2 DataTable）。
 * - 基于 NDataTable：表头 --surface-2、数值列右对齐、行高 44 / 紧凑 36（Rows3 切换，按表格 id 存 localStorage）
 * - 排序 / 多选 / 远程分页 20·50·100 / 列显示设置 / 空状态 / 加载时保留旧数据 + 顶部 2px 进度条
 * - ≤640px 按列的 mobile 标注渲染卡片列表，容器内滚动
 */
import { computed, defineComponent, h, ref, watch, type VNodeChild } from 'vue'
import { NCheckbox, NDataTable, NPagination, NPopover, NSpin } from 'naive-ui'
import type {
  DataTableColumns,
  DataTableRowData,
  DataTableRowKey,
  DataTableSortState,
} from 'naive-ui'
import type { MobileSlot, SorterPayload, TableColumn, TableRow } from './table'
import { Columns3, Rows3 } from 'lucide-vue-next'
import IconBtn from '../base/IconBtn.vue'
import Empty from '../base/Empty.vue'
import BulkBar from './BulkBar.vue'
import { MOBILE_QUERY, useMediaQuery } from '../_internal/media'
import { readStored, writeStored } from '../_internal/storage'
import { useI18n } from '@/i18n'

const props = withDefaults(
  defineProps<{
    columns: TableColumn<TableRow>[]
    data: TableRow[]
    /** 行唯一键：字段名或函数（默认取 row.id / row.key）。 */
    rowKey?: string | ((row: TableRow) => string | number)
    /** 偏好（行高、显示列）存储键；同一表格请固定不变。 */
    tableId?: string
    loading?: boolean
    selectable?: boolean
    remote?: boolean
    pagination?: boolean
    page?: number
    pageSize?: number
    itemCount?: number
    pageCount?: number
    pageSizes?: number[]
    emptyText?: string
    emptyDescription?: string
    maxHeight?: string
    /** 桌面表格虚拟滚动（1000+ 行不卡顿）；手机卡片模式不走它。 */
    virtualScroll?: boolean
    clickableRows?: boolean
    defaultDensity?: 'medium' | 'compact'
  }>(),
  {
    rowKey: undefined,
    tableId: 'default',
    loading: false,
    selectable: false,
    remote: false,
    pagination: true,
    page: 1,
    pageSize: 20,
    itemCount: undefined,
    pageCount: undefined,
    pageSizes: () => [20, 50, 100],
    emptyText: undefined,
    emptyDescription: undefined,
    maxHeight: 'calc(100vh - 220px)',
    virtualScroll: true,
    clickableRows: false,
    defaultDensity: 'medium',
  },
)

const emit = defineEmits<{
  (event: 'update:page', page: number): void
  (event: 'update:pageSize', pageSize: number): void
  (event: 'update:sorter', sorter: SorterPayload | null): void
  (event: 'update:checkedKeys', keys: DataTableRowKey[]): void
  (event: 'rowClick', row: TableRow): void
}>()

const isMobile = useMediaQuery(MOBILE_QUERY)
const { t, localeTag } = useI18n()

/** 空态说明：页面可覆盖，默认跟随语言。 */
const emptyDescriptionText = computed(() => props.emptyDescription ?? t('common.empty'))

/* ---------- 偏好：行高与显示列 ---------- */
const storageBase = computed(() => 'mh.table.' + (props.tableId || 'default'))
const densityKey = computed(() => storageBase.value + '.density')
const columnsKey = computed(() => storageBase.value + '.columns')

const density = ref<'medium' | 'compact'>(readStored(densityKey.value, props.defaultDensity))
const hiddenKeys = ref<string[]>(readStored<string[]>(columnsKey.value, []))

watch(density, (value) => writeStored(densityKey.value, value))
watch(hiddenKeys, (value) => writeStored(columnsKey.value, value))
watch(storageBase, () => {
  density.value = readStored(densityKey.value, props.defaultDensity)
  hiddenKeys.value = readStored<string[]>(columnsKey.value, [])
})

function toggleDensity(): void {
  density.value = density.value === 'compact' ? 'medium' : 'compact'
}

const toggleableColumns = computed(() => props.columns.filter((col) => col.hideable !== false))

function isColumnVisible(key: unknown): boolean {
  return !hiddenKeys.value.includes(String(key))
}

function toggleColumn(key: unknown, visible: boolean): void {
  const value = String(key)
  const next = new Set(hiddenKeys.value)
  if (visible) next.delete(value)
  else next.add(value)
  hiddenKeys.value = Array.from(next)
}

/* ---------- 行键 / 选择 ---------- */
const rowKeyFn = computed<(row: TableRow) => string>(() => {
  const raw = props.rowKey
  if (typeof raw === 'function') return (row) => String(raw(row))
  if (typeof raw === 'string') return (row) => String(row[raw] ?? '')
  return (row) => String(row?.id ?? row?.key ?? '')
})

function rowKeyOf(row: TableRow, index: number): string {
  const key = rowKeyFn.value(row)
  return key === '' || key === 'undefined' ? 'row-' + index : key
}

const checkedKeys = ref<DataTableRowKey[]>([])

function onCheckedKeys(keys: DataTableRowKey[]): void {
  checkedKeys.value = keys
  emit('update:checkedKeys', keys)
}

function clearSelection(): void {
  onCheckedKeys([])
}

function toggleRow(row: TableRow, index: number, checked: boolean): void {
  const key = rowKeyOf(row, index)
  const next = new Set(checkedKeys.value.map((item) => String(item)))
  if (checked) next.add(key)
  else next.delete(key)
  onCheckedKeys(Array.from(next))
}

/* ---------- 列定义 ---------- */
const visibleColumns = computed(() =>
  props.columns.filter((col) => col.hideable === false || isColumnVisible(col.key)),
)

const naiveColumns = computed(() => {
  const list: Array<Record<string, unknown>> = []
  if (props.selectable) list.push({ type: 'selection', fixed: 'left' })
  for (const col of visibleColumns.value) {
    const next: Record<string, unknown> = { ...col }
    next.align = col.align ?? (col.numeric ? 'right' : 'left')
    if (col.numeric) {
      next.className = [col.className, 'mh-td--num'].filter(Boolean).join(' ')
    }
    if (col.sortable && !col.sorter) next.sorter = true
    if (col.mobile === 'actions' && !next.fixed) next.fixed = 'right'
    delete next.mobile
    delete next.numeric
    delete next.hideable
    delete next.sortable
    list.push(next)
  }
  return list as unknown as DataTableColumns<DataTableRowData>
})

const rowClassName = (row: TableRow, index: number): string => {
  const key = rowKeyOf(row, index)
  return checkedKeys.value.some((item) => String(item) === key) ? 'mh-row--checked' : ''
}

/* ---------- 分页 / 排序 ---------- */
const paginationConfig = computed(() => {
  if (props.pagination === false) return false
  return {
    page: props.page ?? 1,
    pageSize: props.pageSize ?? 20,
    itemCount: props.itemCount,
    pageCount: props.pageCount,
    pageSizes: props.pageSizes,
    showSizePicker: true,
    prefix: (info: { itemCount?: number }) =>
      info?.itemCount
        ? t('table.total', { count: info.itemCount.toLocaleString(localeTag.value) })
        : '',
    onChange: (page: number) => emit('update:page', page),
    onUpdatePageSize: (size: number) => {
      emit('update:pageSize', size)
      emit('update:page', 1)
    },
  }
})

function onSorterChange(state: DataTableSortState | DataTableSortState[] | null): void {
  const one = Array.isArray(state) ? state[0] : state
  if (!one) {
    emit('update:sorter', null)
    return
  }
  emit('update:sorter', {
    columnKey: String(one.columnKey),
    order: one.order as SorterPayload['order'],
  })
}

/* ---------- 手机卡片 ---------- */
const displayRows = computed(() => {
  if (props.remote || props.pagination === false) return props.data
  const page = props.page ?? 1
  const size = props.pageSize ?? 20
  return props.data.slice((page - 1) * size, page * size)
})

const mobilePlan = computed(() => {
  const plan: Record<MobileSlot, TableColumn<TableRow>[]> = {
    title: [],
    subtitle: [],
    meta: [],
    actions: [],
    hidden: [],
  }
  const hasHint = props.columns.some((col) => Boolean(col.mobile))
  props.columns.forEach((col, index) => {
    if (!visibleColumns.value.includes(col)) return
    let slot: MobileSlot
    if (col.mobile) slot = col.mobile
    else if (hasHint) slot = 'meta'
    else slot = index === 0 ? 'title' : index === 1 ? 'subtitle' : 'meta'
    plan[slot].push(col)
  })
  return plan
})

function cellContent(col: TableColumn<TableRow>, row: TableRow, index: number): VNodeChild {
  if (typeof col.render === 'function') return col.render(row, index)
  const value = col.key === null || col.key === undefined ? undefined : row[String(col.key)]
  if (value === null || value === undefined || value === '') {
    return h('span', { class: 'mh-dash' }, '—')
  }
  return String(value)
}

const CellValue = defineComponent({
  props: {
    column: { type: Object, required: true },
    row: { type: Object, required: true },
    index: { type: Number, required: true },
  },
  setup(cellProps) {
    return () =>
      cellContent(
        cellProps.column as TableColumn<TableRow>,
        cellProps.row as TableRow,
        cellProps.index as number,
      )
  },
})

function onRowClick(row: TableRow): void {
  if (!props.clickableRows) return
  emit('rowClick', row)
}

const mobilePage = computed(() => props.page ?? 1)
const mobilePageSize = computed(() => props.pageSize ?? 20)
const mobilePageCount = computed(() => props.pageCount)
const mobileItemCount = computed(() => props.itemCount ?? props.data.length)

defineExpose({ clearSelection, toggleDensity })
</script>

<template>
  <div
    class="mh-table"
    :class="{ 'mh-table--compact': density === 'compact', 'mh-table--mobile': isMobile }"
  >
    <div class="mh-table__bar" :class="{ 'is-active': loading && data.length > 0 }"><i /></div>

    <div class="mh-table__toolbar">
      <div class="mh-table__toolbar-left">
        <slot name="toolbar" />
      </div>
      <div class="mh-table__toolbar-right">
        <IconBtn
          :label="density === 'compact' ? t('table.densityStandard') : t('table.densityCompact')"
          :icon="Rows3"
          :active="density === 'compact'"
          @click="toggleDensity"
        />
        <n-popover trigger="click" placement="bottom-end" :show-arrow="false">
          <template #trigger>
            <IconBtn
              :label="t('table.columnSettings')"
              :icon="Columns3"
              :active="hiddenKeys.length > 0"
            />
          </template>
          <div class="mh-table__cols">
            <p class="mh-table__cols-title">{{ t('table.columnsTitle') }}</p>
            <n-checkbox
              v-for="col in toggleableColumns"
              :key="String(col.key)"
              class="mh-table__col-item"
              :checked="isColumnVisible(col.key)"
              :label="String(col.title ?? col.key)"
              @update:checked="(checked: boolean) => toggleColumn(col.key, checked)"
            />
          </div>
        </n-popover>
      </div>
    </div>

    <BulkBar
      v-if="selectable && checkedKeys.length"
      class="mh-table__bulk"
      :count="checkedKeys.length"
      @cancel="clearSelection"
    >
      <slot name="bulk" :clear="clearSelection" />
    </BulkBar>

    <!-- 手机：卡片列表 -->
    <template v-if="isMobile">
      <div v-if="loading && !displayRows.length" class="mh-table__loading">
        <n-spin :size="20" />
      </div>
      <div
        v-else-if="displayRows.length"
        class="mh-cards"
        :style="{ maxHeight }"
        tabindex="0"
        :aria-label="t('table.dataCards')"
      >
        <div
          v-for="(row, index) in displayRows"
          :key="rowKeyOf(row, index)"
          class="mh-cards__item"
          :class="{ 'is-clickable': clickableRows }"
          @click="onRowClick(row)"
        >
          <div class="mh-cards__title">
            <n-checkbox
              v-if="selectable"
              class="mh-cards__check"
              :checked="checkedKeys.some((item) => String(item) === rowKeyOf(row, index))"
              :aria-label="t('table.selectRow', { index: index + 1 })"
              @update:checked="(checked: boolean) => toggleRow(row, index, checked)"
              @click.stop
            />
            <div class="mh-cards__title-cells">
              <CellValue
                v-for="col in mobilePlan.title"
                :key="String(col.key)"
                :column="col"
                :row="row"
                :index="index"
              />
            </div>
          </div>
          <div v-if="mobilePlan.subtitle.length" class="mh-cards__subtitle">
            <template v-for="(col, slotIndex) in mobilePlan.subtitle" :key="String(col.key)">
              <span v-if="slotIndex" class="mh-cards__sep">·</span>
              <CellValue :column="col" :row="row" :index="index" />
            </template>
          </div>
          <div v-if="mobilePlan.meta.length" class="mh-cards__meta">
            <div
              v-for="col in mobilePlan.meta.slice(0, 4)"
              :key="String(col.key)"
              class="mh-cards__meta-item"
            >
              <span class="mh-cards__meta-label">{{ col.title }}</span>
              <span class="mh-cards__meta-value tnum">
                <CellValue :column="col" :row="row" :index="index" />
              </span>
            </div>
          </div>
          <div v-if="mobilePlan.actions.length" class="mh-cards__actions">
            <CellValue
              v-for="col in mobilePlan.actions"
              :key="String(col.key)"
              :column="col"
              :row="row"
              :index="index"
            />
          </div>
        </div>
      </div>
      <div v-else class="mh-cards__empty">
        <slot name="empty">
          <Empty :description="emptyDescriptionText" compact />
        </slot>
      </div>
      <div v-if="pagination !== false" class="mh-table__pager">
        <n-pagination
          :page="mobilePage"
          :page-size="mobilePageSize"
          :item-count="mobileItemCount"
          :page-count="mobilePageCount"
          :page-sizes="pageSizes"
          show-size-picker
          @update:page="(page: number) => emit('update:page', page)"
          @update:page-size="(size: number) => emit('update:pageSize', size)"
        />
      </div>
    </template>

    <!-- 桌面：表格 -->
    <div v-else class="mh-table__body" tabindex="0" :aria-label="t('table.dataTable')">
      <div v-if="loading && !data.length" class="mh-table__loading">
        <n-spin :size="20" />
      </div>
      <n-data-table
        v-else
        :columns="naiveColumns"
        :data="data"
        :row-key="rowKeyFn"
        :pagination="paginationConfig"
        :remote="remote"
        :size="density === 'compact' ? 'small' : 'medium'"
        :row-class-name="rowClassName"
        :scroll-x="undefined"
        :virtual-scroll="virtualScroll"
        :max-height="virtualScroll ? maxHeight : undefined"
        @update:sorter="onSorterChange"
        @update:checked-row-keys="onCheckedKeys"
      >
        <template #empty>
          <slot name="empty">
            <Empty :description="emptyDescriptionText" compact />
          </slot>
        </template>
      </n-data-table>
    </div>
  </div>
</template>

<style scoped>
.mh-table {
  position: relative;
  display: grid;
  gap: var(--sp-2);
  min-width: 0;
}

/* 顶部 2px 进度条：刷新时保留旧数据，只在这里提示 */
.mh-table__bar {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 2px;
  overflow: hidden;
  opacity: 0;
  transition: opacity var(--t-fast) var(--ease);
  pointer-events: none;
  z-index: 2;
}

.mh-table__bar.is-active {
  opacity: 1;
}

.mh-table__bar i {
  display: block;
  height: 100%;
  width: 40%;
  background: var(--brand);
  animation: mh-table-bar 1.1s var(--ease) infinite;
}

@keyframes mh-table-bar {
  0% {
    transform: translateX(-100%);
  }
  100% {
    transform: translateX(260%);
  }
}

@media (prefers-reduced-motion: reduce) {
  .mh-table__bar i {
    animation: none;
    width: 100%;
  }
}

.mh-table__toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-2);
  min-height: 32px;
}

.mh-table__toolbar-left {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
  min-width: 0;
}

.mh-table__toolbar-right {
  display: flex;
  align-items: center;
  gap: var(--sp-1);
  flex: none;
}

.mh-table__cols {
  display: grid;
  gap: var(--sp-1);
  min-width: 160px;
  max-height: 280px;
  overflow: auto;
}

.mh-table__cols-title {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  margin-bottom: var(--sp-1);
}

.mh-table__col-item {
  font-size: var(--fs-small);
}

.mh-table__body {
  position: relative;
  min-width: 0;
}

.mh-table__body :deep(.n-data-table-th) {
  background: var(--surface-2);
  font-size: var(--fs-small);
  font-weight: 500;
  color: var(--fg-muted);
}

.mh-table__body :deep(.n-data-table-td) {
  height: 44px;
}

.mh-table--compact :deep(.n-data-table-td) {
  height: 36px;
}

.mh-table__body :deep(.mh-row--checked .n-data-table-td) {
  background: var(--brand-soft);
}

.mh-table__body :deep(.n-data-table-tr:hover .n-data-table-td) {
  background: var(--surface-hover);
}

.mh-table__body :deep(.mh-td--num),
.mh-table--compact :deep(.mh-td--num) {
  font-variant-numeric: tabular-nums;
}

.mh-table__loading {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--sp-8) 0;
}

/* ---------- 手机卡片 ---------- */
.mh-cards {
  display: grid;
  gap: var(--sp-2);
  overflow: auto;
  -webkit-overflow-scrolling: touch;
}

.mh-cards__item {
  display: grid;
  gap: var(--sp-1);
  padding: var(--sp-3);
  border: 1px solid var(--border);
  border-radius: var(--r-ctl);
  background: var(--surface);
}

.mh-cards__item.is-clickable {
  cursor: pointer;
}

.mh-cards__item.is-clickable:hover {
  border-color: var(--border-strong);
}

.mh-cards__title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-2);
  min-width: 0;
}

.mh-cards__check {
  flex: none;
}

.mh-cards__title-cells {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-2);
  flex: 1;
  min-width: 0;
  font-size: var(--fs-body);
  color: var(--fg);
  font-weight: 500;
}

.mh-cards__title-cells > * {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mh-cards__subtitle {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--sp-1);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.mh-cards__sep {
  color: var(--fg-faint);
}

.mh-cards__meta {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--sp-1) var(--sp-3);
  margin-top: var(--sp-1);
}

.mh-cards__meta-item {
  display: flex;
  align-items: baseline;
  gap: var(--sp-1);
  min-width: 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
}

.mh-cards__meta-label {
  color: var(--fg-muted);
  flex: none;
}

.mh-cards__meta-value {
  color: var(--fg);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mh-cards__actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--sp-2);
  margin-top: var(--sp-1);
}

.mh-cards__empty {
  padding: var(--sp-6) 0;
}

.mh-table__pager {
  display: flex;
  justify-content: flex-end;
  padding-top: var(--sp-1);
}

.mh-dash {
  color: var(--fg-faint);
}
</style>
