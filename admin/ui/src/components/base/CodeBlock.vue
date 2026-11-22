<script setup lang="ts">
/** 等宽代码块：--surface-2 底、圆角 7px、右上角复制，可选语言标签。 */
import { Copy } from 'lucide-vue-next'
import { NButton } from 'naive-ui'
import { copyText, useComponentToast } from '../_internal/feedback'

const props = withDefaults(
  defineProps<{
    code: string
    /** 语言标签，如 bash / json */
    language?: string
    maxHeight?: string
  }>(),
  { language: undefined, maxHeight: '320px' },
)

const toast = useComponentToast()

async function onCopy(): Promise<void> {
  const ok = await copyText(props.code)
  if (ok) toast.success('已复制')
  else toast.error('复制失败：浏览器拒绝了剪贴板访问，请手动选中复制')
}
</script>

<template>
  <div class="mh-code">
    <div class="mh-code__bar">
      <span v-if="language" class="mh-code__lang mono">{{ language }}</span>
      <span v-else />
      <n-button
        quaternary
        size="tiny"
        class="mh-code__copy"
        aria-label="复制"
        title="复制"
        @click="onCopy"
      >
        <template #icon><Copy :size="14" /></template>
        复制
      </n-button>
    </div>
    <pre class="mh-code__pre mono" :style="{ maxHeight }"><code>{{ code }}</code></pre>
  </div>
</template>

<style scoped>
.mh-code {
  background: var(--surface-2);
  border: 1px solid var(--border-soft);
  border-radius: var(--r-ctl);
  overflow: hidden;
}

.mh-code__bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-2);
  padding: var(--sp-1) var(--sp-2) 0;
}

.mh-code__lang {
  color: var(--fg-muted);
  font-size: var(--fs-tiny);
  line-height: var(--lh-tiny);
  text-transform: none;
}

.mh-code__copy {
  --n-text-color: var(--fg-muted);
  --n-text-color-hover: var(--fg);
  --n-color-hover: var(--surface-hover);
  --n-ripple-color: transparent;
  font-size: var(--fs-small);
}

.mh-code__pre {
  margin: 0;
  padding: var(--sp-2) var(--sp-3) var(--sp-3);
  overflow: auto;
  color: var(--fg);
  font-size: 12.5px;
  line-height: 1.6;
  white-space: pre;
}
</style>
