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
 *   · 口径：**全网关**（后端只看显式 ?platform=，不读 X-Platform）—— 页头徽章写明「全网关」或所选平台；
 *     因此不再订阅顶栏平台切换（platform_scoping_review §3：切了数字也不变，只是白打接口）；
 *   · 四态齐全：首次骨架 / 刷新保留旧数据 / 空 / 错误；
 *   · 饼图一律 BarList（04-components §2），行点击跳 /logs 带上当前时间范围。
 *
 * v2.1（10-visual-refresh）：统计条换成 KpiCard 行（带迷你折线）；趋势图输入 / 输出不再堆叠，
 * 量级差大时分双轴；横轴本地时区；端点卡用禁用的「日志」按钮 + 说明，不再用一段灰字。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { NSelect } from 'naive-ui'
import { Download, RefreshCw, ScrollText } from 'lucide-vue-next'
import PageHeader from '@/layout/PageHeader.vue'
import {
  Badge,
  BarList,
  Btn,
  Card,
  Chart,
  DateRange,
  Empty,
  ErrorBlock,
  FilterBar,
  KpiCard,
  Segmented,
  applyLineDefaults,
  type FilterChip,
  type SegmentedOption,
} from '@/components'
import { useTableQuery, useToast } from '@/composables'
import { ApiError, isAbortError } from '@/api/http'
import { errorMessage } from '@/utils/error'
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
  toTrendOption,
  toUsageParams,
  toUsageKpis,
  usageCsv,
  usageChips,
  usageFilename,
  usageHasData,
  usageScopeLabel,
  usageSummary,
  type UsageDistRow,
  type UsageFilters,
  type UsageLogKind,
  type UsagePayload,
  type UsageOptionsPayload,
} from '@/api/usage'
import { groupTitle, useI18n } from '@/i18n'
import { groupOf } from '@/router/pages'
import { usePlatformStore } from '@/stores/platform'
import { useRevisit } from '@/pages/_shared/useRevisit'

const { t } = useI18n()
/** 旧版面板副标题原样保留（收进 ⓘ）；语种跟随 useI18n。 */
const helpText = computed(() => t('page.usage.help'))
const eyebrow = computed(() => groupTitle(groupOf('usage').key))

const toast = useToast()
const router = useRouter()
const platformStore = usePlatformStore()

const table = useTableQuery('usage', {
  filters: { ...USAGE_FILTER_DEFAULTS },
})

/** useTableQuery 的 filters 是 Record<string, string>：默认值已覆盖全部键，这里收窄成强类型。 */
const filters = table.state.filters as unknown as UsageFilters
const window = computed(() => resolveUsageWindow(filters, Date.now(), t))
const win = window
const grain = computed(() => normalizeGrain(filters.grain, window.value.days))

const grainOptions = computed<Array<SegmentedOption<string>>>(() => [
  { label: t('page.usage.grain.hour'), value: 'hour' },
  { label: t('page.usage.grain.day'), value: 'day' },
])

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
    const failure = t('page.usage.failure', {
      action: t('page.usage.action.load'),
      reason: errorMessage(error),
    })
    loadError.value = { message: failure, detail }
    toast.error(failure)
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
    console.warn('[usage] failed to load filter options', error)
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
      toast.error(t('page.usage.dateOrder'))
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

const chips = computed<FilterChip[]>(() =>
  usageChips(filters, options.value, window.value, t),
)
const summary = computed(() => usageSummary(payload.value, t))
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
  hasFilters.value ? t('page.usage.empty.filtered') : t('page.usage.empty.none'),
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
  return [{ label: t('page.usage.option.allPlatforms'), value: '' }, ...fromOptions, ...fallback]
})

const keyOptions = computed(() => [
  { label: t('page.usage.option.allKeys'), value: '' },
  ...(options.value?.keys || []).map((item) => ({
    label: item.name || '#' + item.id,
    value: String(item.id),
  })),
])

const modelOptions = computed(() => [
  { label: t('page.usage.option.allModels'), value: '' },
  ...(options.value?.models || []).map((item) => ({
    label: item.label || item.value,
    value: item.value,
  })),
])

