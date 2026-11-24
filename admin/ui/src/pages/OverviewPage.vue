<script setup lang="ts">
/**
 * S21 概览 /overview（v2.1，10-visual-refresh：英雄卡线路带 + KPI 行 + 平台健康 + 最近请求）
 *
 * 契约：docs/ui-v2/contracts/overview.md（末尾「## 实现对照」）
 * 设计稿：docs/ui-v2/06-pages-a.md P1、10-visual-refresh.md §1.4 / §1.8
 *
 * 数据：
 *   · /api/overview          —— 额度、用量、缓存、账号监测（X-Platform 生效，切平台会变）；
 *   · /api/stats/dashboard   —— ① 线路带 + 平台健康（全网关，window=day 自然日）；
 *                               ② KPI 迷你折线（当前平台，window=day，失败不影响首屏）；
 *   · /api/sentinel/status   —— 告警横幅 + 最近请求（traffic.recent，全网关，近 1 小时）；
 *   · platform store         —— 平台数、图标、启用状态、账号数（外壳已加载，不重复请求）。
 *
 * 口径（第 1 轮审查 P0-5 / P0-6、N5）：每块都在卡头标明——
 *   KPI / 账号状态 / 缓存命中 = 当前平台（或「全网关」），线路带 / 平台健康 / 最近请求 = 全网关；
 *   时间窗一律北京时间自然日（「今日」= 00:00 起，不是滚动 24 小时）。
 *
 * 四态：首次加载骨架 / 刷新保留旧数据（卡片右上角转圈）/ 空 / 错误（ErrorBlock + 重试）。
 * 平台切换：订阅 usePlatformStore().onChange 重新取数，卸载取消。
 * 时间范围（今日 / 7 天 / 30 天）同步路由 query（?days=7）。
 *
 * r3 续跑（11 §2）：
 *   · 后台每 30 秒静默刷新（usePolling：标签页隐藏 / KeepAlive 离开时暂停，回来立即刷新一次）；
 *   · 静默刷新与「回到缓存页」都不置 loading —— 卡片不转圈、不出骨架，只在页头时间旁显示 14px 小转圈；
 *   · 静默刷新失败不弹 toast（避免每 30 秒弹一次），页头状态点变红，手动刷新才提示。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { RefreshCw, TriangleAlert } from 'lucide-vue-next'
import PageHeader from '@/layout/PageHeader.vue'
import {
  Badge,
  Btn,
  Card,
  Empty,
  ErrorBlock,
  KV,
  KpiCard,
  Meter,
  Ring,
  Segmented,
  StatusDot,
  type SegmentedOption,
} from '@/components'
import { usePolling, useRevisit, useTableQuery, useToast } from '@/composables'
import { usePlatformStore } from '@/stores/platform'
import { fmtTime } from '@/utils/format'
import { failureText } from '@/utils/error'
import { ApiError, isAbortError } from '@/api/http'
import { copyText } from '@/platform/clipboard'
import {
  OVERVIEW_DAYS,
  buildAlerts,
  buildBalanceHero,
  buildCache,
  buildLanes,
  buildMonitorRows,
  buildOverviewKpis,
  buildPlatformHealth,
  buildRecent,
  daysLabel,
  getOverview,
  getOverviewTraffic,
  getOverviewTrend,
  getSentinelStatus,
  lanesTotal,
  monitorConclusion,
  normalizeOverviewDays,
  overviewScopeLabel,
  type OverviewPayload,
  type SentinelStatusPayload,
} from '@/api/overview'
import type { StatsDashboardPayload } from '@/api/stats'
import { useI18n } from '@/i18n'
import OverviewHero from './Overview/OverviewHero.vue'
import OverviewHealth from './Overview/OverviewHealth.vue'
import OverviewRecent from './Overview/OverviewRecent.vue'

const { t } = useI18n()
const helpText = computed(() => t('page.overview.help'))

const toast = useToast()
const router = useRouter()
const platformStore = usePlatformStore()

const table = useTableQuery('overview', {
  pageSize: 20,
  filters: { days: '1' },
})

const days = computed(() => normalizeOverviewDays(table.state.filters.days))

const dayOptions = computed<Array<SegmentedOption<string>>>(() =>
  OVERVIEW_DAYS.map((value) => ({
    label: value === 1 ? t('page.overview.range.today') : t('page.overview.range.days', { days: value }),
    value: String(value),
  })),
)

function setDays(value: string | number): void {
  table.setFilter('days', String(normalizeOverviewDays(value)))
}

/* ------------------------------------------------------------------ *
 * 取数
 * ------------------------------------------------------------------ */

