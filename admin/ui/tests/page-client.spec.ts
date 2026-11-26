/**
 * S27 客户端参数：数据转换 / 接口适配纯函数单测。
 * 响应形状按 admin/routers/client_profile.py 与 admin/client_profile.py 的真实返回构造。
 */
import { describe, expect, it } from 'vitest'
import {
  FINGERPRINT_FIELDS,
  VERSION_FIELDS,
  buildProfilePayload,
  dirtyFields,
  headerRows,
  profileForm,
  runtimeView,
  scanView,
  sourceLabel,
  sourceRows,
  sourceTone,
  type ClientProfilePayload,
  type RuntimeDiagnostics,
} from '@/api/client'

/** GET /api/client-profile 的 effective（合成后的生效档案）。 */
const EFFECTIVE: Record<string, unknown> = {
  desktop_version: '5.9.0',
  cli_version: '2.165.0',
  ide_name: 'WorkBuddy',
  ide_type: 'WorkBuddy',
  product: 'SaaS',
  application_name: 'WorkBuddy',
  turing_channel_id: 109144,
  turing_product_name: 'WorkBuddy',
  commit: 'abc123',
  release_date_ms: 1761858456000,
  node_version: 'v22.21.1',
  stainless_package_version: '6.25.0',
  fp_product: 'SaaS',
  ext_name: 'workbuddy-desktop',
  os: 'win32',
  arch: 'x64',
  os_version: '10.0.26220',
  cpu_cores: 20,
  memory_size: 24,
  timezone: 'Asia/Shanghai',
  report_delay: 2000,
  headers: {
    'X-CodeBuddy-Request': '1',
    'X-Requested-With': 'XMLHttpRequest',
    'Accept-Language': 'zh-CN',
  },
  user_agent: 'WorkBuddy/5.9.0 WorkBuddy/5.9.0 CLI/2.165.0',
}

const DIAG: RuntimeDiagnostics = {
  platform: 'win32',
  device_token: {
    available: true,
    source: 'native helper',
    checked: true,
    node_available: true,
    helper_available: true,
    cached_failure: false,
  },
  header_groups: {
    client_profile: ['X-CodeBuddy-Request', 'X-IDE-Name', 'X-IDE-Version', 'X-Product', 'X-Requested-With'],
    sdk_stainless: ['X-Stainless-Lang', 'X-Stainless-Runtime-Version'],
    account_session: ['Authorization', 'User-Agent'],
    request_trace: ['X-Request-ID', 'traceparent'],
  },
}

const PAYLOAD: ClientProfilePayload = {
  effective: EFFECTIVE,
  saved: { desktop_version: '5.9.0' },
  detected: { desktop_version: '5.9.0', os: 'win32' },
  detected_origins: { desktop_version: '安装包', os: '本机系统' },
  detected_package: { desktop_version: '5.9.0' },
  detected_system: { os: 'win32' },
  sources: {
    desktop_version: '现场探测（安装包）',
    cli_version: '已保存',
    os: '现场探测（本机系统）',
    ide_name: '内置兜底',
    turing_channel_id: '环境变量 WORKBUDDY_TURING_CHANNEL_ID',
    headers: '合并（兜底+保存+探测）',
    source_mode: 'auto',
  },
  source: 'auto',
  defaults: {},
  has_client: true,
  diff_saved_vs_effective: {},
  runtime_diagnostics: DIAG,
  dirs: ['C:\\Users\\me\\.workbuddy', 'C:\\Users\\me\\AppData\\Roaming\\WorkBuddy'],
  ua: 'WorkBuddy/5.9.0 WorkBuddy/5.9.0 CLI/2.165.0',
  platform: 'workbuddy',
  product_version: '5.9.0',
}

describe('表单转换与校验', () => {
  it('profileForm 把生效值转成字符串（数字也转）', () => {
    const form = profileForm(EFFECTIVE, VERSION_FIELDS)
    expect(form.desktop_version).toBe('5.9.0')
    expect(form.turing_channel_id).toBe('109144')
    expect(form.release_date_ms).toBe('1761858456000')
    expect(profileForm(null, VERSION_FIELDS).desktop_version).toBe('')
  })

  it('buildProfilePayload：留空跳过、int 转数字、非法值报错', () => {
    const form = profileForm(EFFECTIVE, VERSION_FIELDS)
    const result = buildProfilePayload(form, VERSION_FIELDS)
    expect(result.errors).toEqual([])
    expect(result.profile.desktop_version).toBe('5.9.0')
    expect(result.profile.turing_channel_id).toBe(109144)
    expect(result.profile.release_date_ms).toBe(1761858456000)

    const empty = buildProfilePayload({ desktop_version: '', turing_channel_id: '' }, VERSION_FIELDS)
    expect(empty.profile).toEqual({})
    expect(empty.errors).toEqual([])

    const bad = buildProfilePayload({ turing_channel_id: '12.5', cpu_cores: '-1' }, [
      ...VERSION_FIELDS,
      ...FINGERPRINT_FIELDS,
    ])
    expect(bad.errors).toEqual(['风控 channelId 必须是 ≥0 的整数', 'CPU 核数 必须是 ≥0 的整数'])
    expect(bad.profile).toEqual({})
  })

  it('字符串超过 512 字符时报错', () => {
    const result = buildProfilePayload({ desktop_version: 'x'.repeat(513) }, VERSION_FIELDS)
    expect(result.errors[0]).toContain('不能超过 512')
    expect(result.profile).toEqual({})
  })

  it('dirtyFields 只返回 trim 后有差异的键', () => {
    const baseline = profileForm(EFFECTIVE, FINGERPRINT_FIELDS)
    expect(dirtyFields(baseline, baseline, FINGERPRINT_FIELDS)).toEqual([])
    const form = { ...baseline, cpu_cores: ' 21 ', memory_size: '24' }
    expect(dirtyFields(form, baseline, FINGERPRINT_FIELDS)).toEqual(['cpu_cores'])
  })
})

