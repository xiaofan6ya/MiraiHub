<script setup lang="ts">
/**
 * S16 使用记录 /usage（模板 A：页头 + 筛选 + 统计条 + 趋势 + 分布 BarList）
 *
 * 契约：docs/ui-v2/contracts/usage.md（功能清单就是验收标准）
 * 设计稿：docs/ui-v2/06-pages-c.md P10
 * 旧版：#panel-usage + console-app.js「使用记录」段（loadUsageStats / loadUsageFilterOptions / trendChart / donutChart）
 *
 * 页面只做摆放：筛选映射、时间窗、统计卡、BarList 项、趋势 option、/logs 深链、CSV 都在
 * api/usage.ts 的纯函数里（可单测）。
 *
 * 约定：
 *   · 筛选 / 时间范围 / 趋势粒度同步路由 query（useTableQuery）；
 *   · 平台上下文：订阅 usePlatformStore().onChange（口径仍是全网关，后端只看显式 ?platform=），卸载取消；
 *   · 四态齐全：首次骨架 / 刷新保留旧数据 / 空 / 错误；
 *   · 饼图一律 BarList（04-components §2），行点击跳 /logs 带上当前时间范围。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { NSelect, NSkeleton } from 'naive-ui'
import { Download, RefreshCw, ScrollText } from 'lucide-vue-next'
import PageHeader from '@/layout/PageHeader.vue'
import {
  BarList,
  Btn,
  Card,
  Chart,
  DateRange,
  Empty,
  ErrorBlock,
  FilterBar,
  Segmented,
  StatStrip,
  applyLineDefaults,
  type FilterChip,
  type SegmentedOption,
} from '@/components'
import { useTableQuery, useToast } from '@/composables'
import { usePlatformStore } from '@/stores/platform'
import { ApiError, isAbortError } from '@/api/http'
import { failureText } from '@/utils/error'
import { fmtCredit, fmtInt } from '@/utils/format'
import {
  USAGE_FILTER_DEFAULTS,
  getUsageOptions,
  getUsageStats,
  logsQueryFor,
  metricOf,
  normalizeGrain,
  reconcileFilters,
  resolveUsageWindow,
  saveText,
  toBarItems,
  toStatItems,
  toTrendOption,
  toUsageParams,
  usageCsv,
  usageChips,
  usageFilename,
  usageHasData,
  usageSummary,
  type UsageDistRow,
  type UsageFilters,
  type UsageLogKind,
  type UsagePayload,
  type UsageOptionsPayload,
} from '@/api/usage'

/** 旧版面板副标题原样保留（收进 ⓘ）。 */
const HELP_TEXT =
  '统计平台全部 Key 的用量：请求数、Token、积分与平均耗时。数据来自本地 usage_logs，随请求实时累计。' +
  '成功 = error_kind 为空或 success；Token 与积分只统计成功请求，请求数含失败。'

const toast = useToast()
const router = useRouter()
const platformStore = usePlatformStore()

const table = useTableQuery('usage', {
  filters: { ...USAGE_FILTER_DEFAULTS },
})

/** useTableQuery 的 filters 是 Record<string, string>：默认值已覆盖全部键，这里收窄成强类型。 */
const filters = table.state.filters as unknown as UsageFilters
const window = computed(() => resolveUsageWindow(filters))
const win = window
const grain = computed(() => normalizeGrain(filters.grain, window.value.days))

const grainOptions: Array<SegmentedOption<string>> = [
  { label: '按小时', value: 'hour' },
  { label: '按天', value: 'day' },
]

/* ------------------------------------------------------------------ *
 * 取数
 * ------------------------------------------------------------------ */

const payload = ref<UsagePayload | null>(null)
const options = ref<UsageOptionsPayload | null>(null)
const loading = ref(false)
const loadError = ref<{ message: string; detail: string } | null>(null)

let controller: AbortController | null = null
let optionsController: AbortController | null = null

const firstLoading = computed(() => loading.value && payload.value === null)

function prettyDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail
  try {
    return JSON.stringify(detail, null, 2)
  } catch {
    return String(detail)
  }
}

