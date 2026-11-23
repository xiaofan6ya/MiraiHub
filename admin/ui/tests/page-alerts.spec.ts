import { describe, expect, it } from 'vitest'
import {
  ALERT_COOLDOWN_DEFAULT,
  ALERT_COOLDOWN_MAX,
  ALERT_COOLDOWN_MIN,
  ALERT_CHANNEL_TYPES,
  FALLBACK_CHANNEL_LABELS,
  SMTP_PORT_DEFAULT,
  alertChannelMeta,
  alertFormFromPayload,
  buildAlertConfigBody,
  channelKey,
  clampCooldown,
  cloneAlertForm,
  describeCooldown,
  describeSmtpHealth,
  isConfigDirty,
  normalizePort,
  summarizeTestResult,
  toChannelViews,
  validateChannelTarget,
  type AlertConfigForm,
  type AlertConfigPayload,
  type AlertTestResult,
} from '@/api/alerts'

/**
 * 契约 docs/ui-v2/contracts/alerts.md §二/§三/§四 的数据转换。
 * 测试数据按后端真实响应形状造：
 *   GET  /api/alerts/config → alerts_router.get_config（config 落库值 + channels_summary + config_summary）
 *   POST /api/alerts/test   → alerts_router.send_test（deliver 的 results）
 */

/** 一条真实形状的 GET 响应：飞书渠道 + 由 mail_to 自动补出来的邮件渠道。 */
function samplePayload(): AlertConfigPayload {
  return {
    config: {
      enabled: true,
      cooldown: 600,
      channels: [{ type: 'feishu', target: 'https://open.feishu.cn/open-apis/bot/v2/hook/abc123' }],
      smtp_host: 'smtp.qq.com',
      smtp_port: 465,
      smtp_user: 'me@qq.com',
      smtp_password: '********',
      has_password: true,
      smtp_from: 'me@qq.com',
      smtp_starttls: true,
      smtp: '********',
      has_smtp: true,
      mail_to: 'me@qq.com',
    },
    channels: [
      {
        type: 'feishu',
        label: '飞书群机器人',
        target: 'https://open.feishu.cn/open-apis/bot/v2/hook/abc123',
        enabled: true,
      },
      { type: 'email', label: '邮件（SMTP）', target: 'me@qq.com', enabled: true },
    ],
    labels: { email: '邮件（SMTP）', feishu: '飞书群机器人', qqbot: 'QQ 机器人' },
    enabled: true,
    smtp: {
      host: 'smtp.qq.com',
      port: 465,
      user: 'me@qq.com',
      from: 'me@qq.com',
      mail_to: 'me@qq.com',
      ssl: true,
      compact: true,
      has_password: true,
      ready: true,
      missing: [],
    },
  }
}

function sampleForm(): AlertConfigForm {
  return alertFormFromPayload(samplePayload())
}

describe('toChannelViews：GET channels[] → 渠道列表', () => {
  it('保留类型图标/名称/目标，并把「默认收件邮箱」派生行标出来', () => {
    const rows = toChannelViews(samplePayload())
    expect(rows).toHaveLength(2)
    expect(rows[0]).toMatchObject({
      type: 'feishu',
      label: '飞书群机器人',
      target: 'https://open.feishu.cn/open-apis/bot/v2/hook/abc123',
      enabled: true,
      auto: false,
    })
    // config.channels 里没有 email → 这条是 active_channels() 用 mail_to 补的
    expect(rows[1]).toMatchObject({ type: 'email', target: 'me@qq.com', auto: true })
    expect(rows[1].key).toBe(channelKey('email', 'me@qq.com'))
  })

  it('labels 缺失时回落中文兜底名；未知类型用类型名', () => {
    const payload = samplePayload()
    payload.labels = {}
    payload.channels = [{ type: 'custom', label: '', target: 'https://example.com/hook', enabled: true }]
    payload.config.channels = [{ type: 'custom', target: 'https://example.com/hook' }]
    const rows = toChannelViews(payload)
    expect(rows[0].label).toBe('custom')
    expect(toChannelViews(null)).toEqual([])
  })

  it('显式 email 渠道存在时不再标 auto', () => {
    const payload = samplePayload()
    payload.config.channels = [{ type: 'email', target: 'me@qq.com' }]
    const rows = toChannelViews(payload)
    expect(rows.find((row) => row.type === 'email')?.auto).toBe(false)
  })

  it('channels 摘要为空时退回 config.channels（防止摘要与落库不一致时列表空白）', () => {
    const payload = samplePayload()
    payload.channels = []
    payload.config.channels = [{ type: 'qqbot', target: 'http://127.0.0.1:5700/send_group_msg?group=123' }]
    payload.config.mail_to = ''
    const rows = toChannelViews(payload)
    expect(rows).toHaveLength(1)
    expect(rows[0].type).toBe('qqbot')
    expect(rows[0].enabled).toBe(true)
  })
})

