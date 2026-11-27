<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import { NPopover } from 'naive-ui'
import { Check, Monitor, Moon, Palette, RotateCcw, Sparkles, Sun } from 'lucide-vue-next'
import { usePalette } from '@/theme/usePalette'
import { useTheme } from '@/theme/useTheme'
import { useAppearance } from '@/theme/useAppearance'
import { useI18n, type MessageKey } from '@/i18n'
import { useIslandMessage } from '@/components/feedback/islandQueue'

const open = ref(false)
const { palette, palettes, setPalette } = usePalette()
const { mode, setMode } = useTheme()
const { locale, t } = useI18n()
const message = useIslandMessage()
const isEnglish = computed(() => locale.value === 'en-US')
const { appearance, updateAppearance, resetAppearance } = useAppearance()
const id = useId()
const trigger = ref<HTMLButtonElement | null>(null)
const panelHeight = ref(640)
const panelWidth = ref(360)
const popupX = ref(12)
const popupY = ref(0)
const placement = ref<'bottom-start' | 'top-start'>('bottom-start')
function fitPanel() {
  if (!trigger.value) return
  const anchor = trigger.value.getBoundingClientRect()
  const below = Math.max(100, window.innerHeight - anchor.bottom - 20)
  const above = Math.max(100, anchor.top - 20)
  const viewportWidth = document.documentElement.clientWidth
  panelWidth.value = Math.min(360, viewportWidth - 24)
  popupX.value = Math.max(12, Math.min(anchor.right - panelWidth.value, viewportWidth - panelWidth.value - 12))
  popupY.value = below >= above ? anchor.bottom : anchor.top
  placement.value = below >= above ? 'bottom-start' : 'top-start'
  panelHeight.value = Math.min(830, Math.max(below, above))
}
watch(open, async value => { if (value) { await nextTick(); fitPanel() } })
onMounted(() => window.addEventListener('resize', fitPanel))
onBeforeUnmount(() => window.removeEventListener('resize', fitPanel))
const supportsGlass = typeof CSS !== 'undefined' && (CSS.supports('backdrop-filter', 'blur(1px)') || CSS.supports('-webkit-backdrop-filter', 'blur(1px)'))
const selectedMaterial = computed(() => appearance.value.glassEnabled ? appearance.value.material : 'solid')
const materials = ['solid', 'frosted', 'liquid'] as const
const glowStyles = ['soft', 'vivid'] as const
const motionModes = ['always', 'once', 'off'] as const
type NumericKey = 'glassBlur' | 'glassOpacity' | 'glassSaturation' | 'glowIntensity' | 'glowRadius'
type Slider = { key: NumericKey; label: MessageKey; min: number; max: number; unit: string }
const glowSliders: Slider[] = [
  { key: 'glowIntensity', label: 'studio.intensity', min: 0, max: 100, unit: '%' },
  { key: 'glowRadius', label: 'studio.radius', min: 4, max: 40, unit: 'px' },
]
const glassSliders: Slider[] = [
  { key: 'glassBlur', label: 'studio.blur', min: 0, max: 32, unit: 'px' },
  { key: 'glassOpacity', label: 'studio.opacity', min: 45, max: 100, unit: '%' },
]
function numberInput(key: NumericKey, event: Event) {
  updateAppearance({ [key]: Number((event.target as HTMLInputElement).value) })
}
function selectMaterial(material: typeof materials[number]) {
  updateAppearance(material === 'solid' ? { glassEnabled: false } : { glassEnabled: true, material })
}
const rangeStyle = (slider: Slider) => ({ '--studio-range': `${(appearance.value[slider.key] - slider.min) / (slider.max - slider.min) * 100}%` })
function toggleStudio() { fitPanel(); open.value = !open.value }
function closeOutside(event: MouseEvent) {
  if (!trigger.value?.contains(event.target as Node)) open.value = false
}
function closeStudio() { open.value = false; trigger.value?.focus() }
</script>

