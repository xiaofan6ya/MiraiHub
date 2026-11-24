/**
 * 第 5 轮 i18n 首屏瘦身：只有 zh-CN 静态进入口，en-US 按需加载。
 *
 *   1. 入口源码不再静态 import en-US（构建时它才会成为独立 chunk）；
 *   2. 未加载的语言：setLocale 先加载完再切，加载期间界面保持原语言（不闪键名）；
 *   3. 加载期间再切回中文，以最后一次为准；
 *   4. 存的是 en-US 时，模块初始为中文，initLocale() resolve 后即英文（main.ts 等它再挂载）；
 *   5. registerMessages 注入后 setLocale 同步生效（测试 / SSR 用；tests/setup.ts 已注入）。
 *
 * 用 vi.resetModules() 拿一份「没被 setup 注入过」的新 i18n 模块实例。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import indexSource from '@/i18n/index.ts?raw'
import { enUS } from '@/i18n/messages/en-US'

type I18nModule = typeof import('@/i18n')

async function freshI18n(): Promise<I18nModule> {
  vi.resetModules()
  return import('@/i18n')
}

afterEach(() => {
  localStorage.clear()
  document.documentElement.lang = ''
})

describe('i18n 按需加载', () => {
  it('入口只静态引用 zh-CN，en-US 走动态 import', () => {
    expect(indexSource).not.toMatch(/^\s*import\s[^\n]*['"]\.\/messages\/en-US['"]/m)
    expect(indexSource).toMatch(/import\(\s*['"]\.\/messages\/en-US['"]\s*\)/)
  })

  it('未加载的语言：先加载完再切，期间保持原语言', async () => {
    const i18n = await freshI18n()
    expect(i18n.isLocaleLoaded('zh-CN')).toBe(true)
    expect(i18n.isLocaleLoaded('en-US')).toBe(false)
    expect(i18n.messageCount('en-US')).toBe(0)

    const task = i18n.setLocale('en-US')
    expect(i18n.currentLocale.value).toBe('zh-CN')
    expect(i18n.pendingLocale.value).toBe('en-US')
    expect(i18n.t('common.save')).toBe('保存')

    await task
    expect(i18n.currentLocale.value).toBe('en-US')
    expect(i18n.pendingLocale.value).toBeNull()
    expect(i18n.t('common.save')).toBe('Save')
    expect(localStorage.getItem(i18n.STORAGE_KEY)).toBe('en-US')
    expect(document.documentElement.lang).toBe('en-US')
    expect(i18n.messageCount('en-US')).toBe(Object.keys(enUS).length)

    // 已加载：之后的切换是同步的
    void i18n.setLocale('zh-CN')
    expect(i18n.t('common.save')).toBe('保存')
    void i18n.setLocale('en-US')
    expect(i18n.t('common.save')).toBe('Save')
  })

  it('加载期间又切回中文：以最后一次为准', async () => {
    const i18n = await freshI18n()
    const toEn = i18n.setLocale('en-US')
    void i18n.setLocale('zh-CN')
    expect(i18n.pendingLocale.value).toBeNull()
    await toEn
    expect(i18n.currentLocale.value).toBe('zh-CN')
    expect(i18n.t('common.save')).toBe('保存')
    expect(localStorage.getItem(i18n.STORAGE_KEY)).toBe('zh-CN')
  })

  it('存的是 en-US：模块初始为中文，initLocale 完成后即英文', async () => {
    localStorage.setItem('mh.locale', 'en-US')
    const i18n = await freshI18n()
    expect(i18n.currentLocale.value).toBe('zh-CN')
    await i18n.initLocale()
    expect(i18n.currentLocale.value).toBe('en-US')
    expect(i18n.t('common.save')).toBe('Save')
    expect(document.documentElement.lang).toBe('en-US')
  })

  it('默认中文时 initLocale 立即 resolve，不加载 en-US', async () => {
    const i18n = await freshI18n()
    await i18n.initLocale()
    expect(i18n.currentLocale.value).toBe('zh-CN')
    expect(i18n.isLocaleLoaded('en-US')).toBe(false)
  })

  it('registerMessages 注入后 setLocale 同步生效', async () => {
    const i18n = await freshI18n()
    i18n.registerMessages('en-US', enUS)
    void i18n.setLocale('en-US')
    expect(i18n.t('common.save')).toBe('Save')
    expect(i18n.pendingLocale.value).toBeNull()
  })
})
