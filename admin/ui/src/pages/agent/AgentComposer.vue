<script setup lang="ts">
/**
 * 智能体 · 输入框卡（从 AgentPage 拆出，r3）。
 * 整卡一个边框：聚焦时换 --border-strong + 3px --ring 外环；textarea 无边框、1–8 行自适应；
 * 工具栏紧贴输入框：附件 · 模式（对话开始后才出现）·（右）快捷键提示 · 发送 / 停止（全页唯一主按钮）。
 */
import { ref } from 'vue'
import { NInput } from 'naive-ui'
import { Paperclip, Send, Square, X } from 'lucide-vue-next'
import { Btn, IconBtn, Segmented, type SegmentedOption } from '@/components'
import { useI18n } from '@/i18n'
import { toAgentMode, type AgentMode } from './agentPrefs'

defineProps<{
  modelValue: string
  mode: AgentMode
  showMode: boolean
  sending: boolean
  attachments: Array<{ name: string; size: number }>
  modeOptions: Array<SegmentedOption<string>>
}>()

const emit = defineEmits<{
  (event: 'update:modelValue', value: string): void
  (event: 'update:mode', value: AgentMode): void
  (event: 'send'): void
  (event: 'files', payload: Event): void
  (event: 'remove-attachment', index: number): void
}>()

const { t } = useI18n()
const fileInput = ref<HTMLInputElement | null>(null)

function onKeydown(event: KeyboardEvent): void {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return
  event.preventDefault()
  emit('send')
}
</script>

<template>
  <div class="agent-composer">
    <div v-if="attachments.length" class="agent-attachments">
      <span v-for="(file, index) in attachments" :key="file.name + index" class="agent-attachment">
        <Paperclip :size="14" aria-hidden="true" />
        <span class="agent-attachment__name">{{ file.name }}</span>
        <button type="button" :aria-label="t('page.agent.removeAttachment')" @click="emit('remove-attachment', index)">
          <X :size="14" />
        </button>
      </span>
    </div>
    <n-input
      class="agent-composer__input"
      :value="modelValue"
      type="textarea"
      :bordered="false"
      :autosize="{ minRows: 1, maxRows: 8 }"
      :placeholder="
        mode === 'image'
          ? t('page.agent.placeholder.image')
          : mode === 'video'
            ? t('page.agent.placeholder.video')
            : t('page.agent.placeholder.chat')
      "
      @update:value="(value: string) => emit('update:modelValue', value)"
      @keydown="onKeydown"
    />
    <div class="agent-composer__bar">
      <IconBtn :label="t('page.agent.addAttachment')" :icon="Paperclip" @click="fileInput?.click()" />
      <input ref="fileInput" class="agent-file" type="file" multiple @change="(event: Event) => emit('files', event)" />
      <Segmented
        v-if="showMode"
        :options="modeOptions"
        :model-value="mode"
        :aria-label="t('page.agent.modeAria')"
        @update:model-value="(value: string | number) => emit('update:mode', toAgentMode(value))"
      />
      <span class="agent-composer__spacer" />
      <span class="agent-composer__hint">{{ t('page.agent.newlineHint') }}</span>
      <Btn :variant="sending ? 'danger' : 'primary'" :icon="sending ? Square : Send" @click="emit('send')">
        {{ sending ? t('page.agent.stop') : t('page.agent.send') }}
      </Btn>
    </div>
  </div>
</template>

<style scoped>
.agent-composer {
  display: grid;
  align-content: start;
  gap: var(--sp-2);
  padding: var(--sp-2) var(--sp-3) var(--sp-3);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-lg, 14px);
  box-shadow: var(--shadow-2);
  transition:
    border-color var(--dur-1, 120ms) var(--ease-out, ease),
    box-shadow var(--dur-1, 120ms) var(--ease-out, ease);
}

.agent-composer:focus-within {
  border-color: var(--border-strong);
  box-shadow: var(--shadow-2), 0 0 0 3px var(--ring);
}

/* textarea 融进卡片：去掉 Naive 自带的底色与聚焦光晕（聚焦反馈由整卡外环承担）。 */
.agent-composer__input {
  --n-padding-left: 2px !important;
  --n-padding-right: 2px !important;
  --n-color: transparent !important;
  --n-color-focus: transparent !important;
  --n-box-shadow-focus: none !important;
  background: transparent;
}

.agent-composer__input :deep(.n-input__textarea-el),
.agent-composer__input :deep(.n-input__placeholder) {
  font-size: var(--fs-body);
  line-height: var(--lh-body);
}

.agent-composer__input :deep(.n-input-wrapper) {
  padding-top: var(--sp-1);
  padding-bottom: 0;
}

.agent-composer__bar {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  min-width: 0;
}

.agent-composer__spacer {
  flex: 1 1 auto;
}

.agent-composer__hint {
  font-size: var(--fs-tiny);
  color: var(--fg-faint);
  white-space: nowrap;
}

.agent-file {
  display: none;
}

.agent-attachments {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-1);
  padding-top: var(--sp-1);
}

.agent-attachment {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  max-width: 220px;
  height: 24px;
  padding: 0 4px 0 var(--sp-2);
  border-radius: var(--r-chip);
  background: var(--surface-2);
  border: 1px solid var(--border-soft);
  font-size: var(--fs-tiny);
  color: var(--fg-2);
}

.agent-attachment__name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.agent-attachment button {
  display: inline-flex;
  padding: 2px;
  border: 0;
  border-radius: var(--r-chip);
  background: transparent;
  color: var(--fg-muted);
  cursor: pointer;
}

.agent-attachment button:hover {
  background: var(--surface-hover);
  color: var(--fg);
}

.agent-attachment button:focus-visible {
  outline: none;
  box-shadow: 0 0 0 3px var(--ring);
}

@media (max-width: 640px) {
  .agent-composer__hint {
    display: none;
  }
}

@media (prefers-reduced-motion: reduce) {
  .agent-composer {
    transition: none;
  }
}
</style>
