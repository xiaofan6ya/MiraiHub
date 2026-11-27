<script setup lang="ts">
import TitleTip from '@/components/base/TitleTip.vue'

/**
 * S18 风控哨兵 /sentinel（设计稿 06-pages-a P2，模板 A）。
 *
 * 契约：docs/ui-v2/contracts/sentinel.md（尾部「## 实现对照」）。
 * 旧版：#panel-sentinel + console-app.js 的 loadSentinel / renderSentinel / sentinelPause /
 * sentinelResume / sentinelPollLogs（traffic.js 只是拓扑动画，新版用 RouteRibbon 取代）。
 *
 * 两条轮询：
 *   · /api/sentinel/traffic 每 2 秒（实时请求列表 + 线路带 + 平台健康）；
 *   · /api/sentinel/status 每 10 秒（信号、熔断判定、暂停开关）。
 * 都走 usePolling：页面不可见自动暂停、连续失败 3 次停表并给「恢复」。
 */
import { computed, h, onMounted, ref, type VNodeChild } from 'vue'
import { NInput, NModal, NSelect } from 'naive-ui'
import { Pause, RefreshCw } from 'lucide-vue-next'
import PageHeader from '@/layout/PageHeader.vue'
import {
  Badge,
  Btn,
  Card,
  DataTable,
  Empty,
  ErrorBlock,
  KpiCard,
  RouteRibbon,
  RowActions,
  Segmented,
  StackMeter,
  type FilterChip,
  type SorterPayload,
  type TableColumn,
  type TableRow,
} from '@/components'
import { useConfirm, useKeepAliveSuspended, usePolling, useTableQuery, useToast } from '@/composables'
import { errorMessage, failureText } from '@/utils/error'
import { FMT_EMPTY, fmtInt } from '@/utils/format'
import { groupTitle, useI18n } from '@/i18n'
import { groupOf } from '@/router/pages'
import SentinelStatusPanel from './Sentinel/SentinelStatusPanel.vue'
import SentinelLive from './Sentinel/SentinelLive.vue'
import { useRevisit } from '@/pages/_shared/useRevisit'
import {
  getSentinelStatus,
  getTraffic,
  pausePlatform,
  pausedKeys,
  pausedLabel,
  resumePlatform,
  storageText,
  toAccountRows,
  toBanner,
  toLiveRows,
  toPlatformHealth,
  toRibbon,
  toSentinelKpis,
  toSignalRows,
  toStatusLine,
  type AccountSignalRow,
  type SentinelStatus,
  type SignalRow,
  type TrafficPayload,
} from '@/api/sentinel'

const { t } = useI18n()
const helpText = computed(() => t('page.sentinel.help'))
const eyebrow = computed(() => groupTitle(groupOf('sentinel').key))

const toast = useToast()
/** 第 5 轮：KeepAlive 离开时收起本页直接使用的 NModal（Teleport 不随页面移走，否则会盖在下一页上）。 */
const suspended = useKeepAliveSuspended()
const confirm = useConfirm()
const table = useTableQuery('sentinel', {
  pageSize: 20,
  sortBy: 'time',
  sortOrder: 'desc',
  filters: { tab: 'signals', window: '300', platform: '' },
})

const status = ref<SentinelStatus | null>(null)
const traffic = ref<TrafficPayload | null>(null)
const loading = ref(true)
const statusError = ref('')
const firstStatus = ref(true)

const windowRef = computed(() => Number(table.state.filters.window) || 300)

async function fetchStatus(): Promise<void> {
  try {
    const payload = await getSentinelStatus({ window: windowRef.value })
    status.value = payload
    statusError.value = ''
    if (payload.traffic) traffic.value = payload.traffic
  } catch (error) {
    statusError.value = errorMessage(error)
    throw error
  } finally {
    firstStatus.value = false
    loading.value = false
  }
}

async function fetchTraffic(): Promise<void> {
  const payload = await getTraffic(3600)
  traffic.value = payload
}

