<script setup lang="ts">
/**
 * S10 IP 管理 /ip —— 入口（数据、路由同步、行操作）。视觉按 10-visual-refresh.md：
 *
 *   PageHeader（唯一主按钮「封禁 IP」）
 *   KPI × 4（ip/IpKpis）
 *   页签：活跃 IP / 封禁名单 / 设备关联（?view=，右侧一行说明）
 *   每个页签一张表，表格上方一行工具栏（筛选 … 统计 · 重置 · 刷新）：
 *     ip/IpActiveView（服务端分页排序，点行打开 ip/IpDetailDrawer）
 *     ip/IpBlockedView（来源列区分自动 / 手动，「清理自动封禁」一键删除 auto: 记录）
 *     ip/IpDevicesView
 *   封禁表单：ip/IpBlockDrawer（提交前在这里二次确认）
 *
 * 契约：docs/ui-v2/contracts/ip.md。本页不随平台切换重载（ip 不在 PLATFORM_SCOPED_KEYS）。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter, type LocationQuery } from 'vue-router'
import { Ban } from 'lucide-vue-next'
import PageHeader from '@/layout/PageHeader.vue'
import { Btn, HelpTip, Segmented, type SegmentedOption } from '@/components'
import { useConfirm, useRevisit, useTableQuery, useToast } from '@/composables'
import { fmtInt } from '@/utils/format'
import { errorMessage, failureText } from '@/utils/error'
import { useI18n, type MessageKey } from '@/i18n'
import { ApiError, isAbortError } from '@/api/http'
import {
  activityQueryFromState,
  blockIp,
  blockedExpirySupported,
  blockedSourceStats,
  cleanBlockedRecords,
  deleteBlockedIp,
  getIpActivity,
  isAutoBlock,
  listBlockedIps,
  listIpDevices,
  patchBlockedIp,
  toActivityRows,
  toBlockedRows,
  toDeviceRows,
  updateIpDevice,
  type BlockedIpItem,
  type BlockedIpRow,
  type IpActivityPayload,
  type IpActivityRow,
  type IpDeviceRow,
} from '@/api/ip'
import IpKpis from './ip/IpKpis.vue'
import IpActiveView from './ip/IpActiveView.vue'
import IpBlockedView from './ip/IpBlockedView.vue'
import IpDevicesView from './ip/IpDevicesView.vue'
import IpBlockDrawer from './ip/IpBlockDrawer.vue'
import IpDetailDrawer from './ip/IpDetailDrawer.vue'
import type { IpBlockSubmit, IpCleanProgress, IpErrorState } from './ip/types'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const toast = useToast()
const confirm = useConfirm()

const helpText = computed(() => t('page.ip.help'))

type IpView = 'active' | 'blocked' | 'devices'

function normalizeView(raw: unknown): IpView {
  const value = String(raw ?? '')
  return value === 'blocked' || value === 'devices' ? value : 'active'
}

const view = ref<IpView>(normalizeView(route.query.view))
/** r4：KeepAlive 缓存中的页面仍会收到其它页面的路由变化，只认本页路由。 */
const ownRouteName = route.name
watch(
  () => route.query.view,
  (raw) => {
    if (route.name !== ownRouteName) return
    const next = normalizeView(raw)
    if (next !== view.value) view.value = next
  },
)

const viewOptions = computed<SegmentedOption<string>[]>(() => [
  { label: t('page.ip.view.active'), value: 'active' },
  { label: t('page.ip.view.blocked'), value: 'blocked' },
  { label: t('page.ip.view.devices'), value: 'devices' },
])

const activeTable = useTableQuery('ip', {
  pageSize: 20,
  sortBy: 'requests',
  sortOrder: 'desc',
  filters: { days: '1', q: '', status: '', usage: '', min: '0', max: '' },
})
const blockedTable = useTableQuery('ip-blocked', { pageSize: 20, filters: { bq: '', bs: '', src: '' } })
const deviceTable = useTableQuery('ip-devices', { pageSize: 20, filters: { dq: '', ds: '' } })

/* ------------------------------------------------------------------ *
 * 取数（三块互不阻塞；AbortController 只保留最后一次）
 * ------------------------------------------------------------------ */

const activity = ref<IpActivityPayload | null>(null)
const activityLoading = ref(false)
const activityError = ref<IpErrorState | null>(null)

const blockedItems = ref<BlockedIpItem[] | null>(null)
/** 列表级 expiry_supported（r3 后端；空列表时也能据此开放「时长」）。 */
const blockedExpiryFlag = ref(false)
const blockedLoading = ref(false)
const blockedError = ref<IpErrorState | null>(null)

