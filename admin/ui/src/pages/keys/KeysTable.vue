<script setup lang="ts">
import TitleTip from '@/components/base/TitleTip.vue'

/**
 * API Keys 列表（10-visual-refresh §1.5）。
 *
 * 列：名称（账号范围副标题）· Key（打码 + 复制 + 查看，单行不折）· 限额 / 已用（中性色进度条）·
 *     限制（「并发不限 · 频率随全局」）· 来源 IP（中性色 IP 徽章）· 分组 · 状态 · 操作。
 * 第 1 轮审查：Key 列太窄、复制图标掉到下一行；IP 用品牌色 chip；「不限不限频率」漏分隔符 —— 都在这里改。
 * 「到期」列去掉：后端没有 Key 有效期，整列都是「—」（常态不显示，见创建抽屉里的说明）。
 */
import { computed, h, type VNodeChild } from 'vue'
import { Copy, Eye, Plus } from 'lucide-vue-next'
import {
  Badge,
  Btn,
  DataTable,
  Empty,
  IconBtn,
  Meter,
  RowActions,
  type SorterPayload,
  type TableColumn,
  type TableRow,
} from '@/components'
import { useI18n } from '@/i18n'
import { limitKind, quotaMeterTone, type KeyRow, type KeySortKey } from '@/api/keys'

const props = defineProps<{
  rows: KeyRow[]
  total: number
  page: number
  pageSize: number
  loading: boolean
  /** 第 5 轮：二次进入的静默刷新（DataTable `refreshing`：保留旧数据，只在工具栏转小圈）。 */
  refreshing?: boolean
  sortBy: string
  sortOrder: string
  hasFilters: boolean
  emptyDescription: string
}>()

const emit = defineEmits<{
  (event: 'action', row: KeyRow, key: string): void
  (event: 'ips', row: KeyRow): void
  (event: 'sorter', sorter: SorterPayload | null): void
  (event: 'page', page: number): void
  (event: 'pageSize', size: number): void
  (event: 'create'): void
  (event: 'reset'): void
}>()

const { t } = useI18n()

function asRow(row: TableRow): KeyRow {
  return row as KeyRow
}

function sortOrderOf(key: KeySortKey): 'ascend' | 'descend' | false {
  if (props.sortBy !== key) return false
  if (props.sortOrder === 'asc') return 'ascend'
  if (props.sortOrder === 'desc') return 'descend'
  return false
}

/** 「并发不限 · 频率随全局」：两个限制各自带名字，再用 · 连接（第 1 轮审查 P0-7）。 */
function limitsText(item: KeyRow): string {
  const concurrency = limitKind(item.maxConcurrent)
  const rate = limitKind(item.ratePerMinute)
  const left =
    concurrency === 'unlimited'
      ? t('page.keys.cell.concurrencyUnlimited')
      : concurrency === 'global'
        ? t('page.keys.cell.concurrencyGlobal')
        : t('page.keys.cell.concurrencyN', { n: String(item.maxConcurrent) })
  const right =
    rate === 'unlimited'
      ? t('page.keys.cell.rateUnlimited')
      : rate === 'global'
        ? t('page.keys.cell.rateGlobal')
        : t('page.keys.cell.rateN', { n: String(item.ratePerMinute) })
  return left + ' · ' + right
}

function renderName(row: TableRow): VNodeChild {
  const item = asRow(row)
  return h('div', { class: 'kt-name' }, [
    h(TitleTip, { content: String((item.note ? item.name + ' · ' + item.note : item.name) ?? '') }, { default: () => h('span', { class: 'kt-name__main' }, item.name) }),
    h('span', { class: 'kt-name__sub' }, item.accountLabel),
  ])
}

function renderKey(row: TableRow): VNodeChild {
  const item = asRow(row)
  return h('div', { class: 'kt-key' }, [
    h(TitleTip, { content: String((item.masked) ?? '') }, { default: () => h('code', { class: 'kt-key__text' }, item.masked) }),
    h(IconBtn, {
      icon: Copy,
      label: t('page.keys.cell.copyKey'),
      onClick: (event: MouseEvent) => {
        event.stopPropagation()
        emit('action', item, 'copy')
      },
    }),
    h(IconBtn, {
      icon: Eye,
      label: t('page.keys.action.reveal'),
      onClick: (event: MouseEvent) => {
        event.stopPropagation()
        emit('action', item, 'reveal')
      },
    }),
  ])
}

