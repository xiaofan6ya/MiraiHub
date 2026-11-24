/**
 * S10 IP 管理 /ip：数据转换 / 查询映射 / 过滤排序分页 / 地址与时长校验 的单测。
 *
 * 数据形状全部照抄后端真实响应：
 *   · GET  /api/ip/activity   → admin/routers/ip_management.py:ip_activity
 *   · GET  /api/ip/blocked    → list_blocked_ips
 *   · GET  /api/ip/devices    → devices / identity_items
 * 契约见 docs/ui-v2/contracts/ip.md。
 */
import { describe, expect, it } from 'vitest'
import {
  activityQueryFromState,
  buildClientSummary,
  classifyClient,
  expiryFromDuration,
  filterActivityRows,
  filterBlockedRows,
  filterDeviceRows,
  paginateRows,
  parseBlockTarget,
  sortBlockedRows,
  sortDeviceRows,
  toActivityRows,
  toBlockedRows,
  toDeviceRows,
  type BlockedIpItem,
  type ClientIdentityItem,
  type IpActivityItem,
} from '@/api/ip'

/** ip_activity 的 items[]：一条正常、一条当前 IP + 已封禁、一条脏数据（缺 client_details）。 */
const activityItems: IpActivityItem[] = [
  {
    ip: '203.0.113.7',
    requests: 1842,
    failed: 37,
    blocked_requests: 0,
    credits: 128.4567,
    tokens: 482013,
    avg_tokens: 261.7,
    avg_credits: 0.0697,
    known_usage: 1805,
    last_seen: '2026-11-23T03:12:44',
    blocked: false,
    blocked_id: null,
    client_details: {
      user_agent: 'claude-cli/1.0.60 (external, cli)',
      device_ids: ['dev-9f2a'],
      fingerprints: ['fp-77c1'],
      macs: [],
      identity_source: 'client_reported',
    },
  },
  {
    ip: '198.51.100.9',
    requests: 40,
    failed: 40,
    blocked_requests: 12,
    credits: 0,
    tokens: 0,
    avg_tokens: null,
    avg_credits: null,
    known_usage: 0,
    last_seen: null,
    blocked: true,
    blocked_id: 3,
    client_details: {
      user_agent: 'python-requests/2.32.3',
      device_ids: [],
      fingerprints: [],
      macs: ['aa:bb:cc:dd:ee:ff'],
      identity_source: 'client_reported',
    },
  },
  {
    ip: '192.0.2.44',
    requests: 5,
    failed: 0,
    blocked_requests: 0,
    credits: 0.5,
    tokens: 900,
    avg_tokens: 180,
    avg_credits: 0.1,
    known_usage: 5,
    last_seen: '2026-11-22T23:00:00',
    blocked: false,
    blocked_id: null,
  },
]

describe('toActivityRows：接口 items → 表格行', () => {
  const rows = toActivityRows(activityItems, '203.0.113.7')

  it('行键是 IP，当前访问 IP 被标出来，失败率按请求数算', () => {
    expect(rows).toHaveLength(3)
    expect(rows[0].key).toBe('203.0.113.7')
    expect(rows[0].isCurrent).toBe(true)
    expect(rows[1].isCurrent).toBe(false)
    expect(rows[0].requests).toBe(1842)
    expect(rows[0].failed).toBe(37)
    expect(rows[0].failureRate).toBeCloseTo(37 / 1842, 6)
    expect(rows[1].failureRate).toBe(1)
  })

  it('数值字段透传，空均值保留 null（表格显示 —，不冒充 0）', () => {
    expect(rows[0].credits).toBe(128.4567)
    expect(rows[0].avgCredits).toBe(0.0697)
    expect(rows[0].avgTokens).toBe(261.7)
    expect(rows[0].knownUsage).toBe(1805)
    expect(rows[1].avgCredits).toBeNull()
    expect(rows[1].avgTokens).toBeNull()
    expect(rows[1].lastSeen).toBeNull()
  })

  it('封禁状态与 blocked_id 原样带到行上', () => {
    expect(rows[1].blocked).toBe(true)
    expect(rows[1].blockedId).toBe(3)
    expect(rows[0].blocked).toBe(false)
    expect(rows[0].blockedId).toBeNull()
    expect(rows[1].blockedRequests).toBe(12)
  })

  it('IP 列气泡：UA 识别 + 未上报兜底', () => {
    expect(rows[0].client.client).toBe('Claude 客户端')
    expect(rows[0].client.deviceIds).toEqual(['dev-9f2a'])
    expect(rows[0].client.fingerprints).toEqual(['fp-77c1'])
    expect(rows[0].client.tooltip).toContain('设备 ID：dev-9f2a')
    expect(rows[0].client.tooltip).toContain('指纹：fp-77c1')
    expect(rows[0].client.tooltip).toContain('MAC：未上报')
    expect(rows[1].client.tooltip).toContain('设备 ID：未上报')
    expect(rows[1].client.tooltip).toContain('MAC：aa:bb:cc:dd:ee:ff')
  })

  it('client_details 缺失时不抛异常，按未上报处理', () => {
    expect(rows[2].client.userAgent).toBe('')
    expect(rows[2].client.client).toBe('未知')
    expect(rows[2].client.tooltip).toContain('UA：未记录（更新后新请求开始采集）')
  })

  it('null / undefined 输入返回空数组', () => {
    expect(toActivityRows(null)).toEqual([])
    expect(toActivityRows(undefined, 'any')).toEqual([])
  })
})

