<script setup lang="ts">
/**
 * S17 数据看板 /stats（模板 A：页头 + StatStrip + 主图 + 次级卡片栅格）
 *
 * 契约：docs/ui-v2/contracts/stats.md（末尾「## 实现对照」）
 * 设计稿：docs/ui-v2/06-pages-a.md P3
 *
 * 数据来源：
 *   · /api/stats/dashboard —— KPI、请求趋势（按模型）、平台排行、模型排行、失败分类；
 *   · /api/stats/usage     —— 只补看板接口没有的两块：Token 构成（输入/输出/缓存）与 Key 排行。
 *
 * 后端没有「按桶的成功/失败拆分」，成功/失败只在 summary 里 —— 折线按 dashboard.js 的
 * 实际口径画「全部请求 + 各模型请求数」，成功/失败放在 StatStrip 脚注与卡片副标题。
 * 所有换算（分桶标签、图 option、排行条、成功率语气）都在 api/stats.ts 的纯函数里。
 *
 * 四态：首次加载骨架 / 刷新保留旧数据（卡片右上角转圈）/ 空（图表内 Empty）/ 错误（ErrorBlock + 重试）。
 * 依赖平台上下文：订阅 usePlatformStore().onChange，平台变化时重新取数，卸载取消。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { NSkeleton } from 'naive-ui'
import { RefreshCw } from 'lucide-vue-next'
import PageHeader from '@/layout/PageHeader.vue'
import {
  BarList,
  Btn,
  Card,
  Chart,
  Empty,
  ErrorBlock,
  Segmented,
  StatStrip,
  applyBarDefaults,
  applyLineDefaults,
  type SegmentedOption,
} from '@/components'
import { useTableQuery, useToast } from '@/composables'
import { usePlatformStore } from '@/stores/platform'
import { fmtInt, fmtTime } from '@/utils/format'
import { failureText } from '@/utils/error'
import { ApiError, isAbortError } from '@/api/http'
import {
  STATS_DAYS,
  STATS_DAYS_LABEL,
  STATS_GRAIN_LABEL,
  buildRequestTrend,
  buildTokenTrend,
  cacheFootnote,
  getStatsDashboard,
  getStatsUsage,
  keyBarItems,
  modelBarItems,
  normalizeDays,
  normalizeGranularity,
  platformBarItems,
  requestTrendOption,
  toSummaryItems,
  tokenTrendOption,
  trendFootnote,
  type StatsDashboardPayload,
  type StatsGranularity,
  type StatsUsagePayload,
} from '@/api/stats'

/** 旧版面板副标题（原样保留，收进 ⓘ）。 */
const HELP_TEXT =
  '模型调用分析：窗口内的请求、积分、Token、成功率、延迟与吞吐。' +
  '数据来自本地 usage_logs，聚合在 SQL 侧完成；成功率 = 成功请求 / 全部请求，' +
  '积分与 Token 只统计成功请求，平均耗时也只取成功样本。'

const toast = useToast()
const platformStore = usePlatformStore()

const table = useTableQuery('stats', {
  pageSize: 20,
  filters: { days: '1', grain: 'hour' },
})

const days = computed(() => normalizeDays(table.state.filters.days))
const granularity = computed(() => normalizeGranularity(table.state.filters.grain, days.value))

const dayOptions: Array<SegmentedOption<string>> = STATS_DAYS.map((value) => ({
  label: STATS_DAYS_LABEL[value],
  value: String(value),
}))

const grainOptions: Array<SegmentedOption<string>> = [
  { label: STATS_GRAIN_LABEL.hour, value: 'hour' },
  { label: STATS_GRAIN_LABEL.day, value: 'day' },
]

function setDays(value: string | number): void {
  const next = normalizeDays(value)
  if (next > 7 && table.state.filters.grain !== 'day') {
    // 与旧版 setDays 一致：超过 7 天自动降级为按天。
    table.setFilters({ days: String(next), grain: 'day' })
    return
  }
  table.setFilter('days', String(next))
}

function setGranularity(value: string | number): void {
  const next: StatsGranularity = value === 'day' ? 'day' : 'hour'
  if (next === 'hour' && days.value > 7) {
    toast.warning('超过 7 天只支持按天聚合')
    return
  }
  table.setFilter('grain', next)
}

/* ------------------------------------------------------------------ *
 * 取数
 * ------------------------------------------------------------------ */

