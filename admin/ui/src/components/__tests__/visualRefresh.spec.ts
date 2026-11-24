import { afterEach, describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { Activity } from 'lucide-vue-next'
import KpiCard from '../data/KpiCard.vue'
import Sparkline from '../data/Sparkline.vue'
import SaveBar from '../base/SaveBar.vue'
import { kpiDelta, kpiValueText } from '../data/kpi'
import { cleanSeries, sparkGeometry, sparkToneColor } from '../data/sparkline'
import * as lib from '../index'
import { setLocale } from '@/i18n'

/** 10-visual-refresh §3：Sparkline / KpiCard / SaveBar 的签名与行为。 */

afterEach(() => {
  setLocale('zh-CN')
  document.body.innerHTML = ''
})

describe('components/index.ts 导出', () => {
  it('三个新组件都从统一出口导出', () => {
    expect(lib.Sparkline).toBeTruthy()
    expect(lib.KpiCard).toBeTruthy()
    expect(lib.SaveBar).toBeTruthy()
    expect(typeof lib.kpiDelta).toBe('function')
    expect(typeof lib.sparkGeometry).toBe('function')
  })
})

describe('sparkGeometry', () => {
  it('空数据不画线', () => {
    expect(sparkGeometry([]).line).toBe('')
    expect(sparkGeometry(null).points).toHaveLength(0)
  })

  it('非数当 0，点数与输入一致', () => {
    expect(cleanSeries([1, null, 'x', 3])).toEqual([1, 0, 0, 3])
    const geo = sparkGeometry([1, 2, 3], 100, 28, 2)
    expect(geo.points).toHaveLength(3)
    expect(geo.points[0].x).toBe(0)
    expect(geo.points[2].x).toBe(100)
    // 最小值贴底（y = 高 - pad），最大值贴顶（y = pad）
    expect(geo.points[0].y).toBe(26)
    expect(geo.points[2].y).toBe(2)
    expect(geo.line.startsWith('M0 26')).toBe(true)
    expect(geo.area.endsWith('Z')).toBe(true)
    expect(geo.flat).toBe(false)
  })

  it('全 0 画贴底平线、单点画平线', () => {
    const zero = sparkGeometry([0, 0, 0], 100, 28, 2)
    expect(zero.flat).toBe(true)
    expect(zero.points.every((point) => point.y === 26)).toBe(true)
    const one = sparkGeometry([5], 100, 28, 2)
    expect(one.points).toHaveLength(2)
    expect(one.flat).toBe(true)
  })

  it('语气 → 令牌，未知回落品牌色', () => {
    expect(sparkToneColor('danger')).toBe('var(--danger)')
    expect(sparkToneColor('nope')).toBe('var(--brand)')
  })
})

describe('Sparkline', () => {
  it('纯 SVG：有数据时画线和面积，area=false 不画面积', async () => {
    const wrapper = mount(Sparkline, { props: { data: [1, 3, 2, 5] } })
    expect(wrapper.find('svg.mh-spark').attributes('height')).toBe('28')
    expect(wrapper.find('.mh-spark__line').exists()).toBe(true)
    expect(wrapper.find('.mh-spark__area').exists()).toBe(true)
    await wrapper.setProps({ area: false, height: 40 })
    expect(wrapper.find('.mh-spark__area').exists()).toBe(false)
    expect(wrapper.find('svg').attributes('height')).toBe('40')
  })
})

describe('kpiDelta / kpiValueText', () => {
  it('好坏按 deltaGood 判定', () => {
    expect(kpiDelta(12.34)).toEqual({ text: '+12.3%', dir: 'up', verdict: 'good' })
    expect(kpiDelta(-4)).toEqual({ text: '\u22124%', dir: 'down', verdict: 'bad' })
    expect(kpiDelta(-4, 'down')?.verdict).toBe('good')
    expect(kpiDelta(8, 'down')?.verdict).toBe('bad')
    expect(kpiDelta(150)?.text).toBe('+150%')
  })

  it('持平与无效值', () => {
    expect(kpiDelta(0.01)).toEqual({ text: '0%', dir: 'flat', verdict: 'flat' })
    expect(kpiDelta(null)).toBeNull()
    expect(kpiDelta(Number.NaN)).toBeNull()
  })

  it('数字千分位、小数最多 2 位、空值 —', () => {
    expect(kpiValueText(17583)).toBe('17,583')
    expect(kpiValueText(9.556)).toBe('9.56')
    expect(kpiValueText('4.6s')).toBe('4.6s')
    expect(kpiValueText(null)).toBe('—')
    expect(kpiValueText(Number.POSITIVE_INFINITY)).toBe('—')
  })
})

describe('KpiCard', () => {
  it('标签 / 大数字 / 单位 / 环比 chip / 迷你折线 / 图标方块', () => {
    const wrapper = mount(KpiCard, {
      props: { label: '今日请求', value: 5397, unit: '次', delta: 12.4, spark: [1, 2, 3], icon: Activity, hint: '近 24 小时' },
    })
    expect(wrapper.find('.mh-kpi__label').text()).toBe('今日请求')
    expect(wrapper.find('.mh-kpi__num').text()).toBe('5,397')
    expect(wrapper.find('.mh-kpi__unit').text()).toBe('次')
    const chip = wrapper.find('.mh-kpi__delta')
    expect(chip.classes()).toContain('is-good')
    expect(chip.text()).toContain('+12.4%')
    expect(chip.attributes('aria-label')).toBe('较上期 +12.4%')
    expect(wrapper.find('.mh-spark').exists()).toBe(true)
    expect(wrapper.find('.mh-kpi__icon').exists()).toBe(true)
    expect(wrapper.find('.mh-kpi__hint').text()).toBe('近 24 小时')
    // 没有 to：不是链接
    expect(wrapper.element.tagName).toBe('SECTION')
  })

  it('deltaGood=down 时上升是坏的；空值显示 — 且不显示单位', () => {
    const wrapper = mount(KpiCard, { props: { label: '平均首字', value: null, unit: 's', delta: 8, deltaGood: 'down' } })
    expect(wrapper.find('.mh-kpi__num').text()).toBe('—')
    expect(wrapper.find('.mh-kpi__num').classes()).toContain('is-empty')
    expect(wrapper.find('.mh-kpi__unit').exists()).toBe(false)
    expect(wrapper.find('.mh-kpi__delta').classes()).toContain('is-bad')
  })

  it('首次加载显示骨架；刷新（已有值）保留旧值', async () => {
    const wrapper = mount(KpiCard, { props: { label: 'x', value: null, loading: true } })
    expect(wrapper.find('.mh-kpi__skeleton').exists()).toBe(true)
    expect(wrapper.find('.mh-kpi__num').exists()).toBe(false)
    await wrapper.setProps({ value: 42 })
    expect(wrapper.find('.mh-kpi__skeleton').exists()).toBe(false)
    expect(wrapper.find('.mh-kpi__num').text()).toBe('42')
    expect(wrapper.attributes('aria-busy')).toBe('true')
  })

  it('extra 插槽渲染在底部', () => {
    const wrapper = mount(KpiCard, { props: { label: 'x', value: 1 }, slots: { extra: '<span class="foo">附加</span>' } })
    expect(wrapper.find('.mh-kpi__extra .foo').text()).toBe('附加')
  })

  it('英文环比文案', async () => {
    setLocale('en-US')
    const wrapper = mount(KpiCard, { props: { label: 'x', value: 1, delta: -3 } })
    await nextTick()
    expect(wrapper.find('.mh-kpi__delta').attributes('aria-label')).toBe('\u22123% vs previous')
  })
})

describe('SaveBar', () => {
  it('没有改动时不出现', () => {
    expect(mount(SaveBar, { props: { dirty: 0 } }).find('.mh-savebar').exists()).toBe(false)
    expect(mount(SaveBar, { props: { dirty: false } }).find('.mh-savebar').exists()).toBe(false)
  })

  it('有 N 项未保存：放弃 / 保存', async () => {
    const wrapper = mount(SaveBar, { props: { dirty: 3 } })
    expect(wrapper.find('.mh-savebar__text').text()).toBe('有 3 项未保存')
    const buttons = wrapper.findAll('button')
    expect(buttons.map((b) => b.text())).toEqual(['放弃', '保存'])
    await buttons[0].trigger('click')
    await buttons[1].trigger('click')
    expect(wrapper.emitted('discard')).toHaveLength(1)
    expect(wrapper.emitted('save')).toHaveLength(1)
  })

  it('dirty=true 不计数；disabled 时 Ctrl+S 不触发保存', async () => {
    const wrapper = mount(SaveBar, { props: { dirty: true, disabled: true }, attachTo: document.body })
    expect(wrapper.find('.mh-savebar__text').text()).toBe('有未保存的修改')
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 's', ctrlKey: true }))
    expect(wrapper.emitted('save')).toBeUndefined()
    await wrapper.setProps({ disabled: false })
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 's', ctrlKey: true }))
    expect(wrapper.emitted('save')).toHaveLength(1)
    wrapper.unmount()
    // 卸载后不再监听
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 's', ctrlKey: true }))
  })

  it('保存中：放弃禁用，按钮显示「保存中…」', () => {
    const wrapper = mount(SaveBar, { props: { dirty: 1, saving: true } })
    const buttons = wrapper.findAll('button')
    expect(buttons[0].attributes('disabled')).toBeDefined()
    expect(buttons[1].text()).toContain('保存中')
  })
})
