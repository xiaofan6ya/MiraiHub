import { readonly, ref } from 'vue'
import { http } from '@/api/http'

/**
 * 侧栏提示点用的极小聚合（旧版 console-app.js:1213 `refreshShellBadges`）。
 *
 * 两个点：
 *   · 账号组 warn 点：`GET /api/accounts/badges` 的 `expiring_accounts`（积分即将到期的账号数，
 *     纯 SQL 聚合、不加载账号行）。03-shell §2.1 说的「冷却 / 熔断」口径后端没有现成接口，
 *     这里不编造，文案写成「积分即将到期」。
 *   · 概览组 danger 点：`GET /api/sentinel/status` 的 `tripped`（哨兵判定批次级封控，
 *     review_notes 待优化清单第 4 条）。window=300 与哨兵页默认一致。
 *
 * 刷新时机：外壳挂载、切换平台时各一次；之后每 60 秒一次（页面不可见时跳过）。
 * 任何一项失败都不影响功能，也不重试 —— 保持上一次的值。
 */
const expiringAccounts = ref(0)
const sentinelTripped = ref(false)

/** 轮询间隔：侧栏提示点不需要实时，60 秒足够（哨兵页自己有 2 秒的实时轮询）。 */
export const SHELL_BADGE_INTERVAL_MS = 60000

async function refreshAccountsBadge(): Promise<void> {
  try {
    const data = await http<{ expiring_accounts?: number }>('GET', '/api/accounts/badges')
    expiringAccounts.value = Number(data?.expiring_accounts ?? 0) || 0
  } catch {
    /* 角标失败不影响任何功能，也不重试。 */
  }
}

async function refreshSentinelBadge(): Promise<void> {
  try {
    // 先走轻量接口；旧后端没有它（404）或返回里没有 tripped 时退回完整的 status。
    let data: { tripped?: boolean } | null = null
    try {
      data = await http<{ tripped?: boolean }>('GET', '/api/sentinel/tripped', {
        query: { window: 300 },
      })
    } catch {
      data = null
    }
    if (typeof data?.tripped !== 'boolean') {
      data = await http<{ tripped?: boolean }>('GET', '/api/sentinel/status', {
        query: { window: 300 },
      })
    }
    sentinelTripped.value = Boolean(data?.tripped)
  } catch {
    /* 同上：保持上一次的值。 */
  }
}

export async function refreshShellBadges(): Promise<void> {
  await Promise.all([refreshAccountsBadge(), refreshSentinelBadge()])
}

let timer: ReturnType<typeof setInterval> | null = null

/** 外壳挂载后调用；重复调用无副作用。 */
export function startShellBadgePolling(intervalMs: number = SHELL_BADGE_INTERVAL_MS): void {
  if (timer || typeof window === 'undefined') return
  timer = setInterval(() => {
    if (typeof document !== 'undefined' && document.hidden) return
    void refreshShellBadges()
  }, intervalMs)
}

export function stopShellBadgePolling(): void {
  if (!timer) return
  clearInterval(timer)
  timer = null
}

/** 仅测试用：复位。 */
export function resetShellBadges(): void {
  stopShellBadgePolling()
  expiringAccounts.value = 0
  sentinelTripped.value = false
}

export function useShellBadges() {
  return {
    expiringAccounts: readonly(expiringAccounts),
    sentinelTripped: readonly(sentinelTripped),
    refreshShellBadges,
  }
}
