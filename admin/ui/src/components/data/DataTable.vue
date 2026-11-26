<script setup lang="ts">
/**
 * 统一表格封装（04-components §2 DataTable）。
 * - 基于 NDataTable：表头 --surface-2、数值列右对齐、行高 44 / 紧凑 36（Rows3 切换，按表格 id 存 localStorage）
 * - 排序 / 多选 / 远程分页 20·50·100 / 列显示设置 / 空状态 / 加载时保留旧数据 + 顶部 2px 进度条
 * - <768px（10-visual-refresh §1.5）按列的 mobile 标注渲染卡片列表，容器内滚动
 * - 视觉层（§1.5）：表头 --surface-2 12px/500；行高 --row-h / --row-h-compact；悬停 --surface-hover，
 *   选中 --brand-soft；首列 500 字重 + --fg；数字列右对齐 tabular-nums；
 *   行操作（mobile: 'actions' 或 key 为 actions 的列）桌面端悬停 / 键盘聚焦时才显示，触屏一直显示；
 *   工具栏一行：左 toolbar 插槽（搜索、筛选 chip），右 summary「共 N 条」+ toolbar-right 插槽 + 列设置 / 刷新。
 * - 第 3 轮（11-polish-motion §2/§3）：
 *   · rowClass：按行加 class（例如禁用行变灰、实时新行 'mh-row-flash'）；
 *   · virtualScroll 默认 'auto'：本页实际渲染 >200 行才开虚拟滚动（小表行高可变、jsdom 里也能渲染行）；
 *   · 横向滚动时首列（有宽度时）吸附左侧、操作列吸附右侧（stickyFirst=false 可关）；
 *   · 键盘：表格获得焦点后 ↑/↓ 在行间移动，Home/End 到首尾，Enter 触发 row-click（clickableRows 时）；
 *     桌面端点击整行也触发 row-click（点在按钮、链接、输入框、勾选框上时不触发）；
 *   · 排序列不再有灰底（原来各页自己写 --n-merged-td-color-sorting 覆盖，现在统一）。
 * - 第 4 轮：
 *   · refreshing：静默刷新 —— 保留旧数据，只在工具栏右侧（表头右上角）转 14px 小圈（>300ms 才出现），
 *     不出顶部进度条、不出骨架；默认 false，旧的 loading 行为不变；
 *   · 排序 / 筛选 / 数据刷新后保留表格内部滚动位置（11-polish-motion §1）；翻页、改每页条数仍回到顶部。
 */
import { computed, defineComponent, h, nextTick, onBeforeUnmount, ref, watch, type HTMLAttributes, type VNodeChild } from 'vue'
import { NCheckbox, NDataTable, NPagination, NPopover, NSpin } from 'naive-ui'
import type {
  DataTableColumns,
  DataTableRowData,
  DataTableRowKey,
  DataTableSortState,
} from 'naive-ui'
import type { MobileSlot, SorterPayload, TableColumn, TableRow } from './table'
import { Columns3, RotateCw, Rows3 } from 'lucide-vue-next'
import IconBtn from '../base/IconBtn.vue'
import Empty from '../base/Empty.vue'
import type { EmptyIllustrationKind } from '../base/empty'
import BulkBar from './BulkBar.vue'
import { MOBILE_QUERY, TABLE_CARD_QUERY, useMediaQuery } from '../_internal/media'
import { readStored, writeStored } from '../_internal/storage'
import { useI18n } from '@/i18n'
import { useDelayedFlag } from '@/composables/useDelayedFlag'

