/* ==========================================================================
   数据看板（对齐 new-api 「模型调用分析」一屏的口径）
   --------------------------------------------------------------------------
   参考 new-api 的 Dashboard 截图，把它那一屏的信息结构搬过来，映射到本网关
   的数据模型（usage_logs：platform / account / api_key / model / use_case /
   tokens / cached / credits / ttfb / latency / error_kind）：

     KPI 一排 7 张      总数 · 总积分 · 总 Token · 缓存读取 · 平均 RPM · 平均 TPM · 成功率
     性能健康条        成功率 · 平均延迟 · 吞吐(t/s) · 首字(TTFB) + 每模型成功率
     消耗分布          按模型堆叠的柱状 / 面积图（可切换）
     模型调用分析      调用趋势 / 调用次数分布 / 调用次数排行（三个视图切换）
     辅助两块          平台贡献（含成功率） · 失败分类

   数据只来自 `/api/stats/dashboard` 一次请求 —— 所有卡片与图共用同一份
   where 条件，避免「卡片和图形对不上」这种最伤信任的问题。

   为什么是独立脚本而不是塞进 console-app.js：
   那个文件已经 1.6 万行，看板是**纯新增的一屏**，独立文件可以单独修、
   单独加载，出问题也不会把主控制台带崩。
   ========================================================================== */
