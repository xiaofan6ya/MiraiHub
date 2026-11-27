import { ref, watch } from 'vue'
import { effectiveTheme } from './useTheme'

export const PALETTES = [
  { id: 'silver', zh: '星空银', en: 'Star silver', color: '#c9d3df' },
  { id: 'graphite', zh: '星空灰', en: 'Cosmic grey', color: '#abb6c6' },
  { id: 'starlight', zh: '月光白', en: 'Moonstone', color: '#e5dfd3' },
  { id: 'cyan', zh: '深空蓝', en: 'Deep space', color: '#9dc6d6' },
  { id: 'rose', zh: '胭脂', en: 'Rose', color: '#e999b8' },
  { id: 'amber', zh: '鎏金', en: 'Amber', color: '#e3c17b' },
] as const
export type PaletteId = typeof PALETTES[number]['id']
const valid = (id: unknown): id is PaletteId => PALETTES.some(palette => palette.id === id)
function saved(): PaletteId {
  // Retired blue-purple presets migrate to silver; explicit remaining palettes keep their preference.
  try { const value = localStorage.getItem('mh.palette.' + effectiveTheme.value); return valid(value) ? value : 'silver' } catch { return 'silver' }
}
export const palette = ref<PaletteId>(saved())
function apply() { if (typeof document !== 'undefined') document.documentElement.dataset.palette = palette.value }
export function initPalette() { apply() }
watch(effectiveTheme, () => { palette.value = saved(); apply() }, { flush: 'sync' })
watch(palette, () => { apply(); try { localStorage.setItem('mh.palette.' + effectiveTheme.value, palette.value) } catch { /* Memory-only in private mode. */ } }, { flush: 'sync' })
export function usePalette() { return { palette, palettes: PALETTES, setPalette: (id: PaletteId) => { palette.value = id } } }
