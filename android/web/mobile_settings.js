// Incremental SSE parser: network chunks may split both UTF-8 and JSON lines.
class MobileStreamMeter {
  constructor(started) {
    this.started = started; this.buffer = ''; this.lines = [];
    this.firstText = null; this.sample = ''; this.tokens = null;
    this.done = false; this.error = '';
  }
  feed(text, now) {
    this.buffer += text;
    let pos;
    while ((pos = this.buffer.indexOf('\n')) >= 0) {
      const line = this.buffer.slice(0, pos).replace(/\r$/, '');
      this.buffer = this.buffer.slice(pos + 1);
      if (!line) { this.event(now); this.lines = []; }
      else if (line.startsWith('data:')) this.lines.push(line.slice(5).trimStart());
    }
  }
  event(now) {
    if (!this.lines.length) return;
    const data = this.lines.join('\n');
    if (data.trim() === '[DONE]') { this.done = true; return; }
    let obj;
    try { obj = JSON.parse(data); } catch (_) { this.error = '流中出现无法解析的 SSE 数据'; return; }
    if (obj.error || obj.type === 'error') this.error = '接口返回流式错误，请查看调用诊断';
    let text = '';
    for (const choice of obj.choices || []) {
      const delta = choice.delta || {};
      text += (typeof delta.content === 'string' ? delta.content : '') +
              (typeof delta.reasoning_content === 'string' ? delta.reasoning_content : '');
    }
    if (['response.output_text.delta', 'response.reasoning_text.delta', 'response.reasoning_summary_text.delta'].includes(obj.type))
      text += typeof obj.delta === 'string' ? obj.delta : '';
    if (obj.type === 'content_block_delta') text += obj.delta?.text || obj.delta?.thinking || '';
    if (text) {
      if (this.firstText == null) this.firstText = now - this.started;
      this.sample = (this.sample + text).slice(0, 2000);
    }
    const usage = obj.usage || obj.response?.usage;
    if (usage) this.tokens = usage.completion_tokens ?? usage.output_tokens ?? this.tokens;
    if (['response.completed', 'message_stop'].includes(obj.type)) this.done = true;
    if (['response.failed', 'response.incomplete'].includes(obj.type)) this.error = '模型响应未正常完成';
  }
  end(now) {
    // Accept a final complete data line without the trailing blank separator.
    this.feed('\n\n', now);
  }
}