function renderQuota(row: TableRow): VNodeChild {
  const item = asRow(row)
  return h('div', { class: 'kt-quota' }, [
    h('div', { class: 'kt-quota__line tnum' }, [
      h('span', { class: 'kt-quota__used' }, item.usedText),
      h('span', { class: 'kt-quota__sep' }, ' / '),
      h('span', {}, item.limitText),
      item.unlimited ? null : h('span', { class: 'kt-quota__pct' }, item.usageText),
    ]),
    item.unlimited
      ? h('span', { class: 'kt-quota__hint' }, t('page.keys.cell.unlimited'))
      : h(Meter, {
          value: item.usageRatio * 100,
          tone: quotaMeterTone(item),
          height: 4,
          ariaLabel: t('page.keys.cell.meterAria'),
        }),
  ])
}

function renderLimits(row: TableRow): VNodeChild {
  return h('span', { class: 'kt-limits' }, limitsText(asRow(row)))
}

function renderIps(row: TableRow): VNodeChild {
  const item = asRow(row)
  return h('div', { class: 'kt-ips' }, [
    h(TitleTip, { content: String((t('page.keys.cell.ipsTitle')) ?? '') }, { default: () => h(
      'button',
      {
        type: 'button',
        class: 'keys-ips__count',
        onClick: (event: MouseEvent) => {
          event.stopPropagation()
          emit('ips', item)
        },
      },
      item.ipText,
    ) }),
    item.ipBadges.length
      ? h(
          'div',
          { class: 'kt-ips__badges' },
          item.ipBadges.map((entry) =>
            h(TitleTip, { content: String((entry.text) ?? ''), key: entry.ip }, { default: () => h('span', { key: entry.ip, class: 'kt-ip' }, entry.ip) }),
          ),
        )
      : null,
  ])
}

function renderGroup(row: TableRow): VNodeChild {
  const item = asRow(row)
  return item.groupId
    ? h(TitleTip, { content: String((t('page.keys.cell.groupTitle')) ?? '') }, { default: () => h(Badge, { tone: 'neutral' }, { default: () => item.groupLabel }) })
    : h('span', { class: 'kt-muted' }, item.groupLabel)
}

function renderStatus(row: TableRow): VNodeChild {
  const item = asRow(row)
  return h(Badge, { tone: item.statusTone, dot: true }, { default: () => item.statusLabel })
}

function renderActions(row: TableRow): VNodeChild {
  const item = asRow(row)
  return h(RowActions, {
    actions: [
      { key: 'edit', label: t('common.edit') },
      { key: 'copy', label: t('page.keys.action.copy') },
      { key: 'reveal', label: t('page.keys.action.reveal') },
      { key: 'usage', label: t('page.keys.action.usage') },
      { key: 'reset', label: t('page.keys.action.reset') },
      {
        key: 'toggle',
        label: item.status === 'active' ? t('page.keys.action.disable') : t('page.keys.action.enable'),
      },
      { key: 'delete', label: t('common.delete'), danger: true },
    ],
    max: 2,
    onSelect: (key: string) => emit('action', item, key),
  })
}

const columns = computed<TableColumn[]>(() => [
  { key: 'name', title: t('page.keys.col.name'), mobile: 'title', minWidth: 200, sortable: true, sortOrder: sortOrderOf('name'), render: renderName },
  { key: 'key', title: 'Key', mobile: 'subtitle', width: 270, render: renderKey },
  { key: 'quota', title: t('page.keys.col.quota'), mobile: 'meta', width: 190, sortable: true, sortOrder: sortOrderOf('used'), render: renderQuota },
  { key: 'concurrency', title: t('page.keys.col.limits'), mobile: 'meta', width: 170, sortable: true, sortOrder: sortOrderOf('concurrency'), render: renderLimits },
  { key: 'ips', title: t('page.keys.col.ips'), mobile: 'meta', minWidth: 190, render: renderIps },
  { key: 'group', title: t('page.keys.col.group'), mobile: 'meta', width: 130, render: renderGroup },
  { key: 'status', title: t('page.keys.col.status'), mobile: 'meta', width: 96, render: renderStatus },
  { key: 'actions', title: '', mobile: 'actions', width: 150, hideable: false, fixed: 'right', render: renderActions },
])
</script>

