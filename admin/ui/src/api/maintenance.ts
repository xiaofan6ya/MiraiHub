/**
 * 更新与备份（/api/maintenance/*、/api/app/releases）。
 *
 * 旧版实现散在三处，这里逐条对齐：
 *   · admin/console/review-improvements.js：WBReview.loadUpdate / backup / doRestore / doRestartService；
 *   · admin/console/desktop.js：版本更新对话框（浏览器只下载，桌面壳才安装）；
 *   · admin/console/release-notes.js：发布说明的 Markdown 渲染（marked + DOMPurify 白名单）。
 *
 * 后端：admin/routers/maintenance.py、admin/routers/releases.py、desktop/updates.py、desktop/app.py。
 * 本页不做任何「本机能力」假设：桌面壳 / 文件系统运维开关是否可用一律由运行时事实决定。
 */
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import {
  ApiError,
  CSRF_COOKIE_NAME,
  CSRF_HEADER_NAME,
  filenameFromContentDisposition,
  http,
  messageFromPayload,
  readCookie,
  resolveUrl,
  type DownloadResult,
} from './http'
import { FMT_EMPTY, fmtTime } from '@/utils/format'

/* ------------------------------------------------------------------ *
 * 类型（字段名与后端保持一致，snake_case 原样保留）
 * ------------------------------------------------------------------ */

/** GET /api/maintenance/update 的 latest（maintenance.py:update_info 只挑这四个字段）。 */
export interface LatestReleaseBrief {
  tag: string
  name: string
  url: string
  date: string
}

/** GET /api/maintenance/update（含 _backup_location() 的全部字段）。 */
export interface MaintenanceUpdatePayload {
  ok?: boolean
  /** package 版本，来自 desktop/version.json。 */
  version: string
  latest: LatestReleaseBrief | null
  available: boolean
  /** 检查失败原因（空串表示正常）。 */
  error: string
  releases_url: string
  /** sqlite / mysql。 */
  database: string
  /** 只有 sqlite 支持页面在线备份。 */
  backup_supported: boolean
  /** 本机文件操作与数据库恢复总闸（ADMIN_ALLOW_FS_OPS）。 */
  fs_ops: boolean
  db_exists: boolean
  /** 字节数；db_exists=false 时为 0。 */
  db_size: number
  db_path: string
  backup_dir: string
  backup_dir_default: string
  backup_dir_is_default: boolean
  backup_dir_writable: boolean
  /** 是否跑在桌面版数据目录下（WORKBUDDY_DESKTOP_DATA）。 */
  desktop_mode: boolean
  commands: { docker?: string; linux?: string } | null
  commands_cn: { docker?: string; linux?: string } | null
  backup_note: string
  /** 非 sqlite 时给出的 mysqldump 命令。 */
  mysql_backup_command: string | null
}

/** GitHub Release 资产（desktop/updates.py:check 归一化后的形状）。 */
export interface ReleaseAsset {
  name?: string
  browser_download_url?: string
  size?: number
  digest?: string
}

/** desktop/updates.py:check 里 releases[] 的每一项。 */
export interface GithubRelease {
  id: number
  tag: string
  name: string
  /** Markdown 发布说明（后端已截断到 100000 字符）。 */
  body: string
  date: string
  url: string
  asset: ReleaseAsset | null
  checksum: ReleaseAsset | null
  /** 有安装包且能拿到 sha256（asset.digest 或 .sha256 文件）。 */
  installable: boolean
}

/** GET /api/app/releases（生产镜像无更新模块时回 503）。 */
export interface ReleasesPayload {
  version: string
  /** windows / android。 */
  platform: string
  releases: GithubRelease[]
  latest: GithubRelease | null
  available: boolean
  error: string
  checked: boolean
  releases_url: string
}

/** POST /api/maintenance/restore 的返回。 */
export interface RestoreResult {
  ok: boolean
  tables?: Record<string, number>
  /** 恢复前安全副本的文件名。 */
  safety_copy?: string
  note?: string
}

export interface BackupDirPayload {
  ok: boolean
  backup_dir: string
  backup_dir_default: string
  backup_dir_is_default: boolean
}

