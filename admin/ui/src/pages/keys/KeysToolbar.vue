<script setup lang="ts">
/**
 * API Keys 表格上方的一行工具栏（10-visual-refresh §1.5）：
 * 搜索 · 分组 · 状态 ……… 统计（共 N 个 Key · 正常 · 已停用 · 不限额）· 重置 · 刷新。
 * 原来是 FilterBar（筛选一行）+「已筛选」chips 一行 + 表格图标一行；三个控件本身就显示了当前值，chips 是重复信息，去掉。
 */
import { onBeforeUnmount, ref, watch } from 'vue'
import { NInput, NSelect } from 'naive-ui'
import { RefreshCw, RotateCw, Search } from 'lucide-vue-next'
import { Btn, IconBtn } from '@/components'
import { useI18n } from '@/i18n'

const props = withDefaults(
  defineProps<{
    q: string
    group: string
    status: string
    groupOptions: Array<{ label: string; value: string }>
    statusOptions: Array<{ label: string; value: string }>
    summary: string
    hasFilters: boolean
    loading?: boolean
  }>(),
  { loading: false },
)

const emit = defineEmits<{
  (event: 'set', name: 'q' | 'group' | 'status', value: string): void
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
  <div class="kt-bar" role="search">
    <NInput
      class="kt-search"
      :value="search"
      :placeholder="t('page.keys.filter.searchPlaceholder')"
      clearable
      :aria-label="t('page.keys.filter.chipSearch')"
      @update:value="onSearch"
    >
      <template #prefix><Search :size="14" class="kt-icon" /></template>
    </NInput>
    <NSelect
      class="kt-select"
      :value="group"
      :options="groupOptions"
      :aria-label="t('page.keys.filter.chipGroup')"
      @update:value="(value: string | number | null) => emit('set', 'group', str(value))"
    />
    <NSelect
      class="kt-select"
      :value="status"
      :options="statusOptions"
      :aria-label="t('page.keys.filter.chipStatus')"
      @update:value="(value: string | number | null) => emit('set', 'status', str(value))"
    />
    <div class="kt-end">
      <span class="kt-summary tnum">{{ summary }}</span>
      <Btn v-if="hasFilters" variant="quiet" size="sm" :icon="RotateCw" @click="onReset">
        {{ t('filter.reset') }}
      </Btn>
      <IconBtn :label="t('common.refresh')" :icon="RefreshCw" :loading="loading" @click="emit('refresh')" />
    </div>
  </div>
</template>

<style scoped>
.kt-bar {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
  flex: 1;
  min-width: 0;
}

.kt-search {
  width: 260px;
}

.kt-icon {
  color: var(--fg-faint);
}

.kt-select {
  width: 160px;
}

.kt-end {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-2);
  margin-left: auto;
}

.kt-summary {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  white-space: nowrap;
}

@media (max-width: 640px) {
  .kt-search,
  .kt-select {
    width: 100%;
  }

  .kt-end {
    margin-left: 0;
    flex-wrap: wrap;
  }

  .kt-summary {
    white-space: normal;
  }
}
</style>
