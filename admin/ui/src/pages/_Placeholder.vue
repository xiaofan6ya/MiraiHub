<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import PageHeader from '@/layout/PageHeader.vue'

/**
 * 占位页：24 个路由在第一阶段全部可点（S04 验收），
 * 显示页面名、「建设中」与旧版位置，后续页面任务逐个替换。
 */
const route = useRoute()
const title = computed(() => String(route.meta.title ?? '页面'))
const key = computed(() => String(route.name ?? ''))
</script>

<template>
  <div class="placeholder">
    <PageHeader :title="title" />

    <section class="ph-card">
      <p class="ph-status">建设中</p>
      <p class="ph-desc">该页面将在后续步骤实现，功能与旧版保持一致。</p>
      <p class="ph-old">
        旧版位置：<code class="mono">/admin?tab={{ key }}</code>
      </p>
    </section>
  </div>
</template>

<style scoped>
.placeholder {
  width: 100%;
  min-width: 0;
}

.ph-card {
  padding: var(--sp-5);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-card);
}

.ph-status {
  margin: 0;
  font-size: var(--fs-h2);
  line-height: var(--lh-h2);
  font-weight: 600;
  color: var(--fg);
}

.ph-desc {
  margin-top: var(--sp-2);
  font-size: var(--fs-body);
  line-height: var(--lh-body);
  color: var(--fg-2);
}

.ph-old {
  margin-top: var(--sp-3);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}
</style>
