<script setup lang="ts">
/**
 * S22 积分流水 /journal（设计稿 06-pages-b P8，模板 B）。
 *
 * 契约：docs/ui-v2/contracts/journal.md。
 * 旧版：#panel-journal + review-improvements.js 的 loadJournal / resetJournal，
 * 数据来自 GET /api/maintenance/credit-events（admin/routers/maintenance.py:credit_events）。
 *
 * 三个必须说清的后端事实（页面上都会如实标注）：
 *   · CreditEvent 没有平台列，平台归属来自 X-Platform 作用域 → 「平台」筛选 = 切全局平台上下文；
 *   · 分页是 page/page_size 偏移，「快速翻页」只能不显示总数，无法让后端跳过 COUNT；
 *   · 没有 search 参数 → 搜索只过滤当前页。
 */
import { computed, h, onBeforeUnmount, onMounted, ref, watch, type VNodeChild } from 'vue'
import { NInput, NSelect, NSkeleton, NSwitch } from 'naive-ui'
import { Download, RefreshCw } from 'lucide-vue-next'
import PageHeader from '@/layout/PageHeader.vue'
import {
  Badge,
  Btn,
  Card,
  DataTable,
  DateRange,
  Empty,
  ErrorBlock,
  FilterBar,
  type FilterChip,
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
  kindMeta,
  kindOptions,
  listCreditEvents,
  pageState,
  rangeToQuery,
  saveCsv,
  sourceOptions,
  summarize,
  toCsv,
  toJournalRows,
  type CreditEventPayload,
  type JournalRow,
} from '@/api/journal'
import { useI18n } from '@/i18n'

const { t } = useI18n()
/** 旧版面板副标题（原样保留，收进 ⓘ）；语种跟随 useI18n。 */
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

const fast = ref(false)

const platformLabel = computed(
  () => platform.currentSpec?.label || platform.currentId || t('page.journal.platformFallback'),
)
const platformOptions = computed(() =>
  (platform.platforms || []).map((item) => ({ label: item.label || item.id, value: item.id })),
)

const rows = computed(() => toJournalRows(payload.value?.items, platformLabel.value, t))
const filteredRows = computed(() => filterJournalRows(rows.value, { q: table.state.filters.q }))

const SORTERS: Record<string, (a: JournalRow, b: JournalRow) => number> = {
  time: (a, b) => String(a.fullTime).localeCompare(String(b.fullTime)),
  delta: (a, b) => a.delta - b.delta,
  balance: (a, b) => Number(a.delta) - Number(b.delta),
}

const sortedRows = computed<JournalRow[]>(() => {
  const key = table.state.sortBy
  const sorter = SORTERS[key]
  if (!sorter) return filteredRows.value
  const factor = table.state.sortOrder === 'asc' ? 1 : table.state.sortOrder === 'desc' ? -1 : 0
  if (!factor) return filteredRows.value
  return [...filteredRows.value].sort((a, b) => sorter(a, b) * factor)
})

const page = computed(() => pageState(payload.value, table.state.pageSize))
const summary = computed(() => summarize(filteredRows.value, page.value.total))

/**
 * 首屏数据没回来之前不能报「共 0 条」：0 条（真的没有流水）和「还没取到」是两件事。
 * payload 为空时给加载态，拿到响应后才显示条数统计。
 */
