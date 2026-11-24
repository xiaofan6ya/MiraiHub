/**
 * S15 用量日志：接口适配 / 数据转换纯函数单测。
 * 响应形状按 admin/routers/logs.py 与 admin/request_payload.py 的真实返回构造。
 */
import { describe, expect, it } from 'vitest'
import {
  beijingMidnightIso,
  buildCurl,
  buildExportQuery,
  buildLiveBars,
  buildLogQuery,
  buildSummaryQuery,
  cacheRateOf,
  captureStatusText,
  liveHintText,
  liveStatusLabel,
  liveTone,
  resolveTimeWindow,
  summaryHintText,
  summaryStatText,
  tpsOf,
  toAccountSummaryRows,
  toLogRow,
  toLogRows,
  useCaseLabel,
  type CaptureSettings,
  type LogFilterState,
  type UsageLogDetail,
  type UsageLogRow,
  type UsageLogSummaryPayload,
} from '@/api/logs'

const NOW = Date.parse('2026-03-10T04:00:00Z')
/** 北京时间 2026-03-10 12:00 → 今日起点 2026-03-09T16:00:00.000Z */
const TODAY_START = '2026-03-09T16:00:00.000Z'

const STATE: LogFilterState = {
  platform: 'trae',
  account: '',
  account_id: '12',
  model: 'glm-5.2',
  key: '生产 Key',
  result: 'error',
  ip: '203.0.113.7',
  range: 'today',
  start: '',
  end: '',
  fast: '',
}

/** GET /api/logs 的 items[]（_serialize 真实字段）。 */
const ROW: UsageLogRow = {
  id: 118230,
  key_name: '生产 Key',
  key_id: 3,
  account_id: 12,
  account_name: '小帆呀',
  platform: 'trae',
  model: 'deepseek-v4.1-flash',
  model_raw: 'main-auto',
  credits: 9.55,
  prompt_tokens: 1200,
  completion_tokens: 300,
  total_tokens: 1500,
  cached_tokens: 960,
  client_ip: '203.0.113.7',
  use_case: 'trae-chat-completions',
  reasoning_effort: 'medium',
  seq: 42,
  ttfb_ms: 850,
  latency_ms: 3850,
  error_kind: '',
  created_at: '2026-03-10T04:00:00',
}

const ERROR_ROW: UsageLogRow = {
  ...ROW,
  id: 118229,
  error_kind: 'upstream_5xx',
  prompt_tokens: 0,
  completion_tokens: 0,
  total_tokens: 0,
  cached_tokens: null,
  credits: 0,
  ttfb_ms: null,
  latency_ms: null,
}

describe('时间窗口（今日 = 北京时间自然日）', () => {
  it('beijingMidnightIso：北京时间 12:00 的今日起点是前一天 16:00Z', () => {
    expect(beijingMidnightIso(NOW)).toBe(TODAY_START)
  })

  it('显式日期优先于 range：起始 00:00:00 / 结束 23:59:59', () => {
    expect(resolveTimeWindow({ range: 'today', start: '2026-03-01', end: '2026-03-02' }, NOW)).toEqual({
      start: '2026-03-01 00:00:00',
      end: '2026-03-02 23:59:59',
    })
  })

  it('今日 / 近 1 小时 / 近 3 天 / 不限', () => {
    expect(resolveTimeWindow({ range: 'today', start: '', end: '' }, NOW).start).toBe(TODAY_START)
    expect(resolveTimeWindow({ range: 'hour', start: '', end: '' }, NOW).start).toBe(
      new Date(NOW - 3600 * 1000).toISOString(),
    )
    expect(resolveTimeWindow({ range: '3', start: '', end: '' }, NOW).start).toBe(
      new Date(NOW - 3 * 86400000).toISOString(),
    )
    expect(resolveTimeWindow({ range: '0', start: '', end: '' }, NOW)).toEqual({ start: '', end: '' })
  })
})

