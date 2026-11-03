import { App as AntdApp, Avatar, Button, Card, Col, Flex, Listy, Modal, Row, Select, Space, Spin, Table, Tabs, Tag, Tooltip, Typography } from 'antd'
import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import PageHeader from '../components/PageHeader'
import StatCards from '../components/StatCard'
import { FMT_EMPTY, fmtCredit, fmtInt, fmtTime, relTime } from '../utils/format'

interface AccountBrief {
  id: number
  name: string
  status: string
  streak_days: number | null
  energy: number | null
}

interface Buddy {
  name?: string
  rarity?: string
  personality?: string
  thumbnail_url?: string
  [k: string]: unknown
}

interface RunRecord {
  id: number
  source?: string
  action?: string
  ok?: boolean
  credit?: number
  energy?: number
  created_at?: string
  message?: string
  steps?: { step?: string; ok?: boolean; message?: string; reward?: number }[]
}

interface Snapshot {
  sections: Record<
    string,
    {
      data?: Record<string, unknown>
      updated_at?: string
    }
  >
  errors?: Record<string, string>
  updated_at?: string
  checked_at?: string
  refresh_running?: boolean
}

interface SnapshotResp {
  account: { id: number; name: string; status: string }
  snapshot: Snapshot
  refresh: { running?: boolean; error?: string }
  latest_run: RunRecord | null
  schedule: { id: number; enabled: boolean; interval_minutes: number; cycle_label: string; next_run_at: string | null } | null
  today: string
}

interface HeatCell {
  date: string
  score: number
  has_new_buddy?: boolean
}

