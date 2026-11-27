<script setup lang="ts">
/** 分段控件：高 28px、--surface-2 底、选中项纸底 + 1px 边框。 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { SegmentedOption } from './segmented'
import { useI18n } from '@/i18n'

const props = withDefaults(
  defineProps<{
    options: Array<SegmentedOption<string | number>>
    modelValue?: string | number
    ariaLabel?: string
    /** 手机上允许横向滚动 */
    scrollable?: boolean
  }>(),
  { modelValue: undefined, ariaLabel: undefined, scrollable: false },
)

const emit = defineEmits<{ (event: 'update:modelValue', value: string | number): void }>()
const { t } = useI18n()
const labelText = computed(() => props.ariaLabel ?? t('common.segmentedAria'))
const host = ref<HTMLElement | null>(null)
const indicator = ref<{ x: number; y: number; width: number; height: number } | null>(null)
let observer: ResizeObserver | undefined

function measure(): void {
  const active = host.value?.querySelector<HTMLElement>('.is-active')
  if (!active || !active.offsetWidth) { indicator.value = null; return }
  const next = { x: active.offsetLeft, y: active.offsetTop, width: active.offsetWidth, height: active.offsetHeight }
  const previous = indicator.value
  if (!previous || Object.keys(next).some(key => next[key as keyof typeof next] !== previous[key as keyof typeof next])) indicator.value = next
}
function observeItems(): void {
  observer?.disconnect()
  if (!host.value) return
  observer?.observe(host.value)
  host.value.querySelectorAll('.mh-seg__item').forEach(item => observer?.observe(item))
}
watch(() => props.modelValue, measure, { flush: 'post' })
watch(() => props.options, () => { observeItems(); measure() }, { deep: true, flush: 'post' })
onMounted(() => {
  if (typeof ResizeObserver !== 'undefined') observer = new ResizeObserver(measure)
  observeItems()
  measure()
  window.addEventListener('resize', measure)
  void document.fonts?.ready.then(measure)
})
onBeforeUnmount(() => { observer?.disconnect(); window.removeEventListener('resize', measure) })


function select(value: string | number): void {
  if (value === props.modelValue) return
  emit('update:modelValue', value)
}
</script>

<template>
  <div
    ref="host"
    class="mh-seg"
    :class="{ 'mh-seg--scroll': scrollable, 'has-indicator': indicator }"
    role="radiogroup"
    :aria-label="labelText"
  >
    <span
      v-if="indicator"
      class="mh-seg__indicator"
      aria-hidden="true"
      :style="{ transform: `translate3d(${indicator.x}px, ${indicator.y}px, 0)`, width: indicator.width + 'px', height: indicator.height + 'px' }"
    />
    <button
      v-for="option in options"
      :key="String(option.value)"
      class="mh-seg__item"
      :class="{ 'is-active': option.value === modelValue }"
      type="button"
      role="radio"
      :aria-checked="option.value === modelValue"
      @click="select(option.value)"
    >
      <component :is="option.icon" v-if="option.icon" :size="14" />
      <span>{{ option.label }}</span>
    </button>
  </div>
</template>

<style scoped>
.mh-seg {
  position: relative;
  isolation: isolate;
  display: inline-flex;
  align-items: center;
  gap: 2px;
  height: 28px;
  padding: 2px;
  border-radius: var(--r-ctl);
  background: var(--surface-2);
}

.mh-seg--scroll {
  max-width: 100%;
  overflow-x: auto;
}

.mh-seg__item {
  position: relative;
  z-index: 1;
  display: inline-flex;
  align-items: center;
  gap: var(--sp-1);
  height: 24px;
  padding: 0 var(--sp-3);
  border: 1px solid transparent;
  border-radius: var(--r-chip);
  background: transparent;
  color: var(--fg-muted);
  font-size: var(--fs-small);
  font-weight: 500;
  line-height: var(--lh-small);
  cursor: pointer;
  white-space: nowrap;
  transition: background var(--t-fast) var(--ease), color var(--t-fast) var(--ease);
}

.mh-seg__item:hover {
  color: var(--fg);
}

.mh-seg__item.is-active {
  background: var(--surface);
  border-color: var(--border);
  color: var(--fg);
  font-weight: 500;
}

.mh-seg__indicator {
  position: absolute;
  top: 0;
  left: 0;
  box-sizing: border-box;
  border: 1px solid var(--border);
  border-radius: var(--r-chip);
  background: var(--surface);
  box-shadow: var(--shadow-1);
  pointer-events: none;
  transition: transform 280ms cubic-bezier(.22,1,.36,1), width 280ms cubic-bezier(.22,1,.36,1), height 280ms cubic-bezier(.22,1,.36,1);
}
.has-indicator .mh-seg__item.is-active { background: transparent; border-color: transparent; }
.mh-seg__item:focus-visible { outline: 2px solid var(--brand); outline-offset: -2px; }
@media (prefers-reduced-motion: reduce) {
  .mh-seg__indicator, .mh-seg__item { transition: none; }
}
</style>
