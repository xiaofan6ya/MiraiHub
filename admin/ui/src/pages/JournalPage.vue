<script setup lang="ts">
import TitleTip from '@/components/base/TitleTip.vue'

/**
 * S22 积分流水 /journal（v2.1 视觉精修：KPI 行 + 一行工具栏的流水表）
 *
 * 契约：docs/ui-v2/contracts/journal.md。规范：docs/ui-v2/10-visual-refresh.md。
 * 数据：GET /api/maintenance/credit-events（admin/routers/maintenance.py:credit_events）。
 *
 * 三个后端事实（页面上如实标注）：
 *   · CreditEvent 没有平台列，平台归属由 X-Platform 作用域决定 → 平台跟随顶栏，不再在表里每行重复
 *     （第 2 轮审查 P1-6：已经按 WorkBuddy 筛了，平台列每行都是 WorkBuddy）；
 *   · 分页是 page/page_size 偏移，「快速翻页」只能不显示总数；
 *   · 没有 search 参数 → 搜索只过滤当前页，**不触发重新请求**（原来每敲一个字都会重拉一次）。
 *
 * 本轮修复：余额列按 delta 排的错误；账号下拉空值无占位；负数变动用红；
 * 「历史导入中，请刷新继续」改成内联提示条 + 「继续导入」按钮；骨架屏改细条。
 */
import { computed, h, onBeforeUnmount, onMounted, ref, watch, type VNodeChild } from 'vue'
import { NInput, NSelect, NSwitch } from 'naive-ui'
import { ArrowDownUp, CircleCheck, Download, Eye, Hash, Info, RefreshCw, RotateCcw } from 'lucide-vue-next'
import PageHeader from '@/layout/PageHeader.vue'
import {
  Badge,
  Btn,
  DataTable,
  DateRange,
  Empty,
  ErrorBlock,
  KpiCard,
  type TableColumn,
  type SorterPayload,
  type TableRow,
} from '@/components'
import { useTableQuery, useToast } from '@/composables'
import { errorMessage } from '@/utils/error'
import { FMT_EMPTY, fmtInt } from '@/utils/format'
import { usePlatformStore } from '@/stores/platform'
import { ApiError, isAbortError } from '@/api/http'
import {
  accountOptions,
  canGoNext,
  csvFilename,
  filterJournalRows,
  importingHint,
  kindOptions,
  listCreditEvents,
  pageState,
  rangeToQuery,
  saveCsv,
  sortJournalRows,
  sourceOptions,
  summarize,
  toCsv,
  toJournalRows,
  type CreditEventPayload,
  type JournalRow,
} from '@/api/journal'
import { useI18n } from '@/i18n'
import { useRevisit } from '@/pages/_shared/useRevisit'

const { t } = useI18n()
const helpText = computed(() => t('page.journal.help'))

const FAST_KEY = 'mh.journal.fast'

const toast = useToast()
const platform = usePlatformStore()
const table = useTableQuery('journal', {
  pageSize: 50,
  filters: { account: '', kind: '', source: '', q: '', start: '', end: '' },
})

const payload = ref<CreditEventPayload | null>(null)
const loading = ref(false)
const loadError = ref<{ message: string; detail: string } | null>(null)
const firstLoading = ref(true)
let controller: AbortController | null = null
let stopPlatformWatch: (() => void) | null = null
let lastSelector = ''

const fast = ref(false)

const platformLabel = computed(
  () => platform.currentSpec?.label || platform.currentId || t('page.journal.platformFallback'),
)

const rows = computed(() => toJournalRows(payload.value?.items, platformLabel.value, t))
const filteredRows = computed(() => filterJournalRows(rows.value, { q: table.state.filters.q }))
const sortedRows = computed<JournalRow[]>(() =>
  sortJournalRows(filteredRows.value, table.state.sortBy, table.state.sortOrder),
)

const page = computed(() => pageState(payload.value, table.state.pageSize))
const summary = computed(() => summarize(filteredRows.value, page.value.total, t))