const statusPoll = usePolling(fetchStatus, 10000)
const trafficPoll = usePolling(fetchTraffic, 2000)

onMounted(() => {
  statusPoll.start()
  trafficPoll.start()
})

/**
 * 手动刷新才显示小转圈；后台轮询静默（以前把 `statusPoll.running` 当 loading，
 * 轮询一直在跑 → 状态区 4 张 KPI 右上角永远在转）。
 */
const refreshing = ref(false)

function refresh(): void {
  refreshing.value = true
  void Promise.all([statusPoll.trigger(), trafficPoll.trigger()])
    .catch(() => undefined)
    .finally(() => {
    refreshing.value = false
  })
}

// KeepAlive：离开时停掉 10s / 2s 两个轮询（缓存页不是 document.hidden，usePolling 自己停不了），
// 回来时 start() 立即各拉一次，保留旧数据，不出骨架（11 §2）。
useRevisit(
  () => {
    statusPoll.start()
    trafficPoll.start()
  },
  {
    onLeave: () => {
      statusPoll.stop()
      trafficPoll.stop()
    },
  },
)

/* ------------------------------ 视图模型 ------------------------------ */
const banner = computed(() => toBanner(status.value, traffic.value))

/**
 * 统计口径（本轮审计纠正）：traffic 的 platforms / totals 是 traffic_stats 按**服务器日期**分键的
 * 「今日累计」，window=3600 只影响 recent 明细 —— 上一轮把第 0 格改名成「近 1 小时请求」是错的。
 * 状态总览只留 4 个 KpiCard；「涉及账号」「暂停中的平台」并进状态行（toStatusLine）。
 */
const kpis = computed(() => toSentinelKpis(status.value, traffic.value, t))
const statusLine = computed(() => toStatusLine(status.value, traffic.value, t))
const ribbon = computed(() => toRibbon(traffic.value, t))
/**
 * 第 5 轮：RouteRibbon 脉冲按「count / windowMinutes」换算。traffic 的计数是**今日累计**，
 * 时间窗取今天已过去的分钟数（至少 60，凌晨刚过零点时不至于快到顶）；随 traffic 每次刷新重算。
 */
const ribbonWindowMinutes = computed(() => {
  void traffic.value
  const now = new Date()
  return Math.max(60, now.getHours() * 60 + now.getMinutes())
})
const health = computed(() => toPlatformHealth(traffic.value, t))
const liveRows = computed(() => toLiveRows(traffic.value, 50, t))
const signalRows = computed(() => toSignalRows(status.value, traffic.value, t))
const accountRows = computed(() => toAccountRows(status.value, traffic.value, t))
const paused = computed(() => pausedKeys(status.value))
const pausedItems = computed(() =>
  paused.value.map((key) => ({ key, label: pausedLabel(key, traffic.value, t) })),
)
const storage = computed(() => storageText(status.value, traffic.value, t))

const platformLabelOf = computed(() => {
  const map = new Map<string, string>()
  for (const row of traffic.value?.platforms || []) map.set(row.platform, row.label || row.platform)
  for (const key of paused.value) map.set(key, pausedLabel(key, traffic.value, t))
  return map
})

const tab = computed(() => (table.state.filters.tab === 'accounts' ? 'accounts' : 'signals'))
const platformFilter = computed(() => table.state.filters.platform)

const platformOptions = computed(() => {
  const options = [{ label: t('page.sentinel.allPlatforms'), value: '' }]
  for (const row of traffic.value?.platforms || []) {
    options.push({ label: row.label || row.platform, value: row.platform })
  }
  for (const key of paused.value) {
    if (!options.some((option) => option.value === key)) {
      options.push({ label: pausedLabel(key, traffic.value, t), value: key })
    }
  }
  return options
})

const windowOptions = computed(() => [
  { label: t('page.sentinel.window.5'), value: '300' },
  { label: t('page.sentinel.window.10'), value: '600' },
  { label: t('page.sentinel.window.30'), value: '1800' },
])

