<script setup lang="ts">
/**
 * 分组管理 · 表格上方一行工具栏（10-visual-refresh §1.5；第 2 轮审查 P1-7）：
 * 搜索 · 模型状态 · Key 绑定 ……… 统计 · 重置 · 刷新。下拉自己显示当前值，不再另起一行「已筛选」chips。
 */
import { onBeforeUnmount, ref, watch } from 'vue'
import { NInput, NSelect } from 'naive-ui'
import { RefreshCw, RotateCw, Search } from 'lucide-vue-next'
import { Btn, IconBtn } from '@/components'
import { useI18n } from '@/i18n'

const props = withDefaults(
  defineProps<{
    q: string
    models: string
    keys: string
    modelOptions: Array<{ label: string; value: string }>
    keyOptions: Array<{ label: string; value: string }>
    summary: string
    hasFilters: boolean
    loading?: boolean
  }>(),
  { loading: false },
)

const emit = defineEmits<{
  (event: 'set', name: 'q' | 'models' | 'keys', value: string): void
  (event: 'reset'): void
  (event: 'refresh'): void
}>()

const { t } = useI18n()

const search = ref(props.q)
let timer: ReturnType<typeof setTimeout> | null = null

watch(
  () => props.q,
  (value) => {
    if (!timer) search.value = value
  },
)

function onSearch(value: string): void {
  search.value = value ?? ''
  if (timer) clearTimeout(timer)
  timer = setTimeout(() => {
    timer = null
    const next = search.value.trim()
    if (next !== props.q) emit('set', 'q', next)
  }, 300)
}

function onReset(): void {
  if (timer) clearTimeout(timer)
  timer = null
  search.value = ''
  emit('reset')
}

onBeforeUnmount(() => {
  if (timer) clearTimeout(timer)
})

function str(value: string | number | null): string {
  return value === null || value === undefined ? '' : String(value)
}
</script>

<template>
  <div class="gb-bar" role="search">
    <NInput
      class="gb-search"
      :value="search"
      :placeholder="t('page.groups.search.placeholder')"
      clearable
      :aria-label="t('page.groups.chip.search')"
      @update:value="onSearch"
    >
      <template #prefix><Search :size="14" class="gb-icon" /></template>
    </NInput>
    <NSelect
      class="gb-select"
      :value="models"
      :options="modelOptions"
      :aria-label="t('page.groups.chip.model')"
      @update:value="(value: string | number | null) => emit('set', 'models', str(value))"
    />
    <NSelect
      class="gb-select"
      :value="keys"
      :options="keyOptions"
      :aria-label="t('page.groups.chip.key')"
      @update:value="(value: string | number | null) => emit('set', 'keys', str(value))"
    />
    <div class="gb-end">
      <span class="gb-summary tnum">{{ summary }}</span>
      <Btn v-if="hasFilters" variant="quiet" size="sm" :icon="RotateCw" @click="onReset">{{ t('filter.reset') }}</Btn>
      <IconBtn :label="t('common.refresh')" :icon="RefreshCw" :loading="loading" @click="emit('refresh')" />
    </div>
  </div>
</template>

<style scoped>
.gb-bar {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
  flex: 1;
  min-width: 0;
}

.gb-search {
  width: 260px;
}

.gb-icon {
  color: var(--fg-faint);
}

.gb-select {
  width: 156px;
}

.gb-end {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-2);
  margin-left: auto;
}

.gb-summary {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  white-space: nowrap;
}

@media (max-width: 640px) {
  .gb-search,
  .gb-select {
    width: 100%;
  }

  .gb-end {
    margin-left: 0;
    flex-wrap: wrap;
  }

  .gb-summary {
    white-space: normal;
  }
}
</style>
