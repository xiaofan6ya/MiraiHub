window.WBPhone = (() => {
  const patterns = {'86': /^1[3-9][0-9]{9}$/, '852': /^[4-9][0-9]{7}$/, '853': /^6[0-9]{7}$/};
  function read(key) {
    try {
      const state = JSON.parse(sessionStorage.getItem(key));
      return state && Date.now() - state.savedAt < 20 * 60 * 1000 ? state : {};
    } catch (_) { return {}; }
  }
  function save(key, state) {
    try { sessionStorage.setItem(key, JSON.stringify({...state, savedAt: Date.now()})); } catch (_) {}
  }
  function clear(key) { try { sessionStorage.removeItem(key); } catch (_) {} }
  function valid(country, mobile) { return !!patterns[country] && patterns[country].test(mobile); }
  let active = null;
  function closeRegion() {
    if (!active) return;
    active.menu.hidden = true;
    active.root.append(active.menu);
    active.menu.classList.remove('wb-region-floating');
    active.menu.style.left = ''; active.menu.style.top = '';
    active.trigger.setAttribute('aria-expanded', 'false');
    active = null;
  }
  document.addEventListener('pointerdown', event => {
    if (active && !active.root.contains(event.target) && !active.menu.contains(event.target)) closeRegion();
  });
  window.addEventListener('resize', closeRegion);
  document.addEventListener('scroll', closeRegion, true);
  function syncRegion(select) { select?._wbRegion?.render(); }
  function mountRegion(select, input) {
    if (select._wbRegion) return syncRegion(select);
    const root = document.createElement('div'); root.className = 'wb-region';
    select.before(root); root.append(select);
    select.classList.add('wb-region-native'); select.tabIndex = -1; select.setAttribute('aria-hidden', 'true');
    const trigger = document.createElement('button'); trigger.type = 'button'; trigger.className = 'wb-region-trigger';
    trigger.setAttribute('aria-haspopup', 'listbox'); trigger.setAttribute('aria-expanded', 'false');
    const menu = document.createElement('div'); menu.className = 'wb-region-menu'; menu.hidden = true;
    menu.id = select.id + '-options'; menu.setAttribute('role', 'listbox'); menu.setAttribute('aria-label', '手机区号');
    trigger.setAttribute('aria-controls', menu.id);
    const names = {'86':'中国大陆', '852':'中国香港', '853':'中国澳门'};
    const options = [...select.options].map(option => {
      const button = document.createElement('button'); button.type = 'button'; button.className = 'wb-region-option';
      button.setAttribute('role', 'option'); button.dataset.value = option.value;
      const code = document.createElement('strong'); code.textContent = '+' + option.value;
      const region = document.createElement('span'); region.textContent = names[option.value];
      button.append(code, region); menu.append(button);
      button.onclick = () => {
        select.value = option.value; select.dispatchEvent(new Event('change', {bubbles:true}));
        closeRegion(); trigger.focus();
      };
      button.onkeydown = event => {
        const index = options.indexOf(button);
        const next = event.key === 'ArrowDown' ? (index + 1) % options.length
          : event.key === 'ArrowUp' ? (index + options.length - 1) % options.length
          : event.key === 'Home' ? 0 : event.key === 'End' ? options.length - 1 : -1;
        if (next >= 0) { event.preventDefault(); options[next].focus(); }
        if (event.key === 'Tab') closeRegion();
      };
      return button;
    });
    const render = () => {
      trigger.textContent = '+' + select.value;
      trigger.setAttribute('aria-label', '手机区号 +' + select.value + ' ' + names[select.value]);
      options.forEach(option => option.setAttribute('aria-selected', String(option.dataset.value === select.value)));
      if (input) { input.maxLength = select.value === '86' ? 11 : 8; input.placeholder = input.maxLength + ' 位手机号'; }
    };
    trigger.onclick = () => {
      const opening = menu.hidden; closeRegion();
      if (opening) {
        active = {root, trigger, menu}; menu.hidden = false; trigger.setAttribute('aria-expanded', 'true');
        const bounds = trigger.getBoundingClientRect();
        document.body.append(menu); menu.classList.add('wb-region-floating');
        const width = menu.offsetWidth, height = menu.offsetHeight;
        menu.style.left = Math.max(12, Math.min(bounds.left, innerWidth - width - 12)) + 'px';
        menu.style.top = Math.max(12, bounds.bottom + height + 6 <= innerHeight - 12
          ? bounds.bottom + 6 : bounds.top - height - 6) + 'px';
        options.find(option => option.dataset.value === select.value)?.focus();
      }
    };
    trigger.onkeydown = event => {
      if (['ArrowDown', 'ArrowUp'].includes(event.key)) { event.preventDefault(); if (menu.hidden) trigger.click(); }
    };
    const dismiss = event => {
      if (event.key === 'Escape' && !menu.hidden) { event.preventDefault(); event.stopPropagation(); closeRegion(); trigger.focus(); }
    };
    root.addEventListener('keydown', dismiss); menu.addEventListener('keydown', dismiss);
    root.append(trigger, menu); select.addEventListener('change', render);
    select._wbRegion = {render}; render();
  }
  return {read, save, clear, valid, mountRegion, syncRegion, closeRegion};
})();
