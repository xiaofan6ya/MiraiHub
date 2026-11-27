<script setup lang="ts">
/**
 * 概览 · 平台健康列表（v2.1）。
 *
 * 每个落库平台一行：logo · 名称 · 状态（圆点 + 文字，只在异常时着色）· 请求占比细条 ·
 * 请求数 · 成功率 · 账号「可用 / 总数」。0 账号且没有流量的平台折叠成最后一行「未接入 N 个」，
 * 不再给它们画绿点（第 3 轮审查 A4 的同一原则：常态不显示，只显示异常）。
 * 点行 = 切到该平台并打开平台管理（与线路带一致）。
 *
 * v2.2：加一行 11px 列头（平台 / 请求占比 / 请求 / 成功率 / 账号），数字列与列头右对齐、
 * 固定列宽，行高 44（与表格 --row-h 一致）；悬停只换底色不出边框；键盘 Tab 逐行、回车打开。
 *
 * r3 续跑：悬停 / 聚焦时行尾滑出 16px 箭头（提示可点）；异常行左侧 2px 色条；
 * 列表首次出现 160ms 淡入；按压 scale(.995)；减少动效时全部关闭。
 */
import { ChevronRight } from 'lucide-vue-next'
import { Card, Empty, Meter, PlatformLogo, TitleTip } from '@/components'
import { fmtInt } from '@/utils/format'
import type { OverviewHealth, OverviewHealthRow } from '@/api/overview'
import { useI18n } from '@/i18n'

defineProps<{
  health: OverviewHealth
  scope: string
  loading?: boolean
}>()

const emit = defineEmits<{ (event: 'select', id: string): void }>()

const { t } = useI18n()

function accountText(row: OverviewHealthRow): string {
  if (row.accounts === null) return '—'
  if (row.active === null) return t('page.overview.health.accounts', { count: fmtInt(row.accounts) })
  return t('page.overview.health.activeOf', { active: fmtInt(row.active), total: fmtInt(row.accounts) })
}
</script>

<template>
  <Card :title="t('page.overview.health.title')" :loading="loading">
    <template #extra>
      <span class="ov-health__scope">{{ scope }}</span>
      <RouterLink class="ov-health__link" to="/platforms">{{ t('page.overview.health.all') }}</RouterLink>
    </template>

    <Empty v-if="!health.rows.length && !health.idle.length" compact :description="t('page.overview.health.empty')" />

    <ul v-else class="ov-health">
      <li class="ov-health__head" aria-hidden="true">
        <span />
        <span>{{ t('page.overview.health.col.platform') }}</span>
        <span>{{ t('page.overview.health.col.share') }}</span>
        <span class="is-num">{{ t('page.overview.health.col.requests') }}</span>
        <span class="is-num">{{ t('page.overview.health.col.rate') }}</span>
        <span class="is-num ov-health__head-acc">{{ t('page.overview.health.col.accounts') }}</span>
      </li>
      <li v-for="row in health.rows" :key="row.id">
        <button
          type="button"
          class="ov-health__row"
          :class="{ 'is-alert': row.tone === 'danger' || row.tone === 'warn', ['is-' + row.tone]: true }"
          @click="emit('select', row.id)"
        >
          <PlatformLogo :id="row.logo || row.id" :size="20" :label="row.name" />
          <span class="ov-health__name">
            <b>{{ row.name }}</b>
            <span class="ov-health__status" :class="'is-' + row.tone">
              <i aria-hidden="true" />{{ row.status }}
            </span>
          </span>
          <span class="ov-health__bar">
            <Meter
              :value="row.share"
              :tone="row.tone === 'danger' ? 'danger' : 'brand'"
              :height="4"
              :aria-label="t('page.overview.health.shareAria', { name: row.name })"
            />
          </span>
          <TitleTip :content="t('page.overview.health.requestsTitle')"><span class="ov-health__num tnum">{{ fmtInt(row.requests) }}</span></TitleTip>
          <span
            class="ov-health__num tnum"
            :class="{ 'is-danger': row.tone === 'danger', 'is-faint': row.successRate === null }"
          >
            {{ row.rateText }}
          </span>
          <span class="ov-health__acc tnum" :class="{ 'is-warn': row.tone === 'warn' }">
            {{ accountText(row) }}
          </span>
          <ChevronRight class="ov-health__go" :size="16" aria-hidden="true" />
        </button>
      </li>
      <li v-if="health.idle.length" class="ov-health__idle">
        <TitleTip :content="health.idle.join(t('page.sentinel.listSeparator'))">{{ t('page.overview.health.idleRow', { count: health.idle.length }) }}</TitleTip>
      </li>
    </ul>
  </Card>
</template>

<style scoped>
.ov-health__scope {
  font-size: var(--fs-small);
  color: var(--fg-muted);
}

.ov-health__link {
  font-size: var(--fs-small);
  color: var(--brand);
  text-decoration: none;
}

.ov-health__link:hover {
  text-decoration: underline;
}