const props = withDefaults(
  defineProps<{
    columns: TableColumn<TableRow>[]
    data: TableRow[]
    /** 行唯一键：字段名或函数（默认取 row.id / row.key）。 */
    rowKey?: string | ((row: TableRow) => string | number)
    /** 偏好（行高、显示列）存储键；同一表格请固定不变。 */
    tableId?: string
    loading?: boolean
    /**
     * 静默刷新中（第 4 轮）：保留旧数据，只在工具栏右侧转小圈（>300ms 才出现），
     * 不出进度条、不出骨架。与 loading 同时为 true 时按 loading 处理。默认 false。
     */
    refreshing?: boolean
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
    /** 空状态插画（第 5 轮，可选）：empty / search / error / offline；不传 = 旧的图标圆。 */
    emptyIllustration?: EmptyIllustrationKind
    maxHeight?: string
    /**
     * 桌面表格虚拟滚动；手机卡片模式不走它。
     * 'auto'（默认）= 本页实际渲染的行数 >200 才开；true / false 强制开关。
     */
    virtualScroll?: boolean | 'auto'
    clickableRows?: boolean
    /** 按行加 class：字符串或 (row, index) => class。 */
    rowClass?: string | ((row: TableRow, index: number) => string | undefined)
    /** 横向滚动时首列吸附左侧（首列需要声明 width / minWidth），默认开。 */
    stickyFirst?: boolean
    defaultDensity?: 'medium' | 'compact'
    /** 工具栏右侧的统计文字，例如「共 862 条 · 错误 12」。 */
    summary?: string
    /** 工具栏右侧显示「刷新」图标按钮（点击 emit refresh）。 */
    refreshable?: boolean
    /** 首列强调（500 字重 + --fg），默认开。 */
    emphasizeFirst?: boolean
  }>(),
  {
    rowKey: undefined,
    tableId: 'default',
    loading: false,
    refreshing: false,
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
    emptyIllustration: undefined,
    maxHeight: 'calc(100vh - 220px)',
    virtualScroll: 'auto',
    clickableRows: false,
    rowClass: undefined,
    stickyFirst: true,
    defaultDensity: 'medium',
    summary: undefined,
    refreshable: false,
    emphasizeFirst: true,
  },
)

const emit = defineEmits<{
  (event: 'update:page', page: number): void
  (event: 'update:pageSize', pageSize: number): void
  (event: 'update:sorter', sorter: SorterPayload | null): void
  (event: 'update:checkedKeys', keys: DataTableRowKey[]): void
  (event: 'rowClick', row: TableRow): void
  (event: 'refresh'): void
}>()

/**
 * 卡片列表断点：<768px（10-visual-refresh §1.5）。同时认 MOBILE_QUERY（≤640）：
 * 页面测试里用 matchMedia 桩只匹配 640 的写法继续有效。
 */
const isPhoneQuery = useMediaQuery(MOBILE_QUERY)
const isNarrowQuery = useMediaQuery(TABLE_CARD_QUERY)
const isMobile = computed(() => isPhoneQuery.value || isNarrowQuery.value)
const { t, localeTag } = useI18n()

/** 静默刷新的小转圈：只在有旧数据、且不是 loading 时显示；>300ms 才出现，快接口不闪。 */
const silentBusy = computed(() => props.refreshing && !props.loading)
const showRefreshSpin = useDelayedFlag(() => silentBusy.value, 300)

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

function isActionsColumn(col: TableColumn<TableRow>): boolean {
  return col.mobile === 'actions' || String(col.key) === 'actions'
}

const naiveColumns = computed(() => {
  const list: Array<Record<string, unknown>> = []
  if (props.selectable) list.push({ type: 'selection', fixed: 'left' })
  visibleColumns.value.forEach((col, index) => {
    const next: Record<string, unknown> = { ...col }
    next.align = col.align ?? (col.numeric ? 'right' : 'left')
    const classes = [col.className]
    if (col.numeric) classes.push('mh-td--num')
    if (index === 0 && props.emphasizeFirst && !col.numeric && !isActionsColumn(col)) classes.push('mh-td--primary')
    if (isActionsColumn(col)) classes.push('mh-td--actions')
    const className = classes.filter(Boolean).join(' ')
    if (className) next.className = className
    if (col.sortable && !col.sorter) next.sorter = true
    if (col.mobile === 'actions' && !next.fixed) next.fixed = 'right'
    // 横向滚动时：操作列吸附右侧、首列（有宽度）吸附左侧（11-polish-motion §2）
    if (scrollX.value !== undefined && !next.fixed) {
      if (isActionsColumn(col)) next.fixed = 'right'
      else if (
        index === 0 &&
        props.stickyFirst &&
        !col.numeric &&
        (Number(col.width) > 0 || Number(col.minWidth) > 0)
      ) {
        next.fixed = 'left'
      }
    }
    delete next.mobile
    delete next.numeric
    delete next.hideable
    delete next.sortable
    list.push(next)
  })
  return list as unknown as DataTableColumns<DataTableRowData>
})

