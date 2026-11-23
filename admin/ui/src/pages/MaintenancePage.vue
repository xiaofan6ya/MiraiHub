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

const toast = useToast()
const confirm = useConfirm()

const sections = [
  { id: "maintenance-version", title: "当前版本" },
  { id: "maintenance-backup", title: "数据库备份" },
  { id: "maintenance-records", title: "备份记录" },
  { id: "maintenance-recover", title: "恢复与重启" },
]

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

const fsOffReason = computed(
  () => "本机文件操作与数据库恢复已关闭（服务端未放行 ADMIN_ALLOW_FS_OPS=1）。需要时在启动环境设置 ADMIN_ALLOW_FS_OPS=1 后重启服务，用完改回 0。",
)

const dbItems = computed<KvItem[]>(() => {
  const payload = update.value
  if (!payload) return []
  const items: KvItem[] = [
    { label: "数据库", value: String(payload.database || "").toUpperCase() || "—" },
    { label: "数据库文件", value: payload.db_path || "—", mono: true, block: true },
    { label: "文件大小", value: payload.db_exists ? formatBytes(payload.db_size) : "—" },
    { label: "备份存放位置", value: payload.backup_dir || "—", mono: true, block: true },
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
    toast.success("已保存：" + record.filename)
  } catch (caught) {
    toast.error(failureText("保存备份", caught))
  }
}

const backupColumns: TableColumn<TableRow>[] = [
  { key: "timeText", title: "时间", width: 160, mobile: "title" },
  { key: "sizeText", title: "大小", width: 120, numeric: true, mobile: "meta" },
  {
    key: "actions",
    title: "下载",
    width: 96,
    align: "right",
    hideable: false,
    mobile: "actions",
    render: (row: TableRow) =>
      h(
        Btn,
        { size: "sm", variant: "quiet", onClick: () => redownload(row.id) },
        () => "下载",
      ),
  },
]

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
    if (update.value) toast.error(failureText("检查更新", caught))
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
    title: "立即备份数据库？",
    message: "备份含账号凭据与 API Key，请私密保存。备份文件会直接下载到本机。",
    okText: "立即备份",
  })
  if (!ok) return
  backupBusy.value = true
  try {
    const file = await downloadBackup()
    backups.value = [{ ...file, id: file.createdAt + "-" + backups.value.length }, ...backups.value]
    toast.success("已备份并下载：" + file.filename + "（" + formatBytes(file.size) + "）")
  } catch (caught) {
    toast.error(failureText("备份", caught))
  } finally {
    backupBusy.value = false
  }
}

async function onSaveDir(): Promise<void> {
  const path = dirDraft.value.trim()
  if (!path) {
    toast.error("保存备份位置失败：请填写目录路径")
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
    toast.success("已更新备份位置")
  } catch (caught) {
    toast.error(failureText("保存备份位置", caught))
  } finally {
    dirBusy.value = false
  }
}

async function onResetDir(): Promise<void> {
  const ok = await confirm({ title: "恢复默认备份位置？", message: "备份会回到数据目录下的 backups 子目录。", okText: "恢复默认" })
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
    toast.success("已恢复默认位置：" + result.backup_dir)
  } catch (caught) {
    toast.error(failureText("恢复默认位置", caught))
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
    title: "恢复备份？",
    message:
      "将用上传的文件整体替换当前数据库（账号、Key、日志、配置全部回到备份时刻）。" +
      "恢复前会自动保存一份安全副本。文件：" + file.name + " · " + formatBytes(file.size),
    danger: true,
    okText: "恢复",
    requireText: "恢复",
  })
  if (!ok) return
  restoring.value = true
  try {
    const result = await restoreDatabase(file)
    const tables = result.tables ?? {}
    toast.success(
      "已恢复备份：账号 " + (tables.accounts ?? "—") +
        " · Key " + (tables.api_keys ?? "—") +
        "；恢复前副本 " + (result.safety_copy || "已保存"),
    )
    await load()
  } catch (caught) {
    toast.error(failureText("恢复备份", caught))
  } finally {
    restoring.value = false
  }
}

async function onRestart(): Promise<void> {
  const ok = await confirm({
    title: "立即重启服务？",
    message: "服务约 2 秒内重启，期间请求会短暂失败。",
    danger: true,
    okText: "立即重启",
  })
  if (!ok) return
  restarting.value = true
  try {
    await restartService()
    toast.success("已触发重启：服务约 2 秒后恢复，稍后刷新本页")
  } catch (caught) {
    toast.error(failureText("重启服务", caught))
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
    title: "下载并安装更新？",
    message: "将下载 " + (view.value.notesTag || "最新版") + " 安装包并校验 SHA256，校验通过后再重启安装。",
    okText: "开始下载",
  })
  if (!ok) return
  nativeBusy.value = true
  try {
    const result = await callDesktop<{ error?: string }>("download_update", id)
    if (result?.error) {
      toast.error(failureText("下载更新", result.error))
      return
    }
    nativeMessage.value = "正在下载更新…"
    toast.success("已开始下载更新")
    pollNative()
  } catch (caught) {
    toast.error(failureText("下载更新", caught))
  } finally {
    nativeBusy.value = false
  }
}

