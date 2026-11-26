#!/usr/bin/env node
/**
 * 新版控制台端到端冒烟（S30）：只用 Node 内置 fetch，不加依赖。
 * 用法：
 *   BASE_URL=http://127.0.0.1:8790 ADMIN_PASSWORD=... node admin/ui/scripts/smoke.mjs
 * 可选：ADMIN_USERNAME（默认 admin）。
 * 检查：登录 → /admin/v2/ → index.html 引用的全部 assets → 各页进入时的 GET 接口。
 */
const BASE = String(process.env.BASE_URL || 'http://127.0.0.1:8790').replace(/\/$/, '')
const USER = process.env.ADMIN_USERNAME || 'admin'
const PASS = process.env.ADMIN_PASSWORD || ''
if (!PASS) { console.error('缺少 ADMIN_PASSWORD'); process.exit(2) }

let cookie = ''
function remember(res) {
  const list = typeof res.headers.getSetCookie === 'function' ? res.headers.getSetCookie() : []
  for (const line of list) {
    const pair = line.split(';')[0]
    if (pair.includes('=')) cookie = cookie ? cookie + '; ' + pair : pair
  }
}
async function request(path, init = {}) {
  const headers = new Headers(init.headers || {})
  if (cookie) headers.set('Cookie', cookie)
  const res = await fetch(BASE + path, { ...init, headers, redirect: 'manual' })
  remember(res)
  return res
}

const failures = []
function check(name, ok, detail) {
  console.log((ok ? 'PASS ' : 'FAIL ') + name + (detail ? '  ' + detail : ''))
  if (!ok) failures.push(name + (detail ? '  ' + detail : ''))
}

const loginBody = new URLSearchParams({ username: USER, password: PASS })
const login = await request('/api/login', {
  method: 'POST',
  headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  body: loginBody,
})
check('登录 /api/login', login.status === 200, 'HTTP ' + login.status)

const index = await request('/admin/v2/')
const html = await index.text()
check('入口 /admin/v2/', index.status === 200 && html.includes('<div id="app">'), 'HTTP ' + index.status)

const assets = [...html.matchAll(/(?:src|href)="([^"]*\/admin\/v2\/assets\/[^"]+)"/g)].map((m) => m[1])
check('index.html 有 assets 引用', assets.length > 0, assets.length + ' 个')
for (const asset of assets) {
  const res = await request(asset)
  check('资源 ' + asset.replace('/admin/v2/', ''), res.status === 200, 'HTTP ' + res.status)
}

const endpoints = [
  '/api/app/version',
  '/api/platforms',
  '/api/overview?days=1',
  '/api/sentinel/status',
  '/api/gateway/config',
  '/api/accounts',
  '/api/keys',
  '/api/logs?page=1&page_size=1',
  '/api/stats/dashboard?days=1',
  '/api/models/configs',
  '/api/groups',
  '/api/schedules',
  '/api/credits/expiry',
  '/api/growth-center/accounts',
  '/api/client-profile',
  '/api/runtime-config',
  '/api/open/admin/config',
  '/api/agent/bootstrap',
]
for (const endpoint of endpoints) {
  const res = await request(endpoint)
  check('接口 ' + endpoint, res.status >= 200 && res.status < 400, 'HTTP ' + res.status)
}

console.log('')
if (failures.length) {
  console.error('冒烟失败 ' + failures.length + ' 项：')
  for (const item of failures) console.error(' - ' + item)
  process.exit(1)
}
console.log('冒烟通过：登录 + 入口 + ' + assets.length + ' 个资源 + ' + endpoints.length + ' 个接口')