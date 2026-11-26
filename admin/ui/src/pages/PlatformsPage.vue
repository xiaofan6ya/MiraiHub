<script setup lang="ts">
/**
 * S20 平台管理 /platforms（模板 B 的卡片变体）
 *
 * 契约：docs/ui-v2/contracts/platforms.md（末尾「## 实现对照」）
 * 设计稿：docs/ui-v2/06-pages-a.md P4
 * 旧版：#panel-platforms + platform-switch.js / platform-actions.js / traffic.js（platform-tiers 分级已于 r4 删除）
 *
 * 三条口径：
 *   1. 平台与能力全部来自 /api/platforms（actions / supports_local_scan / auth_mode），页面不写 if (id === 'trae')；
 *   2. 启用/禁用 = 调度开关（/api/gateway/disable|enable），账号仍可管理；
 *   3. 卡片上的「今日请求 / 成功率 / TTFT / TPS」来自 /api/platform/speed（成功率为 0–100），
 *      「可用账号 / 剩余额度」来自 /api/accounts 快照（拿不到就只显示账号数，不猜）。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { NInput, type DropdownOption } from 'naive-ui'
import { Download, RefreshCw, ScanSearch, Upload } from 'lucide-vue-next'
import PageHeader from '@/layout/PageHeader.vue'
import {
  Btn,
  Card,
  DataTable,
  Empty,
  ErrorBlock,
  FilterBar,
  IconBtn,
  JobProgress,
  Segmented,
  type FilterChip,
  type SegmentedOption,
  type TableRow,
} from '@/components'
import PlatformCardItem from './platforms/PlatformCardItem.vue'
import PlatformTransferDrawer from './platforms/PlatformTransferDrawer.vue'
import { usePlatformBenchColumns } from './platforms/benchColumns'
import { usePlatformImport } from './platforms/usePlatformImport'
import { useConfirm, useRevisit, useTableQuery, useToast } from '@/composables'
import { usePlatformStore } from '@/stores/platform'
import { ApiError, isAbortError } from '@/api/http'
import { failureText } from '@/utils/error'
import { listPlatforms, type PlatformSpec } from '@/api/platforms'
import { useI18n } from '@/i18n'
import {
  aggregateAccounts,
  countByState,
  exportFilename,
  exportPlatformsAll,
  filterPlatforms,
  getAccountsSnapshot,
  getGatewayConfig,
  getPlatformSpeed,
  isExportConfirmRequired,
  runPlatformScan,
  saveJson,
  scanPlanFor,
  setPlatformEnabled,
  toPlatformCards,
  type GatewayConfigPayload,
  type PlatformCard,
  type PlatformScanResult,
  type PlatformSpeedPayload,
  type PlatformSpeedRow,
  type PlatformStateFilter,
} from '@/api/platforms-admin'

const { t } = useI18n()
const helpText = computed(() => t('page.platforms.help'))

const toast = useToast()
const confirm = useConfirm()
const router = useRouter()
const platformStore = usePlatformStore()

const table = useTableQuery('platforms', {
  filters: { q: '', state: '', hours: '' },
})
const filters = table.state.filters as unknown as {
  q: string
  state: string
  hours: string
}

/* ------------------------------------------------------------------ *
 * 取数
 * ------------------------------------------------------------------ */

const platforms = ref<PlatformSpec[]>([])
const speed = ref<PlatformSpeedPayload | null>(null)
const gateway = ref<GatewayConfigPayload | null>(null)
const accounts = ref<Record<string, { total: number; active: number; credits: number }>>({})
const loading = ref(false)
const loadError = ref<{ message: string; detail: string } | null>(null)
let controller: AbortController | null = null