describe('buildLogQuery（keyset 优先，第一页且非快速模式才统计总数）', () => {
  it('全部筛选映射到后端参数', () => {
    const { query } = buildLogQuery(STATE, { page: 1, pageSize: 20, now: NOW })
    expect(query).toMatchObject({
      page: 1,
      page_size: 20,
      platform: 'trae',
      account_id: 12,
      model: 'glm-5.2',
      key: '生产 Key',
      client_ip: '203.0.113.7',
      result: 'error',
      start: TODAY_START,
      with_total: true,
    })
  })

  it('快速翻页不统计总数；第 2 页带 before_id 时也不统计', () => {
    expect(buildLogQuery({ ...STATE, fast: '1' }, { page: 1, pageSize: 50, now: NOW }).query.with_total).toBeUndefined()
    const second = buildLogQuery(STATE, { page: 2, pageSize: 20, beforeId: 118200, now: NOW }).query
    expect(second.before_id).toBe(118200)
    expect(second.with_total).toBeUndefined()
    expect(second.page).toBe(2)
  })

  it('空筛选不下发空串参数', () => {
    const empty: LogFilterState = {
      platform: '', account: '', account_id: '', model: '', key: '', result: '', ip: '',
      range: '0', start: '', end: '', fast: '',
    }
    const { query } = buildLogQuery(empty, { page: 1, pageSize: 20, now: NOW })
    expect(query.platform).toBeUndefined()
    expect(query.account_id).toBeUndefined()
    expect(query.start).toBeUndefined()
    expect(query.with_total).toBe(true)
  })
})

describe('汇总 / 导出查询', () => {
  it('汇总不带 platform（后端 log_summary 没有该参数），今日传 days=0 + start', () => {
    const query = buildSummaryQuery(STATE, NOW)
    expect(query.platform).toBeUndefined()
    expect(query.days).toBe(0)
    expect(query.start).toBe(TODAY_START)
    expect(query.account_id).toBe(12)
  })

  it('近 7 天用 days 滚动窗口，不带 start', () => {
    const query = buildSummaryQuery({ ...STATE, range: '7' }, NOW)
    expect(query.days).toBe(7)
    expect(query.start).toBeUndefined()
  })

  it('导出与列表同口径，但去掉分页参数', () => {
    const query = buildExportQuery(STATE, NOW)
    expect(query.platform).toBe('trae')
    expect(query.start).toBe(TODAY_START)
    expect(query.page).toBeUndefined()
    expect(query.page_size).toBeUndefined()
  })
})

describe('日志行转换 toLogRow', () => {
  it('缓存命中 960/1200 = 80%，TPS = 300 / 3s = 100', () => {
    const row = toLogRow(ROW)
    expect(row.cacheRate).toBe(80)
    expect(row.cacheRateText).toBe('80.00%')
    expect(row.tpsText).toBe('100.0')
    expect(row.ttfbText).toBe('850ms')
    expect(row.latencyText).toBe('3.85s')
    expect(row.resultTone).toBe('ok')
    expect(row.resultLabel).toBe('成功')
    expect(row.useCaseLabel).toBe('对话')
    expect(row.tokensText).toBe('入 1,200 · 出 300 · 缓存 960')
  })

  it('失败行：Badge danger，error_kind 作为文案；没有缓存字段时命中率为 —', () => {
    const row = toLogRow(ERROR_ROW)
    expect(row.isError).toBe(true)
    expect(row.resultLabel).toBe('upstream_5xx')
    expect(row.resultTone).toBe('danger')
    expect(row.cacheRate).toBeNull()
    expect(row.cacheRateText).toBe('—')
    expect(row.tpsText).toBe('—')
    expect(row.ttfbText).toBe('—')
  })

  it('toLogRows 容错 null', () => {
    expect(toLogRows(null)).toEqual([])
    expect(toLogRows([ROW])).toHaveLength(1)
  })
})

describe('缓存命中率与 TPS 的边界', () => {
  it('没有输入 token / cached_tokens 为 null → null（不能显示 0%）', () => {
    expect(cacheRateOf({ prompt_tokens: 0, cached_tokens: 0 })).toBeNull()
    expect(cacheRateOf({ prompt_tokens: 100, cached_tokens: null })).toBeNull()
    expect(cacheRateOf({ prompt_tokens: 100, cached_tokens: 0 })).toBe(0)
    expect(cacheRateOf(null)).toBeNull()
  })

  it('解码段 < 300ms 或 > 300 tok/s 视为不可信', () => {
    expect(tpsOf({ completion_tokens: 100, latency_ms: 1100, ttfb_ms: 900 })).toBeNull()
    expect(tpsOf({ completion_tokens: 100, latency_ms: 1300, ttfb_ms: 900 })).toBe(250)
    expect(tpsOf({ completion_tokens: 1000, latency_ms: 1300, ttfb_ms: 900 })).toBeNull()
  })

  it('用途列短名：平台前缀被去掉，未知值原样', () => {
    expect(useCaseLabel('qoder-completions')).toBe('补全')
    expect(useCaseLabel('anthropic-messages')).toBe('消息')
    expect(useCaseLabel('')).toBe('—')
    expect(useCaseLabel('custom-purpose')).toBe('custom-purpose')
  })
})