<template>
  <button ref="trigger" class="studio-trigger" type="button" :aria-label="t('studio.title')" :aria-expanded="open" aria-haspopup="dialog" @click="toggleStudio"><Palette :size="17" /></button>
  <n-popover v-model:show="open" trigger="manual" :placement="placement" :x="popupX" :y="popupY" :flip="false" :show-arrow="false" :width="panelWidth" style="max-width:calc(100vw - 24px);padding:0" class="theme-studio" @clickoutside="closeOutside">
    <div class="studio-panel" role="dialog" :aria-label="t('studio.title')" :style="{ maxHeight: panelHeight + 'px' }" @keydown.esc.stop="closeStudio">
    <header class="studio-head"><div><span class="studio-label">APPEARANCE</span><strong>{{ t('studio.title') }}</strong><p>{{ t('studio.description') }}</p></div><button class="studio-reset" type="button" :aria-label="t('studio.reset')" :title="t('studio.reset')" @click="resetAppearance"><RotateCcw :size="15" /></button></header>
    <div class="studio-scroll">
    <div class="studio-mode" role="group" :aria-label="t('studio.mode')">
      <button type="button" :class="{ selected: mode === 'light' }" :aria-pressed="mode === 'light'" @click="setMode('light')"><Sun :size="14" />{{ t('studio.light') }}</button>
      <button type="button" :class="{ selected: mode === 'dark' }" :aria-pressed="mode === 'dark'" @click="setMode('dark')"><Moon :size="14" />{{ t('studio.dark') }}</button>
      <button type="button" :class="{ selected: mode === 'system' }" :aria-pressed="mode === 'system'" @click="setMode('system')"><Monitor :size="14" />{{ t('studio.system') }}</button>
    </div>
    <div class="studio-palettes" role="group" :aria-label="t('studio.palette')">
      <button v-for="item in palettes" :key="item.id" type="button" class="studio-swatch" :class="{ selected: palette === item.id }" :aria-pressed="palette === item.id" :style="{ '--swatch': item.color }" @click="setPalette(item.id)"><span class="swatch-preview"><svg viewBox="0 0 64 32" aria-hidden="true"><circle cx="33" cy="16" r="8" fill="var(--swatch)" fill-opacity=".15" /><ellipse cx="33" cy="17" rx="19" ry="5" transform="rotate(-25 33 17)" /><circle cx="33" cy="16" r="8" /><path d="M7 7h4m-2-2v4M53 25h3" opacity=".5" /></svg><Check v-if="palette === item.id" :size="12" class="swatch-check" /></span><span>{{ isEnglish ? item.en : item.zh }}</span></button>
    </div>
    <section class="studio-section" :aria-label="t('studio.motion')">
      <div class="studio-section-head"><div><h3>{{ t('studio.motion') }}</h3><p>{{ t('studio.motionHelp') }}</p></div></div>
      <div class="studio-segments" role="group" :aria-label="t('studio.motion')"><button v-for="option in motionModes" :key="option" type="button" :class="{ selected: appearance.motion === option }" :aria-pressed="appearance.motion === option" @click="updateAppearance({ motion: option })">{{ t(`studio.motion.${option}`) }}</button></div>
      <p class="studio-motion-note">{{ t(`studio.motionNote.${appearance.motion}`) }}</p>
    </section>
    <div class="studio-live" role="img" :aria-label="t('studio.livePreview')">
      <svg class="studio-preview-sky" viewBox="0 0 320 100" preserveAspectRatio="none" aria-hidden="true"><path d="M0 24H320M0 50H320M0 76H320M40 0V100M100 0V100M160 0V100M220 0V100M280 0V100" /><circle cx="256" cy="34" r="28" /><ellipse cx="256" cy="34" rx="45" ry="10" transform="rotate(-24 256 34)" /><path class="studio-preview-bloom" d="M0 84C32 96 50 91 79 63S122 44 153 54S190 24 224 30S279 65 320 37" /><path class="studio-preview-curve" d="M0 84C32 96 50 91 79 63S122 44 153 54S190 24 224 30S279 65 320 37" /></svg>
      <div class="studio-material-sample"><Sparkles :size="15" /><strong>{{ t(`studio.${selectedMaterial}`) }}</strong><span>{{ t('studio.previewCaption') }}</span></div><span class="studio-live-label">{{ t('studio.livePreview') }}</span>
    </div>
    <section class="studio-section" :aria-label="t('studio.glow')">
      <div class="studio-section-head"><div><h3>{{ t('studio.glow') }}</h3><p>{{ t('studio.glowHelp') }}</p></div><button class="studio-toggle" type="button" role="switch" :aria-label="t('studio.glowSwitch')" :aria-checked="appearance.glowEnabled" @click="updateAppearance({ glowEnabled: !appearance.glowEnabled })"><span /></button></div>
      <div class="studio-segments" role="group" :aria-label="t('studio.glow')"><button v-for="option in glowStyles" :key="option" type="button" :class="{ selected: appearance.glowStyle === option }" :aria-pressed="appearance.glowStyle === option" :disabled="!appearance.glowEnabled" @click="updateAppearance({ glowStyle: option })">{{ t(`studio.${option}`) }}</button></div>
      <div v-for="slider in glowSliders" :key="slider.key" class="studio-slider" :class="{ 'is-disabled': !appearance.glowEnabled }"><div><label :for="`${id}-${slider.key}`">{{ t(slider.label) }}</label><output :for="`${id}-${slider.key}`" class="tnum">{{ appearance[slider.key] }}<small>{{ slider.unit }}</small></output></div><input :id="`${id}-${slider.key}`" type="range" :min="slider.min" :max="slider.max" step="1" :value="appearance[slider.key]" :disabled="!appearance.glowEnabled" :style="rangeStyle(slider)" @input="numberInput(slider.key, $event)" /></div>
    </section>
    <section class="studio-section" :aria-label="t('studio.material')">
      <div class="studio-section-head"><div><h3>{{ t('studio.material') }}</h3><p>{{ t('studio.materialHelp') }}</p></div><button class="studio-toggle" type="button" role="switch" :aria-label="t('studio.glassSwitch')" :aria-checked="appearance.glassEnabled" @click="updateAppearance({ glassEnabled: !appearance.glassEnabled })"><span /></button></div>
      <div class="studio-segments" role="group" :aria-label="t('studio.material')"><button v-for="option in materials" :key="option" type="button" :class="{ selected: selectedMaterial === option }" :aria-pressed="selectedMaterial === option" @click="selectMaterial(option)">{{ t(`studio.${option}`) }}</button></div>
      <p v-if="appearance.glassEnabled && appearance.material === 'liquid'" class="studio-hint">{{ t('studio.liquidHelp') }}</p>
      <div v-for="slider in glassSliders" :key="slider.key" class="studio-slider" :class="{ 'is-disabled': !appearance.glassEnabled }"><div><label :for="`${id}-${slider.key}`">{{ t(slider.label) }}</label><output :for="`${id}-${slider.key}`" class="tnum">{{ appearance[slider.key] }}<small>{{ slider.unit }}</small></output></div><input :id="`${id}-${slider.key}`" type="range" :min="slider.min" :max="slider.max" step="1" :value="appearance[slider.key]" :disabled="!appearance.glassEnabled" :style="rangeStyle(slider)" @input="numberInput(slider.key, $event)" /></div>
      <details class="studio-advanced"><summary>{{ t('studio.advanced') }}</summary><div class="studio-slider" :class="{ 'is-disabled': !appearance.glassEnabled }"><div><label :for="`${id}-glassSaturation`">{{ t('studio.saturation') }}</label><output :for="`${id}-glassSaturation`" class="tnum">{{ appearance.glassSaturation }}<small>%</small></output></div><input :id="`${id}-glassSaturation`" type="range" min="90" max="180" step="1" :value="appearance.glassSaturation" :disabled="!appearance.glassEnabled" :style="rangeStyle({key:'glassSaturation',label:'studio.saturation',min:90,max:180,unit:'%'})" @input="numberInput('glassSaturation', $event)" /></div></details>
      <p v-if="appearance.glassEnabled && !supportsGlass" class="studio-hint" role="status">{{ t('studio.browserFallback') }}</p>
    </section>
    <button type="button" class="studio-preview" @click="message.success(t('studio.previewMessage'))">{{ t('studio.preview') }}<span aria-hidden="true">↗</span></button>
    </div>
    <footer class="studio-foot"><span>{{ t('studio.saved') }}</span><span>{{ t('studio.localEffects') }}</span></footer>
    </div>
  </n-popover>
