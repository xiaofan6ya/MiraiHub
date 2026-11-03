import { Suspense, lazy } from 'react'
import { App as AntdApp, ConfigProvider, Flex, Spin, theme as antdTheme } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { HashRouter, Navigate, Route, Routes } from 'react-router-dom'
import { ThemeProvider, useThemeMode } from './context/ThemeContext'
import { AuthProvider, useAuth } from './context/AuthContext'
import { PlatformProvider } from './context/PlatformContext'
import { JobProvider } from './context/JobContext'
import ConsoleLayout from './layouts/ConsoleLayout'
import { makeTheme } from './theme'

// 全部页面按路由懒加载（React.lazy + 动态 import）
const LoginPage = lazy(() => import('./pages/LoginPage'))
const SetupPage = lazy(() => import('./pages/SetupPage'))
const OverviewPage = lazy(() => import('./pages/OverviewPage'))
const SentinelPage = lazy(() => import('./pages/SentinelPage'))
const PlatformsPage = lazy(() => import('./pages/PlatformsPage'))
const GatewayPage = lazy(() => import('./pages/GatewayPage'))
const AccountsPage = lazy(() => import('./pages/accounts/AccountsPage'))
const CreditsPage = lazy(() => import('./pages/CreditsPage'))
const JournalPage = lazy(() => import('./pages/JournalPage'))
const GrowthPage = lazy(() => import('./pages/GrowthPage'))
const UsagePage = lazy(() => import('./pages/UsagePage'))
const KeysPage = lazy(() => import('./pages/KeysPage'))
const IpPage = lazy(() => import('./pages/IpPage'))
const LogsPage = lazy(() => import('./pages/LogsPage'))
const ModelsPage = lazy(() => import('./pages/ModelsPage'))
const GroupsPage = lazy(() => import('./pages/GroupsPage'))
const SchedulesPage = lazy(() => import('./pages/SchedulesPage'))
const SyncPage = lazy(() => import('./pages/SyncPage'))
const AlertsPage = lazy(() => import('./pages/AlertsPage'))
const AgentPage = lazy(() => import('./pages/AgentPage'))
const SettingsPage = lazy(() => import('./pages/SettingsPage'))
const MaintenancePage = lazy(() => import('./pages/MaintenancePage'))
const AppSourcePage = lazy(() => import('./pages/AppSourcePage'))
const AndroidSettingsPage = lazy(() => import('./pages/AndroidSettingsPage'))

/** 无壳小窗外壳：跟随主题的布局背景（popup 无 Layout，需自行铺底色） */
function PopupShell({ children }: { children: React.ReactNode }) {
  const { token } = antdTheme.useToken()
  return (
    <Flex vertical style={{ minHeight: '100vh', background: token.colorBgLayout, padding: 16 }}>
      {children}
    </Flex>
  )
}

function PageFallback() {
  return (
    <div style={{ display: 'flex', justifyContent: 'center', padding: '120px 0' }}>
      <Spin size="large" />
    </div>
  )
}

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { authed, ready, needsSetup } = useAuth()
  if (!ready) return <PageFallback />
  // 没有管理员账号时先引导创建：本地壳（桌面/安卓）本来就没有可用密码，
  // 让它停在登录页只会卡死 —— 那里是唯一能创建账号的入口。
  if (needsSetup) return <Navigate to="/setup" replace />
  if (!authed) return <Navigate to="/login" replace />
  return <>{children}</>
}

function AppRoutes() {
  return (
    <Routes>
      <Route
        path="/login"
        element={
          <Suspense fallback={<PageFallback />}>
            <LoginPage />
          </Suspense>
        }
      />
      {/* 首次初始化（无管理员账号时）：不需要登录态，创建成功后直接进控制台 */}
      <Route
        path="/setup"
        element={
          <Suspense fallback={<PageFallback />}>
            <SetupPage />
          </Suspense>
        }
      />
      {/* 成长计划浏览器小窗：无侧栏/顶栏的独立壳 */}
      <Route
        path="/growth-popup"
        element={
          <RequireAuth>
            <Suspense fallback={<PageFallback />}>
              <PopupShell>
                <GrowthPage popup />
              </PopupShell>
            </Suspense>
          </RequireAuth>
        }
      />
      <Route
        element={
          <RequireAuth>
            <Suspense fallback={<PageFallback />}>
              <ConsoleLayout />
            </Suspense>
          </RequireAuth>
        }
      >
        <Route path="overview" element={<OverviewPage />} />
        <Route path="sentinel" element={<SentinelPage />} />
        <Route path="platforms" element={<PlatformsPage />} />
        <Route path="gateway" element={<GatewayPage />} />
        <Route path="accounts" element={<AccountsPage />} />
        <Route path="credits" element={<CreditsPage />} />
        <Route path="journal" element={<JournalPage />} />
        <Route path="growth" element={<GrowthPage />} />
        <Route path="usage" element={<UsagePage />} />
        <Route path="keys" element={<KeysPage />} />
        <Route path="ip" element={<IpPage />} />
        <Route path="logs" element={<LogsPage />} />
        <Route path="models" element={<ModelsPage />} />
        <Route path="groups" element={<GroupsPage />} />
        <Route path="schedules" element={<SchedulesPage />} />
        <Route path="sync" element={<SyncPage />} />
        <Route path="alerts" element={<AlertsPage />} />
        <Route path="agent" element={<AgentPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="maintenance" element={<MaintenancePage />} />
        <Route path="app-source" element={<AppSourcePage />} />
        <Route path="android" element={<AndroidSettingsPage />} />
        <Route path="*" element={<Navigate to="/overview" replace />} />
      </Route>
    </Routes>
  )
}

function ThemedApp() {
  const { mode } = useThemeMode()
  return (
    <ConfigProvider locale={zhCN} theme={makeTheme(mode)}>
      <AntdApp message={{ maxCount: 4 }} notification={{ maxCount: 4 }}>
        <AuthProvider>
          <PlatformProvider>
            <JobProvider>
              <HashRouter>
                <AppRoutes />
              </HashRouter>
            </JobProvider>
          </PlatformProvider>
        </AuthProvider>
      </AntdApp>
    </ConfigProvider>
  )
}

export default function App() {
  return (
    <ThemeProvider>
      <ThemedApp />
    </ThemeProvider>
  )
}
