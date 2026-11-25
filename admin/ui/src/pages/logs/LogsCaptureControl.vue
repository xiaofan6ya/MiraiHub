<script setup lang="ts">
/**
 * 「请求详情记录」开关：从独占一张卡片收进页头（第 1 轮审查 P2：日志表被挤到首屏以下）。
 *
 * 页头只露出开关 + 名称 + 一个状态胶囊；点胶囊展开浮层看缓存占用、说明、刷新与清空。
 * 接口：GET/PUT /api/logs/capture/settings、DELETE /api/logs/capture/cache（request_payload.py）。
 */
import { computed, onMounted, ref } from 'vue'
import { NPopover, NSwitch } from 'naive-ui'
import { RefreshCw, Trash2 } from 'lucide-vue-next'
import { Btn } from '@/components'
import { useConfirm, useToast } from '@/composables'
import { fmtInt } from '@/utils/format'
import { errorMessage, failureText } from '@/utils/error'
import { useI18n } from '@/i18n'
import {
  clearCaptureCache,
  getCaptureSettings,
  setCaptureEnabled,
  type CaptureSettings,
} from '@/api/logs'

const { t } = useI18n()
const toast = useToast()
const confirm = useConfirm()

const capture = ref<CaptureSettings | null>(null)
const loading = ref(false)
const busy = ref(false)
const error = ref('')

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    capture.value = await getCaptureSettings()
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    loading.value = false
  }
}

async function toggle(enabled: boolean): Promise<void> {
  if (busy.value) return
  busy.value = true
  try {
    capture.value = await setCaptureEnabled(enabled)
    toast.success(enabled ? t('page.logs.toast.captureOn') : t('page.logs.toast.captureOff'))
  } catch (err) {
    toast.error(
      failureText(enabled ? t('page.logs.action.enableCapture') : t('page.logs.action.disableCapture'), err),
    )
  } finally {
    busy.value = false
    void load()
  }
}

async function clear(): Promise<void> {
  const ok = await confirm({
    title: t('page.logs.confirmClear.title'),
    message: t('page.logs.confirmClear.body'),
    okText: t('page.logs.confirmClear.ok'),
    danger: true,
  })
  if (!ok) return
  busy.value = true
  try {
    capture.value = await clearCaptureCache()
    toast.success(t('page.logs.toast.cacheCleared'))
  } catch (err) {
    toast.error(failureText(t('page.logs.action.clearCache'), err))
  } finally {
    busy.value = false
  }
}

/** 胶囊上的短状态：已开启 · 3 条 / 已关闭 / 读取失败。 */
const pillText = computed(() => {
  if (error.value) return t('page.logs.capture.pillError')
  const value = capture.value
  if (!value) return t('page.logs.capture.pillLoading')
  if (!value.enabled) return t('page.logs.capture.off')
  return t('page.logs.capture.pillOn', { count: fmtInt(value.cached_requests) })
})

/** 浮层里的完整状态行（替代原来一整行拼接的灰字）。 */
const statusLine = computed(() => {
  const value = capture.value
  if (!value) return ''
  return t('page.logs.capture.statusLine', {
    count: fmtInt(value.cached_requests),
    max: fmtInt(value.max_entries),
    mib: (Number(value.cache_bytes || 0) / 1024 / 1024).toFixed(1),
    record: Math.round(Number(value.record_max_bytes || 0) / 1024 / 1024),
    minutes: Math.round(Number(value.ttl_seconds || 0) / 60),
  })
})

onMounted(() => {
  void load()
})

defineExpose({ reload: load })
</script>

<template>
  <div class="logs-capture">
    <NSwitch
      size="small"
      :value="capture?.enabled === true"
      :loading="loading || busy"
      :disabled="loading || busy || Boolean(error && !capture)"
      :aria-label="t('page.logs.capture.switchAria')"
      @update:value="toggle"
    />
    <span class="logs-capture__label">{{ t('page.logs.capture.title') }}</span>
    <NPopover trigger="click" placement="bottom-end" :show-arrow="false" :width="320">
      <template #trigger>
        <button
          type="button"
          class="logs-capture__pill"
          :class="{ 'is-on': capture?.enabled, 'is-error': Boolean(error) }"
          :aria-label="t('page.logs.capture.helpLabel')"
        >
          {{ pillText }}
        </button>
      </template>
      <div class="logs-capture__pop">
        <p v-if="error" class="logs-capture__error">{{ t('page.logs.capture.statusError', { error }) }}</p>
        <p v-else-if="statusLine" class="logs-capture__status tnum">{{ statusLine }}</p>
        <p class="logs-capture__help">{{ t('page.logs.captureHelp') }}</p>
        <div class="logs-capture__actions">
          <Btn variant="quiet" size="sm" :icon="RefreshCw" :loading="loading" @click="load">
            {{ t('page.logs.capture.refreshStatus') }}
          </Btn>
          <Btn variant="danger" size="sm" :icon="Trash2" :disabled="busy || !capture" @click="clear">
            {{ t('page.logs.capture.clear') }}
          </Btn>
        </div>
      </div>
    </NPopover>
  </div>
</template>

<style scoped>
.logs-capture {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-2);
  height: 34px;
  padding: 0 var(--sp-2) 0 var(--sp-3);
  border: 1px solid var(--border);
  border-radius: var(--r-ctl);
  background: var(--surface);
}

.logs-capture__label {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-2);
  white-space: nowrap;
}

.logs-capture__pill {
  display: inline-flex;
  align-items: center;
  height: 22px;
  padding: 0 var(--sp-2);
  border: 0;
  border-radius: var(--r-chip);
  background: var(--neutral-soft);
  color: var(--fg-muted);
  font-size: var(--fs-tiny);
  line-height: var(--lh-tiny);
  white-space: nowrap;
  cursor: pointer;
}

.logs-capture__pill.is-on {
  background: var(--brand-soft);
  color: var(--brand);
}

.logs-capture__pill.is-error {
  background: var(--warn-soft);
  color: var(--warn);
}

.logs-capture__pill:focus-visible {
  outline: none;
  box-shadow: 0 0 0 3px var(--ring, var(--brand-soft));
}

.logs-capture__pop {
  display: grid;
  gap: var(--sp-2);
}

.logs-capture__pop p {
  margin: 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
}

.logs-capture__status {
  color: var(--fg);
}

.logs-capture__error {
  color: var(--danger);
}

.logs-capture__help {
  color: var(--fg-muted);
  text-wrap: pretty;
}

.logs-capture__actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--sp-2);
  padding-top: var(--sp-1);
  border-top: 1px solid var(--border-soft);
}
</style>
