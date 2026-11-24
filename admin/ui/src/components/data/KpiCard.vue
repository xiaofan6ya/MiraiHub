<script setup lang="ts">
/**
 * KPI 统计卡（10-visual-refresh §1.4 / §3）。
 * 标签 + 大数字（28/34）+ 单位 + 环比 chip（好坏按 deltaGood 着色）+ 迷你折线 + 可选 32px 图标方块。
 * 有 to 时整卡可点（悬停换 --shadow-2 与 --border-strong，不上浮、不缩放）。
 * 刷新时保留旧值，右上角小转圈；首次加载（value 为 null）显示骨架。
 *
 * 单位（11-polish-motion §3「单位比数字小一级、--fg-muted」）：
 *   · `unit="次"`：数字后面跟一个小一级（--fs-h2）、--fg-muted、tabular-nums 的单位；
 *   · `split-unit`（第 4 轮，默认关）：value 是已经格式化好的字符串（'98.5%'、'4.6s'、'1.2 万'）时
 *     自动把尾部单位拆出来按单位样式显示；同时给了 unit 时以 unit 为准；
 *   · `#unit` 插槽：单位需要自定义渲染（例如带 tooltip）时用，样式同上。
 */
import { computed, type Component } from 'vue'
import { RouterLink } from 'vue-router'
import { NSpin } from 'naive-ui'
import { ArrowDownRight, ArrowUpRight, Minus } from 'lucide-vue-next'
import Sparkline from './Sparkline.vue'
import { kpiDelta, kpiSplitUnit, kpiToneColors, kpiValueText, type KpiDeltaGood, type KpiTone } from './kpi'
import { FMT_EMPTY } from '@/utils/format'
import { useI18n } from '@/i18n'

const props = withDefaults(
  defineProps<{
    label: string
    value: string | number | null
    unit?: string
    /** 环比百分比（12.3 = +12.3%），正负均可；null 不显示。 */
    delta?: number | null
    /** 哪个方向算「好」，默认 up。 */
    deltaGood?: KpiDeltaGood
    hint?: string
    tone?: KpiTone
    icon?: Component
    spark?: number[]
    loading?: boolean
    /** router 路径；有则整卡可点。 */
    to?: string
    /** 第 4 轮：字符串 value 自动拆出尾部单位（'98.5%' → 98.5 + %）。默认 false。 */
    splitUnit?: boolean
  }>(),
  {
    unit: undefined,
    delta: null,
    deltaGood: 'up',
    hint: undefined,
    tone: 'brand',
    icon: undefined,
    spark: undefined,
    loading: false,
    to: undefined,
    splitUnit: false,
  },
)

const { t, localeTag } = useI18n()

const fullText = computed(() => kpiValueText(props.value, localeTag.value))
const isEmpty = computed(() => fullText.value === FMT_EMPTY)
/** 拆单位：只对字符串 value、开了 split-unit、且没显式给 unit 时生效。 */
const split = computed(() =>
  props.splitUnit && !props.unit && typeof props.value === 'string' && !isEmpty.value
    ? kpiSplitUnit(fullText.value)
    : { num: fullText.value, unit: '' },
)
const valueText = computed(() => split.value.num)
const unitText = computed(() => props.unit || split.value.unit)
const showSkeleton = computed(() => props.loading && (props.value === null || props.value === undefined))
const deltaView = computed(() => kpiDelta(props.delta, props.deltaGood))
const deltaIcon = computed(() =>
  deltaView.value?.dir === 'up' ? ArrowUpRight : deltaView.value?.dir === 'down' ? ArrowDownRight : Minus,
)
const deltaAria = computed(() =>
  deltaView.value
    ? deltaView.value.dir === 'flat'
      ? t('kpi.deltaFlat')
      : t('kpi.delta', { value: deltaView.value.text })
    : '',
)
const toneColors = computed(() => kpiToneColors(props.tone))
const sparkTone = computed(() => props.tone ?? 'brand')
const hasSpark = computed(() => Array.isArray(props.spark) && props.spark.length > 1)
const clickable = computed(() => Boolean(props.to))
</script>

