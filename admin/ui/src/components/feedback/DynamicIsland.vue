<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import { Check, ChevronDown, CircleAlert, Info, LoaderCircle, TriangleAlert, X } from 'lucide-vue-next'
import { useI18n } from '@/i18n'
import type { IslandQueue } from './islandQueue'

const props = withDefaults(defineProps<{
  queue: IslandQueue; persistent?: boolean; title?: string; status?: string; statusTone?: string
}>(), { persistent: false, title: '', status: '', statusTone: 'ok' })
const { t } = useI18n()
const active = props.queue.current
const history = props.queue.history
const lifetime = props.queue.lifetime
const surface = ref<HTMLElement | null>(null)
const toggleButton = ref<HTMLButtonElement | null>(null)
const expanded = ref(false)
const panelRevision = ref(0)
const visible = ref(true)
const panelId = useId()
const icons = { success: Check, error: CircleAlert, warning: TriangleAlert, info: Info, loading: LoaderCircle }
const pending = computed(() => Math.max(0, props.queue.items.value.length - 1))
const progressStyle = computed(() => ({
  animationDuration: lifetime.value.remaining + 'ms',
  animationPlayState: lifetime.value.running ? 'running' : 'paused',
  '--remaining': String(lifetime.value.total ? lifetime.value.remaining / lifetime.value.total : 0),
}))
function setExpanded(value: boolean) {
  if (!value && surface.value?.contains(document.activeElement) && document.activeElement !== toggleButton.value) toggleButton.value?.focus({ preventScroll: true })
  if (expanded.value && !value) panelRevision.value++
  expanded.value = value
  // Details and keyboard focus do not suspend a finite notification.
  props.queue.resume('hover')
}
function toggle() { setExpanded(!expanded.value) }
watch(() => active.value?.id, (id, previous) => {
  if (previous !== undefined || id === undefined) setExpanded(false)
})
function visibility() {
  visible.value = !document.hidden
  if (document.hidden) props.queue.pause('hidden')
  else props.queue.resume('hidden')
}
function onOutside(event: Event) {
  const target = event.target as HTMLElement | null
  if (target && !surface.value?.contains(target) && !target.closest('.n-popover, .n-base-select-menu, .n-modal, [data-island-popup]')) setExpanded(false)
}
function escape(event: KeyboardEvent) {
  if (event.key !== 'Escape') return
  if (expanded.value) { setExpanded(false); event.stopPropagation() }
  else if (active.value?.closable) props.queue.dismiss(active.value.id)
}
function hover() {
  if (!expanded.value && active.value?.keepAliveOnHover) props.queue.pause('hover')
}
watch(() => active.value?.id, async () => {
  await nextTick()
  if (surface.value?.matches(':hover')) hover()
})
onMounted(() => {
  document.addEventListener('visibilitychange', visibility)
  document.addEventListener('pointerdown', onOutside)
  document.addEventListener('keydown', escape)
  visibility()
})
onBeforeUnmount(() => {
  document.removeEventListener('visibilitychange', visibility)
  document.removeEventListener('pointerdown', onOutside)
  document.removeEventListener('keydown', escape)
  props.queue.resume('hover')
})
</script>

