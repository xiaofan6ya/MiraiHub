<script setup lang="ts">
/**
 * 线路带（全站唯一视觉记忆点）：SVG 自绘，不用 ECharts。
 * 线宽 = 2 + 18 × 占比；颜色 = 健康度；首选平台 brand 描边 +「首选」；流量脉冲。
 * <640px 退化为纵向条形列表。
 *
 * v2.1（10-visual-refresh §1.8）：
 *   · 有流量时，0 流量线路画成 1px `--border-strong` 细线（idle），整体始终是扇形，
 *     只有一个平台有流量时不会退化成一根实心粗条；
 *   · `maxHeight` 给定时按线路数压缩行高（22–36px），英雄卡里 12 个平台也能放进 280px。
 *
 * 第 4 轮：
 *   · `windowMinutes`：lanes 的 count 覆盖的时间窗（分钟，默认 1440 = 今日 / 24h）。脉冲速率按
 *     「count / windowMinutes = 每分钟请求数」换算，概览切到 7 天 / 30 天时不再快 7 / 30 倍；
 *   · 脉冲固定按请求次数（count）算，metric='tokens' 时也一样（token 数比次数大几个数量级，会把所有线路顶到最快）；
 *   · 暂停收进组件：标签页隐藏、页面被 KeepAlive 缓存（离开）、线路带滚出视口时 `svg.pauseAnimations()`，
 *     回来 `unpauseAnimations()`。页面里原来的兜底可以删（留着也不冲突）。
 */
import { computed, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, watch } from 'vue'
import Badge from '../base/Badge.vue'
import PlatformLogo from '../identity/PlatformLogo.vue'
import { usePrefersReducedMotion } from '../_internal/media'
import { useI18n } from '@/i18n'
import {
  RIBBON_LANE_HEIGHT,
  RIBBON_LANE_MIN_HEIGHT,
  healthColor,
  healthOf,
  healthTone,
  isEmptyRibbon,
  laneRatio,
  lineWidth,
  normalizeRate,
  ribbonLaneHeight,
  pulseDuration,
  type RibbonLane,
} from './routeRibbon'

const props = withDefaults(
  defineProps<{
    /** 左侧起点：标签 + 总量 */
    source: { label: string; total: number }
    lanes: RibbonLane[]
    /** 显示次数还是 token */
    metric?: 'count' | 'tokens'
    /** 单位后缀（默认「次」） */
    unit?: string
    /** 线路区最大高度（px）；不传 = 每条 36px 不压缩（旧行为）。 */
    maxHeight?: number
    /** count 覆盖的时间窗（分钟）。今日 / 24h = 1440（默认），7 天 = 10080，30 天 = 43200。 */
    windowMinutes?: number
  }>(),
  { metric: 'count', unit: undefined, maxHeight: undefined, windowMinutes: 1440 },
)

const emit = defineEmits<{ (event: 'select', platformId: string): void }>()

const reducedMotion = usePrefersReducedMotion()
const { t, localeTag } = useI18n()
/** 单位可被页面覆盖，默认跟随语言（中文「次」/ 英文 reqs）。 */
const unitText = computed(() => props.unit ?? t('ribbon.unit'))

const wrap = ref<HTMLElement | null>(null)
const svgRef = ref<SVGSVGElement | null>(null)
const width = ref(560)
let observer: ResizeObserver | null = null
let visibilityObserver: IntersectionObserver | null = null

/* ---------- 脉冲暂停：隐藏 / 离开 / 滚出视口任一成立就暂停 ---------- */
let pageHidden = typeof document !== 'undefined' && document.hidden
let inactive = false
let offscreen = false

function syncAnimations(): void {
  const svg = svgRef.value
  if (!svg || typeof svg.pauseAnimations !== 'function') return
  try {
    if (pageHidden || inactive || offscreen || reducedMotion.value) svg.pauseAnimations()
    else svg.unpauseAnimations()
  } catch {
    /* 旧浏览器 / jsdom：没有 SMIL 时间线，忽略 */
  }
}

watch(reducedMotion, syncAnimations, { flush: 'post' })

