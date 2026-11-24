/**
 * 迷你折线的几何计算（纯函数，Sparkline.vue 与单测共用）。
 * 坐标系：x ∈ [0, width]，y ∈ [0, height]，y 向下；上下各留 pad 像素，线不会被裁掉一半。
 */

export type SparkTone = 'brand' | 'ok' | 'warn' | 'danger' | 'neutral'

export const SPARK_TONE_COLORS: Record<SparkTone, string> = {
  brand: 'var(--brand)',
  ok: 'var(--ok)',
  warn: 'var(--warn)',
  danger: 'var(--danger)',
  neutral: 'var(--fg-muted)',
}

export function sparkToneColor(tone?: string | null): string {
  const key = String(tone || 'brand') as SparkTone
  return SPARK_TONE_COLORS[key] ?? SPARK_TONE_COLORS.brand
}

export interface SparkPoint {
  x: number
  y: number
}

export interface SparkGeometry {
  points: SparkPoint[]
  /** 折线 path（M … L …）；没有数据时为空串。 */
  line: string
  /** 面积 path（折线 + 底边闭合）；没有数据时为空串。 */
  area: string
  /** 全部为 0 / 全部相同：画成贴底（或居中）的一条平线。 */
  flat: boolean
}

const round = (value: number): number => Math.round(value * 100) / 100

/** 非数一律当 0（后端缺桶时给 null），保证点数与时间轴一致。 */
export function cleanSeries(data: ReadonlyArray<unknown> | null | undefined): number[] {
  if (!Array.isArray(data)) return []
  return data.map((value) => {
    const n = typeof value === 'number' ? value : Number(value)
    return Number.isFinite(n) ? n : 0
  })
}

export function sparkGeometry(
  data: ReadonlyArray<unknown> | null | undefined,
  width = 100,
  height = 28,
  pad = 2,
): SparkGeometry {
  const series = cleanSeries(data)
  if (series.length === 0) return { points: [], line: '', area: '', flat: true }
  // 只有一个点：画成一条平线（两端同值），避免什么都不显示。
  const values = series.length === 1 ? [series[0], series[0]] : series
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min
  const flat = span === 0
  const usable = Math.max(1, height - pad * 2)
  const step = values.length > 1 ? width / (values.length - 1) : width
  const points = values.map((value, index) => {
    const x = round(index * step)
    // 全部相同：全 0 贴底（"没有流量"），否则居中。
    const ratio = flat ? (max === 0 ? 0 : 0.5) : (value - min) / span
    const y = round(pad + usable * (1 - ratio))
    return { x, y }
  })
  const line = points.map((point, index) => (index ? 'L' : 'M') + point.x + ' ' + point.y).join(' ')
  const last = points[points.length - 1]
  const first = points[0]
  const area = line + ' L' + last.x + ' ' + height + ' L' + first.x + ' ' + height + ' Z'
  return { points, line, area, flat }
}
