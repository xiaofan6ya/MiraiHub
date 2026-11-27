<script setup lang="ts">
/**
 * 账号池 · 清理重复账号（弹窗）。
 *
 * 后端（data-scoping D1）：查重 / 去重键是 (平台, 区, uid)，并跟随 X-Platform 作用域；
 * GET /duplicates 每组多了 platform / realm。这里按 platform/realm **分段展示**（fixlog_data_scoping §6.5），
 * 让管理员一眼看出「Qoder 国内 / 国际同一 uid」不会被当成重复。
 *
 * 预览里的「保留」是 id 最小的那条；真正执行时，被 API Key 绑定（单选或多选）的号永不删除，
 * 实际保留的可能是被绑定的那条 —— 弹窗底部写明。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { NModal } from 'naive-ui'
import { CopyX } from 'lucide-vue-next'
import { Btn, Empty, ErrorBlock, TitleTip } from '@/components'
import { useConfirm, useKeepAliveSuspended, useToast } from '@/composables'
import { usePlatformStore } from '@/stores/platform'
import { isAbortError } from '@/api/http'
import { failureText } from '@/utils/error'
import { fmtInt } from '@/utils/format'
import { useI18n } from '@/i18n'
import {
  dedupeAccounts,
  getDuplicates,
  groupDuplicates,
  type DuplicatesPayload,
} from '@/api/accounts'

const props = defineProps<{ show: boolean }>()
const emit = defineEmits<{
  (event: 'update:show', value: boolean): void
  (event: 'done', removed: number): void
}>()

const { t } = useI18n()
const toast = useToast()
/** 第 5 轮：KeepAlive 离开时收起本页直接使用的 NModal（Teleport 不随页面移走，否则会盖在下一页上）。 */
const suspended = useKeepAliveSuspended()
const confirm = useConfirm()
const platformStore = usePlatformStore()

const payload = ref<DuplicatesPayload | null>(null)
const loading = ref(false)
const busy = ref(false)
const error = ref('')
let controller: AbortController | null = null

async function load(): Promise<void> {
  controller?.abort()
  const current = new AbortController()
  controller = current
  loading.value = true
  error.value = ''
  try {
    const next = await getDuplicates(current.signal)
    if (controller !== current) return
    payload.value = next
  } catch (err) {
    if (isAbortError(err) || controller !== current) return
    error.value = failureText(t('page.accounts.dedupe.loadFailed'), err)
  } finally {
    if (controller === current) {
      controller = null
      loading.value = false
    }
  }
}

watch(
  () => props.show,
  (show) => {
    if (show) {
      payload.value = null
      void load()
    } else {
      controller?.abort()
      controller = null
      loading.value = false
    }
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  controller?.abort()
  controller = null
})

const sections = computed(() => groupDuplicates(payload.value))
const redundant = computed(() => Number(payload.value?.redundant) || 0)
const groupCount = computed(() => payload.value?.groups?.length || 0)

const summary = computed(() =>
  payload.value
    ? t('page.accounts.dedupe.summary', {
        total: fmtInt(payload.value.total),
        groups: fmtInt(groupCount.value),
        redundant: fmtInt(redundant.value),
      })
    : '',
)

function platformLabel(platform: string): string {
  if (!platform) return t('page.accounts.dedupe.platformUnknown')
  return platformStore.specFor(platform)?.label || platform
}

function realmLabel(realm: string): string {
  if (realm === 'cn') return t('page.accounts.dedupe.realmCn')
  if (realm === 'intl') return t('page.accounts.dedupe.realmIntl')
  return ''
}

function sectionTitle(platform: string, realm: string): string {
  return [platformLabel(platform), realmLabel(realm)].filter(Boolean).join(' · ')
}

function close(): void {
  emit('update:show', false)
}

