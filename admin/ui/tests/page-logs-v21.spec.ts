/**
 * 用量日志 v2.1 视觉精修：新增纯函数（诊断结构化 / KPI / 徽章语气）单测。
 * 诊断形状按 admin/request_metrics.py:usage_evidence + mark_account_policy + mark_network_error 构造。
 */
import { describe, expect, it } from 'vitest'
import {
  diagnosticView,
  httpStatusTone,
  knownErrorKind,
  niceCeil,
  successTone,
  summaryKpis,
  type UsageLogSummaryPayload,
} from '@/api/logs'
import { errorKindText, networkErrorText } from '@/pages/logs/text'
import { t } from '@/i18n'

describe('diagnosticView：把诊断 JSON 拆成可读结构', () => {
  const raw = {
    id: 'req-1',
    status: 502,
    gateway_result_status: 503,
    upstream_status: 429,
    upstream_error: 'soft_rate',
    network_error: 'ReadTimeout',
    upstream_attempts: 3,
    last_account_id: 12,
    last_model: 'glm-5.2',
    connection_recoveries: 1,
    first_delta_ms: null,
    elapsed_ms: 1834.5,
    upstream_request_ids: ['trace-a', '', 'trace-b'],
    account_policy_events: [
      {
        account_id: 12,
        kind: 'soft_rate',
        http_status: 429,
        model: 'glm-5.2',
        upstream_code: 'rate_limited',
        upstream_message: '请求过于频繁',
        cool_until: '2026-03-10T04:05:00Z',
        breaker_until: '2026-03-10T04:01:00Z',
      },
      { account_id: 15, kind: 'hard_credit', http_status: 402 },
    ],
    network_errors: [{ type: 'ReadTimeout', message: 'timed out', phase: 'http11.receive_response_headers', account_id: 15 }],
    stages: [
      { name: 'select_account', ms: 3.2 },
      { name: 'upstream', ms: 1800 },
      { name: '', ms: 5 },
      { name: 'zero', ms: 0 },
    ],
  }

  it('状态码、错误分类、尝试次数、追踪号', () => {
    const view = diagnosticView(raw)!
    expect(view.gatewayStatus).toBe(503)
    expect(view.upstreamStatus).toBe(429)
    expect(view.upstreamError).toBe('soft_rate')
    expect(view.networkError).toBe('ReadTimeout')
    expect(view.attemptCount).toBe(3)
    expect(view.lastAccountId).toBe(12)
    expect(view.recoveries).toBe(1)
    expect(view.firstDeltaMs).toBeNull()
    expect(view.upstreamRequestIds).toEqual(['trace-a', 'trace-b'])
    expect(view.truncated).toBe(false)
  })

  it('尝试记录：账号路由事件在前、网络错误在后，编号连续；冷却时间取最早', () => {
    const view = diagnosticView(raw)!
    expect(view.attempts.map((item) => [item.index, item.source, item.accountId, item.kind])).toEqual([
      [1, 'account', 12, 'soft_rate'],
      [2, 'account', 15, 'hard_credit'],
      [3, 'network', 15, 'ReadTimeout'],
    ])
    expect(view.attempts[0]).toMatchObject({ httpStatus: 429, code: 'rate_limited', message: '请求过于频繁' })
    expect(view.attempts[0].until).toBe('2026-03-10T04:01:00Z')
    expect(view.attempts[2]).toMatchObject({ phase: 'http11.receive_response_headers', message: 'timed out' })
  })

  it('阶段耗时去掉无名与 0ms；没有 gateway_result_status 时退回 status', () => {
    const view = diagnosticView(raw)!
    expect(view.stages).toEqual([
      { name: 'select_account', ms: 3.2 },
      { name: 'upstream', ms: 1800 },
    ])
    expect(diagnosticView({ status: 502 })!.gatewayStatus).toBe(502)
  })

  it('空值与坏形状不抛错', () => {
    expect(diagnosticView(null)).toBeNull()
    const view = diagnosticView({ account_policy_events: 'x', network_errors: [null, 1], stages: {} })!
    expect(view.attempts).toEqual([])
    expect(view.stages).toEqual([])
    expect(view.attemptCount).toBeNull()
  })
})

describe('KPI 与语气', () => {
  const payload: UsageLogSummaryPayload = {
    range_days: 1, total_requests: 200, ok_requests: 190, failed_requests: 10,
    account_count: 3, unattributed_requests: 2, total_credits: 12.5, total_tokens: 1_234_567,
    window_minutes: 720, items: [],
  }

  it('summaryKpis：成功率 = ok / total；没有请求时为 null（不显示 0%）', () => {
    expect(summaryKpis(payload)).toMatchObject({ requests: 200, ok: 190, failed: 10, successRate: 0.95, accounts: 3, unattributed: 2 })
    expect(summaryKpis({ ...payload, total_requests: 0, ok_requests: 0, failed_requests: 0 })!.successRate).toBeNull()
    expect(summaryKpis(null)).toBeNull()
  })

  it('successTone：≥98% ok / ≥90% warn / 其余 danger / 无样本 neutral', () => {
    expect(successTone(0.99)).toBe('ok')
    expect(successTone(0.95)).toBe('warn')
    expect(successTone(0.5)).toBe('danger')
    expect(successTone(null)).toBe('neutral')
  })

  it('httpStatusTone：2xx ok / 4xx warn / 5xx danger / 无效 neutral', () => {
    expect(httpStatusTone(200)).toBe('ok')
    expect(httpStatusTone(429)).toBe('warn')
    expect(httpStatusTone(503)).toBe('danger')
    expect(httpStatusTone(null)).toBe('neutral')
    expect(httpStatusTone(-1)).toBe('neutral')
  })

  it('niceCeil：柱图刻度取 1/2/5×10ⁿ', () => {
    expect(niceCeil(0)).toBe(1)
    expect(niceCeil(3)).toBe(5)
    expect(niceCeil(7)).toBe(10)
    expect(niceCeil(12)).toBe(20)
    expect(niceCeil(48)).toBe(50)
  })
})

describe('错误分类文案（管理员用语，原值留在 tooltip）', () => {
  it('已知分类翻译成人话，未知原样', () => {
    expect(knownErrorKind('SOFT_RATE')).toBe('soft_rate')
    expect(knownErrorKind('weird')).toBeNull()
    expect(errorKindText(t, 'soft_rate')).toBe('上游限流')
    expect(errorKindText(t, 'no_account')).toBe('无可用账号')
    expect(errorKindText(t, 'custom_kind')).toBe('custom_kind')
    expect(errorKindText(t, '')).toBe('')
  })

  it('网络异常类型翻译', () => {
    expect(networkErrorText(t, 'ReadTimeout')).toBe('读取超时')
    expect(networkErrorText(t, 'SomethingElse')).toBe('SomethingElse')
  })
})