/* ------------------------------------------------------------------ *
 * 接口
 * ------------------------------------------------------------------ */

/** 维护页主接口：版本 + 备份位置 + 升级命令 + 运维开关。 */
export function getUpdateInfo(): Promise<MaintenanceUpdatePayload> {
  return http<MaintenanceUpdatePayload>('GET', '/api/maintenance/update')
}

/**
 * 发布说明与安装包信息（/api/app/releases）。
 *
 * 生产镜像按最小面构建、不含 desktop/updates.py → 503。这是预期的降级：
 * 调用方要接住错误并只降级发布说明区，不能让整页变错误态。
 */
export function getReleasesInfo(): Promise<ReleasesPayload> {
  return http<ReleasesPayload>('GET', '/api/app/releases')
}

/** 备份位置信息（保存位置后回读，与 /update 同源字段）。 */
export function getBackupInfo(): Promise<MaintenanceUpdatePayload> {
  return http<MaintenanceUpdatePayload>('GET', '/api/maintenance/backup/info')
}

export function setBackupDir(body: { path?: string; reset?: boolean }): Promise<BackupDirPayload> {
  return http<BackupDirPayload>('PUT', '/api/maintenance/backup/dir', { body })
}

/** 用上传的 .db 整体替换当前库（需 ADMIN_ALLOW_FS_OPS=1）。 */
export function restoreDatabase(file: Blob): Promise<RestoreResult> {
  return http<RestoreResult>('POST', '/api/maintenance/restore', {
    body: file,
    headers: { 'Content-Type': 'application/octet-stream' },
  })
}

/** 重启服务（需 fs_ops；后端 60 秒频控，额外要 confirm:true）。 */
export function restartService(): Promise<{ ok: boolean; message: string }> {
  return http('POST', '/api/maintenance/restart', { body: { confirm: true } })
}

/* ------------------------------------------------------------------ *
 * 备份下载：POST + blob
 * ------------------------------------------------------------------ *
 * http.download() 只会发 GET，而 /api/maintenance/backup 只接受 POST
 * （maintenance.py:backup 用 FileResponse 回二进制）。api/http.ts 不在本任务改动
 * 范围内，所以在这里补一个 POST 版下载助手：复用 http.ts 的
 * resolveUrl / readCookie / CSRF 常量 / 文件名解析 / 错误文案，
 * 页面仍然不直接 fetch（05-patterns §1）。
 */

/** 一次备份的结果：blob 留在内存里供列表里的「下载」再存一次。 */
export interface BackupFile extends DownloadResult {
  blob: Blob
  /** ISO 时间。 */
  createdAt: string
}

export interface BackupRecord extends BackupFile {
  id: string
}

async function postBlob(path: string): Promise<{ blob: Blob; filename: string }> {
  const headers = new Headers({ Accept: 'application/octet-stream' })
  const token = readCookie(CSRF_COOKIE_NAME)
  if (token) headers.set(CSRF_HEADER_NAME, token)
  const response = await fetch(resolveUrl(path), {
    method: 'POST',
    credentials: 'same-origin',
    headers,
  })
  if (!response.ok) {
    let text = ''
    try {
      text = await response.text()
    } catch {
      text = ''
    }
    let payload: Record<string, unknown> | string | null = null
    if (text.trim()) {
      try {
        payload = JSON.parse(text) as Record<string, unknown>
      } catch {
        payload = text
      }
    }
    throw new ApiError(response.status, messageFromPayload(response.status, payload), payload ?? text)
  }
  const blob = await response.blob()
  return { blob, filename: filenameFromContentDisposition(response.headers.get('content-disposition')) }
}

/** 后端备份文件名的口径（UTC，workbuddy-backup-YYYYmmdd-HHMMSS.db）。 */
export function backupFilename(date = new Date()): string {
  const pad = (value: number): string => String(value).padStart(2, '0')
  return (
    'workbuddy-backup-' +
    date.getUTCFullYear() +
    pad(date.getUTCMonth() + 1) +
    pad(date.getUTCDate()) +
    '-' +
    pad(date.getUTCHours()) +
    pad(date.getUTCMinutes()) +
    pad(date.getUTCSeconds()) +
    '.db'
  )
}

