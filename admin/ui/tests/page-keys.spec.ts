import { describe, expect, it } from 'vitest'
import {
  buildCurlExamples,
  clientModelId,
  draftToInput,
  emptyIpOverview,
  filterKeyRows,
  hasFormErrors,
  keyStats,
  modelChips,
  originOptions,
  paginateKeyRows,
  searchModelChips,
  sortKeyRows,
  statusPatch,
  toIpOverview,
  toKeyRow,
  toKeyRows,
  toUsageView,
  validateKeyForm,
  type AggregateModelsPayload,
  type ApiKeyItem,
  type KeyFormDraft,
  type KeyIpsPayload,
} from '@/api/keys'
import { FMT_EMPTY } from '@/utils/format'

/** 真实响应形状：admin/routers/keys.py:list_keys 的一项。 */
function item(overrides: Partial<ApiKeyItem> = {}): ApiKeyItem {
  return {
    id: 7,
    name: '给设计组的 Key',
    key_prefix: 'sk-3f9a2b7c1d0e',
    masked: 'sk-3f9a2b****c1d0',
    credit_limit: 100,
    credit_used: 96,
    unlimited: false,
    max_concurrent: 0,
    rate_per_minute: null,
    status: 'active',
    note: '设计组共享',
    group_id: 3,
    group_name: '便宜模型组',
    account_id: null,
    account_ids: [11, 12],
    platform: 'trae',
    aggregate: true,
    account_name: '',
    created_at: '2026-11-20T09:12:33',
    ip_summary: {
      days: 1,
      unique_ips: 4,
      requests: 128,
      active_ips: 1,
      active_requests: 2,
      top_ips: [
        { ip: '10.0.0.8', active_requests: 2, requests: 90 },
        { ip: '10.0.0.9', active_requests: 0, requests: 38 },
      ],
      active_scope: '当前服务进程',
    },
    ...overrides,
  }
}

/** desktop/updates 无关；这是 /api/models/aggregate 的真实形状。 */
function aggregate(): AggregateModelsPayload {
  return {
    items: [
      {
        key: 'deepseek-v4-flash',
        name: 'DeepSeek-V4.1-Flash',
        platform_count: 2,
        min_multiplier: 1,
        platforms: [
          { platform: 'workbuddy', model_id: 'deepseek-v4.1-flash', multiplier: 1, enabled: true, platform_label: 'WorkBuddy' },
          { platform: 'trae', model_id: 'dfmodel', client_id: 'dfmodel-trae', multiplier: 0, enabled: true, platform_label: 'Trae' },
        ],
      },
      {
        key: 'glm-5.3-flash',
        name: 'GLM-5.3-Flash',
        platform_count: 1,
        platforms: [{ platform: 'qoder', model_id: 'glm-5.3-flash', multiplier: 0.5, enabled: true }],
      },
      {
        key: 'disabled-one',
        name: '停用模型',
        platform_count: 1,
        platforms: [{ platform: 'trae', model_id: 'x', enabled: false }],
      },
    ],
    total_models: 3,
    total_groups: 3,
  }
}

function draft(overrides: Partial<KeyFormDraft> = {}): KeyFormDraft {
  return {
    name: '',
    note: '',
    groupId: 0,
    unlimited: true,
    creditLimit: '',
    maxConcurrent: '',
    ratePerMinute: '',
    aggregate: true,
    platform: '',
    accountIds: [],
    resetUsed: false,
    ...overrides,
  }
}