.ov-health {
  list-style: none;
  margin: calc(var(--sp-3) * -1) calc(var(--sp-2) * -1) 0;
  padding: 0;
  display: grid;
  animation: ov-health-in 160ms var(--ease-out, ease-out) both;
}

@keyframes ov-health-in {
  from {
    opacity: 0;
  }

  to {
    opacity: 1;
  }
}

.ov-health__head,
.ov-health__row {
  display: grid;
  grid-template-columns: 20px minmax(112px, 1.4fr) minmax(56px, 1fr) 72px 60px 88px;
  align-items: center;
  gap: var(--sp-3);
}

.ov-health__head {
  min-height: 28px;
  padding: 0 calc(var(--sp-2) + 20px) 0 var(--sp-2);
  border-bottom: 1px solid var(--border-soft);
  font-size: var(--fs-tiny);
  line-height: var(--lh-tiny);
  font-weight: 500;
  color: var(--fg-muted);
  white-space: nowrap;
}

.ov-health__head .is-num {
  text-align: right;
}

.ov-health__row {
  width: 100%;
  position: relative;
  min-height: var(--row-h, 44px);
  padding: 6px calc(var(--sp-2) + 20px) 6px var(--sp-2);
  border: 0;
  border-bottom: 1px solid var(--border-soft);
  border-radius: 0;
  background: transparent;
  color: var(--fg);
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition:
    background-color var(--dur-1, 120ms) var(--ease-out, ease-out),
    transform var(--dur-1, 120ms) var(--ease-out, ease-out);
}

.ov-health__row:hover {
  background: var(--surface-hover);
}

.ov-health__row:active {
  transform: scale(0.995);
}

/* 异常行：左侧 2px 色条（不改变行高、不出边框跳变） */
.ov-health__row.is-alert::before {
  content: '';
  position: absolute;
  left: 0;
  top: 8px;
  bottom: 8px;
  width: 2px;
  border-radius: 2px;
  background: var(--warn);
}

.ov-health__row.is-danger::before {
  background: var(--danger);
}

.ov-health__go {
  position: absolute;
  right: var(--sp-2);
  top: 50%;
  color: var(--fg-muted);
  opacity: 0;
  transform: translate(-4px, -50%);
  transition:
    opacity var(--dur-1, 120ms) var(--ease-out, ease-out),
    transform var(--dur-2, 180ms) var(--ease-out, ease-out);
}

.ov-health__row:hover .ov-health__go,
.ov-health__row:focus-visible .ov-health__go {
  opacity: 1;
  transform: translate(0, -50%);
}

@media (prefers-reduced-motion: reduce) {
  .ov-health,
  .ov-health__go {
    animation: none;
    transition: none;
  }

  .ov-health__row:active {
    transform: none;
  }
}

.ov-health__row:active {
  background: var(--surface-2);
}

.ov-health li:last-child .ov-health__row {
  border-bottom: 0;
}

.ov-health__row:focus-visible {
  outline: none;
  box-shadow: 0 0 0 3px var(--ring);
}

.ov-health__name {
  display: grid;
  min-width: 0;
}

.ov-health__name b {
  font-weight: 500;
  font-size: var(--fs-body);
  line-height: var(--lh-body);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ov-health__status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: var(--fs-tiny);
  line-height: var(--lh-tiny);
  color: var(--fg-muted);
}

.ov-health__status i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--fg-faint);
}

.ov-health__status.is-ok i {
  background: var(--ok);
}

.ov-health__status.is-warn {
  color: var(--warn);
}

.ov-health__status.is-warn i {
  background: var(--warn);
}

.ov-health__status.is-danger {
  color: var(--danger);
}

.ov-health__status.is-danger i {
  background: var(--danger);
}

.ov-health__num,
.ov-health__acc {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  text-align: right;
  color: var(--fg-2);
  white-space: nowrap;
}

.ov-health__num.is-danger {
  color: var(--danger);
}

.ov-health__num.is-faint {
  color: var(--fg-faint);
}

.ov-health__acc {
  color: var(--fg-muted);
}

.ov-health__acc.is-warn {
  color: var(--warn);
}

.ov-health__idle {
  padding: var(--sp-3) var(--sp-2) 0;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-faint);
}

/* 1024–1279：左栏只有约 450px，收起占比条列，避免横向溢出（11 §4-4）。 */
@media (max-width: 1279px) {
  .ov-health__head,
  .ov-health__row {
    grid-template-columns: 20px minmax(0, 1fr) 64px 56px 80px;
  }

  .ov-health__bar,
  .ov-health__head > :nth-child(3) {
    display: none;
  }
}

@media (max-width: 640px) {
  .ov-health__head,
  .ov-health__row {
    grid-template-columns: 20px minmax(0, 1fr) 64px 56px;
  }

  .ov-health__bar,
  .ov-health__acc,
  .ov-health__head > :nth-child(3),
  .ov-health__head-acc {
    display: none;
  }
}
</style>
