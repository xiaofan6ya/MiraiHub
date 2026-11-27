<script setup lang="ts">
import type { LayoutSnapshot } from '@/composables/useLayoutSkeleton'
import { useI18n } from '@/i18n'
defineProps<{ snapshot: LayoutSnapshot | null }>()
const { t } = useI18n()
</script>

<template>
  <div class="layout-skeleton-layer" :class="{ 'is-cold': !snapshot }" role="status" :aria-label="t('common.loading')">
    <svg v-if="snapshot" :viewBox="`0 0 ${snapshot.width} ${snapshot.height}`" :style="{ height: snapshot.height + 'px' }" preserveAspectRatio="xMinYMin meet" aria-hidden="true">
      <rect v-for="(rect, index) in snapshot.rects" :key="index" :x="rect.x" :y="rect.y" :width="rect.width" :height="rect.height" :rx="rect.radius" :class="rect.surface ? 'layout-skeleton-surface' : 'layout-skeleton-shape'" />
    </svg>
    <span v-else class="layout-cold-hint"><i aria-hidden="true" />{{ t('common.loading') }}</span>
  </div>
</template>

<style scoped>
.layout-skeleton-layer{position:absolute;inset:0;pointer-events:none;z-index:2;color:var(--fg-muted)}.layout-skeleton-layer svg{display:block;width:100%;max-height:14000px}.layout-skeleton-surface{fill:var(--surface);stroke:var(--border-soft);stroke-width:1}.layout-skeleton-shape{fill:color-mix(in srgb,var(--brand) 13%,var(--surface-2));animation:layout-breathe 1800ms ease-in-out infinite alternate}.layout-cold-hint{display:flex;align-items:center;justify-content:center;gap:9px;padding-top:180px;font-size:12px;color:var(--fg-muted)}.layout-cold-hint i{width:6px;height:6px;border-radius:50%;background:var(--brand);animation:layout-breathe 1300ms ease-in-out infinite alternate}@keyframes layout-breathe{to{opacity:.45}}@media(prefers-reduced-motion:reduce){.layout-skeleton-shape,.layout-cold-hint i{animation:none}}
</style>