/**
 * 横向滚动宽度 = 各可见列声明宽度之和（没写宽度的列按 120px 估）。
 *
 * 1024px 宽时侧栏收起、内容区只剩约 900px，而模型配置等表的列宽合计 1200px 以上：
 * 不给 scroll-x 时 NDataTable 只能硬挤列宽，`fixed: 'right'` 的操作列也不生效。
 * 给了之后：容器比它宽时表格照常铺满 100%，比它窄时表格内横向滚动、操作列固定在右侧。
 */
const scrollX = computed<number | undefined>(() => {
  const cols = visibleColumns.value
  if (!cols.some((col) => Number(col.width) > 0 || Number(col.minWidth) > 0)) return undefined
  const sum = cols.reduce((acc, col) => acc + (Number(col.width) || Number(col.minWidth) || 120), 0)
  return sum + (props.selectable ? 48 : 0)
})

function extraRowClass(row: TableRow, index: number): string {
  const raw = props.rowClass
  if (!raw) return ''
  return (typeof raw === 'function' ? raw(row, index) : raw) || ''
}

/** 已勾选 key 的 Set：每行渲染时 O(1) 查，不再对勾选数组逐个 some（500 行 × 全选 = 25 万次比较）。 */
const checkedSet = computed(() => new Set(checkedKeys.value.map((item) => String(item))))

const rowClassName = (row: TableRow, index: number): string => {
  const key = rowKeyOf(row, index)
  const checked = checkedSet.value.has(key) ? 'mh-row--checked' : ''
  return [checked, extraRowClass(row, index)].filter(Boolean).join(' ')
}

/* ---------- 虚拟滚动：'auto' = 本页实际渲染 >200 行才开 ---------- */
const VIRTUAL_THRESHOLD = 200

const renderedRowCount = computed(() => {
  if (props.remote || props.pagination === false) return props.data.length
  return Math.min(props.data.length, props.pageSize ?? 20)
})

const useVirtual = computed(() =>
  props.virtualScroll === 'auto' ? renderedRowCount.value > VIRTUAL_THRESHOLD : props.virtualScroll === true,
)

/* ---------- 键盘与整行点击 ---------- */
const bodyRef = ref<HTMLElement | null>(null)
const cardsRef = ref<HTMLElement | null>(null)
const ROW_SELECTOR = 'tr[data-mh-row]'
/** 点在这些元素里不算「点这一行」。 */
const INTERACTIVE_SELECTOR =
  'button, a, input, textarea, select, label, [role="button"], [role="checkbox"], [role="switch"], .n-checkbox, .n-switch, .n-select, [data-no-row-click]'

function bodyRows(): HTMLElement[] {
  return Array.from(bodyRef.value?.querySelectorAll<HTMLElement>(ROW_SELECTOR) ?? [])
}

function focusRow(target: HTMLElement | undefined): void {
  if (!target) return
  target.focus({ preventScroll: false })
  target.scrollIntoView?.({ block: 'nearest' })
}

