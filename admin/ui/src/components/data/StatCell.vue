<script setup lang="ts">
/** 统计格内容（StatCard 与 StatStrip 共用）：标签 / 数值 / 单位 / 脚注。 */
import { computed } from 'vue'
import HelpTip from '../base/HelpTip.vue'
import { statToneColor, type StatItem } from './stat'
import { useI18n } from '@/i18n'

const props = defineProps<{ item: StatItem; compact?: boolean }>()
const { t } = useI18n()

const color = computed(() => statToneColor(props.item.tone))
/** 空值「—」：降一级字号、--fg-faint（第 1 轮审查 P1-8：大号「—」像一根横线）。 */
const isEmpty = computed(() => {
  const value = props.item.value
  return value === null || value === undefined || String(value).trim() === '' || String(value).trim() === '—'
})
</script>

<template>
  <div class="mh-stat" :class="{ 'mh-stat--compact': compact }">
    <div class="mh-stat__label">
      <span>{{ item.label }}</span>
      <HelpTip
        v-if="item.help"
        :content="item.help"
        :label="item.label + ' · ' + t('common.help')"
      />
    </div>
    <div class="mh-stat__value" :style="{ color }">
      <span class="mh-stat__num" :class="{ 'is-empty': isEmpty }">{{ item.value }}</span>
      <span v-if="item.unit" class="mh-stat__unit">{{ item.unit }}</span>
    </div>
    <div v-if="item.foot" class="mh-stat__foot">{{ item.foot }}</div>
  </div>
</template>

<style scoped>
.mh-stat {
  display: grid;
  gap: var(--sp-1);
  min-width: 0;
}

.mh-stat__label {
  display: flex;
  align-items: center;
  gap: var(--sp-1);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  font-weight: 500;
  color: var(--fg-muted);
  min-width: 0;
}

.mh-stat__label > span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mh-stat__value {
  display: flex;
  align-items: baseline;
  gap: var(--sp-1);
  min-width: 0;
}

.mh-stat__num {
  font-size: var(--fs-stat);
  line-height: var(--lh-stat);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mh-stat__unit {
  font-size: var(--fs-body);
  line-height: var(--lh-body);
  color: var(--fg-muted);
  flex: none;
}

.mh-stat__foot {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mh-stat__num.is-empty {
  font-size: var(--fs-h1);
  font-weight: 500;
  color: var(--fg-faint);
}

.mh-stat--compact .mh-stat__num {
  font-size: var(--fs-h1);
  line-height: var(--lh-h1);
}
</style>
