<script setup lang="ts">
/**
 * 账号池 · 表格（AccountsPage 拆分，10-visual-refresh §1.5 / §5）。
 *
 * 只负责「怎么画」：列定义、单元格、批量条、空状态；数据、筛选、排序状态都由页面传进来，
 * 行操作 / 批量操作以事件抛回页面处理（页面统一做确认、请求、刷新）。
 *
 * 视觉要点（第 1 轮审查 P1-5 / P1-2）：
 *   · 账号列 ≥ 280px，主行是名称（500 字重），副行 12px：**只有 UID 用等宽**，域名用 sans 灰字，
 *     不再把「令牌 N 天」塞进副行（有令牌的平台单独一列），保证一行不折三行；
 *   · 状态列「圆点 + 文字」，已禁用是灰色，冷却 / 熔断是琥珀，可用是绿；
 *   · 余额 Meter 只有余额紧张（<20% / <5%）才着色，其余中性灰。
 */
import { computed, h, ref, type VNodeChild } from 'vue'
import {
  Btn,
  DataTable,
  Empty,
  Meter,
  RowActions,
  StatusDot,
  type RowAction,
  type SorterPayload,
  type TableColumn,
  type TableRow,
} from '@/components'
import { Inbox } from 'lucide-vue-next'
import { FMT_EMPTY, fmtCredit, fmtFullTime, fmtInt } from '@/utils/format'
import { useI18n } from '@/i18n'
import type { AccountRow } from '@/api/accounts'
import type { BulkAction } from './types'

const props = withDefaults(
  defineProps<{
    rows: AccountRow[]
    total: number
    page: number
    pageSize: number
    loading?: boolean
    /** 第 5 轮：二次进入的静默刷新（DataTable `refreshing`：保留旧数据，只在工具栏转小圈）。 */
    refreshing?: boolean
    /** useTableQuery 的排序状态：id / name / credits / last_used。 */
    sortBy: string
    sortOrder: 'asc' | 'desc' | ''
    /** 任一账号 token_supported 时追加「令牌」列。 */
    showToken?: boolean
    hasFilters?: boolean
    bulkBusy?: boolean
  }>(),
  { loading: false, refreshing: false, showToken: false, hasFilters: false, bulkBusy: false },
)

const emit = defineEmits<{
  (event: 'update:page', page: number): void
  (event: 'update:pageSize', size: number): void
  (event: 'sorter', sorter: SorterPayload | null): void
  (event: 'action', row: AccountRow, key: string): void
  (event: 'bulk', action: BulkAction, ids: number[]): void
  (event: 'exportSelected', ids: number[]): void
  (event: 'resetFilters'): void
  (event: 'add'): void
  (event: 'reload'): void
}>()

const { t } = useI18n()

/* ------------------------------ 排序 ------------------------------ */

const SORT_STATE_KEY: Record<string, string> = {
  name: 'name',
  balance: 'credits',
  lastUsed: 'last_used',
}

function sortOrderOf(stateKey: string): 'ascend' | 'descend' | false {
  if (props.sortBy !== stateKey) return false
  if (props.sortOrder === 'asc') return 'ascend'
  if (props.sortOrder === 'desc') return 'descend'
  return false
}

/* ------------------------------ 单元格 ------------------------------ */

function renderAccount(row: TableRow): VNodeChild {
  const item = row as AccountRow
  const sub: VNodeChild[] = []
  sub.push(h('span', { class: 'acc-cell__uid mono', title: item.uid || '' }, item.uidMasked || FMT_EMPTY))
  if (item.domain) {
    sub.push(h('span', { class: 'acc-cell__sep', 'aria-hidden': 'true' }, '·'))
    sub.push(h('span', { class: 'acc-cell__domain', title: item.domain }, item.domain))
  }
  // 已禁用：名称与副行整体降为 muted 灰（10-visual-refresh §5 账号表）
  return h('div', { class: ['acc-cell', item.statusKey === 'disabled' ? 'is-disabled' : ''] }, [
    // 名称可点：打开详情抽屉（契约「点击行进入详情」；只做名称可点，避免误触勾选框与行操作）
    h(
      'button',
      {
        type: 'button',
        class: 'acc-cell__name',
        title: item.name,
        onClick: () => emit('action', item, 'detail'),
      },
      item.name,
    ),
    h('span', { class: 'acc-cell__sub' }, sub),
  ])
}

function renderStatus(row: TableRow): VNodeChild {
  const item = row as AccountRow
  return h('span', { class: 'acc-status', title: item.statusTitle }, [
    h(StatusDot, { tone: item.statusTone, label: item.statusLabel }),
  ])
}

