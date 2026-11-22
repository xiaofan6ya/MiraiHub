<script setup lang="ts">
/** 只读键值列表：左标签 120px --fg-muted，右值。空值显示 —（不显示空串）。 */
import { isBlank } from '../_internal/text'
import Copyable from './Copyable.vue'
import type { KvItem } from './kv'

withDefaults(defineProps<{ items: KvItem[]; labelWidth?: string }>(), {
  labelWidth: '120px',
})
</script>

<template>
  <dl class="mh-kv">
    <div
      v-for="item in items"
      :key="item.label"
      class="mh-kv__row"
      :class="{ 'mh-kv__row--block': item.block }"
    >
      <dt class="mh-kv__label" :style="{ width: labelWidth, flexBasis: labelWidth }">
        {{ item.label }}
      </dt>
      <dd class="mh-kv__value" :class="{ mono: item.mono && !item.slot }">
        <slot v-if="item.slot" :name="item.slot" :item="item" />
        <Copyable v-else-if="item.copy && !isBlank(item.value)" :value="String(item.value)" />
        <span v-else-if="isBlank(item.value)" class="mh-kv__dash">—</span>
        <span v-else>{{ item.value }}</span>
      </dd>
    </div>
  </dl>
</template>

<style scoped>
.mh-kv {
  margin: 0;
  display: grid;
  gap: var(--sp-2);
}

.mh-kv__row {
  display: flex;
  align-items: baseline;
  gap: var(--sp-3);
  min-width: 0;
}

.mh-kv__row--block {
  flex-direction: column;
  gap: var(--sp-1);
}

.mh-kv__label {
  color: var(--fg-muted);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  flex: none;
}

.mh-kv__value {
  margin: 0;
  color: var(--fg);
  font-size: var(--fs-body);
  line-height: var(--lh-body);
  min-width: 0;
  word-break: break-word;
}

.mh-kv__dash {
  color: var(--fg-faint);
}

@media (max-width: 640px) {
  .mh-kv__row {
    flex-direction: column;
    gap: 0;
  }

  .mh-kv__label {
    width: auto !important;
    flex-basis: auto !important;
  }
}
</style>