/**
 * 首屏数据没回来之前不能报「共 0 条」：0 条（真的没有流水）和「还没取到」是两件事。
 * KPI 在 payload 为空时显示骨架；拿到响应后才出数字。
 */
const ready = computed(() => payload.value !== null)
const summaryText = computed(() => {
  if (ready.value) return summary.value.text
  return loading.value ? t('page.journal.loading') : ''
})
const importHint = computed(() => importingHint(payload.value, t))
const hasFilters = computed(() =>
  Boolean(
    (table.state.filters.account && table.state.filters.account !== '0') ||
      table.state.filters.kind ||
      table.state.filters.source ||
      table.state.filters.q ||
      table.state.filters.start ||
      table.state.filters.end,
  ),
)

const accountSelectOptions = computed(() =>
  accountOptions(payload.value?.accounts, t).map((item) => ({
    label: item.label,
    value: String(item.value),
  })),
)
/** 「全部账号」的值是 '0'；路由里没有 account 时也显示成它，而不是空白下拉（第 2 轮审查 P0-16）。 */
const accountValue = computed(() => table.state.filters.account || '0')
const sourceSelectOptions = computed(() =>
  sourceOptions(payload.value?.sources, t).map((item) => ({ label: item.label, value: item.value })),
)
const kindSelectOptions = computed(() => kindOptions(t))

/** 日期区间在路由里存 ISO 字符串，控件要毫秒时间戳。 */
const rangeValue = computed<[number, number] | null>(() => {
  const start = Date.parse(table.state.filters.start)
  const end = Date.parse(table.state.filters.end)
  if (!Number.isFinite(start) || !Number.isFinite(end)) return null
  return [start, end]
})

const emptyDescription = computed(() =>
  hasFilters.value ? t('page.journal.empty.filtered') : t('page.journal.empty.none'),
)

/** 页码文案：快速翻页时不写总页数（与旧版一致）。 */
const pageText = computed(() => {
  const head = t('page.journal.pager.indicator', { page: page.value.page })
  if (fast.value) return head + t('page.journal.pager.noTotal')
  return head + t('page.journal.pager.count', { count: page.value.pageCount })
})

const headerMeta = computed(() => {
  if (!ready.value) return platformLabel.value
  return [
    platformLabel.value,
    fast.value
      ? t('page.journal.meta.fast')
      : t('page.journal.meta.total', { count: fmtInt(page.value.total) }),
  ].join(' · ')
})

const search = ref(table.state.filters.q)
watch(
  () => table.state.filters.q,
  (value) => {
    search.value = value
  },
)

/* ------------------------------ 取数 ------------------------------ */
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
  const filters = table.state.filters
  const range = rangeToQuery(rangeValue.value)
  try {
    const next = await listCreditEvents({
      accountId: filters.account && filters.account !== '0' ? Number(filters.account) : null,
      kind: filters.kind,
      source: filters.source,
      start: range.start,
      end: range.end,
      page: table.state.page,
      pageSize: table.state.pageSize,
      signal: current.signal,
    })
    if (controller !== current) return
    payload.value = next
  } catch (error) {
    if (isAbortError(error) || controller !== current) return
    loadError.value = {
      message: errorMessage(error),
      detail: error instanceof ApiError && error.detail ? prettyDetail(error.detail) : '',
    }
    toast.error(
      t('page.journal.failure', {
        action: t(payload.value === null ? 'page.journal.action.load' : 'page.journal.action.refresh'),
        reason: errorMessage(error),
      }),
    )
  } finally {
    if (controller === current) {
      controller = null
      loading.value = false
      firstLoading.value = false
    }
  }
}

/*
 * 只有「服务端参数」变化才重新请求：分页、账号、类型、来源、时间。
 * 搜索 q 是当前页的客户端过滤，不触发请求。
 */
const serverKey = computed(() => {
  const f = table.state.filters
  return [table.state.page, table.state.pageSize, f.account, f.kind, f.source, f.start, f.end].join('|')
})
watch(serverKey, () => {
  void load()
})