function onVisibility(): void {
  pageHidden = document.hidden
  syncAnimations()
}

onActivated(() => {
  inactive = false
  syncAnimations()
})

onDeactivated(() => {
  inactive = true
  syncAnimations()
})

onMounted(() => {
  if (typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', onVisibility)
    pageHidden = document.hidden
  }
  if (svgRef.value && typeof IntersectionObserver === 'function') {
    visibilityObserver = new IntersectionObserver((entries) => {
      const entry = entries[entries.length - 1]
      if (!entry) return
      offscreen = !entry.isIntersecting
      syncAnimations()
    })
    visibilityObserver.observe(svgRef.value)
  }
  syncAnimations()
  const el = wrap.value
  if (!el) return
  if (typeof ResizeObserver === 'function') {
    observer = new ResizeObserver((entries) => {
      const next = Math.round(entries[0]?.contentRect?.width ?? 0)
      if (next > 0) width.value = next
    })
    observer.observe(el)
  } else if (el.clientWidth > 0) {
    width.value = el.clientWidth
  }
})

onBeforeUnmount(() => {
  observer?.disconnect()
  observer = null
  visibilityObserver?.disconnect()
  visibilityObserver = null
  if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', onVisibility)
})

/** 每分钟请求数 = count / 时间窗分钟数（时间窗非法时按 1440）。 */
const windowMins = computed(() => {
  const value = Number(props.windowMinutes)
  return Number.isFinite(value) && value > 0 ? value : 1440
})

function perMinute(lane: RibbonLane): number {
  const count = Number(lane.count) || 0
  return count > 0 ? count / windowMins.value : 0
}

const total = computed(() => Number(props.source?.total) || 0)
const isEmpty = computed(() => isEmptyRibbon(total.value, props.lanes))
/** 单条线路的行高：按 maxHeight 压缩，下限 22px（行内文字仍可读）。 */
const laneH = computed(() => ribbonLaneHeight(props.lanes.length, props.maxHeight))
const svgHeight = computed(() =>
  Math.max(RIBBON_LANE_MIN_HEIGHT * 2, props.lanes.length * laneH.value),
)

function valueOf(lane: RibbonLane): number {
  return props.metric === 'tokens' ? Number(lane.tokens ?? lane.count) || 0 : Number(lane.count) || 0
}

function rateText(lane: RibbonLane): string {
  const rate = normalizeRate(lane.successRate)
  return rate === null ? '—' : rate.toFixed(1) + '%'
}

function fmt(value: number): string {
  return Number.isFinite(value) ? value.toLocaleString(localeTag.value) : '0'
}

/** 每条线路的几何与配色。 */
const geometry = computed(() => {
  const h = svgHeight.value
  const midY = h / 2
  const x0 = 0
  const x1 = Math.max(120, width.value)
  return props.lanes.map((lane, index) => {
    const y = index * laneH.value + laneH.value / 2
    const value = valueOf(lane)
    /** 有流量的线路带里，0 流量的那几条画细灰线（不参与健康度着色）。 */
    const idle = !isEmpty.value && value <= 0
    const health = isEmpty.value ? 'disabled' : healthOf(lane)
    const w = isEmpty.value ? 2 : idle ? 1 : lineWidth(value, total.value)
    const control1 = x0 + (x1 - x0) * 0.42
    const control2 = x0 + (x1 - x0) * 0.58
    const d = `M ${x0} ${midY} C ${control1} ${midY}, ${control2} ${y}, ${x1} ${y}`
    return {
      lane,
      index,
      y,
      d,
      width: w,
      idle,
      color: idle ? 'var(--border-strong)' : healthColor(health),
      /** 文字侧（成功率、圆点）的颜色：idle 用 faint，不用边框色。 */
      textColor: idle ? 'var(--fg-faint)' : healthColor(health),
      health,
      dashed: Boolean(lane.disabled) && (idle || health === 'disabled'),
      preferred: Boolean(lane.preferred) && !isEmpty.value,
      pulse: !reducedMotion.value && !isEmpty.value && value > 0 && perMinute(lane) > 0
        ? pulseDuration(perMinute(lane))
        : '',
    }
  })
})

