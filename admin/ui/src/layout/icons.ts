import type { Component } from 'vue'
import {
  Activity,
  Boxes,
  Bot,
  DoorOpen,
  FlaskConical,
  LayoutDashboard,
  Plug,
  Settings,
  SlidersHorizontal,
  Users,
} from 'lucide-vue-next'

/**
 * 路由 meta.icon / 分组 icon 字符串 → lucide 组件。
 * 只登记分组图标 + 内部页面图标，导航不要绕开这张表直接 import 图标。
 */
const ICONS: Record<string, Component> = {
  LayoutDashboard,
  Boxes,
  Users,
  Activity,
  SlidersHorizontal,
  Plug,
  Bot,
  DoorOpen,
  Settings,
  FlaskConical,
}

export function iconComponent(name: string | undefined | null): Component {
  if (name && ICONS[name]) return ICONS[name]
  return Settings
}
