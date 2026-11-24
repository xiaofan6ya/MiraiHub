<script setup lang="ts">
/** Key 用量抽屉（GET /api/keys/{id}/usage）：四格 + 中性色进度条。 */
import { computed, ref, watch } from 'vue'
import { Btn, DetailDrawer, ErrorBlock, Meter } from '@/components'
import { errorMessage } from '@/utils/error'
import { useI18n } from '@/i18n'
import { keyUsage, quotaMeterTone, toUsageView, type KeyRow, type KeyUsagePayload } from '@/api/keys'

const props = defineProps<{ show: boolean; row: KeyRow | null }>()
const emit = defineEmits<{ (event: 'update:show', value: boolean): void }>()

const { t } = useI18n()
const loading = ref(false)
const error = ref('')
const data = ref<KeyUsagePayload | null>(null)
let token = 0

async function load(): Promise<void> {
  const row = props.row
  if (!row) return
  const current = ++token
  loading.value = true
  error.value = ''
  data.value = null
  try {
    const payload = await keyUsage(row.id)
    if (current === token) data.value = payload
  } catch (err) {
    if (current === token) error.value = errorMessage(err)
  } finally {
    if (current === token) loading.value = false
  }
}

watch(
  () => [props.show, props.row?.id] as const,
  ([show]) => {
    if (show) void load()
  },
  { immediate: true },
)

const view = computed(() => (data.value ? toUsageView(data.value) : null))
const tone = computed(() =>
  view.value ? quotaMeterTone({ unlimited: view.value.unlimited, usageRatio: view.value.ratio }) : 'neutral',
)
</script>

<template>
  <DetailDrawer
    :show="show"
    :title="t('page.keys.usage.title')"
    :subtitle="row ? row.name + ' · ' + t('page.keys.usage.subtitle') : t('page.keys.usage.subtitle')"
    @update:show="(value: boolean) => emit('update:show', value)"
  >
    <ErrorBlock v-if="error" :title="t('page.keys.usage.error', { error })" compact @retry="load" />
    <div v-else-if="loading" class="ku-skeleton" aria-busy="true">
      <span v-for="index in 3" :key="index" class="mh-skeleton ku-skeleton__row" />
    </div>
    <template v-else-if="view">
      <div class="ku-grid">
        <div class="ku-cell">
          <span>{{ t('page.keys.usage.limit') }}</span>
          <strong class="tnum">{{ view.limitText }}</strong>
        </div>
        <div class="ku-cell">
          <span>{{ t('page.keys.usage.used') }}</span>
          <strong class="tnum">{{ view.usedText }}</strong>
        </div>
        <div class="ku-cell">
          <span>{{ t('page.keys.usage.remaining') }}</span>
          <strong class="tnum">{{ view.remainingText }}</strong>
        </div>
        <div class="ku-cell">
          <span>{{ t('page.keys.usage.rate') }}</span>
          <strong class="tnum">{{ view.percentText }}</strong>
        </div>
      </div>
      <Meter
        v-if="!view.unlimited"
        :value="view.ratio * 100"
        :tone="tone"
        :height="8"
        :aria-label="t('page.keys.usage.meterAria')"
      />
      <p class="ku-hint">{{ t('page.keys.usage.createdAt', { time: view.createdText }) }}</p>
    </template>
    <template #footer>
      <Btn variant="default" @click="emit('update:show', false)">{{ t('common.close') }}</Btn>
    </template>
  </DetailDrawer>
</template>

<style scoped>
.ku-skeleton {
  display: grid;
  gap: var(--sp-2);
}

.ku-skeleton__row {
  height: 18px;
}

.ku-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--sp-2);
}

.ku-cell {
  display: grid;
  gap: 2px;
  padding: var(--sp-3);
  border: 1px solid var(--border-soft);
  border-radius: var(--r-card);
  background: var(--surface-2);
}

.ku-cell span {
  font-size: var(--fs-small);
  color: var(--fg-muted);
}

.ku-cell strong {
  font-size: var(--fs-stat);
  line-height: var(--lh-stat);
  font-weight: 600;
  color: var(--fg);
}

.ku-hint {
  margin: 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

@media (max-width: 640px) {
  .ku-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
