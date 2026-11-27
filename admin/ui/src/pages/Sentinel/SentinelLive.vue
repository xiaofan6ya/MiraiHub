<script setup lang="ts">
/**
 * 风控哨兵 · 实时请求列表（最多 50 行，新的在上）。
 * 时间显示相对时间，悬停显示本地时区绝对时间（10-visual-refresh §5）。
 * 轮询连续失败暂停时，底部给「恢复」。
 */
import { watch } from 'vue'
import { Btn, Card, Empty, TitleTip } from '@/components'
import { useFlashKeys } from '@/composables'
import type { LiveRow } from '@/api/sentinel'
import { useI18n } from '@/i18n'

const props = defineProps<{
  rows: LiveRow[]
  /** 轮询是否因连续失败暂停。 */
  paused: boolean
  pausedMessage: string
}>()

const emit = defineEmits<{ (event: 'resume'): void }>()

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
  <Card :title="t('page.sentinel.live.title')">
    <template #extra>
      <span class="sn-live__hint">
        {{ paused ? t('page.sentinel.live.paused') : t('page.sentinel.refreshEvery') }}
      </span>
    </template>
    <Empty v-if="!rows.length" compact :description="t('page.sentinel.live.empty')" />
    <ol v-else class="sn-live" aria-live="polite">
      <li v-for="row in rows" :key="row.rid" class="sn-live__row" :class="['is-' + row.tone, flash.rowClass(row.rid)]">
        <TitleTip :content="row.at"><span class="sn-live__time tnum">{{ row.time }}</span></TitleTip>
        <TitleTip :content="row.error || row.model"><span class="sn-live__model mono">{{ row.model }}</span></TitleTip>
        <span class="sn-live__plat">{{ row.platformLabel }}</span>
        <span class="sn-live__status tnum">
          <i class="sn-live__dot" aria-hidden="true" />{{ row.statusText }}
        </span>
      </li>
    </ol>
    <p v-if="paused" class="sn-live__warn">
      {{ pausedMessage }}
      <Btn variant="quiet" size="sm" @click="emit('resume')">{{ t('page.sentinel.resume') }}</Btn>
    </p>
  </Card>
</template>

<style scoped>
.sn-live__hint {
  font-size: var(--fs-small);
  color: var(--fg-muted);
}

.sn-live {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  max-height: 460px;
  overflow: auto;
}

.sn-live__row {
  display: grid;
  grid-template-columns: 64px minmax(0, 1fr) 84px 64px;
  gap: var(--sp-2);
  align-items: center;
  min-height: 32px;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  border-bottom: 1px solid var(--border-soft);
}

.sn-live__row:last-child {
  border-bottom: 0;
}

.sn-live__time {
  color: var(--fg-muted);
}

.sn-live__model {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--fg-2);
}

.sn-live__plat {
  color: var(--fg-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sn-live__status {
  display: inline-flex;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
  color: var(--fg-2);
}

.sn-live__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--fg-faint);
}

.sn-live__row.is-ok .sn-live__dot {
  background: var(--ok);
}

.sn-live__row.is-warn .sn-live__dot {
  background: var(--warn);
}

.sn-live__row.is-danger .sn-live__dot {
  background: var(--danger);
}

.sn-live__row.is-danger .sn-live__status {
  color: var(--danger);
}

.sn-live__row.is-brand .sn-live__dot {
  background: var(--brand);
}

.sn-live__warn {
  margin: var(--sp-2) 0 0;
  font-size: var(--fs-small);
  color: var(--warn);
  display: flex;
  align-items: center;
  gap: var(--sp-2);
}

@media (max-width: 640px) {
  .sn-live__row {
    grid-template-columns: 64px minmax(0, 1fr) 64px;
  }

  .sn-live__plat {
    display: none;
  }
}

/* 新行闪一下（11 §1：--brand-soft 900ms 淡出）。全局 .mh-row-flash 由设计线提供，这里给本列表兜底。 */
.sn-live__row.mh-row-flash {
  animation: sn-live-flash 900ms var(--ease-out, ease-out);
}

@keyframes sn-live-flash {
  from {
    background-color: var(--brand-soft);
  }

  to {
    background-color: transparent;
  }
}

@media (prefers-reduced-motion: reduce) {
  .sn-live__row.mh-row-flash {
    animation: none;
  }
}
</style>