describe('classifyClient：UA → 客户端名（顺序与旧版一致）', () => {
  it('按旧版顺序识别', () => {
    expect(classifyClient('claude-cli/1.0')).toBe('Claude 客户端')
    expect(classifyClient('codex_cli_rs/0.44')).toBe('Codex 客户端')
    expect(classifyClient('OpenAI/Python 1.30')).toBe('OpenAI SDK')
    expect(classifyClient('anthropic-sdk-python/0.40')).toBe('Anthropic SDK')
    expect(classifyClient('curl/8.6.0')).toBe('curl')
    expect(classifyClient('python-httpx/0.27')).toBe('Python HTTPX')
    expect(classifyClient('python-requests/2.32.3')).toBe('Python Requests')
    expect(classifyClient('Mozilla/5.0 (Windows NT 10.0)')).toBe('浏览器')
    expect(classifyClient('')).toBe('未知')
    expect(classifyClient(null)).toBe('未知')
  })

  it('buildClientSummary 把每类标识拼成逗号列表', () => {
    const summary = buildClientSummary({
      user_agent: 'curl/8.6.0',
      device_ids: ['a', 'b'],
      fingerprints: [],
      macs: [],
    })
    expect(summary.tooltip).toContain('设备 ID：a, b')
    expect(summary.tooltip).toContain('指纹：未上报')
  })
})

describe('activityQueryFromState：路由 query → 接口参数', () => {
  const base = {
    page: 1,
    pageSize: 20,
    sortBy: 'requests',
    sortOrder: 'desc',
    filters: { days: '1', q: '', status: '', usage: '', min: '0', max: '' },
  }

  it('默认：近 24 小时、requests desc、offset 0、min_requests 0', () => {
    expect(activityQueryFromState(base)).toEqual({
      days: 1,
      sort: 'requests',
      direction: 'desc',
      limit: 20,
      offset: 0,
      min_requests: 0,
    })
  })

  it('第 3 页 offset 按 pageSize 推导；days 夹在 1–90', () => {
    const query = activityQueryFromState({
      ...base,
      page: 3,
      pageSize: 50,
      filters: { ...base.filters, days: '400' },
    })
    expect(query.offset).toBe(100)
    expect(query.limit).toBe(50)
    expect(query.days).toBe(90)
    expect(activityQueryFromState({ ...base, filters: { ...base.filters, days: '0' } }).days).toBe(1)
  })

  it('days 缺失（query 里没写）时回落到 1', () => {
    expect(activityQueryFromState({ ...base, filters: {} }).days).toBe(1)
  })

  it('排序白名单：未知字段回落 requests，方向只认 asc', () => {
    const unknown = activityQueryFromState({ ...base, sortBy: 'status', sortOrder: 'asc' })
    expect(unknown.sort).toBe('requests')
    expect(unknown.direction).toBe('asc')
    const sorted = activityQueryFromState({ ...base, sortBy: 'tokens', sortOrder: 'desc' })
    expect(sorted.sort).toBe('tokens')
  })

  it('流量筛选 tokens / credits 分别带对应上限，选「全部」时不带', () => {
    const tokens = activityQueryFromState({
      ...base,
      filters: { ...base.filters, usage: 'tokens', max: '100', min: '10' },
    })
    expect(tokens.max_avg_tokens).toBe(100)
    expect(tokens.max_avg_credits).toBeUndefined()
    expect(tokens.min_requests).toBe(10)

    const credits = activityQueryFromState({
      ...base,
      filters: { ...base.filters, usage: 'credits', max: '0.01' },
    })
    expect(credits.max_avg_credits).toBe(0.01)
    expect(credits.max_avg_tokens).toBeUndefined()

    const all = activityQueryFromState({ ...base, filters: { ...base.filters, max: '100' } })
    expect(all.max_avg_tokens).toBeUndefined()
    expect(all.max_avg_credits).toBeUndefined()
  })

  it('搜索 IP 去空白后作为 ip 参数；空串不下发', () => {
    expect(
      activityQueryFromState({ ...base, filters: { ...base.filters, q: '  203.0.113.7 ' } }).ip,
    ).toBe('203.0.113.7')
    expect(activityQueryFromState(base).ip).toBeUndefined()
  })
})

