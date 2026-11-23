<script setup lang="ts">
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
import { NInput, NModal, NSelect, NSkeleton } from 'naive-ui'
import { Pause, Play, RefreshCw, ShieldAlert } from 'lucide-vue-next'
import PageHeader from '@/layout/PageHeader.vue'
import {
  Badge,
  Btn,
  Card,
  DataTable,
  Empty,
  ErrorBlock,
  RouteRibbon,
  RowActions,
  Segmented,
  StackMeter,
  StatStrip,
  type FilterChip,
  type SorterPayload,
  type TableColumn,
  type TableRow,
} from '@/components'
import { useConfirm, usePolling, useTableQuery, useToast } from '@/composables'
import { errorMessage, failureText } from '@/utils/error'
import { FMT_EMPTY, fmtInt } from '@/utils/format'
import { useI18n } from '@/i18n'
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
  toSignalRows,
  toStats,
  type AccountSignalRow,
  type SentinelStat,
  type SentinelStatus,
  type SignalRow,
  type TrafficPayload,
} from '@/api/sentinel'

const { t } = useI18n()
const helpText = computed(() => t('page.sentinel.help'))

const toast = useToast()
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

function refresh(): void {
  void statusPoll.trigger()
  void trafficPoll.trigger()
}

/* ------------------------------ 视图模型 ------------------------------ */
const banner = computed(() => toBanner(status.value, traffic.value))

/**
 * traffic 的口径是 window=3600（contracts/sentinel §2.1 的 /api/sentinel/traffic），
 * 滚动 1 小时，不是自然日 —— 统计格不能沿用「今日」，否则与使用记录的
 * 「今日（自然日）」对不上。覆盖放在页面这层：toStats 仍是接口字段的直译。
 */
const STAT_LABEL_OVERRIDES: Record<number, Partial<SentinelStat>> = {
  // 第 0 格原本叫「今日请求」；按序号覆盖，不依赖 api 回传的中文 label（英文态下 label 会变）。
  0: {
    label: t('page.sentinel.stat.requests1h'),
    help: t('page.sentinel.stat.requests1hHelp'),
  },
}

const stats = computed(() =>
  toStats(status.value, traffic.value, t).map((item, index) => {
    const patch = STAT_LABEL_OVERRIDES[index]
    return patch ? { ...item, ...patch } : item
  }),
)
const ribbon = computed(() => toRibbon(traffic.value, t))
const health = computed(() => toPlatformHealth(traffic.value, t))
const liveRows = computed(() => toLiveRows(traffic.value, 50, t))
const signalRows = computed(() => toSignalRows(status.value, traffic.value, t))
const accountRows = computed(() => toAccountRows(status.value, traffic.value, t))
const paused = computed(() => pausedKeys(status.value))
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
  return h(
    Badge,
    { tone, title: String(row.signalLabel || '') },
    { default: () => String(row.severityLabel || '') },
  )
}

function cellCode(row: TableRow): VNodeChild {
  const unmapped = Boolean(row.unmapped)
  return h(
    'span',
    {
      class: 'mono',
      title: unmapped ? t('page.sentinel.unmappedTip') : '',
    },
    String(row.code || '-') + (unmapped ? t('page.sentinel.unmappedTag') : ''),
  )
}