async function onNativeApply(): Promise<void> {
  const ok = await confirm({
    title: "重启并安装更新？",
    message: "客户端会关闭并替换可执行文件，更新期间界面不可用。",
    danger: true,
    okText: "重启并安装",
  })
  if (!ok) return
  nativeBusy.value = true
  try {
    const result = await callDesktop<{ error?: string }>("apply_update")
    if (result?.error) toast.error(failureText("安装更新", result.error))
  } catch (caught) {
    toast.error(failureText("安装更新", caught))
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
    title="更新与备份"
    help="检查正式版本、备份数据库，并按部署方式执行升级。升级命令由服务端按当前版本给出，前端不执行任何命令。"
    :sections="sections"
    :dirty="dirDirty"
  >
    <template #actions>
      <Btn :loading="checking" @click="load(false)">检查更新</Btn>
    </template>

    <SettingsCard v-if="error && !update" id="maintenance-version" title="当前版本" hide-save hide-reset>
      <ErrorBlock :title="'读取维护信息失败：' + error" retry-label="重试" @retry="load(true)" />
    </SettingsCard>

    <SettingsCard
      v-else
      id="maintenance-version"
      title="当前版本"
      description="版本号与升级命令来自服务端；浏览器只能下载安装包，只有桌面客户端外壳才能安装更新。"
      :loading="checking"
      hide-save
      hide-reset
    >
      <p v-if="loading && !update" class="hint">正在读取版本与备份信息…</p>

      <div class="version-hero">
        <div class="version-main">
          <span class="eyebrow">当前版本</span>
          <strong class="version-num tnum">{{ view.version ? "v" + view.version : "—" }}</strong>
        </div>
        <Badge :tone="view.statusTone" dot>{{ view.statusText }}</Badge>
      </div>

      <div class="version-meta">
        <span>最新正式版 <b class="tnum">{{ view.latestTag || "暂未获取" }}</b></span>
        <a
          v-if="view.releasesUrl"
          class="ext-link"
          :href="view.releasesUrl"
          target="_blank"
          rel="noopener noreferrer"
        >
          版本与更新公告<ExternalLink :size="14" />
        </a>
      </div>

      <p v-if="view.warning" class="warn-text" role="status">{{ view.warning }}</p>

      <Section title="部署方式">
        <Segmented
          :options="deployOptions"
          :model-value="deployMethod"
          aria-label="部署方式"
          @update:model-value="onDeploy"
        />
        <p class="hint">{{ detected.hint }}</p>
      </Section>

      <Section v-if="view.available" title="发布说明">
        <p v-if="view.notesTag && view.notesTag !== view.latestTag && view.notesHtml" class="hint">
          发布说明取自 {{ view.notesTag }}（最新正式版 {{ view.latestTag }} 可能还没有可安装的包）。
        </p>
        <div v-if="view.notesHtml" class="release-notes" v-html="view.notesHtml" />
        <Empty v-else compact :description="view.notesEmptyText" />
      </Section>

      <Section v-if="view.available" title="升级操作">
        <template v-if="isServerDeploy">
          <Segmented
            v-if="tabOptions.length > 1"
            :options="tabOptions"
            :model-value="activeTab"
            aria-label="升级命令类型"
            @update:model-value="onCommandTab"
          />
          <NCheckbox v-if="hasCnCommands(update)" v-model:checked="cnMirror">
            国内网络环境（pip 走清华镜像）
          </NCheckbox>
          <CodeBlock v-if="commandText" :code="commandText" language="bash" />
          <p v-else class="hint">服务端没有给出升级命令，请到 Releases 查看对应版本的升级说明。</p>
          <p class="hint">
            先确认 git status 没有未处理的本地改动。NAS 可在容器管理器重新构建并重启，保留 /data 数据卷。
          </p>
        </template>

        <template v-else>
          <p class="hint">
            {{
              deployMethod === "apk"
                ? "APK 安卓版：只能下载安装包，安装由系统确认。"
                : "EXE 桌面版：在桌面客户端里可以直接下载、校验并安装；在浏览器里只能下载安装包。"
            }}
          </p>
          <div class="action-row">
            <Btn
              v-if="nativeShell && view.installable"
              variant="primary"
              :loading="nativeBusy"
              @click="onNativeUpdate"
            >
              下载并安装更新
            </Btn>
            <Btn v-if="nativeReady" variant="primary" :loading="nativeBusy" @click="onNativeApply">
              重启并安装更新
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
                ? "该版本尚未提供 Android APK 或校验文件，可在 Releases 查看。"
                : "该版本尚未提供 Windows EXE 或校验文件，可在 Releases 查看。"
            }}
          </p>
          <p v-if="!nativeShell && deployMethod === 'exe'" class="hint">
            当前不在桌面客户端外壳里，无法在本机安装更新，只能下载安装包。
          </p>
        </template>
      </Section>
    </SettingsCard>

    <SettingsCard
      id="maintenance-backup"
      title="数据库备份"
      description="备份包含账号凭据与 API Key，请私密保存；升级前先做一份备份。"
      :dirty="dirDirty"
      :loading="loading"
      :save-disabled="dirBusy || !dirDirty"
      save-label="保存位置"
      :hide-save="!canFsOps"
      @save="onSaveDir"
      @reset="onCancelDir"
    >
      <template v-if="update">
        <KV :items="dbItems" />

        <div class="field">
          <label class="field-label" for="backup-dir">备份存放位置</label>
          <NInput
            v-if="canFsOps"
            id="backup-dir"
            v-model:value="dirDraft"
            placeholder="绝对路径，例如 /srv/miraihub/backups"
            :disabled="dirBusy"
          />
          <p v-else class="hint">
            修改备份位置需要服务端放行本机文件操作（ADMIN_ALLOW_FS_OPS=1），当前只读。
          </p>
          <div class="action-row">
            <Badge :tone="update.backup_dir_writable ? 'ok' : 'danger'">
              {{ update.backup_dir_writable ? "可写" : "不可写" }}
            </Badge>
            <Badge v-if="!update.backup_dir_is_default" tone="neutral">自定义位置</Badge>
            <Btn
              v-if="canFsOps && !update.backup_dir_is_default"
              variant="quiet"
              size="sm"
              :loading="dirBusy"
              @click="onResetDir"
            >
              恢复默认位置
            </Btn>
          </div>
          <p class="hint">下载时会保存到浏览器的下载目录；这个位置用于「恢复备份」时的默认查找路径。</p>
        </div>
      </template>
      <p v-else class="hint">正在读取备份信息…</p>
    </SettingsCard>

    <SettingsCard
      id="maintenance-records"
      title="备份记录"
      description="每次备份都是一次完整的数据库快照。服务端不留存备份文件，这里是本页本次会话内发起过的备份。"
      hide-save
      hide-reset
    >
      <template v-if="update">
        <div class="action-row">
          <Btn variant="primary" :loading="backupBusy" :disabled="!canBackup" @click="onBackup">立即备份</Btn>
          <span v-if="!canBackup" class="hint">
            当前是 {{ (update.database || "").toUpperCase() }} 数据库，页面在线备份只支持 SQLite。
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
          empty-description="还没有备份。立即备份会把数据库快照下载到本机。"
          @update:page="setPage"
          @update:page-size="setPageSize"
        />

        <p v-if="update.backup_note" class="hint">{{ update.backup_note }}</p>
      </template>
      <p v-else class="hint">正在读取备份信息…</p>
    </SettingsCard>

    <SettingsCard
      id="maintenance-recover"
      title="恢复与重启"
      description="这两项会直接改本机文件与进程，需要服务端显式放行本机文件操作。"
      hide-save
      hide-reset
    >
      <template v-if="update">
        <p v-if="!canFsOps" class="warn-text" role="status">{{ fsOffReason }}</p>

        <Section title="恢复备份" first>
          <p class="hint">
            选择一个 .db 备份文件整体替换当前数据库；恢复前会自动保存一份安全副本，完成后建议重启服务。
          </p>
          <div class="action-row">
            <Btn :disabled="!canRestore" :loading="restoring" @click="pickRestore">恢复备份</Btn>
            <span v-if="!canBackup" class="hint">当前数据库类型不支持在线恢复，请在数据库侧完成。</span>
          </div>
          <input
            ref="fileInput"
            class="hidden-input"
            type="file"
            accept=".db,application/octet-stream"
            @change="onRestoreFile"
          />
        </Section>

        <Section title="服务重启">
          <p class="hint">重启期间约 2 秒不可用，监督器会自动拉起新实例。</p>
          <div class="action-row">
            <Btn variant="danger" :disabled="!canFsOps" :loading="restarting" @click="onRestart">立即重启</Btn>
          </div>
        </Section>
      </template>
      <p v-else class="hint">正在读取备份信息…</p>
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