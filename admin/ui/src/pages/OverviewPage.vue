<script setup lang="ts">
/**
 * S21 概览 /overview（模板 A：状态行 + 英雄卡 + 流量带 + 次级卡片）
 *
 * 契约：docs/ui-v2/contracts/overview.md（末尾「## 实现对照」）
 * 设计稿：docs/ui-v2/06-pages-a.md P1
 *
 * 数据：
 *   · /api/overview          —— 额度、用量、缓存、账号监测（X-Platform 生效，切平台会变）；
 *   · /api/stats/dashboard   —— 流量带的每平台请求数 / 成功率（全网关口径）；
 *   · /api/sentinel/status   —— 告警横幅（封控 / 平台暂停）；
 *   · platform store         —— 平台数、图标、禁用状态（外壳已加载，不重复请求）。
 *
 * 四态：首次加载骨架 / 刷新保留旧数据（卡片右上角转圈）/ 空（各卡片 Empty / 流量带自带「暂无流量」）/ 错误（ErrorBlock + 重试）。
 * 平台切换：订阅 usePlatformStore().onChange 重新取数，卸载取消。
 * 时间范围（今日 / 7 天 / 30 天）同步路由 query（?days=7）。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { NSkeleton } from 'naive-ui'
import { Copy, RefreshCw, TriangleAlert } from 'lucide-vue-next'
import PageHeader from '@/layout/PageHeader.vue'
import {
  Badge,
  Btn,
  Card,
  Empty,
  ErrorBlock,
  IconBtn,
  KV,
  Meter,
  Ring,
  RouteRibbon,
  Segmented,
  StatStrip,
  StatusDot,
  type SegmentedOption,
} from '@/components'
import { useTableQuery, useToast } from '@/composables'
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
  buildUsageItems,
  daysLabel,
  getOverview,
  getOverviewTraffic,
  getSentinelStatus,
  lanesTotal,
  monitorConclusion,
  normalizeOverviewDays,
  type OverviewPayload,
  type SentinelStatusPayload,
} from '@/api/overview'
import type { StatsDashboardPayload } from '@/api/stats'
import { useI18n } from '@/i18n'

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
const sentinel = ref<SentinelStatusPayload | null>(null)
const loading = ref(false)
const loadError = ref<{ message: string; detail: string } | null>(null)
const updatedAt = ref<Date | null>(null)

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

async function load(): Promise<void> {
  controller?.abort()
  const current = new AbortController()
  controller = current
  loading.value = true
  loadError.value = null
  try {
    const [main, lanesData, sentinelData] = await Promise.all([
      getOverview(days.value, current.signal),
      // 流量带 / 告警是锦上添花：失败不拖垮整页。
      getOverviewTraffic(days.value, current.signal).catch((error: unknown) => {
        if (isAbortError(error)) return null
        return null
      }),
      getSentinelStatus(300, current.signal).catch((error: unknown) => {
        if (isAbortError(error)) return null
        return null
      }),
    ])
    if (controller !== current) return
    payload.value = main
    traffic.value = lanesData
    sentinel.value = sentinelData
    updatedAt.value = new Date()
  } catch (error) {
    if (isAbortError(error)) return
    const detail = error instanceof ApiError && error.detail ? prettyDetail(error.detail) : ''
    loadError.value = { message: failureText(t('page.overview.loadError'), error), detail }
    // 已有数据时刷新失败：只提示，保留旧数据。
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
    }
  }
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
const usageItems = computed(() => buildUsageItems(payload.value?.window, days.value, t))
const monitorRows = computed(() => buildMonitorRows(payload.value?.monitor, t))
const monitorTotal = computed(() => Number(payload.value?.monitor?.total ?? 0))
const conclusion = computed(() => monitorConclusion(payload.value?.monitor, t))
const alerts = computed(() => buildAlerts(payload.value, sentinel.value, t))
const cache = computed(() => buildCache(payload.value?.cache, t))
const lanes = computed(() => buildLanes(traffic.value, platformStore.platforms, t))
const trafficTotal = computed(() => lanesTotal(lanes.value))

const modelCount = computed(() => Number(payload.value?.models ?? 0))
const platformCount = computed(() => platformStore.platforms.length)
const platformLabel = computed(() => payload.value?.platform_label || t('page.overview.allPlatforms'))
const rangeLabel = computed(() => daysLabel(days.value, t))
const trafficTitle = computed(() =>
  days.value === 1
    ? t('page.overview.traffic.todayTitle')
    : t('page.overview.traffic.daysTitle', { days: days.value }),
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

/** 点击线路：切到该平台（全局上下文）并打开平台管理页。 */
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

