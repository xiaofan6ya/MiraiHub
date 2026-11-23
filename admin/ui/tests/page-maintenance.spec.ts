import { beforeEach, describe, expect, it } from 'vitest'
import {
  DEPLOY_STORAGE_KEY,
  backupFilename,
  commandFor,
  commandTabs,
  combineUpdateState,
  detectDeployMethod,
  formatBytes,
  hasCnCommands,
  normalizeReleaseNotes,
  readDeployMethod,
  renderReleaseNotes,
  saveDeployMethod,
  toBackupRow,
  type MaintenanceUpdatePayload,
  type ReleasesPayload,
} from '@/api/maintenance'
import { FMT_EMPTY } from '@/utils/format'

/** 真实响应形状：admin/routers/maintenance.py:update_info + _backup_location()。 */
function updatePayload(overrides: Partial<MaintenanceUpdatePayload> = {}): MaintenanceUpdatePayload {
  return {
    ok: true,
    version: '1.1.4',
    latest: { tag: 'v1.2.0', name: 'v1.2.0', url: 'https://github.com/xiaofan6ya/MiraiHub/releases/tag/v1.2.0', date: '2026-11-20T09:12:33Z' },
    available: true,
    error: '',
    releases_url: 'https://github.com/xiaofan6ya/MiraiHub/releases',
    database: 'sqlite',
    backup_supported: true,
    fs_ops: true,
    db_exists: true,
    db_size: 1423360,
    db_path: '/srv/miraihub/data/miraihub.db',
    backup_dir: '/srv/miraihub/data/backups',
    backup_dir_default: '/srv/miraihub/data/backups',
    backup_dir_is_default: true,
    backup_dir_writable: true,
    desktop_mode: false,
    commands: {
      docker: 'git status --short\ngit fetch origin --tags\ngit checkout v1.2.0\ndocker compose up -d --build admin',
      linux: 'git status --short\ngit fetch origin --tags\ngit checkout v1.2.0\npython -m pip install -r requirements.txt',
    },
    commands_cn: {
      docker: 'git checkout v1.2.0\ndocker compose build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple admin',
      linux: 'git checkout v1.2.0\npython -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple',
    },
    backup_note: '备份含账号凭据和 API Key，请私密保存。',
    mysql_backup_command: null,
    ...overrides,
  }
}

/** 真实响应形状：desktop/updates.py:UpdateService.check → GET /api/app/releases。 */
function releasesPayload(overrides: Partial<ReleasesPayload> = {}): ReleasesPayload {
  const latest = {
    id: 451, 
    tag: 'v1.2.0',
    name: 'MiraiHub v1.2.0',
    body: '## 更新内容\n\n- 新增维护页备份列表\n- 修复 `checkout` 后的重启提示\n\n[完整说明](https://github.com/xiaofan6ya/MiraiHub/releases/tag/v1.2.0)',
    date: '2026-11-20T09:12:33Z',
    url: 'https://github.com/xiaofan6ya/MiraiHub/releases/tag/v1.2.0',
    asset: { name: 'miraihub-1.2.0-windows-x64.exe', browser_download_url: 'https://github.com/xiaofan6ya/MiraiHub/releases/download/v1.2.0/miraihub-1.2.0-windows-x64.exe', size: 42351232 },
    checksum: { name: 'miraihub-1.2.0-windows-x64.exe.sha256' },
    installable: true,
  }
  return {
    version: '1.1.4',
    platform: 'windows',
    releases: [latest],
    latest,
    available: true,
    error: '',
    checked: true,
    releases_url: 'https://github.com/xiaofan6ya/MiraiHub/releases',
    ...overrides,
  }
}

beforeEach(() => {
  localStorage.clear()
})

describe('部署方式推断', () => {
  it('桌面外壳 → EXE，安卓外壳 → APK，都没有 → Docker 且标记未识别', () => {
    expect(detectDeployMethod({ desktop: true })).toMatchObject({ method: 'exe', detected: true })
    expect(detectDeployMethod({ mobile: true })).toMatchObject({ method: 'apk', detected: true })
    const fallback = detectDeployMethod({})
    expect(fallback.method).toBe('docker')
    expect(fallback.detected).toBe(false)
    expect(fallback.hint).toContain('无法自动区分')
    // 桌面优先于安卓：同一个外壳里两者都真时按 EXE 处理
    expect(detectDeployMethod({ desktop: true, mobile: true }).method).toBe('exe')
  })

  it('部署方式偏好落 localStorage，非法值忽略', () => {
    expect(readDeployMethod()).toBeNull()
    saveDeployMethod('source')
    expect(localStorage.getItem(DEPLOY_STORAGE_KEY)).toBe('source')
    expect(readDeployMethod()).toBe('source')
    localStorage.setItem(DEPLOY_STORAGE_KEY, 'ftp')
    expect(readDeployMethod()).toBeNull()
  })
})

