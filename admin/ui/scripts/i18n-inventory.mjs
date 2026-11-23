#!/usr/bin/env node
/**
 * i18n 盘点：统计前端还剩多少中文文案（无依赖，node scripts/i18n-inventory.mjs）。
 *
 * 口径故意保守：
 *   · 去掉 <style> / HTML 注释 / 块注释 / 行注释后再数；
 *   · cjkChars  = 中文字符数（含标点）；
 *   · cjkLines  = 仍含中文的代码行数（粗估需要改的行数）；
 *   · literals  = 引号里的中文字符串数量（粗估词条数）；
 *   · 模板里的裸文本按「行」算进 cjkLines，不计入 literals。
 *
 * 用法：
 *   node scripts/i18n-inventory.mjs            # 表格
 *   node scripts/i18n-inventory.mjs --json     # 机器可读
 *   node scripts/i18n-inventory.mjs --scope pages
 */
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(fileURLToPath(new URL('.', import.meta.url)), '..')
const SRC = join(ROOT, 'src')

const SCOPES = {
  pages: [join(SRC, 'pages')],
  shell: [join(SRC, 'layout')],
  components: [join(SRC, 'components')],
  api: [join(SRC, 'api')],
}

const EXCLUDE = [/.spec\.ts$/, /__tests__/, /_Placeholder.vue$/, /_Playground.vue$/]

function walk(dir) {
  const out = []
  for (const name of readdirSync(dir)) {
    const full = join(dir, name)
    const info = statSync(full)
    if (info.isDirectory()) out.push(...walk(full))
    else if (/\.(vue|ts)$/.test(name) && !EXCLUDE.some((re) => re.test(full))) out.push(full)
  }
  return out
}

function stripNoise(source) {
  return source
    .replace(/<style[\s\S]*?<\/style>/g, '')
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n')
    .map((line) => line.replace(/(?<!:)\/\/.*$/, ''))
    .join('\n')
}

const CJK = /[\u4e00-\u9fff]/g
const QUOTED = /(['"`])((?:\\.|(?!\1)[^\\])*?)\1/g

function measure(file) {
  const source = stripNoise(readFileSync(file, 'utf8'))
  const literals = []
  let match
  while ((match = QUOTED.exec(source))) {
    if (/[\u4e00-\u9fff]/.test(match[2])) literals.push(match[2])
  }
  const lines = source.split('\n').filter((line) => /[\u4e00-\u9fff]/.test(line))
  return {
    file: relative(ROOT, file).replaceAll('\\', '/'),
    cjkChars: (source.match(CJK) || []).length,
    cjkLines: lines.length,
    literals: literals.length,
  }
}

const args = process.argv.slice(2)
const json = args.includes('--json')
const scopeArg = args.indexOf('--scope')
const scope = scopeArg >= 0 ? args[scopeArg + 1] : null
const dirs = scope ? SCOPES[scope] : Object.values(SCOPES).flat()
if (!dirs) {
  console.error('unknown scope: ' + scope + ' (pages|shell|components|api)')
  process.exit(1)
}

const rows = dirs.flatMap(walk).map(measure).filter((row) => row.cjkChars > 0)
rows.sort((a, b) => b.cjkChars - a.cjkChars)

const total = rows.reduce(
  (acc, row) => ({
    cjkChars: acc.cjkChars + row.cjkChars,
    cjkLines: acc.cjkLines + row.cjkLines,
    literals: acc.literals + row.literals,
  }),
  { cjkChars: 0, cjkLines: 0, literals: 0 },
)

if (json) {
  console.log(JSON.stringify({ scope: scope || 'all', total, rows }, null, 2))
} else {
  const width = Math.max(12, ...rows.map((row) => row.file.length))
  console.log('file'.padEnd(width) + '  cjkChars  cjkLines  literals')
  console.log('-'.repeat(width + 30))
  for (const row of rows) {
    console.log(
      row.file.padEnd(width) +
        String(row.cjkChars).padStart(10) +
        String(row.cjkLines).padStart(10) +
        String(row.literals).padStart(10),
    )
  }
  console.log('-'.repeat(width + 30))
  console.log(
    ('total (' + rows.length + ' files)').padEnd(width) +
      String(total.cjkChars).padStart(10) +
      String(total.cjkLines).padStart(10) +
      String(total.literals).padStart(10),
  )
}
