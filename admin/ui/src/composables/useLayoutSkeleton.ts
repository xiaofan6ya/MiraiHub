import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch, type Ref } from 'vue'
import { useDelayedFlag } from './useDelayedFlag'

export interface LayoutRect { x: number; y: number; width: number; height: number; radius: number; surface: boolean }
export interface LayoutSnapshot { width: number; height: number; rects: LayoutRect[] }
const storageKey = 'mh.layout-geometry.v1'
// A production module URL includes the build hash, so changed layouts invalidate themselves.
const revision = import.meta.url
const selectors = '.mh-card,.mh-kpi,.mh-strip,.mh-filter,.page-header,.traffic-trend,.mh-chart,.mh-table,h1,h2,h3,p,button,input,th,td,.mh-kpi__num,.mh-kpi__label'
const surfaces = '.mh-card,.mh-kpi,.mh-strip,.mh-filter,.page-header,.mh-table'
export function captureLayout(host: HTMLElement): LayoutSnapshot {
  const bounds = host.getBoundingClientRect()
  const rects: LayoutRect[] = []
  for (const node of Array.from(host.querySelectorAll<HTMLElement>(selectors)).slice(0, 600)) {
    if (node.closest('.layout-skeleton-layer,[data-layout-placeholder],.surface-sheen') || node.closest('[hidden]')) continue
    const style = getComputedStyle(node)
    if (style.display === 'none' || style.visibility === 'hidden') continue
    const r = node.getBoundingClientRect()
    if (r.width < 4 || r.height < 3 || r.top - bounds.top > 12000) continue
    const surface = node.matches(surfaces)
    rects.push({ x: Math.round(r.left - bounds.left), y: Math.round(r.top - bounds.top), width: Math.round(r.width), height: Math.round(Math.min(r.height, surface ? 4000 : 320)), radius: Math.min(parseFloat(style.borderRadius) || (surface ? 12 : 4), 24), surface })
    if (rects.length === 180) break
  }
  const bottom = Math.max(0, ...rects.map(rect => rect.y + rect.height))
  return { width: Math.round(bounds.width), height: Math.round(Math.min(14000, Math.max(bounds.height, bottom))), rects }
}
function valid(value: unknown): value is LayoutSnapshot {
  if (!value || typeof value !== 'object') return false
  const v = value as LayoutSnapshot
  return Number.isFinite(v.width) && v.width > 0 && Number.isFinite(v.height) && v.height > 0 && v.height <= 14000 && Array.isArray(v.rects) && v.rects.length <= 180 && v.rects.every(r => ['x','y','width','height','radius'].every(k => Number.isFinite(r[k as keyof LayoutRect])) && r.width > 0 && r.height > 0 && typeof r.surface === 'boolean')
}
export function layoutCacheKey(path: string, width: number, locale: string, version = revision): string {
  return JSON.stringify([version, path, Math.round(width), locale])
}
export function useLayoutSkeleton(host: Ref<HTMLElement | null>, path: () => string, locale: () => string) {
  const loading = ref(false)
  const snapshot = ref<LayoutSnapshot | null>(null)
  const show = useDelayedFlag(() => loading.value, 140)
  const mode = computed(() => loading.value ? snapshot.value ? 'cached' : 'cold' : undefined)
  const reservedHeight = computed(() => loading.value && snapshot.value ? snapshot.value.height + 'px' : undefined)
  let observer: MutationObserver | undefined
  let resize: ResizeObserver | undefined
  let timer: ReturnType<typeof setTimeout> | undefined
  let frame = 0
  let key = ''
  let disposed = false
  // Once a page has settled, a later inline task must never hide the whole page.
  const settledPaths = new Set<string>()
  function cache(): Record<string, LayoutSnapshot> {
    try { const value = JSON.parse(sessionStorage.getItem(storageKey) || '{}'); return value && typeof value === 'object' && !Array.isArray(value) ? value : {} } catch { return {} }
  }
  function read() {
    const el = host.value
    if (!el) return
    const nextKey = layoutCacheKey(path(), el.getBoundingClientRect().width, locale())
    if (key === nextKey) return
    key = nextKey
    const saved = cache()[key]
    snapshot.value = valid(saved) ? saved : null
  }
  function capture() {
    timer = undefined
    const el = host.value
    if (!el || loading.value || disposed || document.hidden || !el.querySelector('.page-header,.mh-card,.mh-kpi')) return
    // Never learn a transient empty/error/skeleton layout as the real page.
    if (el.querySelector('.mh-error,.error-block,[data-layout-placeholder]')) return
    const next = captureLayout(el)
    if (!valid(next) || !next.rects.length) return
    const values = cache()
    if (JSON.stringify(values[key]) === JSON.stringify(next)) return
    snapshot.value = next
    const recent = Object.entries(values).filter(([id]) => id.startsWith('["' + revision + '"') && id !== key).slice(-5)
    try { sessionStorage.setItem(storageKey, JSON.stringify(Object.fromEntries([...recent, [key, next]]))) } catch { /* Geometry is an optional optimization. */ }
  }
  function inspect() {
    frame = 0
    const el = host.value
    if (!el || disposed) return
    read()
    const placeholders = Array.from(el.querySelectorAll<HTMLElement>('[class*="skeleton"],.mh-table__loading')).filter(node => !node.closest('.layout-skeleton-layer,[class*="skeleton--inline"]') && (node.classList.contains('mh-table__loading') || [...node.classList].some(name => name !== 'mh-skeleton' && name.endsWith('-skeleton'))))
    const initial = settledPaths.has(path()) ? [] : placeholders
    const set = new Set(initial)
    for (const marked of el.querySelectorAll<HTMLElement>('[data-layout-placeholder]')) if (!set.has(marked)) delete marked.dataset.layoutPlaceholder
    for (const node of initial) node.dataset.layoutPlaceholder = 'true'
    loading.value = initial.length > 0
    if (!placeholders.length && el.querySelector('.page-header,.mh-card,.mh-kpi')) settledPaths.add(path())
    if (timer) clearTimeout(timer)
    if (!placeholders.length) timer = setTimeout(capture, 500)
  }
  function schedule() {
    if (!frame && !disposed && typeof requestAnimationFrame === 'function') frame = requestAnimationFrame(inspect)
  }
  onMounted(() => {
    observer = new MutationObserver(records => {
      if (records.some(record => !(record.target instanceof Element) || !record.target.closest('.layout-skeleton-layer'))) schedule()
    })
    if (host.value) observer.observe(host.value, { childList: true, subtree: true, attributes: true, attributeFilter: ['class', 'aria-busy'] })
    if (typeof ResizeObserver === 'function') { resize = new ResizeObserver(schedule); if (host.value) resize.observe(host.value) }
    window.addEventListener('resize', schedule, { passive: true })
    schedule()
  })
  watch([path, locale], async () => { key = ''; loading.value = false; snapshot.value = null; await nextTick(); schedule() }, { flush: 'post' })
  onBeforeUnmount(() => {
    disposed = true; observer?.disconnect(); resize?.disconnect()
    window.removeEventListener('resize', schedule)
    if (frame) cancelAnimationFrame(frame)
    if (timer) clearTimeout(timer)
  })
  return { loading, show, snapshot, mode, reservedHeight }
}
