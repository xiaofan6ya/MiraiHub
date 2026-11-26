import { nextTick } from 'vue'
import type { Router } from 'vue-router'

/**
 * 顶栏标题的平滑替换（11-polish-motion §1「浏览器支持 View Transitions 时用它做顶栏标题的平滑替换」）。
 *
 * 只给带 `view-transition-name: mh-page-title` 的元素（TopBar 的当前页名）做交叉淡入；
 * 整页（root）的默认动画在 base.css 里关掉，内容区仍由 AppShell 的 CSS 淡入上移负责，避免两套动画叠加。
 * 不支持的浏览器、减少动效、首次导航、同一页面换 query：都直接放行，零副作用。
 */
interface ViewTransitionLike {
  ready?: Promise<unknown>
  finished?: Promise<unknown>
  updateCallbackDone?: Promise<unknown>
}

type StartViewTransition = (update: () => Promise<void> | void) => ViewTransitionLike

function startViewTransitionFn(): StartViewTransition | null {
  if (typeof document === 'undefined') return null
  const candidate = (document as unknown as { startViewTransition?: unknown }).startViewTransition
  return typeof candidate === 'function' ? (candidate as StartViewTransition).bind(document) : null
}

function reducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches
  } catch {
    return false
  }
}

/** 安全阀：回调最多等这么久，超时直接结束过渡，导航不会被卡住。 */
const MAX_WAIT_MS = 400

export function installTitleViewTransition(router: Router): void {
  router.beforeResolve((to, from) => {
    const start = startViewTransitionFn()
    if (!start || reducedMotion()) return true
    if (!from.matched.length || to.name === from.name) return true
    if (typeof document !== 'undefined' && document.hidden) return true
    return new Promise<boolean>((resolve) => {
      let released = false
      const release = (): void => {
        if (released) return
        released = true
        resolve(true)
      }
      try {
        const transition = start(
          () =>
            new Promise<void>((done) => {
              const timer = globalThis.setTimeout(done, MAX_WAIT_MS)
              const off = router.afterEach(() => {
                off()
                void nextTick().then(() => {
                  globalThis.clearTimeout(timer)
                  done()
                })
              })
              release()
            }),
        )
        // 过渡被跳过（另一个过渡开始、页面隐藏）时这些 promise 会 reject：吞掉，不打未处理异常。
        transition.ready?.catch(() => undefined)
        transition.finished?.catch(() => undefined)
        transition.updateCallbackDone?.catch(() => undefined)
      } catch {
        release()
      }
      globalThis.setTimeout(release, MAX_WAIT_MS)
    })
  })
}
