<script setup lang="ts">
import TitleTip from '@/components/base/TitleTip.vue'

/**
 * S13 模型广场 /market（模板 A：页头 + 筛选 + 卡片/列表双视图）
 *
 * 契约：docs/ui-v2/contracts/model-market.md
 * 设计稿：docs/ui-v2/06-pages-d.md P15
 *
 * 页面只做摆放：所有字段换算、筛选、排序、分页、时段健康数据都在 api/market.ts 的
 * 纯函数里（可单测）。后端语义（归一模型 / available = 有定价且活跃账号 > 冷却账号）
 * 在 api 层的注释里写清，页面不重复判断。
 *
 * 约定：
 *   · 视图（卡片/列表）与时间窗口都存 localStorage；筛选/排序/分页同步路由 query；
 *   · 口径：**全网关**（/api/stats/model-market 不读 X-Platform），页头徽章写明；
 *     因此不再订阅顶栏平台切换（platform_scoping_review §3）；要看某个平台用页内「平台」筛选；
 *   · 四态齐全：首次加载骨架 7 张卡 / 刷新保留旧数据（右上角转圈）/ 空 / 错误；
 *   · 卡片拆到 ./Market/MarketCardItem.vue；健康状态格直接使用服务端的时段边界和状态。
 */
import {
  computed,
  h,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
  type VNodeChild,
} from 'vue'
import { useRouter } from 'vue-router'
import { NInput, NSelect } from 'naive-ui'
import { Copy, RefreshCw, ScrollText } from 'lucide-vue-next'
import PageHeader from '@/layout/PageHeader.vue'
import {
  Badge,
  Btn,
  Card,
  Copyable,
  DataTable,
  Empty,
  ErrorBlock,
  FilterBar,
  ModelIcon,
  Segmented,
  type FilterChip,
  type SegmentedOption,
  type SorterPayload,
  type TableColumn,
  type TableRow,
} from '@/components'
import { useTableQuery, useToast } from '@/composables'
import { FMT_EMPTY, fmtCompact, fmtCredit, fmtPct } from '@/utils/format'
import { errorMessage } from '@/utils/error'
import { ApiError, isAbortError } from '@/api/http'
import { copyText } from '@/platform/clipboard'
import { avgDurationText, durationState } from '@/api/stats'
import MarketCardItem from './Market/MarketCardItem.vue'
import MarketHealthTimeline from './Market/MarketHealthTimeline.vue'
import { useRevisit } from '@/pages/_shared/useRevisit'
import {
  MARKET_HOURS,
  filterMarketCards,
  getModelAggregate,
  listModelMarket,
  logLinkFor,
  marketHoursLabel,
  marketStats,
  normalizeHours,
  normalizeSort,
  paginateMarketCards,
  platformOptions,
  priceText,
  rateTone,
  readMarketHours,
  readMarketView,
  sortMarketCards,
  toMarketCards,
  writeMarketHours,
  writeMarketView,
  type MarketCard,
  type MarketView,
  type ModelMarketPayload,
} from '@/api/market'
import { groupTitle, useI18n } from '@/i18n'
import { groupOf } from '@/router/pages'

const { t } = useI18n()
/** 旧版面板副标题原样保留（收进 ⓘ）；语种跟随 useI18n。 */
const helpText = computed(() => t('page.market.help'))
const eyebrow = computed(() => groupTitle(groupOf('market').key))

const toast = useToast()
const router = useRouter()

const table = useTableQuery('market', {
  pageSize: 20,
  sortBy: 'requests',
  sortOrder: 'desc',
  filters: { q: '', platform: '', available: '', hours: '' },
})

/* ------------------------------------------------------------------ *
 * 视图 / 时间窗口偏好（localStorage）
 * ------------------------------------------------------------------ */

const view = ref<MarketView>(readMarketView())
const savedHours = ref(readMarketHours())

/** 时间窗口：路由 query 优先，其次 localStorage（契约 §5.5）。 */
const hours = computed(() => normalizeHours(table.state.filters.hours || savedHours.value))
const hoursLabel = computed(() => marketHoursLabel(hours.value, t))

const viewOptions = computed<Array<SegmentedOption<string>>>(() => [
  { label: t('page.market.view.cards'), value: 'cards' },
  { label: t('page.market.view.list'), value: 'list' },
])

const hoursOptions = computed<Array<SegmentedOption<string>>>(() =>
  MARKET_HOURS.map((value) => ({ label: marketHoursLabel(value, t), value: String(value) })),
)

