/* ==========================================================================
   平台切换模块（账号池上方 · 卡片式 · 全局数据上下文）
   --------------------------------------------------------------------------
   设计要点（改之前先读这段）：

   1. **切平台 = 换全局上下文**，不是「给账号列表加个筛选」。
      切到 Trae 之后，使用记录 / 用量日志 / 积分到期 拿到的都只属于 Trae。
      实现方式是给所有请求挂 `X-Platform`，后端按账号归属收窄
      （见 admin/platform/scope.py）。
   2. **两个粒度**：平台（trae）与区域（trae:cn）。
      点平台卡片 = 整个平台（两个区都算）；再点区域 = 只看那个区。
   3. **卡片而不是下拉**：平台会越来越多，卡片能同时展示 logo / 名称 /
      账号数，扫一眼就知道哪个平台在跑、哪个是空的。
   4. **新增平台不用改这里**：平台与区域都由 `/api/platforms` 下发，
      logo 从 `platform-icons.js` 取，缺图时自动用占位图标。
   ========================================================================== */
(function () {
  "use strict";

  var STORE_KEY = "wb_platform_current";

  var state = {
    items: [],
    platform: "workbuddy",
    realm: "",
    counts: {},      // platform 或 platform:realm -> 账号数（取不到就不显示）
    loaded: false,
    busy: false,
  };
  var listeners = [];

  function $(sel) { return document.querySelector(sel); }

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (ch) {
      return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch];
    });
  }

  function specOf(id) {
    return state.items.filter(function (item) { return item.id === id; })[0] || null;
  }

  function icon(id) {
    return (window.WBPlatformIcons && window.WBPlatformIcons.get(id)) || "";
  }

  function storedToken() {
    return window.TOKEN || localStorage.getItem("wb_admin_token") || "";
  }

  function saved() {
    try {
      var raw = JSON.parse(localStorage.getItem(STORE_KEY) || "null");
      if (raw && raw.platform) return raw;
    } catch (e) { /* 本地值损坏时忽略 */ }
    return null;
  }

  function persist() {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify({
        platform: state.platform, realm: state.realm,
      }));
    } catch (e) { /* 隐私模式写不进去，不影响使用 */ }
  }

  /* 平台专属动作。
   *
   * **只有 Trae 有**：它的凭据来自 Trae 桌面端 `storage.json`，
   * 与 WorkBuddy 完全不同的路径，因此需要单独的「扫描 / 导入 / 刷新」入口。
   *
   * WorkBuddy 不在这里放按钮 —— 它的「手机号登录 / 导入 / 扫描本机」本来
   * 就在账号池页面顶部。在这里再放一份有两个问题：
   *   1. 移动端会带出桌面专属的扫描入口（`test_console_boot` 明确断言
   *      移动端不能出现 `scanLocal()`）；
   *   2. 同一动作两个入口，用户不知道该点哪个。
   */
  /**
 * 该平台能做哪些动作。
 *
 * **能力由后端声明**（`PlatformSpec.extra["actions"]` → `/api/platforms` 的
 * `actions` 字段），前端只负责渲染。
 *
 * 改造前这里写死「只有 trae 有按钮」，导致新增平台没有扫描/导入入口 ——
 * 用户根本没法把账号导进来。
 */