const accountOptions = computed(() => [
  { label: t('page.usage.option.allAccounts'), value: '' },
  ...(options.value?.accounts || []).map((item) => ({
    label: (item.name || t('page.usage.option.account', { id: item.id })) + ' · #' + item.id,
    value: String(item.id),
  })),
])

const endpointOptions = computed(() => [
  { label: t('page.usage.option.allEndpoints'), value: '' },
  ...(options.value?.use_cases || []).map((item) => ({ label: item, value: item })),
])

/* ------------------------------------------------------------------ *
 * 统计条 / 趋势 / 分布
 * ------------------------------------------------------------------ */

const kpis = computed(() => toUsageKpis(payload.value?.overview, payload.value?.trend, t))
const scopeLabel = computed(() => usageScopeLabel(filters, options.value, t))
const trendRows = computed(() => payload.value?.trend || [])
const trendOption = computed(() =>
  applyLineDefaults(toTrendOption(trendRows.value, grain.value, t)),
)

const modelMetric = computed(() => metricOf(payload.value?.by_model))
const keyMetric = computed(() => metricOf(payload.value?.by_key))
const endpointMetric = computed(() => metricOf(payload.value?.by_endpoint))

const modelBars = computed(() => toBarItems(payload.value?.by_model, modelMetric.value, t))
const keyBars = computed(() => toBarItems(payload.value?.by_key, keyMetric.value, t))
const endpointBars = computed(() =>
  toBarItems(payload.value?.by_endpoint, endpointMetric.value, t),
)
const accountBars = computed(() => toBarItems(payload.value?.by_account, 'requests', t))
const resultBars = computed(() => toBarItems(payload.value?.by_result, 'requests', t))

const numberFormat = (value: number): string => fmtInt(value)

function metricText(metric: 'credits' | 'requests'): string {
  return metric === 'credits'
    ? t('page.usage.metric.credits')
    : t('page.usage.metric.requests')
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
    toast.error(t('page.usage.exportEmpty'))
    return
  }
  saveText(
    usageCsv(data, { windowLabel: window.value.label, grain: grain.value }, t),
    usageFilename(),
  )
  toast.success(t('page.usage.exportDone'))
}

/* ------------------------------------------------------------------ *
 * 生命周期
 * ------------------------------------------------------------------ */

onMounted(() => {
  reload()
})

onBeforeUnmount(() => {
  controller?.abort()
  controller = null
  optionsController?.abort()
  optionsController = null
})

