<script setup lang="ts">
/**
 * 分组管理 · 新建 / 编辑抽屉（FormDrawer lg）：分组名 · 模型穿梭框 · 备注。
 *
 * 模型目录（GET /api/models/configs）第一次打开时加载、组件内缓存；失败时抽屉里 ErrorBlock + 重试，
 * 不挡分组名 / 备注输入。穿梭框 value = 上游 id（contracts/groups.md §3.5），
 * 停用 / 目录里找不到的旧值原样保留并标注（PUT 是全量覆盖，不能悄悄删掉授权）。
 * 校验通过后 emit('submit', payload)，接口调用和 Toast 在页面里做；失败时抽屉保持打开。
 */
import { computed, ref, watch } from 'vue'
import { NInput, NTransfer } from 'naive-ui'
import { Badge, ErrorBlock, FormDrawer, FormField, HelpTip } from '@/components'
import { failureText } from '@/utils/error'
import { useI18n } from '@/i18n'
import {
  buildGroupEditorOptions,
  listModelConfigs,
  type GroupInput,
  type GroupItem,
  type GroupModelOption,
  type ModelConfigItem,
} from '@/api/groups'

const props = withDefaults(
  defineProps<{
    show: boolean
    /** 正在编辑的分组；null = 新建。 */
    editing: GroupItem | null
    /** 全部分组（前端先查重名，后端 409 兜底）。 */
    groups: GroupItem[]
    submitting?: boolean
  }>(),
  { submitting: false },
)

const emit = defineEmits<{
  (event: 'update:show', show: boolean): void
  (event: 'submit', payload: GroupInput): void
}>()

const { t } = useI18n()

const formName = ref('')
const formNote = ref('')
const errorName = ref('')
const errorModels = ref('')

const storedModels = ref<string[]>([])
const selection = ref<string[]>([])
const selectionEdited = ref(false)

const catalog = ref<ModelConfigItem[] | null>(null)
const catalogLoading = ref(false)
const catalogError = ref('')

const title = computed(() => (props.editing ? t('page.groups.drawer.edit') : t('page.groups.drawer.create')))
const submitText = computed(() => (props.editing ? t('page.groups.submit.save') : t('page.groups.submit.create')))

const plan = computed(() => (catalog.value ? buildGroupEditorOptions(catalog.value, selection.value, t) : null))
const editorOptions = computed<GroupModelOption[]>(() => plan.value?.options || [])

const optionIndex = computed(() => {
  const index = new Map<string, GroupModelOption>()
  for (const option of editorOptions.value) index.set(option.value, option)
  return index
})

const outOfCatalog = computed(() => editorOptions.value.filter((option) => !option.known || !option.enabled))

const outOfCatalogHint = computed(() =>
  outOfCatalog.value
    .map((option) =>
      option.known
        ? t('page.groups.option.disabled', { id: option.modelId })
        : t('page.groups.option.missing', { id: option.value }),
    )
    .join('\n'),
)

const transferEmpty = computed(() => Boolean(catalog.value) && !catalogLoading.value && editorOptions.value.length === 0)

function transferFilter(pattern: string, option: { label: string; value: string | number }): boolean {
  const keyword = pattern.trim().toLowerCase()
  if (!keyword) return true
  const meta = optionIndex.value.get(String(option.value))
  const haystack = [option.label, String(option.value), meta?.modelId, meta?.platform, ...(meta?.platforms || [])]
    .filter(Boolean)
    .join(' ')
    .toLowerCase()
  return haystack.includes(keyword)
}

async function ensureCatalog(force = false): Promise<void> {
  if (catalogLoading.value) return
  if (catalog.value && !force) return
  catalogLoading.value = true
  catalogError.value = ''
  try {
    const payload = await listModelConfigs()
    catalog.value = Array.isArray(payload?.items) ? payload.items : []
    if (!selectionEdited.value) {
      selection.value = buildGroupEditorOptions(catalog.value, storedModels.value, t).selected
    }
  } catch (error) {
    catalogError.value = failureText(t('page.groups.action.catalog'), error)
  } finally {
    catalogLoading.value = false
  }
}

