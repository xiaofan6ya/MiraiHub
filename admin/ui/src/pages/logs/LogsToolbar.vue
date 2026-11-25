<script setup lang="ts">
/**
 * 用量日志的一行工具栏（10-visual-refresh §1.5：表格上方空白 ≤56px）。
 *
 * 常用筛选直接露出：模型搜索 · 结果（全部 / 成功 / 失败）· 时间范围 · 平台 · 账号；
 * 低频筛选（Key、客户端 IP、自定义日期、快速翻页）收进「更多筛选」浮层，生效后在同一行显示可移除的 chip。
 * 右侧是「共 N 条」和「重置」。原来的 FilterBar 卡片 + 单独一行统计 + 单独一行表格图标合并到这一行。
 *
 * 文本输入 300ms 防抖后才写进路由（与 FilterBar 同一口径）。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { NCheckbox, NDatePicker, NInput, NPopover, NSelect, type SelectOption } from 'naive-ui'
import { RotateCw, Search, SlidersHorizontal, X } from 'lucide-vue-next'
import { Btn, HelpTip, Segmented } from '@/components'
import { useI18n } from '@/i18n'
import type { LogFilterState } from '@/api/logs'

/** NSelect 的选项形态；本地只用到 label/value，但必须兼容 naive 的联合类型。 */
type Option = SelectOption

const props = withDefaults(
  defineProps<{
    filters: LogFilterState
    platformOptions: Option[]
    accountOptions: Option[]
    rangeOptions: Option[]
    accountsLoading?: boolean
    /** 右侧计数：「共 1,234 条」/「第 2 页」 */
    countText?: string
    /** 当前账号名筛选（?account= 从别的页面带进来）以外的账号显示名 */
    accountLabel?: string
  }>(),
  { accountsLoading: false, countText: '', accountLabel: '' },
)

const emit = defineEmits<{
  (event: 'set', key: keyof LogFilterState, value: string): void
  (event: 'setMany', values: Partial<LogFilterState>): void
  (event: 'reset'): void
}>()

const { t } = useI18n()

/* ---------- 防抖文本输入 ---------- */
type TextKey = 'model' | 'key' | 'ip'
const draft = ref<Record<TextKey, string>>({
  model: props.filters.model,
  key: props.filters.key,
  ip: props.filters.ip,
})
const timers: Partial<Record<TextKey, ReturnType<typeof setTimeout>>> = {}

watch(
  () => [props.filters.model, props.filters.key, props.filters.ip] as const,
  ([model, key, ip]) => {
    // 路由变化（重置 / 移除 chip / 前进后退）时同步输入框；正在输入的那一项等防抖结束再说。
    if (!timers.model) draft.value.model = model
    if (!timers.key) draft.value.key = key
    if (!timers.ip) draft.value.ip = ip
  },
)

function typeText(name: TextKey, value: string): void {
  draft.value[name] = value ?? ''
  const pending = timers[name]
  if (pending) clearTimeout(pending)
  timers[name] = setTimeout(() => {
    delete timers[name]
    const next = (draft.value[name] || '').trim()
    if (next !== props.filters[name]) emit('set', name, next)
  }, 300)
}

onBeforeUnmount(() => {
  for (const timer of Object.values(timers)) if (timer) clearTimeout(timer)
})

/* ---------- 结果分段 ---------- */
const resultOptions = computed(() => [
  { label: t('page.logs.filter.resultAll'), value: '' },
  { label: t('page.logs.filter.resultSuccess'), value: 'success' },
  { label: t('page.logs.filter.resultError'), value: 'error' },
])

/* ---------- 更多筛选 ---------- */
const moreCount = computed(() => {
  const f = props.filters
  return [f.key, f.ip, f.start || f.end, f.fast === '1' ? '1' : '', f.account].filter(Boolean).length
})

interface MoreChip {
  key: string
  label: string
  value: string
}