</template>

<style scoped>
.studio-motion-note{margin:8px 0 0;color:var(--fg-muted);font-size:11px;line-height:1.7}
.studio-trigger{display:grid;place-items:center;width:34px;height:34px;padding:0;border:1px solid var(--border);border-radius:11px;background:var(--surface);color:var(--fg-2);cursor:pointer;transition:transform 200ms var(--ease-spring),background 160ms}.studio-trigger:hover{background:var(--surface-hover)}.studio-trigger:active{transform:scale(.94)}
.studio-head{display:flex;justify-content:space-between;gap:8px;align-items:flex-start;padding:8px 2px 16px}.studio-head strong{font-size:15px;font-weight:600}.studio-head p{font-size:11px;color:var(--fg-muted);margin-top:3px}.studio-label{font:8px var(--font-mono);letter-spacing:1px;color:var(--fg-muted);margin-top:5px}.studio-mode{display:flex;padding:3px;gap:3px;background:var(--surface-2);border:1px solid var(--border-soft);border-radius:11px}.studio-mode button{flex:1;display:flex;align-items:center;justify-content:center;gap:7px;border:0;border-radius:8px;background:transparent;padding:7px;color:var(--fg-muted);cursor:pointer;font-size:12px}.studio-mode button.selected{background:var(--surface-raised);color:var(--fg);box-shadow:var(--shadow-1)}
.studio-palettes{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin:16px 0}.studio-swatch{padding:0 0 6px;border:1px solid var(--border);border-radius:9px;overflow:hidden;background:var(--surface);color:var(--fg-muted);font-size:11px;cursor:pointer;transition:border-color 180ms,transform 220ms var(--ease-spring)}.studio-swatch:hover{transform:translateY(-2px)}.studio-swatch.selected{border-color:var(--swatch);color:var(--fg)}.swatch-preview{height:43px;display:flex;justify-content:center;gap:7px;align-items:center;padding:8px;position:relative;background:#10161d;margin-bottom:6px}.swatch-preview i{width:4px;height:25px;border-radius:3px;background:var(--swatch);opacity:.65}.swatch-preview svg{width:64px;height:36px;fill:none;stroke:var(--swatch);stroke-width:.8}.swatch-check{position:absolute;right:4px;top:4px;color:var(--swatch)}
.studio-preview{display:flex;justify-content:space-between;width:100%;padding:9px 10px;border:1px solid var(--border);border-radius:8px;background:var(--surface-2);color:var(--fg-2);font-size:11px;cursor:pointer}.studio-preview:hover{border-color:var(--brand)}.studio-foot{margin:10px 0 2px;font-size:10px;text-align:center;color:var(--fg-muted)}
@media(prefers-reduced-motion:reduce){.studio-swatch:hover,.studio-trigger:active{transform:none}}
.studio-panel{display:flex;flex-direction:column;max-height:min(830px,calc(100dvh - 112px));color:var(--fg)}.studio-head{padding:16px 18px 14px;align-items:center;flex:none}.studio-head strong{display:block;margin-top:4px;font-size:16px}.studio-head p{margin:4px 0 0;line-height:1.5}.studio-label{font-size:8px;letter-spacing:1.8px}.studio-reset{display:grid;place-items:center;width:30px;height:30px;flex:none;border:1px solid var(--border);border-radius:9px;background:var(--surface-2);color:var(--fg-muted);cursor:pointer}.studio-reset:hover{color:var(--fg);background:var(--surface-hover)}
.studio-scroll{min-height:0;overflow:auto;overscroll-behavior:contain;padding:0 18px 14px;scrollbar-width:thin;scrollbar-color:var(--border-strong) transparent}.studio-mode button{font-size:11px}.studio-palettes{gap:7px;margin:12px 0}.studio-swatch{padding-bottom:5px;font-size:10px}.swatch-preview{height:30px;padding:0;margin-bottom:4px}.swatch-preview svg{width:54px;height:29px}.swatch-preview .swatch-check{width:12px;height:12px}
.studio-live{position:relative;isolation:isolate;display:flex;align-items:center;min-height:92px;padding:12px 16px;border:1px solid var(--border);border-radius:12px;background:var(--bg);overflow:hidden}.studio-preview-sky{position:absolute;inset:0;width:100%;height:100%;fill:none;stroke:var(--brand);stroke-width:.5;stroke-opacity:.13}.studio-preview-sky circle{fill:var(--brand);fill-opacity:.2;stroke-opacity:.65}.studio-preview-sky ellipse{stroke-opacity:.5}.studio-preview-sky .studio-preview-curve{stroke:var(--chart-line,var(--brand));stroke-opacity:1;stroke-width:1.5;filter:drop-shadow(0 0 calc(var(--glow-radius) * .18) color-mix(in srgb,var(--brand) calc(var(--glow-power) * 90%),transparent))}.studio-preview-sky .studio-preview-bloom{stroke:var(--chart-line,var(--brand));stroke-opacity:var(--chart-glow-opacity);stroke-width:5;filter:blur(var(--chart-glow-blur))}.studio-material-sample{position:relative;display:grid;grid-template-columns:16px auto;align-items:center;column-gap:7px;row-gap:3px;padding:11px 13px;border:1px solid var(--border-strong);border-radius:12px;background:var(--surface);box-shadow:var(--shadow-1)}.studio-material-sample strong{font-size:12px;font-weight:600}.studio-material-sample span{grid-column:1/-1;font-size:9px;color:var(--fg-muted)}.studio-live-label{position:absolute;right:9px;bottom:7px;font-size:8px;letter-spacing:.6px;color:var(--fg-muted)}
.studio-section{padding:14px 0;border-bottom:1px solid var(--border-soft)}.studio-section-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:10px}.studio-section-head h3{margin:0;font-size:12px;font-weight:600}.studio-section-head p{margin:3px 0 0;font-size:10px;color:var(--fg-muted);line-height:1.5}.studio-toggle{position:relative;flex:none;width:32px;height:19px;padding:2px;border:1px solid var(--border-strong);border-radius:20px;background:var(--surface-2);cursor:pointer;transition:background 180ms,box-shadow 180ms}.studio-toggle span{display:block;width:13px;height:13px;border-radius:50%;background:var(--fg-muted);transition:transform 200ms var(--ease-spring),background 180ms}.studio-toggle[aria-checked=true]{background:var(--brand);box-shadow:0 0 10px color-mix(in srgb,var(--brand) calc(var(--glow-power) * 36%),transparent)}.studio-toggle[aria-checked=true] span{transform:translateX(13px);background:var(--bg)}
.studio-segments{display:flex;padding:3px;gap:3px;background:var(--surface-2);border:1px solid var(--border-soft);border-radius:10px}.studio-segments button{flex:1;min-width:0;border:1px solid transparent;border-radius:7px;background:transparent;padding:6px 4px;color:var(--fg-muted);cursor:pointer;font:inherit;font-size:11px;transition:background 160ms,color 160ms}.studio-segments button.selected{background:var(--surface-raised);border-color:var(--border);color:var(--fg);box-shadow:var(--shadow-1)}.studio-segments button:disabled{opacity:.4;cursor:default}
.studio-slider{margin-top:10px;transition:opacity 160ms}.studio-slider>div{display:flex;align-items:center;justify-content:space-between;font-size:10px;color:var(--fg-2)}.studio-slider output{font-family:var(--font-mono);font-size:10px;color:var(--fg)}.studio-slider output small{margin-left:3px;font:inherit;font-size:9px;color:var(--fg-muted)}.studio-slider input{display:block;width:100%;height:18px;margin:3px 0 0;padding:0;appearance:none;-webkit-appearance:none;background:transparent;cursor:pointer;accent-color:var(--brand)}.studio-slider input::-webkit-slider-runnable-track{height:3px;border-radius:9px;background:linear-gradient(to right,var(--brand) var(--studio-range),var(--border-strong) var(--studio-range))}.studio-slider input::-moz-range-track{height:3px;border-radius:9px;background:var(--border-strong)}.studio-slider input::-moz-range-progress{height:3px;border-radius:9px;background:var(--brand)}.studio-slider input::-webkit-slider-thumb{appearance:none;-webkit-appearance:none;width:12px;height:12px;margin-top:-4.5px;border:2px solid var(--surface-raised);border-radius:50%;background:var(--brand);box-shadow:0 1px 5px #0003}.studio-slider input::-moz-range-thumb{width:9px;height:9px;border:2px solid var(--surface-raised);border-radius:50%;background:var(--brand)}.studio-slider.is-disabled{opacity:.35}.studio-slider input:disabled{cursor:default}.studio-hint{font-size:10px;line-height:1.65;margin:8px 0 0;color:var(--fg-muted)}.studio-advanced{font-size:10px;margin-top:8px;color:var(--fg-muted)}.studio-advanced summary{cursor:pointer;padding:3px 0}.studio-advanced[open]{padding-bottom:2px}
.studio-preview{margin-top:14px}.studio-foot{display:grid;gap:3px;flex:none;margin:0;padding:9px 12px;border-top:1px solid var(--border-soft);font-size:9px;line-height:1.5}.studio-panel button:focus-visible,.studio-panel input:focus-visible,.studio-panel summary:focus-visible{outline:2px solid var(--brand);outline-offset:3px}
@media(max-height:540px){.studio-panel{max-height:calc(100dvh - 88px)}.studio-head{padding-top:10px;padding-bottom:10px}.studio-foot{padding-top:6px;padding-bottom:6px}}
@media(prefers-reduced-motion:reduce){.studio-toggle span,.studio-toggle{transition:none}}
</style>
