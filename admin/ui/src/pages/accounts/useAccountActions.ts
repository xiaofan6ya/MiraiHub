/**
 * 账号池 · 整池任务与行操作（从 AccountsPage.vue 拆出，§5「入口文件 <700 行」）。
 *
 * 页面传入行数据、重载、启动后台任务和删除回调；这里只负责确认弹窗、请求和 Toast。
 * 「编辑」「详情」是页面内抽屉，仍由页面处理。
 */
import type { Ref } from 'vue'
import { useConfirm, useToast } from '@/composables'
import { failureText } from '@/utils/error'
import { fmtInt } from '@/utils/format'
import { useI18n } from '@/i18n'
import {
  catTravelBatchAsync,
  deleteAccount,
  growthRunAsync,
  healthCheckAccount,
  healthCheckBatch,
  patchAccount,
  refreshAccount,
  refreshBatch,
  runAccountTask,
  type AccountJobSnapshot,
  type AccountRow,
} from '@/api/accounts'

export interface AccountActionDeps {
  /** 当前平台的全部行（未筛选）。 */
  rows: Readonly<Ref<AccountRow[]>>
  load: () => Promise<void>
  startJob: (snapshot: AccountJobSnapshot) => void
  /** 删除成功后：关详情、清选择（不含重载，重载由这里调用 load）。 */
  onDeleted: (id: number) => void
}

/** 行操作键（不含页面自己处理的 edit / detail）。 */
export type AccountRowActionKey = 'refresh' | 'check' | 'enable' | 'disable' | 'checkin' | 'delete'

export function useAccountActions(deps: AccountActionDeps) {
  const toast = useToast()
  const confirm = useConfirm()
  const { t } = useI18n()

  async function refreshAll(): Promise<void> {
    const total = deps.rows.value.length
    if (!total) {
      toast.warning(t('page.accounts.refreshNone'))
      return
    }
    const ok = await confirm({
      title: t('page.accounts.refreshTitle', { count: total }),
      message: t('page.accounts.refreshBody'),
      okText: t('page.accounts.refreshOk'),
    })
    if (!ok) return
    try {
      deps.startJob(await refreshBatch())
    } catch (error) {
      toast.error(failureText(t('page.accounts.refreshFailed'), error))
    }
  }

  async function checkAll(): Promise<void> {
    const ids = deps.rows.value.map((row) => row.id)
    if (!ids.length) {
      toast.warning(t('page.accounts.checkNone'))
      return
    }
    const ok = await confirm({
      title: t('page.accounts.checkTitle', { count: ids.length }),
      message: t('page.accounts.checkBody'),
      okText: t('page.accounts.checkOk'),
    })
    if (!ok) return
    try {
      deps.startJob(await healthCheckBatch(ids))
    } catch (error) {
      toast.error(failureText(t('page.accounts.checkFailed'), error))
    }
  }

  async function runGrowth(): Promise<void> {
    // 成长接口遇到非 WorkBuddy 账号返回 400（data-scoping D2）：只提交本身支持成长任务的已启用账号。
    const ids = deps.rows.value
      .filter((row) => row.statusKey === 'active' && row.features.growth)
      .map((row) => row.id)
    if (!ids.length) {
      toast.warning(t('page.accounts.growthNone'))
      return
    }
    const ok = await confirm({
      title: t('page.accounts.growthTitle'),
      message: t('page.accounts.growthBody'),
      okText: t('page.accounts.growthOk'),
    })
    if (!ok) return
    try {
      deps.startJob(await growthRunAsync(ids))
    } catch (error) {
      toast.error(failureText(t('page.accounts.growthFailed'), error))
    }
  }

  async function runCatTravel(): Promise<void> {
    const ok = await confirm({
      title: t('page.accounts.catTitle'),
      message: t('page.accounts.catBody'),
      okText: t('page.accounts.growthOk'),
    })
    if (!ok) return
    try {
      // 不传 ids：后端只处理当前作用域内的 WorkBuddy 账号。
      deps.startJob(await catTravelBatchAsync())
    } catch (error) {
      toast.error(failureText(t('page.accounts.catFailed'), error))
    }
  }

  async function refreshOne(row: AccountRow): Promise<void> {
    try {
      const result = await refreshAccount(row.id)
      toast.success(t('page.accounts.refreshedOne', { name: row.name, remain: fmtInt(result.balance_remain) }))
      await deps.load()
    } catch (error) {
      toast.error(failureText(t('page.accounts.refreshOne', { name: row.name }), error))
    }
  }

  async function checkOne(row: AccountRow): Promise<void> {
    try {
      deps.startJob(await healthCheckAccount(row.id))
    } catch (error) {
      toast.error(failureText(t('page.accounts.checkOne', { name: row.name }), error))
    }
  }

  async function toggleOne(row: AccountRow, enable: boolean): Promise<void> {
    const title = enable
      ? t('page.accounts.toggleTitleEnable', { name: row.name })
      : t('page.accounts.toggleTitleDisable', { name: row.name })
    const ok = await confirm({
      title,
      message: enable ? t('page.accounts.toggleBodyEnable') : t('page.accounts.toggleBodyDisable'),
      okText: enable ? t('page.accounts.actEnable') : t('page.accounts.actDisable'),
      danger: !enable,
    })
    if (!ok) return
    try {
      await patchAccount(row.id, { status: enable ? 'active' : 'disabled' })
      toast.success(
        enable
          ? t('page.accounts.toggledEnable', { name: row.name })
          : t('page.accounts.toggledDisable', { name: row.name }),
      )
      await deps.load()
    } catch (error) {
      toast.error(failureText(title, error))
    }
  }

  async function checkinOne(row: AccountRow): Promise<void> {
    try {
      const result = await runAccountTask(row.id, 'checkin')
      if (result.supported === false) {
        toast.warning(result.reason || t('page.accounts.checkinUnsupported'))
      } else if (result.ok) {
        toast.success(
          t('page.accounts.checkinDone', {
            name: row.name,
            summary: result.summary ? t('page.accounts.checkinSummary', { summary: result.summary }) : '',
          }),
        )
      } else {
        toast.error(
          t('page.accounts.checkinFailed', {
            reason: result.summary || result.reason || t('page.accounts.authUnknownReason'),
          }),
        )
      }
      await deps.load()
    } catch (error) {
      toast.error(failureText(t('page.accounts.checkinOne', { name: row.name }), error))
    }
  }

  async function removeOne(row: AccountRow): Promise<void> {
    const ok = await confirm({
      title: t('page.accounts.deleteOneTitle', { name: row.name }),
      message: t('page.accounts.deleteOneBody'),
      okText: t('common.delete'),
      danger: true,
    })
    if (!ok) return
    try {
      await deleteAccount(row.id)
      toast.success(t('page.accounts.deletedOne', { name: row.name }))
      deps.onDeleted(row.id)
      await deps.load()
    } catch (error) {
      toast.error(failureText(t('page.accounts.deleteOneTitle', { name: row.name }), error))
    }
  }

  async function runRowAction(row: AccountRow, key: string): Promise<boolean> {
    if (key === 'refresh') await refreshOne(row)
    else if (key === 'check') await checkOne(row)
    else if (key === 'enable' || key === 'disable') await toggleOne(row, key === 'enable')
    else if (key === 'checkin') await checkinOne(row)
    else if (key === 'delete') await removeOne(row)
    else return false
    return true
  }

  return { refreshAll, checkAll, runGrowth, runCatTravel, runRowAction }
}