function cellReason(row: TableRow): VNodeChild {
  const label = String(row.label || '')
  const signal = String(row.signalLabel || '')
  const detail = String(row.detail || '')
  return h('span', { class: 'sentinel-reason', title: detail }, signal ? signal + ' · ' + label : label || FMT_EMPTY)
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
    await resumePlatform(platform)
    toast.success(t('page.sentinel.toast.resumed', { name }))
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
    <PageHeader :title="t('page.sentinel.title')" :help="helpText">
      <template #actions>
        <span class="sentinel-storage">{{ t('page.sentinel.storage', { backend: storage }) }}</span>
        <Btn variant="quiet" :icon="Pause" @click="openPause('', banner.suggestedSeconds)">
          {{ t('page.sentinel.pauseOpen') }}
        </Btn>
        <Btn variant="quiet" :icon="RefreshCw" :loading="statusPoll.running.value && loading" @click="refresh()">
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
    <div v-else-if="loading && !status" class="sentinel-skeleton" aria-busy="true">
      <n-skeleton height="64px" />
      <n-skeleton height="96px" />
      <n-skeleton :repeat="3" height="120px" />
    </div>

    <template v-else>
      <!-- 结论横幅 -->
      <section class="sentinel-banner" :class="'is-' + banner.tone" role="status">
        <ShieldAlert :size="20" class="sentinel-banner__icon" />
        <div class="sentinel-banner__body">
          <strong>{{ banner.title }}</strong>
          <span>{{ banner.detail }}</span>
        </div>
        <div class="sentinel-banner__actions">
          <Btn
            v-if="banner.tripped"
            variant="danger"
            solid
            size="sm"
            @click="openPause(banner.platform, banner.suggestedSeconds)"
          >
            {{
              banner.platformLabel
                ? t('page.sentinel.pauseTrippedLabel', { platform: banner.platformLabel })
                : t('page.sentinel.pauseTrippedThis')
            }}
          </Btn>
          <Btn
            v-for="key in paused"
            :key="key"
            variant="quiet"
            size="sm"
            :icon="Play"
            @click="onResume(key === 'all' ? '' : key)"
          >
            {{ t('page.sentinel.resumeBtn', { name: pausedLabel(key, traffic, t) }) }}
          </Btn>
        </div>
      </section>

      <StatStrip :items="stats" />

      <div class="sentinel-split">
        <div class="sentinel-main">
          <Card :title="t('page.sentinel.realtime.title')">
            <template #extra>
              <span class="sentinel-hint">{{ t('page.sentinel.realtime.hint') }}</span>
            </template>
            <RouteRibbon :source="ribbon.source" :lanes="ribbon.lanes" @select="selectPlatform" />
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
          <Card :title="t('page.sentinel.live.title')">
            <template #extra>
              <span class="sentinel-hint">
                {{ trafficPoll.paused.value ? t('page.sentinel.live.paused') : t('page.sentinel.refreshEvery') }}
              </span>
            </template>
            <Empty v-if="!liveRows.length" compact :description="t('page.sentinel.live.empty')" />
            <ol v-else class="sentinel-live" aria-live="polite">
              <li v-for="row in liveRows" :key="row.rid" class="sentinel-live__row" :class="'is-' + row.tone">
                <span class="sentinel-live__time tnum">{{ row.time }}</span>
                <span class="sentinel-live__model mono" :title="row.error">{{ row.model }}</span>
                <span class="sentinel-live__plat">{{ row.platformLabel }}</span>
                <span class="sentinel-live__status tnum">{{ row.statusText }}</span>
              </li>
            </ol>
            <p v-if="trafficPoll.paused.value" class="sentinel-warn">
              {{ trafficPoll.pausedMessage.value }}
              <Btn variant="quiet" size="sm" @click="trafficPoll.resume()">{{ t('page.sentinel.resume') }}</Btn>
            </p>
          </Card>
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
      v-model:show="pauseOpen"
      preset="card"
      :title="t('page.sentinel.pauseModalTitle')"
      :style="{ width: '480px' }"
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
  gap: var(--sp-3);
}
.sentinel-banner {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  padding: var(--sp-4) var(--sp-5);
  border-radius: var(--r-card);
  border: 1px solid var(--border);
  flex-wrap: wrap;
}
.sentinel-banner.is-ok {
  background: var(--ok-soft);
  border-color: var(--ok-soft);
}
.sentinel-banner.is-danger {
  background: var(--danger-soft);
  border-color: var(--danger-soft);
}
.sentinel-banner__icon {
  color: var(--fg-muted);
  flex: none;
}
.sentinel-banner.is-ok .sentinel-banner__icon {
  color: var(--ok);
}
.sentinel-banner.is-danger .sentinel-banner__icon {
  color: var(--danger);
}
.sentinel-banner__body {
  display: grid;
  gap: 2px;
  min-width: 0;
  flex: 1;
}
.sentinel-banner__body strong {
  font-size: var(--fs-body);
  color: var(--fg);
}
.sentinel-banner__body span {
  font-size: var(--fs-small);
  color: var(--fg-muted);
}
.sentinel-banner__actions {
  display: flex;
  gap: var(--sp-2);
  flex-wrap: wrap;
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
.sentinel-live {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 2px;
  max-height: 420px;
  overflow: auto;
}
.sentinel-live__row {
  display: grid;
  grid-template-columns: 64px minmax(0, 1fr) 84px 56px;
  gap: var(--sp-2);
  align-items: center;
  padding: 4px 0;
  font-size: var(--fs-small);
  border-bottom: 1px solid var(--border-soft);
  animation: sentinel-row-in 150ms var(--ease);
}
.sentinel-live__time {
  color: var(--fg-muted);
}
.sentinel-live__model {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--fg-2);
}
.sentinel-live__plat {
  color: var(--fg-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sentinel-live__status {
  text-align: right;
}
.sentinel-live__row.is-ok .sentinel-live__status {
  color: var(--ok);
}
.sentinel-live__row.is-warn .sentinel-live__status {
  color: var(--warn);
}
.sentinel-live__row.is-danger .sentinel-live__status {
  color: var(--danger);
}
.sentinel-live__row.is-brand .sentinel-live__status {
  color: var(--brand);
}
@keyframes sentinel-row-in {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
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
  font-size: 13px;
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
  .sentinel-live__row {
    grid-template-columns: 64px minmax(0, 1fr) 56px;
  }
  .sentinel-live__plat {
    display: none;
  }
}
</style>