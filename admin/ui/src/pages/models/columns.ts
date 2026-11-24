/**
 * 模型配置表格的单元格渲染与列定义（从 ModelsPage.vue 拆出，10-visual-refresh §5）。
 * 只搬家、不改逻辑；行内点击 / 行操作回调由页面传入。
 * 注意：这些 h() 节点由 DataTable 渲染，本来就拿不到页面的 scoped id，样式在页面里用 :deep 写。
 */
import { computed, h, type ComputedRef, type Ref, type VNodeChild } from 'vue'
import { Brain, Image as ImageIcon, Sparkles, Video, Wrench } from 'lucide-vue-next'
import {
  Badge,
  Chip,
  Copyable,
  ModelIcon,
  PlatformLogo,
  RowActions,
  type TableColumn,
  type TableRow,
} from '@/components'
import { usePlatformStore } from '@/stores/platform'
import { FMT_EMPTY, fmtDuration, fmtInt, fmtPct } from '@/utils/format'
import { useI18n } from '@/i18n'
import {
  CAPABILITY_LABELS,
  MODEL_ALIAS_LIMIT,
  MODEL_PLATFORM_LIMIT,
  fmtMultiplier,
  fmtTokensShort,
  indexHealth,
  isBadgeDimmed,
  multiplierTone,
  platformBadges,
  type CapabilityKey,
  type ModelHealthItem,
  type ModelPlatformRef,
  type ModelRow,
} from '@/api/models'

export interface ModelColumnDeps {
  health: Ref<ModelHealthItem[] | null>
  sortOrderOf: (stateKey: string) => 'ascend' | 'descend' | false
  openEdit: (row: ModelRow) => Promise<void> | void
  onRowAction: (row: ModelRow, key: string) => Promise<void> | void
}

