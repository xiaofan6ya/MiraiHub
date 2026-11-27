import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, nextTick, ref } from 'vue'
import { mount, type VueWrapper } from '@vue/test-utils'
import { enterPage, vReveal } from '@/composables/usePageMotion'
import Hero from '@/pages/Overview/OverviewHero.vue'
import LoginPage from '@/login/LoginPage.vue'

vi.mock('@/api/http', () => ({ http: vi.fn(() => Promise.resolve({ version: '2.0.0' })), ApiError: class extends Error {} }))
vi.mock('@/api/auth', () => ({ setUnauthorizedHandler: vi.fn() }))
vi.mock('@/components', () => ({
  Card: defineComponent({ template: '<section><slot/><slot name="extra"/></section>' }),
  Badge: defineComponent({ template: '<span><slot/></span>' }),
  IconBtn: defineComponent({ template: '<button />' }),
  RouteRibbon: defineComponent({ template: '<div />' }),
}))

let frames = new Map<number, FrameRequestCallback>()
let seq = 0
let time = 0
let callbacks: IntersectionObserverCallback[] = []
let observed = new Set<Element>()
let wrappers: VueWrapper[] = []
const animate = vi.fn(() => ({ id: '', onfinish: null, oncancel: null, cancel: vi.fn(), play: vi.fn() }))
function tick(ms = 16) { time += ms; const batch = [...frames.values()]; frames.clear(); batch.forEach(fn => fn(time)) }
function visible(node: Element, isIntersecting: boolean) {
  if (observed.has(node)) callbacks.forEach(fn => fn([{ target: node, isIntersecting } as IntersectionObserverEntry], {} as IntersectionObserver))
  tick()
}
function hero() {
  const wrapper = mount(Hero, { props: { title: 'Traffic', scope: 'All', rangeLabel: 'Today', lanes: [], total: 128402, baseUrl: '/v1', replayKey: 0 }, global: { stubs: { TrafficTrend: true, OrbitScene: true } } })
  wrappers.push(wrapper)
  return wrapper
}
beforeEach(() => {
  frames = new Map(); seq = 0; time = 0; callbacks = []; observed = new Set(); animate.mockClear()
  vi.spyOn(performance, 'now').mockImplementation(() => time)
  vi.stubGlobal('requestAnimationFrame', (fn: FrameRequestCallback) => { frames.set(++seq, fn); return seq })
  vi.stubGlobal('cancelAnimationFrame', (id: number) => frames.delete(id))
  vi.stubGlobal('IntersectionObserver', class {
    constructor(fn: IntersectionObserverCallback) { callbacks.push(fn) }
    observe(node: Element) { observed.add(node) } unobserve(node: Element) { observed.delete(node) } disconnect() { observed.clear() }
  })
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ top: 10, bottom: 100, left: 0, right: 200, width: 200, height: 90 } as DOMRect)
  Object.defineProperty(HTMLElement.prototype, 'animate', { configurable: true, value: animate })
  Object.defineProperty(HTMLElement.prototype, 'getAnimations', { configurable: true, value: () => [] })
})
afterEach(() => { wrappers.forEach(w => w.unmount()); wrappers = []; vi.restoreAllMocks(); vi.unstubAllGlobals(); document.body.innerHTML = '' })

