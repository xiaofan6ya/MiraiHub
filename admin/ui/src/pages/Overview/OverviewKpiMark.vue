<script setup lang="ts">
defineProps<{ kind: string }>()
</script>

<template>
  <svg class="kpi-orbit-mark" viewBox="0 0 48 36" fill="none" stroke="currentColor" stroke-width=".8" focusable="false">
    <g v-if="kind === 'balance'"><ellipse cx="24" cy="18" rx="20" ry="7" transform="rotate(-28 24 18)" /><circle cx="24" cy="18" r="9" /><path d="M19 12c-3 5-2 10 3 14" opacity=".5" /><circle cx="40" cy="8" r="1.8" fill="currentColor" stroke="none" /></g>
    <g v-else-if="kind === 'requests'"><path d="m5 27 10-8 10 4L35 8l9 5" opacity=".5" /><circle cx="5" cy="27" r="2" /><circle cx="15" cy="19" r="2" /><circle cx="25" cy="23" r="2" /><circle cx="35" cy="8" r="2.5" /><circle cx="44" cy="13" r="1.5" /><path d="M34 26h8m-4-4v8" opacity=".6" /></g>
    <g v-else-if="kind === 'success'"><circle cx="26" cy="18" r="13" stroke-dasharray="43 8 15 8" /><circle cx="26" cy="18" r="8" opacity=".5" /><path d="m22 18 3 3 5-6M7 8v8m-4-4h8" /><circle cx="38" cy="13" r="2" fill="currentColor" stroke="none" /></g>
    <g v-else><path d="m8 15 16-9 16 9-16 9Zm0 6 16 9 16-9M8 26l16 9 16-9" /><path d="M24 24v6M8 15v5m32-5v5" opacity=".5" /><circle cx="42" cy="5" r="1.5" fill="currentColor" stroke="none" /></g>
  </svg>
</template>

<style scoped>
.kpi-orbit-mark{display:block;width:100%;height:100%;transition:transform 600ms cubic-bezier(.22,1,.36,1)}
[data-surface-active] .kpi-orbit-mark{transform:translate3d(calc(var(--surface-px,0)*2px),calc(var(--surface-py,0)*2px),0)}
@media(prefers-reduced-motion:reduce){.kpi-orbit-mark{transition:none;transform:none!important}}
</style>