watch(
  () => props.show,
  (open) => {
    if (!open) return
    const item = props.editing
    storedModels.value = item && Array.isArray(item.models) ? item.models.slice() : []
    selection.value = buildGroupEditorOptions(catalog.value, storedModels.value, t).selected
    selectionEdited.value = false
    formName.value = item ? item.name : ''
    formNote.value = item ? item.note || '' : ''
    errorName.value = ''
    errorModels.value = ''
    void ensureCatalog()
  },
  { immediate: true },
)

function onTransfer(value: Array<string | number>): void {
  selection.value = value.map(String)
  selectionEdited.value = true
  errorModels.value = ''
}

function submit(): void {
  if (props.submitting) return
  errorName.value = ''
  errorModels.value = ''
  const name = formName.value.trim()
  if (!name) {
    errorName.value = t('page.groups.form.requiredName')
    return
  }
  const editingId = props.editing ? props.editing.id : -1
  if (props.groups.some((group) => group.name === name && group.id !== editingId)) {
    errorName.value = t('page.groups.form.duplicate', { name })
    return
  }
  if (!selection.value.length) {
    errorModels.value = t('page.groups.form.requiredModels')
    return
  }
  emit('submit', { name, note: formNote.value.trim(), models: selection.value.slice() })
}
</script>

<template>
  <FormDrawer
    :show="show"
    :title="title"
    size="lg"
    :submit-text="submitText"
    :submit-loading="submitting"
    @update:show="(value: boolean) => emit('update:show', value)"
    @submit="submit"
  >
    <FormField :label="t('page.groups.form.name')" required :error="errorName" :hint="t('page.groups.form.nameHint')">
      <NInput
        v-model:value="formName"
        :placeholder="t('page.groups.form.namePlaceholder')"
        maxlength="64"
        show-count
        clearable
        @update:value="errorName = ''"
      />
    </FormField>

    <FormField :label="t('page.groups.form.models')" required :error="errorModels" :hint="t('page.groups.form.modelsHint')">
      <div class="ge-transfer">
        <ErrorBlock
          v-if="catalogError && !catalog"
          :title="catalogError"
          :retry-label="t('common.retry')"
          compact
          @retry="ensureCatalog(true)"
        />
        <div v-else-if="catalogLoading && !catalog" class="ge-skeleton" aria-busy="true">
          <span v-for="index in 4" :key="index" class="mh-skeleton ge-skeleton__row" />
        </div>
        <NTransfer
          v-else
          :value="selection"
          :options="editorOptions"
          source-filterable
          :source-title="t('page.groups.transfer.source')"
          :target-title="t('page.groups.transfer.target')"
          :source-filter-placeholder="t('page.groups.transfer.searchPlaceholder')"
          :filter="transferFilter"
          size="small"
          @update:value="onTransfer"
        />
        <p v-if="catalogLoading && catalog" class="ge-hint">{{ t('page.groups.catalogRefreshing') }}</p>
        <p v-if="transferEmpty" class="ge-hint">{{ t('page.groups.catalogEmpty') }}</p>
        <p v-if="outOfCatalog.length" class="ge-hint">
          <Badge tone="warn">{{ t('page.groups.outOfCatalog', { count: outOfCatalog.length }) }}</Badge>
          <HelpTip :content="outOfCatalogHint" :label="t('page.groups.outOfCatalogTip')" />
          {{ t('page.groups.outOfCatalogKeep') }}
        </p>
      </div>
    </FormField>

    <FormField :label="t('page.groups.form.note')" :hint="t('page.groups.form.noteHint')">
      <NInput v-model:value="formNote" :placeholder="t('page.groups.form.notePlaceholder')" maxlength="200" />
    </FormField>
  </FormDrawer>
</template>

<style scoped>
.ge-transfer {
  display: grid;
  gap: var(--sp-2);
  min-width: 0;
}

.ge-transfer :deep(.n-transfer) {
  height: 320px;
}

.ge-skeleton {
  display: grid;
  gap: var(--sp-2);
}

.ge-skeleton__row {
  height: 28px;
}

.ge-hint {
  margin: 0;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--sp-1);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}
</style>
