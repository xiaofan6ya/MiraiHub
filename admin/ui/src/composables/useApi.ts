/**
 * 04-components §4 列出的 useApi()：把 api 层的能力收成一个组合式函数，
 * 页面里 const { http, download } = useApi() 也行，直接从 @/api 引也行。
 *
 * 这里刻意保持"薄"：真正的策略在 src/api/http.ts，页面不要绕过它直接用 fetch。
 */
import { ApiError, download, http, stream } from '@/api/http'

export function useApi() {
  return { http, download, stream, ApiError }
}
