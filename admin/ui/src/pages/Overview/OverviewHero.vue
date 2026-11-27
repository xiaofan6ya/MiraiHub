<script setup lang="ts">
/**
 * 概览 · 英雄卡：线路带（全站唯一视觉记忆点，10-visual-refresh §1.8）。
 *
 * 布局：卡头 = 标题（今日流量 / 近 N 天流量）+ 口径徽章「全网关」+ 成功率 + 「N / M 个平台有流量」；
 * 卡体 = RouteRibbon（maxHeight 260，12 个平台也能放下；0 流量平台画细灰线，单平台有流量时仍是扇形）；
 * 卡脚 = Base URL（可复制）+ 「点击线路切换平台」提示。
 * 圆角 --r-lg，顶部叠一条 --grad-brand 的 2px 细线做点缀（只在英雄区用品牌渐变）。
 *
 * v2.2（11-polish-motion）：
 *   · 线路区固定最小高度 220px 并垂直居中 —— 平台少时卡片不缩成一条，骨架与真实卡同高；
 *   · 卡体顶部叠一层 --bg-glow 品牌光晕，和下方 KPI 行拉开层次；
 *   · 卡头事实（成功率 / 有流量平台）改成「小标签 + 数值」两段，数值 600 字重；
 *   · skeleton=true 时线路区换成一块骨架（首屏只用这一种骨架样式）。
 *
 * r3 续跑（11 §1「线路带：可见性 hidden 时暂停」）：
 *   · 卡头加「实时」呼吸点：有流量且可见时亮，暂停时变灰（减少动效时不呼吸）。
 *
 * 第 5 轮：
 *   · SMIL 脉冲的暂停已收进 RouteRibbon（标签页隐藏 / KeepAlive 离开 / 滚出视口），删掉本组件
 *     对 <svg> 的 pauseAnimations 兜底——留着会在线路带滚出视口时把动画重新打开；
 *   · `windowMinutes` 透传给 RouteRibbon（= days × 1440），7 / 30 天时脉冲不再快 7 / 30 倍。
 */
import { computed, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref } from 'vue'
import { Copy, ChevronDown, ArrowUpRight } from 'lucide-vue-next'
import TrafficTrend from '@/components/charts/TrafficTrend.vue'
import OrbitScene from '@/components/ambient/OrbitScene.vue'
import { Badge, Card, IconBtn, RouteRibbon } from '@/components'
import { useCountUp } from '@/composables/useCountUp'
import { fmtPct } from '@/utils/format'
import type { OverviewLane } from '@/api/overview'
import { useI18n } from '@/i18n'

const props = defineProps<{
  title: string
  scope: string
  rangeLabel: string
  lanes: OverviewLane[]
  total: number
  replayKey?: number
  series?: number[]
  buckets?: string[]
  loading?: boolean
  baseUrl: string
  /** 首屏骨架：线路区与事实位显示骨架，尺寸与真实卡一致。 */
  skeleton?: boolean
  /** lanes 计数覆盖的时间窗（分钟）；不传 = 1440（今日 / 24h）。 */
  windowMinutes?: number
}>()

const emit = defineEmits<{
  (event: 'select', id: string): void
  (event: 'copy'): void
}>()

const { t, localeTag } = useI18n()
const numberEl = ref<HTMLElement | null>(null)
const displayTotal = useCountUp(numberEl, () => props.total, () => props.replayKey, () => !props.loading && !props.skeleton)

/** 只驱动卡头「实时 / 空闲」小点；SMIL 动画本身由 RouteRibbon 暂停。 */
const paused = ref(false)
let inactive = false

function applyPause(): void {
  const hidden = typeof document !== 'undefined' && document.hidden
  paused.value = hidden || inactive
}

onMounted(() => {
  if (typeof document !== 'undefined') document.addEventListener('visibilitychange', applyPause)
  applyPause()
})
onBeforeUnmount(() => {
  if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', applyPause)
})
onActivated(() => {
  inactive = false
  applyPause()
})
onDeactivated(() => {
  inactive = true
  applyPause()
})

const live = computed(() => !props.skeleton && props.total > 0 && !paused.value)

const activeCount = computed(() => props.lanes.filter((lane) => lane.count > 0).length)

