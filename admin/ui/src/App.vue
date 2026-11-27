<script setup lang="ts">
import { computed } from 'vue'
import IslandProvider from '@/components/feedback/IslandProvider.vue'
import {
  NConfigProvider,
  NDialogProvider,
  NLoadingBarProvider,
  NMessageProvider,
  dateEnUS,
  dateZhCN,
  enUS,
  zhCN,
} from 'naive-ui'
import { useNaiveTheme } from '@/theme/naiveTheme'
import { useI18n } from '@/i18n'

const { theme, themeOverrides } = useNaiveTheme()
const { locale } = useI18n()

/** Naive 自带的分页 / 日期面板文案跟随界面语言，避免中英混排。 */
const naiveLocale = computed(() => (locale.value === 'en-US' ? enUS : zhCN))
const naiveDateLocale = computed(() => (locale.value === 'en-US' ? dateEnUS : dateZhCN))
</script>

<template>
  <n-config-provider
    :locale="naiveLocale"
    :date-locale="naiveDateLocale"
    :theme="theme"
    :theme-overrides="themeOverrides"
  >
    <n-loading-bar-provider>
      <n-message-provider :max="3" placement="top-right">
        <n-dialog-provider>
          <IslandProvider><router-view /></IslandProvider>
        </n-dialog-provider>
      </n-message-provider>
    </n-loading-bar-provider>
  </n-config-provider>
</template>
