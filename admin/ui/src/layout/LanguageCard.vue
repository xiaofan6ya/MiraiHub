<script setup lang="ts">
/**
 * 设置页「界面语言」区块：本机偏好，只写 localStorage（mh.locale），不提交后端。
 * 抽成独立组件便于单测：语言切换不需要拉起设置页的四个接口。
 */
import { computed } from 'vue'
import SettingsCard from './SettingsCard.vue'
import FormField from '@/components/overlay/FormField.vue'
import Segmented from '@/components/base/Segmented.vue'
import { useI18n } from '@/i18n'

const { t, locale, setLocale } = useI18n()

const options = computed(() => [
  { label: t('page.settings.language.zh'), value: 'zh-CN' },
  { label: t('page.settings.language.en'), value: 'en-US' },
])

function onLocaleChange(value: string | number): void {
  setLocale(String(value) === 'en-US' ? 'en-US' : 'zh-CN')
}
</script>

<template>
  <SettingsCard
    id="settings-language"
    :title="t('page.settings.language.title')"
    :description="t('page.settings.language.description')"
    hide-save
    hide-reset
  >
    <FormField :label="t('page.settings.language.label')" :hint="t('page.settings.language.hint')">
      <Segmented
        :options="options"
        :model-value="locale"
        :aria-label="t('page.settings.language.label')"
        @update:model-value="onLocaleChange"
      />
    </FormField>
  </SettingsCard>
</template>
