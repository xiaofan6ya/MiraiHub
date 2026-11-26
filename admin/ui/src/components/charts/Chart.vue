<script setup lang="ts">
/**
 * ECharts 封装：统一主题 + 默认样式，主题切换时即时重绘。
 * 系列色取 --chart-1..8，字体/坐标轴/网格/tooltip 全部来自 tokens。
 *
 * 加载时机：echarts + zrender + vue-echarts ≈190KB gzip，画出来的像素不参与 LCP
 * （canvas 不是 LCP 候选元素），所以它不该压在首屏上。src/components/index.ts 是统一出口，
 * 任何页面只要从那里 import 组件，Chart 就会进模块图 —— 这里坚持静态 import 的话，
 * 每个页面首屏都要多下这 190KB。改成动态 import 之后：页面骨架/卡片先出，
 * 图表库到位后补画；容器高度由 height 先占位，不产生位移（CLS 不变）。
 *
 * v2.2（11-polish-motion §2）：
 *   · 响应式主题：option 在渲染前经 finalizeChartOption 按**当前主题**重算面积渐变，
 *     页面 computed 不需要依赖主题（修 F2 报告里「切到暗色后面积渐变仍是旧颜色」）。
 *     F1 的 useChartTheme() 落地后，可把 effectiveTheme 换成它的版本号，行为不变。
 *   · 动画 ≤300ms；数据点 >1500 或 prefers-reduced-motion 时关动画。
 *   · tooltip：系列色点 + 数值右对齐 + 完整本地时间标题（option.mhTooltipTitles）。
 *   · 空数据：不画空坐标系，显示 Empty（与卡片同高，不跳动）。
 *   · resize：自己用 ResizeObserver + requestAnimationFrame 合并，一帧最多一次。
 *
 * 第 4 轮（KeepAlive）：
 *   · 页面被缓存（deactivated）时 DOM 已脱离文档，此时切主题若立即按新 key 重建 v-chart，
 *     echarts 会在 0×0 的容器里初始化；回来时尺寸和离开前一样，ResizeObserver 不再触发 → 图表空白。
 *     现在：缓存期间的主题变化只记一笔，回来（activated）时再重建；回来时总是 resize 一次。
 */
import { computed, defineAsyncComponent, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, watch } from 'vue'
import Empty from '@/components/base/Empty.vue'
import { buildChartTheme } from '@/theme/echartsTheme'
import { useTheme } from '@/theme/useTheme'
import { useI18n } from '@/i18n'
import { chartHasData, finalizeChartOption } from '@/components/charts/options'
import type { registerTheme as registerThemeFn } from '@/components/charts/chartRuntime'

const props = withDefaults(
  defineProps<{
    /** ECharts option（建议配合 applyLineDefaults / applyBarDefaults） */
    option: Record<string, unknown>
    height?: string
    loading?: boolean
    autoresize?: boolean
    /** 没有任何有效数据点时的说明；不传用通用「暂无数据」。 */
    emptyText?: string
  }>(),
  { height: '240px', loading: false, autoresize: true, emptyText: undefined },
)

const { effectiveTheme } = useTheme()
const { localeTag } = useI18n()

/** 图表库到位后由异步 loader 赋值；在此之前重建主题是空操作。 */
let registerTheme: typeof registerThemeFn | null = null

let version = 0
const themeName = ref('')

/** KeepAlive 缓存中（页面已离开）：主题重建推迟到回来时。 */
let inactive = false
let themeDirty = false

