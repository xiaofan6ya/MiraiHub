<script setup lang="ts">
/**
 * 线路带（全站唯一视觉记忆点）：SVG 自绘，不用 ECharts。
 * 线宽 = 2 + 18 × 占比；颜色 = 健康度；首选平台 brand 描边 +「首选」；流量脉冲。
 * <640px 退化为纵向条形列表。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import Badge from '../base/Badge.vue'
import PlatformLogo from '../identity/PlatformLogo.vue'
import { usePrefersReducedMotion } from '../_internal/media'
import { useI18n } from '@/i18n'
import {
  RIBBON_LANE_HEIGHT,
  healthColor,
  healthOf,
  healthTone,
  isEmptyRibbon,
  laneRatio,
  lineWidth,
  normalizeRate,
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
  }>(),
  { metric: 'count', unit: undefined },
)

const emit = defineEmits<{ (event: 'select', platformId: string): void }>()

const reducedMotion = usePrefersReducedMotion()
const { t, localeTag } = useI18n()
/** 单位可被页面覆盖，默认跟随语言（中文「次」/ 英文 reqs）。 */
const unitText = computed(() => props.unit ?? t('ribbon.unit'))

const wrap = ref<HTMLElement | null>(null)
const width = ref(560)
let observer: ResizeObserver | null = null

onMounted(() => {
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
})

const total = computed(() => Number(props.source?.total) || 0)
const isEmpty = computed(() => isEmptyRibbon(total.value, props.lanes))
const svgHeight = computed(() => Math.max(RIBBON_LANE_HEIGHT, props.lanes.length * RIBBON_LANE_HEIGHT))

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
    const y = index * RIBBON_LANE_HEIGHT + RIBBON_LANE_HEIGHT / 2
    const health = isEmpty.value ? 'disabled' : healthOf(lane)
    const w = isEmpty.value ? 2 : lineWidth(valueOf(lane), total.value)
    const control1 = x0 + (x1 - x0) * 0.42
    const control2 = x0 + (x1 - x0) * 0.58
    const d = `M ${x0} ${midY} C ${control1} ${midY}, ${control2} ${y}, ${x1} ${y}`
    return {
      lane,
      index,
      y,
      d,
      width: w,
      color: healthColor(health),
      health,
      dashed: health === 'disabled' && Boolean(lane.disabled),
      preferred: Boolean(lane.preferred) && !isEmpty.value,
      pulse: !reducedMotion.value && !isEmpty.value && valueOf(lane) > 0
        ? pulseDuration(valueOf(lane) / 1440)
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
                background: healthColor(isEmpty ? 'disabled' : healthOf(lane)),
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
          class="mh-ribbon__svg"
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
        <li v-for="item in geometry" :key="item.lane.id" class="mh-ribbon__lane">
          <button class="mh-ribbon__lane-btn" type="button" @click="select(item.lane)">
            <PlatformLogo :id="item.lane.logo || item.lane.id" :size="18" />
            <span class="mh-ribbon__lane-name">{{ item.lane.name }}</span>
            <Badge v-if="item.lane.preferred" tone="brand">{{ t('ribbon.preferred') }}</Badge>
            <span class="mh-ribbon__lane-count tnum">{{ fmt(valueOf(item.lane)) }}</span>
            <span class="mh-ribbon__lane-rate tnum" :style="{ color: item.color }">
              {{ rateText(item.lane) }}
            </span>
            <i class="mh-ribbon__lane-dot" :style="{ background: item.color }" aria-hidden="true" />
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
  color: var(--fg-faint);
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
