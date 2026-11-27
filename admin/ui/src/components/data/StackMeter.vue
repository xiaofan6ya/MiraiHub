<script setup lang="ts">
/** 多段堆叠进度（如到期风险分布），下方图例。 */
import { computed } from 'vue'
import TitleTip from '../base/TitleTip.vue'
import { badgeToneStyle } from '../base/badge'
import type { StackMeterItem } from './stackmeter'

const props = withDefaults(
  defineProps<{
    items: StackMeterItem[]
    /** 总量：默认取各段之和；用于「占比」的分母（如含未展示项） */
    total?: number
    height?: number
    unit?: string
    showLegend?: boolean
  }>(),
  { total: undefined, height: 8, unit: '', showLegend: true },
)

const sum = computed(() => props.items.reduce((acc, item) => acc + (Number(item.value) || 0), 0))
const denominator = computed(() => {
  const value = props.total ?? sum.value
  return value > 0 ? value : 0
})

function widthOf(item: StackMeterItem): string {
  if (!denominator.value) return '0%'
  const ratio = Math.min(1, Math.max(0, (Number(item.value) || 0) / denominator.value))
  return ratio * 100 + '%'
}

function colorOf(item: StackMeterItem): string {
  return badgeToneStyle(item.tone ?? 'brand').text
}
</script>

<template>
  <div class="mh-stack">
    <div class="mh-stack__bar" :style="{ height: height + 'px' }">
      <TitleTip v-for="item in items" :key="item.label" :content="item.label + ' ' + item.value + unit"><span
        class="mh-stack__seg"
        :style="{ width: widthOf(item), background: colorOf(item) }"
      /></TitleTip>
    </div>
    <ul v-if="showLegend" class="mh-stack__legend">
      <li v-for="item in items" :key="item.label" class="mh-stack__legend-item">
        <i class="mh-stack__dot" :style="{ background: colorOf(item) }" aria-hidden="true" />
        <span class="mh-stack__label">{{ item.label }}</span>
        <span class="mh-stack__value tnum">{{ item.value }}{{ unit }}</span>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.mh-stack {
  display: grid;
  gap: var(--sp-3);
  min-width: 0;
}

.mh-stack__bar {
  display: flex;
  width: 100%;
  border-radius: 50px;
  overflow: hidden;
  background: var(--border-soft);
}

.mh-stack__seg {
  height: 100%;
}

.mh-stack__legend {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-2) var(--sp-4);
}

.mh-stack__legend-item {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-2);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.mh-stack__dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex: none;
}

.mh-stack__value {
  color: var(--fg-2);
}
</style>
