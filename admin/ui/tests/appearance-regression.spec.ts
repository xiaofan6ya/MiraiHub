import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent, nextTick } from 'vue'
import ThemeStudio from '@/layout/ThemeStudio.vue'
import TrafficTrend from '@/components/charts/TrafficTrend.vue'

vi.mock('naive-ui', () => ({ NPopover: defineComponent({ template: '<aside><slot name="trigger"/><slot/></aside>' }) }))
vi.mock('@/components/feedback/islandQueue', () => ({ useIslandMessage: () => ({ success: vi.fn() }) }))
const modules = import.meta.glob('../src/theme/useAppearance.ts')
let api: any
beforeAll(async () => { const loader=modules['../src/theme/useAppearance.ts']; api=loader ? await loader() : null })
beforeEach(() => { api?.resetAppearance(); api?.initAppearance() })
function available() { console.log('OBSERVED appearance-settings=' + Boolean(api)); expect(api).toBeTruthy(); return api }

describe('Glass materials and adjustable glow', () => {
 it('offers a glass switch and solid/frosted/liquid choices in the existing studio', () => {
  const w=mount(ThemeStudio);const text=w.text();const enabled=w.find('[role=switch][aria-label="启用玻璃材质"]').exists()
  console.log('OBSERVED glass-controls='+enabled+'|'+text.includes('毛玻璃')+'|'+text.includes('液态玻璃'))
  expect(enabled).toBe(true);expect(text).toContain('实色');expect(text).toContain('毛玻璃');expect(text).toContain('液态玻璃');w.unmount()
 })
 it('applies material parameters live and persists them locally', () => {
  const a=available();a.updateAppearance({glassEnabled:true,material:'liquid',glassBlur:26,glassOpacity:60,glassSaturation:150,glowIntensity:90,glowRadius:30})
  const root=document.documentElement,saved=JSON.parse(localStorage.getItem('mh.appearance') || '{}')
  console.log('OBSERVED material='+root.dataset.material+'|'+root.style.getPropertyValue('--glass-blur')+'|'+root.style.getPropertyValue('--glass-opacity'))
  expect(root.dataset.material).toBe('liquid');expect(root.style.getPropertyValue('--glass-blur')).toBe('26px');expect(root.style.getPropertyValue('--glass-opacity')).toBe('60%');expect(saved.glowIntensity).toBe(90);expect(saved.glowRadius).toBe(30)
 })
 it('can disable glass and glow without losing the chosen parameters', () => {
  const a=available();a.updateAppearance({glassEnabled:true,material:'liquid',glowIntensity:88,glowEnabled:false});a.updateAppearance({glassEnabled:false})
  console.log('OBSERVED disabled='+document.documentElement.dataset.material+'|'+document.documentElement.dataset.glow)
  expect(document.documentElement.dataset.material).toBe('solid');expect(document.documentElement.dataset.glow).toBe('off');expect(a.appearance.value.glowIntensity).toBe(88)
  a.updateAppearance({glowEnabled:true,glassEnabled:true});expect(document.documentElement.dataset.material).toBe('liquid');expect(a.appearance.value.glowIntensity).toBe(88)
 })
 it('clamps numeric input and rejects malformed settings', () => {
  const a=available(),p=a.normalizeAppearance({glassBlur:999,glassOpacity:-2,glassSaturation:Infinity,glowIntensity:300,glowRadius:-99,material:'unknown',glowEnabled:'false'})
  console.log('OBSERVED clamped='+p.glassBlur+'|'+p.glassOpacity+'|'+p.glowIntensity+'|'+p.glowRadius)
  expect(p.glassBlur).toBe(32);expect(p.glassOpacity).toBe(45);expect(p.glowIntensity).toBe(100);expect(p.glowRadius).toBe(4);expect(p.material).toBe('frosted');expect(p.glowEnabled).toBe(true);expect(a.normalizeAppearance(null)).toEqual(a.DEFAULT_APPEARANCE)
 })
 it('restores saved values, tolerates invalid storage and resets cleanly', () => {
  const a=available();localStorage.setItem('mh.appearance',JSON.stringify({glassEnabled:true,material:'frosted',glowIntensity:74}));expect(a.readAppearance().glowIntensity).toBe(74)
  localStorage.setItem('mh.appearance','{broken');expect(a.readAppearance()).toEqual(a.DEFAULT_APPEARANCE)
  a.updateAppearance({glowIntensity:99});a.resetAppearance();console.log('OBSERVED reset-strength='+a.appearance.value.glowIntensity);expect(a.appearance.value).toEqual(a.DEFAULT_APPEARANCE)
 })
 it('strengthens the actual traffic curve using the same geometry', () => {
  const w=mount(TrafficTrend,{props:{values:[3,8,5,12]}}),bloom=w.find('.trend-bloom'),line=w.find('.trend-line')
  console.log('OBSERVED traffic-bloom='+bloom.exists())
  expect(bloom.exists()).toBe(true);expect(bloom.attributes('d')).toBe(line.attributes('d'));w.unmount()
 })
 it('updates brightness without remounting the chart or resetting keyboard selection', async () => {
  const a=available(),w=mount(TrafficTrend,{props:{values:[3,8,5,12]}});await w.find('.traffic-trend').trigger('keydown',{key:'ArrowRight'});const node=w.find('.trend-line').element,tip=w.find('.trend-tooltip').text()
  a.updateAppearance({glowStyle:'vivid',glowIntensity:95});await nextTick()
  console.log('OBSERVED glow-live='+document.documentElement.dataset.glowStyle+'|'+(w.find('.trend-line').element===node))
  expect(document.documentElement.dataset.glowStyle).toBe('vivid');expect(w.find('.trend-line').element).toBe(node);expect(w.find('.trend-tooltip').text()).toBe(tip);w.unmount()
 })
})
