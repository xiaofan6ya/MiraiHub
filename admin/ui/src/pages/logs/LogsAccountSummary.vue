<script setup lang="ts">
import TitleTip from '@/components/base/TitleTip.vue'

/**
 * 按账号汇总（放在日志表下方，默认收起；第 1 轮审查 P2：日志表要在第一位）。
 * 数据与顶部 KPI 同一次 /api/logs/summary，展开不额外请求。行点击 = 按该账号筛选。
 */
import { computed, h, ref, type VNodeChild } from 'vue'
import { ChevronDown } from 'lucide-vue-next'
import {
  Btn,
  Card,
  DataTable,
  Empty,
  ErrorBlock,
  Meter,
  type TableColumn,
  type TableRow,
} from '@/components'
import { FMT_EMPTY, fmtCompact, fmtCredit, fmtFullTime, fmtInt, fmtRelative } from '@/utils/format'
import { useI18n } from '@/i18n'
import { toAccountSummaryRows, type AccountSummaryRow, type UsageLogSummaryPayload } from '@/api/logs'

const props = withDefaults(
  defineProps<{
    summary: UsageLogSummaryPayload | null
    loading?: boolean
    error?: { message: string; detail: string } | null
  }>(),
  { loading: false, error: null },
)

const emit = defineEmits<{
  (event: 'pick', row: AccountSummaryRow): void
  (event: 'retry'): void
}>()

const { t } = useI18n()
const open = ref(false)

const rows = computed<AccountSummaryRow[]>(() => toAccountSummaryRows(props.summary?.items))
const maxCredits = computed(() => Math.max(1, ...rows.value.map((row) => row.credits)))

const headline = computed(() => {
  const s = props.summary
  if (!s) return ''
  const parts = [t('page.logs.summary.accounts', { count: fmtInt(s.account_count) })]
  if (s.unattributed_requests) {
    parts.push(t('page.logs.kpi.unattributedHint', { count: fmtInt(s.unattributed_requests) }))
  }
  return parts.join(' · ')
})

function asRow(row: TableRow): AccountSummaryRow {
  return row as unknown as AccountSummaryRow
}

const columns = computed<TableColumn[]>(() => [
  {
    key: 'account_name',
    title: t('page.logs.summaryCol.account'),
    mobile: 'title',
    minWidth: 180,
    render: (row: TableRow): VNodeChild => {
      const item = asRow(row)
      return h(
        'span',
        { class: item.unattributed ? 'ls-name is-muted' : 'ls-name' },
        item.unattributed ? t('page.logs.summaryCol.unattributed') : item.account_name || '#' + item.account_id,
      )
    },
  },
  {
    key: 'requests',
    title: t('page.logs.summaryCol.requests'),
    mobile: 'meta',
    width: 96,
    numeric: true,
    render: (row: TableRow) => h('span', { class: 'ls-num' }, fmtInt(asRow(row).requests)),
  },
  {
    key: 'failed_requests',
    title: t('page.logs.summaryCol.failed'),
    mobile: 'meta',
    width: 80,
    numeric: true,
    render: (row: TableRow) => {
      const item = asRow(row)
      return h('span', { class: item.failed_requests > 0 ? 'ls-num is-danger' : 'ls-num is-faint' }, fmtInt(item.failed_requests))
    },
  },
  {
    key: 'credits',
    title: t('page.logs.summaryCol.credits'),
    mobile: 'meta',
    width: 96,
    numeric: true,
    render: (row: TableRow) => h('span', { class: 'ls-num' }, fmtCredit(asRow(row).credits)),
  },
  {
    key: 'total_tokens',
    title: t('page.logs.summaryCol.tokens'),
    mobile: 'meta',
    width: 96,
    numeric: true,
    render: (row: TableRow) => h(TitleTip, { content: String((fmtInt(asRow(row).total_tokens)) ?? '') }, { default: () => h('span', { class: 'ls-num' }, fmtCompact(asRow(row).total_tokens)) }),
  },
  {
    key: 'cache_rate',
    title: t('page.logs.summaryCol.cacheRate'),
    mobile: 'hidden',
    width: 88,
    numeric: true,
    render: (row: TableRow) => h('span', { class: 'ls-num' }, asRow(row).cacheRateText),
  },
  {
    key: 'avg_latency_ms',
    title: t('page.logs.summaryCol.avgLatency'),
    mobile: 'hidden',
    width: 96,
    numeric: true,
    render: (row: TableRow) => h('span', { class: 'ls-num' }, asRow(row).avgLatencyText),
  },
  {
    key: 'last_used_at',
    title: t('page.logs.summaryCol.lastUsed'),
    mobile: 'meta',
    width: 112,
    render: (row: TableRow) => {
      const item = asRow(row)
      return item.last_used_at
        ? h(TitleTip, { content: String((fmtFullTime(item.last_used_at)) ?? '') }, { default: () => h('span', { class: 'ls-time' }, fmtRelative(item.last_used_at)) })
        : h('span', { class: 'ls-num is-faint' }, FMT_EMPTY)
    },
  },
  {
    key: 'share',
    title: t('page.logs.summaryCol.share'),
    mobile: 'hidden',
    width: 140,
    render: (row: TableRow): VNodeChild => {
      const item = asRow(row)
      // 分类占比用中性色（01-direction §3：状态色只表达状态）
      return h(Meter, {
        value: item.credits,
        max: maxCredits.value,
        tone: 'neutral',
        valueText: Math.round((item.credits / maxCredits.value) * 100) + '%',
        showValue: true,
        ariaLabel: t('page.logs.summaryCol.share'),
      })
    },
  },
])