function actionsFor(spec) {
  if (window.WBPlatformActions) {
    return window.WBPlatformActions.actionsFor(spec);
  }
  return [];
}

  /* 图标表在 platform-actions.js 里（连同动作定义一起） */
  var ACTION_ICONS = (window.WBPlatformActions
    && window.WBPlatformActions.icons) || {};;

  function countFor(key) {
    var value = state.counts[key];
    return (value === undefined || value === null) ? null : value;
  }

  function render() {
    var host = $("#platformCards");
    if (!host) return;

    host.innerHTML = state.items.map(function (item) {
      var on = item.id === state.platform;
      var total = countFor(item.id);
      var sub = total === null ? "—" : total + " 个账号";
      // 单区平台（trae-cn / trae-intl）的 `realms` 为空，但 `realm` 有值——
      // 直接从 spec 的固定区域取标签，否则卡片上只写「1 个账号」，
      // 看不出它是国内版还是国际版。
      var realmLabels = (item.realms || []).length
        ? (item.realms || []).map(function (r) { return r.label; }).join(" / ")
        : ((item.realm_label || (item.realm === "cn" ? "国内版"
            : item.realm === "intl" ? "国际版" : "")) || "");
      return '<button type="button" class="platform-card' + (on ? " active" : "") +
        '" role="tab" aria-selected="' + (on ? "true" : "false") +
        '" data-platform="' + esc(item.id) + '" data-tooltip="' + esc(item.notes || item.label) + '">' +
        '<span class="platform-card-logo">' + icon(item.id) + "</span>" +
        '<span class="platform-card-body">' +
        '<span class="platform-card-name">' + esc(item.label) + "</span>" +
        '<span class="platform-card-sub">' + esc(sub) +
        (realmLabels ? " · " + esc(realmLabels) : "") +
        "</span></span>" +
        '<span class="platform-card-check" aria-hidden="true">' +
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3">' +
        '<path d="M20 6 9 17l-5-5"/></svg></span>' +
        "</button>";
    }).join("");

    // 区域：只有当前平台真的有多个区域时才出现
    var spec = specOf(state.platform);
    var realmHost = $("#platformRealmTabs");
    if (realmHost) {
      var realms = (spec && spec.realms) || [];
      if (realms.length > 1) {
        realmHost.hidden = false;
        realmHost.innerHTML =
          '<button type="button" class="platform-realm' + (state.realm ? "" : " active") +
          '" data-realm="">全部</button>' +
          realms.map(function (realm) {
            var on = realm.key === state.realm;
            return '<button type="button" class="platform-realm' + (on ? " active" : "") +
              '" data-realm="' + esc(realm.key) + '">' + esc(realm.label) + "</button>";
          }).join("");
      } else {
        realmHost.hidden = true;
        realmHost.innerHTML = "";
      }
    }

    var actionHost = $("#platformActions");
    if (actionHost) {
      actionHost.innerHTML = actionsFor(spec).map(function (action) {
        return '<button type="button" class="console-btn' +
          (action.primary ? " primary" : " quiet") + '" data-platform-action="' +
          esc(action.name) + '">' + (ACTION_ICONS[action.icon] || "") +
          esc(action.label) + "</button>";
      }).join("");
    }

    var note = $("#platformSwitchNote");
    if (note) note.textContent = (spec && spec.notes) || "";

    var subtitle = $("#accountPoolSubtitle");
    if (subtitle) {
      var label = spec ? spec.label : state.platform;
      var realmLabel = "";
      if (spec && state.realm) {
        (spec.realms || []).forEach(function (realm) {
          if (realm.key === state.realm) realmLabel = " · " + realm.label;
        });
      }
      subtitle.textContent = "当前平台：" + label + realmLabel +
        "；账号、用量、积分与日志都只统计这个平台。";
    }
  }

  function emit() {
    var detail = {
      platform: state.platform,
      realm: state.realm,
      spec: specOf(state.platform),
      selector: window.WBPlatform.route(),
    };
    listeners.forEach(function (fn) {
      try { fn(detail); } catch (e) { console.error("[platform] listener failed", e); }
    });
    try {
      document.dispatchEvent(new CustomEvent("wb-platform-change", { detail: detail }));
    } catch (e) { /* 老浏览器没有 CustomEvent 构造器 */ }
  }

  function select(platform, realm, opts) {
    var spec = specOf(platform);
    if (!spec) return;
    var nextRealm = realm || "";
    if (nextRealm) {
      var valid = (spec.realms || []).some(function (r) { return r.key === nextRealm; });
      if (!valid) nextRealm = "";
    }
    var changed = platform !== state.platform || nextRealm !== state.realm;
    state.platform = platform;
    state.realm = nextRealm;
    persist();
    render();
    if (changed || (opts && opts.force)) {
      // 首屏加载（opts.silent）也要通知一次：默认面板就是平台管理，
      // 概览卡片得等平台解析完才有数据，不发事件它会一直空着。
      emit();
      // 账号数在 emit 之后再拉一次：切换过程中/新平台首次进入时，
      // account_count 可能还没回来，补一次让卡片上的数字不会是 "—"。
      //
      // ⚠️ 这里必须带 `true`（重新请求 /api/platforms），不能只重算缓存：
      // 「授权登录 / 导入 / 扫描」成功后前端会 `select()` 切到目标平台，
      // 那一刻账号是刚写进库的，而 `state.items` 还是页面打开时的旧数据 ——
      // 重算只能得到旧数字，表现就是「账号导进去了，卡片上的数量没变」
      // （2026-10-31 实测）。
      loadCounts(true);
    }
  }

  /* 账号数：`/api/platforms` 已经随平台一起下发（account_count / realm_counts），
     但那是**上一次请求的缓存**。导入 / 删除账号后必须重新拉一次，
     否则卡片上的数字永远停在页面打开那一刻 —— 只有整页刷新才变
     （这是「导入账号后底部账号数不变」的真正原因，2026-10-31 实测确认）。
     下发的数字缺失时才显示 "—"，绝不猜。 */
  function applyCounts(items) {
    state.counts = {};
    (items || []).forEach(function (item) {
      if (item.account_count === undefined || item.account_count === null) return;
      state.counts[item.id] = item.account_count;
      var perRealm = item.realm_counts || {};
      Object.keys(perRealm).forEach(function (realm) {
        state.counts[item.id + ":" + realm] = perRealm[realm];
      });
    });
  }

  /**
   * @param {boolean} refresh
   *   true  = 重新请求 `/api/platforms`（导入/删除/刷新账号后调）
   *   false = 用已经加载的 items 重算（切平台时调，省一次请求）
   */
  function loadCounts(refresh) {
    if (!refresh) {
      applyCounts(state.items);
      render();
      return Promise.resolve(state);
    }
    return fetch("/api/platforms", { headers: { "X-Admin-Token": storedToken() } })
      .then(function (res) { return res.ok ? res.json() : null; })
      .then(function (data) {
        if (data && data.items && data.items.length) {
          // 顺带更新 spec：账号数之外的能力声明 / 区域也会跟着变
          state.items = data.items;
        }
        applyCounts(state.items);
        render();
        return state;
      })
      .catch(function () {
        applyCounts(state.items);   // 网络失败时退回旧数字，不清零
        render();
        return state;
      });
  }

  function bind() {
    var panel = $("#platformSwitch");
    if (!panel || panel.dataset.bound === "1") return;
    panel.dataset.bound = "1";
    panel.addEventListener("click", function (event) {
      var card = event.target.closest("[data-platform]");
      if (card) { select(card.dataset.platform); return; }
      var realm = event.target.closest("[data-realm]");
      if (realm) { select(state.platform, realm.dataset.realm); return; }
      var action = event.target.closest("[data-platform-action]");
      if (action) { window.WBPlatform.action(action.dataset.platformAction); }
    });
  }

  window.WBPlatform = {
    /**
     * 切换平台（供外部调用）。
     *
     * `select` 原本只是模块内部函数，只有卡片点击时才被调用。
     * 「概览底部的平台状态点击切换」这类外部场景需要它，所以显式导出 ——
     * 否则外部只能自己改 localStorage，那会绕过 `emit()` 通知，
     * 导致其它面板不刷新（实测报错：`WBPlatform.select is not a function`）。
     *
     * 返回是否真的发生了切换（同一平台重复调用返回 false）。
     */
    select: function (platform, realm) {
      var before = state.platform + "|" + state.realm;
      select(platform, realm);
      return before !== (state.platform + "|" + state.realm);
    },
    enter: function () {
      bind();
      return window.WBPlatform.load();
    },
    load: function () {
      if (state.busy) return Promise.resolve(state);
      state.busy = true;
      return fetch("/api/platforms", { headers: { "X-Admin-Token": storedToken() } })
        .then(function (res) { return res.ok ? res.json() : null; })
        .then(function (data) {
          state.busy = false;
          if (!data || !data.items) {
            state.items = [{ id: "workbuddy", label: "WorkBuddy", realms: [] }];
          } else {
            state.items = data.items;
          }
          var picked = saved() || {};
          var exists = state.items.some(function (item) { return item.id === picked.platform; });
          var platform = exists ? picked.platform : (state.items[0] || {}).id || "workbuddy";
          state.loaded = true;
          select(platform, exists ? picked.realm : "", { force: true });
          return loadCounts().then(function () { return state; });        })
        .catch(function (err) {
          state.busy = false;
          state.items = [{ id: "workbuddy", label: "WorkBuddy", realms: [] }];
          select("workbuddy", "", { force: true });
          console.warn("[platform] 平台列表加载失败", err);
          return state;
        });
    },
    current: function () { return { platform: state.platform, realm: state.realm }; },
    spec: function () { return specOf(state.platform); },
    selector: function () { return window.WBPlatform.route(); },
    onChange: function (fn) {
      if (typeof fn === "function") listeners.push(fn);
      return function () {
        listeners = listeners.filter(function (item) { return item !== fn; });
      };
    },
    /** 给 /v1 或后端统计用的选择器：trae:cn / trae / workbuddy */
    route: function () {
      return state.realm ? state.platform + ":" + state.realm : state.platform;
    },
    /** 统计接口统一带上它 —— 这就是「切平台 = 换全局上下文」的落点。 */
    header: function () {
      return { "X-Platform": window.WBPlatform.route() };
    },
    refreshCounts: function () { return loadCounts(true); },
    action: function (name) {
      document.dispatchEvent(new CustomEvent("wb-platform-action", {
        // 带上 spec：`platform` 是对外 id（trae-cn / trae-intl），
        // 而业务处理器要的是**落库平台**（trae），两者必须一起给出，
        // 否则处理器只能靠字符串前缀猜。
        detail: { action: name, platform: state.platform, realm: state.realm,
                  spec: specOf(state.platform) },
      }));
    },
  };
})();