<template>
  <Teleport to="body" :disabled="persistent">
    <div class="island-anchor" :class="{ 'is-docked': persistent }">
      <Transition name="island-arrive">
        <section v-if="persistent || active" ref="surface" class="dynamic-island"
          :class="['is-' + (active?.tone || statusTone), { 'is-expanded': expanded, 'has-notification': !!active, 'is-persistent': persistent }]"
          :aria-label="t(persistent ? 'island.workspace' : 'island.notification')" data-testid="dynamic-island"
          @mouseenter="hover" @mouseleave="queue.resume('hover')">
          <div class="island-compact">
            <button ref="toggleButton" type="button" class="island-toggle island-expand" :aria-label="t(expanded ? 'island.collapse' : 'island.expand')"
              :aria-expanded="expanded" :aria-controls="panelId" @click="toggle">
              <span class="island-symbol" aria-hidden="true"><component v-if="active" :is="icons[active.tone]" :size="17" :class="{ 'island-spinner': active.tone === 'loading' }" /><svg v-else class="island-signal" :class="{ 'is-paused': !visible }" viewBox="0 0 24 24" width="19" height="19" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path class="island-signal__track" d="M2 12h4l3-7 4 14 3-7h6" /><path class="island-signal__trace" pathLength="1" d="M2 12h4l3-7 4 14 3-7h6" /></svg></span>
              <Transition name="island-copy" mode="out-in">
                <span :key="active?.id ?? 'idle'" class="island-summary">
                  <span v-if="active" class="island-message" :role="active.tone === 'error' ? 'alert' : 'status'" aria-atomic="true">{{ active.text }}<span v-if="active.count > 1"> ×{{ active.count }}</span></span>
                  <template v-else><span class="island-title">{{ title || t('island.workspace') }}</span><span class="island-idle-status"><i :class="'tone-' + statusTone" />{{ status || t('island.ready') }}</span></template>
                </span>
              </Transition>
              <span v-if="pending" class="island-pending" :aria-label="t('island.pending', { count: pending })">+{{ pending }}</span>
              <ChevronDown :size="14" class="island-chevron" aria-hidden="true" />
            </button>
            <button v-if="active?.closable" type="button" class="island-control island-dismiss" :aria-label="t('common.close')" @click="queue.dismiss(active.id)"><X :size="15" /></button>
          </div>
          <div :id="panelId" class="island-panel" :inert="!expanded" :aria-hidden="!expanded">
            <div class="island-panel-clip"><div class="island-panel-content">
              <div v-if="active" class="island-notice-detail"><span class="island-detail-label">{{ t('island.notification') }}</span><p>{{ active.text }}</p><span v-if="active.duration > 0" class="island-auto-hint">{{ t('island.autoReturn') }}</span></div>
              <div :key="panelRevision"><slot name="panel" :close="() => setExpanded(false)" /></div>
              <div v-if="persistent" class="island-recent">
                <div class="island-recent-head"><span>{{ t('island.recent') }}</span><span>{{ history.length }}/20</span></div>
                <ul v-if="history.length"><li v-for="item in history.slice(0, 5)" :key="item.id"><component :is="icons[item.tone]" :size="14" aria-hidden="true" /><span>{{ item.text }}</span><b v-if="item.count > 1">×{{ item.count }}</b></li></ul>
                <p v-else class="island-empty">{{ t('island.noNotifications') }}</p>
              </div>
            </div></div>
          </div>
          <span v-if="active && active.duration > 0" :key="lifetime.version" class="island-lifetime" :style="progressStyle" aria-hidden="true" />
        </section>
      </Transition>
    </div>
  </Teleport>
</template>

