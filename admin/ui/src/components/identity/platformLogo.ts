/** 平台 icon 解析：旧版 admin/console/platform-icons.js 的映射逻辑（纯函数）。 */
import { PLATFORM_ALIASES, PLATFORM_RASTER_ICONS, PLATFORM_SVG_ICONS } from './platformIcons'

export type PlatformIconKind = 'svg' | 'raster' | 'fallback'

/** 平台 id → 图标 key（同产品国内/国际站共用图标）。 */
export function resolvePlatformIconKey(id?: string | null): string {
  const key = String(id || '').toLowerCase()
  return PLATFORM_ALIASES[key] ?? key
}

export function platformIconKind(id?: string | null): PlatformIconKind {
  const key = resolvePlatformIconKey(id)
  if (Object.prototype.hasOwnProperty.call(PLATFORM_SVG_ICONS, key)) return 'svg'
  if (Object.prototype.hasOwnProperty.call(PLATFORM_RASTER_ICONS, key)) return 'raster'
  return 'fallback'
}

export function platformSvgBody(id?: string | null): string | null {
  const key = resolvePlatformIconKey(id)
  return PLATFORM_SVG_ICONS[key] ?? null
}

export function platformRaster(id?: string | null): { file: string; tone: string } | null {
  const key = resolvePlatformIconKey(id)
  return PLATFORM_RASTER_ICONS[key] ?? null
}