describe('列表行转换（/api/keys → 表格）', () => {
  it('限额/已用/并发/来源 IP/分组/状态按旧版口径展示', () => {
    const row = toKeyRow(item())
    expect(row.name).toBe('给设计组的 Key')
    expect(row.masked).toBe('sk-3f9a2b****c1d0')
    expect(row.limitText).toBe('100')
    expect(row.usedText).toBe('96')
    expect(row.usageRatio).toBeCloseTo(0.96)
    expect(row.usageTone).toBe('danger')
    expect(row.usageText).toBe('96.0%')
    expect(row.concurrencyText).toBe('不限')
    expect(row.rateText).toBe('频率随全局')
    expect(row.ipText).toBe('4 个 / 24h')
    expect(row.ipBadges[0]).toEqual({ ip: '10.0.0.8', text: '10.0.0.8 · 2 并发 · 90 次' })
    expect(row.groupLabel).toBe('便宜模型组')
    expect(row.statusLabel).toBe('正常')
    expect(row.statusTone).toBe('ok')
    expect(row.accountLabel).toBe('独占 2 个账号')
    // 后端没有到期字段：如实显示 —
    expect(row.expireText).toBe(FMT_EMPTY)
  })

  it('不限额 / 单账号 / 已停用 / 前缀缺失的兜底', () => {
    const unlimited = toKeyRow(item({ unlimited: true, credit_limit: 0, credit_used: 12 }))
    expect(unlimited.limitText).toBe('∞')
    expect(unlimited.usageText).toBe('不限额度')
    expect(unlimited.usageTone).toBe('ok')

    const single = toKeyRow(item({ account_ids: [], account_id: 5, account_name: '号 A' }))
    expect(single.accountLabel).toBe('独占账号：号 A')

    const revoked = toKeyRow(item({ status: 'revoked' }))
    expect(revoked.statusLabel).toBe('已停用')
    expect(revoked.statusTone).toBe('neutral')

    const legacy = toKeyRow(item({ masked: '', key_prefix: 'sk-abcdef123456' }))
    expect(legacy.masked).toBe('sk-abc…3456')
    expect(toKeyRows(null)).toEqual([])
  })

  it('60% 与 90% 是额度着色分界', () => {
    expect(toKeyRow(item({ credit_limit: 100, credit_used: 59 })).usageTone).toBe('ok')
    expect(toKeyRow(item({ credit_limit: 100, credit_used: 60 })).usageTone).toBe('warn')
    expect(toKeyRow(item({ credit_limit: 100, credit_used: 90 })).usageTone).toBe('danger')
  })
})

describe('筛选 / 排序 / 分页 / 统计', () => {
  const rows = [
    toKeyRow(item({ id: 1, name: 'Alpha', group_id: 0, group_name: '', credit_limit: 10, credit_used: 1, max_concurrent: null })),
    toKeyRow(item({ id: 2, name: 'Beta', status: 'revoked', credit_limit: 100, credit_used: 90, max_concurrent: 4 })),
    toKeyRow(item({ id: 3, name: 'Gamma', note: '市场部', created_at: '2026-11-21T10:00:00' })),
  ]

  it('名称/备注/账号搜索 + 分组 + 状态', () => {
    expect(filterKeyRows(rows, { q: 'alpha' })).toHaveLength(1)
    expect(filterKeyRows(rows, { q: '市场' })).toHaveLength(1)
    expect(filterKeyRows(rows, { group: 'none' })).toHaveLength(1)
    expect(filterKeyRows(rows, { status: 'revoked' })).toHaveLength(1)
    expect(filterKeyRows(rows, {})).toHaveLength(3)
  })

  it('名称与数值列排序（不限额排最大）', () => {
    expect(sortKeyRows(rows, 'name', 'asc').map((row) => row.name)).toEqual(['Alpha', 'Beta', 'Gamma'])
    expect(sortKeyRows(rows, 'used', 'desc')[0].name).toBe('Gamma')
    expect(sortKeyRows(rows, 'concurrency', 'desc')[0].name).toBe('Beta')
    expect(sortKeyRows(rows, 'limit', 'asc')[0].name).toBe('Alpha')
    expect(sortKeyRows(rows, '', 'asc')).toBe(rows)
  })

  it('分页夹紧页码，统计正常/停用/不限额', () => {
    const page = paginateKeyRows(rows, 9, 2)
    expect(page.page).toBe(2)
    expect(page.items).toHaveLength(1)
    expect(page.total).toBe(3)
    expect(keyStats(rows)).toEqual({ total: 3, active: 2, revoked: 1, unlimited: 0 })
    expect(statusPatch('active')).toEqual({ status: 'revoked' })
    expect(statusPatch('revoked')).toEqual({ status: 'active' })
  })
})

