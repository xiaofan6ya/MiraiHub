import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { vReveal } from '@/composables/usePageMotion'

let wrapper: VueWrapper | undefined
let callbacks: IntersectionObserverCallback[]
let observers: Array<{ observe: ReturnType<typeof vi.fn>; unobserve: ReturnType<typeof vi.fn>; disconnect: ReturnType<typeof vi.fn> }>
let frames: Map<number, FrameRequestCallback>
let reduced: boolean
const originalAnimate = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'animate')

beforeEach(() => {
  callbacks = []; observers = []; frames = new Map(); reduced = false
  vi.stubGlobal('IntersectionObserver', class {
    observe = vi.fn(); unobserve = vi.fn(); disconnect = vi.fn()
    constructor(callback: IntersectionObserverCallback) { callbacks.push(callback); observers.push(this) }
  })
  vi.stubGlobal('requestAnimationFrame', vi.fn((callback: FrameRequestCallback) => { frames.set(1, callback); return 1 }))
  vi.stubGlobal('cancelAnimationFrame', vi.fn((id: number) => { frames.delete(id) }))
  vi.spyOn(window, 'matchMedia').mockImplementation(() => ({ matches: reduced }) as MediaQueryList)
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ top: 10, bottom: 100, width: 200, height: 90 } as DOMRect)
  Object.defineProperty(HTMLElement.prototype, 'animate', { configurable: true, value: vi.fn(() => ({ id: '' }) as Animation) })
})
afterEach(() => {
  wrapper?.unmount(); wrapper = undefined; vi.restoreAllMocks(); vi.unstubAllGlobals()
  if (originalAnimate) Object.defineProperty(HTMLElement.prototype, 'animate', originalAnimate)
  else Reflect.deleteProperty(HTMLElement.prototype, 'animate')
})

function render(count = 8): void {
  wrapper = mount({ directives: { reveal: vReveal }, template: `<div><section v-for="n in ${count}" :key="n" v-reveal /></div>` }, { attachTo: document.body })
}
function intersect(): void {
  const entries: IntersectionObserverEntry[] = wrapper!.findAll('section').map(node => {
    const target = node.element as HTMLElement
    const rect = target.getBoundingClientRect()
    return { time: 0, isIntersecting: true, intersectionRatio: 1, target, boundingClientRect: rect, intersectionRect: rect, rootBounds: null }
  })
  callbacks[0]?.(entries, {} as IntersectionObserver)
}

describe('Repeatable card reveal lifecycle', () => {
  it('shares one observer and animates every visible card with a bounded stagger', () => {
    render(); expect(observers).toHaveLength(1); expect(observers[0]?.observe).toHaveBeenCalledTimes(8)
    intersect(); expect(requestAnimationFrame).toHaveBeenCalledTimes(1)
    frames.get(1)?.(0)
    expect(HTMLElement.prototype.animate).toHaveBeenCalledTimes(8)
    expect(observers[0]?.unobserve).not.toHaveBeenCalled()
    expect(observers[0]?.disconnect).not.toHaveBeenCalled()
    for (const call of vi.mocked(HTMLElement.prototype.animate).mock.calls) expect((call[1] as KeyframeAnimationOptions).delay).toBeLessThanOrEqual(190)
  })
  it('keeps observation available for a preference change but skips reduced-motion animation', () => {
    reduced = true; render()
    expect(observers).toHaveLength(1); intersect(); frames.get(1)?.(0)
    expect(HTMLElement.prototype.animate).not.toHaveBeenCalled()
  })
  it('cancels a queued frame and releases the observer on unmount', () => {
    render(2); intersect(); wrapper?.unmount(); wrapper = undefined
    expect(cancelAnimationFrame).toHaveBeenCalledWith(1)
    expect(frames.size).toBe(0)
    expect(observers[0]?.disconnect).toHaveBeenCalledTimes(1)
  })
})
