/* Release Markdown is rendered locally and sanitized before insertion. */
window.WBDesktop = (() => {
  let native = false, initialized = false, checking = false, releases = null, dialog, poll, startupPrompt = false, startupRequested = false, installRequested = false;
  const prompted = new Set();
  const versionButton = () => document.getElementById('appVersion');
  const side = document.getElementById('appSide');
  const footer = side.querySelector('.side-foot');
  const navigation = document.getElementById('sideNav');
  const compactSidebar = matchMedia('(max-width:900px)');
  function positionFooter() {
    if (compactSidebar.matches) {
      navigation.appendChild(footer);
    } else {
      side.appendChild(footer);
    }
  }
  compactSidebar.addEventListener('change', positionFooter);
  positionFooter();
  const element = (tag, text, className) => {
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = text;
    if (className) el.className = className;
    return el;
  };
  const button = (text, action, primary = false) => {
    const el = element('button', text, primary ? 'primary' : '');
    el.type = 'button'; el.addEventListener('click', action); return el;
  };
  const link = (text, url) => {
    const el = element('a', text);
    if (/^https:\/\/github\.com\/xiaofan6ya\/MiraiHub\/releases(?:\/|$)/.test(url)) {
      el.href = url; el.target = '_blank'; el.rel = 'noopener noreferrer';
    }
    return el;
  };
  function label() {
    if (!releases) return;
    versionButton().textContent = `v${releases.version} · ${releases.available ? '发现新版本' : '检查更新'}`;
    versionButton().classList.toggle('has-update', !!releases.available);
  }
  async function check(auto = false) {
    startupRequested ||= auto;
    if (checking) return;
    checking = true;
    try {
      if (native) releases = await pywebview.api.check_updates();
      else {
        const response = await api('/api/app/releases');
        if (!response.ok) throw new Error(`检查失败：${statusText(response.status)}`);
        releases = await response.json();
      }
      label();
      if (dialog?.open) render();
      if (startupRequested && !releases.startup_prompt_handled && releases.available && releases.latest) {
        const tag = releases.latest.tag;
        if (!prompted.has(tag)) {
          prompted.add(tag);
          showUpdates(true);
        }
      }
    } catch (error) {
      if (releases) releases.error = error.message;
      else releases = {version:'—', releases:[], error:error.message};
      if (dialog?.open) render();
    } finally { checking = false; startupRequested = false; }
  }
  function render() {
    dialog.replaceChildren();
    const header = element('div', undefined, 'wb-update-header');
    header.append(element('h2', startupPrompt ? '发现新版本' : '版本与更新'), button('关闭', () => dialog.close()));
    dialog.append(header, element('p', `当前版本 v${releases?.version || '…'} · 更新由你决定`, 'wb-update-meta'));
    if (releases?.error) dialog.append(element('p', releases.error, 'wb-update-meta'));
    const actions = element('div', undefined, 'wb-update-actions');
    actions.append(button('重新检查', () => check()), link('打开 Releases', 'https://github.com/xiaofan6ya/MiraiHub/releases'));
    const latest = releases?.latest;
    if (!native && latest?.asset) {
      actions.append(link(startupPrompt ? '立即更新' : window.WBMobile ? '下载最新版 APK' : '下载最新版 EXE', latest.asset.browser_download_url));
    }
    if (native && latest?.installable) {
      const download = button(startupPrompt ? '立即更新' : '下载最新版', async () => {
        download.disabled = true;
        try {
          installRequested = startupPrompt;
          const result = await pywebview.api.download_update(latest.id);
          if (result.error) { installRequested = false; status.textContent = result.error; }
          else watchDownload();
        } catch (error) { installRequested = false; status.textContent = error.message; }
        finally { download.disabled = false; }
      }, true);
      actions.append(download);
    } else if (latest && !latest.installable) {
      dialog.append(element('p', window.WBMobile ? '该版本尚未提供 Android APK 或校验文件，可在 Releases 查看。' : '该版本尚未提供 Windows EXE 或校验文件，可在 Releases 查看。', 'wb-update-meta'));
    }
    dialog.append(actions);
    if (startupPrompt && latest) {
      const defer = element('div', undefined, 'wb-update-actions');
      defer.append(button('暂不更新', () => dialog.close()));
      dialog.append(defer);
      dialog.append(element('p', native ? '立即更新将下载并校验安装包，然后重启客户端。' : '立即更新将打开安装包下载，安装由系统确认。', 'wb-update-meta'));
    }
    const status = element('p', releases?.update?.message || '', 'wb-update-meta');
    status.id = 'wbUpdateStatus'; dialog.append(status);
    const install = button('重启并安装更新', async () => {
      install.disabled = true;
      try {
        const result = await pywebview.api.apply_update();
        if (result.error) status.textContent = result.error;
      } catch (error) { status.textContent = error.message; }
      finally { install.disabled = false; }
    }, true);
    install.id = 'wbUpdateInstall'; install.hidden = !native || !releases?.update?.ready; dialog.append(install);
    if (latest) dialog.append(releaseNotes(latest, '最新版本'));
    const history = element('div', undefined, 'wb-update-history');
    history.append(element('h3', '历史版本'));
    for (const row of releases?.releases || []) {
      if (row.id !== latest?.id) history.append(releaseNotes(row));
    }
    if (!releases?.releases?.length) history.append(element('p', '暂无已发布版本。', 'wb-update-meta'));
    dialog.append(history);
    if (native) {
      const runtime = element('p', releases?.runtime || '运行环境正在准备…', 'wb-update-meta');
      runtime.id = 'wbRuntimeStatus'; dialog.append(runtime);
      dialog.append(button('重试准备运行环境', () => pywebview.api.retry_runtime()));
      watchDownload();
    }
  }
  function releaseNotes(row, heading) {
    const item = element('section', undefined, 'wb-update-release');
    const notes = element('div', undefined, 'wb-release-markdown');
    if (window.WBReleaseNotes) WBReleaseNotes.render(notes, row.body);
    else notes.textContent = row.body || '暂无更新说明';
    item.append(element('h3', `${heading ? heading + ' · ' : ''}${row.name || row.tag}`),
      element('p', `${row.tag} · ${row.date ? new Date(row.date).toLocaleString() : ''}`, 'wb-update-meta'),
      notes, link('查看此版本', row.url));
    return item;
  }
  function watchDownload() {
    clearInterval(poll);
    let polling = false;
    poll = setInterval(async () => {
      if (!dialog?.open) { clearInterval(poll); return; }
      if (polling) return;
      polling = true;
      try {
        const result = await pywebview.api.update_status();
        document.getElementById('wbUpdateStatus').textContent = result.message;
        document.getElementById('wbRuntimeStatus').textContent = result.runtime;
        document.getElementById('wbUpdateInstall').hidden = !result.ready;
        if (releases) releases.update = result;
        if (installRequested && result.ready) {
          installRequested = false;
          const applied = await pywebview.api.apply_update();
          if (applied.error) document.getElementById('wbUpdateStatus').textContent = applied.error;
        } else if (!result.busy && !result.ready) installRequested = false;
      } catch (_) {
        const status = document.getElementById('wbUpdateStatus');
        if (status) status.textContent = '更新状态暂不可用，请稍后重试';
      } finally { polling = false; }
    }, 1000);
  }
  function showUpdates(auto = false) {
    startupPrompt = auto;
    if (!dialog) {
      dialog = element('dialog', undefined, 'wb-update-dialog');
      dialog.setAttribute('aria-label', '版本与更新'); document.body.append(dialog);
      dialog.addEventListener('click', e => { if (e.target === dialog) dialog.close(); });
      dialog.addEventListener('close', () => { installRequested = false; clearInterval(poll); });
    }
    render(); if (!dialog.open) dialog.showModal();
  }
  async function openUpdates() {
    showUpdates();
    await check();
  }
  async function initializeNative() {
    // pywebview 的注入分两步：先把 `window.pywebview.api` 对象挂上来，再异步
    // 把方法写进去、并发 `pywebviewready`。macOS（WKWebView）上这两步之间的
    // 空窗比 Windows 明显，页面若在空窗里调用就会拿到「api 存在但
    // desktop_login 不是函数」。所以拿方法名当就绪判据，而不是拿对象当。
    if (typeof window.pywebview?.api?.desktop_login !== 'function') return;
    if (initialized) return;
    initialized = true; native = true;
    document.documentElement.classList.add('native-window');
    // A refreshed native tab may keep its selected panel in the query string.
    // Reassert the desktop marker before asking the bridge for its shell token.
    try {
      const url = new URL(location.href);
      if (url.pathname === '/admin' || url.pathname === '/admin/') {
        url.searchParams.set('desktop', '1');
        history.replaceState(null, '', url.pathname + '?' + url.searchParams.toString() + url.hash);
        document.documentElement.classList.add('desktop-mode');
      }
    } catch (_) {}
    const controls = element('div', undefined, 'window-controls');
    for (const [action, text, label] of [['minimize','−','最小化'], ['maximize','□','最大化或还原'], ['close','×','关闭窗口']]) {
      const control = button(text, () => pywebview.api.window_action(action));
      control.setAttribute('aria-label', label);
      controls.append(control);
    }
    if (!document.querySelector('.window-controls')) document.querySelector('.topbar-actions').append(controls);
    if (!document.querySelector('.window-resize')) for (const edge of ['top','right','bottom','left','top-left','top-right','bottom-left','bottom-right']) {
      const handle = element('div', undefined, `window-resize resize-${edge}`);
      handle.setAttribute('aria-hidden', 'true');
      handle.addEventListener('mousedown', event => { if (event.button === 0) { event.preventDefault(); pywebview.api.window_action(`resize-${edge}`); } });
      document.body.append(handle);
    }
    try {
      await ensureLocalSession();
      // Existing persisted token may already have entered the application.
      if (document.getElementById('app').classList.contains('hidden')) enterApp();
      document.getElementById('localBoot')?.remove();
      document.getElementById('topbarMode').textContent = '本地运行';
    } catch (error) {
      const message = String(error && error.message || error);
      document.getElementById('localBootStatus').textContent =
        /is not a function|undefined/.test(message)
          ? `本地桥接尚未就绪（${message}），点「重新连接」重试`
          : `本地连接失败：${message}`;
      initialized = false;
    }
    check(true);
  }
  fetch('/api/app/version').then(r => r.json()).then(data => {
    if (!releases) { releases = {...data, releases:[]}; label(); }
  }).catch(() => {});
  window.addEventListener('pywebviewready', initializeNative);
  document.getElementById('sideNav').addEventListener('click', event => {
    const item = event.target.closest('[data-tab]');
    if (item) document.getElementById('topbarTitle').textContent = [...item.childNodes]
      .filter(node => node.nodeType === Node.TEXT_NODE).map(node => node.textContent).join('').trim();
  });
  if (typeof window.pywebview?.api?.desktop_login === 'function') initializeNative();
  // 兜底轮询：万一 pywebviewready 因为导航时序丢了，方法一到位就补上（最多等 10s）
  let bridgeTries = 0;
  const bridgeTimer = setInterval(() => {
    if (typeof window.pywebview?.api?.desktop_login === 'function') {
      clearInterval(bridgeTimer);
      initializeNative();
    } else if (++bridgeTries > 40) {
      clearInterval(bridgeTimer);
    }
  }, 250);
  return {openUpdates, retryLocal: initializeNative, checkUpdatesAtStartup: () => check(true)};
})();