/* ------------------------------ 交互 ------------------------------ */
function applyFilter(name: string, value: string | number | null): void {
  const text = value === null || value === undefined ? '' : String(value)
  table.setFilter(name, name === 'account' && text === '0' ? '' : text)
}

let searchTimer: ReturnType<typeof setTimeout> | null = null
function onSearch(value: string): void {
  search.value = value
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    searchTimer = null
    table.setFilter('q', value || '')
  }, 250)
}

function onRange(value: [number, number] | null): void {
  const range = rangeToQuery(value)
  table.setFilters({ start: range.start, end: range.end })
}

function resetFilters(): void {
  search.value = ''
  table.reset()
}

function goPage(next: number): void {
  const target = Math.max(1, Math.floor(next || 1))
  if (!fast.value) table.setPage(Math.min(target, page.value.pageCount))
  else table.setPage(target)
}

function toggleFast(value: boolean): void {
  fast.value = value
  try {
    localStorage.setItem(FAST_KEY, value ? '1' : '0')
  } catch {
    /* 隐私模式写不进去，不影响本次会话 */
  }
}

function onSorter(sorter: SorterPayload | null): void {
  const key = String(sorter?.columnKey || '')
  if (!sorter || !sorter.order || (key !== 'time' && key !== 'delta' && key !== 'balance')) {
    table.setSort('', '')
    return
  }
  table.setSort(key, sorter.order === 'ascend' ? 'asc' : 'desc')
}

function sortOrderOf(key: string): 'ascend' | 'descend' | false {
  if (table.state.sortBy !== key) return false
  if (table.state.sortOrder === 'asc') return 'ascend'
  return table.state.sortOrder === 'desc' ? 'descend' : false
}

function exportCsv(): void {
  if (!filteredRows.value.length) {
    toast.error(t('page.journal.exportEmpty'))
    return
  }
  try {
    saveCsv(toCsv(filteredRows.value, t), csvFilename())
    toast.success(t('page.journal.exportDone', { count: filteredRows.value.length }))
  } catch (error) {
    toast.error(
      t('page.journal.failure', {
        action: t('page.journal.action.export'),
        reason: errorMessage(error),
      }),
    )
  }
}

/* ------------------------------ 单元格 ------------------------------ */
function cellKind(row: TableRow): VNodeChild {
  const item = row as JournalRow
  return h(Badge, { tone: item.kindTone, dot: true }, { default: () => item.kindLabel })
}

function cellDelta(row: TableRow): VNodeChild {
  const item = row as JournalRow
  // 数值一律 sans + tabular-nums（第 2 轮审查 P1-8：同一张表不混用 mono 与 sans 数字）
  return h('span', { class: 'journal-delta tnum is-' + item.deltaTone }, item.deltaText)
}

function cellBalance(row: TableRow): VNodeChild {
  const item = row as JournalRow
  return h(TitleTip, { content: String((item.balanceRecorded ? '' : t('page.journal.balanceMissing')) ?? '') }, { default: () => h(
    'span',
    {
      class: item.balanceRecorded ? 'tnum' : 'journal-faint',
    },
    item.balanceRecorded ? item.balanceText : FMT_EMPTY,
  ) })
}

function cellAccount(row: TableRow): VNodeChild {
  const item = row as JournalRow
  return h('div', { class: 'journal-account' }, [
    h(TitleTip, { content: String((item.accountName) ?? '') }, { default: () => h('span', { class: 'journal-account__name' }, item.accountName || t('journal.row.account')) }),
    item.accountId ? h('span', { class: 'journal-account__id mono' }, '#' + item.accountId) : null,
  ])
}

