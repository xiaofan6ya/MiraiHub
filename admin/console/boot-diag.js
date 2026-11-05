/* 浏览器端自检上报（在 <head> 里第一个执行，早于任何业务脚本）。

   背景：控制台在开发机上用真实浏览器反复验证都正常（0 个 pageerror、26 个脚本
   全部加载、函数齐全），但使用者那边持续出现
       Uncaught SyntaxError: Unexpected end of input (at /admin?tab=xxx:1:19)
   并且按钮整片失灵。相同报错也可能来自动态生成的 onclick 属性被引号截断，
   即使页面没有内联 <script>、核心函数全部存在也不能排除业务代码错误。
   必须保留报错位置、点击处理器与资源状态，不能仅凭脚本已加载就归因于外部注入。

   本文件因此负责"取证"而不是"修复"：
     1) 抢在最早注册 window error 捕获，拿到真实的 filename/lineno/colno/stack
     2) MutationObserver 盯着 documentElement，记录所有后来出现的 <script>（含被删除的）
     3) 对比 window 全局属性前后差异，抓 MAIN world 注入留下的痕迹
     4) 用 performance resource timing 列出每个脚本的 HTTP 状态与字节数
     5) 一次性 POST 到 /api/client-diag/report，落到服务端 logs/client-diag.jsonl
   UI 只在真出问题时弹一条红条（附带可复制的完整报告），正常情况下完全静默。 */