async function loadStats(refresh = false): Promise<void> {
  controller?.abort()
  const current = new AbortController()
  controller = current
  loading.value = true
  loadError.value = null
  try {
    const data = await getUsageStats(toUsageParams(filters, { refresh }), current.signal)
    payload.value = data
  } catch (error) {
    if (isAbortError(error)) return
    const detail = error instanceof ApiError && error.detail ? prettyDetail(error.detail) : ''
    loadError.value = { message: failureText('加载使用记录', error), detail }
    toast.error(failureText('加载使用记录', error))
  } finally {
    if (controller === current) {
      controller = null
      loading.value = false
    }
  }
}

async function loadOptions(): Promise<void> {
  optionsController?.abort()
  const current = new AbortController()
  optionsController = current
  try {
    const win = window.value
    const data = await getUsageOptions({ start: win.startIso, end: win.explicit ? win.endIso : undefined }, current.signal)
    options.value = data
    // 窗口变了以后，已选值可能不在新选项里 → 回落「全部」（旧版 keep()）。
    const next = reconcileFilters({ ...filters }, data)
    const changed =
      next.key !== filters.key ||
      next.model !== filters.model ||
      next.account !== filters.account ||
      next.endpoint !== filters.endpoint ||
      next.platform !== filters.platform
    if (changed) {
      table.setFilters({
        key: next.key,
        model: next.model,
        account: next.account,
        endpoint: next.endpoint,
        platform: next.platform,
      })
    }
  } catch (error) {
    if (isAbortError(error)) return
    // 选项拉取失败不阻塞统计（旧版同样吞掉）
    console.warn('[usage] 筛选项加载失败', error)
  } finally {
    if (optionsController === current) optionsController = null
  }
}

function reload(): void {
  void loadStats()
  void loadOptions()
}

function refresh(): void {
  void loadStats(true)
}

/* ------------------------------------------------------------------ *
 * 筛选
 * ------------------------------------------------------------------ */

function applyFilter(name: string, value: string | number | null): void {
  table.setFilter(name, value === null || value === undefined ? '' : String(value))
}

function onFilterChange(values: Record<string, unknown>): void {
  const next: Record<string, string> = {}
  for (const [name, value] of Object.entries(values)) {
    next[name] = value === null || value === undefined ? '' : String(value)
  }
  table.setFilters(next)
}

/** DateRange 与路由 query 双向绑定：空 = 默认「今日（自然日）」。 */
const rangeValue = computed<[number, number] | null>({
  get: (): [number, number] => [win.value.startMs, win.value.endMs],
  set: (value: [number, number] | null) => {
    if (!value) {
      table.setFilters({ start: '', end: '' })
      return
    }
    const [start, end] = value
    if (!Number.isFinite(start) || !Number.isFinite(end)) return
    if (start > end) {
      toast.error('开始时间不能晚于结束时间')
      return
    }
    table.setFilters({ start: new Date(start).toISOString(), end: new Date(end).toISOString() })
  },
})

function resetFilters(): void {
  table.setFilters({ ...USAGE_FILTER_DEFAULTS })
}

function removeChip(key: string): void {
  if (key === 'range') {
    table.setFilters({ start: '', end: '' })
    return
  }
  table.setFilter(key, '')
}

function setGrain(value: string | number): void {
  table.setFilter('grain', String(value))
}

const chips = computed<FilterChip[]>(() => usageChips(filters, options.value, window.value))
const summary = computed(() => usageSummary(payload.value))
const hasFilters = computed(() =>
  Boolean(
    filters.platform ||
      filters.key ||
      filters.model ||
      filters.account ||
      filters.endpoint ||
      filters.start ||
      filters.end,
  ),
)
const emptyNote = computed(() =>
  hasFilters.value
    ? '当前筛选条件下没有记录，试试放宽时间范围或清除筛选。'
    : '该时间范围内没有任何请求记录。',
)

/* ------------------------------------------------------------------ *
 * 筛选项下拉
 * ------------------------------------------------------------------ */

const platformOptions = computed(() => {
  const fromOptions = (options.value?.platforms || []).map((item) => ({
    label: item.label || item.value,
    value: item.value,
  }))
  const seen = new Set(fromOptions.map((item) => item.value))
  const fallback = platformStore.items
    .map((spec) => ({
      label: spec.realm_label ? spec.label + ' ' + spec.realm_label : spec.label,
      value: spec.storage_platform || spec.id,
    }))
    .filter((item) => item.value && !seen.has(item.value))
  return [{ label: '全部平台（全网关）', value: '' }, ...fromOptions, ...fallback]
})

