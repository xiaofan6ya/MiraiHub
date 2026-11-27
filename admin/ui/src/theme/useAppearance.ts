import { readonly, ref, watch } from 'vue'

export interface AppearanceSettings {
  motion: 'always' | 'once' | 'off'
  glassEnabled: boolean
  material: 'frosted' | 'liquid'
  glassBlur: number
  glassOpacity: number
  glassSaturation: number
  glowEnabled: boolean
  glowStyle: 'soft' | 'vivid'
  glowIntensity: number
  glowRadius: number
}

export const DEFAULT_APPEARANCE: Readonly<AppearanceSettings> = Object.freeze({
  motion: 'always', glassEnabled: false, material: 'frosted', glassBlur: 18, glassOpacity: 82,
  glassSaturation: 130, glowEnabled: true, glowStyle: 'soft', glowIntensity: 45, glowRadius: 18,
})
const STORAGE_KEY = 'mh.appearance'
const numeric = (value: unknown, fallback: number, min: number, max: number) =>
  typeof value === 'number' && Number.isFinite(value) ? Math.round(Math.max(min, Math.min(max, value))) : fallback

/** Accept only known fields and safe finite ranges, including restored browser storage. */
export function normalizeAppearance(input: unknown): AppearanceSettings {
  const p = input && typeof input === 'object' && !Array.isArray(input) ? input as Record<string, unknown> : {}
  const d = DEFAULT_APPEARANCE
  return {
    motion: p.motion === 'once' || p.motion === 'off' ? p.motion : 'always',
    glassEnabled: typeof p.glassEnabled === 'boolean' ? p.glassEnabled : d.glassEnabled,
    material: p.material === 'liquid' ? 'liquid' : 'frosted',
    glassBlur: numeric(p.glassBlur, d.glassBlur, 0, 32),
    glassOpacity: numeric(p.glassOpacity, d.glassOpacity, 45, 100),
    glassSaturation: numeric(p.glassSaturation, d.glassSaturation, 90, 180),
    glowEnabled: typeof p.glowEnabled === 'boolean' ? p.glowEnabled : d.glowEnabled,
    glowStyle: p.glowStyle === 'vivid' ? 'vivid' : 'soft',
    glowIntensity: numeric(p.glowIntensity, d.glowIntensity, 0, 100),
    glowRadius: numeric(p.glowRadius, d.glowRadius, 4, 40),
  }
}
export function readAppearance(): AppearanceSettings {
  try { return normalizeAppearance(JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null')) }
  catch { return { ...DEFAULT_APPEARANCE } }
}

const state = ref<AppearanceSettings>(readAppearance())
export const appearance = readonly(state)
function applyAppearance(): void {
  if (typeof document === 'undefined') return
  const p = state.value, root = document.documentElement
  const power = p.glowEnabled ? p.glowIntensity / 100 : 0
  const vivid = p.glowStyle === 'vivid'
  const previousMotion = root.dataset.motion
  root.dataset.motion = p.motion
  if (previousMotion !== p.motion) {
    if (p.motion === 'off') document.getAnimations?.().forEach(animation => {
      // Finish native transitions and release WAAPI arrival transforms immediately.
      if (animation.effect?.getTiming().iterations !== Infinity) {
        try { animation.finish() } catch { /* An unresolved timeline can simply be cancelled. */ }
      }
      animation.cancel()
    })
    document.dispatchEvent(new Event('mh:motion-preference'))
  }
  root.dataset.material = p.glassEnabled ? p.material : 'solid'
  root.dataset.glow = p.glowEnabled ? 'on' : 'off'
  root.dataset.glowStyle = p.glowStyle
  const values: Record<string, string> = {
    '--glass-blur': p.glassBlur + 'px', '--glass-opacity': p.glassOpacity + '%',
    '--glass-saturation': p.glassSaturation + '%', '--glow-power': String(power),
    '--glow-radius': p.glowRadius + 'px',
    '--glow-spot-opacity': (power * (vivid ? 40 : 20)).toFixed(2) + '%',
    '--glow-edge-opacity': (power * (vivid ? 24 : 10)).toFixed(2) + '%',
    '--glow-ambient-opacity': (power * (vivid ? 22 : 12)).toFixed(2) + '%',
    '--chart-glow-opacity': (power * (vivid ? .9 : .45)).toFixed(3),
    '--chart-glow-blur': (p.glowRadius * (vivid ? .28 : .2)).toFixed(2) + 'px',
    '--chart-fill-opacity': p.glowEnabled ? (.08 + power * .3).toFixed(3) : '.14',
    '--chart-ink-mix': (power * (vivid ? 50 : 0)).toFixed(2) + '%',
  }
  for (const [key, value] of Object.entries(values)) root.style.setProperty(key, value)
}
watch(state, () => {
  applyAppearance()
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(state.value)) } catch { /* Private-mode preferences stay in memory. */ }
}, { flush: 'sync' })

let initialized = false
export function initAppearance(): void {
  applyAppearance()
  if (initialized || typeof window === 'undefined') return
  initialized = true
  window.addEventListener('storage', event => {
    if (event.key !== STORAGE_KEY && event.key !== null) return
    // One immutable update avoids intermediate writes while a slider is dragged.
    const next = readAppearance()
    if (JSON.stringify(next) !== JSON.stringify(state.value)) state.value = next
  })
}
export function updateAppearance(patch: Partial<AppearanceSettings>): void {
  state.value = normalizeAppearance({ ...state.value, ...patch })
}
export function resetAppearance(): void { state.value = { ...DEFAULT_APPEARANCE } }
export function useAppearance() { return { appearance, updateAppearance, resetAppearance } }
