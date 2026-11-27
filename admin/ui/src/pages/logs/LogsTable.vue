<script setup lang="ts">
import TitleTip from '@/components/base/TitleTip.vue'

/**
 * 用量日志主表格（10-visual-refresh §1.5）。
 *
 * 列：时间 · 模型（端点 · 思考强度）· 状态徽章 · 首字 · 耗时 · Token · 命中率 · 积分 · Key · 账号/平台 · IP · 详情。
 * 第 1 轮审查：首字和总耗时原来挤在一格三行，现在分两列右对齐；积分不再用 warn 色（状态色只表达状态）；
 * 失败行的徽章写人话（「上游限流」），原始分类放 tooltip。
 * 工具栏由父组件通过 #toolbar 插槽传入，和 DataTable 自带的密度 / 列设置图标同一行。
 */
import { computed, h, type VNodeChild } from 'vue'
import { ChevronLeft, ChevronRight } from 'lucide-vue-next'
import {
  Badge,
  Btn,
  DataTable,
  Empty,
  ErrorBlock,
  type TableColumn,
  type TableRow,
} from '@/components'
import { FMT_EMPTY, fmtCredit, fmtInt, fmtRelative } from '@/utils/format'
import { useI18n } from '@/i18n'
import type { LogRowView } from '@/api/logs'
import { errorKindText } from './text'

const props = withDefaults(
  defineProps<{
    rows: LogRowView[] | null
    loading?: boolean
    /** 第 5 轮：二次进入的静默刷新（DataTable `refreshing`：保留旧数据，只在工具栏转小圈）。 */
    refreshing?: boolean
    /** 首次加载失败（rows 为 null 时才整块显示） */
    error?: { message: string; detail: string } | null
    page: number
    hasMore: boolean
    pagerInfo: string
    platformLabel: (value: string) => string
    /** 有生效筛选（空态文案与按钮不同） */
    filtered?: boolean
  }>(),
  { loading: false, refreshing: false, error: null, filtered: false },
)

const emit = defineEmits<{
  (event: 'open', row: LogRowView): void
  (event: 'page', page: number): void
  (event: 'retry'): void
  (event: 'reset'): void
}>()

const { t } = useI18n()

function asRow(row: TableRow): LogRowView {
  return row as unknown as LogRowView
}

function statusLabel(item: LogRowView): string {
  if (!item.isError) return t('page.logs.detail.success')
  return errorKindText(t, item.error_kind) || t('page.logs.detail.failed')
}

function modelSub(item: LogRowView): string {
  const parts: string[] = []
  if (item.useCaseLabel && item.useCaseLabel !== FMT_EMPTY) parts.push(item.useCaseLabel)
  if (item.reasoning_effort) parts.push(t('page.logs.col.reasoning', { value: item.reasoning_effort }))
  return parts.join(' · ')
}