describe('filterActivityRows：状态只过滤当前页', () => {
  const rows = toActivityRows(activityItems, '203.0.113.7')

  it('blocked / allowed', () => {
    expect(filterActivityRows(rows, { status: 'blocked' }).map((row) => row.ip)).toEqual([
      '198.51.100.9',
    ])
    expect(filterActivityRows(rows, { status: 'allowed' }).map((row) => row.ip)).toEqual([
      '203.0.113.7',
      '192.0.2.44',
    ])
  })

  it('空筛选返回副本，不改入参', () => {
    const copy = filterActivityRows(rows)
    expect(copy.map((row) => row.ip)).toEqual(rows.map((row) => row.ip))
    expect(copy).not.toBe(rows)
  })
})

/** list_blocked_ips 的真实响应（后端按 enabled DESC, created_at DESC）。 */
const blockedItems: BlockedIpItem[] = [
  {
    id: 3,
    ip: '198.51.100.9',
    enabled: true,
    note: '异常刷请求',
    created_at: '2026-11-22T10:00:00',
    updated_at: '2026-11-22T10:00:00',
  },
  {
    id: 2,
    ip: '203.0.113.99',
    enabled: false,
    note: '',
    created_at: '2026-11-20T08:30:00',
    updated_at: '2026-11-21T09:00:00',
  },
  {
    id: 1,
    ip: '192.0.2.7',
    enabled: true,
    note: '扫描 /admin',
    created_at: '2026-11-18T01:00:00',
    updated_at: '2026-11-18T01:00:00',
    expires_at: '2026-11-25T01:00:00',
  },
]

describe('toBlockedRows / filterBlockedRows / sortBlockedRows：封禁名单', () => {
  const rows = toBlockedRows(blockedItems)

  it('行键是 id；expires_at 为空即永久', () => {
    expect(rows.map((row) => row.key)).toEqual(['3', '2', '1'])
    expect(rows[0].expiry).toBe('permanent')
    expect(rows[0].expiresAt).toBeNull()
    expect(rows[2].expiry).toBe('timed')
    expect(rows[2].expiresAt).toBe('2026-11-25T01:00:00')
    expect(rows[1].enabled).toBe(false)
    expect(rows[1].note).toBe('')
  })

  it('搜索 IP / 原因，状态 enabled / disabled', () => {
    expect(filterBlockedRows(rows, { q: '198.51' }).map((row) => row.ip)).toEqual(['198.51.100.9'])
    expect(filterBlockedRows(rows, { q: '扫描' }).map((row) => row.ip)).toEqual(['192.0.2.7'])
    expect(filterBlockedRows(rows, { status: 'enabled' }).map((row) => row.id)).toEqual([3, 1])
    expect(filterBlockedRows(rows, { status: 'disabled' }).map((row) => row.id)).toEqual([2])
    expect(filterBlockedRows(rows, { q: '203.0.113.99' }).map((row) => row.id)).toEqual([2])
  })

  it('排序：ip / enabled / createdAt；非法入参保持后端顺序且不改入参', () => {
    expect(sortBlockedRows(rows, 'ip', 'asc').map((row) => row.id)).toEqual([1, 3, 2])
    expect(sortBlockedRows(rows, 'enabled', 'asc').map((row) => row.id)).toEqual([2, 3, 1])
    expect(sortBlockedRows(rows, 'createdAt', 'desc').map((row) => row.id)).toEqual([3, 2, 1])
    expect(sortBlockedRows(rows, 'note', 'asc').map((row) => row.id)).toEqual([3, 2, 1])
    expect(sortBlockedRows(rows, 'ip', '').map((row) => row.id)).toEqual([3, 2, 1])
    expect(rows.map((row) => row.id)).toEqual([3, 2, 1])
  })

  it('空 / 脏数据兜底', () => {
    expect(toBlockedRows(null)).toEqual([])
    const [row] = toBlockedRows([{ id: 9 } as unknown as BlockedIpItem])
    expect(row.ip).toBe('')
    expect(row.enabled).toBe(false)
    expect(row.note).toBe('')
    expect(row.createdAt).toBeNull()
  })
})