/** 全网关加权成功率：Σ(请求 × 成功率) / Σ请求；没有请求时 —。 */
const rateText = computed(() => {
  let weighted = 0
  let base = 0
  for (const lane of props.lanes) {
    const rate = lane.successRate
    if (lane.count <= 0 || rate === null || rate === undefined) continue
    const ratio = rate > 1 ? rate / 100 : rate
    weighted += ratio * lane.count
    base += lane.count
  }
  return base > 0 ? fmtPct(weighted / base, 1) : '—'
})
</script>

<template>
  <Card class="ov-hero" hero :title="title" :loading="loading && !skeleton" :motion-pending="loading || skeleton">
    <template #extra>
      <span class="ov-hero__live" :class="{ 'is-on': live }"><i aria-hidden="true" />{{ live ? t('page.overview.hero.live') : t('page.overview.hero.idle') }}</span>
      <Badge tone="neutral">{{ scope }}</Badge>
    </template>
    <div v-if="skeleton" class="ov-hero__primary" aria-hidden="true"><div class="ov-hero__summary"><span class="mh-skeleton hero-sk-label" /><span class="mh-skeleton hero-sk-number" /><span class="mh-skeleton hero-sk-facts" /></div><div class="mh-skeleton hero-sk-chart" /></div>
    <div v-else class="ov-hero__primary">
      <div class="ov-hero__summary">
        <p class="ov-hero__eyebrow">{{ t('overview.refined.requestCount') }}<ArrowUpRight :size="15" /></p>
        <p ref="numberEl" class="ov-hero__number tnum" data-count-up :aria-label="total.toLocaleString(localeTag)">{{ displayTotal.toLocaleString(localeTag) }}</p>
        <span class="ov-hero__period">{{ rangeLabel }}</span>
        <div class="ov-hero__facts"><div><span>{{ t('page.overview.hero.rate') }}</span><strong class="tnum">{{ rateText }}</strong></div><div><span>{{ t('overview.refined.trend') }}</span><strong class="ov-hero__platforms">{{ t('page.overview.hero.active', { active: activeCount, total: lanes.length }) }}</strong></div></div>
      </div>
      <OrbitScene class="ov-hero__planet" />
      <TrafficTrend :replay-key="replayKey" :values="series ?? []" :buckets="buckets" />
    </div>
    <details class="ov-hero__routes">
      <summary>{{ t('overview.refined.routes') }}<span class="ov-hero__route-count">{{ lanes.length }}</span><ChevronDown :size="14" /></summary>
      <div class="ov-hero__stage"><RouteRibbon class="ov-hero__ribbon" :source="{ label: t('page.overview.source'), total }" :lanes="lanes" :max-height="260" :window-minutes="windowMinutes" @select="(id: string) => emit('select', id)" /><p v-if="!lanes.length" class="ov-hero__empty">{{ t('page.overview.traffic.empty') }}</p></div>
    </details>
    <footer class="ov-hero__foot"><span class="ov-hero__label">Base URL</span><code class="mono ov-status__url">{{ baseUrl }}</code><IconBtn :label="t('page.overview.copyBase')" :icon="Copy" @click="emit('copy')" /><span class="ov-hero__hint">{{ t('page.overview.traffic.hint', { range: rangeLabel }) }}</span></footer>
  </Card>
</template>

