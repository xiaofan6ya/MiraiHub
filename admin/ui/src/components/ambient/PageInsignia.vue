<script setup lang="ts">
import type { Component } from 'vue'
defineProps<{ icon: Component }>()
</script>

<template>
  <svg class="page-insignia" width="80" height="80" viewBox="0 0 96 96" fill="none" aria-hidden="true" focusable="false">
    <circle class="page-insignia__halo" cx="48" cy="48" r="44" />
    <circle class="page-insignia__orbit" cx="48" cy="48" r="40" stroke-dasharray="2 7" />
    <path class="page-insignia__drawing" pathLength="1" d="M11 63C0 49 24 21 48 17s48 12 37 29S57 76 31 78" />
    <rect class="page-insignia__tile" x="23" y="23" width="50" height="50" rx="16" />
    <path class="page-insignia__highlight" d="M37 24h20c9 0 15 5 15 15" />
    <component :is="icon" x="34" y="34" :size="28" :stroke-width="1.6" />
    <g class="page-insignia__satellites">
      <circle cx="17" cy="67" r="3" />
      <circle cx="79" cy="27" r="2.5" />
      <path d="M79 66v8m-4-4h8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
    </g>
  </svg>
</template>

<style scoped>
.page-insignia{display:block;flex:none;color:var(--brand);fill:none;background:transparent;overflow:visible}
.page-insignia__halo{fill:none}
.page-insignia__orbit{stroke:var(--border-strong);stroke-width:1}
.page-insignia__drawing{fill:none;stroke:var(--brand);stroke-width:1;opacity:.42;stroke-linecap:round;animation:insignia-draw 720ms ease-out both}
.page-insignia__tile{fill:none;stroke:var(--border-strong);stroke-width:1}
.page-insignia__highlight{stroke:var(--brand);stroke-width:1;opacity:.45}
.page-insignia__satellites{fill:var(--brand);transform-origin:48px 48px;transition:transform 450ms var(--ease-out)}
@media(hover:hover) and (prefers-reduced-motion:no-preference){.page-insignia:hover .page-insignia__satellites{transform:rotate(14deg)}}
@keyframes insignia-draw{from{stroke-dasharray:1;stroke-dashoffset:1;opacity:0}to{stroke-dasharray:1;stroke-dashoffset:0;opacity:.42}}
@media(prefers-reduced-motion:reduce){.page-insignia__drawing{animation:none}.page-insignia__satellites{transition:none}}
@media(max-width:767px){.page-insignia{width:56px;height:56px}}
</style>