const columns = computed<TableColumn[]>(() => [
  {
    key: 'time',
    title: t('page.journal.col.time'),
    mobile: 'subtitle',
    width: 150,
    sortable: true,
    sortOrder: sortOrderOf('time'),
    render: (row: TableRow) =>
      h(TitleTip, { content: String((String(row.fullTime || '')) ?? '') }, { default: () => h('span', { class: 'tnum journal-time' }, String(row.time || FMT_EMPTY)) }),
  },
  {
    key: 'accountText',
    title: t('page.journal.col.account'),
    mobile: 'title',
    minWidth: 200,
    render: cellAccount,
  },
  { key: 'kindLabel', title: t('page.journal.col.kind'), mobile: 'meta', width: 120, render: cellKind },
  {
    key: 'delta',
    title: t('page.journal.col.delta'),
    mobile: 'meta',
    width: 110,
    numeric: true,
    sortable: true,
    sortOrder: sortOrderOf('delta'),
    render: cellDelta,
  },
  {
    key: 'balance',
    title: t('page.journal.col.balance'),
    mobile: 'meta',
    width: 120,
    numeric: true,
    sortable: true,
    sortOrder: sortOrderOf('balance'),
    render: cellBalance,
  },
  {
    key: 'note',
    title: t('page.journal.col.note'),
    mobile: 'meta',
    minWidth: 220,
    render: (row: TableRow) => {
      const note = String(row.note || '')
      return note && note !== FMT_EMPTY
        ? h(TitleTip, { content: String((String(row.source || '')) ?? '') }, { default: () => h('span', { class: 'journal-note' }, note) })
        : h('span', { class: 'journal-faint' }, FMT_EMPTY)
    },
  },
])

/* ------------------------------ 生命周期 ------------------------------ */
onMounted(async () => {
  try {
    fast.value = localStorage.getItem(FAST_KEY) === '1'
  } catch {
    fast.value = false
  }
  lastSelector = platform.selector
  stopPlatformWatch = platform.onChange((detail) => {
    if (detail.selector === lastSelector) return
    lastSelector = detail.selector
    // 切平台：回到第 1 页（若已在第 1 页，serverKey 不变，手动重取）。
    if (table.state.page !== 1) table.setPage(1)
    else void load()
  })
  if (!platform.loaded) await platform.load()
  lastSelector = platform.selector
  void load()
})

/** 回到 KeepAlive 缓存页：静默刷新，不出骨架、不给表格盖遮罩（11 §2「二次进入不闪」）。 */
const silentRefresh = ref(false)
useRevisit(async () => {
  silentRefresh.value = true
  try {
    await load()
  } finally {
    silentRefresh.value = false
  }
})

onBeforeUnmount(() => {
  stopPlatformWatch?.()
  stopPlatformWatch = null
  controller?.abort()
  controller = null
  if (searchTimer) clearTimeout(searchTimer)
})
</script>

