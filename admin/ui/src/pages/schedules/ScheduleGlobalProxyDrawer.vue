<script setup lang="ts">
/**
 * 定时任务 · 全局出口与并发抽屉（从 SchedulesPage.vue 拆出，10-visual-refresh §5）。
 * 打开时按当前全局配置回填；保存成功后 emit('saved', 新配置)，由页面更新并重拉列表。
 */
import { computed, ref, watch } from 'vue'
import { NInputNumber, NSelect } from 'naive-ui'
import { FormDrawer, FormField } from '@/components'
import { useToast } from '@/composables'
import { failureText } from '@/utils/error'
import { useI18n } from '@/i18n'
import {
  DEFAULT_PROXY_FORM,
  buildProxyPoolOptions,
  putGlobalScheduleProxy,
  type ProxyListPayload,
  type ScheduleGlobalProxy,
} from '@/api/schedules'

const props = defineProps<{
  show: boolean
  globalProxy: ScheduleGlobalProxy | null
  proxyPayload: ProxyListPayload | null
  help: string
}>()

const emit = defineEmits<{
  (event: 'update:show', value: boolean): void
  (event: 'saved', value: ScheduleGlobalProxy): void
}>()

const { t } = useI18n()
const toast = useToast()

const globalForm = ref<ScheduleGlobalProxy>({ ...DEFAULT_PROXY_FORM, proxy_pool: '', concurrency: 0, time_budget_minutes: 0, stale_minutes: 0 })
const globalSaving = ref(false)

const globalProxyOptions = computed(() =>
  buildProxyPoolOptions(props.proxyPayload, props.globalProxy, globalForm.value.proxy_pool),
)

watch(
  () => props.show,
  (open) => {
    if (!open) return
    const current = props.globalProxy
    globalForm.value = {
      proxy_pool: current?.proxy_pool || '',
      concurrency: Number(current?.concurrency || 0),
      time_budget_minutes: Number(current?.time_budget_minutes || 0),
      stale_minutes: Number(current?.stale_minutes || 0),
    }
  },
  { immediate: true },
)

async function saveGlobalProxy(): Promise<void> {
  if (globalSaving.value) return
  globalSaving.value = true
  try {
    const payload = await putGlobalScheduleProxy(globalForm.value)
    const next: ScheduleGlobalProxy = {
      proxy_pool: payload?.proxy_pool ?? globalForm.value.proxy_pool,
      concurrency: Number(payload?.concurrency ?? globalForm.value.concurrency),
      time_budget_minutes: Number(payload?.time_budget_minutes ?? globalForm.value.time_budget_minutes),
      stale_minutes: Number(payload?.stale_minutes ?? globalForm.value.stale_minutes),
    }
    toast.success(t('page.schedules.toast.globalSaved'))
    emit('update:show', false)
    emit('saved', next)
  } catch (error) {
    toast.error(failureText(t('page.schedules.action.saveGlobal'), error))
  } finally {
    globalSaving.value = false
  }
}
</script>

<template>
  <FormDrawer
    :show="show"
    @update:show="(value: boolean) => emit('update:show', value)"
    :title="t('page.schedules.global.title')"
    :submit-text="t('page.schedules.global.submit')"
    :submit-loading="globalSaving"
    @submit="saveGlobalProxy"
  >
    <p class="schedules-hint">{{ help }}</p>

    <FormField
      :label="t('page.schedules.global.proxy')"
      :hint="t('page.schedules.global.proxyHint')"
    >
      <n-select
        :value="globalForm.proxy_pool"
        :options="globalProxyOptions"
        :placeholder="t('page.schedules.global.proxyPlaceholder')"
        @update:value="(value: string) => (globalForm.proxy_pool = value)"
      />
    </FormField>

    <FormField
      :label="t('page.schedules.global.concurrency')"
      :hint="t('page.schedules.global.concurrencyHint')"
    >
      <n-input-number v-model:value="globalForm.concurrency" :min="0" :max="64" :step="1" />
    </FormField>

    <FormField
      :label="t('page.schedules.global.budget')"
      :hint="t('page.schedules.global.budgetHint')"
    >
      <n-input-number v-model:value="globalForm.time_budget_minutes" :min="0" :max="720" :step="1" />
    </FormField>

    <FormField
      :label="t('page.schedules.global.stale')"
      :hint="t('page.schedules.global.staleHint')"
    >
      <n-input-number v-model:value="globalForm.stale_minutes" :min="0" :max="10080" :step="1" />
    </FormField>
  </FormDrawer>
</template>

<style scoped>
.schedules-hint {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--sp-1);
  margin: 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}
</style>
