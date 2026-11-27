import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent, h, nextTick, ref } from 'vue'
import { layoutCacheKey, useLayoutSkeleton } from '@/composables/useLayoutSkeleton'
import DataTable from '@/components/data/DataTable.vue'

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); sessionStorage.clear(); document.body.innerHTML = '' })

describe('Adaptive loading lifecycle', () => {
  it('separates layouts by build, page, width and language', () => {
    const original = layoutCacheKey('/overview', 1000, 'zh-CN', 'build-a')
    expect(new Set([
      original,
      layoutCacheKey('/keys', 1000, 'zh-CN', 'build-a'),
      layoutCacheKey('/overview', 390, 'zh-CN', 'build-a'),
      layoutCacheKey('/overview', 1000, 'en', 'build-a'),
      layoutCacheKey('/overview', 1000, 'zh-CN', 'build-b'),
    ]).size).toBe(5)
  })

  it('stops treating later inline loading as a page load and cleans up observers', async () => {
    vi.useFakeTimers()
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ x: 0, y: 0, left: 0, top: 0, right: 600, bottom: 500, width: 600, height: 500, toJSON: () => ({}) })
    const disconnected = vi.fn()
    vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() { disconnected() } })
    const busy = ref(true)
    let state: ReturnType<typeof useLayoutSkeleton>
    const wrapper = mount(defineComponent({ setup() {
      const host = ref<HTMLElement | null>(null)
      state = useLayoutSkeleton(host, () => '/overview', () => 'zh-CN')
      return () => h('main', { ref: host }, [h('header', { class: 'page-header' }), busy.value ? h('div', { class: 'page-skeleton' }) : h('section', { class: 'mh-card' }, 'Real page')])
    } }), { attachTo: document.body })
    await vi.advanceTimersByTimeAsync(40)
    expect(state!.loading.value).toBe(true)
    expect(wrapper.find('[data-layout-placeholder]').exists()).toBe(true)
    busy.value = false; await nextTick(); await vi.advanceTimersByTimeAsync(600)
    expect(state!.loading.value).toBe(false)
    expect(state!.snapshot.value?.rects.length).toBeGreaterThan(0)
    busy.value = true; await nextTick(); await vi.advanceTimersByTimeAsync(80)
    expect(state!.loading.value).toBe(false)
    expect(wrapper.find('[data-layout-placeholder]').exists()).toBe(false)
    wrapper.unmount()
    expect(disconnected).toHaveBeenCalledOnce()
    await vi.advanceTimersByTimeAsync(1000)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('retains a settled empty table while its next refresh is pending', async () => {
    const wrapper = mount(DataTable, { props: { columns: [{ key: 'name', title: 'Name' }], data: [], loading: false }, attachTo: document.body })
    expect(wrapper.find('.mh-table__loading').exists()).toBe(false)
    const empty = wrapper.find('.mh-empty').element
    await wrapper.setProps({ loading: true })
    expect(wrapper.find('.mh-table__loading').exists()).toBe(false)
    expect(wrapper.find('.mh-empty').element).toBe(empty)
    wrapper.unmount()
  })
})
