/**
 * 预警管理（/api/alerts/*）。
 *
 * 后端：
 *   admin/platform/alerts_router.py —— get_config / put_config / send_test
 *   admin/alerting.py              —— channels_summary / config_summary / deliver / CHANNEL_LABELS
 *
 * 这个文件同时放着「接口响应 → 页面表单/展示数据」的纯函数：契约 §二、§三 的每条规则
 * 都在这里落地，页面只负责摆放。后端字段名保持 snake_case，不在这里改名。
 *
 * 三个必须守住的后端语义（写错就会静默丢配置）：
 *   1. PUT 是全量覆盖：没发的字段会落到后端默认值（enabled=true / cooldown=600 / channels=[]），
 *      所以 buildAlertConfigBody() 永远产出完整请求体；
 *   2. 密码 / 一条式授权码只写不读：GET 只会回 "********" 或 has_* 标记，
 *      表单初值必须是空串（留空 = 不修改），掩码串也绝不能回写；
 *   3. 渠道没有 per-channel 启用位：config.channels 只有 {type,target}，
 *      「停用」只能靠保存时把它从数组里剔除（见契约 §八.2）。
 */
import { http } from './http'

/* -------------------------------------------------------------------------- */
/* 后端形状                                                                    */
/* -------------------------------------------------------------------------- */

/** PUT 时的单条渠道：后端只认这两个字段，空 type 或空 target 会被丢弃。 */
export interface AlertChannelConfig {
  type: string
  target: string
}

/** GET 的 channels[]：alerting.channels_summary() 的返回项。 */
export interface AlertChannelSummary {
  type: string
  label: string
  target: string
  /** 后端只按「target 非空」判断，没有真正的停用位。 */
  enabled: boolean
}

/** GET 的 smtp：alerting.config_summary() 的邮件链路体检。 */
export interface AlertSmtpHealth {
  host: string
  port: number
  user: string
  from: string
  mail_to: string
  ssl: boolean
  /** 是否在走 `qqSMTP:授权码` 一条式。 */
  compact: boolean
  has_password: boolean
  ready: boolean
  missing: string[]
}

/** GET 的 config：system_settings["alerts:config"] 的落库值（未保存过时为空对象）。 */
export interface AlertConfigStored {
  enabled?: boolean
  cooldown?: number
  channels?: AlertChannelConfig[]
  smtp_host?: string
  smtp_port?: number
  smtp_user?: string
  /** 恒为掩码 "********"（只有存在密码时才出现）。 */
  smtp_password?: string
  has_password?: boolean
  smtp_from?: string
  smtp_starttls?: boolean
  /** 一条式简写 `qqSMTP:授权码` 的掩码。 */
  smtp?: string
  has_smtp?: boolean
  mail_to?: string
}

export interface AlertConfigPayload {
  config: AlertConfigStored
  channels: AlertChannelSummary[]
  labels: Record<string, string>
  /** 顶层回声，与 config.enabled 同源。 */
  enabled: boolean
  smtp: AlertSmtpHealth
}

/** PUT 请求体：字段与 ConfigIn 一一对应，一个都不能省（全量覆盖）。 */
export interface AlertConfigSave {
  enabled: boolean
  cooldown: number
  channels: AlertChannelConfig[]
  smtp_host: string
  smtp_port: number
  smtp_user: string
  smtp_password: string
  smtp_from: string
  smtp_starttls: boolean
  smtp: string
  mail_to: string
}

export interface AlertSaveResult {
  ok: boolean
  channels: AlertChannelSummary[]
}

/** POST /api/alerts/test 的 results[]。 */
export interface AlertTestChannelResult {
  type: string
  target: string
  ok: boolean
  error: string
}

export interface AlertTestResult {
  /** 全部渠道成功且至少一个渠道时才为 true。 */
  ok: boolean
  title: string
  text: string
  level: string
  results: AlertTestChannelResult[]
  smtp?: AlertSmtpHealth
}

/* -------------------------------------------------------------------------- */
/* 接口                                                                        */
/* -------------------------------------------------------------------------- */

/** 当前配置（**不含密码**，只有 has_* 标记）。 */
export function getAlertConfig(): Promise<AlertConfigPayload> {
  return http<AlertConfigPayload>('GET', '/api/alerts/config')
}

/** 保存渠道与 SMTP 设置（全量）。后端顺带 reset_cooldown()，立刻生效。 */
export function putAlertConfig(body: AlertConfigSave): Promise<AlertSaveResult> {
  return http<AlertSaveResult>('PUT', '/api/alerts/config', { body })
}

