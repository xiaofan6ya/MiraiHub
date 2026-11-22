<script setup lang="ts">
/** 时间区间：NDatePicker(datetimerange) + 今日 / 昨日 / 近 7 天 / 近 30 天快捷项。 */
import { computed } from 'vue'
import { NDatePicker } from 'naive-ui'
import { dateRangeShortcuts } from './dateRange'

type DateRangeValue = [number, number] | null

const props = withDefaults(
  defineProps<{
    modelValue?: DateRangeValue
    placeholder?: string
    /** 控件宽度（默认 260px，与 04-components §2 FilterBar 一致） */
    width?: number
    clearable?: boolean
  }>(),
  { modelValue: null, placeholder: '选择时间区间', width: 260, clearable: true },
)

const emit = defineEmits<{ (event: 'update:modelValue', value: DateRangeValue): void }>()

const shortcuts = computed(() => dateRangeShortcuts())
</script>

<template>
  <n-date-picker
    class="mh-daterange"
    type="datetimerange"
    :value="modelValue"
    :placeholder="placeholder"
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