// 回到 KeepAlive 缓存页：只静默重取统计（筛选项跟时间窗走，不变），不出骨架（11 §2）。
useRevisit(() => loadStats())

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
    <PageHeader :help="helpText" :eyebrow="eyebrow">
      <template #meta>
        <Badge tone="neutral">{{ scopeLabel }}</Badge>
      </template>
      <template #actions>
        <Btn variant="default" :icon="Download" :disabled="!payload" @click="exportCsv">
          {{ t('page.usage.export') }}
        </Btn>
        <Btn variant="quiet" :icon="RefreshCw" :loading="loading" @click="refresh">
          {{ t('page.usage.refresh') }}
        </Btn>
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
            :aria-label="t('page.usage.filter.platformAria')"
            @update:value="(value: string | null) => applyFilter('platform', value)"
          />
          <n-select
            class="usage-select"
            :value="filters.key"
            :options="keyOptions"
            :aria-label="t('page.usage.filter.keyAria')"
            @update:value="(value: string | null) => applyFilter('key', value)"
          />
          <n-select
            class="usage-select"
            :value="filters.model"
            :options="modelOptions"
            :aria-label="t('page.usage.filter.modelAria')"
            @update:value="(value: string | null) => applyFilter('model', value)"
          />
          <n-select
            class="usage-select"
            :value="filters.account"
            :options="accountOptions"
            :aria-label="t('page.usage.filter.accountAria')"
            @update:value="(value: string | null) => applyFilter('account', value)"
          />
          <n-select
            class="usage-select"
            :value="filters.endpoint"
            :options="endpointOptions"
            :aria-label="t('page.usage.filter.endpointAria')"
            @update:value="(value: string | null) => applyFilter('endpoint', value)"
          />
          <DateRange
            v-model="rangeValue"
            :width="300"
            :placeholder="t('page.usage.filter.datePlaceholder')"
          />
        </template>
      </FilterBar>
    </Card>

    <ErrorBlock
      v-if="loadError && !payload"
      :title="loadError.message"
      :detail="loadError.detail"
      @retry="reload"
    />

    <div
      v-else-if="firstLoading"
      class="usage-skeleton"
      aria-busy="true"
      :aria-label="t('page.usage.loadingAria')"
    >
      <!-- 与真实布局同构：KpiCard 自带骨架、趋势卡同标题同高、3 栏分布卡（11 §4-1）。 -->
      <section class="usage-kpis">
        <KpiCard v-for="item in kpis" :key="item.key" :label="item.label" :value="null" loading />
      </section>
      <Card class="usage-trend" :title="t('page.usage.card.trend')">
        <div class="mh-skeleton usage-skeleton__chart" />
      </Card>
      <div class="usage-grid usage-grid--3">
        <div v-for="index in 3" :key="index" class="mh-skeleton usage-skeleton__card" />
      </div>
    </div>

    <template v-else>
      <section class="usage-kpis" :aria-label="t('page.usage.kpiAria')">
        <KpiCard
          v-for="item in kpis"
          :key="item.key"
          :label="item.label"
          :value="item.value"
          split-unit
          :hint="item.hint"
          :tone="item.tone"
          :spark="item.spark"
          :loading="loading && !!payload"
        />
      </section>

      <Card class="usage-trend" :title="t('page.usage.card.trend')" :loading="loading && !!payload">
        <template #extra>
          <span class="usage-metric">{{ t('page.usage.trendScope', { range: win.label }) }}</span>
          <Segmented
            :options="grainOptions"
            :model-value="grain"
            :aria-label="t('page.usage.grainAria')"
            @update:model-value="setGrain"
          />
        </template>
        <Chart v-if="trendRows.length" :option="trendOption" height="260px" />
        <Empty v-else compact :description="emptyNote">
          <Btn v-if="hasFilters" variant="default" size="sm" @click="resetFilters">
            {{ t('page.usage.clearFilters') }}
          </Btn>
          <Btn v-else variant="quiet" size="sm" :icon="RefreshCw" @click="refresh">
            {{ t('page.usage.refresh') }}
          </Btn>
        </Empty>
      </Card>

      <div class="usage-grid usage-grid--3">
        <Card :title="t('page.usage.card.models')">
          <template #extra>
            <span class="usage-metric">{{ metricText(modelMetric) }}</span>
            <Btn
              variant="quiet"
              size="sm"
              :icon="ScrollText"
              :disabled="!modelBars.length"
              @click="openLogs('model')"
            >
              {{ t('page.usage.action.logs') }}
            </Btn>
          </template>
          <div
            v-if="modelBars.length"
            class="usage-bars"
            role="group"
            :aria-label="t('page.usage.group.modelsAria')"
            @click="onBarClick('model', $event)"
          >
            <BarList :items="modelBars" :format="rowFormat(modelMetric)" />
          </div>
          <Empty v-else compact :description="emptyNote" />
        </Card>

        <Card :title="t('page.usage.card.keys')">
          <template #extra>
            <span class="usage-metric">{{ metricText(keyMetric) }}</span>
            <Btn
              variant="quiet"
              size="sm"
              :icon="ScrollText"
              :disabled="!keyBars.length"
              @click="openLogs('key')"
            >
              {{ t('page.usage.action.logs') }}
            </Btn>
          </template>
          <div
            v-if="keyBars.length"
            class="usage-bars"
            role="group"
            :aria-label="t('page.usage.group.keysAria')"
            @click="onBarClick('key', $event)"
          >
            <BarList :items="keyBars" :format="rowFormat(keyMetric)" />
          </div>
          <Empty v-else compact :description="emptyNote" />
        </Card>

        <Card :title="t('page.usage.card.endpoints')">
          <template #extra>
            <span class="usage-metric">{{ metricText(endpointMetric) }}</span>
            <!-- 第 2 轮审查 P2：与其他卡一致放「日志」按钮，禁用并在悬停说明原因 -->
            <span class="usage-disabled-tip" :title="t('page.usage.card.endpointHint')">
              <Btn
                variant="quiet"
                size="sm"
                :icon="ScrollText"
                disabled
                :aria-label="t('page.usage.card.endpointHint')"
              >
                {{ t('page.usage.action.logs') }}
              </Btn>
            </span>
          </template>
          <div v-if="endpointBars.length" class="usage-bars">
            <BarList :items="endpointBars" :format="rowFormat(endpointMetric)" />
          </div>
          <Empty v-else compact :description="emptyNote" />
        </Card>
      </div>

      <div class="usage-grid usage-grid--2">
        <Card :title="t('page.usage.card.accounts')">
          <template #extra>
            <span class="usage-metric">{{ t('page.usage.metric.requests') }}</span>
            <Btn
              variant="quiet"
              size="sm"
              :icon="ScrollText"
              :disabled="!accountBars.length"
              @click="openLogs('account')"
            >
              {{ t('page.usage.action.logs') }}
            </Btn>
          </template>
          <div
            v-if="accountBars.length"
            class="usage-bars"
            role="group"
            :aria-label="t('page.usage.group.accountsAria')"
            @click="onBarClick('account', $event)"
          >
            <BarList :items="accountBars" :format="numberFormat" />
          </div>
          <Empty v-else compact :description="emptyNote" />
        </Card>

        <Card :title="t('page.usage.card.results')">
          <template #extra>
            <span class="usage-metric">{{ t('page.usage.metric.allRequests') }}</span>
            <Btn
              variant="quiet"
              size="sm"
              :icon="ScrollText"
              :disabled="!resultBars.length"
              @click="openLogs('result')"
            >
              {{ t('page.usage.action.logs') }}
            </Btn>
          </template>
          <div
            v-if="resultBars.length"
            class="usage-bars"
            role="group"
            :aria-label="t('page.usage.group.resultsAria')"
            @click="onBarClick('result', $event)"
          >
            <BarList :items="resultBars" :format="numberFormat" />
          </div>
          <Empty v-else compact :description="emptyNote" />
        </Card>
      </div>

      <p v-if="payload" class="usage-footnote">{{ t('page.usage.footnote') }}</p>
    </template>
  </div>
