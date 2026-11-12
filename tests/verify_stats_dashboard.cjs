/* 数据看板 UI 验证：真实控制台 + 打桩 API，离线跑（不碰上游）。
 *
 * 锁定四件事：
 *   1. 侧栏「概览」组里能看到「数据看板」，点进去 7 张 KPI 卡片齐全；
 *   2. 消耗分布默认柱状图，切「面积图」后 SVG 里出现堆叠路径；
 *   3. 调用分析三视图可切，排行榜有行；
 *   4. 深浅两套主题、桌面/手机两档宽度都不横向溢出。
 *
 * 用法：node tests/verify_stats_dashboard.cjs [outDir]
 */
const fs = require('fs');
const path = require('path');
const http = require('http');
const assert = require('assert');

const { chromium } = require('C:/Users/Administrator/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');

const root = path.resolve(__dirname, '..');
const consoleDir = path.join(root, 'admin', 'console');
const out = process.argv[2] || path.join(root, 'screenshots', 'stats-dashboard');

const buckets = Array.from({ length: 24 }, (_, i) => {
  const hour = String(i).padStart(2, '0');
  return `2026-11-11 ${hour}:00`;
});
const shape = (fn) => buckets.map((_, index) => fn(index));

const dashboard = {
  window: { days: 1, granularity: 'hour', start: '2026-11-11T00:00:00Z', end: '2026-11-12T00:00:00Z', minutes: 1440 },
  filters: { platform: '', model: '' },
  platform_label: '全部平台',
  summary: {
    requests: 868514, successes: 868253, failures: 261, success_rate: 0.9997,
    credits: 2413.76, total_tokens: 1430000000, prompt_tokens: 1080000000,
    completion_tokens: 350000000, cached_tokens: 260000000, cache_hit_rate: 0.2407,
    rpm: 603.14, tpm: 993055.56, avg_latency_ms: 12640, avg_ttfb_ms: 400,
    throughput_tps: 76.8, window_minutes: 1440,
  },
  models: [
    { model: 'gpt-4.1', label: 'GPT-4.1', requests: 312000, successes: 311980, failures: 20, success_rate: 0.9999, tokens: 510000000, credits: 905.2, avg_latency_ms: 12400, avg_ttfb_ms: 380, tps: 82.1 },
    { model: 'claude-sonnet-4', label: 'Claude Sonnet 4', requests: 221000, successes: 220910, failures: 90, success_rate: 0.9996, tokens: 402000000, credits: 712.4, avg_latency_ms: 13800, avg_ttfb_ms: 520, tps: 66.4 },
    { model: 'gemini-2.5-pro', label: 'Gemini 2.5 Pro', requests: 168000, successes: 167980, failures: 20, success_rate: 0.9999, tokens: 268000000, credits: 402.1, avg_latency_ms: 11200, avg_ttfb_ms: 340, tps: 91.2 },
    { model: 'deepseek-v4.1-flash', label: 'DeepSeek V4.1 Flash', requests: 121000, successes: 120930, failures: 70, success_rate: 0.9994, tokens: 190000000, credits: 264.8, avg_latency_ms: 8600, avg_ttfb_ms: 260, tps: 104.6 },
    { model: 'qwen3-235b-a22b', label: 'Qwen3 235B', requests: 31000, successes: 30990, failures: 10, success_rate: 0.9997, tokens: 42000000, credits: 88.4, avg_latency_ms: 10100, avg_ttfb_ms: 300, tps: 77.3 },
    { model: 'gpt-4.1-mini', label: 'GPT-4.1 mini', requests: 15514, successes: 15463, failures: 51, success_rate: 0.9967, tokens: 18000000, credits: 40.9, avg_latency_ms: 9400, avg_ttfb_ms: 280, tps: 88.0 },
  ],
  platforms: [
    { value: 'workbuddy', label: 'WorkBuddy', requests: 520000, success_rate: 0.9998, tokens: 900000000, credits: 1402.4 },
    { value: 'loomy', label: 'Loomy', requests: 210000, success_rate: 0.9996, tokens: 340000000, credits: 610.2 },
    { value: 'trae', label: 'Trae', requests: 138514, success_rate: 0.9991, tokens: 190000000, credits: 401.1 },
  ],
  distribution: {
    buckets,
    series: [
      { name: 'GPT-4.1', metric: 'credits', values: shape((i) => 40 + Math.round(Math.sin(i / 2) * 18) + i) },
      { name: 'Claude Sonnet 4', metric: 'credits', values: shape((i) => 32 + Math.round(Math.cos(i / 3) * 14) + i / 2) },
      { name: 'Gemini 2.5 Pro', metric: 'credits', values: shape((i) => 26 + Math.round(Math.sin(i / 4) * 12)) },
      { name: 'DeepSeek V4.1 Flash', metric: 'credits', values: shape((i) => 18 + Math.round(Math.cos(i / 5) * 9)) },
      { name: 'Qwen3 235B', metric: 'credits', values: shape((i) => 9 + (i % 5)) },
      { name: '其他 3 个模型', metric: 'credits', values: shape((i) => 4 + (i % 3)) },
    ],
  },
  trend: {
    buckets,
    total_requests: shape((i) => 24000 + Math.round(Math.sin(i / 2) * 9000) + i * 320),
    series: [
      { name: 'GPT-4.1', requests: shape((i) => 12000 + Math.round(Math.sin(i / 2) * 4200)), tokens: [], credits: [] },
      { name: 'Claude Sonnet 4', requests: shape((i) => 8000 + Math.round(Math.cos(i / 3) * 3200)), tokens: [], credits: [] },
      { name: 'Gemini 2.5 Pro', requests: shape((i) => 5200 + Math.round(Math.sin(i / 4) * 2100)), tokens: [], credits: [] },
    ],
  },
  ranking: [
    { model: 'gpt-4.1', label: 'GPT-4.1', requests: 312000, share: 0.3592 },
    { model: 'claude-sonnet-4', label: 'Claude Sonnet 4', requests: 221000, share: 0.2544 },
    { model: 'gemini-2.5-pro', label: 'Gemini 2.5 Pro', requests: 168000, share: 0.1934 },
    { model: 'deepseek-v4.1-flash', label: 'DeepSeek V4.1 Flash', requests: 121000, share: 0.1393 },
    { model: 'qwen3-235b-a22b', label: 'Qwen3 235B', requests: 31000, share: 0.0357 },
    { model: 'gpt-4.1-mini', label: 'GPT-4.1 mini', requests: 15514, share: 0.0179 },
  ],
  errors: [
    { kind: 'server', count: 118 },
    { kind: 'soft_rate', count: 74 },
    { kind: 'session_dead', count: 41 },
    { kind: 'transport', count: 28 },
  ],
};

