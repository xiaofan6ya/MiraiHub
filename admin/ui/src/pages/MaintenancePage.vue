<script setup lang="ts">
/**
 * S06 更新与备份 /maintenance（设计稿 06-pages-d P24，模板 C）。
 *
 * 三块旧功能：当前版本 + 检查更新（含升级操作）、数据库备份（含备份列表）、恢复与重启。
 * 硬约束：
 *   · 浏览器只下载安装包，只有 pywebview 外壳才安装更新（不硬编本机能力）；
 *   · fs_ops=false 时恢复/重启/改备份位置一律禁用并写清开启方法，不靠点下去报错；
 *   · 发布说明必须经 DOMPurify（见 @/api/maintenance:renderReleaseNotes）。
 */
import { computed, h, onBeforeUnmount, onMounted, ref } from "vue"
import { NCheckbox, NInput } from "naive-ui"
import { ExternalLink } from "lucide-vue-next"
import SettingsLayout from "@/layout/SettingsLayout.vue"
import SettingsCard from "@/layout/SettingsCard.vue"
import { Badge, Btn, CodeBlock, DataTable, Empty, ErrorBlock, KV, Section, Segmented } from "@/components"
import type { KvItem, TableColumn, TableRow } from "@/components"
import { useConfirm, useTableQuery, useToast } from "@/composables"
import { errorMessage, failureText } from "@/utils/error"
import { useI18n } from "@/i18n"
import {
  COMMAND_TABS,
  DEPLOY_LABELS,
  DEPLOY_METHODS,
  commandFor,
  commandTabs,
  combineUpdateState,
  detectDeployMethod,
  downloadBackup,
  formatBytes,
  getReleasesInfo,
  getUpdateInfo,
  hasCnCommands,
  readDeployMethod,
  restoreDatabase,
  restartService,
  saveBlob,
  saveDeployMethod,
  setBackupDir,
  toBackupRow,
  type BackupRecord,
  type CommandTab,
  type DeployDetectResult,
  type DeployMethod,
  type MaintenanceUpdatePayload,
  type ReleasesPayload,
} from "@/api/maintenance"
import { callDesktop, isDesktop } from "@/platform/desktop"

const { t } = useI18n()

const toast = useToast()
const confirm = useConfirm()

const sections = computed(() => [
  { id: "maintenance-version", title: t("page.maintenance.section.version") },
  { id: "maintenance-backup", title: t("page.maintenance.section.backup") },
  { id: "maintenance-records", title: t("page.maintenance.section.records") },
  { id: "maintenance-recover", title: t("page.maintenance.section.recover") },
])

function hasMobileShell(): boolean {
  if (typeof window === "undefined") return false
  return Boolean((window as unknown as { WBMobile?: unknown }).WBMobile)
}

const loading = ref(false)
const checking = ref(false)
const error = ref("")
const update = ref<MaintenanceUpdatePayload | null>(null)
const releases = ref<ReleasesPayload | null>(null)
const releasesError = ref("")

const nativeShell = ref(isDesktop())
const detected = ref<DeployDetectResult>(detectDeployMethod({ desktop: nativeShell.value, mobile: hasMobileShell() }))
const deployMethod = ref<DeployMethod>(readDeployMethod() ?? detected.value.method)
const commandTab = ref<CommandTab>("docker")
const cnMirror = ref(false)

const backups = ref<BackupRecord[]>([])
const backupBusy = ref(false)
const restoring = ref(false)
const restarting = ref(false)
const dirDraft = ref("")
const dirBusy = ref(false)

const nativeBusy = ref(false)
const nativeMessage = ref("")
const nativeReady = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

const view = computed(() => combineUpdateState(update.value, releases.value, releasesError.value))
const canFsOps = computed(() => Boolean(update.value?.fs_ops))
const canBackup = computed(() => Boolean(update.value?.backup_supported))
const canRestore = computed(() => canFsOps.value && canBackup.value)
const isServerDeploy = computed(() => deployMethod.value === "docker" || deployMethod.value === "source")

const deployOptions = DEPLOY_METHODS.map((method) => ({ label: DEPLOY_LABELS[method], value: method }))
const tabs = computed(() => commandTabs(update.value))
const tabOptions = computed(() => COMMAND_TABS.filter((tab) => tabs.value.includes(tab.value)))
const activeTab = computed<CommandTab>(() =>
  tabs.value.includes(commandTab.value) ? commandTab.value : (tabs.value[0] ?? "docker"),
)
const commandText = computed(() => commandFor(update.value, activeTab.value, cnMirror.value))