const devices = ref<IpDeviceRow[] | null>(null)
const deviceLoading = ref(false)
const deviceError = ref<IpErrorState | null>(null)

let activityController: AbortController | null = null
let blockedController: AbortController | null = null
let deviceController: AbortController | null = null

function prettyDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail
  try {
    return JSON.stringify(detail, null, 2)
  } catch {
    return String(detail)
  }
}

function toErrorState(error: unknown): IpErrorState {
  return {
    message: errorMessage(error),
    detail: error instanceof ApiError && error.detail ? prettyDetail(error.detail) : '',
  }
}

const activityQuery = computed(() => activityQueryFromState(activeTable.state))

async function loadActivity(): Promise<void> {
  activityController?.abort()
  const current = new AbortController()
  activityController = current
  activityLoading.value = true
  activityError.value = null
  try {
    const payload = await getIpActivity(activityQuery.value, current.signal)
    activity.value = payload
    // 后端 offset 越界时返回空页而不是报错：按 total_ips 把页码纠正回来。
    const total = Number(payload?.total_ips) || 0
    const maxPage = Math.max(1, Math.ceil(total / activeTable.state.pageSize))
    if (activeTable.state.page > maxPage) activeTable.setPage(maxPage)
  } catch (error) {
    if (isAbortError(error)) return
    activityError.value = toErrorState(error)
    toast.error(
      failureText(activity.value === null ? t('page.ip.action.loadActivity') : t('page.ip.action.refreshActivity'), error),
    )
  } finally {
    if (activityController === current) {
      activityController = null
      activityLoading.value = false
    }
  }
}

async function loadBlocked(): Promise<void> {
  blockedController?.abort()
  const current = new AbortController()
  blockedController = current
  blockedLoading.value = true
  blockedError.value = null
  try {
    const payload = await listBlockedIps(current.signal)
    blockedItems.value = Array.isArray(payload?.items) ? payload.items : []
    blockedExpiryFlag.value = payload?.expiry_supported === true
  } catch (error) {
    if (isAbortError(error)) return
    blockedError.value = toErrorState(error)
    toast.error(
      failureText(blockedItems.value === null ? t('page.ip.action.loadBlocked') : t('page.ip.action.refreshBlocked'), error),
    )
  } finally {
    if (blockedController === current) {
      blockedController = null
      blockedLoading.value = false
    }
  }
}

async function loadDevices(): Promise<void> {
  deviceController?.abort()
  const current = new AbortController()
  deviceController = current
  deviceLoading.value = true
  deviceError.value = null
  try {
    const payload = await listIpDevices(current.signal)
    devices.value = toDeviceRows(Array.isArray(payload?.items) ? payload.items : [])
  } catch (error) {
    if (isAbortError(error)) return
    deviceError.value = toErrorState(error)
    toast.error(
      failureText(devices.value === null ? t('page.ip.action.loadDevices') : t('page.ip.action.refreshDevices'), error),
    )
  } finally {
    if (deviceController === current) {
      deviceController = null
      deviceLoading.value = false
    }
  }
}

watch(
  () => JSON.stringify(activityQuery.value),
  () => {
    void loadActivity()
  },
)

const blockedRows = computed<BlockedIpRow[] | null>(() =>
  blockedItems.value === null ? null : toBlockedRows(blockedItems.value),
)
const blockedStats = computed(() => (blockedRows.value === null ? null : blockedSourceStats(blockedRows.value)))
/** 后端返回 expires_at / expiry_supported 才开放「时长」与「到期」列（老后端 → 只允许永久）。 */
const expirySupported = computed(() => blockedExpirySupported(blockedItems.value, blockedExpiryFlag.value))
const activityRows = computed(() => toActivityRows(activity.value?.items, activity.value?.current_ip))

const kpiLoading = computed(() => activityLoading.value || blockedLoading.value || deviceLoading.value)

/* ------------------------------------------------------------------ *
 * 页签
 * ------------------------------------------------------------------ */

const viewDesc = computed(() => {
  if (view.value === 'blocked') return t('page.ip.blocked.help')
  if (view.value === 'devices') return t('page.ip.devices.help')
  return t('page.ip.stat.requestsFoot')
})
const viewNote = computed(() => {
  if (view.value === 'blocked') return expirySupported.value ? t('page.ip.blocked.expiryHelp') : t('page.ip.blocked.permanentOnly')
  if (view.value === 'devices') return t('page.ip.devices.note')
  return ''
})
const viewNoteLabel = computed(() =>
  view.value === 'devices' ? t('page.ip.devices.noteLabel') : t('page.ip.blocked.expiryHelpLabel'),
)

