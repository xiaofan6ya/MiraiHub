const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'admin/console/index.html'), 'utf8');
const section = (start, end) => html.slice(html.indexOf(start), html.indexOf(end, html.indexOf(start)));
const scripts = [
  'const $ = s => document.querySelector(s); let TOKEN = "test-token"; const TOKEN_KEY="test-token";',
  'const refreshes = []; function loadAccounts() {refreshes.push("accounts")} function loadPlatformOverview() {refreshes.push("overview")}',
  section('let _localSessionPromise', '/* ==================== 运行配置'),
  section('async function safeJson(', '/**\n * 直接复制一段文本'),
  html.match(/function esc\(s\)\{[^\n]+/)[0],
  section('async function platformBeginAuth(', 'async function platformCheckin('),
].join('\n');
const styles = [...html.matchAll(/<style\b[^>]*>[\s\S]*?<\/style>/g)].map(m => m[0]).join('\n');
const links = [...html.matchAll(/<link[^>]+rel="stylesheet"[^>]*>/g)].map(m => m[0]).join('\n');
const shell = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">${styles}${links}</head><body>
<div id="modal" class="fixed inset-0 modal-overlay hidden items-center justify-center z-50 p-3">
<div id="modalBox" class="rounded-2xl w-full max-w-6xl p-6 max-h-[88vh] overflow-y-auto" style="background:var(--surface);border:1px solid var(--line)"></div></div>
<div id="toasts" class="toast flex flex-col gap-2"></div></body></html>`;

(async () => {
  const browser = await chromium.launch({headless: true});
  const page = await browser.newPage({viewport: {width: 1128, height: 800}});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  let serial = 0, mode = 'manual', expires = 600;
  let active = {id: 'trae-cn', storage: 'trae', realm: 'cn', label: 'Trae 国内版', auth: {manual_callback: true}};
  const sessions = new Map();
  const submissions = [], polls = [], claims = [];
  await page.route('**/*', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const json = (body, status=200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (/^\/api\/(?:[^/]+\/device|platforms\/[^/]+\/auth)\/start$/.test(url.pathname)) {
      const nonce = 'session-' + (++serial);
      sessions.set(nonce, {accepted: mode === 'automatic' || mode.startsWith('device'), acceptedPolls: 0, spec: {...active}});
      return json({nonce, expires_in: expires, auth_url: 'https://www.trae.cn/authorization?' + new URLSearchParams({
        login_version: '1', auth_from: 'trae', login_channel: 'native_ide', plugin_version: '2.3.98528',
        auth_type: 'local', client_id: 'ono9krqynydwx5', redirect: '0',
        auth_callback_url: 'http://127.0.0.1:8790/authorize', machine_id: 'a'.repeat(64), device_id: '1149420231460112',
        code_challenge: 'b'.repeat(64), code_challenge_method: 'S256', login_trace_id: nonce,
      })});
    }
    if (/^\/api\/platforms\/[^/]+\/auth\/callback$/.test(url.pathname)) {
      assert.equal(request.headers()['x-admin-token'], 'test-token');
      const body = request.postDataJSON();
      submissions.push({...body, path: url.pathname});
      assert.equal(url.pathname, '/api/platforms/' + sessions.get(body.nonce).spec.id + '/auth/callback');
      assert.equal(body.realm, sessions.get(body.nonce).spec.realm);
      if (!/[?&](code|ticket)=valid/.test(body.callback_url)) return json({detail: '回调缺少授权码，请复制授权后跳转页面的完整地址，而不是授权页链接'}, 400);
      sessions.get(body.nonce).accepted = true;
      return json({ok: true});
    }
    if (/^\/api\/(?:[^/]+\/device|platforms\/[^/]+\/auth)\/poll$/.test(url.pathname)) {
      const nonce = url.searchParams.get('nonce');
      polls.push(nonce);
      if (mode === 'failure') return json({detail: {error: '模拟上游拒绝'}}, 502);
      const session = sessions.get(nonce);
      assert.equal(url.pathname, session.spec.auth.lifecycle ? '/api/platforms/' + session.spec.id + '/auth/poll' : '/api/' + session.spec.storage + '/device/poll');
      if (mode === 'bind-phone' && session.accepted && !session.bound) return json({pending: true, stage: 'bind_phone', summary: '请绑定手机号'});
      if (mode === 'device-failure') return json({ok: false, error: '设备授权被拒绝'});
      if (mode.startsWith('device')) {
        session.acceptedPolls += 1;
        if (mode === 'device-slow' && session.acceptedPolls === 1) await new Promise(resolve => setTimeout(resolve, 12000));
        return json({pending: false, credentials: {nonce, token: 'mock-device-token'}});
      }
      if (mode === 'gateway-failure') return route.fulfill({status: 502, contentType: 'text/html', body: '<!DOCTYPE html><html>Cloudflare gateway error</html>'});
      if (session.accepted) {
        session.acceptedPolls += 1;
        if (mode === 'gateway-recovery' && session.acceptedPolls <= 2) {
          return route.fulfill({status: 502, contentType: 'text/html', body: '<!DOCTYPE html><html>Cloudflare gateway error</html>'});
        }
        if (mode === 'gateway-recovery' && session.acceptedPolls === 3) {
          return json({pending: true, stage: 'exchanging', summary: '回调已接收，正在向 Trae 换取登录凭证…'});
        }
        if (mode === 'hung-poll' && session.acceptedPolls === 1) {
          // A stalled response must be aborted by the actual UI's 10-second deadline.
          await new Promise(resolve => setTimeout(resolve, 15000));
          return json({pending: true}).catch(() => {});
        }
      }
      return json(session.accepted ? {ok: true, account_id: 1} : {pending: true});
    }
    if (/^\/api\/platforms\/loomy\/auth\/bind\/(send|verify)$/.test(url.pathname)) {
      const body = request.postDataJSON();
      assert.equal(body.phone, '13800138000');
      if (url.pathname.endsWith('/verify')) {assert.equal(body.code, '123456'); sessions.get(body.nonce).bound = true;}
      return json({ok: true});
    }
    if (/^\/api\/[^/]+\/device\/claim$/.test(url.pathname)) {
      claims.push({path: url.pathname, ...request.postDataJSON()});
      if (mode === 'device-claim-failure') return json({detail: '模拟保存失败'}, 400);
      return json({ok: true, name: '测试账号'});
    }
    const relative = url.pathname.startsWith('/admin/assets/')
      ? 'admin/console/' + url.pathname.slice('/admin/assets/'.length)
      : url.pathname.startsWith('/static/') ? 'admin/' + url.pathname.slice(1) : '';
    if (relative && fs.existsSync(path.join(root, relative))) return route.fulfill({path: path.join(root, relative)});
    if (url.pathname === '/') return route.fulfill({contentType:'text/html', body: shell});
    return route.fulfill({status: 204, body: ''});
  });
  await page.goto('http://callback-test.local/');
  await page.addScriptTag({content: scripts});
  const begin = async () => {
    await page.evaluate(spec => {void platformBeginAuth(spec, spec.storage)}, active);
    await page.locator('#authStatus').waitFor({state: 'visible'});
    assert.equal(await page.locator('#authCallbackUrl').count(), active.auth.manual_callback ? 1 : 0);
  };
  const status = page.locator('#authStatus');
  await begin();
  await page.waitForTimeout(350);
  await page.screenshot({path: path.join(__dirname, 'trae-callback-light.png')});
  await page.evaluate(() => document.documentElement.dataset.theme = 'dark');
  await page.waitForTimeout(350);
  console.log('dark field styles:', await page.locator('#authCallbackUrl').evaluate(el => ({
    background: getComputedStyle(el).backgroundColor, color: getComputedStyle(el).color,
    surface: getComputedStyle(el).getPropertyValue('--surface'),
  })));
  await page.screenshot({path: path.join(__dirname, 'trae-callback-dark.png')});
  await page.setViewportSize({width: 390, height: 844});
  await page.screenshot({path: path.join(__dirname, 'trae-callback-mobile.png')});
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.setViewportSize({width: 1128, height: 800});
  await page.evaluate(() => document.documentElement.dataset.theme = 'light');
  await page.locator('#authCallbackUrl').fill('https://www.trae.cn/authorization');
  await page.locator('#authCallbackSubmit').click();
  await page.waitForFunction(() => document.getElementById('authCallbackError').textContent.includes('回调缺少授权码'));
  assert.equal(await page.locator('#authCallbackSubmit').isEnabled(), true);
  await page.locator('#authCallbackUrl').fill('http://127.0.0.1:8790/authorize?code=valid&state=session-1');
  await page.locator('#authCallbackSubmit').click();
  await page.waitForFunction(() => document.getElementById('authStatus').textContent.includes('授权成功'));
  assert.equal(submissions.at(-1).nonce, 'session-1');
  assert.equal(await page.locator('#authCallbackUrl').inputValue(), '');
  await page.waitForFunction(() => document.getElementById('modal').classList.contains('hidden'));
  assert.deepEqual(await page.evaluate(() => refreshes), ['accounts', 'overview']);

  mode = 'automatic';
  await begin();
  await page.waitForFunction(() => document.getElementById('authStatus').textContent.includes('授权成功'));
  // Opening another dialog must not let the previous success timer close it.
  await page.evaluate(() => openModal('<p id="otherDialog">另一个弹窗</p>'));
  await page.waitForTimeout(1100);
  assert.equal(await page.locator('#otherDialog').isVisible(), true);

  mode = 'manual';
  await begin();
  const oldNonce = 'session-' + serial;
  await page.locator('.modal-close-button').click();
  await page.waitForTimeout(250);
  const oldCount = polls.filter(n => n === oldNonce).length;
  await begin();
  await page.waitForTimeout(2200);
  assert.equal(polls.filter(n => n === oldNonce).length, oldCount);
  assert.ok(polls.includes('session-' + serial));

  mode = 'failure';
  await page.waitForFunction(() => document.getElementById('authStatus').textContent.includes('模拟上游拒绝'));
  assert.equal(await page.locator('#authCallbackSubmit').isDisabled(), true);

  mode = 'gateway-recovery';
  await begin();
  const beforeRecovery = submissions.length;
  await page.locator('#authCallbackUrl').fill('http://127.0.0.1:8790/authorize?code=valid');
  await page.locator('#authCallbackSubmit').click();
  await page.waitForFunction(() => document.getElementById('authStatus').textContent.includes('HTTP 502'));
  assert.ok(!(await status.textContent()).includes('<!DOCTYPE'));
  await page.waitForFunction(() => document.getElementById('authStatus').textContent.includes('换取登录凭证'));
  assert.equal(await page.locator('#authCallbackSubmit').isDisabled(), true);
  await page.waitForFunction(() => document.getElementById('authStatus').textContent.includes('授权成功'));
  assert.equal(submissions.length, beforeRecovery + 1);

  mode = 'hung-poll';
  await begin();
  await page.locator('#authCallbackUrl').fill('http://127.0.0.1:8790/authorize?code=valid');
  await page.locator('#authCallbackSubmit').click();
  await page.waitForFunction(() => document.getElementById('authStatus').textContent.includes('查询超时'));
  await page.waitForFunction(() => document.getElementById('authStatus').textContent.includes('授权成功'));

  mode = 'gateway-failure';
  await begin();
  await page.waitForFunction(() => document.getElementById('authStatus').textContent.includes('授权失败'));
  assert.ok((await status.textContent()).includes('HTTP 502'));
  assert.ok(!(await status.textContent()).includes('DOCTYPE'));
  assert.ok(!(await status.textContent()).includes('Cloudflare'));

  mode = 'manual';
  active = {id: 'alpha-east', storage: 'alpha', realm: 'east', label: '独立回调平台', auth: {manual_callback: true}};
  await begin();
  assert.ok((await page.locator('#modalBox h3').textContent()).includes('独立回调平台'));
  await page.locator('#authCallbackUrl').fill('http://localhost/return?ticket=valid');
  await page.locator('#authCallbackSubmit').click();
  await page.waitForFunction(() => document.getElementById('authStatus').textContent.includes('授权成功'));
  assert.equal(submissions.at(-1).path, '/api/platforms/alpha-east/auth/callback');
  assert.equal(submissions.at(-1).realm, 'east');

  active = {id: 'qoder', storage: 'qoder', realm: 'cn', label: 'Qoder 中国版', auth: {manual_callback: false}};
  mode = 'device-slow';
  await begin();
  const deviceNonce = 'session-' + serial;
  await page.waitForFunction(() => document.getElementById('authStatus').textContent.includes('授权成功'));
  assert.equal(polls.filter(n => n === deviceNonce).length, 1);
  assert.equal(claims.at(-1).path, '/api/qoder/device/claim');
  assert.equal(claims.at(-1).credentials.nonce, deviceNonce);

  mode = 'device-failure';
  const claimsBeforeFailure = claims.length;
  await begin();
  await page.waitForFunction(() => document.getElementById('authStatus').textContent.includes('设备授权被拒绝'));
  assert.equal(claims.length, claimsBeforeFailure);

  mode = 'device-claim-failure';
  await begin();
  await page.waitForFunction(() => document.getElementById('authStatus').textContent.includes('模拟保存失败'));

  for (const [id, storage, realm] of [['trae-cn','trae','cn'],['trae-intl','trae','intl'],
      ['qoder','qoder','cn'],['qoder-intl','qoder','intl'],['workbuddy','workbuddy',''],['loomy','loomy','cn']]) {
    active = {id, storage, realm, label: id, auth: {manual_callback: true, lifecycle: true}};
    mode = id === 'loomy' ? 'bind-phone' : 'manual';
    await begin();
    await page.locator('#authCallbackUrl').fill('http://localhost/return?code=valid');
    await page.locator('#authCallbackSubmit').click();
    if (id === 'loomy') {
      await page.locator('#authBindPhone').waitFor({state: 'visible'});
      await page.locator('#authBindPhone').fill('13800138000');
      await page.locator('#authBindSend').click();
      await page.waitForFunction(() => document.getElementById('authBindError').textContent.includes('验证码已发送'));
      await page.locator('#authBindCode').fill('123456');
      await page.locator('#authBindVerify').click();
    }
    await page.waitForFunction(() => document.getElementById('authStatus').textContent.includes('授权成功'));
    assert.equal(submissions.at(-1).path, '/api/platforms/' + id + '/auth/callback');
  }

  active = {id: 'trae-cn', storage: 'trae', realm: 'cn', label: 'Trae 国内版', auth: {manual_callback: true}};
  mode = 'manual'; expires = 0.05;
  await begin();
  await page.waitForFunction(() => document.getElementById('authStatus').textContent.includes('已超时'));
  assert.equal(await page.locator('#authCallbackSubmit').isDisabled(), true);
  assert.deepEqual(errors, []);
  await browser.close();
  console.log('PASS: generic callback adapter UI, Qoder device polling/claim/failure, manual retry/import, automatic callback, modal isolation, closure, errors, expiry, gateway HTML recovery/failure, stalled polling timeout, desktop/mobile themes; no browser errors');
})().catch(error => { console.error(error); process.exit(1) });