const payload = ref<OverviewPayload | null>(null)
const traffic = ref<StatsDashboardPayload | null>(null)
const trend = ref<StatsDashboardPayload | null>(null)
const sentinel = ref<SentinelStatusPayload | null>(null)
const loading = ref(false)
const loadError = ref<{ message: string; detail: string } | null>(null)
const updatedAt = ref<Date | null>(null)
/** 后台静默刷新进行中（页头小转圈）。 */
const silentBusy = ref(false)

let controller: AbortController | null = null

const firstLoading = computed(() => loading.value && payload.value === null)

function prettyDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail
  try {
    return JSON.stringify(detail, null, 2)
  } catch {
    return String(detail)
  }
}

/** 线路带 / 告警 / 折线是锦上添花：失败一律降级成 null，不拖垮整页。 */
function soft<T>(promise: Promise<T>): Promise<T | null> {
  return promise.catch(() => null)
}

async function load(options: { silent?: boolean } = {}): Promise<void> {
  // 静默刷新只在已有数据、且没有手动请求在途时进行（不打断手动刷新）。
  const silent = Boolean(options.silent) && payload.value !== null
  if (silent && controller) return
  controller?.abort()
  const current = new AbortController()
  controller = current
  if (silent) silentBusy.value = true
  else loading.value = true
  if (!silent) loadError.value = null
  try {
    const [main, lanesData, sentinelData] = await Promise.all([
      getOverview(days.value, current.signal),
      soft(getOverviewTraffic(days.value, current.signal)),
      soft(getSentinelStatus(300, current.signal)),
    ])
    if (controller !== current) return
    loadError.value = null
    payload.value = main
    traffic.value = lanesData
    sentinel.value = sentinelData
    updatedAt.value = new Date()
    void loadTrend(main, current)
  } catch (error) {
    if (isAbortError(error)) return
    const detail = error instanceof ApiError && error.detail ? prettyDetail(error.detail) : ''
    loadError.value = { message: failureText(t('page.overview.loadError'), error), detail }
    // 已有数据时刷新失败：只提示，保留旧数据；后台静默刷新失败不弹 toast。
    if (silent) return
    toast.error(
      failureText(
        t(payload.value ? 'page.overview.refreshError' : 'page.overview.loadError'),
        error,
      ),
    )
  } finally {
    if (controller === current) {
      controller = null
      loading.value = false
      silentBusy.value = false
    }
  }
}

/**
 * KPI 迷你折线：与 KPI 同口径（当前平台）。不阻塞首屏；
 * 没有当前平台（全网关）时直接复用线路带那份序列。
 */
async function loadTrend(main: OverviewPayload, current: AbortController): Promise<void> {
  const platform = String(main.platform || '')
  if (!platform) {
    trend.value = traffic.value
    return
  }
  const data = await soft(getOverviewTrend(days.value, platform, current.signal))
  if (current.signal.aborted) return
  trend.value = data
}

/* ------------------------------------------------------------------ *
 * 展示数据（全部来自 api/overview.ts 的纯函数）
 * ------------------------------------------------------------------ */

const hero = computed(() =>
  buildBalanceHero(
    payload.value?.accounts,
    payload.value?.today,
    payload.value?.window,
    payload.value?.average_credits,
    t,
  ),
)
const kpis = computed(() => buildOverviewKpis(payload.value, trend.value, days.value, t))
const monitorRows = computed(() => buildMonitorRows(payload.value?.monitor, t))
const monitorTotal = computed(() => Number(payload.value?.monitor?.total ?? 0))
const conclusion = computed(() => monitorConclusion(payload.value?.monitor, t))
const alerts = computed(() => buildAlerts(payload.value, sentinel.value, t))
const cache = computed(() => buildCache(payload.value?.cache, t))
const lanes = computed(() => buildLanes(traffic.value, platformStore.platforms, t))
const trafficTotal = computed(() => lanesTotal(lanes.value))
const health = computed(() => buildPlatformHealth(traffic.value, platformStore.platforms, t))
const recent = computed(() => buildRecent(sentinel.value, 8, t))

const modelCount = computed(() => Number(payload.value?.models ?? 0))
const platformCount = computed(() => platformStore.platforms.length)
const rangeLabel = computed(() => daysLabel(days.value, t))
const trafficTitle = computed(() =>
  days.value === 1
    ? t('page.overview.traffic.todayTitle')
    : t('page.overview.traffic.daysTitle', { days: days.value }),
)

