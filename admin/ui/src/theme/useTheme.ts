import { computed, ref, watch } from 'vue'

export type ThemeMode = 'light' | 'dark' | 'system'
export type EffectiveTheme = 'light' | 'dark'

const STORAGE_KEY = 'mh.theme'
const LEGACY_KEY = 'wb_console_theme'

const isThemeMode = (value: string | null): value is ThemeMode =>
  value === 'light' || value === 'dark' || value === 'system'

function readInitialMode(): ThemeMode {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (isThemeMode(saved)) return saved
    // 首次进入新控制台：沿用旧版主题，避免用户看到闪白。
    const legacy = localStorage.getItem(LEGACY_KEY)
    if (legacy === 'dark' || legacy === 'light') return legacy
  } catch {
    /* 隐私模式下 localStorage 不可用，退回跟随系统。 */
  }
  return 'system'
}

const mediaQuery =
  typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    ? window.matchMedia('(prefers-color-scheme: dark)')
    : null

const mode = ref<ThemeMode>(readInitialMode())
const systemPrefersDark = ref(mediaQuery?.matches ?? false)

export const effectiveTheme = computed<EffectiveTheme>(() => {
  if (mode.value === 'system') return systemPrefersDark.value ? 'dark' : 'light'
  return mode.value
})

function applyTheme(theme: EffectiveTheme): void {
  if (typeof document === 'undefined') return
  document.documentElement.dataset.theme = theme
  document.documentElement.style.colorScheme = theme
}

/** 在挂载应用之前调用，避免首帧使用错误主题。 */
export function initTheme(): void {
  applyTheme(effectiveTheme.value)
  mediaQuery?.addEventListener?.('change', (event) => {
    systemPrefersDark.value = event.matches
  })
}

watch(effectiveTheme, (theme) => applyTheme(theme), { flush: 'sync' })

watch(mode, (value) => {
  try {
    localStorage.setItem(STORAGE_KEY, value)
  } catch {
    /* 存不了不影响当前会话。 */
  }
})

export function useTheme() {
  const setMode = (next: ThemeMode): void => {
    mode.value = next
  }

  /** 顶栏按钮循环：浅色 → 深色 → 跟随系统。 */
  const cycle = (): void => {
    mode.value = mode.value === 'light' ? 'dark' : mode.value === 'dark' ? 'system' : 'light'
  }

  return { mode, effectiveTheme, setMode, cycle }
}
