<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { NDrawer, NDrawerContent, useDialog, useMessage } from 'naive-ui'
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

/**
 * 登录设备抽屉（06-pages-d P25）。
 * 旧版 `openSessionsPanel()`：允许多设备在线；这里能踢下线、拉黑、一键踢其它。
 */
const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (event: 'update:open', value: boolean): void }>()

const message = useMessage()
const dialog = useDialog()

const payload = ref<SessionsPayload | null>(null)
const loading = ref(false)
const error = ref('')
const busySid = ref('')

const show = computed({
  get: () => props.open,
  set: (value: boolean) => emit('update:open', value),
})
const width = computed(() => (isPhone.value ? '100%' : 480))
const summary = computed(() => sessionsSummary(payload.value))

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
  if (row.current) return { text: '本机', tone: 'ok' }
  if (row.state === 'active') return { text: '在线', tone: 'ok' }
  if (row.state === 'revoked') return { text: '已踢下线', tone: 'warn' }
  return { text: '已过期', tone: 'warn' }
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
)

async function kick(sid: string): Promise<void> {
  busySid.value = sid
  try {
    await revokeSession(sid)
    message.success('已踢下线')
    await load()
  } catch (err) {
    message.error('踢下线失败：' + (err instanceof Error ? err.message : String(err)))
  } finally {
    busySid.value = ''
  }
}

function ban(sid: string): void {
  dialog.warning({
    title: '拉黑这台设备？',
    content: '拉黑后这台机器的所有后续登录都会被拒绝（不只是当前这次）。可以在「已拉黑设备」里解除。',
    positiveText: '拉黑',
    negativeText: '取消',
    onPositiveClick: async () => {
      busySid.value = sid
      try {
        await banSession(sid)
        message.success('已拉黑该设备')
        await load()
      } catch (err) {
        message.error('拉黑失败：' + (err instanceof Error ? err.message : String(err)))
      } finally {
        busySid.value = ''
      }
    },
  })
}

function kickOthers(): void {
  dialog.warning({
    title: '踢掉其它设备？',
    content: '将踢掉除本机外的全部登录设备，它们在下一个请求时就要重新登录。',
    positiveText: '踢掉其它设备',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        const result = await revokeOtherSessions()
        message.success('已踢下线 ' + (result.revoked || 0) + ' 台设备')
        await load()
      } catch (err) {
        message.error('操作失败：' + (err instanceof Error ? err.message : String(err)))
      }
    },
  })
}

async function unban(banId: number): Promise<void> {
  try {
    await unbanDevice(banId)
    message.success('已解除拉黑')
    await load()
  } catch (err) {
    message.error('解除失败：' + (err instanceof Error ? err.message : String(err)))
  }
}
</script>

<template>
  <n-drawer v-model:show="show" placement="right" :width="width" :block-scroll="true">
    <n-drawer-content
      title="登录设备"
      closable
      :native-scrollbar="false"
      body-content-style="padding: 0 20px 20px"
    >
      <p class="sess-note">
        允许多台设备同时在线。凭证不可搬运：会话与设备标识绑定，把 Cookie 复制到另一台机器也过不了校验。
      </p>

      <div v-if="loading && !payload" class="sess-state">加载中…</div>
      <div v-else-if="error" class="sess-state error">
        <span>读取失败：{{ error }}</span>
        <button type="button" class="sess-btn" @click="load">重试</button>
      </div>
      <div v-else-if="!rows.length" class="sess-state">没有登录记录</div>

      <ul v-else class="sess-list">
        <li v-for="row in rows" :key="row.sid" class="sess-row">
          <div class="sess-body">
            <div class="sess-title">
              <span class="sess-name">{{ row.label || '未知设备' }}</span>
              <span class="badge" :class="'badge-' + stateBadge(row).tone">
                {{ stateBadge(row).text }}
              </span>
            </div>
            <div class="sess-meta">
              <span>{{ row.ip || '—' }}</span>
              <span class="dot-sep">·</span>
              <span>登录 {{ formatTime(row.created_at) }}</span>
              <span class="dot-sep">·</span>
              <span>最近活动 {{ formatTime(row.last_seen_at) }}</span>
            </div>
          </div>
          <div v-if="row.state === 'active' && !row.current" class="sess-ops">
            <button
              type="button"
              class="sess-btn"
              :disabled="busySid === row.sid"
              @click="kick(row.sid)"
            >
              踢下线
            </button>
            <button
              type="button"
              class="sess-btn danger"
              :disabled="busySid === row.sid"
              @click="ban(row.sid)"
            >
              拉黑
            </button>
          </div>
          <span v-else-if="row.current" class="sess-current">当前设备</span>
        </li>
      </ul>

      <div v-if="bans.length" class="sess-bans">
        <p class="sess-bans-title">已拉黑设备</p>
        <div v-for="item in bans" :key="item.id" class="sess-ban-row">
          <span class="sess-ban-text">
            {{ item.label || '未知设备' }} · {{ item.ip || '—' }} · {{ item.reason || '' }}
          </span>
          <button type="button" class="sess-btn" @click="unban(item.id)">解除</button>
        </div>
      </div>

      <template #footer>
        <div class="sess-footer">
          <span class="sess-summary">{{ summary }}</span>
          <div class="sess-footer-ops">
            <button type="button" class="sess-btn" @click="load">刷新</button>
            <button type="button" class="sess-btn danger" @click="kickOthers">踢掉其它设备</button>
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