/** devices 的真实响应（identity_items 归并后的形状）。 */
const deviceItems: ClientIdentityItem[] = [
  {
    kind: 'device_id',
    digest: '9f2a1c3b5d',
    value: 'dev-9f2a',
    blocked: false,
    note: '',
    source: 'client_reported',
    ips: ['203.0.113.7', '198.51.100.4'],
    last_seen: '2026-11-23T03:11:02',
  },
  {
    kind: 'mac',
    digest: 'aabbccddeeff',
    value: 'aa:bb:cc:dd:ee:ff',
    blocked: true,
    note: '批量注册',
    source: 'client_reported',
    ips: ['192.0.2.44'],
    last_seen: '2026-11-22T12:00:00',
  },
  {
    kind: 'fingerprint',
    digest: 'fp77c1',
    value: 'fp-77c1',
    blocked: false,
    note: '',
    source: 'client_reported',
    ips: [],
    last_seen: null,
  },
]

describe('toDeviceRows / filterDeviceRows / sortDeviceRows：设备关联', () => {
  const rows = toDeviceRows(deviceItems)

  it('行键是 kind:digest，类型标签按旧版文案', () => {
    expect(rows.map((row) => row.key)).toEqual([
      'device_id:9f2a1c3b5d',
      'mac:aabbccddeeff',
      'fingerprint:fp77c1',
    ])
    expect(rows.map((row) => row.kindLabel)).toEqual(['设备 ID', 'MAC', '客户端指纹'])
    expect(rows[0].ips).toEqual(['203.0.113.7', '198.51.100.4'])
  })

  it('搜索标识 / 类型 / 关联 IP，状态 blocked / allowed', () => {
    expect(filterDeviceRows(rows, { q: 'dev-9f2a' }).map((row) => row.digest)).toEqual([
      '9f2a1c3b5d',
    ])
    expect(filterDeviceRows(rows, { q: '192.0.2.44' }).map((row) => row.digest)).toEqual([
      'aabbccddeeff',
    ])
    expect(filterDeviceRows(rows, { q: '设备 id' }).map((row) => row.digest)).toEqual([
      '9f2a1c3b5d',
    ])
    expect(filterDeviceRows(rows, { status: 'blocked' }).map((row) => row.digest)).toEqual([
      'aabbccddeeff',
    ])
    expect(filterDeviceRows(rows, { status: 'allowed' }).map((row) => row.digest)).toEqual([
      '9f2a1c3b5d',
      'fp77c1',
    ])
  })

  it('排序：类型 / 最近活动 / 状态；空方向保持后端顺序', () => {
    expect(sortDeviceRows(rows, 'kindLabel', 'asc').map((row) => row.digest)).toEqual([
      'fp77c1',
      '9f2a1c3b5d',
      'aabbccddeeff',
    ])
    expect(sortDeviceRows(rows, 'lastSeen', 'desc').map((row) => row.digest)).toEqual([
      '9f2a1c3b5d',
      'aabbccddeeff',
      'fp77c1',
    ])
    expect(sortDeviceRows(rows, 'blocked', 'desc').map((row) => row.digest)).toEqual([
      'aabbccddeeff',
      '9f2a1c3b5d',
      'fp77c1',
    ])
    expect(sortDeviceRows(rows, 'blocked', '').map((row) => row.digest)).toEqual([
      '9f2a1c3b5d',
      'aabbccddeeff',
      'fp77c1',
    ])
  })
})

