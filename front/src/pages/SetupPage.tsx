import { App as AntdApp, Alert, Avatar, Button, Card, Flex, Form, Input, Space, Steps, Typography } from 'antd'
import { IdcardOutlined, LockOutlined, MoonOutlined, SunOutlined, UserOutlined } from '@ant-design/icons'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { useAuth } from '../context/AuthContext'
import { useThemeMode } from '../context/ThemeContext'

interface SetupState {
  configured: boolean
  /** 当前来源是否被允许初始化（默认仅本机） */
  needs_setup: boolean
  source: string
  local: boolean
  /** 以下三项仅未初始化时返回，用于表单前端预校验 */
  username_min?: number
  password_min?: number
  db_ready?: boolean
}

/** 首次初始化：没有管理员账号时引导创建（桌面端 / 安卓端重点路径） */
export default function SetupPage() {
  const { message } = AntdApp.useApp()
  const { mode, toggle } = useThemeMode()
  const { refreshSetup, adoptToken } = useAuth()
  const navigate = useNavigate()
  const [form] = Form.useForm()
  const [state, setState] = useState<SetupState | null>(null)
  const [loadError, setLoadError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    void api<SetupState>('/api/setup/state', { skipPlatform: true })
      .then((s) => {
        setState(s)
        // 已初始化就没必要停在这里，交给登录页（这里不重复写密码）。
        if (s.configured) navigate('/login', { replace: true })
      })
      .catch((e: Error) => setLoadError(e.message || '无法读取初始化状态'))
  }, [navigate])

  const usernameMin = state?.username_min ?? 3
  const passwordMin = state?.password_min ?? 8
  const blocked = !!state && !state.needs_setup

  const onFinish = async (v: { username: string; password: string }) => {
    setSubmitting(true)
    try {
      const r = await api<{ ok: boolean; token: string; username: string }>('/api/setup', {
        method: 'POST',
        body: { username: v.username, password: v.password },
        skipPlatform: true,
      })
      if (!r?.token) throw new Error('初始化响应缺少 token')
      // 先让 AuthContext 采纳令牌、再刷新初始化状态，最后换路由：
      // 顺序反了会被 RequireAuth 按旧状态弹回登录页。
      adoptToken(r.token)
      await refreshSetup()
      message.success(`管理员账号已创建，欢迎 ${r.username}`)
      navigate('/overview', { replace: true })
    } catch (e) {
      const err = e as Error & { status?: number }
      if (err.status === 409) {
        message.info('管理员账号已存在，请直接登录')
        await refreshSetup()
        navigate('/login', { replace: true })
        return
      }
      message.error(err.message || '创建失败')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Flex align="center" justify="center" style={{ minHeight: '100vh', padding: 16 }}>
      <Button
        type="text"
        icon={mode === 'dark' ? <SunOutlined /> : <MoonOutlined />}
        onClick={toggle}
        style={{ position: 'fixed', top: 12, right: 12 }}
      />
      <Card style={{ width: 460, maxWidth: '100%' }}>
        <Flex vertical align="center" gap={8} style={{ marginBottom: 18 }}>
          <Avatar shape="square" size={44} src="/api/app/icon" />
          <Typography.Title level={4} style={{ margin: 0 }}>
            创建管理员账号
          </Typography.Title>
          <Typography.Text type="secondary" style={{ textAlign: 'center' }}>
            首次使用需要一个账号来保护管理后台。创建后用它登录局域网内的管理页；
            本机客户端会保持免登录。
          </Typography.Text>
        </Flex>

        <Steps
          size="small"
          current={0}
          orientation="horizontal"
          style={{ marginBottom: 18 }}
          items={[{ title: '设置账号' }, { title: '开始使用' }]}
        />

        {loadError && (
          <Alert type="error" showIcon title="无法读取初始化状态" description={loadError} style={{ marginBottom: 14 }} />
        )}
        {blocked && (
          <Alert
            type="warning"
            showIcon
            title="当前来源不允许初始化"
            description="这个入口只对本机开放。请在运行服务的机器上打开管理后台，或设置 ADMIN_SETUP_ALLOW_REMOTE=1 后重试。"
            style={{ marginBottom: 14 }}
          />
        )}
        {state?.db_ready === false && (
          <Alert
            type="warning"
            showIcon
            title="数据库暂不可用"
            description="账号写入数据库后才生效。稍后重试；如持续失败请查看服务日志。"
            style={{ marginBottom: 14 }}
          />
        )}

        <Form form={form} layout="vertical" onFinish={onFinish} autoComplete="off" disabled={blocked}>
          <Form.Item
            name="username"
            label="管理员用户名"
            rules={[
              { required: true, message: '请输入用户名' },
              { min: usernameMin, message: `至少 ${usernameMin} 个字符` },
              { max: 64, message: '最多 64 个字符' },
              { pattern: /^[\w.@ -]+$/, message: '只能包含字母、数字与 . _ - @ 空格' },
            ]}
          >
            <Input prefix={<UserOutlined />} placeholder="例如 admin" autoFocus allowClear />
          </Form.Item>
          <Form.Item
            name="password"
            label={`密码（至少 ${passwordMin} 位）`}
            rules={[
              { required: true, message: '请输入密码' },
              { min: passwordMin, message: `密码至少 ${passwordMin} 位` },
              { max: 128, message: '密码最多 128 位' },
            ]}
          >
            <Input.Password prefix={<LockOutlined />} placeholder="不要使用 12345678 这类常见口令" />
          </Form.Item>
          <Form.Item
            name="confirm"
            label="确认密码"
            dependencies={['password']}
            rules={[
              { required: true, message: '请再次输入密码' },
              ({ getFieldValue }) => ({
                validator: (_, v) =>
                  v === getFieldValue('password')
                    ? Promise.resolve()
                    : Promise.reject(new Error('两次输入不一致')),
              }),
            ]}
          >
            <Input.Password prefix={<IdcardOutlined />} placeholder="再次输入密码" />
          </Form.Item>
          <Space orientation="vertical" size={8} style={{ width: '100%' }}>
            <Button type="primary" htmlType="submit" block loading={submitting}>
              创建并进入控制台
            </Button>
            <Button type="link" block onClick={() => navigate('/login', { replace: true })}>
              已有账号，去登录
            </Button>
          </Space>
        </Form>

        <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 12 }}>
          账号保存在本机数据库。之后可在「设置 → 账号安全」修改密码；忘记密码需清空数据库重新初始化。
        </Typography.Text>
      </Card>
    </Flex>
  )
}
