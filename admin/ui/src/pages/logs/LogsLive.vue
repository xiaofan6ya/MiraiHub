<script setup lang="ts">
import TitleTip from '@/components/base/TitleTip.vue'
/**
 * 请求动态（近 15 分钟，每分钟一根柱；横轴本地时区）+ 最近 5 条请求。
 *
 * 数据：/api/logs/requests 5 秒轮询（父组件负责轮询与暂停）。
 * 第 1 轮审查：15 根柱全空时只剩一排空槽、200 徽章被拉成长条、孤立的「m」列 ——
 * 这里改成带刻度和网格线的小柱图；全空时叠一行说明（含「最近一条记录在 x 前」），
 * 徽章在格子里左对齐、不拉伸。
 */
import { computed } from 'vue'
import { Pause, Play } from 'lucide-vue-next'
import { Badge, Btn, Card, ErrorBlock } from '@/components'
import { FMT_EMPTY, fmtDuration, fmtFullTime, fmtTime } from '@/utils/format'
import { useI18n } from '@/i18n'
import { liveStatusLabel, liveTone, niceCeil, type LiveBar, type LiveRequest } from '@/api/logs'

const props = withDefaults(
  defineProps<{
    bars: LiveBar[]
    recent: LiveRequest[]
    hint: string
    /** 连续失败自动暂停 */
    autoPaused?: boolean
    autoPausedMessage?: string
    userPaused?: boolean
    loading?: boolean
    /** 表格里最新一条记录的相对时间（全空时提示用；没有则空串） */
    lastSeenText?: string
  }>(),
  { autoPaused: false, autoPausedMessage: '', userPaused: false, loading: false, lastSeenText: '' },
)

const emit = defineEmits<{ (event: 'toggle'): void }>()

const { t } = useI18n()

const total = computed(() => props.bars.reduce((sum, bar) => sum + bar.requests, 0))
const errors = computed(() => props.bars.reduce((sum, bar) => sum + bar.errors, 0))
const top = computed(() => niceCeil(Math.max(0, ...props.bars.map((bar) => bar.requests))))
const paused = computed(() => props.userPaused || props.autoPaused)

/** 横轴只标首、尾和每 5 分钟一格，避免 15 个时间挤成一排。 */
function showTick(index: number): boolean {
  const last = props.bars.length - 1
  return index === 0 || index === last || (last - index) % 5 === 0
}

function heightOf(value: number): string {
  if (value <= 0) return '0%'
  return Math.max(4, (value / top.value) * 100) + '%'
}

function elapsedText(item: LiveRequest): string {
  return item.elapsed_ms > 0 ? fmtDuration(item.elapsed_ms) : FMT_EMPTY
}
</script>

<template>
  <Card class="logs-live" :loading="loading && !paused">
    <template #title>{{ t('page.logs.live.title') }}</template>
    <template #extra>
      <span class="logs-live__hint" :class="{ 'is-warn': autoPaused }">{{ hint }}</span>
      <Btn variant="quiet" size="sm" :icon="paused ? Play : Pause" @click="emit('toggle')">
        {{ paused ? t('page.logs.live.resume') : t('page.logs.live.pause') }}
      </Btn>
    </template>

    <ErrorBlock
      v-if="autoPaused"
      compact
      class="logs-live__paused"
      :title="autoPausedMessage || t('page.logs.live.pausedAuto')"
      :retry-label="t('page.logs.live.resumeAuto')"
      @retry="emit('toggle')"
    />

    <div class="logs-live__body">
      <div class="logs-chart">
        <div class="logs-chart__legend">
          <span class="logs-chart__key"><i class="is-req" />{{ t('page.logs.live.legendRequests') }}</span>
          <span class="logs-chart__key"><i class="is-err" />{{ t('page.logs.live.legendErrors') }}</span>
          <span class="logs-chart__sum tnum">
            {{ t('page.logs.live.sum', { total, errors }) }}
          </span>
        </div>
        <div class="logs-chart__plot">
          <div class="logs-chart__yaxis tnum" aria-hidden="true">
            <span>{{ top }}</span>
            <span>0</span>
          </div>
          <div class="logs-chart__area">
            <div class="logs-chart__grid" aria-hidden="true"><i /><i /><i /></div>
            <div class="logs-chart__bars" role="img" :aria-label="t('page.logs.live.barsAria')">
              <TitleTip v-for="bar in bars" :key="bar.at" :content="t('page.logs.live.barTitle', { label: bar.label, requests: bar.requests, errors: bar.errors })"><div
                class="logs-chart__col"
              >
                <div class="logs-chart__bar" :style="{ height: heightOf(bar.requests) }">
                  <div
                    v-if="bar.errors"
                    class="logs-chart__bar-err"
                    :style="{ height: Math.min(100, (bar.errors / Math.max(1, bar.requests)) * 100) + '%' }"
                  />
                </div>
              </div></TitleTip>
            </div>
            <p v-if="total === 0" class="logs-chart__empty">
              {{ t('page.logs.live.empty') }}
              <template v-if="lastSeenText">
                · {{ t('page.logs.live.lastSeen', { time: lastSeenText }) }}
              </template>
            </p>
          </div>
        </div>
        <div class="logs-chart__xaxis tnum" aria-hidden="true">
          <span v-for="(bar, index) in bars" :key="bar.at" class="logs-chart__tick">
            {{ showTick(index) ? bar.label : '' }}
          </span>
        </div>
      </div>

      <div class="logs-recent">
        <p class="logs-recent__title">{{ t('page.logs.live.recentTitle') }}</p>
        <ul v-if="recent.length" class="logs-recent__list">
          <li v-for="item in recent" :key="item.request_id" class="logs-recent__row">
            <TitleTip :content="fmtFullTime(item.started_at)"><span class="logs-recent__time tnum">
              {{ fmtTime(item.started_at) }}
            </span></TitleTip>
            <TitleTip :content="item.model || item.path"><span class="logs-recent__model">{{ item.model || item.path }}</span></TitleTip>
            <span class="logs-recent__status"><Badge :tone="liveTone(item)" dot>{{ liveStatusLabel(item) }}</Badge></span>
            <span class="logs-recent__elapsed tnum">{{ elapsedText(item) }}</span>
          </li>
        </ul>
        <p v-else class="logs-recent__empty">{{ t('page.logs.live.recentEmpty') }}</p>
      </div>
    </div>
  </Card>