const chips = computed<FilterChip[]>(() => {
  const list: FilterChip[] = []
  if (platformFilter.value) {
    list.push({
      key: 'platform',
      label: t('page.sentinel.chip.platform'),
      value: platformLabelOf.value.get(platformFilter.value) || platformFilter.value,
    })
  }
  if (table.state.filters.window && table.state.filters.window !== '300') {
    const option = windowOptions.value.find((item) => item.value === table.state.filters.window)
    list.push({ key: 'window', label: t('page.sentinel.chip.window'), value: option?.label || table.state.filters.window })
  }
  return list
})

const SORT_KEYS: Record<string, string> = { time: 'time', accountId: 'account', count: 'count', lastTs: 'last' }

function sortOrderOf(key: string): 'ascend' | 'descend' | false {
  if (table.state.sortBy !== key) return false
  if (table.state.sortOrder === 'asc') return 'ascend'
  return table.state.sortOrder === 'desc' ? 'descend' : false
}

function onSorter(sorter: SorterPayload | null): void {
  const key = sorter && sorter.order ? SORT_KEYS[String(sorter.columnKey)] : ''
  if (!key) {
    table.setSort('time', 'desc')
    return
  }
  table.setSort(key, sorter!.order === 'ascend' ? 'asc' : 'desc')
}

function filteredSignals(): SignalRow[] {
  const platform = platformFilter.value
  const rows = platform ? signalRows.value.filter((row) => row.platform === platform) : signalRows.value
  return rows
}

function filteredAccounts(): AccountSignalRow[] {
  const platform = platformFilter.value
  return platform ? accountRows.value.filter((row) => row.platform === platform) : accountRows.value
}

function sortedByTime<T extends { ts: number }>(rows: T[]): T[] {
  if (table.state.sortBy !== 'time') return rows
  const factor = table.state.sortOrder === 'asc' ? 1 : -1
  return [...rows].sort((a, b) => (a.ts - b.ts) * factor)
}

function sortedAccounts(rows: AccountSignalRow[]): AccountSignalRow[] {
  const factor = table.state.sortOrder === 'asc' ? 1 : -1
  if (table.state.sortBy === 'account') return [...rows].sort((a, b) => (a.accountId - b.accountId) * factor)
  if (table.state.sortBy === 'count') return [...rows].sort((a, b) => (a.count - b.count) * factor)
  if (table.state.sortBy === 'last') return [...rows].sort((a, b) => (a.lastTs - b.lastTs) * factor)
  return rows
}

const tableRows = computed<TableRow[]>(() => {
  const rows = tab.value === 'accounts'
    ? sortedAccounts(filteredAccounts())
    : (sortedByTime(filteredSignals()) as unknown as TableRow[])
  return rows as unknown as TableRow[]
})

function paginate<T>(rows: T[]): T[] {
  const start = (table.state.page - 1) * table.state.pageSize
  return rows.slice(start, start + table.state.pageSize)
}

const pageRows = computed<TableRow[]>(() => paginate(tableRows.value))

function onTab(value: string | number): void {
  table.setFilter('tab', String(value))
  table.setPage(1)
}

function onWindow(value: string | number | null): void {
  table.setFilter('window', value == null ? '' : String(value))
  void statusPoll.trigger()
}

function onPlatformFilter(value: string | number | null): void {
  table.setFilter('platform', value == null ? '' : String(value))
  table.setPage(1)
}

function removeChip(key: string): void {
  table.setFilter(key, key === 'window' ? '300' : '')
  if (key === 'window') void statusPoll.trigger()
}

function resetFilters(): void {
  table.reset()
  void statusPoll.trigger()
}

function selectPlatform(platformId: string): void {
  onPlatformFilter(platformFilter.value === platformId ? '' : platformId)
}

/* ------------------------------ 单元格 ------------------------------ */
function cellPlatform(row: TableRow): string {
  return String(row.platformLabel || FMT_EMPTY)
}

function cellAccount(row: TableRow): string {
  const id = Number(row.accountId || 0)
  return id ? '#' + id : FMT_EMPTY
}

