// Offline browser regression: click the rendered controls rather than calling
// their handlers directly. Every API request is mocked; no credentials are read.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('C:/Users/Administrator/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root = path.resolve(__dirname, '..');
const platforms = [
  ['workbuddy', 'WorkBuddy', 'workbuddy', '', ['scan', 'import', 'refresh-quota', 'usage']],
  ['trae-cn', 'Trae 国内版', 'trae', 'cn', ['begin-auth', 'scan', 'import', 'refresh-quota', 'web-cookie']],
  ['trae-intl', 'Trae 国际版', 'trae', 'intl', ['begin-auth', 'scan', 'import', 'refresh-quota', 'web-cookie']],
  ['qoder', 'Qoder 中国版', 'qoder', 'cn', ['begin-auth', 'refresh-quota']],
  ['qoder-intl', 'Qoder 国际版', 'qoder', 'intl', ['begin-auth', 'refresh-quota']],
  ['loomy', 'Loomy', 'loomy', 'cn', ['scan', 'import', 'refresh-quota']],
].map(([id, label, storage_platform, realm, actions]) => ({
  id, label, storage_platform, realm, actions, realms: [], account_count: 0,
  supports_local_scan: storage_platform !== 'qoder',
}));

async function run() {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [], calls = [];
    let failScan = false;
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => localStorage.setItem('wb_admin_token', 'test-token'));
    await page.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url()), pathname = url.pathname;
      if (pathname.startsWith('/api/')) {
        calls.push({ pathname, query: url.searchParams.toString(), method: request.method(),
          platform: request.headers()['x-platform'] });
        if (pathname.endsWith('/scan-local') && failScan) {
          failScan = false;
          return route.abort('failed');
        }
        let data = { items: [], models: [], fields: [], summary: { total: 0, active: 0, available: 0 }, ok: true };
        if (pathname === '/api/platforms') data = { items: platforms };
        if (pathname.endsWith('/device/start')) data = { auth_url: 'https://example.test/authorize', nonce: 'test-session' };
        if (pathname.endsWith('/device/poll')) data = { pending: true };
        if (pathname.endsWith('/meta')) data = { auth_mode: 'local-session-only' };
        if (pathname.endsWith('/scan-local')) {
          data = { exists: true, items: [], errors: [], pool_only_sample: [] };
          if (pathname.includes('/trae/')) data.items = [{ realm: url.searchParams.get('realm'), name: 'Demo', uid: 'demo', has_refresh_token: true }];
          if (pathname.includes('/loomy/')) data.items = [{ has_plain_credential: true, uid: 'demo', source: 'demo.json' }];
        }
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(data) });
      }
      let file;
      if (pathname === '/admin') file = path.join(root, 'admin/console/index.html');
      else if (pathname.startsWith('/admin/assets/')) file = path.join(root, 'admin/console', pathname.slice(14));
      else if (pathname.startsWith('/static/')) file = path.join(root, 'admin/static', pathname.slice(8));
      if (!file || !fs.existsSync(file)) return route.fulfill({ status: 404, body: '' });
      const mime = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.svg': 'image/svg+xml' };
      return route.fulfill({ status: 200, contentType: mime[path.extname(file)] || 'application/octet-stream', body: fs.readFileSync(file) });
    });
    await page.goto('http://wb2api.test/admin?tab=platforms');
    await page.locator('#platformCards [data-platform="workbuddy"]').waitFor({ state: 'visible' });

    async function closeModal() { await page.evaluate(() => window.closeModal()); }
    async function assertHandlers() {
      const invalid = await page.evaluate(() => [...document.querySelectorAll('[onclick],[onchange],[oninput]')].flatMap(el =>
        ['onclick', 'onchange', 'oninput'].filter(name => el.hasAttribute(name)).flatMap(name => {
          try { new Function('event', el.getAttribute(name)); return []; }
          catch (e) { return [el.outerHTML.slice(0, 200) + ': ' + e.message]; }
        })));
      assert.deepEqual(invalid, [], 'Rendered event attributes must parse');
    }
    async function clickAndRequest(selector, pathname, query, platform) {
      const before = calls.length;
      await selector.click();
      await page.waitForFunction(() => !document.getElementById('globalLoadBar').classList.contains('on'));
      await page.waitForTimeout(150);
      const matching = calls.slice(before).filter(call => call.pathname === pathname);
      assert.equal(matching.length, 1, `One request per click: ${pathname}`);
      assert.equal(matching[0].query, query);
      assert.equal(matching[0].platform, platform);
      await assertHandlers();
    }

    // Both entry points, every platform, switching away from and back to WB.
    for (const spec of [...platforms, platforms[0]]) {
      console.log('Checking ' + spec.id);
      await page.locator('#platformCards [data-platform="' + spec.id + '"]').click();
      const toolbar = page.locator('#platformActions');
      if (spec.actions.includes('scan')) {
        const prefix = spec.id === 'workbuddy' ? '/api/accounts' : '/api/' + spec.storage_platform;
        const query = spec.realm ? 'realm=' + spec.realm : '';
        await clickAndRequest(toolbar.locator('[data-platform-action="scan"]'), prefix + '/scan-local', query, spec.id);
        await closeModal();
      }
      if (spec.actions.includes('begin-auth')) {
        await clickAndRequest(toolbar.locator('[data-platform-action="begin-auth"]'), '/api/' + spec.storage_platform + '/device/start', 'realm=' + spec.realm, spec.id);
        assert.match(await page.locator('#modalBox').innerText(), /授权登录/);
        await closeModal();
      }
      await page.locator('#sideNav [data-tab="accounts"]').click();
      if (spec.id === 'workbuddy') {
        assert.equal(await page.locator('#intakeWbActions').isVisible(), true);
        assert.equal(await page.locator('#intakePlatformLogin').isVisible(), false);
      } else {
        assert.equal(await page.locator('#intakeWbActions').isVisible(), false);
        const intake = page.locator('#intakePlatformLogin');
        const action = spec.actions.includes('scan') ? 'scan' : 'begin-auth';
        const suffix = action === 'scan' ? '/scan-local' : '/device/start';
        await clickAndRequest(intake.locator('[data-platform-action="' + action + '"]'), '/api/' + spec.storage_platform + suffix, 'realm=' + spec.realm, spec.id);
        await closeModal();
      }
      await page.locator('#sideNav [data-tab="platforms"]').click();
    }

    // Repeated enter/render must not double-bind the action toolbar.
    await page.evaluate(async () => { await WBPlatform.enter(); await WBPlatform.enter(); });
    await clickAndRequest(page.locator('#platformActions [data-platform-action="scan"]'), '/api/accounts/scan-local', '', 'workbuddy');
    await closeModal();

    // The import button in an existing scan dialog retains the scanned platform
    // even if the global selection changes before the user confirms.
    await page.locator('#platformCards [data-platform="trae-intl"]').click();
    await clickAndRequest(page.locator('#platformActions [data-platform-action="scan"]'), '/api/trae/scan-local', 'realm=intl', 'trae-intl');
    await page.evaluate(() => WBPlatform.select('loomy'));
    await clickAndRequest(page.locator('#traeScanImport'), '/api/trae/import-local', 'realm=intl', 'trae-intl');
    await closeModal();
    await page.locator('#platformCards [data-platform="trae-cn"]').click();
    await page.locator('#platformActions [data-platform-action="web-cookie"]').click();
    await assertHandlers();
    await page.locator('#webCookieInput').fill('demo=value');
    await clickAndRequest(page.locator('#modalBox button').filter({ hasText: '保存' }), '/api/trae/web-cookie', '', 'trae-cn');
    await closeModal();

    // Stale WB buttons dispatch to the selected platform, and async failures
    // produce a visible error instead of an unhandled promise rejection.
    await page.locator('#platformCards [data-platform="loomy"]').click();
    const before = calls.length;
    await page.evaluate(() => scanLocal());
    assert.equal(calls.slice(before).some(call => call.pathname === '/api/accounts/scan-local'), false);
    assert.equal(calls.slice(before).some(call => call.pathname === '/api/loomy/scan-local'), true);
    await closeModal();
    failScan = true;
    await page.locator('#platformActions [data-platform-action="scan"]').click();
    await page.getByText(/操作失败：/).waitFor({ state: 'visible' });
    assert.deepEqual(errors, []);
    console.log('Platform actions: both toolbars, six platforms, realm/header isolation, repeat binding, stale WB action and network failure passed.');
  } finally { await browser.close(); }
}
run().catch(error => { console.error(error); process.exitCode = 1; });
