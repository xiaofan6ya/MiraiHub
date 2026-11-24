<script setup lang="ts">
/**
 * 模型广场 · 单张模型卡（v2.1，10-visual-refresh §1.1 / 第 1、3 轮审查）。
 *
 * 层次（自上而下）：
 *   1. 头：32px 模型图标方块 + 名称 + 调用名（mono，可复制）+ 可用状态徽章（圆点）；
 *   2. 平台行：每个平台**一个**图标（按平台 id 去重，同平台多区合并进 tooltip），图标之间 6px 间距，
 *      最多 6 个，其余收成「+N」chip（悬停列出全部）；右侧灰字「N 个平台」；
 *   3. 价格：大字「×0.80 / 免费 / 按量计费 / 未定价」+ 一行说明；
 *   4. 四格指标：成功率 / TTFT / 平均耗时 / 吞吐；无样本显示「—」，平均值 > 1 小时显示「数据异常」；
 *   5. 迷你折线（窗口内请求趋势）+ 底部两个次级按钮。
 * 不可用的卡整体降到 55% 不透明度（不只是一个红徽章）。
 */
import { computed } from 'vue'
import { Copy, ScrollText } from 'lucide-vue-next'
import { Badge, Btn, ModelIcon, PlatformLogo, Sparkline } from '@/components'
import { FMT_EMPTY, fmtCompact, fmtPct } from '@/utils/format'
import { priceText, rateTone, type MarketCard } from '@/api/market'
import { avgDurationText, durationState } from '@/api/stats'
import { useI18n } from '@/i18n'

const props = defineProps<{
  card: MarketCard
  /** 窗口文案（「近 24 小时」等）。 */
  hoursLabel: string
}>()

const emit = defineEmits<{
  (event: 'copy', card: MarketCard): void
  (event: 'logs', card: MarketCard): void
}>()

const { t } = useI18n()

/** 平台图标最多露 6 个，其余进 +N。 */
const MAX_LOGOS = 6

const logos = computed(() =>
  props.card.platforms.slice(0, MAX_LOGOS).map((id, index) => ({
    id,
    title: props.card.platformTitles?.[index] || props.card.platformLabels[index] || id,
  })),
)

const restCount = computed(() => Math.max(0, props.card.platforms.length - MAX_LOGOS))
const restTitle = computed(() =>
  props.card.platforms
    .slice(MAX_LOGOS)
    .map((id, index) => props.card.platformTitles?.[index + MAX_LOGOS] || props.card.platformLabels[index + MAX_LOGOS] || id)
    .join(t('page.market.chipsJoin')),
)
const allPlatformsTitle = computed(() =>
  props.card.platforms
    .map((id, index) => props.card.platformTitles?.[index] || props.card.platformLabels[index] || id)
    .join(t('page.market.chipsJoin')),
)

const price = computed(() => priceText(props.card, t))
const priceNote = computed(() =>
  props.card.free
    ? t('page.market.card.freeNote')
    : props.card.metered
      ? t('page.market.card.meteredNote')
      : props.card.multiplier === null
        ? t('page.market.card.noPriceNote')
        : t('page.market.card.priceNote'),
)
const priceMuted = computed(() => !props.card.free && !props.card.metered && props.card.multiplier === null)

interface Metric {
  key: string
  label: string
  value: string
  tone?: string
  muted: boolean
}

const metrics = computed<Metric[]>(() => {
  const card = props.card
  const ttfb = durationState(card.ttfbMs)
  const latency = durationState(card.latencyMs)
  return [
    {
      key: 'rate',
      label: t('page.market.card.rate'),
      value: fmtPct(card.successRate),
      tone: card.successRate === null ? undefined : rateTone(card.successRate),
      muted: card.successRate === null,
    },
    {
      key: 'ttft',
      label: 'TTFT',
      value: avgDurationText(card.ttfbMs, t),
      tone: ttfb === 'anomaly' ? 'warn' : undefined,
      muted: ttfb === 'none',
    },
    {
      key: 'latency',
      label: t('page.market.col.latency'),
      value: avgDurationText(card.latencyMs, t),
      tone: latency === 'anomaly' ? 'warn' : undefined,
      muted: latency === 'none',
    },
    {
      key: 'tps',
      label: t('page.market.col.tps'),
      value: card.tps > 0 ? card.tps.toFixed(1) + ' t/s' : FMT_EMPTY,
      muted: !(card.tps > 0),
    },
  ]
})

