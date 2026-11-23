<script setup lang="ts">
/**
 * S13 模型广场 /market（模板 A：页头 + 筛选 + 卡片/列表双视图）
 *
 * 契约：docs/ui-v2/contracts/model-market.md
 * 设计稿：docs/ui-v2/06-pages-d.md P15
 *
 * 页面只做摆放：所有字段换算、筛选、排序、分页、sparkline 分桶都在 api/market.ts 的
 * 纯函数里（可单测）。后端语义（归一模型 / available = 有定价且活跃账号 > 冷却账号）
 * 在 api 层的注释里写清，页面不重复判断。
 *
 * 约定：
 *   · 视图（卡片/列表）与时间窗口都存 localStorage；筛选/排序/分页同步路由 query；
 *   · 依赖平台上下文：订阅 usePlatformStore().onChange，平台真正变化时重新取数，卸载取消；
 *   · 四态齐全：首次加载骨架 7 张卡 / 刷新保留旧数据（右上角转圈）/ 空 / 错误；
 *   · sparkline 用内联 SVG 自绘（不新增依赖）。
 */
import {
  computed,
  defineComponent,
  h,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
  type PropType,
  type VNodeChild,
} from 'vue'
import { useRouter } from 'vue-router'
import { NInput, NSelect, NSkeleton } from 'naive-ui'
import { Copy, RefreshCw, ScrollText } from 'lucide-vue-next'
import PageHeader from '@/layout/PageHeader.vue'
import {
  Badge,
  Btn,
  Card,
  Chip,
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
import { usePlatformStore } from '@/stores/platform'
import { FMT_EMPTY, fmtCompact, fmtCredit, fmtDuration, fmtPct } from '@/utils/format'
import { failureText } from '@/utils/error'
import { ApiError, isAbortError } from '@/api/http'
import { copyText } from '@/platform/clipboard'
import {
  MARKET_HOURS,
  MARKET_HOURS_LABEL,
  filterMarketCards,
  getMarketTrend,
  getModelAggregate,
  indexTrendSeries,
  listModelMarket,
  logLinkFor,
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

/** 旧版面板副标题原样保留（收进 ⓘ）。 */
const HELP_TEXT =
  '按模型看现在能提供什么：价格、近窗口用量、成功率与延迟、可用账号数。' +
  '可用性 = 活跃账号 − 该模型冷却中的账号；没有定价的模型不计为可用。' +
  '数据来自小时汇总表，后端缓存 30 秒。'

const toast = useToast()
const router = useRouter()
const platformStore = usePlatformStore()

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
const hoursLabel = computed(() => MARKET_HOURS_LABEL[hours.value] || hours.value + ' 小时')

const viewOptions: Array<SegmentedOption<string>> = [
  { label: '卡片', value: 'cards' },
  { label: '列表', value: 'list' },
]

const hoursOptions: Array<SegmentedOption<string>> = MARKET_HOURS.map((value) => ({
  label: MARKET_HOURS_LABEL[value],
  value: String(value),
}))

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
const trend = ref<Awaited<ReturnType<typeof getMarketTrend>>['trend'] | null>(null)
const loading = ref(false)
const loadError = ref<{ message: string; detail: string } | null>(null)
const trendMissing = ref(false)

let controller: AbortController | null = null

const cards = computed(() => toMarketCards(payload.value, aggregate.value, trend.value))

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
    const [market, agg, dash] = await Promise.all([
      listModelMarket(hours.value, current.signal),
      // 副接口失败不影响主流程（契约 §8.2）
      getModelAggregate(current.signal).catch(() => null),
      // sparkline 只是锦上添花：拿不到就不画，不弹错
      getMarketTrend(hours.value, current.signal).catch(() => null),
    ])
    payload.value = market
    aggregate.value = agg
    trend.value = dash?.trend ?? null
    trendMissing.value = !indexTrendSeries(trend.value).size
  } catch (error) {
    if (isAbortError(error)) return
    const detail = error instanceof ApiError && error.detail ? prettyDetail(error.detail) : ''
    loadError.value = { message: failureText('加载模型广场', error), detail }
    toast.error(failureText('加载模型广场', error))
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
  { label: '平台：全部', value: '' },
  ...platformOptions(cards.value).map((item) => ({ label: item.label, value: item.value })),
])

