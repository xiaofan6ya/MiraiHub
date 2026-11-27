<script setup lang="ts">
import { computed, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, useId } from 'vue'

import { appearance } from '@/theme/useAppearance'

// A small, self-contained SVG scene. Only the satellite and stars animate; the planet is rasterized once.
const id = 'orbit-' + useId().replace(/[^a-zA-Z0-9_-]/g, '')
const scene = ref<HTMLElement | null>(null)
const visible = ref(false)
const hidden = ref(typeof document !== 'undefined' && document.hidden)
const calm = ref(true)
const inactive = ref(false)
const running = computed(() => visible.value && !hidden.value && !calm.value && !inactive.value && appearance.value.motion !== 'off')
let observer: IntersectionObserver | undefined
let media: MediaQueryList | undefined
function syncVisibility() { hidden.value = document.hidden }
function syncMotion() { calm.value = media?.matches ?? true }
onMounted(() => {
  media = window.matchMedia?.('(prefers-reduced-motion: reduce)')
  syncMotion()
  media?.addEventListener?.('change', syncMotion)
  document.addEventListener('visibilitychange', syncVisibility)
  if (typeof IntersectionObserver === 'function' && scene.value) {
    observer = new IntersectionObserver(entries => { visible.value = entries.some(entry => entry.isIntersecting) }, { threshold: .05 })
    observer.observe(scene.value)
  } else visible.value = true
})
onActivated(() => { inactive.value = false; syncVisibility() })
onDeactivated(() => { inactive.value = true })
onBeforeUnmount(() => {
  observer?.disconnect()
  media?.removeEventListener?.('change', syncMotion)
  document.removeEventListener('visibilitychange', syncVisibility)
})
</script>

<template>
  <div ref="scene" class="orbit-scene" :class="{ 'is-running': running }" aria-hidden="true">
    <svg viewBox="0 0 260 290" focusable="false" class="orbit-scene__art">
      <defs>
        <radialGradient :id="id + '-halo'"><stop stop-color="var(--cosmic-light)" stop-opacity=".1" /><stop offset="1" stop-color="var(--cosmic-light)" stop-opacity="0" /></radialGradient>
        <radialGradient :id="id + '-sphere'" cx=".27" cy=".22" r=".83"><stop stop-color="var(--planet-highlight)" /><stop offset=".35" stop-color="var(--planet-mid)" /><stop offset=".74" stop-color="var(--planet-shadow)" /><stop offset="1" stop-color="var(--planet-dark)" /></radialGradient>
        <linearGradient :id="id + '-ring'" x1="0" y1="0" x2="1" y2=".5"><stop stop-color="var(--cosmic-light)" stop-opacity=".12" /><stop offset=".45" stop-color="var(--cosmic-light)" stop-opacity=".7" /><stop offset="1" stop-color="var(--cosmic-light)" stop-opacity=".14" /></linearGradient>
        <clipPath :id="id + '-clip'"><circle cx="130" cy="137" r="56" /></clipPath>
      </defs>
      <circle cx="130" cy="137" r="105" :fill="`url(#${id}-halo)`" />
      <g class="orbit-scene__parallax">
        <ellipse cx="130" cy="144" rx="117" ry="42" transform="rotate(-28 130 144)" class="orbit-scene__guide" stroke-dasharray="2 6" />
        <ellipse cx="130" cy="144" rx="102" ry="29" transform="rotate(-28 130 144)" :stroke="`url(#${id}-ring)`" stroke-width="8" fill="none" opacity=".6" />
        <ellipse cx="130" cy="144" rx="94" ry="23" transform="rotate(-28 130 144)" class="orbit-scene__guide" />
        <circle cx="130" cy="137" r="56" :fill="`url(#${id}-sphere)`" />
        <g :clip-path="`url(#${id}-clip)`" fill="none" stroke="var(--cosmic-light)" stroke-width=".6" opacity=".13" transform="rotate(-18 130 137)">
          <path d="M66 110Q127 88 192 111M64 119Q132 100 196 121M63 128Q127 117 196 134M67 144Q130 136 198 149M69 153Q133 147 194 161M73 165Q126 162 189 173" />
          <path d="M70 113Q122 127 194 122M64 136Q139 147 191 145M74 161Q126 173 188 167" stroke-width="2" opacity=".4" />
        </g>
        <path d="M28 144a102 29 0 0 0 204 0" transform="rotate(-28 130 144)" :stroke="`url(#${id}-ring)`" stroke-width="8" fill="none" />
        <path d="M18 144a112 36 0 0 0 224 0" transform="rotate(-28 130 144)" class="orbit-scene__guide" />
        <g class="orbit-scene__satellite"><circle cx="130" cy="35" r="3" fill="var(--cosmic-light)" /><circle cx="130" cy="35" r="7" fill="var(--cosmic-light)" opacity=".08" /></g>
      </g>
      <g class="orbit-scene__stars" fill="var(--cosmic-light)"><circle cx="42" cy="62" r="1.2" /><circle cx="218" cy="48" r="1" /><circle cx="219" cy="219" r="1.1" /><circle cx="51" cy="222" r=".7" /></g>
      <path d="M192 68h8m-4-4v8M50 190h6m-3-3v6" stroke="var(--cosmic-light)" stroke-width=".7" opacity=".45" />
      <path d="M111 245h38m-30 5h22" stroke="var(--cosmic-light)" stroke-width=".6" opacity=".3" />
    </svg>
  </div>
</template>

<style scoped>
.orbit-scene{width:100%;max-width:190px;aspect-ratio:260/290;pointer-events:none;contain:layout paint}
.orbit-scene__art{width:100%;height:100%;display:block;overflow:visible}
.orbit-scene__guide{fill:none;stroke:var(--cosmic-light);stroke-width:.7;opacity:.24}
.orbit-scene__parallax{transform:translate3d(calc(var(--surface-px,0)*5px),calc(var(--surface-py,0)*4px),0);transition:transform 650ms cubic-bezier(.22,1,.36,1)}
.orbit-scene__satellite{transform-origin:130px 137px;animation:orbit-revolve 72s linear infinite;animation-play-state:paused}
.orbit-scene__stars{animation:orbit-twinkle 7s ease-in-out infinite alternate;animation-play-state:paused}
.is-running .orbit-scene__satellite,.is-running .orbit-scene__stars{animation-play-state:running}
@keyframes orbit-revolve{to{transform:rotate(360deg)}}
@keyframes orbit-twinkle{from{opacity:.35}to{opacity:.9}}
@media(prefers-reduced-motion:reduce){.orbit-scene__satellite,.orbit-scene__stars{animation:none}.orbit-scene__parallax{transform:none;transition:none}}
</style>
