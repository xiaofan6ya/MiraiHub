/* MiraiHub 控制台主题：**波纹展开切换**。
 *
 * 之前是「直接换 data-theme」：整页颜色一帧内跳变，暗色切换时卡片会闪一下
 * 白底（浏览器重绘顺序不一致），观感就是"一闪一闪、不丝滑"。
 *
 * 现在照抄 mirai-mifan 官网的做法（用户指定参考）：
 *   1. 支持 View Transitions 的浏览器：`document.startViewTransition` 换主题，
 *      然后以**点击点为圆心**对 `::view-transition-new(root)` 做
 *      `clip-path: circle()` 展开 —— 新主题像水波一样从手指按下的地方荡开；
 *   2. 不支持 VT 的浏览器：退化为一个全屏 overlay（暗色底）做同样的
 *      clip-path 圆形展开，播完再真正换主题 —— 依然没有整页闪白；
 *   3. `prefers-reduced-motion` 用户：直接切，不做动画（尊重系统设置）。
 */
window.WBTheme = (() => {
  const key = 'wb_console_theme';
  const RIPPLE_MS = 400;
  let theme = 'light';
  let vtStyleReady = false;
  try { if (localStorage.getItem(key) === 'dark') theme = 'dark'; } catch (_) {}

  function syncButtons() {
    document.querySelectorAll('[data-theme-choice]').forEach(button => {
      button.setAttribute('aria-pressed', String(button.dataset.themeChoice === theme));
    });
  }

  function render() {
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
    syncButtons();
  }

  function apply(value) {
    theme = value === 'dark' ? 'dark' : 'light';
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
    try { localStorage.setItem(key, theme); } catch (_) {}
    syncButtons();
    window.dispatchEvent(new Event('wb-theme-change'));
  }

  /* ---- View Transitions 样式：关掉默认交叉淡化，层级交给 clip-path ---- */
  function ensureVtStyles() {
    if (vtStyleReady || document.getElementById('wb-theme-vt-style')) return;
    const style = document.createElement('style');
    style.id = 'wb-theme-vt-style';
    style.textContent =
      '::view-transition-old(root),::view-transition-new(root){animation:none;mix-blend-mode:normal}' +
      '::view-transition-old(root){z-index:1}' +
      '::view-transition-new(root){z-index:2147483646}' +
      // 变浅色：动画作用于**旧主题（暗色）收拢**，必须把 old 提到 new 之上，
      // 否则浅色 new 层全程盖住 old —— 收拢波纹完全看不见（用户实测：
      // 暗色→浅色"动画没了"就是这个原因）。照抄 mirai-mifan 的层级反转。
      '.wb-theme-to-light::view-transition-old(root){z-index:2147483646}' +
      '.wb-theme-to-light::view-transition-new(root){z-index:1}';
    document.head.appendChild(style);
    vtStyleReady = true;
  }

  /** 波纹圆心：点击点 > 触发按钮中心 > 屏幕中心。 */
  function originOf(event) {
    if (event && Number.isFinite(event.clientX) && (event.clientX || event.clientY)) {
      return { x: event.clientX, y: event.clientY };
    }
    const el = event && event.currentTarget;
    if (el && el.getBoundingClientRect) {
      const r = el.getBoundingClientRect();
      return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
    }
    return { x: window.innerWidth / 2, y: window.innerHeight / 2 };
  }

  function circleAt(p, radius) {
    return `circle(${radius}px at ${Math.round(p.x)}px ${Math.round(p.y)}px)`;
  }

  function endRadiusOf(p) {
    return Math.ceil(Math.hypot(
      Math.max(p.x, window.innerWidth - p.x),
      Math.max(p.y, window.innerHeight - p.y)));
  }

  /** 降级路径：全屏 overlay（暗色底）做 clip-path 展开，播完再换主题。 */
  function playOverlayRipple(p, goingDark) {
    const endRadius = endRadiusOf(p);
    const clip = goingDark
      ? [circleAt(p, 0), circleAt(p, endRadius)]     // 变暗：暗色从圆心荡开
      : [circleAt(p, endRadius), circleAt(p, 0)];    // 回浅色：暗色向圆心收拢
    const overlay = document.createElement('div');
    overlay.setAttribute('aria-hidden', 'true');
    overlay.style.cssText =
      'position:fixed;inset:0;pointer-events:none;z-index:2147483646;' +
      'background:#141414;clip-path:' + clip[0];
    document.body.appendChild(overlay);
    return new Promise(resolve => {
      const anim = overlay.animate(
        { clipPath: clip },
        { duration: RIPPLE_MS, easing: 'ease-in-out', fill: 'forwards' });
      const done = () => { overlay.remove(); resolve(); };
      anim.addEventListener('finish', done, { once: true });
      anim.addEventListener('cancel', done, { once: true });
    });
  }

  /** 对外入口：set('dark'|'light', event)。event 用于波纹圆心定位。 */
  function set(value, event) {
    const next = value === 'dark' ? 'dark' : 'light';
    if (next === theme) {
      render();
      return;
    }
    const goingDark = next === 'dark';

    if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      apply(next);
      return;
    }

    const p = originOf(event);
    if (typeof document.startViewTransition !== 'function') {
      // 降级：overlay 波纹。变暗先荡开再换；回浅色先换再让暗色收拢。
      const seq = goingDark
        ? playOverlayRipple(p, true).then(() => apply(next))
        : Promise.resolve(apply(next)).then(() => playOverlayRipple(p, false));
      seq.catch(() => apply(next));
      return;
    }

    // 主路径：View Transitions + clip-path 圆形展开
    ensureVtStyles();
    const endRadius = endRadiusOf(p);
    const clip = goingDark
      ? [circleAt(p, 0), circleAt(p, endRadius)]
      : [circleAt(p, endRadius), circleAt(p, 0)];
    const transitionClass = goingDark
      ? 'wb-theme-to-dark' : 'wb-theme-to-light';
    try {
      document.documentElement.classList.add(transitionClass);
      const transition = document.startViewTransition(() => apply(next));
      transition.finished.finally(() => {
        document.documentElement.classList.remove(transitionClass);
      });
      transition.ready.then(() => {
        try {
          document.documentElement.animate(
            { clipPath: clip },
            {
              duration: RIPPLE_MS,
              easing: 'ease-in-out',
              fill: 'forwards',
              pseudoElement: goingDark
                ? '::view-transition-new(root)'
                : '::view-transition-old(root)',
            });
        } catch (_) { /* 伪元素动画不支持时保持默认交叉淡化 */ }
      }).catch(() => {});
    } catch (_) {
      apply(next);
    }
  }

  render();
  document.addEventListener('DOMContentLoaded', render);
  window.addEventListener('storage', event => {
    if (event.key === key || event.key === null) {
      theme = event.newValue === 'dark' ? 'dark' : 'light';
      render();
    }
  });
  return { set };
})();