/** 口径标签：用量 / 账号跟随当前平台，流量是全网关。 */
const usageScope = computed(() => overviewScopeLabel(payload.value, 'usage', t))
const accountScope = computed(() => overviewScopeLabel(payload.value, 'accounts', t))
const trafficScope = computed(() => overviewScopeLabel(payload.value, 'traffic', t))
/** 「北京时间自然日」写在 KPI 行标题里（第 1 轮审查 P0-5：「今日」不是滚动 24 小时）。 */
const kpiCaption = computed(() =>
  t('page.overview.kpi.caption', { scope: usageScope.value, range: rangeLabel.value }),
)

const updatedText = computed(() => (updatedAt.value ? fmtTime(updatedAt.value) : '—'))
const statusText = computed(() =>
  loadError.value
    ? t('page.overview.status.error')
    : firstLoading.value
      ? t('page.overview.status.loading')
      : t('page.overview.status.ok'),
)
const statusTone = computed(() => (loadError.value ? 'danger' : firstLoading.value ? 'neutral' : 'ok'))

const baseUrl = computed(() =>
  (typeof location === 'undefined' ? '' : location.origin) + '/v1',
)

async function copyBase(): Promise<void> {
  const ok = await copyText(baseUrl.value)
  if (ok) toast.success(t('common.copied'))
  else toast.error(t('common.copyFailed'))
}

/** 点击线路 / 健康行：切到该平台（全局上下文）并打开平台管理页。 */
function onLaneSelect(id: string): void {
  const spec = platformStore.specOf(id) || platformStore.specFor(id)
  if (spec) platformStore.setCurrent(spec.id)
  void router.push('/platforms')
}

