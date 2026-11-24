/**
 * 图表默认样式（04-components §2 + 10-visual-refresh §1.8，纯函数便于复用与测试）。
 *
 * v2.1：
 *   · 折线 2px、平滑、默认不画点；面积用同色渐变 areaGradient（顶部 18% → 透明）；
 *   · 图例统一 8px 圆点 + 文字（第 1 轮审查：不要 ECharts 默认的「-o-」）；
 *   · 数值轴默认用 compact 格式（600,000,000 → 600M）；
 *   · tooltip `confine`，窄卡片里不溢出。
 * 颜色一律不在这里写死：系列色来自主题的 --chart-1..8，图例颜色跟随系列，天然一致。
 */
import { bucketLocalParts, fmtCompact } from '@/utils/format'
import { areaGradient, chartColor } from '@/theme/echartsTheme'

type AnyOption = Record<string, unknown>
type AnySeries = Record<string, unknown>

function mapSeries(option: AnyOption, mapper: (series: AnySeries, index: number) => AnySeries): AnyOption {
  const series = option.series
  if (!Array.isArray(series)) return option
  return { ...option, series: series.map((item, index) => mapper({ ...(item as AnySeries) }, index)) }
}

/** 数值轴刻度：compact（1.2K / 600M）。 */
export function compactAxisLabel(value: number): string {
  return fmtCompact(value)
}

function withAxisDefaults(axis: unknown): unknown {
  const apply = (item: unknown): unknown => {
    if (!item || typeof item !== 'object') return item
    const value = item as AnySeries
    if (value.type !== 'value') return value
    const label = (value.axisLabel as AnySeries) || {}
    return { ...value, axisLabel: { formatter: compactAxisLabel, ...label } }
  }
  return Array.isArray(axis) ? axis.map(apply) : apply(axis)
}

function withCategoryDefaults(axis: unknown): unknown {
  const apply = (item: unknown): unknown => {
    if (!item || typeof item !== 'object') return item
    const value = item as AnySeries
    if (value.type !== 'category') return value
    const label = (value.axisLabel as AnySeries) || {}
    return { ...value, axisLabel: { hideOverlap: true, ...label } }
  }
  return Array.isArray(axis) ? axis.map(apply) : apply(axis)
}

/** 统一网格 / tooltip / 坐标轴 / 图例。 */
export function applyChartDefaults(option: AnyOption): AnyOption {
  const legend = option.legend
  const out: AnyOption = {
    grid: { left: 8, right: 8, top: 32, bottom: 4, containLabel: true, ...(option.grid as AnyOption) },
    ...option,
    tooltip: {
      trigger: 'axis',
      confine: true,
      axisPointer: { type: 'line' },
      ...(option.tooltip as AnyOption),
    },
  }
  if (legend && typeof legend === 'object' && !Array.isArray(legend)) {
    out.legend = { icon: 'circle', itemWidth: 8, itemHeight: 8, itemGap: 16, ...(legend as AnyOption) }
  }
  if (option.yAxis !== undefined) out.yAxis = withAxisDefaults(option.yAxis)
  if (option.xAxis !== undefined) out.xAxis = withCategoryDefaults(option.xAxis)
  return out
}

/**
 * 系列颜色：显式 color / itemStyle.color（字符串）优先，否则按序号取 --chart-n，
 * 与主题调色板同源，图例颜色天然一致。
 */
export function seriesColor(series: AnySeries, index: number): string {
  if (typeof series.color === 'string' && series.color) return series.color
  const itemStyle = series.itemStyle as AnySeries | undefined
  if (itemStyle && typeof itemStyle.color === 'string' && itemStyle.color) return itemStyle.color
  return chartColor(index + 1)
}

/** 折线：线宽 2、平滑、默认不画数据点（悬停才显示）、面积用同色渐变（顶部 18% → 透明，§1.8）。 */
export function applyLineDefaults(option: AnyOption): AnyOption {
  const base = applyChartDefaults(option)
  return mapSeries(base, (series, index) => ({
    type: 'line',
    showSymbol: false,
    symbolSize: 6,
    smooth: true,
    emphasis: { focus: 'series' },
    ...series,
    lineStyle: { width: 2, ...(series.lineStyle as AnySeries) },
    areaStyle:
      series.areaStyle === null
        ? null
        : { color: areaGradient(seriesColor(series, index)), opacity: 1, ...(series.areaStyle as AnySeries) },
  }))
}

