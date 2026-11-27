import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { h } from 'vue'
import Empty from '../base/Empty.vue'
import EmptyIllustration from '../base/EmptyIllustration.vue'
import { EMPTY_ILLUSTRATIONS, isEmptyIllustration } from '../base/empty'
import * as lib from '../index'

/** 第 5 轮：Empty 插画位。默认行为不变；插画只用主题令牌、纯装饰。 */

describe('Empty 默认插画', () => {
  it('完整空面板使用轻量 SVG，并保留默认文案', () => {
    const wrapper = mount(Empty)
    expect(wrapper.find('.mh-empty__icon').exists()).toBe(false)
    expect(wrapper.find('.mh-empty__art').exists()).toBe(true)
    expect(wrapper.find('svg.mh-ill').exists()).toBe(true)
    expect(wrapper.find('.mh-empty__desc').text()).toBe('暂无数据')
  })
  it('紧凑表格行保留小图标', () => {
    const wrapper = mount(Empty, { props: { compact: true } })
    expect(wrapper.find('.mh-empty__icon').exists()).toBe(true)
    expect(wrapper.find('svg.mh-ill').exists()).toBe(false)
  })
})

describe('Empty 插画位', () => {
  it.each(EMPTY_ILLUSTRATIONS)('illustration="%s" 替换图标圆', (kind) => {
    const wrapper = mount(Empty, { props: { illustration: kind, title: '标题', description: '说明' } })
    expect(wrapper.find('.mh-empty__icon').exists()).toBe(false)
    const svg = wrapper.find('.mh-empty__art svg.mh-ill')
    expect(svg.exists()).toBe(true)
    expect(svg.attributes('data-kind')).toBe(kind)
    expect(svg.attributes('aria-hidden')).toBe('true')
    expect(svg.attributes('width')).toBe('120')
    expect(wrapper.find('.mh-empty__title').text()).toBe('标题')
  })

  it('compact 下插画缩到 88×66', () => {
    const wrapper = mount(Empty, { props: { illustration: 'search', compact: true } })
    const svg = wrapper.find('svg.mh-ill')
    expect(svg.attributes('width')).toBe('88')
    expect(svg.attributes('height')).toBe('66')
  })

  it('#illustration 插槽优先，可放自定义插画', () => {
    const wrapper = mount(Empty, {
      props: { illustration: 'error' },
      slots: { illustration: () => h('img', { class: 'custom-art', alt: '' }) },
    })
    expect(wrapper.find('.custom-art').exists()).toBe(true)
    expect(wrapper.find('svg.mh-ill').exists()).toBe(false)
  })

  it('插画颜色只用令牌（无写死颜色）', () => {
    for (const kind of EMPTY_ILLUSTRATIONS) {
      const html = mount(EmptyIllustration, { props: { kind } }).html()
      expect(html).not.toMatch(/#[0-9a-f]{3,8}\b|rgb\(|fill="(?!none)/i)
    }
  })

  it('类型守卫与统一出口', () => {
    expect(isEmptyIllustration('offline')).toBe(true)
    expect(isEmptyIllustration('nope')).toBe(false)
    expect(lib.EmptyIllustration).toBeTruthy()
    expect(lib.EMPTY_ILLUSTRATIONS).toHaveLength(4)
  })
})