describe('alertFormFromPayload：GET → 表单初值（密码不回显）', () => {
  it('掩码字段不落到输入框，只保留 has_* 供 placeholder 使用', () => {
    const payload = samplePayload()
    const form = alertFormFromPayload(payload)
    expect(form.smtpPassword).toBe('')
    expect(form.smtpCompact).toBe('')
    expect(form.clearCompact).toBe(false)
    expect(payload.config.has_password).toBe(true)
    expect(payload.config.has_smtp).toBe(true)
    expect(form.smtpHost).toBe('smtp.qq.com')
    expect(form.smtpPort).toBe(465)
    expect(form.smtpStarttls).toBe(true)
    expect(form.mailTo).toBe('me@qq.com')
    expect(form.enabled).toBe(true)
    expect(form.cooldown).toBe(600)
  })

  it('从未保存过（config = {}）时给默认值', () => {
    const form = alertFormFromPayload({
      config: {},
      channels: [],
      labels: {},
      enabled: true,
      smtp: {
        host: '',
        port: 465,
        user: '',
        from: '',
        mail_to: '',
        ssl: true,
        compact: false,
        has_password: false,
        ready: false,
        missing: ['邮件服务器（或 ADMIN_ALERT_SMTP=qqSMTP:授权码）'],
      },
    })
    expect(form.enabled).toBe(true)
    expect(form.cooldown).toBe(ALERT_COOLDOWN_DEFAULT)
    expect(form.smtpPort).toBe(SMTP_PORT_DEFAULT)
    expect(form.smtpStarttls).toBe(true)
    expect(form.channels).toEqual([])
  })

  it('config.enabled=false 时关闭开关', () => {
    const payload = samplePayload()
    payload.config.enabled = false
    expect(alertFormFromPayload(payload).enabled).toBe(false)
  })
})

describe('buildAlertConfigBody：表单 → PUT 全量请求体', () => {
  it('补齐所有字段；密码留空原样发空串（后端 = 不修改）', () => {
    const body = buildAlertConfigBody(sampleForm())
    expect(body).toEqual({
      enabled: true,
      cooldown: 600,
      channels: [{ type: 'feishu', target: 'https://open.feishu.cn/open-apis/bot/v2/hook/abc123' }],
      smtp_host: 'smtp.qq.com',
      smtp_port: 465,
      smtp_user: 'me@qq.com',
      smtp_password: '',
      smtp_from: 'me@qq.com',
      smtp_starttls: true,
      smtp: '',
      mail_to: 'me@qq.com',
    })
  })

  it('停用渠道与「默认收件邮箱」派生渠道都不写回；空 target 跳过；重复去除', () => {
    const form = sampleForm()
    form.channels = [
      { key: channelKey('feishu', 'https://a'), type: 'feishu', label: '飞书', target: 'https://a', enabled: true, auto: false },
      { key: channelKey('feishu', 'https://a'), type: 'feishu', label: '飞书', target: 'https://a', enabled: true, auto: false },
      { key: channelKey('qqbot', 'https://b'), type: 'qqbot', label: 'QQ', target: 'https://b', enabled: false, auto: false },
      { key: channelKey('email', 'me@qq.com'), type: 'email', label: '邮件', target: 'me@qq.com', enabled: true, auto: true },
      { key: channelKey('email', ''), type: 'email', label: '邮件', target: '  ', enabled: true, auto: false },
    ]
    expect(buildAlertConfigBody(form).channels).toEqual([{ type: 'feishu', target: 'https://a' }])
  })

  it('掩码串永不回写：smtp_password / smtp 收到 ******** 时发空串', () => {
    const form = sampleForm()
    form.smtpPassword = '********'
    form.smtpCompact = '********'
    const body = buildAlertConfigBody(form)
    expect(body.smtp_password).toBe('')
    expect(body.smtp).toBe('')
  })

  it('新密码与一条式授权码原样提交；点过「清除」则发 __clear__', () => {
    const form = sampleForm()
    form.smtpPassword = 'new-app-password'
    form.smtpCompact = ' qqSMTP:abcdefghijklmnop '
    expect(buildAlertConfigBody(form).smtp_password).toBe('new-app-password')
    expect(buildAlertConfigBody(form).smtp).toBe('qqSMTP:abcdefghijklmnop')

    form.clearCompact = true
    expect(buildAlertConfigBody(form).smtp).toBe('__clear__')
  })

  it('cooldown / port 越界回落（与旧版 min=30 max=86400、端口 465 一致）', () => {
    const form = sampleForm()
    form.cooldown = 10
    form.smtpPort = 70000
    expect(buildAlertConfigBody(form).cooldown).toBe(ALERT_COOLDOWN_MIN)
    expect(buildAlertConfigBody(form).smtp_port).toBe(SMTP_PORT_DEFAULT)

    form.cooldown = 999999
    form.smtpPort = 587
    expect(buildAlertConfigBody(form).cooldown).toBe(ALERT_COOLDOWN_MAX)
    expect(buildAlertConfigBody(form).smtp_port).toBe(587)
  })

  it('文本字段去首尾空白', () => {
    const form = sampleForm()
    form.smtpHost = '  smtp.example.com '
    form.smtpUser = ' bot@example.com '
    form.smtpFrom = ' from@example.com '
    form.mailTo = ' me@example.com '
    const body = buildAlertConfigBody(form)
    expect(body.smtp_host).toBe('smtp.example.com')
    expect(body.smtp_user).toBe('bot@example.com')
    expect(body.smtp_from).toBe('from@example.com')
    expect(body.mail_to).toBe('me@example.com')
  })
})