const market = {
  window_hours: 24,
  start: '2026-11-11T00:00:00Z',
  end: '2026-11-12T00:00:00Z',
  source: 'rollup',
  total: 4,
  available_models: 3,
  items: [
    { key: 'gpt-4.1', name: 'GPT-4.1', platforms: ['workbuddy'], platform_labels: ['WorkBuddy'],
      models: ['gpt-4.1'], requests: 312000, success_rate: 0.9999, tokens: 510000000,
      credits: 905.2, avg_latency_ms: 12400, avg_ttfb_ms: 380, tps: 82.1,
      pricing: [{ platform: 'workbuddy', platform_label: 'WorkBuddy', model_id: 'gpt-4.1',
                  multiplier: 0.5, free: false }],
      free: false, min_multiplier: 0.5, active_accounts: 5, exhausted_accounts: 0, available: true },
    { key: 'claude-sonnet-4', name: 'Claude Sonnet 4', platforms: ['workbuddy', 'loomy'],
      platform_labels: ['WorkBuddy', 'Loomy'], models: ['claude-sonnet-4'],
      requests: 221000, success_rate: 0.9996, tokens: 402000000, credits: 712.4,
      avg_latency_ms: 13800, avg_ttfb_ms: 520, tps: 66.4,
      pricing: [{ platform: 'loomy', platform_label: 'Loomy', model_id: 'claude-sonnet-4',
                  multiplier: 0.8, free: false }],
      free: false, min_multiplier: 0.8, active_accounts: 3, exhausted_accounts: 1, available: true },
    { key: 'gemini-2.5-pro', name: 'Gemini 2.5 Pro', platforms: ['trae'],
      platform_labels: ['Trae'], models: ['gemini-2.5-pro'], requests: 168000,
      success_rate: 0.9999, tokens: 268000000, credits: 402.1, avg_latency_ms: 11200,
      avg_ttfb_ms: 340, tps: 91.2,
      pricing: [{ platform: 'trae', platform_label: 'Trae', model_id: 'gemini-2.5-pro',
                  multiplier: 0.4, free: false }],
      free: false, min_multiplier: 0.4, active_accounts: 2, exhausted_accounts: 2, available: false },
    { key: 'deepseek-v4.1-flash', name: 'DeepSeek V4.1 Flash', platforms: ['workbuddy'],
      platform_labels: ['WorkBuddy'], models: ['dfmodel'], requests: 0,
      success_rate: null, tokens: 0, credits: 0, avg_latency_ms: 0, avg_ttfb_ms: 0, tps: 0,
      pricing: [{ platform: 'workbuddy', platform_label: 'WorkBuddy', model_id: 'dfmodel',
                  multiplier: 0, free: true }],
      free: true, min_multiplier: 0, active_accounts: 5, exhausted_accounts: 0, available: true },
  ],
};

