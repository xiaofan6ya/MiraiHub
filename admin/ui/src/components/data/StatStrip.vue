<script setup lang="ts">
/**
 * 统计卡一组：**一个**卡片外框，内部用竖分隔线分成 N 格（N=2–5）。
 * 手机上 2 列网格。
 */
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import StatCell from './StatCell.vue'
import { stripColumns, type StatItem } from './stat'

const props = withDefaults(
  defineProps<{
    items: StatItem[]
    /** 手动指定列数（默认按条目数自动 2–5） */
    columns?: number
  }>(),
  { columns: undefined },
)

const cols = computed(() => props.columns ?? stripColumns(props.items.length))
</script>

<template>
  <section class="mh-strip" :style="{ '--strip-cols': String(cols) }">
    <template v-for="item in items" :key="item.label">
      <RouterLink v-if="item.to" :to="item.to" class="mh-strip__cell mh-strip__cell--link">
        <StatCell :item="item" />
      </RouterLink>
      <div v-else class="mh-strip__cell">
        <StatCell :item="item" />
      </div>
    </template>
  </section>
</template>

<style scoped>
.mh-strip {
  display: grid;
  grid-template-columns: repeat(var(--strip-cols, 3), minmax(0, 1fr));
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-card);
  overflow: hidden;
}

.mh-strip__cell {
  padding: var(--sp-4) var(--sp-5);
  min-width: 0;
  border-left: 1px solid var(--border-soft);
}

.mh-strip__cell:first-child {
  border-left: 0;
}

.mh-strip__cell--link {
  color: inherit;
  text-decoration: none;
  transition: background var(--t-fast) var(--ease);
}

.mh-strip__cell--link:hover {
  background: var(--surface-hover);
  text-decoration: none;
}

@media (max-width: 640px) {
  .mh-strip {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .mh-strip__cell {
    border-left: 0;
    padding: var(--sp-3) var(--sp-4);
  }

  .mh-strip__cell:nth-child(2n) {
    border-left: 1px solid var(--border-soft);
  }

  .mh-strip__cell:nth-child(n + 3) {
    border-top: 1px solid var(--border-soft);
  }
}
</style>
