<script setup lang="ts">
/**
 * S17 数据看板 /stats（v2.1：页头 + KPI 行 + 两张主图 + 三张排行卡）
 *
 * 契约：docs/ui-v2/contracts/stats.md（末尾「## 实现对照」）
 * 设计稿：docs/ui-v2/06-pages-a.md P3、10-visual-refresh.md §1.4 / §1.8
 *
 * 数据来源：
 *   · /api/stats/dashboard —— KPI、请求趋势（按模型）、平台排行、模型排行、失败分类；
 *   · /api/stats/usage     —— 只补看板接口没有的两块：Token 构成（输入/输出/缓存）与 Key 排行。
 *
 * 口径（页头 meta 写明）：
 *   · **全网关**：两个接口都不读 X-Platform（后端 `scope: "gateway"`），切平台数字不变 ——
 *     所以本页不再订阅平台切换（platform_scoping_review §3：原来订阅了也只是白打一遍接口）；
 *   · days=1 是**滚动 24 小时**，不是自然日（按钮文案就写「近 24 小时」）；
 *   · 横轴一律本地时区（bucketLabel）；平均耗时 > 1 小时显示「数据异常」（avgDurationText）。
 *
 * 四态：首次加载骨架 / 刷新保留旧数据（卡片右上角转圈）/ 空（图表内 Empty）/ 错误（ErrorBlock + 重试）。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RefreshCw } from 'lucide-vue-next'
import PageHeader from '@/layout/PageHeader.vue'
import {
  Badge,
  BarList,
  Btn,
  Card,
  Chart,
  Empty,
  ErrorBlock,
  KpiCard,
  Segmented,
  applyBarDefaults,
  applyLineDefaults,
  type SegmentedOption,
} from '@/components'
import { useTableQuery, useToast } from '@/composables'
import { fmtInt, fmtTime } from '@/utils/format'
import { errorMessage } from '@/utils/error'
import { ApiError, isAbortError } from '@/api/http'
import {
  STATS_DAYS,
  buildRequestTrend,
  buildTokenTrend,
  cacheFootnote,
  errorBarItems,
  getStatsDashboard,
  getStatsUsage,
  keyBarItems,
  modelBarItems,
  normalizeDays,
  normalizeGranularity,
  platformBarItems,
  requestTrendOption,
  statsScopeLabel,
  toStatsKpis,
  tokenTrendOption,
  trendFootnote,
  type StatsDashboardPayload,
  type StatsGranularity,
  type StatsUsagePayload,
} from '@/api/stats'
import { groupTitle, useI18n } from '@/i18n'
import { groupOf } from '@/router/pages'
import { useRevisit } from '@/pages/_shared/useRevisit'

const { t } = useI18n()
/** 旧版面板副标题（原样保留，收进 ⓘ）；语种跟随 useI18n。 */
const helpText = computed(() => t('page.stats.help'))
/** 页头 eyebrow：所在分组名（10-visual-refresh §1.3）。 */
const eyebrow = computed(() => groupTitle(groupOf('stats').key))

const toast = useToast()

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

const kpis = computed(() => toStatsKpis(dashboard.value, usage.value, t))

const requestTrend = computed(() => buildRequestTrend(dashboard.value, t))
const requestNote = computed(() => trendFootnote(dashboard.value, t))
const lineOption = computed(() => applyLineDefaults(requestTrendOption(dashboard.value, t)))

const tokenTrend = computed(() => buildTokenTrend(usage.value))
const tokenNote = computed(() => cacheFootnote(usage.value, t))
const barOption = computed(() => applyBarDefaults(tokenTrendOption(usage.value, t)))

const platformItems = computed(() => platformBarItems(dashboard.value, t))
const modelItems = computed(() => modelBarItems(dashboard.value, t))
const keyItems = computed(() => keyBarItems(usage.value, t))
const errorItems = computed(() => errorBarItems(dashboard.value, t))

const hasRequestTrend = computed(
  () => requestTrend.value.total.length > 0 || requestTrend.value.series.length > 0,
)
const hasTokenTrend = computed(() => tokenTrend.value.labels.length > 0)

const isEmptyWindow = computed(() => Number(dashboard.value?.summary?.requests ?? 0) === 0)

const scopeLabel = computed(() => statsScopeLabel(dashboard.value, t))

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
 * 生命周期
 *
 * 不再订阅平台切换：/api/stats/dashboard 与 /usage 都不读 X-Platform（全网关口径），
 * 切平台后数字本来就不变，重新请求只是白打一次（platform_scoping_review §3）。
 * ------------------------------------------------------------------ */

onMounted(() => {
  void load()
})

onBeforeUnmount(() => {
  controller?.abort()
  controller = null
})

// 回到 KeepAlive 缓存页：保留旧数据静默刷新，不出骨架（11 §2）。
useRevisit(() => load())

// 时间窗口 / 粒度（含浏览器前进后退、分享链接打开）是唯一需要重新请求的视图状态。
watch([days, granularity], () => {
  void load()
})
</script>