function rebuildTheme(): void {
  if (!registerTheme) return
  if (inactive) {
    themeDirty = true
    return
  }
  themeDirty = false
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

/* ---------- 减弱动效 ---------- */
const reducedMotion = ref(false)
let motionQuery: MediaQueryList | null = null
const onMotionChange = (event: MediaQueryListEvent): void => {
  reducedMotion.value = event.matches
}

/* ---------- 主题切换：useTheme + html[data-theme] 兜底，同一帧只重建一次 ---------- */
let themeFrame = 0
function scheduleThemeRebuild(): void {
  if (themeFrame) return
  // 等 data-theme 落到 <html> 之后再读 CSS 变量。
  themeFrame = requestAnimationFrame(() => {
    themeFrame = 0
    rebuildTheme()
  })
}

watch(effectiveTheme, scheduleThemeRebuild)

/**
 * 兜底：主题不经 useTheme 改（其它标签页同步、外部脚本、F1 将来的 useChartTheme 之前）时，
 * 监听 <html data-theme> 也能即时重绘。F1 的 useChartTheme() 落地后可删掉这一段。
 */
let themeObserver: MutationObserver | null = null
let lastThemeAttr = ''

/** 渲染用的 option：依赖主题，主题一变就按新 CSS 变量重算渐变。 */
const finalOption = computed(() => {
  void effectiveTheme.value
  void themeName.value
  return finalizeChartOption(props.option, { reducedMotion: reducedMotion.value, locale: localeTag.value })
})

const isEmpty = computed(() => !props.loading && !chartHasData(props.option))

/** 系列数量变化时全量替换，避免残留幽灵系列。 */
const updateOptions = { notMerge: true, lazyUpdate: false }

/* ---------- resize：ResizeObserver + rAF 合并 ---------- */
const root = ref<HTMLElement | null>(null)
const chartRef = ref<{ resize?: () => void } | null>(null)
let observer: ResizeObserver | null = null
let frame = 0
let lastWidth = -1
let lastHeight = -1

function scheduleResize(): void {
  if (frame) return
  frame = requestAnimationFrame(() => {
    frame = 0
    chartRef.value?.resize?.()
  })
}

onMounted(() => {
  // 首帧 CSS 变量可能还没就绪，挂载后再注册一次。
  rebuildTheme()
  if (typeof MutationObserver !== 'undefined' && typeof document !== 'undefined') {
    const html = document.documentElement
    lastThemeAttr = html.getAttribute('data-theme') || ''
    themeObserver = new MutationObserver(() => {
      const next = html.getAttribute('data-theme') || ''
      if (next === lastThemeAttr) return
      lastThemeAttr = next
      scheduleThemeRebuild()
    })
    themeObserver.observe(html, { attributes: true, attributeFilter: ['data-theme'] })
  }
  if (typeof window !== 'undefined' && typeof window.matchMedia === 'function') {
    motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)')
    reducedMotion.value = motionQuery.matches
    motionQuery.addEventListener?.('change', onMotionChange)
  }
  if (props.autoresize && root.value && typeof ResizeObserver !== 'undefined') {
    observer = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect
      if (!rect) return
      // 只在尺寸真的变化时重排（隐藏到 display:none 时 0×0 不处理）。
      if (rect.width === 0 || rect.height === 0) return
      if (Math.round(rect.width) === lastWidth && Math.round(rect.height) === lastHeight) return
      lastWidth = Math.round(rect.width)
      lastHeight = Math.round(rect.height)
      scheduleResize()
    })
    observer.observe(root.value)
  }
})

onDeactivated(() => {
  inactive = true
})

onActivated(() => {
  if (!inactive) return
  inactive = false
  if (themeDirty) rebuildTheme()
  // 离开期间窗口可能变过尺寸，或者容器回来时尺寸与离开前相同（ResizeObserver 不触发）：总是补一次。
  lastWidth = -1
  lastHeight = -1
  scheduleResize()
})

onBeforeUnmount(() => {
  observer?.disconnect()
  observer = null
  themeObserver?.disconnect()
  themeObserver = null
  if (themeFrame) cancelAnimationFrame(themeFrame)
  themeFrame = 0
  if (frame) cancelAnimationFrame(frame)
  frame = 0
  motionQuery?.removeEventListener?.('change', onMotionChange)
})
</script>

<template>
  <div ref="root" class="mh-chart" :style="{ height: props.height }">
    <div v-if="isEmpty" class="mh-chart__empty">
      <Empty compact :description="props.emptyText" />
    </div>
    <component
      :is="VChart"
      v-else
      ref="chartRef"
      :key="themeName"
      class="mh-chart__canvas"
      :option="finalOption"
      :theme="themeName"
      :loading="props.loading"
      :autoresize="false"
      :update-options="updateOptions"
    />
  </div>
</template>

<style scoped>
.mh-chart {
  position: relative;
  width: 100%;
  min-width: 0;
}

.mh-chart__canvas {
  width: 100%;
  height: 100%;
  animation: mh-chart-in var(--dur-2, 180ms) var(--ease-out, cubic-bezier(.16, 1, .3, 1));
}

.mh-chart__empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  border-radius: var(--r-ctl);
  background: var(--surface-2);
}

@keyframes mh-chart-in {
  from { opacity: 0; }
  to { opacity: 1; }
}

@media (prefers-reduced-motion: reduce) {
  .mh-chart__canvas { animation: none; }
}
</style>
