/** Badge 语气 → 令牌映射。文字用状态色，底色用 -soft 版本（01-direction §3）。 */

export type BadgeTone = 'ok' | 'warn' | 'danger' | 'neutral' | 'brand'

export interface BadgeToneStyle {
  /** 文字色令牌。 */
  text: string
  /** 底色令牌。 */
  bg: string
}

export const BADGE_TONES: Record<BadgeTone, BadgeToneStyle> = {
  ok: { text: 'var(--ok)', bg: 'var(--ok-soft)' },
  warn: { text: 'var(--warn)', bg: 'var(--warn-soft)' },
  danger: { text: 'var(--danger)', bg: 'var(--danger-soft)' },
  neutral: { text: 'var(--fg-muted)', bg: 'var(--neutral-soft)' },
  brand: { text: 'var(--brand)', bg: 'var(--brand-soft)' },
}

export const BADGE_TONE_NAMES: BadgeTone[] = ['ok', 'warn', 'danger', 'neutral', 'brand']

/** 未知语气一律按 neutral 处理，避免出现无底色的裸文字。 */
export function badgeToneStyle(tone?: string | null): BadgeToneStyle {
  const key = String(tone || '').trim().toLowerCase() as BadgeTone
  return BADGE_TONES[key] ?? BADGE_TONES.neutral
}
