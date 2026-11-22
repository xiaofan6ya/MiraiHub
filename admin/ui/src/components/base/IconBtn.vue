<script setup lang="ts">
/** 32×32 纯图标按钮：必须有 aria-label 与 tooltip（05-patterns §7）。 */
import { type Component } from 'vue'
import { NButton, NTooltip } from 'naive-ui'

withDefaults(
  defineProps<{
    /** 必填：既是 tooltip 文案也是 aria-label。 */
    label: string
    icon: Component
    disabled?: boolean
    loading?: boolean
    /** 图标色沿用 --fg-2；danger 用于删除类。 */
    tone?: 'default' | 'danger'
    /** active 时高亮（如密度/列设置已打开）。 */
    active?: boolean
  }>(),
  { disabled: false, loading: false, tone: 'default', active: false },
)

defineEmits<{ (event: 'click', payload: MouseEvent): void }>()
</script>

<template>
  <n-tooltip :delay="400" :disabled="false">
    <template #trigger>
      <n-button
        class="mh-icon-btn"
        :class="{
          'mh-icon-btn--danger': tone === 'danger',
          'mh-icon-btn--active': active,
        }"
        quaternary
        :focusable="false"
        :loading="loading"
        :disabled="disabled"
        :aria-label="label"
        @click="$emit('click', $event)"
      >
        <template #icon><component :is="icon" :size="16" /></template>
      </n-button>
    </template>
    {{ label }}
  </n-tooltip>
</template>

<style scoped>
.mh-icon-btn {
  width: 32px;
  height: 32px;
  padding: 0;
  --n-border-radius: var(--r-ctl);
  --n-text-color: var(--fg-2);
  --n-text-color-hover: var(--fg);
  --n-text-color-pressed: var(--fg);
  --n-text-color-focus: var(--fg);
  --n-color-hover: var(--surface-hover);
  --n-color-pressed: var(--surface-2);
  --n-color-focus: var(--surface-hover);
  --n-ripple-color: transparent;
}

.mh-icon-btn--danger {
  --n-text-color: var(--danger);
  --n-text-color-hover: var(--danger);
  --n-text-color-pressed: var(--danger);
  --n-color-hover: var(--danger-soft);
}

.mh-icon-btn--active {
  --n-color-hover: var(--surface-2);
  --n-color-focus: var(--surface-2);
  background: var(--surface-2);
  color: var(--fg);
}
</style>
