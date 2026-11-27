<script setup lang="ts">
/**
 * 「Android 本机」设置卡片 —— 只在 APK 的 WebView 里出现（`window.WBAndroid` 存在时）。
 *
 * 为什么独立成一个组件：这块能力原来只存在于旧控制台的移动端设置面板
 * （`android/web/settings.html` + `mobile_settings.js`），后台前端合并后旧面板不再被
 * WebView 加载（`MainActivity.java:145` 加载的是一个 `/admin`），所以要在新版里补回来。
 * 做成独立卡片而不是塞进 SettingsPage，是因为它只在 APK 里有意义，浏览器里整块不渲染。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { NPopconfirm } from 'naive-ui'
import { BatteryCharging, Bell, Power, RefreshCw } from 'lucide-vue-next'
import SettingsCard from '@/layout/SettingsCard.vue'
import Btn from '@/components/base/Btn.vue'
import KV from '@/components/base/KV.vue'
import Section from '@/components/base/Section.vue'
import { androidStatus, openAndroidSettings, restartAndroidService, type AndroidStatus } from '@/platform/android'
import { fmtTime } from '@/utils/format'
import { useI18n } from '@/i18n'

const { t } = useI18n()
const status = ref<AndroidStatus>({})
const refreshing = ref(false)

function refresh(): void {
  status.value = androidStatus()
}

function refreshSoon(): void {
  refreshing.value = true
  refresh()
  window.setTimeout(() => { refreshing.value = false }, 400)
}

const serviceText = computed(() => {
  if (status.value.error) return status.value.error
  if (status.value.starting) return t('page.settings.android.stateStarting')
  if (status.value.registered) return t('page.settings.android.stateRunning')
  return t('page.settings.android.stateStopped')
})

const lines = computed(() => [
  { label: t('page.settings.android.service'), value: serviceText.value },
  {
    label: t('page.settings.android.origin'),
    value: status.value.origin || '',
    mono: true,
    copy: Boolean(status.value.origin),
  },
  {
    label: t('page.settings.android.healthy'),
    value: status.value.last_healthy ? fmtTime(Number(status.value.last_healthy)) : '',
  },
  {
    label: t('page.settings.android.notifications'),
    value: status.value.notifications ? t('page.settings.android.allowed') : t('page.settings.android.blocked'),
  },
  {
    label: t('page.settings.android.battery'),
    value: status.value.battery_unrestricted
      ? t('page.settings.android.unrestricted')
      : t('page.settings.android.restricted'),
  },
  {
    label: t('page.settings.android.background'),
    value: status.value.background_restricted ? t('page.settings.android.blocked') : t('page.settings.android.allowed'),
  },
  {
    label: t('page.settings.android.keeper'),
    value: status.value.keeper_enabled
      ? t('page.settings.android.keeperOn', { count: Number(status.value.interrupts) || 0 })
      : t('page.settings.android.keeperOff'),
  },
])

let timer: number | undefined
onMounted(() => {
  refresh()
  // 服务状态由原生侧异步刷新，10 秒对齐一次就够；不轮询更快会白耗电。
  timer = window.setInterval(refresh, 10_000)
})
onBeforeUnmount(() => {
  if (timer !== undefined) window.clearInterval(timer)
})
</script>

<template>
  <SettingsCard
    id="settings-android"
    :title="t('page.settings.android.title')"
    :description="t('page.settings.android.description')"
    plain
  >
    <Section :title="t('page.settings.android.statusTitle')" first>
      <div class="android-card__bar">
        <Btn variant="default" size="sm" :icon="RefreshCw" :loading="refreshing" @click="refreshSoon">
          {{ t('page.settings.android.refresh') }}
        </Btn>
      </div>
      <KV :items="lines" />
    </Section>

    <Section :title="t('page.settings.android.actions')">
      <div class="android-card__bar">
        <n-popconfirm @positive-click="restartAndroidService">
          <template #trigger>
            <Btn variant="default" :icon="Power">{{ t('page.settings.android.restart') }}</Btn>
          </template>
          {{ t('page.settings.android.restartConfirm') }}
        </n-popconfirm>
        <Btn variant="default" :icon="Bell" @click="openAndroidSettings('notifications')">
          {{ t('page.settings.android.openNotifications') }}
        </Btn>
        <Btn variant="default" :icon="BatteryCharging" @click="openAndroidSettings('battery')">
          {{ t('page.settings.android.openBattery') }}
        </Btn>
        <Btn variant="default" :icon="RefreshCw" @click="openAndroidSettings('startup')">
          {{ t('page.settings.android.openStartup') }}
        </Btn>
      </div>
      <p class="android-card__note">{{ t('page.settings.android.hint') }}</p>
    </Section>
  </SettingsCard>
</template>

<style scoped>
.android-card__bar {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-2);
  margin-bottom: var(--sp-3);
}

.android-card__note {
  margin: var(--sp-3) 0 0;
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--fg-muted);
}
</style>