function moveFocus(from: HTMLElement | null, step: number | 'first' | 'last'): void {
  const rows = bodyRows()
  if (!rows.length) return
  if (step === 'first') return focusRow(rows[0])
  if (step === 'last') return focusRow(rows[rows.length - 1])
  const index = from ? rows.indexOf(from) : -1
  const next = index < 0 ? (step > 0 ? 0 : rows.length - 1) : Math.min(rows.length - 1, Math.max(0, index + step))
  focusRow(rows[next])
}

function onRowKeydown(event: KeyboardEvent, row: TableRow): void {
  const tr = event.currentTarget as HTMLElement | null
  // 行内输入框 / 按钮自己的按键不劫持
  if (!tr || event.target !== tr) return
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault()
    moveFocus(tr, event.key === 'ArrowDown' ? 1 : -1)
  } else if (event.key === 'Home' || event.key === 'End') {
    event.preventDefault()
    moveFocus(tr, event.key === 'Home' ? 'first' : 'last')
  } else if (event.key === 'Enter' && props.clickableRows) {
    event.preventDefault()
    emit('rowClick', row)
  }
}

/**
 * 虚拟滚动下的焦点保持（第 4 轮）：键盘停在某一行后用滚轮滚走，这一行被虚拟列表回收，
 * 焦点会直接掉到 <body>，之后方向键全部失效。记住最后聚焦的行 key；行被回收时把焦点交还给
 * 表格容器（不滚动），再按 ↑/↓ 从记住的那一行（还在 DOM 里时）或视口里第一行继续。
 */
let lastFocusedKey: string | null = null

function rowByKey(key: string | null): HTMLElement | undefined {
  if (!key) return undefined
  return bodyRows().find((row) => row.dataset.mhKey === key)
}

function onBodyFocusout(event: FocusEvent): void {
  const from = event.target as HTMLElement | null
  if (!from?.matches?.(ROW_SELECTOR) || event.relatedTarget) return
  globalThis.setTimeout(() => {
    const root = bodyRef.value
    if (!root || typeof document === 'undefined') return
    const active = document.activeElement
    if (active && active !== document.body) return
    if (!from.isConnected) root.focus({ preventScroll: true })
  }, 0)
}

function firstRowInView(): HTMLElement | undefined {
  const rows = bodyRows()
  const host = scrollHost()
  if (!host) return rows[0]
  const top = host.getBoundingClientRect().top
  return rows.find((row) => row.getBoundingClientRect().bottom > top + 1) ?? rows[0]
}

/** 表格容器（Tab 聚焦到它）按 ↓ / ↑ 进入第一行 / 最后一行；虚拟滚动下从记住的行或视口第一行继续。 */
function onBodyKeydown(event: KeyboardEvent): void {
  if (event.target !== event.currentTarget) return
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault()
    const remembered = rowByKey(lastFocusedKey)
    if (remembered) return moveFocus(remembered, event.key === 'ArrowDown' ? 1 : -1)
    if (useVirtual.value && lastFocusedKey) return focusRow(firstRowInView())
    moveFocus(null, event.key === 'ArrowDown' ? 'first' : 'last')
  }
}

function onRowNativeClick(event: MouseEvent, row: TableRow): void {
  if (!props.clickableRows) return
  const target = event.target as Element | null
  const tr = event.currentTarget as Element | null
  const hit = target?.closest?.(INTERACTIVE_SELECTOR)
  if (hit && tr?.contains(hit)) return
  // 选中文字时不当成点击
  const selection = typeof window !== 'undefined' ? window.getSelection?.() : null
  if (selection && selection.type === 'Range' && String(selection).trim()) return
  emit('rowClick', row)
}

const rowProps = (row: TableRow): HTMLAttributes => {
  const key = rowKeyFn.value(row)
  const attrs: HTMLAttributes & Record<string, unknown> = {
    'data-mh-row': '',
    'data-mh-key': key,
    tabindex: -1,
    onKeydown: (event: KeyboardEvent) => onRowKeydown(event, row),
    onFocus: () => {
      lastFocusedKey = key
    },
  }
  if (props.clickableRows) {
    attrs.class = 'mh-row--clickable'
    attrs.onClick = (event: MouseEvent) => onRowNativeClick(event, row)
  }
  return attrs
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
    onChange: (page: number) => {
      markPagerChange()
      emit('update:page', page)
    },
    onUpdatePageSize: (size: number) => {
      markPagerChange()
      emit('update:pageSize', size)
      emit('update:page', 1)
    },
  }
})

