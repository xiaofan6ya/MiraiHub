/** 线路带的纯计算：线宽、健康度配色、脉冲周期（可直接单测）。 */
import type { BadgeTone } from '../base/badge'

/** 线宽 = 2 + 18 × 占比（px）。 */
export const RIBBON_MIN_WIDTH = 2
export const RIBBON_MAX_EXTRA = 18
/** 每条线路的行高。 */
export const RIBBON_LANE_HEIGHT = 36
/** 压缩后的最小行高（再小行内 logo + 文字就挤了）。 */
export const RIBBON_LANE_MIN_HEIGHT = 22

/**
 * 行高：不给 maxHeight 时固定 36px（旧行为）；给了就按线路数压缩到能放下，
 * 下限 22px —— 超过 maxHeight 时宁可略高，也不让文字重叠。
 */
export function ribbonLaneHeight(count: number, maxHeight?: number | null): number {
  const n = Math.max(1, Math.floor(Number(count) || 0))
  const max = Number(maxHeight)
  if (!Number.isFinite(max) || max <= 0) return RIBBON_LANE_HEIGHT
  return Math.max(
    RIBBON_LANE_MIN_HEIGHT,
    Math.min(RIBBON_LANE_HEIGHT, Math.floor(max / n)),
  )
}

export type RibbonHealth = 'ok' | 'warn' | 'danger' | 'disabled'

export interface RibbonLane {
  id: string
  name: string
  /** 平台 icon 资源键（PlatformLogo 的 id）。 */
  logo?: string
  /** 次数（metric=count 时显示） */
  count: number
  /** token 数（metric=tokens 时显示） */
  tokens?: number
  /** 成功率：0–1 或 0–100 都接受 */
  successRate?: number | null
  /** 后端状态文案（仅展示用） */
  status?: string
  disabled?: boolean
  /** 调度顺序第 1 位：brand 描边 +「首选」徽章 */
  preferred?: boolean
}

/** 线宽：占比 = count / total；无流量时退回 2px。 */
export function lineWidth(count: number, total: number): number {
  const c = Number.isFinite(count) ? Number(count) : 0
  const t = Number.isFinite(total) ? Number(total) : 0
  if (c <= 0 || t <= 0) return RIBBON_MIN_WIDTH
  const ratio = Math.min(1, c / t)
  return RIBBON_MIN_WIDTH + RIBBON_MAX_EXTRA * ratio
}

/** 占比（0–1），用于条形列表。 */
export function laneRatio(count: number, total: number): number {
  const c = Number.isFinite(count) ? Number(count) : 0
  const t = Number.isFinite(total) ? Number(total) : 0
  if (c <= 0 || t <= 0) return 0
  return Math.min(1, c / t)
}

/** 成功率归一成 0–100；≤1 视为比值（与后端字段两种写法都兼容）。 */
export function normalizeRate(value?: number | null): number | null {
  if (value === null || value === undefined || !Number.isFinite(value)) return null
  const rate = Number(value)
  if (rate < 0) return 0
  if (rate <= 1) return rate * 100
  return Math.min(100, rate)
}

/** ≥90 健康 / 60–90 警告 / <60 危险 / 已禁用或无数灰线。 */
export function healthOf(lane: Pick<RibbonLane, 'disabled' | 'successRate'>): RibbonHealth {
  if (lane?.disabled) return 'disabled'
  const rate = normalizeRate(lane?.successRate)
  if (rate === null) return 'disabled'
  if (rate >= 90) return 'ok'
  if (rate >= 60) return 'warn'
  return 'danger'
}

const HEALTH_COLORS: Record<RibbonHealth, string> = {
  ok: 'var(--ok)',
  warn: 'var(--warn)',
  danger: 'var(--danger)',
  disabled: 'var(--fg-faint)',
}

export function healthColor(health: RibbonHealth): string {
  return HEALTH_COLORS[health] ?? HEALTH_COLORS.disabled
}

/** 健康度 → 徽章语气（成功率文字着色、状态圆点）。 */
export function healthTone(health: RibbonHealth): BadgeTone {
  if (health === 'ok') return 'ok'
  if (health === 'warn') return 'warn'
  if (health === 'danger') return 'danger'
  return 'neutral'
}

/** 脉冲周期（秒）= clamp(1.2, 6 / ln(每分钟请求数), 6)；无流量返回 0（不显示）。 */
export function pulseSeconds(perMinute: number): number {
  const rpm = Number.isFinite(perMinute) ? Number(perMinute) : 0
  if (rpm <= 1) return rpm <= 0 ? 0 : 6
  const seconds = 6 / Math.log(rpm)
  return Math.min(6, Math.max(1.2, seconds))
}

/** 供 SVG animateMotion 使用的 dur 值。 */
export function pulseDuration(perMinute: number): string {
  const seconds = pulseSeconds(perMinute)
  return seconds > 0 ? seconds.toFixed(2) + 's' : '0s'
}

/** 是否「暂无流量」：总量为 0 或所有线路为 0。 */
export function isEmptyRibbon(total: number, lanes: Array<Pick<RibbonLane, 'count'>>): boolean {
  const sum = lanes.reduce((acc, lane) => acc + (Number.isFinite(lane.count) ? lane.count : 0), 0)
  return sum <= 0 || !(Number(total) > 0)
}