const hours = computed(() => (filters.hours === '168' ? 168 : 24))
const firstLoading = computed(() => loading.value && !platforms.value.length)

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
    const [list, bench, cfg, snapshot] = await Promise.all([
      listPlatforms(current.signal),
      getPlatformSpeed(hours.value, current.signal).catch(() => null),
      getGatewayConfig(current.signal).catch(() => null),
      // 账号快照只用于「可用账号 / 剩余额度」：拿不到就降级，不阻塞整页
      getAccountsSnapshot(current.signal).catch(() => null),
    ])
    // r4（P1）：listPlatforms 不带 signal、副接口把 AbortError 吞成 null —— 被新请求中止的旧请求
    // 仍会 resolve，并把速度表 / 调度配置 / 账号快照覆盖成空。只认最后一次请求。
    if (controller !== current) return
    platforms.value = Array.isArray(list?.items) ? list.items : []
    speed.value = bench
    gateway.value = cfg
    accounts.value = aggregateAccounts(snapshot?.items || [])
  } catch (error) {
    if (isAbortError(error) || controller !== current) return
    const detail = error instanceof ApiError && error.detail ? prettyDetail(error.detail) : ''
    loadError.value = { message: failureText(t('page.platforms.action.load'), error), detail }
    toast.error(failureText(t('page.platforms.action.load'), error))
  } finally {
    if (controller === current) {
      controller = null
      loading.value = false
    }
  }
}

function refresh(): void {
  void load()
}

/* ------------------------------------------------------------------ *
 * 卡片
 * ------------------------------------------------------------------ */

const cards = computed<PlatformCard[]>(() =>
  toPlatformCards({
    platforms: platforms.value,
    speed: speed.value,
    gateway: gateway.value,
    accounts: accounts.value,
  }),
)
const counts = computed(() => countByState(cards.value))
const stateFilter = computed<PlatformStateFilter>(() => {
  if (filters.state === 'enabled' || filters.state === 'disabled') return filters.state
  return 'all'
})
const visibleCards = computed(() =>
  filterPlatforms(cards.value, { state: stateFilter.value, q: filters.q }),
)
const maxCredits = computed(() =>
  cards.value.reduce((max, card) => Math.max(max, Number(card.credits) || 0), 0),
)

const stateOptions = computed<Array<SegmentedOption<string>>>(() => [
  { label: t('page.platforms.state.all', { count: counts.value.all }), value: 'all' },
  { label: t('page.platforms.state.enabled', { count: counts.value.enabled }), value: 'enabled' },
  { label: t('page.platforms.state.disabled', { count: counts.value.disabled }), value: 'disabled' },
])

const chips = computed<FilterChip[]>(() => {
  const list: FilterChip[] = []
  if (filters.q) list.push({ key: 'q', label: t('page.platforms.chip.search'), value: filters.q })
  if (stateFilter.value !== 'all') {
    list.push({
      key: 'state',
      label: t('page.platforms.chip.state'),
      value:
        stateFilter.value === 'enabled'
          ? t('page.platforms.badge.enabled')
          : t('page.platforms.badge.disabled'),
    })
  }
  return list
})

const summary = computed(() =>
  t('page.platforms.summary', {
    all: counts.value.all,
    enabled: counts.value.enabled,
    disabled: counts.value.disabled,
  }),
)

const search = ref(filters.q)
watch(
  () => filters.q,
  (value) => {
    search.value = value
  },
)

function onFilterChange(values: Record<string, unknown>): void {
  const next: Record<string, string> = {}
  for (const [name, value] of Object.entries(values)) {
    next[name] = value === null || value === undefined ? '' : String(value)
  }
  table.setFilters(next)
}

function setState(value: string | number): void {
  table.setFilter('state', value === 'enabled' || value === 'disabled' ? String(value) : '')
}

function removeChip(key: string): void {
  if (key === 'q') search.value = ''
  table.setFilter(key, '')
}

function resetFilters(): void {
  search.value = ''
  table.setFilters({ q: '', state: '' })
}

function setHours(value: string | number): void {
  table.setFilter('hours', String(value) === '168' ? '168' : '')
}

const hoursOptions = computed<Array<SegmentedOption<string>>>(() => [
  { label: t('page.platforms.hours.24'), value: '24' },
  { label: t('page.platforms.hours.168'), value: '168' },
])

function isCurrent(card: PlatformCard): boolean {
  return platformStore.platform === card.id && (platformStore.realm || '') === (card.realm || '')
}

/* ------------------------------------------------------------------ *
 * 卡片操作
 * ------------------------------------------------------------------ */

function openAccounts(card: PlatformCard): void {
  platformStore.setCurrent(card.id, card.realm || '')
  void router.push('/accounts')
}

function makeCurrent(card: PlatformCard): void {
  if (isCurrent(card)) return
  platformStore.setCurrent(card.id, card.realm || '')
  toast.success(t('page.platforms.toast.switched', { name: card.label }))
}

