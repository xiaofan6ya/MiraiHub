/** 后台任务（useJob 轮询出的状态给 JobProgress 展示）。 */
export interface JobItem {
  id: string
  name: string
  status: 'running' | 'done' | 'failed'
  /** 0–100 */
  percent: number
  succeeded?: number
  failed?: number
  message?: string
}
