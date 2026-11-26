<script setup lang="ts">
/** 水平进度条：高 6px、圆角 3px、底 --border-soft，填充按 tone。 */
import { computed } from 'vue'
import { badgeToneStyle, type BadgeTone } from '../base/badge'
import { useI18n } from '@/i18n'

const props = withDefaults(
  defineProps<{
    /** 当前值 */
    value: number
    /** 满值，默认 100 */
    max?: number
    tone?: BadgeTone
    /** 右侧数值文字 */
    showValue?: boolean
    valueText?: string
    height?: number
    ariaLabel?: string
  }>(),
  {
    max: 100,
    tone: 'brand',
    showValue: false,
    valueText: undefined,
    height: 6,
    ariaLabel: undefined,
  },
)

const ratio = computed(() => {
  const max = props.max || 100
  if (!Number.isFinite(props.value) || max <= 0) return 0
  return Math.min(1, Math.max(0, props.value / max))
})

const { t } = useI18n()
const ariaText = computed(() => props.ariaLabel ?? t('common.shareAria'))
const fill = computed(() => badgeToneStyle(props.tone).text)
const text = computed(() => props.valueText ?? Math.round(ratio.value * 100) + '%')
</script>

<template>
  <div class="mh-meter">
    <div
      class="mh-meter__track"
      :style="{ height: height + 'px' }"
      role="progressbar"
      :aria-label="ariaText"
      :aria-valuenow="Math.round(ratio * 100)"
      aria-valuemin="0"
      aria-valuemax="100"
    >
      <div class="mh-meter__fill" :style="{ width: ratio * 100 + '%', background: fill }" />
    </div>
    <span v-if="showValue" class="mh-meter__text tnum">{{ text }}</span>
  </div>
</template>

<style scoped>
.mh-meter {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  min-width: 0;
  width: 100%;
}

.mh-meter__track {
  flex: 1;
  min-width: 0;
  border-radius: 50px;
  background: var(--border-soft);
  overflow: hidden;
}

.mh-meter__fill {
  height: 100%;
  border-radius: inherit;
  transition: width var(--t-base) var(--ease);
}

.mh-meter__text {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  flex: none;
}
</style>
