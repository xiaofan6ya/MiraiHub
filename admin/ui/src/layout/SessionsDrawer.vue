<script setup lang="ts">
import { useIslandMessage as useMessage } from '@/components/feedback/islandQueue'
import { computed, ref, watch } from 'vue'
import { NDrawer, NDrawerContent, useDialog } from 'naive-ui'
import {
  banSession,
  listSessions,
  revokeOtherSessions,
  revokeSession,
  sessionsSummary,
  unbanDevice,
  type SessionsPayload,
} from '@/api/session'
import { isPhone } from './shellState'
import { useI18n } from '@/i18n'

/**
 * 登录设备抽屉（06-pages-d P25）。
 * 旧版 `openSessionsPanel()`：允许多设备在线；这里能踢下线、拉黑、一键踢其它。
 */
const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (event: 'update:open', value: boolean): void }>()

const message = useMessage()
const dialog = useDialog()
const { t } = useI18n()

const payload = ref<SessionsPayload | null>(null)
const loading = ref(false)
const error = ref('')
const busySid = ref('')

const show = computed({
  get: () => props.open,
  set: (value: boolean) => emit('update:open', value),
})
const width = computed(() => (isPhone.value ? '100%' : 480))
const summary = computed(() => sessionsSummary(payload.value, t))

/** 当前设备置顶，其余按后端顺序（最近活跃排前面）。 */
const rows = computed(() => {
  const items = payload.value?.items ?? []
  return [...items].sort((a, b) => Number(b.current) - Number(a.current))
})
const bans = computed(() => payload.value?.bans ?? [])

function formatTime(value: string | null): string {
  if (!value) return '—'
  return String(value).replace('T', ' ').slice(0, 19)
}

function stateBadge(row: { state: string; current: boolean }): { text: string; tone: string } {
  if (row.current) return { text: t('sessions.current'), tone: 'ok' }
  if (row.state === 'active') return { text: t('sessions.online'), tone: 'ok' }
  if (row.state === 'revoked') return { text: t('sessions.revoked'), tone: 'warn' }
  return { text: t('sessions.expired'), tone: 'warn' }
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    payload.value = await listSessions()
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    loading.value = false
  }
}

watch(
  () => props.open,
  (open) => {
    if (open) void load()
  },
  // 外壳第一次打开时才挂载本组件（按需加载），挂载那一刻 open 已经是 true。
  { immediate: true },
)

async function kick(sid: string): Promise<void> {
  busySid.value = sid
  try {
    await revokeSession(sid)
    message.success(t('sessions.kickSuccess'))
    await load()
  } catch (err) {
    message.error(t('sessions.kickFailed', { error: err instanceof Error ? err.message : String(err) }))
  } finally {
    busySid.value = ''
  }
}

function ban(sid: string): void {
  dialog.warning({
    title: t('sessions.banTitle'),
    content: t('sessions.banBody'),
    positiveText: t('sessions.banPositive'),
    negativeText: t('common.cancel'),
    onPositiveClick: async () => {
      busySid.value = sid
      try {
        await banSession(sid)
        message.success(t('sessions.banSuccess'))
        await load()
      } catch (err) {
        message.error(t('sessions.banFailed', { error: err instanceof Error ? err.message : String(err) }))
      } finally {
        busySid.value = ''
      }
    },
  })
}

function kickOthers(): void {
  dialog.warning({
    title: t('sessions.kickOthersTitle'),
    content: t('sessions.kickOthersBody'),
    positiveText: t('sessions.kickOthersPositive'),
    negativeText: t('common.cancel'),
    onPositiveClick: async () => {
      try {
        const result = await revokeOtherSessions()
        message.success(t('sessions.kickedCount', { count: result.revoked || 0 }))
        await load()
      } catch (err) {
        message.error(t('sessions.operationFailed', { error: err instanceof Error ? err.message : String(err) }))
      }
    },
  })
}

async function unban(banId: number): Promise<void> {
  try {
    await unbanDevice(banId)
    message.success(t('sessions.unbanSuccess'))
    await load()
  } catch (err) {
    message.error(t('sessions.unbanFailed', { error: err instanceof Error ? err.message : String(err) }))
  }
}
</script>

