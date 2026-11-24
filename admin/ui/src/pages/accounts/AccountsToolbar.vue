<script setup lang="ts">
/**
 * 账号池 · 工具栏（放在表格工具栏的左侧插槽里，整张表上方只有这一行，10-visual-refresh §1.5）。
 *
 * 左：状态页签（带计数）+ 搜索 + 积分区间；右：「共 N 个 · 可用 M · 剩余 X」统计与重置。
 * 搜索 300ms 防抖后才写进路由 query（列表是客户端筛选，不发请求，但路由写入要节流）。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { NInput } from 'naive-ui'
import { RotateCcw, Search } from 'lucide-vue-next'
import { Btn, Segmented, type SegmentedOption } from '@/components'
import { useI18n } from '@/i18n'
import { fmtInt } from '@/utils/format'
import type { AccountStatusTab, AccountTabCounts } from '@/api/accounts'

const props = withDefaults(
  defineProps<{
    status: AccountStatusTab
    counts: AccountTabCounts
    q: string
    min: string
    max: string
    summary?: string
    hasFilters?: boolean
  }>(),
  { summary: '', hasFilters: false },
)

const emit = defineEmits<{
  (event: 'update:status', value: AccountStatusTab): void
  (event: 'update:q', value: string): void
  (event: 'update:min', value: string): void
  (event: 'update:max', value: string): void
  (event: 'reset'): void
}>()

const { t } = useI18n()

const STATUS_LABEL_KEY = {
  all: 'status.account.all',
  active: 'status.account.active',
  disabled: 'status.account.disabled',
  cooling: 'status.account.cooling',
  breaker: 'status.account.breaker',
} as const

const STATUS_ORDER: AccountStatusTab[] = ['all', 'active', 'cooling', 'breaker', 'disabled']

const statusOptions = computed<Array<SegmentedOption<string>>>(() =>
  STATUS_ORDER.map((key) => ({
    label: t(STATUS_LABEL_KEY[key]) + ' ' + fmtInt(props.counts[key]),
    value: key,
  })),
)

/* 搜索：本地值立即响应输入，300ms 后才同步给页面（路由）。 */
const search = ref(props.q)
watch(
  () => props.q,
  (value) => {
    if (value !== search.value) search.value = value
  },
)

let timer: ReturnType<typeof setTimeout> | null = null
function onSearch(value: string): void {
  search.value = value
  if (timer) clearTimeout(timer)
  timer = setTimeout(() => {
    timer = null
    emit('update:q', value || '')
  }, 300)
}

/** 积分区间只接受非负数字；其它输入（如 "abc"）当作清空，避免筛出 0 条却看不出原因。 */
function cleanNumber(value: string): string {
  const text = String(value || '').trim()
  if (!text) return ''
  const n = Number(text)
  return Number.isFinite(n) && n >= 0 ? String(n) : ''
}

function onStatus(value: string | number): void {
  emit('update:status', String(value) as AccountStatusTab)
}

function reset(): void {
  if (timer) clearTimeout(timer)
  timer = null
  search.value = ''
  emit('reset')
}

onBeforeUnmount(() => {
  if (timer) clearTimeout(timer)
})
</script>

<template>
  <div class="acc-toolbar">
    <Segmented
      class="acc-toolbar__tabs"
      :options="statusOptions"
      :model-value="status"
      :aria-label="t('page.accounts.statusAria')"
      scrollable
      @update:model-value="onStatus"
    />
    <n-input
      class="acc-toolbar__search"
      size="small"
      :value="search"
      :placeholder="t('page.accounts.searchPlaceholder')"
      clearable
      @update:value="onSearch"
    >
      <template #prefix><Search :size="14" /></template>
    </n-input>
    <div class="acc-toolbar__range" role="group" :aria-label="t('page.accounts.rangeAria')">
      <n-input
        class="acc-toolbar__num"
        size="small"
        :value="min"
        :placeholder="t('page.accounts.minPlaceholder')"
        clearable
        @update:value="(value: string) => emit('update:min', cleanNumber(value))"
      />
      <span class="acc-toolbar__dash" aria-hidden="true">–</span>
      <n-input
        class="acc-toolbar__num"
        size="small"
        :value="max"
        :placeholder="t('page.accounts.maxPlaceholder')"
        clearable
        @update:value="(value: string) => emit('update:max', cleanNumber(value))"
      />
    </div>
    <Btn v-if="hasFilters" variant="quiet" size="sm" :icon="RotateCcw" @click="reset">
      {{ t('common.reset') }}
    </Btn>
    <span v-if="summary" class="acc-toolbar__summary tnum">{{ summary }}</span>
  </div>
</template>

<style scoped>
.acc-toolbar {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
  min-width: 0;
  width: 100%;
}

.acc-toolbar__tabs {
  flex: none;
  max-width: 100%;
}

.acc-toolbar__search {
  width: 220px;
}

.acc-toolbar__range {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-1);
}

.acc-toolbar__num {
  width: 92px;
}

.acc-toolbar__dash {
  color: var(--fg-faint);
}

.acc-toolbar__summary {
  margin-left: auto;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  white-space: nowrap;
}

@media (max-width: 767px) {
  .acc-toolbar__search {
    width: 100%;
  }

  .acc-toolbar__range {
    width: 100%;
  }

  .acc-toolbar__num {
    flex: 1;
    width: auto;
  }

  .acc-toolbar__summary {
    margin-left: 0;
    white-space: normal;
  }
}
</style>
