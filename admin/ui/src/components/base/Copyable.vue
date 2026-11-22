<script setup lang="ts">
/** 等宽文本 + 复制按钮；mask 时打码，Eye 切换明文。 */
import { computed, ref } from 'vue'
import { Copy, Eye, EyeOff } from 'lucide-vue-next'
import { NButton } from 'naive-ui'
import { copyText, useComponentToast } from '../_internal/feedback'
import { maskValue } from '../_internal/text'

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
  if (ok) toast.success('已复制')
  else toast.error('复制失败：浏览器拒绝了剪贴板访问，请手动选中复制')
}
</script>

<template>
  <span class="mh-copy" :class="{ 'mh-copy--muted': muted }">
    <code class="mh-copy__text mono">{{ display }}</code>
    <n-button
      v-if="mask"
      quaternary
      size="tiny"
      class="mh-copy__btn"
      :aria-label="revealed ? '隐藏明文' : '显示明文'"
      :title="revealed ? '隐藏明文' : '显示明文'"
      @click="revealed = !revealed"
    >
      <template #icon><component :is="revealed ? EyeOff : Eye" :size="14" /></template>
    </n-button>
    <n-button
      quaternary
      size="tiny"
      class="mh-copy__btn"
      aria-label="复制"
      title="复制"
      @click="onCopy"
    >
      <template #icon><Copy :size="14" /></template>
    </n-button>
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