/* ------------------------------------------------------------------ *
 * 生命周期
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

// 回到缓存页（KeepAlive）：保留旧数据静默刷新，不出骨架（11-polish-motion §2）。
useRevisit(() => load({ silent: true }), { minIntervalMs: 5000 })

// 线路带 / KPI 的后台静默刷新：30 秒一次，不可见或离开页面时暂停（usePolling 自带）。
const poll = usePolling(() => load({ silent: true }), 30000, { immediate: false })
onMounted(() => poll.start())

// 时间范围（含浏览器前进后退、分享链接）是唯一需要重新请求的视图状态。
watch(days, () => {
  void load()
})
</script>

<template>
  <div class="ov-page">
    <PageHeader :help="helpText">
      <template #meta>
        <StatusDot :tone="statusTone" :label="statusText" />
        <span class="ov-meta">
          {{ t('page.overview.platformsModels', { platforms: platformCount, models: modelCount }) }}
        </span>
        <span class="ov-meta ov-meta--time">
          <RefreshCw
            v-if="silentBusy"
            class="ov-spin"
            :size="14"
            :aria-label="t('page.overview.silentRefreshing')"
          />
          {{ t('page.overview.updatedAt', { time: updatedText }) }}
        </span>
      </template>
      <template #actions>
        <Segmented
          :options="dayOptions"
          :model-value="String(days)"
          :aria-label="t('page.overview.rangeAria')"
          @update:model-value="setDays"
        />
        <Btn variant="quiet" :icon="RefreshCw" :loading="loading" @click="load()">
          {{ t('page.overview.refresh') }}
        </Btn>
      </template>
    </PageHeader>

    <ErrorBlock
      v-if="loadError && !payload"
      :title="loadError.message"
      :detail="loadError.detail"
      @retry="load()"
    />

    <div
      v-else-if="firstLoading"
      class="ov-skeleton"
      aria-busy="true"
      :aria-label="t('page.overview.loadingAria')"
    >
      <!-- 骨架与真实布局同构：英雄卡 / KPI 卡 / 两栏，同尺寸，数据到来不跳动（11 §4-1）。 -->
      <OverviewHero
        :title="trafficTitle"
        scope=""
        :range-label="rangeLabel"
        :lanes="[]"
        :total="0"
        :base-url="baseUrl"
        skeleton
      />
      <section class="ov-section">
        <p class="ov-section__caption"><span class="mh-skeleton ov-skeleton__caption" /></p>
        <div class="ov-kpis">
          <KpiCard v-for="item in kpis" :key="item.key" :label="item.label" :value="null" loading />
        </div>
      </section>
      <div class="ov-grid">
        <div class="mh-skeleton ov-skeleton__block" />
        <div class="mh-skeleton ov-skeleton__block" />
      </div>
    </div>

    <template v-else>
      <!-- 告警横幅（只在异常时出现） -->
      <div
        v-for="(alert, index) in alerts"
        :key="alert.title + index"
        class="ov-alert"
        :class="alert.level === 'danger' ? 'is-danger' : 'is-warn'"
        role="status"
      >
        <TriangleAlert :size="16" class="ov-alert__icon" />
        <div class="ov-alert__main">
          <strong>{{ alert.title }}</strong>
          <span class="ov-alert__detail">{{ alert.detail }}</span>
        </div>
        <RouterLink class="ov-alert__link" :to="alert.to">{{ alert.actionLabel }} →</RouterLink>
      </div>

      <!-- 英雄卡：线路带（全网关） -->
      <OverviewHero
        :title="trafficTitle"
        :scope="trafficScope"
        :range-label="rangeLabel"
        :lanes="lanes"
        :total="trafficTotal"
        :loading="loading"
        :base-url="baseUrl"
        :window-minutes="days * 1440"
        @select="onLaneSelect"
        @copy="copyBase"
      />

      <!-- KPI 行（当前平台 · 自然日） -->
      <section class="ov-section" :aria-label="kpiCaption">
        <p class="ov-section__caption">
          <Badge tone="neutral">{{ usageScope }}</Badge>
          <span>{{ kpiCaption }}</span>
        </p>
        <div class="ov-kpis">
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
          >
            <template v-if="item.key === 'balance'" #extra>
              <div class="ov-balance">
                <Meter
                  :value="hero.ratio * 100"
                  :tone="hero.tone"
                  :height="4"
                  :aria-label="t('page.overview.balance.ratioAria', { ratio: hero.ratioText })"
                />
                <p class="ov-balance__rows">
                  <span v-for="row in hero.rows" :key="row.label">
                    {{ row.label }} <b class="tnum">{{ row.value }}</b>
                  </span>
                </p>
                <p v-if="hero.note" class="ov-note">{{ hero.note }}</p>
              </div>
            </template>
          </KpiCard>
        </div>
      </section>

      <!-- 平台健康 + 账号状态 / 缓存命中 -->
      <div class="ov-grid">
        <OverviewHealth
          :health="health"
          :scope="trafficScope"
          :loading="loading"
          @select="onLaneSelect"
        />

        <div class="ov-side">
          <Card :title="t('page.overview.accounts.title')" :loading="loading">
            <template #extra>
              <span class="ov-scope">{{ accountScope }}</span>
              <RouterLink class="ov-link" to="/accounts">{{ t('page.overview.accounts.all') }}</RouterLink>
            </template>
            <div v-if="monitorTotal > 0" class="ov-monitor">
              <div v-for="row in monitorRows" :key="row.key" class="ov-monitor__row">
                <span class="ov-monitor__label" :class="{ 'is-faint': row.faint }">
                  {{ row.label }}
                </span>
                <Meter
                  :value="row.pct"
                  :tone="row.tone"
                  :height="6"
                  :aria-label="t('page.overview.accounts.ratioAria', { label: row.label })"
                />
                <span class="ov-monitor__count tnum" :class="{ 'is-faint': row.faint }">
                  {{ row.count }}
                </span>
              </div>
              <p class="ov-conclusion" :class="conclusion.tone === 'ok' ? 'is-ok' : 'is-warn'">
                <RouterLink v-if="conclusion.to" :to="conclusion.to">
                  {{ conclusion.text }} →
                </RouterLink>
                <template v-else>{{ conclusion.text }}</template>
              </p>
            </div>
            <Empty v-else :description="t('page.overview.accounts.empty')" compact>
              <Btn variant="primary" size="sm" @click="router.push('/accounts')">
                {{ t('page.overview.accounts.go') }}
              </Btn>
            </Empty>
          </Card>

          <Card :title="t('page.overview.cache.title')" :loading="loading">
            <template #extra>
              <span class="ov-scope">{{ usageScope }}</span>
            </template>
            <div class="ov-cache">
              <Ring
                :value="cache.value"
                :text="cache.text"
                tone="brand"
                :label="t('page.overview.cache.ringLabel')"
              />
              <div class="ov-cache__body">
                <KV :items="cache.kv" label-width="72px" />
                <p class="ov-note">{{ cache.note }}</p>
              </div>
            </div>
          </Card>
        </div>
      </div>

      <!-- 最近请求（全网关 · 近 1 小时） -->
      <OverviewRecent :rows="recent" :scope="trafficScope" :loading="loading" />
    </template>
  </div>
</template>

<style scoped>
.ov-page {
  display: grid;
  gap: var(--sp-5);
  min-width: 0;
}

.ov-meta {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  white-space: nowrap;
}

.ov-meta--time {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-variant-numeric: tabular-nums;
}

.ov-spin {
  color: var(--fg-muted);
  animation: ov-spin 900ms linear infinite;
}

@keyframes ov-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .ov-spin {
    animation-duration: 2.4s;
  }
}

.ov-skeleton {
  display: grid;
  gap: var(--sp-5);
  min-width: 0;
}

.ov-skeleton__caption {
  display: inline-block;
  width: 240px;
  max-width: 60%;
  height: 18px;
  border-radius: var(--r-chip);
}

.ov-skeleton__block {
  min-height: 320px;
  border-radius: var(--r-card);
}

/* ---------- 告警横幅 ---------- */
.ov-alert {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  padding: var(--sp-3) var(--sp-4);
  border-radius: var(--r-card);
  border: 1px solid transparent;
  min-width: 0;
}

