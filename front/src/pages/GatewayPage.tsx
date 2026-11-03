import { App as AntdApp, Button, Card, Col, Row, Space, Switch, Table, Tag, Tooltip, Typography } from 'antd'
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import { platformSpeed, type PlatformSpeedRow } from '../api/platforms'
import { usePlatform } from '../context/PlatformContext'
import PageHeader from '../components/PageHeader'
import TopologyViz, { type TopoEdge, type TopoNode } from '../components/TopologyViz'
import PlatformLogo from '../components/PlatformLogo'
import { FMT_EMPTY, fmtCredit, fmtInt, fmtPct } from '../utils/format'

interface GatewayConfig {
  strategy: string
  priority: Record<string, number>
  disabled: Record<string, { at: number; reason: string }>
  spread_unbound: boolean
  strategies: { value: string; label: string; description: string }[]
  platforms: { id: string; label: string; managed: boolean }[]
}

interface OrderItem {
  platform: string
  label: string
  ok: boolean
  accounts: number
  active: number
  cooling: number
  usable: number
  balance_remain: number
  balance_total: number
  balance_ratio: number | null
  disabled: boolean
  available: boolean
  reason: string
}

export default function GatewayPage() {
  const { message } = AntdApp.useApp()
  const { platformKey } = usePlatform()
  const [config, setConfig] = useState<GatewayConfig | null>(null)
  const [order, setOrder] = useState<OrderItem[]>([])
  const [speed, setSpeed] = useState<PlatformSpeedRow[]>([])
  const [loading, setLoading] = useState(false)
  const [burst, setBurst] = useState(0)
  const dragIndexRef = useRef<number | null>(null)
  const [dragOver, setDragOver] = useState<number | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [c, o, s] = await Promise.all([
        api<GatewayConfig>('/api/gateway/config'),
        api<{ items: OrderItem[] }>('/api/gateway/order'),
        platformSpeed(24),
      ])
      setConfig(c)
      setOrder(o?.items ?? [])
      setSpeed(s?.items ?? [])
    } catch (e) {
      message.error((e as Error).message || '网关配置加载失败')
    } finally {
      setLoading(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [platformKey])

  useEffect(() => {
    void load()
  }, [load])

  const saveConfig = async (patch: Partial<Record<string, unknown>>) => {
    try {
      const r = await api<{ config: GatewayConfig }>('/api/gateway/config', { method: 'PUT', body: patch })
      if (r?.config) setConfig(r.config)
      message.success('调度配置已保存')
      void load()
    } catch (e) {
      message.error((e as Error).message || '保存失败')
    }
  }

  /** 拖拽排序：把 from 位置的平台移到 to，priority 重排为 10/20/30…（仅 priority 策略） */
  const moveOrder = async (from: number, to: number) => {
    if (from < 0 || from === to || config?.strategy !== 'priority') return
    const ids = order.map((o) => o.platform)
    const [moved] = ids.splice(from, 1)
    ids.splice(to, 0, moved)
    const priority: Record<string, number> = {}
    ids.forEach((id, i) => {
      priority[id] = (i + 1) * 10
    })
    try {
      await api('/api/gateway/config', { method: 'PUT', body: { priority } })
      message.success('优先级已更新')
      void load()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  // 拓扑：Key → 网关 → 平台（按 order 可用账号数为权重）
  const nodes: TopoNode[][] = [
    [{ id: 'key', label: 'API Key', sub: `${order.length} 平台` }],
    [{ id: 'gw', label: '调度网关', tone: 'ok', sub: config?.strategy || '' }],
    order.map((o) => ({
      id: o.platform,
      label: o.label || o.platform,
      tone: o.disabled ? 'danger' : o.usable > 0 ? 'ok' : 'warn',
      sub: o.disabled ? '已禁用' : `${o.usable} 可用`,
      icon: <PlatformLogo id={o.platform} size={20} />,
    })) as TopoNode[],
  ]
  const maxUsable = Math.max(1, ...order.map((o) => o.usable))
  const edges: TopoEdge[] = [
    { from: 'key', to: 'gw', weight: 0.8, tone: 'ok' },
    ...order.map((o) => ({
      from: 'gw',
      to: o.platform,
      weight: o.disabled ? 0 : Math.max(0.15, o.usable / maxUsable),
      tone: o.disabled ? ('idle' as const) : o.usable > 0 ? ('ok' as const) : ('warn' as const),
    })),
  ]

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      <PageHeader
        title="调度网关"
        description="跨平台调度策略与平台优先级"
        onRefresh={() => void load()}
        refreshing={loading}
      />

      {/* 调度策略 */}
      <Card size="small" style={{ height: '100%' }} title="调度策略">
        <Row gutter={[8, 8]} align="stretch">
          {(config?.strategies ?? []).map((s) => (
            <Col key={s.value} xs={24} md={12} xl={8}>
              <Card
                size="small"
                hoverable
                style={{ height: '100%', ...(config?.strategy === s.value ? { borderColor: '#2E9BFF' } : {}) }}
                onClick={() => void saveConfig({ strategy: s.value })}
              >
                <div>
                  <Space size={6} align="center">
                    <Typography.Text strong>{s.label}</Typography.Text>
                    {config?.strategy === s.value ? <Tag color="processing" style={{ margin: 0 }}>当前</Tag> : null}
                  </Space>
                  <Typography.Paragraph type="secondary" style={{ fontSize: 12, margin: 0 }}>
                    {s.description}
                  </Typography.Paragraph>
                </div>
              </Card>
            </Col>
          ))}
        </Row>
        <Row style={{ marginTop: 12 }} align="middle">
          <Tooltip title="没有绑定账号的 Key（无头请求）也按调度策略跨平台派发">
            <Typography.Text>无头请求也走跨平台调度：</Typography.Text>
          </Tooltip>
          <Switch
            checked={config?.spread_unbound ?? false}
            onChange={(v) => void saveConfig({ spread_unbound: v })}
            style={{ marginLeft: 8 }}
          />
        </Row>
      </Card>

      <Row gutter={[12, 12]} align="stretch">
        <Col xs={24} xl={14}>
          <Card
            size="small"
            title="调度可视化"
            extra={
              <Button size="small" onClick={() => setBurst((v) => v + 1)}>
                模拟一批请求
              </Button>
            }
          >
            <TopologyViz layers={nodes} edges={edges} height={Math.max(220, order.length * 44 + 80)} burst={burst} />
          </Card>
        </Col>
        <Col xs={24} xl={10}>
          <Card size="small" style={{ height: '100%' }} title="当前调度顺序" extra={config?.strategy === 'priority' ? <Typography.Text type="secondary" style={{ fontSize: 12 }}>拖动行调整优先级</Typography.Text> : <Typography.Text type="secondary" style={{ fontSize: 12 }}>切换到「优先级」策略后可拖动排序</Typography.Text>}>
            <Table
              size="small"
              rowKey="platform"
              loading={loading}
              dataSource={order}
              pagination={false}
              onRow={config?.strategy === 'priority' ? (_r, idx) => {
                const i = idx ?? -1
                return {
                  draggable: true,
                  onDragStart: () => {
                    dragIndexRef.current = i
                  },
                  onDragOver: (e: React.DragEvent) => {
                    e.preventDefault()
                    if (dragOver !== i) setDragOver(i)
                  },
                  onDrop: () => void moveOrder(dragIndexRef.current ?? -1, i),
                  onDragEnd: () => {
                    dragIndexRef.current = null
                    setDragOver(null)
                  },
                  style: dragOver === i ? { borderTop: '2px solid #2E9BFF' } : undefined,
                }
              } : undefined}
              columns={[
                {
                  title: '',
                  width: 30,
                  render: () => (config?.strategy === 'priority' ? <Typography.Text type="secondary" style={{ cursor: 'grab' }}>⠿</Typography.Text> : null),
                },
                { title: '#', width: 40, render: (_, __, i) => <span className="num">{i + 1}</span> },
                { title: '平台', dataIndex: 'label', render: (v, r) => (r.disabled ? <Typography.Text type="secondary">{v}</Typography.Text> : <Typography.Text strong>{v}</Typography.Text>) },
                { title: '原因', dataIndex: 'reason', ellipsis: true },
                { title: '可用', dataIndex: 'usable', width: 70, align: 'right', render: (v) => <span className="num">{fmtInt(v)}</span> },
                { title: '余额', dataIndex: 'balance_remain', width: 90, align: 'right', render: (v, r) => <span className="num">{fmtCredit(v)}{r.balance_ratio !== null && r.balance_ratio !== undefined ? `（${fmtPct(r.balance_ratio, 0)}）` : ''}</span> },
              ]}
            />
          </Card>
        </Col>
      </Row>

      <Row gutter={[12, 12]} align="stretch">
        <Col span={24}>
          <Card size="small" style={{ height: '100%' }} title="平台速度（TTFT / TPS，近 24h）">
            <Table
              size="small"
              rowKey={(r) => String(r.platform ?? r.label)}
              loading={loading}
              dataSource={speed}
              pagination={false}
              columns={[
                { title: '平台', dataIndex: 'label', ellipsis: true },
                { title: 'TTFT P50', dataIndex: 'ttfb_p50', width: 100, align: 'right', render: (v) => <span className="num">{v ? `${Math.round(v)}ms` : FMT_EMPTY}</span> },
                { title: 'TPS P50', dataIndex: 'tps_p50', width: 90, align: 'right', render: (v) => <span className="num">{v ? v.toFixed(1) : FMT_EMPTY}</span> },
                { title: '成功率', dataIndex: 'success_rate', width: 90, align: 'right', render: (v) => <span className="num">{fmtPct(v, 1)}</span> },
                { title: '跑分', dataIndex: 'score', width: 70, align: 'right', render: (v) => <span className="num">{v ? v.toFixed(1) : FMT_EMPTY}</span> },
              ]}
            />
          </Card>
        </Col>

      </Row>
    </Space>
  )
}