<template>
  <component
    :is="clickable ? RouterLink : 'section'"
    :to="clickable ? to : undefined"
    class="mh-kpi"
    :class="{ 'mh-kpi--link': clickable, 'is-loading': loading }"
    :aria-busy="loading ? 'true' : undefined"
  >
    <div class="mh-kpi__head">
      <span class="mh-kpi__label">{{ label }}</span>
      <n-spin v-if="loading && !showSkeleton" class="mh-kpi__spin" :size="14" />
      <span
        v-if="icon"
        class="mh-kpi__icon"
        :style="{ color: toneColors.fg, background: toneColors.bg }"
        aria-hidden="true"
      >
        <component :is="icon" :size="16" />
      </span>
    </div>

    <div v-if="showSkeleton" class="mh-kpi__skeleton">
      <span class="mh-skeleton mh-kpi__sk-num" />
      <span class="mh-skeleton mh-kpi__sk-line" />
    </div>
    <template v-else>
      <div class="mh-kpi__value">
        <!-- 数字变化：tabular-nums 保持宽度，新值淡入 180ms（不做数字滚动，11-polish-motion §1） -->
        <transition name="mh-fade" mode="out-in">
          <span :key="valueText" class="mh-kpi__num" :class="{ 'is-empty': isEmpty }">{{ valueText }}</span>
        </transition>
        <span v-if="$slots.unit && !isEmpty" class="mh-kpi__unit"><slot name="unit" /></span>
        <span v-else-if="unitText && !isEmpty" class="mh-kpi__unit">{{ unitText }}</span>
        <span
          v-if="deltaView"
          class="mh-kpi__delta"
          :class="'is-' + deltaView.verdict"
          :aria-label="deltaAria"
          :title="deltaAria"
        >
          <component :is="deltaIcon" :size="12" aria-hidden="true" />
          {{ deltaView.text }}
        </span>
      </div>
      <Sparkline v-if="hasSpark" class="mh-kpi__spark" :data="spark || []" :tone="sparkTone" :height="28" />
      <p v-if="hint" class="mh-kpi__hint">{{ hint }}</p>
    </template>

    <div v-if="$slots.extra" class="mh-kpi__extra">
      <slot name="extra" />
    </div>
  </component>
</template>

<style scoped>
.mh-kpi {
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
  min-width: 0;
  padding: var(--sp-4) var(--sp-5);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-card);
  box-shadow: var(--shadow-1);
  color: inherit;
  text-decoration: none;
}

.mh-kpi--link {
  cursor: pointer;
  transition:
    box-shadow var(--t-base) var(--ease),
    border-color var(--t-base) var(--ease),
    transform var(--dur-1) var(--ease-out);
}

/* 按压反馈（11-polish-motion §1） */
.mh-kpi--link:active {
  transform: scale(.98);
}

@media (prefers-reduced-motion: reduce) {
  .mh-kpi--link:active {
    transform: none;
  }
}

.mh-kpi--link:hover,
.mh-kpi--link:focus-visible {
  box-shadow: var(--shadow-2);
  border-color: var(--border-strong);
  text-decoration: none;
  color: inherit;
}

.mh-kpi__head {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  min-height: 20px;
}

.mh-kpi__label {
  flex: 1 1 auto;
  min-width: 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  font-weight: 500;
  color: var(--fg-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mh-kpi__spin {
  flex: none;
}

.mh-kpi__icon {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  margin: -6px -4px -6px 0;
  border-radius: var(--r-ctl);
}

.mh-kpi__value {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 4px var(--sp-2);
  min-width: 0;
}

.mh-kpi__num {
  font-size: var(--fs-stat);
  line-height: var(--lh-stat);
  font-weight: 600;
  color: var(--fg);
  font-variant-numeric: tabular-nums;
  letter-spacing: 0;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 空值「—」降一级、用 --fg-faint，不像一根横线（第 1 轮审查 P1-8） */
.mh-kpi__num.is-empty {
  font-size: var(--fs-h1);
  line-height: var(--lh-stat);
  font-weight: 500;
  color: var(--fg-faint);
}

.mh-kpi__unit {
  /* 比数字（--fs-stat 28）小一级：15px / 500，--fg-muted，tabular-nums（「1,234 / 5,678」类单位也不抖） */
  font-size: var(--fs-h2);
  line-height: var(--lh-h2);
  font-weight: 500;
  color: var(--fg-muted);
  font-variant-numeric: tabular-nums;
  letter-spacing: 0;
  white-space: nowrap;
  margin-left: -4px;
}

.mh-kpi__delta {
  align-self: center;
  display: inline-flex;
  align-items: center;
  gap: 2px;
  height: 20px;
  padding: 0 6px;
  border-radius: var(--r-chip);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  font-weight: 500;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.mh-kpi__delta.is-good {
  color: var(--ok);
  background: var(--ok-soft);
}

.mh-kpi__delta.is-bad {
  color: var(--danger);
  background: var(--danger-soft);
}

.mh-kpi__delta.is-flat {
  color: var(--fg-muted);
  background: var(--neutral-soft);
}

.mh-kpi__spark {
  margin-top: 2px;
}

.mh-kpi__hint {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mh-kpi__skeleton {
  display: grid;
  gap: var(--sp-2);
  padding: 4px 0;
}

.mh-kpi__sk-num {
  width: 46%;
  height: 26px;
}

.mh-kpi__sk-line {
  width: 72%;
  height: 12px;
}

.mh-kpi__extra {
  margin-top: var(--sp-1);
  padding-top: var(--sp-2);
  border-top: 1px solid var(--border-soft);
  font-size: var(--fs-small);
  color: var(--fg-2);
  min-width: 0;
}
</style>