const aggregate = {
  items: [
    { key: 'gpt-4.1', name: 'GPT-4.1', platforms: [
      { platform: 'workbuddy', platform_label: 'WorkBuddy', model_id: 'gpt-4.1',
        client_id: 'gpt-4.1-workbuddy', multiplier: 0.5 }] },
    { key: 'deepseek-v4.1-flash', name: 'DeepSeek V4.1 Flash', platforms: [
      { platform: 'workbuddy', platform_label: 'WorkBuddy', model_id: 'dfmodel',
        client_id: 'dfmodel-workbuddy', multiplier: 0 }] },
  ],
};

const server = http.createServer((req, res) => {
  const pathname = new URL(req.url, 'http://localhost').pathname;
  let file;
  if (pathname === '/admin' || pathname === '/admin/') file = path.join(consoleDir, 'index.html');
  else if (pathname.startsWith('/admin/assets/')) file = path.join(consoleDir, pathname.slice('/admin/assets/'.length));
  else if (pathname.startsWith('/static/')) file = path.join(root, 'admin', pathname);
  else file = path.join(root, pathname);
  fs.readFile(file, (err, data) => {
    if (err) { res.writeHead(404); return res.end('not found'); }
    const type = file.endsWith('.js') ? 'text/javascript'
      : file.endsWith('.css') ? 'text/css'
      : file.endsWith('.svg') ? 'image/svg+xml'
      : file.endsWith('.png') ? 'image/png'
      : file.endsWith('.ico') ? 'image/x-icon'
      : 'text/html; charset=utf-8';
    res.writeHead(200, { 'Content-Type': type });
    res.end(data);
  });
});

