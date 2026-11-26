<script setup lang="ts">
/**
 * 账号池 · 编辑抽屉（名称 / 并发上限；后端 PATCH 只接受这两项与 status）。
 */
import { reactive, ref, watch } from 'vue'
import { NInput, NInputNumber } from 'naive-ui'
import { FormDrawer, FormField } from '@/components'
import { useToast } from '@/composables'
import { failureText } from '@/utils/error'
import { useI18n } from '@/i18n'
import { patchAccount, type AccountRow } from '@/api/accounts'

const props = defineProps<{
  show: boolean
  row: AccountRow | null
}>()

const emit = defineEmits<{
  (event: 'update:show', value: boolean): void
  (event: 'saved'): void
}>()

const { t } = useI18n()
const toast = useToast()

const form = reactive({ name: '', maxInFlight: null as number | null })
const error = ref('')
const saving = ref(false)

watch(
  () => [props.show, props.row?.id] as const,
  ([show]) => {
    if (!show || !props.row) return
    form.name = props.row.name
    form.maxInFlight = props.row.raw.max_in_flight ?? null
    error.value = ''
  },
  { immediate: true },
)

async function save(): Promise<void> {
  const target = props.row
  if (!target || saving.value) return
  const name = form.name.trim()
  if (!name) {
    error.value = t('page.accounts.editNameRequired')
    return
  }
  error.value = ''
  saving.value = true
  try {
    await patchAccount(target.id, {
      name,
      max_in_flight: form.maxInFlight === null ? null : Number(form.maxInFlight),
    })
    toast.success(t('page.accounts.saved', { name }))
    emit('update:show', false)
    emit('saved')
  } catch (err) {
    toast.error(failureText(t('page.accounts.saveFailed'), err))
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <FormDrawer
    :show="show"
    :title="t('page.accounts.editTitle')"
    :submit-loading="saving"
    @update:show="(value: boolean) => emit('update:show', value)"
    @submit="save"
  >
    <FormField
      :label="t('page.accounts.editName')"
      required
      :error="error"
      :hint="t('page.accounts.editNameHint')"
    >
      <n-input
        v-model:value="form.name"
        :placeholder="t('page.accounts.editNamePlaceholder')"
        maxlength="64"
        clearable
      />
    </FormField>
    <FormField :label="t('page.accounts.editMax')" :hint="t('page.accounts.editMaxHint')">
      <n-input-number
        v-model:value="form.maxInFlight"
        :min="0"
        :placeholder="t('page.accounts.editMaxPlaceholder')"
        clearable
      />
    </FormField>
    <p class="acc-edit__note">{{ t('page.accounts.editNote') }}</p>
  </FormDrawer>
</template>

<style scoped>
.acc-edit__note {
  margin: var(--sp-2) 0 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}
</style>