const availabilityOptions = [
  { label: '只看可用', value: 'available' },
  { label: '全部模型', value: '' },
]

const SORT_VALUES = ['requests:desc', 'price:asc', 'tps:desc', 'success_rate:desc', 'name:asc']

const sortOptions = [
  { label: '按调用量', value: 'requests:desc' },
  { label: '按价格', value: 'price:asc' },
  { label: '按速度', value: 'tps:desc' },
  { label: '按成功率', value: 'success_rate:desc' },
  { label: '按名称', value: 'name:asc' },
]

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
  if (filters.q) list.push({ key: 'q', label: '搜索', value: filters.q })
  if (filters.platform) {
    const hit = platformFilterOptions.value.find((item) => item.value === filters.platform)
    list.push({ key: 'platform', label: '平台', value: hit ? hit.label : filters.platform })
  }
  if (filters.available === 'available') list.push({ key: 'available', label: '能力', value: '仅可用' })
  return list
})

const summary = computed(() => {
  const current = filteredStats.value
  if (!hasFilters.value) {
    return '共 ' + stats.value.total + ' 个模型 · 可用 ' + stats.value.available + ' · 请求 ' +
      fmtCompact(stats.value.requests)
  }
  return '筛选出 ' + current.total + ' / 共 ' + stats.value.total + ' 个模型 · 可用 ' +
    current.available + ' · 请求 ' + fmtCompact(current.requests)
})

const emptyDescription = computed(() => {
  if (hasFilters.value) return '没有符合条件的模型。清空筛选后可看到全部模型。'
  if (stats.value.total === 0) return '模型广场为空，请先添加模型配置。'
  if (stats.value.available === 0) return '所有模型当前都不可用。'
  return '没有符合条件的模型。'
})

const windowSummary = computed(() => {
  const data = payload.value
  if (!data) return ''
  const source = data.source === 'rollup' ? '小时汇总' : '原始日志'
  return '窗口 ' + (MARKET_HOURS_LABEL[data.window_hours] || data.window_hours + ' 小时') +
    ' · 共 ' + data.total + ' 个模型 · 可用 ' + data.available_models + ' · 数据源 ' + source
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
  if (ok) toast.success('已复制 ' + card.callName)
  else toast.error('复制失败：浏览器拒绝了剪贴板访问，请手动选中复制')
}

function openLogs(card: MarketCard): void {
  const link = logLinkFor(card)
  void router.push(link)
}

/* ------------------------------------------------------------------ *
 * sparkline（内联 SVG，不新增依赖）
 * ------------------------------------------------------------------ */

const Sparkline = defineComponent({
  name: 'MarketSparkline',
  props: {
    values: { type: Array as PropType<number[]>, default: () => [] },
    label: { type: String, default: '近窗口请求趋势' },
  },
  setup(props) {
    const WIDTH = 120
    const HEIGHT = 28
    return (): VNodeChild => {
      if (!props.values.length) {
        return h('span', { class: 'mkt-dash', title: '没有分时数据' }, FMT_EMPTY)
      }
      const barWidth = WIDTH / props.values.length
      return h(
        'svg',
        {
          class: 'mkt-spark',
          viewBox: '0 0 ' + WIDTH + ' ' + HEIGHT,
          preserveAspectRatio: 'none',
          role: 'img',
          'aria-label': props.label,
        },
        props.values.map((ratio, index) =>
          h('rect', {
            key: index,
            x: (index * barWidth + barWidth * 0.15).toFixed(2),
            y: (HEIGHT - Math.max(ratio, 0.04) * HEIGHT).toFixed(2),
            width: Math.max(barWidth * 0.7, 0.6).toFixed(2),
            height: (Math.max(ratio, 0.04) * HEIGHT).toFixed(2),
            rx: 1,
          }),
        ),
      )
    }
  },
})

function sparkOf(card: MarketCard): string {
  return card.sparkTotal === null
    ? '没有分时数据'
    : '近窗口 ' + fmtCompact(card.sparkTotal) + ' 次请求（分桶）'
}

/* ------------------------------------------------------------------ *
 * 列表视图列
 * ------------------------------------------------------------------ */

function renderModel(row: TableRow): VNodeChild {
  const card = row as unknown as MarketCard
  return h('div', { class: 'mkt-cell' }, [
    h(ModelIcon, { vendor: card.key, model: card.callName, size: 18 }),
    h('div', { class: 'mkt-cell__main' }, [
      h('span', { class: 'mkt-cell__name', title: card.name }, card.name),
      h(
        'span',
        { class: 'mkt-cell__sub', title: card.platformLabels.join(' · ') },
        card.platformLabels.join(' · ') || '暂无平台',
      ),
    ]),
  ])
}

function renderMoney(row: TableRow): VNodeChild {
  const card = row as unknown as MarketCard
  return h('span', { class: card.multiplier === null ? 'mkt-dash' : 'mkt-num' }, priceText(card))
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
      { default: () => '复制' },
    ),
    h(
      Btn,
      {
        variant: 'quiet',
        size: 'sm',
        icon: ScrollText,
        onClick: () => openLogs(card),
      },
      { default: () => '日志' },
    ),
  ])
}

