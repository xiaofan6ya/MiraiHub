/**
 * RowActions 组件测试：浮层开合（点击 / 选择 / Esc / 外点）+ 键盘与 aria 不回归。
 *
 * 回归的缺陷：菜单项 select 之后 naive 的 trigger="click" 不会收浮层，
 * 真实浏览器里浮层一直挂在页面上、盖住页面随后弹出的确认框。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { nextTick } from 'vue'
import RowActions from '@/components/data/RowActions.vue'
import type { RowAction } from '@/components/data/rowActions'

/** 2 个直接按钮 + 2 个收进菜单（普通项在前、危险项在最后）。 */
const ACTIONS: RowAction[] = [
  { key: 'edit', label: '编辑' },
  { key: 'view', label: '详情' },
  { key: 'reset', label: '重置密码' },
  { key: 'delete', label: '删除', danger: true },
]

let wrapper: VueWrapper | null = null

async function settle(): Promise<void> {
  await flushPromises()
  await nextTick()
}

function mountRowActions(actions: RowAction[] = ACTIONS): VueWrapper {
  wrapper = mount(RowActions, { props: { actions }, attachTo: document.body })
  return wrapper
}

/** 「⋯」触发按钮（IconBtn → NButton）。 */
function triggerButton(): HTMLElement {
  const node = document.body.querySelector('.mh-icon-btn') as HTMLElement | null
  if (!node) throw new Error('找不到「⋯」触发按钮')
  return node
}

/** 浮层内容 teleport 到 body，只能从 document 上找。 */
function menu(): HTMLElement | null {
  return document.body.querySelector('.mh-row-actions__menu') as HTMLElement | null
}

function menuItems(): HTMLElement[] {
  const root = menu()
  return root ? (Array.from(root.querySelectorAll('button')) as HTMLElement[]) : []
}

async function click(element: Element | null, label = ''): Promise<void> {
  if (!element) throw new Error('找不到可点元素' + (label ? '：' + label : ''))
  ;(element as HTMLElement).click()
  await settle()
}

async function openMenu(): Promise<void> {
  await click(triggerButton(), '⋯')
  if (!menu()) throw new Error('浮层没有打开')
}

function item(label: string): HTMLElement | null {
  return menuItems().find((el) => (el.textContent || '').trim() === label) || null
}

/** 真实浏览器的一次「外点」：mousedown + mouseup 都落在浮层外（naive 的 clickoutside 依赖这两个事件）。 */
async function clickOutside(): Promise<void> {
  const outside = document.createElement('div')
  document.body.appendChild(outside)
  outside.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
  outside.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
  outside.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  await settle()
}

beforeEach(() => {
  // vueuc 的 VFollower 依赖 ResizeObserver，jsdom 没有。
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe(): void {}
      unobserve(): void {}
      disconnect(): void {}
    },
  )
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
})

describe('RowActions 行操作', () => {
  it('前 2 个操作直接显示，其余进菜单：普通项在前、危险项在最后并带分隔线', async () => {
    const w = mountRowActions()
    await settle()

    expect(w.findAll('.mh-btn').map((btn) => btn.text())).toEqual(['编辑', '详情'])
    expect(menu()).toBeNull() // 默认收起

    await openMenu()

    expect(menuItems().map((el) => (el.textContent || '').trim())).toEqual(['重置密码', '删除'])
    const divider = menu()?.querySelector('.mh-row-actions__divider')
    expect(divider).not.toBeNull()
    // 分隔线夹在普通项与危险项之间
    const nodes = Array.from(menu()?.children || [])
    expect(nodes.indexOf(divider as Element)).toBe(1)
    expect(nodes).toHaveLength(3)
  })

  it('点击「⋯」打开浮层，再点一次收起', async () => {
    mountRowActions()
    await settle()
    expect(menu()).toBeNull()

    await openMenu()
    expect(menu()).not.toBeNull()

    await click(triggerButton(), '⋯')
    expect(menu()).toBeNull()
  })

  it('点选普通项：emit select 的 key，浮层自动关闭', async () => {
    const w = mountRowActions()
    await settle()
    await openMenu()

    await click(item('重置密码'), '重置密码')

    expect(w.emitted('select')).toEqual([['reset']])
    expect(menu()).toBeNull()
  })

  it('点选危险项：emit select 的 key，浮层自动关闭', async () => {
    const w = mountRowActions()
    await settle()
    await openMenu()

    await click(item('删除'), '删除')

    expect(w.emitted('select')).toEqual([['delete']])
    expect(menu()).toBeNull()
  })

  it('Esc 收起浮层且不触发 select，焦点回到「⋯」触发按钮', async () => {
    const w = mountRowActions()
    await settle()
    await openMenu()

    // 焦点在外面的情况下按 Esc 也得收：浮层挂在 body 上，焦点不保证还在菜单里。
    ;(document.activeElement as HTMLElement | null)?.blur?.()
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await settle()

    expect(menu()).toBeNull()
    expect(w.emitted('select')).toBeUndefined()
    expect(document.activeElement).toBe(triggerButton())
  })

  it('键盘焦点在菜单项上时 Esc 同样收敛', async () => {
    const w = mountRowActions()
    await settle()
    await openMenu()

    const target = item('重置密码')
    if (!target) throw new Error('菜单里没有「重置密码」')
    target.focus()
    target.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await settle()

    expect(menu()).toBeNull()
    expect(w.emitted('select')).toBeUndefined()
  })

  it('点击浮层外部收起，且不触发 select', async () => {
    const w = mountRowActions()
    await settle()
    await openMenu()

    await clickOutside()

    expect(menu()).toBeNull()
    expect(w.emitted('select')).toBeUndefined()
  })

  it('直接显示的按钮照旧 emit select，且不会打开浮层', async () => {
    const w = mountRowActions()
    await settle()

    const inline = w.findAll('.mh-btn')[1]
    await click(inline.element, '详情')

    expect(w.emitted('select')).toEqual([['view']])
    expect(menu()).toBeNull()
  })

  it('aria 与键盘可达性保持：触发按钮有 aria-label，菜单项可聚焦、禁用项真禁用', async () => {
    const w = mountRowActions([
      { key: 'edit', label: '编辑' },
      { key: 'view', label: '详情' },
      { key: 'reset', label: '重置密码' },
      { key: 'archive', label: '归档', disabled: true },
      { key: 'delete', label: '删除', danger: true },
    ])
    await settle()

    expect(triggerButton().getAttribute('aria-label')).toBe('更多操作')

    await openMenu()

    const items = menuItems()
    expect(items.map((el) => (el.textContent || '').trim())).toEqual(['重置密码', '归档', '删除'])
    expect(items[0].getAttribute('tabindex')).not.toBe('-1')
    expect(items[1].hasAttribute('disabled')).toBe(true)
    expect(w.emitted('select')).toBeUndefined()
  })

  it('可操作项缩到只剩直接按钮时收敛浮层状态（不会留一个下次自动弹出的空菜单）', async () => {
    const w = mountRowActions()
    await settle()
    await openMenu()
    expect(menu()).not.toBeNull()

    await w.setProps({ actions: ACTIONS.slice(0, 2) })
    await settle()
    expect(w.find('.mh-icon-btn').exists()).toBe(false)

    // 再变回 4 项：浮层应该保持关闭。
    await w.setProps({ actions: ACTIONS })
    await settle()
    expect(menu()).toBeNull()
  })
})