describe('clampCooldown / normalizePort / describeCooldown', () => {
  it('cooldown 默认 600、范围 30–86400', () => {
    expect(clampCooldown(undefined)).toBe(600)
    expect(clampCooldown('')).toBe(600)
    expect(clampCooldown(Number.NaN)).toBe(600)
    expect(clampCooldown(0)).toBe(600)
    expect(clampCooldown(29.6)).toBe(30)
    expect(clampCooldown(86401)).toBe(86400)
  })

  it('端口默认 465、只接受 1–65535', () => {
    expect(normalizePort(undefined)).toBe(465)
    expect(normalizePort('')).toBe(465)
    expect(normalizePort(0)).toBe(465)
    expect(normalizePort(65536)).toBe(465)
    expect(normalizePort('587')).toBe(587)
  })

  it('去重窗口的人话', () => {
    expect(describeCooldown(600)).toBe('10 分钟')
    expect(describeCooldown(86400)).toBe('24 小时')
    expect(describeCooldown(90)).toBe('1 分 30 秒')
    expect(describeCooldown(45)).toBe('45 秒')
    expect(describeCooldown(10)).toBe('30 秒')
  })
})

describe('validateChannelTarget：抽屉里的目标校验（旧版 alertsAddChannel 的规则）', () => {
  it('邮件：单个与多个地址', () => {
    expect(validateChannelTarget('email', 'me@qq.com')).toBe('')
    expect(validateChannelTarget('email', 'a@x.com, b@y.com')).toBe('')
    expect(validateChannelTarget('email', 'me@qq')).toContain('收件邮箱格式不对')
    expect(validateChannelTarget('email', 'a@x.com,broken')).toContain('收件邮箱格式不对')
  })

  it('飞书：必须 http(s)://', () => {
    expect(validateChannelTarget('feishu', 'https://open.feishu.cn/open-apis/bot/v2/hook/x')).toBe('')
    expect(validateChannelTarget('feishu', 'ftp://x')).toBe('Webhook 地址需以 http(s):// 开头')
  })

  it('QQ 机器人：必须 http(s):// 且带 ?group=群号', () => {
    expect(validateChannelTarget('qqbot', 'http://127.0.0.1:5700/send_group_msg?group=123')).toBe('')
    expect(validateChannelTarget('qqbot', 'http://127.0.0.1:5700/send_group_msg')).toBe(
      'QQ 机器人地址需带 ?group=群号',
    )
  })

  it('空值与非 http(s) 校验', () => {
    expect(validateChannelTarget('feishu', '   ')).toBe('请填写地址')
    expect(validateChannelTarget('feishu', 'open.feishu.cn/hook')).toBe('Webhook 地址需以 http(s):// 开头')
  })
})

describe('alertChannelMeta：类型切换动态字段', () => {
  it('三种已知类型各有自己的 placeholder / 规则', () => {
    expect(alertChannelMeta('email').rule).toBe('email')
    expect(alertChannelMeta('email').placeholder).toContain('收件邮箱')
    expect(alertChannelMeta('feishu').rule).toBe('url')
    expect(alertChannelMeta('feishu').placeholder).toContain('open.feishu.cn')
    expect(alertChannelMeta('qqbot').rule).toBe('qqbot')
    expect(alertChannelMeta('qqbot').hint).toContain('?group=群号')
  })

  it('未知类型走兜底文案，标签优先用后端 labels', () => {
    const meta = alertChannelMeta('wecom', { wecom: '企业微信' })
    expect(meta.label).toBe('企业微信')
    expect(meta.kind).toBe('unknown')
    expect(meta.rule).toBe('text')
    expect(ALERT_CHANNEL_TYPES).toEqual(['feishu', 'qqbot', 'email'])
    expect(FALLBACK_CHANNEL_LABELS.email).toBe('邮件（SMTP）')
  })
})