<template>
  <div class="journal-page">
    <PageHeader :help="helpText">
      <template #meta>
        <span class="journal-meta tnum">{{ headerMeta }}</span>
      </template>
      <template #actions>
        <Btn variant="quiet" :icon="RefreshCw" :loading="loading" @click="load()">
          {{ t('page.journal.refresh') }}
        </Btn>
        <Btn variant="default" :icon="Download" @click="exportCsv">
          {{ t('page.journal.export') }}
        </Btn>
      </template>
    </PageHeader>

    <ErrorBlock
      v-if="loadError && payload === null"
      :title="t('page.journal.errorTitle', { message: loadError.message })"
      :detail="loadError.detail"
      @retry="load()"
    />

    <template v-else>
      <!-- KPI 行：总数是全量 COUNT；净变化 / 笔数只算当前页（hint 写明口径） -->
      <section class="journal-kpis" :aria-label="t('page.journal.kpi.aria')">
        <KpiCard
          :label="t('page.journal.kpi.total')"
          :value="ready && !fast ? fmtInt(page.total) : null"
          :unit="t('page.journal.kpi.totalUnit')"
          :hint="fast ? t('page.journal.kpi.totalFast') : t('page.journal.kpi.totalHint')"
          :icon="Hash"
          tone="neutral"
          :loading="!ready"
        />
        <KpiCard
          :label="t('page.journal.kpi.net')"
          :value="ready ? summary.netText : null"
          :unit="t('page.journal.kpi.creditUnit')"
          :hint="t('page.journal.kpi.pageHint', { count: fmtInt(summary.loaded) })"
          :icon="ArrowDownUp"
          :tone="summary.netDelta > 0 ? 'ok' : 'neutral'"
          :loading="!ready"
        />
        <KpiCard
          :label="t('journal.kind.confirmed')"
          :value="ready ? fmtInt(summary.confirmed) : null"
          :unit="t('page.journal.kpi.countUnit')"
          :hint="t('page.journal.kpi.confirmedHint')"
          :icon="CircleCheck"
          tone="neutral"
          :loading="!ready"
        />
        <KpiCard
          :label="t('journal.kind.observed')"
          :value="ready ? fmtInt(summary.observed) : null"
          :unit="t('page.journal.kpi.countUnit')"
          :hint="t('page.journal.kpi.observedHint')"
          :icon="Eye"
          tone="neutral"
          :loading="!ready"
        />
      </section>

      <!-- 历史导入中：内联提示条 + 继续导入（原来是一行孤零零的橙字，要用户自己去点刷新） -->
      <div v-if="importHint" class="journal-notice" role="status">
        <Info :size="16" class="journal-notice__icon" />
        <span class="journal-notice__text">{{ t('page.journal.importingBar') }}</span>
        <Btn variant="default" size="sm" :loading="loading" @click="load()">
          {{ t('page.journal.importingContinue') }}
        </Btn>
      </div>

      <section class="journal-card">
        <div v-if="firstLoading" class="journal-skeleton" aria-busy="true" :aria-label="t('page.journal.loadingAria')">
          <div class="journal-skeleton__bar mh-skeleton" />
          <div v-for="index in 8" :key="index" class="journal-skeleton__row mh-skeleton" />
        </div>

        <DataTable
          v-else
          table-id="journal"
          :columns="columns"
          :data="sortedRows as unknown as TableRow[]"
          row-key="key"
          :loading="loading && !silentRefresh"
          :refreshing="loading && silentRefresh"
          :pagination="false"
          :max-height="'calc(100vh - 360px)'"
          :empty-description="emptyDescription"
          @update:sorter="onSorter"
        >
          <!-- 一行工具栏：左筛选，右统计 + 表格图标（DataTable 自带） -->
          <template #toolbar>
            <div class="journal-toolbar">
              <n-select
                class="journal-toolbar__select"
                size="small"
                :value="accountValue"
                :options="accountSelectOptions"
                :placeholder="t('journal.option.allAccounts')"
                :aria-label="t('page.journal.chip.account')"
                filterable
                @update:value="(value: string) => applyFilter('account', value)"
              />
              <n-select
                class="journal-toolbar__select journal-toolbar__select--sm"
                size="small"
                :value="table.state.filters.kind"
                :options="kindSelectOptions"
                :aria-label="t('page.journal.chip.kind')"
                @update:value="(value: string) => applyFilter('kind', value)"
              />
              <n-select
                class="journal-toolbar__select"
                size="small"
                :value="table.state.filters.source"
                :options="sourceSelectOptions"
                :aria-label="t('page.journal.chip.source')"
                @update:value="(value: string) => applyFilter('source', value)"
              />
              <DateRange :model-value="rangeValue" @update:model-value="onRange" />
              <n-input
                class="journal-toolbar__search"
                size="small"
                :value="search"
                :placeholder="t('page.journal.searchPlaceholder')"
                clearable
                @update:value="onSearch"
              />
              <Btn v-if="hasFilters" variant="quiet" size="sm" :icon="RotateCcw" @click="resetFilters">
                {{ t('common.reset') }}
              </Btn>
              <span class="journal-toolbar__summary tnum">{{ summaryText }}</span>
            </div>
          </template>
          <template #empty>
            <Empty :description="emptyDescription">
              <Btn v-if="hasFilters" variant="default" size="sm" @click="resetFilters">
                {{ t('page.journal.clearFilters') }}
              </Btn>
            </Empty>
          </template>
        </DataTable>

        <div v-if="!firstLoading" class="journal-footer">
          <p class="journal-hint">{{ t('page.journal.footer.note') }}</p>
          <div class="journal-pager">
            <label class="journal-fast">
              <n-switch :value="fast" size="small" @update:value="toggleFast" />
              <span>{{ t('page.journal.fast') }}</span>
            </label>
            <Btn variant="quiet" size="sm" :disabled="!page.hasPrev || loading" @click="goPage(page.page - 1)">
              {{ t('page.journal.pager.prev') }}
            </Btn>
            <span class="journal-page-text tnum">{{ pageText }}</span>
            <Btn
              variant="quiet"
              size="sm"
              :disabled="!canGoNext(fast, page, sortedRows.length) || loading"
              @click="goPage(page.page + 1)"
            >
              {{ t('page.journal.pager.next') }}
            </Btn>
          </div>
        </div>
      </section>

      <p class="journal-hint journal-hint--foot">{{ t('page.journal.footer.hint') }}</p>
    </template>
  </div>