function cellSeverity(row: TableRow): VNodeChild {
  const tone = String(row.severityTone || 'neutral') as 'ok' | 'warn' | 'danger' | 'neutral' | 'brand'
  return h(TitleTip, { content: String((String(row.signalLabel || '')) ?? '') }, { default: () => h(
    Badge,
    { tone },
    { default: () => String(row.severityLabel || '') },
  ) })
}

function cellCode(row: TableRow): VNodeChild {
  const unmapped = Boolean(row.unmapped)
  return h(TitleTip, { content: String((unmapped ? t('page.sentinel.unmappedTip') : '') ?? '') }, { default: () => h(
    'span',
    {
      class: 'mono',
    },
    String(row.code || '-') + (unmapped ? t('page.sentinel.unmappedTag') : ''),
  ) })
}

function cellReason(row: TableRow): VNodeChild {
  const label = String(row.label || '')
  const signal = String(row.signalLabel || '')
  const detail = String(row.detail || '')
  return h(TitleTip, { content: String((detail) ?? '') }, { default: () => h('span', { class: 'sentinel-reason' }, signal ? signal + ' · ' + label : label || FMT_EMPTY) })
}

function cellActions(row: TableRow): VNodeChild {
  const platform = String(row.platform || '')
  const isPaused = paused.value.includes(platform) || paused.value.includes('all')
  return h(RowActions, {
    actions: [
      isPaused
        ? { key: 'resume', label: t('page.sentinel.row.resume') }
        : { key: 'pause', label: t('page.sentinel.row.pause'), danger: true, disabled: !platform || platform === 'unknown' },
    ],
    max: 1,
    onSelect: (key: string) => void onRowAction(row, key),
  })
}

/* ------------------------------ 止损 ------------------------------ */
const pauseOpen = ref(false)
const pauseTarget = ref('')
const pauseSeconds = ref('600')
const pauseReason = ref('')
const pausing = ref(false)

const pauseSecondOptions = computed(() => [
  { label: t('page.sentinel.pauseSeconds.10'), value: '600' },
  { label: t('page.sentinel.pauseSeconds.30'), value: '1800' },
  { label: t('page.sentinel.pauseSeconds.untilManual'), value: '0' },
])

function openPause(platform = '', seconds = 600): void {
  pauseTarget.value = platform
  pauseSeconds.value = String(seconds)
  pauseReason.value = t('page.sentinel.reasonDefault')
  pauseOpen.value = true
}

async function submitPause(): Promise<void> {
  const name = pauseTarget.value
    ? platformLabelOf.value.get(pauseTarget.value) || pauseTarget.value
    : t('page.sentinel.allPlatforms')
  const seconds = Number(pauseSeconds.value) || 0
  const duration = seconds > 0
    ? t('page.sentinel.pauseDuration.minutes', { minutes: Math.round(seconds / 60) })
    : t('page.sentinel.pauseSeconds.untilManual')
  const ok = await confirm({
    title: t('page.sentinel.confirmPause.title', { name }),
    message: t('page.sentinel.confirmPause.body', { duration }),
    danger: true,
    okText: t('page.sentinel.confirmPause.ok'),
  })
  if (!ok) return
  pausing.value = true
  try {
    await pausePlatform({
      platform: pauseTarget.value,
      seconds,
      reason: pauseReason.value || t('page.sentinel.reasonDefault'),
    })
    pauseOpen.value = false
    toast.success(t('page.sentinel.toast.paused', { name }))
    refresh()
  } catch (error) {
    toast.error(failureText(t('page.sentinel.action.pause'), error))
  } finally {
    pausing.value = false
  }
}

