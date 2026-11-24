import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import KV from '../base/KV.vue'

/**
 * 第 2 轮审查 P0-12：block 行是 flex-direction: column，内联 flex-basis: 120px 会变成**高度**，
 * 值被挤到 120px 以下。block 行不能再带宽度 / flex-basis。
 */
describe('KV 键值列表', () => {
  it('普通行：标签固定宽度（默认 120px，经 --kv-label-w）', () => {
    const wrapper = mount(KV, { props: { items: [{ label: '名称', value: 'abc' }] } })
    const dt = wrapper.find('dt').element as HTMLElement
    // 宽度经 CSS 变量交给样式表（width / flex-basis 都读它），手机端不需要 !important 覆盖
    expect(dt.style.getPropertyValue('--kv-label-w')).toBe('120px')
    expect(dt.style.width).toBe('')
  })

  it('block 行：不写 width / flex-basis，值紧跟在标签下面', () => {
    const wrapper = mount(KV, {
      props: { items: [{ label: '数据库文件', value: 'C:/data/app.db', block: true }], labelWidth: '160px' },
    })
    const row = wrapper.find('.mh-kv__row')
    expect(row.classes()).toContain('mh-kv__row--block')
    const dt = wrapper.find('dt').element as HTMLElement
    expect(dt.style.width).toBe('')
    expect(dt.style.flexBasis).toBe('')
    expect(dt.style.getPropertyValue('--kv-label-w')).toBe('')
    expect(wrapper.find('dd').text()).toBe('C:/data/app.db')
  })

  it('空值显示 —', () => {
    const wrapper = mount(KV, { props: { items: [{ label: '备注', value: '' }] } })
    expect(wrapper.find('.mh-kv__dash').text()).toBe('—')
  })
})
