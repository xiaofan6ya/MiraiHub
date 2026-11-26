<script setup lang="ts">
/**
 * 8px 状态圆点 + 可选文字。
 * pulse 保留做兼容，但**不再闪烁**（10-visual-refresh §1.9：不允许自发闪烁的状态点）：
 * 改成一圈静态的 3px 同色光环，表示「正在运行」。
 */
import { computed } from 'vue'
import { badgeToneStyle, type BadgeTone } from './badge'

const props = withDefaults(
  defineProps<{
    tone?: BadgeTone
    label?: string
    pulse?: boolean
  }>(),
  { tone: 'neutral', label: undefined, pulse: false },
)

const color = computed(() => badgeToneStyle(props.tone).text)
</script>

<template>
  <span class="mh-status" :class="{ 'mh-status--pulse': pulse }">
    <i class="mh-status__dot" :style="{ background: color }" aria-hidden="true" />
    <span v-if="label || $slots.default" class="mh-status__label">
      <slot>{{ label }}</slot>
    </span>
  </span>
</template>

<style scoped>
.mh-status {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-2);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-2);
}

.mh-status__dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex: none;
}

.mh-status--pulse .mh-status__dot {
  box-shadow: 0 0 0 3px var(--brand-soft);
}
</style>
