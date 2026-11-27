<script setup lang="ts">
import { computed, ref, useId, watch } from 'vue'
import { useI18n } from '@/i18n'
import { trafficGeometry } from './trafficTrend'
import { replayVisuals } from '@/composables/usePageMotion'
const props = defineProps<{ values: number[]; buckets?: string[]; replayKey?: number }>()
const { t, localeTag } = useI18n()
const id = useId().replace(/[^a-zA-Z0-9_-]/g, '')
const geometry = computed(() => trafficGeometry(props.values))
const svg = ref<SVGSVGElement | null>(null)
// Update the existing geometry; one explicit revision owns redraws, without remounting the SVG.
watch(() => props.replayKey, () => { if (svg.value) replayVisuals(svg.value) }, { flush: 'post' })
const cursor = ref<number | null>(null)
const selected = computed(() => cursor.value === null ? null : geometry.value.points[Math.min(cursor.value, geometry.value.points.length - 1)])
function label(index: number) {
  const value = props.buckets?.[index]
  if (!value) return String(index + 1)
  const time = /(?:T|\s)(\d{2}:\d{2})/.exec(value)
  return time ? time[1] : value.length > 10 ? value.slice(0, 10) : value
}
const ticks = computed(() => [...new Set(Array.from({ length: Math.min(6, props.values.length) }, (_, index) => Math.round(index * (props.values.length - 1) / Math.max(1, Math.min(6, props.values.length) - 1))))])
const number = (value: number) => value.toLocaleString(localeTag.value)
function point(event: PointerEvent) {
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect()
  cursor.value = Math.round(Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)) * (props.values.length - 1))
}
function step(direction: number) { cursor.value = Math.max(0, Math.min(props.values.length - 1, (cursor.value ?? -1) + direction)) }
</script>

<template>
  <div v-if="geometry.points.length" class="traffic-trend" tabindex="0" role="group" :aria-label="t('trend.aria')" @pointermove="point" @pointerleave="cursor = null" @blur="cursor = null" @keydown.right.prevent="step(1)" @keydown.left.prevent="step(-1)" @keydown.home.prevent="cursor = 0" @keydown.end.prevent="cursor = values.length - 1">
    <div class="trend-scale" aria-hidden="true"><span>{{ number(geometry.max) }}</span><span>{{ number(Math.round(geometry.max / 2)) }}</span><span>0</span></div>
    <svg ref="svg" class="trend-svg" viewBox="0 0 920 240" preserveAspectRatio="none" aria-hidden="true">
      <defs><linearGradient :id="'trend-fill-' + id" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="var(--brand)" class="trend-gradient-start" /><stop offset="100%" stop-color="var(--brand)" stop-opacity="0" /></linearGradient></defs>
      <path v-for="y in [36, 126, 216]" :key="y" :d="`M0 ${y} H920`" class="trend-grid" />
      <path :d="geometry.area" :fill="`url(#trend-fill-${id})`" class="trend-area" />
      <path :d="geometry.line" class="trend-bloom" pathLength="1" />
      <path :d="geometry.line" class="trend-line" pathLength="1" />
      <template v-if="selected"><path :d="`M${selected.x},16 V216`" class="trend-guide" /><circle :cx="selected.x" :cy="selected.y" r="12" class="trend-point-glow" /><circle :cx="selected.x" :cy="selected.y" r="4" class="trend-point" /></template>
      <circle v-else :cx="geometry.points[geometry.points.length - 1]!.x" :cy="geometry.points[geometry.points.length - 1]!.y" r="3" class="trend-point" />
    </svg>
    <div v-if="selected" class="trend-tooltip" :style="{ left: Math.max(12, Math.min(88, selected.x / 9.2)) + '%' }"><span>{{ label(cursor!) }}</span><strong>{{ number(selected.value) }} <small>{{ t('trend.requests') }}</small></strong></div>
    <div class="trend-ticks" aria-hidden="true"><span v-for="index in ticks" :key="index">{{ label(index) }}</span></div>
    <span class="mh-sr-only" aria-live="polite">{{ selected ? label(cursor!) + ': ' + number(selected.value) : t('trend.keyboard') }}</span>
  </div>
  <div v-else class="trend-empty"><span class="trend-empty-line" /><p>{{ t('trend.empty') }}</p></div>