/** 发一条测试通知。**用已保存的配置**，没有单渠道版本。 */
export function sendAlertTest(): Promise<AlertTestResult> {
  return http<AlertTestResult>('POST', '/api/alerts/test')
}

/* -------------------------------------------------------------------------- */
/* 常量                                                                        */
/* -------------------------------------------------------------------------- */

/** 与旧版 `#alertCooldown min=30 max=86400` 一致。 */
export const ALERT_COOLDOWN_MIN = 30
export const ALERT_COOLDOWN_MAX = 86400
export const ALERT_COOLDOWN_DEFAULT = 600
export const SMTP_PORT_DEFAULT = 465
/** 后端的掩码串：只用于显示占位，绝不能回写。 */
export const SMTP_MASK = '********'
/** 后端约定的「清空一条式授权码」标记。 */
export const SMTP_CLEAR = '__clear__'

/** 后端 CHANNEL_LABELS 的兜底副本（labels 缺失时下拉也要能显示中文）。 */
export const FALLBACK_CHANNEL_LABELS: Record<string, string> = {
  email: '邮件（SMTP）',
  feishu: '飞书群机器人',
  qqbot: 'QQ 机器人',
}

/** 渠道类型下拉顺序，与旧版 ui-select 的 options 一致。 */
export const ALERT_CHANNEL_TYPES = ['feishu', 'qqbot', 'email'] as const

/* -------------------------------------------------------------------------- */
/* 渠道类型元信息（类型切换 → 动态字段）                                        */
/* -------------------------------------------------------------------------- */

export type AlertChannelKind = 'email' | 'webhook' | 'unknown'
export type AlertChannelRule = 'email' | 'url' | 'qqbot' | 'text'

export interface AlertChannelMeta {
  type: string
  label: string
  kind: AlertChannelKind
  /** 目标输入框的 placeholder。 */
  placeholder: string
  /** 目标输入框下方的帮助文字（旧版 applyAlertChannelType 的说明）。 */
  hint: string
  rule: AlertChannelRule
}

/** 类型 → 目标字段的占位/说明/校验规则（旧版 applyAlertChannelType 的移植）。 */
export function alertChannelMeta(type: string, labels: Record<string, string> = {}): AlertChannelMeta {
  const label = labels[type] || FALLBACK_CHANNEL_LABELS[type] || type
  if (type === 'email') {
    return {
      type,
      label,
      kind: 'email',
      placeholder: '收件邮箱，例如 me@qq.com（多个用英文逗号分隔）',
      hint: '邮件渠道需要两步：这里填收件邮箱；下方「SMTP（邮件渠道）」里填服务器与授权码（QQ 邮箱可用一条式 qqSMTP:授权码）。',
      rule: 'email',
    }
  }
  if (type === 'feishu') {
    return {
      type,
      label,
      kind: 'webhook',
      placeholder: 'https://open.feishu.cn/open-apis/bot/v2/hook/…',
      hint: '填写飞书群「自定义机器人」的 Webhook 地址；多个渠道会同时发送。',
      rule: 'url',
    }
  }
  if (type === 'qqbot') {
    return {
      type,
      label,
      kind: 'webhook',
      placeholder: 'http://127.0.0.1:5700/send_group_msg?group=群号',
      hint: 'QQ 群机器人（go-cqhttp / OneBot）地址，末尾必须带 ?group=群号。',
      rule: 'qqbot',
    }
  }
  return {
    type,
    label,
    kind: 'unknown',
    placeholder: '渠道地址',
    hint: '后端不认识这个渠道类型，发送时会报「未知渠道类型」。',
    rule: 'text',
  }
}