function select(lane: RibbonLane): void {
  emit('select', lane.id)
}
</script>

<template>
  <div class="mh-ribbon">
    <!-- 手机：横向条形列表 -->
    <div class="mh-ribbon__mobile">
      <div class="mh-ribbon__mobile-head">
        <span class="mh-ribbon__source-label">{{ source.label }}</span>
        <span class="mh-ribbon__source-total tnum">{{ fmt(total) }} {{ unitText }}</span>
      </div>
      <p v-if="isEmpty" class="mh-ribbon__empty-text">{{ t('ribbon.noTraffic') }}</p>
      <ul class="mh-ribbon__bars">
        <li v-for="lane in lanes" :key="lane.id" class="mh-ribbon__bar-row" @click="select(lane)">
          <div class="mh-ribbon__bar-head">
            <PlatformLogo :id="lane.logo || lane.id" :size="18" />
            <span class="mh-ribbon__bar-name">{{ lane.name }}</span>
            <Badge v-if="lane.preferred" tone="brand">{{ t('ribbon.preferred') }}</Badge>
            <span class="mh-ribbon__bar-count tnum">{{ fmt(valueOf(lane)) }}</span>
            <span
              class="mh-ribbon__bar-rate tnum"
              :style="{ color: healthColor(healthOf(lane)) }"
            >{{ rateText(lane) }}</span>
          </div>
          <div class="mh-ribbon__bar-track">
            <div
              class="mh-ribbon__bar-fill"
              :style="{
                width: (laneRatio(valueOf(lane), total) * 100).toFixed(2) + '%',
                background: healthColor(isEmpty || valueOf(lane) <= 0 ? 'disabled' : healthOf(lane)),
              }"
            />
          </div>
        </li>
      </ul>
    </div>

    <!-- 桌面：SVG 曲线 -->
    <div class="mh-ribbon__desktop">
      <div class="mh-ribbon__origin">
        <span class="mh-ribbon__source-label">{{ source.label }}</span>
        <span class="mh-ribbon__source-total tnum">{{ fmt(total) }}</span>
        <span class="mh-ribbon__source-unit">{{ t('ribbon.sourceUnit', { unit: unitText }) }}</span>
      </div>
      <div ref="wrap" class="mh-ribbon__canvas">
        <svg
          ref="svgRef"
          class="mh-ribbon__svg"
          :class="{ 'is-empty': isEmpty }"
          :width="Math.max(120, width)"
          :height="svgHeight"
          :viewBox="`0 0 ${Math.max(120, width)} ${svgHeight}`"
          aria-hidden="true"
        >
          <g v-for="item in geometry" :key="item.lane.id">
            <path
              v-if="item.preferred"
              :d="item.d"
              fill="none"
              stroke="var(--brand)"
              :stroke-width="item.width + 2"
              stroke-linecap="round"
            />
            <path
              :d="item.d"
              fill="none"
              :stroke="item.color"
              :stroke-opacity="item.idle ? 0.9 : 1"
              :stroke-width="item.width"
              :stroke-dasharray="item.dashed ? '4 4' : undefined"
              stroke-linecap="round"
            />
            <circle
              v-if="item.pulse"
              class="mh-ribbon__pulse"
              r="2"
              :fill="item.color"
            >
              <animateMotion :dur="item.pulse" repeatCount="indefinite" :path="item.d" />
            </circle>
          </g>
        </svg>
        <span v-if="isEmpty" class="mh-ribbon__empty">{{ t('ribbon.noTraffic') }}</span>
      </div>
      <ul class="mh-ribbon__lanes">
        <li
          v-for="item in geometry"
          :key="item.lane.id"
          class="mh-ribbon__lane"
          :class="{ 'is-idle': item.idle }"
          :style="{ height: laneH + 'px' }"
        >
          <button class="mh-ribbon__lane-btn" type="button" @click="select(item.lane)">
            <PlatformLogo :id="item.lane.logo || item.lane.id" :size="laneH < 30 ? 16 : 18" />
            <span class="mh-ribbon__lane-name">{{ item.lane.name }}</span>
            <Badge v-if="item.lane.preferred" tone="brand">{{ t('ribbon.preferred') }}</Badge>
            <span class="mh-ribbon__lane-count tnum">{{ fmt(valueOf(item.lane)) }}</span>
            <span class="mh-ribbon__lane-rate tnum" :style="{ color: item.textColor }">
              {{ rateText(item.lane) }}
            </span>
            <i class="mh-ribbon__lane-dot" :style="{ background: item.textColor }" aria-hidden="true" />
          </button>
        </li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
