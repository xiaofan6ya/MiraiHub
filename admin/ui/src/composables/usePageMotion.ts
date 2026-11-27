import type { ObjectDirective } from 'vue'
import { appearance } from '@/theme/useAppearance'

const reduced = () => appearance.value.motion === 'off' || window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
const arrivalIds = new Set(['mh-page-arrival', 'mh-section-arrival'])
const entered = new WeakSet<Element>()
const revealed = new WeakSet<HTMLElement>()
const loading = new WeakMap<HTMLElement, boolean>()
const blocked = new WeakMap<HTMLElement, boolean>()
const isBlocked = (node: Element) => loading.get(node as HTMLElement) === true || Boolean(node.closest('[data-motion-loading="true"]') || node.querySelector('[aria-busy="true"], .mh-skeleton, .n-skeleton'))

/** Mount already starts finite CSS drawings. Only restart settled artwork, never its active first run. */
export function replayVisuals(root: Element): void {
  if (reduced() || isBlocked(root)) return
  root.getAnimations?.({ subtree: true }).forEach(animation => {
    if (appearance.value.motion === 'once' || arrivalIds.has(animation.id) || animation.playState === 'running' || animation.pending || animation.effect?.getTiming().iterations === Infinity) return
    animation.cancel()
    animation.play()
  })
  const counters = [...root.querySelectorAll('[data-count-up]')]
  if (root.matches('[data-count-up]')) counters.push(root)
  counters.forEach(node => { if (!isBlocked(node)) node.dispatchEvent(new Event('mh:motion-replay')) })
}

/** Card directives own data-dependent arrivals; route transitions only animate static page chrome. */
export function enterPage(el: Element, done: () => void): void {
  if (!(el instanceof HTMLElement) || typeof el.animate !== 'function' || reduced() || (appearance.value.motion === 'once' && entered.has(el))) { done(); return }
  entered.add(el)
  const cards = [...el.querySelectorAll<HTMLElement>('[data-motion-reveal]')]
  if (el.hasAttribute('data-motion-reveal')) cards.push(el)
  cards.forEach(enqueue)
  const nodes = Array.from(el.children).filter((node): node is HTMLElement => {
    const box = node.getBoundingClientRect()
    return node instanceof HTMLElement && !node.hasAttribute('data-motion-reveal') && !node.querySelector('[data-motion-reveal]') && !isBlocked(node) && box.top < window.innerHeight && box.bottom > 0
  })
  if (!nodes.length) { done(); return }
  let remaining = nodes.length
  for (const [index, node] of nodes.entries()) {
    replayVisuals(node)
    ;(node.getAnimations?.() ?? []).filter(animation => arrivalIds.has(animation.id)).forEach(animation => animation.cancel())
    node.dataset.motionEntering = 'true'
    const animation = node.animate([{ opacity: 0, transform: 'translate3d(0,12px,0)' }, { opacity: 1, transform: 'none' }], { duration: 460, delay: Math.min(index, 5) * 42, easing: 'cubic-bezier(.2,.8,.2,1)', fill: 'backwards' })
    animation.id = 'mh-page-arrival'
    let settled = false
    const settle = () => { if (settled) return; settled = true; delete node.dataset.motionEntering; if (--remaining === 0) done() }
    animation.onfinish = settle
    animation.oncancel = settle
  }
}

let observer: IntersectionObserver | null = null
const observed = new Set<HTMLElement>()
const visible = new WeakMap<HTMLElement, boolean>()
const pending = new Set<HTMLElement>()
let frame = 0
function enqueue(node: HTMLElement) {
  pending.add(node)
  if (!frame) frame = requestAnimationFrame(revealBatch)
}
function revealBatch() {
  frame = 0
  const nodes = [...pending]; pending.clear()
  let index = 0
  for (const node of nodes) {
    const box = node.getBoundingClientRect()
    const busy = isBlocked(node)
    blocked.set(node, busy)
    if (!node.isConnected || busy || box.top >= window.innerHeight || box.bottom <= 0 || node.closest('[data-motion-entering]') || reduced() || typeof node.animate !== 'function' || (appearance.value.motion === 'once' && revealed.has(node))) continue
    const animations = node.getAnimations?.() ?? []
    if (animations.some(animation => arrivalIds.has(animation.id) && animation.playState === 'running')) continue
    revealed.add(node)
    replayVisuals(node)
    const animation = node.animate([{ opacity: 0, transform: 'translate3d(0,10px,0)' }, { opacity: 1, transform: 'none' }], { duration: 480, delay: Math.min(index++, 5) * 38, easing: 'cubic-bezier(.2,.8,.2,1)', fill: 'backwards' })
    animation.id = 'mh-section-arrival'
  }
}
function syncLoading(node: HTMLElement, value?: boolean) {
  loading.set(node, value === true)
  if (value) node.dataset.motionLoading = 'true'
  else delete node.dataset.motionLoading
}

/** A placeholder never consumes an entrance. The first ready render and each completed refresh do. */
export const vReveal: ObjectDirective<HTMLElement, boolean | undefined> = {
  mounted(node, binding) {
    node.dataset.motionReveal = ''
    syncLoading(node, binding.value)
    blocked.set(node, isBlocked(node))
    if (typeof IntersectionObserver !== 'function' || typeof requestAnimationFrame !== 'function') return
    observer ??= new IntersectionObserver(entries => {
      for (const entry of entries) {
        const target = entry.target as HTMLElement
        if (!observed.has(target)) continue
        const wasVisible = visible.get(target)
        visible.set(target, entry.isIntersecting)
        if (entry.isIntersecting && !wasVisible) enqueue(target)
      }
    }, { threshold: .08 })
    observed.add(node); observer.observe(node)
  },
  updated(node, binding) {
    const wasBlocked = blocked.get(node)
    syncLoading(node, binding.value)
    const busy = isBlocked(node)
    blocked.set(node, busy)
    if (!busy && (wasBlocked || binding.oldValue === true && binding.value === false)) enqueue(node)
  },
  beforeUnmount(node) {
    observer?.unobserve(node); observed.delete(node); visible.delete(node); pending.delete(node); loading.delete(node); blocked.delete(node)
    if (!observed.size) { observer?.disconnect(); observer = null }
    if (!pending.size && frame) { cancelAnimationFrame(frame); frame = 0 }
  },
}