describe('Login and repeatable system motion', () => {
  it('replays a card after leaving and re-entering the viewport', () => {
    const w = mount({ directives: { reveal: vReveal }, template: '<section v-reveal />' }, { attachTo: document.body }); wrappers.push(w)
    visible(w.element, true); const first = animate.mock.calls.length
    visible(w.element, false); visible(w.element, true)
    console.log(`OBSERVED reveal first=${first} reentered=${animate.mock.calls.length}`)
    expect(animate.mock.calls.length).toBe(first + 1)
  })
  it('replays completed refreshes even if the data did not change', async () => {
    const busy = ref(false)
    const w = mount({ setup: () => ({ busy }), directives: { reveal: vReveal }, template: '<section v-reveal="busy" />' }, { attachTo: document.body }); wrappers.push(w)
    visible(w.element, true); animate.mockClear()
    for (let i = 0; i < 2; i++) { busy.value = true; await nextTick(); busy.value = false; await nextTick(); tick() }
    console.log(`OBSERVED completed-refresh-replays=${animate.mock.calls.length}`)
    expect(animate.mock.calls.length).toBe(2)
  })
  it('animates all visible cards rather than dropping the seventh onward', () => {
    const w = mount({ directives: { reveal: vReveal }, template: '<main><section v-for="n in 9" :key="n" v-reveal /></main>' }, { attachTo: document.body }); wrappers.push(w)
    const nodes = w.findAll('section').map(n => n.element)
    callbacks.forEach(fn => fn(nodes.map(target => ({ target, isIntersecting: true } as unknown as IntersectionObserverEntry)), {} as IntersectionObserver)); tick()
    console.log(`OBSERVED visible-card-animations=${animate.mock.calls.length}`)
    expect(animate.mock.calls.length).toBe(9)
  })
  it('counts the overview total through intermediate numbers and lands exactly', async () => {
    const w = hero(); const first = w.find('.ov-hero__number').text(); tick(220); await nextTick(); const middle = w.find('.ov-hero__number').text(); tick(1200); await nextTick(); const end = w.find('.ov-hero__number').text()
    console.log(`OBSERVED count-up=${first}|${middle}|${end}`)
    expect(first).toBe('0'); expect(Number(middle.replaceAll(',', ''))).toBeGreaterThan(0); expect(Number(middle.replaceAll(',', ''))).toBeLessThan(128402); expect(end).toBe('128,402')
  })
  it('replays equal totals on each explicit refresh key', async () => {
    const w = hero(); tick(1500); await nextTick()
    await w.setProps({ replayKey: 1 }); const first = w.find('.ov-hero__number').text(); tick(1500); await nextTick()
    await w.setProps({ replayKey: 2 }); const second = w.find('.ov-hero__number').text(); tick(1500); await nextTick()
    console.log(`OBSERVED same-total-replay=${first}|${second}|${w.find('.ov-hero__number').text()}`)
    expect(first).toBe('0'); expect(second).toBe('0'); expect(w.find('.ov-hero__number').text()).toBe('128,402')
  })
  it('replays route arrivals on every return', () => {
    const root = document.createElement('main'); root.innerHTML = '<section></section><section></section>'; document.body.append(root)
    enterPage(root, () => {}); const first = animate.mock.calls.length; enterPage(root, () => {})
    console.log(`OBSERVED route-arrival=${first}|${animate.mock.calls.length}`)
    expect(first).toBeGreaterThan(0); expect(animate.mock.calls.length).toBe(first * 2)
  })
  it('restarts a settled counter when the existing card is revealed again', async () => {
    const w = hero(); tick(1500); await nextTick()
    w.find('.ov-hero__number').element.dispatchEvent(new Event('mh:motion-replay')); await nextTick()
    const restarted = w.find('.ov-hero__number').text(); tick(1500); await nextTick()
    console.log(`OBSERVED card-counter-replay=${restarted}|${w.find('.ov-hero__number').text()}`)
    expect(restarted).toBe('0'); expect(w.find('.ov-hero__number').text()).toBe('128,402')
  })
  it('honors reduced motion and never delays the final number', async () => {
    vi.spyOn(window, 'matchMedia').mockImplementation(() => ({matches:true}) as MediaQueryList)
    const w = hero(); await nextTick()
    console.log(`OBSERVED reduced-motion-count=${w.find('.ov-hero__number').text()}`)
    expect(w.find('.ov-hero__number').text()).toBe('128,402'); expect(frames.size).toBe(0)
  })
  it('cancels a pending count-up when the hero is removed', () => {
    const w = hero(); expect(frames.size).toBe(1); w.unmount(); wrappers=[]
    console.log(`OBSERVED unmount-pending-frames=${frames.size}`)
    expect(frames.size).toBe(0)
  })
  it('decorates the login with a dedicated SVG scene and keeps native login fields', () => {
    const w = mount(LoginPage); wrappers.push(w)
    console.log(`OBSERVED login-orbital-scene=${w.find('.login__constellation').exists()}`)
    expect(w.find('.login__constellation').exists()).toBe(true)
    expect(w.find('input[autocomplete=username]').exists()).toBe(true); expect(w.find('input[autocomplete=current-password]').exists()).toBe(true)
  })
})