describe('当前版本视图（/update + /releases 合并）', () => {
  it('有新版本：状态、发布说明、安装包与下载文案都按真实响应生成', () => {
    const view = combineUpdateState(updatePayload(), releasesPayload())
    expect(view.version).toBe('1.1.4')
    expect(view.latestTag).toBe('v1.2.0')
    expect(view.available).toBe(true)
    expect(view.statusTone).toBe('brand')
    expect(view.statusText).toBe('有新版本可升级')
    expect(view.warning).toBe('')
    expect(view.notesTag).toBe('v1.2.0')
    expect(view.notesHtml).toContain('<h2>更新内容</h2>')
    expect(view.notesHtml).toContain('<li>新增维护页备份列表</li>')
    expect(view.notesHtml).toContain('target="_blank"')
    expect(view.downloadUrl).toBe('https://github.com/xiaofan6ya/MiraiHub/releases/download/v1.2.0/miraihub-1.2.0-windows-x64.exe')
    expect(view.downloadLabel).toBe('下载最新版 EXE')
    expect(view.installable).toBe(true)
    expect(view.releaseId).toBe(451)
    expect(view.releasesUrl).toBe('https://github.com/xiaofan6ya/MiraiHub/releases')
  })

  it('安卓外壳的下载文案给 APK', () => {
    const view = combineUpdateState(updatePayload(), releasesPayload({ platform: 'android' }))
    expect(view.downloadLabel).toBe('下载最新版 APK')
  })

  it('releases 503（生产镜像无更新模块）：只降级说明与安装入口，版本结论保留', () => {
    const view = combineUpdateState(
      updatePayload({ available: false, latest: null, commands: null, commands_cn: null, error: '当前部署不包含客户端更新模块（生产镜像按最小面构建，请用镜像升级）' }),
      null,
      '当前部署不包含客户端更新模块（生产镜像按最小面构建，请用镜像升级）',
    )
    expect(view.statusTone).toBe('warn')
    expect(view.statusText).toBe('版本检查未完成')
    expect(view.notesHtml).toBe('')
    expect(view.notesEmptyText).toContain('当前部署没有提供发布说明')
    expect(view.notesEmptyText).toContain('生产镜像')
    expect(view.downloadUrl).toBe('')
    expect(view.installable).toBe(false)
    expect(view.releaseId).toBeNull()
  })

  it('没有新版本：neutral + 暂无新版本，没有发布说明时给出通用说明', () => {
    const view = combineUpdateState(updatePayload({ available: false }), releasesPayload({ latest: null }))
    expect(view.statusTone).toBe('neutral')
    expect(view.statusText).toBe('暂无新版本')
    expect(view.notesEmptyText).toBe('该部署没有提供发布说明，可在 Releases 查看。')
  })
})

describe('发布说明渲染（marked + DOMPurify）', () => {
  it('剥掉被整段包在 markdown 围栏里的历史正文', () => {
    const fenced = '```markdown\n## 更新内容\n\n- 修复登录\n```'
    expect(normalizeReleaseNotes(fenced)).toBe('## 更新内容\n\n- 修复登录')
    expect(normalizeReleaseNotes('## 更新内容')).toBe('## 更新内容')
    expect(normalizeReleaseNotes('')).toBe('')
    expect(normalizeReleaseNotes(null)).toBe('')
  })

  it('脏 HTML 被白名单过滤：script / onerror / javascript: 链接都不留', () => {
    const html = renderReleaseNotes(
      '## 标题\n\n<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>\n\n[点我](javascript:alert(1))\n\n[公告](https://github.com/xiaofan6ya/MiraiHub/releases)',
    )
    expect(html).not.toContain('<script')
    expect(html).not.toContain('onerror')
    expect(html).not.toContain('javascript:')
    expect(html).toContain('href="https://github.com/xiaofan6ya/MiraiHub/releases"')
    expect(html).toContain('rel="noopener noreferrer"')
  })
})

describe('升级命令适配', () => {
  it('按部署方式与国内网络开关取命令', () => {
    const payload = updatePayload()
    expect(commandFor(payload, 'docker')).toContain('docker compose up -d --build admin')
    expect(commandFor(payload, 'linux')).toContain('python -m pip install -r requirements.txt')
    expect(commandFor(payload, 'linux', true)).toContain('pypi.tuna.tsinghua.edu.cn')
    expect(commandTabs(payload)).toEqual(['docker', 'linux'])
    expect(hasCnCommands(payload)).toBe(true)
  })

  it('后端没给命令时返回空串，页签为空', () => {
    const payload = updatePayload({ commands: null, commands_cn: null })
    expect(commandTabs(payload)).toEqual([])
    expect(hasCnCommands(payload)).toBe(false)
    expect(commandFor(payload, 'docker')).toBe('')
    expect(commandFor(null, 'docker')).toBe('')
  })
})

describe('备份记录展示', () => {
  it('大小按 B/KB/MB 格式化，空值给 —', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(1023)).toBe('1023 B')
    expect(formatBytes(1024)).toBe('1.0 KB')
    expect(formatBytes(1423360)).toBe('1.4 MB')
    expect(formatBytes(42351232)).toBe('40.4 MB')
    expect(formatBytes(null)).toBe(FMT_EMPTY)
    expect(formatBytes(-1)).toBe(FMT_EMPTY)
  })

  it('备份记录 → 表格行（时间走统一 fmtTime，大小带单位）', () => {
    const row = toBackupRow({
      id: 'backup-1',
      filename: 'workbuddy-backup-20261123-021500.db',
      size: 1423360,
      createdAt: new Date().toISOString(),
      blob: new Blob(['x']),
    })
    expect(row.id).toBe('backup-1')
    expect(row.sizeText).toBe('1.4 MB')
    // 今天的时间只给 HH:mm:ss
    expect(String(row.timeText)).toMatch(/^\d{2}:\d{2}:\d{2}$/)
  })

  it('下载文件名按后端口径（UTC）', () => {
    const name = backupFilename(new Date(Date.UTC(2026, 10, 23, 2, 15, 7)))
    expect(name).toBe('workbuddy-backup-20261123-021507.db')
  })
})
