/**
 * 复制到剪贴板（04-components §1 Copyable 的底层）。
 *
 * 先走 navigator.clipboard（要求安全上下文），失败时回退
 * textarea + document.execCommand('copy') —— 局域网 http 访问下
 * navigator.clipboard 不可用，这是旧版实测踩过的坑。
 */
import { ref } from 'vue'

/** 回退方案：临时 textarea + execCommand。返回是否成功。 */
export function fallbackCopy(text: string): boolean {
  if (typeof document === 'undefined') return false
  const area = document.createElement('textarea')
  area.value = text
  area.setAttribute('readonly', 'readonly')
  area.style.position = 'fixed'
  area.style.top = '-1000px'
  area.style.opacity = '0'
  document.body.appendChild(area)
  try {
    area.select()
    area.setSelectionRange(0, area.value.length)
    const exec = document.execCommand
    if (typeof exec !== 'function') return false
    return Boolean(document.execCommand('copy'))
  } catch {
    return false
  } finally {
    area.remove()
  }
}

export async function copyText(text: string): Promise<boolean> {
  const value = String(text ?? '')
  if (!value) return false
  try {
    if (typeof navigator !== 'undefined' && navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(value)
      return true
    }
  } catch {
    /* 权限被拒 / 非安全上下文：走回退 */
  }
  return fallbackCopy(value)
}

export interface CopyHandle {
  copied: ReturnType<typeof ref<boolean>>
  copy: (text: string) => Promise<boolean>
}

export function useCopy(): CopyHandle {
  const copied = ref(false)
  let timer: ReturnType<typeof setTimeout> | null = null

  async function copy(text: string): Promise<boolean> {
    const ok = await copyText(text)
    copied.value = ok
    if (timer) clearTimeout(timer)
    if (ok) {
      timer = setTimeout(() => {
        copied.value = false
        timer = null
      }, 1500)
    }
    return ok
  }

  return { copied, copy }
}