/** 目标输入的校验：返回空串表示通过（规则与旧版 alertsAddChannel + 后端一致）。 */
export function validateChannelTarget(type: string, target: string): string {
  const value = String(target ?? '').trim()
  if (!value) return '请填写地址'
  const meta = alertChannelMeta(type)
  if (meta.rule === 'email') {
    const parts = value.split(',').map((part) => part.trim()).filter(Boolean)
    if (!parts.length) return '请填写收件邮箱'
    for (const part of parts) {
      if (!/^[^@\s,]+@[^@\s,]+\.[^@\s,]+$/.test(part)) {
        return '收件邮箱格式不对，例如 me@qq.com（多个用英文逗号分隔）'
      }
    }
    return ''
  }
  if (meta.rule === 'url' || meta.rule === 'qqbot') {
    if (!/^https?:\/\//i.test(value)) return 'Webhook 地址需以 http(s):// 开头'
  }
  if (meta.rule === 'qqbot' && !/[?&]group=/.test(value)) {
    return 'QQ 机器人地址需带 ?group=群号'
  }
  return ''
}

/* -------------------------------------------------------------------------- */
/* 表单模型                                                                    */
/* -------------------------------------------------------------------------- */

/** 页面编辑态的一条渠道。 */
export interface AlertChannelView {
  /** 列表 key：后端没有 id，用 type + target 唯一化。 */
  key: string
  type: string
  label: string
  target: string
  /** 编辑态开关（关闭 → 保存时剔除，见契约 §八.2）。 */
  enabled: boolean
  /** 由 config.mail_to 自动补出来的邮件渠道：只读，删不掉。 */
  auto: boolean
}

/** 页面表单：字段名可口，提交前由 buildAlertConfigBody 映射回后端字段。 */
export interface AlertConfigForm {
  enabled: boolean
  cooldown: number
  channels: AlertChannelView[]
  smtpHost: string
  smtpPort: number | null
  smtpUser: string
  smtpPassword: string
  smtpFrom: string
  smtpStarttls: boolean
  smtpCompact: string
  mailTo: string
  /** 已点「清除授权码」：保存时发 __clear__。 */
  clearCompact: boolean
}

export function channelKey(type: string, target: string): string {
  return type + '::' + target
}

/** GET → 渠道列表（含「默认收件邮箱派生」标记）。 */
export function toChannelViews(payload: AlertConfigPayload | null | undefined): AlertChannelView[] {
  if (!payload) return []
  const config = payload.config ?? {}
  const labels = { ...FALLBACK_CHANNEL_LABELS, ...(payload.labels || {}) }
  const stored = Array.isArray(config.channels) ? config.channels : []
  const mailTo = String(config.mail_to || '').trim()
  const hasExplicitEmail = stored.some((item) => String(item?.type || '').toLowerCase() === 'email')
  const summary = Array.isArray(payload.channels) ? payload.channels : []
  // 正常情况看 channels 摘要（它就是「谁真的会收到」）；摘要空但落库有值时退回 config.channels。
  const source: AlertChannelSummary[] = summary.length
    ? summary
    : stored.map((item) => ({
        type: String(item?.type || ''),
        label: labels[String(item?.type || '')] || String(item?.type || ''),
        target: String(item?.target || ''),
        enabled: true,
      }))
  return source
    .filter((item) => String(item?.type || '').trim())
    .map((item) => {
      const type = String(item.type).trim()
      const target = String(item.target || '').trim()
      const auto =
        Boolean(mailTo) &&
        !hasExplicitEmail &&
        type.toLowerCase() === 'email' &&
        target === mailTo
      return {
        key: channelKey(type, target),
        type,
        label: labels[type] || String(item.label || '') || type,
        target,
        enabled: item.enabled !== false,
        auto,
      }
    })
}

/** GET → 表单初值。密码与授权码恒为空串（后端不回显，留空即不修改）。 */
export function alertFormFromPayload(payload: AlertConfigPayload): AlertConfigForm {
  const config = payload.config ?? {}
  return {
    enabled: config.enabled !== false && payload.enabled !== false,
    cooldown: clampCooldown(config.cooldown),
    channels: toChannelViews(payload),
    smtpHost: String(config.smtp_host || ''),
    smtpPort: normalizePort(config.smtp_port),
    smtpUser: String(config.smtp_user || ''),
    smtpPassword: '',
    smtpFrom: String(config.smtp_from || ''),
    smtpStarttls: config.smtp_starttls !== false,
    smtpCompact: '',
    mailTo: String(config.mail_to || ''),
    clearCompact: false,
  }
}

export function cloneAlertForm(form: AlertConfigForm): AlertConfigForm {
  return { ...form, channels: form.channels.map((channel) => ({ ...channel })) }
}

/** 掩码串不能回写；其它值原样（密码可能含空格，不 trim）。 */
function sanitizeSecret(value: unknown): string {
  const text = value === null || value === undefined ? '' : String(value)
  return text === SMTP_MASK ? '' : text
}

function trimText(value: unknown): string {
  return value === null || value === undefined ? '' : String(value).trim()
}

/** 去重窗口：30–86400 秒，非法值回落 600（后端还有一次 max(30, n)）。 */
export function clampCooldown(value: unknown): number {
  const n = Number(value)
  if (!Number.isFinite(n) || n <= 0) return ALERT_COOLDOWN_DEFAULT
  return Math.min(ALERT_COOLDOWN_MAX, Math.max(ALERT_COOLDOWN_MIN, Math.round(n)))
}

/** 端口：1–65535，非法值回落 465。 */
export function normalizePort(value: unknown): number {
  const n = Number(value)
  if (!Number.isFinite(n) || n < 1 || n > 65535) return SMTP_PORT_DEFAULT
  return Math.trunc(n)
}

/** 表单 → PUT 请求体（全量；停用/派生渠道不写回）。 */
export function buildAlertConfigBody(form: AlertConfigForm): AlertConfigSave {
  const channels: AlertChannelConfig[] = []
  const seen = new Set<string>()
  for (const channel of form.channels || []) {
    if (!channel || channel.auto || channel.enabled === false) continue
    const type = trimText(channel.type)
    const target = trimText(channel.target)
    if (!type || !target) continue
    const key = channelKey(type, target)
    if (seen.has(key)) continue
    seen.add(key)
    channels.push({ type, target })
  }
  return {
    enabled: form.enabled !== false,
    cooldown: clampCooldown(form.cooldown),
    channels,
    smtp_host: trimText(form.smtpHost),
    smtp_port: normalizePort(form.smtpPort),
    smtp_user: trimText(form.smtpUser),
    smtp_password: sanitizeSecret(form.smtpPassword),
    smtp_from: trimText(form.smtpFrom),
    smtp_starttls: Boolean(form.smtpStarttls),
    smtp: form.clearCompact ? SMTP_CLEAR : sanitizeSecret(form.smtpCompact).trim(),
    mail_to: trimText(form.mailTo),
  }
}

/** 与上次保存的快照比对（含「待清除授权码」标记与未提交的密码）。 */
export function isConfigDirty(current: AlertConfigForm, saved: AlertConfigForm): boolean {
  return JSON.stringify(buildAlertConfigBody(current)) !== JSON.stringify(buildAlertConfigBody(saved))
}

/* -------------------------------------------------------------------------- */
/* 展示适配                                                                    */
/* -------------------------------------------------------------------------- */

/** 秒 → 人话（600 → 「10 分钟」），用于去重窗口的说明。 */
export function describeCooldown(seconds: unknown): string {
  const n = clampCooldown(seconds)
  if (n % 3600 === 0) return n / 3600 + ' 小时'
  if (n % 60 === 0) return n / 60 + ' 分钟'
  if (n >= 60) return Math.floor(n / 60) + ' 分 ' + (n % 60) + ' 秒'
  return n + ' 秒'
}

export interface SmtpHealthView {
  tone: 'ok' | 'warn'
  text: string
}

/** 邮件链路体检（旧版 renderSmtpHealth 的移植）。 */
export function describeSmtpHealth(smtp: AlertSmtpHealth | null | undefined): SmtpHealthView {
  if (!smtp) return { tone: 'warn', text: '没有拿到邮件链路状态，点「重新加载」再试。' }
  const server = smtp.host ? smtp.host + ':' + smtp.port + (smtp.ssl ? ' SSL' : '') : '服务器未配置'
  if (smtp.ready) {
    const sender = smtp.from || smtp.user || '未配置发件人'
    return {
      tone: 'ok',
      text: '邮件链路就绪：' + sender + ' → ' + (smtp.mail_to || '渠道收件人') + '（' + server + '）',
    }
  }
  const missing = (smtp.missing || []).filter(Boolean)
  return {
    tone: 'warn',
    text: missing.length ? '邮件链路还差：' + missing.join('、') : '邮件链路未就绪',
  }
}

export interface TestSummary {
  tone: 'success' | 'warning' | 'error'
  text: string
}

/** POST /api/alerts/test 的结果 → 一句 Toast。 */
export function summarizeTestResult(
  result: AlertTestResult | null | undefined,
  labels: Record<string, string> = FALLBACK_CHANNEL_LABELS,
): TestSummary {
  const results = Array.isArray(result?.results) ? result.results : []
  if (!results.length) {
    return { tone: 'error', text: '发送测试通知失败：没有配置任何渠道，先添加渠道并保存' }
  }
  const nameOf = (type: string): string => labels[type] || FALLBACK_CHANNEL_LABELS[type] || type || '未知渠道'
  const failed = results.filter((item) => !item.ok)
  if (!failed.length) {
    return { tone: 'success', text: '测试通知已发送：' + results.length + ' 个渠道全部成功' }
  }
  const detail =
    failed
      .slice(0, 2)
      .map((item) => nameOf(item.type) + '：' + (item.error || '未知原因'))
      .join('；') + (failed.length > 2 ? '…' : '')
  const body = failed.length + '/' + results.length + ' 个渠道（' + detail + '）'
  return failed.length === results.length
    ? { tone: 'error', text: '发送测试通知失败：' + body }
    : { tone: 'warning', text: '测试通知部分失败：' + body }
}
