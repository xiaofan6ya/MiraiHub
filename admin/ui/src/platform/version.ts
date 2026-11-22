import { readonly, ref } from 'vue'
import { http } from '@/api/http'
import { checkUpdates, isDesktop } from './desktop'

/**
 * 版本号与「有更新」。数据来源与旧版 `appVersion` / `WBDesktop.openUpdates` 相同：
 * · 版本号：`GET /api/app/version`（releases.py:16，无需登录）；
 * · 是否有新版本：`GET /api/app/releases` 的 `available`（生产镜像没有更新模块时返回 503，静默忽略）；
 * · 桌面壳里改用 `pywebview.api.check_updates()`。
 */
const appVersion = ref('')
const updateAvailable = ref(false)
const releasesError = ref('')
let loaded = false

export async function loadAppInfo(): Promise<void> {
  if (loaded) return
  loaded = true
  try {
    const data = await http<{ version?: string }>('GET', '/api/app/version')
    if (data?.version) appVersion.value = String(data.version)
  } catch {
    /* 版本号拿不到就留空，不阻塞外壳。 */
  }
  try {
    if (isDesktop()) {
      const native = await checkUpdates()
      const parsed = native as { available?: boolean } | null
      updateAvailable.value = Boolean(parsed?.available)
      return
    }
    const releases = await http<{ available?: boolean }>('GET', '/api/app/releases')
    updateAvailable.value = Boolean(releases?.available)
  } catch (error) {
    // 生产镜像按最小面构建，没有自我更新通道 → 503。这不是错误，只是没有更新信息。
    releasesError.value = error instanceof Error ? error.message : String(error)
  }
}

export function useAppInfo() {
  return {
    version: readonly(appVersion),
    hasUpdate: readonly(updateAvailable),
    releasesError: readonly(releasesError),
    load: loadAppInfo,
  }
}
