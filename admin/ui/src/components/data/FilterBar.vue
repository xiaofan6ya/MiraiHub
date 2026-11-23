<script setup lang="ts">
/**
 * 一行筛选控件 + 右侧「重置」；下方「已筛选」chips 与结果统计。
 * 筛选变化 300ms 防抖后自动查询（不需要「查询」按钮）。
 */
import { computed, onScopeDispose, ref } from 'vue'
import { RotateCw, X } from 'lucide-vue-next'
import Btn from '../base/Btn.vue'
import type { FilterChip } from './filter'
import { useI18n } from '@/i18n'

const props = withDefaults(
  defineProps<{
    chips?: FilterChip[]
    /** 结果统计文字，如「共 862 次 · 成功 850 · 错误 12」 */
    summary?: string
    resetLabel?: string
    debounceMs?: number
    loading?: boolean
  }>(),
  {
    chips: () => [],
    summary: undefined,
    resetLabel: undefined,
    debounceMs: 300,
    loading: false,
  },
)

const emit = defineEmits<{
  (event: 'change', values: Record<string, unknown>): void
  (event: 'reset'): void
  (event: 'remove', key: string): void
}>()

const { t } = useI18n()
const resetText = computed(() => props.resetLabel ?? t('filter.reset'))
const pending = ref<Record<string, unknown>>({})
let timer: ReturnType<typeof setTimeout> | null = null

function flush(): void {
  if (timer) {
    clearTimeout(timer)
    timer = null
  }
  if (Object.keys(pending.value).length === 0) return
  const values = { ...pending.value }
  pending.value = {}
  emit('change', values)
}

/** 槽内控件 onChange 时调用：300ms 防抖后合并成一次查询。 */
function change(key: string, value: unknown): void {
  pending.value[key] = value
  if (timer) clearTimeout(timer)
  timer = setTimeout(flush, props.debounceMs)
}

onScopeDispose(() => {
  if (timer) clearTimeout(timer)
})
</script>

<template>
  <div class="mh-filter">
    <div class="mh-filter__row">
      <div class="mh-filter__controls">
        <slot :change="change" :flush="flush" />
      </div>
      <Btn variant="quiet" size="sm" :icon="RotateCw" :loading="loading" @click="emit('reset')">
        {{ resetText }}
      </Btn>
    </div>
    <div v-if="chips.length || summary" class="mh-filter__chips">
      <span v-if="chips.length" class="mh-filter__chips-label">{{ t('filter.applied') }}</span>
      <button
        v-for="chip in chips"
        :key="chip.key"
        class="mh-filter__chip"
        type="button"
        :aria-label="t('filter.remove', { label: chip.label })"
        @click="emit('remove', chip.key)"
      >
        <span>{{ chip.label }}</span>
        <span v-if="chip.value" class="mh-filter__chip-value">{{ chip.value }}</span>
        <X :size="12" />
      </button>
      <span v-if="summary" class="mh-filter__summary tnum">{{ summary }}</span>
    </div>
  </div>
</template>

<style scoped>
.mh-filter {
  display: grid;
  gap: var(--sp-2);
  min-width: 0;
}

.mh-filter__row {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
}

.mh-filter__controls {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
  flex: 1;
  min-width: 0;
}

.mh-filter__chips {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
  min-height: 22px;
}

.mh-filter__chips-label {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-faint);
}

.mh-filter__chip {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-1);
  height: 22px;
  padding: 0 var(--sp-2);
  border-radius: var(--r-chip);
  border: 1px solid var(--border-soft);
  background: var(--surface-2);
  color: var(--fg-2);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  cursor: pointer;
}

.mh-filter__chip:hover {
  border-color: var(--border-strong);
  color: var(--fg);
}

.mh-filter__chip-value {
  color: var(--fg);
  font-weight: 500;
}

.mh-filter__summary {
  margin-left: auto;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

@media (max-width: 640px) {
  .mh-filter__summary {
    margin-left: 0;
    width: 100%;
  }
}
</style>
