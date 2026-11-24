/**
 * 输入防抖（11-polish-motion §2「输入筛选 250ms 防抖」）。
 *
 *   const keyword = ref('')
 *   const debouncedKeyword = useDebouncedRef(keyword)      // 250ms 后才跟上
 *   watch(debouncedKeyword, () => reload())
 */
import { onScopeDispose, ref, watch, type Ref } from 'vue'

export const INPUT_DEBOUNCE_MS = 250

export function useDebouncedRef<T>(source: Ref<T>, delayMs = INPUT_DEBOUNCE_MS): Ref<T> {
  const value = ref(source.value) as Ref<T>
  let timer: ReturnType<typeof setTimeout> | null = null
  watch(source, (next) => {
    if (timer) clearTimeout(timer)
    timer = setTimeout(() => {
      timer = null
      value.value = next
    }, delayMs)
  })
  onScopeDispose(() => {
    if (timer) clearTimeout(timer)
  })
  return value
}
