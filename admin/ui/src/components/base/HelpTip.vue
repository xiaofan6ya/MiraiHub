<script setup lang="ts">
/** 收纳旧版页面上大段灰色说明：CircleHelp 14px + 弹层，最大宽 320px。 */
import { CircleHelp } from 'lucide-vue-next'
import { NPopover } from 'naive-ui'

withDefaults(
  defineProps<{
    /** 无插槽时的说明文字。 */
    content?: string
    label?: string
  }>(),
  { content: undefined, label: '说明' },
)
</script>

<template>
  <n-popover trigger="hover" placement="top" :show-arrow="true" :style="{ maxWidth: '320px' }">
    <template #trigger>
      <button class="mh-help" type="button" :aria-label="label">
        <CircleHelp :size="14" />
      </button>
    </template>
    <div class="mh-help__content">
      <slot>{{ content }}</slot>
    </div>
  </n-popover>
</template>

<style scoped>
.mh-help {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--fg-faint);
  cursor: help;
  border-radius: 50%;
  flex: none;
}

.mh-help:hover {
  color: var(--fg-muted);
}

.mh-help__content {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-2);
  max-width: 320px;
}
</style>
