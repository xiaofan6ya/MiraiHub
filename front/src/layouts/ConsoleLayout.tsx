import {
  AppstoreOutlined,
  BarChartOutlined,
  BellOutlined,
  ClockCircleOutlined,
  CloudServerOutlined,
  CloudUploadOutlined,
  DashboardOutlined,
  DatabaseOutlined,
  FileSearchOutlined,
  GlobalOutlined,
  KeyOutlined,
  LogoutOutlined,
  MenuUnfoldOutlined,
  MinusOutlined,
  MobileOutlined,
  MoonOutlined,
  CloseOutlined,
  RobotOutlined,
  SaveOutlined,
  SettingOutlined,
  SafetyOutlined,
  SunOutlined,
  TagsOutlined,
  TeamOutlined,
  ToolOutlined,
  TransactionOutlined,
  UserOutlined,
} from '@ant-design/icons'
import { App as AntdApp, Avatar, Button, Drawer, Dropdown, Flex, Layout, Menu, Segmented, Select, Tooltip, Typography } from 'antd'
import { useEffect, useMemo, useState } from 'react'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import PlatformLogo from '../components/PlatformLogo'
import { usePlatform } from '../context/PlatformContext'
import { useAuth } from '../context/AuthContext'
import { useThemeMode } from '../context/ThemeContext'
import { isAndroid, isDesktop, nativeWindowAction } from '../bridges/native'

const { Sider, Header, Content } = Layout

interface NavItem {
  key: string
  label: string
  icon: React.ReactNode
}

/** 按功能分组的导航（旧版侧栏 8 组 + debug 独立成组） */
const NAV_GROUPS: { label: string; children: NavItem[] }[] = [
  {
    label: '总览',
    children: [
      { key: 'overview', label: '概览', icon: <DashboardOutlined /> },
      { key: 'sentinel', label: '风控哨兵', icon: <SafetyOutlined /> },
    ],
  },
  {
    label: '平台',
    children: [
      { key: 'platforms', label: '平台管理', icon: <AppstoreOutlined /> },
      { key: 'gateway', label: '调度网关', icon: <CloudServerOutlined /> },
    ],
  },
  {
    label: '账号',
    children: [
      { key: 'accounts', label: '账号池', icon: <TeamOutlined /> },
      { key: 'credits', label: '积分到期', icon: <ClockCircleOutlined /> },
      { key: 'journal', label: '积分流水', icon: <TransactionOutlined /> },
    ],
  },
  {
    label: '调用',
    children: [
      { key: 'usage', label: '使用记录', icon: <BarChartOutlined /> },
      { key: 'keys', label: 'API Keys', icon: <KeyOutlined /> },
      { key: 'ip', label: 'IP 管理', icon: <GlobalOutlined /> },
    ],
  },
  {
    label: '调试',
    children: [
      { key: 'logs', label: '用量日志', icon: <FileSearchOutlined /> },
      { key: 'app-source', label: '逆向产物', icon: <ToolOutlined /> },
    ],
  },
  {
    label: '配置',
    children: [
      { key: 'models', label: '模型配置', icon: <DatabaseOutlined /> },
      { key: 'groups', label: '模型分组', icon: <TagsOutlined /> },
      { key: 'schedules', label: '定时任务', icon: <SettingOutlined /> },
    ],
  },
  {
    label: '接入',
    children: [
      { key: 'sync', label: '同步设置', icon: <CloudUploadOutlined /> },
      { key: 'alerts', label: '预警管理', icon: <BellOutlined /> },
    ],
  },
  {
    label: '智能体',
    children: [{ key: 'agent', label: '创作台', icon: <RobotOutlined /> }],
  },
  {
    label: '系统',
    children: [
      { key: 'settings', label: '设置', icon: <SettingOutlined /> },
      { key: 'maintenance', label: '更新与备份', icon: <SaveOutlined /> },
    ],
  },
]

/** 安卓壳内追加"安卓设置"导航（WBAndroid 桥存在时） */
const ANDROID_NAV = { label: '安卓', children: [{ key: 'android', label: '安卓设置', icon: <MobileOutlined /> }] }

