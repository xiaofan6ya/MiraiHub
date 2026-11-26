/// <reference types="vite/client" />

interface Window {
  /** 桌面版（pywebview）注入的能力，只在桌面客户端里存在。 */
  pywebview?: { api?: Record<string, unknown> }
}
