/* ============================================================================
   智能体（AI 创作台）前端
   ----------------------------------------------------------------------------
   与后端的契约（`/api/agent/*`）：
     GET   bootstrap                → 模型能力、预设、预估系数、merge 可用性
     POST  chat                     → SSE：meta / reasoning / delta / tool_start /
                                      tool_end / media / notice / done / error
     POST  enhance / estimate / plan / merge
     GET   assets[/id]
     GET   conversations[/id]       → 列表 / 详情（含上下文用量）
     PATCH conversations/{id}       → 重命名、置顶、模型、思考设置
     DELETE conversations/{id}

   这一版按反馈重做的点：
   1. **模型与思考控制移到顶栏**，底部只留「模式 + 增强 + 发送」。
   2. **所有选择都持久化**（localStorage + 会话表）：刷新后模型、模式、
      思考档位、是否展示思考、当前会话都回到原样。
   3. **思考内容落库**：刷新后思考块仍在，不再丢失。
   4. **顶栏显示上下文用量**（与服务端裁剪口径一致）。
   5. **会话支持重命名 / 置顶 / 删除**。
   ========================================================================== */
window.WBAgent = (() => {
  const $ = id => document.getElementById(id);
  const BASE = '/api/agent';
  const PREF_KEY = 'wb_agent_prefs';

  const S = {
    ready: false,
    boot: null,
    conversationId: '',
    mode: 'chat',
    model: '',
    reasoning: '',
    thinking: null,
    image: {model: '', size: '1024x1024'},
    video: {model: '', resolution: '', seconds: 5, aspect: ''},
    long: {total: 45},
    sending: false,
    abort: null,
    selected: new Set(),
    assets: [],
    conversations: [],
    context: null,
    estimateTimer: null,
    lastPlan: [],
  };

  // ------------------------------------------------------- 偏好持久化
  function loadPrefs() {
    try {
      const raw = JSON.parse(localStorage.getItem(PREF_KEY) || '{}');
      if (!raw || typeof raw !== 'object') return;
      if (typeof raw.mode === 'string') S.mode = raw.mode;
      if (typeof raw.model === 'string') S.model = raw.model;
      if (typeof raw.reasoning === 'string') S.reasoning = raw.reasoning;
      if (raw.thinking === true || raw.thinking === null) S.thinking = raw.thinking;
      if (raw.image && typeof raw.image === 'object') Object.assign(S.image, raw.image);
      if (raw.video && typeof raw.video === 'object') Object.assign(S.video, raw.video);
      if (raw.long && typeof raw.long === 'object') Object.assign(S.long, raw.long);
      if (typeof raw.conversationId === 'string') S.conversationId = raw.conversationId;
    } catch (_) { /* 存储损坏就当没有偏好 */ }
  }

  function savePrefs() {
    try {
      localStorage.setItem(PREF_KEY, JSON.stringify({
        mode: S.mode, model: S.model, reasoning: S.reasoning,
        thinking: S.thinking, image: S.image, video: S.video, long: S.long,
        conversationId: S.conversationId,
      }));
    } catch (_) { /* 隐私模式下写入失败，忽略 */ }
  }

  // ------------------------------------------------------------------ 工具
  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  const fmtCredits = value => {
    const n = Number(value);
    if (!Number.isFinite(n)) return '—';
    return n >= 100 ? n.toFixed(0) : n.toFixed(2).replace(/\.?0+$/, '');
  };
  const fmtTime = value => {
    if (!value) return '';
    const date = new Date(value.endsWith?.('Z') ? value : value + 'Z');
    if (Number.isNaN(date.getTime())) return '';
    const diff = (Date.now() - date.getTime()) / 60000;
    if (diff < 1) return '刚刚';
    if (diff < 60) return Math.floor(diff) + ' 分钟前';
    if (diff < 1440) return Math.floor(diff / 60) + ' 小时前';
    return `${date.getMonth() + 1}/${date.getDate()}`;
  };
  const fmtTokens = value => {
    const n = Number(value) || 0;
    if (n >= 1000) return (n / 1000).toFixed(n >= 10000 ? 0 : 1) + 'K';
    return String(n);
  };

  /** 极简 Markdown：代码块、行内代码、粗体、列表、换行。 */
  function renderMarkdown(text) {
    const raw = String(text ?? '');
    if (!raw) return '';
    const blocks = [];
    let body = raw.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) => {
      blocks.push(`<pre><code>${esc(code.replace(/\n$/, ''))}</code></pre>`);
      return `\u0000B${blocks.length - 1}\u0000`;
    });
    body = esc(body)
      .replace(/`([^`\n]+)`/g, '<code>$1</code>')
      .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
      .replace(/^(\s*)[-*]\s+(.+)$/gm, '$1• $2')
      .replace(/\n{2,}/g, '</p><p>')
      .replace(/\n/g, '<br>');
    body = `<p>${body}</p>`;
    return body.replace(/\u0000B(\d+)\u0000/g, (_, index) => blocks[Number(index)] || '');
  }

  async function api(path, options = {}) {
    const response = await fetch(BASE + path, {
      ...options,
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
      credentials: 'same-origin',
    });
    if (!response.ok) {
      let detail = `HTTP ${response.status}`;
      try {
        const data = await response.json();
        detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || data);
      } catch (_) { /* 非 JSON */ }
      throw new Error(detail);
    }
    return response.json();
  }

  const toast = (message, kind = 'ok') =>
    (window.toast ? window.toast(message, kind) : console.log(message));

  /** 系统下拉（复用 ui-select），紧凑样式。
   *
   * `onChange` 必须交给组件自己的 `options.onChange`：组件内部
   * `uiSelectPick()` 会 `ev.stopPropagation()`，所以在外层监听 click 永远
   * 收不到选择事件（实测踩过：下拉显示已切换，但状态与落库都是旧值）。
   */
  function selectHTML(id, options, value, placeholder = '请选择', onChange = null) {
    if (typeof window.uiSelectHTML !== 'function') {
      return `<select id="${id}" class="agent-native-select"
        style="height:28px;border-radius:7px;border:1px solid var(--border);font-size:11px;padding:0 6px;background:var(--surface);color:var(--fg)">
        ${options.map(o => `<option value="${esc(o.value)}" ${String(o.value) === String(value) ? 'selected' : ''}>${esc(o.label)}</option>`).join('')}
      </select>`;
    }
    return window.uiSelectHTML(id, {
      options, value, placeholder, class: 'agent-select-btn',
      // 包一层：组件会把 (value, option) 传进来，这里统一成只给 value。
      onChange: onChange ? (next) => onChange(next) : null,
    });
  }

  /** 给原生 select 兜底绑定（组件不可用时才生效）。 */
  function bindSelect(id, onChange) {
    if (typeof window.initUISelects === 'function') {
      try { window.initUISelects([[id, '选择']]); } catch (_) { /* 组件兜底 */ }
    }
    const host = $(id);
    if (!host || host.tagName !== 'SELECT') return;
    host.addEventListener('change', event => onChange(event.target.value));
  }

  // ------------------------------------------------------------ 初始化
  let bound = false;

  async function enter() {
    if (!$('agentStream')) return;
    if (!bound) { bind(); bound = true; }
    if (S.ready) { refreshAssets(); loadConversations(); return; }
    loadPrefs();
    try {
      S.boot = await api('/bootstrap');
    } catch (error) {
      $('agentStream').innerHTML = `<div class="agent-empty"><h3>无法连接智能体</h3>
        <p>${esc(error.message)}</p></div>`;
      return;
    }
    S.ready = true;
    const models = S.boot.models || {};
    // 偏好里的模型必须仍然可用，否则回落到第一个。
    if (!(models.chat || []).includes(S.model)) S.model = (models.chat || [])[0] || '';
    if (!(models.image || []).includes(S.image.model)) S.image.model = (models.image || [])[0] || '';
    const rows = S.boot.video || [];
    if (!rows.some(row => row.id === S.video.model)) S.video.model = rows[0]?.id || '';
    if (!S.video.resolution) applyVideoDefaults(rows[0] || {});
    renderMode();
    renderTopSelectors();
    renderAside();
    renderEmpty();
    loadConversations();
    refreshAssets();
    // 恢复上次打开的会话（含历史与思考内容）
    if (S.conversationId) await openConversation(S.conversationId, {silent: true});
  }

  function applyVideoDefaults(row) {
    S.video.resolution = row.default_resolution || (row.resolutions || [])[0] || '';
    S.video.seconds = row.default_seconds || row.min_seconds || 5;
    S.video.aspect = row.default_aspect_ratio || (row.aspect_ratios || [])[0] || '';
  }

  const videoRow = () => (S.boot?.video || []).find(row => row.id === S.video.model) || {};

  // ------------------------------------------------- 顶栏：模型 + 思考
  const REASONING_LEVELS = ['', 'low', 'high'];
  const REASONING_LABELS = {'': '默认思考', low: '浅思考', high: '深思考'};

  function renderTopSelectors() {
    const modelHost = $('agentModelHost');
    if (modelHost) {
      const models = (S.boot?.models?.chat || []).map(id => ({value: id, label: id}));
      modelHost.innerHTML = selectHTML('agentModelSelect', models, S.model, '无可用模型',
        value => {
          S.model = value;
          savePrefs();
          persistConversation({model: value});
        });
      bindSelect('agentModelSelect', () => {});
    }
    const reasoningHost = $('agentReasoningHost');
    if (reasoningHost) {
      const options = REASONING_LEVELS.map(level => ({
        value: level, label: REASONING_LABELS[level],
      }));
      reasoningHost.innerHTML = selectHTML('agentReasoningSelect', options, S.reasoning,
        '默认思考', value => {
          // 先写 S 再落库：顺序反了会出现「下拉显示深思考，存进会话的是旧值」。
          S.reasoning = REASONING_LEVELS.includes(value) ? value : '';
          savePrefs();
          persistConversation({reasoning: S.reasoning});
        });
      bindSelect('agentReasoningSelect', () => {});
    }
    renderThinkingToggle();
  }

  function renderThinkingToggle() {
    const button = $('agentThinking');
    if (!button) return;
    const on = S.thinking === true;
    button.setAttribute('aria-pressed', String(on));
    button.innerHTML = `<i class="fa-solid ${on ? 'fa-eye' : 'fa-eye-slash'}"></i>`;
    button.style.color = on ? 'var(--brand-ink)' : '';
    button.style.borderColor = on ? 'color-mix(in srgb,var(--brand) 35%,transparent)' : '';
  }

  function toggleThinking() {
    S.thinking = S.thinking === true ? null : true;
    renderThinkingToggle();
    savePrefs();
    persistConversation({thinking: S.thinking});
  }

  /** 把当前设置写回会话，换设备/刷新后仍是同一套。 */
  function persistConversation(patch) {
    if (!S.conversationId) return;
    api('/conversations/' + encodeURIComponent(S.conversationId), {
      method: 'PATCH', body: JSON.stringify(patch),
    }).catch(() => { /* 静默：设置持久化失败不该打断创作 */ });
  }

  // ------------------------------------------------------ 模式 + 预设
  const MODES = [
    {id: 'chat', label: '对话', icon: 'fa-comment'},
    {id: 'image', label: '生图', icon: 'fa-image'},
    {id: 'video', label: '生视频', icon: 'fa-film'},
  ];

  function renderMode() {
    const host = $('agentModes');
    if (!host) return;
    host.innerHTML = MODES.map(mode => `
      <button type="button" role="switch" aria-pressed="${S.mode === mode.id}"
              onclick="WBAgent.setMode('${mode.id}')"
              data-tooltip="${mode.label}"
              data-tooltip-description="${{chat: '普通对话，可调用生图/生视频工具。', image: '直接生成图片，套用右侧尺寸与模型。', video: '生成短视频；超过 15 秒用右侧长视频分段。'}[mode.id]}">
        <i class="fa-solid ${mode.icon}" aria-hidden="true"></i>${mode.label}
      </button>`).join('');
    const input = $('agentInput');
    if (input) {
      input.placeholder = {
        chat: '问点什么，或让我帮你生成图片和视频…',
        image: '描述画面：主体 / 环境 / 光线 / 风格…',
        video: '描述镜头：主体动作 / 镜头运动 / 光线氛围…',
      }[S.mode];
    }
    renderPresets();
  }

  function setMode(mode) {
    if (S.sending) return;
    S.mode = mode;
    savePrefs();
    renderMode();
    renderAside();
    persistConversation({mode});
    $('agentInput')?.focus();
  }

  function renderPresets() {
    const host = $('agentPresets');
    if (!host) return;
    const presets = (S.boot?.presets || {})[S.mode] || [];
    if (!presets.length || S.mode === 'chat') { host.innerHTML = ''; return; }
    host.innerHTML = presets.map(preset => `
      <button type="button" class="agent-preset" onclick="WBAgent.applyPreset('${preset.id}')"
              data-tooltip="${esc(preset.label)}"
              data-tooltip-description="把风格模板插入输入框并套用推荐尺寸/时长，可继续修改。">
        <i class="fa-solid ${preset.icon || 'fa-wand-magic-sparkles'}" aria-hidden="true"></i>${esc(preset.label)}
      </button>`).join('');
  }

  function applyPreset(id) {
    if (S.sending) return;
    const preset = ((S.boot?.presets || {})[S.mode] || []).find(row => row.id === id);
    if (!preset) return;
    const input = $('agentInput');
    input.value = preset.prompt;
    if (preset.size) S.image.size = preset.size;
    if (preset.seconds) S.video.seconds = preset.seconds;
    if (preset.resolution) S.video.resolution = preset.resolution;
    if (preset.aspect_ratio) S.video.aspect = preset.aspect_ratio;
    savePrefs();
    renderAside();
    input.focus();
    const at = preset.prompt.indexOf('{主题}');
    if (at >= 0) input.setSelectionRange(at, at + 4);
    else input.setSelectionRange(input.value.length, input.value.length);
    autoSize();
  }

  // ------------------------------------------------------------ 参数面板
  function renderAside() {
    const host = $('agentAside');
    if (!host) return;
    const cards = [];
    if (S.mode === 'image') cards.push(renderImageCard());
    if (S.mode === 'video') cards.push(renderVideoCard(), renderLongCard());
    cards.push(renderGalleryCard());
    if (S.boot?.merge && !S.boot.merge.available) {
      cards.push(`<div class="agent-card"><h4>
        <i class="fa-solid fa-triangle-exclamation"></i>缺少 ffmpeg</h4>
        <p class="agent-note">当前环境没有 ffmpeg，无法合并长视频。分段生成仍可用。</p></div>`);
    }
    host.innerHTML = cards.join('');
    wireAside();
    refreshEstimate();
    if (S.assets?.length) renderGallery(S.assets);
    else refreshAssets();
  }

  function renderImageCard() {
    const models = (S.boot?.models?.image || []).map(id => ({value: id, label: id}));
    const sizes = ['1024x1024', '1280x720', '720x1280', '1024x1536'];
    return `<div class="agent-card">
      <h4><i class="fa-solid fa-image"></i>生图参数</h4>
      <div class="agent-field"><label>模型</label>
        ${selectHTML('agentImageModel', models, S.image.model, '无可用模型', value => {
          S.image.model = value; savePrefs(); refreshEstimate();
        })}</div>
      <div class="agent-field"><label>尺寸</label>
        <div class="agent-options">${sizes.map(size => `
          <button type="button" class="agent-opt" aria-pressed="${S.image.size === size}"
                  onclick="WBAgent.setParam('image','size','${size}')">${size}</button>`).join('')}</div>
      </div>
      <div class="agent-estimate">
        <span class="v" id="agentEstimateValue">—</span><span class="u">积分</span>
        <span class="r" id="agentEstimateRange"></span>
      </div>
      <p class="agent-note">按本网关历史真实用量估算，仅供参考。</p>
    </div>`;
  }

  function renderVideoCard() {
    const models = (S.boot?.models?.video || []).map(id => ({value: id, label: id}));
    const row = videoRow();
    const resolutions = row.resolutions || [];
    const ratios = row.aspect_ratios || [];
    const min = row.min_seconds || 1;
    const max = row.max_seconds || 15;
    const seconds = [];
    const supported = row.supported_seconds || [];
    if (supported.length) seconds.push(...supported);
    else for (let value = min; value <= max; value += 1) seconds.push(value);
    return `<div class="agent-card">
      <h4><i class="fa-solid fa-film"></i>生视频参数</h4>
      <div class="agent-field"><label>模型</label>
        ${selectHTML('agentVideoModel', models, S.video.model, '无可用模型', value => {
          S.video.model = value;
          applyVideoDefaults(videoRow());
          savePrefs();
          renderAside();
        })}</div>
      <div class="agent-field"><label>分辨率</label>
        <div class="agent-options">${resolutions.map(value => `
          <button type="button" class="agent-opt" aria-pressed="${S.video.resolution === value}"
                  onclick="WBAgent.setParam('video','resolution','${value}')">${value}</button>`).join('')
            || '<span class="agent-note">未声明</span>'}</div>
      </div>
      <div class="agent-field"><label>比例</label>
        <div class="agent-options">${ratios.map(value => `
          <button type="button" class="agent-opt" aria-pressed="${S.video.aspect === value}"
                  onclick="WBAgent.setParam('video','aspect','${value}')">${value}</button>`).join('')
            || '<span class="agent-note">未声明</span>'}</div>
      </div>
      <div class="agent-field"><label>时长 · 单段上限 ${max}s</label>
        <div class="agent-options">${seconds.map(value => `
          <button type="button" class="agent-opt" aria-pressed="${Number(S.video.seconds) === value}"
                  onclick="WBAgent.setParam('video','seconds',${value})">${value}s</button>`).join('')}</div>
      </div>
      <div class="agent-estimate">
        <span class="v" id="agentEstimateValue">—</span><span class="u">积分</span>
        <span class="r" id="agentEstimateRange"></span>
      </div>
      <p class="agent-note">按本网关历史真实用量估算，仅供参考。</p>
    </div>`;
  }

  function renderLongCard() {
    const max = videoRow().max_seconds || 15;
    const total = Number(S.long.total) || 45;
    const segments = Math.ceil(total / max);
    return `<div class="agent-card">
      <h4><i class="fa-solid fa-layer-group"></i>长视频分段</h4>
      <div class="agent-field"><label>目标总时长（秒）</label>
        <input type="number" min="${max}" max="${max * 40}" step="1" value="${total}"
               onchange="WBAgent.setLong(this.value)" /></div>
      <p class="agent-note">单段上限 ${max} 秒，${total} 秒约 <b>${segments}</b> 段。生成后用素材库「合并」接成成片。</p>
      <div class="agent-options" style="margin-top:6px">
        <button type="button" class="agent-btn" style="height:26px;font-size:10px;padding:0 8px"
                onclick="WBAgent.planLong()"><i class="fa-solid fa-list-ol"></i>规划分镜</button>
      </div>
      <div id="agentPlanResult"></div>
    </div>`;
  }

  function renderGalleryCard() {
    return `<div class="agent-card">
      <h4><i class="fa-solid fa-images"></i>素材库<span class="spacer"></span>
        <button type="button" class="agent-btn icon"
                style="width:24px;height:24px;border:0;background:transparent"
                onclick="WBAgent.refreshAssets()"
                data-tooltip="刷新素材" data-tooltip-description="重新读取已生成的图片与视频。">
          <i class="fa-solid fa-rotate"></i></button></h4>
      <div class="agent-gallery" id="agentGallery">
        <div class="agent-gallery-empty">加载中…</div>
      </div>
      <div class="agent-options" style="margin-top:7px" id="agentMergeBar" hidden>
        <button type="button" class="agent-btn" style="height:26px;font-size:10px;padding:0 8px"
                onclick="WBAgent.mergeSelected()"><i class="fa-solid fa-scissors"></i>合并选中</button>
        <button type="button" class="agent-btn" style="height:26px;font-size:10px;padding:0 8px"
                onclick="WBAgent.clearSelection()">清空</button>
      </div>
    </div>`;
  }

  /** 参数面板重建后只需补一次可访问性初始化；
   *  选择回调已经通过 `selectHTML(..., onChange)` 直接交给组件，
   *  不再需要在这里补 click 监听（那条路会被 stopPropagation 吞掉）。 */
  function wireAside() {
    for (const id of ('agentImageModel', 'agentVideoModel')) {
      if (typeof window.initUISelects === 'function' && $(id)) {
        try { window.initUISelects([[id, '选择']]); } catch (_) { /* 组件兜底 */ }
      }
    }
  }

  function setParam(group, key, value) {
    if (S.sending) return;
    S[group][key] = value;
    savePrefs();
    renderAside();
  }

  function setLong(value) {
    S.long.total = Math.max(1, Number(value) || 45);
    savePrefs();
    renderAside();
  }

  // ------------------------------------------------------------ 上下文用量
  function renderContext(usage) {
    if (usage) S.context = usage;
    const chip = $('agentContext');
    if (!chip || !S.context) return;
    const {used_tokens: used, budget_tokens: budget, percent} = S.context;
    chip.hidden = false;
    chip.innerHTML = `<i class="fa-solid fa-database" style="font-size:9px"></i>
      上下文 ${percent}%`;
    chip.dataset.tooltip = `上下文已用 ${percent}%`;
    chip.dataset.tooltipDescription =
      `约 ${fmtTokens(used)} / ${fmtTokens(budget)} tokens（含系统提示、工具定义与历史消息）。` +
      '接近上限时会自动从最早的消息开始裁剪，并保留一句摘要。';
  }

  // ------------------------------------------------------------ 积分预估
  function refreshEstimate() {
    clearTimeout(S.estimateTimer);
    S.estimateTimer = setTimeout(async () => {
      const valueEl = $('agentEstimateValue');
      if (!valueEl) return;
      try {
        const body = S.mode === 'image'
          ? {kind: 'image', count: 1, size: S.image.size, model: S.image.model}
          : {kind: 'video', seconds: S.video.seconds, resolution: S.video.resolution,
             model: S.video.model};
        const data = await api('/estimate', {method: 'POST', body: JSON.stringify(body)});
        if (!valueEl.isConnected) return;
        valueEl.textContent = fmtCredits(data.credits);
        const range = data.credits_range || [];
        const label = $('agentEstimateRange');
        if (label) label.textContent = range.length
          ? `约 ${fmtCredits(range[0])}~${fmtCredits(range[1])}` : '';
      } catch (_) {
        valueEl.textContent = '—';
      }
    }, 160);
  }

  // ------------------------------------------------------------ 空状态
  function renderEmpty() {
    const stream = $('agentStream');
    if (!stream || stream.querySelector('.agent-msg, .agent-media, .agent-tool')) return;
    stream.innerHTML = `<div class="agent-empty">
      <div class="logo"><i class="fa-solid fa-wand-magic-sparkles"></i></div>
      <h3>开始创作</h3>
      <p>直接对话，或切换到「生图 / 生视频」。<br>
         我可以用内置账号池生成图片与视频，也能把多段分镜合并成完整长片。</p>
      <div class="agent-starters" id="agentStarters"></div>
    </div>`;
    const starters = [
      {text: '生成一张赛博朋克风格的城市夜景', mode: 'image'},
      {text: '做一个 30 秒的猫咪日常短片', mode: 'video'},
      {text: '帮我写一段产品宣传文案', mode: 'chat'},
      {text: '有哪些可用的模型？', mode: 'chat'},
    ];
    $('agentStarters').innerHTML = starters.map(item => `
      <button type="button" class="agent-starter"
              onclick="WBAgent.quickStart('${item.mode}','${esc(item.text)}')">${esc(item.text)}</button>`).join('');
  }

  function quickStart(mode, text) {
    if (S.sending) return;
    setMode(mode);
    $('agentInput').value = text;
    autoSize();
    submit();
  }

  // ------------------------------------------------------------ 消息渲染
  function appendMessage(role, html) {
    const stream = $('agentStream');
    stream.querySelector('.agent-empty')?.remove();
    const node = document.createElement('div');
    node.className = `agent-msg ${role}`;
    node.innerHTML = `<div class="avatar"><i class="fa-solid ${role === 'user' ? 'fa-user' : 'fa-wand-magic-sparkles'}"></i></div>
      <div class="body"><div class="bubble">${html}</div></div>`;
    stream.appendChild(node);
    scrollToEnd(true);
    return node;
  }

  /** 思考块：内容落库，刷新后再渲染仍可见。
   *
   * `open` 默认 true：用户要「生成中能看见思考」。生成结束时由
   * `finishThinking()` 自动折叠，避免历史越长、占屏越多。
   */
  function appendThink(text, open = true) {
    const stream = $('agentStream');
    const node = document.createElement('details');
    node.className = 'agent-think';
    node.open = open;
    node.innerHTML = `<summary><i class="fa-solid fa-brain"></i>
      <span class="think-label">思考过程</span>
      <span class="think-hint"></span></summary>
      <div class="think-body"></div>`;
    node.querySelector('.think-body').textContent = text;
    stream.appendChild(node);
    return node;
  }

  /** 一轮结束时收起思考块（用户手动展开过的不动）。 */
  function finishThinking(node, seconds) {
    if (!node) return;
    const label = node.querySelector('.think-label');
    if (label) label.textContent = `思考过程 · ${seconds.toFixed(1)}s`;
    const hint = node.querySelector('.think-hint');
    if (hint) hint.textContent = '已收起';
    // 只在「仍是展开状态」时收起：用户自己点开回看的就别抢他的操作。
    node.open = false;
  }

  function appendTool(tool, label) {
    const stream = $('agentStream');
    stream.querySelector('.agent-empty')?.remove();
    const node = document.createElement('div');
    node.className = 'agent-tool';
    node.dataset.tool = tool;
    node.innerHTML = `<div class="agent-tool-head">
      <span class="spin" aria-hidden="true"></span>
      <span class="tw">${esc(label || tool)}</span>
      <span class="st">执行中…</span></div>`;
    stream.appendChild(node);
    scrollToEnd();
    return node;
  }

  function finishTool(node, ok, summary, ms) {
    if (!node) return;
    node.classList.add(ok ? 'ok' : 'fail');
    const head = node.querySelector('.agent-tool-head');
    head.querySelector('.spin')?.remove();
    const icon = document.createElement('span');
    icon.className = 'done-ic';
    icon.innerHTML = `<i class="fa-solid ${ok ? 'fa-circle-check' : 'fa-circle-exclamation'}"></i>`;
    head.prepend(icon);
    const text = head.querySelector('.tw');
    if (summary) text.textContent = summary;
    head.querySelector('.st').textContent = ms != null
      ? `${(ms / 1000).toFixed(1)}s` : (ok ? '完成' : '失败');
  }

  /** Typing Indicator：等待首个 token 时的三点跳动。
   *
   * 之前只有文字「处理中」，长思考（十几秒）看起来像卡死。三点动画是
   * 聊天界面的通用语汇，用户一眼就知道「还在产出」。
   */
  function appendTyping() {
    const stream = $('agentStream');
    stream.querySelector('.agent-empty')?.remove();
    const node = document.createElement('div');
    node.className = 'agent-typing';
    node.innerHTML = `<span class="dots"><i></i><i></i><i></i></span>`;
    stream.appendChild(node);
    scrollToEnd();
    return node;
  }

  function appendNotice(text) {
    const stream = $('agentStream');
    const node = document.createElement('div');
    node.className = 'agent-pending';
    node.innerHTML = `<span class="dots"><i></i><i></i><i></i></span>${esc(text)}`;
    stream.appendChild(node);
    scrollToEnd();
    return node;
  }

  /** 助手消息底部的用量条：token 与积分。
   *
   * 数值全部来自服务端 `done` 事件（它来自 `_record_usage` 写入的同一份
   * 用量记录），前端不做任何二次推算 —— 否则「界面显示的积分」和
   * 「用量记录里的积分」迟早对不上。
   */
  function appendUsage(node, usage) {
    if (!node || !usage) return;
    // 挂在 `.body` 里、气泡的**兄弟位置**：
    //   * 放气泡里会被流式渲染的 innerHTML 重写抹掉（实测）；
    //   * 直接挂 .agent-msg 又会和气泡抢同一行、把气泡挤扁（实测会竖排）。
    // 挂在 .body 这个纵向容器里两个问题都不存在。
    const host = node.querySelector('.body') || node;
    host.querySelector('.agent-usage')?.remove();
    const parts = [];
    const prompt = Number(usage.prompt_tokens || 0);
    const completion = Number(usage.completion_tokens || 0);
    const total = Number(usage.total_tokens || (prompt + completion)) || 0;
    const credits = Number(usage.credits || 0);
    if (total) {
      parts.push(`<span data-tooltip="本轮 Token" data-tooltip-description="输入 ${fmtTokens(prompt)} · 输出 ${fmtTokens(completion)}，合计 ${fmtTokens(total)} tokens。">`
        + `<i class="fa-solid fa-coins" aria-hidden="true"></i> ${fmtTokens(total)} tokens</span>`);
    }
    if (credits) {
      parts.push(`<span class="credits" data-tooltip="本轮消耗积分" data-tooltip-description="来自用量记录的真实扣费（上游返回的 usage.credit），不是估算值。">`
        + `<i class="fa-solid fa-bolt" aria-hidden="true"></i> ${fmtCredits(credits)} 积分</span>`);
    }
    if (!parts.length) return;
    const row = document.createElement('div');
    row.className = 'agent-usage';
    row.innerHTML = parts.join('');
    host.appendChild(row);
  }

  function appendMedia(asset) {
    const stream = $('agentStream');
    const url = `${BASE}/assets/${asset.asset_id}`;
    const isVideo = asset.kind === 'video';
    const node = document.createElement('div');
    node.className = 'agent-media';
    node.innerHTML = `${isVideo
      ? `<video src="${url}" controls preload="metadata" playsinline></video>`
      : `<img src="${url}" alt="${esc((asset.prompt || '').slice(0, 60))}" loading="lazy" />`}
      <div class="meta">
        <span>${esc(asset.model || '')}</span>
        ${isVideo && asset.duration_s ? `<span>· ${Math.round(asset.duration_s)}s</span>` : ''}
        ${asset.credits ? `<span>· ${fmtCredits(asset.credits)} 积分</span>` : ''}
        <span class="spacer"></span>
        <a href="${url}" download="${asset.asset_id}${isVideo ? '.mp4' : '.png'}">下载</a>
      </div>`;
    stream.appendChild(node);
    scrollToEnd();
  }

  function scrollToEnd(force = false) {
    const stream = $('agentStream');
    const near = stream.scrollHeight - stream.scrollTop - stream.clientHeight < 170;
    if (near || force) stream.scrollTop = stream.scrollHeight;
  }

  // ------------------------------------------------------------ 发送
  function autoSize() {
    const input = $('agentInput');
    if (!input) return;
    input.style.height = 'auto';
    input.style.height = Math.min(180, Math.max(52, input.scrollHeight)) + 'px';
  }

  function setBusy(busy) {
    S.sending = busy;
    const send = $('agentSend');
    if (send) {
      send.classList.toggle('stop', busy);
      send.innerHTML = busy
        ? '<i class="fa-solid fa-stop"></i> 停止'
        : '<i class="fa-solid fa-paper-plane"></i> 发送';
    }
    const enhance = $('agentEnhance');
    if (enhance) enhance.disabled = busy;
  }

  async function submit() {
    if (S.sending) { stop(); return; }
    const input = $('agentInput');
    const text = input.value.trim();
    if (!text) return;
    if (!S.ready) { toast('智能体尚未就绪', 'err'); return; }

    appendMessage('user', renderMarkdown(text));
    input.value = '';
    autoSize();
    setBusy(true);

    const controller = new AbortController();
    S.abort = controller;

    let assistantNode = null;
    let assistantBubble = null;
    let thinkNode = null;
    let thinkBody = null;
    let thinkStarted = 0;
    let typing = appendTyping();
    let lastUsage = null;
    const toolNodes = new Map();
    const notices = [];

    // ---- 流式渲染：缓冲 + 批量 + 平滑补字 -------------------------------
    //
    // 为什么不能「收到一个 token 就 innerHTML 一次」：
    //   1. 上游 token 频率可达每秒几十个，每次 innerHTML 都要重新解析整段
    //      Markdown 并重排，长回答时会明显掉帧；
    //   2. 重排还会让滚动位置跳动（scrollTop 随内容变化）。
    //
    // 做法（三层，各管一段）：
    //   * `pendingText`：收到的增量先进缓冲；
    //   * 每 `FLUSH_MS` 把缓冲并入 `shownText` 并**重绘一次**（批量渲染）；
    //   * `shownText` 再按 `revealNext()` 逐字追平 `targetText`，形成平滑补字。
    //     —— 如果网络一次性给了 200 字，直接全放出来就是「跳变」；
    //        逐字补上才有「正在打字」的观感。
    const FLUSH_MS = 60;        // 批量渲染间隔
    const REVEAL_MS = 16;       // 补字帧间隔（约 60fps）
    const REVEAL_CHARS = 2;     // 每帧补几个字

    let targetText = '';        // 已收到（含缓冲）的完整文本
    let shownText = '';         // 已显示到界面的文本
    let flushTimer = null;
    let revealTimer = null;
    let doneStreaming = false;

    const paint = () => {
      if (!assistantBubble) return;
      // 只在末尾加光标，内容用 Markdown 渲染。
      assistantBubble.innerHTML = renderMarkdown(shownText)
        + (shownText ? '<span class="agent-caret" aria-hidden="true"></span>' : '');
      scrollToEnd();
    };

    const revealNext = () => {
      if (shownText.length >= targetText.length) {
        revealTimer = null;
        if (doneStreaming) {
          // 追平后收尾：去掉光标，补一次最终渲染。
          if (assistantBubble) assistantBubble.innerHTML = renderMarkdown(shownText);
        }
        return;
      }
      // 落后越多补得越快，避免长时间追不上（积压 400 字时每帧多补一些）。
      const behind = targetText.length - shownText.length;
      const step = behind > 240 ? 8 : behind > 80 ? 4 : REVEAL_CHARS;
      shownText = targetText.slice(0, Math.min(targetText.length, shownText.length + step));
      paint();
      revealTimer = setTimeout(revealNext, REVEAL_MS);
    };

    const startReveal = () => {
      if (revealTimer) return;
      revealTimer = setTimeout(revealNext, REVEAL_MS);
    };

    const flush = () => {
      flushTimer = null;
      if (!assistantNode) return;
      startReveal();
    };

    const queueDelta = piece => {
      targetText += piece;
      if (!assistantNode) {
        assistantNode = appendMessage('assistant', '');
        assistantBubble = assistantNode.querySelector('.bubble');
      }
      // 首个 token 到达 → 换掉三点动画
      if (typing) { typing.remove(); typing = null; }
      if (!flushTimer) flushTimer = setTimeout(flush, FLUSH_MS);
    };

    /** 收尾：让补字**自然追平**，不整段重绘。
     *
     * 之前的实现直接 `shownText = targetText` 再重绘一次，效果是最后一段
     * 文字「啪」地整块出现 —— 就是看到的收尾卡顿。现在只标记流已结束，
     * 由 `revealNext()` 继续按帧补完，追平后自己去掉光标。
     * 只有在极端情况下（补字停了但没追平）才兜底强制写完。
     */
    const settle = () => {
      doneStreaming = true;
      if (flushTimer) { clearTimeout(flushTimer); flushTimer = null; }
      // 把还没落屏的缓冲算进目标，确保不会漏字。
      if (shownText.length >= targetText.length) {
        if (assistantBubble) assistantBubble.innerHTML = renderMarkdown(shownText);
        if (revealTimer) { clearTimeout(revealTimer); revealTimer = null; }
        return;
      }
      startReveal();
      // 兜底：1.2 秒后仍未追平（比如页面被节流），直接写完。
      setTimeout(() => {
        if (shownText.length < targetText.length) {
          if (revealTimer) { clearTimeout(revealTimer); revealTimer = null; }
          shownText = targetText;
          if (assistantBubble) assistantBubble.innerHTML = renderMarkdown(shownText);
        }
      }, 1200);
    };

    const body = {
      message: text,
      conversation_id: S.conversationId || undefined,
      mode: S.mode,
      model: S.model,
      reasoning: S.reasoning || undefined,
      thinking: S.thinking === true ? true : undefined,
    };
    if (S.mode === 'image') body.image = S.image;
    if (S.mode === 'video') body.video = S.video;

    try {
      const response = await fetch(BASE + '/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      if (!response.ok) {
        let detail = `HTTP ${response.status}`;
        try { const data = await response.json(); detail = data.detail || detail; } catch (_) {}
        throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
      }
      await readStream(response, event => {
        switch (event.type) {
          case 'meta':
            if (event.conversation_id && event.conversation_id !== S.conversationId) {
              S.conversationId = event.conversation_id;
              savePrefs();
              persistConversation({model: S.model, mode: S.mode,
                                   reasoning: S.reasoning, thinking: S.thinking});
            }
            if (event.context) renderContext(event.context);
            break;
          case 'notice':
            notices.push(appendNotice(event.text || '处理中…'));
            break;
          case 'reasoning':
            if (!thinkNode) {
              thinkStarted = Date.now();
              if (typing) { typing.remove(); typing = null; }
              thinkNode = appendThink('');
              thinkBody = thinkNode.querySelector('.think-body');
            }
            thinkBody.textContent += event.text || '';
            thinkBody.scrollTop = thinkBody.scrollHeight;
            break;
          case 'delta':
            queueDelta(event.text || '');
            break;
          case 'tool_start':
            toolNodes.set(event.tool, appendTool(event.tool, event.label));
            break;
          case 'tool_end':
            finishTool(toolNodes.get(event.tool), event.ok, event.summary, event.ms);
            toolNodes.delete(event.tool);
            break;
          case 'media':
            appendMedia(event.asset || {});
            break;
          case 'done':
            if (event.context) renderContext(event.context);
            // 用量直接显示在助手消息底部，顶栏那个 chip 不再重复占位。
            lastUsage = event.usage || (event.credits ? {credits: event.credits} : null);
            if (assistantNode && lastUsage) appendUsage(assistantNode, lastUsage);
            break;
          case 'error':
            appendMessage('assistant',
              `<span style="color:var(--danger)">${esc(event.message || '处理失败')}</span>`);
            break;
        }
      });
    } catch (error) {
      if (error.name === 'AbortError') {
        appendMessage('assistant', '<span style="color:var(--fg-subtle)">已停止</span>');
      } else {
        appendMessage('assistant',
          `<span style="color:var(--danger)">请求失败：${esc(error.message)}</span>`);
      }
    } finally {
      if (typing) { typing.remove(); typing = null; }
      notices.forEach(node => node.remove());
      toolNodes.forEach((node, name) => finishTool(node, false, `${name} · 已中断`, null));
      toolNodes.clear();
      settle();
      // 思考块在生成结束后自动收起（内容仍在，点开可回看）。
      if (thinkNode) {
        finishThinking(thinkNode, (Date.now() - thinkStarted) / 1000);
      }
      S.abort = null;
      setBusy(false);
      loadConversations();
      refreshAssets();
    }
  }

  /** SSE 按空行分帧：网络分片会把一个 data 行切断。 */
  async function readStream(response, onEvent) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, {stream: true});
      let index;
      while ((index = buffer.indexOf('\n\n')) >= 0) {
        const frame = buffer.slice(0, index);
        buffer = buffer.slice(index + 2);
        const data = frame.split('\n')
          .filter(line => line.startsWith('data:'))
          .map(line => line.slice(5).trim())
          .join('\n');
        if (!data) continue;
        try { onEvent(JSON.parse(data)); } catch (_) { /* 跳过坏帧 */ }
      }
    }
  }

  function stop() { S.abort?.abort(); }

  async function enhance() {
    const input = $('agentInput');
    const draft = input.value.trim();
    if (!draft) { toast('请先输入提示词', 'err'); input.focus(); return; }
    const button = $('agentEnhance');
    const original = button.innerHTML;
    button.disabled = true;
    button.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin"></i> 增强中';
    try {
      // 刻意不传 model：增强要的是结论，由后端挑能关思考的模型。
      const data = await api('/enhance', {
        method: 'POST',
        body: JSON.stringify({prompt: draft, kind: S.mode === 'video' ? 'video' : 'image',
                              mode: S.mode}),
      });
      if (!data.ok) throw new Error(data.error || '增强失败');
      input.value = data.prompt;
      autoSize();
      toast('提示词已优化');
    } catch (error) {
      toast('增强失败：' + error.message, 'err');
    } finally {
      button.disabled = false;
      button.innerHTML = original;
    }
  }

  // ------------------------------------------------------------ 长视频
  async function planLong() {
    const host = $('agentPlanResult');
    if (!host) return;
    host.innerHTML = '<p class="agent-note">规划中…</p>';
    try {
      const data = await api('/plan', {
        method: 'POST',
        body: JSON.stringify({total_seconds: S.long.total, model: S.video.model}),
      });
      if (!data.ok) throw new Error(data.error || '规划失败');
      S.lastPlan = data.plan || [];
      host.innerHTML = `<div class="agent-shots">
          ${(data.plan || []).map((seconds, index) => `<div class="agent-shot">
            <span class="n">${index + 1}</span><span class="t">${seconds} 秒分镜</span></div>`).join('')}
        </div>
        <div class="agent-estimate" style="margin-top:7px">
          <span class="v">${fmtCredits(data.credits)}</span><span class="u">积分</span>
          <span class="r">约 ${fmtCredits((data.credits_range || [])[0])}~${fmtCredits((data.credits_range || [])[1])}</span>
        </div>
        <div class="agent-options" style="margin-top:7px">
          <button type="button" class="agent-btn" style="height:26px;font-size:10px;padding:0 8px"
                  onclick="WBAgent.fillPlan()"><i class="fa-solid fa-arrow-down"></i>填入分镜骨架</button>
        </div>`;
    } catch (error) {
      host.innerHTML = `<p class="agent-note" style="color:var(--danger)">${esc(error.message)}</p>`;
    }
  }

  function fillPlan() {
    if (!S.lastPlan.length) return;
    const input = $('agentInput');
    input.value = '请按以下分镜逐段生成视频，最后合并成一个完整视频：\n'
      + S.lastPlan.map((seconds, index) => `${index + 1}. [${seconds}s] `).join('\n');
    autoSize();
    input.focus();
  }

  // ------------------------------------------------------------ 素材库
  async function refreshAssets() {
    const host = $('agentGallery');
    if (!host) return;
    try {
      const data = await api('/assets?limit=24');
      S.assets = data.items || [];
      renderGallery(S.assets);
    } catch (_) {
      host.innerHTML = '<div class="agent-gallery-empty">读取失败</div>';
    }
  }

  function renderGallery(items) {
    const host = $('agentGallery');
    if (!host) return;
    if (!items.length) {
      host.innerHTML = '<div class="agent-gallery-empty">还没有作品</div>';
      updateMergeBar();
      return;
    }
    host.innerHTML = items.map(item => {
      const url = `${BASE}/assets/${item.asset_id}`;
      const selected = S.selected.has(item.asset_id);
      return `<button type="button" class="agent-thumb ${selected ? 'sel' : ''}"
          onclick="WBAgent.toggleAsset('${item.asset_id}')"
          data-tooltip="${esc(item.model || '')}"
          data-tooltip-description="${esc((item.prompt || '').slice(0, 120))}">
        ${item.kind === 'video'
          ? `<video src="${url}" muted preload="metadata"></video>`
          : `<img src="${url}" alt="" loading="lazy" />`}
        <span class="badge">${item.kind === 'video' ? `${Math.round(item.duration_s || 0)}s` : 'IMG'}</span>
      </button>`;
    }).join('');
    updateMergeBar();
  }

  function toggleAsset(assetId) {
    if (S.selected.has(assetId)) S.selected.delete(assetId);
    else S.selected.add(assetId);
    renderGallery(S.assets || []);
  }

  function clearSelection() {
    S.selected.clear();
    renderGallery(S.assets || []);
  }

  function updateMergeBar() {
    const bar = $('agentMergeBar');
    if (!bar) return;
    const videos = [...S.selected].filter(id =>
      (S.assets || []).some(item => item.asset_id === id && item.kind === 'video')).length;
    bar.hidden = videos < 2;
  }

  async function mergeSelected() {
    if (S.selected.size < 2) { toast('至少选择两个视频片段', 'err'); return; }
    const bar = $('agentMergeBar');
    const original = bar.innerHTML;
    bar.innerHTML = '<span class="agent-note">合并中…</span>';
    try {
      const data = await api('/merge', {
        method: 'POST',
        body: JSON.stringify({asset_ids: [...S.selected], conversation_id: S.conversationId}),
      });
      if (!data.ok) throw new Error(data.error || '合并失败');
      toast(`已合并 ${data.segments} 段，共 ${data.duration_s} 秒`);
      appendMedia({...data, kind: 'video', credits: 0});
      S.selected.clear();
      refreshAssets();
    } catch (error) {
      toast('合并失败：' + error.message, 'err');
      bar.innerHTML = original;
    }
  }

  // ------------------------------------------------------------ 会话管理
  async function loadConversations() {
    const host = $('agentSideList');
    if (!host) return;
    try {
      const data = await api('/conversations?limit=40');
      S.conversations = data.items || [];
      renderConversations();
    } catch (_) {
      host.innerHTML = '<div class="agent-side-empty">列表读取失败</div>';
    }
  }

  function renderConversations() {
    const host = $('agentSideList');
    if (!host) return;
    if (!S.conversations.length) {
      host.innerHTML = '<div class="agent-side-empty">还没有会话<br>发一句话就开始了</div>';
      return;
    }
    host.innerHTML = S.conversations.map(row => `
      <div class="agent-conv ${row.conversation_id === S.conversationId ? 'active' : ''}"
           data-id="${row.conversation_id}" onclick="WBAgent.openConversation('${row.conversation_id}')">
        <span class="pin" ${row.pinned ? '' : 'hidden'}
              data-tooltip="已置顶" data-tooltip-description="置顶会话固定排在最前。">
          <i class="fa-solid fa-thumbtack"></i></span>
        <span class="label">${esc(row.title || '新会话')}</span>
        <time>${esc(fmtTime(row.updated_at))}</time>
        <span class="ops">
          <button type="button" data-tooltip="重命名" aria-label="重命名"
                  onclick="event.stopPropagation();WBAgent.renameConversation('${row.conversation_id}')">
            <i class="fa-solid fa-pen"></i></button>
          <button type="button" data-tooltip="${row.pinned ? '取消置顶' : '置顶'}"
                  aria-label="${row.pinned ? '取消置顶' : '置顶'}"
                  onclick="event.stopPropagation();WBAgent.pinConversation('${row.conversation_id}',${row.pinned ? 'false' : 'true'})">
            <i class="fa-solid fa-thumbtack"></i></button>
          <button type="button" data-tooltip="删除" aria-label="删除"
                  onclick="event.stopPropagation();WBAgent.deleteConversation('${row.conversation_id}')">
            <i class="fa-solid fa-trash"></i></button>
        </span>
      </div>`).join('');
  }

  /** 重命名：用控制台的模态框，不用浏览器 prompt。
   *
   * `window.prompt` 会显示「127.0.0.1:8790 显示…」这种地址栏文案，
   * 且样式完全不受控 —— 在一个精心设计的控制台里非常突兀。
   */
  function renameConversation(conversationId) {
    const row = S.conversations.find(item => item.conversation_id === conversationId);
    const current = row?.title || '';
    window.openModal(`<h3 class="font-semibold text-lg mb-1">重命名会话</h3>
      <p class="text-xs mb-3" style="color:var(--fg-muted)">只改显示名称，不影响上下文内容。</p>
      <input id="agentRenameInput" class="w-full border rounded-lg px-3 py-2 text-sm"
             maxlength="120" value="${esc(current)}" placeholder="输入新的会话名称" />
      <p id="agentRenameErr" class="text-xs mt-2" style="color:var(--danger)"></p>
      <div class="flex justify-end gap-2 mt-4">
        <button type="button" class="console-btn quiet" onclick="closeModal()">取消</button>
        <button type="button" class="console-btn primary" id="agentRenameOk">保存</button>
      </div>`, 'max-w-md');

    const input = $('agentRenameInput');
    const error = $('agentRenameErr');
    const ok = $('agentRenameOk');
    input?.focus();
    input?.select();

    const commit = async () => {
      const title = (input?.value || '').trim();
      if (!title) { if (error) error.textContent = '标题不能为空'; return; }
      ok.disabled = true;
      try {
        await api('/conversations/' + encodeURIComponent(conversationId), {
          method: 'PATCH', body: JSON.stringify({title}),
        });
        closeModal();
        toast('已重命名');
        loadConversations();
      } catch (err) {
        if (error) error.textContent = err.message;
        ok.disabled = false;
      }
    };
    ok?.addEventListener('click', commit);
    input?.addEventListener('keydown', event => {
      if (event.key === 'Enter') { event.preventDefault(); commit(); }
    });
  }

  async function pinConversation(conversationId, pinned) {
    try {
      await api('/conversations/' + encodeURIComponent(conversationId), {
        method: 'PATCH', body: JSON.stringify({pinned}),
      });
      loadConversations();
    } catch (error) {
      toast('操作失败：' + error.message, 'err');
    }
  }

  async function deleteConversation(conversationId) {
    const ok = window.uiConfirm
      ? await window.uiConfirm({title: '删除会话', message: '删除后这段对话与上下文不可恢复。',
                                danger: true, okText: '删除'})
      : window.confirm('删除这段对话？');
    if (!ok) return;
    try {
      await api('/conversations/' + encodeURIComponent(conversationId), {method: 'DELETE'});
      if (S.conversationId === conversationId) {
        S.conversationId = '';
        savePrefs();
        $('agentStream').innerHTML = '';
        renderEmpty();
      }
      toast('已删除');
      loadConversations();
    } catch (error) {
      toast('删除失败：' + error.message, 'err');
    }
  }

  async function openConversation(conversationId, options = {}) {
    if (S.sending) { toast('请先停止当前生成', 'err'); return; }
    try {
      const data = await api('/conversations/' + encodeURIComponent(conversationId));
      S.conversationId = conversationId;
      if (data.model) S.model = data.model;
      if (data.mode) S.mode = data.mode;
      if (typeof data.reasoning === 'string') S.reasoning = data.reasoning;
      if (data.thinking === true || data.thinking === null) S.thinking = data.thinking;
      savePrefs();
      renderMode();
      renderTopSelectors();
      renderAside();
      renderContext(data.context);

      const stream = $('agentStream');
      stream.innerHTML = '';
      for (const message of data.messages || []) {
        const role = message.role;
        if (role === 'user') {
          appendMessage('user', renderMarkdown(message.content));
        } else if (role === 'assistant') {
          if (message.reasoning_content) appendThink(message.reasoning_content, false);
          if (message.content) {
            const node = appendMessage('assistant', renderMarkdown(message.content));
            // 历史里的用量也要显示（服务端随消息一起存），
            // 否则重新打开会话就看不到「这次花了多少」。
            if (message.usage) appendUsage(node, message.usage);
          }
        }
      }
      if (!stream.querySelector('.agent-msg, .agent-think')) renderEmpty();
      loadConversations();
      setTimeout(() => { stream.scrollTop = stream.scrollHeight; }, 50);
    } catch (error) {
      if (!options.silent) toast('打开会话失败：' + error.message, 'err');
      else if (S.conversationId) { S.conversationId = ''; savePrefs(); }
    }
  }

  function newConversation() {
    if (S.sending) { toast('请先停止当前生成', 'err'); return; }
    S.conversationId = '';
    savePrefs();
    const stream = $('agentStream');
    stream.innerHTML = '';
    renderEmpty();
    const chip = $('agentCredits');
    if (chip) chip.hidden = true;
    S.context = null;
    const ctx = $('agentContext');
    if (ctx) ctx.hidden = true;
    loadConversations();
    $('agentInput')?.focus();
  }

  // ------------------------------------------------------------ 绑定
  function bind() {
    const input = $('agentInput');
    if (!input) return;
    input.addEventListener('input', autoSize);
    input.addEventListener('keydown', event => {
      if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        submit();
      }
    });
    const on = (id, handler) => $(id)?.addEventListener('click', handler);
    on('agentSend', submit);
    on('agentEnhance', enhance);
    on('agentNew', newConversation);
    on('agentThinking', toggleThinking);
    on('agentAsideToggle', () => {
      const shell = $('agentShell');
      shell?.classList.toggle('aside-off');
      // 按钮反映当前状态，否则会有「点了没反应」的错觉。
      $('agentAsideToggle')?.setAttribute('aria-pressed',
        String(!!shell?.classList.contains('aside-off')));
    });
    on('agentSideToggle', () => {
      const shell = $('agentShell');
      shell?.classList.toggle('side-off');
      $('agentSideToggle')?.setAttribute('aria-pressed',
        String(!!shell?.classList.contains('side-off')));
    });
    autoSize();
  }

  // 控制台主脚本可能在 agent.js 之前就 activateTab('agent')。
  window.addEventListener('wb-agent-needed', enter);
  document.addEventListener('wb-agent-needed', enter);
  if (document.documentElement.dataset.activeTab === 'agent') enter();

  return {
    enter, setMode, setParam, setLong, toggleThinking,
    applyPreset, quickStart, submit, stop, enhance,
    planLong, fillPlan, refreshAssets, toggleAsset, clearSelection, mergeSelected,
    openConversation, newConversation,
    renameConversation, pinConversation, deleteConversation,
  };
})();
