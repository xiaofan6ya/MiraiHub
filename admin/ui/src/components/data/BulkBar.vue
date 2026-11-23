<script setup lang="ts">
/** 多选后出现在表格上方：已选 N 项 + 批量操作 + 取消选择（高 44px，--brand-soft 底）。 */
import Btn from '../base/Btn.vue'
import { computed } from 'vue'
import { useI18n } from '@/i18n'

const props = withDefaults(defineProps<{ count: number; unit?: string }>(), { unit: undefined })
const { t } = useI18n()
const unitText = computed(() => props.unit ?? t('common.unitItem'))

const emit = defineEmits<{ (event: 'cancel'): void }>()
</script>

<template>
  <div class="mh-bulk" role="region" :aria-label="t('common.bulkRegion')">
    <span class="mh-bulk__count">{{ t('common.selected', { count, unit: unitText }) }}</span>
    <div class="mh-bulk__actions">
      <slot />
    </div>
    <Btn variant="quiet" size="sm" @click="emit('cancel')">{{ t('common.clearSelection') }}</Btn>
  </div>
</template>

<style scoped>
.mh-bulk {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  height: 44px;
  padding: 0 var(--sp-3);
  background: var(--brand-soft);
  border: 1px solid var(--brand-soft);
  border-radius: var(--r-ctl);
  flex-wrap: wrap;
}

.mh-bulk__count {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--brand);
  font-weight: 500;
  flex: none;
}

.mh-bulk__actions {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
  flex: 1;
  min-width: 0;
}
</style>
