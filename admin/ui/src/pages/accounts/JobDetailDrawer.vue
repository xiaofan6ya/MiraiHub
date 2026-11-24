<script setup lang="ts">
/**
 * 账号池 · 后台任务详情（jobrunner.snapshot 的逐条结果）。
 */
import { computed } from 'vue'
import { FormDrawer } from '@/components'
import { useI18n } from '@/i18n'
import { fmtInt } from '@/utils/format'
import { jobDoneText, type AccountJobSnapshot } from '@/api/accounts'

const props = defineProps<{
  show: boolean
  snapshot: AccountJobSnapshot | null
}>()

const emit = defineEmits<{ (event: 'update:show', value: boolean): void }>()

const { t } = useI18n()

const title = computed(() => props.snapshot?.title || t('page.accounts.jobTitle'))
const items = computed(() => props.snapshot?.items || [])
const head = computed(() =>
  [
    jobDoneText(props.snapshot, t),
    fmtInt(Number(props.snapshot?.done) || 0) + ' / ' + fmtInt(Number(props.snapshot?.total) || 0),
  ].join(' · '),
)

function rowName(row: Record<string, unknown>, index: number): string {
  return String(row.account || row.name || '#' + String(row.account_id || index + 1))
}

function rowText(row: Record<string, unknown>): string {
  const text = String(row.summary || row.reason || '')
  if (text) return text
  return row.ok === false ? t('page.accounts.jobItemFailed') : t('page.accounts.jobItemDone')
}
</script>

<template>
  <FormDrawer
    :show="show"
    :title="title"
    size="lg"
    hide-footer
    @update:show="(value: boolean) => emit('update:show', value)"
  >
    <p class="acc-job__head tnum">{{ head }}</p>
    <ul v-if="items.length" class="acc-job__list">
      <li
        v-for="(row, index) in items"
        :key="index"
        class="acc-job__row"
        :class="{ 'is-failed': row.ok === false }"
      >
        <span class="acc-job__name">{{ rowName(row, index) }}</span>
        <span class="acc-job__text">{{ rowText(row) }}</span>
      </li>
    </ul>
    <p v-else class="acc-job__head">{{ t('page.accounts.jobDetailEmpty') }}</p>
  </FormDrawer>
</template>

<style scoped>
.acc-job__head {
  margin: 0 0 var(--sp-3);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.acc-job__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  max-height: 60vh;
  overflow: auto;
}

.acc-job__row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 2fr);
  gap: var(--sp-3);
  align-items: center;
  min-height: var(--row-h-compact, 36px);
  border-bottom: 1px solid var(--border-soft);
  font-size: var(--fs-small);
}

.acc-job__name {
  color: var(--fg);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.acc-job__text {
  color: var(--fg-muted);
}

.acc-job__row.is-failed .acc-job__text {
  color: var(--danger);
}
</style>
