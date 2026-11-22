import { readonly, ref } from 'vue'
import { http } from '@/api/http'

/**
 * 侧栏提示点用的极小聚合（旧版 console-app.js:1213 `refreshShellBadges`）。
 *
 * 03-shell §2.1 说的是「账号组有冷却/熔断账号时显示 warn 圆点」，但旧版与后端
 * 都**没有**这个口径的现成接口；唯一已有的侧栏圆点数据是
 * `GET /api/accounts/badges` 的 `expiring_accounts`（积分即将到期的账号数，
 * 纯 SQL 聚合、不加载账号行）。这里沿用它，文案写成「积分即将到期」，
 * 不编造「冷却/熔断」的数字。等对应页面接上自己的接口后再替换数据源。
 */
const expiringAccounts = ref(0)

export async function refreshShellBadges(): Promise<void> {
  try {
    const data = await http<{ expiring_accounts?: number }>('GET', '/api/accounts/badges')
    expiringAccounts.value = Number(data?.expiring_accounts ?? 0) || 0
  } catch {
    /* 角标失败不影响任何功能，也不重试。 */
  }
}

export function useShellBadges() {
  return { expiringAccounts: readonly(expiringAccounts), refreshShellBadges }
}
