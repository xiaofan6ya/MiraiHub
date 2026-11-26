/** IP 管理页子组件共用的类型（<script setup> 里不能导出，单独放这里）。 */

/** 封禁抽屉提交给页面的内容；expiresAt 为 null = 永久。 */
export interface IpBlockSubmit {
  ip: string
  note: string
  expiresAt: string | null
  /** 预设时长（秒）；有值时优先提交 duration_seconds。 */
  durationSeconds?: number | null
}

/** 各区块的加载失败状态（ErrorBlock 的标题原因 + 可展开详情）。 */
export interface IpErrorState {
  message: string
  detail: string
}

/** 清理自动封禁的进度。 */
export interface IpCleanProgress {
  done: number
  total: number
}
