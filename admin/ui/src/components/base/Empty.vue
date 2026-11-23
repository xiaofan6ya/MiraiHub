<script setup lang="ts">
/** 空状态：32px 灰图标 + 一句说明 + 0–2 个操作。 */
import { computed } from 'vue'
import { useI18n } from '@/i18n'
import { Inbox } from 'lucide-vue-next'
import { NEmpty } from 'naive-ui'
import type { Component } from 'vue'

const props = withDefaults(
  defineProps<{
    /** 说明：说清楚下一步（01-direction §8）。 */
    description?: string
    icon?: Component
    /** 紧凑模式：表格内嵌。 */
    compact?: boolean
  }>(),
  { description: undefined, icon: undefined, compact: false },
)

const { t } = useI18n()
const iconComponent = computed(() => props.icon ?? Inbox)
const desc = computed(() => props.description ?? t('common.empty'))
</script>

<template>
  <n-empty class="mh-empty" :class="{ 'mh-empty--compact': compact }">
    <template #icon>
      <component :is="iconComponent" :size="32" />
    </template>
    <span class="mh-empty__desc"><slot name="description">{{ desc }}</slot></span>
    <template v-if="$slots.default" #extra>
      <div class="mh-empty__actions"><slot /></div>
    </template>
  </n-empty>
</template>

<style scoped>
.mh-empty {
  padding: var(--sp-8) var(--sp-4);
}

.mh-empty--compact {
  padding: var(--sp-6) var(--sp-4);
}

.mh-empty :deep(.n-empty__icon) {
  color: var(--fg-faint);
}

.mh-empty__desc {
  display: block;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.mh-empty__actions {
  display: flex;
  gap: var(--sp-2);
  justify-content: center;
  flex-wrap: wrap;
  margin-top: var(--sp-2);
}
</style>