export function useModelColumns(deps: ModelColumnDeps): ComputedRef<TableColumn[]> {
  const { health, sortOrderOf, openEdit, onRowAction } = deps
  const { t } = useI18n()
  const platformStore = usePlatformStore()


  const CAP_ICONS: Record<CapabilityKey, typeof Brain> = {
    image: ImageIcon,
    tools: Wrench,
    reasoning: Brain,
    video: Video,
    'image-out': Sparkles,
  }

  const CAP_TONES: Record<CapabilityKey, string> = {
    image: 'models-cap--image',
    tools: 'models-cap--tools',
    reasoning: 'models-cap--reasoning',
    video: 'models-cap--video',
    'image-out': 'models-cap--image',
  }

  function platformTooltip(row: ModelRow): string {
    return row.platforms
      .map(
        (ref: ModelPlatformRef) =>
          ref.platformLabel +
          ' · ' +
          ref.clientId +
          ' · ' +
          fmtMultiplier(ref.multiplier) +
          (ref.enabled ? '' : t('page.models.cell.disabledSuffix')),
      )
      .join('\n')
  }

  function renderModelCell(row: ModelRow): VNodeChild {
    const caps = row.capabilityKeys.map((key) =>
      h(
        'span',
        {
          key: 'cap-' + key,
          class: ['models-cap', CAP_TONES[key]],
          title: CAPABILITY_LABELS[key],
          'aria-label': CAPABILITY_LABELS[key],
        },
        [h(CAP_ICONS[key], { size: 12 })],
      ),
    )
    const aliasChips = row.aliases.slice(0, MODEL_ALIAS_LIMIT).map((alias) =>
      h(
        Chip,
        { key: 'alias-' + alias, mono: true, copy: true, value: alias, title: t('page.models.cell.aliasCopy') },
        {
        default: () => alias,
      }),
    )
    if (row.aliasOverflow > 0) {
      const rest = row.aliases.slice(MODEL_ALIAS_LIMIT)
      aliasChips.push(
        h(
          Chip,
          {
            key: 'alias-more',
            title: t('page.models.cell.aliasMore', { count: row.aliasOverflow, list: rest.join('、') }),
          },
          { default: () => '+' + row.aliasOverflow },
        ),
      )
    }

    return h(
      'div',
      {
        class: 'models-name',
        role: 'button',
        tabindex: 0,
        title: t('page.models.cell.editTitle', { name: row.name }),
        onClick: () => void openEdit(row),
        onKeydown: (event: KeyboardEvent) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault()
            void openEdit(row)
          }
        },
      },
      [
        h(ModelIcon, { vendor: row.vendor, size: 20, label: row.vendorLabel }),
        h('div', { class: 'models-name__main' }, [
          h('div', { class: 'models-name__title' }, [
            h('span', { class: 'models-name__text' }, row.name),
            row.free
              ? h(Badge, { tone: 'ok' }, { default: () => t('page.models.cell.free') })
              : null,
            row.officialVisible
              ? null
              : h(Badge, { tone: 'neutral' }, { default: () => t('page.models.cell.legacy') }),
            row.partiallyEnabled
              ? h(Badge, { tone: 'warn' }, {
                  default: () => t('page.models.cell.platformsDisabled', { count: row.disabledCount }),
                })
              : null,
          ]),
          // 一行高度控制在 ~64px：能力图标并到标题行尾，别名 chip 和调用名同一行（第 1 轮审查 P2）。
          h('div', { class: 'models-name__ids' }, [
            h(Copyable, { value: row.callId, muted: true }),
            ...aliasChips,
          ]),
        ]),
        caps.length
          ? h('div', { class: 'models-name__caps', 'aria-label': t('page.models.cell.capsAria') }, caps)
          : null,
      ],
    )
  }

  /** 模型健康按上游 id 建索引：平台列用它判断「哪个平台在跑流量」。 */
  const healthIndex = computed(() => indexHealth(health.value))

  /** 区级平台 id 的可用账号数（平台列表的 active_count，旧后端退回 account_count；没有这一项为 null）。 */
  function accountsOfPlatform(platformId: string): number | null {
    const spec = platformStore.items.find((item) => item.id === platformId)
    if (!spec) return null
    const active = spec.active_count
    if (typeof active === 'number' && Number.isFinite(active)) return active
    const total = spec.account_count
    return typeof total === 'number' && Number.isFinite(total) ? total : null
  }

  function badgeTitle(label: string, realms: string[], requests: number, accounts: number | null): string {
    const parts = [label]
    if (realms.length > 1) parts.push(realms.join(' / '))
    if (requests > 0) parts.push(t('page.models.cell.platformRequests', { count: fmtInt(requests) }))
    if (accounts !== null) parts.push(t('page.models.cell.platformAccounts', { count: fmtInt(accounts) }))
    return parts.join(' · ')
  }

  /**
   * 平台列（第 3 轮审查 A3 重做）：
   *   · 同一平台多区 / 多 model_id 合成一个 logo（trae-cn + trae-intl → Trae，右下角写区数）；
   *   · 在跑流量的 > 有可用账号 > 已启用但 0 账号 > 全部禁用；禁用或 0 账号的 logo 降到 40%；
   *   · logo 放进 24px 方形浅底小块、彼此间隔 4px（原来 -8px 叠放，互相压住，单色图标认不出）；
   *   · 最多 5 个 + 「+N」，尾部灰字「n/m 平台」。
   */
  function renderPlatforms(row: ModelRow): VNodeChild {
    const badges = platformBadges(row.platforms, {
      requestsOf: (ref) => healthIndex.value.get(ref.upstreamId.toLowerCase())?.requests ?? 0,
      accountsOf: accountsOfPlatform,
    })
    const shown = badges.slice(0, MODEL_PLATFORM_LIMIT)
    const nodes: VNodeChild[] = shown.map((badge) =>
      h(
        'span',
        {
          key: 'logo-' + badge.platform,
          class: ['models-platform', { 'is-off': isBadgeDimmed(badge), 'is-live': badge.requests > 0 }],
          title: badgeTitle(badge.label, badge.realms, badge.requests, badge.accounts),
        },
        [
          h(PlatformLogo, { id: badge.icon, size: 16, label: badge.label }),
          badge.realms.length > 1
            ? h('span', { class: 'models-platform__count tnum', 'aria-hidden': 'true' }, String(badge.realms.length))
            : null,
        ],
      ),
    )
    if (badges.length > shown.length) {
      const rest = badges.slice(MODEL_PLATFORM_LIMIT).map((badge) => badge.label)
      nodes.push(
        h(
          'span',
          { key: 'logo-more', class: 'models-platform is-more tnum', title: rest.join(' · ') },
          ['+' + (badges.length - shown.length)],
        ),
      )
    }
    return h('div', { class: 'models-platforms', title: platformTooltip(row) }, [
      ...nodes,
      h(
        'span',
        { key: 'logo-sum', class: 'models-platforms__sum tnum' },
        t('page.models.status.platforms', { enabled: row.enabledCount, total: row.platformCount }),
      ),
    ])
  }

  function renderMultiplier(row: ModelRow): VNodeChild {
    const tone = multiplierTone(row.multiplier)
    const hint =
      row.multiplier === null
        ? t('page.models.cell.multiplierPending')
        : row.multiplier < 0
          ? t('page.models.cell.multiplierTokenBilled')
          : row.multiplier === 0
            ? t('page.models.cell.multiplierFree')
            : t('page.models.cell.multiplierRelative')
    return h('span', { class: ['models-mult', 'is-' + tone], title: hint }, row.multiplierText)
  }

  function renderLimits(row: ModelRow): VNodeChild {
    const lines: VNodeChild[] = [
      h('span', { class: 'models-limits__line' }, [
        h('span', { class: 'models-limits__label' }, t('page.models.cell.context')),
        h('span', { class: 'tnum' }, fmtTokensShort(row.contextWindow)),
      ]),
      h('span', { class: 'models-limits__line' }, [
        h('span', { class: 'models-limits__label' }, t('page.models.cell.output')),
        h('span', { class: 'tnum' }, fmtTokensShort(row.maxOutputTokens)),
      ]),
    ]
    if (row.availableAccounts !== null) {
      lines.push(
        h(
          'span',
          {
            class: 'models-limits__line',
            title: t('page.models.cell.accountsTitle'),
          },
          [
            h('span', { class: 'models-limits__label' }, t('page.models.cell.accounts')),
            h('span', { class: 'tnum' }, row.availableAccounts + '/' + (row.eligibleAccounts ?? 0)),
          ],
        ),
      )
    }
    return h('div', { class: 'models-limits' }, lines)
  }

  function renderHealth(row: ModelRow): VNodeChild {
    if (!row.health) return h('span', { class: 'models-dash' }, FMT_EMPTY)
    // 没有任何样本：只显示一个「—」（原来是「TTFT — 无样本 0 次」三段拼在一起，第 1 轮审查 P0-7/8）。
    if (row.requests <= 0 && row.successRate === null && (row.ttftP50 === null || row.ttftP50 <= 0)) {
      const noAccounts = row.availableAccounts === 0 && (row.eligibleAccounts || 0) > 0
      return h('div', { class: 'models-health' }, [
        h('span', { class: 'models-dash', title: t('page.models.cell.noSamples') }, FMT_EMPTY),
        noAccounts ? h(Badge, { tone: 'danger' }, { default: () => t('page.models.cell.noAccounts') }) : null,
      ])
    }
    const successRate = row.successRate
    const tone = successRate === null ? 'neutral' : successRate >= 0.95 ? 'ok' : successRate >= 0.8 ? 'warn' : 'danger'
    return h('div', { class: 'models-health' }, [
      h('span', { class: 'models-health__line' }, [
        h('span', { class: 'models-health__label' }, 'TTFT'),
        h('span', { class: 'tnum' }, fmtDuration(row.ttftP50)),
      ]),
      h('span', { class: 'models-health__line' }, [
        h(Badge, { tone }, {
          default: () => (successRate === null ? t('page.models.cell.noSamples') : fmtPct(successRate)),
        }),
        h(
          'span',
          { class: 'models-health__sub tnum' },
          t('page.models.cell.requests', { count: fmtInt(row.requests) }),
        ),
      ]),
      row.availableAccounts === 0 && (row.eligibleAccounts || 0) > 0
        ? h(Badge, { tone: 'danger' }, { default: () => t('page.models.cell.noAccounts') })
        : null,
    ])
  }

  function renderStatus(row: ModelRow): VNodeChild {
    const tone = row.enabled ? 'ok' : row.partiallyEnabled ? 'warn' : 'neutral'
    const text = row.enabled
      ? t('page.models.status.enabled')
      : row.partiallyEnabled
        ? t('page.models.status.partial')
        : t('page.models.status.disabled')
    // 「n/m 平台」移到平台列尾部（第 3 轮审查 A3：状态列 116px 放不下，会折成两行）。
    return h('div', { class: 'models-status' }, [h(Badge, { tone, dot: true }, { default: () => text })])
  }

  function renderActions(row: ModelRow): VNodeChild {
    return h(
      'div',
      { class: 'models-actions', onClick: (event: Event) => event.stopPropagation() },
      [
        h(RowActions, {
          actions: [
            { key: 'edit', label: t('common.edit') },
            {
              key: 'toggle',
              label: row.enabled ? t('page.models.action.disableAll') : t('page.models.action.enableAll'),
            },
            { key: 'speed', label: t('page.models.action.speed'), disabled: !row.speedTestable },
            { key: 'delete', label: t('common.delete'), danger: true },
          ],
          max: 1,
          onSelect: (key: string) => {
            void onRowAction(row, key)
          },
        }),
      ],
    )
  }

  const columns = computed<TableColumn[]>(() => [
    {
      key: 'name',
      title: t('page.models.col.name'),
      mobile: 'title',
      minWidth: 320,
      sortable: true,
      sortOrder: sortOrderOf('name'),
      render: (row: TableRow) => renderModelCell(row as ModelRow),
    },
    {
      key: 'platforms',
      title: t('page.models.col.platforms'),
      mobile: 'meta',
      width: 212,
      sortable: true,
      sortOrder: sortOrderOf('platforms'),
      render: (row: TableRow) => renderPlatforms(row as ModelRow),
    },
    {
      key: 'multiplier',
      title: t('page.models.col.multiplier'),
      mobile: 'meta',
      width: 100,
      numeric: true,
      sortable: true,
      sortOrder: sortOrderOf('multiplier'),
      render: (row: TableRow) => renderMultiplier(row as ModelRow),
    },
    {
      key: 'limits',
      title: t('page.models.col.limits'),
      mobile: 'meta',
      width: 156,
      render: (row: TableRow) => renderLimits(row as ModelRow),
    },
    {
      key: 'health',
      title: t('page.models.col.health'),
      mobile: 'title',
      width: 186,
      render: (row: TableRow) => renderHealth(row as ModelRow),
    },
    {
      key: 'status',
      title: t('page.models.col.status'),
      mobile: 'meta',
      width: 116,
      render: (row: TableRow) => renderStatus(row as ModelRow),
    },
    {
      key: 'actions',
      title: t('page.models.col.actions'),
      mobile: 'actions',
      width: 132,
      hideable: false,
      fixed: 'right',
      render: (row: TableRow) => renderActions(row as ModelRow),
    },
  ])

  return columns
}
