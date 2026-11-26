/**
 * 外壳用到的两个小工具：OpenAI Base URL 与复制。
 * 复制实现统一在 S03/S05 的 `@/composables/useCopy`（navigator.clipboard →
 * textarea + execCommand 回退），这里只做转出，不在外壳里再写一份。
 */
export { copyText, fallbackCopy } from '@/composables/useCopy'

/** 复制按钮与「复制 API 地址」命令共用的值：`http://host/v1`。 */
export function apiBaseUrl(): string {
  const origin = typeof window === 'undefined' ? '' : window.location.origin
  return origin + '/v1'
}
