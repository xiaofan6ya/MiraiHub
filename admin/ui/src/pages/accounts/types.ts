/**
 * 账号池子组件之间共用的类型（放在 .ts 里，避免从 .vue 里 import 类型）。
 */

/** 批量条上的同步操作（POST /api/accounts/bulk 的 action）。 */
export type BulkAction = 'enable' | 'disable' | 'refresh' | 'check' | 'delete'

/** 「添加账号」抽屉的几种内容（与 api/account-add.ts 的 AddMethodKey 一一对应）。 */
export type AddDrawerKind = 'manual' | 'import' | 'scan' | 'phone' | 'authorize'

/** 详情抽屉底部按钮抛回页面的操作键（与行操作同名，页面统一处理）。 */
export type DetailActionKey = 'edit' | 'refresh' | 'check' | 'enable' | 'disable' | 'delete' | 'checkin'