const keyOptions = computed(() => [
  { label: '全部 Key', value: '' },
  ...(options.value?.keys || []).map((item) => ({ label: item.name || '#' + item.id, value: String(item.id) })),
])

const modelOptions = computed(() => [
  { label: '全部模型', value: '' },
  ...(options.value?.models || []).map((item) => ({ label: item.label || item.value, value: item.value })),
])

const accountOptions = computed(() => [
  { label: '全部账号', value: '' },
  ...(options.value?.accounts || []).map((item) => ({
    label: (item.name || '账号 ' + item.id) + ' · #' + item.id,
    value: String(item.id),
  })),
])

const endpointOptions = computed(() => [
  { label: '全部端点', value: '' },
  ...(options.value?.use_cases || []).map((item) => ({ label: item, value: item })),
])

/* ------------------------------------------------------------------ *
 * 统计条 / 趋势 / 分布
 * ------------------------------------------------------------------ */

const statItems = computed(() => toStatItems(payload.value?.overview))
const trendRows = computed(() => payload.value?.trend || [])
const trendOption = computed(() => applyLineDefaults(toTrendOption(trendRows.value, grain.value)))

const modelMetric = computed(() => metricOf(payload.value?.by_model))
const keyMetric = computed(() => metricOf(payload.value?.by_key))
const endpointMetric = computed(() => metricOf(payload.value?.by_endpoint))

const modelBars = computed(() => toBarItems(payload.value?.by_model, modelMetric.value))
const keyBars = computed(() => toBarItems(payload.value?.by_key, keyMetric.value))
const endpointBars = computed(() => toBarItems(payload.value?.by_endpoint, endpointMetric.value))
const accountBars = computed(() => toBarItems(payload.value?.by_account, 'requests'))
const resultBars = computed(() => toBarItems(payload.value?.by_result, 'requests'))

const numberFormat = (value: number): string => fmtInt(value)

function metricText(metric: 'credits' | 'requests'): string {
  return metric === 'credits' ? '按积分' : '按成功请求数'
}

function rowFormat(metric: 'credits' | 'requests'): (value: number) => string {
  return metric === 'credits' ? (value: number) => fmtCredit(value) : numberFormat
}

/* ------------------------------------------------------------------ *
 * BarList 行点击 → /logs（BarList 没有 click 事件，按 li 取标签再映射回源行）
 * ------------------------------------------------------------------ */

function rowsFor(kind: UsageLogKind): Array<UsageDistRow | UsagePayload['by_model'][number] | UsagePayload['by_account'][number] | UsagePayload['by_result'][number]> {
  const data = payload.value
  if (!data) return []
  if (kind === 'model') return data.by_model
  if (kind === 'key') return data.by_key
  if (kind === 'account') return data.by_account
  return data.by_result
}

function labelFromEvent(event: MouseEvent): string {
  const target = event.target as HTMLElement | null
  const row = target?.closest('.mh-barlist__row')
  const label = row?.querySelector('.mh-barlist__label')
  return (label?.textContent || '').trim()
}

function openLogs(kind: UsageLogKind, row?: unknown): void {
  const target = row ?? rowsFor(kind).find((item) => item.label)
  const query = logsQueryFor(kind, target as never, filters)
  if (!query) return
  void router.push({ path: '/logs', query })
}

function onBarClick(kind: UsageLogKind, event: MouseEvent): void {
  const label = labelFromEvent(event)
  if (!label) return
  const row = rowsFor(kind).find((item) => String(item.label) === label)
  if (!row) return
  openLogs(kind, row)
}

/* ------------------------------------------------------------------ *
 * 导出
 * ------------------------------------------------------------------ */

function exportCsv(): void {
  const data = payload.value
  if (!data || !usageHasData(data)) {
    toast.error('导出失败：当前筛选下没有数据')
    return
  }
  saveText(usageCsv(data, { windowLabel: window.value.label, grain: grain.value }), usageFilename())
  toast.success('已导出使用记录 CSV')
}

/* ------------------------------------------------------------------ *
 * 生命周期
 * ------------------------------------------------------------------ */

let offPlatform: (() => void) | null = null
let lastSelector = platformStore.selector

onMounted(() => {
  lastSelector = platformStore.selector
  reload()
  offPlatform = platformStore.onChange((detail) => {
    // 首屏 store 加载后也会 emit 一次：选择没变就别重复打接口。
    if (detail.selector === lastSelector) return
    lastSelector = detail.selector
    reload()
  })
})