const dirDirty = computed(
  () => Boolean(update.value) && dirDraft.value.trim() !== (update.value as MaintenanceUpdatePayload).backup_dir,
)

const fsOffReason = computed(() => t("page.maintenance.fsOffReason"))

const dbItems = computed<KvItem[]>(() => {
  const payload = update.value
  if (!payload) return []
  const items: KvItem[] = [
    { label: t("page.maintenance.kv.database"), value: String(payload.database || "").toUpperCase() || "—" },
    { label: t("page.maintenance.kv.databaseFile"), value: payload.db_path || "—", mono: true, block: true },
    { label: t("page.maintenance.kv.fileSize"), value: payload.db_exists ? formatBytes(payload.db_size) : "—" },
    { label: t("page.maintenance.kv.backupDir"), value: payload.backup_dir || "—", mono: true, block: true },
  ]
  return items
})

const { state: tableState, setPage, setPageSize } = useTableQuery("maintenance-backups", { pageSize: 5 })

const backupRows = computed<TableRow[]>(() => {
  const start = (tableState.page - 1) * tableState.pageSize
  return backups.value.slice(start, start + tableState.pageSize).map(toBackupRow)
})

function recordById(id: unknown): BackupRecord | undefined {
  return backups.value.find((item) => item.id === String(id))
}

function redownload(id: unknown): void {
  const record = recordById(id)
  if (!record) return
  try {
    saveBlob(record.blob, record.filename)
    toast.success(t("page.maintenance.toast.savedFile", { name: record.filename }))
  } catch (caught) {
    toast.error(failureText(t("page.maintenance.action.saveBackup"), caught))
  }
}

const backupColumns = computed<TableColumn<TableRow>[]>(() => [
  { key: "timeText", title: t("page.maintenance.col.time"), width: 160, mobile: "title" },
  { key: "sizeText", title: t("page.maintenance.col.size"), width: 120, numeric: true, mobile: "meta" },
  {
    key: "actions",
    title: t("page.maintenance.col.download"),
    width: 96,
    align: "right",
    hideable: false,
    mobile: "actions",
    render: (row: TableRow) =>
      h(
        Btn,
        { size: "sm", variant: "quiet", onClick: () => redownload(row.id) },
        () => t("page.maintenance.col.download"),
      ),
  },
])

async function load(initial = false): Promise<void> {
  if (initial) loading.value = true
  checking.value = true
  if (initial) error.value = ""
  try {
    const payload = await getUpdateInfo()
    update.value = payload
    dirDraft.value = payload.backup_dir
    const available = commandTabs(payload)
    if (available.length && !available.includes(commandTab.value)) commandTab.value = available[0]
    try {
      releases.value = await getReleasesInfo()
      releasesError.value = ""
    } catch (caught) {
      // 生产镜像没有更新模块（503）：只降级发布说明与安装入口，整页保持可用。
      releases.value = null
      releasesError.value = errorMessage(caught)
    }
  } catch (caught) {
    if (update.value) toast.error(failureText(t("page.maintenance.action.checkUpdate"), caught))
    else error.value = errorMessage(caught)
  } finally {
    loading.value = false
    checking.value = false
  }
}

function onCommandTab(value: string | number): void {
  const tab = String(value) as CommandTab
  if (COMMAND_TABS.some((item) => item.value === tab)) commandTab.value = tab
}

function onDeploy(value: string | number): void {
  const method = String(value) as DeployMethod
  if (!DEPLOY_METHODS.includes(method)) return
  deployMethod.value = method
  saveDeployMethod(method)
}

async function onBackup(): Promise<void> {
  const ok = await confirm({
    title: t("page.maintenance.confirm.backup.title"),
    message: t("page.maintenance.confirm.backup.body"),
    okText: t("page.maintenance.records.backupNow"),
  })
  if (!ok) return
  backupBusy.value = true
  try {
    const file = await downloadBackup()
    backups.value = [{ ...file, id: file.createdAt + "-" + backups.value.length }, ...backups.value]
    toast.success(t("page.maintenance.toast.backedUp", { name: file.filename, size: formatBytes(file.size) }))
  } catch (caught) {
    toast.error(failureText(t("page.maintenance.action.backup"), caught))
  } finally {
    backupBusy.value = false
  }
}

