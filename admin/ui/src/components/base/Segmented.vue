<script setup lang="ts">
/** 分段控件：高 28px、--surface-2 底、选中项纸底 + 1px 边框。 */
import type { SegmentedOption } from './segmented'

const props = withDefaults(
  defineProps<{
    options: Array<SegmentedOption<string | number>>
    modelValue?: string | number
    ariaLabel?: string
    /** 手机上允许横向滚动 */
    scrollable?: boolean
  }>(),
  { modelValue: undefined, ariaLabel: '分段切换', scrollable: false },
)

const emit = defineEmits<{ (event: 'update:modelValue', value: string | number): void }>()

function select(value: string | number): void {
  if (value === props.modelValue) return
  emit('update:modelValue', value)
}
</script>

<template>
  <div
    class="mh-seg"
    :class="{ 'mh-seg--scroll': scrollable }"
    role="radiogroup"
    :aria-label="ariaLabel"
  >
    <button
      v-for="option in options"
      :key="String(option.value)"
      class="mh-seg__item"
      :class="{ 'is-active': option.value === modelValue }"
      type="button"
      role="radio"
      :aria-checked="option.value === modelValue"
      @click="select(option.value)"
    >
      <component :is="option.icon" v-if="option.icon" :size="14" />
      <span>{{ option.label }}</span>
    </button>
  </div>
</template>

<style scoped>
.mh-seg {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  height: 28px;
  padding: 2px;
  border-radius: var(--r-ctl);
  background: var(--surface-2);
}

.mh-seg--scroll {
  max-width: 100%;
  overflow-x: auto;
}

.mh-seg__item {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-1);
  height: 24px;
  padding: 0 var(--sp-3);
  border: 1px solid transparent;
  border-radius: var(--r-chip);
  background: transparent;
  color: var(--fg-muted);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  cursor: pointer;
  white-space: nowrap;
  transition: background var(--t-fast) var(--ease), color var(--t-fast) var(--ease);
}

.mh-seg__item:hover {
  color: var(--fg);
}

.mh-seg__item.is-active {
  background: var(--surface);
  border-color: var(--border);
  color: var(--fg);
  font-weight: 500;
}
</style>
