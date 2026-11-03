import { App as AntdApp, Button, Card, Col, Form, Input, InputNumber, Modal, Row, Select, Space, Switch, Tag, Typography } from 'antd'
import { LockOutlined } from '@ant-design/icons'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import PageHeader from '../components/PageHeader'

interface RuntimeField {
  attr: string
  label: string
  group: string
  kind: 'bool' | 'int' | 'float' | 'str'
  desc: string
  min: number | null
  max: number | null
  step: number
  restart: boolean
  value: unknown
  default: unknown
  overridden: boolean
}

export default function SettingsPage() {
  const { message, modal } = AntdApp.useApp()
  const navigate = useNavigate()
  const [fields, setFields] = useState<RuntimeField[]>([])
  const [group, setGroup] = useState('')
  const [dirty, setDirty] = useState<Record<string, unknown>>({})
  const [saving, setSaving] = useState(false)
  const [pwdForm] = Form.useForm()
  const [pwdOpen, setPwdOpen] = useState(false)

  const load = useCallback(async () => {
    try {
      const r = await api<{ fields: RuntimeField[] }>('/api/runtime-config')
      setFields(r.fields ?? [])
      const g = r.fields?.[0]?.group
      if (g) setGroup((prev) => prev || g)
    } catch (e) {
      message.error((e as Error).message || '运行配置加载失败')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const groups = useMemo(() => [...new Set(fields.map((f) => f.group))], [fields])
  const groupFields = fields.filter((f) => f.group === group)

  const askRestart = () => {
    modal.confirm({
      title: '部分配置需要重启服务才能生效',
      content: '是否立即重启服务？重启需要数秒，期间网关短暂不可用。',
      okText: '立即重启',
      cancelText: '稍后手动重启',
      onOk: async () => {
        try {
          await api('/api/maintenance/restart', { method: 'POST', body: { confirm: true } })
          message.success('重启指令已下发，8 秒后自动刷新页面…')
          setTimeout(() => window.location.reload(), 8000)
        } catch (e) {
          message.error((e as Error).message)
        }
      },
    })
  }

  const saveRuntime = async () => {
    if (!Object.keys(dirty).length) return
    setSaving(true)
    try {
      const r = await api<{ changed: string[]; reverted: string[]; errors: Record<string, string>; fields: RuntimeField[] }>('/api/runtime-config', {
        method: 'PUT',
        body: { updates: dirty },
      })
      setFields(r.fields ?? [])
      setDirty({})
      if (r.errors && Object.keys(r.errors).length) {
        message.warning(`部分字段未能保存：${Object.entries(r.errors).map(([k, v]) => `${k}：${v}`).join('；')}`)
      } else {
        message.success(`已保存 ${r.changed?.length ?? 0} 项${r.reverted?.length ? `，恢复默认 ${r.reverted.length} 项` : ''}`)
      }
      if ((r.fields ?? []).some((f) => f.restart && f.overridden && r.changed?.includes(f.attr))) askRestart()
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const changePassword = async () => {
    const v = await pwdForm.validateFields()
    if (v.new_password.length < 6) {
      message.warning('新密码至少 6 位')
      return
    }
    try {
      await api('/api/admin/password', { method: 'PATCH', body: { old_password: v.old_password, new_password: v.new_password } })
      message.success('密码已修改')
      pwdForm.resetFields()
      setPwdOpen(false)
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const renderField = (f: RuntimeField) => {
    const cur = f.attr in dirty ? dirty[f.attr] : f.value
    const changed = f.attr in dirty
    return (
      <Row key={f.attr} align="middle" gutter={8} style={{ padding: '6px 0', borderBottom: '1px solid rgba(128,128,128,0.08)' }}>
        <Col span={7}>
          <Space size={4}>
            <Typography.Text>{f.label}</Typography.Text>
            {f.restart && <Tag color="warning" style={{ fontSize: 11 }}>需重启</Tag>}
            {f.overridden && <Tag color="processing" style={{ fontSize: 11 }}>已覆盖</Tag>}
          </Space>
          {f.desc && (
            <Typography.Text type="secondary" style={{ display: 'block', fontSize: 11 }}>
              {f.desc}
            </Typography.Text>
          )}
        </Col>
        <Col span={11}>
          {f.kind === 'bool' ? (
            <Switch size="small" checked={!!cur} onChange={(v) => setDirty((d) => ({ ...d, [f.attr]: v }))} />
          ) : f.kind === 'int' || f.kind === 'float' ? (
            <InputNumber
              size="small"
              style={{ width: 200 }}
              min={f.min ?? undefined}
              max={f.max ?? undefined}
              step={f.kind === 'float' ? f.step || 0.1 : 1}
              value={typeof cur === 'number' ? cur : Number(cur)}
              onChange={(v) => setDirty((d) => ({ ...d, [f.attr]: v }))}
            />
          ) : (
            <Input size="small" style={{ width: 260 }} value={String(cur ?? '')} onChange={(e) => setDirty((d) => ({ ...d, [f.attr]: e.target.value }))} />
          )}
        </Col>
        <Col span={6} style={{ textAlign: 'right' }}>
          <Space size={4}>
            {changed && (
              <Button size="small" onClick={() => setDirty((d) => { const n = { ...d }; delete n[f.attr]; return n })}>
                撤销
              </Button>
            )}
            {f.overridden && (
              <Button
                size="small"
                onClick={() => setDirty((d) => ({ ...d, [f.attr]: null }))}
              >
                恢复默认
              </Button>
            )}
            <Typography.Text type="secondary" style={{ fontSize: 11 }} className="num">
              默认 {String(f.default)}
            </Typography.Text>
          </Space>
        </Col>
      </Row>
    )
  }

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      <PageHeader
        title="设置"
        onRefresh={() => void load()}
        actions={
          Object.keys(dirty).length > 0 && (
            <Button type="primary" loading={saving} onClick={() => void saveRuntime()}>
              保存 {Object.keys(dirty).length} 项修改
            </Button>
          )
        }
      />

      <Card size="small" style={{ height: '100%' }} title="运行配置">
        <Space orientation="vertical" size={12} style={{ width: '100%' }}>
          <Select value={group} onChange={setGroup} style={{ minWidth: 220 }} options={groups.map((g) => ({ value: g, label: g }))} />
          <div>{groupFields.map(renderField)}</div>
          {Object.keys(dirty).length > 0 && (
            <Button type="primary" loading={saving} onClick={() => void saveRuntime()}>
              保存 {Object.keys(dirty).length} 项修改
            </Button>
          )}
        </Space>
      </Card>


      <Row gutter={[12, 12]} align="stretch">
        <Col xs={24} md={12}>
          <Card size="small" style={{ height: '100%' }} title="账号安全">
            <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 12 }}>
              定期更换管理密码可降低爆破风险；修改后当前会话保持登录，新会话需用新密码。
              若后台凭据来自 .env（ADMIN_USERNAME / ADMIN_PASSWORD），请直接修改 .env
              后重启 —— 环境变量优先，此处修改对它不生效。
            </Typography.Paragraph>
            <Button type="primary" icon={<LockOutlined />} onClick={() => setPwdOpen(true)}>
              修改登录密码
            </Button>
          </Card>
        </Col>
        <Col xs={24} md={12}>
          <Card size="small" style={{ height: '100%' }} title="逆向产物工具">
            <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
              客户端拆包、产物检索与注册凭证管理等开发工具已归入 <Tag>调试 → 逆向产物</Tag>。
            </Typography.Paragraph>
            <Button onClick={() => navigate('/app-source')}>打开逆向产物</Button>
          </Card>
        </Col>
      </Row>
      <Modal
        title="修改登录密码"
        open={pwdOpen}
        onCancel={() => {
          setPwdOpen(false)
          pwdForm.resetFields()
        }}
        onOk={() => void changePassword()}
        okText="确认修改"
        width={420}
      >
        <Form form={pwdForm} layout="vertical" onFinish={() => void changePassword()}>
          <Form.Item name="old_password" label="当前密码" rules={[{ required: true, message: '请输入当前密码' }]}>
            <Input.Password />
          </Form.Item>
          <Form.Item name="new_password" label="新密码（≥6 位）" rules={[{ required: true, message: '请输入新密码' }]}>
            <Input.Password />
          </Form.Item>
          <Form.Item
            name="confirm"
            label="确认新密码"
            dependencies={['new_password']}
            rules={[
              { required: true, message: '请再次输入' },
              ({ getFieldValue }) => ({
                validator: (_, v) => (v === getFieldValue('new_password') ? Promise.resolve() : Promise.reject(new Error('两次输入不一致'))),
              }),
            ]}
          >
            <Input.Password />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  )
}
