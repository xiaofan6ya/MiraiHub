<script setup lang="ts">
/** 只读详情抽屉（日志 / 账号）：640px，标题 + 徽章，内容用 Section 分组。 */
import { NDrawer, NDrawerContent } from 'naive-ui'
import Badge from '../base/Badge.vue'
import type { BadgeTone } from '../base/badge'

withDefaults(
  defineProps<{
    show: boolean
    title: string
    badge?: { tone: BadgeTone; label: string } | null
    subtitle?: string
    width?: number
  }>(),
  { badge: null, subtitle: undefined, width: 640 },
)

const emit = defineEmits<{ (event: 'update:show', show: boolean): void }>()
</script>

<template>
  <n-drawer
    :show="show"
    :width="width"
    placement="right"
    @update:show="(value: boolean) => emit('update:show', value)"
  >
    <n-drawer-content closable :native-scrollbar="false">
      <template #header>
        <div class="mh-detail__head">
          <span class="mh-detail__title">{{ title }}</span>
          <Badge v-if="badge" :tone="badge.tone" dot>{{ badge.label }}</Badge>
          <span v-if="subtitle" class="mh-detail__subtitle">{{ subtitle }}</span>
        </div>
      </template>
      <div class="mh-detail__body">
        <slot />
      </div>
      <template v-if="$slots.footer" #footer>
        <div class="mh-detail__footer"><slot name="footer" /></div>
      </template>
    </n-drawer-content>
  </n-drawer>
</template>

<style scoped>
.mh-detail__head {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  min-width: 0;
  flex-wrap: wrap;
}

.mh-detail__title {
  font-size: var(--fs-h2);
  line-height: var(--lh-h2);
  font-weight: 600;
  color: var(--fg);
}

.mh-detail__subtitle {
  font-size: var(--fs-small);
  color: var(--fg-muted);
}

.mh-detail__body {
  display: grid;
  gap: var(--sp-3);
  min-width: 0;
}

.mh-detail__footer {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--sp-2);
  width: 100%;
}
</style>