const columns = computed<TableColumn[]>(() => [
  {
    key: 'name',
    title: '模型',
    mobile: 'title',
    minWidth: 220,
    sortable: true,
    sortOrder: sortOrderOf('name'),
    render: renderModel,
  },
  {
    key: 'price',
    title: '倍率',
    mobile: 'meta',
    width: 96,
    numeric: true,
    sortable: true,
    sortOrder: sortOrderOf('price'),
    render: renderMoney,
  },
  {
    key: 'requests',
    title: '请求',
    mobile: 'meta',
    width: 96,
    numeric: true,
    sortable: true,
    sortOrder: sortOrderOf('requests'),
    render: (row: TableRow) => renderNum(fmtCompact((row as unknown as MarketCard).requests)),
  },
  {
    key: 'success_rate',
    title: '成功率',
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
    render: (row: TableRow) =>
      renderNum(
        fmtDuration((row as unknown as MarketCard).ttfbMs),
        (row as unknown as MarketCard).ttfbMs <= 0,
      ),
  },
  {
    key: 'latency',
    title: '延迟',
    mobile: 'hidden',
    width: 88,
    numeric: true,
    render: (row: TableRow) => {
      const card = row as unknown as MarketCard
      return renderNum(fmtDuration(card.latencyMs), card.latencyMs <= 0)
    },
  },
  {
    key: 'tps',
    title: '吞吐',
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
    key: 'spark',
    title: '近窗口趋势',
    mobile: 'hidden',
    width: 140,
    render: (row: TableRow) => h(Sparkline, { values: (row as unknown as MarketCard).spark }),
  },
  {
    key: 'credits',
    title: '积分',
    mobile: 'meta',
    width: 96,
    numeric: true,
    render: (row: TableRow) => renderNum(fmtCredit((row as unknown as MarketCard).credits)),
  },
  {
    key: 'status',
    title: '状态',
    mobile: 'meta',
    width: 104,
    render: (row: TableRow) => {
      const card = row as unknown as MarketCard
      return h(
        Badge,
        { tone: card.available ? 'ok' : 'danger' },
        { default: () => (card.available ? '可用' : '不可用') },
      )
    },
  },
  {
    key: 'callName',
    title: '调用名',
    mobile: 'subtitle',
    minWidth: 180,
    render: (row: TableRow) =>
      h(Copyable, { value: (row as unknown as MarketCard).callName, muted: true }),
  },
  {
    key: 'actions',
    title: '操作',
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

let offPlatform: (() => void) | null = null
let lastSelector = platformStore.selector

onMounted(() => {
  lastSelector = platformStore.selector
  void load()
  offPlatform = platformStore.onChange((detail) => {
    // 首屏 store 加载后也会 emit 一次：选择没变就别重复打接口。
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

// 切时间窗口 = 唯一需要重新请求的场景（契约 §9.2）。
watch(hours, () => {
  void load()
})
</script>

<template>
  <div class="market-page">
    <PageHeader title="模型广场" :help="HELP_TEXT">
      <template #actions>
        <Segmented
          :options="viewOptions"
          :model-value="view"
          aria-label="视图切换"
          @update:model-value="setView"
        />
        <Segmented
          :options="hoursOptions"
          :model-value="String(hours)"
          aria-label="时间窗口"
          @update:model-value="setHours"
        />
        <Btn variant="quiet" :icon="RefreshCw" :loading="loading" @click="load()">刷新</Btn>
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
              placeholder="搜索模型名 / 调用名"
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
              aria-label="排序"
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

        <div v-else-if="firstLoading" class="market-skeleton" aria-busy="true" aria-label="模型广场加载中">
          <div v-for="index in 7" :key="index" class="market-skeleton__card">
            <n-skeleton text :repeat="4" />
          </div>
        </div>

        <template v-else-if="view === 'cards'">
          <div v-if="pageData.items.length" class="market-grid">
            <article
              v-for="card in pageData.items"
              :key="card.key"
              class="market-card"
              :class="{ 'is-off': !card.available }"
            >
              <header class="market-card__head">
                <ModelIcon :vendor="card.key" :model="card.callName" :size="20" />
                <div class="market-card__title">
                  <h3 :title="card.name">{{ card.name }}</h3>
                  <div class="market-card__chips">
                    <Chip
                      v-for="label in card.platformLabels.slice(0, 3)"
                      :key="label"
                      :title="label"
                    >
                      {{ label }}
                    </Chip>
                    <Chip
                      v-if="card.platformLabels.length > 3"
                      :title="card.platformLabels.slice(3).join('、')"
                    >
                      +{{ card.platformLabels.length - 3 }}
                    </Chip>
                    <span v-if="!card.platformCount" class="market-dash">暂无平台</span>
                  </div>
                </div>
                <Badge :tone="card.available ? 'ok' : 'danger'">
                  {{ card.available ? '可用' : '不可用' }}
                </Badge>
              </header>

              <div class="market-card__price">
                <b>{{ priceText(card) }}</b>
                <span>{{ card.free ? '全部平台免费' : '每千 Token 倍率' }}</span>
              </div>

              <dl class="market-card__stats">
                <div>
                  <dt>平台数</dt>
                  <dd class="tnum">{{ card.platformCount }}</dd>
                </div>
                <div>
                  <dt>成功率</dt>
                  <dd class="tnum" :class="'is-' + rateTone(card.successRate)">
                    {{ fmtPct(card.successRate) }}
                  </dd>
                </div>
                <div>
                  <dt>TTFT</dt>
                  <dd class="tnum">{{ fmtDuration(card.ttfbMs) }}</dd>
                </div>
              </dl>

              <div class="market-card__spark">
                <span class="market-card__spark-label">
                  近 {{ hoursLabel }} {{ fmtCompact(card.requests) }} 次
                </span>
                <Sparkline :values="card.spark" :label="sparkOf(card)" />
              </div>

              <footer class="market-card__foot">
                <Btn variant="quiet" size="sm" :icon="Copy" @click="copyName(card)">
                  复制模型名
                </Btn>
                <Btn variant="quiet" size="sm" :icon="ScrollText" @click="openLogs(card)">
                  查看日志
                </Btn>
                <code class="market-card__call" :title="card.callName">{{ card.callName }}</code>
              </footer>
            </article>
          </div>

          <Empty v-else :description="emptyDescription">
            <Btn v-if="hasFilters" variant="default" size="sm" @click="resetFilters">
              清除筛选
            </Btn>
            <Btn v-else variant="quiet" size="sm" :icon="RefreshCw" @click="load()">刷新</Btn>
          </Empty>

          <div v-if="pageData.pageCount > 1" class="market-pager">
            <Btn
              variant="quiet"
              size="sm"
              :disabled="pageData.page <= 1"
              @click="table.setPage(pageData.page - 1)"
            >
              上一页
            </Btn>
            <span class="market-pager__text tnum">
              第 {{ pageData.page }} / {{ pageData.pageCount }} 页 · 共
              {{ pageData.total }} 个模型
            </span>
            <Btn
              variant="quiet"
              size="sm"
              :disabled="pageData.page >= pageData.pageCount"
              @click="table.setPage(pageData.page + 1)"
            >
              下一页
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
          :loading="loading"
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
                清除筛选
              </Btn>
              <Btn v-else variant="quiet" size="sm" :icon="RefreshCw" @click="load()">刷新</Btn>
            </Empty>
          </template>
        </DataTable>

        <p v-if="windowSummary" class="market-window">
          {{ windowSummary }}
          <span v-if="trendMissing" class="market-window__hint">
            · 分时趋势只覆盖 Top 模型，其余不画 sparkline
          </span>
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
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
}

.market-card {
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
  padding: var(--sp-4);
  min-width: 0;
  border: 1px solid var(--border);
  border-radius: var(--r-card);
  background: var(--surface);
}

.market-card.is-off {
  opacity: 0.55;
}

.market-card__head {
  display: flex;
  align-items: flex-start;
  gap: var(--sp-2);
}

.market-card__title {
  flex: 1;
  min-width: 0;
}

.market-card__title h3 {
  margin: 0;
  font-size: var(--fs-h2);
  line-height: var(--lh-h2);
  font-weight: 600;
  color: var(--fg);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.market-card__chips {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--sp-1);
  margin-top: var(--sp-1);
}

.market-card__price {
  display: flex;
  align-items: baseline;
  gap: var(--sp-2);
}

.market-card__price b {
  font-family: var(--font-mono);
  font-size: 18px;
  font-weight: 600;
  color: var(--fg);
}

.market-card__price span {
  font-size: var(--fs-small);
  color: var(--fg-muted);
}

.market-card__stats {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--sp-2);
  margin: 0;
  padding-top: var(--sp-3);
  border-top: 1px solid var(--border-soft);
}

.market-card__stats dt {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.market-card__stats dd {
  margin: 2px 0 0;
  font-size: var(--fs-body);
  font-weight: 600;
  color: var(--fg);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.market-card__spark {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  min-width: 0;
}

.market-card__spark-label {
  font-size: var(--fs-small);
  color: var(--fg-muted);
  white-space: nowrap;
}

.market-spark,
:deep(.mkt-spark) {
  display: block;
  flex: 1;
  min-width: 60px;
  height: 28px;
  color: var(--brand);
  fill: currentColor;
}

.market-card__foot {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--sp-1);
  padding-top: var(--sp-3);
  border-top: 1px solid var(--border-soft);
}

.market-card__call,
:deep(.mkt-call) {
  margin-left: auto;
  max-width: 55%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--fg-faint);
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
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
}

.market-skeleton__card {
  display: grid;
  gap: var(--sp-2);
  padding: var(--sp-4);
  min-height: 180px;
  border: 1px solid var(--border-soft);
  border-radius: var(--r-card);
  background: var(--surface);
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

  .market-card__spark {
    flex-wrap: wrap;
  }
}
</style>