function setView(raw: string | number): void {
  const next = normalizeView(raw)
  if (next === view.value) return
  view.value = next
  const query: LocationQuery = { ...route.query, view: next }
  delete query.page
  void router.replace({ query })
  if (next === 'active' && activity.value === null && !activityLoading.value) void loadActivity()
  if (next === 'blocked' && blockedItems.value === null && !blockedLoading.value) void loadBlocked()
  if (next === 'devices' && devices.value === null && !deviceLoading.value) void loadDevices()
}

/* ------------------------------------------------------------------ *
 * 详情抽屉（活跃 IP 行）
 * ------------------------------------------------------------------ */

const DAYS_KEYS: Record<string, MessageKey> = {
  '1': 'page.ip.days.d1',
  '7': 'page.ip.days.d7',
  '30': 'page.ip.days.d30',
  '90': 'page.ip.days.d90',
}

const detailOpen = ref(false)
const detailIp = ref('')
const detailSnapshot = ref<IpActivityRow | null>(null)
/** 刷新后取最新一行；本页已不在列表里时保留打开时的快照。 */
const detailRow = computed<IpActivityRow | null>(
  () => activityRows.value.find((row) => row.ip === detailIp.value) || detailSnapshot.value,
)
const rangeLabel = computed(() => {
  const key = DAYS_KEYS[activeTable.state.filters.days || '1']
  return key ? t(key) : ''
})

function openDetail(row: IpActivityRow): void {
  detailIp.value = row.ip
  detailSnapshot.value = row
  detailOpen.value = true
}

/* ------------------------------------------------------------------ *
 * 行操作
 * ------------------------------------------------------------------ */

function viewLogs(ip: string): void {
  void router.push({ path: '/logs', query: { client_ip: ip } })
}

async function onActivityAction(row: IpActivityRow, key: string): Promise<void> {
  if (key === 'logs') {
    viewLogs(row.ip)
    return
  }
  if (row.blocked) await unblockActivity(row)
  else openBlock(row.ip)
}

async function unblockActivity(row: IpActivityRow): Promise<void> {
  if (row.blockedId === null) {
    toast.warning(t('page.ip.toast.noBlockRecord'))
    return
  }
  try {
    await patchBlockedIp(row.blockedId, { enabled: false })
    toast.success(t('page.ip.toast.unblocked'))
    await Promise.all([loadActivity(), loadBlocked()])
  } catch (error) {
    toast.error(failureText(t('page.ip.action.unblock'), error))
  }
}

async function onBlockedAction(row: BlockedIpRow, key: string): Promise<void> {
  if (key === 'logs') viewLogs(row.ip)
  else if (key === 'delete') await removeBlocked(row)
  else if (key === 'permanent') await makePermanent(row)
  else await toggleBlocked(row)
}

/** 定时封禁改为永久（PATCH permanent: true）。 */
async function makePermanent(row: BlockedIpRow): Promise<void> {
  try {
    await patchBlockedIp(row.id, { permanent: true, enabled: true })
    toast.success(t('page.ip.toast.madePermanent' as MessageKey, { ip: row.ip }))
    await Promise.all([loadBlocked(), loadActivity()])
  } catch (error) {
    toast.error(failureText(t('page.ip.action.makePermanent' as MessageKey), error))
  }
}

async function toggleBlocked(row: BlockedIpRow): Promise<void> {
  if (!row.enabled) {
    const ok = await confirm({
      title: t('page.ip.confirm.reblock.title', { ip: row.ip }),
      message: t('page.ip.confirm.reblock.body'),
      okText: t('page.ip.action.reblock'),
      danger: true,
    })
    if (!ok) return
  }
  try {
    await patchBlockedIp(row.id, { enabled: !row.enabled })
    toast.success(row.enabled ? t('page.ip.toast.unblocked') : t('page.ip.toast.reblocked'))
    await Promise.all([loadBlocked(), loadActivity()])
  } catch (error) {
    toast.error(failureText(row.enabled ? t('page.ip.action.unblock') : t('page.ip.action.reblock'), error))
  }
}

