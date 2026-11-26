/**
 * 外壳与通用组件的中英切换：
 *   页头（标题 + 同组页签 + aria）、空态、错误块、确认框、设置页语言卡片。
 * 切回中文的清理放在 afterEach，避免语言 ref 泄漏到其它用例。
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { nextTick } from 'vue'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import PageHeader from '@/layout/PageHeader.vue'
import Empty from '@/components/base/Empty.vue'
import ErrorBlock from '@/components/base/ErrorBlock.vue'
import ConfirmDialog from '@/components/overlay/ConfirmDialog.vue'
import LanguageCard from '@/layout/LanguageCard.vue'
import { setLocale } from '@/i18n'

let wrapper: VueWrapper | null = null

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      {
        path: '/accounts',
        name: 'accounts',
        component: { template: '<div />' },
        meta: { title: '账号池', group: '账号', platformScoped: true },
      },
    ],
  })
}

async function mountHeader(): Promise<{ router: Router; wrapper: VueWrapper }> {
  const router = makeRouter()
  await router.push('/accounts')
  await router.isReady()
  wrapper = mount(PageHeader, { global: { plugins: [router] } })
  return { router, wrapper }
}

beforeEach(() => {
  setLocale('zh-CN')
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  setLocale('zh-CN')
  document.body.innerHTML = ''
})

describe('页头', () => {
  it('标题与同组页签跟随语言，aria 同步', async () => {
    const { wrapper } = await mountHeader()
    expect(wrapper.find('h1.ph-title').text()).toContain('账号池')
    expect(wrapper.findAll('.ph-tab').map((tab) => tab.text())).toEqual([
      '账号池',
      '积分到期',
      '积分流水',
      '成长计划',
    ])
    expect(wrapper.find('nav.ph-tabs').attributes('aria-label')).toBe('账号页签')

    setLocale('en-US')
    await nextTick()
    expect(wrapper.find('h1.ph-title').text()).toContain('Accounts')
    expect(wrapper.findAll('.ph-tab').map((tab) => tab.text())).toEqual([
      'Accounts',
      'Credits expiring',
      'Credit history',
      'Growth plan',
    ])
    expect(wrapper.find('nav.ph-tabs').attributes('aria-label')).toBe('Accounts tabs')
  })
})

describe('通用组件', () => {
  it('空态默认文案', async () => {
    const local = mount(Empty)
    expect(local.text()).toContain('暂无数据')
    setLocale('en-US')
    await nextTick()
    expect(local.text()).toContain('No data')
    local.unmount()
  })

  it('错误块的默认重试与详情开关', async () => {
    const local = mount(ErrorBlock, { props: { title: '失败', detail: 'boom' } })
    expect(local.text()).toContain('重试')
    expect(local.text()).toContain('展开详细信息')
    setLocale('en-US')
    await nextTick()
    expect(local.text()).toContain('Retry')
    expect(local.text()).toContain('Show details')
    local.unmount()
  })

  it('确认框的按钮与输入门禁文案', async () => {
    const local = mount(ConfirmDialog, {
      props: { show: true, title: '删除账号？', requireText: '删除' },
      attachTo: document.body,
    })
    await flushPromises()
    expect(document.body.textContent).toContain('确定')
    expect(document.body.textContent).toContain('请输入 删除 以确认')

    setLocale('en-US')
    await nextTick()
    expect(document.body.textContent).toContain('OK')
    expect(document.body.textContent).toContain('Type 删除 to confirm')
    local.unmount()
  })
})

describe('设置页语言卡片', () => {
  it('点 English 立即切换、记到 mh.locale，并可切回中文', async () => {
    const local = mount(LanguageCard)
    expect(local.text()).toContain('界面语言')
    expect(local.text()).toContain('中文')
    expect(local.text()).toContain('English')

    const buttons = local.findAll('.mh-seg__item')
    expect(buttons).toHaveLength(2)
    await buttons[1].trigger('click')
    await nextTick()

    expect(local.text()).toContain('Interface language')
    expect(localStorage.getItem('mh.locale')).toBe('en-US')
    expect(document.documentElement.lang).toBe('en-US')

    await local.findAll('.mh-seg__item')[0].trigger('click')
    await nextTick()
    expect(local.text()).toContain('界面语言')
    expect(localStorage.getItem('mh.locale')).toBe('zh-CN')
    local.unmount()
  })
})