/** 触发浏览器保存（与 http.download() 内联的做法一致，只服务本文件的 blob）。 */
export function saveBlob(blob: Blob, filename: string): void {
  if (typeof document === 'undefined' || typeof URL === 'undefined' || typeof URL.createObjectURL !== 'function') return
  const href = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = href
  link.download = filename
  link.style.display = 'none'
  document.body.appendChild(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(href), 0)
}

/** 立即备份：POST /api/maintenance/backup → 二进制 → 触发下载。 */
export async function downloadBackup(filename?: string): Promise<BackupFile> {
  const { blob, filename: fromHeader } = await postBlob('/api/maintenance/backup')
  const name = filename || fromHeader || backupFilename()
  saveBlob(blob, name)
  return { blob, filename: name, size: blob.size, createdAt: new Date().toISOString() }
}

/* ------------------------------------------------------------------ *
 * 纯函数：数据转换 / 接口适配（单测覆盖）
 * ------------------------------------------------------------------ */

export type DeployMethod = 'docker' | 'source' | 'exe' | 'apk'

export const DEPLOY_LABELS: Record<DeployMethod, string> = {
  docker: 'Docker 容器',
  source: '源码部署',
  exe: 'EXE 桌面版',
  apk: 'APK 安卓版',
}

export const DEPLOY_METHODS: DeployMethod[] = ['docker', 'source', 'exe', 'apk']

/** 部署方式偏好（只影响展示哪一套升级操作，不改任何后端状态）。 */
export const DEPLOY_STORAGE_KEY = 'mh.maintenance.deploy'

export interface DeployDetectInput {
  /** window.pywebview 存在（src/platform/desktop.ts:isDesktop）。 */
  desktop?: boolean
  /** window.WBMobile 存在（安卓外壳，旧版 desktop.js 用它区分 APK/EXE 文案）。 */
  mobile?: boolean
}

export interface DeployDetectResult {
  method: DeployMethod
  /** 是否由运行时事实推出（false 表示只是默认值，用户可改）。 */
  detected: boolean
  hint: string
}

/**
 * 推断部署方式。
 *
 * 服务端部署（python main.py 与 docker 容器）在浏览器里无法区分：
 * 后端没有下发部署方式，UA 也看不出容器。所以这里只认外壳（EXE/APK），
 * 其余回落 Docker（旧版维护页的默认页签），并把「无法自动区分」写进提示，
 * 不让用户以为自动识别过。
 */
export function detectDeployMethod(input: DeployDetectInput = {}): DeployDetectResult {
  if (input.desktop) {
    return {
      method: 'exe',
      detected: true,
      hint: '检测到桌面客户端外壳（EXE）：可以直接下载、校验并安装更新。',
    }
  }
  if (input.mobile) {
    return {
      method: 'apk',
      detected: true,
      hint: '检测到安卓外壳（APK）：只能下载安装包，安装由系统确认。',
    }
  }
  return {
    method: 'docker',
    detected: false,
    hint: '浏览器里无法自动区分容器与源码部署，请按实际部署方式选择对应命令。',
  }
}

export function isDeployMethod(value: unknown): value is DeployMethod {
  return typeof value === 'string' && (DEPLOY_METHODS as string[]).includes(value)
}

export function readDeployMethod(): DeployMethod | null {
  try {
    const raw = localStorage.getItem(DEPLOY_STORAGE_KEY)
    return isDeployMethod(raw) ? raw : null
  } catch {
    return null
  }
}

export function saveDeployMethod(method: DeployMethod): void {
  try {
    localStorage.setItem(DEPLOY_STORAGE_KEY, method)
  } catch {
    /* 隐私模式写不进去，不影响本次会话 */
  }
}

export type CommandTab = 'docker' | 'linux'

/** 后端 commands 的两个键：docker（容器）与 linux（源码）。 */
export const COMMAND_TABS: Array<{ value: CommandTab; label: string }> = [
  { value: 'docker', label: 'Docker Compose' },
  { value: 'linux', label: 'Linux 源码' },
]

