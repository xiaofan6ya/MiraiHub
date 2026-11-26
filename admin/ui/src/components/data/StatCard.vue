<script setup lang="ts">
/** 单张统计卡（需要独立卡片时用；成组请用 StatStrip；新页面优先用 KpiCard）。 */
import { RouterLink } from 'vue-router'
import Card from '../base/Card.vue'
import StatCell from './StatCell.vue'
import type { StatItem } from './stat'

defineProps<{ item: StatItem }>()
</script>

<template>
  <RouterLink v-if="item.to" :to="item.to" class="mh-stat-link">
    <Card hoverable>
      <StatCell :item="item" />
    </Card>
  </RouterLink>
  <Card v-else>
    <StatCell :item="item" />
  </Card>
</template>

<style scoped>
.mh-stat-link {
  display: block;
  color: inherit;
  text-decoration: none;
  border-radius: var(--r-card);
}

.mh-stat-link:hover {
  text-decoration: none;
  color: inherit;
}
</style>
