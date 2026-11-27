<script setup lang="ts">
// Fixed seed: no random render differences, canvas, downloads, timers or full-screen filters.
const stars = Array.from({ length: 46 }, (_, i) => ({ x: (i * 233 + 89) % 1440, y: (i * i * 37 + 23) % 340, r: i % 9 === 0 ? 1.2 : .65, opacity: .2 + (i % 4) * .13 }))
</script>

<template>
  <svg class="cosmic-backdrop" viewBox="0 0 1440 400" preserveAspectRatio="xMidYMin slice" aria-hidden="true" focusable="false">
    <g fill="var(--cosmic-light)"><circle v-for="(star, i) in stars" :key="i" :cx="star.x" :cy="star.y" :r="star.r" :opacity="star.opacity" /></g>
    <g fill="none" stroke="var(--cosmic-light)" stroke-width=".6" opacity=".11"><path d="M919 -90a290 190 -28 0 0 470 298M956 -69a250 160 -28 0 0 402 268" /><path d="M1160 64h10m-5-5v10M363 103h6m-3-3v6" /></g>
  </svg>
</template>

<style scoped>
.cosmic-backdrop{position:absolute;inset:68px 0 auto;width:100%;height:400px;pointer-events:none;color:var(--cosmic-light);opacity:var(--cosmic-visibility,.5);overflow:hidden}
@media(max-width:767px){.cosmic-backdrop{top:60px;height:270px}}
</style>