<style scoped>
.island-anchor{position:fixed;z-index:4500;top:calc(var(--topbar-h,68px) + 12px);left:0;right:0;display:flex;justify-content:center;padding:0 12px;pointer-events:none}
.island-anchor.is-docked{position:absolute;top:10px;z-index:50}
.dynamic-island{--island-tone:var(--brand);position:relative;width:284px;max-width:100%;overflow:hidden;pointer-events:auto;border:1px solid color-mix(in srgb,var(--brand) 24%,var(--border));border-radius:26px;background:color-mix(in srgb,var(--surface-raised,var(--surface)) 96%,var(--brand));color:var(--fg);box-shadow:var(--shadow-pop),inset 0 1px 0 color-mix(in srgb,var(--fg) 6%,transparent);isolation:isolate;transform-origin:50% 0;transition:width 550ms cubic-bezier(.22,1.16,.36,1),border-radius 450ms ease,background 200ms ease}
.dynamic-island.has-notification{width:400px}.dynamic-island.is-expanded{width:488px;border-radius:28px}
.island-compact{position:relative;display:flex;align-items:center;min-height:46px;padding:0 8px}
.island-toggle{display:flex;align-items:center;gap:10px;flex:1;min-width:0;min-height:46px;padding:0 8px;border:0;color:inherit;background:transparent;font:inherit;text-align:left;cursor:pointer;border-radius:24px;-webkit-app-region:no-drag}
.island-symbol{flex:none;display:grid;place-items:center;width:28px;height:28px;border-radius:50%;background:color-mix(in srgb,var(--island-tone) 12%,transparent);color:var(--island-tone)}
.island-signal__track{opacity:.2}.island-signal__trace{stroke-dasharray:1;animation:island-signal-draw 2800ms cubic-bezier(.4,0,.2,1) infinite}.island-signal.is-paused .island-signal__trace{animation-play-state:paused}@keyframes island-signal-draw{0%{stroke-dashoffset:1}45%,65%{stroke-dashoffset:0}100%{stroke-dashoffset:-1}}@media(prefers-reduced-motion:reduce){.island-signal__trace{animation:none;stroke-dashoffset:0}}
.is-success{--island-tone:var(--ok)}.is-error,.is-danger{--island-tone:var(--danger)}.is-warning,.is-warn{--island-tone:var(--warn)}
.island-summary{display:flex;flex:1;align-items:center;gap:12px;min-width:0;font-size:12px;line-height:20px;font-weight:500}.island-title,.island-message{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}.island-title{flex:1}.island-message{flex:1}.island-idle-status{display:flex;align-items:center;gap:5px;flex:none;font-size:10px;color:var(--fg-muted)}
.island-idle-status i{width:5px;height:5px;border-radius:50%;background:var(--ok)}.island-idle-status .tone-warn{background:var(--warn)}.island-idle-status .tone-danger{background:var(--danger)}
.island-chevron{flex:none;color:var(--fg-muted);transition:transform 450ms cubic-bezier(.22,1.16,.36,1)}.is-expanded .island-chevron{transform:rotate(180deg)}
.island-control{display:grid;place-items:center;flex:none;width:28px;height:28px;padding:0;border:0;border-radius:50%;background:transparent;color:var(--fg-muted);cursor:pointer}.island-control:hover{background:var(--surface-hover);color:var(--fg)}.island-control:focus-visible,.island-toggle:focus-visible{outline:2px solid var(--brand);outline-offset:-2px}
.island-pending{font-size:10px;color:var(--fg-2);background:var(--surface-2);padding:1px 5px;border-radius:10px;white-space:nowrap}
.island-panel{display:grid;grid-template-rows:0fr;visibility:hidden;opacity:0;transition:grid-template-rows 550ms cubic-bezier(.22,1.1,.36,1),opacity 180ms ease,visibility 550ms}
.is-expanded .island-panel{grid-template-rows:1fr;visibility:visible;opacity:1;transition:grid-template-rows 550ms cubic-bezier(.22,1.1,.36,1),opacity 250ms 100ms ease,visibility 0ms}
.island-panel-clip{overflow:hidden;min-height:0}.island-panel-content{padding:0 20px 18px;max-height:min(530px,calc(100dvh - 100px));overflow-y:auto;overscroll-behavior:contain;transform:translateY(-6px);transition:transform 450ms cubic-bezier(.22,1.1,.36,1)}.is-expanded .island-panel-content{transform:none}
.island-notice-detail{border-top:1px solid var(--border-soft);padding:15px 0}.island-detail-label,.island-auto-hint{font-size:10px;color:var(--fg-muted)}.island-notice-detail p{margin:7px 0;font-size:13px;line-height:1.8;white-space:pre-wrap;overflow-wrap:anywhere}
.island-recent{margin-top:15px;border-top:1px solid var(--border-soft);padding-top:14px}.island-recent-head{display:flex;justify-content:space-between;color:var(--fg-muted);font-size:10px}.island-recent ul{list-style:none;margin:10px 0 0;padding:0;display:grid;gap:9px}.island-recent li{display:flex;align-items:flex-start;gap:8px;color:var(--fg-2);font-size:11px;line-height:18px}.island-recent li svg{flex:none;margin-top:2px;color:var(--brand)}.island-recent li span{flex:1;min-width:0;overflow-wrap:anywhere;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}.island-recent li b{font-size:10px;font-weight:500}.island-empty{font-size:11px;margin:11px 0 0;color:var(--fg-muted)}
.island-lifetime{position:absolute;bottom:0;left:20px;right:20px;height:2px;background:var(--island-tone);opacity:.6;transform-origin:left;animation:island-countdown linear forwards}
.island-arrive-enter-active{animation:island-arrive 550ms cubic-bezier(.22,1.16,.36,1) both}.island-arrive-leave-active{transition:opacity 150ms,transform 250ms}.island-arrive-leave-to{opacity:0;transform:translateY(-8px) scale(.93)}
.island-copy-enter-active{transition:opacity 150ms,transform 250ms}.island-copy-leave-active{transition:opacity 80ms}.island-copy-enter-from{opacity:0;transform:translateY(6px)}.island-copy-leave-to{opacity:0}
.island-spinner{animation:island-spin 1s linear infinite}@keyframes island-spin{to{transform:rotate(360deg)}}@keyframes island-countdown{from{transform:scaleX(var(--remaining,1))}to{transform:scaleX(0)}}@keyframes island-arrive{from{opacity:0;transform:translateY(-12px) scale(.85,.8)}to{opacity:1;transform:none}}
@media(max-width:767px){.island-anchor.is-docked{top:7px}.dynamic-island{width:262px}.dynamic-island.has-notification{width:340px}.dynamic-island.is-expanded{width:488px}.island-panel-content{padding-left:16px;padding-right:16px}}
@media(prefers-reduced-motion:reduce){.dynamic-island,.island-panel,.island-panel-content,.island-chevron,.island-copy-enter-active,.island-copy-leave-active,.island-arrive-leave-active{transition:none}.island-arrive-enter-active,.island-spinner{animation:none}.island-lifetime{display:none}}
</style>
