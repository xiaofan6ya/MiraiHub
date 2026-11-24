<script setup lang="ts">
/**
 * 迷你折线（10-visual-refresh §3）：纯 SVG，不依赖 echarts，KPI 卡和表格单元格都能用。
 * 宽度跟随容器（100%），高度默认 28px；面积用同色到透明的渐变。
 */
import { computed } from 'vue'
import { sparkGeometry, sparkToneColor, type SparkTone } from './sparkline'

const props = withDefaults(
  defineProps<{
    data: number[]
    tone?: SparkTone
    /** 像素高度，默认 28。 */
    height?: number
    /** 是否画面积渐变，默认 true。 */
    area?: boolean
  }>(),
  { tone: 'brand', height: 28, area: true },
)

/** viewBox 宽度固定 100，配合 preserveAspectRatio="none" 横向拉伸；线宽用 non-scaling-stroke 保持 1.5px。 */
const VIEW_W = 100

/** 每个实例一个渐变 id（同页多个 Sparkline 不能共用 id，否则颜色串台）。 */
const gradientId = 'mh-spark-' + Math.random().toString(36).slice(2, 10)

const h = computed(() => Math.max(8, Math.round(Number(props.height) || 28)))
const geometry = computed(() => sparkGeometry(props.data, VIEW_W, h.value, 2))
const color = computed(() => sparkToneColor(props.tone))
const hasData = computed(() => geometry.value.points.length > 0)
</script>

<template>
  <svg
    class="mh-spark"
    :class="{ 'is-flat': geometry.flat }"
    :viewBox="`0 0 ${VIEW_W} ${h}`"
    :height="h"
    preserveAspectRatio="none"
    aria-hidden="true"
    focusable="false"
  >
    <defs v-if="area && hasData">
      <linearGradient :id="gradientId" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" :stop-color="color" stop-opacity="0.18" />
        <stop offset="100%" :stop-color="color" stop-opacity="0" />
      </linearGradient>
    </defs>
    <path
      v-if="area && hasData && !geometry.flat"
      class="mh-spark__area"
      :d="geometry.area"
      :fill="`url(#${gradientId})`"
    />
    <path
      v-if="hasData"
      class="mh-spark__line"
      :d="geometry.line"
      :stroke="geometry.flat ? 'var(--border-strong)' : color"
      fill="none"
      vector-effect="non-scaling-stroke"
    />
  </svg>
</template>

<style scoped>
.mh-spark {
  display: block;
  width: 100%;
  overflow: visible;
}

.mh-spark__line {
  stroke-width: 1.5;
  stroke-linecap: round;
  stroke-linejoin: round;
}
</style>