async function removeBlocked(row: BlockedIpRow): Promise<void> {
  const ok = await confirm({
    title: t('page.ip.confirm.delete.title', { ip: row.ip }),
    message: t('page.ip.confirm.delete.body'),
    okText: t('common.delete'),
    danger: true,
  })
  if (!ok) return
  try {
    await deleteBlockedIp(row.id)
    toast.success(t('page.ip.toast.deletedRecord'))
    await Promise.all([loadBlocked(), loadActivity()])
  } catch (error) {
    toast.error(failureText(t('page.ip.action.deleteRecord'), error))
  }
}

/* ---------- 一键清理自动封禁（备注以 auto: 开头） ---------- */

const cleaning = ref<IpCleanProgress | null>(null)

async function cleanAutoBlocks(): Promise<void> {
  if (cleaning.value) return
  const targets = (blockedRows.value || []).filter(isAutoBlock)
  if (!targets.length) {
    toast.warning(t('page.ip.toast.cleanNone'))
    return
  }
  const enabled = targets.filter((row) => row.enabled).length
  const ok = await confirm({
    title: t('page.ip.confirm.cleanAuto.title', { count: fmtInt(targets.length) }),
    message: t('page.ip.confirm.cleanAuto.body', { enabled: fmtInt(enabled) }),
    okText: t('page.ip.confirm.cleanAuto.ok'),
    danger: true,
    // 一次删很多条时要求输入确认文字，防误触（04-components §3）。
    requireText: targets.length >= 20 ? t('page.ip.confirm.cleanAuto.ok') : undefined,
  })
  if (!ok) return
  cleaning.value = { done: 0, total: targets.length }
  try {
    // r3：一次 POST /api/ip/blocked/batch-delete；老后端（404/405）自动回落逐条 DELETE。
    const result = await cleanBlockedRecords(
      targets.map((row) => row.id),
      {
        onProgress: (done, total) => {
          cleaning.value = { done, total }
        },
      },
    )
    if (!result.failed.length) {
      toast.success(t('page.ip.toast.cleanDone', { count: fmtInt(result.deleted.length) }))
    } else {
      toast.error(
        t('page.ip.toast.cleanPartial', {
          done: fmtInt(result.deleted.length),
          failed: fmtInt(result.failed.length),
          reason: errorMessage(result.failed[0].error),
        }),
      )
    }
  } catch (error) {
    toast.error(failureText(t('page.ip.confirm.cleanAuto.ok'), error))
  } finally {
    cleaning.value = null
    await Promise.all([loadBlocked(), loadActivity()])
  }
}

async function toggleDevice(row: IpDeviceRow): Promise<void> {
  if (!row.blocked) {
    const ok = await confirm({
      title: t('page.ip.confirm.blockDevice.title'),
      message: t('page.ip.confirm.blockDevice.body', { value: row.value }),
      okText: t('page.ip.action.blockDevice'),
      danger: true,
    })
    if (!ok) return
  }
  try {
    await updateIpDevice({ kind: row.kind, value: row.value, blocked: !row.blocked, note: row.note })
    toast.success(row.blocked ? t('page.ip.toast.deviceUnblocked') : t('page.ip.toast.deviceBlocked'))
    await loadDevices()
  } catch (error) {
    toast.error(failureText(row.blocked ? t('page.ip.action.unblockDevice') : t('page.ip.action.blockDevice'), error))
  }
}

/* ------------------------------------------------------------------ *
 * 封禁抽屉
 * ------------------------------------------------------------------ */

const drawerOpen = ref(false)
const drawerIp = ref('')
const submitting = ref(false)

function openBlock(ip?: string): void {
  drawerIp.value = ip || ''
  drawerOpen.value = true
}

async function submitBlock(payload: IpBlockSubmit): Promise<void> {
  if (submitting.value) return
  const currentIp = activity.value?.current_ip || ''
  const selfHint = currentIp && payload.ip === currentIp ? t('page.ip.confirm.block.selfHint') : ''
  const ok = await confirm({
    title: t('page.ip.confirm.block.title', { ip: payload.ip }),
    message: t('page.ip.confirm.block.body', { hint: selfHint }),
    okText: t('page.ip.drawer.title'),
    danger: true,
  })
  if (!ok) return
  submitting.value = true
  try {
    await blockIp({
      ip: payload.ip,
      note: payload.note,
      expires_at: payload.expiresAt,
      duration_seconds: payload.durationSeconds ?? null,
    })
    toast.success(t('page.ip.toast.blocked', { ip: payload.ip }))
    drawerOpen.value = false
    await Promise.all([loadActivity(), loadBlocked()])
  } catch (error) {
    toast.error(failureText(t('page.ip.action.blockIp'), error))
  } finally {
    submitting.value = false
  }
}