async function onResume(platform = ''): Promise<void> {
  const name = platform
    ? platformLabelOf.value.get(platform) || platform
    : t('page.sentinel.allPlatforms')
  const ok = await confirm({
    title: t('page.sentinel.confirmResume.title', { name }),
    message: t('page.sentinel.confirmResume.body'),
    okText: t('page.sentinel.confirmResume.ok'),
  })
  if (!ok) return
  try {
    const result = await resumePlatform(platform)
    // 恢复单个平台时，全局暂停仍在（data-scoping D4：后端不再顺手解除 all）——必须讲清楚，
    // 否则用户以为恢复了，请求却还是 503。
    if (platform && result && result.still_paused_by_all) {
      toast.warning(t('page.sentinel.toast.stillPausedByAll', { name }))
    } else {
      toast.success(t('page.sentinel.toast.resumed', { name }))
    }
    refresh()
  } catch (error) {
    toast.error(failureText(t('page.sentinel.action.resume'), error))
  }
}

async function onRowAction(row: TableRow, key: string): Promise<void> {
  const platform = String(row.platform || '')
  if (key === 'pause') {
    openPause(platform, banner.value.suggestedSeconds)
    return
  }
  if (key === 'resume') await onResume(platform)
}

/* ------------------------------ 表格列 ------------------------------ */
const columns = computed<TableColumn[]>(() =>
  tab.value === 'accounts'
    ? [
        { key: 'platformLabel', title: t('page.sentinel.col.platform'), mobile: 'title', width: 140 },
        {
          key: 'accountId',
          title: t('page.sentinel.col.account'),
          mobile: 'subtitle',
          width: 100,
          numeric: true,
          sortable: true,
          sortOrder: sortOrderOf('account'),
          render: cellAccount,
        },
        { key: 'label', title: t('page.sentinel.col.lastSignal'), mobile: 'meta', minWidth: 220, render: cellReason },
        { key: 'severityLabel', title: t('page.sentinel.col.severity'), mobile: 'meta', width: 110, render: cellSeverity },
        {
          key: 'count',
          title: t('page.sentinel.col.count'),
          mobile: 'meta',
          width: 90,
          numeric: true,
          sortable: true,
          sortOrder: sortOrderOf('count'),
          render: (row: TableRow) => fmtInt(row.count),
        },
        { key: 'firstTime', title: t('page.sentinel.col.first'), mobile: 'meta', width: 120 },
        {
          key: 'lastTime',
          title: t('page.sentinel.col.last'),
          mobile: 'meta',
          width: 130,
          sortable: true,
          sortOrder: sortOrderOf('last'),
        },
      ]
    : [
        {
          key: 'time',
          title: t('page.sentinel.col.time'),
          mobile: 'title',
          width: 120,
          sortable: true,
          sortOrder: sortOrderOf('time'),
        },
        { key: 'platformLabel', title: t('page.sentinel.col.platform'), mobile: 'subtitle', width: 140 },
        {
          key: 'accountId',
          title: t('page.sentinel.col.account'),
          mobile: 'meta',
          width: 100,
          numeric: true,
          sortable: true,
          sortOrder: sortOrderOf('account'),
          render: cellAccount,
        },
        { key: 'code', title: t('page.sentinel.col.code'), mobile: 'meta', width: 130, render: cellCode },
        { key: 'reason', title: t('page.sentinel.col.reason'), mobile: 'meta', minWidth: 220, render: cellReason },
        { key: 'model', title: t('page.sentinel.col.model'), mobile: 'meta', width: 170 },
        {
          key: 'actions',
          title: t('page.sentinel.col.actions'),
          mobile: 'actions',
          width: 130,
          hideable: false,
          render: cellActions,
        },
      ],
)
</script>

