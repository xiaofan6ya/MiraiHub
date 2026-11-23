import { describe, expect, it } from 'vitest'
import {
  diffRuntimeFields,
  filterProxies,
  groupRuntimeFields,
  proxyStateOf,
  proxySummaryText,
  restartPendingOf,
  sourceStatusLines,
  unpackPercent,
  validatePasswordChange,
  validateProxyInput,
  type ProxyNode,
  type RuntimeField,
} from '@/api/settings'

/**
 * 契约 docs/ui-v2/contracts/settings.md 末尾「## 实现对照（S28）」的数据转换。
 * 测试数据按后端真实响应形状造：
 *   GET /api/runtime-config → admin/runtime_settings.py:snapshot
 *   GET /api/proxies        → admin/routers/proxies.py:list_proxies
 *   GET /api/app-source/status → admin/routers/app_source.py:_status_payload
 */

function field(overrides: Partial<RuntimeField> = {}): RuntimeField {
  return {
    attr: 'GATEWAY_REQUEST_TIMEOUT',
    label: '网关总时限（秒）',
    group: '网关与超时',
    kind: 'float',
    desc: '单个请求的端到端硬上限',
    min: 1,
    max: null,
    step: 1,
    restart: false,
    value: 60,
    default: 60,
    overridden: false,
    ...overrides,
  }
}

function proxy(overrides: Partial<ProxyNode> = {}): ProxyNode {
  return {
    id: 1,
    label: '机房A-01',
    url: 'http://user:***@10.0.0.1:8080',
    enabled: true,
    max_concurrency: 2,
    pool: '机房',
    blocked: false,
    blocked_until: null,
    last_ok_at: null,
    last_error: '',
    state: 'ready',
    ...overrides,
  }
}

describe('运行配置：分组 / 改动 diff / 重启横幅', () => {
  const fields: RuntimeField[] = [
    field(),
    field({ attr: 'MAX_ROTATE', label: '单请求最多换号次数', kind: 'int', value: 3, default: 3 }),
    field({ attr: 'STICKY_ENABLED', label: '会话粘性路由', group: '并发与账号池', kind: 'bool', value: true, default: true }),
    field({ attr: 'UPSTREAM_CLIENT_NAME', label: '上游 client 列产品名', group: '记录与上游', kind: 'str', value: '', default: '' }),
  ]

  it('按后端 group 分组且保持 CATALOG 顺序', () => {
    const groups = groupRuntimeFields(fields)
    expect(groups.map((item) => item.group)).toEqual(['网关与超时', '并发与账号池', '记录与上游'])
    expect(groups[0].items.map((item) => item.attr)).toEqual(['GATEWAY_REQUEST_TIMEOUT', 'MAX_ROTATE'])
    expect(groupRuntimeFields(null)).toEqual([])
  })

  it('diff 只返回真正变化的字段（类型按 kind 比较）', () => {
    const draft = {
      GATEWAY_REQUEST_TIMEOUT: 90,
      MAX_ROTATE: '3',
      STICKY_ENABLED: false,
      UPSTREAM_CLIENT_NAME: '',
    }
    const updates = diffRuntimeFields(fields, draft)
    expect(updates).toEqual({ GATEWAY_REQUEST_TIMEOUT: 90, STICKY_ENABLED: false })
  })

  it('清空字符串 = 回落 .env 默认（提交空串）', () => {
    const withValue = fields.map((item) =>
      item.attr === 'UPSTREAM_CLIENT_NAME' ? { ...item, value: 'miraihub', overridden: true } : item,
    )
    expect(diffRuntimeFields(withValue, { UPSTREAM_CLIENT_NAME: '' })).toEqual({ UPSTREAM_CLIENT_NAME: '' })
  })

  it('重启横幅只统计 changed ∩ restart 字段', () => {
    const restartField = field({ attr: 'ENABLE_DOCS', label: '开放 API 文档', restart: true, value: false, kind: 'bool' })
    const list = [...fields, restartField]
    expect(restartPendingOf(list, ['GATEWAY_REQUEST_TIMEOUT', 'ENABLE_DOCS'])).toEqual([restartField])
    expect(restartPendingOf(list, ['GATEWAY_REQUEST_TIMEOUT'])).toEqual([])
    expect(restartPendingOf(null, ['X'])).toEqual([])
  })
})