/** 该份 update 里实际有哪些命令页签。 */
export function commandTabs(update: MaintenanceUpdatePayload | null): CommandTab[] {
  const commands = update?.commands
  if (!commands) return []
  return COMMAND_TABS.filter((tab) => Boolean((commands as Record<string, string | undefined>)[tab.value])).map(
    (tab) => tab.value,
  )
}

/**
 * 取命令文本。cn=true 且后端给了 commands_cn 时用国内网络版本（pip 走清华镜像）。
 * 后端命令由 tag 拼出来，前端只展示、不执行 —— 与旧版一致。
 */
export function commandFor(
  update: MaintenanceUpdatePayload | null,
  tab: CommandTab,
  cn = false,
): string {
  if (!update) return ''
  const source = cn && update.commands_cn ? update.commands_cn : update.commands
  const value = (source as Record<string, string | undefined> | null | undefined)?.[tab]
  return typeof value === 'string' ? value : ''
}

/** 该份 update 是否提供国内网络环境的命令。 */
export function hasCnCommands(update: MaintenanceUpdatePayload | null): boolean {
  return Boolean(update?.commands_cn && Object.keys(update.commands_cn).length)
}

export type UpdateStatusTone = 'ok' | 'warn' | 'danger' | 'neutral' | 'brand'

/** 当前版本卡的视图模型（/update 与 /releases 合并后的展示口径）。 */
export interface UpdateView {
  version: string
  latestTag: string
  available: boolean
  statusTone: UpdateStatusTone
  statusText: string
  /** 检查失败原因（空串表示没出错）。 */
  warning: string
  /** 已 DOMPurify 过的发布说明 HTML。 */
  notesHtml: string
  /** 发布说明对应的版本号（可能与 latestTag 不同：维护接口挑正式版 tag，releases 挑有安装包的）。 */
  notesTag: string
  /** 没有发布说明时的说明文字。 */
  notesEmptyText: string
  /** 安装包下载地址（没有则为空串）。 */
  downloadUrl: string
  /** 下载按钮文案：安卓外壳给 APK，其余给 EXE。 */
  downloadLabel: string
  /** 桌面壳能否安装（有安装包 + 可校验）。 */
  installable: boolean
  releaseId: number | null
  releasesUrl: string
}

/**
 * 合并两个接口的展示口径。
 *
 * 分工：/api/maintenance/update 决定版本号 / 是否有新版本 / 升级命令；
 * /api/app/releases 提供发布说明正文、安装包地址与 installable。
 * 后者 503 时只降级说明与安装入口，状态徽章仍然能给出结论。
 */
export function combineUpdateState(
  update: MaintenanceUpdatePayload | null,
  releases: ReleasesPayload | null,
  releasesError = '',
): UpdateView {
  const version = String(update?.version || releases?.version || '')
  const latestTag = String(update?.latest?.tag || releases?.latest?.tag || '')
  const available = Boolean(update?.available)
  const warning = String(update?.error || releasesError || releases?.error || '').trim()
  const latest = releases?.latest ?? null
  const notesHtml = latest ? renderReleaseNotes(latest.body) : ''

  let statusTone: UpdateStatusTone = 'neutral'
  let statusText = '暂无新版本'
  if (!latestTag) {
    statusTone = 'warn'
    statusText = '版本检查未完成'
  } else if (available) {
    statusTone = 'brand'
    statusText = '有新版本可升级'
  }

  const notesEmptyText = latest
    ? ''
    : warning
      ? '当前部署没有提供发布说明：' + warning
      : '该部署没有提供发布说明，可在 Releases 查看。'

  const platform = String(releases?.platform || '')
  return {
    version,
    latestTag,
    available,
    statusTone,
    statusText,
    warning,
    notesHtml,
    notesTag: latest ? String(latest.tag || '') : '',
    notesEmptyText,
    // 只认安装包地址：下载按钮写的是「下载最新版 EXE/APK」，退化到 Releases 首页会误导。
    downloadUrl: String(latest?.asset?.browser_download_url || ''),
    downloadLabel: platform === 'android' ? '下载最新版 APK' : '下载最新版 EXE',
    installable: Boolean(latest?.installable),
    releaseId: latest && Number.isFinite(Number(latest.id)) ? Number(latest.id) : null,
    releasesUrl: String(update?.releases_url || releases?.releases_url || ''),
  }
}