describe('describeSmtpHealth：邮件链路体检', () => {
  it('就绪时给出发件人、收件人与服务器', () => {
    const view = describeSmtpHealth(samplePayload().smtp)
    expect(view.tone).toBe('ok')
    expect(view.text).toContain('me@qq.com')
    expect(view.text).toContain('smtp.qq.com:465 SSL')
  })

  it('未就绪时列出缺什么', () => {
    const view = describeSmtpHealth({
      host: '',
      port: 465,
      user: '',
      from: '',
      mail_to: '',
      ssl: true,
      compact: false,
      has_password: false,
      ready: false,
      missing: ['邮件服务器（或 ADMIN_ALERT_SMTP=qqSMTP:授权码）', '收件邮箱（默认收件人）'],
    })
    expect(view.tone).toBe('warn')
    expect(view.text).toBe('邮件链路还差：邮件服务器（或 ADMIN_ALERT_SMTP=qqSMTP:授权码）、收件邮箱（默认收件人）')
    expect(describeSmtpHealth(null).tone).toBe('warn')
  })
})

describe('summarizeTestResult：POST /api/alerts/test → Toast', () => {
  function result(partial: Partial<AlertTestResult>): AlertTestResult {
    return {
      ok: true,
      title: '[风控提醒] 测试 出现封控信号',
      text: '这是一条来自 MiraiHub 的测试通知。',
      level: 'info',
      results: [],
      ...partial,
    }
  }

  it('全部成功', () => {
    const summary = summarizeTestResult(
      result({
        results: [
          { type: 'feishu', target: 'https://x', ok: true, error: '' },
          { type: 'email', target: 'me@qq.com', ok: true, error: '' },
        ],
      }),
    )
    expect(summary).toEqual({ tone: 'success', text: '测试通知已发送：2 个渠道全部成功' })
  })

  it('部分失败：warning，并带渠道中文名与后端 error', () => {
    const summary = summarizeTestResult(
      result({
        ok: false,
        results: [
          { type: 'feishu', target: 'https://x', ok: true, error: '' },
          {
            type: 'email',
            target: 'me@qq.com',
            ok: false,
            error: "SMTPAuthenticationError: (535, b'Error: authentication failed')",
          },
        ],
      }),
    )
    expect(summary.tone).toBe('warning')
    expect(summary.text).toContain('1/2 个渠道')
    expect(summary.text).toContain('邮件（SMTP）')
    expect(summary.text).toContain('authentication failed')
  })

  it('全部失败 → error；没有渠道 → error', () => {
    const allFailed = summarizeTestResult(
      result({
        ok: false,
        results: [{ type: 'feishu', target: 'https://x', ok: false, error: 'HTTP 401 unauthorized' }],
      }),
    )
    expect(allFailed.tone).toBe('error')
    expect(allFailed.text).toContain('发送测试通知失败')

    const empty = summarizeTestResult(result({ ok: false, results: [] }))
    expect(empty.tone).toBe('error')
    expect(empty.text).toContain('没有配置任何渠道')
  })
})

describe('isConfigDirty / cloneAlertForm：未保存状态', () => {
  it('未改动时为 false，任何字段改动都为 true', () => {
    const form = sampleForm()
    const saved = cloneAlertForm(form)
    expect(isConfigDirty(form, saved)).toBe(false)

    form.cooldown = 1200
    expect(isConfigDirty(form, saved)).toBe(true)

    const form2 = sampleForm()
    const saved2 = cloneAlertForm(form2)
    form2.smtpPassword = 'typed-but-unsaved'
    expect(isConfigDirty(form2, saved2)).toBe(true)
  })

  it('渠道开关与清除标记也算未保存；掩码不算改动', () => {
    const form = sampleForm()
    const saved = cloneAlertForm(form)
    const feishu = form.channels.find((channel) => channel.type === 'feishu')
    if (feishu) feishu.enabled = false
    expect(isConfigDirty(form, saved)).toBe(true)

    const cleared = sampleForm()
    const savedCleared = cloneAlertForm(cleared)
    cleared.clearCompact = true
    expect(isConfigDirty(cleared, savedCleared)).toBe(true)

    // 掩码写进输入框不算改动（buildAlertConfigBody 会清成空串）
    const masked = sampleForm()
    const savedMasked = cloneAlertForm(masked)
    masked.smtpPassword = '********'
    expect(isConfigDirty(masked, savedMasked)).toBe(false)
  })
})
