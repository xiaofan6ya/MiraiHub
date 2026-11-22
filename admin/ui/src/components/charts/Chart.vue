<script setup lang="ts">
/**
 * ECharts 封装：统一主题 + 默认样式，主题切换时重建实例。
 * 系列色取 --chart-1..8，字体/坐标轴/网格/tooltip 全部来自 tokens。
 */
import { onMounted, ref, watch } from 'vue'
import VChart from 'vue-echarts'
import 'vue-echarts/style.css'
import { registerTheme, use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart, LineChart } from 'echarts/charts'
import {
  DatasetComponent,
  GridComponent,
  LegendComponent,
  TitleComponent,
  TooltipComponent,
} from 'echarts/components'
import { buildChartTheme } from '@/theme/echartsTheme'
import { useTheme } from '@/theme/useTheme'

use([
  CanvasRenderer,
  LineChart,
  BarChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DatasetComponent,
  TitleComponent,
])

const props = withDefaults(
  defineProps<{
    /** ECharts option（建议配合 applyLineDefaults / applyBarDefaults） */
    option: Record<string, unknown>
    height?: string
    loading?: boolean
    autoresize?: boolean
  }>(),
  { height: '240px', loading: false, autoresize: true },
)

const { effectiveTheme } = useTheme()

let version = 0
const themeName = ref('')

function rebuildTheme(): void {
  version += 1
  themeName.value = 'miraihub-' + version
  registerTheme(themeName.value, buildChartTheme())
}

rebuildTheme()

watch(effectiveTheme, () => {
  // 等 data-theme 落到 <html> 之后再读 CSS 变量。
  requestAnimationFrame(() => rebuildTheme())
})

onMounted(() => {
  // 首帧 CSS 变量可能还没就绪，挂载后再注册一次。
  if (!themeName.value) rebuildTheme()
})

/** 系列数量变化时全量替换，避免残留幽灵系列。 */
const updateOptions = { notMerge: true, lazyUpdate: false }
</script>

<template>
  <div class="mh-chart" :style="{ height }">
    <v-chart
      :key="themeName"
      class="mh-chart__canvas"
      :option="option"
      :theme="themeName"
      :loading="loading"
      :autoresize="autoresize"
      :update-options="updateOptions"
    />
  </div>
</template>

<style scoped>
.mh-chart {
  width: 100%;
  min-width: 0;
}

.mh-chart__canvas {
  width: 100%;
  height: 100%;
}
</style>