const columns = computed<TableColumn[]>(() => [
  {
    key: 'time',
    title: t('page.logs.col.time'),
    mobile: 'subtitle',
    width: 96,
    render: (row: TableRow): VNodeChild => {
      const item = asRow(row)
      return h(TitleTip, { content: item.timeFull + ' · ' + fmtRelative(item.created_at) }, { default: () => h(
        'span',
        { class: 'lt-time tnum' },
        item.timeShort,
      ) })
    },
  },
  {
    key: 'model',
    title: t('page.logs.col.model'),
    mobile: 'title',
    minWidth: 200,
    render: (row: TableRow): VNodeChild => {
      const item = asRow(row)
      const sub = modelSub(item)
      return h('div', { class: 'lt-cell' }, [
        h(TitleTip, { content: String((item.model_raw && item.model_raw !== item.model ? item.model + ' ← ' + item.model_raw : item.model) ?? '') }, { default: () => h('span', { class: 'lt-model' }, item.model || FMT_EMPTY) }),
        sub ? h(TitleTip, { content: String((item.use_case || undefined) ?? '') }, { default: () => h('small', { class: 'lt-sub' }, sub) }) : null,
      ])
    },
  },
  {
    key: 'result',
    title: t('page.logs.col.status'),
    mobile: 'meta',
    width: 128,
    render: (row: TableRow): VNodeChild => {
      const item = asRow(row)
      return h(TitleTip, { content: String((item.error_kind || 'success') ?? '') }, { default: () => h('span', { class: 'lt-badge' }, [
        h(Badge, { tone: item.resultTone, dot: true }, { default: () => statusLabel(item) }),
      ]) })
    },
  },
  {
    key: 'ttfb',
    title: t('page.logs.col.ttft'),
    mobile: 'meta',
    width: 84,
    numeric: true,
    render: (row: TableRow): VNodeChild => h('span', { class: 'lt-num' }, asRow(row).ttfbText),
  },
  {
    key: 'latency',
    title: t('page.logs.col.elapsed'),
    mobile: 'meta',
    width: 96,
    numeric: true,
    render: (row: TableRow): VNodeChild => {
      const item = asRow(row)
      return h('div', { class: 'lt-cell lt-cell--end' }, [
        h('span', { class: 'lt-num' }, item.latencyText),
        item.tpsText !== FMT_EMPTY
          ? h('small', { class: 'lt-sub tnum' }, t('page.logs.col.tps', { value: item.tpsText }))
          : null,
      ])
    },
  },
  {
    key: 'tokens',
    title: 'Token',
    mobile: 'meta',
    width: 150,
    numeric: true,
    render: (row: TableRow): VNodeChild => {
      const item = asRow(row)
      if (item.tokensText === FMT_EMPTY) return h('span', { class: 'lt-faint' }, FMT_EMPTY)
      return h(TitleTip, { content: String((item.tokensText) ?? '') }, { default: () => h('div', { class: 'lt-cell lt-cell--end' }, [
        h('span', { class: 'lt-num' }, fmtInt(item.total_tokens)),
        h(
          'small',
          { class: 'lt-sub tnum' },
          t('page.logs.col.tokenIn', { value: fmtInt(item.prompt_tokens) }) +
            ' · ' +
            t('page.logs.col.tokenOut', { value: fmtInt(item.completion_tokens) }),
        ),
      ]) })
    },
  },
  {
    key: 'cache_rate',
    title: t('page.logs.col.cacheRate'),
    mobile: 'hidden',
    width: 80,
    numeric: true,
    render: (row: TableRow): VNodeChild => {
      const item = asRow(row)
      return h('span', { class: item.cacheRate === null ? 'lt-faint' : 'lt-num' }, item.cacheRateText)
    },
  },
  {
    key: 'credits',
    title: t('page.logs.col.credits'),
    mobile: 'meta',
    width: 84,
    numeric: true,
    render: (row: TableRow): VNodeChild => {
      const item = asRow(row)
      return item.credits > 0
        ? h('span', { class: 'lt-num' }, fmtCredit(item.credits))
        : h('span', { class: 'lt-faint' }, FMT_EMPTY)
    },
  },
  {
    key: 'key_name',
    title: 'Key',
    mobile: 'hidden',
    width: 140,
    render: (row: TableRow): VNodeChild => {
      const item = asRow(row)
      return h(TitleTip, { content: String((item.key_name) ?? '') }, { default: () => h('span', { class: 'lt-ellipsis' }, item.key_name || FMT_EMPTY) })
    },
  },
  {
    key: 'platform_account',
    title: t('page.logs.col.accountPlatform'),
    mobile: 'meta',
    width: 180,
    render: (row: TableRow): VNodeChild => {
      const item = asRow(row)
      return h('div', { class: 'lt-cell' }, [
        h(TitleTip, { content: String((item.account_name) ?? '') }, { default: () => h('span', { class: 'lt-ellipsis' }, item.account_name || FMT_EMPTY) }),
        h('small', { class: 'lt-sub' }, item.platform ? props.platformLabel(item.platform) : FMT_EMPTY),
      ])
    },
  },
  {
    key: 'client_ip',
    title: t('page.logs.col.clientIp'),
    mobile: 'hidden',
    width: 136,
    render: (row: TableRow): VNodeChild => {
      const item = asRow(row)
      return item.client_ip
        ? h(TitleTip, { content: String((item.client_ip) ?? '') }, { default: () => h('span', { class: 'lt-mono' }, item.client_ip) })
        : h('span', { class: 'lt-faint' }, FMT_EMPTY)
    },
  },
  {
    key: 'actions',
    title: '',
    mobile: 'actions',
    width: 64,
    hideable: false,
    fixed: 'right',
    render: (row: TableRow): VNodeChild =>
      h(
        'button',
        {
          type: 'button',
          class: 'lt-link',
          'aria-label': t('page.logs.col.detailAria'),
          onClick: (event: MouseEvent) => {
            event.stopPropagation()
            emit('open', asRow(row))
          },
        },
        t('page.logs.col.detail'),
      ),
  },
])