const moreChips = computed<MoreChip[]>(() => {
  const f = props.filters
  const list: MoreChip[] = []
  if (f.account) list.push({ key: 'account', label: t('page.logs.chip.accountName'), value: f.account })
  if (f.key) list.push({ key: 'key', label: 'Key', value: f.key })
  if (f.ip) list.push({ key: 'ip', label: t('page.logs.chip.clientIp'), value: f.ip })
  if (f.start || f.end) {
    list.push({ key: 'start', label: t('page.logs.chip.date'), value: (f.start || '…') + ' ~ ' + (f.end || '…') })
  }
  if (f.fast === '1') list.push({ key: 'fast', label: t('page.logs.chip.fast'), value: t('page.logs.chip.fastValue') })
  return list
})

function removeChip(key: string): void {
  if (key === 'start') {
    emit('setMany', { start: '', end: '' })
    return
  }
  if (key === 'key' || key === 'ip') {
    const pending = timers[key]
    if (pending) clearTimeout(pending)
    delete timers[key]
    draft.value[key] = ''
  }
  emit('set', key as keyof LogFilterState, '')
}

const hasAnyFilter = computed(() => {
  const f = props.filters
  return Boolean(
    f.platform || f.account || f.account_id || f.model || f.key || f.result || f.ip || f.start || f.end || f.fast ||
      (f.range && f.range !== 'today'),
  )
})

/** 自定义日期生效时，时间范围下拉显示为「自定义日期」并禁用（显式日期优先于 range）。 */
const rangeValue = computed(() => (props.filters.start ? '__custom' : props.filters.range || 'today'))
const rangeSelectOptions = computed(() =>
  props.filters.start
    ? [{ label: t('page.logs.filter.customRange'), value: '__custom' }, ...props.rangeOptions]
    : props.rangeOptions,
)

function onRange(value: string | number | null): void {
  const next = String(value ?? '')
  if (!next || next === '__custom') return
  if (props.filters.start || props.filters.end) emit('setMany', { range: next, start: '', end: '' })
  else emit('set', 'range', next)
}

function selectValue(value: string | number | null): string {
  return value === null || value === undefined ? '' : String(value)
}
</script>

<template>
  <div class="logs-toolbar" role="search" :aria-label="t('page.logs.filter.aria')">
    <NInput
      class="logs-toolbar__search"
      :value="draft.model"
      :placeholder="t('page.logs.filter.modelPlaceholder')"
      clearable
      :aria-label="t('page.logs.filter.modelAria')"
      @update:value="(value: string) => typeText('model', value)"
    >
      <template #prefix><Search :size="14" class="logs-toolbar__icon" /></template>
    </NInput>

    <Segmented
      :options="resultOptions"
      :model-value="filters.result"
      :aria-label="t('page.logs.filter.resultAria')"
      @update:model-value="(value: string | number) => emit('set', 'result', String(value))"
    />

    <NSelect
      class="logs-toolbar__range"
      :value="rangeValue"
      :options="rangeSelectOptions"
      :aria-label="t('page.logs.filter.rangeAria')"
      @update:value="onRange"
    />

    <NSelect
      class="logs-toolbar__platform"
      :value="filters.platform"
      :options="platformOptions"
      :aria-label="t('page.logs.filter.platformAria')"
      @update:value="(value: string | number | null) => emit('set', 'platform', selectValue(value))"
    />

    <NSelect
      class="logs-toolbar__account"
      :value="filters.account_id"
      :options="accountOptions"
      :loading="accountsLoading"
      filterable
      :aria-label="t('page.logs.filter.accountAria')"
      @update:value="(value: string | number | null) => emit('set', 'account_id', selectValue(value))"
    />

    <NPopover trigger="click" placement="bottom-start" :show-arrow="false" :width="320">
      <template #trigger>
        <Btn variant="default" :icon="SlidersHorizontal" :aria-label="t('page.logs.filter.more')">
          {{ t('page.logs.filter.more') }}
          <span v-if="moreCount" class="logs-toolbar__count tnum">{{ moreCount }}</span>
        </Btn>
      </template>
      <div class="logs-more">
        <label class="logs-more__field">
          <span>Key</span>
          <NInput
            :value="draft.key"
            :placeholder="t('page.logs.filter.keyPlaceholder')"
            clearable
            aria-label="Key"
            @update:value="(value: string) => typeText('key', value)"
          />
        </label>
        <label class="logs-more__field">
          <span>{{ t('page.logs.filter.clientIpAria') }}</span>
          <NInput
            :value="draft.ip"
            :placeholder="t('page.logs.filter.ipPlaceholder')"
            clearable
            :aria-label="t('page.logs.filter.clientIpAria')"
            @update:value="(value: string) => typeText('ip', value)"
          />
        </label>
        <div class="logs-more__field">
          <span>{{ t('page.logs.filter.dateLabel') }}</span>
          <div class="logs-more__dates">
            <NDatePicker
              type="date"
              value-format="yyyy-MM-dd"
              :formatted-value="filters.start || null"
              clearable
              :aria-label="t('page.logs.filter.startAria')"
              :placeholder="t('page.logs.filter.startPlaceholder')"
              @update:formatted-value="(value: string | null) => emit('set', 'start', value || '')"
            />
            <NDatePicker
              type="date"
              value-format="yyyy-MM-dd"
              :formatted-value="filters.end || null"
              clearable
              :aria-label="t('page.logs.filter.endAria')"
              :placeholder="t('page.logs.filter.endPlaceholder')"
              @update:formatted-value="(value: string | null) => emit('set', 'end', value || '')"
            />
          </div>
        </div>
        <div class="logs-more__check">
          <NCheckbox
            :checked="filters.fast === '1'"
            @update:checked="(checked: boolean) => emit('set', 'fast', checked ? '1' : '')"
          >
            {{ t('page.logs.filter.fastLabel') }}
          </NCheckbox>
          <HelpTip :content="t('page.logs.filter.fastTitle')" :label="t('page.logs.filter.helpLabel')" />
        </div>
      </div>
    </NPopover>

    <button
      v-for="chip in moreChips"
      :key="chip.key"
      type="button"
      class="logs-toolbar__chip"
      :aria-label="t('filter.remove', { label: chip.label })"
      @click="removeChip(chip.key)"
    >
      <span class="logs-toolbar__chip-label">{{ chip.label }}</span>
      <span class="logs-toolbar__chip-value">{{ chip.value }}</span>
      <X :size="14" />
    </button>

    <div class="logs-toolbar__end">
      <span v-if="countText" class="logs-toolbar__total tnum">{{ countText }}</span>
      <Btn v-if="hasAnyFilter" variant="quiet" size="sm" :icon="RotateCw" @click="emit('reset')">
        {{ t('filter.reset') }}
      </Btn>
    </div>
  </div>
