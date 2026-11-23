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
import { errorMessage } from '@/utils/error'
import { ApiError, isAbortError } from '@/api/http'
import {
  STATS_DAYS,
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
import { useI18n } from '@/i18n'

const { t } = useI18n()
/** 旧版面板副标题（原样保留，收进 ⓘ）；语种跟随 useI18n。 */
const helpText = computed(() => t('page.stats.help'))

const toast = useToast()
const platformStore = usePlatformStore()

const table = useTableQuery('stats', {
  pageSize: 20,
  filters: { days: '1', grain: 'hour' },
})

const days = computed(() => normalizeDays(table.state.filters.days))
const granularity = computed(() => normalizeGranularity(table.state.filters.grain, days.value))

/**
 * days=1 是**滚动 24 小时**（后端 window.start = 当前 - 24h），不是自然日；
 * 页面上的按钮按真实口径写「近 24 小时」，与使用记录页的「今日（自然日）」区分。
 */
const dayOptions = computed<Array<SegmentedOption<string>>>(() =>
  STATS_DAYS.map((value) => ({
    label:
      value === 1
        ? t('page.stats.range.rolling24')
        : t('page.stats.range.days', { days: value }),
    value: String(value),
  })),
)

const grainOptions = computed<Array<SegmentedOption<string>>>(() => [
  { label: t('page.stats.grain.hour'), value: 'hour' },
  { label: t('page.stats.grain.day'), value: 'day' },
])

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
    toast.warning(t('page.stats.grainFallback'))
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
  return { message: t('page.stats.failure', { action, reason: errorMessage(error) }), detail }
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
        usageError.value = errorOf(t('page.stats.action.loadTokens'), error)
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
    loadError.value = errorOf(t('page.stats.action.load'), error)
    // 已有数据时刷新失败：只提示，保留旧数据。
    toast.error(
      t('page.stats.failure', {
        action: t(dashboard.value ? 'page.stats.action.refresh' : 'page.stats.action.load'),
        reason: errorMessage(error),
      }),
    )
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

const summaryItems = computed(() => toSummaryItems(dashboard.value?.summary, t))

const requestTrend = computed(() => buildRequestTrend(dashboard.value, t))
const requestNote = computed(() => trendFootnote(dashboard.value, t))
const lineOption = computed(() => applyLineDefaults(requestTrendOption(dashboard.value, t)))

const tokenTrend = computed(() => buildTokenTrend(usage.value))
const tokenNote = computed(() => cacheFootnote(usage.value, t))
const barOption = computed(() => applyBarDefaults(tokenTrendOption(usage.value, t)))

const platformItems = computed(() => platformBarItems(dashboard.value, t))
const modelItems = computed(() => modelBarItems(dashboard.value, t))
const keyItems = computed(() => keyBarItems(usage.value, t))

const hasRequestTrend = computed(
  () => requestTrend.value.total.length > 0 || requestTrend.value.series.length > 0,
)
const hasTokenTrend = computed(() => tokenTrend.value.labels.length > 0)

const isEmptyWindow = computed(() => Number(dashboard.value?.summary?.requests ?? 0) === 0)

const windowText = computed(() => {
  const window = dashboard.value?.window
  if (!window) return t('page.stats.windowGuess', { days: days.value })
  return t('page.stats.window', { start: fmtTime(window.start), end: fmtTime(window.end) })
})

const updatedText = computed(() => (loadedAt.value ? fmtTime(loadedAt.value) : ''))

const platformNote = computed(() =>
  platformItems.value.length
    ? t('page.stats.note.platforms', { count: platformItems.value.length })
    : t('page.stats.note.none'),
)
const modelNote = computed(() =>
  modelItems.value.length
    ? t('page.stats.note.models', { count: modelItems.value.length })
    : t('page.stats.note.none'),
)
const keyNote = computed(() =>
  keyItems.value.length
    ? t('page.stats.note.keys', { count: keyItems.value.length })
    : t('page.stats.note.none'),
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
    <PageHeader :help="helpText">
      <template #actions>
        <Segmented
          :options="dayOptions"
          :model-value="String(days)"
          :aria-label="t('page.stats.rangeAria')"
          @update:model-value="setDays"
        />
        <Segmented
          :options="grainOptions"
          :model-value="granularity"
          :aria-label="t('page.stats.grainAria')"
          @update:model-value="setGranularity"
        />
        <Btn variant="quiet" :icon="RefreshCw" :loading="loading" @click="load()">
          {{ t('page.stats.refresh') }}
        </Btn>
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
      :aria-label="t('page.stats.loadingAria')"
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
        <span v-if="days === 1" class="stats-scope">{{ t('page.stats.rollingScope') }}</span>
        <span v-if="updatedText">{{ t('page.stats.updatedAt', { time: updatedText }) }}</span>
        <span v-if="dashboard?.source === 'raw'">{{ t('page.stats.sourceRaw') }}</span>
      </p>

      <Card :title="t('page.stats.card.requests')" :loading="loading">
        <template #extra>
          <span class="stats-note">{{ requestNote }}</span>
        </template>
        <Chart v-if="hasRequestTrend" :option="lineOption" height="260px" />
        <Empty
          v-else
          :description="isEmptyWindow ? t('page.stats.empty.requests') : t('page.stats.empty.trend')"
          compact
        />
      </Card>

      <div class="stats-grid">
        <Card :title="t('page.stats.card.tokens')" :loading="loading">
          <template #extra>
            <span class="stats-note">{{ tokenNote }}</span>
          </template>
          <ErrorBlock
            v-if="usageError && !usage"
            :title="usageError.message"
            :detail="usageError.detail"
            compact
            @retry="load()"
          />
          <Chart v-else-if="hasTokenTrend" :option="barOption" height="240px" />
          <Empty v-else :description="t('page.stats.empty.tokens')" compact />
        </Card>

        <Card :title="t('page.stats.card.platforms')">
          <template #extra>
            <span class="stats-note">{{ platformNote }}</span>
          </template>
          <BarList
            v-if="platformItems.length"
            :items="platformItems"
            :limit="8"
            :unit="t('page.stats.unit.requests')"
            :format="fmtInt"
          />
          <Empty v-else :description="t('page.stats.empty.platforms')" compact />
        </Card>
      </div>

      <div class="stats-grid">
        <Card :title="t('page.stats.card.models')">
          <template #extra>
            <span class="stats-note">{{ modelNote }}</span>
          </template>
          <BarList
            v-if="modelItems.length"
            :items="modelItems"
            :limit="8"
            :unit="t('page.stats.unit.requests')"
            :format="fmtInt"
          />
          <Empty v-else :description="t('page.stats.empty.models')" compact />
        </Card>

        <Card :title="t('page.stats.card.keys')">
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
            :unit="t('page.stats.unit.requests')"
            :format="fmtInt"
          />
          <Empty v-else :description="t('page.stats.empty.keys')" compact />
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

.stats-scope {
  color: var(--fg-2);
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