describe('按账号汇总转换', () => {
  const items: UsageLogSummaryPayload['items'] = [
    {
      account_id: 0, account_name: '未记录账号 / 未选号', requests: 12, failed_requests: 4, ok_requests: 8,
      credits: 0.5, total_tokens: 900, prompt_tokens: 600, completion_tokens: 300, cached_tokens: 0,
      avg_latency_ms: 2400, last_used_at: '2026-03-10T03:00:00',
    },
    {
      account_id: 12, account_name: '小帆呀', requests: 850, failed_requests: 0, ok_requests: 850,
      credits: 729.31, total_tokens: 120000, prompt_tokens: 80000, completion_tokens: 40000,
      cached_tokens: 64000, avg_latency_ms: 3950, last_used_at: '2026-03-10T03:59:00',
    },
  ]

  it('按积分倒序，未记录账号固定最后，命中率与平均耗时格式化', () => {
    const rows = toAccountSummaryRows(items)
    expect(rows.map((row) => row.account_id)).toEqual([12, 0])
    expect(rows[0].cacheRateText).toBe('80.00%')
    expect(rows[0].avgLatencyText).toBe('3.95s')
    expect(rows[1].unattributed).toBe(true)
  })

  it('汇总文案：账号数 / 成功失败 / 积分 / 未记录次数', () => {
    const payload: UsageLogSummaryPayload = {
      range_days: 1, total_requests: 862, ok_requests: 850, failed_requests: 12,
      account_count: 1, unattributed_requests: 12, total_credits: 729.81, total_tokens: 120900,
      window_minutes: 720, items,
    }
    expect(summaryHintText(payload)).toBe('1 个账号 · 成功 850 / 错误 12 · 729.81 积分 · 12 次未记录账号 / 未选号')
    expect(summaryStatText(payload)).toBe('当前筛选共 862 次 · 成功 850 · 错误 12')
    expect(summaryHintText(null)).toBe('')
  })
})

describe('请求详情记录状态行', () => {
  const settings: CaptureSettings = {
    enabled: false, ttl_seconds: 3600, max_cache_bytes: 64 * 1024 * 1024, max_entries: 200,
    cached_requests: 0, cache_bytes: 0, active_bytes: 0, record_max_bytes: 8 * 1024 * 1024,
    body_max_bytes: 8 * 1024 * 1024, storage: 'process_memory', restart_clears: true,
    source: 'settings (live)',
  }

  it('关闭状态：0/200 条 · 0.0 MiB · 单条 8 MiB · 60 分钟过期', () => {
    expect(captureStatusText(settings)).toBe(
      '已关闭 · 缓存 0/200 条 · 0.0 MiB · 单条上限 8 MiB · 60 分钟过期 · 重启清空',
    )
  })

  it('开启且有缓存：条数与 MiB 跟随真实字段', () => {
    expect(captureStatusText({ ...settings, enabled: true, cached_requests: 3, cache_bytes: 1.5 * 1024 * 1024 })).toBe(
      '已开启 · 缓存 3/200 条 · 1.5 MiB · 单条上限 8 MiB · 60 分钟过期 · 重启清空',
    )
    expect(captureStatusText(null)).toBe('读取开关状态…')
  })
})

