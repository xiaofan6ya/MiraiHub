<script setup lang="ts">
/** 时间区间：NDatePicker(datetimerange) + 今日 / 昨日 / 近 7 天 / 近 30 天快捷项。 */
import { computed } from 'vue'
import { NDatePicker } from 'naive-ui'
import { dateRangeShortcuts } from './dateRange'
import { t, type MessageKey } from '@/i18n'

type DateRangeValue = [number, number] | null

const props = withDefaults(
  defineProps<{
    modelValue?: DateRangeValue
    placeholder?: string
    /** 控件宽度（默认 260px，与 04-components §2 FilterBar 一致） */
    width?: number
    clearable?: boolean
  }>(),
  { modelValue: null, placeholder: undefined, width: 260, clearable: true },
)

const emit = defineEmits<{ (event: 'update:modelValue', value: DateRangeValue): void }>()

const placeholderText = computed(() => props.placeholder ?? t('date.placeholder'))

/** 快捷项 label 是中文 key（dateRange.ts 的纯函数保持无依赖），这里按当前语言重写。 */
const SHORTCUT_KEYS: Record<string, MessageKey> = {
  '今日': 'date.today',
  '昨日': 'date.yesterday',
  '近 7 天': 'date.last7',
  '近 30 天': 'date.last30',
}

const shortcuts = computed(() => {
  const table = dateRangeShortcuts()
  const out: Record<string, () => [number, number]> = {}
  for (const [label, value] of Object.entries(table)) {
    const key = SHORTCUT_KEYS[label]
    out[key ? t(key) : label] = value
  }
  return out
})
</script>

<template>
  <n-date-picker
    class="mh-daterange"
    type="datetimerange"
    :value="modelValue"
    :placeholder="placeholderText"
    :shortcuts="shortcuts"
    :clearable="clearable"
    :style="{ width: width + 'px' }"
    size="medium"
    @update:value="(value: [number, number] | null) => emit('update:modelValue', value)"
  />
</template>

<style scoped>
.mh-daterange {
  max-width: 100%;
}
</style>
