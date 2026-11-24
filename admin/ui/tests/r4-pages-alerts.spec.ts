import { describe, expect, it } from 'vitest'
import {
  alertFormFromPayload,
  buildAlertConfigBody,
  isMaskedTarget,
  toChannelViews,
  type AlertConfigPayload,
} from '@/api/alerts'

/**
 * r4：后端 ALERT_MASK_TARGETS 默认 1，GET 回来的 target 就是打码串。
 * 两条不同的 webhook 打码后可能完全相同（同 host、末 4 位相同），
 * 前端不能再按 type::target 去重 / 当列表 key，否则保存时第二条会被静默删掉。
 */
const MASKED = 'http://127.0.0.1:5700/send_group_msg?group=***1234'

function collidingPayload(): AlertConfigPayload {
  return {
    config: {
      enabled: true,
      cooldown: 600,
      channels: [
        { type: 'qqbot', target: MASKED },
        { type: 'qqbot', target: MASKED },
        { type: 'feishu', target: 'https://open.feishu.cn/open-apis/bot/v2/hook/abc' },
      ],
      mail_to: '',
    },
    channels: [
      { type: 'qqbot', label: 'QQ', target: MASKED, target_masked: MASKED, masked: true, enabled: true },
      { type: 'qqbot', label: 'QQ', target: MASKED, target_masked: MASKED, masked: true, enabled: true },
      {
        type: 'feishu',
        label: 'Feishu',
        target: 'https://open.feishu.cn/open-apis/bot/v2/hook/abc',
        enabled: true,
      },
    ],
    labels: {},
  } as AlertConfigPayload
}

describe('r4：打码撞串的告警渠道', () => {
  it('isMaskedTarget 只认 ***', () => {
    expect(isMaskedTarget(MASKED)).toBe(true)
    expect(isMaskedTarget('https://example.com/hook')).toBe(false)
    expect(isMaskedTarget('')).toBe(false)
  })

  it('列表 key 唯一（v-for 不冲突、删除一条不连带另一条）', () => {
    const rows = toChannelViews(collidingPayload())
    expect(rows).toHaveLength(3)
    expect(new Set(rows.map((row) => row.key)).size).toBe(3)
  })

  it('保存时两条打码值相同的渠道都保留，顺序不变', () => {
    const body = buildAlertConfigBody(alertFormFromPayload(collidingPayload()))
    expect(body.channels).toEqual([
      { type: 'qqbot', target: MASKED },
      { type: 'qqbot', target: MASKED },
      { type: 'feishu', target: 'https://open.feishu.cn/open-apis/bot/v2/hook/abc' },
    ])
  })

  it('删除其中一条后只剩一条打码值', () => {
    const form = alertFormFromPayload(collidingPayload())
    const victim = form.channels[1]
    form.channels = form.channels.filter((item) => item.key !== victim.key)
    const body = buildAlertConfigBody(form)
    expect(body.channels?.filter((item) => item.target === MASKED)).toHaveLength(1)
  })

  it('明文 target 仍然去重', () => {
    const form = alertFormFromPayload(collidingPayload())
    form.channels = [
      ...form.channels,
      { ...form.channels[2], key: 'dup' },
    ]
    const body = buildAlertConfigBody(form)
    expect(body.channels?.filter((item) => item.type === 'feishu')).toHaveLength(1)
  })
})
