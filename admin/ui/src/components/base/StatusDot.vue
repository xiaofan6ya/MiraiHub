<script setup lang="ts">
/** 8px 状态圆点 + 可选文字；pulse 只用于「运行中」。 */
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
  animation: mh-status-pulse 1.6s var(--ease) infinite;
}

@keyframes mh-status-pulse {
  0% {
    box-shadow: 0 0 0 0 var(--brand-soft);
  }
  70% {
    box-shadow: 0 0 0 4px var(--brand-soft);
  }
  100% {
    box-shadow: 0 0 0 0 var(--brand-soft);
  }
}

@media (prefers-reduced-motion: reduce) {
  .mh-status--pulse .mh-status__dot {
    animation: none;
  }
}
</style>
