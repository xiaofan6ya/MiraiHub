/* ==========================================================================
   流量可视化（拓扑图 + 粒子动画）
   --------------------------------------------------------------------------
   参考 ai_studio_code 的做法：SVG 贝塞尔曲线 + `offset-path` 粒子沿路径移动。

   ## 为什么做成独立模块

   「平台网关调度策略」和「风控哨兵」都要这套动画，只是节点和流的含义不同：

     网关：Key ──> 网关 ──> 各平台（展示策略怎么选平台）
     哨兵：请求 ──> 哨兵 ──> 各平台（展示请求结果 / 风控信号）

   所以抽成通用组件 `WBTraffic`，两边各自装配节点与数据源。

   ## 三层结构

     #wbTraffic > svg.wbt-svg      连线（贝塞尔曲线，带虚线流动）
                > .wbt-node        节点（左：源 / 中：中枢 / 右：目标列）
                > .wbt-packet      粒子（沿 offset-path 移动）

   ## 关键实现点（都是踩过的）

   1. **坐标必须相对容器**，用 `getBoundingClientRect()` 相减，
      否则滚动/缩放后连线和节点会错位
   2. **粒子用 `offset-path: path('...')`**，浏览器原生沿路径动画，
      比自己算插值可靠
   3. **窗口 resize 要重绘**，否则容器尺寸变了连线留在旧位置
   4. **动画结束要移除 DOM**，否则跑久了会有几千个残留节点
   ========================================================================== */