describe('请求动态聚合 buildLiveBars', () => {
  const recent = [
    { request_id: 1, key_id: 1, account_id: 12, client_ip: '', path: '/v1/chat/completions', model: 'glm-5.2', started_at: '2026-03-10T03:59:10Z', status: 200, outcome: 'success', elapsed_ms: 1200, phase: 'done', reason: '', upstream_attempts: 1 },
    { request_id: 2, key_id: 1, account_id: 12, client_ip: '', path: '/v1/chat/completions', model: 'glm-5.2', started_at: '2026-03-10T03:59:40Z', status: 502, outcome: 'error', elapsed_ms: 900, phase: 'done', reason: 'upstream', upstream_attempts: 1 },
    { request_id: 3, key_id: 1, account_id: 12, client_ip: '', path: '/v1/chat/completions', model: 'glm-5.2', started_at: '2026-03-10T04:00:05Z', status: null, outcome: 'running', elapsed_ms: 300, phase: 'upstream', reason: '', upstream_attempts: 0 },
  ]

  it('按分钟分桶（含空分钟），错误单独计数', () => {
    const bars = buildLiveBars(recent, Date.parse('2026-03-10T04:00:30Z'), 3)
    expect(bars).toHaveLength(3)
    // 标签按本地时区（图表口径）；断言与运行测试的机器时区无关
    const hm = (iso: string) => {
      const date = new Date(iso)
      return String(date.getHours()).padStart(2, '0') + ':' + String(date.getMinutes()).padStart(2, '0')
    }
    expect(bars.map((bar) => bar.label)).toEqual([
      hm('2026-03-10T03:58:00Z'),
      hm('2026-03-10T03:59:00Z'),
      hm('2026-03-10T04:00:00Z'),
    ])
    expect(bars[1]).toMatchObject({ requests: 2, errors: 1 })
    expect(bars[2]).toMatchObject({ requests: 1, errors: 0 })
    expect(bars[0]).toMatchObject({ requests: 0, errors: 0 })
  })

  it('状态文案与色调：进行中按阶段、超时、200 中断、错误', () => {
    const running = recent[2]
    expect(liveStatusLabel(running)).toBe('转发上游')
    expect(liveTone(running)).toBe('brand')
    expect(liveTone(recent[1])).toBe('danger')
    expect(liveTone({ ...recent[1], status: 200, outcome: 'interrupted' })).toBe('warn')
    expect(liveStatusLabel({ ...recent[2], outcome: 'timeout', phase: 'done' })).toBe('超时')
    expect(liveHintText({ active: [], active_count: 2, recent, usage_revision: 1, mode: 'stream' })).toBe(
      '进行中 2 个 · 最近 15 分钟 3 条',
    )
  })
})

describe('详情：复制 curl', () => {
  const detail: UsageLogDetail = {
    ...ROW,
    request_capture: {
      captured_at: '2026-03-10T04:00:00+00:00',
      ingress: {
        headers: {
          'content-type': 'application/json',
          authorization: '[REDACTED]',
          'user-agent': 'WorkBuddy/1.0',
          _truncated: 'true',
        },
        body_bytes: 120,
        body_cached_bytes: 120,
        body_truncated: false,
      },
      upstream: [
        {
          attempt: 1, method: 'POST', url: 'https://api.example.com/v1/chat/completions',
          query_keys: ['beta'], account_id: 12, dispatch_at: null, http_status: 200,
          body_available: true, headers: {}, body_bytes: 10, body_cached_bytes: 10, body_truncated: false,
        },
      ],
      upstream_omitted: 0,
    },
    diagnostic: null,
  }

  it('输出 method/url 与未脱敏请求头，跳过 [REDACTED] 与 _ 前缀', () => {
    const curl = buildCurl(detail, '{"model":"glm-5.2"}')
    expect(curl).toContain("curl -X POST 'https://api.example.com/v1/chat/completions'")
    expect(curl).toContain("-H 'content-type: application/json'")
    expect(curl).toContain("-H 'user-agent: WorkBuddy/1.0'")
    expect(curl).not.toContain('authorization')
    expect(curl).not.toContain('_truncated')
    expect(curl).toContain('--data-raw ' + "'" + '{"model":"glm-5.2"}' + "'")
  })

  it('单引号按 shell 规则转义；没有正文时留注释说明', () => {
    const curl = buildCurl(detail, "it's ok")
    expect(curl).toContain("--data-raw 'it'\\''s ok'")
    expect(buildCurl(detail)).toContain('正文未读取')
  })

  it('没有请求缓存时返回空串', () => {
    expect(buildCurl({ ...detail, request_capture: null })).toBe('')
    expect(buildCurl(null)).toBe('')
  })
})