<script setup lang="ts">
/**
 * 按钮。基于 NButton，只通过 --n-* 变量换色，不覆盖结构样式。
 * 约定：**每个区域最多 1 个 primary**（04-components §1）。
 */
import { computed, type Component } from 'vue'
import { NButton, NPopover } from 'naive-ui'
import type { BtnSize, BtnVariant } from './btn'
import { useDelayedFlag } from '@/composables/useDelayedFlag'

// 让外层 Dropdown/Popover 识别嵌套弹层，事件与定位继续绑定实际按钮。
defineOptions({ inheritAttrs: false, __popover__: true })

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
    internalSyncTargetWithParent?: boolean
    internalInheritedEventHandlers?: { onClick: () => void; onMouseenter: () => void; onMouseleave: () => void; onFocus: () => void; onBlur: () => void }[]
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

const emit = defineEmits<{ (event: 'click', payload: MouseEvent): void }>()

/**
 * loading 两段式（11-polish-motion §2）：loading 一变 true 立即进入「处理中」——
 * 原生 disabled、aria-busy、光标 progress，但**外观不变**（不变灰、不出转圈）；
 * 超过 300ms 还没结束才交给 Naive 显示转圈。快接口不会一闪。
 */
const showSpinner = useDelayedFlag(() => props.loading, 300)
const pending = computed(() => props.loading && !showSpinner.value)

function onClick(event: MouseEvent): void {
  if (props.loading || props.disabled) return
  emit('click', event)
}

/**
 * 同时把语义交给 Naive（primary / error），样式仍以下面的 --n-* 令牌为准；
 * 这样即使样式表没加载，主按钮也不会退化成描边按钮。
 */
const naiveType = computed(() =>
  props.variant === 'primary' ? 'primary' : props.variant === 'danger' && props.solid ? 'error' : 'default',
)

const naiveSize = computed(() =>
  props.size === 'sm' ? 'small' : props.size === 'lg' ? 'large' : 'medium',
)
</script>

<template>
  <n-popover trigger="hover" placement="top" :show-arrow="true" :disabled="!title" :style="{ maxWidth: '320px' }"
    :internal-sync-target-with-parent="internalSyncTargetWithParent"
    :internal-inherited-event-handlers="internalInheritedEventHandlers">
    <template #trigger>
      <n-button
        v-bind="$attrs"
        class="mh-btn"
        :type="naiveType"
        :class="[
          `mh-btn--${variant}`,
          `mh-btn--${size}`,
          { 'mh-btn--solid': solid, 'mh-btn--block': block, 'mh-btn--pending': pending && !disabled },
        ]"
        :size="naiveSize"
        :loading="showSpinner"
        :disabled="disabled || loading"
        :block="block"
        :attr-type="nativeType"
        :aria-label="ariaLabel || title"
        :aria-busy="loading ? 'true' : undefined"
        @click="onClick"
      >
        <template v-if="icon || $slots.icon" #icon>
          <slot name="icon"><component :is="icon" :size="16" /></slot>
        </template>
        <slot />
      </n-button>
    </template>
    <div class="mh-help__content">{{ title }}</div>
  </n-popover>
</template>

<style scoped>
/*
 * 为什么每个 --n-* 都带 !important：
 * NButton 默认把自己的主题变量写成**行内 style**（style="--n-color: …"），
 * 行内声明比任何类选择器都优先 —— 不加 !important，下面的变体配色全部失效，
 * primary 也会渲染成 Naive 默认的描边按钮（第 1 轮审查 P1-1「整站没有一个主按钮」的根因）。
 * 作者样式表里的 !important 高于行内普通声明，这是不改 Naive 全局配置时最小的修法。
 */
.mh-btn {
  --n-icon-size: 16px !important;
  --n-border-radius: var(--r-ctl) !important;
  font-weight: 500;
}