(function () {
  "use strict";

  var BUILD = "20261103-actions1";
  var ENDPOINT = "/api/client-diag/report";
  var rec = {
    build: BUILD,
    at: "",
    url: location.href,
    referrer: document.referrer || "",
    ua: navigator.userAgent,
    errors: [],
    injected: [],
    globals: [],
    assets: [],
    blocked: [],
    clicks: [],     // 点击是否真的到达页面（区分「被盖住」和「处理器没跑」）
    stalls: [],     // 主线程停摆 / 调试器暂停（Pause on exceptions 会让整页冻结）
    doc: {},
    fns: {},
    sent: false
  };

  var _warned = false;

  function safe(fn) {
    try { return fn(); } catch (e) { return null; }
  }

  function pushErr(o) {
    if (rec.errors.length < 40) rec.errors.push(o);
  }

  function isFatalSyntax(o) {
    return /Unexpected end of input|Unexpected token|Invalid or unexpected token/i
      .test(o.msg || "");
  }

  // ---- 1) 最早的错误捕获 ------------------------------------------------
  window.addEventListener("error", function (e) {
    var t = e.target || {};
    var tag = (t.tagName || "").toUpperCase();
    var resourceFail = !e.message && (tag === "SCRIPT" || tag === "LINK" || tag === "IMG");
    var o = {
      k: resourceFail ? "resource" : "js",
      msg: (e.message || "").slice(0, 400),
      file: e.filename || "",
      line: e.lineno || 0,
      col: e.colno || 0,
      tag: tag,
      src: (t.src || t.href || "").slice(0, 400),
      stack: ((e.error && e.error.stack) || "").slice(0, 900),
      at: Math.round(performance.now()),
      // 关键：在**报错当刻**记一次脚本清单。如果那段脚本事后被注入方删掉，
      // 等到 collect() 再看就什么都没有了（用户这次就是这样：inline=[] 但
      // 确实有一段内联脚本报了 EOF）。
      scriptsAtError: safe(function () {
        return {
          n: document.scripts.length,
          inline: Array.prototype.filter.call(document.scripts, function (s) { return !s.src; })
            .map(function (s) { return (s.textContent || "").slice(0, 300); })
        };
      })
    };
    pushErr(o);
    if (!resourceFail && isFatalSyntax(o)) showFatal(o);
    schedule();
  }, true);

  window.addEventListener("unhandledrejection", function (e) {
    var r = e.reason || {};
    pushErr({
      k: "reject",
      msg: String((r && r.message) || r).slice(0, 400),
      stack: String((r && r.stack) || "").slice(0, 900),
      at: Math.round(performance.now())
    });
    schedule();
  });

  // ---- 2) <script> 节点的增删（扩展/代理最常见的注入方式）----------------
  var _mo = safe(function () {
    var mo = new MutationObserver(function (muts) {
      for (var i = 0; i < muts.length; i++) {
        var m = muts[i];
        logNodes(m.addedNodes, "added");
        logNodes(m.removedNodes, "removed");
      }
    });
    function logNodes(list, how) {
      if (!list) return;
      for (var j = 0; j < list.length; j++) {
        var n = list[j];
        if (!n || n.nodeType !== 1 || String(n.tagName).toUpperCase() !== "SCRIPT") continue;
        // 我们自己的 26 个脚本都是同源 src（parser 插入时也会触发这里的 added）。
        // 只记「没有 src」或「跨源」的 —— 那才是外部注入的特征。
        var src = n.src || "";
        if (src && src.indexOf(location.origin) === 0) continue;
        // 就地摘掉：chrome-extension:// 这类外源脚本在 fetch 完成前被移出文档
        // 就不会执行，等于在 CSP 之外再加一道保险（CSP 也可能被扩展绕过）。
        var killed = false;
        if (how === "added") {
          try {
            if (n.parentNode) { n.parentNode.removeChild(n); killed = true; }
          } catch (e) { /* ignore */ }
          try {
            console.warn("[WB] 已移除外部注入的脚本：", src || ("内联 " + (n.textContent || "").slice(0, 80)));
          } catch (e) { /* ignore */ }
        }
        if (rec.injected.length < 20) {
          rec.injected.push({
            how: how,
            src: src.slice(0, 300),
            len: (n.textContent || "").length,
            head: (n.textContent || "").replace(/\s+/g, " ").slice(0, 200),
            removedByUs: killed,
            at: Math.round(performance.now())
          });
        }
        schedule();
      }
    }
    mo.observe(document.documentElement, { childList: true, subtree: true });
    return mo;
  });

  // ---- 3) 全局属性快照：MAIN world 注入会留下新键 ------------------------
  var _globals0 = safe(function () {
    return Object.getOwnPropertyNames(window);
  }) || [];

  // ---- 4) 收集 + 上报 ---------------------------------------------------
  function collect() {
    rec.at = new Date().toISOString();
    rec.doc = safe(function () {
      var html = document.documentElement ? document.documentElement.outerHTML : "";
      return {
        chars: html.length,
        lines: html.split("\n").length,
        complete: !!document.getElementById("wb-page-end"),
        scripts: document.scripts.length,
        inline: Array.prototype.filter.call(document.scripts, function (s) { return !s.src; })
          .map(function (s) { return (s.textContent || "").slice(0, 200); }),
        readyState: document.readyState,
        viewport: innerWidth + "x" + innerHeight,
        dpr: devicePixelRatio
      };
    });
    rec.fns = safe(function () {
      var keys = ["openPhoneLogin", "openUpload", "openExport", "scanLocal", "loadAccounts",
        "activateTab", "loadPlatforms", "WBDesktop", "WBTheme", "WBPlatform"];
      var out = {};
      keys.forEach(function (k) { out[k] = typeof window[k]; });
      return out;
    });
    rec.assets = safe(function () {
      return performance.getEntriesByType("resource")
        .filter(function (e) { return /\.(js|css)(\?|$)/.test(e.name); })
        .map(function (e) {
          return {
            n: e.name.replace(location.origin, "").slice(0, 120),
            status: e.responseStatus || 0,
            transfer: e.transferSize,
            body: e.encodedBodySize,
            decoded: e.decodedBodySize,
            ms: Math.round(e.duration)
          };
        });
    }) || [];
    rec.globals = safe(function () {
      var now = Object.getOwnPropertyNames(window);
      var add = [];
      for (var i = 0; i < now.length; i++) {
        if (_globals0.indexOf(now[i]) === -1 && add.length < 40) add.push(now[i]);
      }
      return add;
    }) || [];
    // 「按钮点了没反应」最常见的原因是有一个看不见的层盖在上面（残留的
    // modal 遮罩、扩展注入的浮层…）。逐个取按钮中心点做命中测试，
    // 命中的不是按钮自己就记下来 —— 这比让使用者描述现象准得多。
    rec.blocked = safe(function () {
      var out = [];
      var all = document.querySelectorAll("button,[onclick]");
      for (var i = 0; i < all.length && out.length < 10; i++) {
        var el = all[i];
        var r = el.getBoundingClientRect();
        if (r.width < 8 || r.height < 8) continue;
        if (r.bottom < 0 || r.top > innerHeight) continue;
        var top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        if (!top || top === el || el.contains(top) || top.contains(el)) continue;
        out.push({
          btn: (el.innerText || "").replace(/\s+/g, " ").trim().slice(0, 24),
          cover: String(top.tagName || "") + "#" + (top.id || "") + "." + String(top.className || "").slice(0, 60)
        });
      }
      return out;
    }) || [];
  }

  function send() {
    // One report per page lifecycle. Several error/resource callbacks can fire
    // together; resending the same full snapshot creates a false attack flood.
    if (rec.sent) return;
    collect();
    if (rec.blocked && rec.blocked.length) {
      try {
        console.warn("[WB] 有按钮被其它层盖住（点了会没反应）：", rec.blocked);
      } catch (e) { /* ignore */ }
    }
    var body;
    try { body = JSON.stringify(rec); } catch (e) { return; }
    if (body.length > 60000) body = body.slice(0, 60000);
    rec.sent = true;
    // 只走 fetch：上报接口现在需要后台会话 + CSRF 双提交头。
    // `navigator.sendBeacon` 不能带自定义头（CSRF 校验必失败）且会绕过
    // secure-fetch.js 的统一包装。fetch(keepalive) 在页面卸载时同样能发出。
    // 本脚本比 secure-fetch.js 先加载，所以这里自己补头，不依赖加载顺序。
    try {
      var csrf = "";
      var m = document.cookie.match(/(?:^|;\\s*)wb_csrf=([^;]*)/);
      if (m) csrf = decodeURIComponent(m[1]);
      var headers = { "Content-Type": "application/json" };
      if (csrf) headers["X-CSRF-Token"] = csrf;
      fetch(ENDPOINT, {
        method: "POST", body: body, keepalive: true,
        credentials: "same-origin",
        headers: headers
      });
    } catch (e) { /* 上报失败不影响页面功能 */ }
  }

  var _t = null;
  function schedule() {
    if (_t) return;
    _t = setTimeout(function () { _t = null; send(); }, 1200);
  }

  // ---- 4.5) 抓一份「浏览器实际收到的 HTML」--------------------------------
  // 报错坐标是「文档末行+1、第 20 列」，说明出错的是**文档里的一段内联脚本**
  // （跑到 EOF 才停）。但 DOM 里数出来的内联脚本是 0 —— 所以要看浏览器收到的
  // 响应体本身末尾到底多出了什么：重新拉一次同 URL，把长度/行数/末尾原样带回。
  function probeDoc() {
    try {
      fetch(location.href, { cache: "no-store", credentials: "same-origin" })
        .then(function (r) { return r.text(); })
        .then(function (t) {
          rec.served = {
            chars: t.length,
            lines: t.split("\n").length,
            head: t.slice(0, 80),
            tail: t.slice(-200)
          };
          send();
        })
        .catch(function (err) {
          rec.served = { error: String(err).slice(0, 160) };
        });
    } catch (e) { /* ignore */ }
  }

  // ---- 5) 只有 SyntaxError 才打扰用户 -----------------------------------
  function showFatal(o) {
    if (_warned) return;
    _warned = true;
    function render() {
      if (document.getElementById("wbBootWarn")) return;
      var host = document.createElement("div");
      host.id = "wbBootWarn";
      host.setAttribute("role", "alert");
      host.style.cssText = "position:fixed;left:0;right:0;bottom:0;z-index:2147483647;"
        + "padding:12px 16px;background:#7f1d1d;color:#fff;font:13px/1.6 system-ui,sans-serif;"
        + "display:flex;gap:12px;align-items:center;flex-wrap:wrap;box-shadow:0 -6px 20px rgba(0,0,0,.25);"
        // 红条本身不接收点击：否则它会挡住底下侧边栏底部的按钮（"退出登录"等），
        // 变成"点了没反应"的一部分。按钮再单独打开 pointer-events。
        + "pointer-events:none";
      var span = document.createElement("span");
      span.style.cssText = "flex:1 1 420px;min-width:0";
      var b = document.createElement("b");
      b.textContent = "页面脚本执行出错，部分按钮可能无法使用";
      span.appendChild(b);
      var code = document.createElement("code");
      code.style.cssText = "display:block;margin-top:4px;font-size:11px;opacity:.9;"
        + "white-space:pre-wrap;word-break:break-all";
      code.textContent = (o.file || "(浏览器未提供脚本来源)")
        + ":" + o.line + ":" + o.col + "  " + o.msg;
      span.appendChild(code);

      var copy = document.createElement("button");
      copy.type = "button";
      copy.textContent = "复制诊断信息";
      copy.style.cssText = "padding:6px 12px;border-radius:8px;border:0;background:#fff;"
        + "color:#7f1d1d;font-weight:600;cursor:pointer;pointer-events:auto";
      copy.onclick = function () {
        collect();
        var txt = JSON.stringify(rec, null, 1);
        safe(function () {
          if (navigator.clipboard) navigator.clipboard.writeText(txt);
        });
        copy.textContent = "已复制";
      };

      var rel = document.createElement("button");
      rel.type = "button";
      rel.textContent = "强制刷新";
      rel.style.cssText = "padding:6px 12px;border-radius:8px;border:1px solid rgba(255,255,255,.6);"
        + "background:transparent;color:#fff;font-weight:600;cursor:pointer;pointer-events:auto";
      rel.onclick = function () { location.reload(); };

      host.appendChild(span);
      host.appendChild(copy);
      host.appendChild(rel);
      (document.body || document.documentElement).appendChild(host);
    }
    if (document.body) render(); else document.addEventListener("DOMContentLoaded", render);
  }

  // ---- 5.5) 点击追踪：确认点击事件真的到达了页面 -------------------------
  //
  // 「按钮点了没反应」有三种完全不同的原因，必须能区分开：
  //   a) 点击被别的层挡住  → elementFromPoint 命中测试能看出（blocked）
  //   b) 点击到了，但内联处理器没跑 / 跑挂了 → 这里记录 act（onclick 原文）
  //   c) 整页被冻结（DevTools 的 Pause on exceptions、断点、主线程长任务）
  //      → 下面的 stall 检测：定时器间隔突然拉大就是被暂停过
  document.addEventListener("click", function (e) {
    try {
      var el = e.target;
      if (!el || el.nodeType !== 1) return;
      if (rec.clicks.length < 12) {
        var host = el.closest ? el.closest("[onclick],[data-platform-action]") : null;
        rec.clicks.push({
          sel: String(el.tagName || "").toLowerCase() + (el.id ? "#" + el.id : ""),
          hit: !!host,
          act: host ? String(host.getAttribute("onclick") || host.getAttribute("data-platform-action") || "").slice(0, 60) : "",
          at: Math.round(performance.now())
        });
      }
      schedule();
    } catch (err) { /* 追踪本身不能影响页面 */ }
  }, true);

  (function watchStall() {
    var last = performance.now();
    setInterval(function () {
      var now = performance.now();
      var gap = now - last;
      last = now;
      if (gap > 2500 && rec.stalls.length < 8) {
        rec.stalls.push({ gap: Math.round(gap), at: Math.round(now) });
      }
    }, 250);
  })();

  // ---- 6) 两个时间点上报：load 后（早期注入）与 8s 后（延迟注入）---------
  window.addEventListener("load", function () {
    probeDoc();
    setTimeout(send, 1000);
    setTimeout(function () { if (!rec.errors.length && !rec.injected.length) send(); }, 8000);
  });
})();
