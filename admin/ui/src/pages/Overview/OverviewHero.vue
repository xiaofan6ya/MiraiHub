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
import { Copy } from 'lucide-vue-next'
import { Badge, Card, IconBtn, RouteRibbon } from '@/components'
import { fmtPct } from '@/utils/format'
import type { OverviewLane } from '@/api/overview'
import { useI18n } from '@/i18n'

const props = defineProps<{
  title: string
  scope: string
  rangeLabel: string
  lanes: OverviewLane[]
  total: number
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

const { t } = useI18n()

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
  <Card class="ov-hero" hero :title="title" :loading="loading && !skeleton">
    <template #extra>
      <div v-if="skeleton" class="ov-hero__meta" aria-hidden="true">
        <span class="mh-skeleton ov-hero__sk-fact" />
        <span class="mh-skeleton ov-hero__sk-fact" />
      </div>
      <div v-else class="ov-hero__meta">
        <span class="ov-hero__live" :class="{ 'is-on': live }">
          <i aria-hidden="true" />{{ live ? t('page.overview.hero.live') : t('page.overview.hero.idle') }}
        </span>
        <Badge tone="neutral">{{ scope }}</Badge>
        <span class="ov-hero__fact">
          <span class="ov-hero__fact-label">{{ t('page.overview.hero.rate') }}</span>
          <b class="tnum">{{ rateText }}</b>
        </span>
        <span class="ov-hero__sep" aria-hidden="true" />
        <span class="ov-hero__fact">
          {{ t('page.overview.hero.active', { active: activeCount, total: lanes.length }) }}
        </span>
      </div>
    </template>

    <div class="ov-hero__stage">
      <div v-if="skeleton" class="mh-skeleton ov-hero__sk-stage" aria-hidden="true" />
      <template v-else>
        <RouteRibbon
          class="ov-hero__ribbon"
          :source="{ label: t('page.overview.source'), total }"
          :lanes="lanes"
          :max-height="260"
          :window-minutes="windowMinutes"
          @select="(id: string) => emit('select', id)"
        />
        <p v-if="!lanes.length" class="ov-hero__empty">{{ t('page.overview.traffic.empty') }}</p>
      </template>
    </div>

    <footer class="ov-hero__foot">
      <span class="ov-hero__label">Base URL</span>
      <code class="mono ov-status__url">{{ baseUrl }}</code>
      <IconBtn :label="t('page.overview.copyBase')" :icon="Copy" @click="emit('copy')" />
      <span class="ov-hero__hint">{{ t('page.overview.traffic.hint', { range: rangeLabel }) }}</span>
    </footer>
  </Card>
</template>

<style scoped>
.ov-hero {
  position: relative;
  overflow: hidden;
  border-radius: var(--r-lg);
  background-image: var(--bg-glow);
  background-repeat: no-repeat;
}

/* 英雄区小面积品牌点缀（--grad-brand 只用于 logo 底和英雄区，10-visual-refresh §2） */
.ov-hero::before {
  content: '';
  position: absolute;
  inset: 0 0 auto;
  height: 2px;
  background: var(--grad-brand);
  pointer-events: none;
}

.ov-hero__meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--sp-1) var(--sp-3);
}

.ov-hero__fact {
  display: inline-flex;
  align-items: baseline;
  gap: var(--sp-1);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  white-space: nowrap;
}

.ov-hero__fact-label {
  color: var(--fg-muted);
}

.ov-hero__fact b {
  font-weight: 600;
  color: var(--fg);
}

.ov-hero__live {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: var(--fs-tiny);
  line-height: var(--lh-tiny);
  font-weight: 500;
  letter-spacing: 0.02em;
  color: var(--fg-muted);
  white-space: nowrap;
}

.ov-hero__live i {
  position: relative;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--fg-faint);
}

.ov-hero__live.is-on {
  color: var(--ok);
}

.ov-hero__live.is-on i {
  background: var(--ok);
}

.ov-hero__live.is-on i::after {
  content: '';
  position: absolute;
  inset: -3px;
  border-radius: 50%;
  border: 1px solid var(--ok);
  opacity: 0;
  animation: ov-hero-breathe 2.4s var(--ease-out, ease-out) infinite;
}

@keyframes ov-hero-breathe {
  0% {
    transform: scale(0.6);
    opacity: 0.7;
  }

  100% {
    transform: scale(1.6);
    opacity: 0;
  }
}

@media (prefers-reduced-motion: reduce) {
  .ov-hero__live.is-on i::after,
  .ov-hero__ribbon {
    animation: none;
  }
}

.ov-hero__sep {
  width: 1px;
  height: 12px;
  background: var(--border);
}

/* 线路区：固定最小高度、垂直居中（10 §1.8：220–280px），平台少时不缩成一条。 */
.ov-hero__stage {
  display: flex;
  flex-direction: column;
  justify-content: center;
  min-height: 220px;
  padding: var(--sp-2) 0;
}

.ov-hero__ribbon {
  width: 100%;
  animation: ov-hero-in 160ms var(--ease-out, ease-out) both;
}

@keyframes ov-hero-in {
  from {
    opacity: 0;
  }

  to {
    opacity: 1;
  }
}

.ov-hero__sk-stage {
  height: 200px;
  border-radius: var(--r-card);
}

.ov-hero__sk-fact {
  display: inline-block;
  width: 72px;
  height: 18px;
  border-radius: var(--r-chip);
}

.ov-hero__empty {
  margin: var(--sp-2) 0 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.ov-hero__foot {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--sp-2) var(--sp-3);
  margin-top: var(--sp-3);
  padding-top: var(--sp-3);
  border-top: 1px solid var(--border-soft);
}

.ov-hero__label {
  font-size: var(--fs-small);
  color: var(--fg-muted);
}

.ov-status__url {
  color: var(--fg-2);
  background: var(--surface-2);
  border: 1px solid var(--border-soft);
  border-radius: var(--r-chip);
  padding: 1px var(--sp-2);
  word-break: break-all;
}

.ov-hero__hint {
  margin-left: auto;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

@media (max-width: 640px) {
  .ov-hero__stage {
    min-height: 0;
  }

  .ov-hero__sep {
    display: none;
  }

  .ov-hero__hint {
    margin-left: 0;
    width: 100%;
  }
}
</style>