const summaryText = computed(() => {
  if (payload.value !== null) return summary.value.text
  return loading.value ? t('page.journal.loading') : ''
})
const importHint = computed(() => importingHint(payload.value, t))
const hasFilters = computed(() =>
  Boolean(
    table.state.filters.account ||
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

const chips = computed<FilterChip[]>(() => {
  const filters = table.state.filters
  const list: FilterChip[] = []
  if (filters.account) {
    const option = accountSelectOptions.value.find((item) => item.value === filters.account)
    if (option && filters.account !== '0') {
      list.push({ key: 'account', label: t('page.journal.chip.account'), value: option.label })
    }
  }
  if (filters.kind) {
    const option = kindSelectOptions.value.find((item) => item.value === filters.kind)
    list.push({ key: 'kind', label: t('page.journal.chip.kind'), value: option?.label || filters.kind })
  }
  if (filters.source) {
    const label = sourceOptions(payload.value?.sources, t).find(
      (item) => item.value === filters.source,
    )?.label
    list.push({ key: 'source', label: t('page.journal.chip.source'), value: label || filters.source })
  }
  if (filters.start && filters.end) {
    list.push({
      key: 'range',
      label: t('page.journal.chip.time'),
      value: filters.start.slice(0, 16).replace('T', ' ') + ' ~ ' + filters.end.slice(11, 16),
    })
  }
  if (filters.q) list.push({ key: 'q', label: t('page.journal.chip.search'), value: filters.q })
  return list
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
    payload.value = next
  } catch (error) {
    if (isAbortError(error)) return
    loadError.value = {
      message: errorMessage(error),
      detail: error instanceof ApiError && error.detail ? prettyDetail(error.detail) : '',
    }
    toast.error(
      t('page.journal.failure', {
        action: t(
          payload.value === null ? 'page.journal.action.load' : 'page.journal.action.refresh',
        ),
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

watch(
  () => [table.state.page, table.state.pageSize, table.state.filters],
  () => {
    void load()
  },
  { deep: true },
)

/* ------------------------------ 交互 ------------------------------ */
function onFilterChange(values: Record<string, unknown>): void {
  const next: Record<string, string> = {}
  for (const [name, value] of Object.entries(values)) next[name] = String(value ?? '')
  table.setFilters(next)
}

function applyFilter(name: string, value: string | number | null): void {
  table.setFilter(name, value === null || value === undefined ? '' : String(value))
}

function onRange(value: [number, number] | null): void {
  const range = rangeToQuery(value)
  table.setFilters({ start: range.start, end: range.end })
}

function onPlatform(value: string | number): void {
  platform.setCurrent(String(value))
}

function removeChip(key: string): void {
  if (key === 'range') {
    table.setFilters({ start: '', end: '' })
    return
  }
  if (key === 'q') search.value = ''
  table.setFilter(key, key === 'account' ? '0' : '')
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
  if (!sorter || !sorter.order || !SORTERS[String(sorter.columnKey)]) {
    table.setSort('', '')
    return
  }
  table.setSort(String(sorter.columnKey), sorter.order === 'ascend' ? 'asc' : 'desc')
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
  return h(Badge, { tone: item.kindTone }, { default: () => item.kindLabel })
}

function cellDelta(row: TableRow): VNodeChild {
  const item = row as JournalRow
  return h('span', { class: 'journal-delta mono tnum is-' + item.deltaTone }, item.deltaText)
}

function cellBalance(row: TableRow): VNodeChild {
  const item = row as JournalRow
  return h(
    'span',
    {
      class: item.balanceRecorded ? 'tnum' : 'journal-muted',
      title: item.balanceRecorded ? '' : t('page.journal.balanceMissing'),
    },
    item.balanceText,
  )
}

const columns = computed<TableColumn[]>(() => [
  {
    key: 'time',
    title: t('page.journal.col.time'),
    mobile: 'title',
    width: 140,
    sortable: true,
    sortOrder: sortOrderOf('time'),
    render: (row: TableRow) => h('span', { title: String(row.fullTime || '') }, String(row.time || FMT_EMPTY)),
  },
  { key: 'accountText', title: t('page.journal.col.account'), mobile: 'subtitle', minWidth: 180 },
  { key: 'platformText', title: t('page.journal.col.platform'), mobile: 'meta', width: 140 },
  { key: 'kindLabel', title: t('page.journal.col.kind'), mobile: 'meta', width: 120, render: cellKind },
  {
    key: 'deltaText',
    title: t('page.journal.col.delta'),
    mobile: 'meta',
    width: 110,
    numeric: true,
    sortable: true,
    sortOrder: sortOrderOf('delta'),
    render: cellDelta,
  },
  {
    key: 'balanceText',
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
    render: (row: TableRow) =>
      h('span', { class: 'journal-note', title: String(row.source || '') }, String(row.note || FMT_EMPTY)),
  },
])

/* ------------------------------ 生命周期 ------------------------------ */
onMounted(async () => {
  try {
    fast.value = localStorage.getItem(FAST_KEY) === '1'
  } catch {
    fast.value = false
  }
  stopPlatformWatch = platform.onChange(() => {
    table.setPage(1)
    void load()
  })
  if (!platform.loaded) await platform.load()
  void load()
})

onBeforeUnmount(() => {
  stopPlatformWatch?.()
  stopPlatformWatch = null
  controller?.abort()
})
</script>

<template>
  <div class="journal-page">
    <PageHeader :help="helpText">
      <template #actions>
        <Btn variant="quiet" :icon="RefreshCw" :loading="loading" @click="load()">
          {{ t('page.journal.refresh') }}
        </Btn>
        <Btn variant="default" :icon="Download" @click="exportCsv">
          {{ t('page.journal.export') }}
        </Btn>
      </template>
    </PageHeader>

    <Card flush class="journal-card">
      <div class="journal-filter">
        <FilterBar
          :chips="chips"
          :summary="summaryText"
          :loading="loading"
          @change="onFilterChange"
          @reset="resetFilters"
          @remove="removeChip"
        >
          <template #default="{ change }">
            <n-select
              class="journal-filter__select"
              :value="platform.currentId"
              :options="platformOptions"
              :aria-label="t('page.journal.platformAria')"
              @update:value="onPlatform"
            />
            <n-select
              class="journal-filter__select"
              :value="table.state.filters.account"
              :options="accountSelectOptions"
              @update:value="(value: string) => applyFilter('account', value)"
            />
            <n-select
              class="journal-filter__select"
              :value="table.state.filters.kind"
              :options="kindSelectOptions"
              @update:value="(value: string) => applyFilter('kind', value)"
            />
            <n-select
              class="journal-filter__select"
              :value="table.state.filters.source"
              :options="sourceSelectOptions"
              @update:value="(value: string) => applyFilter('source', value)"
            />
            <DateRange :model-value="rangeValue" @update:model-value="onRange" />
            <n-input
              class="journal-filter__search"
              :value="search"
              :placeholder="t('page.journal.searchPlaceholder')"
              clearable
              @update:value="
                (value: string) => {
                  search = value
                  change('q', value)
                }
              "
            />
          </template>
        </FilterBar>
      </div>

      <div class="journal-body">
        <ErrorBlock
          v-if="loadError && payload === null"
          :title="t('page.journal.errorTitle', { message: loadError.message })"
          :detail="loadError.detail"
          @retry="load()"
        />

        <div
          v-else-if="firstLoading"
          class="journal-skeleton"
          aria-busy="true"
          :aria-label="t('page.journal.loadingAria')"
        >
          <n-skeleton v-for="index in 6" :key="index" height="44px" />
        </div>

        <template v-else>
          <p v-if="importHint" class="journal-notice" role="status">{{ importHint }}</p>

          <DataTable
            table-id="journal"
            :columns="columns"
            :data="sortedRows as unknown as TableRow[]"
            row-key="key"
            :loading="loading"
            :pagination="false"
            :max-height="'calc(100vh - 340px)'"
            :empty-description="emptyDescription"
            @update:sorter="onSorter"
          >
            <template #empty>
              <Empty :description="emptyDescription">
                <Btn v-if="hasFilters" variant="default" size="sm" @click="resetFilters">
                  {{ t('page.journal.clearFilters') }}
                </Btn>
              </Empty>
            </template>
          </DataTable>

          <div class="journal-footer">
            <p class="journal-hint">
              {{ importHint || t('page.journal.footer.note') }}
            </p>
            <div class="journal-pager">
              <Btn
                variant="default"
                size="sm"
                :disabled="!page.hasPrev || loading"
                @click="goPage(page.page - 1)"
              >
                {{ t('page.journal.pager.prev') }}
              </Btn>
              <span class="journal-page-text tnum">{{ pageText }}</span>
              <Btn
                variant="default"
                size="sm"
                :disabled="!canGoNext(fast, page, sortedRows.length) || loading"
                @click="goPage(page.page + 1)"
              >
                {{ t('page.journal.pager.next') }}
              </Btn>
              <label class="journal-fast">
                <n-switch :value="fast" size="small" @update:value="toggleFast" />
                <span>{{ t('page.journal.fast') }}</span>
              </label>
            </div>
          </div>

          <p class="journal-hint journal-hint--foot">
            {{ t('page.journal.footer.hint') }}
            <template v-if="!fast">
              {{ t('page.journal.footer.total', { count: fmtInt(page.total) }) }}
            </template>
          </p>
        </template>
      </div>
    </Card>
  </div>
</template>

<style scoped>
.journal-page {
  display: grid;
  gap: var(--sp-4);
  min-width: 0;
}
.journal-filter {
  padding: var(--sp-4) var(--sp-5) 0;
}
.journal-filter__select {
  width: 160px;
}
.journal-filter__search {
  width: 240px;
}
.journal-body {
  padding: var(--sp-3) var(--sp-5) var(--sp-5);
  display: grid;
  gap: var(--sp-2);
}
.journal-skeleton {
  display: grid;
  gap: var(--sp-2);
}
.journal-notice {
  font-size: var(--fs-small);
  color: var(--warn);
}
.journal-delta.is-ok {
  color: var(--ok);
}
.journal-delta.is-danger {
  color: var(--danger);
}
.journal-delta.is-neutral {
  color: var(--fg-muted);
}
.journal-muted {
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
}
.journal-hint {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  max-width: 68ch;
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
  font-size: var(--fs-small);
  color: var(--fg-muted);
}
@media (max-width: 640px) {
  .journal-filter__select,
  .journal-filter__search {
    width: 100%;
  }
  .journal-footer {
    align-items: flex-start;
  }
}
</style>