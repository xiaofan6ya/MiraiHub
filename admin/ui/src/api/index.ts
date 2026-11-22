/**
 * api 层统一出口：页面只从这里取函数，不关心文件拆分。
 * 每个业务域一个文件（05-patterns §1），类型定义与函数写在同一个文件里。
 */
export * from './http'
export * from './auth'
export * from './session'
export * from './platforms'
