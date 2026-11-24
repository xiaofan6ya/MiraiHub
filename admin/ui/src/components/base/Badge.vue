<script setup lang="ts">
/**
 * 状态徽章（10-visual-refresh §1.6）：高 22px、圆角 --r-chip（6px）、soft 底 + 状态色文字、12px/500；
 * dot 变体前面带 6px 圆点（表格状态列「圆点 + 文字」用它）。状态与颜色的映射全站一致。
 */
import { computed } from 'vue'
import { badgeToneStyle, type BadgeTone } from './badge'

const props = withDefaults(
  defineProps<{
    tone?: BadgeTone
    /** 左侧 6px 状态圆点。 */
    dot?: boolean
  }>(),
  { tone: 'neutral', dot: false },
)

const style = computed(() => badgeToneStyle(props.tone))
</script>

<template>
  <span class="mh-badge" :style="{ color: style.text, background: style.bg }">
    <i v-if="dot" class="mh-badge__dot" :style="{ background: style.text }" aria-hidden="true" />
    <slot />
  </span>
</template>

<style scoped>
.mh-badge {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-1);
  height: 22px;
  padding: 0 var(--sp-2);
  border-radius: var(--r-chip);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  font-weight: 500;
  white-space: nowrap;
  vertical-align: middle;
  font-variant-numeric: tabular-nums;
}

.mh-badge__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex: none;
}
</style>
