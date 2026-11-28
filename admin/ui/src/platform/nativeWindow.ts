/**
 * 无边框桌面窗口的「自绘窗饰」：拖拽区、− □ × 与边缘缩放把手。
 *
 * ## 为什么不直接用 pywebview 内建的 `.pywebview-drag-region`
 *
 * pywebview 6 只在页面加载完成那一刻给**当时已存在**的元素绑 mousedown
 * （`webview/js/customize.js`：`querySelectorAll` 后逐个 addEventListener，
 * 无事件委托、无 MutationObserver）。Vue 顶栏在加载之后才挂载，永远轮不到
 * 绑定；而且顶栏是 `pointer-events:none` 的浮岛布局，绑上了也收不到事件。
 * 这正是 v1.1.6 「窗口拖不动」的根因。
 *
 * 所以这里自己复刻同一套协议：mousedown 记下 clientX/Y，mousemove 把
 * 「cursor.screenX − 起始 clientX」交给壳 —— 那正是窗口左上角应到的**绝对
 * 屏幕坐标**（pywebview `window.move` 的语义），壳再按 DPI 换算挪窗口。
 *
 * 缩放把手沿用壳里现成的 `resize-<edge>` 动作（WM_SYSCOMMAND SC_SIZE），
 * 与旧控制台的 `.window-resize` 行为一致。
 */
import { ref } from 'vue'
import { callDesktop, initDesktop, isDesktop, windowAction } from './desktop'

/** 当前是否跑在无边框（frameless）桌面壳里 —— 只有 Windows 壳是。 */
export const framelessWindow = ref(false)
/** 窗口是否处于最大化（自绘 □/❐ 图标与「最大化时禁止拖动」的判据）。 */
export const windowMaximized = ref(false)
/**
 * 壳里查到的真实最大化状态。前端的标记在页面导航后会丢（启动页最大化 →
 * 进工作台），所以 mousedown 时重新查一次，两条判据任一为真都不拖。
 */
let maximizedRemote: boolean | null = null

function refreshWindowState(): void {
  void callDesktop<{ state?: string }>('window_state').then((state) => {
    maximizedRemote = state?.state === 'maximized'
  })
}

let detected = false

/**
 * 探测窗口形态（幂等）：问壳要 `window_frame`；旧壳没有这个方法时按 UA
 * 兜底（EdgeChromium 的 UA 必含 Windows NT，macOS WKWebView 必含 Macintosh）。
 * 必须等 `initDesktop()` 的握手完成后再问，否则 api 方法还没挂上。
 */
export async function detectFramelessWindow(): Promise<void> {
  if (detected) return
  await initDesktop()
  if (!isDesktop()) return
  detected = true
  const frame = await callDesktop<{ frameless?: boolean } | null>('window_frame')
  framelessWindow.value = frame?.frameless === true
    || (!frame && /Windows NT/.test(navigator.userAgent))
}

/** −（收进托盘或最小化）。 */
export function minimizeWindow(): void {
  void windowAction('minimize')
}

/** □/❐：最大化与还原互相切换。 */
export function toggleWindowMaximize(): void {
  void windowAction('maximize')
  windowMaximized.value = !windowMaximized.value
}

/** ×：托盘模式收进托盘，窗口模式排队退出。 */
export function closeWindow(): void {
  void windowAction('close')
}

interface InteractiveGuard {
  closest: (selector: string) => Element | null
}

function insideInteractive(target: EventTarget | null): boolean {
  const element = target as InteractiveGuard | null
  return Boolean(element?.closest?.('button, input, textarea, select, a, [role="button"]'))
}

/**
 * 把一个元素变成窗口拖拽区（双击切换最大化）。事件绑定即生效 —— 是否真的
 * 拖动由按下时的实时状态决定（frameless 探测、最大化与否），所以
 * 不用等探测完成再绑。
 *
 * ## 为什么用 Pointer Events + setPointerCapture，而不是 mousedown/mouseup
 *
 * v1.1.7 首版用 window 级 mouseup 收尾：窗口在拖动中被 SetWindowPos 挪动，
 * WebView2 偶发把松手事件吞掉（指针已移出渲染区 / 系统抢走捕获），`dragging`
 * 就永远卡在 true —— 之后**每次** mousemove 都会把窗口瞬移到「光标 − 按下偏移」
 * 处，窗口追着鼠标满屏跑：这正是「一直闪、一直卡」的实测根因。
 *
 * 现在：pointerdown 时对元素 `setPointerCapture`，pointerup/cancel **保证**
 * 送回同一元素；再叠加「按键已物理松开即终止」的兜底守卫，三条路都能停。
 */
