/**
 * 注意：这里**不要**静态 import 'echarts/core'。
 *
 * 这个模块被 Chart.vue 静态依赖（buildChartTheme 是纯函数），一旦在顶层 import
 * echarts 的任何一个入口，echarts（≈190KB gzip）就会跟着进每个页面的模块图，
 * Chart.vue 里的动态 import 就白做了。需要 registerTheme 的地方自己动态取。
 *
 * 分工（10-visual-refresh §1.8）：
 *   · 网格边距 / 柱宽仍由 components/charts/options.ts 的 apply*Defaults 负责（F2）；
 *   · 主题负责：网格线（--border-soft 虚线）、折线 2px 平滑、图例 8px 圆点、
 *     tooltip（--surface-raised + --shadow-pop）、坐标轴配色；
 *   · 面积渐变：主题里不能给所有折线默认加 areaStyle（会把每条线都填成面积），
 *     需要面积的系列用 areaGradient() 生成 areaStyle.color；
 *   · **横轴本地时区由页面负责**：后端桶键是 UTC，页面用 utils/format 的
 *     bucketLocalParts() / fmtLocalHM() 生成标签，主题不碰数据。
 */
export const CHART_THEME_NAME = 'miraihub'

const cssVar = (name: string): string => {
  if (typeof document === 'undefined' || typeof getComputedStyle !== 'function') return ''
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

/** 浅色兜底（读不到 CSS 变量时，例如测试环境）。 */
const FALLBACK: Readonly<Record<string, string>> = {
  '--chart-1': '#4b657b',
  '--chart-2': '#14866A',
  '--chart-3': '#B7791F',
  '--chart-4': '#7a899e',
  '--chart-5': '#1F8FB3',
  '--chart-6': '#C8382B',
  '--chart-7': '#6B7A2E',
  '--chart-8': '#9AA1AD',
  '--fg': '#171A21',
  '--fg-2': '#454B57',
  '--fg-muted': '#6B7280',
  '--border': '#E3E6EC',
  '--border-soft': '#EDEFF3',
  '--border-strong': '#CDD2DB',
  '--surface-raised': '#FFFFFF',
  '--brand': '#4b657b',
}

function tokenOf(name: string): string {
  return cssVar(name) || FALLBACK[name] || ''
}

const FONT_FALLBACK = '"IBM Plex Sans", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif'

/** 把 #RRGGBB / rgb() / rgba() 转成指定透明度的 rgba；解析不了时原样返回。 */
export function withAlpha(color: string, alpha: number): string {
  const value = String(color || '').trim()
  const a = Math.max(0, Math.min(1, alpha))
  const hex = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(value)
  if (hex) {
    let body = hex[1]
    if (body.length === 3) body = body.split('').map((ch) => ch + ch).join('')
    const r = parseInt(body.slice(0, 2), 16)
    const g = parseInt(body.slice(2, 4), 16)
    const b = parseInt(body.slice(4, 6), 16)
    return `rgba(${r}, ${g}, ${b}, ${a})`
  }
  const rgb = /^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)/i.exec(value)
  if (rgb) return `rgba(${rgb[1]}, ${rgb[2]}, ${rgb[3]}, ${a})`
  return value
}

/** ECharts 线性渐变（纯对象，不需要 import echarts.graphic）。 */
export interface ChartLinearGradient {
  type: 'linear'
  x: number
  y: number
  x2: number
  y2: number
  colorStops: Array<{ offset: number; color: string }>
}

/**
 * 面积渐变：顶部 18% → 底部透明（10-visual-refresh §1.8）。
 * @param color 系列颜色；不传用 --chart-1（品牌色）。
 */
export function areaGradient(color?: string, topAlpha = 0.18): ChartLinearGradient {
  const base = color || tokenOf('--chart-1')
  return {
    type: 'linear',
    x: 0,
    y: 0,
    x2: 0,
    y2: 1,
    colorStops: [
      { offset: 0, color: withAlpha(base, topAlpha) },
      { offset: 1, color: withAlpha(base, 0) },
    ],
  }
}

/** 第 n 个分类色（1 起），图例与系列颜色同源。 */
export function chartColor(index: number): string {
  const n = ((Math.max(1, Math.floor(index)) - 1) % 8) + 1
  return tokenOf('--chart-' + n)
}

/** 图表主题与 tokens.css 同源；主题切换后重新注册并重建图表实例。 */
export function buildChartTheme(): Record<string, unknown> {
  const chartColors = Array.from({ length: 8 }, (_, index) => tokenOf('--chart-' + (index + 1)))
  const fg = tokenOf('--fg')
  const fg2 = tokenOf('--fg-2')
  const fgMuted = tokenOf('--fg-muted')
  const border = tokenOf('--border')
  const borderSoft = tokenOf('--border-soft')
  const borderStrong = tokenOf('--border-strong')
  const surfaceRaised = tokenOf('--surface-raised')
  const fontSans = cssVar('--font-sans') || FONT_FALLBACK

  const axisCommon = {
    axisLine: { lineStyle: { color: border } },
    axisTick: { show: false },
    axisLabel: { color: fgMuted, fontSize: 11, hideOverlap: true },
    splitLine: { show: false },
  }

  return {
    color: chartColors,
    backgroundColor: 'transparent',
    textStyle: { fontFamily: fontSans, fontSize: 11, color: fgMuted },
    title: { textStyle: { color: fg, fontSize: 15, fontWeight: 600 } },
    // 图例：8px 圆点 + 文字（第 1 轮审查 P2「数据看板」：不用默认的 -o- 样式）
    legend: {
      icon: 'circle',
      itemWidth: 8,
      itemHeight: 8,
      itemGap: 16,
      textStyle: { color: fg2, fontSize: 12 },
      inactiveColor: borderStrong,
    },
    tooltip: {
      backgroundColor: surfaceRaised,
      borderColor: border,
      borderWidth: 1,
      padding: [8, 12],
      textStyle: { color: fg, fontSize: 12, fontFamily: fontSans },
      extraCssText: 'box-shadow: var(--shadow-pop); border-radius: 10px;',
      axisPointer: {
        lineStyle: { color: borderStrong, width: 1, type: 'dashed' },
        crossStyle: { color: borderStrong },
      },
    },
    axisPointer: {
      lineStyle: { color: borderStrong, width: 1, type: 'dashed' },
    },
    categoryAxis: axisCommon,
    timeAxis: axisCommon,
    valueAxis: {
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: fgMuted, fontSize: 11 },
      splitLine: { lineStyle: { color: borderSoft, type: 'dashed' } },
    },
    logAxis: {
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: fgMuted, fontSize: 11 },
      splitLine: { lineStyle: { color: borderSoft, type: 'dashed' } },
    },
    // 折线 2px、平滑、默认不画点（悬停时由 emphasis 显示）
    line: {
      smooth: true,
      symbol: 'circle',
      symbolSize: 6,
      showSymbol: false,
      lineStyle: { width: 2 },
      emphasis: { focus: 'none', lineStyle: { width: 2 } },
    },
  }
}

export async function registerChartTheme(): Promise<void> {
  const { registerTheme } = await import('echarts/core')
  registerTheme(CHART_THEME_NAME, buildChartTheme())
}
