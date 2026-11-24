/**
 * 平台管理 · 导入全部（FormDrawer + 后台任务 JobProgress）的状态与动作（从 PlatformsPage 拆出，r3）。
 * 逻辑逐字搬家，只把页面依赖（t / toast / 重新取数）改成参数。
 */
import { computed, effectScope, markRaw, onBeforeUnmount, ref } from 'vue'
import type { JobItem } from '@/components'
import { useJob, type JobSnapshot } from '@/composables'
import type { Translate } from '@/i18n'
import { parsePlatformBackup, submitPlatformImport } from '@/api/platforms-admin'

interface ToastLike {
  success: (message: string) => void
  error: (message: string) => void
}

export function usePlatformImport(options: {
  t: Translate
  toast: ToastLike
  /** 任务结束后刷新页面与平台计数。 */
  onJobFinished: () => void
}) {
  const { t, toast } = options


  const importOpen = ref(false)
  const importFiles = ref<Array<{ name: string; text: string }>>([])
  const importNotes = ref<string[]>([])
  const importPreview = ref<Array<{ storage: string; count: number }>>([])
  const submitting = ref(false)

  function openImport(preset?: string): void {
    importFiles.value = []
    importNotes.value = []
    importPreview.value = []
    importOpen.value = true
    if (preset) importNotes.value.push(t('page.platforms.import.preset', { name: preset }))
  }

  async function onImportFiles(event: Event): Promise<void> {
    const input = event.target as HTMLInputElement | null
    const files = Array.from(input?.files || [])
    if (!files.length) return
    const loaded: Array<{ name: string; text: string }> = []
    for (const file of files) {
      try {
        loaded.push({ name: file.name, text: await file.text() })
      } catch (error) {
        importNotes.value.push(
          t('page.platforms.import.readFailed', {
            name: file.name,
            reason: (error as Error)?.message || String(error),
          }),
        )
      }
    }
    importFiles.value = loaded
    const parsed = parsePlatformBackup(loaded)
    importNotes.value = parsed.notes.slice()
    importPreview.value = Object.entries(parsed.byPlatform).map(([storage, list]) => ({
      storage,
      count: list.length,
    }))
  }

  /** useJob 的最小结构（只要 snapshot / percent 两个 ref）。 */
  interface TrackedJobHandle {
    snapshot: { value: JobSnapshot | null }
    percent: { value: number }
  }

  const jobHandles = ref<Array<{ id: string; name: string; handle: TrackedJobHandle }>>([])
  const jobScopes: Array<ReturnType<typeof effectScope>> = []

  function trackJob(id: string, name: string): void {
    const scope = effectScope()
    const handle = scope.run(() =>
      useJob(id, {
        pollUrl: '/api/accounts/job/',
        onFinish: (snapshot: JobSnapshot) => {
          const result = (snapshot.result || {}) as { added?: number }
          if (snapshot.status === 'failed') {
            toast.error(
              t('page.platforms.import.failed', {
                name,
                reason: snapshot.error || t('page.platforms.import.unknownError'),
              }),
            )
          } else {
            toast.success(
              t('page.platforms.import.done', { name, count: Number(result.added || 0) }),
            )
          }
          options.onJobFinished()
        },
      }),
    )
    if (!handle) return
    jobScopes.push(scope)
    // useJob 返回 ref 句柄；不 markRaw 会被 ref 深响应式解包，jobItems 再读 .value 会崩。
    jobHandles.value.push({ id, name, handle: markRaw(handle) as unknown as TrackedJobHandle })
  }

  const jobItems = computed<JobItem[]>(() =>
    jobHandles.value
      .filter((entry) => entry.handle.snapshot.value !== null)
      .map((entry) => {
        const snapshot = entry.handle.snapshot.value as JobSnapshot
        const result = (snapshot.result || {}) as { added?: number; failed?: number }
        return {
          id: entry.id,
          name: entry.name,
          status: snapshot.status === 'running' ? 'running' : snapshot.status === 'failed' ? 'failed' : 'done',
          percent: entry.handle.percent.value,
          succeeded: Number(result.added || 0),
          failed: Number(result.failed || 0),
          message: snapshot.current || snapshot.error || '',
        } as JobItem
      }),
  )

  function hideJob(id: string): void {
    jobHandles.value = jobHandles.value.filter((entry) => entry.id !== id)
  }

  async function submitImport(): Promise<void> {
    if (submitting.value) return
    const parsed = parsePlatformBackup(importFiles.value)
    const entries = Object.entries(parsed.byPlatform).filter(([, list]) => list.length)
    if (!entries.length) {
      toast.error(t('page.platforms.import.noAccounts'))
      return
    }
    submitting.value = true
    let submitted = 0
    try {
      for (const [storage, list] of entries) {
        try {
          const jobId = await submitPlatformImport(storage, list)
          trackJob(jobId, t('page.platforms.import.jobName', { storage }))
          submitted += 1
        } catch (error) {
          toast.error(
            t('page.platforms.import.submitFailed', {
              storage,
              reason: (error as Error)?.message || String(error),
            }),
          )
        }
      }
      if (submitted) {
        toast.success(t('page.platforms.import.submitted', { count: submitted }))
        importOpen.value = false
      }
    } finally {
      submitting.value = false
    }
  }

  onBeforeUnmount(() => {
    for (const scope of jobScopes) scope.stop()
    jobScopes.length = 0
  })

  return {
    importOpen,
    importNotes,
    importPreview,
    submitting,
    openImport,
    onImportFiles,
    jobItems,
    hideJob,
    submitImport,
  }
}
