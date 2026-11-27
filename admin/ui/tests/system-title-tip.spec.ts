import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { h } from 'vue'
import { NDropdown, NPopover } from 'naive-ui'
import { Check } from 'lucide-vue-next'
import { Btn, IconBtn, TitleTip } from '@/components'

const mounted: ReturnType<typeof mount>[] = []
afterEach(() => {
  mounted.splice(0).forEach(wrapper => wrapper.unmount())
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})
const settle = () => new Promise(resolve => setTimeout(resolve, 180))

describe('system descriptions', () => {
  it('shows mh-help content without a native title or an extra layout wrapper', async () => {
    const wrapper = mount({ render: () => h('div', { class: 'test-grid' }, [h(TitleTip, { content: '完整说明' }, () => h('span', { class: 'ellipsis-cell' }, '说明'))]) }, {
      attachTo: document.body,
    })
    mounted.push(wrapper)
    expect(wrapper.find('.ellipsis-cell').element.parentElement?.className).toBe('test-grid')
    expect(wrapper.find('.mh-title-tip__trigger').exists()).toBe(false)
    expect(wrapper.find('[title]').exists()).toBe(false)
    await wrapper.find('.ellipsis-cell').trigger('mouseenter')
    await settle()
    expect(document.querySelector('.mh-help__content')?.textContent).toBe('完整说明')
  })

  it('keeps dropdown click handling and custom button attributes when tips are nested', async () => {
    const warning = vi.spyOn(console, 'warn')
    const wrapper = mount(NDropdown, {
      attachTo: document.body,
      props: { trigger: 'click', options: [{ key: 'test', label: '菜单操作' }] },
      slots: { default: () => h(Btn, { title: '按钮说明', class: 'custom-button', 'data-action': 'open' }, () => '打开菜单') },
    })
    mounted.push(wrapper)
    const button = wrapper.find('button')
    expect(button.classes()).toContain('custom-button')
    expect(button.attributes('data-action')).toBe('open')
    expect(button.attributes('title')).toBeUndefined()
    await button.trigger('click')
    await settle()
    expect(document.querySelector('.n-dropdown-menu')?.textContent).toContain('菜单操作')
    expect(warning.mock.calls.flat().join(' ')).not.toContain('non-element root')
  })

  it('opens an outer popover from an IconBtn while keeping its description', async () => {
    const wrapper = mount(NPopover, {
      attachTo: document.body,
      props: { trigger: 'click' },
      slots: {
        trigger: () => h(IconBtn, { label: '列设置', icon: Check }),
        default: () => h('div', { class: 'column-panel' }, '列设置面板'),
      },
    })
    mounted.push(wrapper)
    expect(wrapper.find('button').attributes('aria-label')).toBe('列设置')
    await wrapper.find('button').trigger('click')
    await settle()
    expect(document.querySelector('.column-panel')?.textContent).toBe('列设置面板')
  })
})
