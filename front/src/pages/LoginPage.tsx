import { App as AntdApp, Alert, Avatar, Button, Card, Flex, Form, Input, Typography } from 'antd'
import { LockOutlined, MoonOutlined, SunOutlined, UserOutlined } from '@ant-design/icons'
import { useEffect, useState } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { useThemeMode } from '../context/ThemeContext'
import { ensureLocalSession, isDesktopMode } from '../bridges/native'
import { setToken, type ApiError } from '../api/client'

export default function LoginPage() {
  const { authed, ready, needsSetup, login } = useAuth()
  const { mode, toggle } = useThemeMode()
  const { message } = AntdApp.useApp()
  const navigate = useNavigate()
  const [submitting, setSubmitting] = useState(false)

  // 还没有管理员账号：登录一定失败，直接切到创建引导。
  // 本地壳（桌面/安卓）没有 .env，这是唯一能创建账号的路径。
  useEffect(() => {
    if (ready && needsSetup) navigate('/setup', { replace: true })
  }, [ready, needsSetup, navigate])

  // 桌面/安卓本地壳：无需登录表单，直接走桥换取本地 token
  useEffect(() => {
    if (!ready || authed) return
    if (!isDesktopMode()) return
    void ensureLocalSession(true).then((t) => {
      if (t) {
        setToken(t)
        window.location.reload()
      }
    })
  }, [ready, authed])

  if (ready && authed) return <Navigate to="/overview" replace />

  const onFinish = async ({ username, password }: { username: string; password: string }) => {
    setSubmitting(true)
    try {
      await login(username, password)
      message.success('登录成功')
      navigate('/overview', { replace: true })
    } catch (e) {
      const err = e as ApiError
      if (err.code === 'setup_required' || err.status === 503) {
        // 后端明确说「还没创建管理员账号」：交给上面的 effect 跳引导。
        message.info('尚未创建管理员账号，正在跳转创建引导')
        navigate('/setup', { replace: true })
        return
      }
      message.error(err.message || '登录失败')
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
      <Card style={{ width: 360 }}>
        <Flex vertical align="center" gap={12} style={{ marginBottom: 20 }}>
          <Avatar shape="square" size={44} src="/api/app/icon" />
          <Typography.Title level={4} style={{ margin: 0 }}>
            MiraiHub 管理后台
          </Typography.Title>
          <Typography.Text type="secondary">一个 API Key，调用多家上游</Typography.Text>
        </Flex>
        {needsSetup && (
          <Alert
            type="info"
            showIcon
            title="还没有管理员账号"
            description="首次使用请先创建账号，之后用该账号登录。"
            action={
              <Button size="small" type="primary" onClick={() => navigate('/setup')}>
                去创建
              </Button>
            }
            style={{ marginBottom: 14 }}
          />
        )}
        <Form layout="vertical" onFinish={onFinish} autoComplete="off">
          <Form.Item name="username" label="用户名" rules={[{ required: true, message: '请输入用户名' }]}>
            <Input prefix={<UserOutlined />} placeholder="管理用户名" autoFocus />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }]}>
            <Input.Password prefix={<LockOutlined />} placeholder="管理密码" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={submitting}>
            登录
          </Button>
        </Form>
        <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 12 }}>
          连续失败会触发登录保护，请稍后再试。
        </Typography.Text>
      </Card>
    </Flex>
  )
}
