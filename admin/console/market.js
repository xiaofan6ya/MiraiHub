/* ==========================================================================
   模型广场（参考 new-api 的 Model Square 卡片）
   --------------------------------------------------------------------------
   每张卡回答四个问题：**这个模型多少钱、现在能不能用、最近快不快、
   怎么调用**。数据分两路取：

     /api/stats/model-market     近 N 小时用量 + 定价 + 可用账号数（一次聚合）
     /api/models/aggregate       跨平台归一、各平台的「完整调用名」

   为什么不在一个接口里全做完：聚合视图是模型配置页的既有能力（带区/别名
   归一逻辑），广场只借用它的 `client_id`，不重复实现一遍归一规则。
   ========================================================================== */
(function () {
  "use strict";

  var state = {
    hours: 24,
    platform: "",
    query: "",
    onlyAvailable: false,
    sort: "requests",     // requests | price | availability | name
    view: "cards",        // cards | list
    data: null,
    aggregate: null,
    loading: false,
    loaded: false,
    seq: 0,
  };

  function el(id) { return document.getElementById(id); }

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function toast(message, kind) {
    if (typeof window.toast === "function") window.toast(message, kind);
  }

  function num(value) {
    var n = Number(value) || 0;
    if (Math.abs(n) >= 1e8) return (n / 1e8).toFixed(2) + "亿";
    if (Math.abs(n) >= 1e4) return (n / 1e4).toFixed(1) + "万";
    return n.toLocaleString("zh-CN");
  }

  function credit(value) {
    var n = Number(value);
    if (!Number.isFinite(n)) return "—";
    return n.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function pct(value, digits) {
    if (value === null || value === undefined) return "—";
    return (Number(value) * 100).toFixed(digits == null ? 2 : digits) + "%";
  }

  function latency(ms) {
    var n = Number(ms) || 0;
    if (!n) return "—";
    if (n < 1000) return Math.round(n) + " ms";
    if (n < 60000) return (n / 1000).toFixed(2) + " 秒";
    return Math.floor(n / 60000) + " 分 " + Math.round((n % 60000) / 1000) + " 秒";
  }

  function price(item) {
    if (item.free) return "免费";
    if (!item.pricing || !item.pricing.length) return "未定价";
    return "×" + Number(item.min_multiplier || 0).toFixed(2);
  }

  function rateClass(value) {
    if (value === null || value === undefined) return "wbd-neutral";
    if (value >= 0.995) return "wbd-ok";
    if (value >= 0.97) return "wbd-warn";
    return "wbd-danger";
  }

  /** 把 aggregate 的 client_id 按 canonical key 索引起来。 */
  function clientIds(aggregate) {
    var map = {};
    ((aggregate && aggregate.items) || []).forEach(function (item) {
      var key = item.key || item.name;
      var ids = (item.platforms || []).map(function (row) {
        return {
          platform: row.platform,
          platform_label: row.platform_label || row.platform,
          model_id: row.client_id || row.model_id,
          multiplier: row.multiplier,
        };
      });
      map[key] = ids;
    });
    return map;
  }

  function keyOf(item) {
    return String(item.key || item.name || "").toLowerCase();
  }

  function lookup(map, item) {
    var direct = map[item.key];
    if (direct) return direct;
    var target = keyOf(item);
    var found = null;
    Object.keys(map).forEach(function (key) {
      if (!found && key.toLowerCase() === target) found = map[key];
    });
    return found || [];
  }

  function visible(items) {
    var query = state.query.trim().toLowerCase();
    var out = items.filter(function (item) {
      if (state.onlyAvailable && !item.available) return false;
      if (state.platform && (item.platforms || []).indexOf(state.platform) < 0) return false;
      if (!query) return true;
      var haystack = [item.name, item.key].concat(item.models || []).join(" ").toLowerCase();
      return haystack.indexOf(query) >= 0;
    });
    var sorters = {
      requests: function (a, b) { return b.requests - a.requests; },
      price: function (a, b) { return a.min_multiplier - b.min_multiplier; },
      availability: function (a, b) {
        return (b.active_accounts - b.exhausted_accounts) - (a.active_accounts - a.exhausted_accounts);
      },
      name: function (a, b) { return String(a.name).localeCompare(String(b.name), "zh-CN"); },
    };
    return out.sort(sorters[state.sort] || sorters.requests);
  }

  function statusBar(item) {
    var value = item.success_rate === null || item.success_rate === undefined ? 0 : item.success_rate;
    var color = value >= 0.995 ? "var(--ok)" : value >= 0.97 ? "var(--warn)" : "var(--danger)";
    return '<span class="wbm-bar"><i style="width:' + Math.max(2, value * 100).toFixed(1) +
      "%;background:" + color + '"></i></span>';
  }

  function card(item, ids) {
    var primary = ids[0] || { model_id: (item.models || [])[0] || item.key, platform_label: "" };
    var platforms = (item.platform_labels || item.platforms || []).map(function (label) {
      return '<span class="wbm-chip">' + esc(label) + "</span>";
    }).join("");
    var capacity = Math.max(0, item.active_accounts - item.exhausted_accounts);
    return '<article class="wbm-card' + (item.available ? "" : " is-off") + '">' +
      '<header class="wbm-card-head">' +
        '<div class="wbm-title"><h3>' + esc(item.name) + "</h3>" +
          '<div class="wbm-chips">' + platforms + "</div></div>" +
        '<button type="button" class="wbm-copy" title="复制调用名" data-copy="' +
          esc(primary.model_id) + '">复制</button>' +
      "</header>" +
      '<div class="wbm-price"><b>' + price(item) + "</b><span>每千 Token 倍率</span></div>" +
      '<dl class="wbm-stats">' +
        "<div><dt>24h 请求</dt><dd>" + num(item.requests) + "</dd></div>" +
        "<div><dt>成功率</dt><dd class=\"" + rateClass(item.success_rate) + '">' +
          pct(item.success_rate) + "</dd></div>" +
        "<div><dt>平均延迟</dt><dd>" + latency(item.avg_latency_ms) + "</dd></div>" +
        "<div><dt>吞吐</dt><dd>" + (item.tps ? item.tps.toFixed(1) + " t/s" : "—") + "</dd></div>" +
        "<div><dt>首字</dt><dd>" + latency(item.avg_ttfb_ms) + "</dd></div>" +
        "<div><dt>积分</dt><dd>" + credit(item.credits) + "</dd></div>" +
      "</dl>" +
      statusBar(item) +
      '<footer class="wbm-foot">' +
        '<span class="' + (item.available ? "wbd-ok" : "wbd-danger") + '">' +
          (item.available ? "可用账号 " + capacity : "当前不可用") + "</span>" +
        (item.exhausted_accounts ? "<span>额度耗尽 " + item.exhausted_accounts + "</span>" : "") +
        '<code title="' + esc(primary.model_id) + '">' + esc(primary.model_id) + "</code>" +
      "</footer>" +
      "</article>";
  }

  function listRow(item, ids) {
    var primary = ids[0] || {};
    return "<tr>" +
      '<td><div class="wbm-name">' + esc(item.name) + "</div>" +
        '<div class="wbm-sub">' + esc((item.platform_labels || []).join(" · ")) + "</div></td>" +
      "<td>" + price(item) + "</td>" +
      "<td class='num'>" + num(item.requests) + "</td>" +
      "<td class='num " + rateClass(item.success_rate) + "'>" + pct(item.success_rate) + "</td>" +
      "<td class='num'>" + latency(item.avg_latency_ms) + "</td>" +
      "<td class='num'>" + (item.tps ? item.tps.toFixed(1) : "—") + "</td>" +
      "<td class='num'>" + credit(item.credits) + "</td>" +
      "<td class='" + (item.available ? "wbd-ok" : "wbd-danger") + "'>" +
        (item.available ? "可用" : "不可用") + "</td>" +
      "<td><code class='wbm-code'>" + esc(primary.model_id || "") + "</code></td>" +
      "</tr>";
  }

  function render() {
    var host = el("wbmRoot");
    if (!host || !state.data) return;
    var ids = clientIds(state.aggregate);
    var rows = visible(state.data.items || []);
    var head = '<div class="wbm-summary"><span>窗口 ' + state.data.window_hours +
      " 小时</span><b>" + rows.length + "</b><span>个模型 · 可用 " +
      rows.filter(function (item) { return item.available; }).length + " · 数据源 " +
      (state.data.source === "rollup" ? "小时汇总" : "原始日志") + "</span></div>";

    if (!rows.length) {
      host.innerHTML = head + '<div class="wbd-empty">没有符合条件的模型</div>';
      return;
    }
    if (state.view === "list") {
      host.innerHTML = head + '<div class="wbm-table-wrap"><table class="tbl wbm-table"><thead><tr>' +
        "<th>模型</th><th>价格</th><th class='num'>请求</th><th class='num'>成功率</th>" +
        "<th class='num'>延迟</th><th class='num'>吞吐</th><th class='num'>积分</th>" +
        "<th>状态</th><th>调用名</th></tr></thead><tbody>" +
        rows.map(function (item) { return listRow(item, lookup(ids, item)); }).join("") +
        "</tbody></table></div>";
      return;
    }
    host.innerHTML = head + '<div class="wbm-grid">' +
      rows.map(function (item) { return card(item, lookup(ids, item)); }).join("") + "</div>";
  }

  async function load(force) {
    var host = el("wbmRoot");
    if (!host) return;
    if (!force && state.loaded) { render(); return; }
    var seq = ++state.seq;
    state.loading = true;
    if (!state.loaded) {
      host.innerHTML = '<div class="wbm-grid">' +
        new Array(7).join('<div class="wbm-card wbm-skeleton"></div>') + "</div>";
    }
    try {
      var results = await Promise.all([
        api("/api/stats/model-market?hours=" + state.hours),
        api("/api/models/aggregate").catch(function () { return null; }),
      ]);
      var market = await results[0].json();
      var aggregate = results[1] ? await results[1].json().catch(function () { return null; }) : null;
      if (seq !== state.seq) return;
      if (!results[0].ok) throw new Error(market.detail || ("HTTP " + results[0].status));
      state.data = market;
      state.aggregate = aggregate;
      state.loaded = true;
      render();
      bindFilters();
    } catch (error) {
      if (seq !== state.seq) return;
      host.innerHTML = '<div class="wbd-error">模型广场加载失败：' + esc(error.message) + "</div>";
      toast("模型广场加载失败", "err");
    } finally {
      if (seq === state.seq) state.loading = false;
    }
  }

  function setHours(hours) {
    state.hours = Number(hours) || 24;
    document.querySelectorAll("[data-wbm-hours]").forEach(function (button) {
      button.classList.toggle("active", Number(button.dataset.wbmHours) === state.hours);
    });
    load(true);
  }

  function setView(view) {
    state.view = view === "list" ? "list" : "cards";
    document.querySelectorAll("[data-wbm-view]").forEach(function (button) {
      button.classList.toggle("active", button.dataset.wbmView === state.view);
    });
    render();
  }

  function bindFilters() {
    var search = el("wbmSearch");
    if (search && !search.dataset.bound) {
      search.dataset.bound = "1";
      search.addEventListener("input", function () { state.query = search.value; render(); });
    }
    var available = el("wbmAvailable");
    if (available && !available.dataset.bound) {
      available.dataset.bound = "1";
      available.addEventListener("change", function () {
        state.onlyAvailable = available.checked;
        render();
      });
    }
    var platform = el("wbmPlatform");
    if (platform && !platform.dataset.bound) {
      platform.dataset.bound = "1";
      var options = [""].concat(((state.data && state.data.items) || [])
        .reduce(function (all, item) { return all.concat(item.platforms || []); }, [])
        .filter(function (value, index, list) { return value && list.indexOf(value) === index; }));
      var labels = {};
      ((state.data && state.data.items) || []).forEach(function (item) {
        (item.platforms || []).forEach(function (value, index) {
          labels[value] = (item.platform_labels || [])[index] || value;
        });
      });
      platform.innerHTML = options.map(function (value) {
        return '<option value="' + esc(value) + '">' +
          esc(value ? labels[value] || value : "全部平台") + "</option>";
      }).join("");
      platform.addEventListener("change", function () { state.platform = platform.value; render(); });
    }
    var sort = el("wbmSort");
    if (sort && !sort.dataset.bound) {
      sort.dataset.bound = "1";
      sort.addEventListener("change", function () { state.sort = sort.value; render(); });
    }
  }

  document.addEventListener("click", function (event) {
    var target = event.target.closest && event.target.closest("[data-copy]");
    if (!target) return;
    var text = target.dataset.copy;
    var write = navigator.clipboard && navigator.clipboard.writeText
      ? navigator.clipboard.writeText(text)
      : Promise.reject(new Error("no clipboard"));
    write.then(function () { toast("已复制 " + text, "ok"); })
      .catch(function () { toast("复制失败，请手动选择 " + text, "err"); });
  });

  window.WBModelMarket = {
    load: load,
    render: function () { bindFilters(); render(); },
    bindFilters: bindFilters,
    setHours: setHours,
    setView: setView,
    state: state,
  };
})();