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
  type SentinelStatus,
  type SignalRow,
  type TrafficPayload,
} from '@/api/sentinel'

const HELP_TEXT =
  '实时监测上游封控信号（403 / 11140 / 4015 等）与每个平台的请求结果。' +
  '同一窗口内多个账号被风控时判定为批次级封控，可立即暂停该平台止损：' +
  '暂停期间请求直接返回 503，不消耗账号额度、也不打到上游。'

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
const stats = computed(() => toStats(status.value, traffic.value))
const ribbon = computed(() => toRibbon(traffic.value))
const health = computed(() => toPlatformHealth(traffic.value))
const liveRows = computed(() => toLiveRows(traffic.value, 50))
const signalRows = computed(() => toSignalRows(status.value, traffic.value))
const accountRows = computed(() => toAccountRows(status.value, traffic.value))
const paused = computed(() => pausedKeys(status.value))
const storage = computed(() => storageText(status.value, traffic.value))

const platformLabelOf = computed(() => {
  const map = new Map<string, string>()
  for (const row of traffic.value?.platforms || []) map.set(row.platform, row.label || row.platform)
  for (const key of paused.value) map.set(key, pausedLabel(key, traffic.value))
  return map
})

const tab = computed(() => (table.state.filters.tab === 'accounts' ? 'accounts' : 'signals'))
const platformFilter = computed(() => table.state.filters.platform)

const platformOptions = computed(() => {
  const options = [{ label: '全部平台', value: '' }]
  for (const row of traffic.value?.platforms || []) {
    options.push({ label: row.label || row.platform, value: row.platform })
  }
  for (const key of paused.value) {
    if (!options.some((option) => option.value === key)) options.push({ label: pausedLabel(key, traffic.value), value: key })
  }
  return options
})

const windowOptions = [
  { label: '5 分钟', value: '300' },
  { label: '10 分钟', value: '600' },
  { label: '30 分钟', value: '1800' },
]

const chips = computed<FilterChip[]>(() => {
  const list: FilterChip[] = []
  if (platformFilter.value) {
    list.push({ key: 'platform', label: '平台', value: platformLabelOf.value.get(platformFilter.value) || platformFilter.value })
  }
  if (table.state.filters.window && table.state.filters.window !== '300') {
    const option = windowOptions.find((item) => item.value === table.state.filters.window)
    list.push({ key: 'window', label: '窗口', value: option?.label || table.state.filters.window })
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
      title: unmapped
        ? '未收录的码：该平台这个码还没进 sentinel.PLATFORM_SIGNALS，补一行映射即可'
        : '',
    },
    String(row.code || '-') + (unmapped ? ' · 未归类' : ''),
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
        ? { key: 'resume', label: '恢复该平台' }
        : { key: 'pause', label: '暂停该平台', danger: true, disabled: !platform || platform === 'unknown' },
    ],
    max: 1,
    onSelect: (key: string) => void onRowAction(row, key),
  })
}

/* ------------------------------ 止损 ------------------------------ */
const pauseOpen = ref(false)
const pauseTarget = ref('')
const pauseSeconds = ref('600')
const pauseReason = ref('手动止损')
const pausing = ref(false)

const pauseSecondOptions = [
  { label: '10 分钟', value: '600' },
  { label: '30 分钟', value: '1800' },
  { label: '直到手动恢复', value: '0' },
]

function openPause(platform = '', seconds = 600): void {
  pauseTarget.value = platform
  pauseSeconds.value = String(seconds)
  pauseReason.value = '手动止损'
  pauseOpen.value = true
}

async function submitPause(): Promise<void> {
  const name = pauseTarget.value ? platformLabelOf.value.get(pauseTarget.value) || pauseTarget.value : '全部平台'
  const seconds = Number(pauseSeconds.value) || 0
  const ok = await confirm({
    title: '暂停 ' + name + ' 的上游请求？',
    message:
      (seconds > 0 ? '暂停 ' + Math.round(seconds / 60) + ' 分钟' : '直到手动恢复') +
      '。暂停期间请求会直接返回 503，不消耗账号额度、也不会打到上游。',
    danger: true,
    okText: '暂停',
  })
  if (!ok) return
  pausing.value = true
  try {
    await pausePlatform({ platform: pauseTarget.value, seconds, reason: pauseReason.value || '手动止损' })
    pauseOpen.value = false
    toast.success('已暂停：' + name)
    refresh()
  } catch (error) {
    toast.error(failureText('暂停平台', error))
  } finally {
    pausing.value = false
  }
}