export default function ConsoleLayout() {
  const navigate = useNavigate()
  const location = useLocation()
  const { logout } = useAuth()
  const { mode, toggle } = useThemeMode()
  const { message } = AntdApp.useApp()

  const [collapsed, setCollapsed] = useState(false)
  const [isMobile, setIsMobile] = useState(() => window.matchMedia('(max-width: 768px)').matches)
  const [mobileOpen, setMobileOpen] = useState(false)
  const androidShell = isAndroid()

  // ≤768px：侧栏隐藏，改用抽屉
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 768px)')
    const onChange = (e: MediaQueryListEvent) => {
      setIsMobile(e.matches)
      if (!e.matches) setMobileOpen(false)
    }
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])
  const { platforms, current, select, loading } = usePlatform()

  const currentSpec = platforms.find((p) => p.id === current.platform)
  const realmOptions = useMemo(() => {
    const realms = currentSpec?.realms ?? []
    if (!realms.length) return null
    return [
      { label: '全部', value: '' },
      ...realms.map((r) => ({ label: currentSpec?.realm_labels?.[r.key] ?? r.label, value: r.key })),
    ]
  }, [currentSpec])

  // 旧版深链兼容：/admin?tab=xxx → #/xxx
  useEffect(() => {
    const q = new URLSearchParams(window.location.search)
    const tab = q.get('tab')
    if (tab && /^[a-z-]+$/.test(tab)) {
      q.delete('tab')
      const rest = q.toString()
      window.history.replaceState(null, '', `${window.location.pathname}${rest ? `?${rest}` : ''}`)
      navigate(`/${tab}`, { replace: true })
    }
  }, [navigate])

  // 桌面壳：启动时检查更新（对应旧版 WBDesktop.checkUpdatesAtStartup）
  useEffect(() => {
    if (!isDesktop()) return
    void window.pywebview?.api
      ?.check_updates?.()
      .then((r: unknown) => {
        if (r && typeof r === 'object' && (r as Record<string, unknown>).available) {
          message.info('发现新版本，可在"更新与备份"页查看')
        }
      })
      .catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const menuItems = useMemo(
    () =>
      [...(androidShell ? [ANDROID_NAV] : []), ...NAV_GROUPS].map((g) => ({
        type: 'group' as const,
        label: g.label,
        children: g.children.map((it) => ({ key: it.key, icon: it.icon, label: it.label })),
      })),
    [androidShell],
  )


  const selectedKey = location.pathname.replace(/^\//, '') || 'overview'

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Drawer
        title={
          <Flex align="center" gap={8}>
            <Avatar shape="square" size={24} src="/api/app/icon" />
            <Typography.Text strong>MiraiHub</Typography.Text>
          </Flex>
        }
        placement="left"
        open={isMobile && mobileOpen}
        onClose={() => setMobileOpen(false)}
        size={240}
        styles={{ body: { padding: '8px 0' } }}
      >
        <Menu
          mode="inline"
          items={menuItems}
          selectedKeys={[selectedKey]}
          onClick={({ key }) => {
            navigate(`/${key}`)
            setMobileOpen(false)
          }}
          style={{ borderInlineEnd: 'none' }}
        />
      </Drawer>
      {!isMobile && (
      <Sider
        width={216}
        collapsedWidth={64}
        collapsed={collapsed}
        theme="light"
        className="console-sider"
        breakpoint="lg"
        onBreakpoint={(broken) => setCollapsed(broken)}
        style={{ borderInlineEnd: '1px solid rgba(128,128,128,0.15)', position: 'sticky', top: 0, height: '100vh', overflow: 'auto' }}
      >
        <Flex align="center" gap={8} style={{ padding: '12px 16px' }}>
          <Avatar shape="square" size={28} src="/api/app/icon" />
          {!collapsed && (
            <Typography.Text strong style={{ fontSize: 15 }}>
              MiraiHub
            </Typography.Text>
          )}
        </Flex>
        <Menu
          mode="inline"
          items={menuItems}
          selectedKeys={[selectedKey]}
          inlineCollapsed={collapsed}
          onClick={({ key }) => navigate(`/${key}`)}
          style={{ borderInlineEnd: 'none' }}
        />
      </Sider>
      )}
      <Layout>
        <Header>
          <Flex align="center" justify="space-between" gap={12} wrap="wrap" style={{ minHeight: 48, height: 'auto', paddingBlock: 6 }}>
            <Flex align="center" gap={8}>
              <Button
                type="text"
                size="small"
                icon={<MenuUnfoldOutlined />}
                onClick={() => (isMobile ? setMobileOpen(true) : setCollapsed((v) => !v))}
              />
              {/* 全局产品口径：所有面板的操作都跟随这里所选的产品 */}
              <Select
                size="small"
                loading={loading}
                value={current.platform}
                style={{ minWidth: 150, maxWidth: isMobile ? 132 : undefined }}
                options={platforms.map((p) => ({
                  value: p.id,
                  label: (
                    <Flex align="center" gap={6}>
                      <PlatformLogo id={p.id} icon={p.icon} size={16} />
                      {p.label}
                    </Flex>
                  ),
                }))}
                onChange={(v) => {
                  const spec = platforms.find((p) => p.id === v)
                  select(v, spec?.realm ?? spec?.realms?.find((r) => r.default)?.key ?? spec?.realms?.[0]?.key ?? null)
                }}
              />
              {realmOptions && (
                <Segmented
                  size="small"
                  options={realmOptions}
                  value={current.realm ?? ''}
                  onChange={(v) => select(current.platform, (v as string) || null)}
                />
              )}
            </Flex>
            <Flex align="center" gap={4}>
              {isDesktop() && (
                <>
                  <Tooltip title="最小化">
                    <Button type="text" size="small" icon={<MinusOutlined />} onClick={() => void nativeWindowAction('minimize')} />
                  </Tooltip>
                  <Tooltip title="关闭">
                    <Button type="text" size="small" icon={<CloseOutlined />} onClick={() => void nativeWindowAction('close')} />
                  </Tooltip>
                </>
              )}
              <Tooltip title={mode === 'dark' ? '切换亮色' : '切换暗色'}>
                <Button type="text" size="small" icon={mode === 'dark' ? <SunOutlined /> : <MoonOutlined />} onClick={toggle} />
              </Tooltip>
              <Dropdown
                menu={{
                  items: [{ key: 'logout', icon: <LogoutOutlined />, label: '退出登录' }],
                  onClick: ({ key }) => {
                    if (key === 'logout') void logout()
                  },
                }}
              >
                <Button type="text" size="small" icon={<UserOutlined />} />
              </Dropdown>
            </Flex>
          </Flex>
        </Header>
        <Content style={{ padding: 16, minHeight: 'auto' }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  )
}
