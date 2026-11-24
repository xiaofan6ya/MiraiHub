import { beforeEach } from 'vitest'
import { registerMessages } from '@/i18n'
import { enUS } from '@/i18n/messages/en-US'

// 第 5 轮起 en-US 字典在产物里是按需加载的独立 chunk；测试里同步注入，
// 让 setLocale('en-US') 保持同步生效（现有用例切完语言立即断言英文）。
registerMessages('en-US', enUS)

// jsdom 不实现 matchMedia，主题 hook 依赖它。
if (!window.matchMedia) {
  window.matchMedia = (query: string): MediaQueryList =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      addListener: () => undefined,
      removeListener: () => undefined,
      dispatchEvent: () => false,
    }) as unknown as MediaQueryList
}

beforeEach(() => {
  localStorage.clear()
  document.documentElement.removeAttribute('data-theme')
})