function renderBalance(row: TableRow): VNodeChild {
  const item = row as AccountRow
  return h('div', { class: 'acc-balance', title: item.balanceTitle }, [
    h('span', { class: 'acc-balance__text tnum' }, [
      h('span', { class: item.statusKey === 'disabled' ? 'acc-muted-num' : 'acc-strong' }, fmtInt(item.balanceRemain)),
      h('span', { class: 'acc-balance__total' }, ' / ' + fmtInt(item.balanceTotal)),
    ]),
    h(Meter, {
      value: item.balancePct,
      tone: item.balanceTone,
      height: 4,
      ariaLabel: t('page.accounts.balanceAria', { name: item.name }),
    }),
  ])
}

function renderToday(row: TableRow): VNodeChild {
  const item = row as AccountRow
  const zero = item.todayCredits <= 0 && item.todayRequests <= 0
  if (zero) return h('span', { class: 'acc-faint tnum' }, FMT_EMPTY)
  return h(
    'span',
    { class: 'tnum', title: t('page.accounts.todayTitle', { count: fmtInt(item.todayRequests) }) },
    fmtCredit(item.todayCredits),
  )
}

function renderExpire(row: TableRow): VNodeChild {
  const item = row as AccountRow
  if (!item.expireAt) return h('span', { class: 'acc-faint' }, FMT_EMPTY)
  return h(
    'span',
    { class: 'acc-expire is-' + item.expireTone, title: fmtFullTime(item.expireAt) },
    item.expireText,
  )
}

function renderLastUsed(row: TableRow): VNodeChild {
  const item = row as AccountRow
  if (!item.raw.last_used_at) return h('span', { class: 'acc-faint' }, FMT_EMPTY)
  return h('span', { class: 'acc-muted', title: fmtFullTime(item.raw.last_used_at) }, item.lastUsedText)
}

function renderNote(row: TableRow): VNodeChild {
  const item = row as AccountRow
  if (!item.boundKeyName) return h('span', { class: 'acc-faint' }, FMT_EMPTY)
  return h('span', { class: 'acc-note', title: item.note }, item.boundKeyName)
}

function renderToken(row: TableRow): VNodeChild {
  const item = row as AccountRow
  if (!item.raw.token_supported) return h('span', { class: 'acc-faint' }, FMT_EMPTY)
  const expired = item.raw.token_state === 'expired' || item.raw.renewal_state === 'expired'
  const expiring = item.raw.token_state === 'expiring' || item.raw.renewal_state === 'expiring'
  const days = item.raw.token_days_left
  const text =
    days === null || days === undefined
      ? item.raw.token_state || t('page.accounts.tokenUnknown')
      : t('page.accounts.tokenDays', { days })
  const title = t('page.accounts.tokenTitle', {
    text,
    renewal:
      item.raw.renewal_days_left === null || item.raw.renewal_days_left === undefined
        ? t('page.accounts.tokenUnknown')
        : t('page.accounts.tokenDays', { days: item.raw.renewal_days_left }),
  })
  return h(
    'span',
    { class: expired ? 'acc-expire is-danger' : expiring ? 'acc-expire is-warn' : 'acc-muted', title },
    text,
  )
}

function actionList(item: AccountRow): RowAction[] {
  const actions: RowAction[] = [
    { key: 'refresh', label: t('page.accounts.actRefresh') },
    { key: 'check', label: t('page.accounts.actCheck') },
    { key: 'edit', label: t('page.accounts.actEdit') },
    {
      key: item.status === 'active' ? 'disable' : 'enable',
      label: item.status === 'active' ? t('page.accounts.actDisable') : t('page.accounts.actEnable'),
    },
    { key: 'detail', label: t('page.accounts.actDetail') },
  ]
  if (item.features.checkin) actions.push({ key: 'checkin', label: t('page.accounts.actCheckin') })
  actions.push({ key: 'delete', label: t('common.delete'), danger: true })
  return actions
}

function renderActions(row: TableRow): VNodeChild {
  const item = row as AccountRow
  return h(RowActions, {
    actions: actionList(item),
    max: 2,
    onSelect: (key: string) => emit('action', item, key),
  })
}