onBeforeUnmount(() => {
  offPlatform?.()
  offPlatform = null
  controller?.abort()
  controller = null
  optionsController?.abort()
  optionsController = null
})

// 时间窗变化：统计与筛选项都要重取。
watch(
  () => filters.start + '|' + filters.end,
  () => {
    void loadStats()
    void loadOptions()
  },
)

// 其它筛选 / 粒度变化：只重取统计（选项只跟窗口走）。
watch(
  () => [filters.platform, filters.key, filters.model, filters.account, filters.endpoint, filters.grain].join('|'),
  () => {
    void loadStats()
  },
)
</script>

<template>
  <div class="usage-page">
    <PageHeader title="使用记录" :help="HELP_TEXT">
      <template #actions>
        <Btn variant="default" :icon="Download" :disabled="!payload" @click="exportCsv">导出</Btn>
        <Btn variant="quiet" :icon="RefreshCw" :loading="loading" @click="refresh">刷新</Btn>
      </template>
    </PageHeader>

    <Card>
      <FilterBar
        :chips="chips"
        :summary="summary"
        :loading="loading"
        @change="onFilterChange"
        @reset="resetFilters"
        @remove="removeChip"
      >
        <template #default>
          <n-select
            class="usage-select"
            :value="filters.platform"
            :options="platformOptions"
            aria-label="按平台筛选"
            @update:value="(value: string | null) => applyFilter('platform', value)"
          />
          <n-select
            class="usage-select"
            :value="filters.key"
            :options="keyOptions"
            aria-label="按 Key 筛选"
            @update:value="(value: string | null) => applyFilter('key', value)"
          />
          <n-select
            class="usage-select"
            :value="filters.model"
            :options="modelOptions"
            aria-label="按模型筛选"
            @update:value="(value: string | null) => applyFilter('model', value)"
          />
          <n-select
            class="usage-select"
            :value="filters.account"
            :options="accountOptions"
            aria-label="按账号筛选"
            @update:value="(value: string | null) => applyFilter('account', value)"
          />
          <n-select
            class="usage-select"
            :value="filters.endpoint"
            :options="endpointOptions"
            aria-label="按端点筛选"
            @update:value="(value: string | null) => applyFilter('endpoint', value)"
          />
          <DateRange v-model="rangeValue" :width="300" placeholder="默认今日（自然日）" />
        </template>
      </FilterBar>
    </Card>

    <ErrorBlock
      v-if="loadError && !payload"
      :title="loadError.message"
      :detail="loadError.detail"
      @retry="reload"
    />

    <div v-else-if="firstLoading" class="usage-skeleton" aria-busy="true" aria-label="使用记录加载中">
      <div class="usage-skeleton__strip"><n-skeleton text :repeat="2" /></div>
      <div class="usage-skeleton__card"><n-skeleton text :repeat="6" /></div>
      <div class="usage-skeleton__grid">
        <div v-for="index in 3" :key="index" class="usage-skeleton__card">
          <n-skeleton text :repeat="4" />
        </div>
      </div>
    </div>

    <template v-else>
      <StatStrip :items="statItems" :columns="6" />

      <Card title="Token 使用趋势" :loading="loading && !!payload">
        <template #extra>
          <Segmented
            :options="grainOptions"
            :model-value="grain"
            aria-label="趋势粒度"
            @update:model-value="setGrain"
          />
        </template>
        <Chart v-if="trendRows.length" :option="trendOption" height="240px" />
        <Empty v-else compact :description="emptyNote">
          <Btn v-if="hasFilters" variant="default" size="sm" @click="resetFilters">清除筛选</Btn>
          <Btn v-else variant="quiet" size="sm" :icon="RefreshCw" @click="refresh">刷新</Btn>
        </Empty>
      </Card>

      <div class="usage-grid usage-grid--3">
        <Card title="模型分布">
          <template #extra>
            <span class="usage-metric">{{ metricText(modelMetric) }}</span>
            <Btn
              variant="quiet"
              size="sm"
              :icon="ScrollText"
              :disabled="!modelBars.length"
              @click="openLogs('model')"
            >
              日志
            </Btn>
          </template>
          <div
            v-if="modelBars.length"
            class="usage-bars"
            role="group"
            aria-label="模型分布，点击行查看该模型日志"
            @click="onBarClick('model', $event)"
          >
            <BarList :items="modelBars" :format="rowFormat(modelMetric)" />
          </div>
          <Empty v-else compact :description="emptyNote" />
        </Card>

        <Card title="Key 使用分布">
          <template #extra>
            <span class="usage-metric">{{ metricText(keyMetric) }}</span>
            <Btn
              variant="quiet"
              size="sm"
              :icon="ScrollText"
              :disabled="!keyBars.length"
              @click="openLogs('key')"
            >
              日志
            </Btn>
          </template>
          <div
            v-if="keyBars.length"
            class="usage-bars"
            role="group"
            aria-label="Key 使用分布，点击行查看该 Key 日志"
            @click="onBarClick('key', $event)"
          >
            <BarList :items="keyBars" :format="rowFormat(keyMetric)" />
          </div>
          <Empty v-else compact :description="emptyNote" />
        </Card>

        <Card title="端点分布">
          <template #extra>
            <span class="usage-metric">{{ metricText(endpointMetric) }}</span>
            <span class="usage-hint">端点维度暂不支持跳日志</span>
          </template>
          <div v-if="endpointBars.length" class="usage-bars">
            <BarList :items="endpointBars" :format="rowFormat(endpointMetric)" />
          </div>
          <Empty v-else compact :description="emptyNote" />
        </Card>
      </div>

      <div class="usage-grid usage-grid--2">
        <Card title="账号分布">
          <template #extra>
            <span class="usage-metric">按成功请求数</span>
            <Btn
              variant="quiet"
              size="sm"
              :icon="ScrollText"
              :disabled="!accountBars.length"
              @click="openLogs('account')"
            >
              日志
            </Btn>
          </template>
          <div
            v-if="accountBars.length"
            class="usage-bars"
            role="group"
            aria-label="账号分布，点击行查看该账号日志"
            @click="onBarClick('account', $event)"
          >
            <BarList :items="accountBars" :format="numberFormat" />
          </div>
          <Empty v-else compact :description="emptyNote" />
        </Card>

        <Card title="请求结果分布">
          <template #extra>
            <span class="usage-metric">全部请求</span>
            <Btn
              variant="quiet"
              size="sm"
              :icon="ScrollText"
              :disabled="!resultBars.length"
              @click="openLogs('result')"
            >
              日志
            </Btn>
          </template>
          <div
            v-if="resultBars.length"
            class="usage-bars"
            role="group"
            aria-label="请求结果分布，点击行查看该类日志"
            @click="onBarClick('result', $event)"
          >
            <BarList :items="resultBars" :format="numberFormat" />
          </div>
          <Empty v-else compact :description="emptyNote" />
        </Card>
      </div>

      <p v-if="payload" class="usage-footnote">
        口径：成功 = error_kind 为空或 success；Token 与积分只统计成功请求，请求数含失败。
        非「成功」的结果行统一跳 result=error（/api/logs 只有成功/错误两档）。
      </p>
    </template>
  </div>
