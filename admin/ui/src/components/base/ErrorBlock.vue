<script setup lang="ts">
/** 局部错误：朱色图标 + 错误标题 + 可展开的等宽详情 + 重试。 */
import { computed, ref } from 'vue'
import { ChevronDown, TriangleAlert } from 'lucide-vue-next'
import Btn from './Btn.vue'
import { useI18n } from '@/i18n'

const props = withDefaults(
  defineProps<{
    title: string
    /** 详细信息（后端 detail / 堆栈）——可展开，等宽显示。 */
    detail?: string
    retryLabel?: string
    /** 是否显示重试按钮。 */
    retry?: boolean
    compact?: boolean
  }>(),
  { detail: undefined, retryLabel: undefined, retry: true, compact: false },
)

const emit = defineEmits<{ (event: 'retry'): void }>()

const { t } = useI18n()
const open = ref(false)
const hasDetail = computed(() => Boolean(props.detail && props.detail.trim()))
const retryText = computed(() => props.retryLabel ?? t('common.retry'))
</script>

<template>
  <div class="mh-error" :class="{ 'mh-error--compact': compact }" role="alert">
    <TriangleAlert class="mh-error__icon" :size="18" />
    <div class="mh-error__main">
      <p class="mh-error__title">{{ title }}</p>
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
  border: 1px solid var(--danger-soft);
  border-radius: var(--r-ctl);
  background: var(--danger-soft);
}

.mh-error--compact {
  padding: var(--sp-3);
}

.mh-error__icon {
  color: var(--danger);
  flex: none;
  margin-top: 2px;
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