<template>
  <n-drawer v-model:show="show" placement="right" :width="width" :block-scroll="true">
    <n-drawer-content
      :title="t('sessions.title')"
      closable
      :native-scrollbar="false"
      body-content-style="padding: 0 20px 20px"
    >
      <p class="sess-note">{{ t('sessions.subtitle') }}</p>

      <div v-if="loading && !payload" class="sess-state">{{ t('sessions.loading') }}</div>
      <div v-else-if="error" class="sess-state error">
        <span>{{ t('sessions.loadFailed', { error }) }}</span>
        <button type="button" class="sess-btn" @click="load">{{ t('sessions.retry') }}</button>
      </div>
      <div v-else-if="!rows.length" class="sess-state">{{ t('sessions.empty') }}</div>

      <ul v-else class="sess-list">
        <li v-for="row in rows" :key="row.sid" class="sess-row">
          <div class="sess-body">
            <div class="sess-title">
              <span class="sess-name">{{ row.label || t('sessions.unknownDevice') }}</span>
              <span class="badge" :class="'badge-' + stateBadge(row).tone">
                {{ stateBadge(row).text }}
              </span>
            </div>
            <div class="sess-meta">
              <span>{{ row.ip || '—' }}</span>
              <span class="dot-sep">·</span>
              <span>{{ t('sessions.loginAt', { time: formatTime(row.created_at) }) }}</span>
              <span class="dot-sep">·</span>
              <span>{{ t('sessions.lastSeen', { time: formatTime(row.last_seen_at) }) }}</span>
            </div>
          </div>
          <div v-if="row.state === 'active' && !row.current" class="sess-ops">
            <button
              type="button"
              class="sess-btn"
              :disabled="busySid === row.sid"
              @click="kick(row.sid)"
            >
              {{ t('sessions.kick') }}
            </button>
            <button
              type="button"
              class="sess-btn danger"
              :disabled="busySid === row.sid"
              @click="ban(row.sid)"
            >
              {{ t('sessions.ban') }}
            </button>
          </div>
          <span v-else-if="row.current" class="sess-current">{{ t('sessions.currentDevice') }}</span>
        </li>
      </ul>

      <div v-if="bans.length" class="sess-bans">
        <p class="sess-bans-title">{{ t('sessions.banned') }}</p>
        <div v-for="item in bans" :key="item.id" class="sess-ban-row">
          <span class="sess-ban-text">
            {{ item.label || t('sessions.unknownDevice') }} · {{ item.ip || '—' }} · {{ item.reason || '' }}
          </span>
          <button type="button" class="sess-btn" @click="unban(item.id)">{{ t('sessions.unban') }}</button>
        </div>
      </div>

      <template #footer>
        <div class="sess-footer">
          <span class="sess-summary">{{ summary }}</span>
          <div class="sess-footer-ops">
            <button type="button" class="sess-btn" @click="load">{{ t('sessions.refresh') }}</button>
            <button type="button" class="sess-btn danger" @click="kickOthers">{{ t('sessions.kickOthers') }}</button>
          </div>
        </div>
      </template>
    </n-drawer-content>
  </n-drawer>
</template>

<style scoped>
.sess-note {
  margin: 0 0 var(--sp-3);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}

.sess-state {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  padding: var(--sp-4) 0;
  font-size: var(--fs-small);
  color: var(--fg-muted);
}

.sess-state.error {
  color: var(--danger);
}

.sess-list {
  list-style: none;
  margin: 0;
  padding: 0;
  border-top: 1px solid var(--border-soft);
}

.sess-row {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  padding: var(--sp-3) 0;
  border-bottom: 1px solid var(--border-soft);
}

.sess-body {
  min-width: 0;
  flex: 1 1 auto;
}

.sess-title {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
}

.sess-name {
  font-size: var(--fs-body);
  color: var(--fg);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.badge {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  height: 20px;
  padding: 0 6px;
  border-radius: var(--r-chip);
  font-size: var(--fs-tiny);
  line-height: var(--lh-tiny);
  font-weight: 500;
}

.badge-ok {
  color: var(--ok);
  background: var(--ok-soft);
}

.badge-warn {
  color: var(--warn);
  background: var(--warn-soft);
}

.sess-meta {
  margin-top: 2px;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  font-variant-numeric: tabular-nums;
  word-break: break-word;
}

.dot-sep {
  margin: 0 4px;
  color: var(--fg-faint);
}

.sess-ops {
  display: flex;
  gap: var(--sp-1);
  flex: 0 0 auto;
}

.sess-btn {
  height: 28px;
  padding: 0 10px;
  border: 1px solid var(--border-strong);
  border-radius: var(--r-ctl);
  background: var(--surface);
  color: var(--fg-2);
  font-size: var(--fs-small);
  cursor: pointer;
}

.sess-btn:hover:not(:disabled) {
  background: var(--surface-hover);
  color: var(--fg);
}

.sess-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.sess-btn.danger {
  color: var(--danger);
  border-color: var(--danger-soft);
}

.sess-current {
  flex: 0 0 auto;
  font-size: var(--fs-tiny);
  color: var(--fg-faint);
}

.sess-bans {
  margin-top: var(--sp-4);
}

.sess-bans-title {
  margin: 0 0 var(--sp-2);
  font-size: var(--fs-small);
  color: var(--fg-muted);
}

.sess-ban-row {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  padding: 6px 0;
  border-bottom: 1px solid var(--border-soft);
}

.sess-ban-text {
  flex: 1 1 auto;
  min-width: 0;
  font-size: var(--fs-small);
  color: var(--fg-2);
  word-break: break-word;
}

.sess-footer {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
  width: 100%;
}

.sess-summary {
  font-size: var(--fs-small);
  color: var(--fg-muted);
}

.sess-footer-ops {
  margin-left: auto;
  display: flex;
  gap: var(--sp-2);
}

@media (max-width: 640px) {
  .sess-row {
    align-items: flex-start;
    flex-wrap: wrap;
  }
  .sess-ops {
    width: 100%;
    justify-content: flex-end;
  }
}
</style>
