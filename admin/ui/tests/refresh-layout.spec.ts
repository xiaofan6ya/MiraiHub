import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { createAppRouter } from '@/router'
import KpiCard from '@/components/data/KpiCard.vue'
import Card from '@/components/base/Card.vue'
import DynamicIsland from '@/components/feedback/DynamicIsland.vue'
import { createIslandQueue } from '@/components/feedback/islandQueue'

afterEach(() => { document.body.innerHTML = ''; vi.useRealTimers() })
describe('Stable refresh and adaptive loading', () => {
  it('preserves viewport scroll for a query change on the same page', async () => {
    const router = createAppRouter()
    const result = await router.options.scrollBehavior?.({ path: '/overview' } as any, { path: '/overview' } as any, null)
    console.log('OBSERVED same_page_scroll=' + JSON.stringify(result))
    expect(result).toBe(false)
  })
  it('still restores browser history and starts a different page at the top', async () => {
    const router = createAppRouter()
    const restore = { top: 720, left: 0 }
    expect(await router.options.scrollBehavior?.({ path: '/overview' } as any, { path: '/keys' } as any, restore)).toEqual(restore)
    expect(await router.options.scrollBehavior?.({ path: '/keys' } as any, { path: '/overview' } as any, null)).toEqual({ top: 0 })
  })
  it('updates a live KPI in the same DOM node instead of fading through an empty state', async () => {
    const wrapper = mount(KpiCard, { props: { label: 'Requests', value: 100 } })
    const original = wrapper.find('.mh-kpi__num').element
    await wrapper.setProps({ value: 101, loading: true })
    const stable = wrapper.find('.mh-kpi__num').element === original
    console.log('OBSERVED stable_kpi_node=' + stable)
    wrapper.unmount()
    expect(stable).toBe(true)
  })
  it('does not insert a new header when an untitled card refreshes', async () => {
    const wrapper = mount(Card, { slots: { default: '<p>Retained content</p>' } })
    const original = wrapper.find('p').element
    await wrapper.setProps({ loading: true })
    const inserted = wrapper.find('.mh-card__head').exists()
    console.log('OBSERVED refresh_inserts_header=' + inserted)
    expect(wrapper.find('p').element).toBe(original)
    wrapper.unmount()
    expect(inserted).toBe(false)
  })
  it('draws the idle SVG signal as a repeatable stroke animation', async () => {
    const queue = createIslandQueue()
    const wrapper = mount(DynamicIsland, { props: { queue, persistent: true }, attachTo: document.body })
    await nextTick()
    const signal = document.querySelector('.island-signal__trace')
    console.log('OBSERVED animated_signal=' + Boolean(signal))
    const length = signal?.getAttribute('pathLength')
    wrapper.unmount(); queue.dispose()
    expect(length).toBe('1')
  })
  it('derives skeleton rectangles from real element geometry and never stores labels', async () => {
    const modulePath = '../src/composables/useLayoutSkeleton.ts'
    const layout = await import(/* @vite-ignore */ modulePath).catch(() => null)
    console.log('OBSERVED adaptive_skeleton=' + Boolean(layout?.captureLayout))
    expect(layout?.captureLayout).toBeTypeOf('function')
    if (!layout) return
    const host = document.createElement('main')
    host.innerHTML = '<article class="mh-page"><section class="mh-card"><h2>PRIVATE CONTENT MUST NOT BE CACHED</h2></section></article>'
    document.body.append(host)
    for (const [index, el] of [host, ...host.querySelectorAll('*')].entries()) {
      el.getBoundingClientRect = () => ({ x: 0, y: index * 30, left: 0, top: index * 30, width: 600, height: index ? 80 : 500, right: 600, bottom: index * 30 + (index ? 80 : 500), toJSON: () => ({}) })
    }
    const snapshot = layout.captureLayout(host)
    expect(snapshot.rects.length).toBeGreaterThan(0)
    expect(JSON.stringify(snapshot)).not.toContain('PRIVATE')
    expect(snapshot.rects.some((rect: any) => rect.y === 90)).toBe(true)
  })
})