<template>
  <div class="sentinel-page">
    <PageHeader :title="t('page.sentinel.title')" :help="helpText" :eyebrow="eyebrow">
      <template #meta>
        <Badge tone="neutral">{{ t('page.sentinel.scope') }}</Badge>
        <span class="sentinel-storage">{{ t('page.sentinel.storage', { backend: storage }) }}</span>
      </template>
      <template #actions>
        <Btn variant="default" :icon="Pause" @click="openPause('', banner.suggestedSeconds)">
          {{ t('page.sentinel.pauseOpen') }}
        </Btn>
        <Btn variant="quiet" :icon="RefreshCw" :loading="refreshing || (loading && !status)" @click="refresh()">
          {{ t('common.refresh') }}
        </Btn>
      </template>
    </PageHeader>

    <ErrorBlock
      v-if="statusError && !status"
      :title="t('page.sentinel.loadFailed', { reason: statusError })"
      :retry-label="t('common.retry')"
      @retry="refresh()"
    />
    <!-- 骨架与真实布局同构：状态行 / 4 张 KpiCard / 左右分栏，统一 .mh-skeleton（11 §4-1）。 -->
    <div v-else-if="loading && !status" class="sentinel-skeleton" aria-busy="true">
      <div class="mh-skeleton sentinel-skeleton__line" />
      <section class="sentinel-skeleton__kpis">
        <KpiCard v-for="item in kpis" :key="item.key" :label="item.label" :value="null" loading />
      </section>
      <div class="sentinel-split">
        <div class="mh-skeleton sentinel-skeleton__main" />
        <div class="mh-skeleton sentinel-skeleton__side" />
      </div>
    </div>

    <template v-else>
      <!-- 状态总览：常态一行、异常横幅 + 4 个 KPI -->
      <SentinelStatusPanel
        :banner="banner"
        :line="statusLine"
        :kpis="kpis"
        :paused="pausedItems"
        :loading="refreshing"
        @pause="(platform: string, seconds: number) => openPause(platform, seconds)"
        @resume="(key: string) => onResume(key === 'all' ? '' : key)"
      />

      <div class="sentinel-split">
        <div class="sentinel-main">
          <Card :title="t('page.sentinel.realtime.title')">
            <template #extra>
              <span class="sentinel-hint">{{ t('page.sentinel.realtime.hint') }}</span>
            </template>
            <RouteRibbon
              :source="ribbon.source"
              :lanes="ribbon.lanes"
              :max-height="300"
              :window-minutes="ribbonWindowMinutes"
              @select="selectPlatform"
            />
            <p class="sentinel-legend">
              <span><i class="dot is-ok" />{{ t('page.sentinel.legend.ok') }}</span>
              <span><i class="dot is-warn" />{{ t('page.sentinel.legend.warn') }}</span>
              <span><i class="dot is-danger" />{{ t('page.sentinel.legend.danger') }}</span>
              <span class="sentinel-muted">
                {{ trafficPoll.paused.value ? trafficPoll.pausedMessage.value : t('page.sentinel.refreshEvery') }}
              </span>
            </p>
          </Card>

          <Card :title="t('page.sentinel.health.title')">
            <template #extra>
              <span class="sentinel-hint">{{ t('page.sentinel.health.hint') }}</span>
            </template>
            <Empty v-if="!health.length" compact :description="t('page.sentinel.health.empty')" />
            <div v-for="row in health" :key="row.platform" class="sentinel-health">
              <div class="sentinel-health__head">
                <span class="sentinel-health__name">{{ row.label }}</span>
                <span class="sentinel-health__rate" :class="'is-' + row.tone">{{ row.rateText }}</span>
                <span class="sentinel-health__count">
                  {{ t('page.sentinel.health.count', { requests: fmtInt(row.requests) }) }}<template v-if="row.failed"> · {{ t('page.sentinel.health.failed', { failed: fmtInt(row.failed) }) }}</template>
                </span>
                <RowActions
                  :actions="[
                    paused.includes(row.platform) || paused.includes('all')
                      ? { key: 'resume', label: t('page.sentinel.row.resume') }
                      : { key: 'pause', label: t('page.sentinel.row.pause'), danger: true },
                  ]"
                  :max="1"
                  @select="(key: string) => onRowAction({ platform: row.platform }, key)"
                />
              </div>
              <StackMeter :items="row.items" :total="row.requests" :height="8" :unit="t('page.sentinel.health.unit')" />
            </div>
            <p v-if="statusPoll.paused.value" class="sentinel-warn">
              {{ statusPoll.pausedMessage.value }}
              <Btn variant="quiet" size="sm" @click="statusPoll.resume()">{{ t('page.sentinel.resume') }}</Btn>
            </p>
          </Card>
        </div>

        <div class="sentinel-side">
          <SentinelLive
            :rows="liveRows"
            :paused="trafficPoll.paused.value"
            :paused-message="trafficPoll.pausedMessage.value"
            @resume="trafficPoll.resume()"
          />
        </div>
      </div>

      <Card flush>
        <div class="sentinel-table-head">
          <Segmented
            :options="[
              { label: t('page.sentinel.tab.signals'), value: 'signals' },
              { label: t('page.sentinel.tab.accounts'), value: 'accounts' },
            ]"
            :model-value="tab"
            :aria-label="t('page.sentinel.tabsAria')"
            @update:model-value="onTab"
          />
          <div class="sentinel-table-filters">
            <n-select
              class="sentinel-filter"
              :value="table.state.filters.window"
              :options="windowOptions"
              @update:value="onWindow"
            />
            <n-select
              class="sentinel-filter"
              :value="platformFilter"
              :options="platformOptions"
              @update:value="onPlatformFilter"
            />
            <Btn
              v-if="chips.length"
              variant="quiet"
              size="sm"
              @click="resetFilters"
            >
              {{ t('common.reset') }}
            </Btn>
          </div>
        </div>
        <div class="sentinel-chips">
          <button
            v-for="chip in chips"
            :key="chip.key"
            type="button"
            class="sentinel-chip"
            @click="removeChip(chip.key)"
          >
            {{ chip.label }}：{{ chip.value }} ✕
          </button>
          <span class="sentinel-muted">
            {{
              tab === 'accounts'
                ? t('page.sentinel.table.accountsNote', { count: filteredAccounts().length })
                : t('page.sentinel.table.signalsNote', { count: filteredSignals().length })
            }}
          </span>
        </div>

        <DataTable
          table-id="sentinel-signals"
          :columns="columns"
          :data="pageRows"
          row-key="id"
          remote
          :loading="statusPoll.running.value && !status"
          :page="table.state.page"
          :page-size="table.state.pageSize"
          :item-count="tableRows.length"
          :page-sizes="[20, 50, 100]"
          :empty-description="t('page.sentinel.empty.accounts')"
          @update:page="table.setPage"
          @update:page-size="table.setPageSize"
          @update:sorter="onSorter"
        >
          <template #empty>
            <Empty
              :description="tab === 'accounts' ? t('page.sentinel.empty.accounts') : t('page.sentinel.empty.signals')"
              compact
            />
          </template>
        </DataTable>
      </Card>
    </template>

    <!-- 暂停平台 -->
    <n-modal
      :show="pauseOpen && !suspended"
      @update:show="(value: boolean) => (pauseOpen = value)"
      preset="card"
      :title="t('page.sentinel.pauseModalTitle')"
      :style="{ width: '480px', maxWidth: 'calc(100vw - 32px)' }"
    >
      <div class="sentinel-form">
        <label class="sentinel-field">
          <span>{{ t('page.sentinel.field.target') }}</span>
          <n-select v-model:value="pauseTarget" :options="platformOptions" />
        </label>
        <label class="sentinel-field">
          <span>{{ t('page.sentinel.field.duration') }}</span>
          <n-select v-model:value="pauseSeconds" :options="pauseSecondOptions" />
        </label>
        <label class="sentinel-field">
          <span>{{ t('page.sentinel.field.reason') }}</span>
          <n-input
            v-model:value="pauseReason"
            :placeholder="t('page.sentinel.reasonPlaceholder')"
            maxlength="200"
          />
        </label>
        <p class="sentinel-hint">{{ t('page.sentinel.pauseTip') }}</p>
      </div>
      <template #footer>
        <div class="sentinel-form__actions">
          <Btn variant="default" @click="pauseOpen = false">{{ t('common.cancel') }}</Btn>
          <Btn variant="danger" solid :loading="pausing" @click="submitPause">
            {{ t('page.sentinel.confirmPause.ok') }}
          </Btn>
        </div>
      </template>
    </n-modal>
  </div>
