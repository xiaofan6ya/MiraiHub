<script setup lang="ts">
/** 统一替代浏览器原生 title；直接复用插槽触发节点，不改变网格和文本截断布局。 */
import { NPopover } from 'naive-ui'

defineOptions({ inheritAttrs: false, __popover__: true })

withDefaults(
  defineProps<{
    content: string
    disabled?: boolean
    internalSyncTargetWithParent?: boolean
    internalInheritedEventHandlers?: { onClick: () => void; onMouseenter: () => void; onMouseleave: () => void; onFocus: () => void; onBlur: () => void }[]
  }>(),
  { disabled: false },
)
</script>

<template>
  <n-popover trigger="hover" placement="top" :show-arrow="true" :disabled="disabled || !content" :style="{ maxWidth: '320px' }"
    :internal-sync-target-with-parent="internalSyncTargetWithParent"
    :internal-inherited-event-handlers="internalInheritedEventHandlers">
    <template #trigger>
      <slot />
    </template>
    <div class="mh-help__content">{{ content }}</div>
  </n-popover>
</template>