<template>
  <DataTable
    class="kt-table"
    table-id="keys"
    :columns="columns"
    :data="rows"
    row-key="id"
    remote
    :loading="loading"
    :refreshing="refreshing"
    :page="page"
    :page-size="pageSize"
    :item-count="total"
    :page-sizes="[20, 50, 100]"
    :empty-description="emptyDescription"
    @update:page="(value: number) => emit('page', value)"
    @update:page-size="(value: number) => emit('pageSize', value)"
    @update:sorter="(value: SorterPayload | null) => emit('sorter', value)"
  >
    <template #toolbar>
      <slot name="toolbar" />
    </template>
    <template #empty>
      <Empty :description="emptyDescription">
        <Btn v-if="hasFilters" variant="default" size="sm" @click="emit('reset')">
          {{ t('page.keys.clearFilters') }}
        </Btn>
        <Btn v-else variant="default" size="sm" :icon="Plus" @click="emit('create')">
          {{ t('page.keys.action.create') }}
        </Btn>
      </Empty>
    </template>
  </DataTable>
</template>

<style scoped>
.kt-table :deep(.mh-table__toolbar-left) {
  flex: 1;
}

.kt-table :deep(.kt-name) {
  display: grid;
  gap: 2px;
  min-width: 0;
}

.kt-table :deep(.kt-name__main) {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 500;
  color: var(--fg);
}

.kt-table :deep(.kt-name__sub),
.kt-table :deep(.kt-muted),
.kt-table :deep(.kt-quota__hint) {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.kt-table :deep(.kt-key) {
  display: flex;
  align-items: center;
  gap: 2px;
  min-width: 0;
  flex-wrap: nowrap;
}

.kt-table :deep(.kt-key__text) {
  flex: 0 1 auto;
  min-width: 0;
  margin-right: var(--sp-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--font-mono);
  color: var(--fg-2);
}

.kt-table :deep(.kt-quota) {
  display: grid;
  gap: var(--sp-1);
  min-width: 0;
}

.kt-table :deep(.kt-quota__line) {
  display: flex;
  align-items: baseline;
  gap: 2px;
  color: var(--fg-2);
  white-space: nowrap;
}

.kt-table :deep(.kt-quota__used) {
  color: var(--fg);
  font-weight: 500;
}

.kt-table :deep(.kt-quota__sep) {
  color: var(--fg-faint);
}

.kt-table :deep(.kt-quota__pct) {
  margin-left: auto;
  font-size: var(--fs-tiny);
  color: var(--fg-muted);
}

.kt-table :deep(.kt-limits) {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-2);
}

.kt-table :deep(.kt-ips) {
  display: grid;
  gap: var(--sp-1);
  min-width: 0;
}

.kt-table :deep(.keys-ips__count) {
  justify-self: start;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--brand);
  font-size: var(--fs-small);
  cursor: pointer;
}

.kt-table :deep(.keys-ips__count:hover) {
  text-decoration: underline;
}

.kt-table :deep(.kt-ips__badges) {
  display: flex;
  gap: var(--sp-1);
  flex-wrap: wrap;
}

/* IP 是标识，不是状态：中性底 + 等宽（第 1 轮审查 P2 API Keys） */
.kt-table :deep(.kt-ip) {
  display: inline-flex;
  align-items: center;
  height: 20px;
  padding: 0 6px;
  border-radius: var(--r-chip);
  background: var(--neutral-soft);
  color: var(--fg-2);
  font-family: var(--font-mono);
  font-size: var(--fs-tiny);
  line-height: 1;
}
</style>