/* 主操作：靛底白字 */
.mh-btn--primary {
  --n-color: var(--brand) !important;
  --n-color-hover: var(--brand-hover) !important;
  --n-color-pressed: var(--brand-hover) !important;
  --n-color-focus: var(--brand-hover) !important;
  --n-text-color: var(--brand-fg) !important;
  --n-text-color-hover: var(--brand-fg) !important;
  --n-text-color-pressed: var(--brand-fg) !important;
  --n-text-color-focus: var(--brand-fg) !important;
  --n-border: 1px solid var(--brand) !important;
  --n-border-hover: 1px solid var(--brand-hover) !important;
  --n-border-pressed: 1px solid var(--brand-hover) !important;
  --n-border-focus: 1px solid var(--brand-hover) !important;
  --n-ripple-color: transparent !important;
  /* 未改动时保存按钮仍是靛色，只降透明度（第 2 轮审查 P1-2：别让「取消」看起来像主操作）。 */
  --n-color-disabled: var(--brand) !important;
  --n-text-color-disabled: var(--brand-fg) !important;
  --n-border-disabled: 1px solid var(--brand) !important;
  --n-opacity-disabled: 0.4 !important;
}

/* 次操作：纸底 + 1px 描边 */
.mh-btn--default {
  --n-color: var(--surface) !important;
  --n-color-hover: var(--surface-hover) !important;
  --n-color-pressed: var(--surface-2) !important;
  --n-color-focus: var(--surface) !important;
  --n-text-color: var(--fg) !important;
  --n-text-color-hover: var(--fg) !important;
  --n-text-color-pressed: var(--fg) !important;
  --n-text-color-focus: var(--fg) !important;
  --n-border: 1px solid var(--border-strong) !important;
  --n-border-hover: 1px solid var(--border-strong) !important;
  --n-border-pressed: 1px solid var(--border-strong) !important;
  --n-border-focus: 1px solid var(--brand) !important;
  --n-ripple-color: transparent !important;
  /* 禁用态：不叠加透明度，文字用 --fg-faint（第 2 轮审查 P1-11：深色下几乎看不见）。 */
  --n-color-disabled: var(--surface-2) !important;
  --n-text-color-disabled: var(--fg-faint) !important;
  --n-border-disabled: 1px solid var(--border) !important;
  --n-opacity-disabled: 1 !important;
}

/* 无边框：工具栏、行内操作 */
.mh-btn--quiet {
  --n-color: transparent !important;
  --n-color-hover: var(--surface-hover) !important;
  --n-color-pressed: var(--surface-2) !important;
  --n-color-focus: transparent !important;
  --n-text-color: var(--fg-2) !important;
  --n-text-color-hover: var(--fg) !important;
  --n-text-color-pressed: var(--fg) !important;
  --n-text-color-focus: var(--fg) !important;
  --n-border: 1px solid transparent !important;
  --n-border-hover: 1px solid transparent !important;
  --n-border-pressed: 1px solid transparent !important;
  --n-border-focus: 1px solid transparent !important;
  --n-ripple-color: transparent !important;
  --n-color-disabled: transparent !important;
  --n-text-color-disabled: var(--fg-faint) !important;
  --n-border-disabled: 1px solid transparent !important;
  --n-opacity-disabled: 1 !important;
}

/* 危险操作：朱色文字；solid 时朱底白字 */
.mh-btn--danger {
  --n-color: var(--surface) !important;
  --n-color-hover: var(--danger-soft) !important;
  --n-color-pressed: var(--danger-soft) !important;
  --n-color-focus: var(--surface) !important;
  --n-text-color: var(--danger) !important;
  --n-text-color-hover: var(--danger) !important;
  --n-text-color-pressed: var(--danger) !important;
  --n-text-color-focus: var(--danger) !important;
  --n-border: 1px solid var(--border-strong) !important;
  --n-border-hover: 1px solid var(--danger) !important;
  --n-border-pressed: 1px solid var(--danger) !important;
  --n-border-focus: 1px solid var(--danger) !important;
  --n-ripple-color: transparent !important;
  --n-color-disabled: var(--surface) !important;
  --n-text-color-disabled: var(--fg-faint) !important;
  --n-border-disabled: 1px solid var(--border) !important;
  --n-opacity-disabled: 1 !important;
}

