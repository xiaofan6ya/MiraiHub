<script setup lang="ts">
/**
 * 概览 · 最近请求（近 1 小时，全网关，取自 /api/sentinel/status 合并下发的 traffic.recent，
 * 不额外打接口）。最多 8 行：相对时间（悬停绝对时间）· 模型 · 平台 · 状态圆点 + 状态码。
 * 空时说明「近 1 小时没有请求」，并给去用量日志的入口 —— 避免长时间无流量时让人以为页面坏了（第 3 轮审查 C）。
 */
import { watch } from 'vue'
import { Card, Empty } from '@/components'
import { useFlashKeys } from '@/composables'
import type { LiveRow } from '@/api/sentinel'
import { useI18n } from '@/i18n'

const props = defineProps<{
  rows: LiveRow[]
  scope: string
  loading?: boolean
}>()

const { t } = useI18n()

/** 轮询带来的新请求行闪一下；首次渲染只记住，不闪。 */
const flash = useFlashKeys()
watch(
  () => props.rows,
  (rows) => flash.track(rows, (row) => row.rid),
  { immediate: true },
)
</script>

<template>
  <Card :title="t('page.overview.recent.title')" :loading="loading">
    <template #extra>
      <span class="ov-recent__scope">{{ scope }}</span>
      <RouterLink class="ov-recent__link" to="/logs">{{ t('page.overview.recent.all') }}</RouterLink>
    </template>
    <Empty v-if="!rows.length" compact :description="t('page.overview.recent.empty')" />
    <ol v-else class="ov-recent">
      <li v-for="row in rows" :key="row.rid" class="ov-recent__row" :class="['is-' + row.tone, flash.rowClass(row.rid)]">
        <span class="ov-recent__time tnum" :title="row.at">{{ row.time }}</span>
        <span class="ov-recent__model mono" :title="row.error || row.model">{{ row.model }}</span>
        <span class="ov-recent__plat">{{ row.platformLabel }}</span>
        <span class="ov-recent__status tnum"><i aria-hidden="true" />{{ row.statusText }}</span>
      </li>
    </ol>
  </Card>
</template>

<style scoped>
.ov-recent__scope {
  font-size: var(--fs-small);
  color: var(--fg-muted);
}

.ov-recent__link {
  font-size: var(--fs-small);
  color: var(--brand);
  text-decoration: none;
}

.ov-recent__link:hover {
  text-decoration: underline;
}

.ov-recent {
  list-style: none;
  margin: calc(var(--sp-2) * -1) 0 0;
  padding: 0;
  display: grid;
}

.ov-recent__row {
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr) minmax(0, 120px) 72px;
  align-items: center;
  gap: var(--sp-3);
  min-height: 36px;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  border-bottom: 1px solid var(--border-soft);
}

.ov-recent__row:last-child {
  border-bottom: 0;
}

.ov-recent__time {
  color: var(--fg-muted);
}

.ov-recent__model {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--fg-2);
}

.ov-recent__plat {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--fg-muted);
}

.ov-recent__status {
  display: inline-flex;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
  color: var(--fg-2);
}

.ov-recent__status i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--fg-faint);
}

.ov-recent__row.is-ok .ov-recent__status i {
  background: var(--ok);
}

.ov-recent__row.is-warn .ov-recent__status i {
  background: var(--warn);
}

.ov-recent__row.is-danger .ov-recent__status {
  color: var(--danger);
}

.ov-recent__row.is-danger .ov-recent__status i {
  background: var(--danger);
}

.ov-recent__row.is-brand .ov-recent__status i {
  background: var(--brand);
}

@media (max-width: 640px) {
  .ov-recent__row {
    grid-template-columns: 64px minmax(0, 1fr) 64px;
  }

  .ov-recent__plat {
    display: none;
  }
}

/* 新行闪一下（11 §1：--brand-soft 900ms 淡出）。全局 .mh-row-flash 由设计线提供，这里给本列表兜底。 */
.ov-recent__row.mh-row-flash {
  animation: ov-recent-flash 900ms var(--ease-out, ease-out);
}

@keyframes ov-recent-flash {
  from {
    background-color: var(--brand-soft);
  }

  to {
    background-color: transparent;
  }
}

@media (prefers-reduced-motion: reduce) {
  .ov-recent__row.mh-row-flash {
    animation: none;
  }
}
</style>
