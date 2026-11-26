/**
 * 延迟显示的布尔值（11-polish-motion §2「按钮点击后立即进入 loading 态，>300ms 才显示转圈，避免一闪」）。
 *
 *   const showSpinner = useDelayedFlag(() => props.loading, 300)
 *
 * source 变 true 后过 delayMs 才变 true；source 一变 false 立刻变 false。
 * 骨架屏、刷新小转圈同理：快接口（<300ms）完全不出现闪烁。
 */
import { onScopeDispose, ref, watch, type Ref, type WatchSource } from 'vue'

export function useDelayedFlag(source: WatchSource<boolean>, delayMs = 300): Ref<boolean> {
  const flag = ref(false)
  let timer: ReturnType<typeof setTimeout> | null = null
  const clear = (): void => {
    if (timer) {
      clearTimeout(timer)
      timer = null
    }
  }
  watch(
    source,
    (on) => {
      clear()
      if (!on) {
        flag.value = false
        return
      }
      if (delayMs <= 0) {
        flag.value = true
        return
      }
      timer = setTimeout(() => {
        timer = null
        flag.value = true
      }, delayMs)
    },
    { immediate: true },
  )
  onScopeDispose(clear)
  return flag
}