</template>

<style scoped>
.logs-toolbar {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
  flex: 1;
  min-width: 0;
}

.logs-toolbar__search {
  width: 220px;
}

.logs-toolbar__icon {
  color: var(--fg-faint);
}

.logs-toolbar__range {
  width: 150px;
}

.logs-toolbar__platform {
  width: 140px;
}

.logs-toolbar__account {
  width: 220px;
}

.logs-toolbar__count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  margin-left: var(--sp-1);
  padding: 0 5px;
  border-radius: 9px;
  background: var(--brand-soft);
  color: var(--brand);
  font-size: var(--fs-tiny);
  line-height: 1;
}

.logs-toolbar__chip {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-1);
  height: 26px;
  max-width: 260px;
  padding: 0 var(--sp-2);
  border: 1px solid var(--border-soft);
  border-radius: var(--r-chip);
  background: var(--surface-2);
  color: var(--fg-2);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  cursor: pointer;
}

.logs-toolbar__chip:hover {
  border-color: var(--border-strong);
  color: var(--fg);
}

.logs-toolbar__chip-label {
  color: var(--fg-muted);
}

.logs-toolbar__chip-value {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--fg);
  font-weight: 500;
}

.logs-toolbar__end {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-2);
  margin-left: auto;
}

.logs-toolbar__total {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  white-space: nowrap;
}

.logs-more {
  display: grid;
  gap: var(--sp-3);
}

.logs-more__field {
  display: grid;
  gap: var(--sp-1);
}

.logs-more__field > span {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.logs-more__dates {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--sp-2);
}

.logs-more__check {
  display: flex;
  align-items: center;
  gap: var(--sp-1);
}

@media (max-width: 640px) {
  .logs-toolbar__search,
  .logs-toolbar__range,
  .logs-toolbar__platform,
  .logs-toolbar__account {
    width: 100%;
  }

  .logs-toolbar__end {
    margin-left: 0;
  }
}
</style>
