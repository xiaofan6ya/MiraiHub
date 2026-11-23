<script setup lang="ts">
/** 模型厂商图标：沿用旧版 model-icons.js 的映射，全部 currentColor 跟随主题。 */
import { computed } from 'vue'
import { Bot } from 'lucide-vue-next'
import { MODEL_ICONS } from './modelIcons'
import { resolveModelIconKey } from './modelIconResolve'
import { useI18n } from '@/i18n'

const props = withDefaults(
  defineProps<{
    /** 模型 id 或厂商名，二选一即可 */
    model?: string
    vendor?: string
    size?: number
    label?: string
  }>(),
  { model: undefined, vendor: undefined, size: 16, label: undefined },
)

const { t } = useI18n()
const key = computed(() => resolveModelIconKey(props.vendor || props.model))
const entry = computed(() => (key.value ? MODEL_ICONS[key.value] : undefined))
const fill = computed(() => (entry.value?.fill === 'solid' ? 'currentColor' : 'none'))
</script>

<template>
  <span
    class="mh-micon"
    :style="{ width: size + 'px', height: size + 'px' }"
    role="img"
    :aria-label="label || model || vendor || t('common.model')"
  >
    <svg
      v-if="entry"
      class="mh-micon__svg"
      :viewBox="entry.viewBox"
      :fill="fill"
      aria-hidden="true"
      v-html="entry.body"
    />
    <Bot v-else :size="size" aria-hidden="true" />
  </span>
</template>

<style scoped>
.mh-micon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
  color: var(--fg-2);
}

.mh-micon__svg {
  width: 100%;
  height: 100%;
  display: block;
}
</style>