</template>

<style scoped>
.usage-page {
  display: grid;
  gap: var(--sp-4);
  min-width: 0;
}

/* KPI 行：≥1280 一行 6 个，往下自动 3 / 2 / 1 个；卡片按内容等高（10-visual-refresh §1.4）。 */
.usage-kpis {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(176px, 1fr));
  gap: var(--sp-4);
  align-items: start;
  min-width: 0;
}

/* 趋势是本页唯一的大图：大卡圆角。 */
.usage-trend {
  border-radius: var(--r-lg);
}

.usage-disabled-tip {
  display: inline-flex;
  cursor: not-allowed;
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
  /* 子卡按内容高度排，不互相撑高留白（第 1 轮审查 P1-3） */
  align-items: start;
}

.usage-grid--2 {
  grid-template-columns: repeat(2, minmax(0, 1fr));
  /* 子卡按内容高度排，不互相撑高留白（第 1 轮审查 P1-3） */
  align-items: start;
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
  margin: 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  max-width: 80ch;
  text-wrap: pretty;
}

.usage-skeleton {
  display: grid;
  gap: var(--sp-4);
}

/* 与 <Chart height="260px"> 同高 */
.usage-skeleton__chart {
  height: 260px;
  border-radius: var(--r-ctl);
}

.usage-skeleton__card {
  min-height: 280px;
  border-radius: var(--r-card);
}

@media (max-width: 1279px) {
  .usage-grid--3 {
    grid-template-columns: repeat(2, minmax(0, 1fr));
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