describe('表单校验与请求体', () => {
  it('编辑时名称必填；数字项必须是非负整数', () => {
    expect(validateKeyForm(draft(), false).name).toBeUndefined()
    expect(validateKeyForm(draft(), true).name).toBe('名称不能为空')
    const errors = validateKeyForm(
      draft({ unlimited: false, maxConcurrent: '-1', ratePerMinute: '2.5', creditLimit: 'abc' }),
      false,
    )
    expect(errors.maxConcurrent).toBe('并发上限不能小于 0')
    expect(errors.ratePerMinute).toBe('每分钟请求数必须是整数')
    expect(errors.creditLimit).toBe('额度必须是数字')
    expect(hasFormErrors(errors)).toBe(true)
    expect(hasFormErrors(validateKeyForm(draft({ maxConcurrent: '0' }), false))).toBe(false)
  })

  it('不限额/留空 → unlimited + credit_limit null，空流量上限 → null', () => {
    const input = draftToInput(draft({ name: ' A ', groupId: 2, accountIds: [3, 4] }))
    expect(input.name).toBe('A')
    expect(input.unlimited).toBe(true)
    expect(input.credit_limit).toBeNull()
    expect(input.max_concurrent).toBeNull()
    expect(input.rate_per_minute).toBeNull()
    expect(input.group_id).toBe(2)
    expect(input.account_ids).toEqual([3, 4])

    const limited = draftToInput(draft({ unlimited: false, creditLimit: '20', maxConcurrent: '0' }))
    expect(limited.unlimited).toBe(false)
    expect(limited.credit_limit).toBe(20)
    expect(limited.max_concurrent).toBe(0)
  })
})

describe('对接信息（模型 / 地址 / curl）', () => {
  it('调用名带平台后缀，不重复叠加', () => {
    expect(clientModelId('deepseek-v4.1-flash', 'workbuddy')).toBe('deepseek-v4.1-flash-workbuddy')
    expect(clientModelId('dfmodel-trae', 'trae')).toBe('dfmodel-trae')
    expect(clientModelId('x', '')).toBe('x')
  })

  it('可用模型 chip：过滤停用平台、优先用后端 client_id、只显示前 N 个', () => {
    const { chips, total, rest } = modelChips(aggregate(), 1)
    expect(total).toBe(2)
    expect(chips).toHaveLength(1)
    expect(rest).toBe(1)
    expect(chips[0].copyValue).toBe('deepseek-v4.1-flash-workbuddy')
    expect(chips[0].platformCount).toBe(2)
    expect(chips[0].title).toContain('dfmodel-trae')
    expect(chips[0].title).toContain('FREE')
  })

  it('全部模型抽屉：按名字与调用名搜索', () => {
    expect(searchModelChips(aggregate(), '')).toHaveLength(2)
    expect(searchModelChips(aggregate(), 'glm')).toHaveLength(1)
    expect(searchModelChips(aggregate(), 'dfmodel-trae')).toHaveLength(1)
    expect(searchModelChips(aggregate(), 'zzz')).toHaveLength(0)
  })

  it('连接地址候选：当前访问 + 本机 + 内网 IP', () => {
    const options = originOptions(
      { lan_ips: ['192.168.1.20'], port: 8790 },
      { origin: 'http://127.0.0.1:8790', hostname: '127.0.0.1', port: '' },
    )
    // 当前访问与本机选项内容相同 → Map 去重，只剩一条 + 内网
    expect(options.map((option) => option.value)).toEqual([
      'http://127.0.0.1:8790',
      'http://192.168.1.20:8790',
    ])
    expect(options[1].label).toBe('局域网 · 192.168.1.20:8790')

    const lan = originOptions({ lan_ips: [], port: 8790 }, { origin: 'http://192.168.1.20:8790', hostname: '192.168.1.20', port: '' })
    expect(lan.map((option) => option.value)).toEqual(['http://192.168.1.20:8790', 'http://127.0.0.1'])
  })

  it('curl 示例：OpenAI 含 /v1、Claude 不含 /v1，模型名自动填入', () => {
    const examples = buildCurlExamples('http://127.0.0.1:8790', 'deepseek-v4.1-flash-workbuddy')
    expect(examples.openai).toContain('http://127.0.0.1:8790/v1/chat/completions')
    expect(examples.openai).toContain('"model":"deepseek-v4.1-flash-workbuddy"')
    expect(examples.claude).toContain('curl http://127.0.0.1:8790/v1/messages')
    expect(examples.claude).not.toContain('/v1/v1/')
    expect(examples.audioCurl).toContain('/v1/audio/transcriptions')
    expect(examples.audioCurl).toContain('workbuddy-asr')
    expect(examples.videoCurl).toContain('/v1/videos')
    expect(buildCurlExamples('http://h:1').openai).toContain('"model":"auto"')
  })
})