const columns = computed<TableColumn[]>(() => {
  const list: TableColumn[] = [
    {
      key: 'name',
      title: t('page.accounts.colAccount'),
      mobile: 'title',
      minWidth: 280,
      sortable: true,
      sortOrder: sortOrderOf(SORT_STATE_KEY.name),
      hideable: false,
      render: renderAccount,
    },
    {
      key: 'status',
      title: t('page.accounts.colStatus'),
      mobile: 'subtitle',
      width: 104,
      render: renderStatus,
    },
    {
      key: 'balance',
      title: t('page.accounts.colBalance'),
      mobile: 'meta',
      width: 180,
      sortable: true,
      sortOrder: sortOrderOf(SORT_STATE_KEY.balance),
      render: renderBalance,
    },
    {
      key: 'today',
      title: t('page.accounts.colToday'),
      mobile: 'meta',
      width: 104,
      numeric: true,
      render: renderToday,
    },
    {
      key: 'expire',
      title: t('page.accounts.colExpire'),
      mobile: 'meta',
      width: 128,
      render: renderExpire,
    },
  ]
  if (props.showToken) {
    list.push({
      key: 'token',
      title: t('page.accounts.colToken'),
      mobile: 'meta',
      width: 96,
      render: renderToken,
    })
  }
  list.push(
    {
      key: 'lastUsed',
      title: t('page.accounts.colLastUsed'),
      mobile: 'meta',
      width: 112,
      sortable: true,
      sortOrder: sortOrderOf(SORT_STATE_KEY.lastUsed),
      render: renderLastUsed,
    },
    {
      key: 'note',
      title: t('page.accounts.colNote'),
      mobile: 'hidden',
      width: 140,
      render: renderNote,
    },
    {
      key: 'actions',
      title: t('page.accounts.colActions'),
      mobile: 'actions',
      width: 150,
      hideable: false,
      fixed: 'right',
      render: renderActions,
    },
  )
  return list
})

/* ------------------------------ 选择 / 批量 ------------------------------ */

const tableRef = ref<InstanceType<typeof DataTable> | null>(null)
const checkedKeys = ref<string[]>([])

function onCheckedKeys(keys: Array<string | number>): void {
  checkedKeys.value = keys.map(String)
}

const selectedIds = computed(() => {
  const keys = new Set(checkedKeys.value)
  return props.rows.filter((row) => keys.has(row.key)).map((row) => row.id)
})

/** 选中项可能跨页：用 key 直接还原 id（key 即 String(id)）。 */
function selectedAllIds(): number[] {
  const ids = checkedKeys.value.map((key) => Number(key)).filter((id) => Number.isFinite(id) && id > 0)
  return ids.length ? ids : selectedIds.value
}

function clearSelection(): void {
  checkedKeys.value = []
  tableRef.value?.clearSelection()
}

function bulk(action: BulkAction): void {
  const ids = selectedAllIds()
  if (!ids.length) return
  emit('bulk', action, ids.sort((a, b) => a - b))
}

function exportSelected(): void {
  const ids = selectedAllIds()
  if (!ids.length) return
  emit('exportSelected', ids.sort((a, b) => b - a))
}

defineExpose({ clearSelection })
</script>

<template>
  <DataTable
    ref="tableRef"
    class="acc-table"
    table-id="accounts"
    :columns="columns"
    :data="rows"
    row-key="key"
    remote
    selectable
    :loading="loading"
    :refreshing="refreshing"
    :page="page"
    :page-size="pageSize"
    :item-count="total"
    :page-sizes="[50, 100, 200]"
    :empty-description="t('page.accounts.tableEmpty')"
    @update:page="(value: number) => emit('update:page', value)"
    @update:page-size="(value: number) => emit('update:pageSize', value)"
    @update:sorter="(sorter: SorterPayload | null) => emit('sorter', sorter)"
    @update:checked-keys="onCheckedKeys"
  >
    <template #toolbar>
      <slot name="toolbar" />
    </template>

    <template #bulk>
      <Btn variant="quiet" size="sm" :disabled="bulkBusy" @click="bulk('enable')">
        {{ t('page.accounts.bulkEnable') }}
      </Btn>
      <Btn variant="quiet" size="sm" :disabled="bulkBusy" @click="bulk('disable')">
        {{ t('page.accounts.bulkDisable') }}
      </Btn>
      <Btn variant="quiet" size="sm" :disabled="bulkBusy" @click="bulk('refresh')">
        {{ t('page.accounts.bulkRefresh') }}
      </Btn>
      <Btn variant="quiet" size="sm" :disabled="bulkBusy" @click="bulk('check')">
        {{ t('page.accounts.bulkCheck') }}
      </Btn>
      <Btn variant="quiet" size="sm" :disabled="bulkBusy" @click="exportSelected">
        {{ t('page.accounts.exportSelection') }}
      </Btn>
      <Btn variant="danger" size="sm" :disabled="bulkBusy" @click="bulk('delete')">
        {{ t('common.delete') }}
      </Btn>
    </template>

    <template #empty>
      <Empty
        :icon="Inbox"
        :description="hasFilters ? t('page.accounts.emptyFiltered') : t('page.accounts.emptyPlatform')"
      >
        <Btn v-if="hasFilters" variant="default" size="sm" @click="emit('resetFilters')">
          {{ t('page.accounts.clearFilters') }}
        </Btn>
        <template v-else>
          <Btn variant="default" size="sm" @click="emit('add')">
            {{ t('page.accounts.importAccounts') }}
          </Btn>
          <Btn variant="quiet" size="sm" @click="emit('reload')">{{ t('common.refresh') }}</Btn>
        </template>
      </Empty>
    </template>
  </DataTable>
