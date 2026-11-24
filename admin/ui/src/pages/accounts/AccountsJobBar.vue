<script setup lang="ts">
/**
 * 账号池 · 后台任务内联条（表格卡片顶部；只在有任务时出现，完成后可隐藏）。
 */
import { Btn, Meter } from '@/components'
import { useI18n } from '@/i18n'

withDefaults(
  defineProps<{
    title: string
    percent: number
    done: number
    total: number
    running: boolean
    failed?: boolean
  }>(),
  { failed: false },
)

const emit = defineEmits<{
  (event: 'view'): void
  (event: 'hide'): void
}>()

const { t } = useI18n()
</script>

<template>
  <div class="acc-jobbar" :class="{ 'is-done': !running }">
    <span class="acc-jobbar__name">{{ title }}</span>
    <Meter :value="percent" :height="4" :tone="running ? 'brand' : failed ? 'danger' : 'ok'" />
    <span class="acc-jobbar__num tnum">{{ done }} / {{ total }}</span>
    <Btn variant="quiet" size="sm" @click="emit('view')">{{ t('job.view') }}</Btn>
    <Btn v-if="!running" variant="quiet" size="sm" @click="emit('hide')">
      {{ t('page.accounts.hide') }}
    </Btn>
  </div>
</template>

<style scoped>
.acc-jobbar {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  margin: 0 0 var(--sp-3);
  padding: var(--sp-2) var(--sp-3);
  border-radius: var(--r-ctl);
  background: var(--brand-soft);
  min-width: 0;
}

.acc-jobbar.is-done {
  background: var(--surface-2);
}

.acc-jobbar__name {
  font-size: var(--fs-small);
  font-weight: 500;
  color: var(--fg);
  flex: none;
}

.acc-jobbar__num {
  font-size: var(--fs-small);
  color: var(--fg-muted);
  flex: none;
}

.acc-jobbar :deep(.mh-meter) {
  flex: 1;
  min-width: 80px;
}

@media (max-width: 767px) {
  .acc-jobbar {
    flex-wrap: wrap;
  }
}
</style>