describe('来源徽标', () => {
  it('环境变量 warn、安装包/本机系统/现场探测 ok、已保存 brand、兜底 neutral', () => {
    expect(sourceTone('环境变量 WORKBUDDY_OS')).toBe('warn')
    expect(sourceTone('现场探测（安装包）')).toBe('ok')
    expect(sourceTone('现场探测（本机系统）')).toBe('ok')
    expect(sourceTone('现场探测')).toBe('ok')
    expect(sourceTone('已保存')).toBe('brand')
    expect(sourceTone('内置兜底')).toBe('neutral')
  })

  it('短标签与完整来源', () => {
    expect(sourceLabel('现场探测（安装包）')).toBe('安装包实测')
    expect(sourceLabel('现场探测（本机系统）')).toBe('本机系统')
    expect(sourceLabel('环境变量 WORKBUDDY_UA')).toBe('环境变量')
    expect(sourceLabel('已保存')).toBe('已保存')
    expect(sourceLabel('内置兜底')).toBe('兜底默认')
    expect(sourceLabel('')).toBe('—')
  })

  it('sourceRows 按键排序并排除 source_mode', () => {
    const rows = sourceRows(PAYLOAD.sources)
    expect(rows.map((row) => row.key)).toEqual([
      'cli_version',
      'desktop_version',
      'headers',
      'ide_name',
      'os',
      'turing_channel_id',
    ])
    expect(rows[2]).toMatchObject({ key: 'headers', tone: 'neutral' })
    expect(sourceRows(null)).toEqual([])
  })
})

describe('运行时诊断', () => {
  it('全部可用：warning=false，行文案取自 device_token', () => {
    const view = runtimeView(DIAG)
    expect(view.warning).toBe(false)
    expect(view.rows.map((row) => row.label)).toEqual(['运行平台', '设备令牌', 'Node 运行时', 'Native helper'])
    expect(view.rows[1]).toMatchObject({ value: '原生 SDK 令牌可用（本机实测）', tone: 'ok' })
    expect(view.groups.map((group) => group.name)).toEqual(['account_session', 'client_profile', 'request_trace', 'sdk_stainless'])
  })

  it('令牌不可用 / 无缓存 / 缺 Node 时 warning=true', () => {
    const view = runtimeView({
      platform: 'linux',
      device_token: { available: false, checked: false, node_available: false, helper_available: false },
      header_groups: {},
    })
    expect(view.warning).toBe(true)
    expect(view.rows[1].value).toBe('尚无 SDK 令牌缓存')
    expect(view.rows[2].tone).toBe('warn')
    expect(view.groups).toEqual([])
  })
})

describe('扫描位置', () => {
  it('目录 + 凭据位置 → 已检测到客户端，明文/身份保留', () => {
    const view = scanView(PAYLOAD, {
      ok: true,
      platform: 'workbuddy',
      has_client: true,
      detected: {
        items: [
          { source: 'C:\\a\\auth-session.json', uid_hint: '193****2486', has_plain_credential: true },
          { dir: 'C:\\b\\auth.v1.dat', account_id: 12, has_plain_credential: false },
        ],
        base_url: 'https://api.example.com',
      },
      errors: ['探测超时'],
    })
    expect(view.stateText).toBe('已检测到客户端')
    expect(view.dirs).toEqual(PAYLOAD.dirs)
    expect(view.items[0]).toEqual({ where: 'C:\\a\\auth-session.json', who: '193****2486', plain: true })
    expect(view.items[1].who).toBe('12')
    expect(view.baseUrl).toBe('https://api.example.com')
    expect(view.errors).toEqual(['探测超时'])
  })

  it('只有探测参数 / 完全没有客户端', () => {
    const paramsOnly = scanView({ ...PAYLOAD, dirs: [] }, null)
    expect(paramsOnly.stateText).toBe('已探测到参数 · 本机没有该客户端的数据目录')
    const none = scanView({ ...PAYLOAD, dirs: [], has_client: false, detected: {} }, null)
    expect(none.stateText).toBe('未检测到客户端')
    expect(none.dirs).toEqual([])
  })
})

describe('风控 / 审计请求头表', () => {
  it('档案头给出真实值，X-IDE-* 从生效档案派生，运行时组标 —', () => {
    const rows = headerRows(PAYLOAD)
    const byName = Object.fromEntries(rows.map((row) => [row.name, row]))
    expect(byName['X-CodeBuddy-Request']).toMatchObject({ value: '1', source: '档案值（探测/保存/兜底）' })
    expect(byName['X-IDE-Name']).toMatchObject({ value: 'WorkBuddy', source: '档案值（探测/保存/兜底）' })
    expect(byName['X-IDE-Version']).toMatchObject({ value: '5.9.0' })
    expect(byName['X-Product']).toMatchObject({ value: 'SaaS' })
    expect(byName['X-Stainless-Lang']).toMatchObject({ value: '—（运行时生成）', source: 'SDK 资料（仅在明确使用该 SDK 的业务中适用）' })
    expect(byName['Authorization']).toMatchObject({ source: '账号凭据 / 会话字段' })
    expect(byName['traceparent']).toMatchObject({ source: '调用方明确提供的关联字段' })
  })

  it('没有运行时诊断时退化为 effective.headers；空数据返回空', () => {
    const rows = headerRows({ ...PAYLOAD, runtime_diagnostics: {} })
    expect(rows.map((row) => row.name)).toEqual(['X-CodeBuddy-Request', 'X-Requested-With', 'Accept-Language'])
    expect(headerRows(null)).toEqual([])
  })
})