// 时间范围（含浏览器前进后退、分享链接）是唯一需要重新请求的视图状态。
watch(days, () => {
  void load()
})
</script>

<template>
  <div class="ov-page">
    <PageHeader :help="helpText">
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
      <n-skeleton height="48px" />
      <!-- 与真实卡片同尺寸的静态文案：LCP 在首屏即锁定，不再等 /api/overview。 -->
      <div class="mh-card ov-skeleton__note">
        <p class="ov-balance__value">{{ t('common.loading') }}</p>
        <p class="ov-note">{{ t('page.overview.balance.remaining') }}</p>
      </div>
      <div class="ov-hero">
        <n-skeleton height="196px" />
        <n-skeleton height="196px" />
      </div>
      <div class="ov-grid">
        <n-skeleton height="280px" />
        <n-skeleton height="280px" />
      </div>
      <n-skeleton height="196px" />
    </div>

    <template v-else>
      <!-- 状态行 -->
      <Card flush class="ov-status-card">
        <div class="ov-status">
          <StatusDot :tone="statusTone" :label="statusText" :pulse="!loadError && !firstLoading" />
          <span class="ov-status__label">Base URL</span>
          <code class="mono ov-status__url">{{ baseUrl }}</code>
          <IconBtn :label="t('page.overview.copyBase')" :icon="Copy" @click="copyBase" />
          <span class="ov-status__meta">
            {{ t('page.overview.platformsModels', { platforms: platformCount, models: modelCount }) }}
          </span>
          <span class="ov-status__meta">{{ t('page.overview.current', { platform: platformLabel }) }}</span>
          <span class="ov-status__right">{{ t('page.overview.updatedAt', { time: updatedText }) }}</span>
        </div>
      </Card>

      <!-- 告警横幅 -->
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

      <!-- 英雄卡 + 用量条 -->
      <div class="ov-hero">
        <Card class="ov-balance">
          <div class="ov-balance__head">
            <span class="ov-label">{{ t('page.overview.balance.remaining') }}</span>
            <Badge tone="neutral">{{ t('page.overview.balance.models', { count: modelCount }) }}</Badge>
          </div>
          <p class="ov-balance__value">{{ hero.remainText }}</p>
          <p class="ov-balance__rows">
            <span v-for="row in hero.rows" :key="row.label">
              {{ row.label }} <b class="tnum">{{ row.value }}</b>
            </span>
          </p>
          <Meter
            :value="hero.ratio * 100"
            :tone="hero.tone"
            :aria-label="t('page.overview.balance.ratioAria', { ratio: hero.ratioText })"
          />
          <p class="ov-note">{{ hero.caption }}</p>
          <p v-if="hero.note" class="ov-note">{{ hero.note }}</p>
        </Card>
        <StatStrip :items="usageItems" />
      </div>

      <!-- 流量带 + 账号状态 -->
      <div class="ov-grid">
        <Card :title="trafficTitle" :loading="loading">
          <template #extra>
            <span class="ov-note">{{ t('page.overview.traffic.hint', { range: rangeLabel }) }}</span>
          </template>
          <RouteRibbon
            :source="{ label: t('page.overview.source'), total: trafficTotal }"
            :lanes="lanes"
            @select="onLaneSelect"
          />
          <p v-if="!lanes.length" class="ov-note">{{ t('page.overview.traffic.empty') }}</p>
        </Card>

        <Card :title="t('page.overview.accounts.title')" :loading="loading">
          <template #extra>
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
                :aria-label="t('page.overview.accounts.ratioAria', { label: row.label })"
              />
              <span class="ov-monitor__count tnum" :class="{ 'is-faint': row.faint }">
                {{ row.count }}
              </span>
            </div>
            <p
              class="ov-conclusion"
              :class="conclusion.tone === 'ok' ? 'is-ok' : 'is-warn'"
            >
              <RouterLink v-if="conclusion.to" :to="conclusion.to">
                {{ conclusion.text }} →
              </RouterLink>
              <template v-else>{{ conclusion.text }}</template>
            </p>
          </div>
          <Empty v-else :description="t('page.overview.accounts.empty')" compact>
            <Btn variant="default" size="sm" @click="router.push('/accounts')">
              {{ t('page.overview.accounts.go') }}
            </Btn>
          </Empty>
        </Card>
      </div>

      <!-- 缓存命中 -->
      <Card :title="t('page.overview.cache.title')" :loading="loading">
        <div class="ov-cache">
          <Ring
            :value="cache.value"
            :text="cache.text"
            tone="brand"
            :label="t('page.overview.cache.ringLabel')"
          />
          <div class="ov-cache__body">
            <KV :items="cache.kv" label-width="84px" />
            <p class="ov-note">{{ cache.note }}</p>
          </div>
        </div>
      </Card>
    </template>
  </div>