const sparkTitle = computed(() =>
  props.card.sparkTotal === null
    ? t('page.market.spark.none')
    : t('page.market.spark.total', { count: fmtCompact(props.card.sparkTotal) }),
)
</script>

<template>
  <article class="mkc" :class="{ 'is-off': !card.available }">
    <header class="mkc__head">
      <span class="mkc__icon" aria-hidden="true">
        <ModelIcon :vendor="card.key" :model="card.callName" :size="20" />
      </span>
      <div class="mkc__title">
        <h3 :title="card.name">{{ card.name }}</h3>
        <code class="mkc__call" :title="card.callName">{{ card.callName || FMT_EMPTY }}</code>
      </div>
      <Badge dot :tone="card.available ? 'ok' : 'neutral'">
        {{ card.available ? t('page.market.state.available') : t('page.market.state.unavailable') }}
      </Badge>
    </header>

    <div class="mkc__platforms" :title="allPlatformsTitle">
      <ul v-if="card.platformCount" class="mkc__logos">
        <li v-for="logo in logos" :key="logo.id" class="mkc__logo" :title="logo.title">
          <PlatformLogo :id="logo.id" :size="18" :label="logo.title" />
        </li>
        <li v-if="restCount" class="mkc__more" :title="restTitle">+{{ restCount }}</li>
      </ul>
      <span v-else class="mkc__muted">{{ t('page.market.card.noPlatform') }}</span>
      <span class="mkc__count">{{ t('page.market.card.platformCount', { count: card.platformCount }) }}</span>
    </div>

    <div class="mkc__price">
      <b :class="{ 'is-muted': priceMuted, 'is-free': card.free }">{{ price }}</b>
      <span>{{ priceNote }}</span>
    </div>

    <dl class="mkc__stats">
      <div v-for="item in metrics" :key="item.key">
        <dt>{{ item.label }}</dt>
        <dd class="tnum" :class="[item.tone ? 'is-' + item.tone : '', { 'is-muted': item.muted }]">
          {{ item.value }}
        </dd>
      </div>
    </dl>

    <div class="mkc__spark" :title="sparkTitle">
      <span class="mkc__spark-label">
        {{ t('page.market.card.spark', { window: hoursLabel, count: fmtCompact(card.requests) }) }}
      </span>
      <div v-if="card.spark.length" class="mkc__spark-line">
        <Sparkline :data="card.spark" tone="brand" :height="28" />
      </div>
      <span v-else class="mkc__muted">{{ FMT_EMPTY }}</span>
    </div>

    <footer class="mkc__foot">
      <Btn variant="quiet" size="sm" :icon="Copy" @click="emit('copy', card)">
        {{ t('page.market.action.copy') }}
      </Btn>
      <Btn variant="quiet" size="sm" :icon="ScrollText" @click="emit('logs', card)">
        {{ t('page.market.action.logs') }}
      </Btn>
    </footer>
  </article>
</template>

<style scoped>
.mkc {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
  padding: var(--sp-4);
  min-width: 0;
  border: 1px solid var(--border);
  border-radius: var(--r-card);
  background: var(--surface);
  box-shadow: var(--shadow-1);
  transition:
    box-shadow var(--dur-2, 180ms) var(--ease-out, ease-out),
    border-color var(--dur-2, 180ms) var(--ease-out, ease-out);
}

/* 悬停质感（10 §1.1）：换 --shadow-2 + --border-strong，不上浮不缩放；键盘聚焦到卡内按钮时同样高亮。 */
.mkc:hover,
.mkc:focus-within {
  border-color: var(--border-strong);
  box-shadow: var(--shadow-2);
}

/* 悬停时图标方块染一点品牌色，给卡片一个视觉锚点 */
.mkc:hover .mkc__icon,
.mkc:focus-within .mkc__icon {
  border-color: var(--border);
  background: var(--brand-soft);
}

