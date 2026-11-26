import { computed, readonly, ref } from 'vue'

/**
 * 外壳自己的 UI 状态（与业务无关，所以不进 Pinia store）：
 * 侧栏收起、移动端抽屉、登录设备抽屉、命令面板。
 * 断点按 10-visual-refresh §1.2（覆盖 03-shell §3）：
 *   ≥1280 完整侧栏 / 768–1279 自动收起成 64px 图标栏 / <768 抽屉；≤640 手机细节（抽屉铺满等）。
 */

const COLLAPSE_KEY = 'mh.sideCollapsed'

function readStoredCollapse(): boolean | null {
  try {
    const raw = localStorage.getItem(COLLAPSE_KEY)
    if (raw === '1') return true
    if (raw === '0') return false
  } catch {
    /* 隐私模式 */
  }
  return null
}

function storeCollapse(value: boolean): void {
  try {
    localStorage.setItem(COLLAPSE_KEY, value ? '1' : '0')
  } catch {
    /* 存不了不影响本次会话 */
  }
}

function viewportWidth(): number {
  if (typeof window === 'undefined') return 1280
  return window.innerWidth || 1280
}

/** 抽屉断点：<768 侧栏变成抽屉（10-visual-refresh §1.2）。 */
export const DRAWER_MAX = 767
/** 完整侧栏的最小宽度：<1280 自动收起成图标栏。 */
export const FULL_SIDE_MIN = 1280

/** 768–1279 默认收起；≥1280 默认展开。用户手动切换过就永远听用户的。 */
export function defaultCollapsed(width = viewportWidth()): boolean {
  return width > DRAWER_MAX && width < FULL_SIDE_MIN
}

const storedCollapse = readStoredCollapse()

export const sideCollapsed = ref(storedCollapse ?? defaultCollapsed())
/** 用户是否手动切换过侧栏（手动过就不再跟随断点自动收起）。 */
export const sideCollapsePinned = ref(storedCollapse !== null)

export const mobileNavOpen = ref(false)
export const sessionsOpen = ref(false)
export const commandPaletteOpen = ref(false)

export const viewport = ref(viewportWidth())

export const isMobile = computed(() => viewport.value <= DRAWER_MAX)
export const isPhone = computed(() => viewport.value <= 640)
export const isNarrow = computed(() => viewport.value <= 1279)

let listening = false

/** 外壳挂载时调一次：监听视口变化。 */
export function initShellViewport(): void {
  if (listening || typeof window === 'undefined') return
  listening = true
  const onResize = (): void => {
    viewport.value = viewportWidth()
    // 没手动切换过的用户跟随断点：768–1279 自动收起，≥1280 自动展开。
    if (!sideCollapsePinned.value && !isMobile.value) {
      sideCollapsed.value = defaultCollapsed(viewport.value)
    }
    if (!isMobile.value) mobileNavOpen.value = false
  }
  window.addEventListener('resize', onResize, { passive: true })
  onResize()
}

export function setSideCollapsed(value: boolean): void {
  sideCollapsed.value = value
  sideCollapsePinned.value = true
  storeCollapse(value)
}

export function toggleSideCollapsed(): void {
  setSideCollapsed(!sideCollapsed.value)
}

export function toggleMobileNav(): void {
  mobileNavOpen.value = !mobileNavOpen.value
}

export function closeMobileNav(): void {
  mobileNavOpen.value = false
}

export function openCommandPalette(): void {
  commandPaletteOpen.value = true
}

export function closeCommandPalette(): void {
  commandPaletteOpen.value = false
}

export function toggleCommandPalette(): void {
  commandPaletteOpen.value = !commandPaletteOpen.value
}

export function openSessionsDrawer(): void {
  sessionsOpen.value = true
}

/** 仅测试用：把三个浮层状态复位（模块级单例，测试之间会串）。 */
export function resetShellState(): void {
  mobileNavOpen.value = false
  sessionsOpen.value = false
  commandPaletteOpen.value = false
}

/** 供组件读取的只读视图（写操作走上面的函数）。 */
export function useShellState() {
  return {
    sideCollapsed: readonly(sideCollapsed),
    mobileNavOpen: readonly(mobileNavOpen),
    sessionsOpen: readonly(sessionsOpen),
    paletteOpen: readonly(commandPaletteOpen),
    isMobile,
    isPhone,
    isNarrow,
    viewport: readonly(viewport),
  }
}
