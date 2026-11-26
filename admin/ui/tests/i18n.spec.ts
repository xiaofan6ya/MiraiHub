/**
 * 轻量 i18n 的核心契约：
 *   1. 中英字典 key 完全对齐（en-US 用 Record<MessageKey, string> 在类型层也保证）；
 *   2. t() 插值 / 缺省回退 / 切换后立即生效；
 *   3. 语言写入 localStorage `mh.locale`，<html lang> 跟随；
 *   4. 三个代表页的数据文案（overview / accounts / settings）带 translate 参数后能出英文。
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { enUS } from '@/i18n/messages/en-US'
import { zhCN } from '@/i18n/messages/zh-CN'
import {
  STORAGE_KEY,
  groupTitle,
  initLocale,
  messageCount,
  pageTitle,
  resolveInitialLocale,
  setLocale,
  t,
} from '@/i18n'
import {
  buildBalanceHero,
  buildMonitorRows,
  daysLabel,
  monitorConclusion,
} from '@/api/overview'
import { toAccountRows, jobDoneText, type AccountItem } from '@/api/accounts'
import { proxyStateOf, validateProxyInput, validatePasswordChange, sourceStatusLines } from '@/api/settings'

beforeEach(() => {
  setLocale('zh-CN')
})

afterEach(() => {
  setLocale('zh-CN')
  localStorage.clear()
})

describe('字典', () => {
  it('中英 key 完全一致，且没有空词条', () => {
    const zhKeys = Object.keys(zhCN).sort()
    const enKeys = Object.keys(enUS).sort()
    expect(enKeys).toEqual(zhKeys)
    expect(messageCount('zh-CN')).toBe(zhKeys.length)
    expect(messageCount('en-US')).toBe(zhKeys.length)
    for (const key of zhKeys) {
      expect(zhCN[key as keyof typeof zhCN].trim(), key).not.toBe('')
      expect(enUS[key as keyof typeof enUS].trim(), key).not.toBe('')
    }
  })
})

describe('t() / 持久化 / <html lang>', () => {
  it('默认中文，插值替换 {name}', () => {
    expect(t('common.totalItems', { count: 3 })).toBe('共 3 项')
    expect(t('shell.accountsExpiring', { count: 2 })).toBe('有 2 个账号积分即将到期')
  })

  it('缺参数时保留占位符，未知 key 原样返回（不渲染 undefined）', () => {
    expect(t('common.totalItems')).toBe('共 {count} 项')
    expect(t('not.a.real.key' as Parameters<typeof t>[0])).toBe('not.a.real.key')
  })

  it('setLocale 立即生效、写 localStorage、同步 document.lang', () => {
    setLocale('en-US')
    expect(t('common.save')).toBe('Save')
    expect(localStorage.getItem(STORAGE_KEY)).toBe('en-US')
    expect(document.documentElement.lang).toBe('en-US')

    setLocale('zh-CN')
    expect(t('common.save')).toBe('保存')
    expect(document.documentElement.lang).toBe('zh-CN')
  })

  it('坏值回落中文；initLocale 把当前语言写到 <html lang>', () => {
    expect(resolveInitialLocale('fr-FR')).toBe('zh-CN')
    expect(resolveInitialLocale(null)).toBe('zh-CN')
    expect(resolveInitialLocale('en-US')).toBe('en-US')
    setLocale('en-US')
    initLocale()
    expect(document.documentElement.lang).toBe('en-US')
  })
})

describe('导航与代表页文案', () => {
  it('24 页 / 9 组的标题跟随语言', () => {
    expect(pageTitle('accounts')).toBe('账号池')
    expect(pageTitle('settings')).toBe('设置')
    expect(groupTitle('usage')).toBe('调用')
    setLocale('en-US')
    expect(pageTitle('accounts')).toBe('Accounts')
    expect(pageTitle('settings')).toBe('Settings')
    expect(groupTitle('usage')).toBe('Traffic')
  })

  it('overview：英雄卡 / 用量条 / 账号结论 / 告警都出英文', () => {
    const accounts = { total: 3, balance_remain: 12954, balance_total: 17583, disabled_balance: 125, used_ratio: null }
    const today = { requests: 10, ok_requests: 9, prompt_tokens: 1, completion_tokens: 1, total_tokens: 2, cached_tokens: 0, credits: 9.55 }
    const win = { ...today, requests: 20, credits: 198.91, days: 7 }
    const monitor = { active: 3, cooling: 0, breaker: 0, disabled: 0, no_balance: 0, total: 3, items: [] }

    expect(buildBalanceHero(accounts, today, win, 66.3).caption).toBe('总额度 17,583 · 3 个账号')
    setLocale('en-US')
    const hero = buildBalanceHero(accounts, today, win, 66.3)
    expect(hero.caption).toBe('Total 17,583 · accounts: 3')
    expect(hero.note).toContain('disabled accounts')
    expect(hero.rows.map((row) => row.label)).toEqual(['Today', '7 days', 'Avg'])
    expect(daysLabel(30)).toBe('Last 30 days')
    expect(buildMonitorRows(monitor)[0].label).toBe('Active')
    expect(monitorConclusion(monitor)).toEqual({ text: 'All accounts healthy', tone: 'ok', to: '' })
  })

  it('accounts：状态 Badge / 任务文案出英文', () => {
    const item = {
      id: 1,
      name: '主号',
      uid: 'u-1',
      platform: 'workbuddy',
      domain: '',
      status: 'active',
      gateway_state: 'cooling',
      gateway_reason: 'breaker',
      gateway_until: '2026-11-23T06:00:00',
      balance_remain: 100,
      balance_total: 200,
      credits_soonest_expire_at: null,
      last_used_at: null,
      bound_key_name: '主 Key',
      token_supported: true,
      token_state: 'ok',
      token_days_left: 12,
    } as unknown as AccountItem

    const zhRow = toAccountRows([item])[0]
    expect(zhRow.statusLabel).toBe('熔断')
    expect(zhRow.tokenText).toBe('令牌 12 天')
    expect(zhRow.note).toBe('绑定 Key：主 Key')

    setLocale('en-US')
    const enRow = toAccountRows([item])[0]
    expect(enRow.statusLabel).toBe('Tripped')
    expect(enRow.statusTitle).toContain('Breaker open')
    expect(enRow.tokenText).toBe('Token 12 days')
    expect(enRow.note).toBe('Bound key: 主 Key')
    expect(jobDoneText({ job_id: 'j1', status: 'running', total: 3, done: 1 } as never)).toBe(
      'Job finished: 1/3',
    )
  })

  it('settings：代理状态 / 表单校验 / 逆向产物行出英文', () => {
    expect(proxyStateOf({ state: 'blocked' } as never)).toEqual({ label: '静默中', tone: 'warn' })
    setLocale('en-US')
    expect(proxyStateOf({ state: 'blocked' } as never)).toEqual({ label: 'Throttled', tone: 'warn' })
    expect(validateProxyInput({ url: '' })).toBe('Enter the proxy address')
    expect(validatePasswordChange('', 'x', 'x')).toBe('Enter the current password')
    expect(sourceStatusLines({ can_unpack: true } as never)[4]).toEqual({
      label: 'Unpackable',
      value: 'Yes',
    })
  })
})