<template>
  <div class="stats-page">
    <PageHeader :help="helpText" :eyebrow="eyebrow">
      <template #meta>
        <Badge tone="neutral">{{ scopeLabel }}</Badge>
      </template>
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
      <!-- 骨架与真实布局同构（KpiCard 自带骨架、图表卡同标题同高），数据到来不跳动（11 §4-1）。 -->
      <section class="stats-kpis">
        <KpiCard v-for="item in kpis" :key="item.key" :label="item.label" :value="null" loading />
      </section>
      <p class="stats-meta"><span class="mh-skeleton stats-skeleton__line" /></p>
      <div class="stats-charts">
        <Card class="stats-chart-card mh-panel-card" :title="t('page.stats.card.requests')">
          <div class="mh-skeleton stats-skeleton__chart" />
        </Card>
        <Card class="stats-chart-card mh-panel-card" :title="t('page.stats.card.tokens')">
          <div class="mh-skeleton stats-skeleton__chart" />
        </Card>
      </div>
      <div class="stats-ranks">
        <div v-for="index in 3" :key="index" class="mh-skeleton stats-skeleton__rank mh-panel-placeholder" />
      </div>
    </div>

    <template v-else>
      <section class="stats-kpis" :aria-label="t('page.stats.kpiAria')">
        <KpiCard
          v-for="item in kpis"
          :key="item.key"
          :label="item.label"
          :value="item.value"
          split-unit
          :hint="item.hint"
          :tone="item.tone"
          :spark="item.spark"
          :loading="loading && !!dashboard"
        />
      </section>

      <p class="stats-meta">
        <span>{{ windowText }}</span>
        <span v-if="days === 1" class="stats-scope">{{ t('page.stats.rollingScope') }}</span>
        <span>{{ t('page.stats.scopeNote', { scope: scopeLabel }) }}</span>
        <span v-if="updatedText">{{ t('page.stats.updatedAt', { time: updatedText }) }}</span>
        <span v-if="dashboard?.source === 'raw'">{{ t('page.stats.sourceRaw') }}</span>
      </p>

      <div class="stats-charts">
        <Card class="stats-chart-card mh-panel-card" :title="t('page.stats.card.requests')" :loading="loading">
          <template #extra>
            <span class="stats-note">{{ requestNote }}</span>
          </template>
          <Chart v-if="hasRequestTrend" :option="lineOption" height="260px" />
          <Empty
            v-else
            :description="isEmptyWindow ? t('page.stats.empty.requests') : t('page.stats.empty.trend')"
            compact
          />
          <p v-if="errorItems.length" class="stats-errors">
            <span class="stats-errors__label">{{ t('page.stats.errorsLabel') }}</span>
            <span v-for="item in errorItems.slice(0, 5)" :key="item.label" class="stats-errors__item">
              {{ item.label }} <b class="tnum">{{ fmtInt(item.value) }}</b>
            </span>
          </p>
        </Card>

        <Card class="stats-chart-card mh-panel-card" :title="t('page.stats.card.tokens')" :loading="loading">
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
          <Chart v-else-if="hasTokenTrend" :option="barOption" height="260px" />
          <Empty v-else :description="t('page.stats.empty.tokens')" compact />
        </Card>
      </div>

      <div class="stats-ranks">
        <Card class="mh-panel-card" :title="t('page.stats.card.platforms')">
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

        <Card class="mh-panel-card" :title="t('page.stats.card.models')">
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

        <Card class="mh-panel-card" :title="t('page.stats.card.keys')">
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

/* KPI 行：≥1280 一行 5 个；768–1279 自动换成 2–3 个；手机 1 个。卡片跨行等高。 */
.stats-kpis {
  display: grid;
  grid-auto-rows: 1fr;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: var(--sp-4);
  align-items: stretch;
  min-width: 0;
}

.stats-charts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--sp-4);
  min-width: 0;
  align-items: stretch;
}

/* 两张主图是大卡：圆角用 --r-lg（10-visual-refresh §1.1）。 */
.stats-chart-card {
  border-radius: var(--r-lg);
}

.stats-ranks {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--sp-4);
  min-width: 0;
  /* 排行与趋势共享面板尺寸；长列表只滚动卡片内容。 */
  align-items: stretch;
}

.stats-skeleton {
  display: grid;
  gap: var(--sp-4);
  min-width: 0;
}

.stats-skeleton__line {
  display: inline-block;
  width: 320px;
  max-width: 70%;
  height: 16px;
  border-radius: var(--r-chip);
}

/* 与 <Chart height="260px"> 同高 */
.stats-skeleton__chart {
  height: 260px;
  border-radius: var(--r-ctl);
}

.stats-skeleton__rank {
  min-height: 240px;
  border-radius: var(--r-card);
}

.stats-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--sp-1) var(--sp-4);
  margin: calc(var(--sp-2) * -1) 0 0;
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

.stats-errors {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: var(--sp-1) var(--sp-3);
  margin: var(--sp-3) 0 0;
  padding-top: var(--sp-3);
  border-top: 1px solid var(--border-soft);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.stats-errors__label {
  color: var(--fg-2);
}

.stats-errors__item b {
  font-weight: 500;
  color: var(--danger);
}

@media (max-width: 1279px) {
  .stats-ranks {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 1023px) {
  .stats-charts,
  .stats-ranks {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (max-width: 640px) {
  .stats-meta {
    gap: var(--sp-1);
  }
}
</style>