const dashboard = ref<StatsDashboardPayload | null>(null)
const usage = ref<StatsUsagePayload | null>(null)
const usageError = ref<{ message: string; detail: string } | null>(null)
const loading = ref(false)
const loadError = ref<{ message: string; detail: string } | null>(null)
const loadedAt = ref<Date | null>(null)

let controller: AbortController | null = null

const firstLoading = computed(() => loading.value && dashboard.value === null)

function prettyDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail
  try {
    return JSON.stringify(detail, null, 2)
  } catch {
    return String(detail)
  }
}

function errorOf(action: string, error: unknown): { message: string; detail: string } {
  const detail = error instanceof ApiError && error.detail ? prettyDetail(error.detail) : ''
  return { message: failureText(action, error), detail }
}

async function load(): Promise<void> {
  controller?.abort()
  const current = new AbortController()
  controller = current
  loading.value = true
  loadError.value = null
  try {
    const query = { days: days.value, granularity: granularity.value }
    const [main, extra] = await Promise.all([
      getStatsDashboard(query, current.signal),
      // 补充接口失败不拖垮整页（Token / Key 两张卡单独报错 + 重试）。
      getStatsUsage(query, current.signal).catch((error: unknown) => {
        if (isAbortError(error)) return null
        usageError.value = errorOf('加载 Token / Key 明细', error)
        return null
      }),
    ])
    if (controller !== current) return
    dashboard.value = main
    usage.value = extra
    if (extra) usageError.value = null
    loadedAt.value = new Date()
  } catch (error) {
    if (isAbortError(error)) return
    loadError.value = errorOf('加载数据看板', error)
    // 已有数据时刷新失败：只提示，保留旧数据。
    toast.error(failureText(dashboard.value ? '刷新数据看板' : '加载数据看板', error))
  } finally {
    if (controller === current) {
      controller = null
      loading.value = false
    }
  }
}

/* ------------------------------------------------------------------ *
 * 展示数据
 * ------------------------------------------------------------------ */

const summaryItems = computed(() => toSummaryItems(dashboard.value?.summary))

const requestTrend = computed(() => buildRequestTrend(dashboard.value))
const requestNote = computed(() => trendFootnote(dashboard.value))
const lineOption = computed(() => applyLineDefaults(requestTrendOption(dashboard.value)))

const tokenTrend = computed(() => buildTokenTrend(usage.value))
const barOption = computed(() => applyBarDefaults(tokenTrendOption(usage.value)))

const platformItems = computed(() => platformBarItems(dashboard.value))
const modelItems = computed(() => modelBarItems(dashboard.value))
const keyItems = computed(() => keyBarItems(usage.value))

const hasRequestTrend = computed(
  () => requestTrend.value.total.length > 0 || requestTrend.value.series.length > 0,
)
const hasTokenTrend = computed(() => tokenTrend.value.labels.length > 0)

const isEmptyWindow = computed(() => Number(dashboard.value?.summary?.requests ?? 0) === 0)

const windowText = computed(() => {
  const window = dashboard.value?.window
  if (!window) return '近 ' + days.value + ' 天'
  return '窗口 ' + fmtTime(window.start) + ' → ' + fmtTime(window.end)
})

const updatedText = computed(() => (loadedAt.value ? fmtTime(loadedAt.value) : ''))

const platformNote = computed(() =>
  platformItems.value.length ? '共 ' + platformItems.value.length + ' 个平台 · 按请求数' : '无数据',
)
const modelNote = computed(() =>
  modelItems.value.length ? '共 ' + modelItems.value.length + ' 个模型 · 按请求数' : '无数据',
)
const keyNote = computed(() =>
  keyItems.value.length ? 'Top ' + keyItems.value.length + ' · 按请求数' : '无数据',
)

/* ------------------------------------------------------------------ *
 * 平台上下文（订阅 + 卸载取消）
 * ------------------------------------------------------------------ */

let offPlatform: (() => void) | null = null
let lastSelector = platformStore.selector

onMounted(() => {
  lastSelector = platformStore.selector
  void load()
  offPlatform = platformStore.onChange((detail) => {
    // 首屏 store 加载完成后也会 emit 一次：选择没变就不重复打接口。
    if (detail.selector === lastSelector) return
    lastSelector = detail.selector
    void load()
  })
})

onBeforeUnmount(() => {
  offPlatform?.()
  offPlatform = null
  controller?.abort()
  controller = null
})