<style scoped>
.ov-hero{position:relative;overflow:hidden;background-image:radial-gradient(ellipse at 8% 0,color-mix(in srgb,var(--brand) 5%,transparent),transparent 60%)}
.ov-hero::before{content:'';position:absolute;top:0;left:10%;right:10%;height:1px;background:linear-gradient(90deg,transparent,color-mix(in srgb,var(--brand) 45%,transparent),transparent);pointer-events:none}
.ov-hero__primary{display:grid;grid-template-columns:minmax(230px,.8fr) minmax(115px,.45fr) minmax(0,1.8fr);gap:20px;align-items:center;padding:0 0 22px;min-height:286px}
.ov-hero__planet{justify-self:center;opacity:.95}
.ov-hero__summary{padding:12px 0}.ov-hero__eyebrow{display:flex;align-items:center;gap:12px;color:var(--fg-muted);font-size:12px}.ov-hero__eyebrow svg{color:var(--brand)}
.ov-hero__number{font-size:clamp(40px,4.2vw,62px);line-height:1.18;letter-spacing:-2.8px;font-weight:500;margin:12px 0 8px;color:var(--fg)}
.ov-hero__period{display:inline-flex;align-items:center;font-size:10px;color:var(--brand);border:1px solid color-mix(in srgb,var(--brand) 15%,transparent);background:var(--brand-soft);border-radius:20px;padding:1px 9px}
.ov-hero__facts{display:flex;gap:28px;margin-top:28px;padding-top:18px;border-top:1px solid var(--border-soft)}.ov-hero__facts>div{display:grid;gap:6px}.ov-hero__facts span{font-size:10px;color:var(--fg-muted)}.ov-hero__facts strong{font-size:22px;line-height:1.4;font-weight:500;letter-spacing:-.5px}.ov-hero__facts .ov-hero__platforms{font-size:11px;letter-spacing:0;color:var(--fg-2)}
.ov-hero__live{display:inline-flex;align-items:center;gap:6px;font-size:10px;color:var(--fg-muted);white-space:nowrap}.ov-hero__live i{width:5px;height:5px;border-radius:50%;background:var(--fg-muted)}.ov-hero__live.is-on i{background:var(--ok);box-shadow:0 0 0 3px var(--ok-soft)}
.ov-hero__routes{border-top:1px solid var(--border-soft)}.ov-hero__routes summary{display:flex;align-items:center;gap:8px;padding:12px 0;cursor:pointer;list-style:none;font-size:11px;color:var(--fg-muted)}.ov-hero__routes summary::-webkit-details-marker{display:none}.ov-hero__routes summary:hover{color:var(--brand)}.ov-hero__routes summary>svg{margin-left:auto;transition:transform 260ms var(--ease-out)}.ov-hero__routes[open] summary>svg{transform:rotate(180deg)}.ov-hero__route-count{padding:0 6px;border-radius:4px;background:var(--surface-2);font-size:10px}.ov-hero__stage{min-height:100px;padding:8px 0 24px}.ov-hero__ribbon{width:100%}.ov-hero__empty{font-size:12px;color:var(--fg-muted);text-align:center;margin:12px}
.ov-hero__foot{display:flex;align-items:center;gap:10px;padding-top:10px;border-top:1px solid var(--border-soft);min-width:0;font-size:10px;color:var(--fg-muted)}.ov-hero__label{font-size:9px;letter-spacing:.8px;text-transform:uppercase;white-space:nowrap}.ov-status__url{font-size:10px;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--fg-2)}.ov-hero__hint{margin-left:auto;font-size:10px}
.ov-hero :deep(.mh-ribbon__desktop){grid-template-columns:max-content minmax(120px,1fr) minmax(232px,288px)}
.ov-hero :deep(.mh-ribbon__origin){min-width:140px;padding-right:8px}
@supports(interpolate-size:allow-keywords){.ov-hero__routes{interpolate-size:allow-keywords}.ov-hero__routes::details-content{block-size:0;opacity:0;overflow:clip;transition:block-size 420ms var(--ease-out),opacity 220ms,content-visibility 420ms;transition-behavior:allow-discrete}.ov-hero__routes[open]::details-content{block-size:auto;opacity:1}}
.hero-sk-label{width:80px;height:14px}.hero-sk-number{width:180px;height:60px;margin:16px 0}.hero-sk-facts{width:180px;height:52px;margin-top:28px}.hero-sk-chart{height:238px}
@media(max-width:1100px){.ov-hero__primary{gap:24px;grid-template-columns:minmax(180px,.8fr) minmax(0,1.8fr)}.ov-hero__planet{display:none}.ov-hero__facts{gap:16px}.ov-hero__hint{display:none}}
@media(max-width:767px){.ov-hero__primary{grid-template-columns:1fr;gap:0;padding-bottom:16px}.ov-hero__summary{padding:8px 0}.ov-hero__number{font-size:46px}.ov-hero__facts{margin-top:16px;gap:40px;padding-top:12px}.ov-hero__facts strong{font-size:20px}.ov-hero__period{font-size:9px}.ov-hero__foot{gap:6px}.ov-hero__live{font-size:9px}}
</style>