</template>

<style scoped>
.sentinel-page {
  display: grid;
  gap: var(--sp-4);
  min-width: 0;
}
.sentinel-storage {
  font-size: var(--fs-small);
  color: var(--fg-muted);
}
.sentinel-skeleton {
  display: grid;
  gap: var(--sp-4);
  min-width: 0;
}
/* 与 SentinelStatusPanel 的状态行同高 */
.sentinel-skeleton__line {
  height: 58px;
  border-radius: var(--r-card);
}
.sentinel-skeleton__kpis {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--sp-4);
}
.sentinel-skeleton__main {
  min-height: 420px;
  border-radius: var(--r-card);
}
.sentinel-skeleton__side {
  min-height: 420px;
  border-radius: var(--r-card);
}
@media (max-width: 1279px) {
  .sentinel-skeleton__kpis {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (max-width: 767px) {
  .sentinel-skeleton__kpis {
    grid-template-columns: minmax(0, 1fr);
  }
}
.sentinel-split {
  display: grid;
  grid-template-columns: minmax(0, 2fr) minmax(0, 1fr);
  gap: var(--sp-4);
  align-items: start;
}
.sentinel-main,
.sentinel-side {
  display: grid;
  gap: var(--sp-4);
  min-width: 0;
}
.sentinel-hint {
  font-size: var(--fs-small);
  color: var(--fg-muted);
}
.sentinel-legend {
  display: flex;
  align-items: center;
  gap: var(--sp-4);
  margin-top: var(--sp-2);
  font-size: var(--fs-small);
  color: var(--fg-muted);
  flex-wrap: wrap;
}
.sentinel-legend span {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-1);
}
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
}
.dot.is-ok {
  background: var(--ok);
}
.dot.is-warn {
  background: var(--warn);
}
.dot.is-danger {
  background: var(--danger);
}
.sentinel-muted {
  color: var(--fg-muted);
  font-size: var(--fs-small);
}
.sentinel-health {
  display: grid;
  gap: var(--sp-1);
  padding: var(--sp-2) 0;
  border-top: 1px solid var(--border-soft);
}
.sentinel-health:first-of-type {
  border-top: 0;
}
.sentinel-health__head {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  flex-wrap: wrap;
  font-size: var(--fs-small);
}
.sentinel-health__name {
  font-weight: 500;
  color: var(--fg);
  min-width: 96px;
}
.sentinel-health__rate {
  font-variant-numeric: tabular-nums;
}
.sentinel-health__rate.is-ok {
  color: var(--ok);
}
.sentinel-health__rate.is-warn {
  color: var(--warn);
}
.sentinel-health__rate.is-danger {
  color: var(--danger);
}
.sentinel-health__count {
  color: var(--fg-muted);
  margin-left: auto;
}
.sentinel-warn {
  margin-top: var(--sp-2);
  font-size: var(--fs-small);
  color: var(--warn);
  display: flex;
  align-items: center;
  gap: var(--sp-2);
}
.sentinel-table-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-3);
  padding: var(--sp-4) var(--sp-5) 0;
  flex-wrap: wrap;
}
.sentinel-table-filters {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
}
.sentinel-filter {
  width: 140px;
}
.sentinel-chips {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  padding: var(--sp-2) var(--sp-5);
  flex-wrap: wrap;
}
.sentinel-chip {
  border: 1px solid var(--border);
  background: var(--surface-2);
  color: var(--fg-2);
  border-radius: var(--r-chip);
  padding: 2px var(--sp-2);
  font-size: var(--fs-small);
  cursor: pointer;
}
.sentinel-form {
  display: grid;
  gap: var(--sp-3);
}
.sentinel-field {
  display: grid;
  gap: var(--sp-1);
  font-size: var(--fs-h3);
  color: var(--fg);
}
.sentinel-form__actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--sp-2);
}
@media (max-width: 1023px) {
  .sentinel-split {
    grid-template-columns: minmax(0, 1fr);
  }
}
@media (max-width: 640px) {
  .sentinel-filter {
    width: 100%;
  }
}
</style>