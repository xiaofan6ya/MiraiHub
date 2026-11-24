<script setup lang="ts">
/**
 * 定时任务 · 结果详情抽屉（从 SchedulesPage.vue 拆出，10-visual-refresh §5）。
 * 只读：摘要 / 字段表 / 逐账号明细 / 原始 JSON。
 */
import { computed } from 'vue'
import { CodeBlock, DataTable, DetailDrawer, KV, type KvItem, type TableColumn, type TableRow } from '@/components'
import { fmtInt, fmtTime } from '@/utils/format'
import { useI18n } from '@/i18n'
import { RESULT_FIELD_LABELS, type ScheduleRow } from '@/api/schedules'

const props = defineProps<{
  show: boolean
  row: ScheduleRow | null
}>()

const emit = defineEmits<{
  (event: 'update:show', value: boolean): void
}>()

const { t } = useI18n()

const resultSubtitle = computed(() => {
  const row = props.row
  if (!row) return undefined
  return t('page.schedules.result.subtitle', {
    state: row.enabled
      ? t('page.schedules.result.subtitleEnabled')
      : t('page.schedules.result.subtitleDisabled'),
    time: row.lastRunAt ? fmtTime(row.lastRunAt) : t('page.schedules.cell.neverRun'),
  })
})

const resultTitle = computed(() =>
  props.row
    ? t('page.schedules.result.titleOf', { name: props.row.displayName })
    : t('page.schedules.result.title'),
)

const resultKv = computed<KvItem[]>(() => {
  const parsed = props.row?.result.parsed
  if (!parsed) return []
  const items: KvItem[] = []
  for (const [key, value] of Object.entries(parsed)) {
    if (Array.isArray(value) || (value && typeof value === 'object')) continue
    const label = RESULT_FIELD_LABELS[key] || key
    const textValue =
      typeof value === 'boolean'
        ? value
          ? t('page.schedules.result.yes')
          : t('page.schedules.result.no')
        : value === null || value === ''
          ? ''
          : String(value)
    items.push({ label, value: textValue.length > 240 ? textValue.slice(0, 240) + '…' : textValue })
  }
  return items
})

const resultDetailColumns = computed<TableColumn[]>(() => [
  { key: 'account', title: t('page.schedules.result.colAccount'), mobile: 'title', minWidth: 160 },
  { key: 'status', title: t('page.schedules.result.colStatus'), mobile: 'meta', width: 96 },
  { key: 'credit', title: t('page.schedules.result.colCredit'), mobile: 'meta', width: 88, numeric: true },
  { key: 'reason', title: t('page.schedules.result.colReason'), mobile: 'meta', minWidth: 200 },
])

const resultDetailRows = computed<TableRow[]>(() =>
  (props.row?.result.detail ?? []).map((item, index) => ({
    key: String(item.account_id ?? index),
    account: String(item.account ?? item.name ?? '#' + String(item.account_id ?? index)),
    status:
      item.ok === false
        ? t('page.schedules.result.statusFailed')
        : String(item.status || t('page.schedules.result.statusOk')),
    credit: item.credit === undefined || item.credit === null ? '' : fmtInt(item.credit),
    reason: String(item.reason ?? item.error ?? ''),
  }))
)
</script>

<template>
  <DetailDrawer
    :show="show"
    @update:show="(value: boolean) => emit('update:show', value)"
    :title="resultTitle"
    :badge="row ? { tone: row.result.tone, label: row.result.label } : null"
    :subtitle="resultSubtitle"
  >
    <template v-if="row">
      <p v-if="row.result.summary" class="schedules-result__summary">
        {{ row.result.summary }}
      </p>

      <KV v-if="resultKv.length" :items="resultKv" />

      <DataTable
        v-if="resultDetailRows.length"
        table-id="schedules-result"
        :columns="resultDetailColumns"
        :data="resultDetailRows"
        row-key="key"
        pagination
        :page-size="10"
      />

      <CodeBlock
        v-if="row.result.raw"
        :code="row.result.raw"
        language="json"
        max-height="280px"
      />
      <p v-else class="schedules-hint">{{ t('page.schedules.result.empty') }}</p>
    </template>
  </DetailDrawer>
</template>

<style scoped>
.schedules-hint {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--sp-1);
  margin: 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.schedules-result__summary {
  margin: 0;
  padding: var(--sp-2) var(--sp-3);
  border-radius: var(--r-ctl);
  background: var(--surface-2);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-2);
}
</style>