export default function GrowthPage({ popup = false }: { popup?: boolean } = {}) {
  const { message, modal } = AntdApp.useApp()
  const [accounts, setAccounts] = useState<AccountBrief[]>([])
  const [aid, setAid] = useState<number | null>(null)
  const [snap, setSnap] = useState<SnapshotResp | null>(null)
  const [records, setRecords] = useState<RunRecord[]>([])
  const [hasMore, setHasMore] = useState(false)
  const [loading, setLoading] = useState(false)
  const [acting, setActing] = useState(false)
  const [officialOpen, setOfficialOpen] = useState(false)

  const loadAccounts = useCallback(async () => {
    try {
      const r = await api<{ items: AccountBrief[] }>('/api/growth-center/accounts')
      setAccounts(r.items ?? [])
      setAid((prev) => prev ?? r.items?.[0]?.id ?? null)
    } catch (e) {
      message.error((e as Error).message || '账号列表加载失败')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const loadDetail = useCallback(
    async (id: number, quiet = false) => {
      if (!quiet) setLoading(true)
      try {
        const [r, rec] = await Promise.all([
          api<SnapshotResp>(`/api/growth-center/accounts/${id}`),
          api<{ items: RunRecord[]; has_more: boolean; next_before: number | null }>(`/api/growth-center/accounts/${id}/records`),
        ])
        setSnap(r)
        setRecords(rec.items ?? [])
        setHasMore(!!rec.has_more)
      } catch (e) {
        message.error((e as Error).message || '成长快照加载失败')
      } finally {
        setLoading(false)
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  )

  useEffect(() => {
    void loadAccounts()
  }, [loadAccounts])

  useEffect(() => {
    if (aid) void loadDetail(aid)
  }, [aid, loadDetail])

  // 刷新运行时 1.6s 轮询，空闲 15s 快照轮询
  useEffect(() => {
    if (!aid) return
    const timer = window.setInterval(
      () => {
        if (document.hidden) return
        void loadDetail(aid, true)
      },
      snap?.refresh?.running || snap?.snapshot?.refresh_running ? 1600 : 15000,
    )
    return () => window.clearInterval(timer)
  }, [aid, snap?.refresh?.running, snap?.snapshot?.refresh_running, loadDetail])

  const doRefresh = async () => {
    if (!aid) return
    try {
      await api(`/api/growth-center/accounts/${aid}/refresh`, { method: 'POST', body: {} })
      message.info('已开始刷新快照…')
      void loadDetail(aid, true)
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const doAction = (action: 'open' | 'gift' | 'draw' | 'checkin' | 'redeem', title: string, tier?: string | number) => {
    if (!aid) return
    modal.confirm({
      title,
      content: '操作会调用上游官方接口，可能消耗积分或抽奖机会。',
      okText: '确认执行',
      onOk: async () => {
        setActing(true)
        try {
          const r = await api<{ ok?: boolean; credit?: number; reason?: string }>(`/api/growth-center/accounts/${aid}/action`, {
            method: 'POST',
            body: tier !== undefined ? { action, tier } : { action },
          })
          if (r?.ok === false) message.warning(r.reason || '执行未生效')
          else message.success(`执行成功${r?.credit ? `：+${fmtCredit(r.credit)} 积分` : ''}`)
          void loadDetail(aid, true)
        } catch (e) {
          message.error((e as Error).message)
        } finally {
          setActing(false)
        }
      },
    })
  }

  const section = <T,>(key: string): T | undefined => snap?.snapshot?.sections?.[key]?.data as T | undefined

  const streak = section<{ streak?: { days?: number; month_total_days?: number; next_tier?: number; next_tier_remaining?: number } }>('streak')
  const redemption = section<{
    tiers?: { tier: string; days: number; credit: number; energy: number; cards: number; chances: number }[]
    remaining_days?: number
    [k: string]: unknown
  }>('streak')
  const energy = section<{ balance?: number; total_earned?: number }>('energy')
  const quota = section<{ balance?: number; affordable?: number; cost_per_open?: number }>('quota')
  const profile = section<{ level?: number; level_name?: string; completed?: number; total?: number }>('profile')
  const checkin = section<{ today_checked_in?: boolean; daily_credit?: number; streak_days?: number }>('checkin')
  const buddy = section<{ buddy?: Buddy | null }>('buddy')
  const buddies = section<{ buddies?: Buddy[]; count?: number }>('buddies')
  const travel = section<{ state?: string; location?: { name?: string; duration_hours?: number }; reward_credit?: number; daily_limit_reached?: boolean }>('travel')
  const lottery = section<{ chances?: number }>('lottery')
  const heatmap = section<{ cells?: HeatCell[]; today?: { score?: number; is_active?: boolean; status_text?: string } }>('heatmap')

  const heatCells = heatmap?.cells ?? []
  const maxScore = Math.max(1, ...heatCells.map((c) => c.score))
  // 横板：约 7 行，列数随天数自适应（宽度铺开而不是竖成长条）
  const cols = Math.max(14, Math.ceil(heatCells.length / 7))

  const loadMoreRecords = async () => {
    if (!aid || !records.length) return
    try {
      const before = records[records.length - 1]?.id
      const rec = await api<{ items: RunRecord[]; has_more: boolean }>(`/api/growth-center/accounts/${aid}/records?before=${before}`)
      setRecords((prev) => [...prev, ...(rec.items ?? [])])
      setHasMore(!!rec.has_more)
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      <PageHeader
        title="成长计划"
        description={popup ? '独立小窗 · 自动跟随平台口径' : snap?.snapshot?.checked_at ? `快照更新于 ${relTime(snap.snapshot.checked_at)}` : undefined}
        actions={
          <Space>
            <Select
              showSearch={{ optionFilterProp: 'label' }}
              style={{ minWidth: 200 }}
              placeholder="选择账号"
              value={aid ?? undefined}
              onChange={(v) => setAid(v)}
              options={accounts.map((a) => ({
                value: a.id,
                label: `${a.name}${a.streak_days !== null ? ` · 连登 ${a.streak_days} 天` : ''}`,
              }))}
            />
            <Button onClick={() => void doRefresh()} loading={!!snap?.refresh?.running}>
              刷新快照
            </Button>
            <Button onClick={() => setOfficialOpen(true)}>官方领取记录</Button>
          </Space>
        }
      />

      {!aid && <Card size="small"><Typography.Text type="secondary">该平台暂无成长计划账号</Typography.Text></Card>}
      <OfficialRecordsModal aid={aid} open={officialOpen} onClose={() => setOfficialOpen(false)} />

      {aid && loading && !snap && (
        <Card size="small" style={{ height: '100%' }}>
          <div style={{ textAlign: 'center', padding: 40 }}>
            <Spin size="large" />
          </div>
        </Card>
      )}

      {snap && (
        <>
          {/* Hero */}
          <Card size="small" style={{ height: '100%' }}>
            <Row align="middle" gutter={16}>
              <Col flex="none">
                <Avatar size={44} style={{ backgroundColor: '#2E9BFF' }}>
                  {profile?.level ? `Lv${profile.level}` : snap.account.name.slice(0, 1)}
                </Avatar>
              </Col>
              <Col flex="auto">
                <Typography.Text strong style={{ fontSize: 16 }}>
                  {snap.account.name}
                  {profile?.level_name ? ` · ${profile.level_name}` : ''}
                </Typography.Text>
                <Typography.Text type="secondary" style={{ display: 'block', fontSize: 12 }}>
                  {profile?.completed !== undefined
                    ? `任务进度 ${fmtInt(profile?.completed)}/${fmtInt(profile?.total)}`
                    : '等级信息需在官方页完成后同步'}
                  {snap.schedule ? ` · 自动巡检：${snap.schedule.cycle_label}（${snap.schedule.enabled ? '启用' : '停用'}）` : ''}
                </Typography.Text>
              </Col>
            </Row>
          </Card>

          <StatCards
            items={[
              { title: '连续登录', value: fmtInt(streak?.streak?.days), suffix: '天', hint: `本月 ${fmtInt(streak?.streak?.month_total_days)} 天 · 下一档还需 ${fmtInt(streak?.streak?.next_tier_remaining)} 天` },
              { title: '能量值', value: fmtCredit(energy?.balance), hint: `累计获得 ${fmtCredit(energy?.total_earned)}` },
              { title: '今日活跃', value: heatmap?.today?.is_active ? '已活跃' : '未活跃', suffix: heatmap?.today?.score !== undefined ? `${fmtInt(heatmap?.today?.score)} 分` : undefined, tone: heatmap?.today?.is_active ? 'success' : 'warning' },
              { title: '抽奖机会', value: fmtInt(lottery?.chances), suffix: '次' },
            ]}
          />

          <Row gutter={[12, 12]} align="stretch">
            {/* 盲盒 */}
            <Col xs={24} md={12} xl={6}>
              <Card size="small" style={{ height: '100%' }} title="惊喜盲盒">
                <Typography.Text>余额 {fmtCredit(quota?.balance)} · 单开 {fmtCredit(quota?.cost_per_open)}</Typography.Text>
                <Typography.Text type="secondary" style={{ display: 'block', fontSize: 12 }}>
                  可开 {fmtInt(quota?.affordable)} 次
                </Typography.Text>
                <Button type="primary" block style={{ marginTop: 8 }} disabled={!quota?.affordable} loading={acting} onClick={() => doAction('open', '开启盲盒？')}>
                  开盲盒
                </Button>
              </Card>
            </Col>
            {/* Buddy */}
            <Col xs={24} md={12} xl={6}>
              <Card size="small" style={{ height: '100%' }} title="Buddy 宠物">
                {buddy?.buddy ? (
                  <>
                    <Flex align="center" gap={14}>
                      <img
                        src={buddy.buddy.thumbnail_url || undefined}
                        alt={buddy.buddy.name}
                        style={{ width: 110, height: 110, objectFit: 'contain', borderRadius: 10, background: 'rgba(128,128,128,0.06)' }}
                      />
                      <div style={{ minWidth: 0, flex: 1 }}>
                        <Typography.Text strong style={{ fontSize: 15 }}>{buddy.buddy.name}</Typography.Text>
                        <div style={{ marginTop: 4 }}>
                          {buddy.buddy.rarity && <Tag color="geekblue">{buddy.buddy.rarity}</Tag>}
                        </div>
                        {buddy.buddy.personality && (
                          <Tooltip title={buddy.buddy.personality}>
                            <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 4 }} ellipsis>
                              {buddy.buddy.personality}
                            </Typography.Text>
                          </Tooltip>
                        )}
                      </div>
                    </Flex>
                    {buddies?.count ? (
                    <Button
                      block
                      size="small"
                      style={{ marginTop: 8 }}
                      onClick={() =>
                        modal.info({
                          title: `宠物图鉴（${fmtInt(buddies?.count)}）`,
                          width: 560,
                          content: (
                            <Listy
                              items={buddies?.buddies ?? []}
                              rowKey={(b) => String(b.instance_id ?? b.name ?? '')}
                              itemRender={(b: Buddy) => (
                                  <Space>
                                    <Avatar size={28} src={b.thumbnail_url || undefined} />
                                    <Typography.Text>{b.name}</Typography.Text>
                                    <Tag>{b.rarity || ''}</Tag>
                                  </Space>
                              )}
                            />
                          ),
                        })
                      }
                    >
                      查看图鉴（{fmtInt(buddies?.count)}）
                    </Button>
                    ) : null}
                  </>
                ) : (
                  <Typography.Text type="secondary">暂无 Buddy，去开盲盒吧</Typography.Text>
                )}
              </Card>
            </Col>
            {/* 旅行 */}
            <Col xs={24} md={12} xl={6}>
              <Card size="small" style={{ height: '100%' }} title="猫猫旅行">
                <Typography.Text strong>{travel?.location?.name || (travel?.state === 'traveling' ? '旅行中' : FMT_EMPTY)}</Typography.Text>
                <Typography.Text type="secondary" style={{ display: 'block', fontSize: 12 }}>
                  {travel?.location?.duration_hours ? `预计 ${travel.location.duration_hours} 小时` : ''}
                  {travel?.reward_credit ? ` · 奖励 ${fmtCredit(travel.reward_credit)}` : ''}
                  {travel?.daily_limit_reached ? ' · 今日次数已用完' : ''}
                </Typography.Text>
                <Button block size="small" style={{ marginTop: 8 }} loading={acting} onClick={() => doAction('gift', '领取旅行礼物？')}>
                  领取礼物
                </Button>
              </Card>
            </Col>
            {/* 打卡 */}
            <Col xs={24} md={12} xl={6}>
              <Card size="small" style={{ height: '100%' }} title="每日打卡">
                <Typography.Text strong>{checkin?.today_checked_in ? '今日已打卡' : '今日未打卡'}</Typography.Text>
                <Typography.Text type="secondary" style={{ display: 'block', fontSize: 12 }}>
                  奖励 {fmtCredit(checkin?.daily_credit)} · 连续 {fmtInt(checkin?.streak_days)} 天
                </Typography.Text>
                <Button
                  block
                  size="small"
                  type={checkin?.today_checked_in ? 'default' : 'primary'}
                  style={{ marginTop: 8 }}
                  disabled={!!checkin?.today_checked_in}
                  loading={acting}
                  onClick={() => doAction('checkin', '执行每日打卡？')}
                >
                  {checkin?.today_checked_in ? '已打卡' : '打卡'}
                </Button>
              </Card>
            </Col>
          </Row>

          {/* 连登奖励 */}
          {(redemption?.tiers ?? []).length > 0 && (
            <Card size="small" style={{ height: '100%' }} title={`连登奖励（本月 ${fmtInt(streak?.streak?.month_total_days)} 天，剩 ${fmtInt(redemption?.remaining_days)} 天）`}>
              <Table
                size="small"
                rowKey="tier"
                dataSource={redemption?.tiers ?? []}
                pagination={false}
                columns={[
                  {
                    title: '档位',
                    dataIndex: 'tier',
                    width: 80,
                    render: (v: string, r: { days: number; [k: string]: unknown }) => {
                      const status = String((redemption as Record<string, unknown> | undefined)?.[`tier_${v}_status`] ?? '')
                      const tone = status === 'claimed' || status === 'redeemed' ? 'success' : status === 'available' ? 'processing' : 'default'
                      const label = status === 'claimed' || status === 'redeemed' ? '已领取' : status === 'available' ? '可领取' : status === 'locked' ? '未达成' : status || FMT_EMPTY
                      return (
                        <Space size={4} wrap={false}>
                          <Tag color={tone}>{r.days} 天</Tag>
                          <Typography.Text type="secondary" style={{ fontSize: 11 }}>{label}</Typography.Text>
                        </Space>
                      )
                    },
                  },
                  { title: '需连登', dataIndex: 'days', width: 90, align: 'right', render: (v) => <span className="num">{fmtInt(v)} 天</span> },
                  { title: '积分', dataIndex: 'credit', width: 90, align: 'right', render: (v) => <span className="num">{fmtCredit(v)}</span> },
                  { title: '能量', dataIndex: 'energy', width: 90, align: 'right', render: (v) => <span className="num">{fmtCredit(v)}</span> },
                  { title: '卡片', dataIndex: 'cards', width: 80, align: 'right', render: (v) => <span className="num">{fmtInt(v)}</span> },
                  { title: '抽奖', dataIndex: 'chances', width: 80, align: 'right', render: (v) => <span className="num">{fmtInt(v)}</span> },
                  {
                    title: '操作',
                    width: 110,
                    render: (_, r) => {
                      const tier = String(r.tier)
                      const status = String((redemption as Record<string, unknown> | undefined)?.[`tier_${tier}_status`] ?? '')
                      if (status === 'claimed' || status === 'redeemed') return <Tag color="success">已领取</Tag>
                      if (status === 'available')
                        return (
                          <Button size="small" type="primary" loading={acting} onClick={() => doAction('redeem', `兑换 ${r.days} 天档奖励？`, tier)}>
                            领取
                          </Button>
                        )
                      const left = Math.max(0, Number(r.days) - Number(streak?.streak?.days ?? 0))
                      return <Tag>还差 {fmtInt(Number(left))} 天</Tag>
                    },
                  },
                ]}
              />
            </Card>
          )}

          {/* 月活跃热力图 */}
          {heatCells.length > 0 && (
            <Card size="small" style={{ height: '100%' }} title="月活跃地图">
              <div style={{ display: 'grid', gridTemplateColumns: `repeat(${cols}, 18px)`, gap: 4 }}>
                {heatCells.map((c) => {
                  const intensity = c.score / maxScore
                  return (
                    <Tooltip
                      key={c.date}
                      title={
                        <span>
                          {c.date}
                          <br />
                          活跃分 {c.score}
                          {c.has_new_buddy ? (
                            <>
                              <br />
                              获得新宠物
                            </>
                          ) : null}
                        </span>
                      }
                    >
                    <div
                      style={{
                        width: 18,
                        height: 18,
                        borderRadius: 3,
                        backgroundColor: c.score > 0 ? `rgba(65,118,230,${0.18 + intensity * 0.8})` : 'rgba(128,128,128,0.12)',
                        border: c.date === snap.today ? '1.5px solid #2E9BFF' : undefined,
                        cursor: 'default',
                      }}
                    />
                    </Tooltip>
                  )
                })}
              </div>
            </Card>
          )}

          {/* 执行记录 */}
          <Card size="small" style={{ height: '100%' }} title="执行与领取记录">
            <Table
              size="small"
              rowKey="id"
              dataSource={records}
              pagination={false}
              expandable={{
                expandedRowRender: (r: RunRecord) => (
                  <Space orientation="vertical" size={2}>
                    {(r.steps ?? []).map((s, i) => (
                      <Typography.Text key={i} type={s.ok === false ? 'danger' : undefined} style={{ fontSize: 12 }}>
                        {s.ok === false ? '✗' : '✓'} {s.message || s.step}
                        {s.reward ? `（+${fmtCredit(s.reward)}）` : ''}
                      </Typography.Text>
                    ))}
                    {!r.steps?.length && r.message && <Typography.Text style={{ fontSize: 12 }}>{r.message}</Typography.Text>}
                  </Space>
                ),
              }}
              columns={[
                { title: '时间', dataIndex: 'created_at', width: 150, render: (v) => <span className="num">{relTime(v)}</span> },
                { title: '动作', dataIndex: 'action', width: 120, render: (v) => v || FMT_EMPTY },
                { title: '来源', dataIndex: 'source', width: 110 },
                { title: '结果', dataIndex: 'ok', width: 80, render: (v) => (v ? <Tag color="success">成功</Tag> : <Tag color="error">失败</Tag>) },
                { title: '积分', dataIndex: 'credit', width: 90, align: 'right', render: (v) => <span className="num">{v ? `+${fmtCredit(v)}` : FMT_EMPTY}</span> },
                { title: '说明', dataIndex: 'message', ellipsis: true },
              ]}
            />
            {hasMore && (
              <Button block type="text" onClick={() => void loadMoreRecords()}>
                加载更早记录
              </Button>
            )}
          </Card>
        </>
      )}
    </Space>
  )
}


/** 官方领取记录列名中文映射（后端字段原文 → 展示名） */
const RECORD_COL_LABELS: Record<string, string> = {
  record_id: '记录 ID',
  draw_id: '抽奖 ID',
  reward_id: '奖励 ID',
  prize_name: '奖品名称',
  prize_type: '奖品类型',
  credit_amount: '积分数量',
  reward_credit: '奖励积分',
  credit_granted: '获得积分',
  energy_granted: '获得能量',
  cards_granted: '获得卡片',
  chances_granted: '抽奖次数',
  claimed_at: '领取时间',
  arrive_at: '到达时间',
  depart_at: '出发时间',
  travel_date: '旅行日期',
  drawn_at: '抽奖时间',
  created_at: '创建时间',
  acquired_at: '获得时间',
  status: '状态',
  location: '地点',
}

/** 官方领取记录（travel / draws / rewards 三类，服务端分页） */
function OfficialRecordsModal({ aid, open, onClose }: { aid: number | null; open: boolean; onClose: () => void }) {
  const [kind, setKind] = useState<'travel' | 'draws' | 'rewards'>('travel')
  const [rows, setRows] = useState<Record<string, unknown>[]>([])
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!open || !aid) return
    setLoading(true)
    api<{ items: Record<string, unknown>[]; total?: number }>(
      `/api/growth-center/accounts/${aid}/official-records?kind=${kind}&page=${page}`,
    )
      .then((r) => {
        setRows(r.items ?? [])
        setTotal(r.total ?? r.items?.length ?? 0)
      })
      .catch(() => setRows([]))
      .finally(() => setLoading(false))
  }, [open, aid, kind, page])

  const cols = rows[0]
    ? Object.keys(rows[0])
        .filter((k) => typeof rows[0][k] !== 'object')
        .map((k) => ({
          title: RECORD_COL_LABELS[k] ?? k,
          dataIndex: k,
          ellipsis: true,
          render: (v: unknown) => {
            // epoch 秒时间戳 → 北京时间
            if (typeof v === 'number' && v > 1_000_000_000) return <span className="num">{fmtTime(new Date(v * 1000).toISOString())}</span>
            return typeof v === 'object' ? JSON.stringify(v) : String(v ?? FMT_EMPTY)
          },
        }))
    : []

  return (
    <Modal title="官方领取记录" open={open} onCancel={onClose} footer={null} width={780}>
      <Space vertical size={8} style={{ width: '100%' }}>
        <Tabs
          activeKey={kind}
          onChange={(k) => {
            setKind(k as typeof kind)
            setPage(1)
          }}
          items={[
            { key: 'travel', label: '猫猫旅行' },
            { key: 'draws', label: '抽奖记录' },
            { key: 'rewards', label: '奖励领取' },
          ]}
        />
        <Table
          size="small"
          scroll={{ x: 'max-content' }}
          rowKey={(_, i) => String(i)}
          loading={loading}
          dataSource={rows}
          pagination={{ current: page, pageSize: 10, total, onChange: (p) => setPage(p), showSizeChanger: false, showTotal: (t) => `共 ${t} 条` }}
          columns={cols}
        />
      </Space>
    </Modal>
  )
}