function setView(value: string | number): void {
  const next: MarketView = value === 'list' ? 'list' : 'cards'
  if (next === view.value) return
  view.value = next
  writeMarketView(next)
}

function setHours(value: string | number): void {
  const next = normalizeHours(value)
  savedHours.value = next
  writeMarketHours(next)
  if (table.state.filters.hours === String(next)) return
  table.setFilter('hours', String(next))
}

/* ------------------------------------------------------------------ *
 * 取数
 * ------------------------------------------------------------------ */

const payload = ref<ModelMarketPayload | null>(null)
const aggregate = ref<Awaited<ReturnType<typeof getModelAggregate>> | null>(null)
const loading = ref(false)
const loadError = ref<{ message: string; detail: string } | null>(null)

let controller: AbortController | null = null

const cards = computed(() => toMarketCards(payload.value, aggregate.value, null, t))

const firstLoading = computed(() => loading.value && payload.value === null)
const hasFilters = computed(() =>
  Boolean(
    table.state.filters.q ||
      table.state.filters.platform ||
      table.state.filters.available,
  ),
)

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
    const [market, agg] = await Promise.all([
      listModelMarket(hours.value, current.signal),
      // 副接口失败不影响主流程（契约 §8.2）
      getModelAggregate(current.signal).catch(() => null),
    ])
    // r4：副接口吞掉 AbortError 后旧请求仍可能 resolve；只认最后一次请求（防切时间窗时旧数据回写）。
    if (controller !== current) return
    payload.value = market
    aggregate.value = agg
  } catch (error) {
    if (isAbortError(error) || controller !== current) return
    const detail = error instanceof ApiError && error.detail ? prettyDetail(error.detail) : ''
    const failure = t('page.market.failure', {
      action: t('page.market.action.load'),
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

/* ------------------------------------------------------------------ *
 * 筛选 / 排序 / 分页
 * ------------------------------------------------------------------ */

const filteredCards = computed(() => filterMarketCards(cards.value, table.state.filters))
const sortedCards = computed(() =>
  sortMarketCards(filteredCards.value, table.state.sortBy, table.state.sortOrder),
)
const pageData = computed(() =>
  paginateMarketCards(sortedCards.value, table.state.page, table.state.pageSize),
)
/** DataTable 的行类型是 Record<string, unknown>：卡片行直接交给它渲染（列定义里再取回）。 */
const tableRows = computed<TableRow[]>(() => pageData.value.items as unknown as TableRow[])
const stats = computed(() => marketStats(cards.value))
const filteredStats = computed(() => marketStats(filteredCards.value))

/** 搜索框是受控的本地副本：输入即时回显，300ms 后由 FilterBar 写进路由 query。 */
const search = ref(table.state.filters.q)
watch(
  () => table.state.filters.q,
  (value) => {
    search.value = value
  },
)

const platformFilterOptions = computed(() => [
  { label: t('page.market.filter.platformAll'), value: '' },
  ...platformOptions(cards.value).map((item) => ({ label: item.label, value: item.value })),
])

const availabilityOptions = computed(() => [
  { label: t('page.market.filter.onlyAvailable'), value: 'available' },
  { label: t('page.market.filter.allModels'), value: '' },
])

const SORT_VALUES = ['requests:desc', 'price:asc', 'tps:desc', 'success_rate:desc', 'name:asc']

const sortOptions = computed(() => [
  { label: t('page.market.sort.requests'), value: 'requests:desc' },
  { label: t('page.market.sort.price'), value: 'price:asc' },
  { label: t('page.market.sort.tps'), value: 'tps:desc' },
  { label: t('page.market.sort.rate'), value: 'success_rate:desc' },
  { label: t('page.market.sort.name'), value: 'name:asc' },
])

const sortValue = computed(() => {
  const key = normalizeSort(table.state.sortBy)
  const order = table.state.sortOrder || (key === 'price' || key === 'name' ? 'asc' : 'desc')
  if (key === 'requests' && order === 'desc') return 'requests:desc'
  return key + ':' + order
})

function onSortSelect(value: string): void {
  const [key, order] = String(value || '').split(':')
  table.setSort(normalizeSort(key), order === 'asc' ? 'asc' : 'desc')
}

const SORT_KEY_MAP: Record<string, string> = {
  requests: 'requests',
  price: 'price',
  tps: 'tps',
  success_rate: 'success_rate',
  name: 'name',
}

function sortOrderOf(stateKey: string): 'ascend' | 'descend' | false {
  if (normalizeSort(table.state.sortBy) !== stateKey) return false
  if (table.state.sortOrder === 'asc') return 'ascend'
  if (table.state.sortOrder === 'desc') return 'descend'
  return false
}

function onSorter(sorter: SorterPayload | null): void {
  if (!sorter || !sorter.order) {
    table.setSort('requests', 'desc')
    return
  }
  const stateKey = SORT_KEY_MAP[sorter.columnKey]
  if (!stateKey) return
  table.setSort(stateKey, sorter.order === 'ascend' ? 'asc' : 'desc')
}

const chips = computed<FilterChip[]>(() => {
  const filters = table.state.filters
  const list: FilterChip[] = []
  if (filters.q) list.push({ key: 'q', label: t('page.market.chip.search'), value: filters.q })
  if (filters.platform) {
    const hit = platformFilterOptions.value.find((item) => item.value === filters.platform)
    list.push({
      key: 'platform',
      label: t('page.market.chip.platform'),
      value: hit ? hit.label : filters.platform,
    })
  }
  if (filters.available === 'available') {
    list.push({
      key: 'available',
      label: t('page.market.filter.capability'),
      value: t('page.market.filter.onlyAvailableShort'),
    })
  }
  return list
})

const summary = computed(() => {
  const current = filteredStats.value
  if (!hasFilters.value) {
    return t('page.market.summary.all', {
      total: stats.value.total,
      available: stats.value.available,
      requests: fmtCompact(stats.value.requests),
    })
  }
  return t('page.market.summary.filtered', {
    shown: current.total,
    total: stats.value.total,
    available: current.available,
    requests: fmtCompact(current.requests),
  })
})

const emptyDescription = computed(() => {
  if (hasFilters.value) return t('page.market.empty.filtered')
  if (stats.value.total === 0) return t('page.market.empty.none')
  if (stats.value.available === 0) return t('page.market.empty.unavailable')
  return t('page.market.empty.plain')
})

const windowSummary = computed(() => {
  const data = payload.value
  if (!data) return ''
  const source =
    data.source === 'rollup' ? t('page.market.source.rollup') : t('page.market.source.raw')
  return t('page.market.window', {
    window: marketHoursLabel(data.window_hours, t),
    total: data.total,
    available: data.available_models,
    source,
  })
})

function onFilterChange(values: Record<string, unknown>): void {
  const next: Record<string, string> = {}
  for (const [name, value] of Object.entries(values)) next[name] = String(value ?? '')
  table.setFilters(next)
}

function applyFilter(name: string, value: string | number | null): void {
  table.setFilter(name, value === null || value === undefined ? '' : String(value))
}

function removeChip(key: string): void {
  if (key === 'q') search.value = ''
  table.setFilter(key, '')
}

function resetFilters(): void {
  search.value = ''
  table.setFilters({ q: '', platform: '', available: '' })
}

/* ------------------------------------------------------------------ *
 * 操作
 * ------------------------------------------------------------------ */

async function copyName(card: MarketCard): Promise<void> {
  const ok = await copyText(card.callName)
  if (ok) toast.success(t('page.market.copyDone', { name: card.callName }))
  else toast.error(t('page.market.copyFailed'))
}

function openLogs(card: MarketCard): void {
  const link = logLinkFor(card)
  void router.push(link)
}

/* ------------------------------------------------------------------ *
 * 列表视图列
 * ------------------------------------------------------------------ */

function renderModel(row: TableRow): VNodeChild {
  const card = row as unknown as MarketCard
  return h('div', { class: 'mkt-cell' }, [
    h(ModelIcon, { vendor: card.key, model: card.callName, size: 18 }),
    h('div', { class: 'mkt-cell__main' }, [
      h(TitleTip, { content: String((card.name) ?? '') }, { default: () => h('span', { class: 'mkt-cell__name' }, card.name) }),
      h(TitleTip, { content: String((card.platformLabels.join(' · ')) ?? '') }, { default: () => h(
        'span',
        { class: 'mkt-cell__sub' },
        card.platformLabels.join(' · ') || t('page.market.card.noPlatform'),
      ) }),
    ]),
  ])
}

function renderMoney(row: TableRow): VNodeChild {
  const card = row as unknown as MarketCard
  return h(
    'span',
    { class: card.multiplier === null && !card.metered && !card.free ? 'mkt-dash' : 'mkt-num' },
    priceText(card, t),
  )
}

function renderRate(row: TableRow): VNodeChild {
  const card = row as unknown as MarketCard
  return h(
    'span',
    { class: 'mkt-num is-' + rateTone(card.successRate) },
    fmtPct(card.successRate),
  )
}

function renderNum(text: string, muted = false): VNodeChild {
  return h('span', { class: muted ? 'mkt-num mkt-dash' : 'mkt-num' }, text)
}

/** 平均耗时单元格：无样本 —；> 1 小时「数据异常」（avgDurationText 的兜底）。 */
function renderDuration(ms: number): VNodeChild {
  const state = durationState(ms)
  return h(
    'span',
    { class: state === 'none' ? 'mkt-num mkt-dash' : state === 'anomaly' ? 'mkt-num is-warn' : 'mkt-num' },
    avgDurationText(ms, t),
  )
}

function renderActions(row: TableRow): VNodeChild {
  const card = row as unknown as MarketCard
  return h('div', { class: 'mkt-actions' }, [
    h(
      Btn,
      {
        variant: 'quiet',
        size: 'sm',
        icon: Copy,
        onClick: () => void copyName(card),
      },
      { default: () => t('page.market.action.copyShort') },
    ),
    h(
      Btn,
      {
        variant: 'quiet',
        size: 'sm',
        icon: ScrollText,
        onClick: () => openLogs(card),
      },
      { default: () => t('page.market.action.logsShort') },
    ),
  ])
}

const columns = computed<TableColumn[]>(() => [
  {
    key: 'name',
    title: t('page.market.col.model'),
    mobile: 'title',
    minWidth: 220,
    sortable: true,
    sortOrder: sortOrderOf('name'),
    render: renderModel,
  },
  {
    key: 'price',
    title: t('page.market.col.price'),
    mobile: 'meta',
    width: 96,
    numeric: true,
    sortable: true,
    sortOrder: sortOrderOf('price'),
    render: renderMoney,
  },
  {
    key: 'requests',
    title: t('page.market.col.requests'),
    mobile: 'meta',
    width: 96,
    numeric: true,
    sortable: true,
    sortOrder: sortOrderOf('requests'),
    render: (row: TableRow) => renderNum(fmtCompact((row as unknown as MarketCard).requests)),
  },
  {
    key: 'success_rate',
    title: t('page.market.col.rate'),
    mobile: 'meta',
    width: 96,
    numeric: true,
    sortable: true,
    sortOrder: sortOrderOf('success_rate'),
    render: renderRate,
  },
  {
    key: 'ttfb',
    title: 'TTFT',
    mobile: 'meta',
    width: 88,
    numeric: true,
    render: (row: TableRow) => renderDuration((row as unknown as MarketCard).ttfbMs),
  },
  {
    key: 'latency',
    title: t('page.market.col.latency'),
    mobile: 'hidden',
    width: 88,
    numeric: true,
    render: (row: TableRow) => renderDuration((row as unknown as MarketCard).latencyMs),
  },
  {
    key: 'tps',
    title: t('page.market.col.tps'),
    mobile: 'hidden',
    width: 96,
    numeric: true,
    sortable: true,
    sortOrder: sortOrderOf('tps'),
    render: (row: TableRow) => {
      const card = row as unknown as MarketCard
      return renderNum(card.tps ? card.tps.toFixed(1) + ' t/s' : FMT_EMPTY, !card.tps)
    },
  },
  {
    key: 'health',
    title: t('page.market.health.title'),
    mobile: 'hidden',
    width: 140,
    render: (row: TableRow) => {
      const card = row as unknown as MarketCard
      return h(MarketHealthTimeline, { health: card.health, hoursLabel: hoursLabel.value, mini: true })
    },
  },
  {
    key: 'credits',
    title: t('page.market.col.credits'),
    mobile: 'meta',
    width: 96,
    numeric: true,
    render: (row: TableRow) => renderNum(fmtCredit((row as unknown as MarketCard).credits)),
  },
  {
    key: 'status',
    title: t('page.market.col.status'),
    mobile: 'meta',
    width: 104,
    render: (row: TableRow) => {
      const card = row as unknown as MarketCard
      return h(
        Badge,
        { tone: card.available ? 'ok' : 'neutral', dot: true },
        {
          default: () =>
            card.available
              ? t('page.market.state.available')
              : t('page.market.state.unavailable'),
        },
      )
    },
  },
  {
    key: 'callName',
    title: t('page.market.col.callName'),
    mobile: 'subtitle',
    minWidth: 180,
    render: (row: TableRow) =>
      h(Copyable, { value: (row as unknown as MarketCard).callName, muted: true }),
  },
  {
    key: 'actions',
    title: t('page.market.col.actions'),
    mobile: 'actions',
    width: 132,
    hideable: false,
    fixed: 'right',
    render: renderActions,
  },
])

/* ------------------------------------------------------------------ *
 * 生命周期
 * ------------------------------------------------------------------ */

onMounted(() => {
  void load()
})

onBeforeUnmount(() => {
  controller?.abort()
  controller = null
})

// 回到 KeepAlive 缓存页：保留旧卡片静默刷新，不出骨架（11 §2）。
/**
 * 第 5 轮：二次进入（KeepAlive）/ 后台轮询属于「静默刷新」——DataTable 用 `refreshing`
 * 保留旧数据、只在工具栏转小圈，不出顶部进度条。用计数，重叠的静默刷新互不打断。
 */
const silentDepth = ref(0)
const silentRefresh = computed(() => silentDepth.value > 0)
async function silently(task: () => Promise<unknown> | void): Promise<void> {
  silentDepth.value += 1
  try {
    await task()
  } finally {
    silentDepth.value -= 1
  }
}

useRevisit(() => silently(() => load()))

// 切时间窗口 = 唯一需要重新请求的场景（契约 §9.2）。
watch(hours, () => {
  void load()
})
</script>

<template>
  <div class="market-page">
    <PageHeader :help="helpText" :eyebrow="eyebrow">
      <template #meta>
        <Badge tone="neutral">{{ t('page.market.scope') }}</Badge>
      </template>
      <template #actions>
        <Segmented
          :options="viewOptions"
          :model-value="view"
          :aria-label="t('page.market.viewAria')"
          @update:model-value="setView"
        />
        <Segmented
          :options="hoursOptions"
          :model-value="String(hours)"
          :aria-label="t('page.market.hoursAria')"
          @update:model-value="setHours"
        />
        <Btn variant="quiet" :icon="RefreshCw" :loading="loading" @click="load()">
          {{ t('page.market.refresh') }}
        </Btn>
      </template>
    </PageHeader>

    <Card :loading="loading && cards.length > 0">
      <div class="market-filter">
        <FilterBar
          :chips="chips"
          :summary="summary"
          :loading="loading"
          @change="onFilterChange"
          @reset="resetFilters"
          @remove="removeChip"
        >
          <template #default="{ change }">
            <n-input
              class="market-filter__search"
              :value="search"
              :placeholder="t('page.market.searchPlaceholder')"
              clearable
              @update:value="
                (value: string) => {
                  search = value
                  change('q', value)
                }
              "
            />
            <n-select
              class="market-filter__select"
              :value="table.state.filters.platform"
              :options="platformFilterOptions"
              @update:value="(value: string | null) => applyFilter('platform', value)"
            />
            <n-select
              class="market-filter__select"
              :value="table.state.filters.available"
              :options="availabilityOptions"
              @update:value="(value: string | null) => applyFilter('available', value)"
            />
            <n-select
              class="market-filter__select"
              :value="sortValue"
              :options="sortOptions"
              :aria-label="t('page.market.sortAria')"
              @update:value="onSortSelect"
            />
          </template>
        </FilterBar>
      </div>

      <div class="market-body">
        <ErrorBlock
          v-if="loadError && payload === null"
          :title="loadError.message"
          :detail="loadError.detail"
          @retry="load()"
        />

        <div
          v-else-if="firstLoading"
          class="market-skeleton"
          aria-busy="true"
          :aria-label="t('page.market.loadingAria')"
        >
          <div v-for="index in 8" :key="index" class="mh-skeleton market-skeleton__card" />
        </div>

        <template v-else-if="view === 'cards'">
          <div v-if="pageData.items.length" class="market-grid">
            <MarketCardItem
              v-for="card in pageData.items"
              :key="card.key"
              :card="card"
              :hours-label="hoursLabel"
              @copy="copyName"
              @logs="openLogs"
            />
          </div>

          <Empty v-else :description="emptyDescription">
            <Btn v-if="hasFilters" variant="default" size="sm" @click="resetFilters">
              {{ t('page.market.clearFilters') }}
            </Btn>
            <Btn v-else variant="quiet" size="sm" :icon="RefreshCw" @click="load()">
              {{ t('page.market.refresh') }}
            </Btn>
          </Empty>

          <div v-if="pageData.pageCount > 1" class="market-pager">
            <Btn
              variant="quiet"
              size="sm"
              :disabled="pageData.page <= 1"
              @click="table.setPage(pageData.page - 1)"
            >
              {{ t('page.market.pager.prev') }}
            </Btn>
            <span class="market-pager__text tnum">
              {{
                t('page.market.pager', {
                  page: pageData.page,
                  count: pageData.pageCount,
                  total: pageData.total,
                })
              }}
            </span>
            <Btn
              variant="quiet"
              size="sm"
              :disabled="pageData.page >= pageData.pageCount"
              @click="table.setPage(pageData.page + 1)"
            >
              {{ t('page.market.pager.next') }}
            </Btn>
          </div>
        </template>

        <DataTable
          v-else
          table-id="market"
          :columns="columns"
          :data="tableRows"
          row-key="key"
          remote
          :loading="loading && !silentRefresh"
          :refreshing="loading && silentRefresh"
          :page="table.state.page"
          :page-size="table.state.pageSize"
          :item-count="pageData.total"
          :page-sizes="[20, 50, 100]"
          :empty-description="emptyDescription"
          @update:page="table.setPage"
          @update:page-size="table.setPageSize"
          @update:sorter="onSorter"
        >
          <template #empty>
            <Empty :description="emptyDescription">
              <Btn v-if="hasFilters" variant="default" size="sm" @click="resetFilters">
                {{ t('page.market.clearFilters') }}
              </Btn>
              <Btn v-else variant="quiet" size="sm" :icon="RefreshCw" @click="load()">
                {{ t('page.market.refresh') }}
              </Btn>
            </Empty>
          </template>
        </DataTable>

        <p v-if="windowSummary" class="market-window">
          {{ windowSummary }}
        </p>
      </div>
    </Card>
  </div>
</template>

<style scoped>
.market-page {
  display: grid;
  gap: var(--sp-4);
  min-width: 0;
}

.market-filter {
  padding-bottom: var(--sp-3);
  border-bottom: 1px solid var(--border-soft);
}

.market-filter__search {
  width: 240px;
  max-width: 100%;
}

.market-filter__select {
  width: 160px;
}

.market-body {
  display: grid;
  gap: var(--sp-3);
  padding-top: var(--sp-4);
  min-width: 0;
}

.market-grid {
  display: grid;
  gap: var(--sp-3);
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  /* 同一行卡片等高、底部操作条对齐（卡片内 footer margin-top:auto） */
  align-items: stretch;
}

.market-dash,
:deep(.mkt-dash) {
  color: var(--fg-faint);
}

:deep(.mkt-num) {
  font-variant-numeric: tabular-nums;
}

:deep(.mkt-num.is-ok) {
  color: var(--ok);
}

:deep(.mkt-num.is-warn) {
  color: var(--warn);
}

:deep(.mkt-num.is-danger) {
  color: var(--danger);
}

:deep(.mkt-num.is-neutral) {
  color: var(--fg-faint);
}

.is-ok {
  color: var(--ok);
}

.is-warn {
  color: var(--warn);
}

.is-danger {
  color: var(--danger);
}

.is-neutral {
  color: var(--fg-faint);
}

:deep(.mkt-cell) {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  min-width: 0;
}

:deep(.mkt-cell__main) {
  display: grid;
  min-width: 0;
}

:deep(.mkt-cell__name) {
  font-weight: 500;
  color: var(--fg);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

:deep(.mkt-cell__sub) {
  font-size: var(--fs-small);
  color: var(--fg-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

:deep(.mkt-actions) {
  display: flex;
  align-items: center;
  gap: var(--sp-1);
  justify-content: flex-end;
}

.market-skeleton {
  display: grid;
  gap: var(--sp-3);
  /* 与 .market-grid 同列宽，数据到来不重排 */
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
}

/* ≈ MarketCardItem 的实际高度 */
.market-skeleton__card {
  min-height: 336px;
  border-radius: var(--r-card);
}

.market-pager {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--sp-3);
}

.market-pager__text {
  font-size: var(--fs-small);
  color: var(--fg-muted);
}

.market-window {
  margin: 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.market-window__hint {
  color: var(--fg-faint);
}

@media (max-width: 640px) {
  .market-filter__search,
  .market-filter__select {
    width: 100%;
  }

  .market-grid,
  .market-skeleton {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