.mh-ribbon {
  min-width: 0;
}

/* ---------- 桌面 ---------- */
.mh-ribbon__desktop {
  display: grid;
  grid-template-columns: 88px minmax(120px, 1fr) minmax(232px, 288px);
  align-items: stretch;
  gap: var(--sp-3);
  min-width: 0;
}

.mh-ribbon__origin {
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: var(--sp-1);
  min-width: 0;
}

.mh-ribbon__source-label {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.mh-ribbon__source-total {
  font-size: var(--fs-h1);
  line-height: var(--lh-h1);
  font-weight: 600;
  color: var(--fg);
}

.mh-ribbon__source-unit {
  font-size: var(--fs-tiny);
  line-height: var(--lh-tiny);
  color: var(--fg-muted);
}

.mh-ribbon__canvas {
  position: relative;
  min-width: 0;
  overflow: hidden;
}

.mh-ribbon__svg {
  display: block;
}

.mh-ribbon__pulse {
  opacity: 0.95;
}

.mh-ribbon__empty {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: var(--fs-small);
  color: var(--fg-faint);
  pointer-events: none;
}

.mh-ribbon__lanes {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
}

.mh-ribbon__lane {
  height: 36px;
  display: flex;
  align-items: center;
}

.mh-ribbon__lane-btn {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  width: 100%;
  height: 100%;
  padding: 0 var(--sp-1);
  border: 0;
  border-radius: var(--r-chip);
  background: transparent;
  color: var(--fg);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  text-align: left;
  cursor: pointer;
}

.mh-ribbon__lane-btn:hover {
  background: var(--surface-hover);
}

.mh-ribbon__lane-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--fg-2);
}

.mh-ribbon__lane.is-idle .mh-ribbon__lane-name,
.mh-ribbon__lane.is-idle .mh-ribbon__lane-count {
  color: var(--fg-faint);
}

.mh-ribbon__lane-count {
  color: var(--fg-muted);
  flex: none;
}

.mh-ribbon__lane-rate {
  font-weight: 500;
  min-width: 42px;
  text-align: right;
  flex: none;
}

.mh-ribbon__lane-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex: none;
}

/* ---------- 手机：条形列表 ---------- */
.mh-ribbon__mobile {
  display: none;
}

.mh-ribbon__mobile-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--sp-2);
  margin-bottom: var(--sp-2);
}

.mh-ribbon__bars {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: var(--sp-2);
}

.mh-ribbon__bar-row {
  display: grid;
  gap: var(--sp-1);
  cursor: pointer;
}

.mh-ribbon__bar-head {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  min-width: 0;
}

.mh-ribbon__bar-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--fg-2);
}

.mh-ribbon__bar-count {
  color: var(--fg-muted);
}

.mh-ribbon__bar-rate {
  min-width: 42px;
  text-align: right;
  font-weight: 500;
}

.mh-ribbon__bar-track {
  height: 6px;
  border-radius: 50px;
  background: var(--border-soft);
  overflow: hidden;
}

.mh-ribbon__bar-fill {
  height: 100%;
  border-radius: inherit;
}

.mh-ribbon__empty-text {
  font-size: var(--fs-small);
  color: var(--fg-muted);
  margin-bottom: var(--sp-2);
}

@media (max-width: 640px) {
  .mh-ribbon__desktop {
    display: none;
  }

  .mh-ribbon__mobile {
    display: block;
  }
}

@media (prefers-reduced-motion: reduce) {
  .mh-ribbon__pulse {
    display: none;
  }
}
</style>