/* ------------------------------------------------------------------ *
 * 生命周期
 * ------------------------------------------------------------------ */

onMounted(() => {
  void loadActivity()
  void loadBlocked()
  void loadDevices()
})

/**
 * 第 5 轮：二次进入（KeepAlive）/ 后台轮询属于「静默刷新」——DataTable 用 `refreshing`
 * 保留旧数据、只在工具栏转小圈，不出顶部进度条。用计数，重叠的静默刷新互不打断。
 */
const silentDepth = ref(0)
const silentRefresh = computed(() => silentDepth.value > 0)
async function silently(task: () => Promise<unknown> | void): Promise<void> {
  silentDepth.value += 1
  try {
    await task()
  } finally {
    silentDepth.value -= 1
  }
}

/**
 * 二次进入（KeepAlive 命中）：保留三块旧数据，后台静默刷新（各块骨架只在「从未加载过」时出现）。
 * 离开时中止还在路上的请求；清理中不打断。
 */
useRevisit(
  () => {
    if (cleaning.value) return
    void silently(() => Promise.all([loadActivity(), loadBlocked(), loadDevices()]))
  },
  {
    minIntervalMs: 10_000,
    onLeave: () => {
      activityController?.abort()
      deviceController?.abort()
    },
  },
)

onBeforeUnmount(() => {
  activityController?.abort()
  blockedController?.abort()
  deviceController?.abort()
  activityController = null
  blockedController = null
  deviceController = null
})
</script>

<template>
  <div class="ip-page mh-page mh-page--wide">
    <PageHeader :title="t('page.ip.title')" :help="helpText">
      <template #actions>
        <Btn variant="primary" :icon="Ban" @click="openBlock()">{{ t('page.ip.action.blockIp') }}</Btn>
      </template>
    </PageHeader>

    <IpKpis
      :activity="activity"
      :blocked-stats="blockedStats"
      :device-count="devices ? devices.length : null"
      :loading="kpiLoading"
    />

    <div class="ip-tabs">
      <Segmented
        :options="viewOptions"
        :model-value="view"
        :aria-label="t('page.ip.viewAria')"
        @update:model-value="setView"
      />
      <p class="ip-tabs__desc">{{ viewDesc }}</p>
      <HelpTip v-if="viewNote" :content="viewNote" :label="viewNoteLabel" />
    </div>

    <IpActiveView
      v-if="view === 'active'"
      :table="activeTable"
      :payload="activity"
      :loading="activityLoading && !silentRefresh"
      :refreshing="activityLoading && silentRefresh"
      :error="activityError"
      @action="onActivityAction"
      @open="openDetail"
      @refresh="loadActivity()"
      @retry="loadActivity()"
    />
    <IpBlockedView
      v-else-if="view === 'blocked'"
      :table="blockedTable"
      :rows="blockedRows"
      :loading="blockedLoading && !silentRefresh"
      :refreshing="blockedLoading && silentRefresh"
      :error="blockedError"
      :expiry-supported="expirySupported"
      :cleaning="cleaning"
      @action="onBlockedAction"
      @clean-auto="cleanAutoBlocks"
      @create="openBlock()"
      @refresh="loadBlocked()"
      @retry="loadBlocked()"
    />
    <IpDevicesView
      v-else
      :table="deviceTable"
      :rows="devices"
      :loading="deviceLoading && !silentRefresh"
      :refreshing="deviceLoading && silentRefresh"
      :error="deviceError"
      @toggle="toggleDevice"
      @refresh="loadDevices()"
      @retry="loadDevices()"
    />

    <IpDetailDrawer
      v-model:show="detailOpen"
      :row="detailRow"
      :range-label="rangeLabel"
      @action="onActivityAction"
    />

    <IpBlockDrawer
      v-model:show="drawerOpen"
      :initial-ip="drawerIp"
      :expiry-supported="expirySupported"
      :submitting="submitting"
      @submit="submitBlock"
    />
  </div>
</template>

<style scoped>
.ip-page {
  display: grid;
  gap: var(--sp-4);
  min-width: 0;
}

.ip-tabs {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  flex-wrap: wrap;
  min-width: 0;
  margin-top: var(--sp-2);
}

.ip-tabs__desc {
  margin: 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

@media (max-width: 640px) {
  .ip-tabs {
    gap: var(--sp-2);
  }
}
</style>
