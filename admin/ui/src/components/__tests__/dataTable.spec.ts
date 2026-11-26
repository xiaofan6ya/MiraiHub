import { beforeEach, describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { h } from 'vue'
import DataTable from '../data/DataTable.vue'
import type { TableColumn } from '../data/table'

type Row = Record<string, unknown>

const columns: TableColumn[] = [
  { key: 'account', title: '账号', mobile: 'title' },
  { key: 'status', title: '状态', mobile: 'title' },
  { key: 'platform', title: '平台', mobile: 'subtitle' },
  { key: 'remain', title: '剩余', mobile: 'subtitle', numeric: true },
  { key: 'requests', title: '请求', mobile: 'meta', numeric: true },
  { key: 'points', title: '积分', mobile: 'meta', numeric: true },
  { key: 'hitRate', title: '命中率', mobile: 'meta' },
  { key: 'createdAt', title: '创建时间', mobile: 'meta', hideable: false },
  { key: 'actions', title: '操作', mobile: 'actions', render: () => h('button', { class: 'row-action' }, '刷新') },
  { key: 'secret', title: '内部字段', mobile: 'hidden' },
]

const rows: Row[] = [
  {
    id: 'a1',
    account: '193****2486',
    status: '活跃',
    platform: 'WorkBuddy',
    remain: 4764,
    requests: 340,
    points: 9,
    hitRate: '95%',
    createdAt: '2026-11-01',
    secret: 'internal-a1',
  },
  {
    id: 'a2',
    account: '193****9001',
    status: '冷却',
    platform: 'Loomy',
    remain: 210,
    requests: 12,
    points: 0,
    hitRate: '72%',
    createdAt: '2026-11-02',
    secret: 'internal-a2',
  },
]

function setViewport(mobile: boolean): void {
  window.matchMedia = ((query: string) => ({
    matches: mobile && query.includes('640'),
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia
}

function mountTable(): ReturnType<typeof mount> {
  return mount(DataTable, {
    props: {
      columns,
      data: rows,
      tableId: 'accounts',
      rowKey: 'id',
      selectable: true,
      pagination: false,
    },
  })
}

describe('DataTable 手机卡片模式（按列 mobile 标注渲染）', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('≤640px 不渲染表格，改成卡片列表', () => {
    setViewport(true)
    const wrapper = mountTable()
    expect(wrapper.find('.n-data-table').exists()).toBe(false)
    expect(wrapper.findAll('.mh-cards__item')).toHaveLength(2)
  })

  it('title 行放主要标识与状态，subtitle 用 · 连接', () => {
    setViewport(true)
    const wrapper = mountTable()
    const card = wrapper.findAll('.mh-cards__item')[0]
    const title = card.find('.mh-cards__title').text()
    expect(title).toContain('193****2486')
    expect(title).toContain('活跃')
    const subtitle = card.find('.mh-cards__subtitle').text()
    expect(subtitle).toContain('WorkBuddy')
    expect(subtitle).toContain('·')
    expect(subtitle).toContain('4764')
  })

  it('meta 按列标题渲染，最多 4 项', () => {
    setViewport(true)
    const wrapper = mountTable()
    const card = wrapper.findAll('.mh-cards__item')[0]
    const labels = card.findAll('.mh-cards__meta-label').map((node) => node.text())
    expect(labels).toEqual(['请求', '积分', '命中率', '创建时间'])
    expect(card.findAll('.mh-cards__meta-item')).toHaveLength(4)
    expect(card.find('.mh-cards__meta-value').text()).toContain('340')
  })

  it('actions 列渲染到卡片底部；hidden 列不渲染', () => {
    setViewport(true)
    const wrapper = mountTable()
    const card = wrapper.findAll('.mh-cards__item')[0]
    expect(card.find('.mh-cards__actions .row-action').exists()).toBe(true)
    expect(card.text()).not.toContain('internal-a1')
    expect(card.text()).not.toContain('内部字段')
  })

  it('桌面宽度继续用 NDataTable', () => {
    setViewport(false)
    const wrapper = mountTable()
    expect(wrapper.find('.n-data-table').exists()).toBe(true)
    expect(wrapper.findAll('.mh-cards__item')).toHaveLength(0)
  })
})
