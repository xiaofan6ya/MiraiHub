<script setup lang="ts">
/**
 * 智能体 · 右栏「模型与参数」卡（从 AgentPage 拆出，r3）。
 * 每个字段「标签在上 · 控件在下」，字段之间 12px；图片 / 视频才出现的字段在分隔线下方成组。
 */
import { computed } from 'vue'
import { NSelect } from 'naive-ui'
import { Card } from '@/components'
import { useI18n } from '@/i18n'
import { toThinkingPref, type AgentMode, type ThinkingPref } from './agentPrefs'

defineProps<{
  mode: AgentMode
  modelOptions: Array<{ label: string; value: string }>
  model: string
  thinking: ThinkingPref
  reasoning: string
  imageSize: string
  videoSeconds: string
  videoResolution: string
  videoAspect: string
  estimate: number | null
}>()

const emit = defineEmits<{
  (event: 'update:model', value: string): void
  (event: 'update:thinking', value: ThinkingPref): void
  (event: 'update:reasoning', value: string): void
  (event: 'update:imageSize', value: string): void
  (event: 'update:videoSeconds', value: string): void
  (event: 'update:videoResolution', value: string): void
  (event: 'update:videoAspect', value: string): void
}>()

const { t } = useI18n()

const reasoningOptions = computed(() => [
  { label: t('page.agent.reasoningOption.default'), value: '' },
  { label: t('page.agent.reasoningOption.low'), value: 'low' },
  { label: t('page.agent.reasoningOption.medium'), value: 'medium' },
  { label: t('page.agent.reasoningOption.high'), value: 'high' },
  { label: t('page.agent.reasoningOption.max'), value: 'max' },
])

const thinkingOptions = computed(() => [
  { label: t('page.agent.thinkingOption.default'), value: 'default' },
  { label: t('page.agent.thinkingOption.on'), value: 'on' },
  { label: t('page.agent.thinkingOption.off'), value: 'off' },
])

const imageSizeOptions = ['1024x1024', '1024x1536', '1280x720', '1536x1024'].map((value) => ({ label: value, value }))
const resolutionOptions = ['720P', '1080P'].map((value) => ({ label: value, value }))
const aspectOptions = ['16:9', '9:16', '1:1'].map((value) => ({ label: value, value }))
const secondsOptions = computed(() =>
  [5, 6, 8, 10].map((value) => ({ label: t('page.agent.seconds', { seconds: value }), value: String(value) })),
)
</script>

<template>
  <Card :title="t('page.agent.card.model')">
    <div class="agent-fields">
      <label class="agent-field">
        <span class="agent-label">{{ t('page.agent.field.model') }}</span>
        <n-select
          :value="model"
          :options="modelOptions"
          size="small"
          filterable
          :placeholder="t('page.agent.selectModel')"
          @update:value="(value: string | number | null) => emit('update:model', String(value || ''))"
        />
      </label>
      <div class="agent-field-row">
        <label class="agent-field">
          <span class="agent-label">{{ t('page.agent.field.thinking') }}</span>
          <n-select
            :value="thinking"
            :options="thinkingOptions"
            size="small"
            @update:value="(value: string | number | null) => emit('update:thinking', toThinkingPref(value))"
          />
        </label>
        <label class="agent-field">
          <span class="agent-label">{{ t('page.agent.field.reasoning') }}</span>
          <n-select
            :value="reasoning"
            :options="reasoningOptions"
            size="small"
            @update:value="(value: string | number | null) => emit('update:reasoning', String(value || ''))"
          />
        </label>
      </div>

      <div v-if="mode === 'image'" class="agent-fields agent-fields--media">
        <label class="agent-field">
          <span class="agent-label">{{ t('page.agent.field.size') }}</span>
          <n-select
            :value="imageSize"
            :options="imageSizeOptions"
            size="small"
            @update:value="(value: string | number | null) => emit('update:imageSize', String(value || ''))"
          />
        </label>
      </div>
      <div v-else-if="mode === 'video'" class="agent-fields agent-fields--media">
        <label class="agent-field">
          <span class="agent-label">{{ t('page.agent.field.seconds') }}</span>
          <n-select
            :value="videoSeconds"
            :options="secondsOptions"
            size="small"
            @update:value="(value: string | number | null) => emit('update:videoSeconds', String(value || '5'))"
          />
        </label>
        <div class="agent-field-row">
          <label class="agent-field">
            <span class="agent-label">{{ t('page.agent.field.resolution') }}</span>
            <n-select
              :value="videoResolution"
              :options="resolutionOptions"
              size="small"
              @update:value="(value: string | number | null) => emit('update:videoResolution', String(value || ''))"
            />
          </label>
          <label class="agent-field">
            <span class="agent-label">{{ t('page.agent.field.aspect') }}</span>
            <n-select
              :value="videoAspect"
              :options="aspectOptions"
              size="small"
              @update:value="(value: string | number | null) => emit('update:videoAspect', String(value || ''))"
            />
          </label>
        </div>
      </div>

      <p class="agent-billing">
        <template v-if="mode === 'chat'">{{ t('page.agent.chatBilling') }}</template>
        <template v-else>
          {{ t('page.agent.estimateNote', {
            value: estimate === null ? t('page.agent.estimateNone') : t('page.agent.credits', { credits: estimate }),
          }) }}
        </template>
      </p>
    </div>
  </Card>
</template>

<style scoped>
.agent-fields {
  display: grid;
  gap: var(--sp-3);
}

.agent-fields--media {
  padding-top: var(--sp-3);
  border-top: 1px solid var(--border-soft);
}

.agent-field-row {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--sp-2);
}

.agent-field {
  display: grid;
  gap: 4px;
  min-width: 0;
}

.agent-label {
  font-size: var(--fs-tiny);
  line-height: var(--lh-tiny);
  font-weight: 500;
  color: var(--fg-muted);
}

.agent-billing {
  margin: 0;
  padding: var(--sp-2) var(--sp-3);
  border-radius: var(--r-ctl);
  background: var(--surface-2);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  font-variant-numeric: tabular-nums;
}
</style>
