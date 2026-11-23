/**
 * 平台网关（/api/gateway/*）与平台速度（/api/platform/speed）。
 *
 * 后端：
 *   · admin/platform/gateway_api.py —— get_config / put_config / get_health / resolve / order / disable / enable
 *   · admin/platform/gateway_router.py —— STRATEGIES / routing_config / save_routing / health / order
 *   · admin/platform/bench_router.py —— GET /api/platform/speed
 *
 * 字段名与后端完全一致（snake_case 原样保留）。
 * 策略文案、顺序、原因全部**以后端返回为准**，前端不复刻排序逻辑（见 gateway_router.order）。
 */
import { http } from './http'
import { FMT_EMPTY, fmtDuration, fmtInt } from '@/utils/format'

export interface GatewayStrategyOption {
  value: string
  label: string
  description: string
}

export interface GatewayDisabledInfo {
  at?: number
  reason?: string
}

export interface GatewayPlatformMeta {
  id: string
  label: string
  /** 该平台是否提供平台网关（spec.platform_gateway）。 */
  managed: boolean
}

/** `GET /api/gateway/config`（gateway_router.routing_config + platforms）。 */
export interface GatewayConfig {
  strategy: string
  /** 平台 -> 优先级（数字小优先；未设置的按 100 处理）。 */
  priority: Record<string, number>
  disabled: Record<string, GatewayDisabledInfo>
  /** 无头请求（不带 X-Platform / 无绑定 Key）是否参与负载分摊，默认 false。 */
  spread_unbound: boolean
  strategies: GatewayStrategyOption[]
  platforms: GatewayPlatformMeta[]
}

export interface GatewaySaveInput {
  strategy?: string
  priority?: Record<string, number>
  spread_unbound?: boolean
  disabled?: Record<string, boolean>
}

export interface GatewaySaveResult {
  ok: boolean
  config: GatewayConfig
}

/** 单平台健康度（gateway_router.health 的真实字段）。 */
export interface GatewayHealthItem {
  platform: string
  label: string
  ok: boolean
  accounts: number
  active: number
  cooling: number
  usable: number
  balance_remain: number
  balance_total: number
  /** 剩余比例（0–1）；总额为 0 时为 null（不冒充 0%）。 */
  balance_ratio: number | null
  soonest_expire_at: number | null
  disabled: boolean
}

/** `GET /api/gateway/order` 的一行：健康度 + 是否可用 + 排序原因。 */
export interface GatewayOrderItem extends GatewayHealthItem {
  available: boolean
  reason: string
}

export interface GatewayOrderPayload {
  items: GatewayOrderItem[]
}

export interface GatewayHealthPayload {
  items: GatewayHealthItem[]
  strategy: string
}

/** `GET /api/platform/speed` 的一行（bench_router.platform_speed）。 */
export interface PlatformSpeedItem {
  platform: string
  label?: string
  requests: number
  ok_requests?: number
  /** 0–100 的百分比（后端已 ×100）。 */
  success_rate: number
  ttfb_p50?: number
  ttfb_avg?: number
  latency_p50?: number
  latency_p95?: number
  latency_avg?: number
  tps_p50?: number
  tps_avg?: number
  jitter?: number
  samples?: number
  low_sample?: boolean
  score?: number
  rank?: number
}

export interface PlatformSpeedPayload {
  hours: number
  model?: string
  items: PlatformSpeedItem[]
  fastest?: string
  steadiest?: string
  observed_at?: string
}

/* ------------------------------------------------------------------ *
 * 接口
 * ------------------------------------------------------------------ */

export function getGatewayConfig(signal?: AbortSignal): Promise<GatewayConfig> {
  return http<GatewayConfig>('GET', '/api/gateway/config', { signal })
}

export function saveGatewayConfig(input: GatewaySaveInput): Promise<GatewaySaveResult> {
  return http<GatewaySaveResult>('PUT', '/api/gateway/config', { body: input })
}