describe('paginateRows：客户端分页（封禁 / 设备共用）', () => {
  const rows = Array.from({ length: 42 }, (_, index) => index + 1)

  it('第 2 页取 20 条，页数向上取整', () => {
    const page = paginateRows(rows, 2, 20)
    expect(page.total).toBe(42)
    expect(page.pageCount).toBe(3)
    expect(page.page).toBe(2)
    expect(page.items).toHaveLength(20)
    expect(page.items[0]).toBe(21)
  })

  it('越界回退到最后一页；空数据 0 页 / 页码 1', () => {
    const page = paginateRows(rows, 99, 20)
    expect(page.page).toBe(3)
    expect(page.items).toEqual([41, 42])
    expect(paginateRows([], 3, 20)).toEqual({ items: [], total: 0, pageCount: 0, page: 1 })
  })

  it('每页条数非法时回落到 20', () => {
    expect(paginateRows(rows, 1, 0).items).toHaveLength(20)
  })
})

describe('parseBlockTarget：封禁地址（事实来源：后端 _normalize_ip）', () => {
  it('空值', () => {
    expect(parseBlockTarget('')).toEqual({
      kind: 'empty',
      value: '',
      message: '请填写要封禁的 IP 地址',
    })
    expect(parseBlockTarget('   ').kind).toBe('empty')
  })

  it('IPv4 / IPv6 单地址通过', () => {
    expect(parseBlockTarget(' 203.0.113.7 ')).toEqual({
      kind: 'ip',
      value: '203.0.113.7',
      message: '',
    })
    expect(parseBlockTarget('2001:db8::1').kind).toBe('ip')
    expect(parseBlockTarget('2001:db8:0:0:0:0:0:1').kind).toBe('ip')
  })

  it('非法地址给出与后端 400 同文案的提示', () => {
    expect(parseBlockTarget('203.0.113.256')).toEqual({
      kind: 'invalid',
      value: '203.0.113.256',
      message: '请输入有效的 IPv4 或 IPv6 地址',
    })
    expect(parseBlockTarget('abc').kind).toBe('invalid')
    expect(parseBlockTarget('2001:db8:::1').kind).toBe('invalid')
  })

  it('CIDR 单独标出：后端只接受单地址，表单层拦住并说明', () => {
    const cidr = parseBlockTarget('198.51.100.0/24')
    expect(cidr.kind).toBe('cidr')
    expect(cidr.message).toContain('CIDR')
    expect(parseBlockTarget('2001:db8::/32').kind).toBe('cidr')
    expect(parseBlockTarget('198.51.100.0/33').kind).toBe('invalid')
    expect(parseBlockTarget('203.0.113.0/').kind).toBe('invalid')
  })
})

describe('expiryFromDuration：时长 → expires_at', () => {
  const now = Date.UTC(2026, 10, 23, 3, 0, 0)

  it('永久不提交 expires_at', () => {
    expect(expiryFromDuration('permanent', now)).toEqual({ expiresAt: null, durationSeconds: null, error: '' })
  })

  it('1 小时 / 1 天 / 7 天换算成 ISO', () => {
    expect(expiryFromDuration('1h', now).expiresAt).toBe('2026-11-23T04:00:00.000Z')
    expect(expiryFromDuration('1d', now).expiresAt).toBe('2026-11-24T03:00:00.000Z')
    expect(expiryFromDuration('7d', now).expiresAt).toBe('2026-11-30T03:00:00.000Z')
    // r3：预设时长同时给出 duration_seconds（提交时优先用它，避免浏览器时钟偏差）
    expect(expiryFromDuration('1h', now).durationSeconds).toBe(3600)
    expect(expiryFromDuration('30d', now).durationSeconds).toBe(30 * 86400)
  })

  it('自定义超过 366 天被拦下（后端上限）', () => {
    expect(expiryFromDuration('custom', now, now + 367 * 86400000).error).toBe('到期时间不能超过 366 天')
  })

  it('自定义：未选 / 早于当前 报错，晚于当前通过', () => {
    expect(expiryFromDuration('custom', now, null)).toEqual({
      expiresAt: null,
      durationSeconds: null,
      error: '请选择到期时间',
    })
    expect(expiryFromDuration('custom', now, now - 1000).error).toBe('到期时间必须晚于当前时间')
    const plan = expiryFromDuration('custom', now, now + 3600000)
    expect(plan.error).toBe('')
    expect(plan.expiresAt).toBe('2026-11-23T04:00:00.000Z')
  })
})
