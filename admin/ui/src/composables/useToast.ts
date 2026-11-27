/**
 * Toast（04-components §3）：右上角，成功 2.5s、错误 6s 且可手动关闭。
 *
 * 文案按 05-patterns §6：用过去式（"已保存""已禁用 3 个账号"），
 * 错误用 "<动作>失败：<原因>"，原因取后端 detail / message（utils/error 的 failureText）。
 */
import { useIslandMessage as useMessage } from '@/components/feedback/islandQueue'

/** 各类型的默认停留时长（毫秒）。 */
export const TOAST_DURATION = {
  success: 2500,
  info: 2500,
  warning: 4000,
  error: 6000,
} as const

export type ToastType = keyof typeof TOAST_DURATION

export interface ToastOptions {
  duration?: number
  closable?: boolean
}

export function useToast() {
  const message = useMessage()

  function show(type: ToastType, text: string, options: ToastOptions = {}) {
    const duration = options.duration ?? TOAST_DURATION[type]
    // 错误默认允许手动关闭：6 秒里用户可能已经在看别处，给个叉更保险。
    const closable = options.closable ?? type === 'error'
    const send =
      type === 'success'
        ? message.success
        : type === 'error'
          ? message.error
          : type === 'warning'
            ? message.warning
            : message.info
    return send(text, { duration, closable, keepAliveOnHover: type === 'error' })
  }

  return {
    show,
    success: (text: string, options?: ToastOptions) => show('success', text, options),
    error: (text: string, options?: ToastOptions) => show('error', text, options),
    info: (text: string, options?: ToastOptions) => show('info', text, options),
    warning: (text: string, options?: ToastOptions) => show('warning', text, options),
    /** 页面卸载 / 切换时清掉残留提示。 */
    clear: () => message.destroyAll(),
  }
}