</template>

<style scoped>
.usage-page {
  display: grid;
  gap: var(--sp-4);
  min-width: 0;
}

.usage-select {
  width: 160px;
  max-width: 100%;
}

.usage-grid {
  display: grid;
  gap: var(--sp-4);
  min-width: 0;
}

.usage-grid--3 {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.usage-grid--2 {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.usage-metric {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  white-space: nowrap;
}

.usage-hint {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-faint);
  white-space: nowrap;
}

/* 只让有跳转的 BarList 行看起来可点（端点卡不加这个类）。 */
.usage-bars :deep(.mh-barlist__row) {
  cursor: pointer;
  border-radius: var(--r-chip);
  transition: background var(--t-fast) var(--ease);
}

.usage-bars :deep(.mh-barlist__row:hover) {
  background: var(--surface-hover);
}

.usage-footnote {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  max-width: 68ch;
}

.usage-skeleton {
  display: grid;
  gap: var(--sp-4);
}

.usage-skeleton__strip,
.usage-skeleton__card {
  padding: var(--sp-5);
  border: 1px solid var(--border);
  border-radius: var(--r-card);
  background: var(--surface);
}

.usage-skeleton__grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--sp-4);
}

@media (max-width: 1279px) {
  .usage-grid--3 {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 1023px) {
  .usage-skeleton__grid {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (max-width: 640px) {
  .usage-grid--3,
  .usage-grid--2 {
    grid-template-columns: minmax(0, 1fr);
  }

  .usage-select {
    width: 100%;
  }
}
</style>