describe('用量与来源 IP 抽屉', () => {
  it('用量四格与着色', () => {
    const view = toUsageView({
      id: 7, name: 'k', credit_limit: 100, credit_used: 96, credit_remaining: 4,
      usage_percent: 96, unlimited: false, status: 'active', created_at: '2026-11-20T09:12:33',
    })
    expect(view.limitText).toBe('100')
    expect(view.usedText).toBe('96')
    expect(view.remainingText).toBe('4')
    expect(view.percentText).toBe('96.0%')
    expect(view.tone).toBe('danger')
    expect(view.unlimited).toBe(false)

    const unlimited = toUsageView({
      id: 8, name: 'u', credit_limit: 0, credit_used: 5, credit_remaining: null,
      usage_percent: null, unlimited: true, status: 'active', created_at: null,
    })
    expect(unlimited.limitText).toContain('∞')
    expect(unlimited.remainingText).toBe('∞')
    expect(unlimited.percentText).toBe('N/A')
    expect(unlimited.createdText).toBe(FMT_EMPTY)
  })

  it('来源 IP 汇总与行', () => {
    const payload: KeyIpsPayload = {
      id: 7, name: 'k', days: 1, sort: 'requests', direction: 'desc', offset: 0, limit: 100,
      total_ips: 2, total_requests: 30, active_ips: 1, active_requests: 2, active_scope: '当前服务进程',
      active: [], items: [
        { ip: '10.0.0.8', requests: 20, failed: 1, credits: 3.5, tokens: 1200, last_seen: '2026-11-23T03:00:00',
          models: [{ model: 'glm-5.3-flash', requests: 20, tokens: 1200, last_seen: null, recent: true }],
          active_requests: 2, blocked: false, blocked_id: null },
        { ip: '10.0.0.9', requests: 10, failed: 0, credits: 1, tokens: 300, last_seen: null,
          models: [], active_requests: 0, blocked: true, blocked_id: 3 },
      ],
    }
    const view = toIpOverview(payload)
    expect(view.totalIps).toBe(2)
    expect(view.requests).toBe(30)
    expect(view.rows[0].models).toBe('glm-5.3-flash')
    expect(view.rows[0].lastSeen).not.toBe(FMT_EMPTY)
    expect(view.rows[1].lastSeen).toBe(FMT_EMPTY)
    expect(view.rows[1].blocked).toBe(true)
  })

  it('来源 IP 空态：汇总归零、无明细（切 Key / 取数失败时先清空）', () => {
    const empty = emptyIpOverview()
    expect(empty).toEqual({ totalIps: 0, requests: 0, activeIps: 0, activeRequests: 0, rows: [] })
  })
})