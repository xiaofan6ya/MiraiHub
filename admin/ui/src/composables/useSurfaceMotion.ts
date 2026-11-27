import { onBeforeUnmount, onMounted, type Ref } from 'vue'
import { appearance } from '@/theme/useAppearance'

/** One delegated listener, one queued frame, and one local light. Pointer movement never enters Vue state. */
export function useSurfaceMotion(root: Ref<HTMLElement | null>): void {
  let stop: (() => void) | undefined
  onMounted(() => {
    const host = root.value
    if (!host || typeof window.matchMedia !== 'function') return
    const fine = window.matchMedia('(hover: hover) and (pointer: fine)')
    const calm = window.matchMedia('(prefers-reduced-motion: reduce)')
    let surface: HTMLElement | null = null
    let light: HTMLElement | null = null
    let artwork: SVGElement | null = null
    let bounds: DOMRect | null = null
    let travelX = 5, travelY = 4
    const resized = typeof ResizeObserver === 'function' ? new ResizeObserver(() => { bounds = null }) : null
    let frame = 0
    let x = 0, y = 0
    const rest = () => {
      if (frame) cancelAnimationFrame(frame)
      frame = 0
      if (surface) {
        delete surface.dataset.surfaceActive
      }
      if (artwork) artwork.style.transform = ''
      resized?.disconnect()
      bounds = null
      surface = null
      light = null
      artwork = null
    }
    const paint = () => {
      frame = 0
      if (!surface?.isConnected) { rest(); return }
      const box = bounds ?? (bounds = surface.getBoundingClientRect())
      const localX = Math.max(0, Math.min(box.width, x - box.left))
      const localY = Math.max(0, Math.min(box.height, y - box.top))
      // Direct transforms avoid inherited custom-property invalidation, especially on large table cards.
      if (light) light.style.transform = `translate3d(${(localX - 150).toFixed(1)}px,${(localY - 150).toFixed(1)}px,0)`
      if (artwork) artwork.style.transform = `translate3d(${((localX / (box.width || 1) * 2 - 1) * travelX).toFixed(2)}px,${((localY / (box.height || 1) * 2 - 1) * travelY).toFixed(2)}px,0)`
      if (surface.dataset.surfaceActive !== 'true') surface.dataset.surfaceActive = 'true'
    }
    const follow = (event: PointerEvent) => {
      if (!fine.matches || calm.matches || appearance.value.motion === 'off' || document.hidden || event.pointerType === 'touch') { rest(); return }
      const next = event.target instanceof Element ? event.target.closest<HTMLElement>('.mh-kpi, .mh-card') : null
      if (next !== surface) {
        rest()
        surface = next && host.contains(next) ? next : null
        light = surface?.querySelector<HTMLElement>(':scope > .surface-sheen > .surface-sheen__light') ?? null
        artwork = surface?.querySelector<SVGElement>('.orbit-scene__parallax, .kpi-orbit-mark') ?? null
        travelX = artwork?.classList.contains('kpi-orbit-mark') ? 2 : 5
        travelY = artwork?.classList.contains('kpi-orbit-mark') ? 2 : 4
        if (surface) resized?.observe(surface)
      }
      if (!surface) return
      x = event.clientX; y = event.clientY
      if (!frame) frame = requestAnimationFrame(paint)
    }
    const sync = () => {
      host.removeEventListener('pointermove', follow)
      rest()
      if (fine.matches && !calm.matches && appearance.value.motion !== 'off' && !document.hidden) host.addEventListener('pointermove', follow, { passive: true })
    }
    fine.addEventListener?.('change', sync)
    calm.addEventListener?.('change', sync)
    document.addEventListener('visibilitychange', sync)
    document.addEventListener('mh:motion-preference', sync)
    host.addEventListener('pointerleave', rest)
    // A card can scroll out from under a stationary pointer.
    window.addEventListener('scroll', rest, { capture: true, passive: true })
    window.addEventListener('blur', rest)
    window.addEventListener('resize', rest, { passive: true })
    sync()
    stop = () => {
      host.removeEventListener('pointermove', follow)
      host.removeEventListener('pointerleave', rest)
      fine.removeEventListener?.('change', sync)
      calm.removeEventListener?.('change', sync)
      document.removeEventListener('visibilitychange', sync)
      document.removeEventListener('mh:motion-preference', sync)
      window.removeEventListener('scroll', rest, { capture: true })
      window.removeEventListener('blur', rest)
      window.removeEventListener('resize', rest)
      rest()
    }
  })
  onBeforeUnmount(() => stop?.())
}
