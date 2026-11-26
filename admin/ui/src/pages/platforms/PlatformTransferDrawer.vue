<script setup lang="ts">
/**
 * 平台管理 · 导入抽屉 / 导出抽屉（从 PlatformsPage 拆出，r3）。只有表单外观，读文件和提交仍在页面。
 */
import { computed } from 'vue'
import { NSelect } from 'naive-ui'
import { FileJson } from 'lucide-vue-next'
import { FormDrawer, Segmented, type SegmentedOption } from '@/components'
import { useI18n } from '@/i18n'

const props = defineProps<{
  kind: 'import' | 'export'
  show: boolean
  /* 导入 */
  preview?: Array<{ storage: string; count: number }>
  notes?: string[]
  submitting?: boolean
  /* 导出 */
  exporting?: boolean
  mode?: 'backup' | 'plain'
  scope?: string
  scopeOptions?: Array<{ label: string; value: string }>
}>()

const emit = defineEmits<{
  (event: 'update:show', show: boolean): void
  (event: 'files', payload: Event): void
  (event: 'submit'): void
  (event: 'update:mode', mode: 'backup' | 'plain'): void
  (event: 'update:scope', scope: string): void
}>()

const { t } = useI18n()

const modeOptions = computed<Array<SegmentedOption<string>>>(() => [
  { label: t('page.platforms.export.backup'), value: 'backup' },
  { label: t('page.platforms.export.plain'), value: 'plain' },
])

const previewList = computed(() => props.preview || [])
const noteList = computed(() => props.notes || [])
</script>

<template>
  <FormDrawer
    v-if="kind === 'import'"
    :show="show"
    :title="t('page.platforms.import.title')"
    :submit-text="t('page.platforms.import.submit')"
    :submit-disabled="!previewList.length || submitting"
    :submit-loading="submitting"
    @update:show="(value: boolean) => emit('update:show', value)"
    @submit="emit('submit')"
  >
    <p class="pf-hint">{{ t('page.platforms.import.hint') }}</p>
    <label class="pf-drop">
      <FileJson :size="20" aria-hidden="true" />
      <input
        class="pf-file"
        type="file"
        accept=".json,application/json"
        multiple
        :aria-label="t('page.platforms.import.fileAria')"
        @change="(event: Event) => emit('files', event)"
      />
    </label>
    <ul v-if="previewList.length" class="pf-preview">
      <li v-for="item in previewList" :key="item.storage">
        <span class="pf-preview__name">{{ item.storage }}</span>
        <span class="pf-preview__count">{{ t('page.platforms.import.previewCount', { count: item.count }) }}</span>
      </li>
    </ul>
    <p v-for="(note, index) in noteList" :key="index" class="pf-hint">{{ note }}</p>
  </FormDrawer>

  <FormDrawer
    v-else
    :show="show"
    :title="t('page.platforms.export.title')"
    :submit-text="t('page.platforms.export.start')"
    :submit-loading="exporting"
    @update:show="(value: boolean) => emit('update:show', value)"
    @submit="emit('submit')"
  >
    <p class="pf-hint">{{ t('page.platforms.export.hint') }}</p>
    <Segmented
      :options="modeOptions"
      :model-value="mode || 'backup'"
      :aria-label="t('page.platforms.export.modeAria')"
      @update:model-value="(value: string | number) => emit('update:mode', String(value) === 'plain' ? 'plain' : 'backup')"
    />
    <n-select
      :value="scope ?? ''"
      :options="scopeOptions || []"
      :aria-label="t('page.platforms.export.scopeAria')"
      :placeholder="t('page.platforms.export.scopeAria')"
      @update:value="(value: string | number | null) => emit('update:scope', String(value ?? ''))"
    />
  </FormDrawer>
</template>

<style scoped>
.pf-hint {
  margin: 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.pf-drop {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  padding: var(--sp-4);
  border: 1px dashed var(--border-strong);
  border-radius: var(--r-card);
  background: var(--surface-2);
  color: var(--fg-muted);
  cursor: pointer;
  transition: border-color var(--dur-1, 120ms) var(--ease-out, var(--ease));
}

.pf-drop:hover,
.pf-drop:focus-within {
  border-color: var(--brand);
}

.pf-file {
  font-size: var(--fs-small);
  color: var(--fg-2);
  min-width: 0;
}

.pf-preview {
  display: grid;
  gap: 0;
  margin: 0;
  padding: 0;
  list-style: none;
  border: 1px solid var(--border-soft);
  border-radius: var(--r-ctl);
  font-size: var(--fs-small);
}

.pf-preview li {
  display: flex;
  justify-content: space-between;
  gap: var(--sp-3);
  padding: var(--sp-2) var(--sp-3);
}

.pf-preview li + li {
  border-top: 1px solid var(--border-soft);
}

.pf-preview__name {
  font-family: var(--font-mono);
  color: var(--fg);
}

.pf-preview__count {
  color: var(--fg-muted);
  font-variant-numeric: tabular-nums;
}
</style>