</template>

<style scoped>
.ov-page {
  display: grid;
  gap: var(--sp-4);
  min-width: 0;
}

.ov-skeleton {
  display: grid;
  gap: var(--sp-4);
  min-width: 0;
}

.ov-skeleton__note {
  min-height: 196px;
  padding: var(--sp-4) var(--sp-5);
}

/* ---------- 状态行 ---------- */
.ov-status-card {
  min-width: 0;
}

.ov-status {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--sp-2) var(--sp-3);
  min-height: 48px;
  padding: var(--sp-2) var(--sp-5);
}

.ov-status__label {
  font-size: var(--fs-small);
  color: var(--fg-muted);
}

.ov-status__url {
  color: var(--fg-2);
  background: var(--surface-2);
  border: 1px solid var(--border-soft);
  border-radius: var(--r-chip);
  padding: 1px var(--sp-2);
  word-break: break-all;
}

.ov-status__meta {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.ov-status__right {
  margin-left: auto;
  font-size: var(--fs-small);
  color: var(--fg-muted);
}

/* ---------- 告警横幅 ---------- */
.ov-alert {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  padding: var(--sp-3) var(--sp-4);
  border-radius: var(--r-ctl);
  border: 1px solid transparent;
  min-width: 0;
}

.ov-alert.is-danger {
  background: var(--danger-soft);
  border-color: var(--danger-soft);
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

/* ---------- 英雄卡 ---------- */
.ov-hero {
  display: grid;
  grid-template-columns: minmax(0, 5fr) minmax(0, 7fr);
  gap: var(--sp-4);
  align-items: stretch;
  min-width: 0;
}

.ov-balance {
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
}

.ov-balance__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-2);
}

.ov-label {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.ov-balance__value {
  margin: 0;
  font-size: var(--fs-display);
  line-height: var(--lh-display);
  font-weight: 600;
  color: var(--fg);
  font-variant-numeric: tabular-nums;
}

.ov-balance__rows {
  margin: 0;
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: var(--sp-1) var(--sp-4);
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

/* ---------- 两列卡片 ---------- */
.ov-grid {
  display: grid;
  grid-template-columns: minmax(0, 3fr) minmax(0, 2fr);
  gap: var(--sp-4);
  align-items: stretch;
  min-width: 0;
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
  color: var(--fg-muted);
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
  gap: var(--sp-6);
  flex-wrap: wrap;
}

.ov-cache__body {
  display: grid;
  gap: var(--sp-3);
  flex: 1;
  min-width: 220px;
}

@media (max-width: 1023px) {
  .ov-hero,
  .ov-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (max-width: 640px) {
  .ov-status {
    padding: var(--sp-3) var(--sp-4);
  }

  .ov-status__right {
    margin-left: 0;
    width: 100%;
  }

  .ov-cache {
    gap: var(--sp-4);
  }
}
</style>