// 时间窗口 / 粒度（含浏览器前进后退、分享链接打开）是唯一需要重新请求的视图状态。
watch([days, granularity], () => {
  void load()
})
</script>

<template>
  <div class="stats-page">
    <PageHeader title="数据看板" :help="HELP_TEXT">
      <template #actions>
        <Segmented
          :options="dayOptions"
          :model-value="String(days)"
          aria-label="统计区间"
          @update:model-value="setDays"
        />
        <Segmented
          :options="grainOptions"
          :model-value="granularity"
          aria-label="聚合粒度"
          @update:model-value="setGranularity"
        />
        <Btn variant="quiet" :icon="RefreshCw" :loading="loading" @click="load()">刷新</Btn>
      </template>
    </PageHeader>

    <ErrorBlock
      v-if="loadError && !dashboard"
      :title="loadError.message"
      :detail="loadError.detail"
      @retry="load()"
    />

    <div
      v-else-if="firstLoading"
      class="stats-skeleton"
      aria-busy="true"
      aria-label="数据看板加载中"
    >
      <n-skeleton height="96px" />
      <n-skeleton height="280px" />
      <div class="stats-grid">
        <n-skeleton height="248px" />
        <n-skeleton height="248px" />
      </div>
      <div class="stats-grid">
        <n-skeleton height="248px" />
        <n-skeleton height="248px" />
      </div>
    </div>

    <template v-else>
      <StatStrip :items="summaryItems" />

      <p class="stats-meta">
        <span>{{ windowText }}</span>
        <span v-if="updatedText">更新于 {{ updatedText }}</span>
        <span v-if="dashboard?.source === 'raw'">数据源：原表（汇总表降级）</span>
      </p>

      <Card title="请求趋势" :loading="loading">
        <template #extra>
          <span class="stats-note">{{ requestNote }}</span>
        </template>
        <Chart v-if="hasRequestTrend" :option="lineOption" height="260px" />
        <Empty
          v-else
          :description="isEmptyWindow ? '窗口内没有调用记录。换个时间窗口，或先发起一次调用。' : '没有可画的趋势数据。'"
          compact
        />
      </Card>

      <div class="stats-grid">
        <Card title="Token 趋势" :loading="loading">
          <template #extra>
            <span class="stats-note">{{ cacheFootnote(usage) }}</span>
          </template>
          <ErrorBlock
            v-if="usageError && !usage"
            :title="usageError.message"
            :detail="usageError.detail"
            compact
            @retry="load()"
          />
          <Chart v-else-if="hasTokenTrend" :option="barOption" height="240px" />
          <Empty v-else description="窗口内没有 Token 记录。" compact />
        </Card>

        <Card title="平台排行">
          <template #extra>
            <span class="stats-note">{{ platformNote }}</span>
          </template>
          <BarList
            v-if="platformItems.length"
            :items="platformItems"
            :limit="8"
            unit=" 次"
            :format="fmtInt"
          />
          <Empty v-else description="窗口内没有平台流量。" compact />
        </Card>
      </div>

      <div class="stats-grid">
        <Card title="模型排行">
          <template #extra>
            <span class="stats-note">{{ modelNote }}</span>
          </template>
          <BarList
            v-if="modelItems.length"
            :items="modelItems"
            :limit="8"
            unit=" 次"
            :format="fmtInt"
          />
          <Empty v-else description="窗口内没有模型调用。" compact />
        </Card>

        <Card title="Key 排行">
          <template #extra>
            <span class="stats-note">{{ keyNote }}</span>
          </template>
          <ErrorBlock
            v-if="usageError && !usage"
            :title="usageError.message"
            :detail="usageError.detail"
            compact
            @retry="load()"
          />
          <BarList
            v-else-if="keyItems.length"
            :items="keyItems"
            :limit="8"
            unit=" 次"
            :format="fmtInt"
          />
          <Empty v-else description="窗口内没有 Key 调用记录。" compact />
        </Card>
      </div>
    </template>
  </div>
</template>

<style scoped>
.stats-page {
  display: grid;
  gap: var(--sp-4);
  min-width: 0;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--sp-4);
  min-width: 0;
}

.stats-skeleton {
  display: grid;
  gap: var(--sp-4);
  min-width: 0;
}

.stats-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--sp-1) var(--sp-4);
  margin: 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.stats-note {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  font-variant-numeric: tabular-nums;
}

@media (max-width: 1023px) {
  .stats-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (max-width: 640px) {
  .stats-meta {
    gap: var(--sp-1);
  }
}
</style>