export function getGatewayOrder(signal?: AbortSignal): Promise<GatewayOrderPayload> {
  return http<GatewayOrderPayload>('GET', '/api/gateway/order', { signal })
}

export function getGatewayHealth(signal?: AbortSignal): Promise<GatewayHealthPayload> {
  return http<GatewayHealthPayload>('GET', '/api/gateway/health', { signal })
}

/** 禁用 / 恢复某平台网关（禁用只影响调度，账号数据保留）。 */
export function setGatewayEnabled(
  platform: string,
  enabled: boolean,
  reason = '',
): Promise<{ ok: boolean; platform: string; enabled: boolean }> {
  const action = enabled ? 'enable' : 'disable'
  return http<{ ok: boolean; platform: string; enabled: boolean }>(
    'POST',
    '/api/gateway/' + action,
    { body: { platform, reason } },
  )
}

export function getPlatformSpeed(hours = 24, signal?: AbortSignal): Promise<PlatformSpeedPayload> {
  return http<PlatformSpeedPayload>('GET', '/api/platform/speed', { query: { hours }, signal })
}

/* ------------------------------------------------------------------ *
 * 纯函数：策略 / 优先级
 * ------------------------------------------------------------------ */

export function strategyOf(
  config: GatewayConfig | null | undefined,
  value?: string | null,
): GatewayStrategyOption | null {
  if (!config) return null
  const target = value ?? config.strategy
  return config.strategies.find((item) => item.value === target) || null
}

/** 当前（或指定）策略的说明；配置缺失时给空串。 */
export function strategyDescription(config: GatewayConfig | null | undefined, value?: string): string {
  return strategyOf(config, value)?.description || ''
}

