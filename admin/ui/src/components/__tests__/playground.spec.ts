import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { NConfigProvider, NDialogProvider, NMessageProvider, dateZhCN, zhCN } from 'naive-ui'
import Playground from '@/pages/_Playground.vue'

/** RouterLink / Chart 在 jsdom 里没有意义：换成占位，专注组件渲染冒烟。 */
const RouterLinkStub = defineComponent({
  name: 'RouterLink',
  props: { to: { type: [String, Object], default: '' } },
  setup(_props, { slots }) {
    return () => h('a', slots.default?.())
  },
})

const ChartStub = defineComponent({
  name: 'Chart',
  props: { option: { type: Object, default: () => ({}) }, height: { type: String, default: '' } },
  setup() {
    return () => h('div', { class: 'stub-chart' })
  },
})

const Host = defineComponent({
  setup() {
    return () =>
      h(
        NConfigProvider,
        { locale: zhCN, dateLocale: dateZhCN },
        {
          default: () =>
            h(NMessageProvider, {}, {
              default: () =>
                h(NDialogProvider, {}, { default: () => h(Playground) }),
            }),
        },
      )
  },
})

describe('_Playground 组件展示页冒烟', () => {
  let consoleError: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined)
  })

  afterEach(() => {
    consoleError.mockRestore()
  })

  it('一次渲染全部组件，不产生运行时报错', () => {
    const wrapper = mount(Host, {
      attachTo: document.body,
      global: { stubs: { RouterLink: RouterLinkStub, Chart: ChartStub } },
    })

    const text = wrapper.text()
    for (const title of ['基础组件', '数据展示', '图表', '表单与浮层', '平台与模型图标']) {
      expect(text).toContain(title)
    }
    // 6 个假平台都出现在线路带里
    for (const name of ['WorkBuddy', 'Loomy', 'Trae 国内版', 'Qoder 中国版', 'Trae 国际版', '腾讯 Marvis']) {
      expect(text).toContain(name)
    }
    // 空数据线路带
    expect(wrapper.findAll('.mh-ribbon').length).toBe(2)
    // DataTable 在桌面宽度走表格
    expect(wrapper.find('.n-data-table').exists()).toBe(true)
    // Badge 五个语气
    expect(wrapper.findAll('.mh-badge').length).toBeGreaterThanOrEqual(5)
    // 平台 / 模型图标
    expect(wrapper.findAll('.mh-plogo').length).toBeGreaterThanOrEqual(13)
    expect(wrapper.findAll('.mh-micon').length).toBeGreaterThanOrEqual(14)

    const errors = consoleError.mock.calls
      .map((call) => String(call[0] ?? ''))
      .filter((message) => message.includes('[Vue warn]'))
    expect(errors).toEqual([])

    wrapper.unmount()
  })
})