/** 柱状：圆角顶 3px、最大宽 24px。折线系列（混合图）只补线宽。 */
export function applyBarDefaults(option: AnyOption): AnyOption {
  const base = applyChartDefaults(option)
  return mapSeries(base, (series) =>
    series.type === 'line'
      ? {
          showSymbol: false,
          smooth: true,
          ...series,
          lineStyle: { width: 2, ...(series.lineStyle as AnySeries) },
        }
      : {
          type: 'bar',
          barMaxWidth: 24,
          ...series,
          itemStyle: { borderRadius: [3, 3, 0, 0], ...(series.itemStyle as AnySeries) },
        },
  )
}

/* ===================================================================== */
/* 11-polish-motion §1 / §2：渲染前统一收尾（Chart.vue 在每次渲染前调用）      */
/* ===================================================================== */

/** 图表动画时长上限（11 §2：≤300ms）。 */
export const CHART_ANIMATION_MS = 260
/** 数据点总数超过这个值就关动画（大数据量下动画只会掉帧）。 */
export const CHART_ANIMATION_POINT_LIMIT = 1500
/** 页面可在 option 上挂这个键，给 tooltip 提供完整时间标题（与 xAxis.data 一一对应），渲染前会被剥掉。 */
export const TOOLTIP_TITLES_KEY = 'mhTooltipTitles'

const pad2 = (value: number): string => String(value).padStart(2, '0')

/** 本地时区偏移文本（UTC+08:00）；date 决定夏令时等偏移。 */
export function localOffsetLabel(date: Date = new Date()): string {
  const minutes = -date.getTimezoneOffset()
  const abs = Math.abs(minutes)
  return 'UTC' + (minutes >= 0 ? '+' : '-') + pad2(Math.floor(abs / 60)) + ':' + pad2(abs % 60)
}

/**
 * 时间桶键 → tooltip 的完整标题（11 §3「悬停显示完整时间」）：
 *   · 小时桶（UTC 键）→ 本地时区「2026-11-23 23:00 UTC+08:00」；
 *   · 天桶「2026-11-23」等其它键原样返回（没有时刻，无法换算）。
 * 页面 / api 把结果数组挂到 option.mhTooltipTitles，与 xAxis.data 一一对应。
 */
export function bucketTooltipTitle(key: unknown): string {
  const text = String(key ?? '').trim()
  const parts = bucketLocalParts(text)
  if (!parts) return text
  const local = new Date(parts.y, parts.mo - 1, parts.d, parts.h, parts.mi)
  return (
    parts.y + '-' + pad2(parts.mo) + '-' + pad2(parts.d) + ' ' + pad2(parts.h) + ':' + pad2(parts.mi) +
    ' ' + localOffsetLabel(local)
  )
}

export interface ChartFinalizeContext {
  /** prefers-reduced-motion: reduce 时关闭动画。 */
  reducedMotion?: boolean
  /** BCP-47 标签，tooltip 数值格式化用。 */
  locale?: string
}

function seriesList(option: AnyOption): AnySeries[] {
  return Array.isArray(option.series) ? (option.series as AnySeries[]) : []
}

function pointValue(item: unknown): number | null {
  let value: unknown = item
  if (item && typeof item === 'object' && !Array.isArray(item)) value = (item as AnySeries).value
  if (Array.isArray(value)) value = value[value.length - 1]
  if (value === null || value === undefined || value === '' || value === '-') return null
  const num = Number(value)
  return Number.isFinite(num) ? num : null
}

/** 所有系列数据点数之和（决定要不要关动画）。 */
export function chartPointCount(option: AnyOption): number {
  return seriesList(option).reduce((sum, series) => sum + (Array.isArray(series.data) ? series.data.length : 0), 0)
}

/** 至少有一个系列带一个有效数值（否则 Chart.vue 画空状态，而不是一张空坐标系）。 */
export function chartHasData(option: AnyOption): boolean {
  return seriesList(option).some(
    (series) => Array.isArray(series.data) && series.data.some((item) => pointValue(item) !== null),
  )
}

function isLinearGradient(value: unknown): boolean {
  return !!value && typeof value === 'object' && (value as AnySeries).type === 'linear'
}