function onSorterChange(state: DataTableSortState | DataTableSortState[] | null): void {
  // 本地排序：NDataTable 重排行后写回原位置；远程排序：等新 data 到来时 watch 再记、再写。
  // 本地分页翻页不会换 data，pagerChanged 会一直留着 → 之后排序不再保留滚动（第 5 轮修）。
  // 排序是用户新的一次操作，先清掉翻页标记。
  pagerChanged = false
  captureScroll()
  const one = Array.isArray(state) ? state[0] : state
  if (!one) emit('update:sorter', null)
  else {
    emit('update:sorter', {
      columnKey: String(one.columnKey),
      order: one.order as SorterPayload['order'],
    })
  }
  restoreScroll()
}

/* ---------- 排序 / 筛选 / 刷新后保留滚动位置（11-polish-motion §1） ---------- */
/**
 * NDataTable 在 data 换成新数组（远程排序、筛选、刷新）或骨架 ↔ 表格切换时会把内部滚动容器
 * 重置到顶部。这里在数据变化**之前**记下内部滚动位置，渲染完成后的下一帧再写回去；
 * 由分页器翻页 / 改每页条数引起的变化不恢复（用户预期回到顶部）。
 * 只处理表格自己的滚动容器（max-height 内滚动、虚拟列表、手机卡片列表），不动 window 滚动。
 */
interface ScrollPos {
  top: number
  left: number
}

let pendingScroll: ScrollPos | null = null
let pagerChanged = false
let restoreFrame = 0

function scrollHost(): HTMLElement | null {
  if (isMobile.value) return cardsRef.value
  const root = bodyRef.value
  if (!root) return null
  const candidates = root.querySelectorAll<HTMLElement>(
    '.n-data-table-base-table-body .v-vl, .n-data-table-base-table-body .n-scrollbar-container, .n-data-table-base-table-body',
  )
  for (const el of Array.from(candidates)) {
    if (el.scrollHeight > el.clientHeight || el.scrollWidth > el.clientWidth) return el
  }
  return null
}

function captureScroll(): void {
  if (pagerChanged) return
  const el = scrollHost()
  if (!el) return
  if (el.scrollTop > 0 || el.scrollLeft > 0) pendingScroll = { top: el.scrollTop, left: el.scrollLeft }
}

function markPagerChange(): void {
  pagerChanged = true
  pendingScroll = null
  // 本地分页翻页不换 data（watch 不会来清标记）：渲染完就清，否则下一次后台刷新 / 排序都不保留滚动。
  // 远程分页要等新一页 data 到来时由 watch 清。
  if (!props.remote) {
    void nextTick(() => {
      pagerChanged = false
    })
  }
}

function restoreScroll(): void {
  const target = pendingScroll
  if (!target) return
  if (!props.data.length) return // 骨架 / 空态期间先留着，等数据回来再写
  pendingScroll = null
  void nextTick(() => {
    if (typeof requestAnimationFrame !== 'function') return
    if (restoreFrame) cancelAnimationFrame(restoreFrame)
    // NDataTable 自己的滚动重置发生在渲染之后：下一帧再写回才不会被覆盖。
    restoreFrame = requestAnimationFrame(() => {
      restoreFrame = 0
      const el = scrollHost()
      if (!el) return
      el.scrollTop = Math.min(target.top, Math.max(0, el.scrollHeight - el.clientHeight))
      el.scrollLeft = target.left
    })
  })
}

watch(
  () => props.data,
  () => {
    // flush: 'pre'：DOM 还是旧的，记下旧位置
    if (!pendingScroll) captureScroll()
    pagerChanged = false
    restoreScroll()
  },
  { flush: 'pre' },
)

