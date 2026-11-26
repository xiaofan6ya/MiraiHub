/**
 * 空状态插画种类（第 5 轮）：无数据 / 无结果 / 出错 / 离线。
 * 插画是内联 SVG，颜色全部来自主题令牌（浅色 / 深色自动跟随），不额外请求图片。
 */
export type EmptyIllustrationKind = 'empty' | 'search' | 'error' | 'offline'

export const EMPTY_ILLUSTRATIONS: ReadonlyArray<EmptyIllustrationKind> = ['empty', 'search', 'error', 'offline']

export function isEmptyIllustration(value: unknown): value is EmptyIllustrationKind {
  return value === 'empty' || value === 'search' || value === 'error' || value === 'offline'
}