describe('修改登录密码：前端只拦能提前发现的两件事', () => {
  it('两次不一致 / 少于 6 位 / 新旧相同', () => {
    expect(validatePasswordChange('old-pass', 'new-pass', 'new-pass')).toBe('')
    expect(validatePasswordChange('old-pass', '12345', '12345')).toBe('新密码长度不能少于 6 位')
    expect(validatePasswordChange('old-pass', 'new-pass', 'new-pas')).toBe('两次输入的新密码不一致')
    expect(validatePasswordChange('old-pass', 'old-pass', 'old-pass')).toBe('新密码不能与当前密码相同')
    expect(validatePasswordChange('', 'new-pass', 'new-pass')).toBe('请输入当前密码')
  })
})

describe('出口代理节点：状态 / 摘要 / 过滤 / 表单校验', () => {
  it('状态文案与语气', () => {
    expect(proxyStateOf(proxy())).toEqual({ label: '可用', tone: 'ok' })
    expect(proxyStateOf(proxy({ state: 'blocked' }))).toEqual({ label: '静默中', tone: 'warn' })
    expect(proxyStateOf(proxy({ state: 'disabled', enabled: false }))).toEqual({ label: '已停用', tone: 'neutral' })
    expect(proxyStateOf(null)).toEqual({ label: '已停用', tone: 'neutral' })
  })

  it('摘要文字（与旧版面板同口径）', () => {
    expect(
      proxySummaryText({ total: 3, ready: 1, blocked: 1, disabled: 1, slots: 4 }),
    ).toBe('共 3 个节点，可用 1，静默 1，停用 1 · 可并发槽位 4')
    expect(proxySummaryText(null)).toBe('')
  })

  it('按分组与关键字过滤（名称/地址/分组）', () => {
    const nodes = [proxy(), proxy({ id: 2, label: '住宅-上海', pool: '住宅', url: 'socks5://u:***@1.2.3.4:1080' })]
    expect(filterProxies(nodes, { pool: '机房' }).map((item) => item.id)).toEqual([1])
    expect(filterProxies(nodes, { q: '上海' }).map((item) => item.id)).toEqual([2])
    expect(filterProxies(nodes, { q: 'socks' }).map((item) => item.id)).toEqual([2])
    expect(filterProxies(nodes, {})).toHaveLength(2)
  })

  it('表单校验：空 / 太短 / 打码值 / 协议 / 并发范围', () => {
    expect(validateProxyInput({ url: '' })).toBe('请填写代理地址')
    expect(validateProxyInput({ url: 'http://' })).toBe('代理地址至少 8 个字符')
    expect(validateProxyInput({ url: 'http://user:***@host:80' })).toContain('打码后的展示值')
    expect(validateProxyInput({ url: 'ftp://host:21' })).toContain('必须以 http://')
    expect(validateProxyInput({ url: 'env:PROXY_MAIN' })).toBe('')
    expect(validateProxyInput({ url: 'socks5://host:1080', max_concurrency: 40 })).toContain('1–32')
    expect(validateProxyInput({ url: 'http://user:pass@host:8080', max_concurrency: 2 })).toBe('')
  })
})

describe('逆向产物状态', () => {
  it('状态行字段与空值占位', () => {
    const lines = sourceStatusLines({
      install_dir: 'D:\\WorkBuddy',
      asar: 'D:\\WorkBuddy\\resources\\app.asar',
      source_dir: '',
      python: '3.14.0',
      node: '',
      can_unpack: true,
    })
    expect(lines.map((line) => line.label)).toEqual([
      '客户端安装目录', 'app.asar', '逆向产物目录', 'Python / Node', '可拆包',
    ])
    expect(lines[2].value).toBe('—')
    expect(lines[3].value).toBe('3.14.0 / —')
    expect(lines[4].value).toBe('是')
    expect(sourceStatusLines(null)).toEqual([])
  })

  it('拆包进度百分比优先 percent，其次 done/total', () => {
    expect(unpackPercent({ percent: 42.6 })).toBe(43)
    expect(unpackPercent({ done: 3, total: 4 })).toBe(75)
    expect(unpackPercent({ percent: 130 })).toBe(100)
    expect(unpackPercent(null)).toBe(0)
  })
})
