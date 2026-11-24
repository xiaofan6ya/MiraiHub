<script setup lang="ts">
/**
 * 统计卡一组：**一个**卡片外框（与 KpiCard 同外观：12px 圆角 + --shadow-1），
 * 内部用 1px --border-soft 分隔成 N 格（N=2–5）；多行时横竖分隔线都有。
 * ≥1280 按 stripColumns，768–1279 最多 3 列，手机 2 列。
 */
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import StatCell from './StatCell.vue'
import { stripColumns, stripColumnsMedium, type StatItem } from './stat'

const props = withDefaults(
  defineProps<{
    items: StatItem[]
    /** 手动指定列数（默认按条目数自动 2–5） */
    columns?: number
  }>(),
  { columns: undefined },
)

const cols = computed(() => props.columns ?? stripColumns(props.items.length))
const colsMedium = computed(() =>
  props.columns ? Math.min(props.columns, 3) : stripColumnsMedium(props.items.length),
)
</script>

<template>
  <section
    class="mh-strip"
    :style="{ '--strip-cols': String(cols), '--strip-cols-md': String(colsMedium) }"
  >
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
  box-shadow: var(--shadow-1);
  overflow: hidden;
}

/* 每格向左、向上各投 1px 分隔线；最外圈那条落在边框区域，被 overflow 裁掉 */
.mh-strip__cell {
  padding: var(--sp-4) var(--sp-5);
  min-width: 0;
  box-shadow:
    -1px 0 0 0 var(--border-soft),
    0 -1px 0 0 var(--border-soft);
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

@media (max-width: 1279px) {
  .mh-strip {
    grid-template-columns: repeat(var(--strip-cols-md, 3), minmax(0, 1fr));
  }
}

@media (max-width: 767px) {
  .mh-strip {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .mh-strip__cell {
    padding: var(--sp-3) var(--sp-4);
  }
}
</style>