.mh-btn--danger.mh-btn--solid {
  --n-color: var(--danger) !important;
  --n-color-hover: var(--danger) !important;
  --n-color-pressed: var(--danger) !important;
  --n-color-focus: var(--danger) !important;
  --n-text-color: var(--brand-fg) !important;
  --n-text-color-hover: var(--brand-fg) !important;
  --n-text-color-pressed: var(--brand-fg) !important;
  --n-text-color-focus: var(--brand-fg) !important;
  --n-border: 1px solid var(--danger) !important;
  --n-border-hover: 1px solid var(--danger) !important;
  --n-border-pressed: 1px solid var(--danger) !important;
  --n-border-focus: 1px solid var(--danger) !important;
}

.mh-btn--block {
  width: 100%;
}

/* 高度（10-visual-refresh §1.6）：md 34px / sm 28px；lg 38px 只给登录页这类单按钮场景。 */
.mh-btn--md {
  --n-height: var(--ctl-h) !important;
}

.mh-btn--sm {
  --n-height: var(--ctl-h-sm) !important;
  --n-padding: 0 10px !important;
  --n-icon-size: 14px !important;
}

.mh-btn--lg {
  --n-height: 38px !important;
}

/* 主按钮：品牌实色 + 内部 1px 高光，悬停 --brand-hover（上面的 --n-color-hover） */
.mh-btn--primary:not(.n-button--disabled) {
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, .14), var(--shadow-1);
}

.mh-btn--danger.mh-btn--solid:not(.n-button--disabled) {
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, .14);
}

/* 次按钮：极轻的投影，和表面区分开（深色下靠边框） */
.mh-btn--default:not(.n-button--disabled) {
  box-shadow: var(--shadow-1);
}

/* 聚焦环：键盘聚焦时 3px --ring（Naive 的 focus 只换边框色） */
.mh-btn:focus-visible {
  outline: none;
  box-shadow: 0 0 0 3px var(--ring);
}

/* 按压反馈（11-polish-motion §1）：scale .98，--dur-1。Naive 自己的 transition 列表一并写上，避免被覆盖掉。 */
.mh-btn {
  transition:
    transform var(--dur-1) var(--ease-out),
    color var(--dur-2) var(--ease-out),
    background-color var(--dur-2) var(--ease-out),
    opacity var(--dur-2) var(--ease-out),
    border-color var(--dur-2) var(--ease-out),
    box-shadow var(--dur-2) var(--ease-out);
}

.mh-btn:not(.n-button--disabled):active {
  transform: scale(.98);
}

/* 处理中但还没到 300ms：保持正常外观（disabled 变体色 = 正常色），只换光标 */
.mh-btn--pending {
  --n-opacity-disabled: 1 !important;
  cursor: progress !important;
}

.mh-btn--pending.mh-btn--primary {
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, .14), var(--shadow-1);
}

.mh-btn--pending.mh-btn--default {
  --n-color-disabled: var(--surface) !important;
  --n-text-color-disabled: var(--fg) !important;
  --n-border-disabled: 1px solid var(--border-strong) !important;
}

.mh-btn--pending.mh-btn--quiet {
  --n-text-color-disabled: var(--fg-2) !important;
}

.mh-btn--pending.mh-btn--danger:not(.mh-btn--solid) {
  --n-color-disabled: var(--surface) !important;
  --n-text-color-disabled: var(--danger) !important;
  --n-border-disabled: 1px solid var(--border-strong) !important;
}

.mh-btn--pending.mh-btn--danger.mh-btn--solid {
  --n-color-disabled: var(--danger) !important;
  --n-text-color-disabled: var(--brand-fg) !important;
  --n-border-disabled: 1px solid var(--danger) !important;
}

@media (prefers-reduced-motion: reduce) {
  .mh-btn:not(.n-button--disabled):active {
    transform: none;
  }
}
</style>
