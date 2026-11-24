<script setup lang="ts">
/**
 * 局部错误（10-visual-refresh §1.7）：danger-soft 底 + 朱色图标 + 原因（title）+ 下一步（hint，可选）
 * + 可展开的等宽详情 + 「重试」。
 */
import { computed, ref } from 'vue'
import { ChevronDown, TriangleAlert } from 'lucide-vue-next'
import Btn from './Btn.vue'
import { useI18n } from '@/i18n'

const props = withDefaults(
  defineProps<{
    title: string
    /** 下一步怎么办（一行灰字），可选。 */
    hint?: string
    /** 详细信息（后端 detail / 堆栈）——可展开，等宽显示。 */
    detail?: string
    retryLabel?: string
    /** 是否显示重试按钮。 */
    retry?: boolean
    compact?: boolean
  }>(),
  { hint: undefined, detail: undefined, retryLabel: undefined, retry: true, compact: false },
)

const emit = defineEmits<{ (event: 'retry'): void }>()

const { t } = useI18n()
const open = ref(false)
const hasDetail = computed(() => Boolean(props.detail && props.detail.trim()))
const retryText = computed(() => props.retryLabel ?? t('common.retry'))
</script>

<template>
  <div class="mh-error" :class="{ 'mh-error--compact': compact }" role="alert">
    <span class="mh-error__icon" aria-hidden="true"><TriangleAlert :size="16" /></span>
    <div class="mh-error__main">
      <p class="mh-error__title">{{ title }}</p>
      <p v-if="hint" class="mh-error__hint">{{ hint }}</p>
      <template v-if="hasDetail">
        <button class="mh-error__toggle" type="button" :aria-expanded="open" @click="open = !open">
          <ChevronDown :size="14" :class="{ 'is-open': open }" />
          {{ open ? t('common.collapseDetails') : t('common.expandDetails') }}
        </button>
        <pre v-if="open" class="mh-error__detail mono">{{ detail }}</pre>
      </template>
    </div>
    <Btn v-if="retry" variant="default" size="sm" @click="emit('retry')">{{ retryText }}</Btn>
  </div>
</template>

<style scoped>
.mh-error {
  display: flex;
  align-items: flex-start;
  gap: var(--sp-3);
  padding: var(--sp-4);
  /* 第 4 轮：边框原来与底色同为 --danger-soft，等于没有边；改成状态色 22% 的发丝边（不支持 color-mix 时退回原样） */
  border: 1px solid var(--danger-soft);
  border-color: color-mix(in srgb, var(--danger) 22%, transparent);
  border-radius: var(--r-card);
  background: var(--danger-soft);
}

.mh-error--compact {
  padding: var(--sp-3);
}

.mh-error__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: var(--surface);
  color: var(--danger);
  flex: none;
  margin-top: -3px;
}

.mh-error--compact .mh-error__icon {
  width: 22px;
  height: 22px;
  margin-top: 0;
}

.mh-error__hint {
  margin-top: 2px;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-2);
}

.mh-error__main {
  flex: 1;
  min-width: 0;
}

.mh-error__title {
  font-size: var(--fs-body);
  line-height: var(--lh-body);
  color: var(--fg);
  font-weight: 500;
}

.mh-error__toggle {
  margin-top: var(--sp-1);
  display: inline-flex;
  align-items: center;
  gap: var(--sp-1);
  border: 0;
  background: transparent;
  padding: 0;
  font-size: var(--fs-small);
  color: var(--fg-muted);
  cursor: pointer;
}

.mh-error__toggle:hover {
  color: var(--fg);
}

.mh-error__toggle svg {
  transition: transform var(--dur-2) var(--ease-out);
}

.mh-error__toggle .is-open {
  transform: rotate(180deg);
}

.mh-error__detail {
  margin: var(--sp-2) 0 0;
  padding: var(--sp-2);
  border-radius: var(--r-ctl);
  background: var(--surface);
  border: 1px solid var(--border);
  color: var(--fg-2);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 160px;
  overflow: auto;
}
</style>
