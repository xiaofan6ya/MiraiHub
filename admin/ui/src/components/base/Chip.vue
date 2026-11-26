<script setup lang="ts">
/** 模型名 / 标签 chip：高 24px、--surface-2 底、1px --border-soft，可关闭、可点击复制。 */
import { computed } from 'vue'
import { NTag } from 'naive-ui'
import { copyText, useComponentToast } from '../_internal/feedback'
import { t } from '@/i18n'

const props = withDefaults(
  defineProps<{
    /** 关闭按钮 */
    closable?: boolean
    /** 点击复制 */
    copy?: boolean
    /** 复制的内容（不填则从默认插槽文案取不到，需显式传） */
    value?: string
    /** 等宽字体（模型 ID、Key） */
    mono?: boolean
  }>(),
  { closable: false, copy: false, value: undefined, mono: false },
)

const emit = defineEmits<{ (event: 'close'): void; (event: 'copied', value: string): void }>()

const toast = useComponentToast()

const tagColor = {
  color: 'var(--surface-2)',
  textColor: 'var(--fg-2)',
  borderColor: 'var(--border-soft)',
}

const interactive = computed(() => props.copy && Boolean(props.value))

async function onClick(): Promise<void> {
  if (!interactive.value) return
  const ok = await copyText(props.value || '')
  if (ok) {
    toast.success(t('common.copied'))
    emit('copied', props.value || '')
  } else {
    toast.error(t('common.copyFailed'))
  }
}
</script>

<template>
  <n-tag
    class="mh-chip"
    :class="{ 'mh-chip--copy': interactive, mono: mono }"
    :color="tagColor"
    :closable="closable"
    :bordered="true"
    :round="false"
    size="small"
    :role="interactive ? 'button' : undefined"
    :tabindex="interactive ? 0 : undefined"
    @close="emit('close')"
    @click="onClick"
  >
    <slot />
  </n-tag>
</template>

<style scoped>
.mh-chip {
  height: 24px;
  border-radius: var(--r-chip);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  --n-height: 24px;
  --n-border-radius: var(--r-chip);
}

.mh-chip--copy {
  cursor: pointer;
}

.mh-chip--copy:hover {
  --n-color: var(--surface-hover);
}
</style>
