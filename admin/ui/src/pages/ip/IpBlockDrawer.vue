<script setup lang="ts">
/**
 * IP 管理 · 封禁表单抽屉（FormDrawer 480px）。
 *
 * 只做输入与校验，提交时 emit('submit', { ip, note, expiresAt })；二次确认和接口调用在页面里做，
 * 失败时抽屉保持打开、内容不丢（contracts/ip.md §3.5）。
 * 后端不支持 expires_at 时（老后端：BlockedIPIn 只收 ip / note），不显示「时长」，只说明「永久封禁」——
 * 否则界面写着「1 小时」，后端却静默丢弃字段、永久封禁。r3 后端支持后：预设时长提交 duration_seconds，
 * 自定义提交 expires_at（≤366 天）。
 */
import { computed, ref, watch } from 'vue'
import { NDatePicker, NInput, NSelect } from 'naive-ui'
import { FormDrawer, FormField } from '@/components'
import { useI18n } from '@/i18n'
import {
  BLOCK_DURATION_OPTIONS,
  expiryFromDuration,
  parseBlockTarget,
  type BlockDuration,
} from '@/api/ip'
import type { IpBlockSubmit } from './types'

const props = withDefaults(
  defineProps<{
    show: boolean
    /** 打开时预填的 IP（行内「封禁」）。 */
    initialIp?: string
    expirySupported: boolean
    submitting?: boolean
  }>(),
  { initialIp: '', submitting: false },
)

const emit = defineEmits<{
  (event: 'update:show', show: boolean): void
  (event: 'submit', payload: IpBlockSubmit): void
}>()

const { t } = useI18n()

const formIp = ref('')
const formNote = ref('')
const formDuration = ref<BlockDuration>('permanent')
const formCustomMs = ref<number | null>(null)
const errorIp = ref('')
const errorDuration = ref('')

// label 是 getter，放进 computed 才会随语言切换刷新
const durationOptions = computed(() =>
  BLOCK_DURATION_OPTIONS.map((option) => ({ label: option.label, value: option.value as string })),
)

watch(
  () => props.show,
  (open) => {
    if (!open) return
    formIp.value = props.initialIp || ''
    formNote.value = ''
    formDuration.value = 'permanent'
    formCustomMs.value = null
    errorIp.value = ''
    errorDuration.value = ''
  },
  { immediate: true },
)

const showCustom = computed(() => props.expirySupported && formDuration.value === 'custom')

function onDuration(value: string | number | null): void {
  const next = String(value ?? 'permanent') as BlockDuration
  formDuration.value = next
  errorDuration.value = ''
  if (next !== 'custom') formCustomMs.value = null
}

function submit(): void {
  if (props.submitting) return
  errorIp.value = ''
  errorDuration.value = ''
  const target = parseBlockTarget(formIp.value)
  if (target.kind !== 'ip') {
    errorIp.value = target.message
    return
  }
  let expiresAt: string | null = null
  let durationSeconds: number | null = null
  if (props.expirySupported) {
    const plan = expiryFromDuration(formDuration.value, Date.now(), formCustomMs.value)
    if (plan.error) {
      errorDuration.value = plan.error
      return
    }
    expiresAt = plan.expiresAt
    durationSeconds = plan.durationSeconds
  }
  emit('submit', { ip: target.value, note: formNote.value.trim(), expiresAt, durationSeconds })
}
</script>

<template>
  <FormDrawer
    :show="show"
    :title="t('page.ip.drawer.title')"
    :submit-text="t('page.ip.drawer.title')"
    :submit-loading="submitting"
    @update:show="(value: boolean) => emit('update:show', value)"
    @submit="submit"
  >
    <FormField :label="t('page.ip.drawer.ip')" required :error="errorIp" :hint="t('page.ip.drawer.ipHint')">
      <NInput
        v-model:value="formIp"
        :placeholder="t('page.ip.drawer.ipPlaceholder')"
        maxlength="64"
        clearable
        @update:value="errorIp = ''"
      />
    </FormField>

    <FormField :label="t('page.ip.drawer.note')" :hint="t('page.ip.drawer.noteHint')">
      <NInput v-model:value="formNote" :placeholder="t('page.ip.drawer.notePlaceholder')" maxlength="255" show-count />
    </FormField>

    <template v-if="expirySupported">
      <FormField :label="t('page.ip.drawer.duration')" :error="showCustom ? '' : errorDuration" :hint="t('page.ip.drawer.durationHint')">
        <NSelect :value="formDuration" :options="durationOptions" @update:value="onDuration" />
      </FormField>
      <FormField v-if="showCustom" :label="t('page.ip.drawer.expiresAt')" required :error="errorDuration">
        <NDatePicker
          v-model:value="formCustomMs"
          type="datetime"
          class="ibd-datetime"
          clearable
          @update:value="errorDuration = ''"
        />
      </FormField>
    </template>
    <FormField v-else :label="t('page.ip.drawer.duration')">
      <p class="ibd-note">
        {{ t('page.ip.blocked.permanent') }} · {{ t('page.ip.drawer.permanentOnly') }}
      </p>
    </FormField>
  </FormDrawer>
</template>

<style scoped>
.ibd-datetime {
  width: 100%;
}

.ibd-note {
  margin: 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}
</style>
