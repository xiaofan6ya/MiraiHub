/**
 * 顶栏平台选择器的纯函数（第 3 轮审查 A4）：圆点状态、排序、副标题去重。
 * 抽出来是为了能直接单测，PlatformPicker.vue 只负责摆放。
 */
import type { PlatformSpec } from '@/api/platforms'

/**
 * 圆点语气（01-direction §3.3）：
 *   · ok    —— 有可用账号；
 *   · warn  —— 有账号，但全部不可用（需要后端给 active_count 才能判断）；
 *   · muted —— 0 个账号 / 平台已停用 / 账号数未知。
 * 旧实现只看 enabled，0 账号的平台和「35 个账号全部禁用」的 WorkBuddy 都显示绿点。
 */
export type PickerDotTone = 'ok' | 'warn' | 'muted'

export function pickerDotTone(
  spec: Pick<PlatformSpec, 'enabled' | 'active_count'>,
  total: number | null | undefined,
): PickerDotTone {
  if (spec.enabled === false) return 'muted'
  const count = typeof total === 'number' && Number.isFinite(total) ? total : null
  const active = typeof spec.active_count === 'number' && Number.isFinite(spec.active_count) ? spec.active_count : null
  if (active !== null) {
    if (active > 0) return 'ok'
    return count !== null && count > 0 ? 'warn' : 'muted'
  }
  if (count === null) return 'muted'
  return count > 0 ? 'ok' : 'muted'
}

/**
 * 排序：有账号的平台在前，0 账号 / 未知的在后；同组保持后端登记顺序（稳定排序）。
 * 当前选中的平台不特殊处理（它有勾选标记）。
 */
export function sortPickerPlatforms(
  items: readonly PlatformSpec[],
  countOf: (id: string) => number | null,
): PlatformSpec[] {
  return items
    .map((item, index) => {
      const total = countOf(item.id)
      return { item, index, rank: typeof total === 'number' && total > 0 ? 0 : 1 }
    })
    .sort((a, b) => a.rank - b.rank || a.index - b.index)
    .map((entry) => entry.item)
}

/** 副标题：名称已经含区域（「Trae 国内版」）时不再重复显示「国内版」。 */
export function pickerSubtitle(label: string, realmText: string): string {
  const sub = String(realmText || '').trim()
  if (!sub) return ''
  return String(label || '').includes(sub) ? '' : sub
}