export function bindWindowDrag(
  el: HTMLElement,
  enabled: () => boolean = () => framelessWindow.value,
): void {
  let startX = 0
  let startY = 0
  let dragging = false
  let lastX = -1
  let lastY = -1

  function stopDragging(pointerId?: number): void {
    if (!dragging) return
    dragging = false
    if (pointerId !== undefined) {
      try { el.releasePointerCapture(pointerId) } catch { /* 已释放 */ }
    }
  }

  function moveTo(event: PointerEvent): void {
    const callback = (window as unknown as {
      pywebview?: { _jsApiCallback?: (name: string, params: number[], id: string) => void }
    }).pywebview?._jsApiCallback
    if (typeof callback !== 'function') return
    const x = event.screenX - startX
    const y = event.screenY - startY
    if (x === lastX && y === lastY) return
    lastX = x
    lastY = y
    callback('pywebviewMoveWindow', [x, y], 'move')
  }

  el.addEventListener('pointerdown', (event) => {
    if (event.button !== 0 || !enabled()) return
    if (insideInteractive(event.target)) return
    refreshWindowState()
    if (windowMaximized.value || maximizedRemote) return
    dragging = true
    lastX = -1
    lastY = -1
    startX = event.clientX
    startY = event.clientY
    // 捕获后即使指针移出窗口，pointermove/up 也只派发给本元素 —— 松手必达。
    try { el.setPointerCapture(event.pointerId) } catch { /* 已失效则靠兜底守卫 */ }
    // 文本选择 / 拖拽链接会和窗口拖动打架，直接吃掉默认行为。
    event.preventDefault()
  })
  el.addEventListener('pointermove', (event) => {
    if (!dragging) return
    // 兜底：不管 pointerup 是否被吞，左键物理松开就立即终止。
    if (!(event.buttons & 1)) {
      stopDragging(event.pointerId)
      return
    }
    moveTo(event)
  })
  el.addEventListener('pointerup', (event) => stopDragging(event.pointerId))
  el.addEventListener('pointercancel', (event) => stopDragging(event.pointerId))
  el.addEventListener('lostpointercapture', (event) => stopDragging(event.pointerId))
  window.addEventListener('blur', () => { dragging = false })
  el.addEventListener('dblclick', (event) => {
    if (!enabled()) return
    if (insideInteractive(event.target)) return
    toggleWindowMaximize()
  })
}

const RESIZE_EDGES = [
  'top', 'right', 'bottom', 'left',
  'top-left', 'top-right', 'bottom-left', 'bottom-right',
] as const

const RESIZE_HANDLE_CSS = [
  '[data-wb-resize]{position:fixed;z-index:9999}',
  '[data-wb-resize="top"]{top:0;left:0;right:0;height:6px;cursor:ns-resize}',
  '[data-wb-resize="bottom"]{bottom:0;left:0;right:0;height:6px;cursor:ns-resize}',
  '[data-wb-resize="left"]{left:0;top:0;bottom:0;width:6px;cursor:ew-resize}',
  '[data-wb-resize="right"]{right:0;top:0;bottom:0;width:6px;cursor:ew-resize}',
  '[data-wb-resize="top-left"]{top:0;left:0;width:10px;height:10px;cursor:nwse-resize}',
  '[data-wb-resize="bottom-right"]{bottom:0;right:0;width:10px;height:10px;cursor:nwse-resize}',
  '[data-wb-resize="top-right"]{top:0;right:0;width:10px;height:10px;cursor:nesw-resize}',
  '[data-wb-resize="bottom-left"]{bottom:0;left:0;width:10px;height:10px;cursor:nesw-resize}',
].join('')

/**
 * 注入 8 条边缘缩放把手（无边框窗口没有系统可拖边框，只能自绘）。
 * 幂等：把手已存在就不再注入。只在 frameless 壳里调用。
 */
export function installResizeHandles(): void {
  if (!framelessWindow.value) return
  if (document.querySelector('[data-wb-resize]')) return
  for (const edge of RESIZE_EDGES) {
    const handle = document.createElement('div')
    handle.dataset.wbResize = edge
    handle.setAttribute('aria-hidden', 'true')
    handle.addEventListener('mousedown', (event) => {
      if (event.button !== 0) return
      event.preventDefault()
      void windowAction('resize-' + edge)
    })
    document.body.appendChild(handle)
  }
  const style = document.createElement('style')
  style.textContent = RESIZE_HANDLE_CSS
  document.head.appendChild(style)
}

/** 外壳挂载时的一次性入口：探测形态 + 装把手。 */
export async function initWindowChrome(): Promise<void> {
  await detectFramelessWindow()
  installResizeHandles()
}
