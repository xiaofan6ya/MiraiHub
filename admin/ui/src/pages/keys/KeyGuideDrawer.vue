<script setup lang="ts">
/** 接入指南抽屉：按协议分页签给出 curl / Python 示例（地址与模型按当前连接地址填好）。 */
import { ref } from 'vue'
import { NTabPane, NTabs } from 'naive-ui'
import { Btn, CodeBlock, DetailDrawer } from '@/components'
import { useI18n } from '@/i18n'
import type { CurlExamples } from '@/api/keys'

defineProps<{ show: boolean; examples: CurlExamples }>()
const emit = defineEmits<{ (event: 'update:show', value: boolean): void }>()

const { t } = useI18n()
const tab = ref('openai')
</script>

<template>
  <DetailDrawer
    :show="show"
    :title="t('page.keys.guide.title')"
    :subtitle="t('page.keys.guide.subtitle')"
    :width="720"
    @update:show="(value: boolean) => emit('update:show', value)"
  >
    <NTabs v-model:value="tab" type="line" animated>
      <NTabPane name="openai" :tab="t('page.keys.guide.openai')">
        <CodeBlock :code="examples.openai" language="bash" />
      </NTabPane>
      <NTabPane name="claude" tab="Claude Code / Anthropic">
        <CodeBlock :code="examples.claude" language="bash" />
      </NTabPane>
      <NTabPane name="audio" :tab="t('page.keys.guide.audio')">
        <p class="kg-hint">{{ t('page.keys.guide.audioNote') }}</p>
        <div class="kg-stack">
          <CodeBlock :code="examples.audioCurl" language="bash" />
          <CodeBlock :code="examples.audioPython" language="python" />
        </div>
      </NTabPane>
      <NTabPane name="images" :tab="t('page.keys.guide.images')">
        <p class="kg-hint">{{ t('page.keys.guide.imagesNote') }}</p>
        <CodeBlock :code="examples.imagesPython" language="python" />
      </NTabPane>
      <NTabPane name="videos" :tab="t('page.keys.guide.videos')">
        <p class="kg-hint">{{ t('page.keys.guide.videosNote') }}</p>
        <div class="kg-stack">
          <CodeBlock :code="examples.videoCurl" language="bash" />
          <CodeBlock :code="examples.videoPython" language="python" />
        </div>
      </NTabPane>
    </NTabs>
    <template #footer>
      <Btn variant="default" @click="emit('update:show', false)">{{ t('common.close') }}</Btn>
    </template>
  </DetailDrawer>
</template>

<style scoped>
.kg-hint {
  margin: 0 0 var(--sp-2);
  max-width: 80ch;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  text-wrap: pretty;
}

.kg-stack {
  display: grid;
  gap: var(--sp-2);
}
</style>