function onRowClick(row: TableRow): void {
  emit('pick', asRow(row))
}
</script>

<template>
  <Card class="ls" :loading="loading && open">
    <template #title>
      <button type="button" class="ls-toggle" :aria-expanded="open" @click="open = !open">
        <ChevronDown :size="16" class="ls-toggle__icon" :class="{ 'is-open': open }" />
        {{ t('page.logs.summary.title') }}
      </button>
    </template>
    <template #extra>
      <span class="ls-headline tnum">{{ headline }}</span>
      <Btn variant="quiet" size="sm" @click="open = !open">
        {{ open ? t('page.logs.summary.collapse') : t('page.logs.summary.expand') }}
      </Btn>
    </template>
    <template v-if="open">
      <ErrorBlock
        v-if="error"
        compact
        :title="t('page.logs.summary.error', { error: error.message })"
        :detail="error.detail"
        @retry="emit('retry')"
      />
      <Empty v-else-if="summary && !rows.length" compact :description="t('page.logs.summary.empty')" />
      <DataTable
        v-else
        table-id="logs-summary"
        :columns="columns"
        :data="rows as unknown as TableRow[]"
        row-key="account_id"
        :loading="loading"
        :pagination="false"
        :virtual-scroll="false"
        clickable-rows
        :empty-description="t('page.logs.summary.empty')"
        @row-click="onRowClick"
      />
    </template>
  </Card>
</template>

<style scoped>
.ls-toggle {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-1);
  padding: 0;
  border: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  cursor: pointer;
}

.ls-toggle__icon {
  color: var(--fg-muted);
  transform: rotate(-90deg);
  transition: transform var(--t-fast) var(--ease);
}

.ls-toggle__icon.is-open {
  transform: none;
}

.ls-headline {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.ls :deep(.ls-name) {
  font-weight: 500;
  color: var(--fg);
}

.ls :deep(.ls-name.is-muted) {
  font-weight: 400;
  color: var(--fg-muted);
}

.ls :deep(.ls-num) {
  font-variant-numeric: tabular-nums;
  color: var(--fg);
}

.ls :deep(.ls-num.is-faint) {
  color: var(--fg-faint);
}

.ls :deep(.ls-num.is-danger) {
  color: var(--danger);
}

.ls :deep(.ls-time) {
  color: var(--fg-2);
  white-space: nowrap;
}

@media (prefers-reduced-motion: reduce) {
  .ls-toggle__icon {
    transition: none;
  }
}
</style>