</template>

<style scoped>
.acc-table :deep(.acc-cell) {
  display: grid;
  gap: 1px;
  min-width: 0;
}

.acc-table :deep(.acc-cell__name) {
  display: block;
  max-width: 100%;
  padding: 0;
  border: 0;
  background: transparent;
  font: inherit;
  text-align: left;
  cursor: pointer;
  font-weight: 500;
  color: var(--fg);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.acc-table :deep(.acc-cell.is-disabled .acc-cell__name),
.acc-table :deep(.acc-cell.is-disabled .acc-cell__uid) {
  color: var(--fg-muted);
}

/*
 * 已禁用 = 整行灰（11 / 第 3 轮任务）：F1 的 DataTable 还没有 rowClass prop，
 * 先用 :has() 从账号格的 is-disabled 反推整行（Chrome 105+ / Safari 15.4+ / Firefox 121+；
 * 不支持的浏览器退化为只有账号格和余额灰，与上一轮一致）。F1 加 rowClass 后改成行类即可。
 * 操作列保持原色，禁用账号仍要能一眼找到「启用」。
 */
.acc-table :deep(tr:has(.acc-cell.is-disabled) > td:not(:last-child)),
.acc-table :deep(.mh-cards__item:has(.acc-cell.is-disabled)) {
  color: var(--fg-muted);
}

.acc-table :deep(tr:has(.acc-cell.is-disabled) .acc-strong),
.acc-table :deep(tr:has(.acc-cell.is-disabled) .acc-expire),
.acc-table :deep(tr:has(.acc-cell.is-disabled) .acc-note),
.acc-table :deep(tr:has(.acc-cell.is-disabled) .acc-cell__uid) {
  color: var(--fg-muted);
}

.acc-table :deep(tr:has(.acc-cell.is-disabled) .mh-meter),
.acc-table :deep(tr:has(.acc-cell.is-disabled) .acc-status) {
  filter: grayscale(1);
  opacity: 0.6;
}

.acc-table :deep(.acc-cell__name:hover) {
  color: var(--brand);
}

.acc-table :deep(.acc-cell__name:focus-visible) {
  outline: none;
  border-radius: var(--r-chip);
  box-shadow: 0 0 0 3px var(--ring);
}

/* 副行一行到底：UID 等宽、域名 sans 灰字，超长省略（不折行） */
.acc-table :deep(.acc-cell__sub) {
  display: flex;
  align-items: baseline;
  gap: var(--sp-1);
  min-width: 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  white-space: nowrap;
  overflow: hidden;
}

.acc-table :deep(.acc-cell__uid) {
  flex: none;
  color: var(--fg-2);
}

.acc-table :deep(.acc-cell__sep) {
  flex: none;
  color: var(--fg-faint);
}

.acc-table :deep(.acc-cell__domain) {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.acc-table :deep(.acc-status) {
  display: inline-flex;
  white-space: nowrap;
}

.acc-table :deep(.acc-balance) {
  display: grid;
  gap: 4px;
  min-width: 0;
}

.acc-table :deep(.acc-balance__text) {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
}

.acc-table :deep(.acc-balance__total) {
  color: var(--fg-muted);
}

.acc-table :deep(.acc-strong) {
  font-weight: 500;
  color: var(--fg);
}

.acc-table :deep(.acc-muted-num) {
  color: var(--fg-muted);
}

.acc-table :deep(.acc-faint) {
  color: var(--fg-faint);
}

.acc-table :deep(.acc-muted) {
  color: var(--fg-muted);
  font-size: var(--fs-small);
}

.acc-table :deep(.acc-note) {
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: middle;
  font-size: var(--fs-small);
  color: var(--fg-2);
}

.acc-table :deep(.acc-expire) {
  font-size: var(--fs-small);
  color: var(--fg-2);
  white-space: nowrap;
}

.acc-table :deep(.acc-expire.is-warn) {
  color: var(--warn);
}

.acc-table :deep(.acc-expire.is-danger) {
  color: var(--danger);
}

.acc-table :deep(.acc-expire.is-neutral) {
  color: var(--fg-2);
}
</style>
