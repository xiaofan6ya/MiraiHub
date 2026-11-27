import type { Component } from 'vue'
import { Activity, ArrowLeftRight, BellRing, BookOpen, Bot, Boxes, Cable, CalendarClock, ChartNoAxesCombined, CircleDollarSign, DoorOpen, Globe2, KeyRound, Layers3, Network, RadioTower, Settings2, ShieldCheck, Sparkles, SquareTerminal, UsersRound, Waypoints, Wrench } from 'lucide-vue-next'
import type { PageKey } from '@/router/pages'

/** Presentation only: navigation, permissions and platform scope still come from the page table. */
const icons: Partial<Record<PageKey, Component>> = {
  accounts: UsersRound, platforms: Boxes, gateway: Network, models: Layers3, market: Sparkles,
  groups: Boxes, schedules: CalendarClock, logs: SquareTerminal, usage: Activity, keys: KeyRound,
  ip: Globe2, credits: CircleDollarSign, journal: BookOpen, growth: ChartNoAxesCombined,
  sentinel: ShieldCheck, stats: ChartNoAxesCombined, client: Cable, sync: ArrowLeftRight,
  alerts: BellRing, agent: Bot, openentry: DoorOpen, settings: Settings2, maintenance: Wrench,
  proxy: Waypoints,
}
export function pageIcon(key: PageKey): Component { return icons[key] ?? RadioTower }