(function () {
  "use strict";

  var SVG_NS = "http://www.w3.org/2000/svg";

  /* ---------------------------------------------------------------------- */
  /* 工具                                                                    */
  /* ---------------------------------------------------------------------- */

  function $(sel, root) {
    return (root || document).querySelector(sel);
  }

  /** 元素中心点（相对容器）。 */
  function centerOf(element, container) {
    if (!element || !container) return { x: 0, y: 0 };
    var outer = container.getBoundingClientRect();
    var rect = element.getBoundingClientRect();
    return {
      x: rect.left - outer.left + rect.width / 2,
      y: rect.top - outer.top + rect.height / 2,
    };
  }

  /** 水平向右的平滑 S 曲线（同参考实现）。 */
  function curvePath(start, end) {
    var midX = start.x + (end.x - start.x) / 2;
    return "M " + start.x + " " + start.y +
      " C " + midX + " " + start.y + ", " +
      midX + " " + end.y + ", " +
      end.x + " " + end.y;
  }

  /* ---------------------------------------------------------------------- */
  /* 拓扑图                                                                  */
  /* ---------------------------------------------------------------------- */

  /**
   * 创建一个拓扑图实例。
   *
   * @param {HTMLElement} host  容器（需要有 `position: relative`）
   * @param {Object} options
   *   nodes: [{id, label, sub, side: "left"|"center"|"right", tone}]
   *   links: [{from, to, kind}]        kind: "main" | "branch"
   */
  function Topology(host, options) {
    this.host = host;
    this.options = options || {};
    this.nodes = {};
    this.paths = {};
    this.build();
  }

  Topology.prototype.build = function () {
    var host = this.host;
    if (!host) return;
    host.classList.add("wbt-root");
    host.innerHTML = "";

    // 1) SVG 图层（放最底）
    var svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("class", "wbt-svg");
    host.appendChild(svg);
    this.svg = svg;

    // 2) 节点
    var nodes = this.options.nodes || [];
    var leftCol = document.createElement("div");
    leftCol.className = "wbt-col wbt-col-left";
    var centerCol = document.createElement("div");
    centerCol.className = "wbt-col wbt-col-center";
    var rightCol = document.createElement("div");
    rightCol.className = "wbt-col wbt-col-right";

    nodes.forEach(function (node) {
      var el = document.createElement("div");
      el.className = "wbt-node" + (node.tone ? " wbt-tone-" + node.tone : "");
      el.dataset.nodeId = node.id;
      el.innerHTML =
        '<span class="wbt-node-icon"></span>' +
        '<span class="wbt-node-body">' +
        '<div class="wbt-node-label">' + escapeHtml(node.label || node.id) + "</div>" +
        (node.sub
          ? '<div class="wbt-node-sub">' + escapeHtml(node.sub) + "</div>"
          : "") +
        "</span>";
      if (node.side === "left") leftCol.appendChild(el);
      else if (node.side === "right") rightCol.appendChild(el);
      else centerCol.appendChild(el);
      this_nodes_set(this, node.id, el);
    }, this);

    host.appendChild(leftCol);
    host.appendChild(centerCol);
    host.appendChild(rightCol);

    this.leftCol = leftCol;
    this.centerCol = centerCol;
    this.rightCol = rightCol;

    var self = this;
    // 首次绘制延后一帧，等布局稳定
    requestAnimationFrame(function () { self.redraw(); });
    // 尺寸变化要重绘
    if (typeof ResizeObserver !== "undefined") {
      this._observer = new ResizeObserver(function () { self.redraw(); });
      this._observer.observe(host);
    }
    window.addEventListener("resize", function () { self.redraw(); });
  };

  function this_nodes_set(topology, id, el) {
    topology.nodes[id] = el;
  }

  Topology.prototype.node = function (id) {
    return this.nodes[id] || null;
  };

  /** 重绘所有连线。 */
  Topology.prototype.redraw = function () {
    var self = this;
    var host = this.host;
    if (!host || !this.svg) return;

    var width = host.clientWidth;
    var height = host.clientHeight;
    if (!width || !height) return;

    this.svg.setAttribute("viewBox", "0 0 " + width + " " + height);
    this.svg.setAttribute("width", width);
    this.svg.setAttribute("height", height);

    // 清掉旧路径（节点可能变了）
    while (this.svg.firstChild) this.svg.removeChild(this.svg.firstChild);
    this.paths = {};

    (this.options.links || []).forEach(function (link) {
      var from = self.nodes[link.from];
      var to = self.nodes[link.to];
      if (!from || !to) return;
      var start = centerOf(from, host);
      var end = centerOf(to, host);
      var d = curvePath(start, end);

      var path = document.createElementNS(SVG_NS, "path");
      path.setAttribute("d", d);
      path.setAttribute("class", "wbt-link wbt-link-" + (link.kind || "main"));
      self.svg.appendChild(path);
      self.paths[link.from + "->" + link.to] = d;

      // 高亮当前生效的连线（策略选中的平台）
      if (link.active) path.classList.add("is-active");
    });
  };

  /** 让某条连线高亮 / 变暗。 */
  Topology.prototype.setActiveLink = function (fromId, toId) {
    var self = this;
    Object.keys(this.paths).forEach(function (key) {
      var el = self.svg.querySelectorAll(".wbt-link");
      // 简化：按顺序重置再设
    });
    var all = this.svg.querySelectorAll(".wbt-link");
    for (var i = 0; i < all.length; i += 1) all[i].classList.remove("is-active");
    if (!fromId || !toId) return;
    var target = this.paths[fromId + "->" + toId];
    if (!target) return;
    for (var j = 0; j < all.length; j += 1) {
      if (all[j].getAttribute("d") === target) all[j].classList.add("is-active");
    }
  };

  /**
   * 沿某条连线发射一个粒子。
   *
   * @param {string} fromId 起点节点 id
   * @param {string} toId   终点节点 id
   * @param {Object} opts   {duration, color, onDone}
   */
  Topology.prototype.fire = function (fromId, toId, opts) {
    var options = opts || {};
    var key = fromId + "->" + toId;
    var d = this.paths[key];
    if (!d) {
      // 路径还没画（首次渲染未完成），跳过而不是报错
      return null;
    }
    var duration = options.duration || 700;
    var packet = document.createElement("div");
    // tone 让 CSS 能区分「成功 / 限流 / 失败」三种粒子：
    // 成功要明显是绿色的，
    // 所以 ok 的粒子放大一号并加强辉光，danger 收小减少视觉噪声。
    packet.className = "wbt-packet" + (options.tone ? " wbt-packet-" + options.tone : "");
    if (options.color) packet.style.setProperty("--wbt-packet", options.color);
    packet.style.offsetPath = "path('" + d + "')";
    packet.style.animationDuration = duration + "ms";
    this.host.appendChild(packet);

    // 连线也按 tone 闪一下：粒子只有 7-9px，远看分不清绿/黄/红，
    // 而「这条链路当前是成功还是失败」是运维最需要一眼看到的信息。
    if (options.tone) {
      var path = this.paths[key];
      if (path && path.classList) {
        path.classList.add("wbt-link-tone-" + options.tone);
        setTimeout(function () {
          path.classList.remove("wbt-link-tone-" + options.tone);
        }, duration + 240);
      }
    }

    var self = this;
    // 结束即移除 —— 不清理会累积几千个 DOM
    var timer = setTimeout(function () {
      if (packet.parentNode) packet.parentNode.removeChild(packet);
      if (typeof options.onDone === "function") options.onDone();
    }, duration + 30);
    packet._wbtTimer = timer;

    // 到达时高亮目标节点
    if (toId) {
      setTimeout(function () { self.pulse(toId); }, Math.max(0, duration - 80));
    }
    void self;
    return packet;
  };

  /** 节点脉冲反馈。 */
  Topology.prototype.pulse = function (nodeId, tone) {
    var el = this.nodes[nodeId];
    if (!el) return;
    el.classList.remove("is-hit");
    // 强制重排以重启动画
    void el.offsetWidth;
    el.classList.add("is-hit");
    if (tone) el.dataset.hitTone = tone;
    setTimeout(function () { el.classList.remove("is-hit"); }, 420);
  };

  /** 更新节点副标题。 */
  Topology.prototype.setSub = function (nodeId, text) {
    var el = this.nodes[nodeId];
    if (!el) return;
    var sub = el.querySelector(".wbt-node-sub");
    if (!sub) {
      sub = document.createElement("div");
      sub.className = "wbt-node-sub";
      el.appendChild(sub);
    }
    sub.textContent = text;
  };

  /** 标记某节点为「当前生效」。 */
  Topology.prototype.setActiveNode = function (nodeId) {
    var nodes = this.nodes;
    Object.keys(nodes).forEach(function (key) {
      nodes[key].classList.toggle("is-active", key === nodeId);
    });
  };

/**
   * 更新节点文案/状态，**不重建 DOM**。
   *
   * 切换策略时如果 `build()` 重建，会出现「先清空再重画」的闪动，
   * 且容器高度瞬间变化导致页面抽搐。这里只改文本和 class。
   *
   * @param {Object} patches  {nodeId: {label?, sub?, active?, icon?}}
   */
  Topology.prototype.updateNodes = function (patches) {
    var self = this;
    Object.keys(patches || {}).forEach(function (id) {
      var el = self.nodes[id];
      var patch = patches[id];
      if (!el || !patch) return;
      var labelEl = el.querySelector(".wbt-node-label");
      if (labelEl && patch.label != null) labelEl.textContent = patch.label;
      var subEl = el.querySelector(".wbt-node-sub");
      if (subEl && patch.sub != null) subEl.textContent = patch.sub;
      if (patch.active != null) {
        el.classList.toggle("is-active", Boolean(patch.active));
      }
    });
    // 节点尺寸可能变了 -> 重画连线（比重建 DOM 便宜得多）
    this.redraw();
  };

  /** 给节点插入图标（如果有）。 */
  Topology.prototype.setNodeIcon = function (nodeId, html) {
    var el = this.nodes[nodeId];
    if (!el) return;
    var slot = el.querySelector(".wbt-node-icon");
    if (!slot) {
      slot = document.createElement("span");
      slot.className = "wbt-node-icon";
      el.insertBefore(slot, el.firstChild);
    }
    slot.innerHTML = html || "";
  };

  Topology.prototype.destroy = function () {
    if (this._observer) this._observer.disconnect();
    if (this.host) this.host.innerHTML = "";
  };

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  /* ---------------------------------------------------------------------- */
  /* 导出                                                                    */
  /* ---------------------------------------------------------------------- */

  window.WBTraffic = {
    Topology: Topology,
    centerOf: centerOf,
    curvePath: curvePath,
  };
})();