async function runDedupe(): Promise<void> {
  if (!redundant.value || busy.value) return
  const ok = await confirm({
    title: t('page.accounts.dedupe.confirmTitle', { count: fmtInt(redundant.value) }),
    message: t('page.accounts.dedupe.confirmBody'),
    okText: t('page.accounts.dedupe.confirmOk'),
    danger: true,
  })
  if (!ok) return
  busy.value = true
  try {
    const result = await dedupeAccounts()
    const removed = Number(result?.removed) || 0
    toast.success(t('page.accounts.dedupe.done', { count: fmtInt(removed) }))
    emit('done', removed)
    close()
  } catch (err) {
    toast.error(failureText(t('page.accounts.dedupe.run'), err))
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <n-modal
    :show="show && !suspended"
    preset="card"
    class="acc-dedupe"
    :title="t('page.accounts.dedupe.title')"
    :bordered="false"
    :mask-closable="!busy"
    :closable="!busy"
    :style="{ width: 'min(720px, calc(100vw - 32px))' }"
    @update:show="(value: boolean) => emit('update:show', value)"
  >
    <p class="acc-dedupe__intro">{{ t('page.accounts.dedupe.intro') }}</p>

    <div v-if="loading" class="acc-dedupe__skeleton">
      <div v-for="index in 4" :key="index" class="acc-dedupe__skeleton-row mh-skeleton" />
    </div>
    <ErrorBlock v-else-if="error" :title="error" compact @retry="load" />
    <Empty
      v-else-if="payload && !groupCount"
      compact
      :icon="CopyX"
      :description="t('page.accounts.dedupe.empty', { total: fmtInt(payload.total) })"
    />
    <template v-else-if="payload">
      <p class="acc-dedupe__summary tnum">{{ summary }}</p>
      <div class="acc-dedupe__body">
        <section v-for="section in sections" :key="section.key" class="acc-dedupe__section">
          <header class="acc-dedupe__section-head">
            <span class="acc-dedupe__section-title">{{ sectionTitle(section.platform, section.realm) }}</span>
            <span class="acc-dedupe__section-meta tnum">
              {{ t('page.accounts.dedupe.sectionMeta', { groups: fmtInt(section.groups.length), redundant: fmtInt(section.redundant) }) }}
            </span>
          </header>
          <ul class="acc-dedupe__groups">
            <li v-for="group in section.groups" :key="section.key + ':' + group.uid" class="acc-dedupe__group">
              <TitleTip :content="group.uid"><span class="acc-dedupe__uid mono">{{ group.uid }}</span></TitleTip>
              <span class="acc-dedupe__keep">
                <span class="acc-dedupe__label">{{ t('page.accounts.dedupe.keep') }}</span>
                {{ group.keep.name || '#' + group.keep.id }}
                <span class="acc-dedupe__id mono">#{{ group.keep.id }}</span>
              </span>
              <span class="acc-dedupe__remove">
                <span class="acc-dedupe__label">{{ t('page.accounts.dedupe.remove', { count: group.remove.length }) }}</span>
                <TitleTip v-for="item in group.remove" :key="item.id" :content="item.created_at">
                  <span class="acc-dedupe__chip">{{ item.name || '#' + item.id }} <span class="mono">#{{ item.id }}</span></span>
                </TitleTip>
              </span>
            </li>
          </ul>
        </section>
      </div>
      <p class="acc-dedupe__note">{{ t('page.accounts.dedupe.note') }}</p>
    </template>

    <template #footer>
      <div class="acc-dedupe__footer">
        <Btn variant="quiet" :disabled="busy" @click="close">{{ t('common.cancel') }}</Btn>
        <Btn variant="danger" solid :loading="busy" :disabled="!redundant || loading" @click="runDedupe">
          {{ redundant ? t('page.accounts.dedupe.runCount', { count: fmtInt(redundant) }) : t('page.accounts.dedupe.run') }}
        </Btn>
      </div>
    </template>
  </n-modal>
</template>

<style scoped>
.acc-dedupe__intro,
.acc-dedupe__note {
  margin: 0 0 var(--sp-3);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  text-wrap: pretty;
}

.acc-dedupe__note {
  margin: var(--sp-3) 0 0;
}

.acc-dedupe__summary {
  margin: 0 0 var(--sp-2);
  font-size: var(--fs-small);
  color: var(--fg-2);
}

.acc-dedupe__body {
  display: grid;
  gap: var(--sp-3);
  max-height: 52vh;
  overflow: auto;
}

.acc-dedupe__section {
  border: 1px solid var(--border-soft);
  border-radius: var(--r-ctl);
  overflow: hidden;
}

.acc-dedupe__section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-2);
  padding: var(--sp-2) var(--sp-3);
  background: var(--surface-2);
}

.acc-dedupe__section-title {
  font-weight: 500;
  color: var(--fg);
}

.acc-dedupe__section-meta {
  font-size: var(--fs-small);
  color: var(--fg-muted);
}

.acc-dedupe__groups {
  list-style: none;
  margin: 0;
  padding: 0;
}

.acc-dedupe__group {
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr) minmax(0, 1.6fr);
  gap: var(--sp-3);
  align-items: center;
  padding: var(--sp-2) var(--sp-3);
  border-top: 1px solid var(--border-soft);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-2);
}

.acc-dedupe__uid {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--fg);
}

.acc-dedupe__keep,
.acc-dedupe__remove {
  display: flex;
  align-items: center;
  gap: var(--sp-1);
  flex-wrap: wrap;
  min-width: 0;
}

.acc-dedupe__label {
  color: var(--fg-muted);
}

.acc-dedupe__id {
  color: var(--fg-faint);
}

.acc-dedupe__chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 22px;
  padding: 0 var(--sp-2);
  border-radius: var(--r-chip);
  background: var(--neutral-soft);
  color: var(--fg-2);
}

.acc-dedupe__footer {
  display: flex;
  justify-content: flex-end;
  gap: var(--sp-2);
}

.acc-dedupe__skeleton {
  display: grid;
  gap: var(--sp-2);
}

.acc-dedupe__skeleton-row {
  height: 12px;
  border-radius: var(--r-chip);
  background: var(--surface-2);
}

@media (max-width: 767px) {
  .acc-dedupe__group {
    grid-template-columns: minmax(0, 1fr);
    gap: var(--sp-1);
  }
}
</style>