onBeforeUnmount(() => {
  if (restoreFrame && typeof cancelAnimationFrame === 'function') cancelAnimationFrame(restoreFrame)
  restoreFrame = 0
})

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
        <span
          v-if="showRefreshSpin"
          class="mh-table__refreshing"
          role="status"
          :aria-label="t('common.loading')"
          data-testid="mh-table-refreshing"
        >
          <n-spin :size="14" />
        </span>
        <span v-if="summary" class="mh-table__summary">{{ summary }}</span>
        <slot name="toolbar-right" />
        <IconBtn
          v-if="refreshable"
          :label="t('table.refresh')"
          :icon="RotateCw"
          :loading="loading || refreshing"
          @click="emit('refresh')"
        />
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
      <div v-if="loading && !displayRows.length" class="mh-table__loading" aria-busy="true">
        <span v-for="n in 4" :key="n" class="mh-skeleton mh-table__sk-card" />
      </div>
      <div
        v-else-if="displayRows.length"
        ref="cardsRef"
        class="mh-cards"
        :style="{ maxHeight }"
        tabindex="0"
        :aria-label="t('table.dataCards')"
      >
        <div
          v-for="(row, index) in displayRows"
          :key="rowKeyOf(row, index)"
          class="mh-cards__item"
          :class="[{ 'is-clickable': clickableRows }, extraRowClass(row, index)]"
          @click="onRowClick(row)"
        >
          <div class="mh-cards__title">
            <n-checkbox
              v-if="selectable"
              class="mh-cards__check"
              :checked="checkedSet.has(rowKeyOf(row, index))"
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
          <Empty :description="emptyDescriptionText" :illustration="emptyIllustration" compact />
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
    <div
      v-else
      ref="bodyRef"
      class="mh-table__body"
      :aria-busy="loading || refreshing ? 'true' : undefined"
      tabindex="0"
      :aria-label="t('table.dataTable')"
      :aria-keyshortcuts="'ArrowUp ArrowDown Home End Enter'"
      @keydown="onBodyKeydown"
      @focusout="onBodyFocusout"
    >
      <!-- 首次加载：只用一种骨架屏（10-visual-refresh §1.7），模拟表头 + 6 行；刷新时保留旧数据 + 顶部进度条 -->
      <div v-if="loading && !data.length" class="mh-table__loading" aria-busy="true">
        <span class="mh-skeleton mh-table__sk-head" />
        <span v-for="n in 6" :key="n" class="mh-skeleton mh-table__sk-row" />
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
        :row-props="rowProps"
        :scroll-x="scrollX"
        :virtual-scroll="useVirtual"
        :max-height="virtualScroll !== false ? maxHeight : undefined"
        @update:sorter="onSorterChange"
        @update:checked-row-keys="onCheckedKeys"
      >
        <template #empty>
          <slot name="empty">
            <Empty :description="emptyDescriptionText" :illustration="emptyIllustration" compact />
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
  transition: opacity var(--dur-1) var(--ease-out);
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

/* 工具栏合成一行（§1.5）：表格上方空白 ≤ 56px */
.mh-table__toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: var(--sp-2);
  min-width: 0;
  min-height: var(--ctl-h);
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
  margin-left: auto;
}

.mh-table__refreshing {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  margin-right: var(--sp-1);
  animation: mh-fade-in var(--dur-2) var(--ease-out) both;
}

