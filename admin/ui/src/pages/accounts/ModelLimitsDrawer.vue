<script setup lang="ts">
/**
 * 账号池 · 模型额度与冷却（只读：GET /api/accounts/model-limits）。
 * 打开时取数，关闭 / 卸载时取消。
 */
import { onBeforeUnmount, ref, watch } from 'vue'
import { Empty, ErrorBlock, FormDrawer } from '@/components'
import { isAbortError } from '@/api/http'
import { failureText } from '@/utils/error'
import { fmtDuration } from '@/utils/format'
import { useI18n } from '@/i18n'
import { getModelLimits } from '@/api/accounts'

const props = defineProps<{ show: boolean }>()
const emit = defineEmits<{ (event: 'update:show', value: boolean): void }>()

const { t } = useI18n()

const loading = ref(false)
const error = ref('')
const rows = ref<Array<Record<string, unknown>>>([])
let controller: AbortController | null = null

async function load(): Promise<void> {
  controller?.abort()
  const current = new AbortController()
  controller = current
  loading.value = true
  error.value = ''
  try {
    const payload = await getModelLimits(current.signal)
    if (controller !== current) return
    rows.value = Array.isArray(payload?.items) ? payload.items : []
  } catch (err) {
    if (isAbortError(err) || controller !== current) return
    error.value = failureText(t('page.accounts.limitsLoadFailed'), err)
  } finally {
    if (controller === current) {
      controller = null
      loading.value = false
    }
  }
}

watch(
  () => props.show,
  (show) => {
    if (show) void load()
    else {
      controller?.abort()
      controller = null
      loading.value = false
    }
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  controller?.abort()
  controller = null
})

function accountLabel(row: Record<string, unknown>): string {
  const name = String(row.account_name || '')
  return name || '#' + String(row.account_id ?? '')
}
</script>

<template>
  <FormDrawer
    :show="show"
    :title="t('page.accounts.limitsTitle')"
    size="lg"
    hide-footer
    @update:show="(value: boolean) => emit('update:show', value)"
  >
    <p class="acc-limits__intro">{{ t('page.accounts.limitsIntro') }}</p>
    <div v-if="loading" class="acc-limits__skeleton">
      <div v-for="index in 4" :key="index" class="acc-limits__skeleton-row mh-skeleton" />
    </div>
    <ErrorBlock v-else-if="error" :title="error" compact @retry="load" />
    <ul v-else-if="rows.length" class="acc-limits__list">
      <li v-for="(row, index) in rows" :key="index" class="acc-limits__row">
        <span class="acc-limits__name">{{ accountLabel(row) }}</span>
        <span class="mono acc-limits__model">{{ String(row.model || '') }}</span>
        <span class="tnum acc-limits__left">{{ fmtDuration((Number(row.remaining_seconds) || 0) * 1000) }}</span>
      </li>
    </ul>
    <Empty v-else :description="t('page.accounts.limitsEmpty')" compact />
  </FormDrawer>
</template>

<style scoped>
.acc-limits__intro {
  margin: 0 0 var(--sp-3);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.acc-limits__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
}

.acc-limits__row {
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) minmax(0, 1.4fr) auto;
  gap: var(--sp-3);
  align-items: center;
  min-height: var(--row-h-compact, 36px);
  border-bottom: 1px solid var(--border-soft);
  font-size: var(--fs-small);
  color: var(--fg-2);
}

.acc-limits__row:last-child {
  border-bottom: 0;
}

.acc-limits__name {
  color: var(--fg);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.acc-limits__model {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.acc-limits__left {
  text-align: right;
}

.acc-limits__skeleton {
  display: grid;
  gap: var(--sp-2);
}

.acc-limits__skeleton-row {
  height: 12px;
  border-radius: var(--r-chip);
  background: var(--surface-2);
}
</style>
