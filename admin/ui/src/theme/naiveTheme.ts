import { computed, ref, watch } from 'vue'
import { darkTheme, type GlobalTheme, type GlobalThemeOverrides } from 'naive-ui'
import { useTheme } from './useTheme'
import { palette } from './usePalette'

const cssVar = (name: string): string => {
  if (typeof document === 'undefined' || typeof getComputedStyle !== 'function') return ''
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

/**
 * 字体栈的兜底常量（与 tokens.css 的 --font-sans / --font-mono 同值）。
 *
 * buildThemeOverrides() 若在 tokens.css 生效前被调用（测试环境 / 样式异步注入），
 * cssVar() 读到的是空串；把空串交给 Naive 的 fontFamily 会让组件内字体回落到浏览器默认。
 */
export const FONT_SANS_FALLBACK =
  '"IBM Plex Sans", "PingFang SC", "HarmonyOS Sans SC", "MiSans", "Microsoft YaHei UI", "Microsoft YaHei", "Noto Sans CJK SC", system-ui, sans-serif'
export const FONT_MONO_FALLBACK = '"JetBrains Mono", ui-monospace, "Cascadia Mono", Consolas, monospace'

/**
 * 颜色令牌的兜底（tokens.css 浅色同值）。CSS 变量读不到时用它：
 * Naive 会用 seemly 对 primaryColor 等做颜色运算，空串会得到错误的派生色。
 */
export const LIGHT_FALLBACK: Readonly<Record<string, string>> = {
  '--brand': '#4b657b',
  '--brand-hover': '#354d62',
  '--ok': '#14866A',
  '--warn': '#B7791F',
  '--danger': '#C8382B',
  '--bg': '#f3f5f7',
  '--surface': '#FFFFFF',
  '--surface-raised': '#FFFFFF',
  '--surface-2': '#edf1f4',
  '--surface-hover': '#e7edf1',
  '--border': '#dce3e9',
  '--border-soft': '#e8edf1',
  '--border-strong': '#bdc9d3',
  '--fg': '#202a34',
  '--fg-2': '#4f5f6d',
  '--fg-muted': '#657584',
  '--fg-faint': '#82919e',
  '--ring': 'rgba(75, 101, 123, .24)',
}

/** 读令牌；读不到时按浅色兜底。 */
export function token(name: string): string {
  return cssVar(name) || LIGHT_FALLBACK[name] || ''
}

/** 控件尺寸（10-visual-refresh §1.6：按钮 / 输入框 md 34px，sm 28px）。 */
export const CONTROL_HEIGHT = { small: '28px', medium: '34px', large: '38px' } as const
/** 圆角（与 tokens.css --r-ctl / --r-chip / --r-card 同值）。 */
export const RADIUS = { ctl: '9px', chip: '6px', card: '17px', lg: '21px' } as const

/**
 * 从 tokens.css 读取令牌生成 Naive UI 覆盖，保证两套主题只有一处真值。
 * 主题切换后由调用方触发 rebuild。
 */
export function buildThemeOverrides(): GlobalThemeOverrides {
  const brand = token('--brand')
  const brandHover = token('--brand-hover')
  const ok = token('--ok')
  const warn = token('--warn')
  const danger = token('--danger')
  const bg = token('--bg')
  const surface = token('--surface')
  const surfaceRaised = token('--surface-raised')
  const surface2 = token('--surface-2')
  const surfaceHover = token('--surface-hover')
  const border = token('--border')
  const borderSoft = token('--border-soft')
  const borderStrong = token('--border-strong')
  const fg = token('--fg')
  const fg2 = token('--fg-2')
  const fgMuted = token('--fg-muted')
  const fgFaint = token('--fg-faint')
  const ring = token('--ring')
  const fontSans = cssVar('--font-sans') || FONT_SANS_FALLBACK
  const fontMono = cssVar('--font-mono') || FONT_MONO_FALLBACK
  const controlBorder = '1px solid ' + borderStrong
  const controlBorderHover = '1px solid ' + brand
  const focusRing = '0 0 0 3px ' + ring

  return {
    common: {
      primaryColor: brand,
      primaryColorHover: brandHover,
      primaryColorPressed: brandHover,
      primaryColorSuppl: brand,
      infoColor: brand,
      infoColorHover: brandHover,
      successColor: ok,
      successColorHover: ok,
      warningColor: warn,
      warningColorHover: warn,
      errorColor: danger,
      errorColorHover: danger,
      textColorBase: fg,
      textColor1: fg,
      textColor2: fg2,
      textColor3: fgMuted,
      textColorDisabled: fgFaint,
      placeholderColor: fgFaint,
      borderColor: border,
      dividerColor: borderSoft,
      bodyColor: bg,
      cardColor: surface,
      modalColor: surfaceRaised,
      popoverColor: surfaceRaised,
      tableColor: surface,
      tableHeaderColor: surface2,
      tableColorHover: surfaceHover,
      hoverColor: surfaceHover,
      inputColor: surface,
      actionColor: surface2,
      tagColor: surface2,
      borderRadius: RADIUS.ctl,
      borderRadiusSmall: RADIUS.chip,
      fontFamily: fontSans,
      fontFamilyMono: fontMono,
      fontSize: '14px',
      fontSizeSmall: '12px',
      fontWeightStrong: '600',
      heightMedium: CONTROL_HEIGHT.medium,
      heightSmall: CONTROL_HEIGHT.small,
      heightLarge: CONTROL_HEIGHT.large,
      boxShadow1: 'var(--shadow-1)',
      boxShadow2: 'var(--shadow-pop)',
      boxShadow3: 'var(--shadow-pop)',
    },
    Button: {
      borderRadiusMedium: RADIUS.ctl,
      borderRadiusSmall: RADIUS.ctl,
      borderRadiusLarge: RADIUS.ctl,
      heightMedium: CONTROL_HEIGHT.medium,
      heightSmall: CONTROL_HEIGHT.small,
      heightLarge: CONTROL_HEIGHT.large,
      fontWeight: '500',
    },
    Card: {
      borderRadius: RADIUS.card,
      color: surface,
      colorModal: surfaceRaised,
      colorPopover: surfaceRaised,
      borderColor: border,
    },
    DataTable: {
      borderRadius: RADIUS.card,
      thColor: surface2,
      thTextColor: fgMuted,
      thFontWeight: '500',
      tdColor: surface,
      tdColorHover: surfaceHover,
      tdColorStriped: surface2,
      borderColor: borderSoft,
      tdTextColor: fg,
      thPaddingMedium: '8px 12px',
      tdPaddingMedium: '10px 12px',
      thPaddingSmall: '6px 12px',
      tdPaddingSmall: '6px 12px',
    },
    Input: {
      borderRadius: RADIUS.ctl,
      heightMedium: CONTROL_HEIGHT.medium,
      heightSmall: CONTROL_HEIGHT.small,
      color: surface,
      colorFocus: surface,
      border: controlBorder,
      borderHover: controlBorderHover,
      borderFocus: controlBorderHover,
      boxShadowFocus: focusRing,
      placeholderColor: fgFaint,
    },
    Select: {
      peers: {
        InternalSelection: {
          borderRadius: RADIUS.ctl,
          heightMedium: CONTROL_HEIGHT.medium,
          heightSmall: CONTROL_HEIGHT.small,
          color: surface,
          border: controlBorder,
          borderHover: controlBorderHover,
          borderFocus: controlBorderHover,
          borderActive: controlBorderHover,
          boxShadowFocus: focusRing,
          boxShadowActive: focusRing,
          placeholderColor: fgFaint,
        },
        InternalSelectMenu: {
          borderRadius: RADIUS.card,
          color: surfaceRaised,
          optionColorPending: surfaceHover,
        },
      },
    },
    Popover: {
      color: surfaceRaised,
      borderRadius: RADIUS.card,
      boxShadow: 'var(--shadow-pop)',
    },
    Dropdown: {
      color: surfaceRaised,
      borderRadius: RADIUS.card,
      optionColorHover: surfaceHover,
    },
    Drawer: {
      color: surfaceRaised,
      borderRadius: RADIUS.lg,
      headerBorderBottom: '1px solid ' + borderSoft,
      footerBorderTop: '1px solid ' + borderSoft,
    },
    Dialog: {
      borderRadius: RADIUS.lg,
      color: surfaceRaised,
    },
    Modal: {
      color: surfaceRaised,
    },
    Message: {
      borderRadius: RADIUS.card,
      color: surfaceRaised,
      colorInfo: surfaceRaised,
      colorSuccess: surfaceRaised,
      colorError: surfaceRaised,
      colorWarning: surfaceRaised,
      colorLoading: surfaceRaised,
      textColor: fg,
      textColorInfo: fg,
      textColorSuccess: fg,
      textColorError: fg,
      textColorWarning: fg,
      textColorLoading: fg,
      boxShadow: 'var(--shadow-pop)',
      boxShadowInfo: 'var(--shadow-pop)',
      boxShadowSuccess: 'var(--shadow-pop)',
      boxShadowError: 'var(--shadow-pop)',
      boxShadowWarning: 'var(--shadow-pop)',
      boxShadowLoading: 'var(--shadow-pop)',
    },
    Tag: {
      borderRadius: RADIUS.chip,
    },
    Pagination: {
      itemBorderRadius: RADIUS.ctl,
    },
  }
}

export function useNaiveTheme() {
  const { effectiveTheme } = useTheme()
  const themeOverrides = ref<GlobalThemeOverrides>(buildThemeOverrides())

  watch(
    [effectiveTheme, palette],
    () => {
      // 等 data-theme 写入后再读取 CSS 变量。
      const run = (): void => {
        themeOverrides.value = buildThemeOverrides()
      }
      if (typeof requestAnimationFrame === 'function') requestAnimationFrame(run)
      else run()
    },
    { flush: 'post' },
  )

  const theme = computed<GlobalTheme | null>(() =>
    effectiveTheme.value === 'dark' ? darkTheme : null,
  )

  const rebuild = (): void => {
    themeOverrides.value = buildThemeOverrides()
  }

  return { theme, themeOverrides, rebuild }
}