async function toggleEnabled(card: PlatformCard): Promise<void> {
  const enabling = !card.enabled
  if (!enabling) {
    const ok = await confirmDisable(card)
    if (!ok) return
  }
  try {
    await setPlatformEnabled(card.storage, enabling, enabling ? '' : t('page.platforms.disabledReason'))
    toast.success(
      enabling
        ? t('page.platforms.toast.enabled', { name: card.label })
        : t('page.platforms.toast.disabled', { name: card.label }),
    )
    await load()
    void platformStore.reload()
  } catch (error) {
    toast.error(
      failureText(
        enabling ? t('page.platforms.action.enable') : t('page.platforms.action.disable'),
        error,
      ),
    )
  }
}

function confirmDisable(card: PlatformCard): Promise<boolean> {
  return confirm({
    title: t('page.platforms.confirmDisable.title', { name: card.label }),
    message: t('page.platforms.confirmDisable.body'),
    okText: t('page.platforms.confirmDisable.ok'),
    danger: true,
  })
}

async function scanOne(card: PlatformCard): Promise<void> {
  const spec = platforms.value.find((item) => item.id === card.id)
  const plan = scanPlanFor(spec)
  if (!spec || !plan) {
    toast.error(t('page.platforms.scan.noSpec'))
    return
  }
  toast.info(t('page.platforms.scan.starting', { name: card.label }))
  const result = await runPlatformScan(spec, plan)
  if (result.note && !result.imported && !result.scanned) {
    toast.error(card.label + ' · ' + result.note)
  } else {
    toast.success(
      t('page.platforms.scan.done', {
        name: card.label,
        scanned: result.scanned,
        imported: result.imported,
      }),
    )
  }
  await load()
  void platformStore.refreshCounts()
}

async function exportOne(card: PlatformCard): Promise<void> {
  await runExport('backup', card.storage)
}

/** 完整凭证导出前的确认（后端 export_guard 要求带 X-Export-Confirm 头）。 */
function confirmSecrets(): Promise<boolean> {
  return confirm({
    title: t('page.platforms.export.secretsTitle'),
    message: t('page.platforms.export.secretsMessage'),
    okText: t('page.platforms.export.secretsOk'),
    danger: true,
  })
}

async function fetchExport(mode: 'backup' | 'plain', platform: string): Promise<Record<string, unknown> | null> {
  let confirmed = false
  if (mode === 'backup') {
    if (!(await confirmSecrets())) return null
    confirmed = true
  }
  try {
    return await exportPlatformsAll(mode, platform, confirmed)
  } catch (error) {
    // 老前端缓存 / 后端开关变化：收到 428 时补一次确认再重发
    if (!confirmed && isExportConfirmRequired(error)) {
      if (!(await confirmSecrets())) return null
      return exportPlatformsAll(mode, platform, true)
    }
    throw error
  }
}

async function runExport(mode: 'backup' | 'plain', platform = ''): Promise<void> {
  if (exporting.value) return
  exporting.value = true
  try {
    const data = await fetchExport(mode, platform)
    if (!data) return
    const platformsMap = (data?.platforms || {}) as Record<string, { count?: number }>
    const count = Object.values(platformsMap).reduce((sum, item) => sum + Number(item?.count || 0), 0)
    if (!count) {
      toast.error(t('page.platforms.export.empty'))
      return
    }
    const scope = String(data?.platform_scope || (platform || 'all-platforms'))
    saveJson(data, exportFilename(mode, scope))
    toast.success(
      t('page.platforms.export.done', { count, platforms: Object.keys(platformsMap).length }),
    )
    exportOpen.value = false
  } catch (error) {
    toast.error(failureText(t('page.platforms.action.export'), error))
  } finally {
    exporting.value = false
  }
}

/* ------------------------------------------------------------------ *
 * 卡片 ⋯ 菜单
 * ------------------------------------------------------------------ */

