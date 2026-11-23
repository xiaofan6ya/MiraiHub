<script setup lang="ts">
/**
 * ECharts 封装：统一主题 + 默认样式，主题切换时重建实例。
 * 系列色取 --chart-1..8，字体/坐标轴/网格/tooltip 全部来自 tokens。
 *
 * 加载时机：echarts + zrender + vue-echarts ≈190KB gzip，画出来的像素不参与 LCP
 * （canvas 不是 LCP 候选元素），所以它不该压在首屏上。src/components/index.ts 是统一出口，
 * 任何页面只要从那里 import 组件，Chart 就会进模块图 —— 这里坚持静态 import 的话，
 * 每个页面首屏都要多下这 190KB。改成动态 import 之后：页面骨架/卡片先出，
 * 图表库到位后补画；容器高度由 height 先占位，不产生位移（CLS 不变）。
 */
import { defineAsyncComponent, onMounted, ref, watch } from 'vue'
import { buildChartTheme } from '@/theme/echartsTheme'
import { useTheme } from '@/theme/useTheme'
import type { registerTheme as registerThemeFn } from '@/components/charts/chartRuntime'

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

/** 图表库到位后由异步 loader 赋值；在此之前重建主题是空操作。 */
let registerTheme: typeof registerThemeFn | null = null

let version = 0
const themeName = ref('')

function rebuildTheme(): void {
  if (!registerTheme) return
  version += 1
  themeName.value = 'miraihub-' + version
  registerTheme(themeName.value, buildChartTheme())
}

/** 只有真正要画图时才解析 vue-echarts / echarts（chartRuntime 是纯静态登记的模块）。 */
const VChart = defineAsyncComponent(async () => {
  const runtime = await import('@/components/charts/chartRuntime')
  registerTheme = runtime.registerTheme
  // 主题必须在 v-chart 首次渲染前注册好。
  rebuildTheme()
  return runtime.VChart
})

watch(effectiveTheme, () => {
  // 等 data-theme 落到 <html> 之后再读 CSS 变量。
  requestAnimationFrame(() => rebuildTheme())
})

onMounted(() => {
  // 首帧 CSS 变量可能还没就绪，挂载后再注册一次。
  rebuildTheme()
})

/** 系列数量变化时全量替换，避免残留幽灵系列。 */
const updateOptions = { notMerge: true, lazyUpdate: false }
</script>

<template>
  <div class="mh-chart" :style="{ height: props.height }">
    <component
      :is="VChart"
      :key="themeName"
      class="mh-chart__canvas"
      :option="props.option"
      :theme="themeName"
      :loading="props.loading"
      :autoresize="props.autoresize"
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