.ov-alert.is-danger {
  background: var(--danger-soft);
  border-color: var(--danger);
  color: var(--danger);
}

.ov-alert.is-warn {
  background: var(--warn-soft);
  border-color: var(--warn-soft);
  color: var(--warn);
}

.ov-alert__icon {
  flex: none;
}

.ov-alert__main {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: var(--sp-1) var(--sp-3);
  flex: 1;
  min-width: 0;
  color: var(--fg);
}

.ov-alert__detail {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.ov-alert__link {
  flex: none;
  font-size: var(--fs-small);
  font-weight: 500;
  color: inherit;
  text-decoration: none;
}

.ov-alert__link:hover {
  text-decoration: underline;
}

/* ---------- KPI 行 ---------- */
.ov-section {
  display: grid;
  gap: var(--sp-2);
  min-width: 0;
}

.ov-section__caption {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  margin: 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

/* 一行 4 个（≥1280）、2 个（≥768）、1 个（手机）；卡片按内容等高，不强撑（§1.4）。 */
.ov-kpis {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--sp-4);
  align-items: start;
  min-width: 0;
}

.ov-balance {
  display: grid;
  gap: var(--sp-2);
}

.ov-balance__rows {
  margin: 0;
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: var(--sp-1) var(--sp-3);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.ov-balance__rows b {
  color: var(--fg);
  font-weight: 500;
}

.ov-note {
  margin: 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

/* ---------- 两列 ---------- */
.ov-grid {
  display: grid;
  grid-template-columns: minmax(0, 8fr) minmax(0, 4fr);
  gap: var(--sp-4);
  align-items: start;
  min-width: 0;
}

.ov-side {
  display: grid;
  gap: var(--sp-4);
  min-width: 0;
}

.ov-scope {
  font-size: var(--fs-small);
  color: var(--fg-muted);
}

/* ---------- 账号状态 ---------- */
.ov-monitor {
  display: grid;
  gap: var(--sp-3);
}

.ov-monitor__row {
  display: grid;
  grid-template-columns: 64px minmax(0, 1fr) 32px;
  align-items: center;
  gap: var(--sp-3);
}

.ov-monitor__label {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-2);
}

.ov-monitor__count {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg);
  text-align: right;
}

.ov-monitor__label.is-faint,
.ov-monitor__count.is-faint {
  color: var(--fg-faint);
}

.ov-conclusion {
  margin: var(--sp-1) 0 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
}

.ov-conclusion.is-ok {
  color: var(--ok);
}

.ov-conclusion.is-warn {
  color: var(--warn);
}

.ov-conclusion a {
  color: inherit;
  text-decoration: none;
  font-weight: 500;
}

.ov-conclusion a:hover {
  text-decoration: underline;
}

.ov-link {
  font-size: var(--fs-small);
  color: var(--brand);
  text-decoration: none;
}

.ov-link:hover {
  text-decoration: underline;
}

/* ---------- 缓存 ---------- */
.ov-cache {
  display: flex;
  align-items: center;
  gap: var(--sp-4);
  flex-wrap: wrap;
}

.ov-cache__body {
  display: grid;
  gap: var(--sp-2);
  flex: 1;
  min-width: 160px;
}

@media (max-width: 1279px) {
  .ov-kpis {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 1023px) {
  .ov-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .ov-side {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 767px) {
  .ov-kpis,
  .ov-side {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