function menuOptions(card: PlatformCard): DropdownOption[] {
  const options: DropdownOption[] = [
    {
      label: isCurrent(card) ? t('page.platforms.card.current') : t('page.platforms.card.setCurrent'),
      key: 'current',
      disabled: isCurrent(card),
    },
    { type: 'divider', key: 'divider-0' },
    {
      label: card.enabled ? t('page.platforms.action.disable') : t('page.platforms.action.enable'),
      key: 'toggle',
    },
    {
      label: t('page.platforms.menu.scan'),
      key: 'scan',
      disabled: !card.actions.some((item) => item.name === 'scan'),
    },
    { label: t('page.platforms.menu.export'), key: 'export' },
  ]
  const extra = card.actions.filter((item) => !['scan', 'export'].includes(item.name))
  if (extra.length) {
    options.push({ type: 'divider', key: 'divider-1' })
    for (const action of extra) {
      options.push({
        label:
          action.name === 'import' || action.name === 'import-file'
            ? action.label
            : t('page.platforms.menu.accountsSuffix', { label: action.label }),
        key: 'action:' + action.name,
      })
    }
  }
  return options
}

function onMenuSelect(card: PlatformCard, key: string | number): void {
  const name = String(key)
  if (name === 'current') {
    makeCurrent(card)
    return
  }
  if (name === 'toggle') {
    void toggleEnabled(card)
    return
  }
  if (name === 'scan') {
    void scanOne(card)
    return
  }
  if (name === 'export') {
    void exportOne(card)
    return
  }
  if (name.startsWith('action:')) {
    const action = name.slice(7)
    if (action === 'import' || action === 'import-file') {
      openImport(card.storage)
      return
    }
    // 其余平台动作（签到 / 刷新额度 / 授权登录…）在账号池按账号执行（S25/S26）
    platformStore.setCurrent(card.id, card.realm || '')
    toast.info(t('page.platforms.toast.actionsInAccounts', { name: card.label }))
    void router.push('/accounts')
  }
}

/* ------------------------------------------------------------------ *
 * 扫描全部
 * ------------------------------------------------------------------ */

const scanningAll = ref(false)
const scanResults = ref<PlatformScanResult[]>([])

async function scanAll(): Promise<void> {
  if (scanningAll.value) return
  if (!platforms.value.length) {
    toast.error(t('page.platforms.scan.notReady'))
    return
  }
  scanningAll.value = true
  scanResults.value = []
  let imported = 0
  try {
    for (const spec of platforms.value) {
      const plan = scanPlanFor(spec)
      if (!plan) continue
      if (!spec.supports_local_scan && plan.kind !== 'accounts-import') continue
      const result = await runPlatformScan(spec, plan)
      scanResults.value.push(result)
      imported += result.imported
    }
    toast.success(t('page.platforms.scan.allDone', { count: imported }))
    await load()
    void platformStore.refreshCounts()
  } finally {
    scanningAll.value = false
  }
}

/* ------------------------------------------------------------------ *
 * 导入全部（状态与任务跟踪见 platforms/usePlatformImport.ts）
 * ------------------------------------------------------------------ */

const {
  importOpen,
  importNotes,
  importPreview,
  submitting,
  openImport,
  onImportFiles,
  jobItems,
  hideJob,
  submitImport,
} = usePlatformImport({
  t,
  toast,
  onJobFinished: () => {
    void load()
    void platformStore.refreshCounts()
  },
})

/* ------------------------------------------------------------------ *
 * 导出全部（FormDrawer）
 * ------------------------------------------------------------------ */

const exportOpen = ref(false)
const exporting = ref(false)
const exportMode = ref<'backup' | 'plain'>('backup')
const exportScope = ref('')

const exportScopeOptions = computed(() => {
  const seen = new Set<string>()
  const options = [{ label: t('page.platforms.export.allPlatforms'), value: '' }]
  for (const card of cards.value) {
    if (!card.accountCount || seen.has(card.storage)) continue
    seen.add(card.storage)
    options.push({
      label: t('page.platforms.export.platformLabel', {
        label: card.label,
        count: card.accountCount,
      }),
      value: card.storage,
    })
  }
  return options
})

/* ------------------------------------------------------------------ *
 * 跑分表
 * ------------------------------------------------------------------ */

const benchRows = computed<TableRow[]>(() =>
  ((speed.value?.items || []) as PlatformSpeedRow[]).map((row) => ({ ...row })),
)

const benchColumns = usePlatformBenchColumns(t)

