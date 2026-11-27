<script setup lang="ts">
/** 等宽文本 + 复制按钮；mask 时打码，Eye 切换明文。 */
import { computed, ref } from 'vue'
import { Copy, Eye, EyeOff } from 'lucide-vue-next'
import { NButton } from 'naive-ui'
import TitleTip from './TitleTip.vue'
import { copyText, useComponentToast } from '../_internal/feedback'
import { maskValue } from '../_internal/text'
import { t } from '@/i18n'

const props = withDefaults(
  defineProps<{
    value: string
    /** 打码显示（Key / 令牌） */
    mask?: boolean
    /** 等宽字号，默认 12.5px（tokens.css .mono） */
    muted?: boolean
  }>(),
  { mask: false, muted: false },
)

const toast = useComponentToast()
const revealed = ref(false)

const display = computed(() => {
  if (!props.mask || revealed.value) return props.value
  return maskValue(props.value)
})

async function onCopy(): Promise<void> {
  const ok = await copyText(props.value)
  if (ok) toast.success(t('common.copied'))
  else toast.error(t('common.copyFailed'))
}
</script>

<template>
  <span class="mh-copy" :class="{ 'mh-copy--muted': muted }">
    <code class="mh-copy__text mono">{{ display }}</code>
    <TitleTip v-if="mask" :content="revealed ? t('common.hideValue') : t('common.showValue')"><n-button
      quaternary
      size="tiny"
      class="mh-copy__btn"
      :aria-label="revealed ? t('common.hideValue') : t('common.showValue')"
      @click="revealed = !revealed"
    >
      <template #icon><component :is="revealed ? EyeOff : Eye" :size="14" /></template>
    </n-button></TitleTip>
    <TitleTip :content="t('common.copy')"><n-button
      quaternary
      size="tiny"
      class="mh-copy__btn"
      :aria-label="t('common.copy')"
      @click="onCopy"
    >
      <template #icon><Copy :size="14" /></template>
    </n-button></TitleTip>
  </span>
</template>

<style scoped>
.mh-copy {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-1);
  min-width: 0;
  max-width: 100%;
}

.mh-copy__text {
  font-family: var(--font-mono);
  font-size: 12.5px;
  color: var(--fg);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mh-copy--muted .mh-copy__text {
  color: var(--fg-2);
}

.mh-copy__btn {
  --n-text-color: var(--fg-muted);
  --n-text-color-hover: var(--fg);
  --n-text-color-pressed: var(--fg);
  --n-color-hover: var(--surface-hover);
  --n-ripple-color: transparent;
  width: 24px;
  height: 24px;
  padding: 0;
  flex: none;
}
</style>