.mh-table__summary {
  margin-right: var(--sp-1);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
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

.mh-table__body :deep(.n-data-table) {
  --n-border-radius: var(--r-card);
  /* 排序列不出灰底（F4 报告：原来三个页面各自覆盖，这里统一） */
  --n-merged-td-color-sorting: var(--n-merged-td-color);
  --n-merged-th-color-sorting: var(--n-merged-th-color);
}

/* 表格容器自身获得焦点（Tab 进来）：外环画在容器上 */
.mh-table__body:focus-visible {
  outline: 3px solid var(--ring);
  outline-offset: 2px;
  border-radius: var(--r-card);
}

/* 键盘移到某一行：浅底 + 左侧 2px 品牌色竖条（不改边框，行高不跳） */
.mh-table__body :deep(tr[data-mh-row]:focus) {
  outline: none;
}

.mh-table__body :deep(tr[data-mh-row]:focus-visible .n-data-table-td) {
  background: var(--surface-hover);
}

.mh-table__body :deep(tr[data-mh-row]:focus-visible .n-data-table-td:first-child) {
  box-shadow: inset 2px 0 0 var(--brand);
}

.mh-table__body :deep(.mh-row--clickable) {
  cursor: pointer;
}

.mh-table__body :deep(.n-data-table-th) {
  height: 40px;
  background: var(--surface-2);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  font-weight: 500;
  color: var(--fg-muted);
  white-space: nowrap;
}

.mh-table__body :deep(.n-data-table-td) {
  height: var(--row-h);
  border-bottom-color: var(--border-soft);
  color: var(--fg-2);
}

.mh-table--compact :deep(.n-data-table-td) {
  height: var(--row-h-compact);
}

.mh-table__body :deep(.n-data-table-tr:hover .n-data-table-td) {
  background: var(--surface-hover);
}

/* 选中行在悬停之后声明：选中优先 */
.mh-table__body :deep(.mh-row--checked .n-data-table-td),
.mh-table__body :deep(.n-data-table-tr.mh-row--checked:hover .n-data-table-td) {
  background: var(--brand-soft);
}

/* 首列（名称）：500 字重 + --fg */
.mh-table__body :deep(.n-data-table-td.mh-td--primary) {
  font-weight: 500;
  color: var(--fg);
}

.mh-table__body :deep(.mh-td--num),
.mh-table--compact :deep(.mh-td--num) {
  font-variant-numeric: tabular-nums;
  color: var(--fg);
}

/*
 * 行操作：桌面端（能悬停的精确指针）悬停或键盘聚焦到这一行时才显示；
 * 触屏一直显示。用 opacity 而不是 display，避免列宽跳动、按钮仍可被 Tab 聚焦。
 */
@media (hover: hover) and (pointer: fine) {
  .mh-table__body :deep(.n-data-table-td.mh-td--actions > *) {
    opacity: 0;
    transition: opacity var(--dur-1) var(--ease-out);
  }

  .mh-table__body :deep(.n-data-table-tr:hover .n-data-table-td.mh-td--actions > *),
  .mh-table__body :deep(.n-data-table-tr:focus-within .n-data-table-td.mh-td--actions > *),
  .mh-table__body :deep(.n-data-table-tr:focus .n-data-table-td.mh-td--actions > *),
  .mh-table__body :deep(.mh-row--checked .n-data-table-td.mh-td--actions > *) {
    opacity: 1;
  }
}

.mh-table__loading {
  display: grid;
  gap: var(--sp-2);
  padding: var(--sp-2) 0;
}

.mh-table__sk-head {
  height: 32px;
  border-radius: var(--r-ctl);
}

.mh-table__sk-row {
  height: 14px;
  margin: 11px var(--sp-3);
}

.mh-table__sk-row:nth-child(2n) {
  width: 82%;
}

.mh-table__sk-card {
  height: 84px;
  border-radius: var(--r-card);
}

/* ---------- 手机卡片 ---------- */
.mh-cards {
  display: grid;
  gap: var(--sp-2);
  min-width: 0;
  overflow: auto;
  -webkit-overflow-scrolling: touch;
}

.mh-cards__item {
  display: grid;
  gap: var(--sp-1);
  padding: var(--sp-3) var(--sp-4);
  border: 1px solid var(--border);
  border-radius: var(--r-card);
  background: var(--surface);
  box-shadow: var(--shadow-1);
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
