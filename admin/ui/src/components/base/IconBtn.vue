<script setup lang="ts">
/**
 * 32×32 纯图标按钮：必须有 aria-label 与 tooltip（05-patterns §7）。
 * 可以用 Tab 聚焦（以前 focusable=false，键盘用户点不到表格工具栏的图标按钮）。
 */
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
  --n-border-radius: var(--r-ctl) !important;
  --n-text-color: var(--fg-2) !important;
  --n-text-color-hover: var(--fg) !important;
  --n-text-color-pressed: var(--fg) !important;
  --n-text-color-focus: var(--fg) !important;
  --n-color-hover: var(--surface-hover) !important;
  --n-color-pressed: var(--surface-2) !important;
  --n-color-focus: var(--surface-hover) !important;
  --n-ripple-color: transparent !important;
}

.mh-icon-btn {
  transition:
    transform var(--dur-1) var(--ease-out),
    color var(--dur-1) var(--ease-out),
    background-color var(--dur-1) var(--ease-out);
}

/* 按压反馈（11-polish-motion §1）；减少动效时不缩放 */
.mh-icon-btn:not(.n-button--disabled):active {
  transform: scale(.94);
}

@media (prefers-reduced-motion: reduce) {
  .mh-icon-btn:not(.n-button--disabled):active {
    transform: none;
  }
}

.mh-icon-btn--danger {
  --n-text-color: var(--danger) !important;
  --n-text-color-hover: var(--danger) !important;
  --n-text-color-pressed: var(--danger) !important;
  --n-color-hover: var(--danger-soft) !important;
}

.mh-icon-btn--active {
  --n-color-hover: var(--surface-2) !important;
  --n-color-focus: var(--surface-2) !important;
  background: var(--surface-2);
  color: var(--fg);
}
</style>