/* 不可用：不再整卡 opacity（文字对比度不足、深色下发糊），改成「降一级表面 + 文字转灰 + 图标去色」。 */
.mkc.is-off {
  background: var(--surface-2);
  box-shadow: none;
}

.mkc.is-off .mkc__title h3,
.mkc.is-off .mkc__price b {
  color: var(--fg-muted);
}

.mkc.is-off .mkc__icon,
.mkc.is-off .mkc__logos,
.mkc.is-off .mkc__spark-line {
  filter: grayscale(1);
  opacity: 0.7;
}

.mkc__head {
  display: flex;
  align-items: flex-start;
  gap: var(--sp-3);
  min-width: 0;
}

.mkc__icon {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: var(--r-ctl);
  background: var(--surface-2);
  border: 1px solid var(--border-soft);
  transition: background-color var(--dur-2, 180ms) var(--ease-out, ease-out);
}

.mkc__title {
  flex: 1;
  min-width: 0;
  display: grid;
  gap: 2px;
}

.mkc__title h3 {
  margin: 0;
  font-size: var(--fs-h2);
  line-height: var(--lh-h2);
  font-weight: 600;
  letter-spacing: -0.01em;
  color: var(--fg);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mkc__call {
  font-family: var(--font-mono);
  font-size: var(--fs-tiny);
  line-height: var(--lh-tiny);
  color: var(--fg-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mkc__platforms {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  min-width: 0;
  min-height: 26px;
}

.mkc__logos {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
  min-width: 0;
}

.mkc__logo {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: var(--surface-2);
  border: 1px solid var(--border-soft);
}

.mkc__more {
  display: inline-flex;
  font-variant-numeric: tabular-nums;
  align-items: center;
  height: 24px;
  padding: 0 var(--sp-2);
  border-radius: var(--r-chip);
  background: var(--surface-2);
  border: 1px solid var(--border-soft);
  font-size: var(--fs-tiny);
  color: var(--fg-2);
  cursor: default;
}

.mkc__count {
  margin-left: auto;
  flex: none;
  font-size: var(--fs-small);
  color: var(--fg-muted);
  white-space: nowrap;
}

.mkc__price {
  display: grid;
  gap: 2px;
}

.mkc__price b {
  font-size: var(--fs-stat);
  line-height: var(--lh-stat);
  font-weight: 600;
  letter-spacing: -0.01em;
  color: var(--fg);
  font-variant-numeric: tabular-nums;
}

.mkc__price b.is-free {
  color: var(--ok);
}

.mkc__price b.is-muted {
  font-size: var(--fs-h2);
  line-height: var(--lh-h2);
  color: var(--fg-faint);
}

.mkc__price span {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.mkc__stats {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--sp-2);
  margin: 0;
  padding-top: var(--sp-3);
  border-top: 1px solid var(--border-soft);
}

.mkc__stats dt {
  font-size: var(--fs-tiny);
  line-height: var(--lh-tiny);
  color: var(--fg-muted);
  white-space: nowrap;
}

.mkc__stats dd {
  margin: 2px 0 0;
  font-variant-numeric: tabular-nums;
  font-size: var(--fs-body);
  line-height: var(--lh-body);
  font-weight: 500;
  color: var(--fg);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mkc__stats dd.is-ok {
  color: var(--ok);
}

.mkc__stats dd.is-warn {
  color: var(--warn);
}

.mkc__stats dd.is-danger {
  color: var(--danger);
}

.mkc__stats dd.is-muted,
.mkc__muted {
  color: var(--fg-faint);
  font-weight: 400;
}

.mkc__spark {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  min-width: 0;
}

.mkc__spark-label {
  font-size: var(--fs-small);
  color: var(--fg-muted);
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

.mkc__spark-line {
  flex: 1;
  min-width: 60px;
}

.mkc__foot {
  display: flex;
  align-items: center;
  gap: var(--sp-1);
  /* 同一行卡片等高时操作条贴底对齐 */
  margin-top: auto;
  padding-top: var(--sp-3);
  border-top: 1px solid var(--border-soft);
}

@media (prefers-reduced-motion: reduce) {
  .mkc,
  .mkc__icon {
    transition: none;
  }
}

@media (max-width: 640px) {
  .mkc__stats {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .mkc__spark {
    flex-wrap: wrap;
  }
}
</style>
