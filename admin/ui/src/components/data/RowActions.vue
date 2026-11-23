<script setup lang="ts">
/**
 * 表格行操作：前 2 个用 quiet 文字按钮直接显示，其余收进「⋯」下拉；
 * 危险操作在菜单最下方、朱色、上方加分隔线（04-components §2 DataTable）。
 */
import { computed } from 'vue'
import { NPopover } from 'naive-ui'
import { MoreHorizontal } from 'lucide-vue-next'
import Btn from '../base/Btn.vue'
import IconBtn from '../base/IconBtn.vue'
import { useI18n } from '@/i18n'
import type { RowAction } from './rowActions'

const props = withDefaults(defineProps<{ actions: RowAction[]; max?: number }>(), { max: 2 })

const emit = defineEmits<{ (event: 'select', key: string): void }>()
const { t } = useI18n()

const visible = computed(() => props.actions.filter((action) => !action.hidden))
const inline = computed(() => visible.value.slice(0, Math.max(0, props.max)))
const overflow = computed(() => visible.value.slice(Math.max(0, props.max)))
const normal = computed(() => overflow.value.filter((action) => !action.danger))
const dangerous = computed(() => overflow.value.filter((action) => action.danger))
</script>

<template>
  <div class="mh-row-actions">
    <Btn
      v-for="action in inline"
      :key="action.key"
      variant="quiet"
      size="sm"
      :disabled="action.disabled"
      @click="emit('select', action.key)"
    >
      {{ action.label }}
    </Btn>
    <n-popover v-if="overflow.length" trigger="click" placement="bottom-end" :show-arrow="false">
      <template #trigger>
        <IconBtn :label="t('common.moreActions')" :icon="MoreHorizontal" />
      </template>
      <div class="mh-row-actions__menu">
        <Btn
          v-for="action in normal"
          :key="action.key"
          variant="quiet"
          size="sm"
          block
          :disabled="action.disabled"
          class="mh-row-actions__item"
          @click="emit('select', action.key)"
        >
          {{ action.label }}
        </Btn>
        <i v-if="normal.length && dangerous.length" class="mh-row-actions__divider" />
        <Btn
          v-for="action in dangerous"
          :key="action.key"
          variant="danger"
          size="sm"
          block
          :disabled="action.disabled"
          class="mh-row-actions__item"
          @click="emit('select', action.key)"
        >
          {{ action.label }}
        </Btn>
      </div>
    </n-popover>
  </div>
</template>

<style scoped>
.mh-row-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--sp-1);
}

.mh-row-actions__menu {
  display: grid;
  gap: var(--sp-1);
  min-width: 140px;
}

.mh-row-actions__item {
  justify-content: flex-start;
}

.mh-row-actions__divider {
  display: block;
  height: 1px;
  background: var(--border-soft);
  margin: var(--sp-1) 0;
}
</style>
