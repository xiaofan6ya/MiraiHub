import { createRouter, createWebHashHistory, type RouteRecordRaw, type Router } from 'vue-router'
import AppShell from '@/layout/AppShell.vue'
import Placeholder from '@/pages/_Placeholder.vue'
import { PAGES, atRootHash, clearLegacyQuery, readLegacyTab } from './pages'

/**
 * 路由实例（hash 模式）：`/admin/v2/#/accounts`，后端只需要服务一个 index.html。
 * 页面表在 ./pages.ts（导航、页签、命令面板共用），这里只负责路由与旧链接兼容。
 */
export * from './pages'

const pageRoutes: RouteRecordRaw[] = PAGES.map((page) => ({
  path: page.key,
  name: page.key,
  component: Placeholder,
  meta: {
    group: page.group,
    groupKey: page.groupKey,
    title: page.title,
    icon: page.icon,
    platformScoped: page.platformScoped,
  },
}))

/** 建一个路由器实例。导出工厂是为了测试可以拿全新的实例（旧链接重定向只跑一次）。 */
export function createAppRouter(): Router {
  let legacyChecked = false

  const router = createRouter({
    history: createWebHashHistory(),
    routes: [
      {
        path: '/',
        component: AppShell,
        children: [
          { path: '', redirect: '/overview' },
          ...pageRoutes,
          {
            path: '_playground',
            name: '_playground',
            component: () => import('@/pages/_Playground.vue'),
            meta: {
              group: '系统',
              groupKey: 'system',
              title: '组件预览',
              icon: 'FlaskConical',
              platformScoped: false,
              hidden: true,
            },
          },
        ],
      },
      { path: '/:pathMatch(.*)*', redirect: '/overview' },
    ],
    scrollBehavior: () => ({ top: 0 }),
  })

  router.beforeEach(() => {
    if (legacyChecked) return true
    legacyChecked = true
    // 只处理「没有 hash 路由的旧链接」，显式 hash 优先。
    if (!atRootHash()) return true
    const legacy = readLegacyTab()
    if (!legacy) return true
    clearLegacyQuery()
    return { path: '/' + legacy }
  })

  return router
}

const router = createAppRouter()

export default router
