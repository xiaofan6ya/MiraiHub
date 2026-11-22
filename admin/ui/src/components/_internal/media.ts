import { onScopeDispose, ref, type Ref } from 'vue'

/** 手机断点：04-components 规定 ≤640px 走卡片列表。 */
export const MOBILE_QUERY = '(max-width: 640px)'

function safeMatchMedia(query: string): MediaQueryList | null {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return null
  try {
    return window.matchMedia(query)
  } catch {
    return null
  }
}

/** 响应式媒体查询：变化时自动更新，作用域销毁时移除监听。 */
export function useMediaQuery(query: string): Ref<boolean> {
  const mql = safeMatchMedia(query)
  const matches = ref(mql ? mql.matches : false)
  if (!mql) return matches
  const handler = (event: MediaQueryListEvent): void => {
    matches.value = event.matches
  }
  if (typeof mql.addEventListener === 'function') {
    mql.addEventListener('change', handler)
    onScopeDispose(() => mql.removeEventListener('change', handler))
  } else if (typeof mql.addListener === 'function') {
    mql.addListener(handler)
    onScopeDispose(() => mql.removeListener(handler))
  }
  return matches
}

/** 系统是否要求减少动效（线路带脉冲、骨架动画都要尊重）。 */
export function usePrefersReducedMotion(): Ref<boolean> {
  return useMediaQuery('(prefers-reduced-motion: reduce)')
}
