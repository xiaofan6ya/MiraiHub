<script setup lang="ts">
import { computed } from 'vue'
import { TitleTip } from '@/components'
import type { MarketHealth, MarketHealthState } from '@/api/market'
import { useI18n } from '@/i18n'
import { fmtInt, fmtPct } from '@/utils/format'

const props = defineProps<{ health?: MarketHealth; hoursLabel: string; mini?: boolean }>()
const { t } = useI18n()
const stateKey = {
  up: 'page.market.health.up', warn: 'page.market.health.warn',
  down: 'page.market.health.down', off: 'page.market.health.off', idle: 'page.market.health.idle',
} as const satisfies Record<MarketHealthState, string>
const cells = computed(() => (props.health?.cells || []).map(cell => {
  const label = cell.missing ? t('page.market.health.missing') : t(stateKey[cell.state])
  const parts = [cell.tip, label, t('page.market.health.requests', { count: fmtInt(cell.requests) })]
  if (cell.requests && cell.success_rate !== null) parts.push(t('page.market.health.rate', { rate: fmtPct(cell.success_rate) }))
  if (cell.avg_latency_ms) parts.push(t('page.market.health.latency', { ms: fmtInt(cell.avg_latency_ms) }))
  if (cell.cooling) parts.push(t('page.market.health.cooling', { count: cell.cooling }))
  if (cell.partial) parts.push(t('page.market.health.coverage', { percent: Math.round((cell.coverage ?? 1) * 100) }))
  return { ...cell, title: parts.join(' · ') }
}))
const scale = computed(() => {
  const all = cells.value
  return [...new Set([0, Math.floor((all.length - 1) / 2), all.length - 1])]
    .filter(index => index >= 0).map(index => all[index]?.label || '')
})
const summary = computed(() => props.health?.measured && props.health.uptime !== null
  ? t('page.market.health.uptime', { rate: fmtPct(props.health.uptime) })
  : t('page.market.health.noCalls'))
</script>

<template>
  <div class="wbm-axis" :class="{ 'is-mini': mini }">
    <div v-if="!mini" class="wbm-axis-head">
      <span>{{ t('page.market.health.window', { window: hoursLabel, hours: health?.bucketHours || 1 }) }}</span>
      <b>{{ cells.length ? summary : t('page.market.health.missing') }}</b>
    </div>
    <div v-if="cells.length" class="wbm-cells" :class="{ 'is-mini': mini }" role="img"
      :aria-label="t('page.market.health.aria', { window: hoursLabel, count: cells.length }) + ' · ' + summary">
      <TitleTip v-for="(cell, index) in cells" :key="index" :content="cell.title"><i class="wbm-cell"
        :class="['is-' + cell.state, { 'is-partial': cell.partial }]"
        :style="{ opacity: Math.max(0.55, cell.coverage ?? 1) }" aria-hidden="true" /></TitleTip>
    </div>
    <span v-else class="wbm-axis-empty">{{ t('page.market.health.missing') }}</span>
    <div v-if="!mini && cells.length > 1" class="wbm-axis-scale" aria-hidden="true">
      <span v-for="(label, index) in scale" :key="index">{{ label }}</span>
    </div>
  </div>
</template>

<style scoped>
.wbm-axis{min-width:0;padding-top:var(--sp-3);border-top:1px solid var(--border-soft)}
.wbm-axis.is-mini{padding:0;border:0}
.wbm-axis-head{display:flex;align-items:baseline;justify-content:space-between;flex-wrap:wrap;gap:4px 8px;margin-bottom:6px;font-size:var(--fs-tiny);color:var(--fg-muted)}
.wbm-axis-head b{font-weight:500;font-variant-numeric:tabular-nums}
.wbm-cells{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(0,1fr);gap:2px;height:16px}
.wbm-cells.is-mini{height:14px;gap:1px;min-width:96px}
.wbm-cell{display:block;min-width:0;border-radius:2px;background:var(--surface-3,var(--surface-2));transition:transform 120ms ease}
.wbm-cell.is-up{background:var(--ok)}
.wbm-cell.is-warn{background:var(--warn)}
.wbm-cell.is-down{background:var(--danger)}
.wbm-cell.is-idle{background:color-mix(in srgb,var(--brand) 52%,var(--surface));box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--brand) 72%,var(--border))}
.wbm-cell.is-off{background:repeating-linear-gradient(135deg,var(--danger) 0 2px,color-mix(in srgb,var(--danger) 26%,transparent) 2px 4px)}
.wbm-cell.is-partial{outline:1px solid var(--border-strong);outline-offset:-1px}
.wbm-axis-scale{display:flex;justify-content:space-between;gap:6px;margin-top:4px;font-size:var(--fs-tiny);color:var(--fg-muted);font-variant-numeric:tabular-nums}
.wbm-axis-empty{font-size:var(--fs-small);color:var(--fg-muted)}
@media(hover:hover) and (prefers-reduced-motion:no-preference){.wbm-cell:hover{transform:scaleY(1.25)}}
@media(prefers-reduced-motion:reduce){.wbm-cell{transition:none}}
</style>