async function onResume(platform = ''): Promise<void> {
  const name = platform ? platformLabelOf.value.get(platform) || platform : '全部平台'
  const ok = await confirm({
    title: '恢复 ' + name + ' 的上游请求？',
    message: '恢复后该平台立即重新接收请求，仍处于风控窗口的账号可能继续被上游拦截。',
    okText: '恢复',
  })
  if (!ok) return
  try {
    await resumePlatform(platform)
    toast.success('已恢复：' + name)
    refresh()
  } catch (error) {
    toast.error(failureText('恢复平台', error))
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
        { key: 'platformLabel', title: '平台', mobile: 'title', width: 140 },
        {
          key: 'accountId',
          title: '账号',
          mobile: 'subtitle',
          width: 100,
          numeric: true,
          sortable: true,
          sortOrder: sortOrderOf('account'),
          render: cellAccount,
        },
        { key: 'label', title: '最近信号', mobile: 'meta', minWidth: 220, render: cellReason },
        { key: 'severityLabel', title: '类型', mobile: 'meta', width: 110, render: cellSeverity },
        {
          key: 'count',
          title: '次数',
          mobile: 'meta',
          width: 90,
          numeric: true,
          sortable: true,
          sortOrder: sortOrderOf('count'),
          render: (row: TableRow) => fmtInt(row.count),
        },
        { key: 'firstTime', title: '首次', mobile: 'meta', width: 120 },
        {
          key: 'lastTime',
          title: '最近',
          mobile: 'meta',
          width: 130,
          sortable: true,
          sortOrder: sortOrderOf('last'),
        },
      ]
    : [
        {
          key: 'time',
          title: '时间',
          mobile: 'title',
          width: 120,
          sortable: true,
          sortOrder: sortOrderOf('time'),
        },
        { key: 'platformLabel', title: '平台', mobile: 'subtitle', width: 140 },
        {
          key: 'accountId',
          title: '账号',
          mobile: 'meta',
          width: 100,
          numeric: true,
          sortable: true,
          sortOrder: sortOrderOf('account'),
          render: cellAccount,
        },
        { key: 'code', title: '信号码', mobile: 'meta', width: 130, render: cellCode },
        { key: 'reason', title: '原因', mobile: 'meta', minWidth: 220, render: cellReason },
        { key: 'model', title: '模型', mobile: 'meta', width: 170 },
        {
          key: 'actions',
          title: '操作',
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
    <PageHeader title="风控哨兵" :help="HELP_TEXT">
      <template #actions>
        <span class="sentinel-storage">存储：{{ storage }}</span>
        <Btn variant="quiet" :icon="Pause" @click="openPause('', banner.suggestedSeconds)">暂停平台…</Btn>
        <Btn variant="quiet" :icon="RefreshCw" :loading="statusPoll.running.value && loading" @click="refresh()">刷新</Btn>
      </template>
    </PageHeader>

    <ErrorBlock
      v-if="statusError && !status"
      :title="'哨兵加载失败：' + statusError"
      retry-label="重试"
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
            暂停{{ banner.platformLabel ? ' ' + banner.platformLabel : '该平台' }}止损
          </Btn>
          <Btn
            v-for="key in paused"
            :key="key"
            variant="quiet"
            size="sm"
            :icon="Play"
            @click="onResume(key === 'all' ? '' : key)"
          >
            恢复 {{ pausedLabel(key, traffic) }}
          </Btn>
        </div>
      </section>

      <StatStrip :items="stats" />

      <div class="sentinel-split">
        <div class="sentinel-main">
          <Card title="实时流量">
            <template #extra>
              <span class="sentinel-hint">最近请求走向与结果 · 含超时 / 503 / 报错</span>
            </template>
            <RouteRibbon :source="ribbon.source" :lanes="ribbon.lanes" @select="selectPlatform" />
            <p class="sentinel-legend">
              <span><i class="dot is-ok" />成功</span>
              <span><i class="dot is-warn" />限流</span>
              <span><i class="dot is-danger" />失败 / 超时</span>
              <span class="sentinel-muted">{{ trafficPoll.paused.value ? trafficPoll.pausedMessage.value : '每 2 秒刷新' }}</span>
            </p>
          </Card>

          <Card title="平台健康">
            <template #extra>
              <span class="sentinel-hint">成功率 · 失败 / 超时 · 今日请求</span>
            </template>
            <Empty v-if="!health.length" compact description="今天还没有请求。" />
            <div v-for="row in health" :key="row.platform" class="sentinel-health">
              <div class="sentinel-health__head">
                <span class="sentinel-health__name">{{ row.label }}</span>
                <span class="sentinel-health__rate" :class="'is-' + row.tone">{{ row.rateText }}</span>
                <span class="sentinel-health__count">
                  {{ fmtInt(row.requests) }} 次<template v-if="row.failed"> · {{ fmtInt(row.failed) }} 失败</template>
                </span>
                <RowActions
                  :actions="[
                    paused.includes(row.platform) || paused.includes('all')
                      ? { key: 'resume', label: '恢复该平台' }
                      : { key: 'pause', label: '暂停该平台', danger: true },
                  ]"
                  :max="1"
                  @select="(key: string) => onRowAction({ platform: row.platform }, key)"
                />
              </div>
              <StackMeter :items="row.items" :total="row.requests" :height="8" unit=" 次" />
            </div>
            <p v-if="statusPoll.paused.value" class="sentinel-warn">
              {{ statusPoll.pausedMessage.value }}
              <Btn variant="quiet" size="sm" @click="statusPoll.resume()">恢复</Btn>
            </p>
          </Card>
        </div>

        <div class="sentinel-side">
          <Card title="实时请求">
            <template #extra>
              <span class="sentinel-hint">{{ trafficPoll.paused.value ? '已暂停' : '每 2 秒刷新' }}</span>
            </template>
            <Empty v-if="!liveRows.length" compact description="最近没有请求。" />
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
              <Btn variant="quiet" size="sm" @click="trafficPoll.resume()">恢复</Btn>
            </p>
          </Card>
        </div>
      </div>

      <Card flush>
        <div class="sentinel-table-head">
          <Segmented
            :options="[
              { label: '封控信号', value: 'signals' },
              { label: '受影响账号', value: 'accounts' },
            ]"
            :model-value="tab"
            aria-label="信号视图"
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
              重置
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
            {{ tab === 'accounts' ? '按最近一次信号排序 · ' + filteredAccounts().length + ' 个账号有信号' : '最近 ' + filteredSignals().length + ' 条（Redis 保留 15 分钟，自动过期）' }}
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
          empty-description="最近没有账号被上游拦。"
          @update:page="table.setPage"
          @update:page-size="table.setPageSize"
          @update:sorter="onSorter"
        >
          <template #empty>
            <Empty :description="tab === 'accounts' ? '最近没有账号被上游拦。' : '暂无明细。'" compact />
          </template>
        </DataTable>
      </Card>
    </template>

    <!-- 暂停平台 -->
    <n-modal v-model:show="pauseOpen" preset="card" title="暂停平台（止损）" :style="{ width: '480px' }">
      <div class="sentinel-form">
        <label class="sentinel-field">
          <span>目标平台</span>
          <n-select v-model:value="pauseTarget" :options="platformOptions" />
        </label>
        <label class="sentinel-field">
          <span>暂停时长</span>
          <n-select v-model:value="pauseSeconds" :options="pauseSecondOptions" />
        </label>
        <label class="sentinel-field">
          <span>原因</span>
          <n-input v-model:value="pauseReason" placeholder="手动止损" maxlength="200" />
        </label>
        <p class="sentinel-hint">
          暂停生效点在网关鉴权之后、选号之前：直接返回 503 + Retry-After，不消耗账号额度、不打上游。
        </p>
      </div>
      <template #footer>
        <div class="sentinel-form__actions">
          <Btn variant="default" @click="pauseOpen = false">取消</Btn>
          <Btn variant="danger" solid :loading="pausing" @click="submitPause">暂停</Btn>
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