/* ------------------------------------------------------------------ *
 * 生命周期
 * ------------------------------------------------------------------ */

let offPlatform: (() => void) | null = null
let lastSelector = platformStore.selector

onMounted(() => {
  lastSelector = platformStore.selector
  void load()
  offPlatform = platformStore.onChange((detail) => {
    if (detail.selector === lastSelector) return
    lastSelector = detail.selector
    void load()
  })
})

/**
 * 二次进入（KeepAlive 缓存命中）不重新出骨架：保留旧数据，后台静默刷新（11-polish-motion §2）。
 * 骨架只在「加载中且还没有任何平台」时出现，所以这里直接 load() 即可，只会转右上角刷新圈。
 */
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

useRevisit(
  () => {
    if (!scanningAll.value) void silently(() => load())
  },
  { minIntervalMs: 10_000, onLeave: () => controller?.abort() },
)

onBeforeUnmount(() => {
  offPlatform?.()
  offPlatform = null
  controller?.abort()
  controller = null
})

watch(
  () => filters.hours,
  () => {
    void load()
  },
)
</script>

<template>
  <div class="pf-page">
    <PageHeader :title="t('page.platforms.title')" :help="helpText">
      <template #actions>
        <Btn variant="default" :icon="ScanSearch" :loading="scanningAll" :disabled="loading" @click="scanAll">
          {{ t('page.platforms.action.scanAll') }}
        </Btn>
        <Btn variant="default" :icon="Download" :disabled="loading" @click="exportOpen = true">
          {{ t('page.platforms.action.export') }}
        </Btn>
        <IconBtn :label="t('common.refresh')" :icon="RefreshCw" :loading="loading" @click="refresh" />
        <Btn variant="primary" :icon="Upload" :disabled="loading" @click="openImport()">
          {{ t('page.platforms.action.import') }}
        </Btn>
      </template>
    </PageHeader>

    <ErrorBlock
      v-if="loadError && !platforms.length"
      :title="loadError.message"
      :detail="loadError.detail"
      @retry="refresh"
    />

    <div
      v-else-if="firstLoading"
      class="pf-skeleton"
      aria-busy="true"
      :aria-label="t('page.platforms.loadingAria')"
    >
      <div v-for="index in 6" :key="index" class="pf-skeleton__card">
        <span class="mh-skeleton pf-sk pf-sk--head" />
        <span class="mh-skeleton pf-sk pf-sk--stats" />
        <span class="mh-skeleton pf-sk pf-sk--line" />
      </div>
    </div>

    <template v-else>
      <!-- 不再「卡片套卡片」：筛选条直接贴在页头下方（第 1 轮审查 P2）。 -->
      <section class="pf-list">
        <FilterBar
          :chips="chips"
          :summary="summary"
          :loading="loading"
          @change="onFilterChange"
          @reset="resetFilters"
          @remove="removeChip"
        >
          <template #default>
            <Segmented
              :options="stateOptions"
              :model-value="stateFilter"
              :aria-label="t('page.platforms.stateAria')"
              @update:model-value="setState"
            />
            <n-input
              v-model:value="search"
              class="pf-search"
              :placeholder="t('page.platforms.searchPlaceholder')"
              clearable
              @update:value="(value: string) => onFilterChange({ q: value })"
            />
          </template>
        </FilterBar>

        <div v-if="visibleCards.length" class="pf-grid">
          <PlatformCardItem
            v-for="card in visibleCards"
            :key="card.id"
            :card="card"
            :current="isCurrent(card)"
            :max-credits="maxCredits"
            :menu-options="menuOptions(card)"
            @accounts="openAccounts"
            @select="onMenuSelect"
          />
        </div>

        <Empty
          v-else
          :description="
            filters.q || stateFilter !== 'all'
              ? t('page.platforms.empty.filtered')
              : t('page.platforms.empty.all')
          "
        >
          <Btn v-if="filters.q || stateFilter !== 'all'" variant="default" size="sm" @click="resetFilters">
            {{ t('page.platforms.clearFilters') }}
          </Btn>
          <Btn v-else variant="quiet" size="sm" :icon="RefreshCw" @click="refresh">
            {{ t('common.refresh') }}
          </Btn>
        </Empty>
      </section>

      <Card :title="t('page.platforms.speed.title')" :loading="loading && !!speed">
        <template #extra>
          <Segmented
            :options="hoursOptions"
            :model-value="String(hours)"
            :aria-label="t('page.platforms.speed.windowAria')"
            @update:model-value="setHours"
          />
        </template>
        <DataTable
          table-id="platform-bench"
          :columns="benchColumns"
          :data="benchRows"
          row-key="platform"
          :loading="loading && !silentRefresh"
          :refreshing="loading && silentRefresh"
          :empty-description="t('page.platforms.speed.empty')"
        >
          <template #empty>
            <Empty compact :description="t('page.platforms.speed.empty')" />
          </template>
        </DataTable>
      </Card>

      <Card v-if="scanResults.length" :title="t('page.platforms.scan.resultTitle')">
        <ul class="pf-scan">
          <li v-for="item in scanResults" :key="item.storage" class="pf-scan__row">
            <span class="pf-scan__name">{{ item.label }}</span>
            <span class="tnum">{{ t('page.platforms.scan.scanned', { count: item.scanned }) }}</span>
            <span class="tnum">{{ t('page.platforms.scan.imported', { count: item.imported }) }}</span>
            <span class="tnum">{{ t('page.platforms.scan.skipped', { count: item.skipped }) }}</span>
            <span class="pf-scan__note">{{ item.note }}</span>
          </li>
        </ul>
      </Card>
    </template>

    <PlatformTransferDrawer
      v-model:show="importOpen"
      kind="import"
      :preview="importPreview"
      :notes="importNotes"
      :submitting="submitting"
      @files="onImportFiles"
      @submit="submitImport"
    />

    <PlatformTransferDrawer
      v-model:show="exportOpen"
      v-model:mode="exportMode"
      v-model:scope="exportScope"
      kind="export"
      :exporting="exporting"
      :scope-options="exportScopeOptions"
      @submit="runExport(exportMode, exportScope)"
    />

    <JobProgress :jobs="jobItems" :title="t('page.platforms.job.title')" @hide="hideJob" />
  </div>