</template>

<style scoped>
.journal-page {
  display: grid;
  gap: var(--sp-5);
  min-width: 0;
  align-items: start;
}

.journal-meta {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.journal-kpis {
  display: grid;
  grid-auto-rows: 1fr;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--sp-4);
  align-items: stretch;
}

.journal-card {
  min-width: 0;
  display: grid;
  gap: var(--sp-2);
  padding: var(--sp-3) var(--sp-4) var(--sp-3);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-card);
  box-shadow: var(--shadow-1);
}

.journal-toolbar {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
  min-width: 0;
  width: 100%;
}

.journal-toolbar__select {
  width: 170px;
}

.journal-toolbar__select--sm {
  width: 130px;
}

.journal-toolbar__search {
  width: 220px;
}

.journal-toolbar__summary {
  margin-left: auto;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.journal-notice {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  padding: var(--sp-2) var(--sp-4);
  border: 1px solid var(--warn-soft);
  border-radius: var(--r-card);
  background: var(--warn-soft);
}

.journal-notice__icon {
  flex: none;
  color: var(--warn);
}

.journal-notice__text {
  flex: 1;
  min-width: 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg);
}

.journal-skeleton {
  display: grid;
  gap: var(--sp-3);
  padding: var(--sp-2) 0;
}

.journal-skeleton__bar {
  height: 28px;
  width: 60%;
  border-radius: var(--r-ctl);
  background: var(--surface-2);
}

.journal-skeleton__row {
  height: 12px;
  border-radius: var(--r-chip);
  background: var(--surface-2);
}

.journal-time {
  color: var(--fg-2);
}

.journal-account {
  display: flex;
  align-items: baseline;
  gap: var(--sp-2);
  min-width: 0;
}

.journal-account__name {
  font-weight: 500;
  color: var(--fg);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.journal-account__id {
  flex: none;
  font-size: var(--fs-small);
  color: var(--fg-faint);
}

.journal-delta {
  font-weight: 500;
}

.journal-delta.is-ok {
  color: var(--ok);
}

.journal-delta.is-fg {
  color: var(--fg);
}

.journal-delta.is-neutral {
  color: var(--fg-muted);
}

.journal-faint {
  color: var(--fg-faint);
}

.journal-note {
  color: var(--fg-2);
  font-size: var(--fs-small);
}

.journal-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-3);
  flex-wrap: wrap;
  padding-top: var(--sp-1);
}

.journal-hint {
  margin: 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  max-width: 80ch;
  text-wrap: pretty;
}

.journal-hint--foot {
  max-width: none;
}

.journal-pager {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
}

.journal-page-text {
  font-size: var(--fs-small);
  color: var(--fg-2);
}

.journal-fast {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-1);
  margin-right: var(--sp-2);
  font-size: var(--fs-small);
  color: var(--fg-muted);
}

@media (max-width: 1279px) {
  .journal-kpis {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 767px) {
  .journal-kpis {
    grid-template-columns: minmax(0, 1fr);
  }

  .journal-toolbar__select,
  .journal-toolbar__select--sm,
  .journal-toolbar__search {
    width: 100%;
  }

  .journal-toolbar__summary {
    margin-left: 0;
  }

  .journal-footer {
    align-items: flex-start;
  }
}
</style>
