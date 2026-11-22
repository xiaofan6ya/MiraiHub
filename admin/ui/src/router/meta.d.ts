import 'vue-router'

/** 路由 meta 的唯一约定（03-shell §1）：导航、面包屑、命令面板都读它。 */
declare module 'vue-router' {
  interface RouteMeta {
    /** 分组中文名 */
    group?: string
    /** 分组 key（`mh.lastTab.<groupKey>`） */
    groupKey?: string
    /** 页面中文名 */
    title?: string
    /** lucide 图标名（分组图标） */
    icon?: string
    /** 顶栏是否显示平台上下文选择器 */
    platformScoped?: boolean
    /** 不在侧栏/命令面板出现的内部页面 */
    hidden?: boolean
  }
}