(function () {
  "use strict";

  var state = {
    days: 1,
    granularity: "hour",
    platform: "",
    distMode: "bar",        // bar | area
    analysisMode: "trend",  // trend | distribution | ranking
    data: null,
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

  /* ── 数字格式 ────────────────────────────────────────────────────────── */

  function cnum(value) {
    var n = Number(value) || 0;
    var abs = Math.abs(n);
    if (abs >= 1e8) return (n / 1e8).toFixed(2) + "亿";
    if (abs >= 1e4) return (n / 1e4).toFixed(1) + "万";
    return n.toLocaleString("zh-CN");
  }

  function exact(value) {
    return (Number(value) || 0).toLocaleString("zh-CN");
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

  /** 桶标签：小时只显示 时:分，按天显示 月-日。 */
  function bucketLabel(key) {
    var text = String(key || "");
    if (text.length >= 13) return text.slice(5, 13).replace("-", "/");
    return text.slice(5);
  }

  function rateClass(value) {
    if (value === null || value === undefined) return "wbd-neutral";
    if (value >= 0.995) return "wbd-ok";
    if (value >= 0.97) return "wbd-warn";
    return "wbd-danger";
  }

  /* ── 颜色 ────────────────────────────────────────────────────────────── */

  var FALLBACK_COLORS = ["#3E6A99", "#33a6a0", "#526fb2", "#9379c2", "#ce9a43",
                         "#cb6c6d", "#4492bd", "#86a152"];

  function palette() {
    var styles = getComputedStyle(document.documentElement);
    var out = [];
    for (var i = 1; i <= 8; i += 1) {
      var value = styles.getPropertyValue("--c" + i).trim();
      out.push(value || FALLBACK_COLORS[i - 1]);
    }
    return out;
  }

  /* ── SVG 小工具 ──────────────────────────────────────────────────────── */

  var SVG_NS = "http://www.w3.org/2000/svg";
  var CHART_H = 232;
  var PAD = { top: 12, right: 12, bottom: 26, left: 46 };

  function node(name, attrs) {
    var element = document.createElementNS(SVG_NS, name);
    Object.keys(attrs || {}).forEach(function (key) { element.setAttribute(key, attrs[key]); });
    return element;
  }

  function niceMax(value) {
    var v = Number(value) || 0;
    if (v <= 0) return 1;
    var magnitude = Math.pow(10, Math.floor(Math.log10(v)));
    var scaled = v / magnitude;
    var step = scaled <= 1 ? 1 : scaled <= 2 ? 2 : scaled <= 5 ? 5 : 10;
    return step * magnitude;
  }

  function withTooltip(element, text) {
    var title = node("title", {});
    title.textContent = text;
    element.appendChild(title);
    return element;
  }

  /**
   * 画一张图：`draw(svg, plot)` 只负责画内容，外层负责尺寸/坐标轴/网格。
   * 一次性生成完整 SVG（而不是逐帧 append），重绘时整块替换 —— 避免旧节点残留。
   */
  function chart(host, build) {
    if (!host) return;
    host.replaceChildren();
    var width = Math.max(320, host.clientWidth || 640);
    var height = CHART_H;
    var svg = node("svg", {
      viewBox: "0 0 " + width + " " + height,
      width: "100%", height: CHART_H, role: "img",
      preserveAspectRatio: "none", class: "wbd-svg",
    });
    var plot = {
      left: PAD.left, right: width - PAD.right,
      top: PAD.top, bottom: height - PAD.bottom,
      width: width - PAD.left - PAD.right,
      height: height - PAD.top - PAD.bottom,
      width_: width, height_: height,
      grid: function (max, format) {
        for (var i = 0; i <= 4; i += 1) {
          var value = (max / 4) * i;
          var y = plot.bottom - (plot.height * i) / 4;
          svg.appendChild(node("line", {
            x1: plot.left, x2: plot.right, y1: y, y2: y, class: "wbd-grid-line",
          }));
          var label = node("text", { x: plot.left - 6, y: y + 3, class: "wbd-axis", "text-anchor": "end" });
          label.textContent = format(value);
          svg.appendChild(label);
        }
      },
      svg: svg,
    };
    build(svg, plot);
    host.appendChild(svg);
  }

  /** 横轴刻度：最多 7 个，避免小时桶挤成实心带。 */
  function drawXLabels(svg, plot, buckets) {
    if (!buckets.length) return;
    // 窄屏（手机）只放 4 个刻度：7 个 10px 的标签在 390px 上会叠成一团。
    var maxLabels = plot.width < 520 ? 4 : 7;
    var step = Math.max(1, Math.ceil(buckets.length / maxLabels));
    var slot = plot.width / Math.max(1, buckets.length);
    for (var i = 0; i < buckets.length; i += step) {
      var text = node("text", {
        x: plot.left + slot * (i + 0.5), y: plot.bottom + 16,
        class: "wbd-axis", "text-anchor": "middle",
      });
      text.textContent = bucketLabel(buckets[i]);
      svg.appendChild(text);
    }
  }

  /* ── 堆叠柱 / 堆叠面积 ───────────────────────────────────────────────── */

  function drawStacked(host, buckets, series, mode, unit) {
    var colors = palette();
    chart(host, function (svg, plot) {
      var totals = buckets.map(function (_, index) {
        return series.reduce(function (sum, item) { return sum + (Number(item.values[index]) || 0); }, 0);
      });
      var max = niceMax(Math.max.apply(null, totals.concat([0])));
      var fmt = unit === "credits" ? function (v) { return credit(v); } : cnum;
      plot.grid(max, fmt);

      var slot = plot.width / Math.max(1, buckets.length);
      var barWidth = Math.max(1.5, Math.min(18, slot * 0.62));

      if (mode === "area") {
        // 堆叠面积：从下往上累加，每一层自己的上下边界都算出来再一次性画。
        var cumulative = buckets.map(function () { return 0; });
        series.forEach(function (item, index) {
          var color = colors[index % colors.length];
          var upper = [];
          var lower = [];
          for (var i = 0; i < buckets.length; i += 1) {
            var value = Number(item.values[i]) || 0;
            var before = cumulative[i];
            cumulative[i] = before + value;
            var x = plot.left + slot * (i + 0.5);
            upper.push([x, plot.bottom - (cumulative[i] / max) * plot.height]);
            lower.push([x, plot.bottom - (before / max) * plot.height]);
          }
          var d = "M" + upper.map(function (pair) { return pair[0] + "," + pair[1]; }).join("L") +
            "L" + lower.reverse().map(function (pair) { return pair[0] + "," + pair[1]; }).join("L") + "Z";
          svg.appendChild(node("path", {
            d: d, fill: color, "fill-opacity": 0.55, stroke: color, "stroke-width": 1,
          }));
        });
      } else {
        for (var i = 0; i < buckets.length; i += 1) {
          var base = plot.bottom;
          var x = plot.left + slot * (i + 0.5) - barWidth / 2;
          series.forEach(function (item, index) {
            var value = Number(item.values[i]) || 0;
            if (!value) return;
            var h = (value / max) * plot.height;
            base -= h;
            var rect = node("rect", {
              x: x, y: base, width: barWidth, height: Math.max(0.8, h),
              fill: colors[index % colors.length], rx: Math.min(2, barWidth / 3),
            });
            svg.appendChild(withTooltip(rect, item.name + " · " + fmt(value) + " · " + bucketLabel(buckets[i])));
          });
        }
      }
      drawXLabels(svg, plot, buckets);
    });
  }

  /* ── 多线趋势 ────────────────────────────────────────────────────────── */

  function drawLines(host, buckets, series, valueKey, unit) {
    var colors = palette();
    chart(host, function (svg, plot) {
      var values = [];
      series.forEach(function (item) {
        (item[valueKey] || []).forEach(function (value) { values.push(Number(value) || 0); });
      });
      var max = niceMax(Math.max.apply(null, values.concat([0])));
      var fmt = unit === "credits" ? function (v) { return credit(v); } : cnum;
      plot.grid(max, fmt);

      var slot = plot.width / Math.max(1, buckets.length);
      series.forEach(function (item, index) {
        var points = (item[valueKey] || []).map(function (value, i) {
          return [plot.left + slot * (i + 0.5), plot.bottom - ((Number(value) || 0) / max) * plot.height];
        });
        if (!points.length) return;
        var color = colors[index % colors.length];
        svg.appendChild(node("polyline", {
          points: points.map(function (pair) { return pair[0] + "," + pair[1]; }).join(" "),
          fill: "none", stroke: color, "stroke-width": 1.8,
          "stroke-linejoin": "round", "stroke-linecap": "round",
        }));
        points.forEach(function (pair, i) {
          var dot = node("circle", { cx: pair[0], cy: pair[1], r: 2.2, fill: color });
          svg.appendChild(withTooltip(dot, item.name + " · " + bucketLabel(buckets[i]) + " · " + fmt(item[valueKey][i])));
        });
      });
      drawXLabels(svg, plot, buckets);
    });
  }

  /* ── 排行列表 ────────────────────────────────────────────────────────── */

  function renderRanking(rows) {
    if (!rows || !rows.length) return '<div class="wbd-empty">窗口内没有调用记录</div>';
    var colors = palette();
    return '<div class="wbd-rank">' + rows.slice(0, 12).map(function (row, index) {
      var width = Math.max(1.5, (row.share || 0) * 100);
      return '<div class="wbd-rank-row">' +
        '<span class="wbd-rank-no">' + (index + 1) + '</span>' +
        '<span class="wbd-rank-name" title="' + esc(row.model) + '">' + esc(row.label || row.model || "未知模型") + '</span>' +
        '<span class="wbd-rank-bar"><i style="width:' + width.toFixed(2) + '%;background:' +
          colors[index % colors.length] + '"></i></span>' +
        '<span class="wbd-rank-num">' + exact(row.requests) + '</span>' +
        '<span class="wbd-rank-pct">' + pct(row.share, 1) + '</span>' +
        '</div>';
    }).join("") + "</div>";
  }

  /* ── 渲染 ────────────────────────────────────────────────────────────── */

  function segmented(items, active, action) {
    return '<div class="wbd-seg">' + items.map(function (item) {
      return '<button type="button" class="' + (item.value === active ? "active" : "") +
        '" onclick="WBStatsDashboard.' + action + '(\'' + item.value + '\')">' + esc(item.label) + "</button>";
    }).join("") + "</div>";
  }

  function render(data) {
    var host = el("wbdRoot");
    if (!host) return;
    var summary = data.summary || {};
    var models = data.models || [];
    var topModels = models.slice(0, 8);
    var distribution = data.distribution || { buckets: [], series: [] };
    var trend = data.trend || { buckets: [], series: [], total_requests: [] };
    var totalCredit = summary.credits || 0;
    var distUnit = distribution.series.length && distribution.series[0].metric === "tokens" ? "tokens" : "credits";

    host.innerHTML =
      /* KPI 一排：对齐 new-api 顶部 7 张 */
      '<section class="wbd-kpis wbd-enter" style="--i:0">' +
        kpi("Σ", "info", "总数", exact(summary.requests), "请求数（含失败）",
            summary.requests, "int") +
        kpi("¥", "ok", "总积分", credit(summary.credits), "成功请求消耗",
            summary.credits, "credit") +
        kpi("T", "brand", "总 Token", cnum(summary.total_tokens),
            "入 " + cnum(summary.prompt_tokens) + " · 出 " + cnum(summary.completion_tokens),
            summary.total_tokens, "compact") +
        kpi("C", "purple", "缓存读取", cnum(summary.cached_tokens),
            summary.cache_hit_rate == null ? "无样本" : "命中率 " + pct(summary.cache_hit_rate, 1),
            summary.cached_tokens, "compact") +
        kpi("RPM", "teal", "平均 RPM", cnum(summary.rpm), "每分钟请求数",
            summary.rpm, "num2") +
        kpi("TPM", "amber", "平均 TPM", cnum(summary.tpm), "每分钟 Token 数",
            summary.tpm, "num2") +
        kpi("✓", rateTone(summary.success_rate), "成功率", pct(summary.success_rate),
            "成功 " + exact(summary.successes) + " · 失败 " + exact(summary.failures),
            summary.success_rate, "pct") +
      "</section>" +

      /* 性能健康条 */
      '<section class="wbd-health wbd-enter" style="--i:1">' +
        '<span class="wbd-health-title">性能健康</span>' +
        '<span class="wbd-health-cell">成功率 <b class="' + rateClass(summary.success_rate) + '">' + pct(summary.success_rate) + "</b></span>" +
        '<span class="wbd-health-cell">平均延迟 <b>' + latency(summary.avg_latency_ms) + "</b></span>" +
        '<span class="wbd-health-cell">首字 <b>' + latency(summary.avg_ttfb_ms) + "</b></span>" +
        '<span class="wbd-health-cell">吞吐 <b>' + (summary.throughput_tps ? summary.throughput_tps.toFixed(1) + " t/s" : "—") + "</b></span>" +
        '<div class="wbd-health-models">' + topModels.map(function (row) {
          return '<span class="wbd-model-chip" title="' + esc(row.model) + ' · ' + exact(row.requests) + ' 次调用">' +
            '<i class="' + rateClass(row.success_rate) + '"></i>' + esc(row.label || row.model || "—") +
            " " + pct(row.success_rate, 2) + "</span>";
        }).join("") + "</div>" +
      "</section>" +

      /* 消耗分布 */
      '<section class="wbd-card wbd-enter" style="--i:2">' +
        '<header class="wbd-card-head">' +
          '<div class="wbd-card-title">消耗分布' +
            '<span class="wbd-card-total">' + (distUnit === "credits"
              ? "总计 " + credit(totalCredit)
              : "总计 " + cnum(summary.total_tokens) + " Token") + "</span></div>" +
          segmented([{ value: "bar", label: "柱状图" }, { value: "area", label: "面积图" }],
                    state.distMode, "setDistMode") +
        "</header>" +
        '<div class="wbd-legend">' + (distribution.series || []).map(function (item, index) {
          var colors = palette();
          return '<span class="wbd-legend-item"><i style="background:' +
            colors[index % colors.length] + '"></i>' + esc(item.name) + "</span>";
        }).join("") + "</div>" +
        '<div id="wbdDistChart" class="wbd-chart"></div>' +
      "</section>" +

      /* 模型调用分析 */
      '<section class="wbd-card wbd-enter" style="--i:3">' +
        '<header class="wbd-card-head">' +
          '<div class="wbd-card-title">模型调用分析' +
            '<span class="wbd-card-total">总计 ' + exact(summary.requests) + " 次调用</span></div>" +
          segmented([{ value: "trend", label: "调用趋势" },
                     { value: "distribution", label: "调用次数分布" },
                     { value: "ranking", label: "调用次数排行" },
                     { value: "platforms", label: "平台分流" }],
                    state.analysisMode, "setAnalysisMode") +
        "</header>" +
        (state.analysisMode === "ranking"
          ? '<div class="wbd-rank-wrap">' + renderRanking(data.ranking || []) + "</div>"
          : state.analysisMode === "platforms"
            ? '<div class="wbd-split-wrap">' + renderPlatformSplit(data.platforms || [], summary) + "</div>"
            : '<div class="wbd-legend">' + (trend.series || []).map(function (item, index) {
                var colors = palette();
                return '<span class="wbd-legend-item"><i style="background:' +
                  colors[index % colors.length] + '"></i>' + esc(item.name) + "</span>";
              }).join("") + "</div>" +
              '<div id="wbdAnalysisChart" class="wbd-chart"></div>') +
      "</section>" +

      /* 平台贡献 + 失败分类 */
      '<div class="wbd-grid2 wbd-enter" style="--i:4">' +
        '<section class="wbd-card">' +
          '<header class="wbd-card-head"><div class="wbd-card-title">平台贡献</div>' +
            '<span class="wbd-muted">点击切换筛选</span></header>' +
          '<div class="wbd-platforms">' + ((data.platforms || []).map(function (row) {
            var active = (state.platform || "") === (row.value || "");
            return '<button type="button" class="wbd-platform' + (active ? " active" : "") +
              '" onclick="WBStatsDashboard.setPlatform(\'' + esc(row.value) + '\')">' +
              "<b>" + esc(row.label) + "</b>" +
              "<span>" + exact(row.requests) + " 次 · " + credit(row.credits) + " 积分</span>" +
              '<em class="' + rateClass(row.success_rate) + '">' + pct(row.success_rate, 2) + "</em>" +
              "</button>";
          }).join("") || '<div class="wbd-empty">窗口内没有平台流量</div>') + "</div>" +
        "</section>" +
        '<section class="wbd-card">' +
          '<header class="wbd-card-head"><div class="wbd-card-title">失败分类</div>' +
            '<span class="wbd-muted">error_kind</span></header>' +
          '<div class="wbd-errors">' + ((data.errors || []).length ? data.errors.map(function (row) {
            var share = summary.failures ? row.count / summary.failures : 0;
            return '<div class="wbd-error-row"><span>' + esc(row.kind) + "</span>" +
              '<i><b style="width:' + (share * 100).toFixed(1) + '%"></b></i>' +
              "<em>" + exact(row.count) + "</em></div>";
          }).join("") : '<div class="wbd-empty">窗口内没有失败请求</div>') + "</div>" +
        "</section>" +
      "</div>";

    // 图表在 DOM 就位后再画，否则 clientWidth 取不到。
    if (state.distMode === "area") {
      drawStacked(el("wbdDistChart"), distribution.buckets, distribution.series, "area", distUnit);
    } else {
      drawStacked(el("wbdDistChart"), distribution.buckets, distribution.series, "bar", distUnit);
    }
    if (state.analysisMode === "trend") {
      drawLines(el("wbdAnalysisChart"), trend.buckets, trend.series, "requests", "requests");
    } else if (state.analysisMode === "distribution") {
      drawStacked(el("wbdAnalysisChart"), trend.buckets,
        [{ name: "调用次数", values: trend.total_requests || [] }], "bar", "requests");
    }
    // 数字动效放在图表之后：先让骨架/图形就位，再滚数字，视觉上更稳。
    requestAnimationFrame(animateKpis);
  }

  /* ── KPI 数字动效 ────────────────────────────────────────────────────── */

var KPI_FORMATTERS = {
  int: function (value) { return Math.round(value).toLocaleString("zh-CN"); },
  // RPM/TPM 用同一套缩写（万/亿）：否则动画结束时会从「99.3万」跳成
  // 「993,055.56」，在窄卡片里被省略号截断，看起来像数据没加载完。
  num2: function (value) { return cnum(value); },
  credit: function (value) { return credit(value); },
  compact: function (value) { return cnum(value); },
  pct: function (value) { return (value * 100).toFixed(2) + "%"; },
};

/**
 * KPI 从 0 滚到目标值。
 *
 * 为什么要动效：这排数字是「结论」，直接跳变会让人怀疑是不是没刷新；
 * 从 0 滚上去能明确传达「这是刚取回来的新数据」。620ms + easeOutCubic，
 * 再长就拖沓；`prefers-reduced-motion` 下直接落到终值（无障碍要求）。
 */
function animateKpis() {
  var reduce = window.matchMedia
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  document.querySelectorAll("[data-kpi-value]").forEach(function (node) {
    var target = Number(node.dataset.kpiValue);
    var format = KPI_FORMATTERS[node.dataset.kpiFormat] || KPI_FORMATTERS.int;
    if (!Number.isFinite(target)) return;
    if (reduce) { node.textContent = format(target); return; }
    var begin = performance.now();
    var duration = 620;
    var frame = function (now) {
      var t = Math.min(1, (now - begin) / duration);
      var eased = 1 - Math.pow(1 - t, 3);
      node.textContent = format(t < 1 ? target * eased : target);
      if (t < 1) requestAnimationFrame(frame);
    };
    requestAnimationFrame(frame);
  });
}

/* ── 平台分流 ────────────────────────────────────────────────────────── */

/** 横向条形：平台维度用 HTML 画（比 SVG 更容易对齐中文名与数字）。 */
function renderPlatformSplit(rows, summary) {
  if (!rows || !rows.length) return '<div class="wbd-empty">窗口内没有平台流量</div>';
  var max = Math.max.apply(null, rows.map(function (row) { return row.requests || 0; }).concat([1]));
  var colors = palette();
  return '<div class="wbd-split">' + rows.map(function (row, index) {
    var width = Math.max(2, ((row.requests || 0) / max) * 100);
    return '<div class="wbd-split-row">' +
      '<span class="wbd-split-name">' + esc(row.label) + "</span>" +
      '<span class="wbd-split-bar"><i style="width:' + width.toFixed(1) +
        "%;background:" + colors[index % colors.length] + '"></i></span>' +
      '<span class="wbd-split-num">' + exact(row.requests) + "</span>" +
      '<span class="wbd-split-share">' +
        pct(summary.requests ? row.requests / summary.requests : 0, 1) + "</span>" +
      '<span class="wbd-split-extra">' + credit(row.credits) + " 积分" +
        '<em class="' + rateClass(row.success_rate) + '">' + pct(row.success_rate, 2) + "</em></span>" +
      "</div>";
  }).join("") + "</div>";
}

function rateTone(rate) {
    if (rate === null || rate === undefined) return "info";
    if (rate >= 0.995) return "ok";
    if (rate >= 0.97) return "warn";
    return "danger";
  }

  function kpi(glyph, tone, label, value, note, raw, format) {
    var animate = (raw === null || raw === undefined || !Number.isFinite(Number(raw)))
      ? "" : ' data-kpi-value="' + Number(raw) + '" data-kpi-format="' + esc(format || "int") + '"';
    return '<div class="wbd-kpi wbd-kpi-' + tone + '">' +
      '<div class="wbd-kpi-head"><i class="wbd-kpi-glyph">' + esc(glyph) + "</i>" +
      '<span class="wbd-kpi-label">' + esc(label) + "</span></div>" +
      '<div class="wbd-kpi-value"' + animate + ">" + esc(value) + "</div>" +
      '<div class="wbd-kpi-note">' + esc(note) + "</div>" +
      "</div>";
  }

  /* ── 加载 / 交互 ─────────────────────────────────────────────────────── */

  async function load(force) {
    var host = el("wbdRoot");
    if (!host) return;
    if (!force && state.loaded && !state.loading) {
      // 二次进入：直接重画（主题色可能变了），不重复打接口。
      if (state.data) render(state.data);
      return;
    }
    var seq = ++state.seq;
    state.loading = true;
    host.classList.add("is-loading");
    syncToolbar();
    if (!state.loaded) {
      host.innerHTML = '<div class="wbd-skeleton"><div class="wbd-skeleton-kpis">' +
        new Array(8).join("<i></i>") + "</div><div class=\"wbd-skeleton-block\"></div>" +
        '<div class="wbd-skeleton-block"></div></div>';
    }
    try {
      var params = new URLSearchParams();
      params.set("days", String(state.days));
      params.set("granularity", state.granularity);
      if (state.platform) params.set("platform", state.platform);
      var response = await api("/api/stats/dashboard?" + params.toString());
      var data = await response.json().catch(function () { return {}; });
      if (seq !== state.seq) return;
      if (!response.ok) throw new Error(data.detail || ("HTTP " + response.status));
      state.data = data;
      state.loaded = true;
      render(data);
    } catch (error) {
      if (seq !== state.seq) return;
      host.innerHTML = '<div class="wbd-error">数据看板加载失败：' + esc(error.message) + "</div>";
      toast("数据看板加载失败", "err");
    } finally {
      if (seq === state.seq) {
        state.loading = false;
        host.classList.remove("is-loading");
      }
    }
  }

  function setDays(days) {
    state.days = Number(days) || 1;
    if (state.days > 7) state.granularity = "day";
    if (state.granularity === "hour" && state.days > 7) state.granularity = "day";
    syncToolbar();
    load(true);
  }

  function setGranularity(granularity) {
    if (granularity === "hour" && state.days > 7) {
      toast("超过 7 天只支持按天聚合", "err");
      return;
    }
    state.granularity = granularity === "day" ? "day" : "hour";
    syncToolbar();
    load(true);
  }

  function setPlatform(value) {
    state.platform = state.platform === value ? "" : value;
    load(true);
  }

  function setDistMode(mode) {
    state.distMode = mode === "area" ? "area" : "bar";
    if (state.data) render(state.data);
  }

  function setAnalysisMode(mode) {
    state.analysisMode = ["trend", "distribution", "ranking"].indexOf(mode) >= 0 ? mode : "trend";
    if (state.data) render(state.data);
  }

  function syncToolbar() {
    document.querySelectorAll("[data-wbd-days]").forEach(function (button) {
      button.classList.toggle("active", Number(button.dataset.wbdDays) === state.days);
    });
    document.querySelectorAll("[data-wbd-grain]").forEach(function (button) {
      button.classList.toggle("active", button.dataset.wbdGrain === state.granularity);
    });
  }

  /** 主题切换后重画：SVG 里的颜色是渲染时取的 CSS 变量，不重画会留旧色。 */
  function repaint() {
    if (state.data) render(state.data);
  }

  var resizeTimer = null;
  window.addEventListener("resize", function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(repaint, 160);
  });

  window.WBStatsDashboard = {
    load: load,
    repaint: repaint,
    setDays: setDays,
    setGranularity: setGranularity,
    setPlatform: setPlatform,
    setDistMode: setDistMode,
    setAnalysisMode: setAnalysisMode,
    state: state,
    // 测试钩子：让 verify_stats_dashboard.cjs 能单独验证图表绘制，不必等整页。
    debug: { drawStacked: drawStacked, drawLines: drawLines, palette: palette },
  };
})();