async function onSaveDir(): Promise<void> {
  const path = dirDraft.value.trim()
  if (!path) {
    toast.error(t("page.maintenance.toast.dirRequired"))
    return
  }
  dirBusy.value = true
  try {
    const result = await setBackupDir({ path })
    if (update.value) {
      update.value = {
        ...update.value,
        backup_dir: result.backup_dir,
        backup_dir_default: result.backup_dir_default,
        backup_dir_is_default: result.backup_dir_is_default,
      }
    }
    dirDraft.value = result.backup_dir
    toast.success(t("page.maintenance.toast.dirSaved"))
  } catch (caught) {
    toast.error(failureText(t("page.maintenance.action.saveDir"), caught))
  } finally {
    dirBusy.value = false
  }
}

async function onResetDir(): Promise<void> {
  const ok = await confirm({
    title: t("page.maintenance.confirm.resetDir.title"),
    message: t("page.maintenance.confirm.resetDir.body"),
    okText: t("page.maintenance.confirm.resetDir.ok"),
  })
  if (!ok) return
  dirBusy.value = true
  try {
    const result = await setBackupDir({ reset: true })
    if (update.value) {
      update.value = {
        ...update.value,
        backup_dir: result.backup_dir,
        backup_dir_default: result.backup_dir_default,
        backup_dir_is_default: result.backup_dir_is_default,
      }
    }
    dirDraft.value = result.backup_dir
    toast.success(t("page.maintenance.toast.dirReset", { path: result.backup_dir }))
  } catch (caught) {
    toast.error(failureText(t("page.maintenance.action.resetDir"), caught))
  } finally {
    dirBusy.value = false
  }
}

function onCancelDir(): void {
  dirDraft.value = update.value?.backup_dir ?? ""
}

function pickRestore(): void {
  fileInput.value?.click()
}

async function onRestoreFile(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ""
  if (!file) return
  const ok = await confirm({
    title: t("page.maintenance.confirm.restore.title"),
    message: t("page.maintenance.confirm.restore.body", { name: file.name, size: formatBytes(file.size) }),
    danger: true,
    okText: t("page.maintenance.confirm.restore.ok"),
    requireText: t("page.maintenance.confirm.restore.ok"),
  })
  if (!ok) return
  restoring.value = true
  try {
    const result = await restoreDatabase(file)
    const tables = result.tables ?? {}
    toast.success(
      t("page.maintenance.toast.restored", {
        accounts: tables.accounts ?? "—",
        keys: tables.api_keys ?? "—",
        copy: result.safety_copy || t("page.maintenance.safetyCopySaved"),
      }),
    )
    await load()
  } catch (caught) {
    toast.error(failureText(t("page.maintenance.action.restore"), caught))
  } finally {
    restoring.value = false
  }
}

async function onRestart(): Promise<void> {
  const ok = await confirm({
    title: t("page.maintenance.confirm.restart.title"),
    message: t("page.maintenance.confirm.restart.body"),
    danger: true,
    okText: t("page.maintenance.recover.restartBtn"),
  })
  if (!ok) return
  restarting.value = true
  try {
    await restartService()
    toast.success(t("page.maintenance.toast.restarted"))
  } catch (caught) {
    toast.error(failureText(t("page.maintenance.action.restart"), caught))
  } finally {
    restarting.value = false
  }
}

let nativeTimer: ReturnType<typeof setInterval> | null = null

function stopNativePoll(): void {
  if (nativeTimer) {
    clearInterval(nativeTimer)
    nativeTimer = null
  }
}

function pollNative(): void {
  stopNativePoll()
  nativeTimer = setInterval(() => {
    void (async () => {
      const status = await callDesktop<{ busy?: boolean; ready?: boolean; message?: string }>("update_status")
      if (!status) {
        stopNativePoll()
        return
      }
      nativeMessage.value = String(status.message ?? "")
      nativeReady.value = Boolean(status.ready)
      if (!status.busy && !status.ready) stopNativePoll()
    })()
  }, 1000)
}