</template>

<style scoped>
.traffic-trend{position:relative;min-width:0;width:100%;padding-top:12px;outline-offset:8px;border-radius:8px;touch-action:pan-y}
.trend-svg{width:100%;height:238px;display:block;overflow:visible}.trend-grid{stroke:var(--border-soft);stroke-width:1;stroke-dasharray:2 6;vector-effect:non-scaling-stroke}.trend-line{fill:none;stroke:var(--chart-line,var(--brand));stroke-width:2.5;filter:drop-shadow(0 0 var(--chart-glow-blur,3px) color-mix(in srgb,var(--brand) calc(var(--glow-power,.45) * 70%),transparent));stroke-linecap:round;vector-effect:non-scaling-stroke;stroke-dasharray:1;animation:trend-draw 1100ms cubic-bezier(.22,.8,.2,1) both}
.trend-gradient-start{stop-opacity:var(--chart-fill-opacity,.19)}
.trend-bloom{fill:none;stroke:var(--chart-line,var(--brand));stroke-width:calc(2.5px + var(--glow-power,.45) * 4px);stroke-linecap:round;vector-effect:non-scaling-stroke;stroke-dasharray:1;opacity:var(--chart-glow-opacity,.2);filter:blur(var(--chart-glow-blur,3.6px));animation:trend-draw 1100ms cubic-bezier(.22,.8,.2,1) both;pointer-events:none}
.trend-area{animation:trend-fill 900ms ease-out both}.trend-point{fill:var(--chart-line,var(--brand));stroke:var(--surface);stroke-width:2;filter:drop-shadow(0 0 var(--chart-glow-blur,3px) var(--brand));vector-effect:non-scaling-stroke}.trend-point-glow{fill:var(--brand);opacity:.1}.trend-guide{stroke:var(--brand);opacity:.35;stroke-dasharray:3 5;stroke-width:1;vector-effect:non-scaling-stroke}
.trend-scale{position:absolute;inset:36px 0 47px;display:flex;flex-direction:column;justify-content:space-between;align-items:flex-end;color:var(--fg-muted);font:10px var(--font-mono);pointer-events:none}.trend-scale span{background:var(--surface);padding-left:8px;z-index:1}
.trend-ticks{display:flex;justify-content:space-between;gap:8px;font:10px var(--font-mono);color:var(--fg-muted);margin-top:4px}.trend-tooltip{position:absolute;top:22px;transform:translateX(-50%);display:grid;gap:3px;min-width:100px;padding:10px 12px;border:1px solid var(--border-strong);border-radius:10px;background:var(--surface-raised);box-shadow:var(--shadow-pop);pointer-events:none;font-size:10px;color:var(--fg-muted)}.trend-tooltip strong{font-size:16px;color:var(--fg);font-weight:500}.trend-tooltip small{font-size:10px;font-weight:400;color:var(--fg-muted)}
.trend-empty{min-height:258px;display:grid;align-content:center;gap:16px;color:var(--fg-muted);font-size:12px}.trend-empty-line{height:1px;background:var(--border);width:100%}.trend-empty p{text-align:center}
@keyframes trend-draw{from{stroke-dashoffset:1}to{stroke-dashoffset:0}}@keyframes trend-fill{from{opacity:0}to{opacity:1}}
@media(max-width:767px){.trend-svg{height:180px}.trend-scale{inset:28px 0 40px}.trend-ticks{font-size:9px}.trend-empty{min-height:180px}}
@media(prefers-reduced-motion:reduce){.trend-line,.trend-bloom,.trend-area{animation:none}}
</style>
