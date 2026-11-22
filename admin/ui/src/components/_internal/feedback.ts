/**
 * 组件内部使用的反馈能力的**唯一适配层**。
 *
 * 实现委托给 S03 已落地的 `@/composables`（useToast / copyText），
 * 组件只 import 本文件：core 之后若要调整默认时长或文案规则，
 * 改一处即可，无需触碰具体组件。
 *
 * 没有 NMessageProvider 的场景（例如组件单测）静默降级为 no-op。
 */
import { copyText, useToast as useCoreToast } from '@/composables'

export { copyText }

export interface ToastApi {
  success(content: string, durationMs?: number): void
  error(content: string, durationMs?: number): void
  info(content: string, durationMs?: number): void
  warning(content: string, durationMs?: number): void
}

const NOOP: ToastApi = {
  success: () => undefined,
  error: () => undefined,
  info: () => undefined,
  warning: () => undefined,
}

type CoreToast = ReturnType<typeof useCoreToast>

/** 成功 2.5s、错误 6s 且可手动关闭（04-components §3）。 */
export function useComponentToast(): ToastApi {
  let api: CoreToast | null = null
  try {
    api = useCoreToast()
  } catch {
    api = null
  }
  if (!api) return NOOP
  const toast = api
  return {
    success: (content, durationMs) => toast.success(content, { duration: durationMs }),
    error: (content, durationMs) => toast.error(content, { duration: durationMs }),
    info: (content, durationMs) => toast.info(content, { duration: durationMs }),
    warning: (content, durationMs) => toast.warning(content, { duration: durationMs }),
  }
}