async function onNativeUpdate(): Promise<void> {
  const id = view.value.releaseId
  if (!id) return
  const ok = await confirm({
    title: t("page.maintenance.confirm.install.title"),
    message: t("page.maintenance.confirm.install.body", {
      tag: view.value.notesTag || t("page.maintenance.latestFallback"),
    }),
    okText: t("page.maintenance.confirm.install.ok"),
  })
  if (!ok) return
  nativeBusy.value = true
  try {
    const result = await callDesktop<{ error?: string }>("download_update", id)
    if (result?.error) {
      toast.error(failureText(t("page.maintenance.action.downloadUpdate"), result.error))
      return
    }
    nativeMessage.value = t("page.maintenance.toast.downloading")
    toast.success(t("page.maintenance.toast.downloadStarted"))
    pollNative()
  } catch (caught) {
    toast.error(failureText(t("page.maintenance.action.downloadUpdate"), caught))
  } finally {
    nativeBusy.value = false
  }
}

async function onNativeApply(): Promise<void> {
  const ok = await confirm({
    title: t("page.maintenance.confirm.apply.title"),
    message: t("page.maintenance.confirm.apply.body"),
    danger: true,
    okText: t("page.maintenance.confirm.apply.ok"),
  })
  if (!ok) return
  nativeBusy.value = true
  try {
    const result = await callDesktop<{ error?: string }>("apply_update")
    if (result?.error) toast.error(failureText(t("page.maintenance.action.installUpdate"), result.error))
  } catch (caught) {
    toast.error(failureText(t("page.maintenance.action.installUpdate"), caught))
  } finally {
    nativeBusy.value = false
  }
}

onMounted(() => {
  void load(true)
})

onBeforeUnmount(stopNativePoll)
</script>