/** 显式优先级；未设置的平台按 100 处理（与 gateway_router.order 的 _priority_key 一致）。 */
export function priorityOf(
  config: GatewayConfig | null | undefined,
  platform: string,
  fallback = 100,
): number {
  const value = config?.priority?.[platform]
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

/**
 * 拖拽后的优先级：列表顺序 → 10/20/30…。
 *
 * 与旧版一致（后端只认数字大小），并且**整表**重排，避免旧值把某些平台插回前面。
 */
export function priorityPayload(rows: Array<{ platform: string }>): Record<string, number> {
  const payload: Record<string, number> = {}
  rows.forEach((row, index) => {
    if (!row || !row.platform) return
    payload[row.platform] = (index + 1) * 10
  })
  return payload
}

/* ------------------------------------------------------------------ *
 * 纯函数：调度预演
 * ------------------------------------------------------------------ */

export interface SimulatedLane {
  platform: string
  label: string
  /** 该平台在 100 个模拟请求里分到的次数。 */
  count: number
  usable: number
  disabled: boolean
}

/**
 * 按权重分配整数（最大余额法），保证各份之和恰好等于 total。
 *
 * 例：[3, 1], 100 → [75, 25]。
 */
export function distributeByWeight(weights: number[], total: number): number[] {
  const safe = (weights || []).map((weight) => Math.max(0, Number(weight) || 0))
  const sum = safe.reduce((acc, value) => acc + value, 0)
  if (!safe.length || sum <= 0 || total <= 0) return safe.map(() => 0)
  const raw = safe.map((weight) => (weight / sum) * total)
  const counts = raw.map((value) => Math.floor(value))
  let left = Math.floor(total) - counts.reduce((acc, value) => acc + value, 0)
  const order = raw
    .map((value, index) => ({ index, fraction: value - Math.floor(value) }))
    .sort((a, b) => b.fraction - a.fraction || a.index - b.index)
  let cursor = 0
  while (left > 0 && order.length) {
    counts[order[cursor % order.length].index] += 1
    left -= 1
    cursor += 1
  }
  return counts
}

/**
 * 调度预演（**纯前端模拟**，与真实调度不完全相同，仅用于解释当前顺序）。
 *
 * 口径（契约 gateway.md 实现对照已注明）：
 *   · round_robin：平均分摊（余数给靠前的平台）；
 *   · random：按「可用账号数」加权随机（rng 可注入，便于测试）；
 *   · priority / balance / expiring / throughput：按「可用账号数」加权分摊 ——
 *     顺序列表本身已由后端策略排好，这里只体现平台容量差异。
 * 不可用 / 已禁用的平台不参与。
 */
export function simulateRouting(
  order: GatewayOrderItem[] | null | undefined,
  strategy: string,
  total = 100,
  rng: () => number = Math.random,
): SimulatedLane[] {
  const pool = (order || []).filter((item) => item && item.available !== false && !item.disabled)
  if (!pool.length || total <= 0) return []
  const weights = pool.map((item) => Math.max(1, Number(item.usable) || 1))
  let counts: number[]
  if (strategy === 'round_robin') {
    const base = Math.floor(total / pool.length)
    const rest = total % pool.length
    counts = pool.map((_, index) => base + (index < rest ? 1 : 0))
  } else if (strategy === 'random') {
    counts = pool.map(() => 0)
    const sum = weights.reduce((acc, value) => acc + value, 0)
    for (let n = 0; n < total; n += 1) {
      let pick = Math.max(0, Math.min(0.999999, rng())) * sum
      let index = 0
      while (index < weights.length - 1 && pick >= weights[index]) {
        pick -= weights[index]
        index += 1
      }
      counts[index] += 1
    }
  } else {
    counts = distributeByWeight(weights, total)
  }
  return pool.map((item, index) => ({
    platform: item.platform,
    label: item.label || item.platform,
    count: counts[index],
    usable: Number(item.usable) || 0,
    disabled: Boolean(item.disabled),
  }))
}

/** 「100 个请求中：WorkBuddy 62 · Loomy 38」 */
export function simulationText(
  lanes: SimulatedLane[] | null | undefined,
  total = 100,
): string {
  const items = (lanes || []).filter((lane) => lane.count > 0)
  if (!items.length) return '没有可调度的平台'
  return total + ' 个请求中：' + items.map((lane) => lane.label + ' ' + lane.count).join(' · ')
}

/* ------------------------------------------------------------------ *
 * 纯函数：平台速度表
 * ------------------------------------------------------------------ */

export interface SpeedRowView extends PlatformSpeedItem {
  successText: string
  ttfbText: string
  tpsText: string
  latencyText: string
  tone: 'ok' | 'warn' | 'danger'
}

/** 成功率着色：≥98 ok / ≥90 warn / 其它 danger（旧版同一口径）。 */
export function speedTone(rate: number): 'ok' | 'warn' | 'danger' {
  const value = Number(rate) || 0
  if (value >= 98) return 'ok'
  if (value >= 90) return 'warn'
  return 'danger'
}

function oneDecimal(value: number): string {
  const rounded = Math.round(value * 10) / 10
  return Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1)
}

export function toSpeedRows(items: PlatformSpeedItem[] | null | undefined): SpeedRowView[] {
  return (items || []).map((item) => {
    const rate = Number(item.success_rate) || 0
    return {
      ...item,
      successText: oneDecimal(rate) + '%',
      ttfbText: item.ttfb_avg ? Math.round(item.ttfb_avg) + 'ms' : FMT_EMPTY,
      tpsText: item.tps_avg ? Math.round(item.tps_avg) + ' tok/s' : FMT_EMPTY,
      latencyText: item.latency_avg ? fmtDuration(item.latency_avg) : FMT_EMPTY,
      tone: speedTone(rate),
    }
  })
}

/** 「近 24 小时 · 2 个平台有样本」 */
export function speedPayloadText(payload: PlatformSpeedPayload | null | undefined): string {
  if (!payload) return ''
  const count = (payload.items || []).length
  return '近 ' + fmtInt(payload.hours) + ' 小时 · ' + count + ' 个平台有样本'
}

/** 平台额度：千分位；没有数据时 —（不显示 0 冒充无数据）。 */
export function formatBalance(value: number | null | undefined): string {
  if (value === null || value === undefined) return FMT_EMPTY
  return fmtInt(value)
}