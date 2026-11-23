<script setup lang="ts">
/** 环形图（缓存命中率这类单一比值）：直径 104px、线宽 10px、中间数值。 */
import { computed } from 'vue'
import { badgeToneStyle, type BadgeTone } from '../base/badge'
import { useI18n } from '@/i18n'

const props = withDefaults(
  defineProps<{
    /** 0–100 */
    value: number
    /** 中间数值文字（默认取 value 取整 + %） */
    text?: string
    /** 数值后的单位/说明 */
    unit?: string
    label?: string
    tone?: BadgeTone
    size?: number
    strokeWidth?: number
  }>(),
  {
    text: undefined,
    unit: '',
    label: undefined,
    tone: 'brand',
    size: 104,
    strokeWidth: 10,
  },
)

const ratio = computed(() => {
  if (!Number.isFinite(props.value)) return 0
  return Math.min(1, Math.max(0, props.value / 100))
})

const radius = computed(() => (props.size - props.strokeWidth) / 2)
const circumference = computed(() => 2 * Math.PI * radius.value)
const dash = computed(() => circumference.value * ratio.value)
const color = computed(() => badgeToneStyle(props.tone).text)
const { t } = useI18n()
const centerText = computed(() => props.text ?? Math.round(ratio.value * 100) + '%')
const ariaText = computed(() => props.label || t('common.shareAria') + ' ' + centerText.value)
</script>

<template>
  <div class="mh-ring">
    <div class="mh-ring__dial" :style="{ width: size + 'px', height: size + 'px' }">
      <svg
        :width="size"
        :height="size"
        :viewBox="`0 0 ${size} ${size}`"
        role="img"
        :aria-label="ariaText"
      >
        <circle
          :cx="size / 2"
          :cy="size / 2"
          :r="radius"
          fill="none"
          stroke="var(--border-soft)"
          :stroke-width="strokeWidth"
        />
        <circle
          :cx="size / 2"
          :cy="size / 2"
          :r="radius"
          fill="none"
          :stroke="color"
          :stroke-width="strokeWidth"
          stroke-linecap="round"
          :stroke-dasharray="`${dash} ${circumference}`"
          :transform="`rotate(-90 ${size / 2} ${size / 2})`"
        />
      </svg>
      <div class="mh-ring__center">
        <span class="mh-ring__value">{{ centerText }}</span>
        <span v-if="unit" class="mh-ring__unit">{{ unit }}</span>
      </div>
    </div>
    <div v-if="label" class="mh-ring__label">{{ label }}</div>
  </div>
</template>

<style scoped>
.mh-ring {
  display: inline-grid;
  justify-items: center;
  gap: var(--sp-2);
}

.mh-ring__dial {
  position: relative;
  display: grid;
  place-items: center;
}

.mh-ring svg {
  display: block;
}

.mh-ring__center {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 2px;
  pointer-events: none;
}

.mh-ring__value {
  font-size: 22px;
  line-height: 30px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--fg);
}

.mh-ring__unit {
  font-size: var(--fs-small);
  color: var(--fg-muted);
  align-self: center;
}

.mh-ring__label {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}
</style>