<template>
  <SettingsLayout
    :title="t('page.maintenance.title')"
    :help="t('page.maintenance.help')"
    :sections="sections"
    :dirty="dirDirty"
  >
    <template #actions>
      <Btn :loading="checking" @click="load(false)">{{ t('page.maintenance.action.checkUpdate') }}</Btn>
    </template>

    <SettingsCard
      v-if="error && !update"
      id="maintenance-version"
      :title="t('page.maintenance.section.version')"
      hide-save
      hide-reset
    >
      <ErrorBlock
        :title="t('page.maintenance.error.load', { error })"
        :retry-label="t('common.retry')"
        @retry="load(true)"
      />
    </SettingsCard>

    <SettingsCard
      v-else
      id="maintenance-version"
      :title="t('page.maintenance.section.version')"
      :description="t('page.maintenance.version.description')"
      :loading="checking"
      hide-save
      hide-reset
    >
      <p v-if="loading && !update" class="hint">{{ t('page.maintenance.loadingVersion') }}</p>

      <div class="version-hero">
        <div class="version-main">
          <span class="eyebrow">{{ t('page.maintenance.section.version') }}</span>
          <strong class="version-num tnum">{{ view.version ? "v" + view.version : "—" }}</strong>
        </div>
        <Badge :tone="view.statusTone" dot>{{ view.statusText }}</Badge>
      </div>

      <div class="version-meta">
        <span>
          {{ t('page.maintenance.latestRelease') }}
          <b class="tnum">{{ view.latestTag || t('page.maintenance.latestUnknown') }}</b>
        </span>
        <a
          v-if="view.releasesUrl"
          class="ext-link"
          :href="view.releasesUrl"
          target="_blank"
          rel="noopener noreferrer"
        >
          {{ t('page.maintenance.releasesLink') }}<ExternalLink :size="14" />
        </a>
      </div>

      <p v-if="view.warning" class="warn-text" role="status">{{ view.warning }}</p>

      <Section :title="t('page.maintenance.deploy.title')">
        <Segmented
          :options="deployOptions"
          :model-value="deployMethod"
          :aria-label="t('page.maintenance.deploy.title')"
          @update:model-value="onDeploy"
        />
        <p class="hint">{{ detected.hint }}</p>
      </Section>

      <Section v-if="view.available" :title="t('page.maintenance.notes.title')">
        <p v-if="view.notesTag && view.notesTag !== view.latestTag && view.notesHtml" class="hint">
          {{
            t('page.maintenance.notes.from', { notes: view.notesTag, latest: view.latestTag })
          }}
        </p>
        <div v-if="view.notesHtml" class="release-notes" v-html="view.notesHtml" />
        <Empty v-else compact :description="view.notesEmptyText" />
      </Section>

      <Section v-if="view.available" :title="t('page.maintenance.upgrade.title')">
        <template v-if="isServerDeploy">
          <Segmented
            v-if="tabOptions.length > 1"
            :options="tabOptions"
            :model-value="activeTab"
            :aria-label="t('page.maintenance.upgrade.tabAria')"
            @update:model-value="onCommandTab"
          />
          <NCheckbox v-if="hasCnCommands(update)" v-model:checked="cnMirror">
            {{ t('page.maintenance.upgrade.cnMirror') }}
          </NCheckbox>
          <CodeBlock v-if="commandText" :code="commandText" language="bash" />
          <p v-else class="hint">{{ t('page.maintenance.upgrade.noCommand') }}</p>
          <p class="hint">{{ t('page.maintenance.upgrade.gitNote') }}</p>
        </template>

        <template v-else>
          <p class="hint">
            {{
              deployMethod === "apk"
                ? t('page.maintenance.upgrade.apkHint')
                : t('page.maintenance.upgrade.exeHint')
            }}
          </p>
          <div class="action-row">
            <Btn
              v-if="nativeShell && view.installable"
              variant="primary"
              :loading="nativeBusy"
              @click="onNativeUpdate"
            >
              {{ t('page.maintenance.upgrade.downloadInstall') }}
            </Btn>
            <Btn v-if="nativeReady" variant="primary" :loading="nativeBusy" @click="onNativeApply">
              {{ t('page.maintenance.upgrade.restartInstall') }}
            </Btn>
            <a
              v-if="view.downloadUrl"
              class="ext-link"
              :href="view.downloadUrl"
              target="_blank"
              rel="noopener noreferrer"
            >
              {{ view.downloadLabel }}<ExternalLink :size="14" />
            </a>
          </div>
          <p v-if="nativeMessage" class="hint" role="status">{{ nativeMessage }}</p>
          <p v-if="!view.installable" class="hint">
            {{
              deployMethod === "apk"
                ? t('page.maintenance.upgrade.apkMissing')
                : t('page.maintenance.upgrade.exeMissing')
            }}
          </p>
          <p v-if="!nativeShell && deployMethod === 'exe'" class="hint">
            {{ t('page.maintenance.upgrade.notShell') }}
          </p>
        </template>
      </Section>
    </SettingsCard>

    <SettingsCard
      id="maintenance-backup"
      :title="t('page.maintenance.section.backup')"
      :description="t('page.maintenance.backup.description')"
      :dirty="dirDirty"
      :loading="loading"
      :save-disabled="dirBusy || !dirDirty"
      :save-label="t('page.maintenance.backup.saveLabel')"
      :hide-save="!canFsOps"
      @save="onSaveDir"
      @reset="onCancelDir"
    >
      <template v-if="update">
        <KV :items="dbItems" />

        <div class="field">
          <label class="field-label" for="backup-dir">{{ t('page.maintenance.kv.backupDir') }}</label>
          <NInput
            v-if="canFsOps"
            id="backup-dir"
            v-model:value="dirDraft"
            :placeholder="t('page.maintenance.backup.dirPlaceholder')"
            :disabled="dirBusy"
          />
          <p v-else class="hint">{{ t('page.maintenance.backup.dirReadonly') }}</p>
          <div class="action-row">
            <Badge :tone="update.backup_dir_writable ? 'ok' : 'danger'">
              {{ update.backup_dir_writable ? t('page.maintenance.backup.writable') : t('page.maintenance.backup.notWritable') }}
            </Badge>
            <Badge v-if="!update.backup_dir_is_default" tone="neutral">
              {{ t('page.maintenance.backup.custom') }}
            </Badge>
            <Btn
              v-if="canFsOps && !update.backup_dir_is_default"
              variant="quiet"
              size="sm"
              :loading="dirBusy"
              @click="onResetDir"
            >
              {{ t('page.maintenance.backup.resetDir') }}
            </Btn>
          </div>
          <p class="hint">{{ t('page.maintenance.backup.dirNote') }}</p>
        </div>
      </template>
      <p v-else class="hint">{{ t('page.maintenance.loadingBackup') }}</p>
    </SettingsCard>

    <SettingsCard
      id="maintenance-records"
      :title="t('page.maintenance.section.records')"
      :description="t('page.maintenance.records.description')"
      hide-save
      hide-reset
    >
      <template v-if="update">
        <div class="action-row">
          <Btn variant="primary" :loading="backupBusy" :disabled="!canBackup" @click="onBackup">
            {{ t('page.maintenance.records.backupNow') }}
          </Btn>
          <span v-if="!canBackup" class="hint">
            {{ t('page.maintenance.records.sqliteOnly', { db: (update.database || "").toUpperCase() }) }}
          </span>
        </div>

        <CodeBlock
          v-if="!canBackup && update.mysql_backup_command"
          :code="update.mysql_backup_command"
          language="bash"
        />

        <DataTable
          :columns="backupColumns"
          :data="backupRows"
          row-key="id"
          table-id="maintenance-backups"
          remote
          :page="tableState.page"
          :page-size="tableState.pageSize"
          :item-count="backups.length"
          :page-sizes="[5, 10, 20]"
          max-height="320px"
          :empty-description="t('page.maintenance.records.empty')"
          @update:page="setPage"
          @update:page-size="setPageSize"
        />

        <p v-if="update.backup_note" class="hint">{{ update.backup_note }}</p>
      </template>
      <p v-else class="hint">{{ t('page.maintenance.loadingBackup') }}</p>
    </SettingsCard>

    <SettingsCard
      id="maintenance-recover"
      :title="t('page.maintenance.section.recover')"
      :description="t('page.maintenance.recover.description')"
      hide-save
      hide-reset
    >
      <template v-if="update">
        <p v-if="!canFsOps" class="warn-text" role="status">{{ fsOffReason }}</p>

        <Section :title="t('page.maintenance.recover.restoreTitle')" first>
          <p class="hint">{{ t('page.maintenance.recover.restoreHint') }}</p>
          <div class="action-row">
            <Btn :disabled="!canRestore" :loading="restoring" @click="pickRestore">
              {{ t('page.maintenance.recover.restoreTitle') }}
            </Btn>
            <span v-if="!canBackup" class="hint">{{ t('page.maintenance.recover.unsupported') }}</span>
          </div>
          <input
            ref="fileInput"
            class="hidden-input"
            type="file"
            accept=".db,application/octet-stream"
            @change="onRestoreFile"
          />
        </Section>

        <Section :title="t('page.maintenance.recover.restartTitle')">
          <p class="hint">{{ t('page.maintenance.recover.restartHint') }}</p>
          <div class="action-row">
            <Btn variant="danger" :disabled="!canFsOps" :loading="restarting" @click="onRestart">
              {{ t('page.maintenance.recover.restartBtn') }}
            </Btn>
          </div>
        </Section>
      </template>
      <p v-else class="hint">{{ t('page.maintenance.loadingBackup') }}</p>
    </SettingsCard>
  </SettingsLayout>