const escapeHtml = (text: unknown): string =>
  String(text ?? '').replace(/[&<>"']/g, (ch) =>
    ch === '&' ? '&amp;' : ch === '<' ? '&lt;' : ch === '>' ? '&gt;' : ch === '"' ? '&quot;' : '&#39;',
  )

/** tooltip 默认数值格式：千分位，最多 2 位小数；无值 —。 */
export function tooltipNumber(value: unknown, locale?: string): string {
  const num = pointValue(value)
  if (num === null) return '—'
  try {
    return num.toLocaleString(locale || undefined, { maximumFractionDigits: 2 })
  } catch {
    return String(num)
  }
}

export interface TooltipParam {
  seriesName?: string
  value?: unknown
  data?: unknown
  color?: unknown
  dataIndex?: number
  axisValueLabel?: string
  name?: string
}

type ValueFormatter = (value: unknown, dataIndex?: number) => string

/**
 * 轴触发 tooltip 的 HTML（11 §3 / 任务 3）：
 *   标题 = 完整本地时间（option.mhTooltipTitles[dataIndex]，没有就用横轴标签）；
 *   每行 = 系列色点 + 名称（左） + 数值（右对齐、tabular-nums）。
 * 颜色全部用 CSS 变量 —— tooltip 是 DOM 节点，主题切换时自动跟随。
 */
export function buildAxisTooltip(
  params: TooltipParam | TooltipParam[],
  titles: string[] | null,
  locale?: string,
  valueFormatter?: ValueFormatter,
): string {
  const list = (Array.isArray(params) ? params : [params]).filter(Boolean)
  if (!list.length) return ''
  const first = list[0]
  const index = typeof first.dataIndex === 'number' ? first.dataIndex : -1
  const title = (titles && index >= 0 && titles[index]) || first.axisValueLabel || first.name || ''
  const rows = list
    .map((item) => {
      const color = typeof item.color === 'string' && item.color ? item.color : 'var(--fg-muted)'
      const raw = item.value !== undefined ? item.value : item.data
      const text = valueFormatter ? valueFormatter(raw, item.dataIndex) : tooltipNumber(raw, locale)
      return (
        '<div style="display:flex;align-items:center;gap:8px;min-width:168px;line-height:20px">' +
        `<span style="width:8px;height:8px;border-radius:50%;flex:none;background:${escapeHtml(color)}"></span>` +
        `<span style="flex:1;min-width:0;color:var(--fg-2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(item.seriesName)}</span>` +
        `<span style="margin-left:16px;font-variant-numeric:tabular-nums;font-weight:500;color:var(--fg);text-align:right">${escapeHtml(text)}</span>` +
        '</div>'
      )
    })
    .join('')
  const head = title
    ? `<div style="margin-bottom:4px;font-size:var(--fs-tiny,11px);color:var(--fg-muted);font-variant-numeric:tabular-nums">${escapeHtml(title)}</div>`
    : ''
  return head + rows
}

/**
 * 渲染前收尾（纯函数，Chart.vue 每次渲染 / 主题切换时调用）：
 *   1. 面积渐变按**当前主题**重算（解决「切到暗色后面积仍是浅色渐变」：页面 computed 不依赖主题）；
 *   2. 动画 ≤300ms、easing cubicOut；数据点 > 1500 或减弱动效时关动画；
 *   3. 轴触发 tooltip 默认用 buildAxisTooltip（页面自己给了 formatter 的不动）；
 *   4. 剥掉页面附带的 mhTooltipTitles。
 */
export function finalizeChartOption(option: AnyOption, ctx: ChartFinalizeContext = {}): AnyOption {
  const { [TOOLTIP_TITLES_KEY]: rawTitles, ...rest } = option
  const titles = Array.isArray(rawTitles) ? rawTitles.map((item) => String(item ?? '')) : null
  const out: AnyOption = mapSeries(rest, (series, index) => {
    const area = series.areaStyle as AnySeries | null | undefined
    if (area && typeof area === 'object' && isLinearGradient(area.color)) {
      return { ...series, areaStyle: { ...area, color: areaGradient(seriesColor(series, index)) } }
    }
    return series
  })

  const heavy = chartPointCount(rest) > CHART_ANIMATION_POINT_LIMIT
  if (ctx.reducedMotion || heavy || rest.animation === false) {
    out.animation = false
  } else {
    const cap = (value: unknown): number =>
      Math.min(300, Number.isFinite(Number(value)) && Number(value) > 0 ? Number(value) : CHART_ANIMATION_MS)
    out.animation = true
    out.animationDuration = cap(rest.animationDuration)
    out.animationDurationUpdate = cap(rest.animationDurationUpdate)
    out.animationEasing = rest.animationEasing ?? 'cubicOut'
    out.animationEasingUpdate = rest.animationEasingUpdate ?? 'cubicOut'
  }

  const tooltip = rest.tooltip as AnySeries | undefined
  if (tooltip && typeof tooltip === 'object' && (tooltip.trigger ?? 'axis') === 'axis' && !tooltip.formatter) {
    const valueFormatter = typeof tooltip.valueFormatter === 'function' ? (tooltip.valueFormatter as ValueFormatter) : undefined
    out.tooltip = {
      ...tooltip,
      transitionDuration: 0.15,
      formatter: (params: TooltipParam | TooltipParam[]) => buildAxisTooltip(params, titles, ctx.locale, valueFormatter),
    }
  }
  return out
}