/* ------------------------------------------------------------------ *
 * 发布说明：Markdown → HTML（白名单）
 * ------------------------------------------------------------------ */

const ALLOWED_TAGS = [
  'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'br', 'hr', 'strong', 'em', 'del', 'ul', 'ol', 'li',
  'blockquote', 'pre', 'code', 'a', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'input',
]

const ALLOWED_ATTR = ['href', 'title', 'align', 'type', 'checked', 'disabled']

/**
 * 旧版 release-notes.js 的 normalize：部分历史版本的 Release 正文被整段套在
 * 三个反引号 + markdown 的围栏里，直接渲染会变成一个巨大的代码块。
 */
export function normalizeReleaseNotes(body: string | null | undefined): string {
  const text = String(body || '').replace(/\r\n?/g, '\n').trim()
  if (!text) return ''
  const wrapped = /^(`{3,}|~{3,})[ \t]*(?:markdown|md)[ \t]*\n([\s\S]*?)\n\1[ \t]*$/i.exec(text)
  return wrapped ? wrapped[2].trim() : text
}

/** 链接只留 http(s)，其余去掉 href；外链一律新窗口 + noopener。 */
function hardenLinks(html: string): string {
  if (typeof document === 'undefined') return html
  const template = document.createElement('template')
  template.innerHTML = html
  template.content.querySelectorAll('a').forEach((link) => {
    const href = link.getAttribute('href') || ''
    if (!/^https?:\/\//i.test(href)) link.removeAttribute('href')
    else {
      link.setAttribute('target', '_blank')
      link.setAttribute('rel', 'noopener noreferrer')
    }
  })
  template.content.querySelectorAll('input').forEach((input) => {
    input.setAttribute('type', 'checkbox')
    input.setAttribute('disabled', 'disabled')
  })
  return template.innerHTML
}

/**
 * 发布说明渲染（强制 DOMPurify）。
 *
 * 顺序固定：normalize → marked（gfm + breaks）→ DOMPurify 白名单 → 链接硬化。
 * 不允许把 marked 的输出直接 v-html —— Release 正文是外部内容。
 */
export function renderReleaseNotes(body: string | null | undefined): string {
  const text = normalizeReleaseNotes(body)
  if (!text) return ''
  const raw = marked.parse(text, { gfm: true, breaks: true, async: false }) as string
  const safe = String(
    DOMPurify.sanitize(raw, {
      ALLOWED_TAGS,
      ALLOWED_ATTR,
      ALLOW_DATA_ATTR: false,
    }),
  )
  return hardenLinks(safe)
}

/* ------------------------------------------------------------------ *
 * 备份列表 / 大小格式化
 * ------------------------------------------------------------------ */

/** 字节数 → 人话（B / KB / MB / GB），非法值给 —。 */
export function formatBytes(value: unknown): string {
  if (value === null || value === undefined || value === '') return FMT_EMPTY
  const n = Number(value)
  if (!Number.isFinite(n) || n < 0) return FMT_EMPTY
  if (n < 1024) return Math.round(n) + ' B'
  const units = ['KB', 'MB', 'GB', 'TB']
  let size = n / 1024
  let index = 0
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024
    index += 1
  }
  return size.toFixed(size >= 100 ? 0 : 1) + ' ' + units[index]
}

/** 备份记录 → 表格行（时间列走全站 fmtTime：今天只给 HH:mm:ss）。 */
export function toBackupRow(record: BackupRecord): Record<string, unknown> {
  return {
    id: record.id,
    filename: record.filename,
    size: record.size,
    createdAt: record.createdAt,
    timeText: fmtTime(record.createdAt),
    sizeText: formatBytes(record.size),
  }
}