</template>

<style scoped>
.pf-page {
  display: grid;
  gap: var(--sp-4);
  min-width: 0;
}

.pf-search {
  width: 240px;
  max-width: 100%;
}

.pf-list {
  display: grid;
  gap: var(--sp-3);
  margin-bottom: var(--sp-4);
}

.pf-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: var(--sp-4);
}

/* 骨架：只用 .mh-skeleton 一种样式（10 §1.7），形状与真卡片一致，数据到来不跳动。 */
.pf-skeleton {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: var(--sp-4);
}

.pf-skeleton__card {
  display: grid;
  gap: var(--sp-3);
  min-height: 212px;
  padding: var(--sp-4);
  border: 1px solid var(--border);
  border-radius: var(--r-card);
  background: var(--surface);
}

.pf-sk {
  display: block;
  border-radius: var(--r-ctl);
}

.pf-sk--head { height: 40px; width: 70%; }
.pf-sk--stats { height: 64px; }
.pf-sk--line { height: 28px; width: 50%; }

/* 速度表单元格（DataTable 渲染，需 :deep） */
:deep(.pf-low) { color: var(--warn); }
:deep(.pf-rank) { color: var(--fg-faint); font-variant-numeric: tabular-nums; }

.pf-scan {
  display: grid;
  gap: 0;
  margin: 0;
  padding: 0;
  list-style: none;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
}

.pf-scan__row {
  display: grid;
  grid-template-columns: minmax(96px, 160px) repeat(3, max-content) minmax(0, 1fr);
  gap: var(--sp-4);
  align-items: baseline;
  padding: var(--sp-2) 0;
  color: var(--fg-2);
  font-variant-numeric: tabular-nums;
}

.pf-scan__row + .pf-scan__row {
  border-top: 1px solid var(--border-soft);
}

.pf-scan__name {
  font-weight: 500;
  color: var(--fg);
}

.pf-scan__note {
  color: var(--fg-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (max-width: 640px) {
  .pf-grid,
  .pf-skeleton {
    grid-template-columns: minmax(0, 1fr);
  }

  .pf-search {
    width: 100%;
  }

  .pf-scan__row {
    grid-template-columns: minmax(0, 1fr);
    gap: 2px;
  }
}
</style>