</template>

<style scoped>
.version-hero {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--sp-3);
  flex-wrap: wrap;
}
.version-main {
  display: grid;
  gap: var(--sp-1);
}
.eyebrow {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}
.version-num {
  font-size: var(--fs-stat);
  line-height: var(--lh-stat);
  font-weight: 600;
  color: var(--fg);
}
.version-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-3);
  flex-wrap: wrap;
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
}
.version-meta b {
  color: var(--fg);
  font-weight: 600;
}
.ext-link {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-1);
  color: var(--brand);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  text-decoration: none;
}
.ext-link:hover {
  text-decoration: underline;
}
.hint {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-muted);
  max-width: 68ch;
}
.warn-text {
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--warn);
  max-width: 68ch;
}
.action-row {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
}
.field {
  display: grid;
  gap: var(--sp-2);
}
.field-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--fg);
}
.hidden-input {
  display: none;
}
.release-notes {
  max-height: 360px;
  overflow: auto;
  padding: var(--sp-3);
  border: 1px solid var(--border-soft);
  border-radius: var(--r-ctl);
  background: var(--surface-2);
  font-size: var(--fs-small);
  line-height: var(--lh-small);
  color: var(--fg-2);
}
.release-notes :deep(h1),
.release-notes :deep(h2),
.release-notes :deep(h3) {
  font-size: var(--fs-body);
  line-height: var(--lh-body);
  font-weight: 600;
  color: var(--fg);
  margin: var(--sp-3) 0 var(--sp-1);
}
.release-notes :deep(ul),
.release-notes :deep(ol) {
  padding-left: var(--sp-5);
  margin: var(--sp-1) 0;
}
.release-notes :deep(p) {
  margin: var(--sp-1) 0;
}
.release-notes :deep(code) {
  font-family: var(--font-mono);
}
.release-notes :deep(pre) {
  overflow: auto;
  padding: var(--sp-2);
  border-radius: var(--r-ctl);
  background: var(--surface);
}
.release-notes :deep(a) {
  color: var(--brand);
}
</style>