</template>

<style scoped>
.logs-live__hint {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.logs-live__hint.is-warn {
  color: var(--warn);
}

.logs-live__paused {
  margin-bottom: var(--sp-3);
}

.logs-live__body {
  display: grid;
  grid-template-columns: minmax(0, 1.6fr) minmax(0, 1fr);
  gap: var(--sp-5);
  align-items: start;
}

/* ---------- 小柱图 ---------- */
.logs-chart {
  display: grid;
  gap: var(--sp-2);
  min-width: 0;
}

.logs-chart__legend {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.logs-chart__key {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.logs-chart__key i {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.logs-chart__key .is-req {
  background: var(--chart-1, var(--brand));
}

.logs-chart__key .is-err {
  background: var(--danger);
}

.logs-chart__sum {
  margin-left: auto;
  color: var(--fg-2);
}

.logs-chart__plot {
  display: grid;
  grid-template-columns: 28px minmax(0, 1fr);
  gap: var(--sp-2);
  height: 112px;
}

.logs-chart__yaxis {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  align-items: flex-end;
  font-size: var(--fs-tiny);
  line-height: var(--lh-tiny);
  color: var(--fg-faint);
}

.logs-chart__area {
  position: relative;
  min-width: 0;
  border-bottom: 1px solid var(--border);
}

.logs-chart__grid {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  pointer-events: none;
}

.logs-chart__grid i {
  display: block;
  border-top: 1px dashed var(--border-soft);
}

.logs-chart__grid i:last-child {
  border-top-color: transparent;
}

.logs-chart__bars {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: flex-end;
  gap: 4px;
}

.logs-chart__col {
  flex: 1;
  min-width: 0;
  height: 100%;
  display: flex;
  align-items: flex-end;
  justify-content: center;
}

.logs-chart__bar {
  position: relative;
  width: 100%;
  max-width: 24px;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  background: var(--chart-1, var(--brand));
  border-radius: 3px 3px 0 0;
  overflow: hidden;
  transition: height var(--t-base) var(--ease);
}

.logs-chart__bar-err {
  width: 100%;
  background: var(--danger);
}

.logs-chart__empty {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0;
  padding: 0 var(--sp-4);
  text-align: center;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.logs-chart__xaxis {
  display: flex;
  gap: 4px;
  padding-left: calc(28px + var(--sp-2));
  font-size: var(--fs-tiny);
  line-height: var(--lh-tiny);
  color: var(--fg-faint);
}

.logs-chart__tick {
  flex: 1;
  min-width: 0;
  text-align: center;
  white-space: nowrap;
  overflow: visible;
}

/* ---------- 最近请求 ---------- */
.logs-recent {
  display: grid;
  gap: var(--sp-2);
  min-width: 0;
}

.logs-recent__title {
  margin: 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.logs-recent__list {
  display: grid;
  margin: 0;
  padding: 0;
  list-style: none;
}

.logs-recent__row {
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr) auto 60px;
  align-items: center;
  gap: var(--sp-2);
  min-height: 28px;
  border-top: 1px solid var(--border-soft);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
}

.logs-recent__row:first-child {
  border-top: 0;
}

.logs-recent__time {
  color: var(--fg-muted);
  white-space: nowrap;
}

.logs-recent__model {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--font-mono);
  color: var(--fg);
}

.logs-recent__status {
  justify-self: start;
}

.logs-recent__elapsed {
  text-align: right;
  color: var(--fg-2);
}

.logs-recent__empty {
  margin: 0;
  font-size: var(--fs-small);
  color: var(--fg-faint);
}

@media (max-width: 1023px) {
  .logs-live__body {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (prefers-reduced-motion: reduce) {
  .logs-chart__bar {
    transition: none;
  }
}
</style>
