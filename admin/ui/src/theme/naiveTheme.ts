import { computed, ref, watch } from 'vue'
import { darkTheme, type GlobalTheme, type GlobalThemeOverrides } from 'naive-ui'
import { useTheme } from './useTheme'

const cssVar = (name: string): string =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim()

/**
 * 从 tokens.css 读取令牌生成 Naive UI 覆盖，保证两套主题只有一处真值。
 * 主题切换后由调用方触发 rebuild。
 */
export function buildThemeOverrides(): GlobalThemeOverrides {
  const brand = cssVar('--brand')
  const brandHover = cssVar('--brand-hover')
  const ok = cssVar('--ok')
  const warn = cssVar('--warn')
  const danger = cssVar('--danger')
  const bg = cssVar('--bg')
  const surface = cssVar('--surface')
  const surface2 = cssVar('--surface-2')
  const surfaceHover = cssVar('--surface-hover')
  const border = cssVar('--border')
  const borderStrong = cssVar('--border-strong')
  const fg = cssVar('--fg')
  const fg2 = cssVar('--fg-2')
  const fgMuted = cssVar('--fg-muted')
  const fontSans = cssVar('--font-sans')
  const fontMono = cssVar('--font-mono')
  const controlBorder = '1px solid ' + borderStrong
  const controlBorderHover = '1px solid ' + brand
  const focusRing = '0 0 0 2px var(--brand-soft)'

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
      borderColor: border,
      dividerColor: border,
      bodyColor: bg,
      cardColor: surface,
      modalColor: surface,
      popoverColor: surface,
      tableColor: surface,
      tableHeaderColor: surface2,
      tableColorHover: surfaceHover,
      inputColor: surface,
      actionColor: surface2,
      tagColor: surface2,
      borderRadius: '7px',
      borderRadiusSmall: '5px',
      fontFamily: fontSans,
      fontFamilyMono: fontMono,
      fontSize: '14px',
      fontSizeSmall: '12px',
      fontWeightStrong: '600',
      heightMedium: '32px',
      heightSmall: '28px',
      heightLarge: '36px',
      boxShadow1: 'none',
      boxShadow2: 'var(--shadow-pop)',
      boxShadow3: 'var(--shadow-pop)',
    },
    Button: {
      borderRadiusMedium: '7px',
      borderRadiusSmall: '7px',
      borderRadiusLarge: '7px',
      heightMedium: '32px',
      heightSmall: '28px',
      heightLarge: '36px',
      fontWeight: '500',
    },
    Card: {
      borderRadius: '10px',
      color: surface,
      borderColor: border,
    },
    DataTable: {
      borderRadius: '10px',
      thColor: surface2,
      thTextColor: fgMuted,
      thFontWeight: '500',
      tdColor: surface,
      tdColorHover: surfaceHover,
      tdColorStriped: surface2,
      borderColor: border,
      tdTextColor: fg,
      thPaddingMedium: '8px 12px',
      tdPaddingMedium: '10px 12px',
    },
    Input: {
      borderRadius: '7px',
      color: surface,
      colorFocus: surface,
      border: controlBorder,
      borderHover: controlBorderHover,
      borderFocus: controlBorderHover,
      boxShadowFocus: focusRing,
    },
    Select: {
      peers: {
        InternalSelection: {
          borderRadius: '7px',
          color: surface,
          border: controlBorder,
          borderHover: controlBorderHover,
          borderFocus: controlBorderHover,
          boxShadowFocus: focusRing,
        },
      },
    },
    Drawer: {
      color: surface,
      borderRadius: '10px',
    },
    Dialog: {
      borderRadius: '10px',
      color: surface,
    },
  }
}

export function useNaiveTheme() {
  const { effectiveTheme } = useTheme()
  const themeOverrides = ref<GlobalThemeOverrides>(buildThemeOverrides())

  watch(
    effectiveTheme,
    () => {
      // 等 data-theme 写入后再读取 CSS 变量。
      requestAnimationFrame(() => {
        themeOverrides.value = buildThemeOverrides()
      })
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