window.WBMobileSettings = (() => {
  const el = id => document.getElementById(id);
  let started = false, checking = false, lastCheck = 0, speedController = null;
  const duration = ms => ms == null ? '—' : ms < 1000 ? `${ms.toFixed(1)} ms` : `${(ms / 1000).toFixed(2)} 秒`;
  function systemStatus() {
    let state = {};
    try { state = JSON.parse(window.WBAndroid?.status() || '{}'); } catch (_) { }
    const title = state.starting ? '正在启动 / 恢复' : state.registered ? '系统前台服务已注册' : '系统服务未注册';
    el('mobileServiceState').innerHTML = `<p><b>${esc(title)}</b></p><p class="mobile-service-address">${esc(state.origin || location.origin)}/v1</p>
      <p>运行通知：${state.notifications ? '已允许' : '未允许或通知通道关闭'}</p>
      <p>电池优化：${state.battery_unrestricted ? '不受限制' : '尚未授权后台高耗电'}</p>
      <p>后台运行：${state.background_restricted ? '被系统限制' : '未被限制'} · CPU 保活：${state.wake_lock ? '已启用' : '未启用'}${state.power_save ? ' · 省电模式已开启' : ''}</p>
      ${state.last_healthy ? `<p>最近监听检查：${esc(new Date(state.last_healthy).toLocaleTimeString())}</p>` : ''}
      ${state.error ? `<p class="mobile-check-failed">${esc(state.error)}</p>` : ''}`;
  }
  async function request(path, options = {}, admin = true, timeout = 12000) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    const begin = performance.now();
    try {
      const response = await fetch(path, {...options, cache:'no-store', signal:controller.signal,
        headers:{...(admin ? {'X-Admin-Token':TOKEN} : {}), ...options.headers}});
      const text = await response.text();
      let data;
      try { data = JSON.parse(text); } catch (_) { data = text; }
      return {status:response.status, ok:response.ok, data, ms:performance.now() - begin};
    } finally { clearTimeout(timer); }
  }
  async function keyValue() {
    const id = el('mobileTestKey').value;
    if (!id) throw new Error('请先在 API Keys 页面创建一个启用的 Key');
    const result = await request(`/api/keys/${encodeURIComponent(id)}/view`);
    if (!result.ok || !result.data.key) throw new Error('此 Key 不可用，请重新选择');
    return result.data.key;
  }
  function resultRow(name, ok, detail, ms) {
    const row = document.createElement('div');
    row.className = `mobile-check-row ${ok ? 'mobile-check-ok' : 'mobile-check-failed'}`;
    row.innerHTML = `<div><b>${esc(name)}</b><small>${esc(detail)}</small></div><span>${duration(ms)}</span>`;
    el('mobileCheckResults').append(row);
  }
  async function check(force = false) {
    systemStatus();
    window.WBProfileTransfer?.status();
    if (checking || speedController || (!force && Date.now() - lastCheck < 30000)) return;
    checking = true;
    el('mobileCheck').disabled = true; el('mobileTestKey').disabled = true;
    el('mobileCheckResults').replaceChildren();
    try {
      const probes = [
        ['本机 HTTP 监听', '/api/app/version', 200, true, undefined, data => !!data?.version],
        ['账号管理 / 数据库', '/api/accounts', 200, true, undefined, data => Array.isArray(data?.items) && data.items.some(a => a.status === 'active')],
        ['本地模型配置', '/api/models/configs', 200, true, undefined, data => Array.isArray(data?.items) && data.items.some(m => m.enabled && m.model_id)],
        ['手机号授权页面', '/join', 200, false],
        ['Chat 入口鉴权', '/v1/chat/completions', 401, false, 'POST'],
        ['Responses 入口鉴权', '/v1/responses', 401, false, 'POST'],
        ['Messages 入口鉴权', '/v1/messages', 401, false, 'POST'],
      ];
      for (const [name, path, expected, admin, method, validate] of probes) {
        try {
          const r = await request(path, method ? {method, headers:{'Content-Type':'application/json'}, body:'{}'} : {}, admin);
          const ok = r.status === expected && (!validate || validate(r.data));
          const detail = path === '/api/accounts' && Array.isArray(r.data?.items) ? ` · ${r.data.items.length} 个账号${ok ? '' : '，没有启用的账号'}` :
            path === '/api/models/configs' && Array.isArray(r.data?.items) ? ` · ${r.data.items.filter(m=>m.enabled).length} 个启用模型${ok ? '' : '，请等待初始化或同步模型'}` : ok ? '' : ' · 未通过';
          resultRow(name, ok, expected === 401 && ok ? '正确拒绝无 Key 请求 · 尚未验证模型推理' : `HTTP ${r.status}${detail}`, r.ms);
        } catch (_) { resultRow(name, false, '连接失败或 12 秒内未完成', null); }
      }
      const keys = await request('/api/keys');
      if (!keys.ok) throw new Error('读取 Key 列表失败');
      const selectedKey = el('mobileTestKey').value;
      const active = (keys.data.items || []).filter(row => row.status === 'active');
      el('mobileTestKey').innerHTML = active.map(row => `<option value="${Number(row.id)}">${esc(row.name || '未命名')} · ${esc(row.masked || '')}</option>`).join('') || '<option value="">尚无可用 Key</option>';
      if (active.some(row => String(row.id) === selectedKey)) el('mobileTestKey').value = selectedKey;
      const oldModel = el('mobileTestModel').value;
      el('mobileTestModel').innerHTML = '<option value="">无可用模型</option>';
      if (active.length) {
        const key = await keyValue();
        const r = await request('/v1/models', {headers:{Authorization:`Bearer ${key}`}}, false);
        const models = r.ok && Array.isArray(r.data?.data) ? r.data.data : [];
        resultRow('真实 Key 模型列表', r.ok && models.length > 0, `HTTP ${r.status} · ${models.length} 个可用模型`, r.ms);
        el('mobileTestModel').innerHTML = models.map(row => `<option value="${esc(row.id)}">${esc(row.id)}</option>`).join('') || '<option value="">无可用模型</option>';
        if (models.some(row => row.id === oldModel)) el('mobileTestModel').value = oldModel;
      } else resultRow('真实 Key 模型列表', false, '请先创建 API Key，再重新检查', null);
    } catch (error) { resultRow('检查未完成', false, error.name === 'AbortError' ? '检查超时' : error.message, null); }
    finally { checking = false; lastCheck = Date.now(); el('mobileCheck').disabled = false; el('mobileTestKey').disabled = false; }
  }
  async function speedTest() {
    if (speedController || checking) return;
    const model = el('mobileTestModel').value;
    if (!model) { toast('请先选择一个可用模型', 'err'); return; }
    speedController = new AbortController();
    const controller = speedController;
    let cancelled = false, expired = false, reader;
    el('mobileSpeedCancel').onclick = () => { cancelled = true; controller.abort(); };
    el('mobileSpeedCancel').hidden = false;
    el('mobileSpeedTest').disabled = true; el('mobileCheck').disabled = true;
    el('mobileSpeedResult').textContent = '正在准备测速…';
    let timer, begin, meter, headerMs = null, byteMs = null, status = null;
    const paint = (phase, total) => {
      el('mobileSpeedResult').innerHTML = `<p><b>${esc(phase)}</b>${status ? ` · HTTP ${status}` : ''}</p>
        <div class="mobile-speed-metrics"><span>响应头<b>${duration(headerMs)}</b></span><span>传输首字节<b>${duration(byteMs)}</b></span>
        <span>有效文字首字<b>${duration(meter?.firstText)}</b></span><span>总耗时<b>${duration(total)}</b></span>
        ${meter?.tokens != null ? `<span>输出 token<b>${Number(meter.tokens)}</b></span><span>生成速度<b>${meter.firstText != null && total > meter.firstText ? (meter.tokens / ((total - meter.firstText) / 1000)).toFixed(1) + ' t/s' : '—'}</b></span>` : ''}</div>
        ${meter?.sample ? `<pre class="mobile-speed-sample">${esc(meter.sample)}</pre>` : ''}`;
    };
    try {
      const key = await keyValue();
      if (controller.signal.aborted) throw new DOMException('Aborted', 'AbortError');
      const protocol = el('mobileTestProtocol').value;
      const path = {chat:'/v1/chat/completions', responses:'/v1/responses', messages:'/v1/messages'}[protocol];
      const body = protocol === 'responses' ? {model, stream:true, input:'请用两句话介绍你自己。', max_output_tokens:128} :
        {model, stream:true, messages:[{role:'user', content:'请用两句话介绍你自己。'}], max_tokens:128,
          ...(protocol === 'chat' ? {stream_options:{include_usage:true}} : {})};
      begin = performance.now(); meter = new MobileStreamMeter(begin);
      timer = setTimeout(() => { expired = true; controller.abort(); }, 60000);
      paint('等待接口响应', 0);
      const response = await fetch(path, {method:'POST', cache:'no-store', signal:controller.signal,
        headers:{Authorization:`Bearer ${key}`, 'X-API-Key':key, 'anthropic-version':'2023-06-01', 'Content-Type':'application/json'}, body:JSON.stringify(body)});
      headerMs = performance.now() - begin; status = response.status;
      if (!response.ok) throw new Error(`接口返回 HTTP ${status}，请查看调用诊断与所选 Key 的模型权限`);
      if (!(response.headers.get('Content-Type') || '').includes('text/event-stream')) throw new Error('接口未返回 SSE 流');
      reader = response.body.getReader(); const decoder = new TextDecoder();
      while (!meter.done && !meter.error) {
        const item = await reader.read(); const now = performance.now();
        if (item.done) { meter.feed(decoder.decode(), now); meter.end(now); break; }
        if (byteMs == null && item.value.length) byteMs = now - begin;
        meter.feed(decoder.decode(item.value, {stream:true}), now);
        paint(meter.firstText == null ? '已连接，等待有效文字' : '正在接收回答', now - begin);
      }
      if (meter.error) throw new Error(meter.error);
      if (!meter.done) throw new Error('连接已关闭，但未收到流结束标记');
      if (meter.firstText == null) throw new Error('流已结束，但没有返回有效文字');
      paint('测速成功 · 流完整结束', performance.now() - begin);
    } catch (error) {
      paint(cancelled ? '已停止测速' : expired ? '测速超时（60 秒）' : `测速失败：${error.name === 'AbortError' ? '连接中断' : error.message}`, begin ? performance.now() - begin : 0);
    } finally {
      clearTimeout(timer);
      if (reader) { try { await reader.cancel(); } catch (_) { } reader.releaseLock(); }
      speedController = null; el('mobileSpeedCancel').hidden = true;
      el('mobileSpeedTest').disabled = false; el('mobileCheck').disabled = false;
    }
  }
  const activate = activateTab;
  activateTab = function(name, options) { activate(name, options); if (name === 'sync' && started) check(); };
  function start() {
    if (started) return;
    started = true;
    el('mobileCheck').onclick = () => check(true);
    el('mobileTestKey').onchange = () => check(true);
    el('mobileSpeedTest').onclick = speedTest;
    el('mobileRestart').onclick = async () => {
      if (speedController || checking) { toast('请等待检查结束或停止测速', 'err'); return; }
      if (await uiConfirm({title:'重启本地服务', message:'当前 API 对话会断开，账号和设置将保留。', okText:'重启'})) window.WBAndroid?.restartService();
    };
    el('mobileNotifications').onclick = () => window.WBAndroid?.openSystemSettings('notifications');
    el('mobileBattery').onclick = () => window.WBAndroid?.openSystemSettings('battery');
    window.addEventListener('wb-shell-resume', () => {
      if (!document.hidden && document.documentElement.dataset.activeTab === 'sync') check();
    });
    setInterval(() => { if (!document.hidden && document.documentElement.dataset.activeTab === 'sync') systemStatus(); }, 5000);
    check(true);
  }
  return {start, check};
})();
