<script setup lang="ts">
/**
 * 横向条形排行（替代饼图）：名称 + Meter + 数值 + 占比。
 * 最多显示 8 项，其余合并为「其他」（04-components §2 图表）。
 */
import { computed } from 'vue'
import { useI18n } from '@/i18n'
import Meter from './Meter.vue'
import TitleTip from '../base/TitleTip.vue'
import { badgeToneStyle } from '../base/badge'
import type { BarListItem } from './barlist'

const props = withDefaults(
  defineProps<{
    items: BarListItem[]
    limit?: number
    /** 分母：默认取全部条目之和 */
    total?: number
    unit?: string
    /** 数值格式化（默认千分位） */
    format?: (value: number) => string
  }>(),
  { limit: 8, total: undefined, unit: '', format: undefined },
)

const { t, localeTag } = useI18n()

const defaultFormat = (value: number): string =>
  Number.isFinite(value) ? value.toLocaleString(localeTag.value) : '—'

const rows = computed<BarListItem[]>(() => {
  const sorted = [...props.items].sort((a, b) => (b.value || 0) - (a.value || 0))
  if (sorted.length <= props.limit) return sorted
  const head = sorted.slice(0, props.limit)
  const rest = sorted.slice(props.limit)
  const restValue = rest.reduce((acc, item) => acc + (Number(item.value) || 0), 0)
  if (restValue > 0) head.push({ label: t('common.otherItems', { count: rest.length }), value: restValue, tone: 'neutral' })
  return head
})

const denominator = computed(() => {
  const explicit = props.total
  if (explicit !== undefined) return explicit
  return props.items.reduce((acc, item) => acc + (Number(item.value) || 0), 0)
})

/**
 * 条长按**最大项**缩放（第 2 轮审查 P2：按总数缩放时最长的条才 13%，8 根条都很短）；
 * 右侧百分比仍是占全部的份额（denominator）。
 */
const scaleMax = computed(() =>
  rows.value.reduce((max, item) => Math.max(max, Number(item.value) || 0), 0),
)

function pctOf(value: number): string {
  if (!denominator.value) return '0.0%'
  return ((value / denominator.value) * 100).toFixed(1) + '%'
}

const fmt = computed(() => props.format ?? defaultFormat)
</script>

<template>
  <ul class="mh-barlist">
    <li v-for="item in rows" :key="item.label" class="mh-barlist__row">
      <TitleTip :content="item.label"><span class="mh-barlist__label">{{ item.label }}</span></TitleTip>
      <Meter
        class="mh-barlist__meter"
        :value="item.value"
        :max="scaleMax || 1"
        :tone="item.tone ?? 'brand'"
        :aria-label="item.label + ' · ' + t('common.shareAria')"
      />
      <span class="mh-barlist__value tnum">{{ fmt(item.value) }}{{ unit }}</span>
      <span class="mh-barlist__pct tnum" :style="{ color: badgeToneStyle('neutral').text }">
        {{ pctOf(item.value) }}
      </span>
    </li>
  </ul>
</template>

<style scoped>
.mh-barlist {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: var(--sp-2);
}

.mh-barlist__row {
  display: grid;
  grid-template-columns: minmax(72px, 30%) 1fr auto auto;
  align-items: center;
  gap: var(--sp-3);
  min-width: 0;
}

.mh-barlist__label {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mh-barlist__value {
  font-size: var(--fs-small);
  color: var(--fg);
  text-align: right;
}

.mh-barlist__pct {
  font-size: var(--fs-small);
  text-align: right;
  width: 52px;
}

@media (max-width: 640px) {
  .mh-barlist__row {
    grid-template-columns: minmax(64px, 34%) 1fr auto;
  }

  .mh-barlist__pct {
    display: none;
  }
}
</style>
