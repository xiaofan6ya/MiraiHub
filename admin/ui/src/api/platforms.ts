/**
 * 平台元信息（/api/platforms）。
 *
 * 这不是"给账号列表加个筛选"：切平台 = 换全局上下文，后端按 X-Platform 收窄
 * 所有统计口径（见 docs/ui-v2/contracts/platform-context.md）。
 * 平台列表与能力一律由后端下发，前端不硬编码平台 id —— 新增平台前端零改动。
 *
 * 后端：admin/platform/router.py（_serialize / list_platforms / get_current / set_current）。
 */
import { http } from './http'

/** 区域（如 Trae 的国内版 / 国际版）。 */
export interface PlatformRealm {
  key: string
  label: string
  /** 平台声明的默认区域。 */
  default?: boolean
}

/**
 * 平台自评等级。r4：分级功能已从前端删除，这里只保留类型以兼容后端仍下发的字段，前端不再读取。
 */
export interface PlatformRating {
  /** 只放行 hang / elite / npc / bad 四档，未知档位后端已丢弃。 */
  tier?: 'hang' | 'elite' | 'npc' | 'bad' | string
  reason?: string
  /** 打分细则（可选，前端暂未消费）。 */
  scores?: Record<string, number>
  source?: string
}

/** 平台声明的动作：前端据此渲染"扫描 / 导入 / 刷新额度…"入口。 */
export type PlatformAction =
  | 'scan'
  | 'import'
  | 'refresh-quota'
  | 'usage'
  | 'web-cookie'
  | 'checkin'
  | 'begin-auth'
  | string

/**
 * /api/platforms 下发的单个平台。
 *
 * 字段名与后端 _serialize() 完全一致（snake_case 原样保留，不做转换）。
 */
export interface PlatformSpec {
  /** 对外平台 id（trae-cn / trae-intl 与 trae 是不同 item）。 */
  id: string
  label: string
  enabled: boolean
  supports_local_scan: boolean
  /** local-scan | web-login | import-only（由平台声明，决定"加号"入口）。 */
  auth_mode: string
  default_realm: string
  notes: string
  /** 图标 key，PlatformLogo 组件据此取图。 */
  icon: string
  /** 落库平台（trae-cn 与 trae-intl 都落 trae）。 */
  storage_platform: string
  /** 单区平台的固定区域；多区平台为空串。 */
  realm: string
  realm_label: string
  realms: PlatformRealm[]
  /** 是否经网关代理（后端字段名 proxied）。 */
  proxied: boolean
  /** r4：前端不再消费（分级已删除），保留可选以兼容后端响应。 */
  rating?: PlatformRating
  /** 平台自定义扩展（actions / quota_unit 的原始来源）。 */
  extra: Record<string, unknown>
  /** 是否提供管理契约（账号池 / 任务 / 客户端探测 / 概览卡片）。 */
  managed: boolean
  actions: PlatformAction[]
  /** 额度口径：credits（积分制）/ token（令牌钱包）。 */
  quota_unit: 'credits' | 'token' | string
  quota_unit_label: string
  /** 账号数（按落库平台 + 区域统计）。 */
  account_count?: number | null
  /** 每个区域的账号数：{cn: 3, intl: 0}。 */
  realm_counts?: Record<string, number>
  /**
   * 可用（status=active）账号数。**可选**：当前后端还没有这个字段（已提给后端工作流），
   * 有它时顶栏平台选择器才能区分「有账号但全部禁用」（琥珀）与「有可用账号」（绿）。
   */
  active_count?: number | null
}

/** 平台装配状态（admin/platform/loader.py: status()）。 */
export interface PlatformLoaderStatus {
  loaded: string[]
  failed: Record<string, string>
}

export interface PlatformListPayload {
  items: PlatformSpec[]
  /** 默认平台（registry.DEFAULT_PLATFORM，恒为 workbuddy）。 */
  default: string
  loader: PlatformLoaderStatus
}

export interface PlatformCurrent {
  platform: string
  realm: string
  /** 数据库里存的原始选择器（如 "trae:cn"）。 */
  raw?: string
}

export interface PlatformCurrentPayload {
  ok: boolean
  platform?: string
  realm?: string
  raw?: string
  /** 后端遇到未知平台时回 {ok:false, error}（HTTP 仍是 200）。 */
  error?: string
}

/**
 * 平台列表 + 每个平台（每个区域）的账号数。
 *
 * 账号数随列表一起下发：否则卡片要按平台数逐个再打接口（N 次串行）。
 * 注意它是"上一次请求的缓存"，导入/删除账号后必须重新调用。
 */
export function listPlatforms(signal?: AbortSignal): Promise<PlatformListPayload> {
  return http<PlatformListPayload>('GET', '/api/platforms', signal ? { signal } : {})
}

/**
 * 服务端记住的当前平台。
 *
 * 后端读 system_settings['platform:current']，没有则回落默认平台；
 * 返回的 platform/realm 已经过 registry.resolve() 归一化。
 */
export function getCurrentPlatform(): Promise<PlatformCurrent> {
  return http<PlatformCurrent>('GET', '/api/platforms/current')
}

/**
 * 记住当前平台（按登录会话共享，落 system_settings）。
 *
 * 未知平台时后端回 {ok:false, error}，不会抛错 —— 调用方需要自己看 ok。
 */
export function putCurrentPlatform(body: { platform: string; realm?: string }): Promise<PlatformCurrentPayload> {
  return http<PlatformCurrentPayload>('PUT', '/api/platforms/current', {
    body: { platform: body.platform, realm: body.realm || '' },
  })
}
