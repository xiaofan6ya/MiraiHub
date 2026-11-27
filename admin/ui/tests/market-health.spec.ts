import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { TitleTip } from '@/components'
import { marketHealth, type MarketItem, type ModelMarketPayload } from '@/api/market'
import MarketHealthTimeline from '@/pages/Market/MarketHealthTimeline.vue'
import { setLocale } from '@/i18n'

function health() {
  const states = ['up', 'warn', 'down', 'off', 'idle'] as const
  const item = { uptime: 0.9, measured_cells: 3,
    timeline: states.map((state, i) => ({ state, requests: i < 3 ? 10 : 0,
      success_rate: i === 0 ? 1 : 0.8, cooling: state === 'off' ? 2 : 0, coverage: i ? 1 : 0.5 })),
  } as MarketItem
  const payload = { bucket_hours: 6, buckets: states.map((_, i) => ({ label: i + ':00', tip: '北京时间 ' + i + ':00' })) } as ModelMarketPayload
  return marketHealth(item, payload)
}

describe('model market health periods', () => {
  it('preserves backend states and bucket boundaries rather than deriving health from volume', () => {
    const result = health()
    expect(result.cells.map(c => c.state)).toEqual(['up', 'warn', 'down', 'off', 'idle'])
    expect(result.cells[2].label).toBe('2:00')
    expect(result.bucketHours).toBe(6)
    expect(result.cells[0]).toMatchObject({ coverage: 0.5, partial: true, missing: false })
  })

  it('does not invent healthy periods for older responses without timeline data', () => {
    const result = marketHealth({ requests: 100, success_rate: 1 } as MarketItem, {} as ModelMarketPayload)
    expect(result.cells).toEqual([])
    expect(result.uptime).toBeNull()
    const missing = marketHealth({} as MarketItem, { buckets: [{ label: '08:00' }] } as ModelMarketPayload)
    expect(missing.cells[0]).toMatchObject({ state: 'idle', missing: true, requests: 0 })
  })

  it('renders all five original cell states and partial-period system tooltips without a request sparkline', async () => {
    setLocale('zh-CN')
    const wrapper = mount(MarketHealthTimeline, { props: { health: health(), hoursLabel: '7 天' }, attachTo: document.body })
    expect(wrapper.findAll('.wbm-cell')).toHaveLength(5)
    const tips = wrapper.findAllComponents(TitleTip)
    expect(tips[3].props('content')).toContain('2 个账号在冷却')
    expect(tips[0].props('content')).toContain('50%')
    expect(tips[4].props('content')).toContain('不代表故障')
    expect(wrapper.find('[title]').exists()).toBe(false)
    await wrapper.find('.is-idle').trigger('mouseenter')
    await new Promise(resolve => setTimeout(resolve, 180))
    expect(document.querySelector('.mh-help__content')?.textContent).toContain('不代表故障')
    expect(wrapper.find('.wbm-cells').attributes('aria-label')).toContain('5 格')
    expect(wrapper.find('svg').exists()).toBe(false)
    wrapper.unmount()
  })

  it('distinguishes a missing series from a window with no calls', () => {
    const result = health(); result.measured = 0; result.uptime = null
    const wrapper = mount(MarketHealthTimeline, { props: { health: result, hoursLabel: '24 小时' } })
    expect(wrapper.text()).toContain('窗口内无调用')
    wrapper.unmount()
    const absent = mount(MarketHealthTimeline, { props: { hoursLabel: '24 小时' } })
    expect(absent.text()).toContain('服务端未返回时段数据')
    expect(absent.find('.wbm-cells').exists()).toBe(false)
    absent.unmount()
  })
})
