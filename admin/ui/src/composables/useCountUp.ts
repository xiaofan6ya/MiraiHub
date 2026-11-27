import { onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, watch, type Ref } from 'vue'
import { appearance } from '@/theme/useAppearance'

/** One exact count-up per ready revision; lifecycle and reveal requests join the active run. */
export function useCountUp(element: Ref<HTMLElement | null>, target: () => number, replayKey: () => unknown, ready: () => boolean = () => true) {
  const value = ref(0)
  let frame = 0
  let inactive = false
  let played = false
  let media: MediaQueryList | undefined
  let bound: HTMLElement | null = null
  const finalValue = () => Math.max(0, Math.round(Number.isFinite(target()) ? target() : 0))
  function cancel() { if (frame) cancelAnimationFrame(frame); frame = 0 }
  function settle() { cancel(); value.value = finalValue() }
  function replay() {
    const end = finalValue()
    if (!ready() || inactive || document.hidden || appearance.value.motion === 'off' || media?.matches || typeof requestAnimationFrame !== 'function' || end === 0) { settle(); return }
    // mount, activated, updated props and v-reveal can request the same run in one paint.
    if (frame) return
    if (appearance.value.motion === 'once' && played) { settle(); return }
    played = true
    value.value = 0
    const start = performance.now()
    const step = (now: number) => {
      const progress = Math.min(1, Math.max(0, (now - start) / 1100))
      value.value = progress === 1 ? end : Math.floor(end * (1 - (1 - progress) ** 3))
      frame = progress < 1 ? requestAnimationFrame(step) : 0
    }
    frame = requestAnimationFrame(step)
  }
  function visibility() { if (document.hidden) settle(); else replay() }
  function bind() {
    if (bound === element.value) return
    bound?.removeEventListener('mh:motion-replay', replay)
    bound = element.value
    bound?.addEventListener('mh:motion-replay', replay)
  }
  watch([target, replayKey, ready, element], (next, previous) => {
    bind()
    // Background data updates settle at their new value; explicit revisions own refresh playback.
    if (played && previous && next[1] === previous[1] && next[2] && previous[2] && next[3] === previous[3]) settle()
    else replay()
  }, { flush: 'post' })
  watch(() => appearance.value.motion, () => { if (appearance.value.motion === 'off') settle() }, { flush: 'sync' })
  onMounted(() => {
    media = window.matchMedia?.('(prefers-reduced-motion: reduce)')
    media?.addEventListener?.('change', replay)
    bind()
    document.addEventListener('visibilitychange', visibility)
    replay()
  })
  onActivated(() => { inactive = false; replay() })
  onDeactivated(() => { inactive = true; settle() })
  onBeforeUnmount(() => {
    cancel()
    bound?.removeEventListener('mh:motion-replay', replay)
    media?.removeEventListener?.('change', replay)
    document.removeEventListener('visibilitychange', visibility)
  })
  return value
}
