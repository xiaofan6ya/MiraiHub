/**
 * 错误文案（05-patterns §6：错误写"<动作>失败：<原因>"，不要"出错了请稍后再试"）。
 */
import { ApiError } from '@/api/http'

/** 从任何抛出来的东西里取一句人话。 */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message
  if (error instanceof Error) return error.message || '未知错误'
  if (typeof error === 'string' && error.trim()) return error.trim()
  if (error && typeof error === 'object') {
    const record = error as Record<string, unknown>
    for (const key of ['message', 'error', 'detail']) {
      const value = record[key]
      if (typeof value === 'string' && value.trim()) return value.trim()
    }
  }
  return '未知错误'
}

/** "<动作>失败：<原因>"，动作用动词（"刷新额度" → "刷新额度失败：上游返回 401…"）。 */
export function failureText(action: string, error: unknown): string {
  return action + '失败：' + errorMessage(error)
}