(async () => {
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()); });

    await page.addInitScript(() => {
      localStorage.setItem('wb_admin_token', 'synthetic-token');
      localStorage.setItem('wb_admin_username', 'tester');
    });

    await page.route('**/api/**', async (route) => {
      const url = new URL(route.request().url());
      let data = { items: [], accounts: [], summary: {}, models: [], platforms: [] };
      if (url.pathname === '/api/app/version') data = { version: '1.1.1' };
      else if (url.pathname === '/api/stats/dashboard') data = dashboard;
      else if (url.pathname === '/api/stats/model-market') data = market;
      else if (url.pathname === '/api/models/aggregate') data = aggregate;
      else if (url.pathname === '/api/overview') data = {
        balance: { total: 1000, used: 200 }, today: { requests: 10, tokens: 100 },
        cache: { hit: 0.3 }, accounts: { total: 3, active: 3 },
      };
      else if (url.pathname === '/api/platforms') data = { items: [] };
      else if (url.pathname === '/api/sentinel/status') data = { items: [], signals: [] };
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(data) });
    });

    await page.goto(`http://127.0.0.1:${server.address().port}/admin`);
    await page.waitForFunction(() => typeof window.activateTab === 'function', null, { timeout: 15000 });
    await page.evaluate(() => activateTab('stats'));

    await page.locator('#panel-stats:not(.hidden)').waitFor();
    await page.locator('.wbd-kpi').first().waitFor();
    // KPI 有 620ms 的滚动动效，断言前先等它落地。
    await page.waitForTimeout(900);

    if (process.env.WBD_DEBUG) {
      console.log('PAGE_ERRORS', JSON.stringify(errors));
      console.log('DIST', await page.evaluate(() => {
        const host = document.getElementById('wbdDistChart');
        return {
          series: window.WBStatsDashboard.state.data.distribution.series.length,
          first: window.WBStatsDashboard.state.data.distribution.series[0].values.slice(0, 3),
          firstType: typeof window.WBStatsDashboard.state.data.distribution.series[0].values[0],
          hostWidth: host ? host.clientWidth : -1,
          buckets: window.WBStatsDashboard.state.data.distribution.buckets.length,
          legend: document.querySelectorAll('.wbd-legend-item').length,
          rects: host ? host.querySelectorAll('rect').length : -1,
          tail: host ? host.innerHTML.slice(-200) : 'MISSING',
        };
      }));
    }

    assert.equal(await page.locator('.wbd-kpi').count(), 7, 'KPI 应有 7 张');
    assert.equal(await page.locator('.wbd-model-chip').count(), 6, '性能健康带应有 6 个模型');
    assert((await page.locator('.wbd-kpi').nth(1).innerText()).includes('2,413.76'), '总积分格式');
    assert((await page.locator('.wbd-kpi').nth(3).innerText()).includes('2.60亿'), '缓存读取应缩写');

    // 柱子：24 个桶 × 6 个系列里凡是 >0 的都应有 rect
    const bars = await page.locator('#wbdDistChart svg rect').count();
    assert(bars >= 24, '消耗分布应有柱子，实际 ' + bars);
    await page.screenshot({ path: path.join(out, 'dashboard-desktop.png'), fullPage: true });

    // 切面积图
    await page.getByRole('button', { name: '面积图' }).click();
    await page.waitForFunction(() => document.querySelectorAll('#wbdDistChart svg path').length >= 3);
    assert((await page.locator('#wbdDistChart svg path').count()) >= 3, '面积图应有堆叠路径');
    await page.screenshot({ path: path.join(out, 'dashboard-area.png'), fullPage: true });

    // 调用次数分布 / 排行
    await page.getByRole('button', { name: '调用次数分布' }).click();
    await page.waitForFunction(() => document.querySelectorAll('#wbdAnalysisChart svg rect').length > 0);
    await page.getByRole('button', { name: '调用次数排行' }).click();
    await page.locator('.wbd-rank-row').first().waitFor();
    assert.equal(await page.locator('.wbd-rank-row').count(), 6, '排行行数');
    assert((await page.locator('.wbd-rank-row').first().innerText()).includes('35.9%'), '排行占比');
    await page.screenshot({ path: path.join(out, 'dashboard-ranking.png'), fullPage: true });

    // ── 模型广场 ────────────────────────────────────────────────────────
    await page.evaluate(() => activateTab('market'));
    await page.locator('#panel-market:not(.hidden)').waitFor();
    await page.locator('.wbm-card').first().waitFor();
    assert.equal(await page.locator('.wbm-card').count(), 4, '广场卡片数');
    assert((await page.locator('.wbm-card').first().innerText()).includes('×0.50'), '定价显示');
    assert((await page.locator('.wbm-card').nth(3).innerText()).includes('免费'), '免费模型');
    assert((await page.locator('.wbm-card').nth(2).innerText()).includes('当前不可用'), '不可用标记');
    await page.screenshot({ path: path.join(out, 'market-desktop.png'), fullPage: true });

    await page.locator('#wbmAvailable').check();
    await page.waitForFunction(() => document.querySelectorAll('.wbm-card').length === 3);
    await page.getByRole('button', { name: '列表' }).click();
    await page.locator('.wbm-table tbody tr').first().waitFor();
    assert.equal(await page.locator('.wbm-table tbody tr').count(), 3, '列表行数');
    await page.screenshot({ path: path.join(out, 'market-list.png'), fullPage: true });
    await page.locator('#wbmAvailable').uncheck();
    await page.getByRole('button', { name: '卡片' }).click();

    // 回到看板再拍深浅两套主题
    await page.evaluate(() => activateTab('stats'));
    await page.locator('#panel-stats:not(.hidden)').waitFor();

    // 深色 + 窄屏
    for (const [name, width, height, dark] of [['dark', 1280, 960, true], ['mobile', 390, 844, false]]) {
      await page.setViewportSize({ width, height });
      await page.evaluate((value) => { document.documentElement.dataset.theme = value ? 'dark' : 'light'; }, dark);
      await page.evaluate(() => window.WBStatsDashboard.repaint());
      await page.waitForTimeout(250);
      assert(!(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)), name + ' 横向溢出');
      await page.screenshot({ path: path.join(out, 'dashboard-' + name + '.png'), fullPage: true });
    }

    assert.deepEqual(errors, [], '页面不应有脚本错误');
    console.log(JSON.stringify({ passed: true, kpis: 7, bars, screenshots: out }));
  } finally {
    await browser.close();
    server.close();
  }
})().catch((error) => { console.error(error); process.exit(1); });