/**
 * 用量日志页内共用的文案小工具（拆页后 Table / Drawer 共用）。
 */
import type { MessageKey, Translate } from '@/i18n'
import { knownErrorKind } from '@/api/logs'

/** 错误分类 → 管理员看得懂的话；未知分类原样返回（tooltip 里仍有原值）。 */
export function errorKindText(t: Translate, kind: string | null | undefined): string {
  const raw = String(kind || '').trim()
  if (!raw) return ''
  const known = knownErrorKind(raw)
  return known ? t(('page.logs.kind.' + known) as MessageKey) : raw
}

/** 网络异常类型（ReadTimeout / ConnectError…）→ 中文说明；未知原样。 */
export function networkErrorText(t: Translate, type: string | null | undefined): string {
  const raw = String(type || '').trim()
  if (!raw) return ''
  const map: Record<string, MessageKey> = {
    ConnectTimeout: 'page.logs.net.connectTimeout',
    ReadTimeout: 'page.logs.net.readTimeout',
    WriteTimeout: 'page.logs.net.writeTimeout',
    PoolTimeout: 'page.logs.net.poolTimeout',
    ConnectError: 'page.logs.net.connectError',
    ReadError: 'page.logs.net.readError',
    WriteError: 'page.logs.net.writeError',
    RemoteProtocolError: 'page.logs.net.protocolError',
  }
  return map[raw] ? t(map[raw]) : raw
}
