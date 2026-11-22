<script setup lang="ts">
/**
 * 按钮。基于 NButton，只通过 --n-* 变量换色，不覆盖结构样式。
 * 约定：**每个区域最多 1 个 primary**（04-components §1）。
 */
import { computed, type Component } from 'vue'
import { NButton } from 'naive-ui'
import type { BtnSize, BtnVariant } from './btn'

const props = withDefaults(
  defineProps<{
    variant?: BtnVariant
    size?: BtnSize
    /** 左侧图标（lucide 组件），固定 16px。 */
    icon?: Component
    loading?: boolean
    disabled?: boolean
    block?: boolean
    /** danger 变体填充朱底白字。 */
    solid?: boolean
    title?: string
    ariaLabel?: string
    nativeType?: 'button' | 'submit' | 'reset'
  }>(),
  {
    variant: 'default',
    size: 'md',
    icon: undefined,
    loading: false,
    disabled: false,
    block: false,
    solid: false,
    title: undefined,
    ariaLabel: undefined,
    nativeType: 'button',
  },
)

defineEmits<{ (event: 'click', payload: MouseEvent): void }>()

const naiveSize = computed(() =>
  props.size === 'sm' ? 'small' : props.size === 'lg' ? 'large' : 'medium',
)
</script>

<template>
  <n-button
    class="mh-btn"
    :class="[`mh-btn--${variant}`, `mh-btn--${size}`, { 'mh-btn--solid': solid, 'mh-btn--block': block }]"
    :size="naiveSize"
    :loading="loading"
    :disabled="disabled || loading"
    :block="block"
    :attr-type="nativeType"
    :title="title"
    :aria-label="ariaLabel || title"
    @click="$emit('click', $event)"
  >
    <template v-if="icon || $slots.icon" #icon>
      <slot name="icon"><component :is="icon" :size="16" /></slot>
    </template>
    <slot />
  </n-button>
</template>

<style scoped>
.mh-btn {
  --n-icon-size: 16px;
  --n-border-radius: var(--r-ctl);
  font-weight: 500;
}

/* 主操作：靛底白字 */
.mh-btn--primary {
  --n-color: var(--brand);
  --n-color-hover: var(--brand-hover);
  --n-color-pressed: var(--brand-hover);
  --n-color-focus: var(--brand-hover);
  --n-text-color: var(--brand-fg);
  --n-text-color-hover: var(--brand-fg);
  --n-text-color-pressed: var(--brand-fg);
  --n-text-color-focus: var(--brand-fg);
  --n-border: 1px solid var(--brand);
  --n-border-hover: 1px solid var(--brand-hover);
  --n-border-pressed: 1px solid var(--brand-hover);
  --n-border-focus: 1px solid var(--brand-hover);
  --n-ripple-color: transparent;
}

/* 次操作：纸底 + 1px 描边 */
.mh-btn--default {
  --n-color: var(--surface);
  --n-color-hover: var(--surface-hover);
  --n-color-pressed: var(--surface-2);
  --n-color-focus: var(--surface);
  --n-text-color: var(--fg);
  --n-text-color-hover: var(--fg);
  --n-text-color-pressed: var(--fg);
  --n-text-color-focus: var(--fg);
  --n-border: 1px solid var(--border-strong);
  --n-border-hover: 1px solid var(--border-strong);
  --n-border-pressed: 1px solid var(--border-strong);
  --n-border-focus: 1px solid var(--brand);
  --n-ripple-color: transparent;
}

/* 无边框：工具栏、行内操作 */
.mh-btn--quiet {
  --n-color: transparent;
  --n-color-hover: var(--surface-hover);
  --n-color-pressed: var(--surface-2);
  --n-color-focus: transparent;
  --n-text-color: var(--fg-2);
  --n-text-color-hover: var(--fg);
  --n-text-color-pressed: var(--fg);
  --n-text-color-focus: var(--fg);
  --n-border: 1px solid transparent;
  --n-border-hover: 1px solid transparent;
  --n-border-pressed: 1px solid transparent;
  --n-border-focus: 1px solid transparent;
  --n-ripple-color: transparent;
}

/* 危险操作：朱色文字；solid 时朱底白字 */
.mh-btn--danger {
  --n-color: var(--surface);
  --n-color-hover: var(--danger-soft);
  --n-color-pressed: var(--danger-soft);
  --n-color-focus: var(--surface);
  --n-text-color: var(--danger);
  --n-text-color-hover: var(--danger);
  --n-text-color-pressed: var(--danger);
  --n-text-color-focus: var(--danger);
  --n-border: 1px solid var(--border-strong);
  --n-border-hover: 1px solid var(--danger);
  --n-border-pressed: 1px solid var(--danger);
  --n-border-focus: 1px solid var(--danger);
  --n-ripple-color: transparent;
}

.mh-btn--danger.mh-btn--solid {
  --n-color: var(--danger);
  --n-color-hover: var(--danger);
  --n-color-pressed: var(--danger);
  --n-color-focus: var(--danger);
  --n-text-color: var(--brand-fg);
  --n-text-color-hover: var(--brand-fg);
  --n-text-color-pressed: var(--brand-fg);
  --n-text-color-focus: var(--brand-fg);
  --n-border: 1px solid var(--danger);
  --n-border-hover: 1px solid var(--danger);
  --n-border-pressed: 1px solid var(--danger);
  --n-border-focus: 1px solid var(--danger);
}

.mh-btn--block {
  width: 100%;
}
</style>