function onRowClick(row: TableRow): void {
  emit('open', asRow(row))
}
</script>

<template>
  <section class="lt">
    <ErrorBlock
      v-if="error && rows === null"
      :title="t('page.logs.error.loadList', { error: error.message })"
      :detail="error.detail"
      @retry="emit('retry')"
    />
    <template v-else>
      <DataTable
        table-id="logs"
        :columns="columns"
        :data="(rows || []) as unknown as TableRow[]"
        row-key="id"
        :loading="loading"
        :refreshing="refreshing"
        :pagination="false"
        :virtual-scroll="true"
        max-height="calc(100vh - 260px)"
        clickable-rows
        :empty-description="t('page.logs.table.empty')"
        @row-click="onRowClick"
      >
        <template #toolbar>
          <slot name="toolbar" />
        </template>
        <template #empty>
          <Empty :description="filtered ? t('page.logs.table.emptyFiltered') : t('page.logs.table.empty')">
            <Btn v-if="filtered" variant="default" size="sm" @click="emit('reset')">
              {{ t('page.logs.clearFilters') }}
            </Btn>
          </Empty>
        </template>
      </DataTable>
      <footer v-if="rows && (rows.length || page > 1)" class="lt-pager">
        <span class="lt-pager__info tnum">{{ pagerInfo }}</span>
        <div class="lt-pager__actions">
          <Btn size="sm" :icon="ChevronLeft" :disabled="page < 2" @click="emit('page', page - 1)">
            {{ t('page.logs.pager.prev') }}
          </Btn>
          <Btn size="sm" :disabled="!hasMore" @click="emit('page', page + 1)">
            {{ t('page.logs.pager.next') }}
            <ChevronRight :size="14" />
          </Btn>
        </div>
      </footer>
    </template>
  </section>
</template>

<style scoped>
.lt {
  display: grid;
  gap: var(--sp-2);
  min-width: 0;
}

/* 工具栏占满左侧，计数与「重置」才能贴到最右（DataTable 自带的密度 / 列设置在其后） */
.lt :deep(.mh-table__toolbar-left) {
  flex: 1;
}

.lt :deep(.lt-cell) {
  display: grid;
  gap: 2px;
  min-width: 0;
}

.lt :deep(.lt-cell--end) {
  justify-items: end;
}

.lt :deep(.lt-model) {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--font-mono);
  font-weight: 500;
  color: var(--fg);
}

.lt :deep(.lt-sub) {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--fs-tiny);
  line-height: var(--lh-tiny);
  color: var(--fg-muted);
}

.lt :deep(.lt-time) {
  color: var(--fg-2);
  white-space: nowrap;
}

.lt :deep(.lt-num) {
  font-variant-numeric: tabular-nums;
  color: var(--fg);
  white-space: nowrap;
}

.lt :deep(.lt-faint) {
  color: var(--fg-faint);
}

.lt :deep(.lt-ellipsis) {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--fg-2);
}

.lt :deep(.lt-mono) {
  font-family: var(--font-mono);
  color: var(--fg-2);
  white-space: nowrap;
}

.lt :deep(.lt-badge) {
  display: inline-flex;
  max-width: 100%;
}

.lt :deep(.lt-link) {
  border: 0;
  padding: 0;
  background: transparent;
  color: var(--brand);
  font-size: var(--fs-small);
  cursor: pointer;
  opacity: 0;
  transition: opacity var(--t-fast) var(--ease);
}

/* 行操作：桌面悬停 / 键盘聚焦时出现，触屏一直显示（10-visual-refresh §1.5） */
.lt :deep(tr:hover .lt-link),
.lt :deep(.lt-link:focus-visible) {
  opacity: 1;
}

@media (hover: none) {
  .lt :deep(.lt-link) {
    opacity: 1;
  }
}

.lt-pager {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-2);
  padding: var(--sp-1) 0;
}

.lt-pager__info {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.lt-pager__actions {
  display: flex;
  align-items: center;
  gap: var(--sp-1);
}
</style>
